"""Constraint-based layout engine — phase 1 scaffolding (ADR 0003).

This module holds the placement primitives that every annotation type will
eventually share: a :class:`Placeable` value object describing what the solver
may move, and a :class:`LayoutSolver` that places a set of placeables along one
axis via a single 1D strip solve.

Phase 1 (issue #79) is *scaffolding only*: it generalises the existing 1D strip
solver into the axis-neutral primitive ADR 0003 calls for and wraps it in the
`Placeable`/`LayoutSolver` surface, but **nothing in the default drawing path
constructs a Placeable yet** — the passes are migrated in later phases (#80–83).
The 1D solve is the deterministic minimum-total-leader-length PAVA algorithm
(:func:`_solve_strip_1d_pava`, ADR 0009 Amdt 4) — pure standard library, no
third-party solver (the earlier Cassowary/``kiwisolver`` satisfaction solve was
retired once PAVA gave the exact L1 placement), so it is unit-testable without
building a drawing.

Explicitly deferred to later phases (so the surface is honest about its limits):
global 2D non-overlap (the disjunctive constraint ADR 0003 notes is non-linear),
the assignment layer (which zone/side — the collect-then-solve carve,
``place_strip_candidates`` in ``annotations/_common.py``, since #150/P3 retired
``Strip.allocate``), the escalation ladder, leader-length minimisation, alignment
groups, and connector crossing. The only solve phase 1 performs correctly is the
1D strip solve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple

Axis = Literal["x", "y"]


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
    (:func:`_solve_strip_1d_pava`) — the uniform-gap special case of
    :func:`_solve_strip_1d_var` — which retired the earlier Cassowary
    (kiwisolver) constraint-satisfaction solve (its arbitrary feasible vertex
    was replaced by the L1-optimal, dependency-free placement)."""
    if not naturals:
        return []
    return _solve_strip_1d_pava(naturals, [min_gap] * (len(naturals) - 1), lo, hi)


def _greedy_strip_1d_var(naturals, gaps, lo, hi, *, prefix=False):
    """Greedy 1D placement with **per-pair** gaps (ADR 0003 phase 3a, #81).

    ``gaps[i]`` is the minimum ``naturals[i+1] - naturals[i]`` separation;
    ``len(gaps) == max(len(naturals) - 1, 0)``. Otherwise identical to
    :func:`_greedy_strip_1d` (its uniform special case is ``gaps = [g]*(n-1)``).
    """
    result: list = []
    for i, nat in enumerate(naturals):
        floor = lo if i == 0 else result[-1] + gaps[i - 1]
        v = max(floor, nat)
        if v > hi:
            if prefix:
                break
            return None
        result.append(v)
    return result


def _solve_strip_1d_var(naturals, gaps, lo, hi):
    """1D placement with **per-pair** gaps (ADR 0003 phase 3a, #81).

    Like :func:`_solve_strip_1d`, but the required separation between adjacent
    items is ``gaps[i]`` rather than one uniform value — the capability the
    ``Placeable.size`` field exists for, used when heterogeneous items (e.g. a
    deep step-dim slot next to a shallow height slot) share one strip.
    ``len(gaps)`` must be ``max(len(naturals) - 1, 0)``.

    Delegates to :func:`_solve_strip_1d_pava` — same contract (``None`` when
    provably infeasible), but the L1-optimal, deterministic, dependency-free
    placement that retired the Cassowary (kiwisolver) satisfaction solve."""
    if not naturals:
        return []
    return _solve_strip_1d_pava(naturals, gaps, lo, hi)


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

    Same contract as :func:`_solve_strip_1d_var`: *naturals* sorted ascending,
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


def _feasible_segments(lo, hi, bands):
    """The sub-intervals of ``[lo, hi]`` left after punching out the keep-out
    *bands* (each an ``(band_lo, band_hi)`` pair). Overlapping/touching bands are
    merged and clipped to ``[lo, hi]`` first; the returned ``(seg_lo, seg_hi)``
    list is ascending and pairwise disjoint (empty when the bands cover
    everything)."""
    clipped: list[tuple[float, float]] = []
    for b_lo, b_hi in sorted(bands):
        b_lo, b_hi = max(b_lo, lo), min(b_hi, hi)
        if b_hi <= b_lo:
            continue
        if clipped and b_lo <= clipped[-1][1]:
            clipped[-1] = (clipped[-1][0], max(clipped[-1][1], b_hi))
        else:
            clipped.append((b_lo, b_hi))
    segments: list[tuple[float, float]] = []
    cursor = lo
    for b_lo, b_hi in clipped:
        if b_lo > cursor:
            segments.append((cursor, b_lo))
        cursor = max(cursor, b_hi)
    if cursor < hi:
        segments.append((cursor, hi))
    return segments


