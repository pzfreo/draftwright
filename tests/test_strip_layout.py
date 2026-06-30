"""Unit tests for the ADR 0009 collect-then-solve strip-placement stage.

Grows with the boundary-labeling migration (tracking #320). P0b (#317): the
complete per-strip occupancy model — `strip_obstacles` — that closes the
`_occupied_boxes` blind spots behind #133/#225/#305.
"""

from __future__ import annotations

from build123d import Box, BuildPart, Cylinder, Hole, Pos, Rotation

from draftwright import build_drawing
from draftwright.annotations._common import (
    _occupied_boxes,
    strip_obstacles,
)


def _same(a, b, tol=1e-6):
    return a is not None and b is not None and all(abs(x - y) <= tol for x, y in zip(a, b))


def _drive_screw_x():
    # X-turned cylinder + coaxial axial bore: centrelines + a bore-callout leader.
    with BuildPart() as p:
        Cylinder(radius=6, height=20)
        Hole(0.8, depth=8)
    return Rotation(0, 90, 0) * p.part


def test_strip_obstacles_captures_centreline_that_occupied_boxes_drops():
    # _occupied_boxes excludes bare centrelines; the complete occupancy must not —
    # a centreline through a callout's row is exactly the #305 blind spot.
    dwg = build_drawing(_drive_screw_x())
    cl = dwg._named["centerline_front"]
    b = cl.bounding_box()
    cbox = (b.min.X, b.min.Y, b.max.X, b.max.Y)

    obst = strip_obstacles(dwg)
    occ = _occupied_boxes(dwg)

    assert any(_same(x, cbox) for x in obst), "centreline missing from strip_obstacles"
    assert not any(_same(x, cbox) for x in occ), "expected _occupied_boxes to exclude centrelines"


def test_strip_obstacles_captures_full_leader_footprint_not_just_label():
    # A bore callout's leader shaft extends well past its text box. _occupied_boxes
    # records only the label box; strip_obstacles must record the full footprint,
    # or a placer thinks the shaft's row is free (#133/#225).
    dwg = build_drawing(_drive_screw_x())
    name, leader = next((n, o) for n, o in dwg.iter_annotations() if n.startswith("hc_"))

    lb = leader.label_bbox
    gb = leader.bounding_box()
    full = (gb.min.X, gb.min.Y, gb.max.X, gb.max.Y)

    # the leader genuinely extends beyond its label (else the test proves nothing)
    assert gb.min.X < lb[0] - 0.5 or gb.max.X > lb[2] + 0.5, "leader shaft not past its label?"

    obst = strip_obstacles(dwg)
    occ = _occupied_boxes(dwg)
    assert any(_same(x, full) for x in obst), "full leader footprint missing from strip_obstacles"
    # _occupied_boxes records (at most) the narrower label box for this leader
    assert not any(_same(x, full) for x in occ)


def test_strip_obstacles_view_filter():
    # A box with a side-drilled hole: side-view occupancy excludes front/plan items.
    part = Box(60, 40, 30) - Pos(0, 0, 8) * Rotation(0, 90, 0) * Cylinder(3, 80)
    dwg = build_drawing(part)

    everywhere = strip_obstacles(dwg)
    side = strip_obstacles(dwg, view="side")
    side_annos = list(dwg.annotations_in_view("side"))

    assert side, "expected some side-view obstacles"
    assert len(side) < len(everywhere), "view filter should narrow the set"
    # one obstacle per side-view annotation that bbox-es (≤ the annotation count)
    assert len(side) <= len(side_annos)
