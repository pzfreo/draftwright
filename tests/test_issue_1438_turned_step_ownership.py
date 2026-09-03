"""#1438 — accepted turned steps retain direct or groove ownership."""

from __future__ import annotations

from pathlib import Path

import pytest
from b123d_recognisers.evidence import build_recognition_evidence
from build123d import Align, Cylinder, Pos, import_step

from draftwright import build_drawing
from draftwright.model import detect
from draftwright.recognition_ownership import (
    RecognitionOwnershipBuilder,
)

FIXTURES = Path(__file__).parent / "fixtures" / "evaluation"


def _stepped_shaft():
    return Cylinder(20, 60) + Pos(0, 0, 45) * Cylinder(30, 30)


def _occurrences(ownership, family: str):
    return tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == family
    )


def test_each_plain_turned_step_is_represented_by_its_exact_step_feature() -> None:
    drawing = build_drawing(_stepped_shaft())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrences = _occurrences(ownership, "turned_steps")
    bindings = tuple(ownership.binding_for(occurrence) for occurrence in occurrences)

    assert len(occurrences) == 2
    assert all(binding is not None for binding in bindings)
    assert all(
        binding is not None
        and binding.disposition == "represented"
        and binding.reason_code == "turned_step_adapter"
        and binding.feature.kind == "step"
        and any(binding.feature is feature for feature in drawing.model().features)
        for binding in bindings
    )
    assert ownership.unexpectedly_missing == ()


def test_groove_floor_step_is_absorbed_by_the_exact_groove_feature() -> None:
    drawing = build_drawing(import_step(FIXTURES / "groove-narrow.step"))
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    step_occurrences = _occurrences(ownership, "turned_steps")
    groove_occurrence = _occurrences(ownership, "grooves")[0]
    groove_binding = ownership.binding_for(groove_occurrence)
    step_bindings = tuple(ownership.binding_for(occurrence) for occurrence in step_occurrences)
    absorbed = tuple(
        binding
        for binding in step_bindings
        if binding is not None and binding.disposition == "absorbed"
    )

    assert len(step_occurrences) == 3
    assert groove_binding is not None
    assert groove_binding.disposition == "represented"
    assert groove_binding.reason_code == "direct_adapter"
    assert len(absorbed) == 1
    assert absorbed[0].reason_code == "turned_step_groove_owner"
    assert absorbed[0].feature is groove_binding.feature
    assert absorbed[0].feature.kind == "groove"
    assert (
        sum(
            binding is not None and binding.reason_code == "turned_step_adapter"
            for binding in step_bindings
        )
        == 2
    )
    assert ownership.unexpectedly_missing == ()


def test_ambiguous_groove_takeover_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shaft = Cylinder(10, 60)
    shaft -= Pos(0, 0, 15) * (Cylinder(10, 4) - Cylinder(8, 4))
    shaft -= Pos(0, 0, -15) * (Cylinder(10, 4) - Cylinder(7, 4))
    real_match = detect.groove_owns_turned_step_band

    def two_grooves_claim_lower_band(groove, step) -> bool:
        if step.lo == pytest.approx(-17.0) and step.hi == pytest.approx(-13.0):
            return True
        return real_match(groove, step)

    monkeypatch.setattr(
        detect,
        "groove_owns_turned_step_band",
        two_grooves_claim_lower_band,
    )
    drawing = build_drawing(shaft)
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    step_occurrences = _occurrences(ownership, "turned_steps")
    ambiguous = next(
        occurrence
        for occurrence in step_occurrences
        if ownership.evidence.record(occurrence).lo == pytest.approx(-17.0)
    )
    groove_bindings = tuple(
        ownership.binding_for(occurrence) for occurrence in _occurrences(ownership, "grooves")
    )

    assert len(groove_bindings) == 2
    assert all(
        binding is not None
        and binding.disposition == "represented"
        and binding.reason_code == "direct_adapter"
        and binding.feature.kind == "groove"
        for binding in groove_bindings
    )
    assert ownership.binding_for(ambiguous) is None
    assert ownership.status(ambiguous) == "unexpectedly_missing"
    assert ambiguous in ownership.unexpectedly_missing


