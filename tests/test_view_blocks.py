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
        assert blocks["front"].top == pytest.approx(_est_pv_below_depth())
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
        block_stack_h = (
            fv.bottom + 2 * fv.hh + fv.top + g.vertical_gutter + pv.bottom + 2 * pv.hh + pv.top
        )
        expected_y_offset = max(0.0, (page_h - 2 * _MARGIN - block_stack_h) / 2)
        actual_y_offset = g.FV_Y - _MARGIN - fv.bottom - fv.hh

        assert actual_y_offset == pytest.approx(expected_y_offset)

    def test_estimator_vertical_stack_centres_ballooned_plan_block(self):
        from draftwright._core import _MARGIN
        from draftwright.compose import (
            StripDepths,
            _compose_view_blocks,
            _layout_geometry,
        )

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
        block_stack_h = (
            fv.bottom + 2 * fv.hh + fv.top + g.vertical_gutter + pv.bottom + 2 * pv.hh + pv.top
        )
        expected_y_offset = max(0.0, (page_h - 2 * _MARGIN - block_stack_h) / 2)
        actual_y_offset = g.FV_Y - _MARGIN - fv.bottom - fv.hh

        assert pv.bottom > 20.0
        assert actual_y_offset == pytest.approx(expected_y_offset)


@pytest.mark.parametrize("convention", ["first", "third"])
def test_principal_views_keep_blank_gutters_outside_measured_annotation_bands(convention):
    from draftwright.compose import (
        _VIEW_GUTTER,
        _VIEW_GUTTER_PREFERRED,
        ViewBlock,
        _build_zones,
        _layout_geometry,
    )

    blocks = {
        "front": ViewBlock(25, 6, top=21, right=24, bottom=27, left=30),
        "plan": ViewBlock(25, 17.5, top=33, right=36, bottom=39, left=42),
        "side": ViewBlock(17.5, 6, top=45, right=48, bottom=51, left=54),
    }
    g = _layout_geometry(
        50,
        35,
        12,
        1,
        594,
        420,
        150,
        None,
        blocks=blocks,
        convention=convention,
    )
    assert g.fits
    assert _VIEW_GUTTER <= g.vertical_gutter <= _VIEW_GUTTER_PREFERRED
    assert _VIEW_GUTTER <= g.side_gutter <= _VIEW_GUTTER_PREFERRED
    front = blocks["front"].footprint(g.FV_X, g.FV_Y)
    plan = blocks["plan"].footprint(g.PV_X, g.PV_Y)
    side = blocks["side"].footprint(g.SV_X, g.SV_Y)
    fv_zones, pv_zones, _ = _build_zones(g, 10.0, 420.0)

    if convention == "third":
        assert plan[1] - front[3] == pytest.approx(g.vertical_gutter)
        assert side[0] - max(front[2], plan[2]) == pytest.approx(g.side_gutter)
        assert fv_zones.above.outer_limit == pytest.approx(front[3])
        assert pv_zones.below.outer_limit == pytest.approx(plan[1])
        assert fv_zones.right.outer_limit == pytest.approx(max(front[2], plan[2]))
        assert pv_zones.right.outer_limit == pytest.approx(max(front[2], plan[2]))
    else:
        assert front[1] - plan[3] == pytest.approx(g.vertical_gutter)
        assert min(front[0], plan[0]) - side[2] == pytest.approx(g.side_gutter)
        assert fv_zones.below.outer_limit == pytest.approx(front[1])
        assert pv_zones.above.outer_limit == pytest.approx(plan[3])


def test_optional_gutter_uses_slack_without_losing_title_block_clearance():
    from draftwright.compose import _VIEW_GUTTER, _VIEW_GUTTER_PREFERRED, _layout_geometry

    small = _layout_geometry(90, 45, 20, 1, 297, 210, 120, None, 0, arrangement="stacked-iso")
    roomy = _layout_geometry(90, 60, 20, 1, 594, 420, 150, None, 0, arrangement="stacked-iso")

    assert small.auto_fits
    assert small.auto_clears_tb
    assert _VIEW_GUTTER <= small.vertical_gutter < _VIEW_GUTTER_PREFERRED
    assert roomy.vertical_gutter == pytest.approx(_VIEW_GUTTER_PREFERRED)
    assert roomy.side_gutter == pytest.approx(_VIEW_GUTTER_PREFERRED)
