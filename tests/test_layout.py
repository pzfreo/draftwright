"""Tests for draftwright.layout — the ADR 0003 1D strip primitives + fit_box.

These exercise the placement primitives in isolation, with no drawing build,
which is the point of putting them in their own module.
"""

import sys
import tracemalloc

import pytest

import draftwright.layout as L
from draftwright.layout import (
    _ANCHOR_WEIGHT,
    FitBoxTrace,
    _assign_balloon_bands,
    _greedy_strip_1d,
    _solve_guarded_strip_1d,
    _solve_segmented_strip_1d,
    _solve_strip_1d,
    _solve_strip_1d_pava,
    fit_box,
)

# Pure solver unit tests — fast, no OCC builds — so the whole module is part of
# the build-light `smoke` subset (#153).
pytestmark = pytest.mark.smoke


class TestAssignBalloonBands:
    def test_nearest_band_remains_the_default(self):
        members = ["a", "b", "c"]
        choices = [{"left": 1.0, "top": 100.0} for _ in members]

        bands, dropped = _assign_balloon_bands(members, choices, {"left": 3, "top": 3})

        assert bands["left"] == members
        assert bands["top"] == []
        assert dropped == 0

    def test_near_band_preference_can_outweigh_small_distance_cost(self):
        members = ["a", "b", "c"]
        choices = [{"left": 1.0, "top": 20.0} for _ in members]

        bands, dropped = _assign_balloon_bands(
            members,
            choices,
            {"left": 3, "top": 3},
            prefer_bands=("left", "top"),
            preference_limit=25.0,
        )

        assert len(bands["left"]) == 2
        assert len(bands["top"]) == 1
        assert dropped == 0

    def test_preference_does_not_force_a_remote_band(self):
        members = ["a", "b", "c"]
        choices = [{"left": 1.0, "top": 100.0} for _ in members]

        bands, dropped = _assign_balloon_bands(
            members,
            choices,
            {"left": 3, "top": 3},
            prefer_bands=("left", "top"),
            preference_limit=25.0,
        )

        assert bands["left"] == members
        assert bands["top"] == []
        assert dropped == 0

    def test_required_members_win_before_optional_leader_cost(self):
        members = ["required-row", "optional-marker"]
        choices = [{"left": 100.0}, {"left": 1.0}]

        ordinary, ordinary_dropped = _assign_balloon_bands(members, choices, {"left": 1})
        prioritised, prioritised_dropped = _assign_balloon_bands(
            members,
            choices,
            {"left": 1},
            required_count=1,
        )

        assert ordinary["left"] == ["optional-marker"]
        assert prioritised["left"] == ["required-row"]
        assert ordinary_dropped == prioritised_dropped == 1

    def test_required_count_must_address_the_member_prefix(self):
        with pytest.raises(ValueError, match="required_count"):
            _assign_balloon_bands(["row"], [{"left": 1.0}], {"left": 1}, required_count=2)


class TestSolveSegmentedStrip1d:
    def test_empty_members_need_no_segments(self):
        assert _solve_segmented_strip_1d([], 10.0, []) == []

    def test_uses_disjoint_segments_without_crossing_member_order(self):
        result = _solve_segmented_strip_1d([8.0, 12.0, 88.0], 10.0, [(0.0, 20.0), (80.0, 100.0)])
        assert result is not None
        assert 0.0 <= result[0] < result[1] <= 20.0
        assert result[1] - result[0] >= 10.0
        assert result[2] == pytest.approx(88.0)

    def test_chooses_the_minimum_leader_length_partition(self):
        assert _solve_segmented_strip_1d(
            [5.0, 45.0, 55.0], 10.0, [(0.0, 20.0), (40.0, 60.0)]
        ) == pytest.approx([5.0, 45.0, 55.0])

    def test_rejects_segments_too_close_to_preserve_the_global_gap(self):
        with pytest.raises(ValueError, match="separated by gap"):
            _solve_segmented_strip_1d([4.0, 6.0], 5.0, [(0.0, 5.0), (6.0, 15.0)])

    def test_returns_none_when_combined_capacity_is_too_small(self):
        assert (
            _solve_segmented_strip_1d([0.0, 10.0, 20.0], 10.0, [(0.0, 5.0), (20.0, 25.0)]) is None
        )

    def test_prefix_mode_places_the_largest_leading_subset(self):
        assert _solve_segmented_strip_1d(
            [0.0, 10.0, 20.0],
            10.0,
            [(0.0, 5.0), (20.0, 25.0)],
            prefix=True,
        ) == [0.0, 20.0]


