"""#1247: recognised angled steps fail visibly without invented drafting semantics."""

from __future__ import annotations

from pathlib import Path

from b123d_recognisers import (
    AngledStep,
    build_recognition_result,
    recognise_angled_steps,
    recognise_chamfers,
)
from build123d import import_step

from draftwright import build_drawing
from draftwright.linting.angled_step_coverage import lint_angled_step_coverage

_FIXTURE = Path(__file__).parent / "fixtures" / "issue_1247_angled_blind_step.step"


def _angled_step_part():
    return import_step(str(_FIXTURE))


def test_corpus_fixture_pins_angled_step_chamfer_ownership() -> None:
    """Direct candidates overlap; the provider aggregate resolves the physical owner."""

    part = _angled_step_part()
    direct_steps = recognise_angled_steps(part)
    direct_chamfers = recognise_chamfers(part)
    recognition = build_recognition_result(part)

    assert len(part.faces()) == 10
    assert abs(part.volume - 60975.0) < 0.01
    assert direct_steps == [
        AngledStep(
            axis="y",
            leg1=18.028,
            leg2=12.018,
            angle=33.69,
            length=12.0,
            at=(9.014, 25.0, 18.991),
        )
    ]
    assert len(direct_chamfers) == 2, "direct calls precede cross-family reconciliation"
    step = direct_steps[0]
    overlapping_chamfers = [
        chamfer
        for chamfer in direct_chamfers
        if (
            chamfer.axis,
            chamfer.leg1,
            chamfer.leg2,
            chamfer.angle,
            chamfer.at,
        )
        == (step.axis, step.leg1, step.leg2, step.angle, step.at)
    ]
    assert len(overlapping_chamfers) == 1
    overlapping_chamfer = overlapping_chamfers[0]
    assert recognition.angled_steps == tuple(direct_steps)
    assert overlapping_chamfer not in recognition.chamfers
    assert recognition.chamfers == tuple(
        chamfer for chamfer in direct_chamfers if chamfer != overlapping_chamfer
    )


def test_angled_step_is_specific_actionable_and_non_info() -> None:
    part = _angled_step_part()
    recognition = build_recognition_result(part)
    drawing = build_drawing(part)

    assert len(recognition.angled_steps) == 1
    issues = drawing.lint()
    assert [issue.code for issue in issues] == ["angled_step_requirement_unsupported"]
    assert issues[0].severity == "warning"
    assert (
        "recognised angled blind step at 33.69 degrees with 12 mm run (1 of 1)"
        in issues[0].message
    )
    assert "outside automatic drawing approval" in issues[0].message
    summary = drawing.lint_summary()
    assert summary["warnings"] == summary["geometry_issues"] == 1
    assert summary["quality"]["completeness"]["unrecognised_geometry_reports"] == 0


def test_angled_step_is_one_explicit_unsupported_completeness_outcome() -> None:
    drawing = build_drawing(_angled_step_part())
    completeness = drawing.lint_summary()["quality"]["completeness"]

    assert len(lint_angled_step_coverage(drawing.recognition())) == 1
    assert completeness["available"] is True
    assert completeness["audited_score"] == 0.0
    assert completeness["requirements"] == 1
    assert completeness["unsupported"] == 1
    assert completeness["by_family"]["angled_steps"] == 1
    assert "angled_steps" not in completeness["unscored_recognized_families"]
    assert completeness["unscored_recognized_families"] == ["chamfers"]
