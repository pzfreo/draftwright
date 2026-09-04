"""Generation-time recognition evidence (#1438, moved to a sidecar by #1460).

Script generation used to embed a `DRAFTWRIGHT_RECOGNITION_SNAPSHOT` literal in the Python it
wrote. The generated script is a drawing declaration a person edits; evidence about the run that
produced it now lives in the document beside it, where it can be diffed and re-read without
parsing Python. The byte-snapshot integrity #1438 established is unchanged and still guarded
here — the sidecar inherits it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Align, Box, Cylinder, Pos, RegularPolygon, Rot, export_step, extrude

from draftwright import sheet_emit as sheet_emit_module
from draftwright.sheet_emit import generate_sheet_script, inspection_sidecar_path

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


def _generate(part, tmp_path: Path, stem: str) -> tuple[Path, dict]:
    """Generate a script from a STEP export of *part*; return (script path, sidecar document)."""

    step = tmp_path / f"{stem}.step"
    export_step(part, str(step))
    script = Path(generate_sheet_script(str(step), out=str(tmp_path / stem)))
    return script, json.loads(Path(inspection_sidecar_path(str(script))).read_text("utf-8"))


def _unused(document: dict) -> list[dict]:
    """Findings recognition made that the drawing does not act on."""

    return [entry for entry in document["found"] if not entry["draftwright"]["acted_on"]]


def test_the_generated_script_carries_no_recognition_evidence(tmp_path: Path) -> None:
    """The evidence belongs beside the script, not inside it."""

    script, document = _generate(_oriented_slot_part(), tmp_path, "oriented")
    source = script.read_text(encoding="utf-8")

    assert "DRAFTWRIGHT_RECOGNITION_SNAPSHOT" not in source
    assert "disposition" not in source
    assert "acted_on" not in source
    assert "FeatureRef" not in source and "FaceRef" not in source
    assert document["found"], "precondition: there is evidence to have left out"


def test_the_sidecar_exposes_each_deferred_oriented_slot(tmp_path: Path) -> None:
    _script, document = _generate(_oriented_slot_part(), tmp_path, "oriented")

    slots = [entry for entry in document["found"] if entry["family"] == "oriented_slots"]
    assert [entry["id"] for entry in slots] == [
        "oriented_slots:1",
        "oriented_slots:2",
        "oriented_slots:3",
    ]
    assert {entry["feature_type"] for entry in slots} == {"OrientedSlot"}
    assert {entry["feature_schema_version"] for entry in slots} == {1}
    for entry in slots:
        outcome = entry["draftwright"]
        assert outcome["acted_on"] is False
        assert outcome["disposition"] == "deferred"
        assert outcome["reason"] == "consumer_semantics_deferred"
        assert outcome["tracking"] == "https://github.com/pzfreo/draftwright/issues/1430"
    assert [entry["feature"]["center"] for entry in slots] == [
        [-30.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [30.0, 0.0, 0.0],
    ]
    assert json.loads(json.dumps(document, allow_nan=False, sort_keys=True)) == document


def test_a_part_with_nothing_unused_claims_nothing_more(tmp_path: Path) -> None:
    _script, document = _generate(Box(40, 30, 20), tmp_path, "plain")

    assert _unused(document) == []
    assert "complete" not in json.dumps(document)
    assert document["missed"]["rejected_candidates"]["available"] is False, (
        "an empty unused list is not a completeness claim: what recognition rejected is unknown"
    )


def test_absorbed_findings_are_acted_on_while_an_unsupported_one_is_not(tmp_path: Path) -> None:
    _script, document = _generate(_mixed_absorbed_unsupported_part(), tmp_path, "mixed")

    assert [
        (entry["family"], entry["draftwright"]["disposition"]) for entry in _unused(document)
    ] == [("passages", "unsupported")]
    absorbed = [
        entry for entry in document["found"] if entry["draftwright"]["disposition"] == "absorbed"
    ]
    assert absorbed, "fixture precondition: the part must also produce an absorbed finding"
    assert all(entry["draftwright"]["acted_on"] for entry in absorbed)


def test_projection_evidence_is_reported_unused_while_its_step_is_acted_on(tmp_path: Path) -> None:
    _script, document = _generate(_step_projection_evidence_part(), tmp_path, "steps")

    assert {
        (entry["family"], entry["draftwright"]["disposition"]) for entry in _unused(document)
    } == {
        ("step_levels", "evidence_only"),
        ("risers", "evidence_only"),
    }
    through = [entry for entry in document["found"] if entry["family"] == "through_steps"]
    assert through and all(entry["draftwright"]["acted_on"] for entry in through)


def test_a_lost_owner_is_reported_rather_than_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finding whose recorded owner vanished must surface as unexpectedly missing, never be
    quietly removed from the document."""

    real = sheet_emit_module._detect_part_model_analysis

    def lose_bindings(source, *, pmi="off"):
        model, analysis = real(source, pmi=pmi)
        ownership = analysis.recognition_ownership
        return model, replace(analysis, recognition_ownership=replace(ownership, bindings=()))

    monkeypatch.setattr(sheet_emit_module, "_detect_part_model_analysis", lose_bindings)
    _script, document = _generate(_through_step_part(), tmp_path, "lost")

    assert ("through_steps", "unexpectedly_missing") in {
        (entry["family"], entry["draftwright"]["disposition"]) for entry in _unused(document)
    }


def test_the_sidecar_carries_exact_source_identity_and_hash_deterministically(
    tmp_path: Path,
) -> None:
    step = tmp_path / "source.step"
    export_step(Box(40, 30, 20), str(step))
    expected_hash = hashlib.sha256(step.read_bytes()).hexdigest()

    first = generate_sheet_script(str(step), out=str(tmp_path / "first"))
    second = generate_sheet_script(str(step), out=str(tmp_path / "second"))
    first_document = json.loads(Path(inspection_sidecar_path(first)).read_text("utf-8"))
    second_document = json.loads(Path(inspection_sidecar_path(second)).read_text("utf-8"))

    assert first_document == second_document
    assert first_document["source"] == {"name": "source.step", "sha256": expected_hash}


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
    document = json.loads(Path(inspection_sidecar_path(script)).read_text("utf-8"))

    assert document["source"]["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert _unused(document) == []
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
    assert not Path(inspection_sidecar_path(str(tmp_path / "replaced.py"))).exists()


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
    assert not Path(inspection_sidecar_path(str(tmp_path / "deleted.py"))).exists()


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
    document = json.loads(Path(inspection_sidecar_path(script)).read_text("utf-8"))

    assert repr(str(first.resolve())) in script_source
    assert repr(str(second.resolve())) not in script_source
    assert document["source"] == {
        "name": "source.step",
        "sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
    }
    assert _unused(document) == []