class TestSolveStrip1d:
    def test_feasible_positions_respect_bounds_and_gap(self):
        out = _solve_strip_1d([10, 11, 12], min_gap=5, lo=0, hi=100)
        assert out is not None and len(out) == 3
        assert all(0 <= v <= 100 for v in out)
        assert out[1] - out[0] >= 5 - 1e-9
        assert out[2] - out[1] >= 5 - 1e-9

    def test_unconstrained_values_sit_at_their_naturals(self):
        # Already gap-apart and in range → solved values equal the naturals.
        out = _solve_strip_1d([0, 20, 40], min_gap=5, lo=-50, hi=50)
        assert out == [0, 20, 40]

    def test_provably_infeasible_returns_none(self):
        # 3 items need 2*min_gap = 40 of span but only 10 is available.
        assert _solve_strip_1d([0, 0, 0], min_gap=20, lo=0, hi=10) is None

    def test_empty_returns_empty(self):
        assert _solve_strip_1d([], min_gap=5, lo=0, hi=10) == []

    def test_guarded_solve_reassigns_earlier_coordinate_jointly(self):
        # A greedy retry holds B's original 5 mm coordinate, drops A, then moves
        # B to 10.  The joint solve sees the complete inventory and places both.
        assert _solve_guarded_strip_1d(
            [0.0, 5.0],
            5.0,
            [[(5.0, 5.0)], [(0.0, 0.0), (10.0, 10.0)]],
        ) == [5.0, 10.0]

    def test_guarded_solve_uses_geometry_endpoints_not_a_sampling_grid(self):
        assert _solve_guarded_strip_1d(
            [0.0, 5.0],
            5.0,
            [[(0.3, 0.3)], [(5.3, 5.3)]],
        ) == [0.3, 5.3]

    def test_guarded_solve_rejects_invalid_or_empty_constraints(self):
        with pytest.raises(ValueError, match="equal length"):
            _solve_guarded_strip_1d([0.0], 5.0, [])
        assert _solve_guarded_strip_1d([0.0], 5.0, [[]]) is None
        with pytest.raises(ValueError, match="positive"):
            _solve_guarded_strip_1d([0.0], 0.0, [[(0.0, 1.0)]])
        with pytest.raises(ValueError, match="descending"):
            _solve_guarded_strip_1d([0.0], 5.0, [[(2.0, 1.0)]])

    def test_guarded_solve_fails_closed_before_fragmented_interval_scan_explodes(self):
        # A valid dense sheet may leave dozens of disjoint free intervals per
        # balloon after retained-label keep-outs are carved.  This inventory
        # exceeds the deterministic interval-membership work budget.
        count = 61
        allowed = [
            [
                (float(interval * 30 + member), float(interval * 30 + member + 20))
                for interval in range(40)
            ]
            for member in range(count)
        ]

        assert (
            _solve_guarded_strip_1d(
                [float(member * 11) for member in range(count)],
                11.0,
                allowed,
            )
            is None
        )

    def test_guarded_solve_applies_budget_before_materialising_endpoint_sources(self, monkeypatch):
        import draftwright.layout as layout_module

        class Endpoint(float):
            def __float__(self):
                raise AssertionError("budgeted endpoint was materialised")

        monkeypatch.setattr(layout_module, "_GUARDED_STRIP_MAX_INTERVAL_PROBES", 1)

        assert (
            _solve_guarded_strip_1d(
                [0.0],
                1.0,
                [[(Endpoint(0.0), Endpoint(1.0)), (Endpoint(2.0), Endpoint(3.0))]],
            )
            is None
        )

    def test_guarded_solve_state_budget_is_independently_load_bearing(self, monkeypatch):
        import draftwright.layout as layout_module

        count = 20
        naturals = [member * 11.037 for member in range(count)]
        allowed = [
            [
                (
                    block * 400.123 + member * 0.137,
                    block * 400.123 + member * 0.137 + 300.0,
                )
                for block in range(3)
            ]
            for member in range(count)
        ]
        monkeypatch.setattr(layout_module, "_GUARDED_STRIP_MAX_INTERVAL_PROBES", 10_000_000)
        monkeypatch.setattr(layout_module, "_GUARDED_STRIP_MAX_STATES", 20_000)
        assert _solve_guarded_strip_1d(naturals, 11.0, allowed) is None

        # The inventory is feasible; only the explicit state budget rejects it.
        monkeypatch.setattr(layout_module, "_GUARDED_STRIP_MAX_STATES", 10_000_000)
        assert _solve_guarded_strip_1d(naturals, 11.0, allowed) == naturals

    def test_guarded_solve_caps_the_first_source_expansion(self, monkeypatch):
        import draftwright.layout as layout_module

        monkeypatch.setattr(layout_module, "_GUARDED_STRIP_MAX_STATES", 4)
        monkeypatch.setattr(layout_module, "_GUARDED_STRIP_MAX_INTERVAL_PROBES", 1_000)

        assert (
            _solve_guarded_strip_1d(
                [0.0, 5.0],
                5.0,
                [[(0.0, 10.0)], [(0.0, 10.0)]],
            )
            is None
        )

    def test_guarded_solve_keeps_a_dense_unfragmented_band_within_budget(self):
        naturals = [float(member * 11) for member in range(61)]

        assert (
            _solve_guarded_strip_1d(
                naturals,
                11.0,
                [[(0.0, 1000.0)] for _member in naturals],
            )
            == naturals
        )

    def test_deterministic_across_runs(self):
        args = ([1.0, 1.0, 1.0, 1.0], 3.0, 0.0, 100.0)
        assert _solve_strip_1d(*args) == _solve_strip_1d(*args)

    def test_works_without_kiwisolver(self, monkeypatch):
        # kiwisolver is retired (#507): the primitive delegates to the pure-Python PAVA
        # solve and must never import it — force kiwisolver unavailable and assert the
        # solve still produces the correct spaced placement (would ImportError if it tried).
        monkeypatch.setitem(sys.modules, "kiwisolver", None)
        assert _solve_strip_1d([0, 0, 0], min_gap=5, lo=0, hi=100) == [0, 5, 10]


