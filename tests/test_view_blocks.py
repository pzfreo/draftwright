"""Composed orthographic view-block geometry behavior."""

import pytest


class TestComposeViewBlocks:
    """#112: estimated view footprints are explicit ViewBlocks."""

    def test_fallback_blocks_without_measured_strips(self):
        from draftwright._core import _DIM_PAD
        from draftwright.compose import (
            _compose_view_blocks,
            _est_pv_below_depth,
            _est_right_strip_depth,
        )

        blocks = _compose_view_blocks(60.0, 40.0, 20.0, 2.0, None, n_steps=3)
        assert set(blocks) == {"front", "plan", "side", "rear"}
        assert blocks["front"].hw == pytest.approx(60.0)
        assert blocks["front"].hh == pytest.approx(20.0)
        assert blocks["front"].right == pytest.approx(_est_right_strip_depth(3))
        assert blocks["front"].left == pytest.approx(_DIM_PAD)
        assert blocks["front"].top == pytest.approx(_DIM_PAD - _est_pv_below_depth())
        assert blocks["plan"].bottom == pytest.approx(_est_pv_below_depth())
        assert blocks["side"].right == pytest.approx(_DIM_PAD)

    def test_ballooned_plan_halo_is_part_of_plan_block(self):
        from draftwright._core import _DIM_PAD
        from draftwright.compose import StripDepths, _compose_view_blocks, _est_pv_below_depth

        strips = StripDepths(right=10.0, left=12.0, top=20.0, pv_halo=30.0)
        blocks = _compose_view_blocks(60.0, 40.0, 20.0, 1.0, strips)

        # The shared column corridors must hold the plan halo, even though the
        # scalar right/left strip estimates are smaller.
        assert blocks["front"].right == pytest.approx(30.0)
        assert blocks["front"].left == pytest.approx(30.0)
        assert blocks["plan"].right == pytest.approx(30.0)
        assert blocks["plan"].left == pytest.approx(30.0)
        assert blocks["plan"].top == pytest.approx(max(_DIM_PAD, strips.top) + strips.pv_halo)
        assert blocks["plan"].bottom == pytest.approx(max(_est_pv_below_depth(), strips.pv_halo))

    def test_section_layout_reserves_side_right_band(self):
        from draftwright._core import _DIM_PAD
        from draftwright.compose import StripDepths, _compose_view_blocks

        strips = StripDepths(right=42.0, left=10.0)
        without_section = _compose_view_blocks(60.0, 40.0, 20.0, 1.0, strips, section=False)
        with_section = _compose_view_blocks(60.0, 40.0, 20.0, 1.0, strips, section=True)

        assert without_section["side"].right == pytest.approx(_DIM_PAD)
        assert with_section["side"].right == pytest.approx(strips.right)

    def test_layout_geometry_consumes_composed_blocks(self, monkeypatch):
        import draftwright.compose as compose

        calls = []
        original = compose._compose_view_blocks

        def wrapped(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(compose, "_compose_view_blocks", wrapped)
        strips = compose.StripDepths(right=42.0, left=10.0)
        g = compose._layout_geometry(
            60.0,
            40.0,
            20.0,
            1.0,
            420.0,
            297.0,
            150.0,
            strips,
            n_steps=2,
            section=True,
        )

        assert g.fits
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args[:6] == (60.0, 40.0, 20.0, 1.0, strips, 2)
        assert kwargs == {"section": True}

    def test_estimator_vertical_stack_is_centred_from_composed_blocks(self):
        from draftwright._core import _MARGIN
        from draftwright.compose import _compose_view_blocks, _layout_geometry

        page_h = 297.0
        blocks = _compose_view_blocks(60.0, 40.0, 20.0, 1.0, None, n_steps=3)
        g = _layout_geometry(
            60.0,
            40.0,
            20.0,
            1.0,
            420.0,
            page_h,
            150.0,
            None,
            n_steps=3,
            warn_no_iso=False,
        )

        fv, pv = blocks["front"], blocks["plan"]
        block_stack_h = fv.bottom + 2 * fv.hh + fv.top + pv.bottom + 2 * pv.hh + pv.top
        expected_y_offset = max(0.0, (page_h - 2 * _MARGIN - block_stack_h) / 2)
        actual_y_offset = g.FV_Y - _MARGIN - fv.bottom - fv.hh

        assert actual_y_offset == pytest.approx(expected_y_offset)

    def test_estimator_vertical_stack_centres_ballooned_plan_block(self):
        from draftwright._core import _MARGIN
        from draftwright.compose import StripDepths, _compose_view_blocks, _layout_geometry

        page_h = 297.0
        strips = StripDepths(right=10.0, left=12.0, top=20.0, pv_halo=30.0)
        blocks = _compose_view_blocks(60.0, 40.0, 20.0, 1.0, strips, n_steps=3)
        g = _layout_geometry(
            60.0,
            40.0,
            20.0,
            1.0,
            420.0,
            page_h,
            150.0,
            strips,
            n_steps=3,
            warn_no_iso=False,
        )

        fv, pv = blocks["front"], blocks["plan"]
        block_stack_h = fv.bottom + 2 * fv.hh + fv.top + pv.bottom + 2 * pv.hh + pv.top
        expected_y_offset = max(0.0, (page_h - 2 * _MARGIN - block_stack_h) / 2)
        actual_y_offset = g.FV_Y - _MARGIN - fv.bottom - fv.hh

        assert pv.bottom > 20.0
        assert actual_y_offset == pytest.approx(expected_y_offset)
