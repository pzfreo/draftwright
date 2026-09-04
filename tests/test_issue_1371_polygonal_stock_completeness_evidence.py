"""#1371 — whole polygonal-stock completeness uses independent prism facts."""

from __future__ import annotations

from dataclasses import replace
from math import cos, pi, sin
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Polygon, Pos, RegularPolygon, Rot, extrude, import_step

from draftwright.evaluation.step_analysis import (
    ObservationError,
    _default_observers,
    _polygonal_stock_drawing_outcomes,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-polygonal-stock-v1.json"


def _stock():
    return extrude(RegularPolygon(20, 6), 30)


def _states(boundary: str, part=None) -> set[str]:
    observed = _default_observers()["polygonal-stock"](_stock() if part is None else part)
    assert len(observed) == 1
    return {fact.downstream[boundary] for fact in observed}


def _annotation_for_parameter(drawing, parameter: str) -> str:
    return next(
        name
        for name in drawing.annotations()
        if any(
            identity.parameter == parameter for identity in drawing.registry.measurement_of(name)
        )
    )


def test_versioned_polygonal_stock_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("polygonal-stock",)
    assert len(corpus.cases) == 13
    assert sum(len(case.expected) for case in corpus.cases) == 6
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "ambiguous",
        "compound",
        "extra-topology",
        "in-plane-rotation",
        "multiple-equal",
        "negative",
        "overlapping-family",
        "positive",
        "principal-axis",
        "schema-boundary",
        "topology-order-variant",
        "translated",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)
    assert all(
        "'1970-01-01T00:00:00'" in (CORPUS.parent / case.provenance["fixture"]).read_text()
        for case in corpus.cases
    )


def test_consumer_contract_publishes_polygonal_stock_completeness_evidence() -> None:
    from draftwright.recogniser_contract import consumer_capability_declaration

    declaration = consumer_capability_declaration()
    family = next(item for item in declaration["families"] if item["id"] == "polygonal-stock")

    assert family["completeness"] == {
        "state": "supported",
        "implementation": "draftwright.evaluation.step_analysis.evaluate_step_corpus",
        "evidence": ["tests/test_issue_1371_polygonal_stock_completeness_evidence.py"],
    }


def test_real_polygonal_stock_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 6
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 24
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 24
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


@pytest.mark.parametrize("axis", tuple("xyz"))
def test_every_principal_polygonal_stock_boundary_is_observed(axis) -> None:
    fixture = "polygonal-stock-roll17.step" if axis == "z" else f"polygonal-stock-{axis}.step"
    observed = _default_observers()["polygonal-stock"](import_step(CORPUS.parent / fixture))

    assert len(observed) == 1
    assert observed[0].identity["axis"] == axis
    assert set(observed[0].downstream.values()) == {"supported"}


def test_arbitrary_rigid_motion_survives_the_owned_framed_pipeline() -> None:
    from draftwright import build_drawing
    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.model.compiled import compile_dimensions

    baseline = build_drawing(_stock(), repair=False)
    moved = build_drawing(
        Pos(91, -37, 48) * Rot(31, 47, 13) * Rot(0, 0, 17) * _stock(),
        framed_recognition=True,
        repair=False,
    )

    def requirements(drawing):
        recognition = drawing.recognition()
        assert recognition is not None
        outcomes = polygonal_stock_outcomes(
            recognition,
            drawing.model().features,
            drawing.registry,
            compile_dimensions(drawing.model()).diagnostics,
        )
        feature = next(item for item in drawing.model().features if item.kind == "polygonal_stock")
        return (
            feature.side_count,
            round(feature.across_flats, 3),
            round(feature.length, 3),
            {(outcome.parameter_id, outcome.state) for outcome in outcomes},
        )

    assert moved.recognition_frame_decision["status"] == "framed"
    assert requirements(moved) == requirements(baseline)
    recognition = moved.recognition()
    assert recognition is not None
    assert _polygonal_stock_drawing_outcomes(tuple(recognition.polygonal_stock), moved) == [
        "supported"
    ]


def test_polygonal_stock_ledger_tracks_two_requirements_and_fails_closed() -> None:
    from draftwright import build_drawing
    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.model.compiled import compile_dimensions
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_stock(), repair=False)
    recognition = drawing.recognition()
    assert recognition is not None
    outcomes = polygonal_stock_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )

    assert {outcome.parameter_id for outcome in outcomes} == {
        "polygon_across_flats.length",
        "stock_length.length",
    }
    assert {outcome.state for outcome in outcomes} == {"placed"}
    missing = polygonal_stock_outcomes(recognition, drawing.model().features, AnnotationRegistry())
    assert len(missing) == 2
    assert {outcome.state for outcome in missing} == {"missing"}
    unverifiable = polygonal_stock_outcomes(
        recognition,
        [feature for feature in drawing.model().features if feature.kind != "polygonal_stock"],
        AnnotationRegistry(),
    )
    assert len(unverifiable) == 1
    assert (unverifiable[0].state, unverifiable[0].requirement_count) == (
        "unverifiable",
        2,
    )


