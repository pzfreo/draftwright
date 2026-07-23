"""Unit tests for the ADR 0009 collect-then-solve strip-placement stage.

Grows with the boundary-labeling migration (tracking #320). P0b (#317): the
complete per-strip occupancy model — `strip_obstacles` — that closes the
label-box-only blind spots behind #133/#225/#305. P0c (#317): the
collect-then-solve seam — `StripCandidate` (a measured render-intent) + `plan_strip`
(order = site order ⇒ crossing-free, then 1-D spacing). P2 (#322): `plan_strip`
priority selection — drop the lowest-priority candidates until the rest fit, the
ranked replacement for the engine's arrival-order drops (prerequisite for routing a
production placer without regressing busy strips).
"""

from __future__ import annotations

from build123d import Box, BuildPart, Cylinder, Hole, Pos, Rotation

from draftwright import build_drawing
from draftwright.annotations._common import strip_obstacles
from draftwright.layout import StripCandidate, plan_strip


def _same(a, b, tol=1e-6):
    return a is not None and b is not None and all(abs(x - y) <= tol for x, y in zip(a, b))


def _drive_screw_x():
    # X-turned cylinder + coaxial axial bore: centrelines + a bore-callout leader.
    with BuildPart() as p:
        Cylinder(radius=6, height=20)
        Hole(0.8, depth=8)
    return Rotation(0, 90, 0) * p.part


def test_strip_obstacles_captures_bare_centreline():
    # A label-box-only occupancy misses bare centrelines; the complete occupancy
    # must not — a centreline through a callout's row is exactly the #305 blind spot.
    dwg = build_drawing(_drive_screw_x())
    cl = dwg.get_annotation("centerline_front")
    b = cl.bounding_box()
    cbox = (b.min.X, b.min.Y, b.max.X, b.max.Y)

    obst = strip_obstacles(dwg)

    # #685 decomposed occupancy: the centreline arrives as stroke boxes (possibly
    # several pieces), not one exact hull — assert its midpoint and both ends are
    # each inside some obstacle box.
    my = (cbox[1] + cbox[3]) / 2
    pts = [(cbox[0] + 0.5, my), ((cbox[0] + cbox[2]) / 2, my), (cbox[2] - 0.5, my)]

    def _holds(boxes, px, py):
        return any(x[0] <= px <= x[2] and x[1] <= py <= x[3] for x in boxes)

    assert all(_holds(obst, *pt) for pt in pts), "centreline missing from strip_obstacles"


def test_strip_obstacles_captures_full_leader_footprint_not_just_label():
    # A bore callout's leader shaft extends well past its text box. A label-box-only
    # occupancy records only the label box; strip_obstacles must record the full
    # footprint, or a placer thinks the shaft's row is free (#133/#225).
    dwg = build_drawing(_drive_screw_x())
    name, leader = next((n, o) for n, o in dwg.iter_annotations() if n.startswith("hc_"))

    lb = leader.label_bbox
    gb = leader.bounding_box()

    # the leader genuinely extends beyond its label (else the test proves nothing)
    assert gb.min.X < lb[0] - 0.5 or gb.max.X > lb[2] + 0.5, "leader shaft not past its label?"

    obst = strip_obstacles(dwg)

    # #685 decomposed occupancy: assert the shaft REGION beyond the label is covered —
    # some obstacle box must contain the leader's tip-side extreme, which the label
    # box does not reach.
    # #688 review: sample the ACTUAL segment endpoints (a diagonal shaft's hull
    # midpoint is not on the shaft), with no slack beyond the production pad.
    def _holds(boxes, px, py):
        return any(x[0] <= px <= x[2] and x[1] <= py <= x[3] for x in boxes)

    seg_pts = [pt for seg in leader.segments for pt in seg]
    assert seg_pts, "leader exposes no segments - the decomposed contract lost its subject"
    assert all(_holds(obst, px, py) for px, py in seg_pts), (
        "leader stroke endpoints missing from strip_obstacles"
    )


def test_coaxial_bore_on_rotational_part_is_not_over_located():
    # #309: the turning-axis bore of a rotational part is located by its centreline,
    # not a redundant position dim. The X-turned drive screw's coaxial bore emitted a
    # y-offset dim (side-below) AND a z-height dim (right) both reading its offset from
    # the datum — the "6"/"6" over-dimensioning. `_locate_off_axis_holes` now excludes
    # the coaxial bore, so no dim_loc_* is placed, and lint stays clean because
    # coverage already credits the bore via its centre mark. (Non-rotational parts and
    # genuine off-centre side-drilled holes are unaffected — the byte-identical
    # side_drilled/dshape/plate_holes snapshots guard that.)
    dwg = build_drawing(_drive_screw_x())
    locs = [n for n, _ in dwg.iter_annotations() if n.startswith("dim_loc")]
    assert locs == [], f"coaxial bore over-located: {locs}"
    assert not any(getattr(i, "code", None) == "feature_not_located" for i in dwg.lint())


def test_rotational_bore_leaders_bounded_to_front_view():
    # #374: the concentric-bore leader stack is placed by plan_strip within the front-view height
    # band, not by the old uncapped `cz + (i-(nb-1)/2)*pitch` fixed stacking that could overrun the
    # view (the CTC-02 defect shape). Assert every ldr_z* leader lands inside [FV_Y ± fv_hh].
    from build123d import Cylinder, Pos

    part = (
        Cylinder(20, 40)
        - Cylinder(5, 40)
        - Pos(0, 0, 14) * Cylinder(8, 12)
        - Pos(0, 0, -14) * Cylinder(6.5, 12)
    )
    dwg = build_drawing(part)
    fb = dwg.view_bounds("front")
    lo, hi = fb[1], fb[3]
    ldrs = [o for n, o in dwg.iter_annotations() if n.startswith("ldr_z")]
    assert len(ldrs) >= 2, "fixture should place several concentric-bore leaders"
    for o in ldrs:
        cy = o.bounding_box().center().Y
        assert lo - 1e-6 <= cy <= hi + 1e-6, f"bore leader at {cy:.2f} outside front-view band"


def test_rotational_bore_leaders_symmetric_when_room():
    # #374 review: with room, plan_strip must reproduce the old symmetric-about-cz stack EXACTLY
    # — each bore keeps its natural `cz + (i-(nb-1)/2)*pitch`. An even bore count (a counterbore =
    # 2 concentric ø) is the case where an all-equal natural would have shifted the stack down by
    # pitch/2; the symmetric naturals keep it centred on the turning axis.
    from build123d import Cylinder, Pos

    part = Cylinder(25, 40) - Cylinder(6, 40) - Pos(0, 0, 14) * Cylinder(10, 12)  # 2 bores (even)
    dwg = build_drawing(part)
    ys = sorted(
        o.bounding_box().center().Y for n, o in dwg.iter_annotations() if n.startswith("ldr_z")
    )
    assert len(ys) == 2
    fv_y = dwg.at("front", *dwg.centroid)[1]
    assert abs((ys[0] + ys[-1]) / 2 - fv_y) < 1e-6, "even bore stack not centred on the axis"


