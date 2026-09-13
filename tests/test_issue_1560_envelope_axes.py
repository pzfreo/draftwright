"""A missing envelope proposal cannot make body extents disappear silently."""

from build123d import Box, Cylinder

from draftwright import build_drawing
from draftwright.model.compiled import compile_dimensions
from draftwright.model.ir import PartModel


def test_undimensioned_prismatic_axes_have_compiler_and_lint_outcomes():
    # The original bracket has this bounding box and only its synthesized
    # overall-height ladder. A deliberately sparse declared model reproduces
    # that planning boundary without depending on unavailable sheet-metal CAD.
    part = Box(70, 73.033, 36.933)
    model = PartModel(bbox=part.bounding_box(), orientation=None, features=[])
    plan = compile_dimensions(model)
    assert [
        (ladder.kind, [rung.value_text for rung in ladder.rungs]) for ladder in plan.ladders
    ] == [("overall_height", ["36.9"])]
    missing = [o for o in plan.diagnostics if o.code == "overall_dim_withheld"]
    assert {(o.parameter_id, round(o.value, 3)) for o in missing} == {
        ("width.length", 70.0),
        ("depth.length", 73.033),
    }

    drawing = build_drawing(part, model=model)
    issues = [i for i in drawing.lint() if i.code == "overall_dim_withheld"]
    assert {i.message for i in issues} == {
        "overall X extent has no approved bounding measurement",
        "overall Y extent has no approved bounding measurement",
    }
    assert all(not issue.measurement_ids for issue in issues)


def test_ordinary_box_and_round_stock_do_not_gain_false_envelope_withholdings():
    for part in (Box(70, 73.033, 36.933), Cylinder(10, 20)):
        drawing = build_drawing(part)
        assert not [i for i in drawing.lint() if i.code == "overall_dim_withheld"]
