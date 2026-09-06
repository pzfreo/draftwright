"""Public lint projection for unsupported prismatic pocket geometry."""

from draftwright.linting.issues import LintIssue
from draftwright.linting.section_recess_coverage import lint_section_recess_coverage


def lint_prismatic_pocket_coverage(recognition) -> list[LintIssue]:
    """Select this diagnostic from the single authoritative section-recess inventory."""
    return [
        issue
        for issue in lint_section_recess_coverage(recognition)
        if issue.code == "prismatic_pocket_requirement_unsupported"
    ]


__all__ = ["lint_prismatic_pocket_coverage"]
