"""#1438: settled ownerless consumer policy is explicit per accepted occurrence."""

from __future__ import annotations

from pathlib import Path

import pytest
from b123d_recognisers.evidence import build_recognition_evidence
from build123d import (
    Align,
    Box,
    BuildPart,
    BuildSketch,
    Cylinder,
    Plane,
    Pos,
    RegularPolygon,
    Rot,
    extrude,
    import_step,
)

from draftwright import build_drawing
from draftwright import recognition_ownership as ownership_module
from draftwright.recogniser_contract import consumer_capability_declaration
from draftwright.recogniser_policy import UNSUPPORTED_FAMILIES, ownerless_occurrence_policy
from draftwright.recognition_ownership import (
    OccurrencePolicyOutcome,
    RecognitionOwnershipBuilder,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_CENTER = (Align.CENTER, Align.CENTER, Align.CENTER)


def _angled_step_part():
    return import_step(str(_FIXTURES / "issue_1247_angled_blind_step.step"))


def _passage_part():
    return Box(40, 40, 10) - extrude(RegularPolygon(6, 6), amount=12, both=True)


def _prismatic_pocket_part():
    with BuildPart() as tool:
        with BuildSketch(Plane.XY.offset(4)):
            RegularPolygon(12, 6)
        extrude(amount=20)
    return Box(120, 80, 20) - tool.part


def _oriented_slot_part():
    part = Box(120, 90, 10)
    for x in (-30, 0, 30):
        part -= Pos(x, 0, 0) * Rot(0, 0, 30) * Box(24, 6, 20, align=_CENTER)
    return part


def _repeating_profile_part():
    return import_step(str(_FIXTURES / "issue_1058_wheel_rh.step"))


@pytest.mark.parametrize(
    ("part_factory", "family", "count", "disposition", "reason_code", "tracking"),
    (
        (
            _angled_step_part,
            "angled_steps",
            1,
            "unsupported",
            "consumer_semantics_unsupported",
            "https://github.com/pzfreo/draftwright/issues/1247",
        ),
        (
            _passage_part,
            "passages",
            1,
            "unsupported",
            "consumer_semantics_unsupported",
            "https://github.com/pzfreo/draftwright/issues/1245",
        ),
        (
            _prismatic_pocket_part,
            "prismatic_pockets",
            1,
            "unsupported",
            "consumer_semantics_unsupported",
            "https://github.com/pzfreo/draftwright/issues/1246",
        ),
        (
            _oriented_slot_part,
            "oriented_slots",
            3,
            "deferred",
            "consumer_semantics_deferred",
            "https://github.com/pzfreo/draftwright/issues/1430",
        ),
        (
            _repeating_profile_part,
            "repeating_radial_profiles",
            1,
            "evidence_only",
            "geometry_only_critique",
            None,
        ),
    ),
    ids=("angled-step", "passage", "prismatic-pocket", "oriented-slots", "radial-profile"),
)
def test_settled_policy_classifies_each_exact_accepted_occurrence(
    part_factory,
    family: str,
    count: int,
    disposition: str,
    reason_code: str,
    tracking: str | None,
) -> None:
    evidence = build_recognition_evidence(part_factory())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()
    occurrences = tuple(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == family
    )

    assert len(occurrences) == count
    assert len({id(occurrence) for occurrence in occurrences}) == count
    for occurrence in occurrences:
        outcome = ownership.outcome_for(occurrence)
        assert type(outcome) is OccurrencePolicyOutcome
        assert outcome.occurrence is occurrence
        assert outcome.disposition == disposition
        assert outcome.reason_code == reason_code
        assert outcome.tracking == tracking
        assert ownership.policy_for(occurrence) is outcome
        assert ownership.binding_for(occurrence) is None
        assert ownership.status(occurrence) == disposition
        assert occurrence in ownership.expected_occurrences
        assert occurrence not in ownership.owner_expected_occurrences
        assert occurrence not in ownership.unexpectedly_missing


def test_policy_outcomes_follow_evidence_order_without_collapsing_equal_members() -> None:
    evidence = build_recognition_evidence(_oriented_slot_part())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()
    occurrences = tuple(
        occurrence
        for occurrence in evidence.features
        if evidence.family(occurrence) == "oriented_slots"
    )

    assert tuple(outcome.occurrence for outcome in ownership.policy_outcomes) == occurrences
    assert len({id(outcome) for outcome in ownership.policy_outcomes}) == 3


def test_supported_unknown_and_malformed_families_cannot_gain_ownerless_policy() -> None:
    assert ownerless_occurrence_policy("holes") is None
    assert ownerless_occurrence_policy("future_family") is None
    with pytest.raises(TypeError, match="exact str"):
        ownerless_occurrence_policy(123)  # type: ignore[arg-type]


def test_shared_ownerless_policy_table_is_immutable() -> None:
    with pytest.raises(TypeError, match="does not support item assignment"):
        UNSUPPORTED_FAMILIES["passages"] = UNSUPPORTED_FAMILIES["angled-steps"]  # type: ignore[index]


def test_an_occurrence_cannot_have_both_owner_and_ownerless_policy(monkeypatch) -> None:
    evidence = build_recognition_evidence(
        Box(30, 30, 10, align=_CENTER) - Cylinder(3, 10, align=_CENTER)
    )
    occurrence = next(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "holes"
    )
    conflict = OccurrencePolicyOutcome(
        occurrence=occurrence,
        disposition="unsupported",
        reason_code="consumer_semantics_unsupported",
        tracking="https://github.com/pzfreo/draftwright/issues/1438",
    )
    monkeypatch.setattr(ownership_module, "_policy_outcomes", lambda _evidence: (conflict,))

    with pytest.raises(RuntimeError, match="conflicting owner and policy"):
        RecognitionOwnershipBuilder(evidence)


def test_policy_outcome_uses_the_existing_capability_declaration() -> None:
    declarations = {
        family["id"]: family for family in consumer_capability_declaration()["families"]
    }
    evidence = build_recognition_evidence(_passage_part())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()
    occurrence = next(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "passages"
    )
    outcome = ownership.policy_for(occurrence)

    assert outcome is not None
    assert outcome.disposition == declarations["passages"]["disposition"]
    assert outcome.tracking == declarations["passages"]["tracking"]


def test_raw_automatic_build_attaches_policy_to_its_exact_evidence_authority() -> None:
    drawing = build_drawing(_passage_part())
    evidence = drawing.recognition_evidence()
    ownership = drawing.recognition_ownership()

    assert evidence is not None
    assert ownership is not None
    assert ownership.evidence is evidence
    occurrence = next(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "passages"
    )
    assert ownership.status(occurrence) == "unsupported"
    outcome = ownership.policy_for(occurrence)
    assert outcome is not None
    assert outcome.tracking is not None
    assert outcome.tracking.endswith("/1245")
