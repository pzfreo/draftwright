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
from typing import Literal

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
    """

    key: str
    anchors: tuple
    size: tuple
    dof_axis: Axis | None
    natural: float
    min_gap: float
    group: str | None = None


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

    def solve_strip(self, *, lo: float, hi: float, axis: Axis) -> dict | None:
        """Place every registered placeable with ``dof_axis == axis`` along that
        axis, as one 1D Cassowary solve within ``[lo, hi]``.

        Returns ``{key: position}`` for the placed members (``{}`` when none have
        this axis), or ``None`` when they cannot be made to fit. Members are
        ordered by ``(natural, key)`` so the result is deterministic regardless
        of registration order; a single ``min_gap`` (the largest any member
        requires) separates neighbours.
        """
        members = sorted(
            (p for p in self._placeables if p.dof_axis == axis),
            key=lambda p: (p.natural, p.key),
        )
        if not members:
            return {}
        gap = max(p.min_gap for p in members)
        naturals = [p.natural for p in members]
        positions = _solve_strip_1d(naturals, gap, lo, hi)
        if positions is None:
            positions = _greedy_strip_1d(naturals, gap, lo, hi)
        if positions is None:
            return None
        return {p.key: pos for p, pos in zip(members, positions, strict=True)}
