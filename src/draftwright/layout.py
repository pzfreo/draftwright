"""Constraint-based layout engine — phase 1 scaffolding (ADR 0003).

This module holds the placement primitives that every annotation type will
eventually share: a :class:`Placeable` value object describing what the solver
may move, and a :class:`LayoutSolver` that places a set of placeables along one
axis as a single Cassowary (kiwisolver) constraint system.

Phase 1 (issue #79) is *scaffolding only*: it generalises the existing 1D strip
solver into the axis-neutral primitive ADR 0003 calls for and wraps it in the
`Placeable`/`LayoutSolver` surface, but **nothing in the default drawing path
constructs a Placeable yet** — the passes are migrated in later phases (#80–83).
The module deliberately depends on nothing but ``kiwisolver`` and the standard
library, so it is unit-testable without building a drawing.

Explicitly deferred to later phases (so the surface is honest about its limits):
global 2D non-overlap (the disjunctive constraint ADR 0003 notes is non-linear),
the assignment layer (which zone/side — still ``Strip.allocate``), the escalation
ladder, leader-length minimisation, alignment groups, and connector crossing.
The only solve phase 1 performs correctly is the 1D strip solve.
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
    """Cassowary 1D placement for a set of labels sharing one strip.

    Returns solved positions (same length as *naturals*), or ``None`` when they
    do not fit within ``[lo, hi]``. Falls back to the greedy cursor when
    kiwisolver is unavailable.

    *naturals* must be sorted ascending; each solved value is bounded to
    ``[lo, hi]`` and adjacent values are at least *min_gap* apart. Variables are
    created in input order, so the solve is deterministic for a given input.
    """
    if not naturals:
        return []
    n = len(naturals)
    if (n - 1) * min_gap > hi - lo:
        return None  # provably infeasible

    try:
        import kiwisolver as ki
    except ImportError:
        return _greedy_strip_1d(naturals, min_gap, lo, hi)

    solver = ki.Solver()
    vs = [ki.Variable(f"v{i}") for i in range(n)]
    try:
        for v in vs:
            solver.addConstraint((v >= lo) | "required")
            solver.addConstraint((v <= hi) | "required")
        for i in range(n - 1):
            solver.addConstraint((vs[i + 1] - vs[i] >= min_gap) | "required")
        for v, nat in zip(vs, naturals, strict=True):
            solver.addConstraint((v == nat) | "strong")
        solver.updateVariables()
        return [v.value() for v in vs]
    except ki.UnsatisfiableConstraint:
        return None


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
    """Cassowary 1D placement with **per-pair** gaps (ADR 0003 phase 3a, #81).

    Like :func:`_solve_strip_1d`, but the required separation between adjacent
    items is ``gaps[i]`` rather than one uniform value — the capability the
    ``Placeable.size`` field exists for, used when heterogeneous items (e.g. a
    deep step-dim slot next to a shallow height slot) share one strip.
    ``len(gaps)`` must be ``max(len(naturals) - 1, 0)``.
    """
    if not naturals:
        return []
    n = len(naturals)
    if sum(gaps) > hi - lo:
        return None  # provably infeasible

    try:
        import kiwisolver as ki
    except ImportError:
        return _greedy_strip_1d_var(naturals, gaps, lo, hi)

    solver = ki.Solver()
    vs = [ki.Variable(f"v{i}") for i in range(n)]
    try:
        for v in vs:
            solver.addConstraint((v >= lo) | "required")
            solver.addConstraint((v <= hi) | "required")
        for i in range(n - 1):
            solver.addConstraint((vs[i + 1] - vs[i] >= gaps[i]) | "required")
        for v, nat in zip(vs, naturals, strict=True):
            solver.addConstraint((v == nat) | "strong")
        solver.updateVariables()
        return [v.value() for v in vs]
    except ki.UnsatisfiableConstraint:
        return None


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

    An ``eligible_sides`` field joins when the multi-side *assign* step lands
    (P2, #322); the P0 seam places on a single, caller-chosen strip.
    """

    key: str
    anchor: tuple[float, float]
    size: tuple[float, float]
    priority: float = 0


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
    perpendicular coordinate that decides those crossings) — resolving ties
    crossing-optimally is the P4 assign/order step (#318). Then spaces the labels
    within ``[lo, hi]`` at least *min_gap* apart via :func:`_solve_strip_1d`.

    **Selection (P2, #322):** when the strip cannot hold everything, the
    lowest-priority candidates are dropped (ties by key, deterministic) until the
    rest fit — keeping the most important. Returns a :class:`StripPlacement`
    (``placed`` {key: position}, ``dropped`` keys). This is the ranked, priority-
    aware replacement for the engine's arrival-order / prefix drops.

    Spacing is the caller's single *min_gap* (as the engine's strips do today) — the
    candidate's ``size`` is carried for the per-pair, label-height-aware gaps that
    come with the optimal packing (P4, #318), and is not consulted here, so
    *min_gap* must clear the tallest label. Candidate *keys* must be unique (they
    key the result). Deterministic throughout.
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
        positions = _solve_strip_1d([c.anchor[idx] for c in ordered], min_gap, lo, hi)
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
        axis, as one 1D Cassowary solve within ``[lo, hi]``.

        Returns ``{key: position}`` for the placed members (``{}`` when none have
        this axis), or ``None`` when they cannot be made to fit. Members are
        ordered by ``(natural, key)`` so the result is deterministic regardless
        of registration order; a single ``min_gap`` (the largest any member
        requires) separates neighbours.

        When the exact Cassowary solve is infeasible and *greedy_fallback* is
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
