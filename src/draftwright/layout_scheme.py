"""Typed, render-free annotation topology for outer sheet planning.

This is the observational first slice of a drafter-style layout scheme.  It records which
semantic annotations need which view corridors before OCC annotation geometry exists.  The
current renderer still makes every placement decision; consumers may compare this plan with
measured view blocks without changing drawing output.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from draftwright.model.ir import authored_dimension_target_view
from draftwright.model.planner import annotation_groups, plan_dimensions
from draftwright.view_plan import VIEW_AXES

_VIEWS = frozenset({"front", "plan", "side", "rear"})
_SIDES = frozenset({"above", "below", "left", "right"})


@dataclass(frozen=True)
class AnnotationDemand:
    """One semantic request for a view-side corridor, without page coordinates."""

    identity: str
    family: str
    view: str
    side: str
    feature_index: int
    model_site: tuple[float, float, float]
    model_interval: tuple[float, float] | None = None
    estimated_ink_em: tuple[float, float] = (1.0, 1.0)
    dedicated_lane: bool = True

    def estimated_paper_span(self, font_size: float, padding: float = 0.0) -> float:
        """Estimate along-corridor ink in page mm without constructing render geometry."""

        if not math.isfinite(font_size) or font_size <= 0:
            raise ValueError("annotation font size must be finite and positive")
        if not math.isfinite(padding) or padding < 0:
            raise ValueError("annotation padding must be finite and non-negative")
        axis = 0 if self.side in {"above", "below"} else 1
        return self.estimated_ink_em[axis] * font_size + 2 * padding

    def estimated_paper_depth(self, font_size: float, padding: float = 0.0) -> float:
        """Estimate perpendicular corridor depth for a shared leader gutter."""

        if not math.isfinite(font_size) or font_size <= 0:
            raise ValueError("annotation font size must be finite and positive")
        if not math.isfinite(padding) or padding < 0:
            raise ValueError("annotation padding must be finite and non-negative")
        axis = 1 if self.side in {"above", "below"} else 0
        return self.estimated_ink_em[axis] * font_size + 2 * padding


@dataclass(frozen=True)
class UnplannedAnnotation:
    """A visible semantic carrier whose corridor cannot yet be selected truthfully."""

    identity: str
    family: str
    feature_index: int
    reason: str


@dataclass(frozen=True)
class AnnotationScheme:
    """Pre-render annotation topology plus explicit planning gaps."""

    demands: tuple[AnnotationDemand, ...]
    unplanned: tuple[UnplannedAnnotation, ...]

    def corridor(self, view: str, side: str) -> tuple[AnnotationDemand, ...]:
        return tuple(
            demand for demand in self.demands if (demand.view, demand.side) == (view, side)
        )

    def corridor_counts(self) -> dict[tuple[str, str], int]:
        counts: dict[tuple[str, str], int] = {}
        for demand in self.demands:
            key = (demand.view, demand.side)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "demands": [
                {
                    "identity": demand.identity,
                    "family": demand.family,
                    "view": demand.view,
                    "side": demand.side,
                    "feature_index": demand.feature_index,
                    "model_site": list(demand.model_site),
                    "model_interval": (
                        list(demand.model_interval) if demand.model_interval is not None else None
                    ),
                    "estimated_ink_em": list(demand.estimated_ink_em),
                    "dedicated_lane": demand.dedicated_lane,
                }
                for demand in self.demands
            ],
            "unplanned": [
                {
                    "identity": item.identity,
                    "family": item.family,
                    "feature_index": item.feature_index,
                    "reason": item.reason,
                }
                for item in self.unplanned
            ],
        }


@dataclass(frozen=True)
class LaneReservation:
    """One demand's model-relative interval in a corridor lane."""

    demand: AnnotationDemand
    start: float
    end: float
    lane: int


@dataclass(frozen=True)
class CorridorLanePlan:
    """Deterministic interval packing for one view-side corridor."""

    view: str
    side: str
    reservations: tuple[LaneReservation, ...]
    shared_depth: float = 0.0

    @property
    def lane_count(self) -> int:
        return 1 + max((item.lane for item in self.reservations), default=-1)


