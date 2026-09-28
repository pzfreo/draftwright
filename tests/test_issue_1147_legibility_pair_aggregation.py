"""Regression coverage for pairwise legibility score aggregation (#1147)."""

from __future__ import annotations

import asyncio
import gc
import pickle
import weakref
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from build123d import Box

from draftwright import build_drawing
from draftwright.drawing import lint_snapshot
from draftwright.linting.issues import (
    LintIssue,
    _collect_issue_aggregation,
    _current_issue_aggregation,
    _IssueAggregation,
)
from draftwright.linting.quality import quality_components
from draftwright.linting.structural import lint_drawing


def _legibility(issues, aggregation=None):
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
        _aggregation=aggregation,
    )["legibility"]


def _crossed_items():
    labels = [
        SimpleNamespace(label="4× ⌀6 THRU", label_bbox=(x, 20.0, x + 10.0, 30.0))
        for x in (20.0, 40.0)
    ]
    centre_marks = [
        SimpleNamespace(
            is_centerline=True,
            segments=(((18.0, float(y)), (52.0, float(y))),),
        )
        for y in range(21, 26)
    ]
    return [*labels, *centre_marks]


def _two_labels_crossed_by_five_centre_marks(aggregation=None):
    aggregation = aggregation or _IssueAggregation()
    issues = [
        issue
        for issue in lint_drawing(_crossed_items(), _aggregation=aggregation)
        if issue.code == "label_centerline_overlap"
    ]
    return issues, aggregation


def test_two_affected_labels_keep_ten_pair_findings_but_take_two_score_penalties():
    issues, aggregation = _two_labels_crossed_by_five_centre_marks()
    legibility = _legibility(issues, aggregation)

    assert len(issues) == 10, "every offending label/centre-mark pair remains inspectable"
    assert all("4× ⌀6 THRU" in issue.message for issue in issues)
    assert {issue.location for issue in issues} == {(35.0, float(y)) for y in range(21, 26)}
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


def test_run_local_pair_subject_does_not_change_public_lint_issue_value_behaviour():
    # The source annotation deliberately contains an unpickleable member. Only the public
    # issue value may survive lint; the summary-scoped side ledger must not retain that graph.
    items = _crossed_items()
    items[0].unpickleable = lambda: None
    aggregation = _IssueAggregation()
    issue = next(
        issue
        for issue in lint_drawing(items, _aggregation=aggregation)
        if issue.code == "label_centerline_overlap"
    )

    assert type(issue) is LintIssue
    assert "aggregation" not in repr(issue)
    assert not any("aggregation" in key for key in asdict(issue))
    assert not any("aggregation" in key for key in vars(issue))
    assert replace(issue) == issue
    assert pickle.loads(pickle.dumps(issue)) == issue


def test_summary_evidence_does_not_retain_source_annotations_or_their_graphs():
    class SourceGraph:
        pass

    class Annotation:
        pass

    source_graph = SourceGraph()
    annotation = Annotation()
    annotation.label = "4× ⌀6 THRU"
    annotation.label_bbox = (20.0, 20.0, 30.0, 30.0)
    annotation.source_graph = source_graph
    annotation_ref = weakref.ref(annotation)
    graph_ref = weakref.ref(source_graph)
    aggregation = _IssueAggregation()
    issues = lint_drawing(
        [
            annotation,
            SimpleNamespace(
                is_centerline=True,
                segments=(((18.0, 25.0), (52.0, 25.0)),),
            ),
        ],
        _aggregation=aggregation,
    )
    assert [issue.code for issue in issues] == ["label_centerline_overlap"]

    del annotation, source_graph
    gc.collect()

    assert annotation_ref() is None
    assert graph_ref() is None
    assert aggregation.token_for(issues[0]) is not None


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
    aggregation = _IssueAggregation()
    subject_token = object()
    issues = [
        LintIssue(severity="warning", code="annotation_overlap", message="collision"),
        LintIssue(
            severity="warning",
            code="label_centerline_overlap",
            message="centreline crossing",
        ),
    ]
    for issue in issues:
        aggregation.record_pair(issue, subject_token)

    legibility = _legibility(issues, aggregation)

    assert legibility["primary_issues"] == 2
    assert legibility["primary_by_code"] == {
        "annotation_overlap": 1,
        "label_centerline_overlap": 1,
    }
    assert legibility["score"] == pytest.approx(0.9)


