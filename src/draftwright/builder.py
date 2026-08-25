"""Build orchestration (#138 / ADR 0005, P6).

The pipeline driver: `build_drawing` runs analysis -> assemble (project +
annotate + fit) -> measure-and-repack -> returns the `Drawing`; `make_drawing`
wraps it with export. (The editable-script generator moved out: #940 retired the
imperative one and `sheet_emit` owns the surviving declarative emitter.) Imports
`drawing` (the result object), `analysis`, the annotation orchestrator, and the
stage modules -- never make_drawing -- so the graph stays a DAG.
"""

from __future__ import annotations

import collections
import json
import os
import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Literal, cast

from build123d import (
    Shape,
)
from build123d_drafting.helpers import (
    draft_preset,
    format_drawing_scale,
)
from OCP.Standard import Standard_Failure

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
from draftwright._warnings import ScaleCompletenessWarning
from draftwright.analysis import Analysis, _analyse, _apply_principal_view_pins
from draftwright.annotations._common import (
    SolveTrace,
    annotation_ink_obstacles,
    place_iso_nts_note,
)
from draftwright.annotations.gears import render_gear_tables
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
from draftwright.drawing import Drawing, feature_key
from draftwright.fonts import PLEX_MONO
from draftwright.linting import LintIssue
from draftwright.linting.coverage import lint_axial_coverage
from draftwright.model import (
    Datum,
    Feature,
    GrooveFeature,
    PartModel,
    StepFeature,
    build_pmi_features,
)
from draftwright.projection import (
    _bbox_within,
    _fit_iso_view,
    _project_iso,
)
from draftwright.view_plan import ARRANGEMENTS, ViewConstraints, resolve_from_analysis

# A view centre must move by more than this (mm) for the measure-and-repack
# pass to re-assemble.  Below it, the estimate already matched the measured
# footprint and pass 1 stands (the common, non-ballooned case).
_REPACK_TOL = 0.75
_REPACK_MAX_ITER = 3


def _validate_authored_view_layout(dwg: Drawing, constraints) -> None:
    """Apply the hard, non-relaxing half of ADR 0018's authored layout contract.

    Current arrangements remain the planner's candidates; this validator accepts one only
    when it satisfies every relation/pin.  A constraint that would require a candidate the
    engine cannot yet generate is therefore an explicit infeasibility, never inert metadata.
    """

    if not isinstance(constraints, ViewConstraints):
        return

    def bounds(name: str):
        placement = dwg.view_plan.placements.get(name)
        if placement is not None:
            return placement.bounds
        if name in dwg.views:
            return dwg.view_bounds(name)
        raise ValueError(f"authored layout names absent view {name!r}")

    for relation in constraints.relations:
        sb = bounds(relation.subject)
        rb = bounds(relation.reference)
        gap = relation.gap or 0.0
        checks = {
            "left_of": sb[2] + gap <= rb[0] + 1e-6,
            "right_of": sb[0] + 1e-6 >= rb[2] + gap,
            "above": sb[1] + 1e-6 >= rb[3] + gap,
            "below": sb[3] + gap <= rb[1] + 1e-6,
            "align_x": abs((sb[0] + sb[2]) - (rb[0] + rb[2])) <= 1e-6,
            "align_y": abs((sb[1] + sb[3]) - (rb[1] + rb[3])) <= 1e-6,
        }
        if not checks[relation.relation]:
            where = f" at {relation.source}" if relation.source is not None else ""
            raise ValueError(
                f"authored view constraint{where} is infeasible: {relation.subject!r} "
                f"must be {relation.relation} {relation.reference!r}"
                + (f" with gap {gap:g} mm" if relation.gap is not None else "")
            )

    for pin in constraints.pins:
        if pin.view not in dwg.views:
            raise ValueError(f"authored view pin names absent view {pin.view!r}")
        actual = dwg.at(pin.view, 0.0, 0.0, 0.0)
        if max(abs(actual[i] - pin.at[i]) for i in range(2)) > 0.05:
            where = f" at {pin.source}" if pin.source is not None else ""
            raise ValueError(
                f"authored whole-view pin{where} is infeasible: {pin.view!r} projection "
                f"origin resolved to ({actual[0]:.3f}, {actual[1]:.3f}) mm, not "
                f"({pin.at[0]:.3f}, {pin.at[1]:.3f}) mm; the pin was not moved or relaxed"
            )


