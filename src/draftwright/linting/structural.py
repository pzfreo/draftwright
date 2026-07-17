"""structural — duck-typed structural lint of a composed annotation list.

Vendored from ``build123d_drafting.helpers`` (ADR 0007: draftwright owns
linting; helpers is the rendering library). ``lint_drawing`` dispatches by
attribute presence, not type, so it needs no import of the drawing-object
classes. Page bounds are passed explicitly by the caller (``page_bbox``); the
``set_page`` module-global fallback is kept inert here (``_DRAWING_PAGE = None``)
since draftwright threads the page extent directly (see ``Drawing.lint``).
"""

from __future__ import annotations

import re

from build123d import GeomType

from draftwright.linting.issues import LintIssue

# Inert: draftwright always passes page_bbox explicitly (the set_page global
# coupling is severed, ADR 0007). Kept so the vendored body is a faithful copy.
_DRAWING_PAGE = None


def _seg_intersects_rect(p, q, rect) -> bool:
    """True if segment p→q intersects the (min_x, min_y, max_x, max_y) rect.

    Thin wrapper over :func:`_seg_hits_box` (the same Liang–Barsky clip) with no
    padding, kept for call-site readability.
    """
    return bool(_seg_hits_box(p, q, rect, pad=0.0))


def _loc_token(item):
    """A cheap (~14 µs) fingerprint of *item*'s placement, or None if it has no
    location. ``Shape.locate()`` transforms *in place*, so identity alone can't
    detect a relocated live object — the token can."""
    try:
        loc = getattr(item, "location", None)
        return None if loc is None else tuple(loc)
    except Exception:
        return None


def _ann_box(item, cache):
    """*item*'s full rendered bbox as (min_x, min_y, max_x, max_y), or None; memoised.

    An *optimal* ``bounding_box()`` on fused annotation geometry costs ~10 ms —
    the dominant lint cost once the view edges are cached (#602). Every lint
    check that needs a full box goes through this one memo, so an item is
    measured at most once per cache lifetime. Entries are id-keyed and store
    the item itself plus its location token: the identity check means a
    caller-persisted cache can't return a stale box after ``id()`` reuse (same
    pattern as ``_view_edge_entries``), and the token check re-measures an
    object relocated in place via ``.locate()``-style transforms — the engine
    itself always *replaces* (the repair loop's ``_replace_dim`` swaps in a
    freshly built object), but ``Drawing.items`` exposes live shapes. Known
    limit, accepted: a mutation that changes geometry while *preserving*
    ``.location`` (or mutating a location-less duck-typed stand-in) is
    undetectable without hashing topology — which would cost as much as the
    measure this memo avoids — so such objects must be replaced or the cache
    cleared, per the engine-wide discipline above. A failed
    measure is cached as ``None`` — deterministic for unchanged geometry, so
    the affected checks skip the item exactly as their old per-site handlers
    did, without re-raising per lint.
    """
    key = id(item)
    hit = cache.get(key)
    token = _loc_token(item)
    if hit is not None and hit[0] is item and hit[1] == token:
        return hit[2]
    try:
        bb = item.bounding_box()
        box = (bb.min.X, bb.min.Y, bb.max.X, bb.max.Y)
    except Exception:
        box = None
    cache[key] = (item, token, box)
    return box


def _centerline_extent(cl_item, box_cache=None):
    """Return (min_x, min_y, max_x, max_y) for a centreline.

    Prefers the zero-width ``.segments`` (the true centreline) so a thin-faced
    centreline still reads as a zero-width vertical/horizontal line; falls back
    to the rendered ``.bounding_box()`` (which is line_width wide).
    """
    segs = getattr(cl_item, "segments", None)
    if segs:
        xs = [p[0] for s in segs for p in s]
        ys = [p[1] for s in segs for p in s]
        return (min(xs), min(ys), max(xs), max(ys))
    box = _ann_box(cl_item, box_cache if box_cache is not None else {})
    if box is None:
        raise ValueError("centreline bbox unavailable")
    return box


