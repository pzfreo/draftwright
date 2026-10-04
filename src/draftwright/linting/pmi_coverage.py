"""Source-to-extraction completeness for AP242 PMI (#623)."""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from math import dist
from typing import Literal

from build123d import Align, Location, Mode, Sketch, Text

from draftwright._geometry import _cylindrical_finish_site, _fmt_pmi_magnitude
from draftwright.linting.issues import LintIssue
from draftwright.pmi import PmiExtractionReport

_SUPPORTED_MANUFACTURING_REQUIREMENTS = frozenset(
    ("external_thread", "internal_thread", "knurl", "edge_condition", "surface_finish")
)
_MANUFACTURING_REF = re.compile(r"\bSEE (MFG [1-9][0-9]*)\b")
_DIAMETER_TOKEN = re.compile(r"ø(\d+(?:\.\d+)?)")
_KNURL_MAX_TOKEN = re.compile(r"ø(\d+(?:\.\d+)?)\s+MAX AFTER KNURL\b")


def _source_knurl_max_is_visible(label: str, printed: str, aspect) -> bool:
    """Judge source maximum ink independently of the row's callout formatter."""
    maximum = getattr(aspect, "maximum_diameter", None)
    if maximum is None:
        return True
    expected = Decimal(_fmt_pmi_magnitude(maximum))
    row_match = _KNURL_MAX_TOKEN.search(printed)
    if row_match is None or Decimal(row_match.group(1)) != expected:
        return False
    leader_match = _DIAMETER_TOKEN.search(label)
    return leader_match is not None and Decimal(leader_match.group(1)) == expected


def _title_value_has_finished_ink(title, field: str, value: str) -> bool:
    """Compare the selected STEP value with the finished faces in its title cell.

    Input specs and the PDF text layer can survive a helper that omits the
    visible value.  The helper draws centred value glyphs above a lower caption;
    compare those glyphs with independently constructed text in the placed cell.
    Any unreadable geometry fails closed.
    """
    try:
        _field, _value, size, font = next(
            spec for spec in title.title_field_specs if spec[0] == field
        )
        cell = title.cell_bbox(field)
        cx = title.position.X + (cell["min_x"] + cell["max_x"]) / 2
        cy = title.position.Y + (cell["min_y"] + cell["max_y"]) / 2
        expected = Text(
            txt=value,
            font_size=size,
            font_path=font,
            align=(Align.CENTER, Align.CENTER),
            mode=Mode.PRIVATE,
        ).moved(Location((cx, cy, 0)))
        # The caption occupies the lower quarter of this helper-owned cell.
        # Judge the entire remaining interior: extra glyphs need not lie in
        # the canonical value's own bounding box.
        lower = title.position.Y + cell["min_y"] + cell["height"] * 0.25
        upper = title.position.Y + cell["max_y"] - 0.1
        left = title.position.X + cell["min_x"] + 0.1
        right = title.position.X + cell["max_x"] - 0.1

        actual = []
        for face in title.faces():
            box = face.bounding_box()
            if box.max.X > left and box.min.X < right and box.max.Y > lower and box.min.Y < upper:
                actual.append(face)
        if not actual:
            return False
        finished = Sketch(children=actual)
        missing = sum(face.area for face in expected.cut(finished).faces())
        extra = sum(face.area for face in finished.cut(expected).faces())
        return missing + extra <= 1e-5
    except Exception:
        # A geometry or font failure cannot vouch for source-selected ink.
        return False


