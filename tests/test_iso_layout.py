"""Isometric placement and empty-rectangle layout behavior."""

import pytest


class TestIsoEmptyRect:
    def test_largest_empty_rect_fallback_when_fully_covered(self):
        # When obstacles leave no genuine gap, _largest_empty_rect returns the
        # whole drawable (documented fallback) — the mechanism iso_valid checks.
        from draftwright._core import _largest_empty_rect

        drawable = (10.0, 10.0, 90.0, 90.0)
        assert _largest_empty_rect(drawable, [drawable]) == drawable

    def test_layout_geometry_iso_valid_false_when_no_gap(self):
        # A part that fills the sheet leaves no empty rectangle for the iso, so
        # the fallback returns the drawable (overlapping the view obstacles) and
        # iso_valid is False — the flag _fits uses to reject such a layout.
        from draftwright.compose import _layout_geometry

        g = _layout_geometry(200, 150, 150, 2.0, 297.0, 210.0, 120.0, None)
        assert g.iso_valid is False

    def test_layout_geometry_iso_valid_true_for_normal_part(self):
        from draftwright.compose import _layout_geometry

        g = _layout_geometry(20, 20, 20, 1.0, 297.0, 210.0, 120.0, None)
        assert g.iso_valid is True

    def test_target_aspect_can_choose_a_wide_short_gap_over_the_largest_square(self):
        from draftwright._core import _largest_empty_rect

        drawable = (0.0, 0.0, 100.0, 100.0)
        obstacles = [(40.0, 0.0, 100.0, 70.0)]

        assert _largest_empty_rect(drawable, obstacles) == (0.0, 0.0, 40.0, 70.0)
        assert _largest_empty_rect(drawable, obstacles, target_size=(60.0, 20.0)) == (
            0.0,
            70.0,
            100.0,
            100.0,
        )

    def test_degenerate_obstacle_still_splits_a_gap(self):
        from draftwright._core import _largest_empty_rect

        assert _largest_empty_rect(
            (0.0, 0.0, 10.0, 10.0),
            [(5.0, 5.0, 5.0, 5.0)],
        ) == (0.0, 0.0, 5.0, 5.0)

    def test_annotation_sized_obstacle_set_stays_bounded(self):
        """Detail placement must not turn ordinary occupancy into a quartic search."""
        from draftwright._core import _largest_empty_rect

        drawable = (0.0, 0.0, 1000.0, 1000.0)
        boxes = []
        for i in range(300):
            x = 20.0 + i * 1.6
            y = 20.0 + ((i * 137) % 600) * 1.5
            boxes.append((x, y, x + 0.7, y + 0.7))

        class BoundedObstacleReads:
            """Reject repeated obstacle rescans deterministically."""

            def __init__(self, values, maximum_reads):
                self.values = values
                self.maximum_reads = maximum_reads
                self.reads = 0

            def __iter__(self):
                for value in self.values:
                    self.reads += 1
                    if self.reads > self.maximum_reads:
                        raise AssertionError(
                            "largest-empty-rectangle search repeatedly rescanned its obstacles"
                        )
                    yield value

        obstacles = BoundedObstacleReads(boxes, maximum_reads=3 * len(boxes))
        result = _largest_empty_rect(
            drawable,
            obstacles,
            target_size=(1635.0, 2752.0),
            warn=False,
        )

        x0, y0, x1, y1 = result
        assert x0 < x1 and y0 < y1
        assert obstacles.reads <= obstacles.maximum_reads
        with pytest.raises(AssertionError, match="repeatedly rescanned"):
            for _ in range(4):
                list(obstacles)
        assert not any(
            x0 < ox1 and ox0 < x1 and y0 < oy1 and oy0 < y1 for ox0, oy0, ox1, oy1 in boxes
        )

    @pytest.mark.parametrize(
        "target_size",
        [(float("nan"), 1.0), (0.0, 1.0), (1.0, float("nan")), (1.0, 0.0)],
    )
    def test_target_aspect_requires_two_finite_positive_dimensions(self, target_size):
        from draftwright._core import _largest_empty_rect

        with pytest.raises(ValueError, match="finite positive dimensions"):
            _largest_empty_rect((0.0, 0.0, 10.0, 10.0), [], target_size=target_size)
