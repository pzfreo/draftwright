"""Render-free legibility policy shared by detail planning and drawing."""

from types import SimpleNamespace

import pytest

from draftwright import analysis as analysis_module
from draftwright._core import crowded_horizontal_step_runs, y_chain_detail_scale_needed
from draftwright.annotation_layout_profile import AnnotationLayoutProfile, use_layout_profile
from draftwright.annotations._common import (
    DerivedViewReservation,
    _clear_derived_view_reservation,
    strip_obstacles,
)
from draftwright.annotations.leaders import _fixed_annotation_obstacles
from draftwright.annotations.sections import (
    _detail_ink_within_reservation,
    _reserved_detail_box_is_clear,
)
from draftwright.compose import StripDepths, _compose_view_blocks, _layout_geometry, choose_scale
from draftwright.layout_selection import lost_required_derived_view_reservations


def _approved_y_chain(lengths, *, gap=0.0):
    groups = []
    station = 0.0
    for length in lengths:
        measurement = SimpleNamespace(
            span=((0.0, station, 0.0), (0.0, station + length, 0.0)),
            value=float(length),
            value_text=str(length),
            tolerance=None,
            display_decimals=None,
        )
        group = SimpleNamespace(
            facts=SimpleNamespace(
                frame=SimpleNamespace(axis="y", origin=(0.0, 0.0, 0.0)),
                profile=None,
                profile_group=None,
            ),
            dim=lambda *, kind, item=measurement: item if kind == "length" else None,
        )
        groups.append(group)
        station += length + gap
    return SimpleNamespace(of_kind=lambda kind: tuple(groups) if kind == "step" else ())


def _approved_x_head(*, displayed_diameter_offset=0.0):
    rows = []
    station = -3.2
    for length, diameter in ((3.2, 4), (0.5, 6), (2, 10), (3, 5), (20, 3)):
        lo, hi = station, station + length
        rows.append(
            SimpleNamespace(
                facts=SimpleNamespace(
                    frame=SimpleNamespace(axis="x", origin=((lo + hi) / 2, 0, 0)),
                    profile="one physical profile",
                    profile_group=None,
                    diameter=float(diameter),
                ),
                dim=lambda *, kind, span=((lo, 0, 0), (hi, 0, 0)), size=length, dia=(diameter + displayed_diameter_offset): (
                    SimpleNamespace(value=size, span=span)
                    if kind == "length"
                    else SimpleNamespace(value=dia)
                    if kind == "diameter"
                    else None
                ),
            )
        )
        station = hi
    return SimpleNamespace(of_kind=lambda kind: tuple(rows) if kind == "step" else ())


def test_x_crowded_run_and_partial_head_demand_share_renderer_rule(monkeypatch):
    monkeypatch.setattr(analysis_module, "_text_size", lambda text, *_a, **_k: (len(text), 3))
    assert crowded_horizontal_step_runs(
        ((-3.2, 0), (0, 0.5), (0.5, 2.5), (2.5, 5.5), (5.5, 25.5)),
        1.0,
        2.7,
    ) == ((0, 1, 2, 3),)
    bb = SimpleNamespace(min=SimpleNamespace(Z=-5.0), max=SimpleNamespace(Z=5.0))
    draft = SimpleNamespace(font_size=3.0, arrow_length=2.7, pad_around_text=2.0)
    footprints = analysis_module._automatic_x_head_detail_footprints(
        _approved_x_head(), bb, draft, section_count=0, planned_views=None
    )
    assert footprints is not None
    name, width, height = footprints(1.0)[0]
    assert name == "detail_a"
    assert width == pytest.approx(87.0)
    assert 55.0 < height < 70.0
    assert footprints(5.0) == ()  # only the isolated 0.5 mm step remains sub-floor


def test_x_head_crop_uses_physical_profile_not_displayed_diameter(monkeypatch):
    monkeypatch.setattr(analysis_module, "_text_size", lambda text, *_a, **_k: (len(text), 3))
    bb = SimpleNamespace(min=SimpleNamespace(Z=-5.0), max=SimpleNamespace(Z=5.0))
    draft = SimpleNamespace(font_size=3.0, arrow_length=2.7, pad_around_text=2.0)
    ordinary = analysis_module._automatic_x_head_detail_footprints(
        _approved_x_head(), bb, draft, section_count=0, planned_views=None
    )
    different_nominal = analysis_module._automatic_x_head_detail_footprints(
        _approved_x_head(displayed_diameter_offset=1.0),
        bb,
        draft,
        section_count=0,
        planned_views=None,
    )
    assert ordinary is not None and different_nominal is not None
    assert ordinary(1.0) == different_nominal(1.0)


