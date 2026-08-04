"""Every public recogniser family is accounted for, and the migrated ones run once (#1019).

ADR 0017 makes ``RecognitionResult`` the one inventory a build recognises into. That claim is
only worth anything if it is *complete* — an aggregate that quietly omits half the families
still leaves the engine scanning the same solid from four places, which is the state ADR 0017
was written to end.

So the manifest is fail-closed on the package surface itself: adding a ``recognise_*`` to
``draftwright.recognition.__all__`` without deciding whether the aggregate owns it fails here.
The decision is forced at the moment the recogniser is written, when the author knows why.
"""

import importlib
import pkgutil
from contextlib import contextmanager
from dataclasses import fields
from math import cos, radians, sin

from build123d import Box, Cone, Cylinder, Pos
from conftest import counting_calls

import draftwright.model.detect as detect_module
import draftwright.recognition as recognition
import draftwright.recognition.result as result_module
from draftwright import Sheet, build_drawing
from draftwright.analysis import _analyse
from draftwright.annotations.orchestrator import build_model
from draftwright.recognition import (
    RecognitionResult,
    analyse_cylinders,
    build_recognition_result,
    recognise_bosses,
    recognise_countersinks,
    recognise_hole_patterns,
    recognise_holes,
    recognise_pocket_patterns,
    recognise_pockets,
    recognise_rectangular_pads,
    recognise_slots,
    recognise_turned_steps,
)
from draftwright.recognition.result import DEFERRED, MIGRATED, Deferral


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


#: Between them these cover every RecognitionResult field with a NON-EMPTY inventory, which
#: is what makes the equality assertions in the oracle below discriminating rather than
#: comparing () to ().
#: Built lazily, not at import: a module-level list of solids pays five OCC builds during
#: *collection*, in every pytest process that imports this file — smoke tier and every xdist
#: worker included.
_ORACLE_FIXTURES = [
    ("bolt circle with countersinks", _bolt_circle_with_countersinks),
    ("slot grid", _slot_grid_plate),
    ("pocket grid", _pocket_grid_plate),
    ("stepped shaft", _stepped_shaft),
    ("padded plate", _padded_plate),
]


def _public_families() -> set[str]:
    """Every recogniser the package exports.

    Taken from ``__all__`` rather than from ``dir()``: the manifest is a promise about the
    *public* surface, and a module-private helper is free to come and go.
    """
    return {name for name in recognition.__all__ if name.startswith("recognise_")}


