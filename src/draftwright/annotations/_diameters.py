"""Compiler-approved step and boss diameter callouts and physical routing.

The public pass in ``from_model`` supplies the established radial and shared-leader
entry points. This owner chooses approved content, strip rows, and reroute candidates.
"""

from __future__ import annotations

import math
from itertools import groupby
from typing import Any, cast

from build123d_drafting.helpers import DEFAULT_FONT_PATH, Leader

from draftwright._core import (
    Analysis,
    _drawing_bounds,
    _greedy_strip_ys,
    _solve_strip_ys,
    _text_size,
    _tol_suffix,
)
from draftwright.annotations._common import (
    CROSSABLE_TYPES,
    _anno_box,
    _box_hits,
    strip_obstacles,
)
from draftwright.annotations._pocket_pad import _POCKET_LEAD_DIRS
from draftwright.annotations.leaders import view_material
from draftwright.model.ir import KnurlRequirement, ThreadRequirement
from draftwright.model.manufacturing_schedule import manufacturing_callout_suffix

_END_DIAMETER_LEAD_DIRS = (
    *_POCKET_LEAD_DIRS,
    *((math.cos(math.pi * i / 16), math.sin(math.pi * i / 16)) for i in range(32) if i % 4),
)


def _place_what_fits(
    specs,
    axis: int,
    min_gap: float,
    lo: float,
    hi: float,
    *,
    solve_strip_ys=_solve_strip_ys,
    greedy_strip_ys=_greedy_strip_ys,
):
    """Fit as many ø specs as the strip ``[lo, hi]`` holds at ``min_gap`` spacing,
    dropping the SMALLEST-diameter spec first when the full set overflows — so the
    significant ODs survive and only the finest bands fall to ``feature_not_dimensioned``,
    never the whole row/column (#298). ``specs`` = ``[(tip, dia, label, feat, mids), ...]``;
    ``axis`` selects the strip coordinate of ``tip`` (0 = page-x for the row-below, 1 =
    page-y for the column-left). Returns ``(survivors_in_strip_order, positions)`` —
    ``([], [])`` if not even one fits. A part whose full row already fits keeps every
    spec in strip order, so existing output is unchanged."""
    survivors = sorted(specs, key=lambda s: s[0][axis])
    while survivors:
        naturals = [s[0][axis] for s in survivors]
        pos = solve_strip_ys(naturals, min_gap, lo, hi) or greedy_strip_ys(
            naturals, min_gap, lo, hi
        )
        if pos is not None:
            return survivors, pos
        drop = min(range(len(survivors)), key=lambda i: survivors[i][1])
        survivors.pop(drop)
    return [], []