def _snap_out_of_bands(value, bands, lo, hi):
    """Nudge *value* to the nearer edge of any keep-out band it lies inside, toward
    the roomier half of ``[lo, hi]`` on a tie, then clamp to ``[lo, hi]``. Used only
    for the shallow-strip fallback in :func:`_solve_strip_1d_pava_banded`: when a
    strip is too thin to clear a band, this reproduces the old ``_coaxial_lift``
    clamp — sit at the strip edge farthest from the row (minimal residual), rather
    than dead-centre on it."""
    p = value
    for b_lo, b_hi in bands:
        if b_lo < p < b_hi:
            p = b_hi if (hi - value) >= (value - lo) else b_lo
    return min(max(p, lo), hi)


def _solve_strip_1d_pava_banded(naturals, gaps, lo, hi, weights, bands):
    """Band-aware :func:`_solve_strip_1d_pava` (ADR 0009 Amendment 5, P4c, #318):
    the same minimum-(weighted-)total-leader-length placement, but the labels should
    also avoid the keep-out *bands* — the page rows a callout's text may not sit on
    (a view centre-line, a location-dimension's extension line — #305/#321). This
    retires the pre-solve ``_coaxial_lift`` nudge: reserved-row avoidance is now a
    property the solve honours, not a fixed lift the spacing solve could later
    re-crowd.

    A band is a **non-convex** keep-out (stay above *or* below the row), which the
    plain PAVA box cannot express. But the bands split ``[lo, hi]`` into disjoint
    feasible **segments** (:func:`_feasible_segments`), and *within one segment* the
    box is convex again — so the exact PAVA atom still applies. The labels keep
    their fixed ascending order (crossing-free, established upstream), so they map to
    the segments as **contiguous runs at non-decreasing segment indices**; a small
    DP over ``(segment, labels-placed)`` searches for a low-cost such partition,
    solving each run with :func:`_solve_strip_1d_pava` inside its segment. With no
    bands it is exactly the plain solve (byte-identical). Deterministic: each run
    solve is, and ties between equal-cost states break on the lexicographically
    smaller position vector.

    **Not globally optimal across ≥2 segments.** The DP keeps one representative
    per labels-placed count, but a run's feasible room in a later segment depends
    on the *last placed position*, not just the count — so a costlier prefix that
    lowers the last label can unlock a cheaper suffix the DP won't find, and (the
    reachable symptom) a band present alongside an ``anchored`` candidate can drag
    the anchor off its natural. Unreachable on the corpus today (anchor and band
    never co-occur in a placed strip — the centre-line band is gated to rotational
    parts, where :func:`plan_strip`'s caller does not anchor), so output is
    byte-identical; the exact fix (a Pareto frontier over ``(cost, last_pos)`` per
    count) is tracked as a follow-up.

    **Graceful degradation.** Avoidance is a strong preference, not a hard drop: a
    band can be *wider than the whole strip* (a shallow view), leaving no segment to
    hold the label. Rather than drop a real callout to honour the band (against
    policy B — never drop a real annotation just to avoid a crossing), the DP-can't-
    place case falls back to a plain solve toward band-edge-snapped naturals — the
    label sits at the strip edge farthest from the row (minimal residual, as the old
    lift did). A genuine over-capacity strip still returns ``None`` (the fallback
    plain solve does), for the caller's drop-and-retry loop.
    """
    if weights is None:
        weights = [1.0] * len(naturals)
    if not bands:
        return _solve_strip_1d_pava(naturals, gaps, lo, hi, weights)
    if not naturals:
        return []
    n = len(naturals)
    segments = _feasible_segments(lo, hi, bands)

    # DP: best[k] = (cost, positions) to place labels[:k] using the segments seen so
    # far. Each segment takes a contiguous run labels[k:j] (possibly empty), solved
    # by the PAVA atom within the segment's convex box.
    best: dict[int, tuple[float, list[float]]] = {0: (0.0, [])}
    for seg_lo, seg_hi in segments:
        nxt = dict(best)  # carry every state forward = put no labels in this segment
        for k, (cost, pos) in best.items():
            if k >= n:
                continue  # all labels already placed in earlier segments → no run here
            # Lower bound for this segment's run: the cross-segment min-gap to the
            # last label placed in an earlier segment. Tighten seg_lo up to it (a
            # still-convex box) rather than solving against raw seg_lo and rejecting
            # a too-close result — rejecting would discard a valid, strictly-cheaper
            # placement that a shift up would reach (parking a label on the band edge
            # or forcing the fallback; #379 review).
            run_lo = seg_lo if not pos else max(seg_lo, pos[-1] + gaps[k - 1])
            for j in range(k + 1, n + 1):
                sol = _solve_strip_1d_pava(
                    naturals[k:j], gaps[k : j - 1], run_lo, seg_hi, weights[k:j]
                )
                if sol is None:
                    break  # a longer run only needs more room → also infeasible
                total = cost + sum(
                    w * abs(p - nat)
                    for p, nat, w in zip(sol, naturals[k:j], weights[k:j], strict=True)
                )
                cand = pos + sol
                if j not in nxt or (total, cand) < (nxt[j][0], nxt[j][1]):
                    nxt[j] = (total, cand)
        best = nxt
    if n in best:
        return best[n][1]

    # No band-avoiding placement fits every label → accept minimal band intrusion:
    # solve toward naturals snapped to the nearer band edge (the shallow-strip
    # fallback). Returns None only when the strip is genuinely over capacity.
    snapped = [_snap_out_of_bands(nat, bands, lo, hi) for nat in naturals]
    return _solve_strip_1d_pava(snapped, gaps, lo, hi, weights)


