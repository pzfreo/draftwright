"""Topology-level rejection cases for principal inner-profile coverage."""

import pytest
from build123d import GeomType, Vector

from draftwright.linting.coverage import _supported_inner_profile


class _Bounds:
    def __init__(self, x0: float, y0: float, x1: float, y1: float):
        self.min = Vector(x0, y0, 0)
        self.max = Vector(x1, y1, 0)
        self.size = Vector(x1 - x0, y1 - y0, 0)

    def center(self):
        return (self.min + self.max) / 2


class _Edge:
    def __init__(
        self,
        kind: GeomType,
        bounds: _Bounds,
        *,
        radius: float | None = None,
        centre: tuple[float, float] | None = None,
        length: float = 1.0,
    ):
        self.geom_type = kind
        self._bounds = bounds
        self.length = length
        if radius is not None:
            self.radius = radius
        if centre is not None:
            self.arc_center = Vector(*centre)

    def bounding_box(self):
        return self._bounds


class _Wire:
    def __init__(self, edges: list[_Edge], bounds: _Bounds, *, length: float = 1.0):
        self._edges = edges
        self._bounds = bounds
        self.length = length

    def edges(self):
        return self._edges

    def bounding_box(self):
        return self._bounds


def _line(x0: float, y0: float, x1: float, y1: float) -> _Edge:
    return _Edge(GeomType.LINE, _Bounds(x0, y0, x1, y1))


def _arc(radius: float | None, centre: tuple[float, float]) -> _Edge:
    return _Edge(GeomType.CIRCLE, _Bounds(0, 0, 0, 0), radius=radius, centre=centre)


_OBROUND_LINES = [_line(2, 0, 8, 0), _line(2, 4, 8, 4)]
_OBROUND_ARCS = [_arc(2, (2, 2)), _arc(2, (8, 2))]
_ROUND_RECT_LINES = [
    _line(2, 0, 10, 0),
    _line(2, 10, 10, 10),
    _line(0, 2, 0, 8),
    _line(12, 2, 12, 8),
]


@pytest.mark.parametrize(
    "wire",
    [
        _Wire([], _Bounds(0, 0, 0, 0)),
        _Wire([_arc(None, (2, 2))], _Bounds(0, 0, 4, 4)),
        _Wire([_line(0, 0, 4, 4)], _Bounds(0, 0, 4, 4)),
        _Wire([_Edge(GeomType.ELLIPSE, _Bounds(0, 0, 6, 4))], _Bounds(0, 0, 6, 4)),
        _Wire([_line(2, 0, 8, 0), *_OBROUND_ARCS], _Bounds(0, 0, 10, 4)),
        _Wire(
            [*_OBROUND_LINES, _arc(2, (2, 2)), _arc(1.5, (8, 2))],
            _Bounds(0, 0, 10, 4),
        ),
        _Wire([*_OBROUND_LINES, _arc(None, (2, 2)), _arc(2, (8, 2))], _Bounds(0, 0, 10, 4)),
        _Wire(
            [_line(2, 1, 8, 1), _line(2, 3, 8, 3), *_OBROUND_ARCS],
            _Bounds(0, 0, 10, 4),
        ),
        _Wire(
            [*_OBROUND_LINES, _arc(2, (3, 2)), _arc(2, (8, 2))],
            _Bounds(0, 0, 10, 4),
        ),
        _Wire(
            [*_ROUND_RECT_LINES, *[_arc(5, point) for point in ((5, 5),) * 4]],
            _Bounds(0, 0, 12, 10),
        ),
        _Wire(
            [
                *_ROUND_RECT_LINES[:2],
                _line(2, 2, 2, 8),
                _line(10, 2, 10, 8),
                *[_arc(2, point) for point in ((2, 2), (2, 8), (10, 2), (10, 8))],
            ],
            _Bounds(0, 0, 12, 10),
        ),
    ],
    ids=(
        "empty",
        "circle-without-radius",
        "diagonal-linear-loop",
        "unsupported-curve",
        "mixed-edge-count",
        "unequal-arc-radii",
        "mixed-loop-arc-without-radius",
        "lines-away-from-boundary",
        "wrong-arc-centre",
        "corner-radius-consumes-short-side",
        "rounded-rectangle-lines-away-from-boundary",
    ),
)
def test_unsupported_topologies_fail_closed(wire):
    assert not _supported_inner_profile(wire, ("x", "y"), 1e-5)
