"""#1246: recognised prismatic pockets fail visibly without false dimensions."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from b123d_recognisers import (
    build_recognition_result,
    recognise_pockets,
    recognise_prismatic_pockets,
)
from b123d_recognisers.profiled_bores import principal_boundary_plane
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

from draftwright import build_drawing
from draftwright.linting.coverage import _prismatic_pocket_matches_principal_wire
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
        boundary = principal_boundary_plane(face, bbox)
        if boundary is None:
            continue
        axis, plane_axes, at = boundary
        for wire in face.inner_wires():
            if _prismatic_pocket_matches_principal_wire(pocket, wire, axis, plane_axes, at, tol):
                return wire, axis, plane_axes, at, tol
    raise AssertionError("fixture has no principal mouth matching its PrismaticPocket record")


def test_a_prismatic_pocket_is_specific_actionable_and_non_info() -> None:
    part = _hexagonal_pocket_part()
    recognition = build_recognition_result(part)

    assert len(recognition.prismatic_pockets) == 1
    assert recognition.pockets == ()
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
    recognition = build_recognition_result(part)

    assert len(recognition.prismatic_pockets) == 1
    codes = [issue.code for issue in build_drawing(part).lint()]
    assert codes.count("prismatic_pocket_requirement_unsupported") == 1
    assert codes.count("unrecognised_defining_geometry") == 1


@pytest.mark.parametrize(
    "case",
    (
        "wrong_axis",
        "wrong_mouth",
        "invalid_open_sign",
        "side_count",
        "unsupported_edge",
        "malformed_record",
        "vertex_mismatch",
    ),
)
def test_prismatic_pocket_profile_correlation_fails_closed(case: str) -> None:
    part = _hexagonal_pocket_part()
    pocket = build_recognition_result(part).prismatic_pockets[0]
    wire, axis, plane_axes, at, tol = _matched_mouth(part, pocket)
    candidate = pocket
    candidate_wire = wire

    if case == "wrong_axis":
        candidate = replace(pocket, axis="x")
    elif case == "wrong_mouth":
        moved = list(pocket.at)
        moved["xyz".index(axis)] += 10
        candidate = replace(pocket, at=tuple(moved))
    elif case == "invalid_open_sign":
        candidate = replace(pocket, open_sign=0)
    elif case == "side_count":
        candidate = replace(pocket, sides=pocket.sides + 1)
    elif case == "unsupported_edge":
        candidate_wire = SimpleNamespace(
            vertices=wire.vertices,
            edges=lambda: [SimpleNamespace(geom_type=GeomType.ELLIPSE)] * len(pocket.section),
        )
    elif case == "malformed_record":
        candidate = object()
    elif case == "vertex_mismatch":
        section = list(pocket.section)
        section[0] = (100.0, 100.0)
        candidate = replace(pocket, section=tuple(section))

    assert not _prismatic_pocket_matches_principal_wire(
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
    assert completeness["by_family"]["prismatic_pockets"] == 1
    assert "prismatic_pockets" not in completeness["unscored_recognized_families"]


def test_aggregate_reconciliation_counts_the_rectangular_recess_only_as_pocket() -> None:
    part = _hexagonal_pocket_part(rectangular=True)

    assert len(recognise_prismatic_pockets(part)) == 2, "direct calls precede reconciliation"
    assert len(recognise_pockets(part)) == 1
    recognition = build_recognition_result(part)
    assert len(recognition.prismatic_pockets) == 1
    assert len(recognition.pockets) == 1

    issues = lint_prismatic_pocket_coverage(recognition)
    assert len(issues) == 1
    completeness = build_drawing(part).lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == 6
    assert completeness["placed"] == 5
    assert completeness["unsupported"] == 1
    assert completeness["by_family"]["pockets"] == 5
    assert completeness["by_family"]["prismatic_pockets"] == 1


def test_a_four_sided_survivor_is_unsupported_not_misclassified_as_non_rectangular() -> None:
    part = Box(80, 80, 20) - Pos(0, 0, 4) * Rot(0, 0, 30) * Box(30, 24, 20)

    recognition = build_recognition_result(part)
    assert recognition.pockets == ()
    assert len(recognition.prismatic_pockets) == 1
    assert recognition.prismatic_pockets[0].sides == 4

    drawing = build_drawing(part)
    codes = [issue.code for issue in drawing.lint()]
    assert codes.count("prismatic_pocket_requirement_unsupported") == 1
    assert "unrecognised_defining_geometry" not in codes
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == completeness["unsupported"] == 1
