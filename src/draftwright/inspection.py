"""Read-only recognition evidence for a STEP file.

`inspect_step` exists so a person or an agent can find and correct two different kinds of
failing without building a drawing first:

* failings in **recognition** — the recogniser found the wrong thing, or missed something; and
* failings in **conversion** — recognition found it, and Draftwright did nothing useful with it.

The document therefore says three things, and keeps them apart:

* ``found`` — every feature the recogniser accepted, exactly as it stated it, and beside each
  one what Draftwright did with it;
* ``faces`` / ``missed`` — report-local source-face evidence and geometry no accepted feature
  claimed; and
* ``source`` / ``producer`` — which bytes were read and which versions read them, so a finding
  can be reproduced or filed upstream.

``missed`` reports both geometry that no accepted occurrence claimed and the provider's bounded
candidate-lifecycle explanation from the same recognition run. The closed candidate graph links
rejected evidence to report-local source-face IDs. These are evidence for review, not recognition
recall: ordinary stock faces are unclaimed, detector candidates can overlap, and accepted
candidate counts precede public projection and deduplication.

Nothing here is a completeness or readiness claim. An unclaimed face is not proof of a missed
feature: stock and plain faces are unclaimed too.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from os import PathLike
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast

from draftwright.reporting import (
    JsonValue,
    ReportUnavailableError,
    json_value,
    producer,
    project_occurrences,
)

if TYPE_CHECKING:  # typing only — naming these must not cost the CAD kernel at import
    from draftwright._core import Analysis
    from draftwright.model import PartModel

INSPECTION_SCHEMA = "draftwright-step-inspection"
INSPECTION_SCHEMA_VERSION = 4

# Version 1 reports raw caller coordinates only. Framed recognition moves geometry into a
# provider working frame, and b123d-recognisers#493 cannot yet tell a consumer whether a refused
# framed run had already recognised — so refuse rather than report working-frame values as
# caller coordinates (ADR 3, was 0020).
_SUPPORTED_FRAME_STATUS = "raw"

# The recognition options that change what this document says. PMI lowering can rewrite a
# grouped hole member into a singleton owner, so two runs over identical bytes can disagree
# about what Draftwright did with a finding. The document records the mode rather than leaving
# `source.sha256` to imply a reproducibility it does not have.
_PMI_MODES = frozenset({"off", "report", "annotate"})

# Draftwright acted on the feature: it is represented by an IR feature of its own, or absorbed
# into one. Every other disposition means the recogniser found something the drawing does not
# use, which is the conversion failing this document exists to surface.
_ACTED_ON = frozenset({"represented", "absorbed"})

_FAMILY_EVALUATIONS = frozenset({"evaluated", "not-applicable"})
_RECOGNITION_OUTCOMES = frozenset({"accepted", "rejected"})
_EXPLANATION_COVERAGE = frozenset({"bounded"})
_DETECTOR_FAMILIES = frozenset(
    {
        "countersinks",
        "holes",
        "double_d_bores",
        "bosses",
        "polygonal_bosses",
        "polygonal_stock",
        "channels",
        "slots",
        "rectangular_blind_slots",
        "round_bottom_blind_slots",
        "grooves",
        "flats",
        "pockets",
        "prismatic_pockets",
        "edge_open_circular_pockets",
        "edge_open_prismatic_recesses",
        "section_recesses",
        "pads",
        "repeating_radial_profiles",
        "turned_steps",
        "step_levels",
        "risers",
        "chamfers",
        "angled_steps",
        "paired_ramp_steps",
        "gusset_ribs",
        "through_steps",
        "circular_blind_steps",
        "passages",
        "oriented_slots",
        "blends",
        "fillets",
        "plates",
    }
)
_RECONCILIATION_REASONS = frozenset(
    {
        "default.accepted",
        "recess.prismatic_superseded_by_pocket",
        "recess.pocket_superseded_by_rectangular_blind_slot",
        "recess.pocket_superseded_by_edge_open_circular_pocket",
        "recess.pocket_superseded_by_passage",
        "recess.pocket_superseded_by_prismatic",
        "recess.slot_superseded_by_pocket",
        "recess.slot_superseded_by_prismatic",
        "recess.slot_superseded_by_passage",
        "recess.passage_superseded_by_slot",
        "recess.passage_superseded_by_oriented_slot",
        "bevel.chamfer_superseded_by_angled_step",
        "blend.fillet_superseded_by_circular_blind_step",
        "blend.chain_superseded_by_fillet",
        "bore.hole_superseded_by_double_d_bore",
        "turned.step_groove_compatible",
        "turned.groove_step_compatible",
    }
)
_DIAGNOSTIC_CODES = frozenset({"unsupported.subdivided_angled_step_terminal"})
_DIAGNOSTIC_STATUSES = frozenset({"unsupported"})


class InspectionUnavailableError(RuntimeError):
    """The STEP source cannot yield a truthful inspection document."""


def _json_value_or_refuse(value: Any) -> Any:
    """Apply the strict-JSON gate, converting its refusal into the documented failure.

    NaN and Infinity mean a measurement that cannot be stated, which is an inspection failure
    rather than a bare `ValueError` from `json` escaping the contract.
    """

    try:
        return json_value(value)
    except ValueError as error:
        raise InspectionUnavailableError(f"a value cannot be stated as JSON: {error}") from error


def _vector(value) -> list[float]:
    return [float(value.X), float(value.Y), float(value.Z)]


def _face(face) -> dict[str, Any]:
    """Describe one unclaimed face by bounded geometry, never by a reference or topology index."""

    try:
        # Not `getattr(face, "geom_type", None)`: the property raises on a degenerate face
        # rather than returning None, so a default cannot stand in for it.
        surface = face.geom_type.name.lower()
        # `Face.center()` is CenterOf.GEOMETRY — the parameter-space mid-point, which lies on
        # the surface. It is NOT the area centroid: on a cylindrical hole wall the two are 5 mm
        # apart, and the area centroid sits on the axis, inside the material rather than on the
        # face. A reader locating this face wants the point on it, so the field is named for
        # what it is rather than borrowing a word that would be wrong.
        position = face.center()
        box = face.bounding_box()
        area = float(face.area)
        bounds = {"min": _vector(box.min), "max": _vector(box.max)}
    except ValueError as error:
        raise InspectionUnavailableError("a source face cannot be described") from error
    return {
        "surface": surface,
        "area": area,
        "position": _vector(position),
        "bbox": bounds,
    }


def _faces(evidence, references) -> list[dict[str, Any]]:
    """Order face descriptions by their own serialized values.

    The provider hands faces back as an unordered set of address-hashed references, so without
    this the document changes between runs. Two faces that sort equal serialize identically, so
    their relative order cannot be observed — no identity is invented.
    """

    described = [_face(evidence.face(reference)) for reference in references]
    # One strict-JSON gate, not two: this sort key rejected NaN independently of `json_value`
    # and escaped as a bare `ValueError`, outside the documented failure contract.
    return sorted(described, key=lambda item: json.dumps(_json_value_or_refuse(item)))


def _face_set(evidence, references, all_faces: frozenset, *, field: str) -> frozenset:
    """Validate one same-authority face-reference set and return it unchanged."""

    if type(references) is not frozenset:
        raise InspectionUnavailableError(f"provider {field} is not a frozen face set")
    if not references <= all_faces:
        raise InspectionUnavailableError(f"provider {field} contains a foreign face reference")
    try:
        for reference in references:
            evidence.face(reference)
    except (TypeError, ValueError) as error:
        raise InspectionUnavailableError(
            f"provider {field} contains a foreign face reference"
        ) from error
    return references


def _candidate_nodes(evidence) -> list[dict[str, Any]]:
    """Close the public rejected-candidate graph in its documented source order."""

    roots = getattr(evidence, "rejected_candidates", None)
    if type(roots) is not tuple:
        raise InspectionUnavailableError("provider rejected-candidate roster is unavailable")
    queue = deque(roots)
    root_set = set(roots)
    if len(root_set) != len(roots):
        raise InspectionUnavailableError("provider rejected-candidate roster contains duplicates")
    seen: set = set()
    nodes: list[dict[str, Any]] = []
    while queue:
        reference = queue.popleft()
        if reference in seen:
            continue
        try:
            family = evidence.candidate_family(reference)
            outcome = _enum_value(
                evidence.candidate_outcome(reference),
                field="candidate outcome",
                allowed=_RECOGNITION_OUTCOMES,
            )
            reason = _enum_value(
                evidence.candidate_reason(reference),
                field="candidate reason",
                allowed=_RECONCILIATION_REASONS,
            )
            defining = evidence.candidate_defining_faces(reference)
            constituent = evidence.candidate_constituent_faces(reference)
            related = evidence.related_candidates(reference)
        except (AttributeError, TypeError, ValueError) as error:
            raise InspectionUnavailableError(
                "provider candidate evidence contains a foreign or stale reference"
            ) from error
        if type(family) is not str or family not in _DETECTOR_FAMILIES:
            raise InspectionUnavailableError("provider candidate evidence has an invalid family")
        if type(defining) is not frozenset or type(constituent) is not frozenset:
            raise InspectionUnavailableError("provider candidate evidence has invalid face sets")
        if not defining <= constituent:
            raise InspectionUnavailableError(
                "provider candidate defining faces are not a subset of constituent faces"
            )
        if type(related) is not tuple or len(set(related)) != len(related):
            raise InspectionUnavailableError(
                "provider candidate evidence has an invalid related-candidate roster"
            )
        seen.add(reference)
        nodes.append(
            {
                "reference": reference,
                "rejected_root": reference in root_set,
                "family": family,
                "outcome": outcome,
                "reason": reason,
                "defining": defining,
                "constituent": constituent,
                "related": related,
            }
        )
        queue.extend(candidate for candidate in related if candidate not in seen)
    if any(node["outcome"] != "rejected" for node in nodes if node["rejected_root"]):
        raise InspectionUnavailableError(
            "provider rejected-candidate roster contains an accepted candidate"
        )
    return nodes


def _source_faces(evidence, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict]:
    """Build deterministic report-local face IDs without serializing provider references.

    Provider face references are unordered. Geometry alone cannot distinguish symmetric faces,
    so the stable sort key also contains every reported accepted/candidate/unclaimed membership.
    Faces tied on that complete key are observationally interchangeable: every emitted link
    contains either all of them or none of them.
    """

    source = getattr(evidence, "faces", None)
    if type(source) is not frozenset or not source:
        raise InspectionUnavailableError("provider source-face roster is unavailable")
    all_faces = source
    accepted: list[tuple[frozenset, frozenset]] = []
    for occurrence in evidence.features:
        accepted.append(
            (
                _face_set(
                    evidence,
                    evidence.defining_faces(occurrence),
                    all_faces,
                    field="accepted defining faces",
                ),
                _face_set(
                    evidence,
                    evidence.constituent_faces(occurrence),
                    all_faces,
                    field="accepted constituent faces",
                ),
            )
        )
    for node in candidates:
        node["defining"] = _face_set(
            evidence, node["defining"], all_faces, field="candidate defining faces"
        )
        node["constituent"] = _face_set(
            evidence, node["constituent"], all_faces, field="candidate constituent faces"
        )
    unassociated = _face_set(
        evidence,
        evidence.association.unassociated_faces,
        all_faces,
        field="unassociated faces",
    )

    sortable = []
    for reference in all_faces:
        description = _face(evidence.face(reference))
        membership = (
            tuple(
                (index, reference in defining, reference in constituent)
                for index, (defining, constituent) in enumerate(accepted)
                if reference in defining or reference in constituent
            ),
            tuple(
                (index, reference in node["defining"], reference in node["constituent"])
                for index, node in enumerate(candidates)
                if reference in node["defining"] or reference in node["constituent"]
            ),
            reference in unassociated,
        )
        sortable.append(
            (
                json.dumps(_json_value_or_refuse(description), sort_keys=True),
                membership,
                reference,
                description,
            )
        )
    sortable.sort(key=lambda item: (item[0], item[1]))
    face_ids: dict = {}
    rows = []
    for index, (_description_key, _membership, reference, description) in enumerate(
        sortable, start=1
    ):
        face_id = f"face:{index}"
        face_ids[reference] = face_id
        rows.append({"id": face_id, **description})
    return rows, face_ids


def _face_ids(references: frozenset, face_ids: dict) -> list[str]:
    try:
        values = [face_ids[reference] for reference in references]
    except (KeyError, ValueError) as error:
        raise InspectionUnavailableError(
            "provider face reference is absent from the source roster"
        ) from error
    return sorted(values, key=lambda value: int(value.split(":", 1)[1]))


def _found(evidence, ownership, model, face_ids: dict, occurrences=None) -> list[dict[str, Any]]:
    """Every accepted feature, as the recogniser stated it, beside what Draftwright did with it.

    The occurrence ledger comes from the shared report projector, which refuses an unclassified
    ownership ledger — so a document is never produced by dropping a feature Draftwright cannot
    account for.
    """

    if occurrences is None:
        try:
            occurrences, _requirements, _summary = project_occurrences(evidence, ownership, model)
        except ReportUnavailableError as error:
            raise InspectionUnavailableError(str(error)) from error

    found = []
    for reference, occurrence in zip(evidence.features, occurrences, strict=True):
        defining = evidence.defining_faces(reference)
        constituent = evidence.constituent_faces(reference)
        if type(defining) is not frozenset or type(constituent) is not frozenset:
            raise InspectionUnavailableError("provider accepted occurrence has invalid face sets")
        if not defining <= constituent:
            raise InspectionUnavailableError(
                "provider accepted defining faces are not a subset of constituent faces"
            )
        found.append(
            {
                "id": occurrence["id"],
                "family": occurrence["family"],
                "defining_face_ids": _face_ids(defining, face_ids),
                "constituent_face_ids": _face_ids(constituent, face_ids),
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
        )
    return found


def _enum_value(value, *, field: str, allowed: frozenset[str]) -> str:
    projected = getattr(value, "value", None)
    if type(projected) is not str or projected not in allowed:
        raise InspectionUnavailableError(f"provider explanation has invalid {field}")
    return projected


def _count(value, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise InspectionUnavailableError(f"provider explanation has invalid {field}")
    return value


def _candidate_lifecycle(evidence) -> dict[str, Any]:
    """Project the admitted bounded explanation from this exact evidence run."""

    report = getattr(evidence, "report", None)
    if report is None:
        raise InspectionUnavailableError(
            "same-run provider candidate-lifecycle explanation is unavailable"
        )
    if getattr(report, "result", None) is not evidence.result:
        raise InspectionUnavailableError(
            "provider explanation does not belong to this recognition result"
        )
    coverage = _enum_value(
        getattr(report, "coverage", None),
        field="coverage",
        allowed=_EXPLANATION_COVERAGE,
    )
    source_families = getattr(report, "detector_families", None)
    if type(source_families) is not tuple or not source_families:
        raise InspectionUnavailableError(
            "provider explanation has no closed detector-family roster"
        )

    families = []
    names: set[str] = set()
    for source in source_families:
        family = getattr(source, "family", None)
        if type(family) is not str or not family or family in names:
            raise InspectionUnavailableError(
                "provider explanation has an invalid or duplicate detector family"
            )
        names.add(family)
        evaluation = _enum_value(
            getattr(source, "evaluation", None),
            field="family evaluation",
            allowed=_FAMILY_EVALUATIONS,
        )
        proposed = _count(getattr(source, "proposed", None), field="proposed count")
        accepted = _count(getattr(source, "accepted", None), field="accepted count")
        rejected = _count(getattr(source, "rejected", None), field="rejected count")
        source_dispositions = getattr(source, "dispositions", None)
        if type(source_dispositions) is not tuple:
            raise InspectionUnavailableError(
                "provider explanation has an invalid disposition roster"
            )
        dispositions = []
        disposition_keys: set[tuple[str, str]] = set()
        outcome_counts = {outcome: 0 for outcome in _RECOGNITION_OUTCOMES}
        for source_disposition in source_dispositions:
            reason = _enum_value(
                getattr(source_disposition, "reason", None),
                field="reconciliation reason",
                allowed=_RECONCILIATION_REASONS,
            )
            outcome = _enum_value(
                getattr(source_disposition, "outcome", None),
                field="recognition outcome",
                allowed=_RECOGNITION_OUTCOMES,
            )
            occurrences = _count(
                getattr(source_disposition, "occurrences", None),
                field="disposition occurrence count",
            )
            related = _count(
                getattr(source_disposition, "related_occurrences", None),
                field="related occurrence count",
            )
            disposition_key = (reason, outcome)
            if disposition_key in disposition_keys:
                raise InspectionUnavailableError(
                    f"provider explanation repeats a disposition for detector family {family!r}"
                )
            disposition_keys.add(disposition_key)
            outcome_counts[outcome] += occurrences
            dispositions.append(
                {
                    "reason": reason,
                    "outcome": outcome,
                    "occurrences": occurrences,
                    "related_occurrences": related,
                }
            )
        if proposed != accepted + rejected or outcome_counts != {
            "accepted": accepted,
            "rejected": rejected,
        }:
            raise InspectionUnavailableError(
                f"provider explanation counts disagree for detector family {family!r}"
            )
        if evaluation == "not-applicable" and (proposed or dispositions):
            raise InspectionUnavailableError(
                f"not-applicable detector family {family!r} reports candidate activity"
            )
        families.append(
            {
                "family": family,
                "evaluation": evaluation,
                "proposed": proposed,
                "accepted": accepted,
                "rejected": rejected,
                "dispositions": dispositions,
            }
        )
    if names != _DETECTOR_FAMILIES:
        raise InspectionUnavailableError("provider explanation detector-family roster changed")

    source_diagnostics = getattr(report, "diagnostics", None)
    if type(source_diagnostics) is not tuple:
        raise InspectionUnavailableError("provider explanation has invalid diagnostics")
    diagnostics = []
    for source in source_diagnostics:
        family = getattr(source, "family", None)
        axis = getattr(source, "axis", None)
        at = getattr(source, "at", None)
        if (
            type(family) is not str
            or family not in names
            or axis not in {"x", "y", "z"}
            or type(at) is not tuple
            or len(at) != 3
        ):
            raise InspectionUnavailableError("provider explanation has invalid diagnostic context")
        diagnostics.append(
            {
                "code": _enum_value(
                    getattr(source, "code", None),
                    field="diagnostic code",
                    allowed=_DIAGNOSTIC_CODES,
                ),
                "status": _enum_value(
                    getattr(source, "status", None),
                    field="diagnostic status",
                    allowed=_DIAGNOSTIC_STATUSES,
                ),
                "family": family,
                "axis": axis,
                "at": list(at),
                "raw_outer_edges": _count(
                    getattr(source, "raw_outer_edges", None),
                    field="diagnostic raw-edge count",
                ),
                "effective_outer_sides": _count(
                    getattr(source, "effective_outer_sides", None),
                    field="diagnostic effective-side count",
                ),
            }
        )
    return {
        "available": True,
        "coverage": coverage,
        "scope": "detector-candidate-lifecycle",
        "recognition_recall": "not-assessed",
        "families": families,
        "diagnostics": diagnostics,
    }


def _candidate_evidence(
    lifecycle: dict[str, Any],
    candidates: list[dict[str, Any]],
    face_ids: dict,
) -> list[dict[str, Any]]:
    """Project and reconcile the closed public candidate graph from this run."""

    candidate_ids = {
        node["reference"]: f"candidate:{index}" for index, node in enumerate(candidates, start=1)
    }
    family_rows = {row["family"]: row for row in lifecycle["families"]}
    root_counts: dict[str, int] = {family: 0 for family in family_rows}
    disposition_counts: dict[tuple[str, str, str], tuple[int, int]] = {}
    for node in candidates:
        if not node["rejected_root"]:
            continue
        family = node["family"]
        root_counts[family] += 1
        key = (family, node["reason"], node["outcome"])
        occurrences, related = disposition_counts.get(key, (0, 0))
        disposition_counts[key] = (occurrences + 1, related + len(node["related"]))

    for family, row in family_rows.items():
        if root_counts[family] != row["rejected"]:
            raise InspectionUnavailableError(
                f"provider rejected-candidate roster disagrees for detector family {family!r}"
            )
        for disposition in row["dispositions"]:
            if disposition["outcome"] != "rejected":
                continue
            key = (family, disposition["reason"], disposition["outcome"])
            actual = disposition_counts.get(key, (0, 0))
            expected = (
                disposition["occurrences"],
                disposition["related_occurrences"],
            )
            if actual != expected:
                raise InspectionUnavailableError(
                    "provider rejected-candidate relationships disagree for detector family "
                    f"{family!r}"
                )

    projected = []
    for node in candidates:
        try:
            related_ids = [candidate_ids[reference] for reference in node["related"]]
        except KeyError as error:
            raise InspectionUnavailableError(
                "provider related candidate is absent from the candidate graph"
            ) from error
        projected.append(
            {
                "id": candidate_ids[node["reference"]],
                "source": "rejected-roster" if node["rejected_root"] else "related",
                "family": node["family"],
                "outcome": node["outcome"],
                "reason": node["reason"],
                "defining_face_ids": _face_ids(node["defining"], face_ids),
                "constituent_face_ids": _face_ids(node["constituent"], face_ids),
                "related_candidate_ids": related_ids,
            }
        )
    return projected


def _missed(evidence, candidates: list[dict[str, Any]], face_ids: dict) -> dict[str, Any]:
    """Geometry no accepted feature claimed, as the provider accounts for it.

    Unclaimed does not mean missed. Stock, background and deliberately plain faces are unclaimed
    too, and they are in the denominator. This is a place to start looking, not a defect list.

    Candidate lifecycle counts are detector decisions before public projection and
    deduplication. They explain this run's bounded decisions but do not measure recall.
    """

    association = evidence.association
    lifecycle = _candidate_lifecycle(evidence)
    lifecycle["candidates"] = _candidate_evidence(lifecycle, candidates, face_ids)
    return {
        "unclaimed_faces": [
            {"id": face_ids[reference], **_face(evidence.face(reference))}
            for reference in sorted(
                association.unassociated_faces,
                key=lambda reference: int(face_ids[reference].split(":", 1)[1]),
            )
        ],
        "face_count": {
            "total": association.face_count.total,
            "claimed": association.face_count.associated,
            "unclaimed": association.face_count.unassociated,
        },
        "rejected_candidates": lifecycle,
    }


def inspect_step(path: str | PathLike[str]) -> dict[str, JsonValue]:
    """Return the recognition evidence for the STEP file at *path*.

    The source is resolved once and read once; recognition consumes a private copy of those
    exact hashed bytes, so replacing a mutable or symlinked source mid-inspection cannot make
    the document describe two different files.

    Exactly one aggregate recognition run happens, and its evidence, model and conversion-time
    ownership are reused as-is. No drawing build, view projection, annotation placement, render,
    export or physical lint path runs.

    It does, however, share the engine's ONE detect seam, which sizes the part while
    detecting, so some scale-selection and dimension-planning work is done and discarded.
    Measured, that is about 0.02% of an inspection — the cost is recognition and STEP
    parsing — so the shared seam stays rather than growing a second one that could not be
    checked against the drawing path. Evidence and method:
    `docs/research/1462-inspect-seam-cost.md`.

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
            # `pmi="off"` keeps recognition geometry-only (ADR 3, was 0013): no PMI record is lowered
            # into the IR, so an authored annotation cannot change which feature owns what.
            model, analysis = _detect_part_model_analysis(snapshot, pmi="off")
        except ValueError as error:
            raise InspectionUnavailableError(
                f"could not read solid STEP geometry from {source_name!r}"
            ) from error
        return _document(model, analysis, source_name, source_bytes)


