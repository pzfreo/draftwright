"""Compiler-approved overall extents in the shared dimension corridor.

The planner supplies each extent and its label. This pass chooses a selected view,
registers a mandatory strip candidate, and reports an unplaced measurement after
the bounded above/interior retry. ``from_model`` keeps the public pass binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any

from draftwright._core import (
    _SLOT_DIM_DEPTH,
    _SLOT_DIM_WIDTH,
    _STRIP_SPACING,
    _WITNESS_LIFT_MM,
    Strip,
    _tol_suffix,
)
from draftwright.annotations._common import (
    PRIORITY,
    CorridorCandidate,
    InteriorDimensionJob,
    PlacementContext,
    dim_footprint,
    dimension_candidate_geometry,
    strip_free_span,
    strip_occupants,
)
from draftwright.annotations._height_ladder import _OVERALL_SUBCHAIN
from draftwright.model.compiled import DimensionId, FeatureRef
from draftwright.model.ir_foundation import Point
from draftwright.view_plan import views_showing

_MANDATORY_OVERALL_PRIORITY = PRIORITY.MANDATORY


def _env_label(approved, draft) -> str:
    """Include approved tolerance in the label; Dimension ignores it with label= (#1215)."""
    return f"{approved.value_text}{_tol_suffix(approved.tolerance, draft)}"


@dataclass(frozen=True, slots=True)
class _EnvelopeDropRetry:
    """One overall extent's deferred above/interior retry and refusal evidence."""

    dwg: Any
    ctx: PlacementContext
    dim_builder: Any
    place_strip_candidates_fn: Any
    view: str
    below: Strip | None
    above: Strip | None
    xs: tuple[float, float]
    label: str
    tier: float
    feature: FeatureRef | None
    measurement: DimensionId | None
    measurement_span: tuple[Point, Point] | None

    def report(self, name: str) -> None:
        # Both settled strips explain why the approved overall extent is missing.
        which = "width" if name.endswith("width") else "depth"
        msg = (
            f"overall {which} dimension not placed ({self.view}-view below and above strips full)"
        )
        for side_name, side_strip in (("below", self.below), ("above", self.above)):
            occupants = strip_occupants(self.dwg, side_strip, self.view, "y") if side_strip else []
            if occupants:
                msg = f"{msg[:-1]}; {side_name} occupied by: {', '.join(occupants)})"
        self.ctx.record_issue(
            "error",
            "overall_dim_withheld",
            msg,
            measurement=self.measurement,
            measurement_span=self.measurement_span,
        )

    def drop(self, name: str) -> None:
        # A mid-drain retry could occupy a later forced corridor candidate's space.
        self.ctx.post_drain.append(partial(self.retry, name))

    def retry(self, name: str) -> None:
        bounds = self.dwg.view_bounds(self.view)
        if bounds is not None:
            lift = bounds[3] + _WITNESS_LIFT_MM

            def _fallback_build(pos, _l=lift):
                dim = self.dim_builder(
                    (self.xs[0], _l, 0),
                    (self.xs[1], _l, 0),
                    "above",
                    pos - _l,
                    self.dwg.draft,
                    label=self.label,
                )
                dim._dw_measurement_span = self.measurement_span
                return dim

            if self.above is not None:
                if not self.place_strip_candidates_fn(
                    self.dwg,
                    self.above,
                    self.view,
                    "y",
                    [(name, _fallback_build)],
                    self.tier,
                    ctx=self.ctx,
                    measurements={name: self.measurement},
                    features={name: self.feature},
                    trace=self.ctx.trace,
                    trace_label=f"{name}_above_fallthrough",
                ):
                    return
            interior_jobs = getattr(self.ctx, "interior_dimensions", None)
            if interior_jobs is not None and not self.ctx.exterior_dimensions_only:

                def _interior_build(pos, _l=lift):
                    dim = self.dim_builder(
                        (self.xs[0], _l, 0),
                        (self.xs[1], _l, 0),
                        "below",
                        abs(pos - _l),
                        self.dwg.draft,
                        label=self.label,
                    )
                    dim._dw_measurement_span = self.measurement_span
                    return dim

                interior_jobs.append(
                    InteriorDimensionJob(
                        name=name,
                        view=self.view,
                        side="above",
                        build=_fallback_build,
                        on_place=lambda _name: None,
                        on_drop=self.report,
                        lane_step=(
                            self.tier
                            + (self.above.spacing if self.above is not None else _STRIP_SPACING)
                        ),
                        priority=_MANDATORY_OVERALL_PRIORITY,
                        feature=self.feature,
                        measurement=self.measurement,
                        interior_build=_interior_build,
                        analytical_geometry=lambda pos, _l=lift: dimension_candidate_geometry(
                            (self.xs[0], _l, 0),
                            (self.xs[1], _l, 0),
                            "below",
                            abs(pos - _l),
                            self.dwg.draft,
                            self.label,
                        ),
                    )
                )
                return
        self.report(name)


def render_envelope(
    dwg,
    plan,
    a,
    *,
    ctx,
    layout_frame_fn,
    register_corridor_fn,
    dim_builder,
    place_strip_candidates_fn,
) -> int:
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

        state = _EnvelopeDropRetry(
            dwg=dwg,
            ctx=ctx,
            dim_builder=dim_builder,
            place_strip_candidates_fn=place_strip_candidates_fn,
            view=view,
            below=strip,
            above=above_strip,
            xs=xs,
            label=label,
            tier=tier,
            feature=env.ref,
            measurement=measurement,
            measurement_span=measurement_span,
        )

        register_corridor_fn(
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
                on_drop=state.drop,
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
    frame = layout_frame_fn(a)
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
            lambda pos, _p1=p1, _p2=p2, _w=witness, _v=_env_label(extent, dwg.draft): dim_builder(
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