def test_rotational_bore_leader_overflow_excluded_from_coverage():
    # #374 review: a dropped bore must be registered via ctx.coverage.drop_diam so coverage lint does
    # not double-report it as feature_not_dimensioned on top of the callout_dropped warning.
    from build123d import Cylinder, Pos

    part = Cylinder(70, 6)
    for i, r in enumerate(
        [60, 52, 44, 36, 28, 20, 12]
    ):  # many nested bores → front view overflows
        part -= Pos(0, 0, 3 - i * 0.7) * Cylinder(r, 6)
    dwg = build_drawing(part)
    dropped = dwg.coverage.dropped_diams
    assert dropped, "overflowed bore leaders should register as dropped diams"
    assert not any(i.code == "feature_not_dimensioned" for i in dwg.lint())
    # priority = diameter: the larger bores are retained, only the smaller ones drop
    kept = [float(o.label.lstrip("ø")) for n, o in dwg.iter_annotations() if n.startswith("ldr_z")]
    assert kept and max(dropped) < min(kept), "the ranking should drop the smallest bores first"


def test_linear_array_pitch_dim_placed_via_side_strip_clean():
    # #374 part 2: an axis-aligned linear-array pitch dim is placed onto its side's zone strip via
    # the obstacle-aware carve (not the old count-based `10*prior` stack). Assert it is present,
    # on-page, and overlaps nothing (no annotation_overlap lint) — the carve's job.
    from build123d import Box, Cylinder, Pos

    part = Box(120, 40, 10)
    for x in (-45, -15, 15, 45):  # a 4-hole row along X → one "3× 30" pitch dim
        part -= Pos(x, 0, 0) * Cylinder(3, 10)
    dwg = build_drawing(part)
    pitch = [o for n, o in dwg.iter_annotations() if n.startswith("dim_pitch")]
    assert len(pitch) == 1, "the linear array should get exactly one pitch dim"
    b = pitch[0].bounding_box()
    assert (
        -1 <= b.min.X and b.max.X <= dwg.page_w + 1 and -1 <= b.min.Y and b.max.Y <= dwg.page_h + 1
    )
    assert not any(i.code == "annotation_overlap" for i in dwg.lint())


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
    o = dwg.get_annotation(other)
    lb2 = getattr(o, "label_bbox", None)
    if lb2 is not None:
        cx, cy = (lb2[0] + lb2[2]) / 2, (lb2[1] + lb2[3]) / 2
    else:
        b = o.bounding_box()
        cx, cy = (b.min.X + b.max.X) / 2, (b.min.Y + b.max.Y) / 2
    # #685: compare by containment of the annotation's solid centre, not hull identity.
    assert any(x[0] <= cx <= x[2] and x[1] <= cy <= x[3] for x in everywhere)
    assert not any(x[0] <= cx <= x[2] and x[1] <= cy <= x[3] for x in side), (
        f"{other} (other ortho view) leaked into side"
    )


def test_strip_obstacles_keeps_section_hatch_in_every_per_view_query():
    # The section hatch is owned by no ortho view (view_of is None); a per-view
    # strip solve must still avoid it — restricting it to view=None would
    # re-open that blind spot (review S1).
    part = Box(80, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)
    dwg = build_drawing(part)
    assert "section_hatch" in dwg.annotations() and dwg.view_of("section_hatch") is None
    b = dwg.get_annotation("section_hatch").bounding_box()
    hbox = (b.min.X, b.min.Y, b.max.X, b.max.Y)
    for v in ("front", "plan", "side"):
        assert any(_same(x, hbox) for x in strip_obstacles(dwg, view=v)), f"hatch dropped from {v}"


# --- Escalation objects (ADR 0009 Amendment 1, P5-strand-2 scaffolding, #351) -----


def test_record_callout_drop_emits_a_callout_escalation():
    # PR-2 routing: a dropped hole callout emits a first-class Escalation into
    # ctx.escalations (which the tabulation resolver now triggers on), alongside the
    # `callout_dropped` lint code (kept for coverage). 1:1 with the code → byte-identical.
    # PR-3 (#351): `feature` now carries the dropped group's IR feature (a PatternFeature
    # when it's a fully-surviving recognised pattern, else None) rather than the diameter —
    # the resolver groups pattern balloons on it.
    from draftwright.annotations._common import PlacementContext
    from draftwright.annotations.holes import _record_callout_drop
    from draftwright.linting.coverage import CoverageState
    from draftwright.registry import AnnotationRegistry

    # The build-issue + dropped-diameter bookkeeping now routes through the ctx's registry/
    # coverage (#639), not the drawing — so the dwg arg is inert here.
    ctx = PlacementContext(registry=AnnotationRegistry(), coverage=CoverageState())
    _record_callout_drop(ctx, object(), "plan", 6.0, "no room beside the view")
    assert [i.code for i in ctx.registry.issues] == ["callout_dropped"]
    assert ctx.coverage.dropped_diams == [6.0]
    assert len(ctx.escalations) == 1
    e = ctx.escalations[0]
    assert e.kind == "callout" and e.view == "plan" and e.feature is None

    ctx2 = PlacementContext(registry=AnnotationRegistry(), coverage=CoverageState())
    sentinel = object()
    _record_callout_drop(ctx2, object(), "plan", 6.0, "no room beside the view", sentinel)
    assert ctx2.escalations[0].feature is sentinel


def test_record_slot_drop_emits_a_slot_escalation():
    # #351 PR-4a: a dropped slot dim (width/length/position) emits a first-class
    # "slot" Escalation alongside the existing slot_dim_dropped lint code — purely
    # additive, no resolver consumes it yet (slots have no natural grouping remedy).
    from draftwright.annotations._common import PlacementContext
    from draftwright.annotations.from_model import _record_slot_drop
    from draftwright.registry import AnnotationRegistry

    ctx = PlacementContext(registry=AnnotationRegistry())
    sentinel = object()
    _record_slot_drop(ctx, object(), "width", 0, "plan", sentinel)
    assert [(i.severity, i.code) for i in ctx.registry.issues] == [("info", "slot_dim_dropped")]
    assert len(ctx.escalations) == 1
    e = ctx.escalations[0]
    assert e.kind == "slot" and e.view == "plan" and e.feature is sentinel


def test_record_pmi_drop_emits_a_pmi_escalation():
    # #351 PR-4a: a PMI dim that finds no strip space was previously silent (no lint
    # code at all). Now records pmi_dropped + a first-class "pmi" Escalation.
    from types import SimpleNamespace

    from draftwright.annotations._common import PlacementContext
    from draftwright.annotations.from_model import _record_pmi_drop
    from draftwright.registry import AnnotationRegistry

    ctx = PlacementContext(registry=AnnotationRegistry())
    rec = SimpleNamespace(pmi_kind="linear")
    _record_pmi_drop(ctx, object(), "X", "12.0", rec)
    assert [(i.severity, i.code) for i in ctx.registry.issues] == [("warning", "pmi_dropped")]
    assert len(ctx.escalations) == 1
    e = ctx.escalations[0]
    assert e.kind == "pmi" and e.view == "front" and e.feature is rec

    ctx2 = PlacementContext(registry=AnnotationRegistry())
    _record_pmi_drop(ctx2, object(), "Y", "12.0", SimpleNamespace(pmi_kind="linear"))
    assert ctx2.escalations[0].view == "side"

    # A bore diameter/radius uses a DIFFERENT view table (the view where the bore
    # appears as a circle) from linear dims — conflating the two mislabelled every
    # dropped bore diameter/radius (caught by review, #351 PR-4a).
    for ax, want_view in (("Z", "plan"), ("X", "side"), ("Y", "front")):
        ctx3 = PlacementContext(registry=AnnotationRegistry())
        _record_pmi_drop(ctx3, object(), ax, "ø12.0", SimpleNamespace(pmi_kind="diameter"))
        assert ctx3.escalations[0].view == want_view, ax


