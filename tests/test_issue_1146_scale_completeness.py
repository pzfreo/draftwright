"""#1146 — explicit drawing scales may not silently reduce completeness."""

from __future__ import annotations

import re
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import (
    ScaleCompletenessWarning,
    ScaleIncompatibilityError,
    Sheet,
    SoftDeprecationWarning,
    build_drawing,
)
from draftwright.builder import _is_required_scale_drop
from draftwright.linting import LintIssue


def _scale_sensitive_plate():
    """A4 at 1:1 drops one X location; the same required set fits at 1:2."""
    part = Box(100, 100, 8)
    part -= Pos(-30, -20, 0) * Cylinder(2, 8)
    part -= Pos(25, 25, 0) * Cylinder(2.7, 8)
    return part


def _placement_drops(drawing):
    return [issue for issue in drawing.lint() if _is_required_scale_drop(issue)]


@pytest.mark.timeout(120)
def test_fixture_proves_requested_scale_loses_an_outcome_but_half_scale_is_complete():
    with pytest.warns(ScaleCompletenessWarning, match="scale_policy='permissive'"):
        requested = build_drawing(
            _scale_sensitive_plate(),
            page="A4",
            scale=1.0,
            scale_policy="permissive",
            repair=False,
        )
    half = build_drawing(
        _scale_sensitive_plate(),
        page="A4",
        scale=0.5,
        scale_policy="permissive",
        repair=False,
    )

    (drop,) = _placement_drops(requested)
    assert drop.code == "location_ref_dropped"
    assert drop.measurement_ids and drop.hole_requirement_ids
    assert _placement_drops(half) == []


@pytest.mark.timeout(120)
def test_default_fallback_returns_largest_complete_standard_scale_and_reports_decision():
    with pytest.warns(ScaleCompletenessWarning, match="complete fallback scale 0.5"):
        drawing = build_drawing(_scale_sensitive_plate(), page="A4", scale=1.0, repair=False)

    assert drawing.scale == 0.5
    assert _placement_drops(drawing) == []
    assert drawing.scale_decision == {
        "policy": "fallback",
        "requested_scale": 1.0,
        "effective_scale": 0.5,
        "status": "fallback",
        "blockers": drawing.scale_decision["blockers"],
        "attempted_scales": (1.0, 0.5),
        "attempts": drawing.scale_decision["attempts"],
    }
    (blocker,) = drawing.scale_decision["blockers"]
    assert blocker["code"] == "location_ref_dropped"
    assert blocker["measurements"][0]["parameter"] == "location.location"
    assert blocker["hole_requirements"][0]["parameter"] == "location.location.x"
    assert [(item["scale"], item["status"]) for item in drawing.scale_decision["attempts"]] == [
        (1.0, "incomplete"),
        (0.5, "complete"),
    ]


@pytest.mark.timeout(120)
def test_strict_policy_fails_with_machine_readable_required_outcomes():
    with pytest.raises(ScaleIncompatibilityError) as caught:
        build_drawing(
            _scale_sensitive_plate(),
            page="A4",
            scale=1.0,
            scale_policy="strict",
            repair=False,
        )

    decision = caught.value.decision
    assert decision["status"] == "rejected"
    assert decision["requested_scale"] == decision["effective_scale"] == 1.0
    assert decision["attempted_scales"] == (1.0,)
    assert decision["attempts"][0]["status"] == "incomplete"
    assert {item["code"] for item in decision["blockers"]} == {"location_ref_dropped"}


@pytest.mark.timeout(120)
def test_permissive_policy_is_explicit_warns_and_preserves_degraded_request():
    with pytest.warns(ScaleCompletenessWarning, match="returning the incomplete drawing"):
        drawing = build_drawing(
            _scale_sensitive_plate(),
            page="A4",
            scale=1.0,
            scale_policy="permissive",
            repair=False,
        )

    assert drawing.scale == 1.0
    assert drawing.scale_decision["status"] == "degraded"
    assert drawing.scale_decision["policy"] == "permissive"
    assert {item.code for item in _placement_drops(drawing)} == {"location_ref_dropped"}


