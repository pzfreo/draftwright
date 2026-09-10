"""Projection furniture identifies the resolved layout, including implicit defaults."""

from inspect import Parameter, signature
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos, export_step
from typer.testing import CliRunner

from draftwright import Drawing, Sheet, build_drawing, make_drawing
from draftwright.audit import compare_measurements
from draftwright.sheet_emit import emit_sheet_script, generate_sheet_script


@pytest.fixture(scope="module")
def plate():
    part = Box(50, 35, 12) - Pos(-13, -8, 0) * Cylinder(3, 20)
    model = Sheet.from_part(part).model()
    assert len([f for f in model.features if f.kind == "hole"]) == 1
    return part, model


@pytest.mark.parametrize("projection", [None, "first", "third"])
@pytest.mark.parametrize("auto_dims", [True, False])
def test_visibility_changes_only_furniture(plate, projection, auto_dims):
    part, model = plate
    options = dict(model=model, projection=projection, auto_dims=auto_dims, page="A3", scale=1)
    shown = build_drawing(part, **options)
    hidden = build_drawing(part, projection_symbol=False, **options)
    symbol = shown.get_annotation("projection_symbol")
    assert symbol.method == shown.view_plan.convention == (projection or "third")
    assert symbol.edges()
    assert set(shown.annotations()) - set(hidden.annotations()) == {"projection_symbol"}
    assert not set(hidden.annotations()) - set(shown.annotations())
    assert shown.view_plan == hidden.view_plan
    assert all(shown.view_bounds(v) == hidden.view_bounds(v) for v in shown.views)
    if auto_dims:
        assert compare_measurements(shown, hidden)["status"] == "preserved"
    assert not [i for i in shown.lint() if i.severity == "error"]


@pytest.mark.parametrize("projection", [None, "first", "third"])
@pytest.mark.parametrize("visible", [True, False])
def test_generated_script_replays_symbol_policy(plate, projection, visible, tmp_path, monkeypatch):
    part, model = plate
    script = emit_sheet_script(
        model,
        "part = supplied_part",
        str(tmp_path / "drawing"),
        title="PROJECTION",
        number="PROJECTION-1",
        projection=projection,
        projection_symbol=visible,
        page="A3",
        scale=1,
    )
    assert ("projection_symbol=False" in script) is (not visible)
    captured = []
    monkeypatch.setattr(Drawing, "export", lambda self, *a, **kw: captured.append(self))
    exec(compile(script, "projection-example.py", "exec"), {"supplied_part": part})
    (drawing,) = captured
    assert drawing.view_plan.convention == (projection or "third")
    assert ("projection_symbol" in drawing.annotations()) is visible
    if visible:
        assert drawing.get_annotation("projection_symbol").method == drawing.view_plan.convention


@pytest.mark.parametrize("entry", [build_drawing, make_drawing, Sheet, generate_sheet_script])
@pytest.mark.parametrize("invalid", [None, "false", 0])
def test_invalid_visibility_refused_before_loading_input(entry, invalid, tmp_path):
    with pytest.raises(ValueError, match="projection_symbol must be a boolean"):
        entry(str(tmp_path / "missing.step"), projection_symbol=invalid)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("route", ["direct", "step_script", "object_script"])
@pytest.mark.parametrize("visible", [True, False])
def test_cli_policy_reaches_rendered_exports(route, visible, plate, tmp_path, monkeypatch):
    from draftwright import cli

    part, _ = plate
    source = tmp_path / "plate.step"
    export_step(part, source)
    if route == "object_script":
        source = tmp_path / "plate_source.py"
        source.write_text("from build123d import Box\npart = Box(50, 35, 12)\n")
        source = f"{source}:part"
    drawings = []
    original_export = Drawing.export

    def capture(self, *args, **kwargs):
        drawings.append(self)
        return original_export(self, *args, **kwargs)

    monkeypatch.setattr(Drawing, "export", capture)
    stem = tmp_path / "result"
    args = [str(source), "--out", str(stem), "--no-report", "--format", "svg,pdf,dxf"]
    if not visible:
        args.append("--no-projection-symbol")
    if route != "direct":
        args.append("--script")
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    if route != "direct":
        script = stem.with_suffix(".py").read_text()
        assert ("projection_symbol=False" in script) is (not visible)
        exec(compile(script, str(stem.with_suffix(".py")), "exec"), {})
    assert drawings
    for drawing in drawings:
        assert drawing.view_plan.convention == "third"
        assert ("projection_symbol" in drawing.annotations()) is visible
    for suffix in ("svg", "pdf", "dxf"):
        assert Path(f"{stem}.{suffix}").stat().st_size > 100


@pytest.mark.parametrize(
    ("entry", "previous"),
    [
        (
            build_drawing,
            "step_file out title number tolerance drawn_by scale page auto_dims "
            "detail_view pmi repair assembly model decorations requested authored trace material "
            "date revision company frame projection zones scale_policy reproducible "
            "framed_recognition text_position text_orientation _post_build _required_tables "
            "_views _include_iso _view_constraints _document_input",
        ),
        (
            make_drawing,
            "step_file out title number tolerance drawn_by scale page auto_dims "
            "detail_view pmi assembly material date revision company frame projection zones "
            "scale_policy reproducible framed_recognition text_position text_orientation",
        ),
    ],
)
def test_existing_positional_arguments_keep_their_bindings(entry, previous):
    names = previous.split()
    sentinels = [object() for _ in names]
    bound = signature(entry).bind(*sentinels)
    assert bound.arguments == dict(zip(names, sentinels, strict=True))
    assert signature(entry).parameters["projection_symbol"].kind is Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("projection", ["first", "third"])
@pytest.mark.parametrize("border", [{"frame": True}, {"zones": True}])
def test_symbol_clears_framed_content_margin(plate, projection, border):
    part, model = plate
    drawing = build_drawing(part, model=model, projection=projection, **border)
    symbol = drawing.get_annotation("projection_symbol")
    assert symbol.method == projection
    # The drawn frame is 10 mm in; content reserves another 6 mm inside it.
    frame = drawing.get_annotation("sheet_frame").bounding_box()
    box = symbol.bounding_box()
    assert box.min.X >= frame.min.X + 6 - 0.1
    assert box.max.X <= frame.max.X - 6 + 0.1
    assert box.min.Y >= frame.min.Y + 6 - 0.1
    assert box.max.Y <= frame.max.Y - 6 + 0.1
    hidden = build_drawing(
        part, model=model, projection=projection, projection_symbol=False, **border
    )
    # A frame can already trigger label/centreline critique. Enabling its projection
    # symbol must not add a finding; do not pretend the whole fixture was lint-clean.
    assert [(i.severity, i.code, i.message) for i in drawing.lint()] == [
        (i.severity, i.code, i.message) for i in hidden.lint()
    ]
