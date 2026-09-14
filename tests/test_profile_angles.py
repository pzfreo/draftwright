"""Released profile evidence supplies angle requirements without merging bodies."""

from dataclasses import replace
from math import cos, dist, pi, sin
from types import SimpleNamespace

import pytest
from build123d import (
    Axis,
    Box,
    Compound,
    Cylinder,
    Polygon,
    Pos,
    RegularPolygon,
    Rot,
    extrude,
    fillet,
)
from quiddity.evidence import ProfileLine, build_recognition_evidence

from draftwright import Sheet
from draftwright.model import AngularReference, angle
from draftwright.profile_angles import profile_angle_repetitions, profile_angle_requirements
from draftwright.recognition_ownership import RecognitionOwnershipBuilder


@pytest.fixture(scope="module", params=[(0, 0, 0), (90, 0, 0), (0, 90, 0)])
def profile_evidence(request):
    outline = RegularPolygon(30, 3)
    ring = extrude(outline - RegularPolygon(8, 3), amount=4)
    assert len(ring.solids()) == 1
    part = Rot(*request.param) * Compound(
        children=[ring, Pos(100, 0, 0) * ring, Pos(200, 0, 0) * Box(20, 20, 4)]
    )
    assert len(part.solids()) == 3
    evidence = build_recognition_evidence(part)
    return evidence, profile_angle_requirements(evidence)


def test_sharp_profiles_keep_distinct_bodies_and_exclude_inner_wires(profile_evidence):
    evidence, requirements = profile_evidence
    assert len(requirements) == 6
    assert len({item.source.body_faces for item in requirements}) == 2
    assert len({item.source.face for item in requirements}) == 2
    for item in requirements:
        normal = item.source.profile.normal
        axis = max(range(3), key=lambda index: abs(normal[index]))
        assert normal[axis] == pytest.approx((1, -1, 1)[axis])
        assert item.source.profile.inner_loop_count == 1
        assert len(item.source.profile.supports) == 3
        assert not item.virtual_vertex
        assert AngularReference(
            item.vertex, item.first, item.second
        ).angle_degrees == pytest.approx(60)
        for index, point in ((item.first_index, item.first), (item.second_index, item.second)):
            support = item.source.profile.supports[index]
            assert type(support) is ProfileLine
            edge = evidence.profile_edge(item.source, index)
            endpoints = [tuple(edge.position_at(t)) for t in (0, 1)]
            assert min(dist(point, endpoint) for endpoint in endpoints) < 1e-6
            assert min(dist(item.vertex, endpoint) for endpoint in endpoints) < 1e-6


def test_profile_binding_retains_exact_supports_and_rejects_foreign_authority(profile_evidence):
    evidence, requirements = profile_evidence
    assert len(requirements) == 6
    ledger = RecognitionOwnershipBuilder(evidence)
    item = requirements[0]
    feature = angle(vertex=item.vertex, first=item.first, second=item.second, sector="opposite")
    ledger.bind_profile_angle(item, feature)
    (binding,) = ledger.snapshot().profile_angles
    assert binding.requirement is item and binding.feature is feature
    with pytest.raises(ValueError, match="already has an owner"):
        ledger.bind_profile_angle(item, replace(feature))
    replacement = replace(feature)
    ledger.remap_feature(feature, (replacement,))
    (remapped,) = ledger.snapshot().profile_angles
    assert remapped.requirement is item and remapped.feature is replacement
    ledger.remap_feature(replace(replacement), (feature,))
    assert ledger.snapshot().profile_angles[0].feature is replacement
    foreign = build_recognition_evidence(Box(10, 10, 2))
    with pytest.raises(ValueError):
        RecognitionOwnershipBuilder(foreign).bind_profile_angle(item, feature)


def test_absent_profile_authority_does_not_fabricate_requirements():
    assert profile_angle_requirements(None) == ()
    assert profile_angle_repetitions(None) == ()


@pytest.fixture(scope="module")
def chamfer_and_unrelated_equal_corners():
    part = extrude(
        Polygon((-45, -30), (45, -30), (45, 18), (33, 30), (0, 30), (-45, -15), align=None),
        amount=20,
    )
    evidence = build_recognition_evidence(part)
    assert len(evidence.result.chamfers) == 1
    assert evidence.result.chamfers[0].leg1 == 12
    return part, evidence


