"""Cell overflow is visible even when the composed block stays inside the page."""

from pathlib import Path

import pypdfium2 as pdfium
import pytest
from build123d import Align, Box, Mode, Text

from draftwright import Sheet
from draftwright.fonts import PLEX_SANS_CONDENSED


def _box_sheet(**options):
    sheet = Sheet(Box(30, 20, 10), page="A2", scale=2, detail_view=False, **options)
    sheet.authored_dimensions()
    envelope = sheet.envelope()
    for parameter in envelope.dimension_ids():
        sheet.dimension(envelope, parameter)
    return sheet


#: A value that exceeds its cell without pushing the block past the page margin.
#: Not `material`: under the ISO 7200 layout that is a flexible cell (111 mm on
#: A2), so overflowing it would need a 75-character material, which nobody meets.
#: `drawn_by` is sized from ISO 7200's 20-character creator capacity, and it sits
#: at the left of its row, so the excess runs INTO the block rather than out past
#: its right edge — which is what keeps this a cell-overflow case and not an
#: out-of-bounds one.
_OVERFLOWING_AUTHOR = "ENGINEERING REVIEW TEAM"
_OVERFLOWING = f"{_OVERFLOWING_AUTHOR} / draftwright"


@pytest.fixture(scope="module")
def crowded_material():
    return _box_sheet(title="FRAME", drawn_by=_OVERFLOWING_AUTHOR).build()


def test_a_field_crossing_cells_inside_page_is_reported(crowded_material):
    drawing = crowded_material
    block = drawing.get_annotation("title_block")
    ink = Text(
        _OVERFLOWING,
        font_size=3,
        font_path=PLEX_SANS_CONDENSED,
        align=(Align.CENTER, Align.CENTER),
        mode=Mode.PRIVATE,
    ).bounding_box()
    assert ink.size.X > block.cell_bbox("designed_by")["width"]
    assert block.bounding_box().max.X < drawing.page_w - 10
    issues = drawing.lint()
    assert not any(issue.code == "annotation_out_of_bounds" for issue in issues)
    overflow = [issue for issue in issues if issue.code == "title_field_overflow"]
    assert len(overflow) == 1
    assert overflow[0].severity == "warning"
    assert "'designed_by'" in overflow[0].message
    assert "52.01" in overflow[0].message and "51.73" in overflow[0].message
    summary = drawing.lint_summary()
    assert summary["passed"]  # The existing flag checks errors, not legibility warnings.
    assert summary["quality"]["legibility"]["by_code"]["title_field_overflow"] == 1
    assert summary["quality"]["legibility"]["score"] < 1


@pytest.mark.parametrize(
    "options,field",
    [
        ({"title": "FRAME\nMACHINING\nREVIEW\nPRELIMINARY"}, "title"),
        ({"number": "DOCUMENT NUMBER WITH A LONG SUFFIX"}, "drawing_number"),
        ({"revision": "PRELIMINARY REVISION A"}, "revision"),
        # Its own cell under the ISO 7200 layout, sized from the standard's
        # 10-character date-of-issue capacity — so a spelled-out date overflows
        # where "SEPTEMBER 9 2026" now fits.
        ({"date": "WEDNESDAY 9 SEPTEMBER 2026"}, "date"),
        (
            {"tolerance": "TOLERANCE REQUIREMENTS ARE IN THE PROCESS SPECIFICATION"},
            "general_tolerance",
        ),
        ({"drawn_by": "ENGINEERING REVIEW TEAM " * 8}, "designed_by"),
        ({"company": "MANUFACTURING REVIEW ORGANISATION " * 8}, "legal_owner"),
    ],
)
def test_all_rendered_fields_and_multiline_height_are_checked(options, field):
    drawing = _box_sheet(**options).build()
    block = drawing.get_annotation("title_block")
    value, font_size, font_path = next(
        spec[1:] for spec in block.title_field_specs if spec[0] == field
    )
    ink = Text(
        value,
        font_size=font_size,
        font_path=font_path,
        align=(Align.CENTER, Align.CENTER),
        mode=Mode.PRIVATE,
    ).bounding_box()
    cell = block.cell_bbox(field)
    assert ink.size.X > cell["width"] or ink.size.Y > cell["height"]
    overflow = [issue for issue in drawing.lint() if issue.code == "title_field_overflow"]
    assert any(f"{field!r}" in issue.message for issue in overflow)
    assert drawing.draft.font_size == 3
    if field == "legal_owner":
        block_bounds = block.bounding_box()
        assert block_bounds.min.X < 10 or block_bounds.max.X > drawing.page_w - 10
        page_overflow = [
            issue for issue in drawing.lint() if issue.code == "annotation_out_of_bounds"
        ]
        assert page_overflow and all(issue.severity == "error" for issue in page_overflow)


def test_export_preserves_the_full_overflowing_value(crowded_material, tmp_path):
    before = crowded_material.measurement_snapshot()
    paths = crowded_material.export(str(tmp_path / "crowded"), formats=("pdf", "svg"))
    with pdfium.PdfDocument(paths["pdf"]) as pdf:
        text = pdf[0].get_textpage().get_text_range()
    assert _OVERFLOWING in text
    assert crowded_material.measurement_snapshot() == before
    assert any(issue.code == "title_field_overflow" for issue in crowded_material.lint())


@pytest.fixture(scope="module")
def note_control_drawings():
    document = (Path(__file__).parents[1] / "docs/reference/sheet.md").read_text()
    marked = document.split("<!-- issue-1536-notes-example -->", 1)[1]
    recipe = marked.split("```python\n", 1)[1].split("```", 1)[0]
    drawings = []
    for page, scale in (("A4", 1), ("A4", 2), ("A2", 2)):
        namespace = {}
        exec(recipe.replace('page="A4", scale=2', f'page="{page}", scale={scale}'), namespace)
        drawings.append(namespace["drawing"])
    return drawings


def test_documented_page_scale_controls_preserve_printed_notes_and_measurements(
    note_control_drawings,
):
    sizes = []
    for drawing in note_control_drawings:
        assert not drawing.lint()
        assert drawing.draft.font_size == 3
        notes = drawing.get_annotation("notes0")
        assert notes is not None
        assert any("TITANIUM - GRADE TBD" in row[0] for row in notes.table_rows)
        sizes.append(notes.table_size)
        assert {claim.meaning[0][0] for claim in drawing.measurement_snapshot().claims} == {
            10,
            20,
            30,
        }
    assert sizes[0] == pytest.approx(sizes[1])
    assert sizes[1] == pytest.approx(sizes[2])


def test_documented_notes_export_without_losing_full_material(note_control_drawings, tmp_path):
    drawing = note_control_drawings[1]
    path = drawing.export(str(tmp_path / "notes"), formats=("pdf",))["pdf"]
    with pdfium.PdfDocument(path) as pdf:
        text = pdf[0].get_textpage().get_text_range()
    assert "SEE NOTES" in text
    assert "TITANIUM - GRADE TBD" in text
    assert "Hinge fit and pin retention: engineering decision required" in text
