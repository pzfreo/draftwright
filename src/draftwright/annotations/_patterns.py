"""Grouped pattern callouts and compiler-approved pitch furniture.

The hole pass supplies its live pitch-placement and hole-furniture bindings so the
existing strip exemption, tracing, and annotation transactions retain one owner.
"""

from __future__ import annotations

import math
from functools import partial

from draftwright._core import _END_ON, Analysis, layout_frame
from draftwright._geometry import plane_axes
from draftwright.annotations.from_model import (
    _leader_callout_reach,
    _obround_radius_candidates,
    _pocket_label,
    _radial_candidates,
    _slot_label,
    _tol_suffix,
    place_machined_leader_jobs,
)
from draftwright.annotations.leaders import LeaderRegionPolicy
from draftwright.model.compiled import resolve_feature
from draftwright.model.ir import PatternFeature


def _place_pitch_after_pmi(place_pitch_dim, *args, ctx, **kwargs):
    """Offer generated pitch only after source PMI has used the shared strips."""
    if ctx.defer_pattern_pitch:
        ctx.deferred_pattern_pitch_names.add(args[8])
        ctx.deferred_pattern_pitches.append(partial(place_pitch_dim, *args, ctx=ctx, **kwargs))
        return
    return place_pitch_dim(*args, ctx=ctx, **kwargs)


def _furnish_uncalled_patterns(
    dwg, a: Analysis, view_of_axis, plan, *, ctx, furnished, add_furniture
):
    """Pattern furniture for patterns that got no callout — the pitch/grid dims only.

    Furniture has always been a side effect of placing a bore callout, which was fine while
    the two stood or fell together. An authored set separates them: `dimension(pattern,
    "pitch")` names the pitch and nothing else, so there is no callout to hang the furniture
    off and the measurement the script explicitly asked for was silently not drawn (#925) —
    the blank-drawing failure `_check_authored_targets` exists to prevent, arriving
    by a different route.

    Additive by construction: it runs only for patterns `_add_furniture` did not already
    handle, so every drawing with a placed callout is untouched. Bolt-circle centrelines are
    NOT swept — a centreline with no callout is furniture for a feature the drawing does not
    otherwise mention, and unlike a pitch dim nobody asked for it.
    """
    for group in plan.of_kind("pattern"):
        feat = resolve_feature(group.ref)
        if id(feat) in furnished or feat.pattern == "bolt_circle":
            continue
        axis = feat.frame.axis
        if axis not in view_of_axis:
            continue
        view = group.view
        j = 0
        while any(
            nm == f"dim_pitch_{view}{j}" or nm.startswith(f"dim_pitch_{view}{j}_")
            for nm in (*ctx.registry.names(), *ctx.deferred_pattern_pitch_names)
        ):
            j += 1
        add_furniture(
            dwg,
            a,
            view,
            j,
            feat,
            partial(layout_frame(a).project, view),
            ctx=ctx,
            plan=plan,
            furnished=furnished,
            cover=False,
        )


