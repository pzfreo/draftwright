"""Public make_drawing entry-point behavior."""

from pathlib import Path

import pytest
from build123d import Box, Cylinder, export_step
from build123d_drafting import Centerline

from draftwright import Drawing, make_drawing


@pytest.mark.timeout(120)
def test_make_drawing_box(tmp_path):
    """make_drawing() produces SVG and DXF for a simple box STEP file."""
    # Build a simple box and export to STEP
    box = Box(30, 20, 10)
    step_file = str(tmp_path / "box.step")
    export_step(box, step_file)

    out_stem = str(tmp_path / "box_drawing")
    svg_path, dxf_path = make_drawing(
        step_file,
        out=out_stem,
        title="TEST BOX",
        number="TST-001",
    )

    assert Path(svg_path).exists()
    assert Path(dxf_path).exists()
    assert Path(svg_path).stat().st_size > 1000
    assert Path(dxf_path).stat().st_size > 100

    # SVG should have the full page dimensions injected
    svg_content = Path(svg_path).read_text()
    assert 'mm"' in svg_content  # width/height in mm


@pytest.mark.timeout(120)
def test_make_drawing_cylinder_uses_centerline(tmp_path, monkeypatch):
    """make_drawing() retains automatic cylinder furniture before serialization."""
    cyl = Cylinder(radius=15, height=40)
    step_file = str(tmp_path / "cyl.step")
    export_step(cyl, step_file)

    captured = []

    def capture(drawing, *, formats):
        captured.append((drawing, formats))
        return {"svg": "unused.svg", "dxf": "unused.dxf"}

    monkeypatch.setattr(Drawing, "export", capture)
    make_drawing(step_file, out=str(tmp_path / "cyl_drawing"), title="CYL")

    [(drawing, formats)] = captured
    assert formats == ("svg", "dxf")
    assert isinstance(drawing.get_annotation("centerline_front"), Centerline)


@pytest.mark.timeout(120)
def test_make_drawing_default_title(tmp_path, monkeypatch):
    """Title defaults to a display-formatted uppercase stem when not provided."""
    box = Box(10, 10, 10)
    step_file = str(tmp_path / "my_part.step")
    export_step(box, step_file)

    captured = []

    def capture(drawing, *, formats):
        captured.append(drawing)
        return {"svg": "unused.svg", "dxf": "unused.dxf"}

    monkeypatch.setattr(Drawing, "export", capture)
    make_drawing(step_file, out=str(tmp_path / "out"))

    [drawing] = captured
    title = drawing.get_annotation("title_block")
    assert title.label == "MY PART"


@pytest.mark.timeout(120)
def test_make_drawing_object_defaults_out_to_drawing(tmp_path, monkeypatch):
    """An in-memory object uses the default output stem and writes both public formats."""
    monkeypatch.chdir(tmp_path)
    box = Box(10, 10, 10)

    svg_path, dxf_path = make_drawing(box)

    assert Path(svg_path).name == "drawing.svg"
    assert Path(dxf_path).name == "drawing.dxf"
    assert (tmp_path / "drawing.svg").exists()
    assert (tmp_path / "drawing.dxf").exists()
    assert (tmp_path / "drawing.svg").stat().st_size > 1000
    assert (tmp_path / "drawing.dxf").stat().st_size > 100
