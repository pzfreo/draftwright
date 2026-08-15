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
    fixed_ink: bool = True
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


def _materialize(dwg, job: FeatureLeaderJob, candidate: _MeasuredLeaderCandidate):
    annotation = candidate.annotation
    if annotation is not None:
        return annotation
    annotation = job.build(candidate.tip, candidate.elbow, candidate.feature)
    if not _geometry_matches(candidate, annotation):
        return None
    # Seed the Drawing's shared OCC-box memo with the one rendered survivor.
    # Candidate evaluation remains arithmetic; lint can reuse this validation
    # measurement rather than tessellating the committed Leader again (#1138).
    _geom_box(annotation, getattr(dwg, "box_cache", None))
    return annotation


def _fixed_blockers(candidate, job, page, named_obstacles) -> tuple[str, ...]:
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
        name
        for name, box in named_obstacles
        if (
            _ink_hits_box(candidate, box)
            if job.fixed_ink
            else candidate.label_box is not None and _boxes_overlap(candidate.label_box, box)
        )
    )
    return tuple(dict.fromkeys(blockers))


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
    raw_jobs = []
    fallback_jobs = []
    for job in jobs:
        if job.fallback_candidates is None:
            joint, fallback = tee(job.candidates)
        else:
            joint, fallback = job.candidates, job.fallback_candidates
        raw_jobs.append(iter(joint))
        fallback_jobs.append(iter(fallback))

    def fixed_obstacles() -> dict[str, tuple[tuple[str, tuple[float, float, float, float]], ...]]:
        """Committed obstacles only; optional provisional rows yield to facts."""

        title_block = (
            analysis.PAGE_W - analysis.TB_W - _TB_CLEAR,
            _TB_CLEAR,
            analysis.PAGE_W - _TB_CLEAR,
            _TB_CLEAR + _TB_H,
        )
        title_reservation = (
            () if "title_block" in dwg.annotations() else (("title_block:reserved", title_block),)
        )
        return {
            view: (
                *title_reservation,
                *(
                    (name, box)
                    for name, box in strip_obstacles(
                        dwg,
                        view=view,
                        crossable=CROSSABLE_TYPES,
                        named=True,
                    )
                    if not getattr(
                        dwg.get_annotation(name), "is_provisional_layout_reservation", False
                    )
                ),
            )
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
        }
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

    def set_assignment(
        value,
        *,
        optimal,
        states=0,
        fixed_probes=0,
        pair_probes=0,
        placed=0,
        priority=0.0,
        penalty=0,
        cost=0.0,
    ):
        for event in [shared_event, *noun_events.values()]:
            if event is not None:
                event.update(
                    {
                        "assignment": value,
                        "optimal": optimal,
                        "states": states,
                        "fixed_probes": fixed_probes,
                        "pair_probes": pair_probes,
                        "inventory_jobs": len(jobs),
                        "objective": {
                            "placed": placed,
                            "priority": priority,
                            "penalty": penalty,
                            "cost": cost,
                        },
                    }
                )

    def greedy(reason, prepared=None, rejected=None, *, fixed_probes=0, pair_probes=0) -> int:
        """Deterministic first-clear floor in original stage/job order."""

        placed_count = 0
        total_priority = 0.0
        total_penalty = 0
        total_cost = 0.0
        fixed = fixed_obstacles()
        for job_index, job in enumerate(jobs):
            obstacle_count = len(fixed[job.view])
            blockers_by_raw = list(rejected[job_index]) if rejected is not None else []
            raw_count = max((raw_index + 1 for raw_index, _ in blockers_by_raw), default=0)
            selected = None
            selected_policy_b: tuple[str, ...] = ()
            source = prepared[job_index] if prepared is not None else None
            if source is None:
                source = (
                    _measure(raw_index, raw, job, dwg.draft)
                    for raw_index, raw in enumerate(fallback_jobs[job_index])
                )
            for candidate in source:
                raw_count = max(raw_count, candidate.raw_index + 1)
                blockers = _fixed_blockers(candidate, job, page, fixed[job.view])
                obstacles = tuple(box for _name, box in fixed[job.view])
                accepted = (
                    job.fallback_accept(candidate, obstacles, page)
                    if job.fallback_accept is not None
                    else not blockers
                )
                if not accepted:
                    blockers_by_raw.append((candidate.raw_index, blockers))
                    continue
                annotation = _materialize(dwg, job, candidate)
                if annotation is None:
                    blockers_by_raw.append((candidate.raw_index, ("geometry_validation",)))
                    continue
                selected = candidate
                selected_policy_b = blockers
                break
            if selected is None:
                drop(job_index)
                record_item(
                    job_index,
                    None,
                    raw_count,
                    blockers_by_raw,
                    obstacle_count=obstacle_count,
                    viable_count=len(source) if isinstance(source, list) else None,
                    reason="no_clear_room",
                )
                continue
            place(job_index, selected, annotation)
            fixed[job.view] = (
                *fixed[job.view],
                *((job.name, box) for box in annotation_obstacle_boxes(dwg, annotation)),
            )
            record_item(
                job_index,
                selected,
                raw_count,
                blockers_by_raw,
                obstacle_count=obstacle_count,
                viable_count=len(source) if isinstance(source, list) else None,
                policy_b_blockers=selected_policy_b,
            )
            placed_count += 1
            total_priority += job.priority
            total_penalty += len(selected_policy_b)
            total_cost += selected.cost
        set_assignment(
            reason,
            optimal=False,
            fixed_probes=fixed_probes,
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
        return greedy("greedy_fixed_probe_budget", fixed_probes=fixed_probe_bound)
    viable_by_job = []
    policy_blockers_by_job = []
    rejected_by_job = []
    raw_count_by_job = []
    for job, iterator in zip(jobs, raw_jobs, strict=True):
        viable = []
        policy_blockers = []
        rejected = []
        raw_count = 0
        for raw_index, raw in enumerate(iterator):
            raw_count = raw_index + 1
            candidate = _measure(raw_index, raw, job, dwg.draft)
            blockers = _fixed_blockers(candidate, job, page, fixed[job.view])
            hard_blocked = any(
                blocker in {"page", "unmeasurable_label"}
                or blocker.startswith(("view:", "title_block"))
                for blocker in blockers
            )
            if blockers and (hard_blocked or not job.allow_policy_b_fixed):
                rejected.append((raw_index, blockers))
                continue
            viable.append(candidate)
            policy_blockers.append(blockers)
        viable_by_job.append(viable)
        policy_blockers_by_job.append(policy_blockers)
        rejected_by_job.append(rejected)
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
    set_assignment(
        "joint" if assignment.optimal else "joint_budget_incumbent",
        optimal=assignment.optimal,
        states=assignment.states,
        fixed_probes=fixed_probe_bound,
        pair_probes=pair_probes,
        placed=sum(choice is not None for choice in final_choices),
        priority=objective_priority,
        penalty=objective_penalty,
        cost=objective_cost,
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
                reason=(
                    "geometry_validation"
                    if job_index in geometry_failures
                    else "assignment_conflict"
                    if assignment.optimal and viable_by_job[job_index]
                    else "bounded_search_incumbent"
                    if viable_by_job[job_index]
                    else "no_clear_room"
                ),
            )
            continue
        candidate = viable_by_job[job_index][choice]
        place(job_index, candidate, materialized[job_index])
        record_item(
            job_index,
            candidate,
            raw_count_by_job[job_index],
            [*rejected_by_job[job_index], *assignment_blockers[job_index]],
            obstacle_count=len(fixed[jobs[job_index].view]),
            viable_count=len(viable_by_job[job_index]),
            policy_b_blockers=policy_blockers_by_job[job_index][choice],
        )
        placed_count += 1
    return placed_count
