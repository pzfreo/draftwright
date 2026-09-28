"""Isometric view placement and page-fit behavior."""

import pytest
from _kernel import B123D_GE_011, SKIP_011
from build123d import Box

from draftwright import build_drawing
from draftwright._core import _iso_bbox
from draftwright.projection import _largest_clear_factor

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
    # This checks the historical 1.3× cap; demand-guided intentionally permits 1.5×.
    dwg = build_drawing(Box(40, 30, 20), scale=1, page="A1", annotation_layout="estimated-strips")
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


@pytest.fixture(scope="module")
def ctc01_iso_case():
    """One STEPControl_Reader build shared by the CTC-01 iso collision and oracle checks."""
    from pathlib import Path

    from draftwright import builder as builder_mod

    source = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap203.stp"
    seen = []
    real = builder_mod._project_iso

    def capture(dwg, analysis, scale, *args, **kwargs):
        seen.append((dwg, analysis))
        return real(dwg, analysis, scale, *args, **kwargs)

    builder_mod._project_iso = capture
    try:
        drawing = build_drawing(source)
    finally:
        builder_mod._project_iso = real
    matching = [analysis for dwg, analysis in seen if dwg is drawing]
    assert matching, "precondition: the final CTC-01 build did not project an iso"
    return drawing, matching[-1]


def _assert_iso_search_matches_reprojection(drawing, analysis, base, lo, hi, obstacles, region):
    """Compare every bisection box with real OCC geometry, then check the chosen factor."""
    from draftwright import projection as projection_mod
    from draftwright._geometry import _boxes_overlap

    entry_scale = drawing.iso_projection_scale
    ox, oy = analysis.ISO_X, analysis.ISO_Y

    def clear(box):
        return (region is None or projection_mod._bbox_within(box, region)) and not any(
            _boxes_overlap(box, obstacle) for obstacle in obstacles
        )

    def measured(factor):
        projection_mod._project_iso(drawing, analysis, analysis.SCALE * factor)
        actual = _iso_bbox(drawing)
        ratio = factor / (entry_scale / analysis.SCALE)
        predicted = (
            ox + ratio * (base[0] - ox),
            oy + ratio * (base[1] - oy),
            ox + ratio * (base[2] - ox),
            oy + ratio * (base[3] - oy),
        )
        assert actual == pytest.approx(predicted, abs=1e-6), (
            f"CTC-01 iso bbox differs at factor {factor}: {actual} versus {predicted}"
        )
        return actual

    assert clear(base), "precondition: the measured seed already hits an obstacle"
    assert not clear(measured(hi)), "precondition: the ceiling does not exercise bisection"
    left, right = lo, hi
    for _ in range(projection_mod._ISO_CLEAR_STEPS):
        mid = (left + right) / 2
        if clear(measured(mid)):
            left = mid
        else:
            right = mid
    projection_mod._project_iso(drawing, analysis, entry_scale)
    return left


@pytest.mark.slow  # CTC fixture build (#153)
def test_the_iso_no_longer_grows_over_ctc_01s_pocket_position_dim(ctc01_iso_case):
    """The one NATURAL case in the corpus, found only by the #1240 review.

    Both hunts for a reproducing fixture reported none, and the PR said so — but they searched
    for *strip* collisions and this is the other direction: on `main`, CTC-01 AP203's iso grows
    over `m_pocket0_pos_long`'s witness lines. It escaped every sweep because
    `view_annotation_overlap` compares projected EDGES, not bboxes, so the drawing linted clean
    while the boxes genuinely overlapped (#1240 review F2).

    Asserted against the whole fixture rather than that one name: any annotation ink inside the
    final iso bbox is the defect, whichever annotation it belongs to.
    """
    from draftwright._geometry import _boxes_overlap
    from draftwright.annotations._common import annotation_obstacle_boxes

    drawing, _analysis = ctc01_iso_case
    assert "iso" in drawing.views, "precondition: the fixture has no iso view"
    iso = _iso_bbox(drawing)
    intruders = sorted(
        {
            name
            for name, obj in drawing.iter_annotations()
            if not getattr(obj, "is_sheet_frame", False)
            and not getattr(obj, "is_zone_grid", False)
            for box in annotation_obstacle_boxes(drawing, obj)
            if _boxes_overlap(box, iso)
        }
    )
    assert not intruders, f"the iso grew over placed annotation ink: {intruders}"