@pytest.mark.parametrize("severities", [("info", "error"), ("error", "info")])
def test_one_group_uses_its_strongest_severity_in_either_observation_order(severities):
    all_issues, aggregation = _two_labels_crossed_by_five_centre_marks()
    issues = all_issues[:2]
    for issue, severity in zip(issues, severities, strict=True):
        issue.severity = severity
        issue.message = severity

    legibility = _legibility(issues, aggregation)

    assert legibility["errors"] == 1
    assert legibility["infos"] == 1
    assert legibility["primary_issues"] == 1
    assert legibility["primary_errors"] == 1
    assert legibility["primary_infos"] == 0
    assert legibility["score"] == pytest.approx(0.85)


def test_separate_lint_runs_have_independent_summary_scoped_group_tokens():
    first, first_aggregation = _two_labels_crossed_by_five_centre_marks()
    second, second_aggregation = _two_labels_crossed_by_five_centre_marks()

    assert _legibility(first, first_aggregation)["primary_issues"] == 2
    assert _legibility(second, second_aggregation)["primary_issues"] == 2
    assert all(first_aggregation.token_for(issue) is None for issue in second)
    assert all(second_aggregation.token_for(issue) is None for issue in first)


def test_separate_structural_passes_in_one_summary_ledger_get_distinct_tokens():
    aggregation = _IssueAggregation()
    first, _ = _two_labels_crossed_by_five_centre_marks(aggregation)
    second, _ = _two_labels_crossed_by_five_centre_marks(aggregation)

    combined = _legibility([*first, *second], aggregation)

    assert combined["raw_issues"] == 20
    assert combined["primary_issues"] == 4
    assert aggregation.token_for(first[0]) is not aggregation.token_for(second[0])


def test_ledger_retains_plain_issue_values_so_custom_replacements_cannot_reuse_their_ids():
    aggregation = _IssueAggregation()
    issues, returned_aggregation = _two_labels_crossed_by_five_centre_marks(aggregation)
    assert returned_aggregation is aggregation
    recorded = weakref.ref(issues[0])
    del issues

    assert recorded() is not None, "the summary ledger must keep the address owner alive"

    del returned_aggregation, aggregation
    assert recorded() is None, "the summary ledger must not retain issues beyond its scope"


def test_multi_scale_drawing_aggregates_across_both_scale_groups():
    drawing = build_drawing(Box(20, 15, 10))
    items = _crossed_items()
    for item in items[:1] + items[2:7]:
        item._dw_scale = 1.0
    for item in items[1:2]:
        item._dw_scale = 2.0
    # Give the second label its own five centre marks in the second scale group.
    second_marks = [
        SimpleNamespace(
            is_centerline=True,
            segments=mark.segments,
            _dw_scale=2.0,
        )
        for mark in items[2:7]
    ]
    drawing.items = [items[0], *items[2:7], items[1], *second_marks]
    drawing.views.clear()
    drawing.part = None

    legibility = drawing.lint_summary()["quality"]["legibility"]

    # 20, not 10. This test used to pin the per-scale-group split: each label was compared only
    # with the five centre marks carrying its own `_dw_scale`, so 5 + 5. That split is what made
    # the pairwise checks blind across groups (#1216) — a label overlapping a centre mark is a
    # geometric fact, and which view's scale tagged the mark has nothing to do with it. Every
    # label now meets every mark: 2 × 10.
    #
    # What this test is FOR is unchanged, and is the point: the aggregation still collapses the
    # raw findings to 2 primaries and the score is still 0.9. The pairing got complete; the
    # aggregation did not move.
    assert legibility["raw_issues"] == 20
    assert legibility["primary_issues"] == 2
    assert legibility["score"] == pytest.approx(0.9)


