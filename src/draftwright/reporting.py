"""Versioned machine-readable drawing reports and generation-time gap snapshots."""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Mapping
from importlib.metadata import version as distribution_version
from os import PathLike
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from draftwright.recogniser_schema import consumed_record_schema_versions_for_type

if TYPE_CHECKING:
    from draftwright.model import PartModel
    from draftwright.recognition_cache import RecognitionEvidenceView
    from draftwright.recognition_ownership import RecognitionOwnership

REPORT_SCHEMA = "draftwright-report"
REPORT_SCHEMA_VERSION = 1
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
_SNAPSHOT_SCHEMA = "draftwright-recognition-snapshot"
_SNAPSHOT_SCHEMA_VERSION = 1

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class ReportUnavailableError(RuntimeError):
    """The drawing cannot yet produce a truthful occurrence-level report."""


def _json_value(value: object) -> JsonValue:
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


def _producer() -> dict[str, str]:
    return {
        "draftwright": distribution_version("draftwright"),
        "b123d-recognisers": distribution_version("b123d-recognisers"),
    }


def _recognition_coordinates(evidence: RecognitionEvidenceView) -> dict[str, JsonValue]:
    """Describe where public record coordinates live without conflating source and working."""

    from b123d_recognisers.evidence import FramedRecognitionEvidence, RecognitionEvidence

    if type(evidence) is RecognitionEvidence:
        return {
            "record_space": "caller",
            "caller_from_record": {"kind": "identity"},
        }
    if type(evidence) is not FramedRecognitionEvidence:
        raise ReportUnavailableError("recognition evidence has an unsupported authority type")
    frame = evidence.frame
    return {
        "record_space": "provider-working",
        "caller_from_record": {
            "kind": "rigid-frame",
            "origin_mm": _json_value(frame.origin),
            "x_axis": _json_value(frame.x),
            "y_axis": _json_value(frame.y),
            "z_axis": _json_value(frame.z),
            "gauge": frame.gauge.value,
        },
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
    explicit = tuple(getattr(outcome, "measurement_ids", ()))
    if explicit:
        return explicit
    representation = getattr(outcome, "representation_feature", None)
    representation_parameter = getattr(outcome, "representation_parameter", None)
    if representation is not None and isinstance(representation_parameter, str):
        return ((representation, representation_parameter),)
    parameter = getattr(outcome, "parameter_id", None)
    if not isinstance(parameter, str) or parameter == "?":
        return ()
    return tuple((feature, parameter) for feature in getattr(outcome, "features", ()))


AnnotationIndex = dict[tuple[int, str], tuple[object, tuple[str, ...]]]


def _annotation_index(registry: object) -> AnnotationIndex:
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


def _annotation_names(index: AnnotationIndex, outcome: object) -> list[str]:
    result: set[str] = set()
    for feature, parameter in _outcome_measurements(outcome):
        candidate = index.get((id(feature), parameter))
        if candidate is not None and candidate[0] is feature:
            result.update(candidate[1])
    return sorted(result)


def _requirements(
    *,
    evidence: RecognitionEvidenceView,
    model: PartModel,
    occurrences: list[dict[str, Any]],
    registry: object,
    omissions: tuple[object, ...],
    dimension_plan: object | None,
    part: object | None,
    requirement_outcomes: Mapping[str, tuple[Any, ...]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[str]], set[str]]:
    """Project the recognition-owned semantic denominator exactly once."""

    if requirement_outcomes is None:
        from draftwright.linting.requirements import recognized_requirement_outcomes

        requirement_outcomes = recognized_requirement_outcomes(
            evidence.result,
            tuple(model.features),
            registry,
            omissions,
            dimension_plan=dimension_plan,
            part=part,
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

    for family, outcomes in requirement_outcomes.items():
        for outcome in outcomes:
            state = getattr(outcome, "state", None)
            if state not in _REQUIREMENT_STATES:
                raise ReportUnavailableError(
                    f"recognized requirement family {family!r} has invalid state {state!r}"
                )
            source_records = _outcome_records(outcome)
            if not source_records:
                raise ReportUnavailableError(
                    f"recognized requirement family {family!r} has no exact source records"
                )
            source_ids = sorted(
                {_exact_occurrence_id(record, occurrence_ids) for record in source_records},
                key=occurrence_order.__getitem__,
            )
            if state == "inapplicable":
                inapplicable_occurrences.update(source_ids)
                continue
            count = getattr(outcome, "requirement_count", 1)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ReportUnavailableError(
                    f"recognized requirement family {family!r} has invalid cardinality"
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
            for _index in range(count):
                requirement_id = f"requirement:{len(requirements) + 1}"
                requirements.append(
                    {
                        "id": requirement_id,
                        "family": family,
                        "occurrence_ids": source_ids,
                        "owner_ids": owner_ids,
                        "parameter_id": parameter_id,
                        "state": state,
                        "reason_code": _REQUIREMENT_REASON[state],
                        "annotations": annotations,
                        "representation": representation,
                        "representation_reason": representation_reason,
                    }
                )
                for occurrence_id in source_ids:
                    by_occurrence[occurrence_id].append(requirement_id)

    for occurrence in occurrences:
        if occurrence["disposition"] != "unsupported":
            continue
        occurrence_id = str(occurrence["id"])
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
    evidence: RecognitionEvidenceView | None,
    ownership: RecognitionOwnership | None,
    model: PartModel | None,
) -> tuple[RecognitionEvidenceView, RecognitionOwnership, PartModel]:
    """Refuse unavailable authority before any diagnostic work can trigger recognition."""

    if evidence is None or ownership is None or ownership.evidence is not evidence:
        raise ReportUnavailableError(
            "accepted occurrence ownership is unavailable for this drawing; "
            "raw or exact framed automatic recognition is required by report schema version 1"
        )
    if model is None:
        raise ReportUnavailableError("the drawing has no final IR model")
    return evidence, ownership, model


def _occurrences(
    evidence: RecognitionEvidenceView | None,
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
                "record": _json_value(record.to_dict()),
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


def drawing_report(
    *,
    evidence: RecognitionEvidenceView | None,
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
    """Build the strict schema-v1 report for one raw or exact-framed automatic drawing.

    ``bounded-clear`` means only that this report found no known occurrence, semantic
    requirement, or lint blocker. It is deliberately not manufacturing readiness: recognition
    can miss physical geometry and manufacturing intent remains separately authored.
    """

    occurrences, requirements, summary = _occurrences(
        evidence,
        ownership,
        model,
        registry=registry,
        omissions=omissions,
        dimension_plan=dimension_plan,
        part=part,
        requirement_outcomes=requirement_outcomes,
    )
    lint = cast(dict[str, object], _json_value(lint))
    needs_attention = not bool(lint.get("passed")) or any(
        summary[disposition] for disposition in _ATTENTION_DISPOSITIONS
    )
    needs_attention = needs_attention or any(
        requirement["state"] not in {"placed", "satisfied_by_structured_note"}
        for requirement in requirements
    )
    needs_attention = needs_attention or any(
        occurrence["requirements"]["coverage"] in {"not-projected", "deferred", "unavailable"}
        for occurrence in occurrences
    )
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "needs-attention" if needs_attention else "bounded-clear",
        "producer": _producer(),
        "source": _source(source),
        "outputs": {},
        "recognition": {
            "coverage": "accepted-occurrences",
            "identity_scope": "report-local",
            "coordinates": _recognition_coordinates(cast("RecognitionEvidenceView", evidence)),
            "occurrences": occurrences,
            "requirements": requirements,
            "summary": summary,
        },
        "lint": lint,
    }


def _generation_snapshot(
    *,
    evidence: RecognitionEvidenceView | None,
    ownership: RecognitionOwnership | None,
    model: PartModel | None,
    source: str | PathLike[str] | None,
    source_sha256: str | None,
) -> dict[str, JsonValue]:
    """Project generation-time accepted-occurrence gaps without compiling or rendering."""

    occurrences, _requirements, _summary = _occurrences(evidence, ownership, model)
    gaps = [
        {
            key: occurrence[key]
            for key in (
                "id",
                "family",
                "record_type",
                "record_schema_version",
                "record",
                "disposition",
                "reason_code",
                "tracking",
            )
        }
        for occurrence in occurrences
        if occurrence["disposition"] in _ATTENTION_DISPOSITIONS
    ]
    summary = {
        "total": len(gaps),
        **{
            disposition: sum(gap["disposition"] == disposition for gap in gaps)
            for disposition in (
                "unsupported",
                "deferred",
                "evidence_only",
                "unexpectedly_missing",
            )
        },
    }
    snapshot_source: dict[str, str | None] = {
        **_source(source),
        "sha256": source_sha256,
    }
    return cast(
        dict[str, JsonValue],
        {
            "schema": _SNAPSHOT_SCHEMA,
            "schema_version": _SNAPSHOT_SCHEMA_VERSION,
            "status": (
                "accepted_occurrences_unrepresented"
                if gaps
                else "no_unrepresented_accepted_occurrences"
            ),
            "coverage": "accepted-occurrence-gaps",
            "producer": _producer(),
            "source": snapshot_source,
            "coordinates": _recognition_coordinates(cast("RecognitionEvidenceView", evidence)),
            "summary": summary,
            "gaps": gaps,
        },
    )


def _write_report_document(report: dict[str, object], path: str | PathLike[str]) -> str:
    """Atomically write one strict, deterministic UTF-8 report document."""

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
            prefix=".draftwright-report-",
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
    "ReportUnavailableError",
    "validate_report_inputs",
]
