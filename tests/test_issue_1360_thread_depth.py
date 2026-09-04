"""#1360 foundation: tap depth is structured and bore fits stay on the bore."""

from __future__ import annotations

import pytest
from build123d import Box
from build123d_drafting.helpers import draft_preset

from draftwright import Sheet
from draftwright.annotations.from_model import callout_from_spec
from draftwright.compose import _est_planned_bore_callout_width
from draftwright.model import PartModel, ThreadOperation, hole, plan_dimensions
from draftwright.model.callout import hole_callout_spec, hole_callout_suffix
from draftwright.sheet_emit import emit_sheet_script


def _stacked_sheet() -> tuple[Sheet, object]:
    sheet = Sheet(Box(40, 40, 20), page="A3").authored_dimensions()
    handle = (
        sheet.hole(diameter=5, at=(0, 0, 10), axis="z", through=False, depth=48.5)
        .cbore(diameter=6.35, depth=5.5)
        .thread("M6x1", depth=12)
        .fit("H8")
    )
    for parameter_id in handle.dimension_ids():
        sheet.dimension(handle, parameter_id)
    return sheet, handle


def test_tap_depth_is_a_stable_addressable_parameter_and_printed_measurement():
    sheet, handle = _stacked_sheet()
    assert "thread.depth" in handle.dimension_ids()

    group = next(group for group in plan_dimensions(sheet.model()) if group.feature.kind == "hole")
    spec = hole_callout_spec(group)
    assert spec is not None
    assert spec["thread_depth"] == 12
    assert spec["suffix"] == "M6x1 x 12 DEEP"
    assert {identity.parameter for identity in spec["measurements"]} == {
        "bore.diameter",
        "bore.depth",
        "counterbore.diameter",
        "counterbore.depth",
        "thread.depth",
    }


def test_shared_suffix_formatter_keeps_legacy_hand_built_specs():
    assert hole_callout_suffix({"suffix": "M3x0.5"}) == "M3x0.5"


def test_bore_fit_does_not_leak_onto_the_counterbore_diameter():
    sheet, _handle = _stacked_sheet()
    group = next(group for group in plan_dimensions(sheet.model()) if group.feature.kind == "hole")
    tolerances = {planned.param.parameter_id: planned.param.tolerance for planned in group.dims}
    assert tolerances["bore.diameter"].code == "H8"
    assert tolerances["counterbore.diameter"] is None

    drawing = sheet.build()
    label = next(
        annotation.label
        for name, annotation in drawing.iter_annotations()
        if name.startswith("hc_")
    )
    assert label == "⌀5 H8 ↧ 48.5 ⌴ ⌀6.3 ↧ 5.5 M6x1 x 12 DEEP"
    assert label.count("H8") == 1


def test_fit_and_tolerance_order_keeps_last_writer_for_the_bore():
    def tolerances(*, fit_last: bool):
        sheet = Sheet(Box(40, 40, 20)).auto_dimensions()
        handle = sheet.hole(diameter=5, at=(0, 0, 10), axis="z").cbore(diameter=8, depth=3)
        if fit_last:
            handle.tolerance(0.05).fit("H8")
        else:
            handle.fit("H8").tolerance(0.05)
        group = next(
            group for group in plan_dimensions(sheet.model()) if group.feature.kind == "hole"
        )
        return {planned.param.parameter_id: planned.param.tolerance for planned in group.dims}

    fit_last = tolerances(fit_last=True)
    assert fit_last["bore.diameter"].code == "H8"
    assert fit_last["counterbore.diameter"] == 0.05

    tolerance_last = tolerances(fit_last=False)
    assert tolerance_last["bore.diameter"] == 0.05
    assert tolerance_last["counterbore.diameter"] == 0.05


def test_generated_sheet_preserves_broad_tolerance_beneath_bore_fit():
    sheet = Sheet(Box(40, 40, 20)).auto_dimensions()
    sheet.hole(diameter=5, at=(0, 0, 10), axis="z").cbore(diameter=8, depth=3).tolerance(0.05).fit(
        "H8"
    )
    source = emit_sheet_script(
        sheet.model(), "part", "fit-and-tolerance", title="FIT", number="1360"
    )
    assert ".tolerance(0.05).fit('H8')" in source

    namespace = {"part": Box(40, 40, 20)}
    exec(  # noqa: S102 - generated public Sheet source is the round-trip under test
        compile(source[: source.index("drawing = sheet.build()")], "<fit-tolerance>", "exec"),
        namespace,
    )
    rebuilt = namespace["sheet"]
    group = next(
        group for group in plan_dimensions(rebuilt.model()) if group.feature.kind == "hole"
    )
    tolerances = {planned.param.parameter_id: planned.param.tolerance for planned in group.dims}
    assert tolerances["bore.diameter"].code == "H8"
    assert tolerances["counterbore.diameter"] == 0.05