@dataclass(frozen=True)
class AnnotationLanePlan:
    """Render-free lane allocation for all routable annotation demand."""

    corridors: tuple[CorridorLanePlan, ...]

    def corridor(self, view: str, side: str) -> CorridorLanePlan | None:
        return next(
            (
                corridor
                for corridor in self.corridors
                if (corridor.view, corridor.side) == (view, side)
            ),
            None,
        )

    def corridor_depths(
        self, *, tier: float, gap: float = 0.0, spacing: float = 0.0
    ) -> dict[tuple[str, str], float]:
        """Return perpendicular paper-space depth required by each packed corridor."""

        for name, value, positive in (
            ("tier", tier, True),
            ("gap", gap, False),
            ("spacing", spacing, False),
        ):
            if not math.isfinite(value) or (value <= 0 if positive else value < 0):
                qualifier = "positive" if positive else "non-negative"
                raise ValueError(f"annotation lane {name} must be finite and {qualifier}")
        depths = {}
        for corridor in self.corridors:
            dedicated = (
                gap + corridor.lane_count * tier + max(0, corridor.lane_count - 1) * spacing
                if corridor.lane_count
                else 0.0
            )
            shared = gap + corridor.shared_depth if corridor.shared_depth else 0.0
            depths[(corridor.view, corridor.side)] = max(dedicated, shared)
        return depths


def pack_annotation_lanes(
    scheme: AnnotationScheme,
    *,
    scale: float,
    span_for: Callable[[AnnotationDemand], float],
    depth_for: Callable[[AnnotationDemand], float] | None = None,
    clearance: float = 0.0,
) -> AnnotationLanePlan:
    """Pack corridor demand into the fewest first-fit lanes.

    Intervals are relative to the model origin, so this stage needs neither page coordinates nor
    projected OCC geometry. ``span_for`` supplies the eventual paper-space footprint width (or
    height for left/right corridors); keeping that policy outside this topology layer lets a
    later renderer adapter use measured ink without making this prototype guess typography.
    """

    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("annotation lane scale must be finite and positive")
    if not math.isfinite(clearance) or clearance < 0:
        raise ValueError("annotation lane clearance must be finite and non-negative")

    grouped: dict[tuple[str, str], list[tuple[float, float, AnnotationDemand]]] = {}
    shared: dict[tuple[str, str], list[AnnotationDemand]] = {}
    axis_index = {"x": 0, "y": 1, "z": 2}
    for demand in scheme.demands:
        if demand.view not in VIEW_AXES or demand.side not in _SIDES:
            raise ValueError(
                f"annotation route for {demand.identity!r} must name a principal view and side"
            )
        if not demand.dedicated_lane:
            shared.setdefault((demand.view, demand.side), []).append(demand)
            continue
        span = float(span_for(demand))
        if not math.isfinite(span) or span <= 0:
            raise ValueError(
                f"annotation span for {demand.identity!r} must be finite and positive"
            )
        page_axis = 0 if demand.side in {"above", "below"} else 1
        model_axis = VIEW_AXES[demand.view][page_axis]
        try:
            station = float(demand.model_site[axis_index[model_axis]]) * scale
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError(
                f"annotation site for {demand.identity!r} must contain three coordinates"
            ) from exc
        if not math.isfinite(station):
            raise ValueError(f"annotation site for {demand.identity!r} must be finite")
        if demand.model_interval is None:
            start, end = station, station
        else:
            try:
                start, end = sorted(float(value) * scale for value in demand.model_interval)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"annotation support for {demand.identity!r} must contain two coordinates"
                ) from exc
            if not math.isfinite(start) or not math.isfinite(end):
                raise ValueError(f"annotation support for {demand.identity!r} must be finite")
            station = (start + end) / 2
        half_span = max(span, end - start) / 2
        grouped.setdefault((demand.view, demand.side), []).append(
            (station - half_span, station + half_span, demand)
        )

    corridors: list[CorridorLanePlan] = []
    for view, side in sorted(set(grouped) | set(shared)):
        intervals = grouped.get((view, side), [])
        lane_ends: list[float] = []
        reservations: list[LaneReservation] = []
        for start, end, demand in sorted(
            intervals, key=lambda item: (item[0], item[1], item[2].identity, item[2].feature_index)
        ):
            lane = next(
                (
                    index
                    for index, previous_end in enumerate(lane_ends)
                    if previous_end + clearance <= start
                ),
                len(lane_ends),
            )
            if lane == len(lane_ends):
                lane_ends.append(end)
            else:
                lane_ends[lane] = end
            reservations.append(LaneReservation(demand, start, end, lane))
        shared_depth = (
            max(
                (float(depth_for(demand)) for demand in shared.get((view, side), ())),
                default=0.0,
            )
            if depth_for is not None
            else 0.0
        )
        corridors.append(CorridorLanePlan(view, side, tuple(reservations), shared_depth))
    return AnnotationLanePlan(tuple(corridors))


