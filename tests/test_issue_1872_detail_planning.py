"""Render-free legibility policy shared by detail planning and drawing."""

import pytest

from draftwright._core import y_chain_detail_scale_needed
from draftwright.annotation_layout_profile import AnnotationLayoutProfile, use_layout_profile
from draftwright.annotations._common import DerivedViewReservation, strip_obstacles
from draftwright.annotations.leaders import _fixed_annotation_obstacles
from draftwright.annotations.sections import _reserved_detail_box_is_clear
from draftwright.compose import StripDepths, _compose_view_blocks, _layout_geometry, choose_scale


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
            derived_view_footprints=(("detail_a", 145.0, 100.0),),
        )

    assert ordinary.side_gutter == ordinary.vertical_gutter == 12.0
    assert with_detail.side_gutter == with_detail.vertical_gutter == 6.0
    assert with_detail.derived_views_fit and with_detail.fits
    box = with_detail.derived_view_boxes["detail_a"]
    assert box[2] - box[0] == pytest.approx(145.0)
    assert box[3] - box[1] == pytest.approx(100.0)


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


def test_pre_sheet_detail_demand_participates_in_sheet_selection():
    with use_layout_profile(AnnotationLayoutProfile(view_gutters=True)):
        ordinary = choose_scale(40, 20, 46, scale=1, include_iso=False)
        planned = choose_scale(
            40,
            20,
            46,
            scale=1,
            include_iso=False,
            derived_view_footprints_for_scale=lambda _scale: (("detail_a", 210.0, 44.0),),
        )
    assert ordinary[1:3] == (297.0, 210.0)
    assert planned[1:3] == (420.0, 297.0)


def test_detail_reservation_clears_full_planned_side_annotation_band():
    strips = StripDepths(right=10.0, left=10.0, sv_top=60.0)
    with use_layout_profile(AnnotationLayoutProfile(view_gutters=True)):
        layout = _layout_geometry(
            46,
            20,
            46,
            1,
            297,
            210,
            120,
            strips,
            include_iso=False,
            derived_view_footprints=(("detail_a", 108.16, 44.19),),
        )
    assert layout.derived_views_fit
    detail = layout.derived_view_boxes["detail_a"]
    side = _compose_view_blocks(46, 20, 46, 1, strips)["side"].footprint(layout.SV_X, layout.SV_Y)
    assert (
        detail[2] <= side[0]
        or side[2] <= detail[0]
        or detail[3] <= side[1]
        or side[3] <= detail[1]
    )


def test_required_detail_reservation_is_hard_for_strips_and_feature_leaders():
    class DrawingProbe:
        def __init__(self):
            self.reservation = DerivedViewReservation((10.0, 20.0, 40.0, 55.0))
            self.registry = self

        def iter_annotations(self):
            return iter((("detail_a_reservation", self.reservation),))

        def view_of(self, _name):
            return None

        def feature_of(self, _name):
            return None

    dwg = DrawingProbe()
    assert strip_obstacles(dwg, view="front", named=True) == [
        ("detail_a_reservation", (10.0, 20.0, 40.0, 55.0))
    ]
    fixed = tuple(_fixed_annotation_obstacles(dwg, "front"))
    assert len(fixed) == 1
    assert fixed[0].box == (10.0, 20.0, 40.0, 55.0)
    assert tuple(_fixed_annotation_obstacles(dwg, "front", provisional=True)) == ()


def test_required_detail_reservation_rejects_invalid_box():
    with pytest.raises(ValueError, match="finite nonempty"):
        DerivedViewReservation((10.0, 20.0, float("nan"), 55.0))


def test_reserved_detail_box_must_still_clear_landed_annotations():
    drawable = (0.0, 0.0, 100.0, 100.0)
    box = (10.0, 20.0, 40.0, 55.0)
    assert _reserved_detail_box_is_clear(drawable, ((40.0, 20.0, 50.0, 30.0),), box)
    assert not _reserved_detail_box_is_clear(drawable, ((39.9, 20.0, 50.0, 30.0),), box)
    assert not _reserved_detail_box_is_clear(drawable, (), (10.0, 20.0, 101.0, 55.0))
