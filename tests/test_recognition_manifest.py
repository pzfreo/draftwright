"""Every public recogniser family is accounted for, and the migrated ones run once (#1019).

ADR 0017 makes ``RecognitionResult`` the one inventory a build recognises into. That claim is
only worth anything if it is *complete* — an aggregate that quietly omits half the families
still leaves the engine scanning the same solid from four places, which is the state ADR 0017
was written to end.

So the manifest is fail-closed on the package surface itself: adding a ``recognise_*`` to
``b123d_recognisers.__all__`` without deciding whether the aggregate owns it fails here.
The decision is forced at the moment the recogniser is written, when the author knows why.
"""

import importlib
import pkgutil
from contextlib import contextmanager
from dataclasses import fields
from importlib.metadata import version
from math import cos, radians, sin
from pathlib import Path

import b123d_recognisers as recognition
import b123d_recognisers.result as result_module
from b123d_recognisers import (
    RecognitionResult,
    analyse_cylinders,
    build_recognition_result,
    recognise_angled_steps,
    recognise_bosses,
    recognise_chamfers,
    recognise_channels,
    recognise_countersinks,
    recognise_double_d_bores,
    recognise_fillets,
    recognise_flats,
    recognise_grooves,
    recognise_hole_patterns,
    recognise_holes,
    recognise_passages,
    recognise_plates,
    recognise_pocket_patterns,
    recognise_pockets,
    recognise_polygonal_bosses,
    recognise_polygonal_stock,
    recognise_prismatic_pockets,
    recognise_rectangular_pads,
    recognise_repeating_radial_profiles,
    recognise_risers,
    recognise_section_passages,
    recognise_slot_patterns,
    recognise_slots,
    recognise_turned_steps,
    step_level_records,
)
from b123d_recognisers.result import DEFERRED, MIGRATED, Deferral
from build123d import (
    Align,
    Axis,
    Box,
    BuildPart,
    BuildSketch,
    Cone,
    Cylinder,
    Plane,
    Pos,
    RegularPolygon,
    Rot,
    chamfer,
    extrude,
    import_step,
)
from conftest import recognition_family_calls

import draftwright.model.detect as detect_module
from draftwright import Sheet, build_drawing
from draftwright.analysis import _analyse
from draftwright.annotations.orchestrator import build_model


def _pocketed_plate():
    return Box(80, 60, 20) - Pos(20, 0, 5) * Box(20, 10, 12)


def _bolt_circle_with_countersinks():
    part = Box(100, 100, 12)
    for i in range(6):
        a = radians(i * 60)
        part -= Pos(30 * cos(a), 30 * sin(a), 0) * Cylinder(3, 12)
        part -= Pos(30 * cos(a), 30 * sin(a), 4) * Cone(3, 7, 4)
    return part


def _slot_grid_plate():
    part = Box(180, 130, 20)
    for i in range(2):  # a 2×3 lattice — rect_grid needs n >= 6
        for j in range(3):
            part -= Pos((i - 0.5) * 44, (j - 1) * 34, 0) * Box(24, 8, 20)
    return part


def _pocket_grid_plate():
    part = Box(140, 110, 20)
    for i in range(2):
        for j in range(3):
            part -= Pos((i - 0.5) * 40, (j - 1) * 30, 7) * Box(8, 10, 6)
    return part


def _stepped_shaft():
    part = Cylinder(20, 30) + Pos(0, 0, 30) * Cylinder(14, 30)
    part -= Pos(0, 0, 10) * (Cylinder(20, 5) - Cylinder(16, 5))  # groove
    part -= Pos(20, 0, 45) * Box(20, 40, 30)  # flat
    part -= Pos(0, 0, 52) * Cylinder(5, 20)  # bore
    return part


def _padded_plate():
    return Box(120, 90, 16) + Pos(0, -30, 10) * Box(30, 20, 4)


def _polygonal_boss_plate():
    return Box(100, 80, 10) + Pos(0, 0, 5) * extrude(RegularPolygon(20, 6), 30)


def _polygonal_stock():
    return extrude(RegularPolygon(20, 6), 30)