def pack_estimated_annotation_lanes(
    scheme: AnnotationScheme,
    *,
    scale: float,
    font_size: float,
    padding: float = 0.0,
    clearance: float = 0.0,
) -> AnnotationLanePlan:
    """Pack a scheme using its cheap pre-render ink envelopes."""

    return pack_annotation_lanes(
        scheme,
        scale=scale,
        span_for=lambda demand: demand.estimated_paper_span(font_size, padding),
        depth_for=lambda demand: demand.estimated_paper_depth(font_size, padding),
        clearance=clearance,
    )


def _identity(feature, index: int) -> str:
    return str(
        getattr(feature, "source_id", "")
        or next(iter(getattr(feature, "source_ids", ())), "")
        or f"{getattr(feature, 'kind', type(feature).__name__)}:{index}"
    )


def _site(feature) -> tuple[float, float, float]:
    origin = getattr(getattr(feature, "frame", None), "origin", (0.0, 0.0, 0.0))
    return float(origin[0]), float(origin[1]), float(origin[2])


def _support_interval(feature, view: str, side: str) -> tuple[float, float] | None:
    """Return a dimension's model-space support along its corridor axis, when explicit."""

    page_axis = 0 if side in {"above", "below"} else 1
    axis = "xyz".index(VIEW_AXES[view][page_axis])
    points = tuple(getattr(feature, "ref_pts", ()))
    if len(points) >= 2:
        coordinates = [float(point[axis]) for point in points]
        return min(coordinates), max(coordinates)
    bbox = getattr(feature, "ref_bbox", None)
    if bbox is not None and len(bbox) == 6:
        first, second = sorted((float(bbox[axis]), float(bbox[axis + 3])))
        return first, second
    return None


def _text_ink_em(text: object) -> tuple[float, float]:
    """Cheap Plex-like text envelope used only by pre-render planning."""

    lines = str(text).splitlines() or [""]
    return max(1.0, max(map(len, lines), default=0) * 0.62), max(1.0, len(lines) * 1.3)


def _estimated_ink_em(feature) -> tuple[float, float]:
    kind = getattr(feature, "kind", "")
    if kind == "authored_dimension":
        return _text_ink_em(getattr(feature, "label", ""))
    if kind == "note":
        return _text_ink_em(getattr(feature, "text", ""))
    if kind == "datum_ref":
        return 2.0, 2.0
    if kind == "finish":
        value_width, _ = _text_ink_em(getattr(feature, "ra", ""))
        return max(3.0, 2.0 + value_width), 2.5
    if kind == "control_frame":
        tolerance = getattr(feature, "display_tolerance", None) or getattr(
            feature, "tolerance", ""
        )
        tolerance_width, _ = _text_ink_em(tolerance)
        width = 3.2 + tolerance_width + 2.0 * len(getattr(feature, "datums", ()))
        if getattr(feature, "diameter", False):
            width += 1.44
        if getattr(feature, "modifier", None):
            width += 1.84
        return width, 2.0
    return 1.0, 1.0


