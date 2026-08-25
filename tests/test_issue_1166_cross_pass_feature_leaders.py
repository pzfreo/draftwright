"""#1166 — one bounded same-view inventory for compatible feature leaders."""

import json
import math
from dataclasses import replace
from itertools import combinations
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Edge, Face, Pos, Rot, Wire
from build123d_drafting import DatumFeature
from build123d_drafting.helpers import (
    Centerline,
    CenterlineCircle,
    CenterMark,
    Dimension,
    Draft,
    Leader,
)

from draftwright import ScaleCompletenessWarning, build_drawing
from draftwright._geometry import (
    _boxes_overlap,
    _convex_polygons_overlap,
    _leader_ink_polygons,
    _segments_cross_or_overlap,
    _stroke_polygon,
)
from draftwright.annotations._common import PlacementContext, SolveTrace
from draftwright.annotations.leaders import (
    _FIXED_INVENTORY_EXHAUSTED,
    FeatureLeaderJob,
    _annotation_fixed_ink,
    _candidate_conflict,
    _candidate_hits_component,
    _convex_hull,
    _face_exactly_covered,
    _FixedInkComponent,
    _measure,
    _MeasuredLeaderCandidate,
    _point_in_convex_component,
    _rendered_ink_matches,
    _rendered_residual_components,
    collect_feature_leader,
    drain_feature_leaders,
    feature_leader_fixed_conflicts,
)
from draftwright.annotations.orchestrator import _PASS_SEQUENCE
from draftwright.builder import _is_required_scale_drop, detect_part_model
from draftwright.layout import _assign_leader_candidates
from draftwright.linting.issues import is_placement_drop
from draftwright.model import fillet


def _narrow_cross_pass_solid():
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
    return part


def _narrow_cross_pass_part():
    part = _narrow_cross_pass_solid()
    detected = detect_part_model(part)
    rounded = [
        fillet(axis="x", radius=1, at=(0, -5.5, 0)),
        fillet(axis="x", radius=1, at=(13.55, -5.5, 0)),
        fillet(axis="y", radius=1, at=(0, -5.5, 0)),
        fillet(axis="z", radius=1, at=(0, -5.5, 0)),
        fillet(axis="z", radius=1, at=(13.55, 5.5, 0)),
    ]
    return part, replace(detected, features=[*detected.features, *rounded])


def test_direct_detection_matches_built_model_features_for_narrow_cross_pass_part():
    """The repeated helper's direct model path must retain its recognised inventory."""
    built = tuple(build_drawing(_narrow_cross_pass_solid(), auto_dims=False).model().features)
    detected = tuple(detect_part_model(_narrow_cross_pass_solid()).features)
    assert detected == built


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


@pytest.fixture(scope="module")
def inked_box_dwg():
    """One fixed-ink box build shared by read-only critiques (#656). Mutating tests build their own."""
    return build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)


@pytest.fixture(scope="module")
def tiny_box_dwg():
    return build_drawing(Box(10, 10, 2), page="A4", auto_dims=False)


def test_public_narrow_part_uses_one_cross_pass_inventory(tmp_path):
    part, model = _narrow_cross_pass_part()
    trace_path = tmp_path / "cross-pass.json"

    drawing = build_drawing(part, model=model, page="A3", trace=trace_path)

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

    # The shared stage must use complete leader ink against already-drained
    # dimension/witness strokes. Reverting it to label-only acceptance makes
    # this exact fixture cross dim_loc_side_y550 and m_env_depth while lint
    # otherwise remains silent.
    fixed_dimensions = [
        drawing.get_annotation(name)
        for name in drawing.annotations()
        if name.startswith("dim_") or name.startswith("m_env")
    ]
    assert not any(
        _segments_cross_or_overlap(a0, a1, b0, b1)
        for leader in leaders
        for dimension in fixed_dimensions
        for a0, a1 in leader.segments
        for b0, b1 in dimension.segments
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
    assert event["objective"]["penalty"] == 0
    assert sum(len(item.get("policy_b_blockers", ())) for item in event["items"]) == 0
    assert event["objective"]["cost"] == pytest.approx(
        sum(item["cost"] for item in event["items"])
    )
    assert {item["source_pass"] for item in event["items"]} == {"hole", "fillet", "pocket"}
    assert any(
        blocker == "dim_loc_side_y550:segment:1"
        for item in event["items"]
        for candidate in item["candidate_inventory"]
        for blocker in candidate["fixed_blockers"]
    )
    assert any(
        blocker == "m_env_depth:segment:3"
        for item in event["items"]
        for candidate in item["candidate_inventory"]
        for blocker in candidate["fixed_blockers"]
    )
    # Pairwise alternatives name the semantic owner that won the contested lane.
    assert any(
        blocker in expected
        for item in event["items"]
        for rejected in item["rejected"]
        for blocker in rejected["blockers"]
    )
    for item in event["items"]:
        inventory = item["candidate_inventory"]
        assert len(inventory) == item["candidates_tried"]
        assert sorted(candidate["candidate"] for candidate in inventory) == list(
            range(item["candidates_tried"])
        )
        assert sum(candidate["outcome"] == "selected" for candidate in inventory) == 1
        assert any(
            candidate["outcome"] in {"objective_rejected", "conflict_rejected"}
            for candidate in inventory
        )

    # Fixed Dimension arrows are local analytical components, not a corridor-
    # wide pad. Keep their actual CURVED OCC faces inside the same polygons the
    # solve and trace use.
    dimension = drawing.get_annotation("dim_loc_side_y550")
    components = _annotation_fixed_ink(
        drawing,
        "dim_loc_side_y550",
        dimension,
    )
    arrow_polygons = tuple(
        component.polygons[0] for component in components if ":arrow:" in component.name
    )
    rendered_arrows = []
    for face in dimension.faces():
        bounds = face.bounding_box()
        extents = sorted((bounds.size.X, bounds.size.Y))
        if extents == pytest.approx(
            sorted((drawing.draft.arrow_length, 2.0 * drawing.draft.arrow_length / 3.0))
        ):
            rendered_arrows.append(face)
    assert len(arrow_polygons) == len(rendered_arrows) == 2
    for face in rendered_arrows:
        vertices, _triangles = face.tessellate(0.01)
        assert all(
            any(
                _point_in_convex_component((vertex.X, vertex.Y), polygon)
                for polygon in arrow_polygons
            )
            for vertex in vertices
        )


def test_unavoidable_policy_b_crossing_is_persisted_without_opt_in_trace():
    part, model = _narrow_cross_pass_part()
    drawing = build_drawing(part, model=model, page="A4")

    issues = [issue for issue in drawing.lint() if issue.code == "feature_leader_crossing"]
    assert len(issues) == 1
    assert all("Policy B" in issue.message for issue in issues)
    assert any("dim_loc_side_z7550:segment:3" in issue.message for issue in issues)
    assert all(issue.severity == "info" for issue in issues)
    assert all(issue.measurement_ids for issue in issues)
    legibility = drawing.lint_summary()["quality"]["legibility"]
    # `annotation_ink_overlap` (#1321/#1332) scores on legibility too, and this
    # sheet carries two. Measured, not rendered -- the same family as the cases
    # that were; stage 3 (#1334) is what prevents them.
    assert legibility["by_code"] == {
        "feature_leader_crossing": 1,
        "annotation_ink_overlap": 2,
    }
    assert legibility["score"] < 1.0


def test_near_clear_witness_is_not_falsely_classified_by_strip_padding():
    part = Box(140, 90, 12)
    for x, y, radius in (
        (-55, -30, 3),
        (-40, 25, 4),
        (-20, -10, 2.5),
        (-5, 35, 5),
        (10, -35, 3),
        (25, 10, 4),
        (40, -20, 2.5),
        (55, 30, 5),
        (60, -38, 3),
        (-60, 38, 4),
    ):
        part -= Pos(x, y, 0) * Cylinder(radius, 12)

    drawing = build_drawing(part)
    issues = [issue.message for issue in drawing.lint() if issue.code == "feature_leader_crossing"]

    # hc_plan3 passes 1.45 mm from the actual 0.1-mm witness stroke. The
    # strip carve's historical 1.35-mm arrow padding makes their AABBs overlap,
    # but rendered ink does not. Reusing strip occupancy as collision truth
    # falsely reports this exact callout across m_locx5/m_locx9.
    assert not any("3× ⌀8 THRU" in message for message in issues)


def test_unrelated_center_furniture_is_fixed_ink_but_own_mark_is_not():
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    tip = (bounds[2], (bounds[1] + bounds[3]) / 2.0)
    natural = (tip[0] + 20.0, tip[1], 0.0)
    clear = (tip[0] + 20.0, tip[1] + 10.0, 0.0)
    target_owner = object()
    other_owner = object()
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
    )
    ctx.place(
        CenterMark((tip[0] + 10.0, tip[1], 0.0), 4.0, drawing.draft),
        "unrelated_center_mark",
        view="front",
        feature=other_owner,
    )
    ctx.place(
        CenterMark((*tip, 0.0), 4.0, drawing.draft),
        "own_center_mark",
        view="front",
        feature=target_owner,
    )

    def build(tip, elbow, _feature):
        return Leader(tip=(*tip, 0), elbow=elbow, label="R1", draft=drawing.draft)

    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="m_fillet0",
            view="front",
            silhouette=bounds,
            label="R1",
            candidates=((tip, natural, target_owner), (tip, clear, target_owner)),
            build=build,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
            allow_policy_b_fixed=True,
        ),
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=drawing.get_annotation("title_block").bounding_box().size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == 1
    assert drawing.get_annotation("m_fillet0").segments[0][1][1] == pytest.approx(clear[1])
    assert not any(issue.code == "feature_leader_crossing" for issue in drawing.lint())