def _double_d_plate():
    centre = (Align.CENTER, Align.CENTER, Align.CENTER)
    cutter = Cylinder(5, 20, align=centre) & Box(7.2, 20, 30, align=centre)
    return Box(30, 30, 10, align=centre) - cutter


def _chamfered_filleted_block():
    """A prismatic block with a chamfered edge and a filleted one — the only fixture that
    exercises both edge-treatment inventories with content (#1028/#1254)."""
    from build123d import Axis, chamfer, fillet

    box = Box(60, 40, 30)
    box = chamfer(box.edges().filter_by(Axis.Z).sort_by(Axis.X)[-1], 4)
    return fillet(box.edges().filter_by(Axis.Z).sort_by(Axis.X)[0], 5)


def _rotational_shaft():
    """A plain cylinder — rotational, so the classification-gated families return empty.
    Without this the `rotational` field is False everywhere and the oracle never states the
    gate."""
    return Cylinder(20, 60)


def _stepped_block():
    """A full-span step — the shape `recognise_risers` exists to find (#1025).

    None of the other fixtures produce a riser: a pad, a pocket and a bolt circle all have
    BOUNDED walls, which the full-span test deliberately rejects, so without this the
    aggregate's `risers` field would be compared () to () everywhere.
    """
    return Box(80, 40, 10) + Pos(-20, 0, 10) * Box(40, 40, 12)


def _u_channel():
    return (
        Box(50, 50, 12)
        + Pos(0, -18.75, 15) * Box(50, 12.5, 18)
        + Pos(0, 18.75, 15) * Box(50, 12.5, 18)
    )


def _repeating_wheel():
    return import_step(str(Path(__file__).parent / "fixtures" / "issue_1058_wheel_rh.step"))


#: Between them these cover every RecognitionResult field with a NON-EMPTY inventory, which
#: is what makes the equality assertions in the oracle below discriminating rather than
#: comparing () to ().
#: Built lazily, not at import: a module-level list of solids pays five OCC builds during
#: *collection*, in every pytest process that imports this file — smoke tier and every xdist
#: worker included.
def _angled_blind_step():
    """A partial-width ramp whose blind ends close as triangles, plus a full-length bevel.

    Both families on one part, each claimed by exactly one recogniser, so the fixture pins the
    boundary between them rather than just the new one — and it exercises the reconciliation
    directly: called on their own the recognisers report 2 chamfers and 1 angled step, and the
    aggregate keeps 1 chamfer, because `_reconcile.chamfers_that_are_not_angled_steps` drops the
    chamfer whose face the step owns (#1244).

    Built from a rotated box rather than from the package's own golden construction, which
    sketches on `Plane.XZ`. That construction is **not stable across the build123d versions this
    project supports**: measured, the identical script yields volume 61600.0 with 8 faces under
    0.11.1 and 62275.0 with 9 under 0.10.0, and the angled step is recognised only under 0.11 —
    which is how CI's Python 3.10 shard (build123d <0.11) failed on this test's own
    "no fixture produces a non-empty ['angled_steps']" precondition while every 3.13/3.14 shard
    and both platform canaries passed. `both=True` does not fix it; the difference is in the
    sketch plane, not the extrude direction.

    This construction is byte-identical on both: 10 faces, volume 60975.00, on 0.10.0 and 0.11.1
    alike. The assertions below state that, so a future version that moves it fails here rather
    than silently emptying the inventory.
    """
    base = Box(50, 50, 25, align=(Align.MIN, Align.MIN, Align.MIN))
    ramped = base - (Pos(0, 25, 25) * Rot(0, -33.69, 0) * Box(40, 12, 20))
    bevel = [
        edge
        for edge in ramped.edges().filter_by(Axis.X)
        if abs(edge.center().Z - 25) < 1e-6 and abs(edge.center().Y - 50) < 1e-6
    ]
    assert bevel, "the bevel edge moved; this fixture no longer carries a chamfer"
    part = chamfer(bevel[0], length=3)
    # 0.01, not exact: the ramp angle is 33.69 degrees, so the volume is 60974.9987... and a
    # `:.2f` probe reading of "60975.00" is what made the first tolerance here 1e-6 and wrong.
    # The bound only has to catch the failure mode actually seen — the version difference moved
    # this construction's predecessor by 675 mm3, five orders of magnitude larger.
    assert len(part.faces()) == 10 and abs(part.volume - 60975.0) < 0.01, (
        f"the fixture's geometry moved under build123d {version('build123d')}: "
        f"{len(part.faces())} faces, volume {part.volume} — it is version-sensitive again"
    )
    return part