def _diameter_row_below(
    dwg, items, start: int = 0, trace=None, *, ctx, place_what_fits=_place_what_fits
) -> int:
    """ø-callout row BELOW the front view for X-turned step/boss diameters (#77).
    *items* is ``[(anchor, dia, value_text, feature, tolerance, thread, mids), ...]``. The row is dropped clear of anything
    already below the profile; labels spread along page-x by the ADR 2 (was 0003) strip
    solve. Skips (returns 0) if there is no room; the caller may then try radial
    leaders, and any still-missing diameter reaches coverage lint. *trace* (#736): one
    ``pass_events`` record with a placed/dropped item per callout."""
    if not items:
        return 0
    ev = trace.pass_event("diameter_row_below", view="front") if trace is not None else None
    draft = dwg.draft
    bounds = dwg.view_bounds("front")
    if bounds is None:
        # The row is anchored under the front elevation, and this sheet does not carry one.
        # `view_bounds` has always documented `None` for a view it does not have; with a
        # fixed four-view topology that could not happen, so the caller unpacked it directly
        # and a view set without the front crashed here. Skipping is the established
        # behaviour of this function when there is no room. Later radial fallback or
        # coverage lint resolves the outcome, letting the requirement gate WEIGH a view
        # set instead of the build dying inside a pass.
        return 0
    fx0, fy0, fx1, _ = bounds
    obstacle_bottom = fy0
    for o in dwg.items:
        try:
            ob = o.bounding_box()
        except Exception as exc:  # noqa: BLE001 — an unreadable obstacle cannot be ignored
            for _, _, text, _, _, _, mids in items:
                ctx.record_issue(
                    "warning",
                    "diameter_row_obstacle_unverified",
                    f"ø{text} shared row withheld: below-front obstacle could not be measured "
                    f"({type(exc).__name__})",
                    measurement=mids,
                    evidence_reason="obstacle_unverified",
                )
                if ev is not None:
                    ev["items"].append(
                        {
                            "label": f"ø{text}",
                            "outcome": "dropped",
                            "reason": "obstacle_unverified",
                        }
                    )
            return 0
        if ob.min.Y < fy0 and ob.max.X > fx0 and ob.min.X < fx1:
            obstacle_bottom = min(obstacle_bottom, ob.min.Y)
    label_y = obstacle_bottom - (draft.font_size + 4 * draft.pad_around_text)
    if label_y < _drawing_bounds(dwg)[1] + draft.font_size:
        if ev is not None:
            ev["items"].extend(
                {"label": f"ø{text}", "outcome": "dropped", "reason": "no_room_below"}
                for _, _d, text, _, _, _, _ in items
            )
        return 0
    specs = []  # (tip_page, dia, label, feature, mids), tip on the step's bottom silhouette,
    for anchor, dia, value_text, feat, dtol, thr, mids in items:
        ax, ay, az = anchor
        tip = dwg.at("front", ax, ay, az - dia / 2)
        label = f"ø{value_text}{_tol_suffix(dtol, draft)}" + (f" {thr}" if thr else "")
        specs.append((tip, dia, label, feat, mids))
    # Real measured width, not the per-char estimate: helpers >=0.14 label boxes are
    # honest about the rendered string, so an underestimated min_gap here surfaces as a
    # visible annotation_overlap between adjacent labels (hypothesis tier).
    half_w = (
        max(
            _text_size(
                label,
                draft.font_size,
                getattr(draft, "font_path", DEFAULT_FONT_PATH),
                getattr(draft, "font", "Arial"),
            )[0]
            for _, _, label, _, _ in specs
        )
        / 2
    )
    min_gap = 2 * half_w + 2 * draft.pad_around_text
    # Place what fits; drop the smallest ø first, never the whole row.
    survivors, xs = place_what_fits(specs, 0, min_gap, fx0 + half_w, fx1 - half_w)
    # A leader whose solved elbow lands LEFT of its tip flips its shelf (helpers'
    # direction rule), extending the label LEFTWARD — the min_gap model assumes
    # rightward labels, so a crowd-shifted elbow can land its flipped label on the
    # previous one. A flip alone is fine (a lone edge leader flips harmlessly); only
    # when direction-aware label intervals actually collide, enforce elbow ≥ tip with
    # a left-to-right min_gap cascade; overflow drops the smallest ø and
    # re-solves. No-op for ordinary rows.
    _SHELF = draft.pad_around_text  # helpers Leader shelf_len = gap = draft.pad_around_text

    def _label_ivals(svs, positions):
        out = []
        for sp, lx in zip(svs, positions, strict=True):
            w_i = _text_size(
                sp[2],
                draft.font_size,
                getattr(draft, "font_path", DEFAULT_FONT_PATH),
                getattr(draft, "font", "Arial"),
            )[0]
            if lx < sp[0][0]:  # flipped: label extends left of the elbow
                out.append((lx - _SHELF - w_i, lx - _SHELF))
            else:
                out.append((lx + _SHELF, lx + _SHELF + w_i))
        return out

    def _collides(ivals):
        pairs = zip(sorted(ivals), sorted(ivals)[1:], strict=False)
        return any(a1 > b0 for (_a0, a1), (b0, _b1) in pairs)

    while len(survivors) > 1 and _collides(_label_ivals(survivors, xs)):
        adj: list[float] = []
        for sp, lx in zip(survivors, xs, strict=True):
            v = max(lx, sp[0][0])
            if adj:
                v = max(v, adj[-1] + min_gap)
            adj.append(v)
        if adj[-1] <= fx1 - half_w:
            xs = adj
            break
        drop = min(range(len(survivors)), key=lambda i: survivors[i][1])
        survivors.pop(drop)
        survivors, xs = place_what_fits(survivors, 0, min_gap, fx0 + half_w, fx1 - half_w)
    if ev is not None:  # the specs the fit solve squeezed out, smallest first
        kept = {id(s) for s in survivors}
        ev["items"].extend(
            {"label": s[2], "outcome": "dropped", "reason": "squeezed_out"}
            for s in specs
            if id(s) not in kept
        )
    for i, ((tip, _dia, label, feat, mids), lx) in enumerate(zip(survivors, xs, strict=True)):
        ctx.place(
            Leader(tip=(tip[0], tip[1], 0), elbow=(lx, label_y, 0), label=label, draft=draft),
            f"m_dia_x{start + i}",
            view="front",
            feature=feat,
            measurement=mids,
        )
        if ev is not None:
            ev["items"].append(
                {
                    "name": f"m_dia_x{start + i}",
                    "label": label,
                    "outcome": "placed",
                    "pos": [lx, label_y],
                }
            )
    return len(survivors)


def _diameter_column_left(
    dwg, items, start: int = 0, trace=None, *, ctx, place_what_fits=_place_what_fits
) -> int:
    """ø-callout column to the LEFT of the front view for Z-turned step/boss
    diameters (#131) — the page-Y mirror of the row-below. A per-label occupancy
    gate drops only a label that would overprint a bore leader / existing callout
    sharing the left region (#144), never the whole column. Returns the count placed.
    *trace* (#736): one ``pass_events`` record with a placed/dropped item per callout."""
    if not items:
        return 0
    # Public feature callouts supply seven fields; counted automatic bands add count.
    items = [(*item, 1) if len(item) == 7 else item for item in items]
    ev = trace.pass_event("diameter_column_left", view="front") if trace is not None else None
    draft = dwg.draft
    fx0, fy0, _, fy1 = dwg.view_bounds("front")

    # Real measured width, not the per-char estimate: a thread spec is arbitrary text,
    # so `len * 0.62 em` can underestimate a wide label and let it cross the margin
    # (annotation_out_of_bounds). Measure the completed label like the row-below path does.
    def _label(value_text, dtol, thr, count):
        prefix = f"{count}× " if count > 1 else ""
        return prefix + f"ø{value_text}{_tol_suffix(dtol, draft)}" + (f" {thr}" if thr else "")

    label_w = max(
        _text_size(
            _label(value_text, dtol, thr, count),
            draft.font_size,
            getattr(draft, "font_path", DEFAULT_FONT_PATH),
            getattr(draft, "font", "Arial"),
        )[0]
        for _, _dia, value_text, _, dtol, thr, _, count in items
    )
    elbow_x = fx0 - (draft.font_size + 2 * draft.pad_around_text)
    # A left-directed leader hangs its label a shelf-length PAST the elbow, so the label's left
    # edge sits at elbow_x - shelf - label_w; the guard must reserve the shelf or a near-boundary
    # label overshoots the margin. The shelf is the helpers Leader's gap = draft.pad_around_text,
    # not a fixed 2.0.
    if elbow_x - draft.pad_around_text - label_w < _drawing_bounds(dwg)[0]:
        if ev is not None:
            ev["items"].extend(
                {"label": f"ø{text}", "outcome": "dropped", "reason": "no_room_left"}
                for _, _d, text, _, _, _, _, _ in items
            )
        return 0
    specs = []  # (tip_page, dia, label, feature, mids), tip on the step's left silhouette,
    for anchor, dia, value_text, feat, dtol, thr, mids, count in items:
        ax, ay, az = anchor
        tip = dwg.at("front", ax - dia / 2, ay, az)
        label = _label(value_text, dtol, thr, count)
        specs.append((tip, dia, label, feat, mids))
    half_h = draft.font_size / 2 + draft.pad_around_text
    min_gap = 2 * half_h
    # Place what fits; drop the smallest ø first, never the whole column.
    survivors, ys = place_what_fits(specs, 1, min_gap, fy0 + half_h, fy1 - half_h)
    # Full-footprint occupancy includes leader shafts, witness lines, and hatch.
    # A label-box-only check could let an ø label overprint a leader shaft.
    # Centre lines stay crossable because a diameter dimension may cross one.
    if ev is not None:  # the specs the fit solve squeezed out, smallest first
        kept = {id(s) for s in survivors}
        ev["items"].extend(
            {"label": s[2], "outcome": "dropped", "reason": "squeezed_out"}
            for s in specs
            if id(s) not in kept
        )
    occupied = strip_obstacles(dwg, view="front", crossable=CROSSABLE_TYPES)
    placed = 0
    for i, ((tip, _dia, label, feat, mids), ly) in enumerate(zip(survivors, ys, strict=True)):
        ldr = Leader(tip=(tip[0], tip[1], 0), elbow=(elbow_x, ly, 0), label=label, draft=draft)
        if len(mids) > 1:
            ldr.source_features = tuple(dict.fromkeys(mid.feature for mid in mids))
            ldr.indivisible_measurements = True
        if _box_hits(_anno_box(ldr), occupied):
            if ev is not None:
                ev["items"].append(
                    {"label": label, "outcome": "dropped", "reason": "label_occupied"}
                )
            continue  # would overprint a bore leader / existing callout — drop just this one
        ctx.place(ldr, f"m_dia_z{start + i}", view="front", feature=feat, measurement=mids)
        if ev is not None:
            ev["items"].append(
                {
                    "name": f"m_dia_z{start + i}",
                    "label": label,
                    "outcome": "placed",
                    "pos": [elbow_x, ly],
                }
            )
        occupied.append(_anno_box(ldr))
        placed += 1
    return placed


