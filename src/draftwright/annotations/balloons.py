"""balloons — the leadered hole-balloon render pass (#111/#516).

Moved down from the ``Drawing`` result object (#699): a render pass belongs in the
render layer, not hosted above it — the orchestrator used to call *up* into
``dwg.add_balloons`` by duck-typing. The band-assignment solver it drives
(:func:`layout._assign_balloon_bands`, min-cost max-flow) lives in ``layout.py``
with the other solvers (ADR 0003/0009). ``Drawing.add_balloons`` remains the
public verb: an owner method that threads the build state into this pass.
"""

from __future__ import annotations

import math

from build123d import Align, Circle, Compound, Location, Mode, Text
from build123d_drafting.helpers import Leader

from draftwright._core import (
    _STRIP_GAP,
    _STRIP_SPACING,
    _balloon_halo,
    _balloon_radius,
)
from draftwright.annotations._common import (
    balloon_annotation_label_boxes,
    balloon_geometry_hits_annotation_labels,
    carve_free_segments,
    strip_obstacles,
)
from draftwright.fonts import PLEX_MONO
from draftwright.layout import (
    _assign_balloon_bands,
    _greedy_strip_1d,
    _solve_guarded_strip_1d,
    _solve_segmented_strip_1d,
    _solve_strip_1d,
    _strip_capacity,
)

_BALLOON_RING_STROKE = 0.35


def _top_lane_target(member_count, other_capacities, usable_band_count):
    """Capacity the top lane needs for both balance and maximum cardinality."""

    balanced_share = math.ceil(member_count / usable_band_count)
    capacity_deficit = max(0, member_count - sum(other_capacities))
    return max(balanced_share, capacity_deficit)


def _band_preference_limit(font_size):
    """Maximum extra leader length justified by perimeter spreading."""

    return _balloon_halo(font_size)


def _select_top_lane(lane_options, target, fallback_line):
    """Nearest sufficient lane, else the nearest best-effort lane."""

    fitting = [option for option in lane_options if option[2] >= target]
    if fitting:
        return fitting[0]
    if lane_options:
        return lane_options[0]
    return fallback_line, [], 0