def test_polygonal_stock_ledger_rejects_foreign_malformed_and_duplicate_ir() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.registry import AnnotationRegistry

    recognition = build_raw_recognition_result(_stock(), rotational=False)
    source = recognition.polygonal_stock[0]
    drawing = build_drawing(_stock(), repair=False)
    feature = next(item for item in drawing.model().features if item.kind == "polygonal_stock")

    class MalformedStock:
        kind = "polygonal_stock"
        frame = SimpleNamespace(axis=source.axis, origin=source.center)
        side_count = source.side_count
        across_flats = source.across_flats
        length = source.length
        span = feature.span
        flat_directions = source.flat_directions
        flat_centres = source.flat_centres

        @staticmethod
        def parameters():
            raise TypeError("broken parameter contract")

    assert polygonal_stock_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="run's RecognitionResult"):
        polygonal_stock_outcomes(object(), (), AnnotationRegistry())
    malformed = polygonal_stock_outcomes(recognition, (MalformedStock(),), AnnotationRegistry())
    assert len(malformed) == 1
    assert (malformed[0].state, malformed[0].requirement_count) == ("unverifiable", 2)

    duplicate = polygonal_stock_outcomes(recognition, (feature, feature), AnnotationRegistry())
    assert len(duplicate) == 1
    assert (duplicate[0].state, duplicate[0].requirement_count) == ("unverifiable", 2)

    duplicate_source = replace(recognition, polygonal_stock=(source, source))
    duplicate = polygonal_stock_outcomes(duplicate_source, (feature,), AnnotationRegistry())
    assert len(duplicate) == 1
    assert (duplicate[0].state, duplicate[0].requirement_count) == ("unverifiable", 2)

    overflow = replace(recognition, polygonal_stock=(replace(source, across_flats=10**10000),))
    outcome = polygonal_stock_outcomes(overflow, (feature,), AnnotationRegistry())
    assert len(outcome) == 1
    assert (outcome[0].state, outcome[0].requirement_count) == ("unverifiable", 2)


def test_impossible_stock_inventories_have_one_fail_closed_denominator() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes

    part = _stock()
    recognition = build_raw_recognition_result(part, rotational=False)
    source = recognition.polygonal_stock[0]
    drawing = build_drawing(part, repair=False)
    feature = next(item for item in drawing.model().features if item.kind == "polygonal_stock")
    overflow = replace(source, across_flats=10**10000)
    shift = (100.0, -50.0, 0.0)
    foreign = replace(
        source,
        center=tuple(value + delta for value, delta in zip(source.center, shift, strict=True)),
        flat_centres=tuple(
            tuple(value + delta for value, delta in zip(centre, shift, strict=True))
            for centre in source.flat_centres
        ),
    )

    for inventory in ((overflow, overflow), (source, foreign)):
        outcomes = polygonal_stock_outcomes(
            replace(recognition, polygonal_stock=inventory),
            (feature,),
            drawing.registry,
        )
        assert len(outcomes) == 1
        assert (outcomes[0].state, outcomes[0].requirement_count) == ("unverifiable", 2)
        assert outcomes[0].features == ()


@pytest.mark.parametrize(
    "corruption",
    ("collinear", "tangential", "axial", "far_tangential", "far_axial"),
)
def test_malformed_provider_flat_centres_fail_before_source_ir_self_consistency(
    corruption,
) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.model import build_part_model
    from draftwright.registry import AnnotationRegistry

    part = (
        Pos(1_000_000, -2_000_000, 3_000_000) * _stock()
        if corruption.startswith("far_")
        else _stock()
    )
    recognition = build_raw_recognition_result(part, rotational=False)
    source = recognition.polygonal_stock[0]
    if corruption.startswith("far_"):
        feature = next(
            item for item in build_part_model(part).features if item.kind == "polygonal_stock"
        )
    else:
        drawing = build_drawing(part, repair=False)
        feature = next(item for item in drawing.model().features if item.kind == "polygonal_stock")
    if corruption == "collinear":
        line = (cos(pi / 18), sin(pi / 18), 0.0)
        centres = tuple(
            tuple(
                source.center[index]
                + line[index]
                * (source.across_flats / 2)
                / sum(direction[component] * line[component] for component in range(3))
                for index in range(3)
            )
            for direction in source.flat_directions
        )
    elif corruption in {"tangential", "far_tangential"}:
        centres = tuple(
            (centre[0] - direction[1], centre[1] + direction[0], centre[2])
            for direction, centre in zip(source.flat_directions, source.flat_centres, strict=True)
        )
    else:
        centres = tuple((centre[0], centre[1], centre[2] + 1.0) for centre in source.flat_centres)
    malformed = replace(source, flat_centres=centres)
    matching_ir = replace(feature, flat_centres=centres)

    outcomes = polygonal_stock_outcomes(
        replace(recognition, polygonal_stock=(malformed,)),
        (matching_ir,),
        AnnotationRegistry(),
    )
    assert len(outcomes) == 1
    assert (outcomes[0].state, outcomes[0].requirement_count) == ("unverifiable", 2)


