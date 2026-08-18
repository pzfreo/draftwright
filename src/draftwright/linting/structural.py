"""structural — duck-typed structural lint of a composed annotation list.

Vendored from ``build123d_drafting.helpers`` (ADR 0007: draftwright owns
linting; helpers is the rendering library). ``lint_drawing`` dispatches by
attribute presence, not type, so it needs no import of the drawing-object
classes. Page bounds are passed explicitly by the caller (``page_bbox``); the
``set_page`` module-global fallback is kept inert here (``_DRAWING_PAGE = None``)
since draftwright threads the page extent directly (see ``Drawing.lint``).
"""

from __future__ import annotations

import logging
import re

from build123d import GeomType

from draftwright._core import _shape_box2d
from draftwright._geometry import (
    MATERIAL_VISIBLE_FLOOR,
    _boxes_overlap,
    _segment_clip_extent,
    _segment_clips_box,
    material_reentry_span,
)
from draftwright.linting.issues import LintIssue, _IssueAggregation
from draftwright.projection import _MATERIAL_PAGE_TOLERANCE

#: The shared visible-stroke floor. Imported rather than restated so the critique cannot
#: drift from the router that solves against the same field (#798).
_MATERIAL_REENTRY_FLOOR = MATERIAL_VISIBLE_FLOOR

_log = logging.getLogger(__name__)

# Inert: draftwright always passes page_bbox explicitly (the set_page global
# coupling is severed, ADR 0007). Kept so the vendored body is a faithful copy.
_DRAWING_PAGE = None


def _seg_intersects_rect(p, q, rect) -> bool:
    """True if segment p→q intersects the (min_x, min_y, max_x, max_y) rect.

    Thin wrapper over :func:`draftwright._geometry._segment_clips_box` (the
    Liang–Barsky clip) with no padding, kept for call-site readability.
    """
    return bool(_segment_clips_box(p, q, rect, pad=0.0))


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
    except Exception as exc:  # noqa: BLE001 — not every annotation bbox-es cleanly
        # #701: cached once per item, so this logs once — a silently skipped item
        # is the wrong failure mode for lint (same rationale as _common._geom_box).
        _log.debug(
            "lint: %s did not bbox (%s); box-dependent checks skip it", type(item).__name__, exc
        )
        box = None
    cache[key] = (item, token, box)
    return box


def _centerline_extent(cl_item, box_cache=None):
    """Return (min_x, min_y, max_x, max_y) for a centreline.

    Prefers precise component metadata (``centerline_segments`` plus optional
    ``centerline_boxes``), then the conventional zero-width ``.segments``, so a
    thin-faced centreline still reads as a line while a compound balloon retains
    its compact ring/text glyph. Falls back to rendered ``.bounding_box()``.
    """
    segs = getattr(cl_item, "centerline_segments", getattr(cl_item, "segments", None))
    component_boxes = getattr(cl_item, "centerline_boxes", ())
    if segs or component_boxes:
        xs = [p[0] for s in (segs or ()) for p in s]
        ys = [p[1] for s in (segs or ()) for p in s]
        xs.extend(value for box in component_boxes for value in (box[0], box[2]))
        ys.extend(value for box in component_boxes for value in (box[1], box[3]))
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


def is_dimension_like(item) -> bool:
    """Whether *item* states a measured quantity — the ONE dimension-like predicate.

    :func:`lint_drawing` dispatches its label-vs-measured and dim-inside-part checks through
    this, and so does the principal-dimension sweep below, so a caller asking "does this
    drawing assert anything measurable?" gets the same answer as the check that would
    contradict it. Spelling it a second time in ``drawing.py`` is what let the quality gate
    read ``label_bbox`` — text presence on ANY annotation, a title block included — and call
    it asserted content (#1176 review r3).

    Both dispatch sites call it. Naming a shared predicate and leaving its callers spelling
    the condition out is the same defect one step further back: it would make a change here
    silently stop matching what lint actually does, which is exactly what the paragraph above
    is about (#1176 review r4).

    Deliberately narrow: ``measured_length`` is a plain attribute on the helper's dimension
    types, so this needs none of :func:`_label_bbox`'s raising-property discipline.
    """
    return getattr(item, "measured_length", None) is not None


