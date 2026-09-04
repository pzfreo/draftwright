"""Frame-local AP242 PMI extraction boundary for #1357."""

from __future__ import annotations

from itertools import product
from pathlib import Path

import pytest
from b123d_recognisers import FrameGauge, PartFrame
from build123d import Axis, Box

from draftwright.model.ir import CylindricalReference
from draftwright.pmi import (
    _frame_point,
    _frame_vector,
    _shape_bbox,
    extract_pmi,
    extract_pmi_report,
)

FIXTURES = Path(__file__).parent / "fixtures"
CTC03 = FIXTURES / "nist_ctc_03_asme1_ap242.stp"
GRM03 = FIXTURES / "grm03_thumbwheel_drive_screw_ap242_pmi.step"

FRAME = PartFrame(
    origin=(11.0, -7.0, 3.0),
    x=(0.0, 1.0, 0.0),
    y=(0.0, 0.0, 1.0),
    z=(1.0, 0.0, 0.0),
    gauge=FrameGauge.FULL,
)


def _expected_point(point, frame=FRAME):
    relative = tuple(point[index] - frame.origin[index] for index in range(3))
    return tuple(
        sum(relative[index] * basis[index] for index in range(3))
        for basis in (frame.x, frame.y, frame.z)
    )


def _expected_vector(vector, frame=FRAME):
    return tuple(
        sum(vector[index] * basis[index] for index in range(3))
        for basis in (frame.x, frame.y, frame.z)
    )


def _expected_bbox(bbox, frame=FRAME):
    lower = bbox[:3]
    upper = bbox[3:]
    corners = tuple(
        _expected_point(corner, frame) for corner in product(*zip(lower, upper, strict=True))
    )
    return tuple(min(point[index] for point in corners) for index in range(3)) + tuple(
        max(point[index] for point in corners) for index in range(3)
    )


def _assert_point(actual, expected):
    assert actual == pytest.approx(expected, abs=1e-9)


def _assert_bbox(actual, expected):
    assert actual is not None
    assert actual == pytest.approx(expected, abs=1e-8)


def test_frame_primitives_distinguish_points_vectors_and_bound_transformed_topology():
    root_half = 2**-0.5
    oblique = PartFrame(
        origin=(10.0, 20.0, 30.0),
        x=(root_half, root_half, 0.0),
        y=(-root_half, root_half, 0.0),
        z=(0.0, 0.0, 1.0),
        gauge=FrameGauge.FULL,
    )

    _assert_point(_frame_point((12.0, 23.0, 35.0), oblique), (5 * root_half, root_half, 5.0))
    _assert_point(_frame_vector((2.0, 3.0, 5.0), oblique), (5 * root_half, root_half, 5.0))

    assert _frame_point((1.0, 2.0, 3.0), None) == (1.0, 2.0, 3.0)
    assert _frame_vector((1.0, 2.0, 3.0), None) == (1.0, 2.0, 3.0)

    source = Box(10, 2, 1)
    rotated = source.rotate(Axis.Z, 45)
    geometry_frame = PartFrame(
        origin=(0.0, 0.0, 0.0),
        x=(root_half, root_half, 0.0),
        y=(-root_half, root_half, 0.0),
        z=(0.0, 0.0, 1.0),
        gauge=FrameGauge.FULL,
    )
    expected = _shape_bbox(source.wrapped)
    actual = _shape_bbox(rotated.wrapped, geometry_frame)
    _assert_bbox(actual, expected)

    # Rotating the already-inflated source AABB would enclose air and lose the tight owner
    # boundary used by PMI correlation. The topology-first result recovers the original box.
    inflated = _expected_bbox(_shape_bbox(rotated.wrapped), geometry_frame)
    assert inflated[3] - inflated[0] > actual[3] - actual[0] + 1.0
    assert inflated[4] - inflated[1] > actual[4] - actual[1] + 1.0
    assert _shape_bbox(source.wrapped, None) == expected


@pytest.mark.parametrize("fixture", [CTC03, GRM03], ids=("ctc03", "grm03"))
def test_real_ap242_geometry_is_expressed_in_the_requested_frame(fixture):
    source = extract_pmi_report(fixture)
    local = extract_pmi_report(fixture, frame=FRAME)

    assert local.sources == source.sources
    assert tuple(record.source_id for record in local.records) == tuple(
        record.source_id for record in source.records
    )
    axis_map = {"X": "Z", "Y": "X", "Z": "Y", "?": "?", "": ""}

    for source_record, local_record in zip(source.records, local.records, strict=True):
        scalar_fields = (
            "kind",
            "type_code",
            "value",
            "upper_tol",
            "lower_tol",
            "lower_bound",
            "upper_bound",
            "label",
            "source_id",
            "datum_refs",
            "part21_id",
            "source_category",
            "gtol_modifiers",
            "source_ids",
            "datum_contexts",
            "reference_item_ids",
            "semantic_name",
            "shape_aspect_ids",
        )
        assert tuple(getattr(local_record, field) for field in scalar_fields) == tuple(
            getattr(source_record, field) for field in scalar_fields
        )
        assert len(local_record.ref_pts) == len(source_record.ref_pts)
        for actual, point in zip(local_record.ref_pts, source_record.ref_pts, strict=True):
            _assert_point(actual, _expected_point(point))
        if source_record.ref_bbox is None:
            assert local_record.ref_bbox is None
        else:
            _assert_bbox(local_record.ref_bbox, _expected_bbox(source_record.ref_bbox))
        assert local_record.dominant_axis == axis_map[source_record.dominant_axis]
        assert local_record.reference_axis == axis_map[source_record.reference_axis]

        assert len(local_record.cylindrical_refs) == len(source_record.cylindrical_refs)
        for actual, cylinder in zip(
            local_record.cylindrical_refs, source_record.cylindrical_refs, strict=True
        ):
            expected = CylindricalReference.canonical(
                axis_point=_expected_point(cylinder.axis_origin),
                axis_direction=_expected_vector(cylinder.axis_direction),
                radius=cylinder.radius,
                local_interval=cylinder.axial_interval,
                sense=cylinder.sense,
            )
            _assert_point(actual.axis_origin, expected.axis_origin)
            _assert_point(actual.axis_direction, expected.axis_direction)
            assert actual.radius == pytest.approx(expected.radius, abs=1e-9)
            assert actual.axial_interval == pytest.approx(expected.axial_interval, abs=1e-9)
            assert actual.sense == expected.sense


def test_default_public_api_remains_caller_space_compatible():
    assert extract_pmi_report(CTC03, frame=None) == extract_pmi_report(CTC03)
    assert extract_pmi(CTC03, frame=None) == extract_pmi(CTC03)
