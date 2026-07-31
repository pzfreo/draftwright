"""The grid-lattice convention shared by recognition and declaration (#969).

`recognition._features` and `model.declare` are two halves of one convention: which world
directions span a pattern's plane, which lattice direction is a "column", and what `angle`
names. Disagree and every declared grid array comes back transposed — the #969 defect, invisible
on the hole path (which emits explicit ``members=``) and exposed by pocket/slot arrays, which
recompute their members from ``grid=/rows=/cols=/angle=``.

These tests exercise the seam DIRECTLY — declare a lattice, project and detect it exactly as the
pattern recognisers do, redeclare the detection — with no solid build, so the whole cross-product
of planes, pitch orders, unequal counts and rotations is affordable. That cross-product is the
point. Two branches of it were unexecuted by every fixture in the corpus:

* the angle was reduced modulo 90°, which round-trips only when the shortest lattice basis is
  the column direction (so `row_pitch < col_pitch` failed); and
* recognition projected onto its own cross-product basis, a quarter turn round from the one
  declaration lays out along, so the two errors cancelled on a z-plane grid and on nothing else.

Projecting through `_plane_uv` here rather than just taking ``(x, y)`` is what makes the second
one visible: a test that assumes the basis cannot detect that the basis is wrong.
"""

import math

import pytest

from draftwright.model.declare import _pattern_members
from draftwright.recognition._features import _plane_uv, _rect_grid

_AXIS_UNIT = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


class _Member:
    """The only thing `_rect_grid` asks of a member is where it is."""

    def __init__(self, location):
        self.location = location


def _declare(axis, rows, cols, row_pitch, col_pitch, angle, center=None):
    return _pattern_members(
        "grid",
        center or (0.0, 0.0, 0.0),
        axis,
        rows * cols,
        bcd=None,
        pitch=None,
        direction=None,
        grid=(row_pitch, col_pitch),
        rows=rows,
        cols=cols,
        angle=angle,
    )


def _detect(axis, points):
    """`(rows, cols, row_pitch, col_pitch, angle, center)`, or None if it is not a grid.

    Projects onto the plane basis the way `recognise_hole_patterns` /
    `recognise_pocket_patterns` / `recognise_slot_patterns` all do, so the basis itself is
    under test and not assumed.
    """
    u, v = _plane_uv(_AXIS_UNIT[axis])
    pts = [
        (
            sum(a * b for a, b in zip(p, u, strict=True)),
            sum(a * b for a, b in zip(p, v, strict=True)),
        )
        for p in points
    ]
    return _rect_grid(
        [_Member(p) for p in points],
        pts,
        lambda _members, rows, cols, row_pitch, col_pitch, angle, center: (
            rows,
            cols,
            row_pitch,
            col_pitch,
            angle,
            center,
        ),
    )


def _lattice(points):
    return sorted(tuple(round(c, 3) for c in p) for p in points)


#: Lattices in BOTH pitch orders, because the detector's first basis is the SHORTEST pairwise
#: vector — so `row_pitch < col_pitch` and `row_pitch > col_pitch` take different branches.
#: Counts are unequal so a rows↔cols transpose cannot hide behind a square array.
_LATTICES = [
    (2, 5, 10.0, 45.0),  # shortest basis is a ROW step — the branch #970 r1 got wrong
    (5, 2, 45.0, 10.0),  # ...and its mirror, where the shortest basis is a COLUMN step
    (3, 4, 17.0, 31.0),
    (4, 3, 31.0, 17.0),
    (2, 3, 45.0, 40.0),  # the shape of the `pocket grid` / `slot grid` emit fixtures
    (3, 3, 25.0, 25.0),  # square: the two bases tie in length, so the sort order is arbitrary
]

#: Unrotated plus three rotations that are not multiples of 45°, so a lost quarter-turn cannot
#: land back on the original lattice by symmetry.
_ANGLES = [0.0, 25.0, 37.0, 73.0]


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("angle", _ANGLES)
@pytest.mark.parametrize(("rows", "cols", "row_pitch", "col_pitch"), _LATTICES)
def test_a_detected_grid_redeclares_to_the_same_point_set(
    axis, rows, cols, row_pitch, col_pitch, angle
):
    original = _declare(axis, rows, cols, row_pitch, col_pitch, angle)
    detected = _detect(axis, original)
    assert detected is not None, "the declared lattice was not recognised as a grid at all"
    rebuilt = _declare(axis, *detected[:5], center=detected[5])
    assert _lattice(rebuilt) == _lattice(original), (
        "redeclaring the detection moved the members: recognition and declaration disagree "
        "about the lattice convention"
    )


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("angle", _ANGLES)
@pytest.mark.parametrize(("rows", "cols", "row_pitch", "col_pitch"), _LATTICES)
def test_each_count_keeps_its_own_pitch(axis, rows, cols, row_pitch, col_pitch, angle):
    # The detector is free to call the original rows "columns" — the convention is local to the
    # lattice (first basis = columns), not tied to world axes, so a rotated grid legitimately
    # comes back relabelled. What it may NOT do is separate a count from its pitch: 5 members
    # spaced 45 mm must stay 5-and-45, whichever name they end up under. A point-set check alone
    # would pass on a self-cancelling mislabel; this fails on it.
    d_rows, d_cols, d_rp, d_cp, _angle, _center = _detect(
        axis, _declare(axis, rows, cols, row_pitch, col_pitch, angle)
    )
    assert {(d_rows, d_rp), (d_cols, d_cp)} == {(rows, row_pitch), (cols, col_pitch)}


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_recognition_and_declaration_span_the_plane_the_same_way(axis):
    # The basis itself, stated once. `_plane_uv` takes a vector and `declare._plane_axes` a
    # letter; they must land on the same pair, or `angle` is measured in one frame and applied
    # in another — which is how a quarter-turn error hid behind a mod-90 fold for so long.
    from draftwright.model.declare import _plane_axes

    assert _plane_uv(_AXIS_UNIT[axis]) == _plane_axes(axis)


def test_an_oblique_axis_still_gets_an_orthonormal_plane():
    # `_plane_uv` only defers to the shared basis for an axis-aligned axis; an oblique one has
    # no declared counterpart (the IR carries an axis LETTER) and keeps the generic
    # construction. Pin that the fallback still returns a sane frame, so the guard above cannot
    # be satisfied by breaking it.
    axis = (0.0, 0.6, 0.8)
    u, v = _plane_uv(axis)
    for w in (u, v):
        assert math.isclose(math.hypot(*w), 1.0, abs_tol=1e-9)
        assert math.isclose(sum(a * b for a, b in zip(w, axis, strict=True)), 0.0, abs_tol=1e-9)
    assert math.isclose(sum(a * b for a, b in zip(u, v, strict=True)), 0.0, abs_tol=1e-9)
