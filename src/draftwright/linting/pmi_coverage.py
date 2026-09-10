"""Source-to-extraction completeness for AP242 PMI (#623)."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from draftwright.linting.issues import LintIssue
from draftwright.pmi import PmiExtractionReport

_SUPPORTED_MANUFACTURING_REQUIREMENTS = frozenset(("external_thread", "internal_thread", "knurl"))


def _registry_subject(feature):
    """The feature a placed annotation is registered against for source-bearing IR."""
    return getattr(feature, "origin", None) or feature


def _registry_names_for_feature(registry, feature) -> list:
    """Names owned directly by an IR feature or by its opaque compiled provenance ref."""
    subject = _registry_subject(feature)
    direct = registry.names_for_feature(subject)
    if direct:
        return list(direct)
    if not all(hasattr(registry, method) for method in ("names", "feature_of", "measurement_of")):
        return []
    return [
        name
        for name in registry.names()
        if any(
            getattr(measurement, "feature", None) == subject
            for measurement in registry.measurement_of(name)
        )
    ]


def _registry_names_for_decoration(registry, key: tuple) -> list:
    """Canonical annotations that draw the exact decorated parameter."""
    feature = key[0]
    if len(key) >= 3 and key[1] in ("nominal_requirement", "manufacturing_requirement"):
        parameter = str(key[2])
    else:
        kind = str(key[1]) if len(key) > 1 else ""
        role = str(key[2]) if len(key) > 2 else ""
        parameter = f"{role}.{kind}" if role else ""
    return [
        name
        for name in registry.names()
        if any(
            getattr(measurement, "feature", None) == feature
            and (
                getattr(measurement, "parameter", "") == parameter
                if parameter
                else getattr(measurement, "parameter", "").endswith(f".{kind}")
            )
            for measurement in registry.measurement_of(name)
        )
    ]


def _source_ids(item) -> tuple[str, ...]:
    """Return every external source represented by one record or IR feature."""
    plural = tuple(getattr(item, "source_ids", ()))
    singular = getattr(item, "source_id", "")
    return tuple(dict.fromkeys(((singular,) if singular else ()) + plural))


def _source_category_counts(report: PmiExtractionReport) -> dict[str, int]:
    return dict(sorted(Counter(source.category for source in report.sources).items()))


def _source_drop_ids(registry) -> set[str]:
    """External sources with a specific placement/validation drop outcome.

    Imported PMI does not render through one dedicated pass: a typed thread, knurl, datum,
    or dimension enters the ordinary feature renderer and therefore keeps that renderer's
    specific ``*_dropped`` code.  Exact source provenance, rather than the generic code
    ``pmi_dropped``, is what makes the outcome a PMI drop.
    """
    return {
        source_id
        for issue in registry.issues
        if str(getattr(issue, "code", "")) == "pmi_dropped"
        or str(getattr(issue, "code", "")).endswith("_dropped")
        for source_id in getattr(issue, "source_ids", ())
    }


def _decorated_source_features(decorations, *, features=()) -> list[tuple[tuple, tuple[str, ...]]]:
    """Imported requirement provenance carried by canonical feature decorations (#1116)."""
    out = []
    for key, value in (decorations or {}).items():
        if not isinstance(key, tuple) or not key:
            continue
        source_ids = _source_ids(value)
        if source_ids:
            out.append((key, source_ids))
    for feature in features:
        owner = feature
        target = getattr(feature, "member", feature)
        thread = getattr(target, "thread", None)
        source_ids = _source_ids(thread)
        if source_ids and feature.kind in ("hole", "pattern"):
            out.append(
                (
                    (owner, "manufacturing_requirement", "bore.diameter", "thread"),
                    source_ids,
                )
            )
        knurl = getattr(target, "knurl", None)
        source_ids = _source_ids(knurl)
        if source_ids:
            parameter = "step.diameter" if feature.kind == "step" else "boss.diameter"
            out.append(
                (
                    (owner, "manufacturing_requirement", parameter, "knurl"),
                    source_ids,
                )
            )
        if feature.kind in ("step", "boss"):
            thread = getattr(feature, "thread", None)
            source_ids = _source_ids(thread)
            if source_ids:
                parameter = "step.diameter" if feature.kind == "step" else "boss.diameter"
                out.append(
                    (
                        (owner, "manufacturing_requirement", parameter, "thread"),
                        source_ids,
                    )
                )
    return out


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
        "manufacturing_requirement": "manufacturing requirement",
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
    report: PmiExtractionReport | None,
    features,
    registry,
    mode: str,
    *,
    decorations=None,
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
    decorated = _decorated_source_features(decorations, features=features)
    lowered_features.extend(key[0] for key, source_ids in decorated if set(source_ids) & extracted)
    lowered = {source_id for feature in lowered_features for source_id in _source_ids(feature)}
    lowered.update(
        source_id
        for _key, source_ids in decorated
        for source_id in source_ids
        if source_id in extracted
    )
    rendered = {
        source_id
        for feature in lowered_features
        for source_id in _source_ids(feature)
        if _registry_names_for_feature(registry, feature)
    }
    rendered.update(
        source_id
        for key, source_ids in decorated
        if _registry_names_for_decoration(registry, key)
        for source_id in source_ids
        if source_id in extracted
    )
    dropped = _source_drop_ids(registry) & lowered
    return {
        "mode": mode,
        # Which document these counts describe (#1563). The drawing's geometry and the AP242
        # census need not come from the same call any more — a script-built Sheet names its
        # source — so a consumer reading `lowered: 10` can see what the 10 were counted against
        # instead of inferring it from whatever file it thinks was passed.
        "source": {"name": report.source_name, "sha256": report.source_sha256},
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


def lint_pmi_lowering(
    report: PmiExtractionReport | None, features, mode: str, *, decorations=None
) -> list[LintIssue]:
    """Report extracted AP242 requirements that did not reach concept-shaped IR."""
    if report is None or mode == "off":
        return []

    by_source: dict[str, list[object]] = {}
    for feature in features:
        for source_id in _source_ids(feature):
            by_source.setdefault(source_id, []).append(feature)
    for key, source_ids in _decorated_source_features(decorations, features=features):
        for source_id in source_ids:
            by_source.setdefault(source_id, []).append(key[0])

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
            unsupported_manufacturing = (
                record.source_category == "manufacturing_requirement"
                and record.kind not in _SUPPORTED_MANUFACTURING_REQUIREMENTS
            )
            severity: Literal["error", "warning", "info"] = (
                "info"
                if mode != "annotate"
                else "warning"
                if unsupported_manufacturing
                else "error"
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


def lint_pmi_unreconciled(report, features, *, decorations=None) -> list[LintIssue]:
    """Report source-bearing content that NO census was available to check (#1563).

    Every other check in this module reasons from an extraction report. When there is none the
    whole module falls silent, and the two states it cannot then distinguish are exactly the
    ones a consumer needs kept apart: a drawing with no source PMI, and a drawing whose source
    PMI was never looked at. A script-built `Sheet` was always the second — it holds an
    in-memory solid — so the raw `sheet.add(PmiFeature(...))` fallbacks the emitter writes, and
    the `source='ap242_pmi'` provenance on ordinary declarations, went unexamined in silence.
    Deleting them changed no diagnostic at all.

    This runs on exactly that gap: content claiming an AP242 origin with no census behind it.
    It reports the claim as unverified, never as satisfied or as false — the honest outcome
    when the evidence is absent rather than negative. Supply the document (``Sheet(...,
    source=…)`` or a STEP path) and the real reconciliation replaces this.
    """
    if report is not None:
        return []
    unchecked = {
        source_id
        for feature in features
        for source_id in _source_ids(feature)
        if getattr(feature, "source", "") == "ap242_pmi" or getattr(feature, "kind", None) == "pmi"
    }
    for _key, source_ids in _decorated_source_features(decorations, features=features):
        unchecked.update(source_ids)
    raw = sum(1 for feature in features if getattr(feature, "kind", None) == "pmi")
    if not unchecked and not raw:
        return []
    detail = f"{len(unchecked)} AP242 source reference(s)"
    if raw:
        detail += f", including {raw} raw PMI record(s) not lowered to a drafting concept"
    return [
        LintIssue(
            severity="warning",
            code="pmi_unreconciled",
            message=(
                f"this drawing declares {detail}, and no AP242 census was available to check "
                "them — the claims are unverified, not satisfied. Name the source STEP "
                "(Sheet(..., source='part.step', pmi='annotate')) to reconcile them"
            ),
            source_ids=tuple(sorted(unchecked)),
        )
    ]


def lint_pmi_source_unknown(report, features, *, decorations=None) -> list[LintIssue]:
    """Report declared AP242 provenance the census does not contain (#1563).

    `lint_pmi_lowering` walks the census and asks what became of each record. Nothing walked
    the other way, so a declaration could claim ``source='ap242_pmi'`` with a `source_id` no
    record carries and be accepted in silence — a fabricated provenance, which is a worse
    failure than a missing one because it reads as evidence.
    """
    if report is None:
        return []
    known = {source_id for record in report.records for source_id in _source_ids(record)}
    known.update(entity.source_id for entity in report.sources if getattr(entity, "source_id", ""))
    claimed: dict[str, object] = {}
    for feature in features:
        if (
            getattr(feature, "source", "") != "ap242_pmi"
            and getattr(feature, "kind", None) != "pmi"
        ):
            continue
        for source_id in _source_ids(feature):
            claimed.setdefault(source_id, feature)
    for _key, source_ids in _decorated_source_features(decorations, features=features):
        for source_id in source_ids:
            claimed.setdefault(source_id, None)
    return [
        LintIssue(
            severity="error",
            code="pmi_source_unknown",
            message=(
                f"a declaration claims AP242 source {source_id}, which "
                f"{report.source_name or 'the reconciled document'} does not contain — the "
                "provenance is fabricated, not merely unmatched"
            ),
            source_ids=(source_id,),
        )
        for source_id in sorted(claimed)
        if source_id not in known
    ]


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
        "authored_dim_source_unresolved",
    }
)


def lint_pmi_rendering(features, registry, mode: str, *, decorations=None) -> list[LintIssue]:
    """Report source-bearing typed PMI that produced no annotation or placement drop.

    ADR 5 (was 0010)'s registry is the existing annotation-to-feature provenance owner. A placement
    rejection is already a structured source-bearing ``*_dropped`` build issue (the code stays
    specific to the ordinary renderer typed PMI entered), so this reconciliation is derived
    from those two outcomes rather than maintained in a parallel ledger.
    """
    if mode != "annotate":
        return []

    by_source: dict[str, list[object]] = {}
    for feature in features:
        if getattr(feature, "kind", None) != "pmi":
            for source_id in _source_ids(feature):
                by_source.setdefault(source_id, []).append(feature)
    for key, source_ids in _decorated_source_features(decorations, features=features):
        for source_id in source_ids:
            by_source.setdefault(source_id, []).append(key)

    dropped = _source_drop_ids(registry)
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
            _registry_names_for_decoration(registry, feature)
            if isinstance(feature, tuple)
            else _registry_names_for_feature(registry, feature)
            for feature in source_features
        )
    ]
