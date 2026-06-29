"""Renderer-seam tests (ADR 0008) — the first real consumer of the planner output.

Validates that the IR/planner contract carries what a renderer needs to build a
hole callout: the bore/counterbore values, blind-vs-through, and the pattern count
all reconstruct from the plan, and the seam produces placed callout leaders via the
real projection + helpers. (Content correctness vs the engine is the gate's job at
the #201 swap-in; here we prove the seam mechanically and at the spec level.)
"""

import math

from build123d import Box, Cylinder, Pos
from build123d_drafting.helpers import Leader

from draftwright import build_drawing
from draftwright.annotations.from_model import hole_callout_spec, render_callouts
from draftwright.model import build_part_model, plan_dimensions


def _groups(part):
    return plan_dimensions(build_part_model(part))


def _hole_or_pattern_spec(part):
    g = next(g for g in _groups(part) if g.feature_kind in ("hole", "pattern"))
    return hole_callout_spec(g)


class TestCalloutSpec:
    def test_simple_through_hole(self):
        part = Box(60, 60, 12) - Pos(0, 0, 0) * Cylinder(4, 30)
        s = _hole_or_pattern_spec(part)
        assert s["diameter"] == 8.0 and s["through"] is True
        assert s["count"] is None and s["cbore_dia"] is None

    def test_blind_hole_carries_depth(self):
        part = Box(60, 60, 20) - Pos(0, 0, 6) * Cylinder(4, 16)  # blind ø8
        s = _hole_or_pattern_spec(part)
        assert s["diameter"] == 8.0 and s["through"] is False and s["depth"] is not None

    def test_counterbored_hole(self):
        part = Box(60, 60, 16) - Pos(0, 0, 0) * Cylinder(4, 30) - Pos(0, 0, 4) * Cylinder(8, 12)
        s = _hole_or_pattern_spec(part)
        assert s["diameter"] == 8.0
        assert s["cbore_dia"] == 16.0 and s["cbore_depth"] is not None

    def test_bolt_circle_is_one_counted_callout_with_bc_suffix(self):
        part = Cylinder(40, 8)
        for i in range(6):
            a = i * math.pi / 3
            part -= Pos(25 * math.cos(a), 25 * math.sin(a), 0) * Cylinder(3, 20)
        s = _hole_or_pattern_spec(part)
        assert s["diameter"] == 6.0 and s["count"] == 6  # 6× ø6, not six callouts
        assert s["suffix"] is not None and "BC" in s["suffix"]  # BCD in the suffix

    def test_spotface_maps_to_the_step(self):
        # The renderer must not drop a spotface (review): step = cbore or spotface.
        from draftwright.model import Frame, HoleFeature, PartModel

        hole = HoleFeature(
            Frame((0, 0, 0), "z"), 6.0, depth=None, through=True, spotface=(14.0, 1.0)
        )
        g = plan_dimensions(PartModel(bbox=None, orientation=None, features=[hole]))[0]
        s = hole_callout_spec(g)
        assert s["cbore_dia"] == 14.0 and s["cbore_depth"] == 1.0


class TestRender:
    def test_produces_placed_callout_leaders(self):
        part = Box(80, 60, 12) - Pos(20, 0, 0) * Cylinder(4, 30) - Pos(-20, 0, 0) * Cylinder(4, 30)
        dwg = build_drawing(part, number="X")
        anns = render_callouts(dwg, _groups(part))
        # The two identical holes group into one ``2×`` callout (machining-spec
        # grouping in the IR), so one placed leader — not one per hole.
        assert len(anns) == 1 and all(isinstance(a, Leader) for a in anns)

    def test_bolt_circle_renders_one_leader_pointing_at_a_member(self):
        part = Cylinder(40, 8)
        for i in range(6):
            a = i * math.pi / 3
            part -= Pos(25 * math.cos(a), 25 * math.sin(a), 0) * Cylinder(3, 20)
        dwg = build_drawing(part, number="X")
        groups = _groups(part)
        pat = next(g for g in groups if g.feature_kind == "pattern")
        anns = render_callouts(dwg, groups)
        assert len(anns) == 1  # one grouped callout for the whole bolt circle
        # the leader tips at a member hole, not the empty pattern centre
        member_tip = dwg.at(pat.view, *pat.feature.members[0])
        assert abs(anns[0].tip[0] - member_tip[0]) < 1e-6
        assert abs(anns[0].tip[1] - member_tip[1]) < 1e-6
