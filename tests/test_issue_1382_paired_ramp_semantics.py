"""#1382 — paired-ramp records have one complete, auditable Draftwright path."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from b123d_recognisers import build_raw_recognition_result
from build123d import Box, Compound, Plane, Polygon, Pos, Rot, extrude

from draftwright import Sheet, build_drawing
from draftwright.linting.issues import LintIssue
from draftwright.linting.paired_ramp_step_coverage import (
    lint_paired_ramp_step_coverage,
    paired_ramp_step_requirement_outcomes,
)
from draftwright.model import Frame, PairedRampStepFeature, paired_ramp_step
from draftwright.model.compiled import DimensionId, compile_dimensions
from draftwright.registry import AnnotationRegistry
from draftwright.sheet_emit import _feature_line


def _paired_ramp_part():
    profile = Polygon((0, -8), (0, 8), (-10, 0))
    cutter = Pos(20, 20, 0) * extrude(Plane.XZ * profile, 25)
    return Box(40, 40, 30) - cutter


def _shallow_paired_ramp_part():
    profile = Polygon((0, -0.5), (0, 0.5), (-10, 0))
    cutter = Pos(20, 20, 0) * extrude(Plane.XZ * profile, 25)
    return Box(40, 40, 30) - cutter


def _ramp_feature(drawing) -> PairedRampStepFeature:
    return next(
        feature for feature in drawing.model().features if feature.kind == "paired_ramp_step"
    )


def _ramp_annotation(drawing):
    name = next(name for name in drawing.annotations() if name.startswith("m_paired_ramp_"))
    return name, drawing.registry.named(name)


def _assert_owned_ramp_callout(drawing, feature, name=None):
    if name is None:
        name, annotation = _ramp_annotation(drawing)
    else:
        annotation = drawing.registry.named(name)
    identity = drawing.registry.identity_of(name)
    view = {"x": "side", "y": "front", "z": "plan"}[feature.axis]

    assert identity["view"] == view
    assert {measurement.parameter for measurement in identity["measurement"]} == {
        "ramp_angle.angle",
        "ramp_run.length",
    }
    assert all(measurement.feature is feature for measurement in identity["measurement"])
    assert annotation.tip == pytest.approx(drawing.at(view, *feature.frame.origin)[:2])
    assert not [issue for issue in drawing.lint() if "paired_ramp" in issue.code]
    return annotation


def test_aggregate_record_lowers_to_exact_ir_and_two_planned_requirements() -> None:
    part = _paired_ramp_part()
    drawing = build_drawing(part)
    recognition = drawing.recognition()

    assert recognition is not None
    assert len(recognition.paired_ramp_steps) == 1
    source = recognition.paired_ramp_steps[0]
    assert (source.axis, source.angle, source.length) == ("y", 51.34, 25.0)
    feature = _ramp_feature(drawing)
    assert feature == PairedRampStepFeature(
        frame=feature.frame,
        axis="y",
        angle=51.34,
        length=25.0,
    )
    assert feature.frame.origin == source.at
    assert feature.span == (
        (source.at[0], source.at[1] - source.length / 2, source.at[2]),
        (source.at[0], source.at[1] + source.length / 2, source.at[2]),
    )
    assert [parameter.parameter_id for parameter in feature.parameters()] == [
        "ramp_angle.angle",
        "ramp_run.length",
    ]

    group = next(iter(compile_dimensions(drawing.model()).of_kind("paired_ramp_step")))
    assert group.view == "front"
    assert [dimension.id.parameter for dimension in group.dims] == [
        "ramp_angle.angle",
        "ramp_run.length",
    ]


def test_0412_shallow_nonzero_pair_keeps_the_existing_complete_consumer_meaning() -> None:
    drawing = build_drawing(_shallow_paired_ramp_part())
    recognition = drawing.recognition()
    assert recognition is not None
    assert len(recognition.paired_ramp_steps) == 1
    source = recognition.paired_ramp_steps[0]
    assert (source.axis, source.angle, source.length) == ("y", 87.14, 25.0)

    feature = _ramp_feature(drawing)
    assert (feature.axis, feature.angle, feature.length, feature.frame.origin) == (
        source.axis,
        source.angle,
        source.length,
        source.at,
    )
    name, annotation = _ramp_annotation(drawing)
    assert annotation.label == "2× 87.1° × 25 RUN"
    _assert_owned_ramp_callout(drawing, feature, name)
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["by_family"]["paired_ramp_steps"] == 2
    assert completeness["placed"] == completeness["requirements"] == 2

    sheet = Sheet(_shallow_paired_ramp_part())
    handle = sheet.paired_ramp_step(
        axis=source.axis,
        angle=source.angle,
        length=source.length,
        at=source.at,
    )
    sheet.authored_dimensions().dimension(handle, "ramp_angle.angle").dimension(
        handle, "ramp_run.length"
    )
    declared = sheet.build()
    declared_feature = _ramp_feature(declared)
    line = _feature_line(declared_feature)
    namespace = {"sheet": type("S", (), {"paired_ramp_step": staticmethod(paired_ramp_step)})()}
    assert eval(line, {"__builtins__": {}}, namespace) == declared_feature  # noqa: S307
    assert _ramp_annotation(declared)[1].label == annotation.label


def test_detected_build_consumes_the_one_aggregate_inventory_without_rescanning(
    monkeypatch,
) -> None:
    import draftwright.model.detect as detect

    def forbidden_sibling_scan(_part):
        raise AssertionError("paired-ramp family was rescanned outside the aggregate")

    monkeypatch.setattr(detect, "recognise_paired_ramp_steps", forbidden_sibling_scan)
    drawing = build_drawing(_paired_ramp_part())

    assert _ramp_feature(drawing).angle == 51.34


def test_explicit_declaration_and_generated_line_round_trip_the_full_feature() -> None:
    declared = paired_ramp_step(axis="y", angle=51.34, length=25, at=(10, 7.5, 0))
    assert isinstance(declared, PairedRampStepFeature)
    assert declared.parameters()[0].value == 51.34
    assert declared.parameters()[1].value == 25.0

    line = _feature_line(declared)
    assert line == ('sheet.paired_ramp_step(axis="y", angle=51.34, length=25, at=(10, 7.5, 0))')
    namespace = {"sheet": type("S", (), {"paired_ramp_step": staticmethod(paired_ramp_step)})()}
    assert eval(line, {"__builtins__": {}}, namespace) == declared  # noqa: S307

    with pytest.raises(ValueError, match="finite acute angle"):
        paired_ramp_step(axis="y", angle=90, length=25, at=(10, 7.5, 0))
    with pytest.raises(ValueError, match="finite acute angle"):
        paired_ramp_step(axis="y", angle=True, length=25, at=(10, 7.5, 0))
    with pytest.raises(ValueError, match="positive"):
        paired_ramp_step(axis="y", angle=45, length=0, at=(10, 7.5, 0))


def test_ir_rejects_invalid_frames_and_measurements() -> None:
    frame = Frame((10, 7.5, 0), "y")

    with pytest.raises(ValueError, match="agree with the feature frame"):
        PairedRampStepFeature(frame, "x", 45, 25)
    with pytest.raises(ValueError, match="axis must be x, y, or z"):
        PairedRampStepFeature(Frame((10, 7.5, 0), "xy"), "xy", 45, 25)
    with pytest.raises(ValueError, match="finite acute angle"):
        PairedRampStepFeature(frame, "y", float("nan"), 25)
    with pytest.raises(ValueError, match="finite acute angle"):
        PairedRampStepFeature(frame, "y", True, 25)
    with pytest.raises(ValueError, match="finite and positive"):
        PairedRampStepFeature(frame, "y", 45, 0)
    with pytest.raises(ValueError, match="finite and positive"):
        PairedRampStepFeature(frame, "y", 45, True)

    assert PairedRampStepFeature(frame, "y", 45, 25).references() == []


def test_auto_callout_carries_both_identities_at_the_physical_ridge() -> None:
    drawing = build_drawing(_paired_ramp_part())
    feature = _ramp_feature(drawing)
    _name, annotation = _ramp_annotation(drawing)

    assert annotation.label == "2× 51.3° × 25 RUN"
    _assert_owned_ramp_callout(drawing, feature)


def test_physical_ridge_evidence_observes_final_leader_translation() -> None:
    drawing = build_drawing(_paired_ramp_part())
    feature = _ramp_feature(drawing)
    _name, annotation = _ramp_annotation(drawing)
    projected_ridge = drawing.at("front", *feature.frame.origin)[:2]

    annotation.position = (5, 0, 0)

    assert annotation.tip != pytest.approx(projected_ridge)


@pytest.mark.parametrize(
    ("part", "axis", "view"),
    [
        (_paired_ramp_part(), "y", "front"),
        (Rot(0, 0, -90) * _paired_ramp_part(), "x", "side"),
        (Rot(90, 0, 0) * _paired_ramp_part(), "z", "plan"),
    ],
)
def test_end_on_view_routing_is_derived_for_every_principal_axis(part, axis, view) -> None:
    drawing = build_drawing(part)
    feature = _ramp_feature(drawing)

    assert feature.axis == axis
    assert {"x": "side", "y": "front", "z": "plan"}[axis] == view
    _assert_owned_ramp_callout(drawing, feature)


def test_compound_keeps_each_body_owned_ramp_and_four_requirements() -> None:
    base = _paired_ramp_part()
    part = Compound([Pos(-60, 0, 0) * base, Pos(60, 0, 0) * base])
    drawing = build_drawing(part)
    completeness = drawing.lint_summary()["quality"]["completeness"]

    assert len(drawing.recognition().paired_ramp_steps) == 2  # type: ignore[union-attr]
    assert (
        len(
            [feature for feature in drawing.model().features if feature.kind == "paired_ramp_step"]
        )
        == 2
    )
    names = [name for name in drawing.annotations() if name.startswith("m_paired_ramp_")]
    assert len(names) == 2
    assert completeness["by_family"]["paired_ramp_steps"] == 4
    assert completeness["placed"] == completeness["requirements"] == 4

    owned = []
    for name in names:
        feature = drawing.registry.identity_of(name)["measurement"][0].feature
        _assert_owned_ramp_callout(drawing, feature, name)
        owned.append(feature)
    assert len({id(feature) for feature in owned}) == 2
    source_origins = {
        step.at
        for step in drawing.recognition().paired_ramp_steps  # type: ignore[union-attr]
    }
    assert {feature.frame.origin for feature in owned} == source_origins
    owned_x = sorted(feature.frame.origin[0] for feature in owned)
    assert owned_x[1] - owned_x[0] == pytest.approx(120.0)

    # A one-feature live replay filters the other aggregate-owned feature from the shared
    # renderer rather than reconstructing or rescanning it.
    first = next(
        feature for feature in drawing.model().features if feature.kind == "paired_ramp_step"
    )
    drawing.drop(first)
    replayed = drawing.callout(first)
    assert replayed.startswith("m_paired_ramp_")
    _assert_owned_ramp_callout(drawing, first, replayed)


@pytest.mark.parametrize(
    ("parameter", "tolerance_role", "expected", "suppressed"),
    [
        ("ramp_angle.angle", "angle", "2× 51.3 ±0.2°", "ramp_run.length"),
        ("ramp_run.length", "run", "25 ±0.2 RUN", "ramp_angle.angle"),
    ],
)
def test_authored_partial_sets_and_tolerances_do_not_resurrect_omitted_content(
    parameter, tolerance_role, expected, suppressed
) -> None:
    part = _paired_ramp_part()
    recognition = build_raw_recognition_result(part)
    source = recognition.paired_ramp_steps[0]
    sheet = Sheet(part)
    handle = sheet.paired_ramp_step(
        axis=source.axis,
        angle=source.angle,
        length=source.length,
        at=source.at,
    )
    handle.tolerance(0.2, on=tolerance_role)
    sheet.authored_dimensions().dimension(handle, parameter)

    drawing = sheet.build()
    _name, annotation = _ramp_annotation(drawing)
    outcomes = paired_ramp_step_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )

    assert annotation.label == expected
    assert (
        [(outcome.parameter_id, outcome.state) for outcome in outcomes]
        == [
            (parameter, "placed"),
            (suppressed, "suppressed"),
        ]
        if parameter == "ramp_angle.angle"
        else [
            (suppressed, "suppressed"),
            (parameter, "placed"),
        ]
    )


def test_ledger_distinguishes_every_outcome_and_fails_closed_on_ambiguity() -> None:
    drawing = build_drawing(_paired_ramp_part())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = _ramp_feature(drawing)

    placed = paired_ramp_step_requirement_outcomes(
        recognition, drawing.model().features, drawing.registry
    )
    assert [outcome.state for outcome in placed] == ["placed", "placed"]

    empty = AnnotationRegistry()
    assert [
        outcome.state
        for outcome in paired_ramp_step_requirement_outcomes(
            recognition, drawing.model().features, empty
        )
    ] == ["missing", "missing"]

    omission = SimpleNamespace(feature=feature, parameter_id="ramp_angle.angle", authored=True)
    suppressed = paired_ramp_step_requirement_outcomes(
        recognition, drawing.model().features, empty, (omission,)
    )
    assert [outcome.state for outcome in suppressed] == ["suppressed", "missing"]

    dropped_registry = AnnotationRegistry()
    dropped_registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="paired_ramp_step_dropped",
            measurement_ids=(DimensionId(feature, "ramp_run.length"),),
            outcome_stage="placement",
        )
    )
    dropped = paired_ramp_step_requirement_outcomes(
        recognition, drawing.model().features, dropped_registry
    )
    assert [outcome.state for outcome in dropped] == ["missing", "dropped"]

    satisfied_registry = AnnotationRegistry()
    satisfied_registry.add(
        object(),
        "structured_note",
        "front",
        feature=feature,
        satisfaction=DimensionId(feature, "ramp_angle.angle"),
    )
    satisfied = paired_ramp_step_requirement_outcomes(
        recognition, drawing.model().features, satisfied_registry
    )
    assert [outcome.state for outcome in satisfied] == [
        "satisfied_by_structured_note",
        "missing",
    ]

    duplicated = replace(
        recognition,
        paired_ramp_steps=(
            recognition.paired_ramp_steps[0],
            recognition.paired_ramp_steps[0],
        ),
    )
    ambiguous = paired_ramp_step_requirement_outcomes(duplicated, drawing.model().features, empty)
    assert len(ambiguous) == 4
    assert {outcome.state for outcome in ambiguous} == {"unverifiable"}

    x, y, z = feature.frame.origin
    corrupted_features = (
        replace(feature, angle=feature.angle + 1),
        replace(feature, length=feature.length + 1),
        replace(feature, frame=Frame((x + 1, y, z), feature.axis)),
        replace(feature, frame=Frame(feature.frame.origin, "x"), axis="x"),
    )
    for corrupted in corrupted_features:
        outcomes = paired_ramp_step_requirement_outcomes(recognition, (corrupted,), empty)
        assert [outcome.state for outcome in outcomes] == ["unverifiable", "unverifiable"]


def test_ledger_rejects_wrong_runs_and_malformed_correspondence_without_guessing() -> None:
    drawing = build_drawing(_paired_ramp_part())
    recognition = drawing.recognition()
    assert recognition is not None

    assert paired_ramp_step_requirement_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="requires the run's RecognitionResult"):
        paired_ramp_step_requirement_outcomes(SimpleNamespace(), (), AnnotationRegistry())

    malformed_source = SimpleNamespace(axis="y", angle=51.34, length=25, at=None)
    malformed_run = replace(recognition, paired_ramp_steps=(malformed_source,))
    malformed_outcomes = paired_ramp_step_requirement_outcomes(
        malformed_run, drawing.model().features, AnnotationRegistry()
    )
    assert [outcome.state for outcome in malformed_outcomes] == [
        "unverifiable",
        "unverifiable",
    ]
    assert all(value != value for value in malformed_outcomes[0].source_at)  # NaN sentinel

    source = recognition.paired_ramp_steps[0]
    broken_ir = SimpleNamespace(
        kind="paired_ramp_step",
        axis=source.axis,
        angle=source.angle,
        length=source.length,
        at=source.at,
        parameters=None,
    )
    outcomes = paired_ramp_step_requirement_outcomes(
        recognition, (SimpleNamespace(kind="paired_ramp_step"), broken_ir), AnnotationRegistry()
    )
    assert [outcome.state for outcome in outcomes] == ["unverifiable", "unverifiable"]

    issues = lint_paired_ramp_step_coverage(
        _paired_ramp_part(),
        recognition=recognition,
        features=drawing.model().features,
        registry=AnnotationRegistry(),
        assembly=False,
    )
    assert {issue.code for issue in issues} == {"paired_ramp_step_requirement_missing"}


def test_quality_counts_both_requirements_and_no_longer_marks_family_unscored() -> None:
    completeness = build_drawing(_paired_ramp_part()).lint_summary()["quality"]["completeness"]

    assert completeness["by_family"]["paired_ramp_steps"] == 2
    assert completeness["placed"] == completeness["requirements"] == 2
    assert completeness["audited_score"] == 1.0
    assert "paired_ramp_steps" not in completeness["unscored_recognized_families"]


def test_live_and_deferred_callout_verbs_reuse_the_same_renderer() -> None:
    live = build_drawing(_paired_ramp_part())
    live_feature = _ramp_feature(live)
    live.drop(live_feature)
    live_name = live.callout(live_feature)
    assert live_name.startswith("m_paired_ramp_")
    assert _assert_owned_ramp_callout(live, live_feature, live_name).label == "2× 51.3° × 25 RUN"

    deferred = build_drawing(_paired_ramp_part())
    deferred_feature = _ramp_feature(deferred)
    deferred.drop(deferred_feature)
    with deferred.deferred():
        deferred.callout(deferred_feature)
    deferred_name, _annotation = _ramp_annotation(deferred)
    assert (
        _assert_owned_ramp_callout(deferred, deferred_feature, deferred_name).label
        == "2× 51.3° × 25 RUN"
    )
