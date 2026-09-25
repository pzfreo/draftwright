"""Versioned machine-readable drawing reports and generation-time gap snapshots."""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import version as distribution_version
from math import isfinite
from numbers import Real
from os import PathLike
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from draftwright.recogniser_schema import consumed_record_schema_versions_for_type

if TYPE_CHECKING:
    from quiddity.evidence import RecognitionEvidence

    from draftwright.model import PartModel
    from draftwright.profile_angles import ProfileAngle
    from draftwright.recognition_ownership import RecognitionOwnership

REPORT_SCHEMA = "draftwright-report"
REPORT_SCHEMA_VERSION = 3
_DECLARED_REPORT_SCHEMA_VERSION = 8

_LAYOUT_REMEDIES = (
    "page",
    "scale",
    "view",
    "section",
    "schedule",
    "side",
    "priority",
    "pin",
)
_LAYOUT_REMEDIES_BY_CODE = {
    "annotation_overlap": ("page", "scale", "view", "side", "priority", "pin"),
    "annotation_ink_overlap": ("page", "scale", "view", "side", "priority", "pin"),
    "label_centerline_overlap": ("view", "side", "priority", "pin"),
    "leader_line_through_text": ("view", "side", "priority", "pin"),
    "annotation_out_of_bounds": ("page", "scale", "view", "side"),
    "view_annotation_overlap": ("page", "scale", "view", "side"),
    "view_overlap": ("page", "scale", "view"),
    "view_out_of_bounds": ("page", "scale", "view"),
    "leader_crosses_silhouette": ("view", "section", "side"),
}

_REMEDIATION_DOMAINS = (
    "contradiction",
    "missing-carrier",
    "layout",
    "importer-lowering",
    "source-ambiguity",
    "evidence-unavailable",
    "unclassified",
)
_REMEDIATION_SEVERITIES = ("error", "warning", "info")
_REMEDIATION_PRESERVE = (
    "measurement-owner",
    "parameter-meaning",
    "source-provenance",
    "unrelated-requirement-outcomes",
)
_IMPORTER_LOWERING_CODES = frozenset(
    {
        "angled_step_requirement_unsupported",
        "dimension_kind_unsupported",
        "passage_requirement_unsupported",
        "pmi_not_extracted",
        "pmi_not_lowered",
        "pmi_not_rendered",
        "pmi_present_but_ignored",
        "prismatic_pocket_requirement_unsupported",
        "section_recess_requirement_unsupported",
        "unrecognised_defining_geometry",
    }
)
_SOURCE_AMBIGUITY_CODES = frozenset(
    {
        "authored_dim_degenerate",
        "nominal_rounded",
        "pmi_unreconciled",
        "section_recess_recognition_refused",
        "step_position_coincident_with_datum",
    }
)
_EVIDENCE_UNAVAILABLE_CODES = frozenset(
    {
        "authored_dim_source_unresolved",
        "claimed_representation_no_expected_value",
        "claimed_representation_unreadable",
        "gear_correspondence_unverifiable",
        "pad_footprint_not_defined",
    }
)
_MISSING_CARRIER_CODES = frozenset(
    {
        "authored_omission",
        "axial_length_missing",
        "boss_height_missing",
        "collapsed_tolerance_withheld",
        "feature_count_mismatch",
        "feature_no_centermark",
        "feature_not_dimensioned",
        "feature_not_located",
        "gear_semantics_missing",
        "missing_principal_dimension",
        "pattern_pitch_tolerance_withheld",
        "plan_incomplete",
        "pocket_not_located",
        "profiled_bore_not_dimensioned",
        "section_dropped",
        "step_dim_withheld",
        "turned_profile_not_spanned",
    }
)
_LAYOUT_RESPONSIBILITY_CODES = frozenset(
    {"gdt_sheet_fallback", "gdt_side_relaxed", "pmi_sheet_fallback"}
)


@dataclass
class _RemediationGroup:
    """Mutable aggregation only; :meth:`document` returns the closed public projection."""

    domain: str
    severity: str = "info"
    finding_ids: set[str] = field(default_factory=set)
    occurrence_ids: set[str] = field(default_factory=set)
    requirement_ids: set[str] = field(default_factory=set)
    declaration_ids: set[str] = field(default_factory=set)
    owner_ids: set[str] = field(default_factory=set)
    annotation_names: set[str] = field(default_factory=set)
    source_ids: set[str] = field(default_factory=set)
    subjects: list[dict[str, object]] = field(default_factory=list)
    repairable_finding_ids: set[str] = field(default_factory=set)

    def raise_severity(self, severity: object) -> None:
        candidate = severity if severity in _REMEDIATION_SEVERITIES else "info"
        if _REMEDIATION_SEVERITIES.index(candidate) < _REMEDIATION_SEVERITIES.index(self.severity):
            self.severity = candidate

    def document(self) -> dict[str, object]:
        repairable = sorted(self.repairable_finding_ids)
        evidence_refs = self.finding_ids | self.occurrence_ids | self.requirement_ids
        unaddressed = sorted(evidence_refs - self.repairable_finding_ids)
        actions = (
            [{"verb": "Drawing.repair", "arguments": {}, "applies_to": repairable}]
            if repairable
            else []
        )
        return {
            "domain": self.domain,
            "severity": self.severity,
            "finding_ids": sorted(self.finding_ids),
            "occurrence_ids": sorted(self.occurrence_ids),
            "requirement_ids": sorted(self.requirement_ids),
            "declaration_ids": sorted(self.declaration_ids),
            "owner_ids": sorted(self.owner_ids),
            "annotation_names": sorted(self.annotation_names),
            "source_ids": sorted(self.source_ids),
            "subjects": sorted(self.subjects, key=lambda row: str(row["evidence_ref"])),
            "supported_actions": actions,
            "unaddressed_evidence_refs": unaddressed,
            "no_supported_action_reason": (
                None
                if actions and not unaddressed
                else "no bounded public remedy is classified for this evidence"
            ),
            "preserve": list(_REMEDIATION_PRESERVE),
        }


@dataclass(frozen=True)
class RequirementSnapshot:
    """Live typed report inputs captured through the drawing's existing ledger pass.

    This is run-local evidence, not a serialized document or an independent build.
    Callers serialize edits and reads; mutable drawing objects are not frozen by it.
    """

    evidence: RecognitionEvidence
    ownership: RecognitionOwnership
    model: PartModel
    source: str | PathLike[str] | None
    registry: object
    omissions: tuple
    dimension_plan: object
    part: object
    outcomes: Mapping[str, tuple[Any, ...]]
    lint: Mapping | None = None


@dataclass(frozen=True, eq=False)
class CatalogRequirement:
    """A source-owned obligation retained before any member's ink is evaluated."""

    family: str
    source_records: tuple[object, ...]
    source_profile: object | None
    parameter_id: str | None
    requirement_count: int
    requirement_count_known: bool
    features: tuple[object, ...]
    members: tuple
    dependency_alternatives: tuple
    intrinsic_exclusion: object | None
    unsupported: bool = False
    representation_alternatives: tuple = ()
    outcome: object | None = None


@dataclass(frozen=True, eq=False)
class RequirementCatalog:
    """One run's typed denominator; these references are never persistence IDs."""

    evidence: object
    ownership: object
    features: tuple[object, ...]
    families: tuple[str, ...]
    requirements: tuple[CatalogRequirement, ...]


def build_requirement_catalog(
    *, evidence, ownership, model, part, requirement_outcomes=None
) -> RequirementCatalog:
    """Use the existing ledger producers and strict source projection before authoring."""
    from draftwright.linting.requirements import (
        REQUIREMENT_SOURCE_FAMILIES,
        recognized_requirement_outcomes,
        requirement_source_census,
    )
    from draftwright.registry import AnnotationRegistry

    evidence, ownership, model = validate_report_inputs(evidence, ownership, model)
    registry = AnnotationRegistry()
    outcomes = requirement_outcomes
    if outcomes is None:
        outcomes = recognized_requirement_outcomes(
            evidence.result,
            tuple(model.features),
            registry,
            (),
            part=part,
            evidence=evidence,
            ownership=ownership,
            datum=next((datum for datum in model.datums if datum.id == "datum_xy"), None),
        )
    if set(outcomes) != REQUIREMENT_SOURCE_FAMILIES | {"outer_profile_angles"}:
        raise ReportUnavailableError("catalog requirement family roster changed")
    # Reuse the strict projection's authority, profile-denominator, source-set and
    # unsupported-disposition checks. The typed rows below come from the producers;
    # serialized IDs, annotation rows and local coverage counts are not their input.
    project_occurrences(
        evidence,
        ownership,
        model,
        registry=registry,
        part=part,
        requirement_outcomes=outcomes,
    )
    records = tuple(evidence.record(reference) for reference in evidence.features)
    record_order = {id(record): index for index, record in enumerate(records)}
    feature_order = {id(feature): feature for feature in model.features}
    if any(
        feature_order.get(id(feature)) is not feature
        for binding in ownership.bindings
        for feature in binding.features
    ):
        raise ReportUnavailableError("catalog lost an exact conversion-time physical owner")
    entries = []
    keys = set()
    projected_records: set[int] = set()
    for family, rows in outcomes.items():
        for row in rows:
            sources_by_id = {id(record): record for record in _outcome_records(row)}
            sources = tuple(
                sorted(sources_by_id.values(), key=lambda record: record_order[id(record)])
            )
            profile = getattr(row, "source_profile", None)
            parameter = getattr(row, "catalog_parameter_id", row.parameter_id)
            count = getattr(row, "requirement_count", 1)
            known = getattr(row, "requirement_count_known", True)
            if (
                type(parameter) is not str
                or not parameter
                or type(count) is not int
                or count < 1
                or type(known) is not bool
                or (parameter != "?" and count != 1)
            ):
                raise ReportUnavailableError(
                    "invalid source-owned catalog identity or cardinality"
                )
            source_key = tuple(id(record) for record in sources)
            profile_key = (
                (id(profile.source), profile.first_index, profile.second_index)
                if profile is not None
                else None
            )
            key = (family, source_key, profile_key, parameter)
            if key in keys:
                raise ReportUnavailableError(
                    "ambiguous duplicate source-owned catalog requirement"
                )
            keys.add(key)
            projected_records.update(source_key)
            features = tuple(getattr(row, "features", ()))
            if any(feature_order.get(id(feature)) is not feature for feature in features):
                raise ReportUnavailableError("catalog requirement has a foreign physical owner")
            alternatives = tuple(getattr(row, "dependency_alternatives", ()))
            _validate_catalog_supports(alternatives, feature_order)
            representations = tuple(getattr(row, "representation_alternatives", ()))
            _validate_catalog_supports(tuple((term,) for term in representations), feature_order)
            exclusion = getattr(row, "intrinsic_exclusion", None)
            if exclusion is not None:
                for record in exclusion.source_records:
                    index = record_order.get(id(record))
                    if index is None or records[index] is not record:
                        raise ReportUnavailableError(
                            "catalog exclusion has foreign source evidence"
                        )
                datum = getattr(exclusion, "datum", None)
                if datum is not None and not any(datum is item for item in model.datums):
                    raise ReportUnavailableError("catalog exclusion has a foreign datum")
            entries.append(
                CatalogRequirement(
                    family,
                    sources,
                    profile,
                    None if parameter == "?" else parameter,
                    count,
                    known,
                    features,
                    tuple(getattr(row, "members", ())),
                    alternatives,
                    exclusion,
                    row.state == "unsupported",
                    representations,
                    row,
                )
            )
    for reference, record in requirement_source_census(evidence, ownership):
        if (
            ownership.status(reference) not in {"unsupported", "deferred", "evidence_only"}
            and id(record) not in projected_records
        ):
            raise ReportUnavailableError("recognized requirement source has no ledger outcome")
    for reference, record in zip(evidence.features, records, strict=True):
        if ownership.status(reference) == "unsupported" and id(record) not in projected_records:
            entries.append(
                CatalogRequirement(
                    evidence.family(reference),
                    (record,),
                    None,
                    None,
                    1,
                    True,
                    (),
                    (),
                    (),
                    None,
                    True,
                )
            )
    return RequirementCatalog(
        evidence, ownership, tuple(model.features), tuple(outcomes), tuple(entries)
    )


