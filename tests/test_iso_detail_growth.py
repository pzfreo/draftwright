"""A detail may cap isometric growth, but must not freeze its temporary seed."""

from types import SimpleNamespace

import pytest

from draftwright import builder
from draftwright._geometry import _boxes_overlap
from draftwright.projection import _clear_iso_translation


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
    monkeypatch.setattr(builder, "_clear_iso_translation", lambda *_args: None)
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


def test_measured_iso_footprint_moves_to_nearest_free_space():
    box = (2.0, 2.0, 22.0, 22.0)
    region = (0.0, 0.0, 50.0, 50.0)

    assert _clear_iso_translation(box, region, [(0.0, 0.0, 50.0, 25.0)]) == (0.0, 23.0)
    assert _clear_iso_translation(box, region, [region]) is None
    assert _clear_iso_translation((0.0, 0.0, 60.0, 20.0), region, ()) is None
    assert _clear_iso_translation((0.0, 0.0, 0.0, 20.0), region, ()) is None


def test_detail_constrained_iso_relocates_at_sheet_scale(monkeypatch):
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
    scale = analysis.planned_iso_scale
    drawing.centre = (0.0, 0.0)

    def project(_drawing, selected, new_scale):
        nonlocal scale
        scale = new_scale
        drawing.centre = (selected.ISO_X, selected.ISO_Y)

    def iso_bbox(_drawing):
        x, y = drawing.centre
        return (x - 10 * scale, y - 10 * scale, x + 10 * scale, y + 10 * scale)

    monkeypatch.setattr(builder, "_project_iso", project)
    monkeypatch.setattr(builder, "_iso_bbox", iso_bbox)
    monkeypatch.setattr(
        builder,
        "replace",
        lambda original, **changes: SimpleNamespace(**{**vars(original), **changes}),
    )

    assert builder._settle_iso_view(drawing, analysis, obstacles=((11, 11, 12, 12),)) is None
    assert scale == pytest.approx(1.0)
    assert drawing.centre != (analysis.ISO_X, analysis.ISO_Y)
    assert not _boxes_overlap(iso_bbox(drawing), (7.0, 7.0, 21.0, 21.0))
