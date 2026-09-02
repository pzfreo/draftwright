"""#1372 — rectangular-pad completeness uses independent physical facts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Location

from draftwright.evaluation.step_analysis import (
    _default_observers,
    _pad_axis,
    _pad_bounds,
    _pad_drawing_outcomes,
    _pad_model_outcomes,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-rectangular-pads-v1.json"
_MIN = (Align.MIN, Align.MIN, Align.MIN)
_PLANE_AXES = {"x": ("y", "z"), "y": ("z", "x"), "z": ("x", "y")}


def _box(size, position):
    return Box(*size, align=_MIN).moved(Location(position))


def _lone(axis: str = "z", direction: int = 1):
    if axis == "z":
        body_pos = (0, 0, 0 if direction > 0 else 5)
        pad_pos = (5, 8, 10 if direction > 0 else 0)
        return _box((40, 30, 10), body_pos) + _box((15, 10, 5), pad_pos)
    if axis == "x":
        body_pos = (0 if direction > 0 else 5, 0, 0)
        pad_pos = (10 if direction > 0 else 0, 8, 5)
        return _box((10, 30, 40), body_pos) + _box((5, 10, 15), pad_pos)
    body_pos = (0, 0 if direction > 0 else 5, 0)
    pad_pos = (5, 10 if direction > 0 else 0, 8)
    return _box((40, 10, 30), body_pos) + _box((15, 5, 10), pad_pos)


def _states(boundary: str, *, axis: str = "z", direction: int = 1) -> set[str]:
    observed = _default_observers()["rectangular-pads"](_lone(axis, direction))
    assert len(observed) == 1
    return {fact.downstream[boundary] for fact in observed}


def test_versioned_pad_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("rectangular-pads",)
    assert len(corpus.cases) == 12
    assert sum(len(case.expected) for case in corpus.cases) == 12
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "ambiguous",
        "compound",
        "multiple",
        "multiple-equal",
        "negative",
        "overlapping-family",
        "positive",
        "principal-orientation",
        "signed-direction",
        "topology-order-variant",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)
    assert all(
        "'1970-01-01T00:00:00'"
        in (CORPUS.parent / case.provenance["fixture"]).read_text().splitlines()[3]
        for case in corpus.cases
    )


def test_real_pad_corpus_scores_all_layers_and_topology_order_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 12
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 36
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 48
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


@pytest.mark.parametrize("axis", tuple("xyz"))
@pytest.mark.parametrize("direction", (-1, 1))
def test_every_signed_principal_pad_boundary_is_observed(axis, direction) -> None:
    observed = _default_observers()["rectangular-pads"](_lone(axis, direction))

    assert len(observed) == 1
    assert observed[0].identity["axis"] == axis
    assert observed[0].identity["direction"] == direction
    assert set(observed[0].downstream.values()) == {"supported"}


def test_pad_observer_normalises_ir_bounds_and_frame_owned_axis() -> None:
    class FrameOwnedPad:
        frame = SimpleNamespace(axis="z")

        @staticmethod
        def bounds(axis):
            return {"x": (1, 4), "y": (2, 6), "z": (3, 8)}[axis]

    pad = FrameOwnedPad()

    assert _pad_axis(pad) == "z"
    assert _pad_bounds(pad) == {
        "x": (1.0, 4.0),
        "y": (2.0, 6.0),
        "z": (3.0, 8.0),
    }


def test_pad_ledger_tracks_five_physical_requirements_and_fails_closed() -> None:
    from draftwright import build_drawing
    from draftwright.linting.pad_coverage import pad_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    outcomes = pad_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )

    assert {outcome.parameter_id for outcome in outcomes} == {
        "pad_width.length",
        "pad_length.length",
        "pad_height.length",
        "location_pad.location.x",
        "location_pad.location.y",
    }
    assert {outcome.state for outcome in outcomes} == {"placed"}
    missing = pad_requirement_outcomes(recognition, drawing.model().features, AnnotationRegistry())
    assert len(missing) == 5
    assert {outcome.state for outcome in missing} == {"missing"}
    unverifiable = pad_requirement_outcomes(
        recognition,
        [feature for feature in drawing.model().features if feature.kind != "pad"],
        AnnotationRegistry(),
    )
    assert len(unverifiable) == 1
    assert (unverifiable[0].state, unverifiable[0].requirement_count) == (
        "unverifiable",
        5,
    )


def test_pad_ledger_rejects_foreign_results_malformed_and_duplicate_ir() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.pad_coverage import pad_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    recognition = build_raw_recognition_result(_lone())
    source = recognition.pads[0]

    class MalformedPad:
        kind = "pad"
        axis = source.axis
        direction = source.direction
        x0, x1 = source.x0, source.x1
        y0, y1 = source.y0, source.y1
        z0, z1 = source.z0, source.z1

        @staticmethod
        def parameters():
            raise TypeError("broken parameter contract")

    assert pad_requirement_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="run's RecognitionResult"):
        pad_requirement_outcomes(object(), (), AnnotationRegistry())
    malformed = pad_requirement_outcomes(recognition, (MalformedPad(),), AnnotationRegistry())
    assert len(malformed) == 1
    assert (malformed[0].state, malformed[0].requirement_count) == ("unverifiable", 5)

    drawing = build_drawing(_lone())
    feature = next(item for item in drawing.model().features if item.kind == "pad")
    duplicate = pad_requirement_outcomes(recognition, (feature, feature), AnnotationRegistry())
    assert len(duplicate) == 1
    assert (duplicate[0].state, duplicate[0].requirement_count) == ("unverifiable", 5)


@pytest.mark.parametrize(
    "corruption",
    (
        "raises",
        "parameter_ids",
        "parameter_values",
        "footprint_span",
        "missing_height_span",
        "wrong_height_span",
        "location_stem",
    ),
)
def test_pad_ledger_rejects_every_malformed_compiler_parameter_contract(corruption) -> None:
    from draftwright import build_drawing
    from draftwright.linting.pad_coverage import pad_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "pad")
    parameters = list(feature.parameters())

    if corruption == "parameter_ids":
        parameters[0] = replace(parameters[0], role="wrong_pad_width")
    elif corruption == "parameter_values":
        parameters[1] = replace(parameters[1], value=parameters[1].value + 1.0)
    elif corruption == "footprint_span":
        parameters[0] = replace(parameters[0], span=((0, 0, 0), (1, 0, 0)))
    elif corruption == "missing_height_span":
        parameters[2] = replace(parameters[2], span=None)
    elif corruption == "wrong_height_span":
        assert parameters[2].span is not None
        parameters[2] = replace(parameters[2], span=tuple(reversed(parameters[2].span)))

    class ParameterContractProxy:
        kind = "pad"
        LOCATION_STEM = "wrong_location_stem" if corruption == "location_stem" else "location_pad"

        def __getattr__(self, name):
            return getattr(feature, name)

        def parameters(self):
            if corruption == "raises":
                raise ValueError("invalid parameter contract")
            return parameters

    outcomes = pad_requirement_outcomes(
        recognition,
        (ParameterContractProxy(),),
        AnnotationRegistry(),
    )

    assert len(outcomes) == 1
    assert (outcomes[0].state, outcomes[0].requirement_count) == ("unverifiable", 5)


@pytest.mark.parametrize("axis", tuple("xyz"))
@pytest.mark.parametrize("corruption", ("frame_origin", "parameter_value", "axis_roles"))
def test_pad_correspondence_rejects_compiler_significant_ir_corruption(axis, corruption) -> None:
    from draftwright import build_drawing
    from draftwright.linting.pad_coverage import pad_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone(axis))
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "pad")
    if corruption == "frame_origin":
        corrupted = replace(
            feature,
            frame=replace(
                feature.frame,
                origin=tuple(value + 100.0 for value in feature.frame.origin),
            ),
        )
    elif corruption == "parameter_value":
        corrupted = replace(feature, length=feature.length + 7.0)
    else:
        long_bounds = feature.bounds(feature.long_axis)
        width_bounds = feature.bounds(feature.width_axis)
        corrupted = replace(
            feature,
            long_axis=feature.width_axis,
            width_axis=feature.long_axis,
            length=feature.width,
            width=feature.length,
            lo=width_bounds[0],
            hi=width_bounds[1],
            w_center=sum(long_bounds) / 2,
        )

    assert _pad_model_outcomes(tuple(recognition.pads), recognition, (corrupted,)) == ["unknown"]
    outcomes = pad_requirement_outcomes(
        recognition,
        (corrupted,),
        AnnotationRegistry(),
    )
    assert len(outcomes) == 1
    assert (outcomes[0].state, outcomes[0].requirement_count) == ("unverifiable", 5)


def test_pad_ledger_distinguishes_suppressed_inapplicable_and_dropped() -> None:
    from draftwright import build_drawing
    from draftwright.linting.issues import LintIssue
    from draftwright.linting.pad_coverage import pad_requirement_outcomes
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "pad")
    registry = AnnotationRegistry()
    registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="pad_height_dropped",
            measurement_ids=(DimensionId(feature, "pad_height.length"),),
            outcome_stage="placement",
        )
    )
    omissions = (
        SimpleNamespace(
            feature=feature,
            parameter_id="pad_width.length",
            authored=True,
            code="authored_omission",
        ),
        SimpleNamespace(
            feature=feature,
            parameter_id="location_pad.location.x",
            authored=False,
            code="pad_location_coincident_with_datum",
        ),
    )

    states = {
        outcome.parameter_id: outcome.state
        for outcome in pad_requirement_outcomes(
            recognition, drawing.model().features, registry, omissions
        )
    }
    assert states["pad_width.length"] == "suppressed"
    assert states["pad_height.length"] == "dropped"
    assert states["location_pad.location.x"] == "inapplicable"
    assert states["pad_length.length"] == "missing"


def test_pad_ledger_retains_structured_note_satisfaction_separately_from_ink() -> None:
    from draftwright import build_drawing
    from draftwright.linting.pad_coverage import pad_requirement_outcomes
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "pad")
    registry = AnnotationRegistry()
    registry.add(
        SimpleNamespace(),
        "pad_length_note",
        "front",
        feature=feature,
        satisfaction=DimensionId(feature, "pad_length.length"),
    )
    registry.add(
        SimpleNamespace(),
        "pad_location_note",
        "plan",
        feature=feature,
        satisfaction=DimensionId(feature, "location"),
    )

    states = {
        outcome.parameter_id: outcome.state
        for outcome in pad_requirement_outcomes(recognition, drawing.model().features, registry)
    }

    assert states["pad_length.length"] == "satisfied_by_structured_note"
    assert states["location_pad.location.x"] == "satisfied_by_structured_note"
    assert states["location_pad.location.y"] == "satisfied_by_structured_note"
    assert states["pad_width.length"] == "missing"


def test_pad_coverage_does_not_duplicate_a_placement_drop() -> None:
    from draftwright import build_drawing
    from draftwright.linting.issues import LintIssue
    from draftwright.linting.pad_coverage import lint_pad_coverage
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "pad")
    registry = AnnotationRegistry()
    registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="pad_height_dropped",
            measurement_ids=(DimensionId(feature, "pad_height.length"),),
            outcome_stage="placement",
        )
    )

    issues = lint_pad_coverage(
        drawing.part,
        recognition=recognition,
        features=drawing.model().features,
        registry=registry,
    )
    assert not [issue for issue in issues if issue.code == "pad_requirement_dropped"]
    assert [issue.code for issue in issues].count("pad_requirement_missing") == 4


def test_pad_observer_uses_one_build_owned_recognition_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_recognition_evidence
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_recognition_evidence", counted)
    assert _default_observers()["rectangular-pads"](_lone())
    assert calls == 1


def test_removing_pads_from_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_pads(self):
        model = original(self)
        return replace(model, features=[f for f in model.features if f.kind != "pad"])

    monkeypatch.setattr(Drawing, "model", without_pads)
    assert _states("ir_adapter") == {"unknown"}


def test_missing_per_pad_boundary_outcomes_fail_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_pad_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_observer_fails_closed_when_build_or_recognition_is_unavailable(monkeypatch) -> None:
    import draftwright.builder as builder
    from draftwright.evaluation.step_analysis import ObservationError

    def failed_build(*_args, **_kwargs):
        raise RuntimeError("probe")

    monkeypatch.setattr(builder, "build_drawing", failed_build)
    with pytest.raises(ObservationError, match="drawing build failed: probe"):
        _default_observers()["rectangular-pads"](_lone())


def test_observer_fails_closed_when_built_recognition_is_unavailable(monkeypatch) -> None:
    from draftwright.drawing import Drawing
    from draftwright.evaluation.step_analysis import ObservationError

    monkeypatch.setattr(Drawing, "recognition", lambda _self: None)
    with pytest.raises(ObservationError, match="recognition access failed"):
        _default_observers()["rectangular-pads"](_lone())


def test_observer_failure_cannot_pass_a_zero_pad_negative_case(monkeypatch) -> None:
    import draftwright.builder as builder

    corpus = load_corpus(CORPUS)
    negative = next(case for case in corpus.cases if not case.expected)

    def failed_build(*_args, **_kwargs):
        raise RuntimeError("negative-case probe")

    monkeypatch.setattr(builder, "build_drawing", failed_build)
    damaged = evaluate_step_corpus(replace(corpus, cases=(negative,)))

    assert damaged.complete_cases == damaged.conformant_cases == 0
    assert damaged.cases[0].outcome == "unknown"
    assert [(issue.layer, issue.family) for issue in damaged.cases[0].diagnostics] == [
        ("analysis", "rectangular-pads")
    ]


def test_corrupting_public_pad_declaration_loses_declaration_credit(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.pad

    def wrong_bounds(self, obj=None, **kw):
        kw["x1"] = float(kw["x1"]) + 1.0
        return original(self, obj, **kw)

    monkeypatch.setattr(Sheet, "pad", wrong_bounds)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_pad_lines_loses_generated_code_credit(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_pad_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if "sheet.pad(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_pad_lines)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"supported"}
    assert _states("generated_code") == {"unknown"}


def _annotation_for_parameter(drawing, parameter: str):
    if parameter.startswith("location_pad.location."):
        return next(
            name
            for name in drawing.annotations()
            if any(
                fact[1] == parameter
                for fact in getattr(drawing.registry.named(name), "covers_hole_locations", ())
            )
        )
    return next(
        name
        for name in drawing.annotations()
        if any(
            identity.parameter == parameter for identity in drawing.registry.measurement_of(name)
        )
    )


@pytest.mark.parametrize(
    "parameter",
    ("pad_width.length", "pad_height.length", "location_pad.location.x"),
)
def test_wrong_pad_measurement_ink_loses_drawing_credit(monkeypatch, parameter) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        annotation = drawing.registry.named(_annotation_for_parameter(drawing, parameter))
        annotation.label = "999"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_ink)
    assert _states("drawing_consumer") == {"unsupported"}


def test_swapping_valid_x_y_pad_location_values_loses_drawing_credit() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    x_name = _annotation_for_parameter(drawing, "location_pad.location.x")
    y_name = _annotation_for_parameter(drawing, "location_pad.location.y")
    x_annotation = drawing.registry.named(x_name)
    y_annotation = drawing.registry.named(y_name)
    x_annotation.label, y_annotation.label = y_annotation.label, x_annotation.label

    assert _pad_drawing_outcomes(tuple(recognition.pads), drawing) == ["unsupported"]


def test_pad_drawing_ignores_malformed_and_foreign_location_riders() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    name = _annotation_for_parameter(drawing, "location_pad.location.x")
    annotation = drawing.registry.named(name)
    foreign = next(feature for feature in drawing.model().features if feature.kind == "envelope")
    annotation.covers_hole_locations = (
        *annotation.covers_hole_locations,
        object(),
        (foreign, "location.x", (0, 0, 0)),
    )

    assert _pad_drawing_outcomes(tuple(recognition.pads), drawing) == ["supported"]


@pytest.mark.parametrize("corruption", ("missing_axis", "non_numeric"))
def test_invalid_directional_pad_approval_loses_drawing_credit(monkeypatch, corruption) -> None:
    from draftwright import build_drawing
    from draftwright.linting import evidence
    from draftwright.model.compiled import compile_dimensions

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    original = evidence.compiled_values

    if corruption == "missing_axis":
        x_name = _annotation_for_parameter(drawing, "location_pad.location.x")
        y_name = _annotation_for_parameter(drawing, "location_pad.location.y")
        drawing.registry.named(x_name).label = drawing.registry.named(y_name).label
        claims = evidence.verify_measurement_claims(
            drawing.registry, compile_dimensions(drawing.model())
        )
        assert any(claim.annotation == x_name and claim.state == "confirmed" for claim in claims)

    def corrupted_values(plan):
        values = original(plan)
        location_id = next(key for key in values if key.parameter == "location_pad.location")
        approvals = values[location_id]
        if corruption == "missing_axis":
            values[location_id] = tuple(
                approval for approval in approvals if approval.discriminator != "x"
            )
        else:
            values[location_id] = tuple(
                replace(approval, value_text="not-a-number")
                if approval.discriminator == "x"
                else approval
                for approval in approvals
            )
        return values

    monkeypatch.setattr(evidence, "compiled_values", corrupted_values)

    assert _pad_drawing_outcomes(tuple(recognition.pads), drawing) == ["unsupported"]


def test_severing_one_directional_pad_location_fact_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_x_location_fact(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = _annotation_for_parameter(drawing, "location_pad.location.x")
        drawing.registry.named(name).covers_hole_locations = ()
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_x_location_fact)
    assert _states("drawing_consumer") == {"unsupported"}


def test_severing_one_pad_measurement_claim_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_width_claim(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = _annotation_for_parameter(drawing, "pad_width.length")
        identity = drawing.registry.identity_of(name)
        drawing.registry.reapply(name, {**identity, "measurement": ()})
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_width_claim)
    assert _states("drawing_consumer") == {"unsupported"}


def test_deleting_provider_pads_cannot_shrink_independent_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def without_pads(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, pads=())

    monkeypatch.setattr(analysis, "_result_from_evidence", without_pads)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 12
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


def _weaken_parameter(pad, parameter: str):
    bounds = {
        axis: [float(getattr(pad, f"{axis}0")), float(getattr(pad, f"{axis}1"))] for axis in "xyz"
    }
    if parameter == "height":
        terminal = 1 if pad.direction > 0 else 0
        bounds[pad.axis][terminal] += 1.0 if pad.direction > 0 else -1.0
    else:
        long_axis, width_axis = _PLANE_AXES[pad.axis]
        target = width_axis if parameter == "width" else long_axis
        bounds[target][0] -= 0.5
        bounds[target][1] += 0.5
    return replace(
        pad,
        **{f"{axis}{index}": bounds[axis][index] for axis in "xyz" for index in (0, 1)},
    )


@pytest.mark.parametrize("parameter", ("width", "length", "height"))
def test_weakening_provider_measurements_reduces_parameter_fidelity(
    monkeypatch, parameter
) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def weakened_pads(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(
            result,
            pads=tuple(_weaken_parameter(pad, parameter) for pad in result.pads),
        )

    monkeypatch.setattr(analysis, "_result_from_evidence", weakened_pads)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.passed == 24
    assert damaged.parameter_fidelity.total == 36


@pytest.mark.parametrize("field", ("direction", "attachment"))
def test_weakening_provider_identity_reduces_detection_recall(monkeypatch, field) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def weakened_pads(*args, **kwargs):
        result = original(*args, **kwargs)
        values = []
        for pad in result.pads:
            if field == "direction":
                values.append(replace(pad, direction=-pad.direction))
                continue
            shift_axis = _PLANE_AXES[pad.axis][0]
            values.append(
                replace(
                    pad,
                    **{
                        f"{shift_axis}0": getattr(pad, f"{shift_axis}0") + 1.0,
                        f"{shift_axis}1": getattr(pad, f"{shift_axis}1") + 1.0,
                    },
                )
            )
        return replace(result, pads=tuple(values))

    monkeypatch.setattr(analysis, "_result_from_evidence", weakened_pads)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 0.0
    assert damaged.detection.missed == 12
    assert damaged.detection.false_positives == 12


def test_deleting_pad_declaration_cannot_shrink_quality_denominator() -> None:
    from draftwright import Sheet, build_drawing

    complete = build_drawing(_lone())
    sparse = Sheet(_lone())
    envelope = sparse.envelope()
    sparse.dimension(envelope, "width.length")

    complete_summary = complete.lint_summary()
    sparse_summary = sparse.build().lint_summary()
    complete_quality = complete_summary["quality"]["completeness"]
    sparse_quality = sparse_summary["quality"]["completeness"]
    assert complete_quality["requirements"] == sparse_quality["requirements"] == 5
    assert complete_quality["by_family"]["pads"] == 5
    assert complete_quality["placed"] == 5
    assert sparse_quality["unverifiable"] == 5
    assert complete_quality["audited_score"] == 1.0
    assert sparse_quality["audited_score"] == 0.0
    assert sparse_summary["geometry_issues"] == 2
    assert {
        issue["code"] for issue in sparse_summary["issues"] if issue["code"].startswith("pad_")
    } == {"pad_footprint_not_defined", "pad_requirement_unverifiable"}