def test_provider_quantization_allowance_scales_with_stock_not_world_position() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.model import build_part_model
    from draftwright.registry import AnnotationRegistry

    part = extrude(RegularPolygon(1_000, 6), 30)
    recognition = build_raw_recognition_result(part, rotational=False)
    feature = next(
        item for item in build_part_model(part).features if item.kind == "polygonal_stock"
    )

    outcomes = polygonal_stock_outcomes(recognition, (feature,), AnnotationRegistry())

    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"missing"}


@pytest.mark.parametrize("rotation", (1, 10.58, 19.42))
def test_provider_direction_quantization_accepts_a_huge_rotated_stock_record(rotation) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.model import build_part_model
    from draftwright.registry import AnnotationRegistry

    part = extrude(RegularPolygon(5_000_000, 6, rotation=rotation), 30)
    recognition = build_raw_recognition_result(part, rotational=False)
    assert len(recognition.polygonal_stock) == 1
    feature = next(
        item for item in build_part_model(part).features if item.kind == "polygonal_stock"
    )

    outcomes = polygonal_stock_outcomes(recognition, (feature,), AnnotationRegistry())

    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"missing"}


def test_provider_contract_rejects_every_malformed_structural_boundary() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.polygonal_stock_coverage import (
        _canonical_span,
        _canonical_support_ring,
        _expected_support_offsets,
        _validate_polygonal_stock_source,
    )

    source = build_raw_recognition_result(_stock(), rotational=False).polygonal_stock[0]

    with pytest.raises(ValueError, match="exactly two endpoints"):
        _canonical_span(((0.0, 0.0, 0.0),))
    with pytest.raises(ValueError, match="paired direction and centre rings"):
        _canonical_support_ring((), ())
    with pytest.raises(ValueError, match="must intersect"):
        _expected_support_offsets(((1.0, 0.0, 0.0), (1.0, 0.0, 0.0)), 20.0, 2)

    invalid = [replace(source, axis="q"), replace(source, across_flats=0.0)]

    directions = list(source.flat_directions)
    directions[0] = (0.0, 0.0, 0.0)
    invalid.append(replace(source, flat_directions=tuple(directions)))

    directions = list(source.flat_directions)
    directions[1] = directions[0]
    invalid.append(replace(source, flat_directions=tuple(directions)))

    centres = list(source.flat_centres)
    centres[0] = (float("nan"), *centres[0][1:])
    invalid.append(replace(source, flat_centres=tuple(centres)))
    invalid.append(
        replace(
            source,
            flat_directions=source.flat_directions[:-1],
            flat_centres=source.flat_centres[:-1],
        )
    )

    centres = list(source.flat_centres)
    centres[1] = centres[0]
    invalid.append(replace(source, flat_centres=tuple(centres)))

    angles = (0.0, pi / 3, 2 * pi / 3 + 0.1, pi + 0.1, 4 * pi / 3, 5 * pi / 3)
    directions = tuple((cos(angle), sin(angle), 0.0) for angle in angles)
    offsets = _expected_support_offsets(directions, source.across_flats, 2)
    centres = tuple(
        tuple(float(source.center[index]) + offset[index] for index in range(3))
        for offset in offsets
    )
    invalid.append(replace(source, flat_directions=directions, flat_centres=centres))

    for malformed in invalid:
        with pytest.raises(ValueError):
            _validate_polygonal_stock_source(malformed)