def _coalesce_aligned_linear_pitch_dims(dwg, a: Analysis, *, ctx) -> None:
    """Show one pitch for parallel hole rows with identical projected stations.

    Separate recognised patterns can own the same ordinate chain (CTC04 has two
    pairs of three-hole rows).  Both approved measurements still belong to the
    surviving mark; only redundant ink is removed.  Do this after placement so
    a row that could not place a dimension cannot erase one that did.
    """
    groups: dict[tuple, list[tuple[str, object, float]]] = {}
    for name, annotation in tuple(dwg.iter_annotations()):
        if not name.startswith("dim_pitch_"):
            continue
        feature = ctx.registry.feature_of(name)
        if not isinstance(feature, PatternFeature) or feature.pattern != "linear":
            continue
        members = feature.members
        if len(members) < 2:
            continue
        view = ctx.registry.view_of(name)
        if view is None or annotation.label_bbox is None:
            continue
        projected = [layout_frame(a).project(view, member) for member in members]
        p1, p2 = projected[0], projected[-1]
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        if abs(dx) < 1e-6 and abs(dy) > 1e-6:
            if any(abs(point[0] - p1[0]) > 1e-3 for point in projected):
                continue
            axis = "vertical"
            stations = tuple(sorted(round(point[1], 3) for point in projected))
            transverse = p1[0]
            label_transverse = (annotation.label_bbox[0] + annotation.label_bbox[2]) / 2
        elif abs(dy) < 1e-6 and abs(dx) > 1e-6:
            if any(abs(point[1] - p1[1]) > 1e-3 for point in projected):
                continue
            axis = "horizontal"
            stations = tuple(sorted(round(point[0], 3) for point in projected))
            transverse = p1[1]
            label_transverse = (annotation.label_bbox[1] + annotation.label_bbox[3]) / 2
        else:
            continue  # rotated rows need their own explicit witness geometry
        key = (view, axis, stations, annotation.label)
        groups.setdefault(key, []).append((name, annotation, abs(label_transverse - transverse)))

    for rows in groups.values():
        if len(rows) < 2:
            continue
        # Keep the mark with the shortest witness reach; the other may have
        # fallen back to the opposite side only because this pitch occupied its strip.
        chosen_name, chosen, _ = min(rows, key=lambda row: row[2])
        identity = ctx.registry.identity_of(chosen_name)
        measurements = list(ctx.registry.measurement_of(chosen_name))
        owners = list(getattr(chosen, "source_features", ()))
        for name, _annotation, _ in rows:
            if name == chosen_name:
                continue
            measurements.extend(ctx.registry.measurement_of(name))
            owner = ctx.registry.feature_of(name)
            if owner is not None:
                owners.append(owner)
            dwg.remove(name)
        identity["measurement"] = tuple(measurements)
        ctx.registry.reapply(chosen_name, identity)
        # Feature annotations acquire this provenance after they are built.
        setattr(chosen, "source_features", tuple(owners))  # noqa: B010