def test_circular_center_furniture_keeps_its_empty_interior_available():
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
    )
    ring_owner = object()
    leader_owner = object()
    ctx.place(
        CenterlineCircle((100.0, 100.0), 40.0, drawing.draft),
        "unrelated_center_circle",
        view="front",
        feature=ring_owner,
    )
    components = _annotation_fixed_ink(
        drawing,
        "unrelated_center_circle",
        drawing.get_annotation("unrelated_center_circle"),
    )
    assert components
    assert all(":arc:" in component.name for component in components)
    assert not any(component.name.endswith(":geometry") for component in components)
    arc_polygons = tuple(component.polygons[0] for component in components)
    for face in drawing.get_annotation("unrelated_center_circle").faces():
        for edge in face.edges():
            assert all(
                any(
                    _point_in_convex_component((position.X, position.Y), polygon)
                    for polygon in arc_polygons
                )
                for position in edge.positions(tuple(index / 32 for index in range(33)))
            )

    ring_stroke = _stroke_polygon((79.9, 99.0), (80.1, 101.0), 0.1)
    assert ring_stroke is not None
    ring_hit = _MeasuredLeaderCandidate(
        object(),
        (79.9, 99.0),
        (80.1, 101.0),
        leader_owner,
        0,
        1.0,
        None,
        (),
        (ring_stroke,),
    )
    assert any(_candidate_hits_component(ring_hit, component) for component in components)

    tip = (92.0, 100.0)
    elbow = (100.0, 100.0)

    def build(tip, elbow, _feature):
        return Leader(tip=(*tip, 0), elbow=(*elbow, 0), label="R1", draft=drawing.draft)

    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="interior_leader",
            view="front",
            silhouette=(0.0, 0.0, 1.0, 1.0),
            label="R1",
            candidates=((tip, elbow, leader_owner),),
            build=build,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
        ),
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=drawing.get_annotation("title_block").bounding_box().size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == 1
    assert drawing.get_annotation("interior_leader").segments[0][1] == elbow


def test_shifted_dimension_keeps_rendered_arrowheads_in_fixed_ink(inked_box_dwg):
    drawing = inked_box_dwg
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
    )
    shifted = Dimension(
        (0.0, 0.0),
        (20.0, 0.0),
        "above",
        10.0,
        drawing.draft,
        label="20",
        label_offset_x=12.0,
    )
    assert len(shifted.segments) == 3
    ctx.place(shifted, "shifted_dimension", view="front")
    components = _annotation_fixed_ink(drawing, "shifted_dimension", shifted)
    arrows = [component for component in components if ":arrow:" in component.name]
    assert len(arrows) == 2

    arrow_only_stroke = _stroke_polygon((19.2, 9.3), (19.2, 10.7), 0.1)
    assert arrow_only_stroke is not None
    arrow_only = _MeasuredLeaderCandidate(
        object(),
        (19.2, 9.3),
        (19.2, 10.7),
        object(),
        0,
        1.0,
        None,
        (),
        (arrow_only_stroke,),
    )
    blockers = [
        component.name
        for component in components
        if _candidate_hits_component(arrow_only, component)
    ]
    assert blockers == ["shifted_dimension:arrow:1"]

    basic = Dimension(
        (0.0, 20.0),
        (20.0, 20.0),
        "above",
        10.0,
        drawing.draft,
        label="20",
        basic=True,
    )
    ctx.place(basic, "basic_dimension", view="front")
    basic_components = _annotation_fixed_ink(drawing, "basic_dimension", basic)
    assert len([component for component in basic_components if ":arrow:" in component.name]) == 2


def test_filled_datum_face_is_part_of_the_fixed_ink_inventory(inked_box_dwg):
    drawing = inked_box_dwg
    datum = DatumFeature("A", draft=drawing.draft, filled=True)

    components = _annotation_fixed_ink(drawing, "datum_test", datum)
    triangle_only_stroke = _stroke_polygon((-0.5, 2.0), (0.5, 2.0), 0.1)
    assert triangle_only_stroke is not None
    candidate = _MeasuredLeaderCandidate(
        object(),
        (-0.5, 2.0),
        (0.5, 2.0),
        object(),
        0,
        1.0,
        None,
        (),
        (triangle_only_stroke,),
    )

    blockers = [
        component.name
        for component in components
        if _candidate_hits_component(candidate, component)
    ]
    assert len(blockers) == 1
    assert blockers[0].startswith("datum_test:ink:")


def test_ownerless_section_centerline_at_the_tip_is_not_a_global_axis_exemption():
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    tip = (bounds[2], (bounds[1] + bounds[3]) / 2.0)
    elbow = (tip[0] + 20.0, tip[1], 0.0)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
    )
    ctx.place(
        Centerline((tip[0], tip[1] - 6.0, 0.0), (tip[0], tip[1] + 6.0, 0.0)),
        "section_line",
        view="front",
    )

    def build(tip, elbow, _feature):
        return Leader(tip=(*tip, 0), elbow=elbow, label="R1", draft=drawing.draft)

    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="m_fillet0",
            view="front",
            silhouette=bounds,
            label="R1",
            candidates=((tip, elbow, object()),),
            build=build,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
            allow_policy_b_fixed=True,
        ),
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=drawing.get_annotation("title_block").bounding_box().size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == 1
    assert any(
        issue.code == "feature_leader_crossing" and "section_line:segment:0" in issue.message
        for issue in drawing.lint()
    )
    assert feature_leader_fixed_conflicts(drawing, ("section_line",)) == (
        ("m_fillet0", "section_line:segment:0"),
    )


def test_explicit_global_turning_axis_allows_only_tip_attachment():
    drawing = build_drawing(Cylinder(10, 20), page="A4")
    centerline = drawing.get_annotation("centerline_front")
    components = _annotation_fixed_ink(drawing, "centerline_front", centerline)
    axis_components = [component for component in components if component.segment is not None]
    assert axis_components
    assert all(component.global_axis for component in axis_components)
    axis_component = axis_components[len(axis_components) // 2]
    assert axis_component.segment is not None
    first, second = axis_component.segment
    tip = ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)
    elbow = (tip[0] + 10.0, tip[1] + 10.0)
    primary = _leader_ink_polygons(
        tip,
        elbow,
        arrow_length=drawing.draft.arrow_length,
        line_width=drawing.draft.line_width,
    )
    candidate = _MeasuredLeaderCandidate(
        object(),
        tip,
        elbow,
        object(),
        0,
        1.0,
        None,
        ((tip, elbow),),
        primary,
        (),
    )
    assert _candidate_hits_component(candidate, axis_component) is False

    shelf_segment = ((elbow[0], elbow[1]), (tip[0] - 10.0, elbow[1]))
    shelf = _stroke_polygon(*shelf_segment, drawing.draft.line_width)
    assert shelf is not None
    shelf_crossing = replace(
        candidate,
        segments=((tip, elbow), shelf_segment),
        ink_polygons=(*primary, shelf),
        axis_residual_polygons=(shelf,),
    )
    continuous_ink = _stroke_polygon(
        (tip[0], tip[1] - 20.0),
        (tip[0], tip[1] + 20.0),
        drawing.draft.line_width,
    )
    assert continuous_ink is not None
    continuous_axis = replace(
        axis_component,
        polygons=(continuous_ink,),
        segment=((tip[0], tip[1] - 20.0), (tip[0], tip[1] + 20.0)),
    )
    assert _candidate_hits_component(shelf_crossing, continuous_axis) is True


