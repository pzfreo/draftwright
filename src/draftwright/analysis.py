"""Geometry/feature analysis — build the Analysis namespace from a part (#138 / ADR 1 (was 0005), P4).

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
from typing import Any, cast

from build123d import Compound, Shape
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from quiddity import (
    BoltCircle,
    HoleRecord,
    LinearArray,
    PartFrame,
    RecognitionResult,
    RectGrid,
    TurnedProfile,
    TurnedProfileKey,
    TurnedStep,
    analyse_cylinders,
    full_cylinders,
)
from quiddity.evidence import RecognitionEvidence, build_recognition_evidence

from draftwright._core import (
    _CONCENTRIC_TOL_MM,
    _DIM_PAD,
    _FONT_SIZE,
    _FRAME_BAND,
    _MARGIN,
    _MIN_RENDER_MM,
    _MIN_STEP_SEP_MM,
    _MIN_VIEW_MM,
    Analysis,
    DetailRequest,
    SheetMargins,
    _content_margin,
    _detail_caption,
    _dimension_draft,
    _fmt,
    _legible_steps,
    _Projector,
    _sheet_option_margins,
    _text_size,
    _tol_suffix,
    _validated_title_block_width,
    crowded_horizontal_step_runs,
    supported_secondary_crop,
    y_chain_detail_scale_needed,
)
from draftwright._geometry import _classify_rotational_cylinders, _solids_body
from draftwright._geometry import (
    _is_rotational as _is_rotational,
)
from draftwright._geometry import (
    dedup_diams as dedup_diams,
)
from draftwright.annotation_layout_profile import cap_planned_strips
from draftwright.auxiliary_layout import document_note_rows
from draftwright.compose import (
    StripDepths,
    _build_rear_zones,
    _build_zones,
    _est_hole_table_sizes,
    _est_planned_bore_callout_width,
    _est_table_size,
    _layout_geometry,
    _measure_strips,
    _strips_for_derived_views,
    choose_scale,
)
from draftwright.model.compiled import compile_dimensions
from draftwright.model.detect import _build_part_model_from_recognition
from draftwright.model.ir import (
    Datum,
    DocumentNote,
    GrooveFeature,
    PartModel,
    StepFeature,
    StepLevelFeature,
)
from draftwright.model.manufacturing_schedule import manufacturing_schedule
from draftwright.model.planner import annotation_groups, plan_dimensions
from draftwright.progress import observed_stage
from draftwright.recognition_cache import _result_from_evidence
from draftwright.recognition_frame import (
    FramedDetection,
    FramedDetectionRefusal,
    prepare_framed_detection,
    require_unambiguous_groove_owner,
)
from draftwright.recognition_ownership import RecognitionOwnershipBuilder
from draftwright.sheet_metadata import SheetMetadata
from draftwright.view_plan import (
    DERIVED_VIEW_IDENTIFIERS,
    ViewConstraints,
    arrangement_of,
    principal_placements,
    third_angle_view_names,
)

_log = logging.getLogger(__name__)

_ScalePick = tuple[float, float, float, float]


def _automatic_y_chain_detail_footprints(
    approved,
    bb,
    draft,
    *,
    section_count: int,
    planned_views: tuple[str, ...] | None,
) -> Callable[[float], tuple[tuple[str, float, float], ...]] | None:
    """Pre-sheet minimum footprint for one unambiguous approved Y-step chain.

    Unknown/multiple physical profiles remain unplanned rather than reserving a
    guessed box. The final detailer still owns the crop and validates real ink.
    """
    if planned_views is not None and "side" not in planned_views:
        return None
    # A planned section can yield late and release its letter, so its detail
    # successor is not a stable identity until section placement is made part
    # of the same pre-sheet derived-view plan.
    if section_count != 0:
        return None
    rows = []
    memberships = set()
    for group in approved.of_kind("step"):
        if group.facts.frame.axis != "y":
            continue
        length = group.dim(kind="length")
        if length is None or length.span is None:
            continue
        origin = group.facts.frame.origin
        membership = group.facts.profile or group.facts.profile_group
        memberships.add(repr(membership))
        lo, hi = sorted((float(length.span[0][1]), float(length.span[1][1])))
        rows.append((lo, hi, length, float(origin[0]), float(origin[2])))
    if len(rows) < 2 or len(memberships) != 1:
        return None
    if (
        max(row[3] for row in rows) - min(row[3] for row in rows) > 0.5
        or max(row[4] for row in rows) - min(row[4] for row in rows) > 0.5
    ):
        return None
    rows.sort(key=lambda row: (row[0], row[1]))
    # The renderer separates axially disconnected profiles even on one axis
    # line. A single pre-sheet box would otherwise plan a phantom combined
    # chain and could enlarge the sheet without any matching detail request.
    if any(
        abs(previous[1] - current[0]) > 1e-3 + 1e-9
        for previous, current in zip(rows, rows[1:], strict=False)
    ):
        return None
    # The renderer states a contiguous repeated pitch of three or more once on
    # the parent view. Such a chain does not request an enlarged detail.
    repeat = 1
    for prev, current in zip(rows, rows[1:], strict=False):
        old, new = prev[2], current[2]
        same_text = (
            old.value_text == new.value_text
            if old.display_decimals is not None or new.display_decimals is not None
            else _fmt(old.value) == _fmt(new.value)
        )
        repeat = (
            repeat + 1
            if abs(prev[1] - current[0]) <= 1e-4
            and same_text
            and old.tolerance is None
            and new.tolerance is None
            else 1
        )
        if repeat >= 3:
            return None
    widths = tuple(
        _text_size(
            row[2].value_text + _tol_suffix(row[2].tolerance, draft),
            draft.font_size,
            font=getattr(draft, "font", "Arial"),
        )[0]
        for row in rows
    )
    axis_lo = min(row[0] for row in rows)
    axis_hi = max(row[1] for row in rows)
    axis_z = sum(row[4] for row in rows) / len(rows)
    letter = DERIVED_VIEW_IDENTIFIERS[section_count]
    view_name = f"detail_{letter.lower()}"
    # Measure the worst six-significant-digit scale caption once. The candidate
    # scale loop (which may bisect 60 times) must stay box arithmetic.
    caption_req = DetailRequest(
        axis="y",
        lo=axis_lo,
        hi=axis_hi,
        scale_needed=1.0,
        redraw=lambda *_args: 0,
        source_view="side",
        cross_axis="z",
        cross_lo=axis_z,
        cross_hi=axis_z,
        kind="y-turned-chain",
    )
    caption_w = _text_size(
        _detail_caption(caption_req, letter, 9.99999, bb),
        draft.font_size,
        font=getattr(draft, "font", "Arial"),
    )[0]
    footprint_cache: dict[float, tuple[tuple[str, float, float], ...]] = {}

    def for_scale(scale: float) -> tuple[tuple[str, float, float], ...]:
        if scale in footprint_cache:
            return footprint_cache[scale]
        needed = y_chain_detail_scale_needed(
            tuple((row[0] * scale, row[1] * scale, row[2].value) for row in rows),
            widths,
            arrow_length=draft.arrow_length,
            text_padding=draft.pad_around_text,
        )
        result: tuple[tuple[str, float, float], ...]
        if needed is None or needed > scale * 10:
            result = ()
        else:
            target = next(
                (scale * factor for factor in (2, 5, 10) if scale * factor >= needed), scale * 10
            )
            min_scale = max(needed, scale * 1.2 + 1e-6)
            cross_half = max(0.1, min((bb.max.Z - bb.min.Z) / 4, 6.0 / target))
            # Reserve the minimum sufficient footprint. An arbitrary cushion
            # can turn a feasible A4 detail into a false refusal; measured
            # containment after rendering catches any actual estimate error.
            width = max((axis_hi - axis_lo) * min_scale, caption_w)
            height = (
                2 * cross_half * min_scale
                + draft.font_size
                + 2 * draft.pad_around_text
                + draft.arrow_length
                + _DIM_PAD
                + 8.0
            )
            result = ((view_name, width, height),)
        footprint_cache[scale] = result
        return result

    return for_scale


def _automatic_x_head_detail_footprints(
    approved,
    bb,
    draft,
    *,
    section_count: int,
    planned_views: tuple[str, ...] | None,
) -> Callable[[float], tuple[tuple[str, float, float], ...]] | None:
    """Pre-sheet extent of one unambiguous approved X-turned crowded head.

    The same short-run test and supported-profile crop as the renderer define
    the demand. Multiple physical profiles/heads remain explicitly unplanned.
    """
    if section_count or (planned_views is not None and "front" not in planned_views):
        return None
    rows = []
    memberships = set()
    for group in approved.of_kind("step"):
        if group.facts.frame.axis != "x":
            continue
        length = group.dim(kind="length")
        diameter = group.dim(kind="diameter")
        if length is None or length.span is None or diameter is None:
            return None
        lo, hi = sorted((float(length.span[0][0]), float(length.span[1][0])))
        origin = group.facts.frame.origin
        rows.append(
            (
                lo,
                hi,
                length,
                diameter,
                float(origin[1]),
                float(origin[2]),
                float(getattr(group.facts, "diameter", diameter.value)),
            )
        )
        memberships.add(repr(group.facts.profile or group.facts.profile_group))
    if len(rows) < 2 or len(memberships) != 1:
        return None
    if (
        max(row[4] for row in rows) - min(row[4] for row in rows) > 0.5
        or max(row[5] for row in rows) - min(row[5] for row in rows) > 0.5
    ):
        return None
    ordered = sorted(rows, key=lambda row: (row[0], row[1]))
    if rows != ordered:
        # The renderer forms short runs in compiled order. Do not reserve a
        # different physically sorted run until both boundaries share ordering.
        return None
    if any(
        abs(previous[1] - current[0]) > 1e-3 + 1e-9
        for previous, current in zip(rows, rows[1:], strict=False)
    ):
        return None
    # The renderer crops against the controlled step's physical profile, not
    # the displayed nominal diameter (which PMI may specify independently).
    radial_extents = tuple((row[5] - row[6] / 2, row[5] + row[6] / 2) for row in rows)
    full_lo = min(extent[0] for extent in radial_extents)
    full_hi = max(extent[1] for extent in radial_extents)
    letter = DERIVED_VIEW_IDENTIFIERS[section_count]
    view_name = f"detail_{letter.lower()}"
    cache: dict[float, tuple[tuple[str, float, float], ...]] = {}

    def for_scale(scale: float) -> tuple[tuple[str, float, float], ...]:
        if scale in cache:
            return cache[scale]
        heads = crowded_horizontal_step_runs(
            tuple((row[0], row[1]) for row in rows), scale, draft.arrow_length
        )
        result: tuple[tuple[str, float, float], ...] = ()
        if len(heads) == 1:
            head = [rows[index] for index in heads[0]]
            lo = min(row[0] for row in head)
            hi = max(row[1] for row in head)
            min_length = min(row[2].value for row in head)
            needed = _MIN_STEP_SEP_MM / min_length if min_length > 0 else float("inf")
            target = next(
                (scale * factor for factor in (2, 5, 10) if scale * factor >= needed),
                scale * 10,
            )
            min_scale = max(needed, scale * 1.2 + 1e-6)
            if 10.0 < min_scale <= 15.0 and scale <= 5.0:
                target = min_scale = 10.0
            support = tuple(
                (station, row[4], row[5] + row[6] / 2)
                for row in head
                for station in (row[0], row[1])
            )
            crop = supported_secondary_crop(support, "z", full_lo, full_hi, needed)
            if crop is not None and min_scale <= target:
                request = DetailRequest(
                    axis="x",
                    lo=lo,
                    hi=hi,
                    scale_needed=needed,
                    redraw=lambda *_args: 0,
                    source_view="front",
                    cross_axis="z",
                    cross_lo=crop[0],
                    cross_hi=crop[1],
                    kind="turned-head",
                )
                caption_w = _text_size(
                    _detail_caption(request, letter, min_scale, bb),
                    draft.font_size,
                    font=getattr(draft, "font", "Arial"),
                )[0]
                result = (
                    (
                        view_name,
                        max((hi - lo) * min_scale, caption_w),
                        (crop[1] - crop[0]) * min_scale
                        + 2 * (draft.font_size + 2 * draft.pad_around_text)
                        + draft.arrow_length
                        + min(_DIM_PAD, 6.0)
                        + 8.0,
                    ),
                )
        cache[scale] = result
        return result

    return for_scale


@observed_stage("recognition")
def _raw_recognition(
    part,
    *,
    cylinders,
    rotational: bool,
) -> tuple[RecognitionResult, RecognitionEvidence | None]:
    evidence = build_recognition_evidence(
        part,
        cylinders=cylinders,
        rotational=rotational,
    )
    result = _result_from_evidence(evidence)
    return result, evidence if result is evidence.result else None


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
    margin: float | SheetMargins,
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
    margins = margin if isinstance(margin, SheetMargins) else SheetMargins.uniform(margin)
    planned = set(views or ("front", "plan", "side"))
    centres = {
        "front": (geometry.FV_X, geometry.FV_Y),
        "plan": (geometry.PV_X, geometry.PV_Y),
        "side": (geometry.SV_X, geometry.SV_Y),
    }
    if "rear" in planned:
        centres["rear"] = (geometry.RV_X, geometry.RV_Y)
    cx, cy, cz = centre
    projected_centre = {
        "front": (cx * scale, cz * scale),
        "plan": (cx * scale, cy * scale),
        "side": (cy * scale, cz * scale),
        "rear": (-cx * scale, cz * scale),
    }
    translations = []
    for pin in constraints.pins:
        if pin.view not in projected_centre:
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
                f"{geometry.convention}-angle relationships; they imply different group translations"
            )

    geometry.FV_X += dx
    geometry.FV_Y += dy
    geometry.PV_X += dx
    geometry.PV_Y += dy
    geometry.SV_X += dx
    geometry.SV_Y += dy
    if "rear" in planned:
        geometry.RV_X += dx
        geometry.RV_Y += dy
    geometry.sv_geometry_right += dx
    geometry.sv_right += dx
    geometry.sv_right_wall += dx
    geometry.front_plan_wall += dy
    # Geometry bounds are the minimum pre-projection feasibility gate. Annotation bands use
    # the shifted anchors below and remain subject to the ordinary completeness/lint gates.
    extents = {
        "front": (geometry.FV_X, geometry.FV_Y, geometry.fv_hw, geometry.fv_hh),
        "plan": (geometry.PV_X, geometry.PV_Y, geometry.fv_hw, geometry.pv_hh),
        "side": (geometry.SV_X, geometry.SV_Y, geometry.sv_hw, geometry.fv_hh),
    }
    if "rear" in planned:
        extents["rear"] = (geometry.RV_X, geometry.RV_Y, geometry.fv_hw, geometry.fv_hh)
    page_w, page_h = page
    for name in planned:
        x, y, hw, hh = extents[name]
        if (
            x - hw < margins.left
            or x + hw > page_w - margins.right
            or y - hh < margins.bottom
            or y + hh > page_h - margins.top
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


def _resolved_iso_scale(arrangement: str, authored: float | None) -> tuple[float | None, bool]:
    """Resolve the projection factor while retaining whether it is a hard authored value."""
    if arrangement == "staggered-side" and authored is None:
        return 0.65, False
    return authored, authored is not None


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
    # An explicit Sheet.section() request (ADR 4 (was 0011)) reserves the row even when no
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
    # step plus the groove could falsely certify a two-step synthetic profile.
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
# also receive a hole callout / location dim.  Off-axis holes (a bolt
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
# rescales with _FONT_SIZE instead of being a bare mm guess.
# Single overall dim: two glyph-heights of line offset + the outboard label pad.
# The overall height dim leads the right ladder, so it carries an extra pad of
# clearance from the view above the first step dim's witness.
# Stacked step dims sit deeper so each ladder rung's label clears the rung below.

# A plan view with at least this many holes escalates to a hole chart when it is
# too dense to dimension every hole individually. Below it, a dropped ref
# stays a legibility drop rather than tabulating a handful of holes.

# Smallest projected step height (page-mm) that can still carry a *legible*
# stacked dimension between its two extension lines.  Derived from what has to
# fit vertically: the label (font height) plus an arrowhead at each end plus
# the text clearance above and below — not an arbitrary page-mm cutoff.
# Used as the single gate in BOTH _analyse (n_steps) and _auto_annotate
# (dim_step placement) so the two can never diverge.

# Minimum page-mm separation between two *consecutive* dimensioned step heights.
# Shoulders closer than this on the page read as one, so only the first of such
# a cluster is dimensioned and the rest surface via lint. Sized to the
# value-label footprint (one glyph height + clearance) — enough to tell two
# stacked step dims apart, without dropping genuinely-distinct shoulders.

# Minimum page-mm separation between two *consecutive* hole-location dimensions
# along one axis. Stacked location dims sit on separate tiers, so their value
# labels never collide (the tier pitch handles that); the legibility limit is the
# extension lines / arrowheads merging when two holes share almost the same
# position on that axis. Sized to one arrowhead plus clearance — smaller than the
# step-spacing gate, which also stacks labels in one column. Holes closer than
# this read as one, so only the first of such a run is dimensioned and the rest
# surface via lint: "fits" is not the same as "legible".


# ---------------------------------------------------------------------------
# Annotation depth estimators
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
    title_block_margins: SheetMargins | None = None,
    title_block_width: float | None = None,
    warn_advisory: bool = True,
    advisories: list[tuple[str, str]] | None = None,
    views: tuple[str, ...] | None = None,
    include_iso: bool = True,
    iso_scale_factor: float | None = None,
    convention: str = "third",
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
    if min_dim <= 0:
        raise ValueError(
            "drawing geometry degenerates: the part has no three-dimensional extent "
            "and no scale can produce a solid drawing"
        )
    min_view = min_dim * SCALE
    if min_view < _MIN_RENDER_MM:
        safe = _MIN_RENDER_MM / min_dim
        raise ValueError(
            f"scale {SCALE!r} projects the smallest part dimension "
            f"({min_dim:.0f} mm) to {min_view:.3g} mm — the drawing geometry degenerates "
            f"below {_MIN_RENDER_MM:g} mm (OCCT arc construction fails). "
            f"Use scale ≥ {safe:.3g} or omit the scale for automatic selection."
        )
    if not warn_advisory and advisories is None:
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
        title_block_margins=title_block_margins,
        title_block_width=title_block_width,
        views=views,
        include_iso=include_iso,
        iso_scale_factor=iso_scale_factor,
        convention=convention,
    )
    # Warn only when omitting the scale would truly give a legible fit (auto scale itself is
    # legible) but the requested scale is below the floor. A part illegible at every
    # page-fitting scale can't be helped by a bigger one, so nagging there would be false.
    if min_view < _MIN_VIEW_MM <= auto_scale * min_dim:
        safe = _MIN_VIEW_MM / min_dim
        # No stacklevel that reaches user code: this fires deep in _analyse, and the public
        # entry points (make_drawing, Sheet.export, build_drawing) sit at different depths.
        # The message is self-contained (names the scale, the projection, and the fix).
        message = (
            f"scale {SCALE!r} projects the smallest part dimension ({min_dim:.0f} mm) to "
            f"{min_view:.1f} mm, below the {_MIN_VIEW_MM:.0f} mm legibility floor — "
            f"annotations may crowd or overlap. Honouring the requested scale; use "
            f"scale ≥ {safe:.3g} or omit the scale for an automatic legible fit."
        )
        if advisories is not None:
            advisories.append(("legibility_floor_breached", message))
        if warn_advisory:
            warnings.warn(message, stacklevel=1)


@dataclass(frozen=True)
class _AnalysisRequest:
    step_file: Any
    out: Any
    scale: Any
    page: Any
    pmi: Any
    source: Any
    model: Any
    decorations: Any
    authored: Any
    requested: Any
    metadata: SheetMetadata
    _reuse: Any
    _required_tables: Any
    _arrangements: Any
    _views: Any
    _include_iso: Any
    _view_constraints: Any
    _plan_automatic_details: Any
    _framed_recognition: Any
    _document_input: Any
    _scale_from_prior_analysis: Any


@dataclass(frozen=True)
class _SourceState:
    reuse: Analysis | None
    convention: Any
    frame: Any
    sheet_margins: Any
    content_margins: Any
    title_block_width: Any
    margin: Any
    title_block_margins: Any
    part: Any
    source_part: Any
    recognition_frame: Any
    recognition_frame_decision: Any
    pmi_defaulted: Any
    pmi_mode: Any
    pmi_report: Any
    pmi_records: Any
    bb: Any
    x_size: Any
    y_size: Any
    z_size: Any
    cx: Any
    cy: Any
    cz: Any
    bbox_max: Any
    z_cyls: Any
    cross_cyls: Any
    z_diams: Any
    cross_diams: Any
    od_diam: Any
    od_axis: Any
    is_rotational: Any
    layout_model: Any
    recognition: Any
    recognition_evidence: Any


@dataclass(frozen=True)
class _ModelState:
    recognition_evidence: RecognitionEvidence | None
    _turned: Any
    _profiles: Any
    step_zs: Any
    shared_cyls: Any
    _draft_est: Any
    _arrow_length: Any
    _pad_around_text: Any
    holes: list[HoleRecord]
    patterns: list[BoltCircle | LinearArray | RectGrid]
    bosses: Any
    slots: Any
    pads: Any
    sizing_model: Any
    recognition_ownership: Any


@dataclass(frozen=True)
class _DemandState:
    strip_sizing_model: Any
    planned_manufacturing_schedule: Any
    bore_callout_width: Any
    layout_section: Any
    detail_footprints_for_scale: Any
    layout_table_sizes: Any
    layout_required_tables: Any
    planned_iso_scale: Any
    layout_step_zs: Any


@dataclass(frozen=True)
class _PlacementState:
    layout_advisories: Any
    ARRANGEMENT: Any
    layout_iso_scale: Any
    layout_iso_scale_authored: Any
    SCALE: Any
    PAGE_W: Any
    PAGE_H: Any
    TB_W: Any
    n_steps: Any
    strips: Any
    _g: Any
    fv_zones: Any
    pv_zones: Any
    sv_zones: Any


@dataclass(frozen=True)
class _SheetOptions:
    convention: str
    frame: bool
    sheet_margins: SheetMargins
    content_margins: SheetMargins
    title_block_width: float | None
    margin: SheetMargins | float
    title_block_margins: SheetMargins | None


def _sheet_options(r: _AnalysisRequest) -> _SheetOptions:
    projection = r.metadata.projection
    frame = r.metadata.frame
    zones = r.metadata.zones
    margin_left = r.metadata.margin_left
    margin_right = r.metadata.margin_right
    margin_top = r.metadata.margin_top
    margin_bottom = r.metadata.margin_bottom
    title_block_width = r.metadata.title_block_width
    convention = projection or "third"
    # The zone-grid ruler draws its ticks on the frame, so it implies one.
    frame = frame or zones
    # The content margin — raised by the sheet-frame band so scale/page selection and
    # placement both reserve room for the border. Computed up front so the choose_scale inside
    # step-count convergence sees it too.
    sheet_margins = _sheet_option_margins(
        margin_left=margin_left,
        margin_right=margin_right,
        margin_top=margin_top,
        margin_bottom=margin_bottom,
    )
    content_margins = sheet_margins.inset(_FRAME_BAND if frame else 0.0)
    title_block_width = _validated_title_block_width(title_block_width)
    custom_margins = any(
        value is not None for value in (margin_left, margin_right, margin_top, margin_bottom)
    )
    margin = content_margins if custom_margins else _content_margin(frame)
    title_block_margins = None
    if title_block_width is not None:
        title_block_margins = sheet_margins
    elif custom_margins:
        title_block_margins = replace(
            sheet_margins, right=sheet_margins.right + 1.0, bottom=sheet_margins.bottom + 1.0
        )
    return _SheetOptions(
        convention=convention,
        frame=frame,
        sheet_margins=sheet_margins,
        content_margins=content_margins,
        title_block_width=title_block_width,
        margin=margin,
        title_block_margins=title_block_margins,
    )


def _extract_source_pmi(step_file, source, pmi_mode, recognition_frame):
    pmi_report = None
    pmi_records = []
    pmi_source = None if isinstance(step_file, Shape) else step_file
    if pmi_source is None and source is not None:
        pmi_source = source
    if pmi_source is not None:
        from draftwright.pmi import PmiExtractionReport, extract_pmi_report

        try:
            pmi_report = extract_pmi_report(pmi_source, frame=recognition_frame)
            if pmi_mode != "off":
                pmi_records = list(pmi_report.records)
        except Exception as exc:
            _log.warning("PMI extraction failed: %s", exc)
            pmi_report = PmiExtractionReport(error=f"{type(exc).__name__}: {exc}")

    return pmi_report, pmi_records


def _prepare_source(r: _AnalysisRequest) -> _SourceState:
    step_file = r.step_file
    pmi = r.pmi
    source = r.source
    model = r.model
    decorations = r.decorations
    frame = r.metadata.frame
    _reuse = r._reuse
    _framed_recognition = r._framed_recognition
    _document_input = r._document_input
    margin_left = r.metadata.margin_left
    margin_right = r.metadata.margin_right
    margin_top = r.metadata.margin_top
    margin_bottom = r.metadata.margin_bottom
    title_block_width = r.metadata.title_block_width
    if _document_input is not None:
        if model is None or _framed_recognition:
            raise ValueError("document members require their sealed raw model")
        _document_input.validate(
            step_file, model.features if isinstance(model, PartModel) else model
        )
        if _reuse is None:
            _reuse = _document_input.analysis
        elif _reuse.part is not _document_input.analysis.part:
            raise ValueError("document analysis reuse names a foreign working solid")
    options = _sheet_options(r)
    convention = options.convention
    frame = options.frame
    sheet_margins = options.sheet_margins
    content_margins = options.content_margins
    title_block_width = options.title_block_width
    any(value is not None for value in (margin_left, margin_right, margin_top, margin_bottom))
    margin = options.margin
    title_block_margins = options.title_block_margins
    recognition: RecognitionResult | None
    recognition_evidence: RecognitionEvidence | None = None
    recognition_frame: PartFrame | None = None
    recognition_frame_decision: dict[str, object]
    if _reuse is not None:
        # Explicit-scale fallback changes only page-space layout. Reuse the immutable geometry,
        # STEP/PMI census, classification, and recognition waist from the requested trial rather
        # than importing and recognising the same part for each attempt.
        part = _reuse.part
        source_part = _reuse.source_part if _reuse.source_part is not None else part
        recognition_frame = cast(PartFrame | None, _reuse.recognition_frame)
        recognition_frame_decision = dict(
            _reuse.recognition_frame_decision
            or {"status": "not_evaluated", "gauge": None, "refusal_reason": None}
        )
        src = str(_reuse.step_file)
        pmi_defaulted = pmi is None
        pmi_mode = _reuse.pmi_mode if pmi_defaulted else pmi
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
        recognition_evidence = _reuse.recognition_evidence if layout_model is None else None
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

        # Declarations retain caller-coordinate authority and run no aggregate (ADR 4 (was 0011)).
        # Automatic builds make the framed/raw selection here, above every bbox, IR, planner,
        # projection and physical-lint consumer (ADR 3 (was 0020) activation).
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
                recognition, recognition_evidence = _raw_recognition(
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
                recognition, recognition_evidence = _raw_recognition(
                    part,
                    cylinders=(_gc.z_cyls, _gc.cross_cyls),
                    rotational=_gc.is_rotational,
                )

        # Semantic PMI census (AP242 only; separate read-only pass). Framed extraction receives
        # the provider frame before it classifies correlation topology, preserving tight local
        # boxes and arbitrary directions (ADR 3 (was 0020)). Even off mode inventories a STEP
        # source so it can report ignored authored PMI.
        #
        # An in-memory Shape has no AP242 document of its own, which used to end the matter — and
        # a `Sheet` ALWAYS holds one, so no script-built drawing reconciled its PMI at all, not
        # even to say it had not. `source` is the caller naming the STEP the solid was
        # read from; a generated script already opens exactly that path in its own `part =
        # import_step(...)` line, so the emitter can state it. It is the caller's claim, not a
        # proof the bytes produced this solid, so `pmi_source` records name and digest and the
        # stage summary reports them rather than leaving the link assumed.
        pmi_report, pmi_records = _extract_source_pmi(
            step_file, source, pmi_mode, recognition_frame
        )

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

    return _SourceState(
        reuse=_reuse,
        convention=convention,
        frame=frame,
        sheet_margins=sheet_margins,
        content_margins=content_margins,
        title_block_width=title_block_width,
        margin=margin,
        title_block_margins=title_block_margins,
        part=part,
        source_part=source_part,
        recognition_frame=recognition_frame,
        recognition_frame_decision=recognition_frame_decision,
        pmi_defaulted=pmi_defaulted,
        pmi_mode=pmi_mode,
        pmi_report=pmi_report,
        pmi_records=pmi_records,
        bb=bb,
        x_size=x_size,
        y_size=y_size,
        z_size=z_size,
        cx=cx,
        cy=cy,
        cz=cz,
        bbox_max=bbox_max,
        z_cyls=z_cyls,
        cross_cyls=cross_cyls,
        z_diams=z_diams,
        cross_diams=cross_diams,
        od_diam=od_diam,
        od_axis=od_axis,
        is_rotational=is_rotational,
        layout_model=layout_model,
        recognition=recognition,
        recognition_evidence=recognition_evidence,
    )


def _build_sizing_model(r: _AnalysisRequest, s: _SourceState) -> _ModelState:
    text_position = r.metadata.text_position
    text_orientation = r.metadata.text_orientation
    _reuse = s.reuse
    _document_input = r._document_input
    part = s.part
    pmi_mode = s.pmi_mode
    pmi_records = s.pmi_records
    bb = s.bb
    cx = s.cx
    cy = s.cy
    z_cyls = s.z_cyls
    cross_cyls = s.cross_cyls
    z_diams = s.z_diams
    od_diam = s.od_diam
    od_axis = s.od_axis
    is_rotational = s.is_rotational
    layout_model = s.layout_model
    recognition = s.recognition
    recognition_evidence = s.recognition_evidence
    # Step Z-levels feed both the step-height ladder and the page-sizing step
    # count. For a vertical (Z-axis) turned part, take them from the unified
    # turned-step model (ADR 1 (was 0008) step 1): it filters shoulders by the OD
    # silhouette, so an internal feature face — a blind bore's flat floor — is
    # never read as a phantom OD shoulder (the area-only filter in
    # recognise_face_levels admitted it). Prismatic and other parts keep the
    # general face-level scan, which recognise_turned_steps cannot replace (no
    # cylinders → no profile).
    # ADR 4 (was 0011) / ADR 3 (was 0017 §6): a declared model skips detection.  The gate has to sit
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
        # declared turned part — both are failures the gate must not introduce.
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
        # not become a phantom global prismatic ladder during page sizing.
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

    # Pass 1 (two-pass layout): measure annotation strip depths before
    # view positions are fixed.  font_size=3.0 is a fixed page-mm constant so
    # all annotation sizes are scale-independent — no circularity.
    # Construct the same draft preset used later in build_drawing() to read
    # arrow_length and pad_around_text from their authoritative source rather
    # than re-stating them as magic literals in the estimators.
    _draft_est = _dimension_draft(text_position, text_orientation)
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
    pads = list(recognition.pads) if recognition else []
    # Build the IR once, up front, so page/scale selection sizes from the SAME feature
    # model the renderers use — detected and declared parts share one sizing path and no
    # recogniser record reaches the sheet estimators (ADR 1 (was 0008)). A declared
    # model sizes from its own declaration (ADR 4 (was 0011)); otherwise the detected records are
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
    ownership_builder = (
        RecognitionOwnershipBuilder(recognition_evidence)
        if layout_model is None and _reuse is None and recognition_evidence is not None
        else None
    )
    sizing_model = (
        layout_model
        if layout_model is not None
        else cast(PartModel, _reuse.model)
        if _reuse is not None and _reuse.model is not None
        else _build_part_model_from_recognition(
            part,
            cast(RecognitionResult, recognition),
            evidence=recognition_evidence,
            ownership=ownership_builder,
            holes=holes,
            double_d_bores=double_d_bores,
            patterns=patterns,
            bosses=bosses,
            polygonal_bosses=polygonal_bosses,
            polygonal_stock=polygonal_stock,
            slots=slots,
            # Injected from the aggregate because `build_part_model` otherwise detects these
            # three itself, which is the duplicate scan ADR 3 (was 0017) exists to remove. On this
            # branch `recognition` is non-None by construction (it is the not-declared arm).
            slot_patterns=list(recognition.slot_patterns) if recognition else None,
            oriented_slots=list(recognition.oriented_slots) if recognition else None,
            oriented_slot_patterns=(
                list(recognition.oriented_slot_patterns) if recognition else None
            ),
            grooves=list(recognition.grooves) if recognition else None,
            risers=list(recognition.risers) if recognition else None,
            chamfers=list(recognition.chamfers) if recognition else None,
            fillets=list(recognition.fillets) if recognition else None,
            blends=list(recognition.blends) if recognition else None,
            circular_blind_steps=(list(recognition.circular_blind_steps) if recognition else None),
            paired_ramp_steps=list(recognition.paired_ramp_steps) if recognition else None,
            through_steps=list(recognition.through_steps) if recognition else None,
            plates=list(recognition.plates) if recognition else None,
            flats=list(recognition.flats) if recognition else None,
            section_recesses=list(recognition.section_recesses) if recognition else None,
            section_recess_patterns=(
                list(recognition.section_recess_patterns) if recognition else None
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
    recognition_ownership = (
        None
        if layout_model is not None
        else _reuse.recognition_ownership
        if _reuse is not None
        else ownership_builder.snapshot()
        if ownership_builder is not None
        else None
    )
    if _document_input is not None:
        # Declared sizing still reads only the member's authored model. Critique receives
        # the original conversion authority rather than acquiring another recognition run.
        recognition_evidence = _document_input.analysis.recognition_evidence
        recognition_ownership = _document_input.analysis.recognition_ownership
    return _ModelState(
        recognition_evidence=recognition_evidence,
        _turned=_turned,
        _profiles=_profiles,
        step_zs=step_zs,
        shared_cyls=shared_cyls,
        _draft_est=_draft_est,
        _arrow_length=_arrow_length,
        _pad_around_text=_pad_around_text,
        holes=holes,
        patterns=patterns,
        bosses=bosses,
        slots=slots,
        pads=pads,
        sizing_model=sizing_model,
        recognition_ownership=recognition_ownership,
    )


def _plan_sheet_demand(r: _AnalysisRequest, s: _SourceState, m: _ModelState) -> _DemandState:
    authored = r.authored
    requested = r.requested
    _required_tables = r._required_tables
    _views = r._views
    _view_constraints = r._view_constraints
    _plan_automatic_details = r._plan_automatic_details
    _document_input = r._document_input
    pmi_mode = s.pmi_mode
    bb = s.bb
    cx = s.cx
    cy = s.cy
    is_rotational = s.is_rotational
    _profiles = m._profiles
    _draft_est = m._draft_est
    _pad_around_text = m._pad_around_text
    sizing_model = m.sizing_model
    # Dimension feasibility and annotation footprints consume the authored set.
    # Derived-view dependencies still use sizing_model below; omitting an unrelated
    # envelope extent must not force its view back onto an authored sheet.
    strip_sizing_model = replace(
        sizing_model,
        authored_dimensions=tuple(authored)
        if authored is not None
        else sizing_model.authored_dimensions,
        requested_dimensions=tuple(requested) if requested else sizing_model.requested_dimensions,
    )
    # ADR 2 (was 0018) Phase 5.5: prove the chosen principal set can carry every approved
    # dimension before scale selection or projection.  A reduced view set is therefore a
    # re-plan, not the fixed three-view plan rendered into fewer views.
    sizing_groups = plan_dimensions(
        strip_sizing_model,
        planned_views=third_angle_view_names() if _views is None else _views,
    )
    # Compile ahead of sheet selection only when a derived Y chain or a schedule
    # needs approved measurements. Other parts keep their existing cheap path.
    needs_step_detail_plan = _plan_automatic_details and any(
        isinstance(feature, StepFeature) and feature.frame.axis in {"x", "y"}
        for feature in strip_sizing_model.features
    )
    approved_for_sizing = (
        compile_dimensions(strip_sizing_model, groups=sizing_groups)
        if strip_sizing_model.schedules or needs_step_detail_plan
        else None
    )
    schedule_tables = (
        approved_for_sizing.schedules
        if approved_for_sizing is not None and strip_sizing_model.schedules
        else ()
    )
    sizing_groups = annotation_groups(strip_sizing_model, sizing_groups)
    planned_manufacturing_schedule = manufacturing_schedule(
        strip_sizing_model,
        include_source_pmi=pmi_mode == "annotate",
    )
    bore_callout_width = _est_planned_bore_callout_width(
        sizing_groups,
        _draft_est,
        font_size=_FONT_SIZE,
        pad_around_text=_pad_around_text,
        include_source_pmi=_document_input is None or pmi_mode == "annotate",
        manufacturing_tags=(
            planned_manufacturing_schedule.tags_by_source
            if planned_manufacturing_schedule is not None
            else None
        ),
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
    # Generated Sheet scripts may author the settled principal view *set* while
    # leaving derived views automatic. That is not a request to disable the
    # automatic detail's pre-sheet footprint. Only an authored/augmented derived
    # view or a page-position constraint makes this reservation unsafe to infer.
    automatic_detail_space = _view_constraints is None or (
        _view_constraints.derived_source in (None, "automatic")
        and not _view_constraints.derived
        and not _view_constraints.added_derived
        and not _view_constraints.relations
        and not _view_constraints.pins
    )
    y_detail_footprints_for_scale = (
        _automatic_y_chain_detail_footprints(
            approved_for_sizing,
            bb,
            _draft_est,
            section_count=section_count,
            planned_views=_views,
        )
        if approved_for_sizing is not None and needs_step_detail_plan and automatic_detail_space
        else None
    )
    x_detail_footprints_for_scale = (
        _automatic_x_head_detail_footprints(
            approved_for_sizing,
            bb,
            _draft_est,
            section_count=section_count,
            planned_views=_views,
        )
        if approved_for_sizing is not None and needs_step_detail_plan and automatic_detail_space
        else None
    )

    def derived_view_footprints_for_scale(scale: float):
        y = y_detail_footprints_for_scale(scale) if y_detail_footprints_for_scale else ()
        x = x_detail_footprints_for_scale(scale) if x_detail_footprints_for_scale else ()
        # Both automatic producers currently propose DETAIL A. Until the
        # identifier pool is planned before sheet selection, neither box may
        # impersonate the other's renderer-owned request.
        return () if x and y else x or y

    detail_footprints_for_scale = (
        derived_view_footprints_for_scale
        if x_detail_footprints_for_scale or y_detail_footprints_for_scale
        else None
    )
    layout_table_sizes = _est_hole_table_sizes(
        sizing_model, bb, font_size=_FONT_SIZE, pad_around_text=_pad_around_text
    )
    layout_required_tables = tuple(_required_tables) + tuple(
        (
            _est_table_size(
                tuple(tuple(cell.text for cell in row) for row in schedule.rows),
                font_size=_FONT_SIZE,
                pad_around_text=_pad_around_text,
            ),
            schedule.prefer,
        )
        for schedule in schedule_tables
    )
    if planned_manufacturing_schedule is not None:
        layout_required_tables += (
            (
                _est_table_size(
                    planned_manufacturing_schedule.rows,
                    font_size=_FONT_SIZE,
                    pad_around_text=_pad_around_text,
                ),
                "tr",
            ),
        )
    # Drawing-wide source requirements are late furniture, but their text is
    # already known here. Give the page chooser the same measured rows the
    # renderer will draw, after the less-flexible detail/section reservations.
    # A redundant datum description is deliberately not a visible note; its
    # late safety fallback remains checked by the ordinary table fit/lint path.
    visible_document_notes = tuple(
        feature
        for feature in sizing_model.features
        if isinstance(feature, DocumentNote) and feature.on_drawing
    )
    if visible_document_notes and (_document_input is None or pmi_mode == "annotate"):
        layout_required_tables += (
            (
                _est_table_size(
                    document_note_rows(visible_document_notes),
                    font_size=_FONT_SIZE,
                    pad_around_text=_pad_around_text,
                ),
                "tr",
            ),
        )
    planned_iso_scale = _planned_iso_scale(_view_constraints)
    # Recognition's raw face levels can be owned by a plate, channel, or pocket and
    # removed from the final step ladder. Reserve only the levels present in that IR,
    # just as a declared replay does. Keep step_zs as the recognition diagnostic.
    layout_step_zs = _declared_step_zs(sizing_model, _profiles, bb)

    return _DemandState(
        strip_sizing_model=strip_sizing_model,
        planned_manufacturing_schedule=planned_manufacturing_schedule,
        bore_callout_width=bore_callout_width,
        layout_section=layout_section,
        detail_footprints_for_scale=detail_footprints_for_scale,
        layout_table_sizes=layout_table_sizes,
        layout_required_tables=layout_required_tables,
        planned_iso_scale=planned_iso_scale,
        layout_step_zs=layout_step_zs,
    )


@dataclass(frozen=True)
class _ScaleState:
    advisories: tuple[tuple[str, str], ...]
    scale: float
    page_w: float
    page_h: float
    title_block_width: float
    arrangement: str
    iso_scale: float | None
    iso_scale_authored: bool


def _select_sheet(
    r: _AnalysisRequest, s: _SourceState, m: _ModelState, d: _DemandState
) -> _ScaleState:
    scale = r.scale
    page = r.page
    text_position = r.metadata.text_position
    text_orientation = r.metadata.text_orientation
    _reuse = s.reuse
    _arrangements = r._arrangements
    _views = r._views
    _include_iso = r._include_iso
    _view_constraints = r._view_constraints
    _scale_from_prior_analysis = r._scale_from_prior_analysis
    title_block_width = r.metadata.title_block_width
    convention = s.convention
    title_block_width = s.title_block_width
    margin = s.margin
    title_block_margins = s.title_block_margins
    bb = s.bb
    x_size = s.x_size
    y_size = s.y_size
    z_size = s.z_size
    _arrow_length = m._arrow_length
    _pad_around_text = m._pad_around_text
    strip_sizing_model = d.strip_sizing_model
    bore_callout_width = d.bore_callout_width
    layout_section = d.layout_section
    detail_footprints_for_scale = d.detail_footprints_for_scale
    layout_table_sizes = d.layout_table_sizes
    layout_required_tables = d.layout_required_tables
    planned_iso_scale = d.planned_iso_scale
    layout_step_zs = d.layout_step_zs

    # Choose scale/page, iterating so the reserved step corridor matches the
    # number of steps the legibility gate will actually place — not the raw
    # face count. Otherwise a part with many sub-legible faces (e.g. a staircase
    # with 15 tiny treads) reserves a phantom step ladder that blocks a larger
    # scale. Seed conservatively (all faces), then re-gate at the chosen scale;
    # converges in a couple of rounds.
    def _measure_for_step_count(n_steps_i: int) -> StripDepths:
        return cap_planned_strips(
            _measure_strips(
                strip_sizing_model,
                n_steps_i,
                arrow_length=_arrow_length,
                pad_around_text=_pad_around_text,
                bore_callout_width=bore_callout_width,
                text_position=text_position,
                text_orientation=text_orientation,
            )
        )

    layout_advisories: list[tuple[str, str]] = []

    def _pick_for_step_count(n_steps_i: int, strips_i: StripDepths) -> _ScalePick:
        layout_advisories.clear()
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
            title_block_margins=title_block_margins,
            title_block_width=title_block_width,
            arrangements=_arrangements,
            advisories=layout_advisories,
            views=_views,
            include_iso=_include_iso,
            iso_scale_factor=planned_iso_scale,
            convention=convention,
            derived_view_footprints_for_scale=detail_footprints_for_scale,
        )

    scale_pick, strips_i, n_for_sizing = _converge_step_sizing(
        len(layout_step_zs),
        _measure_for_step_count,
        _pick_for_step_count,
        lambda scale_i: len(_legible_steps(layout_step_zs, bb.min.Z, scale_i)[0]),
    )
    SCALE, PAGE_W, PAGE_H, TB_W = scale_pick
    if _scale_from_prior_analysis and _reuse is not None and SCALE == _reuse.SCALE:
        # A fixed-scale recomposition cannot rediscover why the prior automatic
        # analysis chose that exact scale (or re-emit its caller-scale warning).
        # Retain those scale diagnostics; page-fit diagnostics are recomputed for
        # the newly selected arrangement instead.
        layout_advisories.extend(
            (code, message)
            for code, message in _reuse.layout_advisories
            if code in {"scale_fallback_applied", "legibility_floor_breached"}
            and code not in {current for current, _ in layout_advisories}
        )
    # The fourth dimension of the ADR 2 (was 0018 §5) choice, carried from `choose_scale` rather than
    # re-derived here: this call sees MEASURED strip depths where selection saw estimates, so
    # re-deriving would compose the sheet under a different arrangement than the one whose
    # feasibility was actually established.
    ARRANGEMENT = arrangement_of(scale_pick)
    # The staggered-side scheme reserves the upper-right corridor for defining
    # orthographic dimensions.  Its ISO is orientation-only (NTS), so project it
    # smaller from the outset rather than placing annotations against a temporary
    # sheet-scale obstacle and shrinking it after those placements are settled.
    layout_iso_scale, layout_iso_scale_authored = _resolved_iso_scale(
        ARRANGEMENT, planned_iso_scale
    )
    # Candidate profile recomposition pins the scale selected (and, if authored,
    # already validated) by the first analysis so it cannot search another sheet.
    # This is not a new caller request: an automatic fallback may legitimately be
    # below the hard floor that applies to newly authored scales.
    _validate_explicit_scale(
        None if _scale_from_prior_analysis else scale,
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
        title_block_margins=title_block_margins,
        title_block_width=title_block_width,
        warn_advisory=_reuse is None,
        advisories=layout_advisories,
        views=_views,
        include_iso=_include_iso,
        iso_scale_factor=layout_iso_scale,
        convention=convention,
    )
    return _ScaleState(
        advisories=tuple(layout_advisories),
        scale=SCALE,
        page_w=PAGE_W,
        page_h=PAGE_H,
        title_block_width=TB_W,
        arrangement=ARRANGEMENT,
        iso_scale=layout_iso_scale,
        iso_scale_authored=layout_iso_scale_authored,
    )


def _place_sheet(
    r: _AnalysisRequest, s: _SourceState, m: _ModelState, d: _DemandState
) -> _PlacementState:
    _reuse = s.reuse
    _arrangements = r._arrangements
    _views = r._views
    _include_iso = r._include_iso
    _view_constraints = r._view_constraints
    _scale_from_prior_analysis = r._scale_from_prior_analysis
    convention = s.convention
    margin = s.margin
    title_block_margins = s.title_block_margins
    bb = s.bb
    x_size = s.x_size
    y_size = s.y_size
    z_size = s.z_size
    cx = s.cx
    cy = s.cy
    cz = s.cz
    _arrow_length = m._arrow_length
    _pad_around_text = m._pad_around_text
    strip_sizing_model = d.strip_sizing_model
    bore_callout_width = d.bore_callout_width
    layout_section = d.layout_section
    detail_footprints_for_scale = d.detail_footprints_for_scale
    layout_table_sizes = d.layout_table_sizes
    layout_required_tables = d.layout_required_tables
    layout_step_zs = d.layout_step_zs

    picked = _select_sheet(r, s, m, d)
    layout_advisories = list(picked.advisories)
    SCALE, PAGE_W, PAGE_H, TB_W = (
        picked.scale,
        picked.page_w,
        picked.page_h,
        picked.title_block_width,
    )
    ARRANGEMENT = picked.arrangement
    layout_iso_scale = picked.iso_scale
    layout_iso_scale_authored = picked.iso_scale_authored
    # margin was computed up front (_content_margin(frame)) so scale selection already saw it.
    # Refine: apply the same legibility gate _auto_annotate uses for dim_step.
    n_steps = len(_legible_steps(layout_step_zs, bb.min.Z, SCALE)[0])
    strips = cap_planned_strips(
        _measure_strips(
            strip_sizing_model,
            n_steps,
            arrow_length=_arrow_length,
            pad_around_text=_pad_around_text,
            bore_callout_width=bore_callout_width,
        )
    )
    derived_footprints = (
        detail_footprints_for_scale(SCALE) if detail_footprints_for_scale is not None else ()
    )
    strips = _strips_for_derived_views(strips, derived_footprints)
    # View positions + iso empty-rectangle, shared with scale selection (_fits)
    # via _layout_geometry so placement and fit never diverge.  _fit_iso_view
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
        title_block_margins=title_block_margins,
        arrangement=ARRANGEMENT,
        views=_views,
        include_iso=_include_iso,
        iso_scale_factor=layout_iso_scale,
        convention=convention,
        derived_view_footprints=derived_footprints,
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
    if isinstance(_view_constraints, ViewConstraints):
        places = principal_placements(_g)
        for relation in _view_constraints.relations:
            if relation.subject in _g.planned_views and relation.reference in _g.planned_views:
                relation.validate(
                    places[relation.subject].bounds, places[relation.reference].bounds
                )
    FV_X = _g.FV_X
    FV_Y = _g.FV_Y
    PV_X = _g.PV_X
    PV_Y = _g.PV_Y
    SV_X = _g.SV_X
    SV_Y = _g.SV_Y
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
    if ARRANGEMENT == "staggered-side":
        # The aligned row spends headroom to clear the title block. Its exterior ladders
        # retain the full label height and 1 mm of clear air rather than the legacy 2.5 mm.
        # Scope this to the opt-in arrangement so established sheets remain byte-identical.
        for view_zones in (fv_zones, pv_zones, sv_zones):
            for side in ("above", "below", "left", "right"):
                strip = getattr(view_zones, side, None)
                if strip is not None:
                    strip.spacing = 1.0

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

    return _PlacementState(
        layout_advisories=layout_advisories,
        ARRANGEMENT=ARRANGEMENT,
        layout_iso_scale=layout_iso_scale,
        layout_iso_scale_authored=layout_iso_scale_authored,
        SCALE=SCALE,
        PAGE_W=PAGE_W,
        PAGE_H=PAGE_H,
        TB_W=TB_W,
        n_steps=n_steps,
        strips=strips,
        _g=_g,
        fv_zones=fv_zones,
        pv_zones=pv_zones,
        sv_zones=sv_zones,
    )


def _assemble_analysis(
    r: _AnalysisRequest, s: _SourceState, m: _ModelState, d: _DemandState, p: _PlacementState
) -> Analysis:
    return Analysis(
        layout_advisories=tuple(p.layout_advisories),
        arrangement=p.ARRANGEMENT,
        planned_views=r._views,
        planned_iso=r._include_iso,
        RV_X=p._g.RV_X,
        RV_Y=p._g.RV_Y,
        rv_zones=_build_rear_zones(p._g, s.margin, p.PAGE_H),
        derived_view_boxes=tuple(p._g.derived_view_boxes.items()),
        planned_iso_scale=p.layout_iso_scale,
        planned_iso_scale_authored=p.layout_iso_scale_authored,
        view_constraints=r._view_constraints,
        part=s.part,
        source_part=s.source_part,
        recognition_frame=s.recognition_frame,
        recognition_frame_decision=s.recognition_frame_decision,
        pmi_working_records=(
            tuple(s.pmi_records)
            if s.recognition_frame is not None and s.pmi_mode != "off"
            else None
        ),
        recognition=s.recognition,
        recognition_evidence=m.recognition_evidence,
        recognition_ownership=m.recognition_ownership,
        bb=s.bb,
        x_size=s.x_size,
        y_size=s.y_size,
        z_size=s.z_size,
        cx=s.cx,
        cy=s.cy,
        cz=s.cz,
        bbox_max=s.bbox_max,
        holes=m.holes,
        patterns=m.patterns,
        bosses=m.bosses,
        slots=m.slots,
        pads=m.pads,
        z_diams=s.z_diams,
        cross_diams=s.cross_diams,
        cyls=m.shared_cyls,
        prof=m._turned,
        profiles=m._profiles,
        od_diam=s.od_diam,
        is_rotational=s.is_rotational,
        od_axis=s.od_axis,
        step_zs=m.step_zs,
        layout_strips=p.strips,
        layout_n_steps=p.n_steps,
        layout_section=d.layout_section,
        layout_table_sizes=d.layout_table_sizes,
        layout_required_tables=d.layout_required_tables,
        sv_right=p._g.sv_right,
        iso_right_limit=p._g.iso_right,
        SCALE=p.SCALE,
        PAGE_W=p.PAGE_W,
        PAGE_H=p.PAGE_H,
        TB_W=p.TB_W,
        DIM_PAD=_DIM_PAD,
        margin=_content_margin(s.frame),
        sheet_margins=s.sheet_margins,
        content_margins=s.content_margins,
        title_block_width=s.title_block_width,
        title_block_margins=s.title_block_margins,
        x_offset=p._g.x_offset,
        FV_X=p._g.FV_X,
        FV_Y=p._g.FV_Y,
        PV_X=p._g.PV_X,
        PV_Y=p._g.PV_Y,
        SV_X=p._g.SV_X,
        SV_Y=p._g.SV_Y,
        proj=_Projector(
            fv_x=p._g.FV_X,
            fv_y=p._g.FV_Y,
            sv_x=p._g.SV_X,
            sv_y=p._g.SV_Y,
            pv_x=p._g.PV_X,
            pv_y=p._g.PV_Y,
            rv_x=p._g.RV_X,
            rv_y=p._g.RV_Y,
            cx=s.cx,
            cy=s.cy,
            cz=s.cz,
            scale=p.SCALE,
        ),
        ISO_X=p._g.ISO_X,
        ISO_Y=p._g.ISO_Y,
        iso_left_limit=p._g.iso_left,
        iso_bottom_limit=p._g.iso_bottom,
        iso_top_limit=p._g.iso_top,
        # View half-extents in page units (convenient for strip arithmetic)
        fv_hw=p._g.fv_hw,
        fv_hh=p._g.fv_hh,
        pv_hh=p._g.pv_hh,
        sv_hw=p._g.sv_hw,
        # Strip / zone layout model — the per-view strips ADR 2 (was 0009) placement reads
        fv_zones=p.fv_zones,
        pv_zones=p.pv_zones,
        sv_zones=p.sv_zones,
        step_file=r.step_file,
        title=r.metadata.title,
        number=r.metadata.number,
        tolerance=r.metadata.tolerance,
        drawn_by=r.metadata.drawn_by,
        material=r.metadata.material,
        date=r.metadata.date,
        revision=r.metadata.revision,
        company=r.metadata.company,
        approved_by=r.metadata.approved_by,
        document_type=r.metadata.document_type,
        sheet=r.metadata.sheet,
        frame=s.frame,
        projection=r.metadata.projection,
        projection_symbol=r.metadata.projection_symbol,
        text_position=r.metadata.text_position,
        text_orientation=r.metadata.text_orientation,
        leader_region=r.metadata.leader_region,
        projection_convention=s.convention,
        zones=r.metadata.zones,
        out=r.out,
        pmi_report=s.pmi_report,
        pmi_mode=s.pmi_mode,
        pmi_defaulted=s.pmi_defaulted,
        manufacturing_schedule=d.planned_manufacturing_schedule,
        document_member=r._document_input is not None,
        document_source_annotations=(
            r._document_input.source_annotations() if r._document_input is not None else ()
        ),
        # The sizing model IS the render model when detection ran (identical inputs by
        # construction); store it so the pipeline never detects twice
        # (ADR 1 (was 0008 Amdt 5)). A declared model (layout_model) is NOT stored: the
        # builder coerces + decorates the caller's model itself.
        model=m.sizing_model if s.layout_model is None else None,
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
    source=None,
    model=None,
    decorations=None,
    authored=None,
    requested=None,
    material=None,
    date="",
    revision="A",
    company="",
    approved_by="",
    document_type="",
    sheet="",
    frame: bool = False,
    projection: str | None = None,
    projection_symbol: bool = True,
    text_position: str = "inline",
    text_orientation: str = "aligned",
    leader_region: str = "auto",
    zones: bool = False,
    _reuse: Analysis | None = None,
    _required_tables=(),
    _arrangements: tuple[str, ...] | None = None,
    _views: tuple[str, ...] | None = None,
    _include_iso: bool = True,
    _view_constraints=None,
    _plan_automatic_details: bool = True,
    _framed_recognition: bool = False,
    _document_input=None,
    _scale_from_prior_analysis: bool = False,
    margin_left: float | None = None,
    margin_right: float | None = None,
    margin_top: float | None = None,
    margin_bottom: float | None = None,
    title_block_width: float | None = None,
    _sheet_metadata: SheetMetadata | None = None,
) -> Analysis:
    """Load STEP or use a build123d Shape, analyse geometry, compute layout.

    Returns an :class:`Analysis`.
    """
    metadata = (
        _sheet_metadata if _sheet_metadata is not None else SheetMetadata.from_mapping(locals())
    )
    r = _AnalysisRequest(
        step_file=step_file,
        out=out,
        scale=scale,
        page=page,
        pmi=pmi,
        source=source,
        model=model,
        decorations=decorations,
        authored=authored,
        requested=requested,
        metadata=metadata,
        _reuse=_reuse,
        _required_tables=_required_tables,
        _arrangements=_arrangements,
        _views=_views,
        _include_iso=_include_iso,
        _view_constraints=_view_constraints,
        _plan_automatic_details=_plan_automatic_details,
        _framed_recognition=_framed_recognition,
        _document_input=_document_input,
        _scale_from_prior_analysis=_scale_from_prior_analysis,
    )
    s = _prepare_source(r)
    m = _build_sizing_model(r, s)
    d = _plan_sheet_demand(r, s, m)
    p = _place_sheet(r, s, m, d)
    return _assemble_analysis(r, s, m, d, p)
