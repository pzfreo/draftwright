"""Line-work crossing a label's text (#1321, redesigned by #1332).

Pure logic — the measure is Liang-Barsky clipping over the annotations' own
segment lists, with no CAD call anywhere, which is why this module is in the
unit tier. That is the point of the redesign as much as a consequence of it: the
first implementation measured the *area of shared ink*, which depends on how the
CAD kernel carves an intersection into faces, and the two supported build123d
releases carve it differently.
"""

import pytest

from draftwright.linting.ink_overlap import (
    MAX_TIGHT_LABEL_HEIGHT,
    MIN_CROSSING_MM,
    Crossing,
    crossing_length,
    is_tight,
    label_crossings,
    length_inside,
    segments_of,
    shorter_side,
)


def measure(item, box):
    """`crossing_length` over an item, the way `structural.py` reaches it."""
    return crossing_length(segments_of(item), box)


#: A 10 x 4 mm label box, big enough that a crossing's length is obvious by eye.
BOX = (10.0, 10.0, 20.0, 14.0)


class _Annotation:
    """The only surface the measure touches: a segment list and a label.

    ``segments`` — the public, *located* property — not ``_segments_local``, which
    is the un-located build-frame cache. Every box this is measured against comes
    from ``label_bbox`` in page coordinates, so the stub must expose the same
    attribute the real code reads or these tests pass against a frame mismatch.
    """

    def __init__(self, segments, label="?"):
        self.segments = segments
        self.label = label


class TestLengthInside:
    def test_a_segment_clean_through_the_box_measures_its_width(self):
        assert length_inside(((0.0, 12.0), (30.0, 12.0)), BOX) == pytest.approx(10.0)

    def test_a_segment_that_misses_measures_nothing(self):
        assert length_inside(((0.0, 30.0), (30.0, 30.0)), BOX) == 0.0

    def test_a_segment_stopping_short_measures_only_what_is_inside(self):
        assert length_inside(((0.0, 12.0), (13.0, 12.0)), BOX) == pytest.approx(3.0)

    def test_a_segment_starting_inside_measures_from_where_it_starts(self):
        assert length_inside(((17.0, 12.0), (40.0, 12.0)), BOX) == pytest.approx(3.0)

    def test_a_segment_wholly_inside_measures_its_whole_self(self):
        assert length_inside(((12.0, 12.0), (16.0, 12.0)), BOX) == pytest.approx(4.0)

    def test_a_diagonal_measures_its_own_length_not_its_extent(self):
        # Corner to corner of the box: 10 x 4 mm, so sqrt(116), not 10 or 14.
        assert length_inside(((10.0, 10.0), (20.0, 14.0)), BOX) == pytest.approx(116**0.5)

    def test_a_zero_length_segment_measures_nothing(self):
        assert length_inside(((12.0, 12.0), (12.0, 12.0)), BOX) == 0.0

    def test_a_segment_along_the_boundary_measures_nothing(self):
        # Grazing the edge is not drawing through the text. Zero, not 10.
        assert length_inside(((0.0, 10.0), (30.0, 10.0)), BOX) == 0.0

    def test_the_measure_does_not_depend_on_direction(self):
        forward = length_inside(((0.0, 12.0), (13.0, 12.0)), BOX)
        backward = length_inside(((13.0, 12.0), (0.0, 12.0)), BOX)
        assert forward == pytest.approx(backward)


