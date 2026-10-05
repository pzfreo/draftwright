"""Cheap page-space geometry for annotation placement.

Leader footprints, decomposed occupancy, centerline-label clearance, and the
one-dimensional carve are shared by the corridor solver and rendering passes.
"""

from __future__ import annotations

import logging

from draftwright._geometry import _boxes_overlap, _segment_crosses_box
from draftwright.linting.structural import _ann_box, _centerline_extent

# Preserve the existing occupancy diagnostic's logger category.
_log = logging.getLogger("draftwright.annotations._common")


def _geom_box(o, cache=None):
    """Full rendered-geometry bbox ``(x0, y0, x1, y1)`` of an annotation — leader
    shafts and arrow tips, dimension witness/extension lines, centrelines, hatch —
    *not* just its label box. ``None`` if it does not bbox cleanly (logged at
    debug: a silently dropped occupant is the wrong failure mode for an occupancy
    model, so the omission is at least observable).

    With *cache* (a drawing's :attr:`~draftwright.drawing.Drawing.box_cache`) the
    measurement goes through lint's ``_ann_box`` memo instead of straight to OCC, so
    an annotation is boxed once per build rather than once here and again in lint
    (#1138). Same memo, not a parallel one — its identity+location-token entries are
    what make sharing safe, and lint's prune is what keeps it from leaking. Without a
    cache the behaviour is unchanged, which keeps the duck-typed callers and the
    tests that call this bare working."""
    if cache is not None:
        return _ann_box(o, cache)
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception as exc:  # noqa: BLE001 — not every annotation bbox-es cleanly
        _log.debug("strip occupancy: %s did not bbox (%s); omitted", type(o).__name__, exc)
        return None


def _box_outside_explicit_drawable_bounds(dwg, box, *, tolerance=1e-6):
    """Reject complete ink outside a real drawing's declared content rectangle.

    Lightweight placement probes without explicit page geometry intentionally have
    no page constraint; guessing margins from their dimensions would invent one.
    """
    page = getattr(dwg, "drawable_bounds", None)
    return page is not None and any(
        (
            box[0] < page[0] - tolerance,
            box[1] < page[1] - tolerance,
            box[2] > page[2] + tolerance,
            box[3] > page[3] + tolerance,
        )
    )


def _first_box_conflict(box, named_obstacles):
    """Return the first hard-obstacle reason, preserving caller-specified order."""
    for reason, obstacles in named_obstacles:
        if _box_hits(box, obstacles):
            return reason
    return None


def leader_callout_geometry(tip, elbow, draft, *, text_side="auto", callout_box=None):
    """Exact cheap label box and line segments for a callout-bearing ``Leader``.

    This is the position-dependent subset of :func:`leader_footprint` needed by
    the shared feature-leader solve.  It mirrors the helper's two input edges and
    the translation of the already-measured callout sketch, without constructing
    the arrow/shelf OCC faces.  ``None`` matches the helper's forced-side guard.
    """

    gap = draft.pad_around_text
    if text_side == "auto":
        shelf_dir = 1.0 if elbow[0] >= tip[0] else -1.0
    else:
        shelf_dir = 1.0 if text_side == "right" else -1.0
    shelf_end = (elbow[0] + shelf_dir * gap, elbow[1])
    segments = (
        ((float(tip[0]), float(tip[1])), (float(elbow[0]), float(elbow[1]))),
        ((float(elbow[0]), float(elbow[1])), (float(shelf_end[0]), float(shelf_end[1]))),
    )

    label_box = None
    if callout_box is not None:
        cw = callout_box[2] - callout_box[0]
        ch = callout_box[3] - callout_box[1]
        if shelf_dir > 0:
            cx0, cx1 = shelf_end[0], shelf_end[0] + cw
        else:
            cx0, cx1 = shelf_end[0] - cw, shelf_end[0]
        cy0, cy1 = elbow[1] - ch / 2.0, elbow[1] + ch / 2.0
        label_box = (cx0, cy0, cx1, cy1)
        if text_side != "auto" and _segment_crosses_box(
            (tip[0], tip[1]), (elbow[0], elbow[1]), label_box
        ):
            return None
    return label_box, segments


def analytical_leader_lands_clear(
    candidate,
    obstacles,
    silhouette,
    page,
    *,
    label,
    geom_clear=False,
) -> bool:
    """Apply the producer's label/full-ink clearance floor to an unbuilt leader."""
    label_box = candidate.label_box
    if not label or label_box is None:
        return False
    check_box = label_box
    if geom_clear:
        xs = [label_box[0], label_box[2]]
        ys = [label_box[1], label_box[3]]
        for polygon in candidate.ink_polygons:
            xs.extend(point[0] for point in polygon)
            ys.extend(point[1] for point in polygon)
        check_box = (min(xs), min(ys), max(xs), max(ys))
    return not (
        _box_hits(check_box, obstacles)
        or _box_hits(label_box, [silhouette])
        or label_box[0] < page[0]
        or label_box[1] < page[1]
        or label_box[2] > page[2]
        or label_box[3] > page[3]
    )


