"""A drawing-wide text choice must preserve content, ink bounds and replay."""

import json
from math import cos, radians, sin, sqrt

import pytest
from build123d import Box, Cylinder, Polygon, Pos, export_step, extrude
from build123d_drafting import Dimension

from draftwright import Sheet, build_drawing
from draftwright._core import _dimension_draft
from draftwright.annotations._common import dim_footprint
from draftwright.annotations.angular import AngularInk
from draftwright.audit import compare_measurements
from draftwright.sheet_emit import emit_sheet_script, generate_sheet_script


@pytest.fixture(scope="module")
def plate():
    part = Box(50, 35, 12) - Pos(-13, -8, 0) * Cylinder(3, 20)
    model = Sheet.from_part(part).model()
    assert len([feature for feature in model.features if feature.kind == "hole"]) == 1
    return part, model


@pytest.mark.parametrize("position", ["inline", "above"])
@pytest.mark.parametrize("orientation", ["aligned", "horizontal"])
@pytest.mark.parametrize("projection", ["first", "third"])
@pytest.mark.parametrize("scale", [None, 1])
def test_style_preserves_complete_plate_measurements(
    plate, position, orientation, projection, scale
):
    part, model = plate
    baseline = build_drawing(part, model=model, page="A3", scale=scale, projection=projection)
    options = json.loads(json.dumps({"text_position": position, "text_orientation": orientation}))
    drawing = build_drawing(
        part, model=model, page="A3", scale=scale, projection=projection, **options
    )
    assert drawing.draft.text_position == position
    assert drawing.draft.text_orientation == orientation
    assert compare_measurements(baseline, drawing)["status"] == "preserved"
    dimensions = [
        annotation
        for _, annotation in drawing.iter_annotations()
        if isinstance(annotation, Dimension)
    ]
    assert len(dimensions) >= 3
    if orientation == "horizontal":
        assert all(
            dim.label_polygon[1][1] == pytest.approx(dim.label_polygon[0][1]) for dim in dimensions
        )
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


@pytest.mark.parametrize(
    "position,orientation",
    [("above", "aligned"), ("above", "horizontal"), ("inline", "horizontal")],
)
@pytest.mark.parametrize("angle", [0, 10, 45, 80, 90, 145])
@pytest.mark.parametrize("distance", [2, 10])
@pytest.mark.parametrize("length", [3, 45])
def test_styled_analytical_bounds_enclose_actual_ink(
    position, orientation, angle, length, distance
):
    end = (length * cos(radians(angle)), length * sin(radians(angle)))
    side = "left" if angle == 90 else "above"
    draft = _dimension_draft(position, orientation)
    label = "3" if length == 3 else "45 ±0.01"
    estimate = dim_footprint((0, 0), end, side, distance, draft, label)
    actual = Dimension((0, 0), end, side, distance, draft, label=label).bounding_box()
    assert estimate[0] <= actual.min.X + 1e-5
    assert estimate[1] <= actual.min.Y + 1e-5
    assert estimate[2] >= actual.max.X - 1e-5
    assert estimate[3] >= actual.max.Y - 1e-5
    assert estimate[2] - estimate[0] < actual.size.X + 5
    assert estimate[3] - estimate[1] < actual.size.Y + 5


@pytest.mark.parametrize("position", ["inline", "above"])
@pytest.mark.parametrize("orientation", ["aligned", "horizontal"])
def test_angular_style_preserves_full_text_and_geometry(position, orientation):
    draft = _dimension_draft(position, orientation)
    ink = AngularInk((0, 0), (15, 0), (7.5, 7.5 * sqrt(3)), "60 ±0.01°", draft)
    annotation = ink.build(ink.minimum_radius + 5)
    assert annotation.label == "60 ±0.01°"
    assert annotation.measured_angle == pytest.approx(60)
    polygon = annotation.label_polygon
    if orientation == "horizontal":
        assert polygon[1][1] == pytest.approx(polygon[0][1])
    radial = ink.bisector
    nearest = min(x * radial[0] + y * radial[1] for x, y in polygon)
    if position == "above":
        assert nearest > annotation.arc_radius + draft.pad_around_text
        middle = ink.point(annotation.arc_radius, ink.middle)
        assert any(face.is_inside((*middle, 0)) for face in annotation.faces())
    else:
        assert nearest < annotation.arc_radius
    actual = annotation.bounding_box()
    box = ink.footprint(annotation.arc_radius)
    assert box[0] <= actual.min.X and box[1] <= actual.min.Y
    assert box[2] >= actual.max.X and box[3] >= actual.max.Y


