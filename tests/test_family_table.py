"""The first canonical family consumer preserves its existing ownership vocabulary."""

from quiddity import RecognitionResult, capability_manifest

from draftwright.family_table import FAMILIES, ownership_families
from draftwright.linting.quality import _AUDITED_FAMILIES, _RECOGNISED_REQUIREMENT_FAMILIES
from draftwright.linting.requirements import REQUIREMENT_SOURCE_FAMILIES
from draftwright.recogniser_policy import ownerless_occurrence_policy
from draftwright.recognition_ownership import (
    CONDITIONAL_FAMILIES,
    DIRECT_FAMILIES,
    GROUPABLE_FAMILIES,
    NESTED_FAMILIES,
)


def test_family_table_names_join_the_released_provider_and_current_consumers() -> None:
    ids = [row.id for row in FAMILIES]
    provider_ids = [row.provider_id for row in FAMILIES if row.provider_id is not None]
    evidence_families = [
        row.evidence_family for row in FAMILIES if row.evidence_family is not None
    ]
    assert len(ids) == len(set(ids))
    assert len(provider_ids) == len(set(provider_ids))
    assert len(evidence_families) == len(set(evidence_families))
    assert set(provider_ids) == {family["id"] for family in capability_manifest()["families"]}
    assert all(
        row.result_attr in RecognitionResult.__annotations__
        for row in FAMILIES
        if row.provider_id is not None
    )

    provider_exceptions = {"pads": "rectangular-pads", "step-levels": "face-levels"}
    aggregate_only = {
        "gusset-rib-patterns",
        "oriented-slot-patterns",
        "slot-patterns",
        "section-recess-patterns",
    }
    for row in FAMILIES:
        if row.provider_id is not None:
            assert row.provider_id == provider_exceptions.get(row.id, row.id)
        if row.result_attr is not None:
            assert row.result_attr == row.id.replace("-", "_")
        assert row.evidence_family == (None if row.id in aggregate_only else row.result_attr)

    by_id = {row.id: row for row in FAMILIES}
    assert (by_id["pads"].provider_id, by_id["pads"].evidence_family) == (
        "rectangular-pads",
        "pads",
    )
    assert (by_id["step-levels"].provider_id, by_id["step-levels"].result_attr) == (
        "face-levels",
        "step_levels",
    )
    assert by_id["section-recess-patterns"].quality_key == "pocket_patterns"
    assert {
        (row.id, row.result_attr)
        for row in FAMILIES
        if row.result_attr is not None and row.evidence_family is None
    } == {
        ("gusset-rib-patterns", "gusset_rib_patterns"),
        ("oriented-slot-patterns", "oriented_slot_patterns"),
        ("slot-patterns", "slot_patterns"),
        ("section-recess-patterns", "section_recess_patterns"),
    }


def test_family_table_metadata_matches_the_existing_requirement_views() -> None:
    assert {
        row.requirement_key or row.result_attr for row in FAMILIES if row.requirement_source
    } == REQUIREMENT_SOURCE_FAMILIES
    assert {row.requirement_key or row.result_attr for row in FAMILIES if row.lint_audited} == set(
        _AUDITED_FAMILIES
    )
    assert {
        row.result_attr: row.quality_key
        for row in FAMILIES
        if row.quality_key is not None and row.result_attr is not None
    } == _RECOGNISED_REQUIREMENT_FAMILIES


def test_ownership_categories_are_derived_and_keep_their_established_members() -> None:
    assert (
        DIRECT_FAMILIES
        == ownership_families("direct")
        == {
            "blends",
            "chamfers",
            "circular_blind_steps",
            "double_d_bores",
            "fillets",
            "flats",
            "grooves",
            "oriented_slots",
            "pads",
            "paired_ramp_steps",
            "polygonal_bosses",
            "polygonal_stock",
        }
    )
    assert (
        GROUPABLE_FAMILIES
        == ownership_families("groupable")
        == {
            "gusset_ribs",
            "holes",
            "section_recesses",
            "slots",
        }
    )
    assert NESTED_FAMILIES == ownership_families("nested") == {"countersinks"}
    assert (
        CONDITIONAL_FAMILIES
        == ownership_families("conditional")
        == {
            "bosses",
            "hole_patterns",
            "plates",
            "through_steps",
            "turned_steps",
        }
    )
    for row in FAMILIES:
        if row.ownership_class != "ownerless":
            continue
        policy_name = row.evidence_family or row.provider_id
        assert policy_name is not None
        policy = ownerless_occurrence_policy(policy_name)
        assert policy is not None
        assert row.policy_status == policy.disposition
