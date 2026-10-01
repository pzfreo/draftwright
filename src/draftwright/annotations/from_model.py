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
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

from build123d_drafting.helpers import (
    DEFAULT_FONT_PATH,
    CenterMark,
    Dimension,
    HoleCallout,
    Leader,
)

from draftwright._core import (
    _EDGE_ON,
    _END_ON,
    Strip,
    _dim,
    _drawing_bounds,
    _fmt,
    _greedy_strip_ys,
    _iso_bbox,
    _solve_strip_ys,
    _text_line_spacing_em,
    _text_size,
    _tol_suffix,
    _wrap_callout_text,
    layout_frame,
)
from draftwright._geometry import (
    _boxes_overlap,
    _segment_clips_box,
)
from draftwright.annotations._axial_render import (
    _draw_step_chain as _draw_step_chain,
)
from draftwright.annotations._axial_render import (
    _global_axis_centerline as _global_axis_centerline,
)
from draftwright.annotations._axial_render import (
    _next_steplen_start as _next_steplen_start,
)
from draftwright.annotations._axial_render import (
    _record_step_chain_drop as _record_step_chain_drop,
)
from draftwright.annotations._axial_render import (
    _render_height_ladder_in_view as _render_height_ladder_in_view,
)
from draftwright.annotations._axial_render import (
    _step_measurements as _step_measurements,
)
from draftwright.annotations._axial_render import (
    _step_value_text as _step_value_text,
)
from draftwright.annotations._axial_render import (
    _StepChainSegment as _StepChainSegment,
)
from draftwright.annotations._axial_render import (
    ladder_plan_for as ladder_plan_for,
)
from draftwright.annotations._axial_render import (
    queue_step_detail as _queue_step_detail_owner,
)
from draftwright.annotations._axial_render import (
    render_height_ladder as render_height_ladder,
)
from draftwright.annotations._axial_render import (
    render_local_turned_centerlines as render_local_turned_centerlines,
)
from draftwright.annotations._axial_render import (
    render_rotational as render_rotational,
)
from draftwright.annotations._axial_render import (
    render_step_positions as render_step_positions,
)
from draftwright.annotations._circular_recesses import (
    circular_recess_jobs,
    circular_step_candidates,
)
from draftwright.annotations._common import (
    _SIZE_SUBCHAIN,
    CROSSABLE_TYPES,
    CorridorCandidate,
    PlacementContext,
    _box_hits,
    _geom_box,
    _ray_exit_dist,
    _with_hole_center_coverage,
    analytical_leader_lands_clear,
    carve_free_position,
    dim_footprint,
    leader_callout_geometry,
    place_strip_candidates,
    register_corridor,
    strip_obstacles,
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
from draftwright.annotations._envelope import (
    _MANDATORY_OVERALL_PRIORITY as _MANDATORY_OVERALL_PRIORITY,
)
from draftwright.annotations._envelope import _env_label as _env_label
from draftwright.annotations._envelope import _EnvelopeDropRetry as _EnvelopeDropRetry
from draftwright.annotations._envelope import render_envelope as _render_envelope_owner
from draftwright.annotations._gdt import render_gdt as _render_gdt_owner
from draftwright.annotations._height_ladder import (
    _OVERALL_SUBCHAIN as _OVERALL_SUBCHAIN,
)
from draftwright.annotations._locations import (
    _circular_channel_axis_marks as _circular_channel_axis_marks,
)
from draftwright.annotations._locations import _location_candidate as _location_candidate
from draftwright.annotations._locations import (
    render_circular_channel_locations as _render_circular_channel_locations_owner,
)
from draftwright.annotations._locations import render_locations as _render_locations_owner
from draftwright.annotations._machined_leaders import (
    MachinedLeaderBindings,
)
from draftwright.annotations._machined_leaders import (
    place_machined_leader_jobs as _place_machined_leader_jobs_owner,
)
from draftwright.annotations._pmi_dimensions import (
    _MEASUREMENT_BASIS as _MEASUREMENT_BASIS,
)
from draftwright.annotations._pmi_dimensions import (
    _MIN_INPLACE_BORE_HALF_MM as _MIN_INPLACE_BORE_HALF_MM,
)
from draftwright.annotations._pmi_dimensions import (
    _UNRENDERABLE_DIMENSION_KINDS as _UNRENDERABLE_DIMENSION_KINDS,
)
from draftwright.annotations._pmi_dimensions import (
    _bore_span_offsets as _bore_span_offsets,
)
from draftwright.annotations._pmi_dimensions import (
    _leader_route_is_readable as _leader_route_is_readable,
)
from draftwright.annotations._pmi_dimensions import (
    _pmi_dim_spec as _pmi_dim_spec,
)
from draftwright.annotations._pmi_dimensions import (
    _pmi_leader_spec as _pmi_leader_spec,
)
from draftwright.annotations._pmi_dimensions import (
    _pmi_queue_options as _pmi_queue_options,
)
from draftwright.annotations._pmi_dimensions import (
    _pmi_source_ids as _pmi_source_ids,
)
from draftwright.annotations._pmi_dimensions import (
    _pmi_witness_from_bbox as _pmi_witness_from_bbox,
)
from draftwright.annotations._pmi_dimensions import (
    _record_pmi_drop as _record_pmi_drop,
)
from draftwright.annotations._pmi_dimensions import (
    _renderable_pmi_records as _renderable_pmi_records,
)
from draftwright.annotations._pmi_dimensions import (
    _sheet_leader_fallback as _sheet_leader_fallback,
)
from draftwright.annotations._pmi_dimensions import (
    _unsupported_kind_records as _unsupported_kind_records,
)
from draftwright.annotations._pmi_dimensions import (
    render_angular_dimensions as _render_angular_dimensions_owner,
)
from draftwright.annotations._pmi_dimensions import (
    render_pmi as _render_pmi_owner,
)
from draftwright.annotations._pocket_pad import _POCKET_LEAD_DIRS as _POCKET_LEAD_DIRS
from draftwright.annotations._pocket_pad import _pocket_label as _pocket_label
from draftwright.annotations._pocket_pad import pad_height_jobs as _pad_height_jobs
from draftwright.annotations._pocket_pad import pocket_jobs as _pocket_jobs
from draftwright.annotations._polygonal_prisms import (
    polygonal_prism_jobs as _polygonal_prism_jobs_owner,
)

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
from draftwright.annotations.leaders import (
    LeaderRegionPolicy,
    collect_feature_leader,
    feature_leader_candidates,
    material_penalty_units,
    place_feature_leader_jobs,
)
from draftwright.annotations.routed import RoutedLeader
from draftwright.auxiliary_layout import document_note_rows
from draftwright.compose import _attribute_annotations
from draftwright.leader_policy import effective_leader_region_policy

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
    DimensionId,
    FeatureRef,
)
from draftwright.model.ir import (
    HoleFeature,
    PatternFeature,
)
from draftwright.model.ir_foundation import Point


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