def test_legacy_shape_fallbacks_preserve_polygonal_stock_identity_and_declaration() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.evaluation.step_analysis import (
        _declared_polygonal_stock_model,
        _polygonal_stock_identity,
        _polygonal_stock_parameters,
    )
    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_key

    source = build_raw_recognition_result(_stock(), rotational=False).polygonal_stock[0]
    legacy = SimpleNamespace(
        axis=source.axis,
        frame=SimpleNamespace(axis=source.axis, origin=source.center),
        side_count=source.side_count,
        across_flats=source.across_flats,
        base=source.base,
        top=source.top,
        flat_directions=source.flat_directions,
        flat_centres=source.flat_centres,
    )
    axisless = SimpleNamespace(
        **{key: value for key, value in vars(legacy).items() if key != "axis"}
    )

    assert _polygonal_stock_identity(axisless) == (str(source.axis), tuple(source.center))
    assert _polygonal_stock_parameters(legacy)["length"] == round(source.top - source.base, 3)
    assert polygonal_stock_key(legacy)[4] == round(source.top - source.base, 3)

    model = _declared_polygonal_stock_model(_stock(), (axisless,))
    feature = next(item for item in model.features if item.kind == "polygonal_stock")
    axis_index = "xyz".index(str(source.axis))
    assert feature.frame.axis == str(source.axis)
    assert (feature.span[0][axis_index], feature.span[1][axis_index]) == (
        source.base,
        source.top,
    )
    explicit = SimpleNamespace(**vars(axisless), span=feature.span)
    explicit_model = _declared_polygonal_stock_model(_stock(), (explicit,))
    explicit_feature = next(
        item for item in explicit_model.features if item.kind == "polygonal_stock"
    )
    assert explicit_feature.span == feature.span


def test_malformed_source_and_ir_key_fail_closed_without_partial_credit() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.registry import AnnotationRegistry

    recognition = build_raw_recognition_result(_stock(), rotational=False)
    wrong_source = replace(recognition, polygonal_stock=(SimpleNamespace(),))
    outcomes = polygonal_stock_outcomes(wrong_source, (), AnnotationRegistry())
    assert [(item.state, item.requirement_count) for item in outcomes] == [("unverifiable", 2)]

    malformed_ir = SimpleNamespace(kind="polygonal_stock", axis="z")
    outcomes = polygonal_stock_outcomes(recognition, (malformed_ir,), AnnotationRegistry())
    assert [(item.state, item.requirement_count) for item in outcomes] == [("unverifiable", 2)]


def test_provider_model_tolerance_accepts_a_real_noisy_stock_record() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.model import build_part_model
    from draftwright.registry import AnnotationRegistry

    points = [(20 * cos(2 * pi * index / 6), 20 * sin(2 * pi * index / 6)) for index in range(6)]
    points[0] = (points[0][0], points[0][1] + 0.05)
    part = extrude(Polygon(*points), 30)
    recognition = build_raw_recognition_result(part, rotational=False)
    assert len(recognition.polygonal_stock) == 1
    feature = next(
        item for item in build_part_model(part).features if item.kind == "polygonal_stock"
    )

    outcomes = polygonal_stock_outcomes(recognition, (feature,), AnnotationRegistry())

    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"missing"}


@pytest.mark.parametrize(
    ("apothem", "angles"),
    (
        (20.0, (0, 58.1, 120, 180, 238.1, 300)),
        (100.0, (0, 59, 120, 180, 239, 300)),
        (
            100_000.0,
            (10.80869, 68.88457, 128.85039, 190.80869, 248.88457, 308.85039),
        ),
        (
            50_000.0,
            (25.24814, 85.38473, 146.18819, 205.24814, 265.38473, 326.18819),
        ),
        (
            5_000_000.0,
            (25.75, 84.75, 146.25, 205.75, 264.75, 326.25),
        ),
    ),
)
def test_provider_angular_tolerance_accepts_a_real_near_regular_stock_record(
    apothem, angles
) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.model import build_part_model
    from draftwright.registry import AnnotationRegistry

    normals = tuple((cos(angle * pi / 180), sin(angle * pi / 180)) for angle in angles)
    points = []
    for index, first in enumerate(normals):
        second = normals[(index + 1) % len(normals)]
        determinant = first[0] * second[1] - first[1] * second[0]
        points.append(
            (
                apothem * (second[1] - first[1]) / determinant,
                apothem * (first[0] - second[0]) / determinant,
            )
        )
    part = extrude(Polygon(*points), 30)
    recognition = build_raw_recognition_result(part, rotational=False)
    assert len(recognition.polygonal_stock) == 1
    feature = next(
        item for item in build_part_model(part).features if item.kind == "polygonal_stock"
    )

    outcomes = polygonal_stock_outcomes(recognition, (feature,), AnnotationRegistry())

    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"missing"}