# ---------------------------------------------------------------------------
# Collect-then-solve strip stage (ADR 0009)
# ---------------------------------------------------------------------------


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

    An ``eligible_sides`` field joins when the multi-side *assign* step lands
    (P2, #322); the P0 seam places on a single, caller-chosen strip.
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


def plan_strip(candidates, lo, hi, min_gap, *, axis: Axis = "y", forbidden=()):
    """Collect-then-solve placement of *candidates* along one strip (ADR 0009).

    Orders the labels in **site order** along *axis* — placing them in site order
    keeps leaders crossing-free when the sites have **distinct** strip-axis
    coordinates. Sites that share that coordinate are ordered deterministically by
    ``key``; that tie-break is *not* crossing-optimal (it doesn't see the
    perpendicular coordinate that decides those crossings) — resolving ties
    crossing-optimally is the P4 assign/order step (#318). Then spaces the labels
    within ``[lo, hi]``, at least *min_gap* apart, via the per-pair solve
    described below.

    **Selection (P2, #322):** when the strip cannot hold everything, the
    lowest-priority candidates are dropped (ties by key, deterministic) until the
    rest fit — keeping the most important. Returns a :class:`StripPlacement`
    (``placed`` {key: position}, ``dropped`` keys). This is the ranked, priority-
    aware replacement for the engine's arrival-order / prefix drops.

    **Spacing (P4a, #318):** each adjacent pair's required gap is the larger of
    the two candidates' strip-axis extents (``size[idx]``), floored at the
    caller's *min_gap* — the same "larger of the two neighbours' requirements"
    rule :meth:`LayoutSolver.solve_strip` already uses for heterogeneous
    ``Placeable``s, applied here to ``StripCandidate.size`` instead of an
    explicit per-item ``min_gap`` field. *min_gap* is therefore a floor (minimum
    clearance/padding regardless of label size), not the whole story; solved via
    :func:`_solve_strip_1d_pava` (P4b, ADR 0009 Amendment 4), which finds the
    *minimum-total-leader-length* placement rather than merely one that satisfies
    the constraints, deterministically. A candidate marked ``anchored`` is kept at
    its natural position (a dominating weight into the solve) so a tie in that
    minimum can't slide it off — e.g. a central hole's callout off the view-centre
    row. Candidate *keys* must be unique (they key the result). Deterministic
    throughout.

    **Keep-out bands (P4c, #318):** *forbidden* is an iterable of ``(centre,
    half_width)`` rows the labels must avoid — a view centre-line or a location
    dimension's extension line a callout's text may not sit on (#305/#321). It
    routes the spacing through :func:`_solve_strip_1d_pava_banded`, so avoidance is
    a property the solve honours (retiring the pre-solve ``_coaxial_lift`` nudge).
    Empty (the default) → the plain solve, byte-for-byte.
    """
    if not candidates:
        return StripPlacement({}, ())
    keys = [c.key for c in candidates]
    if len(set(keys)) != len(keys):  # like LayoutSolver.register — never silently drop
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
        bands = [(c - h, c + h) for c, h in forbidden]
        positions = _solve_strip_1d_pava_banded(naturals, gaps, lo, hi, weights, bands)
        if positions is not None:
            placed = {c.key: p for c, p in zip(ordered, positions, strict=True)}
            return StripPlacement(placed, tuple(dropped))
        # over capacity → drop the lowest-priority candidate (ties by key) and retry
        victim = min(keep, key=lambda c: (c.priority, c.key))
        keep.remove(victim)
        dropped.append(victim.key)
    return StripPlacement({}, tuple(dropped))


# ---------------------------------------------------------------------------
# Placeable protocol + solver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Placeable:
    """One thing the layout solver may position — a dimension, leader, callout,
    or (later) a table. A pass builds a ``Placeable`` *describing* its annotation;
    the annotation object itself stays a plain build123d-drafting primitive.

    Attributes:
        key: stable identifier; drives deterministic variable ordering and keys
            the solved-position result.
        anchors: fixed points the annotation must connect to — a leader/callout
            has one (its tip), a dimension two (its witness points), a table none.
        size: ``(width, height)`` of the label/footprint, from text metrics; the
            position is what the solver decides.
        dof_axis: the axis the solver may slide this along (``"x"``/``"y"``), or
            ``None`` if it is pinned (e.g. a corner-anchored table).
        natural: preferred position on ``dof_axis`` (e.g. the tip's coordinate);
            the solver pulls toward it with ``strong`` priority.
        min_gap: required clearance to a neighbour on ``dof_axis`` (centre-to-
            centre), derived from ``size`` plus padding.
        group: alignment-group id; placeables sharing a group will share an
            offset variable once alignment lands (phase 3, #81).
        locked: when true the solver must keep this at ``natural`` and never
            move it — a deliberate (human/AI) placement that wins over automatic
            layout. The global solve (#82) honours this; the Drawing-level pin
            verb (``dwg.pin`` / #89) is how callers set it.
    """

    key: str
    anchors: tuple
    size: tuple
    dof_axis: Axis | None
    natural: float
    min_gap: float
    group: str | None = None
    locked: bool = False


class LayoutSolver:
    """Accumulates :class:`Placeable`s and places them as one constraint system.

    Phase 1 (#79) implements exactly one solve — :meth:`solve_strip`, the 1D
    strip case — over ``Placeable``s instead of raw float lists. Global 2D
    non-overlap, the assignment layer, and escalation are later phases; no API
    for them exists here, by design, so they cannot be misused early.
    """

    def __init__(self) -> None:
        self._placeables: list[Placeable] = []
        self._keys: set[str] = set()

    def register(self, placeable: Placeable) -> None:
        """Add a placeable to be solved.

        Raises ``ValueError`` on a duplicate ``key`` — the key is the result
        handle, so a collision would silently drop a placeable from the solved
        map rather than fail visibly.
        """
        if placeable.key in self._keys:
            raise ValueError(f"duplicate placeable key {placeable.key!r}")
        self._keys.add(placeable.key)
        self._placeables.append(placeable)

    def solve_strip(
        self, *, lo: float, hi: float, axis: Axis, greedy_fallback: bool = True
    ) -> dict | None:
        """Place every registered placeable with ``dof_axis == axis`` along that
        axis, as one 1D strip solve within ``[lo, hi]``.

        Returns ``{key: position}`` for the placed members (``{}`` when none have
        this axis), or ``None`` when they cannot be made to fit. Members are
        ordered by ``(natural, key)`` so the result is deterministic regardless
        of registration order; a single ``min_gap`` (the largest any member
        requires) separates neighbours.

        When the exact 1D solve is infeasible and *greedy_fallback* is
        true (the default), a greedy packing is tried before giving up. A caller
        that does its own overflow handling (e.g. dropping a prefix and recording
        it) passes ``greedy_fallback=False`` to get the exact-or-``None`` contract
        of the bare 1D primitive, so its drop logic fires exactly when the solve
        is full.
        """
        members = sorted(
            (p for p in self._placeables if p.dof_axis == axis),
            key=lambda p: (p.natural, p.key),
        )
        if not members:
            return {}
        naturals = [p.natural for p in members]
        min_gaps = [p.min_gap for p in members]
        if len(set(min_gaps)) <= 1:
            # Uniform (or single) members — the scalar primitive, byte-identical
            # to the pre-#81 path (preserves its exact (n-1)*gap arithmetic).
            gap = max(min_gaps)
            positions = _solve_strip_1d(naturals, gap, lo, hi)
            if positions is None and greedy_fallback:
                positions = _greedy_strip_1d(naturals, gap, lo, hi)
        else:
            # Heterogeneous members — per-pair gaps so a larger member's
            # clearance doesn't over-separate every pair (#81).
            gaps = [max(min_gaps[i], min_gaps[i + 1]) for i in range(len(members) - 1)]
            positions = _solve_strip_1d_var(naturals, gaps, lo, hi)
            if positions is None and greedy_fallback:
                positions = _greedy_strip_1d_var(naturals, gaps, lo, hi)
        if positions is None:
            return None
        return {p.key: pos for p, pos in zip(members, positions, strict=True)}

    def place_box(self, *, size, region, obstacles, prefer="br"):
        """Place a rigid box (a table, a GD&T frame, a revision block) in a free
        part of the page near a preferred corner (ADR 0003 phase 4b, #93).

        The 2D analogue of :meth:`solve_strip`: returns the box's ``(x0, y0)``
        page-mm position, or ``None`` if it does not fit. *size* is ``(w, h)``;
        *region* and each *obstacle* are ``(x0, y0, x1, y1)`` boxes; *prefer* is
        one of ``"bl" "br" "tl" "tr"`` — the region corner to sit nearest. This
        is the reusable 2D-placement capability tables and (later) GD&T frames
        and BOM/revision blocks share.
        """
        return fit_box(size, region, obstacles, prefer)


def fit_box(size, region, obstacles, prefer="br"):
    """Place a ``(w, h)`` box in *region* avoiding *obstacles*, sat as near the
    *prefer* corner as possible (ADR 0003, #93).

    *region* and each obstacle are ``(x0, y0, x1, y1)`` page-mm boxes. *prefer* is
    one of ``"bl" "br" "tl" "tr"``. Returns the box ``(x0, y0)`` or ``None``.

    An optimal placement always has each box edge flush against a region or
    obstacle edge, so the candidate top-left positions are exactly
    ``{edge, edge - boxsize}`` per axis — O(n) each, O(n²) positions, each
    checked against the obstacles in O(n). That is O(n³), tractable for the
    dozens-of-annotations obstacle sets the hole table feeds it (the old
    rectangle-enumeration form was O(n⁴) and blew up — #93 review). Deterministic
    (sorted candidates, first minimum wins).
    """
    w, h = size
    rx0, ry0, rx1, ry1 = region
    if w > rx1 - rx0 or h > ry1 - ry0:
        return None
    # Only obstacles that can intersect the region constrain the placement.
    obs = [o for o in obstacles if o[0] < rx1 and rx0 < o[2] and o[1] < ry1 and ry0 < o[3]]
    x_edges = {rx0, rx1, *(o[0] for o in obs), *(o[2] for o in obs)}
    y_edges = {ry0, ry1, *(o[1] for o in obs), *(o[3] for o in obs)}
    xs = sorted({x for e in x_edges for x in (e, e - w) if rx0 <= x <= rx1 - w})
    ys = sorted({y for e in y_edges for y in (e, e - h) if ry0 <= y <= ry1 - h})

    right = prefer in ("br", "tr")
    top = prefer in ("tl", "tr")
    cx = rx1 if right else rx0
    cy = ry1 if top else ry0
    best = None
    best_score = None
    for bx in xs:
        for by in ys:
            if any(bx < o[2] and o[0] < bx + w and by < o[3] and o[1] < by + h for o in obs):
                continue
            bcx = bx + w if right else bx
            bcy = by + h if top else by
            score = (bcx - cx) ** 2 + (bcy - cy) ** 2
            if best_score is None or score < best_score:
                best_score = score
                best = (bx, by)
    return best
