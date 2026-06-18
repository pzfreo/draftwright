"""Tests for draftwright.make_drawing."""

import math
from pathlib import Path

import pytest
from build123d import Box, Compound, Cylinder, Edge, Pos, Rotation, export_step
from build123d_drafting import HoleCallout, Leader, ViewCoordinates, view_axes

from draftwright import Drawing, build_drawing, make_drawing
from draftwright.make_drawing import (
    _MIN_VIEW_MM,
    _export_shape,
    _fits,
    _fmt,
    _is_rotational,
    analyse_cylinders,
    analyse_face_levels,
    choose_scale,
    dedup_diams,
    generate_script,
    lint_feature_coverage,
)

# ---------------------------------------------------------------------------
# Pure-function unit tests (fast, no OCP projection)
# ---------------------------------------------------------------------------


class TestFmt:
    def test_integer_value(self):
        assert _fmt(36.0) == "36"

    def test_fractional_value(self):
        assert _fmt(14.7) == "14.7"

    def test_zero(self):
        assert _fmt(0.0) == "0"

    def test_step_float_noise(self):
        # STEP-imported bounding boxes carry fp noise; near-integers must label cleanly
        assert _fmt(800.0000000000001) == "800"
        assert _fmt(-5.9999999999999) == "-6"


class TestDedupDiams:
    def test_empty(self):
        assert dedup_diams([]) == []

    def test_single(self):
        assert dedup_diams([{"diameter": 10.0, "area": 1}]) == [10.0]

    def test_deduplicates_close_values(self):
        cyls = [{"diameter": 10.0, "area": 1}, {"diameter": 10.05, "area": 1}]
        result = dedup_diams(cyls)
        assert len(result) == 1

    def test_keeps_distinct_values(self):
        cyls = [{"diameter": 10.0, "area": 1}, {"diameter": 20.0, "area": 1}]
        result = dedup_diams(cyls)
        assert len(result) == 2

    def test_sorted_descending(self):
        cyls = [
            {"diameter": 5.0, "area": 1},
            {"diameter": 20.0, "area": 1},
            {"diameter": 10.0, "area": 1},
        ]
        result = dedup_diams(cyls)
        assert result == [20.0, 10.0, 5.0]


class TestChooseScale:
    def test_tiny_part_fits_A4(self):
        # 20×20×20 mm — enlargement scales don't fit A4/A3, lands on A4 2:1
        scale, pw, ph, tbw = choose_scale(20, 20, 20)
        assert int(pw) == 297
        assert scale == 2.0

    def test_medium_part_gets_A3(self):
        # 80×80×80 mm — fits A3 1:1 because the view rows clear the title block,
        # so its width no longer forces the jump to A2 (#62)
        scale, pw, ph, tbw = choose_scale(80, 80, 80)
        assert int(pw) == 420

    def test_ctc01_sized_part_gets_A2_not_A1(self):
        # 800×450×150 mm (NIST CTC-01) — iso sits above the title block so tb_w
        # is dropped from the width constraint.  A2 fits; A1 is no longer chosen (#103).
        scale, pw, ph, tbw = choose_scale(800, 450, 150)
        assert scale == pytest.approx(0.2)
        assert int(pw) == 594  # A2 (594 mm), not A1 (841 mm)

    def test_large_part_gets_bigger_page(self):
        scale, pw, ph, tbw = choose_scale(300, 300, 300)
        assert pw > 420

    def test_returns_four_values(self):
        result = choose_scale(50, 50, 50)
        assert len(result) == 4

    def test_result_fits_on_page(self):
        # The chosen scale+page should actually fit the layout
        x, y, z = 60, 60, 15
        scale, pw, ph, tbw = choose_scale(x, y, z)
        assert _fits(x, y, z, scale, pw, ph, tbw)

    # Enlargement scales for small parts (#62)

    def test_small_part_gets_enlargement_scale(self):
        # 28 × 8.5 × 12.5 mm (issue #62 part) → enlarged, and kept on the
        # smallest sheet: 2:1 on A4, not 5:1 on A3.  The ladder is page-major,
        # so a smaller sheet is preferred over a larger enlargement scale.
        scale, pw, ph, tbw = choose_scale(28, 8.5, 12.5)
        assert scale == 2.0
        assert int(pw) == 297

    def test_very_small_part_gets_10x(self):
        scale, pw, ph, tbw = choose_scale(8, 4, 4)
        assert scale == 10.0
        assert int(pw) == 297


class TestChooseScaleOverrides:
    def test_scale_and_page_used_verbatim(self):
        assert choose_scale(28, 8.5, 12.5, scale=5, page="A3") == (5.0, 420.0, 297.0, 150.0)

    def test_scale_and_page_honoured_even_when_too_small(self):
        # Explicit overrides win even if the layout doesn't fit (warning only)
        scale, pw, ph, tbw = choose_scale(300, 300, 300, scale=1, page="A4")
        assert (scale, pw) == (1.0, 297.0)

    def test_page_only_picks_largest_fitting_scale(self):
        scale, pw, ph, tbw = choose_scale(28, 8.5, 12.5, page="A3")
        assert (pw, ph) == (420.0, 297.0)
        assert scale == 5.0

    def test_specified_page_enlarges_long_short_part_via_2d_iso(self):
        # A long, short part (100 × 10 × 11, e.g. a staircase) fills a specified
        # A3 sheet at 2:1.  The conservative row model would reject 2:1 (it
        # charges the iso a row column), but on a fixed page the iso is packed
        # into vertical headroom, so the larger scale genuinely fits (#staircase).
        from draftwright.make_drawing import _fits

        assert not _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0)
        assert _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0, pack_iso_2d=True)
        scale, pw, ph, _ = choose_scale(100, 10, 11, page="A3")
        assert scale == 2.0
        assert (pw, ph) == (420.0, 297.0)
        # Automatic selection (no page) stays conservative — A4 at 1:1.
        assert choose_scale(100, 10, 11)[:3] == (1.0, 297.0, 210.0)

    def test_scale_only_picks_smallest_fitting_page(self):
        scale, pw, ph, tbw = choose_scale(28, 8.5, 12.5, scale=2)
        assert scale == 2.0
        assert int(pw) == 297

    def test_scale_only_enlarges_long_short_part_via_2d_iso(self):
        # Fixed scale, no page: choose_scale walks the page list with
        # pack_iso_2d=True, so a long/short part keeps the requested 2:1 by
        # packing the iso into vertical headroom.  At 2:1 the part overruns A4
        # but fits A3; the conservative row model would have rejected A3 too.
        from draftwright.make_drawing import _fits

        assert not _fits(100, 10, 11, 2.0, 297.0, 210.0, 120.0, pack_iso_2d=True)
        assert _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0, pack_iso_2d=True)
        assert not _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0)
        assert choose_scale(100, 10, 11, scale=2) == (2.0, 420.0, 297.0, 150.0)

    def test_page_tuple(self):
        scale, pw, ph, tbw = choose_scale(10, 10, 10, page=(420, 297))
        assert (pw, ph, tbw) == (420.0, 297.0, 150.0)

    def test_page_wxh_string(self):
        scale, pw, ph, tbw = choose_scale(10, 10, 10, page="420x297")
        assert (pw, ph) == (420.0, 297.0)

    def test_page_name_case_insensitive(self):
        scale, pw, ph, tbw = choose_scale(10, 10, 10, page="a3")
        assert (pw, ph) == (420.0, 297.0)

    def test_unknown_page_raises(self):
        with pytest.raises(ValueError, match="page size"):
            choose_scale(10, 10, 10, page="B5")

    def test_nonpositive_scale_raises(self):
        with pytest.raises(ValueError, match="scale"):
            choose_scale(10, 10, 10, scale=0)


class TestIsoEmptyRect:
    def test_largest_empty_rect_fallback_when_fully_covered(self):
        # When obstacles leave no genuine gap, _largest_empty_rect returns the
        # whole drawable (documented fallback) — the mechanism iso_valid checks.
        from draftwright.make_drawing import _largest_empty_rect

        drawable = (10.0, 10.0, 90.0, 90.0)
        assert _largest_empty_rect(drawable, [drawable]) == drawable

    def test_layout_geometry_iso_valid_false_when_no_gap(self):
        # A part that fills the sheet leaves no empty rectangle for the iso, so
        # the fallback returns the drawable (overlapping the view obstacles) and
        # iso_valid is False — the flag _fits uses to reject such a layout.
        from draftwright.make_drawing import _layout_geometry

        g = _layout_geometry(200, 150, 150, 2.0, 297.0, 210.0, 120.0, None)
        assert g.iso_valid is False

    def test_layout_geometry_iso_valid_true_for_normal_part(self):
        from draftwright.make_drawing import _layout_geometry

        g = _layout_geometry(20, 20, 20, 1.0, 297.0, 210.0, 120.0, None)
        assert g.iso_valid is True


class TestScaleMinimum:
    """Scale too small → ValueError before OCCT degenerates (#129)."""

    def test_tiny_scale_raises(self, tmp_path):
        # 80 mm thin part at scale=0.1 → 8 mm projection < _MIN_VIEW_MM
        part = Box(680, 860, 80)
        with pytest.raises(ValueError, match="annotation geometry degenerates"):
            make_drawing(part, out=str(tmp_path / "out"), scale=0.1)

    def test_error_message_suggests_safe_scale(self, tmp_path):
        part = Box(680, 860, 80)
        with pytest.raises(ValueError) as exc:
            make_drawing(part, out=str(tmp_path / "out"), scale=0.1)
        msg = str(exc.value)
        assert "scale" in msg.lower()
        # Should mention the minimum safe scale (≥ 10/80 = 0.125)
        import re

        nums = re.findall(r"\d+\.?\d*", msg)
        safe_scales = [float(n) for n in nums if 0.1 < float(n) < 1.0]
        assert any(s >= _MIN_VIEW_MM / 80 for s in safe_scales)

    def test_safe_scale_does_not_raise(self, tmp_path):
        # 0.2 → 80*0.2 = 16 mm > _MIN_VIEW_MM
        part = Box(680, 860, 80)
        result = make_drawing(part, out=str(tmp_path / "out"), scale=0.2)
        assert result is not None

    def test_auto_scale_thin_part_does_not_raise(self, tmp_path):
        # Auto-selected scale for a thin plate must not trigger the SIGABRT guard.
        part = Box(80, 50, 8)
        result = make_drawing(part, out=str(tmp_path / "out"))
        assert result is not None


class TestSectionHatchEdges:
    """Unit tests for _section_hatch_edges even-odd fill algorithm."""

    def test_rectangle_hatch_line_through_corner_fills_interior(self):
        # A 45° hatch line passing exactly through a corner vertex must not
        # produce an odd-length hits list — the span must still be drawn.
        # Face.make_rect(10, 5, Plane.XZ) gives corners at X∈[-5,5], Z∈[-2.5,2.5].
        # With spacing=5, c=0 gives hatch line through corner (-5,-2.5).
        from build123d import Face, Plane

        from draftwright.make_drawing import _section_hatch_edges

        face = Face.make_rect(10, 5, Plane.XZ)
        edges = _section_hatch_edges(face, lambda x: x, lambda z: z, spacing=5.0)
        assert len(edges) > 0, "corner vertex hit must not suppress all hatch spans"
        for e in edges:
            p0, p1 = e.position_at(0), e.position_at(1)
            assert p1.X - p0.X > 0.1, f"zero-length hatch span dx={p1.X - p0.X}"

    def test_hatch_edges_are_45_degrees(self):
        from build123d import Face, Plane

        from draftwright.make_drawing import _section_hatch_edges

        face = Face.make_rect(20, 15, Plane.XZ)
        edges = _section_hatch_edges(face, lambda x: x, lambda z: z, spacing=4.5)
        assert len(edges) > 0
        for e in edges:
            p0, p1 = e.position_at(0), e.position_at(1)
            dx, dy = p1.X - p0.X, p1.Y - p0.Y
            assert abs(dy / dx - 1.0) < 0.01, f"hatch not at 45°: slope={dy / dx}"