def test_escalation_is_a_frozen_value_with_default_remedies():
    import dataclasses

    import pytest

    from draftwright.annotations._common import Escalation

    e = Escalation(kind="callout", view="plan", feature=("hole", 1), reason="strip_full")
    assert (e.kind, e.view, e.reason) == ("callout", "plan", "strip_full")
    assert e.remedies == ()  # default: the resolver's ladder decides
    e2 = Escalation("location", None, None, "no_room", ("table", "drop"))
    assert e2.view is None and e2.remedies == ("table", "drop")
    with pytest.raises(dataclasses.FrozenInstanceError):  # immutable value object
        e.kind = "x"


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


def test_plan_strip_priority_is_a_float_magnitude_drops_smallest():
    # D3 (#322): priority is a per-feature magnitude (a hole's diameter) — a float, not
    # just an int rank. Over capacity, the smallest-bore candidate drops first.
    cands = [
        StripCandidate("big", (0.0, 0.0), (6, 3), priority=6.0),
        StripCandidate("mid", (0.0, 2.0), (6, 3), priority=3.2),
        StripCandidate("small", (0.0, 4.0), (6, 3), priority=1.5),
    ]
    res = plan_strip(cands, lo=0, hi=6, min_gap=5)  # 6 mm strip holds ~2 at 5 mm gap
    assert res.dropped == ("small",), "smallest bore should drop first"
    assert set(res.placed) == {"big", "mid"}


def test_plan_strip_x_axis():
    cands = [StripCandidate("a", (10, 0), (6, 3)), StripCandidate("b", (14, 0), (6, 3))]
    p = plan_strip(cands, 0, 100, 5, axis="x").placed
    assert p["a"] <= p["b"]


def test_plan_strip_uses_per_pair_gaps_for_heterogeneous_sizes():
    # P4a (#318): candidates with different strip-axis sizes are spaced by the
    # LARGER of each pair's own size (or the floor min_gap), not one global gap —
    # the same per-pair-gap rule #81 originally added for the (since-retired,
    # #547) LayoutSolver.solve_strip's heterogeneous Placeables. Exercises
    # plan_strip's own gap-list wiring (ordered[i]/[i+1] indexing, idx
    # selection), not just the underlying PAVA primitive (which is already
    # covered elsewhere, in isolation).
    cands = [
        StripCandidate("a", (0.0, 0.0), (6, 4), priority=0),
        StripCandidate("b", (0.0, 0.0), (6, 4), priority=0),
        StripCandidate("c", (0.0, 0.0), (6, 12), priority=0),
    ]
    res = plan_strip(cands, lo=0, hi=100, min_gap=1)
    p = res.placed
    assert abs((p["b"] - p["a"]) - 4) < 1e-6  # max(4, 4, floor 1)
    assert abs((p["c"] - p["b"]) - 12) < 1e-6  # max(4, 12, floor 1)


def test_plan_strip_anchored_candidate_stays_on_its_natural():
    # P4b (#318, Amendment 4): an `anchored` candidate is kept at its natural
    # position while the rest flow around it. Two callouts 2 mm apart naturally
    # but needing a 7 mm gap → a total-leader-length tie; anchoring the first
    # pins it (how a central hole's callout stays on the view-centre row).
    import pytest

    anchored = StripCandidate("mid", (0.0, 100.0), (6, 3), anchored=True)
    other = StripCandidate("off", (0.0, 102.0), (6, 3))
    res = plan_strip([anchored, other], lo=0, hi=200, min_gap=7)
    assert res.placed["mid"] == pytest.approx(100.0), "anchored candidate slid off its natural"
    assert res.placed["off"] == pytest.approx(107.0)
    # Without the anchor the min-leader tie resolves to the other vertex.
    plain = plan_strip(
        [StripCandidate("mid", (0.0, 100.0), (6, 3)), other], lo=0, hi=200, min_gap=7
    )
    assert plain.placed["mid"] == pytest.approx(95.0)


def test_carve_free_segments_merges_overlapping_intervals():
    # Two overlapping keep-out intervals must merge into ONE blocked region
    # before the free segments are computed, not be treated as independently
    # subtractable (which would under-block the overlap). Equivalent to the
    # retired `_feasible_segments`'s `test_overlapping_bands_merge`.
    from draftwright.annotations._common import carve_free_segments

    segs = carve_free_segments(0.0, 100.0, [(30.0, 50.0), (45.0, 70.0)], 0.0)
    assert segs == [(0.0, 30.0), (70.0, 100.0)]


def test_carve_free_segments_clips_intervals_at_the_strip_edge():
    # An interval extending past [lo, hi] clips to the strip bounds rather than
    # producing a free segment outside them. Equivalent to the retired
    # `_feasible_segments`'s `test_edge_bands_clip_to_the_strip`.
    from draftwright.annotations._common import carve_free_segments

    segs = carve_free_segments(0.0, 100.0, [(-10.0, 10.0), (90.0, 110.0)], 0.0)
    assert segs == [(10.0, 90.0)]


def test_carve_free_segments_fully_covered_leaves_no_segment():
    # An interval spanning the whole strip leaves zero free segments — the
    # trigger for `holes.py`'s whole-range-blocked fallback. Equivalent to the
    # retired `_feasible_segments`'s `test_band_covering_everything_leaves_no_segment`.
    from draftwright.annotations._common import carve_free_segments

    assert carve_free_segments(40.0, 60.0, [(30.0, 70.0)], 0.0) == []


def test_carve_then_plan_strip_keeps_a_label_off_a_reserved_row():
    # ADR 0009 Amendment 9 (#381): `plan_strip` no longer knows about keep-out
    # bands itself — a caller carves the band out of the strip with the same
    # `carve_free_segments` every other obstacle already uses, then calls
    # `plan_strip` once per free segment (see `annotations/holes.py`). A label
    # whose natural sits on the band lands in the nearer free segment, at its
    # edge; one already clear stays put.
    import pytest

    from draftwright.annotations._common import carve_free_segments

    segs = carve_free_segments(0.0, 100.0, [(42.0, 58.0)], 0.0)
    assert segs == [(0.0, 42.0), (58.0, 100.0)]
    on = plan_strip([_cand("on", 50)], lo=0.0, hi=42.0, min_gap=5)
    assert on.placed["on"] == pytest.approx(42.0), "on-row label not moved off the band"
    clear = plan_strip([_cand("clear", 80)], lo=58.0, hi=100.0, min_gap=5)
    assert clear.placed["clear"] == pytest.approx(80.0), "already-clear label should not move"
    # Without the band the on-row label stays on its natural.
    plain = plan_strip([_cand("on", 50)], lo=0, hi=100, min_gap=5)
    assert plain.placed["on"] == pytest.approx(50.0)


def test_carve_around_a_band_keeps_an_anchored_candidate_on_its_natural():
    # #381: the retired banded-DP kept one representative per labels-placed
    # count, so a band could drag an `anchored` candidate off its natural when
    # a cheaper-looking prefix (from an unrelated candidate) won the DP's
    # pruning. Carving the band out first and solving each free segment
    # independently (Amendment 9) resolves this BY CONSTRUCTION: an anchored
    # candidate assigned to its own band-free segment is never in the same
    # solve as anything the band would have forced a trade-off against.
    # naturals [29, 34], gap 10, band (30, 33) — the DP's own reachable
    # regression case (docs/adr/0009 Amendment 5) — with label 1 anchored.
    import pytest

    from draftwright.annotations._common import carve_free_segments

    segs = carve_free_segments(0.0, 100.0, [(30.0, 33.0)], 0.0)
    assert segs == [(0.0, 30.0), (33.0, 100.0)]
    below = plan_strip([StripCandidate("below", (0.0, 29.0), (6, 3))], lo=0.0, hi=30.0, min_gap=10)
    above = plan_strip(
        [StripCandidate("above", (0.0, 34.0), (6, 3), anchored=True)],
        lo=33.0,
        hi=100.0,
        min_gap=10,
    )
    assert below.placed["below"] == pytest.approx(29.0)
    assert above.placed["above"] == pytest.approx(34.0), (
        "anchored candidate dragged off its natural"
    )


