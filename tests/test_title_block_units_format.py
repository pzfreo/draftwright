"""The standard title block states the drawing units and effective sheet format."""

from collections import Counter
from dataclasses import replace

import pytest
from _parts import dense_plate
from build123d import Box, Location

from draftwright import Sheet, build_drawing


def _fields(drawing) -> dict[str, str]:
    block = drawing.get_annotation("title_block")
    return {field: value for field, value, _size, _font in block.title_field_specs}


@pytest.mark.parametrize("page", ("A4", "A2", "A0"))
def test_named_iso_page_states_units_and_format(page):
    sheet = Sheet(Box(30, 20, 10), page=page, scale=1, detail_view=False)
    sheet.authored_dimensions()
    drawing = sheet.build()

    fields = _fields(drawing)
    assert fields["units"] == "mm"
    assert fields["format"] == page
    assert not [issue for issue in drawing.lint() if issue.code == "title_field_overflow"]


def test_dimension_specified_iso_page_uses_its_standard_format_name():
    drawing = build_drawing(Box(30, 20, 10), page=(420, 594), scale=1, auto_dims=False)

    assert _fields(drawing)["format"] == "A2"


def test_nonstandard_page_states_its_actual_dimensions():
    drawing = build_drawing(Box(30, 20, 10), page=(500, 300), scale=1, auto_dims=False)

    assert _fields(drawing)["format"] == "500x300"


def test_new_fields_have_real_cells_and_reach_the_pdf_text_layer():
    drawing = build_drawing(Box(30, 20, 10), page="A2", scale=1, auto_dims=False)
    block = drawing.get_annotation("title_block")

    assert block.cell_bbox("units")["width"] > 0
    assert block.cell_bbox("format")["width"] > 0
    values = {value for value, *_rest in block.pdf_text_specs}
    assert {"mm", "A2"} <= values
    assert not [issue for issue in drawing.lint() if issue.code == "title_field_overflow"]


def test_related_vertical_dividers_share_one_grid():
    drawing = build_drawing(Box(30, 20, 10), page="A4", scale=1, auto_dims=False)
    block = drawing.get_annotation("title_block")

    principal = {
        block.cell_bbox(field)["min_x"]
        for field in ("document_type", "drawing_number", "units", "date")
    }
    secondary = {block.cell_bbox(field)["min_x"] for field in ("general_tolerance", "approved_by")}
    tertiary = {block.cell_bbox(field)["min_x"] for field in ("format", "revision")}

    assert len(principal) == len(secondary) == len(tertiary) == 1


def test_title_block_is_constructed_once_per_build_issue_1942(monkeypatch):
    from draftwright.annotations import _sheet_furniture

    original = _sheet_furniture._make_title_block
    calls = []

    def counted(drawing, analysis):
        calls.append((analysis.PAGE_W, analysis.PAGE_H))
        return original(drawing, analysis)

    monkeypatch.setattr(_sheet_furniture, "_make_title_block", counted)
    drawing = build_drawing(Box(30, 20, 10), page="A4", scale=1)
    block = drawing.get_annotation("title_block")
    bounds = block.bounding_box()

    # The measured reservation and the placed block are both exercised by this build.
    assert drawing.pending_title_block_box() == pytest.approx(
        (bounds.min.X, bounds.min.Y, bounds.max.X, bounds.max.Y)
    )
    assert block.pdf_text_specs
    assert calls == [(297.0, 210.0)]


def test_title_block_is_shared_across_page_retries_issue_1942(monkeypatch):
    from draftwright.annotations import _sheet_furniture
    from draftwright.drawing import Drawing

    # This part still exercises multiple attempts on the same page with the
    # current recogniser, so the cache-sharing assertion keeps its precondition.
    part = dense_plate()

    original_for = Drawing.title_block_for
    original_make = _sheet_furniture._make_title_block
    request_drawings = {}
    built = []
    calls = []

    def cached(drawing, key, factory):
        # Retain the instances: an id-only record could be recycled after a
        # discarded retry, making one Drawing look like two (or vice versa).
        request_drawings.setdefault(key, []).append(drawing)

        def counted_factory():
            built.append(key)
            return factory()

        return original_for(drawing, key, counted_factory)

    def counted_make(drawing, analysis):
        calls.append((analysis.PAGE_W, analysis.PAGE_H))
        return original_make(drawing, analysis)

    monkeypatch.setattr(Drawing, "title_block_for", cached)
    monkeypatch.setattr(_sheet_furniture, "_make_title_block", counted_make)
    drawing = build_drawing(part)

    # Repeated calls on one Drawing are insufficient: this must cross a build retry.
    assert any(
        len({id(drawing) for drawing in drawings}) > 1 for drawings in request_drawings.values()
    )
    assert Counter(built) == Counter(request_drawings.keys())
    assert len(calls) == len(built)
    assert drawing.get_annotation("title_block") is not None


def test_cached_title_block_annotations_have_independent_ownership_issue_1942(monkeypatch):
    from draftwright._core import SheetMargins
    from draftwright.analysis import _analyse
    from draftwright.annotations import _sheet_furniture
    from draftwright.builder import _assemble

    part = Box(30, 20, 10)
    analysis = _analyse(
        part, title="OWNED", number="DWG-1", tolerance=None, drawn_by="A", out="owned", pmi="off"
    )
    moved_analysis = replace(analysis, title_block_margins=SheetMargins(right=20, bottom=20))
    original_make = _sheet_furniture._make_title_block
    calls = []

    def counted(drawing, candidate):
        calls.append((candidate.PAGE_W, candidate.PAGE_H))
        return original_make(drawing, candidate)

    monkeypatch.setattr(_sheet_furniture, "_make_title_block", counted)
    cache = {}
    first = _assemble(analysis, "owned", None, False, auto_dims=False, title_block_cache=cache)
    second = _assemble(
        moved_analysis, "owned", None, False, auto_dims=False, title_block_cache=cache
    )
    first_block = first.get_annotation("title_block")
    second_block = second.get_annotation("title_block")
    first_bbox = first_block.bounding_box()
    first_bounds = (first_bbox.min.X, first_bbox.min.Y, first_bbox.max.X, first_bbox.max.Y)
    first_rect = first_block.draftwright_link_rect
    first_specs = first_block.pdf_text_specs

    assert len(calls) == 1
    assert first_block is not second_block
    offset_x = second_block.draftwright_link_rect[0] - first_rect[0]
    offset_y = second_block.draftwright_link_rect[1] - first_rect[1]
    assert (offset_x, offset_y) == pytest.approx((-9, 9))
    assert [
        (text, x - offset_x, y - offset_y, size, font)
        for text, x, y, size, font in second_block.pdf_text_specs
    ] == list(first_specs)
    second_block.draftwright_link_rect = (0, 0, 1, 1)
    second_block.locate(Location((0, 0, 0)))
    assert second_block.bounding_box().min.X != pytest.approx(first_bounds[0])
    second.remove("title_block")

    assert first.get_annotation("title_block") is first_block
    first_bbox_after = first_block.bounding_box()
    assert (
        first_bbox_after.min.X,
        first_bbox_after.min.Y,
        first_bbox_after.max.X,
        first_bbox_after.max.Y,
    ) == pytest.approx(first_bounds)
    assert first_block.draftwright_link_rect == first_rect
    assert first_block.pdf_text_specs == first_specs