def _item_label(item) -> str:
    """The label lint should use for *item*: its ``.label``, or the explicit
    string attached via :func:`annotate` (``_annotate_label``) when it has none
    — e.g. a vanilla build123d ``ExtensionLine`` that does not retain its
    constructor label."""
    return getattr(item, "label", "") or getattr(item, "_annotate_label", "") or ""


def _label_value(label: str) -> float | None:
    """The dimensional value a dimension/callout label asserts, or ``None``.

    Handles the three label shapes lint compares against measured geometry::

        "12.5", "⌀8.5", "7.5 ±0.1"   -> the leading number   (12.5 / 8.5 / 7.5)
        "4× 20"                       -> a pitch span          (4·20 = 80)
        "4× ⌀8.5", "4× ⌀8.5 THRU"     -> a counted diameter    (8.5, not 4·8.5)

    The diameter/radius prefix on the repeated value is the discriminator: a
    bare ``N× v`` is a span of N pitches, but ``N× ⌀d`` counts d-diameter
    features, so the value is ``d`` itself.
    """
    body = label.split("±")[0].split("+")[0]
    nums = re.findall(r"\d+\.?\d*", body.lstrip("ø⌀Rr"))
    if not nums:
        return None
    rep = re.match(r"\s*(\d+)\s*[×x]\s*([ø⌀Rr]?)\s*(\d+\.?\d*)", label)
    try:
        if rep:
            count, prefix, value = rep.group(1), rep.group(2), rep.group(3)
            return float(value) if prefix else int(count) * float(value)
        return float(nums[0])
    except ValueError:
        return None


