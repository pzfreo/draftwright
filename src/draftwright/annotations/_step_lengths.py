"""Turned axial step-length rendering from compiler-approved step groups.

The public pass lives in ``from_model``. Its injected chain placer preserves the
shared immediate/deferred seam and the named test override used by detail recovery.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, Literal, cast

from draftwright._core import (
    _MIN_STEP_SEP_MM,
    DetailRequest,
    _fmt,
    _log,
    _text_size,
    _tol_suffix,
    crowded_horizontal_step_runs,
    supported_secondary_crop,
    y_chain_detail_scale_needed,
)
from draftwright.annotations.from_model import (
    _next_steplen_start,
    _record_step_chain_drop,
    _step_measurements,
    _step_value_text,
    _StepChainSegment,
)
from draftwright.model.ir import StepFeature


def render_step_lengths(
    dwg,
    plan,
    *,
    ctx,
    only=None,
    _profile_bounds_hint=None,
    _profile_view_hint=None,
    _draw_step_chain: Callable[..., int],
) -> int:
    """Render compiler-approved lengths for each physical turned profile."""
    grouped = _render_profile_groups(
        dwg,
        plan,
        ctx=ctx,
        only=only,
        _profile_bounds_hint=_profile_bounds_hint,
        _profile_view_hint=_profile_view_hint,
        _draw_step_chain=_draw_step_chain,
    )
    if grouped is not None:
        return grouped
    return _render_single_profile(
        dwg,
        plan,
        ctx=ctx,
        only=only,
        _profile_bounds_hint=_profile_bounds_hint,
        _profile_view_hint=_profile_view_hint,
        _draw_step_chain=_draw_step_chain,
    )


def _render_profile_groups(
    dwg,
    plan,
    *,
    ctx,
    only=None,
    _profile_bounds_hint=None,
    _profile_view_hint=None,
    _draw_step_chain: Callable[..., int],
) -> int | None:
    """Unified turned step-length chains (ADR 1 (was 0008) #223): each `StepFeature`'s length
    span projects into the profile view and joins the chain that tiles the turning
    axis so every shoulder is located. X-turned → horizontal chain above the front
    view; Z-turned → vertical chain to its right; Y-turned → horizontal chain above
    the side view.

    A crowded **X-turned head** — a contiguous run of steps too short to dimension
    legibly even staggered (shoulders below the page arrowhead floor) — is not crammed
    in line: the main view locates that run as one *block* dim and an enlarged
    `DetailRequest` (#304/#307) is queued to break it down. If the detail later can't
    place, the block still locates the head extent and lint reports the un-located
    interior shoulders — never worse than the prior skip. Parallel or axially disconnected
    profiles are grouped before collapse and rendered against their own silhouettes. Returns the
    count placed, or None for one profile."""
    # One axial chain belongs to one physical axis line. Group BEFORE the repeat-run collapse:
    # equal lengths on parallel shafts are separate requirements, not one global ``N×`` run.
    # View assignment always sees the complete roster, even when ``only`` narrows a deferred
    # edit. Otherwise re-adding one removed profile forgets a surviving sibling's lane and can
    # place both chains on top of each other. Recursive per-profile placement carries an explicit
    # view hint and narrows only the refs that actually receive ink.
    # Emitted declarations round coordinates to 0.001 mm, so exact neighbours may return
    # with a sub-micron numerical seam. This is declaration precision, not a physical gap.
    adjacency_tol = 1e-3 + 1e-9
    line_groups: dict[
        tuple[str, tuple[float, float], object | None], list[tuple[float, float, object]]
    ] = {}
    for group in plan.of_kind("step"):
        if group.facts.frame.axis not in ("x", "y", "z"):
            continue
        length = group.dim(kind="length")
        if length is None or length.span is None:
            continue
        axis_index = "xyz".index(group.facts.frame.axis)
        line_values = [
            round(float(value), 6)
            for index, value in enumerate(group.facts.frame.origin)
            if index != axis_index
        ]
        line = (line_values[0], line_values[1])
        axis_index = "xyz".index(group.facts.frame.axis)
        lo, hi = sorted(float(point[axis_index]) for point in length.span)
        membership = group.facts.profile or group.facts.profile_group
        line_groups.setdefault((group.facts.frame.axis, line, membership), []).append(
            (lo, hi, group.ref)
        )
    profile_groups: list[tuple[tuple[str, tuple[float, float], object | None], set[object]]] = []
    for key, members in sorted(
        line_groups.items(), key=lambda item: (item[0][0], item[0][1], repr(item[0][2]))
    ):
        if key[2] is not None:
            profile_groups.append((key, {ref for _lo, _hi, ref in members}))
            continue
        axis, line, _membership = key
        axis_index = "xyz".index(axis)
        groove_intervals = []
        for groove in plan.of_kind("groove"):
            if groove.facts.frame.axis != axis:
                continue
            groove_line = tuple(
                round(float(value), 6)
                for index, value in enumerate(groove.facts.frame.origin)
                if index != axis_index
            )
            width = groove.dim(kind="length")
            if groove_line != line or width is None:
                continue
            centre = float(groove.facts.frame.origin[axis_index])
            groove_intervals.append(
                (centre - float(width.value) / 2.0, centre + float(width.value) / 2.0)
            )
        profile_runs: list[tuple[float, set[object]]] = []
        for lo, hi, ref in sorted(members, key=lambda member: member[:2]):
            run_hi = profile_runs[-1][0] if profile_runs else float("-inf")
            gap_is_groove = any(
                groove_lo <= run_hi + adjacency_tol and groove_hi >= lo - adjacency_tol
                for groove_lo, groove_hi in groove_intervals
            )
            if not profile_runs or (lo > run_hi + adjacency_tol and not gap_is_groove):
                profile_runs.append((hi, {ref}))
            else:
                run_hi, refs = profile_runs[-1]
                refs.add(ref)
                profile_runs[-1] = (max(run_hi, hi), refs)
        profile_groups.extend((key, refs) for _hi, refs in profile_runs)
    if len(profile_groups) > 1 and _profile_view_hint is None:
        profile_views = {
            "x": ("front", "plan"),
            "y": ("side", "plan"),
            "z": ("front", "side"),
        }

        def _point_for(profile_key):
            profile_axis, profile_line, _profile_membership = profile_key
            point = [0.0, 0.0, 0.0]
            line_index = 0
            for index in range(3):
                if index != "xyz".index(profile_axis):
                    point[index] = profile_line[line_index]
                    line_index += 1
            return point

        def _projected_cross(profile_key, view) -> float:
            axis, _line, _membership = profile_key
            point = _point_for(profile_key)
            px, py, *_ = dwg.at(view, *point)
            axial_point = list(point)
            axial_point["xyz".index(axis)] += 1.0
            ax, ay, *_ = dwg.at(view, *axial_point)
            axial_is_horizontal = abs(float(ax) - float(px)) >= abs(float(ay) - float(py))
            return float(py if axial_is_horizontal else px)

        refs_by_key = dict(profile_groups)
        candidate_views = {
            key: tuple(view for view in profile_views[key[0]] if dwg.view_bounds(view) is not None)
            for key, _refs in profile_groups
        }

        def _projected_axial_interval(key, view) -> tuple[float, float]:
            axis = key[0]
            axis_index = "xyz".index(axis)
            stations = [
                float(point[axis_index])
                for group in plan.of_kind("step")
                if group.ref in refs_by_key[key]
                for length in (group.dim(kind="length"),)
                if length is not None and length.span is not None
                for point in length.span
            ]
            point = _point_for(key)
            projected = []
            for station in (min(stations), max(stations)):
                point[axis_index] = station
                px, py, *_ = dwg.at(view, *point)
                axial_point = list(point)
                axial_point[axis_index] += 1.0
                ax, ay, *_ = dwg.at(view, *axial_point)
                projected.append(
                    float(px if abs(float(ax) - float(px)) >= abs(float(ay) - float(py)) else py)
                )
            return (min(projected), max(projected))

        def _conflict(left, right, view) -> bool:
            if left[0] != right[0]:
                # Perpendicular turning axes do not share a longitudinal lane. Their real
                # page-ink interaction is resolved by the common placement/overlap stages.
                return False
            if round(_projected_cross(left, view), 6) != round(_projected_cross(right, view), 6):
                return False
            left_lo, left_hi = _projected_axial_interval(left, view)
            right_lo, right_hi = _projected_axial_interval(right, view)
            # A shared endpoint still shares witnesses/labels and therefore needs the other
            # longitudinal view.  Truly separated silhouettes may safely reuse this lane.
            return left_lo <= right_hi + 1e-6 and right_lo <= left_hi + 1e-6

        profile_conflicts = {
            (left, right, view): _conflict(left, right, view)
            for left_index, (left, _left_refs) in enumerate(profile_groups)
            for right, _right_refs in profile_groups[left_index + 1 :]
            for view in set(candidate_views[left]) & set(candidate_views[right])
        }

        def _satisfiable(keys, forced=None) -> bool:
            return _profile_assignment_satisfiable(
                candidate_views, profile_conflicts, keys, forced
            )

        accepted: list[Any] = []
        unassigned = []
        for key, _refs in profile_groups:
            if not candidate_views[key] or not _satisfiable([*accepted, key]):
                unassigned.append(key)
            else:
                accepted.append(key)

        # Prefer each axis's conventional view unless that would make the complete accepted
        # roster unsatisfiable.  The forced-prefix solve makes the choice deterministic.
        forced: dict[Any, int] = {}
        for key in accepted:
            trial = {**forced, key: 0}
            if _satisfiable(accepted, trial):
                forced = trial
            else:
                forced[key] = 1
        assignments = {key: candidate_views[key][choice] for key, choice in forced.items()}

        def _cell_bounds(key, view):
            axis, _line, _membership = key
            view_bounds = dwg.view_bounds(view)
            if view_bounds is None:
                return None

            def _cross(profile_key) -> float:
                return _projected_cross(profile_key, view)

            cross = _cross(key)
            siblings = sorted(
                {
                    _cross(profile_key)
                    for profile_key, assigned_view in assignments.items()
                    if profile_key[0] == axis and assigned_view == view
                }
            )
            position = siblings.index(cross)
            point = _point_for(key)
            px, py, *_ = dwg.at(view, *point)
            axial_point = list(point)
            axial_point["xyz".index(axis)] += 1.0
            ax, ay, *_ = dwg.at(view, *axial_point)
            axial_is_horizontal = abs(float(ax) - float(px)) >= abs(float(ay) - float(py))
            cross_lo_index, cross_hi_index = (1, 3) if axial_is_horizontal else (0, 2)
            cross_lo = (
                view_bounds[cross_lo_index]
                if position == 0
                else (siblings[position - 1] + cross) / 2.0
            )
            cross_hi = (
                view_bounds[cross_hi_index]
                if position == len(siblings) - 1
                else (cross + siblings[position + 1]) / 2.0
            )
            if not axial_is_horizontal:
                return (cross_lo, view_bounds[1], cross_hi, view_bounds[3])
            return (view_bounds[0], cross_lo, view_bounds[2], cross_hi)

        requested = None if only is None else set(only)
        placed = 0
        for key, refs in profile_groups:
            refs_to_place = refs if requested is None else refs & requested
            if key not in assignments or not refs_to_place:
                continue
            placed += render_step_lengths(
                dwg,
                plan,
                ctx=ctx,
                only=refs_to_place,
                _profile_bounds_hint=_cell_bounds(key, assignments[key]),
                _profile_view_hint=assignments[key],
                _draw_step_chain=_draw_step_chain,
            )
        for key in unassigned:
            refs = refs_by_key[key]
            if requested is not None:
                refs &= requested
            if not refs:
                continue
            measurements = tuple(
                length.id
                for group in plan.of_kind("step")
                if group.ref in refs
                for length in (group.dim(kind="length"),)
                if length is not None and length.id is not None
            )
            _record_step_chain_drop(
                dwg,
                "no longitudinal view uniquely identifies this physical profile",
                ctx=ctx,
                measurement=measurements,
            )
        return placed

    return None


def _profile_assignment_satisfiable(candidate_views, profile_conflicts, keys, forced=None) -> bool:
    """Solve the two-view profile assignment as 2-SAT.

    A profile chooses its conventional or alternate longitudinal view.  Two profiles
    cannot make a particular joint choice only when both their projected cross line
    *and* axial interval overlap.  This retains fail-closed ambiguity handling while
    allowing any number of axially disjoint coaxial bodies to reuse a lane (#1357).
    """
    forced = forced or {}
    index = {key: i for i, key in enumerate(keys)}
    graph: list[list[int]] = [[] for _ in range(2 * len(keys))]
    reverse: list[list[int]] = [[] for _ in graph]

    def imply(source, target):
        graph[source].append(target)
        reverse[target].append(source)

    for key, i in index.items():
        choices = candidate_views[key]
        if len(choices) == 1:
            imply(2 * i + 1, 2 * i)
        if key in forced:
            choice = forced[key]
            imply(2 * i + (1 - choice), 2 * i + choice)
    for left_i, left in enumerate(keys):
        for right_i in range(left_i + 1, len(keys)):
            right = keys[right_i]
            for left_choice, left_view in enumerate(candidate_views[left]):
                for right_choice, right_view in enumerate(candidate_views[right]):
                    if left_view != right_view or not profile_conflicts.get(
                        (left, right, left_view), False
                    ):
                        continue
                    # not(left=choice and right=choice): each selected literal implies
                    # the negation of the other selected literal.
                    imply(2 * left_i + left_choice, 2 * right_i + (1 - right_choice))
                    imply(2 * right_i + right_choice, 2 * left_i + (1 - left_choice))

    seen = set()
    order = []

    def visit(node):
        if node in seen:
            return
        seen.add(node)
        for target in graph[node]:
            visit(target)
        order.append(node)

    for node in range(len(graph)):
        visit(node)
    components = [-1] * len(graph)

    def assign(node, component):
        if components[node] != -1:
            return
        components[node] = component
        for target in reverse[node]:
            assign(target, component)

    component = 0
    for node in reversed(order):
        if components[node] == -1:
            assign(node, component)
            component += 1
    return all(components[2 * i] != components[2 * i + 1] for i in range(len(keys)))


def _render_single_profile(
    dwg,
    plan,
    *,
    ctx,
    only,
    _profile_bounds_hint,
    _profile_view_hint,
    _draw_step_chain: Callable[..., int],
) -> int:
    """Place one axial profile and request details for crowded shoulders."""
    rows: list[tuple[str, _StepChainSegment]] = []
    step_origins = []
    step_geometry = []
    for g in plan.of_kind("step"):
        if g.facts.frame.axis not in ("x", "y", "z"):
            continue
        if only is not None and g.ref not in only:  # recorded finalize subset
            continue
        length = g.dim(kind="length")
        if length is None or length.span is None:
            continue
        rows.append(
            (
                g.facts.frame.axis,
                _StepChainSegment(
                    length.span[0],
                    length.span[1],
                    length.value,
                    length.tolerance,
                    (length.id,) if length.id is not None else (),
                    value_text=length.value_text,
                    display_decimals=length.display_decimals,
                ),
            )
        )
        step_origins.append(g.facts.frame.origin)
        diameter = g.dim(kind="diameter")
        step_geometry.append(
            (
                g.facts.frame,
                length.span,
                None if diameter is None else float(diameter.value) / 2.0,
            )
        )
    if not rows:
        return 0
    # The automatic pass starts at zero; finalize starts past existing
    # m_steplen names to avoid replacing a dimension.
    start = _next_steplen_start(ctx) if only is not None else 0
    axes = {axis for axis, _seg in rows}
    if len(axes) != 1:
        _log.warning(
            "step-length chain has mixed axes; rendering each axis requires separate groups"
        )
        return 0
    turn_axis = next(iter(axes))
    view = cast(
        Literal["front", "plan", "side"],
        _profile_view_hint or ("side" if turn_axis == "y" else "front"),
    )
    bare_rows = [seg for _axis, seg in rows]
    fsegs = [replace(seg, pa=dwg.at(view, *seg.pa), pb=dwg.at(view, *seg.pb)) for seg in bare_rows]
    horizontal = abs(fsegs[0].pb[0] - fsegs[0].pa[0]) >= abs(fsegs[0].pb[1] - fsegs[0].pa[1])

    # The principal view can contain several disjoint turned bodies. Anchor this chain at its
    # own silhouette, not the compound's outer view edge, otherwise every vertical profile
    # would draw the same dimension line and body ownership would be visually ambiguous.
    radial_axis = {
        ("front", "x"): "z",
        ("front", "z"): "x",
        ("plan", "x"): "y",
        ("plan", "y"): "x",
        ("side", "y"): "z",
        ("side", "z"): "y",
    }.get((view, turn_axis), "x" if turn_axis == "z" else "z")
    radial_index = "xyz".index(radial_axis)
    profile_points: list[tuple[float, ...]] = []
    if _profile_bounds_hint is not None:
        for frame, span, radius in step_geometry:
            if radius is None:
                profile_points = []
                break
            for endpoint in span:
                for sign in (-1.0, 1.0):
                    point = list(endpoint)
                    point[radial_index] = float(frame.origin[radial_index]) + sign * radius
                    profile_points.append(dwg.at(view, *point))
    profile_bounds = (
        (
            min(point[0] for point in profile_points),
            min(point[1] for point in profile_points),
            max(point[0] for point in profile_points),
            max(point[1] for point in profile_points),
        )
        if profile_points
        else _profile_bounds_hint
    )

    y_result = _render_y_profile(
        dwg,
        view,
        bare_rows,
        fsegs,
        step_origins,
        horizontal,
        turn_axis,
        profile_bounds,
        start,
        ctx=ctx,
        _draw_step_chain=_draw_step_chain,
    )
    if y_result is not None:
        return y_result
    x_result = _render_x_crowded_head(
        dwg,
        view,
        bare_rows,
        fsegs,
        step_origins,
        step_geometry,
        horizontal,
        turn_axis,
        profile_bounds,
        start,
        ctx=ctx,
        _draw_step_chain=_draw_step_chain,
    )
    if x_result is not None:
        return x_result

    return _draw_step_chain(
        dwg,
        view,
        fsegs,
        "m_steplen",
        ctx=ctx,
        start=start,
        profile_bounds=profile_bounds,
        placement_bounds=_profile_bounds_hint,
    )


def _render_y_profile(
    dwg,
    view,
    bare_rows,
    fsegs,
    step_origins,
    horizontal,
    turn_axis,
    profile_bounds,
    start,
    *,
    ctx,
    _draw_step_chain: Callable[..., int],
) -> int | None:
    """Collapse repeated Y lengths or request an enlarged side-profile detail."""
    draft = dwg.draft
    # A Y-turned chain that would need near/far staggering is ambiguous in the
    # narrow side view: an interior far-tier segment reads like an overall
    # dimension. Keep one overall block on the side view and redraw the
    # complete shoulder chain in a true enlarged side-profile detail instead.
    if horizontal and turn_axis == "y" and len(fsegs) >= 2:
        label_widths = [
            _text_size(
                _step_value_text(seg) + _tol_suffix(seg.tolerance, draft),
                draft.font_size,
                font=getattr(draft, "font", "Arial"),
            )[0]
            for seg in bare_rows
        ]
        scale_needed = y_chain_detail_scale_needed(
            tuple((seg.pa[0], seg.pb[0], row.value) for seg, row in zip(fsegs, bare_rows)),
            tuple(label_widths),
            arrow_length=draft.arrow_length,
            text_padding=draft.pad_around_text,
        )
        # A long repeated-pitch tail can be stated once on the main view. This
        # removes several competing short labels and may make the remaining
        # isolated links readable without an enlarged detail.
        ordered = sorted(fsegs, key=lambda seg: (seg.pa[0] + seg.pb[0]) / 2)
        compact: list[_StepChainSegment] = []
        collapsed = False
        j = 0
        while j < len(ordered):
            repeat_run = [ordered[j]]
            k = j + 1
            while k < len(ordered):
                prev, cur = repeat_run[-1], ordered[k]
                contiguous = abs(max(prev.pa[0], prev.pb[0]) - min(cur.pa[0], cur.pb[0])) <= 1e-4
                if not (
                    contiguous
                    and (
                        _step_value_text(cur) == _step_value_text(repeat_run[0])
                        if cur.display_decimals is not None
                        or repeat_run[0].display_decimals is not None
                        else _fmt(cur.value) == _fmt(repeat_run[0].value)
                    )
                    and prev.tolerance is None
                    and cur.tolerance is None
                ):
                    break
                repeat_run.append(cur)
                k += 1
            if len(repeat_run) >= 3:
                xs = [p[0] for seg in repeat_run for p in (seg.pa, seg.pb)]
                y = repeat_run[0].pa[1]
                compact.append(
                    _StepChainSegment(
                        (min(xs), y, 0.0),
                        (max(xs), y, 0.0),
                        sum(seg.value for seg in repeat_run),
                        measurements=_step_measurements(repeat_run),
                        label=(
                            f"{len(repeat_run)}× "
                            + (
                                _step_value_text(repeat_run[0])
                                if any(seg.display_decimals is not None for seg in repeat_run)
                                else _fmt(sum(seg.value for seg in repeat_run) / len(repeat_run))
                            )
                        ),
                    )
                )
                collapsed = True
            else:
                compact.extend(repeat_run)
            j = k
        if collapsed:
            return _draw_step_chain(
                dwg,
                view,
                compact,
                "m_steplen",
                allow_collapse=False,
                ctx=ctx,
                start=start,
                profile_bounds=profile_bounds,
            )
        if scale_needed is not None:
            axis_lo = min(min(seg.pa[1], seg.pb[1]) for seg in bare_rows)
            axis_hi = max(max(seg.pa[1], seg.pb[1]) for seg in bare_rows)
            page_xs = [p[0] for seg in fsegs for p in (seg.pa, seg.pb)]
            page_y = fsegs[0].pa[1]
            block = [
                _StepChainSegment(
                    (min(page_xs), page_y, 0.0),
                    (max(page_xs), page_y, 0.0),
                    axis_hi - axis_lo,
                )
            ]
            # Use the detected turning axis, not the sheet/bounding-box centroid:
            # an eccentric shaft's profile may be nowhere near the latter. A single
            # side-profile detail is valid only for a coaxial chain.
            axis_xs = [origin[0] for origin in step_origins]
            axis_zs = [origin[2] for origin in step_origins]
            coaxial = max(axis_xs) - min(axis_xs) <= 0.5 and max(axis_zs) - min(axis_zs) <= 0.5
            if not coaxial:
                return _draw_step_chain(
                    dwg,
                    view,
                    fsegs,
                    "m_steplen",
                    ctx=ctx,
                    start=start,
                    profile_bounds=profile_bounds,
                )
            axis_z = sum(axis_zs) / len(axis_zs)

            # Choose the same standard scale family as the detail renderer, then
            # crop a geometry-relative strip: at most one quarter of the side-view
            # silhouette and at most 12 page-mm tall. The view rectangle is
            # layout geometry and carries no withheld printable diameter.
            detail_target = dwg.scale * 10
            for factor in (2, 5, 10):
                candidate = dwg.scale * factor
                if candidate >= scale_needed:
                    detail_target = candidate
                    break
            _sx0, sy0, _sx1, sy1 = dwg.view_bounds("side")
            silhouette_quarter = abs(sy1 - sy0) / (4 * dwg.scale)
            cross_half = max(0.1, min(silhouette_quarter, 6.0 / detail_target))

            def _redraw_y(dwg, detail_view, coords, detail_scale, _rows=bare_rows):
                def _at(x, y, z):
                    px, py = coords.pp(x, y, z)
                    return (px, py, 0.0)

                dpairs = [
                    (replace(seg, pa=_at(*seg.pa), pb=_at(*seg.pb)), label_widths[i])
                    for i, seg in enumerate(_rows)
                ]
                dpairs.sort(key=lambda item: (item[0].pa[0] + item[0].pb[0]) / 2)
                # The principal-view pass immediately above already collapses every
                # contiguous repeated run of three or more and returns before queuing this
                # callback.  Detail projection is affine, so it cannot turn a non-contiguous
                # run into a contiguous one.  Repeating that collapse here was unreachable
                # and risked letting the two copies drift.
                dsegs = [seg for seg, _width in dpairs]
                detail_widths = [width for _seg, width in dpairs]
                # Never recreate the ambiguous stagger inside a detail. Validate
                # both text clearance and full inside-arrow capacity at the actual
                # fitted scale; returning zero transactionally drops the detail.
                dcw = sorted(
                    ((seg.pa[0] + seg.pb[0]) / 2, detail_widths[i]) for i, seg in enumerate(dsegs)
                )
                if not all(
                    c2 - c1 >= (w1 + w2) / 2 + draft.pad_around_text
                    for (c1, w1), (c2, w2) in zip(dcw, dcw[1:])
                ):
                    _log.info(
                        "Y-chain detail rejected at scale %.3g: labels still collide",
                        detail_scale,
                    )
                    return 0
                if any(
                    abs(seg.pb[0] - seg.pa[0])
                    < detail_widths[i] + 2 * draft.arrow_length + 2 * draft.pad_around_text
                    for i, seg in enumerate(dsegs)
                ):
                    _log.info(
                        "Y-chain detail rejected at scale %.3g: label + inside arrows "
                        "do not fit a segment",
                        detail_scale,
                    )
                    return 0
                return _draw_step_chain(
                    dwg,
                    detail_view,
                    dsegs,
                    f"dim_{detail_view}_steplen",
                    detail_scale,
                    ctx=ctx,
                )

            ctx.detail_requests.append(
                DetailRequest(
                    axis="y",
                    lo=axis_lo,
                    hi=axis_hi,
                    scale_needed=scale_needed,
                    redraw=_redraw_y,
                    pad_top=draft.font_size + 2 * draft.pad_around_text + draft.arrow_length,
                    source_view="side",
                    cross_axis="z",
                    cross_lo=axis_z - cross_half,
                    cross_hi=axis_z + cross_half,
                    kind="y-turned-chain",
                    measurement_ids=_step_measurements(bare_rows),
                    measurement_spans=tuple(
                        (segment.pa, segment.pb)
                        for segment in bare_rows
                        for _measurement in segment.measurements
                    ),
                )
            )
            return _draw_step_chain(
                dwg,
                "side",
                block,
                "m_steplen",
                allow_collapse=False,
                ctx=ctx,
                start=start,
                profile_bounds=profile_bounds,
            )

    return None


def _render_x_crowded_head(
    dwg,
    view,
    bare_rows,
    fsegs,
    step_origins,
    step_geometry,
    horizontal,
    turn_axis,
    profile_bounds,
    start,
    *,
    ctx,
    _draw_step_chain: Callable[..., int],
) -> int | None:
    """Locate crowded X head runs as blocks and queue their enlarged details."""
    draft = dwg.draft
    # X-turned crowded-head detour: split off each contiguous *run of ≥2*
    # sub-floor steps (segment narrower than two arrowheads on the page), locate it as
    # a block, and queue an enlarged detail. A single isolated thin step is left in the
    # main chain — a one-step block would still have that sub-floor width.
    # The legible steps and blocks stay as the main chain.
    if horizontal and turn_axis == "x":
        heads = crowded_horizontal_step_runs(
            tuple((seg.pa[0], seg.pb[0]) for seg in fsegs), 1.0, draft.arrow_length
        )
        if heads:
            blocks = []
            for run in heads:
                ra = [bare_rows[i] for i in run]
                hlo = min(min(seg.pa[0], seg.pb[0]) for seg in ra)
                hhi = max(max(seg.pa[0], seg.pb[0]) for seg in ra)
                minlen = min(seg.value for seg in ra)
                # World→page scale for the detail (no sheet factor — detail_scale is an
                # absolute world→page scale).
                scale_needed = _MIN_STEP_SEP_MM / minlen if minlen > 0 else float("inf")
                # A head *block* is a synthetic span, not one toleranced step — carry no ± (None).
                block_lo = list(step_origins[0])
                block_hi = list(step_origins[0])
                block_lo[0], block_hi[0] = hlo, hhi
                blocks.append(
                    _StepChainSegment(
                        dwg.at(view, *block_lo),
                        dwg.at(view, *block_hi),
                        hhi - hlo,
                    )
                )

                def _redraw(dwg, view, coords, detail_scale, _hw=ra):
                    # View-scoped name prefix so two detail views never collide.
                    # Map world→page against the detail coords (not a live dwg.at) so the view can be
                    # committed only after these dims land — no place-then-roll-back.
                    def _at(x, y, z):
                        px, py = coords.pp(x, y, z)
                        return (px, py, 0.0)

                    hsegs = [replace(seg, pa=_at(*seg.pa), pb=_at(*seg.pb)) for seg in _hw]
                    return _draw_step_chain(
                        dwg, view, hsegs, f"dim_{view}_steplen", detail_scale, ctx=ctx
                    )

                cross_axis: Literal["x", "y", "z"] = "z" if view == "front" else "y"
                cross_index = "xyz".index(cross_axis)
                # Require the same complete controlled step geometry on detected and
                # declared builds. Provider-only bounds disappear from generated Sheet
                # scripts; source-owned profile support survives both paths.
                radial_extents = [
                    (frame.origin[cross_index] - radius, frame.origin[cross_index] + radius)
                    for frame, _span, radius in step_geometry
                    if radius is not None
                ]
                profile_steps: list[StepFeature] = []
                if len(radial_extents) == len(step_geometry):
                    possible_steps = [
                        next(
                            (
                                measurement.feature
                                for measurement in segment.measurements
                                if isinstance(measurement.feature, StepFeature)
                            ),
                            None,
                        )
                        for segment in ra
                    ]
                    if possible_steps and all(step is not None for step in possible_steps):
                        profile_steps = [step for step in possible_steps if step is not None]
                profile_support_points = []
                if profile_steps:
                    for segment, step in zip(ra, profile_steps, strict=True):
                        rim = step.frame.origin[cross_index] + step.diameter / 2
                        for endpoint in (segment.pa, segment.pb):
                            support = list(endpoint)
                            support[cross_index] = rim
                            profile_support_points.append(
                                (float(support[0]), float(support[1]), float(support[2]))
                            )
                cross_bounds = supported_secondary_crop(
                    tuple(profile_support_points),
                    cross_axis,
                    min(lo for lo, _hi in radial_extents) if radial_extents else 0.0,
                    max(hi for _lo, hi in radial_extents) if radial_extents else 0.0,
                    scale_needed,
                )
                ctx.detail_requests.append(
                    DetailRequest(
                        axis="x",
                        lo=hlo,
                        hi=hhi,
                        scale_needed=scale_needed,
                        redraw=_redraw,
                        pad_top=2 * (draft.font_size + 2 * draft.pad_around_text)
                        + draft.arrow_length,
                        source_view=view,
                        cross_axis=cross_axis if cross_bounds is not None else None,
                        cross_lo=None if cross_bounds is None else cross_bounds[0],
                        cross_hi=None if cross_bounds is None else cross_bounds[1],
                        kind="turned-head",
                        # The main view carries only a synthetic head block; these
                        # exact step lengths belong to the detail. Keep their
                        # compiler identities available if that detail cannot fit.
                        measurement_ids=_step_measurements(ra),
                        measurement_spans=tuple(
                            (segment.pa, segment.pb)
                            for segment in ra
                            for _measurement in segment.measurements
                        ),
                        profile_support_points=tuple(profile_support_points),
                    )
                )
            head = {i for run in heads for i in run}
            main = [fsegs[i] for i in range(len(fsegs)) if i not in head] + blocks
            main.sort(key=lambda seg: seg.pa[0])
            # The chain now mixes head-block(s) with real steps — never collapse it to a
            # uniform "N× v" representative (a block is not a repeated step).
            return _draw_step_chain(
                dwg,
                view,
                main,
                "m_steplen",
                allow_collapse=False,
                ctx=ctx,
                start=start,
                profile_bounds=profile_bounds,
            )

    return None
