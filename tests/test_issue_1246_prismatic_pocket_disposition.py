"""#1246: recognised prismatic pockets fail visibly without false dimensions."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from build123d import (
    Box,
    BuildPart,
    BuildSketch,
    Ellipse,
    GeomType,
    Plane,
    Pos,
    RegularPolygon,
    Rot,
    extrude,
)
from quiddity import build_raw_recognition_result

from draftwright import build_drawing
from draftwright.linting.coverage import (
    _principal_boundary_plane,
    _recess_matches_principal_wire,
)
from draftwright.linting.prismatic_pocket_coverage import lint_prismatic_pocket_coverage


def _blind_tool(profile) -> object:
    with BuildPart() as tool:
        with BuildSketch(Plane.XY.offset(4)):
            profile()
        extrude(amount=20)
    return tool.part


def _hexagonal_pocket_part(*, rectangular: bool = False, ellipse: bool = False):
    part = Box(120, 80, 20)
    part -= Pos(-30 if rectangular or ellipse else 0, 0, 0) * _blind_tool(
        lambda: RegularPolygon(12, 6)
    )
    if rectangular:
        part -= Pos(30, 0, 4) * Box(30, 24, 20)
    if ellipse:
        part -= Pos(30, 0, 0) * _blind_tool(lambda: Ellipse(8, 5))
    return part


def _matched_mouth(part, pocket):
    bbox = part.bounding_box()
    tol = max(1e-5, max(float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z)) * 1e-5)
    for face in part.faces():
        boundary = _principal_boundary_plane(face, bbox)
        if boundary is None:
            continue
        axis, plane_axes, at = boundary
        for wire in face.inner_wires():
            if _recess_matches_principal_wire(pocket, wire, axis, plane_axes, at, tol):
                return wire, axis, plane_axes, at, tol
    raise AssertionError("fixture has no principal mouth matching its PrismaticPocket record")


def test_a_prismatic_pocket_is_specific_actionable_and_non_info() -> None:
    part = _hexagonal_pocket_part()
    recognition = build_raw_recognition_result(part)

    assert len(recognition.section_recesses) == 1
    assert all(
        recess.classification.feature_kind == "pocket" for recess in recognition.section_recesses
    )
    drawing = build_drawing(part)
    issues = drawing.lint()

    assert [issue.code for issue in issues] == ["prismatic_pocket_requirement_unsupported"]
    assert issues[0].severity == "warning"
    assert "recognised 6-sided blind prismatic recess 6 mm deep (1 of 1)" in issues[0].message
    assert "outside automatic drawing approval" in issues[0].message
    summary = drawing.lint_summary()
    assert summary["warnings"] == summary["geometry_issues"] == 1
    assert summary["quality"]["completeness"]["unrecognised_geometry_reports"] == 0


def test_a_prismatic_pocket_does_not_hide_an_unrelated_unsupported_profile() -> None:
    part = _hexagonal_pocket_part(ellipse=True)
    recognition = build_raw_recognition_result(part)

    assert len(recognition.section_recesses) == 1
    codes = [issue.code for issue in build_drawing(part).lint()]
    assert codes.count("prismatic_pocket_requirement_unsupported") == 1
    assert codes.count("unrecognised_defining_geometry") == 1


@pytest.mark.parametrize(
    "case",
    (
        "wrong_axis",
        "wrong_mouth",
        "capped_mouth",
        "side_count",
        "unsupported_edge",
        "malformed_record",
        "vertex_mismatch",
    ),
)
def test_prismatic_pocket_profile_correlation_fails_closed(case: str) -> None:
    part = _hexagonal_pocket_part()
    pocket = build_raw_recognition_result(part).section_recesses[0]
    wire, axis, plane_axes, at, tol = _matched_mouth(part, pocket)
    candidate = pocket
    candidate_wire = wire

    geometry = pocket.geometry
    if case == "wrong_axis":
        frame = replace(geometry.frame, run=(1.0, 0.0, 0.0), u=(0.0, 1.0, 0.0), v=(0.0, 0.0, 1.0))
        candidate = replace(pocket, geometry=replace(geometry, frame=frame))
    elif case == "wrong_mouth":
        interval = tuple(value + 10 for value in geometry.run_interval)
        candidate = replace(pocket, geometry=replace(geometry, run_interval=interval))
    elif case == "capped_mouth":
        # Opening the opposite end cannot make this physical mouth count as supported.
        ends = replace(geometry.ends, low=geometry.ends.high, high=geometry.ends.low)
        candidate = replace(pocket, geometry=replace(geometry, ends=ends))
    elif case == "side_count":
        candidate_wire = SimpleNamespace(vertices=lambda: wire.vertices()[:-1], edges=wire.edges)
    elif case == "unsupported_edge":
        candidate_wire = SimpleNamespace(
            vertices=wire.vertices,
            edges=lambda: (
                [SimpleNamespace(geom_type=GeomType.ELLIPSE)] * len(geometry.profile.boundary)
            ),
        )
    elif case == "malformed_record":
        candidate = object()
    elif case == "vertex_mismatch":
        boundary = tuple(
            replace(vertex, point=tuple(2 * c for c in vertex.point))
            for vertex in geometry.profile.boundary
        )
        profile = replace(geometry.profile, boundary=boundary)
        candidate = replace(pocket, geometry=replace(geometry, profile=profile))

    assert not _recess_matches_principal_wire(
        candidate,
        candidate_wire,
        axis,
        plane_axes,
        at,
        tol,
    )


def test_a_prismatic_pocket_is_an_explicit_unsupported_completeness_outcome() -> None:
    completeness = build_drawing(_hexagonal_pocket_part()).lint_summary()["quality"][
        "completeness"
    ]

    assert completeness["available"] is True
    assert completeness["audited_score"] == 0.0
    assert completeness["requirements"] == 1
    assert completeness["unsupported"] == 1
    assert completeness["by_family"]["section_recesses"] == 1
    assert "section_recesses" not in completeness["unscored_recognized_families"]


def test_aggregate_reconciliation_counts_the_rectangular_recess_only_as_pocket() -> None:
    part = _hexagonal_pocket_part(rectangular=True)

    recognition = build_raw_recognition_result(part)
    assert len(recognition.section_recesses) == 2
    assert {recess.classification.section_shape for recess in recognition.section_recesses} == {
        "rectangular",
        "hexagonal",
    }

    issues = lint_prismatic_pocket_coverage(recognition)
    assert len(issues) == 1
    completeness = build_drawing(part).lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == 6
    assert completeness["placed"] == 5
    assert completeness["unsupported"] == 1
    assert completeness["by_family"]["pockets"] == 5
    assert completeness["by_family"]["section_recesses"] == 1


def test_a_four_sided_survivor_is_unsupported_not_misclassified_as_non_rectangular() -> None:
    part = Box(80, 80, 20) - Pos(0, 0, 4) * Rot(0, 0, 30) * Box(30, 24, 20)

    recognition = build_raw_recognition_result(part)
    assert all(
        recess.classification.feature_kind == "pocket" for recess in recognition.section_recesses
    )
    assert len(recognition.section_recesses) == 1
    assert len(recognition.section_recesses[0].geometry.profile.boundary) == 4

    drawing = build_drawing(part)
    codes = [issue.code for issue in drawing.lint()]
    assert codes.count("prismatic_pocket_requirement_unsupported") == 1
    assert "unrecognised_defining_geometry" not in codes
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == completeness["unsupported"] == 1
