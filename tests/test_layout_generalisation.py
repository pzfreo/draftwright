"""Layout generalisation across shapes, scales, and sheet sizes."""

import math

import pytest
from build123d import Align, Box, Cylinder, Pos, Rotation

from draftwright import build_drawing


class TestLayoutGeneralisation:
    @staticmethod
    def _lines_crossing_label(dwg, callout_name, label_bbox):
        """Horizontal lines (other than the callout's own shelf) that cross the
        callout's text box — the #305 'line through the callout text' defect. Shared
        by the coaxial-bore tests."""
        tx0, ty0, tx1, ty1 = label_bbox
        crossings = []
        for n, o in dwg.iter_annotations():
            if n == callout_name:
                continue
            try:
                edges = list(o.edges())
            except Exception:
                continue
            for e in edges:
                vs = e.vertices()
                if len(vs) != 2:
                    continue
                (x0, y0), (x1, y1) = (vs[0].X, vs[0].Y), (vs[1].X, vs[1].Y)
                if abs(y0 - y1) < 0.05 and abs(x0 - x1) > 1.0:  # a horizontal line
                    ym = (y0 + y1) / 2
                    xa, xb = min(x0, x1), max(x0, x1)
                    if ty0 + 0.3 < ym < ty1 - 0.3 and xb > tx0 + 0.3 and xa < tx1 - 0.3:
                        crossings.append((n, round(ym, 2)))
        return crossings

    @pytest.mark.timeout(120)
    def test_turned_flange_gets_both_od_and_hole_furniture(self):
        # A turned-and-drilled flange (cylinder OD + centre bore + bolt circle)
        # must get the turned base set (OD dim + profile centreline) AND the drilled
        # furniture (hole callout + pitch circle) — not one or the other. This
        # is the feature-presence composition from #10, on a genuinely
        # rotational part rather than a prismatic plate.

        from build123d import Cylinder, Pos

        flange = Cylinder(radius=40, height=10) - Cylinder(radius=8, height=10)
        for i in range(6):
            ang = math.radians(60 * i)
            flange -= Pos(28 * math.cos(ang), 28 * math.sin(ang), 0) * Cylinder(2.5, 10)
        dwg = build_drawing(flange)

        assert dwg._analysis.is_rotational, "flange should classify as rotational"
        # Turned base set.
        assert "dim_od" in dwg.annotations()
        assert "centerline_front" in dwg.annotations()
        assert "centerline_side" not in dwg.annotations()  # redundant profile view omitted
        assert "centerline_plan" not in dwg.annotations()  # end view gets marks, not an axis line
        # Drilled furniture.
        assert any(n.startswith("hc_") for n in dwg.annotations()), "expected a hole callout"
        assert any(n.startswith("bc_") for n in dwg.annotations()), "expected a pitch circle"
        # No error-severity lint (warnings tolerated).
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    @pytest.mark.timeout(120)
    def test_turned_flange_dimensions_all_its_bores(self):
        # #36: no per-view callout cap — a turned part with five distinct bores
        # gets a callout for every one (was capped at four largest), more than
        # the old cap, with nothing dropped because they fit.

        from build123d import Cylinder, Pos

        flange = Cylinder(radius=45, height=10) - Cylinder(radius=8, height=10)
        for i, r in enumerate((2.0, 2.5, 3.0, 3.5, 4.0)):
            ang = math.radians(72 * i)
            flange -= Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(r, 10)
        dwg = build_drawing(flange)

        n_callouts = len([n for n in dwg.annotations() if n.startswith("hc_")])
        assert n_callouts > 4, f"expected adaptive >4 callouts, got {n_callouts}"
        assert "callout_dropped" not in {i.code for i in dwg.lint()}

    def test_coaxial_bore_callout_clears_centre_axis(self):
        # #305: a coaxial axial bore on the round (end) view must be leadered OFF
        # the view's horizontal centre axis. Led out along it, the centre mark and
        # the bore's own location-dim extension line run straight through the
        # "⌀… ↓…" callout text (a drafting defect). Assert no horizontal line
        # crosses the callout text box.
        from build123d import BuildPart, Hole

        with BuildPart() as p:
            Cylinder(radius=6, height=20)
            Hole(0.8, depth=8)  # coaxial axial bore: ⌀1.6, depth 8
        dwg = build_drawing(Rotation(0, 90, 0) * p.part, scale=2.0)  # axis along X

        hc = [(n, o) for n, o in dwg.iter_annotations() if n.startswith("hc_side")]
        assert hc, "expected a bore callout on the round (side) view"
        name, leader = hc[0]
        crossings = self._lines_crossing_label(dwg, name, leader.label_bbox)
        assert not crossings, f"line(s) cross the bore callout text: {crossings}"

    def test_coaxial_bore_callout_clears_centre_axis_on_stepped_shaft(self):
        # #305 regression: the lift must also fire for a *stepped* turned shaft (the
        # GRM-03 drive screw), which has a turned step profile but is NOT
        # is_rotational (its varying OD doesn't fill a square cross-section) — the
        # original is_rotational-only gate missed it, leaving the bore callout led
        # straight along the centre axis. Assert no horizontal line crosses the text.

        b = Align.MIN
        part = (
            Cylinder(6, 12, align=(Align.CENTER, Align.CENTER, b))
            + Pos(0, 0, 12) * Cylinder(4, 12, align=(Align.CENTER, Align.CENTER, b))
            - Cylinder(0.8, 8, align=(Align.CENTER, Align.CENTER, b))
        )
        dwg = build_drawing(Rotation(0, 90, 0) * part, scale=2.0)
        assert dwg._analysis.prof is not None and not dwg._analysis.is_rotational

        hc = [(n, o) for n, o in dwg.iter_annotations() if n.startswith("hc_side")]
        assert hc, "expected a bore callout on the round (side) view"
        name, leader = hc[0]
        crossings = self._lines_crossing_label(dwg, name, leader.label_bbox)
        assert not crossings, f"line(s) cross the bore callout text: {crossings}"

    def test_prismatic_central_hole_callout_not_lifted(self):
        # Scope-lock for #305: the coaxial-bore lift is gated to rotational parts.
        # A *prismatic* part's central hole stays on the plan-view centre row —
        # lifting it (the over-broad first cut of this fix) regressed prismatic
        # layouts, because only the rotational round view carries the crossing
        # centre axis. Use a through-only part so an independent optional section
        # reservation cannot legitimately move the callout off this row.

        part = Box(80, 60, 20) - Cylinder(4, 20)
        dwg = build_drawing(part)
        assert not dwg._analysis.is_rotational
        plan_mids = [
            (o.label_bbox[1] + o.label_bbox[3]) / 2
            for n, o in dwg.iter_annotations()
            if n.startswith("hc_plan") and getattr(o, "label_bbox", None)
        ]
        assert plan_mids, "expected plan-view hole callouts"
        assert (
            min(abs(m - dwg.at("plan", *dwg.centroid)[1]) for m in plan_mids) < dwg.draft.font_size
        )

    @pytest.mark.timeout(120)
    def test_step_height_legibility_threshold(self):
        # The step-height dimension gate is the legibility constant
        # (_MIN_STEP_DIM_MM), not an incidental cutoff: a shoulder whose
        # page-projected height falls just below the gate gets no step dim;
        # just above, it does. Pin the gate, not a magic millimetre value.
        #
        # Exercised on a *prismatic* stepped block: the engine's Z step-height
        # ladder (and its legibility gate) still governs prismatic parts. Turned
        # parts now route through the unified IR step-length chain instead (#223),
        # which is sized to fit rather than gated, so they no longer exercise this.

        from draftwright._core import _MIN_STEP_DIM_MM

        def block_with_shoulder_at(length):
            # Square (non-rotational) so it is not a turned part; lower segment
            # height == `length`, shoulder `length` above the base → legibility
            # = length * SCALE.
            return Pos(0, 0, length / 2) * Box(44, 44, length) + Pos(0, 0, length + 12.5) * Box(
                22, 22, 25
            )

        for length, expect in ((12.0, False), (13.0, True)):
            dwg = build_drawing(block_with_shoulder_at(length))
            legible = length * dwg.scale >= _MIN_STEP_DIM_MM
            assert legible is expect, (
                f"length={length} scale={dwg.scale}: legibility expectation wrong"
            )
            has_step = "dim_step_0" in dwg.annotations()
            assert has_step is expect, (
                f"length={length}: step dim present={has_step}, expected {expect}"
            )