def _add_grid_pitch_dims(
    dwg,
    a: Analysis,
    view,
    j,
    members,
    nominals,  # the approved grid_pitch entries, one per lattice axis
    to_page,
    *,
    feature,
    ctx,
    name_prefix="dim_pitch",
    drop_code=None,
    place_pitch_dim,
):
    """Both pitch dimensions of a rectangular grid from its recognised lattice frame."""
    pts = [to_page(m) for m in members]
    actual_feature = resolve_feature(feature)
    by_discriminator = {
        entry.discriminator: entry
        for entry in nominals
        if getattr(entry, "discriminator", None) in {"row", "col"}
    }
    pu, pv = plane_axes(actual_feature.frame.axis)
    angle = math.radians(actual_feature.angle or 0.0)
    column_world = tuple(math.cos(angle) * pu[k] + math.sin(angle) * pv[k] for k in range(3))
    row_world = tuple(-math.sin(angle) * pu[k] + math.cos(angle) * pv[k] for k in range(3))
    origin = actual_feature.frame.origin
    page_origin = to_page(origin)

    def _page_axis(world, discriminator):
        pitch = by_discriminator.get(discriminator)
        if pitch is None:
            return None
        endpoint = to_page(tuple(origin[k] + world[k] for k in range(3)))
        dx, dy = endpoint[0] - page_origin[0], endpoint[1] - page_origin[1]
        unit_scale = math.hypot(dx, dy)
        if unit_scale <= 1e-9:
            return None
        return ((dx / unit_scale, dy / unit_scale), abs(pitch.value) * unit_scale, pitch)

    axes = [
        axis
        for axis in (
            _page_axis(column_world, "col"),
            _page_axis(row_world, "row"),
        )
        if axis is not None
    ]
    if len(axes) != 2:
        return
    axes.sort(key=lambda axis: (round(axis[1], 9), axis[2].discriminator or ""))
    min_pitch_page = min(axis[1] for axis in axes)

    def _axis_dim(u, pitch_page, pitch, sub):
        perp = (-u[1], u[0])

        def along(idx):
            return pts[idx][0] * u[0] + pts[idx][1] * u[1]

        def across(idx):
            return pts[idx][0] * perp[0] + pts[idx][1] * perp[1]

        # Keep the dimension on ONE lattice line. In plan/front, choose the OUTER line on
        # the same page side `_place_pitch_dim` prefers. The former `min(..., key=along)`
        # leaves the perpendicular tie to sub-ulp coordinate noise; 0.4.6 consequently chose
        # a central short-axis row that occupied the only clear corridor for the long pitch.
        # Side-view placement uses the same nearest-part-side policy as `_place_pitch_dim`.
        if view == "side":
            corner_across = [
                a.proj.side_x(y) * perp[0] + a.proj.side_z(z) * perp[1]
                for y in (a.bb.min.Y, a.bb.max.Y)
                for z in (a.bb.min.Z, a.bb.max.Z)
            ]
            point_across = [across(idx) for idx in range(len(pts))]
            positive_reach = max(corner_across) - max(point_across)
            negative_reach = min(point_across) - min(corner_across)
            extremum = max if positive_reach <= negative_reach else min
            anchor = extremum(
                range(len(pts)),
                key=lambda idx: (across(idx), along(idx)),
            )
        else:
            preferred = (-0.3, 1.0) if view == "plan" else (-0.3, -1.0)
            toward_positive = perp[0] * preferred[0] + perp[1] * preferred[1] >= 0.0
            extremum = max if toward_positive else min
            anchor = extremum(
                range(len(pts)),
                key=lambda idx: (across(idx), along(idx)),
            )
        # Of the holes sharing the selected perpendicular coordinate, take both extremes
        # along u. Picking the global max-projection hole instead lands on the opposite
        # diagonal corner and draws the pitch dim diagonally across the grid.
        # Tolerance must be below the PERPENDICULAR lattice-line spacing — which
        # is the *other* axis' pitch, so use the smaller of the two pitches.
        # (pitch_page * 0.25 fails on a high-aspect grid: for the long axis the
        # perpendicular lines are only the short pitch apart, and a quarter of
        # the long pitch can exceed that, merging two lines → diagonal again.)
        anchor_across = across(anchor)
        line_tol = min_pitch_page * 0.25
        line = [idx for idx in range(len(pts)) if abs(across(idx) - anchor_across) < line_tol]
        lo = min(line, key=along)
        hi = max(line, key=along)
        # The holes this dim actually spans, in the order it spans them. `members[lo:hi+1]`
        # is a slice of the IR's member order, which on a grid walks the lattice in neither
        # direction: on a 3x2 grid it hands `_pitch_text` five points whose consecutive gaps
        # are a mix of row and column spacing, so every uniform grid read as jittered and had
        # its authored tolerance withheld.
        spanned = [members[idx] for idx in sorted(line, key=along)]
        span = along(hi) - along(lo)
        n = round(span / pitch_page) + 1
        place_pitch_dim(
            dwg,
            a,
            view,
            members[lo],
            members[hi],
            n,
            _pitch_text(pitch, spanned, dwg.draft, ctx=ctx),
            to_page,
            f"{name_prefix}_{view}{j}_{sub}",
            feature=feature,
            measurement=pitch.id,
            drop_code=drop_code,
            ctx=ctx,
        )

    for sub, (unit, pitch_page, pitch) in enumerate(axes):
        _axis_dim(unit, pitch_page, pitch, sub)