def _hexagonal_passage_plate():
    """A hexagonal THROUGH opening — a passage, the internal counterpart to polygonal stock."""
    plate = Box(120, 80, 20)
    with BuildPart() as tool:
        with BuildSketch(Plane.XY.offset(-20)):
            RegularPolygon(12, 6)
        extrude(amount=60)
    return plate - Pos(-30, 0, 0) * tool.part


def _hexagonal_pocket_plate():
    """A hexagonal BLIND recess (prismatic pocket) beside a rectangular one.

    The rectangular recess is deliberate: both `recognise_pockets` and
    `recognise_prismatic_pockets` claim it when called directly, and the aggregate keeps the
    `Pocket` — so this fixture carries the prismatic-pocket reconciliation as well as a
    non-empty inventory.
    """
    plate = Box(120, 80, 20)
    with BuildPart() as tool:
        with BuildSketch(Plane.XY.offset(4)):
            RegularPolygon(12, 6)
        extrude(amount=20)
    return plate - Pos(-30, 0, 0) * tool.part - Pos(30, 0, 4) * Box(30, 24, 20)


_ORACLE_FIXTURES = [
    ("bolt circle with countersinks", _bolt_circle_with_countersinks),
    ("angled blind step", _angled_blind_step),
    ("hexagonal passage", _hexagonal_passage_plate),
    ("hexagonal pocket", _hexagonal_pocket_plate),
    ("slot grid", _slot_grid_plate),
    ("pocket grid", _pocket_grid_plate),
    ("stepped shaft", _stepped_shaft),
    ("padded plate", _padded_plate),
    ("polygonal boss plate", _polygonal_boss_plate),
    ("polygonal stock", _polygonal_stock),
    ("double-D plate", _double_d_plate),
    ("stepped block", _stepped_block),
    ("U-channel", _u_channel),
    ("chamfered+filleted block", _chamfered_filleted_block),
    ("rotational shaft", _rotational_shaft),
    ("repeating radial wheel", _repeating_wheel),
]


def _public_families() -> set[str]:
    """Every recogniser the package exports.

    Taken from ``__all__`` rather than from ``dir()``: the manifest is a promise about the
    *public* surface, and a module-private helper is free to come and go.
    """
    return {name for name in recognition.__all__ if name.startswith("recognise_")}


_PROJECTED_COMPATIBILITY = frozenset({"recognise_passages"})


def test_every_public_recogniser_is_migrated_or_deferred_with_a_reason():
    families = _public_families()
    classified = MIGRATED | DEFERRED.keys() | _PROJECTED_COMPATIBILITY

    unclassified = families - classified
    assert not unclassified, (
        f"recogniser(s) missing from the ADR 0017 manifest: {sorted(unclassified)}. "
        "Add each to MIGRATED (and run it in build_recognition_result) or to DEFERRED "
        "with the design constraint that stops it."
    )
    stale = classified - families
    assert not stale, f"manifest names non-existent recogniser(s): {sorted(stale)}"
    assert not (MIGRATED & DEFERRED.keys()), "a family cannot be both migrated and deferred"
    assert not (_PROJECTED_COMPATIBILITY & (MIGRATED | DEFERRED.keys())), (
        "a projected compatibility API cannot also be a physical migrated/deferred family"
    )