def _validate_catalog_supports(alternatives, feature_order):
    from draftwright.measurement_support import MeasurementSupport, RequirementAlternative

    for alternative in alternatives:
        if isinstance(alternative, RequirementAlternative):
            identities, terms = alternative.measurements, alternative.supports
        else:
            terms = tuple(alternative)
            if any(not isinstance(term, MeasurementSupport) for term in terms):
                raise ReportUnavailableError("catalog dependency has an invalid support type")
            identities = tuple(term.identity for term in terms)
        if not identities:
            raise ReportUnavailableError("catalog dependency has an empty conjunction")
        for feature, parameter in identities:
            if (
                feature_order.get(id(feature)) is not feature
                or type(parameter) is not str
                or not parameter
            ):
                raise ReportUnavailableError("catalog dependency has a foreign physical owner")
        for term in terms:
            if (
                not isinstance(term, MeasurementSupport)
                or feature_order.get(id(term.feature)) is not term.feature
                or not any(
                    owner is term.feature and parameter == term.parameter_id
                    for owner, parameter in identities
                )
            ):
                raise ReportUnavailableError("catalog support has a foreign or unrelated owner")
            interval = term.lo is not None or term.hi is not None
            point = term.location_point
            if (
                term.axis not in {None, "x", "y", "z"}
                or (
                    interval
                    and (
                        term.axis is None
                        or term.lo is None
                        or term.hi is None
                        or not _finite_support_number(term.lo)
                        or not _finite_support_number(term.hi)
                        or term.lo > term.hi
                    )
                )
                or (
                    point is not None
                    and (
                        term.axis is None
                        or type(point) is not tuple
                        or len(point) != 3
                        or not all(_finite_support_number(value) for value in point)
                    )
                )
            ):
                raise ReportUnavailableError("catalog support has an invalid physical witness")
        if terms:
            required = Counter((id(owner), parameter) for owner, parameter in identities)
            for (owner_id, parameter), count in required.items():
                witnesses = {
                    (term.axis, term.lo, term.hi, term.location_point)
                    for term in terms
                    if id(term.feature) == owner_id and term.parameter_id == parameter
                }
                if len(witnesses) < count:
                    raise ReportUnavailableError(
                        "catalog dependency lost a distinct measurement witness"
                    )


def _finite_support_number(value):
    try:
        return not isinstance(value, bool) and isinstance(value, Real) and isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _catalog_key(row):
    profile = row.source_profile
    return (
        row.family,
        frozenset(id(record) for record in row.source_records),
        (id(profile.source), profile.first_index, profile.second_index)
        if profile is not None
        else None,
        row.parameter_id,
    )


def _support_signature(term):
    return (
        id(term.feature),
        term.parameter_id,
        term.axis,
        term.lo,
        term.hi,
        term.location_point,
    )


def _alternative_signature(alternative):
    from draftwright.measurement_support import RequirementAlternative

    if isinstance(alternative, RequirementAlternative):
        identities, terms = alternative.measurements, alternative.supports
    else:
        terms = tuple(alternative)
        identities = tuple(term.identity for term in terms)
    return (
        frozenset(Counter((id(owner), parameter) for owner, parameter in identities).items()),
        frozenset(Counter(_support_signature(term) for term in terms).items()),
    )


def _catalog_shape(row):
    exclusion = row.intrinsic_exclusion
    return (
        row.requirement_count,
        row.requirement_count_known,
        frozenset(Counter(id(feature) for feature in row.features).items()),
        row.members,
        frozenset(_alternative_signature(recipe) for recipe in row.dependency_alternatives),
        frozenset(_support_signature(term) for term in row.representation_alternatives),
        (
            exclusion.reason_code,
            frozenset(id(record) for record in exclusion.source_records),
            id(getattr(exclusion, "datum", None)),
            getattr(exclusion, "span", None),
        )
        if exclusion is not None
        else None,
        row.unsupported,
    )


def match_requirement_catalog(
    expected: RequirementCatalog, actual: RequirementCatalog
) -> tuple[CatalogRequirement, ...]:
    """Align live outcomes to the sealed source catalog, refusing a changed denominator.

    Both catalogs retain their exact objects. Object addresses here are temporary index
    keys into those strongly held, validated references, never exported identities.
    Local evidence states and selected representations deliberately do not define shape.
    """
    if actual.evidence is not expected.evidence or actual.ownership is not expected.ownership:
        raise ReportUnavailableError("document member has a different recognition authority")
    if set(actual.families) != set(expected.families):
        raise ReportUnavailableError("document member changed the requirement family roster")
    expected_by_key = {_catalog_key(row): row for row in expected.requirements}
    actual_by_key = {_catalog_key(row): row for row in actual.requirements}
    if (
        len(expected_by_key) != len(expected.requirements)
        or len(actual_by_key) != len(actual.requirements)
        or expected_by_key.keys() != actual_by_key.keys()
    ):
        raise ReportUnavailableError("document member changed source-owned requirement identities")
    aligned = []
    for key, baseline in expected_by_key.items():
        current = actual_by_key[key]
        if _catalog_shape(current) != _catalog_shape(baseline):
            raise ReportUnavailableError("document member changed source-owned requirement shape")
        aligned.append(current)
    return tuple(aligned)


@dataclass(frozen=True)
class DocumentRequirementEvaluation:
    requirement: CatalogRequirement
    state: str
    local: tuple
    dependencies: tuple
    combined: CatalogRequirement


@dataclass(frozen=True)
class DocumentEvaluation:
    requirements: tuple[DocumentRequirementEvaluation, ...]
    claims: tuple
    conflicts: tuple
    registry: object
    annotation_refs: Mapping
    members: tuple


def evaluate_document_requirements(catalog, model, part, members, claims) -> DocumentEvaluation:
    """Read shared physical ledgers over all member ink, retaining local outcomes.

    The temporary registry holds the exact existing annotations. It does not add ink
    to a drawing, merge models, recognize geometry, or use a sheet's score as evidence.
    """
    from dataclasses import replace
    from types import SimpleNamespace

    from draftwright.document_evidence import document_conflicts, document_support_proofs
    from draftwright.linting.requirements import recognized_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    registry = AnnotationRegistry()
    annotation_refs = {}
    for index, (name, snapshot, _aligned) in enumerate(members):
        for annotation in sorted(snapshot.registry.names()):
            reference = f"{index}:{annotation}"
            annotation_refs[reference] = (name, annotation)
            registry.add(
                snapshot.registry.named(annotation),
                reference,
                snapshot.registry.view_of(annotation),
                feature=snapshot.registry.feature_of(annotation),
                measurement=snapshot.registry.identity_of(annotation)["measurement"],
                satisfaction=snapshot.registry.satisfaction_of(annotation),
                cells=tuple(
                    replace(cell, schedule=f"{index}:{cell.schedule}")
                    for cell in snapshot.registry.cells_of(annotation)
                ),
            )
    # Existing ledger verification reads each member's approved values and cells.
    # Each entry keeps its actual member compiler authority; nothing is recompiled here.
    plan = SimpleNamespace(
        **{
            field: tuple(
                item
                for _name, snapshot, _aligned in members
                for item in getattr(snapshot.dimension_plan, field, ())
            )
            for field in ("groups", "ladders", "locations", "contingencies")
        },
        schedules=tuple(
            replace(schedule, name=f"{index}:{schedule.name}")
            for index, (_name, snapshot, _aligned) in enumerate(members)
            for schedule in getattr(snapshot.dimension_plan, "schedules", ())
        ),
    )
    outcomes = recognized_requirement_outcomes(
        catalog.evidence.result,
        tuple(model.features),
        registry,
        (),
        dimension_plan=plan,
        part=part,
        evidence=catalog.evidence,
        ownership=catalog.ownership,
        datum=next((datum for datum in model.datums if datum.id == "datum_xy"), None),
    )
    combined = build_requirement_catalog(
        evidence=catalog.evidence,
        ownership=catalog.ownership,
        model=model,
        part=part,
        requirement_outcomes=outcomes,
    )
    aligned = match_requirement_catalog(catalog, combined)
    conflicts = document_conflicts(claims)
    evaluated = []
    for index, (requirement, current) in enumerate(
        zip(catalog.requirements, aligned, strict=True)
    ):
        local = tuple((name, rows[index].outcome) for name, _snapshot, rows in members)
        proofs = document_support_proofs(requirement.dependency_alternatives, claims, conflicts)
        observed = getattr(current.outcome, "state", None)
        # A local direct representation remains direct even when the union makes a
        # producer's conditional alternative applicable (notably opposite plates).
        local_states = {getattr(outcome, "state", None) for _name, outcome in local}
        if (
            requirement.unsupported
            or not requirement.requirement_count_known
            or requirement.parameter_id is None
        ):
            state = "unresolved"
        elif requirement.intrinsic_exclusion is not None:
            state = "inapplicable"
        elif "placed" in local_states and current.features:
            state = "placed"
        elif current.features and observed == "placed":
            state = "placed"
        elif current.features and (
            "satisfied_by_structured_note" in local_states
            or observed == "satisfied_by_structured_note"
        ):
            state = "satisfied_by_structured_note"
        elif proofs:
            state = "dependency-derived"
        else:
            state = "uncovered"
        evaluated.append(DocumentRequirementEvaluation(requirement, state, local, proofs, current))
    return DocumentEvaluation(
        tuple(evaluated), tuple(claims), conflicts, registry, annotation_refs, tuple(members)
    )


_DISPOSITIONS = (
    "represented",
    "absorbed",
    "unsupported",
    "deferred",
    "evidence_only",
    "unexpectedly_missing",
)
_ATTENTION_DISPOSITIONS = frozenset(
    {"unsupported", "deferred", "evidence_only", "unexpectedly_missing"}
)

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class ReportUnavailableError(RuntimeError):
    """The drawing cannot yet produce a truthful occurrence-level report."""


def json_value(value: object) -> JsonValue:
    """Return isolated strict JSON primitives, rejecting NaN/Infinity and repr fallbacks."""

    return cast(JsonValue, json.loads(json.dumps(value, allow_nan=False, sort_keys=True)))


def _record_schema_version(family: str, record: object) -> int:
    versions = consumed_record_schema_versions_for_type(type(record).__name__)
    if len(versions) != 1:
        raise ReportUnavailableError(
            f"record type {type(record).__name__!r} in family {family!r} has no unique "
            "supported schema version"
        )
    return versions[0]


def _source(source: str | PathLike[str] | None) -> dict[str, str | None]:
    if isinstance(source, (str, PathLike)):
        return {"kind": "step", "name": Path(source).name}
    return {"kind": "build123d", "name": None}


