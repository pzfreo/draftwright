"""Tests for draftwright.layout — the ADR 0003 1D strip primitives + fit_box.

These exercise the placement primitives in isolation, with no drawing build,
which is the point of putting them in their own module.
"""

import pytest

import draftwright.layout as L
from draftwright.layout import (
    _ANCHOR_WEIGHT,
    _feasible_segments,
    _greedy_strip_1d,
    _greedy_strip_1d_var,
    _snap_out_of_bands,
    _solve_strip_1d,
    _solve_strip_1d_pava,
    _solve_strip_1d_pava_banded,
    _solve_strip_1d_var,
    fit_box,
)

# Pure solver unit tests — fast, no OCC builds — so the whole module is part of
# the build-light `smoke` subset (#153).
pytestmark = pytest.mark.smoke


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

    def test_deterministic_across_runs(self):
        args = ([1.0, 1.0, 1.0, 1.0], 3.0, 0.0, 100.0)
        assert _solve_strip_1d(*args) == _solve_strip_1d(*args)

    def test_works_without_kiwisolver(self, monkeypatch):
        # kiwisolver is retired (#507): the primitive delegates to the pure-Python PAVA
        # solve and must never import it — force kiwisolver unavailable and assert the
        # solve still produces the correct spaced placement (would ImportError if it tried).
        monkeypatch.setitem(__import__("sys").modules, "kiwisolver", None)
        assert _solve_strip_1d([0, 0, 0], min_gap=5, lo=0, hi=100) == [0, 5, 10]


class TestGreedyStrip1d:
    def test_overflow_returns_none_by_default(self):
        assert _greedy_strip_1d([0, 0, 0], min_gap=20, lo=0, hi=10) is None

    def test_prefix_mode_places_what_fits(self):
        # Only the first two fit in [0, 10] at gap 8 → prefix stops before #3.
        out = _greedy_strip_1d([0, 0, 0], min_gap=8, lo=0, hi=10, prefix=True)
        assert out == [0, 8]


class TestPerPairGaps:
    """#81: per-pair gaps in the 1D primitive (heterogeneous slot depths)."""

    def test_var_honours_each_pair_gap(self):
        # All naturals at 0; gaps [4, 10] → packs to 0, 4, 14.
        out = _solve_strip_1d_var([0.0, 0.0, 0.0], [4.0, 10.0], 0.0, 100.0)
        assert out is not None
        assert out[1] - out[0] >= 4.0 - 1e-9
        assert out[2] - out[1] >= 10.0 - 1e-9

    def test_var_matches_scalar_for_uniform_gaps(self):
        # The uniform special case must reproduce the scalar primitive exactly.
        naturals = [1.0, 1.0, 1.0, 1.0]
        var = _solve_strip_1d_var(naturals, [3.0, 3.0, 3.0], 0.0, 100.0)
        scalar = _solve_strip_1d(naturals, 3.0, 0.0, 100.0)
        assert var == scalar

    def test_var_infeasible_when_gaps_exceed_span(self):
        # sum(gaps) = 14 > span 10 → None.
        assert _solve_strip_1d_var([0.0, 0.0, 0.0], [4.0, 10.0], 0.0, 10.0) is None

    def test_var_empty_and_single(self):
        assert _solve_strip_1d_var([], [], 0.0, 10.0) == []
        assert _solve_strip_1d_var([5.0], [], 0.0, 10.0) == [5.0]

    def test_greedy_var_prefix_drops_overflow(self):
        # gaps [4, 4]; span 6 fits only the first two → prefix stops before #3.
        out = _greedy_strip_1d_var([0.0, 0.0, 0.0], [4.0, 4.0], 0.0, 6.0, prefix=True)
        assert out == [0.0, 4.0]


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
        # as _solve_strip_1d_var.
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


class TestFeasibleSegments:
    """P4c (#318, ADR 0009 Amendment 5): ``[lo, hi]`` minus the keep-out bands."""

    def test_no_bands_is_the_whole_strip(self):
        assert _feasible_segments(0.0, 100.0, []) == [(0.0, 100.0)]

    def test_single_interior_band_splits_in_two(self):
        assert _feasible_segments(0.0, 100.0, [(40.0, 60.0)]) == [(0.0, 40.0), (60.0, 100.0)]

    def test_edge_bands_clip_to_the_strip(self):
        assert _feasible_segments(0.0, 100.0, [(-10.0, 10.0), (90.0, 110.0)]) == [(10.0, 90.0)]

    def test_overlapping_bands_merge(self):
        assert _feasible_segments(0.0, 100.0, [(30.0, 50.0), (45.0, 70.0)]) == [
            (0.0, 30.0),
            (70.0, 100.0),
        ]

    def test_band_covering_everything_leaves_no_segment(self):
        assert _feasible_segments(40.0, 60.0, [(30.0, 70.0)]) == []


