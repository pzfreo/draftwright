"""#1070: exact, bounded DXF conversion for glyph and projected curves."""

from __future__ import annotations

import ezdxf
import pytest
from build123d import Box, Edge
from OCP.Geom import Geom_BSplineCurve
from OCP.GeomConvert import GeomConvert

from draftwright import build_drawing
from draftwright._core import _build_table, draft_preset
from draftwright.export import _DraftwrightDXF, _export_shape


def _reject_bulk_curve_arrays(monkeypatch):
    """Make OCCT's pathologically slow Python sequence wrappers load-bearing."""

    def rejected(*_args, **_kwargs):
        raise AssertionError("bulk OCCT curve arrays are forbidden on the DXF path")

    monkeypatch.setattr(Geom_BSplineCurve, "Poles", rejected)
    monkeypatch.setattr(Geom_BSplineCurve, "KnotSequence", rejected)


def test_empty_curve_fails_before_occt_conversion():
    with pytest.raises(ValueError, match="Edge is empty"):
        _DraftwrightDXF()._convert_bspline(Edge(), {})


def test_curve_without_location_fails_before_occt_conversion():
    class MissingLocationEdge:
        is_null = False
        location = None

        def to_splines(self):
            return self

    with pytest.raises(ValueError, match="Edge has no location"):
        _DraftwrightDXF()._convert_bspline(MissingLocationEdge(), {})


def test_trimmed_rational_bezier_exports_exactly_without_bulk_curve_arrays(monkeypatch):
    source = Edge.make_bezier(
        (0, 0),
        (2, 4),
        (5, 3),
        (7, 0),
        weights=[1.0, 0.8, 1.2, 1.0],
    ).trim(0.2, 0.75)
    converted = source.to_splines()
    adaptor = converted.geom_adaptor()
    expected = GeomConvert.SplitBSplineCurve_s(
        adaptor.Curve().Curve(),
        adaptor.FirstParameter(),
        adaptor.LastParameter(),
        _DraftwrightDXF.PARAMETRIC_TOLERANCE,
    )
    expected.Transform(converted.location.wrapped.Transformation())
    expected_points = [
        (expected.Pole(i).X(), expected.Pole(i).Y(), expected.Pole(i).Z())
        for i in range(1, expected.NbPoles() + 1)
    ]
    expected_weights = (
        [expected.Weight(i) for i in range(1, expected.NbPoles() + 1)]
        if expected.IsRational()
        else []
    )

    _reject_bulk_curve_arrays(monkeypatch)
    exporter = _DraftwrightDXF()
    exporter.add_shape(source, layer="dims")

    (spline,) = exporter._modelspace
    assert spline.dxftype() == "SPLINE"
    assert spline.dxf.degree == expected.Degree()
    assert [tuple(point) for point in spline.control_points] == pytest.approx(expected_points)
    assert list(spline.weights) == pytest.approx(expected_weights)
    expected_knots = [
        expected.Knot(index)
        for index in range(1, expected.NbKnots() + 1)
        for _ in range(expected.Multiplicity(index))
    ]
    expected_tool = ezdxf.math.BSpline(
        expected_points,
        expected.Degree() + 1,
        expected_knots,
        expected_weights or None,
    )
    assert list(spline.knots) == list(expected_tool.knots())


@pytest.mark.timeout(5)
def test_hole_table_dxf_conversion_avoids_bulk_curve_arrays(monkeypatch):
    rows = [("TAG", "SIZE", "QTY")] + [(f"A{i}", f"⌀{i + 1}.5", str(i % 4 + 1)) for i in range(20)]
    table = _build_table(rows, draft_preset(), block_cols=3)
    edge_count = len(table.edges())
    assert edge_count > 500, "fixture must retain enough glyph curves to expose #1070"

    _reject_bulk_curve_arrays(monkeypatch)
    exporter = _DraftwrightDXF()
    _export_shape(exporter, table, "dims", "annotation 'hole_table_plan'")

    entities = list(exporter._modelspace)
    assert len(entities) == edge_count
    assert {entity.dxftype() for entity in entities} == {"LINE", "SPLINE"}


def test_dxf_export_failure_names_the_registered_annotation(tmp_path, monkeypatch):
    drawing = build_drawing(Box(20, 10, 5))
    drawing.add_table([("TAG", "SIZE"), ("A", "⌀5")], name="hole_table_plan")
    table = drawing.get_annotation("hole_table_plan")
    table_edges = set(table.edges())

    real = _DraftwrightDXF.add_shape

    def fail_table(self, shape, layer=""):
        if shape is table or any(edge in table_edges for edge in shape.edges()):
            raise AssertionError("synthetic curve failure")
        return real(self, shape, layer=layer)

    monkeypatch.setattr(_DraftwrightDXF, "add_shape", fail_table)
    with pytest.raises(RuntimeError, match="annotation 'hole_table_plan'"):
        drawing.export(str(tmp_path / "attributed"), formats="dxf")


def test_exported_curve_file_remains_readable(tmp_path):
    drawing = build_drawing(Box(20, 10, 5))
    drawing.add_table([("TAG", "SIZE"), ("A", "⌀5")], name="hole_table_plan")

    path = drawing.export(str(tmp_path / "curves"), formats="dxf")["dxf"]
    entities = list(ezdxf.readfile(path).modelspace())

    assert entities
    assert any(entity.dxftype() == "SPLINE" for entity in entities)
