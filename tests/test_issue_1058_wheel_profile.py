"""Evidence-backed recognition and coverage for the double-D wheel profile (#1058)."""

from collections import Counter
from dataclasses import replace
from hashlib import sha256
from inspect import signature
from math import asin, sqrt
from pathlib import Path
from types import SimpleNamespace

import pytest
from b123d_recognisers import (
    build_recognition_result,
    recognise_double_d_bores,
    recognise_repeating_radial_profiles,
)
from b123d_recognisers.profiled_bores import (
    DoubleDProfile,
    double_d_bores_from_openings,
    double_d_profile,
    principal_boundary_plane,
)
from b123d_recognisers.repeating_profiles import (
    _CurveEvidence,
    _cyclic_edge_orbits,
    _distance,
    _one_closed_cycle,
    _polar_signature,
    _rotate,
    _sample_wire,
)
from build123d import (
    Align,
    Box,
    Cylinder,
    GeomType,
    Plane,
    Polygon,
    Pos,
    RectangleRounded,
    Rot,
    SlotOverall,
    extrude,
    import_step,
)
from conftest import counting_calls

from draftwright import Sheet, build_drawing
from draftwright.annotations.from_model import callout_from_spec
from draftwright.annotations.holes import _record_callout_drop
from draftwright.builder import detect_part_model
from draftwright.linting.coverage import CoverageState, lint_principal_profile_coverage
from draftwright.linting.profiled_bore_coverage import (
    lint_profiled_bore_coverage,
    profiled_bore_key,
)
from draftwright.model import (
    Frame,
    HoleFeature,
    PartModel,
    double_d_bore,
    envelope,
    pattern,
    plan_dimensions,
)
from draftwright.model.callout import hole_callout_spec
from draftwright.sheet_emit import _feature_line, _hole_line, emit_sheet_script

_WHEEL = Path(__file__).parent / "fixtures" / "issue_1058_wheel_rh.step"
_SHA256 = "4911c06426f0ceeedc198416e058aabc1c1a6a65e9e766eca7efed5484a27cda"
_CENTER = (Align.CENTER, Align.CENTER, Align.CENTER)
_RADIAL_REPEAT_COUNT = 13
_CORRESPONDENCE_TOL = 1e-5


def _double_d_bore():
    return Box(30, 30, 10, align=_CENTER) - _double_d_cutter()


def _double_d_cutter():
    return Cylinder(5, 20, align=_CENTER) & Box(7.2, 20, 30, align=_CENTER)


def _lens_bore():
    cutter = (Pos(-2, 0, 0) * Cylinder(5, 20, align=_CENTER)) & (
        Pos(2, 0, 0) * Cylinder(5, 20, align=_CENTER)
    )
    return Box(30, 30, 10, align=_CENTER) - cutter


def _l_bore():
    profile = Plane.XY * Polygon((-5, -5), (5, -5), (5, 0), (0, 0), (0, 5), (-5, 5))
    return Box(30, 30, 10, align=_CENTER) - extrude(profile, 20, both=True)


def _blind_double_d_recess():
    cutter = Cylinder(5, 5, align=_CENTER) & Box(7.2, 20, 5, align=_CENTER)
    return Box(30, 30, 10, align=_CENTER) - Pos(0, 0, 2.5) * cutter


def _opposed_double_d_recesses_with_a_web():
    cutter = Cylinder(5, 3, align=_CENTER) & Box(7.2, 20, 3, align=_CENTER)
    return Box(30, 30, 10, align=_CENTER) - Pos(0, 0, 3.5) * cutter - Pos(0, 0, -3.5) * cutter


def _point(x, y, z=0.0):
    return SimpleNamespace(X=x, Y=y, Z=z)


class _ProfileEdge:
    def __init__(self, geom_type, *, vertices=(), radius=None, centre=None, length=0.0):
        self.geom_type = geom_type
        self._vertices = list(vertices)
        if radius is not None:
            self.radius = radius
        if centre is not None:
            self.arc_center = centre
        self.length = length

    def vertices(self):
        return self._vertices


class _ProfileWire:
    def __init__(self, edges, *, size=(7.2, 10.0, 0.0)):
        self._edges = edges
        self._bbox = SimpleNamespace(size=_point(*size))

    def edges(self):
        return self._edges

    def bounding_box(self):
        return self._bbox


