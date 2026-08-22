"""#1188 — the leader assignment is solved per view, because it separates exactly.

Two facts make it separable: a candidate conflict is only ever built for a same-view
pair, and every term of the lexicographic objective is a sum over jobs. So the optimum
of the whole inventory is the union of the per-view optima — this is a decomposition,
not an approximation, and these tests pin that rather than merely pinning that it is
faster.
"""

from __future__ import annotations

import json

import pytest
from build123d import Box

from draftwright import build_drawing
from draftwright.annotations.leaders import _assign_by_view
from draftwright.layout import _assign_leader_candidates
from draftwright.model import fillet


class TestItIsADecompositionNotAnApproximation:
    def test_one_view_matches_the_undecomposed_solve(self):
        # The reference case: with every job in one view there is nothing to split, so
        # the result must be identical to the solver it wraps.
        costs = [[3.0, 1.0], [2.0, 5.0], [4.0, 4.0]]
        conflicts = [(0, 1, 1, 0)]
        priorities = [1.0, 2.0, 3.0]
        penalties = [[0, 2], [1, 0], [0, 0]]
        direct = _assign_leader_candidates(
            costs, conflicts, priorities=priorities, penalties_by_job=penalties
        )
        split = _assign_by_view(
            ["front"] * 3, costs, conflicts, priorities=priorities, penalties_by_job=penalties
        )
        assert split.choices == direct.choices
        assert split.optimal == direct.optimal

    def test_independent_views_reach_each_view_s_own_optimum(self):
        # Two views, each with a conflict of its own. Splitting must not let one view's
        # constraint leak into the other's answer.
        costs = [[1.0, 9.0], [1.0, 9.0], [1.0, 9.0], [1.0, 9.0]]
        conflicts = [(0, 0, 1, 0), (2, 0, 3, 0)]
        views = ["front", "front", "side", "side"]
        split = _assign_by_view(
            views, costs, conflicts, priorities=[0.0] * 4, penalties_by_job=[[0, 0]] * 4
        )
        # Every job still places; within each view the conflicting pair cannot both take
        # candidate 0, so exactly one of each pair pays the expensive alternative.
        assert all(choice is not None for choice in split.choices)
        assert {split.choices[0], split.choices[1]} == {0, 1}
        assert {split.choices[2], split.choices[3]} == {0, 1}

    def test_a_conflict_spanning_views_fails_closed(self):
        # The guard on the premise this decomposition rests on. Today both call sites
        # build same-view conflicts only, so it never fires — but a cross-view conflict
        # would make the split silently WRONG (each half solved as if the constraint did
        # not exist). Dropping such a pair quietly is the dangerous behaviour; raising is
        # the safe one, so the invariant is asserted rather than assumed.
        from draftwright.annotations.leaders import _FeatureLeaderInvariantError

        with pytest.raises(_FeatureLeaderInvariantError, match="spans views"):
            _assign_by_view(
                ["front", "side"],
                [[1.0], [1.0]],
                [(0, 0, 1, 0)],
                priorities=[0.0, 0.0],
                penalties_by_job=[[0], [0]],
            )

    def test_states_and_optimality_are_aggregated(self):
        costs = [[1.0], [1.0]]
        split = _assign_by_view(
            ["front", "side"], costs, [], priorities=[0.0, 0.0], penalties_by_job=[[0], [0]]
        )
        assert split.optimal is True
        assert split.states >= 0


class TestTheExactSolveNowReachesDenseParts:
    """Before #1188 every fixture with 12+ leader jobs fell back to the greedy floor,
    so Amendment 2's guarantees applied nowhere they were needed."""

    def test_a_dense_fixture_reaches_the_exact_solve(self, tmp_path):
        # Four distinct requirements in each orthographic view. Distinct radii deliberately
        # prevent the renderer's truthful n× collapse: this fixture is about a 12-JOB solve,
        # while physical dense fixtures are free to become less redundant (#1254).
        points_by_axis = {
            "z": ((-50, -40, 0), (-50, 40, 0), (50, -40, 0), (50, 40, 0)),
            "x": ((0, -40, -30), (0, -40, 30), (0, 40, -30), (0, 40, 30)),
            "y": ((-50, 0, -30), (-50, 0, 30), (50, 0, -30), (50, 0, 30)),
        }
        features = []
        for axis, points in points_by_axis.items():
            for point in points:
                features.append(fillet(axis=axis, radius=1 + len(features), at=point))

        trace = tmp_path / "three-view-inventory.json"
        build_drawing(Box(100, 80, 60), model=features, trace=trace, page="A2")
        events = json.loads(trace.read_text())["pass_events"]
        event = next(e for e in events if e.get("label") == "feature_leader_inventory")
        assert event["inventory_jobs"] == 12, "fixture no longer exercises a dense inventory"
        assert event["assignment"] == "joint", (
            f"a {event['inventory_jobs']}-job inventory fell back to "
            f"{event['assignment']!r} — the exact solve must reach dense parts"
        )
        assert event["optimal"] is True
        assert event["objective"]["placed"] == event["inventory_jobs"]