def producer() -> dict[str, str]:
    return {
        "draftwright": distribution_version("draftwright"),
        "quiddity": distribution_version("quiddity"),
    }


def _feature_ids(model: object) -> dict[int, tuple[object, dict[str, str]]]:
    """Allocate deterministic report-local IDs in final IR order, never from topology."""

    result: dict[int, tuple[object, dict[str, str]]] = {}
    counts: Counter[str] = Counter()
    for feature in getattr(model, "features", ()):
        kind = getattr(feature, "kind", None)
        if type(kind) is not str or not kind:
            raise ReportUnavailableError("drawing model contains an IR feature without a kind")
        if id(feature) in result and result[id(feature)][0] is feature:
            raise ReportUnavailableError("drawing model repeats the same IR feature object")
        counts[kind] += 1
        result[id(feature)] = (feature, {"id": f"{kind}:{counts[kind]}", "kind": kind})
    return result


_REQUIREMENT_STATES = frozenset(
    {
        "placed",
        "satisfied_by_structured_note",
        "suppressed",
        "dropped",
        "missing",
        "unverifiable",
        "inapplicable",
        "unsupported",
    }
)
_REQUIREMENT_REASON = {
    "placed": "semantic_measurement_placed",
    "satisfied_by_structured_note": "semantic_structured_note_satisfaction",
    "suppressed": "authored_requirement_suppressed",
    "dropped": "semantic_requirement_dropped",
    "missing": "semantic_requirement_missing",
    "unverifiable": "semantic_requirement_unverifiable",
    "unsupported": "consumer_semantics_unsupported",
}


def _exact_occurrence_id(
    record: object,
    occurrence_ids: dict[int, tuple[object, str]],
) -> str:
    candidate = occurrence_ids.get(id(record))
    if candidate is None or candidate[0] is not record:
        raise ReportUnavailableError(
            "a recognized requirement outcome is not bound to this evidence authority"
        )
    return candidate[1]


def _outcome_records(outcome: object) -> tuple[object, ...]:
    records = tuple(getattr(outcome, "source_records", ()))
    parameter = getattr(outcome, "parameter_id", None)
    if isinstance(parameter, str) and parameter.startswith("countersink."):
        records += tuple(
            countersink
            for record in records
            if (countersink := getattr(record, "csink", None)) is not None
        )
    return records


def _outcome_measurements(outcome: object) -> tuple[tuple[object, str], ...]:
    from draftwright.linting._registry import requirement_measurements

    return requirement_measurements(outcome)


# Private: read only by the two `_annotation_*` helpers below. A published name is a
# promise to a consumer, and this alias has none (#1469).
_AnnotationIndex = dict[tuple[int, str], tuple[object, tuple[str, ...]]]


def _annotation_index(registry: object) -> _AnnotationIndex:
    """Index exact semantic provenance once for linear report projection."""

    names = getattr(registry, "names", None)
    measurement_of = getattr(registry, "measurement_of", None)
    satisfaction_of = getattr(registry, "satisfaction_of", None)
    if not callable(names) or not callable(measurement_of) or not callable(satisfaction_of):
        raise ReportUnavailableError("annotation provenance registry is unavailable")
    registered_names = tuple(names())
    if any(type(name) is not str or not name for name in registered_names):
        raise ReportUnavailableError("annotation provenance registry contains an invalid name")
    building: dict[tuple[int, str], tuple[object, set[str]]] = {}
    for name in sorted(registered_names):
        attached = tuple(measurement_of(name)) + tuple(satisfaction_of(name))
        for identity in attached:
            feature = getattr(identity, "feature", None)
            parameter = getattr(identity, "parameter", None)
            if feature is None or type(parameter) is not str or not parameter:
                continue
            key = (id(feature), parameter)
            candidate = building.get(key)
            if candidate is None:
                building[key] = (feature, {name})
            elif candidate[0] is not feature:
                raise ReportUnavailableError("annotation provenance identity is ambiguous")
            else:
                candidate[1].add(name)
    return {key: (feature, tuple(sorted(values))) for key, (feature, values) in building.items()}


def _annotation_names(index: _AnnotationIndex, outcome: object) -> list[str]:
    result: set[str] = set()
    for feature, parameter in _outcome_measurements(outcome):
        candidate = index.get((id(feature), parameter))
        if candidate is not None and candidate[0] is feature:
            result.update(candidate[1])
    return sorted(result)


def _profile_source(evidence, profile, profile_ids, support_ids):
    source = profile.source
    try:
        if evidence.planar_outer_profile(source.face) is not source:
            raise ValueError("profile does not belong to this evidence run")
        for index in (profile.first_index, profile.second_index):
            evidence.profile_edge(source, index)
    except (AttributeError, TypeError, ValueError, IndexError) as exc:
        raise ReportUnavailableError("profile requirement has no exact issued source") from exc
    profile_id = profile_ids.setdefault(id(source), f"profile:{len(profile_ids) + 1}")
    pair_ids = [
        support_ids.setdefault((id(source), index), f"support:{len(support_ids) + 1}")
        for index in (profile.first_index, profile.second_index)
    ]
    return {"kind": "planar_outer_profile", "profile_id": profile_id, "support_ids": pair_ids}


def _requirements(
    *,
    evidence: RecognitionEvidence,
    ownership: RecognitionOwnership,
    model: PartModel,
    occurrences: list[dict[str, Any]],
    registry: object,
    omissions: tuple[object, ...],
    dimension_plan: object | None,
    part: object | None,
    requirement_outcomes: Mapping[str, tuple[Any, ...]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[str]], set[str]]:
    """Project the recognition-owned semantic denominator exactly once."""

    from draftwright.profile_angles import profile_angle_requirements

    if requirement_outcomes is None:
        from draftwright.linting.requirements import recognized_requirement_outcomes

        requirement_outcomes = recognized_requirement_outcomes(
            evidence.result,
            tuple(model.features),
            registry,
            omissions,
            dimension_plan=dimension_plan,
            part=part,
            evidence=evidence,
            ownership=ownership,
        )

    expected_profiles = Counter(
        (id(item.source), item.first_index, item.second_index)
        for item in profile_angle_requirements(evidence)
    )
    reported_profiles = Counter(
        (
            id(item.source_profile.source),
            item.source_profile.first_index,
            item.source_profile.second_index,
        )
        for item in requirement_outcomes.get("outer_profile_angles", ())
    )
    if reported_profiles != expected_profiles:
        raise ReportUnavailableError(
            "profile requirement ledger differs from the issued denominator"
        )

    occurrence_ids: dict[int, tuple[object, str]] = {}
    occurrences_by_id = {str(item["id"]): item for item in occurrences}
    occurrence_order = {str(item["id"]): index for index, item in enumerate(occurrences)}
    owner_order = {
        owner["id"]: index for index, (_feature, owner) in enumerate(_feature_ids(model).values())
    }
    for reference, projected in zip(evidence.features, occurrences, strict=True):
        record = evidence.record(reference)
        if id(record) in occurrence_ids:
            raise ReportUnavailableError("evidence repeats the same recognition record object")
        occurrence_ids[id(record)] = (record, str(projected["id"]))

    annotation_index = _annotation_index(registry)
    requirements: list[dict[str, Any]] = []
    by_occurrence: dict[str, list[str]] = {key: [] for key in occurrences_by_id}
    inapplicable_occurrences: set[str] = set()
    profile_ids: dict[int, str] = {}
    support_ids: dict[tuple[int, int], str] = {}

    for family, outcomes in requirement_outcomes.items():
        for outcome in outcomes:
            state = getattr(outcome, "state", None)
            if state not in _REQUIREMENT_STATES:
                raise ReportUnavailableError(
                    f"recognized requirement family {family!r} has invalid state {state!r}"
                )
            source_records = _outcome_records(outcome)
            profile: ProfileAngle | None = getattr(outcome, "source_profile", None)
            profile_source = None
            if profile is not None:
                if family != "outer_profile_angles" or source_records:
                    raise ReportUnavailableError(
                        "profile requirement has conflicting source kinds"
                    )
                profile_source = _profile_source(evidence, profile, profile_ids, support_ids)
            if not source_records and profile_source is None:
                raise ReportUnavailableError(
                    f"recognized requirement family {family!r} has no exact source records"
                )
            source_ids = sorted(
                {_exact_occurrence_id(record, occurrence_ids) for record in source_records},
                key=occurrence_order.__getitem__,
            )
            if state == "unsupported" and any(
                occurrences_by_id[source_id]["disposition"] != "unsupported"
                for source_id in source_ids
            ):
                raise ReportUnavailableError(
                    "unsupported requirement contradicts occurrence ownership"
                )
            if state == "inapplicable":
                inapplicable_occurrences.update(source_ids)
                continue
            count = getattr(outcome, "requirement_count", 1)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ReportUnavailableError(
                    f"recognized requirement family {family!r} has invalid cardinality"
                )
            count_known = getattr(outcome, "requirement_count_known", True)
            if type(count_known) is not bool:
                raise ReportUnavailableError(
                    f"recognized requirement family {family!r} has invalid cardinality status"
                )
            parameter = getattr(outcome, "parameter_id", None)
            if type(parameter) is not str or not parameter:
                raise ReportUnavailableError(
                    f"recognized requirement family {family!r} has invalid parameter identity"
                )
            parameter_id = parameter if parameter != "?" else None
            if parameter_id is not None and count != 1:
                raise ReportUnavailableError(
                    f"recognized requirement family {family!r} has ambiguous parameter cardinality"
                )
            owner_ids = sorted(
                {
                    str(owner["id"])
                    for occurrence_id in source_ids
                    for owner in occurrences_by_id[occurrence_id]["owners"]
                },
                key=owner_order.__getitem__,
            )
            if profile_source is not None:
                final_owners = _feature_ids(model)
                owner_ids = []
                for feature in getattr(outcome, "features", ()):
                    matched = final_owners.get(id(feature))
                    if matched is None or matched[0] is not feature:
                        raise ReportUnavailableError(
                            "profile requirement has a non-final IR owner"
                        )
                    owner_ids.append(matched[1]["id"])
                owner_ids.sort(key=owner_order.__getitem__)
            annotations = _annotation_names(annotation_index, outcome)
            representation = getattr(outcome, "representation", None)
            representation_reason = getattr(outcome, "representation_reason", None)
            if representation is not None and type(representation) is not str:
                raise ReportUnavailableError(
                    f"recognized requirement family {family!r} has invalid representation"
                )
            if representation_reason is not None and type(representation_reason) is not str:
                raise ReportUnavailableError(
                    f"recognized requirement family {family!r} has invalid representation reason"
                )
            # Unknown cardinality is represented by one aggregate sentinel row. Expanding the
            # producer's placeholder count would manufacture a physical denominator.
            projected_count = count if count_known else 1
            for _index in range(projected_count):
                requirement_id = f"requirement:{len(requirements) + 1}"
                requirements.append(
                    {
                        "id": requirement_id,
                        "family": family,
                        "occurrence_ids": source_ids,
                        "owner_ids": owner_ids,
                        "parameter_id": parameter_id,
                        "state": state,
                        "reason_code": (
                            _REQUIREMENT_REASON[state]
                            if count_known
                            else "requirement_cardinality_unknown"
                        ),
                        "annotations": annotations,
                        "representation": representation,
                        "representation_reason": representation_reason,
                        **(
                            {"profile_source": profile_source}
                            if profile_source is not None
                            else {}
                        ),
                    }
                )
                for occurrence_id in source_ids:
                    by_occurrence[occurrence_id].append(requirement_id)

    for occurrence in occurrences:
        if occurrence["disposition"] != "unsupported":
            continue
        occurrence_id = str(occurrence["id"])
        if by_occurrence[occurrence_id]:
            if any(
                row["state"] != "unsupported"
                for row in requirements
                if row["id"] in by_occurrence[occurrence_id]
            ):
                raise ReportUnavailableError(
                    "unsupported occurrence has conflicting requirement outcomes"
                )
            continue
        requirement_id = f"requirement:{len(requirements) + 1}"
        requirements.append(
            {
                "id": requirement_id,
                "family": occurrence["family"],
                "occurrence_ids": [occurrence_id],
                "owner_ids": [],
                "parameter_id": None,
                "state": "unsupported",
                "reason_code": _REQUIREMENT_REASON["unsupported"],
                "annotations": [],
                "representation": None,
                "representation_reason": None,
            }
        )
        by_occurrence[occurrence_id].append(requirement_id)
    return requirements, by_occurrence, inapplicable_occurrences


