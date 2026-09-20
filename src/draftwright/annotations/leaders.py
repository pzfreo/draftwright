"""Shared bounded placement for automatic same-view feature leaders (#1166).

The feature renderers own semantic jobs, OCC construction, provenance, and drop
diagnostics.  This module is their one late inventory seam: it lowers the viable
alternatives to numeric costs/conflicts for :mod:`draftwright.layout`, then emits
the selected annotations exactly once.  No page coordinates are public API.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from itertools import chain, islice, tee
from typing import Any

from build123d import Face, Vector, Wire

from draftwright._core import _TB_CLEAR, _TB_H
from draftwright._geometry import (
    MATERIAL_VISIBLE_FLOOR,
    _boxes_overlap,
    _convex_polygon_overlaps_box,
    _convex_polygons_overlap,
    _leader_ink_polygons,
    _stroke_polygon,
    material_reentry_span,
)
from draftwright.annotations._common import (
    CROSSABLE_TYPES,
    _geom_box,
    annotation_obstacle_boxes,
    strip_obstacles,
)
from draftwright.layout import (
    _FLOW_COST_SCALE,
    _LEADER_ASSIGN_MAX_JOBS,
    _assign_leader_candidates,
    _LeaderAssignment,
)
from draftwright.model.compiled import resolve_feature
from draftwright.progress import activity, checkpoint
from draftwright.projection import _MATERIAL_PAGE_TOLERANCE

# One unit is the measured ~0.1 ms analytical candidate cost from #1308.  A real
# OCC Leader probe costs ~14.8 ms on the same corpus, so it is charged 150 units.
# The default preserves the former 512-OCC-probe ceiling while allowing the cheap
# analytical tier to spend the same bounded wall-work budget on more alternatives.
_FEATURE_LEADER_ANALYTICAL_MEASURE_WORK = 1
_FEATURE_LEADER_OCC_MEASURE_WORK = 150
_FEATURE_LEADER_MAX_MEASURE_WORK = 512 * _FEATURE_LEADER_OCC_MEASURE_WORK

# Fixed-ink inventory components and candidate×component probes are already direct
# measured operations, so one component/probe is one work unit (#1308).
_FEATURE_LEADER_MAX_FIXED_WORK = 100_000
_FEATURE_LEADER_MAX_PAIR_PROBES = 100_000
_FIXED_INVENTORY_EXHAUSTED = object()

# Page mm of shaft buried in the part per unit of Policy-B penalty (#798).
#
# This is the exchange rate between the two things the penalty now counts: crossing a
# piece of committed annotation ink, and cutting back through the part body. Stating it
# as a rate is the honest form, because neither strict ordering survives the range. A
# shaft grazing 0.3 mm of material is not worse than crossing a dimension line, and a
# shaft ploughing 63 mm through three lobes is far worse than crossing several. Charging
# per visible stroke width makes the trade continuous: ~1 unit for a graze, 254 for that
# 63 mm cut, so a real cut cannot be bought with a shorter route while a trivial one
# still loses only a close contest.
#
# The unit is the shared visible-stroke floor, and deliberately so — a cut the sheet
# cannot show must not steer the solve, and the router must not price what the critique
# would not report.
_MATERIAL_PENALTY_UNIT = MATERIAL_VISIBLE_FLOOR

# Raw candidates the resource-cap floor may examine past its first acceptable-but-cutting
# route while looking for one that does not cut. Bounded because the floor is what runs
# when the exact solve has already been ruled out on cost: it must stay lazy. Zero would
# restore the pre-#798 first-clear behaviour exactly.
_GREEDY_MATERIAL_LOOKAHEAD = 32


class _FeatureLeaderInvariantError(ValueError):
    """A compiler invariant violation that must remain loud at the public boundary."""


class LeaderCandidateRegion(str, Enum):
    """Region a measured feature-leader candidate deliberately occupies."""

    EXTERIOR = "exterior"
    INTERIOR = "interior"


class LeaderRegionPolicy(str, Enum):
    """Internal candidate-family policy; not yet part of the public DSL."""

    AUTO = "auto"
    INTERIOR = "interior"
    EXTERIOR = "exterior"


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
    spacing = max(
        float(draft.font_size + 2.0 * draft.pad_around_text),
        float(draft.arrow_length + draft.pad_around_text),
    )
    for angle in _INTERIOR_RAY_ANGLES:
        cosine, sine = math.cos(angle), math.sin(angle)
        direction = (ux * cosine - uy * sine, ux * sine + uy * cosine)
        limit = _ray_exit_distance(tip2, direction, silhouette)
        for lane in range(1, _INTERIOR_LANES_PER_RAY + 1):
            distance = lane * spacing
            if distance >= limit - 1e-9:
                break
            elbow = (
                tip2[0] + direction[0] * distance,
                tip2[1] + direction[1] * distance,
                0.0,
            )
            try:
                geometry = analytical_geometry(tip, elbow, feature)
                label = _coerce_box(geometry[0]) if geometry is not None else None
            except Exception:  # noqa: BLE001 — one optional ray must fail closed
                label = None
            if (
                label is not None
                and _box_inside(label, silhouette)
                and (interior_label_clear is None or interior_label_clear(label))
            ):
                yield FeatureLeaderCandidate(
                    tip=tip,
                    elbow=elbow,
                    feature=feature,
                    region=LeaderCandidateRegion.INTERIOR,
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


@dataclass(frozen=True)
class FeatureLeaderJob:
    """One semantic feature-callout job collected for the shared late solve.

    ``candidates`` yields typed alternatives or legacy ``(tip, elbow, feature)``
    triples. ``build`` is called only inside the bounded tier (or lazily by its
    greedy floor), so an oversized inventory cannot trigger collect-all OCC
    construction merely to discover that it is over budget.
    """

    name: str
    view: str
    silhouette: tuple[float, float, float, float]
    label: str
    candidates: Iterable[FeatureLeaderCandidate | tuple[Any, Any, Any]]
    build: Callable[[Any, Any, Any], Any]
    measurement: tuple[Any, ...]
    noun: str
    drop_code: str
    analytical_geometry: (
        Callable[
            [Any, Any, Any],
            tuple[
                tuple[float, float, float, float] | None,
                tuple[tuple[tuple[float, float], tuple[float, float]], ...],
            ]
            | None,
        ]
        | None
    ) = None
    fallback_candidates: Iterable[FeatureLeaderCandidate | tuple[Any, Any, Any]] | None = None
    candidate_budget_fallback_candidates: (
        Iterable[FeatureLeaderCandidate | tuple[Any, Any, Any]] | None
    ) = None
    fallback_accept: (
        Callable[[Any, tuple[Any, ...], tuple[float, float, float, float]], bool] | None
    ) = None
    interior_label_clear: Callable[[tuple[float, float, float, float]], bool] | None = None
    allow_policy_b_fixed: bool = False
    priority: float = 0.0
    on_place: Callable[[Any], None] | None = None
    on_drop: Callable[[str], None] | None = None


@dataclass(frozen=True)
class _MeasuredLeaderCandidate:
    annotation: Any
    tip: tuple[float, float]
    elbow: tuple[float, float]
    feature: Any
    raw_index: int
    cost: float
    label_box: tuple[float, float, float, float] | None
    segments: tuple[tuple[tuple[float, float], tuple[float, float]], ...]
    ink_polygons: tuple[tuple[tuple[float, float], ...], ...]
    axis_residual_polygons: tuple[tuple[tuple[float, float], ...], ...] = ()
    failure_reason: str | None = None
    region: LeaderCandidateRegion = LeaderCandidateRegion.EXTERIOR


@dataclass(frozen=True)
class _FixedInkComponent:
    """One rendered fixed-annotation component with a stable trace identity."""

    name: str
    polygons: tuple[tuple[tuple[float, float], ...], ...] = ()
    box: tuple[float, float, float, float] | None = None
    owner: Any = None
    kind: str = ""
    segment: tuple[tuple[float, float], tuple[float, float]] | None = None
    global_axis: bool = False


def _candidate_measure_work(job: FeatureLeaderJob) -> int:
    return (
        _FEATURE_LEADER_ANALYTICAL_MEASURE_WORK
        if job.analytical_geometry is not None
        else _FEATURE_LEADER_OCC_MEASURE_WORK
    )


def collect_feature_leader(ctx, job: FeatureLeaderJob) -> bool:
    """Append *job* to the per-run shared inventory when that mode is active.

    Direct renderer unit calls and finished-sheet live verbs leave
    ``ctx.feature_leaders`` as ``None`` and therefore retain their established
    immediate path.  Automatic annotation and deferred ``finalize()`` explicitly
    open the list and drain it at the one canonical stage.
    """

    pending = getattr(ctx, "feature_leaders", None)
    if pending is None:
        return False
    pending.append(job)
    return True


def _coerce_segments(raw) -> tuple:
    out = []
    try:
        for segment in raw:
            first, second = segment
            parsed = (
                (float(first[0]), float(first[1])),
                (float(second[0]), float(second[1])),
            )
            if any(not math.isfinite(value) for point in parsed for value in point):
                return ()
            out.append(parsed)
    except Exception:  # noqa: BLE001 — optional metadata iterators may fail
        return ()
    return tuple(out)


def _segments(annotation) -> tuple:
    try:
        raw = getattr(annotation, "segments", ()) or ()
    except Exception:  # noqa: BLE001 — optional fixed metadata must fail closed
        return ()
    return _coerce_segments(raw)


def _coerce_box(raw):
    if raw is None:
        return None
    try:
        box = tuple(float(value) for value in raw)
    except Exception:  # noqa: BLE001 — optional metadata iterators may fail
        return None
    return (
        box
        if len(box) == 4
        and all(math.isfinite(value) for value in box)
        and box[0] < box[2]
        and box[1] < box[3]
        else None
    )


def _label_box(annotation):
    try:
        raw = getattr(annotation, "label_bbox", None)
    except Exception:  # noqa: BLE001 — optional fixed metadata must fail closed
        return None
    return _coerce_box(raw)


def _ink_hits_box(candidate: _MeasuredLeaderCandidate, box) -> bool:
    """Exact local leader ink/label test against one decomposed obstacle box."""

    if candidate.label_box is not None and _boxes_overlap(candidate.label_box, box):
        return True
    return any(_convex_polygon_overlaps_box(polygon, box) for polygon in candidate.ink_polygons)


def _polygon_bounds(polygon):
    xs, ys = zip(*polygon, strict=True)
    return (min(xs), min(ys), max(xs), max(ys))


def _candidate_conflict(
    left: _MeasuredLeaderCandidate,
    right: _MeasuredLeaderCandidate,
    *,
    left_ink_bounds=None,
    right_ink_bounds=None,
):
    """Whether two alternatives' rendered leader ink cannot coexist."""

    if left_ink_bounds is None:
        left_ink_bounds = tuple(_polygon_bounds(polygon) for polygon in left.ink_polygons)
    if right_ink_bounds is None:
        right_ink_bounds = tuple(_polygon_bounds(polygon) for polygon in right.ink_polygons)
    if (
        left.label_box is not None
        and right.label_box is not None
        and _boxes_overlap(left.label_box, right.label_box)
    ):
        return True
    if left.label_box is not None and any(
        _boxes_overlap(bounds, left.label_box)
        and _convex_polygon_overlaps_box(polygon, left.label_box)
        for polygon, bounds in zip(right.ink_polygons, right_ink_bounds, strict=True)
    ):
        return True
    if right.label_box is not None and any(
        _boxes_overlap(bounds, right.label_box)
        and _convex_polygon_overlaps_box(polygon, right.label_box)
        for polygon, bounds in zip(left.ink_polygons, left_ink_bounds, strict=True)
    ):
        return True
    return any(
        _boxes_overlap(left_bounds, right_bounds)
        and _convex_polygons_overlap(left_polygon, right_polygon)
        for left_polygon, left_bounds in zip(left.ink_polygons, left_ink_bounds, strict=True)
        for right_polygon, right_bounds in zip(right.ink_polygons, right_ink_bounds, strict=True)
    )


