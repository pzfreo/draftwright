"""#1166 — one bounded same-view inventory for compatible feature leaders."""

import json
from dataclasses import replace
from itertools import combinations
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot
from build123d_drafting.helpers import Draft, Leader

from draftwright import ScaleCompletenessWarning, build_drawing
from draftwright._geometry import (
    _boxes_overlap,
    _convex_polygons_overlap,
    _leader_ink_polygons,
    _segments_cross_or_overlap,
)
from draftwright.annotations._common import PlacementContext
from draftwright.annotations.leaders import (
    FeatureLeaderJob,
    _candidate_conflict,
    _MeasuredLeaderCandidate,
    collect_feature_leader,
    drain_feature_leaders,
)
from draftwright.annotations.orchestrator import _PASS_SEQUENCE
from draftwright.layout import _assign_leader_candidates
from draftwright.model import fillet


def _narrow_cross_pass_part():
    """Public GRM-class geometry: two side bores, a long pocket and end rounds."""

    part = Box(13.55, 11, 80, align=(Align.MIN, Align.CENTER, Align.MIN))
    part -= Pos(12.61, -1, 0) * Box(
        0.94,
        2,
        62.13,
        align=(Align.MIN, Align.MIN, Align.MIN),
    )
    for z, radius in ((66, 4), (75.5, 1.25)):
        part -= (
            Pos(0, 0, z)
            * Rot(0, 90, 0)
            * Cylinder(
                radius,
                13.55,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )
        )
    detected = build_drawing(part, auto_dims=False).model()
    rounded = [
        fillet(axis="x", radius=1, at=(0, -5.5, 0)),
        fillet(axis="x", radius=1, at=(13.55, -5.5, 0)),
        fillet(axis="y", radius=1, at=(0, -5.5, 0)),
        fillet(axis="z", radius=1, at=(0, -5.5, 0)),
        fillet(axis="z", radius=1, at=(13.55, 5.5, 0)),
    ]
    return part, replace(detected, features=[*detected.features, *rounded])


def _overfull_distinct_hole_strip_part():
    part = Box(80, 20, 8)
    for index in range(12):
        x = -35 + index * 70 / 11
        part -= Pos(x, 0, 4) * Cylinder(0.8 + index * 0.08, 8)
    return part


def _feature_leader_names(drawing):
    prefixes = ("hc_", "m_chamfer", "m_fillet", "m_flat", "m_pocket", "m_groove")
    return [
        name
        for name in drawing.annotations()
        if name.startswith(prefixes) and type(drawing.get_annotation(name)).__name__ == "Leader"
    ]


def test_public_narrow_part_uses_one_cross_pass_inventory(tmp_path):
    part, model = _narrow_cross_pass_part()
    trace_path = tmp_path / "cross-pass.json"

    drawing = build_drawing(part, model=model, page="A4", trace=trace_path)

    expected = {"hc_side0", "hc_side1", "m_fillet_x0", "m_pocket_yz0"}
    assert set(_feature_leader_names(drawing)) == expected
    assert drawing.lint() == []

    expected_parameters = {
        "hc_side0": {"bore.diameter"},
        "hc_side1": {"bore.diameter"},
        "m_fillet_x0": {"fillet.radius"},
        "m_pocket_yz0": {
            "pocket_width.length",
            "pocket_length.length",
            "pocket_depth.length",
        },
    }
    for name, parameters in expected_parameters.items():
        assert drawing.registry.feature_of(name) is not None
        assert {item["parameter_id"] for item in drawing.measurement_keys(name)} == parameters

    leaders = [drawing.get_annotation(name) for name in sorted(expected)]
    for left, right in combinations(leaders, 2):
        assert not _boxes_overlap(left.label_bbox, right.label_bbox)
        assert not any(
            _segments_cross_or_overlap(a0, a1, b0, b1)
            for a0, a1 in left.segments
            for b0, b1 in right.segments
        )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    events = [
        event for event in trace["pass_events"] if event["label"] == "feature_leader_inventory"
    ]
    # Scale/page repacks may start more than one auto phase; each phase still has
    # exactly one authoritative cross-pass drain. The last phase owns the result.
    assert all(
        sum(candidate["phase"] == phase for candidate in events) == 1
        for phase in {candidate["phase"] for candidate in events}
    )
    event = events[-1]
    assert event["assignment"] == "joint"
    assert event["optimal"] is True
    assert event["objective"]["placed"] == 4
    assert event["objective"]["penalty"] == 1
    assert sum(len(item.get("policy_b_blockers", ())) for item in event["items"]) == 1
    assert event["objective"]["cost"] == pytest.approx(
        sum(item["cost"] for item in event["items"])
    )
    assert {item["source_pass"] for item in event["items"]} == {"hole", "fillet", "pocket"}
    assert any(
        blocker.startswith("dim_")
        for item in event["items"]
        for rejected in item["rejected"]
        for blocker in rejected["blockers"]
    )
    # Pairwise alternatives name the semantic owner that won the contested lane.
    assert any(
        blocker in expected
        for item in event["items"]
        for rejected in item["rejected"]
        for blocker in rejected["blockers"]
    )


