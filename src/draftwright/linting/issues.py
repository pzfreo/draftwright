"""LintIssue — the structured lint result (ADR 0007).

Vendored from ``build123d_drafting.helpers`` (which keeps its own copy for its
standalone validators). draftwright owns linting; this is its ``LintIssue``.
"""

from __future__ import annotations

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


class _PairLintIssue(LintIssue):
    """Internal pair observation carrying its primary annotation by identity (#1147).

    ``LintIssue`` is a public structured-diagnostic dataclass, so run-local aggregation state
    must not become one of its fields. This private subclass stores the subject outside the
    dataclass schema: :func:`dataclasses.asdict` sees only the stable public fields, while
    quality aggregation can still recognise several raw comparisons of the same object.

    Holding the object itself (not only ``id(subject)``) also owns the token's lifetime. Two
    issue lists accumulated from separate lint runs cannot accidentally merge after CPython
    reuses a released object's address.
    """

    __slots__ = ("_aggregation_subject",)

    def __init__(self, *, aggregation_subject, **kwargs) -> None:
        super().__init__(**kwargs)
        self._aggregation_subject = aggregation_subject
