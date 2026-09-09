"""Projection convention changes sheet relationships, never physical view identity."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos

import draftwright.builder as builder
from draftwright import Drawing, Sheet, build_drawing
from draftwright.analysis import _analyse
from draftwright.audit import compare_measurements
from draftwright.compose import StripDepths, ViewBlock, _layout_geometry
from draftwright.registry import AnnotationRegistry
from draftwright.sheet_emit import generate_sheet_script


@pytest.fixture(scope="module")
def plate():
    stock = Box(50, 35, 12)
    cutter = Pos(-13, -8, 0) * Cylinder(3, 20)
    part = stock - cutter
    assert cutter.center().X < 0 and cutter.center().Y < 0
    assert part.volume < stock.volume
    return part


def _assert_convention(drawing, method):
    assert drawing.view_plan.convention == method
    front, plan, side = (drawing.view_bounds(v) for v in ("front", "plan", "side"))
    if method == "first":
        assert plan[3] < front[1]
        assert side[2] < front[0]
    else:
        assert plan[1] > front[3]
        assert side[0] > front[2]
    assert "projection_symbol" in drawing.annotations()
    assert drawing.get_annotation("projection_symbol").method == method


def _local_edges(drawing, view):
    ox, oy, _ = drawing.at(view, 0, 0, 0)
    return tuple(
        sorted(
            (
                str(edge.geom_type),
                round((edge.center().X - ox) / drawing.scale, 5),
                round((edge.center().Y - oy) / drawing.scale, 5),
                round(edge.length / drawing.scale, 5),
            )
            for edge in group.edges()
        )
        for group in drawing.views[view]
    )


@pytest.mark.parametrize("options", [{"page": "A3", "scale": 1}, {}])
def test_convention_preserves_physical_views_and_all_measurements(plate, options):
    model = Sheet.from_part(plate).model()
    holes = [f for f in model.features if f.kind == "hole"]
    assert len(holes) == 1 and holes[0].diameter == 6
    drawings = {
        method: build_drawing(plate, model=model, projection=method, **options)
        for method in ("third", "first")
    }
    for method, drawing in drawings.items():
        _assert_convention(drawing, method)
        assert drawing.annotations_of(holes[0])
        assert not [i for i in drawing.lint() if i.severity in {"warning", "error"}]
    comparison = compare_measurements(drawings["third"], drawings["first"])
    assert comparison["status"] == "preserved", comparison
    for view in ("front", "plan", "side"):
        assert _local_edges(drawings["first"], view) == _local_edges(drawings["third"], view)


@pytest.mark.parametrize("method", ["first", "third"])
def test_conflicting_principal_relation_refused_before_projection(plate, method, monkeypatch):
    sheet = Sheet(plate, projection=method).authored_dimensions()
    front = sheet.view("front")
    plan = sheet.view("plan")
    (plan.above if method == "first" else plan.below)(front)
    projected = []
    monkeypatch.setattr(builder, "_assemble", lambda *a, **kw: projected.append(True))
    with pytest.raises(ValueError, match="authored view constraint.*infeasible"):
        sheet.build()
    assert not projected


@pytest.mark.parametrize("method", ["first", "third"])
def test_reduced_authored_views_and_origin_pin(plate, method):
    sheet = Sheet(plate, projection=method, page="A3", scale=1).authored_dimensions()
    hole = sheet.hole(diameter=6, at=(-13, -8, 0), axis="z").tolerance(0.1)
    sheet.dimension(hole, "bore.diameter")
    front = sheet.view("front")
    plan = sheet.view("plan")
    (plan.below if method == "first" else plan.above)(front).align_x(front)
    front.pin((130, 160))
    drawing = sheet.build()
    assert set(drawing.views) == {"front", "plan"}
    assert drawing.at("front", 0, 0, 0) == pytest.approx((130, 160, 0))
    assert drawing.view_plan.convention == method
    labels = [getattr(drawing.get_annotation(n), "label", "") for n in drawing.annotations()]
    assert any("6 ±0.1 THRU" in label for label in labels)


@pytest.mark.parametrize("method", ["first", "third"])
@pytest.mark.parametrize(
    "views", [("front",), ("plan",), ("side",), ("front", "side"), ("plan", "side")]
)
def test_explicit_reduced_view_sets_remain_available(plate, method, views):
    sheet = Sheet(plate, projection=method, page="A3", scale=1).authored_dimensions()
    for view in views:
        sheet.view(view)
    drawing = sheet.build()
    assert set(drawing.views) == set(views)
    assert drawing.view_plan.convention == method
    for view in views:
        assert drawing.views[view][0].edges()
        x0, y0, x1, y1 = drawing.view_bounds(view)
        assert 0 < x0 < x1 < drawing.page_w
        assert 0 < y0 < y1 < drawing.page_h
    if "plan" not in views:
        codes = {issue.code for issue in drawing.lint()}
        assert {"feature_no_centermark", "feature_not_located"} <= codes


def test_first_angle_script_and_exports_keep_the_convention(plate, tmp_path, monkeypatch):
    path = generate_sheet_script(
        plate,
        out=str(tmp_path / "plate"),
        projection="first",
        page="A3",
        scale=1,
        part_expr="part = supplied_part",
    )
    source = Path(path).read_text()
    assert source.count("projection='first'") == 1
    captured = []
    export = Drawing.export
    monkeypatch.setattr(Drawing, "export", lambda self, *a, **kw: captured.append(self))
    exec(compile(source, path, "exec"), {"supplied_part": plate})
    (drawing,) = captured
    _assert_convention(drawing, "first")
    exported = export(drawing, str(tmp_path / "first"), formats=("svg", "pdf", "dxf"))
    assert set(exported) == {"svg", "pdf", "dxf"}
    assert all(Path(p).stat().st_size > 0 for p in exported.values())
    _assert_convention(drawing, "first")


@pytest.mark.parametrize("method", ["first", "third"])
def test_authored_sections_survive_an_isometric_below_the_row(method):
    sheet = Sheet(Box(50, 30, 10), page="A2", projection=method).authored_dimensions()
    sheet.section_view("A", at=-8)
    sheet.section_view("B", at=8)
    drawing = sheet.build()
    if method == "first":
        assert drawing.view_bounds("iso")[3] < drawing.view_bounds("front")[1] - 10
    assert {"section_aa", "section_bb"} <= set(drawing.views)
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


@pytest.mark.parametrize("method", ["first", "third"])
@pytest.mark.parametrize("arrangement", ["columns", "stacked-iso"])
def test_measured_bands_are_packed_on_the_correct_sides(method, arrangement):
    blocks = {
        "front": ViewBlock(25, 6, top=21, right=24, bottom=27, left=30),
        "plan": ViewBlock(25, 17.5, top=33, right=36, bottom=39, left=42),
        "side": ViewBlock(17.5, 6, top=45, right=48, bottom=51, left=54),
    }
    geometry = _layout_geometry(
        50,
        35,
        12,
        1,
        594,
        420,
        150,
        None,
        blocks=blocks,
        convention=method,
        arrangement=arrangement,
    )
    assert geometry.fits
    boxes = {
        name: block.footprint(getattr(geometry, prefix + "_X"), getattr(geometry, prefix + "_Y"))
        for (name, block), prefix in zip(blocks.items(), ("FV", "PV", "SV"), strict=True)
    }
    front, plan, side = (boxes[v] for v in ("front", "plan", "side"))
    if method == "first":
        assert plan[3] <= front[1]
        assert side[2] <= min(front[0], plan[0])
    else:
        assert plan[1] >= front[3]
        assert side[0] >= max(front[2], plan[2])


@pytest.mark.parametrize("method", ["first", "third"])
def test_submillimetre_locations_cannot_claim_a_complete_scale(plate, method):
    with pytest.raises(builder.ScaleIncompatibilityError) as error:
        build_drawing(plate, page="A3", scale=0.01, scale_policy="strict", projection=method)
    assert error.value.decision["status"] == "rejected"
    blockers = error.value.decision["blockers"]
    assert any(b["code"] == "location_ref_dropped" and b["measurements"] for b in blockers)
    assert {"x", "y"} <= {
        m["parameter"].rsplit(".", 1)[-1] for b in blockers for m in b["measurements"]
    }


def test_repack_applies_a_small_move_when_ink_is_outside_the_page(monkeypatch):
    analysis = _analyse(
        Box(50, 30, 10),
        title="",
        number="",
        tolerance="",
        drawn_by="",
        out="small",
        page="A3",
        scale=1,
        projection="first",
    )
    original_geometry = builder._layout_geometry

    def small_correction(*args, **kwargs):
        geometry = original_geometry(*args, **kwargs)
        for name in ("FV_X", "FV_Y", "PV_X", "PV_Y", "SV_X", "SV_Y"):
            setattr(
                geometry, name, getattr(analysis, name) + (0.05 if name.endswith("X") else 0.0)
            )
        return geometry

    monkeypatch.setattr(builder, "_layout_geometry", small_correction)
    monkeypatch.setattr(builder, "_needs_repack", lambda *_: True)
    monkeypatch.setattr(builder, "_annotations_out_of_bounds", lambda *_: True)
    monkeypatch.setattr(builder, "_measure_blocks", lambda *_: {})
    assembled = []

    def assemble(chosen, *args, **kwargs):
        assembled.append(chosen)
        return "corrected drawing"

    monkeypatch.setattr(builder, "_assemble", assemble)
    drawing = SimpleNamespace(registry=AnnotationRegistry())
    result = builder._repack(analysis, drawing, "small", None, False, scale=1, page="A3")
    assert result is not None
    corrected, output = result
    assert output == "corrected drawing" and assembled == [corrected]
    assert 0 < corrected.SV_X - analysis.SV_X < builder._REPACK_TOL
    assert corrected.SV_X - analysis.SV_X == pytest.approx(0.05)


@pytest.mark.parametrize("method", ["first", "third"])
def test_plan_only_does_not_reserve_a_phantom_front_corridor(method):
    geometry = _layout_geometry(
        50,
        35,
        12,
        1,
        297,
        210,
        150,
        StripDepths(right=20, left=20, pv_location_top=250),
        views=("plan",),
        include_iso=False,
        convention=method,
    )
    assert geometry.fits
    assert geometry.planned_views == ("plan",)