@pytest.mark.slow  # shares the CTC-01 STEP build above (#153)
def test_ctc01_iso_similarity_matches_search_projections_issue_1946(ctc01_iso_case, monkeypatch):
    """Real HLR boxes agree at every search factor, including the planned-detail path.

    The first corridor proves similarity on CTC-01's complex AP203 geometry. The second
    asks the actual builder to search from its 65% plan with a planted detail view in the
    growth corridor. Detail relocation is held unavailable so that corridor is exercised.
    The final real projection still belongs to the builder, not this oracle.
    """
    from draftwright import builder as builder_mod
    from draftwright import projection as projection_mod
    from draftwright._geometry import _boxes_overlap

    drawing, analysis = ctc01_iso_case
    original_scale = drawing.iso_projection_scale
    assert analysis.planned_iso_scale == pytest.approx(0.65)
    assert analysis.planned_iso_scale_authored is False

    try:
        projection_mod._project_iso(drawing, analysis, analysis.SCALE)
        base = _iso_bbox(drawing)
        projection_mod._project_iso(drawing, analysis, analysis.SCALE * 1.3)
        grown = _iso_bbox(drawing)
        assert grown[3] > base[3] + 5, "precondition: CTC-01's iso has no upward growth"
        obstacle = (base[0], (base[3] + grown[3]) / 2, base[2], grown[3])
        assert not _boxes_overlap(base, obstacle)
        projection_mod._project_iso(drawing, analysis, analysis.SCALE)
        expected = _assert_iso_search_matches_reprojection(
            drawing, analysis, base, 1.0, 1.3, [obstacle], None
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                projection_mod,
                "_project_iso",
                lambda *_args, **_kwargs: pytest.fail("clearance search reprojected CTC-01"),
            )
            chosen = _largest_clear_factor(drawing, analysis, 1.3, [obstacle], base)
        assert chosen == expected

        # Use the same real CTC-01 drawing at its actual planned seed. The planted detail
        # view contributes a fixed page-mm box through builder._settle_iso_view.
        projection_mod._project_iso(drawing, analysis, analysis.SCALE * 0.65)
        seed = _iso_bbox(drawing)
        detail = (
            analysis.ISO_X - 12,
            seed[3] + 8,
            analysis.ISO_X + 12,
            seed[3] + 11,
        )
        inflated_detail = (detail[0] - 5, detail[1] - 5, detail[2] + 5, detail[3] + 5)
        assert not _boxes_overlap(seed, inflated_detail)
        captured = []
        real_bounds = drawing.view_bounds
        real_search = builder_mod._largest_clear_factor

        def bounds(name):
            return detail if name == "detail_probe" else real_bounds(name)

        def search(dwg, selected, hi, obstacles, box, *, lo, region):
            captured.append((hi, tuple(obstacles), box, lo, region, dwg.iso_projection_scale))
            return real_search(dwg, selected, hi, obstacles, box, lo=lo, region=region)

        drawing.views["detail_probe"] = drawing.views["front"]
        try:
            with monkeypatch.context() as patch:
                patch.setattr(drawing, "view_bounds", bounds)
                patch.setattr(builder_mod, "_clear_iso_translation", lambda *_args: None)
                patch.setattr(builder_mod, "_largest_clear_factor", search)
                patch.setattr(
                    projection_mod,
                    "_project_iso",
                    lambda *_args, **_kwargs: pytest.fail("detail clearance search reprojected"),
                )
                builder_mod._settle_iso_view(drawing, analysis)
        finally:
            drawing.views.pop("detail_probe")

        assert len(captured) == 1, "precondition: the detail path did not search"
        hi, obstacles, box, lo, region, entry_scale = captured[0]
        assert lo == pytest.approx(0.65)
        assert entry_scale == pytest.approx(analysis.SCALE * 0.65)
        assert inflated_detail in obstacles, "precondition: the planted detail was not a blocker"
        assert box == pytest.approx(seed)
        projection_mod._project_iso(drawing, analysis, entry_scale)
        expected = _assert_iso_search_matches_reprojection(
            drawing, analysis, box, lo, hi, obstacles, region
        )
        without_detail = [obstacle for obstacle in obstacles if obstacle != inflated_detail]
        assert (
            _largest_clear_factor(drawing, analysis, hi, without_detail, box, lo=lo, region=region)
            > expected
        ), "precondition: another obstacle, not the detail, capped growth"
        assert (
            _largest_clear_factor(drawing, analysis, hi, obstacles, box, lo=lo, region=region)
            == expected
        )
    finally:
        projection_mod._project_iso(drawing, analysis, original_scale)
