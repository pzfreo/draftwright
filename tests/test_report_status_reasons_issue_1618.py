"""Vector-first raw-report summaries explain every status predicate (#1609, #1618)."""

from copy import deepcopy

from draftwright.reporting import _raw_report_assessment


def _lint() -> dict:
    return {
        "passed": True,
        "score": 1.0,
        "diagnostic_score": 1.0,
        "errors": 0,
        "issues": [],
        "quality": {
            "legibility": {"available": True, "raw_issues": 0},
            "fidelity": {"available": True, "raw_issues": 0},
            "restraint": {
                "available": False,
                "reason": "physical requirement equivalence is unavailable",
            },
        },
    }


def _occurrence(index: int, *, coverage: str = "ledger") -> dict:
    return {
        "id": f"holes:{index}",
        "disposition": "represented",
        "requirements": {"coverage": coverage, "ids": []},
    }


def _requirement(index: int, *, state: str = "placed") -> dict:
    return {
        "id": f"requirement:{index}",
        "state": state,
        "reason_code": "placed",
    }


def test_one_unprojected_occurrence_is_distinct_from_total_failure() -> None:
    one = [_occurrence(index) for index in range(177)]
    one[93] = _occurrence(93, coverage="not-projected")
    all_unprojected = [_occurrence(index, coverage="not-projected") for index in range(177)]

    one_summary = _raw_report_assessment(lint=_lint(), occurrences=one, requirements=[])
    failed_summary = _raw_report_assessment(
        lint=_lint(), occurrences=all_unprojected, requirements=[]
    )

    one_reason = one_summary["status_reasons"][0]
    failed_reason = failed_summary["status_reasons"][0]
    assert one_reason == {
        "code": "occurrence_coverage_unresolved",
        "axis": "recognition",
        "affected_count": 1,
        "denominator": 177,
        "evidence_refs": ["holes:93"],
    }
    assert failed_reason["affected_count"] == failed_reason["denominator"] == 177
    assert len(failed_reason["evidence_refs"]) == 5
    assert one_summary["summary"] != failed_summary["summary"]


def test_needs_attention_has_structured_reasons_and_shared_human_text() -> None:
    result = _raw_report_assessment(
        lint=_lint(),
        occurrences=[_occurrence(1)],
        requirements=[_requirement(1, state="missing")],
    )

    assert result["status"] == "needs-attention"
    assert result["status_reasons"] == [
        {
            "code": "requirement_not_satisfied",
            "axis": "requirements",
            "affected_count": 1,
            "denominator": 1,
            "evidence_refs": ["requirement:1"],
        }
    ]
    reason = result["status_reasons"][0]
    assert result["summary"] == (
        f"needs-attention: {reason['axis']} {reason['code']} "
        f"({reason['affected_count']}/{reason['denominator']})"
    )


def test_every_status_predicate_retains_its_responsible_evidence() -> None:
    lint = _lint()
    lint.update(
        passed=False,
        errors=1,
        issues=[{"severity": "error", "code": "contradiction"}],
    )
    occurrence = _occurrence(1, coverage="deferred")
    occurrence["disposition"] = "evidence_only"
    requirement = _requirement(1, state="unverifiable")
    requirement["reason_code"] = "requirement_cardinality_unknown"

    result = _raw_report_assessment(
        lint=lint, occurrences=[occurrence], requirements=[requirement]
    )

    assert [row["code"] for row in result["status_reasons"]] == [
        "lint_failed",
        "attention_disposition",
        "requirement_not_satisfied",
        "requirement_cardinality_unknown",
        "occurrence_coverage_unresolved",
    ]
    assert [row["axis"] for row in result["status_reasons"]] == [
        "lint",
        "recognition",
        "requirements",
        "completeness",
        "recognition",
    ]
    assert all(row["evidence_refs"] for row in result["status_reasons"])


def test_legacy_numeric_scores_cannot_change_status_or_explanation() -> None:
    lint = _lint()
    occurrences = [_occurrence(1, coverage="unavailable")]
    reference = _raw_report_assessment(lint=lint, occurrences=occurrences, requirements=[])

    changed = deepcopy(lint)
    changed["score"] = -10_000
    changed["diagnostic_score"] = 10_000
    candidate = _raw_report_assessment(lint=changed, occurrences=occurrences, requirements=[])

    assert candidate == reference
    assert "score" not in candidate
    assert "diagnostic_score" not in candidate


def test_bounded_clear_has_empty_reasons_and_unavailable_axes_remain_explicit() -> None:
    result = _raw_report_assessment(
        lint=_lint(),
        occurrences=[_occurrence(1)],
        requirements=[_requirement(1)],
    )

    assert result["status"] == "bounded-clear"
    assert result["status_reasons"] == []
    assert result["axes"]["requirements"] == {
        "status": "clear-within-assessed-scope",
        "affected_count": 0,
        "denominator": 1,
        "denominator_reason": None,
        "unavailable_reasons": [],
    }
    assert result["axes"]["restraint"]["status"] == "unavailable"
    assert result["axes"]["restraint"]["unavailable_reasons"]