def test_every_public_recogniser_reaches_the_package_surface():
    """The manifest is fail-closed *on ``__all__``*, so it is only as closed as the export
    convention. A recogniser defined in a submodule but never exported would be invisible to
    every check above — classified nowhere, and free to be re-scanned from anywhere."""
    # Walked from the filesystem, not from ``vars(recognition)``: the latter holds only the
    # submodules something in this session's import graph happened to pull in, so a new
    # recogniser module imported by (say) score.py alone would be invisible.
    defined = set()
    # ``walk_packages``, not ``iter_modules``: the latter is non-recursive, so a future
    # sub-package of recognisers would escape the check the manifest's fail-closedness rests
    # on.
    for info in pkgutil.walk_packages(recognition.__path__, prefix="b123d_recognisers."):
        module = importlib.import_module(info.name)
        defined |= {
            attr
            for attr, obj in vars(module).items()
            if attr.startswith("recognise_") and getattr(obj, "__module__", "") == module.__name__
        }

    unexported = defined - _public_families()
    assert not unexported, (
        f"recogniser(s) defined but not exported: {sorted(unexported)}. "
        "Add each to b123d_recognisers.__all__ — the ADR 0017 manifest is checked "
        "against that surface, so an unexported recogniser escapes classification."
    )


def test_every_deferral_names_a_constraint_and_a_way_out():
    """A deferral is a constraint to remove, not a note. 'Not got to it yet' is neither.

    Reason codes rather than prose (review of #1021): the paragraphs this replaces went
    stale the moment the migration they described was reverted, and a wrong-but-detailed
    reason passes any check a string can support. What a code *can* carry is a named
    blocker — the issue that removes the constraint — and the classification codes are
    additionally checked against a running build below.
    """
    for family, deferred in DEFERRED.items():
        assert isinstance(deferred.reason, Deferral), f"{family}: reason is not a code"
        if deferred.reason is not Deferral.NO_INDEPENDENT_CONSUMER:
            assert deferred.blocker, (
                f"{family}: deferred with no blocker issue. Either name the issue that "
                "removes the constraint, or the deferral is 'not got to it yet'."
            )


def test_no_deferred_family_is_reachable_from_the_orchestration():
    """The other half of the manifest, checked mechanically rather than in prose: a family
    the aggregate says it does not own must not be *callable* from the aggregate. If
    ``result.py`` imports one, the two halves of the manifest contradict each other and the
    DEFERRED entry is a comment rather than a constraint."""
    reachable = sorted(name for name in DEFERRED if hasattr(result_module, name))
    assert not reachable, (
        f"DEFERRED but imported by the orchestration: {reachable}. "
        "Either migrate the family (move it to MIGRATED and run it in "
        "build_recognition_result) or stop importing it."
    )
    # By behaviour as well as by import form: `import chamfers as c; c.recognise_chamfers()`
    # reaches a DEFERRED family without ever making it an attribute of this module.
    with _counting_every_family() as counts:
        build_recognition_result(_pocketed_plate())
    ran = sorted(name for name in DEFERRED if name in counts)
    assert not ran, f"the orchestration called DEFERRED famil(ies): {ran}"


#: Families the ORCHESTRATION runs only for the class that consumes them (#1028). Listed here
#: rather than derived from DEFERRED because they are no longer deferred: they are MIGRATED
#: *and* gated, which is the distinction #1028 established — owning a family and always
#: running it are different things. Turned parts are the excluded class for plates and angled
#: prismatic steps. Chamfers and fillets left this set in b123d-recognisers 0.2.9 because the
#: aggregate now recognises their conical/toroidal turned forms (#1254/#1281).
_CLASSIFICATION_GATED = (
    "recognise_angled_steps",
    "recognise_plates",
)


def test_a_classification_gated_family_does_not_run_for_the_excluded_class():
    """The remaining classification gate needs teeth.

    Before #1028 they were DEFERRED with a reason claiming ``build_part_model`` skips them
    for turned parts, and this test checked the claim against the running engine — because a
    detailed-but-wrong reason is not hypothetical; one shipped in this epic's first cut and
    was caught by human review, not by a test.

    Now the gate lives in the ORCHESTRATION, so that is what is checked. Deleting it makes
    every turned build scan for a prismatic-only result the model discards, which is the cost
    the deferral was protecting against — and this goes red.

    Two turned fixtures, because plates gates on a CONJUNCTION (not rotational *and* no
    turned profile) and the stepped shaft satisfies both — so on it alone, weakening the gate
    to either half still passes. The plain cylinder has no shoulders, so its profile is
    ``None`` while it is still rotational: it separates the two clauses and fails if the
    rotational half is dropped.
    """
    assert _CLASSIFICATION_GATED, "no family is classification-gated — check vacuous"
    assert set(_CLASSIFICATION_GATED) <= MIGRATED, (
        "a gated family left MIGRATED — if the aggregate no longer owns it, this test is "
        "checking a gate that is not the one doing the work"
    )
    for label, part in (("stepped shaft", _stepped_shaft()), ("plain cylinder", Cylinder(20, 60))):
        with _counting_every_family() as counts:
            build_drawing(part, repair=False)

        ungated = sorted(name for name in _CLASSIFICATION_GATED if name in counts)
        assert not ungated, (
            f"{ungated} ran for a turned part ({label}). The aggregate is supposed to gate "
            "these on the classification it carries, so a turned build never scans for a "
            "result the model would discard — that gate is what made migrating them free."
        )


