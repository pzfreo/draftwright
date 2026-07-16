"""Adversarial (Hypothesis) fuzz of the layout-cleanliness invariant (#641 gap 1).

`test_layout_property.py` fuzzes the same invariant, but from a *fixed seed* over a
modest fixed spread — great as a reproducible determinism gate, blind as a bug finder
(no exploration, no shrinking). This module keeps that seeded tier and adds an
adversarial one: Hypothesis draws the part parameters directly (box sizes, hole
counts/positions, step counts, slot geometry) so a failure **shrinks** toward a
minimal offending part instead of surfacing as an opaque seed index.

The invariant is identical — a finished ``build_drawing`` carries none of the
layout-collision lint codes in :data:`_DEFECTS`. The strategies mirror the six
templates in `test_layout_property.py`, parameter-for-parameter, but pull each value
from a bounded strategy so shrinking reduces real geometry.

Budget: each example is one real OCC build (~3-6 s), so the example count is
deliberately small and the per-example deadline is disabled. This is a bounded CI
tier, not an exhaustive sweep — the seeded tier remains the determinism gate.
"""

from __future__ import annotations

import math

from build123d import Align, Box, Cylinder, Pos, Rotation
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from draftwright import build_drawing

# The layout-collision lint codes a clean sheet must never carry — mirrors
# test_layout_property._DEFECTS / TestLayoutCleanlinessInvariant._DEFECTS. Duplicated
# (a plain constant, not the Test class) to avoid importing a Test*-bound class.
_DEFECTS = {
    "view_annotation_overlap",
    "view_overlap",
    "view_out_of_bounds",
    "annotation_out_of_bounds",
    "annotation_overlap",
}

# Each example is a full build; keep the count small and disable the per-example
# deadline (OCC builds blow any millisecond budget). too_slow/filter health checks
# are expected here for the same reason.
_LAYOUT_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


def _f(lo: float, hi: float) -> st.SearchStrategy[float]:
    """A bounded float strategy (no NaN/inf), lower-bounded so a derived upper bound that
    collapses below the floor still yields a valid, non-empty range."""
    return st.floats(
        min_value=lo, max_value=max(lo + 1e-6, hi), allow_nan=False, allow_infinity=False
    )


@st.composite
def _box_holes(draw):
    """A box with 1-5 scattered through-holes (mirror of _template_box_holes)."""
    w, d, h = draw(_f(40, 160)), draw(_f(30, 120)), draw(_f(10, 40))
    part = Box(w, d, h)
    margin = 8.0
    placed: list[tuple[float, float, float]] = []
    for _ in range(draw(st.integers(1, 5))):
        dia = draw(_f(3, min(w, d) * 0.12))
        x = draw(_f(-w / 2 + margin, w / 2 - margin))
        y = draw(_f(-d / 2 + margin, d / 2 - margin))
        if all(math.hypot(x - px, y - py) >= (dia + pd) / 2 + margin for px, py, pd in placed):
            placed.append((x, y, dia))
            part -= Pos(x, y, 0) * Cylinder(dia / 2, h + 4)
    return part


@st.composite
def _bolt_circle(draw):
    """A box with a 3-8 hole bolt circle (mirror of _template_bolt_circle)."""
    side, h = draw(_f(45, 100)), draw(_f(10, 25))
    part = Box(side, side, h)
    n = draw(st.integers(3, 8))
    bcd = draw(_f(side * 0.35, side * 0.7))
    margin = 5.0
    neighbour_cap = bcd * math.pi / n * 0.6
    edge_cap = side - 2 * margin - bcd
    dia = draw(_f(2.5, min(neighbour_cap, edge_cap)))
    for i in range(n):
        ang = i * 2 * math.pi / n
        part -= Pos(bcd / 2 * math.cos(ang), bcd / 2 * math.sin(ang), 0) * Cylinder(dia / 2, h + 4)
    return part


@st.composite
def _grid(draw):
    """A box with a rows x cols hole grid (mirror of _template_grid)."""
    rows, cols = draw(st.integers(2, 4)), draw(st.integers(2, 5))
    row_pitch, col_pitch = draw(_f(15, 30)), draw(_f(15, 30))
    dia = draw(_f(3, min(row_pitch, col_pitch) * 0.5))
    w = col_pitch * (cols - 1) + 30
    d = row_pitch * (rows - 1) + 30
    h = draw(_f(8, 20))
    part = Box(w, d, h)
    x0, y0 = -col_pitch * (cols - 1) / 2, -row_pitch * (rows - 1) / 2
    for r in range(rows):
        for c in range(cols):
            part -= Pos(x0 + c * col_pitch, y0 + r * row_pitch, 0) * Cylinder(dia / 2, h + 4)
    return part


@st.composite
def _turned_steps(draw):
    """A turned shaft with 2-4 diameter/length steps, either axis (mirror of
    _template_turned_steps)."""
    b = Align.MIN
    s = None
    z = 0.0
    for _ in range(draw(st.integers(2, 4))):
        dia, ln = draw(_f(6, 30)), draw(_f(8, 40))
        seg = Pos(0, 0, z) * Cylinder(dia / 2, ln, align=(Align.CENTER, Align.CENTER, b))
        s = seg if s is None else s + seg
        z += ln
    assert s is not None  # the loop runs 2-4 times, so a segment is always built
    return Rotation(0, 90, 0) * s if draw(st.booleans()) else s


@st.composite
def _counterbore(draw):
    """A box with a through-bore + counterbore, triggering section A-A (mirror of
    _template_counterbore)."""
    w, d, h = draw(_f(45, 100)), draw(_f(30, 70)), draw(_f(15, 35))
    part = Box(w, d, h)
    bore = draw(_f(3, min(w, d) * 0.15))
    cbore = bore * draw(_f(1.4, 2.0))
    cbore_depth = draw(_f(2, h * 0.3))
    part -= Cylinder(bore / 2, h + 4)
    part -= Pos(0, 0, h / 2 - cbore_depth / 2) * Cylinder(cbore / 2, cbore_depth + 2)
    return part


@st.composite
def _slot(draw):
    """A box with one milled slot (mirror of _template_slot)."""
    w, d, h = draw(_f(50, 120)), draw(_f(40, 90)), draw(_f(10, 30))
    part = Box(w, d, h)
    slot_len = draw(_f(min(w, d) * 0.3, min(w, d) * 0.6))
    slot_w = draw(_f(4, 12))
    cutter = Box(slot_len, slot_w, h + 4) if draw(st.booleans()) else Box(slot_w, slot_len, h + 4)
    return part - cutter


_PARTS = st.one_of(_box_holes(), _bolt_circle(), _grid(), _turned_steps(), _counterbore(), _slot())


@given(part=_PARTS)
@_LAYOUT_SETTINGS
def test_generated_part_has_no_layout_collisions(part):
    """A finished drawing of any generated part carries no layout-collision lint code.
    On failure Hypothesis shrinks toward a minimal offending part — e.g. reverting the
    #345 slot/hole dedup guard reproduces as a small counterexample here (the seeded
    tier could only surface it as an opaque case index)."""
    dwg = build_drawing(part)
    hits = sorted({i.code for i in dwg.lint()} & _DEFECTS)
    assert not hits, f"layout defects in finished drawing: {hits}"
