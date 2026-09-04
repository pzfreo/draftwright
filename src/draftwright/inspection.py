"""Read-only recognition evidence for a STEP file.

`inspect_step` exists so a person or an agent can find and correct two different kinds of
failing without building a drawing first:

* failings in **recognition** — the recogniser found the wrong thing, or missed something; and
* failings in **conversion** — recognition found it, and Draftwright did nothing useful with it.

The document therefore says three things, and keeps them apart:

* ``found`` — every feature the recogniser accepted, exactly as it stated it, and beside each
  one what Draftwright did with it;
* ``missed`` — geometry no accepted feature claimed; and
* ``source`` / ``producer`` — which bytes were read and which versions read them, so a finding
  can be reproduced or filed upstream.

``missed`` is currently one half of the story. It reports geometry that went unclaimed, which is
the provider's own accounting. It does **not** yet report what the recogniser considered and
rejected — the provider can explain that, but only from a second recognition run, which would
break the one-run rule of ADR 0017. An upstream change is needed first.

Nothing here is a completeness or readiness claim. An unclaimed face is not proof of a missed
feature: stock and plain faces are unclaimed too.
"""

from __future__ import annotations

import hashlib
import json
from os import PathLike
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast

from draftwright.reporting import (
    JsonValue,
    ReportUnavailableError,
    _json_value,
    _occurrences,
    _producer,
)

if TYPE_CHECKING:  # typing only — naming these must not cost the CAD kernel at import
    from draftwright._core import Analysis
    from draftwright.model import PartModel

INSPECTION_SCHEMA = "draftwright-step-inspection"
INSPECTION_SCHEMA_VERSION = 1

# Version 1 reports raw caller coordinates only. Framed recognition moves geometry into a
# provider working frame, and b123d-recognisers#493 cannot yet tell a consumer whether a refused
# framed run had already recognised — so refuse rather than report working-frame values as
# caller coordinates (ADR 0020).
_SUPPORTED_FRAME_STATUS = "raw"

# Draftwright acted on the feature: it is represented by an IR feature of its own, or absorbed
# into one. Every other disposition means the recogniser found something the drawing does not
# use, which is the conversion failing this document exists to surface.
_ACTED_ON = frozenset({"represented", "absorbed"})


class InspectionUnavailableError(RuntimeError):
    """The STEP source cannot yield a truthful inspection document."""


def _vector(value) -> list[float]:
    return [float(value.X), float(value.Y), float(value.Z)]


def _face(face) -> dict[str, Any]:
    """Describe one unclaimed face by bounded geometry, never by a reference or topology index."""

    try:
        # Not `getattr(face, "geom_type", None)`: the property raises on a degenerate face
        # rather than returning None, so a default cannot stand in for it.
        surface = face.geom_type.name.lower()
        centre = face.center()
        box = face.bounding_box()
    except ValueError as error:
        raise InspectionUnavailableError("a source face cannot be described") from error
    return {
        "surface": surface,
        "area": float(face.area),
        "centroid": _vector(centre),
        "bbox": {"min": _vector(box.min), "max": _vector(box.max)},
    }


def _faces(evidence, references) -> list[dict[str, Any]]:
    """Order face descriptions by their own serialized values.

    The provider hands faces back as an unordered set of address-hashed references, so without
    this the document changes between runs. Two faces that sort equal serialize identically, so
    their relative order cannot be observed — no identity is invented.
    """

    described = [_face(evidence.face(reference)) for reference in references]
    return sorted(described, key=lambda item: json.dumps(item, sort_keys=True, allow_nan=False))


def _found(evidence, ownership, model) -> list[dict[str, Any]]:
    """Every accepted feature, as the recogniser stated it, beside what Draftwright did with it.

    The occurrence ledger comes from the shared report projector, which refuses an unclassified
    ownership ledger — so a document is never produced by dropping a feature Draftwright cannot
    account for.
    """

    try:
        occurrences, _requirements, _summary = _occurrences(evidence, ownership, model)
    except ReportUnavailableError as error:
        raise InspectionUnavailableError(str(error)) from error

    return [
        {
            "id": occurrence["id"],
            "family": occurrence["family"],
            # The recogniser's own record, forwarded exactly as it stated it.
            "feature": occurrence["record"],
            "feature_type": occurrence["record_type"],
            "feature_schema_version": occurrence["record_schema_version"],
            "draftwright": {
                # The plain answer first, so a reader need not learn the vocabulary below it.
                "acted_on": occurrence["disposition"] in _ACTED_ON,
                "disposition": occurrence["disposition"],
                "reason": occurrence["reason_code"],
                "owners": [owner["id"] for owner in occurrence["owners"]],
            },
        }
        for occurrence in occurrences
    ]