def test_complete_requested_and_automatic_scales_report_honest_resolutions():
    explicit = build_drawing(Box(40, 30, 10), page="A4", scale=1.0)
    automatic = build_drawing(Box(40, 30, 10))

    assert explicit.scale_decision == {
        "policy": "fallback",
        "requested_scale": 1.0,
        "effective_scale": 1.0,
        "status": "honored",
        "blockers": (),
        "attempted_scales": (1.0,),
        "attempts": ({"scale": 1.0, "status": "complete", "blockers": ()},),
    }
    assert automatic.scale_decision == {
        "policy": "automatic",
        "requested_scale": None,
        "effective_scale": automatic.scale,
        "status": "automatic",
        "blockers": (),
        "attempted_scales": (),
        "attempts": (),
    }


def test_policy_vocabulary_and_applicability_fail_before_build():
    with pytest.raises(ValueError, match="scale_policy must be"):
        build_drawing(Box(10, 10, 10), scale=1.0, scale_policy="quiet")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="only when an explicit scale"):
        build_drawing(Box(10, 10, 10), scale_policy="strict")


def test_info_severity_or_priority_cannot_hide_a_required_drop():
    issue = LintIssue(
        severity="info",
        code="location_ref_dropped",
        message="mandatory pinned candidate did not fit",
    )
    assert _is_required_scale_drop(issue)


def test_validation_findings_are_not_scale_placement_failures():
    issue = LintIssue(
        severity="error",
        code="declaration_mismatch",
        message="the declared feature differs from the solid",
        outcome_stage="validation",
    )
    assert not _is_required_scale_drop(issue)


def test_unowned_transactional_table_failure_is_not_a_required_scale_drop():
    assert not _is_required_scale_drop(
        LintIssue(severity="warning", code="table_dropped", message="fallback restored")
    )
    assert _is_required_scale_drop(
        LintIssue(
            severity="warning",
            code="table_dropped",
            message="semantic table lost",
            source_ids=("required-table",),
        )
    )


def test_fallback_reports_exhaustion_at_the_hard_rendering_floor(monkeypatch):
    import draftwright.builder as builder

    drop = LintIssue(
        severity="warning",
        code="location_ref_dropped",
        message="required location did not fit",
    )

    def fake_build(*args, scale, **kwargs):
        if scale == 0.2:
            raise ValueError("drawing geometry degenerates below the renderable floor")
        candidate_drop = (
            LintIssue(severity="warning", code="gdt_dropped", message="datum frame did not fit")
            if scale == 0.5
            else drop
        )
        return SimpleNamespace(
            scale=scale, lint=lambda: [candidate_drop], recognition=lambda: None, _analysis=None
        )

    monkeypatch.setattr(builder, "_SCALES", (1.0, 0.5, 0.2, 0.1))
    monkeypatch.setattr(builder, "_build_drawing_once", fake_build)

    with pytest.raises(ScaleIncompatibilityError) as caught:
        builder.build_drawing(Box(10, 10, 10), scale=1.0)

    assert caught.value.decision == {
        "policy": "fallback",
        "requested_scale": 1.0,
        "effective_scale": 0.5,
        "status": "no_complete_scale",
        "blockers": caught.value.decision["blockers"],
        "attempted_scales": (1.0, 0.5, 0.2),
        "attempts": caught.value.decision["attempts"],
    }
    assert caught.value.decision["blockers"][0]["code"] == "gdt_dropped"
    assert [(item["scale"], item["status"]) for item in caught.value.decision["attempts"]] == [
        (1.0, "incomplete"),
        (0.5, "incomplete"),
        (0.2, "render_floor"),
    ]
    assert "no complete standard fallback" not in str(caught.value)


