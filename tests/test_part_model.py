"""Prototype tests for the part-drawing compiler IR (ADR 0008).

Proves the architecture's claims on real geometry:
1. Diverse features (holes + turned steps + bosses), from different detectors,
   flow through ONE planner uniformly.
2. A brand-new `Feature` type is dimensioned with ZERO changes to the planner.
3. The contract survives compound, same-value features (the counterbore review):
   feature grouping is preserved and redundancy is feature-aware, not value-blind.
"""

from dataclasses import dataclass

from build123d import Box, Cylinder, Pos

from draftwright.model import (
    BossFeature,
    DimensionGroup,
    DimParameter,
    Frame,
    HoleFeature,
    PartModel,
    StepFeature,
    build_part_model,
    display,
    plan_dimensions,
)


def _z_stepped_bored():
    """Vertical stepped shaft (ø30 then ø16) with a blind ø8 axial bore."""
    shaft = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
    return shaft - Pos(0, 0, 45) * Cylinder(4, 20)


def _all_dims(groups):
    return [pd for g in groups for pd in g.dims]


class TestBuildPartModel:
    def test_turned_part_yields_steps_and_holes(self):
        model = build_part_model(_z_stepped_bored())
        assert model.orientation == "z"
        kinds = sorted({f.kind for f in model.features})
        assert "step" in kinds and "hole" in kinds

    def test_counterbored_hole_emits_cbore_parameters(self):
        # ø8 bore + ø16 counterbore: both surface, distinguished by `role`.
        part = Box(60, 60, 16) - Pos(0, 0, 0) * Cylinder(4, 30) - Pos(0, 0, 4) * Cylinder(8, 12)
        model = build_part_model(part)
        hole = next(f for f in model.features if isinstance(f, HoleFeature))
        assert hole.cbore is not None
        params = {(p.kind, p.role): p.value for p in hole.parameters()}
        assert params[("diameter", "bore")] == 8.0
        assert params[("diameter", "counterbore")] == 16.0
        assert ("depth", "counterbore") in params

    def test_prismatic_part_yields_bosses_not_steps(self):
        model = build_part_model(Box(80, 60, 10) + Pos(0, 0, 10) * Cylinder(10, 8))
        assert model.orientation is None
        assert any(isinstance(f, BossFeature) for f in model.features)
        assert not any(isinstance(f, StepFeature) for f in model.features)


class TestPlanner:
    def test_diverse_features_flow_through_one_planner(self):
        groups = plan_dimensions(build_part_model(_z_stepped_bored()))
        dims = _all_dims(groups)
        lengths = [pd for pd in dims if pd.param.kind == "length"]
        diams = [pd for pd in dims if pd.param.kind == "diameter"]
        assert lengths and all(pd.convention == "chain" for pd in lengths)
        assert diams and all(pd.convention == "leader" for pd in diams)
        assert sorted({pd.param.value for pd in diams}) == [8.0, 16.0, 30.0]

    def test_compound_hole_callout_is_one_group_one_view_with_anchor(self):
        # bore + counterbore + depth of one hole must be ONE group, in a SINGLE
        # view, with the feature anchor — so it renders as one placeable callout
        # (the grouping + per-param-view + missing-anchor issues from review 2).
        part = Box(60, 60, 16) - Pos(0, 0, 0) * Cylinder(4, 30) - Pos(0, 0, 4) * Cylinder(8, 12)
        groups = plan_dimensions(build_part_model(part))
        hole_groups = [g for g in groups if g.feature_kind == "hole"]
        assert len(hole_groups) == 1
        g = hole_groups[0]
        roles = {(pd.param.kind, pd.param.role) for pd in g.dims}
        assert ("diameter", "bore") in roles and ("diameter", "counterbore") in roles
        assert isinstance(g.view, str) and g.view  # one view for the whole group
        assert g.anchor is not None and len(g.anchor) == 3  # placeable

    def test_no_value_blind_collapse(self):
        # A counterbore ø16 and a boss ø16 share a value but differ in role —
        # BOTH survive (the dedup-collapse bug from review 1).
        hole = HoleFeature(
            Frame((0, 0, 0), "z"), diameter=8.0, depth=None, through=True, cbore=(16.0, 10.0)
        )
        boss = BossFeature(Frame((50, 0, 0), "z"), diameter=16.0)
        groups = plan_dimensions(PartModel(bbox=None, orientation=None, features=[hole, boss]))
        diam_values = sorted(
            pd.param.value for pd in _all_dims(groups) if pd.param.kind == "diameter"
        )
        assert diam_values == [8.0, 16.0, 16.0]

    def test_same_value_distinct_params_both_survive(self):
        # A 10x10 pocket emits two orthogonal 10 mm lengths; both must survive — the
        # within-feature dedup-by-value bug the review reproduced.
        @dataclass(frozen=True)
        class PocketFeature:
            frame: Frame
            kind = "pocket"

            def parameters(self):
                return [
                    DimParameter("length", "pocket", 10.0, span=((0, 0, 0), (10, 0, 0))),
                    DimParameter("length", "pocket", 10.0, span=((0, 0, 0), (0, 10, 0))),
                ]

            def references(self):
                return []

        groups = plan_dimensions(
            PartModel(bbox=None, orientation=None, features=[PocketFeature(Frame((0, 0, 0), "z"))])
        )
        assert len(_all_dims(groups)) == 2

    def test_group_view_follows_axis_not_hardcoded(self):
        # A diameter callout's view is the end-on view of the feature's axis, by
        # one rule for all axes — so an X-axis hole and a Z-axis hole are NOT both
        # forced to 'plan' (the orientation-blind bug from the hard re-review).
        from build123d import Rotation

        z_hole = Box(60, 60, 20) - Pos(0, 0, 0) * Cylinder(5, 40)  # axial bore, Z
        x_hole = Rotation(0, 90, 0) * z_hole  # same, turned to X

        def hole_view(part):
            g = next(
                g for g in plan_dimensions(build_part_model(part)) if g.feature_kind == "hole"
            )
            return g.view

        assert hole_view(z_hole) == "plan"  # Z hole seen end-on in plan
        assert hole_view(x_hole) == "side"  # X hole seen end-on in side — not 'plan'

    def test_labels_are_font_safe(self):
        # display() must not emit GD&T glyphs the pinned font lacks (⌴/⌵/↧).
        for sym in ("⌴", "⌵", "↧"):
            assert sym not in display(DimParameter("depth", "counterbore", 10.0))


class TestOpenClosed:
    def test_new_feature_type_needs_no_planner_change(self):
        @dataclass(frozen=True)
        class KeywayFeature:
            frame: Frame
            width: float
            length: float
            kind = "keyway"

            def parameters(self):
                return [
                    DimParameter("length", "keyway", self.length),
                    DimParameter("length", "keyway", self.width),
                ]

            def references(self):
                return []

        model = PartModel(
            bbox=None,
            orientation=None,
            features=[KeywayFeature(Frame((0, 0, 0), "x"), width=4.0, length=20.0)],
        )
        groups = plan_dimensions(model)  # planner never heard of keyways
        assert isinstance(groups[0], DimensionGroup) and groups[0].feature_kind == "keyway"
        assert sorted(pd.param.value for pd in _all_dims(groups)) == [4.0, 20.0]
