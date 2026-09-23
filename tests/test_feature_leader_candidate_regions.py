import math
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos
from build123d_drafting.helpers import Leader, draft_preset

from draftwright import Sheet
from draftwright.annotations import from_model, leaders
from draftwright.annotations._common import PlacementContext, SolveTrace, leader_callout_geometry
from draftwright.annotations.leaders import (
    FeatureLeaderCandidate,
    FeatureLeaderJob,
    LeaderCandidateRegion,
    LeaderRegionPolicy,
    RadialLeaderTarget,
    _fixed_blockers,
    _FixedInkComponent,
    _measure,
    _MeasuredLeaderCandidate,
    _ray_exit_distance,
    _view_region_blocker,
    feature_leader_candidates,
    interior_leader_candidates,
    place_feature_leader_jobs,
)
from draftwright.annotations.routed import RoutedLeader
from draftwright.model import hole


def _job(*, clearance=None):
    return FeatureLeaderJob(
        name="radius",
        view="plan",
        silhouette=(0.0, 0.0, 100.0, 60.0),
        label="R10",
        candidates=(),
        build=lambda *_args: None,
        analytical_geometry=lambda tip, elbow, _feature: (
            (40.0, 20.0, 50.0, 24.0),
            (((tip[0], tip[1]), (elbow[0], elbow[1])),),
        ),
        measurement=(),
        noun="blend",
        drop_code="blend_dropped",
        interior_label_clear=clearance,
    )


def _candidate(*, region, label=(40.0, 20.0, 50.0, 24.0)):
    return _MeasuredLeaderCandidate(
        annotation=SimpleNamespace(),
        tip=(35.0, 20.0),
        elbow=(40.0, 22.0),
        feature=None,
        raw_index=0,
        cost=1.0,
        label_box=label,
        segments=(),
        ink_polygons=(),
        region=region,
    )


def test_legacy_raw_candidate_retains_exterior_provenance():
    draft = SimpleNamespace(arrow_length=1.0, line_width=0.1)

    legacy = _measure(0, ((35.0, 20.0), (40.0, 22.0), None), _job(), draft)
    interior = _measure(
        1,
        FeatureLeaderCandidate(
            (35.0, 20.0),
            (40.0, 22.0),
            None,
            LeaderCandidateRegion.INTERIOR,
        ),
        _job(clearance=lambda _box: True),
        draft,
    )

    assert legacy.region is LeaderCandidateRegion.EXTERIOR
    assert interior.region is LeaderCandidateRegion.INTERIOR


def test_malformed_candidate_is_retained_as_failed_exterior_inventory():
    draft = SimpleNamespace(arrow_length=1.0, line_width=0.1)

    candidate = _measure(0, (("bad", 20.0), (40.0, object()), None), _job(), draft)

    assert candidate.failure_reason == "geometry_validation"
    assert candidate.region is LeaderCandidateRegion.EXTERIOR


def test_interior_candidate_requires_complete_projected_clearance_proof():
    page = (-10.0, -10.0, 110.0, 70.0)
    interior = _candidate(region=LeaderCandidateRegion.INTERIOR)

    assert _fixed_blockers(interior, _job(), page, ()) == ("view:plan:interior_projection_ink",)
    assert _fixed_blockers(interior, _job(clearance=lambda _box: False), page, ()) == (
        "view:plan:interior_projection_ink",
    )
    assert _fixed_blockers(interior, _job(clearance=lambda _box: True), page, ()) == ()

    outside = _candidate(
        region=LeaderCandidateRegion.INTERIOR,
        label=(95.0, 20.0, 105.0, 24.0),
    )
    assert _fixed_blockers(outside, _job(clearance=lambda _box: True), page, ()) == (
        "view:plan:interior_bounds",
    )


def test_interior_candidate_cannot_relax_fixed_annotation_ink():
    candidate = _candidate(region=LeaderCandidateRegion.INTERIOR)
    blockers = _fixed_blockers(
        candidate,
        _job(clearance=lambda _box: True),
        (-10.0, -10.0, 110.0, 70.0),
        (_FixedInkComponent("dimension:segment", box=(42.0, 18.0, 43.0, 26.0)),),
    )

    assert blockers == ("dimension:segment", "view:plan:interior_annotation_ink")


