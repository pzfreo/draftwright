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
    _boxes_overlap,
    _convex_polygon_overlaps_box,
    _convex_polygons_overlap,
    _leader_ink_polygons,
    _stroke_polygon,
)
from draftwright.annotations._common import (
    CROSSABLE_TYPES,
    _geom_box,
    annotation_obstacle_boxes,
    strip_obstacles,
)
from draftwright.layout import _LEADER_ASSIGN_MAX_JOBS, _assign_leader_candidates
from draftwright.model.compiled import resolve_feature

_FEATURE_LEADER_MAX_CANDIDATES = 512
_FEATURE_LEADER_MAX_FIXED_PROBES = 100_000
_FEATURE_LEADER_MAX_PAIR_PROBES = 100_000


@dataclass(frozen=True)
class FeatureLeaderJob:
    """One semantic feature-callout job collected for the shared late solve.

    ``candidates`` yields cheap ``(tip, elbow, feature)`` triples. ``build`` is
    called only inside the bounded tier (or lazily by its greedy floor), so an
    oversized inventory cannot trigger collect-all OCC construction merely to
    discover that it is over budget.
    """

    name: str
    view: str
    silhouette: tuple[float, float, float, float]
    label: str
    candidates: Iterable[tuple[Any, Any, Any]]
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
    fallback_candidates: Iterable[tuple[Any, Any, Any]] | None = None
    fallback_accept: (
        Callable[[Any, tuple[Any, ...], tuple[float, float, float, float]], bool] | None
    ) = None
    allow_policy_b_fixed: bool = False
    priority: float = 0.0
    on_place: Callable[[Any], None] | None = None
    on_drop: Callable[[], None] | None = None


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
    attachment_polygons: tuple[tuple[tuple[float, float], ...], ...] = ()


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


def _segments(annotation) -> tuple:
    raw = getattr(annotation, "segments", ()) or ()
    out = []
    for segment in raw:
        try:
            first, second = segment
            out.append(
                (
                    (float(first[0]), float(first[1])),
                    (float(second[0]), float(second[1])),
                )
            )
        except (TypeError, ValueError, IndexError):
            continue
    return tuple(out)


