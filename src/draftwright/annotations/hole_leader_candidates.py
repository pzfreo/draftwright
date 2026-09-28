"""Physical candidate streams for side and plan hole callouts.

The hole producer retains its strip solve and transaction. This adapter only
describes alternatives for the existing late feature-leader assignment.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from draftwright.annotations._common import (
    CROSSABLE_TYPES,
    leader_callout_geometry,
    strip_obstacles,
)
from draftwright.annotations.leaders import (
    FeatureLeaderCandidate,
    LeaderRegionPolicy,
    RadialLeaderTarget,
)


def hole_candidate_rows(
    final_y: float | None,
    base_y: float | None,
    segment_y: float | None,
    natural_y: float,
    y_min: float,
    y_max: float,
    obstacle_intervals: list[tuple[float, float]],
) -> tuple[float, ...]:
    """Preserve the strip winner and the producer's ordered row alternatives."""

    rows: list[float] = []
    for y in (
        final_y,
        base_y,
        segment_y,
        min(max(natural_y, y_min), y_max),
        y_min,
        y_max,
        *(value for interval in obstacle_intervals for value in interval),
    ):
        if y is None:
            continue
        y = min(max(float(y), y_min), y_max)
        if not any(abs(y - prior) <= 1e-6 for prior in rows):
            rows.append(y)
    return tuple(rows)


@dataclass(frozen=True)
class HoleLeaderCandidateAdapter:
    """Supply one hole job's physical alternatives to the shared leader solver.

    The callbacks carry the existing rim-tip and member-owner rules. In
    particular, no candidate is built or committed during adapter creation.
    """

    entry: tuple
    locations: tuple
    rows: tuple[float, ...]
    legacy_y: float | None
    owner: Any
    requested_side: str | None
    region_policy: LeaderRegionPolicy
    callout_box: tuple[float, float, float, float] | None
    projected_clear: Callable | None
    column_bands: tuple[tuple[float, float], ...]
    edge: float
    side: str
    view_bounds: tuple[float, float, float, float]
    y_min: float
    y_max: float
    min_gap: float
    to_page: Callable
    elbow_dx: float
    draft: Any
    scale: float
    dwg: Any
    ctx: Any
    anchors: Callable
    member_owner: Callable
    expand_regions: Callable

    def analytical_geometry(self, tip, elbow, _owner):
        candidate_side = "right" if elbow[0] >= tip[0] else "left"
        return leader_callout_geometry(
            tip,
            elbow,
            self.draft,
            text_side=candidate_side,
            callout_box=self.callout_box,
        )

    def exterior(self) -> Iterator[tuple]:
        # The late drain sees corridor ink unavailable to the initial strip solve.
        late_ys = list(self.rows)
        late_boundaries: list[float] = []
        for obstacle in strip_obstacles(self.dwg, crossable=CROSSABLE_TYPES):
            if self.column_bands and not any(
                obstacle[0] < band_hi and obstacle[2] > band_lo
                for band_lo, band_hi in self.column_bands
            ):
                continue
            late_boundaries.extend((obstacle[1] - self.min_gap, obstacle[3] + self.min_gap))
        for y in sorted(
            {
                min(max(float(value), self.y_min), self.y_max)
                for value in late_boundaries
                if self.y_min <= value <= self.y_max
            },
            key=lambda value: (abs(value - float(self.entry[4])), value),
        ):
            if not any(abs(y - prior) <= 1e-6 for prior in late_ys):
                late_ys.append(y)

        for y in late_ys:
            tip, elbow = self.anchors(
                self.entry,
                self.edge,
                self.side,
                y,
                self.to_page,
                self.elbow_dx,
                self.draft,
                self.scale,
            )
            yield (tip, elbow, self.owner)
        if self.requested_side is None:
            other_side = "left" if self.side == "right" else "right"
            other_edge = self.view_bounds[0] if other_side == "left" else self.view_bounds[2]
            for y in self.rows:
                tip, elbow = self.anchors(
                    self.entry,
                    other_edge,
                    other_side,
                    y,
                    self.to_page,
                    self.elbow_dx,
                    self.draft,
                    self.scale,
                )
                yield (tip, elbow, self.owner)

    def interior_anchors(self) -> Iterator[FeatureLeaderCandidate]:
        _locations, dia, callout, _feat, natural_y, rep = self.entry
        anchor_y = (
            self.rows[0] if self.rows else min(max(float(natural_y), self.y_min), self.y_max)
        )
        for location in self.locations or (rep,):
            member = (*self.entry[:5], location)
            tip, elbow = self.anchors(
                member,
                self.edge,
                self.side,
                anchor_y,
                self.to_page,
                self.elbow_dx,
                self.draft,
                self.scale,
            )
            centre = self.to_page(location)
            member_owner = self.member_owner(callout, location, self.owner)
            yield FeatureLeaderCandidate(
                tip=tip,
                elbow=elbow,
                feature=member_owner,
                radial_target=(
                    RadialLeaderTarget(
                        center=(float(centre[0]), float(centre[1])),
                        radius=float(dia) * float(self.scale) / 2.0,
                    )
                    if getattr(callout, "profile_boundary", None) is None
                    else None
                ),
            )

    def raw(self) -> Iterator[FeatureLeaderCandidate]:
        yield from self.expand_regions(
            self.interior_anchors(),
            region_policy=self.region_policy,
            silhouette=self.view_bounds,
            analytical_geometry=(
                self.analytical_geometry
                if self.projected_clear is not None and self.callout_box is not None
                else None
            ),
            draft=self.draft,
            exterior_candidates=self.exterior(),
            interior_label_clear=self.projected_clear,
        )

    def _legacy(self, side: str) -> tuple:
        edge = self.edge if side == self.side else self.view_bounds[0 if side == "left" else 2]
        tip, elbow = self.anchors(
            self.entry,
            edge,
            side,
            self.legacy_y,
            self.to_page,
            self.elbow_dx,
            self.draft,
            self.scale,
        )
        return (tip, elbow, self.owner)

    def fallback(self) -> Iterator[FeatureLeaderCandidate | tuple]:
        if self.region_policy is LeaderRegionPolicy.INTERIOR:
            yield from self.raw()
            return
        # The strip winner remains first. Automatic side choice may try that
        # same row on the other boundary before spending work on later lanes.
        if self.legacy_y is not None:
            yield self._legacy(self.side)
            if self.requested_side is None:
                yield self._legacy("left" if self.side == "right" else "right")
        yield from self.raw()

    def candidate_budget_fallback(self) -> Iterator[FeatureLeaderCandidate | tuple]:
        # Unmeasured candidates cannot improve the pre-joint producer floor.
        if self.region_policy is LeaderRegionPolicy.INTERIOR:
            yield from self.raw()
            return
        if self.legacy_y is None:
            return
        if not getattr(self.ctx, "dense_internal_section", False):
            yield self._legacy(self.side)
            return
        yield from self.raw()
