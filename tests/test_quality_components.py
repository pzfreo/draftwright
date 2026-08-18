"""Fail-closed guards over the quality components' classifications (#1127).

Legibility decides what counts as a lost annotation, and completeness decides which
recognition inventories it can admit to not scoring. Both decisions used to depend on a
literal collection being kept up to date, which fails open: a code or an inventory added
elsewhere joins by omission, and the component keeps reporting a clean sheet. These tests
pin the behaviour that replaced the drop-code list, and ratchet the one classification that
is still a literal map.
"""

from __future__ import annotations

from b123d_recognisers import RecognitionResult

from draftwright.linting.issues import LintIssue
from draftwright.linting.quality import (
    _NON_REQUIREMENT_INVENTORIES,
    _RECOGNISED_REQUIREMENT_FAMILIES,
    quality_components,
)


def _legibility(*issues):
    return quality_components(
        recognition=None,
        features=(),
        registry=None,
        omissions=(),
        issues=issues,
        error_penalty=0.15,
        warning_penalty=0.05,
        # Irrelevant to the component under test; `quality_components` requires it rather
        # than defaulting to True, because a fail-open default that only tests supply is a
        # default that only tests are protected by (#1176 review r3).
        has_asserted_content=True,
    )["legibility"]


def _completeness(*issues):
    return quality_components(
        recognition=None,
        features=(),
        registry=None,
        omissions=(),
        issues=issues,
        error_penalty=0.15,
        warning_penalty=0.05,
        # Irrelevant to the component under test; `quality_components` requires it rather
        # than defaulting to True, because a fail-open default that only tests supply is a
        # default that only tests are protected by (#1176 review r3).
        has_asserted_content=True,
    )["completeness"]


def test_the_audited_score_carries_its_qualifier_in_its_own_name():
    """Only the key naming is checked here — sibling metadata is dropped when a number is
    quoted, a field name is not.

    That a perfect score really is reachable beside an admitted blind spot is a claim about
    a built drawing, not about this dict, and is established on a real part by
    ``test_audited_recognized_requirements_remain_scorable_beside_unscored_families`` in
    ``tests/test_slot_completeness.py``.
    """
    completeness = _completeness()

    assert "score" not in completeness
    assert "audited_score" in completeness


def test_the_denominator_states_what_it_cannot_see():
    completeness = _completeness()

    assert completeness["excludes"] == [
        "physical geometry that recognition did not identify",
        "recognized families without a semantic outcome ledger",
    ]


def test_a_recognition_gap_the_linter_did_notice_is_reported_beside_the_score():
    """A floor on the blind spot, not a measure of it — but absent geometry gets a number
    whenever lint could name it, instead of leaving the score to imply there is none."""
    completeness = _completeness(
        LintIssue(
            severity="warning",
            code="unrecognised_defining_geometry",
            message="dimension-relevant source geometry is absent from recognised IR",
        )
    )

    assert completeness["unrecognised_geometry_reports"] == 1
    assert _completeness()["unrecognised_geometry_reports"] == 0


def test_a_drop_code_nobody_registered_still_counts_against_legibility():
    """The fail-closed half: no list to update, so a new drop cannot score as legible."""
    legibility = _legibility(
        LintIssue(severity="info", code="teleporter_dropped", message="a brand new drop")
    )

    assert legibility["placement_drops"] == 1
    assert legibility["score"] < 1.0
    assert legibility["by_code"] == {"teleporter_dropped": 1}


def test_an_explicit_validation_stage_keeps_a_drop_out_of_legibility():
    """The other half: a producer can still say "this was malformed input, not a layout loss"."""
    legibility = _legibility(
        LintIssue(
            severity="warning",
            code="gdt_dropped",
            message="bad target",
            outcome_stage="validation",
        )
    )

    assert legibility["placement_drops"] == 0
    assert legibility["by_code"] == {}
    assert legibility["score"] == 1.0


def test_an_info_severity_readability_fault_lowers_the_score():
    """A leader through the outline is a legibility defect at info severity, not a free pass."""
    legibility = _legibility(
        LintIssue(
            severity="info",
            code="leader_crosses_silhouette",
            message="leader shaft crosses the view silhouette",
        )
    )

    assert legibility["infos"] == 1
    assert legibility["placement_drops"] == 0, "precondition: this is not a drop"
    assert legibility["score"] < 1.0, (
        "legibility cannot read 1.0 while itemising output it calls unreadable"
    )


def test_semantic_issues_stay_out_of_the_legibility_component():
    legibility = _legibility(
        LintIssue(severity="warning", code="feature_not_dimensioned", message="completeness")
    )

    assert legibility["by_code"] == {}
    assert legibility["score"] == 1.0


def test_every_recognition_inventory_is_either_a_requirement_family_or_excluded():
    inventories = set(RecognitionResult.__dataclass_fields__)
    classified = set(_RECOGNISED_REQUIREMENT_FAMILIES) | _NON_REQUIREMENT_INVENTORIES
    unclassified = sorted(inventories - classified)

    assert unclassified == [], (
        "completeness reports recognised-but-unscored families from a literal map, so these "
        f"inventories would be a blind spot it never admits to: {unclassified}"
    )
    assert not _NON_REQUIREMENT_INVENTORIES - inventories, (
        "excluded inventories that no longer exist make the exclusion rationale unreadable"
    )


def test_an_unavailable_completeness_component_is_data_not_a_zero_score():
    components = quality_components(
        recognition=None,
        features=(),
        registry=None,
        omissions=(),
        issues=(),
        error_penalty=0.15,
        warning_penalty=0.05,
        # Irrelevant to the component under test; `quality_components` requires it rather
        # than defaulting to True, because a fail-open default that only tests supply is a
        # default that only tests are protected by (#1176 review r3).
        has_asserted_content=True,
    )
    completeness = components["completeness"]

    assert completeness["available"] is False
    assert completeness["audited_score"] is None, (
        "a missing inventory must not read as zero coverage"
    )
    assert completeness["coverage"] == "unavailable"
    assert completeness["reason"] == "physical recognition inventory unavailable"
    assert completeness["requirements"] == 0
