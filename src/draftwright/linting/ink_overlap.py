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
whether a visible defect was seen at all. Segment endpoints and exact label
polygons come from annotation metadata, not from a boolean, so both kernels see
the same numbers.

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

from draftwright._geometry import _segment_clip_extent

_log = logging.getLogger(__name__)

#: How far from axis-aligned a dimension's own line must run before its label is
#: treated as rotated, in millimetres of the smaller delta. Shared with
#: `repair.reconcile_witness_labels`, which uses the identical test and tolerance
#: for the identical question (`repair.py`: "A diagonal dim's label_offset_x moves
#: BOTH page coordinates").
#:
#: `label_bbox` is an **axis-aligned** box, so for a label drawn at an angle it is
#: the bounding box of a rotated rectangle and much larger than the glyphs.
#: Measured on `_dense_plate`, `'2× 14.1'` — seven characters about 2.2 mm tall —
#: has a box of 10.197 x 10.197 mm. Clipping against that reports a stroke as
#: crossing 10.2 mm of text when it crosses about 3 mm, so such a label is treated
#: as not crossable.
#:
#: **Asked of the annotation, not of the box.** Five revisions tried to infer
#: rotation from the box's shape or size, and each was falsified by a real case:
#:
#:     height only     every vertical label uncrossable — at 90° the box is an
#:                     exact fit whose height is the text's WIDTH
#:     6 mm cut        every GD&T control frame excluded, measured at 6.150 mm
#:     size first      a two-character label at 45° through, 4.53 mm square
#:     size last       control frames excluded again
#:     aspect only     every DATUM SYMBOL excluded — `DatumFeature.label_bbox` is
#:                     `2.0 * font_size` square by construction, measured at
#:                     exactly 5.000 x 5.000 mm, aspect 1.000: a genuine tight
#:                     frame around a letter that no shape rule can tell from a
#:                     rotated box
#:
#: and shape left a gap in the middle regardless: a `"123.45"` dimension label at
#: 30° measures 7.251 x 5.208, an aspect of 1.39, which reads as tight while the
#: box holds 37.8 mm² against 13.0 mm² of glyphs.
#:
#: A box cannot say whether it is rotated. ``Dimension.label_polygon`` can: it
#: retains the exact four corners computed by the renderer before that rectangle
#: became an AABB. Older direct ``Dimension`` objects are recognised from their
#: public ``measured_length`` and first dimension-line segment; a diagonal one
#: without the polygon is excluded loudly rather than measured against geometry
#: known to be false. No private draftwright placement spec participates.
DIAGONAL_DIM_TOLERANCE_MM = 0.1

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


@dataclass(frozen=True)
class LabelRegion:
    """A label's reporting AABB and optional exact convex keep-clear polygon."""

    box: tuple[float, float, float, float]
    polygon: tuple[tuple[float, float], ...] | None = None


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
    # The key must be a VALUE, never `id(...)`. `id()` of a tuple is reused the
    # instant the caller drops it, so four distinct untight boxes in a loop
    # reported twice — the exclusion this helper exists to keep loud went silent
    # for boxes that merely reused a freed address. `_label_bbox` keys on
    # `id(item)` safely only because `items` holds every annotation alive for the
    # run; a box computed and dropped has no such anchor.
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
            key=("segments", id(item), type(item).__name__),
        )
        return ()
    return tuple(kept)


def _is_polygon(value) -> bool:
    """Whether *value* is a usable ordered convex page-plane polygon."""
    if not isinstance(value, (tuple, list)) or len(value) < 3:
        return False
    if not all(
        isinstance(point, (tuple, list))
        and len(point) == 2
        and all(isinstance(coordinate, (int, float)) for coordinate in point)
        for point in value
    ):
        return False
    signs = []
    for index, point in enumerate(value):
        nxt = value[(index + 1) % len(value)]
        after = value[(index + 2) % len(value)]
        cross = (nxt[0] - point[0]) * (after[1] - nxt[1]) - (nxt[1] - point[1]) * (
            after[0] - nxt[0]
        )
        if abs(cross) > 1e-12:
            signs.append(math.copysign(1.0, cross))
    return bool(signs) and all(sign == signs[0] for sign in signs)


