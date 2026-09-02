"""Geometry/feature analysis — build the Analysis namespace from a part (#138 / ADR 0005, P4).

`_analyse` imports the part (STEP or Shape), runs feature detection (holes,
patterns, cylinders, face levels), classifies it (rotational vs prismatic),
chooses the sheet (scale/page via `compose.choose_scale`) and lays out the view
zones (`compose._layout_geometry`/`_build_zones`) — returning the `Analysis`
namespace the rest of the pipeline reads. Sits above `compose` (née `sheet`,
#640) and below `builder` in the DAG.
"""

from __future__ import annotations

import logging
import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import cast

from b123d_recognisers import (
    PartFrame,
    RecognitionResult,
    TurnedProfile,
    TurnedProfileKey,
    TurnedStep,
    analyse_cylinders,
    build_raw_recognition_result,
    full_cylinders,
)
from build123d import Compound, Shape
from build123d_drafting.helpers import draft_preset
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader

from draftwright._core import (
    _CONCENTRIC_TOL_MM,
    _DIM_PAD,
    _FONT_SIZE,
    _MARGIN,
    _MIN_RENDER_MM,
    _MIN_VIEW_MM,
    Analysis,
    _content_margin,
    _legible_steps,
    _Projector,
)
from draftwright._geometry import _classify_rotational_cylinders, _solids_body
from draftwright._geometry import (
    _is_rotational as _is_rotational,
)
from draftwright._geometry import (
    dedup_diams as dedup_diams,
)
from draftwright.compose import (
    StripDepths,
    _build_zones,
    _est_hole_table_sizes,
    _est_planned_bore_callout_width,
    _layout_geometry,
    _measure_strips,
    choose_scale,
)
from draftwright.model import build_part_model
from draftwright.model.ir import Datum, GrooveFeature, PartModel, StepFeature, StepLevelFeature
from draftwright.model.planner import plan_dimensions
from draftwright.recognition_frame import (
    FramedDetection,
    FramedDetectionRefusal,
    prepare_framed_detection,
    require_unambiguous_groove_owner,
)
from draftwright.view_plan import ViewConstraints, arrangement_of

_log = logging.getLogger(__name__)

_ScalePick = tuple[float, float, float, float]


@dataclass(frozen=True)
class _DeclaredTurnedProfile(TurnedProfile):
    """A synthetic provider-shaped profile retaining Draftwright's opaque membership.

    ``TurnedProfile.profile`` remains a geometrically valid provider key for projection and
    body-local matching.  The additional token is the declared-program identity needed to
    distinguish overlapping coaxial occurrences without leaking it into the provider API.
    """

    profile_group: str | None = None


def _apply_principal_view_pins(
    geometry,
    constraints,
    *,
    scale: float,
    centre: tuple[float, float, float],
    page: tuple[float, float],
    margin: float,
    views: tuple[str, ...] | None,
) -> None:
    """Translate the conventional orthographic group to satisfy authored origin pins.

    Third-angle front/plan/side relationships are hard constraints.  Their layout therefore
    has two translational degrees of freedom, not six independent coordinates: pinning any one
    projection origin translates the complete group, and multiple pins must imply the same
    translation.  This runs before projection/zones, so every annotation strip follows the
    resolved view blocks.
    """

    if not isinstance(constraints, ViewConstraints) or not constraints.pins:
        return
    planned = set(views or ("front", "plan", "side"))
    centres = {
        "front": (geometry.FV_X, geometry.FV_Y),
        "plan": (geometry.PV_X, geometry.PV_Y),
        "side": (geometry.SV_X, geometry.SV_Y),
    }
    cx, cy, cz = centre
    projected_centre = {
        "front": (cx * scale, cz * scale),
        "plan": (cx * scale, cy * scale),
        "side": (cy * scale, cz * scale),
    }
    translations = []
    for pin in constraints.pins:
        if pin.view not in centres:
            where = f" at {pin.source}" if pin.source is not None else ""
            raise ValueError(
                f"whole-view pin{where} targets {pin.view!r}; this slice can anchor principal "
                "orthographic projection origins only"
            )
        if pin.view not in planned:
            raise ValueError(f"whole-view pin targets absent view {pin.view!r}")
        centre_at_pin = (
            pin.at[0] + projected_centre[pin.view][0],
            pin.at[1] + projected_centre[pin.view][1],
        )
        current = centres[pin.view]
        translations.append((centre_at_pin[0] - current[0], centre_at_pin[1] - current[1], pin))
    dx, dy, first = translations[0]
    for other_dx, other_dy, pin in translations[1:]:
        if max(abs(other_dx - dx), abs(other_dy - dy)) > 0.05:
            raise ValueError(
                f"whole-view pins at {first.source} and {pin.source} contradict the fixed "
                "third-angle relationships; they imply different group translations"
            )

    geometry.FV_X += dx
    geometry.FV_Y += dy
    geometry.PV_X += dx
    geometry.PV_Y += dy
    geometry.SV_X += dx
    geometry.SV_Y += dy
    geometry.sv_geometry_right += dx
    geometry.sv_right += dx
    geometry.sv_right_wall += dx
    # Geometry bounds are the minimum pre-projection feasibility gate. Annotation bands use
    # the shifted anchors below and remain subject to the ordinary completeness/lint gates.
    extents = {
        "front": (geometry.FV_X, geometry.FV_Y, geometry.fv_hw, geometry.fv_hh),
        "plan": (geometry.PV_X, geometry.PV_Y, geometry.fv_hw, geometry.pv_hh),
        "side": (geometry.SV_X, geometry.SV_Y, geometry.sv_hw, geometry.fv_hh),
    }
    page_w, page_h = page
    for name in planned:
        x, y, hw, hh = extents[name]
        if (
            x - hw < margin
            or x + hw > page_w - margin
            or y - hh < margin
            or y + hh > page_h - margin
        ):
            pin = translations[0][2]
            raise ValueError(
                f"whole-view pin at {pin.source} is infeasible: translating the conventional "
                f"view group puts {name!r} outside the drawable page; the anchor was not relaxed"
            )


def _planned_section_count(model, constraints, *, is_rotational=False, cx=0.0, cy=0.0) -> int:
    """Number of section blocks the outer compose pass must reserve."""

    automatic = int(_will_section(model, is_rotational=is_rotational, cx=cx, cy=cy))
    if not isinstance(constraints, ViewConstraints):
        return automatic
    requested = (
        constraints.derived
        if constraints.derived_source == "authored"
        else constraints.added_derived
    )
    authored = sum(item.spec.kind == "section" for item in requested)
    return authored if constraints.derived_source == "authored" else automatic + authored


