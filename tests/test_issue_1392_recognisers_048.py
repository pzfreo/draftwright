"""Consumer evidence for the quiddity 0.4.8 adoption (#1392)."""

from __future__ import annotations

import math
from dataclasses import asdict, fields, replace
from pathlib import Path

import pytest
from _pad_rendering_fixtures import _signed_pad
from build123d import Align, Box, Compound, Pos, RegularPolygon, Rot, extrude
from quiddity import (
    FramedRecognitionResult,
    build_framed_recognition_result,
    recognise_plates,
    recognise_polygonal_bosses,
    recognise_rectangular_pads,
)

from draftwright import build_drawing
from draftwright.model import Frame, PadFeature, PartModel
from draftwright.model import pad as declare_pad


@pytest.mark.xfail(strict=True, reason="#1935: update the corridor registration probe")
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