def _profile_evidence(case="valid"):
    """Minimal line/arc evidence for exercising the pure correspondence proof."""
    radius = 5.0
    half_af = 3.6
    half_chord = sqrt(radius * radius - half_af * half_af)
    centre = _point(0.0, 0.0)
    lines = [
        _ProfileEdge(
            GeomType.LINE,
            vertices=(_point(-half_af, -half_chord), _point(-half_af, half_chord)),
            length=2 * half_chord,
        ),
        _ProfileEdge(
            GeomType.LINE,
            vertices=(_point(half_af, -half_chord), _point(half_af, half_chord)),
            length=2 * half_chord,
        ),
    ]
    arc_length = 2 * radius * asin(half_af / radius)
    arcs = [
        _ProfileEdge(GeomType.CIRCLE, radius=radius, centre=centre, length=arc_length),
        _ProfileEdge(GeomType.CIRCLE, radius=radius, centre=centre, length=arc_length),
    ]
    size = (7.2, 10.0, 0.0)

    if case == "zero-radius":
        arcs[0].radius = 0.0
    elif case == "missing-radius":
        del arcs[0].radius
    elif case == "one-vertex":
        lines[0]._vertices.pop()
    elif case == "zero-line":
        lines[0]._vertices[1] = lines[0]._vertices[0]
    elif case == "off-circle-end":
        lines[0]._vertices[0] = _point(0.0, 0.0)
    elif case == "non-parallel":
        lines[1]._vertices = [_point(-half_chord, half_af), _point(half_chord, half_af)]
    elif case == "same-side":
        lines[1]._vertices = list(lines[0]._vertices)
        lines[1].length = lines[0].length
    elif case == "degenerate-flat":
        half_af = radius - 1e-6
        half_chord = sqrt(radius * radius - half_af * half_af)
        for line, x in zip(lines, (-half_af, half_af), strict=True):
            line._vertices = [_point(x, -half_chord), _point(x, half_chord)]
            line.length = 2 * half_chord
        size = (2 * half_af, 2 * radius, 0.0)
    elif case == "wrong-chord-length":
        lines[0].length += 1.0
    elif case == "wrong-arc-length":
        arcs[0].length += 1.0

    return _ProfileWire([*lines, *arcs], size=size)


def _sample_outer_wire(face, *, samples=9) -> tuple[_CurveEvidence, ...]:
    assert samples == 9
    evidence = _sample_wire(face.outer_wire(), ("x", "y"))
    assert evidence is not None
    return evidence


@pytest.fixture(scope="module")
def wheel_part():
    return import_step(str(_WHEEL))


@pytest.fixture(scope="module")
def wheel_drawing():
    return build_drawing(_WHEEL)


def _wheel_end_faces(part):
    bbox = part.bounding_box()
    return [face for face in part.faces() if principal_boundary_plane(face, bbox)]


def test_real_wheel_fixture_proves_the_double_d_profile(wheel_part):
    assert sha256(_WHEEL.read_bytes()).hexdigest() == _SHA256
    assert len(wheel_part.solids()) == 1
    assert Counter(face.geom_type for face in wheel_part.faces()) == Counter(
        {GeomType.BSPLINE: 273, GeomType.CYLINDER: 15, GeomType.PLANE: 4}
    )

    inner = [
        wire
        for face in wheel_part.faces()
        if face.geom_type == GeomType.PLANE
        for wire in face.inner_wires()
    ]
    assert len(inner) == 2, "the same through profile must be visible at both end faces"
    for wire in inner:
        edges = list(wire.edges())
        assert Counter(edge.geom_type for edge in edges) == Counter(
            {GeomType.CIRCLE: 2, GeomType.LINE: 2}
        )
        assert [
            edge.radius for edge in edges if edge.geom_type == GeomType.CIRCLE
        ] == pytest.approx([1.75, 1.75])
        bb = wire.bounding_box()
        assert min(bb.size.X, bb.size.Y) == pytest.approx(2.5)
        assert 1.75 != pytest.approx(min(bb.size.X, bb.size.Y) / 2), (
            "a true obround cap radius is half its short extent; this is a double-D"
        )


def test_real_wheel_proves_complete_thirteen_sector_correspondence(wheel_part):
    end_faces = _wheel_end_faces(wheel_part)
    assert len(end_faces) == 2
    boundaries = [principal_boundary_plane(face, wheel_part.bounding_box()) for face in end_faces]
    assert [boundary[0] for boundary in boundaries if boundary] == ["z", "z"]
    assert sorted(boundary[2] for boundary in boundaries if boundary) == pytest.approx(
        [-3.8500001, 3.8500001]
    )
    for face in end_faces:
        edges = list(face.outer_wire().edges())
        assert Counter(edge.geom_type for edge in edges) == Counter(
            {GeomType.BSPLINE: 39, GeomType.CIRCLE: 13}
        )
        circles = [edge for edge in edges if edge.geom_type == GeomType.CIRCLE]
        assert [edge.radius for edge in circles] == pytest.approx([3.8] * 13)
        assert [(edge.arc_center.X, edge.arc_center.Y) for edge in circles] == pytest.approx(
            [(0.0, 0.0)] * 13,
            abs=1e-6,
        )

        evidence = _sample_outer_wire(face)
        orbits = _cyclic_edge_orbits(
            evidence,
            centre=(0.0, 0.0),
            repeat_count=_RADIAL_REPEAT_COUNT,
            tol=_CORRESPONDENCE_TOL,
        )
        assert orbits is not None
        assert [len(orbit) for orbit in orbits] == [13, 13, 13, 13]
        assert Counter(evidence[orbit[0]].kind for orbit in orbits) == Counter(
            {GeomType.BSPLINE.name: 3, GeomType.CIRCLE.name: 1}
        )