def test_fallback_does_not_hide_an_unrelated_build_error(monkeypatch):
    import draftwright.builder as builder

    drop = LintIssue(
        severity="warning",
        code="location_ref_dropped",
        message="required location did not fit",
    )

    def fake_build(*args, scale, **kwargs):
        if scale < 1.0:
            raise ValueError("invalid declared model")
        return SimpleNamespace(
            scale=scale, lint=lambda: [drop], recognition=lambda: None, _analysis=None
        )

    monkeypatch.setattr(builder, "_SCALES", (1.0, 0.5))
    monkeypatch.setattr(builder, "_build_drawing_once", fake_build)

    with pytest.raises(ValueError, match="invalid declared model"):
        builder.build_drawing(Box(10, 10, 10), scale=1.0)


def test_monotone_step_separation_failure_does_not_rebuild_smaller_scales(monkeypatch):
    import draftwright.builder as builder

    calls = []
    drop = LintIssue(
        severity="warning",
        code="step_dim_dropped",
        message="5 step height(s) too closely spaced to dimension at this scale",
    )

    def fake_build(*args, scale, **kwargs):
        calls.append(scale)
        return SimpleNamespace(
            scale=scale, lint=lambda: [drop], recognition=lambda: None, _analysis=None
        )

    monkeypatch.setattr(builder, "_SCALES", (1.0, 0.5, 0.2, 0.1))
    monkeypatch.setattr(builder, "_build_drawing_once", fake_build)

    with pytest.raises(ScaleIncompatibilityError) as caught:
        builder.build_drawing(Box(10, 10, 10), scale=1.0)

    assert calls == [1.0]
    assert caught.value.decision["status"] == "no_complete_scale"
    assert caught.value.decision["attempted_scales"] == (1.0,)


def test_scale_warning_category_remains_a_dependency_free_user_warning():
    namespace = runpy.run_path(
        str(Path(__file__).parents[1] / "src" / "draftwright" / "_warnings.py")
    )
    assert issubclass(namespace["ScaleCompletenessWarning"], UserWarning)


@pytest.mark.timeout(120)
def test_sheet_authored_table_footprint_participates_in_fallback_and_strict_policy():
    rows = [("NOTES",), *((f"{index}. " + "X" * 70,) for index in range(4))]

    fallback_sheet = Sheet(Box(100, 100, 8), page="A4", scale=1.0).table(
        rows, name="required_notes"
    )
    with pytest.warns(SoftDeprecationWarning):
        fallback_sheet.auto_dimensions()
    with pytest.warns(ScaleCompletenessWarning, match="fallback scale 0.5"):
        drawing = fallback_sheet.build()

    assert drawing.scale == 0.5
    assert "required_notes" in drawing.annotations()
    assert drawing.scale_decision["blockers"][0]["code"] == "table_dropped"
    assert drawing.scale_decision["blockers"][0]["source_ids"] == ("sheet.table:0:required_notes",)

    strict_sheet = Sheet(Box(100, 100, 8), page="A4", scale=1.0, scale_policy="strict").table(
        rows, name="required_notes"
    )
    with pytest.warns(SoftDeprecationWarning):
        strict_sheet.auto_dimensions()
    with pytest.raises(ScaleIncompatibilityError) as caught:
        strict_sheet.build()
    assert caught.value.decision["blockers"][0]["code"] == "table_dropped"


@pytest.mark.timeout(120)
def test_authored_table_footprint_can_select_a_larger_standard_page():
    drawing = (
        Sheet(Box(40, 30, 10), scale=1.0)
        .authored_dimensions()
        .table([("NOTES",), ("X" * 160,)], name="required_notes")
        .build()
    )

    assert drawing.scale == 1.0
    assert (drawing.page_w, drawing.page_h) == (420.0, 297.0)
    assert drawing.scale_decision["status"] == "honored"
    assert "required_notes" in drawing.annotations()


