"""#1421 — rectangular blind-slot completeness uses independent physical facts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest
from b123d_recognisers import build_raw_recognition_result
from build123d import Align, Axis, Box, Pos

from draftwright import Sheet, build_drawing
from draftwright.linting.issues import LintIssue
from draftwright.linting.rectangular_blind_slot_coverage import (
    lint_rectangular_blind_slot_coverage,
    rectangular_blind_slot_key,
    rectangular_blind_slot_requirement_outcomes,
)
from draftwright.model import DimensionId, Frame
from draftwright.model.compiled import compile_dimensions
from draftwright.registry import AnnotationRegistry
from draftwright.sheet_emit import _feature_line

_PARAMETERS = (
    "rectangular_blind_slot_width.length",
    "rectangular_blind_slot_length.length",
    "rectangular_blind_slot_depth.length",
)


def _part(*, width=10, depth=5, length=20):
    stock = Box(30, 20, 40, align=(Align.CENTER, Align.CENTER, Align.MIN))
    tool = Pos(0, 10 - depth, 0) * Box(
        width, depth, length, align=(Align.CENTER, Align.MIN, Align.MIN)
    )
    return stock - tool


@dataclass(frozen=True)
class _Case:
    name: str
    part: object
    axis: str
    open_sign: int
    width_axis: str
    depth_axis: str
    depth_sign: int
    width: float
    length: float
    depth: float
    at: tuple[float, float, float]

    def declaration(self) -> dict:
        return {
            "axis": self.axis,
            "open_sign": self.open_sign,
            "length": self.length,
            "width_axis": self.width_axis,
            "depth_axis": self.depth_axis,
            "depth_sign": self.depth_sign,
            "width": self.width,
            "depth": self.depth,
            "at": self.at,
        }


class _FeatureProxy:
    """Keep structural facts fixed while independently varying compiler parameters."""

    def __init__(self, feature, parameters) -> None:
        self._feature = feature
        self._parameters = tuple(parameters)

    def __getattr__(self, name):
        return getattr(self._feature, name)

    def parameters(self):
        return list(self._parameters)


# Every expected fact is authored here rather than derived from provider output or Draftwright
# IR.  The six physical axis/open-side orientations plus an independently sized specimen make
# recognition loss, sign swaps and parameter-role swaps visible to the benchmark.
_CASES = (
    _Case("z-negative", _part(), "z", -1, "x", "y", 1, 10, 20, 5, (0, 7.5, 10)),
    _Case(
        "z-sized", _part(width=6, depth=4, length=15), "z", -1, "x", "y", 1, 6, 15, 4, (0, 8, 7.5)
    ),
    _Case(
        "z-positive",
        _part().rotate(Axis.X, 180),
        "z",
        1,
        "x",
        "y",
        -1,
        10,
        20,
        5,
        (0, -7.5, -10),
    ),
    _Case(
        "x-negative",
        _part().rotate(Axis.Y, 90),
        "x",
        -1,
        "z",
        "y",
        1,
        10,
        20,
        5,
        (10, 7.5, 0),
    ),
    _Case(
        "x-positive",
        _part().rotate(Axis.Y, -90),
        "x",
        1,
        "z",
        "y",
        1,
        10,
        20,
        5,
        (-10, 7.5, 0),
    ),
    _Case(
        "y-positive",
        _part().rotate(Axis.X, 90),
        "y",
        1,
        "x",
        "z",
        1,
        10,
        20,
        5,
        (0, -10, 7.5),
    ),
    _Case(
        "y-negative",
        _part().rotate(Axis.X, -90),
        "y",
        -1,
        "x",
        "z",
        -1,
        10,
        20,
        5,
        (0, 10, -7.5),
    ),
)


def _source(case: _Case):
    recognition = build_raw_recognition_result(case.part)
    assert recognition.slots == recognition.pockets == recognition.channels == ()
    assert len(recognition.rectangular_blind_slots) == 1
    return recognition, recognition.rectangular_blind_slots[0]


def _feature(drawing):
    features = [
        feature for feature in drawing.model().features if feature.kind == "rectangular_blind_slot"
    ]
    assert len(features) == 1
    return features[0]


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
def test_independent_corpus_reaches_recognition_ir_and_finished_measurements(case) -> None:
    recognition, source = _source(case)
    assert (
        source.axis,
        source.open_sign,
        source.width_axis,
        source.depth_axis,
        source.depth_sign,
        source.width,
        source.length,
        source.depth,
        source.at,
    ) == (
        case.axis,
        case.open_sign,
        case.width_axis,
        case.depth_axis,
        case.depth_sign,
        case.width,
        case.length,
        case.depth,
        case.at,
    )

    drawing = build_drawing(case.part)
    feature = _feature(drawing)
    assert (
        feature.axis,
        feature.open_sign,
        feature.width_axis,
        feature.depth_axis,
        feature.depth_sign,
        feature.width,
        feature.length,
        feature.depth,
        feature.frame.origin,
    ) == (
        case.axis,
        case.open_sign,
        case.width_axis,
        case.depth_axis,
        case.depth_sign,
        case.width,
        case.length,
        case.depth,
        case.at,
    )
    outcomes = rectangular_blind_slot_requirement_outcomes(
        recognition, drawing.model().features, drawing.registry
    )
    assert [(outcome.parameter_id, outcome.state) for outcome in outcomes] == [
        (parameter, "placed") for parameter in _PARAMETERS
    ]
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code.startswith("rectangular_blind_slot_requirement_")
    ]
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["by_family"]["rectangular_blind_slots"] == 3
    assert "rectangular_blind_slots" not in completeness["unscored_recognized_families"]


def test_corpus_denominator_does_not_shrink_when_recognition_is_removed() -> None:
    expected_physical_requirements = 3 * len(_CASES)
    observed = 0
    damaged = 0
    for case in _CASES:
        recognition, _source_record = _source(case)
        observed += 3 * len(recognition.rectangular_blind_slots)
        weakened = replace(recognition, rectangular_blind_slots=())
        damaged += len(
            rectangular_blind_slot_requirement_outcomes(weakened, (), AnnotationRegistry())
        )

    assert expected_physical_requirements == observed == 21
    assert damaged == 0
    assert damaged < expected_physical_requirements


def test_declared_and_executed_generated_sheet_paths_earn_the_same_outcomes() -> None:
    case = _CASES[1]
    recognition, _source_record = _source(case)

    declared = Sheet(case.part).authored_dimensions()
    declared_handle = declared.rectangular_blind_slot(**case.declaration())
    for parameter in _PARAMETERS:
        declared.dimension(declared_handle, parameter)
    declared_drawing = declared.build()

    emitted = _feature_line(declared.model().features[0])
    replay = Sheet(case.part).authored_dimensions()
    replay_handle = eval(emitted, {"sheet": replay})  # noqa: S307
    for parameter in _PARAMETERS:
        replay.dimension(replay_handle, parameter)
    replay_drawing = replay.build()

    signatures = []
    for drawing in (declared_drawing, replay_drawing):
        feature = _feature(drawing)
        outcomes = rectangular_blind_slot_requirement_outcomes(
            recognition, drawing.model().features, drawing.registry
        )
        signatures.append(
            (
                feature,
                tuple((outcome.parameter_id, outcome.state) for outcome in outcomes),
                tuple(
                    sorted(
                        key["parameter_id"]
                        for name in drawing.annotations_of(feature)
                        for key in drawing.measurement_keys(name)
                    )
                ),
            )
        )
    assert signatures[0] == signatures[1]
    assert signatures[0][1] == tuple((parameter, "placed") for parameter in _PARAMETERS)
    assert signatures[0][2] == tuple(sorted(_PARAMETERS))


def test_authored_subset_is_suppressed_not_silently_complete() -> None:
    case = _CASES[0]
    recognition, _source_record = _source(case)
    sheet = Sheet(case.part).authored_dimensions()
    handle = sheet.rectangular_blind_slot(**case.declaration())
    sheet.dimension(handle, _PARAMETERS[0])
    drawing = sheet.build()

    outcomes = rectangular_blind_slot_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )
    assert {outcome.parameter_id: outcome.state for outcome in outcomes} == {
        _PARAMETERS[0]: "placed",
        _PARAMETERS[1]: "suppressed",
        _PARAMETERS[2]: "suppressed",
    }
    issues = [
        issue
        for issue in drawing.lint()
        if issue.code.startswith("rectangular_blind_slot_requirement_")
    ]
    assert len(issues) == 2
    assert {issue.code for issue in issues} == {"rectangular_blind_slot_requirement_suppressed"}
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["by_family"]["rectangular_blind_slots"] == 3
    assert completeness["placed"] >= 1
    assert completeness["suppressed"] >= 2


def test_ledger_distinguishes_every_engine_outcome_and_duplicate_sources() -> None:
    drawing = build_drawing(_CASES[0].part)
    recognition = drawing.recognition()
    assert recognition is not None
    feature = _feature(drawing)

    assert [
        outcome.state
        for outcome in rectangular_blind_slot_requirement_outcomes(
            recognition, drawing.model().features, drawing.registry
        )
    ] == ["placed", "placed", "placed"]

    empty = AnnotationRegistry()
    assert [
        outcome.state
        for outcome in rectangular_blind_slot_requirement_outcomes(
            recognition, drawing.model().features, empty
        )
    ] == ["missing", "missing", "missing"]

    omission = SimpleNamespace(feature=feature, parameter_id=_PARAMETERS[0], authored=True)
    assert [
        outcome.state
        for outcome in rectangular_blind_slot_requirement_outcomes(
            recognition, drawing.model().features, empty, (omission,)
        )
    ] == ["suppressed", "missing", "missing"]

    dropped = AnnotationRegistry()
    dropped.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="rectangular_blind_slot_dropped",
            measurement_ids=(DimensionId(feature, _PARAMETERS[1]),),
            outcome_stage="placement",
        )
    )
    assert [
        outcome.state
        for outcome in rectangular_blind_slot_requirement_outcomes(
            recognition, drawing.model().features, dropped
        )
    ] == ["missing", "dropped", "missing"]

    satisfied = AnnotationRegistry()
    satisfied.add(
        object(),
        "structured_note",
        "front",
        feature=feature,
        satisfaction=DimensionId(feature, _PARAMETERS[2]),
    )
    assert [
        outcome.state
        for outcome in rectangular_blind_slot_requirement_outcomes(
            recognition, drawing.model().features, satisfied
        )
    ] == ["missing", "missing", "satisfied_by_structured_note"]

    manual = AnnotationRegistry()
    manual.add(object(), "unowned_prose", "front", feature=feature)
    assert {
        outcome.state
        for outcome in rectangular_blind_slot_requirement_outcomes(
            recognition, drawing.model().features, manual
        )
    } == {"unverifiable"}

    duplicated = replace(
        recognition,
        rectangular_blind_slots=(
            recognition.rectangular_blind_slots[0],
            recognition.rectangular_blind_slots[0],
        ),
    )
    ambiguous = rectangular_blind_slot_requirement_outcomes(
        duplicated, drawing.model().features, empty
    )
    assert len(ambiguous) == 6
    assert {outcome.state for outcome in ambiguous} == {"unverifiable"}


def test_parameter_correspondence_is_identity_based_not_sibling_order() -> None:
    drawing = build_drawing(_CASES[0].part)
    recognition = drawing.recognition()
    assert recognition is not None
    feature = _feature(drawing)
    reordered = _FeatureProxy(feature, reversed(feature.parameters()))
    registry = AnnotationRegistry()
    registry.add(
        object(),
        "reordered-parameters",
        "front",
        feature=reordered,
        measurement=tuple(DimensionId(reordered, parameter) for parameter in _PARAMETERS),
    )

    assert [
        outcome.state
        for outcome in rectangular_blind_slot_requirement_outcomes(
            recognition, (reordered,), registry
        )
    ] == ["placed", "placed", "placed"]


def test_correspondence_rejects_near_neighbours_and_malformed_ir_parameters() -> None:
    drawing = build_drawing(_CASES[0].part)
    recognition = drawing.recognition()
    assert recognition is not None
    feature = _feature(drawing)
    shifted_origin = list(feature.frame.origin)
    shifted_origin["xyz".index(feature.axis)] += 0.002
    shifted = replace(feature, frame=Frame(tuple(shifted_origin), feature.axis))
    registry = AnnotationRegistry()
    registry.add(
        object(),
        "near-neighbour",
        "front",
        feature=shifted,
        measurement=tuple(DimensionId(shifted, parameter) for parameter in _PARAMETERS),
    )
    assert {
        outcome.state
        for outcome in rectangular_blind_slot_requirement_outcomes(
            recognition, (shifted,), registry
        )
    } == {"unverifiable"}

    wrong_size = replace(feature, width=feature.width + 1)
    assert {
        outcome.state
        for outcome in rectangular_blind_slot_requirement_outcomes(
            recognition, (wrong_size,), AnnotationRegistry()
        )
    } == {"unverifiable"}

    _recognition, source = _source(_CASES[0])
    parameters = tuple(feature.parameters())
    assert parameters[0].span is not None
    shifted_span_start = list(parameters[0].span[0])
    shifted_span_start[0] += 0.002
    malformed_parameters = (
        (
            replace(parameters[0], role="rectangular_blind_slot_unknown"),
            *parameters[1:],
        ),
        (replace(parameters[0], value=parameters[0].value + 1), *parameters[1:]),
        (
            replace(
                parameters[0],
                span=(tuple(shifted_span_start), parameters[0].span[1]),
            ),
            *parameters[1:],
        ),
        (*parameters, parameters[0]),
    )
    for malformed in malformed_parameters:
        proxy = _FeatureProxy(feature, malformed)
        assert rectangular_blind_slot_key(proxy, require_frame=True) == rectangular_blind_slot_key(
            source
        )
        assert {
            outcome.state
            for outcome in rectangular_blind_slot_requirement_outcomes(
                recognition, (proxy,), AnnotationRegistry()
            )
        } == {"unverifiable"}

    assert {
        outcome.state
        for outcome in rectangular_blind_slot_requirement_outcomes(
            recognition, (feature, feature), AnnotationRegistry()
        )
    } == {"unverifiable"}


def test_lint_does_not_duplicate_a_recorded_placement_drop() -> None:
    drawing = build_drawing(_CASES[0].part)
    recognition = drawing.recognition()
    assert recognition is not None
    feature = _feature(drawing)
    registry = AnnotationRegistry()
    registry.record_issue(
        LintIssue(
            "warning",
            "synthetic joint-solver drop",
            code="rectangular_blind_slot_dropped",
            measurement_ids=tuple(DimensionId(feature, parameter) for parameter in _PARAMETERS),
            outcome_stage="placement",
        )
    )

    assert (
        lint_rectangular_blind_slot_coverage(
            _CASES[0].part,
            recognition=recognition,
            features=drawing.model().features,
            registry=registry,
        )
        == []
    )


def test_outcome_boundary_rejects_a_foreign_aggregate() -> None:
    with pytest.raises(TypeError, match="run's RecognitionResult"):
        rectangular_blind_slot_requirement_outcomes(
            object(),
            (),
            AnnotationRegistry(),  # type: ignore[arg-type]
        )

    recognition, _source_record = _source(_CASES[0])
    mutable = replace(
        recognition,
        rectangular_blind_slots=list(recognition.rectangular_blind_slots),  # type: ignore[arg-type]
    )
    with pytest.raises(TypeError, match="immutable tuple"):
        rectangular_blind_slot_requirement_outcomes(mutable, (), AnnotationRegistry())


def test_correspondence_key_rejects_every_malformed_released_fact() -> None:
    _recognition, source = _source(_CASES[0])

    class _BadFloat(float):
        def __float__(self):
            raise ValueError

    malformed_sources = (
        replace(source, width=_BadFloat(1)),
        replace(source, width=float("inf")),
        replace(source, width=True),
        replace(source, width=0),
        replace(source, at=list(source.at)),
        replace(source, width_axis=source.axis),
        replace(source, open_sign=True),
    )
    for malformed in malformed_sources:
        with pytest.raises(ValueError):
            rectangular_blind_slot_key(malformed)

    malformed_recognition = replace(_recognition, rectangular_blind_slots=(malformed_sources[4],))
    malformed_outcomes = rectangular_blind_slot_requirement_outcomes(
        malformed_recognition, (), AnnotationRegistry()
    )
    assert len(malformed_outcomes) == 3
    assert all(outcome.state == "unverifiable" for outcome in malformed_outcomes)

    drawing = build_drawing(_CASES[0].part)
    feature = _feature(drawing)
    mismatched_frame = SimpleNamespace(
        axis=feature.axis,
        open_sign=feature.open_sign,
        width_axis=feature.width_axis,
        depth_axis=feature.depth_axis,
        depth_sign=feature.depth_sign,
        width=feature.width,
        length=feature.length,
        depth=feature.depth,
        frame=Frame(feature.frame.origin, feature.width_axis),
    )
    with pytest.raises(ValueError):
        rectangular_blind_slot_key(mismatched_frame, require_frame=True)