def validate_report_inputs(
    evidence: RecognitionEvidence | None,
    ownership: RecognitionOwnership | None,
    model: PartModel | None,
) -> tuple[RecognitionEvidence, RecognitionOwnership, PartModel]:
    """Refuse unavailable authority before any diagnostic work can trigger recognition."""

    if evidence is None or ownership is None or ownership.evidence is not evidence:
        raise ReportUnavailableError(
            "accepted occurrence ownership is unavailable for this drawing; "
            "raw automatic recognition is required by report schema version 3"
        )
    if model is None:
        raise ReportUnavailableError("the drawing has no final IR model")
    return evidence, ownership, model


def project_occurrences(
    evidence: RecognitionEvidence | None,
    ownership: RecognitionOwnership | None,
    model: PartModel | None,
    *,
    registry: object | None = None,
    omissions: tuple[object, ...] = (),
    dimension_plan: object | None = None,
    part: object | None = None,
    requirement_outcomes: Mapping[str, tuple[Any, ...]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    evidence, ownership, model = validate_report_inputs(evidence, ownership, model)

    feature_ids = _feature_ids(model)
    family_counts: Counter[str] = Counter()
    disposition_counts: Counter[str] = Counter()
    projected: list[dict[str, Any]] = []

    for occurrence in evidence.features:
        family = evidence.family(occurrence)
        record = evidence.record(occurrence)
        family_counts[family] += 1
        binding = ownership.binding_for(occurrence)
        policy = ownership.policy_for(occurrence)
        status = ownership.status(occurrence)
        tracking: str | None = None
        owners: list[dict[str, str]] = []

        if binding is not None:
            resolved: list[dict[str, str]] = []
            for owner in binding.features:
                candidate = feature_ids.get(id(owner))
                if candidate is None or candidate[0] is not owner:
                    resolved = []
                    status = "unexpectedly_missing"
                    reason_code = "recorded_owner_not_in_model"
                    break
                resolved.append(dict(candidate[1]))
            else:
                owners = resolved
                reason_code = binding.reason_code
        elif policy is not None:
            reason_code = policy.reason_code
            tracking = policy.tracking
        elif status == "unexpectedly_missing":
            reason_code = "supported_owner_missing"
        else:
            raise ReportUnavailableError(
                f"accepted occurrence family {family!r} has no reportable disposition"
            )

        if status not in _DISPOSITIONS:
            raise ReportUnavailableError(
                f"accepted occurrence family {family!r} has unsupported status {status!r}"
            )
        disposition_counts[status] += 1
        projected.append(
            {
                "id": f"{family}:{family_counts[family]}",
                "family": family,
                "record_type": type(record).__name__,
                "record_schema_version": _record_schema_version(family, record),
                "record": json_value(record.to_dict()),
                "disposition": status,
                "reason_code": reason_code,
                "tracking": tracking,
                "owners": owners,
                "requirements": {
                    "coverage": "not-projected",
                    "ids": [],
                },
            }
        )

    requirements: list[dict[str, Any]] = []
    if registry is not None:
        requirements, by_occurrence, inapplicable = _requirements(
            evidence=evidence,
            ownership=ownership,
            model=model,
            occurrences=projected,
            registry=registry,
            omissions=omissions,
            dimension_plan=dimension_plan,
            part=part,
            requirement_outcomes=requirement_outcomes,
        )
        for projected_occurrence in projected:
            occurrence_id = str(projected_occurrence["id"])
            requirement_ids = by_occurrence[occurrence_id]
            if requirement_ids:
                coverage = "ledger"
            elif (
                occurrence_id in inapplicable
                or projected_occurrence["disposition"] == "evidence_only"
            ):
                coverage = "not-applicable"
            elif projected_occurrence["disposition"] == "deferred":
                coverage = "deferred"
            elif projected_occurrence["disposition"] == "unexpectedly_missing":
                coverage = "unavailable"
            else:
                coverage = "not-projected"
            projected_occurrence["requirements"] = {
                "coverage": coverage,
                "ids": requirement_ids,
            }
    summary = {"total": len(projected)}
    summary.update({status: disposition_counts[status] for status in _DISPOSITIONS})
    return projected, requirements, summary


def project_feature_occurrence_ids(
    model: PartModel, occurrences: list[dict[str, Any]]
) -> tuple[tuple[str, ...], ...]:
    """Join projected occurrences to final IR positions through exact projected owners.

    The returned tuple is live, generation-run data aligned with ``model.features``. It is
    deliberately not a persistent feature map and never matches on kind, value, position, or
    ordinal resemblance.
    """

    feature_ids = _feature_ids(model)
    owner_positions = {
        owner["id"]: index
        for index, feature in enumerate(model.features)
        for candidate, owner in (feature_ids[id(feature)],)
        if candidate is feature
    }
    joined: list[list[str]] = [[] for _feature in model.features]
    seen_occurrences: set[str] = set()
    for occurrence in occurrences:
        occurrence_id = occurrence.get("id")
        owners = occurrence.get("owners")
        if type(occurrence_id) is not str or not occurrence_id or not isinstance(owners, list):
            raise ReportUnavailableError("projected occurrence has invalid owner identity")
        if occurrence_id in seen_occurrences:
            raise ReportUnavailableError("projected occurrence identity is repeated")
        seen_occurrences.add(occurrence_id)
        for owner in owners:
            owner_id = owner.get("id") if isinstance(owner, dict) else None
            if type(owner_id) is not str:
                raise ReportUnavailableError("projected occurrence has invalid final owner")
            position = owner_positions.get(owner_id)
            if position is None:
                raise ReportUnavailableError("projected occurrence names an unknown final owner")
            joined[position].append(occurrence_id)
    return tuple(tuple(ids) for ids in joined)


_SATISFIED_REQUIREMENT_STATES = frozenset({"placed", "satisfied_by_structured_note"})
_UNRESOLVED_COVERAGE = frozenset({"not-projected", "deferred", "unavailable"})


def _status_reason(
    *,
    code: str,
    axis: str,
    evidence_refs: list[str],
    affected_count: int,
    denominator: int,
) -> dict[str, object]:
    """Return one deterministic report-status reason without manufacturing a score."""

    return {
        "code": code,
        "axis": axis,
        "affected_count": affected_count,
        "denominator": denominator,
        "evidence_refs": sorted(evidence_refs)[:5],
    }


def _axis_summary(
    *,
    affected_count: int,
    denominator: int | None,
    available: bool = True,
    unavailable_reason: str | None = None,
    denominator_reason: str | None = None,
) -> dict[str, object]:
    """Project one independently interpretable axis for an agent-facing summary."""

    return {
        "status": (
            "unavailable"
            if not available
            else "affected"
            if affected_count
            else "clear-within-assessed-scope"
        ),
        "affected_count": affected_count,
        "denominator": denominator,
        "denominator_reason": denominator_reason,
        "unavailable_reasons": [unavailable_reason] if unavailable_reason else [],
    }


def _string_values(value: object) -> tuple[str, ...]:
    """Return only non-empty string identities from an open report field."""

    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _lint_remediation_domain(
    row: Mapping[str, object], *, unclassified_codes: frozenset[str]
) -> str:
    """Classify one serialized finding without reading prose or inventing a remedy."""

    from types import SimpleNamespace

    from draftwright.linting.quality import _is_fidelity_issue, _is_legibility_issue

    code = row.get("code")
    if not isinstance(code, str) or not code:
        return "unclassified"
    if code in unclassified_codes:
        return "unclassified"
    if code in _IMPORTER_LOWERING_CODES:
        return "importer-lowering"
    if code in _SOURCE_AMBIGUITY_CODES:
        return "source-ambiguity"
    if code in _LAYOUT_RESPONSIBILITY_CODES:
        return "layout"
    if code.endswith("_unsupported"):
        return "importer-lowering"
    issue = SimpleNamespace(code=code, outcome_stage=row.get("outcome_stage"))
    if _is_fidelity_issue(issue):
        return "contradiction"
    if _is_legibility_issue(issue):
        return "layout"
    if code in _EVIDENCE_UNAVAILABLE_CODES or code.endswith("_unverifiable"):
        return "evidence-unavailable"
    if (
        code in _MISSING_CARRIER_CODES
        or code.endswith("_missing")
        or code.endswith("_suppressed")
        or code.endswith("_withheld")
        or code.endswith("_dropped")
    ):
        return "missing-carrier"
    return "unclassified"


def _requirement_remediation_domain(row: Mapping[str, object]) -> str | None:
    state = row.get("state")
    if not isinstance(state, str):
        return None
    return {
        "dropped": "layout",
        "suppressed": "missing-carrier",
        "missing": "missing-carrier",
        "unsupported": "importer-lowering",
        "unverifiable": "evidence-unavailable",
    }.get(state)


def _occurrence_remediation_domain(row: Mapping[str, object]) -> str | None:
    disposition = row.get("disposition")
    if not isinstance(disposition, str):
        return None
    return {
        "unsupported": "importer-lowering",
        "deferred": "source-ambiguity",
        "evidence_only": "evidence-unavailable",
        "unexpectedly_missing": "missing-carrier",
    }.get(disposition)


def _remediation_projection(
    *,
    lint: Mapping[str, object],
    occurrences: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
) -> dict[str, object]:
    """Group actionable responsibility using only typed report evidence.

    The ordering is policy, not a score. Public actions are a narrow allowlist backed by an
    existing capability; absent capability stays explicit instead of becoming generated advice.
    """

    groups: dict[str, _RemediationGroup] = {}

    def group(domain: str) -> _RemediationGroup:
        return groups.setdefault(domain, _RemediationGroup(domain))

    issue_rows = lint.get("issues", [])
    quality = lint.get("quality", {})
    unscored = quality.get("unscored", {}) if isinstance(quality, Mapping) else {}
    unclassified_codes = frozenset(
        _string_values(unscored.get("unclassified")) if isinstance(unscored, Mapping) else ()
    )
    for index, value in enumerate(issue_rows if isinstance(issue_rows, list) else []):
        if not isinstance(value, Mapping):
            continue
        lint_domain = _lint_remediation_domain(value, unclassified_codes=unclassified_codes)
        target = group(lint_domain)
        finding_id = f"lint:{index}"
        target.finding_ids.add(finding_id)
        target.raise_severity(value.get("severity"))
        target.declaration_ids.update(_string_values(value.get("declaration_ids")))
        target.owner_ids.update(_string_values(value.get("owner_ids")))
        target.source_ids.update(_string_values(value.get("source_ids")))
        names = (
            *(
                (value.get("annotation_name"),)
                if isinstance(value.get("annotation_name"), str)
                else ()
            ),
            *_string_values(value.get("related_annotation_names")),
        )
        target.annotation_names.update(names)
        if lint_domain == "layout" and value.get("code") == "annotation_ink_overlap":
            target.repairable_finding_ids.add(finding_id)

    for row in requirements:
        requirement_domain = _requirement_remediation_domain(row)
        requirement_id = row.get("id")
        if requirement_domain is None or not isinstance(requirement_id, str) or not requirement_id:
            continue
        target = group(requirement_domain)
        target.requirement_ids.add(requirement_id)
        target.owner_ids.update(_string_values(row.get("owner_ids")))
        target.declaration_ids.update(_string_values(row.get("declaration_ids")))
        target.raise_severity("warning")
        target.subjects.append(
            {
                "evidence_ref": requirement_id,
                "kind": "requirement",
                "family": row.get("family"),
                "parameter_id": row.get("parameter_id"),
                "reason_code": row.get("reason_code"),
            }
        )

    for row in occurrences:
        occurrence_domain = _occurrence_remediation_domain(row)
        occurrence_id = row.get("id")
        if occurrence_domain is None or not isinstance(occurrence_id, str) or not occurrence_id:
            continue
        target = group(occurrence_domain)
        target.occurrence_ids.add(occurrence_id)
        owners = row.get("owners", [])
        if isinstance(owners, list):
            target.owner_ids.update(
                owner["id"]
                for owner in owners
                if isinstance(owner, Mapping) and isinstance(owner.get("id"), str)
            )
        target.raise_severity("warning")
        target.subjects.append(
            {
                "evidence_ref": occurrence_id,
                "kind": "occurrence",
                "family": row.get("family"),
                "parameter_id": None,
                "reason_code": row.get("reason_code"),
            }
        )

    items = [target.document() for target in groups.values()]

    items.sort(
        key=lambda row: (
            _REMEDIATION_SEVERITIES.index(str(row["severity"])),
            _REMEDIATION_DOMAINS.index(str(row["domain"])),
            tuple(cast(list[str], row["finding_ids"])),
            tuple(cast(list[str], row["requirement_ids"])),
            tuple(cast(list[str], row["occurrence_ids"])),
        )
    )
    return {
        "ordering": "severity(error,warning,info)-then-domain-then-evidence-id",
        "items": items,
        "unclassified_finding_ids": sorted(
            finding
            for row in items
            if row["domain"] == "unclassified"
            for finding in cast(list[str], row["finding_ids"])
        ),
    }


def _raw_report_assessment(
    *,
    lint: Mapping[str, object],
    occurrences: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
) -> dict[str, object]:
    """Explain the raw-report verdict using a vector and the evidence that selected it.

    Numeric lint scores are deliberately not read. The legacy aliases remain in the surrounding
    lint payload for compatibility, but cannot affect this status, its reasons, or its prose.
    """

    reasons: list[dict[str, object]] = []
    issues = lint.get("issues", [])
    issue_rows = issues if isinstance(issues, list) else []
    error_ids = sorted(
        {
            str(row.get("code"))
            for row in issue_rows
            if isinstance(row, Mapping)
            and row.get("severity") == "error"
            and isinstance(row.get("code"), str)
        }
    )
    raw_error_count = lint.get("errors", 0)
    error_count = raw_error_count if type(raw_error_count) is int else 0
    lint_affected_count = 0
    if not bool(lint.get("passed")):
        lint_affected_count = max(1, error_count)
        reasons.append(
            _status_reason(
                code="lint_failed",
                axis="lint",
                evidence_refs=[f"lint-code:{code}" for code in error_ids],
                affected_count=lint_affected_count,
                denominator=max(len(issue_rows), lint_affected_count),
            )
        )

    disposition_rows = [
        row for row in occurrences if row["disposition"] in _ATTENTION_DISPOSITIONS
    ]
    if disposition_rows:
        reasons.append(
            _status_reason(
                code="attention_disposition",
                axis="recognition",
                evidence_refs=[str(row["id"]) for row in disposition_rows],
                affected_count=len(disposition_rows),
                denominator=len(occurrences),
            )
        )

    adverse_requirements = [
        row for row in requirements if row["state"] not in _SATISFIED_REQUIREMENT_STATES
    ]
    if adverse_requirements:
        reasons.append(
            _status_reason(
                code="requirement_not_satisfied",
                axis="requirements",
                evidence_refs=[str(row["id"]) for row in adverse_requirements],
                affected_count=len(adverse_requirements),
                denominator=len(requirements),
            )
        )

    unknown_cardinality = [
        row for row in requirements if row["reason_code"] == "requirement_cardinality_unknown"
    ]
    if unknown_cardinality:
        reasons.append(
            _status_reason(
                code="requirement_cardinality_unknown",
                axis="completeness",
                evidence_refs=[str(row["id"]) for row in unknown_cardinality],
                affected_count=len(unknown_cardinality),
                denominator=len(requirements),
            )
        )

    unresolved_coverage = [
        row for row in occurrences if row["requirements"]["coverage"] in _UNRESOLVED_COVERAGE
    ]
    if unresolved_coverage:
        reasons.append(
            _status_reason(
                code="occurrence_coverage_unresolved",
                axis="recognition",
                evidence_refs=[str(row["id"]) for row in unresolved_coverage],
                affected_count=len(unresolved_coverage),
                denominator=len(occurrences),
            )
        )

    quality = lint.get("quality", {})
    quality_rows = quality if isinstance(quality, Mapping) else {}

    def observed_axis(name: str) -> dict[str, object]:
        candidate = quality_rows.get(name, {})
        component = candidate if isinstance(candidate, Mapping) else {}
        available = bool(component.get("available"))
        reason = component.get("reason")
        return _axis_summary(
            affected_count=int(component.get("raw_issues", 0) or 0),
            denominator=None,
            available=available,
            unavailable_reason=(
                str(reason)
                if not available and reason
                else "this axis has no available evidence"
                if not available
                else None
            ),
            denominator_reason="no fixed opportunity denominator is reported for finding-based axes",
        )

    requirement_ids = {
        str(row["id"]) for row in adverse_requirements if isinstance(row.get("id"), str)
    }
    recognition_ids = {
        str(row["id"])
        for row in (*disposition_rows, *unresolved_coverage)
        if isinstance(row.get("id"), str)
    }
    completeness = quality_rows.get("completeness", {})
    completeness_row = completeness if isinstance(completeness, Mapping) else {}
    completeness_available = bool(completeness_row.get("available"))
    adverse_completeness = sum(
        int(completeness_row.get(state, 0) or 0)
        for state in ("suppressed", "dropped", "missing", "unverifiable", "unsupported")
    )
    envelope_axes = completeness_row.get("envelope_axes", {})
    envelope_row = envelope_axes if isinstance(envelope_axes, Mapping) else {}
    envelope_affected = int(envelope_row.get("affected_count", 0) or 0)
    envelope_requirements = int(envelope_row.get("requirements", 0) or 0)
    adverse_completeness += envelope_affected
    known_requirement_count = completeness_row.get("known_requirement_count")
    completeness_denominator = (
        int(known_requirement_count) + envelope_requirements
        if completeness_available and isinstance(known_requirement_count, int)
        else None
    )
    restraint = quality_rows.get("restraint", {})
    restraint_row = restraint if isinstance(restraint, Mapping) else {}
    axes = {
        "recognition": _axis_summary(
            affected_count=len(recognition_ids), denominator=len(occurrences)
        ),
        "requirements": _axis_summary(
            affected_count=len(requirement_ids),
            denominator=len(requirements) if requirements else None,
            available=bool(requirements),
            unavailable_reason=(
                None if requirements else "no auditable physical requirements were projected"
            ),
            denominator_reason=(
                None if requirements else "no physical requirement denominator is available"
            ),
        ),
        "completeness": _axis_summary(
            affected_count=adverse_completeness,
            denominator=completeness_denominator,
            available=completeness_available,
            unavailable_reason=(
                None
                if completeness_available
                else str(
                    completeness_row.get(
                        "reason", "audited requirement completeness is unavailable"
                    )
                )
            ),
            denominator_reason=(
                None
                if completeness_denominator is not None
                else "a complete audited requirement denominator is unavailable"
            ),
        ),
        "fidelity": observed_axis("fidelity"),
        "legibility": observed_axis("legibility"),
        "restraint": _axis_summary(
            affected_count=0,
            denominator=None,
            available=bool(restraint_row.get("available")),
            unavailable_reason=(
                str(restraint_row.get("reason"))
                if not restraint_row.get("available") and restraint_row.get("reason")
                else "this axis has no available evidence"
                if not restraint_row.get("available")
                else None
            ),
            denominator_reason="no evidence-backed redundancy denominator is available",
        ),
        "lint": _axis_summary(
            affected_count=lint_affected_count,
            denominator=max(len(issue_rows), lint_affected_count),
        ),
    }
    status = "needs-attention" if reasons else "bounded-clear"
    if reasons:
        text = f"{status}: " + "; ".join(
            f"{reason['axis']} {reason['code']} "
            f"({reason['affected_count']}/{reason['denominator']})"
            for reason in reasons
        )
    else:
        text = "bounded-clear: no assessed status predicate requires attention"
    return {
        "basis": "evidence-vector-no-scalar",
        "status": status,
        "axes": axes,
        "status_reasons": reasons,
        "remediation": _remediation_projection(
            lint=lint, occurrences=occurrences, requirements=requirements
        ),
        "summary": text,
    }


def drawing_report(
    *,
    evidence: RecognitionEvidence | None,
    ownership: RecognitionOwnership | None,
    model: PartModel | None,
    lint: dict[str, object],
    source: str | PathLike[str] | None,
    registry: object | None = None,
    omissions: tuple[object, ...] = (),
    dimension_plan: object | None = None,
    part: object | None = None,
    requirement_outcomes: Mapping[str, tuple[Any, ...]] | None = None,
) -> dict[str, object]:
    """Build the strict schema-v3 report for one raw automatic drawing.

    ``bounded-clear`` means only that this report found no known occurrence, semantic
    requirement, or lint blocker. It is deliberately not manufacturing readiness: recognition
    can miss physical geometry and manufacturing intent remains separately authored.
    """

    occurrences, requirements, summary = project_occurrences(
        evidence,
        ownership,
        model,
        registry=registry,
        omissions=omissions,
        dimension_plan=dimension_plan,
        part=part,
        requirement_outcomes=requirement_outcomes,
    )
    lint = cast(dict[str, object], json_value(lint))
    assessment = _raw_report_assessment(
        lint=lint,
        occurrences=occurrences,
        requirements=requirements,
    )
    # ``lint`` is the intentionally open producer payload in report v3. Keep the closed
    # occurrence/requirement identity contract unchanged while making the first machine-facing
    # summary a vector rather than the legacy severity scalar (#1609, #1618).
    lint = {"assessment": assessment, **lint}
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": assessment["status"],
        "producer": producer(),
        "source": _source(source),
        "outputs": {},
        "recognition": {
            "coverage": "accepted-occurrences-and-profile-requirements",
            "identity_scope": "report-local",
            "occurrences": occurrences,
            "owners": [owner for _feature, owner in _feature_ids(model).values()],
            "requirements": requirements,
            "summary": summary,
        },
        "lint": lint,
    }


def _declared_representations(model: PartModel, registry: object | None) -> tuple[dict, ...]:
    """Project exact final annotation provenance without feature equality or name guessing."""

    if registry is None:
        return ()
    names = getattr(registry, "names", None)
    measurement_of = getattr(registry, "measurement_of", None)
    satisfaction_of = getattr(registry, "satisfaction_of", None)
    view_of = getattr(registry, "view_of", None)
    named = getattr(registry, "named", None)
    feature_of = getattr(registry, "feature_of", None)
    declaration_of = getattr(registry, "declaration_of", None)
    is_pinned = getattr(registry, "is_pinned", None)
    if not all(
        callable(value)
        for value in (
            names,
            measurement_of,
            satisfaction_of,
            view_of,
            named,
            feature_of,
            declaration_of,
            is_pinned,
        )
    ):
        raise ReportUnavailableError("annotation provenance registry is unavailable")
    names = cast(Any, names)
    measurement_of = cast(Any, measurement_of)
    satisfaction_of = cast(Any, satisfaction_of)
    view_of = cast(Any, view_of)
    named = cast(Any, named)
    feature_of = cast(Any, feature_of)
    declaration_of = cast(Any, declaration_of)
    is_pinned = cast(Any, is_pinned)
    features = {id(feature): feature for feature in model.features}
    rows: list[dict] = []
    for name in sorted(names()):
        measured: dict[int, set[str]] = {}
        satisfied: dict[int, set[str]] = {}
        represented: set[int] = set()
        for primary in (feature_of(name), declaration_of(name)):
            if primary is not None and features.get(id(primary)) is primary:
                represented.add(id(primary))
        for channel, target in (
            (measurement_of(name), measured),
            (satisfaction_of(name), satisfied),
        ):
            for identity in channel:
                feature = getattr(identity, "feature", None)
                parameter = getattr(identity, "parameter", None)
                if (
                    feature is None
                    or features.get(id(feature)) is not feature
                    or type(parameter) is not str
                ):
                    continue
                target.setdefault(id(feature), set()).add(parameter)
                represented.add(id(feature))
        for feature_id in sorted(
            represented,
            key=lambda candidate: next(
                index
                for index, feature in enumerate(model.features)
                if id(feature) == candidate and feature is features[candidate]
            ),
        ):
            rows.append(
                {
                    "name": name,
                    "type": type(named(name)).__name__,
                    "view": view_of(name),
                    "pinned": bool(is_pinned(name)),
                    "feature_object_id": feature_id,
                    "measurements": sorted(measured.get(feature_id, ())),
                    "satisfactions": sorted(satisfied.get(feature_id, ())),
                }
            )
    return tuple(rows)


def _layout_semantics(
    representations: tuple[dict, ...],
    authority: dict[int, tuple[str, str | None]],
) -> dict[str, dict[str, Any]]:
    """Join each live annotation name to report-local final-IR/declaration authority."""

    joined: dict[str, dict[str, set[str]]] = {}
    for representation in representations:
        owner_id, declaration_id = authority[representation["feature_object_id"]]
        row = joined.setdefault(
            representation["name"],
            {
                "owner_ids": set(),
                "declaration_ids": set(),
                "parameters": set(),
            },
        )
        row["owner_ids"].add(owner_id)
        if declaration_id is not None:
            row["declaration_ids"].add(declaration_id)
        row["parameters"].update(representation["measurements"])
        row["parameters"].update(representation["satisfactions"])
    return {
        name: {
            "availability": "available",
            "owner_ids": sorted(row["owner_ids"]),
            "declaration_ids": sorted(row["declaration_ids"]),
            "parameters": sorted(row["parameters"]),
        }
        for name, row in joined.items()
    }


def _unavailable_semantics(reason: str) -> dict[str, Any]:
    return {
        "availability": "unavailable",
        "owner_ids": [],
        "declaration_ids": [],
        "parameters": [],
        "reason": reason,
    }


def _trace_binding_semantics(
    feature,
    measurement,
    declaration,
    authority: dict[int, tuple[str, str | None]],
) -> dict[str, object]:
    """Translate opaque trace objects to report-local semantic IDs, never object reprs."""

    from draftwright.model.compiled import resolve_feature

    owners: set[str] = set()
    declarations: set[str] = set()
    parameters: set[str] = set()
    candidates = [resolve_feature(feature), resolve_feature(declaration)]
    measurements = measurement if isinstance(measurement, (tuple, list)) else (measurement,)
    for item in measurements:
        if item is None:
            continue
        candidates.append(resolve_feature(getattr(item, "feature", None)))
        parameter = getattr(item, "parameter", None)
        if type(parameter) is str:
            parameters.add(parameter)
    for candidate in candidates:
        if candidate is None:
            continue
        linked = authority.get(id(candidate))
        if linked is None:
            continue
        owner_id, declaration_id = linked
        owners.add(owner_id)
        if declaration_id is not None:
            declarations.add(declaration_id)
    if not owners and not declarations and not parameters:
        return _unavailable_semantics(
            "this placement route did not retain final-IR or declaration provenance"
        )
    return {
        "availability": "available",
        "owner_ids": sorted(owners),
        "declaration_ids": sorted(declarations),
        "parameters": sorted(parameters),
    }


def _placement_evidence(trace, authority, annotations) -> dict[str, object]:
    """Project the opt-in solve trace, enriched without changing its standalone v2 schema."""

    if trace is None:
        return {
            "availability": "unavailable",
            "reason": "solve tracing was not enabled for this build",
        }
    status = "available" if getattr(trace, "recording_complete", False) else "partial"
    binding_reader = getattr(trace, "candidate_bindings", None)
    if not callable(binding_reader):
        bindings = {}
        status = "partial"
    else:
        bindings = {
            (seq, name): _trace_binding_semantics(feature, measurement, declaration, authority)
            for seq, name, feature, measurement, declaration in binding_reader()
        }
    document = cast(
        dict[str, Any],
        json_value(
            {
                "version": 2,
                "solves": trace.solves,
                "pass_events": trace.pass_events,
                "escalations": trace.escalations,
            }
        ),
    )
    for solve in document["solves"]:
        seq = solve["seq"]
        for collection in (solve["candidates"], solve["outcomes"]):
            for item in collection:
                item["semantic"] = bindings.get(
                    (seq, item["name"]),
                    _unavailable_semantics(
                        "this candidate has no retained final-IR/declaration binding"
                    ),
                )
                if item.get("outcome") == "dropped":
                    item["remedies"] = list(_LAYOUT_REMEDIES)
    for event in document["pass_events"]:
        for item in event.get("items", []):
            item["semantic"] = annotations.get(
                item.get("name"),
                _unavailable_semantics(
                    "this non-corridor route did not retain semantic provenance"
                ),
            )
            if item.get("outcome") == "dropped":
                item["remedies"] = list(_LAYOUT_REMEDIES)
    return {
        "availability": status,
        **(
            {"reason": "trace recording stopped after an internal recorder failure"}
            if status == "partial"
            else {}
        ),
        **document,
    }


def _declared_layout(
    drawing,
    model: PartModel,
    lint: dict[str, object],
    registry: Any,
    feature_ids: dict[int, tuple[object, dict]],
    identities: tuple,
    representations: tuple[dict, ...],
) -> dict[str, object]:
    """Project final page ink and placement evidence without creating an edit surface."""

    from draftwright.linting.ink_overlap import segments_of
    from draftwright.linting.structural import annotation_bounds

    authority: dict[int, tuple[str, str | None]] = {}
    for index, feature in enumerate(model.features):
        identity = identities[index] if identities else None
        authority[id(feature)] = (
            feature_ids[id(feature)][1]["id"],
            None if identity is None else identity.declaration_id,
        )
    annotations = _layout_semantics(representations, authority)
    rows = []
    for name in sorted(registry.names()):
        annotation = registry.named(name)
        ink = annotation_bounds(annotation, getattr(drawing, "box_cache", {}))
        try:
            label = getattr(annotation, "label_bbox", None)
        except Exception:  # noqa: BLE001 - a duck-typed annotation may reject the read
            label = None
        semantic = annotations.get(
            name,
            _unavailable_semantics(
                "the named ink has no exact final-IR/declaration correspondence"
            ),
        )
        rows.append(
            {
                "name": name,
                "type": type(annotation).__name__,
                "view": registry.view_of(name),
                "semantic": semantic,
                "ink_bounds": (
                    {"availability": "available", "value": list(ink)}
                    if ink is not None
                    else {
                        "availability": "unavailable",
                        "reason": "the annotation did not expose measurable full-ink bounds",
                    }
                ),
                "label_bounds": (
                    {"availability": "available", "value": list(label)}
                    if isinstance(label, (tuple, list)) and len(label) == 4
                    else {
                        "availability": "unavailable",
                        "reason": "the annotation did not expose a measurable label box",
                    }
                ),
                "segments": [[list(start), list(end)] for start, end in segments_of(annotation)],
                "segments_scope": "public-2d-linework-only",
            }
        )
    findings = []
    for index, issue in enumerate(cast(list[dict], lint.get("issues", []))):
        names = tuple(
            dict.fromkeys(
                name
                for name in (
                    issue.get("annotation_name"),
                    *issue.get("related_annotation_names", ()),
                )
                if isinstance(name, str)
            )
        )
        linked = [annotations[name] for name in names if name in annotations]
        findings.append(
            {
                "lint_issue_index": index,
                "code": issue.get("code", ""),
                "annotation_names": list(names),
                "declaration_ids": sorted(
                    {item for row in linked for item in row["declaration_ids"]}
                ),
                "owner_ids": sorted({item for row in linked for item in row["owner_ids"]}),
                "remedies": list(
                    _LAYOUT_REMEDIES
                    if str(issue.get("code", "")).endswith("_dropped")
                    else _LAYOUT_REMEDIES_BY_CODE.get(str(issue.get("code", "")), ())
                ),
            }
        )
    identity_index = {
        identity.declaration_id: index
        for index, identity in enumerate(identities)
        if identity is not None
    }
    overrides = []
    for override in model.layout_overrides:
        feature_index = identity_index.get(override.declaration_id)
        if feature_index is None:
            raise ReportUnavailableError(
                f"layout override {override.declaration_id!r} has no final declaration"
            )
        feature = model.features[feature_index]
        if override.side is not None:
            resolved = getattr(feature, "side", None)
            if resolved != override.side:
                raise ReportUnavailableError(
                    f"layout override {override.declaration_id!r} did not resolve to its recorded side"
                )
            row = {
                "declaration_id": override.declaration_id,
                "control": "side",
                "authored_value": override.side,
                "resolved_value": resolved,
                "intent_class": "layout-only",
                "status": "applied",
            }
        else:
            matches = [
                request
                for request in (*model.requested_dimensions, *(model.authored_dimensions or ()))
                if request.feature is feature and request.role == override.parameter_id
            ]
            if len(matches) != 1 or matches[0].lane != override.lane:
                raise ReportUnavailableError(
                    f"layout override {override.declaration_id!r} did not resolve lane "
                    f"for {override.parameter_id!r}"
                )
            row = {
                "declaration_id": override.declaration_id,
                "parameter_id": override.parameter_id,
                "control": "lane",
                "authored_value": override.lane,
                "resolved_value": matches[0].lane,
                "intent_class": "layout-only",
                "status": "applied",
            }
        overrides.append(row)
    page_w, page_h = float(drawing.page_w), float(drawing.page_h)
    left, bottom, right, top = drawing.drawable_bounds
    result = {
        "availability": "available",
        "coordinate_space": "page-mm-from-sheet-origin",
        "edit_surface": "semantic-dsl-only",
        "overrides": overrides,
        "remedy_vocabulary": list(_LAYOUT_REMEDIES),
        "page": {
            "width": page_w,
            "height": page_h,
            "scale": float(drawing.scale),
            "drawable_bounds": [left, bottom, right, top],
            "margins": [left, bottom, page_w - right, page_h - top],
        },
        "views": [
            {
                "name": name,
                "bounds": list(drawing.view_bounds(name)),
            }
            for name in sorted(drawing.views)
        ],
        "decisions": {
            "scale": drawing.scale_decision,
            "views": drawing.view_decision,
            "arrangement": drawing.arrangement_decision,
            "section": drawing.section_decision,
            "detail": {
                "availability": "unavailable",
                "reason": "detail-request outcomes are not retained after placement",
            },
            "tables": {
                "availability": "unavailable",
                "reason": "table-placement decisions are not retained as a decision ledger",
            },
        },
        "annotations": rows,
        "findings": findings,
        "placement": _placement_evidence(drawing.solve_trace, authority, annotations),
    }
    return cast(dict[str, object], json_value(result))


def declared_drawing_report(
    *,
    model: PartModel | None,
    lint: dict[str, object],
    source: str | PathLike[str] | None,
    registry: object | None = None,
    drawing: object | None = None,
) -> dict[str, object]:
    """Build the declared-sheet report from final IR and exact annotation provenance."""

    if model is None:
        raise ReportUnavailableError("the declared drawing has no final IR model")
    by_kind: Counter[str] = Counter()
    for feature in model.features:
        kind = getattr(feature, "kind", None)
        if not isinstance(kind, str) or not kind:
            raise ReportUnavailableError("declared drawing contains an IR feature without a kind")
        by_kind[kind] += 1
    feature_ids = _feature_ids(model)
    identities = tuple(getattr(model, "declaration_identities", ()))
    if identities and len(identities) != len(model.features):
        raise ReportUnavailableError("declaration identity inventory is not aligned with final IR")
    representations = _declared_representations(model, registry)
    by_feature: dict[int, list[dict]] = {}
    for row in representations:
        feature_object_id = row["feature_object_id"]
        by_feature.setdefault(feature_object_id, []).append(
            {key: value for key, value in row.items() if key != "feature_object_id"}
        )
    entries = []
    unidentified_owner_ids = []
    for index, feature in enumerate(model.features):
        owner = feature_ids[id(feature)][1]
        identity = identities[index] if identities else None
        if identity is None:
            unidentified_owner_ids.append(owner["id"])
            continue
        occurrence_ids = list(identity.occurrence_ids)
        entries.append(
            {
                "id": identity.declaration_id,
                "selector": {
                    "verb": "sheet.by_declaration",
                    "argument": identity.declaration_id,
                },
                "owner": dict(owner),
                "feature_kind": owner["kind"],
                "parameters": [parameter.parameter_id for parameter in feature.parameters()],
                "provenance": identity.provenance,
                "recognition": {
                    "correspondence": (
                        "generation-run-reference" if occurrence_ids else "unavailable"
                    ),
                    "identity_scope": "generation-run-local" if occurrence_ids else None,
                    "occurrence_ids": occurrence_ids,
                    "reason": (
                        "corroborate against the exact generation sidecar before relying on "
                        "these run-local references"
                        if occurrence_ids
                        else "no recognition occurrence correspondence was declared"
                    ),
                },
                "representations": by_feature.get(id(feature), []),
            }
        )
    lint = cast(dict[str, object], json_value(lint))
    quality = cast(dict[str, Any], lint.get("quality", {}))
    completeness = cast(dict[str, Any], quality.get("completeness", {}))
    attention = (
        not bool(lint.get("passed"))
        or bool(lint.get("warnings"))
        or completeness.get("coverage") in {"unavailable", "indeterminate"}
        or completeness.get("audited_score") is None
    )
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": _DECLARED_REPORT_SCHEMA_VERSION,
        "scope": "declared-sheet",
        "status": "needs-attention" if attention else "bounded-clear",
        "producer": producer(),
        "source": _source(source),
        "outputs": {},
        "declarations": {
            "authority": "final-ir",
            "feature_count": sum(by_kind.values()),
            "by_kind": dict(sorted(by_kind.items())),
            "identity_scope": "build-local",
            "entries": entries,
            "unidentified_owner_ids": unidentified_owner_ids,
            "recognition_correspondence": (
                "generation-run-references"
                if any(entry["recognition"]["occurrence_ids"] for entry in entries)
                else "unavailable"
            ),
            "reason": (
                "declared occurrence references are generation-run-local claims and require "
                "the exact sidecar for corroboration; no correspondence was inferred on replay"
                if any(entry["recognition"]["occurrence_ids"] for entry in entries)
                else "declared intent has no same-run accepted-occurrence ownership; "
                "no correspondence was inferred"
            ),
        },
        "layout": (
            _declared_layout(
                drawing,
                model,
                lint,
                registry,
                feature_ids,
                identities,
                representations,
            )
            if drawing is not None and registry is not None
            else {
                "availability": "unavailable",
                "reason": "the live Drawing and annotation registry were not supplied",
            }
        ),
        "lint": lint,
    }


