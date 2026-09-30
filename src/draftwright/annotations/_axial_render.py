"""Axial dimensions and turned-profile furniture from compiled drawing claims.

The public annotation passes remain available through ``from_model``. Step-detail
and step-length calls receive their chain placer from that facade so its test
and detail-recovery override remains effective.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, NamedTuple, cast

from build123d_drafting.helpers import Centerline, Leader

from draftwright._core import (
    _EST_CHAR_WIDTH_EM,
    _MIN_STEP_DIM_MM,
    _SLOT_DIM_HEIGHT,
    _SLOT_DIM_STEP,
    DetailRequest,
    _analysis_margins,
    _classify_steps,
    _dim,
    _drawing_bounds,
    _fmt,
    _log,
    _tol_suffix,
    supported_secondary_crop,
)
from draftwright.annotations._common import (
    _LOC_SUBCHAIN,
    CROSSABLE_TYPES,
    CorridorCandidate,
    Escalation,
    PlacementContext,
    _anno_box,
    _geom_box,
    prevent_dimension_label_ink,
    register_corridor,
    strip_obstacles,
    view_label_clearance,
)
from draftwright.annotations._height_ladder import (
    register_height_ladder_candidates as _register_height_ladder_candidates,
)
from draftwright.layout import StripCandidate, plan_strip
from draftwright.model.compiled import ApprovedDimension, FeatureRef


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


class _StepPositionCandidate(NamedTuple):
    """One approved shoulder position's deferred corridor callbacks."""

    p1: tuple[float, float]
    p2: tuple[float, float]
    label: str
    direction: str
    view: str
    rung: ApprovedDimension
    draft: Any
    ctx: PlacementContext

    def build(self, pos: float) -> Any:
        edge = self.p1[1]
        dim = _dim(
            (self.p1[0], edge, 0),
            (self.p2[0], edge, 0),
            self.direction,
            pos - edge if self.direction == "above" else edge - pos,
            self.draft,
            label=self.label,
        )
        return dim

    def drop(self, _name: str) -> None:
        self.ctx.record_issue(
            "warning",
            "step_position_dropped",
            f"step position {self.rung.final_label} not dimensioned "
            f"({self.view} {self.direction}-strip full)",
            measurement=self.rung.id,
            measurement_span=self.rung.span,
        )


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
        ctx.place(dim, name, view=view, measurement=measurements, scale=detail_scale)
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


def queue_step_detail(
    dwg, plan, feature, a, *, ctx, view_name, label, factor, source, _draw_step_chain
) -> bool:
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

    This pass selects legible approved rungs, then `_height_ladder` registers their
    chained witnesses and corridor candidates, including the left-strip escape for
    short rises. The compiler's omission never arrives here; a placement drop is
    reported as ``placement_unsatisfiable``. Returns the count REGISTERED."""

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

    return _register_height_ladder_candidates(
        dwg,
        frame,
        ctx,
        view,
        chain,
        order_values,
        short_rungs,
        overall,
        step,
        _zspan,
        register_corridor=register_corridor,
    )


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
        name = f"dim_shoulder_{axis}{i}"

        # The compiler's label plus its tolerance — a shoulder states a position the author can
        # tolerance like any other.
        shoulder_label = rung.final_label + _tol_suffix(rung.tolerance, draft)

        candidate_state = _StepPositionCandidate(
            p1,
            p2,
            shoulder_label,
            direction,
            view,
            rung,
            draft,
            ctx,
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
                build=candidate_state.build,
                order=(_LOC_SUBCHAIN, val, name),
                on_place=lambda nm: None,
                on_drop=candidate_state.drop,
                force=True,
                # The opaque provenance handle, passed straight through.
                feature=ladder.ref,
                measurement=rung.id,
                measurement_span=rung.span,
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