class TestCrossingLength:
    def test_the_longest_single_stroke_wins_over_the_total(self):
        """Not the sum. `MIN_CROSSING_MM` guards against corner-clipping, and
        summing defeats it: several strokes each grazing a corner would add up to
        a reported "line through the text" with nothing over a character."""
        item = _Annotation([((0.0, 11.0), (13.0, 11.0)), ((17.0, 13.0), (40.0, 13.0))])
        assert measure(item, BOX) == pytest.approx(3.0)

    def test_several_corner_clips_do_not_add_up_to_a_crossing(self):
        # Three strokes clipping a corner by 0.15 mm each: 0.45 mm summed, which
        # would clear the floor. None of them is a stroke through a digit.
        clips = _Annotation(
            [
                ((9.9, 10.05), (10.15, 10.05)),
                ((9.9, 10.10), (10.15, 10.10)),
                ((9.9, 10.15), (10.15, 10.15)),
            ]
        )
        assert measure(clips, BOX) < MIN_CROSSING_MM

    def test_a_three_coordinate_point_is_rejected_rather_than_raising(self):
        """A `build123d.Vector` yields THREE coordinates under `tuple()`.

        Such a point survives unpacking into `(start, end)` and then raises inside
        `length_inside` — out of `crossing_length`, out of `lint_drawing`, killing
        the run for every other check too.
        """
        assert measure(_Annotation([((0.0, 12.0, 0.0), (30.0, 12.0, 0.0))]), BOX) == 0.0

    def test_an_annotation_with_no_segments_crosses_nothing(self):
        assert measure(_Annotation([]), BOX) == 0.0

    def test_a_missing_label_box_cannot_be_crossed(self):
        item = _Annotation([((0.0, 12.0), (30.0, 12.0))])
        assert measure(item, None) == 0.0

    def test_an_item_without_a_segment_list_contributes_nothing(self):
        # Duck-typed items (ADR 0005) must not kill lint.
        assert measure(object(), BOX) == 0.0

    def test_an_item_whose_segments_raise_contributes_nothing(self):
        class Hostile:
            @property
            def segments(self):
                raise RuntimeError("boom")

        assert measure(Hostile(), BOX) == 0.0

    def test_an_item_whose_segments_are_not_point_pairs_contributes_nothing(self):
        # A raising *getter* is the easy case. An attribute that is present but
        # the wrong shape has to be caught by the same guard, or it escapes
        # `lint_drawing` — the silent-vs-loud failure #701 is about.
        assert measure(_Annotation(["not a segment"]), BOX) == 0.0

    def test_an_item_whose_segments_raise_while_iterating_contributes_nothing(self):
        def exploding():
            yield ((0.0, 12.0), (30.0, 12.0))
            raise RuntimeError("boom")

        assert measure(_Annotation(exploding()), BOX) == 0.0


class TestLabelCrossings:
    def test_line_work_through_a_label_is_reported(self):
        crosser = _Annotation([((0.0, 12.0), (30.0, 12.0))], label="16.5")
        crossed = _Annotation([], label="⌀2.4 THRU")
        (found,) = label_crossings(
            segments_of(crosser), segments_of(crossed), label_a=None, label_b=BOX
        )
        assert found.length == pytest.approx(10.0)
        assert found.crosses_b is True

    def test_both_directions_are_reported_worst_first(self):
        """Two obscured labels are two defects.

        Reporting only the worse of them hid one, and hid it from the #1147
        ledger too, which keys on the label being crossed.
        """
        far = (100.0, 100.0, 110.0, 104.0)
        a = _Annotation([((0.0, 12.0), (13.0, 12.0))], label="a")  # 3 mm into b
        b = _Annotation([((90.0, 102.0), (108.0, 102.0))], label="b")  # 8 mm into a
        found = label_crossings(segments_of(a), segments_of(b), label_a=far, label_b=BOX)
        assert [round(c.length, 3) for c in found] == [8.0, 3.0]
        assert [c.crosses_b for c in found] == [False, True]

    def test_a_pair_that_does_not_reach_either_label_is_not_reported(self):
        a = _Annotation([((0.0, 30.0), (30.0, 30.0))], label="a")
        assert (
            label_crossings(
                segments_of(a), segments_of(_Annotation([])), label_a=None, label_b=BOX
            )
            == []
        )

    def test_a_label_less_annotation_cannot_be_crossed(self):
        """It must NOT fall back to the annotation's full extent.

        A dimension's full box spans its witness lines, so falling back would
        report every dimension whose line-work reaches a neighbour's arm — the
        exact false positive `annotation_overlap` compares label boxes to avoid.
        """
        crosser = _Annotation([((0.0, 12.0), (30.0, 12.0))], label="a")
        assert (
            label_crossings(
                segments_of(crosser),
                segments_of(_Annotation([])),
                label_a=None,
                label_b=None,
            )
            == []
        )


