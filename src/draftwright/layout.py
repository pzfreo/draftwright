"""Constraint-based layout primitives (ADR 0003): the deterministic 1D strip
solve and the 2D free-rectangle box placer.

ADR 0003 originally proposed a class-based surface for this — a
:class:`Placeable` value object plus a :class:`LayoutSolver` that would
accumulate placeables and solve them. That surface shipped (#79/#80) but never
became the path production code actually used: hole callouts and turned
diameters were ported onto a sibling implementation instead —
:class:`StripCandidate`/:func:`plan_strip` (ADR 0009's collect-then-solve
boundary labeling), later wrapped again by ``CorridorCandidate``/
``solve_corridor`` in ``annotations/_common.py`` for strips shared across
passes. By 2026-07-10 `Placeable`/`LayoutSolver` had no production caller left
(only their own tests), so they were deleted (#547) rather than kept as unused
scaffolding; the module docstring here previously undersold this by saying
"nothing... constructs a Placeable yet," which had quietly stayed true for the
wrong reason — the phases that were meant to change that shipped onto
`plan_strip` instead.

What actually lives here today:

- The 1D strip-placement primitives, bottoming out in
  :func:`_solve_strip_1d_pava` — the deterministic minimum-total-leader-length
  PAVA algorithm (ADR 0009 Amdt 4), pure standard library, no third-party
  solver (the earlier Cassowary/``kiwisolver`` satisfaction solve was retired
  once PAVA gave the exact L1 placement). :func:`plan_strip` is the
  production-facing collect-then-solve entry point built on top of it
  (selection, ordering, anchoring); the lower-level
  `_solve_strip_1d`/`_greedy_strip_1d` primitives are unit-tested in isolation
  and consumed directly by the balloon-spread pass (imported from here) and
  the diameter-row pass (via the `_core` aliases). Keep-out-band avoidance
  (a callout must not sit on a centre-line or a location-dim row) briefly lived
  as a `plan_strip`-internal banded solve (ADR 0009 Amendment 5, #318); that had
  a cross-segment correctness gap (#381), so Amendment 9 retired it in favour of
  the caller carving bands into the same obstacle segmentation it already uses
  (`holes.py`) — `plan_strip` itself no longer knows about bands.
- :func:`fit_box` — the 2D free-rectangle placer for tables/GD&T frames/BOM
  blocks (#93), the one part of the original `LayoutSolver` surface that is
  genuinely shared.
- :func:`_assign_leader_candidates` — the bounded within-pass assignment for
  post-drain machined-feature leaders (#740).  The annotation layer lowers
  measured alternatives to numeric costs and pairwise conflicts; this leaf
  maximises placed jobs, then minimises total leader length, retaining the
  legacy greedy incumbent if its deterministic search budget is exhausted.

Global 2D non-overlap (the disjunctive constraint ADR 0003 notes is
non-linear) stays deferred (#94) and may never be needed — see that ADR's
2026-06-18 correction.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Literal, NamedTuple

Axis = Literal["x", "y"]
_LAYOUT_EPSILON = 1e-9
_FLOW_COST_SCALE = 1000
# The guarded DP allocates three count×coordinate matrices; these caps keep that
# inventory to tens—not thousands—of MiB and bound the preceding interval scan.
# They are safety limits, not layout heuristics: hitting either returns infeasible.
_GUARDED_STRIP_MAX_STATES = 500_000
_GUARDED_STRIP_MAX_INTERVAL_PROBES = 2_000_000
_LEADER_ASSIGN_MAX_STATES = 100_000
_LEADER_ASSIGN_MAX_JOBS = 256


# ---------------------------------------------------------------------------
# 1D placement primitive (axis-neutral). Moved verbatim from make_drawing.py's
# _greedy_strip_ys / _solve_strip_ys; the names there are kept as aliases.
# ---------------------------------------------------------------------------


def _greedy_strip_1d(naturals, min_gap, lo, hi, *, prefix=False):
    """Greedy 1D placement: push each value up until the gap clears.

    With *prefix=False* (default): returns ``None`` if any item overflows *hi*.
    With *prefix=True*: stops at the first overflow and returns the placed prefix.
    *naturals* must be sorted ascending.
    """
    result = []
    prev = lo - min_gap
    for nat in naturals:
        v = max(prev + min_gap, nat)
        if v > hi:
            if prefix:
                break
            return None
        result.append(v)
        prev = v
    return result


def _solve_strip_1d(naturals, min_gap, lo, hi):
    """1D placement for a set of labels sharing one strip (uniform *min_gap*).

    Returns solved positions (same length as *naturals*), or ``None`` when they
    do not fit within ``[lo, hi]``.

    *naturals* must be sorted ascending; each solved value is bounded to
    ``[lo, hi]`` and adjacent values are at least *min_gap* apart. Delegates to
    the deterministic minimum-total-leader-length PAVA solve
    (:func:`_solve_strip_1d_pava`), which retired the earlier Cassowary
    (kiwisolver) constraint-satisfaction solve (its arbitrary feasible vertex
    was replaced by the L1-optimal, dependency-free placement)."""
    if not naturals:
        return []
    return _solve_strip_1d_pava(naturals, [min_gap] * (len(naturals) - 1), lo, hi)


def _solve_guarded_strip_1d(naturals, min_gap, allowed_segments):
    """Place an ordered strip when each member has its own allowed intervals.

    Returns one coordinate per member, or ``None`` when the complete set cannot
    fit.  The solve is joint: moving an earlier member may make room for a later
    one.  Candidate coordinates are derived from interval boundaries, natural
    positions, and the exact separation constraint; there is no sampling grid.

    The finite candidate set is complete for this interval-constrained L1
    problem: a feasible placement can be translated until a coordinate reaches
    an interval boundary, its natural position, or another coordinate's
    ``min_gap`` boundary.  Adding every such source shifted by up to ``n`` gaps
    therefore contains a feasible representative whenever one exists.

    Candidate expansion is deliberately resource-bounded.  A dense inventory
    crossed with many disjoint label keep-outs can otherwise create millions of
    Python DP cells before the caller gets a chance to restore its fallbacks.
    Returning ``None`` at the budget is the conservative result: guarded balloon
    placement treats it exactly like any other infeasible inventory and fails the
    replacement transaction closed (ADR 0014 Policy B).
    """
    if not naturals:
        return []
    if len(naturals) != len(allowed_segments):
        raise ValueError("naturals and allowed_segments must have equal length")
    if min_gap <= 0:
        raise ValueError("min_gap must be positive")
    if any(not segments for segments in allowed_segments):
        return None

    count = len(naturals)
    total_segments = sum(len(segments) for segments in allowed_segments)
    for segments in allowed_segments:
        for lo, hi in segments:
            if hi < lo:
                raise ValueError("allowed segment has descending bounds")
    coordinate_budget = min(
        _GUARDED_STRIP_MAX_STATES // count,
        _GUARDED_STRIP_MAX_INTERVAL_PROBES // total_segments,
    )
    if coordinate_budget <= 0:
        return None

    coordinate_set: set[float] = set()
    sources: set[float] = set()

    def add_source(source) -> bool:
        source = float(source)
        if source in sources:
            return True
        sources.add(source)
        for offset in range(-count, count + 1):
            coordinate_set.add(source + offset * min_gap)
            if len(coordinate_set) > coordinate_budget:
                return False
        return True

    for natural, segments in zip(naturals, allowed_segments, strict=True):
        natural = float(natural)
        if not add_source(natural):
            return None
        for lo, hi in segments:
            lo = float(lo)
            hi = float(hi)
            if (
                not add_source(lo)
                or not add_source(hi)
                or not add_source(min(max(natural, lo), hi))
            ):
                return None
    coordinates = sorted(coordinate_set)

    allowed = [
        [any(lo <= value <= hi for lo, hi in segments) for value in coordinates]
        for segments in allowed_segments
    ]
    previous = []
    left = 0
    for index, value in enumerate(coordinates):
        while left < index and coordinates[left] <= value - min_gap + _LAYOUT_EPSILON:
            left += 1
        previous.append(left - 1)

    # DP over ordered members and ordered coordinates.  Each state carries the
    # minimum L1 displacement for a complete prefix; ``None`` means infeasible.
    infinity = float("inf")
    costs = [[infinity] * len(coordinates) for _ in range(count)]
    parents: list[list[int | None]] = [[None] * len(coordinates) for _ in range(count)]
    for coordinate_index, value in enumerate(coordinates):
        if allowed[0][coordinate_index]:
            costs[0][coordinate_index] = abs(value - naturals[0])
    for member_index in range(1, count):
        best_cost = infinity
        best_index = None
        scan = 0
        for coordinate_index, value in enumerate(coordinates):
            limit = previous[coordinate_index]
            while scan <= limit:
                candidate = costs[member_index - 1][scan]
                if candidate < best_cost - _LAYOUT_EPSILON:
                    best_cost = candidate
                    best_index = scan
                scan += 1
            if allowed[member_index][coordinate_index] and best_index is not None:
                costs[member_index][coordinate_index] = best_cost + abs(
                    value - naturals[member_index]
                )
                parents[member_index][coordinate_index] = best_index

    final_index = min(
        range(len(coordinates)),
        key=lambda index: (costs[-1][index], coordinates[index]),
    )
    if math.isinf(costs[-1][final_index]):
        return None
    result = [0.0] * count
    for member_index in range(count - 1, -1, -1):
        result[member_index] = coordinates[final_index]
        parent = parents[member_index][final_index]
        if member_index:
            assert parent is not None
            final_index = parent
    return result


_ANCHOR_WEIGHT = 1.0e6
"""Weight that pins an anchored candidate at its natural position in the weighted
median (:func:`_solve_strip_1d_pava`). Any value that dwarfs the sum of a strip's
non-anchored weights (unit each, at most a few dozen per strip) makes the anchored
point win every pool median, so the solve keeps it put — see :func:`plan_strip`."""


def _weighted_median(members):
    """Lower weighted median of ``(value, weight)`` pairs — the smallest value at
    which the cumulative weight reaches half the total. The L1-minimising point of
    a pool; picking the *lower* end of the (possibly interval-valued) median makes
    the choice deterministic regardless of platform or solver (ADR 0001)."""
    ordered = sorted(members)
    half = sum(w for _, w in ordered) / 2.0
    cum = 0.0
    for value, weight in ordered:
        cum += weight
        if cum >= half:
            return value
    return ordered[-1][0]


def _solve_strip_1d_pava(naturals, gaps, lo, hi, weights=None):
    """Minimum-(weighted-)total-leader-length 1D placement with per-pair gaps
    (ADR 0009 Amendment 4, P4b, #318).

    Unlike a bare constraint-*satisfaction* solve (which only needs to satisfy
    order/gap/bounds — the retired Cassowary/kiwisolver path), this finds the
    placement minimising the (weighted) total leader length
    (``sum(w_i * abs(p_i - naturals[i]))``, L1 — leader length is a real
    distance, not a squared one) subject to the same constraints. It is the
    exact solve the earlier ``scipy.optimize.linprog`` P4b prototype computed,
    but via the **Pool Adjacent Violators Algorithm** with weighted medians, so
    it is **deterministic by construction** — no dependence on a solver's
    arbitrary vertex choice on the (very common) non-unique L1 optimum, which
    diverged across the scipy versions in the CI matrix (the defect Amendment 4
    records).

    Method: the per-pair min-gap folds away with the shift ``s_i = naturals_i −
    Σ_{j<i} gaps_j`` (so "monotone with gaps" becomes plain non-decreasing);
    weighted-median PAVA gives the exact L1 isotonic fit of the shifted values;
    the box ``[lo, hi]`` on ``p`` reduces (via the same shift, using
    monotonicity) to a **global** box ``[lo, hi − Σgaps]`` on ``s`` that an
    exact clamp of each fitted value satisfies; unshifting restores ``p`` with
    every gap met by construction.

    *weights* (default all ``1``) let a caller **anchor** a candidate: a weight
    that dwarfs the others (``_ANCHOR_WEIGHT``) makes that point win every pool
    median, pinning it at its natural position while the rest flow around it.

    Same contract as :func:`_solve_strip_1d`, but with per-pair *gaps*: *naturals* sorted ascending,
    ``len(gaps) == max(len(naturals) - 1, 0)``, and returns ``None`` (never
    raises) when the fixed set is provably infeasible — the caller's
    drop-and-retry loop depends on that.
    """
    if not naturals:
        return []
    n = len(naturals)
    if sum(gaps) > hi - lo:
        return None  # provably infeasible
    if weights is None:
        weights = [1.0] * n

    # Shift naturals so the min-gap chain becomes a plain monotone constraint.
    prefix = 0.0
    shifted = []
    for i, nat in enumerate(naturals):
        if i:
            prefix += gaps[i - 1]
        shifted.append(nat - prefix)
    total_gap = prefix  # Σ gaps

    # Weighted-median PAVA: each block holds its member (value, weight) pairs and
    # its current fitted value; merge adjacent blocks while they violate the
    # non-decreasing order, recomputing the merged block's weighted median.
    blocks: list[list] = []  # each: [fitted_value, [(value, weight), ...]]
    for value, weight in zip(shifted, weights, strict=True):
        block = [value, [(value, weight)]]
        while blocks and blocks[-1][0] > block[0]:
            prev = blocks.pop()
            merged = prev[1] + block[1]
            block = [_weighted_median(merged), merged]
        blocks.append(block)

    # Clamp the (global) box on the shifted axis, then unshift back to positions.
    s_lo, s_hi = lo, hi - total_gap
    fitted = []
    for value, members in blocks:
        clamped = min(max(value, s_lo), s_hi)
        fitted.extend([clamped] * len(members))

    prefix = 0.0
    positions = []
    for i, s in enumerate(fitted):
        if i:
            prefix += gaps[i - 1]
        positions.append(s + prefix)
    return positions


# ---------------------------------------------------------------------------
# Collect-then-solve strip stage (ADR 0009)
# ---------------------------------------------------------------------------


# ── discrete annotation assignments -------------------------------------------------------
@dataclass(frozen=True)
class _LeaderAssignment:
    """Result of :func:`_assign_leader_candidates`.

    ``choices[job]`` is the selected candidate index or ``None``. ``optimal``
    is false when a deterministic resource guard prevented or stopped the exact
    search; the returned incumbent is still at least as good as the legacy
    greedy pass. ``states`` is deterministic work-count evidence for traces and
    tests (zero when the job-count guard prevents search from starting).
    """

    choices: tuple[int | None, ...]
    optimal: bool
    states: int


def _assign_leader_candidates(
    costs_by_job,
    conflicts=(),
    *,
    priorities=None,
    penalties_by_job=None,
    max_states: int = _LEADER_ASSIGN_MAX_STATES,
) -> _LeaderAssignment:
    """Assign at most one candidate per leader job (#740).

    The objectives are lexicographic: maximum placed jobs, maximum summed job
    priority, minimum fixed-obstacle Policy-B penalty, minimum total leader
    length, then the stable input candidate order. ``priorities`` and
    ``penalties_by_job`` default to zero, preserving #740's original objective.
    ``conflicts`` contains
    ``(job_a, candidate_a, job_b, candidate_b)`` pairs that may not coexist.
    The caller derives those pairs from page geometry; this leaf knows only
    indices and numeric costs.

    General candidate-conflict assignment is combinatorial.  The search is
    therefore explicitly bounded.  Before searching, the function constructs
    the old first-clear greedy result as an incumbent.  If the budget is reached,
    that incumbent (or a strictly better one found so far) is returned with
    ``optimal=False``.  Resource pressure can never make the new pass place fewer
    callouts than the pre-#740 algorithm.
    """

    if max_states <= 0:
        raise ValueError("max_states must be positive")
    raw_costs = tuple(tuple(float(cost) for cost in job) for job in costs_by_job)
    if any(not math.isfinite(cost) or cost < 0 for job in raw_costs for cost in job):
        raise ValueError("leader candidate costs must be finite and non-negative")
    # The shared band-flow solver uses the same one-micron fixed-point scale.
    # Besides making summed costs platform-stable, it lets geometrically
    # symmetric alternatives reach the documented candidate-order tie-break
    # instead of being reordered by a few floating-point ULPs.
    costs = tuple(tuple(int(round(cost * _FLOW_COST_SCALE)) for cost in job) for job in raw_costs)
    if priorities is None:
        raw_priorities = (0.0,) * len(costs)
    else:
        raw_priorities = tuple(float(priority) for priority in priorities)
        if len(raw_priorities) != len(costs):
            raise ValueError("leader priorities must match the number of jobs")
    if any(not math.isfinite(priority) for priority in raw_priorities):
        raise ValueError("leader priorities must be finite")
    job_priorities = tuple(int(round(priority * _FLOW_COST_SCALE)) for priority in raw_priorities)
    if penalties_by_job is None:
        penalties = tuple(tuple(0 for _cost in job) for job in costs)
    else:
        penalties = tuple(tuple(int(value) for value in job) for job in penalties_by_job)
        if len(penalties) != len(costs) or any(
            len(job_penalties) != len(job_costs)
            for job_penalties, job_costs in zip(penalties, costs, strict=True)
        ):
            raise ValueError("leader penalties must match the candidate-cost shape")
        if any(value < 0 for job in penalties for value in job):
            raise ValueError("leader penalties must be non-negative")

    offsets = []
    candidate_count = 0
    for job in costs:
        offsets.append(candidate_count)
        candidate_count += len(job)
    # Sparse sets keep memory proportional to actual conflicts. A global bitset
    # per candidate would become quadratic merely because candidate ids are far
    # apart, even when the caller's pair-probe budget found only a few edges.
    adjacency: list[set[int]] = [set() for _ in range(candidate_count)]
    for conflict in conflicts:
        if len(conflict) != 4:
            raise ValueError("leader conflict must contain four indices")
        job_a, candidate_a, job_b, candidate_b = conflict
        if not (0 <= job_a < len(costs) and 0 <= job_b < len(costs)):
            raise ValueError("leader conflict job index is out of range")
        if not (0 <= candidate_a < len(costs[job_a]) and 0 <= candidate_b < len(costs[job_b])):
            raise ValueError("leader conflict candidate index is out of range")
        if job_a == job_b:
            continue  # one-candidate-per-job already makes this pair impossible
        left = offsets[job_a] + candidate_a
        right = offsets[job_b] + candidate_b
        adjacency[left].add(right)
        adjacency[right].add(left)

    # With no cross-job conflicts, each job is independent and the exact answer
    # is simply its cheapest stable candidate. This is the common sparse-drawing
    # path and avoids entering the combinatorial search at all.
    if not any(adjacency):
        independent = tuple(
            (
                min(
                    range(len(job)),
                    key=lambda candidate: (
                        penalties[job_index][candidate],
                        job[candidate],
                        candidate,
                    ),
                )
                if job
                else None
            )
            for job_index, job in enumerate(costs)
        )
        return _LeaderAssignment(independent, True, 1)

    # The exact pre-#740 policy: visit jobs and candidates in input order, and
    # keep the first candidate compatible with every earlier selection.
    greedy = []
    greedy_selected: set[int] = set()
    greedy_cost = 0
    greedy_priority = 0
    greedy_penalty = 0
    for job_index, job in enumerate(costs):
        selected = None
        for candidate_index, cost in enumerate(job):
            candidate = offsets[job_index] + candidate_index
            if adjacency[candidate].isdisjoint(greedy_selected):
                selected = candidate_index
                greedy_selected.add(candidate)
                greedy_cost += cost
                greedy_priority += job_priorities[job_index]
                greedy_penalty += penalties[job_index][candidate_index]
                break
        greedy.append(selected)

    def score(choices, count, priority, penalty, cost):
        # ``None`` sorts after every real candidate, preserving the established
        # input-order tie convention once cardinality and length are equal.
        tie = tuple(
            len(costs[index]) if choice is None else choice for index, choice in enumerate(choices)
        )
        return (-count, -priority, penalty, cost, tie)

    best_choices = tuple(greedy)
    best_count = sum(choice is not None for choice in greedy)
    best_priority = greedy_priority
    best_penalty = greedy_penalty
    best_cost = greedy_cost
    best_score = score(best_choices, best_count, best_priority, best_penalty, best_cost)
    # The exact search is recursive by job. Keep a hard bound comfortably below
    # Python's recursion limit, including the all-blocked case whose pair count is
    # zero and therefore cannot trip the annotation-side pair budget.
    if len(costs) > _LEADER_ASSIGN_MAX_JOBS:
        return _LeaderAssignment(best_choices, False, 0)

    # Optimistic cardinality and cost bounds ignore conflicts; that makes them
    # cheap and safe. The cost bound is needed only when reaching the incumbent
    # cardinality requires selecting every remaining non-empty job, so one
    # suffix sum is sufficient (linear storage, not a quadratic suffix table).
    suffix_nonempty = [0] * (len(costs) + 1)
    suffix_priority = [0] * (len(costs) + 1)
    suffix_min_penalty = [0] * (len(costs) + 1)
    suffix_min_cost = [0] * (len(costs) + 1)
    for index in range(len(costs) - 1, -1, -1):
        suffix_nonempty[index] = suffix_nonempty[index + 1] + bool(costs[index])
        suffix_priority[index] = suffix_priority[index + 1] + (
            job_priorities[index] if costs[index] else 0
        )
        suffix_min_penalty[index] = suffix_min_penalty[index + 1] + (
            min(penalties[index]) if penalties[index] else 0
        )
        suffix_min_cost[index] = suffix_min_cost[index + 1] + (
            min(costs[index]) if costs[index] else 0
        )

    states = 0
    exhausted = False
    choices: list[int | None] = []

    selected_candidates: set[int] = set()

    def search(
        job_index: int,
        placed: int,
        total_priority: int,
        total_penalty: int,
        total_cost: int,
    ) -> None:
        nonlocal states, exhausted, best_choices, best_count, best_priority
        nonlocal best_penalty, best_cost, best_score
        if states >= max_states:
            exhausted = True
            return
        states += 1

        if placed + suffix_nonempty[job_index] < best_count:
            return
        if placed + suffix_nonempty[job_index] == best_count:
            optimistic_priority = total_priority + suffix_priority[job_index]
            if optimistic_priority < best_priority:
                return
            if optimistic_priority == best_priority:
                optimistic_penalty = total_penalty + suffix_min_penalty[job_index]
                if optimistic_penalty > best_penalty:
                    return
                if optimistic_penalty == best_penalty:
                    optimistic_cost = total_cost + suffix_min_cost[job_index]
                    # Keep pruning identical to the exact fixed-point tuple ordering
                    # used by ``score`` below.
                    if optimistic_cost > best_cost:
                        return
        if job_index == len(costs):
            candidate_choices = tuple(choices)
            candidate_score = score(
                candidate_choices,
                placed,
                total_priority,
                total_penalty,
                total_cost,
            )
            if candidate_score < best_score:
                best_choices = candidate_choices
                best_count = placed
                best_priority = total_priority
                best_penalty = total_penalty
                best_cost = total_cost
                best_score = candidate_score
            return

        # Lower-cost alternatives first find a strong incumbent quickly; the
        # candidate index is the deterministic tie-break. Dropping is last because
        # cardinality is the primary objective.
        for candidate_index in sorted(
            range(len(costs[job_index])),
            key=lambda index: (costs[job_index][index], index),
        ):
            candidate = offsets[job_index] + candidate_index
            if not adjacency[candidate].isdisjoint(selected_candidates):
                continue
            choices.append(candidate_index)
            selected_candidates.add(candidate)
            search(
                job_index + 1,
                placed + 1,
                total_priority + job_priorities[job_index],
                total_penalty + penalties[job_index][candidate_index],
                total_cost + costs[job_index][candidate_index],
            )
            selected_candidates.remove(candidate)
            choices.pop()
            if exhausted:
                return
        choices.append(None)
        search(job_index + 1, placed, total_priority, total_penalty, total_cost)
        choices.pop()

    search(0, 0, 0, 0, 0)
    return _LeaderAssignment(best_choices, not exhausted, states)


# ── balloon band assignment (#516; moved from drawing.py, #699) ─────────────────────────
@dataclass
class _FlowEdge:
    to: int
    rev: int
    cap: int
    cost: int


def _strip_capacity(lo: float, hi: float, gap: float) -> int:
    """Number of uniform balloon centres that can fit in ``[lo, hi]``."""

    if hi < lo:
        return 0
    return int(math.floor((hi - lo) / gap + _LAYOUT_EPSILON)) + 1


def _solve_segmented_strip_1d(
    naturals: list[float],
    gap: float,
    segments: list[tuple[float, float]],
    *,
    prefix: bool = False,
) -> list[float] | None:
    """Minimum-L1 ordered placement across disjoint free segments (#901).

    Members retain their natural order, so leaders remain crossing-free.  Dynamic
    programming chooses how many consecutive members each segment receives; the
    existing continuous-strip solver supplies the optimal positions within each
    segment.  Adjacent segments must be separated by at least *gap* (the balloon
    carve guarantees this). ``None`` means the combined capacity is insufficient;
    with *prefix*, the largest placeable leading subset is returned instead.
    """

    if not naturals:
        return []
    ordered = sorted(segments)
    if any(b[0] - a[1] < gap - _LAYOUT_EPSILON for a, b in zip(ordered, ordered[1:])):
        raise ValueError("segmented strip intervals must be separated by gap")
    # member-count -> (cost, coordinates); ties keep the first (leftmost) split.
    states: dict[int, tuple[float, list[float]]] = {0: (0.0, [])}
    for lo, hi in ordered:
        capacity = _strip_capacity(lo, hi, gap)
        next_states = dict(states)  # this segment may remain unused
        for placed, (cost, coords) in states.items():
            for count in range(1, min(capacity, len(naturals) - placed) + 1):
                chunk = naturals[placed : placed + count]
                solved = _solve_strip_1d(chunk, gap, lo, hi)
                assert solved is not None  # count never exceeds _strip_capacity
                candidate = (
                    cost + sum(abs(x - n) for x, n in zip(solved, chunk)),
                    coords + solved,
                )
                previous = next_states.get(placed + count)
                if previous is None or candidate[0] < previous[0] - _LAYOUT_EPSILON:
                    next_states[placed + count] = candidate
        states = next_states
    result = states.get(len(naturals))
    if result is not None:
        return result[1]
    if prefix:
        return states[max(states)][1]
    return None


def _assign_balloon_bands(
    members,
    choices_by_member,
    capacities,
    *,
    prefer_bands=(),
    preference_limit=0.0,
    required_count=0,
):
    """Globally assign balloons to side bands (#516/#901).

    The primary objective is maximum cardinality, followed by maximum placement
    of the first *required_count* members. Within those lexicographic objectives,
    the first member assigned to a band named by *prefer_bands* receives a
    bounded *preference_limit* distance credit before leader length is minimised.
    This is a preference, not a lexicographic override: a remote band stays
    unused. Required-member priority lets a shared automatic table inventory
    include optional non-certifying pattern markers without allowing one to
    displace a row-key balloon (#1144).
    """

    if not 0 <= required_count <= len(members):
        raise ValueError("required_count must be between zero and len(members)")

    band_order = ("left", "right", "top", "bottom")
    bands = [b for b in band_order if capacities.get(b, 0) > 0]
    assigned: dict[str, list] = {b: [] for b in band_order}
    if not members or not bands:
        return assigned, len(members)

    source = 0
    member0 = 1
    band0 = member0 + len(members)
    sink = band0 + len(bands)
    graph: list[list[_FlowEdge]] = [[] for _ in range(sink + 1)]

    def add_edge(fr: int, to: int, cap: int, cost: int) -> _FlowEdge:
        fwd = _FlowEdge(to, len(graph[to]), cap, cost)
        rev = _FlowEdge(fr, len(graph[fr]), 0, -cost)
        graph[fr].append(fwd)
        graph[to].append(rev)
        return fwd

    preference_bonus = int(round(max(0.0, preference_limit) * _FLOW_COST_SCALE))
    base_costs = [
        int(round(max(0.0, distance) * _FLOW_COST_SCALE)) + band_order.index(band)
        for choices in choices_by_member
        for band, distance in choices.items()
        if band in bands
    ]
    max_flow = min(len(members), sum(capacities.get(b, 0) for b in bands))
    # One optional-member penalty dominates the complete possible spread of
    # leader/preference cost across an equal-cardinality flow. It does not alter
    # the primary max-flow objective; it only decides which members survive.
    optional_penalty = max_flow * (max(base_costs, default=0) + preference_bonus + 1) + 1

    used_edges: dict[tuple[int, str], _FlowEdge] = {}
    for i, choices in enumerate(choices_by_member):
        add_edge(source, member0 + i, 1, 0)
        for j, band in enumerate(bands):
            if band not in choices:
                continue
            # Costs are integerised for deterministic shortest paths; the tiny band
            # ordinal keeps exact ties stable without changing real distance order.
            cost = int(round(max(0.0, choices[band]) * _FLOW_COST_SCALE)) + band_order.index(band)
            if i >= required_count:
                cost += optional_penalty
            used_edges[(i, band)] = add_edge(member0 + i, band0 + j, 1, cost)

    # Give the first balloon in each preferred band a bounded distance credit.
    # Unlike the old lexicographically dominant activation bonus (#901 review),
    # this cannot justify a leader more than `preference_limit` longer merely to
    # occupy another side. SPFA supports the negative residual edges.
    preferred = set(prefer_bands)
    for j, band in enumerate(bands):
        capacity = capacities[band]
        if band in preferred and preference_bonus:
            add_edge(band0 + j, sink, 1, -preference_bonus)
            capacity -= 1
        if capacity:
            add_edge(band0 + j, sink, capacity, 0)

    def shortest_path():
        dist = [math.inf] * len(graph)
        prev: list[tuple[int, int] | None] = [None] * len(graph)
        in_queue = [False] * len(graph)
        queue = [source]
        dist[source] = 0
        in_queue[source] = True
        while queue:
            v = queue.pop(0)
            in_queue[v] = False
            for ei, edge in enumerate(graph[v]):
                if edge.cap <= 0:
                    continue
                nd = dist[v] + edge.cost
                if nd >= dist[edge.to]:
                    continue
                dist[edge.to] = nd
                prev[edge.to] = (v, ei)
                if not in_queue[edge.to]:
                    queue.append(edge.to)
                    in_queue[edge.to] = True
        return prev if prev[sink] is not None else None

    flow = 0
    while flow < max_flow and (prev := shortest_path()) is not None:
        v = sink
        while v != source:
            pv, ei = prev[v]
            edge = graph[pv][ei]
            edge.cap -= 1
            graph[v][edge.rev].cap += 1
            v = pv
        flow += 1

    placed = 0
    for i, member in enumerate(members):
        for band in bands:
            edge = used_edges.get((i, band))
            if edge is not None and edge.cap == 0:
                assigned[band].append(member)
                placed += 1
                break
    return assigned, len(members) - placed


@dataclass(frozen=True)
class StripCandidate:
    """One *measured render-intent* ready for strip placement (ADR 0009).

    The boundary-labeling solve reasons over geometry, not semantics: the collect
    step (in ``annotations/``, which may depend on the IR) projects each planner
    render-intent to its page geometry and hands the solver only this — so the
    solver stays a leaf, with no dependency on the IR. A ``StripCandidate`` *is*
    that measured intent.

    Attributes:
        key: stable id — deterministic ordering tie-break and result lookup.
        anchor: the site the leader connects to, in page coords ``(x, y)``. Its
            position along the strip axis sets the label order; placing labels in
            site order keeps leaders crossing-free **for sites with distinct
            strip-axis coordinates** (sites sharing that coordinate are a tie — see
            :func:`plan_strip`).
        size: the label box ``(width, height)`` in page-mm.
        priority: higher wins when the strip is over capacity — the *selection*
            step's ranking (P2, #322). A magnitude (e.g. a hole's diameter), so it
            is a ``float``; ``int`` ranks remain valid (the numeric tower). Unused by
            the P0 seam (all-or-nothing).
        anchored: when ``True`` the spacing solve keeps this candidate at its
            natural position (its ``anchor`` along the strip axis) and flows the
            rest around it (ADR 0009 Amendment 4, P4b). For a central/coaxial hole
            whose callout belongs on the view-centre row: without it the exact
            minimum-total-leader-length solve is free, on a tie, to move the
            central label off centre (the two equal-cost vertices differ only in
            *which* label absorbs the shift). Realised as a dominating weight in
            :func:`_solve_strip_1d_pava`, so it stays a spacing hint, not a hard
            pin (an anchored candidate can still be *dropped* when over capacity).

    Candidates place on a single, caller-chosen strip; side *assignment* stays
    with the caller (the multi-side generalisation was considered in P2/#322
    and not needed — passes pick the strip before solving).
    """

    key: str
    anchor: tuple[float, float]
    size: tuple[float, float]
    priority: float = 0
    anchored: bool = False


class StripPlacement(NamedTuple):
    """Result of :func:`plan_strip`: ``placed`` maps each placed candidate's key to
    its solved position along the strip axis; ``dropped`` is the keys the strip
    could not hold, lowest-priority first (the caller escalates them — detail view,
    table — or surfaces them as lint)."""

    placed: dict
    dropped: tuple


def plan_strip(candidates, lo, hi, min_gap, *, axis: Axis = "y"):
    """Collect-then-solve placement of *candidates* along one strip (ADR 0009).

    Orders the labels in **site order** along *axis* — placing them in site order
    keeps leaders crossing-free when the sites have **distinct** strip-axis
    coordinates. Sites that share that coordinate are ordered deterministically by
    ``key``; that tie-break is *not* crossing-optimal (it doesn't see the
    perpendicular coordinate that decides those crossings) — P4 (#318, closed)
    delivered the min-leader PAVA spacing solve instead, and crossing-optimal
    tie-resolution remains a possible refinement should a real part force it.
    Then spaces the labels
    within ``[lo, hi]``, at least *min_gap* apart, via the per-pair solve
    described below.

    **Selection (P2, #322):** when the strip cannot hold everything, the
    lowest-priority candidates are dropped (ties by key, deterministic) until the
    rest fit — keeping the most important. Returns a :class:`StripPlacement`
    (``placed`` {key: position}, ``dropped`` keys). This is the ranked, priority-
    aware replacement for the engine's arrival-order / prefix drops.

    **Spacing (P4a, #318):** each adjacent pair's required gap is the larger of
    the two candidates' strip-axis extents (``size[idx]``), floored at the
    caller's *min_gap* — the "larger of the two neighbours' requirements" rule
    applied to ``StripCandidate.size`` instead of an explicit per-item
    ``min_gap`` field. *min_gap* is therefore a floor (minimum
    clearance/padding regardless of label size), not the whole story; solved via
    :func:`_solve_strip_1d_pava` (P4b, ADR 0009 Amendment 4), which finds the
    *minimum-total-leader-length* placement rather than merely one that satisfies
    the constraints, deterministically. A candidate marked ``anchored`` is kept at
    its natural position (a dominating weight into the solve) so a tie in that
    minimum can't slide it off — e.g. a central hole's callout off the view-centre
    row. Candidate *keys* must be unique (they key the result). Deterministic
    throughout.

    No keep-out-band support: a caller that needs to avoid a reserved row (a
    view centre-line, a location dimension's extension line — #305/#321) folds
    it into its own obstacle carve and calls this once per free segment, the
    same way it already handles any other 2-D obstacle (ADR 0009 Amendment 9,
    #381, retiring a `plan_strip`-internal banded solve that had a cross-segment
    correctness gap — see `annotations/holes.py`).
    """
    if not candidates:
        return StripPlacement({}, ())
    keys = [c.key for c in candidates]
    if len(set(keys)) != len(keys):  # a collision would silently drop a candidate
        raise ValueError("plan_strip: candidate keys must be unique")
    idx = 1 if axis == "y" else 0

    keep = list(candidates)
    dropped: list[str] = []
    while keep:
        ordered = sorted(keep, key=lambda c: (c.anchor[idx], c.key))
        naturals = [c.anchor[idx] for c in ordered]
        gaps = [
            max(ordered[i].size[idx], ordered[i + 1].size[idx], min_gap)
            for i in range(len(ordered) - 1)
        ]
        weights = [_ANCHOR_WEIGHT if c.anchored else 1.0 for c in ordered]
        positions = _solve_strip_1d_pava(naturals, gaps, lo, hi, weights)
        if positions is not None:
            placed = {c.key: p for c, p in zip(ordered, positions, strict=True)}
            return StripPlacement(placed, tuple(dropped))
        # over capacity → drop the lowest-priority candidate (ties by key) and retry
        victim = min(keep, key=lambda c: (c.priority, c.key))
        keep.remove(victim)
        dropped.append(victim.key)
    return StripPlacement({}, tuple(dropped))


# ---------------------------------------------------------------------------
# 2D free-rectangle box placement (ADR 0003, #93)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FitBoxRejection:
    """One candidate rectangle rejected by :func:`fit_box` (#1145)."""

    region: tuple[float, float, float, float]
    blockers: tuple[str, ...]


@dataclass
class FitBoxTrace:
    """Inspectable record of a :func:`fit_box` decision (#1145).

    The placer stays a pure rectangle solver: callers may supply named obstacles
    and opt into this side record when a failed furniture placement needs an
    actionable diagnostic.  ``rejected_candidates`` retains the exact total;
    ``rejected`` is a bounded, preference-ordered sample for messages.  The hot
    paths that only need a position pay no trace construction cost.
    """

    attempted_candidates: int = 0
    rejected_candidates: int = 0
    rejected: list[FitBoxRejection] = field(default_factory=list)
    violation: str | None = None
    clearance: float = 0.0
    max_rejections: int = field(default=8, repr=False)


def _fit_box_obstacle(item, index):
    """Return ``(name, box)`` for a bare box or ``(name, box)`` input."""
    if (
        isinstance(item, tuple)
        and len(item) == 2
        and isinstance(item[0], str)
        and isinstance(item[1], (tuple, list))
        and len(item[1]) == 4
    ):
        return item[0], tuple(item[1])
    return f"obstacle[{index}]", tuple(item)


def _boxes_overlap(left, right):
    """Return whether two page-space boxes overlap with positive area."""
    return left[0] < right[2] and right[0] < left[2] and left[1] < right[3] and right[1] < left[3]


def fit_box(size, region, obstacles, prefer="br", *, clearance=0.0, trace=None):
    """Place a ``(w, h)`` box in *region* avoiding *obstacles*, sat as near the
    *prefer* corner as possible (ADR 0003, #93).

    *region* and each obstacle are ``(x0, y0, x1, y1)`` page-mm boxes. *prefer* is
    one of ``"bl" "br" "tl" "tr"``. Obstacles may instead be supplied as
    ``(name, box)`` pairs; names do not affect placement. Returns the box
    ``(x0, y0)`` or ``None``.

    *clearance* inflates each obstacle by that many page millimetres before
    candidates are derived and tested.  The usable region itself is unchanged:
    callers own its page/frame inset.

    Pass a :class:`FitBoxTrace` to *trace* to count every rejected candidate and
    retain a bounded preference-ordered sample with the obstacle names that
    blocked it.  This keeps diagnostics derived from the exact solve rather than
    from a second explanatory model.

    An optimal placement always has each box edge flush against a region or
    (clearance-inflated) obstacle edge, so the candidate lower-left positions are exactly
    ``{edge, edge - boxsize}`` per axis — O(n) each, O(n²) positions, each
    checked against the obstacles in O(n). That is O(n³), tractable for the
    dozens-of-annotations obstacle sets the hole table feeds it (the old
    rectangle-enumeration form was O(n⁴) and blew up — #93 review). Candidate
    pairs are streamed rather than materialised, keeping live memory O(n).
    Deterministic (ascending candidates, first minimum wins).
    """
    w, h = size
    rx0, ry0, rx1, ry1 = region
    if clearance < 0:
        raise ValueError("fit_box clearance must be non-negative")
    if trace is not None:
        trace.attempted_candidates = 0
        trace.rejected_candidates = 0
        trace.rejected.clear()
        trace.violation = None
        trace.clearance = clearance
    if w > rx1 - rx0 or h > ry1 - ry0:
        if trace is not None:
            violations = []
            if w > rx1 - rx0:
                violations.append(f"width exceeds usable region by {w - (rx1 - rx0):.1f} mm")
            if h > ry1 - ry0:
                violations.append(f"height exceeds usable region by {h - (ry1 - ry0):.1f} mm")
            trace.violation = "; ".join(violations)
        return None
    # Only obstacles that can intersect the region constrain the placement.
    named = [_fit_box_obstacle(item, index) for index, item in enumerate(obstacles)]
    expanded = [
        (
            name,
            (
                box[0] - clearance,
                box[1] - clearance,
                box[2] + clearance,
                box[3] + clearance,
            ),
        )
        for name, box in named
    ]
    obs = [(name, box) for name, box in expanded if _boxes_overlap(box, region)]
    x_edges = {rx0, rx1, *(o[0] for _name, o in obs), *(o[2] for _name, o in obs)}
    y_edges = {ry0, ry1, *(o[1] for _name, o in obs), *(o[3] for _name, o in obs)}
    xs = sorted({x for e in x_edges for x in (e, e - w) if rx0 <= x <= rx1 - w})
    ys = sorted({y for e in y_edges for y in (e, e - h) if ry0 <= y <= ry1 - h})

    right = prefer in ("br", "tr")
    top = prefer in ("tl", "tr")
    cx = rx1 if right else rx0
    cy = ry1 if top else ry0
    # Scores separate into X + Y terms.  Merge one sorted Y stream per X via a
    # heap so candidates arrive in exact ``(score, bx, by)`` order without ever
    # materialising the O(len(xs) * len(ys)) Cartesian product (#1145 review).
    ranked_y = sorted(
        ((((by + h if top else by) - cy) ** 2, by) for by in ys),
        key=lambda candidate: (candidate[0], candidate[1]),
    )
    candidates: list[tuple[float, float, float, int, float]] = []
    for bx in xs:
        x_score = ((bx + w if right else bx) - cx) ** 2
        y_score, by = ranked_y[0]
        heapq.heappush(candidates, (x_score + y_score, bx, by, 0, x_score))

    while candidates:
        _score, bx, by, y_index, x_score = heapq.heappop(candidates)
        candidate_box = (bx, by, bx + w, by + h)
        retain_rejection = trace is not None and len(trace.rejected) < trace.max_rejections
        if retain_rejection:
            blockers = tuple(sorted({name for name, o in obs if _boxes_overlap(candidate_box, o)}))
            blocked = bool(blockers)
        else:
            # Once the bounded diagnostic sample is full, only collision truth is
            # needed. Preserve the former solver's early exit instead of sorting
            # every blocker name for every remaining O(n²) candidate (#1145 review).
            blockers = ()
            blocked = any(_boxes_overlap(candidate_box, o) for _name, o in obs)
        if trace is not None:
            trace.attempted_candidates += 1
        if not blocked:
            return (bx, by)
        if trace is not None:
            trace.rejected_candidates += 1
            if retain_rejection:
                trace.rejected.append(FitBoxRejection(candidate_box, blockers))

        next_index = y_index + 1
        if next_index < len(ranked_y):
            y_score, next_by = ranked_y[next_index]
            heapq.heappush(
                candidates,
                (x_score + y_score, bx, next_by, next_index, x_score),
            )
    return None