def test_carve_free_segments_no_bands_is_the_whole_strip():
    # Equivalent to the retired `_feasible_segments`'s
    # `test_no_bands_is_the_whole_strip` — the base case the other three
    # carried-over cases (merge/clip/fully-covered) sit alongside.
    from draftwright.annotations._common import carve_free_segments

    assert carve_free_segments(0.0, 100.0, [], 0.0) == [(0.0, 100.0)]


def test_holes_band_clearance_exceeds_min_gap_on_the_real_draft():
    # Guards the invariant docs/adr/0009's "Investigated, not fixed" paragraph
    # relies on to call the cross-segment min_gap violation unreachable: a
    # band's half-width (clr) must exceed min_gap on the actual production
    # draft (builder.py's _assemble draft_preset() call), or that paragraph's
    # reachability argument is wrong.
    from build123d_drafting.helpers import draft_preset

    from draftwright._core import _FONT_SIZE

    draft = draft_preset(font_size=_FONT_SIZE, decimal_precision=1)
    clr = draft.font_size + 3 * draft.pad_around_text
    min_gap = draft.font_size + 2 * draft.pad_around_text
    assert clr > min_gap


def test_plan_strip_min_gap_floors_a_pair_smaller_than_it():
    # A pair whose sizes are both below the caller's min_gap floor must still get
    # at least min_gap of separation — min_gap is a floor, not just a fallback.
    cands = [
        StripCandidate("a", (0.0, 0.0), (6, 1), priority=0),
        StripCandidate("b", (0.0, 0.0), (6, 1), priority=0),
    ]
    res = plan_strip(cands, lo=0, hi=100, min_gap=5)
    assert abs((res.placed["b"] - res.placed["a"]) - 5) < 1e-6


def test_bore_callout_priority_is_the_hole_diameter(monkeypatch):
    # D3 (#322) wiring: each bore-callout StripCandidate's priority is the hole
    # DIAMETER (largest wins over-capacity), not the old n-j prefix-keep placeholder.
    # Spy on plan_strip because the corpus never over-fills a callout strip, so the
    # policy is otherwise unobservable (byte-identical output).
    from build123d import Box, BuildPart, Cylinder, Mode, Pos

    import draftwright.annotations.holes as holes_mod

    captured = []
    orig = holes_mod.plan_strip

    def _spy(cands, *a, **k):
        captured.extend(cands)
        return orig(cands, *a, **k)

    monkeypatch.setattr(holes_mod, "plan_strip", _spy)

    with BuildPart() as p:
        Box(80, 60, 10)
        with BuildPart(mode=Mode.SUBTRACT):
            Pos(-25, 15, 0) * Cylinder(radius=3, height=12)  # ø6
        with BuildPart(mode=Mode.SUBTRACT):
            Pos(20, -18, 0) * Cylinder(radius=1.5, height=12)  # ø3
    build_drawing(p.part)

    pri = {round(c.priority, 1) for c in captured if c.key.startswith("hc_")}
    assert pri, "no bore-callout candidates were routed through plan_strip"
    assert pri <= {3.0, 6.0}, f"priorities should be hole diameters, got {pri}"
    assert 6.0 in pri, "the ø6 bore's priority should be its diameter, not an n-j rank"


def test_place_strip_candidates_reserves_outermost_label_within_bounds():
    # #338: the outermost label extends `tier` beyond its dim line; place_strip_candidates
    # must keep it within outer_limit (the old Strip.allocate checked start+tier<=limit).
    # An above-strip near=8..limit=20 (range 12) fits 2 dim LINES at pad=9 (8, 17), but the
    # 2nd label [17,22] overshoots 20 — so only 1 may be placed; the 2nd is returned.
    from draftwright._core import Strip
    from draftwright.annotations._common import place_strip_candidates

    class _Dwg:
        def __init__(s):
            s.added = []

        def iter_annotations(s):
            return list(s.added)

        def view_of(s, n):
            return "plan"

        def add(s, obj, name, view=None, feature=None):
            s.added.append((name, obj))

    strip = Strip(anchor=0.0, outer_limit=20.0, direction=1.0, gap=8.0, spacing=4.0)  # near=8
    dwg = _Dwg()
    cands = [("a", lambda pos: ("dim", pos)), ("b", lambda pos: ("dim", pos))]
    left = place_strip_candidates(dwg, strip, "plan", "y", cands, tier=5.0, force=True)
    assert len(dwg.added) == 1, "outermost label would overshoot outer_limit — must not place it"
    assert len(left) == 1, "the unplaceable candidate must be returned, not dropped silently"


def test_place_strip_candidates_priority_survives_key_order():
    # #357: over-capacity, plan_strip drops the lowest (priority, key). place_strip_candidates
    # must PLUMB a per-name priority into StripCandidate — otherwise every candidate is priority 0
    # and the drop is by stacking-key alone. Here the two candidates cannot both fit (tall sizes
    # force a 40 mm gap into a ~22 mm span). Candidate "a" gets the smaller key (dropped by key
    # order alone), so a HIGH priority on "a" must flip the outcome: "a" survives, "b" drops.
    from draftwright._core import Strip
    from draftwright.annotations._common import place_strip_candidates

    class _Dwg:
        def __init__(s):
            s.added = []

        def iter_annotations(s):
            return list(s.added)

        def view_of(s, n):
            return "plan"

        def add(s, obj, name, view=None, feature=None):
            s.added.append((name, obj))

    def _run(priorities):
        strip = Strip(anchor=0.0, outer_limit=50.0, direction=1.0, gap=8.0, spacing=4.0)
        dwg = _Dwg()
        cands = [("a", lambda pos: ("dim", pos)), ("b", lambda pos: ("dim", pos))]
        sizes = {"a": (6.0, 40.0), "b": (6.0, 40.0)}  # tall → 40 mm stacking gap, only one fits
        left = place_strip_candidates(
            dwg,
            strip,
            "plan",
            "y",
            cands,
            tier=5.0,
            force=True,
            sizes=sizes,
            priorities=priorities,
        )
        placed = {nm for nm, _ in dwg.added}
        return placed, {nm for nm, _ in left}

    # equal priority → "a" (smaller key) is the victim by key order
    placed0, left0 = _run({})
    assert placed0 == {"b"} and left0 == {"a"}
    # priority on "a" flips it: the important candidate is kept, "b" drops
    placed1, left1 = _run({"a": 5.0})
    assert placed1 == {"a"} and left1 == {"b"}


def test_register_corridor_uses_largest_tier_across_producers():
    # #477: below/right corridors now collect candidates from mixed producers
    # (locations, envelope, GD&T/PMI). Spacing must not depend on which pass registers first.
    from draftwright.annotations._common import (
        CorridorCandidate,
        PlacementContext,
        register_corridor,
    )

    def _cand(name):
        return CorridorCandidate(
            name=name,
            build=lambda _pos: None,
            order=(0, name),
            on_place=lambda _nm: None,
            on_drop=lambda _nm: None,
        )

    ctx = PlacementContext()
    key = ("side", "below")
    register_corridor(ctx, key, object(), "side", "y", 4.0, _cand("small"))
    register_corridor(ctx, key, object(), "side", "y", 9.0, _cand("large"))
    assert ctx.corridor_batch[key]["tier"] == 9.0

    ctx = PlacementContext()
    register_corridor(ctx, key, object(), "side", "y", 9.0, _cand("large"))
    register_corridor(ctx, key, object(), "side", "y", 4.0, _cand("small"))
    assert ctx.corridor_batch[key]["tier"] == 9.0