def _document(
    model: PartModel,
    analysis: Analysis,
    source_name: str,
    source_bytes: bytes,
    *,
    _occurrences=None,
) -> dict[str, JsonValue]:
    if not analysis.part.solids():
        raise InspectionUnavailableError(f"{source_name!r} carries no solid body to inspect")

    frame_status = (analysis.recognition_frame_decision or {}).get("status")
    # Read from the run, never taken from the caller: a provenance field a caller can assert
    # is a field that can contradict the run it describes, which is the untruthful document
    # this field exists to prevent.
    pmi_mode = analysis.pmi_mode
    if pmi_mode not in _PMI_MODES:
        raise InspectionUnavailableError(f"unknown recognition PMI mode {pmi_mode!r}")

    if frame_status != _SUPPORTED_FRAME_STATUS:
        raise InspectionUnavailableError(
            "inspection reports raw caller coordinates only; this run recognised with frame "
            f"status {frame_status!r}"
        )

    evidence = analysis.recognition_evidence
    candidates = _candidate_nodes(evidence)
    faces, face_ids = _source_faces(evidence, candidates)
    document = {
        "schema": INSPECTION_SCHEMA,
        "schema_version": INSPECTION_SCHEMA_VERSION,
        "source": {
            # The basename only: an absolute path is caller-machine detail, not evidence.
            "name": source_name,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
        "producer": producer(),
        # The run options that determined the content below. Without this, two documents over
        # identical bytes can disagree and neither says why.
        "run": {"pmi_mode": pmi_mode},
        "faces": faces,
        "found": _found(
            evidence,
            analysis.recognition_ownership,
            model,
            face_ids,
            _occurrences,
        ),
        "missed": _missed(evidence, candidates, face_ids),
    }
    # Isolates the document from live objects, renders tuples as arrays, and rejects
    # NaN/Infinity rather than emitting a value JSON cannot state.
    return cast("dict[str, JsonValue]", _json_value_or_refuse(document))


__all__ = [
    "INSPECTION_SCHEMA",
    "INSPECTION_SCHEMA_VERSION",
    "InspectionUnavailableError",
    "inspect_step",
]