def test_large_stock_never_scales_the_axial_support_allowance() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.model import build_part_model
    from draftwright.registry import AnnotationRegistry

    part = extrude(RegularPolygon(1_000, 6), 30)
    recognition = build_raw_recognition_result(part, rotational=False)
    source = recognition.polygonal_stock[0]
    feature = next(
        item for item in build_part_model(part).features if item.kind == "polygonal_stock"
    )
    centres = tuple((centre[0], centre[1], centre[2] + 0.1) for centre in source.flat_centres)
    malformed = replace(source, flat_centres=centres)
    matching_ir = replace(feature, flat_centres=centres)

    outcomes = polygonal_stock_outcomes(
        replace(recognition, polygonal_stock=(malformed,)),
        (matching_ir,),
        AnnotationRegistry(),
    )

    assert len(outcomes) == 1
    assert (outcomes[0].state, outcomes[0].requirement_count) == ("unverifiable", 2)


@pytest.mark.parametrize(("corruption", "distance"), (("radial", 0.85), ("tangential", 20.0)))
def test_exact_large_ring_cannot_borrow_unused_angular_slack(corruption, distance) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.model import build_part_model
    from draftwright.registry import AnnotationRegistry

    part = extrude(RegularPolygon(1_000, 6), 30)
    recognition = build_raw_recognition_result(part, rotational=False)
    source = recognition.polygonal_stock[0]
    feature = next(
        item for item in build_part_model(part).features if item.kind == "polygonal_stock"
    )
    centres = []
    for direction, centre in zip(source.flat_directions, source.flat_centres, strict=True):
        delta = (
            direction if corruption == "radial" else (-direction[1], direction[0], direction[2])
        )
        length = sum(component * component for component in delta) ** 0.5
        centres.append(
            tuple(
                value + distance * component / length
                for value, component in zip(centre, delta, strict=True)
            )
        )
    malformed = replace(source, flat_centres=tuple(centres))
    matching_ir = replace(feature, flat_centres=tuple(centres))

    outcomes = polygonal_stock_outcomes(
        replace(recognition, polygonal_stock=(malformed,)),
        (matching_ir,),
        AnnotationRegistry(),
    )

    assert len(outcomes) == 1
    assert (outcomes[0].state, outcomes[0].requirement_count) == ("unverifiable", 2)


@pytest.mark.parametrize(
    "corruption",
    (
        "raises",
        "parameter_ids",
        "parameter_values",
        "af_span",
        "length_span",
        "overflow_span",
    ),
)
def test_polygonal_stock_ledger_rejects_malformed_parameter_contract(corruption) -> None:
    from draftwright import build_drawing
    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_stock(), repair=False)
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "polygonal_stock")
    parameters = list(feature.parameters())
    if corruption == "parameter_ids":
        parameters[0] = replace(parameters[0], role="wrong_across_flats")
    elif corruption == "parameter_values":
        parameters[0] = replace(parameters[0], value=parameters[0].value + 1.0)
    elif corruption == "af_span":
        parameters[0] = replace(parameters[0], span=feature.span)
    elif corruption == "length_span":
        span = [list(point) for point in feature.span]
        span[1]["xyz".index(feature.frame.axis)] += 0.1
        parameters[1] = replace(parameters[1], span=tuple(tuple(point) for point in span))
    elif corruption == "overflow_span":
        parameters[1] = replace(parameters[1], span=((0, 0, 0), (0, 0, 10**10000)))

    class ParameterProxy:
        kind = "polygonal_stock"

        def __getattr__(self, name):
            return getattr(feature, name)

        def parameters(self):
            if corruption == "raises":
                raise ValueError("invalid parameter contract")
            return parameters

    outcomes = polygonal_stock_outcomes(recognition, (ParameterProxy(),), AnnotationRegistry())
    assert len(outcomes) == 1
    assert (outcomes[0].state, outcomes[0].requirement_count) == ("unverifiable", 2)


def test_polygonal_stock_key_rejects_rotated_or_tangentially_shifted_foreign_supports() -> None:
    from draftwright import build_drawing
    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_stock(), repair=False)
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "polygonal_stock")
    directions = []
    centres = []
    delta = 0.1
    for direction, centre in zip(feature.flat_directions, feature.flat_centres, strict=True):
        directions.append(
            (
                direction[0] * cos(delta) - direction[1] * sin(delta),
                direction[0] * sin(delta) + direction[1] * cos(delta),
                direction[2],
            )
        )
        centres.append(
            (centre[0] + 0.25 * direction[1], centre[1] - 0.25 * direction[0], centre[2])
        )
    foreign = replace(
        feature,
        flat_directions=tuple(directions),
        flat_centres=tuple(centres),
    )

    outcomes = polygonal_stock_outcomes(recognition, (foreign,), AnnotationRegistry())
    assert len(outcomes) == 1
    assert (outcomes[0].state, outcomes[0].requirement_count) == ("unverifiable", 2)


