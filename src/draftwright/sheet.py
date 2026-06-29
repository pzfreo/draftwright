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
from dataclasses import dataclass
from types import SimpleNamespace

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
    _axis_letter,
    _fmt,
    _largest_empty_rect,
    _parse_page,
    _tb_width,
    _text_width,
)
from draftwright.recognition import BoltCircle, HoleSpec, RectGrid

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
    holes, patterns, font_size: float = _FONT_SIZE, pad_around_text: float = 2.0
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
    patterned = {h for p in patterns for h in p.holes}
    for p in patterns:
        if _axis_letter(p.holes[0]) != "z":
            continue
        z_refs_x.append(p.center[0] if isinstance(p, BoltCircle) else p.holes[0].location[0])
    z_refs_x += [h.location[0] for h in holes if _axis_letter(h) == "z" and h not in patterned]
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


def _will_balloon(holes, patterns) -> bool:
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
    z = [h for h in holes if _axis_letter(h) == "z"]
    if len(z) < _TABULATE_MIN_HOLES:
        return False
    covered = sum(len(p.holes) for p in patterns if p.holes and _axis_letter(p.holes[0]) == "z")
    return covered < 0.8 * len(z)


# Inter-constant invariant: the gap between the front view top edge and the
# plan view bottom edge equals _DIM_PAD.  The pv_zones.below strip occupies
# that gap, so _DIM_PAD must be at least as wide as the depth pv_below needs.
# If _DIM_PAD is shrunk below _est_pv_below_depth(), dim_width would silently
# overlap the front view rather than failing an allocate().
assert _DIM_PAD >= _est_pv_below_depth(), (
    f"_DIM_PAD ({_DIM_PAD}) is smaller than pv_below slot depth "
    f"({_est_pv_below_depth()}); bump _DIM_PAD or shrink the slot constants."
)


# ---------------------------------------------------------------------------
# Two-pass layout — Pass 1: annotation strip depth measurement (#131)
#
# font_size = 3.0 mm is a fixed page-mm constant, so all annotation depths
# are scale-independent and can be computed before choose_scale() is called.
# ---------------------------------------------------------------------------


