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
inside the O(n²) pair loop. On `nist_ctc_02_asme1_ap203` (162 annotations) that
measured `lint()` at 0.772 s against 0.576 s with both hoisted to one pass per
item — the same recomputation #161 removed from the loop beside it. Callers pass
segments in for that reason.

## What it does not cover

Line-work crossing other line-work, with no text involved: two arrowheads
merging into one blob in open space. That is a real defect and this check is
silent on it, deliberately, because the honest measure for it is a question
about terminator geometry rather than anything a label box can answer. The
flange case happens to be caught anyway — the dimension line runs 0.85 mm into
the neighbouring label on its way — but that is luck, not coverage.

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

import warnings
from collections.abc import Sequence
from dataclasses import dataclass

from draftwright._core import _FONT_SIZE

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
#: 2x it admits every real label measured — 1.416 to 2.694 — and cuts the 10.197
#: rotated one, with the nearest datum on either side a factor of 2 away.
MAX_TIGHT_LABEL_SIDE = 2.0 * _FONT_SIZE

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


def segments_of(item) -> tuple:
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
            if len(start) != 2 or len(end) != 2:
                # Discards this item's line-work entirely, so say so. `_label_bbox`
                # twenty lines away in `structural.py` warns once per item for the
                # same reason (#701: a check that silently skips an item can
                # silently disable itself). If `segments` ever started yielding
                # `Vector`s, every item would contribute nothing and this check
                # would become a no-op that reported a clean sheet.
                problem = "segments are not 2D point pairs"
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
        warnings.warn(
            f"{type(item).__name__}: {problem}; its line-work is excluded from "
            "annotation_ink_overlap",
            stacklevel=2,
        )
        return ()
    return tuple(kept)


def length_inside(segment, box) -> float:
    """Length of *segment* lying inside axis-aligned *box*.

    Liang-Barsky parametric clipping. Returns 0.0 for a segment that misses the
    box, lies on its boundary, or has zero length.
    """
    (x0, y0), (x1, y1) = segment
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for numerator, denominator in (
        (box[0] - x0, dx),
        (x0 - box[2], -dx),
        (box[1] - y0, dy),
        (y0 - box[3], -dy),
    ):
        if denominator == 0.0:
            # Parallel to this edge. `>= 0` rather than `> 0` so that a segment
            # lying exactly ON the boundary measures zero: it shares no interior
            # with the box, and a line running along the top of a text box does
            # not obscure the text. Same strictness as `annotation_overlap`'s own
            # `ox > 0.5 and oy > 0.5`.
            if numerator >= 0.0:
                return 0.0
            continue
        t = numerator / denominator
        if denominator > 0.0:
            if t > t1:
                return 0.0
            t0 = max(t0, t)
        else:
            if t < t0:
                return 0.0
            t1 = min(t1, t)
    if t1 <= t0:
        return 0.0
    return float((t1 - t0) * (dx * dx + dy * dy) ** 0.5)


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

    An explicit sequence test, not ``len(value) != 4``: a duck-typed item
    (ADR 0005) may hand back a ``build123d.BoundBox``, which defines neither
    ``__bool__`` nor ``__len__``, so ``len()`` on one raises ``TypeError`` out of
    `lint_drawing` — the very crash the guard is here to prevent.
    """
    return isinstance(value, Sequence) and len(value) == 4


def shorter_side(label_box) -> float:
    """The label box's shorter side, or ``0.0`` if it is not a box."""
    if not _is_box(label_box):
        return 0.0
    return float(min(label_box[2] - label_box[0], label_box[3] - label_box[1]))


def is_tight(label_box) -> bool:
    """Whether *label_box* fits its text closely enough for clipping to be honest.

    See :data:`MAX_TIGHT_LABEL_SIDE`. Depends on nothing but the box itself, so a
    pair's verdict cannot change because of an unrelated annotation elsewhere on
    the sheet.
    """
    if not _is_box(label_box):
        return False
    return shorter_side(label_box) <= MAX_TIGHT_LABEL_SIDE


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
