"""Focused geometry integration for planned derived-view occupancy."""

from build123d import Box

from draftwright import analysis as analysis_module
from draftwright import build_drawing
from draftwright.annotations._common import DerivedViewReservation


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
