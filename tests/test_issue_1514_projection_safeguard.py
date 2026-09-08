"""Unsupported first-angle intent must never become a third-angle drawing."""

from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos
from typer.testing import CliRunner

from draftwright import Drawing, Sheet, build_drawing, make_drawing
from draftwright.cli import app
from draftwright.sheet_emit import generate_sheet_script


@pytest.fixture(scope="module")
def asymmetric_plate():
    stock = Box(50, 35, 12)
    cutter = Pos(-13, -8, 0) * Cylinder(3, 20)
    part = stock - cutter
    assert cutter.center().X == pytest.approx(-13)
    assert cutter.center().Y == pytest.approx(-8)
    assert stock.volume - part.volume == pytest.approx(3**2 * 3.141592653589793 * 12)
    return part


@pytest.mark.parametrize("entry", [build_drawing, make_drawing, Sheet, generate_sheet_script])
def test_first_angle_refused_before_reading_input(entry, tmp_path):
    missing = tmp_path / "missing.step"
    assert not missing.exists()
    with pytest.raises(ValueError, match="first-angle.*not supported.*third"):
        entry(str(missing), projection="first")
    assert not list(tmp_path.iterdir())


def test_asymmetric_plate_cannot_claim_first_angle(asymmetric_plate):
    with pytest.raises(ValueError, match="first-angle.*not supported.*third"):
        build_drawing(asymmetric_plate, projection="first", page="A3", scale=1)


@pytest.mark.parametrize("projection", [None, "third"])
def test_supported_layout_retains_third_angle_relationships(asymmetric_plate, projection):
    drawing = build_drawing(asymmetric_plate, projection=projection, page="A3", scale=1)
    assert drawing.model().features
    assert {"front", "plan", "side"} <= set(drawing.views)
    front, plan, side = (drawing.view_bounds(v) for v in ("front", "plan", "side"))
    assert plan[1] > front[3]
    assert side[0] > front[2]
    assert ("projection_symbol" in drawing.annotations()) == (projection == "third")


@pytest.mark.parametrize("script", [False, True])
@pytest.mark.parametrize("source", ["missing.step", "missing_module:part"])
def test_cli_refuses_before_loading_step_or_object(script, source, tmp_path):
    args = [source, "--projection", "first", "--out", str(tmp_path / "drawing")]
    if script:
        args.append("--script")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2, result.output
    assert "first-angle" in result.output and "not supported" in result.output
    assert "third" in result.output
    assert not list(tmp_path.iterdir())


def test_edited_generated_script_cannot_claim_first_angle(asymmetric_plate, tmp_path, monkeypatch):
    path = generate_sheet_script(
        asymmetric_plate,
        out=str(tmp_path / "drawing"),
        projection="third",
        part_expr="part = supplied_part",
    )
    source = Path(path).read_text()
    assert source.count("projection='third'") == 1
    edited = source.replace("projection='third'", "projection='first'")
    exports = []
    monkeypatch.setattr(Drawing, "export", lambda *a, **kw: exports.append(True))
    with pytest.raises(ValueError, match="first-angle.*not supported.*third"):
        exec(compile(edited, path, "exec"), {"supplied_part": asymmetric_plate})
    assert not exports
