"""Hard fit wins over discretionary spacing for measured page blocks."""

from types import SimpleNamespace

import pytest
from build123d import Box

from draftwright import build_drawing
from draftwright.analysis import _analyse
from draftwright.auxiliary_layout import (
    detail_space,
    document_note_rows,
    fit_auxiliary_box,
    section_slot_x,
)
from draftwright.model.ir import DocumentNote, Frame, PartModel


def test_document_notes_have_one_measured_wrapped_shape():
    rows = document_note_rows(
        [SimpleNamespace(text="Inspect the shoulder before assembly and record its diameter")]
    )
    assert rows[0] == ("GENERAL NOTES",)
    assert len(rows) == 3
    assert rows[1][0].startswith("1  Inspect")
    assert rows[2][0].startswith("   ")


def test_flexible_block_prefers_gutter_but_retains_hard_fit():
    region = (0.0, 0.0, 12.0, 12.0)
    obstacle = [(8.0, 0.0, 9.0, 12.0)]
    # Six millimetres cannot fit; the original one-millimetre clearance can.
    assert fit_auxiliary_box((5.0, 5.0), region, obstacle, "tr", clearance=1.0) == (
        2.0,
        7.0,
    )
    spacious = fit_auxiliary_box((5.0, 5.0), (0.0, 0.0, 22.0, 12.0), obstacle, "tr", clearance=1.0)
    assert spacious is not None
    assert spacious[0] >= 15.0  # six mm right of the obstacle


def test_detail_gutter_cannot_reduce_requested_scale():
    region = (0.0, 0.0, 50.0, 30.0)
    obstacle = [(20.0, 0.0, 30.0, 30.0)]
    hard = detail_space(region, obstacle, minimum_size=(10.0, 10.0), desired_size=(18.0, 15.0))
    assert hard[2] - hard[0] >= 18.0
    soft = detail_space(region, obstacle, minimum_size=(10.0, 10.0), desired_size=(10.0, 15.0))
    assert soft[2] <= 14.0 or soft[0] >= 36.0
    # The empty-rectangle primitive returns the drawable when padded obstacles
    # cover it entirely. That sentinel must not masquerade as a spacious fit.
    filled = detail_space(
        (0.0, 0.0, 20.0, 20.0),
        [(6.0, 0.0, 14.0, 20.0)],
        minimum_size=(6.0, 7.0),
        desired_size=(6.0, 7.0),
    )
    assert filled != (0.0, 0.0, 20.0, 20.0)


def test_section_prefers_space_without_crossing_title_block_or_losing_fit():
    assert section_slot_x([(20.0, 80.0)], 10.0, 12.0, shares_title_row=False, tb_left=50) == 38.0
    assert section_slot_x([(20.0, 45.0)], 10.0, 12.0, shares_title_row=False, tb_left=50) == 32.0
    assert section_slot_x([(20.0, 80.0)], 10.0, 12.0, shares_title_row=True, tb_left=50) == 32.0


def test_visible_document_notes_reserve_their_measured_page_block():
    part = Box(20, 10, 5)
    note = DocumentNote(Frame((0.0, 0.0, 0.0), "z"), "Datum A is primary", "datum_scheme")

    def analyse(on_drawing):
        model = PartModel(
            part.bounding_box(),
            "z",
            [DocumentNote(note.frame, note.text, note.note_kind, on_drawing=on_drawing)],
        )
        return _analyse(part, "", "", None, "", "", model=model, pmi="annotate")

    visible = analyse(True)
    assert len(visible.layout_required_tables) == 1
    drawing = build_drawing(
        part,
        model=PartModel(part.bounding_box(), "z", [note]),
        pmi="annotate",
    )
    assert visible.layout_required_tables[0][0] == pytest.approx(
        drawing.get_annotation("general_notes").table_size
    )
    assert not analyse(False).layout_required_tables