def test_exterior_candidate_retains_established_view_policy():
    page = (-10.0, -10.0, 120.0, 70.0)

    assert _fixed_blockers(
        _candidate(region=LeaderCandidateRegion.EXTERIOR),
        _job(clearance=lambda _box: True),
        page,
        (),
    ) == ("view:plan:silhouette",)
    assert (
        _fixed_blockers(
            _candidate(
                region=LeaderCandidateRegion.EXTERIOR,
                label=(105.0, 20.0, 115.0, 24.0),
            ),
            _job(clearance=lambda _box: True),
            page,
            (),
        )
        == ()
    )


def test_unmeasurable_candidate_has_no_view_region_blocker():
    assert (
        _view_region_blocker(
            _candidate(region=LeaderCandidateRegion.INTERIOR, label=None),
            _job(clearance=lambda _box: True),
        )
        is None
    )


def test_resource_floor_rejects_unverified_interior_and_view_blocked_exterior(
    monkeypatch, fresh_drawing
):
    drawing = fresh_drawing("box_40x30x8", page="A4", auto_dims=False)
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    tip = (bounds[2], (bounds[1] + bounds[3]) / 2.0)
    inside = (tip[0] - 10.0, tip[1], 0.0)
    outside = (tip[0] + 20.0, tip[1], 0.0)
    owner = object()
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
    )

    def build(candidate_tip, elbow, _feature):
        return Leader(
            tip=(*candidate_tip, 0.0),
            elbow=elbow,
            label="R1",
            text_side="right" if elbow[0] >= candidate_tip[0] else "left",
            draft=drawing.draft,
        )

    monkeypatch.setattr(leaders, "_FEATURE_LEADER_MAX_FIXED_WORK", 0)
    job = FeatureLeaderJob(
        name="m_fillet0",
        view="front",
        silhouette=bounds,
        label="R1",
        candidates=(),
        fallback_candidates=(
            FeatureLeaderCandidate(
                tip,
                inside,
                owner,
                LeaderCandidateRegion.INTERIOR,
            ),
            FeatureLeaderCandidate(
                tip,
                inside,
                owner,
                LeaderCandidateRegion.EXTERIOR,
            ),
            FeatureLeaderCandidate(
                tip,
                outside,
                owner,
                LeaderCandidateRegion.EXTERIOR,
            ),
        ),
        build=build,
        measurement=(),
        noun="fillet",
        drop_code="fillet_dropped",
        interior_label_clear=lambda _box: True,
        fallback_accept=lambda _candidate, _obstacles, _page: True,
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=drawing.get_annotation("title_block").bounding_box().size.X,
    )

    assert (
        place_feature_leader_jobs(
            drawing,
            analysis,
            ctx,
            (job,),
            producer_floor=True,
        )
        == 1
    )
    assert drawing.get_annotation("m_fillet0").segments[0][1] == outside[:2]


def test_joint_sheet_recovery_runs_after_winners_and_updates_trace(fresh_drawing, tmp_path):
    drawing = fresh_drawing("box_40x30x8", page="A4", auto_dims=False)
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    tip = (bounds[2], (bounds[1] + bounds[3]) / 2.0)
    outside = (tip[0] + 20.0, tip[1], 0.0)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        trace=SolveTrace(tmp_path / "trace.json"),
    )

    def build(candidate_tip, elbow, _feature):
        return Leader((*candidate_tip, 0.0), elbow, "R1", drawing.draft)

    def recover():
        assert "ordinary" in drawing.annotations()
        return (
            Leader(
                (*tip, 0.0),
                (outside[0], outside[1] + 12.0),
                "HEX 100 A/F",
                drawing.draft,
            ),
            None,
        )

    ordinary = FeatureLeaderJob(
        name="ordinary",
        view="front",
        silhouette=bounds,
        label="R1",
        candidates=((tip, outside, None),),
        build=build,
        measurement=(),
        noun="fillet",
        drop_code="fillet_dropped",
        fallback_accept=lambda *_args: True,
    )
    recovered = FeatureLeaderJob(
        name="recovered",
        view="front",
        silhouette=bounds,
        label="HEX 100 A/F",
        candidates=(),
        build=build,
        measurement=(),
        noun="polygonal boss",
        drop_code="polygonal_boss_dropped",
        recover=recover,
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=drawing.get_annotation("title_block").bounding_box().size.X,
    )

    assert place_feature_leader_jobs(drawing, analysis, ctx, (ordinary, recovered)) == 2
    assert {"ordinary", "recovered"} <= set(drawing.annotations())
    assert not [
        issue for issue in drawing.registry.issues if issue.code == "polygonal_boss_dropped"
    ]
    event = next(
        row for row in ctx.trace.pass_events if row["label"] == "feature_leader_inventory"
    )
    placed = [row for row in event["items"] if row["outcome"] == "placed"]
    assert event["objective"]["placed"] == len(placed) == 2
    assert event["objective"]["cost"] == pytest.approx(sum(row["cost"] for row in placed))
    recovered_item = next(row for row in placed if row["name"] == "recovered")
    assert recovered_item["recovery"] == "sheet_recovery"
    assert recovered_item["route"] == "straight"


