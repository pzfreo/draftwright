"""#1338 — the sheet is not the first lever: try the scale step on the selected page.

GRM-03 (28.7 x 10 x 10 mm) selected 2:1 on A4, found its axial coverage incomplete, dropped
the optional ISO and escalated to A3 — while 5:1 on **A4** is clean and keeps the ISO. These
tests pin the recovery order: a larger scale on the page already selected is tried, and must
pass the same gates, before the optional view and then the sheet are spent.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

import draftwright.builder as builder
from draftwright import build_drawing

FIXTURE = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
A4 = (297.0, 210.0)
A3 = (420.0, 297.0)


def _issue_codes(drawing):
    return sorted(issue.code for issue in drawing.lint() if issue.severity in {"error", "warning"})


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


def test_precondition_the_first_selected_scale_really_is_axially_incomplete():
    # The defect only exists because 2:1 — the scale automatic selection reaches first —
    # cannot dimension every shoulder on A4.  Without this the recovery never runs and the
    # test below would pass against unfixed code.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        drawing = build_drawing(
            FIXTURE, pmi="off", out=None, scale=2.0, page="A4", scale_policy="permissive"
        )

    assert (drawing.page_w, drawing.page_h) == A4
    assert "axial_length_missing" in _issue_codes(drawing)


def test_automatic_recovers_on_a4_at_a_larger_scale_instead_of_escalating_the_sheet():
    drawing = build_drawing(FIXTURE, pmi="off", out=None)

    assert (drawing.page_w, drawing.page_h, drawing.scale) == (*A4, 5.0)
    assert "iso" in drawing.views
    assert _requirement_failures(drawing) == []

    assert drawing.scale_decision["status"] == "automatic_replanned"
    assert [
        (attempt["scale"], attempt["page"], attempt["status"], attempt["reason"])
        for attempt in drawing.scale_decision["attempts"]
    ] == [
        (2.0, A4, "axial_coverage_incomplete", "remove_optional_iso"),
        (5.0, A4, "complete", "scale_escalation_on_selected_page"),
    ]
    assert not [
        attempt
        for attempt in drawing.scale_decision["attempts"]
        if attempt["reason"] == "page_escalation_after_optional_iso"
    ]


def test_without_the_selected_page_upscale_the_ladder_still_spends_the_sheet(monkeypatch):
    # Relaxing the named mechanism must change the outcome: with no larger scale to try on
    # the selected page, the same part falls back to the pre-#1338 result — a bigger sheet
    # with the ISO spent — which is what the recovery above is buying.
    recovered = build_drawing(FIXTURE, pmi="off", out=None)

    monkeypatch.setattr(builder, "_AUTOMATIC_UPSCALE_TRIAL_LIMIT", 0)
    drawing = build_drawing(FIXTURE, pmi="off", out=None)

    assert (drawing.page_w, drawing.page_h, drawing.scale) == (*A3, 5.0)
    assert "iso" not in drawing.views
    assert [attempt["reason"] for attempt in drawing.scale_decision["attempts"]].count(
        "page_escalation_after_optional_iso"
    ) == 1
    # And the recovery is not bought with new lint defects: the sheet it avoids carries
    # exactly the same codes, so what the smaller sheet gains is the ISO and the sheet
    # size, not a worse drawing.
    assert _issue_codes(recovered) == _issue_codes(drawing)
    assert _requirement_failures(drawing) == []


@pytest.mark.parametrize("mode", ["off", "report"])
def test_the_recovery_does_not_depend_on_the_pmi_mode(mode):
    drawing = build_drawing(FIXTURE, pmi=mode, out=None)

    assert (drawing.page_w, drawing.page_h, drawing.scale) == (*A4, 5.0)