@pytest.mark.parametrize(
    "case",
    ("zero_length_axis", "box_label", "box_residual", "polygon_label"),
)
def test_global_axis_exemption_keeps_every_non_attachment_collision(case):
    draft = Draft()
    tip = (0.0, 0.0)
    elbow = (5.0, 5.0)
    primary = _leader_ink_polygons(
        tip,
        elbow,
        arrow_length=draft.arrow_length,
        line_width=draft.line_width,
    )
    label_box = (20.0, 20.0, 22.0, 22.0)
    residual = ()
    segment = ((0.0, -10.0), (0.0, 10.0))
    component = _FixedInkComponent(
        "axis:segment:0",
        box=(-0.2, -0.2, 0.2, 0.2),
        kind="Centerline",
        segment=segment,
        global_axis=True,
    )
    if case == "zero_length_axis":
        label_box = (-0.1, -0.1, 0.1, 0.1)
        component = replace(component, segment=((0.0, 0.0), (0.0, 0.0)))
    elif case == "box_label":
        label_box = (-0.1, -0.1, 0.1, 0.1)
    elif case == "box_residual":
        residual_polygon = _stroke_polygon((-1.0, 2.0), (1.0, 2.0), draft.line_width)
        assert residual_polygon is not None
        residual = (residual_polygon,)
        component = replace(component, box=(-0.2, 1.5, 0.2, 2.5))
    else:
        label_box = (-0.1, 1.5, 0.1, 2.5)
        component = replace(
            component,
            box=None,
            polygons=((((-0.2, 1.0), (0.2, 1.0), (0.2, 3.0), (-0.2, 3.0))),),
        )
    candidate = _MeasuredLeaderCandidate(
        object(),
        tip,
        elbow,
        object(),
        0,
        1.0,
        label_box,
        ((tip, elbow),),
        primary,
        residual,
    )

    assert _candidate_hits_component(candidate, component)


def test_global_axis_tip_attachment_does_not_exempt_a_later_shelf_crossing():
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
    )
    axis = Centerline((100.0, 95.0, 0.0), (100.0, 105.0, 0.0))
    axis.is_global_axis_centerline = True
    ctx.place(axis, "test_global_axis", view="front")

    tip = (100.0, 100.0)
    elbow = (100.5, 100.5, 0.0)

    def build(tip, elbow, text_side):
        return Leader(
            tip=(*tip, 0.0),
            elbow=elbow,
            label="R1",
            draft=drawing.draft,
            text_side=text_side,
        )

    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="m_fillet0",
            view="front",
            silhouette=(20.0, 20.0, 40.0, 40.0),
            label="R1",
            candidates=((tip, elbow, "left"), (tip, elbow, "right")),
            build=build,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
            allow_policy_b_fixed=True,
        ),
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=drawing.get_annotation("title_block").bounding_box().size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == 1
    leader = drawing.get_annotation("m_fillet0")
    assert leader.segments[1][1][0] > elbow[0]  # clear right shelf wins
    assert drawing.lint() == []
    assert feature_leader_fixed_conflicts(drawing, ("test_global_axis",)) == ()


def test_global_axis_tip_attachment_does_not_exempt_near_collinear_shaft_travel():
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
    )
    axis = Centerline((50.0, 100.0, 0.0), (150.0, 100.0, 0.0))
    axis.is_global_axis_centerline = True
    ctx.place(axis, "test_global_axis", view="front")

    tip = (50.0, 100.0)
    elbow = (150.0, 100.01, 0.0)

    def build(tip, elbow, _feature):
        return Leader(
            tip=(*tip, 0.0),
            elbow=elbow,
            label="R1",
            draft=drawing.draft,
            text_side="right",
        )

    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="m_fillet0",
            view="front",
            silhouette=(20.0, 20.0, 40.0, 40.0),
            label="R1",
            candidates=((tip, elbow, object()),),
            build=build,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
            allow_policy_b_fixed=True,
        ),
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=drawing.get_annotation("title_block").bounding_box().size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == 1
    assert any(
        issue.code == "feature_leader_crossing" and "test_global_axis:segment:" in issue.message
        for issue in drawing.lint()
    )
    conflicts = feature_leader_fixed_conflicts(drawing, ("test_global_axis",))
    assert conflicts
    assert all(
        leader == "m_fillet0" and component.startswith("test_global_axis:segment:")
        for leader, component in conflicts
    )


@pytest.mark.parametrize("unavailable_attribute", ["segments", "label_bbox"])
def test_final_preflight_yields_when_landed_leader_metadata_is_unavailable(
    monkeypatch, unavailable_attribute
):
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
    )
    leader = Leader(
        tip=(50.0, 50.0, 0.0),
        elbow=(60.0, 60.0, 0.0),
        label="R1",
        draft=drawing.draft,
    )
    ctx.place(leader, "m_fillet0", view="front")
    ctx.place(
        Centerline((40.0, 50.0, 0.0), (70.0, 50.0, 0.0)),
        "section_line",
        view="front",
    )

    def unavailable(_annotation):
        raise RuntimeError(f"{unavailable_attribute} unavailable")

    monkeypatch.setattr(Leader, unavailable_attribute, property(unavailable))

    assert feature_leader_fixed_conflicts(drawing, ("section_line",)) == (
        ("m_fillet0", "m_fillet0:geometry_unverified"),
    )


@pytest.mark.parametrize("rendered_failure", ["empty", "exception"])
def test_final_preflight_fails_closed_when_rendered_faces_are_unavailable(
    monkeypatch, rendered_failure
):
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
    )
    leader = Leader(
        tip=(50.0, 50.0, 0.0),
        elbow=(60.0, 60.0, 0.0),
        label="R1",
        draft=drawing.draft,
    )
    ctx.place(leader, "m_fillet0", view="front")
    ctx.place(
        Centerline((40.0, 40.0, 0.0), (70.0, 40.0, 0.0)),
        "section_line",
        view="front",
    )
    assert feature_leader_fixed_conflicts(drawing, ("section_line",)) == ()

    def unavailable_faces(_annotation):
        if rendered_failure == "exception":
            raise RuntimeError("rendered faces unavailable")
        return ()

    monkeypatch.setattr(Leader, "faces", unavailable_faces)

    assert feature_leader_fixed_conflicts(drawing, ("section_line",)) == (
        ("m_fillet0", "m_fillet0:geometry_unverified"),
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


def test_selected_occ_survivor_validates_arrow_shaft_and_shelf_ink():
    draft = Draft()

    def build(tip, elbow, _feature):
        return Leader(tip=(*tip, 0), elbow=(*elbow, 0), label="R1", draft=draft)

    job = FeatureLeaderJob(
        name="m_fillet0",
        view="front",
        silhouette=(0.0, 0.0, 10.0, 10.0),
        label="R1",
        candidates=(),
        build=build,
        measurement=(),
        noun="fillet",
        drop_code="fillet_dropped",
    )
    candidate = _measure(0, ((0.0, 0.0), (20.0, 5.0), None), job, draft)

    assert _rendered_ink_matches(candidate, candidate.annotation)
    assert not _rendered_ink_matches(
        replace(candidate, ink_polygons=(candidate.ink_polygons[0], *candidate.ink_polygons[2:])),
        candidate.annotation,
    )
    assert not _rendered_ink_matches(
        replace(candidate, ink_polygons=candidate.ink_polygons[:2]),
        candidate.annotation,
    )


def test_machined_leader_wrong_analytical_ink_fails_the_rendered_survivor_gate(
    monkeypatch,
):
    import draftwright.annotations.leaders as leaders

    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    tip = (bounds[2], (bounds[1] + bounds[3]) / 2.0)
    elbow = (tip[0] + 15.0, tip[1])

    def build(tip, elbow, _feature):
        return Leader(tip=(*tip, 0), elbow=(*elbow, 0), label="R1", draft=drawing.draft)

    probe = build(tip, elbow, None)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
    )
    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="m_fillet0",
            view="front",
            silhouette=bounds,
            label="R1",
            candidates=((tip, elbow, None),),
            build=build,
            analytical_geometry=lambda *_args: (probe.label_bbox, probe.segments),
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
        ),
    )
    # Deliberately under-predict the shaft/arrow ink while keeping label and segment
    # metadata exact. _geometry_matches therefore passes; rendered-face validation must
    # be the gate that rejects the selected OCC survivor.
    monkeypatch.setattr(leaders, "_leader_ink_polygons", lambda *_args, **_kwargs: ())
    title = drawing.get_annotation("title_block").bounding_box()
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=title.size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == 0
    assert "m_fillet0" not in drawing.annotations()
    issue = next(issue for issue in drawing.lint() if issue.code == "fillet_dropped")
    assert issue.outcome_stage == "validation"
    assert "rendered geometry validation failed" in issue.message