def _region_box(region) -> tuple[float, float, float, float] | None:
    if isinstance(region, LabelRegion):
        return region.box
    return tuple(region) if _is_box(region) else None


def _region_polygon(region) -> tuple[tuple[float, float], ...] | None:
    if isinstance(region, LabelRegion) and region.polygon is not None:
        return region.polygon
    return None


def _length_inside_polygon(segment, polygon) -> float:
    """Length of a segment inside an ordered convex polygon (Cyrus-Beck)."""
    area2 = sum(
        point[0] * polygon[(index + 1) % len(polygon)][1]
        - polygon[(index + 1) % len(polygon)][0] * point[1]
        for index, point in enumerate(polygon)
    )
    orientation = 1.0 if area2 > 0.0 else -1.0
    start, end = segment
    dx, dy = end[0] - start[0], end[1] - start[1]
    t0, t1 = 0.0, 1.0
    for index, edge_start in enumerate(polygon):
        edge_end = polygon[(index + 1) % len(polygon)]
        ex, ey = edge_end[0] - edge_start[0], edge_end[1] - edge_start[1]
        at_start = orientation * (
            ex * (start[1] - edge_start[1]) - ey * (start[0] - edge_start[0])
        )
        along = orientation * (ex * dy - ey * dx)
        if abs(along) < 1e-12:
            if at_start < -1e-12:
                return 0.0
            continue
        boundary_t = -at_start / along
        if along > 0.0:
            t0 = max(t0, boundary_t)
        else:
            t1 = min(t1, boundary_t)
        if t0 > t1:
            return 0.0
    return float(math.hypot(dx, dy) * max(0.0, t1 - t0))


def length_inside(segment, region) -> float:
    """Length of *segment* lying inside a label region.

    Axis-aligned regions remain a thin derivation from
    `_geometry._segment_clip_extent`, not a second box clipper. That helper
    already clips a segment to a box for lint — `structural.py` uses it
    for `label_centerline_overlap`, whose docstring says lint measures "only the
    rendered part that actually reaches a label (#1144)" — and for a straight
    segment the diagonal of the extent it returns *is* the clipped length.

    A rotated ``Dimension.label_polygon`` is clipped against its actual convex
    rectangle. This is still arithmetic over metadata, not a CAD-kernel boolean.

    An earlier revision here was its own Liang-Barsky implementation. It agreed
    with the shared one on every case tried except the boundary, where it returned
    0.0 while `_segment_clip_extent` documents "inclusive at the boundary (a touch
    is a hit)" — so a stroke lying exactly on a label's edge was a hit for one
    lint check and a miss for another, in the same module. One predicate, and the
    shared convention wins: CLAUDE.md asks for a shared pass extended rather than
    a copy, and a boundary is a measure-zero case not worth a second answer to.
    """
    polygon = _region_polygon(region)
    if polygon is not None:
        return _length_inside_polygon(segment, polygon)
    box = _region_box(region)
    if box is None:
        return 0.0
    clipped = _segment_clip_extent(segment[0], segment[1], box, 0.0)
    if clipped is None:
        return 0.0
    return float(math.hypot(clipped[2] - clipped[0], clipped[3] - clipped[1]))


def _point_in_region(point, region) -> bool:
    polygon = _region_polygon(region)
    if polygon is None:
        box = _region_box(region)
        return bool(
            box is not None
            and box[0] - 1e-9 <= point[0] <= box[2] + 1e-9
            and box[1] - 1e-9 <= point[1] <= box[3] + 1e-9
        )
    area2 = sum(
        vertex[0] * polygon[(index + 1) % len(polygon)][1]
        - polygon[(index + 1) % len(polygon)][0] * vertex[1]
        for index, vertex in enumerate(polygon)
    )
    orientation = 1.0 if area2 > 0.0 else -1.0
    return all(
        orientation
        * (
            (polygon[(index + 1) % len(polygon)][0] - vertex[0]) * (point[1] - vertex[1])
            - (polygon[(index + 1) % len(polygon)][1] - vertex[1]) * (point[0] - vertex[0])
        )
        >= -1e-9
        for index, vertex in enumerate(polygon)
    )


