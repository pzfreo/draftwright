"""Render-free legibility policy shared by detail planning and drawing."""

import pytest

from draftwright._core import y_chain_detail_scale_needed
from draftwright.annotation_layout_profile import AnnotationLayoutProfile, use_layout_profile
from draftwright.compose import _layout_geometry


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


def test_required_detail_spends_preferred_gutter_but_preserves_minimum():
    args = (40, 20, 46, 1, 297, 210, 120, None)
    with use_layout_profile(AnnotationLayoutProfile(view_gutters=True)):
        ordinary = _layout_geometry(*args, include_iso=False)
        with_detail = _layout_geometry(
            *args,
            include_iso=False,
            derived_view_footprints=(("detail_a", 145.0, 80.0),),
        )

    assert ordinary.side_gutter == ordinary.vertical_gutter == 12.0
    assert with_detail.side_gutter == with_detail.vertical_gutter == 6.0
    assert with_detail.derived_views_fit and with_detail.fits
    box = with_detail.derived_view_boxes["detail_a"]
    assert box[2] - box[0] == pytest.approx(145.0)
    assert box[3] - box[1] == pytest.approx(80.0)


def test_impossible_derived_view_fails_fit_instead_of_stealing_minimum_gutter():
    with use_layout_profile(AnnotationLayoutProfile(view_gutters=True)):
        layout = _layout_geometry(
            40,
            20,
            46,
            1,
            297,
            210,
            120,
            None,
            include_iso=False,
            derived_view_footprints=(("detail_a", 195.0, 44.0),),
        )
    assert layout.side_gutter == layout.vertical_gutter == 6.0
    assert not layout.derived_views_fit
    assert not layout.fits
