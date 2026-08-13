"""Regression coverage for pairwise legibility score aggregation (#1147)."""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest
from build123d import Box

from draftwright import build_drawing
from draftwright.linting.issues import LintIssue
from draftwright.linting.quality import quality_components
from draftwright.linting.structural import lint_drawing


def _legibility(issues):
    return quality_components(
        recognition=None,
        features=(),
        registry=None,
        omissions=(),
        issues=issues,
        error_penalty=0.15,
        warning_penalty=0.05,
    )["legibility"]


def _two_labels_crossed_by_five_centre_marks():
    labels = [
        SimpleNamespace(label="4× ⌀6 THRU", label_bbox=(0.0, 0.0, 10.0, 10.0)) for _ in range(2)
    ]
    centre_marks = [
        SimpleNamespace(
            is_centerline=True,
            segments=(((float(x), -2.0), (float(x), 12.0)),),
        )
        for x in range(1, 6)
    ]
    return [
        issue
        for issue in lint_drawing([*labels, *centre_marks])
        if issue.code == "label_centerline_overlap"
    ]


def test_two_affected_labels_keep_ten_pair_findings_but_take_two_score_penalties():
    issues = _two_labels_crossed_by_five_centre_marks()
    legibility = _legibility(issues)

    assert len(issues) == 10, "every offending label/centre-mark pair remains inspectable"
    assert all("4× ⌀6 THRU" in issue.message for issue in issues)
    assert {issue.location for issue in issues} == {(float(x), 5.0) for x in range(1, 6)}
    assert legibility["warnings"] == 10, "the compatibility count remains raw lint findings"
    assert legibility["by_code"] == {"label_centerline_overlap": 10}
    assert legibility["raw_issues"] == 10
    assert legibility["affected_pairs"] == 10
    assert legibility["primary_warnings"] == 2
    assert legibility["primary_issues"] == 2
    assert legibility["primary_by_code"] == {"label_centerline_overlap": 2}
    assert legibility["basis"] == "layout_issue_severity_with_info_floor"
    assert legibility["score_inventory"] == "primary_issues"
    assert legibility["score"] == pytest.approx(0.9)

    # Prove the producer identity is load-bearing: without it, the raw Cartesian findings
    # once again become ten independent penalties, reproducing the original 0.5 score.
    ungrouped = [
        LintIssue(
            severity=issue.severity,
            message=issue.message,
            location=issue.location,
            code=issue.code,
        )
        for issue in issues
    ]
    assert _legibility(ungrouped)["score"] == pytest.approx(0.5)


def test_run_local_pair_subject_is_not_part_of_the_public_lint_issue_schema():
    issue = _two_labels_crossed_by_five_centre_marks()[0]

    assert isinstance(issue, LintIssue)
    assert "aggregation" not in repr(issue)
    assert not any("aggregation" in key for key in asdict(issue))
    assert not any("aggregation" in key for key in vars(issue))


def test_equal_codes_without_a_shared_subject_remain_independent_primary_issues():
    issues = [
        LintIssue(severity="warning", code="annotation_overlap", message="first collision"),
        LintIssue(severity="warning", code="annotation_overlap", message="second collision"),
    ]

    legibility = _legibility(issues)

    assert legibility["primary_issues"] == 2
    assert legibility["primary_by_code"] == {"annotation_overlap": 2}
    assert legibility["score"] == pytest.approx(0.9)


def test_one_subject_with_two_failure_mechanisms_remains_two_primary_issues():
    issues = _two_labels_crossed_by_five_centre_marks()[:2]
    issues[0].code = "annotation_overlap"

    legibility = _legibility(issues)

    assert legibility["primary_issues"] == 2
    assert legibility["primary_by_code"] == {
        "annotation_overlap": 1,
        "label_centerline_overlap": 1,
    }
    assert legibility["score"] == pytest.approx(0.9)


@pytest.mark.parametrize("severities", [("info", "error"), ("error", "info")])
def test_one_group_uses_its_strongest_severity_in_either_observation_order(severities):
    issues = _two_labels_crossed_by_five_centre_marks()[:2]
    for issue, severity in zip(issues, severities, strict=True):
        issue.severity = severity
        issue.message = severity

    legibility = _legibility(issues)

    assert legibility["errors"] == 1
    assert legibility["infos"] == 1
    assert legibility["primary_issues"] == 1
    assert legibility["primary_errors"] == 1
    assert legibility["primary_infos"] == 0
    assert legibility["score"] == pytest.approx(0.85)


def test_legacy_summary_counts_and_diagnostic_score_keep_raw_finding_semantics(monkeypatch):
    issues = _two_labels_crossed_by_five_centre_marks()
    drawing = build_drawing(Box(20, 15, 10))
    monkeypatch.setattr(drawing, "lint", lambda: issues)

    summary = drawing.lint_summary()

    assert summary["warnings"] == 10
    assert summary["by_code"] == {"label_centerline_overlap": 10}
    assert summary["score"] == summary["diagnostic_score"] == pytest.approx(0.5)
    assert len(summary["issues"]) == 10
    assert all(not any("aggregation" in key for key in issue) for issue in summary["issues"])
    assert summary["quality"]["legibility"]["score"] == pytest.approx(0.9)