def lint_drawing(
    items,
    part_bbox=None,
    page_bbox=None,
    drawing_scale: float = 1.0,
    view_shapes: list | None = None,
    view_edge_cache: dict | None = None,
    ann_box_cache: dict | None = None,
) -> list[LintIssue]:
    """Structural checks on a composed annotation list, duck-typed.

    Dispatch is by attribute presence, not type:

    - leader-like  (``.elbow is not None``): elbow-through-label check.
    - dimension-like (``.measured_length is not None``): label-vs-measured and
      dim-inside-part checks.
    - centerline-like (``.is_centerline``): pairwise overlap against dims.

    Page-bounds checking is performed when *page_bbox* is provided as a
    ``(min_x, min_y, max_x, max_y)`` tuple, or when ``set_page()`` has been
    called and stored a module-level page context.  Any annotation whose full
    bounding box extends past the drawable area (page minus margin) is flagged
    as ``annotation_out_of_bounds`` (severity ``"error"``).  This includes a
    :class:`TitleBlock` passed as an item: its bounding box grows when a long
    string (e.g. a verbose subtitle) overflows the frame, so include the title
    block in *items* to catch text that spills past the page edge.

    Args:
        items: annotation objects exposing the relevant attrs (or SimpleNamespace
            stand-ins).
        part_bbox: optional BoundBox of the projected part outline.
        page_bbox: optional ``(min_x, min_y, max_x, max_y)`` drawable area.
            If ``None``, falls back to the module-level context set by
            ``set_page()``.  When neither is set, page-bounds are not checked.
        drawing_scale: the N:1 factor the geometry was scaled by before
            projecting (e.g. ``5.0`` for a 7.5 mm feature drawn at 5:1). The
            label-vs-measured check divides each measured path length by this
            before comparing to the label value, so labels carry the *real*
            dimension while the geometry is drawn enlarged. Defaults to ``1.0``
            (no scaling). See :func:`format_drawing_scale` to render the
            matching "5:1" indicator in the title block.
        view_shapes: optional list of build123d shapes representing projected
            view outlines.  When provided, annotations are checked against the
            view's actual projected edges (``view_annotation_overlap``,
            warning); an annotation inside the view's bounding box but over a
            blank region — a legitimate convention for callouts on large
            faces — is only an info-level ``view_annotation_inside_extents``
            notice.  View bounding boxes are also checked for overlap with
            each other (``view_overlap``, warning) and, when page bounds are
            known, against the drawable area (``view_out_of_bounds``, error).
            Annotations whose line-work must touch the view are not
            false-flagged: centrelines and datum targets are exempt, and
            annotations exposing a ``label_bbox`` (dimensions, leaders, datum
            features, surface-finish marks) are tested by the label-text
            extents only — witness lines, leader shafts, datum triangles, and
            finish marks may enter the view freely.  Shapes whose bounding box
            cannot be computed are silently skipped.
        view_edge_cache: optional dict, persisted by the caller across repeated
            ``lint_drawing`` calls on the *same* views, that memoises each view
            shape's per-edge bounding boxes — the dominant cost when a drawing
            is linted many times (e.g. a build→critique→fix loop) (#143). Pass
            the same dict to successive lints; discard it when the view shapes
            change. Omit it (the default) for a fresh per-call cache, which
            behaves exactly as before.
        ann_box_cache: optional dict memoising each *annotation's* full optimal
            bounding box — the dominant remaining lint cost once view edges are
            cached (#602). Same persistence contract as *view_edge_cache*;
            entries are identity-checked, so a replaced annotation is re-measured
            automatically. Omit it for a fresh per-call cache (which still
            de-duplicates the several checks that need the same item's box).

    Returns:
        list[LintIssue].

    Raises:
        ValueError: if ``drawing_scale`` is not positive (matches
            :func:`format_drawing_scale` / :class:`TitleBlock`).
    """
    if drawing_scale <= 0:
        raise ValueError(f"drawing_scale must be positive, got {drawing_scale}")

    issues: list[LintIssue] = []
    box_cache = {} if ann_box_cache is None else ann_box_cache

    # Resolve page bounds: explicit arg beats module-level context.
    if page_bbox is None and _DRAWING_PAGE is not None:
        p = _DRAWING_PAGE
        page_bbox = (p["min_x"], p["min_y"], p["max_x"], p["max_y"])

    for item in items:
        if getattr(item, "elbow", None) is not None:
            _lint_leader(item, issues, box_cache)
        elif getattr(item, "measured_length", None) is not None:
            _lint_dim(item, part_bbox, issues, drawing_scale, box_cache)

    # Pairwise label-overlap check. The compare-box for a label-less item is an
    # *optimal* bounding_box() — expensive, and previously recomputed for both
    # items of every pair (O(n²): ~200 s on an 83-hole part). Compute each
    # item's box exactly once up front and index into it instead (#161); the
    # result is identical. Centre lines are compared via _centerline_extent, not
    # this box, so they don't need one.
    def _label_box(item):
        lb = getattr(item, "label_bbox", None)
        if lb is not None:
            return lb
        return _ann_box(item, box_cache)

    boxes: list = []
    for item in items:
        if getattr(item, "is_centerline", False):
            boxes.append(None)
            continue
        try:
            boxes.append(_label_box(item))
        except Exception:
            boxes.append(None)

    for i, item_a in enumerate(items):
        for j in range(i + 1, len(items)):
            item_b = items[j]
            try:
                is_cl_a = getattr(item_a, "is_centerline", False)
                is_cl_b = getattr(item_b, "is_centerline", False)

                if is_cl_a and is_cl_b:
                    continue

                if is_cl_a or is_cl_b:
                    dim_item = item_b if is_cl_a else item_a
                    cl_item = item_a if is_cl_a else item_b
                    _lint_centerline_dim_overlap(dim_item, cl_item, issues, box_cache)
                    continue

                # Compare label text extents, NOT full bounding boxes.
                # Full bbox includes witness lines which legitimately overlap for
                # stacked dims (every inner bbox is a subset of the outer one).
                # label_bbox is the keep-clear region around the value text — the
                # thing that actually matters to a reader.
                la_box = boxes[i]
                lb_box = boxes[j]
                if la_box is None or lb_box is None:
                    continue
                ox = max(0.0, min(la_box[2], lb_box[2]) - max(la_box[0], lb_box[0]))
                oy = max(0.0, min(la_box[3], lb_box[3]) - max(la_box[1], lb_box[1]))
                if ox > 0.5 and oy > 0.5:
                    la = getattr(item_a, "label", "?")
                    lb = getattr(item_b, "label", "?")
                    issues.append(
                        LintIssue(
                            severity="warning",
                            message=(
                                f"labels '{la}' and '{lb}' overlap by "
                                f"{ox:.1f}×{oy:.1f} mm — use label_offset_x or "
                                f"increase dim offset to separate them"
                            ),
                            code="annotation_overlap",
                        )
                    )
            except Exception:
                pass

    # Page-bounds check — annotations must stay within the drawable area.
    if page_bbox is not None:
        for item in items:
            try:
                bb = _ann_box(item, box_cache)
                if bb is None:
                    continue
                for detail in _overshoots(bb, page_bbox):
                    lbl = _item_label(item) or "?"
                    issues.append(
                        LintIssue(
                            severity="error",
                            message=(
                                f"annotation '{lbl}' extends past drawable area "
                                f"({detail}) — increase margin or reduce offset"
                            ),
                            code="annotation_out_of_bounds",
                        )
                    )
            except Exception:
                pass

    if view_shapes is not None:
        _lint_view_shapes(
            view_shapes,
            items,
            issues,
            page_bbox=page_bbox,
            edge_cache=view_edge_cache,
            box_cache=box_cache,
        )

    # Principal envelope completeness check: verify each bbox extent appears
    # as a dimension label.  Only runs when part_bbox is supplied.
    if part_bbox is not None:
        covered: set[float] = set()
        for item in items:
            if getattr(item, "measured_length", None) is not None:
                val = _label_value(_item_label(item))
                if val is not None:
                    covered.add(val)

        def _check_extent(axis: str, page_extent: float) -> None:
            world_ext = page_extent / drawing_scale
            tol = max(0.5, world_ext * 0.001)
            if not any(abs(v - world_ext) <= tol for v in covered):
                issues.append(
                    LintIssue(
                        severity="warning",
                        message=(
                            f"no dimension found for {axis} extent ({world_ext:.4g} mm)"
                            " — add dim_width, dim_depth, or equivalent"
                        ),
                        code="missing_principal_dimension",
                    )
                )

        x_ext = part_bbox.max.X - part_bbox.min.X
        y_ext = part_bbox.max.Y - part_bbox.min.Y
        x_approx_y = max(x_ext, y_ext) > 1e-6 and abs(x_ext - y_ext) / max(x_ext, y_ext) < 0.05
        _check_extent("X", x_ext)
        if not x_approx_y:
            _check_extent("Y", y_ext)
        if hasattr(part_bbox.min, "Z") and hasattr(part_bbox.max, "Z"):
            _check_extent("Z", part_bbox.max.Z - part_bbox.min.Z)

    return issues