def _planned_iso_scale(constraints) -> float | None:
    if not isinstance(constraints, ViewConstraints):
        return None
    for item in (*constraints.principals, *constraints.added_principals):
        if item.spec.name == "iso" and item.spec.scale_factor is not None:
            return item.spec.scale_factor
    return None


def _sizing_bores(z_cyls, z_diams, od_diam, cx, cy) -> list:
    """Concentric bore diameters on the rotation axis (the rotational furniture's bore
    set), computed from explicit locals so the sizing IR can be built *before* the
    Analysis namespace exists (#584 WP1 A). The single source shared with
    ``orchestrator.build_model`` (which passes the same values off ``a``)."""
    concentric = {
        c["diameter"]
        for c in full_cylinders(z_cyls)
        if not c["external"]
        and math.hypot(c["axis_xyz"][0] - cx, c["axis_xyz"][1] - cy) <= _CONCENTRIC_TOL_MM
    }
    return [d for d in z_diams if d != od_diam and any(abs(d - c) <= 0.15 for c in concentric)]


def _will_section(model, *, is_rotational=False, cx=0.0, cy=0.0) -> bool:
    """True when the IR *model* contains a section-driving Z hole/pattern.

    Detection-based layout uses recogniser holes; declared-model builds may
    intentionally supply features detection missed. Inspect the public IR shape
    duck-typed here so declared sections get the same page/scale reservation.
    """

    if model is None:
        return False
    # An explicit Sheet.section() request (ADR 0011, #841) reserves the row even when no
    # hole gate qualifies — a blind pocket's floor/depth section has no driving Z hole.
    if getattr(model, "decorations", {}).get("section") is not None:
        return True
    if getattr(model, "decorations", {}).get("auto_sections") is False:
        return False
    features = getattr(model, "features", model)

    def feature_member(pt) -> bool:
        return not (is_rotational and math.hypot(pt[0] - cx, pt[1] - cy) <= _CONCENTRIC_TOL_MM)

    for feat in features:
        if getattr(feat, "kind", None) not in ("hole", "pattern"):
            continue
        frame = getattr(feat, "frame", None)
        if frame is None or frame.axis != "z":
            continue
        members = getattr(feat, "members", ()) or (frame.origin,)
        if not any(feature_member(m) for m in members):
            continue
        bore = getattr(feat, "member", feat)
        if (
            getattr(bore, "cbore", None) is not None
            or getattr(bore, "spotface", None) is not None
            or not getattr(bore, "through", True)
        ):
            return True
    return False


def _coerce_layout_model(model, part, decorations=None) -> PartModel | None:
    """Return the caller-declared IR with authored decorations for layout sizing.

    This mirrors the builder's render-time coercion, but stays local to analysis so
    page/scale/strip selection can see the same authored callout text the renderer
    will later place (#450).
    """
    if model is None:
        return None
    if isinstance(model, PartModel):
        turned_axes = {f.frame.axis for f in model.features if isinstance(f, StepFeature)}
        orientation = next(iter(turned_axes)) if len(turned_axes) == 1 else None
        out = model
        if decorations:
            out = replace(out, decorations={**model.decorations, **decorations})
        # ``orientation`` is derived compiler metadata, not caller authority. Normalise a
        # hand-built PartModel as well as a feature sequence so mixed-axis meaning cannot
        # depend on which StepFeature happened to be declared first.
        if turned_axes and out.orientation != orientation:
            out = replace(out, orientation=orientation)
        return out
    features = list(model)
    bbox = part.bounding_box()
    turned_axes = {f.frame.axis for f in features if isinstance(f, StepFeature)}
    orientation = next(iter(turned_axes)) if len(turned_axes) == 1 else None
    datum = Datum(id="datum_xy", kind="point", at=(bbox.min.X, bbox.min.Y, bbox.min.Z))
    return PartModel(
        bbox=bbox,
        orientation=orientation,
        features=features,
        datums=[datum],
        decorations=decorations or {},
    )