class TestWhatIsNotADefect:
    """The two cases the shared-ink implementation needed tuned constants to
    exclude, and which this measure excludes by construction."""

    def test_stacked_dimensions_sharing_extension_lines_score_zero(self):
        # The case `structural.py`'s own comment rejects full bounding boxes for.
        # Extension lines run away from their own text, so nothing enters the
        # label box: zero, not "a small number below a floor".
        stacked = _Annotation([((10.0, 0.0), (10.0, 9.0)), ((20.0, 0.0), (20.0, 9.0))])
        assert measure(stacked, BOX) == 0.0

    def test_an_arrowhead_meeting_a_witness_line_scores_zero(self):
        """#916: 13.9 x 4.0 mm of shared region, 3.56 mm² of ink, and ordinary
        dimensioning. No *text* is involved, so this measure is silent without
        needing a shape test to say so.

        The witness line is placed to run *through the box's own x-span at a y the
        box does not cover* — a segment that simply missed the box by a wide
        margin would pass against an implementation that ignored the box
        entirely, which is what the first version of this test did.
        """
        witness = _Annotation([((0.0, 9.99), (30.0, 9.99))])
        assert measure(witness, BOX) == 0.0
        # The precondition: nudge it inside and the same segment is measured, so
        # the zero above is the box's doing rather than the fixture's.
        inside = _Annotation([((0.0, 10.01), (30.0, 10.01))])
        assert measure(inside, BOX) == pytest.approx(10.0)


class TestTheFloor:
    def test_the_floor_is_below_the_smallest_confirmed_defect(self):
        # 0.85 mm: the dimension line entering the neighbouring label on
        # `_issue_881_y_step_flange`, rendered at 600 dpi and confirmed.
        assert MIN_CROSSING_MM < 0.85

    def test_the_floor_still_excludes_a_corner_clip(self):
        # A hair of line in the corner of a box is not a stroke through a digit.
        clip = _Annotation([((9.9, 9.9), (10.1, 10.1))])
        assert measure(clip, BOX) < MIN_CROSSING_MM

    def test_a_crossing_at_the_floor_is_reportable(self):
        assert Crossing(length=MIN_CROSSING_MM, label_box=BOX, crosses_b=True).is_reportable()

    def test_a_crossing_below_the_floor_is_not(self):
        assert not Crossing(
            length=MIN_CROSSING_MM - 1e-9, label_box=BOX, crosses_b=True
        ).is_reportable()