def test_every_public_recogniser_is_migrated_or_deferred_with_a_reason():
    families = _public_families()
    classified = MIGRATED | DEFERRED.keys()

    unclassified = families - classified
    assert not unclassified, (
        f"recogniser(s) missing from the ADR 0017 manifest: {sorted(unclassified)}. "
        "Add each to MIGRATED (and run it in build_recognition_result) or to DEFERRED "
        "with the design constraint that stops it."
    )
    stale = classified - families
    assert not stale, f"manifest names non-existent recogniser(s): {sorted(stale)}"
    assert not (MIGRATED & DEFERRED.keys()), "a family cannot be both migrated and deferred"


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
    for info in pkgutil.walk_packages(recognition.__path__, prefix="draftwright.recognition."):
        module = importlib.import_module(info.name)
        defined |= {
            attr
            for attr, obj in vars(module).items()
            if attr.startswith("recognise_") and getattr(obj, "__module__", "") == module.__name__
        }

    unexported = defined - _public_families()
    assert not unexported, (
        f"recogniser(s) defined but not exported: {sorted(unexported)}. "
        "Add each to draftwright.recognition.__all__ — the ADR 0017 manifest is checked "
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


#: The DEFERRED families claiming the classification gate, taken from the manifest rather
#: than listed here — a family given the code later must be checked by it too. Turned parts
#: are the excluded class for all of them: chamfers and fillets are gated on
#: ``rotational is None``, plates additionally on ``prof is None``.
_CLASSIFICATION_GATED = tuple(
    name for name, d in DEFERRED.items() if d.reason is Deferral.CLASSIFICATION_GATED
)


def test_a_classification_gated_reason_is_true_of_the_build():
    """``test_every_deferred_reason_states_a_constraint`` can only measure a reason's
    LENGTH, and a detailed-but-wrong reason is not hypothetical — one shipped in this
    epic's first cut and was caught by human review, not by a test.

    So every family carrying the classification code is checked against the running engine:
    each claims ``build_part_model`` does not call it for a turned part, and that is either
    true of a turned build or the code is fiction. Deleting the gate in ``detect.py``
    makes the family run here and this goes red — which is the whole point, because the
    gate is the stated cost of hoisting the family into the aggregate.

    Two turned fixtures, because plates claims a CONJUNCTION (``prof is None`` *and*
    ``rotational is None``) and the stepped shaft satisfies both — so on it alone, weakening
    the gate to either half still passes. The plain cylinder has no shoulders, so ``prof`` is
    ``None`` while ``rotational`` is not: it separates the two clauses and fails if the
    ``rotational`` half is dropped.
    """
    assert _CLASSIFICATION_GATED, "no family carries the classification code — check vacuous"
    for label, part in (("stepped shaft", _stepped_shaft()), ("plain cylinder", Cylinder(20, 60))):
        with _counting_every_family() as counts:
            build_drawing(part, repair=False)

        ungated = sorted(name for name in _CLASSIFICATION_GATED if name in counts)
        assert not ungated, (
            f"{ungated} ran for a turned part ({label}), but DEFERRED says each is gated "
            "against exactly that classification. Either the gate in model/detect.py went "
            "away — in which case the family is now an unconditional scan and belongs in "
            "MIGRATED — or the reason needs rewriting to say what really stops it."
        )


def test_the_migrated_families_are_the_ones_the_orchestration_actually_runs():
    """Guards the manifest against the failure that makes it worthless: a name listed as
    MIGRATED that ``build_recognition_result`` never calls. Checked by *running* the
    orchestration with each family instrumented, not by reading its source."""
    called: dict[str, int] = {}
    originals = {name: getattr(result_module, name) for name in MIGRATED}

    def spy(name, orig):
        def wrapper(*args, **kwargs):
            called[name] = called.get(name, 0) + 1
            return orig(*args, **kwargs)

        return wrapper

    for name, orig in originals.items():
        setattr(result_module, name, spy(name, orig))
    try:
        # A part carrying every migrated feature kind would be enormous; the claim under test
        # is that the ORCHESTRATION invokes each family, which holds for any solid — a family
        # that finds nothing was still asked.
        result_module.build_recognition_result(Box(40, 30, 10))
    finally:
        for name, orig in originals.items():
            setattr(result_module, name, orig)

    assert set(called) == MIGRATED, (
        f"listed as migrated but never called: {sorted(MIGRATED - set(called))}"
    )
    # MIGRATED's docstring says "exactly once, per orchestration". A second call is a
    # rediscovered substrate, which is the cost the aggregate exists to remove.
    repeated = {name: n for name, n in called.items() if n != 1}
    assert not repeated, f"the orchestration ran a family more than once: {repeated}"


def _expected_inventory(part) -> dict:
    """What each :class:`RecognitionResult` field should hold for *part*, computed by calling
    the recognisers directly — the oracle the aggregate is judged against."""
    cyls = analyse_cylinders(part)
    csinks = recognise_countersinks(part)
    holes = recognise_holes(part, cyls=cyls, csinks=csinks)
    slots = recognise_slots(part)
    pockets = recognise_pockets(part)
    return {
        "cylinders": (tuple(cyls[0]), tuple(cyls[1])),
        "countersinks": tuple(csinks),
        "holes": tuple(holes),
        "hole_patterns": tuple(recognise_hole_patterns(holes)),
        "bosses": tuple(recognise_bosses(part, cyls=cyls)),
        "slots": tuple(slots),
        "pockets": tuple(pockets),
        "pocket_patterns": tuple(recognise_pocket_patterns(pockets)),
        "pads": tuple(recognise_rectangular_pads(part)),
        "turned_steps": tuple(recognise_turned_steps(part, cyls=cyls)),
    }


def test_the_aggregate_carries_what_its_recognisers_returned():
    """Running a family is not the same as keeping its answer.

    Without this, an aggregate that calls every recogniser and then stores ``()`` passes
    every other test here: the orchestration guard only counts calls, and the rescan guard
    is *satisfied* by threading an empty inventory through — which is worse than a rescan,
    because ``build_part_model`` reads an empty list as "the caller supplied an inventory"
    rather than "detect it", so the feature silently disappears from the drawing.

    Every field, not a sample: the fixtures below are chosen so each one is non-empty
    somewhere, and the field list is checked against the dataclass so a field added in a
    later ADR 0017 slice cannot join unasserted.
    """
    covered: set[str] = set()
    for name, build in _ORACLE_FIXTURES:
        part = build()
        expected = _expected_inventory(part)
        assert set(expected) == {f.name for f in fields(RecognitionResult)}, (
            "RecognitionResult grew a field with no oracle — add it to _expected_inventory"
        )
        result = build_recognition_result(part)
        for field, want in expected.items():
            assert getattr(result, field) == want, (
                f"{name}: the aggregate's {field} is not what its recogniser returned"
            )
            if want:
                covered.add(field)

    unexercised = {f.name for f in fields(RecognitionResult)} - covered
    assert not unexercised, (
        f"no fixture produces a non-empty {sorted(unexercised)}, so the equality above "
        "compares () to () and would not notice the answer being discarded"
    )


#: How many times a family may run during ONE automatic build. Anything absent budgets 1.
#: The single entry is the DEFERRED family with two callers that may ask different questions
#: (see ``recognition.result.DEFERRED``) — model construction with ownership-filtered levels,
#: and the critique with unfiltered ones. On this fixture the two level sets happen to
#: coincide, so the second scan is pure waste rather than a second answer; the budget records
#: the deferral's real price and pins it so it cannot quietly rise.
_BUILD_CALL_BUDGET = {"recognise_step_shoulders": 2}

#: How many times a family may run during ONE ``lint()`` of an already-built drawing. The
#: single entry is the same deferral: the critique recognises its own shoulder inventory on
#: every pass, because ADR 0015 forbids it taking one from the model and there is nowhere
#: below both ``model/`` and ``linting/`` for a shared answer to live. #1025 makes it a
#: projection over the aggregate's evidence and this budget goes to zero. Anything else
#: appearing here is lint growing a scan it did not have.
_LINT_CALL_BUDGET = {"recognise_step_shoulders": 1}


@contextmanager
def _counting_every_family():
    """Count calls to every manifest family, by code object (see ``conftest.counting_calls``).

    Counted at the function itself rather than by patching the modules that import it. A
    spy has to be installed on every binding, and a binding form nobody listed is silent —
    which is how four review rounds each found one more (unlisted modules, then aliased
    imports, then containers). A code object cannot be re-bound, so there is genuinely
    nowhere to call these from that the count does not see.
    """
    with counting_calls(
        {name: getattr(recognition, name) for name in MIGRATED | DEFERRED.keys()}
    ) as counts:
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

        for family in MIGRATED:
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


#: What a declared build recognises today, per family. A ratchet baseline, not a target:
#: ADR 0011 says a declared model skips detection, and #1022 takes every entry here to zero.
#: Pinned per family rather than as a total because a total permits SUBSTITUTION — one
#: family dropping out while another appears leaves the count unmoved and the guard silent,
#: which is precisely the quiet migration this test exists to catch.
_DECLARED_BUILD_BASELINE = {
    "recognise_bosses": 1,
    "recognise_countersinks": 1,
    "recognise_face_levels": 1,
    "recognise_hole_patterns": 1,
    "recognise_holes": 1,
    "recognise_pocket_patterns": 1,
    "recognise_pockets": 1,
    "recognise_rectangular_pads": 1,
    "recognise_slots": 1,
    "recognise_step_shoulders": 1,
    "recognise_turned_steps": 1,
}


def test_a_declared_build_does_not_grow_new_recognition():
    """A ratchet on the ADR 0011 / ADR 0017 §6 debt, not a pass mark.

    A declared build is supposed to perform **no** recognition, and today it performs a
    lot — page and scale selection read ``step_zs`` and the turned profile off the
    aggregate even when the model was declared, so the gate needs sizing reworked first
    (#1022). What this pins is the *direction of travel*: the manifest forces a
    MIGRATED/DEFERRED decision for each new family but has no opinion on which way that
    decision loads this path, so without a ratchet here every future migration quietly makes
    the declared path do more work and no test says a word.

    It earned its keep by catching a regression in the PR that introduced it: an earlier
    revision migrated three ``BUILD_MODEL_ONLY`` families and took this path from 11 to 14
    for no detected-path saving at all. They are deferred instead.

    Monotone per family, not by cardinality. Counting ``len(ran) <= 11`` and
    ``sum(ran.values()) <= 11`` — the form this replaces — is satisfied by substitution: drop
    one family, add another, and 11/11 still holds while the declared path is doing entirely
    different work. Each entry may only go DOWN, to absent, which is what #1022 does to all
    of them at once.
    """
    with _counting_every_family() as ran:
        sheet = Sheet(Box(80, 60, 20)).auto_dimensions()
        sheet.envelope()
        sheet.build()

    added = sorted(set(ran) - set(_DECLARED_BUILD_BASELINE))
    assert not added, (
        f"a declared build now recognises {added}, which it did not before. ADR 0011 says a "
        "declared model skips detection — this baseline is a ratchet on the way to zero "
        "(#1022), so a family may leave it but none may join."
    )
    grew = {
        n: (c, _DECLARED_BUILD_BASELINE[n])
        for n, c in ran.items()
        if c > _DECLARED_BUILD_BASELINE[n]
    }
    assert not grew, (
        f"a declared build now calls {grew} (actual, baseline) — a family called more often "
        "is new work on this path just as surely as a new family is."
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
