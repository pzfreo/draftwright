"""Versioned read-only STEP inspection evidence.

:func:`inspect_step` answers one question — *what does Draftwright actually see in this STEP
file?* — without constructing, laying out, rendering, or exporting a drawing.  It is the first
public ``inspect`` surface (#1460): an ordinary Python contract that a later MCP tool can adapt
without re-deriving any of the evidence below.

The document deliberately separates four different kinds of fact so an automated caller can
never confuse them:

* ``geometry`` — measured STEP geometry, in caller coordinates;
* ``recognition`` — the recogniser's *inference*, plus Draftwright's own consumer disposition
  for each accepted occurrence;
* ``pmi`` — semantic AP242 annotation authored in the source document; and
* the ``qualifiers`` list, which states in stable codes what the document is explicitly *not*.

A clear-looking inspection is bounded recognition evidence.  It is not physical completeness and
not manufacturing readiness, and nothing here infers material, process, finish, thread, fit, or
tolerance intent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from os import PathLike
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from draftwright.reporting import (
    _ATTENTION_DISPOSITIONS,
    JsonValue,
    ReportUnavailableError,
    _json_value,
    _occurrences,
    _producer,
)

INSPECTION_SCHEMA = "draftwright-step-inspection"
INSPECTION_SCHEMA_VERSION = 1

# Version 1 is raw/caller-coordinate only.  Framed recognition would move recognised geometry
# into a provider working frame, and b123d-recognisers#493 cannot yet tell a consumer whether a
# refused framed run had already recognised.  The document therefore refuses rather than silently
# reporting working-frame values as caller coordinates (ADR 0020).
_SUPPORTED_FRAME_STATUS = "raw"

# Stable codes for what a clear document does *not* assert.  They are part of the closed schema:
# a caller may branch on them, so adding, removing, or re-meaning one needs a new version.
_QUALIFIERS = (
    "bounded_recognition_evidence_only",
    "not_physical_completeness",
    "not_manufacturing_readiness",
    "no_inferred_material_process_finish_thread_fit_or_tolerance_intent",
)

# A PMI source entity that could not be lowered at all, or only in part, is a real gap in the
# evidence: it keeps the document explicit rather than letting the caller read silence as
# absence.  ``presentation_only`` is not a gap — it is a faithful statement that the source
# entity carries graphics and no semantics.
_PMI_ATTENTION_OUTCOMES = frozenset({"not_extracted", "partially_extracted"})
_PMI_OUTCOMES = ("extracted", "partially_extracted", "presentation_only", "not_extracted")

_UNITS = {"length": "mm", "area": "mm²", "volume": "mm³", "angle": "degree"}


class InspectionUnavailableError(RuntimeError):
    """The STEP source cannot yield a truthful version-1 inspection document."""


def _vector(value) -> list[float]:
    return [float(value.X), float(value.Y), float(value.Z)]


def _bbox(box) -> dict[str, list[float]]:
    return {"min": _vector(box.min), "max": _vector(box.max), "size": _vector(box.size)}


def _face_description(face) -> dict[str, Any]:
    """Describe one original face by bounded geometry, never by a reference or topology index."""

    geom_type = getattr(face, "geom_type", None)
    centre = face.center()
    return {
        "surface": "unknown" if geom_type is None else geom_type.name.lower(),
        "area": float(face.area),
        "centroid": _vector(centre),
        "bbox": _bbox(face.bounding_box()),
    }


def _face_descriptions(evidence, references) -> list[dict[str, Any]]:
    """Return face descriptions in a total order derived only from their own serialized values.

    The provider hands faces back as an unordered ``frozenset`` of opaque references, whose
    iteration order depends on object addresses.  Sorting by the serialized description makes the
    output deterministic without inventing an identity: two faces that sort equal serialize
    identically, so their relative order cannot be observed.
    """

    described = [_face_description(evidence.face(reference)) for reference in references]
    return sorted(described, key=lambda item: json.dumps(item, sort_keys=True, allow_nan=False))


def _measure(measure) -> dict[str, Any]:
    return {
        "total": measure.total,
        "associated": measure.associated,
        "unassociated": measure.unassociated,
        "ratio": measure.ratio,
    }


def _association(evidence) -> dict[str, Any]:
    """Project the provider's own face/area association accounting.

    ``unassociated`` means exactly one thing: no accepted occurrence claimed that face.  Stock,
    background, and deliberately plain faces are in the denominator, so an unassociated face is
    not evidence of a missed feature — the qualifier says so in the document itself.
    """

    association = evidence.association
    return {
        "provenance": "recogniser-evidence",
        "coverage": "accepted-constituent-evidence",
        "face_count": _measure(association.face_count),
        "surface_area": _measure(association.surface_area),
        "families": [
            {
                "family": item.family,
                "face_count": item.face_count,
                "surface_area": item.surface_area,
            }
            # Family contributions overlap and are not additive, so they are reported as the
            # provider states them, in a stable name order rather than a ranked one.
            for item in sorted(association.families, key=lambda item: item.family)
        ],
        "unassociated": {
            "qualifier": "not_evidence_of_missed_feature",
            "faces": _face_descriptions(evidence, association.unassociated_faces),
        },
    }


def _occurrence_documents(
    evidence, ownership, model
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Reuse the report projector's occurrence ledger, then attach bounded face evidence.

    The projector already refuses an incomplete or unclassified ownership ledger, which is what
    keeps the occurrence denominator honest: a document is never produced by dropping an
    occurrence Draftwright cannot classify.
    """

    try:
        occurrences, _requirements, summary = _occurrences(evidence, ownership, model)
    except ReportUnavailableError as error:
        raise InspectionUnavailableError(str(error)) from error

    documents: list[dict[str, Any]] = []
    for reference, occurrence in zip(evidence.features, occurrences, strict=True):
        document = {key: value for key, value in occurrence.items() if key != "requirements"}
        document["faces"] = {
            "coverage": "defining-and-constituent",
            "defining": _face_descriptions(evidence, evidence.defining_faces(reference)),
            "constituent": _face_descriptions(evidence, evidence.constituent_faces(reference)),
        }
        documents.append(document)
    return documents, summary