def test_only_the_chamfers_actual_shared_edges_define_automatic_angles(
    chamfer_and_unrelated_equal_corners,
):
    _, evidence = chamfer_and_unrelated_equal_corners
    # With no feature definitions, the same supplied profile has four 135° corners.
    profiles_only = SimpleNamespace(
        features=(), faces=evidence.faces, planar_outer_profile=evidence.planar_outer_profile
    )
    all_corners = profile_angle_requirements(profiles_only)
    assert len(all_corners) == 4
    assert all(
        AngularReference(c.vertex, c.first, c.second).angle_degrees == pytest.approx(135)
        for c in all_corners
    )
    remaining = profile_angle_requirements(evidence)
    assert {corner.vertex for corner in remaining} == {(-45, -15, 20), (0, 30, 20)}


@pytest.mark.parametrize("kind", ("chamfer", "regular-polygon"))
def test_an_explicit_angle_remains_available_when_a_feature_callout_already_defines_it(
    chamfer_and_unrelated_equal_corners, kind
):
    if kind == "chamfer":
        part, _ = chamfer_and_unrelated_equal_corners
        vertex, first, second = (33, 30, 20), (0, 30, 20), (39, 24, 20)
        label = "135° ±0.05°"
    else:
        part = extrude(RegularPolygon(20, 6), amount=30)
        vertex, first, second = (
            (20, 0, 30),
            (10, 20 * sin(pi / 3), 30),
            (10, -20 * sin(pi / 3), 30),
        )
        label = "120° ±0.05°"
    sheet = Sheet(part, page="A2", scale=1)
    handle = sheet.angle(vertex=vertex, first=first, second=second, sector="opposite")
    handle.tolerance(0.05, on="included.angle")
    sheet.dimension(handle, "included.angle")
    drawing = sheet.build()
    (mark,) = [item for _, item in drawing.iter_annotations() if hasattr(item, "measured_angle")]
    assert mark.label == label
    assert not [issue for issue in drawing.lint() if issue.code.startswith("angular_support_")]


@pytest.fixture(scope="module", params=[False, True])
def repeated_profile_evidence(request):
    part = extrude(RegularPolygon(30, 3), amount=4)
    vertices = [tuple(edge.center()) for edge in part.edges().filter_by(Axis.Z)]
    for index, vertex in enumerate(vertices):
        edge = min(
            part.edges().filter_by(Axis.Z), key=lambda item: dist(tuple(item.center()), vertex)
        )
        part = fillet(edge, 3 if request.param and index == 0 else 2)
    plain = Pos(0, 0, 20) * part
    for index in range(3):
        theta = index * 2 * pi / 3
        part -= Pos(10 * cos(theta), 10 * sin(theta), 2) * Cylinder(2, 8)
    compound = Compound(children=[part, plain])
    assert len(compound.solids()) == 2
    evidence = build_recognition_evidence(compound)
    assert len(evidence.result.hole_patterns) == 1
    requirements = profile_angle_requirements(evidence)
    assert len(requirements) == 6
    assert all(
        AngularReference(item.vertex, item.first, item.second).angle_degrees == pytest.approx(60)
        for item in requirements
    ), "changing one fillet must leave all six angle values equal"
    return evidence, request.param


def test_repetition_requires_the_whole_profile_and_same_body_pattern(repeated_profile_evidence):
    evidence, broken_rotation = repeated_profile_evidence
    repetitions = profile_angle_repetitions(evidence)
    if broken_rotation:
        assert repetitions == (), "equal angles cannot hide a non-repeating rounded profile"
    else:
        assert len(repetitions) == 1, "the unpatterned second body cannot borrow the first pattern"
        (group,) = repetitions
        assert group.pattern is evidence.result.hole_patterns[0]
        assert len(group.members) == 3
        assert len({member.first_index for member in group.members}) == 3
        assert all(member.vertex[2] == pytest.approx(4) for member in group.members)