def test_place_strip_candidates_refills_after_late_forbid_rejection():
    # #524 review: segment preselection must not waste capacity when the chosen
    # high-priority candidate is rejected only after rendering (e.g. title-block
    # forbid). A lower-priority candidate that fits the same segment should backfill it.
    from draftwright._core import Strip
    from draftwright.annotations._common import place_strip_candidates

    class _P:
        def __init__(s, x, y):
            s.X, s.Y = x, y

    class _Obj:
        def __init__(s, x0, y0, x1, y1):
            s._bb = type("_BB", (), {"min": _P(x0, y0), "max": _P(x1, y1)})()

        def bounding_box(s):
            return s._bb

    class _Dwg:
        def __init__(s):
            s.added = []

        def iter_annotations(s):
            return list(s.added)

        def view_of(s, n):
            return "plan"

        def add(s, obj, name, view=None, feature=None):
            s.added.append((name, obj))

    strip = Strip(anchor=0.0, outer_limit=13.0, direction=1.0, gap=0.0, spacing=4.0)
    cands = [
        ("hi", lambda pos: _Obj(-1.0, pos, 1.0, pos + 1.0)),
        ("lo", lambda pos: _Obj(10.0, pos, 12.0, pos + 1.0)),
    ]
    dwg = _Dwg()
    left = place_strip_candidates(
        dwg,
        strip,
        "plan",
        "y",
        cands,
        tier=5.0,
        force=True,
        priorities={"hi": 10.0, "lo": 1.0},
        forbid={"hi": (-2.0, -1.0, 2.0, 2.0)},
    )

    assert {name for name, _ in dwg.added} == {"lo"}
    assert {name for name, _ in left} == {"hi"}


def test_place_strip_candidates_honors_per_candidate_natural_anchor():
    # #511: pinned user dimensions enter the shared corridor with their own natural
    # page coordinate. Unspecified candidates keep the old segment-edge natural.
    from draftwright._core import Strip
    from draftwright.annotations._common import place_strip_candidates

    class _Dwg:
        def __init__(s):
            s.added = []

        def iter_annotations(s):
            return []

        def view_of(s, n):
            return "plan"

        def add(s, obj, name, view=None, feature=None):
            s.added.append((name, obj))

    strip = Strip(anchor=0.0, outer_limit=60.0, direction=1.0, gap=0.0, spacing=4.0)
    dwg = _Dwg()
    left = place_strip_candidates(
        dwg,
        strip,
        "plan",
        "y",
        [("a", lambda pos: ("a", pos)), ("b", lambda pos: ("b", pos))],
        tier=5.0,
        force=True,
        anchored={"b": True},
        naturals={"b": 30.0},
    )

    placed = {name: obj[1] for name, obj in dwg.added}
    assert left == []
    assert placed["b"] == 30.0


def test_place_strip_candidates_ignores_perpendicular_disjoint_obstacle():
    # A right strip stacks along X; the carve projects obstacles onto X. An obstacle that
    # overlaps in X (even after pad inflation) but is DISJOINT in Y — a dim on ANOTHER
    # strip of the view — must NOT block (the slot-width false-collision fix). Without the
    # perpendicular-band filter the X-projection wipes the strip and drops the candidate.
    from draftwright._core import Strip
    from draftwright.annotations._common import place_strip_candidates

    def _obj(x0, y0, x1, y1, tname="Dimension"):
        class _P:
            def __init__(s, x, y):
                s.X, s.Y = x, y

        class _BB:
            def __init__(s):
                s.min, s.max = _P(x0, y0), _P(x1, y1)

        return type(tname, (), {"bounding_box": lambda s: _BB()})()

    class _Dwg:
        def __init__(s, obst):
            s._o, s.added = obst, []

        def iter_annotations(s):
            return list(s._o) + s.added

        def view_of(s, n):
            return "plan"

        def add(s, o, n, view=None, feature=None):
            s.added.append((n, o))

    # obstacle spans the whole strip in X but sits at y=[0,5]; the candidate dim is y=[30,40]
    dwg = _Dwg([("m_env", _obj(0.0, 0.0, 100.0, 5.0))])
    strip = Strip(anchor=6.0, outer_limit=60.0, direction=1.0, gap=2.0, spacing=4.0)  # near=8
    cand = ("m_slot", lambda pos: _obj(pos - 1, 30.0, pos + 1, 40.0))
    left = place_strip_candidates(dwg, strip, "plan", "x", [cand], tier=5.0)
    assert not left and len(dwg.added) == 1, "perpendicular-disjoint obstacle must not block"


def test_carve_free_position_exact_fit_and_innermost_outermost():
    # carve_free_position must place on a strip EXACTLY gap+tier wide — the label reaches
    # outer_limit inclusively, as the old Strip.allocate did (the double-reserve bug
    # dropped it). And it returns the innermost tier by default, outermost when asked.
    from draftwright._core import Strip
    from draftwright.annotations._common import carve_free_position

    class _Dwg:
        def iter_annotations(s):
            return []

        def view_of(s, n):
            return "front"

    # right strip near=116 .. outer=126: exactly one 10 mm tier fits
    tight = Strip(anchor=108.0, outer_limit=126.0, direction=1.0, gap=8.0, spacing=4.0)
    assert carve_free_position(_Dwg(), tight, "front", "x", 10.0, (0.0, 50.0)) == 116.0

    # a roomy strip: innermost is the inner edge (near); outermost still fits within bounds
    wide = Strip(anchor=108.0, outer_limit=160.0, direction=1.0, gap=8.0, spacing=4.0)  # near=116
    inner = carve_free_position(_Dwg(), wide, "front", "x", 10.0, (0.0, 50.0))
    outer = carve_free_position(_Dwg(), wide, "front", "x", 10.0, (0.0, 50.0), outermost=True)
    assert inner == 116.0 and outer + 10.0 <= 160.0 and outer >= inner


def test_carve_free_position_zero_width_perp_band_leader():
    # A narrow-bore PMI Leader queries with a ZERO-WIDTH perp band (px, px) at the leader's
    # x-line. An obstacle straddling px blocks the tier; one perpendicular-disjoint (off to
    # the side) is filtered out and does NOT block. Guards the six Leader sites in
    # render_pmi, which the PMI test fixtures (all wide bores) never exercise.
    from draftwright._core import Strip
    from draftwright.annotations._common import carve_free_position

    def _obj(x0, y0, x1, y1):
        class _P:
            def __init__(s, x, y):
                s.X, s.Y = x, y

        class _BB:
            def __init__(s):
                s.min, s.max = _P(x0, y0), _P(x1, y1)

        return type("Dimension", (), {"bounding_box": lambda s: _BB()})()

    class _Dwg:
        def __init__(s, obst):
            s._o = obst

        def iter_annotations(s):
            return list(s._o)

        def view_of(s, n):
            return "plan"

    # above strip (axis y, perp X): near=108 .. outer=145; leader at px=50
    strip = Strip(anchor=100.0, outer_limit=145.0, direction=1.0, gap=8.0, spacing=4.0)
    # obstacle covering the inner tier [108,118] and STRADDLING x=50 → blocks → pushed out
    blocked = carve_free_position(
        _Dwg([("o", _obj(45, 108, 55, 118))]), strip, "plan", "y", 10.0, (50.0, 50.0)
    )
    # same obstacle shifted off to x=[70,80] → perp-disjoint from px=50 → filtered, not blocking
    free = carve_free_position(
        _Dwg([("o", _obj(70, 108, 80, 118))]), strip, "plan", "y", 10.0, (50.0, 50.0)
    )
    assert free == 108.0, "perp-disjoint obstacle must not block a zero-width-band leader"
    assert blocked != 108.0, (
        "obstacle straddling the leader's x-line must push it off the inner tier"
    )


