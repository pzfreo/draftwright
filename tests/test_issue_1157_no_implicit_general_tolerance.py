"""An unauthored general tolerance is stated as unspecified, never invented (#1157).

A general tolerance is a manufacturing requirement. Defaulting the title block to
``ISO 2768-m`` asserted one on every drawing whose caller never supplied it and whose
source model never carried it — including every STEP-derived generated script, which
emits no ``tolerance=`` at all.
"""

import inspect

import pytest
from build123d import Align, Box, Mode, Text

from draftwright import Sheet
from draftwright.builder import build_drawing, make_drawing
from draftwright.fonts import PLEX_SANS_CONDENSED

_PART = Box(30, 20, 10)


def _sheet(**options):
    sheet = Sheet(_PART, title="BLOCK", page="A3", **options)
    sheet.authored_dimensions()
    return sheet


def _fields(drawing) -> dict[str, str]:
    """The exact non-empty strings the title block rendered, by ISO 7200 field name."""
    block = drawing.get_annotation("title_block")
    return {field: value for field, value, _size, _font in block.title_field_specs}


@pytest.fixture(scope="module")
def unauthored():
    return _sheet().build()


@pytest.fixture(scope="module")
def authored():
    return _sheet(tolerance="ISO 2768-m").build()


def test_the_title_block_has_a_general_tolerance_cell(unauthored):
    """Precondition: the field exists and is populated, so a later assertion about its
    CONTENT is not passing merely because the cell was dropped."""
    assert "general_tolerance" in _fields(unauthored)
    assert unauthored.get_annotation("title_block").cell_bbox("general_tolerance")["width"] > 0


def test_unauthored_general_tolerance_is_stated_unspecified(unauthored):
    fields = _fields(unauthored)
    assert fields["general_tolerance"] == "UNSPECIFIED"
    # The defect this replaces: the string reached the sheet with nobody having authored it.
    assert "ISO 2768-m" not in fields.values()


def test_an_authored_tolerance_round_trips(authored):
    assert _fields(authored)["general_tolerance"] == "ISO 2768-m"


def test_an_explicit_empty_tolerance_leaves_the_cell_blank():
    """`""` stays available as the deliberate blank, distinct from both other states."""
    assert "general_tolerance" not in _fields(_sheet(tolerance="").build())


def test_the_unspecified_text_clears_its_own_cell(unauthored):
    """Chosen for width, so assert the width rather than trusting the choice — and measure
    what the block actually rendered, not a literal repeated here, or a longer replacement
    would overflow the cell while this test went on passing."""
    block = unauthored.get_annotation("title_block")
    ink = Text(
        _fields(unauthored)["general_tolerance"],
        font_size=3,
        font_path=PLEX_SANS_CONDENSED,
        align=(Align.CENTER, Align.CENTER),
        mode=Mode.PRIVATE,
    ).bounding_box()
    assert ink.size.X < block.cell_bbox("general_tolerance")["width"]
    overflow = [
        issue
        for issue in unauthored.lint()
        if issue.code == "title_field_overflow" and "general_tolerance" in issue.message
    ]
    assert overflow == []


@pytest.mark.parametrize("entry", [build_drawing, make_drawing, Sheet.__init__])
def test_no_public_entry_point_defaults_to_a_manufacturing_tolerance(entry):
    default = inspect.signature(entry).parameters["tolerance"].default
    assert default is None, (
        f"{entry.__qualname__} still authors {default!r} on its caller's behalf"
    )


def test_the_cli_default_authors_nothing():
    from draftwright.cli import main as cli_main

    assert inspect.signature(cli_main).parameters["tolerance"].default.default is None


@pytest.mark.slow
def test_a_generated_script_never_authors_a_tolerance(tmp_path):
    """The emitted `Sheet(...)` carries an aspect only when it differs from the default, so
    an unauthored tolerance must leave no trace for a re-run to resurrect."""
    from draftwright.sheet_emit import generate_sheet_script

    plain = generate_sheet_script(
        _PART, out=str(tmp_path / "plain"), title="BLOCK", formats=("svg",), inspect=False
    )
    ctor = _constructor_line(plain)
    assert "tolerance=" not in ctor
    assert "2768" not in ctor

    asked = generate_sheet_script(
        _PART,
        out=str(tmp_path / "asked"),
        title="BLOCK",
        tolerance="ISO 2768-m",
        formats=("svg",),
        inspect=False,
    )
    assert "tolerance='ISO 2768-m'" in _constructor_line(asked)


def _constructor_line(script_path) -> str:
    from pathlib import Path

    for line in Path(script_path).read_text().splitlines():
        if line.startswith("sheet = Sheet("):
            return line
    raise AssertionError(f"no Sheet constructor in {script_path}")
