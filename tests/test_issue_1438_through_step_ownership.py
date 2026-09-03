"""#1438 — through steps retain direct or exact multi-feature legacy ownership."""

from __future__ import annotations

from dataclasses import replace

import pytest
from b123d_recognisers import Plate as RecognisedPlate
from b123d_recognisers.evidence import build_recognition_evidence
from build123d import Box, Compound, Pos, Rot

from draftwright import build_drawing
from draftwright.analysis import _analyse
from draftwright.annotations.orchestrator import build_model as build_analysis_model
from draftwright.model import detect
from draftwright.recognition_ownership import (
    OccurrenceBinding,
    RecognitionOwnershipBuilder,
)


def _through_step_part():
    return Box(40, 30, 20) - Pos(15, 10, 0) * Box(20, 20, 30)


def _monolithic_rebate():
    return Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)


def _equal_span_compound():
    base = Box(80, 40, 8) + Pos(-36, 0, 24) * Box(8, 40, 40)
    return Compound(children=[Pos(0, -100, 0) * base, Pos(0, 100, 0) * base])


def _occurrences(ownership):
    return tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == "through_steps"
    )


def test_native_through_step_is_represented_by_its_exact_aggregate_feature() -> None:
    drawing = build_drawing(_through_step_part())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    (occurrence,) = _occurrences(ownership)
    binding = ownership.binding_for(occurrence)

    assert binding is not None
    assert binding.disposition == "represented"
    assert binding.reason_code == "through_step_adapter"
    assert binding.features == (binding.feature,)
    assert getattr(binding.feature, "kind", None) == "through_step"
    assert any(binding.feature is feature for feature in drawing.model().features)
    assert ownership.unexpectedly_missing == ()


def test_legacy_projection_records_every_exact_feature_needed_for_both_legs() -> None:
    drawing = build_drawing(Rot(90, 0, 0) * _through_step_part())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    (occurrence,) = _occurrences(ownership)
    binding = ownership.binding_for(occurrence)

    assert binding is not None
    assert binding.disposition == "represented"
    assert binding.reason_code == "through_step_legacy_projection"
    assert [getattr(feature, "kind", None) for feature in binding.features] == [
        "step_level",
        "envelope",
    ]
    assert all(
        any(owner is feature for feature in drawing.model().features) for owner in binding.features
    )
    assert ownership.unexpectedly_missing == ()


def test_equal_valued_occurrences_keep_distinct_direct_owners() -> None:
    base = _through_step_part()
    drawing = build_drawing(Compound(children=[Pos(-60, 0, 0) * base, Pos(60, 0, 0) * base]))
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrences = _occurrences(ownership)
    bindings = tuple(ownership.binding_for(occurrence) for occurrence in occurrences)

    assert len(occurrences) == 2
    assert all(binding is not None for binding in bindings)
    assert len({id(binding.feature) for binding in bindings if binding is not None}) == 2
    assert all(
        binding is not None
        and binding.reason_code == "through_step_adapter"
        and getattr(binding.feature, "kind", None) == "through_step"
        for binding in bindings
    )
    assert ownership.unexpectedly_missing == ()


def test_equal_span_plate_owners_are_body_local_and_follow_removal(monkeypatch) -> None:
    part = _equal_span_compound()
    evidence = build_recognition_evidence(part)
    builder = RecognitionOwnershipBuilder(evidence)
    original_convert = detect.convert
    converted_plates: dict[int, object] = {}

    def record_plate_conversion(record, ctx):
        feature = original_convert(record, ctx)
        if type(record) is RecognisedPlate:
            converted_plates[id(record)] = feature
        return feature

    monkeypatch.setattr(detect, "convert", record_plate_conversion)
    model = detect._build_part_model_from_recognition(
        part,
        evidence.result,
        ownership=builder,
    )
    ownership = builder.snapshot()
    occurrences = _occurrences(ownership)
    bindings = tuple(ownership.binding_for(occurrence) for occurrence in occurrences)

    assert len(occurrences) == 2
    assert all(binding is not None for binding in bindings)
    assert all(
        binding is not None
        and binding.reason_code == "through_step_legacy_projection"
        and [feature.kind for feature in binding.features] == ["plate", "envelope", "plate"]
        for binding in bindings
    )
    assert all(
        any(owner is feature for feature in model.features)
        for binding in bindings
        if binding is not None
        for owner in binding.features
    )
    plate_owner_ids = tuple(
        {id(feature) for feature in binding.features if feature.kind == "plate"}
        for binding in bindings
        if binding is not None
    )
    assert len(plate_owner_ids) == 2
    assert all(len(owner_ids) == 2 for owner_ids in plate_owner_ids)
    assert plate_owner_ids[0].isdisjoint(plate_owner_ids[1])
    plate_occurrences = tuple(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "plates"
    )
    for occurrence, binding in zip(occurrences, bindings, strict=True):
        assert binding is not None
        defining_faces = evidence.defining_faces(occurrence)
        expected_records = tuple(
            evidence.record(plate_occurrence)
            for plate_occurrence in plate_occurrences
            if defining_faces & evidence.defining_faces(plate_occurrence)
        )
        assert {id(converted_plates[id(record)]) for record in expected_records} == {
            id(feature) for feature in binding.features if feature.kind == "plate"
        }
    assert ownership.unexpectedly_missing == ()

    first_binding = bindings[0]
    assert first_binding is not None
    first_plate = next(feature for feature in first_binding.features if feature.kind == "plate")
    builder.remap_feature(first_plate, ())
    after_removal = builder.snapshot()
    assert after_removal.binding_for(occurrences[0]) is None
    assert after_removal.status(occurrences[0]) == "unexpectedly_missing"
    assert after_removal.binding_for(occurrences[1]) is not None