def test_continuous_face_coverage_accepts_multi_shape_cut_results():
    """build123d 0.10 returns a ShapeList where 0.11 returns one shape."""

    class MultiShapeCutFace:
        area = 10.0

        def __init__(self, residual_areas):
            self.residual_areas = residual_areas

        def cut(self, *_cover_faces):
            return [SimpleNamespace(area=area) for area in self.residual_areas]

    assert _face_exactly_covered(
        MultiShapeCutFace((3e-10, 4e-10)),
        (((0.0, 0.0), (2.0, 0.0), (0.0, 2.0)),),
        None,
    )
    assert not _face_exactly_covered(
        MultiShapeCutFace((6e-9, 6e-9)),
        (((0.0, 0.0), (2.0, 0.0), (0.0, 2.0)),),
        None,
    )

    class CutMustNotRun:
        area = 10.0

        def __init__(self):
            self.cut_calls = 0

        def cut(self, *_cover_faces):
            self.cut_calls += 1
            return SimpleNamespace(area=0.0)

    cut_must_not_run = CutMustNotRun()
    assert not _face_exactly_covered(cut_must_not_run, ((), ((0.0, 0.0),)), None)
    assert cut_must_not_run.cut_calls == 0

    class CutRaises:
        area = 10.0

        def cut(self, *_cover_faces):
            raise RuntimeError("OCC subtraction failed")

    assert not _face_exactly_covered(
        CutRaises(),
        (((0.0, 0.0), (2.0, 0.0), (0.0, 2.0)),),
        None,
    )


def test_selected_occ_survivor_tessellates_curves_between_quarter_stations():
    circle = Edge.make_circle(1.0)
    face = Face(Wire(circle))
    vertices, _triangles = face.tessellate(0.01)
    inscribed_hull = _convex_hull(tuple((vertex.X, vertex.Y) for vertex in vertices))
    candidate = _MeasuredLeaderCandidate(
        face,
        (0.0, 0.0),
        (1.0, 0.0),
        None,
        0,
        1.0,
        None,
        (),
        (inscribed_hull,),
    )
    # Every tessellation vertex is in this inscribed polygon; the continuous
    # circle still escapes it between every adjacent station.
    assert _rendered_ink_matches(candidate, face) is False

    residual = _rendered_residual_components(
        "fixed",
        face,
        (_FixedInkComponent("fixed:known", polygons=(inscribed_hull,)),),
        None,
        None,
        "CenterlineCircle",
    )
    assert residual is not None
    assert [component.name for component in residual] == ["fixed:arc:0"]


def test_selected_occ_survivor_rejects_a_face_bridging_disjoint_components():
    face = Face.make_rect(10.0, 1.0)
    left = ((-5.0, -0.5), (-4.0, -0.5), (-4.0, 0.5), (-5.0, 0.5))
    right = ((4.0, -0.5), (5.0, -0.5), (5.0, 0.5), (4.0, 0.5))
    candidate = _MeasuredLeaderCandidate(
        face,
        (0.0, 0.0),
        (1.0, 0.0),
        None,
        0,
        1.0,
        None,
        (),
        (left, right),
    )
    # Every tessellation vertex is covered by the union, but each triangle
    # bridges the uncovered eight-millimetre gap between the two components.
    assert _rendered_ink_matches(candidate, face) is False


@pytest.mark.parametrize("failure", ["mismatch", "exception"])
@pytest.mark.parametrize("valid_tail", [True, False])
@pytest.mark.parametrize("drop_callback", [False, True])
def test_rendered_validation_failure_replays_the_producer_tail(
    tmp_path, failure, valid_tail, drop_callback
):
    trace_path = tmp_path / "geometry-feedback.json"
    drawing = build_drawing(
        Box(40, 30, 8),
        page="A4",
        auto_dims=False,
    )
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    tip = (bounds[2], (bounds[1] + bounds[3]) / 2.0)
    short = (tip[0] + 12.0, tip[1])
    long = (tip[0] + 22.0, tip[1] + 4.0)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
        trace=SolveTrace(trace_path),
    )

    def leader(tip, elbow):
        return Leader(tip=(*tip, 0), elbow=(*elbow, 0), label="R1", draft=drawing.draft)

    def analytical(tip, elbow, _feature):
        expected = leader(tip, elbow)
        return expected.label_bbox, expected.segments

    def build(tip, elbow, _feature):
        if elbow == short or not valid_tail:
            # Simulate helper drift after analytical collection: the first
            # winner either raises or no longer matches its measured contract.
            if failure == "exception":
                raise RuntimeError("helper construction failed")
            return leader(tip, (elbow[0] + 1.0, elbow[1]))
        return leader(tip, elbow)

    def on_drop(reason):
        ctx.record_issue(
            "warning",
            "fillet_dropped",
            f"drop callback reason: {reason}",
            outcome_stage=("validation" if reason == "geometry_validation" else "placement"),
        )

    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="m_fillet0",
            view="front",
            silhouette=bounds,
            label="R1",
            candidates=((tip, short, object()), (tip, long, object())),
            build=build,
            analytical_geometry=analytical,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
            on_drop=on_drop if drop_callback else None,
        ),
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=drawing.get_annotation("title_block").bounding_box().size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == int(valid_tail)
    ctx.trace.write()
    issues = [issue for issue in drawing.lint() if issue.code == "fillet_dropped"]
    if valid_tail:
        assert drawing.get_annotation("m_fillet0").segments[0][1] == long
        assert not issues
    else:
        assert "m_fillet0" not in drawing.annotations()
        assert len(issues) == 1
        expected = (
            "geometry_validation" if drop_callback else "rendered geometry validation failed"
        )
        assert expected in issues[0].message
        assert issues[0].outcome_stage == "validation"
        assert not is_placement_drop(issues[0])
        assert not _is_required_scale_drop(issues[0])
    event = next(
        item
        for item in json.loads(trace_path.read_text(encoding="utf-8"))["pass_events"]
        if item["label"] == "feature_leader_inventory"
    )
    assert event["assignment"] == "greedy_geometry_validation"
    assert event["optimal"] is False
    assert event["fixed_probes"] <= event["fixed_probe_bound"]
    item = event["items"][0]
    assert [entry["outcome"] for entry in item["candidate_inventory"]] == [
        "geometry_validation",
        "objective_rejected",
    ]
    assert all(
        entry["blockers"] == ["geometry_validation"]
        for entry in item["producer_fallback"]["rejected"]
    )
    if valid_tail:
        assert item["producer_fallback"]["selected"]["candidate"] == 1
    else:
        assert item["outcome"] == "dropped"
        assert item["reason"] == "geometry_validation"
        assert item["producer_fallback"]["selected"] is None


def test_prebuilt_survivor_mesh_failure_replays_a_valid_tail(tmp_path):
    trace_path = tmp_path / "prebuilt-geometry-feedback.json"
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    tip = (bounds[2], (bounds[1] + bounds[3]) / 2.0)
    bad = (tip[0] + 12.0, tip[1])
    good = (tip[0] + 22.0, tip[1] + 4.0)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
        trace=SolveTrace(trace_path),
    )

    def build(tip, elbow, _feature):
        leader = Leader(tip=(*tip, 0), elbow=(*elbow, 0), label="R1", draft=drawing.draft)
        if elbow != bad:
            return leader

        def faces():
            raise RuntimeError("prebuilt survivor tessellation failed")

        return SimpleNamespace(
            label_bbox=leader.label_bbox,
            segments=leader.segments,
            faces=faces,
        )

    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="m_fillet0",
            view="front",
            silhouette=bounds,
            label="R1",
            candidates=((tip, bad, object()), (tip, good, object())),
            build=build,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
        ),
    )
    title = drawing.get_annotation("title_block").bounding_box()
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=title.size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == 1
    ctx.trace.write()
    assert drawing.get_annotation("m_fillet0").segments[0][1] == good
    event = next(
        item
        for item in json.loads(trace_path.read_text(encoding="utf-8"))["pass_events"]
        if item["label"] == "feature_leader_inventory"
    )
    assert event["assignment"] == "greedy_geometry_validation"
    item = event["items"][0]
    assert item["candidate_inventory"][0]["outcome"] == "geometry_validation"
    assert item["producer_fallback"]["rejected"][0]["blockers"] == ["geometry_validation"]
    assert item["producer_fallback"]["selected"]["candidate"] == 1


