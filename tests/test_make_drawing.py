"""Tests for draftwright.make_drawing."""

from pathlib import Path

import pytest
from build123d import Box, Compound, Cylinder, Edge, Pos, export_step
from build123d_drafting import Leader, ViewCoordinates, view_axes

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
        # 28 × 8.5 × 12.5 mm (issue #62 part) → 5:1 on A3, not 1:1 on A4
        scale, pw, ph, tbw = choose_scale(28, 8.5, 12.5)
        assert scale == 5.0
        assert int(pw) == 420

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

    def test_scale_only_picks_smallest_fitting_page(self):
        scale, pw, ph, tbw = choose_scale(28, 8.5, 12.5, scale=2)
        assert scale == 2.0
        assert int(pw) == 297

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

    def test_right_strip_outer_limits_tightened_to_iso(self):
        # fv.right and pv.right are both bounded by sv_left_edge so bore callout
        # labels cannot cross into the side view.  sv.right is tightened to the
        # actual iso view left edge (iso_x0 - 4) by _auto_annotate().
        # Use a plain box (no holes) so bore callout overhead doesn't push the
        # iso view right and interfere with the sv tightening check.
        from build123d import Box

        from draftwright import build_drawing
        from draftwright.make_drawing import _iso_bbox

        part = Box(80, 60, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        sv_left = a.SV_X - a.sv_hw
        iso_x0, _, _, _ = _iso_bbox(dwg)
        iso_limit = iso_x0 - 4
        # fv right must not extend past the side view left edge
        assert a.fv_zones.right.outer_limit == pytest.approx(sv_left, abs=0.1)
        # pv right is also bounded by sv_left so bore callout labels cannot
        # cross dim_locy extension lines in the side view corridor
        assert a.pv_zones.right.outer_limit == pytest.approx(sv_left, abs=0.1)
        # sv right strip is iso-tightened
        assert a.sv_zones.right.outer_limit == pytest.approx(iso_limit, abs=0.1)

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

    def test_right_depth_capped_at_three_steps(self):
        from draftwright.make_drawing import _est_right_strip_depth

        # n_steps is capped at 3 in the estimator
        assert _est_right_strip_depth(3) == _est_right_strip_depth(10)

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


# ---------------------------------------------------------------------------
# Drawing builder (build_drawing / Drawing / add_view)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_build_drawing_returns_populated_drawing(tmp_path):
    dwg = build_drawing(Box(30, 20, 10), out=str(tmp_path / "b"), title="B", number="DWG-1")
    assert isinstance(dwg, Drawing)
    assert set(dwg.views) == {"front", "plan", "side", "iso"}
    assert dwg.annotations, "expected automatic annotations"
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
    assert [a for a in dwg.annotations] == [dwg._named["title_block"]]


@pytest.mark.timeout(60)
def test_clear_annotations_keeps_title_block():
    # #74 — wholesale removal without knowing the auto-name scheme.
    dwg = build_drawing(Cylinder(15, 40))  # cylinder → od dim, centerlines, …
    assert len(dwg.annotations) > 1
    removed = dwg.clear_annotations()
    assert removed
    assert all(a not in dwg.annotations for a in removed)
    assert len(dwg.annotations) == 1
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
    assert keep_me in dwg.annotations
    assert len(dwg.annotations) == 2  # unnamed leader removed too


@pytest.fixture(scope="module")
def shrunk_iso_drawing():
    # #75 fixture — NIST CTC-01-like plate at 1:5 on A3: the iso overflows at
    # sheet scale and is auto-shrunk. Module-scoped; tests must not mutate it.
    return build_drawing(Box(800, 450, 150), scale=0.2, page="A3")


@pytest.mark.timeout(120)
def test_iso_overflow_shrinks_with_nts_note(shrunk_iso_drawing):
    # #75 — at sheet scale the iso would run past the A3 page edge; it must be
    # re-projected smaller and captioned NTS.
    from draftwright.make_drawing import _iso_bbox

    dwg = shrunk_iso_drawing
    labels = [getattr(a, "label", "") for a in dwg.annotations]
    assert "ISO VIEW (NTS)" in labels
    x0, y0, x1, y1 = _iso_bbox(dwg)
    assert (
        x1 <= dwg.page_w - 10 + 0.5 and x0 >= 0 and y0 >= 10 - 0.5 and y1 <= dwg.page_h - 10 + 0.5
    )


@pytest.mark.timeout(120)
def test_shrunk_iso_keeps_world_to_page_mapping(shrunk_iso_drawing):
    # After the NTS shrink, dwg.at("iso", ...) must still map world points to
    # the page: the centroid lands on the view centre and offsets scale by the
    # shrunk view scale, not the sheet scale.
    dwg = shrunk_iso_drawing
    cx, cy, cz = dwg.centroid
    centre = dwg.at("iso", cx, cy, cz)
    vis, _hid = dwg.views["iso"]
    bb = vis.bounding_box()
    assert bb.min.X < centre[0] < bb.max.X and bb.min.Y < centre[1] < bb.max.Y
    # World +Z maps to page +Y; the offset must use the shrunk view scale (not
    # the sheet scale).  Derive the actual shrunk scale from _coords so the
    # test does not depend on a specific discretised shrink factor.
    shrunk_scale = dwg._coords["iso"]._scale
    assert shrunk_scale < dwg.scale, "iso should be shrunk below sheet scale"
    raised = dwg.at("iso", cx, cy, cz + 100)
    assert raised[1] - centre[1] == pytest.approx(100 * shrunk_scale)


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


@pytest.mark.timeout(60)
def test_drawing_add_and_remove():
    dwg = build_drawing(Box(30, 20, 10))
    n0 = len(dwg.annotations)
    ldr = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label="X", draft=dwg.draft)
    dwg.add(ldr, "ldr_test")
    assert len(dwg.annotations) == n0 + 1
    removed = dwg.remove("ldr_test")
    assert removed is ldr
    assert len(dwg.annotations) == n0
    with pytest.raises(KeyError):
        dwg.remove("does_not_exist")


