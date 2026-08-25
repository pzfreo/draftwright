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
through **both** spellings of a `decorations=` key, builds, and joins what the compiler approved
against what the claiming annotation actually renders. It found three live sites in a single run
— the ones round 6 was still discovering by hand — and it fails on any new one.

Why the join is sound: `registry.measurement_of(name)` is the ADR 0010 seam, so an annotation
that claims a `DimensionId` is asserting it draws that measurement. If the compiler attached a
tolerance to that id and the label carries no suffix, the drawing states a requirement less
precisely than the author wrote it — silently, which is the whole failure mode.

It asserts two things, because "the suffix is missing" was only the half of the converse that
had been looked at (#1216 review r9):

1. every annotation carries one occurrence of the suffix per approved id it claims — counted,
   since a compound callout claims several and one ± satisfied a substring test for all of them;
2. every approved measurement is claimed by SOME annotation, or its absence is reported
   **against that measurement**. Two were neither: a mandatory overall extent starved out of a
   full strip by a leader, and a step rung the legibility gate discarded — each left the drawing
   short a dimension with the lint perfectly clean.

Scope of (2), because it is narrower than "everything the plan approves": `plan.groups` and
`plan.ladders`. Not `plan.contingencies`, whose whole design (ADR 0016 Amdt 5) is to be approved
and deliberately undrawn unless the primary representation places nothing; and not
`plan.locations`, which carry no tolerance to drop — `location` is not among any feature's
`parameters()`, so there is no key to author one against (measured: 204 compiled locations
across this corpus, 0 toleranced).
"""

from __future__ import annotations

from collections import Counter

import pytest
from build123d import Axis, Box, Cylinder, Pos, Rot
from build123d_drafting.helpers import Draft

from draftwright._core import _tol_suffix
from draftwright.builder import build_drawing, detect_part_model
from draftwright.model.compiled import compile_dimensions

_TOL = 0.05

#: `_tol_suffix` reads only `decimal_precision`; pin that to the builder preset's precision
#: without constructing an entire drawing during collection on every xdist worker.
_DRAFT = Draft(decimal_precision=1)


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


def _blind_holes():
    """BLIND holes: `HoleFeature.parameters()` emits the depth role only when a hole is not
    through, so a corpus of through holes leaves `bore.depth` unswept — which is how a reader
    for `depth_tol` shipped against a key the spec never wrote (#1234 review r8)."""
    part = Box(80, 60, 12)
    for x in (-20, 20):
        part -= Pos(x, 0, 5) * Cylinder(5, 4)
    return part


def _plate_with_holes():
    return Box(90, 60, 12) - Pos(-25, 12, 0) * Cylinder(4, 40) - Pos(25, -12, 0) * Cylinder(4, 40)


def _hole_grid():
    """A 3x2 rectangular grid: two pitch dims, one per lattice axis.

    `_add_grid_pitch_dims` handed `_pitch_text` `members[lo:hi+1]` — a slice of the IR's
    member ORDER, which walks a grid in neither lattice direction. The consecutive gaps in
    that slice mix row and column spacing, so a perfectly uniform grid read as jittered and
    every grid pitch withheld its authored tolerance (#1216 review r9)."""
    part = Box(140, 100, 12)
    for x in (-40, 0, 40):
        for y in (-25, 25):
            part -= Pos(x, y, 0) * Cylinder(4, 40)
    return part


def _uniform_staircase():
    """Four equal 12 mm rises: the one part that collapses to a `N x rise` REPRESENTATIVE.

    Without it `_DELIBERATELY_BARE` was inert — neither name in it occurred anywhere in this
    corpus, so the set excluded nothing and the deliberately-bare rule it documents was
    asserted by nothing (#1216 review r9)."""
    part = Box(160, 50, 12)
    for i in range(1, 4):
        part += Pos(-i * 10, 0, i * 12) * Box(160 - i * 20, 50, 12)
    return part


def _uniform_stepped_shaft():
    """Three equal 25 mm turned steps — the turned counterpart, `m_steplen_typ`."""
    shaft = None
    for i in range(3):
        seg = Pos(0, 0, i * 25 + 12.5) * Cylinder((30 - i * 5) / 2, 25)
        shaft = seg if shaft is None else shaft + seg
    return Rot(0, 90, 0) * shaft


_PARTS = {
    "staircase": _staircase,
    "crowded_staircase": _crowded_staircase,
    "linear_pattern": _linear_pattern,
    "short_first_rise": _short_first_rise,
    "turned_shaft": _turned_shaft,
    "blind_holes": _blind_holes,
    "chamfered_block": _chamfered_block,
    "counterbored_plate": _counterbored_plate,
    "plate_with_holes": _plate_with_holes,
    "hole_grid": _hole_grid,
    "uniform_staircase": _uniform_staircase,
    "uniform_stepped_shaft": _uniform_stepped_shaft,
}


@pytest.mark.parametrize("part_name", sorted(_PARTS))
def test_direct_detection_matches_built_model_features_for_sweep_parts(part_name):
    """The faster model-only path must preserve this sweep's recognised features."""
    built = tuple(build_drawing(_PARTS[part_name](), title="T", number="N").model().features)
    detected = tuple(detect_part_model(_PARTS[part_name]()).features)
    assert detected == built


#: The two shapes a `decorations=` key takes. BOTH are swept, because they are not the same
#: experiment: the role key tolerances one parameter, while the bare `(feature, kind)` key —
#: the public spelling in ADR 0011 — tolerances every role of that kind at once. Only the
#: second produces a label that must carry the SAME suffix twice (a counterbored callout's
#: bore and its recess), which is exactly what the old substring predicate could not tell
#: apart from carrying it once (#1216 review r9).
_KEY_MODES = ("role", "kind")

#: Annotations whose mark deliberately states no tolerance, with the reason. A collapsed
#: representative is the case: `N× rise` stands for levels that merely fall within 10% of each
#: other, so a ± would claim the author's tolerance of values that differ.
#:
#: A pattern's `N× pitch` is NOT excluded, but not for the reason this comment used to give —
#: "its gaps are identical by construction" is false, and shipping it is what put `3× 20 ±0.1`
#: over a 20.3 mm gap (the recogniser admits 2% jitter). `_pitch_text` decides per pattern, at
#: the drawn precision, and records `pattern_pitch_tolerance_withheld` when it cannot state it —
#: so a uniform pattern renders the suffix and this sweep is right to require it.
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


def _rendered_text(registry, name) -> str:
    """Everything this annotation puts on the page.

    A `label` for a dimension or callout — and a TABLE's cells, which have no label at all.
    Reading only `label` made both hole-table routes invisible to this sweep: a table claims
    `bore.diameter` through the same ADR 0010 seam a leader does, and printed the bare number
    while the guard read its empty label and passed (#1216 review r9).
    """
    obj = registry.named(name)
    label = getattr(obj, "label", None)
    if label:
        return str(label)
    rows = getattr(obj, "table_rows", None)
    if rows:
        return " ".join(str(cell) for row in rows for cell in row)
    return ""


def _missing(text: str, approved) -> list[str]:
    """The suffixes *text* owes and does not carry — counted, not searched for.

    ONE OCCURRENCE PER APPROVED ID. Every predicate before this one asked "does the label
    contain a tolerance", and each was green over a live drop:

    * "contains a space" — every collapsed `4× 20` label has one;
    * a regex for the three `_tol_suffix` shapes — its fit-class alternative (letters then
      digits) matches `C3` on every chamfer and `4× R5` on every fillet;
    * `_tol_suffix(...) in label` — one term's suffix stands in for another's on a compound
      callout that claims several ids.

    The third was measured, because "it could be satisfied by the wrong term" is the kind of
    claim that reads true and is not: suppressing `csink_dia_tol` in `callout_from_spec` makes
    `hc_plan0` print `⌀6 ±0.1 THRU ⌵ ⌀14 × 90°` while still claiming both the bore and the
    countersink, and `counterbored_plate-kind` PASSES on the substring predicate and FAILS on
    this one. Two honest qualifications: the isolation matters — a coarser mutation that
    suppresses the counterbore term as well is caught by both, because the second callout then
    loses its only suffix — and even for the isolated drop the FILE fails either way, because
    the `-role` parametrisation tolerances the countersink alone and its label then carries no
    suffix at all. So this predicate is strictly stronger and provably catches a case the
    substring one does not; it is not the difference between a green suite and a red one
    (#1216 review r9).

    The plan holds the tolerance, so the expected text is computable exactly rather than
    guessed — which is also the ADR 0016 Amdt 1 shape: compare to the compiler, not to a
    pattern.
    """
    want = Counter(_tol_suffix(dim.tolerance, _DRAFT) for dim in approved)
    return [f"{n}x{sfx!r}" for sfx, n in sorted(want.items()) if text.count(sfx) < n]


#: An issue with one of these codes is the engine SAYING a measurement is not on the sheet.
#: The join below is restricted to them because `measurement_ids` alone is far too weak a
#: signal: `feature_leader_crossing` and `feature_leader_fixed_ink_unverified` carry a
#: measurement on a callout that IS drawn, so a build with a crossing leader would have
#: "reported" every absence in it (#1216 review r10, F9). Matched by suffix as well as by name
#: so a drop code introduced tomorrow counts on the day it is written, which is the same choice
#: `linting/quality.py` makes and for the same reason.
_ABSENCE_CODES = ("placement_unsatisfiable",)
_ABSENCE_SUFFIXES = ("_dropped", "_withheld")


def _absence_reported(drawing) -> set:
    """The measurements the build SAID it did not place.

    Joined by `measurement_ids`, not by code alone. The first cut of this asked only whether
    the build recorded any issue whose code ended `_dropped` / `_withheld` / `_unsatisfiable`,
    which fails OPEN in the direction that matters: substituting an unrelated real code —
    `balloon_dropped`, which the orchestrator emits on exactly the dense sheets where a
    corridor starves — for the step-height report left `step_height.length` approved, drawn by
    nothing, and the guard green. Its docstring claimed the suffix match "fails safe"; it does
    not, and asserting that a specific measurement was reported is what does (#1216 review r9).
    """
    reported: set = set()
    for issue in drawing.registry.issues:
        if issue.code in _ABSENCE_CODES or issue.code.endswith(_ABSENCE_SUFFIXES):
            reported |= set(getattr(issue, "measurement_ids", ()) or ())
    return reported


def _sweep(part_name, model, feature, param, mode):
    """Build once with one decoration; the caller owns the immutable detected model."""
    key = (feature, param.kind, param.role) if mode == "role" else (feature, param.kind)
    drawing = build_drawing(
        _PARTS[part_name](),
        model=model,
        decorations={key: _TOL},
        title="T",
        number="N",
    )
    approved = _approved_with_tolerance(compile_dimensions(drawing.model()))
    if not approved:
        return [], [], 0
    where = f"{part_name}/{feature.kind}.{param.role}[{mode}]"
    dropped = []
    claimed_anywhere: set = set()
    for name in sorted(drawing.registry.names()):
        claimed = drawing.registry.measurement_of(name)
        if not claimed:
            continue
        claimed_anywhere |= set(claimed)
        if name in _DELIBERATELY_BARE:
            continue
        text = _rendered_text(drawing.registry, name)
        owed = _missing(text, [approved[m] for m in claimed if m in approved])
        if owed:
            dropped.append(f"{where}: {name}={text!r} owes {', '.join(owed)}")
    # The other direction. A measurement the compiler approved and NO annotation claims never
    # reached the drawing at all — which is worse than a missing suffix, and the sweep was
    # blind to it because it only ever looked at annotations that existed. Two live cases
    # (#1216 review r9): a mandatory overall extent starved out of a full strip, and a step
    # rung the legibility gate discarded — both lint-clean with the dimension absent.
    #
    # The assertion is "not silently", not "always drawn": whether a leader may starve an
    # overall extent, and whether a rung too short to dimension should escalate, are placement
    # policy (ADR 0014) and are not settled here. Reporting is not contingent on settling them.
    reported = _absence_reported(drawing)
    silent = sorted(
        str(m.parameter) for m in approved if m not in claimed_anywhere and m not in reported
    )
    unreported = (
        [f"{where}: {silent} approved, drawn by nothing, reported by nothing"] if silent else []
    )
    return dropped, unreported, len(approved)


@pytest.mark.parametrize("mode", _KEY_MODES)
@pytest.mark.parametrize("part_name", sorted(_PARTS))
def test_no_approved_tolerance_is_dropped_by_a_renderer(part_name, mode):
    model = detect_part_model(_PARTS[part_name]())
    dropped: list[str] = []
    unreported: list[str] = []
    approved = 0
    for feature in model.features:
        for param in feature.parameters():
            got_dropped, got_unreported, got_approved = _sweep(
                part_name, model, feature, param, mode
            )
            dropped += got_dropped
            unreported += got_unreported
            approved += got_approved
    # This precondition belongs to the property whose coverage it guards. Keeping it here
    # catches a vacuous fixture without rebuilding the entire role-key sweep in another test.
    assert approved, f"{part_name}[{mode}] approves no toleranced dimension; it guards nothing"
    assert not dropped, (
        f"{dropped}\n\nThe compiler approved a tolerance on these measurements and the "
        "annotation claiming them renders no suffix, so the drawing states the requirement "
        "less precisely than the author wrote it. Compose `_tol_suffix` into the label at the "
        "renderer — helpers discard a forwarded `tolerance=` whenever a label is present."
    )
    assert not unreported, (
        f"{unreported}\n\nThe compiler approved these measurements, no annotation claims "
        "them, and the build recorded nothing about it — so the requirement is absent from "
        "the drawing and absent from the lint. Draw it, or record an issue saying it was not "
        "drawn; vanishing is not one of the options."
    )


def test_the_deliberately_bare_names_are_reachable():
    """`_DELIBERATELY_BARE` must exclude something that OCCURS.

    It listed `dim_step_typ` and `m_steplen_typ` against a corpus containing neither, so the
    exclusion was inert: the collapse rule it documents — `N x rise` states one value for a
    whole run, so a ± there would claim the author's tolerance of every level at once — was
    asserted nowhere, and the rule could have been broken in either direction with this file
    green (#1216 review r9).
    """
    drawings = {
        part_name: build_drawing(_PARTS[part_name](), title="T", number="N")
        for part_name in ("uniform_staircase", "uniform_stepped_shaft")
    }
    seen = {name for drawing in drawings.values() for name in drawing.registry.names()}
    assert _DELIBERATELY_BARE <= seen, (
        f"{sorted(_DELIBERATELY_BARE - seen)} never occur in this corpus, so excluding them "
        "excludes nothing"
    )
    for part_name, drawing in drawings.items():
        for name in _DELIBERATELY_BARE & set(drawing.registry.names()):
            label = str(getattr(drawing.registry.named(name), "label", ""))
            assert label.startswith(("3\u00d7", "4\u00d7")), (
                f"{name}={label!r} is not a collapsed `N x` representative, so the reason "
                "this name is excluded no longer holds"
            )


# --------------------------------------------------------------------------------------
# Sites the sweep above cannot reach, each with the fixture that reaches it.
# --------------------------------------------------------------------------------------

#: Sixteen irregular perimeter bores — `_TABULATE_MIN_HOLES`, so the engine escalates the
#: scattered callouts into a hole table by itself. Copied from
#: `tests/test_issue_1144_transactional_hole_table.py` rather than imported: this file must
#: keep working when that one's fixtures change, and a shared constant would make a coverage
#: gap here look like an edit there.
_PERIMETER = (
    (-42.7, -27.4),
    (-15.1, -31.9),
    (11.0, -28.7),
    (44.8, -27.9),
    (-46.0, 32.2),
    (-16.8, 32.4),
    (16.8, 29.3),
    (45.3, 31.5),
    (-56.5, -17.6),
    (-51.6, -7.9),
    (-51.6, 7.5),
    (-51.2, 16.7),
    (50.7, -15.6),
    (56.4, -6.4),
    (50.8, 3.6),
    (55.1, 16.3),
)


def _tabulated_plate():
    part = Box(120, 80, 12)
    for index, (x, y) in enumerate(_PERIMETER):
        part -= Pos(x, y, 0) * Cylinder(1.0 + index * 0.15, 20)
    return part


def test_direct_detection_matches_built_model_features_for_tabulated_plate():
    built = tuple(
        build_drawing(_tabulated_plate(), page="A3", title="T", number="N").model().features
    )
    detected = tuple(detect_part_model(_tabulated_plate()).features)
    assert detected == built


def _toleranced_bore(part_fn, *, role="bore", kind="diameter"):
    """Detect *part_fn*, then build it with one bore toleranced."""
    model = detect_part_model(part_fn())
    feature, param = next(
        (f, p)
        for f in model.features
        if f.kind == "hole"
        for p in f.parameters()
        if p.role == role and p.kind == kind
    )
    drawing = build_drawing(
        part_fn(),
        page="A3",
        model=model,
        decorations={(feature, param.kind, param.role): _TOL},
        title="T",
        number="N",
    )
    approved = _approved_with_tolerance(compile_dimensions(drawing.model()))
    assert approved, "precondition: the decoration approved no toleranced dimension"
    return drawing, approved


def _table_text(drawing, name) -> str:
    table = drawing.registry.named(name)
    assert table is not None, f"precondition: {name} is not on the drawing"
    return " ".join(str(cell) for row in table.table_rows for cell in row)


def test_an_escalated_hole_table_prints_the_authored_tolerance():
    """Density must not cost a requirement.

    When sixteen scattered bores push the engine past `_TABULATE_MIN_HOLES` it withdraws the
    callouts and tabulates them — and the table's cells came from the plan's `value_text` with
    the tolerance left behind, so the SAME part printed `⌀2 ±0.1` beside a leader on a sparser
    sheet and a bare `ø2` in the table on a denser one (#1216 review r9). The sweep above could
    not see it twice over: no fixture there is dense enough to escalate, and its predicate read
    `label`, which a table does not have.
    """
    drawing, approved = _toleranced_bore(_tabulated_plate)
    text = _table_text(drawing, "hole_table_plan")
    claimed = drawing.registry.measurement_of("hole_table_plan")
    owed = _missing(text, [approved[m] for m in claimed if m in approved])
    assert not owed, f"hole_table_plan={text!r} owes {owed}"
    # The precondition, asserted rather than assumed: the table really is claiming the
    # measurement whose tolerance we just checked for.
    assert any(m in approved for m in claimed), (
        "precondition: the table claims none of the approved-toleranced measurements, so the "
        "assertion above is vacuous"
    )


def test_the_public_hole_table_verb_prints_the_authored_tolerance():
    """`add_hole_table()` is the same table by the other door.

    It formatted its own cells off the recognised geometry with `_fmt`, so it dropped the
    tolerance AND minted a value the compiler had already formatted — the ADR 0016 boundary in
    both directions at one site.
    """
    drawing, approved = _toleranced_bore(_plate_with_holes)
    assert drawing.add_hole_table("plan", balloons=False) is not None
    text = _table_text(drawing, "hole_table_plan")
    claimed = drawing.registry.measurement_of("hole_table_plan")
    assert any(m in approved for m in claimed), "precondition: the table claims nothing approved"
    owed = _missing(text, [approved[m] for m in claimed if m in approved])
    assert not owed, f"hole_table_plan={text!r} owes {owed}"


def _starved_extent_plate():
    """An 80x80 plate with a hexagonal boss.

    The A/F callout's leader runs the full depth of the plan view's below corridor, so
    `m_env_width` — force-kept, mandatory priority — solves to `strip_full` and the part's
    width is not dimensioned at all.
    """
    from build123d import BuildPart, BuildSketch, Plane, RegularPolygon, extrude

    plate = Box(80, 80, 10)
    with BuildPart() as boss:
        with BuildSketch(Plane.XY.offset(5)):
            RegularPolygon(20, 6)
        extrude(amount=10)
    return plate + boss.part


def test_a_starved_overall_extent_recovers_to_the_above_strip():
    """#1236: a feature leader spanning the below corridor must not cost the part its width.

    The hex boss's A/F leader fills the plan-below corridor, so `m_env_width` — force-kept,
    mandatory — solves to `strip_full` there. For three review rounds that was the end of it:
    the drop was first silent, then reported. Now it does what a starved slot or plate dim has
    always done — retries on the opposite strip after every corridor has drained — because no
    corridor-side fix reaches a leader (it is not a corridor candidate, so ordering cannot
    arbitrate against it) and an overall dimension above the view is ordinary drafting.
    """
    drawing = build_drawing(_starved_extent_plate(), title="T", number="N")
    assert "m_env_width" in drawing.registry.names(), (
        "the width no longer recovers: the fallthrough regressed, or the fixture stopped "
        "starving the below strip and this test asserts nothing"
    )
    # It really is ABOVE the view — the precondition that the below strip was starved, and
    # the fallthrough (not the ordinary corridor) placed it.
    bounds = drawing.view_bounds("plan")
    box = drawing.registry.named("m_env_width").label_bbox
    assert box[1] > bounds[3], (
        f"m_env_width's label sits at {box}, not above the plan view (top {bounds[3]}) — the "
        "below strip stopped starving, so this fixture no longer reaches the fallthrough"
    )
    assert not [i for i in drawing.lint() if i.code == "overall_dim_withheld"]
    assert not [i for i in drawing.lint() if i.severity == "error"]


def test_a_doubly_starved_extent_is_still_reported(monkeypatch):
    """Both strips full: the fallthrough fails and the drop must be reported, not vanish.

    Constructed by refusing the fallthrough's own placement call (matched on its trace label,
    every other call passes through untouched), because a fixture that genuinely fills BOTH
    strips of a view would be an elaborate layout accident waiting to rot. The assertions are
    the #1216 requirements: error severity so `passed` fails, the measurement attributed, the
    occupants named — and NOT `placement_unsatisfiable`, which `builder._is_required_scale_drop`
    matches by name and which made `build_drawing(scale=...)` raise (#1216 review r9/r10).
    """
    from draftwright.annotations import _common as common_mod
    from draftwright.annotations import from_model as fm

    real = common_mod.place_strip_candidates

    def refuse_fallthrough(*args, **kwargs):
        # Exact names, not a suffix: the slot fallthrough's label `slot_above_fallthrough`
        # also ends `_above_fallthrough`, so a suffix match would refuse a different pass on a
        # slotted fixture and the docstring's "every other call passes through untouched"
        # would be false (#1239 review F2).
        if kwargs.get("trace_label") in (
            "m_env_width_above_fallthrough",
            "m_env_depth_above_fallthrough",
        ):
            return args[4]  # the (name, build) pairs, unplaced — the real return shape
        return real(*args, **kwargs)

    monkeypatch.setattr(fm, "place_strip_candidates", refuse_fallthrough)
    drawing = build_drawing(_starved_extent_plate(), title="T", number="N")
    assert "m_env_width" not in drawing.registry.names(), (
        "precondition: the refusal did not apply — the fallthrough placed the width anyway, "
        "so nothing below tests the report"
    )
    reported = [i for i in drawing.lint() if i.code == "overall_dim_withheld"]
    assert reported, (
        f"both strips full and nothing said so: {[(i.severity, i.code) for i in drawing.lint()]}"
    )
    assert all(i.severity == "error" for i in reported)
    assert any("occupied by" in str(i.message) for i in reported)
    assert any(getattr(i, "measurement_ids", ()) for i in reported)
    assert not [i for i in drawing.lint() if i.code == "placement_unsatisfiable"]
    assert drawing.lint_summary()["passed"] is False
    build_drawing(_starved_extent_plate(), scale=1.0, title="T", number="N")  # must not raise


def _blind_plate_for_table():
    """Blind holes, so the table's DEPTH column carries a value rather than `THRU`."""
    part = Box(90, 60, 14)
    for x in (-25, 25):
        part -= Pos(x, 0, 6) * Cylinder(5, 4)
    return part


def test_direct_detection_matches_built_model_features_for_blind_table_plate():
    built = tuple(
        build_drawing(_blind_plate_for_table(), page="A3", title="T", number="N").model().features
    )
    detected = tuple(detect_part_model(_blind_plate_for_table()).features)
    assert detected == built


def test_the_hole_table_depth_column_prints_the_authored_tolerance():
    """The DEPTH cell, which the diameter tests do not reach.

    Both table tests above tolerance a bore diameter on a plate of THROUGH holes, so the depth
    column reads `THRU` and its own `_cell` call is never exercised: reverting it to `_fmt`
    left the full fast tier green (#1216 review r9, F3b). `HoleFeature.parameters()` emits the
    depth role only for a blind hole — the same corpus gap that shipped `depth_tol` as a reader
    with no writer.
    """
    drawing, approved = _toleranced_bore(_blind_plate_for_table, role="bore", kind="depth")
    assert drawing.add_hole_table("plan", balloons=False) is not None
    text = _table_text(drawing, "hole_table_plan")
    claimed = drawing.registry.measurement_of("hole_table_plan")
    depth_ids = [m for m in claimed if m in approved and str(m.parameter).endswith("depth")]
    assert depth_ids, (
        f"precondition: the table claims no bore depth, so nothing here reaches the DEPTH "
        f"cell: {[str(m.parameter) for m in claimed]}"
    )
    assert "THRU" not in text, f"precondition: these holes are not blind: {text!r}"
    owed = _missing(text, [approved[m] for m in depth_ids])
    assert not owed, f"hole_table_plan={text!r} owes {owed}"


def _partly_drawn_ladder():
    """Two blind recesses at very different depths: two approved rungs, one below the floor.

    The case a retraction keyed on "is this id claimed" cannot see. Both rungs carry the SAME
    `DimensionId` — `step_height.length`, one per feature, because there is no per-level
    identity (ADR 0016 Amdt 3) — so the drawn rung claims the id on behalf of the undrawn one.
    """
    part = Box(120, 70, 40)
    part -= Pos(-30, 0, 40 / 2 - 6 / 2 + 0.001) * Box(30, 30, 6)
    part -= Pos(30, 0, 40 / 2 - 30 / 2 + 0.001) * Box(30, 30, 30)
    return part


def test_a_withholding_is_retracted_only_when_every_mark_is_drawn():
    """A drop is only a drop if the measurement is still absent when the run finishes — and a
    collapsed id means "absent" has to be counted, not looked up.

    Three cases, because the first two versions of this each got one of them wrong:

    * `crowded_staircase` — five approved rungs, all five drawn (three in the enlarged detail).
      Recorded and never revisited, `step_dim_withheld` fired here and said they "are not
      dimensioned at this scale" (#1216 review r9, F5).
    * `_partly_drawn_ladder` — two approved rungs, ONE drawn. A retraction asking whether the
      id is claimed withdraws the report on the strength of that one, and the other rung is
      absent from the drawing and from the lint: the exact silence the report exists to end,
      restored by its own fix (#1216 review r10, F1).
    * `blind_holes` — one approved rung, none drawn. Without it, deleting the record entirely
      would pass.
    """
    recovered = build_drawing(_PARTS["crowded_staircase"](), title="T", number="N")
    ladder = compile_dimensions(recovered.model()).ladder("step_height")
    assert ladder is not None and len(ladder.rungs) > 1, "precondition: no multi-rung ladder"
    mid = ladder.rungs[0].id
    claimers = [
        name
        for name in recovered.registry.names()
        if mid in (recovered.registry.measurement_of(name) or ())
    ]
    # The precondition that the r9 version of this test could not state, because it compared
    # collapsed ids to each other: every rung has a mark of its own.
    assert len(claimers) == len(ladder.rungs), (
        f"precondition: {len(ladder.rungs)} approved rungs and {len(claimers)} annotations "
        f"claim them ({claimers}), so a standing withholding would be CORRECT here"
    )
    assert not [i for i in recovered.lint() if i.code == "step_dim_withheld"], (
        "every approved rung is on the sheet and the build still says one was withheld"
    )

    partial = build_drawing(_partly_drawn_ladder(), title="T", number="N")
    partial_ladder = compile_dimensions(partial.model()).ladder("step_height")
    assert partial_ladder is not None and len(partial_ladder.rungs) == 2, (
        f"precondition: expected two approved rungs, got "
        f"{None if partial_ladder is None else [r.value for r in partial_ladder.rungs]}"
    )
    partial_mid = partial_ladder.rungs[0].id
    assert partial_ladder.rungs[1].id == partial_mid, (
        "precondition: the two rungs no longer share one DimensionId, so this fixture no "
        "longer reaches the collapse it exists to guard"
    )
    partial_claimers = [
        name
        for name in partial.registry.names()
        if partial_mid in (partial.registry.measurement_of(name) or ())
    ]
    assert len(partial_claimers) == 1, (
        f"precondition: expected exactly one rung drawn, got {partial_claimers}"
    )
    assert [i for i in partial.lint() if i.code == "step_dim_withheld"], (
        "one of two approved rungs is on the sheet, the other is on neither the sheet nor the "
        "lint, and the id they share made that look like success"
    )

    unrecovered = build_drawing(_PARTS["blind_holes"](), title="T", number="N")
    assert [i for i in unrecovered.lint() if i.code == "step_dim_withheld"], (
        "the blind hole's floor is approved and drawn by nothing, and nothing said so"
    )


def test_the_public_hole_table_honours_an_authored_omission():
    """Suppression by omission reaches the table too (ADR 0016, #1216 review r10, F6).

    `add_hole_table()` fell back to formatting the recognised geometry whenever the compiler
    had no dimension for a cell — which cannot tell "no entry exists" from "the author left
    this out of their set". A script that dimensions a hole's LOCATION and not its diameter
    got the diameter printed anyway, in a table, by a verb that had just been changed to read
    from the plan. The escalated table has always emitted nothing for the same case.
    """
    from build123d import Align

    from draftwright import Sheet

    xyz_min = (Align.CENTER, Align.CENTER, Align.MIN)
    plate = Box(90, 60, 12, align=xyz_min)
    tool = Pos(-25, 12, 0) * Cylinder(4, 40, align=xyz_min)
    sheet = Sheet(plate - tool, title="T", number="N")
    sheet.hole(tool)
    sheet.envelope()
    sheet.dimension(sheet.features[0], "location")
    drawing = sheet.build()

    plan = compile_dimensions(drawing.model())
    # The precondition: the compiler really did refuse the diameter, and refused it as the
    # AUTHOR's omission rather than a planner rule.
    authored = {o.parameter_id for o in plan.diagnostics if o.authored}
    assert "bore.diameter" in authored, (
        f"precondition: bore.diameter is not an authored omission here: "
        f"{[(o.parameter_id, o.authored) for o in plan.diagnostics]}"
    )
    assert not [dim for group in plan.of_kind("hole") for dim in group.dims], (
        "precondition: the compiler approved a hole dimension, so the fallback is not reached"
    )

    assert drawing.add_hole_table("plan", balloons=False) is not None
    table = drawing.registry.named("hole_table_plan")
    header, *body = table.table_rows
    assert header[1] == "⌀" and len(body) == 1, table.table_rows
    # The CELL, not a substring search. The first version of this asserted `"6" not in text`
    # against a hole whose diameter is 8, so it could not fail — and did not, under the
    # mutation that removes the gate it guards.
    assert body[0][1] == "", (
        f"the author omitted the bore diameter and the table printed it anyway: {table.table_rows}"
    )


def test_the_declared_route_retracts_too():
    """Live and declared must agree about what was withheld (ADR 0011 round-trip).

    The retraction was added to `_auto_annotate` only, and `Drawing.finalize()` runs its own
    copy of the stage list — so the two paths failed in OPPOSITE directions: the auto build of
    `_crowded_staircase` retracted, and the declared build of the same part finalised with all
    five rungs on the sheet and the build still claiming one was withheld (#1216 review r10, F3).
    """
    drawing = build_drawing(_PARTS["crowded_staircase"](), auto_dims=False, title="T", number="N")
    model = drawing.model()
    step = next(f for f in model.features if f.kind == "step_level")
    with drawing.deferred():
        drawing.dimension(step, "length", role="step_height")

    ladder = compile_dimensions(model).ladder("step_height")
    mid = ladder.rungs[0].id
    claimers = [
        name
        for name in drawing.registry.names()
        if mid in (drawing.registry.measurement_of(name) or ())
    ]
    assert len(claimers) == len(ladder.rungs), (
        f"precondition: the declared route drew {len(claimers)} marks for "
        f"{len(ladder.rungs)} rungs ({claimers}), so a standing withholding would be correct"
    )
    assert not [i for i in drawing.lint() if i.code == "step_dim_withheld"], (
        "the declared route drew every rung and still reports one withheld"
    )


def test_only_an_absence_code_counts_as_reporting_an_absence():
    """`_absence_reported`'s vocabulary, asserted directly.

    Restricting the join to absence-shaped codes cannot be demonstrated by mutating the corpus
    — broadening a predicate only makes it more permissive, so every part still passes — but it
    is load-bearing all the same: `feature_leader_crossing` and
    `feature_leader_fixed_ink_unverified` carry a `measurement` on a callout that IS drawn
    (`annotations/leaders.py`), so a build with one crossing leader would otherwise have
    "reported" every absence in it (#1216 review r10, F9). Asserted here over a constructed
    issue list, which is the only place the distinction is visible.
    """
    from types import SimpleNamespace

    from draftwright.model.compiled import DimensionId

    model = detect_part_model(_PARTS["blind_holes"]())
    feature = next(f for f in model.features if f.kind == "hole")
    mid = DimensionId(feature, "bore.diameter")

    def _with(code):
        return SimpleNamespace(
            registry=SimpleNamespace(issues=(SimpleNamespace(code=code, measurement_ids=(mid,)),))
        )

    assert _absence_reported(_with("step_dim_withheld")) == {mid}
    assert _absence_reported(_with("callout_dropped")) == {mid}
    assert _absence_reported(_with("placement_unsatisfiable")) == {mid}
    for code in ("feature_leader_crossing", "feature_leader_fixed_ink_unverified"):
        assert _absence_reported(_with(code)) == set(), (
            f"{code} says a DRAWN annotation is hard to read, not that a measurement is "
            "missing, and it must not stand in for a report that one is"
        )


def _jittered_pattern():
    """Holes at −30, −10, 10.3, 30: the recogniser's 2% jitter admits them as ONE linear
    pattern, and the middle gap is 20.3 against a nominal of 20."""
    part = Box(140, 60, 12)
    for x in (-30.0, -10.0, 10.3, 30.0):
        part -= Pos(x, 0, 0) * Cylinder(3, 40)
    return part


def test_direct_detection_matches_built_model_features_for_jittered_pattern():
    built = tuple(build_drawing(_jittered_pattern(), title="T", number="N").model().features)
    detected = tuple(detect_part_model(_jittered_pattern()).features)
    assert detected == built


def test_a_jittered_pattern_withholds_its_pitch_tolerance_and_says_so():
    """The r8 finding, as a test rather than a probe — the fix shipped without one.

    #1234 printed `3× 20 ±0.1` over that 20.3 mm gap: a claim six times tighter than the part,
    on the strength of "gaps are identical by construction", which the recogniser's own
    tolerance disproves. The rule now is equal at the DRAWN precision; a pattern that fails it
    prints the bare collapse and records `pattern_pitch_tolerance_withheld` against the pitch's
    measurement, so an author who tolerances a jittered pitch is told rather than lied to.
    """
    model = detect_part_model(_jittered_pattern())
    pattern = next(f for f in model.features if f.kind == "pattern")
    param = next(pm for pm in pattern.parameters() if pm.role == "pitch")
    drawing = build_drawing(
        _jittered_pattern(),
        model=model,
        decorations={(pattern, param.kind, param.role): _TOL},
        title="T",
        number="N",
    )
    # The precondition: the tolerance was approved — this is a withholding, not a suppression.
    approved = _approved_with_tolerance(compile_dimensions(drawing.model()))
    pitch_ids = [m for m in approved if str(m.parameter).startswith("pitch")]
    assert pitch_ids, f"precondition: no toleranced pitch approved: {list(approved)}"

    labels = {
        name: str(drawing.registry.named(name).label)
        for name in drawing.registry.names()
        if name.startswith("dim_pitch")
    }
    assert labels, "precondition: no pitch dimension placed"
    assert all("±" not in label for label in labels.values()), (
        f"a jittered pattern claims its authored band of every gap: {labels}"
    )
    withheld = [i for i in drawing.registry.issues if i.code == "pattern_pitch_tolerance_withheld"]
    assert withheld, "the tolerance was withheld silently"
    assert any(set(i.measurement_ids) & set(pitch_ids) for i in withheld), (
        "the withholding is not attributed to the pitch it is about"
    )

    # The control: exactly uniform spacing keeps the suffix — so the assertion above is about
    # the jitter, not about pitch tolerances never rendering.
    uniform_model = detect_part_model(_linear_pattern())
    upattern = next(f for f in uniform_model.features if f.kind == "pattern")
    uparam = next(pm for pm in upattern.parameters() if pm.role == "pitch")
    udrawing = build_drawing(
        _linear_pattern(),
        model=uniform_model,
        decorations={(upattern, uparam.kind, uparam.role): _TOL},
        title="T",
        number="N",
    )
    ulabels = [
        str(udrawing.registry.named(n).label)
        for n in udrawing.registry.names()
        if n.startswith("dim_pitch")
    ]
    assert any("±" in label for label in ulabels), (
        f"a uniform pattern lost its pitch tolerance: {ulabels}"
    )
