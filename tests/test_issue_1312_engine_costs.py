"""Measured small engine costs from #1312."""

from draftwright._geometry import _convex_polygons_overlap


class _AabbOnlyNumber(float):
    """Fail if a touching-box rejection leaks through to the expensive SAT."""

    def __sub__(self, other):
        raise AssertionError("touching AABBs must return before building SAT axes")


def test_convex_overlap_broad_phase_rejects_touching_aabbs_before_the_sat():
    number = _AabbOnlyNumber
    left = ((number(0), number(0)), (number(1), number(0)), (number(0), number(1)))
    right = ((number(1), number(0)), (number(2), number(0)), (number(1), number(1)))

    assert not _convex_polygons_overlap(left, right)


def test_convex_overlap_broad_phase_keeps_real_overlap_for_the_sat():
    left = ((0.0, 0.0), (2.0, 0.0), (0.0, 2.0))
    right = ((0.5, 0.5), (2.5, 0.5), (0.5, 2.5))

    assert _convex_polygons_overlap(left, right)