@pytest.mark.parametrize("depth", [0, -1, float("nan"), float("inf"), True, "12", None, object()])
def test_thread_operation_rejects_nonpositive_or_nonfinite_depth(depth):
    with pytest.raises(ValueError, match="thread depth must be finite and positive"):
        ThreadOperation("M6x1", depth)


@pytest.mark.parametrize("designation", [None, 123, b"M6x1", object(), " "])
def test_thread_operation_rejects_non_string_or_empty_designation(designation):
    with pytest.raises(ValueError, match="thread designation must be a non-empty string"):
        ThreadOperation(designation, 12)


@pytest.mark.parametrize("through", [False, True])
def test_tap_depth_cannot_exceed_available_bore_depth(through):
    with pytest.raises(ValueError, match="thread depth cannot exceed bore depth"):
        hole(
            diameter=5,
            at=(0, 0, 10),
            axis="z",
            through=through,
            depth=5,
            thread=ThreadOperation("M6x1", 5.1),
        )


def test_authored_thread_depth_cannot_survive_without_the_callout_head():
    sheet = Sheet(Box(40, 40, 20)).authored_dimensions()
    handle = sheet.hole(diameter=5, at=(0, 0, 10), axis="z").thread("M6x1", depth=12)
    sheet.dimension(handle, "thread.depth")
    group = next(group for group in plan_dimensions(sheet.model()) if group.feature.kind == "hole")
    with pytest.raises(ValueError, match=r"thread\.depth.*no callout to head"):
        hole_callout_spec(group)


def test_thread_depth_width_estimation_and_rendering_share_one_suffix():
    feature = hole(
        diameter=5,
        at=(0, 0, 10),
        axis="z",
        through=False,
        depth=48.5,
        cbore=(6.35, 5.5),
        thread=ThreadOperation("M6x1", 12),
    )
    model = PartModel(Box(40, 40, 20).bounding_box(), "prismatic", [feature])
    groups = plan_dimensions(model)
    group = next(group for group in groups if group.feature.kind == "hole")
    draft = draft_preset(font_size=2.5, decimal_precision=1)
    rendered = callout_from_spec(hole_callout_spec(group), draft, None)
    assert rendered is not None and "M6x1 x 12 DEEP" in rendered.label
    assert _est_planned_bore_callout_width(groups, draft) >= rendered.callout_width


def test_generated_sheet_round_trips_thread_depth_fit_and_dimension_identity():
    sheet, _handle = _stacked_sheet()
    source = emit_sheet_script(
        sheet.model(),
        "part",
        "stack",
        title="STACK",
        number="1360",
        view_constraints=sheet.view_constraints,
    )
    assert "ThreadOperation(designation='M6x1', depth=12.0)" in source
    assert ".fit('H8')" in source
    assert 'sheet.dimension(hole1, "thread.depth")' in source

    namespace = {"part": Box(40, 40, 20)}
    body = source.replace("\npart\n", "\n", 1)
    exec(  # noqa: S102 - generated public Sheet source is the round-trip under test
        compile(body[: body.index("drawing = sheet.build()")], "<thread-depth>", "exec"),
        namespace,
    )
    rebuilt = namespace["sheet"]
    feature = next(feature for feature in rebuilt.model().features if feature.kind == "hole")
    assert feature.thread == ThreadOperation("M6x1", 12)
    assert {request.role for request in rebuilt.model().authored_dimensions} >= {"thread.depth"}
    group = next(
        group for group in plan_dimensions(rebuilt.model()) if group.feature.kind == "hole"
    )
    tolerances = {planned.param.parameter_id: planned.param.tolerance for planned in group.dims}
    assert tolerances["bore.diameter"].code == "H8"
    assert tolerances["counterbore.diameter"] is None