def _candidate_bounds(candidate: _MeasuredLeaderCandidate):
    """Return one conservative AABB around every rendered component of a candidate."""

    xs: list[float] = []
    ys: list[float] = []
    if candidate.label_box is not None:
        xs.extend((candidate.label_box[0], candidate.label_box[2]))
        ys.extend((candidate.label_box[1], candidate.label_box[3]))
    for polygon in candidate.ink_polygons:
        xs.extend(point[0] for point in polygon)
        ys.extend(point[1] for point in polygon)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _axis_residual_ink(tip, elbow, draft):
    """Leader ink beyond the arrow-sized local tip-attachment neighbourhood."""

    dx, dy = float(elbow[0]) - float(tip[0]), float(elbow[1]) - float(tip[1])
    length = math.hypot(dx, dy)
    local_length = min(length, max(0.0, float(draft.arrow_length)))
    if length <= local_length + 1e-12:
        return ()
    start = (
        float(tip[0]) + dx * local_length / length,
        float(tip[1]) + dy * local_length / length,
    )
    shaft = _stroke_polygon(start, elbow, draft.line_width)
    return (shaft,) if shaft is not None else ()


def _raw_candidate_parts(raw):
    """Return candidate fields while preserving legacy triples as exterior."""

    if isinstance(raw, FeatureLeaderCandidate):
        return raw.tip, raw.elbow, raw.feature, raw.region
    tip, elbow, feature = raw
    return tip, elbow, feature, LeaderCandidateRegion.EXTERIOR


def _measure(raw_index, raw, job: FeatureLeaderJob, draft) -> _MeasuredLeaderCandidate:
    def safe_point(value):
        point = []
        for index in (0, 1):
            try:
                coordinate = float(value[index])
            except Exception:  # noqa: BLE001 — trace needs a stable failed-candidate point
                coordinate = 0.0
            point.append(coordinate if math.isfinite(coordinate) else 0.0)
        return tuple(point)

    if isinstance(raw, FeatureLeaderCandidate):
        raw_tip, raw_elbow, feature, region = raw.tip, raw.elbow, raw.feature, raw.region
    else:
        raw_tip = raw[0] if isinstance(raw, (tuple, list)) and raw else ()
        raw_elbow = raw[1] if isinstance(raw, (tuple, list)) and len(raw) > 1 else ()
        feature = raw[2] if isinstance(raw, (tuple, list)) and len(raw) > 2 else None
        region = LeaderCandidateRegion.EXTERIOR
    tip2 = safe_point(raw_tip)
    elbow2 = safe_point(raw_elbow)
    failure_reason: str | None
    try:
        tip, elbow, feature, region = _raw_candidate_parts(raw)
        tip2 = (float(tip[0]), float(tip[1]))
        elbow2 = (float(elbow[0]), float(elbow[1]))
        if not all(math.isfinite(value) for value in (*tip2, *elbow2)):
            raise ValueError("non-finite leader candidate")
        if job.analytical_geometry is None:
            annotation = job.build(tip, elbow, feature)
            label_box = _label_box(annotation)
            segments = _segments(annotation)
        else:
            annotation = None
            geometry = job.analytical_geometry(tip, elbow, feature)
            if geometry is None:
                label_box, segments = None, ()
            else:
                raw_label, raw_segments = geometry
                if raw_label is None:
                    raise ValueError("missing analytical label box")
                label_box = _coerce_box(raw_label)
                if label_box is None:
                    raise ValueError("invalid analytical label box")
                segments = tuple(
                    (
                        (float(first[0]), float(first[1])),
                        (float(second[0]), float(second[1])),
                    )
                    for first, second in raw_segments
                )
                if not all(
                    math.isfinite(value)
                    for first, second in segments
                    for value in (*first, *second)
                ):
                    raise ValueError("non-finite analytical leader segment")
        if label_box is None or not segments:
            raise ValueError("unmeasurable leader candidate")
        # The helper's shelf length is fixed for a job's label.  Summing real
        # segments gives the deterministic objective for mixed callout types.
        cost = sum(
            math.hypot(second[0] - first[0], second[1] - first[1]) for first, second in segments
        ) or math.hypot(elbow2[0] - tip2[0], elbow2[1] - tip2[1])
        if cost < 0 or not math.isfinite(cost * _FLOW_COST_SCALE):
            raise ValueError("leader candidate cost exceeds the layout fixed-point range")
        primary = _leader_ink_polygons(
            tip2,
            elbow2,
            arrow_length=draft.arrow_length,
            line_width=draft.line_width,
        )
        shelves = tuple(
            polygon
            for first, second in segments[1:]
            if (polygon := _stroke_polygon(first, second, draft.line_width)) is not None
        )
        axis_residual = _axis_residual_ink(tip2, elbow2, draft)
        if any(
            not math.isfinite(coordinate)
            for polygon in (*primary, *shelves, *axis_residual)
            for point in polygon
            for coordinate in point
        ):
            raise ValueError("non-finite analytical leader ink")
    except _FeatureLeaderInvariantError:
        raise
    except Exception:  # noqa: BLE001 — one optional alternative must fail closed
        # Preserve the bounded inventory/trace entry with its truthful terminal
        # cause.  A later producer alternative can still win; one helper failure
        # must not abort unrelated jobs in the shared stage.
        annotation = None
        label_box, segments = None, ()
        tip2 = safe_point(raw_tip)
        elbow2 = safe_point(raw_elbow)
        fallback_cost = math.hypot(elbow2[0] - tip2[0], elbow2[1] - tip2[1])
        cost = fallback_cost if math.isfinite(fallback_cost * _FLOW_COST_SCALE) else 0.0
        primary = shelves = axis_residual = ()
        failure_reason = "geometry_validation"
    else:
        failure_reason = None
    return _MeasuredLeaderCandidate(
        annotation,
        tip2,
        elbow2,
        feature,
        raw_index,
        cost,
        label_box,
        segments,
        (*primary, *shelves),
        (*axis_residual, *shelves),
        failure_reason=failure_reason,
        region=region,
    )


def _geometry_matches(candidate: _MeasuredLeaderCandidate, annotation, *, tol=1e-6) -> bool:
    """Validate a selected analytical candidate against its rendered survivor."""

    actual_label = _label_box(annotation)
    actual_segments = _segments(annotation)
    if candidate.label_box is None or actual_label is None:
        if candidate.label_box != actual_label:
            return False
    elif any(abs(left - right) > tol for left, right in zip(candidate.label_box, actual_label)):
        return False
    if len(candidate.segments) != len(actual_segments):
        return False
    return all(
        all(
            abs(left - right) <= tol
            for left, right in zip(first + second, actual_first + actual_second)
        )
        for (first, second), (actual_first, actual_second) in zip(
            candidate.segments, actual_segments, strict=True
        )
    )


def _point_in_convex_component(point, polygon, *, tol=1e-6) -> bool:
    signs = []
    for first, second in zip(polygon, (*polygon[1:], polygon[0]), strict=True):
        cross = (second[0] - first[0]) * (point[1] - first[1]) - (second[1] - first[1]) * (
            point[0] - first[0]
        )
        if abs(cross) > tol:
            signs.append(cross > 0.0)
    return not signs or all(sign == signs[0] for sign in signs)


def _face_exactly_covered(face, polygons, label, *, tol=1e-8) -> bool:
    """Whether continuous OCC face ink is contained by analytical components."""

    try:
        if label is not None:
            bbox = face.bounding_box()
            if (
                label[0] - tol <= float(bbox.min.X)
                and label[1] - tol <= float(bbox.min.Y)
                and float(bbox.max.X) <= label[2] + tol
                and float(bbox.max.Y) <= label[3] + tol
            ):
                return True
        cover_faces = [
            Face(
                Wire.make_polygon(
                    [Vector(float(x), float(y), 0.0) for x, y in polygon],
                    close=True,
                )
            )
            for polygon in polygons
            if len(polygon) >= 3
        ]
        if not cover_faces:
            return False
        residual = face.cut(*cover_faces)
        # build123d 0.10 returns a ``ShapeList`` for multi-tool cuts, while
        # 0.11 returns one area-bearing shape.  Both represent the same OCC
        # residual; normalise that public-version boundary before applying the
        # continuous-ink containment test.
        if hasattr(residual, "area"):
            residual_area = float(residual.area)
        else:
            residual_area = sum(float(piece.area) for piece in residual)
        return bool(residual_area <= max(tol, abs(float(face.area)) * 1e-9))
    except Exception:  # noqa: BLE001 — optional placement must fail closed
        return False


def _validated_face_mesh(face, tolerance):
    """Return one complete finite triangular mesh, or ``None`` when malformed."""

    checkpoint()
    try:
        vertices, raw_triangles = face.tessellate(tolerance)
        points = tuple((float(vertex.X), float(vertex.Y)) for vertex in vertices)
        if not points or any(not math.isfinite(value) for point in points for value in point):
            return None
        triangles = []
        referenced: set[int] = set()
        for raw_triangle in raw_triangles:
            triangle = tuple(raw_triangle)
            if (
                len(triangle) != 3
                or any(type(index) is not int for index in triangle)
                or len(set(triangle)) != 3
                or any(index < 0 or index >= len(points) for index in triangle)
            ):
                return None
            triangles.append(triangle)
            referenced.update(triangle)
        if not triangles or referenced != set(range(len(points))):
            return None
        edge_kinds = tuple(
            getattr(getattr(edge, "geom_type", None), "name", "") for edge in face.edges()
        )
    except Exception:  # noqa: BLE001 — optional placement must fail closed
        return None
    return points, tuple(triangles), edge_kinds


def _rendered_ink_matches(candidate: _MeasuredLeaderCandidate, annotation, *, tol=1e-6) -> bool:
    """Validate the selected OCC survivor against its analytical ink contract.

    Metadata parity alone cannot detect a helper change to arrow flare or stroke
    width. Tessellate every rendered face after the one selected Leader is built
    and require each mesh triangle to remain inside one convex measured label,
    shaft, shelf, or arrow component. Requiring a common containing component
    also prevents a rendered face from bridging disjoint analytical polygons.
    Candidate exploration remains pure arithmetic; only the bounded survivor
    pays this OCC validation cost.
    """

    def component_covers(points):
        label = candidate.label_box
        if label is not None and all(
            label[0] - tol <= point[0] <= label[2] + tol
            and label[1] - tol <= point[1] <= label[3] + tol
            for point in points
        ):
            return True
        return any(
            all(_point_in_convex_component(point, polygon, tol=tol) for point in points)
            for polygon in candidate.ink_polygons
        )

    try:
        faces = tuple(annotation.faces())
        if not faces:
            return False
        for face in faces:
            mesh = _validated_face_mesh(face, 0.01)
            if mesh is None:
                return False
            points, triangles, edge_kinds = mesh
            for triangle in triangles:
                if not component_covers(tuple(points[index] for index in triangle)):
                    return False
            if any(edge_kind != "LINE" for edge_kind in edge_kinds) and not _face_exactly_covered(
                face,
                candidate.ink_polygons,
                candidate.label_box,
            ):
                return False
    except Exception:  # noqa: BLE001 — optional placement must fail closed
        return False
    return True


