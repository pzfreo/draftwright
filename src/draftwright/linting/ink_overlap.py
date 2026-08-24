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
its own 250-comparison ceiling on four of them. This is arithmetic on a handful
of segments and costs nothing measurable, so the ceiling — and the truncation
report the ceiling needed — are gone.

## What it does not cover

Line-work crossing other line-work, with no text involved: two arrowheads
merging into one blob in open space. That is a real defect and this check is
silent on it, deliberately, because the honest measure for it is a question
about terminator geometry rather than anything a label box can answer. The
flange case happens to be caught anyway — the dimension line runs 0.85 mm into
the neighbouring label on its way — but that is luck, not coverage.

`leader_line_through_text` covers the specific case of a leader shaft through
text and predates this; the two overlap and #1332 records the question of
whether it is now redundant.
"""

from __future__ import annotations

from dataclasses import dataclass

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
#: `_issue_881_y_step_flange`: `'8'`'s dimension line entering the neighbouring
#: `'4× 2'` label. So the floor sits inside the empty band, above the confirmed
#: graze and below the confirmed defect, rather than on top of either.
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
    #: The label box crossed, as ``(min_x, min_y, max_x, max_y)``.
    label_box: tuple[float, float, float, float]
    #: ``True`` when it is *item_b*'s label that is crossed by *item_a*.
    crosses_b: bool

    def is_reportable(self) -> bool:
        return self.length >= MIN_CROSSING_MM


def _segments_of(item) -> tuple:
    """An annotation's line-work as ``((x0, y0), (x1, y1))`` pairs, or ``()``.

    Read defensively: ``items`` is duck-typed (ADR 0005), so an annotation that
    has never heard of the attribute simply contributes no line-work rather than
    killing lint. It is a sequence, not a shape — no CAD call is made here.
    """
    try:
        segments = getattr(item, "_segments_local", None)
    except Exception:  # noqa: BLE001 — duck-typed items may misbehave
        return ()
    return tuple(segments) if segments else ()


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


def crossing_length(item, label_box) -> float:
    """Total line-work of *item* running through *label_box*."""
    if label_box is None:
        return 0.0
    return sum(length_inside(segment, label_box) for segment in _segments_of(item))


def worst_label_crossing(item_a, item_b, *, label_a=None, label_b=None) -> Crossing | None:
    """The worse of the two directions, or ``None`` if neither is reportable.

    *label_a* and *label_b* are the two **text** extents. A label-less annotation
    passes ``None`` and simply cannot be crossed — it must not fall back to the
    annotation's full extent, which spans its witness lines and would report
    every dimension whose line-work reaches a neighbour's arm.
    """
    into_b = crossing_length(item_a, label_b)
    into_a = crossing_length(item_b, label_a)
    if into_b >= into_a:
        best = Crossing(length=into_b, label_box=tuple(label_b or ()), crosses_b=True)
    else:
        best = Crossing(length=into_a, label_box=tuple(label_a or ()), crosses_b=False)
    return best if best.is_reportable() else None
