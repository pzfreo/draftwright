"""Tests for the ink-level overlap check (#1321).

`annotation_overlap` compares label boxes, so anything drawn over a label which
is not itself a label goes unreported. These cover the shape of the rule that
replaces it — and two cases from GRM-03 that fix that shape, because the obvious
implementations break one or the other.
"""

import pytest

from draftwright.linting.ink_overlap import (
    MIN_REGION_MM,
    Place,
    places_where_ink_meets,
    worst_shared_place,
)


class _Vec:
    def __init__(self, x, y):
        self.X, self.Y = float(x), float(y)


class _Box:
    def __init__(self, x0, y0, x1, y1):
        self.min, self.max = _Vec(x0, y0), _Vec(x1, y1)


class _Face:
    def __init__(self, area, box):
        self.area, self._box = area, box

    def bounding_box(self):
        return self._box


class _Shape:
    def __init__(self, faces):
        self._faces = faces

    def faces(self):
        return self._faces


#: A label extent covering the whole page, so a test that is about the *shape*
#: of the shared ink is not also testing where the label happens to be.
_ANYWHERE = (-1e6, -1e6, 1e6, 1e6)


class _Annotation:
    """Intersects to a fixed set of faces, as a real sketch does."""

    def __init__(self, faces):
        self._faces = faces

    def intersect(self, other):
        # A ShapeList, and `None` when disjoint — both shapes the real API has.
        return [_Shape(self._faces)] if self._faces else None


class TestPlacesWhereInkMeets:
    def test_disjoint_shapes_yield_no_places(self):
        # `Shape.intersect` returns None, not an empty list, when disjoint.
        assert places_where_ink_meets(None) == []

    def test_faces_a_millimetre_apart_are_one_place(self):
        faces = [
            _Face(0.06, _Box(20.0, 10.0, 20.1, 11.19)),
            _Face(0.14, _Box(21.0, 10.5, 22.4, 10.6)),
            _Face(0.09, _Box(23.0, 10.5, 23.88, 10.6)),
        ]
        places = places_where_ink_meets([_Shape(faces)])
        assert len(places) == 1
        assert places[0].area == pytest.approx(0.29)

    def test_faces_far_apart_are_separate_places(self):
        places = places_where_ink_meets(
            [
                _Shape(
                    [
                        _Face(1.1, _Box(20.0, 10.0, 20.1, 21.0)),
                        _Face(1.1, _Box(60.0, 10.0, 60.1, 21.0)),
                    ]
                )
            ]
        )
        assert len(places) == 2

    def test_an_l_of_three_faces_merges_to_a_fixpoint(self):
        # Absorbing a group grows its box, and a group already passed over may
        # be adjacent to the grown one. One pass leaves this as two places.
        faces = [
            _Face(1.0, _Box(0, 0, 1, 1)),
            _Face(1.0, _Box(0, 100, 50, 101)),
            _Face(1.0, _Box(49, 0, 50, 101)),
        ]
        assert len(places_where_ink_meets([_Shape(faces)])) == 1

    def test_the_answer_does_not_depend_on_the_order_faces_arrive(self):
        import itertools

        faces = [
            _Face(1.0, _Box(0, 0, 1, 1)),
            _Face(1.0, _Box(0, 100, 50, 101)),
            _Face(1.0, _Box(49, 0, 50, 101)),
        ]
        for order in itertools.permutations(faces):
            assert len(places_where_ink_meets([_Shape(list(order))])) == 1

    def test_zero_area_faces_do_not_widen_a_place(self):
        places = places_where_ink_meets(
            [
                _Shape(
                    [
                        _Face(3.0, _Box(1, 2, 3, 4)),
                        _Face(0.0, _Box(9, 9, 9, 9)),
                    ]
                )
            ]
        )
        assert len(places) == 1
        assert places[0].box == (1.0, 2.0, 3.0, 4.0)


