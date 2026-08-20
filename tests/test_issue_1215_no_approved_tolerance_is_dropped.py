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

import pytest
from build123d import Axis, Box, Cylinder, Pos, Rot

from draftwright._core import _tol_suffix
from draftwright.builder import build_drawing
from draftwright.model.compiled import compile_dimensions

_TOL = 0.05

#: The draft the suffix is formatted against. `_tol_suffix` rounds to `decimal_precision`, so
#: the expected text must come from the same draft the renderer used — a different precision
#: would make the comparison a guess again.
_DRAFT = build_drawing(Box(20, 20, 20), title="T", number="N").draft


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


def _counterbored_plate():
    """A counterbored through hole: the compound callout, whose recess terms are separate
    parameters. Only the BORE's tolerance was threaded, so `⌀8 ±0.1 THRU ⌴ ⌀14` showed one ±
    and silently lost the other (#1234 review r7)."""
    from build123d import Cone

    part = Box(140, 60, 12)
    # Counterbored.
    part -= Pos(-35, 0, 0) * Cylinder(4, 40)
    part -= Pos(-35, 0, 3) * Cylinder(7, 6)
    # Countersunk — the csink terms are separate parameters again, and a fixture without one
    # leaves `csink_dia_tol` / `csink_angle_tol` unswept. This cone geometry is the one
    # `tests/test_issue_1143_hole_completeness.py` uses; my first attempt built a cone the
    # recogniser did not see as a countersink at all, so the sweep silently covered nothing.
    part -= Pos(35, 0, 0) * Cylinder(3, 40)
    part -= Pos(35, 0, 4) * Cone(3, 7, 4)
    return part


def _chamfered_block():
    """Chamfers label as `C3` and fillets as `4× R5` — letters then digits, which the guard's
    second predicate matched as if it were a fit class. Deleting the chamfer renderer's suffix
    left the authored ± off the sheet with the guard green (#1234 review r7)."""
    from build123d import chamfer

    part = Box(60, 40, 20)
    return chamfer(part.edges().filter_by(Axis.Z), 3)


def _plate_with_holes():
    return Box(90, 60, 12) - Pos(-25, 12, 0) * Cylinder(4, 40) - Pos(25, -12, 0) * Cylinder(4, 40)


_PARTS = {
    "staircase": _staircase,
    "crowded_staircase": _crowded_staircase,
    "linear_pattern": _linear_pattern,
    "short_first_rise": _short_first_rise,
    "turned_shaft": _turned_shaft,
    "chamfered_block": _chamfered_block,
    "counterbored_plate": _counterbored_plate,
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


def _renders(label: str, approved) -> bool:
    """Does this label carry the suffix the COMPILER says this measurement has?

    Not a pattern match on the rendered string. Two earlier predicates were guessed and both
    reported green over a live drop:

    * "contains a space" — every collapsed `4× 20` label has one, so the pattern-pitch site
      was invisible;
    * a regex for the three `_tol_suffix` shapes — its fit-class alternative (letters then
      digits) matches `C3` on every chamfer and `4× R5` on every fillet, so deleting the
      chamfer renderer's suffix left the authored ± off the sheet with the guard still green
      (#1234 review r6, r7).

    The plan already holds the tolerance, so the expected text is computable exactly. Comparing
    to the compiler rather than to a guess is also the ADR 0016 Amdt 1 shape.
    """
    return _tol_suffix(approved.tolerance, _DRAFT) in label


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
        # PER ID, not "does it render some tolerance". One annotation can claim several
        # approved-toleranced ids — a compound hole callout does — and rendering the suffix
        # for one of them satisfied the whole check, hiding the others (#1234 review r7).
        for mid in claimed:
            got = approved.get(mid)
            if got is not None and not _renders(label, got):
                want = _tol_suffix(got.tolerance, _DRAFT)
                dropped.append(
                    f"{part_name}/{feature.kind}.{param.role}: {name}={label!r} want{want!r}"
                )
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