def render_balloons(
    dwg,
    a,
    view,
    specs,
    ctx,
    *,
    perimeter=False,
    avoid_annotation_labels=False,
):
    """Place a leadered balloon for each ``(tag, j, hole)`` in *specs*,
    fitted into the halo the layout reserved around the view (#111).

    Each hole is assigned to a reserved band — left, right, top or (when the
    FV↔PV gap has room) bottom of the plan view — by a global
    max-cardinality/min-cost assignment (:func:`layout._assign_balloon_bands`,
    #516), and the balloons in each band are spread along it with the 1D strip
    solver so none overlap, each pulled toward its hole's coordinate.  A
    :class:`Leader` then runs from the hole rim to the glyph.  Because the
    layout reserved this band before placing the views (:func:`_est_plan_halo`
    / :func:`_will_balloon`), the balloons sit in clear space off the part and
    no leader crosses a neighbouring view.  When *perimeter* is true, every
    usable band receives a balloon when the member count permits; this is the
    dense-hole table escalation's deliberate ring treatment (#901).  Ordinary
    and manually requested balloons retain nearest-band assignment.

    The drawing is duck-typed as *dwg* and touched only through its public
    surface; build state rides *a* and *ctx* (ADR 0005 §2 / #639, #699).
    """
    pp = dwg.coords(view).pp
    fs = dwg.draft.font_size
    r = _balloon_radius(fs)
    standoff = _STRIP_GAP
    perimeter_extent = _balloon_halo(fs) if perimeter else standoff + 2 * r
    centre_offset = perimeter_extent - r
    gap = 2 * r + 2 * _STRIP_SPACING  # min centre-to-centre: balloon + padding both sides

    # Plan-view page edges; the reserved bands sit just outside them.
    pl, pr = a.PV_X - a.fv_hw, a.PV_X + a.fv_hw
    pt, pb = a.PV_Y + a.pv_hh, a.PV_Y - a.pv_hh
    sv_left = a.SV_X - a.sv_hw
    margin, ph, pw = a.margin, a.PAGE_H, a.PAGE_W

    # Stack the balloon ring *beyond* the annotations already placed around the
    # plan view, not on top of them (#121). Measure the REAL depth every placed
    # occupant extends into each band from its full rendered footprint — leader
    # shafts, centreline geometry, tables, and bare extension lines included.
    # This intentionally shares the same full-footprint occupancy source as
    # corridor placement (#518), instead of re-growing a per-furniture allowlist.
    top_dim = bot_dim = left_dim = right_dim = 0.0
    obstacles = strip_obstacles(dwg, view=view)
    for x0, y0, x1, y1 in obstacles:
        if x1 > pl and x0 < pr:  # spans the plan's width → top/bottom bands
            if y1 > pt:
                top_dim = max(top_dim, y1 - pt)
            if y0 < pb:
                bot_dim = max(bot_dim, pb - y0)
        if y1 > pb and y0 < pt:  # spans the plan's height → left/right bands
            if x0 < pl:
                left_dim = max(left_dim, pl - x0)
            if x1 > pr:
                right_dim = max(right_dim, x1 - pr)

    # A dense part can stack many pitch dims on one side (holes._place_pitch_dim
    # pushes each successive one 10 mm further out, #92), so the measured depth
    # can exceed the room between the view and the page edge. Clamp each band so
    # its *ring itself* never lands off the drawable area (#349 follow-up) — the
    # ring then sits at the margin and overlaps the far witness lines instead,
    # which is only a tolerated warning (structural.py compares label_bbox, not
    # the full bbox, for overlap), never the out_of_bounds error.
    left_dim = min(left_dim, max(0.0, pl - perimeter_extent - margin))
    right_dim = min(right_dim, max(0.0, pw - margin - pr - perimeter_extent))
    top_dim = min(top_dim, max(0.0, ph - margin - pt - perimeter_extent))
    bot_dim = min(bot_dim, max(0.0, pb - perimeter_extent - margin))

    # A bottom band (below PV, beyond the overall-width dim) is usable only
    # when the FV↔PV gap has room for the width dim *and* a balloon row;
    # otherwise bottom-edge holes fall back to the nearest side/top band.
    bottom_line = pb - bot_dim - centre_offset
    has_bottom = pb - (a.FV_Y + a.fv_hh) > bot_dim + perimeter_extent

    # left/right balloons vary in Y at a fixed X just outside the part; top
    # and bottom balloons vary in X at a fixed Y just beyond it. Each line is
    # offset by its side's dim depth so the ring sits clear of the dims.
    band_defs = {
        "left": ("y", pl - left_dim - centre_offset, margin + r, ph - margin - r),
        "right": ("y", pr + right_dim + centre_offset, margin + r, ph - margin - r),
        "top": ("x", pt + top_dim + centre_offset, pl - standoff, sv_left - r),
        "bottom": ("x", bottom_line, pl - standoff, sv_left - r),
    }

    top_segments = None
    if perimeter:
        # A single remote label must not push the whole top row beyond the
        # deepest occupant (#125/#901).  Probe lanes at obstacle boundaries,
        # carve their horizontal free spans, and take the nearest lane that can
        # carry a balanced share of the ring.  This is measured render geometry;
        # ordered placement across the resulting segments remains in layout.py.
        top_lo, top_hi = band_defs["top"][2:]
        other_band_names = ["left", "right"]
        if has_bottom:
            other_band_names.append("bottom")
        other_capacities = [
            _strip_capacity(*band_defs[name][2:], gap) for name in other_band_names
        ]
        usable_band_count = len(other_band_names) + 1  # plus top
        top_target = _top_lane_target(len(specs), other_capacities, usable_band_count)
        first_line = pt + centre_offset
        last_line = ph - margin - r
        candidates = {first_line}
        for x0, _y0, x1, y1 in obstacles:
            if x1 > top_lo and x0 < top_hi and y1 >= pt:
                candidates.add(y1 + _STRIP_SPACING + r)

        lane_options = []
        for line in sorted(y for y in candidates if first_line <= y <= last_line):
            blocked = [
                (x0, x1)
                for x0, y0, x1, y1 in obstacles
                if y0 - _STRIP_SPACING < line + r and y1 + _STRIP_SPACING > line - r
            ]
            segments = carve_free_segments(top_lo, top_hi, blocked, r + _STRIP_SPACING)
            capacity = sum(_strip_capacity(lo, hi, gap) for lo, hi in segments)
            lane_options.append((line, segments, capacity))

        # Best effort on a genuinely constrained sheet retains the nearest lane;
        # it never recreates the remote "beyond the deepest occupant" geometry.
        top_line, top_segments, _ = _select_top_lane(lane_options, top_target, band_defs["top"][1])
        axis, _line, lo, hi = band_defs["top"]
        band_defs["top"] = (axis, top_line, lo, hi)

    # Globally assign holes across the usable reserved bands.  Nearest-band
    # greedy could crowd one side and drop balloons while another side sat
    # empty; the assignment maximises placed balloons first, then minimises
    # leader distance to the ACTUAL post-depth band lines (#516).
    members = []
    choices_by_member = []
    for tag, j, hole in specs:
        cx, cy = pp(*hole.location)
        choices = {
            "left": abs(cx - band_defs["left"][1]),
            "right": abs(band_defs["right"][1] - cx),
            "top": abs(band_defs["top"][1] - cy),
        }
        if has_bottom:
            choices["bottom"] = abs(cy - band_defs["bottom"][1])
        members.append((tag, j, hole, cx, cy))
        choices_by_member.append(choices)

    capacities = {
        name: (
            sum(_strip_capacity(seg_lo, seg_hi, gap) for seg_lo, seg_hi in top_segments)
            if name == "top" and top_segments is not None
            else _strip_capacity(lo, hi, gap)
            if name != "bottom" or has_bottom
            else 0
        )
        for name, (_axis, _line, lo, hi) in band_defs.items()
    }
    preferred_bands = tuple(name for name, capacity in capacities.items() if capacity > 0)
    if avoid_annotation_labels:
        dropped = _place_guarded_inventory(
            dwg,
            view,
            members,
            choices_by_member,
            band_defs,
            capacities,
            gap,
            fs,
            r,
            ctx,
            top_segments=top_segments,
            prefer_bands=preferred_bands if perimeter else (),
            preference_limit=_band_preference_limit(fs) if perimeter else 0.0,
        )
        if dropped:
            ctx.record_issue(
                "warning",
                "balloon_dropped",
                f"{dropped} balloon(s) could not fit their reserved band and were dropped",
            )
        return

    bands, dropped = _assign_balloon_bands(
        members,
        choices_by_member,
        capacities,
        prefer_bands=preferred_bands if perimeter else (),
        preference_limit=_band_preference_limit(fs) if perimeter else 0.0,
    )
    dropped += _place_band(
        dwg,
        view,
        bands["left"],
        *band_defs["left"],
        gap,
        fs,
        r,
        ctx,
    )
    dropped += _place_band(
        dwg,
        view,
        bands["right"],
        *band_defs["right"],
        gap,
        fs,
        r,
        ctx,
    )
    top_args = (
        dwg,
        view,
        bands["top"],
        *band_defs["top"],
        gap,
        fs,
        r,
        ctx,
    )
    dropped += (
        _place_band(*top_args)
        if top_segments is None
        else _place_band(
            *top_args,
            segments=top_segments,
        )
    )
    dropped += _place_band(
        dwg,
        view,
        bands["bottom"],
        *band_defs["bottom"],
        gap,
        fs,
        r,
        ctx,
    )
    # A band too crowded to hold every balloon drops its tail (the strip solver's
    # prefix fallback) — record it instead of letting the balloons vanish silently
    # (review follow-up). The resolver keeps the callout_dropped lint for a pattern
    # whose balloon did not land, so a missing pattern balloon is still a coverage gap.
    if dropped:
        ctx.record_issue(
            "warning",
            "balloon_dropped",
            f"{dropped} balloon(s) could not fit their reserved band and were dropped",
        )


