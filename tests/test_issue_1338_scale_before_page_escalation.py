"""#1338 — the sheet is not the first lever: try the scale step on the selected page.

GRM-03 (28.7 x 10 x 10 mm) selected 2:1 on A4, found its axial coverage incomplete, dropped
the optional ISO and escalated to A3 — while 5:1 on **A4** is clean and keeps the ISO. The
current 2:1 drawing can instead recover short shoulders in a detail; these tests check
that the bounded upscale can still replace that detail with complete inline dimensions.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

import draftwright.builder as builder
from draftwright import build_drawing

FIXTURE = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
A4 = (297.0, 210.0)


def _requirement_failures(drawing):
    # What #1338 is about: nothing structurally broken, nothing dropped, every shoulder
    # located. Deliberately NOT "no warnings at all" — this part carries a pre-existing
    # `annotation_ink_overlap` in its step chain (#1322's rule reports it at every sheet
    # and scale, including the A3 fallback), and the recovery neither causes nor cures it.
    return [
        issue
        for issue in drawing.lint()
        if issue.severity == "error"
        or issue.code.endswith("_dropped")
        or issue.code == "axial_length_missing"
    ]


def test_first_selected_scale_uses_a_detail_for_short_shoulders():
    # The 2:1 A4 drawing now preserves the short shoulders in a defining detail.
    # The optional measured upscale below may replace that detail, not rescue a drop.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        drawing = build_drawing(
            FIXTURE, pmi="off", out=None, scale=2.0, page="A4", scale_policy="permissive"
        )

    assert (drawing.page_w, drawing.page_h) == A4
    assert drawing.scale == 2.0
    assert "detail_a" in drawing.views
    assert _requirement_failures(drawing) == []


def test_automatic_recovers_on_a4_at_a_larger_scale_instead_of_escalating_the_sheet():
    drawing = build_drawing(FIXTURE, pmi="off", out=None)

    assert (drawing.page_w, drawing.page_h, drawing.scale) == (*A4, 5.0)
    assert "iso" in drawing.views
    assert _requirement_failures(drawing) == []
    # The long default title used to overflow its cell at the recovered scale, and this
    # asserted it stayed a warning — reporting it as a structural error would have
    # rejected the recovery and returned missing axial measurements. Under the ISO 7200
    # layout `title` is a flexible cell taking its row's remainder rather than a fixed
    # 40% of the block, so it no longer overflows at all. The point that survives is the
    # one that mattered: the recovery is not rejected.
    assert [issue for issue in drawing.lint() if issue.code == "title_field_overflow"] == []

    assert drawing.scale_decision["status"] == "automatic_replanned"
    assert [
        (attempt["scale"], attempt["page"], attempt["status"], attempt["reason"])
        for attempt in drawing.scale_decision["attempts"]
    ] == [
        (2.0, A4, "detail_reservation_conservative", "measured_upscale"),
        (5.0, A4, "complete", "measured_upscale"),
    ]
    assert "detail_a" not in drawing.views
    assert not [
        attempt
        for attempt in drawing.scale_decision["attempts"]
        if attempt["reason"] == "page_escalation_after_optional_iso"
    ]


def test_without_the_selected_page_upscale_keeps_the_complete_detail(monkeypatch):
    # With no larger scale to try, retain the already-complete 2:1 detail on A4.
    # The optional upscale improves presentation; it no longer rescues coverage.
    recovered = build_drawing(FIXTURE, pmi="off", out=None)

    monkeypatch.setattr(builder, "_AUTOMATIC_UPSCALE_TRIAL_LIMIT", 0)
    drawing = build_drawing(FIXTURE, pmi="off", out=None)

    assert (drawing.page_w, drawing.page_h, drawing.scale) == (*A4, 2.0)
    assert {"iso", "detail_a"} <= drawing.views.keys()
    assert "detail_a" not in recovered.views
    assert _requirement_failures(drawing) == []


@pytest.mark.parametrize("mode", ["off", "report"])
def test_the_recovery_does_not_depend_on_the_pmi_mode(mode):
    drawing = build_drawing(FIXTURE, pmi=mode, out=None)

    assert (drawing.page_w, drawing.page_h, drawing.scale) == (*A4, 5.0)