def test_typed_radial_recovery_preserves_a_normal_first_segment(monkeypatch, fresh_drawing):
    drawing = fresh_drawing("box_40x30x8", page="A4", auto_dims=False)
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    centre = ((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
    tip = (centre[0] + 5.0, centre[1])
    elbow = (tip[0] + 20.0, tip[1], 0.0)
    candidate = FeatureLeaderCandidate(
        tip,
        elbow,
        None,
        radial_target=RadialLeaderTarget(centre, 5.0),
    )
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
    )

    def forced_fallback(_drawing, search_tip, _view, build_at, _build_routed, _size):
        return build_at((search_tip[0] + 12.0, search_tip[1] + 8.0))

    monkeypatch.setattr(from_model, "_sheet_leader_fallback", forced_fallback)
    from_model.place_machined_leader_jobs(
        drawing,
        SimpleNamespace(leader_region="auto"),
        (("radial", "front", bounds, "R5", (candidate,), ()),),
        noun="fillet",
        drop_code="fillet_dropped",
        ctx=ctx,
        joint=True,
    )

    assert len(ctx.feature_leaders) == 1
    annotation, _feature = ctx.feature_leaders[0].recover()
    assert isinstance(annotation, RoutedLeader)
    first_bend = annotation.bends[0]
    assert first_bend[1] == pytest.approx(tip[1])
    assert first_bend[0] > tip[0]


def test_interior_candidates_are_feature_relative_and_fully_inside_view():
    draft = draft_preset(font_size=3.0, decimal_precision=1)
    silhouette = (0.0, 0.0, 100.0, 60.0)

    def geometry(tip, elbow, _feature):
        return leader_callout_geometry(
            tip,
            elbow,
            draft,
            callout_box=(0.0, 0.0, 12.0, 3.0),
        )

    candidates = tuple(
        interior_leader_candidates(
            (0.0, 30.0),
            (-20.0, 30.0),
            "feature",
            silhouette=silhouette,
            analytical_geometry=geometry,
            draft=draft,
        )
    )

    assert candidates
    assert all(candidate.region is LeaderCandidateRegion.INTERIOR for candidate in candidates)
    for candidate in candidates:
        label, _segments = geometry(candidate.tip, candidate.elbow, candidate.feature)
        assert label is not None
        assert silhouette[0] <= label[0] < label[2] <= silhouette[2]
        assert silhouette[1] <= label[1] < label[3] <= silhouette[3]


def test_interior_candidate_inventory_is_bounded_independent_of_view_size():
    draft = draft_preset(font_size=3.0, decimal_precision=1)

    def geometry(_tip, elbow, _feature):
        x, y = elbow[:2]
        return ((x - 1.0, y - 1.0, x + 1.0, y + 1.0), ())

    candidates = tuple(
        interior_leader_candidates(
            (0.0, 0.0),
            (1.0, 0.0),
            "feature",
            silhouette=(-1_000_000.0, -1_000_000.0, 1_000_000.0, 1_000_000.0),
            analytical_geometry=geometry,
            draft=draft,
        )
    )

    assert len(candidates) == 64


def test_radial_target_moves_each_rotated_tip_to_meet_the_circle_head_on():
    draft = draft_preset(font_size=3.0, decimal_precision=1)
    target = RadialLeaderTarget(center=(0.0, 0.0), radius=10.0)

    def geometry(_tip, elbow, _feature):
        x, y = elbow[:2]
        return ((x - 1.0, y - 1.0, x + 1.0, y + 1.0), ())

    candidates = tuple(
        interior_leader_candidates(
            (10.0, 0.0),
            (20.0, 0.0),
            "circular feature",
            silhouette=(-100.0, -100.0, 100.0, 100.0),
            analytical_geometry=geometry,
            draft=draft,
            radial_target=target,
        )
    )

    assert len(candidates) == 64
    assert (
        len({tuple(round(value, 6) for value in candidate.tip) for candidate in candidates}) == 8
    )
    for candidate in candidates:
        radial = (candidate.tip[0] - target.center[0], candidate.tip[1] - target.center[1])
        shaft = (
            candidate.elbow[0] - candidate.tip[0],
            candidate.elbow[1] - candidate.tip[1],
        )
        assert math.hypot(*radial) == pytest.approx(target.radius)
        assert radial[0] * shaft[1] - radial[1] * shaft[0] == pytest.approx(0.0, abs=1e-9)
        assert radial[0] * shaft[0] + radial[1] * shaft[1] > 0.0


def test_radial_target_rejects_an_unproved_oblique_source_anchor():
    draft = draft_preset(font_size=3.0, decimal_precision=1)
    target = RadialLeaderTarget(center=(0.0, 0.0), radius=10.0)

    assert (
        tuple(
            interior_leader_candidates(
                (10.0, 0.0),
                (20.0, 5.0),
                "circular feature",
                silhouette=(-100.0, -100.0, 100.0, 100.0),
                analytical_geometry=lambda *_args: ((0.0, 0.0, 1.0, 1.0), ()),
                draft=draft,
                radial_target=target,
            )
        )
        == ()
    )


def test_interior_producer_fails_closed_for_unusable_anchors_and_geometry():
    draft = draft_preset(font_size=3.0, decimal_precision=1)
    bounds = (0.0, 0.0, 100.0, 60.0)

    assert _ray_exit_distance((-1.0, 30.0), (1.0, 0.0), bounds) == 0.0
    assert (
        tuple(
            interior_leader_candidates(
                (10.0, 10.0),
                (10.0, 10.0),
                "feature",
                silhouette=bounds,
                analytical_geometry=lambda *_args: None,
                draft=draft,
            )
        )
        == ()
    )

    def broken_geometry(*_args):
        raise ValueError("unreadable optional candidate")

    assert (
        tuple(
            interior_leader_candidates(
                (10.0, 10.0),
                (20.0, 10.0),
                "feature",
                silhouette=bounds,
                analytical_geometry=broken_geometry,
                draft=draft,
            )
        )
        == ()
    )


def test_region_policy_is_applied_by_one_shared_candidate_adapter():
    draft = draft_preset(font_size=3.0, decimal_precision=1)

    def geometry(_tip, elbow, _feature):
        x, y = elbow[:2]
        return ((x - 1.0, y - 1.0, x + 1.0, y + 1.0), ())

    kwargs = {
        "silhouette": (-100.0, -100.0, 100.0, 100.0),
        "analytical_geometry": geometry,
        "draft": draft,
    }
    raw = (((0.0, 0.0), (10.0, 0.0, 0.0), "feature"),)

    automatic = tuple(
        feature_leader_candidates(raw, region_policy=LeaderRegionPolicy.AUTO, **kwargs)
    )
    interior = tuple(
        feature_leader_candidates(raw, region_policy=LeaderRegionPolicy.INTERIOR, **kwargs)
    )
    exterior = tuple(
        feature_leader_candidates(raw, region_policy=LeaderRegionPolicy.EXTERIOR, **kwargs)
    )

    assert {candidate.region for candidate in automatic} == {
        LeaderCandidateRegion.INTERIOR,
        LeaderCandidateRegion.EXTERIOR,
    }
    assert interior and all(
        candidate.region is LeaderCandidateRegion.INTERIOR for candidate in interior
    )
    assert len(exterior) == 1
    assert exterior[0].region is LeaderCandidateRegion.EXTERIOR

    pretyped = FeatureLeaderCandidate(
        tip=(0.0, 0.0),
        elbow=(10.0, 0.0, 0.0),
        feature="feature",
        region=LeaderCandidateRegion.INTERIOR,
    )
    assert tuple(
        feature_leader_candidates((pretyped,), region_policy=LeaderRegionPolicy.AUTO, **kwargs)
    ) == (pretyped,)
    assert (
        tuple(
            feature_leader_candidates(
                (pretyped,), region_policy=LeaderRegionPolicy.EXTERIOR, **kwargs
            )
        )
        == ()
    )


def test_multi_anchor_feature_shares_one_bounded_interior_inventory():
    draft = draft_preset(font_size=3.0, decimal_precision=1)

    def geometry(_tip, elbow, _feature):
        x, y = elbow[:2]
        return ((x - 1.0, y - 1.0, x + 1.0, y + 1.0), ())

    anchors = (
        ((0.0, 0.0), (1.0, 0.0), "first"),
        ((50.0, 50.0), (51.0, 50.0), "second"),
    )
    candidates = tuple(
        feature_leader_candidates(
            anchors,
            region_policy=LeaderRegionPolicy.AUTO,
            silhouette=(-1_000.0, -1_000.0, 1_000.0, 1_000.0),
            analytical_geometry=geometry,
            draft=draft,
            exterior_candidates=(anchors[-1],),
        )
    )

    assert (
        sum(candidate.region is LeaderCandidateRegion.INTERIOR for candidate in candidates) == 64
    )
    assert [
        candidate.feature
        for candidate in candidates
        if candidate.region is LeaderCandidateRegion.EXTERIOR
    ] == ["second"]


def test_late_machined_jobs_offer_interior_then_exterior_candidates(monkeypatch):
    draft = draft_preset(font_size=3.0, decimal_precision=1)
    drawing = SimpleNamespace(draft=draft)
    context = SimpleNamespace(feature_leaders=[], record_issue=lambda *_args, **_kw: None)
    monkeypatch.setattr(
        from_model, "view_label_clearance", lambda _drawing, _view: lambda _box: True
    )

    assert (
        from_model.place_machined_leader_jobs(
            drawing,
            SimpleNamespace(),
            (
                (
                    "radius",
                    "plan",
                    (0.0, 0.0, 100.0, 60.0),
                    "R10",
                    (((0.0, 30.0), (-20.0, 30.0, 0.0), "feature"),),
                    (),
                ),
            ),
            noun="blend",
            drop_code="blend_dropped",
            ctx=context,
            joint=True,
            region_policy=LeaderRegionPolicy.AUTO,
        )
        == 0
    )
    assert len(context.feature_leaders) == 1

    candidates = tuple(context.feature_leaders[0].candidates)
    assert candidates
    assert isinstance(candidates[0], FeatureLeaderCandidate)
    assert candidates[0].region is LeaderCandidateRegion.INTERIOR
    assert any(isinstance(candidate, tuple) for candidate in candidates)
    assert context.feature_leaders[0].fallback_accept(candidates[0], (), None) is True


def test_late_machined_interior_only_policy_filters_exterior_and_unmeasurable_labels(
    monkeypatch,
):
    draft = draft_preset(font_size=3.0, decimal_precision=1)
    drawing = SimpleNamespace(draft=draft)
    context = SimpleNamespace(feature_leaders=[], record_issue=lambda *_args, **_kw: None)
    monkeypatch.setattr(from_model, "_text_size", lambda *_args, **_kwargs: (0.0, 0.0))
    monkeypatch.setattr(
        from_model, "view_label_clearance", lambda _drawing, _view: lambda _box: True
    )

    assert (
        from_model.place_machined_leader_jobs(
            drawing,
            SimpleNamespace(),
            (
                (
                    "radius",
                    "plan",
                    (0.0, 0.0, 100.0, 60.0),
                    "R10",
                    (((0.0, 30.0), (20.0, 30.0, 0.0), "feature"),),
                    (),
                ),
            ),
            noun="blend",
            drop_code="blend_dropped",
            ctx=context,
            joint=True,
            region_policy=LeaderRegionPolicy.INTERIOR,
        )
        == 0
    )
    assert tuple(context.feature_leaders[0].candidates) == ()


def test_grouped_machined_job_shares_one_interior_cap_and_keeps_exterior_floor(
    monkeypatch,
):
    draft = draft_preset(font_size=3.0, decimal_precision=1)
    drawing = SimpleNamespace(draft=draft)
    context = SimpleNamespace(feature_leaders=[], record_issue=lambda *_args, **_kw: None)
    monkeypatch.setattr(
        from_model, "view_label_clearance", lambda _drawing, _view: lambda _box: True
    )
    anchors = tuple(
        ((x, 0.0), (x + 1.0, 0.0), f"feature-{index}")
        for index, x in enumerate((-100.0, 0.0, 100.0))
    )

    from_model.place_machined_leader_jobs(
        drawing,
        SimpleNamespace(),
        (("radius", "plan", (-500.0, -500.0, 500.0, 500.0), "3× R10", anchors, ()),),
        noun="blend",
        drop_code="blend_dropped",
        ctx=context,
        joint=True,
        region_policy=LeaderRegionPolicy.AUTO,
    )

    (job,) = context.feature_leaders
    candidates = tuple(job.candidates)
    interior = [
        candidate
        for candidate in candidates
        if isinstance(candidate, FeatureLeaderCandidate)
        and candidate.region is LeaderCandidateRegion.INTERIOR
    ]
    assert len(interior) == 64
    assert {candidate.feature for candidate in interior} == {
        "feature-0",
        "feature-1",
        "feature-2",
    }
    assert tuple(job.fallback_candidates) == anchors


def _pattern_sheet(*, kind, members, solid, bcd=None):
    sheet = Sheet(solid, page="A4", scale=0.7).authored_dimensions()
    member = hole(diameter=6, through=True, at=members[0], axis="z")
    pattern = sheet.pattern(
        member,
        kind=kind,
        count=len(members),
        members=members,
        bcd=bcd,
    )
    sheet.envelope()
    sheet.dimension(pattern, "bore.diameter")
    if bcd is not None:
        sheet.dimension(pattern, "bolt_circle.diameter")
    return sheet.build()


def test_pattern_transaction_can_select_interior_whitespace():
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    members = ((-30, -20, 0), (-30, 20, 0), (30, -20, 0), (30, 20, 0))
    solid = Box(100, 80, 8, align=align)
    for x, y, z in members:
        solid -= Pos(x, y, z) * Cylinder(3, 8, align=align)

    drawing = _pattern_sheet(kind="other", members=members, solid=solid)

    leader = drawing.get_annotation("hc_plan0")
    bounds = drawing.view_bounds("plan")
    assert leader is not None and leader.label_bbox is not None and bounds is not None
    assert bounds[0] <= leader.label_bbox[0] < leader.label_bbox[2] <= bounds[2]
    assert bounds[1] <= leader.label_bbox[1] < leader.label_bbox[3] <= bounds[3]
    centres = [drawing.at("plan", *member) for member in members]
    centre = min(
        centres,
        key=lambda point: math.hypot(leader.tip[0] - point[0], leader.tip[1] - point[1]),
    )
    radial = (leader.tip[0] - centre[0], leader.tip[1] - centre[1])
    shaft = (leader.elbow[0] - leader.tip[0], leader.elbow[1] - leader.tip[1])
    assert math.hypot(*radial) == pytest.approx(3.0 * drawing.scale)
    assert radial[0] * shaft[1] - radial[1] * shaft[0] == pytest.approx(0.0, abs=1e-9)
    assert radial[0] * shaft[0] + radial[1] * shaft[1] > 0.0
    assert sum(name.startswith("m_cm") for name in drawing.annotations()) == 4
    assert not [
        issue
        for issue in drawing.lint()
        if "overlap" in issue.code or issue.code.endswith("_dropped")
    ]


def test_pattern_transaction_solves_against_its_bolt_circle_furniture():
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    members = tuple(
        (25 * math.cos(index * math.pi / 3), 25 * math.sin(index * math.pi / 3), 0)
        for index in range(6)
    )
    solid = Cylinder(50, 8, align=align)
    for x, y, z in members:
        solid -= Pos(x, y, z) * Cylinder(3, 8, align=align)

    drawing = _pattern_sheet(
        kind="bolt_circle",
        members=members,
        solid=solid,
        bcd=50,
    )

    assert {"hc_plan0", "bc_plan0"} <= set(drawing.annotations())
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code
        in {
            "annotation_overlap",
            "label_centerline_overlap",
            "hole_requirement_missing",
        }
        or issue.code.endswith("_dropped")
    ]


def test_pattern_transaction_removes_staged_furniture_when_callout_cannot_render(
    monkeypatch,
):
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    members = tuple(
        (25 * math.cos(index * math.pi / 3), 25 * math.sin(index * math.pi / 3), 0)
        for index in range(6)
    )
    solid = Cylinder(50, 8, align=align)
    for x, y, z in members:
        solid -= Pos(x, y, z) * Cylinder(3, 8, align=align)
    monkeypatch.setattr(leaders, "_materialize", lambda _dwg, _job, _candidate: None)

    drawing = _pattern_sheet(
        kind="bolt_circle",
        members=members,
        solid=solid,
        bcd=50,
    )

    assert "hc_plan0" not in drawing.annotations()
    assert "bc_plan0" not in drawing.annotations()
    assert any(issue.code == "callout_dropped" for issue in drawing.lint())
