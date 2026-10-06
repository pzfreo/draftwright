"""Imported authored PMI dimensions and included-angle corridor candidates.

The IR records carry source-authored values and proven reference geometry. This owner
lowers them into the shared annotation corridor and retains per-source outcomes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import partial
from typing import Any

from build123d_drafting.helpers import (
    DEFAULT_FONT_PATH,
    Dimension,
    Leader,
    SafeDimension,
)

from draftwright._core import (
    Analysis,
    _dim,
    _drawing_bounds,
    _frame_margins,
    _log,
    _text_size,
)
from draftwright._geometry import (
    _segment_clips_box,
    _segments_cross_or_overlap,
)
from draftwright.annotations._common import (
    CROSSABLE_TYPES,
    PRIORITY,
    CorridorCandidate,
    Escalation,
    _box_hits,
    _geom_box,
    place_strip_candidates,
    register_corridor,
    strip_free_span,
)
from draftwright.annotations._dimension_ink import DimensionInkCandidate
from draftwright.annotations.angular import AngularDimension, AngularInk
from draftwright.annotations.routed import RoutedLeader
from draftwright.linting.ink_overlap import segments_of
from draftwright.model.ir import (
    AUTHORED_DIMENSION_KINDS,
    _linear_projection_view,
    authored_dimension_target_view,
)

# Minimum half of a bore's projected diameter that can carry an in-place label.
_MIN_INPLACE_BORE_HALF_MM = 4.0


def _record_pmi_drop(ctx, ax, label, rec):
    """Record a PMI dim the layout could not place (#208).

    Previously silent (#351 PR-4a) — a PMI dim that found no strip space just
    vanished with no trace beyond a debug log line, unlike every other placer.
    Now records a warning-severity lint code plus a first-class ``Escalation``
    (ADR 2 (was 0009 Amdt 1)). No resolver remedy yet — purely additive visibility.

    *ax* is ``rec.dominant_axis`` (resolved, never ``"?"`` — see the bore-diameter
    call site). The view table differs by ``rec.pmi_kind``: a bore diameter/radius
    is placed in the view where the bore appears as a circle (Z→plan, X→side,
    Y→front — the bbox-perpendicular view), while a linear dim follows the
    dominant-axis table above (X/Z→front, Y→side primary). Conflating the two
    mislabels every dropped bore diameter/radius (#351).
    """
    selected_view = authored_dimension_target_view(
        rec.pmi_kind,
        ax,
        getattr(rec, "view", None),
        getattr(rec, "side", None),
        getattr(rec, "angular_reference", None),
        getattr(rec, "ref_pts", ()),
    )
    if selected_view is not None:
        view = selected_view
    elif rec.pmi_kind in ("diameter", "radius"):
        view = {"Z": "plan", "X": "side", "Y": "front"}.get(ax, "front")
    else:
        view = "front" if ax in ("X", "Z") else "side"
    ctx.record_issue(
        "warning",
        "pmi_dropped",
        f"PMI {label!r} not placed (no room beside the {view})",
        source=getattr(rec, "source_id", ""),
        outcome_stage="placement",
    )
    ctx.escalations.append(
        Escalation(kind="pmi", view=view, feature=rec, reason="no room beside the view")
    )


def _record_pmi_unrenderable(label, rec, *, ctx):
    """Record an authored dimension whose reference geometry can't form a witness (fewer
    than two distinct reference points, or a zero span). Distinct from
    ``pmi_dropped`` (a *placement* failure): this is a *validation* failure, so a caller
    sees a specific reason instead of a misleading "no room" — an authored dim is only
    ``pmi_dropped`` after a real candidate reaches the corridor solver and cannot fit (#562)."""
    source_id = getattr(rec, "source_id", "")
    # `error` for a source-bearing record, for the same reason as
    # `_record_unsupported_dimension_kind`: this SUPPRESSES the sibling `pmi_not_rendered`
    # error, so leaving it a warning turned a lost AP242 requirement into `passed: True`.
    # Suppressing the sibling error requires this issue to retain error severity.
    ctx.record_issue(
        "error" if source_id else "warning",
        "authored_dim_degenerate",
        f"authored dimension {label!r} has degenerate reference geometry (needs two "
        "distinct reference points spanning a nonzero distance)",
        source=source_id,
    )


def _record_pmi_no_candidate(ctx, label, rec):
    """Record authored PMI for which the renderer could not form any placement candidate."""
    source_id = getattr(rec, "source_id", "")
    ctx.record_issue(
        "error" if source_id else "warning",
        "pmi_not_rendered",
        f"authored dimension {label!r} produced no viable render candidate",
        source=source_id,
    )


def _bore_span_offsets(pmi_kind: str, value: float) -> tuple[float, float]:
    """Signed offsets from the bore centroid to each witness base point.

    The invariant is that the DRAWN LENGTH equals the LABELLED VALUE, for both kinds:

    * a ``"diameter"`` record stores the full diameter and its dimension spans the bore,
      ``(-value/2, +value/2)`` — length ``value``;
    * a ``"radius"`` record stores the radius and its dimension runs from the centre to
      the surface, ``(0, +value)`` — length ``value``.

    The predecessor returned a single half-span that the caller applied symmetrically, so
    a radius record drew ``centre ± value``: an R6 record produced a **12 mm** line
    labelled R6 (#1208). The diameter branch was correct, and the radius branch beside it
    had the same shape of error #360 fixed one line along — that fix keyed on the drafting
    category rather than the IR ``.kind``, and never questioned what radius should span.

    Keyed on the drafting dimension category, NOT ``.kind``: the #360 bug used the latter,
    so the diameter branch was dead and every diameter dim spanned ±diameter (2× wide).
    """
    if pmi_kind == "diameter":
        return (-value / 2, value / 2)
    return (0.0, value)


# PMI is pre-authored manufacturing intent from the STEP file. When a strip is over
# capacity it should survive ahead of auto-generated dims (priority 0), like declared
# GD&T. It still lives in the outer run so it does not land between size/location dims.
_PMI_SUBCHAIN = 3
_PMI_CORRIDOR_PRIORITY = PRIORITY.AUTHORED
_PMI_SLOT = 10.0  # mm — slot size for PMI dim lines in the strip


#: Categories the generic linear renderer cannot draw truthfully. The separate angular
#: candidate path admits explicit supported ray geometry via _angular_renderable. `Dimension`
#: measures a straight projected path, so a record whose value is measured on some other
#: basis renders as an annotation whose geometry contradicts its own label — a drawing that
#: asserts something false. Measured on a 1:1 sheet, value against drawn length:
#:
#:   angular       60      ->  16.0   label states degrees, geometry states millimetres
#:   curve_length  25.133  ->  16.0   arc length against its chord (57% out)
#:   curved_dist   25.133  ->  16.0   same
#:   oriented      20.0    ->  16.0   along a stated direction, not the projected axis (25% out)
#:
#: The criterion is whether the value is measured on a basis THIS RENDERER RECEIVES. It
#: draws the projected span between reference points along the dominant axis, so:
#: `linear`, `thickness` and `diameter` are that span by definition and stay. An arc length
#: is not (`curve_length`, `curved_dist`), nor is an angle (`angular`). `oriented` is a
#: straight span, but along a direction the record states and the renderer is never given.
#:
#: `radius` is a straight centre-to-surface span, so its drawn length can
#: equal its labelled value and it stays renderable.
_UNRENDERABLE_DIMENSION_KINDS = frozenset({"angular", "curve_length", "curved_dist", "oriented"})

#: Key diagnostics by measurement kind so each refusal names the correct basis.
_MEASUREMENT_BASIS = {
    "angular": "an angle in degrees",
    "curve_length": "a length along a curve",
    "curved_dist": "a distance along a curve",
    "oriented": "a distance along a stated direction",
}


def _angular_renderable(record) -> bool:
    reference = getattr(record, "angular_reference", None)
    references = tuple(getattr(record, "angular_references", ()))
    return bool(
        record.pmi_kind == "angular"
        and (reference is not None or references)
        and all(
            candidate.principal_axis in ("X", "Y", "Z")
            for candidate in ((reference,) if reference is not None else references)
        )
    )


def _authored_with_usable_references(record) -> bool:
    """Whether *record* is an authored dimension with enough geometry to draw at all.

    Shared by the renderable and refused filters so they cannot drift: a predicate added to
    one only would make a record silently NEITHER drawn nor reported.
    """
    return (
        record.kind == "authored_dimension"
        and record.value > 0
        and (
            len(record.ref_pts) >= 2
            or bool(getattr(record, "angular_references", ()))
            or (
                record.pmi_kind == "diameter"
                and (
                    bool(getattr(record, "cylindrical_refs", ()))
                    or bool(getattr(record, "circular_refs", ()))
                )
            )
        )
        and not getattr(record, "rendering_blockers", ())
    )


def _blocked_authored_dimension_records(records):
    """Source dimensions retained in typed IR but unsafe to draw from incomplete evidence."""
    return [
        record
        for record in records
        if record.kind == "authored_dimension"
        and record.value > 0
        and getattr(record, "rendering_blockers", ())
    ]


def _record_blocked_authored_dimension(ctx, rec):
    source_id = getattr(rec, "source_id", "")
    reasons = "; ".join(rec.rendering_blockers)
    ctx.record_issue(
        "error" if source_id else "warning",
        "authored_dim_source_unresolved",
        f"authored dimension {getattr(rec, 'label', '')!r} is not drawn because its source "
        f"data is unresolved: {reasons}",
        source=source_id,
        outcome_stage="validation",
    )


def _record_unsupported_dimension_kind(ctx, rec):
    """Record an authored dimension whose CATEGORY this renderer cannot draw truthfully.

    A validation outcome, not a placement one — the same distinction
    :func:`_record_pmi_unrenderable` draws, and for the same reason as #1190: an optional or
    unsupported outcome marked as a placement drop makes every scale infeasible, so an
    explicit ``scale=`` request burns the whole ladder and raises where it used to return a
    drawing.
    """
    basis = _MEASUREMENT_BASIS[rec.pmi_kind]
    reason = (
        "supported angular ink requires explicit coplanar rays in a principal view"
        if rec.pmi_kind == "angular"
        else "this renderer measures only a straight projected path"
    )
    source_id = getattr(rec, "source_id", "")
    # `error` for a source-bearing record, matching `_record_pmi_no_candidate` and the
    # three `lint_pmi_*` checks: in annotate mode a requirement that came from the AP242
    # file and is absent from the drawing is an error, and suppressing the sibling
    # `pmi_not_rendered` must not quietly downgrade it. An authored declaration with no
    # source is the author's own, and a warning.
    ctx.record_issue(
        "error" if source_id else "warning",
        "dimension_kind_unsupported",
        f"authored {rec.pmi_kind} dimension {getattr(rec, 'label', '')!r} is not drawn: it "
        f"states {basis}; {reason}",
        source=source_id,
        outcome_stage="validation",
    )


def _renderable_pmi_records(records):
    """PMI records the dimension renderer may place.

    Raw ``PmiFeature`` fallbacks can preserve unsupported AP242 records. Do not render those
    just because they happen to carry a numeric value and references; only drafting dimension
    categories belong in this placement path — and only those this renderer can actually
    draw (see :data:`_UNRENDERABLE_DIMENSION_KINDS`).
    """
    return [
        r
        for r in records
        if _authored_with_usable_references(r)
        and r.pmi_kind in AUTHORED_DIMENSION_KINDS
        and (r.pmi_kind not in _UNRENDERABLE_DIMENSION_KINDS or _angular_renderable(r))
    ]


def _unsupported_kind_records(records):
    """Authored records refused purely because of their category, so the omission can be
    reported. Deliberately not folded into :func:`_renderable_pmi_records`: a record with a
    zero value or one reference point is refused for a different reason and already has its
    own diagnostic."""
    return [
        r
        for r in records
        if _authored_with_usable_references(r)
        and r.pmi_kind in AUTHORED_DIMENSION_KINDS
        and r.pmi_kind in _UNRENDERABLE_DIMENSION_KINDS
        and not _angular_renderable(r)
    ]


def _bore_info(rec):
    """For Size_Diameter / Size_Radius records, return (bore_axis, cx, cy, cz).

    bore_axis is the bbox's LONGEST extent (the bore's depth direction).
    Reuses rec.dominant_axis set by extract_pmi; falls back to re-sorting
    the bbox spans only when dominant_axis is '?' (degenerate bbox).
    The diameter/radius is then placed perpendicular to the bore axis in the
    view where the bore appears as a circle.  Returns None if ref_bbox absent.
    """
    cylinders = tuple(getattr(rec, "cylindrical_refs", ()))
    if cylinders:
        axes = {reference.principal_axis for reference in cylinders}
        if len(axes) != 1 or "?" in axes:
            return None
        first_origin = cylinders[0].axis_origin
        if any(
            any(
                abs(left - right) > 0.01
                for left, right in zip(first_origin, ref.axis_origin, strict=True)
            )
            for ref in cylinders[1:]
        ):
            # Distinct parallel cylinders may belong to a canonical pattern, but once that
            # correlation fails their centroid is not a referenced surface. Never invent a
            # leader target between them.
            return None
        centres = tuple(reference.midpoint for reference in cylinders)
        return (
            next(iter(axes)),
            sum(point[0] for point in centres) / len(centres),
            sum(point[1] for point in centres) / len(centres),
            sum(point[2] for point in centres) / len(centres),
        )

    circles = tuple(getattr(rec, "circular_refs", ()))
    if circles:
        axes = {reference.principal_axis for reference in circles}
        if len(axes) != 1 or "?" in axes:
            return None
        # A semantic size may own a pattern of equal circles. One exact member supplies the
        # witness for the shared authored statement; averaging their centres would invent a
        # target between features. Source order makes the representative deterministic.
        representative = circles[0]
        return (next(iter(axes)), *representative.center)

    bb = rec.ref_bbox
    if bb is None:
        return None
    bore_axis = rec.dominant_axis
    if bore_axis == "?":
        xmin, ymin, zmin, xmax, ymax, zmax = bb
        spans = sorted(
            [("X", abs(xmax - xmin)), ("Y", abs(ymax - ymin)), ("Z", abs(zmax - zmin))],
            key=lambda t: t[1],
            reverse=True,
        )
        bore_axis = spans[0][0]
    cx_f = sum(p[0] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
    cy_f = sum(p[1] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
    cz_f = sum(p[2] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
    return bore_axis, cx_f, cy_f, cz_f


def _pmi_witness_from_bbox(rec, view: str, a: Analysis):
    """Witness points at authored reference stations, supported by their combined bbox.

    A bbox describes the size of the referenced faces, not the relationship between them.
    Its largest extent sent GRM-03's short axial dimensions across the circular end faces.
    ``ref_pts`` carries the proven linear stations; the bbox remains useful only for the
    transverse witness-support coordinate. Not suitable for bore diameters — use
    :func:`_bore_info` instead (#1209).

    When the record carries no ``ref_bbox`` (an authored ``Sheet.measured_dimension()`` with
    only ``ref_pts``, #562), the span is derived from the ref points — so a ref_pts-only
    dimension renders instead of silently vanishing.
    """
    FX = a.proj.front_x
    FZ = a.proj.front_z
    SX = a.proj.side_x
    SZ = a.proj.side_z
    PX = a.proj.plan_x
    PY = a.proj.plan_y
    bb = rec.ref_bbox
    if bb is None:
        pts = rec.ref_pts
        if not pts or len(pts) < 2:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        bb = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
    xmin, ymin, zmin, xmax, ymax, zmax = bb
    ax = rec.dominant_axis
    pts = rec.ref_pts
    if len(pts) < 2:
        return None

    if view == "front" and ax == "X":
        lo, hi = min(point[0] for point in pts), max(point[0] for point in pts)
        p1 = (FX(lo), FZ((zmin + zmax) / 2), 0)
        p2 = (FX(hi), FZ((zmin + zmax) / 2), 0)
        avg_t = FZ((zmin + zmax) / 2)
    elif view == "plan" and ax == "X":
        lo, hi = min(point[0] for point in pts), max(point[0] for point in pts)
        witness_y = sum(point[1] for point in pts) / len(pts)
        p1 = (PX(lo), PY(witness_y), 0)
        p2 = (PX(hi), PY(witness_y), 0)
        avg_t = PY(witness_y)
    elif view == "front" and ax == "Z":
        lo, hi = min(point[2] for point in pts), max(point[2] for point in pts)
        p1 = (FX((xmin + xmax) / 2), FZ(lo), 0)
        p2 = (FX((xmin + xmax) / 2), FZ(hi), 0)
        avg_t = FX((xmin + xmax) / 2)
    elif view == "side" and ax == "Y":
        lo, hi = min(point[1] for point in pts), max(point[1] for point in pts)
        p1 = (SX(lo), SZ((zmin + zmax) / 2), 0)
        p2 = (SX(hi), SZ((zmin + zmax) / 2), 0)
        avg_t = SZ((zmin + zmax) / 2)
    elif view == "plan" and ax == "Y":
        # AP242 may attach broad plate faces to a local Y measurement. Their
        # combined bbox can cover the whole part, so its midpoint collapses
        # distinct witness pairs onto one X station. The proved reference points
        # are the dimension's actual stations (as for plan/X above).
        witness_x = sum(point[0] for point in pts) / len(pts)
        lo, hi = min(point[1] for point in pts), max(point[1] for point in pts)
        p1 = (PX(witness_x), PY(lo), 0)
        p2 = (PX(witness_x), PY(hi), 0)
        avg_t = PX(witness_x)
    else:
        return None

    span = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    # The helper draws short dimensions with external arrows/text. Refusing them at an
    # arbitrary 3 page-mm threshold lost GRM-03's valid 0.5 mm axial station even though
    # the shared strip solve can place it without changing the measured path.
    if span <= 1e-6:
        return None
    return p1, p2, avg_t


@dataclass(frozen=True)
class _PmiDimensionBuild:
    q1: tuple[float, float, float]
    q2: tuple[float, float, float]
    side: str
    witness: float
    label: str
    draft: Any

    def __call__(self, pos: float):
        # The helper draws one extension gap back toward the witnesses; the strip
        # coordinate names the rendered dimension line and label instead.
        dist = (
            pos - self.witness + self.draft.extension_gap
            if self.side in ("above", "right")
            else self.witness - pos + self.draft.extension_gap
        )
        return _dim(self.q1, self.q2, self.side, dist, self.draft, label=self.label)


def _pmi_dim_spec(p1, p2, strip, label, name, view, side, draft, *, leader_fallback=False):
    if strip is None:
        return None
    if side in ("above", "below"):
        axis = "y"
        perp = tuple(sorted((p1[0], p2[0])))
        witness = max(p1[1], p2[1]) + 2 if side == "above" else min(p1[1], p2[1]) - 2
        q1, q2 = (p1[0], witness, 0), (p2[0], witness, 0)
    else:
        axis = "x"
        perp = tuple(sorted((p1[1], p2[1])))
        witness = max(p1[0], p2[0]) + 2 if side == "right" else min(p1[0], p2[0]) - 2
        q1, q2 = (witness, p1[1], 0), (witness, p2[1], 0)
    lo, hi, _inner = strip_free_span(strip)
    if side in ("above", "right") and hi <= witness:
        return None
    if side in ("below", "left") and lo >= witness:
        return None

    order_coord = min(perp)
    spec = {
        "name": name,
        "build": _PmiDimensionBuild(q1, q2, side, witness, label, draft),
        "strip": strip,
        "view": view,
        "side": side,
        "axis": axis,
        "perp": perp,
        "order": (_PMI_SUBCHAIN, order_coord, name),
    }
    if leader_fallback:

        def _build_at(elbow, _tip=p2, _label=label):
            return Leader(_tip, (*elbow, 0), _label, draft)

        def _build_routed(bends, elbow, _tip=p2, _label=label):
            return RoutedLeader(_tip, bends, elbow, _label, draft)

        spec.update(
            {
                "build_at": _build_at,
                "build_routed": _build_routed,
                "tip": p2,
                "label_size": _text_size(
                    label,
                    draft.font_size,
                    getattr(draft, "font_path", DEFAULT_FONT_PATH),
                    getattr(draft, "font", "Arial"),
                ),
            }
        )
    return spec


def _oblique_pmi_dim_spec(p1, p2, strip, label, name, view, side, draft):
    """Place an exact projected span parallel to its two authored witness stations."""
    if strip is None:
        return None
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        return None
    normal = (dy / length, -dx / length)
    toward = {
        "above": (0.0, 1.0),
        "below": (0.0, -1.0),
        "right": (1.0, 0.0),
        "left": (-1.0, 0.0),
    }[side]
    if normal[0] * toward[0] + normal[1] * toward[1] < 0:
        normal = (-normal[0], -normal[1])
    axis = "y" if side in ("above", "below") else "x"
    component = normal[1] if axis == "y" else normal[0]
    if abs(component) <= 1e-6:
        return None
    midpoint = (
        (p1[0] + p2[0]) / 2,
        (p1[1] + p2[1]) / 2,
    )
    coordinate = midpoint[1] if axis == "y" else midpoint[0]
    lo, hi, _inner = strip_free_span(strip)
    if side in ("above", "right") and hi <= coordinate:
        return None
    if side in ("below", "left") and lo >= coordinate:
        return None

    def _build(pos, _component=component, _coordinate=coordinate):
        distance = abs((pos - _coordinate) / _component)
        return _dim(p1, p2, side, max(distance, 1e-6), draft, label=label)

    perp = tuple(sorted((p1[0], p2[0]))) if axis == "y" else tuple(sorted((p1[1], p2[1])))
    return {
        "name": name,
        "build": _build,
        "strip": strip,
        "view": view,
        "side": side,
        "axis": axis,
        "perp": perp,
        "order": (_PMI_SUBCHAIN, min(perp), name),
    }


def _oblique_linear_specs(a: Analysis, rec, label, name, draft):
    if len(rec.ref_pts) != 2:
        return []
    first, second = rec.ref_pts
    projection_view = _linear_projection_view(rec.ref_pts)
    if projection_view == "side":
        view, zones = "side", a.sv_zones
        p1 = (a.proj.side_x(first[1]), a.proj.side_z(first[2]), 0)
        p2 = (a.proj.side_x(second[1]), a.proj.side_z(second[2]), 0)
    elif projection_view == "front":
        view, zones = "front", a.fv_zones
        p1 = (a.proj.front_x(first[0]), a.proj.front_z(first[2]), 0)
        p2 = (a.proj.front_x(second[0]), a.proj.front_z(second[2]), 0)
    elif projection_view == "plan":
        view, zones = "plan", a.pv_zones
        p1 = (a.proj.plan_x(first[0]), a.proj.plan_y(first[1]), 0)
        p2 = (a.proj.plan_x(second[0]), a.proj.plan_y(second[1]), 0)
    else:
        return []
    if rec.view is not None and rec.view != view:
        return []
    sides: tuple[str, ...]
    if rec.side is not None:
        sides = (rec.side,)
    else:
        page_dx, page_dy = p2[0] - p1[0], p2[1] - p1[1]
        sides = (
            ("right", "left", "above", "below")
            if abs(page_dy) >= abs(page_dx)
            else ("above", "below", "right", "left")
        )
    return [
        _oblique_pmi_dim_spec(p1, p2, getattr(zones, side), label, name, view, side, draft)
        for side in sides
    ]


def _leader_route_is_readable(route, owner_bounds) -> bool:
    """House drafting policy for a recovered feature leader's shaft.

    A leader may have one routing elbow before its normal short text shelf, but
    cannot double back or travel farther than its own view's scale warrants.
    These limits are our legibility policy, not an ISO-prescribed distance.
    """
    if owner_bounds is None or not 2 <= len(route) <= 3:
        return False
    diagonal = math.hypot(owner_bounds[2] - owner_bounds[0], owner_bounds[3] - owner_bounds[1])
    max_leg = max(30.0, diagonal)
    legs = tuple(zip(route, route[1:], strict=False))
    if any(math.hypot(b[0] - a[0], b[1] - a[1]) > max_leg for a, b in legs):
        return False
    if sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in legs) > 2 * max_leg:
        return False
    for axis in (0, 1):
        deltas = [b[axis] - a[axis] for a, b in legs]
        if any(delta > 1e-6 for delta in deltas) and any(delta < -1e-6 for delta in deltas):
            return False
    return True


def _sheet_leader_fallback(
    dwg,
    tip,
    view,
    build,
    routed_build=None,
    label_size=None,
    *,
    tip_for_elbow=None,
    accept_candidate=None,
):
    """Return the nearest clear leader on a bounded drawable-sheet grid.

    Adjacent strips remain authoritative. This last resort exists for the distinct
    case where every strip is full while another sheet region is unused (#1797).
    Candidates are derived from drawable fractions, never public coordinates, and
    must clear settled annotation ink plus every non-owning view's complete bounds.
    """
    tip = (tip[0], tip[1])
    page = _drawing_bounds(dwg)
    x0, y0, x1, y1 = page
    fractions = tuple(index / 10.0 for index in range(1, 10))
    other_view_boxes = [
        bounds
        for name in getattr(dwg, "views", {})
        if name != view and (bounds := dwg.view_bounds(name)) is not None
    ]
    all_views = [
        bounds
        for name in getattr(dwg, "views", {})
        if (bounds := dwg.view_bounds(name)) is not None
    ]
    owner_bounds = dwg.view_bounds(view)
    settled_labels = []
    settled_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    settled_non_crossable_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for _name, annotation in dwg.iter_annotations():
        annotation_segments = segments_of(annotation)
        settled_segments.extend(annotation_segments)
        crossable_strokes = (
            isinstance(annotation, (Dimension, SafeDimension, AngularDimension))
            or isinstance(annotation, DimensionInkCandidate)
            or type(annotation).__name__ in CROSSABLE_TYPES
        )
        if not crossable_strokes:
            settled_non_crossable_segments.extend(annotation_segments)
        label_box = getattr(annotation, "label_bbox", None)
        if label_box is not None:
            settled_labels.append(label_box)
    clearance = dwg.draft.font_size + 2.0 * dwg.draft.pad_around_text
    xs = {
        tip[0],
        *(x0 + (x1 - x0) * fraction for fraction in fractions),
        *(
            value
            for bounds in all_views
            for value in (bounds[0] - clearance, bounds[2] + clearance)
        ),
    }
    ys = {
        tip[1],
        *(y0 + (y1 - y0) * fraction for fraction in fractions),
        *(
            value
            for bounds in all_views
            for value in (bounds[1] - clearance, bounds[3] + clearance)
        ),
    }
    corridor_xs = {
        *xs,
        *(
            point[0] + offset
            for segment in settled_non_crossable_segments
            for point in segment
            for offset in (-clearance, clearance)
        ),
    }
    corridor_ys = {
        *ys,
        *(
            point[1] + offset
            for segment in settled_non_crossable_segments
            for point in segment
            for offset in (-clearance, clearance)
        ),
    }
    positions = sorted(
        ((x, y) for x in xs for y in ys if x0 < x < x1 and y0 < y < y1),
        key=lambda point: (math.hypot(point[0] - tip[0], point[1] - tip[1]), point),
    )[:128]

    def _route_blocked(route):
        route_segments = tuple(zip(route, route[1:], strict=False))
        return (
            not _leader_route_is_readable(route, owner_bounds)
            or any(
                _segment_clips_box(start, end, view_box, pad=0.0)
                for start, end in route_segments
                for view_box in other_view_boxes
            )
            or any(
                _segment_clips_box(start, end, label_box, pad=0.0)
                for start, end in route_segments
                for label_box in settled_labels
            )
            or any(
                _segments_cross_or_overlap(start, end, fixed_start, fixed_end)
                for start, end in route_segments
                for fixed_start, fixed_end in settled_non_crossable_segments
            )
        )

    for elbow in positions:
        route_tip = tip_for_elbow(elbow) if tip_for_elbow is not None else tip
        routes: list[tuple[tuple, tuple, Any]] = [((), (route_tip, elbow), build)]
        if routed_build is not None:
            nearest_xs = sorted(
                corridor_xs,
                key=lambda value: abs(value - tip[0]) + abs(value - elbow[0]),
            )[:6]
            nearest_ys = sorted(
                corridor_ys,
                key=lambda value: abs(value - tip[1]) + abs(value - elbow[1]),
            )[:6]
            route_xs = tuple(dict.fromkeys((*nearest_xs, min(corridor_xs), max(corridor_xs))))
            route_ys = tuple(dict.fromkeys((*nearest_ys, min(corridor_ys), max(corridor_ys))))
            bend_routes = [
                ((elbow[0], tip[1]),),
                ((tip[0], elbow[1]),),
                *(((route_x, tip[1]), (route_x, elbow[1])) for route_x in route_xs),
                *(((tip[0], route_y), (elbow[0], route_y)) for route_y in route_ys),
                *(
                    (
                        (route_x, tip[1]),
                        (route_x, route_y),
                        (elbow[0], route_y),
                    )
                    for route_x in route_xs
                    for route_y in route_ys
                ),
                *(
                    (
                        (tip[0], route_y),
                        (route_x, route_y),
                        (route_x, elbow[1]),
                    )
                    for route_x in route_xs
                    for route_y in route_ys
                ),
            ]
            routed = []
            for bends in bend_routes:
                route = (tip, *bends, elbow)
                if any(left == right for left, right in zip(route, route[1:], strict=False)):
                    continue
                if _route_blocked(route):
                    continue
                length = sum(
                    math.hypot(right[0] - left[0], right[1] - left[1])
                    for left, right in zip(route, route[1:], strict=False)
                )
                routed.append((length, bends, route, routed_build))
            routes.extend(item[1:] for item in sorted(routed, key=lambda item: item[0])[:96])
        selected: dict[int, tuple[tuple, Any]] = {}
        for bends, route, candidate_build in routes:
            if _route_blocked(route):
                continue
            shelf_side = 1 if elbow[0] >= route[-2][0] else -1
            selected.setdefault(shelf_side, (bends, candidate_build))
            if len(selected) == 2:
                break
        if not selected:
            continue
        for shelf_side, (bends, candidate_build) in selected.items():
            if label_size is not None:
                width, height = label_size
                gap = dwg.draft.pad_around_text
                label_box = (
                    elbow[0] + gap if shelf_side > 0 else elbow[0] - gap - width,
                    elbow[1] - height / 2.0,
                    elbow[0] + gap + width if shelf_side > 0 else elbow[0] - gap,
                    elbow[1] + height / 2.0,
                )
                if (
                    label_box[0] < x0
                    or label_box[1] < y0
                    or label_box[2] > x1
                    or label_box[3] > y1
                    or _box_hits(label_box, all_views)
                    or _box_hits(label_box, settled_labels)
                    or any(
                        _segment_clips_box(start, end, label_box, pad=0.0)
                        for start, end in settled_segments
                    )
                ):
                    continue
            try:
                candidate = candidate_build(elbow) if not bends else candidate_build(bends, elbow)
                box = _geom_box(candidate)
                label_box = getattr(candidate, "label_bbox", None)
            except Exception:  # noqa: BLE001 — one optional global candidate fails closed
                continue
            if accept_candidate is not None and not accept_candidate(candidate):
                continue
            if (
                box is None
                or label_box is None
                or box[0] < x0
                or box[1] < y0
                or box[2] > x1
                or box[3] > y1
            ):
                continue
            if _box_hits(label_box, all_views):
                continue
            if (
                _box_hits(label_box, settled_labels)
                or any(
                    _segment_clips_box(start, end, fixed_label, pad=0.0)
                    for start, end in segments_of(candidate)
                    for fixed_label in settled_labels
                )
                or any(
                    _segments_cross_or_overlap(start, end, fixed_start, fixed_end)
                    for start, end in segments_of(candidate)
                    for fixed_start, fixed_end in settled_non_crossable_segments
                )
                or any(
                    _segment_clips_box(start, end, view_box, pad=0.0)
                    for start, end in segments_of(candidate)
                    for view_box in other_view_boxes
                )
                or any(
                    _segment_clips_box(start, end, label_box, pad=0.0)
                    for start, end in settled_segments
                )
            ):
                continue
            return candidate
    return None


def _pmi_leader_spec(tip, strip, label, name, view, side, draft):
    if strip is None:
        return None
    axis = "y" if side in ("above", "below") else "x"
    perp = (tip[0], tip[0]) if axis == "y" else (tip[1], tip[1])
    order_coord = tip[0] if axis == "y" else tip[1]

    def _build(pos, _tip=tip, _axis=axis, _label=label):
        elbow = (_tip[0], pos, 0) if _axis == "y" else (pos, _tip[1], 0)
        return Leader(_tip, elbow, _label, draft)

    def _build_at(elbow, _tip=tip, _label=label):
        return Leader(_tip, (*elbow, 0), _label, draft)

    def _build_routed(bends, elbow, _tip=tip, _label=label):
        return RoutedLeader(_tip, bends, elbow, _label, draft)

    return {
        "name": name,
        "build": _build,
        "build_at": _build_at,
        "build_routed": _build_routed,
        "tip": tip,
        "label_size": _text_size(
            label,
            draft.font_size,
            getattr(draft, "font_path", DEFAULT_FONT_PATH),
            getattr(draft, "font", "Arial"),
        ),
        "strip": strip,
        "view": view,
        "side": side,
        "axis": axis,
        "perp": perp,
        "order": (_PMI_SUBCHAIN, order_coord, name),
    }


def _oblique_cylinder_leader_specs(a: Analysis, rec, label, name, draft):
    """Build solved leader candidates from one exact finite-cylinder surface witness."""
    cylinders = tuple(getattr(rec, "cylindrical_refs", ()))
    if not cylinders:
        return []
    sides: tuple[str, ...]
    reference = cylinders[0]
    dx, dy, dz = reference.axis_direction
    cx, cy, cz = reference.midpoint
    if abs(dx) <= 1e-6:
        view, zones, sides = "side", a.sv_zones, ("above", "below")
        surface = (
            a.proj.side_x(cy - dz * reference.radius),
            a.proj.side_z(cz + dy * reference.radius),
            0,
        )
    elif abs(dy) <= 1e-6:
        view, zones, sides = "front", a.fv_zones, ("above", "below")
        surface = (
            a.proj.front_x(cx - dz * reference.radius),
            a.proj.front_z(cz + dx * reference.radius),
            0,
        )
    elif abs(dz) <= 1e-6:
        view, zones, sides = "plan", a.pv_zones, ("right", "left")
        surface = (
            a.proj.plan_x(cx - dy * reference.radius),
            a.proj.plan_y(cy + dx * reference.radius),
            0,
        )
    else:
        return []
    if rec.view is not None and rec.view != view:
        return []
    if rec.side is not None:
        sides = tuple(side for side in sides if side == rec.side)
    # ``surface`` is derived by moving one radius perpendicular to the cylinder axis in
    # its containing projection plane. It is therefore an actual face witness, while the
    # leader shelf remains governed by the ordinary corridor solve.
    return [
        _pmi_leader_spec(surface, getattr(zones, side), label, name, view, side, draft)
        for side in sides
    ]


def _place_corridor_option(
    dwg,
    spec,
    feature,
    *,
    ctx,
    trace=None,
    measurement=None,
    priority=_PMI_CORRIDOR_PRIORITY,
    anchored=False,
    place_candidates=place_strip_candidates,
):
    # *trace*: a PMI dim's post-drop fallback is a standalone strip pass —
    # traced as a pass_event like the other standalone placers.
    left = place_candidates(
        dwg,
        spec["strip"],
        spec["view"],
        spec["axis"],
        [(spec["name"], spec["build"])],
        _PMI_SLOT,
        ctx=ctx,
        force=True,
        features={spec["name"]: feature},
        measurements={spec["name"]: measurement} if measurement is not None else None,
        naturals={spec["name"]: spec["natural"]} if "natural" in spec else None,
        footprints={spec["name"]: spec["footprint"]} if "footprint" in spec else None,
        valid_positions={spec["name"]: spec["valid_position"]}
        if "valid_position" in spec
        else None,
        compact_candidates={spec["name"]: spec["compact_candidates"]}
        if "compact_candidates" in spec
        else None,
        priorities={spec["name"]: priority},
        anchored={spec["name"]: anchored},
        require_clear_ink={spec["name"]},
        trace=trace,
        trace_label="pmi_fallback",
    )
    return not left


def _pmi_queue_options(
    dwg,
    ctx,
    options,
    ax,
    label,
    rec,
    *,
    place_candidates=place_strip_candidates,
    sheet_fallback=None,
):
    specs = [s for s in options if s is not None]
    if not specs:
        return False
    primary, alternates = specs[0], specs[1:]

    def _drop(nm, _alts=alternates, _ax=ax, _label=label, _rec=rec):
        for alt in _alts:
            if _place_corridor_option(
                dwg,
                alt,
                _rec,
                ctx=ctx,
                trace=ctx.trace,
                place_candidates=place_candidates,
            ):
                _log.info(
                    "PMI dim %s placed on fallback %s/%s",
                    nm,
                    alt["view"],
                    alt["side"],
                )
                return
        for option in (primary, *_alts):
            if "build_at" not in option:
                continue
            fallback = (sheet_fallback or _sheet_leader_fallback)(
                dwg,
                option["tip"],
                option["view"],
                option["build_at"],
                option["build_routed"],
                option["label_size"],
            )
            if fallback is None:
                continue
            ctx.place(fallback, nm, view=option["view"], feature=_rec)
            ctx.record_issue(
                "info",
                "pmi_sheet_fallback",
                f"{nm}: adjacent strips were full — placed in clear sheet space",
                source=_pmi_source_ids(_rec),
            )
            return
        _record_pmi_drop(ctx, _ax, _label, _rec)

    register_corridor(
        ctx,
        (primary["view"], primary["side"]),
        primary["strip"],
        primary["view"],
        primary["axis"],
        _PMI_SLOT,
        CorridorCandidate(
            name=primary["name"],
            build=primary["build"],
            order=primary["order"],
            on_place=lambda nm, _ax=ax, _label=label, _rec=rec: _log.info(
                "PMI dim %s %.3g → annotated (%s)", _ax, _rec.value, _label
            ),
            on_drop=_drop,
            priority=_PMI_CORRIDOR_PRIORITY,
            obligation_class="required",  # imported source-owned PMI, not optional ink
            force=True,
            require_clear_ink=True,
            feature=rec,
            natural=primary.get("natural"),
            footprint=primary.get("footprint"),
            valid_position=primary.get("valid_position"),
            compact_candidates=primary.get("compact_candidates"),
        ),
    )
    return True


def _pmi_front_linear(
    dwg,
    a: Analysis,
    ctx,
    rec,
    ax,
    label,
    name,
    primary,
    secondary,
    center,
    *,
    queue_options=_pmi_queue_options,
):
    """An X- or Z-dominant linear PMI dim in a principal view (the two share one shape): the
    witness spans the ref bbox; place ``[primary, secondary]`` when the perpendicular
    midpoint sits on the primary side of the view centre, else fall back to ``[secondary]``
    alone. Returns True/False placed, or ``None`` for a degenerate (no-witness) reference
    so the caller can report it as a validation failure."""
    draft = dwg.draft
    _pmi_queue_options = queue_options
    view = "plan" if ax == "X" and rec.view == "plan" else "front"
    wp = _pmi_witness_from_bbox(rec, view, a)
    if wp is None:
        return None
    p1, p2, avg = wp
    zones = a.pv_zones if view == "plan" else a.fv_zones
    if rec.side is not None:
        sides = [s for s in (primary, secondary) if rec.side == s]
        return _pmi_queue_options(
            dwg,
            ctx,
            [_pmi_dim_spec(p1, p2, getattr(zones, s), label, name, view, s, draft) for s in sides],
            ax,
            label,
            rec,
        )
    placed = False
    if avg >= center:
        placed = _pmi_queue_options(
            dwg,
            ctx,
            [
                _pmi_dim_spec(p1, p2, getattr(zones, primary), label, name, view, primary, draft),
                _pmi_dim_spec(
                    p1, p2, getattr(zones, secondary), label, name, view, secondary, draft
                ),
            ],
            ax,
            label,
            rec,
        )
    if not placed:
        placed = _pmi_queue_options(
            dwg,
            ctx,
            [
                _pmi_dim_spec(
                    p1, p2, getattr(zones, secondary), label, name, view, secondary, draft
                )
            ],
            ax,
            label,
            rec,
        )
    return placed


def _angular_specs(
    a: Analysis, reference, label, name, draft, *, side=None, implicit_degrees=False
):
    axis = reference.principal_axis
    view, to_page, zones = {
        "X": ("side", lambda p: (a.proj.side_x(p[1]), a.proj.side_z(p[2])), a.sv_zones),
        "Y": ("front", lambda p: (a.proj.front_x(p[0]), a.proj.front_z(p[2])), a.fv_zones),
        "Z": ("plan", lambda p: (a.proj.plan_x(p[0]), a.proj.plan_y(p[1])), a.pv_zones),
    }[axis]
    ink = AngularInk(
        to_page(reference.vertex),
        to_page(reference.first),
        to_page(reference.second),
        label,
        draft,
        sector=reference.sector,
        implicit_degrees=implicit_degrees,
    )
    options = []
    for index in sorted(range(2), key=lambda i: -abs(ink.bisector[i])):
        component = ink.bisector[index]
        if abs(component) < 1e-6:
            continue
        candidate_side = (("left", "right"), ("below", "above"))[index][component > 0]
        if side is not None and side != candidate_side:
            continue
        strip = getattr(zones, candidate_side)
        if strip is None:
            continue
        lo, hi, inner = strip_free_span(strip)
        natural = ink.vertex[index] + ink.minimum_radius * component
        natural = max(natural, inner) if component > 0 else min(natural, inner)

        def radius(pos, _index=index, _component=component):
            return (pos - ink.vertex[_index]) / _component

        def valid_position(pos, _radius=radius):
            value = _radius(pos)
            if value < ink.minimum_radius - 1e-9:
                return False
            x0, y0, x1, y1 = ink.footprint(max(ink.minimum_radius, value))
            return (
                x0 >= _frame_margins(a).bounds(a.PAGE_W, a.PAGE_H)[0]
                and y0 >= _frame_margins(a).bounds(a.PAGE_W, a.PAGE_H)[1]
                and x1 <= _frame_margins(a).bounds(a.PAGE_W, a.PAGE_H)[2]
                and y1 <= _frame_margins(a).bounds(a.PAGE_W, a.PAGE_H)[3]
            )

        options.append(
            {
                "name": name,
                "view": view,
                "side": candidate_side,
                "strip": strip,
                "axis": "x" if index == 0 else "y",
                "order": (_PMI_SUBCHAIN, ink.vertex[1 - index], name),
                "natural": natural,
                "valid_position": valid_position,
                "compact_candidates": lambda _original=None: ink.compact_candidates(_PMI_SLOT),
                "build": lambda pos, _radius=radius: ink.build(
                    max(ink.minimum_radius, _radius(pos))
                ),
                "footprint": lambda pos, _radius=radius: ink.footprint(
                    max(ink.minimum_radius, _radius(pos))
                ),
            }
        )
    return options


def render_angular_dimensions(
    dwg,
    plan,
    a,
    *,
    ctx,
    only=None,
    name=None,
    pin=False,
    priority=0.0,
    place_candidates=place_strip_candidates,
) -> int:
    """Queue compiler-approved included angles through the shared corridor solve."""
    count = 0
    rank = max(float(priority), 100.0) if pin else float(priority)
    for index, group in enumerate(plan.of_kind("angle")):
        if only is not None and group.ref not in only:
            continue
        bundles = (group.dims,) if group.shared_label else tuple((d,) for d in group.dims)
        for members in bundles:
            label = group.shared_label or members[0].final_label
            measurement = tuple(member.id for member in members)
            annotation_name = name or f"m_angle_{index}"
            if len(bundles) > 1:
                annotation_name += f"_{members[0].discriminator}"
            options = []
            for member in members:
                reference = member.angular_reference
                if reference is None:
                    raise ValueError("approved included angle has no angular reference")
                options.extend(
                    _angular_specs(
                        a,
                        reference,
                        label,
                        annotation_name,
                        dwg.draft,
                        side=member.side or group.side,
                    )
                )

            def placed(annotation_name):
                if pin:
                    dwg.pin(annotation_name)

            def dropped(
                _name,
                _options=options,
                _measurement=measurement,
                _label=label,
                _ref=group.ref,
            ):
                for option in _options[1:]:
                    if _place_corridor_option(
                        dwg,
                        option,
                        _ref,
                        ctx=ctx,
                        trace=ctx.trace,
                        measurement=_measurement,
                        priority=rank,
                        anchored=pin,
                        place_candidates=place_candidates,
                    ):
                        placed(option["name"])
                        return
                ctx.record_issue(
                    "warning",
                    "angular_dimension_dropped",
                    f"Included angle {_label} could not fit its reference sector",
                    measurement=_measurement,
                )

            if not options:
                dropped("")
                continue
            primary = options[0]
            register_corridor(
                ctx,
                (primary["view"], primary["side"]),
                primary["strip"],
                primary["view"],
                primary["axis"],
                _PMI_SLOT,
                CorridorCandidate(
                    name=primary["name"],
                    build=primary["build"],
                    order=primary["order"],
                    on_place=placed,
                    on_drop=dropped,
                    force=True,
                    feature=group.ref,
                    measurement=measurement,
                    priority=rank,
                    anchored=pin,
                    natural=primary["natural"],
                    footprint=primary["footprint"],
                    valid_position=primary["valid_position"],
                    compact_candidates=primary["compact_candidates"],
                ),
            )
            count += 1
    return count


def _bore_render_options(
    rec,
    cfg,
    label,
    name,
    draft,
    cx,
    cy,
    cz,
    lo,
    hi,
    u,
    v,
    half_span_pg,
    surface_pg,
):
    """Choose in-place witnesses or a surface leader for one projected bore."""
    if half_span_pg >= _MIN_INPLACE_BORE_HALF_MM:
        p1, p2 = cfg["span"](cx, cy, cz, lo, hi)
        order = tuple(
            side
            for side in cfg["order"]
            if (rec.view is None or rec.view == cfg["view"])
            and (rec.side is None or rec.side == side)
        )
        return [
            _pmi_dim_spec(
                p1,
                p2,
                cfg["zones"][side],
                label,
                name,
                cfg["view"],
                side,
                draft,
                leader_fallback=True,
            )
            for side in order
        ]
    leader_order = tuple(
        side
        for side in cfg["leader_order"]
        if (rec.view is None or rec.view == cfg["view"]) and (rec.side is None or rec.side == side)
    )
    return [
        _pmi_leader_spec(
            (u, v + (surface_pg if side == "above" else -surface_pg), 0),
            cfg["zones"][side],
            label,
            name,
            cfg["view"],
            side,
            draft,
        )
        for side in leader_order
    ]


def _place_pmi_record(
    dwg, a: Analysis, ctx, rec, idx, bore_cfg, draft, *, queue_options=_pmi_queue_options
) -> bool:
    """Place one PMI record; returns True when it was queued/placed on a strip.

    Diameter/radius use ``bore_cfg`` (the ``_bore`` table), X/Z linears use the
    shared front-view shape, and Y linears use the
    side-then-plan fallback. A degenerate reference is recorded unrenderable and returns
    False without escalating to the bottom drop.
    """
    _pmi_queue_options = queue_options
    ax = rec.dominant_axis
    label = rec.label
    circular_refs = tuple(getattr(rec, "circular_refs", ()))
    cylindrical_refs = tuple(getattr(rec, "cylindrical_refs", ()))
    pattern_count = len(circular_refs) or (
        len(cylindrical_refs)
        if cylindrical_refs and cylindrical_refs[0].principal_axis == "?"
        else 0
    )
    if (
        rec.pmi_kind == "diameter"
        and pattern_count > 1
        and re.match(r"^\s*\d+\s*[xX×]\s*", label) is None
    ):
        label = f"{pattern_count}× {label}"
    placed = False
    name_x = f"pmi_x_{idx}"
    name_z = f"pmi_z_{idx}"
    name_y = f"pmi_y_{idx}"
    name_d = f"pmi_d_{idx}"

    if rec.pmi_kind == "angular":
        references = tuple(getattr(rec, "angular_references", ())) or (rec.angular_reference,)
        options = [
            option
            for reference in references
            for option in _angular_specs(
                a,
                reference,
                rec.label,
                f"pmi_angle_{idx}",
                draft,
                side=rec.side,
                implicit_degrees=True,
            )
        ]
        placed = _pmi_queue_options(
            dwg,
            ctx,
            options,
            ax,
            label,
            rec,
        )
    elif rec.pmi_kind in ("diameter", "radius"):
        if rec.pmi_kind == "diameter" and cylindrical_refs and ax == "?":
            placed = _pmi_queue_options(
                dwg,
                ctx,
                _oblique_cylinder_leader_specs(a, rec, label, name_d, draft),
                ax,
                label,
                rec,
            )
            return bool(placed)
        # Bore size: a diameter spans centroid ± value/2; a radius runs centroid → +value.
        # See `_bore_span_offsets`.
        info = _bore_info(rec)
        if info is None:
            _log.debug("PMI dim[%d] diam: no ref_bbox, skip", idx)
            _record_pmi_no_candidate(ctx, label, rec)
            return False
        bore_axis, cx_f, cy_f, cz_f = info
        # Resolved axis (handles _bore_info's '?' degenerate-bbox fallback); the diameter
        # view table (Z→plan, X→side, Y→front) differs from the linear-dim one.
        ax = bore_axis
        lo, hi = _bore_span_offsets(rec.pmi_kind, rec.value)
        # The legibility gate and leader target need different quantities:

        # * `half_span_pg` — half the DRAWN span, which is what the legibility gate asks
        #   about ("does the label fit between the witness bases"). A radius dim is `value`
        #   long, not `2 * value`, so the two kinds no longer share it.
        # * `surface_pg` — the distance from the bore centre to its SURFACE, which is `hi`
        #   for BOTH kinds and is where the leader's arrow must point.

        # Keep these separate: changing the legibility span must not move the
        # radius leader's arrow away from the bore surface.
        half_span_pg = ((hi - lo) / 2) * a.SCALE
        surface_pg = hi * a.SCALE  # centre-to-surface on the page (mm), both kinds
        # Narrow bores (page span < text width) lead out to a shelf; bracket dims only
        # when the span fits the label. An unresolved axis matches no cfg → bottom drop.
        cfg = bore_cfg.get(bore_axis)
        if cfg is not None:
            u, v = cfg["centre"](cx_f, cy_f, cz_f)
            placed = _pmi_queue_options(
                dwg,
                ctx,
                _bore_render_options(
                    rec,
                    cfg,
                    label,
                    name_d,
                    draft,
                    cx_f,
                    cy_f,
                    cz_f,
                    lo,
                    hi,
                    u,
                    v,
                    half_span_pg,
                    surface_pg,
                ),
                ax,
                label,
                rec,
            )

    elif rec.pmi_kind == "linear" and ax == "?":
        placed = _pmi_queue_options(
            dwg,
            ctx,
            _oblique_linear_specs(a, rec, label, f"pmi_oblique_{idx}", draft),
            ax,
            label,
            rec,
        )

    elif ax == "X":
        placed = _pmi_front_linear(
            dwg,
            a,
            ctx,
            rec,
            ax,
            label,
            name_x,
            "above",
            "below",
            a.PV_Y if rec.view == "plan" else a.FV_Y,
            queue_options=queue_options,
        )
        if placed is None:
            _log.debug("PMI dim[%d] X: degenerate reference", idx)
            _record_pmi_unrenderable(label, rec, ctx=ctx)
            return False

    elif ax == "Z":
        placed = _pmi_front_linear(
            dwg,
            a,
            ctx,
            rec,
            ax,
            label,
            name_z,
            "right",
            "left",
            a.FV_X,
            queue_options=queue_options,
        )
        if placed is None:
            _log.debug("PMI dim[%d] Z: degenerate reference", idx)
            _record_pmi_unrenderable(label, rec, ctx=ctx)
            return False

    elif ax == "Y" and (rec.view is not None or rec.side is not None):
        # A degenerate reference (no witness in EITHER candidate view) is a validation
        # failure, not a placement one — report it distinctly.
        if (
            _pmi_witness_from_bbox(rec, "side", a) is None
            and _pmi_witness_from_bbox(rec, "plan", a) is None
        ):
            _log.debug("PMI dim[%d] Y: degenerate reference", idx)
            _record_pmi_unrenderable(label, rec, ctx=ctx)
            return False
        # A side override selects an exact strip. A view-only override keeps the ordinary
        # geometry-derived side within that projection instead of changing an unspecified
        # policy merely because its sibling field was supplied.
        target_view = rec.view or ("side" if rec.side in {"above", "below"} else "plan")
        wp = _pmi_witness_from_bbox(rec, target_view, a)
        options = []
        if wp is not None:
            p1, p2, avg = wp
            zones = a.sv_zones if target_view == "side" else a.pv_zones
            target_sides: tuple[str, ...]
            if rec.side is not None:
                target_sides = (rec.side,)
            elif target_view == "side":
                target_sides = ("above", "below") if avg >= a.SV_Y else ("below",)
            else:
                target_sides = ("right", "left") if avg >= a.PV_X else ("left", "right")
            options = [
                _pmi_dim_spec(
                    p1,
                    p2,
                    getattr(zones, target_side),
                    label,
                    name_y,
                    target_view,
                    target_side,
                    draft,
                )
                for target_side in target_sides
            ]
        placed = _pmi_queue_options(dwg, ctx, options, ax, label, rec)

    elif ax == "Y":
        # A degenerate reference (no witness in EITHER candidate view) is a validation
        # failure, not a placement one — report it distinctly.
        if (
            _pmi_witness_from_bbox(rec, "side", a) is None
            and _pmi_witness_from_bbox(rec, "plan", a) is None
        ):
            _log.debug("PMI dim[%d] Y: degenerate reference", idx)
            _record_pmi_unrenderable(label, rec, ctx=ctx)
            return False
        # Try side view (Y maps to SX horizontal).
        wp = _pmi_witness_from_bbox(rec, "side", a)
        if wp is not None:
            p1, p2, avg_sz = wp
            if avg_sz >= a.SV_Y:
                placed = _pmi_queue_options(
                    dwg,
                    ctx,
                    [
                        _pmi_dim_spec(
                            p1, p2, a.sv_zones.above, label, name_y, "side", "above", draft
                        ),
                        _pmi_dim_spec(
                            p1, p2, a.sv_zones.below, label, name_y, "side", "below", draft
                        ),
                    ],
                    ax,
                    label,
                    rec,
                )
            if not placed:
                placed = _pmi_queue_options(
                    dwg,
                    ctx,
                    [
                        _pmi_dim_spec(
                            p1, p2, a.sv_zones.below, label, name_y, "side", "below", draft
                        )
                    ],
                    ax,
                    label,
                    rec,
                )
        # Fall back: plan view (Y maps to PY vertical).
        if not placed:
            wp = _pmi_witness_from_bbox(rec, "plan", a)
            if wp is not None:
                p1, p2, _ = wp
                placed = _pmi_queue_options(
                    dwg,
                    ctx,
                    [
                        _pmi_dim_spec(
                            p1, p2, a.pv_zones.below, label, name_y, "plan", "below", draft
                        )
                    ],
                    ax,
                    label,
                    rec,
                )

    if placed:
        return True
    # No candidate reached the shared solve. Source reconciliation reports this as
    # ``pmi_not_rendered``; ``pmi_dropped`` is reserved for a queued candidate rejected by
    # placement capacity, via ``_pmi_queue_options``'s on-drop callback.
    _log.info("PMI dim[%d] %s %.3g → no viable render candidate", idx, ax, rec.value)
    _record_pmi_no_candidate(ctx, label, rec)
    return False


def render_pmi(
    dwg,
    model,
    a: Analysis,
    *,
    ctx,
    renderable_records=_renderable_pmi_records,
    place_candidates=place_strip_candidates,
    sheet_fallback=_sheet_leader_fallback,
) -> int:
    """Render imported authored dimensions from concept IR as first-class candidates.

    AP242 dimensional PMI lowers to ``AuthoredDimension``; unsupported raw PMI fallback
    records still ride as ``PmiFeature`` so they remain visible to diagnostics (#208/#393).
    Replaces the engine's ``_annotate_pmi``.

    Called from ``_auto_annotate`` before ``drain_corridors`` so authored PMI
    co-solves with automatic strip candidates. Skips only records whose page
    projection has no measurable span; short dimensions use the helper's external layout.

    View assignment:
    - dominant X → front view, fv_zones.above / fv_zones.below
    - dominant Z → front view, fv_zones.right / fv_zones.left
    - dominant Y → side view, sv_zones.above / sv_zones.below
                   (falls back to pv_zones.below for Y dims that are
                    too compressed in the side view)
    """
    draft = dwg.draft
    pmi = [
        f
        for f in model.features
        if f.kind in ("authored_dimension", "pmi")
        and (a.pmi_mode == "annotate" or id(f) not in ctx.document_source_annotation_ids)
    ]
    usable = renderable_records(pmi)
    for blocked in _blocked_authored_dimension_records(pmi):
        _record_blocked_authored_dimension(ctx, blocked)
    for refused in _unsupported_kind_records(pmi):
        _record_unsupported_dimension_kind(ctx, refused)
    n_gtol = sum(1 for r in pmi if r.pmi_kind not in AUTHORED_DIMENSION_KINDS and r.value > 0)
    if n_gtol:
        _log.debug("PMI annotate: %d gtol/datum record(s) not yet annotatable (Phase 4)", n_gtol)
    if not usable:
        _log.info("PMI annotate: no usable records (value>0 with 2+ ref pts)")
        return 0

    FX = a.proj.front_x
    FZ = a.proj.front_z
    SX = a.proj.side_x
    SZ = a.proj.side_z
    PX = a.proj.plan_x
    PY = a.proj.plan_y

    # Per-bore-axis ø/R placement as DATA (ADR 1 (was 0008) orientation-as-data): each bore reads as a
    # circle in ONE view, dimensioned across it in-plane when the page span fits the label, else
    # led out to a shelf. This one table replaces three near-identical Z/X/Y blocks. `order` is
    # the in-place above/below fallback; `leader_order` the narrow-bore one (Y
    # prefers below first). `centre`/`span` project the circle centre and its two span
    # endpoints — symmetric for a diameter, centre-to-surface for a radius.
    _bore: dict[str, dict[str, Any]] = {
        "Z": {
            "view": "plan",
            "zones": {"above": a.pv_zones.above, "below": a.pv_zones.below},
            "order": ("above", "below"),
            "leader_order": ("above", "below"),
            "centre": lambda cx, cy, cz: (PX(cx), PY(cy)),
            "span": lambda cx, cy, cz, lo, hi: (
                (PX(cx + lo), PY(cy), 0),
                (PX(cx + hi), PY(cy), 0),
            ),
        },
        "X": {
            "view": "side",
            "zones": {"above": a.sv_zones.above, "below": a.sv_zones.below},
            "order": ("above", "below"),
            "leader_order": ("above", "below"),
            "centre": lambda cx, cy, cz: (SX(cy), SZ(cz)),
            "span": lambda cx, cy, cz, lo, hi: (
                (SX(cy + lo), SZ(cz), 0),
                (SX(cy + hi), SZ(cz), 0),
            ),
        },
        "Y": {
            "view": "front",
            "zones": {"above": a.fv_zones.above, "below": a.fv_zones.below},
            "order": ("above", "below"),
            "leader_order": ("below", "above"),
            "centre": lambda cx, cy, cz: (FX(cx), FZ(cz)),
            "span": lambda cx, cy, cz, lo, hi: (
                (FX(cx + lo), FZ(cz), 0),
                (FX(cx + hi), FZ(cz), 0),
            ),
        },
    }

    queued = 0
    queue_options = partial(
        _pmi_queue_options,
        place_candidates=place_candidates,
        sheet_fallback=sheet_fallback,
    )
    for idx, rec in enumerate(usable):
        if _place_pmi_record(dwg, a, ctx, rec, idx, _bore, draft, queue_options=queue_options):
            queued += 1
    _log.info("PMI annotate: %d/%d dims queued", queued, len(usable))
    return queued


def _pmi_source_ids(item) -> tuple[str, ...]:
    plural = tuple(getattr(item, "source_ids", ()))
    singular = getattr(item, "source_id", "")
    return tuple(dict.fromkeys(((singular,) if singular else ()) + plural))
