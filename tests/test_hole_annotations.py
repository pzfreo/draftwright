"""Automatic hole and hole-pattern annotations."""

import math

import pytest
from build123d import Box, Cylinder, Pos
from build123d_drafting import HoleCallout

from draftwright import build_drawing
from draftwright.make_drawing import lint_feature_coverage


@pytest.fixture(scope="module")
def plate_drawing():
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


class TestAutoHoleAnnotations:
    """Auto hole callouts (#91), count grouping (#92), centre marks (#95)."""

    @pytest.mark.timeout(120)
    def test_identical_holes_share_one_counted_callout(self, plate_drawing):
        hc = [n for n in plate_drawing.annotations() if n.startswith("hc_plan")]
        # 3 distinct Z specs (4× ø10 thru, ø8 cbore stack, ø12 blind), not 6
        assert len(hc) == 3

    @pytest.mark.timeout(120)
    def test_callouts_cover_all_feature_diameters(self, plate_drawing):
        covered = set()
        for name, ann in plate_drawing.iter_annotations():
            if name.startswith("hc_"):
                covered.update(getattr(ann, "covers_diameters", ()))
        assert covered == {10.0, 8.0, 16.0, 6.0, 12.0}

    @pytest.mark.timeout(120)
    def test_cross_axis_hole_gets_side_view_callout(self, plate_drawing):
        (name,) = [n for n in plate_drawing.annotations() if n.startswith("hc_side")]
        assert plate_drawing.get_annotation(name).covers_diameters == (6.0,)

    @pytest.mark.timeout(120)
    def test_every_hole_gets_a_centre_mark(self, plate_drawing):
        cm = [n for n in plate_drawing.annotations() if n.startswith("m_cm")]
        assert len(cm) == 7  # 6 z-holes in plan + 1 x-hole in side
        assert all(plate_drawing.get_annotation(n).is_centerline for n in cm)

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
        assert "section_line" not in dwg.annotations()
        hc = dwg.get_annotation("hc_plan0")
        assert hc is not None
        plan_right = dwg.view_bounds("plan")[2]  # plan view's right page boundary
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
        assert len([n for n in dwg.annotations() if n.startswith("hc_")]) == 1

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
        assert len([n for n in dwg.annotations() if n.startswith("hc_front")]) == 2
        # Both callouts and both X offsets land. The initial optional-ISO plan loses one
        # Z-height companion; automatic recovery removes that optional view and places the
        # complete set without changing this test's subject: both front-view callouts fit.
        issues = dwg.lint()
        assert issues == []
        assert dwg.scale_decision["status"] == "automatic_replanned"
        assert dwg.scale_decision["attempts"][-1]["reason"] == "remove_optional_iso"

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
        for name, ann in dwg.iter_annotations():
            if name.startswith("hc_"):
                covered.update(ann.covers_diameters)
        assert covered == {2.0, 3.0, 4.0, 5.0, 6.0, 8.0}
        assert "callout_dropped" not in {i.code for i in dwg.lint()}

    @pytest.mark.timeout(60)
    def test_rotational_part_keeps_leader_annotations(self):
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert "ldr_z0" in dwg.annotations()
        assert not any(n.startswith("hc_") for n in dwg.annotations())
        # the central bore still gets a centre mark in the plan view
        assert any(n.startswith("m_cm") and dwg.view_of(n) == "plan" for n in dwg.annotations())

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
        assert 2 * a.SCALE < arrow_len, "fixture must expose the old rim-clamp defect"
        old_corridor = 0.6 * a.DIM_PAD  # ≈ 10.8 mm — the old, oversized offset
        hc_plan_names = [n for n in dwg.annotations() if n.startswith("hc_plan")]
        assert hc_plan_names, "Expected at least one plan-view bore callout"
        for name in hc_plan_names:
            ldr = dwg.get_annotation(name)
            assert ldr.elbow[0] >= plan_right - 1e-6, (
                f"{name}: elbow x={ldr.elbow[0]:.3f} is inside the view "
                f"(plan_right={plan_right:.3f})"
            )
            assert ldr.elbow[0] < plan_right + old_corridor, (
                f"{name}: elbow x={ldr.elbow[0]:.3f} is too far from view "
                f"(should be < plan_right + 0.6×DIM_PAD = {plan_right + old_corridor:.3f})"
            )
            # The bore has less edge margin than the arrow length. Its head may
            # extend past the silhouette; clamping the tip instead puts it off
            # the named rim (#1378). Preserve the compact elbow and real target.
            centre = dwg.at("plan", 15, 0, 0)
            assert math.hypot(ldr.tip[0] - centre[0], ldr.tip[1] - centre[1]) == pytest.approx(
                3 * a.SCALE
            )

    @pytest.mark.timeout(60)
    def test_solve_strip_ys_returns_feasible_positions(self):
        from draftwright._core import _solve_strip_ys

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
        from draftwright._core import _solve_strip_ys

        # Three items need 2 × 8 = 16mm gap, but range is only 10mm.
        result = _solve_strip_ys([5.0, 10.0, 15.0], min_gap=8.0, lo=0.0, hi=10.0)
        assert result is None

    @pytest.mark.timeout(60)
    def test_solve_strip_ys_empty_input(self):
        from draftwright._core import _solve_strip_ys

        assert _solve_strip_ys([], min_gap=8.0, lo=0.0, hi=100.0) == []


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
        assert any(n.startswith("bc_plan") for n in dwg.annotations())
        (hc8,) = [
            a
            for n, a in dwg.iter_annotations()
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
        assert dwg.get_annotation("dim_pitch_plan0").label == "4× 20"
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
        assert len([n for n in dwg.annotations() if n.startswith("hc_plan")]) == 2
        assert len([n for n in dwg.annotations() if n.startswith("dim_pitch_plan")]) == 2
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_top_edge_array_dimensions_above_the_plan_view(self):
        # Below the plan view sit dim_width and the front view — plan pitch
        # dims always go up, with short extension lines for top-edge rows.
        part = Box(140, 50, 10)
        for i in range(4):
            part = part - Pos(-30 + i * 20, 18, 0) * Cylinder(3, 10)
        dwg = build_drawing(part)
        dim = dwg.get_annotation("dim_pitch_plan0")
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
        assert "dim_pitch_plan0" in dwg.annotations()
        (dropped,) = [i for i in dwg.lint() if i.code == "hole_pattern_dim_dropped"]
        assert len(dropped.measurement_ids) == 1
        assert dropped.measurement_ids[0].parameter == "pitch.length"
        summary = dwg.lint_summary()
        # `plan_incomplete` joins it: a dropped pattern pitch is a required placement failure,
        # and since #1250 the automatic path reports one at error severity instead of
        # returning the incomplete sheet as a success.
        assert summary["by_code"] == {"hole_pattern_dim_dropped": 1, "plan_incomplete": 1}
        assert summary["geometry_issues"] == 1
        assert summary["quality"]["completeness"]["dropped"] == 1

    @pytest.mark.timeout(60)
    def test_count_mismatch_surfaces_in_lint(self):
        from build123d import Draft

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
        from build123d_drafting import Dimension

        from draftwright.linting import lint_drawing

        d = Draft(font_size=2.5)
        dim = Dimension((0, 0, 0), (80, 0, 0), "above", 8, d, label="4× 20")
        assert [i for i in lint_drawing([dim]) if i.code == "label_vs_measured"] == []
