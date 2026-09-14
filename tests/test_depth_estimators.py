"""Annotation corridor depth estimators."""

import pytest


class TestDepthEstimators:
    """Pure-function tests for _est_right_strip_depth / _est_pv_below_depth."""

    def test_right_depth_no_steps_equals_dim_pad(self):
        from draftwright._core import _DIM_PAD
        from draftwright.compose import _est_right_strip_depth

        # 0 steps → dim_height only → gap(10) + slot(10) = 20 = _DIM_PAD
        assert _est_right_strip_depth(0) == pytest.approx(_DIM_PAD, abs=0.01)

    def test_right_depth_one_step(self):
        from draftwright.compose import _est_right_strip_depth

        # gap(10) + dim_height(10) + spacing(2.5) + 1×dim_step(14) = 10 + 10 + 2.5 + 14 = 36.5
        assert _est_right_strip_depth(1) == pytest.approx(36.5, abs=0.01)

    def test_right_depth_three_steps(self):
        from draftwright.compose import _est_right_strip_depth

        # gap(10) + dim_height(10) + 3×dim_step(14) + 3×spacing(2.5) = 10+10+3×(2.5+14) = 69.5
        assert _est_right_strip_depth(3) == pytest.approx(69.5, abs=0.01)

    def test_right_depth_grows_per_step_uncapped(self):
        from draftwright._core import _SLOT_DIM_STEP, _STRIP_SPACING
        from draftwright.compose import _est_right_strip_depth

        # #36: no cap — each further step adds one slot + one spacing.
        assert _est_right_strip_depth(10) > _est_right_strip_depth(3)
        assert _est_right_strip_depth(10) - _est_right_strip_depth(3) == pytest.approx(
            7 * (_STRIP_SPACING + _SLOT_DIM_STEP), abs=0.01
        )

    def test_right_depth_increases_with_steps(self):
        from draftwright.compose import _est_right_strip_depth

        assert _est_right_strip_depth(0) < _est_right_strip_depth(1) < _est_right_strip_depth(3)

    def test_pv_below_depth(self):
        from draftwright.compose import _est_pv_below_depth

        # gap(10) + dim_width slot(8) = 18
        assert _est_pv_below_depth() == pytest.approx(18.0, abs=0.01)

    def test_right_depth_fits_in_exact_corridor(self):
        # _est_right_strip_depth(n) must reserve enough corridor for dim_height + n
        # dim_steps stacked from the view edge (gap, then `spacing` between dims) — the
        # cursor-free capacity condition the carve places into (ADR 2 (was 0009) / #150).
        from draftwright._core import (
            _SLOT_DIM_HEIGHT,
            _SLOT_DIM_STEP,
            _STRIP_GAP,
            _STRIP_SPACING,
        )
        from draftwright.compose import _est_right_strip_depth

        for n_steps in (0, 1, 3):
            est = _est_right_strip_depth(n_steps)
            sizes = [_SLOT_DIM_HEIGHT] + [_SLOT_DIM_STEP] * n_steps
            needed = _STRIP_GAP + sum(sizes) + _STRIP_SPACING * (len(sizes) - 1)
            assert needed <= est + 1e-9, f"n_steps={n_steps}: needs {needed} > est {est}"

    def test_pv_below_depth_fits_in_exact_corridor(self):
        # _est_pv_below_depth() must reserve enough for one dim_width from the view edge.
        from draftwright._core import _SLOT_DIM_WIDTH, _STRIP_GAP
        from draftwright.compose import _est_pv_below_depth

        assert _STRIP_GAP + _SLOT_DIM_WIDTH <= _est_pv_below_depth() + 1e-9

