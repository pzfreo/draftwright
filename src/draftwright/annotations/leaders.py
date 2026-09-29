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
from itertools import chain, islice, tee
from typing import Any

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
from draftwright.annotation_layout_profile import layout_flag
from draftwright.annotations._common import (
    CROSSABLE_TYPES,
    _geom_box,
    annotation_obstacle_boxes,
    strip_obstacles,
)
from draftwright.annotations._leader_candidates import (
    FeatureLeaderCandidate,
    LeaderCandidateRegion,
    _box_inside,
)
from draftwright.annotations._leader_candidates import (
    RadialLeaderTarget as RadialLeaderTarget,
)
from draftwright.annotations._leader_candidates import (
    _ray_exit_distance as _ray_exit_distance,
)
from draftwright.annotations._leader_candidates import (
    feature_leader_candidates as feature_leader_candidates,
)
from draftwright.annotations._leader_candidates import (
    interior_leader_candidates as interior_leader_candidates,
)
from draftwright.annotations._leader_commit import (
    _commit_joint_leaders,
    _joint_trace_inventory,
    _JointCommitInput,
    _JointInventoryInput,
    _LeaderTraceRecorder,
    _segments,
)
from draftwright.annotations._leader_fixed_ink import (
    _FIXED_INVENTORY_EXHAUSTED,
    _annotation_fixed_ink,
    _coerce_box,
    _face_exactly_covered,
    _FixedInkComponent,
    _point_in_convex_component,
    _validated_face_mesh,
)
from draftwright.annotations._leader_fixed_ink import (
    _convex_hull as _convex_hull,
)
from draftwright.annotations._leader_fixed_ink import (
    _rendered_face_hull as _rendered_face_hull,
)
from draftwright.annotations._leader_fixed_ink import (
    _rendered_residual_components as _rendered_residual_components,
)
from draftwright.layout import (
    _FLOW_COST_SCALE,
    _LEADER_ASSIGN_MAX_JOBS,
    _assign_leader_candidates,
    _LeaderAssignment,
)
from draftwright.leader_policy import LeaderRegionPolicy as LeaderRegionPolicy
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
    foreign_label_clear: Callable[[tuple[float, float, float, float]], bool] | None = None
    allow_policy_b_fixed: bool = False
    require_clear_label_ink: bool = False
    priority: float = 0.0
    on_place: Callable[[Any], None] | None = None
    on_drop: Callable[[str], None] | None = None
    recover: Callable[[], tuple[Any, Any] | None] | None = None


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
        if isinstance(raw, FeatureLeaderCandidate):
            cost += float(raw.preference_penalty)
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


def _candidate_text_hits_component(candidate, component) -> bool:
    """Text damage is never an acceptable Policy-B shaft crossing."""
    if component.name.endswith(":label") and component.box is not None:
        return _ink_hits_box(candidate, component.box)
    label = candidate.label_box
    if label is None:
        return False
    if component.box is not None:
        return _boxes_overlap(label, component.box)
    return any(_convex_polygon_overlaps_box(polygon, label) for polygon in component.polygons)


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
        if job.foreign_label_clear is not None and not job.foreign_label_clear(label):
            blockers.append(f"view:{job.view}:foreign_annotation_clearance")
    fixed_hits = tuple(
        component
        for component in fixed_components
        if _candidate_hits_component(candidate, component)
    )
    fixed_blockers = tuple(component.name for component in fixed_hits)
    blockers.extend(fixed_blockers)
    if job.require_clear_label_ink:
        blockers.extend(
            f"label_ink:{component.name}"
            for component in fixed_hits
            if _candidate_text_hits_component(candidate, component)
        )
    if fixed_blockers and candidate.region is LeaderCandidateRegion.INTERIOR:
        blockers.append(f"view:{job.view}:interior_annotation_ink")
    return tuple(dict.fromkeys(blockers))