def _pmi_document(report) -> dict[str, Any]:
    """Project the complete AP242 source census, its extracted records, and its error state."""

    if report is None:
        raise InspectionUnavailableError("the STEP source produced no PMI extraction census")
    sources = [
        {
            "source_id": source.source_id,
            "category": source.category,
            "type_code": source.type_code,
            "outcome": source.outcome,
            "reason": source.reason,
        }
        for source in report.sources
    ]
    if report.error is not None:
        status = "extraction_error"
    elif not sources:
        status = "absent"
    else:
        status = "present"
    return {
        "provenance": "step-ap242-source",
        "coverage": "source-census-and-extracted-records",
        "status": status,
        "error": report.error,
        "sources": sources,
        "records": [asdict(record) for record in report.records],
        "summary": {
            "sources": len(sources),
            "records": len(report.records),
            **{
                outcome: sum(source["outcome"] == outcome for source in sources)
                for outcome in _PMI_OUTCOMES
            },
        },
    }


def _needs_attention(summary: dict[str, int], pmi: dict[str, Any]) -> bool:
    if any(summary[disposition] for disposition in _ATTENTION_DISPOSITIONS):
        return True
    if pmi["status"] == "extraction_error":
        return True
    return any(source["outcome"] in _PMI_ATTENTION_OUTCOMES for source in pmi["sources"])


