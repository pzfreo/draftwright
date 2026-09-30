"""Build orchestration (#138 / ADR 1 (was 0005), P6).

The pipeline driver: `build_drawing` runs analysis -> assemble (project +
annotate + fit) -> measure-and-repack -> returns the `Drawing`; `make_drawing`
wraps it with export. (The editable-script generator moved out: #940 retired the
imperative one and `sheet_emit` owns the surviving declarative emitter.) Imports
`drawing` (the result object), `analysis`, the annotation orchestrator, and the
stage modules -- never make_drawing -- so the graph stays a DAG.
"""

from __future__ import annotations

import collections
import math
import os
import warnings
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, cast

from build123d import (
    BoundBox,
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
    _add_default_surface_finish,
    _add_projection_symbol,
    _add_scale_note,
    _add_sheet_frame,
    _add_title_block,
    _add_zone_grid,
    _analysis_margins,
    _dimension_draft,
    _dimension_head_bounds,
    _iso_bbox,
    _log,
    _parse_page,
    _Projector,
    _tb_width,
    _title_block_box,
)
from draftwright._geometry import BOUNDS_ROUNDOFF, _boxes_overlap, _scale_world
from draftwright._warnings import ScaleCompletenessWarning
from draftwright.analysis import Analysis, _analyse, _apply_principal_view_pins
from draftwright.annotation_layout_profile import (
    AnnotationLayoutProfile,
    annotation_layout_policy,
    use_layout_profile,
)
from draftwright.annotations._common import (
    SolveTrace,
    _geom_box,
    annotation_ink_obstacles,
    place_iso_nts_note,
)
from draftwright.annotations.from_model import render_document_notes
from draftwright.annotations.gears import render_gear_tables
from draftwright.annotations.orchestrator import (
    _auto_annotate,
    build_model,
    build_rotational_feature,
)
from draftwright.build_once import build_once
from draftwright.build_options import BuildOptions
from draftwright.build_policy import (
    ScaleIncompatibilityError as ScaleIncompatibilityError,
)
from draftwright.build_policy import (
    _arrangement_quality as _arrangement_quality,
)
from draftwright.build_policy import (
    _automatic_candidate_rejection,
    _axial_dimension_losses,
    _blocker_identity,
    _complete_automatic_plan,
    _hard_layout_issues,
    _has_detail_view,
    _is_expected_candidate_build_failure,
    _layout_issue_records,
    _preserve_requirements_under_arrangement,
    _principal_view_exceeds_page,
    _replannable_losses,
    _scale_attempt,
    _scale_blockers,
    _scale_blockers_from_issues,
    _scale_decision,
    _scale_requirement,
    _short_off_axis_span_blocks_smaller_scales,
    _structural_layout_issues,
)
from draftwright.build_policy import (
    _is_required_scale_drop as _is_required_scale_drop,
)
from draftwright.compose import (
    ViewBlock,
    _attribute_annotations,
    _build_rear_zones,
    _build_zones,
    _layout_geometry,
    _page_furniture_fits,
    _view_geom,
)
from draftwright.document_input import DocumentInput
from draftwright.drawing import Drawing
from draftwright.drawing_diagnostics import reuse_finished_build_lint, suspend_finished_build_lint
from draftwright.explicit_scale import resolve_explicit_scale
from draftwright.layout_safety import candidate_safety_evidence
from draftwright.layout_selection import (
    choose_pre_render_profile,
    lost_required_derived_view_reservations,
    select_best_annotation_layout,
)
from draftwright.linting import LintIssue
from draftwright.linting.coverage import lint_axial_coverage
from draftwright.model import (
    Datum,
    DeclarationIdentity,
    Feature,
    PartModel,
    StepFeature,
    build_pmi_features,
)
from draftwright.progress import activity, build_operation, observed_stage
from draftwright.projection import (
    _ISO_MAX_GROW,
    _bbox_within,
    _clear_iso_translation,
    _fit_iso_view,
    _largest_clear_factor,
    _project_iso,
)
from draftwright.recognition_cache import RecognitionCache
from draftwright.view_plan import (
    ARRANGEMENTS,
    PRINCIPAL_VIEW_NAMES,
    ViewConstraints,
    resolve_from_analysis,
    third_angle_view_names,
)

# A view centre must move by more than this (mm) for the measure-and-repack
# pass to re-assemble.  Below it, the estimate already matched the measured
# footprint and pass 1 stands (the common, non-ballooned case).
_REPACK_TOL = 0.75
_REPACK_MAX_ITER = 3
_ISO_GROWTH_CLEARANCE_MM = 5.0


def _automatic_turned_principals(analysis: Analysis) -> tuple[str, ...] | None:
    """Return the conventional profile + end views for an automatically planned turned part.

    A body of revolution needs one longitudinal/profile projection, not the two repeated
    radial projections in the default third-angle set.  It still keeps its end view: radial
    holes, patterns, flats, threads and similar details may require it.  Which named views
    play those roles depends on the turning axis, so this decision is semantic rather than a
    hard-coded "drop plan" rule.

    This only proposes a candidate.  ``build_drawing`` checks the model's approved dimensions
    before projection and rejects a finished candidate with a required placement loss,
    structural error, or absent-view annotation; an asymmetric feature can therefore veto the
    reduction without making every common turned part compile twice.
    """
    axis = getattr(getattr(analysis, "prof", None), "axis", None)
    if axis is None and getattr(analysis, "is_rotational", False):
        axis = getattr(analysis, "od_axis", None)
    if not isinstance(axis, str):
        return None
    required = {
        "x": frozenset(("front", "side")),
        "y": frozenset(("front", "side")),
        "z": frozenset(("front", "plan")),
    }.get(axis)
    if required is None:
        return None
    return tuple(name for name in third_angle_view_names() if name in required)