def test_a_gated_family_still_runs_for_the_class_that_consumes_it():
    """The counterexample that stops the gate guard being satisfied by never running them.

    A gate that excludes everything passes the test above perfectly. What must also hold is
    that a PRISMATIC build gets every gated family exactly once from the aggregate.
    """
    with _counting_every_family() as counts:
        build_drawing(_pocketed_plate(), repair=False)

    for name in _CLASSIFICATION_GATED:
        assert counts.get(name) == 1, (
            f"{name} ran {counts.get(name, 0)}× on a prismatic build — the gate is supposed "
            "to exclude the turned class, not the class that consumes it"
        )


def test_the_migrated_families_are_the_ones_the_orchestration_actually_runs():
    """Guards the manifest against the failure that makes it worthless: a name listed as
    MIGRATED that ``build_recognition_result`` never calls. Checked by *running* the
    orchestration with each family instrumented, not by reading its source.

    Counted by code object rather than by patching ``result_module``'s bindings. The binding
    form was not merely fragile here, it was wrong: #1022 migrated ``recognise_face_levels``,
    which the orchestration reaches INDIRECTLY through ``step_level_records``, so there is no name
    on this module to patch and the spy raised ``AttributeError``. A family is migrated if the
    orchestration calls it, not if it happens to be called by a line in this file.
    """
    # A part carrying every migrated feature kind would be enormous; the claim under test
    # is that the ORCHESTRATION invokes each family, which holds for any solid — a family
    # that finds nothing was still asked.
    with recognition_family_calls(MIGRATED) as called:
        result_module.build_recognition_result(Box(40, 30, 10))

    assert set(called) == MIGRATED, (
        f"listed as migrated but never called: {sorted(MIGRATED - set(called))}"
    )
    # MIGRATED's docstring says "exactly once, per orchestration". A second call is a
    # rediscovered substrate, which is the cost the aggregate exists to remove.
    repeated = {name: n for name, n in called.items() if n != 1}
    assert not repeated, f"the orchestration ran a family more than once: {repeated}"


