"""Typed remediation stays evidence-bound and never invents an edit (#1618, #1759)."""

from copy import deepcopy

from draftwright.linting.quality import (
    _STAGE_ROUTED_CODES,
    _UNSCORED_CODE_PREFIXES,
    _UNSCORED_CODES,
)
from draftwright.reporting import _lint_remediation_domain, _raw_report_assessment


def _quality() -> dict:
    return {
        "legibility": {"available": True, "raw_issues": 1},
        "fidelity": {"available": True, "raw_issues": 0},
        "completeness": {
            "available": True,
            "known_requirement_count": 1,
            "suppressed": 0,
            "dropped": 0,
            "missing": 0,
            "unverifiable": 1,
            "unsupported": 0,
        },
        "restraint": {"available": False, "reason": "provenance incomplete"},
    }


def _lint(issues: list[dict]) -> dict:
    errors = sum(row["severity"] == "error" for row in issues)
    return {
        "passed": not errors,
        "score": 0.01,
        "diagnostic_score": 0.01,
        "errors": errors,
        "issues": issues,
        "quality": _quality(),
    }


def _assessment(issues: list[dict], *, requirements: list[dict] | None = None) -> dict:
    return _raw_report_assessment(
        lint=_lint(issues), occurrences=[], requirements=requirements or []
    )


def _by_domain(result: dict) -> dict[str, dict]:
    return {row["domain"]: row for row in result["remediation"]["items"]}


def test_ctc_shape_separates_layout_importer_and_provenance_work() -> None:
    result = _assessment(
        [
            {
                "severity": "warning",
                "code": "annotation_ink_overlap",
                "annotation_name": "dim-width",
                "related_annotation_names": ["hole-callout"],
            },
            {
                "severity": "error",
                "code": "pmi_not_lowered",
                "source_ids": ["dimension:0:1:4:17"],
            },
            {
                "severity": "warning",
                "code": "plate_requirement_unverifiable",
            },
        ],
        requirements=[
            {
                "id": "requirement:54",
                "family": "plates",
                "parameter_id": "plate_thickness.length",
                "state": "unverifiable",
                "reason_code": "measurement_provenance_unavailable",
                "owner_ids": ["plate:1"],
            }
        ],
    )

    domains = _by_domain(result)
    assert list(domains) == ["importer-lowering", "layout", "evidence-unavailable"]
    assert domains["layout"]["finding_ids"] == ["lint:0"]
    assert domains["layout"]["annotation_names"] == ["dim-width", "hole-callout"]
    assert domains["layout"]["supported_actions"] == [
        {"verb": "Drawing.repair", "arguments": {}, "applies_to": ["lint:0"]}
    ]
    assert domains["layout"]["unaddressed_evidence_refs"] == []
    assert domains["layout"]["no_supported_action_reason"] is None
    assert domains["importer-lowering"]["finding_ids"] == ["lint:1"]
    assert domains["importer-lowering"]["source_ids"] == ["dimension:0:1:4:17"]
    assert domains["importer-lowering"]["supported_actions"] == []
    assert domains["importer-lowering"]["no_supported_action_reason"]
    assert domains["evidence-unavailable"]["finding_ids"] == ["lint:2"]
    assert domains["evidence-unavailable"]["requirement_ids"] == ["requirement:54"]
    assert domains["evidence-unavailable"]["owner_ids"] == ["plate:1"]
    assert domains["evidence-unavailable"]["subjects"] == [
        {
            "evidence_ref": "requirement:54",
            "kind": "requirement",
            "family": "plates",
            "parameter_id": "plate_thickness.length",
            "reason_code": "measurement_provenance_unavailable",
        }
    ]


def test_ordering_is_explicit_and_independent_of_legacy_scores() -> None:
    issues = [
        {"severity": "warning", "code": "annotation_ink_overlap"},
        {"severity": "error", "code": "pmi_not_lowered"},
        {"severity": "error", "code": "label_vs_measured"},
    ]
    lint = _lint(issues)
    reference = _raw_report_assessment(lint=lint, occurrences=[], requirements=[])

    changed = deepcopy(lint)
    changed["score"] = 10_000
    changed["diagnostic_score"] = -10_000
    candidate = _raw_report_assessment(lint=changed, occurrences=[], requirements=[])

    assert candidate["remediation"] == reference["remediation"]
    assert reference["remediation"]["ordering"] == (
        "severity(error,warning,info)-then-domain-then-evidence-id"
    )
    assert [row["domain"] for row in reference["remediation"]["items"]] == [
        "contradiction",
        "importer-lowering",
        "layout",
    ]


def test_unknown_code_remains_visible_unclassified_and_has_no_guessed_remedy() -> None:
    lint = _lint(
        [
            {
                "severity": "warning",
                "code": "future_unclassified_finding",
                "declaration_ids": ["declaration:7"],
            },
            {"severity": "warning", "code": "future_requirement_missing"},
        ]
    )
    lint["quality"]["unscored"] = {
        "unclassified": ["future_unclassified_finding", "future_requirement_missing"]
    }
    result = _raw_report_assessment(lint=lint, occurrences=[], requirements=[])

    (item,) = result["remediation"]["items"]
    assert item["domain"] == "unclassified"
    assert item["finding_ids"] == ["lint:0", "lint:1"]
    assert item["declaration_ids"] == ["declaration:7"]
    assert item["supported_actions"] == []
    assert item["unaddressed_evidence_refs"] == ["lint:0", "lint:1"]
    assert item["no_supported_action_reason"] == (
        "no bounded public remedy is classified for this evidence"
    )
    assert result["remediation"]["unclassified_finding_ids"] == ["lint:0", "lint:1"]


def test_every_item_names_preservation_invariants_without_page_coordinates() -> None:
    result = _assessment([{"severity": "warning", "code": "annotation_ink_overlap"}])

    (item,) = result["remediation"]["items"]
    assert item["preserve"] == [
        "measurement-owner",
        "parameter-meaning",
        "source-provenance",
        "unrelated-requirement-outcomes",
    ]
    assert "coordinate" not in repr(result).lower()


def test_every_registered_non_axis_finding_has_a_bounded_domain() -> None:
    assert all(
        _lint_remediation_domain(
            {"code": code, "outcome_stage": None}, unclassified_codes=frozenset()
        )
        != "unclassified"
        for code in _UNSCORED_CODES
    )
    assert all(
        _lint_remediation_domain(
            {"code": code, "outcome_stage": "placement"},
            unclassified_codes=frozenset(),
        )
        == "layout"
        for code in _STAGE_ROUTED_CODES
    )
    for prefix in _UNSCORED_CODE_PREFIXES:
        assert (
            _lint_remediation_domain({"code": f"{prefix}missing"}, unclassified_codes=frozenset())
            == "missing-carrier"
        )
        assert (
            _lint_remediation_domain(
                {"code": f"{prefix}suppressed"}, unclassified_codes=frozenset()
            )
            == "missing-carrier"
        )
        assert (
            _lint_remediation_domain(
                {"code": f"{prefix}unverifiable"}, unclassified_codes=frozenset()
            )
            == "evidence-unavailable"
        )
        assert (
            _lint_remediation_domain(
                {"code": f"{prefix}unsupported"}, unclassified_codes=frozenset()
            )
            == "importer-lowering"
        )
