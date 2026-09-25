"""A detail may cap isometric growth, but must not freeze its temporary seed."""

from types import SimpleNamespace

import pytest

from draftwright import builder


def test_planned_iso_grows_toward_sheet_scale_with_detail_as_obstacle(monkeypatch):
    scale = 0.65
    seen = {}

    def iso_bbox(_drawing):
        return (-10 * scale, -10 * scale, 10 * scale, 10 * scale)

    def clear_factor(_drawing, _analysis, ceiling, obstacles, _box, *, lo, region):
        seen.update(ceiling=ceiling, obstacles=obstacles, lo=lo, region=region)
        return ceiling

    def project(_drawing, _analysis, new_scale):
        nonlocal scale
        scale = new_scale

    drawing = SimpleNamespace(views={"iso": object(), "detail_a": object()})
    drawing.view_bounds = lambda name: (12.0, 12.0, 16.0, 16.0)
    analysis = SimpleNamespace(
        planned_iso_scale=0.65,
        planned_iso_scale_authored=False,
        SCALE=1.0,
        ISO_X=0.0,
        ISO_Y=0.0,
        iso_left_limit=-20.0,
        iso_bottom_limit=-20.0,
        iso_right_limit=20.0,
        iso_top_limit=20.0,
    )
    monkeypatch.setattr(builder, "_iso_bbox", iso_bbox)
    monkeypatch.setattr(builder, "_largest_clear_factor", clear_factor)
    monkeypatch.setattr(builder, "_project_iso", project)

    assert builder._settle_iso_view(drawing, analysis, obstacles=((11, 11, 12, 12),)) is None
    assert scale == pytest.approx(1.0)
    assert seen["ceiling"] == pytest.approx(1.0)
    assert seen["lo"] == pytest.approx(0.65)
    assert seen["region"] == (-20.0, -20.0, 20.0, 20.0)
    assert seen["obstacles"] == [
        (6.0, 6.0, 17.0, 17.0),  # annotation ink, with 5 mm clearance
        (7.0, 7.0, 21.0, 21.0),  # detail view, with the same clearance
    ]