def test_production_recogniser_carries_complete_serialisable_profile_evidence(wheel_part):
    (profile,) = recognise_repeating_radial_profiles(wheel_part)

    assert profile.axis == "z"
    assert profile.centre == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)
    assert profile.span == pytest.approx((-3.8500001, 3.8500001))
    assert profile.repeat_count == 13
    assert profile.edge_count == 52
    assert Counter(item[0] for item in profile.sector_signature) == Counter(
        {GeomType.BSPLINE.name: 3, GeomType.CIRCLE.name: 1}
    )
    assert len(profile.to_dict()["sector_signature"]) == 4


def test_equal_profiles_on_distinct_bodies_remain_ambiguous(wheel_part, monkeypatch):
    import b123d_recognisers.repeating_profiles as repeating_profiles

    (profile,) = recognise_repeating_radial_profiles(wheel_part)
    bodies = (object(), object())
    part = SimpleNamespace(solids=lambda: bodies)
    monkeypatch.setattr(
        repeating_profiles,
        "_recognise_solid",
        lambda solid, **_kwargs: (
            [repeating_profiles._RepeatingRadialProposal(profile, object(), object())]
            if solid in bodies
            else []
        ),
    )

    assert repeating_profiles.recognise_repeating_radial_profiles(part) == [profile, profile]


def test_declared_gear_reconciles_to_the_recognition_owned_profile(wheel_part):
    sheet = Sheet(wheel_part)
    sheet.external_spur_gear(
        at=(0, 0, 0),
        axis="z",
        tooth_count=13,
        module=0.5,
        pressure_angle=20,
        profile_shift=0,
        face_width=7.7000002,
        tooth_thickness=0.78,
        tooth_thickness_tolerance=(-0.03, 0.01),
        flank_tolerance_class=7,
    )
    sheet.authored_dimensions()

    drawing = sheet.build()
    recognition = drawing.recognition()
    assert recognition is None, "declared build/render must remain recognition-free"
    codes = {issue.code for issue in drawing.lint()}
    assert drawing.recognition() is not None
    assert (
        not {
            "gear_correspondence_unverifiable",
            "gear_axis_mismatch",
            "gear_repeat_count_mismatch",
        }
        & codes
    )
    assert "unrecognised_defining_geometry" in codes


def test_full_profile_correspondence_ignores_start_and_traversal(wheel_part):
    evidence = _sample_outer_wire(_wheel_end_faces(wheel_part)[0])
    shifted = evidence[7:] + evidence[:7]
    reversed_traversal = tuple(
        replace(edge, points=tuple(reversed(edge.points))) for edge in reversed(evidence)
    )

    for candidate in (shifted, reversed_traversal):
        assert (
            _cyclic_edge_orbits(
                candidate,
                centre=(0.0, 0.0),
                repeat_count=_RADIAL_REPEAT_COUNT,
                tol=_CORRESPONDENCE_TOL,
            )
            is not None
        )


def test_common_circle_arcs_alone_do_not_prove_full_sector_correspondence(wheel_part):
    evidence = _sample_outer_wire(_wheel_end_faces(wheel_part)[0])
    arcs = tuple(edge for edge in evidence if edge.kind == GeomType.CIRCLE.name)
    assert len(arcs) == 13
    assert (
        _cyclic_edge_orbits(
            arcs,
            centre=(0.0, 0.0),
            repeat_count=_RADIAL_REPEAT_COUNT,
            tol=_CORRESPONDENCE_TOL,
        )
        is None
    )


def test_empty_or_unsampleable_wire_evidence_fails_closed():
    class _BadEdge:
        geom_type = GeomType.BSPLINE
        length = 1.0

        def position_at(self, _fraction):
            raise RuntimeError("malformed curve")

    wire = SimpleNamespace(edges=lambda: [_BadEdge()])
    assert not _one_closed_cycle((), tol=_CORRESPONDENCE_TOL)
    assert _sample_wire(wire, ("x", "y")) is None


