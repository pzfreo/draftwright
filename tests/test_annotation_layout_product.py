"""Public, build-scoped selection of the verified annotation layout."""

from pathlib import Path
from runpy import run_path
from types import SimpleNamespace

import pytest
from build123d import Box, export_step
from typer.testing import CliRunner

from draftwright import Sheet, build_drawing
from draftwright.cli import app
from draftwright.layout_selection import select_best_annotation_layout
from draftwright.sheet_emit import generate_sheet_script


@pytest.mark.parametrize("entry", [build_drawing, Sheet, generate_sheet_script])
def test_public_front_doors_reject_unknown_layout_policy(entry, tmp_path):
    with pytest.raises(ValueError, match="annotation_layout must be"):
        entry(tmp_path / "missing.step", annotation_layout="unverified")


def test_best_layout_selects_verified_larger_iso_on_same_sheet_and_scale():
    part = Box(20, 10, 5)
    baseline = build_drawing(part, page="A4", scale=2, annotation_layout="baseline")
    selected = build_drawing(part, page="A4", scale=2, annotation_layout="best")

    decision = selected.annotation_scheme_decision
    assert decision["policy"] == "best"
    assert decision["selected_trial"] == "iso-growth"
    assert (selected.page_w, selected.page_h, selected.scale) == (
        baseline.page_w,
        baseline.page_h,
        baseline.scale,
    )
    assert selected.scale_decision == baseline.scale_decision
    assert selected.view_bounds("front") == baseline.view_bounds("front")
    assert selected.view_bounds("plan") == baseline.view_bounds("plan")
    assert selected.view_bounds("side") == baseline.view_bounds("side")
    assert decision["trials"][0]["semantic_parity"] is True
    assert decision["trials"][0]["missing_annotations"] == 0
    assert decision["trials"][0]["iso_area_ratio"] >= 1.10


def test_best_layout_skips_speculation_without_automatic_annotations():
    selected = build_drawing(Box(20, 10, 5), auto_dims=False, annotation_layout="best")

    assert selected.annotation_scheme_decision["status"] == "retained_baseline"
    assert selected.annotation_scheme_decision["reason"] == "automatic_annotations_disabled"


def test_selector_keeps_baseline_when_a_tidy_candidate_loses_a_dimension(monkeypatch):
    import draftwright.layout_selection as selection

    def record(annotations, quality):
        return {
            "page": [297.0, 210.0],
            "scale": 1.0,
            "views": ["front"],
            "manifest": {
                "annotations": annotations,
                "interior_dimensions": [],
                "coverage": {
                    "requirements": 1,
                    "placed": len(annotations),
                    "missing": 1 - len(annotations),
                    "unsupported": 0,
                    "unverifiable": 0,
                },
                "drops": {},
                "arrangement_quality": {"selection_key": quality},
                "blocker_identities": [],
            },
        }

    required = {"width": {"type": "LinearDimension", "label": "20"}}
    baseline = SimpleNamespace(
        scale=1.0,
        annotation_scheme_decision={},
        record=record(required, (1, 0, 1, 0, 0, -1.0, 62370.0)),
    )
    candidate = SimpleNamespace(
        scale=1.0,
        annotation_scheme_decision={},
        record=record({}, (0, 0, 0, 0, 0, -1.0, 62370.0)),
    )
    monkeypatch.setattr(selection, "drawing_record", lambda drawing: drawing.record)

    selected = select_best_annotation_layout(baseline, lambda _profile: candidate)

    assert selected is baseline
    assert selected.annotation_scheme_decision["status"] == "retained_baseline"
    assert [trial["verdict"] for trial in selected.annotation_scheme_decision["trials"]] == [
        "ineligible",
        "ineligible",
    ]


def test_speculative_build_failure_keeps_the_finished_baseline(monkeypatch):
    import draftwright.layout_selection as selection

    baseline = SimpleNamespace(
        scale=1.0,
        annotation_scheme_decision={},
        record={
            "manifest": {
                "arrangement_quality": {"selection_key": (0, 0, 0, 0, 0, -1.0, 62370.0)},
                "interior_dimensions": [],
            }
        },
    )
    monkeypatch.setattr(selection, "drawing_record", lambda drawing: drawing.record)

    def fail(_profile):
        raise ValueError("candidate cannot fit")

    selected = select_best_annotation_layout(baseline, fail)

    assert selected is baseline
    assert selected.annotation_scheme_decision["status"] == "retained_baseline"
    assert selected.annotation_scheme_decision["trials"] == [
        {"name": "iso-growth", "verdict": "build_failed", "error": "candidate cannot fit"}
    ]