def _expected_inventory(part, *, rotational: bool = False) -> dict:
    """What each :class:`RecognitionResult` field should hold for *part*, computed by calling
    the recognisers directly — the oracle the aggregate is judged against.

    The remaining classification-gated fields are ``()`` for a rotational part, mirroring
    the orchestration's gate (#1028). Chamfers and fillets are deliberately unconditional
    since b123d-recognisers 0.2.9 added turned edge treatments (#1254/#1281).
    """
    cyls = analyse_cylinders(part)
    csinks = recognise_countersinks(part)
    holes = recognise_holes(part, cyls=cyls, csinks=csinks)
    double_d_bores = recognise_double_d_bores(part)
    slots = recognise_slots(part)
    pockets = recognise_pockets(part)
    channels = recognise_channels(part)
    return {
        "cylinders": (tuple(cyls[0]), tuple(cyls[1])),
        "countersinks": tuple(csinks),
        "holes": tuple(holes),
        "double_d_bores": tuple(double_d_bores),
        "hole_patterns": tuple(recognise_hole_patterns(holes)),
        "bosses": tuple(recognise_bosses(part, cyls=cyls)),
        "polygonal_bosses": tuple(recognise_polygonal_bosses(part)),
        "polygonal_stock": tuple(recognise_polygonal_stock(part)),
        "channels": tuple(channels),
        "slots": tuple(slots),
        "slot_patterns": tuple(recognise_slot_patterns(slots)),
        "grooves": tuple(recognise_grooves(part, cyls=cyls)),
        "flats": tuple(recognise_flats(part, cyls=cyls)),
        "pockets": tuple(pockets),
        "pocket_patterns": tuple(recognise_pocket_patterns(pockets)),
        "pads": tuple(recognise_rectangular_pads(part)),
        "repeating_radial_profiles": tuple(recognise_repeating_radial_profiles(part)),
        "turned_steps": tuple(recognise_turned_steps(part, cyls=cyls)),
        "step_levels": tuple(step_level_records(part)),
        "risers": tuple(recognise_risers(part)),
        "rotational": rotational,
        "chamfers": tuple(recognise_chamfers(part)),
        "fillets": tuple(recognise_fillets(part)),
        "plates": (
            tuple(recognise_plates(part))
            if not rotational and not recognise_turned_steps(part, cyls=cyls)
            else ()
        ),
        # Recognised and carried, consumed by nothing yet — draftwright has taken no position
        # on these three (#1245/#1246/#1247, declared `unsupported` in the capability
        # contract). The aggregate must still hold what its recognisers produced: an inventory
        # nobody converts is exactly where an empty tuple would go unnoticed (#1244).
        "angled_steps": tuple(recognise_angled_steps(part)) if not rotational else (),
        "passages": tuple(recognise_passages(part)),
        "section_passages": tuple(recognise_section_passages(part)),
        "prismatic_pockets": tuple(recognise_prismatic_pockets(part)),
    }


#: Fields where a DIRECT recogniser call is not a valid oracle for the aggregate, because the
#: aggregate applies a cross-family reconciliation the direct call deliberately precedes:
#:
#: Passage compatibility and its authoritative SectionPassage source are both observed before
#: aggregate cross-family ownership removes occurrences that yield to a Slot.
#:
#: A four-wall passage yields to the more directly dimensioned `Slot`;
#: `_reconcile.prismatic_pockets_that_are_not_pockets` keeps the `Pocket`; and
#: `_reconcile.chamfers_that_are_not_angled_steps` drops a chamfer whose face a step owns —
#: which is why `chamfers` is here too, latent until a fixture carries an angled step (#1244).
#:
#: For these the assertion is SUBSET, not equality: the aggregate may drop a candidate to
#: another family, and may never invent one. Equality is still demanded everywhere else, so the
#: "ran it and stored ()" failure this test exists for is still caught for 19 of 22 fields.
_RECONCILED_FIELDS = frozenset(
    {"angled_steps", "chamfers", "passages", "prismatic_pockets", "section_passages"}
)


def test_the_aggregate_carries_what_its_recognisers_returned():
    """Running a family is not the same as keeping its answer.

    Without this, an aggregate that calls every recogniser and then stores ``()`` passes
    every other test here: the orchestration guard only counts calls, and the rescan guard
    is *satisfied* by threading an empty inventory through — which is worse than a rescan,
    because ``build_part_model`` reads an empty list as "the caller supplied an inventory"
    rather than "detect it", so the feature silently disappears from the drawing.

    Every field, not a sample: the fixtures below are chosen so each one is non-empty
    somewhere, and the field list is checked against the dataclass so a field added in a
    future evidence-gated ADR 0017 slice cannot join unasserted.
    """
    covered: set[str] = set()
    for name, build in _ORACLE_FIXTURES:
        part = build()
        # The rotational fixture drives the gate; everything else is prismatic. Passed
        # explicitly because the aggregate takes it as an argument rather than deriving it
        # (recognition does not classify — #1028).
        rotational = name == "rotational shaft"
        expected = _expected_inventory(part, rotational=rotational)
        assert set(expected) == {f.name for f in fields(RecognitionResult)}, (
            "RecognitionResult grew a field with no oracle — add it to _expected_inventory"
        )
        result = build_recognition_result(part, rotational=rotational)
        for field, want in expected.items():
            got = getattr(result, field)
            if field in _RECONCILED_FIELDS:
                assert set(got) <= set(want), (
                    f"{name}: the aggregate's {field} contains records its recogniser never "
                    f"produced — reconciliation may only DROP candidates, never invent them"
                )
            else:
                assert got == want, (
                    f"{name}: the aggregate's {field} is not what its recogniser returned"
                )
            if got:
                covered.add(field)

    unexercised = {f.name for f in fields(RecognitionResult)} - covered
    assert not unexercised, (
        f"no fixture produces a non-empty {sorted(unexercised)}, so the equality above "
        "compares () to () and would not notice the answer being discarded"
    )


