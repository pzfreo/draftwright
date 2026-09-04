"""Read-only STEP inspection evidence (#1460).

`inspect_step` is the first public `inspect` surface: a versioned, strict JSON-compatible
evidence document obtained without building, laying out, rendering, or exporting a drawing.
These tests hold the contract the issue specifies — one byte snapshot, one aggregate run, an
honest occurrence denominator, bounded face evidence, an explicit PMI census, and failure
before any misleading partial document.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Align, Box, Line, Pos, Rot, export_step
from conftest import recognition_consumer_calls
from jsonschema.validators import validator_for

from draftwright import InspectionUnavailableError, inspect_step
from draftwright import builder as builder_module
from draftwright import inspection as inspection_module
from draftwright.reporting import ReportUnavailableError

_FIXTURES = Path(__file__).parent / "fixtures"
_PMI_FIXTURE = _FIXTURES / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
_PLAIN_FIXTURE = _FIXTURES / "evaluation" / "plain-block.step"
_PLATE_FIXTURE = _FIXTURES / "grm04_drive_plate.step"
_SCHEMA_PATH = (
    Path(__file__).parents[1] / "docs/reference/draftwright-step-inspection-v1.schema.json"
)

# The stages an inspection must never reach. `compose` is deliberately absent: the shared
# one-run detect seam picks a page and scale while sizing, and none of that reaches the
# document. Everything below would mean a real drawing was built, placed, drawn, or scored.
_FORBIDDEN_STAGES = (
    "draftwright.projection",
    "draftwright.export",
    "draftwright.repair",
    "draftwright.annotations",
    "draftwright.drawing",
    "draftwright.linting",
)


def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate(document: dict) -> None:
    schema = _schema()
    validator_for(schema)(schema).validate(document)


def _oriented_slot_part():
    """Three free-axis slots — the settled `oriented_slots` deferred consumer boundary."""

    centre = (Align.CENTER, Align.CENTER, Align.CENTER)
    part = Box(120, 90, 10)
    for x in (-30, 0, 30):
        part -= Pos(x, 0, 0) * Rot(0, 0, 30) * Box(24, 6, 20, align=centre)
    return part


def _faces(document: dict) -> list[dict]:
    faces = list(document["recognition"]["association"]["unassociated"]["faces"])
    for occurrence in document["recognition"]["occurrences"]:
        faces += occurrence["faces"]["defining"] + occurrence["faces"]["constituent"]
    return faces


def test_a_real_step_fixture_returns_the_documented_strict_v1_document() -> None:
    document = inspect_step(_PMI_FIXTURE)

    _validate(document)
    assert document["schema"] == "draftwright-step-inspection"
    assert document["schema_version"] == 1

    expected_sha256 = hashlib.sha256(_PMI_FIXTURE.read_bytes()).hexdigest()
    assert document["source"] == {
        "kind": "step",
        "name": "grm03_thumbwheel_drive_screw_ap242_pmi.step",
        "sha256": expected_sha256,
        "byte_count": len(_PMI_FIXTURE.read_bytes()),
        "artifact_id": f"step-sha256:{expected_sha256}",
    }
    assert document["units"] == {
        "length": "mm",
        "area": "mm²",
        "volume": "mm³",
        "angle": "degree",
    }
    assert set(document["producer"]) == {"draftwright", "b123d-recognisers"}
    assert all(version for version in document["producer"].values())

    geometry = document["geometry"]
    assert geometry["coordinates"] == "caller"
    assert geometry["bbox"]["min"] == pytest.approx([-3.2, -5.0, -5.0])
    assert geometry["bbox"]["max"] == pytest.approx([25.5, 5.0, 5.0])
    assert geometry["bbox"]["size"] == pytest.approx([28.7, 10.0, 10.0])
    assert geometry["volume"] == pytest.approx(391.4805, rel=1e-4)
    assert geometry["topology"] == {
        "solids": 1,
        "shells": 1,
        "faces": 16,
        "wires": 21,
        "edges": 26,
        "vertices": 17,
    }

    recognition = document["recognition"]
    assert recognition["frame"] == {"status": "raw"}
    assert recognition["identity_scope"] == "document-local"
    summary = recognition["summary"]
    assert summary["total"] == len(recognition["occurrences"]) == 16
    assert summary["represented"] == 9
    assert summary["absorbed"] == 5
    assert summary["evidence_only"] == 2
    assert {occurrence["disposition"] for occurrence in recognition["occurrences"]} == {
        "represented",
        "absorbed",
        "evidence_only",
    }
    assert all(
        occurrence["owners"]
        for occurrence in recognition["occurrences"]
        if occurrence["disposition"] in {"represented", "absorbed"}
    )

    association = recognition["association"]
    assert association["face_count"]["total"] == geometry["topology"]["faces"]
    assert (
        association["face_count"]["associated"] + association["face_count"]["unassociated"]
        == association["face_count"]["total"]
    )
    assert association["face_count"]["ratio"] == pytest.approx(
        association["face_count"]["associated"] / association["face_count"]["total"]
    )
    assert [family["family"] for family in association["families"]] == sorted(
        family["family"] for family in association["families"]
    )

    pmi = document["pmi"]
    assert pmi["status"] == "present"
    assert pmi["error"] is None
    assert pmi["summary"]["sources"] == len(pmi["sources"]) == 26
    assert pmi["summary"]["records"] == len(pmi["records"]) == 18
    assert pmi["summary"]["extracted"] == 18
    assert pmi["summary"]["presentation_only"] == 8
    assert {record["kind"] for record in pmi["records"]} >= {"diameter"}


def test_the_document_is_deterministic_across_runs_of_the_same_bytes() -> None:
    """Face evidence arrives as an unordered `frozenset` of address-hashed references.

    Two runs in one process allocate different reference objects, so an unsorted projection
    changes order between them. The document must not.
    """

    first = inspect_step(_PLATE_FIXTURE)
    second = inspect_step(_PLATE_FIXTURE)

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_face_evidence_is_ordered_only_by_its_own_serialized_values() -> None:
    document = inspect_step(_PLATE_FIXTURE)

    groups = [document["recognition"]["association"]["unassociated"]["faces"]]
    for occurrence in document["recognition"]["occurrences"]:
        groups += [occurrence["faces"]["defining"], occurrence["faces"]["constituent"]]

    assert any(len(group) > 1 for group in groups), (
        "fixture precondition: at least one face group must have two faces to order"
    )
    for group in groups:
        keys = [json.dumps(face, sort_keys=True, allow_nan=False) for face in group]
        assert keys == sorted(keys)


def test_replacing_the_source_after_capture_cannot_split_the_sections(tmp_path, monkeypatch):
    """Geometry, recognition, and PMI all read the one captured snapshot, never the live path."""

    mutable = tmp_path / "part.step"
    mutable.write_bytes(_PMI_FIXTURE.read_bytes())
    expected = inspect_step(_PMI_FIXTURE)
    replacement = _PLATE_FIXTURE.read_bytes()
    assert inspect_step(_PLATE_FIXTURE)["geometry"] != expected["geometry"], (
        "fixture precondition: the two sources must produce different documents"
    )

    from draftwright.builder import _detect_part_model_analysis as real

    def replace_then_detect(part, **kwargs):
        # The bytes are already captured and hashed; swap the original out underneath the run.
        mutable.write_bytes(replacement)
        return real(part, **kwargs)

    monkeypatch.setattr(builder_module, "_detect_part_model_analysis", replace_then_detect)
    document = inspect_step(mutable)

    assert mutable.read_bytes() == replacement
    assert document["source"]["sha256"] == expected["source"]["sha256"]
    assert document["geometry"] == expected["geometry"]
    assert document["recognition"] == expected["recognition"]
    assert document["pmi"] == expected["pmi"]


def test_exactly_one_aggregate_run_and_no_recogniser_called_outside_it() -> None:
    with recognition_consumer_calls() as counts:
        inspect_step(_PLATE_FIXTURE)

    assert counts == {"build_recognition_evidence": 1}


def test_no_drawing_projection_placement_render_export_or_lint_path_runs() -> None:
    executed: set[str] = set()

    def hook(frame, event, arg):
        # Module bodies execute on import; only real function calls prove a stage ran.
        if event == "call" and frame.f_code.co_name != "<module>":
            executed.add(frame.f_globals.get("__name__", ""))

    previous = sys.getprofile()
    sys.setprofile(hook)
    try:
        inspect_step(_PLATE_FIXTURE)
    finally:
        sys.setprofile(previous)

    assert executed, "the profiler recorded nothing — the guard would pass vacuously"
    offenders = sorted(
        name
        for name in executed
        if any(name == stage or name.startswith(f"{stage}.") for stage in _FORBIDDEN_STAGES)
    )
    assert not offenders, f"inspection reached drawing-only stages: {offenders}"


def test_equal_valued_occurrences_stay_separate_in_provider_order(tmp_path) -> None:
    """Three congruent slots are three occurrences, not one deduplicated by value."""

    step = tmp_path / "oriented.step"
    export_step(_oriented_slot_part(), str(step))
    document = inspect_step(step)

    deferred = [
        occurrence
        for occurrence in document["recognition"]["occurrences"]
        if occurrence["family"] == "oriented_slots"
    ]
    assert [occurrence["id"] for occurrence in deferred] == [
        "oriented_slots:1",
        "oriented_slots:2",
        "oriented_slots:3",
    ]
    assert all(occurrence["disposition"] == "deferred" for occurrence in deferred)


def test_serialized_evidence_carries_no_provider_or_topology_identity(tmp_path) -> None:
    step = tmp_path / "oriented.step"
    export_step(_oriented_slot_part(), str(step))
    payload = json.dumps(inspect_step(step))

    for forbidden in ("FeatureRef", "FaceRef", "TopoDS", "0x", "object at "):
        assert forbidden not in payload


def test_unassociated_faces_are_bounded_and_explicitly_not_a_missed_feature() -> None:
    document = inspect_step(_PLATE_FIXTURE)
    association = document["recognition"]["association"]
    unassociated = association["unassociated"]

    assert unassociated["qualifier"] == "not_evidence_of_missed_feature"
    assert association["face_count"]["unassociated"] > 0, (
        "fixture precondition: the part must have at least one unassociated face"
    )
    assert len(unassociated["faces"]) == association["face_count"]["unassociated"]
    for face in unassociated["faces"]:
        assert set(face) == {"surface", "area", "centroid", "bbox"}
        assert face["area"] > 0
        assert len(face["centroid"]) == 3


def test_every_section_states_its_provenance_and_coverage() -> None:
    document = inspect_step(_PLATE_FIXTURE)

    assert document["geometry"]["provenance"] == "step-source"
    assert document["geometry"]["coverage"] == "solid-body"
    assert document["recognition"]["provenance"] == "recogniser-inference"
    assert document["recognition"]["coverage"] == "accepted-occurrences"
    assert document["recognition"]["association"]["provenance"] == "recogniser-evidence"
    assert document["recognition"]["association"]["coverage"] == "accepted-constituent-evidence"
    assert document["pmi"]["provenance"] == "step-ap242-source"
    assert document["pmi"]["coverage"] == "source-census-and-extracted-records"
    for occurrence in document["recognition"]["occurrences"]:
        assert occurrence["faces"]["coverage"] == "defining-and-constituent"


def test_a_clear_document_is_named_bounded_recognition_evidence_not_readiness() -> None:
    document = inspect_step(_PLAIN_FIXTURE)

    assert document["recognition"]["summary"]["total"] == 0, (
        "fixture precondition: a clear status needs a part with no attention dispositions"
    )
    assert document["status"] == "bounded-recognition-evidence"
    assert document["qualifiers"] == [
        "bounded_recognition_evidence_only",
        "not_physical_completeness",
        "not_manufacturing_readiness",
        "no_inferred_material_process_finish_thread_fit_or_tolerance_intent",
    ]


def test_a_deferred_consumer_outcome_makes_the_inspection_require_attention(tmp_path) -> None:
    step = tmp_path / "oriented.step"
    export_step(_oriented_slot_part(), str(step))
    document = inspect_step(step)

    assert document["recognition"]["summary"]["deferred"] > 0
    assert document["status"] == "needs-attention"


def test_an_evidence_only_outcome_alone_makes_the_inspection_require_attention() -> None:
    document = inspect_step(_PLATE_FIXTURE)
    summary = document["recognition"]["summary"]

    assert summary["evidence_only"] > 0
    assert summary["unsupported"] == summary["deferred"] == summary["unexpectedly_missing"] == 0
    assert document["pmi"]["status"] == "absent", (
        "fixture precondition: PMI must not be what raises the status here"
    )
    assert document["status"] == "needs-attention"


def test_a_step_source_without_pmi_reports_an_explicit_absent_census() -> None:
    document = inspect_step(_PLATE_FIXTURE)

    assert document["pmi"]["status"] == "absent"
    assert document["pmi"]["error"] is None
    assert document["pmi"]["sources"] == []
    assert document["pmi"]["records"] == []
    assert document["pmi"]["summary"]["sources"] == 0


def test_a_pmi_extraction_failure_stays_explicit_and_requires_attention(monkeypatch) -> None:
    from draftwright import analysis as analysis_module
    from draftwright import pmi as pmi_module

    def failing(*args, **kwargs):
        raise RuntimeError("simulated XCAF failure")

    monkeypatch.setattr(pmi_module, "extract_pmi_report", failing)
    monkeypatch.setattr(analysis_module, "_import_step", analysis_module._import_step)
    document = inspect_step(_PMI_FIXTURE)

    assert document["pmi"]["status"] == "extraction_error"
    assert "simulated XCAF failure" in document["pmi"]["error"]
    assert document["pmi"]["sources"] == []
    assert document["status"] == "needs-attention"


def test_a_source_entity_draftwright_cannot_lower_never_disappears() -> None:
    document = inspect_step(_PMI_FIXTURE)
    sources = document["pmi"]["sources"]

    unlowered = [source for source in sources if source["outcome"] != "extracted"]
    assert unlowered, "fixture precondition: the census must contain a non-extracted entity"
    assert len(sources) > len(document["pmi"]["records"])
    assert all(source["source_id"] for source in unlowered)


def test_a_missing_path_fails_before_any_document(tmp_path) -> None:
    with pytest.raises(OSError):
        inspect_step(tmp_path / "absent.step")


def test_unreadable_step_bytes_fail_before_any_document(tmp_path) -> None:
    broken = tmp_path / "broken.step"
    broken.write_text("this is not a STEP file\n", encoding="utf-8")

    with pytest.raises(InspectionUnavailableError, match="solid STEP geometry"):
        inspect_step(broken)


def test_a_step_source_without_a_solid_body_fails_before_any_document(tmp_path) -> None:
    curve = tmp_path / "curve.step"
    export_step(Line((0, 0, 0), (10, 0, 0)), str(curve))

    with pytest.raises(InspectionUnavailableError, match="no solid body"):
        inspect_step(curve)


def test_an_unclassified_ownership_ledger_is_refused_rather_than_shrunk(monkeypatch) -> None:
    def refuse(*args, **kwargs):
        raise ReportUnavailableError(
            "accepted occurrence family 'x' has no reportable disposition"
        )

    monkeypatch.setattr(inspection_module, "_occurrences", refuse)

    with pytest.raises(InspectionUnavailableError, match="no reportable disposition") as caught:
        inspect_step(_PLATE_FIXTURE)
    assert isinstance(caught.value.__cause__, ReportUnavailableError)


def test_a_non_raw_recognition_frame_is_refused_until_the_provider_contract_lands(monkeypatch):
    from draftwright.builder import _detect_part_model_analysis as real

    def framed(part, **kwargs):
        model, analysis = real(part, **kwargs)
        return model, replace(
            analysis,
            recognition_frame_decision={
                "status": "framed",
                "gauge": "principal",
                "refusal_reason": None,
            },
        )

    monkeypatch.setattr(builder_module, "_detect_part_model_analysis", framed)

    with pytest.raises(InspectionUnavailableError, match="raw caller coordinates only"):
        inspect_step(_PLATE_FIXTURE)


def test_no_absolute_source_path_reaches_the_document(tmp_path) -> None:
    copied = tmp_path / "part.step"
    copied.write_bytes(_PLATE_FIXTURE.read_bytes())

    payload = json.dumps(inspect_step(copied))

    assert str(tmp_path) not in payload
    assert json.loads(payload)["source"]["name"] == "part.step"


def test_importing_the_public_inspection_names_does_not_load_the_cad_kernel() -> None:
    probe = (
        "import sys; from draftwright import InspectionUnavailableError, inspect_step; "
        "print('build123d' in sys.modules or 'OCP' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "False"


def test_documented_schema_has_the_same_closed_top_level() -> None:
    schema = _schema()

    assert schema["$id"].endswith("draftwright-step-inspection-v1.schema.json")
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(inspect_step(_PLAIN_FIXTURE))
