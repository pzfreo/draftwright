"""#1245: recognised passages fail visibly without invented drafting semantics."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from b123d_recognisers import build_raw_recognition_result
from build123d import Box, Ellipse, GeomType, Pos, RegularPolygon, extrude

from draftwright import build_drawing
from draftwright.linting.coverage import (
    _passage_matches_principal_wire,
    _principal_boundary_plane,
)
from draftwright.linting.passage_coverage import lint_passage_coverage
from draftwright.linting.quality import quality_components
from draftwright.registry import AnnotationRegistry


def _hex_passage_part():
    cutter = extrude(RegularPolygon(6, 6), amount=12, both=True)
    return Box(40, 40, 10) - cutter


def _matched_mouth(part, passage):
    bbox = part.bounding_box()
    tol = max(1e-5, max(float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z)) * 1e-5)
    for face in part.faces():
        boundary = _principal_boundary_plane(face, bbox)
        if boundary is None:
            continue
        axis, plane_axes, at = boundary
        for wire in face.inner_wires():
            if _passage_matches_principal_wire(passage, wire, axis, plane_axes, at, tol):
                return wire, axis, plane_axes, at, tol
    raise AssertionError("fixture has no principal mouth matching its Passage record")


def test_a_recognised_passage_is_specific_actionable_and_non_info() -> None:
    part = _hex_passage_part()
    recognition = build_raw_recognition_result(part)
    drawing = build_drawing(part)

    assert len(recognition.section_passages) == 1
    assert len(recognition.passages) == 1, "precondition: legacy projection exists"
    issues = drawing.lint()

    assert [issue.code for issue in issues] == ["passage_requirement_unsupported"]
    assert issues[0].severity == "warning"
    assert "recognised 6-edge prismatic through-opening (1 of 1)" in issues[0].message
    assert "outside automatic drawing approval" in issues[0].message
    summary = drawing.lint_summary()
    assert summary["warnings"] == summary["geometry_issues"] == 1
    assert summary["quality"]["completeness"]["unrecognised_geometry_reports"] == 0


def test_a_passage_does_not_hide_an_unrelated_unsupported_inner_profile() -> None:
    through = Pos(-10, 0, 0) * extrude(RegularPolygon(3, 6), amount=12, both=True)
    blind = Pos(10, 0, 2) * extrude(Ellipse(5, 3), amount=4)
    part = Box(40, 40, 10) - through - blind
    recognition = build_raw_recognition_result(part)

    assert len(recognition.section_passages) == 1
    codes = [issue.code for issue in build_drawing(part).lint()]
    assert codes.count("passage_requirement_unsupported") == 1
    assert codes.count("unrecognised_defining_geometry") == 1


@pytest.mark.parametrize(
    "case",
    (
        "non_principal_run",
        "no_endpoint_on_face",
        "boundary_cardinality",
        "unsupported_edge",
        "arc_cardinality",
        "malformed_record",
        "vertex_mismatch",
    ),
)
def test_passage_profile_correlation_fails_closed(case: str) -> None:
    part = _hex_passage_part()
    passage = build_raw_recognition_result(part).section_passages[0]
    wire, axis, plane_axes, at, tol = _matched_mouth(part, passage)
    candidate = passage
    candidate_wire = wire

    if case == "non_principal_run":
        candidate = SimpleNamespace(
            frame=SimpleNamespace(
                origin=passage.frame.origin,
                run=(0.1, 0.0, 1.0),
                u=passage.frame.u,
                v=passage.frame.v,
            ),
            run_interval=passage.run_interval,
            section=passage.section,
        )
    elif case == "no_endpoint_on_face":
        candidate = SimpleNamespace(
            frame=passage.frame,
            run_interval=(100.0, 110.0),
            section=passage.section,
        )
    elif case == "boundary_cardinality":
        candidate = SimpleNamespace(
            frame=passage.frame,
            run_interval=passage.run_interval,
            section=SimpleNamespace(boundary=passage.section.boundary[:-1]),
        )
    elif case == "unsupported_edge":
        candidate_wire = SimpleNamespace(
            vertices=wire.vertices,
            edges=lambda: (
                [SimpleNamespace(geom_type=GeomType.ELLIPSE)] * len(passage.section.boundary)
            ),
        )
    elif case == "arc_cardinality":
        boundary = list(passage.section.boundary)
        boundary[0] = SimpleNamespace(point=boundary[0].point, bulge=1.0)
        candidate = SimpleNamespace(
            frame=passage.frame,
            run_interval=passage.run_interval,
            section=SimpleNamespace(boundary=boundary),
        )
    elif case == "malformed_record":
        candidate = object()
    elif case == "vertex_mismatch":
        boundary = list(passage.section.boundary)
        boundary[0] = SimpleNamespace(point=(100.0, 100.0), bulge=boundary[0].bulge)
        candidate = SimpleNamespace(
            frame=passage.frame,
            run_interval=passage.run_interval,
            section=SimpleNamespace(boundary=boundary),
        )

    assert not _passage_matches_principal_wire(
        candidate,
        candidate_wire,
        axis,
        plane_axes,
        at,
        tol,
    )


def test_a_passage_is_an_explicit_unsupported_completeness_outcome() -> None:
    completeness = build_drawing(_hex_passage_part()).lint_summary()["quality"]["completeness"]

    assert completeness["available"] is True
    assert completeness["audited_score"] == 0.0
    assert completeness["requirements"] == 1
    assert completeness["unsupported"] == 1
    assert completeness["by_family"]["passages"] == 1
    assert "passages" not in completeness["unscored_recognized_families"]


def test_only_the_authoritative_rich_inventory_contributes_a_requirement() -> None:
    recognition = build_raw_recognition_result(_hex_passage_part())
    assert recognition.section_passages and recognition.passages
    legacy_only = replace(recognition, section_passages=())

    assert lint_passage_coverage(legacy_only) == []

    completeness = quality_components(
        recognition=legacy_only,
        features=(),
        registry=AnnotationRegistry(),
        omissions=(),
        issues=(),
        error_penalty=0.15,
        warning_penalty=0.05,
        has_asserted_content=False,
    )["completeness"]

    assert completeness["requirements"] == 0
    assert completeness["unsupported"] == 0
    assert completeness["by_family"].get("passages", 0) == 0