def engineering_meaning(meaning):
    """Project one compiled measurement meaning into deterministic JSON data.

    The measurement verifier and generated-replay assessment share this projection.  Keeping
    it here prevents those two evidence surfaces from assigning different meanings to the
    same confirmed compiled claim.
    """
    from draftwright.fits import FitClass

    value, tolerance, span, axis, discriminator, member, angular = meaning
    if isinstance(tolerance, FitClass):
        tolerance = {
            "kind": "fit",
            "code": tolerance.code,
            "lower": tolerance.lower,
            "upper": tolerance.upper,
        }
    return {
        "value": value,
        "tolerance": tolerance,
        "span": span,
        "axis": axis,
        "discriminator": discriminator,
        "location_member": member,
        "angular_reference": angular,
    }


def _document_intents(model, owner_id):
    def projected(items):
        return [
            {
                "owner_id": owner_id(item.feature),
                "role": item.role,
                "discriminator": item.discriminator,
                "member": item.member,
                "display_decimals": item.display_decimals,
                "view": item.view,
                "side": item.side,
            }
            for item in items
        ]

    return {
        "source": "automatic" if model.authored_dimensions is None else "authored",
        "authored": None
        if model.authored_dimensions is None
        else projected(model.authored_dimensions),
        "requested": projected(model.requested_dimensions),
    }


