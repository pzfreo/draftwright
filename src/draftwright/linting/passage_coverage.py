"""Explicit completeness disposition for recognised prismatic passages (#1245)."""

from __future__ import annotations

from draftwright.linting.issues import LintIssue


def lint_passage_coverage(recognition) -> list[LintIssue]:
    """Report every authoritative Passage occurrence as unsupported.

    ``RecognitionResult.section_passages`` is the package's counted physical authority.
    The legacy ``.passages`` inventory is only a compatibility projection and must not be
    reported a second time.  A Passage remains outside the Draftwright IR until one drawing
    contract can truthfully represent the provider's complete line/arc section vocabulary.
    """

    passages = tuple(getattr(recognition, "section_passages", ()))
    total = len(passages)
    issues: list[LintIssue] = []
    for ordinal, passage in enumerate(passages, start=1):
        side_count = len(passage.section.boundary)
        qualifier = f"{side_count}-edge " if side_count else ""
        issues.append(
            LintIssue(
                severity="warning",
                code="passage_requirement_unsupported",
                message=(
                    f"recognised {qualifier}prismatic through-opening "
                    f"({ordinal} of {total}) is not represented by Draftwright dimensions; "
                    "review and define its manufacturing section outside automatic drawing "
                    "approval"
                ),
            )
        )
    return issues


__all__ = ["lint_passage_coverage"]
