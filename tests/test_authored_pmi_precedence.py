"""Imported lengths cover automatic stations without changing a PMI-free chain."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.builder import detect_part_model
from draftwright.linting.coverage import _authored_axial_witness_on_profile
from draftwright.model.compiled import _authored_bore_axis_location_matches, compile_dimensions
from draftwright.model.ir import (
    AuthoredDimension,
    CylindricalReference,
    Datum,
    EnvelopeFeature,
    Frame,
    HoleFeature,
    NominalRequirement,
    PartModel,
    PatternFeature,
    RotationalFeature,
    StepFeature,
    ToleranceDecoration,
)
from draftwright.model.planner import DimensionId, plan_dimensions
from draftwright.model.pmi_lowering import (
    add_authored_step_positions,
    lower_ap242_nominal_diameters,
    lower_ap242_nominal_step_lengths,
)
from draftwright.registry import AnnotationRegistry
from draftwright.reporting import ReportUnavailableError, placed_dimension_sources
from draftwright.sheet_emit import emit_sheet_script, generate_sheet_script

_SPECIFY_PLATE_2192 = Path(__file__).parent / "fixtures/specify_plate_2192_ap242.step"
_PLATE_SOURCE_40_IDS = {
    "dimension:0:1:4:2",
    "dimension:0:1:4:13",
    "dimension:0:1:4:15",
}


def _step(start: float, end: float, diameter: float) -> StepFeature:
    return StepFeature(
        Frame(((start + end) / 2, 0, 0), "x"),
        end - start,
        diameter,
        ((start, 0, 0), (end, 0, 0)),
    )


def _source(start: float, end: float, source_id: str) -> AuthoredDimension:
    return AuthoredDimension(
        Frame(((start + end) / 2, 0, 0), "x"),
        "linear",
        end - start,
        f"{end - start:g}",
        "X",
        ref_pts=((start, 0, 0), (end, 0, 0)),
        source_id=source_id,
    )


def _model(*sources: AuthoredDimension) -> PartModel:
    bbox = (Pos(26, 0, 0) * Box(52, 30, 30)).bounding_box()
    envelope = EnvelopeFeature(Frame((26, 0, 0), "x"), 52, 30, 30, (0, -15, -15), (52, 15, 15))
    return PartModel(
        bbox,
        "x",
        [
            envelope,
            _step(0, 15, 30),
            _step(15, 40, 20),
            _step(40, 52, 12),
            *sources,
        ],
    )


def _step_lengths(model: PartModel) -> list[tuple[float, tuple | None, bool]]:
    return [
        (dimension.param.value, dimension.param.span, dimension.suppressed)
        for group in plan_dimensions(model)
        if isinstance(group.feature, StepFeature)
        for dimension in group.dims
        if dimension.param.parameter_id == "step.length"
    ]


def test_imported_baseline_covers_its_station_and_planner_fills_from_same_end():
    exact = _source(0, 15, "dimension:15")
    baseline = _source(0, 40, "dimension:40")
    model = add_authored_step_positions(lower_ap242_nominal_step_lengths(_model(exact, baseline)))

    # The exact first source has a canonical owner; the 0→40 source remains raw PMI.
    assert [f.source_id for f in model.features if isinstance(f, AuthoredDimension)] == [
        "dimension:40"
    ]
    assert _step_lengths(model) == [
        (15, ((0, 0, 0), (15, 0, 0)), False),
        (25, ((15, 0, 0), (40, 0, 0)), True),
        (12, ((40, 0, 0), (52, 0, 0)), True),
    ]
    compiled = compile_dimensions(model)
    assert [
        (dimension.parameter_id, dimension.value, dimension.span)
        for group in compiled.of_kind("step")
        for dimension in group.dims
        if dimension.kind == "length"
    ] == [
        ("step.length", 15, ((0, 0, 0), (15, 0, 0))),
        ("step_position.length", 52, ((0, 0, 0), (52, 0, 0))),
    ]
    assert compiled.of_kind("step")[-1].dim(kind="length").id.feature.length == 12


def test_single_authored_baseline_makes_uncovered_stations_baselines():
    model = add_authored_step_positions(_model(_source(0, 40, "dimension:40")))
    assert _step_lengths(model) == [
        (15, ((0, 0, 0), (15, 0, 0)), False),
        (25, ((15, 0, 0), (40, 0, 0)), True),
        (12, ((40, 0, 0), (52, 0, 0)), True),
    ]
    position = next(
        dimension
        for group in plan_dimensions(model)
        for dimension in group.dims
        if dimension.param.parameter_id == "step_position.length"
    )
    assert (position.param.value, position.param.span, position.suppressed) == (
        52,
        ((0, 0, 0), (52, 0, 0)),
        False,
    )


def test_one_toleranced_step_source_covers_only_that_chain_segment():
    source = replace(_source(0, 15, "dimension:15"), upper_tol=0.1, lower_tol=-0.1)
    assert _step_lengths(_model(source)) == [
        (15, ((0, 0, 0), (15, 0, 0)), True),
        (25, ((15, 0, 0), (40, 0, 0)), False),
        (12, ((40, 0, 0), (52, 0, 0)), False),
    ]


def test_offset_parallel_face_witnesses_cover_the_same_axial_step():
    source = replace(
        _source(0, 15, "dimension:offset-15"),
        ref_pts=((0, -10, 0), (15, 10, 0)),
    )
    assert [(value, withheld) for value, _span, withheld in _step_lengths(_model(source))] == [
        (15, True),
        (25, False),
        (12, False),
    ]


def test_offset_face_source_still_covers_its_step_beside_another_body():
    source = replace(_source(0, 15, "dimension:first"), ref_pts=((0, -10, 0), (15, 10, 0)))
    model = _model(source)
    model.features.extend(
        replace(
            _step(start, end, diameter),
            frame=Frame(((start + end) / 2, 50, 0), "x"),
            span=((start, 50, 0), (end, 50, 0)),
            profile_group="second-body",
        )
        for start, end, diameter in ((0, 15, 30), (15, 40, 20), (40, 52, 12))
    )
    assert _step_lengths(model)[0] == (15, ((0, 0, 0), (15, 0, 0)), True)


def test_source_precision_is_kept_when_plain_label_differs_from_planner():
    source = replace(_source(0, 15, "dimension:15.0"), label="15.0")
    model = lower_ap242_nominal_step_lengths(_model(source))
    assert any(
        isinstance(feature, AuthoredDimension)
        and feature.source_id == source.source_id
        and feature.label == source.label
        for feature in model.features
    )
    assert _step_lengths(model)[0][2]


def test_basic_source_is_not_lowered_into_an_unboxed_step_dimension():
    source = replace(_source(0, 15, "dimension:basic-15"), basic=True)
    lowered = lower_ap242_nominal_step_lengths(_model(source))
    assert source in lowered.features


def test_noncanonical_diameter_label_stays_authored_and_covers_step_diameter():
    step = _step(0, 15, 30)
    source = AuthoredDimension(
        Frame((7.5, 0, 0), "x"),
        "diameter",
        30,
        "ø30.0",
        "X",
        ref_pts=((7.5, 0, 0),),
        source_id="dimension:od-precision",
        cylindrical_refs=(CylindricalReference((0, 0, 0), (1, 0, 0), 15, (0, 15), "external"),),
    )
    model = lower_ap242_nominal_diameters(
        PartModel(Box(15, 30, 30).bounding_box(), "x", [step, source])
    )
    assert source in model.features
    diameter = next(
        dimension
        for group in plan_dimensions(model)
        if group.feature is step
        for dimension in group.dims
        if dimension.param.parameter_id == "step.diameter"
    )
    assert diameter.suppressed
    part = Pos(7.5, 0, 0) * Cylinder(15, 15, rotation=(0, 90, 0))
    drawing = build_drawing(
        part, model=model, pmi="annotate", page="A3", scale=1, detail_view=False, repair=False
    )
    diameter_labels = [
        drawing.registry.named(name).label
        for name in drawing.registry.names()
        if str(getattr(drawing.registry.named(name), "label", "")).startswith("ø30")
    ]
    assert diameter_labels == ["ø30.0"]


def test_step_diameter_source_does_not_cover_another_bodys_rotational_od():
    source = AuthoredDimension(
        Frame((7.5, 0, 0), "x"),
        "diameter",
        30,
        "ø30.0",
        "X",
        ref_pts=((7.5, 0, 0),),
        source_id="dimension:first-body",
        cylindrical_refs=(CylindricalReference((0, 0, 0), (1, 0, 0), 15, (0, 15), "external"),),
    )
    distant = RotationalFeature(Frame((107.5, 0, 0), "x"), od=30)
    model = lower_ap242_nominal_diameters(
        PartModel(Box(120, 30, 30).bounding_box(), "x", [_step(0, 15, 30), distant, source])
    )
    diameter = next(
        dimension
        for group in plan_dimensions(model)
        if group.feature is distant
        for dimension in group.dims
        if dimension.param.parameter_id == "od.diameter"
    )
    assert not diameter.suppressed


def test_step_diameter_source_covers_whole_bodys_rotational_od():
    source = AuthoredDimension(
        Frame((7.5, 0, 0), "x"),
        "diameter",
        30,
        "ø30.0",
        "X",
        ref_pts=((7.5, 0, 0),),
        source_id="dimension:body-od",
        cylindrical_refs=(CylindricalReference((0, 0, 0), (1, 0, 0), 15, (0, 15), "external"),),
    )
    rotational = RotationalFeature(Frame((26, 0, 0), "x"), od=30)
    model = _model(source)
    model.features.append(rotational)
    lowered = lower_ap242_nominal_diameters(model)
    diameter = next(
        dimension
        for group in plan_dimensions(lowered)
        if group.feature is rotational
        for dimension in group.dims
        if dimension.param.parameter_id == "od.diameter"
    )
    assert diameter.suppressed
    assert diameter.reason == "authored PMI dimension dimension:body-od covers this diameter"


def test_noncanonical_member_diameter_heads_its_own_callout():
    members = ((0, -5, 0), (0, 5, 0))
    hole = HoleFeature(Frame(members[0], "x"), 4, depth=20, through=True)
    pattern = PatternFeature(
        Frame((0, 0, 0), "x"),
        "linear",
        2,
        hole,
        members=members,
        pitch=10,
        direction=(0, 1, 0),
    )
    source = AuthoredDimension(
        Frame(members[0], "x"),
        "diameter",
        4,
        "ø4 H7",
        "X",
        ref_pts=(members[0],),
        source_id="dimension:fit",
        cylindrical_refs=(CylindricalReference(members[0], (1, 0, 0), 2, (-10, 10), "internal"),),
    )
    model = lower_ap242_nominal_diameters(
        PartModel(Box(20, 20, 20).bounding_box(), "x", [pattern, source])
    )
    assert not any(isinstance(feature, AuthoredDimension) for feature in model.features)
    owned = next(feature for feature in model.features if isinstance(feature, PatternFeature))
    assert owned.member_size_requirements[0].label == "ø4 H7"
    group = next(
        group for group in plan_dimensions(model) if isinstance(group.feature, PatternFeature)
    )
    member_diameters = [
        dimension
        for dimension in group.dims
        if dimension.param.parameter_id.startswith("bore.diameter.member_")
    ]
    assert [dimension.suppressed for dimension in member_diameters] == [False, False]
    from draftwright.model.callout import bore_callout_value, hole_callout_batches

    batches = hole_callout_batches(plan_dimensions(model))
    assert [bore_callout_value(batch.spec) for batch in batches] == ["4 H7", "4"]
    part = (
        Box(20, 20, 20)
        - Pos(0, -5, 0) * Cylinder(2, 20, rotation=(0, 90, 0))
        - Pos(0, 5, 0) * Cylinder(2, 20, rotation=(0, 90, 0))
    )
    drawing = build_drawing(
        part, model=model, pmi="annotate", page="A3", scale=1, detail_view=False, repair=False
    )
    assert sorted(
        drawing.registry.named(name).label
        for name in drawing.registry.names()
        if name.startswith("hc_")
    ) == ["⌀4 H7 THRU", "⌀4 THRU"]


def test_conflicting_member_labels_do_not_claim_the_same_callout():
    members = ((0, -5, 0), (0, 5, 0))
    hole = HoleFeature(Frame(members[0], "x"), 4, depth=20, through=True)
    pattern = PatternFeature(
        Frame((0, 0, 0), "x"),
        "linear",
        2,
        hole,
        members=members,
        pitch=10,
        direction=(0, 1, 0),
    )
    refs = (CylindricalReference(members[0], (1, 0, 0), 2, (-10, 10), "internal"),)
    sources = [
        AuthoredDimension(
            Frame(members[0], "x"),
            "diameter",
            4,
            label,
            "X",
            ref_pts=(members[0],),
            source_id=f"dimension:{label}",
            cylindrical_refs=refs,
        )
        for label in ("ø4 H7", "ø4 G7")
    ]
    model = lower_ap242_nominal_diameters(
        PartModel(Box(20, 20, 20).bounding_box(), "x", [pattern, *sources])
    )
    owned = next(feature for feature in model.features if isinstance(feature, PatternFeature))
    assert owned.member_size_requirements[0].label == "ø4 H7"
    (unclaimed,) = [
        feature for feature in model.features if isinstance(feature, AuthoredDimension)
    ]
    assert unclaimed.source_id == "dimension:ø4 G7"
    assert any("conflicting authored aspect" in reason for reason in unclaimed.lowering_blockers)


def test_distinct_hole_sources_keep_their_own_labels():
    members = ((0, -5, 0), (0, 5, 0))
    holes = [HoleFeature(Frame(point, "x"), 4, depth=20, through=True) for point in members]
    sources = [
        AuthoredDimension(
            Frame(point, "x"),
            "diameter",
            4,
            label,
            "X",
            ref_pts=(point,),
            source_id=f"dimension:{index}",
            cylindrical_refs=(CylindricalReference(point, (1, 0, 0), 2, (-10, 10), "internal"),),
        )
        for index, (point, label) in enumerate(zip(members, ("ø4 H7", "ø4 G7"), strict=True))
    ]
    model = lower_ap242_nominal_diameters(
        PartModel(Box(20, 20, 20).bounding_box(), "x", [*holes, *sources])
    )
    assert [
        model.decorations[(hole, "nominal_requirement", "bore.diameter")] for hole in holes
    ] == [
        NominalRequirement(4, "ap242_pmi", ("dimension:0",), "ø4 H7"),
        NominalRequirement(4, "ap242_pmi", ("dimension:1",), "ø4 G7"),
    ]


def test_nominal_requirement_refuses_a_label_that_states_another_diameter():
    with pytest.raises(ValueError, match="label must agree"):
        NominalRequirement(4, "ap242_pmi", ("dimension:wrong",), "ø5 H7")
    with pytest.raises(ValueError, match="label must agree"):
        NominalRequirement(4, "ap242_pmi", ("dimension:compound",), "ø4 THRU")


def test_authored_through_bore_label_folds_into_one_matching_callout():
    point = (0, 0, 0)
    hole = HoleFeature(Frame(point, "x"), 4, depth=20, through=True)
    source = AuthoredDimension(
        Frame(point, "x"),
        "diameter",
        4,
        "ø4 THRU",
        "X",
        ref_pts=(point,),
        source_id="dimension:through",
        cylindrical_refs=(CylindricalReference(point, (1, 0, 0), 2, (-10, 10), "internal"),),
    )
    part = Box(20, 20, 20) - Cylinder(2, 20, rotation=(0, 90, 0))
    model = lower_ap242_nominal_diameters(PartModel(part.bounding_box(), "x", [hole, source]))
    drawing = build_drawing(
        part, model=model, pmi="annotate", page="A3", scale=1, detail_view=False, repair=False
    )
    labels = [
        drawing.registry.named(name).label
        for name in drawing.registry.names()
        if name.startswith(("hc_", "pmi_d_"))
    ]
    assert labels == ["⌀4 THRU"]


def test_ambiguous_identical_step_spans_are_not_both_claimed_by_one_source():
    first = replace(_step(0, 15, 30), profile_group="body-a")
    second = replace(_step(0, 15, 30), profile_group="body-b")
    model = _model(_source(0, 15, "dimension:one"))
    model.features[1:3] = [first, second]
    assert [
        dimension.suppressed
        for group in plan_dimensions(model)
        if group.feature is first or group.feature is second
        for dimension in group.dims
        if dimension.param.parameter_id == "step.length"
    ] == [False, False]


def test_authored_chain_does_not_get_misread_as_a_baseline():
    first = replace(_source(0, 15, "dimension:15"), upper_tol=0.1, lower_tol=-0.1)
    second = replace(_source(15, 40, "dimension:25"), upper_tol=0.1, lower_tol=-0.1)
    assert [
        (value, withheld) for value, _span, withheld in _step_lengths(_model(first, second))
    ] == [
        (15, True),
        (25, True),
        (12, False),
    ]


def test_touching_distinct_profiles_do_not_share_an_authored_baseline():
    model = _model(_source(0, 40, "dimension:40"))
    model.features[2] = replace(model.features[2], profile_group="body-b")
    separated = add_authored_step_positions(model)
    assert not any(
        step.position_span is not None
        for step in separated.features
        if isinstance(step, StepFeature)
    )


def test_second_parallel_body_does_not_disable_first_bodys_baseline():
    model = _model(_source(0, 40, "dimension:body-a"))
    siblings = [
        replace(
            _step(start, end, diameter),
            frame=Frame(((start + end) / 2, 50, 0), "x"),
            span=((start, 50, 0), (end, 50, 0)),
            profile_group="body-b",
        )
        for start, end, diameter in ((0, 15, 30), (15, 40, 20), (40, 52, 12))
    ]
    model.features.extend(siblings)
    prepared = add_authored_step_positions(model)
    assert any(
        step.position_span == ((0, 0, 0), (52, 0, 0))
        for step in prepared.features
        if isinstance(step, StepFeature)
    )
    assert all(step.position_span is None for step in prepared.features if step in siblings)


def test_disconnected_coaxial_body_does_not_disable_first_bodys_baseline():
    model = _model(_source(0, 40, "dimension:first-run"))
    distant = [
        _step(start, end, diameter)
        for start, end, diameter in ((100, 115, 30), (115, 140, 20), (140, 152, 12))
    ]
    model.features.extend(distant)
    prepared = add_authored_step_positions(model)
    assert any(
        step.position_span == ((0, 0, 0), (52, 0, 0))
        for step in prepared.features
        if isinstance(step, StepFeature)
    )
    assert all(step.position_span is None for step in prepared.features if step in distant)


def test_one_authored_overall_does_not_leave_a_redundant_complete_chain():
    prepared = add_authored_step_positions(_model(_source(0, 52, "dimension:overall")))
    plan = compile_dimensions(prepared)
    approved = [
        (dimension.parameter_id, dimension.value)
        for group in plan.of_kind("step")
        for dimension in group.dims
        if dimension.kind == "length"
    ]
    assert approved == [("step.length", 15), ("step_position.length", 40)]


def test_no_pmi_keeps_the_original_chain():
    assert [(value, withheld) for value, _span, withheld in _step_lengths(_model())] == [
        (15, False),
        (25, False),
        (12, False),
    ]


@pytest.mark.parametrize("mode", ["off", "report"])
def test_explicit_model_pmi_still_takes_precedence_in_non_annotation_modes(mode):
    part = (
        Pos(7.5, 0, 0) * Cylinder(15, 15, rotation=(0, 90, 0))
        + Pos(27.5, 0, 0) * Cylinder(10, 25, rotation=(0, 90, 0))
        + Pos(46, 0, 0) * Cylinder(6, 12, rotation=(0, 90, 0))
    )
    drawing = build_drawing(
        part,
        model=_model(_source(0, 40, "dimension:report-only")),
        pmi=mode,
        page="A3",
        scale=1,
        detail_view=False,
        repair=False,
    )
    assert drawing.model().pmi_annotations_enabled
    assert any(feature.kind == "authored_dimension" for feature in drawing.model().features)
    assert [(value, withheld) for value, _span, withheld in _step_lengths(drawing.model())] == [
        (15, False),
        (25, True),
        (12, True),
    ]
    assert sorted(
        drawing.registry.named(name).label
        for name in drawing.registry.names()
        if name.startswith(("m_steplen", "pmi_x_"))
    ) == ["15", "40", "52"]


def test_hidden_document_source_does_not_leave_an_inherited_baseline_mark():
    source = _source(0, 40, "dimension:document-source")
    acquired = add_authored_step_positions(_model(source))
    assert any(
        step.position_derived_from_pmi and step.position_span is not None
        for step in acquired.features
        if isinstance(step, StepFeature)
    )
    report_member = replace(acquired, hidden_authored_dimension_ids=frozenset({id(source)}))
    approved = [
        (dimension.param.parameter_id, dimension.param.value)
        for group in plan_dimensions(report_member)
        for dimension in group.dims
        if isinstance(group.feature, StepFeature)
        and dimension.param.kind == "length"
        and not dimension.suppressed
    ]
    assert approved == [("step.length", 15), ("step.length", 25), ("step.length", 12)]
    member_source = _source(0, 40, "dimension:member-source")
    member = replace(
        report_member,
        features=[*report_member.features, member_source],
    )
    assert [
        (dimension.param.parameter_id, dimension.param.value)
        for group in plan_dimensions(member)
        for dimension in group.dims
        if isinstance(group.feature, StepFeature)
        and dimension.param.kind == "length"
        and not dimension.suppressed
    ] == [("step.length", 15), ("step_position.length", 52)]


def test_report_only_detected_pmi_keeps_generated_step_chain(tmp_path):
    fixture = Path(__file__).parent / "fixtures/grm03_thumbwheel_drive_screw_ap242_pmi.step"
    detected = detect_part_model(str(fixture), pmi="report")
    assert any(isinstance(feature, AuthoredDimension) for feature in detected.features)
    script_path = generate_sheet_script(
        str(fixture), out=str(tmp_path / "report.py"), pmi="report", inspect=False, formats=()
    )
    script = Path(script_path).read_text()
    assert [
        line
        for line in script.splitlines()
        if '"step.length"' in line and line.startswith("sheet.dimension(step")
    ] == [f'sheet.dimension(step{index}, "step.length")' for index in range(1, 6)]


def test_unproved_or_off_axis_source_does_not_cover_a_step():
    source = _source(0, 40, "dimension:40")
    for other in (
        replace(source, dominant_axis="Y"),
        replace(source, ref_pts=((0, 0, 0), (39, 0, 0))),
        replace(source, ref_pts=((0, 100, 0), (40, 100, 0))),
        replace(source, rendering_blockers=("unproved",)),
    ):
        assert [(value, withheld) for value, _span, withheld in _step_lengths(_model(other))] == [
            (15, False),
            (25, False),
            (12, False),
        ]


def test_axial_lint_rejects_a_transverse_source_with_same_end_stations():
    source = replace(
        _source(0, 15, "dimension:transverse"),
        dominant_axis="Y",
        value=10,
        label="10",
        ref_pts=((0, -5, 0), (15, 5, 0)),
    )
    drawing = SimpleNamespace(registry=SimpleNamespace(feature_of=lambda name: source))
    profile = SimpleNamespace(axis="x", steps=[SimpleNamespace(lo=0, hi=15, diameter=30)])
    assert not _authored_axial_witness_on_profile(drawing, "source", profile, (0, 0, 0), 0.6)


def test_exact_imported_overall_extent_replaces_automatic_envelope_width():
    source = replace(
        _source(0, 52, "dimension:overall"),
        ref_pts=((0, -15, -15), (52, -15, -15)),
    )
    model = _model(source)
    width = next(
        dimension
        for group in plan_dimensions(model)
        for dimension in group.dims
        if dimension.param.parameter_id == "width.length"
    )
    assert width.suppressed
    assert width.reason == "authored PMI dimension dimension:overall covers this measurement"


def test_offset_face_witnesses_replace_automatic_envelope_width():
    bbox = (Pos(10, 5, 2.5) * Box(20, 10, 5)).bounding_box()
    envelope = EnvelopeFeature(Frame((10, 5, 2.5), "x"), 20, 5, 10, (0, 0, 0), (20, 10, 5))
    source = AuthoredDimension(
        Frame((10, 5, 2.5), "x"),
        "linear",
        20,
        "20",
        "X",
        ref_pts=((0, 5, 2.5), (20, 5, 2.5)),
        source_id="dimension:face-width",
    )
    plan = plan_dimensions(PartModel(bbox, "x", [envelope, source]))
    width = next(
        dimension
        for group in plan
        if group.feature is envelope
        for dimension in group.dims
        if dimension.param.parameter_id == "width.length"
    )
    assert width.suppressed
    assert width.reason == "authored PMI dimension dimension:face-width covers this extent"


def test_authored_overall_baseline_does_not_restore_a_duplicate_envelope_width():
    source = _source(0, 52, "dimension:overall")
    model = _model(source)
    width = next(
        dimension
        for group in plan_dimensions(model)
        for dimension in group.dims
        if dimension.param.parameter_id == "width.length"
    )
    assert width.suppressed
    assert width.reason == "authored PMI dimension dimension:overall covers this extent"


def test_authored_z_height_does_not_reappear_as_an_overall_ladder():
    bbox = (Pos(5, 5, 5) * Box(10, 10, 10)).bounding_box()
    envelope = EnvelopeFeature(Frame((5, 5, 5), "z"), 10, 10, 10, (0, 0, 0), (10, 10, 10))
    source = AuthoredDimension(
        Frame((10, 0, 5), "z"),
        "linear",
        10,
        "10",
        "Z",
        ref_pts=((10, 0, 0), (10, 0, 10)),
        source_id="dimension:height",
    )
    model = PartModel(bbox, "z", [envelope, source])
    plan = compile_dimensions(model)
    assert plan.ladder("overall_height") is None
    assert any(
        omission.parameter_id == "height.length" and "dimension:height" in omission.reason
        for omission in plan.diagnostics
    )


def test_authored_turned_overall_covers_bbox_height_without_an_envelope():
    bbox = (Pos(0, 0, 26) * Box(24, 24, 52)).bounding_box()
    steps = [
        StepFeature(Frame((0, 0, (lo + hi) / 2), "z"), hi - lo, diameter, ((0, 0, lo), (0, 0, hi)))
        for lo, hi, diameter in ((0, 12, 24), (12, 34, 15), (34, 52, 10))
    ]
    source = AuthoredDimension(
        Frame((0, 0, 26), "z"),
        "linear",
        52,
        "52 +0.1/0",
        "Z",
        ref_pts=((0, 0, 0), (0, 0, 52)),
        source_id="dimension:overall",
    )
    model = PartModel(bbox, "z", [*steps, source])
    plan = compile_dimensions(model)
    assert plan.ladder("overall_height") is None
    assert any(
        omission.parameter_id == "height.length" and "dimension:overall" in omission.reason
        for omission in plan.diagnostics
    )
    unrelated = compile_dimensions(
        replace(model, features=[*steps, replace(source, ref_pts=((20, 0, 0), (20, 0, 52)))])
    )
    assert not any("dimension:overall" in omission.reason for omission in unrelated.diagnostics)


def test_authored_hole_x_location_covers_only_the_x_component():
    bbox = (Pos(20, 15, 2.5) * Box(40, 30, 5)).bounding_box()
    hole = HoleFeature(Frame((10, 5, 2), "z"), 4, depth=None, through=True)
    source = AuthoredDimension(
        Frame((5, 5, 2), "x"),
        "linear",
        10,
        "10",
        "X",
        ref_pts=((0, 5, 2), (10, 5, 2)),
        source_id="dimension:hole-x",
    )
    model = PartModel(bbox, "z", [hole, source], datums=[Datum("datum_xy", "point", (0, 0, 0))])
    plan = compile_dimensions(model)
    hole_locations = [location for location in plan.locations if location.id.feature is hole]
    assert [(location.discriminator, location.value) for location in hole_locations] == [("y", 5)]
    assert any(
        omission.feature is hole
        and omission.parameter_id.endswith(".x")
        and "dimension:hole-x" in omission.reason
        for omission in plan.diagnostics
    )
    overlapping_hole = replace(hole, diameter=6)
    ambiguous = compile_dimensions(replace(model, features=[hole, overlapping_hole, source]))
    assert (
        len([location for location in ambiguous.locations if location.discriminator == "x"]) == 2
    )


def test_authored_bore_axis_station_covers_only_its_physical_hole_location():
    bbox = (Pos(0, 0, 6) * Box(80, 90, 12)).bounding_box()
    deep = HoleFeature(Frame((10, 5, 12), "z"), 4, depth=12, through=True)
    shallow = HoleFeature(Frame((10, 5, 12), "z"), 6, depth=4, through=False)
    source = AuthoredDimension(
        Frame((-7.5, 5, 6), "x"),
        "linear",
        35,
        "35",
        "X",
        ref_pts=((-25, 5, 6), (10, 5, 6)),
        source_id="dimension:bore-x",
    )
    model = PartModel(
        bbox,
        "z",
        [deep, shallow, source],
        datums=[Datum("datum_xy", "point", (-25, -40, 0))],
    )
    plan = compile_dimensions(model)
    assert [(d.id.feature, d.discriminator) for d in plan.locations] == [
        (deep, "y"),
        (shallow, "x"),
        (shallow, "y"),
    ]
    assert any(
        omission.feature is deep
        and omission.parameter_id.endswith(".x")
        and "dimension:bore-x" in omission.reason
        for omission in plan.diagnostics
    )
    wrong_bore = compile_dimensions(
        replace(
            model, features=[deep, shallow, replace(source, ref_pts=((-25, 6, 6), (10, 6, 6)))]
        )
    )
    assert len([d for d in wrong_bore.locations if d.discriminator == "x"]) == 2
    shallow_x = next(
        d for d in plan.locations if d.id.feature is shallow and d.discriminator == "x"
    )
    assert not _authored_bore_axis_location_matches(source, replace(shallow_x, discriminator="z"))
    assert not _authored_bore_axis_location_matches(
        source, replace(shallow_x, span=((-25, 5, 12), (20, 5, 12)))
    )


def test_authored_central_bore_axis_covers_coaxial_pattern_centre_location():
    bbox = (Pos(0, 0, 6) * Box(120, 80, 12)).bounding_box()
    member = HoleFeature(Frame((0, 0, 12), "z"), 6.6, depth=12, through=True)
    pattern = PatternFeature(
        Frame((0, 0, 12), "z"),
        "grid",
        4,
        member,
        members=((-48, -30, 12), (48, -30, 12), (48, 30, 12), (-48, 30, 12)),
        grid=(60, 96),
        rows=2,
        cols=2,
    )
    bore = HoleFeature(Frame((0, 0, 12), "z"), 22, depth=12, through=True)
    source = AuthoredDimension(
        Frame((-30, 0, 4), "x"),
        "linear",
        60,
        "60",
        "X",
        ref_pts=((-60, 0, 4), (0, 0, 4)),
        source_id="dimension:bore-centre",
        basic=True,
    )
    model = PartModel(
        bbox,
        "z",
        [pattern, bore, source],
        datums=[Datum("datum_xy", "point", (-60, -40, 0))],
    )
    plan = compile_dimensions(model)
    assert not [
        location
        for location in plan.locations
        if location.id.feature is pattern and location.discriminator == "x"
    ]
    assert any(
        omission.feature is pattern and "dimension:bore-centre" in omission.reason
        for omission in plan.diagnostics
    )
    without_bore = compile_dimensions(replace(model, features=[pattern, source]))
    assert any(
        location.id.feature is pattern and location.discriminator == "x"
        for location in without_bore.locations
    )


def test_pattern_member_provenance_is_per_measurement():
    hole = HoleFeature(Frame((0, 0, 0), "z"), 4, depth=None, through=True)
    pattern = PatternFeature(
        Frame((0, 5, 0), "z"),
        "linear",
        2,
        hole,
        members=((0, 0, 0), (0, 10, 0)),
        pitch=10,
        direction=(0, 1, 0),
        member_size_requirements=(
            NominalRequirement(4, "ap242_pmi", ("dimension:first-member",)),
            None,
        ),
    )
    model = PartModel(Box(20, 20, 5).bounding_box(), "z", [pattern])
    registry = AnnotationRegistry()
    registry.add(
        object(),
        "hole_terms",
        "plan",
        feature=pattern,
        measurement=(
            DimensionId(pattern, "bore.diameter.member_0"),
            DimensionId(pattern, "bore.diameter.member_1"),
        ),
    )
    rows = [
        row
        for row in placed_dimension_sources(model, registry)
        if row["annotation"] == "hole_terms"
    ]
    assert [(row["parameter_id"], row["source_ids"]) for row in rows] == [
        ("bore.diameter.member_0", ["dimension:first-member"]),
        ("bore.diameter.member_1", []),
    ]


def test_manual_source_is_not_misreported_as_ap242_and_missing_model_refuses():
    step = _step(0, 15, 30)
    model = PartModel(
        Box(15, 30, 30).bounding_box(),
        "x",
        [step],
        decorations={
            (step, "length", "step"): ToleranceDecoration(0.1, "authored", ("manual-1",)),
            (step, "length"): ToleranceDecoration(0.2, "ap242_pmi", ("shadowed",)),
        },
    )
    registry = AnnotationRegistry()
    registry.add(
        object(),
        "step_length",
        "front",
        feature=step,
        measurement=DimensionId(step, "step.length"),
    )
    (row,) = placed_dimension_sources(model, registry)
    assert row["origin"] == "authored"
    assert row["sources"] == [{"origin": "authored", "source_id": "manual-1"}]
    with pytest.raises(ReportUnavailableError, match="needs a built PartModel"):
        placed_dimension_sources(None, registry)


def test_finished_turned_drawing_has_one_plain_mark_per_baseline_station():
    part = (
        Pos(7.5, 0, 0) * Cylinder(15, 15, rotation=(0, 90, 0))
        + Pos(27.5, 0, 0) * Cylinder(10, 25, rotation=(0, 90, 0))
        + Pos(46, 0, 0) * Cylinder(6, 12, rotation=(0, 90, 0))
    )
    model = add_authored_step_positions(
        lower_ap242_nominal_step_lengths(
            _model(_source(0, 15, "dimension:15"), _source(0, 40, "dimension:40"))
        )
    )
    drawing = build_drawing(
        part, model=model, pmi="annotate", page="A3", scale=1, detail_view=False, repair=False
    )
    labels = [
        drawing.registry.named(name).label
        for name in drawing.registry.names()
        if name.startswith(("m_steplen", "pmi_x_"))
    ]
    assert sorted(labels) == ["15", "40", "52"]
    assert not [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]
    sources = {
        row["annotation"]: row
        for row in drawing.dimension_sources()
        if row["annotation"].startswith(("m_steplen", "pmi_x_"))
    }
    assert {
        (row["parameter_id"], row["origin"], tuple(row["source_ids"])) for row in sources.values()
    } == {
        ("step.length", "ap242_pmi", ("dimension:15",)),
        (None, "ap242_pmi", ("dimension:40",)),
        ("step_position.length", "planner", ()),
    }


def test_direct_part_model_with_imported_pmi_gets_baseline_gap_preparation():
    part = (
        Pos(7.5, 0, 0) * Cylinder(15, 15, rotation=(0, 90, 0))
        + Pos(27.5, 0, 0) * Cylinder(10, 25, rotation=(0, 90, 0))
        + Pos(46, 0, 0) * Cylinder(6, 12, rotation=(0, 90, 0))
    )
    drawing = build_drawing(
        part,
        model=_model(_source(0, 40, "dimension:40")),
        pmi="annotate",
        page="A3",
        scale=1,
        detail_view=False,
        repair=False,
    )
    assert any(
        step.position_span == ((0, 0, 0), (52, 0, 0))
        for step in drawing.model().features
        if isinstance(step, StepFeature)
    )


def test_baseline_gap_replays_with_a_distinct_step_position_identity():
    model = add_authored_step_positions(_model(_source(0, 40, "dimension:40")))
    source = emit_sheet_script(
        model,
        "from build123d import Box, Pos\npart = Pos(26, 0, 0) * Box(52, 30, 30)",
        "baseline",
        title="Baseline",
        number="B-1",
        formats=(),
    )
    assert "position_span=((0, 0, 0), (52, 0, 0))" in source
    assert 'sheet.dimension(step3, "step_position.length")' in source
    namespace = {}
    exec(compile(source.split("# ── Build")[0], "<baseline script>", "exec"), namespace)
    replayed = namespace["sheet"].model()
    position = next(
        step.position_span
        for step in replayed.features
        if isinstance(step, StepFeature) and step.position_span is not None
    )
    assert position == ((0, 0, 0), (52, 0, 0))


def test_source_basic_locations_precede_generated_grid_pitch_issue_2192():
    """The real Specify AP242 plate keeps three source 40s and both grid pitches."""

    drawing = build_drawing(
        _SPECIFY_PLATE_2192,
        pmi="annotate",
        out=None,
        page="A1",
        scale=1,
        scale_policy="permissive",
        repair=False,
    )
    authored = {
        feature.source_id: feature
        for feature in drawing.model().features
        if feature.kind == "authored_dimension" and feature.source_id in _PLATE_SOURCE_40_IDS
    }

    assert set(authored) == _PLATE_SOURCE_40_IDS
    for feature in authored.values():
        ink = drawing.annotations_of(feature)
        assert len(ink) == 1
        assert next(iter(ink.values())).label == "40"
    assert {"dim_pitch_plan0_0", "dim_pitch_plan0_1"} <= set(drawing.annotations())
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code
        in {
            "hole_pattern_dim_dropped",
            "annotation_overlap",
            "annotation_ink_overlap",
        }
        or (issue.code == "pmi_dropped" and "'40'" in issue.message)
    ]
