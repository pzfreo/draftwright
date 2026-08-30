"""Consumer evidence for the b123d-recognisers 0.4.8 adoption (#1392)."""

from __future__ import annotations

import math
from dataclasses import asdict, fields, replace
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

import draftwright.analysis as analysis_mod
from draftwright import Drawing, Sheet, build_drawing
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
    expected_axes = {"x": ("y", "z"), "y": ("z", "x"), "z": ("x", "y")}
    assert (pad.long_axis, pad.width_axis) == expected_axes[axis]
    record_bounds = {
        "x": (record.x0, record.x1),
        "y": (record.y0, record.y1),
        "z": (record.z0, record.z1),
    }
    parameters = {parameter.parameter_id: parameter for parameter in pad.parameters()}
    assert parameters["pad_length.length"].value == pytest.approx(
        record_bounds[pad.long_axis][1] - record_bounds[pad.long_axis][0]
    )
    assert parameters["pad_width.length"].value == pytest.approx(
        record_bounds[pad.width_axis][1] - record_bounds[pad.width_axis][0]
    )
    attachment = list(pad.frame.origin)
    terminal = list(pad.frame.origin)
    normal_index = "xyz".index(axis)
    attachment[normal_index] = record_bounds[axis][0 if direction > 0 else 1]
    terminal[normal_index] = record_bounds[axis][1 if direction > 0 else 0]
    assert parameters["pad_height.length"].span == (tuple(attachment), tuple(terminal))
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


def test_x_pad_compose_reserves_both_end_on_dimension_bands():
    """Scale selection must account for the side view's outward right corridor."""
    part = _box((10, 20, 20), (0, 0, 0)) + _box((5, 9, 9), (10, 4, 4))

    drawing = build_drawing(part)
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    placed = {
        key["parameter_id"]
        for name in drawing.annotations_of(pad)
        for key in drawing.measurement_keys(name)
    }

    assert {
        "pad_width.length",
        "pad_length.length",
        "pad_height.length",
        "location_pad.y",
        "location_pad.z",
    } <= placed
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


def test_x_pad_side_strip_consumes_its_reserved_band_at_scale_two():
    """The side strip starts at geometry, not beyond its reserved outer footprint."""
    part = _box((10, 12, 12), (0, 0, 0)) + _box((5, 5.4, 5.4), (10, 2.4, 2.4))

    drawing = build_drawing(part)
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    placed = {
        key["parameter_id"]
        for name in drawing.annotations_of(pad)
        for key in drawing.measurement_keys(name)
    }

    assert drawing.scale == 2
    assert {
        "pad_width.length",
        "pad_length.length",
        "pad_height.length",
        "location_pad.y",
        "location_pad.z",
    } <= placed
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


def test_x_pad_height_leader_stays_out_of_adjacent_front_view_ink():
    """A side-view HIGH label must not enter the front/side annotation corridor."""
    part = _box((10, 60, 16), (0, 0, 0)) + _box((5, 27, 7.2), (10, 12, 3.2))

    drawing = build_drawing(part)

    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


def test_mixed_x_and_opposed_z_pads_keep_their_own_location_ordinates():
    """An off-axis pad cannot become the datum source for the Z-pad location ladder."""
    part = (
        _box((40, 40, 20), (0, 0, 0))
        + _box((5, 10, 8), (40, 5, 5))
        + _box((10, 8, 5), (20, 25, -5))
        + _box((10, 8, 5), (20, 25, 20))
    )

    drawing = build_drawing(part)
    locations = {
        drawing.view_of(name): (annotation.label, drawing.measurement_keys(name))
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_loc")
    }

    assert {view: label for view, (label, _keys) in locations.items()} == {
        "plan": "25",
        "side": "29",
    }
    for _label, keys in locations.values():
        assert len(keys) == 2
        assert {key["parameter_id"] for key in keys} == {"location_pad.location"}
        assert all("/z[" in key["feature"] for key in keys)
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


