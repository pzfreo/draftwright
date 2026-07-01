"""Shared annotation-placement helpers (#138 / ADR 0005, P5).

Page-box geometry the passes share: an annotation's bbox (`_anno_box`), the
set of already-placed boxes a candidate must not overprint (`_occupied_boxes`),
and an AABB overlap test (`_box_hits`). Bottom of the annotations DAG.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


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

    Not yet consumed in production — the collect-then-solve strip stage wires this
    in at P1 (#321). Kept additive here so P0 stays behaviour-preserving."""
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
        tn = type(o).__name__
        if tn == "Dimension" or tn in CROSSABLE_TYPES:
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
