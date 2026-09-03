"""#1438 — generated Python carries a bounded generation-time recognition-gap snapshot."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Align, Box, Cylinder, Pos, RegularPolygon, Rot, export_step, extrude

from draftwright import build_drawing
from draftwright import reporting as reporting_module
from draftwright import sheet_emit as sheet_emit_module
from draftwright.sheet_emit import emit_sheet_script, generate_sheet_script

_CENTER = (Align.CENTER, Align.CENTER, Align.CENTER)


def _oriented_slot_part():
    part = Box(120, 90, 10)
    for x in (-30, 0, 30):
        part -= Pos(x, 0, 0) * Rot(0, 0, 30) * Box(24, 6, 20, align=_CENTER)
    return part


def _mixed_absorbed_unsupported_part():
    part = Box(80, 60, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    for x, y in ((-20, -10), (15, 12)):
        part -= Pos(x, y, 0) * Cylinder(2, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part - extrude(RegularPolygon(6, 6), amount=12, both=True)


def _step_projection_evidence_part():
    aligned = (Align.MIN, Align.MIN, Align.MIN)
    return Box(60, 40, 20, align=aligned) - Pos(30, 0, 10) * Box(30, 40, 10, align=aligned)


def _through_step_part():
    return Box(40, 30, 20) - Pos(15, 10, 0) * Box(20, 20, 30)


def _generation_snapshot_for(part, *, lose_bindings: bool = False) -> dict[str, object]:
    drawing = build_drawing(part)
    ownership = drawing.recognition_ownership()
    if lose_bindings:
        assert ownership is not None
        ownership = replace(ownership, bindings=())
    return reporting_module._generation_snapshot(
        evidence=drawing.recognition_evidence(),
        ownership=ownership,
        model=drawing.model(),
        source=None,
        source_sha256=None,
    )


def _snapshot(source: str) -> dict[str, object]:
    module = ast.parse(source)
    assignment = next(
        statement
        for statement in module.body
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "DRAFTWRIGHT_RECOGNITION_SNAPSHOT"
            for target in statement.targets
        )
    )
    value = ast.literal_eval(assignment.value)
    assert isinstance(value, dict)
    return value


def test_generated_python_exposes_each_deferred_oriented_slot_without_execution(
    tmp_path: Path,
) -> None:
    script = generate_sheet_script(_oriented_slot_part(), out=str(tmp_path / "oriented"))
    source = Path(script).read_text(encoding="utf-8")

    snapshot = _snapshot(source)

    assert snapshot["schema"] == "draftwright-recognition-snapshot"
    assert snapshot["schema_version"] == 1
    assert snapshot["status"] == "accepted_occurrences_unrepresented"
    assert snapshot["coverage"] == "accepted-occurrence-gaps"
    assert snapshot["source"] == {"kind": "build123d", "name": None, "sha256": None}
    assert snapshot["summary"] == {
        "total": 3,
        "unsupported": 0,
        "deferred": 3,
        "evidence_only": 0,
        "unexpectedly_missing": 0,
    }
    gaps = snapshot["gaps"]
    assert isinstance(gaps, list)
    assert [gap["id"] for gap in gaps] == [
        "oriented_slots:1",
        "oriented_slots:2",
        "oriented_slots:3",
    ]
    assert {gap["family"] for gap in gaps} == {"oriented_slots"}
    assert {gap["record_type"] for gap in gaps} == {"OrientedSlot"}
    assert {gap["record_schema_version"] for gap in gaps} == {1}
    assert {gap["disposition"] for gap in gaps} == {"deferred"}
    assert {gap["reason_code"] for gap in gaps} == {"consumer_semantics_deferred"}
    assert {gap["tracking"] for gap in gaps} == {
        "https://github.com/pzfreo/draftwright/issues/1430"
    }
    assert [gap["record"]["center"] for gap in gaps] == [
        [-30.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [30.0, 0.0, 0.0],
    ]
    assert json.loads(json.dumps(snapshot, allow_nan=False, sort_keys=True)) == snapshot
    assert "FeatureRef" not in source
    assert "FaceRef" not in source


def test_empty_snapshot_names_its_bounded_claim_truthfully(tmp_path: Path) -> None:
    script = generate_sheet_script(Box(40, 30, 20), out=str(tmp_path / "plain"))

    snapshot = _snapshot(Path(script).read_text(encoding="utf-8"))

    assert snapshot["status"] == "no_unrepresented_accepted_occurrences"
    assert snapshot["summary"]["total"] == 0
    assert snapshot["gaps"] == []
    assert "complete" not in snapshot["status"]


def test_snapshot_filters_absorbed_occurrences_while_retaining_unsupported_gap() -> None:
    snapshot = _generation_snapshot_for(_mixed_absorbed_unsupported_part())

    assert [(gap["family"], gap["disposition"]) for gap in snapshot["gaps"]] == [
        ("passages", "unsupported")
    ]
    assert snapshot["summary"] == {
        "total": 1,
        "unsupported": 1,
        "deferred": 0,
        "evidence_only": 0,
        "unexpectedly_missing": 0,
    }


def test_snapshot_filters_represented_occurrence_while_retaining_evidence_gaps() -> None:
    snapshot = _generation_snapshot_for(_step_projection_evidence_part())

    assert {(gap["family"], gap["disposition"]) for gap in snapshot["gaps"]} == {
        ("step_levels", "evidence_only"),
        ("risers", "evidence_only"),
    }
    assert all(gap["family"] != "through_steps" for gap in snapshot["gaps"])


def test_snapshot_projects_supported_owner_loss_as_an_unexpected_gap() -> None:
    snapshot = _generation_snapshot_for(_through_step_part(), lose_bindings=True)

    assert [(gap["family"], gap["disposition"]) for gap in snapshot["gaps"]] == [
        ("through_steps", "unexpectedly_missing")
    ]


def test_step_snapshot_carries_exact_source_identity_and_hash_deterministically(
    tmp_path: Path,
) -> None:
    step = tmp_path / "source.step"
    export_step(Box(40, 30, 20), str(step))
    expected_hash = hashlib.sha256(step.read_bytes()).hexdigest()

    first = generate_sheet_script(str(step), out=str(tmp_path / "first"))
    second = generate_sheet_script(str(step), out=str(tmp_path / "second"))
    first_snapshot = _snapshot(Path(first).read_text(encoding="utf-8"))
    second_snapshot = _snapshot(Path(second).read_text(encoding="utf-8"))

    assert first_snapshot == second_snapshot
    assert first_snapshot["source"] == {
        "kind": "step",
        "name": "source.step",
        "sha256": expected_hash,
    }


def test_recognition_uses_immutable_bytes_during_an_aba_source_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    step = tmp_path / "source.step"
    export_step(Box(40, 30, 20), str(step))
    original_bytes = step.read_bytes()
    alternate = tmp_path / "alternate.step"
    export_step(_oriented_slot_part(), str(alternate))
    original = sheet_emit_module._detect_part_model_analysis

    def detect_during_aba(source, *, pmi="off"):
        step.write_bytes(alternate.read_bytes())
        try:
            return original(source, pmi=pmi)
        finally:
            step.write_bytes(original_bytes)

    monkeypatch.setattr(sheet_emit_module, "_detect_part_model_analysis", detect_during_aba)

    script = generate_sheet_script(str(step), out=str(tmp_path / "stable"))
    snapshot = _snapshot(Path(script).read_text(encoding="utf-8"))

    assert snapshot["source"]["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert snapshot["gaps"] == []
    assert step.read_bytes() == original_bytes


def test_persistent_source_replacement_fails_before_writing_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    step = tmp_path / "source.step"
    alternate = tmp_path / "alternate.step"
    export_step(Box(40, 30, 20), str(step))
    export_step(_oriented_slot_part(), str(alternate))
    original = sheet_emit_module._detect_part_model_analysis

    def detect_then_replace(source, *, pmi="off"):
        result = original(source, pmi=pmi)
        step.write_bytes(alternate.read_bytes())
        return result

    monkeypatch.setattr(sheet_emit_module, "_detect_part_model_analysis", detect_then_replace)

    with pytest.raises(RuntimeError, match="STEP replay source changed"):
        generate_sheet_script(str(step), out=str(tmp_path / "replaced"))

    assert not (tmp_path / "replaced.py").exists()


def test_source_deletion_fails_before_writing_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    step = tmp_path / "source.step"
    export_step(Box(40, 30, 20), str(step))
    original = sheet_emit_module._detect_part_model_analysis

    def detect_then_delete(source, *, pmi="off"):
        result = original(source, pmi=pmi)
        step.unlink()
        return result

    monkeypatch.setattr(sheet_emit_module, "_detect_part_model_analysis", detect_then_delete)

    with pytest.raises(RuntimeError, match="STEP replay source became unavailable"):
        generate_sheet_script(str(step), out=str(tmp_path / "deleted"))

    assert not (tmp_path / "deleted.py").exists()


def test_symlink_retarget_cannot_split_replay_source_from_recognition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.step"
    second = tmp_path / "second.step"
    export_step(Box(40, 30, 20), str(first))
    export_step(_oriented_slot_part(), str(second))
    source = tmp_path / "source.step"
    try:
        source.symlink_to(first)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    original = sheet_emit_module._detect_part_model_analysis

    def retarget_then_detect(snapshot_source, *, pmi="off"):
        source.unlink()
        source.symlink_to(second)
        return original(snapshot_source, pmi=pmi)

    monkeypatch.setattr(sheet_emit_module, "_detect_part_model_analysis", retarget_then_detect)

    script = generate_sheet_script(str(source), out=str(tmp_path / "linked"))
    script_source = Path(script).read_text(encoding="utf-8")
    snapshot = _snapshot(script_source)

    assert repr(str(first.resolve())) in script_source
    assert repr(str(second.resolve())) not in script_source
    assert snapshot["source"] == {
        "kind": "step",
        "name": "source.step",
        "sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
    }
    assert snapshot["gaps"] == []


@pytest.mark.parametrize("bad_snapshot", ({"value": object()},))
def test_emitter_rejects_non_json_snapshot_values_before_writing_python(bad_snapshot) -> None:
    model, _analysis = sheet_emit_module._detect_part_model_analysis(Box(10, 10, 10))

    with pytest.raises(TypeError, match="not JSON serializable"):
        emit_sheet_script(
            model,
            "part = None",
            "bad",
            title="BAD",
            number="BAD",
            recognition_snapshot=bad_snapshot,
        )

    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValueError, match="Circular reference"):
        emit_sheet_script(
            model,
            "part = None",
            "bad",
            title="BAD",
            number="BAD",
            recognition_snapshot=cyclic,  # type: ignore[arg-type]
        )