def leader_footprint(tip, elbow, draft, *, text_side="auto", callout_box=None):
    """Analytical page-mm AABB ``(x0, y0, x1, y1)`` of the :class:`Leader` that
    ``Leader(tip, elbow, label="", draft, text_side=…, callout=…)`` would build —
    WITHOUT constructing any OCC geometry (#1138, the #602 pattern applied to
    leaders).

    Probing one callout leader's footprint by *building* it costs 0.12–0.21 s —
    6–10 % of a whole drawing — because a ``Leader`` fuses an ``Arrow``, a swept
    shelf rectangle and the callout sketch before anything can be measured. The
    footprint is arithmetic given the callout's own box, which is measured once
    and memoised rather than re-measured per candidate position.

    Mirrors ``helpers.Leader.__init__``: the shelf runs ``pad_around_text`` from
    the elbow in ``shelf_dir`` (``text_side``, or tip→elbow's horizontal sense when
    ``"auto"``), and the callout hangs at the shelf end — its near edge on the
    shelf end, centred vertically on the elbow. The shaft is inflated by half of
    whichever of *arrow_length* / *line_width* dominates, and over-claiming is the
    safe direction (this box selects which obstacles a carve considers, so a box
    that is too big keeps an irrelevant obstacle, while one that is too small drops
    a real one).

    Returns ``None`` where ``Leader`` itself would raise — a forced *text_side*
    that runs the shaft through the label — so callers omit the candidate exactly
    as the built-geometry probe's except-handler did.
    """
    geometry = leader_callout_geometry(
        tip,
        elbow,
        draft,
        text_side=text_side,
        callout_box=callout_box,
    )
    if geometry is None:
        return None
    label_box, segments = geometry
    shelf_end_x = segments[1][1][0]

    # The arrowhead bounds the shaft only while it is the wider of the two; a style with a
    # heavy line and a small arrow inverts that, and a diagonal shaft then pushes its stroke
    # past an arrow-only box. Pad by whichever dominates.
    half_shaft = max(draft.arrow_length, draft.line_width) / 2.0
    xs = [tip[0] - half_shaft, tip[0] + half_shaft, elbow[0] - half_shaft, elbow[0] + half_shaft]
    ys = [tip[1] - half_shaft, tip[1] + half_shaft, elbow[1] - half_shaft, elbow[1] + half_shaft]

    half_line = draft.line_width / 2.0
    xs += [min(elbow[0], shelf_end_x) - half_line, max(elbow[0], shelf_end_x) + half_line]
    ys += [elbow[1] - half_line, elbow[1] + half_line]

    if label_box is not None:
        xs += [label_box[0], label_box[2]]
        ys += [label_box[1], label_box[3]]

    return (min(xs), min(ys), max(xs), max(ys))


