"""`EQ SP ON ø… BC` must state a datum, not a fitted circle (#1596).

#1595 drew a real printed part whose six ⌀2.4 holes sit in a 2×3 rectangular grid. Four of
them came back as `4× ⌀2.4 THRU EQ SP ON ø34.4 BC`, the remaining two as a separate
`2× ⌀2.4 THRU`. The circle is centred at (0, −20.11) — mid-air, concentric with nothing.

The fit is not a near miss that a tolerance could catch. **Any three non-collinear points are
concyclic, and any four corners of a rectangle are**, so a circle through three or four holes
always fits whatever their arrangement: the residual is zero by construction. Corroboration
has to come from somewhere other than the fit.
"""

import math

import pytest
from build123d import Box, BuildPart, Cylinder, Hole, Locations, PolarLocations

from draftwright import build_drawing
from draftwright.builder import detect_part_model
from draftwright.model.ir import PatternFeature

# The #1595 arrangement, reduced to the smallest part that reproduces it. Row spacing is
# UNEVEN, which is what stops the grid recogniser claiming all six and leaves the four
# concyclic ones to the bolt-circle fitter.
_UNEVEN_ROWS = (-32.30, -7.92, 32.30)
_EVEN_ROWS = (-24.0, 0.0, 24.0)
_COLUMNS = (-12.15, 12.15)


def _grid_plate(rows, columns=_COLUMNS, thickness=6.0):
    with BuildPart() as part:
        Box(60, 90, thickness)
        with Locations(*[(x, y, 0) for x in columns for y in rows]):
            Hole(1.2, depth=thickness)
    return part.part


def _flange(holes, *, central_feature=True):
    """A round flange: *holes* on a ⌀60 circle, optionally with a boss and bore at the centre."""
    with BuildPart() as part:
        Cylinder(40, 8)
        if central_feature:
            Cylinder(12, 16)
            Hole(6, depth=16)
        with PolarLocations(30, holes):
            Hole(3, depth=8)
    return part.part


def _patterns(part):
    return [f for f in detect_part_model(part).features if isinstance(f, PatternFeature)]


def _bolt_circles(part):
    return [f for f in _patterns(part) if f.pattern == "bolt_circle"]


# --- the defect ------------------------------------------------------------------------


def test_precondition_the_recogniser_really_does_propose_this_circle():
    """Without this the tests below pass against unfixed code: the whole question is what
    draftwright does with a `BoltCircle` the recogniser legitimately found."""
    from quiddity import analyse_cylinders, recognise_hole_patterns, recognise_holes

    part = _grid_plate(_UNEVEN_ROWS)
    holes = recognise_holes(part, cyls=analyse_cylinders(part))
    (found,) = recognise_hole_patterns(holes)
    assert type(found).__name__ == "BoltCircle"
    assert len(found.holes) == 4
    assert found.diameter == pytest.approx(34.42, abs=0.01)


def test_the_fitted_circle_is_exactly_the_rectangles_circumcircle():
    """Why the fit is not evidence, as arithmetic rather than assertion.

    Four corners at x = ±12.15, y = −32.30 and −7.92 have one circumcircle, and it is the
    one the engine reports. A fit that cannot fail cannot corroborate anything.
    """
    from quiddity import analyse_cylinders, recognise_hole_patterns, recognise_holes

    part = _grid_plate(_UNEVEN_ROWS)
    (found,) = recognise_hole_patterns(recognise_holes(part, cyls=analyse_cylinders(part)))

    centre_y = (_UNEVEN_ROWS[0] + _UNEVEN_ROWS[1]) / 2
    diameter = 2 * math.hypot(_COLUMNS[1], (_UNEVEN_ROWS[1] - _UNEVEN_ROWS[0]) / 2)
    assert found.center[1] == pytest.approx(centre_y, abs=0.01)
    assert found.diameter == pytest.approx(diameter, abs=0.01)


def test_an_uncorroborated_circle_is_not_carried_into_the_ir():
    assert _bolt_circles(_grid_plate(_UNEVEN_ROWS)) == []


def test_the_members_are_still_drawn_counted_and_located():
    """Refusing the datum must not lose the holes.

    They fall through to the un-patterned grouping, which also repairs the second half of
    #1595's A2: the six stop being split into an unrelated 4 and 2.
    """
    drawing = build_drawing(_grid_plate(_UNEVEN_ROWS), title="T", number="N")
    labels = [str(o.label) for _n, o in drawing.iter_annotations() if getattr(o, "label", None)]
    assert not [label for label in labels if "BC" in label]
    assert "6× ⌀2.4 THRU" in labels


def test_even_spacing_was_never_this_defect():
    """The contrast that explains it: with even rows all six ARE a grid, and the bolt-circle
    fitter never sees four holes on their own. The defect needs the uneven case."""
    (grid,) = _patterns(_grid_plate(_EVEN_ROWS))
    assert grid.pattern == "grid"
    assert grid.count == 6


# --- what must keep working ------------------------------------------------------------