class TestGreedyStrip1d:
    def test_overflow_returns_none_by_default(self):
        assert _greedy_strip_1d([0, 0, 0], min_gap=20, lo=0, hi=10) is None

    def test_prefix_mode_places_what_fits(self):
        # Only the first two fit in [0, 10] at gap 8 → prefix stops before #3.
        out = _greedy_strip_1d([0, 0, 0], min_gap=8, lo=0, hi=10, prefix=True)
        assert out == [0, 8]


class TestSolveStrip1dPava:
    """P4b (#318, ADR 0009 Amendment 4): minimum-total-leader-length placement via
    weighted-median PAVA — the exact L1 optimum the earlier ``scipy.optimize.linprog``
    prototype computed, but deterministic **by construction** (no solver-vertex
    ambiguity on the common non-unique optimum), and with anchoring support."""

    def test_empty_and_single(self):
        assert _solve_strip_1d_pava([], [], 0.0, 10.0) == []
        assert _solve_strip_1d_pava([5.0], [], 0.0, 10.0) == [5.0]

    def test_already_spaced_naturals_are_untouched(self):
        # No pull needed anywhere → the L1-optimal placement is the naturals
        # themselves, same as the satisfaction solve would give.
        out = _solve_strip_1d_pava([0.0, 20.0, 40.0], [5.0, 5.0], -50.0, 50.0)
        assert out == pytest.approx([0.0, 20.0, 40.0])

    def test_infeasible_returns_none(self):
        # sum(gaps) = 14 > span 10 → None, same provably-infeasible contract
        # as _solve_strip_1d.
        assert _solve_strip_1d_pava([0.0, 0.0, 0.0], [4.0, 10.0], 0.0, 10.0) is None

    def test_honours_bounds_and_gaps(self):
        out = _solve_strip_1d_pava([10.0, 11.0, 12.0], [5.0, 5.0], 0.0, 100.0)
        assert out is not None
        assert out[1] - out[0] == pytest.approx(5.0)
        assert out[2] - out[1] == pytest.approx(5.0)
        assert all(0.0 <= v <= 100.0 for v in out)

    def test_minimises_total_leader_length_not_just_satisfies(self):
        # Cassowary's "strong pull toward natural" can settle on any feasible
        # point; PAVA must find the true L1-minimising one. For naturals
        # [10, 7, 4] needing >=5 gap apart (crowded, conflicting pulls), the
        # weighted-median-of-shifted-naturals optimum is [2, 7, 12] (total
        # deviation 16) — the shift q_i = x_i - cumulative gap gives the
        # strictly-decreasing [10, 2, -6]; one pool, weighted median -6..10 -> 2.
        out = _solve_strip_1d_pava([10.0, 7.0, 4.0], [5.0, 5.0], -100.0, 100.0)
        assert out == pytest.approx([2.0, 7.0, 12.0])

    def test_box_clamp_is_exact_not_post_hoc(self):
        # All naturals below lo: the whole block clamps up to lo (exact for L1
        # after the gap-shift, since the box reduces to a GLOBAL [lo, hi-Σgap]).
        out = _solve_strip_1d_pava([-5.0, -4.0], [3.0], 0.0, 100.0)
        assert out == pytest.approx([0.0, 3.0])

    def test_deterministic_by_construction(self):
        # No solver, no platform-dependent vertex choice: the non-unique-optimum
        # case x=[4,3,2,1] (any constant in [2,3] is equally L1-optimal) resolves
        # to the SAME value every call/platform — the lower weighted median (2.0).
        # This is what the scipy/HiGHS route could NOT guarantee (its tie-break
        # diverged across the CI matrix's two scipy builds — Amendment 4).
        out = _solve_strip_1d_pava([4.0, 3.0, 2.0, 1.0], [0.0, 0.0, 0.0], -100.0, 100.0)
        assert out == pytest.approx([2.0, 2.0, 2.0, 2.0])
        assert out == _solve_strip_1d_pava([4.0, 3.0, 2.0, 1.0], [0.0, 0.0, 0.0], -100.0, 100.0)

    def test_anchor_weight_pins_a_candidate_on_a_tie(self):
        # naturals [100, 102] need a 7 mm gap; total-leader-length is a tie
        # between [95,102] (move #0) and [100,107] (move #1). A dominating weight
        # on a candidate keeps IT at its natural and flows the other around it —
        # how a central hole's callout stays on the view-centre row (Amendment 4).
        assert _solve_strip_1d_pava([100.0, 102.0], [7.0], 0.0, 200.0, [_ANCHOR_WEIGHT, 1.0]) == (
            pytest.approx([100.0, 107.0])
        )
        assert _solve_strip_1d_pava([100.0, 102.0], [7.0], 0.0, 200.0, [1.0, _ANCHOR_WEIGHT]) == (
            pytest.approx([95.0, 102.0])
        )
        # unweighted default keeps the deterministic lower-median vertex
        assert _solve_strip_1d_pava([100.0, 102.0], [7.0], 0.0, 200.0) == pytest.approx(
            [95.0, 102.0]
        )


