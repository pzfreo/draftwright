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
    MAX_TIGHT_LABEL_SIDE,
    MIN_CROSSING_MM,
    Crossing,
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


class TestTheLabelBoxMustBeTight:
    """`label_bbox` is the *axis-aligned* box of a possibly-rotated label.

    On `_dense_plate`, `'2× 14.1'` — seven characters about 2.2 mm tall — has a
    10.197 x 10.197 mm **square** box, because it is drawn on a diagonal. Clipping
    against that reports a ~3 mm crossing as 10.2 mm and a 0.4 mm corner graze as
    10.2 mm, defeating `MIN_CROSSING_MM` by a factor of 25. The true rectangle is
    not recoverable from the annotation, so such a box is not crossable at all.
    """

    def test_a_tight_box_is_crossable(self):
        assert is_tight((10.0, 10.0, 20.0, 12.166))

    def test_a_taller_font_is_still_tight(self):
        # A second font size or a stacked tolerance must stay covered. On
        # `_dense_plate` the shorter sides are 2.166, 2.67, 3.252 and 2.694;
        # none of those may be cut, only the 10.197 rotated one.
        assert is_tight((10.0, 10.0, 20.0, 13.252))
        assert is_tight((10.0, 10.0, 20.0, 12.694))

    def test_a_single_character_label_is_tight(self):
        """`'6'` measures 1.416 mm on its shorter side — its glyph WIDTH, not the
        text height. Under the median gate that preceded this, a sheet carrying
        many of these dragged the reference below an ordinary label."""
        assert is_tight((0.0, 0.0, 1.416, 2.166))

    def test_a_quarter_turned_label_is_still_tight(self):
        """At 90° the box is an EXACT fit, but its height is the text's width.

        Gating on height alone made every vertical dimension label longer than
        about three characters uncrossable — silently disabling the check for a
        very common class while fixing a rarer one. Measured on
        `Box(123.456, 87.654, 45.678)`: the vertical `'45.7'` has a label box of
        2.130 x 6.891 mm.
        """
        vertical = (0.0, 0.0, 2.130, 6.891)
        assert shorter_side(vertical) == pytest.approx(2.130)
        assert is_tight(vertical)

    def test_the_rotated_dense_plate_box_is_not_tight(self):
        # The measured case: 10.197 mm on both sides.
        assert not is_tight((38.298, 176.558, 48.495, 186.755))

    def test_the_limit_is_the_engine_text_height_not_the_sheet(self):
        """A FIXED reference, so a pair's verdict cannot change because of an
        unrelated annotation elsewhere on the sheet.

        Two revisions used the sheet's median shorter side, and both were wrong the
        same way. `--zones` — a public CLI flag — put 28 single-character zone
        labels on a sheet, dragged the median to 1.051 mm, pushed the threshold
        below the ordinary 2.166 mm label height, and silently disabled the entire
        check: four findings became zero.
        """
        from draftwright._core import _FONT_SIZE

        assert MAX_TIGHT_LABEL_SIDE == pytest.approx(2.0 * _FONT_SIZE)
        assert is_tight((0.0, 0.0, 10.0, MAX_TIGHT_LABEL_SIDE))
        # Over the size cut AND near-square, which is what the pair of rules means.
        over = MAX_TIGHT_LABEL_SIDE + 1e-6
        assert not is_tight((0.0, 0.0, over, over))

    def test_an_untight_label_cannot_be_crossed(self):
        rotated = (38.298, 176.558, 48.495, 186.755)
        crosser = _Annotation([((0.0, 181.0), (100.0, 181.0))], label="10")
        assert (
            label_crossings(
                segments_of(crosser),
                segments_of(_Annotation([])),
                label_a=None,
                label_b=rotated,
            )
            == []
        )
        # The precondition: the same stroke through a TIGHT box of the same width
        # is reported, so the emptiness above is the gate's doing, not the
        # fixture's.
        tight = (38.298, 179.0, 48.495, 181.166)
        assert label_crossings(
            segments_of(crosser),
            segments_of(_Annotation([])),
            label_a=None,
            label_b=tight,
        )

    def test_a_bound_box_is_not_a_box(self):
        """`build123d.BoundBox` defines neither `__bool__` nor `__len__`.

        `len()` on one raises `TypeError` out of `lint_drawing`, killing every
        other check — the crash the guard exists to prevent, which an earlier
        revision of that guard would itself have caused.
        """

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
        with _logs("too large and too square"):
            assert warn_if_untight((38.298, 176.558, 48.495, 186.755)) is False

    def test_a_tight_box_is_quiet(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert warn_if_untight((10.0, 10.0, 20.0, 12.166)) is True

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
        with _logs("too large and too square"):
            assert warn_if_untight((38.298, 176.558, 48.495, 186.755)) is False

    def test_a_tight_box_is_quiet(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            assert warn_if_untight((10.0, 10.0, 20.0, 12.166)) is True
        assert caplog.records == []

    def test_a_control_frame_is_quiet(self, caplog):
        # Measured: m_gdt0 6.150 x 12.255, m_gdt1 25.161 x 6.150. Over the size
        # cut, but long rather than square, so they stay crossable.
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            assert warn_if_untight((0.0, 0.0, 6.150, 12.255)) is True
            assert warn_if_untight((0.0, 0.0, 25.161, 6.150)) is True
        assert caplog.records == []

    def test_a_malformed_box_is_untight_without_a_report(self, caplog):
        # Nothing useful to say about a box that is not a box; `segments_of` and
        # `crossing_length` already report their own refusals.
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            assert warn_if_untight(()) is False
        assert caplog.records == []

    def test_a_repeated_message_reports_once_per_run(self, caplog):
        """`_label_bbox` memoises the same way (#711): several checks read the
        same item, and `repair()` lints twice per pass for up to three passes."""
        seen: set[str] = set()
        box = (38.298, 176.558, 48.495, 186.755)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            warn_if_untight(box, seen)
            warn_if_untight(box, seen)
            warn_if_untight(box, seen)
        assert len(caplog.records) == 1

    def test_without_a_memo_it_reports_every_time(self, caplog):
        box = (38.298, 176.558, 48.495, 186.755)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            warn_if_untight(box)
            warn_if_untight(box)
        assert len(caplog.records) == 2

    def test_lint_still_returns_under_warnings_as_errors(self):
        """The whole point. A raised warning here would take the sheet's other
        checks with it."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert measure(_Annotation([((0.0, 12.0, 0.0), (30.0, 12.0, 0.0))]), BOX) == 0.0
            assert warn_if_untight((38.298, 176.558, 48.495, 186.755)) is False


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