def test_equal_span_compound_has_automatic_and_standalone_model_parity() -> None:
    part = _equal_span_compound()

    automatic = build_drawing(part).model()
    standalone = detect.build_part_model(part)

    assert standalone.features == automatic.features
    assert standalone.datums == automatic.datums
    assert standalone.orientation == automatic.orientation
    assert not [feature for feature in standalone.features if feature.kind == "through_step"]


def test_equal_span_compound_secondary_model_keeps_exact_evidence_parity() -> None:
    part = _equal_span_compound()

    analysis = _analyse(part, "", "", "ISO 2768-m", "", "drawing")
    secondary = build_analysis_model(analysis)

    assert secondary.features == analysis.model.features
    assert not [feature for feature in secondary.features if feature.kind == "through_step"]


def test_ambiguous_scoped_plate_leg_fails_closed() -> None:
    part = _equal_span_compound()
    evidence = build_recognition_evidence(part)
    result = evidence.result
    step = result.through_steps[0]
    step_occurrence = next(
        occurrence
        for occurrence in evidence.features
        if evidence.family(occurrence) == "through_steps" and evidence.record(occurrence) is step
    )
    local_plate = next(
        evidence.record(occurrence)
        for occurrence in evidence.features
        if evidence.family(occurrence) == "plates"
        and evidence.defining_faces(step_occurrence) & evidence.defining_faces(occurrence)
    )
    duplicate = RecognisedPlate(
        axis=local_plate.axis,
        lo=local_plate.lo,
        hi=local_plate.hi,
        u=local_plate.u,
        v=local_plate.v,
    )

    exact_owner_ids = detect._through_step_plate_owner_record_ids(
        evidence,
        step,
        tuple(result.plates),
    )
    assert id(local_plate) in exact_owner_ids
    assert (
        detect._through_step_plate_owner_record_ids(
            evidence,
            replace(step),
            tuple(result.plates),
        )
        is None
    )
    assert (
        detect._through_step_legacy_owners(
            step,
            part.bounding_box(),
            (),
            (),
            (*result.plates, duplicate),
            envelope_emittable=True,
        )
        is not None
    )
    assert (
        detect._through_step_legacy_owners(
            step,
            part.bounding_box(),
            (),
            (),
            (*result.plates, duplicate),
            envelope_emittable=True,
            plate_owner_record_ids=exact_owner_ids | {id(duplicate)},
        )
        is None
    )


def test_asymmetric_plate_centroid_keeps_exact_evidence_owner() -> None:
    base = Box(80, 40, 8) + Pos(-36, 0, 24) * Box(8, 40, 40)
    part = base - Pos(-36, 15, 30) * Box(12, 6, 10)
    drawing = build_drawing(part)
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    (occurrence,) = _occurrences(ownership)
    binding = ownership.binding_for(occurrence)
    assert binding is not None
    assert binding.reason_code == "through_step_legacy_projection"
    assert [feature.kind for feature in binding.features] == ["plate", "envelope", "plate"]
    assert not [feature for feature in drawing.model().features if feature.kind == "through_step"]
    assert ownership.unexpectedly_missing == ()


def test_unrecorded_through_step_fails_closed_as_unexpectedly_missing() -> None:
    evidence = build_recognition_evidence(_through_step_part())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()
    occurrences = _occurrences(ownership)

    assert ownership.expected_conditional == occurrences
    assert all(
        ownership.status(occurrence) == "unexpectedly_missing" for occurrence in occurrences
    )
    assert ownership.unexpectedly_missing == occurrences


def test_recognition_handoff_rejects_evidence_from_another_run() -> None:
    part = _through_step_part()
    first = build_recognition_evidence(part)
    second = build_recognition_evidence(part)

    with pytest.raises(ValueError, match="evidence and result must come from the same run"):
        detect._build_part_model_from_recognition(
            part,
            first.result,
            evidence=second,
        )