def _pitch_text(pitch, members, draft, *, ctx) -> str:
    """The collapsed pitch label, with its authored tolerance only when that is TRUE.

    An `N× v` label states one value for every gap in the array. A ± on it therefore claims
    that tolerance of each gap — so it is only honest when the gaps agree at the displayed
    precision. The recogniser admits jitter (`_PATTERN_REL_TOL = 0.02`, `_PATTERN_ABS_TOL =
    0.1 mm`), so "identical by construction" is false: holes at x = −30, −10, 10.3, 30 collapse
    to one pitch dim, and composing the suffix there printed `3× 20 ±0.1` over a gap of 20.3 —
    0.3 mm out against an authored ±0.05, a claim six times tighter than the part (#1216).

    This is the rule the engine already applies to the step chain — "a per-step ± would be a
    false claim on N equal steps, so the collapse carries NO tolerance" — and the one the detail
    redraw uses to decide whether to regroup at all: equal at `_fmt` precision, not within a
    percentage.

    Withholding is not silent: a pattern whose spacing does not survive its own displayed
    precision records `pattern_pitch_tolerance_withheld`, so an author who tolerances a jittered
    pitch is told the drawing cannot state it rather than being shown a number that is wrong.
    """
    text = str(pitch.value_text)
    if pitch.tolerance is None:
        return text
    # Compared at the drawn precision, never FORMATTED here: the printed value is the compiler's
    # `value_text`; this function must not mint a second printed value.
    places = draft.decimal_precision
    nominal = round(pitch.value, places)
    gaps = [math.dist(tuple(a), tuple(b)) for a, b in zip(members, members[1:], strict=False)]
    varying = [g for g in gaps if round(g, places) != nominal]
    if gaps and not varying:
        return text + _tol_suffix(pitch.tolerance, draft)
    ctx.record_issue(
        "info",
        "pattern_pitch_tolerance_withheld",
        f"pattern pitch {text}: {len(varying)} of {len(gaps)} gaps differ from it at the "
        "drawn precision, so the authored tolerance is not stated — an N× label would "
        "claim it of every gap",
        measurement=pitch.id,
    )
    return text