def _diameter_step_anchor(anchor, groups):
    """Anchor point for a turned ⌀ leader tip.

    Centre the leader at the MID-LENGTH of the STEP carrying this diameter, rather
    than the frame origin passed in (which for a boss or a shared ⌀'s first-bucketed
    step can be an end corner). Only STEP spans are used: a step round-trips its
    length + centre through the emitted Sheet script (declared ``length=``/``at=``),
    so direct and scripted builds agree (#707 parity); a boss is re-synthesised
    without a declared span and keeps its frame origin in both paths (``anchor``).

    When a ⌀ is shared by several steps, select ONE — the longest run, leftmost on
    ties — and use THAT step's OWN origin (radial + axial). Never the convex-hull
    midpoint (which for disjoint ⌀A–⌀B–⌀A steps falls in the ⌀B gap, off any
    silhouette) and never the bucket ``anchor``'s radial (which for non-coaxial
    same-⌀ steps belongs to a different feature). The selection quantises both the
    length and the lower end to the emitter's 3-dp values, so direct and scripted
    pick the same run. In the #426 finalize/``only=`` path this centres over the
    recorded subset, as the pre-#794 first-recorded-origin anchor also did.
    """
    steps = [
        (g, g.dim(kind="length"))
        for g in groups
        if g.feature_kind == "step" and g.dim(kind="length") is not None
    ]
    steps = [(g, d) for g, d in steps if d.span is not None]
    if not steps:
        return anchor
    idx = {"x": 0, "y": 1, "z": 2}[steps[0][0].facts.frame.axis]

    def _key(item):
        _g, dim = item
        ends = sorted(end[idx] for end in dim.span)
        length = round(ends[1] - ends[0], 3)  # emitter rounds step length and centre (`at`) to
        at = round((ends[0] + ends[1]) / 2, 3)  # 3dp — quantise both so the selection round-trips
        return (length, -(at - length / 2))

    step, length = max(steps, key=_key)
    ends = sorted(end[idx] for end in length.span)
    centred = list(step.facts.frame.origin)  # the SELECTED step's own origin (radial + axial)
    centred[idx] = (ends[0] + ends[1]) / 2
    return tuple(centred)


def _manufacturing_suffix(
    thread, knurl=None, *, include_source_pmi=True, manufacturing_tags=None
) -> str | None:
    """Renderer text for typed manufacturing aspects after the canonical diameter."""
    terms = []
    if isinstance(thread, ThreadRequirement):
        if include_source_pmi:
            terms.append(manufacturing_callout_suffix(thread, manufacturing_tags))
    elif thread:
        terms.append(str(thread))
    if isinstance(knurl, KnurlRequirement) and include_source_pmi:
        terms.append(manufacturing_callout_suffix(knurl, manufacturing_tags))
    return "; ".join(terms) or None


def _external_diameter_rider(
    rider: str | None, value_text: str, axis: str, bore_values: set[str]
) -> str | None:
    """Name a Z-step OD when a bore prints the same nominal size in front view."""
    if axis == "z" and value_text in bore_values:
        return f"{rider} OD" if rider else "OD"
    return rider


_DIAMETER_LEAD_DIRS = {
    "x": ((0, -1), (0, 1)),
    "z": ((-1, 0), (1, 0)),
}


