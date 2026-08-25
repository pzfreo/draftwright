"""Line-work crossing a label's text (#1321, redesigned by #1332).

Pure logic — the measure is Liang-Barsky clipping over the annotations' own
segment lists, with no CAD call anywhere, which is why this module is in the
unit tier. That is the point of the redesign as much as a consequence of it: the
first implementation measured the *area of shared ink*, which depends on how the
CAD kernel carves an intersection into faces, and the two supported build123d
releases carve it differently.
"""

import contextlib
import logging
import warnings

import pytest

from draftwright.linting.ink_overlap import (
    DIAGONAL_DIM_TOLERANCE_MM,
    MIN_CROSSING_MM,
    Crossing,
    LabelRegion,
    crossable_region,
    crossing_length,
    is_tight,
    label_crossings,
    length_inside,
    segments_of,
    shorter_side,
    warn_if_untight,
)

_LOGGER = "draftwright.linting.ink_overlap"


@contextlib.contextmanager
def _logs(fragment):
    """Assert the helpers report *fragment* through the logger, not a warning."""

    records: list[logging.LogRecord] = []

    class _Catch(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger(_LOGGER)
    handler = _Catch()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    assert any(fragment in record.getMessage() for record in records), (
        f"expected a log containing {fragment!r}, saw {[r.getMessage() for r in records]}"
    )


@contextlib.contextmanager
def _logs_any():
    """Collect every record the module logs, for counting rather than matching."""
    records: list[logging.LogRecord] = []

    class _Catch(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger(_LOGGER)
    handler = _Catch()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


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

    def test_a_segment_along_the_boundary_follows_the_shared_convention(self):
        """`_geometry._segment_clip_extent` documents "inclusive at the boundary
        (a touch is a hit)", and `label_centerline_overlap` already measures by it.

        An earlier revision of this module clipped with its own Liang-Barsky and
        answered 0.0 here, so a stroke lying exactly on a label's edge was a hit
        for one lint check and a miss for another in the same module. One
        predicate; the shared convention wins.
        """
        from draftwright._geometry import _segment_clip_extent

        segment, box = ((0.0, 10.0), (30.0, 10.0)), BOX
        assert length_inside(segment, box) == pytest.approx(10.0)
        clipped = _segment_clip_extent(segment[0], segment[1], box, 0.0)
        assert clipped is not None and clipped[2] - clipped[0] == pytest.approx(10.0)

    def test_the_measure_is_the_shared_clipper(self):
        """Not a second implementation. Any disagreement here is the bug this
        derivation exists to make impossible."""
        import math

        from draftwright._geometry import _segment_clip_extent

        for segment in (
            ((0.0, 12.0), (30.0, 12.0)),
            ((0.0, 6.0), (30.0, 18.0)),
            ((15.0, 0.0), (15.0, 30.0)),
            ((0.0, 30.0), (30.0, 30.0)),
            ((10.0, 10.0), (20.0, 14.0)),
        ):
            clipped = _segment_clip_extent(segment[0], segment[1], BOX, 0.0)
            expected = (
                0.0
                if clipped is None
                else math.hypot(clipped[2] - clipped[0], clipped[3] - clipped[1])
            )
            assert length_inside(segment, BOX) == pytest.approx(expected), segment

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

    def test_connected_segments_inside_the_label_are_one_stroke(self):
        """A renderer may split one continuous path at an ordinary vertex.

        Each piece is below the floor, but the connected 0.8 mm stroke is not.
        The shared endpoint lies inside the label, which distinguishes this from
        independent corner grazes.
        """
        box = (0.0, 0.0, 1.0, 1.0)
        connected = _Annotation(
            [
                ((-1.0, 0.5), (0.4, 0.5)),
                ((0.4, 0.5), (0.8, 0.5)),
            ]
        )
        assert measure(connected, box) == pytest.approx(0.8)
        assert measure(connected, box) >= MIN_CROSSING_MM

    def test_segments_connected_only_outside_the_label_do_not_add(self):
        box = (0.0, 0.0, 1.0, 1.0)
        outside_joint = _Annotation(
            [
                ((-1.0, 0.1), (0.4, 0.1)),
                ((-1.0, 0.1), (0.4, 0.2)),
            ]
        )
        assert measure(outside_joint, box) < MIN_CROSSING_MM

    def test_a_three_coordinate_point_is_rejected_rather_than_raising(self):
        """A `build123d.Vector` yields THREE coordinates under `tuple()`.

        Such a point survives unpacking into `(start, end)` and then raises inside
        `length_inside` — out of `crossing_length`, out of `lint_drawing`, killing
        the run for every other check too.
        """
        with _logs("not pairs of 2D numeric points"):
            assert measure(_Annotation([((0.0, 12.0, 0.0), (30.0, 12.0, 0.0))]), BOX) == 0.0

    def test_an_annotation_with_no_segments_crosses_nothing(self):
        assert measure(_Annotation([]), BOX) == 0.0

    def test_a_missing_label_box_cannot_be_crossed(self):
        item = _Annotation([((0.0, 12.0), (30.0, 12.0))])
        assert measure(item, None) == 0.0

    def test_an_item_without_a_segment_list_contributes_nothing(self):
        # Duck-typed items (ADR 0005) must not kill lint.
        assert measure(object(), BOX) == 0.0

    def test_an_item_whose_segments_raise_contributes_nothing_and_says_so(self):
        class Hostile:
            @property
            def segments(self):
                raise RuntimeError("boom")

        # Loud, not silent. Excluding an item's whole line-work without a word is
        # the #701 failure the neighbouring `_label_bbox` logs to avoid: if one
        # annotation type's `segments` started raising, that item would vanish
        # from the check with no signal at all.
        with _logs("reading segments raised RuntimeError"):
            assert measure(Hostile(), BOX) == 0.0

    def test_an_item_whose_segments_are_not_point_pairs_contributes_nothing(self):
        # A raising *getter* is the easy case. An attribute that is present but
        # the wrong shape has to be caught by the same guard, or it escapes
        # `lint_drawing` — the silent-vs-loud failure #701 is about.
        with _logs("reading segments raised"):
            assert measure(_Annotation(["not a segment"]), BOX) == 0.0

    def test_an_item_whose_segments_raise_while_iterating_contributes_nothing(self):
        def exploding():
            yield ((0.0, 12.0), (30.0, 12.0))
            raise RuntimeError("boom")

        with _logs("reading segments raised RuntimeError"):
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
        # The extension lines run PAST the label box's x-span, stopping just short
        # of its lower edge — the real geometry. A stub that missed the box
        # entirely would pass against an implementation that ignored the box, which
        # is what this fixture used to do (it stopped 1 mm clear at y=9.0).
        stacked = _Annotation([((12.0, 0.0), (12.0, 9.99)), ((18.0, 0.0), (18.0, 9.99))])
        assert measure(stacked, BOX) == 0.0
        # The precondition: extend them 0.02 mm past the box's lower edge and the
        # same segments ARE measured, so the zero above is the box's doing rather
        # than the fixture's. (x=12/18 sit inside the box's x-span; a stub on the
        # x-edge would read zero for the boundary rule's reasons, not these.)
        through = _Annotation([((12.0, 0.0), (12.0, 10.01)), ((18.0, 0.0), (18.0, 10.01))])
        assert measure(through, BOX) > 0.0

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


class _DiagonalDim:
    """A dimension whose own line runs on a diagonal, so its label is rotated."""

    measured_length = 10.0
    segments = (((0.0, 0.0), (10.0, 10.0)),)


class _AxisAlignedDim:
    measured_length = 10.0
    segments = (((0.0, 0.0), (10.0, 0.0)),)


class _VerticalDim:
    measured_length = 10.0
    segments = (((0.0, 0.0), (0.0, 10.0)),)


class TestRotationIsAskedOfTheAnnotation:
    """`label_bbox` is axis-aligned, so a rotated label's box is the bounding box
    of a rotated rectangle — much larger than the glyphs, and clipping against it
    reports a stroke as crossing far more text than it does.

    Five revisions tried to infer that from the box's shape or size, and each was
    falsified by a real case: a height rule made every vertical label uncrossable;
    a 6 mm size cut excluded every GD&T control frame (6.150 mm); testing size
    first let a 4.53 mm square rotated label through; testing it last excluded
    frames again; and an aspect rule excluded every DATUM SYMBOL, whose box is
    `2.0 * font_size` **square by construction** — 5.000 x 5.000 mm, aspect 1.000,
    a genuine tight frame around a letter.

    A box cannot say whether it is rotated. A dimension's public segment
    metadata can, and its exact label polygon makes the rotated region measurable.
    """

    def test_a_diagonal_dimension_label_is_not_measurable(self):
        assert not is_tight((10.0, 10.0, 20.0, 20.0), _DiagonalDim())

    def test_a_diagonal_dimension_with_a_polygon_is_measurable(self):
        item = _DiagonalDim()
        item.label_polygon = ((12.0, 10.0), (20.0, 18.0), (18.0, 20.0), (10.0, 12.0))
        region = crossable_region((10.0, 10.0, 20.0, 20.0), item=item)
        assert isinstance(region, LabelRegion)
        assert region.polygon == item.label_polygon

    def test_an_axis_aligned_dimension_label_is_measurable(self):
        assert is_tight((10.0, 10.0, 20.0, 12.2), _AxisAlignedDim())

    def test_a_vertical_dimension_label_is_measurable(self):
        """At 90° the box is an EXACT fit; its height is the text's width."""
        assert is_tight((0.0, 0.0, 2.130, 6.891), _VerticalDim())

    def test_a_square_datum_symbol_is_measurable(self):
        """`DatumFeature.label_bbox` is `2.0 * font_size` square by construction —
        measured at exactly 5.000 x 5.000 mm. No shape rule can tell it from a
        rotated box; its annotation can."""
        assert is_tight((-2.5, 5.15, 2.5, 10.15), object())

    def test_a_control_frame_is_measurable_at_any_size(self):
        assert is_tight((0.0, 0.0, 25.161, 6.150), object())
        assert is_tight((0.0, 0.0, 25.0, 12.3), object())

    def test_an_item_with_no_dimension_metadata_is_measurable(self):
        assert is_tight((10.0, 10.0, 20.0, 12.2), None)
        assert is_tight((10.0, 10.0, 20.0, 12.2), object())

    def test_a_hostile_polygon_does_not_kill_the_check(self):
        class Hostile:
            @property
            def label_polygon(self):
                raise RuntimeError("boom")

        assert is_tight((10.0, 10.0, 20.0, 12.2), Hostile())

    def test_the_tolerance_is_the_repair_pass_figure(self):
        """Shared with `repair.reconcile_witness_labels`, which asks the same
        orientation question with the same tolerance."""
        assert DIAGONAL_DIM_TOLERANCE_MM == pytest.approx(0.1)
        just_off = type(
            "D",
            (),
            {"measured_length": 10.0, "segments": (((0.0, 0.0), (10.0, 0.09)),)},
        )()
        just_on = type(
            "D",
            (),
            {"measured_length": 10.0, "segments": (((0.0, 0.0), (10.0, 0.11)),)},
        )()
        assert is_tight((10.0, 10.0, 20.0, 12.2), just_off)
        assert not is_tight((10.0, 10.0, 20.0, 12.2), just_on)

    def test_an_untight_label_cannot_be_crossed(self):
        rotated = (38.298, 176.558, 48.495, 186.755)
        crosser = _Annotation([((0.0, 181.0), (100.0, 181.0))], label="10")
        assert (
            label_crossings(
                segments_of(crosser),
                segments_of(_Annotation([])),
                label_a=None,
                label_b=None,
            )
            == []
        )
        # The precondition: the same stroke through a measurable box IS reported,
        # so the emptiness above is the exclusion's doing, not the fixture's.
        assert label_crossings(
            segments_of(crosser),
            segments_of(_Annotation([])),
            label_a=None,
            label_b=rotated,
        )

    def test_a_bound_box_is_not_a_box(self):
        class BoundBoxLike:
            def __len__(self):
                raise TypeError("object of type 'BoundBox' has no len()")

        assert not is_tight(BoundBoxLike())
        assert shorter_side(BoundBoxLike()) == 0.0
        assert crossing_length((((0.0, 0.0), (10.0, 10.0)),), BoundBoxLike()) == 0.0

    def test_a_malformed_box_is_never_tight(self):
        assert not is_tight(())
        assert not is_tight((1.0, 2.0))
        assert not is_tight(None)


class TestAMalformedLabelBoxCannotKillLint:
    def test_a_falsy_box_measures_nothing_rather_than_raising(self):
        """`crossing_length` guarded only `None`, so `()` raised `IndexError` out
        of `lint_drawing` — killing every other check for the whole sheet."""
        segments = (((0.0, 0.0), (10.0, 10.0)),)
        assert crossing_length(segments, ()) == 0.0

    def test_a_short_box_measures_nothing_rather_than_raising(self):
        segments = (((0.0, 0.0), (10.0, 10.0)),)
        assert crossing_length(segments, (1.0, 2.0)) == 0.0


class TestTheExclusionIsLoud:
    """An untight box makes its label uncrossable, so the reader gets no finding
    for line-work through it. That must never be silent (#701)."""

    def test_an_untight_box_warns(self):
        with _logs("no exact label_polygon"):
            assert (
                warn_if_untight((38.298, 176.558, 48.495, 186.755), item=_DiagonalDim()) is False
            )

    def test_a_tight_box_is_quiet(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert warn_if_untight((10.0, 10.0, 20.0, 12.166), item=_AxisAlignedDim()) is True

    def test_a_control_frame_is_quiet(self):
        # Measured: m_gdt0 6.150 x 12.255, m_gdt1 25.161 x 6.150. Over the size
        # cut, but long rather than square, so they stay crossable.
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert warn_if_untight((0.0, 0.0, 6.150, 12.255)) is True
            assert warn_if_untight((0.0, 0.0, 25.161, 6.150)) is True

    def test_a_malformed_box_is_untight_without_warning(self):
        # Nothing useful to say about a box that is not a box; `segments_of` and
        # `crossing_length` already report their own refusals.
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert warn_if_untight(()) is False


class TestTheReportIsLoggedNotWarned:
    """Logged at DEBUG, not warned — and that is a correctness constraint.

    These helpers run inside `lint_drawing`'s deliberately unguarded region
    ("#701: the check body runs unguarded"). A `warnings.warn` there propagates
    out under a warnings-as-errors configuration and kills every other check on
    the sheet — the #701 failure inverted. `_label_bbox` logs for the same
    condition twenty lines away, and a logger is silenceable by name.

    DEBUG rather than WARNING because `WARNING` with no logging configured reaches
    stderr through Python's `lastResort` handler: an ordinary supported part
    printed this on every build. The condition is permanent and not fixable by the
    caller.
    """

    def test_an_untight_box_is_reported(self):
        with _logs("no exact label_polygon"):
            assert (
                warn_if_untight((38.298, 176.558, 48.495, 186.755), item=_DiagonalDim()) is False
            )

    def test_a_tight_box_is_quiet(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            assert warn_if_untight((10.0, 10.0, 20.0, 12.166), item=_AxisAlignedDim()) is True
        assert caplog.records == []

    def test_a_control_frame_is_quiet(self, caplog):
        # Measured: m_gdt0 6.150 x 12.255, m_gdt1 25.161 x 6.150. Over the size
        # cut, but long rather than square, so they stay crossable.
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            assert warn_if_untight((0.0, 0.0, 6.150, 12.255)) is True
            assert warn_if_untight((0.0, 0.0, 25.161, 6.150)) is True
        assert caplog.records == []

    def test_a_malformed_box_is_reported_too(self):
        """The one exclusion path that used to say nothing.

        A `label_bbox` that is present but not a box drops the annotation from the
        check, and returning silently there is the #701 shape every sibling path
        goes out of its way to avoid. `_is_box`'s own docstring names the case it
        anticipates — a duck-typed item handing back a `BoundBox`.
        """
        with _logs("label_bbox is not a box"):
            assert warn_if_untight((), item=object()) is False

    def test_a_repeated_message_reports_once_per_run(self, caplog):
        """`_label_bbox` memoises the same way (#711): several checks read the
        same item, and `repair()` lints twice per pass for up to three passes."""
        seen: set[str] = set()
        box = (38.298, 176.558, 48.495, 186.755)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            warn_if_untight(box, seen, _DiagonalDim())
            warn_if_untight(box, seen, _DiagonalDim())
            warn_if_untight(box, seen, _DiagonalDim())
        assert len(caplog.records) == 1

    def test_without_a_memo_it_reports_every_time(self, caplog):
        box = (38.298, 176.558, 48.495, 186.755)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            warn_if_untight(box, None, _DiagonalDim())
            warn_if_untight(box, None, _DiagonalDim())
        assert len(caplog.records) == 2

    def test_lint_still_returns_under_warnings_as_errors(self):
        """The whole point. A raised warning here would take the sheet's other
        checks with it."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert measure(_Annotation([((0.0, 12.0, 0.0), (30.0, 12.0, 0.0))]), BOX) == 0.0
            assert (
                warn_if_untight((38.298, 176.558, 48.495, 186.755), item=_DiagonalDim()) is False
            )


class TestAPointMustBeTwoNumbers:
    """Length alone was not enough, and the guard's own comment said it was.

    `("ab", "cd")` unpacks to two 2-tuples — `tuple("ab")` is `('a', 'b')` — so a
    length-only check kept it, and `length_inside` then evaluated `'c' - 'a'`, a
    `TypeError` outside any handler, killing lint for every other check on the
    sheet. The pre-existing test used `["not a segment"]`, which fails at the
    unpack and so never reached this path.
    """

    def test_string_coordinates_are_rejected_rather_than_raising(self):
        with _logs("2D numeric points"):
            assert measure(_Annotation([("ab", "cd")]), BOX) == 0.0

    def test_none_coordinates_are_rejected_rather_than_raising(self):
        with _logs("2D numeric points"):
            assert measure(_Annotation([((None, None), (1.0, 1.0))]), BOX) == 0.0

    def test_ordinary_ints_are_accepted(self):
        # The guard must not reject a perfectly good integer coordinate.
        assert measure(_Annotation([((0, 12), (30, 12))]), BOX) == pytest.approx(10.0)


class TestABoxMustBeFourNumbers:
    """`Sequence` accepted `str`, which is the same hole closed for `segments`.

    A four-character `label_bbox` passed `isinstance(value, Sequence) and
    len(value) == 4`, and `shorter_side` then evaluated `'n' - 'n'` — a
    `TypeError` out of `lint_drawing`, killing every other check on the sheet, in
    the guard whose docstring claimed to prevent exactly that.
    """

    def test_a_four_character_string_is_not_a_box(self):
        assert shorter_side("abcd") == 0.0
        assert is_tight("abcd") is False
        assert crossing_length((((0.0, 0.0), (9.0, 9.0)),), "abcd") == 0.0

    def test_a_box_of_non_numbers_is_not_a_box(self):
        assert shorter_side(("a", "b", "c", "d")) == 0.0
        assert is_tight((None, None, None, None)) is False

    def test_an_ordinary_box_still_works(self):
        assert shorter_side((0, 0, 10, 3)) == pytest.approx(3.0)
        assert is_tight((0.0, 0.0, 10.0, 3.0)) is True


class TestTheMemoKeyIsAValue:
    """`id()` of a value tuple is recycled the instant the caller drops it.

    An earlier revision keyed the untight memo on `id(label_box)`, so four
    *distinct* boxes created and dropped in a loop reported twice — CPython reuses
    the freed tuple's address, and the exclusion this helper exists to keep loud
    went silent for boxes that merely landed on a recycled one.

    `_label_bbox` keys on `id(item)` safely only because `items` holds every
    annotation alive for the whole run. A box computed and dropped has no such
    anchor, so the key must be the box's value.
    """

    def _boxes_reported(self, seen):
        reported = 0
        with _logs_any() as records:
            for index in range(6):
                # Built and dropped each iteration, so the address is free to reuse.
                if not warn_if_untight((0.0, 0.0, 7.0 + index * 0.001, 7.0), seen, _DiagonalDim()):
                    reported += 1
        return reported, len(records)

    def test_distinct_boxes_each_report(self):
        seen: set = set()
        untight, logged = self._boxes_reported(seen)
        assert untight == 6, "fixture must produce six untight boxes"
        assert logged == 6, (
            f"six distinct boxes logged {logged} times — the memo key is collapsing "
            "boxes that are not the same box"
        )

    def test_distinct_malformed_boxes_each_report(self):
        """The same recycling hazard, in the report added for a box that is not a
        box. Keyed per item, so two annotations both handing back rubbish are two
        lines — not one, which would read as a single misbehaving annotation."""
        seen: set = set()
        items = [object(), object(), object()]
        with _logs_any() as records:
            for item in items:
                assert warn_if_untight((), seen, item) is False
        assert len(records) == len(items), (
            f"{len(items)} annotations with malformed boxes logged {len(records)} "
            "times — the memo key is collapsing distinct items"
        )

    def test_the_same_item_reports_its_malformed_box_once(self):
        seen: set = set()
        item = object()
        with _logs_any() as records:
            warn_if_untight((), seen, item)
            warn_if_untight((), seen, item)
        assert len(records) == 1

    def test_the_same_box_still_reports_once(self):
        seen: set = set()
        box = (0.0, 0.0, 7.0, 7.0)
        with _logs_any() as records:
            warn_if_untight(box, seen, _DiagonalDim())
            warn_if_untight(box, seen, _DiagonalDim())
            warn_if_untight(box, seen, _DiagonalDim())
        assert len(records) == 1