def test_x_pad_footprint_and_location_candidates_join_the_shared_corridor(monkeypatch):
    import draftwright.annotations.from_model as renderer

    registered = set()
    real_register = renderer.register_corridor

    def recording_register(ctx, key, strip, view, axis, tier, candidate):
        if getattr(candidate.feature, "kind", None) == "pad":
            registered.add((candidate.name, key))
        return real_register(ctx, key, strip, view, axis, tier, candidate)

    monkeypatch.setattr(renderer, "register_corridor", recording_register)
    build_drawing(_signed_pad("x", 1))

    assert {
        ("m_pad0_length", ("side", "above")),
        ("m_pad0_width", ("side", "right")),
        ("m_pad0_pos_long", ("side", "above")),
        ("m_pad0_pos_width", ("side", "right")),
    } <= registered


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("direction", [-1, 1])
def test_authored_pad_height_does_not_require_suppressed_footprint_measurements(axis, direction):
    part = _signed_pad(axis, direction)
    automatic = build_drawing(part)
    source_pad = next(feature for feature in automatic.model().features if feature.kind == "pad")
    x0, x1 = source_pad.bounds("x")
    y0, y1 = source_pad.bounds("y")
    z0, z1 = source_pad.bounds("z")
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.pad(
        x0=x0,
        x1=x1,
        y0=y0,
        y1=y1,
        z0=z0,
        z1=z1,
        axis=axis,
        direction=direction,
        at=source_pad.frame.origin,
    )
    handle.tolerance(0.1, on="pad_height")
    sheet.dimension(handle, "pad_height.length")

    drawing = sheet.build()
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    height_names = [
        name
        for name in drawing.annotations_of(pad)
        if "pad_height.length" in {key["parameter_id"] for key in drawing.measurement_keys(name)}
    ]

    assert len(height_names) == 1
    assert drawing.get_annotation(height_names[0]).label == "5 ±0.1 HIGH"

    source = emit_sheet_script(drawing.model(), "part", "out", title="PAD", number="1392")
    marker = "\ndrawing = sheet.build()"
    namespace = {"part": part}
    exec(source[: source.index(marker)] + "\nrebuilt = sheet.build()\n", namespace)  # noqa: S102
    rebuilt: Drawing = namespace["rebuilt"]
    rebuilt_pad = next(feature for feature in rebuilt.model().features if feature.kind == "pad")
    rebuilt_height = next(
        name
        for name in rebuilt.annotations_of(rebuilt_pad)
        if "pad_height.length" in {key["parameter_id"] for key in rebuilt.measurement_keys(name)}
    )
    assert rebuilt.get_annotation(rebuilt_height).label == "5 ±0.1 HIGH"


def test_authored_pad_height_does_not_reserve_suppressed_side_pad_bands(monkeypatch):
    """Suppressed footprint/location marks cannot reduce the selected drawing scale."""
    measured_side_strips = []
    real_measure = analysis_mod._measure_strips

    def record_measure(*args, **kwargs):
        strips = real_measure(*args, **kwargs)
        measured_side_strips.append((strips.sv_top, strips.sv_right))
        return strips

    monkeypatch.setattr(analysis_mod, "_measure_strips", record_measure)
    part = _box((20, 20, 22), (0, 0, 0)) + _box((5, 6, 6), (20, 7, 8))
    sheet = Sheet(part, page="A4").authored_dimensions()
    pad = sheet.pad(
        x0=20,
        x1=25,
        y0=7,
        y1=13,
        z0=8,
        z1=14,
        axis="x",
        at=(22.5, 10, 11),
    )
    sheet.dimension(pad, "pad_height.length")

    drawing = sheet.build()
    drawn_pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    parameter_ids = {
        key["parameter_id"]
        for name in drawing.annotations_of(drawn_pad)
        for key in drawing.measurement_keys(name)
    }

    assert drawing.scale == 2
    assert measured_side_strips
    assert set(measured_side_strips) == {(0.0, 0.0)}
    assert parameter_ids == {"pad_height.length"}
    assert {issue.code for issue in drawing.lint() if issue.severity in {"warning", "error"}} == {
        "pad_footprint_not_defined"
    }


