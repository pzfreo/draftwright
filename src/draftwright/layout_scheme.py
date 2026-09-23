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

    def estimated_paper_span(self, font_size: float, padding: float = 0.0) -> float:
        """Estimate along-corridor ink in page mm without constructing render geometry."""

        if not math.isfinite(font_size) or font_size <= 0:
            raise ValueError("annotation font size must be finite and positive")
        if not math.isfinite(padding) or padding < 0:
            raise ValueError("annotation padding must be finite and non-negative")
        axis = 0 if self.side in {"above", "below"} else 1
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


def pack_annotation_lanes(
    scheme: AnnotationScheme,
    *,
    scale: float,
    span_for: Callable[[AnnotationDemand], float],
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
    axis_index = {"x": 0, "y": 1, "z": 2}
    for demand in scheme.demands:
        if demand.view not in VIEW_AXES or demand.side not in _SIDES:
            raise ValueError(
                f"annotation route for {demand.identity!r} must name a principal view and side"
            )
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
    for (view, side), intervals in sorted(grouped.items()):
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
        corridors.append(CorridorLanePlan(view, side, tuple(reservations)))
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
    return tuple(float(origin[index]) for index in range(3))


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
        return tuple(sorted((float(bbox[axis]), float(bbox[axis + 3]))))
    return None


def _text_ink_em(text: str) -> tuple[float, float]:
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


def plan_annotation_scheme(model) -> AnnotationScheme:
    """Collect explicit dimension/PMI furniture corridor demand from a ``PartModel``.

    Geometry-derived dimensions and feature leaders remain in the existing estimators for this
    prototype. Raw PMI is carried as ``unplanned`` rather than assigned to a guessed corridor.
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

    return AnnotationScheme(tuple(demands), tuple(unplanned))
