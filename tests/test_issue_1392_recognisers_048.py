"""Consumer evidence for the b123d-recognisers 0.4.8 adoption (#1392)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from b123d_recognisers import (
    FramedRecognitionResult,
    build_framed_recognition_result,
    recognise_plates,
    recognise_polygonal_bosses,
    recognise_rectangular_pads,
)
from build123d import (
    Align,
    Box,
    Compound,
    Location,
    Pos,
    RegularPolygon,
    Rot,
    extrude,
)

from draftwright import Drawing, build_drawing
from draftwright.model import Frame, PadFeature, PartModel, ViewPlanIncomplete
from draftwright.model import pad as declare_pad
from draftwright.model.planner import plan_dimensions
from draftwright.sheet_emit import emit_sheet_script

_END_ON = {"x": "side", "y": "front", "z": "plan"}


def _box(size, position):
    return Box(*size, align=(Align.MIN, Align.MIN, Align.MIN)).moved(Location(position))


def _signed_pad(axis: str, direction: int):
    """One asymmetric pad occurrence with a five-millimetre signed attachment span."""
    if axis == "z":
        body_pos = (0, 0, 0 if direction > 0 else 5)
        pad_pos = (5, 8, 10 if direction > 0 else 0)
        return _box((40, 30, 10), body_pos) + _box((15, 10, 5), pad_pos)
    if axis == "x":
        body_pos = (0 if direction > 0 else 5, 0, 0)
        pad_pos = (10 if direction > 0 else 0, 8, 5)
        return _box((10, 30, 40), body_pos) + _box((5, 10, 15), pad_pos)
    body_pos = (0, 0 if direction > 0 else 5, 0)
    pad_pos = (5, 10 if direction > 0 else 0, 8)
    return _box((40, 10, 30), body_pos) + _box((15, 5, 10), pad_pos)


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("direction", [-1, 1])
def test_all_signed_pad_records_reach_complete_solver_owned_drawings(axis, direction):
    part = _signed_pad(axis, direction)
    drawing = build_drawing(part)
    (record,) = drawing.recognition().pads
    pad = next(feature for feature in drawing.model().features if isinstance(feature, PadFeature))

    assert (record.axis, record.direction) == (axis, direction)
    assert (pad.frame.axis, pad.direction) == (axis, direction)
    assert {pad.frame.axis, pad.long_axis, pad.width_axis} == {"x", "y", "z"}
    assert tuple(pad.bounds(name) for name in "xyz") == (
        (record.x0, record.x1),
        (record.y0, record.y1),
        (record.z0, record.z1),
    )
    assert plan_dimensions(drawing.model())[0].view == _END_ON[axis]

    expected = {"pad_width.length", "pad_length.length", "pad_height.length"}
    expected_locations = (
        {"location_pad.location"}
        if axis == "z"
        else {f"location_pad.{pad.long_axis}", f"location_pad.{pad.width_axis}"}
    )
    placed = {
        key["parameter_id"]
        for name in drawing.annotations_of(pad)
        for key in drawing.measurement_keys(name)
    }
    assert expected | expected_locations <= placed
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("direction", [-1, 1])
def test_signed_pad_sheet_code_executes_with_exact_ir_and_measurement_parity(axis, direction):
    part = _signed_pad(axis, direction)
    direct = build_drawing(part)
    source = emit_sheet_script(direct.model(), "part", "out", title="PAD", number="1392")

    assert f"axis='{axis}'" in source
    assert f"direction={direction}" in source
    marker = "\ndrawing = sheet.build()"
    assert source.count(marker) == 1
    namespace = {"part": part}
    exec(source[: source.index(marker)] + "\nrebuilt = sheet.build()\n", namespace)  # noqa: S102
    rebuilt: Drawing = namespace["rebuilt"]

    direct_pad = next(feature for feature in direct.model().features if feature.kind == "pad")
    rebuilt_pad = next(feature for feature in rebuilt.model().features if feature.kind == "pad")
    assert rebuilt_pad == direct_pad

    def evidence(drawing, pad):
        return sorted(
            (drawing.view_of(name), drawing.get_annotation(name).label, key["parameter_id"])
            for name in drawing.annotations_of(pad)
            for key in drawing.measurement_keys(name)
        )

    assert evidence(rebuilt, rebuilt_pad) == evidence(direct, direct_pad)


@pytest.mark.parametrize(
    "parameter",
    [
        "pad_width.length",
        "pad_length.length",
        "pad_height.length",
        "location_pad.y",
        "location_pad.z",
    ],
)
def test_each_missing_side_pad_requirement_has_an_explicit_lint_outcome(parameter):
    drawing = build_drawing(_signed_pad("x", 1))
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    name = next(
        name
        for name in drawing.annotations_of(pad)
        if parameter in {key["parameter_id"] for key in drawing.measurement_keys(name)}
    )

    drawing.remove(name)

    issues = [issue for issue in drawing.lint() if issue.code == "pad_footprint_not_defined"]
    assert len(issues) == 1
    assert "height" in issues[0].message and "in-plane location" in issues[0].message


@pytest.mark.parametrize("direction", [-1, 1])
def test_missing_z_pad_height_has_an_explicit_lint_outcome(direction):
    drawing = build_drawing(_signed_pad("z", direction))
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    name = next(
        name
        for name in drawing.annotations_of(pad)
        if "pad_height.length" in {key["parameter_id"] for key in drawing.measurement_keys(name)}
    )

    drawing.remove(name)

    issues = [issue for issue in drawing.lint() if issue.code == "pad_footprint_not_defined"]
    assert len(issues) == 1
    assert "height" in issues[0].message


@pytest.mark.parametrize("view", ["plan", "side"])
def test_each_missing_z_pad_location_ordinate_has_an_explicit_lint_outcome(view):
    drawing = build_drawing(_signed_pad("z", 1))
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    name = next(
        name
        for name in drawing.annotations_of(pad)
        if drawing.view_of(name) == view
        and "location_pad.location"
        in {key["parameter_id"] for key in drawing.measurement_keys(name)}
    )

    drawing.remove(name)

    issues = [issue for issue in drawing.lint() if issue.code == "pad_footprint_not_defined"]
    assert len(issues) == 1
    assert "in-plane location" in issues[0].message


def test_side_pad_height_and_footprint_require_the_end_on_view():
    drawing = build_drawing(_signed_pad("x", 1))

    with pytest.raises(ViewPlanIncomplete) as caught:
        plan_dimensions(drawing.model(), planned_views=("front", "plan"))

    uncovered = {(item.identity.parameter, item.preferred_view) for item in caught.value.uncovered}
    assert {
        ("pad_width.length", "side"),
        ("pad_length.length", "side"),
        ("pad_height.length", "side"),
        ("location_pad.location", "side"),
    } <= uncovered


def test_pad_ir_keeps_the_legacy_z_constructor_but_refuses_ambiguous_side_bounds():
    legacy = PadFeature(Frame((0, 0, 2.5), "z"), "y", "x", 8, 20, 0, -10, 10, 0, 5)
    assert (legacy.normal_lo, legacy.normal_hi, legacy.z0, legacy.z1) == (0, 5, 0, 5)

    with pytest.raises(ValueError, match="legacy z0=/z1="):
        PadFeature(Frame((2.5, 0, 0), "x"), "z", "y", 8, 20, 0, -10, 10, 0, 5)


def test_pad_ir_and_declaration_reject_ambiguous_or_invalid_signed_bounds():
    base = dict(
        frame=Frame((0, 0, 2.5), "z"),
        width_axis="y",
        long_axis="x",
        width=8,
        length=20,
        w_center=0,
        lo=-10,
        hi=10,
    )
    with pytest.raises(ValueError, match="not both"):
        PadFeature(**base, z0=0, z1=5, normal_lo=0, normal_hi=5)
    with pytest.raises(ValueError, match="needs normal_lo"):
        PadFeature(**base)
    with pytest.raises(ValueError, match="distinct"):
        PadFeature(**(base | {"width_axis": "x"}), normal_lo=0, normal_hi=5)
    with pytest.raises(ValueError, match="direction"):
        PadFeature(**base, normal_lo=0, normal_hi=5, direction=0)
    with pytest.raises(ValueError, match="must increase"):
        PadFeature(**(base | {"width": 0}), normal_lo=0, normal_hi=5)

    valid = PadFeature(**base, normal_lo=0, normal_hi=5)
    with pytest.raises(ValueError, match="unknown pad axis"):
        valid.bounds("q")
    with pytest.raises(ValueError, match="direction"):
        declare_pad(x0=-10, x1=10, y0=-4, y1=4, z0=0, z1=5, direction=0)


def test_a_recognised_pad_missing_from_ir_fails_visible():
    part = _signed_pad("z", 1)
    empty = PartModel(bbox=part.bounding_box(), orientation="prismatic", features=[])

    drawing = build_drawing(part, model=empty)

    issues = [issue for issue in drawing.lint() if issue.code == "pad_footprint_not_defined"]
    assert len(issues) == 1
    assert issues[0].severity == "warning"


def test_0331_framed_pad_records_correspond_to_the_exact_local_working_solid():
    source = Box(100, 70, 12, align=(Align.CENTER, Align.CENTER, Align.MIN))
    source += Pos(18, -10, 12) * Box(28, 16, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    moved = Pos(17, -23, 9) * Rot(31, 47, 13) * source

    framed = build_framed_recognition_result(moved, rotational=False)

    assert isinstance(framed, FramedRecognitionResult)
    assert framed.result.pads == tuple(recognise_rectangular_pads(framed.part))
    (record,) = framed.result.pads
    spans = ((record.x0, record.x1), (record.y0, record.y1), (record.z0, record.z1))
    axial = spans["xyz".index(record.axis)]
    transverse = [span for index, span in enumerate(spans) if index != "xyz".index(record.axis)]
    assert axial[1] - axial[0] == pytest.approx(8, abs=1e-3)
    assert sorted(hi - lo for lo, hi in transverse) == pytest.approx([16, 28], abs=1e-3)


def _polygonal_boss():
    plate = Box(100, 80, 10)
    prism = Pos(0, 0, 5) * extrude(RegularPolygon(20, 6), 30)
    return plate + prism


def test_0332_framed_polygonal_boss_corresponds_to_the_exact_local_working_solid():
    moved = Pos(17, -23, 9) * Rot(31, 47, 13) * _polygonal_boss()

    framed = build_framed_recognition_result(moved, rotational=False)

    assert isinstance(framed, FramedRecognitionResult)
    assert framed.result.polygonal_bosses == tuple(recognise_polygonal_bosses(framed.part))
    (record,) = framed.result.polygonal_bosses
    assert record.side_count == 6
    assert record.across_flats == pytest.approx(20 * math.sqrt(3), abs=1e-3)
    assert record.height == 30


def _bracket():
    return (Pos(0, 0, 5) * Box(80, 60, 10)) + (Pos(0, 0, 35) * Box(80, 10, 50))


def test_0334_framed_plate_occurrences_remain_body_local_on_the_exact_working_solid():
    nested = Compound(
        children=[
            Compound(children=[Pos(-70, 0, 0) * _bracket()]),
            Compound(children=[Pos(70, 0, 0) * _bracket()]),
        ]
    )
    moved = Pos(17, -23, 9) * Rot(31, 47, 13) * nested

    framed = build_framed_recognition_result(moved, rotational=False)

    assert isinstance(framed, FramedRecognitionResult)
    assert framed.result.plates == tuple(recognise_plates(framed.part))
    assert len(framed.result.plates) == 4
    assert len({(plate.axis, plate.u) for plate in framed.result.plates}) == 4


def test_production_names_the_raw_aggregate_boundary_explicitly():
    source_root = Path(__file__).parents[1] / "src" / "draftwright"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in source_root.rglob("*.py"))

    assert "build_raw_recognition_result" in sources
    assert "build_recognition_result" not in sources
