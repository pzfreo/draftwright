"""Unit tests for the ADR 0009 collect-then-solve strip-placement stage.

Grows with the boundary-labeling migration (tracking #320). P0b (#317): the
complete per-strip occupancy model — `strip_obstacles` — that closes the
`_occupied_boxes` blind spots behind #133/#225/#305. P0c (#317): the
collect-then-solve seam — `StripCandidate` (a measured render-intent) + `plan_strip`
(order = site order ⇒ crossing-free, then 1-D spacing). P2 (#322): `plan_strip`
priority selection — drop the lowest-priority candidates until the rest fit, the
ranked replacement for the engine's arrival-order drops (prerequisite for routing a
production placer without regressing busy strips).
"""

from __future__ import annotations

from build123d import Box, BuildPart, Cylinder, Hole, Pos, Rotation

from draftwright import build_drawing
from draftwright.annotations._common import (
    _occupied_boxes,
    strip_obstacles,
)
from draftwright.layout import StripCandidate, plan_strip


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


# --- plan_strip: the collect-then-solve seam (pure geometry, no OCC) --------


def _cand(key, y, w=6.0, h=3.0, priority=0):
    return StripCandidate(key=key, anchor=(0.0, y), size=(w, h), priority=priority)


def test_plan_strip_places_in_site_order_spaced_and_in_bounds():
    res = plan_strip([_cand("a", 10), _cand("b", 12), _cand("c", 14)], lo=0, hi=100, min_gap=5)
    assert set(res.placed) == {"a", "b", "c"} and res.dropped == ()
    p = res.placed
    assert p["a"] <= p["b"] <= p["c"], "site order (crossing-free) not preserved"
    ys = sorted(p.values())
    assert all(b - a >= 5 - 1e-9 for a, b in zip(ys, ys[1:])), "min_gap violated"
    assert all(0 <= v <= 100 for v in p.values()), "out of bounds"


def test_plan_strip_orders_by_site_regardless_of_input_order():
    # shuffled input still resolves to site (anchor) order — the crossing-free move
    p = plan_strip(
        [_cand("c", 14), _cand("a", 10), _cand("b", 12)], lo=0, hi=100, min_gap=5
    ).placed
    assert p["a"] <= p["b"] <= p["c"]


def test_plan_strip_deterministic_key_tiebreak():
    cands = [_cand("b", 10), _cand("a", 10)]  # equal anchors → ordered by key
    p1 = plan_strip(cands, 0, 100, 5)
    p2 = plan_strip(list(reversed(cands)), 0, 100, 5)
    assert p1 == p2
    assert p1.placed["a"] <= p1.placed["b"]


def test_plan_strip_selection_drops_lowest_priority():
    # three 5 mm-gapped labels can't fit a 6 mm strip → keep the two highest-priority
    cands = [
        StripCandidate("hi", (0.0, 0.0), (6, 3), priority=5),
        StripCandidate("mid", (0.0, 2.0), (6, 3), priority=3),
        StripCandidate("lo", (0.0, 4.0), (6, 3), priority=1),
    ]
    res = plan_strip(cands, lo=0, hi=6, min_gap=5)
    assert res.dropped == ("lo",), "should drop only the lowest-priority candidate"
    assert set(res.placed) == {"hi", "mid"}


def test_plan_strip_selection_drops_are_lowest_first_and_deterministic():
    # a zero-width strip fits exactly one → drop the two lowest priorities, in
    # lowest-first order; the highest-priority survivor is kept
    cands = [_cand("a", 0, priority=2), _cand("b", 0, priority=1), _cand("c", 0, priority=3)]
    res = plan_strip(cands, lo=0, hi=0, min_gap=5)
    assert set(res.placed) == {"c"}, "highest priority should survive"
    assert res.dropped == ("b", "a"), "dropped lowest-priority first (1, then 2)"


