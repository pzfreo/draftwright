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
    from quiddity.evidence import RecognitionEvidence

    from draftwright.model import PartModel
    from draftwright.profile_angles import ProfileAngle
    from draftwright.recognition_ownership import RecognitionOwnership

REPORT_SCHEMA = "draftwright-report"
REPORT_SCHEMA_VERSION = 3
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
                source = profile.source
                try:
                    if evidence.planar_outer_profile(source.face) is not source:
                        raise ValueError("profile does not belong to this evidence run")
                    for index in (profile.first_index, profile.second_index):
                        evidence.profile_edge(source, index)
                except (AttributeError, TypeError, ValueError, IndexError) as exc:
                    raise ReportUnavailableError(
                        "profile requirement has no exact issued source"
                    ) from exc
                # Allocate document IDs on first use; no opaque reference or
                # provider topology/support index is serialized.
                profile_id = profile_ids.setdefault(id(source), f"profile:{len(profile_ids) + 1}")
                pair_ids = [
                    support_ids.setdefault((id(source), index), f"support:{len(support_ids) + 1}")
                    for index in (profile.first_index, profile.second_index)
                ]
                profile_source = {
                    "kind": "planar_outer_profile",
                    "profile_id": profile_id,
                    "support_ids": pair_ids,
                }
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
    "JsonValue",
    "ReportUnavailableError",
    "drawing_report",
    # The shared occurrence projector (#1461). Three schema'd public documents are built
    # from these — the drawing report, the STEP inspection document, and the sidecar the
    # script emitter writes — so their shape is a contract, not an implementation detail.
    # `_ATTENTION_DISPOSITIONS` and `_DISPOSITIONS` deliberately stay private: nothing
    # outside this module reads them, and a public name with no consumer is a promise
    # made to no one.
    "json_value",
    "producer",
    "project_occurrences",
    "validate_report_inputs",
    "write_json_document",
]
