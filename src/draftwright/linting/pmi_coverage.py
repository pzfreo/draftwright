"""Source-to-extraction completeness for AP242 PMI (#623)."""

from __future__ import annotations

from typing import Literal

from draftwright.linting.issues import LintIssue
from draftwright.pmi import PmiExtractionReport


def lint_pmi_extraction(report: PmiExtractionReport | None, mode: str) -> list[LintIssue]:
    """Report source PMI that did not survive extraction.

    ``report`` mode is diagnostic, so its outcomes are informational. ``annotate`` promises
    drawing output and therefore treats the same missing source requirement as an error.
    Presentation-only labels are retained in the census but are not requirements.
    """
    if report is None or mode == "off":
        return []

    severity: Literal["error", "info"] = "error" if mode == "annotate" else "info"
    issues = []
    if report.error:
        issues.append(
            LintIssue(
                severity=severity,
                code="pmi_not_extracted",
                message=f"AP242 PMI could not be inventoried: {report.error}",
            )
        )
    issues.extend(
        LintIssue(
            severity=severity,
            code="pmi_not_extracted",
            message=(
                f"AP242 {source.category} {source.source_id} was discovered but not "
                f"extracted: {source.reason}"
            ),
            source_ids=(source.source_id,),
        )
        for source in report.sources
        if source.outcome in ("not_extracted", "partially_extracted")
    )
    return issues
