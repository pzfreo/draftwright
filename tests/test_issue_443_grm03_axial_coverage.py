"""#443 — GRM-03 must locate every turned shoulder on the automatic sheet."""

from pathlib import Path

import pytest
from build123d import import_step

from draftwright import Sheet, SoftDeprecationWarning, build_drawing
from draftwright._core import _anno_box

_FIXTURE = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw.step"


def test_grm03_replans_for_truthful_step_lengths_without_spending_the_iso():
    drawing = build_drawing(_FIXTURE, title="PART")

    # The automatic planner may enlarge the main views instead of retaining a
    # recovery detail, but a long detail caption must never force A3 (#1813).
    assert "iso" in drawing.views
    assert (drawing.page_w, drawing.page_h) == (297.0, 210.0)
    assert drawing.scale == 5.0
    assert drawing.scale_decision["status"] == "automatic_replanned"
    assert drawing.scale_decision["attempted_scales"] == (2.0, 5.0)
    assert [item["views"] for item in drawing.scale_decision["attempts"]] == [
        ("front", "side", "iso", "detail_a"),
        ("front", "side", "iso"),
    ]
    assert [item["status"] for item in drawing.scale_decision["attempts"]] == [
        "detail_reservation_conservative",
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


def test_grm03_detail_caption_fits_the_selected_a4_sheet():
    drawing = build_drawing(_FIXTURE, title="PART", page="A4", scale=2.0)

    assert "detail_a" in drawing.views
    caption = drawing.get_annotation("detail_caption_A")
    assert caption.label == "DETAIL A — PARTIAL PROFILE — SCALE 10:1"
    x0, y0, x1, y1 = _anno_box(caption)
    drawable_x0, drawable_y0, drawable_x1, drawable_y1 = drawing.drawable_bounds
    assert drawable_x0 <= x0 < x1 <= drawable_x1
    assert drawable_y0 <= y0 < y1 <= drawable_y1
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in {"annotation_out_of_bounds", "axial_length_missing"}
    ]


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
