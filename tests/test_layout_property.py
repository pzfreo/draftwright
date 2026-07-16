"""Property/fuzz coverage for the layout-cleanliness invariant (#301, ADR 0009 P5
strand 4).

`TestLayoutCleanlinessInvariant` (`test_make_drawing.py`, #293) proved the
*mechanism* — six hand-picked part archetypes, each asserted defect-free — but
six fixtures raise the floor without proving the ceiling: they miss combinations
and extremes (varied aspect ratios, hole counts, pattern/slot/section mixes).
This generates a modest, **seeded** (reproducible) spread of randomised-but-
bounded parts across several shape templates and asserts the same two
invariants hold for every one of them:

1. No genuine layout-collision lint code (mirrors `TestLayoutCleanlinessInvariant`'s
   `_DEFECTS` set — duplicated locally, not imported; see the comment above
   `_DEFECTS` for why importing the class instead would double-collect its tests).
2. `build_drawing` is deterministic (two builds of the same generated part
   produce an identical layout signature — mirrors `test_layout_cleanliness.py`'s
   `test_build_is_deterministic`).

A fixed master seed (`_SEED`) makes every run identical case-for-case: a CI
failure reproduces exactly by re-running the same (seed, case index), no
separate shrinking machinery needed. Case count is deliberately modest (15 per
check) to keep the fast tier quick, per the issue's own guidance — each
collision-check case is a single OCC build (~3-6s locally), each determinism
case builds twice (~9-15s locally). The two checks live in separate classes so
`pytest-xdist --dist loadscope` (this project's recommended local full-suite
invocation) can schedule them on different workers instead of serializing all
30 cases behind one module-level scope."""

from __future__ import annotations

import math
import random

import pytest
from _layout_sig import _signature
from build123d import Align, Box, Cylinder, Pos, Rotation

from draftwright import build_drawing

# Mirrors TestLayoutCleanlinessInvariant._DEFECTS (test_make_drawing.py, #293) —
# duplicated, not imported: importing that class here would make pytest collect
# and re-run its own 6 tests a second time under this module too (a class bound
# to a Test*-prefixed name at module level is collected wherever it's visible).
_DEFECTS = {
    "view_annotation_overlap",
    "view_overlap",
    "view_out_of_bounds",
    "annotation_out_of_bounds",
    "annotation_overlap",
}
_SEED = 20260702  # fixed: every case is exactly reproducible by (seed, index)
_N_CASES = 15


def _template_box_holes(rng: random.Random):
    """A box with 1-5 scattered through-holes — varied aspect ratio and hole
    count/size beyond the fixed `_prism_holes` archetype."""
    w, d, h = rng.uniform(40, 160), rng.uniform(30, 120), rng.uniform(10, 40)
    part = Box(w, d, h)
    n = rng.randint(1, 5)
    margin = 8.0
    placed: list[tuple[float, float, float]] = []  # (x, y, dia) of holes placed so far
    for _ in range(n):
        dia = rng.uniform(3, min(w, d) * 0.12)
        for _attempt in range(20):
            x = rng.uniform(-w / 2 + margin, w / 2 - margin)
            y = rng.uniform(-d / 2 + margin, d / 2 - margin)
            if all(
                math.hypot(x - px, y - py) >= (dia + pdia) / 2 + margin for px, py, pdia in placed
            ):
                break
        else:
            continue  # no clear spot found after 20 tries — skip this hole
        placed.append((x, y, dia))
        part -= Pos(x, y, 0) * Cylinder(dia / 2, h + 4)
    return part


def _template_bolt_circle(rng: random.Random):
    """A box with an N-hole bolt circle — varied box size, hole count, and BCD
    beyond the fixed `_bolt_circle` archetype."""
    side = rng.uniform(45, 100)
    h = rng.uniform(10, 25)
    part = Box(side, side, h)
    n = rng.randint(3, 8)
    bcd = rng.uniform(side * 0.35, side * 0.7)
    margin = 5.0
    neighbour_cap = bcd * math.pi / n * 0.6  # stay well clear of neighbours
    edge_cap = side - 2 * margin - bcd  # stay clear of the box's outer wall
    dia = rng.uniform(2.5, min(neighbour_cap, edge_cap))
    for i in range(n):
        ang = i * 2 * math.pi / n
        part -= Pos(bcd / 2 * math.cos(ang), bcd / 2 * math.sin(ang), 0) * Cylinder(dia / 2, h + 4)
    return part


