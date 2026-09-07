"""Angular intent must retain oriented rays before it can earn rendered ink (#1504)."""

from dataclasses import replace
from math import cos, radians, sin

import pytest
from build123d import Box

from draftwright import Sheet
from draftwright.model import AngularReference, measured_dimension
from draftwright.sheet_emit import _measured_dimension_line


@pytest.mark.parametrize("angle", (12.850620352742, 30, 60, 90, 120, 179))
@pytest.mark.parametrize("length", (0.001, 1, 1000))
def test_reference_measures_the_selected_rays_in_degrees(angle, length):
    reference = AngularReference(
        vertex=(4, -3, 2),
        first=(4 + length, -3, 2),
        second=(4 + length * cos(radians(angle)), -3 + length * sin(radians(angle)), 2),
    )
    assert reference.angle_degrees == pytest.approx(angle, abs=1e-8)
    assert reference.principal_axis == "Z"
    reversed_rays = replace(reference, first=reference.second, second=reference.first)
    assert reversed_rays.angle_degrees == pytest.approx(angle, abs=1e-8)
    assert reversed_rays.normal == pytest.approx(tuple(-v for v in reference.normal))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"first": (0, 0, 0)}, "nonzero rays"),
        ({"second": (2, 0, 0)}, "parallel or collinear"),
        ({"second": (-2, 0, 0)}, "parallel or collinear"),
        ({"vertex": (float("nan"), 0, 0)}, "finite 3-vector"),
        ({"second": (0, float("inf"), 0)}, "finite 3-vector"),
        ({"first": (1, 0)}, "finite 3-vector"),
        ({"sector": "major"}, "non-reflex"),
        ({"sector": "supplementary"}, "non-reflex"),
        ({"virtual_vertex": "yes"}, "must be a bool"),
    ],
)
def test_ambiguous_or_degenerate_references_are_refused(changes, message):
    valid = dict(vertex=(0, 0, 0), first=(1, 0, 0), second=(0, 1, 0))
    assert AngularReference(**valid).angle_degrees == 90
    with pytest.raises(ValueError, match=message):
        AngularReference(**(valid | changes))


def test_oblique_plane_is_not_misidentified_as_a_principal_projection():
    reference = AngularReference((0, 0, 0), (1, 1, 0), (0, 0, 1))
    assert reference.angle_degrees == 90
    assert reference.principal_axis == "?"


def test_explicit_reference_survives_executed_sheet_emission():
    # Short rays would lose their angle if the emitter rounded these points to 3dp.
    reference = AngularReference(
        vertex=(0, 0, 0),
        first=(0.000123456789, 0, 0),
        second=(0.0000617283945, 0.000106916715543, 0),
        virtual_vertex=True,
    )
    original = measured_dimension(
        kind="angular",
        value=60,
        label="60°",
        dominant_axis="z",
        ref_pts=(),
        angular_reference=reference,
        upper_tol=0.000123456789,
        lower_tol=-0.000234567891,
    )
    line = _measured_dimension_line(original)
    sheet = Sheet(Box(10, 10, 2))
    sheet.authored_dimensions()
    namespace = {"sheet": sheet}
    exec("handle = " + line, namespace)
    replayed = sheet.model().features[0]
    assert replayed.angular_reference == reference
    assert replayed.ref_pts == (reference.first, reference.vertex, reference.second)
    assert replayed.upper_tol == original.upper_tol and replayed.lower_tol == original.lower_tol
    assert replayed.angular_reference.angle_degrees == reference.angle_degrees


def test_reference_cannot_disagree_with_generic_stations_or_dimension_kind():
    reference = AngularReference((0, 0, 0), (1, 0, 0), (0, 1, 0))
    arguments = dict(
        kind="angular",
        value=90,
        label="90°",
        dominant_axis="z",
        ref_pts=(),
        angular_reference=reference,
    )
    valid = measured_dimension(**arguments)
    assert valid.angular_reference == reference
    with pytest.raises(ValueError, match="ref_pts must agree"):
        measured_dimension(**(arguments | {"ref_pts": ((0, 0, 0), (3, 0, 0))}))
    with pytest.raises(ValueError, match="requires an angular dimension"):
        measured_dimension(**(arguments | {"kind": "linear"}))
    with pytest.raises(ValueError, match="requires an angular dimension"):
        replace(valid, dimension_kind="linear")
    with pytest.raises(ValueError, match="ref_pts must agree"):
        replace(valid, ref_pts=((0, 0, 0), (3, 0, 0)))