@pytest.mark.parametrize("valid_tail", [True, False])
@pytest.mark.parametrize("drop_callback", [False, True])
@pytest.mark.parametrize(
    "failure",
    [
        "exception",
        "geometry_none",
        "missing_label",
        "malformed_analytical",
        "reversed_label",
        "degenerate_label",
        "nonfinite_segment",
        "overflow_analytical",
    ],
)
@pytest.mark.parametrize("fixed_budget", [None, 0])
def test_candidate_measurement_failure_is_truthful_and_does_not_abort(
    monkeypatch, tmp_path, valid_tail, drop_callback, failure, fixed_budget
):
    if fixed_budget is not None:
        monkeypatch.setattr(
            "draftwright.annotations.leaders._FEATURE_LEADER_MAX_FIXED_WORK",
            fixed_budget,
        )
    trace_path = tmp_path / "candidate-construction.json"
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    tip = (bounds[2], (bounds[1] + bounds[3]) / 2.0)
    bad = (tip[0] + 12.0, tip[1])
    good = (tip[0] + 22.0, tip[1] + 4.0)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
        trace=SolveTrace(trace_path),
    )

    def build(tip, elbow, _feature):
        if failure == "exception" and (elbow == bad or not valid_tail):
            raise RuntimeError("one candidate could not be constructed")
        return Leader(tip=(*tip, 0), elbow=(*elbow, 0), label="R1", draft=drawing.draft)

    def analytical(tip, elbow, _feature):
        if failure == "geometry_none" and (elbow == bad or not valid_tail):
            return None
        if failure == "missing_label" and (elbow == bad or not valid_tail):
            return None, (((0.0, 0.0), (1.0, 1.0)),)
        if failure == "reversed_label" and (elbow == bad or not valid_tail):
            return (10.0, 10.0, 5.0, 5.0), (((0.0, 0.0), (1.0, 1.0)),)
        if failure == "degenerate_label" and (elbow == bad or not valid_tail):
            return (5.0, 5.0, 5.0, 6.0), (((0.0, 0.0), (1.0, 1.0)),)
        if failure == "nonfinite_segment" and (elbow == bad or not valid_tail):
            return (0.0, 0.0, 1.0, 1.0), (((0.0, 0.0), (float("inf"), 1.0)),)
        if failure == "overflow_analytical" and (elbow == bad or not valid_tail):
            return (0.0, 0.0, 1.0, 1.0), (((1e308, 0.0), (-1e308, 0.0)),)
        if elbow == bad or not valid_tail:
            return (float("nan"), 0.0, 1.0, 1.0), (((0.0, 0.0), (1.0, 1.0)),)
        expected = build(tip, elbow, _feature)
        return expected.label_bbox, expected.segments

    def on_drop(reason):
        ctx.record_issue(
            "warning",
            "fillet_dropped",
            f"drop callback reason: {reason}",
            outcome_stage=("validation" if reason == "geometry_validation" else "placement"),
        )

    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="m_fillet0",
            view="front",
            silhouette=bounds,
            label="R1",
            candidates=((tip, bad, object()), (tip, good, object())),
            build=build,
            analytical_geometry=(analytical if failure != "exception" else None),
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
            on_drop=on_drop if drop_callback else None,
        ),
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=drawing.get_annotation("title_block").bounding_box().size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == int(valid_tail)
    ctx.trace.write()
    issues = [issue for issue in drawing.lint() if issue.code == "fillet_dropped"]
    if valid_tail:
        assert drawing.get_annotation("m_fillet0").segments[0][1] == good
        assert not issues
    else:
        assert "m_fillet0" not in drawing.annotations()
        assert len(issues) == 1
        expected = (
            "geometry_validation" if drop_callback else "rendered geometry validation failed"
        )
        assert expected in issues[0].message
        assert issues[0].outcome_stage == "validation"
        assert not is_placement_drop(issues[0])
        assert not _is_required_scale_drop(issues[0])
    event = next(
        item
        for item in json.loads(trace_path.read_text(encoding="utf-8"))["pass_events"]
        if item["label"] == "feature_leader_inventory"
    )
    item = event["items"][0]
    expected_blockers = ["geometry_validation"]
    if fixed_budget == 0:
        expected_blockers.append("fixed_probe_budget")
    assert item["candidate_inventory"][0]["fixed_blockers"] == expected_blockers
    if valid_tail:
        assert item["candidate_inventory"][1]["outcome"] == "selected"
    else:
        assert item["reason"] == "geometry_validation"


def test_malformed_candidate_metadata_stays_finite_and_fails_closed():
    job = FeatureLeaderJob(
        name="malformed",
        view="front",
        silhouette=(0.0, 0.0, 1.0, 1.0),
        label="R1",
        candidates=(),
        build=lambda *_args: SimpleNamespace(
            label_bbox=("bad", 0.0, 1.0, 1.0),
            segments=(object(),),
        ),
        measurement=(),
        noun="fillet",
        drop_code="fillet_dropped",
    )

    malformed_metadata = _measure(0, ((0.0, 0.0), (1.0, 1.0), object()), job, Draft())
    malformed_raw = _measure(1, (), job, Draft())
    nonfinite_point = _measure(
        2,
        ((float("inf"), 0.0), (1.0, 1.0), object()),
        job,
        Draft(),
    )

    for candidate in (malformed_metadata, malformed_raw, nonfinite_point):
        assert candidate.failure_reason == "geometry_validation"
        assert all(map(math.isfinite, (*candidate.tip, *candidate.elbow, candidate.cost)))


def test_live_context_without_shared_inventory_keeps_immediate_path():
    assert not collect_feature_leader(SimpleNamespace(feature_leaders=None), object())