def test_four_holes_around_a_real_centre_keep_their_bolt_circle():
    """A four-bolt round flange is real and common, so member count alone would refuse as
    much good work as bad. What separates it from the grid is a physical feature at the
    centre that a machinist can indicate off."""
    (pattern,) = _bolt_circles(_flange(4))
    assert pattern.count == 4
    assert pattern.bcd == pytest.approx(60.0)


def test_a_round_body_concentric_with_the_holes_is_itself_corroboration():
    """The same flange with no spigot and no bore still keeps its bolt circle, and should.

    Its ⌀80 body is recognised as a boss on the pattern axis at the pattern centre — a
    physical circular feature a machinist can indicate off. This was written expecting a
    refusal; the code was right and the expectation was wrong, which is worth keeping as a
    test because "the part itself is the concentric feature" is the commonest flange there
    is.
    """
    (pattern,) = _bolt_circles(_flange(4, central_feature=False))
    assert pattern.count == 4


def test_four_holes_at_ninety_degrees_on_a_square_plate_keep_theirs():
    """Equal angular spacing that no lattice explains is evidence in its own right.

    Four holes at 0/90/180/270 on a circle could have been at unequal angles and were not,
    and they are not the intersections of two rows and two columns. Nothing round is needed
    — this is an ordinary four-bolt pattern and refusing it would cost real drawings.
    """
    with BuildPart() as part:
        Box(90, 90, 8)
        with PolarLocations(30, 4):
            Hole(3, depth=8)

    (pattern,) = _bolt_circles(part.part)
    assert pattern.count == 4


def test_the_near_square_rectangle_is_refused_even_though_it_passes_equal_spacing():
    """The subtle case, and the reason the lattice test exists.

    #1595's four holes form a 24.30 x 24.38 rectangle. Its circumcircle fits exactly (all
    rectangles' do) AND its corners sit 90.19 / 89.81 degrees apart — inside any sane `EQ SP`
    tolerance. So every test the callout could apply passes, and the part still has no bolt
    circle. Only "a lattice already explains these holes" separates it from the case above.
    """
    from quiddity import analyse_cylinders, recognise_hole_patterns, recognise_holes

    from draftwright.model.detect import _equally_spaced_around, _explicable_as_a_grid

    part = _grid_plate(_UNEVEN_ROWS)
    (found,) = recognise_hole_patterns(recognise_holes(part, cyls=analyse_cylinders(part)))

    assert _equally_spaced_around(found.holes, "z", found.center), (
        "precondition: this arrangement must PASS equal spacing, or the lattice test below "
        "is not what refuses it"
    )
    assert _explicable_as_a_grid(found.holes, "z")
    assert _bolt_circles(part) == []


def test_five_holes_are_self_evident():
    """Five concyclic holes could have failed to fit and did not, so the fit IS evidence —
    no corroborating feature needed."""
    (pattern,) = _bolt_circles(_flange(5, central_feature=False))
    assert pattern.count == 5
    assert pattern.bcd == pytest.approx(60.0)


class TestTheConcentricityPredicate:
    """`_corroborates_bolt_circle` directly, because two of its three conditions cannot be
    reached by building a part.

    A cross-axis boss placed so its in-plane projection lands on the pattern centre is
    awkward to construct in build123d and would prove nothing about real geometry anyway —
    the recogniser records such a boss at one end, far from the centre in projection. The
    predicate is pure, so it is exercised as one.
    """

    @staticmethod
    def _candidate(axis, location):
        from types import SimpleNamespace

        return SimpleNamespace(axis=axis, location=location)

    def test_a_feature_on_the_pattern_axis_at_the_centre_corroborates(self):
        from draftwright.model.detect import _corroborates_bolt_circle

        assert _corroborates_bolt_circle(
            self._candidate((0.0, 0.0, 1.0), (0.0, 0.0, 25.0)), "z", (0.0, 0.0, 4.0)
        )

    def test_the_axis_coordinate_is_ignored(self):
        """A concentric boss is concentric whether it sits above or below the holes."""
        from draftwright.model.detect import _corroborates_bolt_circle

        assert _corroborates_bolt_circle(
            self._candidate((0.0, 0.0, -1.0), (0.0, 0.0, -900.0)), "z", (0.0, 0.0, 4.0)
        )

    def test_a_feature_on_a_DIFFERENT_axis_does_not(self):
        """The check that no fixture reaches: a boss pointing along X says nothing about a
        bolt circle drilled along Z, however its centre happens to project."""
        from draftwright.model.detect import _corroborates_bolt_circle

        assert not _corroborates_bolt_circle(
            self._candidate((1.0, 0.0, 0.0), (0.0, 0.0, 4.0)), "z", (0.0, 0.0, 4.0)
        )

    def test_a_feature_merely_nearby_does_not(self):
        """The claim is that a machinist may work from this circle. 2 mm out is not that."""
        from draftwright.model.detect import _corroborates_bolt_circle

        assert not _corroborates_bolt_circle(
            self._candidate((0.0, 0.0, 1.0), (2.0, 0.0, 4.0)), "z", (0.0, 0.0, 4.0)
        )