def test_legacy_summary_counts_and_diagnostic_score_keep_raw_finding_semantics():
    drawing = build_drawing(Box(20, 15, 10))
    drawing.items = _crossed_items()
    drawing.views.clear()
    drawing.part = None

    summary = drawing.lint_summary()

    assert summary["warnings"] == 10
    assert summary["by_code"] == {"label_centerline_overlap": 10}
    assert summary["score"] == summary["diagnostic_score"] == pytest.approx(0.5)
    assert len(summary["issues"]) == 10
    assert all(not any("aggregation" in key for key in issue) for issue in summary["issues"])
    assert summary["quality"]["legibility"]["score"] == pytest.approx(0.9)


def test_scoped_lint_keeps_pair_evidence_and_public_dispatch_issue_1945(monkeypatch):
    drawing = build_drawing(Box(20, 15, 10))
    drawing.items = _crossed_items()
    drawing.views.clear()
    drawing.part = None
    expected = drawing.lint_summary()
    base_lint = drawing.lint
    calls = []

    def counted_lint(*, physical=True):
        calls.append(physical)
        return base_lint(physical=physical)

    monkeypatch.setattr(drawing, "lint", counted_lint)
    issues, summary = lint_snapshot(drawing)

    assert calls == [True]
    assert len(issues) == 10  # two labels crossed by five centre marks
    assert summary == expected
    assert summary["quality"]["legibility"]["affected_pairs"] == 10
    assert summary["quality"]["legibility"]["primary_issues"] == 2


def test_nested_lint_snapshot_does_not_reuse_outer_issues_issue_1945(monkeypatch):
    drawing = build_drawing(Box(20, 15, 10))
    outer = LintIssue(severity="warning", code="outer_marker", message="outer")
    inner = LintIssue(severity="warning", code="inner_marker", message="inner")
    calls = 0
    nested_codes = None
    nested_summary = None
    entered = False
    base_summary = drawing.lint_summary

    def reentrant_lint(*, physical=True):
        nonlocal calls, nested_codes
        calls += 1
        if calls == 1:
            return [outer]
        if calls == 2:
            nested_codes = drawing.lint_summary()["by_code"]
        return [inner]

    def reentrant_summary():
        nonlocal entered, nested_summary
        if not entered:
            entered = True
            _, nested_summary = lint_snapshot(drawing)
        return base_summary()

    monkeypatch.setattr(drawing, "lint", reentrant_lint)
    monkeypatch.setattr(drawing, "lint_summary", reentrant_summary)
    issues, summary = lint_snapshot(drawing)

    assert issues == (outer,)
    assert summary["by_code"] == {"outer_marker": 1}
    assert nested_summary["by_code"] == {"inner_marker": 1}
    assert nested_codes == {"inner_marker": 1}
    assert calls == 3


def test_lint_snapshot_does_not_keep_stale_issues_after_mutation_issue_1945():
    drawing = build_drawing(Box(20, 15, 10))
    drawing.items = _crossed_items()
    drawing.views.clear()
    drawing.part = None

    issues, summary = lint_snapshot(drawing)
    assert len(issues) == 10
    assert summary["by_code"]["label_centerline_overlap"] == 10

    drawing.items = []
    assert drawing.lint_summary()["by_code"].get("label_centerline_overlap", 0) == 0


def test_lint_summary_still_dispatches_through_a_public_lint_override(monkeypatch):
    drawing = build_drawing(Box(20, 15, 10))
    calls = []
    external = LintIssue(severity="error", code="external_policy", message="custom critique")

    def custom_lint(*, physical=True):
        calls.append(physical)
        return [external]

    monkeypatch.setattr(drawing, "lint", custom_lint)

    summary = drawing.lint_summary()

    assert calls == [True]
    assert summary["by_code"] == {"external_policy": 1}
    assert summary["issues"][0]["message"] == "custom critique"