def _validate_authored_view_layout(dwg: Drawing, constraints) -> None:
    """Apply the hard, non-relaxing half of ADR 2 (was 0018)'s authored layout contract.

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
        relation.validate(bounds(relation.subject), bounds(relation.reference))

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
    if region[2] <= region[0] or region[3] <= region[1]:
        # The zone the engine composed has no room at all — the `_largest_empty_rect` sliver
        # case. Reporting that as an infeasible *authored* scale states a falsehood
        # about the caller's input: no ordinary authored scale fits a zone of zero extent.
        #
        # Scope, stated because the sibling guard in `projection` covers more: this path builds
        # `region` from the RAW `a.iso_*_limit` and applies no section bump, so a zone inverted
        # by a section crossing `iso_right_limit` is invisible here. Sharing one region helper
        # between the two paths would close that; it is a wider change than this fix.
        _log.warning(
            "Iso zone %.3f x %.3f mm has no room for any scale; leaving the authored iso as projected",
            region[2] - region[0],
            region[3] - region[1],
        )
        return bb
    if not _bbox_within(bb, region):
        if not getattr(a, "planned_iso_scale_authored", True):
            ratios = [
                available / extent
                for extent, available in (
                    (a.ISO_X - bb[0], a.ISO_X - region[0]),
                    (bb[2] - a.ISO_X, region[2] - a.ISO_X),
                    (a.ISO_Y - bb[1], a.ISO_Y - region[1]),
                    (bb[3] - a.ISO_Y, region[3] - a.ISO_Y),
                )
                if extent > 0
            ]
            relative = math.floor(min(ratios, default=1.0) * 0.98 * 10000) / 10000
            if relative > 0:
                _project_iso(dwg, a, a.SCALE * a.planned_iso_scale * relative)
                return _iso_bbox(dwg)
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
    if not getattr(a, "planned_iso_scale_authored", True):
        # Staggered-side starts the orientation view at 65% so annotations are
        # placed against a safe initial obstacle. Once their ink is settled, use
        # the remaining zone instead of leaving that temporary size as a cap.
        # A detail view is defining content outside the composed iso zone. It
        # limits growth to sheet scale and participates in the obstacle search;
        # it must not freeze the temporary 65% seed as the delivered size.
        # The search transforms the already measured view footprint about its projection
        # origin and stops at either the zone boundary or an annotation.
        ratios = [
            available / extent
            for extent, available in (
                (a.ISO_X - bb[0], a.ISO_X - region[0]),
                (bb[2] - a.ISO_X, region[2] - a.ISO_X),
                (a.ISO_Y - bb[1], a.ISO_Y - region[1]),
                (bb[3] - a.ISO_Y, region[3] - a.ISO_Y),
            )
            if extent > 0
        ]
        initial = a.planned_iso_scale
        has_detail = any(name.startswith("detail_") for name in dwg.views)
        ceiling = min(
            1.0 if has_detail else _ISO_MAX_GROW,
            initial * min(ratios, default=1.0) * 0.90,
        )
        if ceiling > initial * 1.05:
            # A bare non-overlap test can leave the iso almost touching a GD&T
            # frame (0.24 mm on CTC01). Reserve visible air around annotation ink
            # and the other view outlines before probing any larger projection.
            growth_obstacles = [_inflate_box(box, _ISO_GROWTH_CLEARANCE_MM) for box in obstacles]
            growth_obstacles.extend(
                _inflate_box(dwg.view_bounds(name), _ISO_GROWTH_CLEARANCE_MM)
                for name in dwg.views
                if name != "iso"
            )
            if has_detail:
                # A defining detail may block growth at the *composed* iso centre
                # while another part of the same free zone can hold a sheet-scale
                # orientation view. Try that measured footprint before accepting a
                # smaller NTS view. Never move the detail or relax its clearance.
                _project_iso(dwg, a, a.SCALE)
                target_box = _iso_bbox(dwg)
                shift = _clear_iso_translation(target_box, region, growth_obstacles)
                if shift is not None:
                    moved = replace(a, ISO_X=a.ISO_X + shift[0], ISO_Y=a.ISO_Y + shift[1])
                    _project_iso(dwg, moved, a.SCALE)
                    settled = _iso_bbox(dwg)
                    if _bbox_within(settled, region) and not any(
                        _boxes_overlap(settled, obstacle) for obstacle in growth_obstacles
                    ):
                        return None
                _project_iso(dwg, a, a.SCALE * initial)
            clear = _largest_clear_factor(
                dwg, a, ceiling, growth_obstacles, bb, lo=initial, region=region
            )
            factor = math.floor(clear * 10000) / 10000
            # Project once at the selected scale, even if the gain is too small to use.
            factor = factor if factor > initial * 1.05 else initial
            _project_iso(dwg, a, a.SCALE * factor)
            if abs(factor - 1.0) < 1e-6:
                return None  # no NTS caption at the actual sheet scale
            return _iso_bbox(dwg)
    return bb


def _cross_view_overlaps(dwg) -> int:
    """Count annotation footprints from *different* views that lack clearance.

    A label owns the drafting preset's external text padding on every side.  Waiting until
    two glyph boxes literally overlap leaves technically non-intersecting text with no visible
    air between neighbouring view blocks (#1262).  Bare line-work still has no clearance band:
    extension/leader lines crossing between views is normal drafting.

    This is the repack trigger: a clean sheet (no cross-view conflict) is left
    exactly as pass 1 placed it, so well-estimated parts stay byte-identical;
    only a sheet with a real collision is re-packed (ADR 2 (was 0004)).
    """
    items = list(_attribute_annotations(dwg))
    clearance = _annotation_clearance(dwg)
    n = 0
    for i in range(len(items)):
        _, vi, bi, li = items[i]
        for j in range(i + 1, len(items)):
            _, vj, bj, lj = items[j]
            # Only a collision involving a text label matters — two bare lines
            # (extension/leader) crossing between views is normal drafting.
            if vi == vj or not (li or lj):
                continue
            padded_bi = _inflate_box(bi, clearance if li else 0.0)
            padded_bj = _inflate_box(bj, clearance if lj else 0.0)
            if min(padded_bi[2], padded_bj[2]) > max(padded_bi[0], padded_bj[0]) and min(
                padded_bi[3], padded_bj[3]
            ) > max(padded_bi[1], padded_bj[1]):
                n += 1
    return n


def _annotation_view_overlaps(dwg, a) -> int:
    """Count labels that lack clearance from a **different** view's geometry.

    The label box is inflated by the drafting preset's external text padding, so a dimension
    or callout that merely grazes a neighbouring view also triggers measured repacking.  This
    is the generic inter-view clearance policy; feature renderers do not know their neighbours.

    This catches a dimension that has grown into a neighbouring view's
    line-work (the staggered step chain bumping the plan view above the front
    view). A third repack trigger besides cross-annotation overlap and page
    overflow: the measured blocks already capture the annotation's real depth, so
    a repack lifts the neighbouring view clear into the headroom (#293). Bare
    extension/leader lines crossing a view are normal drafting and don't count —
    only a text label landing on another view's geometry does.
    """
    geom = _view_geom(a)
    boxes = {v: (cx - hw, cy - hh, cx + hw, cy + hh) for v, (cx, cy, hw, hh) in geom.items()}
    clearance = _annotation_clearance(dwg)
    n = 0
    for _name, v, bb, label in _attribute_annotations(dwg):
        if not label:
            continue
        bb = _inflate_box(bb, clearance)
        for ov, gb in boxes.items():
            if ov == v:
                continue
            if min(bb[2], gb[2]) > max(bb[0], gb[0]) and min(bb[3], gb[3]) > max(bb[1], gb[1]):
                n += 1
                break
    return n


def _annotation_clearance(dwg) -> float:
    """External text clearance used by measured view-block composition."""

    draft = getattr(dwg, "draft", None)
    if draft is not None:
        return float(draft.pad_around_text)
    # Pure layout tests use a deliberately tiny Drawing stand-in.  Keep their policy identical
    # to a real default drawing without making every stand-in reproduce the drafting facade.
    return float(draft_preset(font_size=_FONT_SIZE, decimal_precision=1).pad_around_text)


def _inflate_box(box, clearance):
    return (
        box[0] - clearance,
        box[1] - clearance,
        box[2] + clearance,
        box[3] + clearance,
    )


def _annotations_out_of_bounds(dwg, a, tol: float = BOUNDS_ROUNDOFF) -> bool:
    """True when any view-owned annotation's footprint extends past the drawable
    area — the second repack trigger besides cross-view overlap.  A ballooned
    plan view can overflow the page top (the balloon ring) without crossing
    another view, so the page must still escalate; the measure-and-repack pass
    re-sizes it because the overflowing balloons are part of the plan footprint
    (#92).  Only view-owned annotations count — those are what a repack can move
    by escalating the sheet."""
    lo_x, lo_y, hi_x, hi_y = _analysis_margins(a).bounds(a.PAGE_W, a.PAGE_H)
    for name, o in dwg.iter_annotations():
        if dwg.view_of(name) not in PRINCIPAL_VIEW_NAMES:
            continue
        # Match the lint, which tests each item's FULL bounding_box (extension
        # lines, arrowheads, leader + balloon ring) — not just the label rect —
        # so a dimension whose extension lines overrun the page is caught too.
        bb = _geom_box(o, getattr(dwg, "box_cache", None))
        if bb is None:  # fall back to the label rect, else skip
            lb = getattr(o, "label_bbox", None)
            if lb is None:
                continue
            bb = lb
        if bb[0] < lo_x - tol or bb[1] < lo_y - tol or bb[2] > hi_x + tol or bb[3] > hi_y + tol:
            return True
    return False


def _measure_blocks(dwg, a) -> dict:
    """Measure each orthographic view's *actual* annotation footprint from the
    laid-out drawing (#121, ADR 2 (was 0004) — "lay out, don't predict").

    Each view's four band depths are how far its annotations extend beyond its
    geometry box, **measured** from what the annotation passes produced — not
    estimated. Every annotation is attributed to its recorded owning view,
    and the band depth on a side is the furthest that view's
    annotations reach past the geometry edge there. Returns ``{view_name:
    ViewBlock}`` whose bands the packer can place disjoint, no ``_est_*`` needed.
    """
    geom = _view_geom(a)
    ext: dict = {v: None for v in geom}
    clearance = _annotation_clearance(dwg)
    for name, v, bb, label in _attribute_annotations(dwg):
        # A label's measured footprint includes the same external text clearance used by the
        # repack trigger.  Otherwise repack would notice the shortfall and then reproduce it.
        bb = _inflate_box(bb, clearance if label else 0.0)
        # Outward arrows and extension lines also need paper. The overflow trigger reads
        # full ink; measuring only labels could stall a repack with the arrows still off-page.
        ink = _geom_box(dwg.get_annotation(name), getattr(dwg, "box_cache", None))
        if ink is not None:
            bb = (min(bb[0], ink[0]), min(bb[1], ink[1]), max(bb[2], ink[2]), max(bb[3], ink[3]))
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
    """Wrap a caller-supplied ``model=`` (ADR 4 (was 0011)) into a :class:`PartModel`.
    A ``PartModel`` retains its authored contents while derived turned orientation is
    normalised; a sequence of features is wrapped with the part's
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
    reusable public input (ADR 4 (was 0011)) stays clean across builds."""
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
                # must not be confused with "the author chose nothing".
                authored_dimensions=tuple(authored)
                if authored is not None
                else model.authored_dimensions,
            )
        else:
            out = model
    else:
        features = list(model)
        bbox = part.bounding_box()
        turned_axes = {f.frame.axis for f in features if isinstance(f, StepFeature)}
        orientation = next(iter(turned_axes)) if len(turned_axes) == 1 else None
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
    turned_axes = {f.frame.axis for f in out.features if isinstance(f, StepFeature)}
    orientation = next(iter(turned_axes)) if len(turned_axes) == 1 else None
    if turned_axes and out.orientation != orientation:
        # PartModel.orientation is the compiler's aggregate classification, not a caller
        # override. Derive it from the complete set so list and PartModel front doors are
        # equivalent and mixed-axis declarations are order-independent.
        out = replace(out, orientation=orientation)
    _check_dimension_sources(out)
    return out


def _check_dimension_sources(model: PartModel) -> None:
    """Refuse a model that names **both** dimension sources (ADR 4 (was 0016) / #874).

    The mutual exclusion is a property of the MODEL, not of the façade that usually
    builds it: `build_drawing(part, model=…, requested=…, authored=…)` is a public
    entry point (ADR 4 (was 0011)) and could otherwise construct the state `Sheet` refuses.

    Checked against the **effective** model rather than the arguments of any one call,
    because either source can arrive two ways — as a keyword, or already carried by a
    supplied `PartModel` — and an argument-level guard sees only half of each
    combination (#921). Validating the merged result covers all four."""
    if model.requested_dimensions and model.authored_dimensions is not None:
        raise ValueError(
            "requested= augments the planner's automatic set and authored= replaces it — a "
            "model cannot have both. Drop the requested= entries into the authored set, or "
            "drop authored= to keep the automatic one."
        )


def _detect_part_model_analysis(part, *, pmi="off") -> tuple[PartModel, Analysis]:
    """Return one detected model together with the exact analysis run that produced it."""

    a = _analyse(part, title="", number="", tolerance=None, drawn_by="", out="model", pmi=pmi)
    # `_analyse` already detected and stored the model. Reuse it so this path does
    # not rerun detectors that `build_part_model` cannot take by injection.
    # A hand-built Analysis with no stored model still uses the detection fallback.
    # `Analysis.model` is `object | None` (it sits below the IR in the DAG), so the cast is
    # what says the stored value is the same PartModel `build_model` would have rebuilt.
    model = cast("PartModel", a.model if a.model is not None else build_model(a))
    return model, a


def detect_part_model(part, *, pmi="off") -> PartModel:
    """The **detected** :class:`PartModel` for *part* — feature recognition + analysis only,
    with no view projection, annotation, repack, repair, or export (ADR 4 (was 0011) #453). The cheap
    seed path behind :meth:`draftwright.Sheet.from_part`, so pure feature inspection no longer
    pays for a full drawing (nor its layout/rendering failure modes)."""

    return _detect_part_model_analysis(part, pmi=pmi)[0]


def _layout_advisory(code: str, message: str) -> LintIssue:
    """Validate the closed layout-advisory vocabulary at the lint boundary."""
    if code == "legibility_floor_breached":
        return LintIssue(severity="warning", code="legibility_floor_breached", message=message)
    if code == "page_fit_uncertain":
        return LintIssue(severity="warning", code="page_fit_uncertain", message=message)
    if code == "scale_fallback_applied":
        return LintIssue(severity="warning", code="scale_fallback_applied", message=message)
    raise ValueError(f"unknown layout advisory: {code!r}")


def _assembly_model(a, model, decorations, requested, authored) -> PartModel:
    """Attach declared-only rotational and PMI evidence to this assembly's model."""
    pm = (
        _coerce_model(model, a.part, decorations, requested, authored)
        if model is not None
        else (a.model if a.model is not None else build_model(a))
    )
    if model is not None:
        # A declared model skips detection, so a turned shaft carries no RotationalFeature —
        # and that feature is the sole driver of the turned-axis centrelines + the OD dimension
        # (rot furniture). Synthesise it from the (unconditional) analysis so a declared /
        # emitted-script turned part reproduces the detected drawing. Gated on the
        # caller not having declared one, so an explicit choice wins.
        if not any(f.kind == "rotational" for f in pm.features):
            rot = build_rotational_feature(a)
            if rot is not None:
                identities = pm.declaration_identities
                pm = replace(
                    pm,
                    features=[*pm.features, rot],
                    declaration_identities=(*identities, None) if identities else (),
                )

        # A z step declares a segment of a z-turned profile. `.step()` on a BOSS — an external
        # cylinder on a prismatic part — is a misuse of the verb, and the symptom is that the
        # declared steps leave the bulk of the part unspanned. This is a verb-misuse
        # diagnostic, not a guarantee that the height is dimensioned: whether the approved
        # measurements actually reach the page is settled downstream and reported by lint
        # (`axial_length_missing`). Guard on the tiling condition rather than a classifier
        # proxy (is_rotational / prof both have blind spots).
        # The z-turned span check reports from
        # `linting.coverage.lint_turned_profile_span`. Raising here would make
        # `generate_sheet_script` return nothing for a part whose recognised
        # profile does not tile, and an entry point that produces no script and no
        # drawing is the one failure a consumer cannot route around. The check itself is
        # unchanged in substance; only its severity and its home moved, and the annotate
        # pass's `reset_issues()` is why it cannot be recorded from here.
        # PMI (STEP AP242) is likewise detection-sourced, so a declared / emitted-script model
        # carries none. When PMI annotation is on, synthesise the same imported drafting
        # annotations detection would (render_pmi reads them off the model, gated on a.pmi_mode)
        # so a re-run reproduces the PMI dims. Gated on the caller not having declared
        # imported authored annotations, so an explicit set wins.
        def _declares_imported_pmi(feature) -> bool:
            if feature.kind in ("authored_dimension", "pmi"):
                return True
            return bool(
                getattr(feature, "source_id", "") or tuple(getattr(feature, "source_ids", ()))
            )

        if (
            a.pmi_mode == "annotate"
            and not any(_declares_imported_pmi(feature) for feature in pm.features)
            and not any(
                getattr(value, "source", "") == "ap242_pmi" and getattr(value, "source_ids", ())
                for value in pm.decorations.values()
            )
        ):
            pmi_feats = build_pmi_features(a.pmi, a.part.bounding_box())
            if pmi_feats:
                from draftwright.model.pmi_lowering import lower_ap242_dimensions

                identities = pm.declaration_identities
                identity_by_feature = {
                    id(feature): identity
                    for feature, identity in zip(pm.features, identities, strict=True)
                    if identity is not None
                }
                remapped_identities: dict[int, DeclarationIdentity] = {}

                def preserve_declaration_identity(source, replacements, _member_groups) -> None:
                    identity = identity_by_feature.get(id(source))
                    if identity is not None and replacements:
                        remapped_identities[id(replacements[0])] = identity

                lowered = lower_ap242_dimensions(
                    replace(
                        pm,
                        features=[*pm.features, *pmi_feats],
                        declaration_identities=(),
                    ),
                    feature_remap=preserve_declaration_identity if identities else None,
                )
                pm = replace(
                    lowered,
                    declaration_identities=(
                        tuple(
                            identity_by_feature.get(
                                id(feature), remapped_identities.get(id(feature))
                            )
                            for feature in lowered.features
                        )
                        if identities
                        else ()
                    ),
                )
    return pm


@observed_stage("assemble")
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
    critique_recognition_cache=None,
    reproducible=True,
    title_block_cache=None,
) -> Drawing:
    """Project the 4 views for analysis *a*, run the automatic annotation
    passes, and fit the iso.  This is pass 1 of :func:`build_drawing`; with a
    repacked analysis it is also pass 2 of the measure-and-repack loop (#121).
    *trace* is the opt-in #736 solve-trace recorder (attached to the drawing's
    build state so the annotate + finalize paths thread it), or ``None``."""
    cxs, cys, czs = a.cx * a.SCALE, a.cy * a.SCALE, a.cz * a.SCALE
    dist = a.bbox_max * a.SCALE + 100
    draft = _dimension_draft(a.text_position, a.text_orientation)
    if (a.text_position, a.text_orientation) != ("inline", "aligned"):
        _dimension_head_bounds(draft.arrow_length, draft.head_type)

    scheme_shadow = a.layout_strips.annotation_scheme_shadow_report(a.SCALE)
    pre_render_choice = choose_pre_render_profile(
        a.layout_strips,
        scheme_shadow,
        page=(a.PAGE_W, a.PAGE_H),
        views=tuple(a.planned_views or third_angle_view_names()),
        auto_dims=auto_dims,
    )
    dwg = Drawing(
        scale=a.SCALE,
        page_w=a.PAGE_W,
        page_h=a.PAGE_H,
        tb_w=a.TB_W,
        draft=draft,
        look_at=(cxs, cys, czs),
        dist=dist,
        centroid=(a.cx, a.cy, a.cz),
        out=out,
        part=a.source_part if a.source_part is not None else a.part,
        working_part=a.part,
        cyls=a.cyls,
        assembly=assembly,
        reproducible=reproducible,
    )
    dwg._build.title_block_cache = title_block_cache if title_block_cache is not None else {}
    dwg.annotation_scheme_decision = {
        "status": "shadow",
        "influenced_layout": False,
        **scheme_shadow.to_dict(),
        "pre_render_choice": pre_render_choice,
    }
    # Detect the IR here — before the auto_dims gate — so dwg.model() and feature edits
    # work even in manual mode. _auto_annotate reads this attached model rather
    # than rebuilding. On a repack this runs again on the pass-2 drawing (freshness).
    # Detected path: reuse the model _analyse already built for sizing —
    # detectors run once per build (ADR 1 (was 0008 Amdt 5)). build_model(a) remains the
    # fallback for a manually-constructed Analysis with no stored model.
    pm = _assembly_model(a, model, decorations, requested, authored)
    # A source-proven document default uses the existing title-block carrier when the caller
    # did not explicitly author one. An explicit tolerance, including the blank string, wins.
    general_tolerance_source = None
    hidden_source_annotations = (
        {id(feature) for feature in a.document_source_annotations}
        if a.document_member and a.pmi_mode != "annotate"
        else set()
    )
    if a.tolerance is None:
        defaults = [
            feature
            for feature in pm.features
            if feature.kind == "general_tolerance" and id(feature) not in hidden_source_annotations
        ]
        if len(defaults) == 1:
            general_tolerance_source = defaults[0]
            # The feature union does not expose this kind-specific attribute.
            a = replace(a, tolerance=getattr(general_tolerance_source, "designation"))  # noqa: B009

    # ADR 1 (was 0005 §2): the one build-context attachment — analysis + finished model
    # in a single typed BuildState; the compat properties on Drawing read through it.
    dwg._build.analysis = a
    # The title block's footprint is deterministic before it is drawn, and strip
    # placement must avoid it. Measured once here, at the single site that
    # fills build state, rather than let annotations/ probe the drawing for it.
    dwg._build.pending_title_block_box = _title_block_box(dwg, a)
    # A scale/view fallback is still the same build run. Preserve the exact lazy acquisition
    # rather than copying only its aggregate and orphaning provider-issued occurrence/face
    # references from their authority universe.
    dwg._build.attach_recognition(
        a.recognition,
        evidence=a.recognition_evidence,
        cache=critique_recognition_cache
        if a.recognition is None and a.recognition_evidence is None
        else None,
        ownership=a.recognition_ownership,
    )
    dwg._build.part_model = pm
    dwg._build.general_tolerance_source = general_tolerance_source
    default_finishes = [
        feature
        for feature in pm.features
        if feature.kind == "default_surface_finish"
        and id(feature) not in hidden_source_annotations
    ]
    dwg._build.default_surface_finish_source = (
        default_finishes[0] if len(default_finishes) == 1 else None
    )
    # Persist the caller's detail-view setting: on the auto_dims=False path the flag
    # reaches no pass here, but the finalize drain gates the prismatic detail
    # request on it exactly as the auto pass does.
    dwg._build.detail_view = detail_view
    # The opt-in solve-trace recorder rides BuildState like the rest of the build
    # context (or None when tracing is off), filled at this single construction site.
    dwg._build.trace = trace
    dwg._model_declared = model is not None  # ADR 4 (was 0011): gate model-driven hole render
    # A document member uses a declared model for its sealed physical inventory, but source
    # PMI within that model remains governed by the member's presentation policy.
    dwg.attach_document_context(a.document_member, hidden_source_annotations)

    # The solid this assembly projects. ADR 2 (was 0004) wants the real geometry built ONCE, but the
    # measure-and-repack loop can assemble several times. This parameter permits
    # callers to supply a stand-in for intermediate assemblies while the final one
    # projects the real solid.
    # build123d 0.11 scales about ``shape.location.position`` by default. The solids-only body
    # returned by ``_solids_body`` commonly carries the source primitive's placement as its
    # Location (for a centred Box, its minimum corner), even though its bounding box and every
    # analysis/projector coordinate are expressed in world space.  Scaling about that stored
    # location moves the rendered silhouette away from the solver's world-origin projection at
    # every non-1:1 scale.  Scale world geometry about the world origin explicitly so the view,
    # ViewCoordinates and annotation zones retain one transform.
    part_s = _scale_world(a.part if shape is None else shape, a.SCALE)

    # ADR 2 (was 0018): the views this drawing has, and where they go, come from ONE resolved plan
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
        "rear": ((cxs, cys + dist, czs), (0, 0, 1)),
    }
    dwg._build.view_plan = view_plan = resolve_from_analysis(a)
    # Principal views and the initial same-scale iso share this unchanged scaled
    # solid. Reuse its exact OCC AABB for their projected-edge envelopes only
    # within this assembly; a repack or later iso scale gets a fresh measurement.
    bounds_cache: dict[int, tuple[Shape, BoundBox]] = {}
    for spec in view_plan.of_kind("principal"):
        camera, up = _CAMERAS[spec.name]
        place = view_plan.placements[spec.name]
        dwg._add_view(
            spec.name,
            part_s,
            camera,
            up,
            (place.cx, place.cy),
            scaled=True,
            bounds_cache=bounds_cache,
        )
    dwg.view_decision = {
        "policy": "selected",
        "status": "selected",
        "chosen": tuple(view_plan.principal_names),
        "attempts": (),
    }
    if a.planned_iso:
        if a.planned_iso_scale is None:
            _project_iso(dwg, a, a.SCALE, shape_s=part_s, bounds_cache=bounds_cache)
        else:
            _project_iso(dwg, a, a.SCALE * a.planned_iso_scale)

    _diagnostics = None  # the audit ledger; filled once at the end of this function
    if auto_dims:
        # Snapshot outer_limits before _auto_annotate tightens them against the
        # initial (possibly overflowing) iso.  After _fit_iso_view rescales the
        # iso we restore all three right strips to min(original, final_iso_x_limit)
        # so each strip reflects actual final geometry, not the transient state.
        _fv_ol = a.fv_zones.right.outer_limit
        _pv_ol = a.pv_zones.right.outer_limit
        _sv_ol = a.sv_zones.right.outer_limit
        # The orchestrator returns the omission ledger rather than writing a drawing
        # private, so `annotations/` stays off the state bus. Fill it at the one site
        # below (ADR 1 (was 0005 §2)).
        _diagnostics = _auto_annotate(dwg, a, detail_view=detail_view)
        # The placed annotations are the fit's obstacles: the grow branch may not
        # invade ink that placed legally against the pre-fit iso. Computed HERE because the
        # fit sits below the occupancy model and must not own an obstacle set.
        #
        # `annotation_ink_obstacles`, NOT `strip_obstacles`: the sheet frame and zone grid are
        # registered annotations whose Compound bbox spans the page, so the raw strip set made
        # the iso overlap an "obstacle" at every factor and `--frame` disabled the fit
        # altogether — no growth, no NTS caption, fast tier green. It also broke script/CLI
        # parity, since the `auto_dims=False` branch below computes its obstacles before the
        # frame is added and so kept growing.
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
            # Mirror for the above strips: restore, then re-cap below the final iso only
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
                # clamp (`from_model`), which is a different constraint that must survive.
                # Use the same anchor guard as the initial clamp: an iso x-overlapping
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
        if a.frame:  # sheet border; auto path adds it via the orchestrator
            _add_sheet_frame(dwg, a)
        if a.zones:  # zone-grid ruler on the frame
            _add_zone_grid(dwg, a)
        _add_projection_symbol(dwg, a)
        _add_scale_note(dwg, a)
        _add_default_surface_finish(dwg, a)

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
    render_document_notes(dwg, pm, exclude=hidden_source_annotations)
    render_gear_tables(dwg, pm)

    # The audit ledger is filled at one site for both paths (ADR 1 (was 0005 §2)).
    #
    # It is not a by-product of rendering. The auto path gets it from `_auto_annotate`'s
    # return; `auto_dims=False` draws no automatic dimensions, so it compiles for the
    # diagnostics alone — the plan is discarded, only the record kept. That branch reported an
    # EMPTY ledger while the compiler really had suppressed measurements, which is precisely
    # the false confidence this surface exists to remove.
    #
    # Assign once after the branches to keep this BuildState field at one fill site.
    if _diagnostics is None:
        from draftwright.model.compiled import compile_dimensions

        # No `is not None` guard on the model: `_build.part_model` is filled unconditionally
        # above, so the guard was unreachable — and its fallback was a silent empty ledger,
        # which is the precise failure this whole surface exists to remove. If a future path
        # ever reaches here without a model, that should raise where it happens rather than
        # produce a confident "nothing was suppressed".
        _diagnostics = compile_dimensions(dwg.model()).diagnostics
    dwg._build.omissions = tuple(_diagnostics or ())
    for code, message in a.layout_advisories:
        dwg.registry.record_issue(_layout_advisory(code, message))
    return dwg


def _repack_candidates(a, scale, page):
    """The (scale, page_w, page_h, tb_w) candidates the repack may choose from,
    mirroring :func:`choose_scale`: a user-fixed scale and/or page is honoured;
    otherwise the auto ladder (smallest legible sheet first) is searched."""
    explicit_width = getattr(a, "title_block_width", None)

    def block_width(default):
        return explicit_width if explicit_width is not None else default

    if scale is not None and page is not None:
        pw, ph, tb = _parse_page(page)
        candidates = [(float(scale), pw, ph, block_width(tb))]
    elif page is not None:
        pw, ph, tb = _parse_page(page)
        candidates = [(s, pw, ph, block_width(tb)) for s in _SCALES]
    elif scale is not None:
        candidates = [
            (float(scale), pw, ph, block_width(_tb_width(pw))) for pw, ph in _PAGE_SIZES.values()
        ]
    else:
        candidates = [(s, pw, ph, block_width(tb)) for s, pw, ph, tb in _LADDER]
    # Repacking can escalate the sheet but cannot relax its authored furniture constraints.
    title_margins = getattr(a, "title_block_margins", None)
    margin = _analysis_margins(a) if title_margins is not None else a.margin
    return [
        candidate
        for candidate in candidates
        if _page_furniture_fits(
            *candidate[1:],
            margin=margin,
            title_block_margins=title_margins,
            title_block_width=explicit_width,
        )
    ]


def _needs_repack(dwg, a) -> bool:
    """True when the measured drawing still needs a compose-then-pack pass."""
    return (
        _cross_view_overlaps(dwg) != 0
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
    critique_recognition_cache=None,
    reproducible=True,
    placement_critique: _PlacementCritique | None = None,
):
    """Measure the laid-out drawing's *real* per-view annotation footprints and,
    when a view collides across views, pack the blocks disjoint — escalating the
    sheet/scale until the packed layout fits — then re-assemble (#121, ADR 2 (was 0004) —
    "lay out, don't predict"; the (scale, page) choice is the outer search whose
    fitness is *do the packed disjoint blocks fit*).

    Returns ``(a2, dwg2)`` for the repacked drawing, or ``None`` when pass 1 has
    no cross-view overlap AND nothing overflows the drawable (the common case — a
    clean sheet is left exactly as placed, so well-estimated parts stay
    byte-identical) or when the repack would change nothing (same sheet/scale and
    no view actually moves).
    """
    if not _needs_repack(dwg, a):
        prior_issues = tuple(dwg.registry.issues)
        had_advisory = any(issue.code == "page_fit_uncertain" for issue in prior_issues)
        dwg.registry.drop_issues({"page_fit_uncertain"})
        if had_advisory and placement_critique is not None:
            placement_critique.drop_page_fit_advisory(dwg, prior_issues)
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
            title_block_margins=a.title_block_margins,
            margin=_analysis_margins(a),
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
            # None.) `test_arrangement_gate` therefore forces the trigger, in the
            # same idiom the other repack tests use, and pins BOTH arrangements so the
            # assertion cannot be met by a constant.
            arrangement=a.arrangement,
            views=a.planned_views,
            include_iso=a.planned_iso,
            iso_scale_factor=a.planned_iso_scale,
            convention=a.projection_convention,
        )
        _apply_principal_view_pins(
            geometry,
            a.view_constraints,
            scale=s,
            centre=(a.cx, a.cy, a.cz),
            page=(pw, ph),
            margin=_analysis_margins(a),
            views=a.planned_views,
        )
        return geometry

    candidates = _repack_candidates(a, scale, page)
    auto_search = scale is None and page is None

    def _candidate_fits(g):
        return g.auto_fits if auto_search else g.fits

    fit = next(((c, gg) for c in candidates if _candidate_fits(gg := _geom(c))), None)
    # This search also runs on candidates the outer planner may discard. Keep
    # diagnostics on their registries; only final drawing lint is user-facing.
    repack_advisories: list[tuple[str, str]] = []
    if fit is not None:
        chosen, g = fit
    else:
        chosen = None
        # No standard ISO 5455 scale fits the measured layout. When the scale is NOT
        # pinned, bisect for the largest scale that fits on the largest candidate sheet
        # (the packed layout is monotone in scale) so we never keep an overflowing sheet
        # — mirroring choose_scale's backstop, including its two guards: honour a
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
                repack_advisories.append(
                    (
                        "scale_fallback_applied",
                        f"No standard scale fits the measured layout; using computed {lo:g}",
                    )
                )
                _log.debug(
                    "measure-repack: no standard sheet fits the measured layout; "
                    "using computed %s",
                    format_drawing_scale(lo),
                )
        if chosen is None:
            # Pinned scale, or no positive scale fits the measured blocks on this page:
            # keep the largest candidate and let lint report the overflow (as before).
            chosen = candidates[-1]
            g = _geom(chosen)
            repack_advisories.append(
                ("page_fit_uncertain", f"No sheet/scale fits the measured layout; using {chosen}")
            )
            _log.debug("measure-repack: no sheet/scale fits the measured layout; using %s", chosen)
    s, pw, ph, tb = chosen
    moved = max(
        abs(g.FV_X - a.FV_X),
        abs(g.FV_Y - a.FV_Y),
        abs(g.PV_X - a.PV_X),
        abs(g.PV_Y - a.PV_Y),
        abs(g.SV_X - a.SV_X),
        abs(g.SV_Y - a.SV_Y),
        abs(g.RV_X - a.RV_X) if "rear" in (a.planned_views or ()) else 0.0,
        abs(g.RV_Y - a.RV_Y) if "rear" in (a.planned_views or ()) else 0.0,
    )
    # Seed fit warnings yield to the measured result; retain the explicit legibility
    # advisory only at the scale for which it was evaluated.
    advisories = tuple(
        (code, message)
        for code, message in a.layout_advisories
        if code == "legibility_floor_breached" and s == a.SCALE
    ) + tuple(repack_advisories)
    # Even a sub-millimetre correction matters when ink crosses the page boundary.
    # Keep the ordinary convergence tolerance only for in-bounds content.
    if (
        s == a.SCALE
        and pw == a.PAGE_W
        and ph == a.PAGE_H
        and moved < _REPACK_TOL
        and (moved < 1e-6 or not _annotations_out_of_bounds(dwg, a))
    ):
        prior_issues = dwg.registry.issues
        dwg.registry.drop_issues({"page_fit_uncertain", "scale_fallback_applied"})
        for code, message in repack_advisories:
            dwg.registry.record_issue(_layout_advisory(code, message))
        if placement_critique is not None and dwg.registry.issues != prior_issues:
            placement_critique.discard(dwg)
        return None
    fv_zones, pv_zones, sv_zones = _build_zones(g, _analysis_margins(a), ph)
    a2 = replace(
        a,
        layout_advisories=advisories,
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
        RV_X=g.RV_X,
        RV_Y=g.RV_Y,
        rv_zones=_build_rear_zones(g, _analysis_margins(a), ph),
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
            rv_x=g.RV_X,
            rv_y=g.RV_Y,
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
        critique_recognition_cache=critique_recognition_cache,
        reproducible=reproducible,
        title_block_cache=dwg._build.title_block_cache,
    )
    return a2, dwg2


class _PlacementCritique:
    """Share one finished placement critique between build, repack, and repair gates."""

    def __init__(self):
        self._issues: weakref.WeakKeyDictionary[Drawing, tuple] = weakref.WeakKeyDictionary()

    def get(self, candidate):
        # Builder doubles retain their own lint semantics. This cache never escapes one build;
        # editable public Drawings continue to lint afresh when asked directly.
        if type(candidate) is not Drawing:
            return tuple(candidate.lint(physical=False))
        issues = self._issues.get(candidate)
        if issues is None:
            issues = tuple(candidate.lint(physical=False))
            self._issues[candidate] = issues
        return issues

    def remember(self, drawing: Drawing, issues) -> None:
        self._issues[drawing] = tuple(issues)

    def discard(self, drawing: Drawing) -> None:
        self._issues.pop(drawing, None)

    def drop_page_fit_advisory(self, drawing: Drawing, before: tuple) -> None:
        """Keep a build-local critique after removal of that registry-only advisory."""
        cached = self._issues.get(drawing)
        after = tuple(drawing.registry.issues)
        expected = tuple(issue for issue in before if issue.code != "page_fit_uncertain")
        if (
            cached is None
            or not before
            or len(cached) < len(before)
            or len(after) != len(expected)
            or any(left is not right for left, right in zip(after, expected, strict=True))
            or any(
                left is not right
                for left, right in zip(cached[-len(before) :], before, strict=True)
            )
        ):
            self.discard(drawing)
            return
        self._issues[drawing] = (*cached[: -len(before)], *after)


@observed_stage("repack")
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
    critique_recognition_cache=None,
    reproducible=True,
    placement_critique: _PlacementCritique | None = None,
):
    """Iterate measure→repack→assemble until stable or bounded (#302)."""

    def _required_losses(candidate):
        lint = getattr(candidate, "lint", None)
        # Pure orchestration tests use lightweight drawing doubles. They have no semantic
        # diagnostics, which is equivalent to an empty loss set for this guard.
        if lint is None:
            issues = ()
        elif placement_critique is not None:
            issues = placement_critique.get(candidate)
        else:
            issues = tuple(lint(physical=False))
        blockers = list(_scale_blockers_from_issues(issues))
        blockers.extend(
            {
                "code": issue.code,
                "measurements": tuple(
                    _scale_requirement(mid) for mid in getattr(issue, "measurement_ids", ())
                ),
                "hole_requirements": (),
                "source_ids": tuple(getattr(issue, "source_ids", ())),
            }
            for issue in _replannable_losses(issues)
        )
        return collections.Counter(map(_blocker_identity, blockers))

    cur_a, cur_dwg = a, dwg
    for i in range(_REPACK_MAX_ITER):
        trace_snapshot = trace.snapshot() if trace is not None else None
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
            critique_recognition_cache=critique_recognition_cache,
            reproducible=reproducible,
            placement_critique=placement_critique,
        )
        if repacked is None:
            if _needs_repack(cur_dwg, cur_a):
                cur_dwg.registry.record_issue(
                    LintIssue(
                        severity="warning",
                        code="layout_repack_stalled",
                        message=f"Measured repack stalled after {i} iterations with residual layout triggers",
                    )
                )
                if placement_critique is not None:
                    placement_critique.discard(cur_dwg)
                _log.debug(
                    "measure-repack: stalled after %d iteration(s) with residual layout triggers",
                    i,
                )
            return (cur_a, cur_dwg) if i else None
        next_a, next_dwg = repacked
        introduced = _required_losses(next_dwg) - _required_losses(cur_dwg)
        if introduced:
            if trace is not None:
                trace.restore(trace_snapshot)
            return (cur_a, cur_dwg) if i else None
        cur_a, cur_dwg = next_a, next_dwg

    if _needs_repack(cur_dwg, cur_a):
        cur_dwg.registry.record_issue(
            LintIssue(
                severity="warning",
                code="layout_repack_stalled",
                message=f"Measured repack reached its {_REPACK_MAX_ITER} iteration limit with residual layout triggers",
            )
        )
        if placement_critique is not None:
            placement_critique.discard(cur_dwg)
        _log.debug(
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
    options: BuildOptions,
    *,
    scale: float | None,
    page: str | tuple | None,
    _analysis_base: Analysis | None = None,
    _analysis_sink: Callable[[Analysis], None] | None = None,
    _critique_recognition_cache=None,
    _arrangements: tuple[str, ...] | None = None,
    _select_automatic_views: bool = False,
    _candidate_profile_first: bool = False,
    _title_block_cache=None,
    _placement_critique: _PlacementCritique | None = None,
) -> Drawing:
    return build_once(
        step_file,
        options,
        scale=scale,
        page=page,
        _analysis_base=_analysis_base,
        _analysis_sink=_analysis_sink,
        _critique_recognition_cache=_critique_recognition_cache,
        _arrangements=_arrangements,
        _select_automatic_views=_select_automatic_views,
        _candidate_profile_first=_candidate_profile_first,
        _title_block_cache=_title_block_cache,
        _placement_critique=_placement_critique,
        _analyse=_analyse,
        _coerce_model=_coerce_model,
        _automatic_turned_principals=_automatic_turned_principals,
        _resolve_trace=_resolve_trace,
        _assemble=_assemble,
        _repack_to_fixed_point=_repack_to_fixed_point,
        choose_pre_render_profile=choose_pre_render_profile,
        lost_required_derived_view_reservations=lost_required_derived_view_reservations,
    )


_build_drawing_once.__doc__ = build_once.__doc__


#: Every symptom that may make the optional isometric yield, in the order the gate tests
#: them, and the vocabulary of the `remove_optional_iso` attempt status.
#:
#: **ADR 2 invariant 13 is about this tuple.** The isometric yields only to preserve
#: manufacturing completeness, and these are the three ways a drawing can fail to be
#: complete: a turned part missing an axial station, an authored requirement that cannot
#: place, and a required dimension with nowhere to go. Adding a fourth is an amendment to
#: that record — maintainer's sign-off, the record updated, and only then this tuple.
#: `test_adr0018_view_selection.py::TestWhatMakesTheIsometricYield` fails if it changes.
_ISO_YIELD_TRIGGERS = (
    "axial_coverage_incomplete",
    "required_outcome_dropped",
    "required_dimension_withheld",
)


_AUTOMATIC_UPSCALE_TRIAL_LIMIT = 2
# A validity probe is a complete drawing compile, not a cheap scalar fit.  Sample the
# nearest ISO 5455 scale in each direction, then spend the existing standard-page tail;
# probing a second scale on both sides made complex AP242 script generation perform four
# extra full compiles before reaching the same page verdict.
_AUTOMATIC_VALIDITY_SCALE_TRIAL_LIMIT = 1


@dataclass
class _AutomaticScaleTrials:
    """Bounded page and scale probes for one automatic drawing plan."""

    build: Callable[..., Drawing]
    record_attempt: Callable[..., None]
    qualify: Callable[..., tuple]
    retain_arrangement: Callable[[Drawing], Drawing]
    current_drawing: Callable[[], Drawing]
    latest_analysis: Callable[[], Analysis | None]
    settled_arrangement: str
    settled_principal_views: tuple[str, ...]
    original_page: tuple[float, float]
    scale_candidates: dict[float, Drawing] = field(default_factory=dict)
    scale_failures: dict[float, Exception] = field(default_factory=dict)

    def try_larger_standard_pages(
        self,
        starting_page,
        *,
        include_iso,
        reason,
        fallback_views,
        require_axial_coverage,
        allow_recovery_detail=False,
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
        larger_pages = standard_pages[original_index + 1 :] if original_index is not None else ()
        for page_name, page_dimensions in larger_pages:
            try:
                larger = self.build(
                    None,
                    arrangements=(self.settled_arrangement,),
                    views=self.settled_principal_views,
                    include_iso=include_iso,
                    page_override=page_name,
                    retry_reason=reason,
                )
            except (ValueError, Standard_Failure) as exc:
                if not _is_expected_candidate_build_failure(exc):
                    raise
                _log.info(
                    "automatic page escalation to %s rejected (candidate build failed: %s)",
                    page_name,
                    exc,
                )
                self.record_attempt(
                    None,
                    "error",
                    reason=reason,
                    views=fallback_views,
                    page=page_dimensions,
                    error=str(exc),
                )
                continue
            larger = self.retain_arrangement(larger)
            issues, blockers, rejection = self.qualify(
                larger,
                require_axial_coverage=require_axial_coverage,
                allow_recovery_detail=allow_recovery_detail,
            )
            if rejection is None:
                self.record_attempt(larger.scale, "complete", reason=reason, candidate=larger)
                return larger, issues
            self.record_attempt(
                larger.scale,
                "rejected",
                blockers,
                reason=reason,
                rejection=rejection,
                violations=_layout_issue_records(_hard_layout_issues(issues)),
                candidate=larger,
            )
        return None, None

    def try_scales_on_selected_page(self, candidate_scales, *, reason, require_axial_coverage):
        """Try a bounded scale sequence on the already selected sheet."""
        for candidate_scale in candidate_scales:
            analysis = self.latest_analysis()
            if (
                analysis is not None
                and hasattr(analysis, "bb")
                and _principal_view_exceeds_page(
                    candidate_scale,
                    self.original_page,
                    analysis.bb,
                    self.settled_principal_views,
                )
            ):
                self.record_attempt(
                    candidate_scale,
                    "skipped",
                    reason=reason,
                    rejection="principal_view_exceeds_page",
                    page=self.original_page,
                )
                continue
            candidate_drawing = self.scale_candidates.get(candidate_scale)
            failure = self.scale_failures.get(candidate_scale)
            if candidate_drawing is None and failure is None:
                try:
                    candidate_drawing = self.build(
                        candidate_scale,
                        arrangements=(self.settled_arrangement,),
                        views=self.settled_principal_views,
                        page_override=self.original_page,
                        retry_reason=reason,
                    )
                except (ValueError, Standard_Failure) as exc:
                    if not _is_expected_candidate_build_failure(exc):
                        raise
                    self.scale_failures[candidate_scale] = exc
                    failure = exc
                else:
                    candidate_drawing = self.retain_arrangement(candidate_drawing)
                    self.scale_candidates[candidate_scale] = candidate_drawing
            if failure is not None:
                _log.info(
                    "%s %s:1 rejected (candidate build failed: %s)",
                    reason,
                    candidate_scale,
                    failure,
                )
                self.record_attempt(
                    candidate_scale,
                    "error",
                    reason=reason,
                    views=self.current_drawing().views,
                    page=self.original_page,
                    error=str(failure),
                )
                continue
            assert candidate_drawing is not None
            assert (candidate_drawing.page_w, candidate_drawing.page_h) == self.original_page
            issues, blockers, rejection = self.qualify(
                candidate_drawing,
                require_axial_coverage=require_axial_coverage,
            )
            if rejection is None:
                self.record_attempt(
                    candidate_scale, "complete", reason=reason, candidate=candidate_drawing
                )
                return candidate_drawing, issues
            self.record_attempt(
                candidate_scale,
                "rejected",
                blockers,
                reason=reason,
                rejection=rejection,
                violations=_layout_issue_records(_hard_layout_issues(issues)),
                candidate=candidate_drawing,
            )
        return None, None

    def try_larger_scales_on_selected_page(
        self, starting_scale, *, reason, require_axial_coverage
    ):
        """Try larger scales before spending a sheet or optional view (#1338)."""
        candidate_scales = sorted(item for item in _SCALES if item > starting_scale)[
            :_AUTOMATIC_UPSCALE_TRIAL_LIMIT
        ]
        return self.try_scales_on_selected_page(
            candidate_scales,
            reason=reason,
            require_axial_coverage=require_axial_coverage,
        )

    def try_validity_scales_on_selected_page(
        self, starting_scale, *, reason, require_axial_coverage
    ):
        """Try nearby smaller, then larger scales for a hard sheet-validity defect."""
        smaller = [item for item in _SCALES if item < starting_scale][
            :_AUTOMATIC_VALIDITY_SCALE_TRIAL_LIMIT
        ]
        larger = sorted(item for item in _SCALES if item > starting_scale)[
            :_AUTOMATIC_VALIDITY_SCALE_TRIAL_LIMIT
        ]
        return self.try_scales_on_selected_page(
            (*smaller, *larger),
            reason=reason,
            require_axial_coverage=require_axial_coverage,
        )


@dataclass
class _BuildAttemptContext:
    """Mutable state shared by the bounded attempts for one drawing request."""

    step_file: str | Path | Shape
    options: BuildOptions
    post_build: Callable[[Drawing], Drawing] | None
    analysis_base: Analysis | None
    analysis_sink: Callable[[Analysis], None] | None
    title_block_cache: dict[tuple, tuple] = field(default_factory=dict)
    build_attempt: int = 0
    latest_analysis: Analysis | None = None
    critique_recognition_cache: RecognitionCache | None = None
    built_arrangement: str = ARRANGEMENTS[0]
    placement_critique: _PlacementCritique | None = None

    def __post_init__(self) -> None:
        self.placement_critique = _PlacementCritique() if self.post_build is None else None

    def finish_annotation_layout(self, drawing: Drawing) -> Drawing:
        if self.options.annotation_layout == "demand-guided":
            # A declared build leaves physical critique lazy until critique or export.
            if self.options.model is not None:
                safety_evidence = {
                    "version": 12,
                    "checks_passed": False,
                    "admission_ready": False,
                    "failed_checks": ["physical_critique_deferred"],
                    "checks": [],
                    "page": [float(drawing.page_w), float(drawing.page_h)],
                    "scale": float(drawing.scale),
                    "status": "deferred_until_physical_critique",
                }
            else:
                safety_evidence = candidate_safety_evidence(drawing)
            drawing.annotation_scheme_decision = {
                **drawing.annotation_scheme_decision,
                "safety_evidence": safety_evidence,
                "fallback_decision": "not_evaluated",
            }
        return drawing

    def placement_issues(self, candidate):
        # A post-build hook may edit the sheet, and test doubles retain their lint semantics.
        if self.placement_critique is None:
            return tuple(candidate.lint(physical=False))
        return self.placement_critique.get(candidate)

    def retain_analysis(self, value: Analysis) -> None:
        # Reuse the first geometry analysis across attempts, but report the latest
        # arrangement to the arrangement gate through this analysis sink.
        self.built_arrangement = value.arrangement
        self.latest_analysis = value
        if self.analysis_base is None:
            self.analysis_base = value
        if self.analysis_sink is not None:
            self.analysis_sink(value)

    def build(
        self,
        candidate_scale: float | None,
        arrangements: tuple[str, ...] | None = None,
        views: tuple[str, ...] | None = None,
        include_iso: bool | None = None,
        page_override: str | tuple | None = None,
        select_automatic_views: bool = False,
        retry_reason: str = "initial",
    ) -> Drawing:
        self.build_attempt += 1
        # Rebuilds keep the requested topology unless a retry explicitly changes it.
        views = self.options._views if views is None else views
        if arrangements is None and not self.options.auto_dims:
            # With no compiled dimensions, an alternative arrangement has no
            # placement evidence to prove it safe for deferred annotations.
            arrangements = (ARRANGEMENTS[0],)
        if self.build_attempt > 1:
            activity(
                "retry",
                reason=retry_reason,
                attempt=self.build_attempt,
                scale=candidate_scale,
                page=str(self.options.page if page_override is None else page_override),
            )
        attempt_options = replace(
            self.options,
            _views=views,
            _include_iso=self.options._include_iso if include_iso is None else include_iso,
        )
        with suspend_finished_build_lint():
            built = _build_drawing_once(
                self.step_file,
                attempt_options,
                scale=candidate_scale,
                page=self.options.page if page_override is None else page_override,
                _analysis_base=self.analysis_base,
                _analysis_sink=self.retain_analysis,
                _critique_recognition_cache=self.critique_recognition_cache,
                _arrangements=arrangements,
                _select_automatic_views=select_automatic_views,
                _candidate_profile_first=self.options.annotation_layout == "demand-guided",
                _title_block_cache=self.title_block_cache,
                _placement_critique=self.placement_critique,
            )
            _validate_authored_view_layout(built, self.options._view_constraints)
            if self.options._document_input is not None:
                cast(DocumentInput, self.options._document_input).validate(
                    built.working_part, built.model().features
                )
            return self.post_build(built) if self.post_build is not None else built

    def scale_blockers_for(
        self, built: Drawing, expected_scale: float
    ) -> tuple[tuple[dict, ...], bool]:
        found = _scale_blockers(built)
        # A post-build hook may change representation, and a fallback may change
        # effective scale. Neither proves the next scale rung should be skipped.
        short_off_axis_span = (
            self.post_build is None
            and built.scale == expected_scale
            and _short_off_axis_span_blocks_smaller_scales(found)
        )
        if self.critique_recognition_cache is None:
            evidence_reader = getattr(built, "recognition_evidence", None)
            self.critique_recognition_cache = RecognitionCache(
                result=built.recognition(),
                evidence=evidence_reader() if callable(evidence_reader) else None,
            )
        return found, short_off_axis_span

    def automatic_assessment(self, candidate):
        # Share one recognition-free critique between the builder verdicts.
        issues = self.placement_issues(candidate)
        return issues, _scale_blockers_from_issues(issues)

    def principal_names(self, candidate):
        plan = getattr(candidate, "view_plan", None)
        if plan is not None:
            return tuple(plan.principal_names)
        available = getattr(candidate, "views", None)
        if available is None:
            return tuple(
                self.options._views
                if self.options._views is not None
                else third_angle_view_names()
            )
        return tuple(name for name in third_angle_view_names() if name in available)

    @staticmethod
    def absent_view_owners(candidate):
        result = []
        for name in candidate.annotations():
            owner = candidate.view_of(name)
            if owner is not None and owner not in candidate.views:
                result.append(name)
        return tuple(result)


def _compare_annotation_layout(options: dict, auto_dims: bool) -> Drawing:
    """Compare a candidate against the finished established layout."""
    options["annotation_layout"] = "estimated-strips"
    with use_layout_profile(AnnotationLayoutProfile()):
        baseline = build_drawing(**options)
    if not auto_dims:
        baseline.annotation_scheme_decision = {
            **baseline.annotation_scheme_decision,
            "status": "retained_baseline",
            "policy": "compare",
            "reason": "automatic_annotations_disabled",
        }
        return baseline
    candidate_options = {
        **options,
        "scale": baseline.scale,
        "page": (baseline.page_w, baseline.page_h),
        "scale_policy": "permissive",
        "_replayed_scale": None,
    }

    def build_candidate(profile: AnnotationLayoutProfile) -> Drawing:
        with use_layout_profile(profile), warnings.catch_warnings():
            warnings.simplefilter("ignore", ScaleCompletenessWarning)
            return build_drawing(**candidate_options)

    selected = select_best_annotation_layout(baseline, build_candidate)
    selected.annotation_scheme_decision = {
        **selected.annotation_scheme_decision,
        "safety_evidence": candidate_safety_evidence(selected),
    }
    if selected is not baseline:
        # The speculative build uses a fixed settled scale with permissive checks.
        # Report the caller's original scale policy and resolution on the result.
        selected.scale_decision = baseline.scale_decision
    if selected.solve_trace is not None:
        selected.solve_trace.write()
    return selected


def _replay_structural_issues(issues, allowed_crossings=()) -> tuple[LintIssue, ...]:
    """Keep every structural issue except a counted crossing from the settled drawing."""
    remaining = collections.Counter(allowed_crossings)
    retained = []
    for issue in _structural_layout_issues(issues):
        identity = (
            issue.code,
            issue.annotation_name,
            issue.view,
            issue.related_annotation_names if issue.code == "feature_leader_crossing" else (),
        )
        if (
            issue.code in {"leader_crosses_silhouette", "feature_leader_crossing"}
            and issue.severity == "info"
            and None not in identity
            and remaining[identity] > 0
        ):
            remaining[identity] -= 1
        else:
            retained.append(issue)
    return tuple(retained)


@dataclass
class _AutomaticResolution:
    """The ordered automatic view, arrangement and scale recovery ladder."""

    context: _BuildAttemptContext
    views_are_automatic: bool
    drawing: Drawing = field(init=False)
    dimensions_are_automatic: bool = False
    replanned: bool = False
    replan_attempts: list[dict] = field(default_factory=list)
    view_attempts: list[dict] = field(default_factory=list)
    view_status: str = "default"
    settled_issues: tuple | None = None
    settled_principal_views: tuple[str, ...] = ()
    arrangement_decision: dict | None = None
    settled_arrangement: str = ARRANGEMENTS[0]
    original_scale: float = 1.0
    original_page: tuple[float, float] = (0.0, 0.0)
    trials: _AutomaticScaleTrials = field(init=False)

    def initial_build(self):
        self.drawing = self.context.build(
            None,
            views=self.context.options._views,
            select_automatic_views=self.views_are_automatic
            and self.context.options._views is None,
        )
        self.dimensions_are_automatic = (
            self.context.options.auto_dims and self.drawing.model().authored_dimensions is None
        )
        self.replanned = False
        self.replan_attempts = []
        initial_view_decision = getattr(self.drawing, "view_decision", {})
        self.view_attempts = list(initial_view_decision.get("attempts", ()))
        self.view_status = initial_view_decision.get("status", "default")
        self.settled_issues = None

    def settle_views(self):
        # The reduced topology was selected after analysis but before projection, so the
        # accepted common case performs one assembly. Read back that finished candidate now.
        # A non-source placement drop, structural error, or annotation still owned by an
        # absent view vetoes it and pays for the full-view fallback. Source-owned callout
        # drops remain eligible for the established scale/page recovery below.
        self.settled_principal_views = self.context.principal_names(self.drawing)
        if self.view_status == "candidate":
            candidate_issues, candidate_blockers = self.context.automatic_assessment(self.drawing)
            absent_owners = self.context.absent_view_owners(self.drawing)
            unrecoverable_blockers = tuple(
                blocker for blocker in candidate_blockers if not blocker["source_ids"]
            )
            rejection = _automatic_candidate_rejection(candidate_issues, unrecoverable_blockers)
            if rejection is not None or absent_owners:
                proposed = self.settled_principal_views
                reason = "annotation_owned_by_absent_view" if absent_owners else rejection
                assert reason is not None
                self.view_attempts[-1] = {
                    "views": proposed,
                    "status": "rejected",
                    "reason": reason,
                    "blockers": candidate_blockers,
                    **({"annotations": absent_owners} if absent_owners else {}),
                }
                self.drawing = self.context.build(
                    None, views=third_angle_view_names(), retry_reason=reason
                )
                self.settled_principal_views = self.context.principal_names(self.drawing)
                self.view_status = "retained_after_rejection"
            else:
                self.view_attempts[-1] = {
                    "views": self.settled_principal_views,
                    "status": "chosen",
                    "reason": "redundant_radial_view_removed",
                    "blockers": candidate_blockers,
                }
                self.view_status = "reduced"
                self.settled_issues = candidate_issues
        elif self.view_status == "selected":
            self.view_status = "default"

    def settle_arrangement(self):
        if self.context.built_arrangement != ARRANGEMENTS[0]:
            # The recognition-free critique makes the arrangement independent of whether the
            # model was detected or declared. Carry the already settled view topology through
            # its fallback compile; otherwise proving the arrangement would restore a view.
            self.drawing = _preserve_requirements_under_arrangement(
                self.drawing,
                self.context.built_arrangement,
                lambda candidate_scale, arrangements: self.context.build(
                    candidate_scale,
                    arrangements,
                    views=self.settled_principal_views,
                    retry_reason="arrangement_preserve_requirements",
                ),
                lambda built: _scale_blockers_from_issues(self.context.placement_issues(built)),
            )
            # The arrangement gate may return a rebuilt preferred-layout drawing.  Issues
            # cached from the pre-gate candidate describe different placed ink and must never
            # drive the final completeness decision for that winner.
            self.settled_issues = None
        self.arrangement_decision = getattr(self.drawing, "arrangement_decision", None)
        self.settled_arrangement = (
            self.arrangement_decision["chosen"]
            if self.arrangement_decision is not None
            else self.context.built_arrangement
        )

    def retain_arrangement(self, candidate):
        # Corrective builds stay in the settled arrangement and retain its decision.
        if self.arrangement_decision is not None:
            candidate.arrangement_decision = self.arrangement_decision
        return candidate

    def record_attempt(
        self,
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
        violations=(),
    ):
        if candidate is not None:
            views = candidate.views
            page = (candidate.page_w, candidate.page_h)
        self.replan_attempts.append(
            _scale_attempt(
                scale,
                status,
                blockers,
                reason=reason,
                rejection=rejection,
                violations=violations,
                views=views,
                page=page,
                error=error,
            )
        )

    def qualify_candidate(
        self,
        candidate,
        *,
        require_axial_coverage=False,
        allow_recovery_detail=False,
        allowed_informational_crossings=(),
    ):
        """Apply the settled-drawing verdict before semantic recovery constraints."""
        issues, blockers = self.context.automatic_assessment(candidate)
        if _hard_layout_issues(issues):
            return issues, blockers, "structural_error"
        if _has_detail_view(candidate.views) and not allow_recovery_detail:
            return issues, blockers, "recovery_detail_retained"
        if require_axial_coverage:
            latest_analysis = self.context.latest_analysis
            assert latest_analysis is not None
            profile_kw = (
                {"profiles": latest_analysis.profiles}
                if hasattr(latest_analysis, "profiles")
                else {"prof": latest_analysis.prof}
            )
            if lint_axial_coverage(
                latest_analysis.part, candidate, **profile_kw
            ) or _axial_dimension_losses(issues):
                return issues, blockers, "axial_coverage_incomplete"
        if blockers:
            return issues, blockers, "required_outcome_dropped"
        structural = _replay_structural_issues(issues, allowed_informational_crossings)
        if structural:
            return issues, blockers, "structural_error"
        return issues, blockers, None

    def prepare_trials(self):
        # Every later scale/page/ISO correction is a rebuild. Carry the selected topology
        # explicitly so a successful reduced plan cannot silently revert to three principals.
        self.original_scale = self.drawing.scale
        self.original_page = (self.drawing.page_w, self.drawing.page_h)
        # A detail-bearing candidate can enter the larger-scale tail once to test whether
        # the detail reservation was conservative, then enter the identical tail again when
        # a required placement loss asks the optional ISO to yield. Reuse those finished
        # drawings: the second pass may apply a stricter qualification gate, but rebuilding
        # identical geometry cannot change its answer.
        self.trials = _AutomaticScaleTrials(
            build=self.context.build,
            record_attempt=self.record_attempt,
            qualify=self.qualify_candidate,
            retain_arrangement=self.retain_arrangement,
            current_drawing=lambda: self.drawing,
            latest_analysis=lambda: self.context.latest_analysis,
            settled_arrangement=self.settled_arrangement,
            settled_principal_views=self.settled_principal_views,
            original_page=self.original_page,
        )

    def recover_detail(self):
        # The compose-time estimate conservatively reserves an enlarged
        # detail for a crowded run.  Some larger preferred scales make that run
        # readable inline, so the detail reservation disappears and the same page
        # becomes feasible — GRM-04 is 2:1 under the estimate but complete at 5:1
        # after its Y location re-homes from side-below to plan-right.  Measure
        # those larger candidates only when the settled result actually contains
        # that semantic recovery artifact: post-build occupied rectangles are not
        # a scale-selection input.  A candidate may win only on the same sheet and
        # settled arrangement, with no recovery detail or required placement loss.
        if (
            self.dimensions_are_automatic
            and self.views_are_automatic
            and _has_detail_view(self.drawing.views)
        ):
            self.record_attempt(
                self.drawing.scale,
                "detail_reservation_conservative",
                reason="measured_upscale",
                candidate=self.drawing,
            )
            upscaled, upscaled_issues = self.trials.try_larger_scales_on_selected_page(
                self.original_scale,
                reason="measured_upscale",
                require_axial_coverage=False,
            )
            if upscaled is not None:
                self.drawing = upscaled
                self.settled_issues = upscaled_issues
                self.replanned = True
            elif self.context.options.page is None:
                _detail_issues, detail_blockers = self.context.automatic_assessment(self.drawing)
                has_source_dimensions = any(
                    getattr(feature, "kind", None) == "authored_dimension"
                    and bool(getattr(feature, "source_id", ""))
                    for feature in getattr(self.drawing.model(), "features", ())
                )
                if detail_blockers or has_source_dimensions:
                    # A detail may recover its own measurements while another required
                    # mark remains unplaced. Try the existing bounded page tail in that
                    # case too; retaining the detail is valid if the candidate passes
                    # every structural and required-outcome gate. A complete detected
                    # drawing does not spend a larger sheet just to eliminate its detail.
                    larger, larger_issues = self.trials.try_larger_standard_pages(
                        self.original_page,
                        include_iso=self.context.options._include_iso,
                        reason="page_escalation_after_detail",
                        fallback_views=tuple(self.drawing.views),
                        require_axial_coverage=False,
                        allow_recovery_detail=bool(detail_blockers),
                    )
                    if larger is not None:
                        self.drawing = larger
                        self.settled_issues = larger_issues
                        self.replanned = True

    def recover_hard_layout(self):
        # Hard validity is the first page/scale verdict tier. It opens the bounded recovery
        # ladder independently of completeness, and it never spends the optional isometric:
        # ADR 2 reserves view removal for manufacturing completeness. A clean candidate must
        # pass the same settled-drawing gate as every later correction.
        original_issues, _original_blockers = self.context.automatic_assessment(self.drawing)
        hard_layout = _hard_layout_issues(original_issues)
        if hard_layout:
            self.settled_issues = original_issues
            self.record_attempt(
                self.drawing.scale,
                "hard_layout_invalid",
                _original_blockers,
                reason="layout_validity_recovery",
                violations=_layout_issue_records(hard_layout),
                candidate=self.drawing,
            )
            recovered, recovered_issues = self.trials.try_validity_scales_on_selected_page(
                self.drawing.scale,
                reason="scale_retry_after_hard_layout",
                require_axial_coverage=False,
            )
            if recovered is None and self.context.options.page is None:
                recovered, recovered_issues = self.trials.try_larger_standard_pages(
                    self.original_page,
                    include_iso="iso" in self.drawing.views,
                    reason="page_escalation_after_hard_layout",
                    fallback_views=tuple(self.drawing.views),
                    require_axial_coverage=False,
                    allow_recovery_detail=True,
                )
            if recovered is not None:
                self.drawing = recovered
                self.settled_issues = recovered_issues
                self.replanned = True

    def recover_required_no_iso(self):
        # A required placement loss spends the bounded scale/page recovery budget
        # even when there is no optional ISO to yield. Keep the ISO-removal path
        # specialised and give other automatic plans the same scale-first,
        # page-second opportunity.
        if (
            self.dimensions_are_automatic
            and self.views_are_automatic
            and not (self.context.options._include_iso and "iso" in self.drawing.views)
        ):
            original_issues, required_blockers = self.context.automatic_assessment(self.drawing)
            self.settled_issues = original_issues
            axial_dimension_losses = _axial_dimension_losses(original_issues)
            if (required_blockers or axial_dimension_losses) and not _hard_layout_issues(
                original_issues
            ):
                self.record_attempt(
                    self.drawing.scale,
                    "required_outcome_dropped",
                    required_blockers,
                    reason="required_outcome_recovery",
                    candidate=self.drawing,
                )
                recovered, recovered_issues = self.trials.try_larger_scales_on_selected_page(
                    self.drawing.scale,
                    reason="scale_escalation_after_required_drop",
                    require_axial_coverage=bool(axial_dimension_losses),
                )
                if recovered is None and self.context.options.page is None:
                    recovered, recovered_issues = self.trials.try_larger_standard_pages(
                        self.original_page,
                        include_iso=self.context.options._include_iso,
                        reason="page_escalation_after_required_drop",
                        fallback_views=tuple(self.drawing.views),
                        require_axial_coverage=bool(axial_dimension_losses),
                        allow_recovery_detail=True,
                    )
                if recovered is not None:
                    self.drawing = recovered
                    self.settled_issues = recovered_issues
                    self.replanned = True

    def recover_optional_iso(self):
        # A pictorial view is useful context, but it cannot outrank the
        # dimensions or other required annotations needed to manufacture a part.
        # GRM-03 originally selected 2:1 with ISO, collapsed its 0.5 + 2 mm head
        # steps into an unowned 2.5 mm block, then had no room for the recovery
        # detail. Any required outcome can reach the same correction for the complementary
        # reason: all shoulders are covered, but a required annotation has no route.
        # Re-plan once without the optional ISO in either case. This is
        # deliberately a measured semantic comparison, not suppression of lint:
        # the candidate wins only after the same read-back and required-outcome
        # gates prove it complete.
        if (
            self.dimensions_are_automatic
            and self.context.options._include_iso
            and self.views_are_automatic
            and "iso" in self.drawing.views
        ):
            assert self.context.latest_analysis is not None
            profile_kw = (
                {"profiles": self.context.latest_analysis.profiles}
                if hasattr(self.context.latest_analysis, "profiles")
                else {"prof": self.context.latest_analysis.prof}
            )
            original_issues, original_blockers = self.context.automatic_assessment(self.drawing)
            original_has_axial_gap = bool(
                lint_axial_coverage(self.context.latest_analysis.part, self.drawing, **profile_kw)
                or _axial_dimension_losses(original_issues)
            )
            required_blockers = original_blockers
            self.settled_issues = original_issues
            recovered_on_selected_page = False
            # A required envelope or step dimension with no room is not a blocker
            # by design (see `_REPLANNABLE_LOSS_CODES`), but it still opens this
            # bounded recovery ladder.
            #
            # Scoped by the enclosing gate, which is worth stating so the next reader does
            # not assume otherwise: this block runs only for a drawing that HAS the optional
            # isometric. One that settled without it never replans for this symptom, however
            # starved. Widening that is a separate question from the trigger.
            withheld = _replannable_losses(original_issues)
            if original_has_axial_gap or required_blockers or withheld:
                # The recorded status names WHICH symptom opened the ladder, so the
                # decision reads back honestly, and the vocabulary is
                # `_ISO_YIELD_TRIGGERS` — the declared list ADR 2 invariant 13 is about.
                # `required_outcome_dropped` would be wrong for a withheld dimension:
                # nothing was dropped as a blocker — the mark was approved and had
                # nowhere to go.
                if original_has_axial_gap:
                    entry_status = _ISO_YIELD_TRIGGERS[0]
                elif required_blockers:
                    entry_status = _ISO_YIELD_TRIGGERS[1]
                else:
                    entry_status = _ISO_YIELD_TRIGGERS[2]
                self.record_attempt(
                    self.drawing.scale,
                    entry_status,
                    required_blockers,
                    reason="remove_optional_iso",
                    candidate=self.drawing,
                )
                # Try the bounded larger-scale tail on the selected page before
                # removing the optional ISO or trying a larger sheet. A candidate
                # wins only after the same axial and required-outcome checks.
                upscaled, upscaled_issues = self.trials.try_larger_scales_on_selected_page(
                    self.drawing.scale,
                    reason="scale_escalation_on_selected_page",
                    require_axial_coverage=True,
                )
                if upscaled is not None:
                    self.drawing = upscaled
                    self.settled_issues = upscaled_issues
                    self.replanned = True
                    recovered_on_selected_page = True
            if (
                original_has_axial_gap or required_blockers or withheld
            ) and not recovered_on_selected_page:
                self.try_without_iso()

    def try_without_iso(self):
        try:
            without_iso_proposal = self.context.build(
                None,
                arrangements=(self.settled_arrangement,),
                views=self.settled_principal_views,
                include_iso=False,
                retry_reason="remove_optional_iso",
            )
        except (ValueError, Standard_Failure) as exc:
            if not _is_expected_candidate_build_failure(exc):
                raise
            _log.info("optional-ISO replan rejected (build failed: %s)", exc)
            self.record_attempt(
                self.drawing.scale,
                "error",
                reason="remove_optional_iso",
                views=tuple(name for name in self.drawing.views if name != "iso"),
                page=self.original_page,
                error=str(exc),
            )
        else:
            proposal_page = (
                without_iso_proposal.page_w,
                without_iso_proposal.page_h,
            )
            if proposal_page != self.original_page:
                self.record_attempt(
                    without_iso_proposal.scale,
                    "scale_proposal",
                    reason="remove_optional_iso",
                    candidate=without_iso_proposal,
                )
                try:
                    without_iso = self.context.build(
                        None,
                        arrangements=(self.settled_arrangement,),
                        views=self.settled_principal_views,
                        include_iso=False,
                        retry_reason="remove_optional_iso",
                        page_override=self.original_page,
                    )
                except (ValueError, Standard_Failure) as exc:
                    if not _is_expected_candidate_build_failure(exc):
                        raise
                    _log.info(
                        "fixed-page optional-ISO replan rejected (build failed: %s)",
                        exc,
                    )
                    self.record_attempt(
                        None,
                        "error",
                        reason="remove_optional_iso",
                        views=without_iso_proposal.views,
                        page=self.original_page,
                        error=str(exc),
                    )
                    without_iso = None
            else:
                without_iso = without_iso_proposal

            if without_iso is not None:
                without_iso = self.retain_arrangement(without_iso)
                assert (without_iso.page_w, without_iso.page_h) == self.original_page
                issues, blockers, rejection = self.qualify_candidate(
                    without_iso,
                    require_axial_coverage=True,
                    allow_recovery_detail=True,
                )
                if rejection is None:
                    self.record_attempt(
                        without_iso.scale,
                        "complete",
                        reason="remove_optional_iso",
                        candidate=without_iso,
                    )
                    self.drawing = without_iso
                    self.settled_issues = issues
                    self.replanned = True
                else:
                    self.record_attempt(
                        without_iso.scale,
                        "rejected",
                        blockers,
                        reason="remove_optional_iso",
                        rejection=rejection,
                        violations=_layout_issue_records(_hard_layout_issues(issues)),
                        candidate=without_iso,
                    )
                    # Page preference is subordinate to manufacturing
                    # completeness. Once the settled no-ISO arrangement has failed
                    # on the automatically selected sheet, try only the bounded
                    # sequence of larger standard pages. Each page chooses its scale
                    # through the established fixed-page policy and must pass the
                    # same axial, structural, and required-outcome gates above. A
                    # A detail may be introduced or retained here: it is itself a
                    # semantic recovery view, and this correction must not reject a
                    # complete candidate merely because removing the optional ISO
                    # made room for that required detail.
                    if self.context.options.page is None:
                        larger, issues = self.trials.try_larger_standard_pages(
                            self.original_page,
                            include_iso=False,
                            reason="page_escalation_after_optional_iso",
                            fallback_views=tuple(
                                name for name in self.drawing.views if name != "iso"
                            ),
                            require_axial_coverage=True,
                            allow_recovery_detail=True,
                        )
                        if larger is not None:
                            self.drawing = larger
                            self.settled_issues = issues
                            self.replanned = True

    def replay_scale(self):
        if (
            self.context.options._replayed_scale is not None
            and abs(self.drawing.scale - self.context.options._replayed_scale) > 1e-12
        ):
            # Let the declared model take the complete automatic recovery path first. Most
            # replays (including detail-bearing step drawings) naturally recover the recorded
            # scale and must retain that measured history unchanged. Only a final scale drift
            # pays for one bounded rebuild under the already settled topology/arrangement.
            settled_crossings = tuple(
                (
                    issue.code,
                    issue.annotation_name,
                    issue.view,
                    issue.related_annotation_names
                    if issue.code == "feature_leader_crossing"
                    else (),
                )
                for issue in self.context.automatic_assessment(self.drawing)[0]
                if issue.code in {"leader_crosses_silhouette", "feature_leader_crossing"}
                and issue.severity == "info"
                and issue.annotation_name is not None
                and issue.view is not None
            )
            replayed = self.context.build(
                self.context.options._replayed_scale,
                arrangements=(self.settled_arrangement,),
                views=self.context.principal_names(self.drawing),
                include_iso="iso" in self.drawing.views,
                retry_reason="replay_settled_scale",
            )
            replay_issues, replay_blockers, rejection = self.qualify_candidate(
                replayed,
                require_axial_coverage=False,
                allow_recovery_detail=True,
                allowed_informational_crossings=settled_crossings,
            )
            if rejection is not None:
                raise ValueError(
                    f"settled scale replay {self.context.options._replayed_scale!r} is no longer valid: {rejection}; "
                    f"issues={[(issue.code, issue.severity) for issue in replay_issues]}"
                )
            self.record_attempt(
                replayed.scale,
                "complete",
                replay_blockers,
                reason="replay_settled_scale",
                candidate=replayed,
            )
            self.drawing = replayed
            self.settled_issues = replay_issues
            self.replanned = True

    def finish(self) -> Drawing:
        # The default record, set BEFORE the completeness pass so that pass can replace it.
        # It used to be assigned afterwards and silently overwrote whatever the pass had
        # decided, so an incomplete plan reported itself as an ordinary automatic one.
        # Completeness runs last because the arrangement gate above may return a different
        # drawing, and it is the settled drawing whose completeness matters.
        self.drawing.scale_decision = _scale_decision(
            policy="automatic",
            requested=None,
            effective=self.drawing.scale,
            status="automatic_replanned" if self.replanned else "automatic",
            attempted=tuple(
                item["scale"]
                for item in self.replan_attempts
                if item["scale"] is not None and item["status"] != "skipped"
            ),
            attempts=self.replan_attempts,
        )
        self.drawing.view_decision = {
            "policy": "automatic",
            "status": self.view_status,
            "chosen": self.context.principal_names(self.drawing),
            "attempts": tuple(self.view_attempts),
        }
        if (self.replan_attempts or self.view_attempts) and self.drawing.solve_trace is not None:
            # Every corrective candidate was a full build, and each build writes the
            # one shared trace path, so the file on disk may describe a *rejected*
            # candidate rather than the drawing returned.  The settled drawing's own
            # recorder holds the shipped build's records; give it the last write so
            # DRAFTWRIGHT_TRACE describes the drawing the caller receives.
            self.drawing.solve_trace.write()
        issues = self.settled_issues
        if issues is None:
            issues = self.context.placement_issues(self.drawing)
        return self.context.finish_annotation_layout(
            _complete_automatic_plan(self.drawing, issues=issues)
        )

    def run(self) -> Drawing:
        self.initial_build()
        self.settle_views()
        self.settle_arrangement()
        self.prepare_trials()
        self.recover_detail()
        self.recover_hard_layout()
        self.recover_required_no_iso()
        self.recover_optional_iso()
        self.replay_scale()
        return self.finish()


def _build_drawing_policy(
    step_file,
    build_options: BuildOptions,
    _post_build,
    _analysis_base,
    _analysis_sink,
) -> Drawing:
    """Resolve view, arrangement, scale and page against finished attempts."""
    context = _BuildAttemptContext(
        step_file, build_options, _post_build, _analysis_base, _analysis_sink
    )
    views_are_automatic = build_options._view_constraints is None or (
        isinstance(build_options._view_constraints, ViewConstraints)
        and build_options._view_constraints.is_automatic_only
    )
    if build_options.scale is None:
        if build_options.scale_policy != "fallback":
            raise ValueError("scale_policy applies only when an explicit scale is supplied")
        return _AutomaticResolution(context, views_are_automatic).run()
    return resolve_explicit_scale(
        build_options.scale,
        build_options.scale_policy,
        views_are_automatic=views_are_automatic,
        _views=build_options._views,
        _SCALES=_SCALES,
        _build=context.build,
        _principal_names=context.principal_names,
        _automatic_assessment=context.automatic_assessment,
        _absent_view_owners=context.absent_view_owners,
        scale_blockers_for=context.scale_blockers_for,
        finish_annotation_layout=context.finish_annotation_layout,
    )


@build_operation
def build_drawing(
    step_file: str | Path | Shape,
    out: str | None = None,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str | None = None,
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
    reproducible: bool = True,
    framed_recognition: bool = False,
    text_position: str = "inline",
    text_orientation: str = "aligned",
    _post_build: Callable[[Drawing], Drawing] | None = None,
    _required_tables=(),
    _views: tuple[str, ...] | None = None,
    _include_iso: bool = True,
    _view_constraints=None,
    _document_input=None,
    *,
    _analysis_base: Analysis | None = None,
    _analysis_sink: Callable[[Analysis], None] | None = None,
    projection_symbol: bool = True,
    #: The STEP document the geometry came from, when it is not `step_file` itself.
    #: Keyword-only: inserting it among the positional parameters would shift
    #: every later binding, which `test_existing_positional_arguments_keep_their_bindings`
    #: exists to catch — and did.
    source: str | Path | None = None,
    approved_by: str = "",
    document_type: str = "",
    sheet: str = "",
    margin_left: float | None = None,
    margin_right: float | None = None,
    margin_top: float | None = None,
    margin_bottom: float | None = None,
    title_block_width: float | None = None,
    leader_region: Literal["auto", "interior", "exterior"] = "auto",
    annotation_layout: Literal[
        "estimated-strips", "demand-guided", "compare", "baseline", "candidate-preview", "best"
    ] = "demand-guided",
    _replayed_scale: float | None = None,
) -> Drawing:
    """Build a drawing, protecting required annotations under an explicit scale.

    ``scale_policy`` applies only when ``scale`` is supplied. ``"fallback"`` (the safe
    default) retries smaller preferred ISO 5455 scales and returns the largest one with no
    required placement drop. ``"strict"`` raises :class:`ScaleIncompatibilityError` instead.
    ``"permissive"`` explicitly opts into the historical best-effort result and warns when
    it is degraded. Every returned drawing exposes the JSON-friendly decision through
    :attr:`Drawing.scale_decision`; :attr:`Drawing.scale` is the effective scale.

    Pass ``framed_recognition=True`` to opt an automatic build into the provider-owned local
    recognition frame. Raw remains the default. Other arguments and return semantics are
    unchanged from the one-pass builder.

    Third-angle layout and its matching projection symbol are the default.
    ``projection_symbol=False`` suppresses only the symbol. ``projection='first'`` places plan below
    front and side to its left, keeping the physical viewing directions unchanged.

    ``text_position="inline"|"above"`` and ``text_orientation="aligned"|"horizontal"``
    independently select dimension typography. Defaults preserve existing appearance.

    ``leader_region="auto"|"interior"|"exterior"`` controls the candidate regions for
    feature-leader labels without specifying page coordinates. ``"auto"`` preserves the
    normal shared solve, ``"exterior"`` restores the historical exterior-only inventory,
    and ``"interior"`` requires interior candidates where the feature family has proved
    them. Explicit per-feature ``side=`` constraints remain exterior.

    ``annotation_layout="compare"`` evaluates an alternative on the settled sheet and
    scale, retaining the existing layout unless finished-drawing semantic parity and
    layout quality prove a strict gain. The default ``"demand-guided"`` selects
    a profile before rendering in one build, without a comparison.
    ``"estimated-strips"`` uses the original feature-estimated reservations.
    The original spellings ``"best"``, ``"candidate-preview"``, and ``"baseline"``
    remain accepted aliases.
    """
    annotation_layout = annotation_layout_policy(annotation_layout)
    if annotation_layout == "compare":
        options = locals().copy()
        # The candidate's finished evidence is read again after its nested build returns.
        with reuse_finished_build_lint(Drawing):
            return _compare_annotation_layout(options, auto_dims)
    build_options = BuildOptions.from_mapping(locals())
    if scale is None:
        return _build_drawing_policy(
            step_file, build_options, _post_build, _analysis_base, _analysis_sink
        )
    with reuse_finished_build_lint(Drawing):
        return _build_drawing_policy(
            step_file, build_options, _post_build, _analysis_base, _analysis_sink
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
    tolerance: str | None = None,
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
    reproducible: bool = True,
    framed_recognition: bool = False,
    text_position: str = "inline",
    text_orientation: str = "aligned",
    *,
    projection_symbol: bool = True,
    #: The STEP document the geometry came from, when it is not `step_file` itself.
    #: Keyword-only: inserting it among the positional parameters would shift
    #: every later binding, which `test_existing_positional_arguments_keep_their_bindings`
    #: exists to catch — and did.
    source: str | Path | None = None,
    approved_by: str = "",
    document_type: str = "",
    sheet: str = "",
    margin_left: float | None = None,
    margin_right: float | None = None,
    margin_top: float | None = None,
    margin_bottom: float | None = None,
    title_block_width: float | None = None,
    leader_region: Literal["auto", "interior", "exterior"] = "auto",
    annotation_layout: Literal[
        "estimated-strips", "demand-guided", "compare", "baseline", "candidate-preview", "best"
    ] = "demand-guided",
) -> tuple[str, str]:
    """Generate a 4-view technical drawing from a STEP file or build123d object.

    Args:
        step_file: Path to a STEP/STP file, or a build123d ``Shape`` (e.g. a
            ``Part``, ``Solid``, or ``Compound``) to draw directly.
        out: Output path stem (default: input filename stem, or ``"drawing"``
            when a build123d object is passed).
        title: Part title for the title block (default: stem uppercased).
        number: Drawing number (e.g. ``"DWG-042"``).
        tolerance: General tolerance string (e.g. ``"ISO 2768-m"``). ``None`` (the
            default) states none: the title block says the tolerance is unspecified
            rather than inventing a manufacturing requirement the source never
            carried (#1157). ``""`` requests a blank cell.
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
        reproducible: make repeated exports from the returned drawing byte-identical
            on the same Draftwright version. Off by default because canonical DXF
            ordering has a measurable export-time cost.
        framed_recognition: opt an automatic build into the provider-owned local recognition
            frame. Raw remains the default rollout path.
        leader_region: feature-leader label region policy. ``"auto"`` keeps the normal
            solver, ``"exterior"`` restores exterior-only compatibility, and
            ``"interior"`` requires interior placement where that feature family supports it.
        annotation_layout: ``"compare"`` compares finished layouts on the same sheet and scale
            and selects a candidate only when required annotations and quality are preserved.
            The default ``"demand-guided"`` chooses before rendering in one build; it
            does not establish per-drawing parity to the original layout. ``"estimated-strips"``
            uses the original planning policy. The former names remain aliases.

    Returns:
        Tuple of ``(svg_path, dxf_path)`` for the generated files.

    This is a thin wrapper: ``make_drawing(...)`` is
    ``build_drawing(...).export(formats=("svg", "dxf"))``, unpacked to a tuple.
    To add or remove annotations or add section/auxiliary views before export,
    call :func:`build_drawing` and use the returned :class:`Drawing`.
    """
    # Keep make_drawing's documented tuple while Drawing.export requires explicit
    # formats and returns a dict.
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
        source=source,
        assembly=assembly,
        material=material,
        date=date,
        revision=revision,
        company=company,
        approved_by=approved_by,
        document_type=document_type,
        sheet=sheet,
        margin_left=margin_left,
        margin_right=margin_right,
        margin_top=margin_top,
        margin_bottom=margin_bottom,
        title_block_width=title_block_width,
        frame=frame,
        projection=projection,
        projection_symbol=projection_symbol,
        text_position=text_position,
        text_orientation=text_orientation,
        zones=zones,
        reproducible=reproducible,
        framed_recognition=framed_recognition,
        scale_policy=scale_policy,
        leader_region=leader_region,
        annotation_layout=annotation_layout,
    ).export(formats=("svg", "dxf"))
    assert isinstance(_paths, dict)  # formats=... always returns the {format: path} dict
    return _paths["svg"], _paths["dxf"]
