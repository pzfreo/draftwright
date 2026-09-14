"""Public build_drawing and Drawing entry-point behavior."""

from pathlib import Path

import pytest
from build123d import Box, Cylinder
from build123d_drafting import Leader

from draftwright import Drawing, build_drawing


@pytest.mark.timeout(60)
def test_build_drawing_returns_populated_drawing(tmp_path):
    dwg = build_drawing(Box(30, 20, 10), out=str(tmp_path / "b"), title="B", number="DWG-1")
    assert isinstance(dwg, Drawing)
    assert set(dwg.views) == {"front", "plan", "side", "iso"}
    assert dwg.items, "expected automatic annotations"
    # build_drawing must not write any files — that is export()'s job.
    assert not (tmp_path / "b.svg").exists()
    assert not (tmp_path / "b.dxf").exists()


@pytest.mark.timeout(60)
def test_build_drawing_export_writes_files(tmp_path):
    stem = str(tmp_path / "b")
    dwg = build_drawing(Box(30, 20, 10), out=stem)
    _p = dwg.export(stem, formats=("svg", "dxf"))
    svg = _p["svg"]
    dxf = _p["dxf"]
    assert Path(svg).exists() and Path(dxf).exists()
    assert dwg.svg_path == svg and dwg.dxf_path == dxf


@pytest.mark.timeout(60)
def test_build_drawing_scale_and_page_override(tmp_path):
    # Issue #63 — explicit scale/page reach the Drawing instead of choose_scale's pick
    dwg = build_drawing(Box(28, 8.5, 12.5), out=str(tmp_path / "o"), scale=5, page="A3")
    assert dwg.scale == 5.0
    assert (dwg.page_w, dwg.page_h) == (420.0, 297.0)


@pytest.mark.timeout(60)
def test_build_drawing_auto_dims_false():
    # #74 — views, scale, page, and sheet furniture only; no turned-part dims.
    dwg = build_drawing(Cylinder(15, 40), auto_dims=False)
    assert set(dwg.views) == {"front", "plan", "side", "iso"}
    # Furniture the manual path shares with the auto path: the title block and — since the
    # cylinder's iso is rescaled off sheet scale — the truthful "ISO VIEW (NTS)" note. The
    # note is furniture, not a dimension, so it belongs here (script↔CLI parity); auto_dims
    # still suppresses every *dimension*.
    assert set(dwg.annotations()) == {
        "title_block",
        "note_iso_nts",
        "projection_symbol",
        "scale_note",
    }


@pytest.mark.timeout(60)
def test_clear_annotations_keeps_title_block():
    # #74 — wholesale removal without knowing the auto-name scheme.
    dwg = build_drawing(Cylinder(15, 40))  # cylinder → od dim, centerlines, …
    assert len(dwg.items) > 1
    removed = dwg._clear_annotations()
    assert removed
    assert all(a not in dwg.items for a in removed)
    assert len(dwg.items) == 1
    assert "title_block" in dwg.annotations() and len(dwg.annotations()) == 1


@pytest.mark.timeout(60)
def test_clear_annotations_keep_custom_and_unnamed_removed():
    dwg = build_drawing(Box(30, 20, 10))
    keep_me = dwg._add(
        Leader(tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label="K", draft=dwg.draft), "ldr_k"
    )
    dwg._add(Leader(tip=dwg.at("front", 0, 0, 0), elbow=(6, 6, 0), label="U", draft=dwg.draft))
    dwg._clear_annotations(keep=("title_block", "ldr_k"))
    assert set(dwg.annotations()) == {"title_block", "ldr_k"}
    assert keep_me in dwg.items
    assert len(dwg.items) == 2  # unnamed leader removed too


def test_plumbing_shims_are_deprecated():
    # #817 PR4: the 6 view/annotation plumbing methods are now engine-internal; the public
    # shims warn (and route to the private impl) for one release. Engine calls use the private
    # names directly (no warning) — covered by the ordinary build path.
    dwg = build_drawing(Box(60, 40, 20))
    coords = dwg.coords("front")
    for call in (
        lambda: dwg.clear_annotations(),
        lambda: dwg.drop_view_coordinates("nope"),
        lambda: dwg.attach_part_model(dwg.model()),
        lambda: dwg.attach_solve_trace(None),
        lambda: dwg.set_view_coordinates("front", coords),
        lambda: dwg.add_view("bottom", Box(10, 10, 10), (0, 0, -80), (0, 1, 0), (250.0, 60.0)),
    ):
        with pytest.warns(DeprecationWarning):
            call()