class TestStripZones:
    """Unit tests for the Strip / ViewZones layout primitives (issue #105)."""

    def test_strip_import(self):
        from draftwright.make_drawing import Strip, ViewZones  # noqa: F401

    def test_outward_strip_allocates_and_advances(self):
        from draftwright.make_drawing import Strip

        s = Strip(anchor=100.0, outer_limit=200.0, direction=1, gap=8.0, spacing=4.0)
        pos = s.allocate(10.0)
        assert pos == pytest.approx(108.0)  # anchor + gap
        pos2 = s.allocate(10.0)
        assert pos2 == pytest.approx(122.0)  # 108 + 10 + 4

    def test_inward_strip_allocates_and_retreats(self):
        from draftwright.make_drawing import Strip

        s = Strip(anchor=100.0, outer_limit=0.0, direction=-1, gap=8.0, spacing=4.0)
        pos = s.allocate(10.0)
        assert pos == pytest.approx(92.0)  # anchor - gap (near edge of first slot)
        pos2 = s.allocate(10.0)
        assert pos2 == pytest.approx(78.0)  # 92 - 10 - 4

    def test_strip_returns_none_when_full(self):
        from draftwright.make_drawing import Strip

        s = Strip(anchor=0.0, outer_limit=20.0, direction=1, gap=2.0, spacing=2.0)
        assert s.allocate(10.0) is not None  # fits: 2..12
        assert s.allocate(10.0) is None  # would need 14..24, over limit=20

    def test_strip_available(self):
        from draftwright.make_drawing import Strip

        s = Strip(anchor=50.0, outer_limit=150.0, direction=1)
        assert s.available == pytest.approx(100.0)

    def test_strip_depth_used(self):
        from draftwright.make_drawing import Strip

        s = Strip(anchor=100.0, outer_limit=200.0, direction=1, gap=8.0, spacing=4.0)
        s.allocate(10.0)
        # cursor is now at 108 + 10 + 4 = 122; depth_used = 122 - 100 = 22
        assert s.depth_used == pytest.approx(22.0)

    def test_analyse_returns_view_zones(self):
        from build123d import Box, Cylinder

        from draftwright import build_drawing
        from draftwright.make_drawing import Strip, ViewZones

        part = Box(80, 60, 20) - Cylinder(5, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        assert isinstance(a.fv_zones, ViewZones)
        assert isinstance(a.pv_zones, ViewZones)
        assert isinstance(a.sv_zones, ViewZones)
        assert isinstance(a.fv_zones.right, Strip)
        assert isinstance(a.pv_zones.above, Strip)
        assert isinstance(a.pv_zones.below, Strip)  # dim_width goes here
        assert a.sv_zones.left is None  # abuts front view

    def test_strip_limits_are_within_page(self):
        from build123d import Box, Cylinder

        from draftwright import build_drawing

        part = Box(80, 60, 20) - Cylinder(5, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        margin = a.margin
        # Outer limits should be within the page
        assert a.fv_zones.right.outer_limit <= a.PAGE_W
        assert a.pv_zones.above.outer_limit <= a.PAGE_H
        assert a.fv_zones.left.outer_limit >= margin

    def test_dim_height_routed_through_fv_right_strip(self):
        # dim_height must be placed via the strip; its dimension line must
        # land within the fv_zones.right corridor (anchor..outer_limit).
        from build123d import Box

        from draftwright import build_drawing

        part = Box(60, 40, 30)
        dwg = build_drawing(part)
        a = dwg._analysis
        assert "dim_height" in dwg._named
        ann = dwg._named["dim_height"]
        # The strip consumed at least one slot: cursor has advanced past anchor+gap
        assert a.fv_zones.right.depth_used > 0
        # label is the part height
        assert ann.label == "30"

    def test_pv_below_strip_is_now_active(self):
        # pv_zones.below should be a Strip (not None) after Phase 3
        from build123d import Box

        from draftwright import build_drawing
        from draftwright.make_drawing import Strip

        part = Box(80, 60, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        assert isinstance(a.pv_zones.below, Strip)
        assert a.pv_zones.below.direction == -1
        # The outer_limit must be above the front view top edge (fv_hh from FV_Y)
        assert a.pv_zones.below.outer_limit < a.pv_zones.below.anchor

    def test_dim_width_routed_through_pv_below_strip(self):
        # dim_width must exist below the plan view, with depth_used > 0
        from build123d import Box

        from draftwright import build_drawing

        # non-square part → x_size != y_size → dim_width should appear
        part = Box(80, 40, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        assert "dim_width" in dwg._named
        ann = dwg._named["dim_width"]
        assert ann.label == "80"
        assert a.pv_zones.below.depth_used > 0

    def test_dim_locx_routed_through_pv_above_strip(self):
        # dim_locx dims must be above plan_top and allocated from pv_zones.above
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        part = Box(80, 60, 20) - Pos(20, 10, 0) * Cylinder(5, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        locx_dims = [v for n, v in dwg._named.items() if n.startswith("dim_locx")]
        assert len(locx_dims) >= 1, "expected dim_locx0 to be generated for off-datum cylinder"
        plan_top = dwg.views["plan"][0].bounding_box().max.Y
        assert all(d.dim_level_y > plan_top for d in locx_dims)
        assert a.pv_zones.above.depth_used > 0

    def test_dim_locy_routed_through_sv_above_strip(self):
        # dim_locy dims must be above side_top and allocated from sv_zones.above
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        # Cylinder at Y=10 → offset from datum_y=bb.min.Y → generates dim_locy0
        part = Box(80, 60, 20) - Pos(0, 10, 0) * Cylinder(5, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        locy_dims = [v for n, v in dwg._named.items() if n.startswith("dim_locy")]
        assert len(locy_dims) >= 1, "expected dim_locy0 to be generated for off-datum cylinder"
        side_top = dwg.views["side"][0].bounding_box().max.Y
        assert all(d.dim_level_y > side_top for d in locy_dims)
        assert a.sv_zones.above.depth_used > 0

    def test_dim_step_placed_after_phase3_corridor_widening(self):
        # Phase 3 widens fv_zones.right dynamically for stepped parts.
        # A part with one step face gets gap_fv_sv = 36 mm (vs 18 mm fixed),
        # which is enough for dim_height (10 mm) + spacing (4 mm) + dim_step (14 mm).
        # Both annotations must now appear without overlapping the side view.
        from build123d import Box, Pos

        from draftwright import build_drawing
        from draftwright.make_drawing import _est_right_strip_depth

        part = Box(40, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        # dim_height and at least one dim_step must be generated
        assert "dim_height" in dwg._named
        step_dims = [n for n in dwg._named if n.startswith("dim_step")]
        assert len(step_dims) >= 1, "dim_step must appear after Phase 3 corridor widening"
        # The FV→SV gap must equal the estimator value for the height-gated count.
        # Use the same gate _analyse() applies: (z - bb.min.Z) * SCALE >= 20.
        # a.step_zs is the raw (ungated) list; using len(a.step_zs) would give the
        # wrong expected gap for parts with shallow step faces.
        n = len([z for z in a.step_zs[:3] if (z - a.bb.min.Z) * a.SCALE >= 20])
        expected_gap = _est_right_strip_depth(n)
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        assert sv_left - fv_right == pytest.approx(expected_gap, abs=0.1)
        # Annotations must not enter the side view geometry
        assert sv_left - fv_right > 0

    def test_fv_right_outer_limit_does_not_enter_side_view(self):
        # Phase 1: fv_zones.right outer_limit must be <= the side view left edge.
        # Previously it was iso_right_limit (far right of page), causing dim_step
        # annotations to be placed inside the side view geometry.
        # pv_zones.right is intentionally unrestricted — hole callouts for the
        # plan view go to the right of the plan/side pair (different Y band) and
        # need the full iso-bounded corridor.
        from build123d import Box

        from draftwright import build_drawing

        part = Box(80, 60, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        sv_left = a.SV_X - a.sv_hw
        assert a.fv_zones.right.outer_limit <= sv_left + 0.5

    def test_dim_height_still_placed_after_outer_limit_fix(self):
        # Phase 1: dim_height must still be generated — it fits in the 18 mm
        # corridor (gap=8 + slot=10 = 18 mm exactly).
        from build123d import Box

        from draftwright import build_drawing

        part = Box(60, 40, 30)
        dwg = build_drawing(part)
        assert "dim_height" in dwg._named
        assert dwg._named["dim_height"].label == "30"

    def test_overall_height_dim_sits_outside_step_dims(self):
        # staircase.step review: the overall-height dimension must nest OUTSIDE
        # the step-height dims (placed last so it is outermost), so extension
        # lines nest rather than leapfrog. A stepped part exercises both.
        from build123d import Box, Pos

        from draftwright import build_drawing

        part = (
            Box(40, 12, 60) - Pos(10, 0, 30) * Box(20, 12, 30) - Pos(-10, 0, 40) * Box(20, 12, 20)
        )
        dwg = build_drawing(part)
        assert "dim_height" in dwg._named
        step_dims = [n for n in dwg._named if n.startswith("dim_step")]
        assert step_dims, "expected at least one step dim"
        height_x = dwg._named["dim_height"].bounding_box().max.X
        for n in step_dims:
            step_x = dwg._named[n].bounding_box().max.X
            assert height_x > step_x, f"overall height must sit outside {n}"

    def test_right_strip_outer_limits_tightened_to_iso(self):
        # fv.right and pv.right are both bounded by sv_left_edge so bore callout
        # labels cannot cross into the side view.  The sv.right strip is only
        # iso-tightened (to iso_x0 - 4) when the iso shares the side view's
        # y-range; with the #11 free-rectangle placement the iso may instead sit
        # above the side view, in which case sv.right keeps its full width.
        # Use a plain box (no holes) so bore callout overhead doesn't push the
        # iso view right and interfere with the sv tightening check.
        from build123d import Box

        from draftwright import build_drawing
        from draftwright.make_drawing import _iso_bbox

        part = Box(80, 60, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        sv_left = a.SV_X - a.sv_hw
        iso_x0, iso_y0, _, iso_y1 = _iso_bbox(dwg)
        iso_limit = iso_x0 - 4
        # fv right must not extend past the side view left edge
        assert a.fv_zones.right.outer_limit == pytest.approx(sv_left, abs=0.1)
        # pv right is also bounded by sv_left so bore callout labels cannot
        # cross dim_locy extension lines in the side view corridor
        assert a.pv_zones.right.outer_limit == pytest.approx(sv_left, abs=0.1)
        # sv right strip is iso-tightened only when the iso overlaps its y-range.
        sv_y0, sv_y1 = a.SV_Y - a.fv_hh, a.SV_Y + a.fv_hh
        if sv_y0 < iso_y1 and iso_y0 < sv_y1:
            assert a.sv_zones.right.outer_limit == pytest.approx(iso_limit, abs=0.1)
        else:
            assert a.sv_zones.right.outer_limit > iso_limit

    def test_sv_zones_below_strip_is_active(self):
        # sv_zones.below must be a Strip (not None) after _analyse().
        from build123d import Box

        from draftwright import build_drawing

        part = Box(80, 60, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        assert a.sv_zones.below is not None, "sv_zones.below should be a Strip"

    def test_dim_depth_routed_through_sv_zones_below(self):
        # dim_depth (Y envelope) must be placed below side_top via sv_zones.below.
        # Uses a part where x_size != y_size by > 5% to trigger the annotation.
        from build123d import Box

        from draftwright import build_drawing

        # 80×40×20 box: x_size=80, y_size=40 — differ by > 5%, so dim_depth fires
        part = Box(80, 40, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        assert "dim_depth" in dwg._named, "expected dim_depth for part with x_size != y_size"
        ann = dwg._named["dim_depth"]
        assert ann.label == "40", f"dim_depth label should be y_size=40, got {ann.label!r}"
        assert a.sv_zones.below.depth_used > 0

    def test_dim_depth_absent_for_square_plan(self):
        # dim_depth must be omitted when x_size == y_size (within 5%).
        from build123d import Box

        from draftwright import build_drawing

        part = Box(60, 60, 20)  # square plan: x_size == y_size
        dwg = build_drawing(part)
        assert "dim_depth" not in dwg._named, "dim_depth should be skipped for square plan"


# ---------------------------------------------------------------------------
# Phase 2 annotation depth estimators (#118)
# ---------------------------------------------------------------------------


class TestDepthEstimators:
    """Pure-function tests for _est_right_strip_depth / _est_pv_below_depth."""

    def test_right_depth_no_steps_equals_dim_pad(self):
        from draftwright.make_drawing import _DIM_PAD, _est_right_strip_depth

        # 0 steps → dim_height only → gap(8) + slot(10) = 18 = _DIM_PAD
        assert _est_right_strip_depth(0) == pytest.approx(_DIM_PAD, abs=0.01)

    def test_right_depth_one_step(self):
        from draftwright.make_drawing import _est_right_strip_depth

        # dim_height (10) + spacing (4) + 1×dim_step (14) = 8 + 10 + 4 + 14 = 36
        assert _est_right_strip_depth(1) == pytest.approx(36.0, abs=0.01)

    def test_right_depth_three_steps(self):
        from draftwright.make_drawing import _est_right_strip_depth

        # dim_height (10) + 3×dim_step (14 each) + 3×spacing (4 each) = 8+10+4+14+4+14+4+14 = 72
        assert _est_right_strip_depth(3) == pytest.approx(72.0, abs=0.01)

    def test_right_depth_grows_per_step_uncapped(self):
        from draftwright.make_drawing import (
            _SLOT_DIM_STEP,
            _STRIP_SPACING,
            _est_right_strip_depth,
        )

        # #36: no cap — each further step adds one slot + one spacing.
        assert _est_right_strip_depth(10) > _est_right_strip_depth(3)
        assert _est_right_strip_depth(10) - _est_right_strip_depth(3) == pytest.approx(
            7 * (_STRIP_SPACING + _SLOT_DIM_STEP), abs=0.01
        )

    def test_right_depth_increases_with_steps(self):
        from draftwright.make_drawing import _est_right_strip_depth

        assert _est_right_strip_depth(0) < _est_right_strip_depth(1) < _est_right_strip_depth(3)

    def test_pv_below_depth(self):
        from draftwright.make_drawing import _est_pv_below_depth

        # gap(8) + dim_width slot(8) = 16
        assert _est_pv_below_depth() == pytest.approx(16.0, abs=0.01)

    def test_right_depth_fits_in_exact_corridor(self):
        # A Strip whose available width equals _est_right_strip_depth(n) must
        # accept exactly n+1 allocations (dim_height + n dim_steps).
        from draftwright.make_drawing import (
            _SLOT_DIM_HEIGHT,
            _SLOT_DIM_STEP,
            _STRIP_GAP,
            Strip,
            _est_right_strip_depth,
        )

        for n_steps in (0, 1, 3):
            est = _est_right_strip_depth(n_steps)
            s = Strip(anchor=0.0, outer_limit=est, direction=1, gap=_STRIP_GAP)
            assert s.allocate(_SLOT_DIM_HEIGHT) is not None, (
                f"dim_height must fit for n_steps={n_steps}"
            )
            for i in range(n_steps):
                assert s.allocate(_SLOT_DIM_STEP) is not None, (
                    f"dim_step_{i} must fit for n_steps={n_steps}"
                )

    def test_pv_below_depth_fits_in_exact_corridor(self):
        # A Strip of _est_pv_below_depth() width must accept one dim_width allocation.
        from draftwright.make_drawing import (
            _SLOT_DIM_WIDTH,
            _STRIP_GAP,
            Strip,
            _est_pv_below_depth,
        )

        est = _est_pv_below_depth()
        s = Strip(anchor=100.0, outer_limit=100.0 - est, direction=-1, gap=_STRIP_GAP)
        assert s.allocate(_SLOT_DIM_WIDTH) is not None, "dim_width must fit in pv_below corridor"


# ---------------------------------------------------------------------------
# #31: layout constants derived from text metrics
# ---------------------------------------------------------------------------


class TestDerivedLayoutConstants:
    """Slots / callout widths / iso budget derive from text metrics, not bare mm."""

    def test_slots_derive_from_font_metrics(self):
        from draftwright.make_drawing import (
            _FONT_SIZE,
            _PAD,
            _SLOT_DIM_DEPTH,
            _SLOT_DIM_HEIGHT,
            _SLOT_DIM_STEP,
            _SLOT_DIM_WIDTH,
        )

        assert _SLOT_DIM_WIDTH == pytest.approx(2 * _FONT_SIZE + _PAD)
        assert _SLOT_DIM_DEPTH == pytest.approx(2 * _FONT_SIZE + _PAD)
        assert _SLOT_DIM_HEIGHT == pytest.approx(2 * _FONT_SIZE + 2 * _PAD)
        assert _SLOT_DIM_STEP == pytest.approx(4 * _FONT_SIZE + _PAD)
        # The slots are linear in font metrics — a hypothetical larger font
        # would yield larger slots — so they are not frozen mm constants.
        assert (2 * (2 * _FONT_SIZE) + 2 * _PAD) > _SLOT_DIM_HEIGHT

    def test_text_width_returns_real_glyph_metrics(self):
        from draftwright.make_drawing import _text_width

        assert _text_width("", 3.0) == 0.0
        # A real measurement is positive and grows with the string.
        w1 = _text_width("8", 3.0)
        w3 = _text_width("888", 3.0)
        assert 0.0 < w1 < w3
        # Wider glyphs (uppercase) measure wider than the old 0.6*font fudge
        # would have estimated — the whole point of using real metrics (#31).
        assert _text_width("THRU", 3.0) > 4 * 0.6 * 3.0

    def test_bore_callout_width_scales_with_font_size(self):
        from draftwright.make_drawing import _est_bore_callout_width, find_holes

        part = Box(60, 40, 12) - Pos(0, 0, 6) * Cylinder(3, 12)
        holes = find_holes(part)
        small = _est_bore_callout_width(holes, font_size=3.0)
        large = _est_bore_callout_width(holes, font_size=6.0)
        assert large > small


# ---------------------------------------------------------------------------
# Phase 3 (#118): dynamic FV→SV corridor
# ---------------------------------------------------------------------------


class TestDynamicCorridors:
    """Phase 3 (#118): SV_X and _fits() use the depth estimator for the FV→SV gap."""

    def test_fits_widens_required_space_for_stepped_part(self):
        # x=5, y=100, z=100 at 1:1 on A3 (420×297, tb=150):
        #   n_steps=0 (gap=18): w=417 ≤ 420 → direct fit
        #   n_steps=3 (gap=72): w=471 > 420; views_bottom=39.5 < 45 so
        #     the iso-over-title-block fallback cannot apply → False
        from draftwright.make_drawing import _fits

        assert _fits(5.0, 100.0, 100.0, 1.0, 420.0, 297.0, 150.0, n_steps=0)
        assert not _fits(5.0, 100.0, 100.0, 1.0, 420.0, 297.0, 150.0, n_steps=3)

    def test_fits_zero_steps_same_as_default(self):
        # n_steps=0 must produce the same result as the old signature (no kwarg).
        from draftwright.make_drawing import _fits

        page_w, page_h, tb = 297.0, 210.0, 120.0
        scale, x_size, y_size, z_size = 1.0, 20.0, 20.0, 20.0
        assert _fits(x_size, y_size, z_size, scale, page_w, page_h, tb, n_steps=0) == _fits(
            x_size, y_size, z_size, scale, page_w, page_h, tb
        )

    def test_gap_fv_sv_equals_dim_pad_for_flat_part(self):
        # A plain box (no step faces) → sv_left - fv_right == _DIM_PAD.
        from build123d import Box

        from draftwright import build_drawing
        from draftwright.make_drawing import _DIM_PAD

        a = build_drawing(Box(60, 40, 20))._analysis
        assert len(a.step_zs) == 0
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        assert sv_left - fv_right == pytest.approx(_DIM_PAD, abs=0.1)

    def test_choose_scale_picks_larger_page_for_deep_step_corridor(self):
        # With n_steps=0, x=5 y=z=100 fits A3 at 1:1 (420 mm wide).
        # With n_steps=3, gap_fv_sv jumps to 72 mm — A3 no longer fits and
        # choose_scale must return A2.  This verifies that the conservative
        # n_steps_ub path in _analyse() ensures the page is never too small.
        from draftwright.make_drawing import choose_scale

        _, page_w_flat, _, _ = choose_scale(5.0, 100.0, 100.0, n_steps=0)
        _, page_w_deep, _, _ = choose_scale(5.0, 100.0, 100.0, n_steps=3)
        assert page_w_deep > page_w_flat, (
            "n_steps=3 corridor must force a larger page than n_steps=0"
        )

    def test_gap_fv_sv_widens_for_stepped_part(self):
        # A part with one step ≥20 mm tall (so dim_step is actually placed) gets
        # gap = _est_right_strip_depth(1) = 36 mm.  The ≥20 mm gate matches what
        # _auto_annotate applies — bore floors or shallow faces don't count.
        from build123d import Box, Pos

        from draftwright import build_drawing
        from draftwright.make_drawing import _est_right_strip_depth

        # Box(60, 40, 50): Z -25..+25.  Carve top-right quadrant so the step
        # floor is at Z=0, giving a 25 mm step height (≥20 mm threshold).
        cutout = Pos(15, 0, 12.5) * Box(30, 40, 25)
        part = Box(60, 40, 50) - cutout

        a = build_drawing(part)._analysis
        assert len(a.step_zs) >= 1, "expected at least one step face"
        # The 25 mm step height passes the dim_step ≥20 mm gate → n_steps=1 → gap=36 mm
        expected_gap = _est_right_strip_depth(1)
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        assert sv_left - fv_right == pytest.approx(expected_gap, abs=0.1)


# ---------------------------------------------------------------------------
# Two-pass layout (#131): bore callout width drives gap_fv_sv
# ---------------------------------------------------------------------------


class TestTwoPassLayout:
    """Two-pass layout (#131): bore callout widths widen the FV→SV corridor."""

    def test_bore_callout_widens_gap_fv_sv(self):
        # A part with many small holes generates wide callout labels (e.g.
        # "4× ⌀15.9 THRU") that need more than _DIM_PAD right of the plan view.
        # The two-pass layout must size gap_fv_sv >= bore callout depth.
        from build123d import Box, Cylinder, Pos
        from build123d_drafting.features import find_holes

        from draftwright import build_drawing
        from draftwright.make_drawing import _DIM_PAD, _est_bore_callout_width

        # Four identical cylinders → "4× ⌀16 THRU" callout with a count prefix
        part = (
            Box(100, 80, 20)
            - Pos(30, 25, 0) * Cylinder(16, 20)
            - Pos(-30, 25, 0) * Cylinder(16, 20)
            - Pos(30, -25, 0) * Cylinder(16, 20)
            - Pos(-30, -25, 0) * Cylinder(16, 20)
        )
        dwg = build_drawing(part)
        a = dwg._analysis
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        actual_gap = sv_left - fv_right

        holes = find_holes(part)
        bore_depth = _est_bore_callout_width(holes)
        # bore callout width must exceed DIM_PAD for the test to be meaningful
        assert bore_depth > _DIM_PAD, (
            f"bore callout width {bore_depth:.1f} mm must exceed _DIM_PAD={_DIM_PAD} mm"
        )
        assert actual_gap >= bore_depth - 0.1, (
            f"gap_fv_sv={actual_gap:.1f} mm must be >= bore_depth={bore_depth:.1f} mm"
        )

    def test_plain_box_gap_unchanged(self):
        # A box with no holes: bore callout depth = 0 → gap_fv_sv stays _DIM_PAD.
        from build123d import Box

        from draftwright import build_drawing
        from draftwright.make_drawing import _DIM_PAD

        a = build_drawing(Box(60, 40, 20))._analysis
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        assert sv_left - fv_right == pytest.approx(_DIM_PAD, abs=0.1)

    def test_bore_callout_fits_within_gap(self):
        # Verify actual callout label does not reach sv_left.
        # The Leader label_bbox right edge must stay left of sv_left.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        part = (
            Box(100, 80, 20)
            - Pos(30, 25, 0) * Cylinder(16, 20)
            - Pos(-30, 25, 0) * Cylinder(16, 20)
            - Pos(30, -25, 0) * Cylinder(16, 20)
            - Pos(-30, -25, 0) * Cylinder(16, 20)
        )
        dwg = build_drawing(part)
        a = dwg._analysis
        sv_left = a.SV_X - a.sv_hw
        for name, ann in dwg._named.items():
            if name.startswith("hc_plan") and getattr(ann, "label_bbox", None):
                lx1 = ann.label_bbox[2]  # right edge of callout label
                assert lx1 <= sv_left + 0.5, (
                    f"{name}: label right edge {lx1:.1f} mm exceeds sv_left {sv_left:.1f} mm"
                )

    def test_bolt_circle_suffix_widens_estimate(self):
        # BoltCircle callouts carry "EQ SP ON ø… BC" suffix (~34 mm wide).
        # _est_bore_callout_width must include it when patterns are provided.
        from build123d import Box, Cylinder, Pos
        from build123d_drafting.features import find_hole_patterns, find_holes

        from draftwright.make_drawing import _est_bore_callout_width

        # Six ⌀8 holes at equal 60° spacing on R=35 → BoltCircle pattern
        part = (
            Box(100, 100, 20)
            - Pos(35.0, 0.0, 0) * Cylinder(8, 20)
            - Pos(17.5, 30.31, 0) * Cylinder(8, 20)
            - Pos(-17.5, 30.31, 0) * Cylinder(8, 20)
            - Pos(-35.0, 0.0, 0) * Cylinder(8, 20)
            - Pos(-17.5, -30.31, 0) * Cylinder(8, 20)
            - Pos(17.5, -30.31, 0) * Cylinder(8, 20)
        )
        holes = find_holes(part)
        patterns = find_hole_patterns(holes)

        width_without = _est_bore_callout_width(holes)
        width_with = _est_bore_callout_width(holes, patterns=patterns)
        assert width_with > width_without, (
            f"BoltCircle suffix should widen estimate: {width_without:.1f} → {width_with:.1f} mm"
        )

    def test_pv_below_strip_has_slack(self):
        # pv_zones.below outer_limit = fv_top_edge (not fv_top_edge + 2), giving
        # 18 mm available vs 16 mm needed for dim_width — no razor-fit (#130).
        from build123d import Box

        from draftwright import build_drawing
        from draftwright.make_drawing import _est_pv_below_depth

        part = Box(80, 40, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        available = a.pv_zones.below.anchor - a.pv_zones.below.outer_limit
        needed = _est_pv_below_depth()
        assert available > needed, (
            f"pv_zones.below available {available:.1f} mm must exceed needed {needed:.1f} mm"
        )
        assert "dim_width" in dwg._named, "dim_width must not be skipped"


# ---------------------------------------------------------------------------
# Integration test — requires build123d + OCP (slow)
# ---------------------------------------------------------------------------


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
def test_make_drawing_cylinder_uses_centerline_and_holecallout(tmp_path):
    """make_drawing() adds Centerline and HoleCallout for cylindrical parts."""
    cyl = Cylinder(radius=15, height=40)
    step_file = str(tmp_path / "cyl.step")
    export_step(cyl, step_file)

    svg_path, _ = make_drawing(step_file, out=str(tmp_path / "cyl_drawing"), title="CYL")

    # The drawing must exist and be non-trivial
    assert Path(svg_path).exists()
    assert Path(svg_path).stat().st_size > 1000


@pytest.mark.timeout(120)
def test_make_drawing_default_title(tmp_path):
    """Title defaults to uppercased stem when not provided."""
    box = Box(10, 10, 10)
    step_file = str(tmp_path / "my_part.step")
    export_step(box, step_file)

    svg_path, _ = make_drawing(step_file, out=str(tmp_path / "out"))
    assert Path(svg_path).exists()


@pytest.mark.timeout(120)
def test_make_drawing_accepts_build123d_object(tmp_path):
    """make_drawing() draws an in-memory build123d Shape without a STEP file."""
    box = Box(30, 20, 10)
    out_stem = str(tmp_path / "box_obj")

    svg_path, dxf_path = make_drawing(box, out=out_stem, title="BOX OBJ")

    assert Path(svg_path).exists()
    assert Path(dxf_path).exists()
    assert Path(svg_path).stat().st_size > 1000


@pytest.mark.timeout(120)
def test_make_drawing_object_defaults_out_to_drawing(tmp_path, monkeypatch):
    """Passing an object with no out= writes to 'drawing.svg' in the cwd."""
    monkeypatch.chdir(tmp_path)
    box = Box(10, 10, 10)

    svg_path, dxf_path = make_drawing(box)

    assert Path(svg_path).name == "drawing.svg"
    assert Path(dxf_path).name == "drawing.dxf"
    assert (tmp_path / "drawing.svg").exists()


def test_generate_script_rejects_build123d_object():
    """generate_script() needs a path — a live object cannot be embedded."""
    box = Box(10, 10, 10)
    with pytest.raises(TypeError, match="STEP file path"):
        generate_script(box)


# ---------------------------------------------------------------------------
# ViewCoordinates (pure-Python, no OCP needed)
# ---------------------------------------------------------------------------


class TestViewCoordinates:
    def _front_vc(self):
        # Front view: camera at (0, -100, 0), up=(0,0,1), look_at=(0,0,0)
        # → world X → page_X (+1), world Z → page_Y (+1)
        axes = view_axes((0.0, -100.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        return ViewCoordinates(axes, view_x=100.0, view_y=80.0, cx=0.0, cy=0.0, cz=0.0, scale=1.0)

    def test_px_at_origin(self):
        vc = self._front_vc()
        assert vc.px(0.0) == pytest.approx(100.0)

    def test_py_at_origin(self):
        vc = self._front_vc()
        assert vc.py(0.0) == pytest.approx(80.0)

    def test_px_positive_offset(self):
        vc = self._front_vc()
        assert vc.px(10.0) == pytest.approx(110.0)

    def test_py_positive_offset(self):
        vc = self._front_vc()
        assert vc.py(5.0) == pytest.approx(85.0)

    def test_scale_applied(self):
        axes = view_axes((0.0, -100.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        vc = ViewCoordinates(axes, view_x=0.0, view_y=0.0, cx=0.0, cy=0.0, cz=0.0, scale=2.0)
        assert vc.px(5.0) == pytest.approx(10.0)

    def test_centroid_offset(self):
        axes = view_axes((0.0, -100.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        vc = ViewCoordinates(axes, view_x=50.0, view_y=50.0, cx=10.0, cy=0.0, cz=5.0, scale=1.0)
        assert vc.px(10.0) == pytest.approx(50.0)  # at centroid → view centre
        assert vc.py(5.0) == pytest.approx(50.0)  # at centroid → view centre

    # px_axis / py_axis attributes

    def test_front_view_px_axis(self):
        vc = self._front_vc()
        assert vc.px_axis == "world_X"

    def test_front_view_py_axis(self):
        vc = self._front_vc()
        assert vc.py_axis == "world_Z"

    # pp() matches px()/py() for orthographic views

    def test_pp_front_view_matches_px_py(self):
        vc = self._front_vc()
        page_x, page_y = vc.pp(10.0, 0.0, 5.0)
        assert page_x == pytest.approx(vc.px(10.0))
        assert page_y == pytest.approx(vc.py(5.0))

    def test_pp_front_view_ignores_depth_axis(self):
        # world_Y is depth in front view — varying it should not change the page point
        vc = self._front_vc()
        pt_a = vc.pp(10.0, 0.0, 5.0)
        pt_b = vc.pp(10.0, 50.0, 5.0)
        assert pt_a == pytest.approx(pt_b)

    # Side view: camera on +X axis → world_Y → page_X, world_Z → page_Y

    def _side_vc(self):
        axes = view_axes((100.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        return ViewCoordinates(axes, view_x=150.0, view_y=80.0, cx=0.0, cy=0.0, cz=0.0, scale=1.0)

    def test_side_view_px_axis_is_world_y(self):
        vc = self._side_vc()
        assert vc.px_axis == "world_Y"

    def test_side_view_py_axis_is_world_z(self):
        vc = self._side_vc()
        assert vc.py_axis == "world_Z"

    def test_side_view_px_maps_y_coordinate(self):
        vc = self._side_vc()
        assert vc.px(8.0) == pytest.approx(158.0)

    def test_side_view_py_maps_z_coordinate(self):
        vc = self._side_vc()
        assert vc.py(3.0) == pytest.approx(83.0)

    def test_side_view_pp_matches_px_py(self):
        vc = self._side_vc()
        page_x, page_y = vc.pp(0.0, 8.0, 3.0)
        assert page_x == pytest.approx(vc.px(8.0))
        assert page_y == pytest.approx(vc.py(3.0))

    # Plan view: camera on +Z axis → world_X → page_X, world_Y → page_Y

    def _plan_vc(self):
        axes = view_axes((0.0, 0.0, 100.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0))
        return ViewCoordinates(axes, view_x=100.0, view_y=150.0, cx=0.0, cy=0.0, cz=0.0, scale=1.0)

    def test_plan_view_px_axis_is_world_x(self):
        vc = self._plan_vc()
        assert vc.px_axis == "world_X"

    def test_plan_view_py_axis_is_world_y(self):
        vc = self._plan_vc()
        assert vc.py_axis == "world_Y"

    def test_plan_view_pp_matches_px_py(self):
        vc = self._plan_vc()
        page_x, page_y = vc.pp(7.0, 4.0, 0.0)
        assert page_x == pytest.approx(vc.px(7.0))
        assert page_y == pytest.approx(vc.py(4.0))

    # ISO view: camera at (-DIST, -DIST, DIST) → two world axes → page_X

    def _iso_vc(self):
        # Standard ISO camera: world_X → page_X (+1), world_Y → page_X (-1), world_Z → page_Y (+1)
        axes = view_axes((-100.0, -100.0, 100.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        return ViewCoordinates(axes, view_x=100.0, view_y=80.0, cx=0.0, cy=0.0, cz=0.0, scale=1.0)

    def test_iso_view_px_axis_is_none(self):
        vc = self._iso_vc()
        assert vc.px_axis is None

    def test_iso_view_px_raises_with_helpful_message(self):
        vc = self._iso_vc()
        with pytest.raises(ValueError, match="pp"):
            vc.px(5.0)

    def test_iso_view_py_raises_with_helpful_message(self):
        # world_Z → page_Y uniquely, so py_axis should be set
        # (ISO typically only has the page_X clash, not page_Y)
        vc = self._iso_vc()
        # world_Z maps cleanly to page_Y — py() should still work
        assert vc.py_axis == "world_Z"
        assert vc.py(3.0) == pytest.approx(83.0)

    def test_iso_view_pp_correct(self):
        # For ISO camera at (-100,-100,100) with look_at=(0,0,0), up=(0,0,1):
        # world_X → page_X (+1), world_Y → page_X (-1), world_Z → page_Y (+1)
        # pp(10, 5, 3) → page_x = 100 + (10-0)*1 + (5-0)*(-1) = 105
        #                page_y = 80 + (3-0)*1 = 83
        vc = self._iso_vc()
        page_x, page_y = vc.pp(10.0, 5.0, 3.0)
        assert page_x == pytest.approx(105.0)
        assert page_y == pytest.approx(83.0)

    def test_iso_view_pp_at_centroid_gives_view_centre(self):
        vc = self._iso_vc()
        page_x, page_y = vc.pp(0.0, 0.0, 0.0)
        assert page_x == pytest.approx(100.0)
        assert page_y == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# analyse_cylinders / analyse_face_levels — require OCP (slow)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_analyse_cylinders_box_has_no_z_cylinders():
    from build123d import Box

    box = Box(30, 20, 10)
    z_cyls, cross_cyls = analyse_cylinders(box)
    assert z_cyls == []
    assert cross_cyls == []


@pytest.mark.timeout(60)
def test_analyse_cylinders_finds_cylinder():
    from build123d import Cylinder

    cyl = Cylinder(5, 20)  # radius=5, height=20 → diameter=10
    z_cyls, cross_cyls = analyse_cylinders(cyl)
    assert len(z_cyls) >= 1
    diameters = [c["diameter"] for c in z_cyls]
    assert any(abs(d - 10.0) < 0.5 for d in diameters)


@pytest.mark.timeout(60)
def test_analyse_face_levels_box():
    from build123d import Box

    box = Box(30, 20, 10)
    levels = analyse_face_levels(box)
    # Box centred at origin has Z faces at -5 and +5
    assert any(abs(z - (-5.0)) < 0.1 for z in levels)
    assert any(abs(z - 5.0) < 0.1 for z in levels)


@pytest.mark.timeout(60)
def test_analyse_face_levels_returns_sorted():
    from build123d import Box

    box = Box(30, 20, 10)
    levels = analyse_face_levels(box)
    assert levels == sorted(levels)


@pytest.mark.timeout(60)
def test_analyse_face_levels_area_filter_drops_tiny_faces():
    # A sub-feature horizontal face (e.g. a fragment of engraved text) is far
    # smaller than the plan footprint and must not be counted as a real step.
    # staircase.step review: a 0.57 mm² digit face was dimensioned as z=6.4.
    from build123d import Box, Pos

    # 30×20 footprint (600 mm²); a 1×1 pip on top (1 mm² top face at z=7).
    part = Box(30, 20, 10) + Pos(0, 0, 6) * Box(1, 1, 2)

    # Without the filter the tiny face shows up as a phantom level.
    unfiltered = analyse_face_levels(part)
    assert any(abs(z - 7.0) < 0.1 for z in unfiltered)

    # With a 1%-of-footprint threshold (6 mm²) the 1 mm² face is dropped,
    # leaving only the real slab faces.
    filtered = analyse_face_levels(part, min_area_frac=0.01)
    assert not any(abs(z - 7.0) < 0.1 for z in filtered)
    assert any(abs(z - 5.0) < 0.1 for z in filtered)
    assert any(abs(z - (-5.0)) < 0.1 for z in filtered)


# ---------------------------------------------------------------------------
# Drawing builder (build_drawing / Drawing / add_view)
# ---------------------------------------------------------------------------


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
    svg, dxf = dwg.export(stem)
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
    # #74 — views, scale, page, and title block only; no turned-part dims.
    dwg = build_drawing(Cylinder(15, 40), auto_dims=False)
    assert set(dwg.views) == {"front", "plan", "side", "iso"}
    assert [a for a in dwg.items] == [dwg._named["title_block"]]


@pytest.mark.timeout(60)
def test_clear_annotations_keeps_title_block():
    # #74 — wholesale removal without knowing the auto-name scheme.
    dwg = build_drawing(Cylinder(15, 40))  # cylinder → od dim, centerlines, …
    assert len(dwg.items) > 1
    removed = dwg.clear_annotations()
    assert removed
    assert all(a not in dwg.items for a in removed)
    assert len(dwg.items) == 1
    assert "title_block" in dwg._named and len(dwg._named) == 1


@pytest.mark.timeout(60)
def test_clear_annotations_keep_custom_and_unnamed_removed():
    dwg = build_drawing(Box(30, 20, 10))
    keep_me = dwg.add(
        Leader(tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label="K", draft=dwg.draft), "ldr_k"
    )
    dwg.add(Leader(tip=dwg.at("front", 0, 0, 0), elbow=(6, 6, 0), label="U", draft=dwg.draft))
    dwg.clear_annotations(keep=("title_block", "ldr_k"))
    assert set(dwg._named) == {"title_block", "ldr_k"}
    assert keep_me in dwg.items
    assert len(dwg.items) == 2  # unnamed leader removed too


@pytest.fixture(scope="module")
def ctc01_a3_drawing():
    # Fixture — NIST CTC-01-like plate at 1:5 on A3.  Module-scoped; tests must not mutate it.
    return build_drawing(Box(800, 450, 150), scale=0.2, page="A3")


@pytest.mark.timeout(120)
def test_ctc01_iso_uses_upper_right_zone(ctc01_a3_drawing):
    # #75 updated — wide/flat part on A3: the iso is repositioned into the upper-right
    # zone (above the SV, right of FV/PV) where it fits at sheet scale.  No NTS label.
    from draftwright.make_drawing import _iso_bbox

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
def test_ctc01_iso_world_to_page_mapping(ctc01_a3_drawing):
    # dwg.at("iso", ...) must map world points to page even after the iso is
    # repositioned to the upper-right zone (still projected at sheet scale).
    dwg = ctc01_a3_drawing
    cx, cy, cz = dwg.centroid
    centre = dwg.at("iso", cx, cy, cz)
    vis, _hid = dwg.views["iso"]
    bb = vis.bounding_box()
    assert bb.min.X < centre[0] < bb.max.X and bb.min.Y < centre[1] < bb.max.Y
    iso_scale = dwg._coords["iso"]._scale
    raised = dwg.at("iso", cx, cy, cz + 100)
    assert raised[1] - centre[1] == pytest.approx(100 * iso_scale)


@pytest.mark.timeout(60)
def test_iso_view_grow_capped_at_max():
    # The iso is an orientation aid, not a measured view: fitted to a large empty
    # zone it must not balloon past _ISO_MAX_GROW × sheet scale (was ~8× before).
    from draftwright.make_drawing import _ISO_MAX_GROW

    # Small part forced onto a big sheet → large empty rectangle → would over-grow.
    dwg = build_drawing(Box(40, 30, 20), scale=1, page="A1")
    iso_scale = dwg._coords["iso"]._scale
    sheet_scale = dwg._analysis.SCALE
    assert iso_scale <= _ISO_MAX_GROW * sheet_scale + 1e-6
    assert iso_scale == pytest.approx(_ISO_MAX_GROW * sheet_scale, abs=1e-6)


@pytest.mark.timeout(60)
def test_iso_stays_within_page_bounds():
    # Whether scaled up or not, the iso must always lie within the page margin.
    from draftwright.make_drawing import _iso_bbox

    dwg = build_drawing(Box(30, 20, 10))
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
    from draftwright.make_drawing import _iso_bbox

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


@pytest.mark.timeout(60)
def test_drawing_add_and_remove():
    dwg = build_drawing(Box(30, 20, 10))
    n0 = len(dwg.items)
    ldr = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label="X", draft=dwg.draft)
    dwg.add(ldr, "ldr_test")
    assert len(dwg.items) == n0 + 1
    removed = dwg.remove("ldr_test")
    assert removed is ldr
    assert len(dwg.items) == n0
    with pytest.raises(KeyError):
        dwg.remove("does_not_exist")


@pytest.mark.timeout(60)
def test_drawing_add_replaces_reused_name():
    dwg = build_drawing(Box(30, 20, 10))
    n0 = len(dwg.items)
    first = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label="A", draft=dwg.draft)
    second = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(6, 6, 0), label="B", draft=dwg.draft)
    dwg.add(first, "ldr")
    dwg.add(second, "ldr")  # same name → replaces, no orphan left behind
    assert len(dwg.items) == n0 + 1
    assert first not in dwg.items
    assert dwg.remove("ldr") is second


@pytest.mark.timeout(60)
def test_drawing_at_maps_world_to_page():
    dwg = build_drawing(Box(30, 20, 10))
    cx, cy, cz = dwg.centroid
    base = dwg.at("front", cx, cy, cz)
    # Front view: world +X → page +X, world +Z → page +Y.
    dx = dwg.at("front", cx + 10, cy, cz)
    dz = dwg.at("front", cx, cy, cz + 10)
    assert dx[0] > base[0] and dx[1] == pytest.approx(base[1])
    assert dz[1] > base[1] and dz[0] == pytest.approx(base[0])


@pytest.mark.timeout(60)
def test_drawing_add_view(tmp_path):
    dwg = build_drawing(Box(30, 20, 10))
    look = dwg.look_at
    bottom_cam = (look[0], look[1], look[2] - dwg.dist)
    vc = dwg.add_view("bottom", Box(30, 20, 10), bottom_cam, (0, 1, 0), (260.0, 60.0))
    assert "bottom" in dwg.views
    assert isinstance(vc, ViewCoordinates)
    # The custom view exports alongside the standard ones.
    svg, _ = dwg.export(str(tmp_path / "b"))
    assert Path(svg).exists()


@pytest.mark.timeout(60)
def test_generate_script_emits_build_drawing(tmp_path):
    box = Box(30, 20, 10)
    step = tmp_path / "p.step"
    export_step(box, str(step))
    py = generate_script(str(step), out=str(tmp_path / "p"))
    content = Path(py).read_text(encoding="utf-8")
    assert "build_drawing(" in content
    assert "dwg.export(" in content
    assert "Customise here" in content


# ---------------------------------------------------------------------------
# Part classification (#81) — prismatic parts skip turned-part annotations
# ---------------------------------------------------------------------------


class TestPrismaticClassification:
    @pytest.mark.timeout(60)
    def test_prismatic_part_with_bores_skips_turned_annotations(self):
        # A housing-like plate: Z-axis bores exist, but they are holes — not
        # an OD. dim_od / centrelines / ldr_z* would all be wrong.
        part = (
            Box(100, 60, 20)
            - Pos(20, 10, 0) * Cylinder(5, 30)
            - Pos(-30, -15, 0) * Cylinder(8, 30)
        )
        dwg = build_drawing(part)
        assert "dim_od" not in dwg._named
        assert "centerline_front" not in dwg._named
        assert "centerline_side" not in dwg._named
        assert not any(name.startswith("ldr_z") for name in dwg._named)

    @pytest.mark.timeout(60)
    def test_rotational_part_keeps_turned_annotations(self):
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert "dim_od" in dwg._named
        assert "centerline_front" in dwg._named
        assert "ldr_z0" in dwg._named

    @pytest.mark.timeout(60)
    def test_corner_fillets_do_not_make_a_plate_rotational(self):
        # Big quarter-cylinder corner fillets on a square plate must not be
        # mistaken for an OD.
        from build123d import Axis, fillet

        box = Box(60, 60, 20)
        part = fillet(box.edges().filter_by(Axis.Z), 25)
        dwg = build_drawing(part)
        assert "dim_od" not in dwg._named


# ---------------------------------------------------------------------------
# Export fallback (#83) — element-wise retry with view/layer context
# ---------------------------------------------------------------------------


class _FlakyExporter:
    """Stand-in exporter: rejects multi-element shapes (or everything)."""

    def __init__(self, fail_all=False):
        self.added = []
        self.fail_all = fail_all

    def add_shape(self, shape, layer=None):
        if self.fail_all:
            raise AssertionError("Constraint failed")
        elements = shape.faces() or shape.edges()
        if len(elements) > 1:
            raise AssertionError("Constraint failed")
        self.added.append(shape)


class TestExportShapeFallback:
    def test_compound_falls_back_to_edges(self):
        edges = Compound(
            [
                Edge.make_line((0, 0, 0), (1, 0, 0)),
                Edge.make_line((0, 1, 0), (1, 1, 0)),
            ]
        )
        exporter = _FlakyExporter()
        _export_shape(exporter, edges, "hidden", "view 'iso'")
        assert len(exporter.added) == 2

    def test_all_elements_failing_raises_with_context(self):
        edges = Compound([Edge.make_line((0, 0, 0), (1, 0, 0))])
        with pytest.raises(RuntimeError, match=r"view 'iso' \(layer 'hidden'\)"):
            _export_shape(_FlakyExporter(fail_all=True), edges, "hidden", "view 'iso'")

    def test_annotation_falls_back_to_faces(self):
        from build123d import Draft
        from build123d_drafting import Note

        note = Note("AB", (10, 10), Draft(font_size=3.0))  # two glyphs → ≥2 faces
        exporter = _FlakyExporter()
        _export_shape(exporter, note, "dims", "annotation 'AB'")
        assert len(exporter.added) == len(note.faces())

    def test_mixed_faces_and_loose_edges_all_exported(self):
        # A compound mixing text faces with bare stroke edges must not lose
        # the edges in the element-wise path.
        from build123d import Text

        mixed = Compound([*Text("A", 3).faces(), Edge.make_line((5, 5, 0), (9, 5, 0))])
        exporter = _FlakyExporter()
        _export_shape(exporter, mixed, "dims", "annotation 'mixed'")
        assert len(exporter.added) == len(mixed.faces()) + 1

    def test_svg_exporter_failure_raises_when_nothing_exports(self, monkeypatch):
        # Atomic (SVG) path: whole-shape add fails and the shape decomposes
        # to nothing — the original error must surface, not be swallowed.
        from build123d import ExportSVG

        svg = ExportSVG()
        svg.add_layer("part")

        def boom(self, shape, layer="", **kwargs):
            raise AssertionError("Constraint failed")

        monkeypatch.setattr(ExportSVG, "add_shape", boom)
        with pytest.raises(RuntimeError, match="nothing could be exported"):
            _export_shape(svg, Compound([]), "part", "view 'iso'")

    @pytest.mark.timeout(60)
    def test_export_survives_one_bad_compound(self, tmp_path, monkeypatch):
        # Simulate #83: OCCT raises a bare AssertionError for one view
        # compound. export() must degrade element-wise and still write files.
        from build123d import ExportSVG

        dwg = build_drawing(Box(30, 20, 10))
        real = ExportSVG.add_shape
        state = {"tripped": False}

        def flaky(self, shape, layer="default", **kwargs):
            if not state["tripped"] and layer == "part":
                state["tripped"] = True
                raise AssertionError("Constraint failed")
            return real(self, shape, layer=layer, **kwargs)

        monkeypatch.setattr(ExportSVG, "add_shape", flaky)
        svg, dxf = dwg.export(str(tmp_path / "f"))
        assert Path(svg).exists() and Path(dxf).exists()


# ---------------------------------------------------------------------------
# Feature-coverage lint (#80) — size coverage of hole/boss diameters
# ---------------------------------------------------------------------------


class TestLintFeatureCoverage:
    @pytest.mark.timeout(60)
    def test_uncovered_bore_is_flagged(self):
        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(4, 30)
        issues = lint_feature_coverage(part, [])
        assert [i.code for i in issues] == ["feature_not_dimensioned"]
        assert "ø8" in issues[0].message
        assert issues[0].severity == "warning"

    @pytest.mark.timeout(60)
    def test_diameter_callout_covers_feature(self):
        from build123d import Draft
        from build123d_drafting import Note

        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(4, 30)
        ann = Note("4× ø8 THRU", (10, 10), Draft(font_size=3.0))
        assert lint_feature_coverage(part, [ann]) == []

    @pytest.mark.timeout(60)
    def test_radius_note_does_not_cover(self):
        # An "R4 TYP" fillet note must not mask an undimensioned ø8 bore.
        from build123d import Draft
        from build123d_drafting import Note

        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(4, 30)
        ann = Note("R4 TYP", (10, 10), Draft(font_size=3.0))
        assert [i.code for i in lint_feature_coverage(part, [ann])] == ["feature_not_dimensioned"]

    @pytest.mark.timeout(60)
    def test_slot_split_bore_is_still_a_feature(self):
        # Two opposed keyway notches leave the bore wall as two cylinder patches
        # under half a turn each — together they are still one undimensioned ø10
        # hole. (A single full-width slot would bisect the block into two solids,
        # i.e. two half-bores rather than one keyed hole; coaxial bores in
        # *different* solids are kept distinct, helpers #68.)
        part = (
            Box(60, 40, 10)
            - Cylinder(5, 12)
            - Pos(0, 5, 0) * Box(2, 4, 12)
            - Pos(0, -5, 0) * Box(2, 4, 12)
        )
        assert len(part.solids()) == 1
        issues = lint_feature_coverage(part, [])
        assert any("ø10" in i.message for i in issues)

    @pytest.mark.timeout(60)
    def test_hole_callout_accepts_string_diameter(self):
        from build123d_drafting import HoleCallout

        callout = HoleCallout("8.5 H7", through=True)
        assert callout.covers_diameters == (8.5,)

    @pytest.mark.timeout(60)
    def test_fillets_are_not_features(self):
        from build123d import Axis, fillet

        box = Box(60, 40, 20)
        part = fillet(box.edges().filter_by(Axis.Z), 3)
        assert lint_feature_coverage(part, []) == []

    @pytest.mark.timeout(60)
    def test_drawing_lint_reports_unannotated_bore(self):
        # Prismatic bores now get automatic callouts (#91) — so the sheet is
        # born clean, and removing the callout must surface the bore through
        # the coverage lint as the missing-dimension signal (#80).
        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(5, 30)
        dwg = build_drawing(part)
        assert "feature_not_dimensioned" not in [i.code for i in dwg.lint()]
        for name in [n for n in dwg._named if n.startswith("hc_")]:
            dwg.remove(name)
        codes = [i.code for i in dwg.lint()]
        assert "feature_not_dimensioned" in codes

    @pytest.mark.timeout(60)
    def test_drawing_lint_clean_for_annotated_rotational_part(self):
        dwg = build_drawing(Cylinder(15, 40) - Cylinder(5, 40))
        assert [i for i in dwg.lint() if i.code == "feature_not_dimensioned"] == []

    @pytest.mark.timeout(60)
    def test_title_block_text_is_not_a_callout(self):
        # "BRACKET R8" in the title must not mark ø16 as covered.
        from build123d import Draft
        from build123d_drafting import TitleBlock

        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(8, 30)
        tb = TitleBlock("BRACKET R8", "DWG-1", draft=Draft(font_size=3.0))
        issues = lint_feature_coverage(part, [tb])
        assert [i.code for i in issues] == ["feature_not_dimensioned"]

    @pytest.mark.timeout(60)
    def test_hole_callout_covers_via_structured_metadata(self):
        # HoleCallout draws its ø glyphs geometrically (label is "") — it must
        # still count as coverage.
        from build123d_drafting import HoleCallout

        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(4.25, 30)
        callout = HoleCallout(8.5, count=4, through=True)
        assert lint_feature_coverage(part, [callout]) == []


class TestAutoHoleAnnotations:
    """Auto hole callouts (#91), count grouping (#92), centre marks (#95)."""

    @pytest.fixture(scope="class")
    def plate_drawing(self):
        # 4× ø10 thru corners + centre ø8 thru with ø16×6 cbore + ø6 x-axis
        # cross hole + ø12 blind hole
        part = (
            Box(100, 100, 20)
            - Pos(35, 35, 0) * Cylinder(5, 20)
            - Pos(-35, 35, 0) * Cylinder(5, 20)
            - Pos(35, -35, 0) * Cylinder(5, 20)
            - Pos(-35, -35, 0) * Cylinder(5, 20)
            - Cylinder(4, 20)
            - Pos(0, 0, 7) * Cylinder(8, 6)
            - Pos(0, 25, 0) * Cylinder(3, 100, rotation=(0, 90, 0))
            - Pos(-20, -10, 10 - 4) * Cylinder(6, 8)
        )
        return build_drawing(part)

    @pytest.mark.timeout(120)
    def test_identical_holes_share_one_counted_callout(self, plate_drawing):
        hc = [n for n in plate_drawing._named if n.startswith("hc_plan")]
        # 3 distinct Z specs (4× ø10 thru, ø8 cbore stack, ø12 blind), not 6
        assert len(hc) == 3

    @pytest.mark.timeout(120)
    def test_callouts_cover_all_feature_diameters(self, plate_drawing):
        covered = set()
        for name, ann in plate_drawing._named.items():
            if name.startswith("hc_"):
                covered.update(getattr(ann, "covers_diameters", ()))
        assert covered == {10.0, 8.0, 16.0, 6.0, 12.0}

    @pytest.mark.timeout(120)
    def test_cross_axis_hole_gets_side_view_callout(self, plate_drawing):
        (name,) = [n for n in plate_drawing._named if n.startswith("hc_side")]
        assert plate_drawing._named[name].covers_diameters == (6.0,)

    @pytest.mark.timeout(120)
    def test_every_hole_gets_a_centre_mark(self, plate_drawing):
        cm = [n for n in plate_drawing._named if n.startswith("cm_")]
        assert len(cm) == 7  # 6 z-holes in plan + 1 x-hole in side
        assert all(plate_drawing._named[n].is_centerline for n in cm)

    @pytest.mark.timeout(120)
    def test_sheet_is_lint_clean(self, plate_drawing):
        issues = [i for i in plate_drawing.lint() if i.severity != "info"]
        assert [i.code for i in issues] == []

    @pytest.mark.timeout(60)
    def test_bore_callout_elbow_at_boundary_without_section_line(self):
        # When no section line is placed (no cbore/spotface/blind holes) the
        # plan-view elbow must sit at the view boundary, not past it — the shaft
        # must not cross the view outline (#127).
        part = Box(80, 60, 10) - Pos(25, 15, 0) * Cylinder(4, 10)
        dwg = build_drawing(part)
        assert "section_line" not in dwg._named
        hc = dwg._named.get("hc_plan0")
        assert hc is not None
        plan_right = (
            dwg._analysis.PV_X + (dwg._analysis.bb.max.X - dwg._analysis.cx) * dwg._analysis.SCALE
        )
        elbow_x = hc.elbow[0]
        assert abs(elbow_x - plan_right) < 0.5  # elbow at boundary, not past it

    @pytest.mark.timeout(60)
    def test_through_holes_group_across_wall_thicknesses(self):
        # The same drill through a 10mm and a 7.5mm wall is one "2× ø5 THRU"
        # callout — through specs group regardless of depth.
        part = (
            Box(80, 40, 10)
            - Pos(20, 0, 5) * Box(40, 40, 5)
            - Pos(-20, 0, 0) * Cylinder(2.5, 10)
            - Pos(20, 0, -1.25) * Cylinder(2.5, 7.5)
        )
        dwg = build_drawing(part)
        assert len([n for n in dwg._named if n.startswith("hc_")]) == 1

    @pytest.mark.timeout(60)
    def test_two_front_view_specs_fit_below_the_view(self):
        # The title block only constrains rows that reach its x-range, so
        # the strip below the front view holds multiple callouts (review
        # round 1: the old veto blanked the whole strip on A4).
        part = (
            Box(80, 40, 30)
            - Pos(-20, 0, 5) * Cylinder(2.5, 50, rotation=(90, 0, 0))
            - Pos(25, 0, -5) * Cylinder(4, 50, rotation=(90, 0, 0))
        )
        dwg = build_drawing(part)
        assert len([n for n in dwg._named if n.startswith("hc_front")]) == 2
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(60)
    def test_all_distinct_bores_get_callouts(self):
        # #36: no per-view callout cap — six distinct-diameter holes in a row
        # all get callouts (previously capped at the four largest), and nothing
        # is dropped because they fit.
        part = Box(120, 80, 10)
        for i, r in enumerate([1, 1.5, 2, 2.5, 3, 4]):
            part = part - Pos(-50 + i * 20, 0, 0) * Cylinder(r, 10)
        dwg = build_drawing(part)
        covered = set()
        for name, ann in dwg._named.items():
            if name.startswith("hc_"):
                covered.update(ann.covers_diameters)
        assert covered == {2.0, 3.0, 4.0, 5.0, 6.0, 8.0}
        assert "callout_dropped" not in {i.code for i in dwg.lint()}

    @pytest.mark.timeout(60)
    def test_rotational_part_keeps_leader_annotations(self):
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert "ldr_z0" in dwg._named
        assert not any(n.startswith("hc_") for n in dwg._named)
        # the central bore still gets a centre mark in the plan view
        assert any(n.startswith("cm_plan") for n in dwg._named)

    @pytest.mark.timeout(60)
    def test_plan_bore_leaders_elbow_outside_view(self):
        # Bore callout elbows must sit at or beyond the plan view right boundary
        # (one arrow_length past it), not deep in the annotation corridor.
        # Old code used 0.6 × DIM_PAD ≈ 10.8 mm; new code uses arrow_length ≈ 2.7 mm.
        part = Box(40, 40, 20) - Pos(15, 0, 0) * Cylinder(3, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        plan_right = a.PV_X + (a.bb.max.X - a.cx) * a.SCALE
        arrow_len = dwg.draft.arrow_length
        old_corridor = 0.6 * a.DIM_PAD  # ≈ 10.8 mm — the old, oversized offset
        hc_plan_names = [n for n in dwg._named if n.startswith("hc_plan")]
        assert hc_plan_names, "Expected at least one plan-view bore callout"
        for name in hc_plan_names:
            ldr = dwg._named[name]
            assert ldr.elbow[0] >= plan_right - 1e-6, (
                f"{name}: elbow x={ldr.elbow[0]:.3f} is inside the view "
                f"(plan_right={plan_right:.3f})"
            )
            assert ldr.elbow[0] < plan_right + old_corridor, (
                f"{name}: elbow x={ldr.elbow[0]:.3f} is too far from view "
                f"(should be < plan_right + 0.6×DIM_PAD = {plan_right + old_corridor:.3f})"
            )
            # Arrowhead must still sit inside the view.
            assert ldr.tip[0] + arrow_len <= plan_right + 1e-6, (
                f"{name}: arrowhead back at {ldr.tip[0] + arrow_len:.3f} "
                f"exceeds plan_right={plan_right:.3f}"
            )

    @pytest.mark.timeout(60)
    def test_solve_strip_ys_returns_feasible_positions(self):
        from draftwright.make_drawing import _solve_strip_ys

        # Four natural positions, solver must spread them to respect min_gap=8.
        result = _solve_strip_ys([10.0, 12.0, 14.0, 16.0], min_gap=8.0, lo=0.0, hi=100.0)
        assert result is not None
        assert len(result) == 4
        for y in result:
            assert 0.0 <= y <= 100.0
        for a, b in zip(result, result[1:]):
            assert b - a >= 8.0 - 1e-9

    @pytest.mark.timeout(60)
    def test_solve_strip_ys_infeasible_returns_none(self):
        from draftwright.make_drawing import _solve_strip_ys

        # Three items need 2 × 8 = 16mm gap, but range is only 10mm.
        result = _solve_strip_ys([5.0, 10.0, 15.0], min_gap=8.0, lo=0.0, hi=10.0)
        assert result is None

    @pytest.mark.timeout(60)
    def test_solve_strip_ys_empty_input(self):
        from draftwright.make_drawing import _solve_strip_ys

        assert _solve_strip_ys([], min_gap=8.0, lo=0.0, hi=100.0) == []

    @pytest.mark.timeout(60)
    def test_solve_strip_via_layout_is_byte_identical_to_primitive(self):
        # #80: the hole-callout Y-stack now routes through the LayoutSolver. The
        # adapter must return exactly what the bare primitive did — including for
        # tied natural Ys, where the solver's (natural, key) order must reduce to
        # the input order via the zero-padded keys.
        from draftwright.make_drawing import _solve_strip_via_layout, _solve_strip_ys

        # (naturals, gap, lo, hi)
        cases = [
            ([10.0, 12.0, 14.0, 16.0], 8.0, 0.0, 100.0),  # distinct, feasible
            ([5.0, 5.0, 5.0], 8.0, 0.0, 100.0),  # all tied
            ([0.0, 0.0, 20.0, 20.0], 8.0, 0.0, 100.0),  # paired ties
            ([], 8.0, 0.0, 10.0),  # empty
            ([0.0, 0.0, 0.0], 8.0, 0.0, 10.0),  # infeasible, greedy also overflows
            # Knife-edge: exact solve reports infeasible at (n-1)*gap == hi-lo, but
            # a non-prefix greedy packing would just fit. The adapter must still
            # return None here (matching the primitive), or the caller's drop is
            # silently skipped (#80 review).
            ([4.52, 5.29, 16.07, 22.13], 4.73, 8.44, 22.63),
        ]
        for naturals, gap, lo, hi in cases:
            adapter = _solve_strip_via_layout(naturals, gap, lo, hi, "k")
            primitive = _solve_strip_ys(naturals, gap, lo, hi)
            assert adapter == primitive, naturals


class TestHolePatternAnnotations:
    """Bolt-circle and linear-array sheet furniture + count-aware lint (#92)."""

    @pytest.mark.timeout(120)
    def test_bolt_circle_gets_suffix_and_pitch_circle(self):
        import math

        part = Box(100, 100, 12) - Cylinder(10, 12)
        for i in range(6):
            ang = math.radians(60 * i + 15)
            part = part - Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(4, 12)
        dwg = build_drawing(part)
        assert any(n.startswith("bc_plan") for n in dwg._named)
        (hc8,) = [
            a
            for n, a in dwg._named.items()
            if n.startswith("hc_") and 8.0 in getattr(a, "covers_diameters", ())
        ]
        assert hc8.covers_count == 6
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_linear_array_gets_pitch_dimension(self):
        part = Box(140, 50, 10)
        for i in range(5):
            part = part - Pos(-40 + i * 20, 0, 0) * Cylinder(3, 10)
        dwg = build_drawing(part)
        assert dwg._named["dim_pitch_plan0"].label == "4× 20"
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_opposite_face_arrays_get_separate_callouts_and_pitch_dims(self):
        # Blind holes drilled from opposite faces are different machining
        # operations: two counted callouts, two (tiered) pitch dims.
        part = Box(140, 50, 14)
        for i in range(3):
            part = part - Pos(-30 + i * 20, 8, 4) * Cylinder(3, 6)
            part = part - Pos(-30 + i * 20, -8, -4) * Cylinder(3, 6)
        dwg = build_drawing(part)
        assert len([n for n in dwg._named if n.startswith("hc_plan")]) == 2
        assert len([n for n in dwg._named if n.startswith("dim_pitch_plan")]) == 2
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_top_edge_array_dimensions_above_the_plan_view(self):
        # Below the plan view sit dim_width and the front view — plan pitch
        # dims always go up, with short extension lines for top-edge rows.
        part = Box(140, 50, 10)
        for i in range(4):
            part = part - Pos(-30 + i * 20, 18, 0) * Cylinder(3, 10)
        dwg = build_drawing(part)
        dim = dwg._named["dim_pitch_plan0"]
        plan_top = dwg.views["plan"][0].bounding_box().max.Y
        assert dim.dim_level_y > plan_top
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_pitch_dim_skipped_when_off_page(self):
        # Two parallel vertical arrays on a snug layout: the second tier
        # would cross the page margin — it must skip, never force-place.
        part = Box(60, 180, 10)
        for i in range(5):
            part = part - Pos(-15, -70 + i * 35, 0) * Cylinder(3.5, 10)
        for i in range(4):
            part = part - Pos(15, -52.5 + i * 35, 0) * Cylinder(2.5, 10)
        dwg = build_drawing(part)
        assert "dim_pitch_plan0" in dwg._named
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(60)
    def test_count_mismatch_surfaces_in_lint(self):
        from build123d import Draft
        from build123d_drafting import HoleCallout

        part = Box(100, 100, 10)
        for x in (-30, -10, 10, 30):
            part = part - Pos(x, 0, 0) * Cylinder(5, 10)
        d = Draft(font_size=2.5)
        under = lint_feature_coverage(part, [HoleCallout(10, count=2, draft=d)])
        assert [i.code for i in under] == ["feature_count_mismatch"]
        assert lint_feature_coverage(part, [HoleCallout(10, count=4, draft=d)]) == []

    @pytest.mark.timeout(60)
    def test_text_labels_are_exempt_from_count_check(self):
        from build123d import Draft
        from build123d_drafting import Note

        part = Box(100, 100, 10)
        for x in (-30, 30):
            part = part - Pos(x, 0, 0) * Cylinder(5, 10)
        d = Draft(font_size=2.5)
        assert lint_feature_coverage(part, [Note("ø10 (2 PL)", (0, 0), d)]) == []

    @pytest.mark.timeout(60)
    def test_repetition_label_passes_measured_check(self):
        from build123d import Draft
        from build123d_drafting import Dimension, lint_drawing

        d = Draft(font_size=2.5)
        dim = Dimension((0, 0, 0), (80, 0, 0), "above", 8, d, label="4× 20")
        assert [i for i in lint_drawing([dim]) if i.code == "label_vs_measured"] == []


class TestLocationDimsAndSection:
    """Baseline location dims (#93) and auto section views (#94)."""

    @pytest.fixture(scope="class")
    def plate_drawing(self):
        # corners (a square → bolt-circle group) + centre cbore stack +
        # off-centre blind hole: refs are the BC centre (= cbore hole,
        # deduped) and the blind hole
        part = (
            Box(100, 100, 20)
            - Pos(35, 35, 0) * Cylinder(5, 20)
            - Pos(-35, 35, 0) * Cylinder(5, 20)
            - Pos(35, -35, 0) * Cylinder(5, 20)
            - Pos(-35, -35, 0) * Cylinder(5, 20)
            - Cylinder(4, 20)
            - Pos(0, 0, 7) * Cylinder(8, 6)
            - Pos(-20, -10, 6) * Cylinder(6, 8)
        )
        return build_drawing(part)

    @pytest.mark.timeout(120)
    def test_x_dims_above_the_plan_view(self, plate_drawing):
        labels = {a.label for n, a in plate_drawing._named.items() if n.startswith("dim_locx")}
        assert labels == {"50", "30"}
        plan_top = plate_drawing.views["plan"][0].bounding_box().max.Y
        assert all(
            a.dim_level_y > plan_top
            for n, a in plate_drawing._named.items()
            if n.startswith("dim_locx")
        )

    @pytest.mark.timeout(120)
    def test_y_dims_above_the_side_view(self, plate_drawing):
        labels = {a.label for n, a in plate_drawing._named.items() if n.startswith("dim_locy")}
        assert labels == {"50", "40"}
        side_top = plate_drawing.views["side"][0].bounding_box().max.Y
        assert all(
            a.dim_level_y > side_top
            for n, a in plate_drawing._named.items()
            if n.startswith("dim_locy")
        )

    @pytest.mark.timeout(120)
    def test_section_view_with_cutting_plane_markers(self, plate_drawing):
        assert "section_aa" in plate_drawing.views
        assert plate_drawing._named["section_caption"].label == "SECTION A–A"
        assert plate_drawing._named["section_line"].is_centerline
        assert plate_drawing._named["section_a_left"].label == "A"
        assert plate_drawing._named["section_a_right"].label == "A"

    @pytest.mark.timeout(120)
    def test_section_end_arrows_present(self, plate_drawing):
        # ISO 128-44: cutting-plane ends must have wings + solid filled arrowheads
        for side in ("left", "right"):
            wing = plate_drawing._named[f"section_wing_{side}"]
            arrow = plate_drawing._named[f"section_arrow_{side}"]
            # wing is a single-edge Compound (the perpendicular stub stroke)
            assert len(wing.edges()) == 1
            # arrow is a filled solid (Arrow produces faces, not open barbs)
            assert len(list(arrow.faces())) >= 1
        # wings are below the section line (tip_y < line y)
        sl_y = plate_drawing._named["section_line"].bounding_box().min.Y
        wl_y = plate_drawing._named["section_wing_left"].bounding_box().min.Y
        assert wl_y < sl_y

    @pytest.mark.timeout(120)
    def test_section_hatch_present_and_45_degrees(self, plate_drawing):
        # ISO 128-50: 45° hatching on the cut face
        assert "section_hatch" in plate_drawing._named
        hatch = plate_drawing._named["section_hatch"]
        edges = list(hatch.edges())
        assert len(edges) > 0
        # Each hatch edge should be at approximately 45° (slope ≈ 1)
        for e in edges:
            p0, p1 = e.position_at(0), e.position_at(1)
            dx, dy = p1.X - p0.X, p1.Y - p0.Y
            if abs(dx) > 0.01:
                assert abs(dy / dx - 1.0) < 0.05  # slope ≈ 1 → 45°

    @pytest.mark.timeout(120)
    def test_sheet_is_lint_clean(self, plate_drawing):
        assert [i for i in plate_drawing.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_through_only_plate_gets_no_section(self):
        part = Box(80, 60, 10) - Pos(20, 10, 0) * Cylinder(5, 10)
        dwg = build_drawing(part)
        assert "section_aa" not in dwg.views
        assert "section_line" not in dwg._named
        # but it still gets located
        assert any(n.startswith("dim_locx") for n in dwg._named)

    @pytest.mark.timeout(120)
    def test_underside_cbore_triggers_a_section(self):
        # The issue's acceptance case: a blind cbore from the underside is
        # hidden-line-only everywhere — the section shows it as line-work.
        part = Box(80, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)
        dwg = build_drawing(part)
        assert "section_aa" in dwg.views
        vis, _hid = dwg.views["section_aa"]
        assert len(vis.edges()) > 0
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_section_clears_the_step_dim_ladder(self):
        # Step dims are placed before the section; the section's room check
        # must clear their labels (here: no room at all → skip, never a
        # section with a dim ladder through it).
        part = (
            Box(40, 12, 40)
            - Pos(10, 0, 20) * Box(20, 12, 40)
            - Pos(-10, 0, 0) * Cylinder(3, 40)
            - Pos(-10, 0, 16) * Cylinder(5, 8)
        )
        dwg = build_drawing(part)
        if "section_aa" in dwg.views:
            sb = dwg.views["section_aa"][0].bounding_box()
            for name, ann in dwg._named.items():
                if name.startswith("dim_step") and getattr(ann, "label_bbox", None):
                    x0, y0, x1, y1 = ann.label_bbox
                    assert not (
                        x1 > sb.min.X and x0 < sb.max.X and y1 > sb.min.Y and y0 < sb.max.Y
                    )
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_linear_array_locates_its_nearest_member(self):
        # The baseline dim goes to the hole nearest the datum corner; the
        # pitch dim chains the rest outward.
        part = Box(100, 50, 10)
        for x in (-30, -10, 10, 30):
            part = part - Pos(x, 0, 6) * Cylinder(4, 8)
        dwg = build_drawing(part)
        labels = sorted(a.label for n, a in dwg._named.items() if n.startswith("dim_locx"))
        assert labels == ["20"]

    @pytest.mark.timeout(120)
    def test_section_letters_clear_the_bolt_circle(self, plate_drawing):
        # The corner-hole bolt circle sweeps wider than the part; the
        # cutting-plane letters must sit outside it (lint flags the overlap
        # otherwise).
        codes = [i.code for i in plate_drawing.lint() if i.severity != "info"]
        assert "label_centerline_overlap" not in codes

    @pytest.mark.timeout(120)
    def test_y_dims_tier_past_side_pitch_dims(self):
        # An x-axis array's pitch dim lives above the side view too — the
        # Y-location ladder must start beyond it, not on top of it.
        part = Box(60, 40, 30) - Pos(0, 5, 11) * Cylinder(3, 8)
        for y in (-12, 0, 12):
            part = part - Pos(15, y, 8) * Cylinder(2, 60, rotation=(0, 90, 0))
        dwg = build_drawing(part)
        locy = [a.dim_level_y for n, a in dwg._named.items() if n.startswith("dim_locy")]
        pitch = [a.dim_level_y for n, a in dwg._named.items() if n.startswith("dim_pitch_side")]
        assert locy and pitch
        assert min(abs(ly - py) for ly in locy for py in pitch) >= 8

    @pytest.mark.timeout(120)
    def test_pmi_compound_draws_the_solid_only(self):
        # AP242 STEP with PMI imports as a Compound of solid + annotation
        # geometry (plane border wires, leader curves). The drawing is of
        # the solids: no phantom rectangles in the views, no bbox inflation
        # corrupting the scale and envelope dims, and the section cut works.
        solid = Box(80, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)
        pmi = Edge.make_line((-80, 0, 40), (80, 0, 40))  # well outside the part
        part = Compound(children=[solid, pmi])
        dwg = build_drawing(part)
        assert "section_aa" in dwg.views
        assert dwg._named["dim_height"].label == "20"  # not the PMI z-extent
        # the views contain no line-work above the solid's top
        for vis, hid in dwg.views.values():
            assert vis.bounding_box().size.Y < 200  # sanity: no 160mm phantom

    @pytest.mark.timeout(60)
    def test_rotational_part_gets_neither(self):
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert "section_aa" not in dwg.views
        assert not any(n.startswith("dim_loc") for n in dwg._named)


class TestIsRotational:
    def test_plain_cylinder(self):
        assert _is_rotational(30.0, 30.0, 30.0, 0.0)

    def test_prismatic_envelope(self):
        assert not _is_rotational(100.0, 60.0, 24.0, 0.0)

    def test_small_boss_on_square_plate(self):
        assert not _is_rotational(100.0, 100.0, 40.0, 0.0)

    def test_off_centre_boss(self):
        assert not _is_rotational(100.0, 100.0, 84.0, 8.0)

    def test_no_external_cylinder(self):
        # Bores never qualify as an OD — od_diam is None for hole-only parts
        assert not _is_rotational(100.0, 100.0, None, 0.0)

    @pytest.mark.timeout(60)
    def test_square_plate_with_big_bore_is_prismatic(self):
        # ø85 bore in a 100-square plate: fills the envelope and is
        # concentric, but it is a hole — not an OD.
        part = Box(100, 100, 10) - Cylinder(42.5, 12)
        dwg = build_drawing(part)
        assert "dim_od" not in dwg._named

    @pytest.mark.timeout(60)
    def test_off_centre_bore_is_prismatic(self):
        part = Box(100, 100, 20) - Pos(8, 0, 0) * Cylinder(42, 30)
        dwg = build_drawing(part)
        assert "dim_od" not in dwg._named

    @pytest.mark.timeout(60)
    def test_mirrored_turned_part_stays_rotational(self):
        # Mirroring flips face orientations AND the cylinder frame handedness;
        # the external/bore split must survive it.
        from build123d import Plane, mirror

        part = mirror(Cylinder(30, 40) - Cylinder(10, 40), about=Plane.XZ)
        z_cyls, _ = analyse_cylinders(part)
        flags = {c["diameter"]: c["external"] for c in z_cyls}
        assert flags[60.0] is True and flags[20.0] is False
        dwg = build_drawing(part)
        assert "dim_od" in dwg._named

    @pytest.mark.timeout(60)
    def test_dim_od_uses_the_external_cylinder(self):
        # An internal recess wider than the boss must not be labelled as the
        # OD: dim_od comes from the classified external cylinder.
        part = (
            Box(100, 100, 20)
            + Pos(0, 0, 20) * Cylinder(42.5, 20)
            - Pos(0, 0, -7.5) * Cylinder(45, 5)
        )
        dwg = build_drawing(part)
        assert dwg._named["dim_od"].label == "ø85"

    @pytest.mark.timeout(60)
    def test_unrounded_od_does_not_duplicate_a_bore_leader(self, monkeypatch):
        # analyse_cylinders rounds diameters at source today, which masks the
        # #86 scenario — but the OD/bore exclusion must not depend on that:
        # feature records may carry raw OCCT diameters after the #87 lift.
        # With an unrounded OD (59.9999999 vs the dedup'd 60.0), a float !=
        # leaks the OD into the bore leaders as a duplicate ø60 callout.
        import importlib

        md = importlib.import_module("draftwright.make_drawing")
        real = md.analyse_cylinders

        def unrounded(part):
            z_cyls, cross_cyls = real(part)
            for c in z_cyls:
                if c["external"]:
                    c["diameter"] = 59.9999999
            return z_cyls, cross_cyls

        monkeypatch.setattr(md, "analyse_cylinders", unrounded)
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert dwg._named["dim_od"].label == "ø60"
        leader_labels = [a.label for n, a in dwg._named.items() if n.startswith("ldr_z")]
        assert leader_labels == ["ø20"]

    @pytest.mark.timeout(60)
    def test_lint_reuses_build_drawing_cylinder_analysis(self, monkeypatch):
        # build_drawing seeds the cache, so lint()/export() must not re-scan
        # the solid with analyse_cylinders.
        import importlib

        # (the package re-exports the make_drawing *function*, shadowing the
        # submodule attribute, so plain `import ... as md` grabs the function)
        md = importlib.import_module("draftwright.make_drawing")

        dwg = build_drawing(Box(30, 20, 10))
        calls = {"n": 0}
        real = md.analyse_cylinders

        def counting(part):
            calls["n"] += 1
            return real(part)

        monkeypatch.setattr(md, "analyse_cylinders", counting)
        dwg.lint()
        dwg.lint()
        assert calls["n"] == 0


# ---------------------------------------------------------------------------
# Layout-overfitting regression tests (issue #13)
#
# The fixtures above exercise the prismatic path well but leave the turned
# path and several hard-coded thresholds under-tested — which is how the
# overfitting in #10–#12 went unnoticed. These cases pin the *general*
# behaviour the algorithm should have. Where current `main` does not yet
# meet it, the test is marked xfail(strict=True) so it auto-flags (xpass)
# the moment the corresponding fix lands.
# ---------------------------------------------------------------------------


class TestTurnedPlusDrilledFlange:
    """A flange is turned (square envelope, dominant OD) yet carries discrete
    off-axis holes — the most common turned-and-drilled part. The binary
    turned/prismatic split (#10) classifies it rotational and then withholds
    every hole callout, location dim, and bolt-circle furniture, leaving the
    bolt holes with bare centre marks.
    """

    @staticmethod
    def _flange():
        # ø100 × 20 disc, ø30 central bore, 6 × ø8 holes on an ø80 bolt circle.
        flange = Cylinder(50, 20) - Cylinder(15, 20)
        for i in range(6):
            ang = 2 * math.pi * i / 6
            flange -= Pos(40 * math.cos(ang), 40 * math.sin(ang), 0) * Cylinder(4, 20)
        return flange

    @pytest.mark.timeout(60)
    def test_flange_classifies_rotational_with_od(self):
        # The turned base set is correct today and must stay so.
        dwg = build_drawing(self._flange())
        assert dwg._analysis.is_rotational
        assert "dim_od" in dwg._named
        assert "centerline_front" in dwg._named

    @pytest.mark.timeout(60)
    def test_flange_composes_od_with_bolt_circle_furniture(self):
        dwg = build_drawing(self._flange())
        # Turned base set — already works.
        assert "dim_od" in dwg._named
        # Feature-driven furniture for the bolt circle — withheld today.
        assert any(n.startswith("hc_") for n in dwg._named), "expected hole callouts"
        assert any(n.startswith("dim_loc") for n in dwg._named), "expected location dims"
        assert any(n.startswith("bc_") for n in dwg._named), "expected bolt-circle furniture"


class TestTurnedMultiBoreOverflow:
    """A turned part with 4+ distinct concentric bores. The leader stack caps
    at three (`bores[:3]`); the overflow must not vanish silently — it should
    be annotated or surfaced through the coverage lint (#10).
    """

    @staticmethod
    def _telescoping():
        # ø80 OD with four concentric counterbore steps: ø60 / ø44 / ø30 / ø16.
        part = Cylinder(40, 80)
        part -= Pos(0, 0, 30) * Cylinder(30, 20)
        part -= Pos(0, 0, 10) * Cylinder(22, 30)
        part -= Pos(0, 0, -10) * Cylinder(15, 30)
        part -= Pos(0, 0, -30) * Cylinder(8, 20)
        return part

    @pytest.mark.timeout(60)
    def test_no_bore_silently_dropped(self):
        dwg = build_drawing(self._telescoping())
        a = dwg._analysis
        bores = {d for d in a.z_diams if d != a.od_diam}
        assert bores == {60.0, 44.0, 30.0, 16.0}
        annotated = {
            float(ann.label.lstrip("ø")) for n, ann in dwg._named.items() if n.startswith("ldr_z")
        }
        # Acceptance (#10): annotate all, or surface the overflow via lint —
        # never drop a bore with no trace.
        if annotated != bores:
            assert any(i.code == "feature_not_dimensioned" for i in dwg.lint()), (
                f"bores {bores - annotated} dropped with no lint coverage"
            )


class TestStepHeightThreshold:
    """The step-height gate dimensions a step only when it projects to ≥20 mm
    on the page (`(z - bb.min.Z) * SCALE >= 20`). That page-mm cutoff is
    incidental: a genuine, well-separated step should be dimensioned whatever
    its scaled height (#13).
    """

    @staticmethod
    def _stepped(base_h):
        # Prismatic two-level block: a base of height ``base_h`` (bottom at
        # z=0) with a smaller platform on top. The single interior step face
        # sits ``base_h`` above the part bottom, so at 1:1 it projects to
        # exactly ``base_h`` mm on the page.
        base = Pos(0, 0, base_h / 2) * Box(100, 100, base_h)
        platform = Pos(0, 0, base_h + 5) * Box(60, 60, 10)
        return base + platform

    @pytest.mark.timeout(60)
    def test_step_above_page_gate_is_dimensioned(self):
        # 21 mm of page height — dimensioned. Guards the gate's upper side.
        dwg = build_drawing(self._stepped(21), scale=1.0, page="A2")
        assert any(n.startswith("dim_step") for n in dwg._named)

    @pytest.mark.timeout(60)
    def test_real_step_just_below_page_gate_still_dimensioned(self):
        dwg = build_drawing(self._stepped(19), scale=1.0, page="A2")
        assert any(n.startswith("dim_step") for n in dwg._named)


# ---------------------------------------------------------------------------
# Degenerate near-zero-radius arc sanitisation (CTC-02 "black line" fix)
# ---------------------------------------------------------------------------


class TestSanitizeSvgArcs:
    """build123d's ExportSVG writes a circle seen edge-on as an elliptical arc
    with a vanishing minor radius (ry ~ 1e-7). Renderers blow that up into a
    spurious full-page line. sanitize_svg_arcs rewrites such arcs as the straight
    line segments they actually are, leaving real-radius arcs untouched."""

    def _write(self, tmp_path, body):
        from pathlib import Path

        p = Path(tmp_path) / "t.svg"
        p.write_text(f'<svg><g id="part">{body}</g></svg>', encoding="utf-8")
        return str(p)

    @pytest.mark.timeout(30)
    def test_degenerate_arc_rewritten_to_line(self, tmp_path):
        from pathlib import Path

        from draftwright.make_drawing import sanitize_svg_arcs

        f = self._write(
            tmp_path, '<path d="M 441.547 224.55 A 3.65627 5.88651e-7 90.0 0 0 441.547 222.627" />'
        )
        n = sanitize_svg_arcs(f)
        out = Path(f).read_text(encoding="utf-8")
        assert n == 1
        assert "L 441.547 222.627" in out
        assert " A " not in out  # the degenerate arc command is gone

    @pytest.mark.timeout(30)
    def test_real_radius_arc_preserved(self, tmp_path):
        from pathlib import Path

        from draftwright.make_drawing import sanitize_svg_arcs

        arc = '<path d="M 10 10 A 5.0 5.0 0 0 1 20 20" />'
        f = self._write(tmp_path, arc)
        n = sanitize_svg_arcs(f)
        assert n == 0
        assert "A 5.0 5.0 0 0 1 20 20" in Path(f).read_text(encoding="utf-8")


class TestSilhouetteCircleRefit:
    """Imported-STEP turned features (and concentric arc features like gear-tooth
    tips) project via HLR as faceted BSpline silhouette polylines, not true
    circles (#67). ``add_view`` refits any silhouette whose vertices are
    equidistant from a recognised revolution axis back to an exact circle/arc, so
    DXF carries CIRCLE entities and feature radii read exactly. A silhouette with
    no supporting revolution axis is left untouched."""

    @staticmethod
    def _nurbs(shape):
        # NurbsConvert erases analytic surface types (Cylinder -> BSplineSurface),
        # mimicking a STEP whose turned features come in as NURBS — and forcing
        # the silhouette to project as a spline rather than a native circle.
        from build123d import Solid
        from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert

        return Solid(BRepBuilderAPI_NurbsConvert(shape.wrapped, True).Shape())

    @staticmethod
    def _circle_radii(view_compound):
        from build123d import GeomType

        return sorted(
            round(e.radius, 2) for e in view_compound.edges() if e.geom_type == GeomType.CIRCLE
        )

    @pytest.mark.timeout(120)
    def test_analytic_revolution_silhouette_is_circle(self):
        # Baseline: build123d already recovers an analytic cylinder's on-axis
        # silhouette as a circle. The refit pass must not regress this.
        from build123d import GeomType

        dwg = build_drawing(Cylinder(8, 30), page="A4")
        vis, _ = dwg.views["plan"]
        assert any(e.geom_type == GeomType.CIRCLE for e in vis.edges())

    @pytest.mark.timeout(120)
    def test_concentric_nurbs_ring_silhouette_refit_to_circle(self):
        # The inner analytic cylinder supplies the Z axis; the outer NURBS ring's
        # silhouette (a faceted BSpline at R18) is concentric, so it refits to an
        # exact circle at the true radius instead of staying a spline.
        inner = Cylinder(5, 12)
        outer = self._nurbs(Cylinder(18, 4))
        dwg = build_drawing(Compound([inner, outer]), page="A4")
        vis, _ = dwg.views["plan"]
        radii = [round(r / dwg.scale, 2) for r in self._circle_radii(vis)]
        assert 18.0 in radii  # outer NURBS rim recovered as an exact circle
        assert 5.0 in radii  # inner analytic bore

    @pytest.mark.timeout(120)
    def test_no_axis_silhouette_left_untouched(self):
        # A lone NURBS cylinder has no recognised revolution face (NurbsConvert
        # erased its analytic type), so there is no axis to refit against. The
        # silhouette must stay a spline rather than fabricate a circle.
        from build123d import GeomType

        part = self._nurbs(Cylinder(8, 30))
        dwg = build_drawing(part, page="A4")
        vis, _ = dwg.views["plan"]
        assert all(e.geom_type != GeomType.CIRCLE for e in vis.edges())


# ---------------------------------------------------------------------------
# Lint summary + surfacing of build-time annotation drops (#32)
# ---------------------------------------------------------------------------


class TestLintSummaryAndDrops:
    def test_summary_shape_is_consistent_with_lint(self):
        from build123d import Box, Cylinder

        from draftwright import build_drawing

        dwg = build_drawing(Box(80, 60, 20) - Cylinder(5, 20))
        issues = dwg.lint()
        s = dwg.lint_summary()

        assert set(s) == {
            "passed",
            "score",
            "errors",
            "warnings",
            "infos",
            "by_code",
            "geometry_issues",
            "issues",
        }
        assert s["errors"] + s["warnings"] + s["infos"] == len(issues)
        assert s["passed"] is (s["errors"] == 0)
        assert 0.0 <= s["score"] <= 1.0
        assert sum(s["by_code"].values()) == len(issues)
        assert len(s["issues"]) == len(issues)
        # A single-hole plate doesn't overflow the per-view callout cap.
        assert "callout_dropped" not in s["by_code"]

    def test_recorded_build_issue_surfaces_and_counts(self):
        from build123d import Box

        from draftwright import build_drawing

        dwg = build_drawing(Box(60, 40, 30))
        before = dwg.lint_summary()
        dwg._record_build_issue("warning", "callout_dropped", "synthetic drop")

        codes = {i.code for i in dwg.lint()}
        assert "callout_dropped" in codes

        after = dwg.lint_summary()
        assert after["warnings"] == before["warnings"] + 1
        assert after["by_code"]["callout_dropped"] == 1
        # callout_dropped is a geometry-aware code, so it lifts that count too.
        assert after["geometry_issues"] == before["geometry_issues"] + 1

    def test_dropped_callout_diameter_excluded_from_feature_lint(self):
        # The de-dup contract: a diameter recorded as a dropped callout is
        # excluded from feature_not_dimensioned, so a callout the layout could
        # not place (#36) is surfaced once (as callout_dropped) and not
        # double-reported.
        from build123d import Box, Cylinder

        from draftwright.make_drawing import lint_feature_coverage

        part = Box(60, 40, 20) - Cylinder(5, 20)  # one undimensioned ø10 bore
        base = lint_feature_coverage(part, [])
        assert any(i.code == "feature_not_dimensioned" for i in base)
        excluded = lint_feature_coverage(part, [], exclude=[10.0])
        assert not any(i.code == "feature_not_dimensioned" for i in excluded)

    @pytest.mark.timeout(120)
    def test_step_dims_are_adaptive_not_capped(self):
        # #36: no fixed 3-step cap; #45: five equal ledges form a uniform
        # staircase → one representative dim_step_typ labelled "N× rise",
        # no error-severity lint.
        from build123d import Box, Pos

        from draftwright import build_drawing

        tower = Box(120, 120, 15)
        for i in range(1, 6):
            side = 120 - i * 18
            tower += Pos(0, 0, i * 15) * Box(side, side, 15)
        dwg = build_drawing(tower)
        assert "dim_step_typ" in dwg._named, "uniform staircase should get a TYP dim"
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    def test_legible_steps_gate_drops_closely_spaced(self):
        # #41: a step is dimensioned only if tall enough from the base AND at
        # least _MIN_STEP_SEP_MM (page-mm) above the previously kept step;
        # closely-spaced shoulders are dropped (surfaced via lint), too-short
        # ones are silently omitted.
        from draftwright.make_drawing import (
            _MIN_STEP_DIM_MM,
            _MIN_STEP_SEP_MM,
            _legible_steps,
        )

        base = _MIN_STEP_DIM_MM + 5.0  # all comfortably tall enough from z=0
        zs = [base, base + 0.5, base + 1.0, base + _MIN_STEP_SEP_MM + 1.0]
        kept, n_too_close = _legible_steps(zs, 0.0, scale=1.0)
        assert kept == [base, base + _MIN_STEP_SEP_MM + 1.0]
        assert n_too_close == 2
        # A sub-legible step (too short to carry a label) is omitted, not dropped.
        kept2, n2 = _legible_steps([1.0, base], 0.0, scale=1.0)
        assert kept2 == [base]
        assert n2 == 0

    @pytest.mark.timeout(120)
    def test_location_dims_are_adaptive_not_capped(self):
        # #36: location dims have no fixed cap. Six scattered holes (distinct X
        # and Y, varied diameters so no array collapses them) get far more than
        # the old cap of four location dims, with nothing dropped — they fit.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        plate = Box(140, 90, 8)
        for x, y, r in [
            (-55, -35, 2.0),
            (-33, -12, 2.5),
            (-11, 15, 3.0),
            (12, -20, 3.5),
            (34, 28, 2.0),
            (55, 5, 2.5),
        ]:
            plate -= Pos(x, y, 0) * Cylinder(r, 8)
        dwg = build_drawing(plate)
        n_loc = len([n for n in dwg._named if n.startswith(("dim_locx", "dim_locy"))])
        assert n_loc > 4, f"expected adaptive >4 location dims, got {n_loc}"
        assert "location_ref_dropped" not in {i.code for i in dwg.lint()}

    def test_legible_locations_gate_drops_closely_spaced(self):
        # #43: a location is dimensioned only if it is at least _MIN_LOC_SEP_MM
        # (page-mm) from the previously kept one; closer ones read as one busy
        # cluster and are dropped (surfaced via lint).
        from draftwright.make_drawing import _MIN_LOC_SEP_MM, _legible_locations

        sep = _MIN_LOC_SEP_MM
        positions = [0.0, 1.0, 2.0, sep + 2.0, sep + 2.5, 2 * sep + 5.0]
        kept, n_too_close = _legible_locations(positions, scale=1.0)
        assert kept == [0.0, sep + 2.0, 2 * sep + 5.0]
        assert n_too_close == 3
        # At a larger scale the same world spacing reads fine — nothing dropped.
        kept2, n2 = _legible_locations([0.0, 1.0, 2.0], scale=10.0)
        assert kept2 == [0.0, 1.0, 2.0]
        assert n2 == 0

    @pytest.mark.timeout(120)
    def test_location_tower_trimmed_to_legible_set(self):
        # #43: many unpatterned holes with near-coincident X/Y positions trim to
        # a legible set; the rest surface as location_ref_dropped, no error lint.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        plate = Box(80, 60, 8)
        pts = [
            (-30, -20),
            (-28, 18),
            (-26, -5),
            (-10, 22),
            (-8, -22),
            (6, 10),
            (9, -15),
            (24, 20),
            (27, -8),
            (30, 4),
        ]
        for x, y in pts:
            plate -= Pos(x, y, 0) * Cylinder(1.2, 8)
        dwg = build_drawing(plate)
        codes = {i.code for i in dwg.lint()}
        n_locx = len([n for n in dwg._named if n.startswith("dim_locx")])
        n_locy = len([n for n in dwg._named if n.startswith("dim_locy")])
        assert "location_ref_dropped" in codes  # closely-spaced refs were trimmed
        assert not any(i.severity == "error" for i in dwg.lint())
        # The kept set is strictly fewer than the ten holes per axis.
        assert 0 < n_locx < 10
        assert 0 < n_locy < 10

    @pytest.mark.timeout(120)
    def test_location_gate_ignores_datum_edge_hole(self):
        # #43 follow-up: a hole on the datum edge is never dimensioned (its dim is
        # ~zero), so the gate must not anchor a cluster on it and drop a real
        # neighbour. Box centred at origin -> datum corner at (-40, -30).
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        part = Box(80, 60, 20)
        part -= Pos(-39.3, 0, 0) * Cylinder(0.4, 20)  # ~0.7 mm from datum_x: skipped
        part -= Pos(-36.5, 0, 0) * Cylinder(1.5, 20)  # ~2.8 mm from the edge hole
        dwg = build_drawing(part)
        # The real neighbour is dimensioned...
        assert any(n.startswith("dim_locx") for n in dwg._named)
        # ...and the gate did not record a spurious X spacing drop.
        x_spacing_drops = [
            i for i in dwg.lint() if i.code == "location_ref_dropped" and "X location" in i.message
        ]
        assert x_spacing_drops == []

    @pytest.mark.timeout(120)
    def test_auto_annotate_clears_stale_build_issues(self):
        # Re-annotating starts build-time lint tracking from a clean slate:
        # stale drop records from a prior pass are cleared, not accumulated.
        # (A full second pass is not idempotent — strip cursors advance — but
        # the records always reflect only the latest pass.)
        from build123d import Box

        from draftwright import build_drawing
        from draftwright.make_drawing import _auto_annotate

        dwg = build_drawing(Box(60, 40, 30))
        dwg._record_build_issue("warning", "callout_dropped", "stale")
        assert any(i.message == "stale" for i in dwg._build_issues)
        _auto_annotate(dwg, dwg._analysis)
        assert not any(i.message == "stale" for i in dwg._build_issues)
        assert dwg._dropped_callout_diams == []

    def test_repeated_lint_is_stable(self):
        # lint()/lint_summary() are idempotent — repeated calls return the same
        # issues and never accumulate the build-time drop records.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        plate = Box(120, 60, 8)
        for x, r in zip((-48, -24, 0, 24, 48), (2.0, 2.5, 3.0, 3.5, 4.0)):
            plate -= Pos(x, 0, 0) * Cylinder(r, 8)
        dwg = build_drawing(plate)
        first, second = dwg.lint(), dwg.lint()
        assert len(first) == len(second)
        assert dwg.lint_summary()["by_code"] == dwg.lint_summary()["by_code"]

    def test_placement_unsatisfiable_is_error_severity(self):
        # placement_unsatisfiable (engine could not place a wanted annotation)
        # is error-severity, so it fails the `passed` gate.
        from build123d import Box

        from draftwright import build_drawing

        dwg = build_drawing(Box(60, 40, 30))
        assert dwg.lint_summary()["passed"] is True
        dwg._record_build_issue("error", "placement_unsatisfiable", "synthetic")
        s = dwg.lint_summary()
        assert s["passed"] is False
        assert s["errors"] >= 1
        assert s["by_code"]["placement_unsatisfiable"] == 1


# ---------------------------------------------------------------------------
# Layout generalisation guards (#13) — pin the *general* behaviour the
# algorithm should have on turned/hybrid parts and at the step-legibility
# boundary, so the overfitting that #10–#12/#31 removed cannot creep back.
# ---------------------------------------------------------------------------


class TestLayoutGeneralisation:
    @pytest.mark.timeout(120)
    def test_turned_flange_gets_both_od_and_hole_furniture(self):
        # A turned-and-drilled flange (cylinder OD + centre bore + bolt circle)
        # must get the turned base set (OD dim + centrelines) AND the drilled
        # furniture (hole callout + pitch circle) — not one or the other. This
        # is the feature-presence composition from #10, on a genuinely
        # rotational part rather than a prismatic plate.
        import math

        from build123d import Cylinder, Pos

        from draftwright import build_drawing

        flange = Cylinder(radius=40, height=10) - Cylinder(radius=8, height=10)
        for i in range(6):
            ang = math.radians(60 * i)
            flange -= Pos(28 * math.cos(ang), 28 * math.sin(ang), 0) * Cylinder(2.5, 10)
        dwg = build_drawing(flange)

        assert dwg._analysis.is_rotational, "flange should classify as rotational"
        # Turned base set.
        assert "dim_od" in dwg._named
        assert "centerline_front" in dwg._named
        assert "centerline_side" in dwg._named
        # Drilled furniture.
        assert any(n.startswith("hc_") for n in dwg._named), "expected a hole callout"
        assert any(n.startswith("bc_") for n in dwg._named), "expected a pitch circle"
        # No error-severity lint (warnings tolerated).
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    @pytest.mark.timeout(120)
    def test_turned_flange_dimensions_all_its_bores(self):
        # #36: no per-view callout cap — a turned part with five distinct bores
        # gets a callout for every one (was capped at four largest), more than
        # the old cap, with nothing dropped because they fit.
        import math

        from build123d import Cylinder, Pos

        from draftwright import build_drawing

        flange = Cylinder(radius=45, height=10) - Cylinder(radius=8, height=10)
        for i, r in enumerate((2.0, 2.5, 3.0, 3.5, 4.0)):
            ang = math.radians(72 * i)
            flange -= Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(r, 10)
        dwg = build_drawing(flange)

        n_callouts = len([n for n in dwg._named if n.startswith("hc_")])
        assert n_callouts > 4, f"expected adaptive >4 callouts, got {n_callouts}"
        assert "callout_dropped" not in {i.code for i in dwg.lint()}

    @pytest.mark.timeout(120)
    def test_step_height_legibility_threshold(self):
        # The step-height dimension gate is the legibility constant
        # (_MIN_STEP_DIM_MM), not an incidental cutoff: a shoulder whose
        # page-projected height falls just below the gate gets no step dim;
        # just above, it does. Pin the gate, not a magic millimetre value.
        from build123d import Cylinder, Pos

        from draftwright import build_drawing
        from draftwright.make_drawing import _MIN_STEP_DIM_MM

        def shaft_with_shoulder_at(length):
            # Lower segment height == `length`; shoulder sits `length` above the
            # base (bb.min.Z), so legibility = length * SCALE.
            return Pos(0, 0, length / 2) * Cylinder(22, length) + Pos(
                0, 0, length + 12.5
            ) * Cylinder(11, 25)

        for length, expect in ((12.0, False), (13.0, True)):
            dwg = build_drawing(shaft_with_shoulder_at(length))
            a = dwg._analysis
            legible = length * a.SCALE >= _MIN_STEP_DIM_MM
            assert legible is expect, (
                f"length={length} scale={a.SCALE}: legibility expectation wrong"
            )
            has_step = "dim_step_0" in dwg._named
            assert has_step is expect, (
                f"length={length}: step dim present={has_step}, expected {expect}"
            )


def _crowded_shoulder_part():
    """A tall, narrow stacked-tier block whose shoulders are 3 mm apart in Z.

    At the auto sheet scale the step-legibility gate (#41) drops at least one
    shoulder (3 mm × scale < _MIN_STEP_SEP_MM), which is exactly the trigger for
    the enlarged detail view (#42). Narrow X/Y so the detail footprint fits the
    free space on the sheet.
    """
    parts = [Pos(0, 0, 3) * Box(20, 16, 6)]  # base plate
    z = 6
    for w in (16, 13, 10, 7, 5):
        h = 3
        parts.append(Pos(0, 0, z + h / 2) * Box(w, 12, h))
        z += h
    part = parts[0]
    for p in parts[1:]:
        part = part + p
    return part


@pytest.mark.timeout(120)
class TestDetailView:
    def test_detail_view_off_by_default(self):
        # detail_view=False (default) — no detail view even when shoulders are crowded.
        dwg = build_drawing(_crowded_shoulder_part())
        assert "detail_a" not in dwg.views
        assert "detail_caption" not in dwg._named
        assert not any(n.startswith("dim_detail") for n in dwg._named)

    def test_crowded_shoulders_get_a_detail_view_when_requested(self):
        from draftwright.make_drawing import _legible_steps

        dwg = build_drawing(_crowded_shoulder_part(), detail_view=True)
        a = dwg._analysis
        # Pin the trigger: the gate must actually drop at least one shoulder at
        # the chosen scale, otherwise the test is not exercising #42.
        _, n_dropped = _legible_steps(a.step_zs, a.bb.min.Z, a.SCALE)
        assert n_dropped >= 1
        # The detail view, its caption, and at least one detail step dim exist.
        assert "detail_a" in dwg.views
        assert "detail_caption" in dwg._named
        assert any(n.startswith("dim_detail_step") for n in dwg._named)
        # Drawn at a larger scale than the sheet.
        assert dwg._coords["detail_a"]._scale > a.SCALE
        # No error-severity lint introduced.
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    def test_plain_part_gets_no_detail_view(self):
        dwg = build_drawing(Box(60, 40, 20))
        assert "detail_a" not in dwg.views
        assert "detail_caption" not in dwg._named
        assert not any(n.startswith("dim_detail") for n in dwg._named)
        assert [i for i in dwg.lint() if i.severity == "error"] == []


# ---------------------------------------------------------------------------
# Issue #45: TYP / representative dimensioning for uniform step patterns
# ---------------------------------------------------------------------------


def _uniform_staircase(n_treads=8, rise=15.0, going=20.0, width=30.0):
    """Return a staircase solid with *n_treads* treads of equal rise and going."""
    part = None
    for i in range(n_treads):
        h = (i + 1) * rise
        w = (n_treads - i) * going
        b = Pos(w / 2, 0, h / 2) * Box(w, width, h)
        part = b if part is None else part + b
    return part


class TestTypDimensioning:
    """#45: uniform staircase → single representative dim labelled N× rise."""

    def test_uniform_staircase_gets_typ_dim(self):
        dwg = build_drawing(_uniform_staircase(n_treads=8, rise=15.0))
        named = dwg._named
        assert "dim_step_typ" in named, "expected a single representative step dim"
        assert not any(k.startswith("dim_step_") and k != "dim_step_typ" for k in named)
        assert named["dim_step_typ"].label == "8× 15"
        assert "dim_height" in named
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    def test_typ_label_fractional_rise(self):
        dwg = build_drawing(_uniform_staircase(n_treads=5, rise=12.5, going=18.0))
        assert "dim_step_typ" in dwg._named
        assert dwg._named["dim_step_typ"].label == "5× 12.5"

    def test_irregular_staircase_gets_per_step_dims(self):
        # Non-uniform rises → fall back to per-step ladder.
        # Build as union of full-height slabs with decreasing footprint so
        # OpenCASCADE produces horizontal tread faces at each step level.
        cum_zs = [10.0, 30.0, 40.0, 65.0, 80.0]  # deliberately irregular
        n = len(cum_zs)
        going = 20
        part = None
        for i, total_h in enumerate(cum_zs):
            w = (n - i) * going
            b = Pos(w / 2, 0, total_h / 2) * Box(w, 30, total_h)
            part = b if part is None else part + b
        dwg = build_drawing(part)
        assert "dim_step_typ" not in dwg._named
        assert any(k.startswith("dim_step_") and k != "dim_step_typ" for k in dwg._named)

    def test_two_step_part_not_detected_as_pattern(self):
        # Only 2 interior steps → below the ≥3 threshold; per-step path used.
        from draftwright.make_drawing import _detect_step_repeat

        step_zs = [10.0, 20.0]
        result = _detect_step_repeat(step_zs, 0.0, 30.0)
        assert result is None

    def test_detect_step_repeat_uniform(self):
        from draftwright.make_drawing import _detect_step_repeat

        zs = [15.0, 30.0, 45.0, 60.0, 75.0, 90.0, 105.0]
        n, rise = _detect_step_repeat(zs, 0.0, 120.0)
        assert n == 8
        assert abs(rise - 15.0) < 0.01

    def test_detect_step_repeat_nonuniform(self):
        from draftwright.make_drawing import _detect_step_repeat

        zs = [10.0, 25.0, 35.0, 60.0]
        assert _detect_step_repeat(zs, 0.0, 70.0) is None

    def test_detect_step_repeat_top_gap_mismatch_excluded_from_count(self):
        # When top gap doesn't match the mean rise, n = len(step_zs) not +1.
        from draftwright.make_drawing import _detect_step_repeat

        zs = [10.0, 20.0, 30.0]  # 3 equal interior rises of 10mm
        # top gap = 55 - 30 = 25 ≠ 10 → should NOT add 1
        n, rise = _detect_step_repeat(zs, 0.0, 55.0)
        assert n == 3
        assert abs(rise - 10.0) < 0.01

    def test_three_step_part_gets_typ_dim(self):
        # Integration: exactly 3 interior steps (the minimum threshold) → TYP path.
        dwg = build_drawing(_uniform_staircase(n_treads=4, rise=20.0))
        assert "dim_step_typ" in dwg._named
        assert not any(k.startswith("dim_step_") and k != "dim_step_typ" for k in dwg._named)


# ---------------------------------------------------------------------------
# Issues #26 + #25: dwg.features() and dwg.place_dim()
# ---------------------------------------------------------------------------


def _holed_plate():
    """80×60×20 plate: 4 corner ø10 through-holes (Z-axis) + 1 centre ø6 blind (Z-axis)."""
    return (
        Box(80, 60, 20)
        - Pos(25, 20, 0) * Cylinder(5, 20)
        - Pos(-25, 20, 0) * Cylinder(5, 20)
        - Pos(25, -20, 0) * Cylinder(5, 20)
        - Pos(-25, -20, 0) * Cylinder(5, 20)
        - Pos(0, 0, 5) * Cylinder(3, 10)
    )


class TestFeatures:
    """#26: dwg.features(view) exposes analysis to scripts."""

    def test_feature_info_importable_from_top_level(self):
        from draftwright import FeatureInfo

        f = FeatureInfo(
            type="hole", page_pos=(1.0, 2.0), diameter=5.0, through=True, depth=None, count=1
        )
        assert f.type == "hole"
        assert f.count == 1

    def test_z_axis_holes_appear_in_plan_view(self):
        dwg = build_drawing(_holed_plate())
        feats = dwg.features("plan")
        assert len(feats) == 2  # ø10 group (×4) + ø6 group (×1)
        diams = {f.diameter for f in feats}
        assert diams == {10.0, 6.0}

    def test_through_and_blind_correctly_classified(self):
        dwg = build_drawing(_holed_plate())
        feats = {f.diameter: f for f in dwg.features("plan")}
        assert feats[10.0].through is True
        assert feats[10.0].depth is None
        assert feats[6.0].through is False
        assert feats[6.0].depth == 10.0

    def test_count_groups_identical_holes(self):
        dwg = build_drawing(_holed_plate())
        feats = {f.diameter: f for f in dwg.features("plan")}
        assert feats[10.0].count == 4
        assert feats[6.0].count == 1

    def test_page_pos_is_in_plan_view_coordinate_range(self):
        dwg = build_drawing(_holed_plate())
        a = dwg._analysis
        feats = dwg.features("plan")
        for f in feats:
            px, py = f.page_pos
            # page_pos must lie within the plan view bounds (within half-extents + margin)
            assert abs(px - a.PV_X) <= a.x_size / 2 * a.SCALE + 5
            assert abs(py - a.PV_Y) <= a.y_size / 2 * a.SCALE + 5

    def test_z_axis_holes_absent_from_front_view(self):
        # The holed plate has only Z-axis holes — none should appear in front
        dwg = build_drawing(_holed_plate())
        assert dwg.features("front") == []

    def test_unknown_view_returns_empty(self):
        dwg = build_drawing(Box(40, 30, 20))
        assert dwg.features("nonsense") == []

    def test_no_analysis_returns_empty(self):
        from draftwright import Drawing

        dwg = Drawing(
            scale=1.0,
            page_w=297,
            page_h=210,
            tb_w=100,
            draft=None,
            look_at=(0, 0, 0),
            dist=100,
            centroid=(0, 0, 0),
            out="",
        )
        assert dwg.features("plan") == []


class TestPlaceDim:
    """#25: dwg.place_dim() stacks with the auto-dimension strip."""

    def test_place_dim_adds_named_annotation(self):
        dwg = build_drawing(Box(80, 60, 20))
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        dwg.place_dim(p1, p2, "below", "plan", dwg.draft, name="my_dim", label="80")
        assert "my_dim" in dwg._named
        assert dwg._named["my_dim"].label == "80"

    def test_place_dim_returns_dimension_object(self):
        from build123d_drafting.helpers import Dimension

        dwg = build_drawing(Box(60, 40, 20))
        p1 = dwg.at("front", -30, 0, -10)
        p2 = dwg.at("front", 30, 0, -10)
        result = dwg.place_dim(p1, p2, "below", "front", dwg.draft)
        assert isinstance(result, Dimension)

    def test_two_place_dim_calls_stack_without_overlap(self):
        # Two dims on the same strip must land at different page positions.
        # Use auto_dims=False so the strip has no prior allocations, and
        # "above" where there is ample headroom for two consecutive allocations.
        dwg = build_drawing(Box(80, 60, 20), auto_dims=False)
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        d1 = dwg.place_dim(p1, p2, "above", "plan", dwg.draft, name="d1")
        d2 = dwg.place_dim(p1, p2, "above", "plan", dwg.draft, name="d2")
        # dim_level_y is the y-coordinate of the dim line on the page;
        # two stacked dims must land at different y values.
        assert d1.dim_level_y != d2.dim_level_y

    def test_place_dim_no_analysis_falls_back_to_slot(self):
        # _analysis is None → no strip available → falls back to slot offset, no error.
        from build123d_drafting.helpers import Dimension, draft_preset

        from draftwright import Drawing

        d = draft_preset(font_size=3.0, decimal_precision=1)
        dwg = Drawing(
            scale=1.0,
            page_w=297,
            page_h=210,
            tb_w=100,
            draft=d,
            look_at=(0, 0, 0),
            dist=100,
            centroid=(0, 0, 0),
            out="",
        )
        result = dwg.place_dim((0, 0, 0), (80, 0, 0), "below", "plan", d, slot=8.0)
        assert isinstance(result, Dimension)


# ---------------------------------------------------------------------------
# Issue #29: lint findings carry a suggested-fix code snippet
# ---------------------------------------------------------------------------


class TestLintSuggestions:
    """#29: each LintIssue carries a `suggestion` (str | None) with a fix snippet."""

    def test_feature_not_dimensioned_has_suggestion(self):
        # auto_dims=False leaves the ø10 hole undimensioned → coverage lint fires.
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(5, 20)
        dwg = build_drawing(part, auto_dims=False)
        issues = [i for i in dwg.lint() if i.code == "feature_not_dimensioned"]
        assert issues, "expected a feature_not_dimensioned issue"
        sug = issues[0].suggestion
        assert sug is not None
        assert "dwg.features(" in sug
        assert "HoleCallout(" in sug
        assert "Leader(" in sug

    def test_feature_not_dimensioned_suggestion_is_runnable(self):
        # The headline #29 promise: paste the snippet and the lint resolves.
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(5, 20)
        dwg = build_drawing(part, auto_dims=False)
        assert any(i.code == "feature_not_dimensioned" for i in dwg.lint())

        for f in dwg.features("plan"):
            if abs(f.diameter - 10.0) < 1e-6:
                callout = HoleCallout(
                    f.diameter, count=f.count, through=f.through, depth=f.depth, draft=dwg.draft
                )
                elbow = (f.page_pos[0] + 15, f.page_pos[1] + 10, 0)
                leader = Leader((*f.page_pos, 0), elbow, "", dwg.draft, callout=callout)
                dwg.add(leader, name="hole_10")

        assert not any(i.code == "feature_not_dimensioned" for i in dwg.lint())

    def test_clean_drawing_has_no_suggestions(self):
        # A fully auto-dimensioned plain box should lint clean → no suggestions.
        dwg = build_drawing(Box(60, 40, 20))
        for i in dwg.lint():
            assert i.suggestion is None

    def test_lint_summary_omits_none_suggestion(self):
        # A clean box: issue dicts (if any) must not carry a suggestion key.
        dwg = build_drawing(Box(60, 40, 20))
        for d in dwg.lint_summary()["issues"]:
            assert "suggestion" not in d

    def test_lint_summary_includes_present_suggestion(self):
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(5, 20)
        dwg = build_drawing(part, auto_dims=False)
        dicts = [d for d in dwg.lint_summary()["issues"] if d["code"] == "feature_not_dimensioned"]
        assert dicts
        assert "suggestion" in dicts[0]
        assert dicts[0]["suggestion"]

    def test_step_dim_dropped_suggestion_mentions_detail_view(self):
        dwg = build_drawing(_crowded_shoulder_part())
        issues = [i for i in dwg.lint() if i.code == "step_dim_dropped"]
        assert issues, "crowded shoulders should drop a step dim"
        assert "detail_view=True" in issues[0].suggestion

    def test_annotation_overlap_suggestion_uses_place_dim(self):
        # Synthetic issue — exercise the _suggest_fix branch directly.
        from build123d_drafting.helpers import LintIssue

        from draftwright.make_drawing import _suggest_fix

        dwg = build_drawing(Box(60, 40, 20))
        issue = LintIssue(
            severity="warning",
            message="labels 'dim_width' and 'dim_height' overlap by 3.0×2.0 mm",
            code="annotation_overlap",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "place_dim" in sug
        assert "dim_width" in sug

    def test_dim_inside_part_suggestion_uses_place_dim(self):
        from build123d_drafting.helpers import LintIssue

        from draftwright.make_drawing import _suggest_fix

        dwg = build_drawing(Box(60, 40, 20))
        issue = LintIssue(
            severity="warning",
            message="Dim 'dim_height': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "place_dim" in sug
        assert "dim_height" in sug

    def test_unknown_code_has_no_suggestion(self):
        from build123d_drafting.helpers import LintIssue

        from draftwright.make_drawing import _suggest_fix

        dwg = build_drawing(Box(60, 40, 20))
        issue = LintIssue(severity="info", message="something", code="some_unhandled_code")
        assert _suggest_fix(issue, dwg) is None

    def test_non_integer_diameter_still_gets_suggestion(self):
        # Regression guard for the 1e-6-vs-_fmt bug: radius 4.111 gives a raw
        # diameter of 8.22, but the message reports the 1dp-rounded ø8.2 — a
        # 0.02 gap that a 1e-6 match would drop. The diameter must round-trip
        # with tolerance so the suggestion still appears.
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(4.111, 20)
        dwg = build_drawing(part, auto_dims=False)
        issues = [i for i in dwg.lint() if i.code == "feature_not_dimensioned"]
        assert issues
        assert "ø8.2" in issues[0].message  # rounded, differs from raw 8.22
        assert issues[0].suggestion is not None
        assert 'dwg.features("plan")' in issues[0].suggestion

    def test_feature_count_mismatch_suggestion_sets_count(self):
        # The leading number is `need`; diameter digits (even fractional) must
        # not interfere with the parse.
        from build123d_drafting.helpers import LintIssue

        from draftwright.make_drawing import _suggest_fix

        dwg = build_drawing(Box(60, 40, 20))
        issue = LintIssue(
            severity="warning",
            message="4 ø8.5 features on the part but callouts account for 1",
            code="feature_count_mismatch",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "count=4" in sug


class TestRepair:
    """#30: bounded lint→repair loop acts on violations instead of only reporting."""

    def test_repair_clears_annotation_overlap(self):
        # Two dimensions forced onto the same page location → their labels
        # collide; the repair loop pushes one further out to separate them.
        from draftwright.make_drawing import _dim

        dwg = build_drawing(Box(60, 40, 20))
        d = dwg.draft
        p1, p2 = (40.0, 20.0, 0.0), (80.0, 20.0, 0.0)
        dwg.add(_dim(p1, p2, "above", 8, d, label="AA"), "ov1")
        dwg.add(_dim(p1, p2, "above", 8, d, label="BB"), "ov2")
        assert [i for i in dwg.lint() if i.code == "annotation_overlap"]

        dwg.repair()
        assert not [i for i in dwg.lint() if i.code == "annotation_overlap"]

    def test_repair_dim_inside_part_flips_side(self):
        # dim_inside_part is dormant in the multi-view sheet (lint passes no
        # part_bbox), so drive the repair directly: a wrong-side dim flips to
        # the opposite side and keeps its name binding.
        from build123d_drafting.helpers import LintIssue

        from draftwright.make_drawing import _dim

        dwg = build_drawing(Box(60, 40, 20))
        dim = dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="INSIDE"), "x")
        assert dim._dw_spec.side == "above"

        issue = LintIssue(
            severity="warning",
            message="Dim 'INSIDE': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        assert dwg._repair_dim_inside_part(issue) is True
        new = dwg._named["x"]
        assert new is not dim
        assert new._dw_spec.side == "below"
        assert new in dwg.items and dim not in dwg.items

    def test_repair_inside_part_attempted_once_no_oscillation(self):
        # A side flip that does not help must not be re-flipped (oscillation).
        # The same label is only flipped once across the whole loop.
        from build123d_drafting.helpers import LintIssue

        from draftwright.make_drawing import _dim

        dwg = build_drawing(Box(60, 40, 20))
        dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="OSC"), "x")

        # Monkeypatch lint to always report the same dim_inside_part.
        issue = LintIssue(
            severity="warning",
            message="Dim 'OSC': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        dwg.lint = lambda: [issue]
        dwg.repair(max_iter=5)
        # Flipped exactly once → ends on "below", not back to "above".
        assert dwg._named["x"]._dw_spec.side == "below"

    def test_repair_idempotent_on_clean_drawing(self):
        # build_drawing already repairs by default, so a second pass is a no-op:
        # same objects, same order.
        dwg = build_drawing(Box(60, 40, 20))
        before = [id(o) for o in dwg.items]
        assert dwg.repair() is dwg
        assert [id(o) for o in dwg.items] == before

    def test_repair_does_not_increase_issue_counts(self):
        # Acceptance: on the existing fixtures, error+warning counts after the
        # repair pass are <= the raw greedy placement — no regressions.
        def ew(dwg):
            return sum(1 for i in dwg.lint() if i.severity in ("error", "warning"))

        for part in (Box(60, 40, 20), _holed_plate(), _uniform_staircase()):
            raw = ew(build_drawing(part, repair=False))
            fixed = ew(build_drawing(part, repair=True))
            assert fixed <= raw

    def test_repair_rolls_back_a_pass_that_makes_things_worse(self):
        # Guard: if a repair (e.g. an overlap push off a tight sheet) net-raises
        # the issue count, that pass is undone and the loop stops — repair never
        # makes a drawing worse. Drive it with a stateful lint stub: one fixable
        # overlap before, two issues after the push.
        from build123d_drafting.helpers import LintIssue

        from draftwright.make_drawing import _dim

        dwg = build_drawing(Box(60, 40, 20))
        orig = dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="RB"), "x")
        overlap = LintIssue(
            severity="warning",
            message="labels 'RB' and 'QQ' overlap",
            code="annotation_overlap",
        )
        worse = LintIssue(
            severity="warning", message="label 'RB' off sheet", code="label_out_of_frame"
        )
        calls = {"n": 0}

        def fake_lint():
            calls["n"] += 1
            return [overlap] if calls["n"] == 1 else [overlap, worse]

        dwg.lint = fake_lint
        dwg.repair(max_iter=3)
        # The pushed dim was reverted: 'x' is the original object, offset intact.
        assert dwg._named["x"] is orig
        assert dwg._named["x"]._dw_spec.distance == 8

    def test_build_drawing_repair_flag_is_respected(self):
        # repair=False leaves the greedy placement untouched; the default repairs.
        from draftwright.make_drawing import _dim

        # A clean part is identical either way (nothing to repair).
        a = build_drawing(Box(60, 40, 20), repair=False)
        b = build_drawing(Box(60, 40, 20), repair=True)
        assert [getattr(o, "label", None) for o in a.items] == [
            getattr(o, "label", None) for o in b.items
        ]
        # The factory tags engine dims so repair can re-place them.
        d = _dim((0, 0, 0), (40, 0, 0), "above", 8, a.draft, label="Z")
        assert d._dw_spec.side == "above"


class TestPin:
    """#89: a pinned annotation is never moved by the engine (repair today)."""

    def _two_overlapping(self):
        from draftwright.make_drawing import _dim

        dwg = build_drawing(Box(60, 40, 20))
        p1, p2 = (40.0, 20.0, 0.0), (80.0, 20.0, 0.0)
        dwg.add(_dim(p1, p2, "above", 8, dwg.draft, label="AA"), "a")
        dwg.add(_dim(p1, p2, "above", 8, dwg.draft, label="BB"), "b")
        return dwg

    def test_repair_does_not_move_a_pinned_dim(self):
        # 'a' is the first re-placeable in the overlap, so repair would push it;
        # pinned, it stays at distance 8 and 'b' is pushed instead.
        dwg = self._two_overlapping()
        dwg.pin("a")
        dwg.repair()
        assert dwg._named["a"]._dw_spec.distance == 8  # untouched
        assert dwg._named["b"]._dw_spec.distance > 8  # moved in its place

    def test_unpin_lets_repair_move_it_again(self):
        dwg = self._two_overlapping()
        dwg.pin("a").unpin("a")
        dwg.repair()
        # With nothing pinned, the overlap is resolved (one of them moved).
        assert not [i for i in dwg.lint() if i.code == "annotation_overlap"]

    def test_pin_unknown_name_raises(self):
        dwg = build_drawing(Box(60, 40, 20))
        with pytest.raises(KeyError):
            dwg.pin("does_not_exist")

    def test_pin_and_unpin_are_chainable(self):
        dwg = self._two_overlapping()
        assert dwg.pin("a") is dwg
        assert dwg.unpin("a") is dwg

    def test_placeable_locked_defaults_false(self):
        from draftwright.layout import Placeable

        p = Placeable("k", ((0, 0),), (4, 2), "y", 0.0, 5.0)
        assert p.locked is False
        assert Placeable("k", ((0, 0),), (4, 2), "y", 0.0, 5.0, locked=True).locked is True

    def test_pinning_both_overlap_labels_is_a_noop(self):
        # Both deliberate → the engine respects both and leaves the overlap.
        dwg = self._two_overlapping()
        dwg.pin("a").pin("b")
        dwg.repair()
        assert dwg._named["a"]._dw_spec.distance == 8
        assert dwg._named["b"]._dw_spec.distance == 8

    def test_pinning_a_non_dim_then_repair_does_not_crash(self):
        # _find_dim builds an id-set over pinned objects of any type; pinning a
        # Leader (not a re-placeable dim) must not break repair.
        dwg = self._two_overlapping()
        dwg.add(Leader((0, 0, 0), (10, 10, 0), "L", dwg.draft), "ldr")
        dwg.pin("ldr")
        dwg.repair()  # must not raise
        assert "ldr" in dwg.annotations()

    def test_removed_then_readded_name_is_not_still_pinned(self):
        from draftwright.make_drawing import _dim

        dwg = self._two_overlapping()
        dwg.pin("a")
        dwg.remove("a")
        # Re-add a fresh "a" at the same overlapping spot; it must NOT inherit
        # the old pin, so repair is free to move it.
        dwg.add(_dim((40.0, 20.0, 0.0), (80.0, 20.0, 0.0), "above", 8, dwg.draft, label="AA"), "a")
        assert "a" not in dwg._pinned
        dwg.repair()
        assert not [i for i in dwg.lint() if i.code == "annotation_overlap"]


class TestAnnotationsQuery:
    """#27: introspect existing annotations by name and type."""

    def test_annotations_maps_name_to_type(self):
        dwg = build_drawing(Box(60, 40, 20))
        anns = dwg.annotations()
        # A dict keyed by the names actually registered, valued by class name.
        assert isinstance(anns, dict)
        assert anns  # a box drawing has named annotations
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in anns.items())
        # Every key resolves, and its reported type matches the live object.
        for name, type_name in anns.items():
            assert type(dwg._named[name]).__name__ == type_name

    def test_annotations_omits_unnamed(self):
        from draftwright.make_drawing import _dim

        dwg = build_drawing(Box(60, 40, 20))
        before = dict(dwg.annotations())
        dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="U"))  # no name
        # Unnamed annotation lands in items but not in the name→type map.
        assert dwg.annotations() == before
        assert len(dwg.items) == len(before) + 1

    def test_annotations_reflects_add_and_membership(self):
        from draftwright.make_drawing import _dim

        dwg = build_drawing(Box(60, 40, 20))
        assert "q_dim" not in dwg.annotations()
        dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="Q"), "q_dim")
        assert dwg.annotations()["q_dim"] == "Dimension"

    def test_get_annotation_returns_object_or_none(self):
        from draftwright.make_drawing import _dim

        dwg = build_drawing(Box(60, 40, 20))
        obj = dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="G"), "g")
        assert dwg.get_annotation("g") is obj
        assert dwg.get_annotation("does_not_exist") is None

    def test_get_annotation_follows_remove(self):
        from draftwright.make_drawing import _dim

        dwg = build_drawing(Box(60, 40, 20))
        dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="R"), "r")
        assert dwg.get_annotation("r") is not None
        dwg.remove("r")
        assert dwg.get_annotation("r") is None
        assert "r" not in dwg.annotations()


class TestViewBounds:
    """#28: page bounding box of a named view's projected geometry."""

    def test_view_bounds_returns_page_bbox(self):
        dwg = build_drawing(Box(60, 40, 20))
        b = dwg.view_bounds("front")
        assert b is not None and len(b) == 4
        x0, y0, x1, y1 = b
        assert x1 > x0 and y1 > y0
        # Front view (looking along Y) shows X=60 wide, Z=20 tall, at sheet scale.
        assert (x1 - x0) == pytest.approx(60 * dwg.scale, rel=1e-3)
        assert (y1 - y0) == pytest.approx(20 * dwg.scale, rel=1e-3)

    def test_view_bounds_contains_projected_centroid(self):
        # The part centroid (world origin for a centred Box) projects inside.
        dwg = build_drawing(Box(60, 40, 20))
        x0, y0, x1, y1 = dwg.view_bounds("front")
        px, py, _ = dwg.at("front", 0, 0, 0)
        assert x0 <= px <= x1
        assert y0 <= py <= y1

    def test_view_bounds_unknown_view_is_none(self):
        dwg = build_drawing(Box(60, 40, 20))
        assert dwg.view_bounds("does_not_exist") is None

    def test_view_bounds_for_each_standard_view(self):
        dwg = build_drawing(Box(60, 40, 20))
        for v in ("front", "plan", "side", "iso"):
            b = dwg.view_bounds(v)
            assert b is not None, v
            x0, y0, x1, y1 = b
            assert x1 > x0 and y1 > y0, v

    def test_view_bounds_includes_hidden_lines(self):
        # Bounds union the visible and hidden silhouettes. Replace the front
        # view's hidden compound with one that extends past the visible box and
        # confirm the right edge moves out to it.
        dwg = build_drawing(Box(60, 40, 20))
        vis, _ = dwg.views["front"]
        _, _, x1, _ = dwg.view_bounds("front")
        far = Compound(children=[Edge.make_line((x1 + 10, 0, 0), (x1 + 10, 5, 0))])
        dwg.views["front"] = (vis, far)
        assert dwg.view_bounds("front")[2] == pytest.approx(x1 + 10)


def _x_stepped_shaft():
    """A turned shaft lying along X: ø30 (len 40) then ø16 (len 30).

    Built about Z then rotated so the turning axis is X — the orientation that
    is *not* flagged rotational (the OD logic is Z-centric), exercising #77.
    """
    return Rotation(0, 90, 0) * (Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(8, 30))


class TestTurnedDiameters:
    """#77: external turned diameters (X-axis turning) get ø leader callouts."""

    def test_each_external_diameter_gets_a_callout(self):
        dwg = build_drawing(_x_stepped_shaft())
        labels = {o.label for n, o in dwg._named.items() if n.startswith("ldr_d")}
        assert "ø30" in labels
        assert "ø16" in labels

    def test_no_feature_not_dimensioned_left(self):
        # The whole point: the external diameters no longer lint as uncovered.
        dwg = build_drawing(_x_stepped_shaft())
        codes = dwg.lint_summary()["by_code"]
        assert codes.get("feature_not_dimensioned", 0) == 0

    def test_callouts_are_leaders_on_the_constraint_solver(self):
        # Placed via _solve_strip_ys (ADR 0003 layer-2), so two distinct
        # diameters never share an x and never collide: label xs are min_gap
        # apart and inside the front view's page bounds.
        dwg = build_drawing(_x_stepped_shaft())
        leaders = [o for n, o in dwg._named.items() if n.startswith("ldr_d")]
        assert len(leaders) >= 2
        xs = sorted(ldr.elbow[0] for ldr in leaders)
        assert all(b - a > 1.0 for a, b in zip(xs, xs[1:]))  # spread, not stacked

    def test_z_rotational_part_is_untouched(self):
        # A Z-axis turned part keeps its existing OD/bore path: the new pass is
        # a no-op (no X-axis bosses), so no ldr_d callouts appear.
        dwg = build_drawing(Cylinder(15, 40))  # plain Z disc/shaft
        assert not any(n.startswith("ldr_d") for n in dwg._named)

    def test_unfittable_row_skips_without_crashing(self, monkeypatch):
        # When the labels do not fit the row, both solvers return None; the pass
        # must skip gracefully, not crash the whole build on a None unpack.
        import sys

        m = sys.modules["draftwright.make_drawing"]  # __init__ shadows the submodule
        monkeypatch.setattr(m, "_solve_strip_ys", lambda *a, **k: None)
        monkeypatch.setattr(m, "_greedy_strip_ys", lambda *a, **k: None)
        dwg = build_drawing(_x_stepped_shaft())  # must not raise
        assert not any(n.startswith("ldr_d") for n in dwg._named)
