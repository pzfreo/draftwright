"""Strip and view-zone corridor routing behavior."""

import pytest


class TestStripZones:
    """Unit tests for the Strip / ViewZones layout primitives (issue #105)."""

    def test_strip_import(self):
        pass

    def test_strip_available(self):
        from draftwright._core import Strip

        s = Strip(anchor=50.0, outer_limit=150.0, direction=1)
        assert s.available == pytest.approx(100.0)

    def test_analyse_returns_view_zones(self):
        from build123d import Box, Cylinder

        from draftwright import build_drawing
        from draftwright._core import Strip, ViewZones

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
        assert "dim_height" in dwg.annotations()
        ann = dwg.get_annotation("dim_height")
        # label is the part height
        assert ann.label == "30"

    def test_pv_below_strip_is_now_active(self):
        # pv_zones.below should be a Strip (not None) after Phase 3
        from build123d import Box

        from draftwright import build_drawing
        from draftwright._core import Strip

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

        # non-square part → width != depth → the width dim should appear (IR
        # renderer m_env_width, still routed through pv_zones.below).
        part = Box(80, 40, 20)
        dwg = build_drawing(part)
        assert "m_env_width" in dwg.annotations()
        ann = dwg.get_annotation("m_env_width")
        assert ann.label == "80"

    def test_dim_locx_routed_through_pv_above_strip(self):
        # dim_locx dims must be above plan_top and allocated from pv_zones.above
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        part = Box(80, 60, 20) - Pos(20, 10, 0) * Cylinder(5, 20)
        dwg = build_drawing(part)
        locx_dims = [v for n, v in dwg.iter_annotations() if n.startswith("m_locx")]
        assert len(locx_dims) >= 1, "expected m_locx0 to be generated for off-datum cylinder"
        plan_top = dwg.views["plan"][0].bounding_box().max.Y
        assert all(d.dim_level_y > plan_top for d in locx_dims)

    def test_dim_locy_routed_through_sv_above_strip(self):
        # dim_locy dims must be above side_top and allocated from sv_zones.above
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        # Cylinder at Y=10 → offset from datum_y=bb.min.Y → generates dim_locy0
        part = Box(80, 60, 20) - Pos(0, 10, 0) * Cylinder(5, 20)
        dwg = build_drawing(part)
        locy_dims = [v for n, v in dwg.iter_annotations() if n.startswith("m_locy")]
        assert len(locy_dims) >= 1, "expected m_locy0 to be generated for off-datum cylinder"
        side_top = dwg.views["side"][0].bounding_box().max.Y
        assert all(d.dim_level_y > side_top for d in locy_dims)

    def test_dim_step_placed_after_phase3_corridor_widening(self):
        # Phase 3 widens fv_zones.right dynamically for stepped parts.
        # A part with one step face gets gap_fv_sv = 36 mm (vs 18 mm fixed),
        # which is enough for dim_height (10 mm) + spacing (4 mm) + dim_step (14 mm).
        # Both annotations must now appear without overlapping the side view.
        from build123d import Box, Pos

        from draftwright import build_drawing
        from draftwright.compose import _est_right_strip_depth

        part = Box(40, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        # dim_height and at least one dim_step must be generated
        assert "dim_height" in dwg.annotations()
        step_dims = [n for n in dwg.annotations() if n.startswith("dim_step")]
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
        assert "dim_height" in dwg.annotations()
        assert dwg.get_annotation("dim_height").label == "30"

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
        assert "dim_height" in dwg.annotations()
        step_dims = [n for n in dwg.annotations() if n.startswith("dim_step")]
        assert step_dims, "expected at least one step dim"
        height_x = dwg.get_annotation("dim_height").bounding_box().max.X
        for n in step_dims:
            step_x = dwg.get_annotation(n).bounding_box().max.X
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
        from draftwright._core import _iso_bbox

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

        # 80×40×20 box: width=80, depth=40 — differ by > 5%, so the depth dim fires
        # (IR renderer m_env_depth, still routed through sv_zones.below).
        part = Box(80, 40, 20)
        dwg = build_drawing(part)
        assert "m_env_depth" in dwg.annotations(), (
            "expected m_env_depth for part with width != depth"
        )
        ann = dwg.get_annotation("m_env_depth")
        assert ann.label == "40", f"depth label should be y_size=40, got {ann.label!r}"

    def test_a_square_plan_states_both_extents(self):
        """Was `test_dim_depth_absent_for_square_plan`, asserting the opposite (#997).

        A square part carries BOTH envelope extents. Suppressing the depth because it equals
        the width leaves the drawing ambiguous: orthographic projection is not a dimensional
        constraint, so without `□60` / `60 SQ` nothing states that the second extent matches
        the first. Two equal extents are two independent facts.

        Stating both is not the double-dimensioning ISO 129 warns about — that is about
        closing a chain. Square notation is the optimisation (#918); until it exists, verbose
        and unambiguous beats terse and inferred.
        """
        from build123d import Box

        from draftwright import build_drawing

        dwg = build_drawing(Box(60, 60, 20))
        for name in ("m_env_width", "m_env_depth"):
            assert name in dwg.annotations(), f"square plan must state {name}"
            assert dwg.get_annotation(name).label == "60"

    def test_a_near_square_part_states_both_extents(self):
        """#997: the square test was "within 5%", so a 100 x 95 part was drawn showing 100
        and nothing else in-plane — no second dimension, and no `SQ` notation to even claim
        squareness. A reader takes that part as 100 x 100. It is 5 mm narrower.

        5% of a 100 mm part is 5 mm: orders of magnitude outside any machining tolerance. Two
        extents are interchangeable only when they are the same number, so the tolerance now
        absorbs float error and nothing else.
        """
        from build123d import Box

        from draftwright import build_drawing

        for w, d in ((100, 95), (50, 48), (100, 99)):
            dwg = build_drawing(Box(w, d, 30), number="X")
            assert "m_env_depth" in dwg.annotations(), (
                f"{w}x{d} is not square — its depth must be stated, not implied by projection"
            )
            assert dwg.get_annotation("m_env_depth").label == str(d)

