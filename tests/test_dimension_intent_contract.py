"""Authored dimension hints only select renderer-supported principal corridors."""

from types import SimpleNamespace

import pytest

from draftwright.model import ir

_OBLIQUE_XY = ((0.0, 0.0, 0.0), (3.0, 4.0, 0.0))
_ANGULAR_Z = SimpleNamespace(principal_axis="Z", rays=((1, 0, 0), (0, 1, 0)))
_PROFILE_AXIS = (SimpleNamespace(axis_direction=(0.70710678, 0.70710678, 0.0)),)


@pytest.mark.parametrize("view,side", [("iso", None), (None, "middle")])
def test_dimension_placement_rejects_unknown_view_or_strip(view, side):
    with pytest.raises(ValueError, match="test dimension (view|side) must be one of"):
        ir.validate_placement_intent(view, side, owner="test dimension")


@pytest.mark.parametrize(
    "references, expected",
    [
        ((), None),
        (((0, 0, 0), (0, 0, 0)), None),
        (((0, 0, 0), (1, 2, 3)), None),
        (((0, 0, 0), (3, 4, 0)), "plan"),
        (((0, 0, 0), (3, 0, 4)), "front"),
        (((0, 0, 0), (0, 3, 4)), "side"),
    ],
)
def test_oblique_linear_span_needs_one_lossless_principal_projection(references, expected):
    assert ir._linear_projection_view(references) == expected


@pytest.mark.parametrize(
    "kind,axis,view,side,extras",
    [
        ("linear", "X", "front", "above", {}),
        ("linear", "Y", "plan", "right", {}),
        ("linear", "Z", "front", "left", {}),
        ("linear", "?", "plan", "left", {"ref_pts": _OBLIQUE_XY}),
        ("diameter", "X", "side", "below", {}),
        ("diameter", "?", "plan", "right", {"cylindrical_refs": _PROFILE_AXIS}),
        ("radius", "Y", "front", "above", {}),
        ("angular", "Z", "plan", "right", {"angular_reference": _ANGULAR_Z}),
    ],
)
def test_authored_hints_accept_supported_corridors(kind, axis, view, side, extras):
    ir.validate_authored_dimension_placement(kind, axis, view, side, owner="test", **extras)


@pytest.mark.parametrize(
    "kind,axis,view,side,extras",
    [
        ("linear", "X", "side", "above", {}),
        ("linear", "?", "front", "above", {"ref_pts": _OBLIQUE_XY}),
        ("linear", "?", "plan", "above", {"ref_pts": ((0, 0, 0), (1, 2, 3))}),
        ("diameter", "?", "side", "above", {}),
        ("diameter", "?", "plan", "above", {"cylindrical_refs": _PROFILE_AXIS}),
        ("angular", "Z", "front", "right", {"angular_reference": _ANGULAR_Z}),
    ],
)
def test_authored_hints_reject_corridors_without_a_renderer(kind, axis, view, side, extras):
    with pytest.raises(ValueError, match="supported placement"):
        ir.validate_authored_dimension_placement(kind, axis, view, side, owner="test", **extras)


@pytest.mark.parametrize(
    "kind,axis,view,side,angular,refs,expected",
    [
        ("linear", "X", "front", "above", None, (), "front"),
        ("angular", "Z", None, "right", _ANGULAR_Z, (), "plan"),
        ("linear", "?", None, "left", None, _OBLIQUE_XY, "plan"),
        ("linear", "X", None, None, None, (), None),
        ("diameter", "X", None, "above", None, (), "side"),
        ("linear", "Z", None, "right", None, (), "front"),
        ("linear", "Y", None, "above", None, (), "side"),
        ("linear", "Y", None, "right", None, (), "plan"),
        ("linear", "?", None, "above", None, (), None),
    ],
)
def test_authored_hint_resolves_only_the_selected_view(
    kind, axis, view, side, angular, refs, expected
):
    assert ir.authored_dimension_target_view(kind, axis, view, side, angular, refs) == expected
