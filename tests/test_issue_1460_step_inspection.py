"""Read-only recognition evidence for a STEP file (#1460).

`inspect_step` exists so a person or an agent can find and correct two kinds of failing: the
recogniser found the wrong thing or missed something, and Draftwright found it but did nothing
useful with it. These tests hold what the document must state truthfully for either to be
actionable.
"""

from __future__ import annotations

import json
import logging
import random
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
_PLATE_FIXTURE = _FIXTURES / "grm04_drive_plate.step"
_PLAIN_FIXTURE = _FIXTURES / "evaluation" / "plain-block.step"
_SCHEMA_PATH = (
    Path(__file__).parents[1] / "docs/reference/draftwright-step-inspection-v1.schema.json"
)

# The stages an inspection must never reach. `compose`, `model.planner`, `model.callout` and
# `view_plan` are absent because the shared one-run detect seam sizes a page and plans dimensions
# while detecting; the pinned set below covers those.
_FORBIDDEN_STAGES = (
    "draftwright.projection",
    "draftwright.export",
    "draftwright.repair",
    "draftwright.annotations",
    "draftwright.drawing",
    "draftwright.linting",
)

# The exact engine modules a warm inspection executes. A negative list only catches the stages
# someone thought to name; pinning the whole set means a future change cannot quietly pull
# another module onto this read-only path.
_EXPECTED_ENGINE_MODULES = frozenset(
    {
        # `draftwright` itself is absent: the package root's lazy resolver runs at most once,
        # on first attribute access, and is not part of an inspection.
        "draftwright._core",
        "draftwright._geometry",
        "draftwright._pmi_part21",
        "draftwright.analysis",
        "draftwright.blend_contract",
        "draftwright.builder",
        "draftwright.compose",
        "draftwright.inspection",
        "draftwright.model.callout",
        "draftwright.model.declare",
        "draftwright.model.detect",
        "draftwright.model.ir",
        "draftwright.model.planner",
        "draftwright.pmi",
        "draftwright.recogniser_policy",
        "draftwright.recogniser_schema",
        "draftwright.recognition_cache",
        "draftwright.recognition_ownership",
        "draftwright.reporting",
        "draftwright.view_plan",
    }
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


# --------------------------------------------------------------------------------------
# 1. What the recogniser found, as it stated it
# --------------------------------------------------------------------------------------


def test_a_real_fixture_returns_the_documented_document() -> None:
    document = inspect_step(_PMI_FIXTURE)

    _validate(document)
    assert document["schema"] == "draftwright-step-inspection"
    assert document["schema_version"] == 1
    assert document["source"] == {
        "name": "grm03_thumbwheel_drive_screw_ap242_pmi.step",
        "sha256": __import__("hashlib").sha256(_PMI_FIXTURE.read_bytes()).hexdigest(),
    }
    assert set(document["producer"]) == {"draftwright", "b123d-recognisers"}
    assert all(document["producer"].values())
    assert len(document["found"]) == 16


def test_each_found_feature_is_the_providers_own_record_forwarded_verbatim() -> None:
    """Draftwright wraps the recogniser's finding; it never edits it. A caller correcting a
    recognition failing must be looking at what the recogniser actually said."""

    from draftwright.builder import _detect_part_model_analysis

    document = inspect_step(_PLATE_FIXTURE)
    _model, analysis = _detect_part_model_analysis(_PLATE_FIXTURE, pmi="off")
    evidence = analysis.recognition_evidence

    assert len(document["found"]) == len(evidence.features)
    for entry, reference in zip(document["found"], evidence.features, strict=True):
        record = evidence.record(reference)
        assert entry["family"] == evidence.family(reference)
        assert entry["feature_type"] == type(record).__name__
        assert entry["feature"] == json.loads(json.dumps(record.to_dict()))


def test_every_accepted_feature_becomes_exactly_one_row_in_provider_order(tmp_path) -> None:
    """No repo fixture produces two byte-identical records — a record carries its position — so
    value-equality cannot be staged. The provable invariant is one row per accepted feature."""

    step = tmp_path / "oriented.step"
    export_step(_oriented_slot_part(), str(step))
    found = inspect_step(step)["found"]

    deferred = [entry for entry in found if entry["family"] == "oriented_slots"]
    assert [entry["id"] for entry in deferred] == [
        "oriented_slots:1",
        "oriented_slots:2",
        "oriented_slots:3",
    ]
    assert len({entry["id"] for entry in found}) == len(found)
    assert len({json.dumps(entry["feature"], sort_keys=True) for entry in deferred}) == 3, (
        "precondition: these differ by position, so the guard is cardinality and order"
    )


def test_serialized_evidence_carries_no_provider_or_topology_identity(tmp_path) -> None:
    step = tmp_path / "oriented.step"
    export_step(_oriented_slot_part(), str(step))
    payload = json.dumps(inspect_step(step))

    for forbidden in ("FeatureRef", "FaceRef", "TopoDS", "0x", "object at "):
        assert forbidden not in payload


# --------------------------------------------------------------------------------------
# 2. What went unclaimed
# --------------------------------------------------------------------------------------


def test_unclaimed_geometry_is_reported_with_the_providers_own_accounting() -> None:
    document = inspect_step(_PLATE_FIXTURE)
    missed = document["missed"]

    assert missed["face_count"]["unclaimed"] > 0, (
        "fixture precondition: the part must have at least one unclaimed face"
    )
    assert (
        missed["face_count"]["claimed"] + missed["face_count"]["unclaimed"]
        == (missed["face_count"]["total"])
    )
    assert len(missed["unclaimed_faces"]) == missed["face_count"]["unclaimed"]
    for face in missed["unclaimed_faces"]:
        assert set(face) == {"surface", "area", "centroid", "bbox"}
        assert face["area"] > 0
        assert face["surface"]


def test_the_missing_half_of_missed_says_so_rather_than_reading_as_none() -> None:
    """The recogniser can explain what it proposed and rejected, but only from a second run.
    Absence must be stated, not left to look like 'nothing was rejected'."""

    rejected = inspect_step(_PLAIN_FIXTURE)["missed"]["rejected_candidates"]

    assert rejected["available"] is False
    assert "second recognition run" in rejected["reason"]


def test_unclaimed_face_evidence_states_the_real_surface_and_centroid() -> None:
    faces = inspect_step(_PLATE_FIXTURE)["missed"]["unclaimed_faces"]

    assert {face["surface"] for face in faces} == {"plane"}
    for face in faces:
        assert face["centroid"] != face["bbox"]["min"], (
            "the centroid must be the face's own centre, not a corner of its box"
        )
        assert all(
            low <= value <= high
            for value, low, high in zip(
                face["centroid"], face["bbox"]["min"], face["bbox"]["max"], strict=True
            )
        )


def test_a_face_whose_surface_cannot_be_named_is_refused() -> None:
    """`geom_type` raises on a degenerate face rather than returning None, so a default cannot
    stand in for it."""

    class _Degenerate:
        @property
        def geom_type(self):
            raise ValueError("Cannot determine geometry type of an empty shape")

    with pytest.raises(InspectionUnavailableError, match="cannot be described"):
        inspection_module._face(_Degenerate())


def test_face_descriptions_sort_any_input_order_into_the_same_sequence() -> None:
    """Deterministic by construction: the document-level ordering guard depends on where
    `frozenset` happens to place address-hashed references and can pass on unsorted code by
    luck. This one shuffles the input itself."""

    from draftwright.builder import _detect_part_model_analysis

    _model, analysis = _detect_part_model_analysis(_PLATE_FIXTURE, pmi="off")
    evidence = analysis.recognition_evidence
    references = list(evidence.association.unassociated_faces)
    for reference in evidence.features:
        references += list(evidence.constituent_faces(reference))
    assert len(references) > 1, "precondition: ordering needs at least two faces"

    expected = inspection_module._faces(evidence, references)
    for seed in range(5):
        shuffled = list(references)
        random.Random(seed).shuffle(shuffled)
        assert inspection_module._faces(evidence, shuffled) == expected


# --------------------------------------------------------------------------------------
# 3. What Draftwright did with each finding
# --------------------------------------------------------------------------------------


def test_each_finding_states_plainly_whether_draftwright_acted_on_it() -> None:
    document = inspect_step(_PLATE_FIXTURE)
    outcomes = [entry["draftwright"] for entry in document["found"]]

    acted = [outcome for outcome in outcomes if outcome["acted_on"]]
    ignored = [outcome for outcome in outcomes if not outcome["acted_on"]]
    assert acted and ignored, (
        "fixture precondition: the part must have both acted-on and ignored findings, or this "
        "proves nothing about the distinction"
    )
    assert all(outcome["disposition"] in {"represented", "absorbed"} for outcome in acted)
    assert all(outcome["owners"] for outcome in acted)
    assert all(outcome["disposition"] not in {"represented", "absorbed"} for outcome in ignored)
    assert all(outcome["reason"] for outcome in outcomes)


def test_a_finding_draftwright_will_not_use_carries_its_reason(tmp_path) -> None:
    """The conversion failing this document exists to surface: recognition found it, the drawing
    does not use it, and a reader needs to know why."""

    step = tmp_path / "oriented.step"
    export_step(_oriented_slot_part(), str(step))
    deferred = [
        entry for entry in inspect_step(step)["found"] if entry["family"] == "oriented_slots"
    ]

    assert deferred
    for entry in deferred:
        assert entry["draftwright"]["acted_on"] is False
        assert entry["draftwright"]["disposition"] == "deferred"
        assert entry["draftwright"]["owners"] == []
        assert entry["draftwright"]["reason"] == "consumer_semantics_deferred"


def test_owners_name_ir_features_not_provider_or_topology_identities() -> None:
    document = inspect_step(_PLATE_FIXTURE)
    owners = [owner for entry in document["found"] for owner in entry["draftwright"]["owners"]]

    assert owners
    assert all(":" in owner for owner in owners), "owners are report-local kind:index ids"


def test_a_refused_occurrence_ledger_surfaces_as_an_inspection_failure(monkeypatch) -> None:
    """The refusal lives in the shared report projector and is tested against real unclassified
    ledgers in `test_issue_1438_report_projection`. What is specific here is that inspection
    propagates it rather than degrading to a document with a smaller denominator."""

    def refuse(*args, **kwargs):
        raise ReportUnavailableError(
            "accepted occurrence family 'x' has no reportable disposition"
        )

    monkeypatch.setattr(inspection_module, "_occurrences", refuse)

    with pytest.raises(InspectionUnavailableError, match="no reportable disposition") as caught:
        inspect_step(_PLATE_FIXTURE)
    assert isinstance(caught.value.__cause__, ReportUnavailableError)


# --------------------------------------------------------------------------------------
# Lifecycle, isolation and failure
# --------------------------------------------------------------------------------------


def test_the_document_is_deterministic_across_runs_of_the_same_bytes() -> None:
    assert inspect_step(_PLATE_FIXTURE) == inspect_step(_PLATE_FIXTURE)


def test_replacing_the_source_after_capture_cannot_change_the_document(tmp_path, monkeypatch):
    mutable = tmp_path / "part.step"
    mutable.write_bytes(_PMI_FIXTURE.read_bytes())
    expected = inspect_step(_PMI_FIXTURE)
    replacement = _PLATE_FIXTURE.read_bytes()
    assert inspect_step(_PLATE_FIXTURE)["found"] != expected["found"], (
        "fixture precondition: the two sources must produce different documents"
    )

    real = builder_module._detect_part_model_analysis

    def replace_then_detect(part, **kwargs):
        # The bytes are already captured; swap the original out underneath the run.
        mutable.write_bytes(replacement)
        return real(part, **kwargs)

    monkeypatch.setattr(builder_module, "_detect_part_model_analysis", replace_then_detect)
    document = inspect_step(mutable)

    assert mutable.read_bytes() == replacement
    assert document["source"]["sha256"] == expected["source"]["sha256"]
    assert document["found"] == expected["found"]
    assert document["missed"] == expected["missed"]


def test_exactly_one_aggregate_run_and_no_recogniser_called_outside_it() -> None:
    with recognition_consumer_calls() as counts:
        inspect_step(_PLATE_FIXTURE)

    assert counts == {"build_recognition_evidence": 1}


def _executing_modules(fixture: Path) -> dict[str, int]:
    """Return the draftwright modules whose code runs during one warm inspection.

    Warm matters: a cold call also executes class bodies and decorators of every module it
    imports, which are not stage invocations and would make this guard depend on whatever a
    previous test happened to import.
    """

    inspect_step(fixture)  # warm every import so only execution is measured
    seen: dict[str, int] = {}

    def hook(frame, event, arg):
        if event == "call":
            name = frame.f_globals.get("__name__", "")
            if name.startswith("draftwright"):
                seen[name] = seen.get(name, 0) + 1

    previous = sys.getprofile()
    sys.setprofile(hook)
    try:
        inspect_step(fixture)
    finally:
        sys.setprofile(previous)
    return seen


def test_no_drawing_projection_placement_render_export_or_lint_path_runs() -> None:
    executed = _executing_modules(_PLATE_FIXTURE)

    assert executed, "the profiler recorded nothing — the guard would pass vacuously"
    offenders = sorted(
        name
        for name in executed
        if any(name == stage or name.startswith(f"{stage}.") for stage in _FORBIDDEN_STAGES)
    )
    assert not offenders, f"inspection reached drawing-only stages: {offenders}"


def test_the_set_of_engine_modules_an_inspection_executes_is_pinned() -> None:
    executed = set(_executing_modules(_PLATE_FIXTURE))

    assert executed == _EXPECTED_ENGINE_MODULES, (
        f"unexpected: {sorted(executed - _EXPECTED_ENGINE_MODULES)}; "
        f"no longer executed: {sorted(_EXPECTED_ENGINE_MODULES - executed)}"
    )


def test_recognition_stays_geometry_only(monkeypatch) -> None:
    """PMI lowering can rewrite a grouped hole member into a singleton owner, which would make
    an authored annotation change what this document says Draftwright did."""

    real = builder_module._detect_part_model_analysis
    modes = []

    def record_mode(part, **kwargs):
        modes.append(kwargs.get("pmi"))
        return real(part, **kwargs)

    monkeypatch.setattr(builder_module, "_detect_part_model_analysis", record_mode)
    inspect_step(_PMI_FIXTURE)

    assert modes == ["off"]


def test_the_document_is_isolated_strict_json_not_live_objects() -> None:
    document = inspect_step(_PMI_FIXTURE)

    for entry in document["found"]:
        assert isinstance(entry["feature"], dict)
        for value in entry["feature"].values():
            assert not isinstance(value, tuple)
    assert json.loads(json.dumps(document, allow_nan=False)) == document


def test_a_value_that_cannot_be_stated_as_json_fails_as_an_inspection_failure(monkeypatch):
    real = inspection_module._missed

    def infinite(evidence):
        outcome = real(evidence)
        outcome["face_count"]["total"] = float("inf")
        return outcome

    monkeypatch.setattr(inspection_module, "_missed", infinite)

    with pytest.raises(InspectionUnavailableError, match="cannot be stated as JSON"):
        inspect_step(_PLATE_FIXTURE)


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


def test_a_non_raw_recognition_frame_is_refused_until_the_provider_contract_lands(monkeypatch):
    """Unreachable through the public API today — raw is the ADR 0020 default — so this is a
    forward guard, exercised by substituting the frame decision."""

    real = builder_module._detect_part_model_analysis

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

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(inspect_step(_PLAIN_FIXTURE))


# --------------------------------------------------------------------------------------
# Script generation writes the document to disk, and never into the script
# --------------------------------------------------------------------------------------


def _generate(tmp_path, monkeypatch, fixture: Path, **kwargs) -> tuple[str, Path]:
    from draftwright.sheet_emit import generate_sheet_script, inspection_sidecar_path

    source = tmp_path / "part.step"
    source.write_bytes(fixture.read_bytes())
    monkeypatch.chdir(tmp_path)
    py_path = generate_sheet_script("part.step", out="drawing", **kwargs)
    return py_path, Path(inspection_sidecar_path(py_path))


def test_generating_a_script_writes_the_document_beside_it(tmp_path, monkeypatch) -> None:
    py_path, sidecar = _generate(tmp_path, monkeypatch, _PMI_FIXTURE)

    assert Path(py_path).exists()
    assert sidecar.name == "drawing.draftwright-inspection.json"
    document = json.loads(sidecar.read_text(encoding="utf-8"))
    _validate(document)
    assert document == inspect_step(tmp_path / "part.step"), (
        "the sidecar must be what inspect_step produces for the same bytes"
    )


def test_the_generated_script_contains_none_of_the_evidence(tmp_path, monkeypatch) -> None:
    py_path, sidecar = _generate(tmp_path, monkeypatch, _PMI_FIXTURE)
    source = Path(py_path).read_text(encoding="utf-8")

    assert sidecar.exists() and json.loads(sidecar.read_text(encoding="utf-8"))["found"]
    for marker in (
        "DRAFTWRIGHT_RECOGNITION_SNAPSHOT",
        "acted_on",
        "disposition",
        "draftwright-step",
    ):
        assert marker not in source


def test_the_document_costs_no_second_aggregate_recognition_run(tmp_path, monkeypatch) -> None:
    from draftwright.sheet_emit import generate_sheet_script

    source = tmp_path / "part.step"
    source.write_bytes(_PLATE_FIXTURE.read_bytes())
    monkeypatch.chdir(tmp_path)

    with recognition_consumer_calls() as counts:
        generate_sheet_script("part.step", out="drawing")

    assert counts == {"build_recognition_evidence": 1}


def test_inspect_false_generates_the_script_without_the_document(tmp_path, monkeypatch) -> None:
    py_path, sidecar = _generate(tmp_path, monkeypatch, _PLATE_FIXTURE, inspect=False)

    assert Path(py_path).exists()
    assert not sidecar.exists()


def test_regenerating_without_a_document_removes_the_previous_one(tmp_path, monkeypatch) -> None:
    """A stale document beside a fresh script describes a different part and says nothing
    about it — exactly what this evidence exists to prevent."""

    from draftwright.sheet_emit import generate_sheet_script, inspection_sidecar_path

    (tmp_path / "a.step").write_bytes(_PLATE_FIXTURE.read_bytes())
    (tmp_path / "b.step").write_bytes(_PMI_FIXTURE.read_bytes())
    monkeypatch.chdir(tmp_path)

    py_path = generate_sheet_script("a.step", out="drawing")
    sidecar = Path(inspection_sidecar_path(py_path))
    assert json.loads(sidecar.read_text(encoding="utf-8"))["source"]["name"] == "a.step"

    generate_sheet_script("b.step", out="drawing", inspect=False)

    assert not sidecar.exists(), "the previous run's document must not survive a new script"


def test_a_source_without_a_solid_body_warns_and_still_generates(
    tmp_path, monkeypatch, caplog
) -> None:
    from draftwright.sheet_emit import generate_sheet_script, inspection_sidecar_path

    monkeypatch.chdir(tmp_path)
    export_step(Line((0, 0, 0), (10, 0, 0)), "curve.step")

    with caplog.at_level(logging.WARNING, logger="draftwright.sheet_emit"):
        py_path = generate_sheet_script("curve.step", out="curve")

    assert Path(py_path).exists(), "a missing document must never fail script generation"
    assert not Path(inspection_sidecar_path(py_path)).exists()
    assert "No inspection sidecar written" in caplog.text


def test_a_build123d_object_source_generates_a_script_but_no_document(
    tmp_path, monkeypatch
) -> None:
    from draftwright.sheet_emit import generate_sheet_script, inspection_sidecar_path

    monkeypatch.chdir(tmp_path)
    py_path = generate_sheet_script(Box(30, 20, 10), out="drawing")

    assert Path(py_path).exists()
    assert not Path(inspection_sidecar_path(py_path)).exists(), (
        "a live object has no STEP bytes, so it has no source identity to report"
    )


def test_the_cli_prints_the_document_path_and_no_report_suppresses_it(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from draftwright.cli import app

    (tmp_path / "part.step").write_bytes(_PLATE_FIXTURE.read_bytes())
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["part.step", "--script", "--out", "with"])
    assert result.exit_code == 0, result.output
    assert "with.py" in result.output.split()
    assert "with.draftwright-inspection.json" in result.output.split()

    result = runner.invoke(app, ["part.step", "--script", "--out", "without", "--no-report"])
    assert result.exit_code == 0, result.output
    assert "without.draftwright-inspection.json" not in result.output
    assert not (tmp_path / "without.draftwright-inspection.json").exists()