class TestSolveStrip1dPavaBanded:
    """P4c (#318, ADR 0009 Amendment 5): the min-leader PAVA solve made keep-out-
    band aware (retiring the ``_coaxial_lift`` pre-solve nudge). Avoidance is by
    construction where the strip has room, and degrades gracefully to minimal band
    intrusion (not a drop) where it does not."""

    def test_no_bands_is_byte_identical_to_the_plain_solve(self):
        for naturals, gaps, lo, hi in [
            ([10.0, 7.0, 4.0], [5.0, 5.0], -100.0, 100.0),
            ([-5.0, -4.0], [3.0], 0.0, 100.0),
            ([], [], 0.0, 10.0),
        ]:
            assert _solve_strip_1d_pava_banded(naturals, gaps, lo, hi, None, []) == (
                _solve_strip_1d_pava(naturals, gaps, lo, hi)
            )

    def test_label_on_a_band_is_pushed_to_the_nearer_edge(self):
        # natural 50 sits inside the band (45, 55); the room-available solve seats
        # it at the nearer segment edge (45), not on the row.
        assert _solve_strip_1d_pava_banded([50.0], [], 0.0, 100.0, None, [(45.0, 55.0)]) == (
            pytest.approx([45.0])
        )

    def test_two_labels_already_clear_are_untouched(self):
        # both naturals already sit outside a thin interior band → unchanged.
        assert _solve_strip_1d_pava_banded(
            [40.0, 60.0], [5.0], 0.0, 120.0, None, [(48.0, 52.0)]
        ) == (pytest.approx([40.0, 60.0]))

    def test_shallow_strip_degrades_to_minimal_intrusion_not_a_drop(self):
        # The band (81, 99) is WIDER than the whole strip [82, 98] (the dshape side
        # view): no band-clear position exists, so rather than drop a real callout
        # the solve clamps to the strip edge farthest from the row — 98, an 8 mm
        # residual, exactly what the old _coaxial_lift did. NOT None.
        assert _solve_strip_1d_pava_banded([90.0], [], 82.0, 98.0, None, [(81.0, 99.0)]) == (
            pytest.approx([98.0])
        )

    def test_cross_segment_gap_shifts_the_run_up_not_reject(self):
        # #379 review regression: when the next segment's run at its natural would
        # violate the cross-segment gap to the label already placed below the band,
        # the run must be SHIFTED UP (its lower bound tightened) to the min-cost
        # band-clear placement — not rejected outright (which parked the label on the
        # band edge at higher cost). naturals [29, 34], gap 10, band (30,33): the
        # optimum is [29, 39] (label 0 at its natural below, label 1 shifted up clear
        # of the band), cost 5 — NOT [20, 30] (cost 13, label 1 on the 30 edge).
        assert _solve_strip_1d_pava_banded(
            [29.0, 34.0], [10.0], 0.0, 100.0, None, [(30.0, 33.0)]
        ) == pytest.approx([29.0, 39.0])

    def test_genuine_over_capacity_still_returns_none(self):
        # Three labels needing 50 mm gaps can't fit an 80 mm strip regardless of
        # bands → None, preserving the caller's drop-and-retry contract.
        assert (
            _solve_strip_1d_pava_banded(
                [10.0, 20.0, 30.0], [50.0, 50.0], 0.0, 80.0, None, [(40.0, 45.0)]
            )
            is None
        )

    def test_deterministic_across_calls(self):
        args = ([40.0, 50.0, 60.0], [4.0, 4.0], 0.0, 120.0, None, [(48.0, 58.0)])
        assert _solve_strip_1d_pava_banded(*args) == _solve_strip_1d_pava_banded(*args)

    def test_snap_out_of_bands_prefers_the_roomier_half(self):
        # equidistant from both edges of a strip-engulfing band → toward the
        # roomier half (up), then clamped: matches _coaxial_lift's old direction.
        assert _snap_out_of_bands(90.0, [(81.0, 99.0)], 82.0, 98.0) == 98.0
        # natural clear of every band is returned unchanged.
        assert _snap_out_of_bands(30.0, [(45.0, 55.0)], 0.0, 100.0) == 30.0


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


def test_layout_engine_is_wired_into_the_drawing_path():
    # The hole-callout Y-stack flows through the shared layout engine. Phase 2 (#80)
    # wired it via the LayoutSolver; ADR 0009 P1a (#321) re-routed it through the
    # collect-then-solve seam — plan_strip over StripCandidates — so this guard now
    # tracks that seam (the first production placer on it).
    src = (L.__file__).replace("layout.py", "annotations/holes.py")
    text = open(src).read()
    assert "StripCandidate(" in text
    assert "plan_strip(" in text