def _materialize(dwg, job: FeatureLeaderJob, candidate: _MeasuredLeaderCandidate):
    try:
        annotation = candidate.annotation
        if annotation is None:
            annotation = job.build(candidate.tip, candidate.elbow, candidate.feature)
            if not _geometry_matches(candidate, annotation):
                return None
        if not _rendered_ink_matches(candidate, annotation):
            return None
        # Seed the Drawing's shared OCC-box memo with the one rendered survivor.
        # Candidate evaluation remains arithmetic; lint can reuse this validation
        # measurement rather than tessellating the committed Leader again (#1138).
        _geom_box(annotation, getattr(dwg, "box_cache", None))
    except _FeatureLeaderInvariantError:
        raise
    except Exception:  # noqa: BLE001 — optional placement must fail closed
        return None
    return annotation


def _candidate_hits_component(
    candidate: _MeasuredLeaderCandidate,
    component: _FixedInkComponent,
) -> bool:
    if (
        component.kind in {"CenterMark", "CenterlineCircle"}
        and component.owner is not None
        and component.owner is resolve_feature(candidate.feature)
    ):
        # A feature leader intentionally originates inside its own centre
        # furniture; unrelated centre furniture remains fixed ink (#305). An
        # interior label still has to clear the same feature's furniture: exempt
        # only the arrow-sized attachment neighbourhood, not the shelf or text.
        if candidate.region is LeaderCandidateRegion.EXTERIOR:
            return False
        if component.box is not None:
            if candidate.label_box is not None and _boxes_overlap(
                candidate.label_box, component.box
            ):
                return True
            return any(
                _convex_polygon_overlaps_box(polygon, component.box)
                for polygon in candidate.axis_residual_polygons
            )
        if candidate.label_box is not None and any(
            _convex_polygon_overlaps_box(polygon, candidate.label_box)
            for polygon in component.polygons
        ):
            return True
        return any(
            _convex_polygons_overlap(candidate_polygon, fixed_polygon)
            for candidate_polygon in candidate.axis_residual_polygons
            for fixed_polygon in component.polygons
        )
    if component.kind == "Centerline" and component.global_axis and component.segment is not None:
        first, second = component.segment
        sx, sy = second[0] - first[0], second[1] - first[1]
        length_squared = sx * sx + sy * sy
        if length_squared > 1e-12:
            tx = candidate.tip[0] - first[0]
            ty = candidate.tip[1] - first[1]
            station = (tx * sx + ty * sy) / length_squared
            nearest = (first[0] + station * sx, first[1] + station * sy)
            on_segment = (
                -1e-9 <= station <= 1.0 + 1e-9
                and math.hypot(candidate.tip[0] - nearest[0], candidate.tip[1] - nearest[1])
                <= 1e-6
            )
            lx = candidate.elbow[0] - candidate.tip[0]
            ly = candidate.elbow[1] - candidate.tip[1]
            non_collinear = abs(lx * sy - ly * sx) > 1e-9
            if on_segment and non_collinear:
                # A turned-feature leader may truthfully originate on the
                # global axis centreline.  The local arrow/line junction is an
                # attachment, not an unrelated crossing.  Only that primary
                # tip ink is exempt: a later shelf/label crossing the same axis
                # remains fixed-ink conflict, as does collinear shaft travel.
                residual_polygons = candidate.axis_residual_polygons
                if component.box is not None:
                    if candidate.label_box is not None and _boxes_overlap(
                        candidate.label_box, component.box
                    ):
                        return True
                    return any(
                        _convex_polygon_overlaps_box(polygon, component.box)
                        for polygon in residual_polygons
                    )
                if candidate.label_box is not None and any(
                    _convex_polygon_overlaps_box(polygon, candidate.label_box)
                    for polygon in component.polygons
                ):
                    return True
                return any(
                    _convex_polygons_overlap(candidate_polygon, fixed_polygon)
                    for candidate_polygon in residual_polygons
                    for fixed_polygon in component.polygons
                )
    if component.box is not None:
        return _ink_hits_box(candidate, component.box)
    if candidate.label_box is not None and any(
        _convex_polygon_overlaps_box(polygon, candidate.label_box)
        for polygon in component.polygons
    ):
        return True
    return any(
        _convex_polygons_overlap(candidate_polygon, fixed_polygon)
        for candidate_polygon in candidate.ink_polygons
        for fixed_polygon in component.polygons
    )


def _assign_by_view(
    job_views,
    costs_by_job,
    conflicts,
    *,
    priorities,
    penalties_by_job,
):
    """Solve the leader assignment independently per view and merge the results (#1188).

    **Exact, not an approximation.** Two facts make the problem separable: a candidate
    conflict is only ever constructed for a same-view pair, and every term of the
    lexicographic objective (placed, priority, penalty, cost) is a sum over jobs. The
    optimum of the whole inventory is therefore the union of the per-view optima.

    The reason to bother is that the search is combinatorial in the number of jobs. Solved
    as one set, a twenty-job part exhausts the state budget and falls back to the greedy
    floor — which is what was happening on every dense fixture, so Amendment 2's
    guarantees applied precisely nowhere they were needed. Solved per view, the same
    inventory is three small searches that complete.

    Each view gets the full state budget: the budgets bound the work of one search, and
    these searches are independent.
    """
    order: dict[str, list[int]] = {}
    for job_index, view in enumerate(job_views):
        order.setdefault(view, []).append(job_index)
    choices: list[int | None] = [None] * len(costs_by_job)
    optimal = True
    states = 0
    for view, members in order.items():
        local = {job_index: position for position, job_index in enumerate(members)}
        local_conflicts = []
        connected: dict[int, set[int]] = {position: set() for position in range(len(members))}
        for left_job, left_candidate, right_job, right_candidate in conflicts:
            left_in, right_in = left_job in local, right_job in local
            if not left_in and not right_in:
                continue
            if left_in != right_in:
                # The exactness of this decomposition rests on conflicts never spanning
                # views. Filtering such a pair away would silently produce an invalid
                # assignment, so assert the invariant instead of quietly relying on it.
                raise _FeatureLeaderInvariantError(
                    "leader conflict spans views "
                    f"({job_views[left_job]!r} vs {job_views[right_job]!r}); the per-view "
                    "decomposition is only exact while conflicts are same-view"
                )
            local_conflicts.append(
                (local[left_job], left_candidate, local[right_job], right_candidate)
            )
            connected[local[left_job]].add(local[right_job])
            connected[local[right_job]].add(local[left_job])

        # Conflict-connected components are independent for the same additive
        # reason views are.  Solving a sparse view as one Cartesian product can
        # exhaust the state budget even when each local collision cluster is
        # tiny; splitting the job graph is exact and gives every independent
        # search the documented bound.
        unseen = set(connected)
        components = []
        while unseen:
            root = min(unseen)
            discovered = []
            frontier = [root]
            unseen.discard(root)
            while frontier:
                position = frontier.pop()
                discovered.append(position)
                for neighbour in sorted(connected[position], reverse=True):
                    if neighbour in unseen:
                        unseen.discard(neighbour)
                        frontier.append(neighbour)
            components.append(tuple(sorted(discovered)))

        for component in components:
            component_local = {position: index for index, position in enumerate(component)}
            component_conflicts = [
                (
                    component_local[left],
                    left_candidate,
                    component_local[right],
                    right_candidate,
                )
                for left, left_candidate, right, right_candidate in local_conflicts
                if left in component_local and right in component_local
            ]
            result = _assign_leader_candidates(
                [costs_by_job[members[position]] for position in component],
                component_conflicts,
                priorities=[priorities[members[position]] for position in component],
                penalties_by_job=[penalties_by_job[members[position]] for position in component],
            )
            for component_index, position in enumerate(component):
                choices[members[position]] = result.choices[component_index]
            optimal = optimal and result.optimal
            states += result.states
    return _LeaderAssignment(tuple(choices), optimal, states)


def material_penalty_units(tip, elbow, field) -> int:
    """Policy-B penalty units for the part material a tip→elbow shaft cuts back into.

    Measured against the same filled field, with the same bridge, as the
    ``leader_crosses_silhouette`` critique, so a route a placer accepts cannot be one the
    critique then reports — one predicate by construction, not by agreement. Shared by
    every placer that weighs routing, so the answer cannot drift between them.

    Re-entry, not total traversal: a leader is attached to the feature it names, so its
    first passage out of the body is the legitimate exit every callout makes. Charging it
    would price every correct leader on the sheet as defective.
    """
    if field is None or not field:
        return 0
    cut = material_reentry_span(
        (tip[0], tip[1]), (elbow[0], elbow[1]), field, bridge=_MATERIAL_PAGE_TOLERANCE
    )
    return int(cut / _MATERIAL_PENALTY_UNIT) if cut > _MATERIAL_PENALTY_UNIT else 0


def view_material(dwg, view):
    """The filled projected material for *view*, or ``None`` when it is unavailable.

    The one lookup: the fields are keyed by projected-shape identity because those shapes
    carry no view label, which is a detail no placer should have to know.
    """
    try:
        placed = dwg.views.get(view)
        if not placed or placed[0] is None:
            return None
        return dwg.material_fields().get(id(placed[0]))
    except Exception:  # noqa: BLE001 — an unmeshable part routes on the other constraints
        return None


def _material_units(candidate: _MeasuredLeaderCandidate, field) -> int:
    """:func:`material_penalty_units` for an already-measured shared-inventory candidate."""
    return material_penalty_units(candidate.tip, candidate.elbow, field)


