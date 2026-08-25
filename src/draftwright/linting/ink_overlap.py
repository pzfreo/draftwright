"""Line-work drawn across another annotation's label text.

`annotation_overlap` compares label boxes. That is a deliberate choice, and the
comment beside it gives the reason: full bounding boxes include witness lines,
which legitimately overlap for stacked dimensions, so comparing them cries wolf.

The cost of the choice is that anything drawn over a label which is *not itself
a label* goes unreported — a dimension line, an extension line, a leader shaft.
This measures that directly: **how far another annotation's line-work runs
through this label's text box**, in millimetres.

## Why this measures line length, and not shared ink

The first implementation of #1321 intersected the two annotations' sketches and
measured the shared area. Three things were wrong with it, and each is worth
recording because each cost a wrong answer:

**It answered a question nobody asked.** Two annotations sharing ink is ordinary
— strokes are filled faces about 0.1 mm wide, so everything crosses everything.
What a reader cares about is whether the *digits* are obscured. Asking that
directly needs no shape test, no area floor tuned against line width, and no
grouping: the region of interest is one label box.

**Its answer depended on how the CAD kernel carved up the intersection.**
`Shape.intersect` returns a face list, and the two supported build123d releases
return *different* lists for the same drawing. On `_issue_881_y_step_flange`,
0.11.1 additionally returns three hairline faces one stroke-width tall where two
dimension lines lie along each other; 0.10.0 returns none of them. That changed
a 2.28 mm gap into a bridged one, and the shared-ink check reported the defect
on one kernel and not the other — a 14% margin on a clustering constant deciding
whether a visible defect was seen at all. Segment endpoints come from the
annotation's own spec, not from a boolean, so both kernels see the same numbers.

**It cost a boolean per pair.** Measured across the 23 STEP fixtures in
`tests/fixtures`, that was +79% build time (264.6s -> 473.9s) and it exhausted
its own 250-comparison ceiling on four of them. This is arithmetic, so the
ceiling — and the truncation report the ceiling needed — are gone.

It is arithmetic, but it is not free, and the first version of this said it "costs
nothing measurable" while recomputing `label_bbox` and the `segments` property
inside the O(n²) pair loop — the same recomputation #161 removed from the loop
beside it. Hoisting both to one pass per item took `lint()` on
`nist_ctc_02_asme1_ap203` (162 annotations) from 0.772 s to 0.576 s. Callers pass
segments in for that reason.

Those two figures compare revisions of this branch. Against `main`, which has no
check at all, the same measurement is **0.565 s to 0.570 s** — about 1%. That is
the number that answers "what does this cost", and it is the one to quote.

## What it does not cover

Line-work crossing other line-work, with no text involved: two arrowheads
merging into one blob in open space. That is a real defect and this check is
silent on it, deliberately, because the honest measure for it is a question
about terminator geometry rather than anything a label box can answer. The
flange case happens to be caught anyway — the dimension line runs 0.85 mm into
the neighbouring label on its way — but that is luck, not coverage.

`feature_leader_crossing` overlaps with this one, and **it is observed, not
theoretical** — an earlier revision of this note claimed otherwise and was wrong.
On `test_two_cross_hole_heights_share_their_end_view_ladder_without_crossing` both
fire on the same segment against the same label box:

    info    feature_leader_crossing  hole callout ⌀2.5 THRU retained under
                                     Policy B across: dim_loc_side_z7550:segment:3
    warning annotation_ink_overlap   '75.5' draws 17.4 mm of line-work through
                                     the label '⌀2.5 THRU'

`dim_loc_side_z7550.label` is `'75.5'`, and clipping its segments against
`hc_side1.label_bbox` gives 17.42 mm for `segment:3` and 0.00 for the rest — the
same stroke, the same box.

Both codes are in `_LEGIBILITY_CODES`; `_primary_issues` keys on `(code, token)`
so they never collapse; and `info` takes the WARNING floor in `_issue_component`
("an issue reaching here is a defect in the axis being scored"), so the earlier
claim that the `info` is "deliberately unpenalised" was also wrong. A crossing the
router *deliberately accepted* under Policy B therefore costs two warning
penalties where it used to cost one.

That is a scoring regression this check introduces, recorded rather than papered
over. The fix is either to exclude `:label` blockers from `feature_leader_crossing`
or to suppress this check for a pair that one has already reported — both reach
into the leader router, so #1333 owns the decision.

`leader_line_through_text` is **not** the same check and is not made redundant by
this one. `_lint_leader` tests an item's own elbow against its own `label_bbox` —
a leader running through the text it is itself labelling. This runs only over
`i < j` pairs and never compares an item with itself, so the two cannot fire on
the same defect: that one covers a leader over its own text, this one covers
another annotation's line-work over it.

## A finding this check makes that repair currently cannot act on

`repair.reconcile_witness_labels` slides a label along its own dimension line to
clear a foreign stroke, but it deliberately exempts strokes *parallel* to that
line — ``if (sdy > sdx) == vertical: continue  # parallel = stacked shafts``.
The smallest confirmed defect here is exactly such a stroke: on
`_issue_881_y_step_flange` the reported `'8' -> '4× 2'` crossing is the
horizontal dimension-line segment ``(179.24, 119.0)-(183.29, 119.0)`` entering a
label box centred on the same ``y = 119.0``. Sliding the label along that line
cannot clear a stroke running down it.

`reconcile_witness_labels` skips diagonal strokes too — ``if min(sdx, sdy) > 0.1:
continue`` — so a leader shaft crossing a neighbour's label is a second such
class. Leaders do reach here: on the flange,
`'4× ⌀4 THRU EQ SP ON ø50.9 BC'` appears as a crosser.

So two subsets of what this reports are, today, permanent: detected and
unactionable through the existing repair path. That is a real gap rather than a
defect in either piece, and the two checks having *different* predicates —
transverse-only, diagonal-skipped, parallel-exempt there against any-direction
here — is itself worth resolving into one shared predicate rather than two
copies. Which rule wins is a decision for #1333; #1334 removes the question by
not placing the label there in the first place.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from draftwright._core import _FONT_SIZE
from draftwright._geometry import _segment_clip_extent

_log = logging.getLogger(__name__)

#: The largest a label box's shorter side may be and still be treated as a fit
#: around its text, as a multiple of the engine's own annotation text height
#: (``_core._FONT_SIZE``).
#:
#: `label_bbox` is an **axis-aligned** box around a label that may be drawn at an
#: angle, so for a rotated label it is the bounding box of a rotated rectangle and
#: is much larger than the glyphs. Measured on `_dense_plate`: `'2× 14.1'` — seven
#: characters about 2.2 mm tall — has a label box of **10.197 x 10.197 mm**, a
#: square, because it is drawn on a diagonal.
#:
#: Clipping against that would report a stroke as crossing 10.2 mm of text when it
#: crosses about 3 mm of it, and would report a 0.4 mm corner graze as 10.2 mm —
#: defeating `MIN_CROSSING_MM` by a factor of 25 exactly where this module claims
#: "the region of interest is one label box".
#:
#: The true rectangle is not recoverable here. `_label_bbox_local` is already an
#: axis-aligned bake of it, and the angle is in neither `_init_rot` nor the
#: annotation's location — on that fixture both are zero, because the rotation was
#: applied before the box reached the annotation. Rather than report a number this
#: check cannot stand behind, such a label is treated as **not crossable**, and the
#: gap is recorded: diagonal labels are uncovered until `build123d-drafting-helpers`
#: exposes the rect or its angle.
#:
#: The **shorter side**, not the height. At 90° the AABB is an exact fit, but its
#: height is then the text's *width*: a vertical `'45.7'` measures 2.130 x 6.891 mm,
#: and gating on height alone silently made every vertical label longer than about
#: three characters uncrossable — disabling the check for a very common class.
#:
#: And a **fixed** reference, not the sheet's own median. Two revisions of this used
#: a median, and both were wrong for the same reason: `min(w, h)` on a one- or
#: two-character label is the *glyph width*, not the text height — `'6'` measures
#: 1.416 and `'8'` 1.470 against an ordinary 2.166. So the median moved with how
#: many short labels a sheet happened to carry, and `--zones` — a public CLI flag —
#: put 28 single-character zone labels on the sheet, dragged the median to 1.051,
#: and silently disabled the entire check: 4 findings became 0. A threshold that
#: depends on unrelated annotations also forfeits the sheet-independence this
#: module exists to secure. `_FONT_SIZE` is the engine's own declared annotation
#: text height and moves for nobody.
#:
#: Size alone is not enough, though, and the figure below is not a factor of two
#: clear of everything real. A GD&T feature control frame is one text row plus box
#: padding: measured on a declared sheet, `m_gdt0` is 6.150 x 12.255 mm and
#: `m_gdt1` 25.161 x 6.150 — a **shorter side of 6.150**, only 1.025x this
#: threshold. Excluding those would make a dimension line through a control frame,
#: arguably the annotation whose legibility matters most, unreportable.
#:
#: So size is paired with **shape**. A rotated rectangle's bounding box tends
#: toward square as the angle approaches 45° — the `_dense_plate` diagonal is
#: 10.197 x 10.197, an aspect of exactly 1.00 — while a real text box stays long
#: relative to the text: those control frames measure 1.99, 3.67 and 4.09. A box
#: is untight only when it is **both** larger than text should be **and** nearly
#: square, which the diagonal is and the frames are not, with the nearest real
#: datum 1.33x clear of the aspect cut rather than 1.025x clear of the size one.
MAX_TIGHT_LABEL_SIDE = 2.0 * _FONT_SIZE

#: How long a large box must be, relative to its shorter side, to still read as
#: text rather than as a rotated rectangle's bounding box. See
#: :data:`MAX_TIGHT_LABEL_SIDE`.
#:
#: The residual gap this leaves, stated rather than implied: a **shallow** diagonal
#: escapes both tests. A 12 x 2.2 mm label at 20° has a bounding box of about
#: 12.0 x 6.2 mm — over the size cut but an aspect of 1.94, so it is treated as
#: tight and measured against a box roughly twice the glyphs' area. A stroke
#: through the empty corner could then be reported as crossing text. No instance
#: exists in `tests/fixtures` (671 labelled annotations, one exclusion), and the
#: real fix is for `build123d-drafting-helpers` to expose the rect or its angle;
#: until then this closes the measured case and not the general one.
MIN_TIGHT_LABEL_ASPECT = 1.5

#: How far another annotation's line-work may run through a label's text box
#: before it is reported, in millimetres.
#:
#: Measured, not estimated. Every crossing on the 23 STEP fixtures in
#: `tests/fixtures` was collected with **no floor at all** — 134 of them across
#: both supported build123d releases — and the distribution is not a gradient
#: with a judgement call in it. It is two populations with an empty band between:
#:
#:     0.0 - 0.5 mm :   1        <- one case, and it is a graze
#:     0.5 - 1.0 mm :   0        <- nothing here at all
#:     1.0 - 2.0 mm :   2
#:     2.0 - 5.0 mm : 110
#:     5.0 +    mm :   21
#:
#: The single case below the floor is 0.308 mm on
#: `issue_909_basic_part_design_017_body`: an extension line's tip touching the
#: corner of the `DETAIL A — SCALE 2:1` caption box. Rendered at 600 dpi — the
#: caption is entirely legible, and nothing is drawn across a character.
#:
#: The smallest crossing confirmed as a real defect is **0.854 mm**, on
#: `_issue_881_y_step_flange` — `'8'`'s dimension line entering the neighbouring
#: `'4× 2'` label. That fixture is built in Python and is **not** in the corpus
#: above, and 0.854 mm falls inside the band the histogram shows as empty; the
#: band is empty *of STEP-fixture crossings*, not of defects. So state the floor's
#: position for what it is: it sits at the bottom edge of that band, 0.19 mm above
#: the confirmed graze and 0.35 mm below the smallest confirmed defect.
#:
#: Note what legitimate drafting scores: **exactly zero**. Extension lines run
#: away from their own text, so on a clean sheet nothing enters any label box.
#: This is therefore a guard against corner-clipping, not a separator between two
#: overlapping populations — which is why it can be a round number without that
#: being a tuning choice.
#:
#: The bulk at 2.157-2.166 mm is a line crossing a label vertically: text is
#: about 2.16 mm tall, so those are strokes clean through a row of digits.
MIN_CROSSING_MM = 0.5


@dataclass(frozen=True)
class Crossing:
    """One annotation's line-work running through another's label text."""

    #: Millimetres of line-work inside the label box.
    length: float
    #: The label box crossed, or ``None`` when there was no box to cross. Not
    #: ``tuple[float, ...]``: a crossing of length zero against a label-less
    #: annotation has no box, and declaring one it cannot supply is a lie the
    #: type checker cannot catch because nothing reads the field yet.
    label_box: tuple[float, float, float, float] | None
    #: ``True`` when it is *item_b*'s label that is crossed by *item_a*.
    crosses_b: bool

    def is_reportable(self) -> bool:
        return self.length >= MIN_CROSSING_MM


def _warn_once(message: str, seen, key=None) -> None:
    """Log unless *seen* has already carried this message for this run.

    **Logged at DEBUG, not warned.** `WARNING` with no logging configured reaches
    stderr through Python's `lastResort` handler, so an ordinary supported part
    printed this on every build — twice, and up to six times through `repair()`.
    The condition is permanent and not fixable by the caller, so it belongs in a
    log an operator opts into rather than on their terminal.

    These helpers run inside `lint_drawing`'s deliberately
    unguarded region ("#701: the check body runs unguarded"), so under a
    warnings-as-errors configuration a `warnings.warn` here propagates out and
    kills every other check on the sheet — the #701 failure inverted. The
    neighbouring `_label_bbox` logs for the same condition, and a logger is
    silenceable by name, which was the point of giving this its own category.

    Memoised the same way `_label_bbox` is (#711): several checks read the same
    item, so an unmemoised message floods the log O(n²) on one bad annotation, and
    `repair()` lints twice per pass for up to three passes. A `seen` of ``None``
    logs every time, which is what a direct helper call should do.
    """
    # Keyed per ITEM, not per message. `_label_bbox` — the #711 precedent this
    # cites — memoises on `id(item)` for a reason: the message's only varying part
    # is the annotation's type name, so keying on it collapses N distinct failing
    # annotations into one line. That would hide exactly the systemic failure the
    # `segments_of` docstring names: if `segments` ever started yielding `Vector`s,
    # every item would contribute nothing and this would report it once,
    # indistinguishable from a single misbehaving annotation.
    token = message if key is None else key
    if seen is not None:
        if token in seen:
            return
        seen.add(token)
    _log.debug("%s", message)


def segments_of(item, seen=None) -> tuple:
    """An annotation's line-work as ``((x0, y0), (x1, y1))`` pairs, or ``()``.

    ``segments``, not ``_segments_local``. The private one is the un-located
    build-frame cache; the public property applies the annotation's location, and
    every box measured against it here comes from ``label_bbox``, which is in page
    coordinates. Mixing them measures a located annotation against the wrong part
    of the sheet — on an A4 sheet the title block's own rules cache as
    ``(0, 0)-(150, 16)`` while they are drawn at ``(166, 11)-(286, 27)``. Every
    other reader in this repository uses ``segments`` for the same reason.

    Read defensively: ``items`` is duck-typed (ADR 0005), so an annotation that
    has never heard of the attribute — or whose attribute is not a sequence of
    point pairs — contributes no line-work rather than killing lint. The
    materialisation is inside the guard because an iterable that raises partway
    through is the same failure as a property that raises up front. No CAD call is
    made here.
    """
    problem = None
    try:
        segments = getattr(item, "segments", None)
        if not segments:
            return ()
        kept: list[tuple[tuple, tuple]] = []
        for segment in segments:
            start, end = segment
            start, end = tuple(start), tuple(end)
            # Length 2 exactly. A `build123d.Vector` yields THREE coordinates under
            # `tuple()`, and a 3-tuple survives the unpacking above only to raise
            # inside `length_inside` — out of `crossing_length`, out of
            # `lint_drawing`, killing the whole run. Duck-typed items reach here
            # (ADR 0005), so the shape is checked rather than assumed.
            if (
                len(start) != 2
                or len(end) != 2
                or not all(isinstance(value, (int, float)) for value in (*start, *end))
            ):
                # Discards this item's line-work entirely, so say so. `_label_bbox`
                # twenty lines away in `structural.py` warns once per item for the
                # same reason (#701: a check that silently skips an item can
                # silently disable itself). If `segments` ever started yielding
                # `Vector`s, every item would contribute nothing and this check
                # would become a no-op that reported a clean sheet.
                # Length AND type. `("ab", "cd")` unpacks fine and `tuple("ab")`
                # is `('a', 'b')` — length 2 — so a length-only check kept it and
                # `length_inside` then evaluated `'c' - 'a'`, a `TypeError`
                # outside any handler, killing lint for every other check on the
                # sheet. That is the hole this guard's own comment claimed to
                # close, and the existing `["not a segment"]` test failed at the
                # unpack instead of reaching it.
                problem = "segments are not pairs of 2D numeric points"
                kept = []
                break
            kept.append((start, end))
    except Exception as exc:  # noqa: BLE001 — duck-typed items may misbehave
        problem = f"reading segments raised {type(exc).__name__}"
        kept = []
    if problem is not None:
        # Outside the `try`. An earlier revision warned *inside* it, so under a
        # warnings-as-errors configuration the warning raised and was swallowed by
        # the same handler — the one loud path went quiet exactly when the operator
        # asked for loud. The raising-property path said nothing at all, which is
        # the #701 failure the neighbouring `_label_bbox` logs to avoid: if one
        # annotation type's `segments` started raising, that item would vanish from
        # the check with no signal.
        _warn_once(
            f"{type(item).__name__}: {problem}; its line-work is excluded from "
            "annotation_ink_overlap",
            seen,
            key=("segments", id(item)),
        )
        return ()
    return tuple(kept)


def length_inside(segment, box) -> float:
    """Length of *segment* lying inside axis-aligned *box*.

    A thin derivation from `_geometry._segment_clip_extent`, not a second clipper.
    That helper already clips a segment to a box for lint — `structural.py` uses it
    for `label_centerline_overlap`, whose docstring says lint measures "only the
    rendered part that actually reaches a label (#1144)" — and for a straight
    segment the diagonal of the extent it returns *is* the clipped length.

    An earlier revision here was its own Liang-Barsky implementation. It agreed
    with the shared one on every case tried except the boundary, where it returned
    0.0 while `_segment_clip_extent` documents "inclusive at the boundary (a touch
    is a hit)" — so a stroke lying exactly on a label's edge was a hit for one
    lint check and a miss for another, in the same module. One predicate, and the
    shared convention wins: CLAUDE.md asks for a shared pass extended rather than
    a copy, and a boundary is a measure-zero case not worth a second answer to.
    """
    clipped = _segment_clip_extent(segment[0], segment[1], box, 0.0)
    if clipped is None:
        return 0.0
    return float(math.hypot(clipped[2] - clipped[0], clipped[3] - clipped[1]))


def crossing_length(segments, label_box) -> float:
    """The longest single stroke of *item* running through *label_box*.

    The longest, not the total. ``MIN_CROSSING_MM`` is justified as a guard
    against corner-clipping, and summing defeats it: a callout with a shaft, an
    elbow and a shelf each clipping a corner by 0.15 mm totals 0.45 mm of
    "crossing" while nothing is drawn over a character. What the check claims in
    its message — that a line is drawn through the text — is a statement about one
    stroke, so one stroke is what is measured.

    Takes the segments rather than the annotation so the caller can compute them
    once per item. `segments` is a property that rebuilds its list from the
    annotation's location on every read, and this is called inside an O(n²) loop
    (#161).
    """
    if not _is_box(label_box):
        return 0.0
    return max(
        (length_inside(segment, label_box) for segment in segments),
        default=0.0,
    )


def _is_box(value) -> bool:
    """Whether *value* is usable as ``(min_x, min_y, max_x, max_y)``.

    ``tuple``/``list`` specifically, and the four entries checked for numbers.
    Not ``len(value) != 4``: a duck-typed item (ADR 0005) may hand back a
    ``build123d.BoundBox``, which defines neither ``__bool__`` nor ``__len__``, so
    ``len()`` on one raises ``TypeError`` out of `lint_drawing`. And not
    ``Sequence``, which accepts ``str`` — a four-character label box passed that
    test and ``shorter_side`` then evaluated ``'n' - 'n'``, the identical hole
    closed for ``segments`` one round earlier.
    """
    return (
        isinstance(value, (tuple, list))
        and len(value) == 4
        and all(isinstance(entry, (int, float)) for entry in value)
    )


def shorter_side(label_box) -> float:
    """The label box's shorter side, or ``0.0`` if it is not a box."""
    if not _is_box(label_box):
        return 0.0
    return float(min(label_box[2] - label_box[0], label_box[3] - label_box[1]))


def warn_if_untight(label_box, seen=None) -> bool:
    """Whether *label_box* is tight, warning once if it is not.

    Call this **once per item**, not once per pair: `_label_bbox` in
    `structural.py` memoises its own warning for exactly this reason (#711), since
    an unmemoised one floods the log O(n²) on a single bad annotation.

    Loud rather than silent. An untight box makes its label uncrossable, so the
    reader gets no finding for line-work through it, and a check that quietly
    stops covering an annotation is the #701 failure this module keeps meeting.
    """
    if is_tight(label_box):
        return True
    if not _is_box(label_box):
        return False
    width = label_box[2] - label_box[0]
    height = label_box[3] - label_box[1]
    _warn_once(
        f"label box {width:.3f} x {height:.3f} mm is too large and too square to be "
        "a fit around text — it is probably a rotated label's bounding box, so "
        "line-work through it is not measured (annotation_ink_overlap)",
        seen,
        key=("untight", id(label_box)),
    )
    return False


def is_tight(label_box) -> bool:
    """Whether *label_box* fits its text closely enough for clipping to be honest.

    See :data:`MAX_TIGHT_LABEL_SIDE`. Depends on nothing but the box itself, so a
    pair's verdict cannot change because of an unrelated annotation elsewhere on
    the sheet.
    """
    if not _is_box(label_box):
        return False
    short = shorter_side(label_box)
    if short <= MAX_TIGHT_LABEL_SIDE:
        return True
    long = max(label_box[2] - label_box[0], label_box[3] - label_box[1])
    return bool(long >= MIN_TIGHT_LABEL_ASPECT * short)


def label_crossings(
    segments_a,
    segments_b,
    *,
    label_a=None,
    label_b=None,
) -> list[Crossing]:
    """Every reportable crossing between the pair, worst first.

    Both directions, not the worse of them. A crosses B's label *and* B crosses
    A's is two obscured labels and therefore two defects; returning one hid the
    other, and hid it from the #1147 ledger too, which keys on the label being
    crossed.

    *label_a* and *label_b* are the two **text** extents. A label-less annotation
    passes ``None`` and simply cannot be crossed — it must not fall back to the
    annotation's full extent, which spans its witness lines and would report
    every dimension whose line-work reaches a neighbour's arm.
    """
    # Defensive only: callers are expected to have excluded untight boxes already,
    # once per item rather than once per pair — see `warn_if_untight`.
    if not is_tight(label_a):
        label_a = None
    if not is_tight(label_b):
        label_b = None
    found = [
        Crossing(
            length=crossing_length(segments_a, label_b),
            label_box=tuple(label_b) if label_b else None,
            crosses_b=True,
        ),
        Crossing(
            length=crossing_length(segments_b, label_a),
            label_box=tuple(label_a) if label_a else None,
            crosses_b=False,
        ),
    ]
    return sorted(
        (crossing for crossing in found if crossing.is_reportable()),
        key=lambda crossing: -crossing.length,
    )