class TestTheLabelBoxMustBeTight:
    """`label_bbox` is the *axis-aligned* box of a possibly-rotated label.

    On `_dense_plate`, `'2× 14.1'` — seven characters about 2.2 mm tall — has a
    10.197 x 10.197 mm **square** box, because it is drawn on a diagonal. Clipping
    against that reports a ~3 mm crossing as 10.2 mm and a 0.4 mm corner graze as
    10.2 mm, defeating `MIN_CROSSING_MM` by a factor of 25. The true rectangle is
    not recoverable from the annotation, so such a box is not crossable at all.
    """

    def test_a_tight_box_is_crossable(self):
        assert is_tight((10.0, 10.0, 20.0, 12.166), 2.166)

    def test_a_box_well_above_the_median_height_is_still_tight(self):
        # A second font size or a stacked tolerance must stay covered. On
        # `_dense_plate` the heights are 2.166 (x12), 2.67 (x9), 3.252 and 2.694;
        # none of those may be cut, only the 10.197 rotated one.
        assert is_tight((10.0, 10.0, 20.0, 13.252), 2.166)
        assert is_tight((10.0, 10.0, 20.0, 12.694), 2.166)

    def test_the_limit_is_exactly_the_documented_multiple(self):
        # Stated as a relationship so a change to either is deliberate.
        median = 2.0
        assert is_tight((0.0, 0.0, 10.0, MAX_TIGHT_LABEL_HEIGHT * median), median)
        assert not is_tight((0.0, 0.0, 10.0, MAX_TIGHT_LABEL_HEIGHT * median + 1e-6), median)

    def test_the_rotated_dense_plate_box_is_not_tight(self):
        # The measured case: 10.197 mm tall against a 2.166 mm median.
        assert not is_tight((38.298, 176.558, 48.495, 186.755), 2.166)

    def test_an_untight_label_cannot_be_crossed(self):
        rotated = (38.298, 176.558, 48.495, 186.755)
        crosser = _Annotation([((0.0, 181.0), (100.0, 181.0))], label="10")
        assert (
            label_crossings(
                segments_of(crosser),
                segments_of(_Annotation([])),
                label_a=None,
                label_b=rotated,
                median_shorter_side=2.166,
            )
            == []
        )
        # The precondition: without the tightness gate this same pair reports the
        # full width of the inflated box, so the emptiness above is the gate's
        # doing rather than the fixture's.
        assert label_crossings(
            segments_of(crosser),
            segments_of(_Annotation([])),
            label_a=None,
            label_b=rotated,
            median_shorter_side=None,
        )

    def test_a_quarter_turned_label_is_still_tight(self):
        """At 90° the box is an EXACT fit, but its height is the text's width.

        Gating on height alone made every vertical dimension label longer than
        about three characters uncrossable — silently disabling the check for a
        very common class while fixing a rarer one. Measured on
        `Box(123.456, 87.654, 45.678)`: the vertical `'45.7'` has a label box of
        2.130 x 6.891 mm against a 2.166 mm median shorter side.
        """
        vertical = (0.0, 0.0, 2.130, 6.891)
        assert shorter_side(vertical) == pytest.approx(2.130)
        assert is_tight(vertical, 2.166)

    def test_a_bound_box_is_not_a_box(self):
        """`build123d.BoundBox` defines neither `__bool__` nor `__len__`.

        `len()` on one raises `TypeError` out of `lint_drawing`, killing every
        other check — the crash the guard exists to prevent, which an earlier
        revision of that guard would have caused while claiming to stop it.
        """

        class BoundBoxLike:
            def __len__(self):
                raise TypeError("object of type 'BoundBox' has no len()")

        assert not is_tight(BoundBoxLike(), 2.166)
        assert shorter_side(BoundBoxLike()) == 0.0
        assert crossing_length((((0.0, 0.0), (10.0, 10.0)),), BoundBoxLike()) == 0.0

    def test_a_sheet_with_no_labels_treats_every_box_as_tight(self):
        # Nothing to take a median of, and nothing to cross either.
        assert is_tight((0.0, 0.0, 10.0, 99.0), None)

    def test_a_malformed_box_is_never_tight(self):
        assert not is_tight((), 2.166)
        assert not is_tight((1.0, 2.0), 2.166)
        assert not is_tight(None, 2.166)


class TestAMalformedLabelBoxCannotKillLint:
    def test_a_falsy_box_measures_nothing_rather_than_raising(self):
        """`crossing_length` guarded only `None`, so `()` raised `IndexError` out
        of `lint_drawing` — killing every other check for the whole sheet."""
        segments = (((0.0, 0.0), (10.0, 10.0)),)
        assert crossing_length(segments, ()) == 0.0

    def test_a_short_box_measures_nothing_rather_than_raising(self):
        segments = (((0.0, 0.0), (10.0, 10.0)),)
        assert crossing_length(segments, (1.0, 2.0)) == 0.0