def test_approved_y_chain_produces_scale_dependent_detail_demand(monkeypatch):
    measured = []

    def measure(text, *_args, **_kwargs):
        measured.append(text)
        return float(len(text)), 3.0

    monkeypatch.setattr(
        analysis_module,
        "_text_size",
        measure,
    )
    bb = SimpleNamespace(min=SimpleNamespace(Z=-21.0), max=SimpleNamespace(Z=21.0))
    draft = SimpleNamespace(font_size=3.0, arrow_length=2.0, pad_around_text=1.0)
    footprints = analysis_module._automatic_y_chain_detail_footprints(
        _approved_y_chain((4, 6)),
        bb,
        draft,
        section_count=0,
        planned_views=None,
    )
    assert footprints is not None
    name, width, height = footprints(1.0)[0]
    assert name == "detail_a" and width > 10.0 and height > 20.0
    assert footprints(5.0) == ()  # both lengths are legible on the parent view
    assert len(measured) == 3  # two step labels + one caption, not one per scale probe


def test_profile_recomposition_may_move_but_not_lose_required_detail_space():
    original = SimpleNamespace(derived_view_boxes=(("detail_a", (10.0, 20.0, 50.0, 55.0)),))
    moved = SimpleNamespace(derived_view_boxes=(("detail_a", (60.0, 20.0, 100.0, 55.0)),))
    shrunk = SimpleNamespace(derived_view_boxes=(("detail_a", (60.0, 20.0, 99.0, 55.0)),))
    missing = SimpleNamespace(derived_view_boxes=())
    assert lost_required_derived_view_reservations(original, moved) == ()
    assert lost_required_derived_view_reservations(original, shrunk) == ("detail_a",)
    assert lost_required_derived_view_reservations(original, missing) == ("detail_a",)


def test_repeated_y_pitch_needs_no_planned_detail(monkeypatch):
    monkeypatch.setattr(
        analysis_module,
        "_text_size",
        lambda text, *_args, **_kwargs: (float(len(text)), 3.0),
    )
    bb = SimpleNamespace(min=SimpleNamespace(Z=-21.0), max=SimpleNamespace(Z=21.0))
    draft = SimpleNamespace(font_size=3.0, arrow_length=2.0, pad_around_text=1.0)
    assert (
        analysis_module._automatic_y_chain_detail_footprints(
            _approved_y_chain((4, 4, 4)),
            bb,
            draft,
            section_count=0,
            planned_views=None,
        )
        is None
    )
    assert (
        analysis_module._automatic_y_chain_detail_footprints(
            _approved_y_chain((4, 6), gap=1.0),
            bb,
            draft,
            section_count=0,
            planned_views=None,
        )
        is None
    )


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


def test_missing_required_detail_reservation_cannot_become_unreserved_placement():
    drawing = SimpleNamespace(annotations=lambda: {})
    assert _clear_derived_view_reservation(drawing, "detail_a_layout_reservation") is None
    with pytest.raises(KeyError, match="required derived-view reservation"):
        _clear_derived_view_reservation(drawing, "detail_a_layout_reservation", required=True)


def test_reserved_detail_box_must_still_clear_landed_annotations():
    drawable = (0.0, 0.0, 100.0, 100.0)
    box = (10.0, 20.0, 40.0, 55.0)
    assert _reserved_detail_box_is_clear(drawable, ((40.0, 20.0, 50.0, 30.0),), box)
    assert not _reserved_detail_box_is_clear(drawable, ((39.9, 20.0, 50.0, 30.0),), box)
    assert not _reserved_detail_box_is_clear(drawable, (), (10.0, 20.0, 101.0, 55.0))


def test_measured_detail_ink_must_fit_reserved_box():
    outer = (10.0, 20.0, 40.0, 55.0)
    fitting = DerivedViewReservation((11.0, 21.0, 39.0, 54.0))
    escaping = DerivedViewReservation((11.0, 21.0, 41.0, 54.0))
    assert _detail_ink_within_reservation(outer, (fitting,)) == (
        True,
        fitting.box,
    )
    assert _detail_ink_within_reservation(outer, (fitting, escaping)) == (
        False,
        escaping.box,
    )