def render_pocket_patterns(dwg, plan, a, *, ctx, only=None, place_pitch_dim) -> int:
    """Grouped blind-pocket-array callouts (#841): ONE ``count× W × L × D DEEP`` leader on the
    array centre + the ``(n-1)× pitch`` dim(s), instead of N competing per-pocket size dims.

    A `PocketPatternFeature` composes its member pockets (they are NOT in ``model.features``),
    so `render_pockets` never double-renders them. This owner shares the approved
    pitch preparation with hole and slot arrays, and receives the hole pass's
    pitch placement binding.

    Planner-fed (#728): the width/length/depth VALUES + tolerances are bound explicitly by
    ``(role, kind)`` (``pocket_width``/``pocket_length``/``pocket_depth``, all ``length``),
    never positionally. The callout reads in the view normal to the DEPTH axis (z→plan,
    x→side, y→front). ``only`` restricts placement for `finalize()` (#426), skipping in place
    so ``i`` stays the model index."""
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    view_of = _END_ON
    pat_groups = list(plan.of_kind("pocket_pattern"))
    jobs = []
    furniture = []  # (i, feat, view) for the placed patterns' pitch dims
    for i, g in enumerate(
        sorted(pat_groups, key=lambda g: (g.facts.member_width_axis, g.facts.frame.origin))
    ):
        feat = g.facts
        if only is not None and g.ref not in only:
            continue  # filtered subset: skip in place so i stays the model index
        by_key = {(pd.role, pd.kind): pd for pd in g.dims}
        wpd = by_key.get(("pocket_width", "length"))
        lpd = by_key.get(("pocket_length", "length"))
        dpd = by_key.get(("pocket_depth", "length")) or by_key.get(("pocket_max_depth", "length"))
        dimensions = tuple(d for d in (wpd, lpd, dpd) if d is not None)
        if not dimensions:
            continue
        view = view_of.get(feat.member_depth_axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        label = f"{feat.count}× " + _pocket_label(
            wpd.value_text if wpd is not None else None,
            lpd.value_text if lpd is not None else None,
            dpd.value_text if dpd is not None else None,
            wsfx=_tol_suffix(wpd.tolerance, draft) if wpd is not None else "",
            lsfx=_tol_suffix(lpd.tolerance, draft) if lpd is not None else "",
            maximum_depth=dpd is not None and dpd.role == "pocket_max_depth",
            dsfx=_tol_suffix(dpd.tolerance, draft) if dpd is not None else "",
        )
        # Anchor the one representative leader at the array CENTRE (feat.frame.origin) and
        # attribute it to the pattern feature (ADR 5 (was 0010) provenance).
        name = f"m_pocketpat_{feat.member_width_axis}{feat.member_long_axis}{i}"
        jobs.append(
            (
                name,
                view,
                vb,
                label,
                _radial_candidates(dwg, view, vb, feat, reach, provenance=g.ref),
                tuple(d.id for d in dimensions),
            )
        )
        furniture.append((i, g, view, name))
    placed = place_machined_leader_jobs(
        dwg, a, jobs, noun="pocket pattern", drop_code="pocket_dropped", ctx=ctx
    )
    placed_names = dwg.annotations()
    for i, g, view, name in furniture:
        # Skip the pitch furniture whose grouped size/depth callout dropped for want of room:
        # orphan pitch dims with no `N× W×L×D` leader are an incomplete, misleading spec.
        # Members are computed by _pattern_members (declare rejects explicit
        # members=), so for a linear array they are already ordered along the direction —
        # members[0]/[-1] are the true extrema and the (n-1)× pitch label is truthful. Distinct
        # name prefix (dim_pocketpat_pitch, not the hole pattern's dim_pitch) so a plan-view
        # hole pattern and pocket pattern do not collide on dim_pitch_plan0.
        if name not in placed_names:
            continue
        feat = g.facts
        # The visible ``N×`` is a physical grouping requirement, not decoration.  Retain
        # its structured value on the exact feature-owned callout so completeness and other
        # consumers can verify the ink without parsing a label (ADR 5 (was 0010) / ADR 4 (was 0016)).
        dwg.registry.named(name).covers_count = feat.count
        members = feat.members or (feat.frame.origin,)
        pitch = g.dim(role="pitch")
        grid = tuple(d for d in g.dims if d.role == "grid_pitch")

        def to_page(loc, _view=view):
            return dwg.at(_view, *loc)

        if feat.pattern == "linear" and pitch is not None:
            place_pitch_dim(
                dwg,
                a,
                view,
                members[0],
                members[-1],
                len(members),
                _pitch_text(pitch, members, dwg.draft, ctx=ctx),
                to_page,
                f"dim_pocketpat_pitch_{view}{i}",
                feature=g.ref,
                measurement=pitch.id,
                drop_code="pocket_pattern_dim_dropped",
                ctx=ctx,
            )
        elif feat.pattern == "grid" and len(grid) == 2:
            _add_grid_pitch_dims(
                dwg,
                a,
                view,
                i,
                members,
                grid,
                to_page,
                feature=g.ref,
                drop_code="pocket_pattern_dim_dropped",
                ctx=ctx,
                name_prefix="dim_pocketpat_pitch",
                place_pitch_dim=place_pitch_dim,
            )
    return placed


def render_slot_patterns(dwg, plan, a, *, ctx, only=None, place_pitch_dim) -> int:
    """Grouped milled-slot-array callouts (#841): ONE ``count× SLOT W × L`` leader on the array
    centre + an optional ``2× R`` obround-end leader + the ``(n-1)× pitch`` dim(s), instead of N competing per-slot size dims (some of
    which drop for lack of room, #841 behaviour 1). The through-slot analog of
    :func:`render_pocket_patterns` — a slot has no depth, so the label carries no ``× D DEEP``.

    A `SlotPatternFeature` composes its member slots (they are NOT in ``model.features``), so
    `render_slots` never double-renders them. The width/length VALUES + tolerances are bound
    explicitly by ``(role, kind)`` (``slot_width``/``slot_length``, both ``length``), never
    positionally. The callout reads in the view normal to the slot's THROUGH axis. ``only``
    restricts placement for `finalize()` (#426), skipping in place so ``i`` stays the model
    index."""
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    view_of = _END_ON
    pat_groups = list(plan.of_kind("slot_pattern"))
    jobs = []
    radius_jobs = []
    furniture = []  # (i, feat, view, name) for the placed patterns' pitch dims
    for i, g in enumerate(
        sorted(pat_groups, key=lambda g: (g.facts.member_width_axis, g.facts.frame.origin))
    ):
        feat = g.facts
        if only is not None and g.ref not in only:
            continue  # filtered subset: skip in place so i stays the model index
        through_axis = next(
            axis for axis in "xyz" if axis not in (feat.member_width_axis, feat.member_long_axis)
        )
        by_key = {(pd.role, pd.kind): pd for pd in g.dims}
        wpd = by_key.get(("slot_width", "length"))
        lpd = by_key.get(("slot_length", "length"))
        rpd = by_key.get(("slot_end_radius", "radius"))
        if wpd is None or lpd is None:
            continue
        view = view_of.get(through_axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        label = f"{feat.count}× " + _slot_label(
            wpd.value_text,
            lpd.value_text,
            wsfx=_tol_suffix(wpd.tolerance, draft),
            lsfx=_tol_suffix(lpd.tolerance, draft),
        )
        # Anchor the one representative leader at the array CENTRE (feat.frame.origin) and
        # attribute it to the pattern feature (ADR 5 (was 0010) provenance).
        name = f"m_slotpat_{feat.member_width_axis}{feat.member_long_axis}{i}"
        jobs.append(
            (
                name,
                view,
                vb,
                label,
                _radial_candidates(dwg, view, vb, feat, reach, provenance=g.ref),
                (wpd.id, lpd.id),  # width × length, one callout
            )
        )
        if rpd is not None:
            radius_jobs.append(
                (
                    f"m_slotpat_radius_{feat.member_width_axis}{feat.member_long_axis}{i}",
                    view,
                    vb,
                    f"2× R{rpd.value_text}{_tol_suffix(rpd.tolerance, draft)}",
                    _obround_radius_candidates(
                        dwg,
                        view,
                        vb,
                        centre=feat.members[0] if feat.members else feat.frame.origin,
                        long_axis=feat.member_long_axis,
                        length=lpd.value,
                        radius=rpd.value,
                        reach=reach,
                        provenance=g.ref,
                    ),
                    (rpd.id,),
                )
            )
        furniture.append((i, g, view, name))
    placed = place_machined_leader_jobs(
        dwg, a, jobs, noun="slot pattern", drop_code="slot_dropped", ctx=ctx
    )
    placed += place_machined_leader_jobs(
        dwg,
        a,
        radius_jobs,
        noun="slot-pattern end radius",
        drop_code="slot_dim_dropped",
        ctx=ctx,
        joint=True,
        expand_lanes=False,
        region_policy=LeaderRegionPolicy.AUTO,
    )
    placed_names = dwg.annotations()
    for i, g, view, name in furniture:
        # Skip the pitch furniture whose grouped size callout dropped (orphan pitch dims are a
        # misleading spec). Distinct name prefix (dim_slotpat_pitch) so a plan-view
        # slot pattern and hole/pocket pattern do not collide.
        if name not in placed_names:
            for dimension in (
                (g.dim(role="pitch"),)
                if g.facts.pattern == "linear"
                else tuple(d for d in g.dims if d.role == "grid_pitch")
            ):
                if dimension is not None:
                    ctx.record_issue(
                        "warning",
                        "slot_dim_dropped",
                        "slot pattern pitch not placed because its grouped callout dropped",
                        measurement=dimension.id,
                    )
            continue
        feat = g.facts
        members = feat.members or (feat.frame.origin,)
        pitch = g.dim(role="pitch")
        grid = tuple(d for d in g.dims if d.role == "grid_pitch")

        def to_page(loc, _view=view):
            return dwg.at(_view, *loc)

        if feat.pattern == "linear" and pitch is not None:
            place_pitch_dim(
                dwg,
                a,
                view,
                members[0],
                members[-1],
                len(members),
                _pitch_text(pitch, members, dwg.draft, ctx=ctx),
                to_page,
                f"dim_slotpat_pitch_{view}{i}",
                feature=g.ref,
                measurement=pitch.id,
                drop_code="slot_dim_dropped",
                ctx=ctx,
            )
        elif feat.pattern == "grid" and len(grid) == 2:
            _add_grid_pitch_dims(
                dwg,
                a,
                view,
                i,
                members,
                grid,
                to_page,
                feature=g.ref,
                ctx=ctx,
                name_prefix="dim_slotpat_pitch",
                drop_code="slot_dim_dropped",
                place_pitch_dim=place_pitch_dim,
            )
    return placed
