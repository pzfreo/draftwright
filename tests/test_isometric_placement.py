"""Isometric view placement and page-fit behavior."""

import pytest
from _kernel import B123D_GE_011, SKIP_011
from build123d import Box

from draftwright import build_drawing

_skip_011 = pytest.mark.skipif(B123D_GE_011, reason=SKIP_011)


@pytest.fixture(scope="module")
def small_box_dwg():
    return build_drawing(Box(30, 20, 10))


@pytest.fixture(scope="module")
def ctc01_a3_drawing():
    # Fixture — NIST CTC-01-like plate at 1:5 on A3.  Module-scoped; tests must not mutate it.
    return build_drawing(Box(800, 450, 150), scale=0.2, page="A3")


@pytest.mark.timeout(120)
@_skip_011
def test_ctc01_iso_uses_upper_right_zone(ctc01_a3_drawing):
    # #75 updated — wide/flat part on A3: the iso is repositioned into the upper-right
    # zone (above the SV, right of FV/PV) where it fits at sheet scale.  No NTS label.
    from draftwright._core import _iso_bbox

    dwg = ctc01_a3_drawing
    labels = [getattr(a, "label", "") for a in dwg.items]
    assert "ISO VIEW (NTS)" not in labels  # iso now fits at sheet scale — no NTS
    x0, y0, x1, y1 = _iso_bbox(dwg)
    assert (
        x1 <= dwg.page_w - 10 + 0.5 and x0 >= 0 and y0 >= 10 - 0.5 and y1 <= dwg.page_h - 10 + 0.5
    )
    # iso must be significantly larger than the old 65 mm (shrunken) view
    assert (x1 - x0) > 100


@pytest.mark.timeout(120)
@_skip_011
def test_ctc01_iso_world_to_page_mapping(ctc01_a3_drawing):
    # dwg.at("iso", ...) must map world points to page even after the iso is
    # repositioned to the upper-right zone (still projected at sheet scale).
    dwg = ctc01_a3_drawing
    cx, cy, cz = dwg.centroid
    centre = dwg.at("iso", cx, cy, cz)
    vis, _hid = dwg.views["iso"]
    bb = vis.bounding_box()
    assert bb.min.X < centre[0] < bb.max.X and bb.min.Y < centre[1] < bb.max.Y
    iso_scale = dwg.coords("iso")._scale
    raised = dwg.at("iso", cx, cy, cz + 100)
    # Raising world Z lifts the iso page point by the foreshortened amount: the
    # vertical axis of a (1,1,1)-camera isometric projects at sqrt(2/3) (helpers
    # >=0.11 uses the real basis instead of a 1:1 collapsed mapping).
    assert raised[1] - centre[1] == pytest.approx(100 * iso_scale * (2 / 3) ** 0.5)


@pytest.mark.timeout(60)
def test_iso_view_grow_capped_at_max():
    # The iso is an orientation aid, not a measured view: fitted to a large empty
    # zone it must not balloon past _ISO_MAX_GROW × sheet scale (was ~8× before).
    from draftwright.projection import _ISO_MAX_GROW

    # Small part forced onto a big sheet → large empty rectangle → would over-grow.
    dwg = build_drawing(Box(40, 30, 20), scale=1, page="A1")
    iso_scale = dwg.coords("iso")._scale
    sheet_scale = dwg.scale
    assert iso_scale <= _ISO_MAX_GROW * sheet_scale + 1e-6
    assert iso_scale == pytest.approx(_ISO_MAX_GROW * sheet_scale, abs=1e-6)


@pytest.mark.timeout(60)
def test_iso_stays_within_page_bounds(small_box_dwg):
    # Whether scaled up or not, the iso must always lie within the page margin.
    from draftwright._core import _iso_bbox

    dwg = small_box_dwg
    x0, y0, x1, y1 = _iso_bbox(dwg)
    margin = 10
    assert x0 >= margin - 0.5
    assert y0 >= margin - 0.5
    assert x1 <= dwg.page_w - margin + 0.5
    assert y1 <= dwg.page_h - margin + 0.5


@pytest.mark.timeout(120)
def test_ctc01_iso_picks_upper_right_rectangle(ctc01_a3_drawing):
    # #11 — the general largest-empty-rectangle search must reproduce the #9
    # outcome for the wide/flat-on-A3 case: the chosen iso zone is the
    # upper-right region (right of the FV/PV column, above the SV row).
    dwg = ctc01_a3_drawing
    a = dwg._analysis
    # FV/PV occupy the left column; SV the lower-middle.  The picked rectangle
    # must sit to the right of the FV/PV column and above the SV row.
    fv_right = a.FV_X + a.fv_hw
    sv_top = a.SV_Y + a.fv_hh
    assert a.iso_left_limit >= fv_right
    assert a.iso_bottom_limit >= sv_top
    # And it reaches into the upper-right corner of the drawable area.
    assert a.iso_right_limit >= a.PAGE_W - a.margin - 0.5
    assert a.iso_top_limit >= a.PAGE_H - a.margin - 0.5
    assert a.ISO_X > a.PAGE_W / 2 and a.ISO_Y > a.PAGE_H / 2


@pytest.mark.timeout(120)
def test_tall_part_iso_in_largest_free_zone():
    # #11 — a tall/narrow part has no per-shape branch; the iso must land in the
    # largest empty rectangle, clear of every view bbox and the title block, and
    # stay within the page margins.
    from draftwright._core import _iso_bbox

    dwg = build_drawing(Box(40, 40, 300))
    a = dwg._analysis
    x0, y0, x1, y1 = _iso_bbox(dwg)
    margin = a.margin
    # Within page margins.
    assert x0 >= margin - 0.5
    assert y0 >= margin - 0.5
    assert x1 <= a.PAGE_W - margin + 0.5
    assert y1 <= a.PAGE_H - margin + 0.5

    iso_bb = (x0, y0, x1, y1)

    def overlaps(b1, b2):
        return b1[0] < b2[2] and b2[0] < b1[2] and b1[1] < b2[3] and b2[1] < b1[3]

    # No overlap with any orthographic view bounding box.
    for name in ("front", "plan", "side"):
        vis, hid = dwg.views[name]
        vb = vis.bounding_box()
        view_bb = (vb.min.X, vb.min.Y, vb.max.X, vb.max.Y)
        assert not overlaps(iso_bb, view_bb), f"iso overlaps {name} view"

    # No overlap with the title-block region (bottom-right corner).
    tb_bb = (a.PAGE_W - a.TB_W - 11, 11, a.PAGE_W - 11, 11 + 35)
    assert not overlaps(iso_bb, tb_bb), "iso overlaps title block"
