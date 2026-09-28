import inspect
from pathlib import Path
from typing import get_type_hints

from draftwright import Sheet
from draftwright.sheet import _Control
from draftwright.sheet_views import Self, _SheetViewMethods

_SHEET_REFERENCE = Path(__file__).resolve().parent.parent / "docs" / "reference" / "sheet.md"

_CONTROL_CHARACTERISTICS = {
    "straightness",
    "flatness",
    "circularity",
    "cylindricity",
    "profile_line",
    "profile_surface",
    "angularity",
    "perpendicularity",
    "parallelism",
    "position",
    "concentricity",
    "symmetry",
    "circular_runout",
    "total_runout",
}


def test_every_gdt_characteristic_has_its_own_reference_docstring():
    """A class-level summary cannot populate IDE help or the rendered method entry."""
    public_methods = {
        name
        for name, value in vars(_Control).items()
        if not name.startswith("_") and inspect.isfunction(value)
    }
    assert public_methods == _CONTROL_CHARACTERISTICS
    assert all(inspect.getdoc(getattr(_Control, name)) for name in public_methods)


def test_sheet_reference_includes_public_inherited_view_methods():
    public_view_methods = {
        name
        for name, value in vars(_SheetViewMethods).items()
        if not name.startswith("_") and inspect.isfunction(value)
    }
    assert public_view_methods
    assert all(
        getattr(Sheet, name) is getattr(_SheetViewMethods, name) for name in public_view_methods
    )

    sheet_directive = _SHEET_REFERENCE.read_text().split("::: draftwright.sheet.Sheet\n", 1)[1]
    sheet_options = sheet_directive.split("\n### ", 1)[0]
    assert "      filters: public\n" in sheet_options
    assert "      inherited_members: true\n" in sheet_options


def test_inherited_sheet_fluent_return_hints_resolve():
    fluent = ("take_over", "authored_views", "auto_views", "section", "detail", "row", "column")
    for name in fluent:
        assert get_type_hints(getattr(Sheet, name))["return"] is Self