def _shared_endpoint(left, right):
    for first in left:
        for second in right:
            if math.hypot(first[0] - second[0], first[1] - second[1]) <= 1e-9:
                return ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)
    return None


def crossing_length(segments, label_region) -> float:
    """The longest connected stroke path running through *label_region*.

    Connected pieces are one visual stroke even when the renderer stores them as
    adjacent segments, so their clipped lengths add. Disjoint pieces do not add:
    several independent corner grazes must not combine to clear
    ``MIN_CROSSING_MM``. Segments are joined only at an endpoint that lies inside
    the label region; pieces that happen to connect elsewhere on the sheet remain
    separate crossings here.

    Takes the segments rather than the annotation so the caller can compute them
    once per item. `segments` is a property that rebuilds its list from the
    annotation's location on every read, and this is called inside an O(n²) loop
    (#161).
    """
    if _region_box(label_region) is None:
        return 0.0
    segments = tuple(segments)
    lengths = [length_inside(segment, label_region) for segment in segments]
    active = [index for index, length in enumerate(lengths) if length > 0.0]
    if not active:
        return 0.0
    parent = {index: index for index in active}

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for offset, left in enumerate(active):
        for right in active[offset + 1 :]:
            joint = _shared_endpoint(segments[left], segments[right])
            if joint is not None and _point_in_region(joint, label_region):
                union(left, right)
    totals: dict[int, float] = {}
    for index in active:
        root = find(index)
        totals[root] = totals.get(root, 0.0) + lengths[index]
    return max(totals.values(), default=0.0)


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


def _polygon_of(item, seen=None, report=False):
    try:
        polygon = getattr(item, "label_polygon", None)
    except Exception as exc:  # noqa: BLE001 — duck-typed items may misbehave
        if report:
            _warn_once(
                f"{type(item).__name__}: reading label_polygon raised "
                f"{type(exc).__name__}; its exact label region is unavailable",
                seen,
                key=("label_polygon", id(item)),
            )
        return None
    if polygon is None:
        return None
    if _is_polygon(polygon):
        return tuple((float(point[0]), float(point[1])) for point in polygon)
    if report:
        _warn_once(
            f"{type(item).__name__}: label_polygon is not an ordered convex polygon; "
            "its exact label region is unavailable",
            seen,
            key=("badpolygon", id(item)),
        )
    return None


def _polygon_matches_box(polygon, box) -> bool:
    """Whether the polygon is located with the AABB it claims to refine."""
    xs, ys = zip(*polygon, strict=True)
    actual = (min(xs), min(ys), max(xs), max(ys))
    return all(abs(found - expected) <= 1e-6 for found, expected in zip(actual, box, strict=True))


