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


def test_strip_obstacles_view_filter_drops_other_ortho_views():
    # A box with a side-drilled hole: the side query excludes front/plan-owned
    # blocks (compose-then-pack keeps them disjoint) but is narrower than the whole.
    part = Box(60, 40, 30) - Pos(0, 0, 8) * Rotation(0, 90, 0) * Cylinder(3, 80)
    dwg = build_drawing(part)

    everywhere = strip_obstacles(dwg)
    side = strip_obstacles(dwg, view="side")
    assert 0 < len(side) < len(everywhere), "view filter should narrow the set"

    # a front/plan-owned annotation is present overall but excluded from the side query
    other = next(
        (n for n, _ in dwg.iter_annotations() if dwg.view_of(n) in ("front", "plan")), None
    )
    assert other is not None, "fixture should place a front/plan annotation"
    b = dwg._named[other].bounding_box()
    obox = (b.min.X, b.min.Y, b.max.X, b.max.Y)
    assert any(_same(x, obox) for x in everywhere)
    assert not any(_same(x, obox) for x in side), f"{other} (other ortho view) leaked into side"


def test_strip_obstacles_keeps_section_hatch_in_every_per_view_query():
    # The section hatch is owned by no ortho view (view_of is None); a per-view
    # strip solve must still avoid it — _occupied_boxes special-cased it by name,
    # and restricting it to view=None would re-open that blind spot (review S1).
    part = Box(80, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)
    dwg = build_drawing(part)
    assert "section_hatch" in dwg._named and dwg.view_of("section_hatch") is None
    b = dwg._named["section_hatch"].bounding_box()
    hbox = (b.min.X, b.min.Y, b.max.X, b.max.Y)
    for v in ("front", "plan", "side"):
        assert any(_same(x, hbox) for x in strip_obstacles(dwg, view=v)), f"hatch dropped from {v}"
