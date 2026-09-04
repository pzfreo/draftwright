"""ADR 2 (was 0018): one owner for "which view does this feature read end-on in", and a named
result when that view is not on the sheet.

View-set selection — the ADR's headline, and the thing the thin-plate case needs — cannot
be attempted while the answer to "which view does this hole belong in" is a bare literal
spelled independently at eleven sites in seven modules — eight copies of the end-on routing
and three of the edge-on companion, one of them documenting the duplication in a comment
rather than resolving it. A mapping copied eleven times cannot be taught that a view is
absent. Measured before this change: building without the plan view
died with `KeyError: 'plan'` raised from inside `render_centermarks`, which is not a
decision, a diagnosis, or something the requirement gate could ever weigh.

Two guards here, and neither claims view selection works yet — it does not. They pin the
preconditions: the routing has exactly one owner, and asking for an absent view produces a
first-class result (ADR 2 (was 0018 §6)) rather than an arbitrary pass's exception.
"""

import pathlib
import re

import pytest
from build123d import Box, Cylinder, Pos

import draftwright.view_plan as view_plan_mod
from draftwright import build_drawing
from draftwright._geometry import _EDGE_ON, _END_ON
from draftwright.drawing import ViewNotPlanned

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "draftwright"
#: The routing table written out by hand, in any dict ordering.
_LITERAL = re.compile(
    r"""\{\s*["'][xyz]["']\s*:\s*["'](?:side|front|plan)["']\s*,"""
    r"""\s*["'][xyz]["']\s*:\s*["'](?:side|front|plan)["']\s*,"""
    r"""\s*["'][xyz]["']\s*:\s*["'](?:side|front|plan)["']\s*,?\s*\}"""
)


class TestTheRoutingHasOneOwner:
    def test_no_module_re_spells_the_axis_to_view_table(self):
        # A ratchet, not a style rule: every extra copy is another site that would have to
        # learn about an absent view independently, and the eight that existed are exactly
        # why a two-view build failed with a KeyError instead of a decision.
        offenders = []
        for path in sorted(_SRC.rglob("*.py")):
            if path.name == "_geometry.py":
                continue  # the owner
            for match in _LITERAL.finditer(path.read_text(encoding="utf-8")):
                line = path.read_text(encoding="utf-8")[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(_SRC)}:{line}")
        assert offenders == [], (
            "the axis->view routing is re-spelled instead of imported from "
            f"`_geometry._END_ON`: {offenders}"
        )

    def test_the_owners_still_say_what_they_always_said(self):
        # The consolidation must not have quietly changed either routing while removing
        # copies — these are the two tables as they were spelled at every site.
        assert _END_ON == {"x": "side", "y": "front", "z": "plan"}
        assert _EDGE_ON == {"x": "front", "y": "side", "z": "front"}

    def test_the_two_routings_are_distinct(self):
        # They are separate decisions, not a duplicate to be collapsed: a z-normal face reads
        # end-on in plan and edge-on in front. Guarding this stops a future tidy-up from
        # merging them because both are three-key axis maps.
        assert _END_ON != _EDGE_ON
        assert _END_ON["z"] == "plan" and _EDGE_ON["z"] == "front"

    def test_every_routed_view_is_a_principal_view(self):
        # Either table may only name views the plan actually builds, or routing would send
        # annotations to a view that never exists.
        principals = {spec.name for spec in view_plan_mod.third_angle_principals()}
        assert set(_END_ON.values()) == principals
        assert set(_EDGE_ON.values()) <= principals


class TestAnAbsentViewIsANamedResult:
    def test_asking_for_a_view_not_on_the_sheet_names_it_and_what_is(self):
        drawing = build_drawing(Box(60, 40, 20))
        with pytest.raises(ViewNotPlanned) as caught:
            drawing.at("elevation", 0, 0, 0)
        assert caught.value.view == "elevation"
        assert "plan" in caught.value.planned
        assert "elevation" in str(caught.value)

    def test_it_stays_a_key_error_for_existing_handlers(self):
        # Named, not a new control-flow contract: anything already catching KeyError around
        # a projection keeps working.
        assert issubclass(ViewNotPlanned, KeyError)

    def test_a_planned_view_still_projects(self):
        # The precondition for the test above meaning anything: `at` must not have become a
        # function that only raises.
        assert build_drawing(Box(60, 40, 20)).at("plan", 0, 0, 0)[2] == 0.0

    def test_dropping_a_view_the_annotations_need_refuses_by_name(self, monkeypatch):
        # The end-to-end state of view-set selection today, pinned honestly: dropping the
        # plan view is REFUSED, and refused in terms of the missing view rather than
        # wherever the first pass happened to trip. Making this build succeed is the work
        # ADR 2 (was 0018) still owes — 53 view literals in the render passes — and this test is
        # what will change shape when it lands.
        principals = view_plan_mod.third_angle_principals

        def without_plan():
            return tuple(spec for spec in principals() if spec.name != "plan")

        # `builder` reaches this through `resolve_from_analysis`, so patching the owning
        # module is what takes effect.
        monkeypatch.setattr(view_plan_mod, "third_angle_principals", without_plan)

        part = Box(90, 60, 20) - Pos(20, 15, 0) * Cylinder(4, 20)
        with pytest.raises(ViewNotPlanned) as caught:
            build_drawing(part)
        assert caught.value.view == "plan"
        assert "plan" not in caught.value.planned
        assert {"front", "side"} <= set(caught.value.planned)
