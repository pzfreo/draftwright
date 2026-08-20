"""Every tolerance the compiler approves must reach the annotation that claims it.

`tests/test_compiled_plan_boundary.py` enforces one direction of ADR 0016 Amdt 1 — a renderer
must not emit what the plan withheld. Nothing enforced the converse: **a renderer must emit
everything the plan approved.**

#1215 was one instance of the converse, and six review rounds found it one site at a time —
envelope extents, the overall-height ladder, the turned-step chain, two public `Drawing` verbs,
the deferred corridor route, prismatic step rungs, the detail-view redraw, the short-rise escape,
step shoulders, and pattern pitch. Each round's fix was correct and each round's sweep was scoped
to the shape of the site it had just seen.

This is the general guard. It decorates **every parameter of every feature** on a set of parts,
builds, and joins what the compiler approved against what the claiming annotation actually
renders. It found three live sites in a single run — the ones rounds 6 was still discovering by
hand — and it fails on any new one.

Why the join is sound: `registry.measurement_of(name)` is the ADR 0010 seam, so an annotation
that claims a `DimensionId` is asserting it draws that measurement. If the compiler attached a
tolerance to that id and the label carries no suffix, the drawing states a requirement less
precisely than the author wrote it — silently, which is the whole failure mode.
"""

from __future__ import annotations

import re

import pytest
from build123d import Box, Cylinder, Pos, Rot

from draftwright.builder import build_drawing
from draftwright.model.compiled import compile_dimensions

_TOL = 0.05


def _staircase():
    return Box(120, 60, 15) + Pos(-20, 0, 15) * Box(80, 60, 15) + Pos(-40, 0, 30) * Box(40, 60, 15)


def _crowded_staircase():
    """Tiers 3 mm apart: the legibility gate moves rungs into an enlarged DETAIL view.

    The detail is the case that matters most — those rungs are dimensioned *only* there, so a
    tolerance dropped in the redraw is absent from the drawing entirely while its siblings on
    the main view show theirs.
    """
    part = Pos(0, 0, 3) * Box(20, 16, 6)
    z = 6
    for w in (16, 13, 10, 7, 5):
        part += Pos(0, 0, z + 1.5) * Box(w, 12, 3)
        z += 3
    return part


def _linear_pattern():
    part = Box(140, 60, 12)
    for x in (-40, -20, 0, 20, 40):
        part -= Pos(x, 0, 0) * Cylinder(3, 40)
    return part


def _turned_shaft():
    return Rot(0, 90, 0) * (Cylinder(8, 30) + Pos(0, 0, 30) * Cylinder(5, 20))


def _short_first_rise():
    """A 1 mm first rise: too short to dimension in the right strip, so it escapes to the LEFT
    one (`_build_left`). That branch is live — instrumenting it records 18 hits across the fast
    tier — but no fixture anywhere toleranced a part that reaches it, so the escape dropped its
    suffix unobserved (#1234 review r6)."""
    from build123d import BuildPart, BuildSketch, Plane, Polygon, extrude

    with BuildPart() as part:
        with BuildSketch(Plane.XZ):
            Polygon((0, 0), (50, 0), (50, 1), (25, 1), (25, 20), (0, 20))
        extrude(amount=30)
    return part.part


def _plate_with_holes():
    return Box(90, 60, 12) - Pos(-25, 12, 0) * Cylinder(4, 40) - Pos(25, -12, 0) * Cylinder(4, 40)


_PARTS = {
    "staircase": _staircase,
    "crowded_staircase": _crowded_staircase,
    "linear_pattern": _linear_pattern,
    "short_first_rise": _short_first_rise,
    "turned_shaft": _turned_shaft,
    "plate_with_holes": _plate_with_holes,
}

