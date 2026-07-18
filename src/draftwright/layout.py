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

Global 2D non-overlap (the disjunctive constraint ADR 0003 notes is
non-linear) stays deferred (#94) and may never be needed — see that ADR's
2026-06-18 correction.
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
    (:func:`_solve_strip_1d_pava`), which retired the earlier Cassowary
    (kiwisolver) constraint-satisfaction solve (its arbitrary feasible vertex
    was replaced by the L1-optimal, dependency-free placement)."""
    if not naturals:
        return []
    return _solve_strip_1d_pava(naturals, [min_gap] * (len(naturals) - 1), lo, hi)


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
