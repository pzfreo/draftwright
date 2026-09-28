"""Focused geometry integration for planned derived-view occupancy."""

from build123d import Axis, Box, Cylinder, Pos

from draftwright import analysis as analysis_module
from draftwright import build_drawing
from draftwright.annotations._common import DerivedViewReservation


def _y_chain_part():
    return (Cylinder(15, 4) + Pos(0, 0, 5) * Cylinder(10, 6)).rotate(Axis.X, 90)


def test_unmatched_pre_sheet_reservation_is_removed_before_drawing_returns(monkeypatch):
    real_layout = analysis_module._layout_geometry

    def with_unmatched_detail(*args, **kwargs):
        result = real_layout(*args, **kwargs)
        result.derived_view_boxes = {"detail_a": (10.0, 10.0, 20.0, 20.0)}
        return result

    monkeypatch.setattr(analysis_module, "_layout_geometry", with_unmatched_detail)
    drawing = build_drawing(Box(40, 20, 10), _include_iso=False)
    assert "detail_a_layout_reservation" not in drawing.annotations()
    assert not any(isinstance(item, DerivedViewReservation) for item in drawing.items)


def test_approved_y_chain_uses_pre_sheet_reservation_and_places_both_steps():
    drawing = build_drawing(
        _y_chain_part(),
        scale=1.0,
        scale_policy="permissive",
        _include_iso=False,
    )
    assert drawing._analysis.derived_view_boxes
    assert drawing.detail_decisions[0]["status"] == "placed"
    assert drawing.detail_decisions[0]["fit"]["within_reservation"] is True
    labels = {
        str(annotation.label)
        for name, annotation in drawing.iter_annotations()
        if name.startswith("dim_detail_a_steplen")
    }
    assert {"4", "6"} <= labels
    assert not any(isinstance(item, DerivedViewReservation) for item in drawing.items)


def test_detail_opt_out_does_not_reserve_a_view_that_cannot_render():
    drawing = build_drawing(
        _y_chain_part(),
        scale=1.0,
        scale_policy="permissive",
        detail_view=False,
        _include_iso=False,
    )
    assert drawing._analysis.derived_view_boxes == ()
    assert not any(isinstance(item, DerivedViewReservation) for item in drawing.items)