def _declared_turned_profiles(model: PartModel) -> tuple[TurnedProfile, ...]:
    """Return declared step profiles grouped by their body-local axis line.

    The axis letter plus the two perpendicular frame coordinates identify one line. A
    synthetic provider key retains that ownership in caller coordinates, so parallel declared
    shafts cannot be silently merged into one global profile (#1357).
    """
    # Generated Sheet scripts round coordinates to 0.001 mm. Adjacent authored steps can
    # therefore acquire a 0.0005 mm numerical seam even though they describe one body.
    adjacency_tol = 1e-3 + 1e-9
    steps = [feature for feature in model.features if isinstance(feature, StepFeature)]
    grooves = [feature for feature in model.features if isinstance(feature, GrooveFeature)]
    groups: dict[tuple[str, tuple[float, float], object | None], list[StepFeature]] = {}
    for feature in steps:
        axis = feature.frame.axis
        axis_i = "xyz".index(axis)
        line_values = [float(value) for i, value in enumerate(feature.frame.origin) if i != axis_i]
        line = (line_values[0], line_values[1])
        groups.setdefault((axis, line, feature.profile or feature.profile_group), []).append(
            feature
        )

    body_groups: list[tuple[str, list[StepFeature], object | None]] = []
    for (axis, _line, membership), line_members in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1], repr(item[0][2]))
    ):
        if membership is not None:
            body_groups.append((axis, line_members, membership))
            continue
        axis_i = "xyz".index(axis)
        line_members.sort(
            key=lambda feature: min(float(feature.span[0][axis_i]), float(feature.span[1][axis_i]))
        )
        runs: list[list[StepFeature]] = []
        run_hi = float("-inf")
        for feature in line_members:
            lo, hi = sorted((float(feature.span[0][axis_i]), float(feature.span[1][axis_i])))
            gap_is_groove = any(
                groove.frame.axis == axis
                and all(
                    abs(float(groove.frame.origin[index]) - float(feature.frame.origin[index]))
                    <= adjacency_tol
                    for index in range(3)
                    if index != axis_i
                )
                and float(groove.frame.origin[axis_i]) - float(groove.width) / 2.0
                <= run_hi + adjacency_tol
                and float(groove.frame.origin[axis_i]) + float(groove.width) / 2.0
                >= lo - adjacency_tol
                for groove in grooves
            )
            if not runs or (lo > run_hi + adjacency_tol and not gap_is_groove):
                runs.append([feature])
                run_hi = hi
            else:
                runs[-1].append(feature)
                run_hi = max(run_hi, hi)
        body_groups.extend((axis, members, None) for members in runs)

    profiles = []
    for axis, members, membership in body_groups:
        axis_i = "xyz".index(axis)
        origin = [float(value) for value in members[0].frame.origin]
        origin[axis_i] = 0.0
        radius = max(float(feature.diameter) for feature in members) / 2.0
        lo = min(float(point[axis_i]) for feature in members for point in feature.span)
        hi = max(float(point[axis_i]) for feature in members for point in feature.span)
        bounds: list[float] = []
        for index, value in enumerate(origin):
            bounds.extend((lo, hi) if index == axis_i else (value - radius, value + radius))
        key = (
            membership
            if isinstance(membership, TurnedProfileKey)
            else TurnedProfileKey(
                axis,
                (origin[0], origin[1], origin[2]),
                (bounds[0], bounds[1], bounds[2], bounds[3], bounds[4], bounds[5]),
            )
        )
        provider_profile = TurnedProfile.from_steps(
            TurnedStep(
                axis=axis,
                lo=min(float(feature.span[0][axis_i]), float(feature.span[1][axis_i])),
                hi=max(float(feature.span[0][axis_i]), float(feature.span[1][axis_i])),
                diameter=float(feature.diameter),
                profile=key,
            )
            for feature in members
        )
        assert provider_profile is not None
        profiles.append(
            _DeclaredTurnedProfile(
                axis=provider_profile.axis,
                steps=provider_profile.steps,
                profile=provider_profile.profile,
                profile_group=membership if isinstance(membership, str) else None,
            )
        )
    declared_profiles = tuple(profiles)
    grooves_by_profile: dict[int, list[GrooveFeature]] = {
        id(profile): [] for profile in declared_profiles
    }
    for groove in grooves:
        owners = require_unambiguous_groove_owner(groove, declared_profiles)
        if owners:
            grooves_by_profile[id(owners[0])].append(groove)

    # Detection's TurnedProfile denominator includes the narrow groove band even though the
    # IR deliberately gives that band to GrooveFeature rather than StepFeature. Reconstruct
    # the same physical denominator for declared/emitted programs; otherwise one remaining
    # step plus the groove could falsely certify a two-step synthetic profile (#1357).
    augmented_profiles = []
    for profile in declared_profiles:
        profile_steps = list(profile.steps)
        axis_index = "xyz".index(profile.axis)
        for groove in grooves_by_profile[id(profile)]:
            lo = float(groove.frame.origin[axis_index]) - float(groove.width) / 2.0
            hi = float(groove.frame.origin[axis_index]) + float(groove.width) / 2.0
            if any(
                abs(float(step.lo) - lo) <= adjacency_tol
                and abs(float(step.hi) - hi) <= adjacency_tol
                for step in profile_steps
            ):
                continue
            profile_steps.append(
                TurnedStep(
                    axis=profile.axis,
                    lo=lo,
                    hi=hi,
                    diameter=float(groove.diameter),
                    profile=profile.profile,
                )
            )
        if len(profile_steps) == len(profile.steps):
            augmented_profiles.append(profile)
            continue
        provider_profile = TurnedProfile.from_steps(profile_steps)
        assert provider_profile is not None
        augmented_profiles.append(
            _DeclaredTurnedProfile(
                axis=provider_profile.axis,
                steps=provider_profile.steps,
                profile=provider_profile.profile,
                profile_group=profile.profile_group,
            )
        )
    return tuple(augmented_profiles)


def _declared_step_zs(model: PartModel, profiles: tuple[TurnedProfile, ...], bb) -> list[float]:
    """The step Z-levels page/scale selection converges on, sourced from the declaration.

    Mirrors the detected path's two branches exactly — Z-axis turned profiles contribute the
    union of their interior shoulders, anything else the prismatic height ladder — with the
    ladder read off a declared :class:`StepLevelFeature` instead of re-scanning face levels
    (#1022). The 0.6 mm end-exclusion is the detected path's, kept identical so a declared build
    selects the same page as the equivalent detected one.
    """
    if profiles and {profile.axis for profile in profiles} == {"z"}:
        return sorted(
            {
                z
                for profile in profiles
                for z in profile.shoulders
                if bb.min.Z + 0.6 < z < bb.max.Z - 0.6
            }
        )
    return sorted(
        {
            z
            for f in model.features
            if isinstance(f, StepLevelFeature)
            for z in f.levels
            if bb.min.Z + 0.6 < z < bb.max.Z - 0.6
        }
    )


def _import_step(path) -> Compound:
    """Read solid geometry from a STEP file via OCCT's ``STEPControl_Reader``.

    build123d's ``import_step`` uses the XCAF reader (colours, names, PMI), which
    **segfaults** on some AP242 files carrying semantic PMI — e.g. NIST CTC-02
    AP242 (#20) — before any Python code can intervene. draftwright needs only
    the solid geometry (it drops PMI presentation data anyway), so we read the
    geometry directly. Verified to produce identical shapes (solids, edges, bbox)
    to ``import_step`` on the files that read in both, minus the unused metadata.
    """
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise ValueError(f"could not read STEP file {path!r}")
    reader.TransferRoots()
    return Compound(reader.OneShape())


# ---------------------------------------------------------------------------
# Geometry analysis
# ---------------------------------------------------------------------------


def _converge_step_sizing(
    initial_steps: int,
    measure_strips: Callable[[int], StripDepths],
    pick_scale: Callable[[int, StripDepths], _ScalePick],
    count_legible: Callable[[float], int],
) -> tuple[_ScalePick, StripDepths, int]:
    """Choose scale/page with a step-corridor count that matches legibility.

    The right-side step ladder is reserved before the scale is known, but the
    actual step list is filtered by the chosen scale. Iterate that dependency
    until it reaches a fixed point; if it cycles, reserve the largest count seen
    so the sheet is sized conservatively instead of silently accepting whichever
    value happened to appear on a fixed iteration budget (#520).
    """
    n_for_sizing = initial_steps
    seen: set[int] = set()
    attempted: list[int] = []
    max_iter = max(4, initial_steps + 2)

    for _ in range(max_iter):
        if n_for_sizing in seen:
            break
        seen.add(n_for_sizing)
        attempted.append(n_for_sizing)

        strips = measure_strips(n_for_sizing)
        scale_pick = pick_scale(n_for_sizing, strips)
        n_next = count_legible(scale_pick[0])
        if n_next == n_for_sizing:
            return scale_pick, strips, n_for_sizing
        if n_next in seen:
            n_for_sizing = n_next
            break
        n_for_sizing = n_next

    conservative_n = max(attempted + [n_for_sizing], default=initial_steps)
    strips = measure_strips(conservative_n)
    scale_pick = pick_scale(conservative_n, strips)
    _log.warning(
        "Step-corridor sizing did not converge from %d steps (tried %s); reserving %d steps",
        initial_steps,
        attempted,
        conservative_n,
    )
    return scale_pick, strips, conservative_n


