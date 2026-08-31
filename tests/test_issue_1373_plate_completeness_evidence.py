"""#1373 — multi-plate slab completeness uses independent physical facts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos, RegularPolygon, Rot, extrude, import_step

from draftwright.evaluation.step_analysis import (
    ObservationError,
    _default_observers,
    _plate_drawing_outcomes,
    _plate_model_outcomes,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-plates-v1.json"
_CENTER_MIN = (Align.CENTER, Align.CENTER, Align.MIN)


def _tee():
    base = Box(80, 60, 10, align=_CENTER_MIN)
    wall = Pos(0, 0, 10) * Box(80, 10, 40, align=_CENTER_MIN)
    return base + wall


def _states(boundary: str) -> set[str]:
    observed = _default_observers()["plates"](_tee())
    assert len(observed) == 2
    return {fact.downstream[boundary] for fact in observed}


def _annotation_for_plate(drawing) -> str:
    return next(
        name
        for name in drawing.annotations()
        if any(
            identity.parameter == "thickness.length"
            for identity in drawing.registry.measurement_of(name)
        )
    )


def test_versioned_plate_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("plates",)
    assert len(corpus.cases) == 11
    assert sum(len(case.expected) for case in corpus.cases) == 20
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
        "rotational",
        "topology-order-variant",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)
    assert all(
        "'1970-01-01T00:00:00'"
        in (CORPUS.parent / case.provenance["fixture"]).read_text().splitlines()[3]
        for case in corpus.cases
    )


def test_real_plate_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 20
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 20
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 80
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


@pytest.mark.parametrize(
    ("fixture", "axes"),
    (("plate-t-yz.step", {"y", "z"}), ("plate-t-xz.step", {"x", "z"})),
)
def test_every_principal_plate_boundary_is_observed(fixture, axes) -> None:
    observed = _default_observers()["plates"](import_step(CORPUS.parent / fixture))

    assert {fact.identity["axis"] for fact in observed} == axes
    assert all(set(fact.downstream.values()) == {"supported"} for fact in observed)


def test_arbitrary_rigid_motion_survives_the_owned_framed_pipeline() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    baseline = build_drawing(_tee())
    moved = build_drawing(Pos(91, -37, 48) * Rot(31, 47, 13) * _tee(), framed_recognition=True)

    def requirements(drawing):
        recognition = drawing.recognition()
        assert recognition is not None
        outcomes = plate_requirement_outcomes(
            recognition,
            drawing.model().features,
            drawing.registry,
            compile_dimensions(drawing.model()).diagnostics,
        )
        return (
            sorted(round(plate.thickness, 3) for plate in recognition.plates),
            sorted((outcome.parameter_id, outcome.state) for outcome in outcomes),
        )

    assert moved.recognition_frame_decision["status"] == "framed"
    assert requirements(moved) == requirements(baseline)
    assert _plate_drawing_outcomes(tuple(moved.recognition().plates), moved) == [
        "supported",
        "supported",
    ]


def test_plate_ledger_tracks_one_requirement_per_occurrence_and_fails_closed() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_tee())
    recognition = drawing.recognition()
    assert recognition is not None
    outcomes = plate_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )

    assert len(outcomes) == 2
    assert {outcome.parameter_id for outcome in outcomes} == {"thickness.length"}
    assert {outcome.state for outcome in outcomes} == {"placed"}
    missing = plate_requirement_outcomes(
        recognition, drawing.model().features, AnnotationRegistry()
    )
    assert len(missing) == 2
    assert {outcome.state for outcome in missing} == {"missing"}
    unverifiable = plate_requirement_outcomes(
        recognition,
        [feature for feature in drawing.model().features if feature.kind != "plate"],
        AnnotationRegistry(),
    )
    assert len(unverifiable) == 2
    assert {(outcome.state, outcome.requirement_count) for outcome in unverifiable} == {
        ("unverifiable", 1)
    }


def test_plate_ledger_rejects_foreign_malformed_and_duplicate_ir() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    recognition = build_raw_recognition_result(_tee(), rotational=False)
    source = recognition.plates[0]

    class MalformedPlate:
        kind = "plate"
        axis = source.axis
        lo, hi, u, v = source.lo, source.hi, source.u, source.v

        @staticmethod
        def parameters():
            raise TypeError("broken parameter contract")

    assert plate_requirement_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="run's RecognitionResult"):
        plate_requirement_outcomes(object(), (), AnnotationRegistry())
    malformed = plate_requirement_outcomes(recognition, (MalformedPlate(),), AnnotationRegistry())
    assert len(malformed) == 2
    assert {outcome.state for outcome in malformed} == {"unverifiable"}

    drawing = build_drawing(_tee())
    features = [feature for feature in drawing.model().features if feature.kind == "plate"]
    duplicate = plate_requirement_outcomes(
        recognition, (*features, features[0]), AnnotationRegistry()
    )
    assert len(duplicate) == 2
    assert {outcome.state for outcome in duplicate} == {"unverifiable", "missing"}


@pytest.mark.parametrize(
    "corruption", ("raises", "wrong_id", "wrong_value", "missing_span", "wrong_span")
)
def test_plate_ledger_rejects_every_malformed_parameter_contract(corruption) -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_tee())
    recognition = drawing.recognition()
    assert recognition is not None
    features = [feature for feature in drawing.model().features if feature.kind == "plate"]
    feature = features[0]
    parameter = feature.parameters()[0]
    if corruption == "wrong_id":
        parameter = replace(parameter, role="wrong_thickness")
    elif corruption == "wrong_value":
        parameter = replace(parameter, value=parameter.value + 1.0)
    elif corruption == "missing_span":
        parameter = replace(parameter, span=None)
    elif corruption == "wrong_span":
        assert parameter.span is not None
        parameter = replace(parameter, span=tuple(reversed(parameter.span)))

    class ParameterContractProxy:
        kind = "plate"

        def __getattr__(self, name):
            return getattr(feature, name)

        def parameters(self):
            if corruption == "raises":
                raise ValueError("invalid parameter contract")
            return [parameter]

    outcomes = plate_requirement_outcomes(
        recognition,
        (ParameterContractProxy(), *features[1:]),
        AnnotationRegistry(),
    )
    assert len(outcomes) == 2
    assert outcomes[0].state == "unverifiable"


@pytest.mark.parametrize("field", ("axis", "interval", "witness"))
def test_plate_correspondence_rejects_compiler_significant_ir_corruption(field) -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_tee())
    recognition = drawing.recognition()
    assert recognition is not None
    features = [feature for feature in drawing.model().features if feature.kind == "plate"]
    feature = features[0]
    if field == "axis":
        corrupted = replace(feature, axis={"x": "y", "y": "z", "z": "x"}[feature.axis])
    elif field == "interval":
        corrupted = replace(feature, hi=feature.hi + 1.0)
    else:
        corrupted = replace(feature, u=feature.u + 1.0)
    altered = (corrupted, *features[1:])

    assert "unknown" in _plate_model_outcomes(tuple(recognition.plates), recognition, altered)
    outcomes = plate_requirement_outcomes(recognition, altered, AnnotationRegistry())
    assert "unverifiable" in {outcome.state for outcome in outcomes}


def test_plate_ledger_distinguishes_derived_suppressed_dropped_and_missing() -> None:
    from draftwright import build_drawing
    from draftwright.linting.issues import LintIssue
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.model.compiled import DimensionId, compile_dimensions
    from draftwright.registry import AnnotationRegistry

    u_part = import_step(CORPUS.parent / "plate-u-additive.step")
    u_drawing = build_drawing(u_part)
    u_recognition = u_drawing.recognition()
    assert u_recognition is not None
    derived = plate_requirement_outcomes(
        u_recognition,
        u_drawing.model().features,
        u_drawing.registry,
        compile_dimensions(u_drawing.model()).diagnostics,
    )
    assert [outcome.state for outcome in derived].count("inapplicable") == 1
    unevidenced = plate_requirement_outcomes(
        u_recognition,
        u_drawing.model().features,
        AnnotationRegistry(),
        compile_dimensions(u_drawing.model()).diagnostics,
    )
    assert {outcome.state for outcome in unevidenced} == {"missing"}

    drawing = build_drawing(_tee())
    recognition = drawing.recognition()
    assert recognition is not None
    features = [feature for feature in drawing.model().features if feature.kind == "plate"]
    registry = AnnotationRegistry()
    registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="plate_thickness_dropped",
            measurement_ids=(DimensionId(features[1], "thickness.length"),),
            outcome_stage="placement",
        )
    )
    omissions = (
        SimpleNamespace(
            feature=features[0],
            parameter_id="thickness.length",
            authored=True,
            conveyed_by=None,
        ),
    )
    outcomes = plate_requirement_outcomes(
        recognition, drawing.model().features, registry, omissions
    )
    assert {outcome.state for outcome in outcomes} == {"suppressed", "dropped"}


def test_plate_ledger_retains_structured_note_satisfaction_separately_from_ink() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_tee())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "plate")
    registry = AnnotationRegistry()
    registry.add(
        SimpleNamespace(),
        "plate_thickness_note",
        "front",
        feature=feature,
        satisfaction=DimensionId(feature, "thickness.length"),
    )

    outcomes = plate_requirement_outcomes(recognition, drawing.model().features, registry)
    assert {outcome.state for outcome in outcomes} == {
        "satisfied_by_structured_note",
        "missing",
    }


def test_plate_coverage_does_not_duplicate_a_placement_drop() -> None:
    from draftwright import build_drawing
    from draftwright.linting.issues import LintIssue
    from draftwright.linting.plate_coverage import lint_plate_coverage
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_tee())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "plate")
    registry = AnnotationRegistry()
    registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="plate_thickness_dropped",
            measurement_ids=(DimensionId(feature, "thickness.length"),),
            outcome_stage="placement",
        )
    )

    issues = lint_plate_coverage(
        drawing.part,
        recognition=recognition,
        features=drawing.model().features,
        registry=registry,
    )
    assert not [issue for issue in issues if issue.code == "plate_requirement_dropped"]
    assert [issue.code for issue in issues].count("plate_requirement_missing") == 1


def test_exact_overlapping_family_owners_do_not_create_a_second_plate_denominator() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    slotted = Box(60, 180, 20)
    for y in (-45, -15, 15, 45):
        slotted -= Pos(0, y, 0) * Box(30, 8, 20)
    slot_drawing = build_drawing(slotted)
    slot_recognition = slot_drawing.recognition()
    assert slot_recognition is not None
    envelope_only = tuple(
        feature for feature in slot_drawing.model().features if feature.kind == "envelope"
    )
    slot_outcomes = plate_requirement_outcomes(
        slot_recognition, envelope_only, AnnotationRegistry(), part=slotted
    )
    assert len(slot_outcomes) == 5
    assert {outcome.state for outcome in slot_outcomes} == {"inapplicable"}

    boss_part = Box(100, 80, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER)) + (
        Pos(13, -7, 5) * Rot(0, 0, 11) * extrude(RegularPolygon(20, 6), 30)
    )
    boss_drawing = build_drawing(boss_part)
    boss_recognition = boss_drawing.recognition()
    assert boss_recognition is not None
    envelope_only = tuple(
        feature for feature in boss_drawing.model().features if feature.kind == "envelope"
    )
    (boss_outcome,) = plate_requirement_outcomes(
        boss_recognition, envelope_only, AnnotationRegistry(), part=boss_part
    )
    assert boss_outcome.state == "inapplicable"

    malformed = replace(boss_recognition, polygonal_bosses=(object(),))
    (failed_closed,) = plate_requirement_outcomes(
        malformed, envelope_only, AnnotationRegistry(), part=boss_part
    )
    assert failed_closed.state == "unverifiable"
    (failed_closed_with_compiled_ir,) = plate_requirement_outcomes(
        malformed,
        boss_drawing.model().features,
        boss_drawing.registry,
        part=boss_part,
    )
    assert failed_closed_with_compiled_ir.state == "unverifiable"
    source = boss_recognition.polygonal_bosses[0]
    for corrupted in (
        replace(source, side_count=5),
        replace(source, flat_centres=(source.center,) * 6),
        replace(
            source,
            flat_directions=(
                source.flat_directions[1],
                source.flat_directions[0],
                *source.flat_directions[2:],
            ),
        ),
        replace(
            source,
            flat_centres=((10**10000, *source.flat_centres[0][1:]), *source.flat_centres[1:]),
        ),
        replace(
            source,
            flat_directions=(
                (*source.flat_directions[0][:2], 0.0004),
                *source.flat_directions[1:],
            ),
        ),
    ):
        malformed = replace(boss_recognition, polygonal_bosses=(corrupted,))
        (failed_closed,) = plate_requirement_outcomes(
            malformed, envelope_only, AnnotationRegistry(), part=boss_part
        )
        assert failed_closed.state == "unverifiable"


def test_raw_slot_owner_requires_one_schema_for_every_member() -> None:
    from b123d_recognisers import recognise_slot_patterns

    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import (
        _validated_recognised_pattern,
        plate_requirement_outcomes,
    )
    from draftwright.registry import AnnotationRegistry

    part = Box(60, 180, 20)
    for y in (-45, -15, 15, 45):
        part -= Pos(0, y, 0) * Box(30, 8, 20)
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    (pattern,) = recognition.slot_patterns
    envelope_only = tuple(
        feature for feature in drawing.model().features if feature.kind == "envelope"
    )
    member = pattern.slots[1]
    (provider_noisy_pattern,) = recognise_slot_patterns(
        (
            pattern.slots[0],
            replace(member, w_center=member.w_center + 0.05),
            *pattern.slots[2:],
        )
    )
    assert _validated_recognised_pattern(provider_noisy_pattern) is not None
    foreign_body = list(member.body_key)
    foreign_body[0] += 1
    mutations = (
        replace(member, body_key=tuple(foreign_body)),
        replace(member, long_axis=member.width_axis),
        replace(member, d_hi=member.d_hi + 1),
        replace(member, length=member.length + 7),
        replace(member, w_center=member.w_center + 5),
        replace(member, width=10**10000),
        replace(member, body_key=(10**10000, *member.body_key[1:])),
    )

    for mutated in mutations:
        slots = (pattern.slots[0], mutated, *pattern.slots[2:])
        malformed = replace(
            recognition,
            slot_patterns=(replace(pattern, slots=slots),),
        )
        outcomes = plate_requirement_outcomes(
            malformed,
            envelope_only,
            AnnotationRegistry(),
            part=part,
        )
        assert len(outcomes) == 5
        assert {outcome.state for outcome in outcomes} == {"unverifiable"}
        compiled_outcomes = plate_requirement_outcomes(
            malformed,
            drawing.model().features,
            drawing.registry,
            part=part,
        )
        assert len(compiled_outcomes) == 5
        assert {outcome.state for outcome in compiled_outcomes} == {"unverifiable"}

    malformed = replace(
        recognition,
        slot_patterns=(replace(pattern, slots=(object(), *pattern.slots[1:])),),
    )
    outcomes = plate_requirement_outcomes(
        malformed,
        drawing.model().features,
        drawing.registry,
        part=part,
    )
    assert len(outcomes) == 5
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}

    too_short = replace(
        recognition,
        slot_patterns=(replace(pattern, slots=pattern.slots[:2]),),
    )
    outcomes = plate_requirement_outcomes(
        too_short,
        drawing.model().features,
        drawing.registry,
        part=part,
    )
    assert len(outcomes) == 5
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


def test_raw_slot_grid_uses_only_members_crossing_each_plate_witness() -> None:
    from b123d_recognisers import recognise_slot_patterns

    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import (
        _validated_recognised_pattern,
        plate_requirement_outcomes,
    )
    from draftwright.registry import AnnotationRegistry

    part = Box(140, 180, 20)
    for x in (-40, 0, 40):
        for y in (-45, -15, 15, 45):
            part -= Pos(x, y, 0) * Box(24, 8, 20)
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    assert len(recognition.slot_patterns) == 1
    assert len(recognition.slot_patterns[0].slots) == 12
    assert len(recognition.plates) == 5
    envelope_only = tuple(
        feature for feature in drawing.model().features if feature.kind == "envelope"
    )

    outcomes = plate_requirement_outcomes(
        recognition,
        envelope_only,
        AnnotationRegistry(),
        part=part,
    )

    assert len(outcomes) == 5
    assert {outcome.state for outcome in outcomes} == {"inapplicable"}

    pattern = recognition.slot_patterns[0]
    member = pattern.slots[0]
    moved = replace(member, lo=member.lo + 0.9, hi=member.hi + 0.9)
    slots = (moved, *pattern.slots[1:])
    shifted_center = tuple(
        sum(slot.location[index] for slot in slots) / len(slots) for index in range(3)
    )
    outside_row_tolerance = replace(pattern, slots=slots, center=shifted_center)
    assert not [
        candidate for candidate in recognise_slot_patterns(slots) if hasattr(candidate, "rows")
    ]
    assert _validated_recognised_pattern(outside_row_tolerance) is None

    malformed = replace(recognition, slot_patterns=(outside_row_tolerance,))
    failed_closed = plate_requirement_outcomes(
        malformed,
        envelope_only,
        AnnotationRegistry(),
        part=part,
    )
    assert len(failed_closed) == 5
    assert {outcome.state for outcome in failed_closed} == {"unverifiable"}


@pytest.mark.parametrize("pattern_kind", ["linear", "grid"])
def test_provider_rotated_slot_patterns_pass_aggregate_validation(pattern_kind: str) -> None:
    from math import cos, radians, sin

    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import _validated_recognised_pattern

    angle = radians(15)
    column = (cos(angle), sin(angle))
    row = (-sin(angle), cos(angle))
    if pattern_kind == "linear":
        locations = [(offset * column[0], offset * column[1]) for offset in (-45, -15, 15, 45)]
    else:
        locations = [
            (
                column_offset * column[0] + row_offset * row[0],
                column_offset * column[1] + row_offset * row[1],
            )
            for row_offset in (-40, 0, 40)
            for column_offset in (-45, -15, 15, 45)
        ]
    part = Box(240, 240, 20)
    for x, y in locations:
        part -= Pos(x, y, 0) * Box(24, 8, 20)
    recognition = build_drawing(part).recognition()
    assert recognition is not None
    assert len(recognition.slot_patterns) == 1

    assert _validated_recognised_pattern(recognition.slot_patterns[0]) is not None


def test_overlapping_family_ownership_cannot_cross_disconnected_bodies() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    slotted = Box(60, 180, 20)
    for y in (-45, -15, 15, 45):
        slotted -= Pos(0, y, 0) * Box(30, 8, 20)
    remote_tee = Box(80, 60, 10, align=_CENTER_MIN) + Pos(0, 0, 10) * Box(
        80, 22, 40, align=_CENTER_MIN
    )
    slot_compound = slotted + Pos(150, 0, 0) * remote_tee
    slot_drawing = build_drawing(slot_compound)
    slot_recognition = slot_drawing.recognition()
    assert slot_recognition is not None
    envelope_only = tuple(
        feature for feature in slot_drawing.model().features if feature.kind == "envelope"
    )
    slot_outcomes = plate_requirement_outcomes(
        slot_recognition, envelope_only, AnnotationRegistry(), part=slot_compound
    )
    same_span = [
        plate
        for plate in slot_recognition.plates
        if plate.axis == "y" and plate.lo == -11.0 and plate.hi == 11.0
    ]
    assert len(same_span) == 2
    assert len({(round(plate.u, 3), round(plate.v, 3)) for plate in same_span}) == 2
    assert {outcome.state for outcome in slot_outcomes if outcome.source_at[0] == 0.0} == {
        "inapplicable"
    }
    assert {outcome.state for outcome in slot_outcomes if outcome.source_at[0] > 100.0} == {
        "unverifiable"
    }

    boss_body = Box(100, 80, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER)) + (
        Pos(13, -7, 5) * Rot(0, 0, 11) * extrude(RegularPolygon(20, 6), 30)
    )
    boss_compound = Pos(-100, 0, 0) * boss_body + Pos(100, 0, 0) * _tee()
    boss_drawing = build_drawing(boss_compound)
    boss_recognition = boss_drawing.recognition()
    assert boss_recognition is not None
    envelope_only = tuple(
        feature for feature in boss_drawing.model().features if feature.kind == "envelope"
    )
    boss_outcomes = plate_requirement_outcomes(
        boss_recognition, envelope_only, AnnotationRegistry(), part=boss_compound
    )
    assert {outcome.state for outcome in boss_outcomes} == {"unverifiable"}

    boss = boss_recognition.polygonal_bosses[0]
    centres = list(boss.flat_centres)
    centres[0] = (100.0, 0.0, 20.0)
    malformed = replace(
        boss_recognition,
        polygonal_bosses=(replace(boss, flat_centres=tuple(centres)),),
    )
    malformed_outcomes = plate_requirement_outcomes(
        malformed,
        envelope_only,
        AnnotationRegistry(),
        part=boss_compound,
    )
    assert {outcome.state for outcome in malformed_outcomes} == {"unverifiable"}


def test_boss_owner_requires_one_solid_and_the_complete_axis_span() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    narrow_boss = Box(100, 80, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER)) + (
        Pos(13, 0, 5) * extrude(RegularPolygon(10, 6), 20)
    )
    narrow_drawing = build_drawing(narrow_boss)
    narrow_recognition = narrow_drawing.recognition()
    assert narrow_recognition is not None
    envelope_only = tuple(
        feature for feature in narrow_drawing.model().features if feature.kind == "envelope"
    )
    (narrow_outcome,) = plate_requirement_outcomes(
        narrow_recognition,
        envelope_only,
        AnnotationRegistry(),
        part=narrow_boss,
    )
    assert narrow_outcome.state == "inapplicable"

    multi_axis = (
        Box(120, 80, 10, align=_CENTER_MIN)
        + Pos(0, 0, 10) * extrude(RegularPolygon(10, 6), 20)
        + Pos(-40, 0, 10) * Box(10, 80, 40, align=_CENTER_MIN)
    )
    multi_drawing = build_drawing(multi_axis)
    multi_recognition = multi_drawing.recognition()
    assert multi_recognition is not None
    envelope_only = tuple(
        feature for feature in multi_drawing.model().features if feature.kind == "envelope"
    )
    multi_outcomes = plate_requirement_outcomes(
        multi_recognition,
        envelope_only,
        AnnotationRegistry(),
        part=multi_axis,
    )
    assert len(multi_outcomes) == 2
    assert {outcome.state for outcome in multi_outcomes} == {"unverifiable"}


def test_bored_boss_uses_material_supports_for_same_solid_ownership() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    base = Box(100, 80, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    boss = Pos(0, 0, 5) * extrude(RegularPolygon(20, 6), 30)
    bore = Pos(0, 0, -5) * Cylinder(3, 45, align=_CENTER_MIN)
    part = base + boss - bore
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    assert len(recognition.polygonal_bosses) == 1
    assert len(recognition.holes) == 1
    envelope_only = tuple(
        feature for feature in drawing.model().features if feature.kind == "envelope"
    )

    (outcome,) = plate_requirement_outcomes(
        recognition,
        envelope_only,
        AnnotationRegistry(),
        part=part,
    )

    assert outcome.state == "inapplicable"


def test_offcentre_boss_ownership_does_not_require_material_at_plate_centroid() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    base = Box(100, 80, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    boss = Pos(20, 0, 5) * extrude(RegularPolygon(10, 6), 20)
    unrelated_bore = Pos(0, 0, -5) * Cylinder(3, 45, align=_CENTER_MIN)
    part = base + boss - unrelated_bore
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    assert len(recognition.polygonal_bosses) == 1
    assert len(recognition.holes) == 1
    envelope_only = tuple(
        feature for feature in drawing.model().features if feature.kind == "envelope"
    )

    (outcome,) = plate_requirement_outcomes(
        recognition,
        envelope_only,
        AnnotationRegistry(),
        part=part,
    )

    assert outcome.state == "inapplicable"


def test_void_plate_centroid_cannot_borrow_a_remote_boss_body() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    hex_body = Pos(-100, 0, 0) * (
        Box(80, 60, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        + Pos(0, 0, 5) * extrude(RegularPolygon(10, 6), 20)
    )
    octagon = Pos(100, 0, 0) * (
        Box(80, 60, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        + Pos(0, 0, 5) * extrude(RegularPolygon(10, 8), 20)
    )
    remote_bore = Pos(100, 0, -5) * Cylinder(3, 45, align=_CENTER_MIN)
    part = hex_body + (octagon - remote_bore)
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    assert len(recognition.plates) == 2
    assert len(recognition.polygonal_bosses) == 1
    assert len(recognition.holes) == 1
    envelope_only = tuple(
        feature for feature in drawing.model().features if feature.kind == "envelope"
    )

    outcomes = plate_requirement_outcomes(
        recognition,
        envelope_only,
        AnnotationRegistry(),
        part=part,
    )

    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


def test_void_plate_centroid_does_not_use_a_nested_disconnected_boss() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    outer = (
        Box(100, 80, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        + Pos(0, 0, 5) * extrude(RegularPolygon(12, 8), 20)
        - Pos(0, 0, -5) * Cylinder(8, 40, align=_CENTER_MIN)
    )
    inner = Pos(3, 0, 0) * (
        Box(6, 6, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        + Pos(0, 0, 5) * extrude(RegularPolygon(2.5, 6), 20)
    )
    part = outer + inner
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    assert len(part.solids()) == 2
    assert len(recognition.plates) == 2
    assert len(recognition.polygonal_bosses) == 1
    envelope_only = tuple(
        feature for feature in drawing.model().features if feature.kind == "envelope"
    )

    outcomes = plate_requirement_outcomes(
        recognition,
        envelope_only,
        AnnotationRegistry(),
        part=part,
    )

    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


def test_provider_hexagon_invariant_does_not_narrow_public_owner_geometry() -> None:
    from math import cos, pi, sin

    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import (
        _polygonal_boss_dependencies,
        _without_provider_owned_ir,
    )
    from draftwright.model import polygonal_boss

    part = Box(100, 80, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER)) + (
        Pos(0, 0, 5) * extrude(RegularPolygon(10, 8), 20)
    )
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    (source,) = recognition.plates
    envelope = next(feature for feature in drawing.model().features if feature.kind == "envelope")
    side_count = 8
    directions = tuple(
        (cos(angle), sin(angle), 0.0)
        for angle in (2 * pi * index / side_count for index in range(side_count))
    )
    centres = tuple((8 * direction[0], 8 * direction[1], 15.0) for direction in directions)
    owner = polygonal_boss(
        side_count=side_count,
        across_flats=16,
        height=20,
        at=(0, 0, 15),
        axis="z",
        span=((0, 0, 5), (0, 0, 25)),
        flat_directions=directions,
        flat_centres=centres,
    )

    assert _polygonal_boss_dependencies(source, (envelope, owner)) == (
        (envelope, "height.length"),
        (owner, "boss_height.length"),
    )

    hex_part = Box(100, 80, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER)) + (
        Pos(40, 0, 5) * extrude(RegularPolygon(10, 6), 20)
    )
    hex_drawing = build_drawing(hex_part)
    hex_recognition = hex_drawing.recognition()
    assert hex_recognition is not None
    provider_hex = next(
        feature for feature in hex_drawing.model().features if feature.kind == "polygonal_boss"
    )

    assert _without_provider_owned_ir(
        hex_recognition,
        (envelope, owner, provider_hex),
    ) == (envelope, owner)


def test_unrelated_provider_hexagon_does_not_disable_public_octagon_ownership() -> None:
    from math import cos, pi, sin

    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.model import polygonal_boss
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    octagon = Pos(-100, 0, 0) * Box(
        80, 60, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER)
    ) + (Pos(-100, 0, 5) * extrude(RegularPolygon(10, 8), 20))
    hexagon = Pos(100, 0, 0) * Box(
        80, 60, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER)
    ) + (Pos(100, 0, 5) * extrude(RegularPolygon(10, 6), 20))
    part = octagon + hexagon
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    assert len(recognition.plates) == 2
    assert len(recognition.polygonal_bosses) == 1
    envelope = next(feature for feature in drawing.model().features if feature.kind == "envelope")
    provider_hex = next(
        feature for feature in drawing.model().features if feature.kind == "polygonal_boss"
    )
    side_count = 8
    directions = tuple(
        (cos(angle), sin(angle), 0.0)
        for angle in (2 * pi * index / side_count for index in range(side_count))
    )
    centres = tuple((-100 + 8 * direction[0], 8 * direction[1], 15.0) for direction in directions)
    public_octagon = polygonal_boss(
        side_count=side_count,
        across_flats=16,
        height=20,
        at=(-100, 0, 15),
        axis="z",
        span=((-100, 0, 5), (-100, 0, 25)),
        flat_directions=directions,
        flat_centres=centres,
    )
    registry = AnnotationRegistry()
    registry.add(
        SimpleNamespace(),
        "envelope_height",
        "front",
        feature=envelope,
        measurement=DimensionId(envelope, "height.length"),
    )
    registry.add(
        SimpleNamespace(),
        "octagon_height",
        "front",
        feature=public_octagon,
        measurement=DimensionId(public_octagon, "boss_height.length"),
    )

    outcomes = plate_requirement_outcomes(
        recognition,
        (envelope, provider_hex, public_octagon),
        registry,
        part=part,
    )

    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes if outcome.source_at[0] < 0} == {"inapplicable"}
    assert {outcome.state for outcome in outcomes if outcome.source_at[0] > 0} == {"unverifiable"}
    without_public_owner = plate_requirement_outcomes(
        recognition,
        (envelope, provider_hex),
        AnnotationRegistry(),
        part=part,
    )
    assert {outcome.state for outcome in without_public_owner} == {"unverifiable"}


def test_step_level_alternate_must_land_before_plate_intervals_are_inapplicable() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions
    from draftwright.registry import AnnotationRegistry

    part = Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    plan = compile_dimensions(drawing.model())

    complete = plate_requirement_outcomes(
        recognition, drawing.model().features, drawing.registry, plan.diagnostics
    )
    missing = plate_requirement_outcomes(
        recognition, drawing.model().features, AnnotationRegistry(), plan.diagnostics
    )
    assert len(complete) == len(missing) == 2
    assert {outcome.state for outcome in complete} == {"inapplicable"}
    assert {outcome.state for outcome in missing} == {"unverifiable"}


def test_derived_plate_drawing_credit_requires_verified_dependency_ink() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(import_step(CORPUS.parent / "plate-u-additive.step"))
    recognition = drawing.recognition()
    assert recognition is not None
    channel = next(feature for feature in drawing.model().features if feature.kind == "channel")
    name = next(
        name
        for name in drawing.registry.names()
        if any(
            identity.feature == channel and identity.parameter == "channel_width.length"
            for identity in drawing.registry.measurement_of(name)
        )
    )
    drawing.registry.named(name).label = "999"

    outcomes = _plate_drawing_outcomes(tuple(recognition.plates), drawing)

    assert outcomes.count("unsupported") == 1
    assert outcomes.count("supported") == 2


def test_plate_observer_uses_one_build_owned_recognition_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", counted)
    assert _default_observers()["plates"](_tee())
    assert calls == 1


def test_removing_plates_from_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_plates(self):
        model = original(self)
        return replace(model, features=[f for f in model.features if f.kind != "plate"])

    monkeypatch.setattr(Drawing, "model", without_plates)
    assert _states("ir_adapter") == {"unknown"}


def test_missing_per_plate_boundary_outcomes_fail_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_plate_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_observer_fails_closed_when_build_or_recognition_is_unavailable(monkeypatch) -> None:
    import draftwright.builder as builder

    def failed_build(*_args, **_kwargs):
        raise RuntimeError("probe")

    monkeypatch.setattr(builder, "build_drawing", failed_build)
    with pytest.raises(ObservationError, match="drawing build failed: probe"):
        _default_observers()["plates"](_tee())


def test_observer_fails_closed_when_built_recognition_is_unavailable(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    monkeypatch.setattr(Drawing, "recognition", lambda _self: None)
    with pytest.raises(ObservationError, match="recognition access failed"):
        _default_observers()["plates"](_tee())


def test_observer_failure_cannot_pass_a_zero_plate_negative_case(monkeypatch) -> None:
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
        ("analysis", "plates")
    ]


def test_corrupting_public_plate_declaration_loses_declaration_credit(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.plate

    def wrong_interval(self, obj=None, **kw):
        kw["hi"] = float(kw["hi"]) + 1.0
        return original(self, obj, **kw)

    monkeypatch.setattr(Sheet, "plate", wrong_interval)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_plate_lines_loses_generated_code_credit(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_plate_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(line for line in source.splitlines() if ".plate(" not in line)

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_plate_lines)
    assert _states("generated_code") == {"unknown"}


def test_wrong_plate_ink_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        drawing.registry.named(_annotation_for_plate(drawing)).label = "999"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_ink)
    assert "unsupported" in _states("drawing_consumer")


def test_severing_one_plate_measurement_claim_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_claim(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = _annotation_for_plate(drawing)
        identity = drawing.registry.identity_of(name)
        drawing.registry.reapply(name, {**identity, "measurement": ()})
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_claim)
    assert "unsupported" in _states("drawing_consumer")


def test_deleting_provider_plates_cannot_shrink_the_independent_denominator(
    monkeypatch,
) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def without_plates(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, plates=())

    monkeypatch.setattr(analysis, "build_raw_recognition_result", without_plates)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 20
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


def test_malformed_provider_plate_cannot_pass_or_shrink_the_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def malformed(*args, **kwargs):
        result = original(*args, **kwargs)
        if not result.plates:
            return result
        return replace(result, plates=(object(), *result.plates[1:]))

    monkeypatch.setattr(analysis, "build_raw_recognition_result", malformed)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.missed > 0
    assert damaged.complete_cases < len(damaged.cases)
    assert sum(case.detection.missed for case in damaged.cases) >= 1


def test_symmetric_provider_interval_damage_preserves_identity_but_loses_fidelity(
    monkeypatch,
) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def weakened(*args, **kwargs):
        result = original(*args, **kwargs)
        plates = tuple(
            replace(plate, lo=plate.lo - 0.05, hi=plate.hi + 0.05) for plate in result.plates
        )
        return replace(result, plates=plates)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", weakened)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.passed == 0
    assert damaged.parameter_fidelity.total == 20


def test_shifting_provider_interval_reduces_detection_recall(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def shifted(*args, **kwargs):
        result = original(*args, **kwargs)
        plates = tuple(
            replace(plate, lo=plate.lo + 1.0, hi=plate.hi + 1.0) for plate in result.plates
        )
        return replace(result, plates=plates)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", shifted)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 0.0
    assert damaged.detection.missed == 20
    assert damaged.detection.false_positives == 20


@pytest.mark.parametrize("field", ("u", "v"))
def test_shifting_provider_transverse_witness_reduces_detection_recall(monkeypatch, field) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def shifted(*args, **kwargs):
        result = original(*args, **kwargs)
        plates = tuple(
            replace(plate, **{field: getattr(plate, field) + 1.0}) for plate in result.plates
        )
        return replace(result, plates=plates)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", shifted)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 0.0
    assert damaged.detection.missed == 20
    assert damaged.detection.false_positives == 20


def test_deleting_plate_declarations_cannot_shrink_quality_denominator() -> None:
    from draftwright import Sheet, build_drawing

    complete = build_drawing(_tee())
    sparse = Sheet(_tee())
    envelope = sparse.envelope()
    sparse.dimension(envelope, "width.length")

    complete_quality = complete.lint_summary()["quality"]["completeness"]
    sparse_summary = sparse.build().lint_summary()
    sparse_quality = sparse_summary["quality"]["completeness"]
    assert complete_quality["requirements"] == sparse_quality["requirements"] == 6
    assert complete_quality["by_family"]["plates"] == 2
    assert complete_quality["placed"] == 6
    assert sparse_quality["unverifiable"] == 6
    assert complete_quality["audited_score"] == 1.0
    assert sparse_quality["audited_score"] == 0.0
    assert [
        issue
        for issue in sparse_summary["issues"]
        if issue["code"] == "plate_requirement_unverifiable"
    ]


def test_single_flat_plate_is_envelope_owned_and_adds_no_plate_requirement() -> None:
    from draftwright import build_drawing
    from draftwright.linting.plate_coverage import plate_requirement_outcomes

    drawing = build_drawing(import_step(CORPUS.parent / "plate-single-negative.step"))
    recognition = drawing.recognition()
    assert recognition is not None
    assert recognition.plates == ()
    assert (
        plate_requirement_outcomes(recognition, drawing.model().features, drawing.registry) == []
    )