def _hard_fixed_blockers(blockers) -> tuple[str, ...]:
    """Constraints no compatibility/resource fallback may relax."""

    return tuple(
        blocker
        for blocker in blockers
        if blocker in {"page", "unmeasurable_label", "geometry_validation"}
        or blocker.startswith(("view:", "title_block", "label_ink:"))
    )


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


@dataclass(frozen=True)
class _PrimaryLeaderCandidates:
    viable_by_job: list[list[_MeasuredLeaderCandidate]]
    policy_blockers_by_job: list[list[tuple[str, ...]]]
    material_by_job: list[list[int]]
    rejected_by_job: list[list[tuple[int, tuple[str, ...]]]]


def _classify_primary_leader_candidates(
    jobs,
    measured_by_job,
    possible_fixed_by_job,
    material_by_view,
    page,
) -> _PrimaryLeaderCandidates:
    """Keep hard-clear and Policy-B candidates with their measured material cost."""

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
    return _PrimaryLeaderCandidates(
        viable_by_job, policy_blockers_by_job, material_by_job, rejected_by_job
    )


@dataclass(frozen=True)
class _LeaderPairConflicts:
    conflicts: list[tuple[int, int, int, int]]
    probes: int
    exhausted: bool


def _pair_conflicts(jobs, viable_by_job) -> _LeaderPairConflicts:
    """Enumerate same-view candidate collisions within the pair-probe budget."""

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
    conflicts: list[tuple[int, int, int, int]] = []
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
                        return _LeaderPairConflicts(conflicts, pair_probes, True)
                    if _candidate_conflict(
                        earlier,
                        later,
                        left_ink_bounds=component_bounds_by_job[earlier_job][earlier_index],
                        right_ink_bounds=component_bounds_by_job[later_job][later_index],
                    ):
                        conflicts.append((earlier_job, earlier_index, later_job, later_index))
    return _LeaderPairConflicts(conflicts, pair_probes, False)


def _greedy_boundary_blockers(candidate, job, page, title_block) -> tuple[str, ...]:
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
        if job.foreign_label_clear is not None and not job.foreign_label_clear(label):
            blockers.append(f"view:{job.view}:foreign_annotation_clearance")
    if _ink_hits_box(candidate, title_block):
        blockers.append("title_block:reserved")
    return tuple(blockers)


def _greedy_terminal_reason(rejected) -> str:
    return (
        "geometry_validation"
        if rejected
        and all(
            "geometry_validation" in entry["blockers"]
            and set(entry["blockers"]) <= {"geometry_validation", "fixed_probe_budget"}
            for entry in rejected
        )
        else "no_clear_room"
    )


@dataclass(frozen=True)
class _GreedyFloorStart:
    fixed: Any
    fixed_verified: bool
    legacy_boxes: dict[str, Any]


def _start_greedy_floor(dwg, jobs, views, bounded_fixed_obstacles) -> _GreedyFloorStart:
    fixed_result = bounded_fixed_obstacles()
    fixed_verified = fixed_result is not _FIXED_INVENTORY_EXHAUSTED
    fixed = fixed_result if fixed_verified else {view: () for view in views}
    legacy_boxes = {
        # Producer fallback replays the pre-#1166 acceptance floor; exact
        # blockers below still persist any retained crossing. Optional
        # future section furniture cannot become a resource-cap veto.
        view: _legacy_fallback_obstacles(dwg, view)
        for view in dict.fromkeys(job.view for job in jobs)
    }
    return _GreedyFloorStart(fixed, fixed_verified, legacy_boxes)


@dataclass(frozen=True)
class _GreedyRecoveryCallbacks:
    recovery_for: Callable[..., Any]
    place: Callable[..., Any]
    drop: Callable[..., Any]
    record_item: Callable[..., Any]
    recovery_cost: Callable[..., float]