def _diameter_source_bounds(dwg, view, feature, diameter) -> tuple[float, float, float, float]:
    """Projected cylindrical-envelope bounds for a typed diameter leader.

    A profile-view diameter leader must start on the owning cylindrical band, not at the
    feature centre.  Build the orthographic envelope from the feature's finite axial span and
    both radial basis directions; the projection collapses the hidden radial direction and
    leaves the truthful visible silhouette.  A span-less boss retains its declared centre,
    which is the same fallback used by the legacy diameter row.
    """
    span = getattr(feature, "span", None)
    centres = tuple(span) if span is not None else (feature.frame.origin,)
    axis_index = "xyz".index(feature.frame.axis)
    radius = float(diameter) / 2
    points = []
    for centre in centres:
        points.append(dwg.at(view, *centre))
        for radial_index in range(3):
            if radial_index == axis_index:
                continue
            for sign in (-1, 1):
                point = list(centre)
                point[radial_index] += sign * radius
                points.append(dwg.at(view, *point))
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _render_diameter_leaders(
    dwg,
    a,
    indexed_buckets,
    *,
    prefix,
    start,
    ctx,
    include_source_pmi=True,
    radial_candidates,
    place_jobs,
    leader_reach,
) -> int:
    """Route external diameters through the shared leader solve.

    The legacy row deliberately gives every item the widest label's pitch.  That is stable and
    tidy for short diameter strings, but an authoritative manufacturing paragraph cannot share
    that pitch without evicting its siblings.  Collect typed buckets into the global
    feature-leader solve, which has independent label footprints and multiple clear lanes.
    Plain diameters that did not fit their row use these same candidates.
    """
    vb = dwg.view_bounds("front")
    if vb is None:
        return 0
    reach = leader_reach(dwg.draft)
    jobs = []
    source_ids_by_name = {}
    for index, (_anchor, dia, value_text, refs, dtol, suffix, groups) in indexed_buckets:
        owner = next(iter(refs)) if len(refs) == 1 else None
        representative = groups[0].facts
        source_bounds = _diameter_source_bounds(dwg, "front", representative, dia)
        raw_candidates = radial_candidates(
            dwg,
            "front",
            vb,
            representative,
            reach,
            source_bounds=source_bounds,
            directions=_DIAMETER_LEAD_DIRS[representative.frame.axis],
        )

        def _wide_lanes(_raw=raw_candidates, _owner=owner):
            # Short adjacent turned bands put their radial leaders directly under the axial
            # dimension chain.  The shared adapter adds nearby lanes; typed manufacturing
            # text also needs a bounded set of farther elbows so its shaft can clear those
            # fixed labels while its arrow remains on the same cylindrical rim.
            spacing = dwg.draft.font_size + 2 * dwg.draft.pad_around_text
            for tip, elbow, _provenance in _raw:
                dx, dy = float(elbow[0]) - float(tip[0]), float(elbow[1]) - float(tip[1])
                length = math.hypot(dx, dy) or 1.0
                px, py = -dy / length, dx / length
                for lane in (0, 8, -8):
                    yield (
                        tip,
                        (
                            float(elbow[0]) + px * spacing * lane,
                            float(elbow[1]) + py * spacing * lane,
                            0,
                        ),
                        _owner,
                    )

        candidates = _wide_lanes()
        name = f"{prefix}{start + index}"
        source_ids_by_name[name] = (
            tuple(
                dict.fromkeys(
                    source_id
                    for group in groups
                    for aspect in (
                        group.facts.get("thread"),
                        group.facts.get("knurl"),
                    )
                    for source_id in getattr(aspect, "source_ids", ())
                )
            )
            if include_source_pmi
            else ()
        )
        jobs.append(
            (
                name,
                "front",
                vb,
                f"ø{value_text}{_tol_suffix(dtol, dwg.draft)}" + (f" {suffix}" if suffix else ""),
                candidates,
                tuple(
                    parameter.id
                    for group in groups
                    for parameter in group.dims
                    if parameter.kind == "diameter"
                ),
            )
        )
    return cast(
        int,
        place_jobs(
            dwg,
            a,
            jobs,
            noun="external diameter",
            drop_code="diameter_dropped",
            ctx=ctx,
            geom_clear=True,
            joint=True,
            source_ids_by_name=source_ids_by_name,
        ),
    )


def _diameter_bucket_item(entry):
    """Carry an approved diameter identity into row or column placement."""
    anchor, diameter, value_text, refs, tolerance, rider, groups = entry
    return (
        _diameter_step_anchor(anchor, groups),
        diameter,
        value_text,
        next(iter(refs)) if len(refs) == 1 else None,
        tolerance,
        rider,
        tuple(dim.id for group in groups for dim in group.dims if dim.kind == "diameter"),
    )


def _diameter_bucket_lane(entry) -> int | None:
    """The planner permits one exact lane for a physical diameter bucket."""
    lanes = {
        dim.lane
        for group in entry[6]
        for dim in group.dims
        if dim.kind == "diameter" and dim.lane is not None
    }
    if len(lanes) > 1:
        raise ValueError("one physical diameter cannot have conflicting requested lanes")
    return next(iter(lanes)) if len(lanes) == 1 else None


