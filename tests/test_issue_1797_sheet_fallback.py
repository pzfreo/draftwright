"""Regression coverage for bounded sheet-global annotation recovery (#1797)."""

from types import SimpleNamespace

import pytest
from build123d import Box, Draft
from build123d_drafting import Leader

from draftwright.annotations.from_model import _pmi_dim_spec, _pmi_leader_spec
from draftwright.annotations.routed import RoutedLeader


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
