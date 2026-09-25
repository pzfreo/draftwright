"""Declared Sheet reports retain critique without inventing recognition authority (#1688)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos, export_step
from jsonschema.validators import validator_for

from draftwright import ReportUnavailableError, Sheet
from draftwright.reporting import declared_drawing_report
from draftwright.sheet_emit import generate_sheet_script

_SCHEMA = Path(__file__).parents[1] / "docs/reference/draftwright-report-v8.schema.json"


def _drawing():
    part = Box(300, 200, 2) - Pos(-40, -60, 0) * Cylinder(2.1, 8)
    sheet = Sheet(part, title="PROBE", number="DWG-001")
    hole = sheet.hole(diameter=4.2, at=(-40, -60, 1), axis="z", depth=2)
    sheet.authored_dimensions()
    sheet.dimension(hole, "bore.diameter")
    return sheet.build()


def test_declared_sheet_report_uses_final_ir_authority_and_retains_lint() -> None:
    drawing = _drawing()

    report = drawing.report()

    assert report["schema"] == "draftwright-report"
    assert report["schema_version"] == 8
    assert report["scope"] == "declared-sheet"
    assert report["source"] == {"kind": "build123d", "name": None}
    assert report["declarations"]["authority"] == "final-ir"
    assert report["declarations"]["recognition_correspondence"] == "unavailable"
    assert report["declarations"]["entries"] == []
    assert "no correspondence was inferred" in report["declarations"]["reason"]
    assert report["declarations"]["feature_count"] == len(drawing.model().features)
    assert sum(report["declarations"]["by_kind"].values()) == len(drawing.model().features)
    assert report["lint"] == json.loads(json.dumps(drawing.lint_summary()))
    assert "recognition" not in report

    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    validator_for(schema).check_schema(schema)
    validator_for(schema)(schema).validate(report)
    json.dumps(report, allow_nan=False)


def test_declared_sheet_report_writes_the_same_json_atomically(tmp_path) -> None:
    drawing = _drawing()
    destination = tmp_path / "declared.draftwright.json"

    returned = drawing.write_report(destination)

    assert returned == str(destination)
    assert json.loads(destination.read_text(encoding="utf-8")) == drawing.report()


def test_generated_script_can_write_its_own_declared_report(tmp_path) -> None:
    step = tmp_path / "source.step"
    export_step(Box(40, 30, 5) - Cylinder(2, 10), step)
    script = Path(generate_sheet_script(step, out=str(tmp_path / "generated")))
    source = script.read_text(encoding="utf-8")
    marker = "drawing = sheet.build()"
    prefix = source[: source.index(marker) + len(marker)]
    namespace = {}

    exec(compile(prefix, str(script), "exec"), namespace)  # noqa: S102 — emitted public DSL
    report = namespace["drawing"].report()

    assert report["schema_version"] == 8
    assert report["scope"] == "declared-sheet"
    assert report["declarations"]["recognition_correspondence"] == "generation-run-references"
    assert report["declarations"]["entries"]


@pytest.mark.parametrize(
    ("passed", "warnings", "coverage", "audited_score", "expected"),
    (
        (False, 0, "complete", 1.0, "needs-attention"),
        (True, 1, "complete", 1.0, "needs-attention"),
        (True, 0, "unavailable", 1.0, "needs-attention"),
        (True, 0, "indeterminate", 1.0, "needs-attention"),
        (True, 0, "complete", None, "needs-attention"),
        (True, 0, "complete", 1.0, "bounded-clear"),
    ),
)
def test_declared_report_status_is_fail_closed(
    passed, warnings, coverage, audited_score, expected
) -> None:
    lint = {
        "passed": passed,
        "warnings": warnings,
        "quality": {"completeness": {"coverage": coverage, "audited_score": audited_score}},
    }

    report = declared_drawing_report(model=SimpleNamespace(features=[]), lint=lint, source=None)

    assert report["status"] == expected


@pytest.mark.parametrize(
    ("model", "match"),
    (
        (None, "has no final IR model"),
        (SimpleNamespace(features=[SimpleNamespace(kind="")]), "without a kind"),
    ),
)
def test_declared_report_refuses_an_invalid_final_ir(model, match) -> None:
    with pytest.raises(ReportUnavailableError, match=match):
        declared_drawing_report(model=model, lint={}, source=None)