def _balloon_shaft_segments(cx, cy, bx, by, hole_r, balloon_r):
    """Analytic 2D shaft geometry shared by guarded solve and rendering."""
    dx, dy = bx - cx, by - cy
    distance = math.hypot(dx, dy)
    if distance <= hole_r + balloon_r:
        return ()
    ux, uy = dx / distance, dy / distance
    return (
        (
            (cx + ux * hole_r, cy + uy * hole_r),
            (bx - ux * balloon_r, by - uy * balloon_r),
        ),
    )


def _balloon_glyph_box(bx, by, radius):
    return (bx - radius, by - radius, bx + radius, by + radius)


def _guarded_free_segments(member, axis, line, ranges, radius, scale, label_boxes):
    """Continuous free coordinates for one member on one balloon band.

    Segment/box intersection can change only when the centre-to-centre ray passes
    a label-box corner; glyph intersection changes at the box edges expanded by
    the ring radius.  Partitioning at those geometry events and testing the real
    shortened shaft yields exact free intervals without a sampling grid.
    """
    _tag, _member_index, hole, cx, cy = member
    hole_r = hole.diameter * scale / 2

    def hits(coordinate):
        bx, by = (line, coordinate) if axis == "y" else (coordinate, line)
        return balloon_geometry_hits_annotation_labels(
            _balloon_glyph_box(bx, by, radius),
            _balloon_shaft_segments(cx, cy, bx, by, hole_r, radius),
            label_boxes,
        )

    result = []
    for range_lo, range_hi in ranges:
        critical = {float(range_lo), float(range_hi)}
        for x0, y0, x1, y1 in label_boxes:
            rim_points: list[tuple[float, float]] = []
            for qx in (x0, x1):
                remainder = hole_r * hole_r - (qx - cx) ** 2
                if remainder >= 0:
                    delta = math.sqrt(remainder)
                    rim_points.extend(
                        (qx, qy) for qy in (cy - delta, cy + delta) if y0 <= qy <= y1
                    )
            for qy in (y0, y1):
                remainder = hole_r * hole_r - (qy - cy) ** 2
                if remainder >= 0:
                    delta = math.sqrt(remainder)
                    rim_points.extend(
                        (qx, qy) for qx in (cx - delta, cx + delta) if x0 <= qx <= x1
                    )
            if axis == "y":
                if x0 - radius < line < x1 + radius:
                    critical.update((y0 - radius, y1 + radius))
                if not math.isclose(line, cx):
                    for qx in (x0, x1):
                        if not math.isclose(qx, cx):
                            for qy in (y0, y1):
                                critical.add(cy + (qy - cy) * (line - cx) / (qx - cx))
                    for qx, qy in rim_points:
                        if not math.isclose(qx, cx):
                            critical.add(cy + (qy - cy) * (line - cx) / (qx - cx))
            else:
                if y0 - radius < line < y1 + radius:
                    critical.update((x0 - radius, x1 + radius))
                if not math.isclose(line, cy):
                    for qy in (y0, y1):
                        if not math.isclose(qy, cy):
                            for qx in (x0, x1):
                                critical.add(cx + (qx - cx) * (line - cy) / (qy - cy))
                    for qx, qy in rim_points:
                        if not math.isclose(qy, cy):
                            critical.add(cx + (qx - cx) * (line - cy) / (qy - cy))
        points = sorted({min(max(value, range_lo), range_hi) for value in critical})
        for point in points:
            if not hits(point):
                result.append((point, point))
        for lo, hi in zip(points, points[1:]):
            if hi <= lo:
                continue
            midpoint = (lo + hi) / 2
            if hits(midpoint):
                continue
            free_lo = lo if not hits(lo) else math.nextafter(lo, hi)
            free_hi = hi if not hits(hi) else math.nextafter(hi, lo)
            if free_lo <= free_hi:
                result.append((free_lo, free_hi))

    merged: list[tuple[float, float]] = []
    for lo, hi in sorted(result):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    validated = []
    for lo, hi in merged:
        clearance = max(1e-6, max(abs(lo), abs(hi)) * 1e-12)
        if hits(lo):
            lo += clearance
        if hits(hi):
            hi -= clearance
        if lo <= hi:
            validated.append((lo, hi))
    return tuple(validated)