def test_cross_pass_objective_avoids_the_per_pass_greedy_trap():
    # Job 0's shortest alternative blocks job 1's only candidate. Independent
    # first-clear passes place one; the shared lexicographic solve places both.
    result = _assign_leader_candidates(
        ((1.0, 3.0), (1.0,)),
        ((0, 0, 1, 0),),
        priorities=(0.0, 0.0),
    )
    assert result.choices == (1, 0)
    assert result.optimal is True

    # Cardinality remains primary; priority decides which semantic requirement
    # survives only when both candidates cannot coexist.
    priority = _assign_leader_candidates(
        ((1.0,), (1.0,)),
        ((0, 0, 1, 0),),
        priorities=(1.0, 10.0),
    )
    assert priority.choices == (None, 0)

    policy_b = _assign_leader_candidates(
        ((1.0, 3.0),),
        priorities=(0.0,),
        penalties_by_job=((1, 0),),
    )
    assert policy_b.choices == (1,)  # crossing-free precedes shorter Policy-B fallback


def test_cross_pass_conflicts_use_rendered_shaft_width_not_zero_width_segments():
    draft = Draft()

    def candidate(y):
        tip = (0.0, y)
        elbow = (10.0, y)
        return _MeasuredLeaderCandidate(
            object(),
            tip,
            elbow,
            object(),
            0,
            10.0,
            None,
            ((tip, elbow),),
            _leader_ink_polygons(
                tip,
                elbow,
                arrow_length=0.0,
                line_width=draft.line_width,
            ),
        )

    assert _candidate_conflict(candidate(0.0), candidate(draft.line_width / 2.0))
    # Strict placement geometry treats exact boundary contact as clear.
    assert not _candidate_conflict(candidate(0.0), candidate(draft.line_width))
    assert not _convex_polygons_overlap((), candidate(0.0).ink_polygons[0])


def test_cross_pass_candidate_budget_precedes_collect_all_geometry(monkeypatch, tmp_path):
    part, model = _narrow_cross_pass_part()
    import draftwright.annotations.leaders as leaders

    measured = 0
    original = leaders._measure

    def counted(*args, **kwargs):
        nonlocal measured
        measured += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(leaders, "_FEATURE_LEADER_MAX_CANDIDATES", 1)
    monkeypatch.setattr(leaders, "_measure", counted)
    trace_path = tmp_path / "bounded-cross-pass.json"

    drawing = build_drawing(part, model=model, page="A4", trace=trace_path)
    event = next(
        item
        for item in json.loads(trace_path.read_text(encoding="utf-8"))["pass_events"]
        if item["label"] == "feature_leader_inventory"
    )

    assert event["assignment"] == "greedy_candidate_budget"
    assert event["optimal"] is False
    assert measured < 50  # an uncapped collect-all measures 78 alternatives here
    assert set(_feature_leader_names(drawing)) == {
        "hc_side0",
        "hc_side1",
        "m_fillet_x0",
        "m_pocket_yz0",
    }
    assert drawing.lint() == []


def test_fixed_obstacle_probe_budget_precedes_joint_geometry(monkeypatch, tmp_path):
    part, model = _narrow_cross_pass_part()
    monkeypatch.setattr(
        "draftwright.annotations.leaders._FEATURE_LEADER_MAX_FIXED_PROBES",
        0,
    )
    trace_path = tmp_path / "bounded-fixed-probes.json"

    drawing = build_drawing(part, model=model, page="A4", trace=trace_path)
    event = next(
        item
        for item in json.loads(trace_path.read_text(encoding="utf-8"))["pass_events"]
        if item["label"] == "feature_leader_inventory"
    )

    assert event["assignment"] == "greedy_fixed_probe_budget"
    assert event["optimal"] is False
    assert event["fixed_probes"] > 0
    assert set(_feature_leader_names(drawing)) == {
        "hc_side0",
        "hc_side1",
        "m_fillet_x0",
        "m_pocket_yz0",
    }
    assert drawing.lint() == []