@pytest.mark.timeout(60)
def test_drawing_add_replaces_reused_name():
    dwg = build_drawing(Box(30, 20, 10))
    n0 = len(dwg.annotations)
    first = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label="A", draft=dwg.draft)
    second = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(6, 6, 0), label="B", draft=dwg.draft)
    dwg.add(first, "ldr")
    dwg.add(second, "ldr")  # same name → replaces, no orphan left behind
    assert len(dwg.annotations) == n0 + 1
    assert first not in dwg.annotations
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
        # A bore intersected by a slot leaves cylinder patches under half a
        # turn each — together they are still one undimensioned ø10 hole.
        part = Box(60, 40, 10) - Cylinder(5, 12) - Box(60, 6, 12)
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
        # 4x o10 thru corners + centre o8 thru with o16x6 cbore + o6 x-axis
        # cross hole + o12 blind hole
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
        # 3 distinct Z specs (4x o10 thru, o8 cbore stack, o12 blind), not 6
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
    def test_callout_cap_keeps_the_largest_holes(self):
        part = Box(120, 80, 10)
        for i, r in enumerate([1, 1.5, 2, 2.5, 3, 4]):
            part = part - Pos(-50 + i * 20, 0, 0) * Cylinder(r, 10)
        dwg = build_drawing(part)
        covered = set()
        for name, ann in dwg._named.items():
            if name.startswith("hc_"):
                covered.update(ann.covers_diameters)
        assert covered == {4.0, 5.0, 6.0, 8.0}
        # the dropped specs surface through the coverage lint by design
        flagged = {i.message for i in dwg.lint() if i.code == "feature_not_dimensioned"}
        assert len(flagged) == 2

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
        result = _solve_strip_ys([10.0, 12.0, 14.0, 16.0], min_gap=8.0, y_min=0.0, y_max=100.0)
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
        result = _solve_strip_ys([5.0, 10.0, 15.0], min_gap=8.0, y_min=0.0, y_max=10.0)
        assert result is None

    @pytest.mark.timeout(60)
    def test_solve_strip_ys_empty_input(self):
        from draftwright.make_drawing import _solve_strip_ys

        assert _solve_strip_ys([], min_gap=8.0, y_min=0.0, y_max=100.0) == []


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
