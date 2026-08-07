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


def lint_pmi_lowering(report: PmiExtractionReport | None, features, mode: str) -> list[LintIssue]:
    """Report extracted AP242 requirements that did not reach concept-shaped IR."""
    if report is None or mode == "off":
        return []

    severity: Literal["error", "info"] = "error" if mode == "annotate" else "info"
    by_source: dict[str, list[object]] = {}
    for feature in features:
        if source_id := getattr(feature, "source_id", ""):
            by_source.setdefault(source_id, []).append(feature)

    issues = []
    for record in report.records:
        if not record.source_id:
            continue
        lowered = by_source.get(record.source_id, ())
        if lowered and any(getattr(feature, "kind", None) != "pmi" for feature in lowered):
            continue
        reason = (
            f"remains a raw {record.kind!r} PMI fallback in the typed IR"
            if lowered
            else "did not produce a typed IR feature"
        )
        issues.append(
            LintIssue(
                severity=severity,
                code="pmi_not_lowered",
                message=f"AP242 source {record.source_id} {reason}",
                source_ids=(record.source_id,),
            )
        )
    return issues


def lint_pmi_rendering(features, registry, mode: str) -> list[LintIssue]:
    """Report source-bearing typed PMI that produced no annotation or placement drop.

    ADR 0010's registry is the existing annotation-to-feature provenance owner. A placement
    rejection is already a structured ``pmi_dropped`` build issue, so this reconciliation is
    derived from those two outcomes rather than maintained in a parallel ledger.
    """
    if mode != "annotate":
        return []

    by_source: dict[str, list[object]] = {}
    for feature in features:
        source_id = getattr(feature, "source_id", "")
        if source_id and getattr(feature, "kind", None) == "authored_dimension":
            by_source.setdefault(source_id, []).append(feature)

    dropped = {
        source_id
        for issue in registry.issues
        if getattr(issue, "code", None) == "pmi_dropped"
        for source_id in getattr(issue, "source_ids", ())
    }
    already_reported = {
        source_id
        for issue in registry.issues
        if getattr(issue, "code", None) == "pmi_not_rendered"
        for source_id in getattr(issue, "source_ids", ())
    }
    return [
        LintIssue(
            severity="error",
            code="pmi_not_rendered",
            message=f"AP242 source {source_id} reached typed drafting IR but produced no annotation",
            source_ids=(source_id,),
        )
        for source_id, source_features in by_source.items()
        if source_id not in dropped | already_reported
        and not any(registry.names_for_feature(feature) for feature in source_features)
    ]
