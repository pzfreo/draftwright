"""Explicit completeness disposition for recognised angled blind steps (#1247)."""

from __future__ import annotations

from draftwright.linting.issues import LintIssue


def lint_angled_step_coverage(recognition) -> list[LintIssue]:
    """Report every aggregate-reconciled ``AngledStep`` as unsupported.

    The provider has already reconciled the slanted face against ``Chamfer``.  Each remaining
    record is therefore one physical angled blind step, counted once.  Its measurements are
    evidence, not a Draftwright decision about whether the drawing requires an angle, the two
    legs, the run length, or a section/detail view.
    """

    steps = tuple(getattr(recognition, "angled_steps", ()))
    total = len(steps)
    issues: list[LintIssue] = []
    for ordinal, step in enumerate(steps, start=1):
        angle = float(getattr(step, "angle", 0.0))
        length = float(getattr(step, "length", 0.0))
        issues.append(
            LintIssue(
                severity="warning",
                code="angled_step_requirement_unsupported",
                message=(
                    f"recognised angled blind step at {angle:g} degrees with {length:g} mm run "
                    f"({ordinal} of {total}) is not represented by Draftwright dimensions; "
                    "review and define its manufacturing geometry outside automatic drawing "
                    "approval"
                ),
            )
        )
    return issues


__all__ = ["lint_angled_step_coverage"]
