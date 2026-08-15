"""LintIssue — the structured lint result (ADR 0007).

Vendored from ``build123d_drafting.helpers`` (which keeps its own copy for its
standalone validators). draftwright owns linting; this is its ``LintIssue``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal


@dataclass
class LintIssue:
    severity: Literal["error", "warning", "info"]
    message: str
    location: tuple[float, float] | None = None
    code: str = ""  # stable machine-readable check id, e.g. "label_vs_measured"
    # A ready-to-paste fix snippet, attached by Drawing.lint() via _suggest_fix
    # (#29); None when no concrete repair can be inferred.
    suggestion: str | None = None
    # Compiler measurement identities implicated in a build-time outcome. Empty for checks
    # that do not hold semantic provenance. Kept internal/plain-tuple so adding it does not
    # turn LintIssue into a general requirement ledger (#1018 Gate 1).
    measurement_ids: tuple = ()
    # External source-record identities implicated in this issue. Kept as plain strings and
    # populated narrowly by source reconciliation such as AP242 PMI (#623); this does not
    # introduce a second feature/measurement identity system.
    source_ids: tuple[str, ...] = ()
    # Build-time outcome stage where one issue code can represent more than one failure class.
    # In particular ``gdt_dropped``/``pmi_dropped`` historically cover both malformed input
    # (validation) and a valid candidate that did not fit (placement). Quality components must
    # not infer that distinction from the shared code or its message (#1127).
    outcome_stage: Literal["placement", "validation"] | None = None
    # Recognition-owned physical requirements implicated in a build-time outcome. Appended
    # after the established fields to preserve the positional constructor contract. These are
    # critique evidence, NOT addressable `DimensionId`s: one authored feature-level hole
    # location has two independently observable X/Y requirements while #883 keeps their
    # public addressability deliberately open.
    hole_requirement_ids: tuple = ()


def is_placement_drop(issue: LintIssue) -> bool:
    """Whether *issue* says a valid annotation candidate did not land."""

    stage = issue.outcome_stage
    if stage is not None:
        return bool(stage == "placement")
    return bool(issue.code.endswith("_dropped"))


class _IssueAggregation:
    """Summary-scoped side ledger for pairwise score grouping (#1147).

    Public lint results remain ordinary ``LintIssue`` values. During one ``lint_summary`` call,
    structural lint records an opaque token for each primary annotation beside the emitted issue.
    The ledger retains neither annotations nor their process-local ids after aggregation and is
    never serialised. A fresh instance per summary also makes separate lint runs independent.
    """

    def __init__(self) -> None:
        # Retain the small plain-data issue value for this summary scope so its address cannot
        # be recycled onto an unrelated custom issue. This never retains the annotation/CAD
        # graph: public ``LintIssue`` values carry no aggregation subject (#1147 review).
        self._issues_and_tokens: dict[int, tuple[LintIssue, object, str]] = {}

    def record_pair(self, issue: LintIssue, token: object) -> None:
        self._issues_and_tokens[id(issue)] = (issue, token, issue.code)

    def token_for(self, issue: LintIssue):
        stored = self._issues_and_tokens.get(id(issue))
        return (
            stored[1]
            if stored is not None and stored[0] is issue and stored[2] == issue.code
            else None
        )


_CURRENT_AGGREGATION: ContextVar[_IssueAggregation | None] = ContextVar(
    "draftwright_lint_aggregation", default=None
)


def _current_issue_aggregation() -> _IssueAggregation | None:
    return _CURRENT_AGGREGATION.get()


@contextmanager
def _collect_issue_aggregation():
    """Collect base-lint pair evidence while preserving public ``Drawing.lint`` dispatch."""
    aggregation = _IssueAggregation()
    token = _CURRENT_AGGREGATION.set(aggregation)
    try:
        yield aggregation
    finally:
        _CURRENT_AGGREGATION.reset(token)