class TestFitBox:
    """#93: 2D free-rectangle box placement (tables, GD&T frames, BOM)."""

    def test_empty_region_places_at_preferred_corner(self):
        # No obstacles: a 20x10 box prefers the bottom-right of a 100x100 region.
        assert fit_box((20, 10), (0, 0, 100, 100), [], "br") == (80, 0)
        assert fit_box((20, 10), (0, 0, 100, 100), [], "bl") == (0, 0)
        assert fit_box((20, 10), (0, 0, 100, 100), [], "tr") == (80, 90)
        assert fit_box((20, 10), (0, 0, 100, 100), [], "tl") == (0, 90)

    def test_box_avoids_obstacles(self):
        # An obstacle fills the bottom-right; the box must sit clear of it.
        pos = fit_box((20, 20), (0, 0, 100, 100), [(50, 0, 100, 50)], "br")
        assert pos is not None
        x0, y0 = pos
        # Placed box must not overlap the obstacle.
        assert not (x0 < 100 and 50 < x0 + 20 and y0 < 50 and 0 < y0 + 20)

    def test_returns_none_when_it_cannot_fit(self):
        assert fit_box((200, 10), (0, 0, 100, 100), [], "br") is None  # too wide
        assert fit_box((10, 200), (0, 0, 100, 100), [], "br") is None  # too tall
        # A single obstacle leaving no 60-wide gap anywhere.
        assert fit_box((60, 60), (0, 0, 100, 100), [(20, 0, 80, 100)], "br") is None

    def test_named_rejection_trace_comes_from_the_exact_candidate_solve(self):
        trace = FitBoxTrace()
        assert (
            fit_box(
                (60, 60),
                (0, 0, 100, 100),
                [("central keep-out", (20, 0, 80, 100))],
                "br",
                trace=trace,
            )
            is None
        )
        assert trace.violation is None
        assert trace.attempted_candidates == trace.rejected_candidates >= len(trace.rejected) > 0
        assert all(rejection.blockers == ("central keep-out",) for rejection in trace.rejected)
        # The first record is the genuinely nearest preferred candidate, not a
        # separately reconstructed explanation of the failure.
        assert trace.rejected[0].region == (40, 0, 100, 60)

    def test_trace_attributes_the_nearest_candidate_to_its_distinct_blocker(self):
        trace = FitBoxTrace()
        quadrants = [
            ("bottom-left", (0, 0, 50, 50)),
            ("bottom-right", (50, 0, 100, 50)),
            ("top-left", (0, 50, 50, 100)),
            ("top-right", (50, 50, 100, 100)),
        ]
        assert fit_box((20, 20), (0, 0, 100, 100), quadrants, "tr", trace=trace) is None
        assert trace.rejected[0].region == (80, 80, 100, 100)
        assert trace.rejected[0].blockers == ("top-right",)

    def test_rejection_trace_samples_are_bounded_while_total_remains_exact(self):
        trace = FitBoxTrace(max_rejections=2)
        assert (
            fit_box(
                (60, 60),
                (0, 0, 100, 100),
                [("central keep-out", (20, 0, 80, 100))],
                trace=trace,
            )
            is None
        )
        assert len(trace.rejected) == 2
        assert trace.rejected_candidates == trace.attempted_candidates == 6

    def test_blocker_scan_short_circuits_after_bounded_trace_sample(self, monkeypatch):
        # The first obstacle rejects every candidate. Once the two retained
        # diagnostic samples are full, each remaining candidate must stop there
        # rather than sorting every colliding name (a 30x no-fit regression).
        obstacles = [("cover", (0, 0, 100, 100))]
        obstacles.extend(
            (f"edge-{index}", (index + 0.1, index + 0.2, index + 0.3, index + 0.4))
            for index in range(30)
        )
        overlap_calls = 0
        original = L._boxes_overlap

        def counted(left, right):
            nonlocal overlap_calls
            overlap_calls += 1
            return original(left, right)

        monkeypatch.setattr(L, "_boxes_overlap", counted)
        trace = FitBoxTrace(max_rejections=2)
        assert fit_box((20, 20), (0, 0, 100, 100), obstacles, "tr", trace=trace) is None

        preprocessing = len(obstacles)
        sampled_scans = len(trace.rejected) * len(obstacles)
        short_circuited_scans = trace.rejected_candidates - len(trace.rejected)
        assert overlap_calls == preprocessing + sampled_scans + short_circuited_scans

    def test_trace_explains_a_usable_region_size_violation(self):
        trace = FitBoxTrace()
        assert fit_box((110, 120), (0, 0, 100, 100), [], trace=trace) is None
        assert trace.attempted_candidates == 0
        assert trace.violation == (
            "width exceeds usable region by 10.0 mm; height exceeds usable region by 20.0 mm"
        )

    def test_clearance_inflates_obstacles_and_rejects_negative_values(self):
        obstacle = [("wall", (20, 0, 40, 100))]
        assert fit_box((20, 20), (0, 0, 100, 100), obstacle, "bl") == (0, 0)
        assert fit_box((20, 20), (0, 0, 100, 100), obstacle, "bl", clearance=2) == (42, 0)
        with pytest.raises(ValueError, match="non-negative"):
            fit_box((20, 20), (0, 0, 100, 100), obstacle, clearance=-0.1)

    def test_deterministic(self):
        args = ((20, 10), (0, 0, 100, 100), [(30, 30, 60, 60)], "br")
        assert fit_box(*args) == fit_box(*args)

    def test_interior_obstacle_is_avoided(self):
        # An obstacle floating in the interior must be cleared (exercises the
        # box-vs-obstacle rejection, not just the cut-line filter).
        pos = fit_box((30, 30), (0, 0, 100, 100), [(40, 40, 60, 60)], "br")
        assert pos is not None
        x0, y0 = pos
        assert not (x0 < 60 and 40 < x0 + 30 and y0 < 60 and 40 < y0 + 30)

    def test_fits_into_an_l_shaped_pocket(self):
        # Completeness: the only fit is tucked against three obstacles.
        obstacles = [(0, 0, 40, 100), (60, 0, 100, 100), (40, 60, 60, 100)]
        assert fit_box((20, 60), (0, 0, 100, 100), obstacles, "br") == (40, 0)

    def test_order_independent(self):
        a = [(30, 30, 60, 60), (10, 10, 20, 20), (70, 70, 90, 90)]
        assert fit_box((20, 20), (0, 0, 100, 100), a, "br") == fit_box(
            (20, 20), (0, 0, 100, 100), list(reversed(a)), "br"
        )

    def test_stays_fast_with_many_obstacles(self):
        # O(n^3): dozens of obstacles (the hole-table case) must place quickly,
        # not blow up like the old O(n^4) form. Just assert it returns.
        obstacles = [(i, i, i + 2, i + 2) for i in range(0, 200, 5)]
        assert fit_box((20, 20), (0, 0, 300, 300), obstacles, "tr") is not None

    def test_candidate_cartesian_product_is_streamed_with_bounded_live_memory(self):
        # 400 sparse boxes produce >640k distinct X×Y candidate pairs, but the
        # preferred A4 corner is immediately free. Materialising those triples
        # consumed hundreds of MiB; the separable-score heap needs O(n) state.
        obstacles = [
            (16 + i * 0.2, 16 + i * 0.1, 16.05 + i * 0.2, 16.05 + i * 0.1) for i in range(400)
        ]
        tracemalloc.start()
        try:
            assert fit_box((7.51, 14.03), (16, 16, 281, 194), obstacles, "tr") == pytest.approx(
                (273.49, 179.97)
            )
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 5_000_000


def test_layout_engine_is_wired_into_the_drawing_path():
    # The hole-callout Y-stack flows through the shared layout engine. Phase 2 (#80)
    # wired it via the LayoutSolver; ADR 0009 P1a (#321) re-routed it through the
    # collect-then-solve seam — plan_strip over StripCandidates — so this guard now
    # tracks that seam (the first production placer on it).
    src = (L.__file__).replace("layout.py", "annotations/holes.py")
    text = open(src).read()
    assert "StripCandidate(" in text
    assert "plan_strip(" in text
