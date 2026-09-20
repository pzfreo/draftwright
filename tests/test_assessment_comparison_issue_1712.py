"""Replay comparison is a deterministic evidence vector, never a composite score (#1712)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema.validators import validator_for

from draftwright.audit import (
    ExpectedRequirement,
    IntentionalChange,
    LayoutFindingIdentity,
    compare_assessments,
)

_SCHEMA = (
    Path(__file__).parents[1] / "docs/reference/draftwright-assessment-comparison-v1.schema.json"
)


def _quality():
    completeness = {
        "available": True,
        "audited_score": 1.0,
        "coverage": "partial",
        "unknown_cardinality_rows": 0,
        "placed": 1,
        "satisfied_by_structured_note": 0,
        "suppressed": 0,
        "dropped": 0,
        "missing": 0,
        "unverifiable": 0,
        "inapplicable": 0,
        "unsupported": 0,
    }
    issue_axis = {"available": True, "score": 1.0, "by_code": {}}
    return {
        "completeness": completeness,
        "restraint": {"available": False, "score": None, "reason": "not assessed"},
        "legibility": issue_axis,
        "fidelity": deepcopy(issue_axis),
        "unscored": {
            "available": True,
            "score": None,
            "by_code": {},
            "unclassified": [],
        },
    }


def _assessment(*, crossing=True, claim=True, occurrence="occurrence:1", value=6.0):
    declaration = {
        "id": "declaration:hole",
        "owner": {"id": "hole:1", "kind": "hole"},
        "feature_kind": "hole",
        "parameters": ["bore.diameter"],
        "recognition": {"occurrence_ids": [] if occurrence is None else [occurrence]},
        "representations": (
            [
                {
                    "name": "hole_callout",
                    "type": "Leader",
                    "view": "plan",
                    "pinned": False,
                    "measurements": ["bore.diameter"],
                    "satisfactions": [],
                }
            ]
            if claim
            else []
        ),
    }
    finding = {
        "lint_issue_index": 0,
        "code": "annotation_ink_overlap",
        "annotation_names": ["hole_callout", "dim_width"],
        "declaration_ids": ["declaration:hole"],
        "owner_ids": ["hole:1"],
        "remedies": ["view", "side"],
    }
    issue = {
        "severity": "warning",
        "code": "annotation_ink_overlap",
        "message": "two annotations overlap",
        "location": None,
    }
    meaning = {
        "value": value,
        "tolerance": None,
        "span": None,
        "axis": "diameter",
        "discriminator": None,
        "location_member": None,
        "angular_reference": None,
    }
    return {
        "schema": "draftwright-replay-assessment",
        "schema_version": 2,
        "scope": "generated-script-replay",
        "producer": {"draftwright": "0.4.0", "quiddity": "0.3.1"},
        "source": {"kind": "step", "name": "same.step", "sha256": "a" * 64},
        "script": {"name": "same.py", "sha256": "b" * 64},
        "run": {"pmi_mode": "off", "formats": ["svg"], "reproducible": True},
        "measurements": {
            "authority": "confirmed-compiled-claims",
            "identity_scope": "build-local-declarations",
            "entries": (
                [
                    {
                        "declaration_id": "declaration:hole",
                        "owner_id": "hole:1",
                        "parameter_id": "bore.diameter",
                        "annotation": "hole_callout",
                        "cell": None,
                        "meaning": [meaning],
                        "rendered": ["⌀6 THRU", [], None],
                        "witnesses": [None, []],
                        "verification": "confirmed-within-measurement-verifier-scope",
                    }
                ]
                if claim
                else []
            ),
            "unknown": [],
            "unavailable_owner_claims": [],
        },
        "drawing": {
            "schema": "draftwright-report",
            "schema_version": 8,
            "scope": "declared-sheet",
            "declarations": {"entries": [declaration]},
            "layout": {"findings": [finding] if crossing else []},
            "lint": {
                "issues": [issue] if crossing else [],
                "quality": _quality(),
            },
        },
    }


_EXPECTED = (ExpectedRequirement("declaration:hole", "bore.diameter"),)
_CROSSING = LayoutFindingIdentity(
    "annotation_ink_overlap",
    ("declaration:hole",),
    ("hole_callout", "dim_width"),
)


def test_resolving_selected_crossing_with_expected_measurement_is_preferred() -> None:
    result = compare_assessments(
        _assessment(),
        _assessment(crossing=False),
        expected_requirements=_EXPECTED,
        selected_layout_finding=_CROSSING,
    )

    assert result["decision"] == "preferred", result
    assert result["layout"]["selected_transition"] == "resolved"
    assert not result["requirements"]["blockers"]
    assert result["restraint"]["availability"] == "unavailable"
    assert result["manufacturing_readiness"]["availability"] == "unavailable"


def test_unchanged_explicit_declaration_identity_can_carry_nonrecognition_owner() -> None:
    result = compare_assessments(
        _assessment(occurrence=None),
        _assessment(crossing=False, occurrence=None),
        expected_requirements=_EXPECTED,
        selected_layout_finding=_CROSSING,
    )

    assert result["decision"] == "preferred", result
    transition = result["requirements"]["transitions"][0]
    assert transition["baseline"]["identity"] == "declaration-only"
    assert transition["candidate"]["identity"] == "declaration-only"


def test_deleting_crossed_requirement_is_rejected_even_if_layout_clears() -> None:
    result = compare_assessments(
        _assessment(),
        _assessment(crossing=False, claim=False),
        expected_requirements=_EXPECTED,
        selected_layout_finding=_CROSSING,
    )

    assert result["decision"] == "rejected"
    assert result["layout"]["selected_transition"] == "resolved"
    assert any(
        row["code"] == "requirement_regression" for row in result["requirements"]["blockers"]
    )


def test_fixed_denominator_exposes_a_shared_omission() -> None:
    result = compare_assessments(
        _assessment(crossing=False, claim=False),
        _assessment(crossing=False, claim=False),
        expected_requirements=_EXPECTED,
    )

    assert result["decision"] == "rejected"
    transition = result["requirements"]["transitions"][0]
    assert transition["baseline"]["state"] == transition["candidate"]["state"] == "unrepresented"


def test_restoring_a_fixed_requirement_is_an_improvement_not_a_substitution() -> None:
    result = compare_assessments(
        _assessment(claim=False),
        _assessment(crossing=False),
        expected_requirements=_EXPECTED,
        selected_layout_finding=_CROSSING,
    )

    assert result["decision"] == "preferred", result
    assert [row["code"] for row in result["requirements"]["improvements"]] == [
        "requirement_resolved"
    ]
    assert {row["code"] for row in result["policy"]["improvements"]} == {
        "requirement_resolved",
        "selected_layout_finding_resolved",
    }


def test_equal_value_same_kind_physical_owner_substitution_is_rejected() -> None:
    result = compare_assessments(
        _assessment(),
        _assessment(occurrence="occurrence:2"),
        expected_requirements=_EXPECTED,
    )

    assert result["decision"] == "rejected"
    assert "physical_owner" in result["requirements"]["transitions"][0]["changes"]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("value", 6.5),
        ("tolerance", {"lower": -0.1, "upper": 0.1}),
        ("axis", "x"),
        ("location_member", 1),
    ],
)
def test_compiled_meaning_substitutions_are_rejected(field, replacement) -> None:
    baseline, candidate = _assessment(), _assessment()
    candidate["measurements"]["entries"][0]["meaning"][0][field] = replacement

    result = compare_assessments(baseline, candidate, expected_requirements=_EXPECTED)

    assert result["decision"] == "rejected"
    assert "engineering_meaning" in result["requirements"]["transitions"][0]["changes"]


def test_representation_substitution_is_reported_without_losing_meaning() -> None:
    baseline, candidate = _assessment(), _assessment(crossing=False)
    candidate["drawing"]["declarations"]["entries"][0]["representations"][0]["type"] = "Dimension"

    result = compare_assessments(
        baseline,
        candidate,
        expected_requirements=_EXPECTED,
        selected_layout_finding=_CROSSING,
    )

    assert result["decision"] == "preferred", result
    assert result["requirements"]["transitions"][0]["changes"] == ["representation"]


def test_pin_state_change_requires_explicit_authorisation() -> None:
    baseline, candidate = _assessment(), _assessment()
    candidate["drawing"]["declarations"]["entries"][0]["representations"][0]["pinned"] = True

    rejected = compare_assessments(baseline, candidate, expected_requirements=_EXPECTED)
    accepted = compare_assessments(
        baseline,
        candidate,
        expected_requirements=_EXPECTED,
        intentional_changes=(
            IntentionalChange("declaration:hole", "bore.diameter", "pin layout target"),
        ),
    )

    assert rejected["decision"] == "rejected"
    assert rejected["requirements"]["transitions"][0]["changes"] == ["pin_state"]
    assert accepted["intentional_changes"][0]["changes"] == ["pin_state"]


@pytest.mark.parametrize(
    ("role", "state"),
    [("measurements", "unverifiable"), ("satisfactions", "satisfied")],
)
def test_unconfirmed_and_structurally_satisfied_carriers_stay_distinct(role, state) -> None:
    candidate = _assessment(crossing=False, claim=False)
    candidate["drawing"]["declarations"]["entries"][0]["representations"] = [
        {
            "name": "replacement",
            "type": "Note" if role == "satisfactions" else "Leader",
            "view": "plan",
            "pinned": False,
            "measurements": ["bore.diameter"] if role == "measurements" else [],
            "satisfactions": ["bore.diameter"] if role == "satisfactions" else [],
        }
    ]

    result = compare_assessments(_assessment(), candidate, expected_requirements=_EXPECTED)

    assert result["requirements"]["transitions"][0]["candidate"]["state"] == state
    assert result["decision"] == ("no-preference" if state == "satisfied" else "rejected")


def test_authorised_meaning_change_is_separate_from_incidental_regressions() -> None:
    result = compare_assessments(
        _assessment(),
        _assessment(crossing=False, value=6.5),
        expected_requirements=_EXPECTED,
        intentional_changes=(
            IntentionalChange("declaration:hole", "bore.diameter", "design revision"),
        ),
        selected_layout_finding=_CROSSING,
    )

    assert result["decision"] == "preferred", result
    assert result["intentional_changes"][0]["reason"] == "design revision"
    assert "engineering_meaning" in result["intentional_changes"][0]["changes"]
    assert not result["requirements"]["blockers"]


def test_new_adverse_completeness_fidelity_and_unclassified_evidence_rejects() -> None:
    baseline, candidate = _assessment(), _assessment(crossing=False)
    quality = candidate["drawing"]["lint"]["quality"]
    quality["completeness"]["missing"] = 1
    quality["fidelity"]["by_code"] = {"label_vs_measured": 1}
    quality["fidelity"]["score"] = 0.9
    quality["unscored"]["unclassified"] = ["future_code"]

    result = compare_assessments(
        baseline,
        candidate,
        expected_requirements=_EXPECTED,
        selected_layout_finding=_CROSSING,
    )

    assert result["decision"] == "rejected"
    codes = {row["code"] for row in result["policy"]["blockers"]}
    assert {
        "adverse_completeness_outcome_introduced",
        "fidelity_regression",
        "unclassified_lint_introduced",
    } <= codes
    assert result["fidelity"]["target_validation"]["introduced"] == [
        {"code": "label_vs_measured", "baseline": 0, "candidate": 1}
    ]


def test_other_fail_closed_evidence_is_reported_by_its_own_policy_reason() -> None:
    baseline, candidate = _assessment(crossing=False), _assessment()
    candidate["drawing"]["lint"]["issues"].append(
        {"severity": "error", "code": "new_error", "message": "bad", "location": None}
    )
    completeness = candidate["drawing"]["lint"]["quality"]["completeness"]
    completeness.update(
        available=False,
        unknown_cardinality_rows=1,
        unscored_recognized_families=["new_family"],
        unrecognised_geometry_reports=1,
    )
    candidate["drawing"]["lint"]["quality"]["fidelity"]["available"] = False
    candidate["measurements"]["unknown"] = [
        {"annotation": "mystery", "reason": "compiled_claim_unconfirmed"}
    ]

    result = compare_assessments(
        baseline,
        candidate,
        expected_requirements=_EXPECTED,
        selected_layout_finding={
            "code": "annotation_ink_overlap",
            "declaration_ids": ["declaration:hole"],
            "annotation_names": ["dim_width", "hole_callout"],
        },
    )

    assert result["decision"] == "rejected"
    codes = {row["code"] for row in result["policy"]["blockers"]}
    assert {
        "error_lint_introduced",
        "recognized_family_became_unscored",
        "unrecognised_geometry_reports_increased",
        "layout_finding_introduced",
        "unverifiable_measurement_claim",
    } <= codes
    assert "candidate completeness is unavailable" in result["unavailable"]["reasons"]
    assert "candidate fidelity is unavailable" in result["unavailable"]["reasons"]


def test_a_better_score_from_a_smaller_recognized_denominator_is_rejected() -> None:
    baseline, candidate = _assessment(), _assessment(crossing=False)
    before = baseline["drawing"]["lint"]["quality"]["completeness"]
    after = candidate["drawing"]["lint"]["quality"]["completeness"]
    before.update(known_requirement_count=5, audited_score=0.8, missing=1)
    after.update(known_requirement_count=4, audited_score=1.0, missing=0)

    result = compare_assessments(
        baseline,
        candidate,
        expected_requirements=_EXPECTED,
        selected_layout_finding=_CROSSING,
    )

    assert result["decision"] == "rejected"
    assert any(
        row["code"] == "recognized_requirement_denominator_shrank"
        for row in result["policy"]["blockers"]
    )


def test_lint_delta_preserves_duplicate_finding_cardinality() -> None:
    baseline, candidate = _assessment(crossing=False), _assessment(crossing=False)
    issue = {
        "severity": "info",
        "code": "same_finding",
        "message": "same evidence",
        "location": None,
    }
    baseline["drawing"]["lint"]["issues"] = [issue, deepcopy(issue)]
    candidate["drawing"]["lint"]["issues"] = [deepcopy(issue)]

    result = compare_assessments(baseline, candidate, expected_requirements=_EXPECTED)

    assert result["lint"] == {
        "resolved": [issue],
        "introduced": [],
        "unchanged": [issue],
    }


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda row: row["source"].update(sha256="c" * 64), "source_hash_mismatch"),
        (lambda row: row["run"].update(pmi_mode="annotate"), "run_options_mismatch"),
        (lambda row: row.update(schema_version=1), "candidate_assessment_version_unsupported"),
        (
            lambda row: row["producer"].update(quiddity="0.3.2"),
            "producer_versions_mismatch",
        ),
        (lambda row: row.update(schema="other"), "candidate_assessment_schema_unsupported"),
        (
            lambda row: row["drawing"].update(schema_version=7),
            "candidate_drawing_report_incompatible",
        ),
        (
            lambda row: row["measurements"].update(authority="other"),
            "candidate_measurement_authority_incompatible",
        ),
        (lambda row: row.update(source=None), "source_identity_missing"),
        (lambda row: row["source"].update(kind="build123d"), "immutable_step_source_required"),
        (lambda row: row["source"].update(sha256=None), "source_hash_missing"),
    ],
)
def test_incompatible_authority_is_refused(change, reason) -> None:
    baseline, candidate = _assessment(), _assessment()
    change(candidate)

    result = compare_assessments(baseline, candidate, expected_requirements=_EXPECTED)

    assert result["decision"] == "incomparable"
    assert reason in result["compatibility"]["reasons"]
    assert result["requirements"] is None


def test_mapping_and_tuple_requirements_validate_at_the_public_boundary() -> None:
    result = compare_assessments(
        _assessment(),
        _assessment(),
        expected_requirements=(
            {"declaration_id": "declaration:hole", "parameter_id": "bore.diameter"},
            ("declaration:hole", "bore.diameter"),
        ),
    )
    assert len(result["requirements"]["expected"]) == 1

    with pytest.raises(TypeError, match="two mappings"):
        compare_assessments(None, _assessment())  # type: ignore[arg-type]

    for invalid in (object(), {"declaration_id": "", "parameter_id": "x"}, ("x", "")):
        with pytest.raises((TypeError, ValueError)):
            compare_assessments(
                _assessment(),
                _assessment(),
                expected_requirements=(invalid,),
            )

    with pytest.raises(ValueError, match="reason"):
        compare_assessments(
            _assessment(),
            _assessment(),
            intentional_changes=(
                {"declaration_id": "declaration:hole", "parameter_id": "bore.diameter"},
            ),
        )
    with pytest.raises(ValueError, match="duplicate"):
        compare_assessments(
            _assessment(),
            _assessment(),
            intentional_changes=(
                IntentionalChange("declaration:hole", "bore.diameter", "first"),
                IntentionalChange("declaration:hole", "bore.diameter", "second"),
            ),
        )

    duplicate = _assessment()
    duplicate["drawing"]["declarations"]["entries"].append(
        deepcopy(duplicate["drawing"]["declarations"]["entries"][0])
    )
    with pytest.raises(ValueError, match="duplicate declaration"):
        compare_assessments(duplicate, _assessment())

    for selection in (object(), {"code": ""}, {"code": "x", "declaration_ids": "bad"}):
        with pytest.raises((TypeError, ValueError)):
            compare_assessments(
                _assessment(),
                _assessment(),
                selected_layout_finding=selection,  # type: ignore[arg-type]
            )


def test_output_is_deterministic_for_identical_inputs() -> None:
    baseline, candidate = _assessment(), _assessment(crossing=False)
    first = compare_assessments(
        baseline,
        candidate,
        expected_requirements=_EXPECTED,
        selected_layout_finding=_CROSSING,
    )
    second = compare_assessments(
        deepcopy(baseline),
        deepcopy(candidate),
        expected_requirements=_EXPECTED,
        selected_layout_finding=_CROSSING,
    )
    assert first == second

    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    validator = validator_for(schema)
    validator.check_schema(schema)
    validator(schema).validate(first)