def _render_diameter_lanes(
    dwg,
    a,
    indexed_buckets,
    *,
    axis,
    prefix,
    start,
    ctx,
    radial_candidates,
    place_jobs,
    leader_reach,
) -> int:
    """Offer only the requested witness-relative rank to the shared leader solve."""
    vb = dwg.view_bounds("front")
    if vb is None:
        return 0
    spacing = dwg.draft.font_size + 2 * dwg.draft.pad_around_text
    jobs = []
    names = set()
    requested_lanes = {}
    covers_diameters_by_name = {}
    source_ids_by_name = {}
    include_source_pmi = not ctx.document_member or a.pmi_mode == "annotate"
    for index, entry in indexed_buckets:
        _anchor, dia, value_text, refs, tolerance, rider, groups = entry
        lane = _diameter_bucket_lane(entry)
        if lane is None:
            continue
        representative = groups[0].facts
        owner = next(iter(refs)) if len(refs) == 1 else None
        name = f"{prefix}{start + index}"
        names.add(name)
        requested_lanes[name] = lane
        source_ids_by_name[name] = (
            tuple(
                dict.fromkeys(
                    source_id
                    for group in groups
                    for aspect in (group.facts.get("thread"), group.facts.get("knurl"))
                    for source_id in getattr(aspect, "source_ids", ())
                )
            )
            if include_source_pmi
            else ()
        )
        if axis == "y":
            covers_diameters_by_name[name] = dia
        reach = leader_reach(dwg.draft) + (lane - 1) * spacing
        if axis == "y":
            candidates = radial_candidates(
                dwg,
                "front",
                vb,
                representative,
                reach,
                rim=dia / 2 * a.SCALE,
                directions=_END_DIAMETER_LEAD_DIRS,
                provenance=owner,
            )
        else:
            candidates = radial_candidates(
                dwg,
                "front",
                vb,
                representative,
                reach,
                source_bounds=_diameter_source_bounds(dwg, "front", representative, dia),
                directions=_DIAMETER_LEAD_DIRS[axis],
                provenance=owner,
            )
        label = f"ø{value_text}{_tol_suffix(tolerance, dwg.draft)}"
        if rider:
            label += f" {rider}"
        mids = tuple(
            parameter.id
            for group in groups
            for parameter in group.dims
            if parameter.kind == "diameter"
        )
        jobs.append((name, "front", vb, label, candidates, mids))
    if not jobs:
        return 0
    return cast(
        int,
        place_jobs(
            dwg,
            a,
            jobs,
            noun="diameter at requested lane",
            drop_code="diameter_dropped",
            ctx=ctx,
            geom_clear=True,
            joint=True,
            expand_lanes=False,
            straight_only_names=frozenset(names),
            requested_lanes=requested_lanes,
            covers_diameters_by_name=covers_diameters_by_name,
            source_ids_by_name=source_ids_by_name,
        ),
    )


def _counted_column_items(buckets, draft):
    """Share exactly two equal step labels while retaining both physical identities."""
    entries = list(buckets.values())
    items = [(*_diameter_bucket_item(entry), 1) for entry in entries]
    by_content: dict[tuple, list[int]] = {}
    for index, (entry, item) in enumerate(zip(entries, items, strict=True)):
        if len(entry[6]) == 1 and entry[6][0].feature_kind == "step":
            # Equal printed text may hide unequal approved values or tolerances.
            _anchor, diameter, value, _feature, tolerance, rider, _mids, _count = item
            by_content.setdefault(
                (diameter, value, _tol_suffix(tolerance, draft), rider), []
            ).append(index)
    removed = set()
    for indices in by_content.values():
        if (
            len(indices) != 2
            or items[indices[0]][0][2] == items[indices[1]][0][2]
            or items[indices[0]][4] != items[indices[1]][4]
        ):
            continue
        first, second = indices
        anchor, diameter, value, _feature, tolerance, rider, mids, _count = items[first]
        items[first] = (
            anchor,
            diameter,
            value,
            None,
            tolerance,
            rider,
            mids + items[second][6],
            2,
        )
        removed.add(second)
    return [item for index, item in enumerate(items) if index not in removed]


def _z_bore_specs(plan):
    return [
        (group.facts.frame.origin, dim.value_text)
        for group in plan.of_kind("rotational", "hole")
        if group.facts.frame.axis == "z"
        for dim in group.dims
        if dim.kind == "diameter" and dim.role == "bore"
    ]


def _matching_bore_values(bore_specs, origin):
    return {
        value
        for bore_origin, value in bore_specs
        if all(abs(bore_origin[index] - origin[index]) <= 1e-6 for index in (0, 1))
    }


def _render_x_diameter_buckets(
    dwg,
    a,
    buckets,
    start,
    *,
    ctx,
    trace,
    include_source_pmi,
    radial_candidates,
    place_jobs,
    leader_reach,
    place_what_fits,
) -> int:
    """Keep the ordinary X row and explicit-lane X leaders as separate owners."""
    indexed = list(enumerate(buckets.values()))

    def typed(entry):
        return include_source_pmi and any(
            isinstance(group.facts.get("thread"), ThreadRequirement)
            or isinstance(group.facts.get("knurl"), KnurlRequirement)
            for group in entry[6]
        )

    lane_entries = [(index, entry) for index, entry in indexed if _diameter_bucket_lane(entry)]
    typed_entries = [
        (index, entry)
        for index, entry in indexed
        if not _diameter_bucket_lane(entry) and typed(entry)
    ]
    plain_entries = [
        (index, entry)
        for index, entry in indexed
        if not _diameter_bucket_lane(entry) and not typed(entry)
    ]
    placed = 0
    # A requested lane splits the otherwise unchanged contiguous legacy row.
    for _, run in groupby(enumerate(plain_entries), key=lambda item: item[1][0] - item[0]):
        entries = [entry for _ordinal, entry in run]
        placed += _diameter_row_below(
            dwg,
            [_diameter_bucket_item(entry) for _index, entry in entries],
            start=start + entries[0][0],
            trace=trace,
            ctx=ctx,
            place_what_fits=place_what_fits,
        )
    placed += _render_diameter_leaders(
        dwg,
        a,
        typed_entries,
        prefix="m_dia_x",
        start=start,
        ctx=ctx,
        include_source_pmi=include_source_pmi,
        radial_candidates=radial_candidates,
        place_jobs=place_jobs,
        leader_reach=leader_reach,
    )
    unplaced = [
        entry
        for _index, entry in plain_entries
        if any(
            not dwg.registry.has_measurement(group.dim(kind="diameter").id) for group in entry[6]
        )
    ]
    placed += _render_diameter_leaders(
        dwg,
        a,
        list(enumerate(unplaced)),
        prefix="m_dia_x",
        start=start + len(buckets),
        ctx=ctx,
        include_source_pmi=include_source_pmi,
        radial_candidates=radial_candidates,
        place_jobs=place_jobs,
        leader_reach=leader_reach,
    )
    placed += _render_diameter_lanes(
        dwg,
        a,
        lane_entries,
        axis="x",
        prefix="m_dia_x",
        start=start,
        ctx=ctx,
        radial_candidates=radial_candidates,
        place_jobs=place_jobs,
        leader_reach=leader_reach,
    )
    return placed


