"""Bounded Draftwright consumer evidence for the stable inspection facade."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from build123d import Axis, Box, GeomType, Vertex
from build123d import fillet as bd_fillet

from draftwright.model.declare import fillet

ROOT = Path(__file__).parents[1]
INSPECTION_ROSTER = {
    "AnalyticSurface",
    "BevelReject",
    "SurfaceKind",
    "classify_bevel",
    "cone_rims",
    "floor_face_anchor",
    "inspect_face",
    "read_double_d_tool",
}


def test_declared_geometry_reads_use_only_the_stable_inspection_facade() -> None:
    source = (ROOT / "src/draftwright/model/declare.py").read_text()
    tree = ast.parse(source)
    recogniser_imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("b123d_recognisers")
    }

    assert "b123d_recognisers.inspection" in recogniser_imports
    assert all(not name.startswith("b123d_recognisers._") for name in recogniser_imports)
    roster_imports = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("b123d_recognisers")
        for alias in node.names
        if alias.name in INSPECTION_ROSTER
    }
    assert roster_imports == {("b123d_recognisers.inspection", name) for name in INSPECTION_ROSTER}
    assert "experimental_geometry" not in source
    assert "profiled_bores" not in source
    assert "GeometryGraph" not in source


def test_real_declared_fillet_reads_radius_axis_and_on_surface_anchor() -> None:
    part = bd_fillet(Box(60, 40, 30).edges().filter_by(Axis.Z).sort_by(Axis.X)[-1], 5)
    round_face = next(face for face in part.faces() if face.geom_type is GeomType.CYLINDER)

    feature = fillet(round_face)

    assert feature.axis == "z"
    assert feature.radius == pytest.approx(5)
    assert part.distance_to(Vertex(*feature.frame.origin)) < 0.05


def test_declared_fillet_refuses_a_planar_face_as_a_closed_consumer_error() -> None:
    with pytest.raises(ValueError, match="needs a cylindrical blend face"):
        fillet(Box(10, 20, 30).faces()[0])
