"""Blind-slot recognition and completeness contracts across physical recess families (#1421)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest
from _section_recess_cases import corrupt_recess, declaration_fields
from build123d import (
    Align,
    Axis,
    Box,
    BuildLine,
    BuildSketch,
    Line,
    Pos,
    RadiusArc,
    Vector,
    extrude,
    make_face,
)
from quiddity import build_raw_recognition_result

from draftwright import Sheet, build_drawing
from draftwright.linting.issues import LintIssue
from draftwright.linting.rectangular_blind_slot_coverage import (
    lint_rectangular_blind_slot_coverage,
    rectangular_blind_slot_key,
    rectangular_blind_slot_requirement_outcomes,
)
from draftwright.linting.round_bottom_blind_slot_coverage import (
    lint_round_bottom_blind_slot_coverage,
    round_bottom_blind_slot_key,
    round_bottom_blind_slot_requirement_outcomes,
)
from draftwright.model import DimensionId, Frame
from draftwright.model.compiled import compile_dimensions
from draftwright.registry import AnnotationRegistry
from draftwright.sheet_emit import _feature_line


def _rect_part(*, width=10, depth=5, length=20):
    stock = Box(30, 20, 40, align=(Align.CENTER, Align.CENTER, Align.MIN))
    tool = Pos(0, 10 - depth, 0) * Box(
        width, depth, length, align=(Align.CENTER, Align.MIN, Align.MIN)
    )
    return stock - tool


@dataclass(frozen=True)
class _RectCase:
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


def _round_part(*, flat_width=4, radius=3, length=20):
    half_width = flat_width / 2 + radius
    half_flat = flat_width / 2
    with BuildLine() as boundary:
        Line((-half_width, 0), (half_width, 0))
        RadiusArc((half_width, 0), (half_flat, -radius), radius)
        Line((half_flat, -radius), (-half_flat, -radius))
        RadiusArc((-half_flat, -radius), (-half_width, 0), radius)
    with BuildSketch() as sketch:
        make_face(boundary.line)
    # Keep the cutter terminal at the stock mid-plane while varying the independently
    # authored run: otherwise the provider intentionally declines a non-canonical fixture.
    stock = Pos(0, -5, 0) * Box(30, 10, 2 * length)
    tool = extrude(sketch.sketch, amount=length, dir=Vector(0, 0, 1))
    return stock - tool


@dataclass(frozen=True)
class _RoundCase:
    name: str
    part: object
    axis: str
    open_sign: int
    width_axis: str
    depth_axis: str
    depth_sign: int
    length: float
    radius: float
    flat_width: float
    at: tuple[float, float, float]

    def declaration(self) -> dict:
        return {
            "axis": self.axis,
            "open_sign": self.open_sign,
            "length": self.length,
            "width_axis": self.width_axis,
            "depth_axis": self.depth_axis,
            "depth_sign": self.depth_sign,
            "radius": self.radius,
            "flat_width": self.flat_width,
            "at": self.at,
        }


# Expected facts are authored here, not inferred from recognition or the IR. Both families
# have six axis/open-side orientations and an independently sized specimen.

_RECT_CASES = (
    _RectCase("z-negative", _rect_part(), "z", -1, "x", "y", 1, 10, 20, 5, (0, 7.5, 10)),
    _RectCase(
        "z-sized",
        _rect_part(width=6, depth=4, length=15),
        "z",
        -1,
        "x",
        "y",
        1,
        6,
        15,
        4,
        (0, 8, 7.5),
    ),
    _RectCase(
        "z-positive",
        _rect_part().rotate(Axis.X, 180),
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
    _RectCase(
        "x-negative",
        _rect_part().rotate(Axis.Y, 90),
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
    _RectCase(
        "x-positive",
        _rect_part().rotate(Axis.Y, -90),
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
    _RectCase(
        "y-positive",
        _rect_part().rotate(Axis.X, 90),
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
    _RectCase(
        "y-negative",
        _rect_part().rotate(Axis.X, -90),
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

_ROUND_CASES = (
    _RoundCase("z-positive", _round_part(), "z", 1, "x", "y", 1, 20, 3, 4, (0, -1.5, 10)),
    _RoundCase(
        "z-sized",
        _round_part(flat_width=5, radius=2, length=15),
        "z",
        1,
        "x",
        "y",
        1,
        15,
        2,
        5,
        (0, -1, 7.5),
    ),
    _RoundCase(
        "z-negative",
        _round_part().rotate(Axis.X, 180),
        "z",
        -1,
        "x",
        "y",
        -1,
        20,
        3,
        4,
        (0, 1.5, -10),
    ),
    _RoundCase(
        "x-positive",
        _round_part().rotate(Axis.Y, 90),
        "x",
        1,
        "z",
        "y",
        1,
        20,
        3,
        4,
        (10, -1.5, 0),
    ),
    _RoundCase(
        "x-negative",
        _round_part().rotate(Axis.Y, -90),
        "x",
        -1,
        "z",
        "y",
        1,
        20,
        3,
        4,
        (-10, -1.5, 0),
    ),
    _RoundCase(
        "y-negative",
        _round_part().rotate(Axis.X, 90),
        "y",
        -1,
        "x",
        "z",
        1,
        20,
        3,
        4,
        (0, -10, -1.5),
    ),
    _RoundCase(
        "y-positive",
        _round_part().rotate(Axis.X, -90),
        "y",
        1,
        "x",
        "z",
        -1,
        20,
        3,
        4,
        (0, 10, 1.5),
    ),
)


class _FeatureProxy:
    """Keep structural facts fixed while varying compiler parameters."""

    def __init__(self, feature, parameters) -> None:
        self._feature = feature
        self._parameters = tuple(parameters)

    def __getattr__(self, name):
        return getattr(self._feature, name)

    def parameters(self):
        return list(self._parameters)


@dataclass(frozen=True)
class _Family:
    kind: str
    plural: str
    cases: tuple[_RectCase | _RoundCase, ...]
    parameters: tuple[str, str, str]
    outcomes: object
    key: object
    lint: object
    size_to_damage: str

    @property
    def dropped_code(self) -> str:
        return f"{self.kind}_dropped"

    @property
    def suppressed_code(self) -> str:
        return f"{self.kind}_requirement_suppressed"


_FAMILIES = (
    _Family(
        "rectangular_blind_slot",
        "rectangular_blind_slots",
        _RECT_CASES,
        (
            "rectangular_blind_slot_width.length",
            "rectangular_blind_slot_length.length",
            "rectangular_blind_slot_depth.length",
        ),
        rectangular_blind_slot_requirement_outcomes,
        rectangular_blind_slot_key,
        lint_rectangular_blind_slot_coverage,
        "width",
    ),
    _Family(
        "round_bottom_blind_slot",
        "round_bottom_blind_slots",
        _ROUND_CASES,
        (
            "round_bottom_blind_slot_length.length",
            "round_bottom_blind_slot_flat_width.length",
            "round_bottom_blind_slot_radius.radius",
        ),
        round_bottom_blind_slot_requirement_outcomes,
        round_bottom_blind_slot_key,
        lint_round_bottom_blind_slot_coverage,
        "flat_width",
    ),
)


def _source(case):
    recognition = build_raw_recognition_result(case.part)
    assert recognition.slots == ()
    assert len(recognition.section_recesses) == 1
    return recognition, recognition.section_recesses[0]


def _feature(drawing, family):
    features = [feature for feature in drawing.model().features if feature.kind == family.kind]
    assert len(features) == 1
    return features[0]


@pytest.mark.parametrize(
    ("family", "case"),
    [
        pytest.param(family, case, id=f"{family.kind}-{case.name}")
        for family in _FAMILIES
        for case in family.cases
    ],
)
def test_independent_corpus_reaches_recognition_ir_and_finished_measurements_issue_1421(
    family, case
) -> None:
    recognition, source = _source(case)
    expected = case.declaration()
    assert declaration_fields(source, family.kind) == expected

    drawing = build_drawing(case.part)
    feature = _feature(drawing, family)
    assert {
        name: feature.frame.origin if name == "at" else getattr(feature, name) for name in expected
    } == expected
    outcomes = family.outcomes(recognition, drawing.model().features, drawing.registry)
    assert [(outcome.parameter_id, outcome.state) for outcome in outcomes] == [
        (parameter, "placed") for parameter in family.parameters
    ]
    assert not [
        issue for issue in drawing.lint() if issue.code.startswith(f"{family.kind}_requirement_")
    ]
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["by_family"][family.plural] == 3
    assert family.plural not in completeness["unscored_recognized_families"]


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda family: family.kind)
def test_corpus_denominator_does_not_shrink_when_recognition_is_removed_issue_1421(
    family,
) -> None:
    expected_physical_requirements = 3 * len(family.cases)
    observed = 0
    damaged = 0
    for case in family.cases:
        recognition, _source_record = _source(case)
        observed += 3 * len(recognition.section_recesses)
        weakened = replace(recognition, section_recesses=())
        damaged += len(family.outcomes(weakened, (), AnnotationRegistry()))

    assert expected_physical_requirements == observed == 21
    assert damaged == 0
    assert damaged < expected_physical_requirements


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda family: family.kind)
def test_declared_and_executed_generated_sheet_paths_earn_the_same_outcomes_issue_1421(
    family,
) -> None:
    case = family.cases[1]
    recognition, _source_record = _source(case)

    declared = Sheet(case.part).authored_dimensions()
    declared_handle = getattr(declared, family.kind)(**case.declaration())
    for parameter in family.parameters:
        declared.dimension(declared_handle, parameter)
    declared_drawing = declared.build()

    emitted = _feature_line(declared.model().features[0])
    replay = Sheet(case.part).authored_dimensions()
    replay_handle = eval(emitted, {"sheet": replay})  # noqa: S307
    for parameter in family.parameters:
        replay.dimension(replay_handle, parameter)
    replay_drawing = replay.build()

    signatures = []
    for drawing in (declared_drawing, replay_drawing):
        feature = _feature(drawing, family)
        outcomes = family.outcomes(recognition, drawing.model().features, drawing.registry)
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
    assert signatures[0][1] == tuple((parameter, "placed") for parameter in family.parameters)
    assert signatures[0][2] == tuple(sorted(family.parameters))


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda family: family.kind)
def test_authored_subset_is_suppressed_not_silently_complete_issue_1421(family) -> None:
    case = family.cases[0]
    recognition, _source_record = _source(case)
    sheet = Sheet(case.part).authored_dimensions()
    handle = getattr(sheet, family.kind)(**case.declaration())
    sheet.dimension(handle, family.parameters[0])
    drawing = sheet.build()

    outcomes = family.outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )
    assert {outcome.parameter_id: outcome.state for outcome in outcomes} == {
        family.parameters[0]: "placed",
        family.parameters[1]: "suppressed",
        family.parameters[2]: "suppressed",
    }
    issues = [
        issue for issue in drawing.lint() if issue.code.startswith(f"{family.kind}_requirement_")
    ]
    assert len(issues) == 2
    assert {issue.code for issue in issues} == {family.suppressed_code}
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["by_family"][family.plural] == 3
    assert completeness["placed"] >= 1
    assert completeness["suppressed"] >= 2


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda family: family.kind)
def test_ledger_distinguishes_every_engine_outcome_and_duplicate_sources_issue_1421(
    family,
) -> None:
    drawing = build_drawing(family.cases[0].part)
    recognition = drawing.recognition()
    assert recognition is not None
    feature = _feature(drawing, family)

    assert [
        outcome.state
        for outcome in family.outcomes(recognition, drawing.model().features, drawing.registry)
    ] == ["placed", "placed", "placed"]

    empty = AnnotationRegistry()
    assert [
        outcome.state for outcome in family.outcomes(recognition, drawing.model().features, empty)
    ] == ["missing", "missing", "missing"]

    omission = SimpleNamespace(feature=feature, parameter_id=family.parameters[0], authored=True)
    assert [
        outcome.state
        for outcome in family.outcomes(recognition, drawing.model().features, empty, (omission,))
    ] == ["suppressed", "missing", "missing"]

    dropped = AnnotationRegistry()
    dropped.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code=family.dropped_code,
            measurement_ids=(DimensionId(feature, family.parameters[1]),),
            outcome_stage="placement",
        )
    )
    assert [
        outcome.state
        for outcome in family.outcomes(recognition, drawing.model().features, dropped)
    ] == ["missing", "dropped", "missing"]

    satisfied = AnnotationRegistry()
    satisfied.add(
        object(),
        "structured_note",
        "front",
        feature=feature,
        satisfaction=DimensionId(feature, family.parameters[2]),
    )
    assert [
        outcome.state
        for outcome in family.outcomes(recognition, drawing.model().features, satisfied)
    ] == ["missing", "missing", "satisfied_by_structured_note"]

    manual = AnnotationRegistry()
    manual.add(object(), "unowned_prose", "front", feature=feature)
    assert {
        outcome.state for outcome in family.outcomes(recognition, drawing.model().features, manual)
    } == {"unverifiable"}

    duplicated = replace(
        recognition,
        section_recesses=(recognition.section_recesses[0], recognition.section_recesses[0]),
    )
    ambiguous = family.outcomes(duplicated, drawing.model().features, empty)
    assert len(ambiguous) == 6
    assert {outcome.state for outcome in ambiguous} == {"unverifiable"}


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda family: family.kind)
def test_parameter_correspondence_is_identity_based_not_sibling_order_issue_1421(
    family,
) -> None:
    drawing = build_drawing(family.cases[0].part)
    recognition = drawing.recognition()
    assert recognition is not None
    feature = _feature(drawing, family)
    reordered = _FeatureProxy(feature, reversed(feature.parameters()))
    registry = AnnotationRegistry()
    registry.add(
        object(),
        "reordered-parameters",
        "front",
        feature=reordered,
        measurement=tuple(DimensionId(reordered, parameter) for parameter in family.parameters),
    )

    assert [outcome.state for outcome in family.outcomes(recognition, (reordered,), registry)] == [
        "placed",
        "placed",
        "placed",
    ]


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda family: family.kind)
def test_correspondence_rejects_near_neighbours_and_malformed_ir_parameters_issue_1421(
    family,
) -> None:
    drawing = build_drawing(family.cases[0].part)
    recognition = drawing.recognition()
    assert recognition is not None
    feature = _feature(drawing, family)
    shifted_origin = list(feature.frame.origin)
    shifted_origin["xyz".index(feature.axis)] += 0.002
    shifted = replace(feature, frame=Frame(tuple(shifted_origin), feature.axis))
    registry = AnnotationRegistry()
    registry.add(
        object(),
        "near-neighbour",
        "front",
        feature=shifted,
        measurement=tuple(DimensionId(shifted, parameter) for parameter in family.parameters),
    )
    assert {outcome.state for outcome in family.outcomes(recognition, (shifted,), registry)} == {
        "unverifiable"
    }

    wrong_size = replace(
        feature, **{family.size_to_damage: getattr(feature, family.size_to_damage) + 1}
    )
    assert {
        outcome.state
        for outcome in family.outcomes(recognition, (wrong_size,), AnnotationRegistry())
    } == {"unverifiable"}

    _recognition, source = _source(family.cases[0])
    parameters = tuple(feature.parameters())
    assert parameters[0].span is not None
    shifted_span_start = list(parameters[0].span[0])
    shifted_span_start[0] += 0.002
    malformed_parameters = (
        (replace(parameters[0], role=f"{family.kind}_unknown"), *parameters[1:]),
        (replace(parameters[0], value=parameters[0].value + 1), *parameters[1:]),
        (
            replace(parameters[0], span=(tuple(shifted_span_start), parameters[0].span[1])),
            *parameters[1:],
        ),
        (*parameters, parameters[0]),
    )
    for malformed in malformed_parameters:
        proxy = _FeatureProxy(feature, malformed)
        assert family.key(proxy, require_frame=True) == family.key(source)
        assert {
            outcome.state
            for outcome in family.outcomes(recognition, (proxy,), AnnotationRegistry())
        } == {"unverifiable"}

    assert {
        outcome.state
        for outcome in family.outcomes(recognition, (feature, feature), AnnotationRegistry())
    } == {"unverifiable"}


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda family: family.kind)
def test_lint_does_not_duplicate_a_recorded_placement_drop_issue_1421(family) -> None:
    drawing = build_drawing(family.cases[0].part)
    recognition = drawing.recognition()
    assert recognition is not None
    feature = _feature(drawing, family)
    registry = AnnotationRegistry()
    registry.record_issue(
        LintIssue(
            "warning",
            "synthetic joint-solver drop",
            code=family.dropped_code,
            measurement_ids=tuple(
                DimensionId(feature, parameter) for parameter in family.parameters
            ),
            outcome_stage="placement",
        )
    )

    assert (
        family.lint(
            family.cases[0].part,
            recognition=recognition,
            features=drawing.model().features,
            registry=registry,
        )
        == []
    )


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda family: family.kind)
def test_outcome_boundary_rejects_a_foreign_aggregate_issue_1421(family) -> None:
    with pytest.raises(TypeError, match="run's RecognitionResult"):
        family.outcomes(object(), (), AnnotationRegistry())  # type: ignore[arg-type]

    recognition, _source_record = _source(family.cases[0])
    mutable = replace(
        recognition,
        section_recesses=list(recognition.section_recesses),  # type: ignore[arg-type]
    )
    with pytest.raises(TypeError, match="immutable tuple"):
        family.outcomes(mutable, (), AnnotationRegistry())


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda family: family.kind)
def test_correspondence_key_rejects_every_malformed_released_fact_issue_1421(family) -> None:
    recognition, source = _source(family.cases[0])

    class _BadFloat(float):
        def __float__(self):
            raise ValueError

    malformed_sources = (
        corrupt_recess(source, "boundary_coordinate", _BadFloat(1)),
        corrupt_recess(source, "boundary_coordinate", float("inf")),
        corrupt_recess(source, "boundary_coordinate", True),
        corrupt_recess(source, "run_interval", (0, 0)),
        corrupt_recess(source, "origin", list(source.geometry.frame.origin)),
        corrupt_recess(source, "u", source.geometry.frame.run),
        corrupt_recess(source, "condition", True),
    )
    for malformed in malformed_sources:
        with pytest.raises((TypeError, ValueError)):
            family.key(malformed)

    # A malformed unified recess cannot establish its family or requirements.
    # A valid source with no IR still reports all three gaps.
    for malformed in malformed_sources:
        malformed_recognition = replace(recognition, section_recesses=(malformed,))
        with pytest.raises((TypeError, ValueError)):
            family.outcomes(malformed_recognition, (), AnnotationRegistry())
    unmatched_outcomes = family.outcomes(recognition, (), AnnotationRegistry())
    assert len(unmatched_outcomes) == 3
    assert all(outcome.state == "unverifiable" for outcome in unmatched_outcomes)
    assert all(outcome.source_records == (source,) for outcome in unmatched_outcomes)

    drawing = build_drawing(family.cases[0].part)
    feature = _feature(drawing, family)
    mismatched_frame = SimpleNamespace(
        **{name: getattr(feature, name) for name in family.cases[0].declaration() if name != "at"},
        frame=Frame(feature.frame.origin, feature.width_axis),
    )
    with pytest.raises(ValueError):
        family.key(mismatched_frame, require_frame=True)
