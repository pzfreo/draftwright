"""A supplied date reaches the sheet, and reaches it as its own field (#1585).

The date used to be dropped in silence: `TitleBlock` could only show one of
`revision`/`date` in its top-right cell and revision won, while draftwright
defaults revision to "A" and passes date straight through. Helpers 0.15.3 gives
the date a cell of its own whenever both are set; `_make_title_block` has to
name that cell in its field specs, or the date renders as visible geometry but
stays out of the PDF text layer and out of the cell-overflow check.
"""

import pytest
from build123d import Align, Box, Mode, Text

from draftwright import Sheet
from draftwright.fonts import PLEX_SANS_CONDENSED

DATE = "2026-09-11"


def _sheet(page="A4", **options):
    sheet = Sheet(Box(30, 20, 10), page=page, scale=2, detail_view=False, **options)
    sheet.authored_dimensions()
    envelope = sheet.envelope()
    for parameter in envelope.dimension_ids():
        sheet.dimension(envelope, parameter)
    return sheet


@pytest.fixture(scope="module")
def dated():
    """The reporter's configuration: a date, and the default revision "A"."""
    return _sheet(title="BRACKET", number="DRW-0042", date=DATE).build()


def _fields(drawing):
    block = drawing.get_annotation("title_block")
    return block, {field: value for field, value, _size, _font in block.title_field_specs}


def test_precondition_revision_still_owns_the_top_right_cell(dated):
    # The fix must not work by evicting the revision: it is ISO 7200 field 4 and
    # keeps the cell. If this ever showed the date, the test below would pass for
    # the wrong reason — the date would be displacing revision, not joining it.
    _block, fields = _fields(dated)
    assert fields["revision"] == "A"


def test_the_date_is_a_field_of_its_own(dated):
    block, fields = _fields(dated)
    assert fields["date"] == DATE
    # A real cell, not the revision cell wearing an alias.
    assert block.cell_bbox("date") != block.cell_bbox("revision")


def test_the_date_reaches_the_pdf_text_layer(dated):
    # title_field_specs feeds the overflow lint; pdf_text_specs feeds the
    # selectable text layer. Both are built from the same tuple, and a date
    # present in one and missing from the other is the bug this guards.
    block = dated.get_annotation("title_block")
    assert any(value == DATE for value, *_rest in block.pdf_text_specs)


def test_the_date_is_drawn_inside_its_cell(dated):
    block, _fields_ = _fields(dated)
    cell = block.cell_bbox("date")
    ink = Text(
        DATE,
        font_size=3,
        font_path=PLEX_SANS_CONDENSED,
        align=(Align.CENTER, Align.CENTER),
        mode=Mode.PRIVATE,
    ).bounding_box()
    assert ink.size.X < cell["width"]
    assert not [issue for issue in dated.lint() if issue.code == "title_field_overflow"]


@pytest.mark.parametrize("page", ["A4", "A3"])
@pytest.mark.parametrize("value", ["2026-09-11", "11/09/2026", "11 SEP 2026"])
def test_common_date_formats_fit_on_the_pages_draftwright_uses(page, value):
    # A4 carries a 120 mm title block, A3 and larger a 150 mm one. The cell has
    # to hold a date at both, or the fix trades a silent drop for a visible spill.
    drawing = _sheet(page=page, title="BRACKET", number="DRW-0042", date=value).build()
    overflow = [
        issue
        for issue in drawing.lint()
        if issue.code == "title_field_overflow" and "'date'" in issue.message
    ]
    assert overflow == []


def test_no_date_field_when_the_block_has_no_date_cell():
    # With no revision the date takes the shared top-right cell, where
    # cell_bbox("date") aliases cell_bbox("revision"). Emitting a date entry
    # there would stamp two texts at one centre and lint one cell twice.
    drawing = _sheet(title="BRACKET", number="DRW-0042", date=DATE, revision="").build()
    block, fields = _fields(drawing)
    assert "date" not in fields
    assert fields["revision"] == DATE
    assert block.cell_bbox("date") == block.cell_bbox("revision")
    assert sum(value == DATE for value, *_rest in block.pdf_text_specs) == 1


def test_no_date_field_when_no_date_was_supplied():
    _block, fields = _fields(_sheet(title="BRACKET", number="DRW-0042").build())
    assert "date" not in fields
