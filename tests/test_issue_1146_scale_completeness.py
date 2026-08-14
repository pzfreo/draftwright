"""#1146 — explicit drawing scales may not silently reduce completeness."""

from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import (
    ScaleCompletenessWarning,
    ScaleIncompatibilityError,
    Sheet,
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
    }
    (blocker,) = drawing.scale_decision["blockers"]
    assert blocker["code"] == "location_ref_dropped"
    assert blocker["measurements"][0]["parameter"] == "location.location"
    assert blocker["hole_requirements"][0]["parameter"] == "location.location.x"


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
    }
    assert automatic.scale_decision == {
        "policy": "automatic",
        "requested_scale": None,
        "effective_scale": automatic.scale,
        "status": "automatic",
        "blockers": (),
        "attempted_scales": (),
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
        return SimpleNamespace(scale=scale, lint=lambda: [drop])

    monkeypatch.setattr(builder, "_SCALES", (1.0, 0.5, 0.2, 0.1))
    monkeypatch.setattr(builder, "_build_drawing_once", fake_build)

    with pytest.raises(ScaleIncompatibilityError) as caught:
        builder.build_drawing(Box(10, 10, 10), scale=1.0)

    assert caught.value.decision == {
        "policy": "fallback",
        "requested_scale": 1.0,
        "effective_scale": 1.0,
        "status": "no_complete_scale",
        "blockers": caught.value.decision["blockers"],
        "attempted_scales": (1.0, 0.5, 0.2),
    }
    assert caught.value.decision["blockers"][0]["code"] == "location_ref_dropped"
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
        return SimpleNamespace(scale=scale, lint=lambda: [drop])

    monkeypatch.setattr(builder, "_SCALES", (1.0, 0.5))
    monkeypatch.setattr(builder, "_build_drawing_once", fake_build)

    with pytest.raises(ValueError, match="invalid declared model"):
        builder.build_drawing(Box(10, 10, 10), scale=1.0)


def test_scale_warning_category_remains_a_dependency_free_user_warning():
    namespace = runpy.run_path(
        str(Path(__file__).parents[1] / "src" / "draftwright" / "_warnings.py")
    )
    assert issubclass(namespace["ScaleCompletenessWarning"], UserWarning)


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
