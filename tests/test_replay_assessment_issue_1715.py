"""A generated replay persists exact current-build evidence without stale success (#1715)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, export_step
from jsonschema.validators import validator_for
from referencing import Registry, Resource

from draftwright.replay_assessment import (
    ASSESSMENT_SCHEMA,
    assessment_sidecar_path,
    invalidate_replay_assessment,
    prepare_replay_assessment,
)
from draftwright.sheet_emit import generate_sheet_script, inspection_sidecar_path

_ROOT = Path(__file__).parents[1]
_SCHEMA_PATH = _ROOT / "docs/reference/draftwright-replay-assessment-v1.schema.json"
_REPORT_SCHEMA_PATH = _ROOT / "docs/reference/draftwright-report-v8.schema.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate(document: dict) -> None:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    report_schema = json.loads(_REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = validator_for(schema)
    validator.check_schema(schema)
    registry = Registry().with_resource(
        report_schema["$id"], Resource.from_contents(report_schema)
    )
    validator(schema, registry=registry).validate(document)


def _run(script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=script.parent,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def _declared_report() -> dict:
    return {
        "schema": "draftwright-report",
        "schema_version": 8,
        "scope": "declared-sheet",
        "status": "bounded-clear",
        "producer": {"draftwright": "test", "quiddity": "test"},
        "declarations": {"entries": []},
    }


class _ReportDrawing:
    def __init__(self, report: dict) -> None:
        self._report = report

    def report(self) -> dict:
        return self._report

    def model(self) -> SimpleNamespace:
        return SimpleNamespace(features=(), declaration_identities=())

    def measurement_snapshot(self) -> SimpleNamespace:
        return SimpleNamespace(owners=(), claims=(), unknown=(), cell_unknown=())


def test_real_replay_is_deterministic_and_tracks_an_edited_declaration(tmp_path) -> None:
    step = tmp_path / "source.step"
    export_step(Box(40, 30, 6) - Cylinder(2, 10), step)
    script = Path(
        generate_sheet_script(
            step,
            out=str(tmp_path / "drawing"),
            formats=("svg",),
        )
    )
    assessment = Path(assessment_sidecar_path(script))
    inspection = Path(inspection_sidecar_path(str(script)))
    inspection_bytes = inspection.read_bytes()

    first_run = _run(script)
    assert first_run.returncode == 0, first_run.stderr
    assert first_run.stdout.strip() == str(assessment)
    first_bytes = assessment.read_bytes()
    first = json.loads(first_bytes)
    _validate(first)
    assert first["source"] == {
        "kind": "step",
        "name": "source.step",
        "sha256": _sha(step),
        "reason": None,
    }
    assert first["script"] == {"name": "drawing.py", "sha256": _sha(script)}
    assert first["run"] == {
        "pmi_mode": "off",
        "formats": ["svg"],
        "reproducible": True,
    }
    assert first["outputs"] == [
        {
            "format": "svg",
            "path": str(tmp_path / "drawing.svg"),
            "sha256": _sha(tmp_path / "drawing.svg"),
            "bytes": (tmp_path / "drawing.svg").stat().st_size,
        }
    ]
    assert first["semantic_links"]["authority"] == "drawing.declarations"
    assert first["drawing"]["schema_version"] == 8

    unchanged = _run(script)
    assert unchanged.returncode == 0, unchanged.stderr
    assert assessment.read_bytes() == first_bytes

    source = script.read_text(encoding="utf-8")
    dimension = next(line for line in source.splitlines() if line.startswith("sheet.dimension("))
    script.write_text(source.replace(dimension, f"# {dimension}", 1), encoding="utf-8")
    edited_run = _run(script)
    assert edited_run.returncode == 0, edited_run.stderr
    edited = json.loads(assessment.read_text(encoding="utf-8"))
    _validate(edited)
    assert edited["script"]["sha256"] == _sha(script)
    assert edited["script"]["sha256"] != first["script"]["sha256"]
    assert edited["source"] == first["source"]
    assert edited["drawing"] != first["drawing"]
    assert inspection.read_bytes() == inspection_bytes
    assert source.count("sheet.build()") == 1

    # Preparation removes the prior successful artifact before anything can fail.  A build
    # error therefore cannot leave it looking current.
    failed_source = script.read_text(encoding="utf-8").replace(
        "drawing = sheet.build()",
        'raise RuntimeError("intentional build failure")\ndrawing = sheet.build()',
    )
    script.write_text(failed_source, encoding="utf-8")
    failed = _run(script)
    assert failed.returncode != 0
    assert not assessment.exists()


def test_foreign_destination_is_preserved_and_refused(tmp_path) -> None:
    script = tmp_path / "drawing.py"
    source = tmp_path / "source.step"
    assessment = tmp_path / "drawing.draftwright-assessment.json"
    script.write_text("# exact script\n", encoding="utf-8")
    source.write_bytes(b"STEP")
    assessment.write_text('{"schema":"someone-else","keep":true}\n', encoding="utf-8")

    assert invalidate_replay_assessment(assessment) is False
    with pytest.raises(FileExistsError, match="foreign assessment"):
        prepare_replay_assessment(
            assessment,
            script_path=script,
            source_path=source,
            source_name="source.step",
            pmi_mode="off",
            formats=("svg",),
            reproducible=True,
        )
    assert json.loads(assessment.read_text(encoding="utf-8"))["keep"] is True


def test_missing_strict_report_authority_writes_nothing(tmp_path) -> None:
    script = tmp_path / "drawing.py"
    output = tmp_path / "drawing.svg"
    assessment = tmp_path / "drawing.draftwright-assessment.json"
    script.write_text("# exact script\n", encoding="utf-8")
    output.write_text("<svg/>", encoding="utf-8")
    prepared = prepare_replay_assessment(
        assessment,
        script_path=script,
        source_path=None,
        source_name=None,
        pmi_mode="off",
        formats=("svg",),
        reproducible=True,
    )

    class RefusingDrawing:
        def report(self):
            raise RuntimeError("strict report unavailable")

    with pytest.raises(RuntimeError, match="strict report unavailable"):
        prepared.write(RefusingDrawing(), {"svg": str(output)})  # type: ignore[arg-type]
    assert not assessment.exists()


def test_owned_stale_assessment_is_removed_but_malformed_json_is_not(tmp_path) -> None:
    owned = tmp_path / "owned.json"
    owned.write_text(json.dumps({"schema": ASSESSMENT_SCHEMA}), encoding="utf-8")
    assert invalidate_replay_assessment(owned) is True
    assert not owned.exists()

    foreign = tmp_path / "foreign.json"
    foreign.write_text("not JSON", encoding="utf-8")
    assert invalidate_replay_assessment(foreign) is False
    assert foreign.read_text(encoding="utf-8") == "not JSON"


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"pmi_mode": "guess"}, ValueError),
        ({"formats": "svg"}, TypeError),
        ({"formats": ("obj",)}, ValueError),
        ({"formats": ("svg", "svg")}, ValueError),
        ({"reproducible": 1}, TypeError),
        ({"source_name": "source.step"}, ValueError),
    ],
)
def test_prepare_refuses_ambiguous_or_unserializable_run_identity(
    tmp_path, overrides, error
) -> None:
    script = tmp_path / "drawing.py"
    script.write_text("# exact script\n", encoding="utf-8")
    options = {
        "script_path": script,
        "source_path": None,
        "source_name": None,
        "pmi_mode": "off",
        "formats": ("svg",),
        "reproducible": True,
        **overrides,
    }

    with pytest.raises(error):
        prepare_replay_assessment(tmp_path / "assessment.json", **options)


def test_prepare_requires_a_name_for_a_step_source(tmp_path) -> None:
    script = tmp_path / "drawing.py"
    source = tmp_path / "source.step"
    script.write_text("# exact script\n", encoding="utf-8")
    source.write_bytes(b"STEP")

    with pytest.raises(ValueError, match="source name"):
        prepare_replay_assessment(
            tmp_path / "assessment.json",
            script_path=script,
            source_path=source,
            source_name=None,
            pmi_mode="off",
            formats=("svg",),
            reproducible=True,
        )


@pytest.mark.parametrize("change", ["modify", "remove"])
def test_replay_refuses_a_script_that_changed_after_preparation(tmp_path, change) -> None:
    script = tmp_path / "drawing.py"
    script.write_text("# exact script\n", encoding="utf-8")
    prepared = prepare_replay_assessment(
        tmp_path / "assessment.json",
        script_path=script,
        source_path=None,
        source_name=None,
        pmi_mode="off",
        formats=(),
        reproducible=True,
    )
    if change == "modify":
        script.write_text("# edited script\n", encoding="utf-8")
    else:
        script.unlink()

    with pytest.raises(RuntimeError, match="generated script (changed|became unavailable)"):
        prepared.write(object(), {})  # type: ignore[arg-type]


def test_direct_write_records_a_live_object_and_exact_export(tmp_path) -> None:
    script = tmp_path / "drawing.py"
    output = tmp_path / "drawing.svg"
    destination = tmp_path / "assessment.json"
    script.write_text("# exact script\n", encoding="utf-8")
    output.write_text("<svg/>\n", encoding="utf-8")
    prepared = prepare_replay_assessment(
        destination,
        script_path=script,
        source_path=None,
        source_name=None,
        pmi_mode="off",
        formats=("svg",),
        reproducible=True,
    )

    assert prepared.write(_ReportDrawing(_declared_report()), {"svg": str(output)}) == str(
        destination
    )
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["source"] == {
        "kind": "build123d",
        "name": None,
        "sha256": None,
        "reason": "a live object replay has no immutable STEP byte source",
    }
    assert document["outputs"] == [
        {
            "format": "svg",
            "path": str(output),
            "sha256": _sha(output),
            "bytes": output.stat().st_size,
        }
    ]


def test_direct_write_refuses_a_changed_step_source(tmp_path) -> None:
    script = tmp_path / "drawing.py"
    source = tmp_path / "source.step"
    script.write_text("# exact script\n", encoding="utf-8")
    source.write_bytes(b"first")
    prepared = prepare_replay_assessment(
        tmp_path / "assessment.json",
        script_path=script,
        source_path=source,
        source_name="source.step",
        pmi_mode="off",
        formats=(),
        reproducible=True,
    )
    source.write_bytes(b"second")

    with pytest.raises(RuntimeError, match="STEP source changed during replay"):
        prepared.write(_ReportDrawing(_declared_report()), {})


def test_direct_write_refuses_a_non_authoritative_report(tmp_path) -> None:
    script = tmp_path / "drawing.py"
    script.write_text("# exact script\n", encoding="utf-8")
    prepared = prepare_replay_assessment(
        tmp_path / "assessment.json",
        script_path=script,
        source_path=None,
        source_name=None,
        pmi_mode="off",
        formats=(),
        reproducible=True,
    )

    with pytest.raises(RuntimeError, match="declared-sheet report v8"):
        prepared.write(_ReportDrawing({"schema": "draftwright-report"}), {})


def test_direct_write_refuses_an_export_set_mismatch(tmp_path) -> None:
    script = tmp_path / "drawing.py"
    script.write_text("# exact script\n", encoding="utf-8")
    prepared = prepare_replay_assessment(
        tmp_path / "assessment.json",
        script_path=script,
        source_path=None,
        source_name=None,
        pmi_mode="off",
        formats=("svg",),
        reproducible=True,
    )

    with pytest.raises(RuntimeError, match="export result"):
        prepared.write(_ReportDrawing(_declared_report()), {})


def test_direct_write_preserves_a_foreign_destination_created_during_replay(tmp_path) -> None:
    script = tmp_path / "drawing.py"
    output = tmp_path / "drawing.svg"
    destination = tmp_path / "assessment.json"
    script.write_text("# exact script\n", encoding="utf-8")
    output.write_text("<svg/>\n", encoding="utf-8")
    prepared = prepare_replay_assessment(
        destination,
        script_path=script,
        source_path=None,
        source_name=None,
        pmi_mode="off",
        formats=("svg",),
        reproducible=True,
    )
    destination.write_text('{"schema":"someone-else","keep":true}\n', encoding="utf-8")

    with pytest.raises(FileExistsError, match="foreign assessment"):
        prepared.write(_ReportDrawing(_declared_report()), {"svg": str(output)})
    assert json.loads(destination.read_text(encoding="utf-8"))["keep"] is True
