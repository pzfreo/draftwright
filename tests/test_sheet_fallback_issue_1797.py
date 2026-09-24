"""Regression coverage for bounded sheet-global annotation recovery (#1797)."""

from types import SimpleNamespace

import pytest
from build123d import Box, Draft
from build123d_drafting import Leader

from draftwright._geometry import _segments_cross_or_overlap
from draftwright.annotations.from_model import (
    _pmi_dim_spec,
    _pmi_leader_spec,
    _sheet_leader_fallback,
)
from draftwright.annotations.routed import RoutedLeader
from draftwright.linting.ink_overlap import segments_of


def test_diameter_dimension_may_fall_back_to_a_routed_surface_leader():
    strip = SimpleNamespace(anchor=20.0, direction=1, gap=2.0, outer_limit=80.0)
    spec = _pmi_dim_spec(
        (10.0, 12.0, 0.0),
        (30.0, 12.0, 0.0),
        strip,
        "ø40 ±0.1",
        "pmi_d_2",
        "front",
        "above",
        Draft(font_size=3.0),
        leader_fallback=True,
    )

    assert isinstance(spec["build_at"]((70.0, 70.0)), Leader)
    routed = spec["build_routed"](((40.0, 12.0), (40.0, 70.0)), (70.0, 70.0))
    assert isinstance(routed, Leader)
    assert isinstance(routed, RoutedLeader)
    assert len(routed.bends) == 2
    assert tuple(routed.tip)[:2] == (30.0, 12.0)


def test_linear_dimension_does_not_gain_a_leader_fallback():
    strip = SimpleNamespace(anchor=20.0, direction=1, gap=2.0, outer_limit=80.0)
    spec = _pmi_dim_spec(
        (10.0, 12.0, 0.0),
        (30.0, 12.0, 0.0),
        strip,
        "20 ±0.1",
        "pmi_x_0",
        "front",
        "above",
        Draft(font_size=3.0),
    )

    assert "build_at" not in spec
    assert "build_routed" not in spec


def test_existing_pmi_leader_supplies_global_and_routed_builders():
    strip = SimpleNamespace(anchor=20.0, direction=1, gap=2.0, outer_limit=80.0)
    spec = _pmi_leader_spec(
        (30.0, 12.0, 0.0),
        strip,
        "ø4 ±0.1",
        "pmi_d_3",
        "front",
        "above",
        Draft(font_size=3.0),
    )

    assert isinstance(spec["build_at"]((70.0, 70.0)), Leader)
    assert isinstance(spec["build_routed"](((40.0, 12.0),), (70.0, 70.0)), RoutedLeader)


def test_routed_leader_validates_routes_and_supports_all_over_symbol():
    draft = Draft(font_size=3.0)
    with pytest.raises(ValueError, match="either label or callout"):
        RoutedLeader((0, 0), ((5, 0),), (10, 0), "label", draft, callout=Box(1, 1, 1))
    with pytest.raises(ValueError, match="non-zero shaft"):
        RoutedLeader((0, 0), (), (10, 0), "label", draft)

    leader = RoutedLeader((0, 0), (5, 0), (10, 5), "label", draft, all_over=True)
    assert tuple(leader.bend)[:2] == (5.0, 0.0)


def test_collinear_routed_leader_keeps_standard_arrow_and_label_envelope():
    draft = Draft(font_size=3.0)
    standard = Leader((0, 0), (20, 0), "LABEL", draft)
    routed = RoutedLeader((0, 0), ((10, 0),), (20, 0), "LABEL", draft)

    before, after = standard.bounding_box(), routed.bounding_box()
    assert (after.min.X, after.min.Y, after.max.X, after.max.Y) == pytest.approx(
        (before.min.X, before.min.Y, before.max.X, before.max.Y)
    )
    assert routed.label_bbox == pytest.approx(standard.label_bbox)


def test_sheet_fallback_routes_around_a_settled_leader_shaft():
    draft = Draft(font_size=3.0)
    fixed = Leader((50.0, 10.0), (50.0, 90.0), "FIXED", draft)

    class DrawingStub:
        drawable_bounds = (0.0, 0.0, 100.0, 100.0)
        views = {"front": object()}

        def __init__(self):
            self.draft = draft

        def view_bounds(self, name):
            return (10.0, 10.0, 30.0, 30.0) if name == "front" else None

        def iter_annotations(self):
            return iter((("fixed", fixed),))

    candidate = _sheet_leader_fallback(
        DrawingStub(),
        (20.0, 20.0),
        "front",
        lambda elbow: Leader((20.0, 20.0), elbow, "NEW", draft),
        lambda bends, elbow: RoutedLeader((20.0, 20.0), bends, elbow, "NEW", draft),
    )

    assert candidate is not None
    assert not any(
        _segments_cross_or_overlap(a, b, c, d)
        for a, b in segments_of(candidate)
        for c, d in segments_of(fixed)
    )


def test_sheet_fallback_rejects_a_hidden_radial_prefix_crossing():
    draft = Draft(font_size=3.0)
    fixed = Leader((15.0, 10.0), (15.0, 30.0), "FIXED", draft)
    physical_tip = (10.0, 20.0)
    normal_stub = (20.0, 20.0)

    class DrawingStub:
        drawable_bounds = (0.0, 0.0, 100.0, 100.0)
        views = {"front": object()}

        def __init__(self):
            self.draft = draft

        def view_bounds(self, name):
            return (5.0, 5.0, 25.0, 35.0) if name == "front" else None

        def iter_annotations(self):
            return iter((("fixed", fixed),))

    candidate = _sheet_leader_fallback(
        DrawingStub(),
        normal_stub,
        "front",
        lambda elbow: RoutedLeader(physical_tip, (normal_stub,), elbow, "NEW", draft),
        lambda bends, elbow: RoutedLeader(
            physical_tip, (normal_stub, *bends), elbow, "NEW", draft
        ),
    )

    assert candidate is None


def test_sheet_fallback_rejects_a_hidden_prefix_through_another_view():
    draft = Draft(font_size=3.0)
    physical_tip = (10.0, 20.0)
    normal_stub = (20.0, 20.0)

    class DrawingStub:
        drawable_bounds = (0.0, 0.0, 100.0, 100.0)
        views = {"front": object(), "side": object()}

        def __init__(self):
            self.draft = draft

        def view_bounds(self, name):
            return {
                "front": (5.0, 5.0, 25.0, 35.0),
                "side": (14.0, 19.0, 16.0, 21.0),
            }[name]

        def iter_annotations(self):
            return iter(())

    candidate = _sheet_leader_fallback(
        DrawingStub(),
        normal_stub,
        "front",
        lambda elbow: RoutedLeader(physical_tip, (normal_stub,), elbow, "NEW", draft),
        lambda bends, elbow: RoutedLeader(
            physical_tip, (normal_stub, *bends), elbow, "NEW", draft
        ),
    )

    assert candidate is None
