"""Source-to-extraction completeness for AP242 PMI (#623)."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from draftwright.linting.issues import LintIssue
from draftwright.pmi import PmiExtractionReport


def _registry_subject(feature):
    """The feature a placed annotation is registered against for source-bearing IR."""
    return getattr(feature, "origin", None) or feature


def _source_ids(item) -> tuple[str, ...]:
    """Return every external source represented by one record or IR feature."""
    plural = tuple(getattr(item, "source_ids", ()))
    singular = getattr(item, "source_id", "")
    return tuple(dict.fromkeys(((singular,) if singular else ()) + plural))


def _source_category_counts(report: PmiExtractionReport) -> dict[str, int]:
    return dict(sorted(Counter(source.category for source in report.sources).items()))


def lint_pmi_ignored(
    report: PmiExtractionReport | None, mode: str, *, defaulted: bool
) -> list[LintIssue]:
    """Report one source-level event when off mode deliberately ignores authored PMI."""
    if report is None or mode != "off" or not report.sources:
        return []

    counts = _source_category_counts(report)
    names = {
        "dimension": "dimension",
        "geometric_tolerance": "geometric tolerance",
        "datum": "datum reference",
    }
    inventory = ", ".join(
        f"{count} {names[category]}{'s' if count != 1 else ''}"
        for category, count in counts.items()
    )
    reason = "PMI annotation is disabled by default" if defaulted else "pmi='off' was selected"
    return [
        LintIssue(
            severity="info",
            code="pmi_present_but_ignored",
            message=(
                f"AP242 PMI is present but ignored because {reason} ({inventory}); "
                "use --pmi report to inspect it or --pmi annotate to place supported PMI"
            ),
        )
    ]


def pmi_stage_summary(
    report: PmiExtractionReport | None, features, registry, mode: str
) -> dict[str, object] | None:
    """Derive source-to-render stage counts from the canonical report, IR, and registry.

    The counts are stage inventories, not an additive funnel: presentation-only source labels
    are inventoried but produce no extracted record, while a partially extracted record still
    reached the extraction stage. Distinct source IDs keep every count per source entity.
    """
    if report is None or not report.sources:
        return None

    extracted = {source_id for record in report.records for source_id in _source_ids(record)}
    lowered_features = [
        feature
        for feature in features
        if set(_source_ids(feature)) & extracted and getattr(feature, "kind", None) != "pmi"
    ]
    lowered = {source_id for feature in lowered_features for source_id in _source_ids(feature)}
    rendered = {
        source_id
        for feature in lowered_features
        for source_id in _source_ids(feature)
        if registry.names_for_feature(_registry_subject(feature))
    }
    dropped = {
        source_id
        for issue in registry.issues
        if getattr(issue, "code", None) == "pmi_dropped"
        for source_id in getattr(issue, "source_ids", ())
        if source_id in lowered
    }
    return {
        "mode": mode,
        "sources": len(report.sources),
        "by_category": _source_category_counts(report),
        "extracted": len(extracted),
        "lowered": len(lowered),
        "rendered": len(rendered),
        "dropped": len(dropped),
    }


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
        for source_id in _source_ids(feature):
            by_source.setdefault(source_id, []).append(feature)

    issues = []
    for record in report.records:
        for source_id in _source_ids(record):
            lowered = by_source.get(source_id, ())
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
                    message=f"AP242 source {source_id} {reason}",
                    source_ids=(source_id,),
                )
            )
    return issues


#: Codes that already explain why a typed record produced no annotation. This check exists
#: to catch an UNEXPLAINED omission, so a record carrying one of these must not also be
#: reported here — that is one omission with two reporters at different severities, which
#: is the defect #1190 was opened for.
_EXPLAINED_OMISSION_CODES = frozenset(
    {
        "pmi_not_rendered",
        "dimension_kind_unsupported",
        # `authored_dim_degenerate` exists so "a caller sees a specific reason instead of a
        # misleading 'no room'" — and was then reported alongside a second, vaguer error for
        # the same source. Fixing that only for the code #1177 introduced would have left
        # the defect in place next door.
        "authored_dim_degenerate",
    }
)


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
        if getattr(feature, "kind", None) != "pmi":
            for source_id in _source_ids(feature):
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
        if getattr(issue, "code", None) in _EXPLAINED_OMISSION_CODES
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
        and not any(
            registry.names_for_feature(_registry_subject(feature)) for feature in source_features
        )
    ]