def _label_bbox(item, warned=None):
    """``item.label_bbox`` or ``None`` — a raising property on a user-supplied
    duck-typed item cannot kill lint; it is logged (once per item), not swallowed
    (#701: a check that silently skips an item can silently disable itself).
    *warned* is the per-``lint_drawing``-run set of already-warned item ids —
    several checks read the same item's label_bbox (per centreline pair, per
    view), so an unmemoised warning would flood the log O(n²) on one bad item
    (#711 review). Run-local, not module-global (Codex sweep review): ids are
    only meaningful while the run holds the items alive, and a shared global
    would cross-talk between overlapping runs. ``None`` (a direct helper call)
    just warns every time.
    Known hole: a property that raises ``AttributeError`` *internally* is
    indistinguishable from an absent attribute through ``getattr`` and reads as a
    silent ``None``."""
    try:
        return getattr(item, "label_bbox", None)
    except Exception as exc:  # noqa: BLE001 — duck-typed items may misbehave
        if warned is None or id(item) not in warned:
            if warned is not None:
                warned.add(id(item))
            _log.warning(
                "lint: unreadable label_bbox on %s (%s); item skipped", type(item).__name__, exc
            )
        return None


def _label_reading(item, label: str) -> float | None:
    """The value *item*'s label asserts about the path it is drawn on, or ``None``.

    A bare ``N× v`` is ambiguous, because this codebase draws that label under two
    conventions and the string cannot tell them apart:

    * ``dim_step_typ`` "8× 15" is ONE representative step drawn at **15**;
    * a hole pitch "3× 20" (``annotations/holes.py``, and three sites in
      ``annotations/from_model.py``) spans the whole run at **60**.

    So the producer says which. The one per-unit renderer sets ``_dw_label_value`` to the
    number the ``N×`` multiplies — carried from ``ApprovedDimension.value``, so lint reads
    the compiler's own number rather than re-deriving a convention from the rendered string
    (ADR 0016 Amendment 1). Everything else means what its label says.

    An earlier cut returned BOTH readings for every untagged ``N× v`` and let a dimension
    pass if it matched either. That silently disabled the check on the four
    product-convention producers: the per-unit value is precisely the length you get from
    spanning one member instead of the run — the most likely wrong endpoint for a pitch dim,
    and exactly the defect #1153 and #1209 report. A real engine-produced "3× 30" drawn
    across one pitch went from a 200% warning to no finding at all.

    If another per-unit producer appears untagged it will report a large false discrepancy —
    loudly, as an error — which is the right failure mode for a missing tag.
    """
    declared = getattr(item, "_dw_label_value", None)
    return float(declared) if declared is not None else _label_value(label)


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
    view_material_fields: dict | None = None,
    view_names: list | None = None,
    check_view_placement: bool = True,
    _aggregation: _IssueAggregation | None = None,
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
            cannot be computed are skipped (logged at debug, #701).
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
        view_material_fields: optional ``{id(view_shape): MaterialField}`` giving each
            view's filled projected material (#798), normally
            :meth:`draftwright.Drawing.material_fields`. Required for the
            ``leader_crosses_silhouette`` check — without it there is no material
            knowledge and the check reports nothing, rather than guessing from the
            outline and contradicting the router that solves against this same field.
        view_names: optional names for *view_shapes*, matched **positionally**. The
            caller is the authority on what a view is called — ``Drawing.views`` is
            keyed by name and lint has no other way to find out — so a message can say
            "view 'front'" rather than naming the shape by object address (#1196). A
            supplied name outranks any ``label``/``name`` on the shape itself; without
            one, the fallback is the view's position, never its identity.
            **Appended, not inserted**: the upstream helpers' signature has
            ``view_edge_cache`` as its sixth parameter, and a positional call written against
            that order would silently bind a cache dict here — degrading the name, then
            raising ``KeyError`` once the cache was warm and its ``id()`` keys were
            indexed as a name list.

        check_view_placement: whether to run the checks that compare views to EACH OTHER
            and to the page (``view_overlap``, ``view_out_of_bounds``). They depend on no
            annotation, so a caller that lints the same drawing in several passes — as
            :meth:`draftwright.Drawing.lint` does, one per annotation scale group — must
            ask for them exactly once or their findings are counted once per pass (#1204).
            The annotation-vs-view checks always run, because each pass carries different
            annotations.

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
    # Per-run label_bbox warning memo (#711 review / Codex sweep): threaded to every
    # check so one bad item warns once per lint run, with no cross-run global state.
    warned_label_bbox: set[int] = set()
    # Opaque primary-subject tokens exist only for this call. Every input object remains live
    # in ``items`` while the map is used, so its id cannot be recycled; the summary ledger gets
    # only the tokens, never annotation ids or CAD graphs (#1147 review).
    pair_tokens = {id(item): object() for item in items} if _aggregation is not None else {}

    # Resolve page bounds: explicit arg beats module-level context.
    if page_bbox is None and _DRAWING_PAGE is not None:
        p = _DRAWING_PAGE
        page_bbox = (p["min_x"], p["min_y"], p["max_x"], p["max_y"])

    for item in items:
        if getattr(item, "elbow", None) is not None:
            _lint_leader(item, issues, box_cache, warned=warned_label_bbox)
        elif is_dimension_like(item):
            _lint_dim(item, part_bbox, issues, drawing_scale, box_cache)

    # Pairwise label-overlap check. The compare-box for a label-less item is an
    # *optimal* bounding_box() — expensive, and previously recomputed for both
    # items of every pair (O(n²): ~200 s on an 83-hole part). Compute each
    # item's box exactly once up front and index into it instead (#161); the
    # result is identical. Centre lines are compared via _centerline_extent, not
    # this box, so they don't need one.
    def _label_box(item):
        lb = _label_bbox(item, warned_label_bbox)
        if lb is not None:
            return lb
        return _ann_box(item, box_cache)

    boxes: list = []
    for item in items:
        # The sheet frame (#767) spans the page by design; a None box excludes it from every
        # pairwise overlap (like a centerline), so it doesn't "overlap" every annotation.
        if (
            getattr(item, "is_centerline", False)
            or getattr(item, "is_sheet_frame", False)
            or getattr(item, "is_zone_grid", False)  # border ticks span the frame, like the frame
        ):
            boxes.append(None)
            continue
        boxes.append(_label_box(item))

    # #701: the check body runs unguarded — the fragile duck-typed reads happened
    # above (boxes) or inside the callee; a bug here must fail loudly, not silently
    # disable the check forever.
    for i, item_a in enumerate(items):
        for j in range(i + 1, len(items)):
            item_b = items[j]
            is_cl_a = getattr(item_a, "is_centerline", False)
            is_cl_b = getattr(item_b, "is_centerline", False)

            if is_cl_a and is_cl_b:
                continue

            if is_cl_a or is_cl_b:
                dim_item = item_b if is_cl_a else item_a
                cl_item = item_a if is_cl_a else item_b
                _lint_centerline_dim_overlap(
                    dim_item,
                    cl_item,
                    issues,
                    box_cache,
                    warned=warned_label_bbox,
                    aggregation=_aggregation,
                    subject_token=pair_tokens.get(id(dim_item)),
                )
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

    # Page-bounds check — annotations must stay within the drawable area.
    # (#701: unguarded — _ann_box absorbs the fragile measure; the rest is arithmetic.)
    if page_bbox is not None:
        for item in items:
            if (
                getattr(item, "is_sheet_frame", False)
                or getattr(item, "is_zone_grid", False)
                or getattr(item, "is_zone_label", False)
            ):
                # Frame + zone ruler sit ON / outside the drawable boundary by design (#767/#768).
                continue
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

    if view_shapes is not None:
        _lint_view_shapes(
            view_shapes,
            items,
            issues,
            view_names=view_names,
            check_view_placement=check_view_placement,
            page_bbox=page_bbox,
            edge_cache=view_edge_cache,
            box_cache=box_cache,
            warned=warned_label_bbox,
            material_fields=view_material_fields,
        )

    # Principal envelope completeness check: verify each bbox extent appears
    # as a dimension label.  Only runs when part_bbox is supplied.
    if part_bbox is not None:
        covered: set[float] = set()
        for item in items:
            if is_dimension_like(item):
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
            if not _boxes_overlap(eb, rect):
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
        entries: list | None = [(e, _shape_box2d(e)) for e in vs.edges()]
    except Exception:
        entries = None
    cache[key] = (vs, entries)
    return entries


def _lint_view_shapes(
    view_shapes,
    ann_items,
    issues,
    *,
    view_names=None,
    check_view_placement=True,
    page_bbox=None,
    edge_cache=None,
    box_cache=None,
    warned=None,
    material_fields=None,
) -> None:
    """Check views against annotations (#159/#76), each other (#160), and the page (#75)."""
    # Build the named bbox list. The name must be DETERMINISTIC: several messages
    # below identify a view by it, and `id()` is a fresh memory address on every
    # render, so the same sheet linted twice produced different text and any consumer
    # diffing two renders saw a change that did not exist in the drawing (#1196).
    # Prefer the caller's own name for the view; fall back to position, never identity.
    named_views = []
    view_shape_ids = set()
    for index, vs in enumerate(view_shapes):
        bb = _ann_box(vs, box_cache if box_cache is not None else {})
        if bb is None:
            continue
        supplied = (
            view_names[index] if view_names is not None and index < len(view_names) else None
        )
        # *supplied* first: the docstring argues the caller is the authority on what a
        # view is called, so letting a shape attribute outrank it would contradict that.
        # Latent today — every projected view carries `label == ""` — but DXF layer
        # naming is an obvious future reason to label a view compound, and it would
        # then silently override the drawing's own key.
        name = (
            supplied or getattr(vs, "label", None) or getattr(vs, "name", None) or f"view[{index}]"
        )
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
            if getattr(ann, "is_sheet_frame", False) or getattr(ann, "is_zone_grid", False):
                continue  # sheet border / zone ticks span the page, enclosing every view (#767/#768)
            # #701: unguarded — _label_bbox/_ann_box/_view_edge_entries absorb the
            # fragile reads; a bug in the check itself must fail loudly.
            label_box = _label_bbox(ann, warned)
            ab = label_box if label_box is not None else _ann_box(ann, ann_cache)
            if ab is None:
                continue
            if not _boxes_overlap(vbb, ab):
                continue
            albl = getattr(ann, "label", None) or getattr(ann, "name", None) or type(ann).__name__
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

    # #796/#798 — leader shaft cuts back through the part body. Measured against the
    # build's FILLED projected material (`Drawing.material_fields`), the same lowering
    # ADR 0014 leader routing solves against, so the notice and the router cannot reach
    # opposite verdicts on one shaft.
    #
    # The predicate is re-entry, not crossing: a leader is attached to the feature it
    # names, so its first traversal out of the body is the legitimate exit every ⌀, hole
    # and pocket callout makes. Going back IN is the defect. That subsumes the exemptions
    # the outline-crossing form needed — a shaft passing over a through-hole re-enters no
    # material, so `covers_diameters` is no longer an escape and the real cuts it was
    # hiding (a 16.75 mm hole-callout re-entry on CTC-03) are now visible.
    #
    # Without a field there is no material knowledge, so nothing is reported. Falling back
    # to an outline heuristic would reintroduce exactly the second opinion this check
    # exists to eliminate.
    for vname, vbb, vs in named_views:
        field = (material_fields or {}).get(id(vs))
        if field is None:
            continue
        for ann in ann_items:
            if id(ann) in view_shape_ids:
                continue
            if getattr(ann, "elbow", None) is None:
                continue
            if getattr(ann, "is_centerline", False) or getattr(ann, "is_section_hatch", False):
                continue
            try:
                tip, elbow = ann.tip, ann.elbow
                shaft_bb = (
                    min(tip[0], elbow[0]),
                    min(tip[1], elbow[1]),
                    max(tip[0], elbow[0]),
                    max(tip[1], elbow[1]),
                )
            except Exception:
                continue
            if not _boxes_overlap(vbb, shaft_bb):
                continue
            cut = material_reentry_span(
                (tip[0], tip[1]),
                (elbow[0], elbow[1]),
                field,
                bridge=_MATERIAL_PAGE_TOLERANCE,
            )
            if cut > _MATERIAL_REENTRY_FLOOR:
                albl = getattr(ann, "label", None) or getattr(ann, "name", None) or "leader"
                issues.append(
                    LintIssue(
                        severity="info",
                        message=(
                            f"leader '{albl}' shaft cuts {cut:.1f} mm back through view "
                            f"'{vname}' — route it clear of the body, or dimension the "
                            f"feature in a detail view"
                        ),
                        location=(elbow[0], elbow[1]),
                        code="leader_crosses_silhouette",
                    )
                )

    # The remaining checks compare views against EACH OTHER and against the page, so they
    # say nothing about annotations and give the same answer however the annotations are
    # grouped. `Drawing._lint` splits annotations by scale and calls this once per group,
    # which emitted each of their findings once PER GROUP (#1204).
    #
    # The annotation-vs-view checks above must still run for every group, because each group
    # holds DIFFERENT annotations. A first cut of #1204 nulled `view_shapes` for later
    # groups instead and lost their `view_annotation_inside_extents` findings — trading a
    # double-count for a missed defect.
    if not check_view_placement:
        return

    # #160 — view shape vs view shape bounding box overlaps
    for i, (aname, abb, _) in enumerate(named_views):
        ax0, ay0, ax1, ay1 = abb
        for bname, bbb, _ in named_views[i + 1 :]:
            bx0, by0, bx1, by1 = bbb
            if _boxes_overlap(abb, bbb):
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


def _lint_centerline_dim_overlap(
    dim_item,
    cl_item,
    issues,
    box_cache=None,
    warned=None,
    aggregation=None,
    subject_token=None,
) -> None:
    """Flag label-vs-centerline overlap for a (dim, centerline) pair.

    #701: only the centreline measure is guarded (malformed ``segments`` /
    unmeasurable bbox on a duck-typed item) and the skip is logged; the overlap
    arithmetic runs unguarded so a bug fails loudly instead of silently
    disabling the check.
    """
    overlap = _label_centerline_overlap(dim_item, cl_item, box_cache, warned)
    if overlap is not None:
        ox, oy, location = overlap
        dim_label = getattr(dim_item, "label", "?")
        issue = LintIssue(
            severity="warning",
            message=(
                f"label '{dim_label}' overlaps centerline by "
                f"{ox:.1f}×{oy:.1f} mm — use label_offset_x to shift "
                f"or increase dim offset to clear the centerline"
            ),
            # Pair detail stays in the raw issue: this is the compared centre mark's
            # extent centre, so equal-looking findings can still be traced spatially.
            location=location,
            code="label_centerline_overlap",
        )
        issues.append(issue)
        if aggregation is not None and subject_token is not None:
            # The side ledger sees which half of the pair owns the readability defect;
            # neither the annotation nor its run-local identity enters public diagnostics.
            aggregation.record_pair(issue, subject_token)


def _label_centerline_overlap(dim_item, cl_item, box_cache=None, warned=None):
    """Return the lint-significant label/centreline overlap, or ``None``.

    Balloon placement uses the same predicate before committing a leadered glyph. Keeping
    the tolerance and thin-line handling here prevents placement and critique from making
    contradictory decisions about the final annotation inventory (#1144).
    """
    if box_cache is None:
        box_cache = {}
    try:
        cl_min_x, cl_min_y, cl_max_x, cl_max_y = _centerline_extent(cl_item, box_cache)
    except Exception as exc:  # noqa: BLE001 — duck-typed centreline may not measure
        _log.debug("lint: centreline did not measure (%s); overlap check skips it", exc)
        return None

    label_bbox = _label_bbox(dim_item, warned)
    if label_bbox is None:
        label_bbox = _ann_box(dim_item, box_cache)
    if label_bbox is None:
        return None
    lmin_x, lmin_y, lmax_x, lmax_y = label_bbox

    # A diagonal or elbowed centreline's aggregate AABB contains a large empty
    # triangle. ADR 0009 makes its real components authoritative for BOTH the hit
    # gate and the reported magnitude: a remote shaft must not inflate a 0.1 mm
    # ring-edge touch into a legibility warning.
    segments = getattr(
        cl_item,
        "centerline_segments",
        getattr(cl_item, "segments", None),
    )
    component_boxes = getattr(cl_item, "centerline_boxes", ())

    aggregate_location = ((cl_min_x + cl_max_x) / 2.0, (cl_min_y + cl_max_y) / 2.0)

    def overlap(extent, *, segment=False):
        min_x, min_y, max_x, max_y = extent
        width = max_x - min_x
        height = max_y - min_y
        # A segment is a zero-width stroke, not a filled version of its clipped
        # AABB.  Measure penetration on the stroke's minor axis and travelled
        # distance on its major axis.  This keeps a deep near-vertical crossing
        # significant without reviving the empty diagonal-triangle false positive.
        line_x = segment and width < height
        line_y = segment and height < width
        if line_x or (not segment and width < 0.1):
            centre_x = (min_x + max_x) / 2.0
            ox = min(centre_x - lmin_x, lmax_x - centre_x) if lmin_x < centre_x < lmax_x else 0.0
        else:
            ox = max(0.0, min(lmax_x, max_x) - max(lmin_x, min_x))
        if line_y or (not segment and height < 0.1):
            centre_y = (min_y + max_y) / 2.0
            oy = min(centre_y - lmin_y, lmax_y - centre_y) if lmin_y < centre_y < lmax_y else 0.0
        else:
            oy = max(0.0, min(lmax_y, max_y) - max(lmin_y, min_y))
        if ox <= 0.5 or oy <= 0.5:
            return None
        return ox, oy, aggregate_location

    if segments or component_boxes:
        segment_extents = [
            extent
            for start, end in (segments or ())
            if (extent := _segment_clip_extent(start, end, label_bbox)) is not None
        ]
        box_extents = [box for box in component_boxes if _boxes_overlap(box, label_bbox)]
        significant = [
            result for extent in segment_extents if (result := overlap(extent, segment=True))
        ]
        significant.extend(result for extent in box_extents if (result := overlap(extent)))
        return (
            max(significant, key=lambda result: (result[0] * result[1], result))
            if significant
            else None
        )

    return overlap((cl_min_x, cl_min_y, cl_max_x, cl_max_y))


#: Degree markers a dimension label may carry. ``°`` is what the engine and AP242 emit;
#: ``deg`` is admitted because an authored label is free text.
_DEGREE_MARKS = ("\u00b0", "deg")


def _is_angular_label(label: str) -> bool:
    """Whether *label* states an angle rather than a length."""
    lowered = label.lower()
    return any(mark in lowered for mark in _DEGREE_MARKS)


#: At or above this relative discrepancy a dimension does not merely round differently from
#: its geometry — it states something false, and the drawing asserts a measurement the part does
#: not have. That is a different KIND of defect from everything else lint reports: an
#: overflowing view or a missing callout leaves the drawing INCOMPLETE, while this leaves it
#: actively MISLEADING, and a reader has no way to tell which of the two numbers to believe.
#:
#: So it is an ERROR, and a drawing carrying one cannot report ``passed: True``. Measured
#: before that change on A2: an authored dimension labelled 99 over a 16 mm path — a 518%
#: contradiction — reported ``passed: True``, ``errors: 0``, legacy ``score`` 0.95, with
#: that contradiction as the ONLY finding. The severities were inverted against
#: manufacturing risk: a view running off the page was an error while a false measurement
#: was a warning (#1153). (An earlier version of this comment blamed an "unrelated
#: `view_out_of_bounds`" — that belongs to the A3 variant, where ``passed`` was already
#: ``False``; on A2 nothing else was wrong at all, and a finding that "fails the drawing"
#: cannot coexist with ``passed: True``.)
#:
#: The threshold exists because the 0.5% REPORTING floor below is close enough to display
#: rounding and projected foreshortening to be a judgement call; a discrepancy of several
#: percent cannot be either.
#:
#: It is a POLICY NUMBER, chosen, with no observed sub-threshold case to calibrate against.
#: Measured on the DEFAULT page — which is what `build_drawing(step)` does — the corpus
#: produces nine discrepancies, all on `pmi="annotate"`: 15.7, 90.4, 92.3, 96.1 x3, 97.6 x2
#: and 99.1 per cent, across CTC-03/04/05. The smallest is 15.7%, three times the threshold.
#:
#: Three earlier versions of this comment were wrong about that corpus, which is why the
#: qualification matters. The first said "every real case observed sits far above it" and
#: listed numbers including one not reproducible anywhere. The second cited a CTC-01 record
#: at 3.9% that does not exist — that record is ANGULAR, and #1207 refuses it before
#: anything can measure it. The third said "the only discrepancy of any size is 99.1%",
#: true only with `page="A3"` forced, which is not the default path. Measure on the default
#: page, and say which page, before adding a number here.
#:
#: What can be stated exactly: a foreshortened dimension gives `sec θ - 1`, so 0.05
#: tolerates obliquity up to 17.75 degrees. If a more oblique dimension turns up, widen the
#: threshold on that evidence rather than assuming noise.
_MATERIAL_LABEL_DISCREPANCY = 0.05


def _lint_dim(item, part_bbox, issues, drawing_scale: float = 1.0, box_cache=None) -> None:
    label = _item_label(item)
    measured = getattr(item, "measured_length", None)

    # An ANGULAR label is denominated in degrees; `measured_length` is a projected path in
    # millimetres. Comparing them is a category error, not a discrepancy — it reported a
    # 60 deg dovetail flank as "differs from measured path length 16.000 by 275.0%",
    # blaming an axis swap for a units mismatch (#1177). The label is the discriminator
    # because a rendered annotation carries no `dimension_kind` and cannot be asked what
    # it is.
    #
    # Be honest about the reach: this catches an AUTHORED label that spells the unit, and
    # nothing else. Neither real angular record in the fixtures does — CTC-01's is
    # '60 ±0.5' and CTC-04's is '90 ±1', and 0 of 194 PMI records carry a degree marker —
    # so on the imported path this guard is inert and the renderer's category refusal is
    # the whole protection. An earlier version of this comment claimed the marker "survives
    # every path into lint", which is false. Tagging the annotation with its kind would fix
    # that properly; it is not done here because nothing angular now reaches the renderer.
    #
    # Both guards apply: an angular label is skipped outright (its units differ), and a
    # repeat label is read as its producer declared it (#1153).
    label_val = _label_reading(item, label)
    if label_val is not None and measured is not None and not _is_angular_label(label):
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
                        severity="error" if ratio >= _MATERIAL_LABEL_DISCREPANCY else "warning",
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


def _lint_leader(item, issues, box_cache=None, warned=None) -> None:
    # #701: was a whole-body `except Exception: pass` — an internal bug silently
    # disabled the check forever. Only the duck-typed reads are guarded now.
    box = _label_bbox(item, warned)
    if box is None:
        box = _ann_box(item, box_cache if box_cache is not None else {})
    if box is None:
        return
    minx, miny, maxx, maxy = box
    try:
        ex, ey = item.elbow
    except Exception as exc:  # noqa: BLE001 — duck-typed elbow may not unpack
        _log.warning(
            "lint: unreadable elbow on leader %r (%s); leader_line_through_text skipped",
            _item_label(item) or "?",
            exc,
        )
        return
    if minx <= ex <= maxx and miny <= ey <= maxy:
        issues.append(
            LintIssue(
                severity="error",
                message=(
                    f"Leader '{getattr(item, 'label', '?')}': elbow point "
                    f"({ex:.2f}, {ey:.2f}) is inside the label bbox — leader "
                    f"line passes through the text"
                ),
                location=(ex, ey),
                code="leader_line_through_text",
            )
        )
