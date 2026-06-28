"""Prototype tests for the part-drawing compiler IR (ADR 0008).

Proves the architecture's two claims on real geometry:
1. Diverse features (holes + turned steps + bosses), from different detectors,
   flow through ONE planner uniformly.
2. A brand-new `Feature` type is dimensioned with ZERO changes to the planner —
   the Open/Closed property the design exists for.
"""

from dataclasses import dataclass

from build123d import Box, Cylinder, Pos

from draftwright.model import (
    BossFeature,
    DimParameter,
    Frame,
    HoleFeature,
    PartModel,
    StepFeature,
    build_part_model,
    plan_dimensions,
)


def _z_stepped_bored():
    """Vertical stepped shaft (ø30 then ø16) with a blind ø8 axial bore."""
    shaft = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
    return shaft - Pos(0, 0, 45) * Cylinder(4, 20)


class TestBuildPartModel:
    def test_turned_part_yields_steps_and_holes(self):
        model = build_part_model(_z_stepped_bored())
        assert model.orientation == "z"
        kinds = sorted({f.kind for f in model.features})
        assert "step" in kinds and "hole" in kinds
        assert any(isinstance(f, StepFeature) for f in model.features)
        assert any(isinstance(f, HoleFeature) for f in model.features)

    def test_prismatic_part_yields_bosses_not_steps(self):
        # A plate with one cylindrical boss — not a turned part.
        model = build_part_model(Box(80, 60, 10) + Pos(0, 0, 10) * Cylinder(10, 8))
        assert model.orientation is None
        assert any(isinstance(f, BossFeature) for f in model.features)
        assert not any(isinstance(f, StepFeature) for f in model.features)


class TestPlanner:
    def test_diverse_features_flow_through_one_planner(self):
        plan = plan_dimensions(build_part_model(_z_stepped_bored()))
        by_kind = {}
        for pd in plan:
            by_kind.setdefault(pd.param.kind, []).append(pd)
        # step lengths planned as a chain; diameters as leaders
        assert by_kind["length"] and all(pd.convention == "chain" for pd in by_kind["length"])
        assert by_kind["diameter"] and all(pd.convention == "leader" for pd in by_kind["diameter"])
        # the two OD steps (ø30, ø16) and the ø8 bore all appear, de-duplicated
        diams = sorted(pd.param.value for pd in by_kind["diameter"])
        assert diams == [8.0, 16.0, 30.0]

    def test_redundant_diameters_are_dropped(self):
        # Two features reporting the same diameter → one planned dimension.
        feats = [
            BossFeature(frame=Frame((0, 0, 0), "z"), diameter=12.0),
            BossFeature(frame=Frame((50, 0, 0), "z"), diameter=12.0),
        ]
        plan = plan_dimensions(PartModel(bbox=None, orientation=None, features=feats))
        assert [pd.param.value for pd in plan] == [12.0]


class TestOpenClosed:
    """The load-bearing claim: a new shape is a new Feature type, not a new branch."""

    def test_new_feature_type_needs_no_planner_change(self):
        @dataclass(frozen=True)
        class KeywayFeature:
            frame: Frame
            width: float
            length: float
            kind = "keyway"

            def parameters(self):
                return [
                    DimParameter("length", self.length, f"{self.length:.0f}"),
                    DimParameter("length", self.width, f"{self.width:.0f}"),
                ]

            def references(self):
                return []

        model = PartModel(
            bbox=None,
            orientation=None,
            features=[KeywayFeature(Frame((0, 0, 0), "x"), width=4.0, length=20.0)],
        )
        plan = plan_dimensions(model)  # planner is unchanged; it never heard of keyways
        assert sorted(pd.param.value for pd in plan) == [4.0, 20.0]
        assert all(pd.feature_kind == "keyway" for pd in plan)