def _finish_greedy_recoveries(
    pending_recoveries,
    jobs: list[FeatureLeaderJob],
    callbacks: _GreedyRecoveryCallbacks,
    totals: tuple[int, float, float],
) -> tuple[int, float, float]:
    recovery_for = callbacks.recovery_for
    place = callbacks.place
    drop = callbacks.drop
    record_item = callbacks.record_item
    recovery_cost = callbacks.recovery_cost
    placed_count, total_priority, total_cost = totals
    # Recovery runs after every ordinary winner is committed, so its exact sheet-space
    # predicate sees the complete greedy result rather than depending on producer order.
    for (
        job_index,
        recorded_raw_count,
        recorded_rejected,
        obstacle_count,
        recorded_inventory,
        producer_fallback,
        drop_reason,
    ) in pending_recoveries:
        recovered = recovery_for(job_index)()
        if recovered is not None:
            annotation, feature = recovered
            place(job_index, feature, annotation, recovered=True)
            placed_count += 1
            total_priority += jobs[job_index].priority
            total_cost += recovery_cost(annotation)
        else:
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
            recovered=annotation if recovered is not None else None,
        )
    return placed_count, total_priority, total_cost


@dataclass(frozen=True)
class _GreedySelectionInput:
    dwg: Any
    job: FeatureLeaderJob
    fallback_source: Iterable[Any]
    fixed_components: tuple[_FixedInkComponent, ...]
    fixed_verified: bool
    fixed_probes: int
    page: tuple[float, float, float, float]
    title_block: tuple[float, float, float, float]
    legacy_boxes: tuple[Any, ...]
    field: Any
    reason: str
    prefer_clear: bool
    candidate_entry: Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class _GreedySelection:
    blockers_by_raw: list[tuple[int, tuple[str, ...]]]
    fallback_rejected: list[dict[str, Any]]
    raw_count: int
    selected: _MeasuredLeaderCandidate | None
    selected_policy_b: tuple[str, ...]
    annotation: Any
    inventory: list[dict[str, Any]]
    fixed_verified: bool
    fixed_probes: int


