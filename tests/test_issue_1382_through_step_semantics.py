"""#1382 — through-step records have one complete, auditable Draftwright path."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from b123d_recognisers import BossRecord, PolygonalStock, TurnedProfile, build_recognition_result
from b123d_recognisers import Plate as RecognisedPlate
from build123d import Align, Box, Compound, Pos, Rot

from draftwright import Sheet, build_drawing
from draftwright.linting.ink_overlap import segments_of
from draftwright.linting.issues import LintIssue
from draftwright.linting.through_step_coverage import (
    lint_through_step_coverage,
    through_step_requirement_outcomes,
)
from draftwright.model import Frame, ThroughStepFeature, envelope, plate, step_level, through_step
from draftwright.model.compiled import (
    ApprovedDimension,
    ApprovedLadder,
    DimensionId,
    RenderableDimensionPlan,
    compile_dimensions,
)
from draftwright.model.detect import build_part_model
from draftwright.registry import AnnotationRegistry
from draftwright.sheet_emit import _feature_line, emit_sheet_script


def _through_step_part():
    return Box(40, 30, 20) - Pos(15, 10, 0) * Box(20, 20, 30)


def _crowded_step_part():
    """A narrow staircase whose omitted source rungs request an enlarged detail."""
    part = Pos(0, 0, 3) * Box(20, 16, 6)
    z = 6
    for width in (16, 13, 10, 7, 5):
        part += Pos(0, 0, z + 1.5) * Box(width, 12, 3)
        z += 3
    return part


def _feature(drawing) -> ThroughStepFeature:
    return next(feature for feature in drawing.model().features if feature.kind == "through_step")


def _dimension_names(drawing) -> list[str]:
    return sorted(name for name in drawing.annotations() if name.startswith("dim_through_step_"))


def _parameter_ids(feature) -> tuple[str, str]:
    return tuple(parameter.parameter_id for parameter in feature.parameters())


def _assert_final_witness_span(drawing, name, feature, parameter_id) -> None:
    parameter = next(p for p in feature.parameters() if p.parameter_id == parameter_id)
    view = drawing.registry.identity_of(name)["view"]
    projected = [drawing.at(view, *point)[:2] for point in parameter.span]
    axis = 0 if projected[0][0] != projected[1][0] else 1
    witness_segments = segments_of(drawing.registry.named(name))[-2:]
    assert len(witness_segments) == 2
    assert sorted(segment[0][axis] for segment in witness_segments) == pytest.approx(
        sorted(point[axis] for point in projected)
    )
    perpendicular = 1 - axis
    for point in projected:
        witness = next(
            segment
            for segment in witness_segments
            if segment[0][axis] == pytest.approx(point[axis])
        )
        feature_side = min(
            witness,
            key=lambda endpoint: (endpoint[0] - point[0]) ** 2 + (endpoint[1] - point[1]) ** 2,
        )
        assert feature_side[axis] == pytest.approx(point[axis])
        assert abs(feature_side[perpendicular] - point[perpendicular]) == pytest.approx(
            drawing.draft.extension_gap
        )


def test_aggregate_record_lowers_exactly_to_two_planned_leg_requirements() -> None:
    drawing = build_drawing(_through_step_part())
    recognition = drawing.recognition()
    assert recognition is not None
    assert len(recognition.through_steps) == 1
    source = recognition.through_steps[0]
    feature = _feature(drawing)

    assert (feature.axis, feature.length, feature.frame.origin, feature.section) == (
        source.axis,
        source.length,
        source.at,
        source.section,
    )
    assert set(_parameter_ids(feature)) == {
        "through_step_leg.length.x",
        "through_step_leg.length.y",
    }
    group = next(iter(compile_dimensions(drawing.model()).of_kind("through_step")))
    assert group.view == "plan"
    assert {dimension.id.parameter for dimension in group.dims} == set(_parameter_ids(feature))
    assert len(_dimension_names(drawing)) == 2
    for name in _dimension_names(drawing):
        identity = drawing.registry.identity_of(name)
        assert identity["view"] == "plan"
        assert len(identity["measurement"]) == 1
        measurement = identity["measurement"][0]
        assert measurement.feature is feature
        parameter = next(
            p for p in feature.parameters() if p.parameter_id == measurement.parameter
        )
        _assert_final_witness_span(drawing, name, feature, parameter.parameter_id)
    assert not [issue for issue in drawing.lint() if "through_step" in issue.code]


@pytest.mark.parametrize(
    ("x", "y"),
    [(15, 7), (15, -7), (-15, 7), (-15, -7)],
    ids=["upper-right", "lower-right", "upper-left", "lower-left"],
)
def test_every_missing_corner_places_both_unequal_legs_with_its_own_value(x, y) -> None:
    drawing = build_drawing(Box(40, 30, 20) - Pos(x, y, 0) * Box(20, 20, 30))
    feature = _feature(drawing)
    expected = {parameter.parameter_id: parameter.value for parameter in feature.parameters()}
    names = _dimension_names(drawing)

    assert sorted(expected.values()) == pytest.approx([15, 18])
    assert len(names) == 2
    for name in names:
        (measurement,) = drawing.registry.measurement_of(name)
        assert float(str(drawing.registry.named(name).label)) == pytest.approx(
            expected[measurement.parameter]
        )
        _assert_final_witness_span(drawing, name, feature, measurement.parameter)
    assert not [issue for issue in drawing.lint() if "through_step" in issue.code]


@pytest.mark.parametrize("x", [15, -15], ids=["lower-right", "lower-left"])
def test_full_natural_lower_strip_retries_opposite_before_dropping(x) -> None:
    drawing = build_drawing(Box(40, 30, 20) - Pos(x, -10, 0) * Box(20, 20, 30))

    assert len(_dimension_names(drawing)) == 2
    assert not [issue for issue in drawing.lint() if "through_step" in issue.code]


@pytest.mark.parametrize("translation", [(7, 0, 0), (0, 7, 0)], ids=["parallel", "perpendicular"])
def test_final_witness_assertion_detects_each_displacement_axis(translation) -> None:
    drawing = build_drawing(_through_step_part())
    feature = _feature(drawing)
    name = next(
        candidate
        for candidate in _dimension_names(drawing)
        if drawing.registry.measurement_of(candidate)[0].parameter.endswith(".x")
    )
    (measurement,) = drawing.registry.measurement_of(name)
    drawing.registry.named(name).position = translation

    with pytest.raises(AssertionError):
        _assert_final_witness_span(drawing, name, feature, measurement.parameter)


def test_detected_build_consumes_the_aggregate_without_rescanning(monkeypatch) -> None:
    import draftwright.model.detect as detect

    def forbidden_scan(_part):
        raise AssertionError("through-step family was rescanned outside the aggregate")

    monkeypatch.setattr(detect, "recognise_through_steps", forbidden_scan)
    assert _feature(build_drawing(_through_step_part())).length == 20


def test_explicit_declaration_and_generated_line_round_trip_every_contract_field() -> None:
    declared = through_step(
        axis="z",
        length=20,
        at=(12.5, 7.5, 0),
        section=((5, 15), (5, 0), (20, 0)),
    )
    line = _feature_line(declared)
    assert line == (
        'sheet.through_step(axis="z", length=20, at=(12.5, 7.5, 0), '
        "section=((5, 15), (5, 0), (20, 0)))"
    )
    namespace = {"sheet": type("S", (), {"through_step": staticmethod(through_step)})()}
    assert eval(line, {"__builtins__": {}}, namespace) == declared  # noqa: S307


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"length": True}, "positive"),
        ({"length": 0}, "positive"),
        ({"section": ((5, 15), (5, 0))}, "three finite"),
        ({"section": ((5, True), (5, 0), (20, 0))}, "three finite"),
        ({"section": ((5, 15), (5, 0), (5, -3))}, "orthogonal"),
        ({"section": ((5, 15), (5, 0), (20, float("nan")))}, "finite"),
    ],
)
def test_declaration_rejects_invalid_measurements(kwargs, message) -> None:
    values = {
        "axis": "z",
        "length": 20,
        "at": (12.5, 7.5, 0),
        "section": ((5, 15), (5, 0), (20, 0)),
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        through_step(**values)


def test_ir_rejects_axis_frame_disagreement() -> None:
    with pytest.raises(ValueError, match="agree with the feature frame"):
        ThroughStepFeature(
            Frame((12.5, 7.5, 0), "x"),
            "z",
            20,
            ((5, 15), (5, 0), (20, 0)),
        )

    with pytest.raises(ValueError, match="three finite"):
        ThroughStepFeature(
            Frame((12.5, 7.5, 0), "z"),
            "z",
            20,
            ((5, True), (5, 0), (20, 0)),
        )


@pytest.mark.parametrize(
    ("part", "axis", "view"),
    [
        (Rot(0, 90, 0) * _through_step_part(), "x", "side"),
        (
            Rot(90, 0, 0) * (Box(40, 30, 20) - Pos(15, -10, 0) * Box(20, 20, 30)),
            "y",
            "front",
        ),
    ],
)
def test_aggregate_through_step_precedes_matching_legacy_fragments(part, axis, view) -> None:
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None and recognition.through_steps[0].axis == axis
    feature = _feature(drawing)
    assert feature.axis == axis
    assert not [
        candidate
        for candidate in drawing.model().features
        if candidate.kind in {"step_level", "plate"}
    ]
    assert len(_dimension_names(drawing)) == 2
    assert {drawing.registry.identity_of(name)["view"] for name in _dimension_names(drawing)} == {
        view
    }
    outcomes = through_step_requirement_outcomes(
        recognition, drawing.model().features, drawing.registry
    )
    assert [outcome.state for outcome in outcomes] == ["placed", "placed"]
    for name in _dimension_names(drawing):
        (measurement,) = drawing.registry.measurement_of(name)
        _assert_final_witness_span(drawing, name, feature, measurement.parameter)
    assert not [issue for issue in drawing.lint() if "through_step" in issue.code]


def test_mixed_axis_aggregate_scores_every_physical_leg() -> None:
    base = _through_step_part()
    part = Compound([Pos(-70, 0, 0) * base, Pos(70, 0, 0) * Rot(0, 90, 0) * base])
    drawing = build_drawing(part)
    outcomes = through_step_requirement_outcomes(
        drawing.recognition(), drawing.model().features, drawing.registry
    )

    assert [source.axis for source in drawing.recognition().through_steps] == ["x", "z"]
    assert [
        feature.axis for feature in drawing.model().features if feature.kind == "through_step"
    ] == ["x", "z"]
    assert [outcome.state for outcome in outcomes] == ["placed"] * 4


def test_mixed_legacy_and_aggregate_ownership_reaches_a_fixed_point() -> None:
    base = Rot(90, 0, 0) * _through_step_part()
    drawing = build_drawing(Compound([Pos(-70, 0, 0) * base, Pos(70, 0, 0) * base]))
    features = [feature for feature in drawing.model().features if feature.kind == "through_step"]
    outcomes = through_step_requirement_outcomes(
        drawing.recognition(), drawing.model().features, drawing.registry
    )

    assert len(drawing.recognition().through_steps) == 2  # type: ignore[union-attr]
    assert len(features) == 2
    assert [outcome.state for outcome in outcomes] == ["placed"] * 4


def test_fixed_point_reprojects_shoulders_after_an_owned_level_disappears() -> None:
    part = Rot(90, 0, 0) * _through_step_part()
    recognition = build_recognition_result(part)
    source = recognition.through_steps[0]
    aggregate_owned = replace(
        source,
        at=(11, 0, 7.5),
        section=((2, 15), (2, 0), (20, 0)),
    )
    initially_legacy_owned = replace(
        source,
        at=(-12.5, 0, 10),
        section=((-5, 15), (-5, 5), (-20, 5)),
    )
    riser = replace(recognition.risers[0], positions=(-5,))
    model = build_part_model(
        part,
        through_steps=(aggregate_owned, initially_legacy_owned),
        step_zs=(0, 5),
        face_levels=(),
        risers=(riser,),
        plates=(),
        pockets=(),
        prof=None,
        rotational=None,
    )

    # The first aggregate removes level 0. The remaining level 5 no longer supports the
    # riser's foot at 0, so the second aggregate must be promoted rather than trusting a
    # shoulder that will disappear from the emitted StepLevelFeature.
    assert [feature.kind for feature in model.features].count("through_step") == 2
    assert not [feature for feature in model.features if feature.kind == "step_level"]


def test_standalone_model_emits_the_legacy_owner_that_preempts_the_aggregate() -> None:
    part = Rot(90, 0, 0) * _through_step_part()
    automatic = build_drawing(part).model()
    standalone = build_part_model(part)

    automatic_owners = tuple(
        feature for feature in automatic.features if feature.kind in {"step_level", "through_step"}
    )
    standalone_owners = tuple(
        feature
        for feature in standalone.features
        if feature.kind in {"step_level", "through_step"}
    )

    assert standalone_owners == automatic_owners
    assert [feature.kind for feature in standalone_owners] == ["step_level"]
    ladders = compile_dimensions(standalone).ladders
    assert {ladder.kind for ladder in ladders} >= {"step_height", "step_position"}


def test_only_emittable_plates_may_preempt_the_aggregate_owner() -> None:
    part = Rot(90, 0, 0) * _through_step_part()
    source = build_recognition_result(part).through_steps[0]
    model = build_part_model(
        part,
        through_steps=(source,),
        step_zs=(0,),
        face_levels=(),
        risers=(),
        plates=(RecognisedPlate(axis="x", lo=5, hi=20, u=0, v=0),),
        prof=None,
        rotational=None,
    )

    owners = [
        feature
        for feature in model.features
        if feature.kind in {"through_step", "step_level", "plate"}
    ]
    assert [feature.kind for feature in owners] == ["through_step"]
    assert len(tuple(compile_dimensions(model).of_kind("through_step"))) == 1


def test_edge_pocket_floor_cannot_preempt_then_erase_the_aggregate_owner() -> None:
    base = Rot(90, 0, 0) * _through_step_part()
    edge_pocket = Pos(-20, -10, 0) * Box(
        8,
        6,
        15,
        align=(Align.MIN, Align.MIN, Align.MIN),
    )
    part = base - edge_pocket
    recognition = build_recognition_result(part)
    drawing = build_drawing(part)
    outcomes = through_step_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
    )

    assert len(recognition.through_steps) == 1
    assert [feature.kind for feature in drawing.model().features].count("through_step") == 1
    assert [outcome.state for outcome in outcomes] == ["placed", "placed"]
    assert not [issue for issue in drawing.lint() if "through_step" in issue.code]


def test_base_plate_filter_cannot_erase_the_only_transverse_shoulder_owner() -> None:
    part = Rot(90, 0, 0) * _through_step_part()
    recognition = build_recognition_result(part)
    model = build_part_model(
        part,
        through_steps=recognition.through_steps,
        step_zs=tuple(level.z for level in recognition.step_levels),
        face_levels=recognition.step_levels,
        risers=recognition.risers,
        plates=(
            RecognisedPlate(axis="z", lo=-15, hi=0, u=0, v=0),
            RecognisedPlate(axis="y", lo=-10, hi=-5, u=0, v=0),
        ),
        pockets=(),
        prof=None,
        rotational=None,
    )

    owners = [
        feature
        for feature in model.features
        if feature.kind in {"through_step", "step_level", "plate"}
    ]
    assert [feature.kind for feature in owners].count("through_step") == 1
    assert not [feature for feature in owners if feature.kind == "step_level"]
    assert len(tuple(compile_dimensions(model).of_kind("through_step"))) == 1


def test_injected_turned_classification_cannot_hide_a_supplied_aggregate() -> None:
    part = Rot(90, 0, 0) * _through_step_part()
    recognition = build_recognition_result(part)
    model = build_part_model(
        part,
        through_steps=recognition.through_steps,
        step_zs=tuple(level.z for level in recognition.step_levels),
        face_levels=recognition.step_levels,
        risers=recognition.risers,
        plates=(),
        pockets=(),
        prof=TurnedProfile("z", ()),
        rotational=None,
    )

    assert [feature.kind for feature in model.features].count("through_step") == 1
    assert not [feature for feature in model.features if feature.kind == "step_level"]
    assert len(tuple(compile_dimensions(model).of_kind("through_step"))) == 1


@pytest.mark.parametrize("suppressor", ["round-boss", "polygonal-stock"])
def test_complement_owner_requires_an_emitted_envelope(suppressor) -> None:
    part = Rot(90, 0, 0) * (Box(20, 30, 20) - Pos(7.5, 10, 0) * Box(10, 20, 30))
    recognition = build_recognition_result(part)
    kwargs = {
        "bosses": (),
        "polygonal_stock": (),
    }
    if suppressor == "round-boss":
        kwargs["bosses"] = (
            BossRecord(axis=(0, 0, 1), location=(0, 0, 0), diameter=20, height=30),
        )
    else:
        kwargs["polygonal_stock"] = (
            PolygonalStock(
                axis="z",
                center=(0, 0, 0),
                side_count=4,
                across_flats=20,
                base=-15,
                top=15,
                flat_directions=((1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)),
                flat_centres=((10, 0, 0), (0, 10, 0), (-10, 0, 0), (0, -10, 0)),
            ),
        )
    model = build_part_model(
        part,
        through_steps=recognition.through_steps,
        step_zs=tuple(level.z for level in recognition.step_levels),
        face_levels=recognition.step_levels,
        risers=recognition.risers,
        plates=(),
        pockets=(),
        prof=None,
        rotational=None,
        **kwargs,
    )

    assert not [feature for feature in model.features if feature.kind == "envelope"]
    assert [feature.kind for feature in model.features].count("through_step") == 1
    assert len(tuple(compile_dimensions(model).of_kind("through_step"))) == 1


def test_direct_min_datum_owners_do_not_require_an_envelope() -> None:
    part = Rot(90, 0, 0) * (Box(20, 30, 20) - Pos(7.5, 10, 0) * Box(10, 20, 30))
    recognition = build_recognition_result(part)
    direct_source = replace(
        recognition.through_steps[0],
        at=(-6.25, 0, -7.5),
        section=((-2.5, 0), (-2.5, -15), (-10, -15)),
    )
    riser = replace(recognition.risers[0], positions=(-2.5,))
    model = build_part_model(
        part,
        through_steps=(direct_source,),
        step_zs=(0,),
        face_levels=(),
        risers=(riser,),
        plates=(),
        pockets=(),
        bosses=(BossRecord(axis=(0, 0, 1), location=(0, 0, 0), diameter=20, height=30),),
        polygonal_stock=(),
        prof=None,
        rotational=None,
    )

    assert not [feature for feature in model.features if feature.kind == "envelope"]
    assert not [feature for feature in model.features if feature.kind == "through_step"]
    assert [feature.kind for feature in model.features].count("step_level") == 1


def test_inapplicable_requires_coordinate_proof_of_both_legacy_legs() -> None:
    drawing = build_drawing(Rot(90, 0, 0) * _through_step_part())
    recognition = drawing.recognition()
    assert recognition is not None
    assert not [feature for feature in drawing.model().features if feature.kind == "through_step"]
    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition, drawing.model().features, drawing.registry
        )
    ] == ["inapplicable", "inapplicable"]

    incomplete = tuple(
        replace(feature, shoulders=()) if feature.kind == "step_level" else feature
        for feature in drawing.model().features
    )
    assert {
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition, incomplete, AnnotationRegistry()
        )
    } == {"unverifiable"}

    step = next(feature for feature in drawing.model().features if feature.kind == "step_level")
    drawing.drop(step)
    assert {
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition, drawing.model().features, drawing.registry
        )
    } == {"missing"}
    assert {
        issue.code for issue in drawing.lint() if issue.code.startswith("through_step_requirement")
    } == {"through_step_requirement_missing"}


@pytest.mark.parametrize("run_shift", [0, 1], ids=["identical-record", "distinct-anchor"])
def test_ambiguous_legacy_sources_fail_closed_instead_of_reusing_one_owner(run_shift) -> None:
    drawing = build_drawing(Rot(90, 0, 0) * _through_step_part())
    recognition = drawing.recognition()
    assert recognition is not None
    assert [
        feature.kind for feature in drawing.model().features if feature.kind == "step_level"
    ] == ["step_level"]
    source = recognition.through_steps[0]
    competing = replace(
        source,
        at=(source.at[0], source.at[1] + run_shift, source.at[2]),
    )
    ambiguous = replace(
        recognition,
        through_steps=(source, competing),
    )

    plan = compile_dimensions(drawing.model())
    outcomes = through_step_requirement_outcomes(
        ambiguous,
        drawing.model().features,
        drawing.registry,
        plan.diagnostics,
        plan=plan,
    )

    assert len(outcomes) == 4
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}
    issues = lint_through_step_coverage(
        drawing.part,
        recognition=ambiguous,
        features=drawing.model().features,
        registry=drawing.registry,
        omissions=plan.diagnostics,
        plan=plan,
    )
    assert len(issues) == 4
    assert {issue.code for issue in issues} == {"through_step_requirement_unverifiable"}


def test_authored_empty_legacy_projection_records_suppression_not_inapplicable() -> None:
    sheet = Sheet.from_part(Rot(90, 0, 0) * _through_step_part())
    sheet.take_over(
        dimensions="authored",
        principal_views="automatic",
        derived_views="automatic",
    )
    drawing = sheet.build()
    summary = drawing.lint_summary()
    outcomes = through_step_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
        plan=compile_dimensions(drawing.model()),
    )

    assert [outcome.state for outcome in outcomes] == ["suppressed", "suppressed"]
    completeness = summary["quality"]["completeness"]
    assert completeness["requirements"] == completeness["suppressed"] == 2
    assert completeness["inapplicable"] == 0


def test_unrelated_declared_legacy_dimensions_cannot_cover_physical_legs() -> None:
    part = Rot(90, 0, 0) * _through_step_part()
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.step_level(
        base=-15,
        levels=(-5,),
        shoulders=(("x", 10),),
        datum=(-20, -10, -15),
    )
    for parameter in sheet.features[-1].parameters():
        sheet.dimension(handle, parameter.parameter_id)
    drawing = sheet.build()

    issues = drawing.lint()
    outcomes = through_step_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
        plan=compile_dimensions(drawing.model()),
    )

    assert [outcome.state for outcome in outcomes] == ["unverifiable", "unverifiable"]
    assert {issue.code for issue in issues if "through_step" in issue.code} == {
        "through_step_requirement_unverifiable"
    }
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == completeness["unverifiable"] == 2
    assert completeness["inapplicable"] == 0


def test_remaining_ladder_rungs_cannot_cover_removed_exact_legacy_members() -> None:
    part = Rot(90, 0, 0) * _through_step_part()
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.step_level(
        base=0,
        levels=(10, 15),
        shoulders=(("x", 10), ("x", 20)),
        datum=(5, 0, 0),
    )
    for parameter in sheet.features[-1].parameters():
        sheet.dimension(handle, parameter.parameter_id)
    drawing = sheet.build()
    drawing.lint()  # populate the declared build's one recognition aggregate
    drawing.remove("dim_detail_a_step0")
    drawing.remove("dim_shoulder_x1")

    outcomes = through_step_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
        plan=compile_dimensions(drawing.model()),
    )

    assert [outcome.state for outcome in outcomes] == ["missing", "missing"]
    assert {
        issue.code for issue in drawing.lint() if issue.code.startswith("through_step_requirement")
    } == {"through_step_requirement_missing"}
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == completeness["missing"] == 2
    assert completeness["inapplicable"] == 0


def test_exact_optional_detail_failure_still_closes_the_physical_leg_ledger() -> None:
    part = Rot(90, 0, 0) * _through_step_part()
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.step_level(
        base=0,
        levels=(10, 15),
        shoulders=(("x", 10), ("x", 20)),
        datum=(5, 0, 0),
    )
    for parameter in sheet.features[-1].parameters():
        sheet.dimension(handle, parameter.parameter_id)
    drawing = sheet.build()
    drawing.lint()  # populate the recognition-owned physical inventory
    name = "dim_detail_a_step0"
    (measurement,) = drawing.registry.measurement_of(name)
    span = drawing.registry.named(name)._dw_measurement_span
    drawing.remove(name)
    drawing.registry.record_issue(
        LintIssue(
            "warning",
            "the exact recovery detail could not be placed",
            code="detail_unplaceable",
            measurement_ids=(measurement,),
            measurement_spans=(span,),
        )
    )
    plan = compile_dimensions(drawing.model())

    outcomes = through_step_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        plan.diagnostics,
        plan=plan,
    )

    assert [(outcome.parameter_id, outcome.state) for outcome in outcomes] == [
        ("through_step_leg.length.z", "dropped"),
        ("through_step_leg.length.x", "inapplicable"),
    ]


def test_equal_valued_ladder_members_and_unrelated_drop_do_not_cover_removed_leg() -> None:
    """Value equality and a shared DimensionId must not erase occurrence identity."""
    part = Rot(90, 0, 0) * _through_step_part()
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.step_level(
        base=0,
        levels=(15,),
        shoulders=(("x", -10), ("x", 20)),
        datum=(5, 0, 0),
    )
    for parameter in sheet.features[-1].parameters():
        sheet.dimension(handle, parameter.parameter_id)
    drawing = sheet.build()
    drawing.lint()  # populate the recognised physical inventory
    plan = compile_dimensions(drawing.model())

    shoulder_rungs = tuple(plan.ladder("step_position").rungs)  # type: ignore[union-attr]
    assert [rung.value for rung in shoulder_rungs] == pytest.approx([15, 15])
    drawing.remove("dim_shoulder_x1")  # physical X leg: datum 5 -> shoulder 20
    unrelated = next(rung for rung in shoulder_rungs if rung.span[1][0] == -10)
    drawing.registry.record_issue(
        LintIssue(
            "warning",
            "unrelated equal-valued shoulder drop",
            code="step_position_dropped",
            measurement_ids=(unrelated.id,),
            outcome_stage="placement",
            measurement_spans=(unrelated.span,),
        )
    )

    outcomes = through_step_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        plan.diagnostics,
        plan=plan,
    )

    assert [(outcome.parameter_id, outcome.state) for outcome in outcomes] == [
        ("through_step_leg.length.z", "inapplicable"),
        ("through_step_leg.length.x", "missing"),
    ]


@pytest.mark.parametrize("deferred", [False, True], ids=["live", "deferred-corridor"])
def test_public_dimension_replacement_preserves_legacy_occurrence_span(deferred) -> None:
    drawing = build_drawing(Rot(90, 0, 0) * _through_step_part())
    drawing.lint()  # populate the recognised physical inventory
    envelope_feature = next(
        feature for feature in drawing.model().features if feature.kind == "envelope"
    )
    width = next(
        parameter for parameter in envelope_feature.parameters() if parameter.role == "width"
    )
    drawing.remove("m_env_width")

    if deferred:
        with drawing.deferred():
            drawing.dimension(
                envelope_feature,
                "length",
                role="width",
                name="replacement_width",
                pin=True,
            )
    else:
        drawing.dimension(
            envelope_feature,
            "length",
            role="width",
            name="replacement_width",
        )

    replacement = drawing.registry.named("replacement_width")
    assert replacement._dw_measurement_span == width.span
    plan = compile_dimensions(drawing.model())
    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(
            drawing.recognition(),
            drawing.model().features,
            drawing.registry,
            plan.diagnostics,
            plan=plan,
        )
    ] == ["inapplicable", "inapplicable"]
    assert not [issue for issue in drawing.lint() if "through_step" in issue.code]


def test_complement_requires_legacy_datum_to_be_the_envelope_minimum() -> None:
    part = Rot(90, 0, 0) * _through_step_part()
    drawing = build_drawing(
        part,
        model=[
            envelope(part),
            step_level(
                base=-10,
                levels=(0,),
                shoulders=(("x", 5),),
                datum=(0, 0, -10),
            ),
        ],
        number="X",
    )
    issues = drawing.lint()  # populate the recognised physical inventory
    plan = compile_dimensions(drawing.model())
    outcomes = through_step_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        plan.diagnostics,
        plan=plan,
    )

    assert {outcome.state for outcome in outcomes} == {"unverifiable"}
    assert {issue.code for issue in issues if "through_step" in issue.code} == {
        "through_step_requirement_unverifiable"
    }


def test_legacy_owner_placement_drops_retain_measurement_identity(monkeypatch) -> None:
    import draftwright.annotations._common as common
    import draftwright.annotations.from_model as from_model

    def reject_every_candidate(_dwg, _strip, _view, _axis, candidates, _tier, **_kwargs):
        return list(candidates)

    monkeypatch.setattr(common, "place_strip_candidates", reject_every_candidate)
    monkeypatch.setattr(from_model, "carve_free_position", lambda *_args, **_kwargs: None)

    with pytest.warns(UserWarning, match="drops required annotation outcomes"):
        drawing = build_drawing(Rot(90, 0, 0) * _through_step_part())
    drawing.lint()  # populate the declared/detected build's physical inventory
    plan = compile_dimensions(drawing.model())
    outcomes = through_step_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        plan.diagnostics,
        plan=plan,
    )

    assert [outcome.state for outcome in outcomes] == ["dropped", "dropped"]
    legacy_drops = [
        issue
        for issue in drawing.registry.issues
        if issue.code in {"placement_unsatisfiable", "step_position_dropped"}
        and issue.measurement_ids
        and issue.measurement_ids[0].feature.kind == "step_level"
    ]
    assert {issue.measurement_ids[0].parameter for issue in legacy_drops} == {
        "step_height.length",
        "step_position.length",
    }
    assert all(issue.measurement_spans for issue in legacy_drops)

    l_bracket = Box(80, 40, 8) + Pos(-36, 0, 24) * Box(8, 40, 40)
    with pytest.warns(UserWarning, match="drops required annotation outcomes"):
        plate_drawing = build_drawing(l_bracket)
    plate_drops = [
        issue for issue in plate_drawing.registry.issues if issue.code == "plate_thickness_dropped"
    ]
    assert plate_drops
    assert all(
        issue.measurement_ids
        and issue.measurement_ids[0].feature.kind == "plate"
        and issue.measurement_ids[0].parameter == "thickness.length"
        and issue.measurement_spans
        for issue in plate_drops
    )


@pytest.mark.parametrize("failure", ["letters", "render"])
def test_failed_prismatic_detail_records_exact_recovery_requirements(monkeypatch, failure) -> None:
    import draftwright.annotations.sections as sections
    from draftwright.linting.issues import is_placement_drop

    if failure == "letters":
        monkeypatch.setattr(sections, "_DETAIL_LETTERS", "")
    else:
        monkeypatch.setattr(sections, "_render_detail", lambda *_args, **_kwargs: False)

    drawing = build_drawing(_crowded_step_part())

    (issue,) = [item for item in drawing.registry.issues if item.code == "detail_unplaceable"]
    assert issue.code == "detail_unplaceable"
    # The optional recovery view is scored as a legibility defect, but it is not itself a
    # required sheet/scale candidate. Its exact rungs are consumed by completeness instead.
    assert not is_placement_drop(issue)
    assert len(issue.measurement_ids) == len(issue.measurement_spans) > 0
    assert {measurement.parameter for measurement in issue.measurement_ids} == {
        "step_height.length"
    }
    assert not [item for item in drawing.registry.issues if item.code == "plan_incomplete"]


def test_each_detail_redraw_exception_records_the_exact_rung_drop(monkeypatch) -> None:
    from draftwright.annotations._common import Escalation, PlacementContext
    from draftwright.annotations.sections import _request_prismatic_detail
    from draftwright.model.compiled import (
        ApprovedDimension,
        ApprovedLadder,
        RenderableDimensionPlan,
    )

    drawing = build_drawing(Box(10, 10, 10))
    owner = step_level(base=0, levels=(1, 2), datum=(0, 0, 0))
    measurement = DimensionId(owner, "step_height.length")
    rungs = tuple(
        ApprovedDimension(
            id=measurement,
            value_text=str(z),
            value=z,
            span=((0, 0, 0), (0, 0, z)),
            rendered_label=str(z),
        )
        for z in (1, 2)
    )
    plan = RenderableDimensionPlan(ladders=(ApprovedLadder("step_height", rungs),))
    analysis = SimpleNamespace(
        bb=SimpleNamespace(
            min=SimpleNamespace(X=-5, Z=0),
            max=SimpleNamespace(X=5, Z=10),
        ),
        cy=0,
        SCALE=1,
    )
    ctx = PlacementContext(
        registry=AnnotationRegistry(),
        escalations=[
            Escalation(
                kind="step",
                view="front",
                feature=owner,
                reason="illegible",
                targets=rungs,
            )
        ],
    )
    _request_prismatic_detail(drawing, analysis, ctx=ctx, plan=plan)
    (request,) = ctx.detail_requests
    assert request.measurement_ids == (measurement, measurement)
    assert request.measurement_spans == tuple(rung.span for rung in rungs)

    def reject(*_args, **_kwargs):
        raise RuntimeError("synthetic placement refusal")

    monkeypatch.setattr(ctx, "place", reject)
    coords = SimpleNamespace(pp=lambda x, _y, z: (x, z))
    assert request.redraw(drawing, "detail_test", coords, 20) == 0
    assert [issue.code for issue in ctx.registry.issues] == [
        "detail_step_dim_dropped",
        "detail_step_dim_dropped",
    ]
    assert [issue.measurement_spans[0] for issue in ctx.registry.issues] == [
        rung.span for rung in rungs
    ]


def test_detail_redraw_drops_are_not_counted_again_as_a_request_failure(monkeypatch) -> None:
    from draftwright.annotations._common import PlacementContext

    place = PlacementContext.place

    def reject_detail_dimension(self, obj, name=None, *args, **kwargs):
        if name is not None and name.startswith("dim_detail_"):
            raise RuntimeError("synthetic detail placement refusal")
        return place(self, obj, name, *args, **kwargs)

    monkeypatch.setattr(PlacementContext, "place", reject_detail_dimension)
    with pytest.warns(UserWarning, match="drops required annotation outcomes"):
        drawing = build_drawing(_crowded_step_part())

    drops = [
        issue
        for issue in drawing.registry.issues
        if issue.code in {"detail_step_dim_dropped", "detail_unplaceable"}
    ]
    assert drops
    assert {issue.code for issue in drops} == {"detail_step_dim_dropped"}
    assert len(drops) == len({issue.measurement_spans[0] for issue in drops})


@pytest.mark.parametrize(
    ("part", "axis", "view"),
    [
        (_through_step_part(), "z", "plan"),
        (Rot(0, 90, 0) * _through_step_part(), "x", "side"),
        (Rot(90, 0, 0) * _through_step_part(), "y", "front"),
    ],
)
def test_explicit_declaration_can_choose_local_leg_grammar_on_every_axis(part, axis, view) -> None:
    source = build_recognition_result(part).through_steps[0]
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.through_step(
        axis=source.axis,
        length=source.length,
        at=source.at,
        section=source.section,
    )
    for parameter in sheet.features[-1].parameters():
        sheet.dimension(handle, parameter.parameter_id)
    drawing = sheet.build()
    feature = _feature(drawing)

    assert feature.axis == axis
    assert len(_dimension_names(drawing)) == 2
    assert {drawing.registry.identity_of(name)["view"] for name in _dimension_names(drawing)} == {
        view
    }
    assert {
        measurement.parameter
        for name in _dimension_names(drawing)
        for measurement in drawing.registry.measurement_of(name)
    } == set(_parameter_ids(feature))
    for name in _dimension_names(drawing):
        (measurement,) = drawing.registry.measurement_of(name)
        _assert_final_witness_span(drawing, name, feature, measurement.parameter)


def test_compound_preserves_body_ownership_and_four_independent_requirements() -> None:
    base = _through_step_part()
    drawing = build_drawing(Compound([Pos(-70, -50, 0) * base, Pos(70, 50, 0) * base]))
    features = [feature for feature in drawing.model().features if feature.kind == "through_step"]
    names = _dimension_names(drawing)
    completeness = drawing.lint_summary()["quality"]["completeness"]

    assert len(drawing.recognition().through_steps) == 2  # type: ignore[union-attr]
    assert len(features) == 2
    assert len(names) == 4
    assert completeness["by_family"]["through_steps"] == 4
    assert completeness["requirements"] == completeness["placed"] == 4
    assert {
        id(measurement.feature)
        for name in names
        for measurement in drawing.registry.measurement_of(name)
    } == {id(feature) for feature in features}
    for name in names:
        (measurement,) = drawing.registry.measurement_of(name)
        parameter = next(
            p for p in measurement.feature.parameters() if p.parameter_id == measurement.parameter
        )
        _assert_final_witness_span(drawing, name, measurement.feature, parameter.parameter_id)


def test_authored_dimension_set_can_select_and_tolerance_one_leg_without_resurrection() -> None:
    part = _through_step_part()
    recognition = build_recognition_result(part)
    source = recognition.through_steps[0]
    sheet = Sheet(part)
    handle = sheet.through_step(
        axis=source.axis,
        length=source.length,
        at=source.at,
        section=source.section,
    )
    selected = "through_step_leg.length.x"
    handle.tolerance(0.2, on=selected)
    sheet.authored_dimensions().dimension(handle, selected)
    drawing = sheet.build()
    feature = _feature(drawing)
    outcomes = through_step_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )

    assert len(_dimension_names(drawing)) == 1
    name = _dimension_names(drawing)[0]
    assert drawing.registry.measurement_of(name) == (DimensionId(feature, selected),)
    assert str(drawing.registry.named(name).label) == "15 ±0.2"
    assert [(outcome.parameter_id, outcome.state) for outcome in outcomes] == [
        ("through_step_leg.length.y", "suppressed"),
        (selected, "placed"),
    ]


def test_compiled_boundary_cannot_reconstruct_a_suppressed_unequal_leg() -> None:
    def _compiled(suppressed_length):
        sheet = Sheet(_through_step_part()).authored_dimensions()
        handle = sheet.through_step(
            axis="z",
            length=20,
            at=(12.5, 6, 0),
            section=((5, suppressed_length), (5, 0), (20, 0)),
        )
        sheet.dimension(handle, "through_step_leg.length.x")
        return next(iter(compile_dimensions(sheet.model()).of_kind("through_step")))

    first = _compiled(12)
    mutated = _compiled(19)

    assert first.facts.axis == mutated.facts.axis == "z"
    assert (
        first.facts.outside_directions
        == mutated.facts.outside_directions
        == (
            ("x", 1),
            ("y", 1),
        )
    )
    assert not hasattr(first.facts, "exterior_corner")
    assert [
        (dimension.value, dimension.span, dimension.discriminator) for dimension in first.dims
    ] == [(dimension.value, dimension.span, dimension.discriminator) for dimension in mutated.dims]


def _declared_through_step_sheet():
    part = _through_step_part()
    source = build_recognition_result(part).through_steps[0]
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.through_step(
        axis=source.axis,
        length=source.length,
        at=source.at,
        section=source.section,
    )
    for parameter in sheet.features[-1].parameters():
        sheet.dimension(handle, parameter.parameter_id)
    return part, sheet, handle


def _through_step_tolerances(sheet) -> dict[str, object]:
    group = next(iter(compile_dimensions(sheet.model()).of_kind("through_step")))
    return {dimension.id.parameter: dimension.tolerance for dimension in group.dims}


def test_targeted_family_and_bare_tolerances_have_order_independent_scope() -> None:
    _part, sheet, handle = _declared_through_step_sheet()
    handle.tolerance(0.1, on="x").tolerance(0.2)
    assert _through_step_tolerances(sheet) == {
        "through_step_leg.length.x": pytest.approx(0.2),
        "through_step_leg.length.y": pytest.approx(0.2),
    }

    _part, sheet, handle = _declared_through_step_sheet()
    handle.tolerance(0.3, on="through_step_leg")
    assert _through_step_tolerances(sheet) == {
        "through_step_leg.length.x": pytest.approx(0.3),
        "through_step_leg.length.y": pytest.approx(0.3),
    }


def test_generated_sheet_exec_preserves_one_discriminated_leg_tolerance() -> None:
    part, sheet, handle = _declared_through_step_sheet()
    handle.tolerance(
        0.2,
        on="through_step_leg.length.x",
        source="inspection plan",
        source_ids=("IP-17",),
    )
    source = emit_sheet_script(
        sheet.model(),
        "part",
        "through-step",
        title="Through step",
        number="1382",
    )
    assert (
        ".tolerance(0.2, on='through_step_leg.length.x', "
        "source='inspection plan', source_ids=('IP-17',))"
    ) in source

    namespace = {"part": part}
    body = source.replace("\npart\n", "\n", 1)
    body = body[: body.index("drawing = sheet.build()")]
    exec(compile(body, "<through-step-round-trip>", "exec"), namespace)  # noqa: S102
    assert _through_step_tolerances(namespace["sheet"]) == {
        "through_step_leg.length.x": pytest.approx(0.2),
        "through_step_leg.length.y": None,
    }


@pytest.mark.parametrize("deferred", [False, True], ids=["live", "deferred"])
def test_built_drawing_can_replay_both_exact_legs_with_identity_and_end_view(deferred) -> None:
    drawing = build_drawing(_through_step_part())
    feature = _feature(drawing)
    drawing.drop(feature)
    names = {
        parameter.parameter_id: f"replay_{parameter.discriminator}"
        for parameter in feature.parameters()
    }

    if deferred:
        with drawing.deferred():
            for parameter_id, name in names.items():
                drawing.dimension(feature, parameter_id, name=name)
    else:
        for parameter_id, name in names.items():
            drawing.dimension(feature, parameter_id, name=name)

    for parameter_id, name in names.items():
        assert drawing.registry.measurement_of(name) == (DimensionId(feature, parameter_id),)
        assert drawing.registry.identity_of(name)["view"] == "plan"
        _assert_final_witness_span(drawing, name, feature, parameter_id)
    assert not [issue for issue in drawing.lint() if "through_step" in issue.code]


def test_outcome_ledger_distinguishes_evidence_and_fails_closed() -> None:
    drawing = build_drawing(_through_step_part())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = _feature(drawing)
    parameters = _parameter_ids(feature)
    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition, drawing.model().features, drawing.registry
        )
    ] == ["placed", "placed"]

    empty = AnnotationRegistry()
    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition, drawing.model().features, empty
        )
    ] == ["missing", "missing"]

    omission = SimpleNamespace(feature=feature, parameter_id=parameters[0], authored=True)
    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition, drawing.model().features, empty, (omission,)
        )
    ] == ["suppressed", "missing"]

    dropped = AnnotationRegistry()
    dropped.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="through_step_dim_dropped",
            measurement_ids=(DimensionId(feature, parameters[1]),),
            outcome_stage="placement",
        )
    )
    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition, drawing.model().features, dropped
        )
    ] == ["missing", "dropped"]

    structured = AnnotationRegistry()
    structured.add(
        object(),
        "structured_note",
        "plan",
        feature=feature,
        satisfaction=DimensionId(feature, parameters[0]),
    )
    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition, drawing.model().features, structured
        )
    ] == ["satisfied_by_structured_note", "missing"]

    duplicated = replace(
        recognition,
        through_steps=(recognition.through_steps[0], recognition.through_steps[0]),
    )
    assert {
        outcome.state
        for outcome in through_step_requirement_outcomes(
            duplicated, drawing.model().features, empty
        )
    } == {"unverifiable"}

    assert {
        outcome.state
        for outcome in through_step_requirement_outcomes(recognition, (feature, feature), empty)
    } == {"unverifiable"}

    corruptions = (
        replace(feature, length=feature.length + 1),
        replace(
            feature,
            frame=Frame((feature.frame.origin[0] + 1, *feature.frame.origin[1:]), feature.axis),
        ),
        replace(feature, frame=Frame(feature.frame.origin, "x"), axis="x"),
        replace(feature, section=((5.0, 14.0), feature.section[1], feature.section[2])),
        replace(feature, section=((6.0, 15.0), (6.0, 1.0), (20.0, 1.0))),
        replace(feature, section=(feature.section[0], feature.section[1], (21.0, 0.0))),
    )
    for corrupted in corruptions:
        assert {
            outcome.state
            for outcome in through_step_requirement_outcomes(recognition, (corrupted,), empty)
        } == {"unverifiable"}

    malformed = replace(
        recognition,
        through_steps=(
            SimpleNamespace(
                axis="z",
                length=20,
                at=None,
                section=((5, 15), (5, 0), (20, 0)),
            ),
        ),
    )
    malformed_outcomes = through_step_requirement_outcomes(malformed, (feature,), empty)
    assert [outcome.state for outcome in malformed_outcomes] == [
        "unverifiable",
        "unverifiable",
    ]
    assert all(
        all(coordinate != coordinate for coordinate in outcome.source_at)
        for outcome in malformed_outcomes
    )

    malformed_length = replace(
        recognition,
        through_steps=(replace(recognition.through_steps[0], length=float("nan")),),
    )
    assert {
        outcome.state
        for outcome in through_step_requirement_outcomes(
            malformed_length, drawing.model().features, empty
        )
    } == {"unverifiable"}

    boolean_section = replace(
        recognition,
        through_steps=(
            replace(
                recognition.through_steps[0],
                section=((5.0, 15.0), (5.0, False), (20.0, False)),
            ),
        ),
    )
    assert {
        outcome.state
        for outcome in through_step_requirement_outcomes(
            boolean_section, drawing.model().features, drawing.registry
        )
    } == {"unverifiable"}


@pytest.mark.parametrize(
    "source",
    (
        lambda source: replace(source, at=source.at[:2]),
        lambda source: replace(
            source,
            section=(source.section[0], (6.0, 0.0), source.section[2]),
        ),
        lambda source: replace(
            source,
            section=(source.section[0], source.section[1], (5.0, -5.0)),
        ),
        lambda source: replace(source, length=10**10000),
        lambda source: replace(source, at=(10**10000, source.at[1], source.at[2])),
        lambda source: replace(
            source,
            section=((10**10000, source.section[0][1]), *source.section[1:]),
        ),
    ),
    ids=(
        "short-anchor",
        "diagonal-leg",
        "repeated-leg-axis",
        "overflowing-length",
        "overflowing-anchor",
        "overflowing-section",
    ),
)
def test_malformed_source_geometry_fails_closed(source) -> None:
    drawing = build_drawing(_through_step_part())
    recognition = drawing.recognition()
    assert recognition is not None
    malformed = source(recognition.through_steps[0])

    outcomes = through_step_requirement_outcomes(
        replace(recognition, through_steps=(malformed,)),
        drawing.model().features,
        AnnotationRegistry(),
    )

    assert [outcome.state for outcome in outcomes] == ["unverifiable", "unverifiable"]


def test_malformed_ir_and_provenance_fail_closed_without_hiding_requirements() -> None:
    drawing = build_drawing(_through_step_part())
    recognition = drawing.recognition()
    assert recognition is not None
    source = recognition.through_steps[0]

    class BrokenThroughStep:
        pass

    invalid_owner = BrokenThroughStep()
    invalid_owner.kind = "through_step"
    invalid_owner.axis = source.axis
    invalid_owner.at = source.at
    invalid_owner.length = source.length
    invalid_owner.section = source.section
    invalid_owner.parameters = lambda: None
    incomplete_owner = BrokenThroughStep()
    incomplete_owner.kind = "through_step"
    incomplete_owner.axis = source.axis
    invalid_identity = DimensionId(None, "bad")
    registry = AnnotationRegistry()
    registry.add(
        SimpleNamespace(label="10"),
        "invalid_provenance",
        "plan",
        measurement=invalid_identity,
        satisfaction=invalid_identity,
    )
    registry.add(
        SimpleNamespace(label="10"),
        "valid_structured_provenance",
        "plan",
        satisfaction=DimensionId(invalid_owner, "test.length"),
    )
    registry.record_issue(
        LintIssue(
            "warning",
            "invalid measurement provenance",
            code="synthetic_dim_dropped",
            outcome_stage="placement",
            measurement_ids=(invalid_identity,),
            measurement_spans=(((0, 0, 0), (1, 0, 0)),),
        )
    )

    outcomes = through_step_requirement_outcomes(
        recognition,
        (invalid_owner, incomplete_owner),
        registry,
    )

    assert [outcome.state for outcome in outcomes] == ["unverifiable", "unverifiable"]


@pytest.mark.parametrize("invalid_axis", ["", "xy"], ids=("empty", "multiple"))
def test_non_principal_source_axis_cannot_reuse_matching_ir_ink(invalid_axis) -> None:
    drawing = build_drawing(_through_step_part())
    recognition = drawing.recognition()
    assert recognition is not None
    source = replace(recognition.through_steps[0], axis=invalid_axis)
    recognition = replace(recognition, through_steps=(source,))

    class InjectedThroughStep:
        pass

    owner = InjectedThroughStep()
    owner.kind = "through_step"
    owner.axis = invalid_axis
    owner.at = source.at
    owner.length = source.length
    owner.section = source.section
    parameter_ids = ("through_step_leg.length.y", "through_step_leg.length.x")
    owner.parameters = lambda: tuple(
        SimpleNamespace(parameter_id=parameter_id) for parameter_id in parameter_ids
    )
    registry = AnnotationRegistry()
    for index, parameter_id in enumerate(parameter_ids):
        registry.add(
            SimpleNamespace(label="1"),
            f"injected_{index}",
            "plan",
            feature=owner,
            measurement=DimensionId(owner, parameter_id),
        )

    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(recognition, (owner,), registry)
    ] == ["unverifiable", "unverifiable"]


def test_direct_plate_owners_and_invalid_compiler_label_are_handled_exactly() -> None:
    drawing = build_drawing(Rot(90, 0, 0) * _through_step_part())
    recognition = drawing.recognition()
    assert recognition is not None
    direct_height_plate = plate(axis="z", lo=0, hi=15, u=0, v=0)
    direct_position_plate = plate(axis="x", lo=5, hi=20, u=0, v=0)
    owner_envelope = envelope(Rot(90, 0, 0) * _through_step_part())

    assert {
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition,
            (direct_height_plate, owner_envelope),
            AnnotationRegistry(),
        )
    } == {"unverifiable"}
    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition,
            (direct_height_plate, direct_position_plate),
            AnnotationRegistry(),
        )
    ] == ["missing", "missing"]

    plan = compile_dimensions(drawing.model())
    malformed_plan = replace(
        plan,
        ladders=tuple(
            replace(
                ladder,
                rungs=tuple(replace(rung, value_text="not-a-number") for rung in ladder.rungs),
            )
            if ladder.kind in {"step_height", "step_position"}
            else ladder
            for ladder in plan.ladders
        ),
    )
    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition,
            drawing.model().features,
            drawing.registry,
            malformed_plan.diagnostics,
            plan=malformed_plan,
        )
    ] == ["missing", "missing"]

    def shifted_span(rung):
        if rung.span is None:
            return rung
        return replace(
            rung,
            span=tuple(tuple(coordinate + 1 for coordinate in point) for point in rung.span),
        )

    wrong_span_plan = replace(
        plan,
        ladders=tuple(
            replace(ladder, rungs=tuple(shifted_span(rung) for rung in ladder.rungs))
            if ladder.kind in {"step_height", "step_position"}
            else ladder
            for ladder in plan.ladders
        ),
    )
    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition,
            drawing.model().features,
            drawing.registry,
            wrong_span_plan.diagnostics,
            plan=wrong_span_plan,
        )
    ] == ["missing", "missing"]


@pytest.mark.parametrize(
    ("malformed", "approved_height_span", "rendered_height_span"),
    (
        (True, ((0, 0, 0), (0, 0, 1)), ((0, 0, 0), (0, 0, 1))),
        ("nan", ((0, 0, 0), (0, 0, 1)), ((0, 0, 0), (0, 0, 1))),
        ("inf", ((0, 0, 0), (0, 0, 1)), ((0, 0, 0), (0, 0, 1))),
        ("1", ((0,), (1,)), ((0, 0, 0), (0, 0, 1))),
        ("1", ((0, 0, False), (0, 0, True)), ((0, 0, 0), (0, 0, 1))),
        ("1", ((0, 0, float("nan")), (0, 0, 1)), ((0, 0, 0), (0, 0, 1))),
        ("1", ((0, 0, 10**10000), (0, 0, 1)), ((0, 0, 0), (0, 0, 1))),
        ("1", ((0, 0, 0), (0, 0, 1)), ((0,), (1,))),
        ("1", ((0, 0, 0), (0, 0, 1)), ((0, 0, False), (0, 0, True))),
        ("1", ((0, 0, 0), (0, 0, 1)), ((0, 0, float("nan")), (0, 0, 1))),
        ("1", ((0, 0, 0), (0, 0, 1)), ((0, 0, 10**10000), (0, 0, 1))),
    ),
    ids=(
        "boolean-label",
        "nan-label",
        "infinite-label",
        "short-span",
        "boolean-span",
        "nan-span",
        "overflowing-span",
        "short-rendered-span",
        "boolean-rendered-span",
        "nan-rendered-span",
        "overflowing-rendered-span",
    ),
)
def test_malformed_compiler_content_cannot_certify_a_one_mm_legacy_leg(
    malformed, approved_height_span, rendered_height_span
) -> None:
    drawing = build_drawing(Rot(90, 0, 0) * _through_step_part())
    recognition = drawing.recognition()
    assert recognition is not None
    source = replace(
        recognition.through_steps[0],
        section=((5.0, 1.0), (5.0, 0.0), (20.0, 0.0)),
    )
    recognition = replace(recognition, through_steps=(source,))
    height_plate = plate(axis="z", lo=0, hi=1, u=0, v=0)
    position_plate = plate(axis="x", lo=5, hi=20, u=0, v=0)
    height_id = DimensionId(height_plate, "thickness.length")
    position_id = DimensionId(position_plate, "thickness.length")
    position_span = ((5, 0, 0), (20, 0, 0))
    registry = AnnotationRegistry()
    registry.add(
        SimpleNamespace(label="1", _dw_measurement_span=rendered_height_span),
        "height_plate",
        "front",
        measurement=height_id,
    )
    registry.add(
        SimpleNamespace(label="15", _dw_measurement_span=position_span),
        "position_plate",
        "front",
        measurement=position_id,
    )
    plan = RenderableDimensionPlan(
        ladders=(
            ApprovedLadder(
                "step_height",
                (ApprovedDimension(height_id, malformed, 1.0, approved_height_span),),
            ),
            ApprovedLadder(
                "step_position",
                (ApprovedDimension(position_id, "15", 15.0, position_span),),
            ),
        )
    )

    assert [
        outcome.state
        for outcome in through_step_requirement_outcomes(
            recognition,
            (height_plate, position_plate),
            registry,
            plan=plan,
        )
    ] == ["missing", "inapplicable"]


def test_ledger_rejects_wrong_runs_and_quality_scores_two_requirements() -> None:
    drawing = build_drawing(_through_step_part())
    recognition = drawing.recognition()
    assert recognition is not None
    assert through_step_requirement_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="requires the run's RecognitionResult"):
        through_step_requirement_outcomes(SimpleNamespace(), (), AnnotationRegistry())

    issues = lint_through_step_coverage(
        _through_step_part(),
        recognition=recognition,
        features=drawing.model().features,
        registry=AnnotationRegistry(),
        assembly=False,
    )
    assert {issue.code for issue in issues} == {"through_step_requirement_missing"}
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["by_family"]["through_steps"] == 2
    assert completeness["requirements"] == completeness["placed"] == 2
    assert completeness["audited_score"] == 1.0
    assert "through_steps" not in completeness["unscored_recognized_families"]
