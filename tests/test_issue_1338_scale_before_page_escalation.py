"""#1338 — the sheet is not the first lever: try the scale step on the selected page.

GRM-03 (28.7 x 10 x 10 mm) selected 2:1 on A4, found its axial coverage incomplete, dropped
the optional ISO and escalated to A3 — while 5:1 on **A4** is clean and keeps the ISO. The
current 2:1 drawing can instead recover short shoulders in a detail; these tests check
that the bounded upscale can still replace that detail with complete inline dimensions.
"""

from __future__ import annotations

import hashlib
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

import draftwright.builder as builder
from draftwright import build_drawing, observe_build
from draftwright.annotations._common import _geom_box
from draftwright.annotations._leader_fixed_ink import _annotation_fixed_ink
from draftwright.annotations._placement_occupancy import label_clears_foreign_annotations
from draftwright.pmi import extract_pmi_report

FIXTURE = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
A4 = (297.0, 210.0)
FIXTURE_SHA256 = "4b6462b9cc9f0d419250933bd77fb305f9cfebb7ec2b3f377008732876010a21"


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


def _assert_grm03_pmi_hole_source():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    report = extract_pmi_report(FIXTURE)
    assert any(record.part21_id == "#2004" for record in report.records)


def test_ap242_pmi_side_hole_survives_selected_page_upscale_issue_2177():
    _assert_grm03_pmi_hole_source()

    drawing = build_drawing(FIXTURE, pmi="annotate", out=None)

    assert (drawing.page_w, drawing.page_h, drawing.scale) == (*A4, 5.0)
    assert set(drawing.views) == {"front", "side", "iso"}
    assert drawing.scale_decision["status"] == "automatic_replanned"
    assert drawing.scale_decision["attempts"][-1]["status"] == "complete"
    hole = drawing.get_annotation("hc_side0")
    assert "⌀1.6" in hole.label and "MFG 2" in hole.label
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]

    table = drawing.get_annotation("manufacturing_requirements")
    (reserved,) = _annotation_fixed_ink(drawing, "manufacturing_requirements", table)
    table_box = _geom_box(table)
    assert reserved.box == table_box
    assert reserved.kind == "Table"
    assert hole.label_bbox is not None and table_box is not None
    assert label_clears_foreign_annotations(
        hole.label_bbox, ((table_box, False),), drawing.draft.pad_around_text
    )


def test_explicit_a4_2_to_1_pmi_keeps_required_hole_after_measured_repack_issue_2177():
    _assert_grm03_pmi_hole_source()

    drawing = build_drawing(FIXTURE, pmi="annotate", page="A4", scale=2.0)

    assert (drawing.page_w, drawing.page_h, drawing.scale) == (*A4, 2.0)
    assert drawing.scale_decision["status"] == "honored"
    assert "⌀1.6" in drawing.get_annotation("hc_side0").label
    assert {
        (issue.code, issue.annotation_name)
        for issue in drawing.lint()
        if issue.severity in {"warning", "error"}
    } == {
        ("datum_leader_remote", "m_gdt1"),
        ("interior_label_on_narrow_material", "m_chamfer_x1"),
    }


def test_table_repack_trigger_ignores_unrelated_and_clean_outcomes_issue_2177():
    table = SimpleNamespace(table_rows=(("REF", "REQUIREMENT"),))
    drawing = SimpleNamespace(
        registry=SimpleNamespace(issues=[]),
        iter_annotations=lambda: iter((("schedule", table),)),
    )
    assert not builder._source_placement_drop_with_table(drawing)

    issue = SimpleNamespace(
        code="pmi_dropped", source_ids=("source:1",), outcome_stage="validation"
    )
    drawing.registry.issues = [issue]
    assert not builder._source_placement_drop_with_table(drawing)

    issue.outcome_stage = "placement"
    issue.source_ids = ()
    assert not builder._source_placement_drop_with_table(drawing)

    issue.source_ids = ("source:1",)
    drawing.iter_annotations = lambda: iter(())
    assert not builder._source_placement_drop_with_table(drawing)
    drawing.iter_annotations = lambda: iter((("schedule", table),))
    assert builder._source_placement_drop_with_table(drawing)


def test_unselected_view_geometry_cannot_force_repack_issue_2177(monkeypatch):
    # Analysis retains candidate plan geometry even when the settled drawing
    # contains only front and side. A front label there is not a clash.
    monkeypatch.setattr(
        builder,
        "_view_geom",
        lambda _analysis: {"front": (0, 0, 5, 5), "plan": (20, 20, 5, 5)},
    )
    monkeypatch.setattr(
        builder,
        "_attribute_annotations",
        lambda _drawing: iter((("front_label", "front", (19, 19, 21, 21), True),)),
    )
    drawing = SimpleNamespace(
        views={"front": object()}, draft=SimpleNamespace(pad_around_text=0.5)
    )
    assert builder._annotation_view_overlaps(drawing, object()) == 0
    drawing.views["plan"] = object()
    assert builder._annotation_view_overlaps(drawing, object()) == 1


def test_side_hole_label_clears_neighbour_view_text_issue_2177():
    label = (10.0, 10.0, 20.0, 12.0)
    assert not label_clears_foreign_annotations(label, (((20.8, 10.0, 25.0, 12.0), True),), 0.5)
    assert label_clears_foreign_annotations(label, (((30.0, 10.0, 35.0, 12.0), True),), 0.5)


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
    events = []
    with observe_build(events.append):
        drawing = build_drawing(FIXTURE, pmi="off", out=None)

    assert 1 + sum(event.phase == "retry" for event in events) <= 3

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