def _bbox2d(shape):
    """Return (minx, miny, maxx, maxy) for a build123d shape, or None on failure."""
    try:
        bb = shape.bounding_box()
        return bb.min.X, bb.min.Y, bb.max.X, bb.max.Y
    except Exception:
        return None


def _bboxes_overlap_2d(a, b) -> bool:
    """True if two (minx, miny, maxx, maxy) rectangles overlap in XY."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return bool(ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0)


def _overshoots(bb, bounds) -> list[str]:
    """Sides where (min_x, min_y, max_x, max_y) *bb* spills past *bounds*, as text."""
    bx0, by0, bx1, by1 = bounds
    out = []
    if bb[0] < bx0:
        out.append(f"left by {bx0 - bb[0]:.1f} mm")
    if bb[2] > bx1:
        out.append(f"right by {bb[2] - bx1:.1f} mm")
    if bb[1] < by0:
        out.append(f"below by {by0 - bb[1]:.1f} mm")
    if bb[3] > by1:
        out.append(f"above by {bb[3] - by1:.1f} mm")
    return out


def _edges_intersect_rect(edge_entries, rect) -> bool:
    """True if any ``(edge, bbox2d)`` of *edge_entries* passes through the
    (min_x, min_y, max_x, max_y) rect.

    Straight edges use exact Liang–Barsky clipping; curved edges are sampled
    at roughly 1 mm spacing. An edge whose geometry cannot be analysed counts
    as a hit, so a real overlap is never silently missed.
    """
    for e, eb in edge_entries:
        try:
            if eb is None:
                return True  # unanalysable edge — count as a hit
            if not _bboxes_overlap_2d(eb, rect):
                continue
            if e.geom_type == GeomType.LINE:
                s, t = e.start_point(), e.end_point()
                if _seg_intersects_rect((s.X, s.Y), (t.X, t.Y), rect):
                    return True
                continue
            n = min(200, max(8, int(e.length) + 1))
            for i in range(n + 1):
                p = e.position_at(i / n)
                if rect[0] <= p.X <= rect[2] and rect[1] <= p.Y <= rect[3]:
                    return True
        except Exception:
            return True
    return False


def _view_edge_entries(vs, cache):
    """Per-edge ``(edge, bbox2d)`` list for view shape *vs*, memoised in *cache*.

    Building this list is the dominant lint cost (one optimal bounding box per
    projected edge); a caller-persisted *cache* lets repeated lints of the same
    views reuse it (#143). The shape is stored alongside its entries and checked
    by identity, so a reused cache can't return a stale list after ``id()``
    reuse. ``None`` marks a view whose edges can't be analysed (treated as a
    hit), matching the un-cached behaviour."""
    key = id(vs)
    hit = cache.get(key)
    if hit is not None and hit[0] is vs:
        return hit[1]
    try:
        entries: list | None = [(e, _bbox2d(e)) for e in vs.edges()]
    except Exception:
        entries = None
    cache[key] = (vs, entries)
    return entries


def _lint_view_shapes(
    view_shapes, ann_items, issues, page_bbox=None, edge_cache=None, box_cache=None
) -> None:
    """Check views against annotations (#159/#76), each other (#160), and the page (#75)."""
    # Build named bbox list; use the shape's id as fallback name.
    named_views = []
    view_shape_ids = set()
    for vs in view_shapes:
        bb = _ann_box(vs, box_cache if box_cache is not None else {})
        if bb is None:
            continue
        name = getattr(vs, "label", None) or getattr(vs, "name", None) or f"view@{id(vs)}"
        named_views.append((name, bb, vs))
        view_shape_ids.add(id(vs))

    # #159 — view shape vs annotation overlaps. Line-work (witness lines,
    # leader shafts, centrelines) legitimately enters the view, so test the
    # label-text bbox where the annotation exposes one and skip centrelines
    # entirely; only annotations without a label bbox fall back to their full
    # bounding box. Within the view bbox, only a label that crosses the view's
    # actual projected edges is a warning (#76) — on a large part the bbox is
    # mostly blank face, where placing callouts is a legitimate convention —
    # so a label over a blank region is reported as an info-level notice.
    cache = {} if edge_cache is None else edge_cache
    ann_cache = box_cache if box_cache is not None else {}
    for vname, vbb, vs in named_views:
        vx0, vy0, vx1, vy1 = vbb
        for ann in ann_items:
            if id(ann) in view_shape_ids:
                continue
            if getattr(ann, "is_centerline", False):
                continue  # a centreline must cross the feature it marks
            if getattr(ann, "is_datum_target", False):
                continue  # a datum target sits on the part face by definition
            if getattr(ann, "is_section_hatch", False):
                continue  # hatching is intentionally inside the section view
            try:
                label_box = getattr(ann, "label_bbox", None)
                ab = label_box if label_box is not None else _ann_box(ann, ann_cache)
                if ab is None:
                    continue
                if not _bboxes_overlap_2d(vbb, ab):
                    continue
                albl = (
                    getattr(ann, "label", None) or getattr(ann, "name", None) or type(ann).__name__
                )
                what = "label of annotation" if label_box is not None else "annotation"
                edges = _view_edge_entries(vs, cache)
                if edges is None or _edges_intersect_rect(edges, ab):
                    issues.append(
                        LintIssue(
                            severity="warning",
                            message=(
                                f"view '{vname}' line-work overlaps {what} '{albl}' "
                                f"— increase view spacing or move the annotation"
                            ),
                            code="view_annotation_overlap",
                        )
                    )
                else:
                    issues.append(
                        LintIssue(
                            severity="info",
                            message=(
                                f"{what} '{albl}' lies inside view '{vname}' extents "
                                f"[x={vx0:.1f}–{vx1:.1f}, y={vy0:.1f}–{vy1:.1f}] over a "
                                f"blank region — legitimate for callouts on large faces"
                            ),
                            code="view_annotation_inside_extents",
                        )
                    )
            except Exception:
                pass

    # #160 — view shape vs view shape bounding box overlaps
    for i, (aname, abb, _) in enumerate(named_views):
        ax0, ay0, ax1, ay1 = abb
        for bname, bbb, _ in named_views[i + 1 :]:
            bx0, by0, bx1, by1 = bbb
            if _bboxes_overlap_2d(abb, bbb):
                issues.append(
                    LintIssue(
                        severity="warning",
                        message=(
                            f"view '{aname}' bbox "
                            f"[x={ax0:.1f}–{ax1:.1f}, y={ay0:.1f}–{ay1:.1f}] "
                            f"overlaps view '{bname}' "
                            f"[x={bx0:.1f}–{bx1:.1f}, y={by0:.1f}–{by1:.1f}] "
                            f"— increase spacing between views"
                        ),
                        code="view_overlap",
                    )
                )

    # #75 — views must stay within the drawable area.
    if page_bbox is not None:
        for vname, vbb, _ in named_views:
            for detail in _overshoots(vbb, page_bbox):
                issues.append(
                    LintIssue(
                        severity="error",
                        message=(
                            f"view '{vname}' extends past drawable area ({detail}) "
                            f"— reduce the view scale or move the view"
                        ),
                        code="view_out_of_bounds",
                    )
                )


def _lint_centerline_dim_overlap(dim_item, cl_item, issues, box_cache) -> None:
    """Flag label-vs-centerline overlap for a (dim, centerline) pair."""
    try:
        cl_min_x, cl_min_y, cl_max_x, cl_max_y = _centerline_extent(cl_item, box_cache)

        label_bbox = getattr(dim_item, "label_bbox", None)
        if label_bbox is None:
            label_bbox = _ann_box(dim_item, box_cache)
        if label_bbox is None:
            return
        lmin_x, lmin_y, lmax_x, lmax_y = label_bbox

        cl_w = cl_max_x - cl_min_x
        cl_h = cl_max_y - cl_min_y

        if cl_w < 0.1:
            cl_x = (cl_min_x + cl_max_x) / 2.0
            ox = min(cl_x - lmin_x, lmax_x - cl_x) if lmin_x < cl_x < lmax_x else 0.0
        else:
            ox = max(0.0, min(lmax_x, cl_max_x) - max(lmin_x, cl_min_x))

        if cl_h < 0.1:
            cl_y = (cl_min_y + cl_max_y) / 2.0
            oy = min(cl_y - lmin_y, lmax_y - cl_y) if lmin_y < cl_y < lmax_y else 0.0
        else:
            oy = max(0.0, min(lmax_y, cl_max_y) - max(lmin_y, cl_min_y))

        if ox > 0.5 and oy > 0.5:
            dim_label = getattr(dim_item, "label", "?")
            issues.append(
                LintIssue(
                    severity="warning",
                    message=(
                        f"label '{dim_label}' overlaps centerline by "
                        f"{ox:.1f}×{oy:.1f} mm — use label_offset_x to shift "
                        f"or increase dim offset to clear the centerline"
                    ),
                    code="label_centerline_overlap",
                )
            )
    except Exception:
        pass


def _lint_dim(item, part_bbox, issues, drawing_scale: float = 1.0, box_cache=None) -> None:
    label = _item_label(item)
    measured = getattr(item, "measured_length", None)

    label_val = _label_value(label)
    if label_val is not None and measured is not None:
        # When drawing_scale != 1.0 the geometry was scaled up before projecting
        # (e.g. part.scale(5) for a 7.5 mm feature drawn at 5:1). The measured
        # path length is the *scaled* length; the label carries the *real* value.
        # Divide measured by the scale factor before comparing so a 37.5 mm
        # measured segment with label "7.5" at 5:1 is accepted, not flagged.
        # drawing_scale is guaranteed positive by lint_drawing()'s validation.
        effective_measured = measured / drawing_scale
        if effective_measured > 1e-6:
            ratio = abs(label_val - effective_measured) / effective_measured
            if ratio > 0.005:
                issues.append(
                    LintIssue(
                        severity="warning",
                        message=(
                            f"Dim '{label}': label value {label_val:.3f} differs from "
                            f"measured path length {measured:.3f}"
                            + (
                                f" (÷{drawing_scale} = {effective_measured:.3f})"
                                if drawing_scale != 1.0
                                else ""
                            )
                            + f" by {ratio * 100:.1f}% "
                            f"— possible axis swap or wrong endpoint"
                        ),
                        code="label_vs_measured",
                    )
                )

    if part_bbox is not None:
        db = _ann_box(item, box_cache if box_cache is not None else {})
        if db is None:
            return
        dmin_x, dmin_y, dmax_x, dmax_y = db
        ox = max(0.0, min(dmax_x, part_bbox.max.X) - max(dmin_x, part_bbox.min.X))
        oy = max(0.0, min(dmax_y, part_bbox.max.Y) - max(dmin_y, part_bbox.min.Y))
        overlap = ox * oy
        dim_area = max((dmax_x - dmin_x) * (dmax_y - dmin_y), 1e-9)
        if overlap / dim_area > 0.10:
            issues.append(
                LintIssue(
                    severity="warning",
                    message=(
                        f"Dim '{label}': annotation bbox overlaps part outline by "
                        f"{overlap / dim_area * 100:.0f}% — offset sign may place it inside the view"
                    ),
                    code="dim_inside_part",
                )
            )


def _lint_leader(item, issues, box_cache=None) -> None:
    try:
        box = getattr(item, "label_bbox", None)
        if box is None:
            box = _ann_box(item, box_cache if box_cache is not None else {})
        if box is None:
            return
        minx, miny, maxx, maxy = box
        ex, ey = item.elbow
        if minx <= ex <= maxx and miny <= ey <= maxy:
            issues.append(
                LintIssue(
                    severity="error",
                    message=(
                        f"Leader '{getattr(item, 'label', '?')}': elbow point "
                        f"({ex:.2f}, {ey:.2f}) is inside the label bbox — leader "
                        f"line passes through the text"
                    ),
                    location=item.elbow,
                    code="leader_line_through_text",
                )
            )
    except Exception:
        pass


def _seg_hits_box(p, q, box, pad=0.2):
    """Liang–Barsky: does segment p->q intersect the padded AABB box?"""
    minx, miny, maxx, maxy = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x0 - minx), (dx, maxx - x0), (-dy, y0 - miny), (dy, maxy - y0)):
        if abs(pp) < 1e-12:
            if qq < 0:
                return False
        else:
            r = qq / pp
            if pp < 0:
                if r > t1:
                    return False
                t0 = max(t0, r)
            else:
                if r < t0:
                    return False
                t1 = min(t1, r)
    return t0 <= t1