def _document_views(constraints, owner_id):
    from dataclasses import asdict, replace

    def projected(item):
        target = item.spec.target
        if target is not None and target[0] == "feature":
            target = ("owner", owner_id(target[1]))
        return asdict(replace(item, spec=replace(item.spec, target=target)))

    return {
        "principal_source": constraints.principal_source,
        "derived_source": constraints.derived_source,
        "principals": [projected(item) for item in constraints.principals],
        "added_principals": [projected(item) for item in constraints.added_principals],
        "derived": [projected(item) for item in constraints.derived],
        "added_derived": [projected(item) for item in constraints.added_derived],
        "relations": [asdict(item) for item in constraints.relations],
        "pins": [asdict(item) for item in constraints.pins],
    }


def _document_cell(cell):
    if cell is None:
        return None
    if (
        not isinstance(cell, tuple)
        or len(cell) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in cell)
        or cell[0] < 1
        or cell[1] < 0
    ):
        raise ReportUnavailableError("document evidence has an invalid cell address")
    return {"row": cell[0], "column": cell[1]}


def _document_schedules(schedules, owner_id):
    return [
        {
            "name": schedule.name,
            "prefer": schedule.prefer,
            "rows": [
                {"owner_id": owner_id(row.feature), "parameters": row.parameters}
                for row in schedule.rows
            ],
        }
        for schedule in schedules
    ]