#: Parameters whose mark deliberately states no tolerance, with the reason. A collapsed
#: representative is the case: `N× rise` stands for levels that merely fall within 10% of each
#: other, so a ± would claim the author's tolerance of values that differ. (A pattern's `N× pitch`
#: is NOT here — its gaps are identical by construction, so the ± applies to each one.)
_DELIBERATELY_BARE = {"dim_step_typ", "m_steplen_typ"}


def _approved_with_tolerance(plan):
    approved = {}
    for group in plan.groups:
        for dim in group.dims:
            if dim.tolerance is not None and dim.id is not None:
                approved[dim.id] = dim
    for ladder in plan.ladders:
        for rung in ladder.rungs:
            if rung.tolerance is not None and rung.id is not None:
                approved[rung.id] = rung
    return approved


#: The three shapes `_tol_suffix` emits: " ±t", " +hi -lo", and a fit class's " h6".
_TOLERANCE_SHAPE = re.compile(r"±|[+-]\d|\b[A-Za-z]+\d+$")


def _renders_a_tolerance(label: str) -> bool:
    """Does this label carry a tolerance SUFFIX?

    Not "does it contain a space" — that was the first version, and it made the guard blind to
    the pattern-pitch site, because a collapsed `4× 20` label already has one. A predicate that
    accepts the very labels most likely to hide a drop is worse than no predicate: it reports
    green while the sweep covers nothing (#1234 review r6).
    """
    return bool(_TOLERANCE_SHAPE.search(label.strip()))


def _drops(part_name, feature, param):
    """Annotations that claim an approved-toleranced id but render no suffix."""
    base = build_drawing(_PARTS[part_name](), title="T", number="N")
    model = base.model()
    target = next((f for f in model.features if f.kind == feature.kind and f == feature), None)
    if target is None:
        return []
    drawing = build_drawing(
        _PARTS[part_name](),
        model=model,
        decorations={(target, param.kind, param.role): _TOL},
        title="T",
        number="N",
    )
    approved = _approved_with_tolerance(compile_dimensions(drawing.model()))
    if not approved:
        return []
    dropped = []
    for name in sorted(drawing.registry.names()):
        claimed = drawing.registry.measurement_of(name)
        if not claimed or name in _DELIBERATELY_BARE:
            continue
        label = str(getattr(drawing.registry.named(name), "label", "") or "")
        if any(mid in approved for mid in claimed) and not _renders_a_tolerance(label):
            dropped.append(f"{part_name}/{feature.kind}.{param.role}: {name}={label!r}")
    return dropped


@pytest.mark.parametrize("part_name", sorted(_PARTS))
def test_no_approved_tolerance_is_dropped_by_a_renderer(part_name):
    base = build_drawing(_PARTS[part_name](), title="T", number="N")
    dropped: list[str] = []
    for feature in base.model().features:
        for param in feature.parameters():
            dropped += _drops(part_name, feature, param)
    assert not dropped, (
        f"{dropped}\n\nThe compiler approved a tolerance on these measurements and the "
        "annotation claiming them renders no suffix, so the drawing states the requirement "
        "less precisely than the author wrote it. Compose `_tol_suffix` into the label at the "
        "renderer — helpers discard a forwarded `tolerance=` whenever a label is present."
    )


def test_the_sweep_actually_decorates_something():
    """The precondition. A sweep that approves no toleranced dimension asserts nothing, and
    every part above must contribute — otherwise a fixture silently stops covering its site."""
    for part_name in sorted(_PARTS):
        base = build_drawing(_PARTS[part_name](), title="T", number="N")
        model = base.model()
        seen = 0
        for feature in model.features:
            for param in feature.parameters():
                drawing = build_drawing(
                    _PARTS[part_name](),
                    model=model,
                    decorations={(feature, param.kind, param.role): _TOL},
                    title="T",
                    number="N",
                )
                seen += len(_approved_with_tolerance(compile_dimensions(drawing.model())))
        assert seen, f"{part_name} approves no toleranced dimension; it guards nothing"