def test_ambiguous_or_self_joined_endpoint_graph_fails_closed():
    ambiguous = (
        _CurveEvidence("line", 1.0, ((0.0, 0.0), (1.5e-5, 0.0))),
        _CurveEvidence("line", 1.0, ((0.75e-5, 0.0), (1.0, 0.0))),
    )
    self_joined = (_CurveEvidence("line", 1.0, ((0.0, 0.0), (0.0, 0.0))),)

    assert not _one_closed_cycle(ambiguous, tol=1e-5)
    assert not _one_closed_cycle(self_joined, tol=1e-5)


def test_polar_signature_is_stable_across_the_angle_branch_cut():
    points = ((-1.0, 0.01), (-1.0, -0.01), (-0.9, -0.02))

    assert _polar_signature(points, (0.0, 0.0)) == _polar_signature(
        tuple(reversed(points)), (0.0, 0.0)
    )


def test_malformed_faces_and_a_shape_without_owned_solids_fail_closed(monkeypatch):
    import b123d_recognisers.repeating_profiles as repeating_profiles

    bbox = SimpleNamespace(
        size=_point(1.0, 1.0, 1.0),
        min=_point(0.0, 0.0, 0.0),
        max=_point(1.0, 1.0, 1.0),
    )
    shape = SimpleNamespace(
        bounding_box=lambda: bbox,
        faces=lambda: [object()],
        solids=lambda: [],
    )

    def malformed_boundary(*_args, **_kwargs):
        raise RuntimeError("malformed face")

    monkeypatch.setattr(repeating_profiles, "_prove_boundary", malformed_boundary)
    assert repeating_profiles.recognise_repeating_radial_profiles(shape) == []


