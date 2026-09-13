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


@pytest.fixture(scope="module")
def dated_a3():
    return _sheet(page="A3", title="BRACKET", number="DRW-0042", date=DATE).build()


@pytest.mark.parametrize("value", ["2026-09-11", "11/09/2026", "11 SEP 2026"])
def test_common_date_formats_fit_on_the_pages_draftwright_uses(dated, dated_a3, value):
    # A4 carries a 120 mm title block, A3 and larger a 150 mm one. The cell has
    # to hold a date at both, or the fix trades a silent drop for a visible spill.
    # This is a question about text metrics against a cell, so it measures the
    # two already-built blocks rather than minting a build per format.
    for drawing in (dated, dated_a3):
        cell = drawing.get_annotation("title_block").cell_bbox("date")
        ink = Text(
            value,
            font_size=3,
            font_path=PLEX_SANS_CONDENSED,
            align=(Align.CENTER, Align.CENTER),
            mode=Mode.PRIVATE,
        ).bounding_box()
        assert ink.size.X < cell["width"], f"{value!r} needs {ink.size.X:.2f} mm of {cell}"


def test_whitespace_revision_records_what_the_block_actually_drew():
    # The TitleBlock strips both fields before choosing cells, so a whitespace
    # revision is no revision to it and the date takes the shared top-right cell.
    # Unstripped here, `revision or date` recorded "  " and the drawn date reached
    # neither the PDF text layer nor the overflow lint — #1585 wearing spaces.
    drawing = _sheet(title="BRACKET", number="DRW-0042", date=DATE, revision="  ").build()
    _block, fields = _fields(drawing)
    assert fields["date"] == DATE
    assert "revision" not in fields


def test_a_padded_date_is_recorded_as_it_is_drawn():
    drawing = _sheet(title="BRACKET", number="DRW-0042", date=f"  {DATE}  ").build()
    _block, fields = _fields(drawing)
    assert fields["date"] == DATE


def test_the_drawn_by_cell_is_no_longer_squeezed_by_a_date():
    """The cost this test used to pin is gone.

    Under the two-row block a date took the last two columns from DRAWN BY,
    60% -> 35% of the width, and a long `drawn_by` overflowed and drew across
    the divider. The ISO layout gives the date its own cell in its own row and
    sizes DRAWN BY from ISO 7200's creator capacity, so adding a date costs the
    drawn-by cell nothing.
    """
    long_author = "ACME Engineering Ltd"
    without = _sheet(title="BRACKET", number="DRW-0042", drawn_by=long_author).build()
    with_date = _sheet(title="BRACKET", number="DRW-0042", drawn_by=long_author, date=DATE).build()
    wide = without.get_annotation("title_block").cell_bbox("designed_by")["width"]
    narrow = with_date.get_annotation("title_block").cell_bbox("designed_by")["width"]
    assert narrow == pytest.approx(wide), "a date must not shrink the drawn-by cell"

    # The cell is sized from ISO 7200's creator capacity (20 characters).
    # "ACME Engineering Ltd / draftwright" is 34, so it still overflows — but it
    # overflows identically with and without the date, which is the point. The
    # capacity is nominal and the lint reports the excess either way.
    def overflows(drawing):
        return [
            i.code
            for i in drawing.lint()
            if i.code == "title_field_overflow" and "'designed_by'" in i.message
        ]

    assert overflows(with_date) == overflows(without)


def test_the_date_always_has_its_own_cell_now():
    """The shared top-right cell is gone.

    It existed because ISO 7200's date of issue and revision index competed for
    one cell; the ISO layout gives each its own, so there is no configuration in
    which a supplied date lands in another field's box.
    """
    for kwargs in (
        {"date": DATE},
        {"date": DATE, "revision": ""},
        {"date": DATE, "revision": "B"},
    ):
        drawing = _sheet(title="BRACKET", number="DRW-0042", **kwargs).build()
        block, fields = _fields(drawing)
        assert fields["date"] == DATE
        assert block.cell_bbox("date") != block.cell_bbox("revision")
        assert sum(value == DATE for value, *_rest in block.pdf_text_specs) == 1


def test_a_whitespace_company_does_not_crash_the_build():
    # legal_owner is the third field the block strips, and it was the one left
    # unstripped here. "   " is truthy, so it survived the empty-value filter
    # and then asked for a legal_owner cell the block had not drawn.
    drawing = _sheet(title="BRACKET", number="DRW-0042", company="   ").build()
    _block, fields = _fields(drawing)
    assert "legal_owner" not in fields


def test_a_padded_company_is_recorded_as_it_is_drawn():
    drawing = _sheet(title="BRACKET", number="DRW-0042", company=" ACME ").build()
    _block, fields = _fields(drawing)
    assert fields["legal_owner"] == "ACME"


def test_no_date_field_when_no_date_was_supplied():
    _block, fields = _fields(_sheet(title="BRACKET", number="DRW-0042").build())
    assert "date" not in fields