@pytest.mark.parametrize("representation", ("cyclic", "reversed", "reversed_span"))
def test_equivalent_public_sheet_ring_representations_keep_exact_coverage(representation) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import Sheet

    part = _stock()
    source = build_raw_recognition_result(part, rotational=False).polygonal_stock[0]
    directions = source.flat_directions
    centres = source.flat_centres
    center = source.center
    axis_index = "xyz".index(source.axis)
    start = list(center)
    end = list(center)
    start[axis_index] = source.base
    end[axis_index] = source.top
    span = (tuple(start), tuple(end))
    if representation == "cyclic":
        directions = directions[2:] + directions[:2]
        centres = centres[2:] + centres[:2]
    elif representation == "reversed":
        directions = tuple(reversed(directions))
        centres = tuple(reversed(centres))
    else:
        span = tuple(reversed(span))

    sheet = Sheet(part).authored_dimensions()
    handle = sheet.polygonal_stock(
        side_count=source.side_count,
        across_flats=source.across_flats,
        length=source.length,
        at=center,
        axis=source.axis,
        span=span,
        flat_directions=directions,
        flat_centres=centres,
    )
    sheet.dimension(handle, "polygon_across_flats.length")
    sheet.dimension(handle, "stock_length.length")
    drawing = sheet.build()

    assert not [
        issue for issue in drawing.lint() if issue.code.startswith("polygonal_stock_requirement_")
    ]
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["by_family"]["polygonal_stock"] == 2
    assert completeness["placed"] >= 2


def test_provider_hex_invariant_does_not_narrow_public_declared_stock() -> None:
    from draftwright import Sheet
    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_key

    side_count = 8
    apothem = 10 * cos(pi / side_count)
    directions = tuple(
        (cos(angle), sin(angle), 0.0)
        for angle in (2 * pi * index / side_count for index in range(side_count))
    )
    centres = tuple(
        (apothem * direction[0], apothem * direction[1], 15.0) for direction in directions
    )
    sheet = Sheet(extrude(RegularPolygon(10, side_count), 30)).authored_dimensions()
    handle = sheet.polygonal_stock(
        side_count=side_count,
        across_flats=2 * apothem,
        length=30,
        at=(0, 0, 15),
        axis="z",
        flat_directions=directions,
        flat_centres=centres,
    )
    sheet.dimension(handle, "polygon_across_flats.length")
    sheet.dimension(handle, "stock_length.length")
    drawing = sheet.build()
    feature = next(item for item in drawing.model().features if item.kind == "polygonal_stock")

    assert polygonal_stock_key(feature)[2] == side_count
    assert len(drawing.annotations()) >= 2


def test_polygonal_stock_ledger_distinguishes_suppressed_dropped_structured_and_orphan() -> None:
    from draftwright import build_drawing
    from draftwright.linting.issues import LintIssue
    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_stock(), repair=False)
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "polygonal_stock")
    registry = AnnotationRegistry()
    registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="polygonal_stock_dropped",
            measurement_ids=(DimensionId(feature, "polygon_across_flats.length"),),
            outcome_stage="placement",
        )
    )
    omissions = (
        SimpleNamespace(feature=feature, parameter_id="stock_length.length", authored=True),
    )
    states = {
        outcome.parameter_id: outcome.state
        for outcome in polygonal_stock_outcomes(
            recognition, drawing.model().features, registry, omissions
        )
    }
    assert states == {
        "polygon_across_flats.length": "dropped",
        "stock_length.length": "suppressed",
    }

    structured = AnnotationRegistry()
    structured.add(
        SimpleNamespace(),
        "stock_length_note",
        "front",
        feature=feature,
        satisfaction=DimensionId(feature, "stock_length.length"),
    )
    states = {
        outcome.parameter_id: outcome.state
        for outcome in polygonal_stock_outcomes(recognition, drawing.model().features, structured)
    }
    assert states["stock_length.length"] == "satisfied_by_structured_note"
    assert states["polygon_across_flats.length"] == "missing"

    orphan = AnnotationRegistry()
    orphan.add(SimpleNamespace(), "orphan", "plan", feature=feature)
    assert {
        outcome.state
        for outcome in polygonal_stock_outcomes(recognition, drawing.model().features, orphan)
    } == {"unverifiable"}


def test_polygonal_stock_observer_uses_one_build_owned_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_recognition_evidence
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_recognition_evidence", counted)
    assert _default_observers()["polygonal-stock"](_stock())
    assert calls == 1


