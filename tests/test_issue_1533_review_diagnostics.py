"""Explain bounded critique and inspect its actual ink without changing the drawing."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import jsonschema
import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Drawing, build_drawing
from draftwright.linting import LintIssue


@pytest.fixture(scope="module", params=[False, True], ids=["automatic", "declared"])
def drawing(request):
    d = build_drawing(Box(20, 20, 5) - Pos(5, 0, 0) * Cylinder(4, 5), scale=2)
    if request.param:
        d = build_drawing(d.working_part, model=d.model(), scale=2)
    return d


def _leader(drawing):
    (name,) = [
        name
        for name, item in drawing.iter_annotations()
        if hasattr(item, "tip") and drawing.registry.measurement_of(name)
    ]
    return name, drawing.get_annotation(name)


def test_warning_penalty_is_explained_without_claiming_completeness(monkeypatch):
    d = Drawing(
        scale=1,
        page_w=100,
        page_h=100,
        tb_w=20,
        draft=None,
        look_at=None,
        dist=1,
        centroid=None,
        out="",
    )
    warnings = [LintIssue("warning", "custom critique", code="custom") for _ in range(35)]
    monkeypatch.setattr(d, "lint", lambda: warnings)
    summary = d.lint_summary()
    assert summary["passed"] and summary["score"] == summary["diagnostic_score"] == 0
    assert summary["warnings"] == 35 and summary["errors"] == 0
    explanation = summary["review"]
    assert "0 errors, 35 warnings" in explanation["summary"]
    assert "passed=true; passed is true only with no error-severity" in explanation["summary"]
    assert "not a drawing-quality or completeness score" in explanation["summary"]
    assert "Coverage unavailable" in explanation["coverage"]
    assert "Unavailable:" in explanation["fidelity"]
    assert "Not assessed" in explanation["manufacturing_intent"]
    json.dumps(summary, allow_nan=False)


def test_explanations_retain_each_existing_outcome_and_bound(drawing):
    summary = drawing.lint_summary()
    coverage = summary["quality"]["completeness"]
    assert coverage["available"] and coverage["requirements"] > 0
    for state in (
        "placed",
        "satisfied_by_structured_note",
        "suppressed",
        "dropped",
        "missing",
        "unverifiable",
        "unsupported",
    ):
        assert f"{coverage[state]} {state.replace('_', ' ')}" in summary["review"]["coverage"]
    assert "partial coverage" in summary["review"]["coverage"]
    assert "not proof" in summary["review"]["fidelity"]
    assert "Free text does not establish" in summary["review"]["manufacturing_intent"]


def test_target_preview_uses_recorded_name_view_and_actual_tip(drawing, monkeypatch, tmp_path):
    name, leader = _leader(drawing)
    assert not [i for i in drawing.lint() if i.code.startswith("diameter_leader_target_")]
    old_tip = leader.tip
    monkeypatch.setattr(leader, "location", Pos(0.7, 0, 0) * leader.location)
    assert leader.tip[0] == pytest.approx(old_tip[0] + 0.7)
    before = drawing.lint_summary()
    (issue,) = [i for i in before["issues"] if i["code"] == "diameter_leader_target_mismatch"]
    assert issue["annotation_name"] == name and issue["view"] == drawing.view_of(name)
    assert "outside the named physical boundary tolerance" in issue["evidence_reason"]
    items = tuple(id(item) for item in drawing.items)
    annotations = drawing.annotations()
    measurements = drawing.registry.measurement_of(name)
    paths = (drawing.svg_path, drawing.dxf_path)
    preview = tmp_path / "target.svg"
    assert drawing.preview_annotation(issue["annotation_name"], preview) == str(preview)
    root = ET.parse(preview).getroot()
    ns = {"s": "http://www.w3.org/2000/svg"}
    overlay = root.find("s:g[@id='draftwright-diagnostic']", ns)
    assert overlay is not None
    circle = overlay.find("s:circle", ns)
    assert float(circle.attrib["cx"]) == pytest.approx(leader.tip[0])
    assert float(circle.attrib["cy"]) == pytest.approx(-leader.tip[1])
    assert "Physical target not certified" in " ".join(overlay.itertext())
    assert name in " ".join(overlay.itertext()) and issue["view"] in " ".join(overlay.itertext())
    assert tuple(id(item) for item in drawing.items) == items
    assert drawing.annotations() == annotations
    assert drawing.registry.measurement_of(name) == measurements
    assert (drawing.svg_path, drawing.dxf_path) == paths
    assert drawing.lint_summary() == before


def test_preview_refuses_unfinished_edits_and_unknown_names(drawing, tmp_path):
    name, _ = _leader(drawing)
    path = tmp_path / "preview.svg"
    path.write_text("existing artifact")
    with pytest.raises(KeyError):
        drawing.preview_annotation("unknown", path)
    with pytest.raises(ValueError, match=".svg"):
        drawing.preview_annotation(name, tmp_path / "preview.pdf")
    with drawing.deferred(), pytest.raises(ValueError, match="finish deferred"):
        drawing.preview_annotation(name, path)
    assert path.read_text() == "existing artifact"


def test_open_lint_payload_keeps_report_v3_schema():
    d = build_drawing(Box(20, 20, 5) - Cylinder(4, 5))
    report = d.report()
    assert report["schema_version"] == 3 and "review" in report["lint"]
    schema = Path(__file__).parents[1] / "docs/reference/draftwright-report-v3.schema.json"
    jsonschema.validate(report, json.loads(schema.read_text()))
    before = d.lint_summary()["quality"]["completeness"]
    assert before["placed"] > 0
    note = d.note("All holes and requirements are covered", (10, 10))
    assert d.lint_summary()["quality"]["completeness"] == before
    name, _ = _leader(d)
    d.remove(name)
    after = d.lint_summary()["quality"]["completeness"]
    assert after["requirements"] == before["requirements"]
    assert after["placed"] < before["placed"]
    assert after["missing"] > before["missing"]
    d.remove(note)


def test_preview_handles_unowned_note_and_preserves_artifact_on_bad_geometry(
    drawing, tmp_path, monkeypatch
):
    name = drawing.note("Inspect this text", (10, 10))
    path = tmp_path / "note.svg"
    try:
        drawing.preview_annotation(name, path)
        root = ET.parse(path).getroot()
        overlay = root.find("{http://www.w3.org/2000/svg}g[@id='draftwright-diagnostic']")
        assert "view: unavailable" in " ".join(overlay.itertext())
        assert overlay.find("{http://www.w3.org/2000/svg}circle") is None
        before = path.read_bytes()
        monkeypatch.setattr(drawing, "get_annotation", lambda _: object())
        with pytest.raises(ValueError, match="bounds unavailable"):
            drawing.preview_annotation(name, path)
        assert path.read_bytes() == before
    finally:
        drawing.remove(name)
