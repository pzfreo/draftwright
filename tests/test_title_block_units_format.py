"""The standard title block states the drawing units and effective sheet format."""

import pytest
from build123d import Box

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