def _select_greedy_job(request: _GreedySelectionInput) -> _GreedySelection:
    """Select a job from its lazy producer stream without changing the fallback floor."""

    dwg = request.dwg
    job = request.job
    fallback_source = request.fallback_source
    fixed_components = request.fixed_components
    fixed_verified = request.fixed_verified
    actual_fixed_probes = request.fixed_probes
    page = request.page
    title_block = request.title_block
    legacy_boxes = request.legacy_boxes
    field = request.field
    reason = request.reason
    prefer_clear = request.prefer_clear
    candidate_entry = request.candidate_entry
    blockers_by_raw = []
    fallback_rejected = []
    raw_count = 0
    selected = None
    selected_policy_b: tuple[str, ...] = ()
    annotation = None
    inventory = []
    # Accepted-but-cutting alternatives, held back while a bounded lookahead
    # searches for one that does not cut. Empty in the common case: the first
    # acceptable route usually clears the body, and then this loop breaks exactly
    # where the pre-#798 one did.
    held: list[tuple[int, int, int, Any, tuple[str, ...]]] = []
    examined_since_accept = None
    source = (
        _measure(raw_index, raw, job, dwg.draft) for raw_index, raw in enumerate(fallback_source)
    )
    for candidate in source:
        raw_count = max(raw_count, candidate.raw_index + 1)
        if examined_since_accept is not None:
            examined_since_accept += 1
            if examined_since_accept > _GREEDY_MATERIAL_LOOKAHEAD:
                break
        fixed_components = fixed_components
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
            blockers = (
                *_greedy_boundary_blockers(candidate, job, page, title_block),
                "fixed_probe_budget",
            )
            if candidate.region is LeaderCandidateRegion.INTERIOR:
                blockers = (
                    *blockers,
                    f"view:{job.view}:interior_annotation_ink_unverified",
                )
        hard_blockers = _hard_fixed_blockers(blockers)
        accepted = not hard_blockers and (
            job.fallback_accept(candidate, legacy_boxes, page)
            if job.fallback_accept is not None
            else not tuple(blocker for blocker in blockers if blocker != "fixed_probe_budget")
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
        for _soft, _units, _raw_index, candidate, blockers in sorted(held, key=lambda h: h[:3]):
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
            fixed_components = fixed_components
            if fixed_verified and (
                actual_fixed_probes + len(fixed_components) <= _FEATURE_LEADER_MAX_FIXED_WORK
            ):
                blockers = _fixed_blockers(candidate, job, page, fixed_components)
                actual_fixed_probes += len(fixed_components)
            else:
                fixed_verified = False
                blockers = (
                    *_greedy_boundary_blockers(candidate, job, page, title_block),
                    "fixed_probe_budget",
                )
            if _hard_fixed_blockers(blockers) or not (
                job.fallback_accept(candidate, legacy_boxes, page)
                if job.fallback_accept is not None
                else not tuple(blocker for blocker in blockers if blocker != "fixed_probe_budget")
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
    return _GreedySelection(
        blockers_by_raw,
        fallback_rejected,
        raw_count,
        selected,
        selected_policy_b,
        annotation,
        inventory,
        fixed_verified,
        actual_fixed_probes,
    )


@dataclass(frozen=True)
class _ProvisionalRefinementInput:
    jobs: list[FeatureLeaderJob]
    views: tuple[str, ...]
    viable_by_job: list[list[_MeasuredLeaderCandidate]]
    conflicts: list[tuple[int, int, int, int]]
    policy_blockers_by_job: list[list[tuple[str, ...]]]
    material_by_job: list[list[int]]
    probes_by_view: dict[str, int]
    assignment: _LeaderAssignment
    bounded_fixed_obstacles: Callable[..., Any]


@dataclass(frozen=True)
class _ProvisionalRefinement:
    assignment: _LeaderAssignment
    states: int
    probe_bound: int
    blockers_by_job: list[list[tuple[str, ...]]]
    outcome: str


def _refine_provisional_leaders(inputs: _ProvisionalRefinementInput) -> _ProvisionalRefinement:
    """Try the bounded section-furniture refinement after the primary assignment."""

    jobs = inputs.jobs
    views = inputs.views
    viable_by_job = inputs.viable_by_job
    conflicts = inputs.conflicts
    policy_blockers_by_job = inputs.policy_blockers_by_job
    material_by_job = inputs.material_by_job
    probes_by_view = inputs.probes_by_view
    assignment = inputs.assignment
    bounded_fixed_obstacles = inputs.bounded_fixed_obstacles
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
    return _ProvisionalRefinement(
        assignment,
        assignment_states,
        provisional_probe_bound,
        provisional_blockers_by_job,
        provisional_refinement,
    )


class _LeaderFixedInkInventory:
    """Bounded, sheet-wide fixed ink shared by joint and fallback placement."""

    def __init__(
        self,
        dwg: Any,
        views: tuple[str, ...],
        title_block: tuple[float, float, float, float],
    ):
        self.dwg = dwg
        self.views = views
        self.title_block = title_block
        self._unset = object()
        self._committed = self._unset
        self._provisional = self._unset

    def get(self, *, provisional=False):
        """Lower fixed ink once, stopping before the component work cap."""

        cached = self._provisional if provisional else self._committed
        if cached is not self._unset:
            return cached
        remaining = _FEATURE_LEADER_MAX_FIXED_WORK
        components = []
        if not provisional:
            # The entire mandatory band is hard, including blank cells
            # between rendered title-block strokes and glyphs.
            if remaining < 1:
                cached = _FIXED_INVENTORY_EXHAUSTED
            else:
                components.append(_FixedInkComponent("title_block:reserved", box=self.title_block))
                remaining -= 1
        if cached is self._unset:
            for name, annotation in self.dwg.iter_annotations():
                if (
                    bool(getattr(annotation, "is_provisional_layout_reservation", False))
                    != provisional
                ):
                    continue
                if remaining <= 0:
                    cached = _FIXED_INVENTORY_EXHAUSTED
                    break
                annotation_components = _annotation_fixed_ink(
                    self.dwg,
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
                # boundary. Every job sees the same sheet-wide fixed-ink inventory.
                shared = tuple(components)
                cached = {view: shared for view in self.views}
        if provisional:
            self._provisional = cached
        else:
            self._committed = cached
        return cached


@dataclass(frozen=True)
class _GreedyFloorInput:
    """The lazy floor's streams, boundaries, and commit/trace callbacks."""

    dwg: Any
    jobs: list[FeatureLeaderJob]
    views: tuple[str, ...]
    bounded_fixed_obstacles: Callable[..., Any]
    candidate_budget_fallback_jobs: list[Any]
    fallback_jobs: list[Any]
    page: tuple[float, float, float, float]
    title_block: tuple[float, float, float, float]
    material_by_view: dict[str, Any]
    recorder: _LeaderTraceRecorder
    recovery_for: Callable[..., Any]
    place: Callable[..., Any]
    drop: Callable[..., Any]
    record_policy_b: Callable[..., Any]


def _run_greedy_floor(
    scope: _GreedyFloorInput,
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

    ``prefer_clear`` examines a bounded tail for a route that clears material (#798).
    Geometry-validation replay disables that preference to preserve the producer floor.
    """

    dwg = scope.dwg
    jobs = scope.jobs
    views = scope.views
    bounded_fixed_obstacles = scope.bounded_fixed_obstacles
    candidate_budget_fallback_jobs = scope.candidate_budget_fallback_jobs
    fallback_jobs = scope.fallback_jobs
    page = scope.page
    title_block = scope.title_block
    material_by_view = scope.material_by_view
    recovery_for = scope.recovery_for
    place = scope.place
    drop = scope.drop
    record_policy_b = scope.record_policy_b
    record_item = scope.recorder.record_item
    recovery_cost = scope.recorder.recovery_cost
    candidate_entry = scope.recorder.candidate_entry
    set_assignment = scope.recorder.set_assignment

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
    start = _start_greedy_floor(dwg, jobs, views, bounded_fixed_obstacles)
    fixed_verified = start.fixed_verified
    fixed = start.fixed
    pending_recoveries = []
    legacy_boxes = start.legacy_boxes

    for job_index, job in enumerate(jobs):
        obstacle_count = len(fixed[job.view])
        fallback_source = (
            candidate_budget_fallback_jobs[job_index]
            if reason == "greedy_candidate_budget"
            else fallback_jobs[job_index]
        )
        selection = _select_greedy_job(
            _GreedySelectionInput(
                dwg,
                job,
                fallback_source,
                fixed[job.view],
                fixed_verified,
                actual_fixed_probes,
                page,
                title_block,
                legacy_boxes[job.view],
                material_by_view.get(job.view),
                reason,
                prefer_clear,
                candidate_entry,
            )
        )
        blockers_by_raw = selection.blockers_by_raw
        fallback_rejected = selection.fallback_rejected
        raw_count = selection.raw_count
        selected = selection.selected
        selected_policy_b = selection.selected_policy_b
        annotation = selection.annotation
        inventory = selection.inventory
        fixed_verified = selection.fixed_verified
        actual_fixed_probes = selection.fixed_probes
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
            abandoned_inventories[job_index] if abandoned_inventories is not None else inventory
        )
        recorded_rejected = (
            abandoned_rejected[job_index] if abandoned_rejected is not None else blockers_by_raw
        )
        recorded_raw_count = (
            abandoned_raw_counts[job_index] if abandoned_raw_counts is not None else raw_count
        )
        if selected is None:
            drop_reason = _greedy_terminal_reason(fallback_rejected)
            if recovery_for(job_index) is not None:
                pending_recoveries.append(
                    (
                        job_index,
                        recorded_raw_count,
                        recorded_rejected,
                        obstacle_count,
                        recorded_inventory,
                        producer_fallback,
                        drop_reason,
                    )
                )
                continue
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
    placed_count, total_priority, total_cost = _finish_greedy_recoveries(
        pending_recoveries,
        jobs,
        _GreedyRecoveryCallbacks(recovery_for, place, drop, record_item, recovery_cost),
        (placed_count, total_priority, total_cost),
    )
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


@dataclass(frozen=True)
class _LeaderBatch:
    """Ordered producer streams and one shared fixed-ink/material substrate."""

    crossing_recovery_enabled: bool
    recovery_for: Callable[..., Any]
    page: tuple[float, float, float, float]
    title_block: tuple[float, float, float, float]
    raw_jobs: list
    fallback_jobs: list
    candidate_budget_fallback_jobs: list
    measurement_work_by_view: dict[str, int]
    recorder: _LeaderTraceRecorder
    views: tuple[str, ...]
    material_by_view: dict[str, Any]
    bounded_fixed_obstacles: Callable[..., Any]


def _start_leader_batch(dwg, analysis, ctx, jobs, producer_floor: bool) -> _LeaderBatch:
    """Fork fallback streams without exhausting the producer's candidate order."""
    crossing_recovery_enabled = layout_flag(
        "crossing_recovery", "DRAFTWRIGHT_EXPERIMENTAL_CROSSING_RECOVERY"
    )

    def recovery_for(job_index):
        # The sheet grid is bounded per call, but dense imported parts can have
        # dozens of hole jobs. Keep this optional search within a small inventory.
        job = jobs[job_index]
        if crossing_recovery_enabled and len(jobs) > 16 and job.noun == "hole":
            return None
        return job.recover

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
    recorder = _LeaderTraceRecorder(
        ctx,
        jobs,
        producer_floor,
        measurement_work_by_view,
        _FEATURE_LEADER_MAX_FIXED_WORK,
        _FEATURE_LEADER_MAX_MEASURE_WORK,
    )
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
    # The candidate×component probe cap cannot protect an eager OCC scan. This
    # owner caps inventory lowering and caches it across joint/fallback use.
    bounded_fixed_obstacles = _LeaderFixedInkInventory(dwg, views, title_block).get

    return _LeaderBatch(
        crossing_recovery_enabled,
        recovery_for,
        page,
        title_block,
        raw_jobs,
        fallback_jobs,
        candidate_budget_fallback_jobs,
        measurement_work_by_view,
        recorder,
        views,
        material_by_view,
        bounded_fixed_obstacles,
    )


def _record_policy_b(ctx, jobs, producer_floor, job_index, blockers) -> None:
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


@dataclass(frozen=True)
class _PrimaryJoint:
    """Measured candidates and the bounded primary assignment."""

    measured_by_job: list
    raw_count_by_job: list[int]
    fixed: dict
    fixed_probe_bound: int
    probes_by_view: dict[str, int]
    viable_by_job: list
    policy_blockers_by_job: list
    material_by_job: list
    rejected_by_job: list
    pair_probes: int
    conflicts: list[tuple[int, int, int, int]]
    assignment: _LeaderAssignment


def _prepare_primary_joint(floor: _GreedyFloorInput, batch: _LeaderBatch) -> _PrimaryJoint | int:
    """Admit lazy streams, classify fixed ink, and solve per-view pairs."""
    dwg = floor.dwg
    jobs = floor.jobs
    page = floor.page
    material_by_view = floor.material_by_view
    bounded_fixed_obstacles = floor.bounded_fixed_obstacles
    raw_jobs = batch.raw_jobs
    measurement_work_by_view = batch.measurement_work_by_view

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
            return _run_greedy_floor(floor, "greedy_candidate_budget")
        raw_jobs[job_index] = iter(prefix)

    measured_by_job = [
        [_measure(raw_index, raw, job, dwg.draft) for raw_index, raw in enumerate(iterator)]
        for job, iterator in zip(jobs, raw_jobs, strict=True)
    ]
    raw_count_by_job = [len(candidates) for candidates in measured_by_job]

    fixed = bounded_fixed_obstacles()
    if fixed is _FIXED_INVENTORY_EXHAUSTED:
        return _run_greedy_floor(
            floor,
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
        return _run_greedy_floor(
            floor,
            "greedy_fixed_probe_budget",
            fixed_probe_bound=fixed_probe_bound,
        )
    classified = _classify_primary_leader_candidates(
        jobs, measured_by_job, possible_fixed_by_job, material_by_view, page
    )
    viable_by_job = classified.viable_by_job
    policy_blockers_by_job = classified.policy_blockers_by_job
    material_by_job = classified.material_by_job
    rejected_by_job = classified.rejected_by_job

    pairs = _pair_conflicts(jobs, viable_by_job)
    pair_probes = pairs.probes
    if pairs.exhausted:
        return _run_greedy_floor(
            floor,
            "greedy_pair_budget",
            fixed_probes=fixed_probe_bound,
            fixed_probe_bound=fixed_probe_bound,
            pair_probes=pair_probes,
        )
    conflicts = pairs.conflicts

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

    return _PrimaryJoint(
        measured_by_job,
        raw_count_by_job,
        fixed,
        fixed_probe_bound,
        probes_by_view,
        viable_by_job,
        policy_blockers_by_job,
        material_by_job,
        rejected_by_job,
        pair_probes,
        conflicts,
        assignment,
    )


def _replay_state_budget(
    floor: _GreedyFloorInput, batch: _LeaderBatch, primary: _PrimaryJoint
) -> int | None:
    """Retain a complete incumbent or replay the producer's cardinality floor."""
    assignment = primary.assignment
    fallback_jobs = batch.fallback_jobs
    conflicts = primary.conflicts
    jobs = floor.jobs
    measured_by_job = primary.measured_by_job
    rejected_by_job = primary.rejected_by_job
    viable_by_job = primary.viable_by_job
    policy_blockers_by_job = primary.policy_blockers_by_job
    fixed_probe_bound = primary.fixed_probe_bound
    pair_probes = primary.pair_probes
    raw_count_by_job = primary.raw_count_by_job
    candidate_entry = floor.recorder.candidate_entry

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
        return _run_greedy_floor(
            floor,
            "greedy_state_budget",
            fixed_probes=fixed_probe_bound,
            fixed_probe_bound=fixed_probe_bound,
            pair_probes=pair_probes,
            states=assignment.states,
            abandoned_inventories=abandoned_inventories,
            abandoned_rejected=rejected_by_job,
            abandoned_raw_counts=raw_count_by_job,
        )
    return None


def _materialize_joint_or_replay(
    floor: _GreedyFloorInput,
    primary: _PrimaryJoint,
    refinement: _ProvisionalRefinement,
    joint_inventory: Callable[..., list],
) -> dict | int:
    """Validate selected OCC ink and replay lazy streams if a survivor fails."""
    dwg = floor.dwg
    jobs = floor.jobs
    assignment = refinement.assignment
    viable_by_job = primary.viable_by_job
    fixed_probe_bound = primary.fixed_probe_bound
    provisional_probe_bound = refinement.probe_bound
    provisional_refinement = refinement.outcome
    pair_probes = primary.pair_probes
    assignment_states = refinement.states
    rejected_by_job = primary.rejected_by_job
    raw_count_by_job = primary.raw_count_by_job

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
        return _run_greedy_floor(
            floor,
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

    return materialized


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

    batch = _start_leader_batch(dwg, analysis, ctx, jobs, producer_floor)
    crossing_recovery_enabled = batch.crossing_recovery_enabled
    recovery_for = batch.recovery_for
    page = batch.page
    title_block = batch.title_block
    fallback_jobs = batch.fallback_jobs
    candidate_budget_fallback_jobs = batch.candidate_budget_fallback_jobs
    recorder = batch.recorder
    views = batch.views
    material_by_view = batch.material_by_view
    bounded_fixed_obstacles = batch.bounded_fixed_obstacles

    def place(job_index, candidate, annotation, *, recovered=False):
        job = jobs[job_index]
        # Preserve typed candidate provenance in the registry. Besides trace
        # diagnostics, structural lint uses this to distinguish a solver-proven interior
        # label from an arbitrary annotation that merely happens to lie inside a view.
        ctx.place(
            annotation,
            job.name,
            view=job.view,
            feature=resolve_feature(candidate if recovered else candidate.feature),
            measurement=job.measurement,
            candidate_region=None if recovered else candidate.region.value,
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
        _record_policy_b(ctx, jobs, producer_floor, job_index, blockers)

    floor = _GreedyFloorInput(
        dwg,
        jobs,
        views,
        bounded_fixed_obstacles,
        candidate_budget_fallback_jobs,
        fallback_jobs,
        page,
        title_block,
        material_by_view,
        recorder,
        recovery_for,
        place,
        drop,
        record_policy_b,
    )

    if producer_floor:
        return _run_greedy_floor(floor, "greedy_stage_boundary")

    if len(jobs) > _LEADER_ASSIGN_MAX_JOBS:
        return _run_greedy_floor(floor, "greedy_job_budget")

    primary = _prepare_primary_joint(floor, batch)
    if isinstance(primary, int):
        return primary
    measured_by_job = primary.measured_by_job
    raw_count_by_job = primary.raw_count_by_job
    fixed = primary.fixed
    fixed_probe_bound = primary.fixed_probe_bound
    probes_by_view = primary.probes_by_view
    viable_by_job = primary.viable_by_job
    policy_blockers_by_job = primary.policy_blockers_by_job
    material_by_job = primary.material_by_job
    rejected_by_job = primary.rejected_by_job
    pair_probes = primary.pair_probes
    conflicts = primary.conflicts
    assignment = primary.assignment

    replay = _replay_state_budget(floor, batch, primary)
    if replay is not None:
        return replay
    refinement = _refine_provisional_leaders(
        _ProvisionalRefinementInput(
            jobs,
            views,
            viable_by_job,
            conflicts,
            policy_blockers_by_job,
            material_by_job,
            probes_by_view,
            assignment,
            bounded_fixed_obstacles,
        )
    )
    assignment = refinement.assignment
    assignment_states = refinement.states
    provisional_probe_bound = refinement.probe_bound
    provisional_blockers_by_job = refinement.blockers_by_job
    provisional_refinement = refinement.outcome
    assignment_blockers, joint_inventory = _joint_trace_inventory(
        _JointInventoryInput(
            jobs,
            assignment,
            conflicts,
            viable_by_job,
            rejected_by_job,
            policy_blockers_by_job,
            provisional_blockers_by_job,
            measured_by_job,
            recorder,
        )
    )

    materialized = _materialize_joint_or_replay(floor, primary, refinement, joint_inventory)
    if isinstance(materialized, int):
        return materialized

    return _commit_joint_leaders(
        _JointCommitInput(
            assignment,
            viable_by_job,
            jobs,
            policy_blockers_by_job,
            material_by_job,
            provisional_blockers_by_job,
            fixed_probe_bound,
            provisional_probe_bound,
            provisional_refinement,
            crossing_recovery_enabled,
            recovery_for,
            place,
            drop,
            record_policy_b,
            recorder,
            joint_inventory,
            rejected_by_job,
            raw_count_by_job,
            assignment_blockers,
            fixed,
            materialized,
            assignment_states,
            pair_probes,
        )
    )
