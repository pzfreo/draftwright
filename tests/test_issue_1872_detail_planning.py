"""Render-free legibility policy shared by detail planning and drawing."""

import pytest

from draftwright._core import y_chain_detail_scale_needed


def test_y_chain_that_fits_on_parent_view_needs_no_detail():
    assert (
        y_chain_detail_scale_needed(
            ((0.0, 20.0, 20.0), (20.0, 40.0, 20.0)),
            (5.0, 5.0),
            arrow_length=2.0,
            text_padding=1.0,
        )
        is None
    )


def test_y_chain_plan_uses_shortest_approved_step_and_is_direction_independent():
    forward = ((0.0, 4.0, 4.0), (4.0, 10.0, 6.0))
    reverse = tuple((b, a, length) for a, b, length in forward)
    for segments in (forward, reverse):
        assert y_chain_detail_scale_needed(
            segments,
            (9.0, 9.0),
            arrow_length=2.0,
            text_padding=1.0,
        ) == pytest.approx(3.75)


def test_y_chain_planning_refuses_unmatched_text_inventory():
    with pytest.raises(ValueError, match="must correspond"):
        y_chain_detail_scale_needed(
            ((0.0, 4.0, 4.0),),
            (),
            arrow_length=2.0,
            text_padding=1.0,
        )
