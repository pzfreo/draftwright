"""Sheet layout — compose-then-pack scale/page selection (#138 / ADR 0005, P3; ADR 0004).

The outer layout: estimate each view's annotation footprint (strip depths, anno
boxes, ViewBlock half-extents), then choose the (scale, page) whose composed +
packed blocks fit the sheet disjoint (`choose_scale`), and lay the chosen geometry
into page zones (`_layout_geometry`/`_build_zones`). Footprints are page-mm box
layouts, never bbox-measured geometry (perf).

Below make_drawing in the DAG: imports only `_core` + build123d_drafting; the
measure-and-repack pass (`_repack`, coupled to `_assemble`) stays in the builder.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from types import SimpleNamespace

from build123d_drafting.helpers import format_drawing_scale

from draftwright._core import (
    _DIM_PAD,
    _FONT_SIZE,
    _ISO_MIN_FIT_FRAC,
    _ISO_WIDTH_BUDGET,
    _LADDER,
    _MARGIN,
    _PAGE_SIZES,
    _SCALES,
    _SLOT_DIM_HEIGHT,
    _SLOT_DIM_STEP,
    _SLOT_DIM_WIDTH,
    _STRIP_GAP,
    _STRIP_SPACING,
    _TABULATE_MIN_HOLES,
    _TB_CLEAR,
    _TB_H,
    Strip,
    ViewZones,
    _fmt,
    _largest_empty_rect,
    _parse_page,
    _tag_sequence,
    _tb_width,
    _text_width,
    _tol_suffix,
)
from draftwright.layout import fit_box

_log = logging.getLogger(__name__)


def _est_right_strip_depth(n_steps: int) -> float:
    """Depth needed to the right of the front view.

    Always includes dim_height (1 slot).  *n_steps* dim_step slots follow if
    any step levels are present.  Returns the minimum corridor width (from view
    edge to outer_limit) that makes all those allocations succeed.
    """
    n = 1 + max(n_steps, 0)  # dim_height + one slot per step dim
    # gap + dim_height + (n-1) step slots each preceded by one spacing
    return float(_STRIP_GAP + _SLOT_DIM_HEIGHT + (n - 1) * (_STRIP_SPACING + _SLOT_DIM_STEP))


def _est_pv_below_depth() -> float:
    """Depth needed below the plan view: dim_width (always one slot)."""
    return float(_STRIP_GAP + _SLOT_DIM_WIDTH)


def _est_pv_above_depth(
    model, font_size: float = _FONT_SIZE, pad_around_text: float = 2.0
) -> float:
    """Estimate the depth above the plan view consumed by X-location dims, which
    tier one per distinct datum-X reference (#36) — so the layout can reserve it
    (and a balloon row beyond) *before* placing views, instead of letting the
    tiers spill into headroom (#121).

    WIP estimate standing in for ADR 0004's "lay out, don't predict": a
    conservative upper bound (one spare tier for the pitch dim / rounding), which
    the packer absorbs by scale rather than under-reserving and overlapping.
    Scale-independent (tier height is fixed page-mm).
    """
    z_refs_x: list[float] = []
    for f in model.features:
        if f.kind == "pattern" and f.member.frame.axis == "z":
            # bolt circle's X-ref is the pattern centre (its frame origin); other
            # patterns tier off their first member's X, as the record path did.
            z_refs_x.append(
                f.frame.origin[0]
                if f.pattern == "bolt_circle"
                else (f.members or (f.member.frame.origin,))[0][0]
            )
        elif f.kind == "hole" and f.frame.axis == "z":
            z_refs_x += [pos[0] for pos in (f.members or (f.frame.origin,))]
    distinct: list[float] = []
    for x in sorted(z_refs_x):
        if not distinct or abs(x - distinct[-1]) > 0.5:
            distinct.append(x)
    if not distinct:
        return 0.0
    tier = font_size + 2 * pad_around_text
    return (len(distinct) + 1) * tier  # +1 tier: pitch dim / rounding headroom


def _est_plan_halo(font_size: float = _FONT_SIZE) -> float:
    """Per-side standoff band (page-mm) reserved around the plan view when its
    holes will be ballooned, so the leadered balloon ring sits in clear space
    off the part instead of jamming the views together (#111).

    Scale-independent (font_size is fixed page-mm), like the strip depths: a
    leader standoff + one balloon diameter (``2·r = 3·font_size``) + clearance.
    """
    return _STRIP_GAP + 3 * font_size + _STRIP_SPACING


def _will_balloon(model) -> bool:
    """A-priori (pre-layout) prediction that the plan view will escalate to a
    leadered hole-chart, so its balloon halo can be reserved before the views
    are placed (#111, approach A).

    Conservative and scale-independent: fires when there are at least
    ``_TABULATE_MIN_HOLES`` plan-view holes that are *not* mostly covered by a
    detected pattern (a patterned set is grouped into one ``n× ⌀`` callout +
    pattern dim, so it does not balloon).  May occasionally over-reserve (a
    little wasted corridor) or, if the runtime trigger fires anyway, fall back
    to placing balloons in the unreserved margin — both are graceful.
    """
    # Every z-axis hole occurrence: loose HoleFeature members + pattern members.
    loose = sum(
        len(f.members or (f.frame.origin,))
        for f in model.features
        if f.kind == "hole" and f.frame.axis == "z"
    )
    covered = sum(
        f.count for f in model.features if f.kind == "pattern" and f.member.frame.axis == "z"
    )
    total = loose + covered
    if total < _TABULATE_MIN_HOLES:
        return False
    return bool(covered < 0.8 * total)


def _wrap_table_rows(header, data, ncols):
    """Local mirror of the hole-table row wrapping used by the annotation pass."""

    per = math.ceil(len(data) / ncols)
    blank = ("",) * len(header)
    wide = [tuple(header) * ncols]
    for r in range(per):
        row: tuple = ()
        for c in range(ncols):
            idx = c * per + r
            row += data[idx] if idx < len(data) else blank
        wide.append(row)
    return wide


def _est_table_size(
    rows, font_size: float = _FONT_SIZE, pad_around_text: float = 2.0, block_cols=None
):
    """Table footprint estimate matching ``drawing._build_table``'s sizing model."""

    if not rows:
        return None
    fs = font_size
    pad = pad_around_text
    row_h = fs + 2 * pad
    ncol = len(rows[0])
    bc = block_cols if (block_cols and ncol % block_cols == 0 and block_cols < ncol) else ncol
    block_gap = 3 * pad
    col_w = [
        max(max(_text_width(str(r[c]), fs) for r in rows) + 2 * pad, fs * 2.5) for c in range(ncol)
    ]
    total_w = sum(col_w) + (max(ncol // bc - 1, 0) * block_gap)
    total_h = row_h * len(rows)
    return (total_w, total_h)


def _est_hole_table_sizes(
    model,
    bb,
    font_size: float = _FONT_SIZE,
    pad_around_text: float = 2.0,
) -> tuple[tuple[float, float], ...]:
    """Possible wrapped hole-chart footprints for scale/page fitness (#517).

    The runtime resolver tries the same one-to-four wrapped table blocks after
    replacing dense scattered plan-hole dimensions. This estimate lets the outer
    layout reject a page/scale where none of those table shapes can coexist with
    the placed blocks, instead of discovering that only after annotation.
    """

    # Scattered (loose, non-patterned) z-axis holes — one per member; a loose
    # HoleFeature is by construction not a pattern member (ADR 0008; #584 WP1 A).
    z_holes = [
        (f.diameter, pos)
        for f in model.features
        if f.kind == "hole" and f.frame.axis == "z"
        for pos in (f.members or (f.frame.origin,))
    ]
    if len(z_holes) < _TABULATE_MIN_HOLES:
        return ()
    dx, dy = bb.min.X, bb.min.Y
    header = ("TAG", "ø", "X", "Y")
    data = [
        (tag, f"ø{_fmt(dia)}", _fmt(pos[0] - dx), _fmt(pos[1] - dy))
        for tag, (dia, pos) in zip(_tag_sequence(len(z_holes)), z_holes, strict=True)
    ]
    return tuple(
        size
        for ncols in (1, 2, 3, 4)
        if (
            size := _est_table_size(
                _wrap_table_rows(header, data, ncols), font_size, pad_around_text, len(header)
            )
        )
        is not None
    )


def _est_planned_bore_callout_width(
    groups, draft, font_size: float = _FONT_SIZE, pad_around_text: float = 2.0
) -> float:
    """Estimate widest hole/pattern callout from planned IR dimensions.

    The single bore-callout-width estimator for page/scale selection — detected and
    declared parts both size through it off the IR (#584 WP1 A; it replaced the old
    record-based ``_est_bore_callout_width``). Planned groups see authored decorations
    (e.g. bore tolerances) a detection-derived ``HoleSpec`` could not. Kept in the layout
    estimator layer so page/scale selection does not import renderers to size text (#450).
    """

    def _first(group, kind: str, *roles: str) -> float | None:
        for role in roles:
            for pd in group.dims:
                if pd.param.kind == kind and pd.param.role == role:
                    return float(pd.param.value)
        return None

    def _tol(group):
        return next(
            (
                pd.param.tolerance
                for pd in group.dims
                if pd.param.kind == "diameter" and pd.param.role == "bore"
            ),
            None,
        )

    gap = 0.45 * font_size
    sym_w = font_size
    max_w = 0.0
    for group in groups:
        feat = group.feature
        if getattr(feat, "kind", None) not in ("hole", "pattern"):
            continue
        bore = _first(group, "diameter", "bore")
        if bore is None:
            continue
        depth = _first(group, "depth", "bore")
        cbore_dia = _first(group, "diameter", "counterbore", "spotface")
        cbore_depth = _first(group, "depth", "counterbore", "spotface")
        suffix = None
        if getattr(feat, "kind", None) == "pattern":
            if getattr(feat, "pattern", None) == "bolt_circle" and feat.bcd is not None:
                suffix = f"EQ SP ON ø{_fmt(feat.bcd)} BC"
            elif getattr(feat, "pattern", None) == "grid" and feat.rows and feat.cols:
                suffix = f"({feat.rows}×{feat.cols})"

        token_w: list[float] = []
        count = getattr(feat, "count", None)
        if count and count > 1:
            token_w.append(_text_width(f"{count}×", font_size))
        token_w.append(sym_w)  # ⌀ symbol
        token_w.append(_text_width(f"{_fmt(bore)}{_tol_suffix(_tol(group), draft)}", font_size))
        if depth is None:
            token_w.append(_text_width("THRU", font_size))
        else:
            token_w.append(sym_w)  # depth symbol
            token_w.append(_text_width(_fmt(depth), font_size))
        if cbore_dia is not None:
            token_w.append(sym_w)  # counterbore/spotface symbol
            token_w.append(sym_w)  # ⌀
            token_w.append(_text_width(_fmt(cbore_dia), font_size))
            if cbore_depth is not None:
                token_w.append(sym_w)  # depth symbol
                token_w.append(_text_width(_fmt(cbore_depth), font_size))
        csink_dia = _first(group, "diameter", "countersink")
        if csink_dia is not None:
            token_w.append(sym_w)  # countersink symbol
            token_w.append(sym_w)  # ⌀
            token_w.append(_text_width(_fmt(csink_dia), font_size))
            csink_angle = _first(group, "angle", "countersink")
            if csink_angle is not None:
                token_w.append(_text_width(f"× {_fmt(csink_angle)}°", font_size))
        if suffix is not None:
            token_w.append(_text_width(suffix, font_size))

        n = len(token_w)
        max_w = max(max_w, sum(token_w) + max(n - 1, 0) * gap + pad_around_text)
    return max_w


@dataclass
class StripDepths:
    """Annotation strip depths (page-mm) computed before view positions are fixed.

    Drives the inter-view corridor widths in the two-pass layout (#131).
    """

    right: float  # horizontal corridor right of FV/PV → gap_fv_sv
    left: float  # horizontal corridor left of FV/PV
    top: float = 0.0  # band above PV for tiered X-location dims (#121)
    pv_halo: float = 0.0  # balloon standoff band reserved around the plan view (#111)


def _measure_strips(
    model,
    n_steps: int,
    bb,
    font_size: float = _FONT_SIZE,
    arrow_length: float = 2.7,
    pad_around_text: float = 2.0,
    bore_callout_width: float = 0.0,
) -> StripDepths:
    """Compute annotation strip depths from composed annotation boxes (Pass 1 of #131).

    All annotation sizes are scale-independent because font_size is a fixed
    page-mm constant, so there is no circularity with choose_scale().
    *arrow_length* and *pad_around_text* should come from ``draft_preset(...)``.
    """
    return _footprint_from_boxes(
        _compose_anno_boxes(
            model,
            n_steps,
            bore_callout_width=bore_callout_width,
            font_size=font_size,
            arrow_length=arrow_length,
            pad_around_text=pad_around_text,
        )
    )


@dataclass(frozen=True)
class AnnoBox:
    """A composed annotation band as a page-mm box (#112, ADR 0004 Step 4).

    ``side`` is the view side the band sits on (``"right"``/``"left"`` of the
    front/plan views, or ``"plan_halo"`` for the balloon standoff ring);
    ``depth`` is the band's perpendicular extent from the view edge.  A view's
    footprint is the deepest band per side — see ``_footprint_from_boxes``.

    This is the box-model expression of the scalar corridor reservation that
    ``_measure_strips`` computes (Step 4a): every band that can drive a
    ``StripDepths`` field is emitted as an ``AnnoBox``, and the deepest band per
    side wins (see ``_footprint_from_boxes``).  Today the depths are the same
    estimates ``_measure_strips`` uses, so the two are interchangeable
    (byte-identical); later steps replace the estimates with depths measured
    from the real placement.
    """

    side: str
    depth: float


def _compose_anno_boxes(
    model,
    n_steps: int,
    bore_callout_width: float = 0.0,
    font_size: float = _FONT_SIZE,
    arrow_length: float = 2.7,
    pad_around_text: float = 2.0,
) -> list[AnnoBox]:
    """Compose a drawing's annotation bands as ``AnnoBox`` boxes (#112, Step 4a).

    This is the annotation-footprint authority for scale/page layout. Each
    contributing furniture band is emitted as a box; ``_measure_strips`` only
    reduces these boxes to the legacy ``StripDepths`` shape. Reads the IR
    (``model.features``) — detected and declared parts size through one path
    (#584 WP1 A); ``bore_callout_width`` is the planner-derived callout width the
    caller measured with :func:`_est_planned_bore_callout_width`.
    """
    boxes = [AnnoBox("right", _est_right_strip_depth(n_steps))]  # FV right dim ladder
    bore_depth = bore_callout_width
    if bore_depth > 0:
        # elbow clearance + leader-to-label gap, as in _measure_strips
        bore_depth += pad_around_text + arrow_length
        boxes.append(AnnoBox("right", bore_depth))  # FV/PV right bore callouts
        boxes.append(AnnoBox("left", bore_depth))  # FV/PV left bore callouts
    # Authored Z-axis linear dimensions (Sheet.dimension / AP242 PMI) render as height
    # dims to the front LEFT/RIGHT strips (#562). The right strip already holds the
    # envelope height + step ladder, but the left has only its _DIM_PAD floor, so an
    # authored Z dim was queued and then dropped as "no room". Reserve a slot per authored
    # Z dim on BOTH sides (they split by x position only at layout, so reserve
    # conservatively) — enough depth for the corridor solve to place them.
    z_authored = sum(
        1
        for f in model.features
        if f.kind == "authored_dimension"
        and f.dominant_axis == "Z"
        and getattr(f, "dimension_kind", None) not in ("diameter", "radius", "angular")
    )
    if z_authored:
        slot = _SLOT_DIM_STEP + _STRIP_SPACING
        boxes.append(AnnoBox("right", _est_right_strip_depth(n_steps) + z_authored * slot))
        boxes.append(AnnoBox("left", _STRIP_GAP + z_authored * slot))
    above = _est_pv_above_depth(model, font_size, pad_around_text)
    if above > 0:
        boxes.append(AnnoBox("above", above))  # tiered X-location dims above PV (#121)
    if _will_balloon(model):
        boxes.append(AnnoBox("plan_halo", _est_plan_halo(font_size)))
    return boxes


def _footprint_from_boxes(boxes: list[AnnoBox]) -> StripDepths:
    """Reduce composed ``AnnoBox`` bands to per-side corridor depths (Step 4a).

    Each ``StripDepths`` field is the deepest band on its side; ``left`` keeps
    the ``_DIM_PAD`` floor it has in ``_measure_strips``.
    """

    def deepest(side: str) -> float:
        return max((b.depth for b in boxes if b.side == side), default=0.0)

    return StripDepths(
        right=deepest("right"),
        left=max(_DIM_PAD, deepest("left")),
        top=deepest("above"),
        pv_halo=deepest("plan_halo"),
    )


def _fits(
    x_size,
    y_size,
    z_size,
    scale,
    page_w,
    page_h,
    tb_w,
    n_steps: int = 0,
    strips: StripDepths | None = None,
    pack_iso_2d: bool = False,
    section: bool = False,
    table_sizes=(),
) -> bool:
    """True if the composed 4-view footprint fits the page at this scale.

    ``pack_iso_2d=False`` uses the automatic-selection verdict exposed by
    :func:`_layout_geometry`; ``True`` uses the packed override verdict for
    explicit page/scale requests. The old arithmetic no longer lives here, so
    auto selection and measure-repack consume the same layout authority (#519).
    """
    g = _layout_geometry(
        x_size,
        y_size,
        z_size,
        scale,
        page_w,
        page_h,
        tb_w,
        strips,
        n_steps,
        section=section,
        table_sizes=table_sizes,
        warn_no_iso=False,
    )
    return bool(g.fits if pack_iso_2d else g.auto_fits)


def _bisect_fit_scale(
    x_size, y_size, z_size, pw, ph, tb, n_steps, strips, pack_iso_2d, section=False, table_sizes=()
):
    """Largest scale at which the 4-view layout fits ``(pw, ph)``, found by bisection —
    the layout is monotone in scale (a smaller scale never fits worse). Used only as the
    #350 backstop when even the smallest ISO 5455 ladder scale (1:10000) overflows, i.e.
    an out-of-domain-huge part. Returns ``None`` only if the page cannot hold the layout
    at any positive scale (a degenerate page)."""
    hi = _SCALES[-1]  # 1:10000 — the smallest laddered scale, already known not to fit
    lo = 0.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if _fits(
            x_size,
            y_size,
            z_size,
            mid,
            pw,
            ph,
            tb,
            n_steps=n_steps,
            strips=strips,
            pack_iso_2d=pack_iso_2d,
            section=section,
            table_sizes=table_sizes,
        ):
            lo = mid
        else:
            hi = mid
    return lo if lo > 0.0 else None


def choose_scale(
    x_size: float,
    y_size: float,
    z_size: float,
    n_steps: int = 0,
    scale=None,
    page=None,
    strips: StripDepths | None = None,
    section: bool = False,
    table_sizes=(),
) -> tuple:
    """Return (SCALE, PAGE_W, PAGE_H, TB_W) for a 4-view layout.

    Layout columns: [front(x×z)] [side(y×z)] [iso(~0.7*max)] [title block].
    Rows: [plan(x×y)] above [front/side].
    Tries ISO A-series pages (A4→A3→A2→A1→A0) at preferred scales, including
    ISO 5455 enlargement scales (10:1, 5:1) so small parts get legible views.
    A4 uses a 120 mm title block; A3+ use 150 mm. The title block only
    constrains row width when the view rows would overlap it vertically.

    Args:
        scale: optional fixed scale factor (e.g. ``5`` for 5:1, ``0.5`` for
            1:2); the page is then chosen as the smallest A-series sheet that
            fits.
        page: optional fixed page — an ISO name (``"A3"``), ``"WIDTHxHEIGHT"``
            in mm, or a ``(width, height)`` tuple; the scale is then chosen as
            the largest standard scale that fits. When both ``scale`` and
            ``page`` are given they are used as-is (a warning is logged if the
            layout does not fit).
    """
    if scale is not None and float(scale) <= 0:
        raise ValueError(f"scale must be positive, got {scale!r}")
    if scale is not None and page is not None:
        pw, ph, tb = _parse_page(page)
        if not _fits(
            x_size,
            y_size,
            z_size,
            float(scale),
            pw,
            ph,
            tb,
            n_steps=n_steps,
            strips=strips,
            pack_iso_2d=True,
            section=section,
            table_sizes=table_sizes,
        ):
            _log.warning(
                "Requested scale %s on %s page may not fit the 4-view layout", scale, page
            )
        return float(scale), pw, ph, tb
    if page is not None:
        pw, ph, tb = _parse_page(page)
        candidates = [(s, pw, ph, tb) for s in _SCALES]
        pack_iso_2d = True
    elif scale is not None:
        candidates = [(float(scale), pw, ph, _tb_width(pw)) for pw, ph in _PAGE_SIZES.values()]
        pack_iso_2d = True
    else:
        candidates = _LADDER
        pack_iso_2d = False
    for cand in candidates:
        if _fits(
            x_size,
            y_size,
            z_size,
            *cand,
            n_steps=n_steps,
            strips=strips,
            pack_iso_2d=pack_iso_2d,
            section=section,
            table_sizes=table_sizes,
        ):
            return cand
    # The ISO 5455 ladder exhausted with no standard fit (a part too large even for
    # A0 1:10000). Rather than return a layout that overflows (#350), bisect for the
    # largest scale that genuinely fits on the largest candidate sheet — the layout is
    # monotone in scale — so choose_scale never hands back an overflowing (scale, page).
    # A pinned scale is the one thing we may not reduce (that path returned above).
    if scale is None:
        _pw, _ph, _tb = candidates[-1][1], candidates[-1][2], candidates[-1][3]
        s = _bisect_fit_scale(
            x_size,
            y_size,
            z_size,
            _pw,
            _ph,
            _tb,
            n_steps,
            strips,
            pack_iso_2d,
            section,
            table_sizes,
        )
        if s is not None:
            _log.warning(
                "No standard scale fits %.0f × %.0f × %.0f mm; using computed %s",
                x_size,
                y_size,
                z_size,
                format_drawing_scale(s),
            )
            return s, _pw, _ph, _tb
    _log.warning(
        "No layout fits %.0f × %.0f × %.0f mm; falling back to %s",
        x_size,
        y_size,
        z_size,
        candidates[-1],
    )
    return candidates[-1]


# ---------------------------------------------------------------------------
# Shared analysis step
# ---------------------------------------------------------------------------


def _view_geom(a) -> dict:
    """The three orthographic geometry boxes as ``{view: (cx, cy, hw, hh)}``."""
    return {
        "front": (a.FV_X, a.FV_Y, a.fv_hw, a.fv_hh),
        "plan": (a.PV_X, a.PV_Y, a.fv_hw, a.pv_hh),
        "side": (a.SV_X, a.SV_Y, a.sv_hw, a.fv_hh),
    }


def _anno_bbox(o):
    """Page-space bbox of an annotation: its text ``label_bbox`` if it has one,
    else its geometric bounding box; ``None`` if neither resolves."""
    lb = getattr(o, "label_bbox", None)
    if lb is not None:
        return lb
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception as exc:  # noqa: BLE001 — not every annotation bbox-es cleanly
        # Fails open: an un-bbox-able annotation drops out of the overlap count and
        # the measured footprint. Surface it so a silently-missed repack trigger is
        # debuggable rather than invisible (#121).
        _log.debug("annotation %r has no resolvable bbox: %s", type(o).__name__, exc)
        return None


def _attribute_annotations(dwg, a):
    """Yield ``(name, view, bbox, is_label)`` for every annotation OWNED by an
    orthographic view, per the view recorded at creation (``dwg.view_of``).

    Ownership is authoritative — the annotation pass that drew it knew which view
    it belonged to and tagged it (#121) — so a front-view step dimension sitting
    in the front↔plan gap is the *front* view's, never recovered (and mis-bucketed)
    from page coordinates.  Annotations with no recorded ortho view (title block,
    iso/section/detail furniture) belong to no block and are skipped.  ``is_label``
    is true when the annotation carries a text ``label_bbox`` (a dimension value
    or balloon tag) rather than bare geometry (a centreline/leader line).
    """
    for name, o in dwg.iter_annotations():
        view = dwg.view_of(name)
        if view not in ("front", "plan", "side"):
            continue
        label = getattr(o, "label_bbox", None)
        bb = label if label is not None else _anno_bbox(o)
        if bb is None:
            continue
        yield name, view, bb, label is not None


@dataclass(frozen=True)
class ViewBlock:
    """A view's composite footprint (#112): its geometry half-extents plus the
    reserved annotation-band depth on each side (page-mm).

    The block's outer box is the geometry box inflated by its bands; the layout
    packs these blocks rather than padding bare views with scalar corridors.
    Two blocks that *abut* are separated by ``bandA + bandB``; two that *share*
    a corridor (a band against a common wall or neighbour) by ``max(bandA,
    bandB)`` — see the gap→band map in #112.
    """

    hw: float  # geometry half-width
    hh: float  # geometry half-height
    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0
    left: float = 0.0

    def footprint(self, cx, cy):
        """Outer box of this block placed at centre (cx, cy): the geometry box
        inflated by the per-side bands.  This is what the layout packs and what
        other blocks are placed around — the padding lives on the block, not on
        the caller building the obstacle."""
        return (
            cx - self.hw - self.left,
            cy - self.hh - self.bottom,
            cx + self.hw + self.right,
            cy + self.hh + self.top,
        )


def _padded_box(cx, cy, hw, hh, pad=_DIM_PAD):
    """Footprint of a fixed block at (cx, cy) with a uniform `pad` clearance band.

    The clearance is expressed as the block's own bands (see
    ``ViewBlock.footprint``) — the obstacle the iso is placed around is the
    block's footprint, not an ad-hoc inflation done by the caller.
    """
    return ViewBlock(hw, hh, pad, pad, pad, pad).footprint(cx, cy)


def _compose_view_blocks(
    x_size,
    y_size,
    z_size,
    scale,
    strips: StripDepths | None,
    n_steps: int = 0,
    *,
    section: bool = False,
) -> dict[str, ViewBlock]:
    """Compose estimated orthographic view footprints (#112).

    Each returned ``ViewBlock`` combines the view geometry half-extents with the
    annotation bands reserved for that view. `_layout_geometry` packs these
    blocks; it does not reconstruct bare-view corridor padding itself.
    """
    DIM_PAD = _DIM_PAD
    fv_hw = x_size * scale / 2
    fv_hh = z_size * scale / 2
    pv_hh = y_size * scale / 2
    sv_hw = y_size * scale / 2

    # The front and plan views form a vertical column sharing the left/right
    # corridors (max of the two); the side view shares the FV↔SV corridor; the
    # front↔plan gap is the abutting pair (fv.top + pv.bottom). When the plan
    # view is ballooned (halo > 0), its halo becomes explicit per-side bands so
    # the ballooned plan view is positioned as a unit (#111/#112).
    halo = strips.pv_halo if strips else 0.0
    strip_top = strips.top if strips else 0.0
    gap_fv_sv = max(DIM_PAD, strips.right if strips else _est_right_strip_depth(n_steps), halo)
    gap_left = max(DIM_PAD, strips.left if strips else DIM_PAD, halo)
    pv_below = _est_pv_below_depth()
    # Top band above PV. When the plan view is ballooned, the ring sits beyond
    # the tiered X-location dims, so reserve their real depth (strip_top) plus a
    # balloon row. When not ballooned, keep the historic DIM_PAD.
    pv_top = (max(DIM_PAD, strip_top) + halo) if halo > 0 else DIM_PAD
    sv_right_band = max(DIM_PAD, strips.right if (section and strips) else DIM_PAD)

    return {
        "front": ViewBlock(
            fv_hw,
            fv_hh,
            top=DIM_PAD - pv_below,
            right=gap_fv_sv,
            bottom=DIM_PAD,
            left=gap_left,
        ),
        "plan": ViewBlock(
            fv_hw,
            pv_hh,
            top=pv_top,
            right=gap_fv_sv,
            bottom=max(pv_below, halo),
            left=gap_left,
        ),
        "side": ViewBlock(sv_hw, fv_hh, right=sv_right_band),
    }


def _layout_geometry(
    x_size,
    y_size,
    z_size,
    scale,
    page_w,
    page_h,
    tb_w,
    strips,
    n_steps=0,
    blocks=None,
    section: bool = False,
    table_sizes=(),
    warn_no_iso=True,
):
    """Compute the 4-view layout geometry for a part at a given scale/page.

    Single source of truth shared by scale selection (:func:`_fits`) and view
    placement (:func:`_analyse`): the orthographic FV/PV/SV view centres and
    half-sizes, the annotation-strip gaps, and the largest empty rectangle the
    isometric view is fitted into.  Returns a :class:`SimpleNamespace`.

    When *strips* is ``None`` the annotation-corridor gaps fall back to the
    step-count estimate (used during scale selection before strips are
    measured); otherwise the measured strip depths are used.
    """
    margin = _MARGIN
    DIM_PAD = _DIM_PAD
    bbox_max = max(x_size, y_size, z_size)
    fv_hw = x_size * scale / 2
    fv_hh = z_size * scale / 2
    pv_hh = y_size * scale / 2
    sv_hw = y_size * scale / 2

    est_blocks = _compose_view_blocks(
        x_size, y_size, z_size, scale, strips, n_steps, section=section
    )
    est_fv, est_pv, est_sv = est_blocks["front"], est_blocks["plan"], est_blocks["side"]
    section_hw = max(fv_hw, 12.0)
    section_hh = fv_hh
    if blocks is not None:
        # Measure-and-repack pass (#121, ADR 0004): pack the *measured* per-view
        # footprints disjoint.  Floor each measured band at the estimate — the
        # repack may only GROW a corridor to fit annotations the estimate
        # under-sized (the documented FV-top vs PV-balloon overlap), never shrink
        # below the clearance the estimate guarantees.  The geometry half-extents
        # stay scale-derived (the estimate), not the measured block.
        def _merge(est, meas):
            return ViewBlock(
                est.hw,
                est.hh,
                top=max(est.top, meas.top),
                right=max(est.right, meas.right),
                bottom=max(est.bottom, meas.bottom),
                left=max(est.left, meas.left),
            )

        fv = _merge(est_fv, blocks["front"])
        pv = _merge(est_pv, blocks["plan"])
        sv = _merge(est_sv, blocks["side"])
    else:
        fv, pv, sv = est_fv, est_pv, est_sv
    # Per-side corridor depths from the (possibly measured) blocks. The front and
    # plan views stack vertically (same X, different Y) so they SHARE the left and
    # right corridors — the deeper of the two facing bands. The side view ABUTS
    # the column, so its gap is that column band PLUS its own facing band (sum) —
    # disjoint by construction (#121). Byte-identical for the estimator path,
    # where fv/pv bands are equal and sv.left == 0.
    col_left = max(fv.left, pv.left)
    col_right = max(fv.right, pv.right)

    # FV↔PV vertical gap = fv.top + pv.bottom (abutting → sum). Estimated and
    # measured paths now use the same block footprint semantics: if the plan
    # view carries a bottom halo, that band is part of the stacked block layout
    # rather than a special-case lift outside the ViewBlock model (#112).
    base_gap = fv.top + pv.bottom
    total_h = 2 * margin + fv.bottom + 2 * fv.hh + base_gap + 2 * pv.hh + pv.top
    y_offset = max(0.0, (page_h - total_h) / 2)

    section_right_band = (sv.right + 10.0 + 2 * section_hw + DIM_PAD) if section else 0.0
    total_content_w = (
        col_left
        + col_right
        + x_size * scale
        + y_size * scale
        + max(2 * DIM_PAD, sv.right + DIM_PAD, section_right_band)
        + bbox_max * scale * _ISO_WIDTH_BUDGET
    )
    x_offset = max(0.0, (page_w - 2 * margin - tb_w - total_content_w) / 2)

    # Anchor the FV/PV column on the SHARED left corridor (col_left), not fv.left
    # alone: when the measured plan-view left band is the deeper of the two, the
    # column must clear it or PV slides left of the centred region — and off the
    # margin (#121). Byte-identical on the estimator path (col_left == fv.left),
    # and symmetric with SV_X's use of col_right below.
    FV_X = margin + x_offset + col_left + fv.hw
    FV_Y = y_offset + margin + fv.bottom + fv.hh
    PV_X = FV_X
    # PV abuts the front-view block: gap = front top band + plan bottom band.
    PV_Y = FV_Y + fv.hh + (fv.top + pv.bottom) + pv.hh
    # SV abuts the FV/PV column: gap = column right band + SV's own left band
    # (disjoint sum). Byte-identical to the old max(fv.right, sv.left) on the
    # estimator path (fv.right == pv.right == col_right, sv.left == 0).
    SV_X = FV_X + fv.hw + col_right + sv.left + sv.hw
    SV_Y = FV_Y
    sv_right = SV_X + sv.hw + sv.right
    SECTION_X = SV_X + sv.hw + sv.right + 10.0 + section_hw
    SECTION_Y = FV_Y
    sv_right_wall = (
        (page_w - margin) if (PV_Y - pv_hh) > (margin + _TB_H) else (page_w - tb_w - margin)
    )

    drawable = (margin, margin, page_w - margin, page_h - margin)

    # Title block: a PINNED block.  Its lower-left corner sits _TB_CLEAR in from
    # the right page edge and _TB_CLEAR up from the bottom, _TB_H tall — the same
    # pin the renderer uses in _add_title_block.  Its clearance is the block's
    # own bands: DIM_PAD on the three free sides, and only down to the page
    # margin below (it abuts the bottom sheet edge).  Everything else is laid
    # out to work around its footprint.  (#112, ADR 0004.)
    title_block = ViewBlock(
        tb_w / 2,
        _TB_H / 2,
        top=DIM_PAD,
        right=DIM_PAD,
        bottom=_TB_CLEAR - margin,
        left=DIM_PAD,
    )
    tb_cx, tb_cy = page_w - _TB_CLEAR - tb_w / 2, _TB_CLEAR + _TB_H / 2

    # The iso is the one *placed* block: it takes the largest gap the fixed
    # blocks' footprints leave.  On the repack path use the MEASURED footprints
    # (bands may exceed DIM_PAD), so the iso stays clear of real annotations
    # rather than just the estimate's padded box (#121); the estimator path keeps
    # the DIM_PAD-padded boxes for byte-identity.
    if blocks is not None:
        obstacles = [
            fv.footprint(FV_X, FV_Y),
            pv.footprint(PV_X, PV_Y),
            sv.footprint(SV_X, SV_Y),
            title_block.footprint(tb_cx, tb_cy),
        ]
    else:
        obstacles = [
            _padded_box(FV_X, FV_Y, fv_hw, fv_hh),
            _padded_box(PV_X, PV_Y, fv_hw, pv_hh),
            _padded_box(SV_X, SV_Y, sv_hw, fv_hh),
            title_block.footprint(tb_cx, tb_cy),
        ]
    if section:
        section_block = ViewBlock(
            section_hw,
            section_hh,
            top=DIM_PAD,
            right=DIM_PAD,
            bottom=DIM_PAD + _FONT_SIZE + 4.0,
            left=DIM_PAD,
        )
        obstacles.append(section_block.footprint(SECTION_X, SECTION_Y))
    iso_left, iso_bottom, iso_right, iso_top = _largest_empty_rect(
        drawable, obstacles, warn=warn_no_iso
    )
    # _largest_empty_rect falls back to the full drawable when the obstacles
    # leave no genuine gap; detect that (rect overlaps an obstacle) so callers
    # can treat "no room for the iso" as not-fitting rather than a huge phantom.
    iso_valid = not any(
        iso_left < o[2] and o[0] < iso_right and iso_bottom < o[3] and o[1] < iso_top
        for o in obstacles
    )

    # Does the packed disjoint layout actually fit the sheet? — the fitness the
    # (scale, page) search optimises (#121, ADR 0004).  The union of the three
    # view *footprints* (geometry + bands) must sit inside the drawable area; the
    # orthographic views must clear the title block (stay left of its column
    # unless their bottom is above it); and the iso must have a real gap.  This is
    # what tells the repack to escalate to a larger sheet when the measured
    # footprints no longer fit the estimate's page.
    _view_boxes = [
        fv.footprint(FV_X, FV_Y),
        pv.footprint(PV_X, PV_Y),
        sv.footprint(SV_X, SV_Y),
    ]
    if section:
        _view_boxes.append(section_block.footprint(SECTION_X, SECTION_Y))
    cx0 = min(b[0] for b in _view_boxes)
    cy0 = min(b[1] for b in _view_boxes)
    cx1 = max(b[2] for b in _view_boxes)
    cy1 = max(b[3] for b in _view_boxes)
    _tol = 0.5
    _clears_tb = cy0 >= (_TB_CLEAR + _TB_H)
    _right_limit = (page_w - margin) if _clears_tb else (page_w - tb_w - margin)
    _auto_views_bottom = y_offset + margin + DIM_PAD
    _auto_clears_tb = _auto_views_bottom >= margin + _TB_H
    _auto_row_w = total_content_w + 2 * margin + (0.0 if _auto_clears_tb else tb_w)
    auto_row_fits = _auto_row_w <= page_w + _tol
    iso_fit = min(iso_right - iso_left, iso_top - iso_bottom)
    iso_fits = iso_valid and iso_fit >= _ISO_MIN_FIT_FRAC * bbox_max * scale * _ISO_WIDTH_BUDGET
    # Tables are late furniture and must reserve against the same composed view
    # footprints the fit model validates, not the iso's byte-identity padded-box
    # obstacles. Otherwise a table can be accepted in space already reserved for
    # planned strips/halos and then drop after real annotations are rendered.
    table_obstacles = [
        fv.footprint(FV_X, FV_Y),
        pv.footprint(PV_X, PV_Y),
        sv.footprint(SV_X, SV_Y),
        title_block.footprint(tb_cx, tb_cy),
    ]
    if section:
        table_obstacles.append(section_block.footprint(SECTION_X, SECTION_Y))
    if iso_valid:
        table_obstacles.append((iso_left, iso_bottom, iso_right, iso_top))
    table_fits = not table_sizes or any(
        fit_box(size, drawable, table_obstacles, "tr") is not None for size in table_sizes
    )
    fits = (
        iso_fits
        and table_fits
        and cy0 >= margin - _tol
        and cy1 <= page_h - margin + _tol
        and cx0 >= margin - _tol
        and cx1 <= _right_limit + _tol
    )
    auto_fits = (
        auto_row_fits
        and table_fits
        and cy0 >= margin - _tol
        and cy1 <= page_h - margin + _tol
        and cx0 >= margin - _tol
    )

    return SimpleNamespace(
        x_offset=x_offset,
        fv_hw=fv_hw,
        fv_hh=fv_hh,
        pv_hh=pv_hh,
        sv_hw=sv_hw,
        FV_X=FV_X,
        FV_Y=FV_Y,
        PV_X=PV_X,
        PV_Y=PV_Y,
        SV_X=SV_X,
        SV_Y=SV_Y,
        SECTION_X=SECTION_X,
        SECTION_Y=SECTION_Y,
        sv_right=sv_right,
        sv_right_wall=sv_right_wall,
        iso_left=iso_left,
        iso_bottom=iso_bottom,
        iso_right=iso_right,
        iso_top=iso_top,
        ISO_X=(iso_left + iso_right) / 2,
        ISO_Y=(iso_bottom + iso_top) / 2,
        iso_valid=iso_valid,
        iso_fit=iso_fit,
        iso_fits=iso_fits,
        table_fits=table_fits,
        iso_natural=bbox_max * scale * _ISO_WIDTH_BUDGET,
        auto_views_bottom=_auto_views_bottom,
        auto_clears_tb=_auto_clears_tb,
        auto_row_fits=auto_row_fits,
        auto_fits=auto_fits,
        fits=fits,
    )


def _build_zones(g, margin, page_h):
    """Construct the FV/PV/SV annotation :class:`ViewZones` from a placement
    namespace *g* (the return of :func:`_layout_geometry`).

    Factored out of :func:`_analyse` so the measure-and-repack pass (#121) can
    rebuild the zones from the repacked geometry with the same arithmetic — the
    zones must track the moved view centres, not the pass-1 placement.
    """
    FV_X, FV_Y, fv_hw, fv_hh = g.FV_X, g.FV_Y, g.fv_hw, g.fv_hh
    PV_X, PV_Y, pv_hh = g.PV_X, g.PV_Y, g.pv_hh
    SV_X, SV_Y, sv_hw = g.SV_X, g.SV_Y, g.sv_hw

    fv_right_edge = FV_X + fv_hw
    fv_left_edge = FV_X - fv_hw
    fv_top_edge = FV_Y + fv_hh
    fv_bottom_edge = FV_Y - fv_hh
    pv_right_edge = PV_X + fv_hw  # plan has the same X half-width as front
    pv_left_edge = PV_X - fv_hw
    pv_top_edge = PV_Y + pv_hh
    pv_bottom_edge = PV_Y - pv_hh  # = fv_top_edge + DIM_PAD
    sv_top_edge = SV_Y + fv_hh  # side view has the same Z height as front
    # Outer limit for fv/pv right strips: must not enter the side view.
    sv_left_edge = SV_X - sv_hw  # = fv_right_edge + gap_fv_sv

    fv_zones = ViewZones(
        right=Strip(fv_right_edge, sv_left_edge, direction=1),
        left=Strip(fv_left_edge, margin, direction=-1),
        # Stop the front-view 'above' strip short of pv_bottom_edge by the
        # slack the pv_below slot leaves in the gap, derived (not re-typed) so
        # it tracks _DIM_PAD and the slot constants.
        above=Strip(fv_top_edge, pv_bottom_edge - (_DIM_PAD - _est_pv_below_depth()), direction=1),
        below=Strip(fv_bottom_edge, margin, direction=-1),
    )
    pv_zones = ViewZones(
        # Outer limit = sv_left_edge (not iso_right_limit) so bore callouts in
        # the plan view are bounded by the same hard wall as the FV right strip,
        # preventing labels from crossing m_locy extension lines in the side
        # view.  gap_fv_sv is sized by _measure_strips to accommodate the widest
        # callout, so well-estimated labels will always fit within this bound.
        right=Strip(pv_right_edge, sv_left_edge, direction=1),
        left=Strip(pv_left_edge, margin, direction=-1),
        above=Strip(pv_top_edge, page_h - margin, direction=1),
        # gap_fv_pv = _DIM_PAD; pv_below needs _est_pv_below_depth() mm,
        # leaving (_DIM_PAD - _est_pv_below_depth()) mm slack (assert above).
        below=Strip(pv_bottom_edge, fv_top_edge, direction=-1),
    )
    sv_bottom_edge = SV_Y - fv_hh  # same as fv_bottom_edge; side and front share Z height
    sv_zones = ViewZones(
        # sv_right already includes DIM_PAD; anchor here so the strip never
        # places annotations inside that gap
        right=Strip(g.sv_right, g.sv_right_wall, direction=1),
        left=None,  # immediately abuts the front view's right edge
        above=Strip(sv_top_edge, page_h - margin, direction=1),
        below=Strip(sv_bottom_edge, margin, direction=-1),
    )
    return fv_zones, pv_zones, sv_zones