#: How many times a family may run during ONE automatic build. Anything absent budgets 1, and
#: the map is now EMPTY: #1025 split the last family that needed a second scan into
#: ``recognise_risers`` plus a pure projection, so no family has a reason to run twice.
#: An entry appearing here again needs a reason, not a bigger number.
_BUILD_CALL_BUDGET: dict[str, int] = {}

#: How many times a family may run during ONE ``lint()`` of an already-built drawing. EMPTY
#: since #1025: critique projects the aggregate's riser evidence over its own levels instead
#: of rescanning, so linting a built drawing now recognises NOTHING. That is phase 0's third
#: guard. Any entry here is lint growing back a recognition owner.
_LINT_CALL_BUDGET: dict[str, int] = {}


@contextmanager
def _counting_every_family():
    """Count calls to every manifest family, by code object (see ``conftest.counting_calls``).

    Counted at the function itself rather than by patching the modules that import it. A
    spy has to be installed on every binding, and a binding form nobody listed is silent —
    which is how four review rounds each found one more (unlisted modules, then aliased
    imports, then containers). A code object cannot be re-bound, so there is genuinely
    nowhere to call these from that the count does not see.
    """
    with recognition_family_calls(MIGRATED | DEFERRED.keys()) as counts:
        yield counts


def test_an_automatic_build_runs_each_family_exactly_once_and_lint_runs_no_migrated_one():
    """ADR 0017's headline guard: **one** recognition orchestration per automatic build —
    and linting the built drawing re-runs no family the aggregate owns.

    Not "lint recognises nothing": it still runs its own step-shoulder scan, once per lint
    (``_LINT_CALL_BUDGET``). That is the deferral #1025 removes, and budgeting it is the
    honest form — asserting zero here would need a lint-side memo, which review of #1021
    rejected as a second recognition owner.

    Counted end to end at each recogniser's definition, so it covers every call site rather
    than the one `test_model_construction_does_not_rescan_a_migrated_family` watches. A
    second `build_recognition_result` anywhere in the pipeline, or a consumer bypassing the
    aggregate, shows up here.

    Two things it does NOT catch, both covered elsewhere. A DEFERRED family newly appearing
    on the detected path: the budget defaults to 1, and every DEFERRED family already runs
    during a prismatic build (seven once, ``recognise_step_shoulders`` twice), so "ran once"
    is indistinguishable from "newly ran once" — only the declared path has a family-count
    ratchet (:func:`test_a_declared_build_does_not_grow_new_recognition`). And the substrate
    half of the ADR 0017 guard, "shared substrates not rescanned": ``analyse_cylinders`` is
    deliberately not a ``recognise_*``, so ``_counting_every_family`` cannot see it.
    ``tests/test_detect_once.py::test_cylinder_scan_runs_once_per_build`` (#703) owns that
    half, and #1027 fixed it — its spy had a hand-kept module list that ``recognition.result``
    and ``linting.coverage`` were both missing from, on live paths.
    """
    for label, part in (("prismatic", _pocketed_plate()), ("turned", _stepped_shaft())):
        with _counting_every_family() as counts:
            drawing = build_drawing(part)
            after_build = dict(counts)
            counts.clear()
            drawing.lint()
            after_lint = dict(counts)

        # The remaining prismatic-only families are excluded for a turned part BY DESIGN
        # (#1028/#1254) — owning a family and always running it are different things, and
        # `test_a_classification_gated_family_does_not_run_for_the_excluded_class` owns that
        # claim. Everything else must run exactly once whatever the part class.
        expected_once = MIGRATED - (set(_CLASSIFICATION_GATED) if label == "turned" else set())
        for family in expected_once:
            assert after_build.get(family) == 1, (
                f"{label}: {family} is MIGRATED but ran {after_build.get(family, 0)}× "
                "during the build — the aggregate is supposed to be the only place it runs"
            )
        over = {n: c for n, c in after_build.items() if c > _BUILD_CALL_BUDGET.get(n, 1)}
        assert not over, (
            f"{label}: {over} exceeded the per-build budget. A repeat is either a second "
            "orchestration or a consumer bypassing the aggregate; if it is neither, the "
            "budget entry needs a reason, not a bigger number."
        )
        relinted = {n: c for n, c in after_lint.items() if c > _LINT_CALL_BUDGET.get(n, 0)}
        assert not relinted, (
            f"{label}: linting a built drawing re-ran {relinted} — the build's inventory "
            "is what lint must judge against, not a fresh scan. A new entry here is a new "
            "recognition owner in lint, which is the thing #1025 exists to remove."
        )


