"""Pure ownerless recognizer-policy contract checks."""

import pytest
from quiddity import capability_manifest

from draftwright.recogniser_contract import consumer_capability_declaration
from draftwright.recogniser_policy import (
    EVIDENCE_ONLY_FAMILIES,
    UNSUPPORTED_FAMILIES,
    ownerless_occurrence_policy,
)


def test_supported_unknown_and_malformed_families_cannot_gain_ownerless_policy() -> None:
    assert ownerless_occurrence_policy("holes") is None
    assert ownerless_occurrence_policy("future_family") is None
    with pytest.raises(TypeError, match="exact str"):
        ownerless_occurrence_policy(123)  # type: ignore[arg-type]


def test_shared_ownerless_policy_table_is_immutable() -> None:
    with pytest.raises(TypeError, match="does not support item assignment"):
        UNSUPPORTED_FAMILIES["section_recesses"] = UNSUPPORTED_FAMILIES["angled-steps"]  # type: ignore[index]
    with pytest.raises(TypeError, match="does not support item assignment"):
        EVIDENCE_ONLY_FAMILIES["risers"] = EVIDENCE_ONLY_FAMILIES["step-levels"]  # type: ignore[index]


def test_evidence_occurrence_policy_does_not_downgrade_supported_aggregate_families() -> None:
    declarations = {
        family["id"]: family for family in consumer_capability_declaration()["families"]
    }

    assert declarations["face-levels"]["disposition"] == "supported"
    assert declarations["risers"]["disposition"] == "supported"
    step_policy = ownerless_occurrence_policy("step_levels")
    riser_policy = ownerless_occurrence_policy("risers")
    assert step_policy is not None and step_policy.disposition == "evidence_only"
    assert riser_policy is not None and riser_policy.disposition == "evidence_only"


def test_projection_evidence_policy_matches_the_released_provider_roles() -> None:
    manifest = {family["id"]: family for family in capability_manifest()["families"]}

    assert manifest["face-levels"]["census_output"] is None
    assert manifest["risers"]["census_output"] is None
    assert {record["role"] for record in manifest["face-levels"]["records"]} == {"evidence"}
    assert {record["name"]: record["role"] for record in manifest["risers"]["records"]} == {
        "RiserEvidence": "evidence",
        "StepShoulder": "projection",
    }