def _automatic_route(feature, group, dimension) -> tuple[str, str] | None:
    """Resolve a deterministic natural corridor for one approved automatic dimension."""

    view = dimension.view or group.view
    side = dimension.side or group.side
    if side in _SIDES:
        return view, side
    if feature.kind == "envelope":
        return {
            "width": ("plan", "below"),
            "depth": ("side", "below"),
            "height": ("front", "right"),
        }.get(dimension.param.role)
    if dimension.convention not in {"linear", "chain", "pitch"}:
        return None
    span = dimension.param.span
    if view not in VIEW_AXES or span is None:
        return None
    deltas = [abs(float(span[1][index]) - float(span[0][index])) for index in range(3)]
    projected = VIEW_AXES[view]
    along = max(projected, key=lambda axis: deltas["xyz".index(axis)])
    if deltas["xyz".index(along)] <= 1e-9:
        return None
    return view, "above" if along == projected[0] else "right"


def _automatic_support(members, view: str, side: str) -> tuple[float, float] | None:
    page_axis = 0 if side in {"above", "below"} else 1
    axis = "xyz".index(VIEW_AXES[view][page_axis])
    coordinates = [
        float(point[axis])
        for member in members
        if member.param.span is not None
        for point in member.param.span
    ]
    return (min(coordinates), max(coordinates)) if coordinates else None


def _compound_leader_route(model, group, members, corridor_loads) -> tuple[str, str] | None:
    explicit = {
        (member.view or group.view, member.side or group.side)
        for member in members
        if member.side is not None or group.side is not None
    }
    if explicit:
        return next(iter(explicit)) if len(explicit) == 1 else None
    view = group.view
    if view not in VIEW_AXES:
        return None
    site = _site(group.feature)
    axis_index = {"x": 0, "y": 1, "z": 2}
    bounds = {
        "x": (float(model.bbox.min.X), float(model.bbox.max.X)),
        "y": (float(model.bbox.min.Y), float(model.bbox.max.Y)),
        "z": (float(model.bbox.min.Z), float(model.bbox.max.Z)),
    }
    horizontal, vertical = VIEW_AXES[view]
    candidates = [
        (
            corridor_loads.get((view, "right"), 0),
            abs(bounds[horizontal][1] - site[axis_index[horizontal]]),
            0,
            "right",
        ),
        (
            corridor_loads.get((view, "above"), 0),
            abs(bounds[vertical][1] - site[axis_index[vertical]]),
            1,
            "above",
        ),
        (
            corridor_loads.get((view, "below"), 0),
            abs(site[axis_index[vertical]] - bounds[vertical][0]),
            3,
            "below",
        ),
    ]
    if view != "side":
        candidates.append(
            (
                corridor_loads.get((view, "left"), 0),
                abs(site[axis_index[horizontal]] - bounds[horizontal][0]),
                2,
                "left",
            )
        )
    return view, min(candidates)[3]


def _automatic_demand(
    identity, family, feature_index, feature, members, route, *, dedicated_lane=True
):
    view, side = route
    label = " ".join(f"{member.param.value:g}" for member in members)
    ink = _text_ink_em(label)
    if family == "feature_leader":
        # Quantity/diameter/depth/tolerance syntax is supplied by the renderer rather than
        # the numeric dimension members. Reserve a conservative label tail without creating
        # one perpendicular lane per callout.
        ink = (ink[0] + 6.0, ink[1])
    return AnnotationDemand(
        identity,
        family,
        view,
        side,
        feature_index,
        _site(feature),
        _automatic_support(members, view, side),
        ink,
        dedicated_lane,
    )