def test_multi_feature_binding_is_closed_and_follows_explicit_owner_remaps() -> None:
    evidence = build_recognition_evidence(Rot(90, 0, 0) * _through_step_part())
    builder = RecognitionOwnershipBuilder(evidence)
    (occurrence,) = _occurrences(builder.snapshot())
    record = evidence.record(occurrence)

    class Owner:
        def __init__(self, kind: str) -> None:
            self.kind = kind

    plate = Owner("plate")
    envelope = Owner("envelope")
    with pytest.raises(ValueError, match="unknown multi-feature"):
        builder.bind_many(record, (plate,), reason_code="direct_adapter")
    with pytest.raises(TypeError, match="exact tuple"):
        builder.bind_many(
            record,
            [plate],  # type: ignore[arg-type]
            reason_code="through_step_legacy_projection",
        )
    with pytest.raises(ValueError, match="at least one"):
        builder.bind_many(record, (), reason_code="through_step_legacy_projection")
    with pytest.raises(ValueError, match="repeats"):
        builder.bind_many(
            record,
            (plate, plate),
            reason_code="through_step_legacy_projection",
        )
    with pytest.raises(ValueError, match="requires envelope, plate, or step-level"):
        builder.bind_many(
            record,
            (Owner("through_step"),),
            reason_code="through_step_legacy_projection",
        )

    builder.bind_many(
        record,
        (plate, envelope),
        reason_code="through_step_legacy_projection",
    )
    with pytest.raises(ValueError, match="occurrence already"):
        builder.bind_many(
            record,
            (Owner("step_level"),),
            reason_code="through_step_legacy_projection",
        )
    with pytest.raises(ValueError, match="sole source owner"):
        builder.remap_feature(plate, (Owner("plate"),), ((0,),))
    with pytest.raises(ValueError, match="duplicates an existing owner"):
        builder.remap_feature(plate, (envelope,))

    replacement = Owner("plate")
    builder.remap_feature(plate, (replacement,))
    binding = builder.snapshot().binding_for(occurrence)
    assert binding is not None
    assert binding.features == (replacement, envelope)

    builder.remap_feature(replacement, ())
    missing = builder.snapshot()
    assert missing.binding_for(occurrence) is None
    assert missing.status(occurrence) == "unexpectedly_missing"


def test_failed_shared_owner_remap_is_atomic_and_retryable() -> None:
    base = _through_step_part()
    part = Compound(children=[Pos(-60, 0, 0) * base, Pos(60, 0, 0) * base])
    evidence = build_recognition_evidence(part)
    builder = RecognitionOwnershipBuilder(evidence)
    occurrences = _occurrences(builder.snapshot())

    class Owner:
        def __init__(self, kind: str) -> None:
            self.kind = kind

    shared = Owner("plate")
    first_tail = Owner("envelope")
    second_tail = Owner("step_level")
    builder.bind_many(
        evidence.record(occurrences[0]),
        (shared, first_tail),
        reason_code="through_step_legacy_projection",
    )
    builder.bind_many(
        evidence.record(occurrences[1]),
        (shared, second_tail),
        reason_code="through_step_legacy_projection",
    )
    before = builder.snapshot().bindings

    with pytest.raises(ValueError, match="duplicates an existing owner"):
        builder.remap_feature(shared, (second_tail,))
    assert builder.snapshot().bindings == before

    replacement = Owner("plate")
    builder.remap_feature(shared, (replacement,))
    assert all(binding.features[0] is replacement for binding in builder.snapshot().bindings)


def test_occurrence_binding_rejects_mutable_or_repeated_additional_owners() -> None:
    evidence = build_recognition_evidence(_through_step_part())
    (occurrence,) = _occurrences(RecognitionOwnershipBuilder(evidence).snapshot())
    owner = object()

    with pytest.raises(TypeError, match="exact tuple"):
        OccurrenceBinding(
            occurrence,
            owner,
            additional_features=[object()],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="repeats an IR owner"):
        OccurrenceBinding(occurrence, owner, additional_features=(owner,))


def test_multi_feature_reason_cannot_bind_another_conditional_family() -> None:
    part = Compound(
        children=[
            Pos(-80, 0, 0) * _through_step_part(),
            Pos(80, 0, 0) * _monolithic_rebate(),
        ]
    )
    evidence = build_recognition_evidence(part)
    builder = RecognitionOwnershipBuilder(evidence)
    channel = next(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "channels"
    )

    class StepLevel:
        kind = "step_level"

    with pytest.raises(ValueError, match="does not match occurrence family"):
        builder.bind_many(
            evidence.record(channel),
            (StepLevel(),),
            reason_code="through_step_legacy_projection",
        )


@pytest.mark.parametrize("corruption", ["missing", "unemitted"])
def test_corrupted_final_legacy_owner_selection_fails_closed(monkeypatch, corruption) -> None:
    original = detect._through_step_legacy_owners
    complete_calls = 0

    def corrupt_final_selection(*args, **kwargs):
        nonlocal complete_calls
        result = original(*args, **kwargs)
        if result is None:
            return None
        complete_calls += 1
        if complete_calls != 3:
            return result
        if corruption == "missing":
            return None
        absent_plate = RecognisedPlate(axis="x", lo=5, hi=20, u=0, v=0)
        return (detect._ThroughStepLegacyOwner("plate", absent_plate),)

    monkeypatch.setattr(detect, "_through_step_legacy_owners", corrupt_final_selection)
    drawing = build_drawing(Rot(90, 0, 0) * _through_step_part())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    (occurrence,) = _occurrences(ownership)
    assert ownership.binding_for(occurrence) is None
    assert ownership.status(occurrence) == "unexpectedly_missing"
    assert ownership.unexpectedly_missing == (occurrence,)