def _est_bore_callout_width(
    holes, font_size: float = _FONT_SIZE, patterns=None, pad_around_text: float = 2.0
) -> float:
    """Estimate the maximum bore callout label width (page-mm) across all holes.

    Groups holes by machining spec (same as _annotate_holes), then estimates
    the HoleCallout token sequence width using a character-based formula.
    Includes the BoltCircle suffix ("EQ SP ON ø… BC") when patterns are supplied.
    Returns the label width only — elbow_dx and gap clearance are NOT included;
    callers that need the full strip depth should add those overheads separately.
    Returns 0.0 when the hole list is empty.
    *pad_around_text* should come from ``draft_preset(...).pad_around_text``.
    """
    if not holes:
        return 0.0
    groups: dict = {}
    for h in holes:
        groups.setdefault(HoleSpec.from_hole(h), []).append(h)

    # Map spec_key → the widest pattern-callout suffix, so a spec's grouped
    # callout reserves room for it. A spec can sub-cluster into several patterns
    # (#92); the BoltCircle "EQ SP ON ø… BC" is wider than a RectGrid "(r×c)",
    # so the widest wins (the corridor must hold the longest callout).
    suffix_by_spec: dict = {}
    if patterns:
        for p in patterns:
            if isinstance(p, BoltCircle):
                s = f"EQ SP ON ø{_fmt(p.diameter)} BC"
            elif isinstance(p, RectGrid):
                s = f"({p.rows}×{p.cols})"
            else:
                continue
            key = HoleSpec.from_hole(p.holes[0])
            if len(s) > len(suffix_by_spec.get(key, "")):
                suffix_by_spec[key] = s

    h_fs = font_size
    # gap (inter-token spacing) and sym_w (geometry-symbol cell width) mirror
    # HoleCallout's own internal layout constants so the estimate matches what
    # the primitive actually strokes.  Variable text tokens are measured with
    # real glyph metrics (_text_width) rather than a character-count fudge (#31).
    gap = 0.45 * h_fs
    sym_w = h_fs
    pad = pad_around_text

    max_w = 0.0
    for spec_key, group in groups.items():
        rep = group[0]
        count = len(group) if len(group) > 1 else None
        through = rep.bottom == "through"
        step = rep.cbore or rep.spotface

        token_w: list[float] = []
        if count:
            token_w.append(_text_width(f"{count}×", h_fs))
        token_w.append(sym_w)  # ⌀ symbol
        token_w.append(_text_width(_fmt(rep.diameter), h_fs))
        if through:
            token_w.append(_text_width("THRU", h_fs))
        elif rep.depth:
            token_w.append(sym_w)  # depth symbol
            token_w.append(_text_width(_fmt(rep.depth), h_fs))
        if step:
            token_w.append(sym_w)  # counterbore/spotface symbol
            token_w.append(sym_w)  # ⌀
            token_w.append(_text_width(_fmt(step.diameter), h_fs))
            if step.depth:
                token_w.append(sym_w)  # depth symbol
                token_w.append(_text_width(_fmt(step.depth), h_fs))

        # Pattern callout suffix ("EQ SP ON ø… BC" / "(r×c)"), when this spec
        # group is recognised as a pattern.
        suffix = suffix_by_spec.get(spec_key)
        if suffix is not None:
            token_w.append(_text_width(suffix, h_fs))

        n = len(token_w)
        w = sum(token_w) + max(n - 1, 0) * gap + pad
        max_w = max(max_w, w)

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
    holes,
    patterns,
    n_steps: int,
    bb,
    font_size: float = _FONT_SIZE,
    arrow_length: float = 2.7,
    pad_around_text: float = 2.0,
) -> StripDepths:
    """Compute annotation strip depths from hole geometry (Pass 1 of #131).

    All annotation sizes are scale-independent because font_size is a fixed
    page-mm constant, so there is no circularity with choose_scale().
    *arrow_length* and *pad_around_text* should come from ``draft_preset(...)``.
    """
    bore_depth = _est_bore_callout_width(
        holes, font_size, patterns=patterns, pad_around_text=pad_around_text
    )
    # Add elbow clearance and leader-to-label gap so gap_fv_sv fully contains
    # the composed leader: elbow_dx (= draft.arrow_length) + gap
    # (= draft.pad_around_text), always present.
    if bore_depth > 0:
        bore_depth += pad_around_text + arrow_length
    right = max(_est_right_strip_depth(n_steps), bore_depth)
    left = max(_DIM_PAD, bore_depth)
    top = _est_pv_above_depth(holes, patterns, font_size, pad_around_text)
    pv_halo = _est_plan_halo(font_size) if _will_balloon(holes, patterns) else 0.0
    return StripDepths(right=right, left=left, top=top, pv_halo=pv_halo)


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
    holes,
    patterns,
    n_steps: int,
    font_size: float = _FONT_SIZE,
    arrow_length: float = 2.7,
    pad_around_text: float = 2.0,
) -> list[AnnoBox]:
    """Compose a drawing's annotation bands as ``AnnoBox`` boxes (#112, Step 4a).

    Mirrors ``_measure_strips`` exactly, but emits each contributing band as a
    box rather than folding them into three scalars up front.
    ``_footprint_from_boxes`` reduces these back to the identical
    ``StripDepths``.
    """
    boxes = [AnnoBox("right", _est_right_strip_depth(n_steps))]  # FV right dim ladder
    bore_depth = _est_bore_callout_width(
        holes, font_size, patterns=patterns, pad_around_text=pad_around_text
    )
    if bore_depth > 0:
        # elbow clearance + leader-to-label gap, as in _measure_strips
        bore_depth += pad_around_text + arrow_length
        boxes.append(AnnoBox("right", bore_depth))  # FV/PV right bore callouts
        boxes.append(AnnoBox("left", bore_depth))  # FV/PV left bore callouts
    above = _est_pv_above_depth(holes, patterns, font_size, pad_around_text)
    if above > 0:
        boxes.append(AnnoBox("above", above))  # tiered X-location dims above PV (#121)
    if _will_balloon(holes, patterns):
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
) -> bool:
    """True if the 4-view layout fits the page at this scale.

    Default (``pack_iso_2d=False``) is the conservative row model used by
    automatic scale selection: the iso view is charged a column in the view row
    alongside the title block.  This deliberately over-reserves horizontal space,
    which keeps annotation-heavy parts on a sheet large enough to place all their
    dimensions rather than dropping some onto a tighter sheet.

    When ``pack_iso_2d=True`` — used when the caller fixes the page or scale —
    the iso is instead fitted into the largest empty rectangle the placement
    engine actually uses (:func:`_layout_geometry`), so it may occupy vertical
    headroom above the views rather than a row column.  A long, short part can
    then be enlarged onto the requested sheet (e.g. 2:1 on A3), where the row
    model would have under-scaled it.

    The title block occupies only the bottom ``_TB_H`` mm of the sheet, so the
    views may extend over it horizontally as long as they clear it vertically.
    """
    bbox_max = max(x_size, y_size, z_size)
    halo = strips.pv_halo if strips else 0.0
    gap_fv_sv = max(_DIM_PAD, strips.right if strips else _est_right_strip_depth(n_steps), halo)
    gap_left = max(_DIM_PAD, strips.left if strips else _DIM_PAD, halo)
    # PV top band: when ballooned, hold the tiered X-location dims + a balloon row
    # beyond them (#121); otherwise the historic DIM_PAD (tiers spill into
    # headroom). Mirror _layout_geometry's pv.top so scale/page is sized for it.
    strip_top = strips.top if strips else 0.0
    pv_top = (max(_DIM_PAD, strip_top) + halo) if halo > 0 else _DIM_PAD
    h = _MARGIN + pv_top + y_size * scale + _DIM_PAD + z_size * scale + _DIM_PAD + _MARGIN
    if h > page_h:
        return False

    if not pack_iso_2d:
        # Conservative row model: iso + title block both charged to the row.
        w = (
            _MARGIN
            + gap_left
            + x_size * scale
            + gap_fv_sv
            + y_size * scale
            + _DIM_PAD
            + bbox_max * scale * _ISO_WIDTH_BUDGET
            + _DIM_PAD
            + tb_w
            + _MARGIN
        )
        if w <= page_w:
            return True
        views_bottom = max(0.0, (page_h - h) / 2) + _MARGIN + _DIM_PAD
        # When views clear the title block row, the iso sits above it and the
        # title block no longer constrains horizontal space — drop tb_w from w.
        return bool(w - tb_w <= page_w and views_bottom >= _MARGIN + _TB_H)

    # 2D packing: views + title block fit the row; the iso fits leftover space.
    w_views_tb = (
        _MARGIN
        + gap_left
        + x_size * scale
        + gap_fv_sv
        + y_size * scale
        + _DIM_PAD
        + tb_w
        + _MARGIN
    )
    if w_views_tb > page_w:
        views_bottom = max(0.0, (page_h - h) / 2) + _MARGIN + _DIM_PAD
        if not (w_views_tb - tb_w <= page_w and views_bottom >= _MARGIN + _TB_H):
            return False
    g = _layout_geometry(x_size, y_size, z_size, scale, page_w, page_h, tb_w, strips, n_steps)
    if not g.iso_valid:
        return False
    iso_fit = min(g.iso_right - g.iso_left, g.iso_top - g.iso_bottom)
    return bool(iso_fit >= _ISO_MIN_FIT_FRAC * g.iso_natural)