def test_generated_sheet_preserves_each_independent_pad_tolerance():
    part = _signed_pad("z", 1)
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.pad(x0=5, x1=20, y0=8, y1=18, z0=10, z1=15)
    for parameter, tolerance in (
        ("pad_width.length", 0.1),
        ("pad_length.length", 0.2),
        ("pad_height.length", 0.3),
    ):
        handle.tolerance(tolerance, on=parameter)
        sheet.dimension(handle, parameter)
    direct = sheet.build()

    source = emit_sheet_script(direct.model(), "part", "out", title="PAD", number="1392")
    for parameter in ("pad_width.length", "pad_length.length", "pad_height.length"):
        assert f"on={parameter!r}" in source
    marker = "\ndrawing = sheet.build()"
    namespace = {"part": part}
    exec(source[: source.index(marker)] + "\nrebuilt = sheet.build()\n", namespace)  # noqa: S102
    rebuilt: Drawing = namespace["rebuilt"]

    direct_pad = next(feature for feature in direct.model().features if feature.kind == "pad")
    rebuilt_pad = next(feature for feature in rebuilt.model().features if feature.kind == "pad")

    def labels(drawing, pad):
        return sorted(
            (key["parameter_id"], drawing.get_annotation(name).label)
            for name in drawing.annotations_of(pad)
            for key in drawing.measurement_keys(name)
            if key["parameter_id"].startswith("pad_")
        )

    assert labels(rebuilt, rebuilt_pad) == labels(direct, direct_pad)


def test_pad_ir_preserves_legacy_dataclass_fields_serialisation_and_replace():
    legacy = PadFeature(Frame((0, 0, 2.5), "z"), "y", "x", 8, 20, 0, -10, 10, 0, 5)
    assert (legacy.normal_lo, legacy.normal_hi, legacy.z0, legacy.z1) == (0, 5, 0, 5)
    assert [field.name for field in fields(PadFeature)] == [
        "frame",
        "width_axis",
        "long_axis",
        "width",
        "length",
        "w_center",
        "lo",
        "hi",
        "z0",
        "z1",
        "direction",
    ]
    payload = asdict(legacy)
    assert payload["z0"] == 0 and payload["z1"] == 5
    assert "normal_lo" not in payload and "normal_hi" not in payload
    assert replace(legacy, z0=-1, z1=6).height == 7

    side = PadFeature(Frame((2.5, 0, 0), "x"), "z", "y", 8, 20, 0, -10, 10, 0, 5)
    assert side.normal_lo == 0 and side.normal_hi == 5


def test_pad_ir_and_declaration_reject_invalid_signed_bounds():
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
    with pytest.raises(TypeError, match="z0"):
        PadFeature(**base)
    with pytest.raises(ValueError, match="distinct"):
        PadFeature(**(base | {"width_axis": "x"}), z0=0, z1=5)
    with pytest.raises(ValueError, match="direction"):
        PadFeature(**base, z0=0, z1=5, direction=0)
    with pytest.raises(ValueError, match="direction"):
        PadFeature(**base, z0=0, z1=5, direction=True)
    with pytest.raises(ValueError, match="must increase"):
        PadFeature(**(base | {"width": 0}), z0=0, z1=5)
    for field, value in (
        ("width", math.nan),
        ("length", math.inf),
        ("w_center", -math.inf),
        ("lo", -math.inf),
        ("hi", math.inf),
        ("z0", -math.inf),
        ("z1", math.inf),
    ):
        with pytest.raises(ValueError, match="finite"):
            PadFeature(**(base | {"z0": 0, "z1": 5, field: value}))

    valid = PadFeature(**base, z0=0, z1=5)
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