def document_report(
    *, catalog, evaluation, model, source, run_options, member_recipes, resolved
) -> dict[str, object]:
    """Project a document read; authored schedules select schema v5, otherwise v4.

    Carrier attribution is producer-owned. A missing producer projection stays explicit
    and prevents bounded clearance; ordinary owner/parameter associations are not proof.
    """
    if len(evaluation.requirements) != len(catalog.requirements) or any(
        row.requirement is not expected
        for row, expected in zip(evaluation.requirements, catalog.requirements, strict=True)
    ):
        raise ReportUnavailableError("document evaluation lost its exact catalog")
    occurrences, _unused, occurrence_summary = project_occurrences(
        catalog.evidence, catalog.ownership, model
    )
    occurrence_ids = {
        id(catalog.evidence.record(reference)): (catalog.evidence.record(reference), item["id"])
        for reference, item in zip(catalog.evidence.features, occurrences, strict=True)
    }
    owners = _feature_ids(model)

    def owner_id(feature):
        candidate = owners.get(id(feature))
        if candidate is None or candidate[0] is not feature:
            raise ReportUnavailableError("document claim has no exact common owner")
        return candidate[1]["id"]

    members = evaluation.members
    cell_schema = any(
        snapshot.model.schedules or member_recipes.get(name, {}).get("schedules")
        for name, snapshot, _rows in members
    )
    member_registries = {name: snapshot.registry for name, snapshot, _rows in members}
    sheet_ids = {
        name: f"sheet:{index + 1}" for index, (name, _snapshot, _rows) in enumerate(members)
    }
    if not sheet_ids or len(sheet_ids) != len(members):
        raise ReportUnavailableError("document member identities are absent or repeated")
    refs = set(evaluation.annotation_refs.values())

    def annotation_ref(sheet, name):
        if sheet not in sheet_ids or (sheet, name) not in refs:
            raise ReportUnavailableError("document evidence refers to an absent member annotation")
        return {"sheet_id": sheet_ids[sheet], "annotation": name}

    def combined_ref(name):
        if name not in evaluation.annotation_refs:
            raise ReportUnavailableError("requirement carrier names an absent annotation")
        return annotation_ref(*evaluation.annotation_refs[name])

    def claim_cell(claim):
        if claim.cell is not None and not any(
            (reference.row, reference.column) == claim.cell
            and reference.measurement.feature is claim.owner
            and reference.measurement.parameter == claim.parameter
            for reference in member_registries[claim.sheet].cells_of(claim.annotation)
        ):
            raise ReportUnavailableError("document claim refers to an absent exact cell")
        return _document_cell(claim.cell)

    def carrying_ref(carrier):
        reference: dict[str, Any] = {
            **combined_ref(carrier.annotation),
            "evidence_kind": carrier.kind,
        }
        if cell_schema:
            cell = carrier.cell
            if cell is not None and not any(
                actual is cell for actual in evaluation.registry.cells_of(carrier.annotation)
            ):
                raise ReportUnavailableError("requirement carrier names a foreign cell")
            reference["cell"] = _document_cell(None if cell is None else (cell.row, cell.column))
        return reference

    claims: list[dict[str, Any]] = []
    claim_ids = {}
    for snapshot in evaluation.claims:
        for claim in snapshot.claims:
            key = f"claim:{len(claims) + 1}"
            claim_ids[id(claim)] = key
            claims.append(
                {
                    "id": key,
                    **annotation_ref(claim.sheet, claim.annotation),
                    "owner_id": owner_id(claim.owner),
                    "parameter_id": claim.parameter,
                    "address": claim.address,
                    "meaning": engineering_meaning(claim.meaning),
                    "rendered": claim.rendered,
                    "verification": "confirmed-within-measurement-verifier-scope",
                    **({"cell": claim_cell(claim)} if cell_schema else {}),
                }
            )

    def claim_id(claim):
        result = claim_ids.get(id(claim))
        if result is None:
            raise ReportUnavailableError("document proof refers to an absent confirmed claim")
        return result

    conflicts = [
        {
            "code": "conflicting_engineering_meanings",
            "claim_ids": [claim_id(claim) for claim in conflict.claims],
        }
        for conflict in evaluation.conflicts
    ]
    unknown = [
        {
            **annotation_ref(sheet, annotation),
            "reason_code": reason,
            **({"cell": None} if cell_schema else {}),
        }
        for snapshot in evaluation.claims
        for sheet, annotation, reason in snapshot.unknown
    ]
    if cell_schema:
        unknown.extend(
            {
                **annotation_ref(sheet, item.annotation),
                "reason_code": item.reason,
                "cell": _document_cell(item.cell),
            }
            for snapshot in evaluation.claims
            for sheet, item in snapshot.cell_unknown
        )
    requirements: list[dict[str, Any]] = []
    profile_ids: dict[int, str] = {}
    support_ids: dict[tuple[int, int], str] = {}
    attributed_by_occurrence: dict[str, list[str]] = {item["id"]: [] for item in occurrences}
    for evaluated in evaluation.requirements:
        row = evaluated.requirement
        if evaluated.state not in {
            "placed",
            "satisfied_by_structured_note",
            "dependency-derived",
            "inapplicable",
            "unresolved",
            "uncovered",
        }:
            raise ReportUnavailableError("document requirement has an invalid evaluation state")
        key = f"requirement:{len(requirements) + 1}"
        source_ids = [
            _exact_occurrence_id(record, occurrence_ids) for record in row.source_records
        ]
        local = [
            {
                "sheet_id": sheet_ids[name],
                "state": getattr(outcome, "state", "unsupported"),
                "reason_code": _REQUIREMENT_REASON.get(
                    getattr(outcome, "state", "unsupported"), "source_owned_inapplicability"
                ),
            }
            for name, outcome in evaluated.local
        ]
        proofs = [
            {
                "alternative": proof.alternative,
                "supports": [
                    [claim_id(claim) for claim in carriers] for carriers in proof.carriers
                ],
                "distinct_witnesses": [claim_id(claim) for claim in proof.witnesses],
            }
            for proof in evaluated.dependencies
        ]
        # Populated by each existing ledger's acceptance point. Until a producer
        # supplies it, retain the absence instead of guessing from annotation names.
        carriers = getattr(evaluated.combined.outcome, "carriers", None)
        attributed = carriers is not None
        carrying = [] if carriers is None else [carrying_ref(carrier) for carrier in carriers]
        if evaluated.state == "dependency-derived":
            attributed = bool(proofs)
            carrying = []
        elif evaluated.state not in {"placed", "satisfied_by_structured_note"}:
            attributed = True
            carrying = []
        elif not carrying:
            attributed = False
        result = {
            "id": key,
            "family": row.family,
            "occurrence_ids": source_ids,
            "owner_ids": [owner_id(feature) for feature in row.features],
            "parameter_id": row.parameter_id,
            "requirement_count": row.requirement_count,
            "requirement_count_known": row.requirement_count_known,
            "state": evaluated.state,
            "coverage_credit": int(
                evaluated.state in {"placed", "satisfied_by_structured_note", "dependency-derived"}
            ),
            "local_outcomes": local,
            "carrying_annotations": carrying,
            "carrier_attribution": "available" if attributed else "unavailable",
            "dependency_proofs": proofs,
            "intrinsic_exclusion": None
            if row.intrinsic_exclusion is None
            else {
                "reason_code": row.intrinsic_exclusion.reason_code,
                "occurrence_ids": [
                    _exact_occurrence_id(record, occurrence_ids)
                    for record in row.intrinsic_exclusion.source_records
                ],
                "span": getattr(row.intrinsic_exclusion, "span", None),
            },
        }
        if row.source_profile is not None:
            result["profile_source"] = _profile_source(
                catalog.evidence, row.source_profile, profile_ids, support_ids
            )
        requirements.append(result)
        for identity in source_ids:
            attributed_by_occurrence[identity].append(key)
    for occurrence in occurrences:
        identities = attributed_by_occurrence[occurrence["id"]]
        occurrence["requirements"] = {
            "coverage": "ledger" if identities else "not-projected",
            "ids": identities,
        }

    sheets = []
    for name, snapshot, _rows in members:
        if snapshot.lint is None or name not in resolved or name not in member_recipes:
            raise ReportUnavailableError(f"document sheet {name!r} lacks lint or run options")
        if cell_schema and "schedules" not in member_recipes[name]:
            raise ReportUnavailableError(f"document sheet {name!r} lacks schedule intent")
        options = {
            key: str(value) if isinstance(value, PathLike) else value
            for key, value in member_recipes[name]["options"].items()
        }
        sheets.append(
            {
                "id": sheet_ids[name],
                "name": name,
                "options": options,
                "resolved": resolved[name],
                "dimension_intents": _document_intents(snapshot.model, owner_id),
                "view_intents": _document_views(member_recipes[name]["views"], owner_id),
                "table_intents": member_recipes[name]["tables"],
                **(
                    {
                        "schedule_intents": _document_schedules(
                            member_recipes[name]["schedules"], owner_id
                        )
                    }
                    if cell_schema
                    else {}
                ),
                "lint": snapshot.lint,
            }
        )

    def axis_summary(axis):
        values = [sheet["lint"].get("quality", {}).get(axis, {}) for sheet in sheets]
        unavailable = [
            sheet["id"]
            for sheet, value in zip(sheets, values, strict=True)
            if not value.get("available", False)
        ]
        affected = [
            sheet["id"]
            for sheet, value in zip(sheets, values, strict=True)
            if value.get("raw_issues", 0)
        ]
        return {
            "status": "needs-attention"
            if affected
            else "unassessed"
            if unavailable
            else "clear-within-lint-scope",
            "affected_sheets": affected,
            "unassessed_sheets": unavailable,
        }

    layout = axis_summary("legibility")
    fidelity = {
        **axis_summary("fidelity"),
        "conflicts": conflicts,
        "unknown_claims": unknown,
        "scope": "verified-measurement-claims-and-member-fidelity-lint",
        "unassessed_scope": [
            "authored-note-prose",
            "manufacturing-intent",
            "engineering-content-without-typed-verified-claims",
        ],
    }
    if conflicts or unknown:
        fidelity["status"] = "needs-attention"
    applicable = [row for row in requirements if row["state"] != "inapplicable"]
    unknown_counts = sum(not row["requirement_count_known"] for row in applicable)
    denominator = sum(
        row["requirement_count"] for row in applicable if row["requirement_count_known"]
    )
    credit = sum(row["coverage_credit"] for row in applicable)
    unresolved = [row["id"] for row in applicable if not row["coverage_credit"]]
    unattributed = [row["id"] for row in applicable if row["carrier_attribution"] == "unavailable"]
    coverage = {
        "scope": "accepted-occurrences-and-profile-requirements",
        "known_requirement_count": denominator,
        "unknown_cardinality_rows": unknown_counts,
        "credited_requirements": credit,
        "audited_score": credit / denominator if denominator and not unknown_counts else None,
        "uncovered_or_unresolved": unresolved,
        "unattributed_carriers": unattributed,
        "excludes": ["unrecognised-geometry", "manufacturing-readiness"],
    }
    unresolved_occurrences = [
        item["id"]
        for item in occurrences
        if item["disposition"] in _ATTENTION_DISPOSITIONS
        or item["requirements"]["coverage"] == "not-projected"
    ]
    attention = bool(
        unresolved
        or unattributed
        or unknown_counts
        or unresolved_occurrences
        or conflicts
        or unknown
    )
    attention = (
        attention
        or layout["status"] != "clear-within-lint-scope"
        or fidelity["status"] != "clear-within-lint-scope"
    )
    report = {
        "schema": REPORT_SCHEMA,
        "schema_version": 5 if cell_schema else 4,
        "scope": "document",
        "status": "needs-attention" if attention else "bounded-clear",
        "producer": producer(),
        "source": source,
        "run_options": run_options,
        "outputs": {},
        "recognition": {
            "identity_scope": "document-local",
            "occurrences": occurrences,
            "owners": [owner for _feature, owner in owners.values()],
            "requirements": requirements,
            "summary": occurrence_summary,
            "unresolved_occurrences": unresolved_occurrences,
        },
        "sheets": sheets,
        "claims": claims,
        "assessment": {
            "coverage": coverage,
            "layout": layout,
            "fidelity": fidelity,
            "manufacturing": {"status": "unassessed", "readiness": "not-certified"},
        },
        "replay": {
            "identity_lifetime": "this-report-only",
            "source_snapshot": "immutable-step-bytes",
            "member_reads": "live-at-report-call",
            "requires": "source-recipe-and-current-inventory-assertions",
            "durable_feature_identity": False,
            "serialized_declaration_scope": [
                "dimension-selection",
                "view-constraints",
                "table-text",
                *(["feature-schedules"] if cell_schema else []),
            ],
            "source_recipe_required_for": [
                "feature-decorations",
                "gdt",
                "authored-notes",
                "measured-dimensions",
                "member-pmi-declarations",
                "live-edits",
            ],
            "script_deserialization": False,
        },
    }
    return cast(dict[str, object], json_value(report))


