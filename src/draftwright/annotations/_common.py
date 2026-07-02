"""Shared annotation-placement helpers (#138 / ADR 0005, P5).

Page-box geometry the passes share: an annotation's bbox (`_anno_box`), the
set of already-placed boxes a candidate must not overprint (`_occupied_boxes`),
and an AABB overlap test (`_box_hits`). Bottom of the annotations DAG.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from build123d_drafting.helpers import Dimension, SafeDimension

from draftwright.layout import StripCandidate, plan_strip

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Escalation:
    """A first-class "could not place this here" signal (ADR 0009 Amendment 1, P5-strand-2).

    Placers *collect* one of these into ``dwg._escalations`` at the point of failure —
    instead of recording a stringly-typed ``*_dropped`` lint code and letting the escalators
    grep for it — and one later resolver pass groups them by ``(view, feature-or-pattern)``,
    picks a remedy per group (ISO pattern-grouped balloon / table / detail / drop), and emits
    the ``*_dropped`` lint codes only for what stays unresolved (so coverage lint + the
    cleanliness ratchet keep working). See the ADR / epic #351.

    The hole callout/location placers emit these (#351 PR-2); the resolver in
    ``annotations/orchestrator.py`` (``_maybe_tabulate_holes``) consumes them, including
    the ISO pattern-grouped balloon fallback for a dropped pattern callout (#351 PR-3).

    Attributes:
        kind:     what could not be placed — ``"callout" | "location" | "slot" | "step" | "pmi"``.
        view:     the owning orthographic view (``None`` for drawing-level).
        feature:  reference to the IR feature / ``HoleRef`` / key it belongs to — carries the
                  pattern membership the resolver groups on (a ``"callout"`` escalation's
                  feature is the dropped group's ``PatternFeature`` when it is a
                  fully-surviving recognised pattern, else ``None``). Left untyped to keep
                  this module a leaf (no dependency on ``model.ir``).
        reason:   why placement failed — ``"strip_full" | "illegible" | "corridor_blocked" | "no_room"``.
        remedies: ranked candidate remedies the resolver may pick, e.g.
                  ``("group_balloon", "table", "detail", "drop")``. Empty = resolver's default ladder.
    """

    kind: str
    view: str | None
    feature: object
    reason: str
    remedies: tuple[str, ...] = field(default_factory=tuple)


def _anno_box(o):
    """Page-space bbox ``(x0, y0, x1, y1)`` of an annotation — its text
    ``label_bbox`` if it has one, else its geometric bounding box; ``None`` if
    neither resolves.  Local mirror of ``make_drawing._anno_bbox`` (annotate sits
    below make_drawing, so it cannot import from it)."""
    lb = getattr(o, "label_bbox", None)
    if lb is not None:
        return lb
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
        return None


def _occupied_boxes(dwg):
    """Boxes of already-placed annotations a location dim must not overprint:
    every label-bearing annotation (hole callouts, other dims) plus the section
    hatch.  Bare centrelines/leaders are excluded — those legitimately cross a
    dimension and lint does not flag them."""
    boxes = []
    for name, o in dwg.iter_annotations():
        if getattr(o, "label_bbox", None) is None and name != "section_hatch":
            continue
        bb = _anno_box(o)
        if bb is not None:
            boxes.append(bb)
    return boxes


def _geom_box(o):
    """Full rendered-geometry bbox ``(x0, y0, x1, y1)`` of an annotation — leader
    shafts and arrow tips, dimension witness/extension lines, centrelines, hatch —
    *not* just its label box. ``None`` if it does not bbox cleanly (logged at
    debug: a silently dropped occupant is the wrong failure mode for an occupancy
    model, so the omission is at least observable)."""
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception as exc:  # noqa: BLE001 — not every annotation bbox-es cleanly
        _log.debug("strip occupancy: %s did not bbox (%s); omitted", type(o).__name__, exc)
        return None


CROSSABLE_TYPES = frozenset({"Centerline", "CenterlineCircle", "CenterMark"})
"""Annotation types a *dimension* may legitimately cross (ISO 128): centre lines
and centre marks. A **leader**, by contrast, must avoid them (#305) — so this is a
per-consumer choice, passed as ``crossable`` to :func:`strip_obstacles`."""


def strip_obstacles(dwg, view=None, *, crossable=()):
    """The COMPLETE occupancy for strip placement (ADR 0009): every placed
    annotation's full rendered footprint, optionally restricted to *view*, minus
    any annotation whose type name is in *crossable* (things this particular
    consumer may legitimately overlap — e.g. a location dim crosses a centre line
    but a leader does not; see :data:`CROSSABLE_TYPES`).

    Unlike :func:`_occupied_boxes` (label boxes only, with bare centrelines
    excluded), this captures the geometry a label box hides — leader shafts and
    arrow tips, dimension witness/extension lines, centrelines, and the section
    hatch. That hidden geometry is the 'invisible occupant' class behind the
    recurring strip overlaps (#133/#225/#305): a placer that consults only label
    boxes commits a callout into space a leader or extension line already crosses.

    *view* scoping keeps this view's own annotations **and** drawing-level obstacles
    that no orthographic view owns (the section hatch, title block, …) — those a
    strip placer must still avoid — and drops only the *other* ortho views' blocks
    (which compose-then-pack keeps disjoint, ADR 0004). The section hatch
    (``view_of`` ``None``) is therefore present in every per-view query, the way
    :func:`_occupied_boxes` special-cased it; restricting it to ``view=None`` would
    re-open the very blind spot this closes.

    Boxes are AABBs ``(x0, y0, x1, y1)`` (use with :func:`_box_hits`) — intentionally
    conservative: a diagonal leader's box over-claims its empty triangle (ADR 0009
    notes angled leaders weaken the bound), which only ever over-avoids, never
    under-avoids.

    The occupancy source for the collect-then-solve carve — every migrated renderer's
    ``place_strip_candidates`` call wires this in (#321/#150/P3)."""
    boxes = []
    for name, o in dwg.iter_annotations():
        if view is not None:
            owner = dwg.view_of(name)
            if owner is not None and owner != view:
                continue  # owned by a different ortho view → its own (disjoint) block
        if type(o).__name__ in crossable:
            continue  # this consumer may cross it (centre lines/marks for a dim)
        bb = _geom_box(o)
        if bb is not None:
            boxes.append(bb)
    return boxes


def strip_free_span(strip):
    """``(lo, hi, inner)`` page coords of *strip* along its stacking axis, where
    *inner* is the end nearest the view edge (the first tier a dim fills). Reads the
    live ``outer_limit`` so an orchestrator reservation (#133) stays honoured. The
    cursor-free counterpart of :meth:`Strip.allocate` — a collect-then-solve pass
    (ADR 0009) reads these bounds and carves, rather than advancing a mutable cursor."""
    near = strip.anchor + strip.direction * strip.gap
    if strip.direction == 1:
        return near, strip.outer_limit, near  # lo, hi, inner (=lo)
    return strip.outer_limit, near, near  # lo, hi, inner (=hi)


def carve_free_segments(lo, hi, intervals, pad):
    """``[lo, hi]`` minus every obstacle interval inflated by *pad*, merged and
    complemented — the option-(c) occupancy carve (ADR 0009 / #321). A dim is then
    spaced only WITHIN a clear segment, so it can never overprint a placed occupant
    (a leader shaft, the section hatch, a location-dim tier): the old per-tier
    ``allocate`` + post-hoc ``_box_hits`` retry becomes structural. *intervals* are
    ``(a, b)`` pairs along the strip's stacking axis (e.g. ``(box_y0, box_y1)`` for a
    below strip). Returns a list of ``(seg_lo, seg_hi)`` free segments, lo→hi."""
    blocked = []
    for a0, b0 in intervals:
        a1, b1 = max(lo, a0 - pad), min(hi, b0 + pad)
        if b1 > a1:
            blocked.append((a1, b1))
    blocked.sort()
    merged: list[list[float]] = []
    for a0, b0 in blocked:
        if merged and a0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b0)
        else:
            merged.append([a0, b0])
    free, cur = [], lo
    for a0, b0 in merged:
        if a0 > cur:
            free.append((cur, a0))
        cur = b0
    if cur < hi:
        free.append((cur, hi))
    return free


def corridor_blockers(dwg, view):
    """Boxes of annotations a dimension's *witness corridor* (the span from the view
    edge out to its dim line) must not cross — leaders/callouts, the section hatch, the
    title block: everything that is neither a datum-chained ``Dimension`` nor a
    crossable centre line/mark (:data:`CROSSABLE_TYPES`).

    :func:`strip_obstacles` carves the 1-D strip so a dim *line* clears every occupant,
    but a right/below dim also occupies the 2-D corridor back to the view — and a bore
    callout's leader sitting in that corridor is crossed however far out the line is
    placed (the #133/#225/#305 leader class, in its witness-corridor form). A dim whose
    full footprint hits one of these must route to another view, not overprint it (ISO
    128). Sibling location/envelope dims are excluded: they chain off the shared datum
    and legitimately share the corridor. View scoping mirrors :func:`strip_obstacles`
    (this view's own annotations + drawing-level occupants that no ortho view owns)."""
    boxes = []
    for name, o in dwg.iter_annotations():
        if view is not None:
            owner = dwg.view_of(name)
            if owner is not None and owner != view:
                continue
        if isinstance(o, (Dimension, SafeDimension)) or type(o).__name__ in CROSSABLE_TYPES:
            continue  # datum-chained dims share the corridor; centre lines are crossable
        bb = _geom_box(o)
        if bb is not None:
            boxes.append(bb)
    return boxes


def _box_hits(bb, boxes):
    """True when ``bb`` overlaps any box in ``boxes`` (strict AABB test). Slightly
    more conservative than the within-view label lint (which tolerates a 0.5 mm
    sliver): a touch counts as a hit, so a candidate never overprints — at worst
    it is dropped a hair early."""
    if bb is None:
        return False
    for c in boxes:
        if min(bb[2], c[2]) > max(bb[0], c[0]) and min(bb[3], c[3]) > max(bb[1], c[1]):
            return True
    return False


def _segment_hits_box(p1, p2, box) -> bool:
    """True when line segment *p1*-*p2* intersects axis-aligned *box*
    ``(x0, y0, x1, y1)`` — the precise counterpart of :func:`_box_hits` for a
    genuinely diagonal shaft (ADR 0009 P4/#318, #305: "a diagonal leader's box
    over-claims its empty triangle"). Boxing an angled segment for a coarse
    reject is correct and cheap; boxing it for the final accept/reject decision
    over-avoids free space a real diagonal never crosses. Endpoint-in-box and
    the 4 edge-crossing cases (a standard segment/AABB test).

    The crossing test uses strict inequality deliberately, not an inclusive
    ``<= 0`` — an inclusive test also treats a segment merely COLLINEAR with
    one of the box's (infinite) edge lines as a hit, regardless of whether it
    is anywhere near the box along that line (verified: a vertical segment at
    ``x == box.x0`` but far outside ``[y0, y1]`` false-hits under `<=`). That
    false-positive class is common (any axis-aligned shaft sharing an X or Y
    coordinate with an edge), unlike the strict form's own known gap — a
    segment passing exactly through two opposite corners is a measure-zero
    event for the continuous, non-integer leader positions this computes over
    (review finding, #351 P5 strand 3: tried the inclusive form, reverted)."""
    x0, y0, x1, y1 = box

    def _inside(p):
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    if _inside(p1) or _inside(p2):
        return True

    def _cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def _seg_seg(a1, a2, b1, b2):
        d1, d2 = _cross(b1, b2, a1), _cross(b1, b2, a2)
        d3, d4 = _cross(a1, a2, b1), _cross(a1, a2, b2)
        return ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
            (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
        )

    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    return any(_seg_seg(p1, p2, corners[i], corners[(i + 1) % 4]) for i in range(4))


def place_strip_candidates(dwg, strip, view, axis, cands, tier, *, force=False):
    """Collect-then-solve placement of location/feature dims on one strip (ADR 0009).
    The single shared strip placer that retires the ``Strip.allocate`` cursor (#150,
    P3): each candidate in *cands* — an ``(name, build(pos)->dim)`` pair — is spaced by
    one :func:`plan_strip` solve per free segment of the CARVED strip (`strip` carved
    around :func:`strip_obstacles`), replacing the per-dim ``allocate`` + ``_box_hits``
    tier-retry. *tier* is the label height (sets the inter-dim gap ``tier + spacing``).

    Occupancy is THIS view's own placed annotations plus the drawing-level obstacles no
    ortho view owns (the section hatch), recomputed per call so a dim placed earlier in
    the pass is avoided; other ortho views are disjoint (ADR 0004) and excluded so their
    rows never over-carve this strip. This makes the old post-hoc collision retry
    structural: a dim can never land on a bore-callout leader shaft the label-only
    occupancy missed (#133/#225/#305).

    A right/below dim also occupies the 2-D corridor back to the view edge, which the
    1-D strip carve cannot represent: a leader in that corridor is crossed no matter how
    far out the dim line lands. By default such a placement is rejected so the caller can
    route the dim to the other view (its disjoint block cannot cross this leader).
    ``force=True`` skips that corridor check — the caller's last resort when no view took
    the dim cleanly: keep it on its natural view and accept the (same-feature) leader
    crossing rather than drop a real dimension (policy B). Candidates that find no strip
    tier AT ALL are still returned (a physically full strip — the caller records the
    genuine drop)."""
    if strip is None or not cands:
        return list(cands)
    lo, hi, inner = strip_free_span(strip)
    # Reserve the outermost label's height at the strip boundary. plan_strip bounds the
    # dim-LINE position, but the label extends `tier` OUTWARD from it — so without this
    # the last tier's label overshoots outer_limit (into the iso view / page margin),
    # unlike the old Strip.allocate which checked `start + tier <= outer_limit` (#338
    # review). The strip edge is not an obstacle (obstacles carry their own footprint +
    # pad), so only the boundary needs it; obstacle-bounded segments are unaffected.
    if inner == lo:
        hi -= tier
    else:
        lo += tier
    idx = 1 if axis == "y" else 0
    perp = 0 if axis == "y" else 1  # the axis the dims do NOT stack along
    pad = tier + strip.spacing  # min separation between stacked dim lines
    # Perpendicular band of these candidates. The 1-D carve projects obstacles onto the
    # stacking axis only, so an obstacle on ANOTHER strip of this view — disjoint in the
    # perpendicular axis, never actually touching — would falsely block (e.g. the overall
    # width dim below the view blocking a slot-width dim on the right strip). Filter such
    # obstacles out first. The perpendicular extent is independent of the tier position,
    # so a single probe build per candidate suffices; the corridor check below already
    # uses the full 2-D box, so it needs no such filter.
    pbands = [
        (b[perp], b[perp + 2]) for _n, build in cands if (b := _geom_box(build(lo))) is not None
    ]
    occupied = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
    if pbands:
        band_lo, band_hi = min(p[0] for p in pbands), max(p[1] for p in pbands)
        occupied = [b for b in occupied if b[perp] < band_hi and b[perp + 2] > band_lo]
    blockers = () if force else corridor_blockers(dwg, view)
    segs = carve_free_segments(lo, hi, [(b[idx], b[idx + 2]) for b in occupied], pad)
    # Fill innermost-first (nearest the view), matching the old cursor's stack order.
    segs.sort(key=lambda s: abs((s[0] if inner == lo else s[1]) - inner))
    todo = list(cands)
    for seg_lo, seg_hi in segs:
        if not todo:
            break
        cap = int((seg_hi - seg_lo) / pad) + 1
        take, todo = todo[:cap], todo[cap:]
        nat = seg_lo if inner == lo else seg_hi
        anch = (0.0, nat) if axis == "y" else (nat, 0.0)
        # Keys order the tiers so the FIRST candidate lands on the inner tier: for an
        # inner=lo strip that is the lowest position (ascending keys); for a below strip
        # (inner=hi) it is the highest, so the keys reverse.
        triples = [
            (
                StripCandidate(
                    f"{(k if inner == lo else len(take) - 1 - k):04d}", anch, (tier, tier)
                ),
                nb,
            )
            for k, nb in enumerate(take)
        ]
        res = plan_strip([sc for sc, _ in triples], seg_lo, seg_hi, pad, axis=axis)
        for sc, (name, build) in triples:
            pos = res.placed.get(sc.key)
            if pos is None:  # segment over its estimated capacity (shouldn't occur)
                todo.append((name, build))
                continue
            dim = build(pos)
            if not force and _box_hits(_geom_box(dim), blockers):  # corridor crosses a leader
                todo.append((name, build))
                continue
            dwg.add(dim, name, view=view)
    return todo


def carve_free_position(dwg, strip, view, axis, tier, perp_span, *, outermost=False):
    """The single free tier POSITION on *strip* at which a dim of height *tier* spanning
    *perp_span* ``(lo, hi)`` on the perpendicular axis clears every placed obstacle in
    *view* — the innermost (nearest the view) tier by default, or the outermost fitting
    one when *outermost*. Returns the dim-line page coord, or None if the strip is full.

    The position-returning counterpart of :func:`place_strip_candidates` (which batches,
    builds and adds): a caller that needs a dim's assigned position BEFORE building the
    next — the height-ladder leapfrog chain, where each step dim's witness base is the
    previous dim's line — uses this. Same carve: outer-label tier reservation, the
    perpendicular-band filter (*perp_span* drops obstacles disjoint from this dim's own
    perpendicular extent), and innermost-first fill. No corridor check (a single-strip
    ladder has no alternate view to route to; obstacle tiers are still avoided)."""
    if strip is None:
        return None
    lo, hi, inner = strip_free_span(strip)
    idx = 1 if axis == "y" else 0
    perp = 0 if axis == "y" else 1
    pad = tier + strip.spacing
    band_lo, band_hi = perp_span
    occ = [
        b
        for b in strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
        if b[perp] < band_hi and b[perp + 2] > band_lo
    ]
    segs = carve_free_segments(lo, hi, [(b[idx], b[idx + 2]) for b in occ], pad)
    # A segment holds the dim iff it is at least `tier` wide (the label height). This IS
    # the outer-label reservation — inclusive at the boundary (a strip exactly `gap+tier`
    # wide fits one dim, as the old `allocate` did) — so it must NOT be combined with a
    # separate `hi -= tier` pull-in, which would double-reserve and drop that dim.
    fitting = [s for s in segs if s[1] - s[0] >= tier - 1e-9]
    if not fitting:
        return None
    if inner == lo:  # inner edge = seg lo; outermost = the segment reaching furthest out
        seg = max(fitting, key=lambda s: s[1]) if outermost else min(fitting, key=lambda s: s[0])
        return seg[0]
    seg = min(fitting, key=lambda s: s[0]) if outermost else max(fitting, key=lambda s: s[1])
    return seg[1]
