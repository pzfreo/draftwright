"""Candidate-preview holdouts for authored Sheet and generated Sheet scripts."""

from runpy import run_path

from build123d import Box, Cylinder, export_step

from draftwright import Sheet
from draftwright.layout_selection import _compare, drawing_record
from draftwright.sheet_emit import generate_sheet_script


def _authored_sheet(part, policy):
    sheet = Sheet(part, page="A4", scale=2, annotation_layout=policy)
    hole = sheet.hole(diameter=4, at=(0, 0, 2.5), axis="z", depth=5)
    sheet.datum("A", part.faces().sort_by()[-1])
    sheet.control(hole).position(0.1, to="A")
    sheet.authored_dimensions()
    sheet.dimension(hole, "bore.diameter")
    return sheet.build()


def test_candidate_preview_preserves_authored_sheet_obligations():
    part = Box(30, 20, 5) - Cylinder(2, 10)
    baseline = _authored_sheet(part, "baseline")
    candidate = _authored_sheet(part, "candidate-preview")

    baseline_record = drawing_record(baseline)
    candidate_record = drawing_record(candidate)
    comparison = _compare(baseline_record, candidate_record)
    assert any(feature.kind == "hole" for feature in baseline.model().features)
    assert baseline_record["manifest"]["coverage"]["requirements"] > 0
    assert any(
        name.startswith("m_gdt") and any(owner["kind"] == "hole" for owner in annotation["owners"])
        for name, annotation in baseline_record["manifest"]["annotations"].items()
    )
    assert candidate.report()["schema_version"] == 8
    assert candidate.annotation_scheme_decision["policy"] == "candidate-preview"
    assert candidate.annotation_scheme_decision["safety_evidence"]["admission_ready"] is False
    assert comparison["parity"]["passed"], comparison["parity"]


def test_generated_sheet_script_replays_candidate_preview_semantics(tmp_path):
    source = tmp_path / "authored-source.step"
    export_step(Box(30, 20, 5) - Cylinder(2, 10), source)

    def drawing(policy):
        script = generate_sheet_script(
            str(source),
            out=str(tmp_path / policy),
            title="SHEET HOLDOUT",
            page="A4",
            scale=2,
            annotation_layout=policy,
            formats=(),
            inspect=False,
        )
        return run_path(script)["drawing"]

    baseline = drawing("baseline")
    candidate = drawing("candidate-preview")
    baseline_record = drawing_record(baseline)
    candidate_record = drawing_record(candidate)
    comparison = _compare(baseline_record, candidate_record)

    assert baseline.model().features
    assert baseline_record["manifest"]["annotations"]
    assert candidate.annotation_scheme_decision["policy"] == "candidate-preview"
    assert comparison["parity"]["passed"], comparison["parity"]