def _solve_guarded_band(member_indices, members, band_name, band_defs, free_segments):
    """Return ``{member_index: coordinate}`` for one complete guarded band."""
    axis = band_defs[band_name][0]
    natural_index = 4 if axis == "y" else 3
    ordered = sorted(member_indices, key=lambda index: members[index][natural_index])
    coordinates = _solve_guarded_strip_1d(
        [members[index][natural_index] for index in ordered],
        free_segments.min_gap,
        [free_segments.by_member_band[index, band_name] for index in ordered],
    )
    if coordinates is None:
        return None
    return dict(zip(ordered, coordinates, strict=True))


class _GuardedSegments:
    """Small internal carrier for one guarded inventory solve."""

    def __init__(self, by_member_band, min_gap):
        self.by_member_band = by_member_band
        self.min_gap = min_gap


def _guarded_assignment(
    members,
    choices_by_member,
    band_defs,
    capacities,
    free_segments,
    *,
    prefer_bands=(),
    preference_limit=0.0,
):
    """Globally assign members to bands while all band coordinates remain feasible.

    The ordinary min-cost flow gives a cardinality upper bound using each band's
    geometric capacity.  If that assignment is jointly feasible under the
    retained-label intervals it is preserved exactly.  Otherwise a deterministic
    branch-and-bound search finds the same highest attainable cardinality across
    all bands; label feasibility therefore participates in band assignment rather
    than causing a greedy post-solve drop.
    """
    filtered_choices = [
        {
            band: distance
            for band, distance in choices.items()
            if free_segments.by_member_band.get((index, band))
        }
        for index, choices in enumerate(choices_by_member)
    ]
    effective_capacities = {}
    for band, capacity in capacities.items():
        union = sorted(
            segment
            for index in range(len(members))
            for segment in free_segments.by_member_band.get((index, band), ())
        )
        merged_union: list[tuple[float, float]] = []
        for lo, hi in union:
            if merged_union and lo <= merged_union[-1][1]:
                merged_union[-1] = (
                    merged_union[-1][0],
                    max(merged_union[-1][1], hi),
                )
            else:
                merged_union.append((lo, hi))
        effective_capacities[band] = min(
            capacity,
            sum(_strip_capacity(lo, hi, free_segments.min_gap) for lo, hi in merged_union),
        )
    seed, _flow_dropped = _assign_balloon_bands(
        members,
        filtered_choices,
        effective_capacities,
        prefer_bands=prefer_bands,
        preference_limit=preference_limit,
    )
    member_index = {id(member): index for index, member in enumerate(members)}
    seed_indices = {
        band: [member_index[id(member)] for member in assigned]
        for band, assigned in seed.items()
        if band in band_defs
    }
    seed_solutions = {
        band: _solve_guarded_band(indices, members, band, band_defs, free_segments)
        for band, indices in seed_indices.items()
    }
    target = sum(len(indices) for indices in seed_indices.values())
    if all(solution is not None for solution in seed_solutions.values()):
        return seed_indices, seed_solutions, len(members) - target

    available = [index for index, choices in enumerate(filtered_choices) if choices]
    unavailable = len(members) - len(available)
    ordered = sorted(
        available,
        key=lambda index: (
            len(filtered_choices[index]),
            min(filtered_choices[index].values()),
            index,
        ),
    )
    band_names = tuple(band_defs)
    groups: dict[str, list[int]] = {band: [] for band in band_names}
    solve_cache: dict[tuple[str, tuple[int, ...]], dict[int, float] | None] = {}

    def solve_band(band):
        key = (band, tuple(sorted(groups[band])))
        if key not in solve_cache:
            solve_cache[key] = _solve_guarded_band(
                groups[band], members, band, band_defs, free_segments
            )
        return solve_cache[key]

    for required in range(target, -1, -1):
        answer = None

        def visit(position, placed):
            nonlocal answer
            if answer is not None:
                return
            if placed + len(ordered) - position < required:
                return
            if position == len(ordered):
                if placed == required:
                    answer = (
                        {band: list(indices) for band, indices in groups.items()},
                        {band: solve_band(band) for band in band_names},
                    )
                return
            index = ordered[position]
            choices = sorted(
                filtered_choices[index],
                key=lambda band: (
                    filtered_choices[index][band]
                    - (preference_limit if band in prefer_bands and not groups[band] else 0.0),
                    filtered_choices[index][band],
                    band,
                ),
            )
            for band in choices:
                if len(groups[band]) >= effective_capacities[band]:
                    continue
                groups[band].append(index)
                if solve_band(band) is not None:
                    visit(position + 1, placed + 1)
                groups[band].pop()
                if answer is not None:
                    return
            if placed + len(ordered) - position - 1 >= required:
                visit(position + 1, placed)

        visit(0, 0)
        if answer is not None:
            assignments, solutions = answer
            return assignments, solutions, unavailable + len(available) - required
    return {band: [] for band in band_names}, {band: {} for band in band_names}, len(members)