@pytest.mark.parametrize(
    "metadata",
    [
        {"segments": (((0.0, 0.0), (1.0, 1.0)),)},
        {"fixed_ink_polygons": (((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),)},
        {"label_bbox": (0.0, 0.0, 1.0, 1.0)},
    ],
)
def test_zero_component_budget_stops_before_each_fixed_metadata_source(metadata, tiny_box_dwg):
    drawing = tiny_box_dwg
    face_calls = 0

    def faces():
        nonlocal face_calls
        face_calls += 1
        return (Face.make_rect(1.0, 1.0),)

    annotation_metadata = {
        "segments": (),
        "fixed_ink_polygons": (),
        "label_bbox": None,
        "faces": faces,
    }
    annotation_metadata.update(metadata)
    annotation = SimpleNamespace(**annotation_metadata)

    result = _annotation_fixed_ink(
        drawing,
        "fixed_budget_probe",
        annotation,
        max_components=0,
    )

    assert result is _FIXED_INVENTORY_EXHAUSTED
    assert face_calls == 0


def test_zero_component_budget_stops_before_rendered_face_lowering():
    face_calls = 0

    def faces():
        nonlocal face_calls
        face_calls += 1
        return (Face.make_rect(1.0, 1.0),)

    result = _rendered_residual_components(
        "fixed",
        SimpleNamespace(faces=faces),
        (),
        None,
        None,
        "Note",
        max_components=0,
    )

    assert result is _FIXED_INVENTORY_EXHAUSTED
    assert face_calls == 1


@pytest.mark.parametrize(
    "box_values",
    [
        None,
        (float("nan"), 0.0, 2.0, 2.0),
        (2.0, 2.0, 1.0, 1.0),
        (1.0, 1.0, 1.0, 2.0),
    ],
)
def test_failed_residual_and_bad_box_cannot_certify_partial_fixed_ink(box_values, tiny_box_dwg):
    drawing = tiny_box_dwg

    def faces():
        raise RuntimeError("rendered geometry unavailable")

    def bounding_box():
        if box_values is None:
            raise RuntimeError("rendered geometry unavailable")
        x0, y0, x1, y1 = box_values
        return SimpleNamespace(
            min=SimpleNamespace(X=x0, Y=y0),
            max=SimpleNamespace(X=x1, Y=y1),
        )

    annotation = SimpleNamespace(
        segments=(((0.0, 0.0), (1.0, 0.0)),),
        fixed_ink_polygons=(),
        label_bbox=None,
        faces=faces,
        bounding_box=bounding_box,
    )

    bounded = _annotation_fixed_ink(
        drawing,
        "unavailable_fixed_ink",
        annotation,
        max_components=8,
    )
    conservative = _annotation_fixed_ink(
        drawing,
        "unavailable_fixed_ink",
        annotation,
    )

    assert bounded is _FIXED_INVENTORY_EXHAUSTED
    assert [component.name for component in conservative][-1] == (
        "unavailable_fixed_ink:geometry_unverified"
    )
    assert conservative[-1].box == (0.0, 0.0, drawing.page_w, drawing.page_h)


def test_empty_face_inventory_uses_only_conservative_geometry_fallback(tiny_box_dwg):
    drawing = tiny_box_dwg
    bbox_calls = 0
    rendered_box = Box(4, 3, 1).bounding_box()

    def bounding_box():
        nonlocal bbox_calls
        bbox_calls += 1
        return rendered_box

    annotation = SimpleNamespace(
        segments=(((0.0, 0.0), (1.0, 0.0)),),
        fixed_ink_polygons=(),
        label_bbox=None,
        faces=lambda: (),
        bounding_box=bounding_box,
    )

    bounded = _annotation_fixed_ink(
        drawing,
        "empty_faces",
        annotation,
        max_components=8,
    )
    conservative = _annotation_fixed_ink(drawing, "empty_faces", annotation)

    assert bounded is _FIXED_INVENTORY_EXHAUSTED
    assert [component.name for component in conservative][-1] == "empty_faces:geometry"
    assert bbox_calls == 1


def test_empty_faces_accept_an_explicit_complete_fixed_ink_footprint(tiny_box_dwg):
    drawing = tiny_box_dwg
    polygon = ((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0))
    annotation = SimpleNamespace(
        segments=(),
        fixed_ink_polygons=(polygon,),
        label_bbox=None,
        faces=lambda: (),
    )

    components = _annotation_fixed_ink(
        drawing,
        "explicit_fixed_ink",
        annotation,
        max_components=8,
    )

    assert components is not _FIXED_INVENTORY_EXHAUSTED
    assert [component.name for component in components] == ["explicit_fixed_ink:ink:0"]
    assert set(components[0].polygons[0]) == set(polygon)


@pytest.mark.parametrize("raising_attribute", ["segments", "fixed_ink_polygons", "label_bbox"])
def test_raising_fixed_metadata_uses_the_unavailable_inventory_contract(
    raising_attribute, tiny_box_dwg
):
    drawing = tiny_box_dwg

    class RaisingMetadata:
        segments = ()
        fixed_ink_polygons = ()
        label_bbox = None

        def __getattribute__(self, name):
            if name == raising_attribute:
                raise RuntimeError(f"{name} unavailable")
            return super().__getattribute__(name)

    annotation = RaisingMetadata()
    bounded = _annotation_fixed_ink(
        drawing,
        "raising_metadata",
        annotation,
        max_components=8,
    )
    conservative = _annotation_fixed_ink(drawing, "raising_metadata", annotation)

    assert bounded is _FIXED_INVENTORY_EXHAUSTED
    assert conservative[-1].name == "raising_metadata:geometry_unverified"
    assert conservative[-1].box == (0.0, 0.0, drawing.page_w, drawing.page_h)


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_fixed_segments_use_the_unavailable_inventory_contract(coordinate, tiny_box_dwg):
    drawing = tiny_box_dwg
    annotation = SimpleNamespace(
        segments=(((coordinate, 0.0), (1.0, 0.0)),),
        fixed_ink_polygons=(),
        label_bbox=None,
        faces=lambda: (Face.make_rect(1.0, 1.0),),
    )

    bounded = _annotation_fixed_ink(
        drawing,
        "nonfinite_segment",
        annotation,
        max_components=8,
    )
    conservative = _annotation_fixed_ink(drawing, "nonfinite_segment", annotation)

    assert bounded is _FIXED_INVENTORY_EXHAUSTED
    assert conservative[-1].name == "nonfinite_segment:geometry_unverified"
    assert conservative[-1].box == (0.0, 0.0, drawing.page_w, drawing.page_h)


@pytest.mark.parametrize(
    "polygon",
    [
        ((0.0, 0.0), (1.0, 0.0)),
        ((0.0, 0.0), (1.0, 0.0), (2.0, 0.0)),
        ((0.0, 0.0), (float("nan"), 0.0), (0.0, 1.0)),
    ],
)
def test_malformed_fixed_polygons_use_the_unavailable_inventory_contract(polygon, tiny_box_dwg):
    drawing = tiny_box_dwg
    annotation = SimpleNamespace(
        segments=(),
        fixed_ink_polygons=(polygon,),
        label_bbox=None,
        faces=lambda: (),
    )

    bounded = _annotation_fixed_ink(
        drawing,
        "malformed_polygon",
        annotation,
        max_components=8,
    )
    conservative = _annotation_fixed_ink(drawing, "malformed_polygon", annotation)

    assert bounded is _FIXED_INVENTORY_EXHAUSTED
    assert conservative[-1].name == "malformed_polygon:geometry_unverified"
    assert conservative[-1].box == (0.0, 0.0, drawing.page_w, drawing.page_h)


def test_malformed_rendered_triangles_use_the_unavailable_inventory_contract(tiny_box_dwg):
    drawing = tiny_box_dwg
    rendered_box = Box(4, 3, 1).bounding_box()

    class MalformedFace:
        def tessellate(self, _tolerance):
            vertices = (
                SimpleNamespace(X=0.0, Y=0.0),
                SimpleNamespace(X=1.0, Y=0.0),
                SimpleNamespace(X=0.0, Y=1.0),
            )
            return vertices, ((0, 1, 99),)

        def edges(self):
            return ()

    annotation = SimpleNamespace(
        segments=(((0.0, 0.0), (1.0, 0.0)),),
        fixed_ink_polygons=(),
        label_bbox=None,
        faces=lambda: (MalformedFace(),),
        bounding_box=lambda: rendered_box,
    )

    bounded = _annotation_fixed_ink(
        drawing,
        "malformed_tessellation",
        annotation,
        max_components=8,
    )
    conservative = _annotation_fixed_ink(drawing, "malformed_tessellation", annotation)

    assert bounded is _FIXED_INVENTORY_EXHAUSTED
    assert conservative[-1].name == "malformed_tessellation:geometry"


@pytest.mark.parametrize(
    ("vertices", "triangles"),
    [
        (((0.2, 0.2), (0.3, 0.2), (99.0, 99.0)), ((),)),
        (((0.2, 0.2), (0.3, 0.2), (99.0, 99.0)), ((0,),)),
        (((0.2, 0.2), (0.3, 0.2), (99.0, 99.0)), ((0, 1),)),
        (((0.2, 0.2), (0.3, 0.2), (99.0, 99.0)), ((0, 0, 1),)),
        (((0.2, 0.2), (0.3, 0.2), (99.0, 99.0)), ((0, 1, True),)),
        (((0.2, 0.2), (0.3, 0.2), (99.0, 99.0), (0.2, 0.3)), ((0, 1, 3),)),
        (((0.2, 0.2), (0.3, 0.2), (float("nan"), 99.0)), ((0, 1, 2),)),
    ],
)
def test_malformed_face_mesh_fails_both_rendered_ink_paths(vertices, triangles):
    class MalformedRendered:
        def faces(self):
            return (self,)

        def tessellate(self, _tolerance):
            return tuple(SimpleNamespace(X=x, Y=y) for x, y in vertices), triangles

        def edges(self):
            return ()

    annotation = MalformedRendered()
    containing = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    candidate = _MeasuredLeaderCandidate(
        annotation,
        (0.0, 0.0),
        (1.0, 0.0),
        None,
        0,
        1.0,
        None,
        (),
        (containing,),
    )

    assert _rendered_ink_matches(candidate, annotation) is False
    assert (
        _rendered_residual_components(
            "malformed",
            annotation,
            (_FixedInkComponent("malformed:known", polygons=(containing,)),),
            None,
            None,
            "Note",
        )
        is None
    )


def test_fixed_residual_keeps_a_face_bridging_disjoint_known_components():
    face = Face.make_rect(10.0, 1.0)
    left = ((-5.0, -0.5), (-4.0, -0.5), (-4.0, 0.5), (-5.0, 0.5))
    right = ((4.0, -0.5), (5.0, -0.5), (5.0, 0.5), (4.0, 0.5))
    known = (
        _FixedInkComponent("fixed:left", polygons=(left,)),
        _FixedInkComponent("fixed:right", polygons=(right,)),
    )

    residual = _rendered_residual_components("fixed", face, known, None, None, "Note")

    assert residual is not None
    assert [component.name for component in residual] == ["fixed:ink:0"]
    bridge_stroke = _stroke_polygon((0.0, -1.0), (0.0, 1.0), 0.1)
    assert bridge_stroke is not None
    candidate = _MeasuredLeaderCandidate(
        object(),
        (0.0, -1.0),
        (0.0, 1.0),
        object(),
        0,
        1.0,
        None,
        (),
        (bridge_stroke,),
    )
    assert _candidate_hits_component(candidate, residual[0])


def test_cross_pass_candidate_budget_precedes_collect_all_geometry(monkeypatch, tmp_path):
    part, model = _narrow_cross_pass_part()
    import draftwright.annotations.leaders as leaders

    measured = 0
    original = leaders._measure

    def counted(*args, **kwargs):
        nonlocal measured
        measured += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(leaders, "_FEATURE_LEADER_MAX_MEASURE_WORK", 1)
    monkeypatch.setattr(leaders, "_measure", counted)
    trace_path = tmp_path / "bounded-cross-pass.json"

    drawing = build_drawing(part, model=model, page="A3", trace=trace_path)
    event = next(
        item
        for item in json.loads(trace_path.read_text(encoding="utf-8"))["pass_events"]
        if item["label"] == "feature_leader_inventory"
    )

    assert event["assignment"] == "greedy_candidate_budget"
    assert event["optimal"] is False
    assert event["joint_measurement_work"] > 1
    assert event["joint_measurement_work_limit_per_view"] == 1
    assert measured < 50  # an uncapped collect-all measures 78 alternatives here
    assert set(_feature_leader_names(drawing)) == {
        "hc_side0",
        "hc_side1",
        "m_fillet_x0",
        "m_pocket_yz0",
    }
    issues = drawing.lint()
    assert {issue.code for issue in issues} == {"feature_leader_crossing"}
    assert all("Policy B" in issue.message for issue in issues)


def test_fixed_obstacle_probe_budget_precedes_joint_geometry(monkeypatch, tmp_path):
    part, model = _narrow_cross_pass_part()
    import draftwright.annotations.leaders as leaders

    probes = 0
    lowerings = 0
    original = leaders._candidate_hits_component
    original_lower = leaders._annotation_fixed_ink

    def counted(*args, **kwargs):
        nonlocal probes
        probes += 1
        return original(*args, **kwargs)

    def counted_lower(*args, **kwargs):
        nonlocal lowerings
        lowerings += 1
        return original_lower(*args, **kwargs)

    monkeypatch.setattr(leaders, "_FEATURE_LEADER_MAX_FIXED_WORK", 0)
    monkeypatch.setattr(leaders, "_candidate_hits_component", counted)
    monkeypatch.setattr(leaders, "_annotation_fixed_ink", counted_lower)
    trace_path = tmp_path / "bounded-fixed-probes.json"

    drawing = build_drawing(part, model=model, page="A3", trace=trace_path)
    event = next(
        item
        for item in json.loads(trace_path.read_text(encoding="utf-8"))["pass_events"]
        if item["label"] == "feature_leader_inventory"
    )

    assert event["assignment"] == "greedy_fixed_inventory_budget"
    assert event["optimal"] is False
    assert event["fixed_probes"] == 0
    assert event["fixed_probe_bound"] > 0
    assert event["fixed_work_limit"] == 0
    assert probes == 0
    assert lowerings == 0
    assert set(_feature_leader_names(drawing)) == {
        "hc_side0",
        "hc_side1",
        "m_fillet_x0",
        "m_pocket_yz0",
    }
    issues = drawing.lint()
    assert {issue.code for issue in issues} == {"feature_leader_fixed_ink_unverified"}
    assert all("probe budget exhausted" in issue.message for issue in issues)
    legibility = drawing.lint_summary()["quality"]["legibility"]
    assert legibility["score"] < 1.0
    assert legibility["infos"] == len(issues)
    assert legibility["by_code"] == {"feature_leader_fixed_ink_unverified": len(issues)}


def test_fixed_probe_product_budget_replays_the_exact_producer_floor(monkeypatch, tmp_path):
    """Candidate×fixed work is capped even when the fixed inventory itself fits."""

    part, model = _narrow_cross_pass_part()
    monkeypatch.setattr(
        "draftwright.annotations.leaders._FEATURE_LEADER_MAX_FIXED_WORK",
        75,
    )
    trace_path = tmp_path / "bounded-fixed-probe-product.json"

    drawing = build_drawing(part, model=model, page="A3", trace=trace_path)
    event = next(
        item
        for item in json.loads(trace_path.read_text(encoding="utf-8"))["pass_events"]
        if item["label"] == "feature_leader_inventory"
    )

    assert event["assignment"] == "greedy_fixed_probe_budget"
    assert event["optimal"] is False
    assert event["fixed_probe_bound"] > 75 >= event["fixed_probes"]
    assert event["fixed_work_limit"] == 75
    assert event["pair_probes"] == 0
    assert event["objective"]["placed"] == 4
    assert set(_feature_leader_names(drawing)) == {
        "hc_side0",
        "hc_side1",
        "m_fillet_x0",
        "m_pocket_yz0",
    }
    assert all(item["producer_fallback"]["selected"] is not None for item in event["items"])
    selected = [item["producer_fallback"]["selected"] for item in event["items"]]
    assert selected[0]["fixed_blockers"] == []
    assert all(entry["fixed_blockers"] == ["fixed_probe_budget"] for entry in selected[1:])
    issues = drawing.lint()
    assert len(issues) == 3
    assert {issue.code for issue in issues} == {"feature_leader_fixed_ink_unverified"}
    assert all("probe budget exhausted" in issue.message for issue in issues)


@pytest.mark.parametrize("hard_boundary", ("page", "silhouette", "title"))
def test_resource_fallback_never_bypasses_hard_boundaries(monkeypatch, tmp_path, hard_boundary):
    monkeypatch.setattr(
        "draftwright.annotations.leaders._FEATURE_LEADER_MAX_FIXED_WORK",
        0,
    )
    trace_path = tmp_path / f"hard-{hard_boundary}.json"
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    title = drawing.get_annotation("title_block").bounding_box()
    if hard_boundary == "page":
        tip = (270.0, 100.0)
        elbow = (291.0, 100.0, 0.0)
    elif hard_boundary == "silhouette":
        tip = (120.0, 100.0)
        elbow = (140.0, 100.0, 0.0)
    else:
        title_y = (title.min.Y + title.max.Y) / 2.0
        tip = (title.min.X - 8.0, title_y)
        elbow = (title.min.X + 2.0, title_y, 0.0)
    probe = Leader(tip=(*tip, 0.0), elbow=elbow, label="BLOCKED", draft=drawing.draft)
    silhouette = drawing.view_bounds("front")
    assert silhouette is not None
    if hard_boundary == "silhouette":
        x0, y0, x1, y1 = probe.label_bbox
        silhouette = (x0 - 1.0, y0 - 1.0, x1 + 1.0, y1 + 1.0)
    built = 0

    def analytical(_tip, _elbow, _feature):
        return probe.label_bbox, probe.segments

    def build(_tip, _elbow, _feature):
        nonlocal built
        built += 1
        return probe

    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
        trace=SolveTrace(trace_path),
    )
    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name=f"hard_{hard_boundary}_leader",
            view="front",
            silhouette=silhouette,
            label="BLOCKED",
            candidates=((tip, elbow, object()),),
            build=build,
            analytical_geometry=analytical,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
            fallback_accept=lambda *_args: True,
            allow_policy_b_fixed=True,
        ),
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=title.size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == 0
    assert built == 0
    ctx.trace.write()
    event = next(
        item
        for item in json.loads(trace_path.read_text(encoding="utf-8"))["pass_events"]
        if item["label"] == "feature_leader_inventory"
    )
    assert event["assignment"] == "greedy_fixed_inventory_budget"
    rejected = event["items"][0]["producer_fallback"]["rejected"]
    assert len(rejected) == 1
    target = {
        "page": "page",
        "silhouette": "view:front:silhouette",
        "title": "title_block:reserved",
    }[hard_boundary]
    assert set(rejected[0]["blockers"]) == {target, "fixed_probe_budget"}