def render_circular_channel_locations(
    dwg, plan, a, *, ctx, only=None, pinned=None, axes=None
) -> int:
    """Register approved seat-axis offsets in the shared profile corridors."""
    return _render_circular_channel_locations_owner(
        dwg,
        plan,
        a,
        ctx=ctx,
        only=only,
        pinned=pinned,
        axes=axes,
        dim_builder=lambda *args, **kwargs: _dim(*args, **kwargs),
        register=register_corridor,
        axis_marks=lambda *args, **kwargs: _circular_channel_axis_marks(*args, **kwargs),
    )


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
    return _render_locations_owner(
        dwg,
        plan,
        a,
        ctx=ctx,
        only=only,
        pinned=pinned,
        dim_builder=lambda *args, **kwargs: _dim(*args, **kwargs),
        register=register_corridor,
        iso_bbox=_iso_bbox,
        seat_locations=lambda *args, **kwargs: render_circular_channel_locations(*args, **kwargs),
        location_candidate=lambda *args, **kwargs: _location_candidate(*args, **kwargs),
    )


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
    for member, provenance in zip(members, provenances, strict=False):
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
    cross_view_clearance=False,
) -> int:
    """Submit feature callouts through the shared machined-leader owner."""
    return _place_machined_leader_jobs_owner(
        dwg,
        a,
        jobs,
        noun=noun,
        drop_code=drop_code,
        ctx=ctx,
        geom_clear=geom_clear,
        joint=joint,
        expand_lanes=expand_lanes,
        region_policy=region_policy,
        source_ids_by_name=source_ids_by_name,
        source_drop_severity=source_drop_severity,
        priority=priority,
        straight_only_names=straight_only_names,
        cross_view_clearance=cross_view_clearance,
        bindings=MachinedLeaderBindings(
            wrap_callout_text=_wrap_callout_text,
            text_size=_text_size,
            leader_callout_geometry=leader_callout_geometry,
            effective_leader_region_policy=effective_leader_region_policy,
            view_label_clearance=view_label_clearance,
            feature_leader_candidates=feature_leader_candidates,
            Leader=Leader,
            RoutedLeader=RoutedLeader,
            sheet_leader_fallback=_sheet_leader_fallback,
            analytical_leader_lands_clear=analytical_leader_lands_clear,
            collect_feature_leader=collect_feature_leader,
            place_feature_leader_jobs=place_feature_leader_jobs,
            attribute_annotations=_attribute_annotations,
            boxes_overlap=_boxes_overlap,
        ),
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
    jobs = circular_recess_jobs(
        dwg,
        plan,
        only=only,
        kind=kind,
        reach=_leader_callout_reach(dwg.draft),
        tol_suffix=_tol_suffix,
        step_candidates=lambda *args, **kwargs: _circular_step_candidates(*args, **kwargs),
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
    yield from circular_step_candidates(
        dwg,
        view,
        bounds,
        feature,
        reach,
        label,
        provenance=provenance,
        radial_candidates=_radial_candidates,
        directions=_CIRCULAR_STEP_LEAD_DIRS,
        text_size=_text_size,
        font_path=DEFAULT_FONT_PATH,
        leader_geometry=leader_callout_geometry,
        box_hits=_box_hits,
        segment_clips_box=_segment_clips_box,
    )


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


def _polygonal_prism_jobs(dwg, plan, *, kind: str, name_prefix: str, only=None):
    """Keep the shared leader reach bound to the live public render pass."""
    return _polygonal_prism_jobs_owner(
        dwg,
        plan,
        kind=kind,
        name_prefix=name_prefix,
        only=only,
        leader_callout_reach=_leader_callout_reach,
    )


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


@dataclass(frozen=True, slots=True)
class _BossLengthCandidateGeometry:
    """One approved boss or stock length in its deferred profile corridor."""

    dwg: Any
    p1: Point
    p2: Point
    side: str
    edge: float
    label: str

    def build(self, pos: float) -> Dimension:
        return _dim(
            self.p1,
            self.p2,
            self.side,
            abs(pos - self.edge),
            self.dwg.draft,
            label=self.label,
        )

    def footprint(self, pos: float) -> tuple[float, float, float, float]:
        return cast(
            tuple[float, float, float, float],
            dim_footprint(
                self.p1,
                self.p2,
                self.side,
                abs(pos - self.edge),
                self.dwg.draft,
                self.label,
            ),
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

        candidate_geometry = _BossLengthCandidateGeometry(dwg, p1, p2, side, edge, label)

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
                build=candidate_geometry.build,
                order=(_SIZE_SUBCHAIN, bi, name),
                on_place=lambda _nm: None,
                # Boss heights use reconciliation-only outcomes. Stock
                # length additionally records the compiler measurement identity so its
                # completeness check can distinguish a placement drop from missing output.
                on_drop=dropped,
                force=True,
                feature=g.ref,
                measurement=pd.id,
                footprint=candidate_geometry.footprint,
            ),
        )
        n += 1
    return n


@dataclass(frozen=True, slots=True)
class _PlateDropRetry:
    """One plate's deferred opposite-strip attempt and drop evidence."""

    dwg: Any
    ctx: PlacementContext
    draft: Any
    tier: float
    value: float
    label: str
    view: str
    stack: str
    alternates: list[tuple[str, str, Strip | None, str, Point, Point, float]] | None
    feature: FeatureRef | None
    measurement: DimensionId | None
    measurement_span: tuple[Point, Point] | None

    def drop(self, name: str) -> None:
        # Wait for every corridor to settle before an alternate occupies shared space.
        # Queued retries retain registration order, including when plates contend.
        self.ctx.post_drain.append(partial(self.retry, name))

    def retry(self, name: str) -> None:
        for view2, side2, strip2, axis2, qa, qb, edge2 in self.alternates or ():
            if strip2 is None:
                continue
            foot0 = dim_footprint(qa, qb, side2, self.tier, self.draft, self.label)
            perp = (foot0[1], foot0[3]) if axis2 == "x" else (foot0[0], foot0[2])
            pos = carve_free_position(self.dwg, strip2, view2, axis2, self.tier, perp)
            if pos is not None:
                # Validate the rendered ink against live obstacles and page bounds.
                dim = _dim(qa, qb, side2, pos - edge2, self.draft, label=self.label)
                real = _geom_box(dim)
                page = _drawing_bounds(self.dwg)
                if real is None or (
                    _box_hits(
                        real, strip_obstacles(self.dwg, view=view2, crossable=CROSSABLE_TYPES)
                    )
                    or real[0] < page[0]
                    or real[1] < page[1]
                    or real[2] > page[2]
                    or real[3] > page[3]
                ):
                    continue
                self.ctx.place(
                    dim,
                    name,
                    view=view2,
                    feature=self.feature,
                    measurement=self.measurement,
                    measurement_span=self.measurement_span,
                )
                return
        self.ctx.record_issue(
            "warning",
            "plate_thickness_dropped",
            f"plate thickness {_fmt(self.value)} not dimensioned "
            f"({self.view} {self.stack}-strip full)",
            measurement=self.measurement,
            measurement_span=self.measurement_span,
        )


def render_plates(dwg, plan, a, *, ctx) -> int:
    """Register plate thickness and open-channel width candidates in that order."""
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text

    def drop_factory(*, val, lbl, view, stack, alt, feat, mid, measurement_span):
        return _PlateDropRetry(
            dwg=dwg,
            ctx=ctx,
            draft=draft,
            tier=tier,
            value=val,
            label=lbl,
            view=view,
            stack=stack,
            alternates=alt,
            feature=feat,
            measurement=mid,
            measurement_span=measurement_span,
        ).drop

    return register_plate_thickness(
        dwg, plan, a, ctx=ctx, drop_factory=drop_factory
    ) + register_channel_width(dwg, plan, a, ctx=ctx)


def render_envelope(dwg, plan, a, *, ctx) -> int:
    """Register compiler-approved overall extents in the shared corridor."""
    return _render_envelope_owner(
        dwg,
        plan,
        a,
        ctx=ctx,
        layout_frame_fn=layout_frame,
        register_corridor_fn=register_corridor,
        dim_builder=_dim,
        # The retry runs after corridor drain, so resolve this live binding then.
        place_strip_candidates_fn=lambda *args, **kwargs: place_strip_candidates(*args, **kwargs),
    )


def queue_step_detail(dwg, plan, feature, a, *, ctx, view_name, label, factor, source) -> bool:
    """Queue an authored axial detail with the current chain placer."""
    # The redraw runs later; resolve this binding then so detail recovery and callers
    # overriding ``from_model._draw_step_chain`` see the same placer.
    return _queue_step_detail_owner(
        dwg,
        plan,
        feature,
        a,
        ctx=ctx,
        view_name=view_name,
        label=label,
        factor=factor,
        source=source,
        _draw_step_chain=lambda *args, **kwargs: _draw_step_chain(*args, **kwargs),
    )


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


def render_angular_dimensions(
    dwg, plan, a, *, ctx, only=None, name=None, pin=False, priority=0.0
) -> int:
    """Queue approved included angles through the shared corridor solve."""
    return _render_angular_dimensions_owner(
        dwg,
        plan,
        a,
        ctx=ctx,
        only=only,
        name=name,
        pin=pin,
        priority=priority,
        place_candidates=place_strip_candidates,
    )


def render_pmi(dwg, model, a, *, ctx) -> int:
    """Queue imported authored dimensions through the shared corridor solve."""
    return _render_pmi_owner(
        dwg,
        model,
        a,
        ctx=ctx,
        renderable_records=_renderable_pmi_records,
        place_candidates=place_strip_candidates,
        sheet_fallback=_sheet_leader_fallback,
    )


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


def render_gdt(dwg, model, a, *, ctx) -> int:
    """Register declared GD&T items through the shared corridor stage."""
    return _render_gdt_owner(
        dwg,
        model,
        a,
        ctx=ctx,
        leader_ctor=Leader,
        carve_position=carve_free_position,
        sheet_fallback=_sheet_leader_fallback,
        source_ids_for=_pmi_source_ids,
    )
