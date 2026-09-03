"""#1438 — released framed evidence reaches exact ownership and report v1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from b123d_recognisers import (
    FramedRecognitionEvidence,
    PreparedFramedPart,
    RefusedFramedEvidence,
)
from b123d_recognisers.evidence import FramedEvidenceRefusalReason
from build123d import Axis, Box, Cylinder, Pos
from jsonschema.validators import validator_for

from draftwright import ReportUnavailableError, build_drawing
from draftwright import reporting as reporting_module

_SCHEMA_PATH = Path(__file__).parents[1] / "docs/reference/draftwright-report-v1.schema.json"


def _part():
    return Box(40, 30, 20) - Pos(15, 10, 0) * Box(20, 20, 30) - Pos(8, 8, 0) * Cylinder(2, 20)


def test_framed_evidence_keeps_one_authority_for_result_ownership_and_report() -> None:
    caller = Pos(17, -9, 6) * _part().rotate(Axis((0, 0, 0), (1, 1, 0)), 37)
    drawing = build_drawing(caller, framed_recognition=True)

    evidence = drawing.recognition_evidence()
    ownership = drawing.recognition_ownership()
    assert type(evidence) is FramedRecognitionEvidence
    assert evidence.result is drawing.recognition()
    assert evidence.part is drawing.working_part
    assert evidence.caller_part is drawing.part
    assert ownership is not None
    assert ownership.evidence is evidence
    assert all(ownership.status(occurrence) != "unclassified" for occurrence in evidence.features)

    report = drawing.report()
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = validator_for(schema)
    validator.check_schema(schema)
    validator(schema).validate(report)
    frame = evidence.frame
    assert report["recognition"]["coordinates"] == {
        "record_space": "provider-working",
        "caller_from_record": {
            "kind": "rigid-frame",
            "origin_mm": list(frame.origin),
            "x_axis": list(frame.x),
            "y_axis": list(frame.y),
            "z_axis": list(frame.z),
            "gauge": frame.gauge.value,
        },
    }
    assert report["recognition"]["summary"]["total"] == len(evidence.features)
    snapshot = reporting_module._generation_snapshot(
        evidence=evidence,
        ownership=ownership,
        model=drawing.model(),
        source=None,
        source_sha256=None,
    )
    assert snapshot["coordinates"] == report["recognition"]["coordinates"]


def test_framed_face_refs_resolve_exact_working_and_caller_partners() -> None:
    caller = Pos(17, -9, 6) * _part().rotate(Axis.X, 31)
    drawing = build_drawing(caller, framed_recognition=True)
    evidence = drawing.recognition_evidence()
    assert type(evidence) is FramedRecognitionEvidence

    assert len(evidence.faces) == len(drawing.working_part.faces()) == len(caller.faces())
    for reference in evidence.faces:
        working = evidence.face(reference)
        source = evidence.caller_face(reference)
        assert (
            sum(working.wrapped.IsSame(face.wrapped) for face in drawing.working_part.faces()) == 1
        )
        assert sum(source.wrapped.IsSame(face.wrapped) for face in caller.faces()) == 1
        assert working.wrapped.IsPartner(source.wrapped)


def test_typed_framed_evidence_refusal_preserves_one_result_run_and_fails_report_closed(
    monkeypatch,
) -> None:
    calls: list[str] = []
    original_result = PreparedFramedPart.recognise

    def refuse_evidence(self, *, rotational=False):
        calls.append("evidence-refusal")
        return RefusedFramedEvidence(FramedEvidenceRefusalReason.CALLER_FACE_MAPPING_UNAVAILABLE)

    def recognise_once(self, *, rotational=False):
        calls.append("result")
        return original_result(self, rotational=rotational)

    monkeypatch.setattr(PreparedFramedPart, "recognise_evidence", refuse_evidence)
    monkeypatch.setattr(PreparedFramedPart, "recognise", recognise_once)

    drawing = build_drawing(_part(), framed_recognition=True)

    assert calls == ["evidence-refusal", "result"]
    assert drawing.recognition() is not None
    assert drawing.recognition_evidence() is None
    assert drawing.recognition_ownership() is None
    assert drawing.recognition_frame_decision == {
        "status": "framed",
        "gauge": drawing.recognition_frame.gauge.value,
        "refusal_reason": "caller-face-mapping-unavailable",
    }
    with pytest.raises(ReportUnavailableError, match="exact framed"):
        drawing.report()
