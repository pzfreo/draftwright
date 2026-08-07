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
