"""Shared annotation-placement helpers (#138 / ADR 0005, P5).

Page-box geometry the passes share: an annotation's bbox (`_anno_box`), the
set of already-placed boxes a candidate must not overprint (`_occupied_boxes`),
and an AABB overlap test (`_box_hits`). Bottom of the annotations DAG.
"""

from __future__ import annotations


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
    *not* just its label box. ``None`` if it does not bbox cleanly."""
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
        return None


def strip_obstacles(dwg, view=None):
    """The COMPLETE occupancy for strip placement (ADR 0009): every placed
    annotation's full rendered footprint, optionally restricted to *view*.

    Unlike :func:`_occupied_boxes` (label boxes only, with bare centrelines
    excluded), this captures the geometry a label box hides — leader shafts and
    arrow tips, dimension witness/extension lines, centrelines, and the section
    hatch. That hidden geometry is the 'invisible occupant' class behind the
    recurring strip overlaps (#133/#225/#305): a placer that consults only label
    boxes commits a callout into space a leader or extension line already crosses.

    Returns a list of ``(x0, y0, x1, y1)`` AABBs (use with :func:`_box_hits`).

    Not yet consumed in production — the collect-then-solve strip stage wires this
    in at P1 (#321). Kept additive here so P0 stays behaviour-preserving."""
    boxes = []
    for name, o in dwg.iter_annotations():
        if view is not None and dwg.view_of(name) != view:
            continue
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