def clear_label_of_centerlines(label_bbox, centerlines, gap):
    """``label_offset_x`` so *label_bbox* clears every crossing centre-line-family
    annotation in *centerlines* (#129) — both a turned part's thin vertical/
    horizontal axis :class:`Centerline` and a bolt-circle's wide
    :class:`CenterlineCircle`.

    Two steps. First, decide whether the label's UNSHIFTED position is already
    fine: a crossing only counts past 0.5 mm of depth (a thin line, off its
    midpoint) or overlap (a wide bbox) — the same threshold
    :func:`draftwright.linting.structural.lint_drawing`'s
    ``label_centerline_overlap`` flags — so a marginal graze the lint would not
    flag leaves the label untouched. If nothing crosses by that measure, return
    0.0 without moving anything.

    Otherwise, every centre line the label's row (Y-extent) genuinely reaches —
    *regardless of its own individual X depth* — becomes one forbidden interval
    for the label's LEFT edge (`x`: the label occupies `[x, x+width]`, so it
    clears a crossing's `[c0, c1]` by *gap* exactly when `x` is outside
    `(c0-gap-width, c1+gap)`), carved from a generous span via
    :func:`carve_free_segments` — the same occupancy-carve primitive this
    module's other placers use for the dimension *line* itself. Including every
    reachable centre line here, not just the ones individually past the 0.5 mm
    threshold, matters: once the label is going to move at all, a centre line
    it barely grazed at the ORIGINAL position can end up squarely inside the
    NEW one — this joint carve accounts for all of them in one pass, so moving
    to clear one can never expose a violation against another (the bug class
    an earlier per-centre-line local-search design had (#129). A
    thin **horizontal** line can't be cleared by an X shift at all, so it is
    excluded and left to the lint/repair safety net."""
    if label_bbox is None:
        return 0.0
    lmin_x, lmin_y, lmax_x, lmax_y = label_bbox
    label_w = lmax_x - lmin_x
    extents = []
    natural_violation = False
    for cl in centerlines:
        if not getattr(cl, "is_centerline", False):
            continue
        try:
            cl_min_x, cl_min_y, cl_max_x, cl_max_y = _centerline_extent(cl)
        except Exception:
            continue
        if cl_max_y - cl_min_y < 0.1:
            continue  # a horizontal line's clash can't be fixed by an X shift
        oy = min(lmax_y, cl_max_y) - max(lmin_y, cl_min_y)
        if oy <= 0.5:
            continue  # no real vertical overlap — matches the lint's own oy>0.5 gate
        extents.append((cl_min_x, cl_max_x))
        if cl_max_x - cl_min_x < 0.1:
            cl_x = (cl_min_x + cl_max_x) / 2.0
            ox = min(cl_x - lmin_x, lmax_x - cl_x) if lmin_x < cl_x < lmax_x else 0.0
        else:
            ox = min(lmax_x, cl_max_x) - max(lmin_x, cl_min_x)
        natural_violation = natural_violation or ox > 0.5
    if not natural_violation:
        return 0.0  # already clear enough that the lint would not flag it
    forbidden = [(cl_min_x - gap - label_w, cl_max_x + gap) for cl_min_x, cl_max_x in extents]
    # A span this wide beyond every forbidden edge is free even if every interval
    # merged into one contiguous run (their combined width can never exceed the
    # sum of the individual widths) — carve_free_segments is therefore guaranteed
    # a non-empty result; no defensive empty-result fallback needed.
    total_w = sum(f1 - f0 for f0, f1 in forbidden) + label_w + 10.0
    lo = min([lmin_x, *(f0 for f0, _ in forbidden)]) - total_w
    hi = max([lmax_x, *(f1 for _, f1 in forbidden)]) + total_w
    segs = carve_free_segments(lo, hi, forbidden, 0.0)
    target_x = min((min(max(lmin_x, s0), s1) for s0, s1 in segs), key=lambda x: abs(x - lmin_x))
    return target_x - lmin_x


def occupancy_boxes(o, stroke_pad=None):
    """*o*'s occupancy as a list of AABBs — decomposed, not one hull (#685).

    An annotation's rendered hull includes large EMPTY corner regions (a dimension's
    ink is L-shaped: witness lines + a dim-line band), and helpers ≥0.14's honest
    tight-span rendering made those hulls big enough to collide structurally at view
    corners while the inks stay disjoint. For an annotation exposing ``.segments``
    (helpers ≥0.14 reports the drawn line pieces: witness lines, shafts, box
    strokes), return one box per stroke — inflated by ``_STROKE_PAD`` to cover line
    width plus the arrowheads at stroke junctions — plus its ``label_bbox``.
    Anything else (leaders, hatch, title block) keeps its single hull box.

    Consumers treat the result exactly like a list of hull boxes; the
    perpendicular-band filter in the carve then does the rest — a witness sliver
    carves only its own sliver, and an out-of-band dim-line band stops blocking a
    sibling strip's corner entirely.
    """
    segs = getattr(o, "segments", None)
    if not segs:
        b = _geom_box(o)
        return [b] if b is not None else []
    pad = _STROKE_PAD if stroke_pad is None else stroke_pad
    out = [
        (min(x0, x1) - pad, min(y0, y1) - pad, max(x0, x1) + pad, max(y0, y1) + pad)
        for (x0, y0), (x1, y1) in segs
    ]
    lb = getattr(o, "label_bbox", None)
    if lb is not None:
        out.append((lb[0], lb[1], lb[2], lb[3]))
    return out


# Fallback stroke inflation for decomposed occupancy: line_width/2 (~0.08) plus the
# arrowhead half-width at stroke junctions at DEFAULT presets. Arrow geometry scales
# with font_size (#688), so callers that know the draft derive the pad as
# max(_STROKE_PAD, draft.arrow_length / 2) — arrow half-LENGTH bounds the head's
# half-width (aspect < 1) AND its protrusion past an inside-arrow shaft trim (al/2).
_STROKE_PAD = 1.2


def carve_free_segments(lo, hi, intervals, pad):
    """``[lo, hi]`` minus every obstacle interval inflated by *pad*, merged and
    complemented — the option-(c) occupancy carve (ADR 2 (was 0009) / #321). A dim is then
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


def _box_hits(bb, boxes):
    """True when ``bb`` overlaps any box in ``boxes`` (strict AABB test,
    :func:`draftwright._geometry._boxes_overlap`). Slightly more conservative
    than the within-view label lint (which tolerates a 0.5 mm sliver): a touch
    does not count as an overlap, so a candidate never overprints — at worst it
    is dropped a hair early."""
    return bb is not None and any(_boxes_overlap(bb, c) for c in boxes)