def _settle_iso_view(dwg: Drawing, a: Analysis, *, obstacles=()):
    """Finish the iso without relaxing an authored per-view scale."""

    if a.planned_iso_scale is None:
        return _fit_iso_view(dwg, a, obstacles=obstacles)

    bb = _iso_bbox(dwg)
    region = (
        a.iso_left_limit,
        a.iso_bottom_limit,
        a.iso_right_limit,
        a.iso_top_limit,
    )
    if not _bbox_within(bb, region):
        source = None
        constraints = a.view_constraints
        if isinstance(constraints, ViewConstraints):
            for item in (*constraints.principals, *constraints.added_principals):
                if item.spec.name == "iso" and item.spec.scale_factor is not None:
                    source = item.source
                    break
        where = f" at {source}" if source is not None else ""
        raise ValueError(
            f"authored iso scale{where} is infeasible in its composed view zone; "
            "the requested scale was not reduced"
        )
    return bb


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
    shape=None,
    critique_recognition=None,
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
        if (
            a.pmi_mode == "annotate"
            and not any(
                f.kind in ("authored_dimension", "pmi")
                or (f.kind == "control_frame" and bool(getattr(f, "source_id", "")))
                for f in pm.features
            )
            and not any(
                getattr(value, "source", "") == "ap242_pmi" and getattr(value, "source_ids", ())
                for value in pm.decorations.values()
            )
        ):
            pmi_feats = build_pmi_features(a.pmi, a.part.bounding_box())
            if pmi_feats:
                from draftwright.model.pmi_lowering import lower_ap242_dimensions

                pm = lower_ap242_dimensions(replace(pm, features=[*pm.features, *pmi_feats]))
    # ADR 0005 §2 (#639): the ONE build-context attachment — analysis + finished model
    # in a single typed BuildState; the compat properties on Drawing read through it.
    dwg._build.analysis = a
    dwg._build.recognition = a.recognition if a.recognition is not None else critique_recognition
    dwg._build.part_model = pm
    # Persist the caller's detail-view setting: on the auto_dims=False path the flag
    # reaches no pass here, but the finalize drain gates the prismatic detail
    # request on it exactly as the auto pass does (#661).
    dwg._build.detail_view = detail_view
    # The opt-in #736 solve-trace recorder rides BuildState like the rest of the build context
    # (or None when tracing is off) — filled here at the single construction site, not poked onto
    # a live Drawing through a named method (#830: the engine constructs, never mutates).
    dwg._build.trace = trace
    dwg._model_declared = model is not None  # ADR 0011 #448: gate model-driven hole render

    # The solid this assembly projects. ADR 0004 wants the real geometry built ONCE, but the
    # measure-and-repack loop assembles up to three times, so today it is projected up to three
    # times — the root cause of #1135's hour-long build. This parameter is the seam where the
    # loop's intermediate assemblies will pass a cheap stand-in and only the final one the real
    # solid (#1137). Every caller currently takes the default, so nothing has changed yet.
    part_s = (a.part if shape is None else shape).scale(a.SCALE)

    # ADR 0018: the views this drawing has, and where they go, come from ONE resolved plan
    # instead of three hardcoded calls whose cameras, page fields and layout meaning were
    # spread across this function, `Analysis` and `compose.choose_scale`'s docstring. The plan
    # describes the selected subset of the conventional third-angle set. The projection
    # convention is stated where the views are named rather than implied by three camera
    # literals.
    #
    # Cameras are attached here because they need the scaled part's centre and the projection
    # distance, which the plan deliberately knows nothing about: a `ViewSpec` is a request in
    # MODEL terms, and a camera position in page-scaled coordinates is not one.
    _CAMERAS = {
        "front": ((cxs, cys - dist, czs), (0, 0, 1)),
        "plan": ((cxs, cys, czs + dist), (0, 1, 0)),
        "side": ((cxs + dist, cys, czs), (0, 0, 1)),
    }
    dwg._build.view_plan = view_plan = resolve_from_analysis(a)
    for spec in view_plan.of_kind("principal"):
        camera, up = _CAMERAS[spec.name]
        place = view_plan.placements[spec.name]
        dwg._add_view(spec.name, part_s, camera, up, (place.cx, place.cy), scaled=True)
    if a.planned_iso:
        if a.planned_iso_scale is None:
            _project_iso(dwg, a, a.SCALE, shape_s=part_s)
        else:
            _project_iso(dwg, a, a.SCALE * a.planned_iso_scale)

    _diagnostics = None  # the audit ledger; filled once at the end of this function (#996)
    if auto_dims:
        # Snapshot outer_limits before _auto_annotate tightens them against the
        # initial (possibly overflowing) iso.  After _fit_iso_view rescales the
        # iso we restore all three right strips to min(original, final_iso_x_limit)
        # so each strip reflects actual final geometry, not the transient state.
        _fv_ol = a.fv_zones.right.outer_limit
        _pv_ol = a.pv_zones.right.outer_limit
        _sv_ol = a.sv_zones.right.outer_limit
        # The orchestrator RETURNS the omission ledger rather than writing a drawing private,
        # so `annotations/` stays off the state bus (#639/#830). Filled at the single site
        # below, not here — see there (#996 / ADR 0005 §2).
        _diagnostics = _auto_annotate(dwg, a, detail_view=detail_view)
        # The placed annotations are the fit's obstacles (#1240): the grow branch may not
        # invade ink that placed legally against the pre-fit iso. Computed HERE because the
        # fit sits below the occupancy model and must not own an obstacle set (#1197).
        #
        # `annotation_ink_obstacles`, NOT `strip_obstacles`: the sheet frame and zone grid are
        # registered annotations whose Compound bbox spans the page, so the raw strip set made
        # the iso overlap an "obstacle" at every factor and `--frame` disabled the fit
        # altogether — no growth, no NTS caption, fast tier green. It also broke script/CLI
        # parity, since the `auto_dims=False` branch below computes its obstacles before the
        # frame is added and so kept growing (#1240 review r2).
        _nts_bb = None
        if a.planned_iso:
            _nts_bb = _settle_iso_view(dwg, a, obstacles=annotation_ink_obstacles(dwg))
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
            # Mirror for the ABOVE strips (#1240): restore, then re-cap below the FINAL iso only
            # where the fitted iso horizontally overlaps that view — the transposition of the
            # right-strip re-cap above, for the same customer (deferred edits place through these
            # strips after the build).
            _ix1 = _iso_bbox(dwg)[2]
            _iso_y_lim = _iy0 - 4
            for _strip, _x0, _x1 in (
                (a.pv_zones.above, a.PV_X - a.fv_hw, a.PV_X + a.fv_hw),
                (a.sv_zones.above, a.SV_X - a.sv_hw, a.SV_X + a.sv_hw),
            ):
                # TIGHTEN ONLY — no restore-from-snapshot, unlike the right strips above. Their
                # snapshot exists to give back space `_auto_annotate` took against a transient,
                # possibly-overflowing iso; the above strips have no such pre-existing
                # over-tightening to undo, and restoring would DISCARD the `m_locy` approach-buffer
                # clamp (`from_model`), which is a different constraint that must survive
                # (#1240 review F4). Same anchor guard as the initial clamp: an iso x-overlapping
                # the view from BELOW must not push the limit beneath the anchor and kill the strip.
                if _x0 < _ix1 and _ix0 < _x1 and _iso_y_lim > _strip.anchor:
                    _strip.outer_limit = min(_strip.outer_limit, _iso_y_lim)
    else:
        # Fit + label the iso as the auto path does (annotate defaults True): the NTS
        # note is sheet furniture — like the title block below — that states the iso is
        # not to scale. Suppressing it here silently diverged the emitted-script drawing
        # (auto_dims=False) from the direct CLI, which always labels it (script↔CLI parity).
        _nts_bb = (
            _settle_iso_view(dwg, a, obstacles=annotation_ink_obstacles(dwg))
            if a.planned_iso
            else None
        )
        _add_title_block(dwg, a)
        if a.frame:  # sheet border (#767) — auto path adds it via the orchestrator
            _add_sheet_frame(dwg, a)
        if a.zones:  # zone-grid ruler (#768), on the frame
            _add_zone_grid(dwg, a)
        if a.projection:  # projection-method glyph (#769) — auto path adds it via the orchestrator
            _add_projection_symbol(dwg, a)

    # The NTS caption is post-fit late furniture too, and goes FIRST: it is tied to the
    # iso block it labels, whereas a table may sit anywhere the sheet has room. Placing
    # the constrained one first makes it an obstacle for the free one rather than the
    # reverse (`_fit_iso_view` returns the bbox only when the iso is off sheet scale).
    if _nts_bb is not None:
        place_iso_nts_note(dwg, a, _nts_bb)

    # Gear tables are deliberately post-fit late furniture. `_auto_annotate` runs before
    # `_fit_iso_view`; placing a table there lets the subsequently fitted ISO view move into
    # it. Every initial/repacked assembly reaches this common point after its final ISO fit,
    # and `add_table()` now sees the settled views plus all earlier annotations as obstacles.
    render_gear_tables(dwg, pm)

    # The audit ledger, filled at ONE site for both paths (#996 / ADR 0005 §2).
    #
    # It is not a by-product of rendering. The auto path gets it from `_auto_annotate`'s
    # return; `auto_dims=False` draws no automatic dimensions, so it compiles for the
    # diagnostics alone — the plan is discarded, only the record kept. That branch reported an
    # EMPTY ledger while the compiler really had suppressed measurements, which is precisely
    # the false confidence this surface exists to remove (Codex #996 r1).
    #
    # Assigned once rather than in each branch: two fill sites for one BuildState field is
    # what #830's single-construction rule exists to stop, and the guard caught the first
    # attempt at exactly that.
    if _diagnostics is None:
        from draftwright.model.compiled import compile_dimensions

        # No `is not None` guard on the model: `_build.part_model` is filled unconditionally
        # above, so the guard was unreachable — and its fallback was a silent empty ledger,
        # which is the precise failure this whole surface exists to remove. If a future path
        # ever reaches here without a model, that should raise where it happens rather than
        # produce a confident "nothing was suppressed" (#996).
        _diagnostics = compile_dimensions(dwg.model()).diagnostics
    dwg._build.omissions = tuple(_diagnostics or ())
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
    critique_recognition=None,
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
        geometry = _layout_geometry(
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
            required_tables=a.layout_required_tables,
            warn_no_iso=False,
            margin=a.margin,
            # Compose the repack under the SAME arrangement placement used. Without this the
            # default would silently recompose as `columns`, which is the exact stage
            # disagreement this decision is carried to prevent.
            #
            # No natural part reaches this, and the reason is structural rather than an
            # accident of the corpus: repack only re-assembles when measured footprints move
            # a view, and a part annotated densely enough for that is also dense enough to
            # lose a requirement on the smaller sheet the alternative offers — so the gate
            # above rejects it first. The two conditions are anti-correlated BY the gate.
            # (Measured: across the golden corpus and parts built to provoke it,
            # `_repack_to_fixed_point` is entered under `stacked-iso` and always returns
            # None.) `test_adr0018_arrangement_gate` therefore forces the trigger, in the
            # same idiom the other repack tests use, and pins BOTH arrangements so the
            # assertion cannot be met by a constant.
            arrangement=a.arrangement,
            views=a.planned_views,
            include_iso=a.planned_iso,
            iso_scale_factor=a.planned_iso_scale,
        )
        _apply_principal_view_pins(
            geometry,
            a.view_constraints,
            scale=s,
            centre=(a.cx, a.cy, a.cz),
            page=(pw, ph),
            margin=a.margin,
            views=a.planned_views,
        )
        return geometry

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
        critique_recognition=critique_recognition,
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
    critique_recognition=None,
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
            critique_recognition=critique_recognition,
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