def choose_scale(
    x_size: float,
    y_size: float,
    z_size: float,
    n_steps: int = 0,
    scale=None,
    page=None,
    strips: StripDepths | None = None,
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
        ):
            _log.warning(
                "Requested scale %s on %s page may not fit the 4-view layout", scale, page
            )
        return float(scale), pw, ph, tb
    # When the caller fixes the page or scale, pack the iso into 2D space so the
    # largest scale that genuinely fits the requested sheet is chosen (the iso
    # may sit in vertical headroom).  Automatic selection stays on the
    # conservative row model, which reserves enough space for all annotations.
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
            x_size, y_size, z_size, *cand, n_steps=n_steps, strips=strips, pack_iso_2d=pack_iso_2d
        ):
            return cand
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


def _layout_geometry(
    x_size, y_size, z_size, scale, page_w, page_h, tb_w, strips, n_steps=0, blocks=None
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

    # Compose each view as a block: geometry half-extents + reserved annotation
    # bands per side (#112).  The front and plan views form a vertical column
    # sharing the left/right corridors (max of the two); the side view shares
    # the FV↔SV corridor; the front↔plan gap is the abutting pair
    # (fv.top + pv.bottom).  When the plan view is ballooned (halo > 0) its halo
    # becomes explicit per-side bands so the ballooned plan view is placed as a
    # unit — including a BOTTOM band that pushes the front view down so balloons
    # ring the part below it, not just left/right/top (#111/#112 Phase 2).  All
    # bands reduce to today's arithmetic when halo = 0 (byte-identical).
    halo = strips.pv_halo if strips else 0.0
    strip_top = strips.top if strips else 0.0
    gap_fv_sv = max(DIM_PAD, strips.right if strips else _est_right_strip_depth(n_steps), halo)
    gap_left = max(DIM_PAD, strips.left if strips else DIM_PAD, halo)
    pv_below = _est_pv_below_depth()
    # Top band above PV. When the plan view is ballooned, the ring sits beyond the
    # tiered X-location dims, so reserve their real depth (strip_top) PLUS a
    # balloon row — otherwise the ring overruns the page (#121). When NOT
    # ballooned, keep the historic DIM_PAD: the dim tiers spill harmlessly into
    # the headroom above PV, and reserving more would needlessly grow the layout
    # (and can starve the section view of its leftover space).
    pv_top = (max(DIM_PAD, strip_top) + halo) if halo > 0 else DIM_PAD
    # Estimated blocks (always built): the scale-derived geometry half-extents
    # plus the heuristic per-side corridor depths.
    est_fv = ViewBlock(
        fv_hw, fv_hh, top=DIM_PAD - pv_below, right=gap_fv_sv, bottom=DIM_PAD, left=gap_left
    )
    est_pv = ViewBlock(
        fv_hw,
        pv_hh,
        top=pv_top,
        right=gap_fv_sv,
        bottom=max(pv_below, halo),  # band below PV holds the width dim + a balloon row
        left=gap_left,
    )
    est_sv = ViewBlock(sv_hw, fv_hh, right=DIM_PAD)
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

    # Bottom balloon band: rather than pushing the front view down (which would
    # cascade into the iso/table and the scale choice), LIFT the plan view up
    # into the empty top headroom above it — the front/side views, iso and title
    # block stay anchored, so the table is undisturbed.  The lift is implicit:
    # the vertical stack is centred with the BASE front↔plan gap (so FV/SV centre
    # exactly as when halo = 0), while PV is positioned with the full ballooned
    # gap, leaving it max(0, halo - pv_below) higher.  Byte-identical when
    # halo = 0.  (#112, ADR 0004.)
    # FV↔PV vertical gap = fv.top + pv.bottom (abutting → sum). The estimator path
    # keeps its lift trick (centre on the base gap, place PV on the full gap);
    # the measured path uses the real gap directly.
    base_gap = (fv.top + pv.bottom) if blocks is not None else (fv.top + pv_below)
    total_h = 2 * margin + fv.bottom + 2 * fv.hh + base_gap + 2 * pv.hh + pv.top
    y_offset = max(0.0, (page_h - total_h) / 2)

    total_content_w = (
        col_left
        + col_right
        + x_size * scale
        + y_size * scale
        + max(2 * DIM_PAD, sv.right + DIM_PAD)
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
    # PV uses the full (ballooned) front↔plan gap while FV/SV were centred with
    # the base gap — so the plan view sits pv_lift higher: lifted into the
    # headroom, front view anchored.
    PV_Y = FV_Y + fv.hh + (fv.top + pv.bottom) + pv.hh
    # SV abuts the FV/PV column: gap = column right band + SV's own left band
    # (disjoint sum). Byte-identical to the old max(fv.right, sv.left) on the
    # estimator path (fv.right == pv.right == col_right, sv.left == 0).
    SV_X = FV_X + fv.hw + col_right + sv.left + sv.hw
    SV_Y = FV_Y
    sv_right = SV_X + sv.hw + sv.right
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
    iso_left, iso_bottom, iso_right, iso_top = _largest_empty_rect(drawable, obstacles)
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
    cx0 = min(b[0] for b in _view_boxes)
    cy0 = min(b[1] for b in _view_boxes)
    cx1 = max(b[2] for b in _view_boxes)
    cy1 = max(b[3] for b in _view_boxes)
    _tol = 0.5
    _clears_tb = cy0 >= (_TB_CLEAR + _TB_H)
    _right_limit = (page_w - margin) if _clears_tb else (page_w - tb_w - margin)
    fits = (
        iso_valid
        and cy0 >= margin - _tol
        and cy1 <= page_h - margin + _tol
        and cx0 >= margin - _tol
        and cx1 <= _right_limit + _tol
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
        sv_right=sv_right,
        sv_right_wall=sv_right_wall,
        iso_left=iso_left,
        iso_bottom=iso_bottom,
        iso_right=iso_right,
        iso_top=iso_top,
        ISO_X=(iso_left + iso_right) / 2,
        ISO_Y=(iso_bottom + iso_top) / 2,
        iso_valid=iso_valid,
        iso_natural=bbox_max * scale * _ISO_WIDTH_BUDGET,
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
