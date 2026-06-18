"""Tests for draftwright.layout — the ADR 0003 phase-1 scaffolding (#79).

These exercise the constraint primitive and solver in isolation, with no drawing
build, which is the point of putting them in their own module.
"""

import draftwright.layout as L
from draftwright.layout import (
    LayoutSolver,
    Placeable,
    _greedy_strip_1d,
    _solve_strip_1d,
)


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

    def test_falls_back_to_greedy_without_kiwisolver(self, monkeypatch):
        # Simulate kiwisolver being unavailable; the import inside the primitive
        # then raises ImportError and the greedy cursor is used.
        monkeypatch.setitem(__import__("sys").modules, "kiwisolver", None)
        out = _solve_strip_1d([0, 0, 0], min_gap=5, lo=0, hi=100)
        assert out == _greedy_strip_1d([0, 0, 0], 5, 0, 100)
        assert out == [0, 5, 10]


class TestGreedyStrip1d:
    def test_overflow_returns_none_by_default(self):
        assert _greedy_strip_1d([0, 0, 0], min_gap=20, lo=0, hi=10) is None

    def test_prefix_mode_places_what_fits(self):
        # Only the first two fit in [0, 10] at gap 8 → prefix stops before #3.
        out = _greedy_strip_1d([0, 0, 0], min_gap=8, lo=0, hi=10, prefix=True)
        assert out == [0, 8]


class TestLayoutSolver:
    def _leader(self, key, natural, gap=5.0, axis="x"):
        return Placeable(
            key=key,
            anchors=((natural, 0.0),),
            size=(4.0, 2.0),
            dof_axis=axis,
            natural=natural,
            min_gap=gap,
        )

    def test_solve_strip_places_axis_members_keyed_by_key(self):
        s = LayoutSolver()
        s.register(self._leader("a", 0))
        s.register(self._leader("b", 1))
        s.register(self._leader("c", 2))
        out = s.solve_strip(lo=-50, hi=50, axis="x")
        assert set(out) == {"a", "b", "c"}
        xs = [out["a"], out["b"], out["c"]]
        assert xs[1] - xs[0] >= 5 - 1e-9 and xs[2] - xs[1] >= 5 - 1e-9

    def test_solve_strip_ignores_other_axis_and_pinned(self):
        s = LayoutSolver()
        s.register(self._leader("x1", 0, axis="x"))
        s.register(self._leader("y1", 0, axis="y"))
        s.register(Placeable("pin", ((0, 0),), (4, 2), dof_axis=None, natural=0, min_gap=5))
        out = s.solve_strip(lo=-50, hi=50, axis="x")
        assert set(out) == {"x1"}

    def test_solve_strip_no_members_returns_empty_dict(self):
        s = LayoutSolver()
        s.register(self._leader("y1", 0, axis="y"))
        assert s.solve_strip(lo=0, hi=10, axis="x") == {}

    def test_solve_strip_infeasible_returns_none(self):
        s = LayoutSolver()
        for i in range(5):
            s.register(self._leader(f"k{i}", 0, gap=20))
        assert s.solve_strip(lo=0, hi=10, axis="x") is None

    def test_registration_order_does_not_change_result(self):
        forward = LayoutSolver()
        for k, n in [("a", 0), ("b", 3), ("c", 6)]:
            forward.register(self._leader(k, n))
        shuffled = LayoutSolver()
        for k, n in [("c", 6), ("a", 0), ("b", 3)]:
            shuffled.register(self._leader(k, n))
        assert forward.solve_strip(lo=-50, hi=50, axis="x") == shuffled.solve_strip(
            lo=-50, hi=50, axis="x"
        )


def test_layout_is_not_wired_into_the_default_drawing_path():
    # Phase 1 is scaffolding: no annotation pass constructs a Placeable yet.
    src = (L.__file__).replace("layout.py", "make_drawing.py")
    text = open(src).read()
    assert "Placeable(" not in text
    assert "LayoutSolver(" not in text