class TestWhatCountsAsACollision:
    def test_collinear_line_work_is_not_a_collision(self):
        # Measured on GRM-03: the *largest* shared area on the sheet, and the
        # stacked-dimension case full bounding boxes were rejected for.
        assert (
            worst_shared_place(
                _Annotation([_Face(1.1004, _Box(20.0, 10.0, 20.1, 21.0))]),
                _Annotation([]),
            )
            is None
        )

    def test_stacked_dimensions_sharing_both_extension_lines_are_not_a_collision(self):
        # Two thin places forty millimetres apart. Unioned they are 40 x 11 mm
        # with 2.2 mm² of ink and look like the worst collision on the drawing.
        assert (
            worst_shared_place(
                _Annotation(
                    [
                        _Face(1.1, _Box(20.0, 10.0, 20.1, 21.0)),
                        _Face(1.1, _Box(60.0, 10.0, 60.1, 21.0)),
                    ]
                ),
                _Annotation([]),
            )
            is None
        )

    def test_one_line_crossing_another_is_not_a_collision(self):
        # Strokes are filled faces ~0.1 mm wide, so a perpendicular crossing is
        # about 0.01 mm² — ordinary drafting, on every sheet.
        assert (
            worst_shared_place(
                _Annotation([_Face(0.01, _Box(20.0, 10.0, 20.1, 10.1))]),
                _Annotation([]),
            )
            is None
        )

    def test_a_place_with_width_and_height_is_a_collision(self):
        worst = worst_shared_place(
            _Annotation([_Face(0.9, _Box(20.0, 10.0, 22.0, 12.0))]),
            _Annotation([]),
            keep_clear=(_ANYWHERE, None),
        )
        assert worst is not None
        assert worst.width == 2.0 and worst.height == 2.0

    def test_a_place_of_faces_none_of_which_is_square_is_still_a_collision(self):
        # Measured on GRM-03: 0.10 x 0.10 and 0.10 x 0.60 two millimetres apart,
        # one place of 2.10 x 0.60. A per-face test drops it.
        faces = [
            _Face(0.15, _Box(20.0, 10.0, 20.1, 10.1)),
            _Face(0.35, _Box(22.0, 10.0, 22.1, 10.6)),
        ]
        assert not any(
            (f._box.max.X - f._box.min.X) > MIN_REGION_MM
            and (f._box.max.Y - f._box.min.Y) > MIN_REGION_MM
            for f in faces
        )
        worst = worst_shared_place(
            _Annotation(faces), _Annotation([]), keep_clear=(_ANYWHERE, None)
        )
        assert worst is not None
        assert worst.width == pytest.approx(2.1)

    def test_a_compact_but_nearly_empty_place_is_not_a_collision(self):
        assert (
            worst_shared_place(
                _Annotation([_Face(0.02, _Box(20.0, 10.0, 20.8, 10.8))]),
                _Annotation([]),
            )
            is None
        )

    def test_the_worst_place_is_the_one_returned(self):
        worst = worst_shared_place(
            _Annotation(
                [
                    _Face(0.2, _Box(5.0, 5.0, 6.0, 6.0)),
                    _Face(3.0, _Box(90.0, 90.0, 93.0, 93.0)),
                ]
            ),
            _Annotation([]),
            keep_clear=(_ANYWHERE, None),
        )
        assert worst is not None
        assert worst.box == (90.0, 90.0, 93.0, 93.0)


class TestPlace:
    def test_a_place_knows_its_own_extent(self):
        place = Place(area=1.0, box=(1.0, 2.0, 4.0, 6.0))
        assert place.width == 3.0
        assert place.height == 4.0


class TestInkMustLandOnALabel:
    """Shape alone reports correct drawings.

    An arrowhead meeting another dimension's witness line shares a region with
    real width and height — measured at 13.9 x 4.0 mm on the #916 pocket
    fixture, which the engine's own tests assert lints clean. What is not
    ordinary is ink landing on a *label*, which is the gap `annotation_overlap`
    leaves by comparing label boxes only to each other.
    """

    def test_a_square_region_clear_of_both_labels_is_not_reported(self):
        faces = [_Face(3.56, _Box(30.0, 113.0, 43.9, 117.0))]
        assert (
            worst_shared_place(
                _Annotation(faces),
                _Annotation([]),
                keep_clear=((0.0, 0.0, 10.0, 10.0), (200.0, 200.0, 210.0, 210.0)),
            )
            is None
        )

    def test_the_same_region_over_a_label_is_reported(self):
        faces = [_Face(3.56, _Box(30.0, 113.0, 43.9, 117.0))]
        worst = worst_shared_place(
            _Annotation(faces),
            _Annotation([]),
            keep_clear=((32.0, 114.0, 40.0, 116.0), None),
        )
        assert worst is not None
        assert worst.area == pytest.approx(3.56)

    def test_either_label_is_enough(self):
        faces = [_Face(3.56, _Box(30.0, 113.0, 43.9, 117.0))]
        assert (
            worst_shared_place(
                _Annotation(faces),
                _Annotation([]),
                keep_clear=(None, (32.0, 114.0, 40.0, 116.0)),
            )
            is not None
        )

    def test_no_labels_at_all_falls_back_to_shape(self):
        # An item with no label has no keep-clear box; the caller passes what it
        # has, and an empty tuple means "do not filter".
        assert (
            worst_shared_place(
                _Annotation([_Face(0.9, _Box(20.0, 10.0, 22.0, 12.0))]),
                _Annotation([]),
            )
            is not None
        )


class TestAGrazeIsNotACollision:
    """A place can have width and height, land squarely on a label, and still be
    nothing a reader would notice.

    Measured on the #915 dense fixture: an arrowhead tip clipping one character
    of `'50 × 120 × 5 DEEP'` shares 0.13 mm² across 1.2 x 1.3 mm, and the
    character stays perfectly readable. The cases worth reporting are an order
    up — 0.94 mm² for a dimension line through a label, 1.45 mm² for two
    arrowheads merged into one blob.
    """

    def test_an_arrowhead_clipping_one_character_is_not_reported(self):
        assert (
            worst_shared_place(
                _Annotation([_Face(0.13, _Box(20.0, 10.0, 21.2, 11.3))]),
                _Annotation([]),
                keep_clear=(_ANYWHERE, None),
            )
            is None
        )

    def test_a_line_crossing_a_label_is_reported(self):
        worst = worst_shared_place(
            _Annotation([_Face(0.94, _Box(20.0, 10.0, 29.0, 15.0))]),
            _Annotation([]),
            keep_clear=(_ANYWHERE, None),
        )
        assert worst is not None
        assert worst.area == pytest.approx(0.94)

    def test_the_floor_is_a_stroke_crossing_a_label(self):
        # 0.1 mm of stroke width by three millimetres of crossing. Stated as a
        # relationship so a change to either is deliberate.
        from draftwright.linting.ink_overlap import MIN_COLLISION_MM2

        assert MIN_COLLISION_MM2 == pytest.approx(0.1 * 3.0)