def test_polygonal_stock_observer_rejects_a_missing_build_owned_aggregate(monkeypatch) -> None:
    import draftwright.builder as builder

    drawing = SimpleNamespace(recognition=lambda: None)
    monkeypatch.setattr(builder, "build_drawing", lambda *_args, **_kwargs: drawing)

    with pytest.raises(ObservationError, match="no build-owned recognition result"):
        _default_observers()["polygonal-stock"](_stock())


def test_polygonal_stock_observer_preserves_occurrence_when_boundary_loses_cardinality(
    monkeypatch,
) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_polygonal_stock_model_outcomes", lambda *_args: [])

    (observed,) = _default_observers()["polygonal-stock"](_stock())
    assert observed.downstream == {
        "ir_adapter": "unknown",
        "dsl_declaration": "unknown",
        "generated_code": "unknown",
        "drawing_consumer": "supported",
    }


def test_observer_failure_cannot_pass_a_zero_stock_negative_case(monkeypatch) -> None:
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
        ("analysis", "polygonal-stock")
    ]


def test_removing_polygonal_stock_from_built_ir_loses_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_stock(self):
        model = original(self)
        return replace(
            model,
            features=[feature for feature in model.features if feature.kind != "polygonal_stock"],
        )

    monkeypatch.setattr(Drawing, "model", without_stock)
    assert _states("ir_adapter") == {"unknown"}


def test_corrupting_public_polygonal_stock_declaration_loses_declaration_credit(
    monkeypatch,
) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.polygonal_stock

    def wrong_length(self, **kw):
        kw["length"] = float(kw["length"]) + 1.0
        span = list(kw["span"])
        endpoint = list(span[1])
        endpoint["xyz".index(str(kw["axis"]))] += 1.0
        span[1] = tuple(endpoint)
        kw["span"] = tuple(span)
        return original(self, **kw)

    monkeypatch.setattr(Sheet, "polygonal_stock", wrong_length)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_polygonal_stock_lines_loses_generated_code_credit(
    monkeypatch,
) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_stock_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if "sheet.polygonal_stock(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_stock_lines)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"supported"}
    assert _states("generated_code") == {"unknown"}


@pytest.mark.parametrize("parameter", ("polygon_across_flats.length", "stock_length.length"))
def test_wrong_polygonal_stock_ink_loses_drawing_credit(monkeypatch, parameter) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = _annotation_for_parameter(drawing, parameter)
        drawing.registry.named(name).label = "999"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_ink)
    assert _states("drawing_consumer") == {"unsupported"}


def test_moving_polygonal_stock_leader_off_its_flat_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_moved_tip(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = _annotation_for_parameter(drawing, "polygon_across_flats.length")
        annotation = drawing.registry.named(name)
        annotation._tip_local = (annotation.tip[0] + 7.0, annotation.tip[1] + 9.0)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_moved_tip)
    assert _states("drawing_consumer") == {"unsupported"}


def test_moving_stock_length_witness_off_its_cap_span_loses_drawing_credit(
    monkeypatch,
) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_shifted_witness(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = _annotation_for_parameter(drawing, "stock_length.length")
        spec = drawing.registry.named(name)._dw_spec
        spec.p1 = (spec.p1[0] + 7.0, spec.p1[1] + 9.0)
        spec.p2 = (spec.p2[0] + 7.0, spec.p2[1] + 9.0)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_shifted_witness)
    assert _states("drawing_consumer") == {"unsupported"}


@pytest.mark.parametrize(
    "corruption",
    ("af_view", "length_view", "af_geometry", "length_cardinality"),
)
def test_finished_polygonal_stock_ink_fails_closed_on_malformed_evidence(
    monkeypatch, corruption
) -> None:
    from draftwright import build_drawing

    drawing = build_drawing(_stock(), repair=False)
    recognition = drawing.recognition()
    assert recognition is not None
    af_name = _annotation_for_parameter(drawing, "polygon_across_flats.length")
    length_name = _annotation_for_parameter(drawing, "stock_length.length")

    if corruption in {"af_view", "length_view"}:
        target = af_name if corruption == "af_view" else length_name
        original = drawing.registry.view_of
        monkeypatch.setattr(
            drawing.registry,
            "view_of",
            lambda name: "right" if name == target else original(name),
        )
    elif corruption == "af_geometry":
        drawing.registry.named(af_name)._tip_local = None
    else:
        drawing.registry.named(length_name)._dw_spec = None

    assert _polygonal_stock_drawing_outcomes(tuple(recognition.polygonal_stock), drawing) == [
        "unsupported"
    ]