def test_candidate_budget_preserves_the_exact_pre_joint_hole_floor(monkeypatch):
    part = _overfull_distinct_hole_strip_part()

    with pytest.warns(ScaleCompletenessWarning):
        joint = build_drawing(part, page="A4", scale=1, scale_policy="permissive")
    assert len([name for name in joint.annotations() if name.startswith("hc_plan")]) == 11
    assert len([issue for issue in joint.registry.issues if issue.code == "callout_dropped"]) == 1

    monkeypatch.setattr(
        "draftwright.annotations.leaders._FEATURE_LEADER_MAX_CANDIDATES",
        1,
    )
    with pytest.warns(ScaleCompletenessWarning):
        bounded = build_drawing(part, page="A4", scale=1, scale_policy="permissive")

    # The old queue placed six and dropped six. A resource cap must preserve
    # that exact semantic floor; it must not promote newly exposed alternatives.
    assert len([name for name in bounded.annotations() if name.startswith("hc_plan")]) == 6
    assert (
        len([issue for issue in bounded.registry.issues if issue.code == "callout_dropped"]) == 6
    )


def test_provisional_section_yields_but_mandatory_title_band_does_not():
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    rendered_title_box = drawing.get_annotation("title_block").bounding_box()
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=rendered_title_box.size.X,
    )
    drawing.remove("title_block")  # leave only the mandatory future-band reservation
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    tip = (bounds[2], (bounds[1] + bounds[3]) / 2)
    elbow = (bounds[2] + 15, tip[1], 0)

    def build(tip, elbow, _feature):
        return Leader(tip=(*tip, 0), elbow=elbow, label="REQUIRED", draft=drawing.draft)

    probe = build(tip, elbow, None)
    box = probe.label_bbox
    reservation_name = drawing.note(
        "SECTION RESERVATION",
        ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2),
        view="front",
        name="section_reservation",
    )
    reservation = drawing.get_annotation(reservation_name)
    reservation.is_provisional_layout_reservation = True
    assert _boxes_overlap(probe.label_bbox, reservation.label_bbox)

    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
    )
    assert collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="required_leader",
            view="front",
            silhouette=bounds,
            label="REQUIRED",
            candidates=((tip, elbow, None),),
            build=build,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
        ),
    )
    assert drain_feature_leaders(drawing, analysis, ctx) == 1
    assert "required_leader" in drawing.annotations()

    title_y = (rendered_title_box.min.Y + rendered_title_box.max.Y) / 2
    title_tip = (rendered_title_box.min.X - 8, title_y)
    title_elbow = (rendered_title_box.min.X + 2, title_y, 0)

    def build_in_title(tip, elbow, _feature):
        return Leader(tip=(*tip, 0), elbow=elbow, label="BLOCKED", draft=drawing.draft)

    title_probe = build_in_title(title_tip, title_elbow, None)
    assert _boxes_overlap(
        title_probe.label_bbox,
        (
            rendered_title_box.min.X,
            rendered_title_box.min.Y,
            rendered_title_box.max.X,
            rendered_title_box.max.Y,
        ),
    )
    assert collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="title_blocked_leader",
            view="front",
            silhouette=bounds,
            label="BLOCKED",
            candidates=((title_tip, title_elbow, None),),
            build=build_in_title,
            measurement=(),
            noun="hole",
            drop_code="callout_dropped",
            allow_policy_b_fixed=True,
        ),
    )
    assert drain_feature_leaders(drawing, analysis, ctx) == 0
    assert "title_blocked_leader" not in drawing.annotations()
    assert any(issue.code == "callout_dropped" for issue in drawing.registry.issues)


def test_live_and_deferred_callout_verbs_preserve_the_same_semantic_evidence():
    part, model = _narrow_cross_pass_part()
    holes = [feature for feature in model.features if feature.kind == "hole"]
    pocket = next(feature for feature in model.features if feature.kind == "pocket")
    one_fillet = next(feature for feature in model.features if feature.kind == "fillet")
    features = [*holes, pocket, one_fillet]

    live = build_drawing(part, model=model, page="A4", auto_dims=False)
    for feature in features:
        assert live.callout(feature)

    deferred = build_drawing(part, model=model, page="A4", auto_dims=False)
    with deferred.deferred():
        for feature in features:
            assert deferred.callout(feature) == ""

    assert set(_feature_leader_names(live)) == set(_feature_leader_names(deferred))
    for name in _feature_leader_names(live):
        assert live.measurement_keys(name) == deferred.measurement_keys(name)
        assert live.registry.feature_of(name) is not None
        assert deferred.registry.feature_of(name) is not None


def test_feature_leader_inventory_has_one_canonical_late_stage():
    assert _PASS_SEQUENCE.count("feature_leaders") == 1
    assert _PASS_SEQUENCE.index("drain") < _PASS_SEQUENCE.index("feature_leaders")
    assert _PASS_SEQUENCE.index("grooves") < _PASS_SEQUENCE.index("feature_leaders")
    assert _PASS_SEQUENCE.index("feature_leaders") < _PASS_SEQUENCE.index("section")