def _box_inside(inner, outer) -> bool:
    return bool(
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def _view_region_blocker(candidate, job) -> str | None:
    """Return the hard view blocker for a measured candidate, if any."""

    label = candidate.label_box
    if label is None:
        return None
    if candidate.region is LeaderCandidateRegion.EXTERIOR:
        return f"view:{job.view}:silhouette" if _boxes_overlap(label, job.silhouette) else None
    if not _box_inside(label, job.silhouette):
        return f"view:{job.view}:interior_bounds"
    clear = job.interior_label_clear
    if clear is None or not clear(label):
        return f"view:{job.view}:interior_projection_ink"
    return None


def _fixed_component_bounds(component: _FixedInkComponent):
    """Conservative box for the broad phase before exact ink intersection."""

    if component.box is not None:
        return component.box
    points = [point for polygon in component.polygons for point in polygon]
    if component.segment is not None:
        points.extend(component.segment)
    if not points:
        return None
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _possible_fixed_components(candidate, components):
    """Components whose conservative boxes can intersect candidate ink."""

    candidate_bounds = _candidate_bounds(candidate)
    if candidate_bounds is None:
        return tuple(components)
    possible = []
    for component in components:
        component_bounds = _fixed_component_bounds(component)
        if component_bounds is None or _boxes_overlap(candidate_bounds, component_bounds):
            possible.append(component)
    return tuple(possible)


def _fixed_blockers(candidate, job, page, fixed_components) -> tuple[str, ...]:
    checkpoint()
    blockers = []
    label = candidate.label_box
    if candidate.failure_reason is not None:
        blockers.append(candidate.failure_reason)
    elif label is None:
        blockers.append("unmeasurable_label")
    else:
        if label[0] < page[0] or label[1] < page[1] or label[2] > page[2] or label[3] > page[3]:
            blockers.append("page")
        if view_blocker := _view_region_blocker(candidate, job):
            blockers.append(view_blocker)
    fixed_blockers = tuple(
        component.name
        for component in fixed_components
        if _candidate_hits_component(candidate, component)
    )
    blockers.extend(fixed_blockers)
    if fixed_blockers and candidate.region is LeaderCandidateRegion.INTERIOR:
        blockers.append(f"view:{job.view}:interior_annotation_ink")
    return tuple(dict.fromkeys(blockers))


def _hard_fixed_blockers(blockers) -> tuple[str, ...]:
    """Constraints no compatibility/resource fallback may relax."""

    return tuple(
        blocker
        for blocker in blockers
        if blocker in {"page", "unmeasurable_label", "geometry_validation"}
        or blocker.startswith(("view:", "title_block"))
    )


def _convex_hull(points):
    """Deterministic convex hull for a small rendered face sample."""

    unique = sorted(set(points))
    if len(unique) < 3:
        return ()

    def cross(origin, first, second):
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (
            second[0] - origin[0]
        )

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    return tuple((*lower[:-1], *upper[:-1]))


def _rendered_face_hull(face, *, curve_envelope=0.01):
    """Lower one planar OCC face to a bounded containing polygon.

    Straight-edged faces retain their exact vertices.  Curved helper ink is
    tessellated once while the fixed inventory is collected, then receives a
    tiny square envelope matching the tessellation tolerance.  Candidate
    exploration remains pure polygon arithmetic; unlike an annotation AABB,
    each dashed arc remains a local actual-width component and does not flood a
    circle's empty interior.
    """

    mesh = _validated_face_mesh(face, curve_envelope)
    if mesh is None:
        return (), (), (), ()
    points, triangles, edge_kinds = mesh
    curved = any(edge_kind != "LINE" for edge_kind in edge_kinds)
    if curved:
        hull_points = tuple(
            (x + dx, y + dy)
            for x, y in points
            for dx in (-curve_envelope, curve_envelope)
            for dy in (-curve_envelope, curve_envelope)
        )
    else:
        hull_points = points
    return _convex_hull(hull_points), points, tuple(triangles), edge_kinds


def _rendered_residual_components(
    name,
    annotation,
    components,
    label,
    owner,
    kind,
    *,
    max_components=None,
    allow_empty_faces=False,
):
    """Rendered faces not already represented by segment/label metadata.

    ``Dimension.segments`` is intentionally variable: a shifted label may
    suppress either dimension-line span while both arrowheads still render.
    ``CenterlineCircle`` exposes no linear segments because its chain ring is
    made from OCC arcs, while filled datum/GD&T glyphs expose faces that have no
    segment metadata at all. Face-local lowering handles every rendered
    annotation kind without guessing segment count/order and keeps stable
    component identities.
    """

    def component_covers(points):
        if label is not None and all(
            label[0] - 1e-6 <= point[0] <= label[2] + 1e-6
            and label[1] - 1e-6 <= point[1] <= label[3] + 1e-6
            for point in points
        ):
            return True
        return any(
            all(_point_in_convex_component(point, polygon, tol=1e-5) for point in points)
            for component in components
            for polygon in component.polygons
        )

    residual = []
    try:
        raw_faces = annotation.faces()
        if max_components is None:
            faces = tuple(raw_faces)
        else:
            faces = tuple(islice(iter(raw_faces), max_components + 1))
            if len(faces) > max_components:
                return _FIXED_INVENTORY_EXHAUSTED
    except Exception:  # noqa: BLE001 — optional placement must fail closed
        return None
    if not faces:
        # Some provisional/final furniture publishes a complete analytical
        # footprint through ``fixed_ink_polygons`` while its OCC carrier is
        # edge-only.  That explicit contract is sufficient.  Empty faces with
        # only generic segment/label metadata remain unavailable because those
        # fields may be only a partial description of the rendered object.
        return () if allow_empty_faces else None
    for face in faces:
        try:
            hull, rendered_points, triangles, edge_kinds = _rendered_face_hull(face)
            if not hull:
                return None
            represented = (
                rendered_points
                and triangles
                and all(
                    component_covers(tuple(rendered_points[index] for index in triangle))
                    for triangle in triangles
                )
            )
            curved = any(edge_kind != "LINE" for edge_kind in edge_kinds)
            if represented and (
                not curved
                or _face_exactly_covered(
                    face,
                    tuple(polygon for component in components for polygon in component.polygons),
                    label,
                )
            ):
                continue
            component_kind = (
                "arc"
                if kind == "CenterlineCircle"
                else "arrow"
                if kind == "Dimension"
                and (len(edge_kinds) == 3 or any(edge_kind != "LINE" for edge_kind in edge_kinds))
                else "ink"
            )
        except Exception:  # noqa: BLE001 — malformed rendered ink is unavailable
            return None
        residual.append((component_kind, hull))
        if max_components is not None and len(residual) > max_components:
            return _FIXED_INVENTORY_EXHAUSTED
    residual.sort(
        key=lambda item: (
            item[0],
            min(point[0] for point in item[1]),
            min(point[1] for point in item[1]),
            max(point[0] for point in item[1]),
            max(point[1] for point in item[1]),
        )
    )
    indices: dict[str, int] = {}
    out = []
    for component_kind, polygon in residual:
        index = indices.get(component_kind, 0)
        indices[component_kind] = index + 1
        out.append(
            _FixedInkComponent(
                f"{name}:{component_kind}:{index}",
                polygons=(polygon,),
                owner=owner,
                kind=kind,
            )
        )
    return tuple(out)


def _annotation_fixed_ink(dwg, name, annotation, *, max_components=None):
    """Exact-width fixed ink components for one already-rendered annotation."""

    components: list[_FixedInkComponent] = []
    owner = dwg.registry.feature_of(name)
    kind = type(annotation).__name__
    line_width = (
        0.15 if kind in {"CenterMark", "Centerline", "CenterlineCircle"} else dwg.draft.line_width
    )

    def unavailable():
        if max_components is not None:
            return _FIXED_INVENTORY_EXHAUSTED
        page = (0.0, 0.0, float(dwg.page_w), float(dwg.page_h))
        return (
            *components,
            _FixedInkComponent(
                f"{name}:geometry_unverified",
                box=page,
                owner=owner,
                kind=kind,
            ),
        )

    try:
        raw_segments = getattr(annotation, "segments", ()) or ()
        raw_polygons = getattr(annotation, "fixed_ink_polygons", ()) or ()
        raw_label = getattr(annotation, "label_bbox", None)
        global_axis = bool(getattr(annotation, "is_global_axis_centerline", False))
        if max_components is None:
            segment_prefix = tuple(raw_segments)
            raw_fixed_polygons = tuple(raw_polygons)
        else:
            segment_prefix = tuple(islice(iter(raw_segments), max_components + 1))
            if len(segment_prefix) > max_components:
                return _FIXED_INVENTORY_EXHAUSTED
            raw_fixed_polygons = tuple(islice(iter(raw_polygons), max_components + 1))
            if len(raw_fixed_polygons) > max_components:
                return _FIXED_INVENTORY_EXHAUSTED
    except Exception:  # noqa: BLE001 — unavailable fixed metadata is not exact ink
        return unavailable()
    segments = _coerce_segments(segment_prefix)
    if len(segments) != len(segment_prefix):
        return unavailable()
    if raw_label is not None and _coerce_box(raw_label) is None:
        return unavailable()

    def exhausted():
        return max_components is not None and len(components) > max_components

    for index, polygon in enumerate(raw_fixed_polygons):
        try:
            points = tuple((float(point[0]), float(point[1])) for point in polygon)
        except Exception:  # noqa: BLE001 — partial exact metadata must fail closed
            return unavailable()
        if len(points) < 3 or any(
            not math.isfinite(coordinate) for point in points for coordinate in point
        ):
            return unavailable()
        hull = _convex_hull(points)
        if len(hull) < 3:
            return unavailable()
        components.append(
            _FixedInkComponent(
                f"{name}:ink:{index}",
                polygons=(hull,),
                owner=owner,
                kind=kind,
            )
        )
        if exhausted():
            return _FIXED_INVENTORY_EXHAUSTED
    if type(annotation).__name__ == "Leader" and segments:
        components.append(
            _FixedInkComponent(
                f"{name}:segment:0",
                polygons=_leader_ink_polygons(
                    segments[0][0],
                    segments[0][1],
                    arrow_length=dwg.draft.arrow_length,
                    line_width=dwg.draft.line_width,
                ),
                owner=owner,
                kind=kind,
            )
        )
        if exhausted():
            return _FIXED_INVENTORY_EXHAUSTED
        segment_start = 1
    else:
        segment_start = 0
    for index, (first, second) in enumerate(segments[segment_start:], start=segment_start):
        polygon = _stroke_polygon(first, second, line_width)
        if polygon is not None:
            components.append(
                _FixedInkComponent(
                    f"{name}:segment:{index}",
                    polygons=(polygon,),
                    owner=owner,
                    kind=kind,
                    segment=(first, second),
                    global_axis=global_axis,
                )
            )
            if exhausted():
                return _FIXED_INVENTORY_EXHAUSTED
    label = _coerce_box(raw_label)
    if label is not None:
        components.append(_FixedInkComponent(f"{name}:label", box=label, owner=owner, kind=kind))
        if exhausted():
            return _FIXED_INVENTORY_EXHAUSTED
    remaining = None if max_components is None else max_components - len(components)
    residual = _rendered_residual_components(
        name,
        annotation,
        components,
        label,
        owner,
        kind,
        max_components=remaining,
        allow_empty_faces=bool(raw_fixed_polygons),
    )
    if residual is _FIXED_INVENTORY_EXHAUSTED:
        return _FIXED_INVENTORY_EXHAUSTED
    if residual is None:
        if max_components is not None:
            return _FIXED_INVENTORY_EXHAUSTED
        geometry = _coerce_box(_geom_box(annotation, getattr(dwg, "box_cache", None)))
        if geometry is None:
            return unavailable()
        else:
            components.append(
                _FixedInkComponent(
                    f"{name}:geometry",
                    box=geometry,
                    owner=owner,
                    kind=kind,
                )
            )
            if exhausted():
                return _FIXED_INVENTORY_EXHAUSTED
    else:
        components.extend(residual)
    if not components:
        geometry = _coerce_box(_geom_box(annotation, getattr(dwg, "box_cache", None)))
        if geometry is not None:
            components.append(
                _FixedInkComponent(f"{name}:geometry", box=geometry, owner=owner, kind=kind)
            )
            if exhausted():
                return _FIXED_INVENTORY_EXHAUSTED
    return tuple(components)


def _fixed_annotation_obstacles(dwg, view, *, provisional: bool = False):
    """Decomposed fixed ink with stable component-level trace identities.

    Strip occupancy deliberately pads witness boxes for lane carving.  That is
    not collision truth: #1166 needs actual line-width strokes, local arrow ink,
    rendered labels, and the exact component identity that rejected a leader.
    Leaders also avoid centre furniture; ``CROSSABLE_TYPES`` is a dimension-only
    exemption and must not leak into this inventory.
    """

    for name, annotation in dwg.iter_annotations():
        owner = dwg.view_of(name)
        if owner is not None and owner != view:
            continue
        if bool(getattr(annotation, "is_provisional_layout_reservation", False)) != provisional:
            continue
        yield from _annotation_fixed_ink(dwg, name, annotation)


def _legacy_fallback_obstacles(dwg, view):
    """Pre-shared-solve occupancy, excluding optional future furniture.

    The producer fallback preserves the old first-clear floor when a resource
    guard fires, but a provisional section reservation was never committed ink
    and cannot veto a required feature leader under that fallback.  View scope
    and the historical centre-furniture exemption otherwise match the legacy
    strip inventory exactly.
    """

    return tuple(
        box
        for name, box in strip_obstacles(
            dwg,
            view=view,
            crossable=CROSSABLE_TYPES,
            named=True,
        )
        if not getattr(
            dwg.get_annotation(name),
            "is_provisional_layout_reservation",
            False,
        )
    )


def feature_leader_fixed_conflicts(dwg, fixed_names) -> tuple[tuple[str, str], ...]:
    """Exact rendered-ink conflicts between landed Leaders and named fixed ink."""

    fixed = tuple(
        component
        for name in fixed_names
        if name in dwg.annotations()
        for component in _annotation_fixed_ink(dwg, name, dwg.get_annotation(name))
    )
    conflicts: list[tuple[str, str]] = []
    for name, annotation in dwg.iter_annotations():
        if type(annotation).__name__ != "Leader" or name in fixed_names:
            continue
        segments = _segments(annotation)
        if not segments:
            conflicts.append((name, f"{name}:geometry_unverified"))
            continue
        tip, elbow = segments[0]
        primary = _leader_ink_polygons(
            tip,
            elbow,
            arrow_length=dwg.draft.arrow_length,
            line_width=dwg.draft.line_width,
        )
        shelves = tuple(
            polygon
            for first, second in segments[1:]
            if (polygon := _stroke_polygon(first, second, dwg.draft.line_width)) is not None
        )
        axis_residual = _axis_residual_ink(tip, elbow, dwg.draft)
        label = _label_box(annotation)
        if label is None:
            conflicts.append((name, f"{name}:geometry_unverified"))
            continue
        candidate = _MeasuredLeaderCandidate(
            annotation,
            tip,
            elbow,
            dwg.registry.feature_of(name),
            0,
            0.0,
            label,
            segments,
            (*primary, *shelves),
            (*axis_residual, *shelves),
        )
        if not _rendered_ink_matches(candidate, annotation):
            conflicts.append((name, f"{name}:geometry_unverified"))
            continue
        conflicts.extend(
            (name, component.name)
            for component in fixed
            if _candidate_hits_component(candidate, component)
        )
    return tuple(conflicts)


def drain_feature_leaders(dwg, analysis, ctx) -> int:
    """Solve and emit the run's compatible feature leaders as one inventory."""

    pending = getattr(ctx, "feature_leaders", None)
    if pending is None:
        return 0
    jobs = list(pending)
    pending.clear()
    return place_feature_leader_jobs(dwg, analysis, ctx, jobs)


def place_feature_leader_jobs(dwg, analysis, ctx, jobs, *, producer_floor=False) -> int:
    """Solve explicit jobs now through the shared analytical leader machinery.

    Pre-drain semantic consumers use ``producer_floor=True`` to retain their
    established first-clear ordering without postponing their winners to the
    canonical late inventory.  Candidate measurement and survivor validation
    remain the same shared path; only the assignment tier is fixed to the lazy
    producer floor.
    """

    jobs = list(jobs)
    if not jobs:
        return 0

    trace = getattr(ctx, "trace", None)
    # ``feature_leader_inventory`` is the authoritative cross-pass drain event: consumers
    # and performance ratchets select it to measure the shared late solve. Immediate
    # pre-drain batches retain their noun event below, but must not impersonate that drain
    # merely because they now share its analytical implementation (#1308 release gate).
    shared_event = (
        trace.pass_event("feature_leader_inventory")
        if trace is not None and not producer_floor
        else None
    )
    noun_events = (
        {
            noun: trace.pass_event(f"{noun}_callouts")
            for noun in dict.fromkeys(job.noun for job in jobs)
        }
        if trace is not None
        else {}
    )
    page = (
        analysis.margin,
        analysis.margin,
        analysis.PAGE_W - analysis.margin,
        analysis.PAGE_H - analysis.margin,
    )
    title_block = (
        analysis.PAGE_W - analysis.TB_W - _TB_CLEAR,
        _TB_CLEAR,
        analysis.PAGE_W - _TB_CLEAR,
        _TB_CLEAR + _TB_H,
    )
    raw_jobs = []
    fallback_jobs = []
    candidate_budget_fallback_jobs = []
    measurement_work_by_view: dict[str, int] = {}
    for job in jobs:
        if job.fallback_candidates is None:
            joint, fallback = tee(job.candidates)
        else:
            joint, fallback = job.candidates, job.fallback_candidates
        if job.candidate_budget_fallback_candidates is None:
            fallback, candidate_budget_fallback = tee(fallback)
        else:
            candidate_budget_fallback = job.candidate_budget_fallback_candidates
        raw_jobs.append(iter(joint))
        fallback_jobs.append(iter(fallback))
        candidate_budget_fallback_jobs.append(iter(candidate_budget_fallback))

    views = tuple(dict.fromkeys(job.view for job in jobs))
    # The build's ONE filled-material lowering, indexed the way this stage needs it. Taken
    # from the drawing here rather than threaded through every producer's job, so a new
    # leader family joins the inventory without having to remember to carry the field —
    # and so there is exactly one lowering behind both routing and critique (#798).
    material_by_view: dict[str, Any] = {}
    try:
        fields = dwg.material_fields()
    except Exception:  # noqa: BLE001 — an unmeshable part routes on the other constraints
        fields = {}
    for view in views:
        placed = dwg.views.get(view)
        if placed and placed[0] is not None:
            material_by_view[view] = fields.get(id(placed[0]))
    inventory_unset = object()
    committed_inventory = inventory_unset
    provisional_inventory = inventory_unset

    def bounded_fixed_obstacles(*, provisional=False):
        """Lower fixed ink once, stopping before the component work cap.

        The candidate×component probe cap cannot protect an eager OCC scan that
        happens before it is computed.  Bound the component inventory itself,
        cache it across joint/fallback use, and let resource fallback mark exact
        classification unverified when the inventory is too large.
        """

        nonlocal committed_inventory, provisional_inventory
        cached = provisional_inventory if provisional else committed_inventory
        if cached is not inventory_unset:
            return cached
        remaining = _FEATURE_LEADER_MAX_FIXED_WORK
        components = []
        if not provisional:
            # The entire mandatory band is hard, including blank cells
            # between rendered title-block strokes and glyphs.
            if remaining < 1:
                cached = _FIXED_INVENTORY_EXHAUSTED
            else:
                components.append(_FixedInkComponent("title_block:reserved", box=title_block))
                remaining -= 1
        if cached is inventory_unset:
            for name, annotation in dwg.iter_annotations():
                if (
                    bool(getattr(annotation, "is_provisional_layout_reservation", False))
                    != provisional
                ):
                    continue
                if remaining <= 0:
                    cached = _FIXED_INVENTORY_EXHAUSTED
                    break
                annotation_components = _annotation_fixed_ink(
                    dwg,
                    name,
                    annotation,
                    max_components=remaining,
                )
                if annotation_components is _FIXED_INVENTORY_EXHAUSTED:
                    cached = _FIXED_INVENTORY_EXHAUSTED
                    break
                components.extend(annotation_components)
                remaining -= len(annotation_components)
            else:
                # View ownership is semantic provenance, not a page-space clipping
                # boundary.  A front-view witness line can physically cross a side-view
                # label in the gap between their projections, so every job must see the
                # same sheet-wide fixed-ink inventory.  Lower it once and share the tuple;
                # duplicating components per view would spend the work budget repeatedly.
                shared = tuple(components)
                cached = {view: shared for view in views}
        if provisional:
            provisional_inventory = cached
        else:
            committed_inventory = cached
        return cached

    def record_item(
        job_index,
        candidate,
        raw_count,
        blockers_by_raw,
        *,
        obstacle_count,
        viable_count=None,
        policy_b_blockers=(),
        candidate_inventory=(),
        producer_fallback=None,
        reason=None,
    ):
        job = jobs[job_index]
        item = {
            "name": job.name,
            "view": job.view,
            "label": job.label,
            "source_pass": job.noun,
            "priority": job.priority,
            "candidates_tried": raw_count,
            "obstacles": obstacle_count,
            "rejected": [
                {"candidate": raw_index, "blockers": list(blockers)}
                for raw_index, blockers in blockers_by_raw
                if blockers
            ],
            # The bounded inventory is part of the explanation contract, not
            # just its winner.  Every admitted alternative appears exactly
            # once with the geometry/objective data that decided its fate.
            "candidate_inventory": [dict(entry) for entry in candidate_inventory],
        }
        if producer_fallback is not None:
            item["producer_fallback"] = dict(producer_fallback)
        if viable_count is not None:
            item["viable_candidates"] = viable_count
        if policy_b_blockers:
            item["policy_b_blockers"] = list(policy_b_blockers)
        if candidate is None:
            item.update({"outcome": "dropped", "reason": reason or "no_clear_room"})
        else:
            item.update(
                {
                    "outcome": "placed",
                    "candidate": candidate.raw_index,
                    "tip": list(candidate.tip),
                    "elbow": list(candidate.elbow),
                    "cost": candidate.cost,
                }
            )
        if shared_event is not None:
            shared_event["items"].append(dict(item))
        event = noun_events.get(job.noun)
        if event is not None:
            event["items"].append(dict(item))

    def place(job_index, candidate, annotation):
        job = jobs[job_index]
        # Preserve typed candidate provenance on the rendered object.  Besides trace
        # diagnostics, structural lint uses this to distinguish a solver-proven interior
        # label from an arbitrary annotation that merely happens to lie inside a view.
        annotation._dw_candidate_region = candidate.region.value
        ctx.place(
            annotation,
            job.name,
            view=job.view,
            feature=resolve_feature(candidate.feature),
            measurement=job.measurement,
        )
        if job.on_place is not None:
            job.on_place(annotation)

    def drop(job_index, *, reason="no_clear_room"):
        job = jobs[job_index]
        if job.on_drop is not None:
            job.on_drop(reason)
        else:
            detail = (
                "rendered geometry validation failed"
                if reason == "geometry_validation"
                else "no clear room"
            )
            ctx.record_issue(
                "warning",
                job.drop_code,
                f"{job.noun} callout {job.label} not placed ({detail})",
                measurement=job.measurement,
                outcome_stage=("validation" if reason == "geometry_validation" else "placement"),
            )

    def record_policy_b(job_index, blockers) -> None:
        """Persist an intentionally retained fixed-ink crossing.

        Solve tracing is optional; Policy B is not.  A normal drawing must
        therefore expose the accepted crossing through structured lint rather
        than looking clean merely because the trace recorder was disabled.
        """

        if producer_floor:
            # Immediate pre-drain consumers retain their historical diagnostic
            # contract as well as their selection order. Their later semantic
            # passes already diagnose the resulting drawing; this late-inventory
            # Policy-B finding was never part of the immediate producer floor.
            return

        unverified = "fixed_probe_budget" in blockers
        crossed = tuple(
            blocker
            for blocker in blockers
            if blocker not in {"page", "unmeasurable_label", "fixed_probe_budget"}
            and not blocker.startswith("view:")
        )
        job = jobs[job_index]
        if crossed:
            ctx.record_issue(
                "info",
                "feature_leader_crossing",
                f"{job.noun} callout {job.label} retained under Policy B across: "
                + ", ".join(crossed),
                measurement=job.measurement,
            )
        if unverified:
            ctx.record_issue(
                "info",
                "feature_leader_fixed_ink_unverified",
                f"{job.noun} callout {job.label} retained under the producer floor "
                "without exact fixed-ink classification (probe budget exhausted)",
                measurement=job.measurement,
            )

    def candidate_entry(candidate, status, blockers=(), assignment_blockers=()):
        entry = {
            "candidate": candidate.raw_index,
            "region": candidate.region.value,
            "tip": list(candidate.tip),
            "elbow": list(candidate.elbow),
            "cost": candidate.cost,
            "fixed_blockers": list(blockers),
            "outcome": status,
        }
        if assignment_blockers:
            entry["assignment_blockers"] = list(assignment_blockers)
        return entry

    def set_assignment(
        value,
        *,
        optimal,
        states=0,
        fixed_probes=0,
        fixed_probe_bound=0,
        pair_probes=0,
        placed=0,
        priority=0.0,
        penalty=0,
        cost=0.0,
        provisional_refinement="not_attempted",
        provisional_penalty=0,
    ):
        if "budget" in value or "budget" in provisional_refinement:
            activity("budget", reason=value, refinement=provisional_refinement, states=states)
        for event in [shared_event, *noun_events.values()]:
            if event is not None:
                event.update(
                    {
                        "assignment": value,
                        "optimal": optimal,
                        "states": states,
                        "fixed_probes": fixed_probes,
                        "fixed_probe_bound": fixed_probe_bound,
                        "fixed_work_limit": _FEATURE_LEADER_MAX_FIXED_WORK,
                        "pair_probes": pair_probes,
                        "joint_measurement_work": sum(measurement_work_by_view.values()),
                        "joint_measurement_work_by_view": dict(measurement_work_by_view),
                        "joint_measurement_work_limit_per_view": (
                            _FEATURE_LEADER_MAX_MEASURE_WORK
                        ),
                        "provisional_refinement": provisional_refinement,
                        "inventory_jobs": len(jobs),
                        "objective": {
                            "placed": placed,
                            "priority": priority,
                            "penalty": penalty,
                            "provisional_penalty": provisional_penalty,
                            "cost": cost,
                        },
                    }
                )

    def greedy(
        reason,
        *,
        fixed_probes=0,
        fixed_probe_bound=0,
        pair_probes=0,
        states=0,
        abandoned_inventories=None,
        abandoned_rejected=None,
        abandoned_raw_counts=None,
        prefer_clear=True,
    ) -> int:
        """Deterministic first-clear floor in original stage/job order.

        With *prefer_clear* a job whose first acceptable route cuts back through the part
        looks a bounded distance further for one that does not (#798). Every other job
        behaves exactly as the pre-#798 floor did, because a first acceptable route that
        already clears the body breaks the loop in the same place, and a job that
        exhausts the lookahead resumes the stream in first-clear order rather than
        dropping.

        ``prefer_clear=False`` restores the original selection verbatim. It is used by the
        geometry-validation replay, whose whole purpose is to guarantee cardinality after
        the exact path lost candidates to rendering failures: re-running that with a route
        preference would search for a better answer at the exact moment the caller needs
        the most certain one.
        """

        if "budget" in reason:
            activity(
                "budget",
                reason=reason,
                states=states,
                fixed_probes=fixed_probes,
                pair_probes=pair_probes,
            )
        placed_count = 0
        total_priority = 0.0
        total_penalty = 0
        total_cost = 0.0
        actual_fixed_probes = fixed_probes
        fixed_result = bounded_fixed_obstacles()
        fixed_verified = fixed_result is not _FIXED_INVENTORY_EXHAUSTED
        fixed = fixed_result if fixed_verified else {view: () for view in views}
        legacy_boxes = {
            # Producer fallback replays the pre-#1166 acceptance floor; exact
            # blockers below still persist any retained crossing.  Optional
            # future section furniture cannot become a resource-cap veto.
            view: _legacy_fallback_obstacles(dwg, view)
            for view in dict.fromkeys(job.view for job in jobs)
        }

        def boundary_blockers(candidate, job):
            blockers = []
            label = candidate.label_box
            if candidate.failure_reason is not None:
                blockers.append(candidate.failure_reason)
            elif label is None:
                blockers.append("unmeasurable_label")
            else:
                if (
                    label[0] < page[0]
                    or label[1] < page[1]
                    or label[2] > page[2]
                    or label[3] > page[3]
                ):
                    blockers.append("page")
                if view_blocker := _view_region_blocker(candidate, job):
                    blockers.append(view_blocker)
            if _ink_hits_box(candidate, title_block):
                blockers.append("title_block:reserved")
            return tuple(blockers)

        def terminal_reason(rejected):
            return (
                "geometry_validation"
                if rejected
                and all(
                    "geometry_validation" in entry["blockers"]
                    and set(entry["blockers"])
                    <= {
                        "geometry_validation",
                        "fixed_probe_budget",
                    }
                    for entry in rejected
                )
                else "no_clear_room"
            )

        for job_index, job in enumerate(jobs):
            obstacle_count = len(fixed[job.view])
            blockers_by_raw = []
            fallback_rejected = []
            raw_count = 0
            selected = None
            selected_policy_b: tuple[str, ...] = ()
            annotation = None
            inventory = []
            field = material_by_view.get(job.view)
            # Accepted-but-cutting alternatives, held back while a bounded lookahead
            # searches for one that does not cut. Empty in the common case: the first
            # acceptable route usually clears the body, and then this loop breaks exactly
            # where the pre-#798 one did.
            held: list[tuple[int, int, int, Any, tuple[str, ...]]] = []
            examined_since_accept = None
            fallback_source = (
                candidate_budget_fallback_jobs[job_index]
                if reason == "greedy_candidate_budget"
                else fallback_jobs[job_index]
            )
            source = (
                _measure(raw_index, raw, job, dwg.draft)
                for raw_index, raw in enumerate(fallback_source)
            )
            for candidate in source:
                raw_count = max(raw_count, candidate.raw_index + 1)
                if examined_since_accept is not None:
                    examined_since_accept += 1
                    if examined_since_accept > _GREEDY_MATERIAL_LOOKAHEAD:
                        break
                fixed_components = fixed[job.view]
                if fixed_verified and (
                    actual_fixed_probes + len(fixed_components) <= _FEATURE_LEADER_MAX_FIXED_WORK
                ):
                    blockers = _fixed_blockers(candidate, job, page, fixed_components)
                    actual_fixed_probes += len(fixed_components)
                else:
                    fixed_verified = False
                    # Replay preserves the producer floor when exact
                    # classification exceeds its work budget. Boundary/title
                    # constraints remain hard and the uncertainty is explicit.
                    blockers = (*boundary_blockers(candidate, job), "fixed_probe_budget")
                    if candidate.region is LeaderCandidateRegion.INTERIOR:
                        blockers = (
                            *blockers,
                            f"view:{job.view}:interior_annotation_ink_unverified",
                        )
                hard_blockers = _hard_fixed_blockers(blockers)
                accepted = not hard_blockers and (
                    job.fallback_accept(candidate, legacy_boxes[job.view], page)
                    if job.fallback_accept is not None
                    else not tuple(
                        blocker for blocker in blockers if blocker != "fixed_probe_budget"
                    )
                )
                if not accepted:
                    blockers_by_raw.append((candidate.raw_index, blockers))
                    fallback_rejected.append(
                        {
                            "candidate": candidate.raw_index,
                            "blockers": list(blockers or ("legacy_occupancy",)),
                        }
                    )
                    inventory.append(candidate_entry(candidate, "fixed_rejected", blockers))
                    continue
                units = _material_units(candidate, field) if prefer_clear else 0
                soft = (
                    tuple(blocker for blocker in blockers if blocker != "fixed_probe_budget")
                    if job.allow_policy_b_fixed
                    and reason in {"greedy_fixed_probe_budget", "greedy_pair_budget"}
                    else ()
                )
                if units or soft:
                    # Keep a feasible Policy-B route while looking a bounded
                    # distance for one that clears both the body and fixed ink.
                    # Retain the least-conflicting route if none clears; a routing
                    # preference must never drop a required callout.
                    held.append((len(soft), units, candidate.raw_index, candidate, blockers))
                    if examined_since_accept is None:
                        examined_since_accept = 0
                    continue
                annotation = _materialize(dwg, job, candidate)
                if annotation is None:
                    blockers_by_raw.append((candidate.raw_index, ("geometry_validation",)))
                    inventory.append(
                        candidate_entry(candidate, "geometry_validation", ("geometry_validation",))
                    )
                    fallback_rejected.append(
                        {
                            "candidate": candidate.raw_index,
                            "blockers": ["geometry_validation"],
                        }
                    )
                    continue
                selected = candidate
                selected_policy_b = blockers
                inventory.append(candidate_entry(candidate, "selected", blockers))
                break
            if selected is None:
                # No clear route inside the lookahead. Keep the least-conflicting
                # feasible candidate; original order breaks equal-cost ties.
                for _soft, _units, _raw_index, candidate, blockers in sorted(
                    held, key=lambda h: h[:3]
                ):
                    annotation = _materialize(dwg, job, candidate)
                    if annotation is None:
                        blockers_by_raw.append((candidate.raw_index, ("geometry_validation",)))
                        inventory.append(
                            candidate_entry(
                                candidate, "geometry_validation", ("geometry_validation",)
                            )
                        )
                        fallback_rejected.append(
                            {
                                "candidate": candidate.raw_index,
                                "blockers": ["geometry_validation"],
                            }
                        )
                        continue
                    selected = candidate
                    selected_policy_b = blockers
                    inventory.append(candidate_entry(candidate, "selected", blockers))
                    break
            if selected is None:
                # Nothing clear inside the lookahead, and every held candidate failed to
                # render. RESUME the producer stream in pure first-clear order.
                #
                # Without this the lookahead is not a preference but a truncation: the
                # pre-#798 loop scanned the whole stream, so a job whose early candidates
                # all cut AND all fail geometry validation would be dropped here purely
                # because it was searched for a better route. That is the one way a
                # callout could be lost for a routing reason, which Policy B forbids and
                # which the rest of this design is built to prevent.
                for candidate in source:
                    raw_count = max(raw_count, candidate.raw_index + 1)
                    fixed_components = fixed[job.view]
                    if fixed_verified and (
                        actual_fixed_probes + len(fixed_components)
                        <= _FEATURE_LEADER_MAX_FIXED_WORK
                    ):
                        blockers = _fixed_blockers(candidate, job, page, fixed_components)
                        actual_fixed_probes += len(fixed_components)
                    else:
                        fixed_verified = False
                        blockers = (*boundary_blockers(candidate, job), "fixed_probe_budget")
                    if _hard_fixed_blockers(blockers) or not (
                        job.fallback_accept(candidate, legacy_boxes[job.view], page)
                        if job.fallback_accept is not None
                        else not tuple(
                            blocker for blocker in blockers if blocker != "fixed_probe_budget"
                        )
                    ):
                        blockers_by_raw.append((candidate.raw_index, blockers))
                        fallback_rejected.append(
                            {
                                "candidate": candidate.raw_index,
                                "blockers": list(blockers or ("legacy_occupancy",)),
                            }
                        )
                        inventory.append(candidate_entry(candidate, "fixed_rejected", blockers))
                        continue
                    annotation = _materialize(dwg, job, candidate)
                    if annotation is None:
                        blockers_by_raw.append((candidate.raw_index, ("geometry_validation",)))
                        inventory.append(
                            candidate_entry(
                                candidate, "geometry_validation", ("geometry_validation",)
                            )
                        )
                        fallback_rejected.append(
                            {
                                "candidate": candidate.raw_index,
                                "blockers": ["geometry_validation"],
                            }
                        )
                        continue
                    selected = candidate
                    selected_policy_b = blockers
                    inventory.append(candidate_entry(candidate, "selected", blockers))
                    break
            producer_fallback = {
                "candidates_tried": raw_count,
                "selected": (
                    candidate_entry(selected, "selected", selected_policy_b)
                    if selected is not None
                    else None
                ),
                "rejected": fallback_rejected,
            }
            recorded_inventory = (
                abandoned_inventories[job_index]
                if abandoned_inventories is not None
                else inventory
            )
            recorded_rejected = (
                abandoned_rejected[job_index]
                if abandoned_rejected is not None
                else blockers_by_raw
            )
            recorded_raw_count = (
                abandoned_raw_counts[job_index] if abandoned_raw_counts is not None else raw_count
            )
            if selected is None:
                drop_reason = terminal_reason(fallback_rejected)
                drop(job_index, reason=drop_reason)
                record_item(
                    job_index,
                    None,
                    recorded_raw_count,
                    recorded_rejected,
                    obstacle_count=obstacle_count,
                    candidate_inventory=recorded_inventory,
                    producer_fallback=producer_fallback,
                    reason=drop_reason,
                )
                continue
            place(job_index, selected, annotation)
            record_policy_b(job_index, selected_policy_b)
            if fixed_verified:
                remaining_components = _FEATURE_LEADER_MAX_FIXED_WORK - sum(
                    len(components) for components in fixed.values()
                )
                if remaining_components <= 0:
                    fixed_verified = False
                else:
                    landed_components = _annotation_fixed_ink(
                        dwg,
                        job.name,
                        annotation,
                        max_components=remaining_components,
                    )
                    if landed_components is _FIXED_INVENTORY_EXHAUSTED:
                        fixed_verified = False
                    else:
                        fixed[job.view] = (*fixed[job.view], *landed_components)
            legacy_boxes[job.view] = (
                *legacy_boxes[job.view],
                *annotation_obstacle_boxes(dwg, annotation),
            )
            record_item(
                job_index,
                selected,
                recorded_raw_count,
                recorded_rejected,
                obstacle_count=obstacle_count,
                policy_b_blockers=selected_policy_b,
                candidate_inventory=recorded_inventory,
                producer_fallback=producer_fallback,
            )
            placed_count += 1
            total_priority += job.priority
            # The resource-cap floor replays the producer's own lazy selection, which does
            # not weigh material — its contract is only that it cannot place FEWER
            # callouts than the pre-#1166 renderer. Its reported penalty still counts the
            # material it accepted, so a fallback result is not traced as cleaner than it is.
            total_penalty += len(selected_policy_b) + _material_units(
                selected, material_by_view.get(job.view)
            )
            total_cost += selected.cost
        set_assignment(
            reason,
            optimal=False,
            states=states,
            fixed_probes=actual_fixed_probes,
            fixed_probe_bound=max(fixed_probe_bound, actual_fixed_probes),
            pair_probes=pair_probes,
            placed=placed_count,
            priority=total_priority,
            penalty=total_penalty,
            cost=total_cost,
        )
        return placed_count

    if producer_floor:
        return greedy("greedy_stage_boundary")

    if len(jobs) > _LEADER_ASSIGN_MAX_JOBS:
        return greedy("greedy_job_budget")

    # Budgets are per VIEW, because the solve is (#1188). Jobs in different views never
    # conflict, so they are separate searches sharing nothing; charging them against one
    # global allowance made a three-view part exhaust the budget at a third of the
    # inventory each view could actually handle, and every dense fixture fell back to the
    # greedy floor before the exact solve began.
    for job_index, iterator in enumerate(raw_jobs):
        view = jobs[job_index].view
        unit_work = _candidate_measure_work(jobs[job_index])
        used_work = measurement_work_by_view.get(view, 0)
        remaining_work = max(0, _FEATURE_LEADER_MAX_MEASURE_WORK - used_work)
        admitted = remaining_work // unit_work
        prefix = list(islice(iterator, admitted + 1))
        measurement_work_by_view[view] = used_work + len(prefix) * unit_work
        if len(prefix) > admitted:
            raw_jobs[job_index] = chain(prefix, iterator)
            return greedy("greedy_candidate_budget")
        raw_jobs[job_index] = iter(prefix)

    measured_by_job = [
        [_measure(raw_index, raw, job, dwg.draft) for raw_index, raw in enumerate(iterator)]
        for job, iterator in zip(jobs, raw_jobs, strict=True)
    ]
    raw_count_by_job = [len(candidates) for candidates in measured_by_job]

    fixed = bounded_fixed_obstacles()
    if fixed is _FIXED_INVENTORY_EXHAUSTED:
        return greedy(
            "greedy_fixed_inventory_budget",
            fixed_probe_bound=_FEATURE_LEADER_MAX_FIXED_WORK + 1,
        )
    possible_fixed_by_job = [
        [_possible_fixed_components(candidate, fixed[job.view]) for candidate in candidates]
        for job, candidates in zip(jobs, measured_by_job, strict=True)
    ]
    probes_by_view: dict[str, int] = {}
    for job, possible_by_candidate in zip(jobs, possible_fixed_by_job, strict=True):
        probes_by_view[job.view] = probes_by_view.get(job.view, 0) + sum(
            len(components) for components in possible_by_candidate
        )
    fixed_probe_bound = sum(probes_by_view.values())
    if any(bound > _FEATURE_LEADER_MAX_FIXED_WORK for bound in probes_by_view.values()):
        return greedy(
            "greedy_fixed_probe_budget",
            fixed_probe_bound=fixed_probe_bound,
        )
    viable_by_job = []
    policy_blockers_by_job = []
    material_by_job = []
    rejected_by_job = []
    for job, measured, possible_by_candidate in zip(
        jobs, measured_by_job, possible_fixed_by_job, strict=True
    ):
        viable = []
        policy_blockers = []
        material_units = []
        rejected = []
        field = material_by_view.get(job.view)
        for candidate, possible_components in zip(measured, possible_by_candidate, strict=True):
            blockers = _fixed_blockers(candidate, job, page, possible_components)
            hard_blocked = bool(_hard_fixed_blockers(blockers))
            if blockers and (hard_blocked or not job.allow_policy_b_fixed):
                rejected.append((candidate.raw_index, blockers))
                continue
            viable.append(candidate)
            policy_blockers.append(blockers)
            # Cutting the body is a Policy-B cost, never an eligibility gate: a nested
            # feature can have no clear route at all, and dropping its callout to keep the
            # outline tidy would trade a required measurement for a cosmetic one.
            material_units.append(_material_units(candidate, field))
        viable_by_job.append(viable)
        policy_blockers_by_job.append(policy_blockers)
        material_by_job.append(material_units)
        rejected_by_job.append(rejected)

    component_bounds_by_job = [
        [
            tuple(_polygon_bounds(polygon) for polygon in candidate.ink_polygons)
            for candidate in candidates
        ]
        for candidates in viable_by_job
    ]
    bounds_by_job = [
        [_candidate_bounds(candidate) for candidate in candidates] for candidates in viable_by_job
    ]
    conflicts = []
    pair_probes = 0
    for later_job, later_candidates in enumerate(viable_by_job):
        for earlier_job in range(later_job):
            if jobs[earlier_job].view != jobs[later_job].view:
                continue
            for earlier_index, earlier in enumerate(viable_by_job[earlier_job]):
                for later_index, later in enumerate(later_candidates):
                    earlier_bounds = bounds_by_job[earlier_job][earlier_index]
                    later_bounds = bounds_by_job[later_job][later_index]
                    if (
                        earlier_bounds is None
                        or later_bounds is None
                        or not _boxes_overlap(earlier_bounds, later_bounds)
                    ):
                        continue
                    pair_probes += 1
                    if pair_probes > _FEATURE_LEADER_MAX_PAIR_PROBES:
                        return greedy(
                            "greedy_pair_budget",
                            fixed_probes=fixed_probe_bound,
                            fixed_probe_bound=fixed_probe_bound,
                            pair_probes=pair_probes,
                        )
                    if _candidate_conflict(
                        earlier,
                        later,
                        left_ink_bounds=component_bounds_by_job[earlier_job][earlier_index],
                        right_ink_bounds=component_bounds_by_job[later_job][later_index],
                    ):
                        conflicts.append((earlier_job, earlier_index, later_job, later_index))

    assignment = _assign_by_view(
        [job.view for job in jobs],
        [[candidate.cost for candidate in candidates] for candidates in viable_by_job],
        conflicts,
        priorities=[job.priority for job in jobs],
        penalties_by_job=[
            [
                len(blockers) + units
                for blockers, units in zip(job_blockers, job_units, strict=True)
            ]
            for job_blockers, job_units in zip(
                policy_blockers_by_job, material_by_job, strict=True
            )
        ],
    )

    # Override the established producer layout only for a proven cardinality
    # improvement. A complete incumbent beats any floor with an empty job stream;
    # otherwise the floor may place every job too, with different downstream
    # section/table opportunities. Peek at most one raw candidate per job and
    # restore each nonempty stream for the ordinary fallback/validation paths.
    retain_complete_incumbent = False
    if not assignment.optimal and all(choice is not None for choice in assignment.choices):
        empty = object()
        for job_index, fallback in enumerate(fallback_jobs):
            first = next(fallback, empty)
            if first is empty:
                retain_complete_incumbent = True
                break
            fallback_jobs[job_index] = chain((first,), fallback)

    if not assignment.optimal and not retain_complete_incumbent:
        # The layout solver's bounded-search incumbent is seeded from the new
        # exact-ink candidate order, not from every producer's canonical
        # pre-#1166 lazy fallback.  Replaying that producer floor is the only
        # general guarantee that resource pressure cannot reduce semantic
        # cardinality relative to the established renderer.
        conflict_names_by_candidate: dict[tuple[int, int], set[str]] = {}
        for earlier_job, earlier_index, later_job, later_index in conflicts:
            conflict_names_by_candidate.setdefault((earlier_job, earlier_index), set()).add(
                jobs[later_job].name
            )
            conflict_names_by_candidate.setdefault((later_job, later_index), set()).add(
                jobs[earlier_job].name
            )

        abandoned_inventories = []
        for job_index, measured in enumerate(measured_by_job):
            rejected_lookup = dict(rejected_by_job[job_index])
            viable_index = {
                candidate.raw_index: index
                for index, candidate in enumerate(viable_by_job[job_index])
            }
            inventory = []
            for candidate in measured:
                conflict_names: tuple[str, ...]
                if candidate.raw_index in rejected_lookup:
                    status = "fixed_rejected"
                    blockers = rejected_lookup[candidate.raw_index]
                    conflict_names = ()
                else:
                    status = "joint_abandoned"
                    candidate_index = viable_index[candidate.raw_index]
                    blockers = policy_blockers_by_job[job_index][candidate_index]
                    conflict_names = tuple(
                        sorted(conflict_names_by_candidate.get((job_index, candidate_index), ()))
                    )
                inventory.append(candidate_entry(candidate, status, blockers, conflict_names))
            abandoned_inventories.append(inventory)
        return greedy(
            "greedy_state_budget",
            fixed_probes=fixed_probe_bound,
            fixed_probe_bound=fixed_probe_bound,
            pair_probes=pair_probes,
            states=assignment.states,
            abandoned_inventories=abandoned_inventories,
            abandoned_rejected=rejected_by_job,
            abandoned_raw_counts=raw_count_by_job,
        )
    assignment_states = assignment.states

    # Optional section furniture must never veto a required feature leader.
    # Once the primary committed-ink assignment is proven optimal, however, a
    # second bounded solve may prefer an equally complete/important result that
    # leaves the provisional section row clear.  Encode the established fixed
    # penalty as the major component so the refinement cannot trade a real
    # dimension/witness crossing for future optional furniture.  If either the
    # probe or exact-search budget is exhausted, retain the primary result.
    provisional = (
        bounded_fixed_obstacles(provisional=True)
        if assignment.optimal
        else {view: () for view in views}
    )
    provisional_inventory_exhausted = provisional is _FIXED_INVENTORY_EXHAUSTED
    provisional_probes_by_view: dict[str, int] = {}
    if not provisional_inventory_exhausted:
        for job, candidates in zip(jobs, viable_by_job, strict=True):
            provisional_probes_by_view[job.view] = provisional_probes_by_view.get(
                job.view, 0
            ) + len(candidates) * len(provisional[job.view])
    provisional_probe_bound = (
        _FEATURE_LEADER_MAX_FIXED_WORK + 1
        if provisional_inventory_exhausted
        else sum(provisional_probes_by_view.values())
    )
    provisional_refinement = "not_needed" if assignment.optimal else "primary_state_budget"
    provisional_blockers_by_job: list[list[tuple[str, ...]]] = [
        [() for _candidate in candidates] for candidates in viable_by_job
    ]
    # Per view, matching the primary gate. Summing across views would compare three
    # independent searches' work against one search's budget, so a dense part could clear
    # the primary gate and then never attempt the section refinement at all.
    if (
        not provisional_inventory_exhausted
        and provisional_probe_bound
        and all(
            probes_by_view.get(view, 0) + provisional_probes_by_view.get(view, 0)
            <= _FEATURE_LEADER_MAX_FIXED_WORK
            for view in {*probes_by_view, *provisional_probes_by_view}
        )
    ):
        provisional_blockers_by_job = [
            [
                tuple(
                    component.name
                    for component in provisional[job.view]
                    if _candidate_hits_component(candidate, component)
                )
                for candidate in candidates
            ]
            for job, candidates in zip(jobs, viable_by_job, strict=True)
        ]
        max_provisional_penalty = 1 + sum(
            max((len(blockers) for blockers in job_blockers), default=0)
            for job_blockers in provisional_blockers_by_job
        )
        refined = _assign_by_view(
            [job.view for job in jobs],
            [[candidate.cost for candidate in candidates] for candidates in viable_by_job],
            conflicts,
            priorities=[job.priority for job in jobs],
            penalties_by_job=[
                [
                    (len(fixed_blockers) + units) * max_provisional_penalty
                    + len(provisional_blockers)
                    for fixed_blockers, provisional_blockers, units in zip(
                        fixed_job_blockers,
                        provisional_job_blockers,
                        material_job_units,
                        strict=True,
                    )
                ]
                # Material joins the COMMITTED major component, beside the fixed-ink
                # blockers: a cut through the part is a real defect on the finished sheet,
                # so the refinement must not be able to buy a clear section row with one.
                for fixed_job_blockers, provisional_job_blockers, material_job_units in zip(
                    policy_blockers_by_job,
                    provisional_blockers_by_job,
                    material_by_job,
                    strict=True,
                )
            ],
        )
        if refined.optimal:
            assignment = refined
            assignment_states += refined.states
            provisional_refinement = "selected"
        else:
            assignment_states += refined.states
            provisional_refinement = "state_budget_retained_primary"
    elif provisional_probe_bound:
        provisional_refinement = "probe_budget_retained_primary"
    chosen = {
        (job_index, choice)
        for job_index, choice in enumerate(assignment.choices)
        if choice is not None
    }
    conflict_set = set(conflicts)

    def selected_conflict_names(job_index, candidate_index):
        names = []
        for other_job, other_choice in chosen:
            if other_job == job_index:
                continue
            key = (
                (job_index, candidate_index, other_job, other_choice)
                if job_index < other_job
                else (other_job, other_choice, job_index, candidate_index)
            )
            if key in conflict_set:
                names.append(jobs[other_job].name)
        return tuple(sorted(names))

    assignment_blockers = []
    for job_index, candidates in enumerate(viable_by_job):
        selected_index = assignment.choices[job_index]
        assignment_blockers.append(
            [
                (candidate.raw_index, selected_conflict_names(job_index, candidate_index))
                for candidate_index, candidate in enumerate(candidates)
                if candidate_index != selected_index
                and selected_conflict_names(job_index, candidate_index)
            ]
        )

    def joint_inventory(job_index, geometry_failures=(), *, abandoned=False):
        rejected = dict(rejected_by_job[job_index])
        viable_index = {
            candidate.raw_index: index for index, candidate in enumerate(viable_by_job[job_index])
        }
        choice = assignment.choices[job_index]
        selected_raw = viable_by_job[job_index][choice].raw_index if choice is not None else None
        entries = []
        for candidate in measured_by_job[job_index]:
            if candidate.raw_index in rejected:
                entries.append(
                    candidate_entry(
                        candidate,
                        "fixed_rejected",
                        rejected[candidate.raw_index],
                    )
                )
                continue
            index = viable_index[candidate.raw_index]
            fixed_blockers = policy_blockers_by_job[job_index][index]
            conflicts_with = selected_conflict_names(job_index, index)
            if candidate.raw_index == selected_raw:
                status = (
                    "geometry_validation"
                    if job_index in geometry_failures
                    else "joint_abandoned"
                    if abandoned
                    else "selected"
                )
            elif conflicts_with:
                status = "conflict_rejected"
            else:
                status = "objective_rejected"
            entries.append(candidate_entry(candidate, status, fixed_blockers, conflicts_with))
            if provisional_blockers_by_job[job_index][index]:
                entries[-1]["provisional_blockers"] = list(
                    provisional_blockers_by_job[job_index][index]
                )
        return entries

    materialized = {}
    geometry_failures = set()
    for job_index, choice in enumerate(assignment.choices):
        if choice is None:
            continue
        annotation = _materialize(dwg, jobs[job_index], viable_by_job[job_index][choice])
        if annotation is None:
            geometry_failures.add(job_index)
        else:
            materialized[job_index] = annotation

    if geometry_failures:
        # Rendered-OCC validation is deliberately outside the numeric search,
        # but failure cannot silently reduce the solver's primary cardinality.
        # Replay the canonical lazy producer floor: it validates candidates in
        # order and continues after a bad survivor, remaining bounded by the
        # original streams and preserving the pre-shared-stage semantic floor.
        total_fixed_probes = fixed_probe_bound + (
            provisional_probe_bound
            if provisional_refinement not in {"not_needed", "probe_budget_retained_primary"}
            else 0
        )
        return greedy(
            "greedy_geometry_validation",
            # A pure legacy replay: this exists to guarantee cardinality after the exact
            # path lost candidates to rendering failures, so it must not spend its search
            # looking for a tidier route.
            prefer_clear=False,
            fixed_probes=total_fixed_probes,
            fixed_probe_bound=total_fixed_probes,
            pair_probes=pair_probes,
            states=assignment_states,
            abandoned_inventories=[
                joint_inventory(job_index, geometry_failures, abandoned=True)
                for job_index in range(len(jobs))
            ],
            abandoned_rejected=rejected_by_job,
            abandoned_raw_counts=raw_count_by_job,
        )

    final_choices = list(assignment.choices)

    objective_cost = sum(
        viable_by_job[job_index][choice].cost
        for job_index, choice in enumerate(final_choices)
        if choice is not None
    )
    objective_priority = sum(
        jobs[job_index].priority
        for job_index, choice in enumerate(final_choices)
        if choice is not None
    )
    # Includes the material units, because the solve minimised them: reporting only the
    # fixed-ink blockers would put this trace on a different scale from the greedy floor's
    # (which adds them explicitly), in the direction that makes the joint result look
    # cleaner than it is.
    objective_penalty = sum(
        len(policy_blockers_by_job[job_index][choice]) + material_by_job[job_index][choice]
        for job_index, choice in enumerate(final_choices)
        if choice is not None
    )
    objective_provisional_penalty = sum(
        len(provisional_blockers_by_job[job_index][choice])
        for job_index, choice in enumerate(final_choices)
        if choice is not None
    )

    total_fixed_probes = fixed_probe_bound + (
        provisional_probe_bound
        if provisional_refinement not in {"not_needed", "probe_budget_retained_primary"}
        else 0
    )
    set_assignment(
        "joint" if assignment.optimal else "joint_state_budget",
        optimal=assignment.optimal,
        states=assignment_states,
        fixed_probes=total_fixed_probes,
        fixed_probe_bound=total_fixed_probes,
        pair_probes=pair_probes,
        placed=sum(choice is not None for choice in final_choices),
        priority=objective_priority,
        penalty=objective_penalty,
        provisional_penalty=objective_provisional_penalty,
        cost=objective_cost,
        provisional_refinement=provisional_refinement,
    )
    placed_count = 0
    for job_index, choice in enumerate(final_choices):
        if choice is None:
            reason = (
                "geometry_validation"
                if rejected_by_job[job_index]
                and all(
                    blockers == ("geometry_validation",)
                    for _raw_index, blockers in rejected_by_job[job_index]
                )
                else "assignment_conflict"
                if viable_by_job[job_index]
                else "no_clear_room"
            )
            drop(job_index, reason=reason)
            record_item(
                job_index,
                None,
                raw_count_by_job[job_index],
                [*rejected_by_job[job_index], *assignment_blockers[job_index]],
                obstacle_count=len(fixed[jobs[job_index].view]),
                viable_count=len(viable_by_job[job_index]),
                candidate_inventory=joint_inventory(job_index),
                reason=reason,
            )
            continue
        candidate = viable_by_job[job_index][choice]
        place(job_index, candidate, materialized[job_index])
        record_policy_b(job_index, policy_blockers_by_job[job_index][choice])
        record_item(
            job_index,
            candidate,
            raw_count_by_job[job_index],
            [*rejected_by_job[job_index], *assignment_blockers[job_index]],
            obstacle_count=len(fixed[jobs[job_index].view]),
            viable_count=len(viable_by_job[job_index]),
            policy_b_blockers=policy_blockers_by_job[job_index][choice],
            candidate_inventory=joint_inventory(job_index),
        )
        placed_count += 1
    return placed_count