def test_declared_angular_style_replays_and_exports(tmp_path):
    part = extrude(Polygon((0, 0), (30, 0), (15, 15 * sqrt(3)), align=None), amount=12)
    options = {"text_position": "above", "text_orientation": "horizontal"}
    sheet = Sheet(part, page="A3", scale=1, **options).authored_dimensions()
    vertices = ((0, 0, 12), (30, 0, 12), (15, 15 * sqrt(3), 12))
    for index, vertex in enumerate(vertices):
        first, second = vertices[(index + 1) % 3], vertices[(index + 2) % 3]
        angle = sheet.angle(
            vertex=vertex,
            first=tuple((a + b) / 2 for a, b in zip(vertex, first, strict=True)),
            second=tuple((a + b) / 2 for a, b in zip(vertex, second, strict=True)),
        )
        angle.tolerance(0.01, on="included.angle")
        sheet.dimension(angle, "included.angle")
    direct = sheet.build()
    script = emit_sheet_script(
        sheet.model(),
        "part = supplied_part",
        str(tmp_path / "replay"),
        title="STYLE",
        number="STYLE",
        page="A3",
        scale=1,
        formats=("svg", "pdf", "dxf"),
        **options,
    )
    namespace = {"supplied_part": part}
    exec(script, namespace)
    replay = namespace["drawing"]
    assert replay.draft.text_position == "above" and replay.draft.text_orientation == "horizontal"
    originals = sheet.model().features
    recreated = namespace["sheet"].model().features
    assert len(originals) == len(recreated) == 3
    # Declaration order is an explicit fixture correspondence, never an
    # inference performed by the comparison auditor.
    pairs = tuple(zip(originals, recreated, strict=True))
    assert all(old.angular_reference == new.angular_reference for old, new in pairs)
    assert compare_measurements(direct, replay, feature_pairs=pairs)["status"] == "preserved"
    angles = [a for _, a in replay.iter_annotations() if hasattr(a, "measured_angle")]
    assert len(angles) == 3 and all("0.01" in angle.label for angle in angles)
    assert not [issue for issue in replay.lint() if issue.severity in {"warning", "error"}]
    assert all(
        (tmp_path / f"replay.{extension}").stat().st_size > 100
        for extension in ("svg", "pdf", "dxf")
    )


@pytest.mark.parametrize("field", ["text_position", "text_orientation"])
@pytest.mark.parametrize("route", ["builder", "sheet", "script"])
def test_invalid_style_refuses_before_accessing_the_part(field, route, tmp_path):
    options = {field: "unknown"}
    with pytest.raises(ValueError, match=field):
        if route == "builder":
            build_drawing("does-not-exist.step", **options)
        elif route == "sheet":
            Sheet(object(), **options)
        else:
            generate_sheet_script("does-not-exist.step", out=str(tmp_path / "bad"), **options)


@pytest.mark.parametrize("route", ["direct", "step_script", "object_script"])
def test_cli_style_reaches_real_automatic_ink(route, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from draftwright import cli

    part = Box(50, 35, 12)
    source = tmp_path / "part.step"
    export_step(part, source)
    if route == "object_script":
        source = tmp_path / "part_source.py"
        source.write_text("from build123d import Box\npart = Box(50, 35, 12)\n")
        source = f"{source}:part"
    drawings = []
    original_emit = cli._emit

    def capture(drawing, formats):
        drawings.append(drawing)
        return original_emit(drawing, formats)

    monkeypatch.setattr(cli, "_emit", capture)
    prefix = tmp_path / "styled"
    args = [
        str(source),
        "--out",
        str(prefix),
        "--no-report",
        "--format",
        "svg",
        "--text-position",
        "above",
        "--text-orientation",
        "horizontal",
    ]
    if route != "direct":
        args.append("--script")
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    if route != "direct":
        namespace = {}
        exec(prefix.with_suffix(".py").read_text(), namespace)
        drawings.append(namespace["drawing"])
    (drawing,) = drawings
    assert drawing.draft.text_position == "above"
    assert drawing.draft.text_orientation == "horizontal"
    dimensions = [a for _, a in drawing.iter_annotations() if isinstance(a, Dimension)]
    assert len(dimensions) >= 3
    assert all(a.label_polygon[0][1] == pytest.approx(a.label_polygon[1][1]) for a in dimensions)
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]
    assert prefix.with_suffix(".svg").stat().st_size > 100
