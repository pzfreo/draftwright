"""Build orchestration (#138 / ADR 0005, P6).

The pipeline driver: `build_drawing` runs analysis -> assemble (project +
annotate + fit) -> measure-and-repack -> returns the `Drawing`; `make_drawing`
wraps it with export. (The editable-script generator moved out: #940 retired the
imperative one and `sheet_emit` owns the surviving declarative emitter.) Imports
`drawing` (the result object), `analysis`, the annotation orchestrator, and the
stage modules -- never make_drawing -- so the graph stays a DAG.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast

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
    _add_projection_symbol,
    _add_sheet_frame,
    _add_title_block,
    _add_zone_grid,
    _iso_bbox,
    _log,
    _parse_page,
    _Projector,
    _tb_width,
)
from draftwright.analysis import _analyse
from draftwright.annotations._common import SolveTrace
from draftwright.annotations.orchestrator import (
    _auto_annotate,
    build_model,
    build_rotational_feature,
)
from draftwright.compose import (
    ViewBlock,
    _attribute_annotations,
    _build_zones,
    _layout_geometry,
    _view_geom,
)
from draftwright.drawing import Drawing
from draftwright.fonts import PLEX_MONO
from draftwright.model import (
    Datum,
    Feature,
    GrooveFeature,
    PartModel,
    StepFeature,
    build_pmi_features,
)
from draftwright.projection import (
    _fit_iso_view,
    _project_iso,
)

# A view centre must move by more than this (mm) for the measure-and-repack
# pass to re-assemble.  Below it, the estimate already matched the measured
# footprint and pass 1 stands (the common, non-ballooned case).
_REPACK_TOL = 0.75
_REPACK_MAX_ITER = 3


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


def _coerce_model(model, part, decorations=None, requested=None, authored=None) -> PartModel:
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
        if decorations or requested or authored is not None:
            out = replace(
                model,
                decorations={**model.decorations, **decorations}
                if decorations
                else model.decorations,
                requested_dimensions=tuple(requested) if requested else model.requested_dimensions,
                # `authored is not None` rather than truthiness: an authored set is never
                # empty (the façade refuses that), but None means "the planner chooses" and
                # must not be confused with "the author chose nothing" (#874).
                authored_dimensions=tuple(authored)
                if authored is not None
                else model.authored_dimensions,
            )
        else:
            out = model
    else:
        features = list(model)
        bbox = part.bounding_box()
        orientation = next((f.frame.axis for f in features if isinstance(f, StepFeature)), None)
        datum = Datum(id="datum_xy", kind="point", at=(bbox.min.X, bbox.min.Y, bbox.min.Z))
        out = PartModel(
            bbox=bbox,
            orientation=orientation,
            features=features,
            datums=[datum],
            decorations=decorations or {},
            requested_dimensions=tuple(requested or ()),
            authored_dimensions=None if authored is None else tuple(authored),
        )
    _check_dimension_sources(out)
    return out


def _check_dimension_sources(model: PartModel) -> None:
    """Refuse a model that names **both** dimension sources (ADR 0016 / #874).

    The mutual exclusion is a property of the MODEL, not of the façade that usually
    builds it: `build_drawing(part, model=…, requested=…, authored=…)` is a public
    entry point (ADR 0011) and could otherwise construct the state `Sheet` refuses.

    Checked against the **effective** model rather than the arguments of any one call,
    because either source can arrive two ways — as a keyword, or already carried by a
    supplied `PartModel` — and an argument-level guard sees only half of each
    combination (#921 review). Validating the merged result covers all four."""
    if model.requested_dimensions and model.authored_dimensions is not None:
        raise ValueError(
            "requested= augments the planner's automatic set and authored= replaces it — a "
            "model cannot have both. Drop the requested= entries into the authored set, or "
            "drop authored= to keep the automatic one."
        )


def detect_part_model(part, *, pmi="off") -> PartModel:
    """The **detected** :class:`PartModel` for *part* — feature recognition + analysis only,
    with no view projection, annotation, repack, repair, or export (ADR 0011 #453). The cheap
    seed path behind :meth:`draftwright.Sheet.from_part`, so pure feature inspection no longer
    pays for a full drawing (nor its layout/rendering failure modes)."""
    a = _analyse(
        part, title="", number="", tolerance="ISO 2768-m", drawn_by="", out="model", pmi=pmi
    )
    # `_analyse` already detected and stored the model, so calling `build_model(a)`
    # unconditionally re-ran every detector `build_part_model` doesn't take by injection —
    # the #602 duplicate-detection bug, fixed in `_assemble` but never here. It went unnoticed
    # because the emitter that had the detect-once test went through `_assemble`; #940 made
    # this the path, and migrating that test found it. Same fallback as `_assemble`: a
    # hand-built Analysis with no stored model still detects.
    # `Analysis.model` is `object | None` (it sits below the IR in the DAG), so the cast is
    # what says the stored value is the same PartModel `build_model` would have rebuilt.
    return cast("PartModel", a.model if a.model is not None else build_model(a))


def _assemble(
    a,
    out,
    assembly,
    detail_view,
    auto_dims,
    model=None,
    decorations=None,
    requested=None,
    authored=None,
    trace=None,
) -> Drawing:
    """Project the 4 views for analysis *a*, run the automatic annotation
    passes, and fit the iso.  This is pass 1 of :func:`build_drawing`; with a
    repacked analysis it is also pass 2 of the measure-and-repack loop (#121).
    *trace* is the opt-in #736 solve-trace recorder (attached to the drawing's
    build state so the annotate + finalize paths thread it), or ``None``."""
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
    # Detect the IR here — before the auto_dims gate — so dwg.model() and feature edits
    # work even in manual mode (#398). _auto_annotate reads this attached model rather
    # than rebuilding. On a repack this runs again on the pass-2 drawing (freshness).
    # Detected path: reuse the model _analyse already built for sizing (#584 WP1 A) —
    # detectors run once per build (ADR 0008 Amdt 5, #602). build_model(a) remains the
    # fallback for a manually-constructed Analysis with no stored model.
    if model is None and (requested or authored is not None):
        # Both verbs name a DECLARED feature object (ADR 0016 / #872, #874), and detection
        # builds its own. Silently dropping them would leave a caller's add_dimension() /
        # dimension() with no effect and no diagnostic — the failure mode this project
        # treats as worse than a visible error (#630/#631/#632). An authored set is the
        # worse of the two to drop: the build would quietly revert to the automatic
        # dimensions the author was replacing (#921 review).
        verb = "requested=" if requested else "authored="
        raise ValueError(
            f"{verb} names declared features, so it needs model= too; a detected "
            "model builds its own feature objects that no request can target"
        )
    pm = (
        _coerce_model(model, a.part, decorations, requested, authored)
        if model is not None
        else (a.model if a.model is not None else build_model(a))
    )
    if model is not None:
        # A declared model skips detection, so a turned shaft carries no RotationalFeature —
        # and that feature is the sole driver of the turned-axis centrelines + the OD dimension
        # (rot furniture). Synthesise it from the (unconditional) analysis so a declared /
        # emitted-script turned part reproduces the detected drawing (#472). Gated on the
        # caller not having declared one, so an explicit choice wins.
        if not any(f.kind == "rotational" for f in pm.features):
            rot = build_rotational_feature(a)
            if rot is not None:
                pm = replace(pm, features=[*pm.features, rot])
        # A z step declares a segment of a z-turned profile. `.step()` on a BOSS — an external
        # cylinder on a prismatic part — is a misuse of the verb, and the symptom is that the
        # declared steps leave the bulk of the part unspanned (#631). This is a verb-misuse
        # diagnostic, not a guarantee that the height is dimensioned: whether the approved
        # measurements actually reach the page is settled downstream and reported by lint
        # (`axial_length_missing`). Guard on the tiling condition rather than a classifier
        # proxy (is_rotational / prof both have blind spots).
        z_steps = [f for f in pm.features if isinstance(f, StepFeature) and f.frame.axis == "z"]
        if pm.orientation == "z" and z_steps:
            tol = 1e-3 * max(a.z_size, 1.0)  # small absolute float epsilon, floored
            # Tiling means the segments run end to end — a single reach to each end isn't
            # enough, since an interior gap is a stretch of part no declared step describes.
            # Walk the spans low→high, extending coverage.
            # A GROOVE counts as one of those segments (#953): it is machined INTO the turned
            # profile, so a shaft that has one is still a turned profile, which is the only
            # thing this guard is asking. Without it the emitted script for any grooved shaft
            # RAISED — the detector produces exactly this model (step, groove, step) and the
            # direct build draws it, so the engine rejected its own detector's output the
            # moment that output was declared rather than detected. Grooves are the only
            # detected feature that splits the chain: a chamfered or filleted shoulder leaves
            # the two step spans meeting (checked), and a bore or cross-hole carves no axial
            # interval. #631's own repro still raises.
            # What this does NOT do is verify the declaration against the solid, and it never
            # did: `.step(diameter=20, length=48, at=…)` fabricating one full-extent segment
            # already defeats it on its own, groove or no groove (checked). Declared spans are
            # the author's statement of intent, which ADR 0011 takes at face value rather than
            # re-deriving, so this catches the honest mistake #631 reported — declaring the
            # boss you can see and getting a worse drawing — not a fabricated coverage claim.
            # Hardening it into a real verb-domain check is #956.
            # NOT claimed here: that the resulting drawing dimensions the height. On this very
            # fixture the step chain drops at placement (crowded shoulders) and the height,
            # suppressed at compile time on the premise the chain conveys it, is then conveyed
            # by nothing — identically on the detected path. That is a real defect, tracked
            # separately (#955); raising here would not fix it, only hide it from one of the
            # two front doors.
            spans = sorted(
                [(min(p0[2], p1[2]), max(p0[2], p1[2])) for f in z_steps for (p0, p1) in [f.span]]
                + [
                    (f.frame.origin[2] - f.width / 2, f.frame.origin[2] + f.width / 2)
                    for f in pm.features
                    if isinstance(f, GrooveFeature) and f.frame.axis == "z"
                ]
            )
            covered = a.bb.min.Z
            for lo, hi in spans:
                if lo <= covered + tol:
                    covered = max(covered, hi)
            if spans[0][0] > a.bb.min.Z + tol or covered < a.bb.max.Z - tol:
                raise ValueError(
                    "step() declares a segment of a turned profile, but the declared steps "
                    "don't span this part's full height — it is not a turned (rotational) "
                    "body. For a boss (an external cylinder on a prismatic part) use .boss() "
                    "— it renders its own ø and height."
                )
        # PMI (STEP AP242) is likewise detection-sourced, so a declared / emitted-script model
        # carries none. When PMI annotation is on, synthesise the same imported drafting
        # annotations detection would (render_pmi reads them off the model, gated on a.pmi_mode)
        # so a re-run reproduces the PMI dims (#472). Gated on the caller not having declared
        # imported authored annotations, so an explicit set wins.
        if a.pmi_mode == "annotate" and not any(
            f.kind in ("authored_dimension", "pmi") for f in pm.features
        ):
            pmi_feats = build_pmi_features(a.pmi, a.part.bounding_box())
            if pmi_feats:
                pm = replace(pm, features=[*pm.features, *pmi_feats])
    # ADR 0005 §2 (#639): the ONE build-context attachment — analysis + finished model
    # in a single typed BuildState; the compat properties on Drawing read through it.
    dwg._build.analysis = a
    dwg._build.part_model = pm
    # Persist the caller's detail-view opt-in: on the auto_dims=False path the flag
    # reaches no pass here, but the finalize drain gates the prismatic detail
    # request on it exactly as the auto pass does (#661).
    dwg._build.detail_view = detail_view
    # The opt-in #736 solve-trace recorder rides BuildState like the rest of the build context
    # (or None when tracing is off) — filled here at the single construction site, not poked onto
    # a live Drawing through a named method (#830: the engine constructs, never mutates).
    dwg._build.trace = trace
    dwg._model_declared = model is not None  # ADR 0011 #448: gate model-driven hole render

    part_s = a.part.scale(a.SCALE)
    dwg._add_view(
        "front", part_s, (cxs, cys - dist, czs), (0, 0, 1), (a.FV_X, a.FV_Y), scaled=True
    )
    dwg._add_view("plan", part_s, (cxs, cys, czs + dist), (0, 1, 0), (a.PV_X, a.PV_Y), scaled=True)
    dwg._add_view("side", part_s, (cxs + dist, cys, czs), (0, 0, 1), (a.SV_X, a.SV_Y), scaled=True)
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
        # Fit + label the iso as the auto path does (annotate defaults True): the NTS
        # note is sheet furniture — like the title block below — that states the iso is
        # not to scale. Suppressing it here silently diverged the emitted-script drawing
        # (auto_dims=False) from the direct CLI, which always labels it (script↔CLI parity).
        _fit_iso_view(dwg, a)
        _add_title_block(dwg, a)
        if a.frame:  # sheet border, drawn last (#767) — auto path adds it via the orchestrator
            _add_sheet_frame(dwg, a)
        if a.zones:  # zone-grid ruler (#768), on the frame
            _add_zone_grid(dwg, a)
        if a.projection:  # projection-method glyph (#769) — auto path adds it via the orchestrator
            _add_projection_symbol(dwg, a)
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


def _needs_repack(dwg, a) -> bool:
    """True when the measured drawing still needs a compose-then-pack pass."""
    return (
        _cross_view_overlaps(dwg, a) != 0
        or _annotation_view_overlaps(dwg, a) != 0
        or _annotations_out_of_bounds(dwg, a)
    )


def _repack(
    a,
    dwg,
    out,
    assembly,
    detail_view,
    scale=None,
    page=None,
    model=None,
    decorations=None,
    requested=None,
    authored=None,
    trace=None,
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
    if not _needs_repack(dwg, a):
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
            margin=a.margin,
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
        a2,
        out,
        assembly,
        detail_view,
        auto_dims=True,
        model=model,
        decorations=decorations,
        requested=requested,
        authored=authored,
        trace=trace,
    )
    return a2, dwg2


def _repack_to_fixed_point(
    a,
    dwg,
    out,
    assembly,
    detail_view,
    scale=None,
    page=None,
    model=None,
    decorations=None,
    requested=None,
    authored=None,
    trace=None,
):
    """Iterate measure→repack→assemble until stable or bounded (#302)."""
    cur_a, cur_dwg = a, dwg
    for i in range(_REPACK_MAX_ITER):
        repacked = _repack(
            cur_a,
            cur_dwg,
            out,
            assembly,
            detail_view,
            scale=scale,
            page=page,
            model=model,
            decorations=decorations,
            requested=requested,
            authored=authored,
            trace=trace,
        )
        if repacked is None:
            if _needs_repack(cur_dwg, cur_a):
                _log.warning(
                    "measure-repack: stalled after %d iteration(s) with residual layout triggers",
                    i,
                )
            return (cur_a, cur_dwg) if i else None
        cur_a, cur_dwg = repacked

    if _needs_repack(cur_dwg, cur_a):
        _log.warning(
            "measure-repack: reached iteration limit (%d) with residual layout triggers",
            _REPACK_MAX_ITER,
        )
    return cur_a, cur_dwg


def _resolve_trace(trace, out) -> SolveTrace | None:
    """Resolve :func:`build_drawing`'s ``trace`` option to a :class:`SolveTrace`
    recorder, or ``None`` (off — the default). ``None`` consults the
    ``DRAFTWRIGHT_TRACE`` env var; ``False`` forces off; ``True`` writes
    ``<out>.trace.json`` beside the drawing; a path-or-directory writes there."""
    if trace is False:
        return None
    if trace is None:
        env = os.environ.get("DRAFTWRIGHT_TRACE", "")
        if not env:
            return None
        trace = env
    if trace is True:
        path = Path(f"{out}.trace.json")
    else:
        path = Path(trace)
        if path.is_dir():
            path = path / f"{Path(out).name}.trace.json"
    return SolveTrace(path)


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
    requested: tuple | None = None,
    authored: tuple | None = None,
    trace: str | Path | bool | None = None,
    material: str = "",
    date: str = "",
    revision: str = "A",
    company: str = "",
    frame: bool = False,
    projection: str | None = None,
    zones: bool = False,
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
            page, and sheet furniture (title block, and the "ISO VIEW (NTS)"
            note when the iso is rescaled off sheet scale) are still produced;
            add your own annotations before export. (Annotations added by the default can
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
        trace: the opt-in **solve-trace / explain mode** (#736): record every strip
            placement decision as ONE JSON file per build (schema ``version`` 2),
            with two record types. ``solves`` — the corridor solves: the candidate
            set, the obstacles that carved the strip (with owning annotation names),
            the free segments, and each candidate's outcome (placed/dropped-with-
            reason/deduped/promoted). ``pass_events`` — everything placed outside a
            corridor solve: the standalone strip passes plus the *immediate* placers
            (the post-drain machined-feature leader callouts and the turned
            diameter/step-length set-solves), each with per-item outcomes. The
            ``jq`` contract — corridor dims vs everything else::

                jq '.solves[].outcomes[] | select(.name == "dim_height")' t.trace.json
                jq '.pass_events[] | select(.label == "pocket_callouts") | .items[]' t.trace.json

            ``True`` writes ``<out>.trace.json`` beside the drawing; a path writes
            there (a directory gets ``<stem>.trace.json`` inside it). Default
            ``None`` consults the ``DRAFTWRIGHT_TRACE`` env var (same
            path-or-directory semantics); ``False`` forces it off. **Zero output
            change**: tracing never alters a placement decision, and off (the
            default) costs nothing. Recording-only: an unwritable trace path logs a
            warning and never aborts the build/export.

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
    tracer = _resolve_trace(trace, out)

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
        decorations=decorations,
        material=material,
        date=date,
        revision=revision,
        company=company,
        frame=frame,
        projection=projection,
        zones=zones,
    )

    # Pass 1: place + annotate from the estimated layout, then measure the real
    # per-view footprints and re-pack the blocks disjoint if a view actually
    # moves (#121, ADR 0004 — "lay out, don't predict").  Non-ballooned parts
    # measure ≈ estimate, so they skip pass 2 and stand byte-identical.
    dwg = _assemble(
        a,
        out,
        assembly,
        detail_view,
        auto_dims,
        model=model,
        decorations=decorations,
        requested=requested,
        authored=authored,
        trace=tracer,
    )
    if auto_dims:
        repacked = _repack_to_fixed_point(
            a,
            dwg,
            out,
            assembly,
            detail_view,
            scale=scale,
            page=page,
            model=model,
            decorations=decorations,
            requested=requested,
            authored=authored,
            trace=tracer,
        )
        if repacked is not None:
            a, dwg = repacked
    if repair:
        # Close the loop on the greedy placement: re-place dims behind any
        # mechanically-clear violations (overlap, wrong-side) and re-lint (#30).
        # A no-op on a clean sheet, so default-on costs nothing when there is
        # nothing to fix.
        dwg.repair()
    if tracer is not None:  # one JSON per build; Drawing.finalize() re-writes it (#736)
        tracer.write()
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
    material: str = "",
    date: str = "",
    revision: str = "A",
    company: str = "",
    frame: bool = False,
    projection: str | None = None,
    zones: bool = False,
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
        material=material,
        date=date,
        revision=revision,
        company=company,
        frame=frame,
        projection=projection,
        zones=zones,
    ).export()
    assert svg_path is not None and dxf_path is not None  # export() writes both by default
    return svg_path, dxf_path