def _label_box(annotation):
    raw = getattr(annotation, "label_bbox", None)
    if raw is None:
        return None
    try:
        box = tuple(float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    return box if len(box) == 4 and all(math.isfinite(value) for value in box) else None


def _ink_hits_box(candidate: _MeasuredLeaderCandidate, box) -> bool:
    """Exact local leader ink/label test against one decomposed obstacle box."""

    if candidate.label_box is not None and _boxes_overlap(candidate.label_box, box):
        return True
    return any(_convex_polygon_overlaps_box(polygon, box) for polygon in candidate.ink_polygons)


def _candidate_conflict(left: _MeasuredLeaderCandidate, right: _MeasuredLeaderCandidate):
    """Whether two alternatives' rendered leader ink cannot coexist."""

    if (
        left.label_box is not None
        and right.label_box is not None
        and _boxes_overlap(left.label_box, right.label_box)
    ):
        return True
    if left.label_box is not None and any(
        _convex_polygon_overlaps_box(polygon, left.label_box) for polygon in right.ink_polygons
    ):
        return True
    if right.label_box is not None and any(
        _convex_polygon_overlaps_box(polygon, right.label_box) for polygon in left.ink_polygons
    ):
        return True
    return any(
        _convex_polygons_overlap(left_polygon, right_polygon)
        for left_polygon in left.ink_polygons
        for right_polygon in right.ink_polygons
    )


def _measure(raw_index, raw, job: FeatureLeaderJob, draft) -> _MeasuredLeaderCandidate:
    tip, elbow, feature = raw
    tip2 = (float(tip[0]), float(tip[1]))
    elbow2 = (float(elbow[0]), float(elbow[1]))
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
            label_box, segments = geometry
    # The helper's shelf length is fixed for a job's label.  Summing real
    # segments still gives the right objective for mixed callout types and is
    # deterministic after the bundled-font render.
    cost = sum(
        math.hypot(second[0] - first[0], second[1] - first[1]) for first, second in segments
    ) or math.hypot(elbow2[0] - tip2[0], elbow2[1] - tip2[1])
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
        primary,
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


def _rendered_ink_matches(candidate: _MeasuredLeaderCandidate, annotation, *, tol=1e-6) -> bool:
    """Validate the selected OCC survivor against its analytical ink contract.

    Metadata parity alone cannot detect a helper change to arrow flare or stroke
    width.  Sample every rendered face boundary after the one selected Leader is
    built and require it to remain inside either the measured label box or the
    candidate's shaft/shelf/arrow components. Candidate exploration remains pure
    arithmetic; only the bounded survivor pays this OCC validation cost.
    """

    def covered(point):
        label = candidate.label_box
        if label is not None and (
            label[0] - tol <= point[0] <= label[2] + tol
            and label[1] - tol <= point[1] <= label[3] + tol
        ):
            return True
        return any(
            _point_in_convex_component(point, polygon, tol=tol)
            for polygon in candidate.ink_polygons
        )

    try:
        faces = tuple(annotation.faces())
        if not faces:
            return False
        for face in faces:
            vertices, _triangles = face.tessellate(0.01)
            if not vertices:
                return False
            for vertex in vertices:
                if not covered((float(vertex.X), float(vertex.Y))):
                    return False
    except Exception:  # noqa: BLE001 — optional placement must fail closed
        return False
    return True


def _materialize(dwg, job: FeatureLeaderJob, candidate: _MeasuredLeaderCandidate):
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
        # furniture; unrelated centre furniture remains fixed ink (#305).
        return False
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
                residual_polygons = candidate.ink_polygons[len(candidate.attachment_polygons) :]
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


def _fixed_blockers(candidate, job, page, fixed_components) -> tuple[str, ...]:
    blockers = []
    label = candidate.label_box
    if label is None:
        blockers.append("unmeasurable_label")
    else:
        if label[0] < page[0] or label[1] < page[1] or label[2] > page[2] or label[3] > page[3]:
            blockers.append("page")
        if _boxes_overlap(label, job.silhouette):
            blockers.append(f"view:{job.view}:silhouette")
    blockers.extend(
        component.name
        for component in fixed_components
        if _candidate_hits_component(candidate, component)
    )
    return tuple(dict.fromkeys(blockers))


def _hard_fixed_blockers(blockers) -> tuple[str, ...]:
    """Constraints no compatibility/resource fallback may relax."""

    return tuple(
        blocker
        for blocker in blockers
        if blocker in {"page", "unmeasurable_label"}
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

    try:
        vertices, _triangles = face.tessellate(curve_envelope)
        points = tuple(
            (float(vertex.X), float(vertex.Y))
            for vertex in vertices
            if math.isfinite(float(vertex.X)) and math.isfinite(float(vertex.Y))
        )
        edge_kinds = tuple(
            getattr(getattr(edge, "geom_type", None), "name", "") for edge in face.edges()
        )
        curved = any(edge_kind != "LINE" for edge_kind in edge_kinds)
    except Exception:  # noqa: BLE001 — optional placement must fail closed
        return (), (), ()
    if curved:
        hull_points = tuple(
            (x + dx, y + dy)
            for x, y in points
            for dx in (-curve_envelope, curve_envelope)
            for dy in (-curve_envelope, curve_envelope)
        )
    else:
        hull_points = points
    return _convex_hull(hull_points), points, edge_kinds


def _rendered_residual_components(name, annotation, components, label, owner, kind):
    """Rendered faces not already represented by segment/label metadata.

    ``Dimension.segments`` is intentionally variable: a shifted label may
    suppress either dimension-line span while both arrowheads still render.
    ``CenterlineCircle`` exposes no linear segments because its chain ring is
    made from OCC arcs.  Face-local lowering handles both boundaries without
    guessing segment count/order and keeps stable component identities.
    """

    def covered(point):
        if label is not None and (
            label[0] - 1e-6 <= point[0] <= label[2] + 1e-6
            and label[1] - 1e-6 <= point[1] <= label[3] + 1e-6
        ):
            return True
        return any(
            _point_in_convex_component(point, polygon, tol=1e-5)
            for component in components
            for polygon in component.polygons
        )

    residual = []
    try:
        faces = annotation.faces()
    except Exception:  # noqa: BLE001 — optional placement must fail closed
        return None
    for face in faces:
        hull, rendered_points, edge_kinds = _rendered_face_hull(face)
        if not hull:
            return None
        if rendered_points and all(covered(point) for point in rendered_points):
            continue
        component_kind = (
            "arc"
            if kind == "CenterlineCircle"
            else "arrow"
            if len(edge_kinds) == 3 or any(edge_kind != "LINE" for edge_kind in edge_kinds)
            else "ink"
        )
        residual.append((component_kind, hull))
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


def _annotation_fixed_ink(dwg, name, annotation):
    """Exact-width fixed ink components for one already-rendered annotation."""

    segments = _segments(annotation)
    components = []
    owner = dwg.registry.feature_of(name)
    kind = type(annotation).__name__
    line_width = (
        0.15 if kind in {"CenterMark", "Centerline", "CenterlineCircle"} else dwg.draft.line_width
    )
    for index, polygon in enumerate(getattr(annotation, "fixed_ink_polygons", ()) or ()):
        try:
            points = tuple((float(point[0]), float(point[1])) for point in polygon)
        except (TypeError, ValueError, IndexError):
            continue
        if len(points) >= 3:
            components.append(
                _FixedInkComponent(
                    f"{name}:ink:{index}",
                    polygons=(points,),
                    owner=owner,
                    kind=kind,
                )
            )
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
                    global_axis=bool(getattr(annotation, "is_global_axis_centerline", False)),
                )
            )
    label = _label_box(annotation)
    if label is not None:
        components.append(_FixedInkComponent(f"{name}:label", box=label, owner=owner, kind=kind))
    if kind in {"Dimension", "CenterlineCircle"}:
        residual = _rendered_residual_components(name, annotation, components, label, owner, kind)
        if residual is None:
            geometry = _geom_box(annotation, getattr(dwg, "box_cache", None))
            if geometry is not None:
                components.append(
                    _FixedInkComponent(
                        f"{name}:geometry",
                        box=geometry,
                        owner=owner,
                        kind=kind,
                    )
                )
        else:
            components.extend(residual)
    if not components:
        geometry = _geom_box(annotation, getattr(dwg, "box_cache", None))
        if geometry is not None:
            components.append(
                _FixedInkComponent(f"{name}:geometry", box=geometry, owner=owner, kind=kind)
            )
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
        candidate = _MeasuredLeaderCandidate(
            annotation,
            tip,
            elbow,
            dwg.registry.feature_of(name),
            0,
            0.0,
            _label_box(annotation),
            segments,
            (*primary, *shelves),
            primary,
        )
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
    if not jobs:
        return 0

    trace = getattr(ctx, "trace", None)
    shared_event = trace.pass_event("feature_leader_inventory") if trace is not None else None
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
    for job in jobs:
        if job.fallback_candidates is None:
            joint, fallback = tee(job.candidates)
        else:
            joint, fallback = job.candidates, job.fallback_candidates
        raw_jobs.append(iter(joint))
        fallback_jobs.append(iter(fallback))

    def fixed_obstacles() -> dict[str, tuple[_FixedInkComponent, ...]]:
        """Complete committed fixed ink; optional future furniture is excluded."""

        title_reservation = (
            ()
            if "title_block" in dwg.annotations()
            else (_FixedInkComponent("title_block:reserved", box=title_block),)
        )
        return {
            view: (
                *title_reservation,
                *_fixed_annotation_obstacles(dwg, view),
            )
            for view in dict.fromkeys(job.view for job in jobs)
        }

    def provisional_obstacles() -> dict[str, tuple[_FixedInkComponent, ...]]:
        """Optional future ink used only by a bounded secondary preference."""

        return {
            view: tuple(_fixed_annotation_obstacles(dwg, view, provisional=True))
            for view in dict.fromkeys(job.view for job in jobs)
        }

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
        ctx.place(
            annotation,
            job.name,
            view=job.view,
            feature=resolve_feature(candidate.feature),
            measurement=job.measurement,
        )
        if job.on_place is not None:
            job.on_place(annotation)

    def drop(job_index):
        job = jobs[job_index]
        if job.on_drop is not None:
            job.on_drop()
        else:
            ctx.record_issue(
                "warning",
                job.drop_code,
                f"{job.noun} callout {job.label} not placed (no clear room)",
                measurement=job.measurement,
            )

    def record_policy_b(job_index, blockers) -> None:
        """Persist an intentionally retained fixed-ink crossing.

        Solve tracing is optional; Policy B is not.  A normal drawing must
        therefore expose the accepted crossing through structured lint rather
        than looking clean merely because the trace recorder was disabled.
        """

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
        for event in [shared_event, *noun_events.values()]:
            if event is not None:
                event.update(
                    {
                        "assignment": value,
                        "optimal": optimal,
                        "states": states,
                        "fixed_probes": fixed_probes,
                        "fixed_probe_bound": fixed_probe_bound,
                        "pair_probes": pair_probes,
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
    ) -> int:
        """Deterministic first-clear floor in original stage/job order."""

        placed_count = 0
        total_priority = 0.0
        total_penalty = 0
        total_cost = 0.0
        actual_fixed_probes = fixed_probes
        fixed = fixed_obstacles()
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
            if label is None:
                blockers.append("unmeasurable_label")
            else:
                if (
                    label[0] < page[0]
                    or label[1] < page[1]
                    or label[2] > page[2]
                    or label[3] > page[3]
                ):
                    blockers.append("page")
                if _boxes_overlap(label, job.silhouette):
                    blockers.append(f"view:{job.view}:silhouette")
            if _ink_hits_box(candidate, title_block):
                blockers.append("title_block:reserved")
            return tuple(blockers)

        for job_index, job in enumerate(jobs):
            obstacle_count = len(fixed[job.view])
            blockers_by_raw = []
            fallback_rejected = []
            raw_count = 0
            selected = None
            selected_policy_b: tuple[str, ...] = ()
            inventory = []
            source = (
                _measure(raw_index, raw, job, dwg.draft)
                for raw_index, raw in enumerate(fallback_jobs[job_index])
            )
            for candidate in source:
                raw_count = max(raw_count, candidate.raw_index + 1)
                fixed_components = fixed[job.view]
                if actual_fixed_probes + len(fixed_components) <= _FEATURE_LEADER_MAX_FIXED_PROBES:
                    blockers = _fixed_blockers(candidate, job, page, fixed_components)
                    actual_fixed_probes += len(fixed_components)
                else:
                    # Replay preserves the producer floor when exact
                    # classification exceeds its work budget. Boundary/title
                    # constraints remain hard and the uncertainty is explicit.
                    blockers = (*boundary_blockers(candidate, job), "fixed_probe_budget")
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
                drop(job_index)
                record_item(
                    job_index,
                    None,
                    recorded_raw_count,
                    recorded_rejected,
                    obstacle_count=obstacle_count,
                    candidate_inventory=recorded_inventory,
                    producer_fallback=producer_fallback,
                    reason="no_clear_room",
                )
                continue
            place(job_index, selected, annotation)
            record_policy_b(job_index, selected_policy_b)
            fixed[job.view] = (
                *fixed[job.view],
                *_annotation_fixed_ink(dwg, job.name, annotation),
            )
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
            total_penalty += len(selected_policy_b)
            total_cost += selected.cost
        set_assignment(
            reason,
            optimal=False,
            states=states,
            fixed_probes=actual_fixed_probes,
            fixed_probe_bound=fixed_probe_bound,
            pair_probes=pair_probes,
            placed=placed_count,
            priority=total_priority,
            penalty=total_penalty,
            cost=total_cost,
        )
        return placed_count

    if len(jobs) > _LEADER_ASSIGN_MAX_JOBS:
        return greedy("greedy_job_budget")

    candidate_count = 0
    candidate_counts_by_job = []
    for job_index, iterator in enumerate(raw_jobs):
        remaining = _FEATURE_LEADER_MAX_CANDIDATES - candidate_count
        prefix = list(islice(iterator, remaining + 1))
        if len(prefix) > remaining:
            raw_jobs[job_index] = chain(prefix, iterator)
            return greedy("greedy_candidate_budget")
        raw_jobs[job_index] = iter(prefix)
        candidate_count += len(prefix)
        candidate_counts_by_job.append(len(prefix))

    fixed = fixed_obstacles()
    fixed_probe_bound = sum(
        count * len(fixed[job.view])
        for count, job in zip(candidate_counts_by_job, jobs, strict=True)
    )
    if fixed_probe_bound > _FEATURE_LEADER_MAX_FIXED_PROBES:
        return greedy(
            "greedy_fixed_probe_budget",
            fixed_probe_bound=fixed_probe_bound,
        )
    viable_by_job = []
    policy_blockers_by_job = []
    rejected_by_job = []
    measured_by_job = []
    raw_count_by_job = []
    for job, iterator in zip(jobs, raw_jobs, strict=True):
        viable = []
        policy_blockers = []
        rejected = []
        measured = []
        raw_count = 0
        for raw_index, raw in enumerate(iterator):
            raw_count = raw_index + 1
            candidate = _measure(raw_index, raw, job, dwg.draft)
            measured.append(candidate)
            blockers = _fixed_blockers(candidate, job, page, fixed[job.view])
            hard_blocked = bool(_hard_fixed_blockers(blockers))
            if blockers and (hard_blocked or not job.allow_policy_b_fixed):
                rejected.append((raw_index, blockers))
                continue
            viable.append(candidate)
            policy_blockers.append(blockers)
        viable_by_job.append(viable)
        policy_blockers_by_job.append(policy_blockers)
        rejected_by_job.append(rejected)
        measured_by_job.append(measured)
        raw_count_by_job.append(raw_count)

    pair_probes = 0
    prior_by_view: dict[str, int] = {}
    for job, candidates in zip(jobs, viable_by_job, strict=True):
        pair_probes += prior_by_view.get(job.view, 0) * len(candidates)
        prior_by_view[job.view] = prior_by_view.get(job.view, 0) + len(candidates)
        if pair_probes > _FEATURE_LEADER_MAX_PAIR_PROBES:
            return greedy(
                "greedy_pair_budget",
                fixed_probes=fixed_probe_bound,
                fixed_probe_bound=fixed_probe_bound,
                pair_probes=pair_probes,
            )

    conflicts = []
    for later_job, later_candidates in enumerate(viable_by_job):
        for earlier_job in range(later_job):
            if jobs[earlier_job].view != jobs[later_job].view:
                continue
            for earlier_index, earlier in enumerate(viable_by_job[earlier_job]):
                for later_index, later in enumerate(later_candidates):
                    if _candidate_conflict(earlier, later):
                        conflicts.append((earlier_job, earlier_index, later_job, later_index))

    assignment = _assign_leader_candidates(
        [[candidate.cost for candidate in candidates] for candidates in viable_by_job],
        conflicts,
        priorities=[job.priority for job in jobs],
        penalties_by_job=[
            [len(blockers) for blockers in job_blockers] for job_blockers in policy_blockers_by_job
        ],
    )

    def all_conflict_names(job_index, candidate_index):
        names = set()
        for earlier_job, earlier_index, later_job, later_index in conflicts:
            if (job_index, candidate_index) == (earlier_job, earlier_index):
                names.add(jobs[later_job].name)
            elif (job_index, candidate_index) == (later_job, later_index):
                names.add(jobs[earlier_job].name)
        return tuple(sorted(names))

    if not assignment.optimal:
        # The layout solver's bounded-search incumbent is seeded from the new
        # exact-ink candidate order, not from every producer's canonical
        # pre-#1166 lazy fallback.  Replaying that producer floor is the only
        # general guarantee that resource pressure cannot reduce semantic
        # cardinality relative to the established renderer.
        abandoned_inventories = []
        for job_index, measured in enumerate(measured_by_job):
            rejected_lookup = dict(rejected_by_job[job_index])
            viable_index = {
                candidate.raw_index: index
                for index, candidate in enumerate(viable_by_job[job_index])
            }
            inventory = []
            for candidate in measured:
                if candidate.raw_index in rejected_lookup:
                    status = "fixed_rejected"
                    blockers = rejected_lookup[candidate.raw_index]
                    conflict_names = ()
                else:
                    status = "joint_abandoned"
                    candidate_index = viable_index[candidate.raw_index]
                    blockers = policy_blockers_by_job[job_index][candidate_index]
                    conflict_names = all_conflict_names(job_index, candidate_index)
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
    provisional = provisional_obstacles()
    provisional_probe_bound = sum(
        len(candidates) * len(provisional[job.view])
        for job, candidates in zip(jobs, viable_by_job, strict=True)
    )
    provisional_refinement = "not_needed"
    provisional_blockers_by_job: list[list[tuple[str, ...]]] = [
        [() for _candidate in candidates] for candidates in viable_by_job
    ]
    if provisional_probe_bound and (
        fixed_probe_bound + provisional_probe_bound <= _FEATURE_LEADER_MAX_FIXED_PROBES
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
        refined = _assign_leader_candidates(
            [[candidate.cost for candidate in candidates] for candidates in viable_by_job],
            conflicts,
            priorities=[job.priority for job in jobs],
            penalties_by_job=[
                [
                    len(fixed_blockers) * max_provisional_penalty + len(provisional_blockers)
                    for fixed_blockers, provisional_blockers in zip(
                        fixed_job_blockers,
                        provisional_job_blockers,
                        strict=True,
                    )
                ]
                for fixed_job_blockers, provisional_job_blockers in zip(
                    policy_blockers_by_job,
                    provisional_blockers_by_job,
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

    materialized = {}
    geometry_failures = set()
    final_choices = list(assignment.choices)
    for job_index, choice in enumerate(final_choices):
        if choice is None:
            continue
        annotation = _materialize(dwg, jobs[job_index], viable_by_job[job_index][choice])
        if annotation is None:
            geometry_failures.add(job_index)
            final_choices[job_index] = None
        else:
            materialized[job_index] = annotation

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
    objective_penalty = sum(
        len(policy_blockers_by_job[job_index][choice])
        for job_index, choice in enumerate(final_choices)
        if choice is not None
    )
    objective_provisional_penalty = sum(
        len(provisional_blockers_by_job[job_index][choice])
        for job_index, choice in enumerate(final_choices)
        if choice is not None
    )

    def joint_inventory(job_index):
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
                status = "geometry_validation" if job_index in geometry_failures else "selected"
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

    total_fixed_probes = fixed_probe_bound + (
        provisional_probe_bound
        if provisional_refinement not in {"not_needed", "probe_budget_retained_primary"}
        else 0
    )
    set_assignment(
        "joint",
        optimal=True,
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
            drop(job_index)
            record_item(
                job_index,
                None,
                raw_count_by_job[job_index],
                [*rejected_by_job[job_index], *assignment_blockers[job_index]],
                obstacle_count=len(fixed[jobs[job_index].view]),
                viable_count=len(viable_by_job[job_index]),
                candidate_inventory=joint_inventory(job_index),
                reason=(
                    "geometry_validation"
                    if job_index in geometry_failures
                    else "assignment_conflict"
                    if viable_by_job[job_index]
                    else "no_clear_room"
                ),
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