def test_plan_strip_empty():
    res = plan_strip([], 0, 100, 5)
    assert res.placed == {} and res.dropped == ()


def test_plan_strip_rejects_duplicate_keys():
    # keys key the result — a silent overwrite would drop a candidate (cf. the
    # since-retired LayoutSolver.register, #547, which also raised).
    import pytest

    with pytest.raises(ValueError, match="unique"):
        plan_strip([_cand("a", 10), _cand("a", 20)], lo=0, hi=100, min_gap=5)


# --- fake-dwg strip-occupancy queries (#321) ---


def _fake_dwg(obstacles, view="side", types=None):
    # Minimal dwg for strip_obstacles/corridor_blockers: named
    # annotations exposing a bounding_box() and a single owning view. obstacles:
    # {name: (x0, y0, x1, y1)}; *types* optionally maps a name to its annotation
    # class name (default "_Obst") so corridor_blockers' type filter can be exercised.
    # "Dimension"/"SafeDimension" build a real *subclass* (so the `isinstance` filter,
    # not a name string-match, is exercised — #335/#349 hardening); other names build a
    # plain class of that name (for the crossable/leader name checks).
    from build123d_drafting.helpers import Dimension, SafeDimension

    _dim_bases = {"Dimension": (Dimension,), "SafeDimension": (SafeDimension,)}

    class _P:
        def __init__(s, x, y):
            s.X, s.Y = x, y

    class _BB:
        def __init__(s, x0, y0, x1, y1):
            s.min, s.max = _P(x0, y0), _P(x1, y1)

    def _make(name, bb):
        tn = (types or {}).get(name, "_Obst")
        bases = _dim_bases.get(tn, ())
        body = {"bounding_box": lambda s, _b=_BB(*bb): _b, "__init__": lambda s: None}
        return type(tn, bases, body)()

    class _Dwg:
        def iter_annotations(s):
            return [(n, _make(n, b)) for n, b in obstacles.items()]

        def view_of(s, n):
            return view

    return _Dwg()


def test_corridor_blockers_keeps_leaders_excludes_dim_chains_and_centrelines():
    # A dimension's witness corridor may cross datum-chained dims and centre lines but
    # NOT a leader (#321 P1b). corridor_blockers returns only the blocking geometry.
    # A SafeDimension (helper sibling of Dimension, NOT a subclass) must be excluded
    # exactly like a Dimension — the #335/#349 isinstance-not-string-name hardening; a
    # bare name match would have wrongly kept it as a blocker.
    from draftwright.annotations._common import corridor_blockers

    dwg = _fake_dwg(
        {
            "dim_loc_side_y100": (0, 0, 5, 3),
            "m_env_depth_safe": (1, 1, 6, 4),
            "m_cl": (0, 0, 5, 3),
            "hc_side0": (10, 10, 20, 15),
        },
        types={
            "dim_loc_side_y100": "Dimension",
            "m_env_depth_safe": "SafeDimension",
            "m_cl": "Centerline",
            "hc_side0": "Leader",
        },
    )
    assert corridor_blockers(dwg, "side") == [(10.0, 10.0, 20.0, 15.0)]


def test_side_hole_z_dim_is_kept_not_dropped_under_policy_b():
    # side_drilled's bore-callout leader crosses the Z-location corridor and the front
    # alternate is too narrow to relocate into — policy B KEEPS the dim on its natural
    # view (never drop a real dimension) rather than clearing the overlap by dropping.
    from _layout_sig import CORPUS

    from draftwright import build_drawing

    dwg = build_drawing(CORPUS["side_drilled"]())
    names = {n for n, _ in dwg.iter_annotations()}
    assert any(n.startswith("dim_loc_") and "_z" in n for n in names), "Z location dim was dropped"


# --- unified above-corridor solve (ADR 0009 end state, #345/#346) -----------


def _holed_slot():
    # A hole whose X-location coincides with the slot's near edge (both measure datum→"20").
    from build123d import Box, BuildPart, Hole, Locations, Mode

    with BuildPart() as p:
        Box(60, 40, 20)
        Box(20, 8, 30, mode=Mode.SUBTRACT)  # slot: long_axis X, near edge x=-10
        with Locations((-10, 14, 0), (20, 14, 0), (8, -14, 0)):
            Hole(3, depth=20)
    return p.part


def _holed_slot_frac():
    # As _holed_slot but the coincident datum→edge span is a FRACTIONAL 20.15, not a round
    # 20.0. Pre-fix the slot-position dedup key snapped its endpoint to the displayed value
    # (20.2) while the hole-location key used the raw 20.15, so the ~0.05 mm gap crossed a
    # 0.1 mm page bin and the #345 duplicate escaped dedup. The raw-basis key closes it.
    from build123d import Box, BuildPart, Hole, Locations, Mode

    with BuildPart() as p:
        Box(60, 40, 20)  # bbox min X = -30
        with Locations((0.15, 0, 0)):
            Box(20, 8, 30, mode=Mode.SUBTRACT)  # slot near edge x=-9.85 → datum→edge = 20.15
        with Locations((-9.85, 14, 0), (20, 14, 0), (8, -14, 0)):
            Hole(3, depth=20)
    return p.part


def _plan_above_ladder(dwg):
    """(name, dim) for every plan-view dimension with a horizontal witness — the plan-above
    corridor the location + slot passes share."""
    out = []
    for name in dwg.annotations():
        o = dwg.get_annotation(name)
        spec = getattr(o, "_dw_spec", None)
        if spec is None or dwg.view_of(name) != "plan":
            continue
        if abs(spec.p1[1] - spec.p2[1]) > 1e-6:
            continue  # vertical witness → a right/left dim, not the above ladder
        out.append((name, o))
    return out


def test_corridor_dedups_coincident_hole_and_slot_span():
    # #345: a hole location and a slot position measuring the same datum span collapse to
    # ONE dim. Before the unified corridor solve, m_locx0 and m_slot0_pos both drew "20"
    # over datum→x=-10, a visible duplicate.
    dwg = build_drawing(_holed_slot())
    ladder = _plan_above_ladder(dwg)
    names = {n for n, _ in ladder}
    assert "m_slot0_pos" not in names, "coincident slot position was not deduped away (#345)"
    # No two datum-referenced dims share a measured span (the datum is the leftmost origin).
    datum_x = min(min(o._dw_spec.p1[0], o._dw_spec.p2[0]) for _, o in ladder)
    spans = [
        (
            round(min(o._dw_spec.p1[0], o._dw_spec.p2[0]), 1),
            round(max(o._dw_spec.p1[0], o._dw_spec.p2[0]), 1),
        )
        for _, o in ladder
        if abs(min(o._dw_spec.p1[0], o._dw_spec.p2[0]) - datum_x) < 0.5
    ]
    assert len(spans) == len(set(spans)), f"duplicate datum span in the plan-above ladder: {spans}"