@pytest.mark.parametrize(
    ("hard_defects", "expected_trial", "expected_arrangement"),
    [(1, "columns", "columns"), (0, "planned", "staggered-side")],
)
def test_trial_order_prioritizes_hard_defects_or_required_content(
    monkeypatch, hard_defects, expected_trial, expected_arrangement
):
    import draftwright.layout_selection as selection

    annotation = {"width": {"type": "LinearDimension", "label": "20"}}

    def record(quality):
        return {
            "page": [297.0, 210.0],
            "scale": 1.0,
            "views": ["front"],
            "manifest": {
                "annotations": annotation,
                "interior_dimensions": ["width"],
                "coverage": {
                    "requirements": 1,
                    "placed": 1,
                    "missing": 0,
                    "unsupported": 0,
                    "unverifiable": 0,
                },
                "drops": {},
                "arrangement_quality": {"selection_key": quality},
                "blocker_identities": [],
            },
        }

    baseline = SimpleNamespace(
        scale=1.0,
        annotation_scheme_decision={},
        record=record((hard_defects, 1, hard_defects, 1, 0, -1.0, 62370.0)),
    )
    candidate = SimpleNamespace(
        scale=1.0,
        annotation_scheme_decision={},
        record=record((0, 0, 0, 1, 0, -1.0, 62370.0)),
    )
    monkeypatch.setattr(selection, "drawing_record", lambda drawing: drawing.record)
    profiles = []

    def build(profile):
        profiles.append(profile)
        return candidate

    selected = select_best_annotation_layout(baseline, build)

    assert selected is candidate
    assert selected.annotation_scheme_decision["selected_trial"] == expected_trial
    assert [profile.arrangement for profile in profiles] == [expected_arrangement]


def test_sheet_and_generated_script_forward_layout_policy(tmp_path):
    sheet = Sheet(Box(20, 10, 5), annotation_layout="best")
    assert sheet._opts["annotation_layout"] == "best"

    script = Path(
        generate_sheet_script(
            Box(20, 10, 5),
            out=str(tmp_path / "box"),
            page="A4",
            scale=2,
            annotation_layout="best",
            formats=(),
            inspect=False,
        )
    ).read_text(encoding="utf-8")
    assert "annotation_layout='best'" in script


def test_generated_step_script_replays_the_layout_selection(tmp_path):
    source = tmp_path / "box.step"
    export_step(Box(20, 10, 5), source)
    direct = build_drawing(str(source), annotation_layout="best")
    script = generate_sheet_script(
        str(source),
        out=str(tmp_path / "box"),
        annotation_layout="best",
        formats=(),
        inspect=False,
    )

    drawing = run_path(script)["drawing"]
    assert drawing.annotation_scheme_decision["policy"] == "best"
    assert drawing.annotation_scheme_decision["status"] == "candidate"
    assert (drawing.page_w, drawing.page_h, drawing.scale) == (
        direct.page_w,
        direct.page_h,
        direct.scale,
    )


def test_cli_forwards_layout_policy_to_a_rendered_build(monkeypatch):
    import draftwright.builder as builder

    forwarded = []

    class Drawing:
        out = "out"
        annotation_scheme_decision = {"selected_trial": "columns"}

        def export(self, *, formats):
            return {name: f"out.{name}" for name in formats}

    def capture(*_args, **kwargs):
        forwarded.append(kwargs)
        return Drawing()

    monkeypatch.setattr(builder, "build_drawing", capture)
    result = CliRunner().invoke(
        app,
        ["part.step", "--annotation-layout", "best", "--format", "svg", "--no-report"],
    )

    assert result.exit_code == 0, result.output
    assert forwarded[0]["annotation_layout"] == "best"
    assert "Selected annotation layout: columns" in result.output
