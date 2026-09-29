"""from_model — render planner output into placed annotations (ADR 1 (was 0008)).

The renderer back-end of the compiler: a `DimensionGroup` (read *purely from its
planned parameters* + the feature's metadata) becomes placed `HoleCallout` /
`Dimension` annotations via the existing projection (`Drawing.at`), layout search,
and rendering primitives. GD&T symbols (⌴/↧) are the helper's geometry, which is
exactly why the IR carries semantic `role`s, not glyph strings.

This lives in `annotations/` (not `model/`) so the IR package stays pure — it
imports *down* into `model` + `_core`, and is called by the orchestrator (ADR 1 (was 0008)
Amendment 3: one path, this is its render stage). Judged by **correctness** (lint),
not equivalence to the engine. All renderers here (`render_diameters`/`render_envelope`/
`render_locations`/`render_centermarks`/`render_step_lengths`/`render_slots`, and the
shared `hole_callout_spec`/`callout_from_spec` consumed by the holes pass) are wired
into production — the test-only `render_into`/`render_callouts` parallel was retired
once the holes epic landed (#251).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from functools import partial
from itertools import tee
from typing import Any, Literal, cast

from build123d_drafting import DatumFeature, FeatureControlFrame, SurfaceFinish, TextBlock
from build123d_drafting.helpers import (
    DEFAULT_FONT_PATH,
    Centerline,
    CenterMark,
    Dimension,
    HoleCallout,
    Leader,
    SafeDimension,
)

from draftwright._core import (
    _EDGE_ON,
    _END_ON,
    _EST_CHAR_WIDTH_EM,
    _MIN_STEP_DIM_MM,
    _SLOT_DIM_DEPTH,
    _SLOT_DIM_HEIGHT,
    _SLOT_DIM_STEP,
    _SLOT_DIM_WIDTH,
    _STRIP_SPACING,
    _WITNESS_LIFT_MM,
    DetailRequest,
    _analysis_margins,
    _classify_steps,
    _concentric_with_axis,
    _dim,
    _drawing_bounds,
    _first_free_index,
    _fmt,
    _font_safe_text,
    _frame_margins,
    _greedy_strip_ys,
    _iso_bbox,
    _legible_locations,
    _log,
    _solve_strip_ys,
    _text_line_spacing_em,
    _text_size,
    _title_block_box,
    _tol_suffix,
    _wrap_callout_text,
    layout_frame,
    supported_secondary_crop,
)
from draftwright._geometry import (
    _segment_clips_box,
    _segments_cross_or_overlap,
    _turned_profile_site,
)
from draftwright.annotation_layout_profile import layout_flag
from draftwright.annotations._common import (
    _LOC_SUBCHAIN,
    _SIZE_SUBCHAIN,
    CROSSABLE_TYPES,
    PRIORITY,
    CorridorCandidate,
    Escalation,
    InteriorDimensionJob,
    PlacementContext,
    _anno_box,
    _box_hits,
    _geom_box,
    _hole_location_coverage_fact,
    _ray_exit_dist,
    _same_location_ordinate,
    _with_hole_center_coverage,
    _with_hole_location_coverage,
    analytical_leader_lands_clear,
    annotation_ink_clear,
    carve_free_position,
    dim_footprint,
    dimension_candidate_geometry,
    full_strip_message,
    leader_callout_geometry,
    place_strip_candidates,
    prevent_dimension_label_ink,
    register_corridor,
    short_dimension_label_offset,
    strip_free_span,
    strip_obstacles,
    strip_occupants,
    view_label_clearance,
)
from draftwright.annotations._diameters import _diameter_column_left as _diameter_column_left
from draftwright.annotations._diameters import _diameter_row_below as _diameter_row_below
from draftwright.annotations._diameters import _diameter_source_bounds as _diameter_source_bounds
from draftwright.annotations._diameters import _diameter_step_anchor as _diameter_step_anchor
from draftwright.annotations._diameters import _leader_hole_clearance as _leader_hole_clearance
from draftwright.annotations._diameters import _manufacturing_suffix as _manufacturing_suffix
from draftwright.annotations._diameters import _place_what_fits as _place_what_fits_owner
from draftwright.annotations._diameters import (
    _render_diameter_leaders as _render_diameter_leaders_owner,
)
from draftwright.annotations._diameters import (
    _reroute_crossing_diameters as _reroute_crossing_diameters_owner,
)
from draftwright.annotations._diameters import render_diameters as _render_diameters_owner
from draftwright.annotations._edge_callouts import _chamfer_label as _chamfer_label
from draftwright.annotations._edge_callouts import _corner_candidates as _corner_candidates
from draftwright.annotations._edge_callouts import _fillet_label as _fillet_label
from draftwright.annotations._edge_callouts import chamfer_jobs as _edge_chamfer_jobs
from draftwright.annotations._edge_callouts import radius_jobs as _edge_radius_jobs
from draftwright.annotations._pocket_pad import _POCKET_LEAD_DIRS as _POCKET_LEAD_DIRS
from draftwright.annotations._pocket_pad import _pocket_label as _pocket_label
from draftwright.annotations._pocket_pad import pad_height_jobs as _pad_height_jobs
from draftwright.annotations._pocket_pad import pocket_jobs as _pocket_jobs

# The slot-family implementation owns witness geometry and corridor candidates. Keep
# the public pass here; the late radius jobs join the shared machined-leader solve.
from draftwright.annotations._slots import (
    _obround_radius_candidates as _obround_radius_candidates,
)
from draftwright.annotations._slots import (
    _record_slot_drop as _record_slot_drop,
)
from draftwright.annotations._slots import (
    _render_slot_dimensions,
)
from draftwright.annotations._slots import (
    _slot_end_radius_candidates as _slot_end_radius_candidates,
)
from draftwright.annotations._thin_profiles import register_channel_width, register_plate_thickness
from draftwright.annotations.angular import AngularDimension, AngularInk
from draftwright.annotations.leaders import (
    FeatureLeaderCandidate,
    FeatureLeaderJob,
    LeaderCandidateRegion,
    LeaderRegionPolicy,
    collect_feature_leader,
    feature_leader_candidates,
    material_penalty_units,
    place_feature_leader_jobs,
)
from draftwright.annotations.routed import RoutedLeader
from draftwright.auxiliary_layout import document_note_rows
from draftwright.layout import StripCandidate, plan_strip
from draftwright.leader_policy import effective_leader_region_policy
from draftwright.linting.ink_overlap import segments_of

# Keep this public import path for the holes renderer and callers. The IR owns the
# callout reading so the renderer and page estimator use the same specification.
# The explicit alias marks the re-export as intentional.
from draftwright.model.callout import _first as _first
from draftwright.model.callout import (
    bore_callout_value,
)
from draftwright.model.callout import (
    hole_callout_spec as hole_callout_spec,
)
from draftwright.model.callout import hole_callout_suffix as hole_callout_suffix
from draftwright.model.compiled import (
    ApprovedDimension,
    DimensionId,
    FeatureRef,
    resolve_feature,
    shared_location_text,
)
from draftwright.model.ir import (
    AUTHORED_DIMENSION_KINDS,
    ChamferFeature,
    CircularChannelFeature,
    FilletFeature,
    HoleFeature,
    PatternFeature,
    SlotFeature,
    _linear_projection_view,
    authored_dimension_target_view,
)
from draftwright.view_plan import views_showing


def callout_from_spec(spec, draft, count) -> HoleCallout | None:
    """Build a `HoleCallout` from a :func:`hole_callout_spec` dict. *count* is passed
    explicitly (the bare/test path uses the spec's own count; the engine pass uses its
    view-local hole count) — the single callout builder both the IR and the migrating
    engine pass share, so the bore/cbore/suffix mapping lives in one place (#238 B1).

    **This is the only place draftwright constructs a `HoleCallout`** — and it MUST
    pass each numeric value as a `_fmt` string, never a raw float: `HoleCallout`
    renders a float (``ø8.0``) wider than the equivalent string (``ø8``), which
    shifts placement and can drop callouts (#261). Keep the formatting here; the IR
    carries clean floats (no baked labels). The robust fix — `HoleCallout` formatting
    its own numeric inputs — is upstream in build123d-drafting-helpers."""
    if spec is None:
        return None

    def f(v, decimals=None):  # every value crosses as formatted text
        return _fmt(v, decimals) if v is not None else None

    dia = bore_callout_value(spec, lambda tolerance: _tol_suffix(tolerance, draft))

    def ft(value, key, decimals_key):
        """A formatted term with its own authored tolerance baked in.

        Every term of a compound callout can be toleranced, not just the bore. `.get()`
        because hand-built specs in tests omit the keys (#1234).
        """
        text = f(value, spec.get(decimals_key))
        return None if text is None else text + _tol_suffix(spec.get(key), draft)

    depth = ft(spec["depth"], "depth_tol", "depth_decimals")
    cbore_dia = ft(spec["cbore_dia"], "cbore_dia_tol", "cbore_dia_decimals")
    cbore_depth = ft(spec["cbore_depth"], "cbore_depth_tol", "cbore_depth_decimals")
    csink_dia = ft(spec.get("csink_dia"), "csink_dia_tol", "csink_dia_decimals")
    csink_angle = ft(spec.get("csink_angle"), "csink_angle_tol", "csink_angle_decimals")
    suffix = hole_callout_suffix(spec, lambda tolerance: _tol_suffix(tolerance, draft))
    visible_suffix = _wrap_callout_text(suffix, draft.font_size) if suffix else None
    callout = HoleCallout(
        dia,
        count=count,
        through=spec["through"],
        through_indicator=spec.get("through_indicator", "THRU"),
        depth=depth,
        cbore_dia=cbore_dia,
        cbore_depth=cbore_depth,
        csink_dia=csink_dia,
        # Every value crosses as a _fmt string — a raw float renders
        # "90.0°" and, worse, mismatches the width estimators' `_fmt` (they'd under-reserve).
        # `.get()`: hand-built specs (tolerance/fit tests) omit csk keys.
        csink_angle=csink_angle,
        suffix=visible_suffix,
        draft=draft,
    )
    # Retain token-level text positions from the exact layout recipe used by
    # ``HoleCallout``.  Whole-label centring is not equivalent for compound callouts:
    # vector symbols consume fixed-width cells while text tokens use font metrics.
    h = draft.font_size
    gap = 0.45 * h
    sym_w = h
    visible_tokens: list[tuple[str, str]] = []
    if count:
        visible_tokens.append(("text", f"{count}×"))
    visible_tokens.extend((("sym", "diameter"), ("text", dia)))
    if spec["through"]:
        if indicator := spec.get("through_indicator", "THRU"):
            visible_tokens.append(("text", indicator))
    elif depth is not None:
        visible_tokens.extend((("sym", "depth"), ("text", depth)))
    if cbore_dia is not None:
        visible_tokens.extend((("sym", "counterbore"), ("sym", "diameter"), ("text", cbore_dia)))
        if cbore_depth is not None:
            visible_tokens.extend((("sym", "depth"), ("text", cbore_depth)))
    if csink_dia is not None:
        visible_tokens.extend((("sym", "countersink"), ("sym", "diameter"), ("text", csink_dia)))
        if csink_angle is not None:
            visible_tokens.append(("text", f"× {csink_angle}°"))
    if visible_suffix:
        visible_tokens.append(("text", visible_suffix))

    font_path = getattr(draft, "font_path", DEFAULT_FONT_PATH)
    font_name = getattr(draft, "font", "Arial")
    x = 0.0
    token_specs: list[tuple[str, float, float, float, str | None, str, str, str, str]] = []
    for kind, value in visible_tokens:
        if kind == "sym":
            # Diameter has a faithful Unicode compatibility glyph.  The remaining
            # manufacturing symbols stay vector-only.
            if value == "diameter":
                token_specs.append(
                    (
                        "ø",
                        x + sym_w / 2.0,
                        0.0,
                        h,
                        font_path,
                        font_name,
                        "REGULAR",
                        "center",
                        "middle",
                    )
                )
            x += sym_w + gap
        else:
            lines = value.splitlines()
            spacing = h * _text_line_spacing_em(h, font_path, font_name)
            token_specs.extend(
                (
                    line,
                    x,
                    ((len(lines) - 1) / 2.0 - index) * spacing,
                    h,
                    font_path,
                    font_name,
                    "REGULAR",
                    "left",
                    "middle",
                )
                for index, line in enumerate(lines)
            )
            x += _text_size(value, h, font_path, font_name)[0] + gap
    cb = callout.bounding_box()
    callout.callout_height = cb.size.Y
    ccx, ccy = (cb.min.X + cb.max.X) / 2.0, (cb.min.Y + cb.max.Y) / 2.0
    callout.pdf_text_relative_specs = tuple(
        (value, px - ccx, py - ccy, size, path, name, style, h_align, v_align)
        for value, px, py, size, path, name, style, h_align, v_align in token_specs
    )
    # ``HoleCallout`` renders ISO symbols as geometry and consequently exposes an empty
    # helper-level label. Preserve an equivalent semantic string at this sole construction
    # seam so critique, audit and downstream tooling can identify what the geometry says.
    # Build it from the exact formatted arguments passed above: a second read from the model
    # or raw numbers here could drift from the visible callout (ADR 1 (was 0015) / ADR 4 (was 0016)).
    terms = []
    if count:
        terms.append(f"{count}×")
    terms.append(f"⌀{dia}")
    if spec["through"]:
        if indicator := spec.get("through_indicator", "THRU"):
            terms.append(indicator)
    elif depth is not None:
        terms.extend(("↧", depth))
    if cbore_dia is not None:
        terms.extend(("⌴", f"⌀{cbore_dia}"))
        if cbore_depth is not None:
            terms.extend(("↧", cbore_depth))
    if csink_dia is not None:
        terms.extend(("⌵", f"⌀{csink_dia}"))
        if csink_angle is not None:
            terms.extend(("×", f"{csink_angle}°"))
    if suffix:
        terms.append(suffix)
    callout.label = " ".join(terms)
    callout.measurements = tuple(spec.get("measurements", ()))
    callout.source_ids = tuple(spec.get("source_ids", ()))
    callout.source_measurements = tuple(spec.get("source_measurements", ()))
    callout.geometry_measurements = tuple(spec.get("geometry_measurements", ()))
    callout.geometry_qualifiers = tuple(spec.get("geometry_qualifiers", ()))
    callout.source_features = tuple(spec.get("source_features", ()))
    callout.covers_hole_requirements = tuple(
        requirement
        for requirement, covered in (
            ("bore.through", spec["through"] and bool(spec.get("through_indicator", "THRU"))),
            ("grouping.count", bool(count and count > 1)),
        )
        if covered
    )
    callout.covers_hole_requirements_by_feature = tuple(
        (owner, requirement, owner_count)
        for owner, owner_count in spec.get("owner_counts", ())
        if len(spec.get("owner_counts", ())) > 1
        for requirement in (
            "grouping.count",
            *(("bore.through",) if "bore.through" in callout.covers_hole_requirements else ()),
        )
    )
    profile_coverage = spec.get("profile_coverage")
    callout.covers_profiles = () if profile_coverage is None else (profile_coverage,)
    callout.profile_boundary = spec.get("profile_boundary")
    return callout


def render_slots(dwg, plan, a, *, ctx, only=None) -> int:
    """Place compiler-approved slot, pad, and pocket in-plane dimensions."""
    if not plan.of_kind("slot", "pad", "pocket"):
        return 0
    count, radius_jobs = _render_slot_dimensions(
        dwg, plan, a, ctx=ctx, only=only, reach=_leader_callout_reach(dwg.draft)
    )
    count += place_machined_leader_jobs(
        dwg,
        a,
        radius_jobs,
        noun="slot end radius",
        drop_code="slot_dim_dropped",
        ctx=ctx,
        joint=True,
        expand_lanes=False,
        region_policy=LeaderRegionPolicy.AUTO,
    )
    return count


# Corridor-ladder ordering (ADR 2 (was 0009) end state): feature-SIZE dims sit nearer the
# view (inner run), while datum-referenced LOCATION dims form one ascending outer chain.
# Step-height rungs measure from the common height datum, so they belong to that value-ordered
# run rather than to producer registration order. Segregating ordinary feature sizes
# keeps a slot length or local boss height from landing mid-ladder.
_OVERALL_SUBCHAIN = 2
_MANDATORY_OVERALL_PRIORITY = PRIORITY.MANDATORY
_PRINCIPAL_CHAIN_PRIORITY = PRIORITY.PRINCIPAL

# Minimum half of a bore's PAGE-projected diameter (mm) for its ø dim to fit across the circle
# in-plane; below this the circle is too small to letter inside, so the callout leaders out
# instead. Applied per bore-axis view (plan/side/front).
_MIN_INPLACE_BORE_HALF_MM = 4.0


def _location_candidate(
    dwg,
    ctx,
    name,
    *,
    view,
    span_key,
    label,
    distance,
    build,
    feature=None,
    measurement=None,
    pinned=False,
    footprint=None,
    interior_build=None,
    interior_geometry=None,
    location_coverage=(),
    hole_requirements=(),
    placement_side="above",
):
    """A :class:`CorridorCandidate` for a datum-referenced hole/pattern location dim.
    Location dims outrank a coincident slot-position line in dedup (#345) and form the
    outer, datum-distance-ordered run of the ladder (#346). After view planning has assigned
    the member to plan/side, it is force-kept within that view (policy B); only a physically
    full strip drops (``location_ref_dropped`` → hole-table escalate)."""

    def _placed(nm):
        # Only loose HoleFeatures have rows in the automatic scattered-hole table.
        # Pattern locations remain documented by their own dimensions/furniture; marking
        # them replaceable lets an unrelated successful table silently delete their
        # provenance.
        if getattr(feature, "kind", None) == "hole":
            ctx.coverage.cover_scattered_hole_doc(nm)
        if pinned:
            dwg.pin(nm)

    def _drop(nm):
        edge = "plan view" if view == "plan" else "side view"
        where = "beside" if placement_side in {"left", "right"} else placement_side
        ctx.record_issue(
            "warning",
            "location_ref_dropped",
            f"{nm} not placed (no room {where} the {edge})",
            measurement=measurement,
            hole_requirements=hole_requirements,
        )
        ctx.escalations.append(Escalation("location", view, nm, "strip_full"))

    return CorridorCandidate(
        name=name,
        build=lambda pos: _with_hole_location_coverage(build(pos), location_coverage),
        order=(_LOC_SUBCHAIN, distance, name),
        # A placed location may later be replaced by the scattered-hole table.
        on_place=_placed,
        on_drop=_drop,
        dedup=(view, span_key[0], span_key[1], label),
        precedence=3 if pinned else 2,
        priority=PRIORITY.MANDATORY if pinned else PRIORITY.AUTO,
        force=True,
        feature=feature,  # provenance (ADR 5 (was 0010)): the located hole/pattern
        measurement=measurement,  # which of its measurements this is
        footprint=footprint,  # analytical measure — no probe build
        interior_view=None if pinned else view,
        interior_side=None if pinned else placement_side,
        interior_build=(
            None
            if pinned or interior_build is None
            else lambda pos: _with_hole_location_coverage(interior_build(pos), location_coverage)
        ),
        interior_geometry=None if pinned else interior_geometry,
    )


def _circular_channel_axis_marks(dwg, ctx, dimensions):
    """Give approved axis coordinates a visible natural reference in each projection."""
    existing = {
        getattr(ctx.registry.named(name), "axis_reference_point", None)
        for name in ctx.registry.names()
        if name.startswith("m_seat_axis_")
    }
    for dimension in dimensions:
        view = dimension.view
        if view not in dwg.views:
            continue
        point = dwg.at(view, *dimension.span[1])
        key = (view, round(point[0], 9), round(point[1], 9))
        if key in existing:
            continue
        existing.add(key)
        prefix = f"m_seat_axis_{view}"
        name = f"{prefix}{_first_free_index(prefix, ctx.registry.names())}"
        mark = CenterMark(point, 2 * dwg.draft.arrow_length, dwg.draft)
        mark.axis_reference_point = key
        # Coaxial seats share this geometric reference; it carries no measurement credit
        # and belongs to no single feature whose drop could erase the other seats' axis.
        ctx.place(mark, name, view=view)


def render_circular_channel_locations(
    dwg, plan, a, *, ctx, only=None, pinned=None, axes=None
) -> int:
    """Register approved seat-axis offsets in the shared profile corridors."""
    only_refs = None if only is None else {FeatureRef(feature) for feature in only}
    pinned_refs = {FeatureRef(feature) for feature in (pinned or ())}
    want = {"x", "y", "z"} if axes is None else {axis.lower() for axis in axes}
    if not want <= {"x", "y", "z"}:
        raise ValueError("locate(): axes must be a subset of ('x', 'y', 'z')")
    groups: dict[tuple, list[ApprovedDimension]] = {}
    for dimension in plan.locations:
        if (
            dimension.role != CircularChannelFeature.LOCATION_STEM
            or dimension.discriminator not in want
        ):
            continue
        if only_refs is not None and dimension.ref not in only_refs:
            continue
        # Coaxial seats can share an ordinate even when their run stations differ.
        # Group only equal physical datum/axis ordinates in the same projection.
        axis = "xyz".index(dimension.discriminator)
        key = (dimension.view, axis, dimension.span[0][axis], dimension.span[1][axis])
        groups.setdefault(key, []).append(dimension)
    if not groups:
        return 0
    _circular_channel_axis_marks(
        dwg, ctx, (entry for entries in groups.values() for entry in entries)
    )
    used = set(ctx.registry.names())
    tier = dwg.draft.font_size + 2 * dwg.draft.pad_around_text
    for (view, axis, _start, _end), dimensions in groups.items():
        dimension = dimensions[0]
        mids = tuple(entry.id for entry in dimensions)
        label = shared_location_text(dimensions)
        side, stack = ("right", "x") if axis == 2 else ("above", "y")
        zones = {"front": a.fv_zones, "side": a.sv_zones}[view]
        prefix = f"m_seatloc_{'xyz'[axis]}"
        name = f"{prefix}{_first_free_index(prefix, used)}"
        used.add(name)

        def dropped(_name, mids=mids):
            ctx.record_issue(
                "warning",
                "circular_channel_location_dropped",
                "seat axis location was not placed (profile strip unavailable or full)",
                measurement=mids,
            )

        if view not in dwg.views:
            dropped(name)
            continue
        assert dimension.span is not None
        p1, p2 = (dwg.at(view, *point) for point in dimension.span)
        edge = max(p1[0], p2[0]) if side == "right" else max(p1[1], p2[1])
        pinned_group = any(entry.ref in pinned_refs for entry in dimensions)

        def build(pos, p1=p1, p2=p2, side=side, edge=edge, label=label):
            return _dim(p1, p2, side, abs(pos - edge), dwg.draft, label=label)

        def footprint(pos, p1=p1, p2=p2, side=side, edge=edge, label=label):
            return dim_footprint(p1, p2, side, abs(pos - edge), dwg.draft, label)

        def placed(name, pinned_group=pinned_group):
            if pinned_group:
                dwg.pin(name)

        register_corridor(
            ctx,
            (view, side),
            getattr(zones, side),
            view,
            stack,
            tier,
            CorridorCandidate(
                name=name,
                build=build,
                footprint=footprint,
                order=(_LOC_SUBCHAIN, dimension.value, name),
                on_place=placed,
                on_drop=dropped,
                # Hole and seat axes can share a physical datum ordinate. The
                # shared corridor keeps one visible location while retaining
                # both compiled measurement identities.
                dedup=(
                    view,
                    round(p1[0 if side == "above" else 1], 1),
                    round(p2[0 if side == "above" else 1], 1),
                    label,
                ),
                force=True,
                priority=PRIORITY.MANDATORY if pinned_group else PRIORITY.AUTO,
                feature=dimension.ref if len({entry.ref for entry in dimensions}) == 1 else None,
                measurement=mids,
            ),
        )
    return len(groups)


def render_locations(dwg, plan, a, *, ctx, only=None, pinned=None) -> int:
    """Baseline X/Y hole-location dims from the compiled plan (#238). The compiler decides
    the content (`plan.locations`: which refs survived, from which datum); this renderer owns
    the layout (Amendment 4) — X dims tier above the plan view; Y dims prefer above side
    and re-home vertically beside plan when side is not selected; both remain
    nearest-datum-first, legibility-gated, allocated from the existing strips;
    a ref with no room is dropped as `location_ref_dropped`. Replaces the engine's
    `_add_location_dims`. Returns the count placed.

    Reads `plan.locations`, not `plan_locations(model)`: a location prints a number, so an
    authored set that does not name one must not get one, and the only way to guarantee
    that for every renderer at once is for the withheld entries never to arrive (#925).
    Before this the pass took the raw planner list and drew every ref regardless — an
    authored set naming a single bore still produced its X/Y position dims.

    *only*, when given, restricts placement to refs whose source feature is in the set —
    the #426 finalize() path passes the recorded ``locate`` intents' features so the
    corridor solve runs over the user's edited subset. ``None`` (the auto-pass) places
    every ref, byte-identically.

    *pinned* carries the #511 first slice: deferred user ``locate(..., pin=True)`` calls
    remain first-class corridor candidates, but get higher survival/dedup priority and
    pin their placed names instead of being hand-added after the solve."""
    # This ladder consumes only Z-normal feature locations.  Non-Z pocket/pad entries
    # carry axis-local in-plane spans whose first endpoint is deliberately not the global
    # XY datum on both coordinates.  Derive the ladder datum only after excluding those
    # entries; taking it from ``plan.locations[0]`` lets feature ordering shift every
    # later Z-pad ordinate.
    n = render_circular_channel_locations(dwg, plan, a, ctx=ctx, only=only, pinned=pinned)
    approved = [
        loc for loc in plan.locations if loc.axis == "z" and loc.role != SlotFeature.LOCATION_STEM
    ]
    if not approved:
        return n
    draft = dwg.draft
    datum_x, datum_y = approved[0].span[0][0], approved[0].span[0][1]
    only_refs = None if only is None else {FeatureRef(f) for f in only}
    refs = []
    for loc in approved:
        if only_refs is not None and loc.ref not in only_refs:  # recorded subset only
            continue
        rx, ry = loc.span[1][0], loc.span[1][1]
        # A rotational part's on-axis (concentric) *hole* bore is located by the
        # centreline, not a position dim (matches the engine's feature_holes
        # filter). A pattern ref (role "location_pattern" — e.g. a bolt-circle
        # centre) is NOT filtered, even on the axis. A renderer-side filter only ever
        # REMOVES an approved entry (a drop), so it does not breach the boundary.
        if (
            loc.role == HoleFeature.LOCATION_STEM
            and a.is_rotational
            and _concentric_with_axis(a, rx, ry)
        ):
            continue
        # A slot's position is drawn by `render_slots`, from this same entry (it reads
        # `slot_positions` by role). Reaching it here too is not a second view of one
        # measurement, it is a DIFFERENT measurement with no compiled backing: this ladder
        # takes the plan X/Y of `span[1]` and prints an offset from the datum, while the
        # entry measures along the slot's long axis. It only ever arrived because the
        # `loc.axis != "z"` filter above reads `axis` as "Z-normal", and for a slot `axis`
        # is the LONG axis — so a Z-long slot fell through and an X- or Y-long one did not.
        # Exclude every slot orientation here so its position has one renderer.
        # Provenance (ADR 5 (was 0010)): the located feature. `resolve_feature` is the sanctioned
        # seam for exactly this — the corridor's feature map keys drop/annotations_of.
        # `loc.id` rides along as the measurement identity: the compiler already
        # minted it for this very entry, so the renderer records WHICH measurement it drew
        # rather than leaving the audit to infer it from the annotation's name.
        refs.append(
            (
                rx,
                ry,
                resolve_feature(loc.ref),
                loc.id,
                loc.discriminator,
                _hole_location_coverage_fact(loc),
                loc,
            )
        )
    if not refs:
        return n
    pinned_set = set(pinned or ())
    tier = draft.font_size + 2 * draft.pad_around_text

    # The automatic pass numbers location dimensions positionally. Finalize may
    # run after live locate dimensions already hold names, so it allocates the
    # first free index to avoid Drawing.add replacing one.
    _loc_used = set(ctx.registry.names()) if only is not None else None

    def _loc_name(prefix: str, i: int) -> str:
        if _loc_used is None:
            return f"{prefix}{i}"  # automatic positional name
        name = f"{prefix}{_first_free_index(prefix, _loc_used)}"
        _loc_used.add(name)
        return name

    def _discard_short_refs(refs, coordinate, datum, view):
        # Nearness on paper is not coincidence with the physical datum. A required
        # nonzero span that cannot be drawn must remain a recorded placement failure.
        short = [
            ref
            for ref in refs
            if 1e-6 < abs(ref[coordinate] - datum) and abs(ref[coordinate] - datum) * a.SCALE < 1.0
        ]
        if short:
            axis = "X" if coordinate == 0 else "Y"
            ctx.record_issue(
                "warning",
                "location_ref_dropped",
                f"{len(short)} {axis} location dim(s) project to less than 1 mm (use a detail view)",
                measurement=tuple(mid for ref in short for mid in ref[4]),
                hole_requirements=tuple(
                    (feature, parameter) for ref in short for feature, parameter, _point in ref[5]
                ),
            )
            ctx.escalations.append(Escalation("location", view, None, "illegible"))
        return [ref for ref in refs if ref not in short]

    # --- X locations: tier above the plan view ---
    PX, PY = a.proj.plan_x, a.proj.plan_y
    x_refs: list = []
    for r in refs:
        if r[4] not in (None, "x"):
            continue
        for u in x_refs:
            if _same_location_ordinate(r[0], u[0]):
                u[6].append(r[6])
                u[3] = u[3] or r[2] in pinned_set
                # Collapsing coincident Xs into one dim must ACCUMULATE what it draws
                # The survivor genuinely measures every collapsed feature's X.
                if r[4] in (None, "x") and r[3] is not None and r[3] not in u[4]:
                    u[4].append(r[3])
                if r[4] in (None, "x") and r[3] is not None:
                    fact = r[5]
                    if fact not in u[5]:
                        u[5].append(fact)
                break
        else:
            x_refs.append(
                [
                    r[0],
                    r[1],
                    r[2],
                    r[2] in pinned_set,
                    [r[3]] if r[3] is not None and r[4] in (None, "x") else [],
                    [r[5]] if r[3] is not None and r[4] in (None, "x") else [],
                    [r[6]],
                ]
            )
    x_refs = _discard_short_refs(x_refs, 0, datum_x, "plan")
    _x_drawable = {r[0] for r in x_refs if abs(r[0] - datum_x) * a.SCALE >= 1.0}
    _kept_x, _n_x_close = _legible_locations(_x_drawable, a.SCALE)
    if _n_x_close:
        dropped_x = [r for r in x_refs if r[0] in _x_drawable and r[0] not in set(_kept_x)]
        ctx.record_issue(
            "warning",
            "location_ref_dropped",
            f"{_n_x_close} X location dim(s) too closely spaced to dimension legibly "
            "(use a detail view)",
            measurement=tuple(mid for ref in dropped_x for mid in ref[4]),
            hole_requirements=tuple(
                (feature, parameter) for ref in dropped_x for feature, parameter, _point in ref[5]
            ),
        )
        ctx.escalations.append(Escalation("location", "plan", None, "illegible"))
    _kept_x_set = set(_kept_x)
    x_refs = [r for r in x_refs if r[0] not in _x_drawable or r[0] in _kept_x_set]
    # Register X-location dims into the shared plan-above corridor (ADR 2 (was 0009) end state),
    # so the slot pass feeds the SAME strip: a single solve_corridor drain
    # dedups a coincident slot-position line and orders the whole ladder — instead of each
    # pass carving around the other and interleaving. No alternate view for a plan-X
    # location, so a corridor-blocked dim is force-kept (policy B), not relocated; only a
    # physically full strip drops (→ location_ref_dropped, escalates the hole table).
    for i, (rx, ry, feat, pin_ref, mids, location_facts, location_entries) in enumerate(
        sorted(x_refs, key=lambda r: abs(r[0] - datum_x))
    ):
        if abs(rx - datum_x) * a.SCALE < 1.0:
            continue  # on the datum edge — nothing to dimension
        label = shared_location_text(location_entries)
        n += 1
        label_offset = short_dimension_label_offset(
            (PX(datum_x), PY(ry), 0), (PX(rx), PY(ry), 0), draft, label
        )
        # A single X-location dim shared by two *distinct* features at this X belongs to
        # neither exclusively — leave it unowned so drop cannot over-strip a sibling's
        # dimension and annotations_of never over-claims it (ADR 5 (was 0010)).
        _shared_x = any(
            o[4] in (None, "x") and _same_location_ordinate(o[0], rx) and o[2] != feat
            for o in refs
        )
        _xfeat = None if _shared_x else feat
        # The measurement does NOT follow the feature. Feature-unowned is an
        # ADR 5 (was 0010) *ownership* rule — it stops drop(feature) stripping a sibling's dim. It
        # says nothing about what the dim measures, and a shared dim measures BOTH features'
        # X location. Record every approved measurement in the tuple-valued
        # channel (ADR 4 (was 0016)) so audit can credit all owners.
        # One ADR 4 (was 0016) feature-level location identity per collapsed owner; the structured
        # location facts below carry that this particular visible member is X.
        _xmid = tuple(mids)
        # On the experimental staggered layout the plan can abut the top sheet margin.
        # The farther X stations may use the free exterior strip below the plan while
        # the nearest station keeps its established tier. Both are solver-owned strips.
        x_below = (
            layout_flag("plan_x_below", "DRAFTWRIGHT_EXPERIMENTAL_PLAN_X_BELOW")
            and i > 0
            and a.pv_zones.above.available < a.pv_zones.above.gap + tier
            and a.pv_zones.below.available >= a.pv_zones.below.gap + tier
        )
        x_side = "below" if x_below else "above"
        x_zone = a.pv_zones.below if x_below else a.pv_zones.above
        x_offset = (
            (lambda pos, _ry=ry: PY(_ry) - pos) if x_below else (lambda pos, _ry=ry: pos - PY(_ry))
        )
        register_corridor(
            ctx,
            ("plan", x_side),
            x_zone,
            "plan",
            "y",
            tier,
            _location_candidate(
                dwg,
                ctx,
                _loc_name("m_locx", i),
                view="plan",
                span_key=(round(PX(datum_x), 1), round(PX(rx), 1)),
                label=label,
                distance=abs(rx - datum_x),
                build=lambda pos, _rx=rx, _ry=ry, _label=label, _offset=label_offset, _side=x_side, _offset_fn=x_offset: (
                    _dim(
                        (PX(datum_x), PY(_ry), 0),
                        (PX(_rx), PY(_ry), 0),
                        _side,
                        _offset_fn(pos),
                        draft,
                        label=_label,
                        label_offset_x=_offset,
                    )
                ),
                feature=_xfeat,
                measurement=_xmid,
                location_coverage=location_facts,
                hole_requirements=tuple(
                    (feature, parameter) for feature, parameter, _point in location_facts
                ),
                pinned=pin_ref,
                footprint=lambda pos, _rx=rx, _ry=ry, _label=label, _offset=label_offset, _side=x_side, _offset_fn=x_offset: (
                    dim_footprint(
                        (PX(datum_x), PY(_ry), 0),
                        (PX(_rx), PY(_ry), 0),
                        _side,
                        _offset_fn(pos),
                        draft,
                        _label,
                        label_offset_x=_offset,
                    )
                ),
                interior_build=lambda pos, _rx=rx, _ry=ry, _label=label, _offset=label_offset: (
                    _dim(
                        (PX(datum_x), PY(_ry), 0),
                        (PX(_rx), PY(_ry), 0),
                        "below",
                        abs(pos - PY(_ry)),
                        draft,
                        label=_label,
                        label_offset_x=_offset,
                    )
                ),
                interior_geometry=lambda pos, _rx=rx, _ry=ry, _label=label, _offset=label_offset: (
                    dimension_candidate_geometry(
                        (PX(datum_x), PY(_ry), 0),
                        (PX(_rx), PY(_ry), 0),
                        "below",
                        abs(pos - PY(_ry)),
                        draft,
                        _label,
                        label_offset_x=_offset,
                    )
                ),
            ),
        )

    # --- Y locations: above side, or vertically beside plan when side is absent ---

    # Both are the same face-on Z-feature location requirement. The fixed topology preferred
    # side because Y runs horizontally there; ADR 2 (was 0018) reduced view sets must not retain side
    # solely for that presentation choice, so plan's vertical Y axis is the fallback.
    side_planned = "side" in dwg.views
    SX, SZ = a.proj.side_x, a.proj.side_z
    side_top = SZ(a.bb.max.Z) if side_planned else 0.0
    iso_x0, iso_y0, _, _ = _iso_bbox(dwg)
    y_refs: list = []
    for r in refs:
        if r[4] not in (None, "y"):
            continue
        for u in y_refs:
            if _same_location_ordinate(r[1], u[1]):
                u[6].append(r[6])
                u[3] = u[3] or r[2] in pinned_set
                if r[4] in (None, "y") and r[3] is not None and r[3] not in u[4]:
                    u[4].append(r[3])  # accumulate, as in the X loop
                if r[4] in (None, "y") and r[3] is not None:
                    fact = r[5]
                    if fact not in u[5]:
                        u[5].append(fact)
                break
        else:
            y_refs.append(
                [
                    r[0],
                    r[1],
                    r[2],
                    r[2] in pinned_set,
                    [r[3]] if r[3] is not None and r[4] in (None, "y") else [],
                    [r[5]] if r[3] is not None and r[4] in (None, "y") else [],
                    [r[6]],
                ]
            )
    y_refs = _discard_short_refs(y_refs, 1, datum_y, "side" if side_planned else "plan")
    _y_drawable = {r[1] for r in y_refs if abs(r[1] - datum_y) * a.SCALE >= 1.0}
    _kept_y, _n_y_close = _legible_locations(_y_drawable, a.SCALE)
    if _n_y_close:
        dropped_y = [r for r in y_refs if r[1] in _y_drawable and r[1] not in set(_kept_y)]
        ctx.record_issue(
            "warning",
            "location_ref_dropped",
            f"{_n_y_close} Y location dim(s) too closely spaced to dimension legibly "
            "(use a detail view)",
            measurement=tuple(mid for ref in dropped_y for mid in ref[4]),
            hole_requirements=tuple(
                (feature, parameter) for ref in dropped_y for feature, parameter, _point in ref[5]
            ),
        )
        ctx.escalations.append(
            Escalation("location", "side" if side_planned else "plan", None, "illegible")
        )
    _kept_y_set = set(_kept_y)
    y_refs = [r for r in y_refs if r[1] not in _y_drawable or r[1] in _kept_y_set]
    # Cap the side-above strip below the iso view so Y-location dims never run under it
    # (the carve respects outer_limit); the dim_pitch_side dims are obstacles
    # the carve avoids directly.
    if (
        side_planned
        and y_refs
        and any(SX(ry) + 10 > iso_x0 - 4 for _, ry, _feat, _pin, _mids, _facts, _entries in y_refs)
    ):
        a.sv_zones.above.outer_limit = min(a.sv_zones.above.outer_limit, iso_y0 - 4)
    for i, (rx, ry, feat, pin_ref, mids, location_facts, location_entries) in enumerate(
        sorted(y_refs, key=lambda r: abs(r[1] - datum_y))
    ):
        if abs(ry - datum_y) * a.SCALE < 1.0:
            continue
        label = shared_location_text(location_entries)
        n += 1
        # A shared Y location is unowned for the same reason as shared X.
        _shared_y = any(
            o[4] in (None, "y") and _same_location_ordinate(o[1], ry) and o[2] != feat
            for o in refs
        )
        _yfeat = None if _shared_y else feat
        # Every collapsed feature-level location; the structured facts carry Y (see X above).
        _ymid = tuple(mids)
        if side_planned:
            view, direction, strip, stack = "side", "above", a.sv_zones.above, "y"
            edge = side_top
            pa = (SX(datum_y), edge, 0)
            pb = (SX(ry), edge, 0)
            span_key = (round(pa[0], 1), round(pb[0], 1))
        else:
            view, stack = "plan", "x"
            if a.pv_zones.right is not None:
                direction, strip = "right", a.pv_zones.right
                edge = PX(a.bb.max.X)
            else:
                direction, strip = "left", a.pv_zones.left
                edge = PX(a.bb.min.X)
            pa = (edge, PY(datum_y), 0)
            pb = (edge, PY(ry), 0)
            span_key = (round(pa[1], 1), round(pb[1], 1))
        label_offset = (
            short_dimension_label_offset(pa, pb, draft, label) if view == "side" else 0.0
        )
        interior_direction = {
            "above": "below",
            "below": "above",
            "right": "left",
            "left": "right",
        }[direction]
        register_corridor(
            ctx,
            (view, direction),
            strip,
            view,
            stack,
            tier,
            _location_candidate(
                dwg,
                ctx,
                _loc_name("m_locy", i),
                view=view,
                span_key=span_key,
                label=label,
                distance=abs(ry - datum_y),
                build=lambda pos, _pa=pa, _pb=pb, _direction=direction, _edge=edge, _label=label, _offset=label_offset: (
                    _dim(
                        _pa,
                        _pb,
                        _direction,
                        abs(pos - _edge),
                        draft,
                        label=_label,
                        label_offset_x=_offset,
                    )
                ),
                feature=_yfeat,
                measurement=_ymid,
                location_coverage=location_facts,
                hole_requirements=tuple(
                    (feature, parameter) for feature, parameter, _point in location_facts
                ),
                placement_side=direction,
                pinned=pin_ref,
                footprint=lambda pos, _pa=pa, _pb=pb, _direction=direction, _edge=edge, _label=label, _offset=label_offset: (
                    dim_footprint(
                        _pa,
                        _pb,
                        _direction,
                        abs(pos - _edge),
                        draft,
                        _label,
                        label_offset_x=_offset,
                    )
                ),
                interior_build=lambda pos, _pa=pa, _pb=pb, _direction=interior_direction, _edge=edge, _label=label, _offset=label_offset: (
                    _dim(
                        _pa,
                        _pb,
                        _direction,
                        abs(pos - _edge),
                        draft,
                        label=_label,
                        label_offset_x=_offset,
                    )
                ),
                interior_geometry=lambda pos, _pa=pa, _pb=pb, _direction=interior_direction, _edge=edge, _label=label, _offset=label_offset: (
                    dimension_candidate_geometry(
                        _pa,
                        _pb,
                        _direction,
                        abs(pos - _edge),
                        draft,
                        _label,
                        label_offset_x=_offset,
                    )
                ),
            ),
        )
    return n


def render_centermarks(dwg, furniture_groups, *, ctx) -> int:
    """A centre mark on every hole (plain holes + each pattern member), in the view
    normal to the hole's axis (`_END_ON`), sized by its diameter — the IR migration
    of the engine's inline centre-mark loop. Returns the count placed.

    The size comes off the FEATURE, not the planned bore parameter (ADR 4 (was 0016) / #875).
    A centre mark is furniture derived from the hole's physical size; it is not a displayed
    value, so suppressing the bore dimension must not shrink it. Reading the parameter here
    made a suppressed ⌀20 collapse from a 42 mm mark to the 2.5 mm floor — the governing rule
    (facts live on the feature, parameters carry display values) applied to geometry rather
    than to text."""
    n = 0
    for g in furniture_groups:
        feat = g.feature
        if not isinstance(feat, HoleFeature | PatternFeature):
            continue
        hole = feat.member if isinstance(feat, PatternFeature) else feat
        dia = hole.diameter or 0.0
        size = max(2.5, dia * dwg.scale + 2.0)
        view = g.view or _END_ON.get(feat.frame.axis, "plan")
        if view not in getattr(dwg, "views", (view,)) and all(
            dimension.suppressed for dimension in g.dims
        ):
            continue  # No value-bearing requirement earned furniture in this projection.
        members = feat.members or (g.anchor,)
        for loc in members:
            px, py, *_ = dwg.at(view, *loc)
            ctx.place(
                _with_hole_center_coverage(
                    CenterMark((px, py, 0), size, dwg.draft), feat, loc, view
                ),
                f"m_cm{n}",
                view=view,
                feature=feat,
            )
            n += 1
    return n


def _render_diameter_leaders(
    dwg, a, indexed_buckets, *, prefix, start, ctx, include_source_pmi=True
) -> int:
    """Submit external diameter jobs through the established late leader solve."""
    return _render_diameter_leaders_owner(
        dwg,
        a,
        indexed_buckets,
        prefix=prefix,
        start=start,
        ctx=ctx,
        include_source_pmi=include_source_pmi,
        radial_candidates=_radial_candidates,
        place_jobs=place_machined_leader_jobs,
        leader_reach=_leader_callout_reach,
    )


def _reroute_crossing_diameters(dwg, *, ctx) -> int:
    """Use the live material predicate for the established diameter reroute."""
    return _reroute_crossing_diameters_owner(dwg, ctx=ctx, material_penalty=material_penalty_units)


def _place_what_fits(specs, axis: int, min_gap: float, lo: float, hi: float):
    """Use the live strip-solver bindings at the stable diameter import path."""
    return _place_what_fits_owner(
        specs,
        axis,
        min_gap,
        lo,
        hi,
        solve_strip_ys=_solve_strip_ys,
        greedy_strip_ys=_greedy_strip_ys,
    )


def render_diameters(dwg, plan, a, *, ctx, only=None) -> int:
    """Render approved step and boss diameters through the shared solve."""
    return _render_diameters_owner(
        dwg,
        plan,
        a,
        ctx=ctx,
        only=only,
        radial_candidates=_radial_candidates,
        place_jobs=place_machined_leader_jobs,
        leader_reach=_leader_callout_reach,
        reroute_crossing=lambda dwg, *, ctx: _reroute_crossing_diameters(dwg, ctx=ctx),
        place_what_fits=_place_what_fits,
    )


def _leader_callout_reach(draft) -> float:
    """Outward leader length past the tip (label→elbow) for a machined-feature callout: one
    line height plus six text-pads. Shared so all five callout passes reach the same distance
    into the margin."""
    return float(draft.font_size + 6 * draft.pad_around_text)


def _flat_candidates(dwg, view, vb, members, reach, *, provenances):
    """Keep the established centre-out lead first, then try true margin escapes.

    A flat is a corner feature on its own stock, but not necessarily on the aggregate view:
    parallel stock can put an inner lobe well inside ``vb``.  The old single candidate moved
    only *reach* from that lobe and left the label intersecting the aggregate silhouette.
    ``_radial_candidates`` advances to the view boundary before adding the same reach, so its
    fallbacks use available margin without changing the preferred placement of ordinary flats.
    """
    members = list(members)
    provenances = list(provenances)
    yield from _corner_candidates(
        dwg,
        view,
        vb,
        members,
        reach,
        provenances=provenances,
    )
    for member, provenance in zip(members, provenances):
        for tip, elbow, _owner in _radial_candidates(
            dwg,
            view,
            vb,
            member,
            reach,
            provenance=provenance,
        ):
            # A shared callout has no single owner. Do not let the radial
            # candidate's geometry fallback attach its FeatureFacts projection.
            yield tip, elbow, provenance


def place_machined_leader_jobs(
    dwg,
    a,
    jobs,
    *,
    noun,
    drop_code,
    ctx,
    geom_clear=False,
    joint=False,
    expand_lanes=True,
    region_policy=LeaderRegionPolicy.EXTERIOR,
    source_ids_by_name=None,
    source_drop_severity="warning",
    priority=0.0,
    straight_only_names=frozenset(),
) -> int:
    """Lower every machined callout to the one shared ``FeatureLeaderJob`` path.

    Post-drain families join the canonical late inventory.  Pre-drain families
    are solved immediately through that same analytical machinery with its lazy
    producer floor, preserving both their semantic stage and first-clear order.
    ``region_policy`` lets a later compiler stage opt a complete feature family
    into the shared region adapter without changing other producers.
    """

    source_ids_by_name = source_ids_by_name or {}
    family_region_policy = LeaderRegionPolicy(region_policy)
    late_inventory = joint and getattr(ctx, "feature_leaders", None) is not None
    feature_jobs = []
    interior_clearance_by_view = {}
    for name, view, silhouette, label, raw_candidates, measurement in jobs:
        straight_only = name in straight_only_names
        visible_label = _wrap_callout_text(str(label), dwg.draft.font_size)
        (
            joint_interior_anchors,
            joint_exterior_anchors,
            fallback_interior_anchors,
            fallback_exterior_anchors,
            recovery_anchors,
        ) = tee(iter(raw_candidates), 5)
        label_width, label_height = _text_size(
            visible_label,
            float(dwg.draft.font_size),
            getattr(dwg.draft, "font_path", DEFAULT_FONT_PATH),
            getattr(dwg.draft, "font", "Arial"),
        )
        label_box = (
            (0.0, 0.0, label_width, label_height)
            if label_width > 0.0 and label_height > 0.0
            else None
        )

        def _analytical_geometry(tip, elbow, _feature, *, _label_box=label_box):
            if _label_box is None:
                return None
            return leader_callout_geometry(tip, elbow, dwg.draft, callout_box=_label_box)

        effective_region_policy = effective_leader_region_policy(
            family_region_policy,
            getattr(a, "leader_region", "auto"),
        )
        if (
            effective_region_policy is not LeaderRegionPolicy.EXTERIOR
            and view not in interior_clearance_by_view
        ):
            interior_clearance_by_view[view] = view_label_clearance(dwg, view)
        interior_label_clear = interior_clearance_by_view.get(view)

        def _lane_candidates(
            _interior_anchors=joint_interior_anchors,
            _exterior_anchors=joint_exterior_anchors,
            _silhouette=silhouette,
            _analytical_geometry=_analytical_geometry,
            _interior_label_clear=interior_label_clear,
            _region_policy=effective_region_policy,
            _straight_only=straight_only,
        ):
            if _straight_only:
                yield from _exterior_anchors
                return
            spacing = dwg.draft.font_size + 2 * dwg.draft.pad_around_text
            interior_count = 0
            if late_inventory and _region_policy is not LeaderRegionPolicy.EXTERIOR:
                # Expand the complete semantic job in one call.  Calling the adapter
                # once per physical anchor would turn its per-feature cap into
                # ``anchors × cap`` for grouped fillets and polygonal bosses.
                for candidate in feature_leader_candidates(
                    _interior_anchors,
                    region_policy=LeaderRegionPolicy.INTERIOR,
                    silhouette=_silhouette,
                    analytical_geometry=_analytical_geometry,
                    draft=dwg.draft,
                    interior_label_clear=_interior_label_clear,
                ):
                    yield candidate
                    interior_count += 1
                if _region_policy is LeaderRegionPolicy.INTERIOR:
                    return
            for tip, elbow, feature in _exterior_anchors:
                if interior_count:
                    # Once this job has proven projected-clear interior options,
                    # retain one exterior alternative per semantic anchor instead
                    # of multiplying every anchor by nine compatibility lanes.  A
                    # job with no interior option keeps all exterior lanes below;
                    # AUTO's resource fallback preserves those exterior options.
                    yield (tip, elbow, feature)
                    continue
                dx, dy = float(elbow[0]) - float(tip[0]), float(elbow[1]) - float(tip[1])
                length = math.hypot(dx, dy)
                if length <= 1e-12:
                    yield (tip, elbow, feature)
                    continue
                ux, uy = dx / length, dy / length
                px, py = -dy / length, dx / length
                for outward, lane in (
                    (0, 0),
                    (0, 1),
                    (0, -1),
                    (0, 2),
                    (0, -2),
                    (1, 0),
                    (2, 0),
                    (1, 1),
                    (1, -1),
                ):
                    yield (
                        tip,
                        (
                            float(elbow[0]) + ux * spacing * outward + px * spacing * lane,
                            float(elbow[1]) + uy * spacing * outward + py * spacing * lane,
                            0,
                        ),
                        feature,
                    )

        source_features = tuple(
            {
                id(getattr(identity, "feature", None)): getattr(identity, "feature", None)
                for identity in measurement
                if getattr(identity, "feature", None) is not None
            }.values()
        )

        def _decorate(
            leader,
            *,
            _source_features=source_features,
            _measurement=measurement,
            _semantic_label=label,
            _visible_label=visible_label,
        ):
            if _semantic_label != _visible_label:
                leader.label = _semantic_label
                leader.pdf_text = _visible_label
            leader.source_features = _source_features
            grouping_features = {
                id(identity.feature): identity.feature
                for identity in _measurement
                if getattr(identity, "parameter", None) == "grouping.count"
                and getattr(identity, "feature", None) is not None
            }
            if grouping_features:
                leader.covers_count = len(grouping_features)
            return leader

        def _build(tip, elbow, _feature, *, _label=visible_label, _decorate_fn=_decorate):
            return _decorate_fn(
                Leader(tip=(tip[0], tip[1], 0), elbow=elbow, label=_label, draft=dwg.draft)
            )

        def _recover(
            _anchors=recovery_anchors,
            _view=view,
            _label=visible_label,
            _label_size=(label_width, label_height),
            _build_fn=_build,
            _decorate_fn=_decorate,
        ):
            for raw in _anchors:
                candidate = (
                    raw
                    if isinstance(raw, FeatureLeaderCandidate)
                    else FeatureLeaderCandidate(raw[0], raw[1], raw[2])
                )
                tip, original_elbow, feature = (
                    candidate.tip,
                    candidate.elbow,
                    candidate.feature,
                )
                search_tip = tip
                normal_stub = None
                if candidate.radial_target is not None:
                    dx = float(original_elbow[0]) - float(tip[0])
                    dy = float(original_elbow[1]) - float(tip[1])
                    length = math.hypot(dx, dy)
                    if length <= 1e-9:
                        continue
                    stub_length = min(
                        length,
                        max(
                            dwg.draft.arrow_length, dwg.draft.font_size + dwg.draft.pad_around_text
                        ),
                    )
                    normal_stub = (
                        float(tip[0]) + dx * stub_length / length,
                        float(tip[1]) + dy * stub_length / length,
                    )
                    search_tip = normal_stub

                def build_at(elbow, _tip=tip, _feature=feature):
                    if normal_stub is not None:
                        return _decorate_fn(
                            RoutedLeader(_tip, (normal_stub,), elbow, _label, dwg.draft)
                        )
                    return _build_fn(_tip, (*elbow, 0), _feature)

                def build_routed(bends, elbow, _tip=tip):
                    route_bends = (normal_stub, *bends) if normal_stub is not None else bends
                    return _decorate_fn(RoutedLeader(_tip, route_bends, elbow, _label, dwg.draft))

                annotation = _sheet_leader_fallback(
                    dwg,
                    search_tip,
                    _view,
                    build_at,
                    build_routed,
                    _label_size,
                )
                if annotation is not None:
                    return annotation, feature
            return None

        def _fallback_accept(
            candidate,
            obstacles,
            page,
            *,
            _silhouette=silhouette,
            _geom_clear=geom_clear,
            _label=label,
        ):
            if candidate.region is LeaderCandidateRegion.INTERIOR:
                return True
            return analytical_leader_lands_clear(
                candidate,
                obstacles,
                _silhouette,
                page,
                label=_label,
                geom_clear=_geom_clear,
            )

        source_ids = tuple(source_ids_by_name.get(name, ()))

        def _exterior_floor(_anchors=fallback_exterior_anchors):
            for raw in _anchors:
                candidate = raw if isinstance(raw, FeatureLeaderCandidate) else None
                if candidate is None or candidate.region is LeaderCandidateRegion.EXTERIOR:
                    yield raw

        def _compact_candidates(
            _anchors,
            *,
            _policy=effective_region_policy,
        ):
            for raw in _anchors:
                candidate = (
                    raw
                    if isinstance(raw, FeatureLeaderCandidate)
                    else FeatureLeaderCandidate(*raw)
                )
                if (
                    _policy is LeaderRegionPolicy.AUTO
                    or (
                        _policy is LeaderRegionPolicy.INTERIOR
                        and candidate.region is LeaderCandidateRegion.INTERIOR
                    )
                    or (
                        _policy is LeaderRegionPolicy.EXTERIOR
                        and candidate.region is LeaderCandidateRegion.EXTERIOR
                    )
                ):
                    yield candidate if isinstance(raw, FeatureLeaderCandidate) else raw

        def _on_drop(
            reason,
            *,
            _source_ids=source_ids,
            _label=label,
            _measurement=measurement,
        ):
            validation = reason == "geometry_validation"
            detail = "rendered geometry validation failed" if validation else "no clear room"
            severity = (
                ("error" if _source_ids else "warning")
                if source_drop_severity == "source"
                else source_drop_severity
            )
            ctx.record_issue(
                severity,
                drop_code,
                f"{noun} callout {_label} not placed ({detail})",
                measurement=_measurement,
                source=_source_ids,
                outcome_stage="validation" if validation else "placement",
            )

        if late_inventory:
            candidates = (
                _lane_candidates() if expand_lanes else _compact_candidates(joint_exterior_anchors)
            )
            if effective_region_policy is LeaderRegionPolicy.INTERIOR:
                fallback_candidates = (
                    _lane_candidates(
                        _interior_anchors=fallback_interior_anchors,
                        _exterior_anchors=fallback_exterior_anchors,
                    )
                    if expand_lanes
                    else _compact_candidates(
                        fallback_exterior_anchors,
                        _policy=LeaderRegionPolicy.INTERIOR,
                    )
                )
            else:
                # AUTO's bounded-resource fallback remains the exact established
                # exterior floor; extra interior anchors must not displace a complete
                # incumbent. EXTERIOR naturally uses that same floor.
                fallback_candidates = _exterior_floor()
        else:
            candidates = joint_exterior_anchors
            fallback_candidates = _exterior_floor()

        feature_jobs.append(
            FeatureLeaderJob(
                name=name,
                view=view,
                silhouette=silhouette,
                label=label,
                candidates=candidates,
                build=_build,
                analytical_geometry=_analytical_geometry,
                measurement=tuple(measurement),
                noun=noun,
                drop_code=drop_code,
                priority=priority,
                fallback_candidates=fallback_candidates,
                fallback_accept=_fallback_accept,
                interior_label_clear=interior_label_clear,
                allow_policy_b_fixed=True,
                on_drop=(_on_drop if source_ids or source_drop_severity == "source" else None),
                recover=None if straight_only else _recover,
            )
        )

    if late_inventory:
        for job in feature_jobs:
            collect_feature_leader(ctx, job)
        return 0
    return place_feature_leader_jobs(
        dwg,
        a,
        ctx,
        feature_jobs,
        producer_floor=not late_inventory,
    )


def render_chamfers(dwg, plan, a, *, ctx, only=None) -> int:
    """Submit compiler-approved chamfer jobs to the shared late assignment."""
    jobs, source_ids_by_name, straight_only_names = _edge_chamfer_jobs(
        dwg,
        plan,
        a,
        ctx=ctx,
        only=only,
        leader_callout_reach=_leader_callout_reach,
    )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun="chamfer",
        drop_code="chamfer_dropped",
        ctx=ctx,
        joint=True,
        region_policy=LeaderRegionPolicy.AUTO,
        source_ids_by_name=source_ids_by_name,
        source_drop_severity="source",
        straight_only_names=straight_only_names,
    )


def _collapsed_tolerance(members, *, ctx=None, noun=""):
    """The tolerance an ``n×`` callout may state, or ``None``.

    An `n× R5` label states one value for every member it collapses, so a ± on it claims that
    tolerance of each of them. That is only true when they ALL carry it. This used to be
    "first-AUTHORED tolerance wins" — scan the members and take the first non-``None`` — so
    tolerancing ONE of four fillets printed `4× R5 ±0.1` and claimed the author's band of the
    three they said nothing about (#1216).

    The rule `_pitch_text` applies to a pattern's collapsed pitch and `render_height_ladder`
    applies to its `N× rise` representative ("a ± here would claim the author's tolerance of
    every level"). Scanning order no longer matters, since the answer is now the same whatever
    it is — which also retires the reason the scan used planner order rather than the
    spatially-sorted one.

    Withholding is not silent, for the same reason it is not silent for a pattern pitch: an
    author who tolerances one member of a collapsed group has stated a requirement the sheet
    does not carry, and being shown nothing is how #1215 happened.
    """
    tolerances = [pd.tolerance for _, pd in members]
    first = tolerances[0] if tolerances else None
    if tolerances and (first is None or any(tol != first for tol in tolerances)):
        if ctx is not None and any(tol is not None for tol in tolerances):
            stated = sum(tol is not None for tol in tolerances)
            ctx.record_issue(
                "info",
                "collapsed_tolerance_withheld",
                f"{stated} of {len(tolerances)} collapsed {noun} members carry a tolerance, so "
                f"the one 'n×' label cannot state it — it would claim that band of every "
                f"member",
                measurement=[pd.id for _, pd in members if pd.id is not None],
            )
        return None
    return first


def render_fillets(dwg, plan, a, *, ctx, only=None) -> int:
    """Fillet radius callouts (#561/#1281): a leader from an external edge fillet to its
    ``R{radius}`` label — the arc analog of :func:`render_chamfers`. Equal-radius fillets
    share ONE ``n× R`` callout (#561 acceptance), placed in the view normal to a
    representative rounded edge, led diagonally out of the corner into clear margin, and
    dropped (lint, not silently) if it would overprint placed geometry. Returns the count.

    Planner-fed (#725 / #698): the radius VALUE + its tolerance come from the planner's
    ``DimParameter`` (as in ``render_chamfers``), bound explicitly by ``(role, kind)``,
    never positionally — formatting ``fl.radius`` directly dropped an authored tolerance
    (the #629 class). The equal-radius ``n× R`` COLLAPSE stays render-side (grouping by
    radius here) — planner-side grouping is a structural change,
    explicitly out of scope for this migration (#698). Where grouped members' authored
    tolerances differ — or only some carry one — the collapsed label states NONE of them
    (:func:`_collapsed_tolerance`), because one ``n×`` mark would claim that band of every
    member. For prismatic fillets, ``g.view`` follows the rounded-edge axis and ``_END_ON``
    preserves the established z→plan / x→side / y→front map. A turned toroidal feature instead
    carries the shaft axis plus ``turned=True``; the planner selects ``_PROFILE`` and this
    renderer rotates the physical edge anchor about its actual shaft axis onto that view while
    the shared leader solve chooses its page position (#1276). Grouping stays renderer-side:
    the IR remains one semantic feature per
    physical fillet (ADR 3 (was 0013)), while the annotation registry records all N measurement
    identities (ADR 3 (was 0017) / #1002)."""
    return _render_radius_callouts(
        dwg,
        plan,
        a,
        ctx=ctx,
        only=only,
        kind="fillet",
        role="fillet",
        name_stem="fillet",
        noun="fillet",
        drop_code="fillet_dropped",
    )


def render_blends(dwg, plan, a, *, ctx, only=None) -> int:
    """One solver-owned ``R`` leader for each equal-radius group of accepted Blend chains.

    The aggregate already removed exact Fillet owners. ``axis_direction`` remains compiled
    structure; its dominant ``axis`` selects the least-foreshortened standard orthographic view.
    """
    return _render_radius_callouts(
        dwg,
        plan,
        a,
        ctx=ctx,
        only=only,
        kind="blend",
        role="blend",
        name_stem="blend",
        noun="blend",
        drop_code="blend_dropped",
    )


def _render_radius_callouts(
    dwg,
    plan,
    a,
    *,
    ctx,
    only,
    kind: str,
    role: str,
    name_stem: str,
    noun: str,
    drop_code: str,
) -> int:
    """Submit rounded-edge jobs to the shared late feature-leader assignment."""
    jobs, straight_only_names = _edge_radius_jobs(
        dwg,
        plan,
        a,
        ctx=ctx,
        only=only,
        kind=kind,
        role=role,
        name_stem=name_stem,
        noun=noun,
        drop_code=drop_code,
        collapsed_tolerance=_collapsed_tolerance,
        leader_callout_reach=_leader_callout_reach,
    )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun=noun,
        drop_code=drop_code,
        ctx=ctx,
        joint=True,
        region_policy=LeaderRegionPolicy.AUTO,
        straight_only_names=straight_only_names,
    )


def _paired_ramp_label(angle, run, draft) -> str:
    """Format only the paired-ramp requirements approved by the compiled plan.

    Either parameter can be deliberately omitted by an authored dimension set, so the
    compound leader must degrade without resurrecting that omitted requirement (#1382).
    """
    parts = []
    if angle is not None:
        parts.append(f"2× {angle.value_text}{_tol_suffix(angle.tolerance, draft)}°")
    if run is not None:
        parts.append(f"{run.value_text}{_tol_suffix(run.tolerance, draft)} RUN")
    return " × ".join(parts)


def render_circular_blind_steps(dwg, plan, a, *, ctx, only=None) -> int:
    """Render quarter-cylinder radius and stopped depth through the circular-recess solve."""
    return _render_circular_recesses(
        dwg,
        plan,
        a,
        ctx=ctx,
        only=only,
        kind="circular_blind_step",
        drop_code="circular_blind_step_dropped",
    )


def render_circular_channels(dwg, plan, a, *, ctx, only=None) -> int:
    """Render the actual cylindrical seat diameter, open run, and arc sweep."""
    return _render_circular_recesses(
        dwg,
        plan,
        a,
        ctx=ctx,
        only=only,
        kind="circular_channel",
        drop_code="circular_channel_dropped",
    )


def _render_circular_recesses(dwg, plan, a, *, ctx, only, kind, drop_code) -> int:
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    jobs = []
    grammar = {
        "circular_blind_step": (
            ("circular_step_radius", "radius", "R", ""),
            ("circular_step_depth", "length", "", " DEEP"),
        ),
        "circular_channel": (
            ("seat_diameter", "diameter", "ø", ""),
            ("seat_run", "length", "", " LONG"),
            ("seat_sweep", "angle", "", "° ARC"),
        ),
    }[kind]
    batches: dict[tuple, list] = {}
    for index, group in enumerate(plan.of_kind(kind)):
        if only is not None and group.ref not in only:
            continue
        dimensions = []
        text = []
        for role, dimension_kind, prefix, suffix in grammar:
            dimension = group.dim(role=role, kind=dimension_kind)
            if dimension is not None:
                dimensions.append(dimension)
                text.append(
                    f"{prefix}{dimension.value_text}{_tol_suffix(dimension.tolerance, draft)}{suffix}"
                )
        if not dimensions or group.view is None:
            continue
        # Repeated coaxial seats project onto one arc. A counted callout preserves all
        # approved dimensions without contesting that same wall with duplicate leaders.
        key: tuple = (index,)
        if kind == "circular_channel":
            run = "xyz".index(group.facts.axis)
            centre = tuple(value for i, value in enumerate(group.facts.axis_origin) if i != run)
            key = (
                group.view,
                group.facts.axis,
                centre,
                tuple(
                    # Provider interval subtraction can leave a few binary ULPs (6 vs
                    # 5.999999999999996). Keep original values/ids; only the grouping
                    # key ignores sub-picometre arithmetic residue. Text must still agree.
                    (dimension.parameter_id, round(dimension.value, 12), rendered)
                    for dimension, rendered in zip(dimensions, text, strict=True)
                ),
            )
        batches.setdefault(key, []).append((index, group, dimensions, " × ".join(text)))
    for members in batches.values():
        index, group, _dimensions, label = members[0]
        view = group.view
        bounds = dwg.view_bounds(view)
        if bounds is None:
            continue
        if len(members) > 1:
            label = f"{len(members)}× {label}"

        def candidates(members=members, view=view, bounds=bounds, label=label):
            for _index, member, _dimensions, _text in members:
                for tip, elbow, owner in _circular_step_candidates(
                    dwg, view, bounds, member.facts, reach, label, provenance=member.ref
                ):
                    # Shared ink retains every measurement identity without making one
                    # seat's drop erase its siblings' dimensions.
                    yield tip, elbow, owner if len(members) == 1 else None

        jobs.append(
            (
                f"m_{kind}_{group.facts.axis}{index}",
                view,
                bounds,
                label,
                candidates(),
                tuple(
                    dimension.id
                    for _i, _g, dimensions, _text in members
                    for dimension in dimensions
                ),
            )
        )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun=kind.replace("_", " "),
        drop_code=drop_code,
        ctx=ctx,
        joint=True,
        expand_lanes=False,
    )


@dataclass(frozen=True)
class _ThroughStepLegPlacement:
    """One approved leg's render and post-drain opposite-corridor retry."""

    dwg: Any
    ctx: PlacementContext
    draft: Any
    tier: float
    view: str
    stack: str
    side: str
    alternate_side: str
    alternate_strip: Any
    pa: tuple[float, float, float]
    pb: tuple[float, float, float]
    edge: float
    label: str
    value: float
    feature: FeatureRef
    measurement: DimensionId

    def build(self, position: float) -> Dimension:
        return _dim(
            self.pa, self.pb, self.side, position - self.edge, self.draft, label=self.label
        )

    def footprint(self, position: float) -> tuple[float, float, float, float]:
        return cast(
            tuple[float, float, float, float],
            dim_footprint(
                self.pa, self.pb, self.side, position - self.edge, self.draft, self.label
            ),
        )

    def drop(self, dropped_name: str) -> None:
        self.ctx.post_drain.append(partial(self.retry, dropped_name))

    def alternate_build(self, position: float) -> Dimension:
        return _dim(
            self.pa,
            self.pb,
            self.alternate_side,
            position - self.edge,
            self.draft,
            label=self.label,
        )

    def alternate_footprint(self, position: float) -> tuple[float, float, float, float]:
        return cast(
            tuple[float, float, float, float],
            dim_footprint(
                self.pa,
                self.pb,
                self.alternate_side,
                position - self.edge,
                self.draft,
                self.label,
            ),
        )

    def retry(self, dropped_name: str) -> None:
        if self.alternate_strip is not None:
            left = place_strip_candidates(
                self.dwg,
                self.alternate_strip,
                self.view,
                self.stack,
                [(dropped_name, self.alternate_build)],
                self.tier,
                ctx=self.ctx,
                force=True,
                features={dropped_name: self.feature},
                measurements={dropped_name: self.measurement},
                footprints={dropped_name: self.alternate_footprint},
                trace=self.ctx.trace,
                trace_label=f"through_step_{self.alternate_side}_fallthrough",
            )
            if not left:
                return
        self.ctx.record_issue(
            "warning",
            "through_step_dim_dropped",
            f"through-step leg {_fmt(self.value)} not dimensioned "
            f"({self.view} {self.side}/{self.alternate_side}-strips full)",
            measurement=self.measurement,
        )


def render_through_steps(dwg, plan, a, *, ctx, only=None) -> int:
    """Render both defining legs of each rectangular through step (#1382).

    The provider supplies the canonical open section, so the renderer needs no geometry
    inference: each approved leg span becomes one linear dimension in the end-on view.  The
    missing rectangle's direction signs select the natural outside corridor; the shared
    corridor solve owns the final coordinate, then a post-drain opposite-side retry gets one
    final solver-owned chance before an explicit unavoidable drop is recorded.
    """
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text
    zones_for_view = {"front": a.fv_zones, "side": a.sv_zones, "plan": a.pv_zones}
    opposite = {"above": "below", "below": "above", "left": "right", "right": "left"}
    count = 0
    for index, group in enumerate(plan.of_kind("through_step")):
        if only is not None and group.ref not in only:
            continue
        facts = group.facts
        view = group.view
        if view is None or dwg.view_bounds(view) is None:
            continue
        outside = dict(facts.outside_directions)
        for approved in group.dims:
            if approved.role != "through_step_leg" or approved.span is None:
                continue
            start = dwg.at(view, *approved.span[0])
            end = dwg.at(view, *approved.span[1])
            changed_axis = approved.discriminator
            if changed_axis not in "xyz":
                continue
            perpendicular = next(axis for axis in "xyz" if axis not in (facts.axis, changed_axis))
            probe_world = [
                (a + b) / 2 for a, b in zip(approved.span[0], approved.span[1], strict=True)
            ]
            probe_world["xyz".index(perpendicular)] += outside[perpendicular]
            exterior = dwg.at(view, *probe_world)
            dx, dy = end[0] - start[0], end[1] - start[1]
            if abs(dx) >= abs(dy):
                side = "above" if exterior[1] > (start[1] + end[1]) / 2 else "below"
                stack = "y"
                edge = (start[1] + end[1]) / 2
                pa, pb = (start[0], edge, 0), (end[0], edge, 0)
            else:
                side = "right" if exterior[0] > (start[0] + end[0]) / 2 else "left"
                stack = "x"
                edge = (start[0] + end[0]) / 2
                pa, pb = (edge, start[1], 0), (edge, end[1], 0)
            zones = zones_for_view[view]
            strip = getattr(zones, side)
            alternate_side = opposite[side]
            alternate_strip = getattr(zones, alternate_side)
            if strip is None:
                side, strip = alternate_side, alternate_strip
                alternate_side, alternate_strip = opposite[side], None
            label = approved.value_text + _tol_suffix(approved.tolerance, draft)
            discriminator = approved.discriminator or "leg"
            name = f"dim_through_step_{facts.axis}{index}_{discriminator}"

            placement = _ThroughStepLegPlacement(
                dwg=dwg,
                ctx=ctx,
                draft=draft,
                tier=tier,
                view=view,
                stack=stack,
                side=side,
                alternate_side=alternate_side,
                alternate_strip=alternate_strip,
                pa=pa,
                pb=pb,
                edge=edge,
                label=label,
                value=approved.value,
                feature=group.ref,
                measurement=approved.id,
            )

            register_corridor(
                ctx,
                (view, side),
                strip,
                view,
                stack,
                tier,
                CorridorCandidate(
                    name=name,
                    build=placement.build,
                    order=(_SIZE_SUBCHAIN, index, discriminator, name),
                    on_place=lambda _name: None,
                    on_drop=placement.drop,
                    force=True,
                    feature=group.ref,
                    measurement=approved.id,
                    footprint=placement.footprint,
                ),
            )
            count += 1
    return count


def render_paired_ramp_steps(dwg, plan, a, *, ctx, only=None) -> int:
    """Render each recognised paired-ramp step as one solver-placed compound leader.

    The provider's stable ridge midpoint is the arrow anchor.  The planner selects the
    end-on view where the mirror-symmetric V profile is visible, while the label states the
    two equal acute angles and the shared open-to-terminal run.  Both parameter identities
    ride the one annotation so completeness can score them independently (#1382).
    """
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    jobs = []
    for index, group in enumerate(plan.of_kind("paired_ramp_step")):
        if only is not None and group.ref not in only:
            continue
        angle = next(
            (d for d in group.dims if (d.role, d.kind) == ("ramp_angle", "angle")),
            None,
        )
        run = next(
            (d for d in group.dims if (d.role, d.kind) == ("ramp_run", "length")),
            None,
        )
        if angle is None and run is None:
            continue
        view = group.view
        if view is None:
            continue
        bounds = dwg.view_bounds(view)
        if bounds is None:
            continue
        label = _paired_ramp_label(angle, run, draft)
        jobs.append(
            (
                f"m_paired_ramp_{group.facts.axis}{index}",
                view,
                bounds,
                label,
                _radial_candidates(
                    dwg,
                    view,
                    bounds,
                    group.facts,
                    reach,
                    provenance=group.ref,
                ),
                tuple(d.id for d in (angle, run) if d is not None),
            )
        )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun="paired-ramp step",
        drop_code="paired_ramp_step_dropped",
        ctx=ctx,
        joint=True,
    )


def render_gusset_ribs(dwg, plan, a, *, ctx, only=None) -> int:
    """Place one correlated callout for each rib or provider-proven rib pattern."""
    from types import SimpleNamespace

    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    jobs = []
    for index, group in enumerate(plan.of_kind("gusset_rib")):
        if only is not None and group.ref not in only:
            continue
        approved = {dimension.role: dimension for dimension in group.dims}
        thickness = approved.get("gusset_thickness")
        legs = [dimension for dimension in group.dims if dimension.role == "gusset_leg"]
        relation = approved.get("gusset_pitch") or approved.get("gusset_spacing")
        location = approved.get("gusset_location")
        if thickness is None or len(legs) != 2:
            continue
        feature = group.facts
        count = feature.member_count
        prefix = f"{count}× " if count > 1 else ""
        legs.sort(key=lambda dimension: dimension.discriminator or "")
        label = (
            f"{prefix}GUSSET {thickness.value_text}{_tol_suffix(thickness.tolerance, draft)} THK"
            f" · LEGS {legs[0].value_text}{_tol_suffix(legs[0].tolerance, draft)} × "
            f"{legs[1].value_text}{_tol_suffix(legs[1].tolerance, draft)}"
        )
        if relation is not None:
            qualifier = "PITCH" if relation.role == "gusset_pitch" else "C/C MIRROR"
            label += (
                f" · {relation.value_text}{_tol_suffix(relation.tolerance, draft)} {qualifier}"
            )
        if location is not None:
            subject = "MIRROR PLANE" if feature.pattern == "mirror" else "FIRST CL"
            label += (
                f" · {subject} {location.value_text}"
                f"{_tol_suffix(location.tolerance, draft)} FROM {feature.axis.upper()} MIN"
            )
        view = group.view
        bounds = None if view is None else dwg.view_bounds(view)
        if view is None or bounds is None:
            continue
        anchor = SimpleNamespace(frame=SimpleNamespace(origin=feature.leader_anchor))
        jobs.append(
            (
                f"m_gusset_rib_{feature.axis}{index}",
                view,
                bounds,
                label,
                _radial_candidates(dwg, view, bounds, anchor, reach, provenance=group.ref),
                tuple(dimension.id for dimension in group.dims),
            )
        )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun="gusset rib",
        drop_code="gusset_rib_dropped",
        ctx=ctx,
        joint=True,
    )


def _flat_label(across_text, sfx="") -> str:
    """The machined-flat callout string: ``{across} A/F`` (across flats) — the standard
    abbreviation for a spanner-flat / D / hex size (#148b). *across* is the PLANNED value
    (``pd.param.value``, #726); *sfx* is the pre-formatted tolerance suffix, interleaved
    after the value (``17 ±0.2 A/F`` — the tolerance rides the number, not the ``A/F``
    qualifier). Formatting lives in the render layer, not on the IR feature (ADR 3 (was 0013 §7))."""
    return f"{across_text}{sfx} A/F"


def _flat_definition_count(members) -> int:
    """Count repeated flat definitions without counting one centred opposed set as many.

    ``axis_line`` and ``stock_span`` identify the provider's stock region, but interrupted
    housing geometry can give several independent flat supports that share both.  A true
    double-D or hex remains one definition because every support has its reflection through
    that stock axis; a non-centred family retains one instance per source support.
    """

    if len(members) <= 1:
        return 1
    facts = members[0][0].facts
    transverse = tuple(index for index, axis in enumerate("xyz") if axis != facts.axis)
    centre = tuple(float(value) for value in facts.axis_line)
    points = tuple(
        tuple(float(member.facts.frame.origin[index]) for index in transverse)
        for member, _dimension in members
    )
    centred = all(
        any(
            all(
                abs(candidate[index] - (2.0 * centre[index] - point[index])) <= 0.01
                for index in range(2)
            )
            for candidate in points
        )
        for point in points
    )
    return 1 if centred else len(members)


def render_flats(dwg, plan, a, *, ctx, only=None) -> int:
    """Render physical stock definitions, counting identical ones without counting faces.

    Opposed faces on one stock region first form one across-flats definition. Equal
    definitions on independent stock can then share an explicit n× label while every
    approved measurement remains attached to that ink. Differing values, display text,
    tolerances or stock directions keep separate callouts.
    """
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    collapse: dict = {}
    for g in plan.of_kind("flat"):
        pd = next(
            (d for d in g.dims if (d.role, d.kind) == ("flat", "length")),
            None,
        )
        if pd is None:
            continue
        # Keep line AND span: opposed faces are one definition, whereas parallel
        # or coaxial stock regions contribute independent instances to the n× count.
        collapse.setdefault(
            (
                g.facts.axis,
                g.facts.presentation_axis,
                g.facts.axis_direction,
                g.facts.axis_line,
                g.facts.stock_span,
                round(pd.value, 12),
                pd.value_text,
            ),
            [],
        ).append((g, pd))
    batches: dict[tuple, list] = {}
    for gi, (
        (_axis, presentation_axis, direction, _line, _span, across, _text),
        members,
    ) in enumerate(sorted(collapse.items())):
        if only is not None:
            # Filter a finalize subset AFTER enumerating the collapse so gi
            # stays the full-drawing group index  — see render_fillets.
            members = [gp for gp in members if gp[0].ref in only]
            if not members:
                continue
        tol = _collapsed_tolerance(members, ctx=ctx, noun="flat")
        label = _flat_label(members[0][1].value_text, _tol_suffix(tol, draft))
        key: tuple = (presentation_axis, direction, across, label)
        # Conflicting authored tolerances already produce a withheld-tolerance issue.
        # Such an unresolved definition must not absorb an independent stock's claim.
        if any(pd.tolerance != members[0][1].tolerance for _, pd in members):
            key += (gi,)
        batches.setdefault(key, []).append((gi, members, label))
    jobs = []
    for (presentation_axis, *_key), stocks in batches.items():
        gi, _, label = stocks[0]
        ordered = sorted(
            (member for _, members, _ in stocks for member in members),
            key=lambda gp: gp[0].facts.frame.origin,
        )
        view = ordered[0][0].view
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        definition_count = sum(_flat_definition_count(members) for _, members, _ in stocks)
        if definition_count > 1:
            label = f"{definition_count}× {label}"
        candidates = _flat_candidates(
            dwg,
            view,
            vb,
            [g.facts for g, _ in ordered],
            reach,
            provenances=[g.ref if len(stocks) == 1 else None for g, _ in ordered],
        )
        jobs.append(
            (
                f"m_flat_{presentation_axis}{gi}",
                view,
                vb,
                label,
                candidates,
                tuple(pd.id for _, pd in ordered),
            )
        )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun="flat",
        drop_code="flat_dropped",
        ctx=ctx,
        joint=True,
    )


def _groove_label(width_text, diameter_text, wsfx="", dsfx="") -> str:
    """The turned/circlip-groove callout string: ``{width} WIDE × ø{diameter}`` — the groove's
    axial width and its floor diameter (#148c). *width*/*diameter* are the PLANNED values
    (``pd.param.value``, #727); *wsfx*/*dsfx* are each value's pre-formatted tolerance
    suffix, interleaved so a tolerance rides its own number (``4 ±0.1 WIDE × ø16 ±0.05``) —
    the two params carry independent tolerances (kinds "length"/"diameter"). Formatting
    lives in the render layer, not on the IR feature (ADR 3 (was 0013 §7))."""
    return f"{width_text}{wsfx} WIDE × ø{diameter_text}{dsfx}"


def _rectangular_blind_slot_label(
    width_text=None, length_text=None, depth_text=None, wsfx="", lsfx="", dsfx=""
) -> str:
    """Name only the compiler-approved open-slot terms.

    The full automatic grammar stays compact. An authored subset spells each surviving role
    explicitly so one approved value never disappears or masquerades as another position in
    the compound callout (ADR 4 (was 0016)).
    """
    if width_text is not None and length_text is not None and depth_text is not None:
        return f"OPEN SLOT {width_text}{wsfx} × {length_text}{lsfx} × {depth_text}{dsfx} DEEP"
    terms = []
    if width_text is not None:
        terms.append(f"{width_text}{wsfx} WIDE")
    if length_text is not None:
        terms.append(f"{length_text}{lsfx} LONG")
    if depth_text is not None:
        terms.append(f"{depth_text}{dsfx} DEEP")
    return "OPEN SLOT " + " × ".join(terms)


def _round_bottom_blind_slot_label(flat_width, radius, length, draft) -> str:
    """Name only approved round-bottom requirements with unambiguous role words."""
    terms = []
    if flat_width is not None:
        terms.append(
            f"{flat_width.value_text}{_tol_suffix(flat_width.tolerance, draft)} BOTTOM FLAT"
        )
    if radius is not None:
        terms.append(f"R{radius.value_text}{_tol_suffix(radius.tolerance, draft)}")
    if length is not None:
        terms.append(f"{length.value_text}{_tol_suffix(length.tolerance, draft)} LONG")
    return "ROUND-BOTTOM OPEN SLOT " + " × ".join(terms)


def _slot_label(width_text, length_text, wsfx="", lsfx="") -> str:
    """The grouped slot-array callout string: ``SLOT {width} × {length}`` (#841). A slot has no
    depth, so — unlike :func:`_pocket_label` — there is no ``× depth DEEP``; the ``SLOT`` prefix
    names the feature the way a lone slot's linear dims otherwise would."""
    return f"SLOT {width_text}{wsfx} × {length_text}{lsfx}"


def _oriented_slot_label(width, length, draft) -> str:
    """Name each approved free-direction slot parameter without positional ambiguity."""
    terms = []
    if width is not None:
        terms.append(f"{width.value_text}{_tol_suffix(width.tolerance, draft)} WIDE")
    if length is not None:
        terms.append(f"{length.value_text}{_tol_suffix(length.tolerance, draft)} LONG")
    return "ORIENTED SLOT " + " × ".join(terms)


# Independently owned coaxial diameters can exhaust the eight pocket directions.
# This bounded circular fan supplies additional rim targets to the same solver;
# every candidate remains radial and is ranked for clearance from projected bores.
# The feature anchor lies on a quarter-cylindrical wall, so only the four outward diagonal
# normals are physically meaningful.  Axial page rays can escape the composed end-view
# footprint into adjacent-view ink; the diagonal set keeps auto, live and deferred placement
# on the same local wall corridor.
_CIRCULAR_STEP_LEAD_DIRS = _POCKET_LEAD_DIRS[:4]


def _circular_step_candidates(dwg, view, bounds, feature, reach, label, *, provenance=None):
    """Yield solver candidates whose complete analytical leader clears every other view.

    The shared feature-leader solve is deliberately decomposed by semantic view.  A live
    single-feature replay therefore cannot rely on another view's annotations to represent
    that view's footprint.  Circular-step end-view leaders are one bounded four-candidate
    family, so reject routes whose label or shaft enters another composed view before handing
    the survivors to the same solver used by automatic and deferred placement (#1382).
    """
    width, height = _text_size(
        str(label),
        float(dwg.draft.font_size),
        getattr(dwg.draft, "font_path", DEFAULT_FONT_PATH),
        getattr(dwg.draft, "font", "Arial"),
    )
    callout_box = (0.0, 0.0, width, height)
    pad = max(float(dwg.draft.line_width), float(dwg.draft.arrow_length)) / 2
    other_views = []
    for other_view in dwg.views:
        if other_view == view:
            continue
        other = dwg.view_bounds(other_view)
        if other is not None:
            other_views.append((other[0] - pad, other[1] - pad, other[2] + pad, other[3] + pad))

    for tip, elbow, owner in _radial_candidates(
        dwg,
        view,
        bounds,
        feature,
        reach,
        provenance=provenance,
        directions=_CIRCULAR_STEP_LEAD_DIRS,
    ):
        geometry = leader_callout_geometry(tip, elbow, dwg.draft, callout_box=callout_box)
        if geometry is None:
            continue
        label_box, segments = geometry
        if label_box is None or _box_hits(label_box, other_views):
            continue
        if any(
            _segment_clips_box(first, second, other)
            for first, second in segments
            for other in other_views
        ):
            continue
        yield tip, elbow, owner


def _radial_candidates(
    dwg,
    view,
    vb,
    feature,
    reach,
    *,
    rim=0.0,
    source_bounds=None,
    source_polygon=None,
    directions=_POCKET_LEAD_DIRS,
    provenance=None,
):
    """Lead candidates for a mid-face feature (pocket/groove/boss ø): from the feature's
    projected origin, one candidate per *directions* entry (pocket-style diagonals
    first by default) — exit the silhouette along it (:func:`_ray_exit_dist`) then
    *reach* on into the margin, so even a centre-of-view feature clears the part. A
    non-zero *rim* (page units) advances the arrow tip that far along the lead direction,
    so a boss ø leader's arrowhead lands on the boss circle rather than its centre
    (#629/#700). ``source_bounds`` instead advances to the edge of a rectangular feature
    opening; a pocket leader starting at its centre crosses both the pocket rim and the outer
    silhouette, while a rim tip has only the one legitimate outward exit (#916).
    ``source_polygon`` is the corresponding exact projected opening for a non-axis-aligned
    feature; unlike an AABB it keeps the arrow on the physical rim. Yields
    ``(tip, elbow, feature)`` (same feature each time); #740 assigns the jointly compatible
    set with minimum total length, using this direction order only as the final tie-break."""
    x0, y0, x1, y1 = vb
    origin = dwg.at(view, *feature.frame.origin)
    if source_polygon is not None:
        origin = tuple(
            sum(point[index] for point in source_polygon) / len(source_polygon) for index in (0, 1)
        )
    for dx, dy in directions:
        d = math.hypot(dx, dy)
        ux, uy = dx / d, dy / d
        if source_polygon is not None:
            tip_offset = _ray_polygon_exit_dist(origin, (ux, uy), source_polygon)
            if tip_offset is None:
                continue
        elif source_bounds is not None:
            tip_offset = _ray_exit_dist(origin[0], origin[1], ux, uy, source_bounds)
        else:
            tip_offset = rim
        tip = (origin[0] + ux * tip_offset, origin[1] + uy * tip_offset)
        exit_d = _ray_exit_dist(tip[0], tip[1], ux, uy, (x0, y0, x1, y1))
        elbow = (tip[0] + ux * (exit_d + reach), tip[1] + uy * (exit_d + reach), 0)
        yield (tip, elbow, provenance if provenance is not None else feature)


def _rectangular_blind_slot_candidates(
    dwg,
    view,
    bounds,
    feature,
    reach,
    *,
    length: float | None,
    width: float | None,
    depth_approved: bool,
    provenance,
):
    """Yield leaders anchored only on proved material belonging to an open slot.

    With run/width approved, candidates touch the capped terminal edge, either side wall,
    or their two capped corners. The source-envelope mouth midpoint is air and is never a
    target. A depth-only authored callout instead points at the visible floor centre; that
    needs neither of the suppressed in-plane measurements.
    """
    origin3 = list(feature.frame.origin)
    origin = dwg.at(view, *origin3)[:2]
    targets: list[tuple[tuple[float, float], tuple[float, float]]] = []

    cap3 = None
    if length is not None:
        cap3 = origin3.copy()
        cap3["xyz".index(feature.axis)] -= feature.open_sign * length / 2
    if cap3 is not None and width is not None:
        for side_sign in (-1, 1):
            corner = cap3.copy()
            corner["xyz".index(feature.width_axis)] += side_sign * width / 2
            tip = dwg.at(view, *corner)[:2]
            targets.append((tip, (tip[0] - origin[0], tip[1] - origin[1])))
    if cap3 is not None:
        tip = dwg.at(view, *cap3)[:2]
        targets.append((tip, (tip[0] - origin[0], tip[1] - origin[1])))
    if width is not None:
        for side_sign in (-1, 1):
            side = origin3.copy()
            side["xyz".index(feature.width_axis)] += side_sign * width / 2
            tip = dwg.at(view, *side)[:2]
            targets.append((tip, (tip[0] - origin[0], tip[1] - origin[1])))
    if not targets and depth_approved:
        targets.extend((origin, direction) for direction in _POCKET_LEAD_DIRS)

    x0, y0, x1, y1 = bounds
    for tip, direction in targets:
        norm = math.hypot(*direction)
        ux, uy = direction[0] / norm, direction[1] / norm
        exit_d = _ray_exit_dist(tip[0], tip[1], ux, uy, (x0, y0, x1, y1))
        elbow = (tip[0] + ux * (exit_d + reach), tip[1] + uy * (exit_d + reach), 0)
        yield (tip, elbow, provenance)


def _round_bottom_blind_slot_candidates(
    dwg,
    view,
    bounds,
    feature,
    reach,
    *,
    length: float | None,
    flat_width: float | None,
    radius: float | None,
    provenance,
):
    """Yield physical floor/terminal targets using approved measurements only.

    The compiled facts carry topology but no printable size.  A full group can target the
    terminal corners and round-side extrema.  Partial authored groups use only geometry
    their surviving values prove: the terminal centre for run alone, flat-floor endpoints
    for flat width, and the projected floor centre for radius alone.
    """
    origin3 = list(feature.frame.origin)
    origin = dwg.at(view, *origin3)[:2]
    targets: list[tuple[tuple[float, float], tuple[float, float]]] = []

    cap3 = None
    if length is not None:
        cap3 = origin3.copy()
        cap3["xyz".index(feature.axis)] -= feature.open_sign * length / 2

    total_width = (
        flat_width + 2 * radius if flat_width is not None and radius is not None else None
    )
    if cap3 is not None and total_width is not None:
        for side_sign in (-1, 1):
            corner = cap3.copy()
            corner["xyz".index(feature.width_axis)] += side_sign * total_width / 2
            tip = dwg.at(view, *corner)[:2]
            targets.append((tip, (tip[0] - origin[0], tip[1] - origin[1])))
    if cap3 is not None:
        tip = dwg.at(view, *cap3)[:2]
        targets.append((tip, (tip[0] - origin[0], tip[1] - origin[1])))

    if flat_width is not None:
        for side_sign in (-1, 1):
            floor_end = origin3.copy()
            floor_end["xyz".index(feature.width_axis)] += side_sign * flat_width / 2
            tip = dwg.at(view, *floor_end)[:2]
            targets.append((tip, (tip[0] - origin[0], tip[1] - origin[1])))
    if not targets and radius is not None:
        targets.extend((origin, direction) for direction in _POCKET_LEAD_DIRS)

    x0, y0, x1, y1 = bounds
    for tip, direction in targets:
        norm = math.hypot(*direction)
        if norm == 0:
            continue
        ux, uy = direction[0] / norm, direction[1] / norm
        exit_d = _ray_exit_dist(tip[0], tip[1], ux, uy, (x0, y0, x1, y1))
        elbow = (tip[0] + ux * (exit_d + reach), tip[1] + uy * (exit_d + reach), 0)
        yield (tip, elbow, provenance)


def _ray_polygon_exit_dist(origin, direction, polygon) -> float | None:
    """Nearest non-negative intersection of a ray from inside a projected polygon."""
    ox, oy = origin[:2]
    dx, dy = direction
    hits = []
    for start, end in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        sx, sy = start[:2]
        ex, ey = end[:2]
        edge_x, edge_y = ex - sx, ey - sy
        denominator = dx * edge_y - dy * edge_x
        if abs(denominator) <= 1e-12:
            continue
        offset_x, offset_y = sx - ox, sy - oy
        distance = (offset_x * edge_y - offset_y * edge_x) / denominator
        fraction = (offset_x * dy - offset_y * dx) / denominator
        if distance >= -1e-9 and -1e-9 <= fraction <= 1.0 + 1e-9:
            hits.append(max(0.0, distance))
    return min(hits) if hits else None


def _oriented_slot_rim_polygon(dwg, view, feature):
    """Project the provider-owned section boundary into the selected page view."""
    passage = feature.passage
    # Recover the camera-facing normal from the actual page projection. The midpoint
    # section lies inside the passage; after a tilt its projection is not an opening rim.
    page_origin = dwg.at(view, 0, 0, 0)
    basis = [dwg.at(view, *point) for point in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
    right, up = (tuple(point[index] - page_origin[index] for point in basis) for index in (0, 1))
    facing = (
        right[1] * up[2] - right[2] * up[1],
        right[2] * up[0] - right[0] * up[2],
        right[0] * up[1] - right[1] * up[0],
    )
    near_high = sum(passage.run[index] * facing[index] for index in range(3)) > 0
    station = passage.run_interval[1 if near_high else 0]
    section_origin = tuple(
        passage.origin[index] + station * passage.run[index] for index in range(3)
    )
    points = []
    for (u_coordinate, v_coordinate), _bulge in passage.boundary:
        point = tuple(
            section_origin[index]
            + u_coordinate * passage.u[index]
            + v_coordinate * passage.v[index]
            for index in range(3)
        )
        points.append(dwg.at(view, *point)[:2])
    return tuple(points)


def render_pockets(dwg, plan, a, *, ctx, only=None) -> int:
    """Submit compiler-approved pocket leader jobs to the shared late assignment."""
    jobs = _pocket_jobs(
        dwg,
        plan,
        only=only,
        leader_callout_reach=_leader_callout_reach,
        radial_candidates=_radial_candidates,
    )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun="pocket",
        drop_code="pocket_dropped",
        ctx=ctx,
        joint=True,
    )


def render_rectangular_blind_slots(dwg, plan, a, *, ctx, only=None) -> int:
    """Place one solver-owned open-slot leader per rectangular family.

    Every printed value and tolerance crosses the approved compiler boundary; an authored
    subset produces an explicitly role-labelled subset callout rather than vanishing. The public
    topology signs remain structural facts: they preserve correspondence identity and select
    the physical feature, while the leader participates in the same post-drain joint assignment
    as pockets and the other machined callouts (ADRs 0014/0015).
    """
    reach = _leader_callout_reach(dwg.draft)
    jobs = []
    groups = sorted(
        plan.of_kind("rectangular_blind_slot"),
        key=lambda group: (
            group.facts.axis,
            group.facts.open_sign,
            group.facts.depth_axis,
            group.facts.depth_sign,
            group.facts.frame.origin,
        ),
    )
    for index, group in enumerate(groups):
        if only is not None and group.ref not in only:
            continue
        by_key = {(dim.role, dim.kind): dim for dim in group.dims}
        width = by_key.get(("rectangular_blind_slot_width", "length"))
        length = by_key.get(("rectangular_blind_slot_length", "length"))
        depth = by_key.get(("rectangular_blind_slot_depth", "length"))
        if width is None and length is None and depth is None:
            continue
        facts = group.facts
        view = _END_ON.get(facts.depth_axis)
        if view is None:
            continue
        bounds = dwg.view_bounds(view)
        if bounds is None:
            continue
        jobs.append(
            (
                f"m_rectangular_blind_slot_{facts.axis}{facts.open_sign}_{index}",
                view,
                bounds,
                _rectangular_blind_slot_label(
                    width.value_text if width is not None else None,
                    length.value_text if length is not None else None,
                    depth.value_text if depth is not None else None,
                    wsfx=_tol_suffix(width.tolerance, dwg.draft) if width is not None else "",
                    lsfx=_tol_suffix(length.tolerance, dwg.draft) if length is not None else "",
                    dsfx=_tol_suffix(depth.tolerance, dwg.draft) if depth is not None else "",
                ),
                _rectangular_blind_slot_candidates(
                    dwg,
                    view,
                    bounds,
                    facts,
                    reach,
                    length=length.value if length is not None else None,
                    width=width.value if width is not None else None,
                    depth_approved=depth is not None,
                    provenance=group.ref,
                ),
                tuple(
                    dimension.id for dimension in (width, length, depth) if dimension is not None
                ),
            )
        )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun="rectangular blind slot",
        drop_code="rectangular_blind_slot_dropped",
        ctx=ctx,
        joint=True,
    )


def render_oriented_slots(dwg, plan, a, *, ctx, only=None) -> int:
    """Place one compiler-approved, solver-owned callout per standalone oriented slot."""
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    jobs = []
    groups = sorted(
        plan.of_kind("oriented_slot"),
        key=lambda group: (group.facts.frame.axis, group.facts.frame.origin),
    )
    for index, group in enumerate(groups):
        if only is not None and group.ref not in only:
            continue
        by_key = {(dimension.role, dimension.kind): dimension for dimension in group.dims}
        width = by_key.get(("oriented_slot_width", "length"))
        length = by_key.get(("oriented_slot_length", "length"))
        if width is None and length is None:
            continue
        facts = group.facts
        view = _END_ON.get(facts.frame.axis)
        if view is None:
            continue
        bounds = dwg.view_bounds(view)
        if bounds is None:
            continue
        source_polygon = _oriented_slot_rim_polygon(dwg, view, facts)
        jobs.append(
            (
                f"m_oriented_slot_{facts.frame.axis}_{index}",
                view,
                bounds,
                _oriented_slot_label(width, length, draft),
                _radial_candidates(
                    dwg,
                    view,
                    bounds,
                    facts,
                    reach,
                    source_polygon=source_polygon,
                    provenance=group.ref,
                ),
                tuple(dimension.id for dimension in (width, length) if dimension is not None),
            )
        )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun="oriented slot",
        drop_code="oriented_slot_dropped",
        ctx=ctx,
        joint=True,
    )


def render_round_bottom_blind_slots(dwg, plan, a, *, ctx, only=None) -> int:
    """Place one solver-owned compound leader per round-bottom blind slot."""
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    jobs = []
    groups = sorted(
        plan.of_kind("round_bottom_blind_slot"),
        key=lambda group: (
            group.facts.axis,
            group.facts.open_sign,
            group.facts.depth_axis,
            group.facts.depth_sign,
            group.facts.frame.origin,
        ),
    )
    for index, group in enumerate(groups):
        if only is not None and group.ref not in only:
            continue
        by_key = {(dim.role, dim.kind): dim for dim in group.dims}
        length = by_key.get(("round_bottom_blind_slot_length", "length"))
        flat_width = by_key.get(("round_bottom_blind_slot_flat_width", "length"))
        radius = by_key.get(("round_bottom_blind_slot_radius", "radius"))
        if length is None and flat_width is None and radius is None:
            continue
        facts = group.facts
        view = _END_ON.get(facts.depth_axis)
        if view is None:
            continue
        bounds = dwg.view_bounds(view)
        if bounds is None:
            continue
        jobs.append(
            (
                f"m_round_bottom_blind_slot_{facts.axis}{facts.open_sign}_{index}",
                view,
                bounds,
                _round_bottom_blind_slot_label(flat_width, radius, length, draft),
                _round_bottom_blind_slot_candidates(
                    dwg,
                    view,
                    bounds,
                    facts,
                    reach,
                    length=length.value if length is not None else None,
                    flat_width=flat_width.value if flat_width is not None else None,
                    radius=radius.value if radius is not None else None,
                    provenance=group.ref,
                ),
                tuple(
                    dimension.id
                    for dimension in (length, flat_width, radius)
                    if dimension is not None
                ),
            )
        )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun="round-bottom blind slot",
        drop_code="round_bottom_blind_slot_dropped",
        ctx=ctx,
        joint=True,
    )


def render_pad_heights(dwg, plan, a, *, ctx, only=None) -> int:
    """Submit compiler-approved pad-height jobs to the shared late assignment."""
    jobs = _pad_height_jobs(
        dwg,
        plan,
        only=only,
        leader_callout_reach=_leader_callout_reach,
        radial_candidates=_radial_candidates,
    )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun="pad height",
        drop_code="pad_height_dropped",
        ctx=ctx,
        joint=True,
    )


def render_grooves(dwg, plan, a, *, ctx, only=None) -> int:
    """Turned/circlip-groove callouts (#148c): a leader from each annular groove in round
    stock to its ``{width} WIDE × ø{diameter}`` label. The groove's width is *axial*, so the
    callout lands in the **profile** view (the one showing the stock axis in-plane, where the
    groove reads as a notch in the silhouette) — a Z or X axis in the front, a Y axis in the
    side. Each groove gets its own callout at its own axial position (like a pocket, not
    collapsed by size — two identical grooves on one shaft or on parallel shafts must each be
    dimensioned). The groove sits on the axis, so the leader contributes silhouette-exiting
    alternatives (``_ray_exit_dist``) toward each margin to the shared within-pass assignment
    and is dropped (lint, not silently) if none lands clear. Returns the count placed.

    Planner-fed (#727 / #698): a groove is the multi-parameter case — width AND floor ø in
    one label — so EACH value + its tolerance is bound explicitly by its ``(role, kind)``
    (``("groove", "length")`` / ``("groove", "diameter")``), never positionally, never
    ``dims[0]``. Formatting ``gr.width``/``gr.diameter`` directly dropped an authored
    tolerance (the #629 class). The pass KEEPS its own axis→view map: ``g.view`` is
    ``_END_ON`` (end-on: z→plan), but a groove reads in the PROFILE view where its axial
    width is visible — not provably identical, so the map stays."""
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    view_of = _EDGE_ON  # a groove profile reads on the face its axis lies in
    groove_groups = list(plan.of_kind("groove"))
    jobs = []
    for gi, g in enumerate(
        sorted(groove_groups, key=lambda g: (g.facts.axis, g.facts.frame.origin))
    ):
        gr = g.facts
        if only is not None and g.ref not in only:
            continue  # #426 Ph2b subset (finalize): skip in place — gi stays the model index
        wpd = next((d for d in g.dims if (d.role, d.kind) == ("groove", "length")), None)
        dpd = next((d for d in g.dims if (d.role, d.kind) == ("groove", "diameter")), None)
        if wpd is None or dpd is None:
            continue
        view = view_of.get(gr.axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        jobs.append(
            (
                f"m_groove_{gr.axis}{gi}",
                view,
                vb,
                _groove_label(
                    wpd.value_text,
                    dpd.value_text,
                    wsfx=_tol_suffix(wpd.tolerance, draft),
                    dsfx=_tol_suffix(dpd.tolerance, draft),
                ),
                _radial_candidates(dwg, view, vb, gr, reach, provenance=g.ref),
                (wpd.id, dpd.id),  # one callout, two measurements
            )
        )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun="groove",
        drop_code="groove_dropped",
        ctx=ctx,
        joint=True,
    )


def render_boss_diameters(dwg, plan, a, *, ctx) -> int:
    """ø leaders for a PRISMATIC part's bosses (#629). A boss reads as a circle looking down its
    axis, so its diameter is called out with a leader to that circle in the view normal to the
    axis — a Z boss in the plan, X in the side, Y in the front — free to exit into clear margin
    (``_ray_exit_dist``), like a pocket/groove.

    The ⌀ + its tolerance/fit come from the planner's ``DimParameter`` (as in ``render_diameters``),
    never raw geometry — formatting ``b.diameter`` directly dropped an authored diameter tolerance.

    A *turned* part keeps the diameter row/column (``render_diameters``): there the boss ø sits
    in the OD stack. The turned column-left strip, applied to a prismatic boss, strands its ø
    whenever that narrow strip is tight — dropping the callout even on a half-empty sheet (#629).
    Run BEFORE ``render_diameters`` so its placed measurement identity prevents a second
    rendering of that same boss. An unplaceable one drops lint-visibly.

    Placement rides the shared :func:`place_machined_leader_jobs` adapter (#700 — never a sixth copy
    of the ray-exit loop, #637): rim-anchored :func:`_radial_candidates`, accepted with
    ``geom_clear`` (the full shaft, not just the label, must clear other annotations)."""
    if a.is_rotational or a.profiles:
        # A turned profile means round stock — a band emitted as a boss belongs in the
        # OD diameter row/column, not an end-on plan leader. Only true prismatic parts qualify.
        return 0
    draft = dwg.draft
    view_of = _END_ON  # the view looking down the boss axis
    boss_groups = list(plan.of_kind("boss"))
    reach = draft.font_size + 6 * draft.pad_around_text
    jobs = []
    collapsed: dict[tuple, list[tuple]] = {}
    for g in sorted(boss_groups, key=lambda g: (g.facts.frame.axis, g.facts.frame.origin)):
        b = g.facts
        dpd = next((pd for pd in g.dims if pd.kind == "diameter"), None)
        if dpd is None:
            continue
        dia = dpd.value
        dtol = dpd.tolerance
        thr = _manufacturing_suffix(
            getattr(b, "thread", None),
            getattr(b, "knurl", None),
            include_source_pmi=not ctx.document_member or a.pmi_mode == "annotate",
            manufacturing_tags=ctx.manufacturing_tags,
        )
        if dwg.registry.has_measurement(dpd.id):
            continue
        view = view_of.get(b.frame.axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        suffix = _tol_suffix(dtol, draft)
        key = (b.frame.axis, view, dpd.value_text, suffix, thr or "")
        collapsed.setdefault(key, []).append((g, dpd, b, vb, dia))

    for bi, ((_axis, view, value_text, suffix, thr), members) in enumerate(
        sorted(collapsed.items())
    ):
        count = len(members)
        label = (f"{count}× " if count > 1 else "") + f"ø{value_text}{suffix}"
        if thr:
            label += f" {thr}"
        measurements = tuple(member[1].id for member in members)
        if count > 1:
            measurements += tuple(
                DimensionId(member[1].id.feature, "grouping.count") for member in members
            )

        def candidates(_members=tuple(members), _view=view):
            for group, _dimension, facts, vb, diameter in _members:
                yield from _radial_candidates(
                    dwg,
                    _view,
                    vb,
                    facts,
                    reach,
                    rim=diameter / 2 * a.SCALE,
                    provenance=group.ref,
                )

        jobs.append(
            (
                f"m_bossdia_{_axis}{bi}",
                view,
                members[0][3],
                label,
                candidates(),
                measurements,
            )
        )
    return place_machined_leader_jobs(
        dwg, a, jobs, noun="boss", drop_code="boss_dia_dropped", ctx=ctx, geom_clear=True
    )


def _polygonal_boss_candidates(dwg, view, vb, boss, reach, *, provenance):
    """Leader candidates anchored on the recognised boss's physical side faces."""
    origin = dwg.at(view, *boss.frame.origin)
    for flat_centre, flat_direction in zip(boss.flat_centres, boss.flat_directions, strict=True):
        tip = dwg.at(view, *flat_centre)
        direction_end = dwg.at(
            view,
            boss.frame.origin[0] + flat_direction[0],
            boss.frame.origin[1] + flat_direction[1],
            boss.frame.origin[2] + flat_direction[2],
        )
        dx, dy = direction_end[0] - origin[0], direction_end[1] - origin[1]
        distance = math.hypot(dx, dy)
        if distance <= 1e-9:
            continue
        ux, uy = dx / distance, dy / distance
        exit_distance = _ray_exit_dist(tip[0], tip[1], ux, uy, vb)
        elbow = (
            tip[0] + ux * (exit_distance + reach),
            tip[1] + uy * (exit_distance + reach),
            0,
        )
        yield (tip, elbow, provenance)


def _polygonal_prism_jobs(dwg, plan, *, kind: str, name_prefix: str, only=None):
    """Compile polygonal wall anchors and approved dimensions into shared leader jobs."""
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    jobs = []
    groups = sorted(
        plan.of_kind(kind),
        key=lambda group: (group.facts.frame.axis, group.facts.frame.origin),
    )
    batches: dict[tuple, list] = {}
    for index, group in enumerate(groups):
        dimensions, text = [], []
        across = group.dim(role="polygon_across_flats", kind="length")
        if across is not None:
            prefix = "HEX" if group.facts.side_count == 6 else f"{group.facts.side_count}-SIDED"
            text.append(f"{prefix} {across.value_text}{_tol_suffix(across.tolerance, draft)} A/F")
            dimensions.append(across)
        if kind == "hex_pocket":
            depth = group.dim(role="pocket_depth", kind="length")
            if depth is not None:
                text.append(f"{depth.value_text}{_tol_suffix(depth.tolerance, draft)} DEEP")
                dimensions.append(depth)
        if not dimensions:
            continue
        key: tuple = (index,)
        if kind == "hex_pocket":
            key = (
                group.facts.frame.axis,
                group.facts.open_sign,
                tuple(
                    (dimension.parameter_id, round(dimension.value, 12), label)
                    for dimension, label in zip(dimensions, text, strict=True)
                ),
            )
        batches.setdefault(key, []).append((index, group, dimensions, " × ".join(text)))
    for members in batches.values():
        index = members[0][0]
        if only is not None:
            members = [member for member in members if member[1].ref in only]
            if not members:
                continue
        _, group, _, label = members[0]
        view = _END_ON.get(group.facts.frame.axis)
        if view is None:
            continue
        bounds = dwg.view_bounds(view)
        if bounds is None:
            continue
        if len(members) > 1:
            label = f"{len(members)}× {label}"

        def candidates(members=members, view=view, bounds=bounds):
            for _, member, _, _ in members:
                yield from _polygonal_boss_candidates(
                    dwg,
                    view,
                    bounds,
                    member.facts,
                    reach,
                    provenance=member.ref if len(members) == 1 else None,
                )

        jobs.append(
            (
                f"{name_prefix}_{group.facts.frame.axis}{index}",
                view,
                bounds,
                label,
                candidates(),
                tuple(dimension.id for _, _, dimensions, _ in members for dimension in dimensions),
            )
        )
    return jobs


def _render_polygonal_prisms(
    dwg,
    plan,
    a,
    *,
    ctx,
    kind: str,
    noun: str,
    name_prefix: str,
    drop_code: str,
) -> int:
    jobs = _polygonal_prism_jobs(dwg, plan, kind=kind, name_prefix=name_prefix)
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun=noun,
        drop_code=drop_code,
        ctx=ctx,
        geom_clear=True,
        joint=True,
        region_policy=LeaderRegionPolicy.AUTO,
    )


def render_hex_pockets(dwg, plan, a, *, ctx, only=None) -> int:
    """Place counted nut-pocket sizes through the shared late leader inventory."""
    jobs = _polygonal_prism_jobs(
        dwg, plan, kind="hex_pocket", name_prefix="m_hex_pocket", only=only
    )
    return place_machined_leader_jobs(
        dwg,
        a,
        jobs,
        noun="hex pocket",
        drop_code="hex_pocket_dropped",
        ctx=ctx,
        joint=True,
        priority=1.0,
    )


def render_polygonal_bosses(dwg, plan, a, *, ctx) -> int:
    """Across-flats leaders for bounded regular polygonal bosses (#676)."""
    return _render_polygonal_prisms(
        dwg,
        plan,
        a,
        ctx=ctx,
        kind="polygonal_boss",
        noun="polygonal boss",
        name_prefix="m_polygonal_boss",
        drop_code="polygonal_boss_dropped",
    )


def render_polygonal_stock(dwg, plan, a, *, ctx) -> int:
    """Across-flats leaders for whole regular polygonal stock (#1082)."""
    return _render_polygonal_prisms(
        dwg,
        plan,
        a,
        ctx=ctx,
        kind="polygonal_stock",
        noun="polygonal stock",
        name_prefix="m_polygonal_stock",
        drop_code="polygonal_stock_dropped",
    )


def render_boss_heights(dwg, plan, a, *, ctx) -> int:
    """Queue approved boss heights and polygonal-stock lengths in a profile corridor."""
    tier = dwg.draft.font_size + 2 * dwg.draft.pad_around_text
    specs = {
        "z": ("front", "right", a.fv_zones.right, "x"),
        "x": ("front", "above", a.fv_zones.above, "y"),
        "y": ("side", "above", a.sv_zones.above, "y"),
    }
    n = 0
    for bi, g in enumerate(
        sorted(
            [
                *plan.of_kind("boss"),
                *plan.of_kind("polygonal_boss"),
                *plan.of_kind("polygonal_stock"),
            ],
            key=lambda g: (g.facts.frame.axis, g.facts.frame.origin),
        )
    ):
        b = g.facts
        role = "stock_length" if g.ref.kind == "polygonal_stock" else "boss_height"
        pd = next((d for d in g.dims if (d.role, d.kind) == (role, "length")), None)
        if pd is None or pd.span is None:
            continue
        spec = specs.get(b.frame.axis)
        if spec is None:
            continue
        view, side, strip, stack = spec
        p1 = dwg.at(view, *pd.span[0])
        p2 = dwg.at(view, *pd.span[1])
        edge = max(p1[0], p2[0]) if side == "right" else max(p1[1], p2[1])
        label = pd.value_text + _tol_suffix(pd.tolerance, dwg.draft)
        prefix = "m_stocklength" if g.ref.kind == "polygonal_stock" else "m_bossheight"
        name = f"{prefix}_{b.frame.axis}{bi}"

        def build(pos, p1=p1, p2=p2, side=side, edge=edge, label=label):
            return _dim(p1, p2, side, abs(pos - edge), dwg.draft, label=label)

        def footprint(pos, p1=p1, p2=p2, side=side, edge=edge, label=label):
            return dim_footprint(p1, p2, side, abs(pos - edge), dwg.draft, label)

        def dropped(_name, *, stock=g.ref.kind == "polygonal_stock", measurement=pd.id):
            if stock:
                ctx.record_issue(
                    "warning",
                    "polygonal_stock_length_dropped",
                    "polygonal stock axial length was not placed (profile strip full)",
                    measurement=measurement,
                )

        register_corridor(
            ctx,
            (view, side),
            strip,
            view,
            stack,
            tier,
            CorridorCandidate(
                name=name,
                build=build,
                order=(_SIZE_SUBCHAIN, bi, name),
                on_place=lambda _nm: None,
                # Boss heights use reconciliation-only outcomes. Stock
                # length additionally records the compiler measurement identity so its
                # completeness check can distinguish a placement drop from missing output.
                on_drop=dropped,
                force=True,
                feature=g.ref,
                measurement=pd.id,
                footprint=footprint,
            ),
        )
        n += 1
    return n


def render_plates(dwg, plan, a, *, ctx) -> int:
    """Register plate thickness and open-channel width candidates in that order."""
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text

    def drop_factory(*, val, lbl, view, stack, alt, feat, mid, measurement_span):
        def _drop(nm):
            # Defer opposite-strip fallthrough, as GD&T does, to
            # ctx.post_drain so it runs after EVERY corridor has drained:
            # a mid-drain carve could occupy a corner a later sibling's force candidate
            # needs; post-drain, carve_free_position sees all placed annotations.
            def _retry(
                nm=nm,
                val=val,
                lbl=lbl,
                view=view,
                stack=stack,
                alt=alt,
                feat=feat,
                mid=mid,
                measurement_span=measurement_span,
            ):
                for view2, side2, strip2, axis2, qa, qb, edge2 in alt or ():
                    if strip2 is None:
                        continue
                    foot0 = dim_footprint(qa, qb, side2, tier, draft, lbl)
                    perp = (foot0[1], foot0[3]) if axis2 == "x" else (foot0[0], foot0[2])
                    pos = carve_free_position(dwg, strip2, view2, axis2, tier, perp)
                    if pos is not None:
                        # Accept-time validation: the carve accepted the
                        # ANALYTICAL footprint — build once and re-check the real box
                        # against live obstacles + the page before adding (the same
                        # contract as the corridor's validation fallback). A miss
                        # tries the next alternate.
                        dim = _dim(qa, qb, side2, pos - edge2, draft, label=lbl)
                        dim._dw_measurement_span = measurement_span
                        real = _geom_box(dim)
                        page = _drawing_bounds(dwg)
                        if real is None or (
                            _box_hits(
                                real, strip_obstacles(dwg, view=view2, crossable=CROSSABLE_TYPES)
                            )
                            or real[0] < page[0]
                            or real[1] < page[1]
                            or real[2] > page[2]
                            or real[3] > page[3]
                        ):
                            continue
                        ctx.place(dim, nm, view=view2, feature=feat, measurement=mid)
                        return
                ctx.record_issue(
                    "warning",
                    "plate_thickness_dropped",
                    f"plate thickness {_fmt(val)} not dimensioned ({view} {stack}-strip full)",
                    measurement=mid,
                    measurement_span=measurement_span,
                )

            # Queued retries run in registration order (deterministic; plates sort by
            # axis/lo/hi) and pick their first viable alternate greedily — two plates
            # contending for the same two alternates could in principle assign
            # suboptimally when several plates compete for the same alternates.
            ctx.post_drain.append(_retry)

        return _drop

    return register_plate_thickness(
        dwg, plan, a, ctx=ctx, drop_factory=drop_factory
    ) + register_channel_width(dwg, plan, a, ctx=ctx)


def _env_label(approved, draft) -> str:
    """An envelope extent's label, authored tolerance included (#1215).

    Composed here, exactly as `render_boss_heights` and the plate-thickness and channel-width
    dims already do — `pd.value_text + _tol_suffix(pd.tolerance, draft)`.

    NOT passed as `Dimension(tolerance=...)`, which is what the first version of this fix did
    and why it rendered nothing: helpers' `Dimension` does
    `rendered = label if label is not None else draft._number_with_units(measured, tolerance)`,
    so an explicit label DISCARDS the tolerance. Every dimension here passes a label, because
    the compiler owns the value text. Measured then: `label`, glyph count and label width were
    byte-identical with and without a tolerance, and the exported SVG had the same 115 paths
    (#1234).

    `_tol_suffix` also renders a `FitClass`, which the ink path's `_number_with_units` raises
    on — so composing the label is the only route that satisfies #1215's fit-class line.

    One consequence worth naming: every `_dim` call site in this package passes a label, so
    helpers' own `_number_with_units` formatting is unreachable. That is why the sheet is
    internally consistent on limit-pair ORDER — `_tol_suffix` renders `+upper -lower` for an
    envelope extent and a hole callout alike, while `_number_with_units` would render the
    opposite. The consistency is real but it rests on that unreachability (#1234).
    """
    return f"{approved.value_text}{_tol_suffix(approved.tolerance, draft)}"


def render_envelope(dwg, plan, a, *, ctx) -> int:
    """Overall width (plan, below) + depth (side, below) envelope dims via the IR,
    registered into the same below-strip corridor as feature/location/GD&T/PMI candidates.
    The overall dims use the last ladder subchain so they stack outermost by construction,
    while their mandatory priority prevents best-effort below-strip occupants from starving
    principal dimensions. The **planner** decides suppression (the rotational OD's cross-axis
    extents, X/Z-turned; #250) — there is no square-footprint rule since #997, so a square
    part arrives with both extents. Suppressed entries never arrive. Returns the count
    queued."""
    envs = plan.of_kind("envelope")
    env = envs[0] if envs else None
    if env is None:
        return 0
    n = 0

    def _queue(
        name,
        strip,
        above_strip,
        view,
        tier,
        distance,
        xs,
        label,
        build,
        footprint=None,
        measurement=None,
        measurement_span=None,
    ):
        def _tagged_build(pos, _build=build, _span=measurement_span):
            dim = _build(pos)
            dim._dw_measurement_span = _span
            return dim

        def _report(
            nm,
            _view=view,
            _strip=strip,
            _above=above_strip,
            _mid=measurement,
            _span=measurement_span,
        ):
            # An unplaced overall extent must remain visible to the audit. Use an
            # extent-specific issue code: `placement_unsatisfiable` triggers the
            # required-scale failure path, while this drop can result from a
            # view-scaled obstruction that a scale retry cannot clear. Error
            # severity prevents a missing extent from reporting a clean drawing.
            # Bind the issue to its measurement and name occupants of both strips,
            # since both placement options were tried.
            which = "width" if nm.endswith("width") else "depth"
            msg = (
                f"overall {which} dimension not placed ({_view}-view below and above strips full)"
            )
            for side_name, side_strip in (("below", _strip), ("above", _above)):
                occupants = strip_occupants(dwg, side_strip, _view, "y") if side_strip else []
                if occupants:
                    msg = f"{msg[:-1]}; {side_name} occupied by: {', '.join(occupants)})"
            ctx.record_issue(
                "error",
                "overall_dim_withheld",
                msg,
                measurement=_mid,
                measurement_span=_span,
            )

        def _drop(
            nm,
            _view=view,
            _above=above_strip,
            _mid=measurement,
            _xs=xs,
            _label=label,
            _span=measurement_span,
        ):
            # Opposite-strip fallthrough. A feature leader placed before the drain —
            # a polygonal boss's A/F callout on CTC-01, slot width dims on CTC-04 — can span
            # the whole below corridor, and no corridor-side fix reaches it: the leader is not
            # a corridor candidate, so registration ORDER cannot arbitrate against it, and a
            # reserved band would tax every drawing's leader placement to protect a rare
            # starvation. What the engine already does for a starved slot or plate dim is
            # retry on the opposite strip (`_far_or_drop`, `render_plates`); the overall
            # extent now does the same. An overall dimension above the view is ordinary
            # drafting; a missing one is not.

            # DEFERRED to ctx.post_drain: this drop fires mid-drain, and
            # placing onto the above strip immediately could occupy space a not-yet-solved
            # corridor's force candidate needs. Post-drain, the above strip's occupants are
            # final and `place_strip_candidates` spaces into what is genuinely free.
            def _retry():
                bounds = dwg.view_bounds(_view)
                if bounds is not None:
                    lift = bounds[3] + _WITNESS_LIFT_MM

                    def _fallback_build(pos, _l=lift):
                        dim = _dim(
                            (_xs[0], _l, 0),
                            (_xs[1], _l, 0),
                            "above",
                            pos - _l,
                            dwg.draft,
                            label=_label,
                        )
                        dim._dw_measurement_span = _span
                        return dim

                    if _above is not None:
                        if not place_strip_candidates(
                            dwg,
                            _above,
                            _view,
                            "y",
                            [
                                (
                                    nm,
                                    _fallback_build,
                                )
                            ],
                            tier,
                            ctx=ctx,
                            measurements={nm: _mid},
                            features={nm: env.ref},
                            trace=ctx.trace,
                            trace_label=f"{nm}_above_fallthrough",
                        ):
                            return  # placed above — the measurement is on the sheet
                    interior_jobs = getattr(ctx, "interior_dimensions", None)
                    if interior_jobs is not None and not ctx.exterior_dimensions_only:

                        def _interior_build(pos, _l=lift):
                            dim = _dim(
                                (_xs[0], _l, 0),
                                (_xs[1], _l, 0),
                                "below",
                                abs(pos - _l),
                                dwg.draft,
                                label=_label,
                            )
                            dim._dw_measurement_span = _span
                            return dim

                        interior_jobs.append(
                            InteriorDimensionJob(
                                name=nm,
                                view=_view,
                                side="above",
                                build=_fallback_build,
                                on_place=lambda _name: None,
                                on_drop=_report,
                                lane_step=(
                                    tier
                                    + (_above.spacing if _above is not None else _STRIP_SPACING)
                                ),
                                priority=_MANDATORY_OVERALL_PRIORITY,
                                feature=env.ref,
                                measurement=_mid,
                                interior_build=_interior_build,
                                analytical_geometry=lambda pos, _l=lift: (
                                    dimension_candidate_geometry(
                                        (_xs[0], _l, 0),
                                        (_xs[1], _l, 0),
                                        "below",
                                        abs(pos - _l),
                                        dwg.draft,
                                        _label,
                                    )
                                ),
                            )
                        )
                        return
                _report(nm)

            ctx.post_drain.append(_retry)

        register_corridor(
            ctx,
            (view, "below"),
            strip,
            view,
            "y",
            tier,
            CorridorCandidate(
                name=name,
                build=_tagged_build,
                order=(_OVERALL_SUBCHAIN, distance, name),
                on_place=lambda _nm: None,
                on_drop=_drop,
                priority=_MANDATORY_OVERALL_PRIORITY,
                force=True,
                feature=env.ref,
                measurement=measurement,  # which envelope extent this is
                footprint=footprint,  # analytical measure — no probe build
            ),
        )

    # ADR 2 (was 0018): an extent is observable in EITHER view whose page plane contains its axis —
    # the overall width reads in plan and equally in front. `views_showing`
    # prefers the conventional view while permitting another selected view.
    frame = layout_frame(a)
    for role, axis, slot, ann_name in (
        ("width", "x", _SLOT_DIM_WIDTH, "m_env_width"),
        ("depth", "y", _SLOT_DIM_DEPTH, "m_env_depth"),
    ):
        extent = env.dim(role=role)
        if extent is None or extent.span is None:
            continue
        # A caller may override the derived view for this independent extent.  The planner
        # has already proved that the selected projection can render the measurement and is
        # present in the resolved view plan; placement still goes through the normal strip
        # candidate solve below.
        view = extent.view or views_showing(axis, dwg.views, horizontal=True)
        if (
            extent.view is None
            and role == "width"
            and a.arrangement == "staggered-side"
            and "front" in dwg.views
        ):
            # The staggered scheme gives the plan corridor to feature/slot locations.
            # Overall X is equally observable in the front projection; route it there
            # before placement rather than recovering it into plan-view whitespace.
            view = "front"
        elif extent.view is None and role == "width" and view == "plan" and "front" in dwg.views:
            # A demand-guided plan can reserve precisely the gap and label depth
            # below the plan view, leaving no actual tier for a mandatory width.
            # The same model-space X span is visible in front. Route it there
            # before the shared corridor solve if that view has a real tier;
            # neither a later drop nor an interior retry can create strip depth.
            def _one_tier_fits(strip):
                if strip is None:
                    return False
                lo, hi, _inner = strip_free_span(strip)
                return hi - lo > slot + 1e-6

            if not _one_tier_fits(frame.zones("plan").below) and _one_tier_fits(
                frame.zones("front").below
            ):
                view = "front"
        if view is None:
            # No planned view can carry it. Reported against the measurement, never dropped
            # in silence (ADR 4 (was 0016 Amdt 6)) — and this is exactly what the ADR 2 (was 0018)
            # requirement gate reads to reject a view set that costs a mandatory extent.
            ctx.record_issue(
                "error",
                "overall_dim_withheld",
                f"overall {role} cannot be shown: no planned view lays the {axis} axis "
                f"out horizontally (planned: {tuple(dwg.views)})",
                measurement=extent.id,
                measurement_span=extent.span,
            )
            continue
        index = "xyz".index(axis)
        start_pt = tuple(extent.span[0])
        end_pt = tuple(
            extent.span[1][index] if i == index else value for i, value in enumerate(start_pt)
        )
        p1, p2 = dwg.at(view, *start_pt), dwg.at(view, *end_pt)
        witness = p1[1] - _WITNESS_LIFT_MM
        zones = frame.zones(view)
        _queue(
            ann_name,
            zones.below,
            zones.above,
            view,
            slot,
            abs(end_pt[index] - start_pt[index]),
            (p1[0], p2[0]),
            _env_label(extent, dwg.draft),
            lambda pos, _p1=p1, _p2=p2, _w=witness, _v=_env_label(extent, dwg.draft): _dim(
                (_p1[0], _w, 0),
                (_p2[0], _w, 0),
                "below",
                _w - pos,
                dwg.draft,
                label=_v,
            ),
            # Measure the same rendered label used by the Dimension. The span
            # usually dominates this footprint, but outside arrows can make the
            # label affect its extent.
            footprint=lambda pos, _p1=p1, _p2=p2, _w=witness, _v=_env_label(extent, dwg.draft): (
                dim_footprint((_p1[0], _w, 0), (_p2[0], _w, 0), "below", _w - pos, dwg.draft, _v)
            ),
            measurement=extent.id,
            measurement_span=extent.span,
        )
        n += 1
    return n


@dataclass(frozen=True)
class _StepChainSegment:
    """One step-length claim as it moves from part space through page projection.

    The old positional tuples lost the compiled measurement id when they were rebuilt by
    projection, repeat-run collapse, and detail redraws. Keeping the claim named makes those
    transformations state explicitly whether they preserve, combine, or intentionally omit
    identity (#1004).
    """

    pa: tuple[float, float, float]
    pb: tuple[float, float, float]
    value: float
    tolerance: Any = None
    measurements: tuple[Any, ...] = ()
    label: str | None = None
    value_text: str | None = None
    display_decimals: int | None = None


def _step_value_text(segment: _StepChainSegment) -> str:
    """Compiler-owned nominal text, with a fallback for synthetic block segments."""
    return segment.value_text if segment.value_text is not None else _fmt(segment.value)


def _step_measurements(segs: list[_StepChainSegment]) -> tuple[Any, ...]:
    """Stable union of the measurements represented by *segs*."""
    result: list[Any] = []
    for seg in segs:
        for measurement in seg.measurements:
            if measurement not in result:
                result.append(measurement)
    return tuple(result)


def _record_step_chain_drop(dwg, why: str, *, ctx, measurement=()) -> None:
    """Record the ``step_dim_dropped`` warning for unresolved turned lengths.
    These drops were silent (debug log only) — the user got
    a drawing with no step-length dimensioning and no signal. Mirrors
    ``render_height_ladder``'s prismatic drop, but records ONLY the lint code (not an
    ``Escalation(kind="step")``): that escalation is consumed by
    ``_request_prismatic_detail`` (sections.py), which would redraw *prismatic*
    height-above-base dims for a *turned* chain — the wrong semantics #351 PR-4b
    removed. An authored semantic shoulder detail uses the shared chain pass."""
    ctx.record_issue(
        "warning",
        "step_dim_dropped",
        f"step-length chain dropped: {why} at this scale "
        "(request Sheet.detail_view(..., around=step) at a larger scale)",
        measurement=measurement,
    )


def _draw_step_chain(
    dwg,
    view,
    segs,
    name_prefix,
    detail_scale=None,
    allow_collapse=True,
    *,
    ctx,
    start=0,
    profile_bounds=None,
    placement_bounds=None,
) -> int:
    """Place a turned step-length chain in *view* from structured *segs*, each already
    projected to *view*'s page coords in axis order. Orientation is
    data (the projected span direction): horizontal → chain above the view, vertical
    → chain to the right. A uniform run collapses to one ``N× v`` dim (#230); else a
    per-segment chain, staggered into a near/far tier only when crowded (ISO 129-1,
    #293). The shared ink solve offers both orientations a far tier; off-page
    members are reported individually without erasing valid neighbours.
    ``detail_scale`` tags the dims for label-vs-measured lint when
    drawing inside a scaled detail view. ``allow_collapse=False`` disables the ``N× v``
    collapse — used when the chain mixes a synthetic head-*block* with real steps, where
    a uniform-staircase representative would be a false claim of N equal steps (#307).
    ``profile_bounds`` narrows the placement edge to one body's projected silhouette
    when a compound contains multiple turned profiles. Returns the count placed."""
    if not segs:
        return 0
    vb = profile_bounds or dwg.view_bounds(view)
    if vb is None:
        return 0
    trace = getattr(ctx, "trace", None)  # the immediate placers report to the trace too
    ev = trace.pass_event("step_length_chain", view=view) if trace is not None else None
    x0, y0, x1, y1 = vb
    draft = dwg.draft
    gap = draft.font_size + 4 * draft.pad_around_text
    horizontal = abs(segs[0].pb[0] - segs[0].pa[0]) >= abs(segs[0].pb[1] - segs[0].pa[1])
    vals = [seg.value for seg in segs]
    # The suffix rides the label because helpers discard `tolerance=` when an
    # explicit label is given. Use the same compiler-owned label for the
    # staggering width calculation and the rendered dimension.
    labels = [
        seg.label
        if seg.label is not None
        else _step_value_text(seg) + _tol_suffix(seg.tolerance, draft)
        for seg in segs
    ]
    mean_v = sum(vals) / len(vals)
    explicit_display = any(seg.display_decimals is not None for seg in segs)
    tier_step = draft.font_size + 2 * draft.pad_around_text
    if (
        allow_collapse
        and all(seg.label is None for seg in segs)
        and len(segs) >= 3
        and (max(vals) - min(vals)) <= 0.10 * mean_v
        and (
            not explicit_display
            or all(_step_value_text(seg) == _step_value_text(segs[0]) for seg in segs)
        )
    ):
        # A uniform run collapses to one "N× v" dim; a per-step ± would be a false claim on
        # N equal steps, so the collapse carries no shared tolerance.
        repeated_text = _step_value_text(segs[0]) if explicit_display else _fmt(mean_v)
        label = f"{len(segs)}× {repeated_text}"
        xs = [p[0] for seg in segs for p in (seg.pa, seg.pb)]
        ys = [p[1] for seg in segs for p in (seg.pa, seg.pb)]
        if horizontal:
            dim = _dim((min(xs), y1, 0), (max(xs), y1, 0), "above", gap, draft, label=label)
        else:
            dim = _dim((x1, min(ys), 0), (x1, max(ys), 0), "right", gap, draft, label=label)
        typ_name = f"{name_prefix}_typ" if start == 0 else f"{name_prefix}_typ{start}"
        candidates = [(typ_name, dim, _step_measurements(segs))]
    else:
        tiers = [0] * len(segs)
        if horizontal:
            cw = [
                (
                    (seg.pa[0] + seg.pb[0]) / 2,
                    len(labels[i]) * draft.font_size * _EST_CHAR_WIDTH_EM,
                )
                for i, seg in enumerate(segs)
            ]

            def _clear(items):
                return all(
                    c2 - c1 >= (w1 + w2) / 2 + draft.pad_around_text
                    for (c1, w1), (c2, w2) in zip(items, items[1:])
                )

            if _clear(cw):
                pass
            elif _clear(cw[0::2]) and _clear(cw[1::2]):
                tiers = [i % 2 for i in range(len(segs))]
            else:
                _log.info("step-length chain skipped: too dense even when staggered")
                _record_step_chain_drop(
                    dwg,
                    "shoulders too dense to dimension even when staggered",
                    ctx=ctx,
                    measurement=_step_measurements(segs),
                )
                if ev is not None:
                    ev["items"].append(
                        {"name": name_prefix, "outcome": "dropped", "reason": "too_dense"}
                    )
                return 0
        # A short vertical shoulder is not a density test for the whole chain.
        # Helpers can draw outside arrows, and the shared batch solver below
        # checks actual labels/ink and offers the same far tier on either axis.

        candidates = []
        for i, seg in enumerate(segs):
            if horizontal:
                p1, p2, side = (seg.pa[0], y1, 0), (seg.pb[0], y1, 0), "above"
                dist = gap + tiers[i] * tier_step
            else:
                p1, p2, side = (x1, seg.pa[1], 0), (x1, seg.pb[1], 0), "right"
                dist = gap
            candidates.append(
                (
                    f"{name_prefix}{start + i}",
                    _dim(
                        p1,
                        p2,
                        side,
                        dist,
                        draft,
                        label=labels[i],
                    ),
                    seg.measurements,
                )
            )

    page = _drawing_bounds(dwg)
    # The chain is one placement batch: until commit, no sibling's extension line or
    # terminator exists in strip occupancy.  Select small along-line label offsets against
    # the complete batch before the room guard.  Measurement provenance stays paired
    # by name; only the rendered Dimension survivor changes.
    measurements_by_name = {name: measurements for name, _dim_obj, measurements in candidates}
    # A body-local profile can sit inside a wider flange in the same view.
    # Its near tier stays local; the one alternate tier can reach the outer
    # edge of its assigned view cell without moving measurement supports.
    cell = placement_bounds or dwg.view_bounds(view)
    outer_index = 3 if horizontal else 2
    far_step = max(tier_step, cell[outer_index] - vb[outer_index]) if cell else tier_step
    candidates = [
        (name, dim, measurements_by_name[name])
        for name, dim in prevent_dimension_label_ink(
            [(name, dim) for name, dim, _measurements in candidates],
            page=page,
            obstacles=strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES),
            perpendicular_step=far_step,
            label_clear=view_label_clearance(dwg, view),
        )
    ]

    # Preserve independently placeable measurements when one member is off-page.
    # A missing neighbour stays explicitly unresolved under its own identity.
    survivors = []
    for name, dim, measurements in candidates:
        box = _geom_box(dim)
        if box is not None and not (
            page[0] <= box[0] and box[2] <= page[2] and page[1] <= box[1] and box[3] <= page[3]
        ):
            _record_step_chain_drop(
                dwg,
                "a dimension would fall off the drawable page",
                ctx=ctx,
                measurement=measurements,
            )
            if ev is not None:
                ev["items"].append({"name": name, "outcome": "dropped", "reason": "off_page"})
            continue
        survivors.append((name, dim, measurements))
    for name, dim, measurements in survivors:
        if detail_scale is not None:
            dim._dw_scale = detail_scale
        ctx.place(dim, name, view=view, measurement=measurements)
        if ev is not None:
            b = _anno_box(dim)
            ev["items"].append(
                {"name": name, "outcome": "placed", "box": list(b) if b is not None else None}
            )
    return len(survivors)


def _next_steplen_start(ctx, prefix: str = "m_steplen") -> int:
    """First free m_steplen index past the MAX existing one — the #426 finalize path names
    the chain as a contiguous run from one start, so it must clear every existing name (max+1,
    not first-free: a gap below an occupied index would let the run wrap onto it, #432)."""
    idxs: list[int] = []
    for n in ctx.registry.names():
        if not n.startswith(prefix):
            continue
        rest = n[len(prefix) :]
        if rest.isdigit():
            idxs.append(int(rest))
        elif rest.startswith("_typ"):  # the N× collapse name m_steplen_typ{start}
            tail = rest[4:]
            idxs.append(int(tail) if tail.isdigit() else 0)
    return max(idxs) + 1 if idxs else 0


def queue_step_detail(dwg, plan, feature, a, *, ctx, view_name, label, factor, source) -> bool:
    """Redraw an authored shoulder detail through the shared approved-length pass."""
    target = FeatureRef(feature)
    groups = [group for group in plan.of_kind("step") if group.ref == target]
    if len(groups) != 1:
        return False
    (group,) = groups
    length = group.dim(kind="length")
    if length is None or length.span is None:
        return False
    axis = group.facts.frame.axis
    view = group.view
    in_plane = {"front": ("x", "z"), "side": ("y", "z"), "plan": ("x", "y")}
    if view not in in_plane or axis not in in_plane[view]:
        return False
    cross = cast(Literal["x", "y", "z"], next(value for value in in_plane[view] if value != axis))
    ai, ci = "xyz".index(axis), "xyz".index(cross)
    lo, hi = sorted(point[ai] for point in length.span)
    context = max(1.0, (hi - lo) / 2)
    diameter = group.dim(kind="diameter")
    profile_support_points: tuple[tuple[float, float, float], ...]
    if diameter is not None:
        rim = group.facts.frame.origin[ci] + diameter.value / 2

        # A partial secondary crop may keep a dimension's centreline witnesses
        # while cutting away the shoulder they describe. Carry the physical
        # rim at both measured stations into the shared detail crop guard.
        def at_rim(point: tuple[float, float, float]) -> tuple[float, float, float]:
            return (
                float(rim if ci == 0 else point[0]),
                float(rim if ci == 1 else point[1]),
                float(rim if ci == 2 else point[2]),
            )

        render_span = (
            at_rim(length.span[0]),
            at_rim(length.span[1]),
        )
        profile_support_points = render_span
        cross_bounds = supported_secondary_crop(
            profile_support_points,
            cross,
            getattr(a.bb.min, cross.upper()),
            getattr(a.bb.max, cross.upper()),
            a.SCALE * factor,
        )
        if cross_bounds is None:
            # An unprovable partial crop must not discard an authored detail.
            # Keep the full profile and its centreline witnesses instead.
            cross_lo = cross_hi = None
            profile_support_points = ()
            render_span = length.span
        else:
            cross_lo, cross_hi = cross_bounds
    else:
        # Without a controlled diameter there is no exact outer-rim support
        # for a partial crop. Retain the full secondary extent instead.
        cross_lo = cross_hi = None
        profile_support_points = ()
        render_span = length.span
    # The displayed length must attach to the retained physical edge, not the
    # centreline that the partial secondary crop deliberately omits.
    segment = _StepChainSegment(
        render_span[0],
        render_span[1],
        length.value,
        length.tolerance,
        length.measurement_ids,
        value_text=length.value_text,
        display_decimals=length.display_decimals,
    )

    def redraw(dwg, detail_view, coords, detail_scale):
        projected = replace(segment, pa=coords.pp(*segment.pa), pb=coords.pp(*segment.pb))
        return _draw_step_chain(
            dwg,
            detail_view,
            [projected],
            f"{detail_view}_steplen",
            detail_scale=detail_scale,
            allow_collapse=False,
            ctx=ctx,
        )

    draft = dwg.draft
    band = 2 * draft.font_size + 6 * draft.pad_around_text + 2 * draft.arrow_length
    ctx.detail_requests.append(
        DetailRequest(
            axis=axis,
            lo=lo,
            hi=hi,
            crop_lo=lo - context,
            crop_hi=hi + context,
            scale_needed=a.SCALE * factor,
            redraw=redraw,
            pads=lambda _scale: (band, 0.0) if axis == in_plane[view][1] else (0.0, band),
            source_view=view,
            cross_axis=cross if cross_lo is not None else None,
            cross_lo=cross_lo,
            cross_hi=cross_hi,
            kind="authored-step",
            view_name=view_name,
            label=label,
            scale_factor=factor,
            source=source,
            measurement_ids=length.measurement_ids,
            measurement_spans=(length.span,),
            profile_support_points=profile_support_points,
        )
    )
    return True


def render_step_lengths(
    dwg,
    plan,
    *,
    ctx,
    only=None,
    _profile_bounds_hint=None,
    _profile_view_hint=None,
) -> int:
    """Render approved turned axial lengths through the shared step-chain placer."""
    # Lazy to keep the existing ``from_model._draw_step_chain`` override seam live.
    from draftwright.annotations._step_lengths import render_step_lengths as render

    return render(
        dwg,
        plan,
        ctx=ctx,
        only=only,
        _profile_bounds_hint=_profile_bounds_hint,
        _profile_view_hint=_profile_view_hint,
        _draw_step_chain=_draw_step_chain,
    )


def ladder_plan_for(plan, *, step_height: bool, overall: bool):
    """*plan* narrowed to the ladders a caller actually asked :func:`render_height_ladder` for.

    The renderer draws TWO independent things — a `step_level` feature's correlated rungs and
    the envelope/bbox overall height — and reads a third, `step_position`, only for its
    PRESENCE (short-rung placement). Handing it a plan containing more than was asked for
    draws more than was asked for: the #889 drain passed the whole compiled plan once either
    intent was recorded, so `overall_height()` alone also rebuilt the step rungs — a
    dimension nobody recorded, and live/deferred divergence in the one PR relying on their
    equivalence (#934).

    Exists so the live verb and the finalize drain project the plan the SAME way. Two
    spellings of "which ladders did they ask for" is how they diverged in the first place.

    `step_position` rides `step_height`: it is not content here, it is how those rungs are
    placed, so it is meaningless without them.
    """
    kinds = []
    if step_height:
        kinds += ["step_height", "step_position"]
    if overall:
        kinds.append("overall_height")
    return replace(plan, ladders=tuple(lad for lad in plan.ladders if lad.kind in kinds))


def render_height_ladder(dwg, plan, frame, *, ctx, detail_view: bool = False) -> int:
    """Route approved ladders through the shared vertical-strip placement pass.

    Step rungs retain their front-view renderer. The independent overall height
    may use an explicitly selected rear view, with its own corridor and witnesses.
    """
    overall = plan.ladder("overall_height")
    height_view: str | None = "front"
    if overall is not None:
        height_view = overall.rungs[0].view or next(
            (name for name in ("front", "rear") if name in getattr(dwg, "views", ("front",))),
            None,
        )
        if height_view is None:
            height = overall.rungs[0]
            ctx.record_issue(
                "error",
                "placement_unsatisfiable",
                "overall height cannot be shown: no planned front or rear view",
                measurement=height.id,
                measurement_span=height.span,
                outcome_stage="placement",
            )
            plan = ladder_plan_for(plan, step_height=True, overall=False)
            if plan.ladder("step_height") is None:
                return 0
            height_view = "front"
    if height_view == "front":
        return _render_height_ladder_in_view(
            dwg, plan, frame, ctx=ctx, detail_view=detail_view, view="front"
        )
    count = 0
    for view, steps, height in (("front", True, False), (height_view, False, True)):
        selected = ladder_plan_for(plan, step_height=steps, overall=height)
        if selected.ladder("step_height") is None and selected.ladder("overall_height") is None:
            continue
        count += _render_height_ladder_in_view(
            dwg, selected, frame, ctx=ctx, detail_view=detail_view, view=view
        )
    return count


def _render_height_ladder_in_view(dwg, plan, frame, *, ctx, detail_view, view) -> int:
    """Front-view ladder: prismatic step heights stacked inner→outer, then the overall
    height outermost. The overall height can be authored on the left; candidates enter
    the shared corridor for their side. The leapfrog witness cursor (#237) survives as a
    *build-time chain*: candidates share a ``solved`` position map, and each dim's witness
    anchors on its nearest already-built predecessor's line (the view edge for the first).

    **The first renderer migrated to the ADR 4 (was 0016) boundary.** It takes the compiled
    :class:`RenderableDimensionPlan` and a :class:`LayoutFrame`, not the `PartModel` and the
    `Analysis`. Everything it used to decide about WHAT to draw — which rungs exist, their
    values and labels, whether a uniform staircase collapses to one ``n×`` mark, whether the
    overall height is drawn at all and what its value is — now arrives already decided. It
    could previously reach `StepLevelFeature.levels` and `a.bb` and rebuild all of it,
    bypassing the plan.

    What stays here is placement, and it is a real job: legibility at this scale, the
    leapfrog chain, corridor registration, the left-strip escape for short rises, and the
    lint code when the strip is physically full. Note the two kinds of "not drawn" that meet
    here and must not be confused — the compiler's omission (never arrives; reported through
    the plan's diagnostics) and this pass's drop (arrived, did not fit; reported as
    ``placement_unsatisfiable``). Returns the count REGISTERED."""
    draft = dwg.draft
    _left, right, _bottom, _top = frame.edges(view)
    edge2 = right + 2
    tier = draft.font_size + 2 * draft.pad_around_text

    def _zspan(entry):
        """An entry's witness ends, projected from ITS OWN span.

        Anchoring every rung at the view's bottom edge instead was wrong the moment the
        compiler started measuring from `StepLevelFeature.base`: a declared base above the
        part's bottom made the drawn line span the full part while the label read the
        shorter distance, so the dimension said one thing and measured another (#923).
        The span is the compiler's statement of what is being measured; projecting
        both ends of it is what keeps line and label the same claim."""
        return (
            frame.project(view, entry.span[0])[1],
            frame.project(view, entry.span[1])[1],
        )

    rung_set = plan.ladder("step_height")
    rungs = list(rung_set.rungs) if rung_set is not None else []
    # An OPAQUE handle, passed straight through to the corridor candidate and the
    # escalation. This pass never resolves it: the feature behind it carries the levels
    # and the base, which is the content the compiler already ruled on.
    step = rung_set.ref if rung_set is not None else None
    has_shoulders = plan.ladder("step_position") is not None
    short_rungs: list = []

    # The chain, inner→outer: (name, page-z span, label, tier size, drop message, dim id,
    # per-unit value). The last is the number the LABEL's `N×` prefix multiplies — set only
    # for the representative rung, whose "8× 15" is one 15 mm step rather than a 120 mm run.
    # Carry it from `ApprovedDimension.value` so lint compares against the
    # compiler's own number instead of re-deriving a convention from the rendered string,
    # which is the pattern ADR 4 (was 0016 Amendment 1) exists to stop.
    chain: list = []
    order_values: dict[str, float] = {}
    if rung_set is not None and rung_set.representative:
        (rep,) = rungs
        order_values["dim_step_typ"] = rep.value
        chain.append(
            (
                "dim_step_typ",
                *_zspan(rep),
                rep.final_label,
                _SLOT_DIM_STEP,
                "representative step-height dimension dropped (front-view right strip full)",
                rep.id,
                rep.value,
                # NO tolerance, deliberately. `N× rise` states one value for the whole run, so
                # a ± here would claim the author's tolerance of every level at once — the same
                # rule the turned-step collapse follows. The plain rungs below each
                # state their own measurement and do carry it.
                None,
                rep.span,
            )
        )
    elif rungs:
        # Legibility is a PLACEMENT decision — whether two rungs are too close to dimension
        # depends on the page, not the model — so it stays here while the rung set itself
        # comes from the compiler. Both bounds come off the approved span, not the bbox.
        kept_z, close_z, short_z = _classify_steps(
            [r.span[1][2] for r in rungs],
            rungs[0].span[0][2],
            frame.scale,
            allow_short=has_shoulders,
        )
        n_close = len(close_z)
        kept_level_set = set(kept_z)
        if short_z:
            # The compiler approved these rungs but their page span is shorter
            # than the dimension ink. Report each omitted measurement explicitly.

            # Deliberately NOT a `*_dropped` code: those score against legibility, and this
            # is an omission, which is completeness's ledger. Whether the right answer is a
            # detail-view escalation (as the too-close case gets) or a compiler that never
            # approves a rung this short is a policy decision, and it is not made here.
            # Saying so is not contingent on making it.
            short_set = set(short_z)
            withheld = [rung for rung in rungs if rung.span[1][2] in short_set]
            ctx.record_issue(
                "info",
                "step_dim_withheld",
                f"{len(short_z)} approved step height(s) span less than "
                f"{_MIN_STEP_DIM_MM:.3g} mm on the page from the ladder's datum and are not "
                "dimensioned at this scale",
                # Bind each withheld rung to its own approved measurement so a
                # different absence on the same drawing cannot stand in for it.
                measurement=[rung.id for rung in withheld if rung.id is not None],
                measurement_spans=[rung.span for rung in withheld if rung.id is not None],
            )
        if n_close:
            crowded = tuple(rung for rung in rungs if rung.span[1][2] not in kept_level_set)
            # When detail recovery is enabled the enlarged view owns the omitted rungs.
            # Report the source-view drop only when no recovery was requested; a failed
            # detail records ``detail_unplaceable`` instead.
            if not detail_view:
                ctx.record_issue(
                    "warning",
                    "step_dim_dropped",
                    f"{n_close} step height(s) too closely spaced to dimension at this scale "
                    "(use a detail view)",
                    measurement=[rung.id for rung in crowded if rung.id is not None],
                    measurement_spans=[rung.span for rung in crowded if rung.id is not None],
                    outcome_stage="placement",
                )
            # Record escalation alongside the lint code (ADR 2 (was 0009 Amdt 1)) —
            # `_request_prismatic_detail` (sections.py) consumes this instead of recomputing
            # the legibility gate.
            ctx.escalations.append(
                Escalation(
                    kind="step",
                    view=view,
                    feature=step,
                    reason="illegible",
                    targets=crowded,
                )
            )
        kept = [r for r in rungs if r.span[1][2] in kept_level_set]
        for col, rung in enumerate(kept):
            # A short structural rise needs external arrows, whose ink would swamp the usual
            # right-hand ladder; it goes to the left strip below.
            if has_shoulders and rung.value * frame.scale < _MIN_STEP_DIM_MM:
                short_rungs.append(rung)
                continue
            order_values[f"dim_step_{col}"] = rung.value
            chain.append(
                (
                    f"dim_step_{col}",
                    *_zspan(rung),
                    rung.final_label,
                    _SLOT_DIM_STEP,
                    "step-height dimension dropped (front-view right strip full)",
                    rung.id,
                    None,  # no `N×` prefix: the label states the span itself
                    rung.tolerance,
                    rung.span,
                )
            )

    overall = plan.ladder("overall_height")
    if overall is not None:
        (height,) = overall.rungs
        chain.append(
            (
                "dim_height",
                *_zspan(height),
                height.final_label,
                _SLOT_DIM_HEIGHT,
                "overall height dimension dropped (front-view right strip full)",
                height.id,
                None,  # no `N×` prefix
                height.tolerance,
                height.span,
            )
        )

    # Authored tolerances reach the sheet here, composed into the LABEL like every other
    # toleranced Dimension in this module. Passing one as `Dimension(tolerance=...)` renders
    # nothing, because an explicit label discards it — see `_env_label`.

    # Key tolerances per rung. A ± on the `N× rise` representative would
    # claim the same tolerance for every level; plain ladder rungs each state
    # their own measurement and can carry their own tolerance.
    _tolerances = {c[0]: c[8] for c in chain}

    names = [c[0] for c in chain]
    sides = {
        name: (overall.rungs[0].side or "right")
        if name == "dim_height" and overall is not None
        else "right"
        for name in names
    }
    solved: dict[str, float] = {}
    for k, (
        name,
        zbase,
        ztop,
        label,
        _tsize,
        drop_msg,
        mid,
        per_unit,
        _rt,
        measurement_span,
    ) in enumerate(chain):
        side = sides[name]
        direction = 1 if side == "right" else -1
        edge = edge2 if side == "right" else _left - 2
        strip = frame.zones(view).right if side == "right" else frame.zones(view).left
        predecessors = [pn for pn in names[:k] if sides[pn] == side]

        def _witness_base(pos, predecessors=predecessors, direction=direction, edge=edge):
            base = edge
            for pn in reversed(predecessors):
                if pn in solved:
                    base = solved[pn]
                    break
            # A retry can revisit the inner position after a predecessor was built.
            # Prediction and rendering must use the same non-degenerate witness.
            return edge if direction * (pos - base) < 0.5 else base

        def _build(
            pos,
            name=name,
            zbase=zbase,
            ztop=ztop,
            label=label,
            witness_base=_witness_base,
            side=side,
            direction=direction,
            authored_side=overall.rungs[0].side
            if name == "dim_height" and overall is not None
            else None,
            per_unit=per_unit,
            _tol=_tolerances.get(name),
            measurement_span=measurement_span,
        ):
            base = witness_base(pos)
            solved[name] = pos
            dim = _dim(
                (base, zbase, 0),
                (base, ztop, 0),
                side,
                direction * (pos - base),
                draft,
                label=label + _tol_suffix(_tol, draft),
            )
            if per_unit is not None:
                # What this dimension's `N× v` label actually measures. Lint reads it in
                # preference to parsing the label, because `N× v` is drawn under two
                # conventions here and the string cannot tell them apart: this one is ONE
                # step, while a hole pitch spans the whole run. Same seam as `_dw_scale`.
                dim._dw_label_value = per_unit
            dim._dw_measurement_span = measurement_span
            if authored_side is not None:
                dim._dw_authored_side = authored_side
            return dim

        # The footprint measures the RENDERED string, so it carries the same suffix the
        # Dimension draws. Correctness, not a measured failure mode — see the note in
        # `render_envelope`; the invented "packs the strip too tightly" claim is withdrawn.
        def _foot(
            pos,
            zbase=zbase,
            ztop=ztop,
            label=label + _tol_suffix(_tolerances.get(name), draft),
            witness_base=_witness_base,
            side=side,
            direction=direction,
        ):
            # Predecessor-aware prediction: the conservative edge-anchored
            # witness can falsely exhaust the strip when an inner obstacle sits in the
            # already-traversed region. Use the build chain's witness calculation.
            base = witness_base(pos)
            return dim_footprint(
                (base, zbase, 0), (base, ztop, 0), side, direction * (pos - base), draft, label
            )

        def _drop(
            nm,
            drop_msg=drop_msg.replace("front-view", f"{view}-view").replace(
                "right strip", f"{side} strip"
            ),
            strip=strip,
            name=name,
            measurement=mid,
            measurement_span=measurement_span,
        ):
            solved.pop(name, None)
            # Name what filled the strip so the diagnosis shows the
            # lint message.
            msg = full_strip_message(drop_msg, dwg, strip, view, "x")
            ctx.record_issue(
                "error",
                "placement_unsatisfiable",
                msg,
                measurement=measurement,
                measurement_span=measurement_span,
                outcome_stage="placement",
            )

        register_corridor(
            ctx,
            (view, side),
            strip,
            view,
            "x",
            tier,
            CorridorCandidate(
                name=name,
                build=_build,
                # Steps stack inner→outer in chain order; the overall height rides the
                # OVERALL subchain so it lands outermost by construction (as the envelope
                # dims do). Ordinary rungs join the same value-ordered baseline run as
                # off-axis-hole heights, outside the inner boss-size run, instead
                # of retaining producer registration order.
                order=(
                    (_OVERALL_SUBCHAIN, 0, name)
                    if name == "dim_height"
                    else (_LOC_SUBCHAIN, order_values[name], name)
                ),
                on_place=lambda nm: None,
                on_drop=_drop,
                force=True,  # principal dims: only a physically full strip drops them
                # …and when it IS physically full, they outrank ordinary auto dims rather
                # than tying with them at 0 and losing on the generated key.
                priority=_PRINCIPAL_CHAIN_PRIORITY,
                require_clear_ink=name.startswith("dim_step_"),
                feature=step
                if name != "dim_height"
                else overall.ref
                if overall is not None
                else None,
                measurement=mid,  # the rung's own compiled id
                footprint=_foot,
            ),
        )
    # A short rise needs external arrows, whose ink occupies most of the usual right-hand
    # height ladder. Solve these exceptional rungs in the equivalent left strip so they do
    # not make the mandatory overall height infeasible.
    left_edge = _left - 2
    for i, rung in enumerate(short_rungs):
        name = f"dim_step_{i}"
        zbase, ztop = _zspan(rung)
        # Same composition as the main chain: this is the SHORT-RISE escape, solved in the left
        # strip because external arrows would swamp the right one. It is the same measurement
        # by another route, and it dropped the tolerance.
        label = rung.final_label + _tol_suffix(rung.tolerance, draft)

        def _build_left(pos, zbase=zbase, ztop=ztop, label=label, measurement_span=rung.span):
            dim = _dim(
                (left_edge, zbase, 0),
                (left_edge, ztop, 0),
                "left",
                left_edge - pos,
                draft,
                label=label,
            )
            dim._dw_measurement_span = measurement_span
            return dim

        def _drop_left(nm, measurement=rung.id, measurement_span=rung.span):
            msg = full_strip_message(
                "short step-height dimension dropped (front-view left strip full)",
                dwg,
                frame.zones(view).left,
                view,
                "x",
            )
            ctx.record_issue(
                "error",
                "placement_unsatisfiable",
                msg,
                measurement=measurement,
                measurement_span=measurement_span,
                outcome_stage="placement",
            )

        register_corridor(
            ctx,
            (view, "left"),
            frame.zones(view).left,
            view,
            "x",
            tier,
            CorridorCandidate(
                name=name,
                build=_build_left,
                order=(_SIZE_SUBCHAIN, i, name),
                on_place=lambda nm: None,
                on_drop=_drop_left,
                force=True,
                require_clear_ink=True,
                feature=step,
                measurement=rung.id,
                footprint=lambda pos, zbase=zbase, ztop=ztop, label=label: dim_footprint(
                    (left_edge, zbase, 0),
                    (left_edge, ztop, 0),
                    "left",
                    left_edge - pos,
                    draft,
                    label,
                ),
            ),
        )
    return len(chain) + len(short_rungs)


def render_step_positions(dwg, plan, frame, *, ctx) -> int:
    """Prismatic step POSITIONS (#555): where each shoulder sits along its axis,
    dimensioned from the part datum so a stepped block is fully constrained (the step
    heights alone leave the shoulder location implicit — two geometries draw the same
    sheet). A Y shoulder is a horizontal dim on the side (end) view (which maps Y
    horizontally, where the step profile reads); an X shoulder is above the plan view —
    the same axis→view mapping the hole-location ladder uses. Mixed-axis transitions use
    the side-below strip so they do not collide with the isometric furniture above.
    A shoulder whose strip is full drops with a lint code, not silently.

    Migrated to the ADR 4 (was 0016) boundary: the shoulder chain arrives as the compiled plan's
    ``step_position`` :class:`ApprovedLadder`, and each rung's span carries the datum and
    the station it runs between, so this pass never reaches for `step.shoulders` or the
    bounding box. Returns the count placed."""
    ladder = plan.ladder("step_position")
    rungs = list(ladder.rungs) if ladder is not None else []
    if not rungs:
        return 0
    draft = dwg.draft

    def _axis_of(rung):
        """The compiler-owned shoulder direction.

        A span normally reveals its varying coordinate, but a shoulder on its own datum
        has a degenerate span and reveals no direction at all. Axis is therefore explicit
        structural content on the approved rung, not inferred placement policy."""
        if rung.axis not in ("x", "y"):
            raise AssertionError(f"step-position rung has no X/Y axis: {rung.axis!r}")
        return rung.axis

    axes = {_axis_of(r) for r in rungs}
    mixed_axes = len(axes) > 1
    # Dense transition ladders need only one text tier: arrowhead clearance is
    # along the measured axis, not between outward ladder tiers. Retain
    # the established spacing for ordinary single-axis stepped profiles.
    tier = draft.font_size + (
        draft.pad_around_text if len(rungs) > 2 else 2 * draft.pad_around_text
    )
    n = 0
    counts: dict = {"x": 0, "y": 0}
    # Page-space view edges: the ladder anchors on the view silhouette, which is layout,
    # while the STATIONS come from the approved spans, which is content.
    _sl, _sr, side_bottom, side_top = frame.edges("side")
    _pl, _pr, _pb, plan_top = frame.edges("plan")
    for rung in rungs:
        axis = _axis_of(rung)
        lo, hi = rung.span
        val = rung.value
        i = counts[axis]
        counts[axis] += 1
        if axis == "y" and mixed_axes:
            # Keep mixed-axis Y-profile stations below the side view. The iso
            # caption lives above it and is emitted after the corridor drain,
            # so an above ladder could not see/avoid that furniture.
            view, strip, direction = "side", frame.sv_zones.below, "below"
            p1 = (frame.project(view, lo)[0], side_bottom)
            p2 = (frame.project(view, hi)[0], side_bottom)
        elif axis == "y":
            view, strip, direction = "side", frame.sv_zones.above, "above"
            p1 = (frame.project(view, lo)[0], side_top)
            p2 = (frame.project(view, hi)[0], side_top)
        else:  # x — shoulder along X → above the plan view
            view, strip, direction = "plan", frame.pv_zones.above, "above"
            p1 = (frame.project(view, lo)[0], plan_top)
            p2 = (frame.project(view, hi)[0], plan_top)
        edge = p1[1]
        name = f"dim_shoulder_{axis}{i}"

        # The compiler's label plus its tolerance — a shoulder states a position the author can
        # tolerance like any other.
        shoulder_label = rung.final_label + _tol_suffix(rung.tolerance, draft)

        def _build(
            pos,
            p1=p1,
            p2=p2,
            edge=edge,
            label=shoulder_label,
            direction=direction,
            measurement_span=rung.span,
        ):
            dim = _dim(
                (p1[0], edge, 0),
                (p2[0], edge, 0),
                direction,
                pos - edge if direction == "above" else edge - pos,
                draft,
                label=label,
            )
            dim._dw_measurement_span = measurement_span
            return dim

        def _drop(
            nm,
            label=rung.final_label,
            view=view,
            direction=direction,
            measurement=rung.id,
            measurement_span=rung.span,
        ):
            ctx.record_issue(
                "warning",
                "step_position_dropped",
                f"step position {label} not dimensioned ({view} {direction}-strip full)",
                measurement=measurement,
                measurement_span=measurement_span,
            )

        # ADR 2 (was 0009) corridor candidate: a shoulder position is a datum-referenced
        # location dim — force-kept in the datum-distance ladder, co-solving with the hole
        # locations that share this above-view strip (was a solver-invisible carve).
        # No cross-dedup against hole locations (dedup=None): a hole at the shoulder's exact
        # station sits on the full-span riser and suppresses the shoulder's own recognition,
        # so a live shoulder and a coincident hole-location dim never co-exist — there is no
        # duplicate to collapse, and deduping would only couple the shoulder to the hole's
        # lifecycle for no benefit.
        register_corridor(
            ctx,
            (view, direction),
            strip,
            view,
            "y",
            tier,
            CorridorCandidate(
                name=name,
                build=_build,
                order=(_LOC_SUBCHAIN, val, name),
                on_place=lambda nm: None,
                on_drop=_drop,
                force=True,
                # The opaque provenance handle, passed straight through.
                feature=ladder.ref,
                measurement=rung.id,
            ),
        )
        n += 1
    return n


def _global_axis_centerline(first, second):
    """A turning-axis centreline whose tip attachment is intentional (#1166)."""

    centerline = Centerline(first, second)
    centerline.is_global_axis_centerline = True
    return centerline


def render_rotational(dwg, plan, a, *, ctx) -> int:
    """Rotational furniture from the IR `RotationalFeature` (#237): the OD dim (above
    the profile view), rotation-axis centrelines on planned profile projections, and concentric
    bore leaders stacked to the left of the front view. Returns the count placed.

    The OD/bore dimensions consume only approved compiled entries. Suppressed entries
    therefore cannot reach either a label or the geometry used to place that label.
    Axis centrelines are furniture and remain even when every diameter is omitted."""
    g = next(iter(plan.of_kind("rotational")), None)
    if g is None:
        return 0
    draft = dwg.draft
    FX, FZ = a.proj.front_x, a.proj.front_z
    SX, SZ = a.proj.side_x, a.proj.side_z
    PX, PY = a.proj.plan_x, a.proj.plan_y
    n = 0
    axis = g.facts.frame.axis
    od_dim = g.dim(kind="diameter", role="od")
    bore_dims = [d for d in g.dims if d.kind == "diameter" and d.role == "bore"]

    def _dia_label(dim):
        # Planner-fed value + authored tolerance/fit suffix.
        return f"ø{dim.value_text}{_tol_suffix(dim.tolerance, draft)}"

    def _place_axis_centerline(item, name, view):
        # Automatic view selection may omit one of a turned body's two equivalent profile
        # projections. Furniture is not a semantic requirement and therefore does not pass
        # through the dimension planner's missing-view gate; guard it explicitly so it cannot
        # leave an orphan dashed line at the absent view's former page position.
        if view in dwg.views:
            ctx.place(item, name, view=view)

    if axis == "z":
        # Vertical turning axis (the common case): OD across the top of the front
        # (profile) view; axis centrelines vertical on front + side.
        if od_dim is not None:
            od = od_dim.value
            ctx.place(
                _dim(
                    (FX(a.cx - od / 2), FZ(a.bb.max.Z) + 2, 0),
                    (FX(a.cx + od / 2), FZ(a.bb.max.Z) + 2, 0),
                    "above",
                    8,
                    draft,
                    label=_dia_label(od_dim),
                ),
                "dim_od",
                view="front",
                measurement=od_dim.measurement_ids,
            )
            n += 1
        _place_axis_centerline(
            _global_axis_centerline(
                (FX(a.cx), FZ(a.bb.min.Z) - 5, 0),
                (FX(a.cx), FZ(a.bb.max.Z) + 5, 0),
            ),
            "centerline_front",
            "front",
        )
        _place_axis_centerline(
            _global_axis_centerline(
                (SX(a.cy), SZ(a.bb.min.Z) - 5, 0),
                (SX(a.cy), SZ(a.bb.max.Z) + 5, 0),
            ),
            "centerline_side",
            "side",
        )

        # Concentric bore leaders to the left of the front view, centred on the axis.
        if bore_dims:
            left_edge = FX(a.bb.min.X)
            if left_edge - _analysis_margins(a).left >= a.DIM_PAD:
                elbow_x = left_edge - a.DIM_PAD * 0.6
                pitch = max(10.0, draft.font_size * 3.0)
                # Bound the leader stack to the front-view height through the
                # shared solve. Symmetric natural positions stay fixed when they
                # fit; an overfull band compresses or drops by bore priority.
                nb = len(bore_dims)
                z_lo, z_hi = a.FV_Y - a.fv_hh, a.FV_Y + a.fv_hh
                cands = [
                    StripCandidate(
                        key=f"{i:03d}",
                        anchor=(elbow_x, FZ(a.cz) + (i - (nb - 1) / 2) * pitch),
                        size=(draft.font_size * 3, pitch),
                        priority=d,
                    )
                    for i, dim in enumerate(bore_dims)
                    for d in (dim.value,)
                ]
                placed = plan_strip(cands, z_lo, z_hi, pitch, axis="y").placed
                for i, dim in enumerate(bore_dims):
                    d = dim.value
                    tip_z = placed.get(f"{i:03d}")
                    if tip_z is None:
                        continue  # over the front-view capacity — dropped (ranked), logged below
                    ctx.place(
                        Leader(
                            tip=(FX(a.cx - d / 2), tip_z, 0),
                            elbow=(elbow_x, tip_z, 0),
                            label=_dia_label(dim),
                            draft=draft,
                        ),
                        f"ldr_z{i}",
                        view="front",
                        measurement=dim.id,
                    )
                    n += 1
                dropped = [
                    dim.value for i, dim in enumerate(bore_dims) if placed.get(f"{i:03d}") is None
                ]
                for d in dropped:
                    ctx.coverage.drop_diam(d)  # exclude from coverage to avoid double reporting
                if dropped:
                    ctx.record_issue(
                        "warning",
                        "callout_dropped",
                        f"{len(dropped)} concentric-bore diameter(s) {dropped} not annotated "
                        "(front-view height full) — use a detail view",
                    )
            else:
                _log.info(
                    "Additional diameters %s not annotated (insufficient left margin)",
                    [dim.value for dim in bore_dims],
                )
    elif axis == "x":
        # Horizontal turning axis along X: the OD is the Z extent — a vertical
        # ø dim left of the front (profile) view; axis centrelines run horizontally
        # through z=cz on front and y=cy on plan.
        if od_dim is not None:
            od = od_dim.value
            ctx.place(
                _dim(
                    (FX(a.bb.min.X) - 2, FZ(a.cz - od / 2), 0),
                    (FX(a.bb.min.X) - 2, FZ(a.cz + od / 2), 0),
                    "left",
                    8,
                    draft,
                    label=_dia_label(od_dim),
                ),
                "dim_od",
                view="front",
                measurement=od_dim.measurement_ids,
            )
            n += 1
        _place_axis_centerline(
            _global_axis_centerline(
                (FX(a.bb.min.X) - 5, FZ(a.cz), 0),
                (FX(a.bb.max.X) + 5, FZ(a.cz), 0),
            ),
            "centerline_front",
            "front",
        )
        _place_axis_centerline(
            _global_axis_centerline(
                (PX(a.bb.min.X) - 5, PY(a.cy), 0),
                (PX(a.bb.max.X) + 5, PY(a.cy), 0),
            ),
            "centerline_plan",
            "plan",
        )
    elif axis == "y":
        # Horizontal turning axis along Y: the OD is the Z extent — a vertical
        # ø dim left of the side (profile) view; axis centrelines run horizontally
        # through z=cz on side and vertically through x=cx on plan.
        if od_dim is not None:
            od = od_dim.value
            ctx.place(
                _dim(
                    (SX(a.bb.min.Y) - 2, SZ(a.cz - od / 2), 0),
                    (SX(a.bb.min.Y) - 2, SZ(a.cz + od / 2), 0),
                    "left",
                    8,
                    draft,
                    label=_dia_label(od_dim),
                ),
                "dim_od",
                view="side",
                measurement=od_dim.measurement_ids,
            )
            n += 1
        _place_axis_centerline(
            _global_axis_centerline(
                (SX(a.bb.min.Y) - 5, SZ(a.cz), 0),
                (SX(a.bb.max.Y) + 5, SZ(a.cz), 0),
            ),
            "centerline_side",
            "side",
        )
        _place_axis_centerline(
            _global_axis_centerline(
                (PX(a.cx), PY(a.bb.min.Y) - 5, 0),
                (PX(a.cx), PY(a.bb.max.Y) + 5, 0),
            ),
            "centerline_plan",
            "plan",
        )
    return n


def render_local_turned_centerlines(dwg, a, *, ctx) -> int:
    """Show the axis of a local turned stack on a non-rotational part.

    Mounting lugs can prevent the complete part from classifying as rotational
    while ``a.prof`` still identifies a coaxial stepped stack. Its centered bore
    may be located by that axis only when the axis is actually present on the
    drawing. Mirror the two centerlines from :func:`render_rotational` without
    adding an overall-OD dimension for the non-rotational envelope (#881).
    """
    prof = a.prof
    if a.is_rotational or prof is None:
        return 0
    axis = prof.axis
    FX, FZ = a.proj.front_x, a.proj.front_z
    SX, SZ = a.proj.side_x, a.proj.side_z
    PX, PY = a.proj.plan_x, a.proj.plan_y
    placed = 0

    def _place(item, name, view):
        nonlocal placed
        if view not in dwg.views:
            return
        if ctx.registry.named(name) is not None:
            return
        ctx.place(item, name, view=view)
        placed += 1

    if axis == "x":
        _place(
            _global_axis_centerline(
                (FX(a.bb.min.X) - 5, FZ(a.cz), 0),
                (FX(a.bb.max.X) + 5, FZ(a.cz), 0),
            ),
            "centerline_front",
            "front",
        )
        _place(
            _global_axis_centerline(
                (PX(a.bb.min.X) - 5, PY(a.cy), 0),
                (PX(a.bb.max.X) + 5, PY(a.cy), 0),
            ),
            "centerline_plan",
            "plan",
        )
    elif axis == "y":
        _place(
            _global_axis_centerline(
                (SX(a.bb.min.Y) - 5, SZ(a.cz), 0),
                (SX(a.bb.max.Y) + 5, SZ(a.cz), 0),
            ),
            "centerline_side",
            "side",
        )
        _place(
            _global_axis_centerline(
                (PX(a.cx), PY(a.bb.min.Y) - 5, 0),
                (PX(a.cx), PY(a.bb.max.Y) + 5, 0),
            ),
            "centerline_plan",
            "plan",
        )
    else:
        return 0
    return placed


def _record_pmi_drop(ctx, dwg, ax, label, rec):
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


def _record_pmi_unrenderable(dwg, label, rec, *, ctx):
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
        f"reference geometry is unresolved: {reasons}",
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


def _pmi_witness_from_bbox(rec, view: str, a):
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
        avg_x = (xmin + xmax) / 2
        lo, hi = min(point[1] for point in pts), max(point[1] for point in pts)
        p1 = (PX(avg_x), PY(lo), 0)
        p2 = (PX(avg_x), PY(hi), 0)
        avg_t = PX(avg_x)
    else:
        return None

    span = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    # The helper draws short dimensions with external arrows/text. Refusing them at an
    # arbitrary 3 page-mm threshold lost GRM-03's valid 0.5 mm axial station even though
    # the shared strip solve can place it without changing the measured path.
    if span <= 1e-6:
        return None
    return p1, p2, avg_t


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

    def _build(pos, _q1=q1, _q2=q2, _side=side, _w=witness, _label=label):
        # Dimension's extension-gap convention places the actual line one gap back toward
        # its witnesses. Compensate so the solver's stacking coordinate is the rendered
        # line/label coordinate, keeping the first tier outside the view silhouette.
        dist = (
            pos - _w + draft.extension_gap
            if _side in ("above", "right")
            else _w - pos + draft.extension_gap
        )
        return _dim(_q1, _q2, _side, dist, draft, label=_label)

    order_coord = min(perp)
    spec = {
        "name": name,
        "build": _build,
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


def _oblique_linear_specs(a, rec, label, name, draft):
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
    legs = tuple(zip(route, route[1:]))
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
            or bool(getattr(annotation, "_dw_dimension_candidate", False))
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
        route_segments = tuple(zip(route, route[1:]))
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
                if any(left == right for left, right in zip(route, route[1:])):
                    continue
                if _route_blocked(route):
                    continue
                length = sum(
                    math.hypot(right[0] - left[0], right[1] - left[1])
                    for left, right in zip(route, route[1:])
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


def _oblique_cylinder_leader_specs(a, rec, label, name, draft):
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
):
    # *trace*: a PMI dim's post-drop fallback is a standalone strip pass —
    # traced as a pass_event like the other standalone placers.
    left = place_strip_candidates(
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


def _pmi_queue_options(dwg, ctx, options, ax, label, rec):
    specs = [s for s in options if s is not None]
    if not specs:
        return False
    primary, alternates = specs[0], specs[1:]

    def _drop(nm, _alts=alternates, _ax=ax, _label=label, _rec=rec):
        for alt in _alts:
            if _place_corridor_option(dwg, alt, _rec, ctx=ctx, trace=ctx.trace):
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
            fallback = _sheet_leader_fallback(
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
        _record_pmi_drop(ctx, dwg, _ax, _label, _rec)

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


def _pmi_front_linear(dwg, a, ctx, rec, ax, label, name, primary, secondary, center):
    """An X- or Z-dominant linear PMI dim in the FRONT view (the two share one shape): the
    witness spans the ref bbox; place ``[primary, secondary]`` when the perpendicular
    midpoint sits on the primary side of the view centre, else fall back to ``[secondary]``
    alone. Returns True/False placed, or ``None`` for a degenerate (no-witness) reference
    so the caller can report it as a validation failure."""
    draft = dwg.draft
    wp = _pmi_witness_from_bbox(rec, "front", a)
    if wp is None:
        return None
    p1, p2, avg = wp
    zones = {
        "above": a.fv_zones.above,
        "below": a.fv_zones.below,
        "right": a.fv_zones.right,
        "left": a.fv_zones.left,
    }
    if rec.side is not None:
        sides = [s for s in (primary, secondary) if rec.side == s]
        return _pmi_queue_options(
            dwg,
            ctx,
            [_pmi_dim_spec(p1, p2, zones[s], label, name, "front", s, draft) for s in sides],
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
                _pmi_dim_spec(p1, p2, zones[primary], label, name, "front", primary, draft),
                _pmi_dim_spec(p1, p2, zones[secondary], label, name, "front", secondary, draft),
            ],
            ax,
            label,
            rec,
        )
    if not placed:
        placed = _pmi_queue_options(
            dwg,
            ctx,
            [_pmi_dim_spec(p1, p2, zones[secondary], label, name, "front", secondary, draft)],
            ax,
            label,
            rec,
        )
    return placed


def _angular_specs(a, reference, label, name, draft, *, side=None, implicit_degrees=False):
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


def _place_pmi_record(dwg, a, ctx, rec, idx, bore_cfg, draft) -> bool:
    """Place one PMI record; returns True when it was queued/placed on a strip.

    The old per-record dispatch of ``render_pmi``: diameter/radius via ``bore_cfg`` (the
    ``_bore`` table), X/Z linears via the shared front-view shape, Y linears via the
    side-then-plan fallback. A degenerate reference is recorded unrenderable and returns
    False without escalating to the bottom drop.
    """
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
            if half_span_pg >= _MIN_INPLACE_BORE_HALF_MM:
                p1, p2 = cfg["span"](cx_f, cy_f, cz_f, lo, hi)
                order = tuple(
                    s
                    for s in cfg["order"]
                    if (rec.view is None or rec.view == cfg["view"])
                    and (rec.side is None or rec.side == s)
                )
                placed = _pmi_queue_options(
                    dwg,
                    ctx,
                    [
                        _pmi_dim_spec(
                            p1,
                            p2,
                            cfg["zones"][s],
                            label,
                            name_d,
                            cfg["view"],
                            s,
                            draft,
                            leader_fallback=True,
                        )
                        for s in order
                    ],
                    ax,
                    label,
                    rec,
                )
            else:
                leader_order = tuple(
                    s
                    for s in cfg["leader_order"]
                    if (rec.view is None or rec.view == cfg["view"])
                    and (rec.side is None or rec.side == s)
                )
                placed = _pmi_queue_options(
                    dwg,
                    ctx,
                    [
                        _pmi_leader_spec(
                            (u, v + (surface_pg if s == "above" else -surface_pg), 0),
                            cfg["zones"][s],
                            label,
                            name_d,
                            cfg["view"],
                            s,
                            draft,
                        )
                        for s in leader_order
                    ],
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
        placed = _pmi_front_linear(dwg, a, ctx, rec, ax, label, name_x, "above", "below", a.FV_Y)
        if placed is None:
            _log.debug("PMI dim[%d] X: degenerate reference", idx)
            _record_pmi_unrenderable(dwg, label, rec, ctx=ctx)
            return False

    elif ax == "Z":
        placed = _pmi_front_linear(dwg, a, ctx, rec, ax, label, name_z, "right", "left", a.FV_X)
        if placed is None:
            _log.debug("PMI dim[%d] Z: degenerate reference", idx)
            _record_pmi_unrenderable(dwg, label, rec, ctx=ctx)
            return False

    elif ax == "Y" and (rec.view is not None or rec.side is not None):
        # A degenerate reference (no witness in EITHER candidate view) is a validation
        # failure, not a placement one — report it distinctly.
        if (
            _pmi_witness_from_bbox(rec, "side", a) is None
            and _pmi_witness_from_bbox(rec, "plan", a) is None
        ):
            _log.debug("PMI dim[%d] Y: degenerate reference", idx)
            _record_pmi_unrenderable(dwg, label, rec, ctx=ctx)
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
            _record_pmi_unrenderable(dwg, label, rec, ctx=ctx)
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


def render_pmi(dwg, model, a, *, ctx) -> int:
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
    usable = _renderable_pmi_records(pmi)
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
    # the in-place above/below fallback; `leader_order` the narrow-bore one (Y historically
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
    for idx, rec in enumerate(usable):
        if _place_pmi_record(dwg, a, ctx, rec, idx, _bore, draft):
            queued += 1
    _log.info("PMI annotate: %d/%d dims queued", queued, len(usable))
    return queued


# GD&T aspect side-layer (ADR 4 (was 0011 §4)) — declared feature control frames / datum
# feature symbols / surface finishes. Placed as first-class ADR 2 (was 0009) corridor candidates,
# NOT through the dimension planner (their IR items carry no DimParameters). "note" is a
# free-text manufacturing note — the same leader-into-a-strip mechanism, glyph = text.
_GDT_KINDS = ("control_frame", "datum_ref", "finish", "note")
# Authored-intent run of the shared corridor ladder: GD&T frames tier BEYOND the
# feature-size (_SIZE_SUBCHAIN=0), datum-location (_LOC_SUBCHAIN=1), and overall
# envelope (_OVERALL_SUBCHAIN=2) dim runs, so a frame never lands mid-ladder among
# the dimensions it annotates.
_GDT_SUBCHAIN = 3
# Over-capacity survival rank for an authored GD&T frame: a declared control frame /
# datum / finish / note is deliberate intent, so on a strip too full for every candidate it is
# kept over the auto dims (locations/slots, priority 0) rather than dropped by stacking-key order.
_GDT_CORRIDOR_PRIORITY = PRIORITY.AUTHORED
# Minimum GD&T leader shaft length (page-mm). A zero-length Leader (site == solved tier)
# makes OCC's edge builder raise; nudging to this keeps `_build` total.
_MIN_LEADER = 0.05


def _pmi_source_ids(item) -> tuple[str, ...]:
    plural = tuple(getattr(item, "source_ids", ()))
    singular = getattr(item, "source_id", "")
    return tuple(dict.fromkeys(((singular,) if singular else ()) + plural))


def render_document_notes(dwg, model, *, exclude=()) -> int:
    """Place source-proven drawing-wide requirements in one solver-owned notes block."""
    placed_datum_sources = {
        source_id
        for feature in model.features
        if feature.kind == "datum_ref"
        and dwg.registry.names_for_feature(getattr(feature, "origin", None) or feature)
        for source_id in _pmi_source_ids(feature)
    }
    notes = [
        feature
        for feature in model.features
        if feature.kind == "document_note"
        and (
            feature.on_drawing
            or bool(feature.represented_by_source_ids)
            and not set(feature.represented_by_source_ids) <= placed_datum_sources
        )
        and id(feature) not in exclude
    ]
    if not notes:
        return 0
    rows = document_note_rows(notes)
    placed = dwg.add_table(
        rows,
        prefer="tr",
        name="general_notes",
        _source_ids=tuple(source_id for note in notes for source_id in _pmi_source_ids(note)),
        _features=tuple(notes),
        _drop_code="pmi_dropped",
        _drop_severity="error",
        _left_align_cols=(0,),
    )
    return len(notes) if placed is not None else 0


def _gdt_glyph(item, draft):
    """Build the ISO 1101/5459/1302 glyph sketch for one GD&T IR item at the origin
    (the :class:`Leader` repositions it). A fresh sketch per call — the leader translate
    must not alias a shared object across the strip solve's repeated probe builds."""
    if item.kind == "control_frame":
        tolerance = item.display_tolerance or item.tolerance
        return FeatureControlFrame(
            item.characteristic,
            tolerance,
            datums=item.datums,
            draft=draft,
            diameter=item.diameter,
            modifier=item.modifier,
        )
    if item.kind == "datum_ref":
        return DatumFeature(item.letter, draft=draft)
    if item.kind == "note":  # free-text manufacturing note — a single-line text glyph
        return TextBlock([_font_safe_text(item.text)], position=(0.0, 0.0), draft=draft)
    return SurfaceFinish(item.ra, position=(0.0, 0.0), draft=draft)


def _gdt_pdf_text_specs(glyph, item, draft) -> tuple:
    """Token-level semantic text in coordinates relative to *glyph*'s centre.

    GD&T characteristic/diameter/material-condition rings and surface-finish marks remain
    vector geometry. Their adjacent values and datum letters use the exact cell anchors from
    the helper renderer so selection aligns without treating the whole compound glyph as one
    centred string.
    """
    box = glyph.bounding_box()
    gcx, gcy = (box.min.X + box.max.X) / 2.0, (box.min.Y + box.max.Y) / 2.0
    h = draft.font_size
    font_path = getattr(draft, "font_path", DEFAULT_FONT_PATH)
    font_name = getattr(draft, "font", "Arial")
    specs = []

    def add(value, x, y, size=h, *, h_align="center"):
        specs.append(
            (
                _font_safe_text(value),
                x - gcx,
                y - gcy,
                size,
                font_path,
                font_name,
                "REGULAR",
                h_align,
                "middle",
            )
        )

    if item.kind == "datum_ref":
        x0, y0, x1, y1 = glyph.label_bbox
        add(glyph.label, (x0 + x1) / 2.0, (y0 + y1) / 2.0)
        return tuple(specs)
    if item.kind == "finish":
        x0, y0, x1, y1 = glyph.label_bbox
        add(glyph.label, (x0 + x1) / 2.0, (y0 + y1) / 2.0)
        return tuple(specs)
    if item.kind != "control_frame":
        return ()

    # Mirror helpers._gdt_tol_cell / _gdt_datum_cell. The renderer deliberately
    # uses regular text even when the surrounding Draft requests another style.
    H = 2.0 * h
    pad, diameter_radius, modifier_radius = 0.6 * h, 0.42 * h, 0.62 * h
    x = H + pad
    if item.diameter:
        diameter_cx = x + diameter_radius
        add("ø", diameter_cx, H / 2.0)
        x = diameter_cx + diameter_radius + pad
    tolerance = item.display_tolerance or item.tolerance
    tolerance_width = _text_size(tolerance, h, font_path, font_name)[0]
    tolerance_cx = x + tolerance_width / 2.0
    add(tolerance, tolerance_cx, H / 2.0)
    x = tolerance_cx + tolerance_width / 2.0 + pad
    if item.modifier:
        modifier_cx = x + modifier_radius
        add(item.modifier.upper(), modifier_cx, H / 2.0, size=0.8 * h)

    tolerance_cell_width = (
        pad
        + tolerance_width
        + pad
        + (2.0 * diameter_radius + pad if item.diameter else 0.0)
        + (2.0 * modifier_radius + pad if item.modifier else 0.0)
    )
    datum_start = H + tolerance_cell_width
    for index, letter in enumerate(item.datums):
        add(letter, datum_start + (index + 0.5) * H, H / 2.0)
    return tuple(specs)


def render_gdt(dwg, model, a, *, ctx) -> int:
    """Place declared GD&T frames / datum symbols / surface finishes (#61) as first-class
    ADR 2 (was 0009) corridor candidates — registered into the SAME strip the feature's dimensions
    use, BEFORE ``drain_corridors``, so one solve orders and spaces them crossing-free with
    the dims. Each item carries its target ``(view, side)`` strip + model-space site; the
    leader hangs the glyph off the site into that strip. The strip footprint is the GLYPH's
    own box — NOT the leader+glyph box, whose shaft back to the feature would inflate the
    stacking extent (the same reason dims reserve one label-height). Cross-view separation
    is the compose-then-pack repack's job (ADR 2 (was 0004)): every placed frame is ``view=``-tagged,
    so ``_measure_blocks`` folds it into the block. Returns the count registered."""
    items = [
        f
        for f in model.features
        if f.kind in _GDT_KINDS
        # Automatic AP242 discovery keeps typed IR available in report mode, but report must
        # not draw it. Explicit/round-tripped models remain declarations and therefore render.
        and (
            not _pmi_source_ids(f)
            or a.pmi_mode == "annotate"
            or (ctx.model_declared and id(f) not in ctx.document_source_annotation_ids)
        )
    ]
    if not items:
        return 0
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text
    # (zones, h-projector, v-projector, h-model-index, v-model-index) per view.
    views = {
        "plan": (a.pv_zones, a.proj.plan_x, a.proj.plan_y, 0, 1),
        "front": (a.fv_zones, a.proj.front_x, a.proj.front_z, 0, 2),
        "side": (a.sv_zones, a.proj.side_x, a.proj.side_z, 1, 2),
    }
    # The title block (bottom-right) is added AFTER drain_corridors, so strip placement can't
    # see it — a below/right strip runs down into its region. Its box is deterministic, so
    # reject any GD&T placement that would land on it (BOTH the primary corridor path, via the
    # candidate's `forbid`, AND the fallthrough) else the frame overlaps 'DRAWING'.
    tb_box = _title_block_box(dwg, a)
    n = 0
    for i, item in enumerate(items):
        name = f"m_gdt{i}"
        source_ids = _pmi_source_ids(item)
        satisfaction = tuple(
            DimensionId(item.origin, parameter) for parameter in getattr(item, "satisfies", ())
        )
        vk = views.get(item.view)
        if vk is None or item.side not in ("above", "below", "left", "right"):
            ctx.record_issue(
                "warning",
                "pmi_dropped" if source_ids else "gdt_dropped",
                f"{name}: bad target {item.view!r}/{item.side!r}",
                source=source_ids,
                outcome_stage="validation",
            )
            continue
        zones, hproj, vproj, hi, vi = vk
        strip = getattr(zones, item.side)
        o = item.frame.origin
        if (
            item.kind in ("finish", "note")
            and isinstance(item.origin, ChamferFeature | FilletFeature)
            and item.origin.turned
        ):
            o = _turned_profile_site(item.origin.frame.origin, item.origin.axis, item.view, a.cyls)
        px, py = hproj(o[hi]), vproj(o[vi])
        horizontal = item.side in ("above", "below")  # frame stacks along y
        axis = "y" if horizontal else "x"
        # The IR is public input (ADR 4 (was 0011)), so an invalid glyph spec (a mistyped
        # characteristic, a bad tolerance) must drop THIS item with a warning — never crash
        # the whole drawing build. The helper raises on a bad spec; catch it at the measure
        # (the first build) and drop. `_build` below re-runs `_gdt_glyph` with the same args
        # (so a spec error can't reappear there) AND is made total against the OTHER raise
        # source — a zero-length Leader shaft (see the min-leader guard in `_build`).
        try:
            fallback_glyph = _gdt_glyph(item, draft)
            gb = fallback_glyph.bounding_box().size
        except Exception as e:  # noqa: BLE001 — any glyph-spec error drops one item, not the build
            ctx.record_issue(
                "warning",
                "pmi_dropped" if source_ids else "gdt_dropped",
                f"{name}: cannot render ({type(e).__name__}: {e})",
                source=source_ids,
                outcome_stage="validation",
            )
            continue
        size = (gb.X, gb.Y)

        def _build(pos, _px=px, _py=py, _hz=horizontal, _it=item):
            g = _gdt_glyph(_it, draft)
            tip = (_px, _py)
            # A zero-length leader shaft (the projected site coincides with the solved tier —
            # `pos == py` above/below, `pos == px` left/right) makes OCC's edge builder raise,
            # which would crash the whole build on a public-IR declaration. Guarantee a
            # minimum shaft along the stacking axis (nudge outward; 0.05 mm is invisible) so
            # `_build` is total — the drop-don't-crash invariant holds for every build call.
            if _hz:
                dy = pos - _py
                pos = (
                    pos if abs(dy) >= _MIN_LEADER else _py + math.copysign(_MIN_LEADER, dy or 1.0)
                )
                elbow = (_px, pos)
            else:
                dx = pos - _px
                pos = (
                    pos if abs(dx) >= _MIN_LEADER else _px + math.copysign(_MIN_LEADER, dx or 1.0)
                )
                elbow = (pos, _py)
            leader = Leader(
                tip=tip,
                elbow=elbow,
                label="",
                draft=draft,
                callout=g,
                all_around=getattr(_it, "all_around", False),
                all_over=getattr(_it, "all_over", False),
            )
            if _it.kind == "note":
                # The outer leader intentionally has label="" because the visible
                # payload is a TextBlock callout. Preserve the authored note and measure
                # the embedded Text renderer's face-dependent newline pitch for PDF.
                leader.pdf_text = _font_safe_text(_it.text)
                leader.pdf_text_font_style = "REGULAR"
                leader.pdf_text_line_spacing = _text_line_spacing_em(
                    draft.font_size,
                    getattr(draft, "font_path", DEFAULT_FONT_PATH),
                    getattr(draft, "font", "Arial"),
                )
            else:
                leader.pdf_text_relative_specs = _gdt_pdf_text_specs(g, _it, draft)
            return leader

        def _build_at(elbow, _px=px, _py=py, _it=item, _g=fallback_glyph):
            leader = Leader(
                tip=(_px, _py),
                elbow=(*elbow, 0),
                label="",
                draft=draft,
                callout=_g,
                all_around=getattr(_it, "all_around", False),
                all_over=getattr(_it, "all_over", False),
            )
            if _it.kind == "note":
                leader.pdf_text = _font_safe_text(_it.text)
                leader.pdf_text_font_style = "REGULAR"
                leader.pdf_text_line_spacing = _text_line_spacing_em(
                    draft.font_size,
                    getattr(draft, "font_path", DEFAULT_FONT_PATH),
                    getattr(draft, "font", "Arial"),
                )
            else:
                leader.pdf_text_relative_specs = _gdt_pdf_text_specs(_g, _it, draft)
            return leader

        def _build_routed(bends, elbow, _px=px, _py=py, _it=item, _g=fallback_glyph):
            leader = RoutedLeader(
                (_px, _py),
                bends,
                elbow,
                "",
                draft,
                callout=_g,
                all_around=getattr(_it, "all_around", False),
                all_over=getattr(_it, "all_over", False),
            )
            if _it.kind == "note":
                leader.pdf_text = _font_safe_text(_it.text)
                leader.pdf_text_font_style = "REGULAR"
                leader.pdf_text_line_spacing = _text_line_spacing_em(
                    draft.font_size,
                    getattr(draft, "font_path", DEFAULT_FONT_PATH),
                    getattr(draft, "font", "Arial"),
                )
            else:
                leader.pdf_text_relative_specs = _gdt_pdf_text_specs(_g, _it, draft)
            return leader

        def _compact_candidates(
            original,
            _build=_build,
            _strip=strip,
            _size=size,
            _horizontal=horizontal,
        ):
            """Nearest-first same-strip landings checked later against exact ink.

            Dimension extension lines make their conservative boxes intentionally
            broad.  A GD&T leader may pass through the empty part of such a box, so
            the corridor result is an upper bound rather than necessarily the best
            landing.  Keep this search finite and inside the requested strip.
            """
            if original is None:
                return
            original_pos = original.elbow[1 if _horizontal else 0]
            extent = _size[1 if _horizontal else 0]
            near = _strip.anchor + _strip.direction * (_strip.gap + extent / 2.0)
            distance = (original_pos - near) * _strip.direction
            if distance <= 1e-6:
                return
            step = max(tier + _strip.spacing, 1.0)
            count = min(64, int(math.ceil(distance / step)) + 1)
            for index in range(count):
                travel = min(distance, index * step)
                pos = near + _strip.direction * travel
                if abs(pos - original_pos) <= 1e-6:
                    return
                yield _build(pos)

        def _ink_repair_candidates(
            original,
            _build=_build,
            _strip=strip,
            _size=size,
            _horizontal=horizontal,
        ):
            """Bounded outward tiers for a frame whose complete ink still conflicts.

            Inward exact-ink contraction has already run. This is the same
            corridor's remaining feature-relative space, not a raw page position.
            The shared placer checks each rebuilt frame and shaft against dimensions,
            leaders, other frames, page bounds, and fixed furniture before commit.
            """
            original_pos = original.elbow[1 if _horizontal else 0]
            extent = _size[1 if _horizontal else 0]
            outward_extent = extent / 2.0 if _horizontal else extent
            outer = _strip.outer_limit - _strip.direction * outward_extent
            available = (outer - original_pos) * _strip.direction
            step = max(tier + _strip.spacing, 1.0)
            for index in range(1, min(9, int(available // step) + 1)):
                yield _build(original_pos + _strip.direction * index * step)

        def _drop(
            nm,
            _v=item.view,
            _s=item.side,
            _zones=zones,
            _px=px,
            _py=py,
            _hz=horizontal,
            _sz=size,
            _bld=_build,
            _feat=item.origin or item,
            _tb=tb_box,
            _source=source_ids,
            _satisfaction=satisfaction,
            _global_build=_build_at,
            _routed_build=_build_routed,
            _declaration=item,
        ):
            # Fallthrough: the declared/derived side is full — try the OPPOSITE side of
            # the same view before dropping, so a congested default still places somewhere
            # legible rather than vanishing. DEFERRED via ctx.post_drain (the plate
            # pattern): the carve then runs after EVERY corridor has drained, so it cannot
            # preempt a corner a later sibling's force candidate needs. Force semantics
            # (no corridor-cross check) match the primary path, BUT reject a spot over the
            # (not-yet-placed) title block — a below/right strip runs into it, and the
            # carve can't see it.
            def _retry(
                nm=nm,
                _v=_v,
                _s=_s,
                _zones=_zones,
                _px=_px,
                _py=_py,
                _sz=_sz,
                _bld=_bld,
                _feat=_feat,
                _tb=_tb,
                _source=_source,
                _satisfaction=_satisfaction,
                _global_build=_global_build,
                _routed_build=_routed_build,
                _declaration=_declaration,
            ):
                trace = getattr(ctx, "trace", None)
                event = (
                    trace.pass_event("gdt_post_drain_fallback", view=_v, requested_side=_s)
                    if trace is not None
                    else None
                )
                trace_item = (
                    {"name": nm, "outcome": "unmet", "attempts": []} if event is not None else None
                )
                if event is not None:
                    event["items"].append(trace_item)

                # Relax the requested side when its strip is full, so
                # try the OPPOSITE side, then the two PERPENDICULAR sides, placing on the first
                # with room. A note the caller asked to see should appear somewhere legible
                # rather than vanish; when the requested strip has no room, an explicit `side=`
                # is a preference, not a hard constraint. A perpendicular side flips the leader
                # orientation (`_bld(pos, _hz=hz)`). If the placement lands on a side other than
                # requested, record an INFO issue so the relaxation is visible.
                # A requested annotation must never be silently lost.
                relax_order = {
                    "above": ("below", "right", "left"),
                    "below": ("above", "right", "left"),
                    "left": ("right", "above", "below"),
                    "right": ("left", "above", "below"),
                }[_s]
                for alt in relax_order:
                    alt_strip = getattr(_zones, alt, None)
                    if alt_strip is None:
                        continue
                    hz = alt in ("above", "below")  # perpendicular sides flip the leader axis
                    axis2 = "y" if hz else "x"
                    extent = _sz[1] if axis2 == "y" else _sz[0]  # the glyph's stacking-axis size
                    perp = (_px, _px + _sz[0]) if hz else (_py - _sz[1] / 2, _py + _sz[1] / 2)
                    pos = carve_free_position(dwg, alt_strip, _v, axis2, max(tier, extent), perp)
                    if pos is None:
                        if trace_item is not None:
                            trace_item["attempts"].append(
                                {"side": alt, "outcome": "no_free_position"}
                            )
                        continue
                    dim = _bld(pos, _hz=hz)
                    if _box_hits(_anno_box(dim), (_tb,)):
                        if trace_item is not None:
                            trace_item["attempts"].append(
                                {"side": alt, "outcome": "title_block_conflict"}
                            )
                        continue
                    if not annotation_ink_clear(dwg, dim):
                        if trace_item is not None:
                            trace_item["attempts"].append({"side": alt, "outcome": "ink_conflict"})
                        continue
                    ctx.place(
                        dim,
                        nm,
                        view=_v,
                        feature=_feat,
                        satisfaction=_satisfaction,
                        declaration=_declaration,
                    )  # relaxed side
                    ctx.record_issue(
                        "info",
                        "gdt_side_relaxed",
                        f"{nm}: the {_v} {_s} strip was full — placed on {alt} instead",
                    )
                    if trace_item is not None:
                        trace_item["attempts"].append({"side": alt, "outcome": "placed"})
                        trace_item.update(outcome="placed", side=alt)
                    return
                fallback = _sheet_leader_fallback(
                    dwg,
                    (_px, _py),
                    _v,
                    _global_build,
                    _routed_build,
                    _sz,
                )
                if fallback is not None:
                    ctx.place(
                        fallback,
                        nm,
                        view=_v,
                        feature=_feat,
                        satisfaction=_satisfaction,
                        declaration=_declaration,
                    )
                    ctx.record_issue(
                        "info",
                        "gdt_sheet_fallback",
                        f"{nm}: adjacent {_v} strips were full — placed in clear sheet space",
                        source=_source,
                    )
                    if trace_item is not None:
                        trace_item["attempts"].append({"side": "sheet", "outcome": "placed"})
                        trace_item.update(outcome="placed", side="sheet")
                    return
                if trace_item is not None:
                    trace_item["attempts"].append({"side": "sheet", "outcome": "no_clear_route"})
                ctx.record_issue(
                    "warning",
                    "pmi_dropped" if _source else "gdt_dropped",
                    f"{nm} not placed (no legible room in any {_v} strip or sheet fallback)",
                    source=_source,
                    outcome_stage="placement",
                )

            ctx.post_drain.append(_retry)

        register_corridor(
            ctx,
            (item.view, item.side),
            strip,
            item.view,
            axis,
            tier,
            CorridorCandidate(
                name=name,
                build=_build,
                order=(_GDT_SUBCHAIN, px if horizontal else py, name),
                on_place=lambda nm: None,
                on_drop=_drop,
                dedup=None,
                precedence=0,
                priority=_GDT_CORRIDOR_PRIORITY,  # authored intent outranks auto dims
                # A declared frame has no alternate view — force-keep (policy B) rather than
                # drop a user-authored annotation; only a physically full strip drops.
                force=True,
                # Declared frames belong to their decorated feature. An imported frame has
                # external source provenance but no separately-owned geometric IR feature.
                feature=item.origin or item,
                satisfaction=satisfaction or None,
                declaration=item,
                size=size,
                compact_candidates=_compact_candidates,
                ink_repair_candidates=_ink_repair_candidates,
                require_clear_ink=True,
                # Even a force-kept frame must not stack into the title block —
                # place_strip_candidates rejects a placement hitting this box, then on_drop's
                # fallthrough tries the other side.
                forbid=tb_box,
            ),
        )
        n += 1
    return n