def test_drawing_consumer_requires_both_compiler_approved_stock_dimensions(monkeypatch) -> None:
    import draftwright.model.compiled as compiled
    from draftwright import build_drawing

    drawing = build_drawing(_stock(), repair=False)
    recognition = drawing.recognition()
    assert recognition is not None
    original = compiled.compile_dimensions

    def without_stock_dimensions(model):
        plan = original(model)
        return replace(
            plan,
            groups=tuple(
                replace(group, dims=()) if group.feature_kind == "polygonal_stock" else group
                for group in plan.groups
            ),
        )

    monkeypatch.setattr(compiled, "compile_dimensions", without_stock_dimensions)
    assert _polygonal_stock_drawing_outcomes(tuple(recognition.polygonal_stock), drawing) == [
        "unsupported"
    ]


def test_deleting_provider_stock_cannot_shrink_the_independent_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def without_stock(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, polygonal_stock=())

    monkeypatch.setattr(analysis, "_result_from_evidence", without_stock)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 6
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


@pytest.mark.parametrize("parameter", ("side_count", "across_flats", "length", "flat_supports"))
def test_weakening_provider_parameters_reduces_parameter_fidelity(monkeypatch, parameter) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def weakened(*args, **kwargs):
        result = original(*args, **kwargs)
        values = []
        for stock in result.polygonal_stock:
            if parameter == "side_count":
                plane = [index for index, name in enumerate("xyz") if name != stock.axis]
                directions = []
                centres = []
                for index in range(8):
                    direction = [0.0, 0.0, 0.0]
                    angle = 2 * pi * index / 8
                    direction[plane[0]] = cos(angle)
                    direction[plane[1]] = sin(angle)
                    centre = [float(component) for component in stock.center]
                    for component in plane:
                        centre[component] += direction[component] * stock.across_flats / 2
                    directions.append(tuple(direction))
                    centres.append(tuple(centre))
                values.append(
                    replace(
                        stock,
                        side_count=8,
                        flat_directions=tuple(directions),
                        flat_centres=tuple(centres),
                    )
                )
            elif parameter == "across_flats":
                values.append(replace(stock, across_flats=stock.across_flats + 0.1))
            elif parameter == "length":
                values.append(replace(stock, base=stock.base - 0.05, top=stock.top + 0.05))
            elif parameter == "flat_supports":
                centres = tuple(
                    (centre[0] + 0.1, centre[1], centre[2]) for centre in stock.flat_centres
                )
                values.append(replace(stock, flat_centres=centres))
            else:
                raise AssertionError(parameter)
        return replace(result, polygonal_stock=tuple(values))

    monkeypatch.setattr(analysis, "_result_from_evidence", weakened)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.total == 24
    assert damaged.parameter_fidelity.passed < damaged.parameter_fidelity.total


def test_constant_canonical_values_cannot_pass_the_varied_positive_oracle(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence
    canonical_af = 40 * cos(pi / 6)

    def hard_coded(*args, **kwargs):
        result = original(*args, **kwargs)
        values = []
        for stock in result.polygonal_stock:
            axis_index = "xyz".index(stock.axis)
            center = tuple(float(component) for component in stock.center)
            centres = tuple(
                tuple(center[index] + direction[index] * canonical_af / 2 for index in range(3))
                for direction in stock.flat_directions
            )
            values.append(
                replace(
                    stock,
                    across_flats=canonical_af,
                    base=center[axis_index] - 15,
                    top=center[axis_index] + 15,
                    flat_centres=centres,
                )
            )
        return replace(result, polygonal_stock=tuple(values))

    monkeypatch.setattr(analysis, "_result_from_evidence", hard_coded)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.total == 24
    assert damaged.parameter_fidelity.passed < damaged.parameter_fidelity.total


def test_deleting_declared_stock_cannot_shrink_quality_denominator() -> None:
    from draftwright import Sheet, build_drawing

    complete = build_drawing(_stock(), repair=False)
    sparse = Sheet(_stock())
    sparse.authored_dimensions()

    complete_summary = complete.lint_summary()
    sparse_summary = sparse.build().lint_summary()
    complete_quality = complete_summary["quality"]["completeness"]
    sparse_quality = sparse_summary["quality"]["completeness"]
    assert complete_quality["requirements"] == sparse_quality["requirements"] == 2
    assert complete_quality["by_family"]["polygonal_stock"] == 2
    assert complete_quality["placed"] == 2
    assert sparse_quality["unverifiable"] == 2
    assert complete_quality["audited_score"] == 1.0
    assert sparse_quality["audited_score"] == 0.0
    assert "polygonal_stock" not in complete_quality["unscored_recognized_families"]
    assert "polygonal_stock_requirement_unverifiable" in sparse_summary["by_code"]
