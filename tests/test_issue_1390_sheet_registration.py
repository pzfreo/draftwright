"""Repeated registration must not multiply manufacturing callout counts."""

from dataclasses import replace

import pytest
from build123d import Axis, Box, Cylinder, Pos

from draftwright import Sheet
from draftwright.model import ChamferFeature, chamfer


def test_add_returns_existing_registration_and_preserves_handle_after_reorder():
    sheet = Sheet(Box(20, 20, 10)).authored_dimensions()
    feature = chamfer(leg1=0.5, at=(0, 0, 5), axis="z")
    first = sheet.add(feature)
    second = sheet.add(feature)
    assert len(sheet.features) == 1
    sheet.add(replace(feature, leg1=1.0))
    sheet.features.reverse()
    sheet.dimension(second, "chamfer.length")
    assert sheet.model().authored_dimensions[0].feature is feature
    sheet.dimension(first, "chamfer.length")
    assert len(sheet.features) == 2


def test_equal_but_distinct_features_remain_distinct_registrations():
    sheet = Sheet(Box(20, 20, 10)).authored_dimensions()
    feature = chamfer(leg1=0.5, at=(0, 0, 5), axis="z")
    other = replace(feature)
    assert other == feature and other is not feature
    sheet.add(feature)
    sheet.add(other)
    assert len(sheet.features) == 2
    assert sheet.features[0] is feature and sheet.features[1] is other


@pytest.mark.parametrize("repeat", [False, True])
def test_detected_chamfer_keeps_single_rendered_count(repeat):
    part = Cylinder(5, 10) + Pos(0, 0, 7.5) * Cylinder(2.5, 5)
    part = part.chamfer(0.5, None, part.edges().group_by(Axis.Z)[0])
    sheet = Sheet.from_part(part, scale=2, page="A4")
    features = [f for f in sheet.features if isinstance(f, ChamferFeature)]
    assert len(features) == 1
    feature = features[0]
    sheet.authored_dimensions()
    target = sheet.add(feature) if repeat else feature
    sheet.dimension(target, "chamfer.length")
    drawing = sheet.build()
    assert sum(isinstance(f, ChamferFeature) for f in drawing.model().features) == 1
    annotations = drawing.annotations_of(feature)
    labels = [a.label for a in annotations.values() if hasattr(a, "label")]
    assert labels == ["C0.5"]
