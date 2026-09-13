"""Independent physical sheet options preserve nominal frame and title-block geometry."""

import pytest
from build123d import Box

from draftwright import Sheet, build_drawing
from draftwright._core import _TB_LINE_WIDTH, SheetMargins
from draftwright.sheet_emit import emit_sheet_script


@pytest.mark.parametrize("page,size", [("A4", (297, 210)), ("A3", (420, 297)), ("A2", (594, 420))])
def test_sergio_sheet_options_reach_frame_block_lint_and_script(page, size):
    part = Box(20, 15, 8)
    options = dict(
        frame=True,
        zones=True,
        margin_left=25,
        margin_right=10,
        margin_top=10,
        margin_bottom=10,
        title_block_width=175,
    )
    drawing = build_drawing(part, page=page, scale=1, title="Sergio", number="1612", **options)
    assert drawing.drawable_bounds == (25, 10, size[0] - 10, size[1] - 10)
    frame = drawing.get_annotation("sheet_frame").bounding_box()
    assert (frame.min.X, frame.min.Y, frame.max.X, frame.max.Y) == pytest.approx(
        drawing.drawable_bounds
    )
    block = drawing.get_annotation("title_block")
    assert block.block_bbox["width"] == 175
    ink = block.bounding_box()
    assert ink.size.X == pytest.approx(175 + _TB_LINE_WIDTH)
    assert ink.min.X + _TB_LINE_WIDTH / 2 == pytest.approx(size[0] - 210 + 25)
    assert ink.max.X - _TB_LINE_WIDTH / 2 == pytest.approx(size[0] - 10)
    assert ink.min.Y + _TB_LINE_WIDTH / 2 == pytest.approx(10)
    assert not [
        issue for issue in drawing.lint(physical=False) if issue.code == "annotation_out_of_bounds"
    ]

    source = emit_sheet_script(
        drawing.model(),
        "part",
        "sergio",
        title="Sergio",
        number="1612",
        page=page,
        scale=1,
        **options,
    )
    namespace = {"part": part}
    body = source.replace("\npart\n", "\n", 1).split("drawing = sheet.build()", 1)[0]
    exec(compile(body, "<sergio-sheet>", "exec"), namespace)  # noqa: S102
    replay = namespace["sheet"].build()
    assert replay.drawable_bounds == drawing.drawable_bounds
    assert replay.get_annotation("title_block").block_bbox == block.block_bbox
    replay_box = replay.get_annotation("title_block").bounding_box()
    assert (
        replay_box.min.X,
        replay_box.min.Y,
        replay_box.max.X,
        replay_box.max.Y,
    ) == pytest.approx((ink.min.X, ink.min.Y, ink.max.X, ink.max.Y))


@pytest.mark.parametrize(
    "option,value",
    [
        ("margin_left", -1),
        ("margin_right", float("inf")),
        ("margin_top", float("nan")),
        ("margin_bottom", True),
        ("title_block_width", 0),
        ("title_block_width", float("inf")),
    ],
)
def test_invalid_sheet_options_are_refused_before_build(option, value):
    with pytest.raises(ValueError, match=option):
        Sheet(None, **{option: value})


def test_margins_are_independent_and_zero_is_a_valid_unframed_edge():
    margins = SheetMargins(25, 10, 7, 0)
    assert margins.bounds(210, 297) == (25, 0, 200, 290)
    assert margins.inset(6).bounds(210, 297) == (31, 6, 194, 284)
    assert margins.fits(210, 297)
    assert not margins.fits(35, 297)


@pytest.mark.parametrize("source_kind,script", [("step", False), ("step", True), ("object", True)])
def test_cli_margin_options_reach_the_built_drawing(tmp_path, monkeypatch, script, source_kind):
    from build123d import export_step
    from typer.testing import CliRunner

    from draftwright import Drawing
    from draftwright.cli import app

    if source_kind == "step":
        source = tmp_path / "part.step"
        export_step(Box(20, 15, 8), source)
        source = str(source)
    else:
        (tmp_path / "sergio_part.py").write_text(
            "from build123d import Box\npart = Box(20, 15, 8)\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        source = "sergio_part:part"
    captured = []

    def capture(drawing, *args, **kwargs):
        captured.append(drawing)
        return {"pdf": tmp_path / "sheet.pdf"}

    monkeypatch.setattr(Drawing, "export", capture)
    result = CliRunner().invoke(
        app,
        [
            source,
            "--out",
            str(tmp_path / "sheet"),
            "--page",
            "A4",
            "--scale",
            "1",
            "--frame",
            "--margin-left",
            "25",
            "--margin-right",
            "10",
            "--margin-top",
            "10",
            "--margin-bottom",
            "10",
            "--title-block-width",
            "175",
            "--title",
            "Sergio",
            "--no-report",
            *(["--script"] if script else []),
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    if script:
        path = tmp_path / "sheet.py"
        exec(compile(path.read_text(), str(path), "exec"), {})  # noqa: S102
    assert len(captured) == 1
    drawing = captured[0]
    assert drawing.drawable_bounds == (25, 10, 287, 200)
    block = drawing.get_annotation("title_block")
    assert block.block_bbox["width"] == 175
    assert block.bounding_box().min.Y + _TB_LINE_WIDTH / 2 == pytest.approx(10)


def test_title_border_stroke_allowance_still_rejects_real_overflow():
    from build123d import Pos

    from draftwright.linting.structural import lint_drawing

    drawing = build_drawing(Box(20, 15, 8), page="A4", scale=1, title_block_width=175)
    block = drawing.get_annotation("title_block")
    assert not [
        i
        for i in lint_drawing([block], page_bbox=drawing.drawable_bounds)
        if i.code == "annotation_out_of_bounds"
    ]
    shifted = block.moved(Pos(1, 0, 0))
    assert hasattr(shifted, "title_field_specs")
    assert [
        i
        for i in lint_drawing([shifted], page_bbox=drawing.drawable_bounds)
        if i.code == "annotation_out_of_bounds"
    ]


def test_explicit_page_furniture_constraints_survive_repacking():
    from types import SimpleNamespace

    from draftwright.builder import _repack_candidates
    from draftwright.compose import choose_scale

    margins = SheetMargins(25, 10, 10, 10)
    analysis = SimpleNamespace(
        title_block_width=300,
        title_block_margins=margins,
        content_margins=margins.inset(6),
        margin=16,
    )
    candidates = _repack_candidates(analysis, None, None)
    assert candidates
    assert all(width >= 335 and tb == 300 for _scale, width, _height, tb in candidates)
    assert not _repack_candidates(analysis, None, "A4")
    with pytest.raises(ValueError, match="no feasible sheet area"):
        choose_scale(
            20,
            15,
            8,
            page="A4",
            title_block_width=300,
            margin=margins,
            title_block_margins=margins,
        )
    chosen = choose_scale(
        20, 15, 8, title_block_width=300, margin=margins, title_block_margins=margins
    )
    assert chosen[1] >= 335


@pytest.mark.parametrize("margin", [0, 0.1])
def test_zone_labels_refuse_an_edge_that_cannot_hold_their_ink(margin):
    with pytest.raises(ValueError, match="margin_bottom"):
        build_drawing(Box(20, 15, 8), page="A4", scale=1, zones=True, margin_bottom=margin)