def _automatic_scheme_items(model, groups=None, corridor_loads=None):
    corridor_loads = {} if corridor_loads is None else corridor_loads
    indices = {id(feature): index for index, feature in enumerate(model.features)}
    groups = annotation_groups(model, plan_dimensions(model)) if groups is None else groups
    for group in groups:
        feature_index = indices.get(id(group.feature))
        if feature_index is None:
            feature_index = next(
                index for index, feature in enumerate(model.features) if feature == group.feature
            )
        leader_members = tuple(
            member
            for unit in group.units
            for member in unit.members
            if not member.suppressed and member.convention == "leader"
        )
        if leader_members:
            identity = f"auto:{feature_index}:feature_leader"
            route = _compound_leader_route(model, group, leader_members, corridor_loads)
            if route is None:
                yield UnplannedAnnotation(
                    identity,
                    "feature_leader",
                    feature_index,
                    "compound feature leader has no single typed corridor",
                )
            else:
                explicitly_exterior = any(
                    member.side is not None or group.side is not None for member in leader_members
                )
                demand = _automatic_demand(
                    identity,
                    "feature_leader",
                    feature_index,
                    group.feature,
                    leader_members,
                    route,
                    dedicated_lane=explicitly_exterior,
                )
                corridor_loads[(demand.view, demand.side)] = (
                    corridor_loads.get((demand.view, demand.side), 0) + 1
                )
                yield demand
        for unit in group.units:
            members = tuple(
                member
                for member in unit.members
                if not member.suppressed and member.convention != "leader"
            )
            if not members:
                continue
            identity = f"auto:{feature_index}:{unit.id}"
            resolved_routes = tuple(
                _automatic_route(group.feature, group, member) for member in members
            )
            routes = {route for route in resolved_routes if route is not None}
            if None in resolved_routes or len(routes) != 1:
                yield UnplannedAnnotation(
                    identity,
                    "automatic_dimension",
                    feature_index,
                    "automatic dimension has no single typed corridor",
                )
                continue
            view, side = next(iter(routes))
            demand = _automatic_demand(
                identity,
                "automatic_dimension",
                feature_index,
                group.feature,
                members,
                (view, side),
            )
            corridor_loads[(view, side)] = corridor_loads.get((view, side), 0) + 1
            yield demand


def plan_annotation_scheme(model, *, groups=None) -> AnnotationScheme:
    """Collect typed and approved automatic corridor demand from a ``PartModel``.

    Linear automatic dimensions use explicit placement intent, established envelope routes, or
    their projected support axis. Compound feature leaders use their declared route or a
    deterministic, load-balanced natural corridor. Unresolved angles and raw PMI are carried as
    ``unplanned`` rather than assigned to a guessed corridor.
    """

    demands: list[AnnotationDemand] = []
    unplanned: list[UnplannedAnnotation] = []
    for index, feature in enumerate(model.features):
        kind = getattr(feature, "kind", "")
        identity = _identity(feature, index)
        view = getattr(feature, "view", None)
        side = getattr(feature, "side", None)
        family = kind

        if kind == "authored_dimension":
            view = authored_dimension_target_view(
                getattr(feature, "dimension_kind", ""),
                getattr(feature, "dominant_axis", ""),
                view,
                side,
                getattr(feature, "angular_reference", None),
                getattr(feature, "ref_pts", ()),
            )
            if side is None:
                unplanned.append(
                    UnplannedAnnotation(identity, family, index, "no explicit corridor side")
                )
                continue
        elif kind == "pmi":
            unplanned.append(
                UnplannedAnnotation(identity, family, index, "raw PMI has no typed corridor")
            )
            continue
        elif kind not in {"control_frame", "datum_ref", "finish", "note"}:
            continue

        if view not in _VIEWS or side not in _SIDES:
            unplanned.append(
                UnplannedAnnotation(identity, family, index, "invalid or absent view-side route")
            )
            continue
        support = _support_interval(feature, view, side) if kind == "authored_dimension" else None
        demands.append(
            AnnotationDemand(
                identity,
                family,
                view,
                side,
                index,
                _site(feature),
                support,
                _estimated_ink_em(feature),
            )
        )

    corridor_loads = AnnotationScheme(tuple(demands), ()).corridor_counts()
    for item in _automatic_scheme_items(model, groups, corridor_loads):
        if isinstance(item, AnnotationDemand):
            demands.append(item)
        else:
            unplanned.append(item)

    return AnnotationScheme(tuple(demands), tuple(unplanned))
