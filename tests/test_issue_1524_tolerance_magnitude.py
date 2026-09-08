"""Supplied tolerance values survive nominal precision and actual output."""

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet
from draftwright._core import _dimension_draft, _tol_suffix
from draftwright.annotations.from_model import callout_from_spec
from draftwright.compose import _est_planned_bore_callout_width
from draftwright.model.callout import hole_callout_spec
from draftwright.model.planner import plan_dimensions


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.02, " ±0.02"),
        (0.05, " ±0.05"),
        (0.2, " ±0.2"),
        (1, " ±1.0"),
        (1.234567, " ±1.234567"),
        (0.000000123, " ±0.000000123"),
        ((0.002, 0.015), " +0.015 -0.002"),
        ((0.0, 0.00001), " +0.00001 -0.0"),
    ],
)
def test_numeric_magnitude_is_not_rounded_to_nominal_precision(value, expected):
    assert _tol_suffix(value, _dimension_draft()) == expected


@pytest.mark.parametrize("value,text", [(0.02, "±0.02"), ((0.002, 0.015), "+0.015 -0.002")])
def test_rendered_callout_table_and_reserved_width_keep_small_tolerances(value, text):
    tool = Pos(5, 3, 0) * Cylinder(3, 20)
    part = Box(40, 30, 12) - tool
    sheet = Sheet(part, page="A3", scale=1)
    handle = sheet.hole(tool)
    handle.tolerance(*value) if isinstance(value, tuple) else handle.tolerance(value)
    sheet.dimension(handle, "bore.diameter")
    sheet.dimension(handle, "location")
    model = sheet.model()
    groups = plan_dimensions(model)
    (hole_group,) = [group for group in groups if group.feature.kind == "hole"]
    draft = _dimension_draft()
    primitive = callout_from_spec(hole_callout_spec(hole_group), draft, None)
    assert text in primitive.label
    assert text in " ".join(item[0] for item in primitive.pdf_text_relative_specs)
    estimate = _est_planned_bore_callout_width([hole_group], draft)
    assert estimate >= primitive.bounding_box().size.X - 0.1
    assert estimate < primitive.bounding_box().size.X + 5
    drawing = sheet.build()
    marks = [
        a for _, a in drawing.iter_annotations() if getattr(a, "covers_diameters", ()) == (6,)
    ]
    assert len(marks) == 1 and text in marks[0].label
    table = drawing.add_hole_table(balloons=False)
    assert table is not None
    assert any(text in cell for row in table.table_rows for cell in row)
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]
