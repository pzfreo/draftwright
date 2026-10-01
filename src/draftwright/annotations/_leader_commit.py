"""Final feature-leader trace, joint inventory, and survivor commit.

The assignment solve and producer streams remain in ``leaders``. This owner
records their settled outcomes and commits each surviving annotation once.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from draftwright.annotations._leader_fixed_ink import _coerce_segments
from draftwright.layout import _LeaderAssignment
from draftwright.progress import activity

if TYPE_CHECKING:
    from draftwright.annotations.leaders import FeatureLeaderJob


def _segments(annotation) -> tuple:
    try:
        raw = getattr(annotation, "segments", ()) or ()
    except Exception:  # noqa: BLE001 — optional fixed metadata must fail closed
        return ()
    return _coerce_segments(raw)


class _LeaderTraceRecorder:
    """Record the bounded candidate inventory and final placement objective."""

    def __init__(
        self,
        ctx: Any,
        jobs: list[FeatureLeaderJob],
        producer_floor: bool,
        measurement_work_by_view: dict[str, int],
        fixed_work_limit: int,
        measure_work_limit: int,
    ):
        self.jobs = jobs
        self.measurement_work_by_view = measurement_work_by_view
        self.fixed_work_limit = fixed_work_limit
        self.measure_work_limit = measure_work_limit
        trace = getattr(ctx, "trace", None)
        # Immediate pre-drain batches retain their noun event, but only the
        # canonical late inventory emits the shared drain event.
        self.shared_event = (
            trace.pass_event("feature_leader_inventory")
            if trace is not None and not producer_floor
            else None
        )
        self.noun_events = (
            {
                noun: trace.pass_event(f"{noun}_callouts")
                for noun in dict.fromkeys(job.noun for job in jobs)
            }
            if trace is not None
            else {}
        )

    def record_item(
        self,
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
        recovered=None,
    ):
        job = self.jobs[job_index]
        item = {
            "name": job.name,
            "view": job.view,
            "label": job.label,
            "source_pass": job.noun,
            "priority": job.priority,
            "obligation_class": job.effective_obligation_class,
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
        if recovered is not None:
            bends = tuple(getattr(recovered, "bends", ()))
            item.update(
                {
                    "outcome": "placed",
                    "recovery": "sheet_recovery",
                    "route": "bent" if bends else "straight",
                    "tip": list(recovered.tip[:2]),
                    "elbow": list(recovered.elbow[:2]),
                    "cost": self.recovery_cost(recovered),
                }
            )
            if bends:
                item["bends"] = [list(point[:2]) for point in bends]
        elif candidate is None:
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
        if self.shared_event is not None:
            self.shared_event["items"].append(dict(item))
        event = self.noun_events.get(job.noun)
        if event is not None:
            event["items"].append(dict(item))

    def recovery_cost(self, annotation) -> float:
        """Use the same rendered-segment objective as an ordinary measured candidate."""
        return sum(
            math.hypot(second[0] - first[0], second[1] - first[1])
            for first, second in _segments(annotation)
        )

    def candidate_entry(self, candidate, status, blockers=(), assignment_blockers=()):
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
        self,
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
        for event in [self.shared_event, *self.noun_events.values()]:
            if event is not None:
                event.update(
                    {
                        "assignment": value,
                        "optimal": optimal,
                        "states": states,
                        "fixed_probes": fixed_probes,
                        "fixed_probe_bound": fixed_probe_bound,
                        "fixed_work_limit": self.fixed_work_limit,
                        "pair_probes": pair_probes,
                        "joint_measurement_work": sum(self.measurement_work_by_view.values()),
                        "joint_measurement_work_by_view": dict(self.measurement_work_by_view),
                        "joint_measurement_work_limit_per_view": (self.measure_work_limit),
                        "provisional_refinement": provisional_refinement,
                        "inventory_jobs": len(self.jobs),
                        "objective": {
                            "placed": placed,
                            "priority": priority,
                            "penalty": penalty,
                            "provisional_penalty": provisional_penalty,
                            "cost": cost,
                        },
                    }
                )


@dataclass(frozen=True)
class _JointInventoryInput:
    """Candidate and conflict evidence for the ordered joint trace ledger."""

    jobs: list[FeatureLeaderJob]
    assignment: _LeaderAssignment
    conflicts: list[tuple[int, int, int, int]]
    viable_by_job: list
    rejected_by_job: list
    policy_blockers_by_job: list
    provisional_blockers_by_job: list
    measured_by_job: list
    recorder: _LeaderTraceRecorder


def _joint_trace_inventory(scope: _JointInventoryInput) -> tuple[list, Callable[..., list]]:
    """Record nonselected candidates against the settled assignment."""
    jobs = scope.jobs
    assignment = scope.assignment
    conflicts = scope.conflicts
    viable_by_job = scope.viable_by_job
    rejected_by_job = scope.rejected_by_job
    policy_blockers_by_job = scope.policy_blockers_by_job
    provisional_blockers_by_job = scope.provisional_blockers_by_job
    measured_by_job = scope.measured_by_job
    candidate_entry = scope.recorder.candidate_entry

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

    return assignment_blockers, joint_inventory


@dataclass(frozen=True)
class _JointCommitInput:
    """Settled assignment and callbacks for the survivor commit."""

    assignment: _LeaderAssignment
    viable_by_job: list
    jobs: list[FeatureLeaderJob]
    policy_blockers_by_job: list
    material_by_job: list
    provisional_blockers_by_job: list
    fixed_probe_bound: int
    provisional_probe_bound: int
    provisional_refinement: str
    crossing_recovery_enabled: bool
    recovery_for: Callable[..., Any]
    place: Callable[..., Any]
    drop: Callable[..., Any]
    record_policy_b: Callable[..., Any]
    recorder: _LeaderTraceRecorder
    joint_inventory: Callable[..., list]
    rejected_by_job: list
    raw_count_by_job: list
    assignment_blockers: list
    fixed: dict
    materialized: dict
    assignment_states: int
    pair_probes: int


def _commit_joint_leaders(scope: _JointCommitInput) -> int:
    """Commit clear winners, optional crossing recoveries, and final diagnostics."""
    assignment = scope.assignment
    viable_by_job = scope.viable_by_job
    jobs = scope.jobs
    policy_blockers_by_job = scope.policy_blockers_by_job
    material_by_job = scope.material_by_job
    provisional_blockers_by_job = scope.provisional_blockers_by_job
    fixed_probe_bound = scope.fixed_probe_bound
    provisional_probe_bound = scope.provisional_probe_bound
    provisional_refinement = scope.provisional_refinement
    crossing_recovery_enabled = scope.crossing_recovery_enabled
    recovery_for = scope.recovery_for
    place = scope.place
    drop = scope.drop
    record_policy_b = scope.record_policy_b
    joint_inventory = scope.joint_inventory
    rejected_by_job = scope.rejected_by_job
    raw_count_by_job = scope.raw_count_by_job
    assignment_blockers = scope.assignment_blockers
    fixed = scope.fixed
    materialized = scope.materialized
    assignment_states = scope.assignment_states
    pair_probes = scope.pair_probes
    record_item = scope.recorder.record_item
    recovery_cost = scope.recorder.recovery_cost
    set_assignment = scope.recorder.set_assignment

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
    improve_crossings = crossing_recovery_enabled
    crossing_recoveries = {}
    crossed_choices = {
        job_index
        for job_index, choice in enumerate(final_choices)
        if improve_crossings
        and choice is not None
        and policy_blockers_by_job[job_index][choice]
        and recovery_for(job_index) is not None
    }
    # Commit clear winners first. In the experimental path, give retained Policy B
    # leaders one last sheet-global route against those committed winners.
    for job_index, choice in enumerate(final_choices):
        if choice is None or job_index in crossed_choices:
            continue
        candidate = viable_by_job[job_index][choice]
        place(job_index, candidate, materialized[job_index])
    for job_index in sorted(crossed_choices):
        choice = cast(int, final_choices[job_index])
        recovered = recovery_for(job_index)()
        if recovered is not None:
            annotation, feature = recovered
            place(job_index, feature, annotation, recovered=True)
            crossing_recoveries[job_index] = annotation
        else:
            candidate = viable_by_job[job_index][choice]
            place(job_index, candidate, materialized[job_index])

    placed_count = 0
    recovery_priority = 0.0
    recovery_path_cost = 0.0
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
            recover = recovery_for(job_index)
            recovered = recover() if recover is not None else None
            if recovered is not None:
                annotation, feature = recovered
                place(job_index, feature, annotation, recovered=True)
                placed_count += 1
                recovery_priority += jobs[job_index].priority
                recovery_path_cost += recovery_cost(annotation)
            else:
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
                recovered=annotation if recovered is not None else None,
            )
            continue
        candidate = viable_by_job[job_index][choice]
        if job_index in crossing_recoveries:
            annotation = crossing_recoveries[job_index]
            objective_penalty -= len(policy_blockers_by_job[job_index][choice])
            objective_cost += recovery_cost(annotation) - candidate.cost
            record_item(
                job_index,
                candidate,
                raw_count_by_job[job_index],
                [*rejected_by_job[job_index], *assignment_blockers[job_index]],
                obstacle_count=len(fixed[jobs[job_index].view]),
                viable_count=len(viable_by_job[job_index]),
                candidate_inventory=joint_inventory(job_index),
                recovered=annotation,
            )
            placed_count += 1
            continue
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
    set_assignment(
        "joint" if assignment.optimal else "joint_state_budget",
        optimal=assignment.optimal,
        states=assignment_states,
        fixed_probes=total_fixed_probes,
        fixed_probe_bound=total_fixed_probes,
        pair_probes=pair_probes,
        placed=placed_count,
        priority=objective_priority + recovery_priority,
        penalty=objective_penalty,
        provisional_penalty=objective_provisional_penalty,
        cost=objective_cost + recovery_path_cost,
        provisional_refinement=provisional_refinement,
    )
    return placed_count