def _build_drawing_once(
    step_file: str | Path | Shape,
    out: str | None = None,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str = "ISO 2768-m",
    drawn_by: str = "",
    scale: float | None = None,
    page: str | tuple | None = None,
    auto_dims: bool = True,
    detail_view: bool = True,
    pmi: Literal["off", "report", "annotate"] | None = None,
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
    _analysis_base=None,
    _analysis_sink: Callable[[Analysis], None] | None = None,
    _critique_recognition=None,
    _arrangements: tuple[str, ...] | None = None,
    _views: tuple[str, ...] | None = None,
    _include_iso: bool = True,
    _view_constraints=None,
    _required_tables=(),
) -> Drawing:
    """Build a customisable 4-view :class:`Drawing` without exporting it.

    Same arguments as :func:`make_drawing`, but returns the live :class:`Drawing`
    so you can add or remove annotations and add section/auxiliary views before
    calling :meth:`Drawing.export`. ``make_drawing(...)`` is
    ``build_drawing(...).export(formats=("svg", "dxf"))``, unpacked to a tuple.

    Args:
        auto_dims: pass ``False`` to skip the automatic dimensions,
            centrelines, and leaders (#74) — the automatic set assumes a
            turned part and is wrong for prismatic geometry. Views, scale,
            page, and sheet furniture (title block, and the "ISO VIEW (NTS)"
            note when the iso is rescaled off sheet scale) are still produced;
            add your own annotations before export. (Annotations added by the default can
            also be removed wholesale with :meth:`Drawing.clear_annotations`.)
        detail_view: automatically recover crowded prismatic step dimensions in an
            enlarged detail view. Default ``True``; pass ``False`` to leave them on the
            parent view only and report ``step_dim_dropped`` when they do not fit.
        pmi: AP242 PMI handling. ``None`` (the default) behaves as ``"off"`` but retains
            that it was defaulted so a source containing authored PMI can say annotation is
            disabled by default. Explicit ``"off"`` produces no PMI annotations or per-record
            failures but reports the ignored source inventory; ``"report"`` inventories and
            lowers without rendering; ``"annotate"`` also requires render outcomes.
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
        _reuse=_analysis_base,
        _required_tables=_required_tables,
        _arrangements=_arrangements,
        _views=_views,
        _include_iso=_include_iso,
        _view_constraints=_view_constraints,
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
        critique_recognition=_critique_recognition,
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
            critique_recognition=_critique_recognition,
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
    if _analysis_sink is not None:
        _analysis_sink(a)
    return dwg


class ScaleIncompatibilityError(ValueError):
    """An explicit scale could not preserve required annotation outcomes.

    ``decision`` is the same JSON-friendly record exposed on a successfully returned
    :class:`Drawing` as :attr:`Drawing.scale_decision`.
    """

    def __init__(self, decision: dict):
        self.decision = decision
        codes = ", ".join(sorted({item["code"] for item in decision["blockers"]}))
        attempted = decision.get("attempted_scales", ())
        suffix = f"; tried {list(attempted)}" if attempted else ""
        super().__init__(
            f"requested scale {decision['requested_scale']:g} cannot preserve required "
            f"annotations ({codes or 'no complete standard fallback'}){suffix}"
        )


def _scale_requirement(mid) -> dict:
    """Plain-data identity for one compiler measurement in a scale decision."""
    return {
        "feature": feature_key(getattr(mid, "feature", None)),
        "parameter": str(getattr(mid, "parameter", "")),
    }


def _hole_scale_requirement(requirement) -> dict:
    """Plain-data identity for one recognition-owned hole requirement."""
    feature, parameter = requirement
    return {"feature": feature_key(feature), "parameter": str(parameter)}


def _is_required_scale_drop(issue) -> bool:
    """Whether *issue* is an unresolved required placement outcome.

    Transactional table/balloon attempts may fail while restoring the original complete
    representation; their unowned diagnostics are not evidence that a manufacturing
    requirement was lost.  Semantic measurement/source provenance, an explicit placement
    stage, and all other established ``*_dropped`` codes fail closed.
    """
    stage = getattr(issue, "outcome_stage", None)
    if stage == "validation":
        return False
    if stage == "placement" or issue.code == "placement_unsatisfiable":
        return True
    if not issue.code.endswith("_dropped"):
        return False
    if issue.code in {"table_dropped", "balloon_dropped"}:
        return bool(
            getattr(issue, "measurement_ids", ())
            or getattr(issue, "hole_requirement_ids", ())
            or getattr(issue, "source_ids", ())
        )
    return True


def _scale_blockers_from_issues(issues) -> tuple[dict, ...]:
    """Required placement failures from one already-materialised lint pass."""
    blockers = []
    for issue in issues:
        if not _is_required_scale_drop(issue):
            continue
        blockers.append(
            {
                "severity": issue.severity,
                "code": issue.code,
                "message": issue.message,
                "measurements": tuple(
                    _scale_requirement(mid) for mid in getattr(issue, "measurement_ids", ())
                ),
                "hole_requirements": tuple(
                    _hole_scale_requirement(req)
                    for req in getattr(issue, "hole_requirement_ids", ())
                ),
                "source_ids": tuple(getattr(issue, "source_ids", ())),
            }
        )
    return tuple(blockers)


def _scale_blockers(drawing: Drawing, *, physical: bool = True) -> tuple[dict, ...]:
    """Required placement failures on a finished drawing, as stable plain data.

    ``physical=False`` restricts the critique to the recognition-free components, so a caller
    that must not materialise the ADR 0017 aggregate can still read what failed to place.
    """
    return _scale_blockers_from_issues(drawing.lint(physical=physical))


def _blocker_identity(blocker) -> str:
    """A stable identity for one required placement failure, for comparing two builds.

    Keyed on WHAT was lost — the code and the measurement/requirement/source ids — never the
    message, which carries positions and sheet sizes that differ between two layouts of the
    same defect and would make every blocker look unique.
    """
    return json.dumps(
        {
            "code": blocker.get("code"),
            "measurements": blocker.get("measurements", ()),
            "hole_requirements": blocker.get("hole_requirements", ()),
            "source_ids": blocker.get("source_ids", ()),
        },
        sort_keys=True,
        default=str,
    )


def _complete_automatic_plan(drawing: Drawing, *, issues=None) -> Drawing:
    """Give the automatic path the completeness the explicit path already has (#1250).

    `_scale_blockers` ran on the explicit-scale policy loop and on nothing else, so the two
    paths disagreed about the same part: asked for the sheet and scale it had just chosen
    itself, the engine would refuse or quietly reduce the scale, while the automatic path
    returned the incomplete drawing reporting `passed: True`.

    Severity is NOT the discriminator: the explicit path never accepts a drawing with blockers
    at all. The automatic path cannot safely search scales here, because annotations whose
    semantic identity has not yet reached the registry make candidate coverage unverifiable.
    Accepting a candidate from the known subset would violate ADR 0017/0018 by allowing
    source-only or physical requirements to disappear. It therefore fails closed on the
    settled drawing and records `plan_incomplete` at error severity. This makes `passed` false
    without introducing a second sheet/scale-selection policy ahead of #1262.
    """
    # One materialised recognition-free lint feeds blockers, provenance, and the
    # final decision. Corrective candidates pass their acceptance lint through so
    # the selected winner is not immediately re-linted from scratch.
    issues = tuple(drawing.lint(physical=False)) if issues is None else tuple(issues)
    blockers = _scale_blockers_from_issues(issues)
    if not blockers:
        return drawing

    codes = ", ".join(sorted({item["code"] for item in blockers}))
    dropped = [i for i in issues if _is_required_scale_drop(i)]
    measurements = tuple(
        dict.fromkeys(mid for issue in dropped for mid in getattr(issue, "measurement_ids", ()))
    )
    hole_requirements = tuple(
        dict.fromkeys(
            req for issue in dropped for req in getattr(issue, "hole_requirement_ids", ())
        )
    )
    source_ids = tuple(
        dict.fromkeys(sid for issue in dropped for sid in getattr(issue, "source_ids", ()))
    )
    drawing.registry.record_issue(
        LintIssue(
            severity="error",
            code="plan_incomplete",
            message=(
                f"the automatically planned sheet drops {len(blockers)} required "
                f"annotation outcome(s) ({codes})"
            ),
            measurement_ids=measurements,
            source_ids=source_ids,
            hole_requirement_ids=hole_requirements,
        )
    )
    previous = getattr(drawing, "scale_decision", {})
    previous_attempts = tuple(previous.get("attempts", ()))
    previous_scales = tuple(previous.get("attempted_scales", ()))
    incomplete_attempt = _scale_attempt(
        drawing.scale,
        "incomplete",
        blockers,
        reason="required_outcome_dropped",
        views=drawing.views,
        page=(drawing.page_w, drawing.page_h),
    )
    drawing.scale_decision = _scale_decision(
        policy="automatic",
        requested=None,
        effective=drawing.scale,
        status="incomplete",
        blockers=blockers,
        attempted=previous_scales + (drawing.scale,),
        attempts=previous_attempts + (incomplete_attempt,),
    )
    warnings.warn(
        f"the automatically planned sheet drops required annotation outcomes ({codes}); "
        f"returning the incomplete drawing — see Drawing.scale_decision",
        ScaleCompletenessWarning,
        stacklevel=2,
    )
    return drawing


def _preserve_requirements_under_arrangement(drawing, chosen, build, blockers_for):
    """ADR 0018 §5's first hard gate, applied to the arrangement: preserve every supported
    requirement or reject the candidate.

    The candidate loop can only ask whether the view blocks *fit*. Fitting is necessary and
    not sufficient, which #1130 measured three separate ways: an alternative arrangement can
    reach a larger scale whose enlarged views leave the location dims nowhere to go; it can
    reach a smaller sheet whose reduced free area does the same; and re-deriving it per stage
    can compose a sheet under an arrangement whose feasibility was never established. In each
    case the geometry was feasible and the drawing lost a dimension anyway.

    So the gate is not predicted, it is measured on the finished drawing (ADR 0014 Amdt 3),
    reusing the engine's own definition of a lost requirement — the same `_scale_blockers`
    the explicit-scale policy rejects a scale on. Applying it here is what closes the gap
    that made the automatic path emit sheets it would have refused if asked for them
    explicitly (#1250).

    Costs a second compile only for a drawing that both departed from the preferred
    arrangement AND lost something: an alternative that lost nothing cannot be improved on,
    and the preferred arrangement is never second-guessed at all (the caller checks that
    before calling, since only it knows what the attempt was built under). Every attempt is
    recorded on the returned drawing, so the cost and the reason are both visible.
    """

    def _record(winner, attempts):
        winner.arrangement_decision = {
            "chosen": next(name for name, status, _found in attempts if status == "chosen"),
            "attempts": tuple(
                {"arrangement": name, "status": status, "blockers": found}
                for name, status, found in attempts
            ),
        }
        return winner

    blockers = blockers_for(drawing)
    if not blockers:
        return _record(drawing, [(chosen, "chosen", ())])

    # Rebuild confined to the arrangement every drawing used before this choice existed. It
    # is kept only if it genuinely preserves more — an alternative is not rejected for
    # blockers that the preferred arrangement would have produced too.
    preferred = build(None, (ARRANGEMENTS[0],))
    preferred_blockers = blockers_for(preferred)
    # Compared by IDENTITY as a multiset, not by count. Cardinality alone accepts a
    # DIFFERENT loss: if the preferred layout drops requirement B and the alternative drops
    # requirement A, both have one blocker, and a `<` test keeps the alternative even though
    # the default preserved A. That is the opposite of "preserve every supported requirement
    # or reject the candidate" (#1130 review).
    #
    # The rule is therefore one-sided, and deliberately so: the alternative may not introduce
    # any blocker the preferred result did not already have. It is free to preserve MORE, and
    # it does not have to beat the default on volume — the default is the baseline every
    # drawing had before this choice existed, so an alternative earns its place by costing
    # nothing, not by costing less.
    introduced = collections.Counter(map(_blocker_identity, blockers)) - collections.Counter(
        map(_blocker_identity, preferred_blockers)
    )
    if introduced:
        return _record(
            preferred,
            [
                (chosen, "rejected", blockers),
                (ARRANGEMENTS[0], "chosen", preferred_blockers),
            ],
        )
    return _record(
        drawing,
        [
            (chosen, "chosen", blockers),
            (ARRANGEMENTS[0], "rejected", preferred_blockers),
        ],
    )


def _scale_decision(
    *,
    policy: str,
    requested: float | None,
    effective: float,
    status: str,
    blockers=(),
    attempted=(),
    attempts=(),
) -> dict:
    return {
        "policy": policy,
        "requested_scale": requested,
        "effective_scale": effective,
        "status": status,
        "blockers": tuple(blockers),
        "attempted_scales": tuple(attempted),
        "attempts": tuple(attempts),
    }


def _scale_attempt(
    scale: float | None,
    status: str,
    blockers=(),
    *,
    error: str | None = None,
    reason: str | None = None,
    rejection: str | None = None,
    views: Iterable[str] | None = None,
    page: tuple[float, float] | None = None,
) -> dict:
    """One plain-data trial in an explicit-scale decision."""
    attempt = {"scale": scale, "status": status, "blockers": tuple(blockers)}
    if error is not None:
        attempt["error"] = error
    if reason is not None:
        attempt["reason"] = reason
    if rejection is not None:
        attempt["rejection"] = rejection
    if views is not None:
        attempt["views"] = tuple(views)
    if page is not None:
        attempt["page"] = tuple(page)
    return attempt


def _blocks_all_smaller_scales(blockers) -> bool:
    """True when a blocker is mathematically monotone under scale reduction.

    A step separation already below the fixed paper-space legibility threshold only shrinks
    further at every smaller scale. Rebuilding the whole ISO ladder cannot remove it; stopping
    here is both exact and keeps an impossible fallback bounded (#1146).
    """
    return any(
        item["code"] == "step_dim_dropped" and "too closely spaced" in item["message"]
        for item in blockers
    )


_AUTOMATIC_UPSCALE_TRIAL_LIMIT = 2


def _has_detail_view(views) -> bool:
    """Whether a resolved drawing/view mapping contains an automatic detail."""
    return any(name.startswith("detail_") for name in views)


def _is_expected_candidate_build_failure(exc: Exception) -> bool:
    """Whether a speculative build may reject without hiding an invariant bug."""
    return isinstance(exc, Standard_Failure) or (
        isinstance(exc, ValueError) and "drawing geometry degenerates" in str(exc)
    )


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
    detail_view: bool = True,
    pmi: Literal["off", "report", "annotate"] | None = None,
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
    scale_policy: Literal["strict", "fallback", "permissive"] = "fallback",
    _post_build: Callable[[Drawing], Drawing] | None = None,
    _required_tables=(),
    _views: tuple[str, ...] | None = None,
    _include_iso: bool = True,
    _view_constraints=None,
) -> Drawing:
    """Build a drawing, protecting required annotations under an explicit scale.

    ``scale_policy`` applies only when ``scale`` is supplied. ``"fallback"`` (the safe
    default) retries smaller preferred ISO 5455 scales and returns the largest one with no
    required placement drop. ``"strict"`` raises :class:`ScaleIncompatibilityError` instead.
    ``"permissive"`` explicitly opts into the historical best-effort result and warns when
    it is degraded. Every returned drawing exposes the JSON-friendly decision through
    :attr:`Drawing.scale_decision`; :attr:`Drawing.scale` is the effective scale.

    Other arguments and return semantics are unchanged from the one-pass builder.
    """
    if scale_policy not in {"strict", "fallback", "permissive"}:
        raise ValueError(
            f"scale_policy must be 'strict', 'fallback', or 'permissive', got {scale_policy!r}"
        )
    one_pass = partial(
        _build_drawing_once,
        step_file,
        out=out,
        title=title,
        number=number,
        tolerance=tolerance,
        drawn_by=drawn_by,
        page=page,
        auto_dims=auto_dims,
        detail_view=detail_view,
        pmi=pmi,
        repair=repair,
        assembly=assembly,
        model=model,
        decorations=decorations,
        requested=requested,
        authored=authored,
        trace=trace,
        material=material,
        date=date,
        revision=revision,
        company=company,
        frame=frame,
        projection=projection,
        zones=zones,
        _required_tables=_required_tables,
        _include_iso=_include_iso,
        _view_constraints=_view_constraints,
    )
    analysis_base = None
    latest_analysis = None
    critique_recognition = None

    built_arrangement = ARRANGEMENTS[0]

    def _build(
        candidate_scale: float | None,
        arrangements: tuple[str, ...] | None = None,
        views: tuple[str, ...] | None = None,
        include_iso: bool | None = None,
        page_override: str | tuple | None = None,
    ) -> Drawing:
        nonlocal analysis_base

        # Default to the REQUESTED view set, not to None. Any rebuild — the arrangement
        # gate's fallback, a scale retry — must carry the decisions the attempt was made
        # under; the arrangement gate's rebuild silently reverted a two-view request to four
        # views on a larger sheet until this defaulted (#1130). Same defect class the carried
        # arrangement fixed, one stage further out.
        views = _views if views is None else views

        if arrangements is None and not auto_dims:
            # Nothing to measure means nothing is proved, so fail closed on the arrangement
            # every drawing used before the choice existed. The gate establishes an
            # alternative's feasibility by compiling the requirements and reading what failed
            # to place; a build with no automatic dimensioning compiles none, so an
            # alternative would be accepted on the strength of an empty ledger — and then
            # annotations added afterwards through the deferred/`Sheet` seams would be the
            # ones to lose. Measured: a mixed deferred batch loses its shoulder dimension.
            arrangements = (ARRANGEMENTS[0],)

        def retain_analysis(value: Analysis) -> None:
            # `analysis_base` keeps the FIRST analysis (geometry reuse across attempts);
            # `built_arrangement` tracks the LATEST, because the arrangement gate asks what
            # the attempt just built under. Read here rather than off the returned drawing:
            # engine modules must not touch `dwg._*` (ADR 0005 §2).
            nonlocal analysis_base, built_arrangement, latest_analysis
            built_arrangement = value.arrangement
            latest_analysis = value
            if analysis_base is None:
                analysis_base = value

        built = one_pass(
            scale=candidate_scale,
            page=page if page_override is None else page_override,
            _analysis_base=analysis_base,
            _analysis_sink=retain_analysis,
            _critique_recognition=critique_recognition,
            _arrangements=arrangements,
            _views=views,
            _include_iso=_include_iso if include_iso is None else include_iso,
            _view_constraints=_view_constraints,
        )
        _validate_authored_view_layout(built, _view_constraints)
        return _post_build(built) if _post_build is not None else built

    def scale_blockers_for(built: Drawing) -> tuple[dict, ...]:
        nonlocal critique_recognition
        found = _scale_blockers(built)
        if critique_recognition is None:
            critique_recognition = built.recognition()
        return found

    if scale is None:
        if scale_policy != "fallback":
            raise ValueError("scale_policy applies only when an explicit scale is supplied")
        views_are_automatic = _view_constraints is None or (
            isinstance(_view_constraints, ViewConstraints) and _view_constraints.is_automatic_only
        )
        drawing = _build(None, views=_views)
        if built_arrangement != ARRANGEMENTS[0]:
            # The RECOGNITION-FREE critique, not `scale_blockers_for`: the gate must reach the
            # same verdict whether the model was detected or declared, or the arrangement
            # would depend on how the part was described and ADR 0011's declare-equals-detect
            # parity would break. Materialising the aggregate here would also recognise a
            # solid whose features a declared caller has already stated (ADR 0011 / 0017).
            # Placement drops are recorded during the build, so they survive the restriction.
            drawing = _preserve_requirements_under_arrangement(
                drawing,
                built_arrangement,
                _build,
                lambda built: _scale_blockers(built, physical=False),
            )
        dimensions_are_automatic = auto_dims and drawing.model().authored_dimensions is None
        original_scale = drawing.scale
        original_page = (drawing.page_w, drawing.page_h)
        replanned = False
        replan_attempts = []
        settled_issues = None
        arrangement_decision = getattr(drawing, "arrangement_decision", None)
        settled_arrangement = (
            arrangement_decision["chosen"]
            if arrangement_decision is not None
            else built_arrangement
        )

        def _retain_arrangement(candidate):
            # The corrective builds are confined to the settled arrangement.  Preserve
            # the original decision record as well: it explains why that arrangement won,
            # whereas a fresh one-attempt record would erase the rejected alternatives.
            if arrangement_decision is not None:
                candidate.arrangement_decision = arrangement_decision
            return candidate

        def _automatic_assessment(candidate):
            # One lint pass feeds both structural and requirement gates.  Re-running lint
            # here used to duplicate the most expensive post-build critique for every trial.
            issues = tuple(candidate.lint(physical=False))
            return issues, _scale_blockers_from_issues(issues)

        def _record_attempt(
            scale,
            status,
            blockers=(),
            *,
            reason,
            candidate=None,
            views=None,
            page=None,
            error=None,
            rejection=None,
        ):
            if candidate is not None:
                views = candidate.views
                page = (candidate.page_w, candidate.page_h)
            replan_attempts.append(
                _scale_attempt(
                    scale,
                    status,
                    blockers,
                    reason=reason,
                    rejection=rejection,
                    views=views,
                    page=page,
                    error=error,
                )
            )

        def _qualify_candidate(candidate, *, require_axial_coverage=False):
            """Run cheap semantic gates before the one full acceptance lint."""
            if _has_detail_view(candidate.views):
                return (), (), "recovery_detail_retained"
            if require_axial_coverage:
                assert latest_analysis is not None
                if lint_axial_coverage(
                    latest_analysis.part,
                    candidate,
                    prof=latest_analysis.prof,
                ):
                    return (), (), "axial_coverage_incomplete"
            issues, blockers = _automatic_assessment(candidate)
            if any(issue.severity == "error" for issue in issues):
                return issues, blockers, "structural_error"
            if blockers:
                return issues, blockers, "required_outcome_dropped"
            return issues, blockers, None

        def _try_larger_standard_pages(
            starting_page,
            *,
            include_iso,
            reason,
            fallback_views,
            require_axial_coverage,
        ):
            """Try the bounded standard-page tail under one settled correction policy."""
            standard_pages = tuple(_PAGE_SIZES.items())
            original_index = next(
                (
                    index
                    for index, (_name, dimensions) in enumerate(standard_pages)
                    if tuple(dimensions) == starting_page
                ),
                None,
            )
            larger_pages = (
                standard_pages[original_index + 1 :] if original_index is not None else ()
            )
            for page_name, page_dimensions in larger_pages:
                try:
                    larger = _build(
                        None,
                        arrangements=(settled_arrangement,),
                        include_iso=include_iso,
                        page_override=page_name,
                    )
                except (ValueError, Standard_Failure) as exc:
                    if not _is_expected_candidate_build_failure(exc):
                        raise
                    _log.info(
                        "automatic page escalation to %s rejected (candidate build failed: %s)",
                        page_name,
                        exc,
                    )
                    _record_attempt(
                        None,
                        "error",
                        reason=reason,
                        views=fallback_views,
                        page=page_dimensions,
                        error=str(exc),
                    )
                    continue
                larger = _retain_arrangement(larger)
                issues, blockers, rejection = _qualify_candidate(
                    larger,
                    require_axial_coverage=require_axial_coverage,
                )
                if rejection is None:
                    _record_attempt(
                        larger.scale,
                        "complete",
                        reason=reason,
                        candidate=larger,
                    )
                    return larger, issues
                _record_attempt(
                    larger.scale,
                    "rejected",
                    blockers,
                    reason=reason,
                    rejection=rejection,
                    candidate=larger,
                )
            return None, None

        def _try_larger_scales_on_selected_page(starting_scale, *, reason, require_axial_coverage):
            """Try the bounded larger-scale tail on the ALREADY SELECTED sheet.

            The sheet is not the first lever.  Raising the scale spreads the features apart
            on the page the automatic selection already chose, so a placement shortage that
            a larger sheet would clear can often be cleared without changing the sheet the
            shop receives, and without spending an optional view for it (#1338).  Each
            candidate passes the same structural, required-outcome and (when the failure
            was an axial one) axial-coverage gates as any other attempt.
            """
            candidate_scales = sorted(item for item in _SCALES if item > starting_scale)[
                :_AUTOMATIC_UPSCALE_TRIAL_LIMIT
            ]
            for candidate_scale in candidate_scales:
                try:
                    candidate_drawing = _build(
                        candidate_scale,
                        arrangements=(settled_arrangement,),
                        page_override=original_page,
                    )
                except (ValueError, Standard_Failure) as exc:
                    if not _is_expected_candidate_build_failure(exc):
                        raise
                    _log.info(
                        "%s %s:1 rejected (candidate build failed: %s)",
                        reason,
                        candidate_scale,
                        exc,
                    )
                    _record_attempt(
                        candidate_scale,
                        "error",
                        reason=reason,
                        views=drawing.views,
                        page=original_page,
                        error=str(exc),
                    )
                    continue
                candidate_drawing = _retain_arrangement(candidate_drawing)
                assert (candidate_drawing.page_w, candidate_drawing.page_h) == original_page
                issues, blockers, rejection = _qualify_candidate(
                    candidate_drawing,
                    require_axial_coverage=require_axial_coverage,
                )
                if rejection is None:
                    _record_attempt(
                        candidate_scale,
                        "complete",
                        reason=reason,
                        candidate=candidate_drawing,
                    )
                    return candidate_drawing, issues
                _record_attempt(
                    candidate_scale,
                    "rejected",
                    blockers,
                    reason=reason,
                    rejection=rejection,
                    candidate=candidate_drawing,
                )
            return None, None

        # #1155: the compose-time estimate conservatively reserves an enlarged
        # detail for a crowded run.  Some larger preferred scales make that run
        # readable inline, so the detail reservation disappears and the same page
        # becomes feasible — GRM-04 is 2:1 under the estimate but complete at 5:1
        # after its Y location re-homes from side-below to plan-right.  Measure
        # those larger candidates only when the settled result actually contains
        # that semantic recovery artifact: post-build occupied rectangles are not
        # a scale-selection input.  A candidate may win only on the same sheet and
        # settled arrangement, with no recovery detail or required placement loss.
        if dimensions_are_automatic and views_are_automatic and _has_detail_view(drawing.views):
            _record_attempt(
                drawing.scale,
                "detail_reservation_conservative",
                reason="measured_upscale",
                candidate=drawing,
            )
            upscaled, upscaled_issues = _try_larger_scales_on_selected_page(
                original_scale,
                reason="measured_upscale",
                require_axial_coverage=False,
            )
            if upscaled is not None:
                drawing = upscaled
                settled_issues = upscaled_issues
                replanned = True

        # #443/#1299: a pictorial view is useful context, but it cannot outrank the
        # dimensions or other required annotations needed to manufacture a part.
        # GRM-03 originally selected 2:1 with ISO, collapsed its 0.5 + 2 mm head
        # steps into an unowned 2.5 mm block, then had no room for the recovery
        # detail.  Typed PMI can reach the same correction for the complementary
        # reason: all shoulders are covered, but a required feature callout has no
        # route.  Re-plan once without the optional ISO in either case.  This is
        # deliberately a measured semantic comparison, not suppression of lint:
        # the candidate wins only after the same read-back and required-outcome
        # gates prove it complete.
        if (
            dimensions_are_automatic
            and _include_iso
            and views_are_automatic
            and "iso" in drawing.views
        ):
            assert latest_analysis is not None
            original_has_axial_gap = bool(
                lint_axial_coverage(
                    latest_analysis.part,
                    drawing,
                    prof=latest_analysis.prof,
                )
            )
            original_issues, original_blockers = _automatic_assessment(drawing)
            source_blockers = tuple(
                blocker for blocker in original_blockers if blocker["source_ids"]
            )
            settled_issues = original_issues
            recovered_on_selected_page = False
            if original_has_axial_gap or source_blockers:
                _record_attempt(
                    drawing.scale,
                    (
                        "axial_coverage_incomplete"
                        if original_has_axial_gap
                        else "required_outcome_dropped"
                    ),
                    source_blockers,
                    reason="remove_optional_iso",
                    candidate=drawing,
                )
                # #1338: before spending the optional ISO and then the sheet, try the
                # bounded larger-scale tail on the page already selected.  GRM-03 settled
                # on 5:1/A3 without its ISO while 5:1/A4 is clean WITH it — a strictly
                # better candidate the ladder never reached, because its only recovery
                # order was drop-the-ISO then escalate-the-page.  The gates are unchanged:
                # this wins only by passing the same axial and required-outcome checks the
                # larger sheet would have had to pass.
                upscaled, upscaled_issues = _try_larger_scales_on_selected_page(
                    drawing.scale,
                    reason="scale_escalation_on_selected_page",
                    require_axial_coverage=True,
                )
                if upscaled is not None:
                    drawing = upscaled
                    settled_issues = upscaled_issues
                    replanned = True
                    recovered_on_selected_page = True
            if (original_has_axial_gap or source_blockers) and not recovered_on_selected_page:
                try:
                    without_iso_proposal = _build(
                        None,
                        arrangements=(settled_arrangement,),
                        include_iso=False,
                    )
                except (ValueError, Standard_Failure) as exc:
                    if not _is_expected_candidate_build_failure(exc):
                        raise
                    _log.info("optional-ISO replan rejected (build failed: %s)", exc)
                    _record_attempt(
                        drawing.scale,
                        "error",
                        reason="remove_optional_iso",
                        views=tuple(name for name in drawing.views if name != "iso"),
                        page=original_page,
                        error=str(exc),
                    )
                else:
                    proposal_page = (
                        without_iso_proposal.page_w,
                        without_iso_proposal.page_h,
                    )
                    if proposal_page != original_page:
                        _record_attempt(
                            without_iso_proposal.scale,
                            "scale_proposal",
                            reason="remove_optional_iso",
                            candidate=without_iso_proposal,
                        )
                        try:
                            without_iso = _build(
                                None,
                                arrangements=(settled_arrangement,),
                                include_iso=False,
                                page_override=original_page,
                            )
                        except (ValueError, Standard_Failure) as exc:
                            if not _is_expected_candidate_build_failure(exc):
                                raise
                            _log.info(
                                "fixed-page optional-ISO replan rejected (build failed: %s)",
                                exc,
                            )
                            _record_attempt(
                                None,
                                "error",
                                reason="remove_optional_iso",
                                views=without_iso_proposal.views,
                                page=original_page,
                                error=str(exc),
                            )
                            without_iso = None
                    else:
                        without_iso = without_iso_proposal

                    if without_iso is not None:
                        without_iso = _retain_arrangement(without_iso)
                        assert (without_iso.page_w, without_iso.page_h) == original_page
                        issues, blockers, rejection = _qualify_candidate(
                            without_iso,
                            require_axial_coverage=True,
                        )
                        if rejection is None:
                            _record_attempt(
                                without_iso.scale,
                                "complete",
                                reason="remove_optional_iso",
                                candidate=without_iso,
                            )
                            drawing = without_iso
                            settled_issues = issues
                            replanned = True
                        else:
                            _record_attempt(
                                without_iso.scale,
                                "rejected",
                                blockers,
                                reason="remove_optional_iso",
                                rejection=rejection,
                                candidate=without_iso,
                            )
                            # #1299: page preference is subordinate to manufacturing
                            # completeness. Once the settled no-ISO arrangement has failed
                            # on the automatically selected sheet, try only the bounded
                            # sequence of larger standard pages. Each page chooses its scale
                            # through the established fixed-page policy and must pass the
                            # same axial, structural, and required-outcome gates above.
                            if page is None:
                                larger, issues = _try_larger_standard_pages(
                                    original_page,
                                    include_iso=False,
                                    reason="page_escalation_after_optional_iso",
                                    fallback_views=tuple(
                                        name for name in drawing.views if name != "iso"
                                    ),
                                    require_axial_coverage=True,
                                )
                                if larger is not None:
                                    drawing = larger
                                    settled_issues = issues
                                    replanned = True
        # The default record, set BEFORE the completeness pass so that pass can replace it.
        # It used to be assigned afterwards and silently overwrote whatever the pass had
        # decided, so an incomplete plan reported itself as an ordinary automatic one.
        # Completeness runs last because the arrangement gate above may return a different
        # drawing, and it is the settled drawing whose completeness matters.
        drawing.scale_decision = _scale_decision(
            policy="automatic",
            requested=None,
            effective=drawing.scale,
            status="automatic_replanned" if replanned else "automatic",
            attempted=tuple(
                item["scale"] for item in replan_attempts if item["scale"] is not None
            ),
            attempts=replan_attempts,
        )
        if replan_attempts and drawing.solve_trace is not None:
            # Every corrective candidate was a full build, and each build writes the
            # one shared trace path, so the file on disk may describe a *rejected*
            # candidate rather than the drawing returned.  The settled drawing's own
            # recorder holds the shipped build's records; give it the last write so
            # DRAFTWRIGHT_TRACE always describes the drawing the caller receives
            # (#736 — the same reason a successful finalize re-writes).
            drawing.solve_trace.write()
        return _complete_automatic_plan(drawing, issues=settled_issues)

    requested_scale = float(scale)

    drawing = _build(requested_scale)
    blockers = scale_blockers_for(drawing)
    if not blockers:
        drawing.scale_decision = _scale_decision(
            policy=scale_policy,
            requested=requested_scale,
            effective=drawing.scale,
            status="honored",
            attempted=(requested_scale,),
            attempts=(_scale_attempt(requested_scale, "complete"),),
        )
        return drawing

    if scale_policy == "permissive":
        drawing.scale_decision = _scale_decision(
            policy=scale_policy,
            requested=requested_scale,
            effective=drawing.scale,
            status="degraded",
            blockers=blockers,
            attempted=(requested_scale,),
            attempts=(_scale_attempt(requested_scale, "incomplete", blockers),),
        )
        codes = ", ".join(sorted({item["code"] for item in blockers}))
        warnings.warn(
            f"requested scale {requested_scale:g} dropped required annotation outcomes "
            f"({codes}); returning the incomplete drawing because scale_policy='permissive'",
            ScaleCompletenessWarning,
            stacklevel=2,
        )
        return drawing

    if scale_policy == "strict":
        raise ScaleIncompatibilityError(
            _scale_decision(
                policy=scale_policy,
                requested=requested_scale,
                effective=drawing.scale,
                status="rejected",
                blockers=blockers,
                attempted=(requested_scale,),
                attempts=(_scale_attempt(requested_scale, "incomplete", blockers),),
            )
        )

    attempted = [requested_scale]
    attempts = [_scale_attempt(requested_scale, "incomplete", blockers)]
    last_effective_scale = drawing.scale
    last_blockers = blockers
    if _blocks_all_smaller_scales(blockers):
        raise ScaleIncompatibilityError(
            _scale_decision(
                policy=scale_policy,
                requested=requested_scale,
                effective=drawing.scale,
                status="no_complete_scale",
                blockers=blockers,
                attempted=attempted,
                attempts=attempts,
            )
        )
    # ``_SCALES`` is descending and contains the preferred ISO 5455 reductions. The
    # requested non-standard scale is evaluated first above; fallback candidates must be
    # standard and no greater than it.
    for candidate in (item for item in _SCALES if item < requested_scale):
        attempted.append(candidate)
        try:
            fallback = _build(candidate)
        except ValueError as exc:
            # Once a smaller scale hits the hard rendering floor, every following candidate
            # is smaller still. Do not hide any unrelated build error.
            if "drawing geometry degenerates" in str(exc):
                attempts.append(_scale_attempt(candidate, "render_floor", error=str(exc)))
                break
            raise
        candidate_blockers = scale_blockers_for(fallback)
        if candidate_blockers:
            attempts.append(_scale_attempt(candidate, "incomplete", candidate_blockers))
            last_effective_scale = fallback.scale
            last_blockers = candidate_blockers
            continue
        attempts.append(_scale_attempt(candidate, "complete"))
        fallback.scale_decision = _scale_decision(
            policy=scale_policy,
            requested=requested_scale,
            effective=fallback.scale,
            status="fallback",
            blockers=blockers,
            attempted=attempted,
            attempts=attempts,
        )
        warnings.warn(
            f"requested scale {requested_scale:g} dropped required annotation outcomes; "
            f"using complete fallback scale {fallback.scale:g}",
            ScaleCompletenessWarning,
            stacklevel=2,
        )
        return fallback

    raise ScaleIncompatibilityError(
        _scale_decision(
            policy=scale_policy,
            requested=requested_scale,
            effective=last_effective_scale,
            status="no_complete_scale",
            blockers=last_blockers,
            attempted=attempted,
            attempts=attempts,
        )
    )


# Preserve the established detailed public reference (model/trace/PMI/editing semantics) while
# adding the new policy argument. The one-pass helper owns that long contract because it is the
# pipeline implementation; mkdocstrings and ``help(build_drawing)`` see the augmented text here.
build_drawing.__doc__ = (_build_drawing_once.__doc__ or "").replace(
    "    Args:\n",
    "    Args:\n"
    "        scale_policy: required-annotation policy for an explicit scale. ``'fallback'`` "
    "retries smaller preferred ISO 5455 scales; ``'strict'`` raises "
    ":class:`ScaleIncompatibilityError`; ``'permissive'`` explicitly returns a warned "
    "degraded result. Returned drawings expose ``scale_decision``.\n",
    1,
)


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
    detail_view: bool = True,
    pmi: Literal["off", "report", "annotate"] | None = None,
    assembly: bool | None = None,
    material: str = "",
    date: str = "",
    revision: str = "A",
    company: str = "",
    frame: bool = False,
    projection: str | None = None,
    zones: bool = False,
    scale_policy: Literal["strict", "fallback", "permissive"] = "fallback",
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
        scale_policy: required-annotation policy for an explicit ``scale``. ``"fallback"``
            retries smaller preferred scales, ``"strict"`` raises when the request loses a
            required outcome, and ``"permissive"`` explicitly returns the degraded result.
        page: Page-size override — an ISO name (``"A3"``), ``"WIDTHxHEIGHT"``
            in mm, or a ``(width, height)`` tuple. Default: chosen
            automatically by :func:`choose_scale`.
        auto_dims: pass ``False`` to skip the automatic dimensions,
            centrelines, and leaders (#74) — views, scale, page, and title
            block only.
        detail_view: automatically add an enlarged view for crowded prismatic step
            dimensions. Default ``True``; pass ``False`` to disable that recovery.

    Returns:
        Tuple of ``(svg_path, dxf_path)`` for the generated files.

    This is a thin wrapper: ``make_drawing(...)`` is
    ``build_drawing(...).export(formats=("svg", "dxf"))``, unpacked to a tuple. Not a bare
    ``.export()`` — that is the deprecated legacy shape and warns (#987).
    To add or remove annotations or add section/auxiliary views before export,
    call :func:`build_drawing` and use the returned :class:`Drawing`.
    """
    # `formats=("svg", "dxf")` rather than a bare `.export()` (#987): the no-formats call is
    # the deprecated legacy shape and now warns, and a warning raised from HERE would blame
    # draftwright's own line for a call the caller never made — the #965 stacklevel lesson.
    # This keeps make_drawing's documented `(svg_path, dxf_path)` return while leaving the
    # legacy path with no internal callers, which is what lets it warn honestly.
    _paths = build_drawing(
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
        scale_policy=scale_policy,
    ).export(formats=("svg", "dxf"))
    assert isinstance(_paths, dict)  # formats=... always returns the {format: path} dict
    return _paths["svg"], _paths["dxf"]
