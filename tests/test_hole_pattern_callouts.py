"""Grouped hole-pattern callout behavior."""

import math

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot

from draftwright import build_drawing


class TestHolePatternCallouts:
    """Grouped pattern callouts from the helpers v0.12.0 recognition (RectGrid +
    sub-clustered LinearArrays): a recognised set collapses to one ``n× ⌀``
    callout plus its pattern dimensions, never per-hole balloons/table (#92,
    #111). Coverage lint stays quiet because the grouped callout carries the
    full diameter count."""

    @staticmethod
    def _grid_part():
        # 2 rows × 4 cols of ⌀8 through-holes; 20 mm pitch one way, 25 mm the
        # other → a single RectGrid(2×4).
        part = Box(140, 70, 12)
        for r in range(2):
            for c in range(4):
                part -= Pos(-37.5 + c * 25, -10 + r * 20, 0) * Cylinder(4, 12)
        return part

    @staticmethod
    def _perimeter_part():
        # Rectangular perimeter of ⌀6 holes — five along the top and bottom
        # edges (recognised as two LinearArrays), the rest unpatterned.
        part = Box(140, 100, 12)
        pos = set()
        for x in (-50, -25, 0, 25, 50):
            pos.add((x, -35))
            pos.add((x, 35))
        for y in (-35, 0, 35):
            pos.add((-50, y))
            pos.add((50, y))
        for x, y in pos:
            part -= Pos(x, y, 0) * Cylinder(3, 12)
        return part

    @pytest.mark.timeout(120)
    def test_rect_grid_one_callout_and_two_pitch_dims(self):
        dwg = build_drawing(self._grid_part())
        named = dict(dwg.iter_annotations())
        hc = [n for n in named if n.startswith("hc_")]
        pitch = [n for n in named if n.startswith("dim_pitch_")]
        # one grouped callout covering all eight holes — not eight callouts
        assert len(hc) == 1, f"expected one grouped callout, got {hc}"
        assert named[hc[0]].covers_count == 8
        assert named[hc[0]].covers_diameters == (8.0,)
        # both grid pitch dimensions, labelled (n-1)× pitch
        assert len(pitch) == 2, f"expected two pitch dims, got {pitch}"
        assert {named[n].label for n in pitch} == {"1× 20", "3× 25"}
        # each dim runs ALONG one lattice axis — its endpoints share a coordinate
        # — not diagonally across the grid; and the two are perpendicular.
        axes = set()
        for n in pitch:
            sp = named[n]._dw_spec
            dx, dy = abs(sp.p1[0] - sp.p2[0]), abs(sp.p1[1] - sp.p2[1])
            assert dx < 0.5 or dy < 0.5, f"{n} drawn diagonally: p1={sp.p1} p2={sp.p2}"
            axes.add("vertical" if dx < 0.5 else "horizontal")
        assert axes == {"vertical", "horizontal"}, f"grid dims not perpendicular: {axes}"
        # the grouped callout replaces — never coexists with — per-hole furniture
        assert not [n for n in named if n.startswith("balloon")]
        assert not [n for n in named if "table" in n]

    @pytest.mark.timeout(120)
    def test_rect_grid_pitch_dims_not_diagonal_when_rotated(self):
        # Regression guard for the high-aspect ROTATED grid: each pitch dim must
        # measure along one lattice edge (endpoint span == label span), not
        # corner-to-corner. A 2×5 grid (10 × 45 pitch) rotated 25° — the short-
        # axis dim spans 10 mm; the diagonal bug would make it ~180 mm.
        ang = math.radians(25)
        ca, sa = math.cos(ang), math.sin(ang)
        part = Box(220, 120, 12)
        for r in range(2):
            for c in range(5):
                x, y = (c - 2) * 45, (r - 0.5) * 10
                part -= Pos(x * ca - y * sa, x * sa + y * ca, 0) * Cylinder(4, 12)
        dwg = build_drawing(part)
        scale = dwg.scale
        pitch = [n for n in dwg.annotations() if n.startswith("dim_pitch_")]
        assert len(pitch) == 2, f"expected two grid pitch dims, got {pitch}"
        for n in pitch:
            dim = dwg.get_annotation(n)
            sp = dim._dw_spec
            span = math.hypot(sp.p2[0] - sp.p1[0], sp.p2[1] - sp.p1[1]) / scale
            k, p = dim.label.split("× ")
            expected = int(k) * float(p)
            assert abs(span - expected) < 1.0, (
                f"{n} ({dim.label!r}) endpoint span {span:.1f} ≠ {expected:.1f} — drawn diagonally"
            )

    @pytest.mark.timeout(120)
    def test_x_axis_rect_grid_keeps_both_pitches_in_the_side_view(self):
        """0.4.6 coordinate rounding must not select an interior side-view witness row."""

        ang = math.radians(25)
        ca, sa = math.cos(ang), math.sin(ang)
        centre = (Align.CENTER, Align.CENTER, Align.CENTER)
        part = Box(12, 220, 120, align=centre)
        cutter = Rot(0, 90, 0) * Cylinder(4, 20, align=centre)
        for r in range(2):
            for c in range(5):
                y, z = (c - 2) * 45, (r - 0.5) * 10
                part -= Pos(0, y * ca - z * sa, y * sa + z * ca) * cutter

        dwg = build_drawing(part)
        pitch = [name for name in dwg.annotations() if name.startswith("dim_pitch_")]

        assert len(pitch) == 2, f"expected two side-grid pitch dims, got {pitch}"
        assert {dwg.view_of(name) for name in pitch} == {"side"}
        assert {dwg.get_annotation(name).label for name in pitch} == {"1× 10", "4× 45"}
        assert "hole_pattern_dim_dropped" not in {issue.code for issue in dwg.lint()}

    @pytest.mark.timeout(120)
    def test_rect_grid_coverage_lint_quiet(self):
        codes = {i.code for i in build_drawing(self._grid_part()).lint()}
        assert "feature_not_dimensioned" not in codes
        assert "feature_count_mismatch" not in codes

    @pytest.mark.timeout(120)
    def test_perimeter_rows_dimensioned_not_per_hole(self):
        dwg = build_drawing(self._perimeter_part())
        named = dict(dwg.iter_annotations())
        pitch = [n for n in named if n.startswith("dim_pitch_")]
        # each recognised edge row (5 holes, pitch 25) gets its own pitch dim
        assert len(pitch) >= 2, f"expected the edge rows dimensioned, got {pitch}"
        assert any(named[n].label == "4× 25" for n in pitch)
        # the rows are not exploded into a per-hole table / balloons
        assert not [n for n in named if n.startswith("balloon")]
        assert not [n for n in named if "table" in n]
        assert "feature_not_dimensioned" not in {i.code for i in dwg.lint()}
