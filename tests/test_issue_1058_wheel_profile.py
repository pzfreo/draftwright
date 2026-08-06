"""Evidence-backed coverage for the unsupported double-D wheel bore (#1058)."""

from collections import Counter
from hashlib import sha256
from inspect import signature
from pathlib import Path

import pytest
from build123d import (
    Align,
    Box,
    Cylinder,
    GeomType,
    Plane,
    Polygon,
    Pos,
    RectangleRounded,
    SlotOverall,
    extrude,
    import_step,
)
from conftest import counting_calls

from draftwright import build_drawing
from draftwright.linting.coverage import lint_principal_profile_coverage
from draftwright.recognition import build_recognition_result

_WHEEL = Path(__file__).parent / "fixtures" / "issue_1058_wheel_rh.step"
_SHA256 = "4911c06426f0ceeedc198416e058aabc1c1a6a65e9e766eca7efed5484a27cda"
_CENTER = (Align.CENTER, Align.CENTER, Align.CENTER)


def _double_d_bore():
    cutter = Cylinder(5, 20, align=_CENTER) & Box(7.2, 20, 30, align=_CENTER)
    return Box(30, 30, 10, align=_CENTER) - cutter


def _lens_bore():
    cutter = (Pos(-2, 0, 0) * Cylinder(5, 20, align=_CENTER)) & (
        Pos(2, 0, 0) * Cylinder(5, 20, align=_CENTER)
    )
    return Box(30, 30, 10, align=_CENTER) - cutter


def _l_bore():
    profile = Plane.XY * Polygon((-5, -5), (5, -5), (5, 0), (0, 0), (0, 5), (-5, 5))
    return Box(30, 30, 10, align=_CENTER) - extrude(profile, 20, both=True)


@pytest.fixture(scope="module")
def wheel_part():
    return import_step(str(_WHEEL))


@pytest.fixture(scope="module")
def wheel_drawing():
    return build_drawing(_WHEEL)


def test_real_wheel_fixture_proves_the_unsupported_profile(wheel_part):
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


def test_real_wheel_no_longer_gets_a_confident_envelope_only_result(wheel_drawing):
    assert Counter(feature.kind for feature in wheel_drawing.model().features) == Counter(
        {"envelope": 1}
    )
    recognition = wheel_drawing.recognition()
    assert recognition is not None
    assert not recognition.holes and not recognition.slots and not recognition.pockets

    summary = wheel_drawing.lint_summary()
    assert summary["by_code"] == {"unrecognised_defining_geometry": 1}
    assert summary["score"] < 1.0
    assert "unsupported internal profile" in summary["issues"][0]["message"]


def test_synthetic_double_d_profile_is_reported_without_recognition_rescans():
    with counting_calls({"orchestration": build_recognition_result}) as calls:
        drawing = build_drawing(_double_d_bore())
        assert calls == {"orchestration": 1}
        for _ in range(2):
            issues = drawing.lint()
            assert [issue.code for issue in issues] == ["unrecognised_defining_geometry"]
        assert calls == {"orchestration": 1}


def test_physical_profile_scan_accepts_no_caller_extent():
    assert "bbox" not in signature(lint_principal_profile_coverage).parameters
    issues = lint_principal_profile_coverage(_double_d_bore())
    assert any(
        issue.code == "unrecognised_defining_geometry"
        and "unsupported internal profile" in issue.message
        for issue in issues
    )


@pytest.mark.parametrize("part", [_lens_bore(), _l_bore()], ids=("circular-arcs", "linear-l"))
def test_edge_types_alone_do_not_certify_a_supported_profile(part):
    issues = [
        issue
        for issue in build_drawing(part).lint()
        if issue.code == "unrecognised_defining_geometry"
    ]
    assert len(issues) == 1


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
    if expected_kind is not None:
        assert expected_kind in {feature.kind for feature in drawing.model().features}
    assert not [
        issue for issue in drawing.lint() if issue.code == "unrecognised_defining_geometry"
    ]