def _render_z_diameter_buckets(
    dwg,
    a,
    buckets,
    start,
    *,
    ctx,
    trace,
    radial_candidates,
    place_jobs,
    leader_reach,
    place_what_fits,
) -> int:
    """Keep Z column names stable when one bucket selects an explicit lane."""
    indexed = list(enumerate(buckets.values()))
    placed = 0
    for _, run in groupby(
        enumerate((index, entry) for index, entry in indexed if not _diameter_bucket_lane(entry)),
        key=lambda item: item[1][0] - item[0],
    ):
        entries = [entry for _ordinal, entry in run]
        placed += _diameter_column_left(
            dwg,
            _counted_column_items({index: entry for index, entry in entries}, dwg.draft),
            start=start + entries[0][0],
            trace=trace,
            ctx=ctx,
            place_what_fits=place_what_fits,
        )
    placed += _render_diameter_lanes(
        dwg,
        a,
        [(index, entry) for index, entry in indexed if _diameter_bucket_lane(entry)],
        axis="z",
        prefix="m_dia_z",
        start=start,
        ctx=ctx,
        radial_candidates=radial_candidates,
        place_jobs=place_jobs,
        leader_reach=leader_reach,
    )
    return placed


def render_diameters(
    dwg,
    plan,
    a: Analysis,
    *,
    ctx,
    only=None,
    radial_candidates,
    place_jobs,
    leader_reach,
    reroute_crossing,
    place_what_fits=_place_what_fits,
) -> int:
    """ø leaders for a turned part's external step/boss diameters, from the IR —
    one owned callout per physical diameter measurement, in a tidy row below the front view
    (X-turning), a column to its left (Z-turning), or as radial leaders in the
    end-on front view (Y-turning). Orientation is the feature frame's axis, not
    separate detection paths. Replaces the engine's
    ``_annotate_turned_diameters`` (ADR 1 (was 0008) convergence). Diameters another
    annotation already covers are skipped.

    *only*, when given, restricts placement to step/boss features in the set — the #426
    finalize() path passes the recorded step/boss ``callout`` intents' features.
    ``None`` (the auto-pass) places every diameter with the historical 0-based
    ``m_dia_{x,z}`` naming; Y-axis leaders use ``m_dia_y``."""
    # Equal numeric diameters do not establish one physical measurement. Keep the
    # compiler owner in every key, including plain diameters, so public feature
    # edits retain their independent provenance and tolerances.
    row_buckets: dict = {}  # semantic print key -> [anchor, dia, text, {features}, tol,...]
    col_buckets: dict = {}  # Z-turned
    end_buckets: dict = {}  # Y-turned: radial leaders in the end-on front view
    include_source_pmi = not ctx.document_member or a.pmi_mode == "annotate"
    bore_specs = _z_bore_specs(plan)
    for g in plan.of_kind("step", "boss"):
        if only is not None and g.ref not in only:  # recorded finalize subset
            continue
        dpd = g.dim(kind="diameter")
        if dpd is None:
            continue
        dia = dpd.value
        thr = _manufacturing_suffix(
            g.facts.get("thread"),
            g.facts.get("knurl"),
            include_source_pmi=include_source_pmi,
            manufacturing_tags=ctx.manufacturing_tags,
        )
        bore_values = _matching_bore_values(bore_specs, g.facts.frame.origin)
        thr = _external_diameter_rider(thr, dpd.value_text, g.facts.frame.axis, bore_values)
        if dwg.registry.has_measurement(dpd.id):
            continue
        bucket = {"x": row_buckets, "y": end_buckets, "z": col_buckets}.get(g.facts.frame.axis)
        if bucket is None:
            continue
        dtol = dpd.tolerance
        dkey = (g.ref, dpd.value_text, thr)
        entry = bucket.setdefault(dkey, [g.anchor, dia, dpd.value_text, set(), dtol, thr, []])
        entry[3].add(g.ref)
        entry[6].append(g)
        if entry[4] is None:
            entry[4] = dtol

    # The placers name leaders m_dia_{x,z}{start+i} contiguously from one start.
    # The automatic pass uses start=0. Finalize may run after existing m_dia
    # names, so it starts past the maximum existing index rather than the
    # first-free (which is unsound for a multi-item run when the names are non-contiguous, e.g.
    # after drop: a gap below an occupied index would let the run wrap onto it and silently
    # overwrite an earlier leader). Starting past the max keeps the whole run free.
    def _next_start(prefix):
        idxs = [
            int(n[len(prefix) :])
            for n in ctx.registry.names()
            if n.startswith(prefix) and n[len(prefix) :].isdigit()
        ]
        return max(idxs) + 1 if idxs else 0

    start_x = _next_start("m_dia_x") if only is not None else 0
    start_y = _next_start("m_dia_y") if only is not None else 0
    start_z = _next_start("m_dia_z") if only is not None else 0
    trace = getattr(ctx, "trace", None)  # the immediate placers report to the trace too
    placed = _render_x_diameter_buckets(
        dwg,
        a,
        row_buckets,
        start_x,
        ctx=ctx,
        trace=trace,
        include_source_pmi=include_source_pmi,
        radial_candidates=radial_candidates,
        place_jobs=place_jobs,
        leader_reach=leader_reach,
        place_what_fits=place_what_fits,
    )
    placed += _render_z_diameter_buckets(
        dwg,
        a,
        col_buckets,
        start_z,
        ctx=ctx,
        trace=trace,
        radial_candidates=radial_candidates,
        place_jobs=place_jobs,
        leader_reach=leader_reach,
        place_what_fits=place_what_fits,
    )

    # A Y-axis step is end-on in the front view, so the X/Z profile-strip
    # leaders are geometrically inapplicable. Place one radial leader per
    # owned diameter around the concentric circles instead.
    if end_buckets:
        vb = dwg.view_bounds("front")
        if vb is not None:
            reach = dwg.draft.font_size + 6 * dwg.draft.pad_around_text
            hole_circles = []
            for g in plan.of_kind("hole", "pattern"):
                feature = g.facts
                if feature.frame.axis != "y":
                    continue
                bore = g.dim(kind="diameter", role="bore")
                if bore is None:
                    continue
                diameter = bore.value
                locations = feature.members or (feature.frame.origin,)
                for location in locations:
                    px, py, *_ = dwg.at("front", *location)
                    hole_circles.append((px, py, diameter / 2 * a.SCALE))
            jobs = []
            covered_by_name = {}
            for i, entry in enumerate(end_buckets.values()):
                if _diameter_bucket_lane(entry):
                    continue
                _anchor, dia, value_text, refs, dtol, thr, feature_groups = entry
                representative = feature_groups[0].facts
                owner = next(iter(refs)) if len(refs) == 1 else None
                # Materialise now: a generator expression would close over ``owner``
                # and all jobs would read the final loop iteration's feature when
                # place_machined_leader_jobs consumes them.
                candidates = [
                    (tip, elbow, owner)
                    for tip, elbow, _feature in radial_candidates(
                        dwg,
                        "front",
                        vb,
                        representative,
                        reach,
                        rim=dia / 2 * a.SCALE,
                        directions=_END_DIAMETER_LEAD_DIRS,
                    )
                ]
                # Preserve the existing pre-drain first-clear order, preferring
                # rays that avoid the bore circles.
                candidates.sort(
                    key=lambda candidate: _leader_hole_clearance(candidate, hole_circles),
                    reverse=True,
                )
                label = f"ø{value_text}{_tol_suffix(dtol, dwg.draft)}"
                if thr:
                    label += f" {thr}"
                name = f"m_dia_y{start_y + i}"
                # Retain the compiler identities of this owned diameter bucket.
                mids = tuple(
                    pd.id for gp in feature_groups for pd in gp.dims if pd.kind == "diameter"
                )
                jobs.append((name, "front", vb, label, candidates, mids))
                covered_by_name[name] = dia
            placed += place_jobs(
                dwg,
                a,
                jobs,
                noun="Y-axis step diameter",
                drop_code="diameter_dropped",
                ctx=ctx,
                geom_clear=True,
                cross_view_clearance=True,
            )
            # Internal concentric-circle leaders legitimately exit the outer
            # silhouette, like bore callouts. Retain their diameter coverage.
            for name, dia in covered_by_name.items():
                ann = ctx.registry.named(name)
                if ann is not None:
                    ann.covers_diameters = (dia,)
    placed += _render_diameter_lanes(
        dwg,
        a,
        [
            (index, entry)
            for index, entry in enumerate(end_buckets.values())
            if _diameter_bucket_lane(entry)
        ],
        axis="y",
        prefix="m_dia_y",
        start=start_y,
        ctx=ctx,
        radial_candidates=radial_candidates,
        place_jobs=place_jobs,
        leader_reach=leader_reach,
    )
    # A ⌀ leader routed diagonally into the body — cutting
    # the silhouette, or an end feature whose diagonal merely grazes it — is re-routed
    # to the clear side (the margin the feature sits at). Auto-pass only: the finalize
    # (only=) path replays recorded verbs and must not disturb pinned user dims.
    if only is None:
        # Rerouting must see the principal dimensions that registered before this pass.
        ctx.post_drain.append(lambda: reroute_crossing(dwg, ctx=ctx))
    return placed


