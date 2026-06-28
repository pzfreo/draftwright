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