def _missed(evidence) -> dict[str, Any]:
    """Geometry no accepted feature claimed, as the provider accounts for it.

    Unclaimed does not mean missed. Stock, background and deliberately plain faces are unclaimed
    too, and they are in the denominator. This is a place to start looking, not a defect list.

    The other half — what the recogniser proposed and then rejected, and which families it did
    not evaluate — is the provider's to state and needs a second recognition run to obtain.
    Joining it needs a provider API that explains an already-completed result.
    """

    association = evidence.association
    return {
        "unclaimed_faces": _faces(evidence, association.unassociated_faces),
        "face_count": {
            "total": association.face_count.total,
            "claimed": association.face_count.associated,
            "unclaimed": association.face_count.unassociated,
        },
        "rejected_candidates": {
            "available": False,
            "reason": "provider explanation is only available from a second recognition run",
        },
    }


def inspect_step(path: str | PathLike[str]) -> dict[str, JsonValue]:
    """Return the recognition evidence for the STEP file at *path*.

    The source is resolved once and read once; recognition consumes a private copy of those
    exact hashed bytes, so replacing a mutable or symlinked source mid-inspection cannot make
    the document describe two different files.

    Exactly one aggregate recognition run happens, and its evidence, model and conversion-time
    ownership are reused as-is. No drawing build, view projection, annotation placement, render,
    export or physical lint path runs.

    Raises:
        OSError: the path could not be read (missing, a directory, permissions).
        InspectionUnavailableError: the bytes are not a readable solid STEP body, or the run
            cannot state its evidence truthfully.
    """

    # Imported here, not at module scope: `from draftwright import inspect_step` must stay
    # sub-second, and only an actual inspection needs the ~5 s CAD kernel (#313).
    from draftwright.builder import _detect_part_model_analysis

    source_name = Path(path).name
    resolved = Path(path).resolve()
    source_bytes = resolved.read_bytes()

    with TemporaryDirectory(prefix="draftwright-inspect-") as directory:
        snapshot = Path(directory) / resolved.name
        snapshot.write_bytes(source_bytes)
        try:
            # `pmi="off"` keeps recognition geometry-only (ADR 0013): no PMI record is lowered
            # into the IR, so an authored annotation cannot change which feature owns what.
            model, analysis = _detect_part_model_analysis(snapshot, pmi="off")
        except ValueError as error:
            raise InspectionUnavailableError(
                f"could not read solid STEP geometry from {source_name!r}"
            ) from error
        return _document(model, analysis, source_name, source_bytes)


def _document(
    model: PartModel, analysis: Analysis, source_name: str, source_bytes: bytes
) -> dict[str, JsonValue]:
    if not analysis.part.solids():
        raise InspectionUnavailableError(f"{source_name!r} carries no solid body to inspect")

    frame_status = (analysis.recognition_frame_decision or {}).get("status")
    if frame_status != _SUPPORTED_FRAME_STATUS:
        raise InspectionUnavailableError(
            "inspection reports raw caller coordinates only; this run recognised with frame "
            f"status {frame_status!r}"
        )

    document = {
        "schema": INSPECTION_SCHEMA,
        "schema_version": INSPECTION_SCHEMA_VERSION,
        "source": {
            # The basename only: an absolute path is caller-machine detail, not evidence.
            "name": source_name,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
        "producer": _producer(),
        "found": _found(analysis.recognition_evidence, analysis.recognition_ownership, model),
        "missed": _missed(analysis.recognition_evidence),
    }
    try:
        # The one strict-JSON gate: isolates the document from live objects, renders tuples as
        # arrays, and rejects NaN/Infinity rather than emitting a value JSON cannot state.
        return cast("dict[str, JsonValue]", _json_value(document))
    except ValueError as error:
        raise InspectionUnavailableError(f"a value cannot be stated as JSON: {error}") from error


__all__ = [
    "INSPECTION_SCHEMA",
    "INSPECTION_SCHEMA_VERSION",
    "InspectionUnavailableError",
    "inspect_step",
]