def _place_guarded_inventory(
    dwg,
    view,
    members,
    choices_by_member,
    band_defs,
    capacities,
    gap,
    fs,
    radius,
    ctx,
    *,
    top_segments,
    prefer_bands,
    preference_limit,
):
    """Solve and render the final collision-safe automatic balloon inventory."""
    label_boxes = balloon_annotation_label_boxes(dwg, view)
    by_member_band = {}
    for index, member in enumerate(members):
        for band, (axis, line, lo, hi) in band_defs.items():
            if capacities[band] <= 0:
                continue
            ranges = top_segments if band == "top" and top_segments is not None else ((lo, hi),)
            by_member_band[index, band] = _guarded_free_segments(
                member,
                axis,
                line,
                ranges,
                radius,
                dwg.scale,
                label_boxes,
            )
    guarded = _GuardedSegments(by_member_band, gap)
    assignments, solutions, dropped = _guarded_assignment(
        members,
        choices_by_member,
        band_defs,
        capacities,
        guarded,
        prefer_bands=prefer_bands,
        preference_limit=preference_limit,
    )
    for band, indices in assignments.items():
        axis, line, _lo, _hi = band_defs[band]
        solution = solutions[band]
        for index in sorted(indices, key=lambda item: solution[item]):
            tag, member_index, hole, cx, cy = members[index]
            coordinate = solution[index]
            bx, by = (line, coordinate) if axis == "y" else (coordinate, line)
            _render_balloon(
                dwg,
                view,
                tag,
                member_index,
                hole,
                cx,
                cy,
                bx,
                by,
                fs,
                radius,
                ctx,
            )
    return dropped