@pytest.mark.timeout(120)
def test_automatic_page_preserves_iso_budget_while_forced_page_honors_complete_layout():
    """Auto page fitness is stronger than strict completeness on a caller-forced page."""
    rows = [("H",), *(("X" * 60,) for _ in range(6))]

    def sheet(page=None):
        result = Sheet(
            Box(40, 30, 10), page=page, scale=1.0, scale_policy="strict"
        ).authored_dimensions()
        for index, prefer in enumerate(("tr", "bl", "br")):
            result.table(rows, name=f"required_{index}", prefer=prefer)
        return result

    automatic = sheet().build()
    forced = sheet("A4").build()

    assert (automatic.page_w, automatic.page_h) == (420.0, 297.0)
    assert (forced.page_w, forced.page_h) == (297.0, 210.0)
    assert forced.scale_decision["status"] == "honored"
    assert {f"required_{index}" for index in range(3)} <= set(forced.annotations())
    assert not [issue for issue in forced.lint() if issue.severity != "info"]


@pytest.mark.timeout(120)
def test_sheet_table_sources_are_stable_and_unique_when_display_names_are_reused():
    oversized = [("X" * 300,)]

    both_drop = (
        Sheet(Box(100, 100, 8), page="A4", scale=1.0, scale_policy="strict")
        .authored_dimensions()
        .table(oversized, name="same")
        .table(oversized, name="same")
    )
    with pytest.raises(ScaleIncompatibilityError) as caught:
        both_drop.build()
    assert [item["source_ids"] for item in caught.value.decision["blockers"]] == [
        ("sheet.table:0:same",),
        ("sheet.table:1:same",),
    ]

    drop_then_place = (
        Sheet(Box(100, 100, 8), page="A4", scale=1.0, scale_policy="permissive")
        .authored_dimensions()
        .table(oversized, name="same")
        .table([("OK",), ("visible",)], name="same")
    )
    with pytest.warns(ScaleCompletenessWarning, match="returning the incomplete drawing"):
        drawing = drop_then_place.build()
    assert "same" in drawing.annotations()
    assert [item["source_ids"] for item in drawing.scale_decision["blockers"]] == [
        ("sheet.table:0:same",)
    ]


@pytest.mark.timeout(120)
def test_sheet_fallback_reuses_lazy_physical_recognition(monkeypatch):
    import draftwright.drawing as drawing_module

    sheet = Sheet.from_part(_scale_sensitive_plate(), page="A4", scale=1.0)
    calls = []
    original = drawing_module.build_recognition_result

    def counted(part):
        result = original(part)
        calls.append(result)
        return result

    monkeypatch.setattr(drawing_module, "build_recognition_result", counted)
    with pytest.warns(ScaleCompletenessWarning, match="fallback scale 0.5"):
        drawing = sheet.build()

    assert drawing.scale_decision["attempted_scales"] == (1.0, 0.5)
    assert len(calls) == 1
    assert drawing.recognition() is calls[0]


@pytest.mark.timeout(120)
def test_priority_ranked_solver_drop_cannot_pass_strict_scale_policy():
    part = Cylinder(70, 6)
    for index, radius in enumerate((60, 52, 44, 36, 28, 20, 12)):
        part -= Pos(0, 0, 3 - index * 0.7) * Cylinder(radius, 6)

    with pytest.raises(ScaleIncompatibilityError) as caught:
        build_drawing(part, page="A4", scale=1.0, scale_policy="strict")

    (blocker,) = caught.value.decision["blockers"]
    assert blocker["code"] == "callout_dropped"
    assert "diameter(s)" in blocker["message"]
    # The shared strip solve ranks bore candidates by diameter. The largest bore lands while
    # the next-lower required bore drops; equalising priorities reverses this boundary and must
    # fail this canary, while strict policy still rejects the surviving completeness loss.
    assert "120.0" not in blocker["message"]
    assert "104.0" in blocker["message"]