def test_one_groove_cannot_absorb_two_nearby_accepted_step_bands() -> None:
    def bottom_aligned_cylinder(radius: float, height: float):
        return Cylinder(
            radius,
            height,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )

    width = 0.2
    part = bottom_aligned_cylinder(10, 10)
    part += Pos(0, 0, 10) * bottom_aligned_cylinder(9, width)
    part += Pos(0, 0, 10 + width) * bottom_aligned_cylinder(10, width)
    part += Pos(0, 0, 10 + 2 * width) * bottom_aligned_cylinder(12, 20 - 2 * width)
    drawing = build_drawing(part)
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    groove_occurrences = _occurrences(ownership, "grooves")
    step_occurrences = _occurrences(ownership, "turned_steps")
    ambiguous = tuple(
        occurrence
        for occurrence in step_occurrences
        if 10.0 <= ownership.evidence.record(occurrence).lo < 10.4
    )

    assert len(groove_occurrences) == 1
    groove_binding = ownership.binding_for(groove_occurrences[0])
    assert groove_binding is not None
    assert groove_binding.disposition == "represented"
    assert groove_binding.feature.kind == "groove"
    assert len(step_occurrences) == 4
    assert tuple(ownership.evidence.record(occurrence).diameter for occurrence in ambiguous) == (
        18.0,
        20.0,
    )
    assert all(ownership.binding_for(occurrence) is None for occurrence in ambiguous)
    assert all(ownership.status(occurrence) == "unexpectedly_missing" for occurrence in ambiguous)
    assert ownership.unexpectedly_missing == ambiguous


def test_unbound_turned_step_fails_closed_as_unexpectedly_missing() -> None:
    evidence = build_recognition_evidence(_stepped_shaft())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()
    occurrences = _occurrences(ownership, "turned_steps")

    assert ownership.expected_conditional == occurrences
    assert all(occurrence in ownership.owner_expected_occurrences for occurrence in occurrences)
    assert all(
        ownership.status(occurrence) == "unexpectedly_missing" for occurrence in occurrences
    )
    assert ownership.unexpectedly_missing == occurrences


def test_groove_absorption_requires_an_exact_same_run_groove_owner() -> None:
    evidence = build_recognition_evidence(_stepped_shaft())
    builder = RecognitionOwnershipBuilder(evidence)
    step = evidence.record(_occurrences(builder.snapshot(), "turned_steps")[0])

    class UnownedGrooveFeature:
        kind = "groove"

    with pytest.raises(ValueError, match="requires an existing exact recognition owner"):
        builder.absorb_into(
            step,
            UnownedGrooveFeature(),
            reason_code="turned_step_groove_owner",
        )


def test_one_groove_feature_cannot_absorb_two_exact_same_run_steps() -> None:
    evidence = build_recognition_evidence(import_step(FIXTURES / "groove-narrow.step"))
    builder = RecognitionOwnershipBuilder(evidence)
    initial = builder.snapshot()
    groove = evidence.record(_occurrences(initial, "grooves")[0])
    step_occurrences = _occurrences(initial, "turned_steps")
    steps = tuple(evidence.record(occurrence) for occurrence in step_occurrences)
    owned_step = next(step for step in steps if detect.groove_owns_turned_step_band(groove, step))
    other_step = next(step for step in steps if step is not owned_step)
    owned_occurrence = next(
        occurrence for occurrence in step_occurrences if evidence.record(occurrence) is owned_step
    )
    other_occurrence = next(
        occurrence for occurrence in step_occurrences if evidence.record(occurrence) is other_step
    )

    class GrooveFeature:
        kind = "groove"

    owner = GrooveFeature()
    builder.bind(groove, owner)
    builder.absorb_into(owned_step, owner, reason_code="turned_step_groove_owner")

    with pytest.raises(ValueError, match="already absorbs a turned-step occurrence"):
        builder.absorb_into(other_step, owner, reason_code="turned_step_groove_owner")

    ownership = builder.snapshot()
    assert ownership.binding_for(owned_occurrence) is not None
    assert ownership.binding_for(other_occurrence) is None
