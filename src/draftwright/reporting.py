"""Versioned machine-readable projection of one drawing's accepted recognition evidence."""

from __future__ import annotations

import json
import os
from collections import Counter
from importlib.metadata import version as distribution_version
from os import PathLike
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any, cast

from draftwright.recogniser_schema import consumed_record_schema_versions_for_type

if TYPE_CHECKING:
    from b123d_recognisers.evidence import RecognitionEvidence

    from draftwright.model import PartModel
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


class ReportUnavailableError(RuntimeError):
    """The drawing cannot yet produce a truthful occurrence-level report."""


def _json_value(value: object) -> object:
    """Return isolated strict JSON primitives, rejecting NaN/Infinity and repr fallbacks."""

    return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))


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


def validate_report_inputs(
    evidence: RecognitionEvidence | None,
    ownership: RecognitionOwnership | None,
    model: PartModel | None,
) -> tuple[RecognitionEvidence, RecognitionOwnership, PartModel]:
    """Refuse unavailable authority before any diagnostic work can trigger recognition."""

    if evidence is None or ownership is None or ownership.evidence is not evidence:
        raise ReportUnavailableError(
            "accepted occurrence ownership is unavailable for this drawing; "
            "raw automatic recognition is required by report schema version 1"
        )
    if model is None:
        raise ReportUnavailableError("the drawing has no final IR model")
    return evidence, ownership, model


def _occurrences(
    evidence: RecognitionEvidence | None,
    ownership: RecognitionOwnership | None,
    model: PartModel | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
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
                "requirements": {"coverage": "not-projected", "outcomes": []},
            }
        )

    summary = {"total": len(projected)}
    summary.update({status: disposition_counts[status] for status in _DISPOSITIONS})
    return projected, summary


def drawing_report(
    *,
    evidence: RecognitionEvidence | None,
    ownership: RecognitionOwnership | None,
    model: PartModel | None,
    lint: dict[str, object],
    source: str | PathLike[str] | None,
) -> dict[str, object]:
    """Build the strict schema-v1 report for one raw automatic drawing.

    ``bounded-clear`` means only that this report found no known occurrence-disposition or lint
    blocker. It is deliberately not manufacturing readiness: schema v1 does not yet project the
    compiler's feature→requirement→annotation outcome ledger.
    """

    occurrences, summary = _occurrences(evidence, ownership, model)
    lint = cast(dict[str, object], _json_value(lint))
    needs_attention = not bool(lint.get("passed")) or any(
        summary[disposition] for disposition in _ATTENTION_DISPOSITIONS
    )
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "needs-attention" if needs_attention else "bounded-clear",
        "producer": {
            "draftwright": distribution_version("draftwright"),
            "b123d-recognisers": distribution_version("b123d-recognisers"),
        },
        "source": _source(source),
        "outputs": {},
        "recognition": {
            "coverage": "accepted-occurrences",
            "identity_scope": "report-local",
            "occurrences": occurrences,
            "summary": summary,
        },
        "lint": lint,
    }


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