_REROUTE_EDGE_TOL = 2.0  # page mm: a tip this close to an axial end sits AT that end
_REROUTE_SLACK = 1.0  # page mm: an elbow displaced this far toward the interior is "into the body"


def _copy_shared_measurement_metadata(source, target) -> None:
    for attr in ("source_features", "indivisible_measurements"):
        if hasattr(source, attr):
            setattr(target, attr, getattr(source, attr))


def _reroute_crossing_diameters(dwg, *, ctx, material_penalty) -> int:
    """Route a turned ⌀ leader that heads INTO the part body (#798) out to the
    nearest CLEAR margin, rather than diagonally into the body.

    Two triggers, both re-routed to the clear side:

    * The shaft CUTS THROUGH the body — measured on the shared filled material field,
      the same predicate the router and the critique use (#798). It used to use the
      outline-crossing heuristic the critique has since retired, which made this a third
      consumer on a rule the other two no longer trust: a shaft merely passing over a
      through-hole was re-routed here while the critique would not have flagged it.
    * An END feature (tip at the part's axial extreme, e.g. a ⌀6 boss stub) whose
      row/column-solved elbow diagonals back INTO the body — a near-miss that reads
      as clipping the outline even where it does not strictly cross.

    Axis-aware: an ``m_dia_x`` (X-turned) leader's axial run is page-X and its clear
    margins are left/right; an ``m_dia_z`` (Z-turned) leader's axial run is page-Y
    and its margins are bottom/top. Candidates (near margin → straight out either way
    along the radial axis) are kept only when the new shaft clears the outline AND the
    rebuilt leader's LABEL stays inside the page and off every other view/annotation
    box — so a re-route never trades an info crossing for an out-of-bounds or overlap
    error (the shaft itself, like the row/column placers, is gated only on the
    silhouette). If nothing is both clear and safe the leader is restored unchanged
    (Phase-1 then flags it). A PINNED leader (ADR 2 (was 0012)) is never moved. Returns the
    number re-routed."""
    field = view_material(dwg, "front")
    if field is None:
        return 0

    def _cuts(tip_point, elbow_point) -> bool:
        return bool(material_penalty(tip_point, elbow_point, field))

    fb = dwg.view_bounds("front")
    if not fb:
        return 0
    draft = dwg.draft
    gap = draft.font_size + 2 * draft.pad_around_text
    page = _drawing_bounds(dwg)

    def _within_page(box):
        return box[0] >= page[0] and box[1] >= page[1] and box[2] <= page[2] and box[3] <= page[3]

    rerouted = 0
    for name in [n for n in dwg.annotations() if n.startswith(("m_dia_x", "m_dia_z"))]:
        ldr = dwg.get_annotation(name)
        if ldr is None or getattr(ldr, "elbow", None) is None:
            continue
        if dwg.registry.is_pinned(name):  # a pin is the user's "stays put" (ADR 2 (was 0012))
            continue
        tip, elbow = ldr.tip, ldr.elbow
        crosses = _cuts(tip, elbow)
        ax = 0 if name.startswith("m_dia_x") else 1  # axial page axis (X row / Y column)
        rad = 1 - ax
        lo_b, hi_b = fb[ax], fb[ax + 2]
        at_lo = tip[ax] - lo_b <= _REROUTE_EDGE_TOL
        at_hi = hi_b - tip[ax] <= _REROUTE_EDGE_TOL
        into_body = (at_lo and elbow[ax] > tip[ax] + _REROUTE_SLACK) or (
            at_hi and elbow[ax] < tip[ax] - _REROUTE_SLACK
        )
        if not (crosses or into_body):
            continue
        # Clear-side elbows toward the CLEAR end only: the near axial margin (the end
        # the feature sits at) first, then straight out either way along the radial
        # axis. The far axial margin is deliberately NOT a candidate — a leader run
        # the whole length of the part to the opposite end reads worse than the
        # near-miss it replaces; restore-and-flag is the honest fallback instead.
        toward_lo = tip[ax] - lo_b <= hi_b - tip[ax]
        near_a = lo_b - gap if toward_lo else hi_b + gap
        edge_a = lo_b - draft.pad_around_text if toward_lo else hi_b + draft.pad_around_text

        def _pt(a_val, r_val, _ax=ax):
            p = [0.0, 0.0]
            p[_ax], p[1 - _ax] = a_val, r_val
            return (p[0], p[1])

        candidates = (
            _pt(near_a, tip[rad]),
            _pt(edge_a, tip[rad]),
            _pt(tip[ax], fb[rad] - gap),
            _pt(tip[ax], fb[rad + 2] + gap),
        )
        ident = dwg.registry.identity_of(name)  # every axis, as a unit
        old = dwg.remove(name)  # remove first so obstacles exclude the leader being replaced
        placed_it = False
        try:
            obstacles = strip_obstacles(dwg, crossable=CROSSABLE_TYPES)
            for vis, hid in dwg.views.values():
                for shp in (vis, hid):
                    if shp is None:
                        continue
                    b = shp.bounding_box()
                    obstacles.append((b.min.X, b.min.Y, b.max.X, b.max.Y))
            for ex, ey in candidates:
                if _cuts(tip, (ex, ey)):
                    continue
                cand = Leader(
                    tip=(tip[0], tip[1], 0), elbow=(ex, ey, 0), label=ldr.label, draft=draft
                )
                _copy_shared_measurement_metadata(ldr, cand)
                box = _anno_box(cand)
                if box is None or not _within_page(box) or _box_hits(box, obstacles):
                    continue
                ctx.place(cand, name, view="front")
                dwg.registry.reapply(name, ident)  # the re-routed leader draws the same thing
                rerouted += 1
                placed_it = True
                break
        except Exception:  # noqa: BLE001 — a re-route error must never lose the leader
            placed_it = False
        if not placed_it and dwg.get_annotation(name) is None:
            ctx.place(old, name, view="front")  # restore the leader when rerouting fails
            dwg.registry.reapply(name, ident)
    return rerouted


