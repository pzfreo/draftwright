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
    MIN_CROSSING_MM,
    Crossing,
    crossing_length,
    length_inside,
    worst_label_crossing,
)

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
    def test_separate_segments_add_up(self):
        item = _Annotation([((0.0, 11.0), (13.0, 11.0)), ((17.0, 13.0), (40.0, 13.0))])
        assert crossing_length(item, BOX) == pytest.approx(6.0)

    def test_an_annotation_with_no_segments_crosses_nothing(self):
        assert crossing_length(_Annotation([]), BOX) == 0.0

    def test_a_missing_label_box_cannot_be_crossed(self):
        item = _Annotation([((0.0, 12.0), (30.0, 12.0))])
        assert crossing_length(item, None) == 0.0

    def test_an_item_without_a_segment_list_contributes_nothing(self):
        # Duck-typed items (ADR 0005) must not kill lint.
        assert crossing_length(object(), BOX) == 0.0

    def test_an_item_whose_segments_raise_contributes_nothing(self):
        class Hostile:
            @property
            def segments(self):
                raise RuntimeError("boom")

        assert crossing_length(Hostile(), BOX) == 0.0

    def test_an_item_whose_segments_are_not_point_pairs_contributes_nothing(self):
        # A raising *getter* is the easy case. An attribute that is present but
        # the wrong shape has to be caught by the same guard, or it escapes
        # `lint_drawing` — the silent-vs-loud failure #701 is about.
        assert crossing_length(_Annotation(["not a segment"]), BOX) == 0.0

    def test_an_item_whose_segments_raise_while_iterating_contributes_nothing(self):
        def exploding():
            yield ((0.0, 12.0), (30.0, 12.0))
            raise RuntimeError("boom")

        assert crossing_length(_Annotation(exploding()), BOX) == 0.0


class TestWorstLabelCrossing:
    def test_line_work_through_a_label_is_reported(self):
        crosser = _Annotation([((0.0, 12.0), (30.0, 12.0))], label="16.5")
        crossed = _Annotation([], label="⌀2.4 THRU")
        worst = worst_label_crossing(crosser, crossed, label_a=None, label_b=BOX)
        assert worst is not None
        assert worst.length == pytest.approx(10.0)
        assert worst.crosses_b is True

    def test_the_worse_of_the_two_directions_wins(self):
        far = (100.0, 100.0, 110.0, 104.0)
        a = _Annotation([((0.0, 12.0), (13.0, 12.0))], label="a")  # 3 mm into b
        b = _Annotation([((90.0, 102.0), (108.0, 102.0))], label="b")  # 8 mm into a
        worst = worst_label_crossing(a, b, label_a=far, label_b=BOX)
        assert worst is not None
        assert worst.length == pytest.approx(8.0)
        assert worst.crosses_b is False

    def test_a_pair_that_does_not_reach_either_label_is_not_reported(self):
        a = _Annotation([((0.0, 30.0), (30.0, 30.0))], label="a")
        assert worst_label_crossing(a, _Annotation([]), label_a=None, label_b=BOX) is None

    def test_a_label_less_annotation_cannot_be_crossed(self):
        """It must NOT fall back to the annotation's full extent.

        A dimension's full box spans its witness lines, so falling back would
        report every dimension whose line-work reaches a neighbour's arm — the
        exact false positive `annotation_overlap` compares label boxes to avoid.
        """
        crosser = _Annotation([((0.0, 12.0), (30.0, 12.0))], label="a")
        assert worst_label_crossing(crosser, _Annotation([]), label_a=None, label_b=None) is None


class TestWhatIsNotADefect:
    """The two cases the shared-ink implementation needed tuned constants to
    exclude, and which this measure excludes by construction."""

    def test_stacked_dimensions_sharing_extension_lines_score_zero(self):
        # The case `structural.py`'s own comment rejects full bounding boxes for.
        # Extension lines run away from their own text, so nothing enters the
        # label box: zero, not "a small number below a floor".
        stacked = _Annotation([((10.0, 0.0), (10.0, 9.0)), ((20.0, 0.0), (20.0, 9.0))])
        assert crossing_length(stacked, BOX) == 0.0

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
        assert crossing_length(witness, BOX) == 0.0
        # The precondition: nudge it inside and the same segment is measured, so
        # the zero above is the box's doing rather than the fixture's.
        inside = _Annotation([((0.0, 10.01), (30.0, 10.01))])
        assert crossing_length(inside, BOX) == pytest.approx(10.0)


class TestTheFloor:
    def test_the_floor_is_below_the_smallest_confirmed_defect(self):
        # 0.85 mm: the dimension line entering the neighbouring label on
        # `_issue_881_y_step_flange`, rendered at 600 dpi and confirmed.
        assert MIN_CROSSING_MM < 0.85

    def test_the_floor_still_excludes_a_corner_clip(self):
        # A hair of line in the corner of a box is not a stroke through a digit.
        clip = _Annotation([((9.9, 9.9), (10.1, 10.1))])
        assert crossing_length(clip, BOX) < MIN_CROSSING_MM

    def test_a_crossing_at_the_floor_is_reportable(self):
        assert Crossing(length=MIN_CROSSING_MM, label_box=BOX, crosses_b=True).is_reportable()

    def test_a_crossing_below_the_floor_is_not(self):
        assert not Crossing(
            length=MIN_CROSSING_MM - 1e-9, label_box=BOX, crosses_b=True
        ).is_reportable()
