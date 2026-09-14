"""Two-pass corridor sizing from planned bore callouts."""

import pytest
from _layout_helpers import _sizing_model
from build123d import Box

from draftwright import build_drawing


@pytest.fixture(scope="module")
def plain_box_dwg():
    return build_drawing(Box(60, 40, 20))


class TestTwoPassLayout:
    """Two-pass layout (#131): bore callout widths widen the FV→SV corridor."""

    def test_bore_callout_widens_gap_fv_sv(self):
        # A part with many small holes generates wide callout labels (e.g.
        # "4× ⌀15.9 THRU") that need more than _DIM_PAD right of the plan view.
        # The two-pass layout must size gap_fv_sv >= bore callout depth.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing
        from draftwright._core import _DIM_PAD

        # Four identical cylinders → "4× ⌀16 THRU" callout with a count prefix
        part = (
            Box(100, 80, 20)
            - Pos(30, 25, 0) * Cylinder(16, 20)
            - Pos(-30, 25, 0) * Cylinder(16, 20)
            - Pos(30, -25, 0) * Cylinder(16, 20)
            - Pos(-30, -25, 0) * Cylinder(16, 20)
        )
        dwg = build_drawing(part)
        sv_left = dwg.view_bounds("side")[0]
        fv_right = dwg.view_bounds("front")[2]
        actual_gap = sv_left - fv_right

        _, bore_depth = _sizing_model(part)
        # bore callout width must exceed DIM_PAD for the test to be meaningful
        assert bore_depth > _DIM_PAD, (
            f"bore callout width {bore_depth:.1f} mm must exceed _DIM_PAD={_DIM_PAD} mm"
        )
        assert actual_gap >= bore_depth - 0.1, (
            f"gap_fv_sv={actual_gap:.1f} mm must be >= bore_depth={bore_depth:.1f} mm"
        )

    def test_plain_box_gap_unchanged(self, plain_box_dwg):
        # A box with no holes: bore callout depth = 0 → gap_fv_sv stays _DIM_PAD.

        from draftwright._core import _DIM_PAD

        d = plain_box_dwg
        sv_left = d.view_bounds("side")[0]
        fv_right = d.view_bounds("front")[2]
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
        sv_left = dwg.view_bounds("side")[0]
        for name, ann in dwg.iter_annotations():
            if name.startswith("hc_plan") and getattr(ann, "label_bbox", None):
                lx1 = ann.label_bbox[2]  # right edge of callout label
                assert lx1 <= sv_left + 0.5, (
                    f"{name}: label right edge {lx1:.1f} mm exceeds sv_left {sv_left:.1f} mm"
                )

    def test_bolt_circle_suffix_widens_estimate(self):
        # BoltCircle callouts carry the "EQ SP ON ø… BC" suffix (~34 mm wide); the
        # planner callout-width estimate must include it, so a bolt-circle part is wider
        # than the same ⌀8 bore alone (#584 WP1 A — via _est_planned_bore_callout_width).
        from build123d import Box, Cylinder, Pos

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
        plain = Box(100, 100, 20) - Pos(0, 0, 0) * Cylinder(8, 20)  # one ⌀16 bore, no BC suffix

        _, width_with = _sizing_model(part)
        _, width_without = _sizing_model(plain)
        assert width_with > width_without, (
            f"BoltCircle suffix should widen estimate: {width_without:.1f} → {width_with:.1f} mm"
        )

    def test_pv_below_strip_has_slack(self):
        # pv_zones.below outer_limit = fv_top_edge (not fv_top_edge + 2), giving
        # 18 mm available vs 16 mm needed for dim_width — no razor-fit (#130).
        from build123d import Box

        from draftwright import build_drawing
        from draftwright.compose import _est_pv_below_depth

        part = Box(80, 40, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        available = a.pv_zones.below.anchor - a.pv_zones.below.outer_limit
        needed = _est_pv_below_depth()
        assert available > needed, (
            f"pv_zones.below available {available:.1f} mm must exceed needed {needed:.1f} mm"
        )
        assert "m_env_width" in dwg.annotations(), "width dim must not be skipped"