def test_corridor_dedups_coincident_span_at_fractional_distance():
    # #345 follow-up: dedup must be robust to the snap gap. A coincident hole+slot-edge at a
    # FRACTIONAL 20.15 leaked the duplicate when the two dedup keys used different bases
    # (raw ref vs snapped endpoint). Guards the raw-basis key against regression.
    dwg = build_drawing(_holed_slot_frac())
    ladder = _plan_above_ladder(dwg)
    names = {n for n, _ in ladder}
    assert "m_slot0_pos" not in names, "fractional coincident slot position not deduped (#345)"


def test_corridor_orders_location_ladder_monotonically():
    # #346: hole-location dims sharing the datum origin nest in span order — their dim lines
    # stack outward monotonically as the measured value grows, not interleaved.
    dwg = build_drawing(_holed_slot())
    rungs = []
    for name in dwg.annotations():
        if not name.startswith("m_locx"):
            continue
        o = dwg.get_annotation(name)
        span = abs(o._dw_spec.p2[0] - o._dw_spec.p1[0])
        rungs.append((span, o.bounding_box().max.Y))  # tier proxy: the dim line's page Y
    assert len(rungs) >= 3, f"need >=3 location rungs to test ordering, got {len(rungs)}"
    tiers = [t for _, t in sorted(rungs)]  # ordered by span
    assert tiers == sorted(tiers) or tiers == sorted(tiers, reverse=True), (
        f"location ladder not monotonic by span (interleaved, #346): {sorted(rungs)}"
    )


def _pitch_dim_over_centerline(centerline_factory, centerline_name):
    # Shared #129 repro: a plate whose 2-hole pitch dim naturally centres its label at
    # plan_x(0) — then a centre-line-family annotation is placed exactly there (computed
    # live via a.proj.plan_x(0), not a hardcoded page coordinate, so this stays correct if
    # unrelated layout/margin/scale logic ever shifts it), in the strip the dim will land
    # in, before `_place_pitch_dim` runs (mirrors real render order: centre marks/circles
    # are placed before pitch dims). Returns the placed dim and the (now-cleared) lint
    # issues against that one centerline.
    from build123d import Box, Cylinder, Pos

    from draftwright.annotations.holes import _place_pitch_dim, build_view_of_axis
    from draftwright.linting.structural import _lint_centerline_dim_overlap

    part = Box(100, 50, 10)
    for x in (-30, 30):
        part = part - Pos(x, 0, 0) * Cylinder(3, 10)
    dwg = build_drawing(part)
    a = dwg._analysis
    view, to_page = build_view_of_axis(a)["z"]
    part_cx = a.proj.plan_x(0)
    dwg.add(centerline_factory(part_cx), centerline_name, view="plan")
    _place_pitch_dim(dwg, a, "plan", (-30, 0, 0), (30, 0, 0), 2, 60, to_page, "test_pitch")
    dim = dwg.get_annotation("test_pitch")
    cl = dwg.get_annotation(centerline_name)
    issues = []
    _lint_centerline_dim_overlap(dim, cl, issues)
    return dim, issues


def test_pitch_dim_label_clears_a_thin_vertical_centerline():
    # #129: a turned part's axis Centerline (drawn full-height through the view, e.g.
    # `render_rotational`'s centerline_front/side) sits right where a symmetric 2-hole
    # pitch dim's label would naturally centre. `_place_pitch_dim` must offset the label
    # off it at creation time, not rely on the lint→repair loop.
    from build123d_drafting import Centerline

    dim, issues = _pitch_dim_over_centerline(
        lambda cx: Centerline((cx, 150, 0), (cx, 300, 0)), "test_centerline"
    )
    assert dim._dw_spec.kwargs.get("label_offset_x", 0.0) != 0.0, "label was not shifted"
    assert issues == [], f"label still overlaps the centerline: {[i.message for i in issues]}"


def test_pitch_dim_label_clears_a_bolt_circle_centerline():
    # #129 (broader scope): a bolt-circle pattern's CenterlineCircle is wide, not a thin
    # line — `_compute_label_offset_x`-style midpoint logic alone doesn't cover it. The
    # same creation-time clearing must also push the label past the circle's nearer edge.
    from build123d_drafting import CenterlineCircle

    dim, issues = _pitch_dim_over_centerline(
        lambda cx: CenterlineCircle((cx, 222.5), 30), "test_circle"
    )
    assert dim._dw_spec.kwargs.get("label_offset_x", 0.0) != 0.0, "label was not shifted"
    assert issues == [], f"label still overlaps the bolt circle: {[i.message for i in issues]}"


class _FakeCenterline:
    # A minimal is_centerline-family stand-in for direct clear_label_of_centerlines()
    # unit tests below — controls the extent exactly (no build123d geometry needed),
    # matching the codebase's existing formula-level test pattern (e.g.
    # test_carve_free_segments_*).
    def __init__(self, x0, y0, x1, y1):
        self.is_centerline = True
        self.segments = [((x0, y0), (x1, y1))]


def test_clear_label_of_centerlines_picks_the_nearer_side_not_just_any_clearing_side():
    # #129 review: a purely-symmetric repro (centreline dead-centre on the label) can't
    # tell a correct minimal-distance shift from a same-magnitude wrong-direction one — both
    # clear equally well. Use an OFF-CENTRE thin line so the two directions have different
    # magnitudes, and assert the exact expected minimal shift (not just "some shift").
    import pytest

    from draftwright.annotations._common import clear_label_of_centerlines

    # label x in [0,20] (half-width 10, centred at 10); thin vertical line at x=5, gap=2.
    # shift_right = 5+10+2-10 = 7 (clears past the line's right side); shift_left = -17
    # (clears past its left side) — right is nearer, so 7.0 is the only correct answer.
    got = clear_label_of_centerlines((0, 0, 20, 10), [_FakeCenterline(5, -100, 5, 100)], gap=2)
    assert got == pytest.approx(7.0)


def test_clear_label_of_centerlines_picks_the_nearer_edge_of_a_wide_bbox():
    # Same direction-sensitivity check for the CenterlineCircle/wide-bbox branch.
    import pytest

    from draftwright.annotations._common import clear_label_of_centerlines

    # label x in [0,20]; wide bbox spans x in [8,40] (a bolt circle straddling the label's
    # right portion). Clearing past its left edge (8) is much nearer than past its right
    # edge (40), so the only correct answer is the left-edge shift.
    got = clear_label_of_centerlines((0, 0, 20, 10), [_FakeCenterline(8, -50, 40, 50)], gap=2)
    assert got == pytest.approx(-14.0)


def test_clear_label_of_centerlines_requires_real_y_overlap():
    # #129 review: the thin-line branch used to shift on X-containment alone, without
    # checking the label's Y-range actually overlaps the line's Y-extent — unlike the
    # lint check it mirrors. A centreline nowhere near the label vertically must not
    # trigger any shift.
    from draftwright.annotations._common import clear_label_of_centerlines

    got = clear_label_of_centerlines(
        (10, 10, 30, 20), [_FakeCenterline(15, 1000, 15, 1100)], gap=1
    )
    assert got == 0.0


