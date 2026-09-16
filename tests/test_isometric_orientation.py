"""Isometric orientation regressions."""

from build123d import Box, Pos

from draftwright import build_drawing


class TestIsometricOrientation:
    """The isometric view must agree with the front and right view orientations."""

    @staticmethod
    def _iso_visible_edges(part):
        dwg = build_drawing(part, number="X")
        vis, _ = dwg.views["iso"]
        return len(vis.edges())

    def test_iso_shows_the_minus_y_face_like_the_front_view(self):
        front = Box(60, 40, 30) - Pos(15, -20, 0) * Box(8, 8, 8)
        rear = Box(60, 40, 30) - Pos(15, 20, 0) * Box(8, 8, 8)
        assert self._iso_visible_edges(front) > self._iso_visible_edges(rear)

    def test_iso_shows_the_plus_x_face_like_the_right_view(self):
        right = Box(40, 60, 30) - Pos(20, 15, 0) * Box(8, 8, 8)
        left = Box(40, 60, 30) - Pos(-20, 15, 0) * Box(8, 8, 8)
        assert self._iso_visible_edges(right) > self._iso_visible_edges(left)
