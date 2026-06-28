"""Linting for draftwright drawings (ADR 0007).

draftwright owns linting; ``build123d-drafting-helpers`` is the rendering
library. This package is the single home for it:

- :mod:`.issues` — ``LintIssue``, the structured lint result.
- :mod:`.structural` — ``lint_drawing``: duck-typed structural checks on a
  composed annotation list (overlap, page bounds, label-vs-measured). Vendored
  from ``build123d_drafting.helpers``; upstream copy frozen and deprecated.
- :mod:`.coverage` — ``lint_feature_coverage`` + ``CoverageState``: the
  feature-coverage completeness check and the signal the passes record.
- :mod:`.suggest` — ``_suggest_fix``: ready-to-paste fix snippets (#29).

Import the public surface from here, not the submodules.
"""

from __future__ import annotations

from draftwright.linting.coverage import (
    CoverageState,
    lint_axial_coverage,
    lint_feature_coverage,
)
from draftwright.linting.issues import LintIssue
from draftwright.linting.structural import lint_drawing
from draftwright.linting.suggest import _suggest_fix

__all__ = [
    "CoverageState",
    "LintIssue",
    "_suggest_fix",
    "lint_axial_coverage",
    "lint_drawing",
    "lint_feature_coverage",
]