def write_json_document(report: Mapping[str, object], path: str | PathLike[str]) -> str:
    """Atomically write one strict, deterministic UTF-8 JSON document.

    Named for what it does rather than for its first caller: the body is document-agnostic,
    and `sheet_emit` already uses it for the STEP-inspection sidecar, a different schema with
    a different `$id`. It was `write_report_document` until #1469 promoted it to `__all__`,
    which is the moment an inaccurate name stops being free to change.
    """

    destination = Path(path)
    payload = (
        json.dumps(
            report,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=".draftwright-",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Cleanup is best-effort: never hide the write/replace failure that tells the
                # caller whether the requested report reached its destination.
                pass
        raise
    return str(destination)


__all__ = [
    "REPORT_SCHEMA",
    "REPORT_SCHEMA_VERSION",
    "CatalogRequirement",
    "DocumentEvaluation",
    "DocumentRequirementEvaluation",
    "evaluate_document_requirements",
    "JsonValue",
    "RequirementCatalog",
    "RequirementSnapshot",
    "ReportUnavailableError",
    "build_requirement_catalog",
    "match_requirement_catalog",
    "drawing_report",
    "engineering_meaning",
    "declared_drawing_report",
    "document_report",
    # The shared occurrence projector (#1461). Three schema'd public documents are built
    # from these — the drawing report, the STEP inspection document, and the sidecar the
    # script emitter writes — so their shape is a contract, not an implementation detail.
    # `_ATTENTION_DISPOSITIONS` and `_DISPOSITIONS` deliberately stay private: nothing
    # outside this module reads them, and a public name with no consumer is a promise
    # made to no one.
    "json_value",
    "producer",
    "project_occurrences",
    "project_feature_occurrence_ids",
    "validate_report_inputs",
    "write_json_document",
]