def test_candidate_budget_preserves_the_exact_pre_joint_hole_floor(monkeypatch):
    part = _overfull_distinct_hole_strip_part()

    with pytest.warns(ScaleCompletenessWarning):
        joint = build_drawing(part, page="A4", scale=1, scale_policy="permissive")
    assert len([name for name in joint.annotations() if name.startswith("hc_plan")]) == 12
    assert not [issue for issue in joint.registry.issues if issue.code == "callout_dropped"]

    monkeypatch.setattr(
        "draftwright.annotations.leaders._FEATURE_LEADER_MAX_MEASURE_WORK",
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


def test_future_section_cannot_veto_a_required_leader_but_title_is_hard(monkeypatch):
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
        name="section_a_right",
    )
    reservation = drawing.get_annotation(reservation_name)
    reservation.is_provisional_layout_reservation = True
    assert _boxes_overlap(probe.label_bbox, reservation.label_bbox)

    # Exercise the producer fallback, not only the normal joint solve: optional
    # future furniture must not become a veto when a resource guard fires.
    monkeypatch.setattr(
        "draftwright.annotations.leaders._FEATURE_LEADER_MAX_MEASURE_WORK",
        0,
    )

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
            fallback_accept=lambda candidate, boxes, _page: (
                not any(_boxes_overlap(candidate.label_box, box) for box in boxes)
            ),
            allow_policy_b_fixed=True,
        ),
    )
    assert drain_feature_leaders(drawing, analysis, ctx) == 1
    required = drawing.get_annotation("required_leader")
    assert not hasattr(required, "_dw_provisional_feature_leader_policy")
    assert not any(issue.code == "feature_leader_crossing" for issue in drawing.registry.issues)

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
            fallback_accept=lambda *_args: True,
            allow_policy_b_fixed=True,
        ),
    )
    monkeypatch.setattr(
        "draftwright.annotations.leaders._FEATURE_LEADER_MAX_MEASURE_WORK",
        0,
    )
    assert drain_feature_leaders(drawing, analysis, ctx) == 0
    assert "title_blocked_leader" not in drawing.annotations()
    assert any(issue.code == "callout_dropped" for issue in drawing.registry.issues)


