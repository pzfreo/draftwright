"""Drawing feature-query behavior."""

import math

import pytest
from _parts import holed_plate
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing


@pytest.fixture(scope="module")
def holed_plate_drawing():
    return build_drawing(holed_plate())


class TestFeatures:
    """#26: dwg.features(view) exposes analysis to scripts."""

    def test_feature_info_importable_from_top_level(self):
        from draftwright import FeatureInfo

        f = FeatureInfo(
            type="hole", page_pos=(1.0, 2.0), diameter=5.0, through=True, depth=None, count=1
        )
        assert f.type == "hole"
        assert f.count == 1

    def test_z_axis_holes_appear_in_plan_view(self, holed_plate_drawing):
        dwg = holed_plate_drawing
        feats = dwg.features("plan")
        assert len(feats) == 2  # ø10 group (×4) + ø6 group (×1)
        diams = {f.diameter for f in feats}
        assert diams == {10.0, 6.0}

    def test_through_and_blind_correctly_classified(self, holed_plate_drawing):
        dwg = holed_plate_drawing
        feats = {f.diameter: f for f in dwg.features("plan")}
        assert feats[10.0].through is True
        assert feats[10.0].depth is None
        assert feats[6.0].through is False
        assert feats[6.0].depth == 10.0

    def test_count_groups_identical_holes(self, holed_plate_drawing):
        dwg = holed_plate_drawing
        feats = {f.diameter: f for f in dwg.features("plan")}
        assert feats[10.0].count == 4
        assert feats[6.0].count == 1

    def test_pattern_and_loose_same_spec_holes_are_separate_groups(self):
        # #584 WP1 (B2): features()/the hole table source grouping from the IR, so a
        # recognised pattern and same-spec LOOSE holes are DISTINCT groups (the pattern
        # keeps its bolt-circle callout) — not one flat spec-group. Six ø6 on a bolt
        # circle + two loose ø6 → a count-6 pattern group AND a count-2 loose group,
        # not one count-8 group.
        part = Box(140, 140, 12)
        for i in range(6):
            ang = math.radians(60 * i + 15)
            part -= Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(3, 12)
        part -= Pos(-60, -60, 0) * Cylinder(3, 12)
        part -= Pos(60, 60, 0) * Cylinder(3, 12)
        dwg = build_drawing(part, number="X")
        six = sorted(f.count for f in dwg.features("plan") if f.diameter == 6.0)
        assert six == [2, 6]  # two ø6 groups, not one merged count-8

    def test_page_pos_is_in_plan_view_coordinate_range(self, holed_plate_drawing):
        dwg = holed_plate_drawing
        a = dwg._analysis
        feats = dwg.features("plan")
        for f in feats:
            px, py = f.page_pos
            # page_pos must lie within the plan view bounds (within half-extents + margin)
            assert abs(px - a.PV_X) <= a.x_size / 2 * a.SCALE + 5
            assert abs(py - a.PV_Y) <= a.y_size / 2 * a.SCALE + 5

    def test_z_axis_holes_absent_from_front_view(self, holed_plate_drawing):
        # The holed plate has only Z-axis holes — none should appear in front
        dwg = holed_plate_drawing
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