def test_public_override_replacements_cannot_inherit_discarded_base_issue_tokens(monkeypatch):
    drawing = build_drawing(Box(20, 15, 10))
    drawing.items = _crossed_items()
    drawing.views.clear()
    drawing.part = None
    base_lint = drawing.lint

    def replacing_lint(*, physical=True):
        discarded = base_lint(physical=physical)
        assert len(discarded) == 10
        return [
            LintIssue(
                severity="warning",
                code="label_centerline_overlap",
                message=f"custom replacement {index}",
            )
            for index in range(10)
        ]

    monkeypatch.setattr(drawing, "lint", replacing_lint)

    legibility = drawing.lint_summary()["quality"]["legibility"]

    assert legibility["raw_issues"] == 10
    assert legibility["affected_pairs"] == 0
    assert legibility["primary_issues"] == 10
    assert legibility["score"] == pytest.approx(0.5)


def test_public_override_code_mutations_cannot_inherit_the_old_failure_mechanism(monkeypatch):
    drawing = build_drawing(Box(20, 15, 10))
    drawing.items = _crossed_items()
    drawing.views.clear()
    drawing.part = None
    base_lint = drawing.lint

    def mutating_lint(*, physical=True):
        issues = base_lint(physical=physical)
        assert len(issues) == 10
        for issue in issues:
            issue.code = "annotation_overlap"
        return issues

    monkeypatch.setattr(drawing, "lint", mutating_lint)

    legibility = drawing.lint_summary()["quality"]["legibility"]

    assert legibility["by_code"] == {"annotation_overlap": 10}
    assert legibility["affected_pairs"] == 0
    assert legibility["primary_issues"] == 10
    assert legibility["score"] == pytest.approx(0.5)


def test_a_failing_public_lint_override_cannot_leak_summary_context(monkeypatch):
    drawing = build_drawing(Box(20, 15, 10))

    def failing_lint(*, physical=True):
        assert _current_issue_aggregation() is not None
        raise RuntimeError("custom critique failed")

    monkeypatch.setattr(drawing, "lint", failing_lint)

    with pytest.raises(RuntimeError, match="custom critique failed"):
        drawing.lint_summary()

    assert _current_issue_aggregation() is None


def test_nested_summary_context_restores_the_outer_ledger():
    with _collect_issue_aggregation() as outer:
        assert _current_issue_aggregation() is outer
        with _collect_issue_aggregation() as inner:
            assert inner is not outer
            assert _current_issue_aggregation() is inner
        assert _current_issue_aggregation() is outer

    assert _current_issue_aggregation() is None


def test_overlapping_async_summary_contexts_keep_distinct_task_local_ledgers():
    async def exercise():
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()
        first_observed = asyncio.Event()
        second_exited = asyncio.Event()

        async def collect_first():
            with _collect_issue_aggregation() as aggregation:
                first_entered.set()
                await second_entered.wait()
                # Observe A while B's context is still active. A module-global stack would
                # expose B here; task-local context must continue to expose A.
                while_second_is_active = _current_issue_aggregation()
                first_observed.set()
                await second_exited.wait()
                after_second_exits = _current_issue_aggregation()
                return aggregation, while_second_is_active, after_second_exits

        async def collect_second():
            await first_entered.wait()
            with _collect_issue_aggregation() as aggregation:
                second_entered.set()
                await first_observed.wait()
                while_first_is_active = _current_issue_aggregation()
            second_exited.set()
            return aggregation, while_first_is_active

        return await asyncio.gather(collect_first(), collect_second())

    first, second = asyncio.run(exercise())

    assert first[0] is not second[0]
    assert first[1] is first[0]
    assert first[2] is first[0]
    assert second[1] is second[0]
    assert _current_issue_aggregation() is None