def crossable_region(
    label_box,
    *,
    item=None,
    segments=None,
    seen=None,
    report=False,
) -> LabelRegion | None:
    """Return the honest clipping region for *item*'s label, or ``None``.

    Exact ``label_polygon`` metadata wins. Axis-aligned labels use their AABB.
    A diagonal dimension with no exact polygon is excluded because clipping its
    inflated AABB produces known false positives; the public dimension-line
    segments classify it, never the optional private ``_dw_spec``.
    """
    if not _is_box(label_box) or shorter_side(label_box) <= 0.0:
        if report and not _is_box(label_box):
            _warn_once(
                f"{type(item).__name__ if item is not None else 'annotation'}: "
                f"label_bbox is not a box ({label_box!r}); it is excluded from "
                "annotation_ink_overlap",
                seen,
                key=("badbox", id(item)),
            )
        return None
    box = (
        float(label_box[0]),
        float(label_box[1]),
        float(label_box[2]),
        float(label_box[3]),
    )
    polygon = _polygon_of(item, seen, report)
    if polygon is not None and _polygon_matches_box(polygon, box):
        return LabelRegion(box=box, polygon=polygon)
    if polygon is not None and report:
        _warn_once(
            f"{type(item).__name__}: label_polygon does not match its current label_bbox; "
            "the exact label region is unavailable",
            seen,
            key=("stale-polygon", id(item)),
        )
    if not _is_diagonal_dimension(item, segments):
        return LabelRegion(box=box)
    if report:
        width = label_box[2] - label_box[0]
        height = label_box[3] - label_box[1]
        _warn_once(
            f"label box {width:.3f} x {height:.3f} mm belongs to a diagonal "
            "dimension but has no exact label_polygon; line-work through it is "
            "not measured (annotation_ink_overlap)",
            seen,
            key=("untight", tuple(label_box)),
        )
    return None


def warn_if_untight(label_box, seen=None, item=None, segments=None) -> bool:
    """Whether *label_box* has an honest clipping region, reporting exclusions.

    Call this **once per item**, not once per pair: `_label_bbox` in
    `structural.py` memoises its own warning for exactly this reason (#711), since
    an unmemoised one floods the log O(n²) on a single bad annotation.

    Loud rather than silent. An untight box makes its label uncrossable, so the
    reader gets no finding for line-work through it, and a check that quietly
    stops covering an annotation is the #701 failure this module keeps meeting.
    """
    return (
        crossable_region(
            label_box,
            item=item,
            segments=segments,
            seen=seen,
            report=True,
        )
        is not None
    )


def is_tight(label_box, item=None) -> bool:
    """Whether *label_box* has geometry tight enough for honest clipping.

    See :data:`DIAGONAL_DIM_TOLERANCE_MM`. The box must be a box, and *item* — the
    annotation that owns it — must not be a dimension whose own line runs on a
    diagonal, because then the box is the bounding box of a rotated rectangle
    rather than a fit around the glyphs.

    An *item* of ``None`` asks only whether the box is well-formed. Depends on
    nothing but the pair itself, so a verdict cannot change because of an
    unrelated annotation elsewhere on the sheet.
    """
    return crossable_region(label_box, item=item) is not None


def _is_diagonal_dimension(item, segments=None) -> bool:
    """Whether *item* is a dimension whose line runs neither horizontally nor
    vertically, so its label is drawn at an angle.

    ``Dimension`` documents ``measured_length`` and ``segments`` as public
    metadata. Its first segment is a piece of the dimension line (the witnesses
    follow), so that direction remains authoritative after construction and
    location transforms. An object without that public dimension surface is not
    classified as a dimension at all.
    """
    try:
        measured = getattr(item, "measured_length", None)
        if not isinstance(measured, (int, float)):
            return False
        available = tuple(segments) if segments is not None else segments_of(item)
        if not available:
            return False
        p1, p2 = available[0]
        dx, dy = abs(p2[0] - p1[0]), abs(p2[1] - p1[1])
    except Exception:  # noqa: BLE001 — duck-typed items may misbehave
        return False
    return bool(min(dx, dy) > DIAGONAL_DIM_TOLERANCE_MM)


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
    if _region_box(label_a) is None:
        label_a = None
    if _region_box(label_b) is None:
        label_b = None
    found = [
        Crossing(
            length=crossing_length(segments_a, label_b),
            label_box=_region_box(label_b),
            crosses_b=True,
        ),
        Crossing(
            length=crossing_length(segments_b, label_a),
            label_box=_region_box(label_a),
            crosses_b=False,
        ),
    ]
    return sorted(
        (crossing for crossing in found if crossing.is_reportable()),
        key=lambda crossing: -crossing.length,
    )