def test_rendered_title_keeps_the_whole_mandatory_band_hard():
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    title = drawing.get_annotation("title_block")
    title_box = title.bounding_box()
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=title_box.size.X,
    )

    # This dot sits in blank rendered title-cell space: it misses the title's
    # decomposed strokes/glyphs, but the whole mandatory band is reserved.
    tip = (173.0, 12.0)
    elbow = (174.0, 12.0, 0)

    def build(tip, elbow, _feature):
        return Leader(tip=(*tip, 0), elbow=elbow, label=".", draft=drawing.draft)

    probe = build(tip, elbow, None)
    assert title_box.min.X < probe.label_bbox[0] < probe.label_bbox[2] < title_box.max.X
    assert title_box.min.Y < probe.label_bbox[1] < probe.label_bbox[3] < title_box.max.Y
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
            name="title_cell_leader",
            view="front",
            silhouette=bounds,
            label=".",
            candidates=((tip, elbow, None),),
            build=build,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
        ),
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == 0
    assert "title_cell_leader" not in drawing.annotations()
    assert any(issue.code == "fillet_dropped" for issue in drawing.registry.issues)


def test_provisional_section_refines_without_reducing_required_leaders():
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    mid_y = (bounds[1] + bounds[3]) / 2
    tip = (bounds[2], mid_y)
    crossing_elbow = (bounds[2] + 15, mid_y)
    clear_elbow = (bounds[2] + 15, mid_y + 12)

    def build(tip, elbow, _feature):
        return Leader(tip=(*tip, 0), elbow=(*elbow, 0), label="REQUIRED", draft=drawing.draft)

    crossing = build(tip, crossing_elbow, None)
    box = crossing.label_bbox
    reservation_name = drawing.note(
        "FUTURE SECTION",
        ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2),
        view="front",
        name="section_future_label",
    )
    drawing.get_annotation(reservation_name).is_provisional_layout_reservation = True
    assert _boxes_overlap(crossing.label_bbox, drawing.get_annotation(reservation_name).label_bbox)

    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
    )
    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="required_leader",
            view="front",
            silhouette=bounds,
            label="REQUIRED",
            candidates=((tip, crossing_elbow, None), (tip, clear_elbow, None)),
            build=build,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
            allow_policy_b_fixed=True,
        ),
    )
    title = drawing.get_annotation("title_block").bounding_box()
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=title.size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == 1
    placed = drawing.get_annotation("required_leader")
    assert placed.segments[0][1] == clear_elbow
    assert not _boxes_overlap(
        placed.label_bbox, drawing.get_annotation(reservation_name).label_bbox
    )
    assert not any(issue.code == "feature_leader_crossing" for issue in drawing.registry.issues)


def test_non_provisional_section_prefixed_annotation_is_classified_immediately():
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    bounds = drawing.view_bounds("front")
    assert bounds is not None
    tip = (bounds[2], (bounds[1] + bounds[3]) / 2)
    elbow = (bounds[2] + 15, tip[1], 0)

    def build(tip, elbow, _feature):
        return Leader(tip=(*tip, 0), elbow=elbow, label="REQUIRED", draft=drawing.draft)

    probe = build(tip, elbow, None)
    box = probe.label_bbox
    drawing.note(
        "COMMITTED SECTION NOTE",
        ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2),
        view="front",
        name="section_user_note",
    )
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
    )
    collect_feature_leader(
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
            allow_policy_b_fixed=True,
        ),
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=drawing.get_annotation("title_block").bounding_box().size.X,
    )

    assert drain_feature_leaders(drawing, analysis, ctx) == 1
    required = drawing.get_annotation("required_leader")
    assert not hasattr(required, "_dw_provisional_feature_leader_policy")
    assert any(
        issue.code == "feature_leader_crossing" and "section_user_note:label" in issue.message
        for issue in drawing.registry.issues
    )


def test_final_section_geometry_stays_clear_of_shared_feature_leaders():
    # Central bore + offset counterbore triggers the real final section pass.
    # Ignoring its provisional row lets hc_plan1 take the same y=173.5 lane;
    # the final cutting plane then crosses its shaft and crowds its label.
    part = Box(90, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)
    drawing = build_drawing(part)
    section = drawing.get_annotation("section_line")
    leaders = [
        drawing.get_annotation(name)
        for name in drawing.annotations()
        if name.startswith("hc_plan")
    ]

    assert leaders
    assert "section_aa" in drawing.views
    assert not any(
        _segments_cross_or_overlap(a0, a1, b0, b1)
        for leader in leaders
        for a0, a1 in leader.segments
        for b0, b1 in section.segments
    )
    assert all(
        not any(
            min(start[0], end[0]) < leader.label_bbox[2]
            and max(start[0], end[0]) > leader.label_bbox[0]
            and min(start[1], end[1]) < leader.label_bbox[3]
            and max(start[1], end[1]) > leader.label_bbox[1]
            for start, end in section.segments
        )
        for leader in leaders
    )
    for side in ("left", "right"):
        arrow = drawing.get_annotation(f"section_arrow_{side}")
        polygons = arrow.fixed_ink_polygons
        assert len(polygons) == 1
        for face in arrow.faces():
            vertices, _triangles = face.tessellate(0.01)
            assert all(
                _point_in_convex_component((vertex.X, vertex.Y), polygons[0])
                for vertex in vertices
            )


def test_final_section_rolls_back_when_exact_preflight_rejects(monkeypatch):
    part = Box(90, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)
    monkeypatch.setattr(
        "draftwright.annotations.sections.feature_leader_fixed_conflicts",
        lambda *_args: (("hc_plan0", "section_arrow_right:ink:0"),),
    )

    drawing = build_drawing(part)

    assert any(name.startswith("hc_plan") for name in drawing.annotations())
    assert "section_aa" not in drawing.views
    assert not any(name.startswith("section_") for name in drawing.annotations())


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
