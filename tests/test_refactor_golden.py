"""Strict placement golden for the #638/#639 hotspot-split refactors (epic #635).

These refactors (`render_pmi`, `_annotate_holes`, `finalize`, the #639 PlacementContext
threading) are **behaviour-preserving** — the output must not change at all. This gate is a
stronger, wider counterpart to the retired byte-exact golden (ADR 0007) and the since-retired
ADR-0009 `test_layout_snapshot` (#641 gap 3): it snapshots the FULL placement signature of a
corpus chosen to
exercise every path the split touches — machined-feature leader callouts (chamfer/fillet/
flat/pocket/groove), off-axis hole locations, prismatic height ladders + step positions,
sections, turned diameters, dense-hole table escalation — and also the **build-issue set**
(drops/escalations), which a placement-only snapshot misses but the drop logic in
`_annotate_holes`/`render_pmi` is load-bearing on.

Precision: the 0.1 mm + 1e-6-bias quantisation proven cross-platform-stable for the ADR-0009
snapshot (ADR 0006 pinned fonts). A raw byte/SVG digest is deliberately NOT used — a refactor
that reorders floating-point sums shifts a value ~1 ULP with no real placement change, which a
byte digest false-fails on; 0.1 mm catches the ~mm drift a wrong projector/sign/order causes
while surviving that noise.

**Throwaway:** delete this file and `tests/refactor_golden/` once #638 + #639 land.

Re-bless intentionally (there should be NO intentional change during these refactors):
    DRAFTWRIGHT_UPDATE_GOLDEN=1 uv run pytest tests/test_refactor_golden.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from build123d import Axis, Box, Cylinder, Pos, Rot, Rotation, chamfer, fillet

from draftwright import build_drawing

_GOLDEN_DIR = Path(__file__).parent / "refactor_golden"


# --- corpus: fast parts exercising the #638/#639 code paths ------------------------------


def _chamfered():
    # A single corner chamfer → render_chamfers leader callout.
    plate = Box(90, 60, 20)
    e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
    return chamfer(e, 12)


def _filleted():
    # A single corner fillet → render_fillets leader callout.
    plate = Box(90, 60, 20)
    e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
    return fillet(e, 8)


def _hex_bar():
    # Six flats 60° apart on round stock → render_flats "A/F" callout.
    bar = Cylinder(10, 30)
    for k in range(6):
        bar = bar - Rot(0, 0, 60 * k) * Pos(10.3, 0, 0) * Box(2, 40, 40)
    return bar


def _grooved_shaft():
    # Two annular grooves on one shaft → render_grooves callouts + turned diameters/lengths.
    shaft = Cylinder(10, 60)
    shaft -= Pos(0, 0, 15) * (Cylinder(10, 4) - Cylinder(8, 4))
    shaft -= Pos(0, 0, -15) * (Cylinder(10, 4) - Cylinder(7, 4))
    return shaft


def _pocketed():
    # A blind pocket + a through hole → render_pockets callout + _annotate_holes.
    return Box(80, 60, 20) - Pos(0, 10, 5) * Box(30, 20, 20) - Pos(-20, -15, 0) * Cylinder(3, 30)


def _side_drilled():
    # Radial (X-axis) through-holes at two heights → _locate_off_axis_holes (side/below).
    part = Box(60, 40, 30)
    for z in (8, 20):
        part -= Pos(0, 0, z) * Rotation(0, 90, 0) * Cylinder(3, 80)
    return part


def _prismatic_ladder():
    # An asymmetric step → height ladder + a step-position (shoulder) dim.
    return Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15)


def _centered_rebate():
    # A central channel → two shoulders, both positions dimensioned.
    return Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)


def _bracket_section():
    # Central bore + offset counterbore → plan callouts + section A-A.
    return Box(90, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)


def _turned_stepped():
    # Z-turned stepped cylinder → step diameters + axial length chain.
    from build123d import Align

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    s = Cylinder(12, 16, align=base)
    s += Pos(0, 0, 16) * Cylinder(8, 14, align=base)
    s += Pos(0, 0, 30) * Cylinder(5, 10, align=base)
    return s


def _flange_dense():
    # A bolt circle → dense plan holes escalate to the hole TABLE + balloon ring.
    import math

    flange = Cylinder(radius=45, height=10) - Cylinder(radius=8, height=10)
    for i in range(5):
        ang = math.radians(72 * i)
        flange -= Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(3, 10)
    return flange


def _holed_slot():
    # A hole whose X-location coincides with a slot edge → the #345 corridor dedup path.
    from build123d import BuildPart, Hole, Locations, Mode

    with BuildPart() as p:
        Box(60, 40, 20)
        Box(20, 8, 30, mode=Mode.SUBTRACT)
        with Locations((-10, 14, 0), (20, 14, 0), (8, -14, 0)):
            Hole(3, depth=20)
    return p.part


CORPUS = {
    "chamfered": _chamfered,
    "filleted": _filleted,
    "hex_bar": _hex_bar,
    "grooved_shaft": _grooved_shaft,
    "pocketed": _pocketed,
    "side_drilled": _side_drilled,
    "prismatic_ladder": _prismatic_ladder,
    "centered_rebate": _centered_rebate,
    "bracket_section": _bracket_section,
    "turned_stepped": _turned_stepped,
    "flange_dense": _flange_dense,
    "holed_slot": _holed_slot,
}


# --- signature ---------------------------------------------------------------------------


def _round_bbox(box):
    # 0.1 mm grid + 1e-6 bias: stable under a refactor's FP-reordering, sensitive to real drift.
    if box is None:
        return None
    return [round(float(v) + 1e-6, 1) for v in box]


def _geom_box(o):
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception:
        return None


def _signature(dwg) -> dict:
    annotations = sorted(
        (
            {
                "name": name,
                "view": dwg.view_of(name),
                "type": type(o).__name__,
                "label": getattr(o, "label", "") or "",
                "label_bbox": _round_bbox(getattr(o, "label_bbox", None)),
                "geom_bbox": _round_bbox(_geom_box(o)),
            }
            for name, o in dwg.iter_annotations()
        ),
        key=lambda a: a["name"],
    )
    views = {}
    for vname, shapes in dwg.views.items():
        vis = shapes[0] if isinstance(shapes, (tuple, list)) else shapes
        views[vname] = _round_bbox(_geom_box(vis))
    # Build issues (drops / escalations / warnings) — the drop logic in _annotate_holes and
    # render_pmi is load-bearing and a placement-only snapshot would miss a change to it.
    # Lists, not tuples: JSON has no tuple type, so tuples would fail the round-trip compare.
    issues = sorted([i.severity, i.code, i.message] for i in dwg._build_issues)
    return {
        "views": views,
        "annotations": annotations,
        "item_count": len(dwg.items),
        "build_issues": issues,
    }


@pytest.mark.parametrize("name", list(CORPUS))
def test_refactor_golden(name):
    dwg = build_drawing(CORPUS[name]())
    sig = _signature(dwg)
    golden = _GOLDEN_DIR / f"{name}.json"

    if os.environ.get("DRAFTWRIGHT_UPDATE_GOLDEN"):
        _GOLDEN_DIR.mkdir(exist_ok=True)
        golden.write_text(json.dumps(sig, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return

    assert golden.exists(), (
        f"no golden for {name!r}; generate with "
        f"DRAFTWRIGHT_UPDATE_GOLDEN=1 uv run pytest tests/test_refactor_golden.py"
    )
    expected = json.loads(golden.read_text(encoding="utf-8"))
    assert sig == expected, (
        f"placement/issue drift for {name!r}. The #638/#639 refactors must be byte-for-byte "
        f"behaviour-preserving — this is a real regression, NOT to be re-blessed away."
    )