@pytest.mark.timeout(120)
def test_fallback_reuses_geometry_classification_and_recognition(monkeypatch):
    import draftwright.analysis as analysis

    calls = {"classify": 0, "recognise": 0}
    original_classify = analysis._classify_geometry
    original_recognise = analysis.build_recognition_result

    def counted_classify(*args, **kwargs):
        calls["classify"] += 1
        return original_classify(*args, **kwargs)

    def counted_recognise(*args, **kwargs):
        calls["recognise"] += 1
        return original_recognise(*args, **kwargs)

    monkeypatch.setattr(analysis, "_classify_geometry", counted_classify)
    monkeypatch.setattr(analysis, "build_recognition_result", counted_recognise)
    with pytest.warns(ScaleCompletenessWarning, match="fallback scale 0.5"):
        drawing = build_drawing(_scale_sensitive_plate(), page="A4", scale=1.0, repair=False)

    assert drawing.scale == 0.5
    assert calls == {"classify": 1, "recognise": 1}


def test_cli_rejects_nondefault_policy_without_scale_for_direct_and_script_modes():
    from typer.testing import CliRunner

    from draftwright.cli import app

    for extra in ([], ["--script"]):
        result = CliRunner().invoke(
            app, ["part.step", "--scale-policy", "strict", *extra], color=True
        )
        assert result.exit_code == 2
        output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        message = " ".join(
            line.strip(" │") for line in output.splitlines() if line.startswith("│")
        )
        assert message == (
            "Invalid value for --scale-policy: --scale-policy requires an explicit --scale"
        )


def test_script_emitter_rejects_nondefault_policy_without_scale(tmp_path, monkeypatch):
    from draftwright.sheet_emit import generate_sheet_script

    def detection_must_not_run(*_args, **_kwargs):
        raise AssertionError("invalid script options must fail before model detection")

    monkeypatch.setattr("draftwright.sheet_emit.detect_part_model", detection_must_not_run)

    with pytest.raises(ValueError, match="only when an explicit scale"):
        generate_sheet_script(
            Box(10, 10, 10),
            out=str(tmp_path / "invalid_policy"),
            scale_policy="strict",
        )
    with pytest.raises(ValueError, match="scale_policy must be"):
        generate_sheet_script(
            Box(10, 10, 10),
            out=str(tmp_path / "invalid_vocabulary"),
            scale=1.0,
            scale_policy="banana",
        )


def test_sheet_forwards_the_authored_scale_policy(monkeypatch):
    captured = {}

    def fake_build(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(annotations=lambda: ())

    monkeypatch.setattr("draftwright.sheet.build_drawing", fake_build)
    Sheet(Box(10, 10, 10), scale="1:1", scale_policy="strict").auto_dimensions().build()

    assert captured["scale"] == 1.0
    assert captured["scale_policy"] == "strict"


def test_make_drawing_forwards_policy_to_the_live_build(monkeypatch):
    import draftwright.builder as builder

    captured = {}

    class FakeDrawing:
        def export(self, *, formats):
            assert formats == ("svg", "dxf")
            return {"svg": "drawing.svg", "dxf": "drawing.dxf"}

    def fake_build(*args, **kwargs):
        captured.update(kwargs)
        return FakeDrawing()

    monkeypatch.setattr(builder, "build_drawing", fake_build)
    assert builder.make_drawing(Box(10, 10, 10), scale=1.0, scale_policy="strict") == (
        "drawing.svg",
        "drawing.dxf",
    )
    assert captured["scale_policy"] == "strict"


def test_generated_sheet_script_round_trips_nondefault_policy(tmp_path):
    from draftwright.sheet_emit import generate_sheet_script

    path = generate_sheet_script(
        Box(10, 10, 10),
        out=str(tmp_path / "scale_policy"),
        scale=1.0,
        scale_policy="permissive",
    )
    text = Path(path).read_text(encoding="utf-8")
    assert "scale=1.0" in text
    assert "scale_policy='permissive'" in text
