"""Quiddity 0.3.1 rejected-candidate evidence stays exact and bounded."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from draftwright import InspectionUnavailableError, inspect_step
from draftwright import inspection as inspection_module

_CTC_01 = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap203.stp"


@pytest.fixture(scope="module")
def ctc_document():
    return inspect_step(_CTC_01)


@pytest.fixture(scope="module")
def ctc_evidence():
    from draftwright.builder import _detect_part_model_analysis

    _model, analysis = _detect_part_model_analysis(_CTC_01, pmi="off")
    return analysis.recognition_evidence


@pytest.fixture(scope="module")
def foreign_evidence():
    from draftwright.builder import _detect_part_model_analysis

    _model, analysis = _detect_part_model_analysis(_CTC_01, pmi="off")
    return analysis.recognition_evidence


def test_report_local_face_and_candidate_graph_is_closed(ctc_document) -> None:
    faces = {row["id"]: row for row in ctc_document["faces"]}
    found = ctc_document["found"]
    lifecycle = ctc_document["missed"]["rejected_candidates"]
    candidates = {row["id"]: row for row in lifecycle["candidates"]}

    assert len(faces) == ctc_document["missed"]["face_count"]["total"] == 139
    assert len(candidates) == 16
    assert len([row for row in candidates.values() if row["source"] == "rejected-roster"]) == 8
    assert len([row for row in candidates.values() if row["source"] == "related"]) == 8

    for row in (*found, *candidates.values()):
        defining = set(row["defining_face_ids"])
        constituent = set(row["constituent_face_ids"])
        assert defining <= constituent <= faces.keys()
    for row in candidates.values():
        assert set(row["related_candidate_ids"]) <= candidates.keys()

    rejected_faces = {
        face_id
        for row in candidates.values()
        if row["source"] == "rejected-roster"
        for face_id in row["constituent_face_ids"]
    }
    accepted_faces = {face_id for row in found for face_id in row["constituent_face_ids"]}
    assert rejected_faces and rejected_faces <= accepted_faces, (
        "the fixture must retain the permitted accepted/rejected evidence overlap"
    )
    assert set(faces) - accepted_faces - rejected_faces, (
        "faces carrying neither accepted nor rejected evidence must remain visible"
    )

    payload = json.dumps(ctc_document)
    for forbidden in ("CandidateRef", "FaceRef", "TopoDS", "object at ", "0x"):
        assert forbidden not in payload


def test_rejected_roots_and_direct_relationships_reconcile_exactly(ctc_document) -> None:
    lifecycle = ctc_document["missed"]["rejected_candidates"]
    blends = next(row for row in lifecycle["families"] if row["family"] == "blends")
    rejected = [
        row
        for row in lifecycle["candidates"]
        if row["source"] == "rejected-roster" and row["family"] == "blends"
    ]

    assert len(rejected) == blends["rejected"] == 8
    assert {row["outcome"] for row in rejected} == {"rejected"}
    assert {row["reason"] for row in rejected} == {"blend.chain_superseded_by_fillet"}
    assert sum(len(row["related_candidate_ids"]) for row in rejected) == 8
    related = {row["id"]: row for row in lifecycle["candidates"] if row["source"] == "related"}
    assert all(
        related[related_id]["outcome"] == "accepted"
        for row in rejected
        for related_id in row["related_candidate_ids"]
    )


def test_candidate_and_face_ids_are_deterministic_for_the_same_bytes(ctc_document) -> None:
    assert inspect_step(_CTC_01) == ctc_document


def test_foreign_candidate_reference_fails_closed(ctc_evidence, foreign_evidence) -> None:
    class ForeignRoot:
        rejected_candidates = (foreign_evidence.rejected_candidates[0],)

        def __getattr__(self, name):
            return getattr(ctc_evidence, name)

    with pytest.raises(InspectionUnavailableError, match="foreign or stale"):
        inspection_module._candidate_nodes(ForeignRoot())


def test_candidate_face_subset_and_authority_fail_closed(ctc_evidence, foreign_evidence) -> None:
    root = ctc_evidence.rejected_candidates[0]

    class BrokenSubset:
        def __getattr__(self, name):
            return getattr(ctc_evidence, name)

        def candidate_constituent_faces(self, candidate):
            if candidate is root:
                return frozenset()
            return ctc_evidence.candidate_constituent_faces(candidate)

    with pytest.raises(InspectionUnavailableError, match="not a subset"):
        inspection_module._candidate_nodes(BrokenSubset())

    nodes = inspection_module._candidate_nodes(ctc_evidence)
    foreign_face = next(iter(foreign_evidence.faces))
    nodes[0]["defining"] = nodes[0]["defining"] | {foreign_face}
    nodes[0]["constituent"] = nodes[0]["constituent"] | {foreign_face}
    with pytest.raises(InspectionUnavailableError, match="foreign face reference"):
        inspection_module._source_faces(ctc_evidence, nodes)


def test_candidate_roster_disagreement_fails_closed(ctc_evidence) -> None:
    candidates = inspection_module._candidate_nodes(ctc_evidence)
    _faces, face_ids = inspection_module._source_faces(ctc_evidence, candidates)
    lifecycle = copy.deepcopy(inspection_module._candidate_lifecycle(ctc_evidence))
    blends = next(row for row in lifecycle["families"] if row["family"] == "blends")
    blends["rejected"] += 1

    with pytest.raises(InspectionUnavailableError, match="roster disagrees"):
        inspection_module._candidate_evidence(lifecycle, candidates, face_ids)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("missing_roster", "roster is unavailable"),
        ("duplicate_root", "roster contains duplicates"),
        ("invalid_family", "invalid family"),
        ("mutable_faces", "invalid face sets"),
        ("mutable_related", "invalid related-candidate roster"),
        ("accepted_root", "contains an accepted candidate"),
    ],
)
def test_malformed_candidate_contract_fails_closed(ctc_evidence, damage, message) -> None:
    root = ctc_evidence.rejected_candidates[0]
    related = ctc_evidence.related_candidates(root)[0]
    broken_roster = {
        "missing_roster": [],
        "duplicate_root": (root, root),
    }.get(damage, (root,))

    class BrokenEvidence:
        rejected_candidates = broken_roster

        def __getattr__(self, name):
            return getattr(ctc_evidence, name)

        def candidate_family(self, candidate):
            if damage == "invalid_family" and candidate is root:
                return "invented"
            return ctc_evidence.candidate_family(candidate)

        def candidate_outcome(self, candidate):
            if damage == "accepted_root" and candidate is root:
                return ctc_evidence.candidate_outcome(related)
            return ctc_evidence.candidate_outcome(candidate)

        def candidate_defining_faces(self, candidate):
            if damage == "mutable_faces" and candidate is root:
                return set(ctc_evidence.candidate_defining_faces(candidate))
            return ctc_evidence.candidate_defining_faces(candidate)

        def related_candidates(self, candidate):
            if damage == "mutable_related" and candidate is root:
                return list(ctc_evidence.related_candidates(candidate))
            return ctc_evidence.related_candidates(candidate)

    with pytest.raises(InspectionUnavailableError, match=message):
        inspection_module._candidate_nodes(BrokenEvidence())
