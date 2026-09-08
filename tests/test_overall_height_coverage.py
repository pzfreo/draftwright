"""An incomplete axial chain cannot substitute for the overall extent (#1509)."""

from dataclasses import replace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet
from draftwright.model.compiled import compile_dimensions
from draftwright.model.declare import _envelope_from_bbox
from draftwright.model.ir import Frame, GrooveFeature, PartModel, RequestedDimension, StepFeature
from draftwright.sheet_emit import emit_sheet_script


def _step(lo, hi, *, group="shaft", x=0):
    return StepFeature(
        Frame((x, 0, (lo + hi) / 2), "z"),
        length=hi - lo,
        diameter=24,
        span=((x, 0, lo), (x, 0, hi)),
        profile_group=group,
    )


@pytest.mark.parametrize("axis", ("x", "z"))
@pytest.mark.parametrize(
    "steps,grooves,complete",
    [
        ([_step(-25, -3), _step(-3, 3), _step(3, 25)], [], False),
        ([_step(-31, -3), _step(3, 31)], [], False),
        ([_step(-31, 10), _step(-10, 31)], [], False),
        ([_step(-31, 0), _step(0, 31, group="other")], [], False),
        ([_step(-31, 0), _step(0, 31, x=30)], [], False),
        ([_step(0, 31), _step(-31, 0)], [], True),
        (
            [_step(-31, -3), _step(3, 31)],
            [GrooveFeature(Frame((0, 0, 0), "z"), "z", 6, 18, profile_group="shaft")],
            True,
        ),
        (
            [_step(-31, -3), _step(3, 31)],
            [GrooveFeature(Frame((0, 0, 0), "z"), "z", 6, 18, profile_group="other")],
            False,
        ),
    ],
    ids=(
        "end-caps",
        "internal-gap",
        "overlap",
        "other-body",
        "other-axis-line",
        "complete-reversed",
        "groove-bridge",
        "foreign-groove",
    ),
)
def test_only_connected_approved_spans_can_replace_overall_extent(steps, grooves, complete, axis):
    if axis == "x":
        steps = [
            replace(
                step,
                frame=Frame(tuple(reversed(step.frame.origin)), "x"),
                span=tuple(tuple(reversed(point)) for point in step.span),
            )
            for step in steps
        ]
        grooves = [
            replace(groove, frame=Frame(tuple(reversed(groove.frame.origin)), "x"), axis="x")
            for groove in grooves
        ]
    bb = Box(*((62, 80, 100) if axis == "x" else (100, 80, 62))).bounding_box()
    model = PartModel(bb, axis, [*steps, *grooves, _envelope_from_bbox(bb)])
    plan = compile_dimensions(model)
    assert len(plan.of_kind("step")) == len(steps)
    assert all(group.dim(kind="length") is not None for group in plan.of_kind("step"))
    if axis == "z":
        assert (plan.contingency("step_length") is not None) is complete
        height = plan.ladder("overall_height")
        dimension = height.rungs[0] if height is not None else None
    else:
        dimension = next(
            (
                dim
                for group in plan.of_kind("envelope")
                for dim in group.dims
                if dim.id.parameter == "width.length"
            ),
            None,
        )
    assert (dimension is None) is complete
    if dimension is not None:
        assert dimension.value == pytest.approx(62)
        assert [point["xyz".index(axis)] for point in dimension.span] == pytest.approx([-31, 31])
        parameter = "height.length" if axis == "z" else "width.length"
        assert not [row for row in plan.diagnostics if row.parameter_id == parameter]


def test_incomplete_x_chain_does_not_override_an_authored_width_omission():
    bb = Box(62, 30, 20).bounding_box()
    step = StepFeature(Frame((0, 0, 0), "x"), 50, 20, ((-25, 0, 0), (25, 0, 0)))
    model = PartModel(
        bb,
        "x",
        [step, _envelope_from_bbox(bb)],
        authored_dimensions=(RequestedDimension(step, "step.length"),),
    )
    plan = compile_dimensions(model)
    assert plan.of_kind("step")[0].dim(kind="length").value == 50
    assert not [
        dim
        for group in plan.of_kind("envelope")
        for dim in group.dims
        if dim.id.parameter == "width.length"
    ]
    omissions = [row for row in plan.diagnostics if row.parameter_id == "width.length"]
    assert len(omissions) == 1 and omissions[0].authored


@pytest.fixture(scope="module")
def partial_chain_drawing():
    intervals = [(37, -31, -25), (36, -25, -3), (35, -3, 3), (34, 3, 25), (33, 25, 31)]
    solids = [Pos(0, 0, (lo + hi) / 2) * Cylinder(radius, hi - lo) for radius, lo, hi in intervals]
    part = solids[0].fuse(*solids[1:])
    sheet = Sheet(part, page="A3", scale=1)
    sheet.authored_dimensions()
    for radius, lo, hi in intervals:
        handle = sheet.step(
            diameter=2 * radius,
            length=hi - lo,
            at=(0, 0, (lo + hi) / 2),
            axis="z",
            profile_group="shaft",
        )
        sheet.dimension(handle, "step.diameter")
        if lo >= -25 and hi <= 25:
            sheet.dimension(handle, "step.length")
    sheet.dimension(sheet.envelope(), "height.length")
    assert part.bounding_box().size.Z == pytest.approx(62)
    drawing = sheet.build()
    plan = compile_dimensions(drawing.model())
    lengths = [g.dim(kind="length") for g in plan.of_kind("step")]
    assert sum(dim.value for dim in lengths if dim is not None) == 50
    return drawing


def test_partial_chain_places_overall_with_physical_end_supports(partial_chain_drawing):
    drawing = partial_chain_drawing
    marks = [
        (name, mark)
        for name, mark in drawing.iter_annotations()
        if any(mid.parameter == "height.length" for mid in drawing.registry.measurement_of(name))
    ]
    assert len(marks) == 1
    name, mark = marks[0]
    assert mark.label == "62"
    view = drawing.registry.view_of(name)
    stations = [drawing.at(view, 0, 0, z)[1] for z in (-31, 31)]
    extensions = [
        first[1]
        for first, second in mark.segments
        if abs(first[1] - second[1]) < 1e-6 and abs(first[0] - second[0]) > 1
    ]
    assert all(any(abs(station - actual) < 1e-6 for actual in extensions) for station in stations)
    assert (
        sum(
            any(mid.parameter == "step.length" for mid in drawing.registry.measurement_of(n))
            for n in drawing.annotations()
        )
        == 3
    )
    assert drawing.lint() == drawing.lint()
    # An overall extent must not claim to locate the two omitted end shoulders.
    assert [issue.code for issue in drawing.lint() if issue.severity != "info"] == [
        "axial_length_missing"
    ]


def test_partial_chain_overall_survives_generated_script(partial_chain_drawing, tmp_path):
    original = partial_chain_drawing
    source = emit_sheet_script(
        original.model(),
        "part",
        str(tmp_path / "replay"),
        title="T",
        number="N",
        page="A3",
        scale=1,
        formats=("svg",),
    )
    namespace = {"part": original.working_part}
    exec(source, namespace)
    replay = namespace["drawing"]
    assert replay.registry.named("dim_height").label == "62"
    assert any(
        mid.parameter == "height.length" for mid in replay.registry.measurement_of("dim_height")
    )
    assert [issue.code for issue in replay.lint() if issue.severity != "info"] == [
        "axial_length_missing"
    ]