def _place_band(
    dwg,
    view,
    members,
    axis,
    line,
    lo,
    hi,
    gap,
    fs,
    r,
    ctx,
    *,
    segments=None,
    avoid_annotation_labels=False,
) -> int:
    """Spread *members* (``(tag, j, hole, cx, cy)``) along one reserved band
    with the strip solver, then render a leadered balloon for each (#111).

    *axis* is the band's free axis (``"y"`` for the left/right bands, ``"x"``
    for the top); *line* is the fixed coordinate of the other axis.  Overflow
    beyond ``[lo, hi]`` drops the tail rather than running balloons off-page.
    Returns the number of members dropped, so the caller can surface it as lint
    (a silently truncated balloon leaves a hole undocumented — the resolver
    must know, review follow-up).
    """
    if not members:
        return 0
    k = 4 if axis == "y" else 3  # index of cy / cx in the member tuple
    members.sort(key=lambda m: m[k])
    naturals = [m[k] for m in members]
    if avoid_annotation_labels:
        ranges = segments if segments is not None else ((lo, hi),)
        label_boxes = balloon_annotation_label_boxes(dwg, view)
        allowed = [
            _guarded_free_segments(
                member,
                axis,
                line,
                ranges,
                r,
                dwg.scale,
                label_boxes,
            )
            for member in members
        ]
        coords = _solve_guarded_strip_1d(naturals, gap, allowed)
        if coords is None:
            return len(members)
        for (tag, j, hole, cx, cy), coordinate in zip(members, coords, strict=True):
            bx, by = (line, coordinate) if axis == "y" else (coordinate, line)
            _render_balloon(dwg, view, tag, j, hole, cx, cy, bx, by, fs, r, ctx)
        return 0
    if segments is not None:
        coords = _solve_segmented_strip_1d(naturals, gap, segments, prefix=True) or []
    else:
        coords = (
            _solve_strip_1d(naturals, gap, lo, hi)
            or _greedy_strip_1d(naturals, gap, lo, hi)
            or _greedy_strip_1d(naturals, gap, lo, hi, prefix=True)
        )
    for (tag, j, hole, cx, cy), c in zip(members, coords):
        bx, by = (line, c) if axis == "y" else (c, line)
        _render_balloon(
            dwg,
            view,
            tag,
            j,
            hole,
            cx,
            cy,
            bx,
            by,
            fs,
            r,
            ctx,
        )
    return len(members) - len(coords)