# A hole is "concentric" with a turned part's rotation axis when its drilling
# axis is the Z (OD) axis and its opening sits on the part centreline.  Such
# bores are already dimensioned by the ldr_z bore leaders, so they must not
# also receive a hole callout / location dim (#10).  Off-axis holes (a bolt
# circle, a cross-hole) fall through to the feature-presence path.


# --- lint scoring (see Drawing.lint_summary) -------------------------------


# ---------------------------------------------------------------------------
# Strip / zone layout model
# ---------------------------------------------------------------------------


# Slot sizes for the annotations that allocate from fv/pv/sv strips.
# Shared between the depth estimators below and the allocate() call-sites in
# _auto_annotate() so that a slot-size change is automatically reflected in
# the estimator-driven corridor widths.
#
# A slot is the perpendicular depth (page-mm) reserved for one Dimension: its
# dim-line offset from the view edge plus the label, which sits exactly
# pad_around_text beyond the line (measured: a "right"/"below" Dimension's
# perpendicular span equals offset + pad_around_text - extension_gap, and
# pad == extension_gap in the draft preset).  Each slot is therefore derived
# from text metrics (font_size + pad_around_text), like _MIN_STEP_DIM_MM, so it
# rescales with _FONT_SIZE instead of being a bare mm guess (#31).
# Single overall dim: two glyph-heights of line offset + the outboard label pad.
# The overall height dim leads the right ladder, so it carries an extra pad of
# clearance from the view above the first step dim's witness.
# Stacked step dims sit deeper so each ladder rung's label clears the rung below.

# A plan view with at least this many holes escalates to a hole chart when it is
# too dense to dimension every hole individually (#93). Below it, a dropped ref
# stays a legibility drop rather than tabulating a handful of holes.

# Smallest projected step height (page-mm) that can still carry a *legible*
# stacked dimension between its two extension lines.  Derived from what has to
# fit vertically: the label (font height) plus an arrowhead at each end plus
# the text clearance above and below — not an arbitrary page-mm cutoff (#13).
# Used as the single gate in BOTH _analyse (n_steps) and _auto_annotate
# (dim_step placement) so the two can never diverge.

# Minimum page-mm separation between two *consecutive* dimensioned step heights.
# Shoulders closer than this on the page read as one, so only the first of such
# a cluster is dimensioned and the rest surface via lint (#41). Sized to the
# value-label footprint (one glyph height + clearance) — enough to tell two
# stacked step dims apart, without dropping genuinely-distinct shoulders.

# Minimum page-mm separation between two *consecutive* hole-location dimensions
# along one axis. Stacked location dims sit on separate tiers, so their value
# labels never collide (the tier pitch handles that); the legibility limit is the
# extension lines / arrowheads merging when two holes share almost the same
# position on that axis. Sized to one arrowhead plus clearance — smaller than the
# step-spacing gate, which also stacks labels in one column. Holes closer than
# this read as one, so only the first of such a run is dimensioned and the rest
# surface via lint (#43): "fits" is not the same as "legible".


# ---------------------------------------------------------------------------
# Annotation depth estimators (Phase 2 of #118)
#
# These pure functions estimate the strip depth (mm) required for each
# inter-view boundary BEFORE view positions are fixed.  They are intentionally
# conservative (may over-estimate slightly).  Used by _analyse() (Phase 3) to
# set minimum corridor widths, and by _fits() (Phase 3) for consistent sheet
# selection.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _GeomClass:
    """Cylinder inventory + OD/rotational classification, the first analysis sub-step (#590)."""

    z_cyls: list
    cross_cyls: list
    z_diams: list
    cross_diams: list
    od_diam: float | None
    od_axis: str
    is_rotational: bool


def _classify_geometry(part, x_size, y_size, z_size, cx, cy, cz) -> _GeomClass:
    """Analyse *part*'s cylinders and classify its OD / rotational orientation (#590 split of
    :func:`_analyse`). Partial (fillet) faces are excluded — they would pollute the OD, the bore
    leaders, and the rotational test alike (#81)."""
    z_cyls, cross_cyls = analyse_cylinders(part)
    classification = _classify_rotational_cylinders(
        (z_cyls, cross_cyls),
        sizes=(x_size, y_size, z_size),
        centre=(cx, cy, cz),
    )
    z_diams = list(classification.z_diams)
    cross_diams = list(classification.cross_diams)

    _log.info("Z-axis diameters: %s", z_diams)
    if cross_diams:
        _log.info("Cross-hole diams: %s", cross_diams)

    od_diam = classification.od_diam
    od_axis = classification.od_axis
    is_rotational = classification.is_rotational
    if z_diams and not is_rotational:
        _log.info("Part classified prismatic; skipping OD/centreline/bore annotations")
    return _GeomClass(z_cyls, cross_cyls, z_diams, cross_diams, od_diam, od_axis, is_rotational)


def _validate_explicit_scale(
    scale,
    SCALE,
    x_size,
    y_size,
    z_size,
    n_for_sizing,
    page,
    strips_i,
    layout_section,
    layout_table_sizes,
    layout_required_tables=(),
    margin=_MARGIN,
    warn_advisory: bool = True,
    views: tuple[str, ...] | None = None,
    include_iso: bool = True,
    iso_scale_factor: float | None = None,
) -> None:
    """Enforce the two scale floors when the caller pinned an explicit *scale* (#489, #590 split
    of :func:`_analyse`). An explicit scale is the user's call — honour it, subject to:
      - ``_MIN_RENDER_MM``: a hard geometry limit; below it OCCT's annotation arcs degenerate
        (Geom_TrimmedCurve U1==U2, ~1e-4 mm; 0.1 mm is a conservative floor). Reject with a clean
        message — there is no meaningful drawing this small anyway.
      - ``_MIN_VIEW_MM``: a legibility floor; below it annotations crowd but the drawing is valid,
        so honour the scale with a warning. This floor does NOT bound the auto scale
        (``choose_scale`` is a pure geometric page fit), so a warning is only useful when a
        legible page-fitting scale actually exists — i.e. the auto scale is itself legible."""
    if scale is None:
        return
    min_dim = min(x_size, y_size, z_size)
    min_view = min_dim * SCALE
    if min_view < _MIN_RENDER_MM:
        safe = _MIN_RENDER_MM / min_dim
        raise ValueError(
            f"scale {SCALE!r} projects the smallest part dimension "
            f"({min_dim:.0f} mm) to {min_view:.3g} mm — the drawing geometry degenerates "
            f"below {_MIN_RENDER_MM:g} mm (OCCT arc construction fails). "
            f"Use scale ≥ {safe:.3g} or omit the scale for automatic selection."
        )
    if not warn_advisory:
        return
    auto_scale, _, _, _ = choose_scale(
        x_size,
        y_size,
        z_size,
        n_steps=n_for_sizing,
        scale=None,
        page=page,
        strips=strips_i,
        section=layout_section,
        table_sizes=layout_table_sizes,
        required_tables=layout_required_tables,
        margin=margin,
        views=views,
        include_iso=include_iso,
        iso_scale_factor=iso_scale_factor,
    )
    # Warn only when omitting the scale would truly give a legible fit (auto scale itself is
    # legible) but the requested scale is below the floor. A part illegible at every
    # page-fitting scale can't be helped by a bigger one, so nagging there would be false.
    if min_view < _MIN_VIEW_MM <= auto_scale * min_dim:
        safe = _MIN_VIEW_MM / min_dim
        # No stacklevel that reaches user code: this fires deep in _analyse, and the public
        # entry points (make_drawing, Sheet.export, build_drawing) sit at different depths.
        # The message is self-contained (names the scale, the projection, and the fix).
        warnings.warn(
            f"scale {SCALE!r} projects the smallest part dimension ({min_dim:.0f} mm) to "
            f"{min_view:.1f} mm, below the {_MIN_VIEW_MM:.0f} mm legibility floor — "
            f"annotations may crowd or overlap. Honouring the requested scale; use "
            f"scale ≥ {safe:.3g} or omit the scale for an automatic legible fit."
        )


