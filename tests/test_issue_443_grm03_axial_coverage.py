"""#443 — GRM-03 must locate every turned shoulder on the automatic sheet."""

from pathlib import Path

import pytest
from build123d import import_step

from draftwright import Sheet, SoftDeprecationWarning, build_drawing

_FIXTURE = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw.step"


def test_grm03_replans_for_truthful_step_lengths_without_spending_the_iso():
    drawing = build_drawing(_FIXTURE, title="PART")

    # #443's requirement is the truthful step lengths below, and it still holds. #1338
    # changed what the replan spends to get them: a larger scale on the selected sheet,
    # with the optional ISO retained, rather than the ISO removal this once needed.
    assert "iso" in drawing.views
    assert drawing.scale == 5.0
    assert drawing.scale_decision["status"] == "automatic_replanned"
    assert drawing.scale_decision["attempted_scales"] == (2.0, 5.0)
    assert [item["views"] for item in drawing.scale_decision["attempts"]] == [
        ("front", "side", "iso"),
        ("front", "side", "iso"),
    ]
    assert [item["status"] for item in drawing.scale_decision["attempts"]] == [
        "axial_coverage_incomplete",
        "complete",
    ]
    assert all(item["page"] == (297.0, 210.0) for item in drawing.scale_decision["attempts"])
    assert drawing.view_decision["status"] == "reduced"
    assert drawing.view_decision["chosen"] == ("front", "side")
    step_lengths = {
        drawing.get_annotation(name).label
        for name in drawing.annotations()
        if name.startswith("m_steplen")
    }
    assert {"0.5", "2", "3", "18"} <= step_lengths
    assert not [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]


def test_axial_replan_is_disabled_without_automatic_dimensions():
    drawing = build_drawing(_FIXTURE, title="PART", auto_dims=False)

    assert "iso" in drawing.views
    assert drawing.scale_decision["attempts"] == ()


def test_plain_sheet_auto_views_keeps_the_automatic_replan():
    with pytest.warns(SoftDeprecationWarning):
        drawing = Sheet.from_part(import_step(_FIXTURE)).auto_views().build()

    assert "iso" in drawing.views
    assert drawing.scale == 5.0
    assert not [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]


def test_authored_dimensions_disable_the_automatic_replan():
    with pytest.warns(SoftDeprecationWarning):
        drawing = Sheet.from_part(import_step(_FIXTURE)).authored_dimensions().auto_views().build()

    assert "iso" in drawing.views
    assert drawing.scale_decision["attempts"] == ()