def test_one_changed_intervening_curve_breaks_full_sector_correspondence(wheel_part):
    evidence = list(_sample_outer_wire(_wheel_end_faces(wheel_part)[0]))
    changed_index = next(
        index for index, edge in enumerate(evidence) if edge.kind == GeomType.BSPLINE.name
    )
    changed = evidence[changed_index]
    points = list(changed.points)
    x, y = points[len(points) // 2]
    points[len(points) // 2] = (x + 0.01, y)
    evidence[changed_index] = replace(changed, points=tuple(points))

    assert len([edge for edge in evidence if edge.kind == GeomType.CIRCLE.name]) == 13
    assert (
        _cyclic_edge_orbits(
            evidence,
            centre=(0.0, 0.0),
            repeat_count=_RADIAL_REPEAT_COUNT,
            tol=_CORRESPONDENCE_TOL,
        )
        is None
    )


def test_an_open_outer_cycle_cannot_prove_sector_correspondence(wheel_part):
    evidence = list(_sample_outer_wire(_wheel_end_faces(wheel_part)[0]))
    changed = evidence[0]
    points = list(changed.points)
    x, y = points[0]
    points[0] = (x + 0.01, y)
    evidence[0] = replace(changed, points=tuple(points))

    assert not _one_closed_cycle(tuple(evidence), tol=_CORRESPONDENCE_TOL)
    assert (
        _cyclic_edge_orbits(
            evidence,
            centre=(0.0, 0.0),
            repeat_count=_RADIAL_REPEAT_COUNT,
            tol=_CORRESPONDENCE_TOL,
        )
        is None
    )


def test_one_unequal_sector_pitch_breaks_correspondence_even_when_closed(wheel_part):
    evidence = list(_sample_outer_wire(_wheel_end_faces(wheel_part)[0]))
    endpoint = evidence[0].points[0]
    partners = [
        (edge_index, point_index)
        for edge_index, edge in enumerate(evidence[1:], start=1)
        for point_index in (0, -1)
        if _distance(endpoint, edge.points[point_index]) <= 1e-8
    ]
    assert len(partners) == 1

    moved = _rotate(endpoint, 0.01, centre=(0.0, 0.0))
    for edge_index, point_index in ((0, 0), partners[0]):
        edge = evidence[edge_index]
        points = list(edge.points)
        points[point_index] = moved
        evidence[edge_index] = replace(edge, points=tuple(points))

    assert _one_closed_cycle(tuple(evidence), tol=_CORRESPONDENCE_TOL)
    assert (
        _cyclic_edge_orbits(
            evidence,
            centre=(0.0, 0.0),
            repeat_count=_RADIAL_REPEAT_COUNT,
            tol=_CORRESPONDENCE_TOL,
        )
        is None
    )


def test_non_bijective_sector_mapping_is_rejected_before_orbit_claim(wheel_part, monkeypatch):
    import b123d_recognisers.repeating_profiles as repeating_profiles

    evidence = _sample_outer_wire(_wheel_end_faces(wheel_part)[0])
    assert _one_closed_cycle(evidence, tol=_CORRESPONDENCE_TOL)
    targets = [(index + 4) % len(evidence) for index in range(len(evidence))]
    targets[1] = targets[0]  # two source curves now claim the same physical target curve
    target_for = {id(source): evidence[target] for source, target in zip(evidence, targets)}

    def non_bijective(source, target, **_kwargs):
        return target is target_for[id(source)]

    monkeypatch.setattr(repeating_profiles, "_curves_match", non_bijective)
    assert (
        repeating_profiles._cyclic_edge_orbits(
            evidence,
            centre=(0.0, 0.0),
            repeat_count=_RADIAL_REPEAT_COUNT,
            tol=_CORRESPONDENCE_TOL,
        )
        is None
    )


def test_real_wheel_no_longer_gets_a_confident_envelope_only_result(wheel_drawing):
    assert Counter(feature.kind for feature in wheel_drawing.model().features) == Counter(
        {"envelope": 1, "hole": 1}
    )
    recognition = wheel_drawing.recognition()
    assert recognition is not None
    assert not recognition.holes and not recognition.slots and not recognition.pockets
    assert len(recognition.double_d_bores) == 1
    bore = next(feature for feature in wheel_drawing.model().features if feature.kind == "hole")
    assert bore.profile == "double_d"
    assert bore.diameter == pytest.approx(3.5)
    assert bore.across_flats == pytest.approx(2.5)
    assert bore.depth == pytest.approx(7.7)
    assert bore.through is True
    assert bore.profile_direction == pytest.approx((1.0, 0.0, 0.0))

    group = next(g for g in plan_dimensions(wheel_drawing.model()) if g.feature is bore)
    spec = hole_callout_spec(group)
    assert spec is not None
    assert spec["diameter"] == pytest.approx(3.5)
    assert spec["through"] is True
    assert spec["suffix"] == "DOUBLE-D 2.5 A/F"

    callout = wheel_drawing.get_annotation("hc_plan0")
    assert callout.covers_diameters == (3.5,)
    assert callout.covers_profiles == (("double_d", "z", True, 3.5, 2.5, (1.0, 0.0, 0.0)),)
    expected = callout_from_spec(spec, wheel_drawing.draft, None)
    assert expected is not None
    x0, _y0, x1, _y1 = callout.label_bbox
    assert x1 - x0 == pytest.approx(expected.callout_width, abs=0.5)
    centre = wheel_drawing.at("plan", *bore.frame.origin)
    tip = callout._tip_local
    assert abs(tip[0] - centre[0]) == pytest.approx(bore.across_flats * wheel_drawing.scale / 2)
    assert tip[1] == pytest.approx(centre[1])

    summary = wheel_drawing.lint_summary()
    assert summary["by_code"] == {
        "gear_semantics_missing": 1,
        "unrecognised_defining_geometry": 1,
    }
    assert summary["score"] < 1.0
    assert "unsupported outer boundary" in summary["issues"][0]["message"]
    assert "13 evenly spaced common-circle arcs" in summary["issues"][0]["message"]
    assert "gear" not in summary["issues"][0]["message"].lower()


def test_synthetic_double_d_profile_is_recognised_without_lint_rescans():
    detected = detect_part_model(_double_d_bore())
    with counting_calls({"orchestration": build_recognition_result}) as calls:
        drawing = build_drawing(_double_d_bore())
        assert tuple(detected.features) == tuple(drawing.model().features)
        assert calls == {"orchestration": 1}
        for _ in range(2):
            issues = drawing.lint()
            assert not [i for i in issues if i.code == "unrecognised_defining_geometry"]
        assert calls == {"orchestration": 1}


def test_physical_profile_scan_accepts_no_caller_extent():
    assert "bbox" not in signature(lint_principal_profile_coverage).parameters
    issues = lint_principal_profile_coverage(_double_d_bore())
    assert issues == []


@pytest.mark.parametrize("part", [_lens_bore(), _l_bore()], ids=("circular-arcs", "linear-l"))
def test_edge_types_alone_do_not_certify_a_supported_profile(part):
    assert recognise_double_d_bores(part) == []
    issues = [
        issue
        for issue in build_drawing(part).lint()
        if issue.code == "unrecognised_defining_geometry"
    ]
    assert len(issues) == 1


@pytest.mark.parametrize(
    "case",
    [
        "zero-radius",
        "missing-radius",
        "one-vertex",
        "zero-line",
        "off-circle-end",
        "non-parallel",
        "same-side",
        "degenerate-flat",
        "wrong-chord-length",
        "wrong-arc-length",
    ],
)
def test_malformed_line_arc_evidence_fails_closed(case):
    assert double_d_profile(_profile_evidence(case), ("x", "y"), tol=1e-5) is None


def test_an_unpaired_opening_and_a_failed_void_proof_are_not_bores():
    bbox = SimpleNamespace(min=_point(-5, -5, -5), max=_point(5, 5, 5))
    low = DoubleDProfile(
        centre=(0.0, 0.0, -5.0),
        major_diameter=10.0,
        across_flats=7.2,
        flat_direction=(1.0, 0.0, 0.0),
    )
    high = DoubleDProfile(
        centre=(0.0, 0.0, 5.0),
        major_diameter=10.0,
        across_flats=7.2,
        flat_direction=(1.0, 0.0, 0.0),
    )
    assert (
        double_d_bores_from_openings([("z", -5.0, low, object())], bbox, part=object(), tol=1e-5)
        == []
    )
    assert (
        double_d_bores_from_openings(
            [("z", -5.0, low, object()), ("z", 5.0, high, object())],
            bbox,
            part=object(),
            tol=1e-5,
        )
        == []
    )


def test_recogniser_reports_the_complete_double_d_geometry():
    assert recognise_double_d_bores(_double_d_bore())[0].to_dict() == {
        "axis": (0.0, 0.0, 1.0),
        "location": (0.0, 0.0, 5.0),
        "major_diameter": 10.0,
        "across_flats": 7.2,
        "depth": 10.0,
        "through": True,
        "flat_direction": (1.0, 0.0, 0.0),
    }


def test_double_d_orientation_is_not_limited_to_global_flat_axes():
    cutter = Rot(0, 0, 30) * _double_d_cutter()
    part = Box(30, 30, 10, align=_CENTER) - cutter
    bore = recognise_double_d_bores(part)[0]
    assert bore.major_diameter == pytest.approx(10)
    assert bore.across_flats == pytest.approx(7.2)
    assert bore.flat_direction == pytest.approx((0.8660254, 0.5, 0.0), abs=1e-6)


@pytest.mark.parametrize(
    ("part", "axis"),
    [
        (_double_d_bore(), (0.0, 0.0, 1.0)),
        (Rot(0, 90, 0) * _double_d_bore(), (1.0, 0.0, 0.0)),
        (Rot(90, 0, 0) * _double_d_bore(), (0.0, 1.0, 0.0)),
    ],
    ids=("z", "x", "y"),
)
def test_recogniser_is_uniform_across_principal_bore_axes(part, axis):
    assert recognise_double_d_bores(part)[0].axis == axis


def test_assembly_components_own_their_bore_correspondence():
    part = _double_d_bore() + Pos(0, 0, 30) * _double_d_bore()
    assert len(part.solids()) == 2
    bores = recognise_double_d_bores(part)
    assert len(bores) == 2
    assert [bore.depth for bore in bores] == pytest.approx([10, 10])
    assert lint_principal_profile_coverage(part) == []


def test_a_blind_double_d_recess_is_not_certified_as_a_through_bore():
    part = _blind_double_d_recess()
    assert recognise_double_d_bores(part) == []
    issues = [
        issue
        for issue in build_drawing(part).lint()
        if issue.code == "unrecognised_defining_geometry"
    ]
    assert len(issues) == 1
    assert "unsupported internal profile" in issues[0].message


def test_opposed_blind_recesses_do_not_prove_a_through_bore():
    part = _opposed_double_d_recesses_with_a_web()
    assert recognise_double_d_bores(part) == []
    issues = [
        issue
        for issue in build_drawing(part).lint()
        if issue.code == "unrecognised_defining_geometry"
    ]
    assert len(issues) == 1
    assert "unsupported internal profile" in issues[0].message


def test_declared_object_and_explicit_forms_preserve_profile_and_skip_recognition():
    part = Box(30, 30, 10, align=_CENTER)
    cutter = _double_d_cutter()
    with counting_calls({"orchestration": build_recognition_result}) as calls:
        sheet = Sheet(part)
        object_handle = sheet.double_d_bore(cutter)
        explicit_handle = sheet.double_d_bore(
            major_diameter=10,
            across_flats=7.2,
            at=(8, 0, 0),
            axis="z",
            depth=10,
            profile_direction=(-1, 0, 0),
        )
        sheet.authored_dimensions()
        for handle in (object_handle, explicit_handle):
            sheet.dimension(handle, "bore.diameter")
            sheet.dimension(handle, "profile_across_flats.length")
        drawing = sheet.build()
    assert calls == {}
    bores = [f for f in drawing.model().features if f.kind == "hole"]
    assert [(b.profile, b.diameter, b.across_flats) for b in bores] == [
        ("double_d", 10.0, 7.2),
        ("double_d", 10, 7.2),
    ]
    assert bores[1].profile_direction == pytest.approx((1.0, 0.0, 0.0))


def test_object_declaration_rejects_a_non_prismatic_double_d_tool():
    stepped = _double_d_cutter() + Box(12, 2, 2, align=_CENTER)
    with pytest.raises(ValueError, match="constant extrusion"):
        Sheet(Box(30, 30, 10, align=_CENTER)).double_d_bore(stepped)


def test_explicit_declaration_requires_the_complete_profile_geometry():
    with pytest.raises(ValueError, match="major_diameter"):
        double_d_bore()


@pytest.mark.parametrize(
    "kw",
    [
        {"profile": "keyway", "across_flats": 7.2, "profile_direction": (1, 0, 0)},
        {"profile": "double_d", "across_flats": None, "profile_direction": (1, 0, 0)},
        {"profile": "double_d", "across_flats": 10, "profile_direction": (1, 0, 0)},
        {"profile": "double_d", "across_flats": 7.2, "profile_direction": None},
        {"profile": "double_d", "across_flats": 7.2, "profile_direction": ()},
        {"profile": "double_d", "across_flats": 7.2, "profile_direction": (0, 0, 0)},
        {"profile": "double_d", "across_flats": 7.2, "profile_direction": (float("nan"), 0, 0)},
        {"profile": "double_d", "across_flats": 7.2, "profile_direction": (0, 0, 1)},
        {"profile": None, "across_flats": 7.2, "profile_direction": None},
    ],
)
def test_profile_invariants_are_owned_by_the_public_ir(kw):
    with pytest.raises(ValueError):
        HoleFeature(Frame((0, 0, 0), "z"), 10, depth=10, through=True, **kw)


def test_public_ir_refuses_the_unproved_blind_double_d_state():
    with pytest.raises(ValueError, match="through-only"):
        HoleFeature(
            Frame((0, 0, 0), "z"),
            10,
            depth=5,
            through=False,
            profile="double_d",
            across_flats=7.2,
            profile_direction=(1, 0, 0),
        )


def test_declared_model_cannot_omit_a_physical_double_d_bore_cleanly():
    part = _double_d_bore()
    drawing = build_drawing(part, model=[envelope(part)])
    codes = {issue.code for issue in drawing.lint()}
    assert "profiled_bore_not_dimensioned" in codes
    assert "feature_no_centermark" in codes


def test_a_dropped_profile_only_suppresses_the_exact_physical_requirement():
    part = _double_d_bore()
    recognition = build_recognition_result(part)
    exact = ("double_d", "z", True, 10.0, 7.2, (1.0, 0.0, 0.0))
    wrong_af = ("double_d", "z", True, 10.0, 6.8, (1.0, 0.0, 0.0))
    assert (
        lint_profiled_bore_coverage(
            part,
            [],
            recognition=recognition,
            dropped_profiles=[exact],
        )
        == []
    )
    assert [
        issue.code
        for issue in lint_profiled_bore_coverage(
            part,
            [],
            recognition=recognition,
            dropped_profiles=[wrong_af],
        )
    ] == ["profiled_bore_not_dimensioned"]


def test_profile_coverage_rejects_unowned_results_and_canonicalises_directions():
    part = _double_d_bore()
    assert lint_profiled_bore_coverage(part, [], recognition=None) == []
    with pytest.raises(TypeError, match="RecognitionResult"):
        lint_profiled_bore_coverage(part, [], recognition=object())

    for direction in ((1, 0), (0, 0, 0), (float("nan"), 0, 0)):
        with pytest.raises(ValueError, match="finite non-zero 3-vector"):
            profiled_bore_key("double_d", "z", True, 10, 7.2, direction)
    assert profiled_bore_key("double_d", "z", True, 10, 7.2, (-1, 0, 0))[-1] == (
        1.0,
        0.0,
        0.0,
    )

    recognition = build_recognition_result(part)
    issues = lint_profiled_bore_coverage(part, [], recognition=recognition, assembly=True)
    assert [issue.severity for issue in issues] == ["info"]


def test_a_dropped_counted_callout_records_every_profile_occurrence():
    exact = ("double_d", "z", True, 10.0, 7.2, (1.0, 0.0, 0.0))
    ctx = SimpleNamespace(
        coverage=CoverageState(),
        escalations=[],
        record_issue=lambda *_args, **_kwargs: None,
    )
    callout = SimpleNamespace(covers_profiles=(exact,), covers_count=3)
    _record_callout_drop(ctx, None, "plan", 10.0, "test", callout=callout)
    assert ctx.coverage.dropped_profiles == [exact, exact, exact]


def test_diameter_only_authored_callout_does_not_cover_the_profile_requirement():
    part = _double_d_bore()
    sheet = Sheet(part)
    bore = sheet.double_d_bore(
        major_diameter=10,
        across_flats=7.2,
        at=(0, 0, 5),
        axis="z",
        depth=10,
        profile_direction=(1, 0, 0),
    )
    sheet.authored_dimensions()
    sheet.dimension(bore, "bore.diameter")
    drawing = sheet.build()
    assert "profiled_bore_not_dimensioned" in {issue.code for issue in drawing.lint()}


def test_a_double_d_declaration_does_not_reconcile_against_a_plain_cylinder():
    part = Box(30, 30, 10, align=_CENTER) - Cylinder(5, 20, align=_CENTER)
    sheet = Sheet(part)
    bore = sheet.double_d_bore(
        major_diameter=10,
        across_flats=7.2,
        at=(0, 0, 5),
        axis="z",
        depth=10,
        profile_direction=(1, 0, 0),
    )
    sheet.authored_dimensions()
    sheet.dimension(bore, "bore.diameter")
    sheet.dimension(bore, "profile_across_flats.length")
    drawing = sheet.build()
    assert "declared_feature_absent" in {issue.code for issue in drawing.lint()}


def test_profile_round_trips_through_the_sheet_emitter_line():
    source = next(f for f in detect_part_model(_double_d_bore()).features if f.kind == "hole")
    line = _hole_line(source)
    assert "sheet.double_d_bore(" in line
    assert "major_diameter=10" in line and "across_flats=7.2" in line
    sheet = Sheet(Box(30, 30, 10, align=_CENTER))
    eval(line, {"sheet": sheet})
    assert sheet.features[0] == source


def test_profile_direction_keeps_angular_precision_in_the_sheet_emitter():
    source = double_d_bore(
        major_diameter=10,
        across_flats=7.2,
        at=(0, 0, 0),
        axis="z",
        depth=10,
        profile_direction=(0.866025403784, 0.5, 0),
    )
    line = _hole_line(source)
    assert "profile_direction=(0.866025, 0.5, 0)" in line
    sheet = Sheet(Box(30, 30, 10, align=_CENTER))
    eval(line, {"sheet": sheet})
    assert sheet.features[0].profile_direction == pytest.approx(source.profile_direction, abs=1e-6)


def test_double_d_pattern_member_round_trips_without_losing_its_profile():
    member = double_d_bore(
        major_diameter=10,
        across_flats=7.2,
        at=(0, 0, 0),
        axis="z",
        depth=10,
        profile_direction=(1, 0, 0),
    )
    source = pattern(
        member,
        kind="linear",
        count=2,
        at=(0, 0, 0),
        pitch=20,
        direction=(1, 0, 0),
    )
    line = _feature_line(source)
    assert "sheet.pattern(double_d_bore(" in line
    sheet = Sheet(Box(30, 30, 10, align=_CENTER))
    eval(line, {"sheet": sheet, "double_d_bore": double_d_bore})
    assert sheet.features[0] == source

    model = PartModel(
        bbox=Box(30, 30, 10, align=_CENTER).bounding_box(),
        orientation="prismatic",
        features=[source],
    )
    script = emit_sheet_script(model, "part = PART", "out", title="T", number="1")
    assert "from draftwright.model import double_d_bore" in script


def test_across_flats_cannot_survive_without_the_compound_callout_head():
    sheet = Sheet(Box(30, 30, 10, align=_CENTER))
    bore = sheet.double_d_bore(
        major_diameter=10,
        across_flats=7.2,
        at=(0, 0, 0),
        axis="z",
        depth=10,
    )
    sheet.authored_dimensions()
    sheet.dimension(bore, "profile_across_flats.length")
    with pytest.raises(ValueError, match="profile_across_flats.length"):
        sheet.build()


def test_an_intermediate_double_d_boss_boundary_is_not_an_opening():
    boss = Cylinder(5, 4) & Box(7.2, 20, 4)
    part = Box(30, 30, 10, align=_CENTER) + Pos(0, 0, 5) * boss
    assert not [
        issue
        for issue in build_drawing(part).lint()
        if issue.code == "unrecognised_defining_geometry"
    ]


@pytest.mark.parametrize(
    ("part", "expected_kind"),
    [
        (Box(30, 30, 10, align=_CENTER), None),
        (Box(30, 30, 10, align=_CENTER) - Cylinder(4, 20, align=_CENTER), "hole"),
        (Box(30, 30, 10, align=_CENTER) - Box(8, 16, 20, align=_CENTER), "slot"),
        (
            Box(30, 30, 10, align=_CENTER) - extrude(Plane.XY * SlotOverall(16, 8), 20, both=True),
            "slot",
        ),
        (
            Box(30, 30, 10, align=_CENTER)
            - extrude(Plane.XY * RectangleRounded(16, 10, 2), 20, both=True),
            "slot",
        ),
    ],
    ids=("plain", "circular", "rectangular", "obround", "rounded-rectangular"),
)
def test_supported_principal_profiles_remain_owned_and_clean(part, expected_kind):
    drawing = build_drawing(part)
    assert recognise_double_d_bores(part) == []
    if expected_kind is not None:
        assert expected_kind in {feature.kind for feature in drawing.model().features}
    assert not [
        issue for issue in drawing.lint() if issue.code == "unrecognised_defining_geometry"
    ]