def inspect_step(path: str | PathLike[str]) -> dict[str, JsonValue]:
    """Return the strict, versioned version-1 inspection document for the STEP file at *path*.

    The source is resolved once and read once.  Geometry, recognition, and PMI extraction all
    consume a private copy of those exact hashed bytes, so replacing a mutable (or symlinked)
    source mid-inspection cannot split the three sections across two different files.

    Exactly one aggregate recognition run happens, and its evidence, model, and conversion-time
    ownership are reused as-is: no section re-recognises, and no drawing build, view projection,
    annotation placement, render, export, or physical lint path is required to obtain the
    document.

    Raises:
        OSError: the path could not be read (missing, a directory, permissions).
        InspectionUnavailableError: the bytes are not a readable solid STEP body, or the run
            cannot state its evidence truthfully — an unclassified occurrence ownership ledger,
            an absent aggregate, or a non-raw recognition frame.
    """

    # Imported here, not at module scope: `from draftwright import inspect_step` must stay
    # sub-second, and only an actual inspection has any use for the ~5 s CAD kernel (#313).
    from draftwright.builder import _detect_part_model_analysis

    resolved = Path(path).resolve()
    source_bytes = resolved.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    with TemporaryDirectory(prefix="draftwright-inspect-") as directory:
        snapshot = Path(directory) / resolved.name
        snapshot.write_bytes(source_bytes)
        try:
            # ``pmi="off"`` keeps recognition geometry-only (ADR 0013): PMI records are never
            # lowered into the IR, so they cannot change an occurrence's owner.  The census and
            # the extracted records below are read from the same run regardless of that mode.
            model, analysis = _detect_part_model_analysis(snapshot, pmi="off")
        except ValueError as error:
            raise InspectionUnavailableError(
                f"could not read solid STEP geometry from {resolved.name!r}"
            ) from error

        part = analysis.part
        solids = part.solids()
        if not solids:
            raise InspectionUnavailableError(
                f"STEP source {resolved.name!r} carries no solid body to inspect"
            )

        frame_status = (analysis.recognition_frame_decision or {}).get("status")
        if frame_status != _SUPPORTED_FRAME_STATUS:
            raise InspectionUnavailableError(
                "inspection version 1 reports raw caller coordinates only; this run recognised "
                f"with frame status {frame_status!r}"
            )

        occurrences, summary = _occurrence_documents(
            analysis.recognition_evidence, analysis.recognition_ownership, model
        )
        association = _association(analysis.recognition_evidence)
        pmi = _pmi_document(analysis.pmi_report)

        document = {
            "schema": INSPECTION_SCHEMA,
            "schema_version": INSPECTION_SCHEMA_VERSION,
            "status": "needs-attention"
            if _needs_attention(summary, pmi)
            else "bounded-recognition-evidence",
            "qualifiers": list(_QUALIFIERS),
            "producer": _producer(),
            "source": {
                "kind": "step",
                # The basename only: an absolute path is caller-machine detail, not evidence.
                "name": resolved.name,
                "sha256": source_sha256,
                "byte_count": len(source_bytes),
                "artifact_id": f"step-sha256:{source_sha256}",
            },
            "units": dict(_UNITS),
            "geometry": {
                "provenance": "step-source",
                "coverage": "solid-body",
                "coordinates": "caller",
                "bbox": _bbox(analysis.bb),
                "volume": float(part.volume),
                "topology": {
                    "solids": len(solids),
                    "shells": len(part.shells()),
                    "faces": len(part.faces()),
                    "wires": len(part.wires()),
                    "edges": len(part.edges()),
                    "vertices": len(part.vertices()),
                },
            },
            "recognition": {
                "provenance": "recogniser-inference",
                "coverage": "accepted-occurrences",
                "identity_scope": "document-local",
                "coordinates": "caller",
                "frame": {"status": frame_status},
                "occurrences": occurrences,
                "summary": summary,
                "association": association,
            },
            "pmi": pmi,
        }

    return cast("dict[str, JsonValue]", _json_value(document))


__all__ = [
    "INSPECTION_SCHEMA",
    "INSPECTION_SCHEMA_VERSION",
    "InspectionUnavailableError",
    "inspect_step",
]
