"""Section-view hatch edge behavior."""

from build123d import Face, Plane

from draftwright.annotations.sections import _section_hatch_edges


class TestSectionHatchEdges:
    """Unit tests for the even-odd fill algorithm."""

    def test_rectangle_hatch_line_through_corner_fills_interior(self):
        # A 45° hatch line passing exactly through a corner must still produce a span.
        face = Face.make_rect(10, 5, Plane.XZ)
        edges = _section_hatch_edges(face, lambda x: x, lambda z: z, spacing=5.0)
        assert len(edges) > 0, "corner vertex hit must not suppress all hatch spans"
        for e in edges:
            p0, p1 = e.position_at(0), e.position_at(1)
            assert p1.X - p0.X > 0.1, f"zero-length hatch span dx={p1.X - p0.X}"

    def test_hatch_edges_are_45_degrees(self):
        face = Face.make_rect(20, 15, Plane.XZ)
        edges = _section_hatch_edges(face, lambda x: x, lambda z: z, spacing=4.5)
        assert len(edges) > 0
        for e in edges:
            p0, p1 = e.position_at(0), e.position_at(1)
            dx, dy = p1.X - p0.X, p1.Y - p0.Y
            assert abs(dy / dx - 1.0) < 0.01, f"hatch not at 45°: slope={dy / dx}"