def _render_balloon(
    dwg,
    view,
    tag,
    j,
    hole,
    cx,
    cy,
    bx,
    by,
    fs,
    r,
    ctx,
):
    """Build and add one balloon glyph + leader at solved centre ``(bx, by)``
    for hole ``(cx, cy)`` (#111)."""
    loc = Location((bx, by, 0))
    # The annotation layer fills closed paths, so a circle edge renders as a
    # disc. A thin annular FACE fills as a ring — i.e. a circle outline.
    ring_faces = [f.moved(loc) for f in (Circle(r) - Circle(r - _BALLOON_RING_STROKE)).faces()]
    text = Text(
        txt=tag,
        font_size=fs,
        font_path=PLEX_MONO,
        align=(Align.CENTER, Align.CENTER),
        mode=Mode.PRIVATE,
    ).locate(loc)
    glyph_parts = [*ring_faces, *text.faces()]
    parts = list(glyph_parts)
    shaft_segments = _balloon_shaft_segments(
        cx,
        cy,
        bx,
        by,
        hole.diameter * dwg.scale / 2,
        r,
    )
    # Leader from the hole rim to the balloon's near edge — the glyph is the
    # label, so label="".  Skipped when the balloon could not clear the hole
    # (degenerate fallback), where a leader would be a stub through the ring.
    dx, dy = bx - cx, by - cy
    dist = math.hypot(dx, dy)
    hole_r = hole.diameter * dwg.scale / 2
    if dist > hole_r + r:
        ux, uy = dx / dist, dy / dist
        tip = (cx + ux * hole_r, cy + uy * hole_r, 0)
        elbow = (bx - ux * r, by - uy * r, 0)
        leader = Leader(tip, elbow, "", dwg.draft)
        parts.append(leader)
    balloon = Compound(children=parts)
    # Structural lint treats the shaft and compact glyph as two precise pieces:
    # the shaft avoids the compound AABB's empty diagonal triangle (ADR 0009),
    # while the glyph remains visible to critique instead of disappearing behind
    # the centreline exemption.
    balloon.centerline_segments = shaft_segments
    balloon.centerline_boxes = (_balloon_glyph_box(bx, by, r),)
    # Furniture that legitimately sits on the view geometry — exempt from the
    # annotation-overlap / centreline lint, as the section arrows do.
    balloon.is_centerline = True
    ctx.place(
        balloon,
        f"balloon_{view}_{tag}_{j}",
        view=view,
        feature=ctx.feature_of_hole_at(hole.location),
    )
    return True