# ── Shared machined-feature leader-callout pass  ──────────────────────────────────
# render_chamfers/_fillets/_flats/_pockets/_grooves were the same function five times: pick
# the view an edge/face reads in, lead a diagonal Leader out to a label, and keep it only if
# the LABEL lands in clear margin. They now share one pass and differ only in their label,
# their tip/lead geometry (corner-diagonal vs mid-face radial), and their view map — a sixth
# feature kind is a new thin adapter (or a table row), never a sixth copy.


def _leader_hole_clearance(
    candidate: tuple[tuple[float, float], tuple[float, float, float], Any],
    circles: list[tuple[float, float, float]],
) -> float:
    """Minimum page-space clearance from a leader shaft to projected hole circles.

    Ranking by the complete tip→elbow segment (not just the arrow tip) prevents a
    mathematically correct diameter-rim target from visually identifying a bolt
    hole farther along the same ray. With no holes every direction ties and retains
    the established candidate order.
    """
    if not circles:
        return math.inf
    tip, elbow, _feature = candidate
    ax, ay = tip[0], tip[1]
    bx, by = elbow[0], elbow[1]
    vx, vy = bx - ax, by - ay
    length2 = vx * vx + vy * vy
    clearances = []
    for cx, cy, radius in circles:
        if length2:
            t = max(0.0, min(1.0, ((cx - ax) * vx + (cy - ay) * vy) / length2))
        else:
            t = 0.0
        nearest_x, nearest_y = ax + t * vx, ay + t * vy
        clearances.append(math.hypot(cx - nearest_x, cy - nearest_y) - radius)
    return min(clearances)