def test_a_declared_build_recognises_nothing():
    """ADR 0011 / ADR 0017 §6, now a pass mark rather than a ratchet (#1022).

    This started life as ``test_a_declared_build_does_not_grow_new_recognition``, pinning
    eleven families per build so the debt could only shrink. #1022 took all eleven to zero:
    ``_analyse`` gates the aggregate on whether a model was declared, sizing sources the
    turned profile and step ladder from the declaration, and the repair loop stopped asking
    for the feature-coverage half of lint it never used.

    Zero rather than a budget, because there is no longer a family with a reason to run here
    — anything appearing is a new consumer that has not been threaded through the
    declaration, and it should have to justify itself in review rather than nudge a number.

    Detected-path coverage lives in
    :func:`test_an_automatic_build_runs_each_family_exactly_once_and_lint_runs_no_migrated_one`,
    which is what stops this being satisfiable by breaking recognition outright.
    """
    with _counting_every_family() as ran:
        sheet = Sheet(Box(80, 60, 20)).auto_dimensions()
        sheet.envelope()
        sheet.build()

    assert dict(ran) == {}, (
        f"a declared build recognised {dict(ran)}. ADR 0011 says a caller-supplied model "
        "skips detection — each of these scanned a solid whose features the caller had "
        "already stated, and threw the answer away."
    )


def test_model_construction_does_not_rescan_a_migrated_family(tmp_path):
    """The point of the aggregate. ``build_part_model`` accepts most migrated inventories as
    an argument (``countersinks`` has no parameter — it is spared a rescan by riding inside
    the ``holes is None`` fallback; ``turned_steps`` arrives derived, as ``prof=``); if
    analysis stops threading one through, detection silently scans the solid a
    second time and the ADR 0017 single-inventory claim is false while every test still passes.

    Counts calls made through ``model.detect``'s own globals, so it sees exactly the rescan.
    """
    rescanned: dict[str, int] = {}
    originals = {
        name: getattr(detect_module, name) for name in MIGRATED if hasattr(detect_module, name)
    }
    assert originals, "expected model.detect to import the migrated recognisers"

    def spy(name, orig):
        def wrapper(*args, **kwargs):
            rescanned[name] = rescanned.get(name, 0) + 1
            return orig(*args, **kwargs)

        return wrapper

    for name, orig in originals.items():
        setattr(detect_module, name, spy(name, orig))
    try:
        # Prismatic and turned take different branches through build_part_model, and a
        # rescan on either is a rescan.
        build_drawing(_pocketed_plate())
        build_drawing(Cylinder(20, 60) - Pos(0, 0, 30) * Cylinder(6, 20))
        # The engine's OTHER build_part_model call site (#1019). It is not reachable from
        # build_drawing today, so a rescan there is latent rather than live — which is
        # exactly why it needs its own guard: nothing else would ever notice.
        build_model(_analyse(_pocketed_plate(), "t", "1", "±0.1", "t", tmp_path / "unused.svg"))
    finally:
        for name, orig in originals.items():
            setattr(detect_module, name, orig)

    assert not rescanned, (
        f"model construction re-ran migrated recogniser(s): {sorted(rescanned)} — "
        "analysis must thread the RecognitionResult inventory into build_part_model"
    )
