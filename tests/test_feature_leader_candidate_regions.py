from types import SimpleNamespace

from build123d_drafting.helpers import Leader

from draftwright.annotations import leaders
from draftwright.annotations._common import PlacementContext
from draftwright.annotations.leaders import (
    FeatureLeaderCandidate,
    FeatureLeaderJob,
    LeaderCandidateRegion,
    _fixed_blockers,
    _FixedInkComponent,
    _measure,
    _MeasuredLeaderCandidate,
    _view_region_blocker,
    place_feature_leader_jobs,
)


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
