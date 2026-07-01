"""TEMPORARY characterization gate for the ADR 0009 strip-layout refactor.

Snapshots the **layout signature** of a corpus of parts — every annotation's
owning view, type, label, and rounded bbox, plus each view's projected bbox — and
fails on any drift. The boundary-labeling migration (ADR 0009, tracking #320) is
*behaviour-preserving* through phases P0 (#317), P1 (#321) and P3 (#323); this gate
catches any unintended placement change those phases must not introduce. At P2
(#322) and P4 (#318) — where output deliberately improves — re-bless the affected
snapshots **in that PR** as a reviewed diff.

It is deliberately coarser than the retired byte-exact golden harness (ADR 0007):
it characterises *placement* (the thing the refactor touches), quantised to a
0.1 mm grid (with a small bias so values on a rounding boundary stay stable under
floating-point reordering — see `_round_bbox`), which is cross-platform-
deterministic given the pinned fonts (ADR 0006).

**This file and `tests/layout_snapshots/` are throwaway — delete them at P5 (#319).**

Re-bless intentionally:
    DRAFTWRIGHT_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_layout_snapshot.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from build123d import (
    Align,
    Box,
    BuildPart,
    Cylinder,
    Hole,
    Locations,
    Pos,
    Rotation,
)

from draftwright import build_drawing

_SNAP_DIR = Path(__file__).parent / "layout_snapshots"


# --- corpus: small, fast parts that exercise the strip placers -------------


def _box():
    return Box(40, 30, 12)


def _plate_holes():
    with BuildPart() as p:
        Box(90, 60, 20)
        with Locations((30, 18, 0), (-30, 18, 0), (30, -18, 0), (-30, -18, 0)):
            Hole(4, depth=20)
    return p.part


def _bracket():
    # central bore + offset counterbore → plan callouts + section A-A.
    return Box(90, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)


def _side_drilled():
    # radial (X-axis) through-holes at two heights → the contended side/below
    # strip: off-axis location dims (#133/#225) sharing space with callouts.
    part = Box(60, 40, 30)
    for z in (8, 20):
        part -= Pos(0, 0, z) * Rotation(0, 90, 0) * Cylinder(3, 80)
    return part


def _slotted():
    # an enclosed through-slot (#135) → slot width/length/position dims.
    return Box(50, 30, 20) - Box(20, 8, 30)


def _turned_shaft():
    # Z-turned stepped cylinder → step diameters + axial length chain.
    base = (Align.CENTER, Align.CENTER, Align.MIN)
    s = Cylinder(12, 16, align=base)
    s += Pos(0, 0, 16) * Cylinder(8, 14, align=base)
    s += Pos(0, 0, 30) * Cylinder(5, 10, align=base)
    return s


def _drive_screw_x():
    # X-turned cylinder + coaxial axial bore — the #305 round-view case.
    with BuildPart() as p:
        Cylinder(radius=6, height=20)
        Hole(0.8, depth=8)
    return Rotation(0, 90, 0) * p.part


def _dshape():
    # D-profile bar (circle + flat) with a coaxial stepped bore. Its callout must
    # clear the coaxial bore's location-dim line even though the flat makes the part
    # non-rotational and offsets the bore off centre — the #321 case the shape gate
    # missed. Locks in the occupancy/row-driven lift.
    body = Cylinder(radius=4, height=24)
    body -= Pos(0, -5, 0) * Box(12, 6, 26)
    body -= Cylinder(radius=1.65, height=26)
    body -= Pos(0, 0, 12) * Cylinder(
        radius=0.8, height=3.5, align=(Align.CENTER, Align.CENTER, Align.MAX)
    )
    return Rotation(0, 90, 0) * body


def _flange():
    import math

    flange = Cylinder(radius=45, height=10) - Cylinder(radius=8, height=10)
    for i in range(5):
        ang = math.radians(72 * i)
        flange -= Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(3, 10)
    return flange


CORPUS = {
    "box": _box,
    "plate_holes": _plate_holes,
    "bracket": _bracket,
    "side_drilled": _side_drilled,
    "slotted": _slotted,
    "dshape": _dshape,
    "turned_shaft": _turned_shaft,
    "drive_screw_x": _drive_screw_x,
    "flange": _flange,
}


# --- signature -------------------------------------------------------------


# Quantise to a 0.1 mm grid with a 1e-6 bias.
#
# Two reasons for each part. (a) 0.1 mm, not finer: the placement drift this gate
# cares about is ≥ ~1 mm. (b) the bias: a Dimension's *geometry* box edges land
# *exactly* on the .X5 round-half boundary (the label centre ± a fixed font-derived
# extent), and a leader/witness reroute or the refactor's reordered floating-point
# placement sums shift a value by ~1 ULP — which would flip an on-boundary value's
# rounding and false-FAIL with no real change. 1e-6 is far above the ~1e-13 FP-noise
# floor and far below the 0.1 mm grid, so every on-boundary value resolves the same
# way regardless of that noise. (Cross-platform stability of the *un*-biased values
# is already proven by CI; the bias guards against the refactor's intra-platform FP
# reordering.)
def _round_bbox(box):
    if box is None:
        return None
    return [round(float(v) + 1e-6, 1) for v in box]


def _label_box(o):
    return getattr(o, "label_bbox", None)


def _geom_box(o):
    # The FULL rendered geometry bbox — leader shafts/arrow tips, dimension witness
    # and extension lines, hatch — none of which the label box covers. The leader-
    # routing the refactor reroutes (ADR 0009 P4) is only visible here.
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
                "label_bbox": _round_bbox(_label_box(o)),
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
    # Total render-list size catches drift in *unnamed* annotations too, which
    # iter_annotations() (named only) would otherwise hide.
    return {"views": views, "annotations": annotations, "item_count": len(dwg.items)}


@pytest.mark.parametrize("name", list(CORPUS))
def test_layout_snapshot(name):
    dwg = build_drawing(CORPUS[name]())
    sig = _signature(dwg)
    snap = _SNAP_DIR / f"{name}.json"

    if os.environ.get("DRAFTWRIGHT_UPDATE_SNAPSHOTS"):
        _SNAP_DIR.mkdir(exist_ok=True)
        snap.write_text(json.dumps(sig, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return

    assert snap.exists(), (
        f"no snapshot for {name!r}; generate with "
        f"DRAFTWRIGHT_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_layout_snapshot.py"
    )
    expected = json.loads(snap.read_text(encoding="utf-8"))
    assert sig == expected, (
        f"layout drift for {name!r}. If intentional (P2/P4), re-bless with "
        f"DRAFTWRIGHT_UPDATE_SNAPSHOTS=1; otherwise placement regressed."
    )