def lint_manufacturing_references(registry) -> list[LintIssue]:
    """Judge the finished ink: a short source reference needs its exact full row.

    A table can later be removed through ``drop`` or ``remove``.  The short leader
    still exists, but without the table it no longer states the requirement.
    This check reads placed annotations and their feature owners, never a planned
    table.  Source ownership alone is not proof that the required terms survived.
    """
    table = registry.named("manufacturing_requirements")
    rows = getattr(table, "table_rows", ())
    source_ids_by_tag = getattr(table, "manufacturing_source_ids", {})
    rows_by_tag: dict[str, list[str]] = {}
    if rows and rows[0] == ("REF", "MANUFACTURING REQUIREMENT"):
        current = None
        for tag, requirement in rows[1:]:
            if tag:
                current = tag
                rows_by_tag[current] = []
            if current is not None:
                rows_by_tag[current].append(requirement)
    issues = []
    for name, annotation in registry.iter_named():
        label = str(getattr(annotation, "label", ""))
        for tag in _MANUFACTURING_REF.findall(label):
            aspects = tuple(
                aspect
                for owner in registry.features_of(name)
                for subject in (getattr(owner, "member", None) or owner,)
                for aspect in (getattr(subject, "thread", None), getattr(subject, "knurl", None))
                if aspect is not None and hasattr(aspect, "source_ids") and aspect.source_ids
            )
            if not aspects:
                continue  # a user-authored note is not this engine's keyed carrier
            source_ids = tuple(
                dict.fromkeys(source_id for aspect in aspects for source_id in aspect.source_ids)
            )
            claimed_sources = set(source_ids_by_tag.get(tag, ()))
            printed = " ".join(rows_by_tag.get(tag, ()))
            complete = bool(printed and claimed_sources) and any(
                claimed_sources == set(aspect.source_ids)
                and printed == aspect.callout_text
                and _source_knurl_max_is_visible(label, printed, aspect)
                for aspect in aspects
            )
            if complete:
                continue
            issues.append(
                LintIssue(
                    severity="error",
                    code="manufacturing_reference_unresolved",
                    message=f"{name} refers to {tag}, but its complete source-matched manufacturing row is absent",
                    source_ids=source_ids,
                )
            )
    return issues


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
        if parameter and len(key) > 3:
            parameter += f".{key[3]}"
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
        for index, requirement in enumerate(getattr(feature, "member_size_requirements", ())):
            source_ids = _source_ids(requirement)
            if source_ids:
                out.append(((owner, "diameter", "bore", f"member_{index}"), source_ids))
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
        "surface_label": "surface label",
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
    when the evidence is absent rather than negative. A source-linked finish is an error
    because its face claim cannot be checked. Supply the document (``Sheet(...,
    source=…)`` or a STEP path) and the real reconciliation replaces this.
    """
    if report is not None:
        return []
    unchecked = {
        source_id
        for feature in features
        for source_id in _source_ids(feature)
        if getattr(feature, "source", "") == "ap242_pmi"
        or getattr(feature, "kind", None) == "pmi"
        or getattr(feature, "kind", None) == "finish"
    }
    for _key, source_ids in _decorated_source_features(decorations, features=features):
        unchecked.update(source_ids)
    raw = sum(1 for feature in features if getattr(feature, "kind", None) == "pmi")
    source_finish = any(
        getattr(feature, "kind", None) == "finish" and _source_ids(feature) for feature in features
    )
    if not unchecked and not raw:
        return []
    detail = f"{len(unchecked)} AP242 source reference(s)"
    if raw:
        detail += f", including {raw} raw PMI record(s) not lowered to a drafting concept"
    return [
        LintIssue(
            severity="error" if source_finish else "warning",
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
    the other way, so a declaration or source-linked finish could claim a `source_id` no
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
            and getattr(feature, "kind", None) != "finish"
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


def _lint_pmi_frame_values(report: PmiExtractionReport, registry) -> list[LintIssue]:
    """Compare surviving control-frame ink with the extracted source tolerance."""
    # Read the emitted frame's text, not its IR display hint: a formatter or renderer
    # can change the value after extraction while the source census stays correct.
    source_values = {
        record.source_id: (
            Decimal(str(record.value)),
            next(
                (
                    zone
                    for zone in ("spherical_diameter_zone", "diameter_zone")
                    if zone in record.gtol_modifiers
                ),
                "",
            ),
        )
        for record in report.records
        if record.source_category == "geometric_tolerance" and record.value > 0
    }
    issues = []
    for name, annotation in registry.iter_named():
        declaration = registry.declaration_of(name)
        if getattr(declaration, "kind", None) != "control_frame":
            continue
        source_id = getattr(declaration, "source_id", "")
        source = source_values.get(source_id)
        if source is None:
            continue
        expected, expected_zone = source
        values = []
        for spec in getattr(annotation, "pdf_text_relative_specs", ()):
            try:
                value = Decimal(str(spec[0]))
            except (InvalidOperation, IndexError, TypeError):
                continue
            if value.is_finite():
                values.append(value)
        visual_text = getattr(annotation, "gdt_visual_tolerance", "")
        visual_zone = getattr(annotation, "gdt_visual_zone", "")
        try:
            visual_value = Decimal(visual_text.removeprefix("Sø"))
        except (AttributeError, InvalidOperation, TypeError):
            visual_value = None
        # XCAF exposes a binary float without the source's lexical precision. The
        # compiler displays at most 13 significant digits, so accept only its
        # half-quantum rounding interval against the independent source record.
        display_bound = Decimal("0.5").scaleb(expected.adjusted() - 12)
        if (
            len(values) == 1
            and abs(values[0] - expected) <= display_bound
            and visual_value is not None
            and visual_value.is_finite()
            and abs(visual_value - expected) <= display_bound
            and visual_zone == expected_zone
        ):
            continue
        issues.append(
            LintIssue(
                severity="error",
                code="pmi_value_mismatch",
                message=(
                    f"{name} states PDF {values or 'no numeric value'} and visual "
                    f"{visual_text or 'no numeric value'} with zone "
                    f"{visual_zone or 'none'} for AP242 source {source_id}; "
                    f"source tolerance is {expected} with zone {expected_zone or 'none'}"
                ),
                source_ids=(source_id,),
                annotation_name=name,
            )
        )
    return issues


def _printed_document_notes(table) -> tuple[str, ...]:
    """Read numbered note text back from the placed general-notes table."""
    notes: list[str] = []
    for row in getattr(table, "table_rows", ()):
        if len(row) != 1:
            return ()
        line = row[0]
        if match := re.fullmatch(r"([1-9][0-9]*)  (.*)", line):
            if int(match.group(1)) != len(notes) + 1:
                return ()
            notes.append(match.group(2))
        elif line.startswith("   ") and notes:
            notes[-1] += " " + line.strip()
        elif line != "GENERAL NOTES":
            return ()
    return tuple(notes)


def _lint_pmi_manufacturing_ink(report: PmiExtractionReport, registry) -> list[LintIssue]:
    """Check source-scoped manufacturing text and face sites against surviving content."""
    source = {
        record.source_id: record
        for record in report.records
        if record.source_category == "manufacturing_requirement"
        and record.kind in {"edge_condition", "surface_finish"}
    }
    issues = []
    table = registry.named("general_notes")
    note_owners = (
        tuple(
            owner
            for owner in registry.features_of("general_notes")
            if getattr(owner, "kind", None) == "document_note"
        )
        if table is not None
        else ()
    )
    printed_notes = _printed_document_notes(table)
    for index, owner in enumerate(note_owners):
        source_id = getattr(owner, "source_id", "")
        record = source.get(source_id)
        if record is None:
            continue
        if (
            record.kind == "edge_condition"
            and index < len(printed_notes)
            and printed_notes[index] == record.label
        ):
            continue
        issues.append(
            LintIssue(
                severity="error",
                code="pmi_source_text_mismatch",
                message=f"general_notes does not state AP242 edge condition {source_id} verbatim",
                source_ids=(source_id,),
                annotation_name="general_notes",
            )
        )

    for name, annotation in registry.iter_named():
        declaration = registry.declaration_of(name)
        if getattr(declaration, "kind", None) != "finish":
            continue
        source_id = getattr(declaration, "source_id", "")
        record = source.get(source_id)
        if record is None:
            continue
        match = (
            re.fullmatch(r"\s*Ra\s+(\d+(?:\.\d+)?)\s*(?:um|µm|μm)\s*", record.label, re.I)
            if record.kind == "surface_finish"
            else None
        )
        expected = Decimal(match.group(1)) if match else None
        pdf_specs = tuple(getattr(annotation, "pdf_text_relative_specs", ()))
        glyph_label = getattr(annotation, "gdt_visual_finish", None)
        try:
            pdf_value = Decimal(str(pdf_specs[0][0])) if len(pdf_specs) == 1 else None
            glyph_value = Decimal(str(glyph_label))
        except (InvalidOperation, IndexError, TypeError):
            pdf_value = glyph_value = None
        if expected is None or pdf_value != expected or glyph_value != expected:
            issues.append(
                LintIssue(
                    severity="error",
                    code="pmi_source_text_mismatch",
                    message=f"{name} does not state AP242 face finish {source_id} as {record.label}",
                    source_ids=(source_id,),
                    annotation_name=name,
                )
            )
        site = (
            _cylindrical_finish_site(record.cylindrical_refs[0])
            if len(record.cylindrical_refs) == 1 and not record.lowering_blockers
            else None
        )
        origin = getattr(declaration, "origin", None)
        if (
            site is not None
            and dist(declaration.frame.origin, site[0]) <= 0.001
            and declaration.view == site[1]
            and declaration.side == site[2]
            and tuple(getattr(origin, "reference_item_ids", ())) == record.reference_item_ids
            and tuple(getattr(origin, "cylindrical_refs", ())) == record.cylindrical_refs
        ):
            continue
        issues.append(
            LintIssue(
                severity="error",
                code="pmi_source_site_mismatch",
                message=f"{name} is not anchored to the referenced face finish {source_id}",
                source_ids=(source_id,),
                annotation_name=name,
            )
        )
    return issues


def lint_pmi_rendering(
    features,
    registry,
    mode: str,
    *,
    decorations=None,
    report: PmiExtractionReport | None = None,
    overridden_general_tolerance=False,
) -> list[LintIssue]:
    """Reconcile source-bearing typed PMI with surviving annotation ink.

    ADR 5 (was 0010)'s registry is the existing annotation-to-feature provenance owner. A placement
    rejection is already a structured source-bearing ``*_dropped`` build issue (the code stays
    specific to the ordinary renderer typed PMI entered), so this reconciliation is derived
    from those two outcomes rather than maintained in a parallel ledger. Placed control
    frames are also checked against the independent extracted magnitude.
    """
    if mode != "annotate":
        return _lint_pmi_manufacturing_ink(report, registry) if report is not None else []

    features = tuple(features)
    placed_datums = {
        source_id
        for feature in features
        if getattr(feature, "kind", None) == "datum_ref"
        and _registry_names_for_feature(registry, feature)
        for source_id in _source_ids(feature)
    }
    by_source: dict[str, list[object]] = {}
    for feature in features:
        if overridden_general_tolerance and getattr(feature, "kind", None) == "general_tolerance":
            continue
        # Model-only metadata has no drawing carrier. A redundant datum-scheme
        # statement may take that path only while every proven substitute symbol
        # remains on the finished sheet; removing a datum reopens its source gap.
        if (
            getattr(feature, "kind", None) == "document_note"
            and not getattr(feature, "on_drawing", True)
            and (
                not getattr(feature, "represented_by_source_ids", ())
                or set(feature.represented_by_source_ids) <= placed_datums
            )
        ):
            continue
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
    issues = [
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
    if report is not None:
        issues.extend(_lint_pmi_frame_values(report, registry))
        issues.extend(_lint_pmi_manufacturing_ink(report, registry))
    return issues


def lint_step_title_defaults(
    report: PmiExtractionReport | None,
    registry,
    *,
    material_authored: str | None,
    tolerance_authored: str | None,
    tolerance_source_selected: bool = False,
    pmi_mode: str,
) -> list[LintIssue]:
    """Compare source document defaults with settled title-block text.

    The source census, not a compiled plan or a parsed annotation name, supplies
    the expected value and identity. An explicit empty value intentionally clears
    its cell; a nonempty caller override remains visible and gets a disagreement
    finding when it differs from the source.
    """
    if report is None:
        return []
    title = registry.named("title_block")
    fields = (
        {field: value for field, value, _size, _font in getattr(title, "title_field_specs", ())}
        if title is not None
        else {}
    )
    issues = []
    if report.material_error:
        issues.append(
            LintIssue(
                severity="warning",
                code="step_material_unavailable",
                message=f"STEP material could not be read: {report.material_error}",
            )
        )
    materials = report.material_facts
    if len(materials) > 1:
        issues.append(
            LintIssue(
                severity="warning",
                code="step_material_ambiguous",
                message="STEP contains multiple material properties; no material was selected",
                source_ids=tuple(fact.source_id for fact in materials),
            )
        )
    elif materials:
        fact = materials[0]
        if fact.reason:
            issues.append(
                LintIssue(
                    severity="warning",
                    code="step_material_unavailable",
                    message=f"STEP material {fact.source_id} is unusable: {fact.reason}",
                    source_ids=(fact.source_id,),
                )
            )
        elif (
            material_authored not in (None, "") and fields.get("material", "") != fact.designation
        ):
            issues.append(
                LintIssue(
                    severity="warning",
                    code="step_material_disagreement",
                    message=(
                        f"Title-block material {fields.get('material', '')!r} differs from "
                        f"STEP material {fact.designation!r}"
                    ),
                    source_ids=(fact.source_id,),
                )
            )
        elif material_authored is None and (
            fields.get("material", "") != fact.designation
            or not _title_value_has_finished_ink(title, "material", fact.designation)
        ):
            issues.append(
                LintIssue(
                    severity="error",
                    code="step_material_mismatch",
                    message=f"Title block does not state STEP material {fact.designation!r}",
                    source_ids=(fact.source_id,),
                )
            )
    if pmi_mode == "annotate" and (
        tolerance_source_selected or tolerance_authored not in (None, "")
    ):
        tolerances = [
            record
            for record in report.records
            if record.source_category == "manufacturing_requirement"
            and record.kind == "general_tolerances"
        ]
        if len(tolerances) == 1:
            record = tolerances[0]
            designation = record.label.split(";", 1)[0].strip()
            selected = tolerance_source_selected and tolerance_authored is None
            if designation and (
                fields.get("general_tolerance", "") != designation
                or (
                    selected
                    and not _title_value_has_finished_ink(title, "general_tolerance", designation)
                )
            ):
                issues.append(
                    LintIssue(
                        severity="error" if selected else "warning",
                        code=(
                            "step_general_tolerance_mismatch"
                            if selected
                            else "step_general_tolerance_disagreement"
                        ),
                        message=(
                            f"Title-block general tolerance {fields.get('general_tolerance', '')!r} "
                            f"differs from STEP general tolerance {designation!r}"
                        ),
                        source_ids=record.source_ids or (record.source_id,),
                    )
                )
    return issues
