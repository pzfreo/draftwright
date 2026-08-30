"""End-to-end 0.4.8 adoption and RaisedPad schema-v2 covariance (#1392)."""

import ast
from importlib import metadata
from pathlib import Path

import pytest
from b123d_recognisers import (
    FramedRecognitionResult,
    build_framed_recognition_result,
    build_raw_recognition_result,
)
from build123d import Align, Axis, Box, Compound, Pos, RegularPolygon, Rot, extrude

from draftwright import build_drawing
from draftwright.builder import detect_part_model
from draftwright.model import PadFeature, pad

_MIN = (Align.MIN, Align.MIN, Align.MIN)


def test_exact_wheel_and_production_raw_coordinate_boundary_are_explicit():
    assert metadata.version("b123d-recognisers") == "0.4.8"
    root = Path(__file__).parents[1] / "src" / "draftwright"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "build_recognition_result":
                offenders.append(str(path.relative_to(root)))
            if isinstance(node, ast.Attribute) and node.attr == "build_recognition_result":
                offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_048_public_framed_contract_returns_exact_local_pad_working_solid():
    source = Box(100, 70, 12, align=(Align.CENTER, Align.CENTER, Align.MIN))
    source += Pos(18, -10, 12) * Box(28, 16, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    moved = Pos(17, -23, 9) * Rot(31, 47, 13) * source

    framed = build_framed_recognition_result(moved, rotational=False)

    assert isinstance(framed, FramedRecognitionResult)
    assert framed.part is not moved
    assert len(framed.part.solids()) == len(moved.solids()) == 1
    (record,) = framed.result.pads
    local_bb = framed.part.bounding_box()
    tol = 1e-3  # public records are deterministically rounded to three decimals
    assert local_bb.min.X - tol <= record.x0 < record.x1 <= local_bb.max.X + tol
    assert local_bb.min.Y - tol <= record.y0 < record.y1 <= local_bb.max.Y + tol
    assert local_bb.min.Z - tol <= record.z0 < record.z1 <= local_bb.max.Z + tol
    spans = {
        "x": record.x1 - record.x0,
        "y": record.y1 - record.y0,
        "z": record.z1 - record.z0,
    }
    assert spans.pop(record.axis) == pytest.approx(8, abs=1e-3)
    assert sorted(spans.values()) == pytest.approx([16, 28], abs=1e-3)


def test_048_public_framed_contract_preserves_polygonal_boss_covariance():
    source = Box(100, 80, 10) + Pos(0, 0, 5) * extrude(RegularPolygon(20, 6), 30)
    moved = Pos(17, -23, 9) * Rot(31, 47, 13) * source

    framed = build_framed_recognition_result(moved, rotational=False)

    assert isinstance(framed, FramedRecognitionResult)
    (record,) = framed.result.polygonal_bosses
    assert record.axis in "xyz"
    assert record.side_count == 6
    assert record.height == pytest.approx(30, abs=1e-3)
    local_bb = framed.part.bounding_box()
    axis_bounds = {
        "x": (local_bb.min.X, local_bb.max.X),
        "y": (local_bb.min.Y, local_bb.max.Y),
        "z": (local_bb.min.Z, local_bb.max.Z),
    }
    assert (
        axis_bounds[record.axis][0] - 1e-3
        <= record.base
        < record.top
        <= axis_bounds[record.axis][1] + 1e-3
    )


def test_048_public_framed_contract_preserves_rolled_body_local_plates():
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    member = Box(40, 10, 4, align=align) + Pos(0, 7, 4) * Box(1, 14, 20, align=align)
    rolled = member.rotate(Axis.Z, 37)
    moved = Pos(17, -23, 9) * Rot(31, 47, 13) * rolled

    framed = build_framed_recognition_result(moved, rotational=False)

    assert isinstance(framed, FramedRecognitionResult)
    assert len(framed.result.plates) == 2
    local_bb = framed.part.bounding_box()
    local_bounds = {
        "x": (local_bb.min.X, local_bb.max.X),
        "y": (local_bb.min.Y, local_bb.max.Y),
        "z": (local_bb.min.Z, local_bb.max.Z),
    }
    assert all(
        local_bounds[plate.axis][0] <= plate.lo < plate.hi <= local_bounds[plate.axis][1]
        for plate in framed.result.plates
    )


def _pad_part(axis: str, direction: int):
    base = Pos(-30, -25, -10) * Box(60, 50, 20, align=_MIN)
    if axis == "x":
        origin = (30 if direction > 0 else -36, -15, -7)
        island = Pos(*origin) * Box(6, 20, 12, align=_MIN)
    elif axis == "y":
        origin = (-15, 25 if direction > 0 else -31, -7)
        island = Pos(*origin) * Box(20, 6, 12, align=_MIN)
    else:
        origin = (-15, -9, 10 if direction > 0 else -16)
        island = Pos(*origin) * Box(20, 12, 6, align=_MIN)
    return base + island


def _bounds(feature: PadFeature):
    half = feature.width / 2
    by_axis = {
        feature.long_axis: (feature.lo, feature.hi),
        feature.width_axis: (feature.w_center - half, feature.w_center + half),
        feature.frame.axis: (feature.z0, feature.z1),
    }
    return tuple(value for axis in "xyz" for value in by_axis[axis])


@pytest.mark.parametrize("axis", "xyz")
@pytest.mark.parametrize("direction", (-1, 1))
def test_signed_pad_semantics_reach_ir_solver_and_critique(axis, direction):
    part = _pad_part(axis, direction)
    source = build_raw_recognition_result(part).pads
    assert len(source) == 1
    record = source[0]
    assert (record.axis, record.direction) == (axis, direction)

    feature = next(f for f in detect_part_model(part).features if isinstance(f, PadFeature))
    assert (feature.frame.axis, feature.direction, feature.occurrence) == (axis, direction, 0)
    assert _bounds(feature) == pytest.approx(
        (record.x0, record.x1, record.y0, record.y1, record.z0, record.z1)
    )
    assert {parameter.parameter_id for parameter in feature.parameters()} == {
        "pad_width.length",
        "pad_length.length",
        "pad_height.length",
    }

    rebuilt = pad(
        x0=record.x0,
        x1=record.x1,
        y0=record.y0,
        y1=record.y1,
        z0=record.z0,
        z1=record.z1,
        axis=record.axis,
        direction=record.direction,
        occurrence=0,
    )
    assert rebuilt == feature

    # The six-millimetre height needs enough paper span for inward arrows; at 1:1
    # the correct outcome is an explicit placement drop, not a fabricated dimension.
    drawing = build_drawing(part, page="A1", scale=5.0)
    measurements = {
        identity
        for name in drawing.registry.names()
        for identity in drawing.registry.measurement_of(name)
        if identity.feature == feature
    }
    parameter_ids = {identity.parameter for identity in measurements}
    assert {"pad_width.length", "pad_length.length", "pad_height.length"} <= parameter_ids
    assert {f"location_pad.{candidate}" for candidate in "xyz" if candidate != axis} <= (
        parameter_ids
    )
    assert "pad_footprint_not_defined" not in drawing.lint_summary()["by_code"]


def test_equal_pad_values_keep_distinct_occurrence_identity():
    first = _pad_part("z", 1)
    second = Pos(80, 0, 0) * _pad_part("z", 1)
    model = detect_part_model(Compound(children=[first, second]))
    pads = [feature for feature in model.features if isinstance(feature, PadFeature)]
    assert len(pads) == 2
    assert [feature.occurrence for feature in pads] == [0, 1]
    assert pads[0] != pads[1]