def test_plan_strip_drops_at_exact_feasibility_boundary_not_greedy():
    # At (n-1)*min_gap == hi-lo the exact 1-D solve is infeasible (strict); a greedy
    # pack from lo would just fit all n, but plan_strip DROPS one and re-solves. This
    # is the one intended behaviour change when the bore-callout Pass-2 moved off the
    # old greedy prefix-drop onto plan_strip (#321 P1a) — pin it against drift. The
    # numbers are the deleted adapter test's knife-edge case: gap 4.73 in [8.44, 22.63]
    # gives (4-1)*4.73 == 14.19 == hi-lo, the exact boundary.
    anchors = [4.52, 5.29, 16.07, 22.13]
    cands = [StripCandidate(f"c{i}", (0.0, y), (6, 3)) for i, y in enumerate(anchors)]
    res = plan_strip(cands, lo=8.44, hi=22.63, min_gap=4.73)
    assert len(res.placed) == 3 and len(res.dropped) == 1, "must drop at the exact boundary"
    # a strictly looser strip fits all four (proves the boundary, not a blanket drop)
    loose = plan_strip(cands, lo=8.44, hi=22.64, min_gap=4.73)
    assert len(loose.placed) == 4 and loose.dropped == ()


def test_plan_strip_x_axis():
    cands = [StripCandidate("a", (10, 0), (6, 3)), StripCandidate("b", (14, 0), (6, 3))]
    p = plan_strip(cands, 0, 100, 5, axis="x").placed
    assert p["a"] <= p["b"]


def test_plan_strip_empty():
    res = plan_strip([], 0, 100, 5)
    assert res.placed == {} and res.dropped == ()


def test_plan_strip_rejects_duplicate_keys():
    # keys key the result — a silent overwrite would drop a candidate (cf.
    # LayoutSolver.register, which also raises).
    import pytest

    with pytest.raises(ValueError, match="unique"):
        plan_strip([_cand("a", 10), _cand("a", 20)], lo=0, hi=100, min_gap=5)


# --- _envelope_tier: overall dim stacks OUTSIDE every obstacle (#321 P3a-envelope) ---


def _fake_dwg(obstacles, view="side"):
    # Minimal dwg for strip_obstacles/_envelope_tier: named annotations exposing a
    # bounding_box() and a single owning view. obstacles: {name: (x0, y0, x1, y1)}.
    class _P:
        def __init__(s, x, y):
            s.X, s.Y = x, y

    class _BB:
        def __init__(s, x0, y0, x1, y1):
            s.min, s.max = _P(x0, y0), _P(x1, y1)

    class _Obst:
        def __init__(s, bb):
            s._bb = bb

        def bounding_box(s):
            return s._bb

    class _Dwg:
        def iter_annotations(s):
            return [(n, _Obst(_BB(*b))) for n, b in obstacles.items()]

        def view_of(s, n):
            return view

    return _Dwg()


def test_envelope_tier_stacks_outside_a_middle_tier_obstacle():
    # A below strip (anchor 61, gap 8 → inner tier at 53, outer_limit 10). An obstacle
    # sits in a MIDDLE tier [30,36] with the inner tier [40,53] left FREE. The overall
    # dim must land OUTSIDE it (≤ 30 − spacing), not in the nearer-the-view free tier —
    # picking the innermost free segment would invert the ISO stack (review #1).
    from draftwright._core import Strip
    from draftwright.annotations.from_model import _envelope_tier

    strip = Strip(anchor=61.0, outer_limit=10.0, direction=-1.0)  # gap 8, spacing 4
    dwg = _fake_dwg({"mid": (100.0, 30.0, 120.0, 36.0)})
    pd = _envelope_tier(dwg, strip, "side", size=8.0)
    assert pd is not None and pd <= 30.0, f"envelope inverted into inner tier: pd={pd}"


def test_envelope_tier_uses_inner_tier_when_strip_is_clear():
    # No obstacles → the overall dim takes the innermost tier (anchor − gap = 53),
    # matching the first Strip.allocate it replaces (byte-identity on hole-free parts).
    from draftwright._core import Strip
    from draftwright.annotations.from_model import _envelope_tier

    strip = Strip(anchor=61.0, outer_limit=10.0, direction=-1.0)
    assert _envelope_tier(_fake_dwg({}), strip, "side", size=8.0) == 53.0
