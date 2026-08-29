"""Explicit completeness disposition for recognised prismatic pockets (#1246)."""

from __future__ import annotations

from draftwright.linting.issues import LintIssue


def lint_prismatic_pocket_coverage(recognition) -> list[LintIssue]:
    """Report every aggregate-reconciled PrismaticPocket occurrence as unsupported.

    The aggregate has already removed candidates also owned by ``Pocket``.  Each remaining record
    is therefore one physical recess not superseded by that family, counted once.  Its
    arbitrary polygonal section has no general Draftwright dimension grammar yet, so reporting
    it is truthful while converting it to width/length or regular-polygon A/F would not be.
    """

    pockets = tuple(getattr(recognition, "prismatic_pockets", ()))
    total = len(pockets)
    issues: list[LintIssue] = []
    for ordinal, pocket in enumerate(pockets, start=1):
        side_count = int(getattr(pocket, "sides", 0))
        qualifier = f"{side_count}-sided " if side_count else "polygonal "
        depth = float(getattr(pocket, "depth", 0.0))
        issues.append(
            LintIssue(
                severity="warning",
                code="prismatic_pocket_requirement_unsupported",
                message=(
                    f"recognised {qualifier}blind prismatic recess {depth:g} mm deep "
                    f"({ordinal} of {total}) is not represented by Draftwright dimensions; "
                    "review and define its section and depth outside automatic drawing approval"
                ),
            )
        )
    return issues


__all__ = ["lint_prismatic_pocket_coverage"]
