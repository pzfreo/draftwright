"""Build orchestration (#138 / ADR 0005, P6).

The pipeline driver: `build_drawing` runs analysis -> assemble (project +
annotate + fit) -> measure-and-repack -> returns the `Drawing`; `make_drawing`
wraps it with export; plus the editable-script generator and the CLI. Imports
`drawing` (the result object), `analysis`, the annotation orchestrator, and the
stage modules -- never make_drawing -- so the graph stays a DAG.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Literal

from build123d import (
    Shape,
)
from build123d_drafting.helpers import (
    draft_preset,
    format_drawing_scale,
)

from draftwright._core import (
    _FONT_SIZE,
    _LADDER,
    _PAGE_SIZES,
    _SCALES,
    Analysis,
    _add_title_block,
    _iso_bbox,
    _log,
    _parse_page,
    _Projector,
    _tb_width,
)
from draftwright.analysis import _analyse
from draftwright.annotate import _auto_annotate, build_model, build_rotational_feature
from draftwright.annotations.sections import feature_hole_keys
from draftwright.drawing import Drawing
from draftwright.fonts import PLEX_MONO
from draftwright.model import (
    Datum,
    Feature,
    PartModel,
    StepFeature,
    build_pmi_features,
    display,
    plan_sections,
)
from draftwright.projection import (
    _fit_iso_view,
    _project_iso,
)
from draftwright.sheet import (
    ViewBlock,
    _attribute_annotations,
    _build_zones,
    _layout_geometry,
    _view_geom,
)

_TB_W = 150.0
# Minimum acceptable projected view dimension (page-mm).  Below this, annotation
# geometry (leader wires, centre marks, bore callout elbows) can degenerate and
# cause OCCT Standard_DomainError / SIGABRT (#129).


# ---------------------------------------------------------------------------
# SVG post-processing
# ---------------------------------------------------------------------------


# Equidistance tolerance (page-mm) for accepting a sampled silhouette spline as
# a circle about a known projected axis.  Loose enough to swallow HLR's spline
# approximation error, tight enough not to round a genuinely off-axis curve.

_REPACK_TOL = 0.75


def _cross_view_overlaps(dwg, a) -> int:
    """Count pairs of annotations attributed to *different* views whose boxes
    overlap — the #121 failure (a plan-view balloon over a front-view dimension).

    This is the repack trigger: a clean sheet (no cross-view overlap) is left
    exactly as pass 1 placed it, so well-estimated parts stay byte-identical;
    only a sheet with a real collision is re-packed (ADR 0004).
    """
    items = list(_attribute_annotations(dwg, a))
    n = 0
    for i in range(len(items)):
        _, vi, bi, li = items[i]
        for j in range(i + 1, len(items)):
            _, vj, bj, lj = items[j]
            # Only a collision involving a text label matters — two bare lines
            # (extension/leader) crossing between views is normal drafting.
            if vi == vj or not (li or lj):
                continue
            if min(bi[2], bj[2]) > max(bi[0], bj[0]) and min(bi[3], bj[3]) > max(bi[1], bj[1]):
                n += 1
    return n


def _annotation_view_overlaps(dwg, a) -> int:
    """Count view-owned annotation *labels* whose box overlaps a **different**
    view's geometry box — a dimension that has grown into a neighbouring view's
    line-work (the staggered step chain bumping the plan view above the front
    view). A third repack trigger besides cross-annotation overlap and page
    overflow: the measured blocks already capture the annotation's real depth, so
    a repack lifts the neighbouring view clear into the headroom (#293). Bare
    extension/leader lines crossing a view are normal drafting and don't count —
    only a text label landing on another view's geometry does.
    """
    geom = _view_geom(a)
    boxes = {v: (cx - hw, cy - hh, cx + hw, cy + hh) for v, (cx, cy, hw, hh) in geom.items()}
    n = 0
    for _name, v, bb, label in _attribute_annotations(dwg, a):
        if not label:
            continue
        for ov, gb in boxes.items():
            if ov == v:
                continue
            if min(bb[2], gb[2]) > max(bb[0], gb[0]) and min(bb[3], gb[3]) > max(bb[1], gb[1]):
                n += 1
                break
    return n


def _annotations_out_of_bounds(dwg, a, tol: float = 1.0) -> bool:
    """True when any view-owned annotation's footprint extends past the drawable
    area — the second repack trigger besides cross-view overlap.  A ballooned
    plan view can overflow the page top (the balloon ring) without crossing
    another view, so the page must still escalate; the measure-and-repack pass
    re-sizes it because the overflowing balloons are part of the plan footprint
    (#92).  Only view-owned annotations count — those are what a repack can move
    by escalating the sheet."""
    lo, hi_x, hi_y = a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin
    for name, o in dwg.iter_annotations():
        if dwg.view_of(name) not in ("front", "plan", "side"):
            continue
        # Match the lint, which tests each item's FULL bounding_box (extension
        # lines, arrowheads, leader + balloon ring) — not just the label rect —
        # so a dimension whose extension lines overrun the page is caught too.
        try:
            b = o.bounding_box()
            bb = (b.min.X, b.min.Y, b.max.X, b.max.Y)
        except Exception:  # noqa: BLE001 — fall back to the label rect, else skip
            lb = getattr(o, "label_bbox", None)
            if lb is None:
                continue
            bb = lb
        if bb[0] < lo - tol or bb[1] < lo - tol or bb[2] > hi_x + tol or bb[3] > hi_y + tol:
            return True
    return False


def _measure_blocks(dwg, a) -> dict:
    """Measure each orthographic view's *actual* annotation footprint from the
    laid-out drawing (#121, ADR 0004 — "lay out, don't predict").

    Each view's four band depths are how far its annotations extend beyond its
    geometry box, **measured** from what the annotation passes produced — not
    estimated. Every annotation is attributed to the nearest view (by its
    label/box centre), and the band depth on a side is the furthest that view's
    annotations reach past the geometry edge there. Returns ``{view_name:
    ViewBlock}`` whose bands the packer can place disjoint, no ``_est_*`` needed.
    """
    geom = _view_geom(a)
    ext: dict = {v: None for v in geom}
    for _name, v, bb, _label in _attribute_annotations(dwg, a):
        e = ext[v]
        ext[v] = (
            bb
            if e is None
            else (min(e[0], bb[0]), min(e[1], bb[1]), max(e[2], bb[2]), max(e[3], bb[3]))
        )

    blocks: dict = {}
    for v, (cx, cy, hw, hh) in geom.items():
        e = ext[v]
        if e is None:
            blocks[v] = ViewBlock(hw, hh)
            continue
        blocks[v] = ViewBlock(
            hw,
            hh,
            top=max(0.0, e[3] - (cy + hh)),
            right=max(0.0, e[2] - (cx + hw)),
            bottom=max(0.0, (cy - hh) - e[1]),
            left=max(0.0, (cx - hw) - e[0]),
        )
    return blocks


# ---------------------------------------------------------------------------
# Drawing builder (composable; make_drawing == build_drawing + export)
# ---------------------------------------------------------------------------


def _coerce_model(model, part, decorations=None) -> PartModel:
    """Wrap a caller-supplied ``model=`` (ADR 0011) into a :class:`PartModel`. A
    ``PartModel`` is used verbatim; a sequence of features is wrapped with the part's
    bbox, a default corner location datum (matching ``detect.py``, so hole location
    dims measure from the min corner), and an orientation inferred from any turned
    ``StepFeature`` (so a declared shaft renders as turned).

    Takes the *part* directly (not the full :class:`Analysis`) so it needs only the bbox —
    the cheap wrapping path behind :meth:`draftwright.Sheet.model` (#453), which materialises
    the IR without projecting or annotating a drawing.

    ``decorations`` (P2a) is the authored aspect side-layer — ``{(feature, kind) ->
    tolerance}`` — merged onto the model so the planner can read it; only applied when
    given (a bare ``PartModel`` keeps its own decorations otherwise). A verbatim
    ``PartModel`` is never mutated — decorations merge into a copy so the caller's
    reusable public input (ADR 0011) stays clean across builds."""
    if isinstance(model, PartModel):
        if decorations:
            return replace(model, decorations={**model.decorations, **decorations})
        return model
    features = list(model)
    bbox = part.bounding_box()
    orientation = next((f.frame.axis for f in features if isinstance(f, StepFeature)), None)
    datum = Datum(id="datum_xy", kind="point", at=(bbox.min.X, bbox.min.Y, bbox.min.Z))
    return PartModel(
        bbox=bbox,
        orientation=orientation,
        features=features,
        datums=[datum],
        decorations=decorations or {},
    )


def detect_part_model(part, *, pmi="off") -> PartModel:
    """The **detected** :class:`PartModel` for *part* — feature recognition + analysis only,
    with no view projection, annotation, repack, repair, or export (ADR 0011 #453). The cheap
    seed path behind :meth:`draftwright.Sheet.from_part`, so pure feature inspection no longer
    pays for a full drawing (nor its layout/rendering failure modes)."""
    a = _analyse(
        part, title="", number="", tolerance="ISO 2768-m", drawn_by="", out="model", pmi=pmi
    )
    model: PartModel = build_model(a)  # build_model is untyped; it returns a PartModel
    return model


def _assemble(a, out, assembly, detail_view, auto_dims, model=None, decorations=None) -> Drawing:
    """Project the 4 views for analysis *a*, run the automatic annotation
    passes, and fit the iso.  This is pass 1 of :func:`build_drawing`; with a
    repacked analysis it is also pass 2 of the measure-and-repack loop (#121)."""
    cxs, cys, czs = a.cx * a.SCALE, a.cy * a.SCALE, a.cz * a.SCALE
    dist = a.bbox_max * a.SCALE + 100

    dwg = Drawing(
        scale=a.SCALE,
        page_w=a.PAGE_W,
        page_h=a.PAGE_H,
        tb_w=a.TB_W,
        draft=draft_preset(font_size=_FONT_SIZE, decimal_precision=1, font_path=PLEX_MONO),
        look_at=(cxs, cys, czs),
        dist=dist,
        centroid=(a.cx, a.cy, a.cz),
        out=out,
        part=a.part,
        cyls=a.cyls,
        assembly=assembly,
    )
    dwg._analysis = a  # expose analysis namespace for testing and future strip access
    # Detect the IR here — before the auto_dims gate — so dwg.model() and feature edits
    # work even in manual mode (#398). _auto_annotate reads this attached model rather
    # than rebuilding. On a repack this runs again on the pass-2 drawing (freshness).
    dwg._part_model = (
        _coerce_model(model, a.part, decorations) if model is not None else build_model(a)
    )
    if model is not None:
        # A declared model skips detection, so a turned shaft carries no RotationalFeature —
        # and that feature is the sole driver of the turned-axis centrelines + the OD dimension
        # (rot furniture). Synthesise it from the (unconditional) analysis so a declared /
        # emitted-script turned part reproduces the detected drawing (#472). Gated on the
        # caller not having declared one, so an explicit choice wins.
        pm = dwg._part_model
        if not any(f.kind == "rotational" for f in pm.features):
            rot = build_rotational_feature(a)
            if rot is not None:
                dwg._part_model = replace(pm, features=[*pm.features, rot])
        # PMI (STEP AP242) is likewise detection-sourced, so a declared / emitted-script model
        # carries none. When PMI annotation is on, synthesise the same PmiFeatures detection
        # would (render_pmi reads them off the model, gated on a.pmi_mode) so a re-run reproduces
        # the PMI dims (#472). Gated on the caller not having declared PMI, so an explicit set wins.
        pm = dwg._part_model
        if a.pmi_mode == "annotate" and not any(f.kind == "pmi" for f in pm.features):
            pmi_feats = build_pmi_features(a.pmi, a.part.bounding_box())
            if pmi_feats:
                dwg._part_model = replace(pm, features=[*pm.features, *pmi_feats])
    dwg._model_declared = model is not None  # ADR 0011 #448: gate model-driven hole render

    part_s = a.part.scale(a.SCALE)
    dwg.add_view("front", part_s, (cxs, cys - dist, czs), (0, 0, 1), (a.FV_X, a.FV_Y), scaled=True)
    dwg.add_view("plan", part_s, (cxs, cys, czs + dist), (0, 1, 0), (a.PV_X, a.PV_Y), scaled=True)
    dwg.add_view("side", part_s, (cxs + dist, cys, czs), (0, 0, 1), (a.SV_X, a.SV_Y), scaled=True)
    _project_iso(dwg, a, a.SCALE, shape_s=part_s)

    if auto_dims:
        # Snapshot outer_limits before _auto_annotate tightens them against the
        # initial (possibly overflowing) iso.  After _fit_iso_view rescales the
        # iso we restore all three right strips to min(original, final_iso_x_limit)
        # so each strip reflects actual final geometry, not the transient state.
        _fv_ol = a.fv_zones.right.outer_limit
        _pv_ol = a.pv_zones.right.outer_limit
        _sv_ol = a.sv_zones.right.outer_limit
        _auto_annotate(dwg, a, detail_view=detail_view)
        _fit_iso_view(dwg, a)
        _ix0, _iy0, _, _iy1 = _iso_bbox(dwg)
        _final_iso_x_lim = _ix0 - 4
        a.fv_zones.right.outer_limit = min(_fv_ol, _final_iso_x_lim)
        a.pv_zones.right.outer_limit = min(_pv_ol, _final_iso_x_lim)
        # Only re-cap the SV right strip when the iso shares its y-range (see the
        # matching guard in _auto_annotate); otherwise restore its full width.
        if (a.SV_Y - a.fv_hh) < _iy1 and _iy0 < (a.SV_Y + a.fv_hh):
            a.sv_zones.right.outer_limit = min(_sv_ol, _final_iso_x_lim)
        else:
            a.sv_zones.right.outer_limit = _sv_ol
    else:
        _fit_iso_view(dwg, a, annotate=False)
        _add_title_block(dwg, a)
    return dwg


def _repack_candidates(a, scale, page):
    """The (scale, page_w, page_h, tb_w) candidates the repack may choose from,
    mirroring :func:`choose_scale`: a user-fixed scale and/or page is honoured;
    otherwise the auto ladder (smallest legible sheet first) is searched."""
    if scale is not None and page is not None:
        pw, ph, tb = _parse_page(page)
        return [(float(scale), pw, ph, tb)]
    if page is not None:
        pw, ph, tb = _parse_page(page)
        return [(s, pw, ph, tb) for s in _SCALES]
    if scale is not None:
        return [(float(scale), pw, ph, _tb_width(pw)) for pw, ph in _PAGE_SIZES.values()]
    # Auto repack uses the same composed-footprint fitness as choose_scale (#519),
    # so it no longer needs a pass-1 floor to compensate for divergent fit models.
    return list(_LADDER)


def _repack(
    a, dwg, out, assembly, detail_view, scale=None, page=None, model=None, decorations=None
):
    """Measure the laid-out drawing's *real* per-view annotation footprints and,
    when a view collides across views, pack the blocks disjoint — escalating the
    sheet/scale until the packed layout fits — then re-assemble (#121, ADR 0004 —
    "lay out, don't predict"; the (scale, page) choice is the outer search whose
    fitness is *do the packed disjoint blocks fit*).

    Returns ``(a2, dwg2)`` for the repacked drawing, or ``None`` when pass 1 has
    no cross-view overlap AND nothing overflows the drawable (the common case — a
    clean sheet is left exactly as placed, so well-estimated parts stay
    byte-identical) or when the repack would change nothing (same sheet/scale and
    no view actually moves).
    """
    if (
        _cross_view_overlaps(dwg, a) == 0
        and _annotation_view_overlaps(dwg, a) == 0
        and not _annotations_out_of_bounds(dwg, a)
    ):
        return None
    blocks = _measure_blocks(dwg, a)

    def _geom(cand):
        s, pw, ph, tb = cand
        return _layout_geometry(
            a.x_size,
            a.y_size,
            a.z_size,
            s,
            pw,
            ph,
            tb,
            a.layout_strips,
            a.layout_n_steps,
            blocks=blocks,
            section=a.layout_section,
            table_sizes=a.layout_table_sizes,
            warn_no_iso=False,
        )

    candidates = _repack_candidates(a, scale, page)
    auto_search = scale is None and page is None

    def _candidate_fits(g):
        return g.auto_fits if auto_search else g.fits

    fit = next(((c, gg) for c in candidates if _candidate_fits(gg := _geom(c))), None)
    if fit is not None:
        chosen, g = fit
    else:
        chosen = None
        # No standard ISO 5455 scale fits the measured layout. When the scale is NOT
        # pinned, bisect for the largest scale that fits on the largest candidate sheet
        # (the packed layout is monotone in scale) so we never keep an overflowing sheet
        # (#350) — mirroring choose_scale's backstop, including its two guards: honour a
        # pinned scale (may not reduce it), and fall back if no positive scale fits.
        if scale is None:
            _, pw0, ph0, tb0 = candidates[-1]
            lo, hi = 0.0, candidates[-1][0]
            for _ in range(60):
                mid = (lo + hi) / 2.0
                if _candidate_fits(_geom((mid, pw0, ph0, tb0))):
                    lo = mid
                else:
                    hi = mid
            if lo > 0.0:
                chosen = (lo, pw0, ph0, tb0)
                g = _geom(chosen)
                _log.warning(
                    "measure-repack: no standard sheet fits the measured layout; "
                    "using computed %s",
                    format_drawing_scale(lo),
                )
        if chosen is None:
            # Pinned scale, or no positive scale fits the measured blocks on this page:
            # keep the largest candidate and let lint report the overflow (as before).
            chosen = candidates[-1]
            g = _geom(chosen)
            _log.warning(
                "measure-repack: no sheet/scale fits the measured layout; using %s", chosen
            )
    s, pw, ph, tb = chosen
    moved = max(
        abs(g.FV_X - a.FV_X),
        abs(g.FV_Y - a.FV_Y),
        abs(g.PV_X - a.PV_X),
        abs(g.PV_Y - a.PV_Y),
        abs(g.SV_X - a.SV_X),
        abs(g.SV_Y - a.SV_Y),
    )
    if s == a.SCALE and pw == a.PAGE_W and ph == a.PAGE_H and moved < _REPACK_TOL:
        return None
    fv_zones, pv_zones, sv_zones = _build_zones(g, a.margin, ph)
    a2 = replace(
        a,
        SCALE=s,
        PAGE_W=pw,
        PAGE_H=ph,
        TB_W=tb,
        x_offset=g.x_offset,
        FV_X=g.FV_X,
        FV_Y=g.FV_Y,
        PV_X=g.PV_X,
        PV_Y=g.PV_Y,
        SV_X=g.SV_X,
        SV_Y=g.SV_Y,
        fv_hw=g.fv_hw,
        fv_hh=g.fv_hh,
        pv_hh=g.pv_hh,
        sv_hw=g.sv_hw,
        sv_right=g.sv_right,
        iso_right_limit=g.iso_right,
        ISO_X=g.ISO_X,
        ISO_Y=g.ISO_Y,
        iso_left_limit=g.iso_left,
        iso_bottom_limit=g.iso_bottom,
        iso_top_limit=g.iso_top,
        proj=_Projector(
            fv_x=g.FV_X,
            fv_y=g.FV_Y,
            sv_x=g.SV_X,
            sv_y=g.SV_Y,
            pv_x=g.PV_X,
            pv_y=g.PV_Y,
            cx=a.cx,
            cy=a.cy,
            cz=a.cz,
            scale=s,
        ),
        fv_zones=fv_zones,
        pv_zones=pv_zones,
        sv_zones=sv_zones,
    )
    dwg2 = _assemble(
        a2, out, assembly, detail_view, auto_dims=True, model=model, decorations=decorations
    )
    return a2, dwg2


def build_drawing(
    step_file: str | Path | Shape,
    out: str | None = None,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str = "ISO 2768-m",
    drawn_by: str = "",
    scale: float | None = None,
    page: str | tuple | None = None,
    auto_dims: bool = True,
    detail_view: bool = False,
    pmi: Literal["off", "report", "annotate"] = "off",
    repair: bool = True,
    assembly: bool | None = None,
    model: Sequence[Feature] | PartModel | None = None,
    decorations: dict | None = None,
) -> Drawing:
    """Build a customisable 4-view :class:`Drawing` without exporting it.

    Same arguments as :func:`make_drawing`, but returns the live :class:`Drawing`
    so you can add or remove annotations and add section/auxiliary views before
    calling :meth:`Drawing.export`. ``make_drawing(...)`` is exactly
    ``build_drawing(...).export()``.

    Args:
        auto_dims: pass ``False`` to skip the automatic dimensions,
            centrelines, and leaders (#74) — the automatic set assumes a
            turned part and is wrong for prismatic geometry. Views, scale,
            page, and title block are still produced; add your own
            annotations before export. (Annotations added by the default can
            also be removed wholesale with :meth:`Drawing.clear_annotations`.)
        repair: run the bounded lint→repair loop (:meth:`Drawing.repair`) after
            placement to fix mechanically-clear violations (a dim on the wrong
            side, two overlapping labels). Default ``True``; a no-op on a clean
            sheet. Pass ``False`` to inspect the raw greedy placement (#30).
        assembly: severity of the feature-coverage lint for a general-arrangement
            drawing. ``None`` (default) auto-detects — a multi-solid part is an
            assembly, whose per-part bores are reported at ``info`` rather than
            ``warning`` (a GA omits them by design). Force with ``True``/``False``
            (#69).
        model: a caller-supplied IR (ADR 0011) — a :class:`PartModel`, or a sequence
            of :class:`Feature`\\ s (declared with :func:`draftwright.model.hole`,
            ``boss``, ``step``, … from the objects you built). When given, **feature
            detection is skipped** and the auto-pass dimensions exactly the declared
            features; ``None`` (default) detects normally. Detection and declaration are
            two producers of the same IR — everything downstream is untouched. (Notes:
            sheet scale/zone estimation and the coverage lint still detect independently,
            so a *partial* declaration will flag the undeclared geometry. A declared
            hole/pattern now renders at its declared position even where detection missed
            it (#448); the one remaining detection-dependent bit is the off-axis
            side-drilled hole *location* dim, which needs recogniser-Hole geometry a
            declared feature doesn't carry. See ADR 0011.)

    Returns:
        A :class:`Drawing` with the standard front/plan/side/iso views projected
        and the automatic dimensions + title block already added.
    """
    stem = "drawing" if isinstance(step_file, Shape) else Path(step_file).stem
    out = out or stem
    for _ext in (".svg", ".dxf"):
        if out.endswith(_ext):
            out = out[: -len(_ext)]
            break
    title = title or stem.replace("_", " ").upper()

    a = _analyse(
        step_file,
        title,
        number,
        tolerance,
        drawn_by,
        out,
        scale=scale,
        page=page,
        pmi=pmi,
        model=model,
    )

    # Pass 1: place + annotate from the estimated layout, then measure the real
    # per-view footprints and re-pack the blocks disjoint if a view actually
    # moves (#121, ADR 0004 — "lay out, don't predict").  Non-ballooned parts
    # measure ≈ estimate, so they skip pass 2 and stand byte-identical.
    dwg = _assemble(a, out, assembly, detail_view, auto_dims, model=model, decorations=decorations)
    if auto_dims:
        repacked = _repack(
            a,
            dwg,
            out,
            assembly,
            detail_view,
            scale=scale,
            page=page,
            model=model,
            decorations=decorations,
        )
        if repacked is not None:
            a, dwg = repacked
    if repair:
        # Close the loop on the greedy placement: re-place dims behind any
        # mechanically-clear violations (overlap, wrong-side) and re-lint (#30).
        # A no-op on a clean sheet, so default-on costs nothing when there is
        # nothing to fix.
        dwg.repair()
    return dwg


# ---------------------------------------------------------------------------
# Direct export (SVG + DXF)
# ---------------------------------------------------------------------------


def make_drawing(
    step_file: str | Path | Shape,
    out: str | None = None,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str = "ISO 2768-m",
    drawn_by: str = "",
    scale: float | None = None,
    page: str | tuple | None = None,
    auto_dims: bool = True,
    detail_view: bool = False,
    pmi: Literal["off", "report", "annotate"] = "off",
    assembly: bool | None = None,
) -> tuple[str, str]:
    """Generate a 4-view technical drawing from a STEP file or build123d object.

    Args:
        step_file: Path to a STEP/STP file, or a build123d ``Shape`` (e.g. a
            ``Part``, ``Solid``, or ``Compound``) to draw directly.
        out: Output path stem (default: input filename stem, or ``"drawing"``
            when a build123d object is passed).
        title: Part title for the title block (default: stem uppercased).
        number: Drawing number (e.g. ``"DWG-042"``).
        tolerance: General tolerance string (e.g. ``"ISO 2768-m"``).
        drawn_by: Designer name for the title block.
        scale: Drawing-scale override (e.g. ``5`` for 5:1, ``0.5`` for 1:2).
            Default: chosen automatically by :func:`choose_scale`.
        page: Page-size override — an ISO name (``"A3"``), ``"WIDTHxHEIGHT"``
            in mm, or a ``(width, height)`` tuple. Default: chosen
            automatically by :func:`choose_scale`.
        auto_dims: pass ``False`` to skip the automatic dimensions,
            centrelines, and leaders (#74) — views, scale, page, and title
            block only.

    Returns:
        Tuple of ``(svg_path, dxf_path)`` for the generated files.

    This is a thin wrapper: ``make_drawing(...)`` is ``build_drawing(...).export()``.
    To add or remove annotations or add section/auxiliary views before export,
    call :func:`build_drawing` and use the returned :class:`Drawing`.
    """
    svg_path, dxf_path = build_drawing(
        step_file,
        out=out,
        title=title,
        number=number,
        tolerance=tolerance,
        drawn_by=drawn_by,
        scale=scale,
        page=page,
        auto_dims=auto_dims,
        detail_view=detail_view,
        pmi=pmi,
        assembly=assembly,
    ).export()
    assert svg_path is not None and dxf_path is not None  # export() writes both by default
    return svg_path, dxf_path


# ---------------------------------------------------------------------------
# Script generation (Cog-enabled .py output)
# ---------------------------------------------------------------------------


def _fmt_pt(p) -> str:
    """A compact ``x, y, z`` for a model-space point (integers stay integers).

    Near-integer coords round to an ``int`` (not ``f"{c:.0f}"``) so a symmetric part's
    tiny-negative bbox-centre float (``-1e-16``) prints ``0``, never ``-0`` (#416 review).
    """
    return ", ".join(f"{round(c)}" if abs(c - round(c)) < 1e-6 else f"{c:.1f}" for c in p)


# Feature kinds with no intent verb yet — emitted as a flagged comment, never a broken
# call. The build_drawing(auto_dims=True) pointer recovers the full auto drawing (#424).
_GAP_KINDS = {
    "rotational": "auto-pass draws the overall OD + centrelines + concentric bore leaders; "
    "out of scope for the intent verbs (#419)",
    "pmi": "pre-authored PMI, rendered by --pmi annotate; an add verb is deferred to #422",
}


def _feature_listing(a: Analysis) -> str:
    """Emit the detected features as **runnable intent-verb calls** (#400 Ph2 / #426) that
    reconstruct the drawing on the detect-only build (``auto_dims=False``) above.

    The verb calls run inside a ``with dwg.deferred():`` block: each verb **records** its
    intent, and on block exit ``finalize()`` drains them through the auto-pass's own batch
    solvers (#426 Phase 5) — so the reconstruction reaches auto-pass placement quality
    (crossing-free locations, the priority-drop callout solve, the turned diameter /
    step-length set-solves) rather than greedy live placement.

    Each feature is redrawn against the detected model (``dwg.model().features[i]``, the
    ADR-0008 IR): holes/patterns → ``callout`` + ``locate`` + ``furniture``; steps/bosses →
    ``callout`` (ø); steps/envelopes/slots → ``dimension(...)`` per linear param; prismatic
    step levels → one ``dimension(..., role="step_height")`` intent that regenerates the
    correlated height ladder. A section A–A is recorded when a counterbored/spotfaced/blind
    Z-hole warrants one (finalize renders it last). Feature kinds with no verb yet
    (rotational, pmi) are emitted as flagged comments naming the gap (#424) — never silently
    dropped. Commenting any line drops exactly that intent (nothing is auto-drawn, so there
    is no double-dimension risk). Pure function of *a*.
    """
    model = build_model(a)
    feats = getattr(model, "features", [])
    if not feats:
        return (
            "# ── Reconstruct the drawing (#400 Ph2 / #426) ─────────────────────────────────\n"
            "# No dimensionable features detected.\n"
        )
    # The recorded verb calls + inline gap comments — go inside `with dwg.deferred():`.
    body: list[str] = []
    for i, feat in enumerate(feats):
        kind = feat.kind
        body.append(f"# features[{i}]  {kind} @ ({_fmt_pt(feat.frame.origin)})")
        if kind in _GAP_KINDS:
            body.append(f"#     {kind} — {_GAP_KINDS[kind]}. auto_dims=True to keep it.")
            continue
        body.append(f"f = dwg.model().features[{i}]")
        if kind in ("hole", "pattern"):
            body.append("dwg.callout(f)")
            if feat.frame.axis == "z":
                body.append("dwg.locate(f)")
            else:
                # locate() is Z-axis only (it rejects side-drilled bores by contract, #133);
                # an off-axis bore's position is auto-pass-only. Flag it like a gap kind (#424).
                body.append(
                    f"#     locate() is Z-axis only — this {feat.frame.axis}-drilled bore's "
                    "position is auto-pass-only (#133). auto_dims=True to keep it."
                )
            body.append("dwg.furniture(f)")
        elif kind in ("step", "boss"):
            if feat.frame.axis in ("x", "z"):
                body.append("dwg.callout(f)")
            else:
                # callout() places X/Z-turned diameters only; a Y-turned step/boss is
                # auto-pass-only (its diameter is not placeable, and the auto-pass skips it too).
                body.append(
                    f"#     callout() places X/Z-turned diameters only — this "
                    f"{feat.frame.axis}-turned step/boss is auto-pass-only. auto_dims=True to keep it."
                )
        elif kind == "step_level":
            body.append(
                'dwg.dimension(f, "length", role="step_height")   # prismatic height ladder'
            )
            continue
        for p in feat.parameters():
            if p.span is not None or kind == "slot":  # a linear dim dimension() accepts
                body.append(f'dwg.dimension(f, "{p.kind}", role="{p.role}")   # {display(p)}')
    if plan_sections(model, feature_hole_keys(a)) is not None:
        body += [
            "",
            "# Section A–A (part-level; comment to drop the whole section)",
            "dwg.section()",
        ]
    header = [
        "# ── Reconstruct the drawing at intent level (record → finalize, #426) ─────────",
        "# The verbs RECORD intents inside `with dwg.deferred()`; on block exit finalize()",
        "# drains them through the auto-pass's own batch solvers, so the reconstruction",
        "# reaches auto-pass placement quality — not greedy live placement. Comment any",
        "# single line to drop exactly that intent; comment a whole block to stop",
        "# dimensioning that feature. The build above is detect-only, so nothing is drawn",
        "# twice. Kinds with no verb yet are flagged inline — build_drawing(auto_dims=True)",
        "# recovers the full automatic drawing for those.",
        "#",
    ]
    # A part whose every feature is a gap kind (and no section) records nothing — emit the
    # flagged comments flat rather than an empty `with` block (an IndentationError).
    if not any(ln.strip() and not ln.startswith("#") for ln in body):
        return "\n".join(header + body) + "\n"
    indented = ["    " + ln if ln.strip() else ln for ln in body]
    return "\n".join(header + ["with dwg.deferred():"] + indented) + "\n"


def _write_script(a: Analysis, scale: float | None = None, page: str | None = None) -> str:
    """Write an editable script at ``a.out + '.py'`` that calls make_drawing().

    ``scale``/``page`` are the caller's *overrides* (``None`` = auto); ``pmi`` is
    carried from the analysis (``a.pmi_mode``). All three are preserved as config
    fields and threaded into the emitted ``build_drawing(...)`` call so the script
    reproduces the CLI's intent (#388).
    """
    py_path = a.out + ".py"
    py_name = Path(py_path).name

    cog_output = "\n".join(
        [
            f"STEP_FILE = {a.step_file!r}",
            f"TITLE = {a.title!r}",
            f"NUMBER = {a.number!r}",
            f"TOLERANCE = {a.tolerance!r}",
            f"DRAWN_BY = {a.drawn_by!r}",
            f"PMI = {a.pmi_mode!r}",
            f"SCALE = {scale!r}",
            f"PAGE = {page!r}",
        ]
    )

    cog_block = (
        "# [[[cog\n"
        "# ── Config: edit these, then run `cog -r <script>.py` to update ────────────\n"
        f"_STEP_FILE = {a.step_file!r}\n"
        f"_TITLE     = {a.title!r}\n"
        f"_NUMBER    = {a.number!r}\n"
        f"_TOLERANCE = {a.tolerance!r}\n"
        f"_DRAWN_BY  = {a.drawn_by!r}\n"
        f"_PMI       = {a.pmi_mode!r}   # 'off' | 'report' | 'annotate'\n"
        f"_SCALE     = {scale!r}   # None = auto; e.g. 5 for 5:1, 0.5 for 1:2\n"
        f"_PAGE      = {page!r}   # None = auto; e.g. 'A3' or (297, 210)\n"
        "try:\n"
        "    cog  # NameError → not under cog\n"
        "    for _k, _v in [\n"
        "        ('STEP_FILE', repr(_STEP_FILE)), ('TITLE', repr(_TITLE)),\n"
        "        ('NUMBER', repr(_NUMBER)), ('TOLERANCE', repr(_TOLERANCE)),\n"
        "        ('DRAWN_BY', repr(_DRAWN_BY)), ('PMI', repr(_PMI)),\n"
        "        ('SCALE', repr(_SCALE)), ('PAGE', repr(_PAGE)),\n"
        "    ]:\n"
        "        cog.outl(f'{_k} = {_v}')\n"
        "except NameError:\n"
        "    pass\n"
        "# ]]]\n"
        f"{cog_output}\n"
        "# [[[end]]]"
    )

    _tq = '"""'
    _safe_doc_title = a.title.replace(_tq, "'''")
    _safe_doc_number = a.number.replace(_tq, "'''")
    header = (
        f"#!/usr/bin/env python3\n"
        f'"""\n'
        f"{_safe_doc_title} — Technical drawing ({_safe_doc_number}).\n"
        f"\n"
        f"Auto-generated by make-drawing. Edit freely.\n"
        f"To update metadata: edit _STEP_FILE / _TITLE / etc. in the cog block, then run:\n"
        f"  cog -r {py_name}   (pip install cogapp)\n"
        f"\n"
        f"Run:  uv run python {py_name}\n"
        f'"""\n'
        f"import os as _os\n"
        f"from draftwright import build_drawing\n"
        f"\n"
        f"# Available for lint-suggestion snippets (dwg.lint_summary()); unused otherwise.\n"
        f"from build123d_drafting import Dimension, HoleCallout, Leader  # noqa: F401\n"
        f"\n"
        f"# ── Config (auto-updated by cog) ──────────────────────────────────────────────\n"
    )

    run_section = (
        "\n"
        "# ── Build drawing (detect-only 4-view layout; dimensions added below) ──────────\n"
        "_stem = _os.path.splitext(__file__)[0]\n"
        "dwg = build_drawing(\n"
        "    STEP_FILE,\n"
        "    out=_stem,\n"
        "    title=TITLE,\n"
        "    number=NUMBER,\n"
        "    tolerance=TOLERANCE,\n"
        "    drawn_by=DRAWN_BY,\n"
        "    pmi=PMI,\n"
        "    scale=SCALE,\n"
        "    page=PAGE,\n"
        "    auto_dims=False,   # detect-only — the intent verbs below add every dimension\n"
        ")\n"
        "\n"
        "# ── Customise here — runs BEFORE export, so edits land in the output ───────────\n"
        "# Prefer domain edits (the intent verbs below) over page mechanics (at / Leader);\n"
        "# the engine places annotations automatically — say WHAT, not WHERE.\n"
        "# dwg.model().features     → detected feature IR for domain edits\n"
        "# dwg.dimension(f, param, role=…, pin=True, priority=…)  → feature-backed dimension\n"
        "# dwg.locate(f, pin=True)  → feature-backed location dimension\n"
        "# dwg.annotations()        → {name: type} of every named annotation\n"
        "# dwg.get_annotation(name) → the named annotation object, or None\n"
        "# dwg.remove(name) / dwg.add(obj, name)\n"
        "# dwg.pin(name) / dwg.unpin(name)  → fix a placement so repair never moves it\n"
        "# dwg.lint_summary()       → {passed, score, by_code, issues:[…suggestion]}\n"
        "# dwg.repair()             → auto-fix mechanically-fixable lint (never worsens)\n"
        "# dwg.add_view(name, shape, camera, up, position)  → section / auxiliary view\n"
        "# dwg.items / dwg.views / dwg.at(view,x,y,z) / dwg.view_bounds(view)  → low-level escape\n"
        "# dwg.place_dim(...)       → deprecated raw page-coordinate dimension escape hatch\n"
        "# Example — add a pinned feature-backed linear dimension:\n"
        "#   env = next(f for f in dwg.model().features if f.kind == 'envelope')\n"
        "#   dwg.dimension(env, 'length', role='width', side='below', pin=True)\n"
        "\n" + _feature_listing(a) + "\n"
        "# finalize() (auto-run on the `with` exit above, and again by export) batch-solved\n"
        "# the recorded intents; repair() is now just a peephole net — it never worsens the\n"
        "# sheet (#426 Phase 5).\n"
        "dwg.repair()\n"
        "\n"
        "# ── Export ────────────────────────────────────────────────────────────────────\n"
        "svg_path, dxf_path = dwg.export(_stem)\n"
        # ASCII arrow: a Unicode → crashes the print on a Windows cp1252 console
        # (UnicodeEncodeError) — the generated script must run everywhere.
        'print(f"SVG -> {svg_path}")\n'
        'print(f"DXF -> {dxf_path}")\n'
    )

    content = header + cog_block + run_section
    Path(py_path).write_text(content, encoding="utf-8")
    _log.info("Script → %s", py_path)
    return py_path


def generate_script(
    step_file: str,
    out: str | None = None,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str = "ISO 2768-m",
    drawn_by: str = "",
    pmi: Literal["off", "report", "annotate"] = "off",
    scale: float | None = None,
    page: str | None = None,
) -> str:
    """Generate an editable Cog-enabled drawing script from a STEP file.

    Returns:
        Path to the generated ``.py`` file.
    """
    if isinstance(step_file, Shape):
        raise TypeError(
            "generate_script() requires a STEP file path — the generated script "
            "reloads geometry from disk and cannot embed a live build123d object. "
            "Use make_drawing() directly to draw an in-memory object."
        )
    stem = Path(step_file).stem
    out = out or stem
    for _ext in (".py", ".svg", ".dxf"):
        if out.endswith(_ext):
            out = out[: -len(_ext)]
            break
    title = title or stem.replace("_", " ").upper()
    # scale/page are NOT passed to this analysis: the script embeds them as literal
    # config fields and re-validates them at run time inside build_drawing(). Validating
    # here too would crash generation on an out-of-range value (e.g. --script --scale
    # 0.001 / --page A9) instead of writing the script and deferring — inconsistent with
    # a large unfittable scale, which already defers (review #401).
    a = _analyse(step_file, title, number, tolerance, drawn_by, out, pmi=pmi)
    return _write_script(a, scale=scale, page=page)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli():
    """Compat shim: the CLI moved to the Typer app in ``draftwright.cli`` (#289).

    Kept so ``python -m draftwright.make_drawing`` and existing
    ``from draftwright... import _cli`` imports keep working; the engine entry
    point (``[project.scripts]``) points straight at ``draftwright.cli:app``.
    Imported lazily so a bare ``import draftwright.builder`` does not pull Typer.
    """
    from draftwright.cli import app

    app()


if __name__ == "__main__":
    _cli()