def _template_grid(rng: random.Random):
    """A box with a rows x cols hole grid — a pattern shape the fixed 6-archetype
    corpus never exercises for the layout-cleanliness invariant (only the bolt
    circle does)."""
    rows, cols = rng.randint(2, 4), rng.randint(2, 5)
    row_pitch, col_pitch = rng.uniform(15, 30), rng.uniform(15, 30)
    dia = rng.uniform(3, min(row_pitch, col_pitch) * 0.5)
    w = col_pitch * (cols - 1) + 30
    d = row_pitch * (rows - 1) + 30
    h = rng.uniform(8, 20)
    part = Box(w, d, h)
    x0, y0 = -col_pitch * (cols - 1) / 2, -row_pitch * (rows - 1) / 2
    for r in range(rows):
        for c in range(cols):
            part -= Pos(x0 + c * col_pitch, y0 + r * row_pitch, 0) * Cylinder(dia / 2, h + 4)
    return part


def _template_turned_steps(rng: random.Random):
    """A turned shaft with 2-4 random diameter/length steps — varied step counts
    beyond the fixed `_x_simple`/`_x_crowded`/`_z_stepped` archetypes, on either
    turning axis."""
    b = Align.MIN
    n = rng.randint(2, 4)
    s = None
    z = 0.0
    for _ in range(n):
        dia = rng.uniform(6, 30)
        ln = rng.uniform(8, 40)
        seg = Pos(0, 0, z) * Cylinder(dia / 2, ln, align=(Align.CENTER, Align.CENTER, b))
        s = seg if s is None else s + seg
        z += ln
    return Rotation(0, 90, 0) * s if rng.random() < 0.5 else s  # X-turned or Z-turned


def _template_counterbore(rng: random.Random):
    """A box with a through-bore + counterbore — triggers section A-A, varied
    box/bore dimensions beyond the fixed `_counterbored` archetype."""
    w, d, h = rng.uniform(45, 100), rng.uniform(30, 70), rng.uniform(15, 35)
    part = Box(w, d, h)
    bore = rng.uniform(3, min(w, d) * 0.15)
    cbore = bore * rng.uniform(1.4, 2.0)
    cbore_depth = rng.uniform(2, h * 0.3)
    part -= Cylinder(bore / 2, h + 4)
    part -= Pos(0, 0, h / 2 - cbore_depth / 2) * Cylinder(cbore / 2, cbore_depth + 2)
    return part


def _template_slot(rng: random.Random):
    """A box with one milled slot — a genuine gap in the fixed 6-archetype
    corpus (#301: "mixed slot + section... extremes"), which has no slot
    fixture at all."""
    w, d, h = rng.uniform(50, 120), rng.uniform(40, 90), rng.uniform(10, 30)
    part = Box(w, d, h)
    slot_len = rng.uniform(min(w, d) * 0.3, min(w, d) * 0.6)
    slot_w = rng.uniform(4, 12)
    along_x = rng.random() < 0.5
    if along_x:
        cutter = Box(slot_len, slot_w, h + 4)
    else:
        cutter = Box(slot_w, slot_len, h + 4)
    part -= cutter
    return part


_TEMPLATES = (
    _template_box_holes,
    _template_bolt_circle,
    _template_grid,
    _template_turned_steps,
    _template_counterbore,
    _template_slot,
)


def _generate(seed: int, index: int):
    """One reproducible generated part: a fresh RNG per (seed, index) pair, so
    a failing case is exactly reproducible in isolation without replaying every
    earlier case. `random.Random` only accepts a scalar seed, so combine the two
    into one int rather than replaying `_N_CASES` draws from a shared stream."""
    rng = random.Random(seed * 10_000 + index)
    template = rng.choice(_TEMPLATES)
    return template(rng)


# Each check lives in its own class (not a bare module-level function) so
# `pytest-xdist --dist loadscope` treats them as two independently-schedulable
# scopes instead of serializing all 30 cases behind one module-level group.
class TestGeneratedPartCollisions:
    @pytest.mark.timeout(60)
    @pytest.mark.parametrize("case", range(_N_CASES))
    def test_no_layout_collisions(self, case):
        part = _generate(_SEED, case)
        dwg = build_drawing(part)
        hits = sorted({i.code for i in dwg.lint()} & _DEFECTS)
        assert not hits, f"seed={_SEED} case={case}: layout defects in finished drawing: {hits}"


class TestGeneratedPartDeterminism:
    @pytest.mark.timeout(60)
    @pytest.mark.parametrize("case", range(_N_CASES))
    def test_build_is_deterministic(self, case):
        part_a, part_b = _generate(_SEED, case), _generate(_SEED, case)
        sig_a, sig_b = _signature(build_drawing(part_a)), _signature(build_drawing(part_b))
        assert sig_a == sig_b, f"seed={_SEED} case={case}: two builds produced different layouts"
