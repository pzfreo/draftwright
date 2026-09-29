"""Typed feature-leader regions and bounded candidate expansion.

Feature renderers supply physical anchors.  This rank-four owner expands those
anchors into deterministic interior/exterior alternatives before the shared
late assignment measures their ink and conflicts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from itertools import islice, tee
from typing import Any

from draftwright.annotations._leader_fixed_ink import _coerce_box
from draftwright.leader_policy import LeaderRegionPolicy


def _box_inside(inner, outer) -> bool:
    return bool(
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


class LeaderCandidateRegion(str, Enum):
    """Region a measured feature-leader candidate deliberately occupies."""

    EXTERIOR = "exterior"
    INTERIOR = "interior"


@dataclass(frozen=True)
class RadialLeaderTarget:
    """A circular boundary a leader must meet along its radius.

    Interior placement may rotate a leader ray to find clear label whitespace.  A
    fixed tip is sufficient for a corner, but rotating about a point on a circle
    makes the new shaft meet the circumference obliquely.  Carrying the projected
    centre and radius lets the shared producer validate the original proved normal,
    generate alternative rim sites, and keep every interior station on its own normal
    without learning which feature family supplied it.
    """

    center: tuple[float, float]
    radius: float

    def tip(self, direction: tuple[float, float]) -> tuple[float, float]:
        return (
            self.center[0] + direction[0] * self.radius,
            self.center[1] + direction[1] * self.radius,
        )


@dataclass(frozen=True)
class FeatureLeaderCandidate:
    """One typed producer alternative before analytical measurement.

    Existing producers may continue yielding ``(tip, elbow, feature)`` triples;
    those triples retain their historical exterior semantics.  Producers must
    opt into interior placement explicitly so the solver can apply its stricter
    proof and fallback rules.
    """

    tip: Any
    elbow: Any
    feature: Any
    region: LeaderCandidateRegion = LeaderCandidateRegion.EXTERIOR
    radial_target: RadialLeaderTarget | None = None
    preference_penalty: float = 0.0


_INTERIOR_RAY_ANGLES = (
    0.0,
    math.pi / 4.0,
    -math.pi / 4.0,
    math.pi / 2.0,
    -math.pi / 2.0,
    3.0 * math.pi / 4.0,
    -3.0 * math.pi / 4.0,
    math.pi,
)
# Eight one-text-lane stations on each ray keep the producer's contribution
# bounded at 64 alternatives per physical anchor.  That covers a useful local
# neighbourhood without making candidate count depend on part or sheet size.
_INTERIOR_LANES_PER_RAY = 8
# Multi-anchor features share one bounded interior inventory; pattern size must
# not multiply shared-solver work.
_INTERIOR_CANDIDATES_PER_FEATURE = 64


def _ray_exit_distance(point, direction, bounds) -> float:
    """Distance from an in-bounds point to the first rectangle edge on a ray."""

    x, y = point
    dx, dy = direction
    if not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]):
        return 0.0
    distances = []
    if dx > 1e-12:
        distances.append((bounds[2] - x) / dx)
    elif dx < -1e-12:
        distances.append((bounds[0] - x) / dx)
    if dy > 1e-12:
        distances.append((bounds[3] - y) / dy)
    elif dy < -1e-12:
        distances.append((bounds[1] - y) / dy)
    positive = [distance for distance in distances if distance >= 0.0]
    return min(positive, default=0.0)


def interior_leader_candidates(
    tip,
    preferred_elbow,
    feature,
    *,
    silhouette,
    analytical_geometry,
    draft,
    interior_label_clear=None,
    radial_target: RadialLeaderTarget | None = None,
):
    """Yield deterministic feature-relative label candidates inside a view.

    The producer's established tip-to-elbow ray supplies orientation rather than
    page coordinates.  Eight rotations cover the natural ray, its perpendiculars,
    diagonals, and reverse.  Candidate spacing is one text lane, so the inventory
    scales with drafting style and available view space instead of part-specific
    distances.  Exact projected-edge and annotation clearance remains the shared
    solver's responsibility.
    """

    tip2 = (float(tip[0]), float(tip[1]))
    dx = float(preferred_elbow[0]) - tip2[0]
    dy = float(preferred_elbow[1]) - tip2[1]
    length = math.hypot(dx, dy)
    if length <= 1e-12:
        return
    ux, uy = dx / length, dy / length
    if radial_target is not None:
        rx = tip2[0] - radial_target.center[0]
        ry = tip2[1] - radial_target.center[1]
        radial_length = math.hypot(rx, ry)
        tolerance = 1e-6 * max(1.0, radial_target.radius)
        if (
            abs(radial_length - radial_target.radius) > tolerance
            or abs(rx * uy - ry * ux) > tolerance
            or rx * ux + ry * uy <= 0.0
        ):
            return
    spacing = max(
        float(draft.font_size + 2.0 * draft.pad_around_text),
        float(draft.arrow_length + draft.pad_around_text),
    )
    # A corner-like attachment fans around its fixed tip.  A circular attachment
    # exposes the corresponding proved rim site for each ray, so the shaft remains
    # normal to the circumference instead of pivoting around one fixed point.
    for angle in _INTERIOR_RAY_ANGLES:
        cosine, sine = math.cos(angle), math.sin(angle)
        direction = (ux * cosine - uy * sine, ux * sine + uy * cosine)
        candidate_tip = radial_target.tip(direction) if radial_target is not None else tip2
        limit = _ray_exit_distance(candidate_tip, direction, silhouette)
        for lane in range(1, _INTERIOR_LANES_PER_RAY + 1):
            distance = lane * spacing
            if distance >= limit - 1e-9:
                break
            elbow = (
                candidate_tip[0] + direction[0] * distance,
                candidate_tip[1] + direction[1] * distance,
                0.0,
            )
            try:
                geometry = analytical_geometry(candidate_tip, elbow, feature)
                label = _coerce_box(geometry[0]) if geometry is not None else None
            except Exception:  # noqa: BLE001 — one optional ray must fail closed
                label = None
            if (
                label is not None
                and _box_inside(label, silhouette)
                and (interior_label_clear is None or interior_label_clear(label))
            ):
                yield FeatureLeaderCandidate(
                    tip=candidate_tip,
                    elbow=elbow,
                    feature=feature,
                    region=LeaderCandidateRegion.INTERIOR,
                    radial_target=radial_target,
                )


def feature_leader_candidates(
    raw_candidates,
    *,
    region_policy,
    silhouette,
    analytical_geometry,
    draft,
    exterior_candidates=None,
    interior_label_clear=None,
):
    """Apply one region policy to a producer's existing physical anchors.

    Families continue to own only their semantic tip/preferred-elbow pairs and
    annotation builder.  This adapter owns region expansion and filtering, so
    no family reimplements interior rays, distances, containment, or typed
    provenance.  ``exterior_candidates`` preserves an established exterior
    inventory when a feature supplies additional physical interior anchors.
    Legacy tuples remain exterior anchors.
    """

    policy = LeaderRegionPolicy(region_policy)
    anchors, default_exterior = tee(iter(raw_candidates))
    if policy is LeaderRegionPolicy.EXTERIOR:
        source = default_exterior if exterior_candidates is None else exterior_candidates
        for raw in source:
            candidate = (
                raw
                if isinstance(raw, FeatureLeaderCandidate)
                else FeatureLeaderCandidate(tip=raw[0], elbow=raw[1], feature=raw[2])
            )
            if candidate.region is LeaderCandidateRegion.EXTERIOR:
                yield candidate
        return

    # A grouped callout may have many equally valid physical attachment sites.
    # Share the bounded inventory round-robin across those sites; exhausting all
    # 64 lanes from the first member would make later members semantically
    # ineligible merely because they appeared later in deterministic model order.
    interior_sources = []
    for raw in islice(anchors, _INTERIOR_CANDIDATES_PER_FEATURE):
        candidate = (
            raw
            if isinstance(raw, FeatureLeaderCandidate)
            else FeatureLeaderCandidate(tip=raw[0], elbow=raw[1], feature=raw[2])
        )
        if candidate.region is LeaderCandidateRegion.INTERIOR:
            interior_sources.append(iter((candidate,)))
            continue
        if analytical_geometry is not None:
            interior_sources.append(
                iter(
                    interior_leader_candidates(
                        candidate.tip,
                        candidate.elbow,
                        candidate.feature,
                        silhouette=silhouette,
                        analytical_geometry=analytical_geometry,
                        draft=draft,
                        interior_label_clear=interior_label_clear,
                        radial_target=candidate.radial_target,
                    )
                )
            )

    interior_count = 0
    while interior_sources and interior_count < _INTERIOR_CANDIDATES_PER_FEATURE:
        remaining = []
        for source in interior_sources:
            try:
                interior = next(source)
            except StopIteration:
                continue
            yield interior
            interior_count += 1
            remaining.append(source)
            if interior_count >= _INTERIOR_CANDIDATES_PER_FEATURE:
                break
        interior_sources = remaining

    if policy is LeaderRegionPolicy.INTERIOR:
        return
    source = default_exterior if exterior_candidates is None else exterior_candidates
    for raw in source:
        candidate = (
            raw
            if isinstance(raw, FeatureLeaderCandidate)
            else FeatureLeaderCandidate(tip=raw[0], elbow=raw[1], feature=raw[2])
        )
        if candidate.region is LeaderCandidateRegion.EXTERIOR:
            yield candidate