def _analyse(
    step_file,
    title,
    number,
    tolerance,
    drawn_by,
    out,
    scale=None,
    page=None,
    pmi=None,
    model=None,
    decorations=None,
    authored=None,
    material="",
    date="",
    revision="A",
    company="",
    frame: bool = False,
    projection: str | None = None,
    zones: bool = False,
    _reuse: Analysis | None = None,
    _required_tables=(),
    _arrangements: tuple[str, ...] | None = None,
    _views: tuple[str, ...] | None = None,
    _include_iso: bool = True,
    _view_constraints=None,
    _framed_recognition: bool = False,
) -> Analysis:
    """Load STEP or use a build123d Shape, analyse geometry, compute layout.

    Returns an :class:`Analysis`.
    """
    # The zone-grid ruler (#768) draws its ticks on the frame, so it implies one.
    frame = frame or zones
    # The content margin — raised by the sheet-frame band (#767) so scale/page selection and
    # placement both reserve room for the border. Computed up front so the choose_scale inside
    # step-count convergence sees it too.
    margin = _content_margin(frame)
    recognition: RecognitionResult | None
    recognition_frame: PartFrame | None = None
    recognition_frame_decision: dict[str, object]
    if _reuse is not None:
        # Explicit-scale fallback changes only page-space layout. Reuse the immutable geometry,
        # STEP/PMI census, classification, and recognition waist from the requested trial rather
        # than importing and recognising the same part up to fifteen more times (#1146).
        part = _reuse.part
        source_part = _reuse.source_part if _reuse.source_part is not None else part
        recognition_frame = cast(PartFrame | None, _reuse.recognition_frame)
        recognition_frame_decision = dict(
            _reuse.recognition_frame_decision
            or {"status": "not_evaluated", "gauge": None, "refusal_reason": None}
        )
        src = str(_reuse.step_file)
        pmi_defaulted = _reuse.pmi_defaulted
        pmi_mode = _reuse.pmi_mode
        pmi_report = _reuse.pmi_report
        pmi_records = _reuse.pmi if pmi_mode != "off" else []
        bb = _reuse.bb
        x_size, y_size, z_size = _reuse.x_size, _reuse.y_size, _reuse.z_size
        cx, cy, cz = _reuse.cx, _reuse.cy, _reuse.cz
        bbox_max = _reuse.bbox_max
        z_cyls, cross_cyls = (list(items) for items in _reuse.cyls)
        z_diams, cross_diams = _reuse.z_diams, _reuse.cross_diams
        od_diam = _reuse.od_diam
        od_axis = _reuse.od_axis
        is_rotational = _reuse.is_rotational
        layout_model = _coerce_layout_model(model, part, decorations)
        recognition = _reuse.recognition if layout_model is None else None
    else:
        if isinstance(step_file, Shape):
            part = step_file
            src = "build123d object"
        else:
            part = _import_step(step_file)
            src = str(step_file)
        part = _solids_body(part, src)
        source_part = part

        pmi_defaulted = pmi is None
        pmi_mode = "off" if pmi_defaulted else pmi

        pmi_report = None
        pmi_records = []

        # Declarations retain caller-coordinate authority and run no aggregate (ADR 0011).
        # Automatic builds make the framed/raw selection here, above every bbox, IR, planner,
        # projection and physical-lint consumer (ADR 0020 activation).
        layout_model = _coerce_layout_model(model, part, decorations)
        recognition = None
        if layout_model is None and _framed_recognition:
            framed = prepare_framed_detection(part)
            if isinstance(framed, FramedDetection):
                part = framed.part
                recognition = framed.result
                classification = framed.classification
                _gc = _GeomClass(
                    list(classification.z_cyls),
                    list(classification.cross_cyls),
                    list(classification.z_diams),
                    list(classification.cross_diams),
                    classification.od_diam,
                    classification.od_axis,
                    classification.is_rotational,
                )
                recognition_frame = framed.frame
                recognition_frame_decision = {
                    "status": "framed",
                    "gauge": framed.frame.gauge.value,
                    "refusal_reason": None,
                }
            else:
                assert isinstance(framed, FramedDetectionRefusal)
                # The accepted leaf boundary never hides recovery. Analysis is the explicit
                # product-policy owner: one typed provider refusal selects one raw aggregate.
                raw_bb = part.bounding_box()
                raw_centre = raw_bb.center()
                _gc = _classify_geometry(
                    part,
                    raw_bb.size.X,
                    raw_bb.size.Y,
                    raw_bb.size.Z,
                    raw_centre.X,
                    raw_centre.Y,
                    raw_centre.Z,
                )
                recognition = build_raw_recognition_result(
                    part,
                    cylinders=(_gc.z_cyls, _gc.cross_cyls),
                    rotational=_gc.is_rotational,
                )
                recognition_frame_decision = {
                    "status": "raw_fallback",
                    "gauge": None,
                    "refusal_reason": framed.reason.value,
                }
        else:
            bb0 = part.bounding_box()
            c0 = bb0.center()
            _gc = _classify_geometry(part, bb0.size.X, bb0.size.Y, bb0.size.Z, c0.X, c0.Y, c0.Z)
            recognition_frame_decision = {
                "status": "declared" if layout_model is not None else "raw",
                "gauge": None,
                "refusal_reason": None,
            }
            if layout_model is None:
                recognition = build_raw_recognition_result(
                    part,
                    cylinders=(_gc.z_cyls, _gc.cross_cyls),
                    rotational=_gc.is_rotational,
                )

        # Semantic PMI census (AP242 only; separate read-only pass). Framed extraction receives
        # the provider frame before it classifies correlation topology, preserving tight local
        # boxes and arbitrary directions (#1401 / ADR 0020). Even off mode inventories a STEP
        # source so it can report ignored authored PMI; in-memory Shapes have no AP242 document.
        if not isinstance(step_file, Shape):
            from draftwright.pmi import PmiExtractionReport, extract_pmi_report

            try:
                pmi_report = extract_pmi_report(step_file, frame=recognition_frame)
                if pmi_mode != "off":
                    pmi_records = list(pmi_report.records)
            except Exception as exc:
                _log.warning("PMI extraction failed: %s", exc)
                pmi_report = PmiExtractionReport(error=f"{type(exc).__name__}: {exc}")

        bb = part.bounding_box()
        x_size = bb.max.X - bb.min.X
        y_size = bb.max.Y - bb.min.Y
        z_size = bb.max.Z - bb.min.Z
        cx = (bb.min.X + bb.max.X) / 2
        cy = (bb.min.Y + bb.max.Y) / 2
        cz = (bb.min.Z + bb.max.Z) / 2
        bbox_max = max(x_size, y_size, z_size)

        _log.info("Loaded %s  bbox: %.2f × %.2f × %.2f mm", src, x_size, y_size, z_size)

        z_cyls, cross_cyls = _gc.z_cyls, _gc.cross_cyls
        z_diams, cross_diams = _gc.z_diams, _gc.cross_diams
        od_diam, od_axis, is_rotational = _gc.od_diam, _gc.od_axis, _gc.is_rotational

    # Step Z-levels feed both the step-height ladder and the page-sizing step
    # count. For a vertical (Z-axis) turned part, take them from the unified
    # turned-step model (ADR 0008 step 1): it filters shoulders by the OD
    # silhouette, so an internal feature face — a blind bore's flat floor — is
    # never read as a phantom OD shoulder (the area-only filter in
    # recognise_face_levels admitted it). Prismatic and other parts keep the
    # general face-level scan, which recognise_turned_steps cannot replace (no
    # cylinders → no profile).
    # ADR 0011 / ADR 0017 §6: a declared model skips detection (#1022).  The gate has to sit
    # here, ABOVE the aggregate, which is why `_coerce_layout_model` moved up from its old
    # place below — it is pure (IR in, IR out) and reads nothing this block computes.
    _turned: TurnedProfile | None
    _profiles: tuple[TurnedProfile, ...]
    step_zs: list[float]
    if _reuse is not None:
        _turned = _reuse.prof
        _profiles = _reuse.profiles
        step_zs = list(_reuse.step_zs)
    elif layout_model is not None:
        # Sizing must source profiles and `step_zs` from the DECLARATION here. Taking them from
        # a recognition that has been gated away would silently change page/scale selection,
        # and leaving the plural inventory empty would silently disable axial critique for a
        # declared turned part — both are failures the gate must not introduce (#1022).
        recognition = None
        _profiles = _declared_turned_profiles(layout_model)
        _turned = _profiles[0] if len(_profiles) == 1 else None
        step_zs = _declared_step_zs(layout_model, _profiles, bb)
    else:
        assert recognition is not None
        _profiles = recognition.turned_profiles
        _turned = _profiles[0] if len(_profiles) == 1 else None
        # Plural turned profiles own their body-local shoulders; the aggregate's compatible
        # ladder projection intentionally returns prismatic FaceLevels unless exactly one
        # Z-profile exists. Project the plural inventory explicitly so equal occurrences do
        # not become a phantom global prismatic ladder during page sizing (#1357).
        step_zs = (
            sorted(
                {
                    station
                    for profile in _profiles
                    for station in profile.shoulders
                    if bb.min.Z + 0.6 < station < bb.max.Z - 0.6
                }
            )
            if len(_profiles) > 1 and {profile.axis for profile in _profiles} == {"z"}
            else recognition.step_ladder_for_z_span(bb.min.Z, bb.max.Z)
        )
    # The aggregate owns the shared substrate from here on.  Rebind the local projection so
    # model construction, Analysis and the finished BuildState all consume the same inventory
    # object rather than parallel list/tuple wrappers that merely happen to contain equal data.
    # A declared build has no aggregate, but `analyse_cylinders` already ran in
    # `_classify_geometry` — it is substrate, not a recogniser, so reusing it gates nothing.
    shared_cyls = (
        recognition.cylinders if recognition is not None else (tuple(z_cyls), tuple(cross_cyls))
    )
    shared_z_cyls, _shared_cross_cyls = shared_cyls

    # Pass 1 (two-pass layout, #131): measure annotation strip depths before
    # view positions are fixed.  font_size=3.0 is a fixed page-mm constant so
    # all annotation sizes are scale-independent — no circularity.
    # Construct the same draft preset used later in build_drawing() to read
    # arrow_length and pad_around_text from their authoritative source rather
    # than re-stating them as magic literals in the estimators.
    _draft_est = draft_preset(font_size=_FONT_SIZE, decimal_precision=1)
    _arrow_length = _draft_est.arrow_length
    _pad_around_text = _draft_est.pad_around_text
    # Empty on the declared path — NOT "this part has no holes", but "nothing was detected".
    # Every consumer that would read them as an inventory is either skipped there
    # (`build_part_model`, `build_model`) or goes through the lazy aggregate instead
    # (critique — see `Drawing._recognition`), so the two never get confused.
    holes = list(recognition.holes) if recognition else []
    double_d_bores = list(recognition.double_d_bores) if recognition else []
    patterns = list(recognition.hole_patterns) if recognition else []
    bosses = list(recognition.bosses) if recognition else []
    polygonal_bosses = list(recognition.polygonal_bosses) if recognition else []
    polygonal_stock = list(recognition.polygonal_stock) if recognition else []
    slots = list(recognition.slots) if recognition else []
    pockets = list(recognition.pockets) if recognition else []
    pocket_patterns = list(recognition.pocket_patterns) if recognition else []
    pads = list(recognition.pads) if recognition else []
    # Build the IR once, up front, so page/scale selection sizes from the SAME feature
    # model the renderers use — detected and declared parts share one sizing path and no
    # recogniser record reaches the sheet estimators (ADR 0008; #584 WP1 A). A declared
    # model sizes from its own declaration (ADR 0011); otherwise the detected records are
    # adapted into the IR (cheap — no re-recognition). Sizing is byte-identical to the old
    # record-based estimators EXCEPT where a pattern shares a machining spec with loose
    # holes: the IR keeps them as separate features, so the corridor sizes for the pattern's
    # own callout, not a phantom merged "N×" (the renderer emits them separately too — the
    # old estimator over-reserved). This can shift a tightly-packed such part's layout.
    _bores = (
        tuple(_sizing_bores(shared_z_cyls, z_diams, od_diam, cx, cy))
        if is_rotational and od_axis == "z"
        else ()
    )
    sizing_model = (
        layout_model
        if layout_model is not None
        else cast(PartModel, _reuse.model)
        if _reuse is not None and _reuse.model is not None
        else build_part_model(
            part,
            holes=holes,
            double_d_bores=double_d_bores,
            patterns=patterns,
            bosses=bosses,
            polygonal_bosses=polygonal_bosses,
            polygonal_stock=polygonal_stock,
            channels=list(recognition.channels) if recognition else None,
            slots=slots,
            # Injected from the aggregate since #1026 — `build_part_model` detected these
            # three itself, which is the duplicate scan ADR 0017 exists to remove. On this
            # branch `recognition` is non-None by construction (it is the not-declared arm).
            slot_patterns=list(recognition.slot_patterns) if recognition else None,
            grooves=list(recognition.grooves) if recognition else None,
            risers=list(recognition.risers) if recognition else None,
            chamfers=list(recognition.chamfers) if recognition else None,
            fillets=list(recognition.fillets) if recognition else None,
            circular_blind_steps=(list(recognition.circular_blind_steps) if recognition else None),
            paired_ramp_steps=list(recognition.paired_ramp_steps) if recognition else None,
            through_steps=list(recognition.through_steps) if recognition else None,
            plates=list(recognition.plates) if recognition else None,
            flats=list(recognition.flats) if recognition else None,
            pockets=pockets,
            pocket_patterns=pocket_patterns,
            rectangular_blind_slots=(
                list(recognition.rectangular_blind_slots) if recognition else None
            ),
            pads=pads,
            profiles=_profiles,
            step_zs=step_zs,
            face_levels=list(recognition.step_levels) if recognition else None,
            rotational=(od_diam, _bores, od_axis) if is_rotational else None,
            pmi=pmi_records,
            lower_pmi=pmi_mode == "annotate",
            cyls=shared_cyls,
        )
    )
    # Authored omission affects annotation FOOTPRINT sizing, but the analysis model retains
    # its historical automatic requirement inventory for view-feasibility preflight.  The
    # builder applies the same authored tuple to the render model later.  Keeping this as a
    # strip-only copy avoids letting a sparse authored dimension set erase semantic view
    # requirements (for example the parent view needed by an authored detail), while ensuring
    # suppressed pad bands cannot reduce the selected scale (#1392).
    strip_sizing_model = (
        replace(sizing_model, authored_dimensions=tuple(authored))
        if authored is not None
        else sizing_model
    )
    # ADR 0018 Phase 5.5: prove the chosen principal set can carry every approved
    # dimension before scale selection or projection.  A reduced view set is therefore a
    # re-plan, not the fixed three-view plan rendered into fewer views.
    sizing_groups = plan_dimensions(sizing_model, planned_views=_views)
    bore_callout_width = _est_planned_bore_callout_width(
        sizing_groups, _draft_est, font_size=_FONT_SIZE, pad_around_text=_pad_around_text
    )
    section_count = _planned_section_count(
        sizing_model,
        _view_constraints,
        is_rotational=is_rotational,
        cx=cx,
        cy=cy,
    )
    # Preserve the long-standing public diagnostic shape for the common zero/one case while
    # carrying an integer only when authored constraints genuinely reserve multiple sections.
    layout_section = section_count if section_count > 1 else bool(section_count)
    layout_table_sizes = _est_hole_table_sizes(
        sizing_model, bb, font_size=_FONT_SIZE, pad_around_text=_pad_around_text
    )
    layout_required_tables = tuple(_required_tables)
    planned_iso_scale = _planned_iso_scale(_view_constraints)

    # Choose scale/page, iterating so the reserved step corridor matches the
    # number of steps the legibility gate will actually place (#1) — not the raw
    # face count. Otherwise a part with many sub-legible faces (e.g. a staircase
    # with 15 tiny treads) reserves a phantom step ladder that blocks a larger
    # scale. Seed conservatively (all faces), then re-gate at the chosen scale;
    # converges in a couple of rounds.
    def _measure_for_step_count(n_steps_i: int) -> StripDepths:
        return _measure_strips(
            strip_sizing_model,
            n_steps_i,
            bb,
            arrow_length=_arrow_length,
            pad_around_text=_pad_around_text,
            bore_callout_width=bore_callout_width,
        )

    def _pick_for_step_count(n_steps_i: int, strips_i: StripDepths) -> _ScalePick:
        return choose_scale(
            x_size,
            y_size,
            z_size,
            n_steps=n_steps_i,
            scale=scale,
            page=page,
            strips=strips_i,
            section=layout_section,
            table_sizes=layout_table_sizes,
            required_tables=layout_required_tables,
            margin=margin,
            arrangements=_arrangements,
            views=_views,
            include_iso=_include_iso,
            iso_scale_factor=planned_iso_scale,
        )

    scale_pick, strips_i, n_for_sizing = _converge_step_sizing(
        len(step_zs),
        _measure_for_step_count,
        _pick_for_step_count,
        lambda scale_i: len(_legible_steps(step_zs, bb.min.Z, scale_i)[0]),
    )
    SCALE, PAGE_W, PAGE_H, TB_W = scale_pick
    # The fourth dimension of the ADR 0018 §5 choice, carried from `choose_scale` rather than
    # re-derived here: this call sees MEASURED strip depths where selection saw estimates, so
    # re-deriving would compose the sheet under a different arrangement than the one whose
    # feasibility was actually established (#1130).
    ARRANGEMENT = arrangement_of(scale_pick)
    _validate_explicit_scale(
        scale,
        SCALE,
        x_size,
        y_size,
        z_size,
        n_for_sizing,
        page,
        strips_i,
        layout_section,
        layout_table_sizes,
        layout_required_tables,
        margin=margin,
        warn_advisory=_reuse is None,
        views=_views,
        include_iso=_include_iso,
        iso_scale_factor=planned_iso_scale,
    )
    DIM_PAD = _DIM_PAD
    # margin was computed up front (_content_margin(frame)) so scale selection already saw it.
    # Refine: apply the same legibility gate _auto_annotate uses for dim_step.
    n_steps = len(_legible_steps(step_zs, bb.min.Z, SCALE)[0])
    strips = _measure_strips(
        strip_sizing_model,
        n_steps,
        bb,
        arrow_length=_arrow_length,
        pad_around_text=_pad_around_text,
        bore_callout_width=bore_callout_width,
    )
    # View positions + iso empty-rectangle, shared with scale selection (_fits)
    # via _layout_geometry so placement and fit never diverge (#11).  _fit_iso_view
    # later scales the iso to fill its rectangle.
    _g = _layout_geometry(
        x_size,
        y_size,
        z_size,
        SCALE,
        PAGE_W,
        PAGE_H,
        TB_W,
        strips,
        n_steps,
        section=layout_section,
        table_sizes=layout_table_sizes,
        required_tables=layout_required_tables,
        margin=margin,
        arrangement=ARRANGEMENT,
        views=_views,
        include_iso=_include_iso,
        iso_scale_factor=planned_iso_scale,
    )
    _apply_principal_view_pins(
        _g,
        _view_constraints,
        scale=SCALE,
        centre=(cx, cy, cz),
        page=(PAGE_W, PAGE_H),
        margin=margin,
        views=_views,
    )
    fv_hw = _g.fv_hw
    fv_hh = _g.fv_hh
    pv_hh = _g.pv_hh
    sv_hw = _g.sv_hw
    x_offset = _g.x_offset
    FV_X = _g.FV_X
    FV_Y = _g.FV_Y
    PV_X = _g.PV_X
    PV_Y = _g.PV_Y
    SV_X = _g.SV_X
    SV_Y = _g.SV_Y
    sv_right = _g.sv_right
    iso_left_limit = _g.iso_left
    iso_bottom_limit = _g.iso_bottom
    iso_right_limit = _g.iso_right
    iso_top_limit = _g.iso_top
    ISO_X = _g.ISO_X
    ISO_Y = _g.ISO_Y

    # ------------------------------------------------------------------
    # Strip / zone construction.
    # Phase 1: defines regions only — annotation functions still use their
    # own hard-coded offsets.  Later phases will route each annotation
    # through strip.allocate().  The iso view's outer limits are conservative
    # here (PAGE_H - margin / iso_right_limit); _auto_annotate() tightens
    # them once the iso has been projected.
    fv_zones, pv_zones, sv_zones = _build_zones(_g, margin, PAGE_H)

    page_label = {297: "A4", 420: "A3", 594: "A2", 841: "A1", 1189: "A0"}.get(
        int(PAGE_W), f"{PAGE_W:.0f}mm"
    )
    _log.info(
        "Scale %s:1  page %s  FV(%.0f,%.0f) PV(%.0f,%.0f) SV(%.0f,%.0f) ISO(%.0f,%.0f)",
        SCALE,
        page_label,
        FV_X,
        FV_Y,
        PV_X,
        PV_Y,
        SV_X,
        SV_Y,
        ISO_X,
        ISO_Y,
    )

    return Analysis(
        arrangement=ARRANGEMENT,
        planned_views=_views,
        planned_iso=_include_iso,
        planned_iso_scale=planned_iso_scale,
        view_constraints=_view_constraints,
        part=part,
        source_part=source_part,
        recognition_frame=recognition_frame,
        recognition_frame_decision=recognition_frame_decision,
        pmi_working_records=(
            tuple(pmi_records) if recognition_frame is not None and pmi_mode != "off" else None
        ),
        recognition=recognition,
        bb=bb,
        x_size=x_size,
        y_size=y_size,
        z_size=z_size,
        cx=cx,
        cy=cy,
        cz=cz,
        bbox_max=bbox_max,
        holes=holes,
        patterns=patterns,
        bosses=bosses,
        slots=slots,
        pockets=pockets,
        pocket_patterns=pocket_patterns,
        pads=pads,
        z_diams=z_diams,
        cross_diams=cross_diams,
        cyls=shared_cyls,
        prof=_turned,
        profiles=_profiles,
        od_diam=od_diam,
        is_rotational=is_rotational,
        od_axis=od_axis,
        step_zs=step_zs,
        layout_strips=strips,
        layout_n_steps=n_steps,
        layout_section=layout_section,
        layout_table_sizes=layout_table_sizes,
        layout_required_tables=layout_required_tables,
        sv_right=sv_right,
        iso_right_limit=iso_right_limit,
        SCALE=SCALE,
        PAGE_W=PAGE_W,
        PAGE_H=PAGE_H,
        TB_W=TB_W,
        DIM_PAD=DIM_PAD,
        margin=margin,
        x_offset=x_offset,
        FV_X=FV_X,
        FV_Y=FV_Y,
        PV_X=PV_X,
        PV_Y=PV_Y,
        SV_X=SV_X,
        SV_Y=SV_Y,
        proj=_Projector(
            fv_x=FV_X,
            fv_y=FV_Y,
            sv_x=SV_X,
            sv_y=SV_Y,
            pv_x=PV_X,
            pv_y=PV_Y,
            cx=cx,
            cy=cy,
            cz=cz,
            scale=SCALE,
        ),
        ISO_X=ISO_X,
        ISO_Y=ISO_Y,
        iso_left_limit=iso_left_limit,
        iso_bottom_limit=iso_bottom_limit,
        iso_top_limit=iso_top_limit,
        # View half-extents in page units (convenient for strip arithmetic)
        fv_hw=fv_hw,
        fv_hh=fv_hh,
        pv_hh=pv_hh,
        sv_hw=sv_hw,
        # Strip / zone layout model — the per-view strips ADR 0009 placement reads
        fv_zones=fv_zones,
        pv_zones=pv_zones,
        sv_zones=sv_zones,
        step_file=step_file,
        title=title,
        number=number,
        tolerance=tolerance,
        drawn_by=drawn_by,
        material=material,
        date=date,
        revision=revision,
        company=company,
        frame=frame,
        projection=projection,
        zones=zones,
        out=out,
        pmi_report=pmi_report,
        pmi_mode=pmi_mode,
        pmi_defaulted=pmi_defaulted,
        # The sizing model IS the render model when detection ran (identical inputs by
        # construction — #584 WP1 A); store it so the pipeline never detects twice
        # (ADR 0008 Amdt 5, #602). A declared model (layout_model) is NOT stored: the
        # builder coerces + decorates the caller's model itself.
        model=sizing_model if layout_model is None else None,
    )