def test_clear_label_of_centerlines_requires_real_depth_not_just_containment():
    # #129 second review: the thin-line branch shifted on bare X-containment, with no
    # minimum-depth requirement — unlike the wide-bbox branch (ox<=0.5: continue) and the
    # lint check both branches are meant to mirror. A marginal (<=0.5mm) containment must
    # not trigger a shift — especially since a shift here can land the label ON a SECOND,
    # previously-clear centreline, creating a worse violation than doing nothing.
    from draftwright.annotations._common import clear_label_of_centerlines

    # cl1 is only 0.2mm inside the label (not a real lint violation); cl2 doesn't overlap
    # the label at all originally. Before the fix this returned 1.2 (shifting onto cl2).
    lbb = (-0.2, 0, 3.8, 10)
    cl1 = _FakeCenterline(0, -100, 0, 100)
    cl2 = _FakeCenterline(4.0, -100, 4.0, 100)
    got = clear_label_of_centerlines(lbb, [cl1, cl2], gap=1)
    assert got == 0.0


def test_clear_label_of_centerlines_merges_adjacent_forbidden_intervals():
    # #129 fourth review: the joint carve routes through carve_free_segments, which only
    # merges forbidden intervals that actually touch/overlap — two close-together
    # centerlines (5 and 8) produce overlapping forbidden zones that must merge into ONE
    # block, while a third, distant one (50) stays separate and must not disturb the
    # result. Exercises the interval-merge path a 2-centerline case can't distinguish from
    # a correct implementation (superseded the old "two well separated centerlines" case,
    # which no longer stresses anything a harder test doesn't already cover).
    from draftwright.annotations._common import clear_label_of_centerlines

    cl1 = _FakeCenterline(5, -100, 5, 100)
    cl2 = _FakeCenterline(8, -100, 8, 100)
    cl3 = _FakeCenterline(50, -100, 50, 100)
    total = clear_label_of_centerlines((0, 0, 10, 10), [cl1, cl2, cl3], gap=1)
    lo, hi = total, 10 + total
    assert not (lo < 5 < hi), "still overlaps the first (merged-block) centerline"
    assert not (lo < 8 < hi), "still overlaps the second (merged-block) centerline"
    assert not (lo < 50 < hi), "still overlaps the distant, separate centerline"


def test_clear_label_of_centerlines_folds_in_a_centerline_that_only_grazed_the_natural_position():
    # #129 fourth review: the docstring's core claim about why every reachable centerline
    # is carved (not just the ones individually past the 0.5mm natural-position threshold)
    # is that a centerline with ZERO overlap at the natural position can still end up
    # inside the position a shift lands on. Proven here: with only cl_v, the nearest clear
    # position is -6.0 (verified below); placing cl_graze exactly where that landing spot
    # would be (and nowhere near the natural [0,10] position, so it contributes nothing to
    # the natural-violation check) must change the outcome to a position clearing both.
    from draftwright.annotations._common import clear_label_of_centerlines

    cl_v = _FakeCenterline(5, -100, 5, 100)
    solo = clear_label_of_centerlines((0, 0, 10, 10), [cl_v], gap=1)
    assert solo == -6.0, f"solo-centerline baseline drifted (was -6.0), got {solo}"

    cl_graze = _FakeCenterline(-3, -100, -3, 100)  # inside [-6, 4], outside natural [0, 10]
    both = clear_label_of_centerlines((0, 0, 10, 10), [cl_v, cl_graze], gap=1)
    assert both != solo, "the graze centerline was not folded into the forbidden set"
    lo, hi = both, 10 + both
    assert not (lo < 5 < hi), "still overlaps cl_v"
    assert not (lo < -3 < hi), "still overlaps the folded-in graze centerline"


def test_clear_label_of_centerlines_recovers_from_a_cascading_re_violation():
    # #129 second review: the "well separated" case above never actually exercises the
    # re-crossing bug a naive per-centreline local search has, since the two centrelines
    # there never interact. This case genuinely distinguishes them: clearing cl1(x=0)
    # alone lands the label on cl2(x=6). A single forward pass that clears cl2 and stops
    # would leave cl1 re-violated; the joint carve (#129 third review — every reachable
    # centreline is carved out in one pass, not walked one at a time) clears both.
    from draftwright.annotations._common import clear_label_of_centerlines

    cl1 = _FakeCenterline(0, -100, 0, 100)
    cl2 = _FakeCenterline(6, -100, 6, 100)
    total = clear_label_of_centerlines((0, 0, 10, 10), [cl1, cl2], gap=1)
    lo, hi = total, 10 + total
    assert not (lo < 0 < hi), "still overlaps the first centerline (the old-algorithm bug)"
    assert not (lo < 6 < hi), "still overlaps the second centerline"


def test_clear_label_of_centerlines_solves_a_tight_squeeze():
    # #129 third review: a per-centreline local search (nearest-edge, one at a time) could
    # defeat itself when two centrelines sit closer together than the label needs to clear
    # both — label width 10 vs a 9mm gap between the centrelines here is exactly that case,
    # with no position that clears both by hugging either one individually. The joint carve
    # (clear_label_of_centerlines routes through carve_free_segments, exactly like the
    # dimension LINE's own placement elsewhere in this module) finds the "go around both"
    # position that does exist further out — this is now solved, not just bounded/safe.
    from draftwright.annotations._common import clear_label_of_centerlines

    cl1 = _FakeCenterline(5, -100, 5, 100)
    cl2 = _FakeCenterline(14, -100, 14, 100)
    total = clear_label_of_centerlines((0, 0, 10, 10), [cl1, cl2], gap=1)
    lo, hi = total, 10 + total
    assert not (lo < 5 < hi), "still overlaps the first centerline"
    assert not (lo < 14 < hi), "still overlaps the second centerline"


def test_box_within_page_and_clear_rejects_a_shift_that_hits_an_obstacle():
    # #129 second review: holes.py's _clear_and_validate falls back to the unshifted dim
    # when a shift would leave the page or hit a real obstacle, but that closure is not
    # independently callable — nothing exercised the rejection branch. The check itself
    # is a small pure predicate (box_within_page_and_clear), factored out for exactly this.
    from draftwright.annotations._common import box_within_page_and_clear

    page_box = (0, 0, 100, 100)
    obstacles = [(40, 40, 60, 60)]
    assert box_within_page_and_clear((10, 10, 20, 20), page_box, obstacles), "clear box rejected"
    assert not box_within_page_and_clear((45, 45, 55, 55), page_box, obstacles), (
        "obstacle-hitting box accepted"
    )
    assert not box_within_page_and_clear((-5, 10, 5, 20), page_box, obstacles), (
        "off-page box accepted"
    )


def test_leader_decoration_stages_follow_the_drain():
    # #733: best-effort machined-feature leader callouts place AFTER the corridor
    # drain, so a principal dim that registers early but places at the drain can
    # never have its strip stolen by an immediate callout (the CTC-02/04 regression:
    # #689 moved the ladder to register-then-drain and pocket/fillet callouts filled
    # its strip first). Ordering IS the priority encoding for the immediate placers,
    # and _PASS_SEQUENCE is its single source of truth — pin it.
    from draftwright.annotations.orchestrator import _PASS_SEQUENCE

    drain = _PASS_SEQUENCE.index("drain")
    section = _PASS_SEQUENCE.index("section")
    for stage in ("chamfers", "fillets", "flats", "pockets", "grooves"):
        i = _PASS_SEQUENCE.index(stage)
        # After the drain (principals exist first) but BEFORE the section/details/
        # title block, which must see the callouts as obstacles (Codex review).
        assert drain < i < section, (
            f"{stage!r} must place after the drain and before the section — decoration "
            "never starves a principal dim, and later views must see it (#733)"
        )
    assert section < _PASS_SEQUENCE.index("details") < _PASS_SEQUENCE.index("title_block")
