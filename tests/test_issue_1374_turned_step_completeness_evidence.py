"""#1374 — turned-step completeness uses independent physical OD-band facts."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Compound, Cylinder, Pos, Rot, import_step

from draftwright.evaluation.step_analysis import (
    ObservationError,
    _default_observers,
    evaluate_step_corpus,
    load_corpus,
)

FIXTURES = Path(__file__).parent / "fixtures" / "evaluation"
CORPUS = FIXTURES / "corpus-turned-steps-v1.json"


def _shaft(name: str = "turned-step-axis-x.step"):
    return import_step(FIXTURES / name)


def _stepped_shaft():
    return Cylinder(15, 20, align=(Align.CENTER, Align.CENTER, Align.MIN)) + Pos(
        0, 0, 20
    ) * Cylinder(10, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))


def _three_axially_disjoint_shafts():
    return Compound(children=[Pos(0, 0, station) * _stepped_shaft() for station in (0, 100, 200)])


def _odd_thousandth_shaft(variant: str):
    shaft = Cylinder(15, 20.001, align=(Align.CENTER, Align.CENTER, Align.MIN)) + Pos(
        0, 0, 20.001
    ) * Cylinder(10, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    if variant == "translated":
        return Pos(17, -23, 41) * shaft
    if variant == "reversed":
        return Rot(0, 180, 0) * shaft
    return shaft


def _states(boundary: str, part=None) -> list[str]:
    observed = _default_observers()["turned-steps"](_shaft() if part is None else part)
    assert observed
    return [fact.downstream[boundary] for fact in observed]


def test_versioned_turned_step_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("turned-steps",)
    assert len(corpus.cases) == 11
    assert sum(len(case.expected) for case in corpus.cases) == 26
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "axis-x",
        "axis-y",
        "axis-z",
        "blind-bore",
        "compound",
        "groove-owned-band",
        "grouped-ink",
        "multiple-axis-lines",
        "multiple-equal",
        "negative",
        "overlapping-family",
        "positive",
        "through-bore",
        "topology-order-variant",
        "translated",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)


def test_real_turned_step_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 26
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 52
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 104
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_raw_translated_record_retains_body_local_axis_line_and_band_facts() -> None:
    from b123d_recognisers import build_raw_recognition_result

    result = build_raw_recognition_result(_shaft("turned-step-translated-blind-bore.step"))

    assert len(result.turned_profiles) == 1
    profile = result.turned_profiles[0]
    assert profile.profile.axis_origin == pytest.approx((91.0, -37.0, 0.0))
    assert [(step.lo, step.hi, step.diameter) for step in profile.steps] == pytest.approx(
        [(48.0, 78.0, 30.0), (78.0, 108.0, 16.0)]
    )


def test_framed_route_preserves_rotated_turned_profile_on_exact_local_part() -> None:
    from b123d_recognisers import (
        FramedRecognitionResult,
        build_framed_recognition_result,
    )

    moved = Pos(17, -23, 9) * Rot(23, 37, 11) * _shaft()
    framed = build_framed_recognition_result(moved, rotational=True)

    assert isinstance(framed, FramedRecognitionResult)
    assert sorted(
        (step.length, step.diameter)
        for profile in framed.result.turned_profiles
        for step in profile.steps
    ) == pytest.approx([(15.0, 12.0), (20.0, 30.0), (30.0, 20.0)])


def test_compound_retains_two_body_local_profile_lines() -> None:
    from b123d_recognisers import build_raw_recognition_result

    result = build_raw_recognition_result(_shaft("turned-step-compound.step"))

    assert len(result.turned_profiles) == 2
    assert {profile.profile.axis_origin[:2] for profile in result.turned_profiles} == {
        (-40.0, 0.0),
        (40.0, 0.0),
    }
    assert sorted(len(profile.steps) for profile in result.turned_profiles) == [2, 2]


def test_unique_groove_floor_band_is_not_a_second_turned_step_fact() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.turned_step_coverage import physical_turned_steps

    result = build_raw_recognition_result(_shaft("groove-lone-y.step"))
    assert len(result.turned_profiles[0].steps) == 3
    assert len(result.grooves) == 1

    retained = physical_turned_steps(result)

    assert [(step.lo, step.hi, step.diameter) for _profile, step in retained] == pytest.approx(
        [(-30.0, -2.0, 20.0), (2.0, 30.0, 20.0)]
    )


def test_groove_floor_ownership_allows_the_public_coordinate_quantisation_boundary() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import (
        physical_turned_steps,
        turned_step_requirement_outcomes,
    )
    from draftwright.recognition_frame import profiles_owning_axial_band

    # Groove.at is published at 0.001 mm while TurnedProfileKey.axis_origin retains eight
    # decimal places.  These records still describe one physical axis line at half a Groove
    # coordinate quantum and must not create a duplicate StepFeature for the groove floor.
    part = Pos(0.0005, 0, 0) * _shaft("groove-lone-y.step")
    recognition = build_raw_recognition_result(part)

    assert recognition.turned_profiles[0].profile.axis_origin == pytest.approx((0.0005, 0, 0))
    assert recognition.grooves[0].at == pytest.approx((0.001, 0, 0))
    assert len(physical_turned_steps(recognition)) == 2
    # The tolerance includes exactly half of the 0.001 mm Groove coordinate quantum plus
    # its 5e-9 arithmetic allowance, but not the next representable mismatch beyond it.
    assert (
        profiles_owning_axial_band(
            recognition.turned_profiles,
            axis="y",
            centre=(0.001, 0, 0),
            width=recognition.grooves[0].width,
        )
        == recognition.turned_profiles
    )
    assert not profiles_owning_axial_band(
        recognition.turned_profiles,
        axis="y",
        centre=(0.001000006, 0, 0),
        width=recognition.grooves[0].width,
    )

    drawing = build_drawing(part)
    assert len([feature for feature in drawing.model().features if feature.kind == "step"]) == 2
    assert len([feature for feature in drawing.model().features if feature.kind == "groove"]) == 1
    outcomes = turned_step_requirement_outcomes(
        recognition, drawing.model().features, drawing.registry
    )
    assert len(outcomes) == 4
    assert "unverifiable" not in {outcome.state for outcome in outcomes}


def test_equal_lengths_share_ink_but_keep_every_measurement_identity() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(_shaft("turned-step-repeated-lengths.step"))
    name = next(name for name in drawing.annotations() if name.startswith("m_steplen_typ"))
    measurements = drawing.registry.measurement_of(name)

    assert drawing.registry.named(name).label == "3× 10"
    assert len(measurements) == 3
    assert {measurement.parameter for measurement in measurements} == {"step.length"}
    assert {measurement.feature.length for measurement in measurements} == {10.0}


def test_largest_band_od_can_use_exact_rotational_od_representation() -> None:
    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    drawing = build_drawing(_shaft("turned-step-axis-z.step"))
    recognition = drawing.recognition()
    assert recognition is not None
    outcomes = turned_step_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )
    maximum = next(
        outcome
        for outcome in outcomes
        if outcome.parameter_id == "step.diameter" and outcome.features[0].diameter == 28.0
    )

    assert maximum.state == "placed"
    assert maximum.representation_feature.kind == "rotational"
    assert maximum.representation_parameter == "od.diameter"


@pytest.mark.parametrize("framed", [False, True])
def test_global_od_cannot_multiply_across_disjoint_coaxial_profiles(framed: bool) -> None:
    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes

    drawing = build_drawing(_three_axially_disjoint_shafts(), framed_recognition=framed)
    recognition = drawing.recognition()
    assert recognition is not None
    outcomes = turned_step_requirement_outcomes(
        recognition, drawing.model().features, drawing.registry
    )
    largest = [
        outcome
        for outcome in outcomes
        if outcome.parameter_id == "step.diameter"
        and outcome.features
        and outcome.features[0].diameter == 30
    ]

    assert len(largest) == 3
    assert not [
        outcome for outcome in largest if outcome.representation_parameter == "od.diameter"
    ]
    assert len(
        {outcome.representation_feature for outcome in largest if outcome.state == "placed"}
    ) == sum(outcome.state == "placed" for outcome in largest)


def test_native_step_diameter_evidence_precedes_global_od_alternate() -> None:
    from draftwright import build_drawing
    from draftwright.linting.issues import LintIssue
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_shaft("turned-step-axis-z.step"))
    recognition = drawing.recognition()
    assert recognition is not None
    features = drawing.model().features
    step = next(
        feature for feature in features if feature.kind == "step" and feature.diameter == 28
    )
    rotational = next(feature for feature in features if feature.kind == "rotational")

    def diameter_outcome(registry, omissions=()):
        return next(
            outcome
            for outcome in turned_step_requirement_outcomes(
                recognition, features, registry, omissions
            )
            if outcome.parameter_id == "step.diameter" and step in outcome.features
        )

    direct = AnnotationRegistry()
    direct.add(
        object(),
        "direct",
        "front",
        feature=step,
        measurement=DimensionId(step, "step.diameter"),
    )
    assert (diameter_outcome(direct).state, diameter_outcome(direct).representation_feature) == (
        "placed",
        step,
    )

    omission = SimpleNamespace(feature=step, parameter_id="step.diameter", authored=True)
    suppressed = diameter_outcome(AnnotationRegistry(), (omission,))
    assert (suppressed.state, suppressed.representation_feature) == ("suppressed", step)

    dropped_registry = AnnotationRegistry()
    dropped_registry.record_issue(
        LintIssue(
            "warning",
            "synthetic direct drop",
            code="step_dim_dropped",
            measurement_ids=(DimensionId(step, "step.diameter"),),
        )
    )
    dropped = diameter_outcome(dropped_registry)
    assert (dropped.state, dropped.representation_feature) == ("dropped", step)

    alternate = AnnotationRegistry()
    alternate.add(
        object(),
        "alternate",
        "front",
        feature=rotational,
        measurement=DimensionId(rotational, "od.diameter"),
    )
    alternate_outcome = diameter_outcome(alternate)
    assert (alternate_outcome.state, alternate_outcome.representation_feature) == (
        "placed",
        rotational,
    )


def test_turned_step_ledger_tracks_two_requirements_per_physical_band() -> None:
    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_shaft())
    recognition = drawing.recognition()
    assert recognition is not None
    outcomes = turned_step_requirement_outcomes(
        recognition, drawing.model().features, drawing.registry
    )

    assert len(outcomes) == 6
    assert {(item.parameter_id, item.state) for item in outcomes} == {
        ("step.length", "placed"),
        ("step.diameter", "placed"),
    }
    missing = turned_step_requirement_outcomes(
        recognition, drawing.model().features, AnnotationRegistry()
    )
    assert {item.state for item in missing} == {"missing"}
    unverifiable = turned_step_requirement_outcomes(
        recognition,
        [feature for feature in drawing.model().features if feature.kind != "step"],
        AnnotationRegistry(),
    )
    assert {item.state for item in unverifiable} == {"unverifiable"}


def test_turned_step_ledger_rejects_foreign_results_and_malformed_ir() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    recognition = build_raw_recognition_result(_shaft())
    source = recognition.turned_profiles[0].steps[0]

    class MalformedStep:
        kind = "step"
        frame = SimpleNamespace(axis=source.axis, origin=(0.0, 0.0, source.lo))
        diameter = source.diameter
        span = None

        @staticmethod
        def parameters():
            raise TypeError("broken parameter contract")

    assert turned_step_requirement_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="run's RecognitionResult"):
        turned_step_requirement_outcomes(object(), (), AnnotationRegistry())
    outcomes = turned_step_requirement_outcomes(
        recognition, (MalformedStep(),), AnnotationRegistry()
    )
    assert outcomes
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("profile_axis", "profile and frame use different axes"),
        ("zero_span", "span must have positive length"),
        ("frame_midpoint", "frame origin is not the axial-span midpoint"),
        ("diameter", "diameter must be positive"),
    ],
)
def test_turned_step_ir_schema_rejects_nonphysical_values(mutation: str, message: str) -> None:
    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import turned_step_key

    feature = next(
        item for item in build_drawing(_shaft()).model().features if item.kind == "step"
    )
    if mutation == "profile_axis":
        other_axis = next(axis for axis in "xyz" if axis != feature.frame.axis)
        malformed = replace(feature, profile=replace(feature.profile, axis=other_axis))
    elif mutation == "zero_span":
        malformed = replace(feature, span=(feature.span[0], feature.span[0]))
    elif mutation == "frame_midpoint":
        origin = list(feature.frame.origin)
        origin["xyz".index(feature.frame.axis)] += 1.0
        malformed = replace(feature, frame=replace(feature.frame, origin=tuple(origin)))
    else:
        malformed = replace(feature, diameter=0.0)

    with pytest.raises(ValueError, match=message):
        turned_step_key(malformed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("diameter", "30"),
        ("diameter", True),
        ("diameter", float("nan")),
        ("axis", "q"),
        ("lo", "0"),
        ("hi", "20"),
    ],
)
def test_malformed_public_step_records_fail_closed(field: str, value) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes

    part = _shaft()
    recognition = build_raw_recognition_result(part)
    broken = replace(recognition.turned_steps[0], **{field: value})
    malformed = replace(recognition, turned_steps=(broken, *recognition.turned_steps[1:]))
    drawing = build_drawing(part)

    outcomes = turned_step_requirement_outcomes(
        malformed, drawing.model().features, drawing.registry
    )

    assert outcomes[:2]
    assert {outcome.state for outcome in outcomes[:2]} == {"unverifiable"}


def test_public_step_profile_relation_rejects_every_structural_schema_break() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.turned_step_coverage import turned_step_source_key

    recognition = build_raw_recognition_result(_shaft())
    source = recognition.turned_steps[0]
    key = source.profile
    assert key is not None
    other_axis = next(axis for axis in "xyz" if axis != source.axis)
    different_bounds = list(key.body_bounds)
    different_bounds[0] -= 1
    different_key = replace(key, body_bounds=tuple(different_bounds))
    structural_impostor = SimpleNamespace(
        axis=key.axis,
        axis_origin=key.axis_origin,
        body_bounds=key.body_bounds,
    )
    cases = (
        (
            SimpleNamespace(axis=source.axis, profile=None),
            replace(source, profile=None),
            "lacks body-local profile identity",
        ),
        (
            SimpleNamespace(axis=source.axis, profile=key),
            replace(source, profile=different_key),
            "disagrees with its containing profile identity",
        ),
        (
            SimpleNamespace(axis=source.axis, profile=structural_impostor),
            replace(source, profile=structural_impostor),
            "must be TurnedProfileKey",
        ),
        (
            replace(source, profile=replace(key, axis_origin=key.axis_origin[:2])),
            replace(source, profile=replace(key, axis_origin=key.axis_origin[:2])),
            "exactly three coordinates",
        ),
        (
            replace(source, profile=replace(key, body_bounds=key.body_bounds[:-1])),
            replace(source, profile=replace(key, body_bounds=key.body_bounds[:-1])),
            "exactly six coordinates",
        ),
        (
            SimpleNamespace(axis=other_axis, profile=key),
            source,
            "containing profile use different axes",
        ),
        (
            replace(source, profile=replace(key, axis=other_axis)),
            replace(source, profile=replace(key, axis=other_axis)),
            "profile key use different axes",
        ),
        (source, replace(source, hi=source.lo), "positive length"),
        (source, replace(source, diameter=0), "diameter must be positive"),
    )

    for profile, step, message in cases:
        with pytest.raises((TypeError, ValueError), match=message):
            turned_step_source_key(profile, step)


@pytest.mark.parametrize(
    "bounds",
    [
        ("bad",) * 6,
        (100, 101, 100, 101, 100, 101),
        (False, True, False, True, False, True),
        (1, -1, 1, -1, 1, -1),
    ],
)
def test_complete_public_profile_key_schema_fails_closed(bounds) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes

    part = _shaft()
    recognition = build_raw_recognition_result(part)
    malformed_steps = tuple(
        replace(step, profile=replace(step.profile, body_bounds=bounds))
        for step in recognition.turned_steps
    )
    malformed = replace(recognition, turned_steps=malformed_steps)
    drawing = build_drawing(part)

    outcomes = turned_step_requirement_outcomes(
        malformed, drawing.model().features, drawing.registry
    )

    assert outcomes
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


@pytest.mark.parametrize("field", ["axis_origin", "body_bounds"])
def test_public_profile_key_requires_immutable_tuple_fields(field: str) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes

    part = _shaft()
    recognition = build_raw_recognition_result(part)
    malformed_steps = tuple(
        replace(step, profile=replace(step.profile, **{field: list(getattr(step.profile, field))}))
        for step in recognition.turned_steps
    )
    malformed = replace(recognition, turned_steps=malformed_steps)
    drawing = build_drawing(part)

    outcomes = turned_step_requirement_outcomes(
        malformed, drawing.model().features, drawing.registry
    )

    assert len(outcomes) == 2 * len(recognition.turned_steps)
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


@pytest.mark.parametrize("field", ["diameter", "lo"])
def test_overflowing_public_reals_fail_closed(field: str) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes

    part = _shaft()
    recognition = build_raw_recognition_result(part)
    broken = replace(recognition.turned_steps[0], **{field: Fraction(10**10_000, 1)})
    malformed = replace(recognition, turned_steps=(broken, *recognition.turned_steps[1:]))
    drawing = build_drawing(part)

    outcomes = turned_step_requirement_outcomes(
        malformed, drawing.model().features, drawing.registry
    )

    assert outcomes[:2]
    assert {outcome.state for outcome in outcomes[:2]} == {"unverifiable"}


@pytest.mark.parametrize("variant", ["asymmetric", "coordinate-floor"])
def test_valid_provider_profile_bounds_need_not_be_transversely_centred(variant: str) -> None:
    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes

    if variant == "asymmetric":
        part = _stepped_shaft() + Pos(14.5, 0, 5) * Box(2, 2, 5)
    else:
        part = Pos(0.0000005, 0, 0) * _stepped_shaft()
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None

    outcomes = turned_step_requirement_outcomes(
        recognition, drawing.model().features, drawing.registry
    )

    assert len(outcomes) == 2 * len(recognition.turned_steps)
    assert "unverifiable" not in {outcome.state for outcome in outcomes}


@pytest.mark.parametrize(
    "mutation",
    [
        "invalid_bounds",
        "axial_origin",
        "different_bounds",
        "submicro_bounds",
        "submicro_origin",
    ],
)
def test_automatic_ir_must_retain_exact_validated_profile_ownership(mutation: str) -> None:
    from draftwright import build_drawing
    from draftwright.evaluation.step_analysis import _turned_step_model_outcomes
    from draftwright.linting.turned_step_coverage import physical_turned_steps

    drawing = build_drawing(_shaft())
    recognition = drawing.recognition()
    assert recognition is not None
    sources = physical_turned_steps(recognition)
    features = list(drawing.model().features)
    index = next(i for i, feature in enumerate(features) if feature.kind == "step")
    feature = features[index]
    profile = feature.profile
    assert profile is not None
    if mutation == "invalid_bounds":
        changed = replace(profile, body_bounds=("bad",) * 6)
    elif mutation == "axial_origin":
        origin = list(profile.axis_origin)
        origin["xyz".index(profile.axis)] = 999
        changed = replace(profile, axis_origin=tuple(origin))
    elif mutation == "submicro_origin":
        origin = list(profile.axis_origin)
        transverse_index = next(index for index in range(3) if index != "xyz".index(profile.axis))
        origin[transverse_index] += 1e-7
        changed = replace(profile, axis_origin=tuple(origin))
    else:
        bounds = list(profile.body_bounds)
        bound_index = 2 * "xyz".index(profile.axis)
        delta = 1e-7 if mutation == "submicro_bounds" else 1
        bounds[bound_index] -= delta
        bounds[bound_index + 1] += delta
        changed = replace(profile, body_bounds=tuple(bounds))
    features[index] = replace(feature, profile=changed)

    outcomes = _turned_step_model_outcomes(sources, recognition, features)
    if mutation in {"different_bounds", "submicro_bounds", "submicro_origin"}:
        assert outcomes.count("unknown") == 1
        assert outcomes.count("supported") == len(outcomes) - 1
    else:
        assert set(outcomes) == {"unknown"}


def test_automatic_ir_cannot_drop_profile_ownership() -> None:
    from draftwright import build_drawing
    from draftwright.evaluation.step_analysis import _turned_step_model_outcomes
    from draftwright.linting.turned_step_coverage import physical_turned_steps

    drawing = build_drawing(_shaft())
    recognition = drawing.recognition()
    assert recognition is not None
    sources = physical_turned_steps(recognition)
    features = [
        replace(feature, profile=None) if feature.kind == "step" else feature
        for feature in drawing.model().features
    ]

    assert set(_turned_step_model_outcomes(sources, recognition, features)) == {"unknown"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("width", "bad"),
        ("width", "4"),
        ("width", float("nan")),
        ("width", float("inf")),
        ("width", True),
        ("width", -4),
        ("width", Fraction(10**10_000, 1)),
        ("diameter", "16"),
        ("diameter", float("nan")),
        ("diameter", float("inf")),
        ("diameter", True),
        ("diameter", -16),
        ("axis", "q"),
        ("at", ("bad", 0, 0)),
        ("at", ("0", "0", "0")),
        ("at", [0, 0, 0]),
        ("at", (0, True, 0)),
        ("at", (0,)),
    ],
)
def test_malformed_groove_ownership_makes_the_raw_band_roster_unverifiable(
    field: str, value
) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.quality import quality_components
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes

    part = _shaft("groove-lone-y.step")
    recognition = build_raw_recognition_result(part)
    assert len(recognition.grooves) == 1
    malformed = replace(
        recognition,
        grooves=(replace(recognition.grooves[0], **{field: value}),),
    )
    drawing = build_drawing(part)

    outcomes = turned_step_requirement_outcomes(
        malformed, drawing.model().features, drawing.registry
    )

    assert len(outcomes) == 2 * len(recognition.turned_steps)
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}
    completeness = quality_components(
        recognition=malformed,
        features=drawing.model().features,
        registry=drawing.registry,
        omissions=(),
        issues=(),
        error_penalty=0.1,
        warning_penalty=0.02,
        has_asserted_content=True,
        part=part,
    )["completeness"]
    assert completeness["by_family"]["turned_steps"] == len(outcomes)
    assert completeness["by_family"]["grooves"] == 2
    assert completeness["unverifiable"] == len(outcomes) + 2


@pytest.mark.parametrize("field", ["turned_steps", "grooves"])
@pytest.mark.parametrize("container", ["list", "generator"])
def test_mutable_or_one_shot_root_inventories_preserve_the_raw_step_denominator(
    field: str, container: str
) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.quality import quality_components
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes

    part = _shaft("groove-lone-y.step")
    recognition = build_raw_recognition_result(part)
    values = getattr(recognition, field)
    replacement = list(values) if container == "list" else iter(values)
    malformed = replace(recognition, **{field: replacement})
    drawing = build_drawing(part)

    outcomes = turned_step_requirement_outcomes(
        malformed, drawing.model().features, drawing.registry
    )

    assert len(outcomes) == 2 * len(recognition.turned_steps)
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}

    quality_replacement = list(values) if container == "list" else iter(values)
    quality_malformed = replace(recognition, **{field: quality_replacement})
    completeness = quality_components(
        recognition=quality_malformed,
        features=drawing.model().features,
        registry=drawing.registry,
        omissions=(),
        issues=(),
        error_penalty=0.1,
        warning_penalty=0.02,
        has_asserted_content=True,
        part=part,
    )["completeness"]
    assert completeness["by_family"]["turned_steps"] == len(outcomes)
    assert completeness["unverifiable"] >= len(outcomes)


def test_physical_band_roster_requires_both_root_tuple_inventories() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.turned_step_coverage import physical_turned_steps

    recognition = build_raw_recognition_result(_shaft("groove-lone-y.step"))
    with pytest.raises(TypeError, match="turned_steps must be an immutable tuple"):
        physical_turned_steps(replace(recognition, turned_steps=list(recognition.turned_steps)))
    with pytest.raises(TypeError, match="grooves must be an immutable tuple"):
        physical_turned_steps(replace(recognition, grooves=list(recognition.grooves)))


@pytest.mark.parametrize("value", [None, 42, object()])
def test_noniterable_root_step_inventory_retains_an_aggregate_contract_outcome(value) -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.quality import quality_components
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes

    part = _shaft("groove-lone-y.step")
    recognition = build_raw_recognition_result(part)
    malformed = replace(recognition, turned_steps=value)
    drawing = build_drawing(part)

    outcomes = turned_step_requirement_outcomes(
        malformed, drawing.model().features, drawing.registry
    )

    assert len(outcomes) == 1
    assert (outcomes[0].parameter_id, outcomes[0].state, outcomes[0].requirement_count) == (
        "?",
        "unverifiable",
        1,
    )
    completeness = quality_components(
        recognition=malformed,
        features=drawing.model().features,
        registry=drawing.registry,
        omissions=(),
        issues=(),
        error_penalty=0.1,
        warning_penalty=0.02,
        has_asserted_content=True,
        part=part,
    )["completeness"]
    assert completeness["by_family"]["turned_steps"] == 1
    assert completeness["unverifiable"] >= 1


def test_malformed_rotational_alternate_cannot_abort_or_earn_credit() -> None:
    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes

    drawing = build_drawing(_shaft())
    recognition = drawing.recognition()
    assert recognition is not None
    malformed = SimpleNamespace(
        kind="rotational",
        frame=SimpleNamespace(axis="x", origin=(0.0, 0.0, 0.0)),
        od="bad",
    )

    outcomes = turned_step_requirement_outcomes(
        recognition, (*drawing.model().features, malformed), drawing.registry
    )

    assert outcomes
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


@pytest.mark.parametrize(
    ("field", "value"),
    [("axis", "q"), ("lo", "0"), ("lo", "bad"), ("diameter", "bad")],
)
def test_quality_component_classifies_malformed_source_as_unverifiable(field: str, value) -> None:
    from draftwright import build_drawing
    from draftwright.linting.quality import quality_components

    drawing = build_drawing(_shaft())
    recognition = drawing.recognition()
    assert recognition is not None
    malformed = replace(
        recognition,
        turned_steps=(
            replace(recognition.turned_steps[0], **{field: value}),
            *recognition.turned_steps[1:],
        ),
    )
    completeness = quality_components(
        recognition=malformed,
        features=drawing.model().features,
        registry=drawing.registry,
        omissions=(),
        issues=(),
        error_penalty=0.1,
        warning_penalty=0.02,
        has_asserted_content=True,
        part=_shaft(),
    )["completeness"]

    assert completeness["by_family"]["turned_steps"] == 6
    assert completeness["unverifiable"] == 2
    assert completeness["placed"] == 4


def test_quality_component_isolates_a_nonrecord_step_member_from_hole_coverage() -> None:
    from draftwright import build_drawing
    from draftwright.linting.quality import quality_components

    part = _shaft("turned-step-through-bore.step")
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    malformed = replace(
        recognition,
        turned_steps=(None, *recognition.turned_steps[1:]),
    )

    def completeness_for(result):
        return quality_components(
            recognition=result,
            features=drawing.model().features,
            registry=drawing.registry,
            omissions=(),
            issues=(),
            error_penalty=0.1,
            warning_penalty=0.02,
            has_asserted_content=True,
            part=part,
        )["completeness"]

    valid = completeness_for(recognition)
    damaged = completeness_for(malformed)

    assert (valid["by_family"]["turned_steps"], valid["by_family"]["holes"]) == (4, 4)
    assert (valid["requirements"], valid["placed"], valid["missing"], valid["unverifiable"]) == (
        8,
        6,
        2,
        0,
    )
    assert (damaged["by_family"]["turned_steps"], damaged["by_family"]["holes"]) == (4, 4)
    assert (
        damaged["requirements"],
        damaged["placed"],
        damaged["missing"],
        damaged["unverifiable"],
    ) == (8, 2, 4, 2)


def test_turned_step_ledger_distinguishes_suppressed_dropped_and_orphan_evidence() -> None:
    from draftwright import build_drawing
    from draftwright.linting.issues import LintIssue
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_shaft())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "step")

    omission = SimpleNamespace(feature=feature, parameter_id="step.length", authored=True)
    suppressed = turned_step_requirement_outcomes(
        recognition, drawing.model().features, AnnotationRegistry(), (omission,)
    )
    matching = [item for item in suppressed if feature in item.features]
    assert {item.parameter_id: item.state for item in matching} == {
        "step.length": "suppressed",
        "step.diameter": "missing",
    }

    dropped_registry = AnnotationRegistry()
    dropped_registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="step_dim_dropped",
            measurement_ids=(DimensionId(feature, "step.length"),),
        )
    )
    dropped = turned_step_requirement_outcomes(
        recognition, drawing.model().features, dropped_registry
    )
    matching = [item for item in dropped if feature in item.features]
    assert {item.parameter_id: item.state for item in matching} == {
        "step.length": "dropped",
        "step.diameter": "missing",
    }

    orphan_registry = AnnotationRegistry()
    orphan_registry.add(object(), "orphan", "plan", feature=feature)
    orphan = turned_step_requirement_outcomes(
        recognition, drawing.model().features, orphan_registry
    )
    assert {item.state for item in orphan if feature in item.features} == {"unverifiable"}


def test_turned_step_ledger_distinguishes_structured_satisfaction() -> None:
    from draftwright import build_drawing
    from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_shaft())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "step")
    registry = AnnotationRegistry()
    registry.add(
        object(),
        "structured_note",
        "plan",
        feature=feature,
        satisfaction=DimensionId(feature, "step.length"),
    )

    outcomes = turned_step_requirement_outcomes(recognition, drawing.model().features, registry)
    matching = [item for item in outcomes if feature in item.features]
    assert {item.parameter_id: item.state for item in matching} == {
        "step.length": "satisfied_by_structured_note",
        "step.diameter": "missing",
    }


def test_every_turned_step_boundary_is_supported_on_real_public_paths() -> None:
    for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"):
        assert set(_states(boundary)) == {"supported"}


def test_turned_step_observer_retains_malformed_source_as_explicit_invalid_evidence(
    monkeypatch,
) -> None:
    import draftwright.builder as builder
    from draftwright.evaluation.step_analysis import _DOWNSTREAM_BOUNDARIES

    original = builder.build_drawing
    expected_count = 0
    injected = False

    def with_malformed_source(*args, **kwargs):
        nonlocal expected_count, injected
        drawing = original(*args, **kwargs)
        recognition = drawing.recognition()
        if injected or recognition is None:
            return drawing
        expected_count = len(recognition.turned_steps)
        malformed = replace(
            recognition,
            turned_steps=(None, *recognition.turned_steps[1:]),
        )
        monkeypatch.setattr(drawing, "recognition", lambda: malformed)
        injected = True
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_malformed_source)

    facts = _default_observers()["turned-steps"](_shaft())
    invalid = [fact for fact in facts if fact.identity["axis"] == "<invalid>"]

    assert expected_count > 1
    assert len(facts) == expected_count
    assert len(invalid) == 1
    fact = invalid[0]

    assert fact.identity == {
        "axis": "<invalid>",
        "axis_line": "<invalid>",
        "station": "<invalid>",
    }
    assert fact.parameters == {"length": "<invalid>", "diameter": "<invalid>"}
    assert fact.downstream == {boundary: "unknown" for boundary in _DOWNSTREAM_BOUNDARIES}


def test_generated_drawing_evaluator_requires_the_public_build_boundary(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit
    from draftwright.evaluation.step_analysis import _generated_sheet_drawing

    monkeypatch.setattr(
        sheet_emit, "emit_sheet_script", lambda *_args, **_kwargs: "sheet = None\n"
    )

    with pytest.raises(ValueError, match="no drawing build boundary"):
        _generated_sheet_drawing(object(), object())


def test_turned_step_observer_uses_one_build_owned_recognition_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_recognition_evidence
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_recognition_evidence", counted)
    assert _default_observers()["turned-steps"](_shaft())
    assert calls == 1


def test_removing_steps_from_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_steps(self):
        model = original(self)
        return replace(model, features=[item for item in model.features if item.kind != "step"])

    monkeypatch.setattr(Drawing, "model", without_steps)
    assert set(_states("ir_adapter")) == {"unknown"}


@pytest.mark.parametrize("mutation", ["length", "frame", "span", "surplus"])
def test_inconsistent_or_surplus_step_ir_loses_exact_correspondence(
    monkeypatch, mutation: str
) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def damaged(self):
        model = original(self)
        features = list(model.features)
        index = next(i for i, feature in enumerate(features) if feature.kind == "step")
        feature = features[index]
        transverse = next(i for i, axis in enumerate("xyz") if axis != feature.frame.axis)

        if mutation == "length":
            features[index] = replace(feature, length=feature.length + 1)
        elif mutation == "frame":
            origin = list(feature.frame.origin)
            origin[transverse] += 100
            features[index] = replace(feature, frame=replace(feature.frame, origin=tuple(origin)))
        elif mutation == "span":
            shifted = []
            for point in feature.span:
                point = list(point)
                point[transverse] += 100
                shifted.append(tuple(point))
            features[index] = replace(feature, span=tuple(shifted))
        else:
            origin = list(feature.frame.origin)
            origin[transverse] += 100
            span = []
            for point in feature.span:
                point = list(point)
                point[transverse] += 100
                span.append(tuple(point))
            features.append(
                replace(
                    feature,
                    frame=replace(feature.frame, origin=tuple(origin)),
                    span=tuple(span),
                    profile=None,
                    profile_group="foreign",
                )
            )
        return replace(model, features=features)

    monkeypatch.setattr(Drawing, "model", damaged)
    assert set(_states("ir_adapter")) == {"unknown"}
    if mutation == "surplus":
        assert set(_states("generated_code")) == {"unknown"}


def test_missing_per_band_boundary_outcomes_fail_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_turned_step_model_outcomes", lambda *_args: [])
    assert set(_states("ir_adapter")) == {"unknown"}


def test_observer_failure_cannot_pass_zero_band_negative(monkeypatch) -> None:
    import draftwright.builder as builder

    corpus = load_corpus(CORPUS)
    negative = next(case for case in corpus.cases if case.case_id == "turned-step-plain-negative")

    def failed_build(*_args, **_kwargs):
        raise RuntimeError("negative-case probe")

    monkeypatch.setattr(builder, "build_drawing", failed_build)
    with pytest.raises(ObservationError, match="drawing build failed"):
        _default_observers()["turned-steps"](Box(20, 20, 20))
    damaged = evaluate_step_corpus(replace(corpus, cases=(negative,)))
    assert damaged.complete_cases == damaged.conformant_cases == 0
    assert [(issue.layer, issue.family) for issue in damaged.cases[0].diagnostics] == [
        ("analysis", "turned-steps")
    ]


def test_missing_build_owned_recognition_fails_closed(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_recognition(*args, **kwargs):
        drawing = original(*args, **kwargs)
        monkeypatch.setattr(type(drawing), "recognition", lambda _drawing: None)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_recognition)
    with pytest.raises(ObservationError, match="recognition access failed"):
        _default_observers()["turned-steps"](_shaft())


@pytest.mark.parametrize("parameter", ["diameter", "length"])
def test_corrupting_public_step_declaration_loses_dsl_credit(monkeypatch, parameter: str) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.step

    def wrong_value(self, obj=None, **kw):
        kw[parameter] = float(kw[parameter]) + 1.0
        return original(self, obj, **kw)

    monkeypatch.setattr(Sheet, "step", wrong_value)
    assert set(_states("ir_adapter")) == {"supported"}
    assert set(_states("dsl_declaration")) == {"unknown"}


def test_deleting_generated_step_lines_loses_generated_code_credit(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_step_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if "sheet.step(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_step_lines)
    assert set(_states("ir_adapter")) == {"supported"}
    assert set(_states("dsl_declaration")) == {"supported"}
    assert set(_states("generated_code")) == {"unknown"}


def test_generated_code_credit_requires_successful_sheet_build(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    def failed_build(self):
        raise RuntimeError("generated build probe")

    monkeypatch.setattr(Sheet, "build", failed_build)
    assert set(_states("ir_adapter")) == {"supported"}
    assert set(_states("dsl_declaration")) == {"supported"}
    assert set(_states("generated_code")) == {"unknown"}


@pytest.mark.parametrize("variant", ["origin", "translated", "reversed"])
def test_generated_steps_preserve_odd_thousandth_span_midpoints(variant: str) -> None:
    part = _odd_thousandth_shaft(variant)

    assert set(_states("ir_adapter", part=part)) == {"supported"}
    assert set(_states("dsl_declaration", part=part)) == {"supported"}
    assert set(_states("generated_code", part=part)) == {"supported"}


@pytest.mark.parametrize("prefix", ["m_steplen", "m_dia"])
def test_removing_placed_step_measurement_loses_drawing_credit(monkeypatch, prefix: str) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_measurement(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith(prefix))
        drawing.remove(name)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_measurement)
    assert "unsupported" in _states("drawing_consumer")


def test_moving_step_length_witness_off_physical_span_loses_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_span(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_steplen"))
        spec = drawing.registry.named(name)._dw_spec
        spec.p1 = (float(spec.p1[0]) + 3.0, float(spec.p1[1]), 0)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_span)
    assert "unsupported" in _states("drawing_consumer")


@pytest.mark.parametrize("label", ["2× 10", "3× 999 NOTE 10"])
def test_grouped_length_label_requires_exact_count_and_value(monkeypatch, label: str) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_multiplier(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_steplen_typ"))
        drawing.registry.named(name).label = label
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_multiplier)
    states = _states("drawing_consumer", part=_shaft("turned-step-repeated-lengths.step"))
    assert states.count("unsupported") == 3


def test_generated_grouped_length_requires_exact_complete_label(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.build

    def with_false_generated_label(self):
        drawing = original(self)
        name = next(name for name in drawing.annotations() if name.startswith("m_steplen_typ"))
        drawing.registry.named(name).label = "3× 999 NOTE 10"
        return drawing

    monkeypatch.setattr(Sheet, "build", with_false_generated_label)
    part = _shaft("turned-step-repeated-lengths.step")
    assert set(_states("ir_adapter", part=part)) == {"supported"}
    assert set(_states("dsl_declaration", part=part)) == {"supported"}
    assert _states("generated_code", part=part).count("unsupported") == 3


def test_grouped_length_cannot_skip_an_equal_adjacent_member(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_gapped_claim(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_steplen_typ"))
        measurements = drawing.registry.measurement_of(name)
        assert len(measurements) == 3
        identity = drawing.registry.identity_of(name)
        identity["measurement"] = (measurements[0], measurements[2])
        drawing.registry.reapply(name, identity)
        drawing.registry.named(name).label = "2× 10"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_gapped_claim)
    states = _states("drawing_consumer", part=_shaft("turned-step-repeated-lengths.step"))
    assert states.count("unsupported") == 3


def test_moving_diameter_leader_off_physical_surface_loses_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_tip(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_dia"))
        drawing.registry.named(name).position = (50.0, 0.0, 0.0)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_tip)
    assert "unsupported" in _states("drawing_consumer")


def test_native_step_diameter_requires_exact_complete_label(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_false_diameter(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_dia"))
        drawing.registry.named(name).label = "ø999 NOTE 20"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_false_diameter)
    assert "unsupported" in _states("drawing_consumer")


def test_moving_global_od_witness_off_exact_diameter_loses_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_od_span(*args, **kwargs):
        drawing = original(*args, **kwargs)
        spec = drawing.registry.named("dim_od")._dw_spec
        spec.p1 = (float(spec.p1[0]) + 3.0, float(spec.p1[1]), 0)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_od_span)
    part = _shaft("turned-step-axis-z.step")
    assert set(_states("ir_adapter", part=part)) == {"supported"}
    assert set(_states("dsl_declaration", part=part)) == {"supported"}
    assert set(_states("generated_code", part=part)) == {"supported"}
    assert "unsupported" in _states("drawing_consumer", part=part)


def test_global_od_requires_exact_complete_label(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_false_diameter(*args, **kwargs):
        drawing = original(*args, **kwargs)
        drawing.registry.named("dim_od").label = "ø999 NOTE 28"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_false_diameter)
    part = _shaft("turned-step-axis-z.step")
    assert "unsupported" in _states("drawing_consumer", part=part)


def test_severing_step_measurement_provenance_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_provenance(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_steplen"))
        identity = drawing.registry.identity_of(name)
        identity["measurement"] = ()
        drawing.registry.reapply(name, identity)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_provenance)
    assert "unsupported" in _states("drawing_consumer")


def test_deleting_provider_profiles_cannot_shrink_independent_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def without_profiles(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, turned_steps=())

    monkeypatch.setattr(analysis, "_result_from_evidence", without_profiles)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 26
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


@pytest.mark.parametrize("parameter", ["diameter", "length"])
def test_weakening_provider_band_parameter_reduces_fidelity(monkeypatch, parameter: str) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def weakened(*args, **kwargs):
        result = original(*args, **kwargs)
        if parameter == "diameter":
            steps = tuple(
                replace(step, diameter=step.diameter + 0.5) for step in result.turned_steps
            )
        else:
            profiles = {}
            for step in result.turned_steps:
                profile = step.profile
                assert profile is not None
                if profile not in profiles:
                    bounds = list(profile.body_bounds)
                    axial = 2 * "xyz".index(profile.axis)
                    bounds[axial] -= 0.25
                    bounds[axial + 1] += 0.25
                    profiles[profile] = replace(profile, body_bounds=tuple(bounds))
            steps = tuple(
                replace(
                    step,
                    lo=step.lo - 0.25,
                    hi=step.hi + 0.25,
                    profile=profiles[step.profile],
                )
                for step in result.turned_steps
            )
        return replace(result, turned_steps=steps)

    monkeypatch.setattr(analysis, "_result_from_evidence", weakened)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.passed == 26
    assert damaged.parameter_fidelity.total == 52


def test_quality_summary_counts_two_audited_requirements_per_band() -> None:
    from draftwright import build_drawing

    completeness = build_drawing(_shaft()).lint_summary()["quality"]["completeness"]

    assert completeness["by_family"]["turned_steps"] == 6
    assert completeness["placed"] == completeness["requirements"] == 6
    assert completeness["audited_score"] == 1.0
    assert "turned_steps" not in completeness["unscored_recognized_families"]
