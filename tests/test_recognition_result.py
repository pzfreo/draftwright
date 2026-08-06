"""ADR 0017 phase 1: one explicit recognition result."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from build123d import Axis, Box, Cylinder, Pos, chamfer, fillet
from conftest import counting_calls

from draftwright import build_drawing
from draftwright.model import build_part_model
from draftwright.recognition import (
    FaceLevel,
    RecognitionResult,
    build_recognition_result,
    recognise_plates,
)


def _plate_with_holes():
    plate = Box(60, 40, 8)
    return plate - Pos(-15, 0, 0) * Cylinder(3, 8) - Pos(15, 0, 0) * Cylinder(3, 8)


def test_recognition_result_is_frozen_and_owns_tuple_inventories():
    result = build_recognition_result(_plate_with_holes())

    assert isinstance(result, RecognitionResult)
    assert len(result.holes) == 2
    assert isinstance(result.holes, tuple)
    with pytest.raises(FrozenInstanceError):
        result.holes = ()


def test_built_drawing_exposes_its_recognition_result_without_private_state(monkeypatch):
    """The accessor hands back THE build's result, not an equal-looking rebuild — every
    value assertion below is satisfied by a second orchestration nobody asked for.

    Asserted through the public surface: comparing against ``drawing._analysis.recognition``
    would be exactly the test-side reach-through ``test_private_test_attr_reads`` exists to
    stop. Stable identity rules out a rebuild-per-call; the poisoned orchestration rules out
    a rebuild-then-cache, which stable identity alone would accept.
    """
    import draftwright.analysis as analysis_module
    import draftwright.recognition.result as result_module

    drawing = build_drawing(_plate_with_holes())

    result = drawing.recognition()
    assert isinstance(result, RecognitionResult)
    assert len(result.holes) == 2
    assert result.cylinders

    def forbidden(*args, **kwargs):
        raise AssertionError("Drawing.recognition() re-ran recognition instead of handing back")

    monkeypatch.setattr(result_module, "build_recognition_result", forbidden)
    monkeypatch.setattr(analysis_module, "build_recognition_result", forbidden)
    assert drawing.recognition() is result


def test_orchestrator_injects_each_shared_dependency_once(monkeypatch):
    import draftwright.recognition.result as result_module

    calls: dict[str, int] = {}
    cylinders = ([{"axis": "z"}], [{"axis": "x"}])
    countersinks = [object()]
    holes = [object()]
    slots = [object()]
    pockets = [object()]

    def fake_cylinders(part):
        calls["cylinders"] = calls.get("cylinders", 0) + 1
        return cylinders

    def counted(name, returns):
        """A stand-in that records the call and returns *returns*."""

        def fake(part, **kwargs):
            calls[name] = calls.get(name, 0) + 1
            return returns

        return fake

    def cyl_consumer(name, returns):
        """A stand-in for a recogniser the orchestration hands the cylinder substrate.

        Asserts the SAME list objects arrive — the point of the aggregate is that no
        recogniser rediscovers a dependency, and an equal-but-fresh pair would mean it did.
        """

        def fake(part, *, cyls=None, **kwargs):
            calls[name] = calls.get(name, 0) + 1
            assert cyls is not None, f"{name} was not given the cylinder substrate"
            assert cyls[0] is cylinders[0] and cyls[1] is cylinders[1]
            return returns

        return fake

    def derived(name, source, returns):
        """A stand-in for a pattern recogniser, which must be handed its members."""

        def fake(records):
            calls[name] = calls.get(name, 0) + 1
            assert records is source, f"{name} was not given the accepted member records"
            return returns

        return fake

    def fake_holes(part, *, cyls=None, csinks=None):
        calls["holes"] = calls.get("holes", 0) + 1
        assert cyls[0] is cylinders[0] and cyls[1] is cylinders[1]
        assert csinks is countersinks
        return holes

    monkeypatch.setattr(result_module, "analyse_cylinders", fake_cylinders)
    monkeypatch.setattr(
        result_module, "recognise_countersinks", counted("countersinks", countersinks)
    )
    monkeypatch.setattr(result_module, "recognise_holes", fake_holes)
    monkeypatch.setattr(result_module, "recognise_hole_patterns", derived("patterns", holes, []))
    monkeypatch.setattr(result_module, "recognise_bosses", cyl_consumer("bosses", []))
    monkeypatch.setattr(result_module, "recognise_channels", counted("channels", []))
    monkeypatch.setattr(result_module, "recognise_slots", counted("slots", slots))
    monkeypatch.setattr(result_module, "recognise_pockets", counted("pockets", pockets))
    monkeypatch.setattr(
        result_module, "recognise_pocket_patterns", derived("pocket_patterns", pockets, [])
    )
    monkeypatch.setattr(result_module, "recognise_rectangular_pads", counted("pads", []))
    monkeypatch.setattr(result_module, "recognise_turned_steps", cyl_consumer("turned_steps", []))
    # The area-filtered records retain face support (#915); `step_ladder()` projects their Z
    # values for sizing/critique. It takes the part only — no substrate identity to assert.
    level_records = [FaceLevel(4.0, (0.0, 8.0), (0.0, 6.0)), FaceLevel(9.0)]
    monkeypatch.setattr(result_module, "step_level_records", counted("step_levels", level_records))
    # #1026 hoisted these three out of `build_part_model`. `slot_patterns` is derived, so it
    # must be handed the accepted slot records rather than rediscovering them; the other two
    # take the shared cylinder substrate.
    monkeypatch.setattr(
        result_module, "recognise_slot_patterns", derived("slot_patterns", slots, [])
    )
    monkeypatch.setattr(result_module, "recognise_grooves", cyl_consumer("grooves", []))
    monkeypatch.setattr(result_module, "recognise_flats", cyl_consumer("flats", []))
    # #1025: the level-free riser scan. Takes the part only — the level set that used to
    # make this family caller-specific now lives in `project_step_shoulders`.
    monkeypatch.setattr(result_module, "recognise_risers", counted("risers", []))
    # #1028: gated on the classification the aggregate carries. This orchestration runs
    # with the default `rotational=False`, so all three are expected to run.
    monkeypatch.setattr(result_module, "recognise_chamfers", counted("chamfers", []))
    monkeypatch.setattr(result_module, "recognise_fillets", counted("fillets", []))
    monkeypatch.setattr(result_module, "recognise_plates", counted("plates", []))

    built = result_module.build_recognition_result(object())

    # ONCE, not merely "at least once": a second call is a rediscovered substrate wearing
    # a correct answer's clothes. Both halves — WHICH families ran, and how often.
    assert set(calls) == {
        "cylinders",
        "countersinks",
        "holes",
        "patterns",
        "bosses",
        "channels",
        "slots",
        "pockets",
        "pocket_patterns",
        "pads",
        "turned_steps",
        "step_levels",
        "slot_patterns",
        "grooves",
        "flats",
        "risers",
        "chamfers",
        "fillets",
        "plates",
    }, f"the orchestration ran a different set of families: {sorted(calls)}"
    assert set(calls.values()) == {1}, f"a family ran more than once: {calls}"
    assert built.holes == tuple(holes)
    assert built.step_levels == tuple(level_records)
    bb = SimpleNamespace(min=SimpleNamespace(Z=0.0), max=SimpleNamespace(Z=10.0))
    assert built.step_ladder(bb) == [4.0, 9.0]


def _grooved_flatted_shaft():
    """A turned shaft carrying both a groove and a machined flat."""
    part = Cylinder(20, 30) + Pos(0, 0, 30) * Cylinder(14, 30)
    part -= Pos(0, 0, 10) * (Cylinder(20, 5) - Cylinder(16, 5))  # groove
    part -= Pos(20, 0, 45) * Box(20, 40, 30)  # flat
    return part


def _chamfered_filleted_block():
    """One chamfered edge and one filleted edge — the #1028 gated inventories, with content."""
    box = Box(60, 40, 30)
    box = chamfer(box.edges().filter_by(Axis.Z).sort_by(Axis.X)[-1], 4)
    return fillet(box.edges().filter_by(Axis.Z).sort_by(Axis.X)[0], 5)


def _l_bracket():
    """A base slab plus an upright wall — two plates on different axes."""
    return Box(80, 40, 8) + Pos(-36, 0, 24) * Box(8, 40, 40)


def _slot_lattice_plate():
    """A 2x3 slot lattice — `rect_grid` needs n >= 6 to form a slot pattern."""
    part = Box(180, 130, 20)
    for i in range(2):
        for j in range(3):
            part -= Pos((i - 0.5) * 44, (j - 1) * 34, 0) * Box(24, 8, 20)
    return part


@pytest.mark.parametrize(
    ("name", "build"),
    [
        ("grooved+flatted shaft", _grooved_flatted_shaft),
        ("slot lattice", _slot_lattice_plate),
        # The #1028 families. Without these the equivalence guard could not see a defect where
        # an injected non-empty inventory is consumed as empty — `None` rescans, `()` must
        # suppress, and only a fixture that actually produces the feature tells them apart
        # (Codex #1033 r2).
        ("chamfered+filleted block", _chamfered_filleted_block),
        ("L-bracket plates", _l_bracket),
    ],
)
def test_injecting_the_aggregate_builds_the_same_model_as_detecting(name, build):
    """The migration's actual claim: feeding ``build_part_model`` the aggregate's inventories
    produces the model it would have detected for itself (#1026).

    The manifest's value oracle cannot check this. It computes the expected inventory the same
    way the orchestration does, so if the *inputs* were wrong — a recogniser needing a filtered
    or reconciled set rather than the aggregate's raw accepted records — production and oracle
    would agree on the same wrong answer and the guard would pass (Codex #1030 r1).

    This compares the two paths end to end instead: detect-for-yourself against
    inject-from-the-aggregate. It is the equivalence every hoist in ADR 0017 assumes and the
    one that silently stops holding when a recogniser's contract drifts.
    """
    part = build()
    rec = build_recognition_result(part)

    detected = build_part_model(part)
    injected = build_part_model(
        part,
        holes=list(rec.holes),
        patterns=list(rec.hole_patterns),
        bosses=list(rec.bosses),
        slots=list(rec.slots),
        slot_patterns=list(rec.slot_patterns),
        grooves=list(rec.grooves),
        flats=list(rec.flats),
        pockets=list(rec.pockets),
        pocket_patterns=list(rec.pocket_patterns),
        pads=list(rec.pads),
        step_zs=rec.step_ladder(part.bounding_box()),
        face_levels=list(rec.step_levels),
        risers=list(rec.risers),
        chamfers=list(rec.chamfers),
        fillets=list(rec.fillets),
        plates=list(rec.plates),
        cyls=rec.cylinders,
    )

    assert [f.kind for f in injected.features] == [f.kind for f in detected.features], (
        f"{name}: injecting the aggregate changed WHICH features the model carries"
    )
    assert injected.features == detected.features, (
        f"{name}: injecting the aggregate changed the features' values"
    )


def test_the_gate_is_the_orchestrations_not_the_call_sites():
    """#1028's actual claim: a family can be MIGRATED *and* not always run.

    The three classification-gated families were deferred because hoisting them
    unconditionally would scan every turned build for a result `build_part_model` discards.
    The resolution was not to keep them out of the aggregate but to let the aggregate decide
    once — so the gate must live in `build_recognition_result` itself, and be visible in what
    it returns rather than inferred from who called it.
    """
    # An L-bracket: a base slab plus an upright wall, which is what `recognise_plates` looks
    # for. A plain box has no plates, so it would make the gate assertion below vacuous.
    prismatic = Box(80, 40, 8) + Pos(-36, 0, 24) * Box(8, 40, 40)
    turned = Cylinder(20, 60)

    assert build_recognition_result(prismatic, rotational=False).rotational is False
    assert build_recognition_result(turned, rotational=True).rotational is True

    # Same solid, both classifications: only the gate differs, so any difference in the three
    # inventories is the gate's doing and not the geometry's.
    ungated = build_recognition_result(prismatic, rotational=False)
    gated = build_recognition_result(prismatic, rotational=True)

    assert gated.chamfers == () and gated.fillets == () and gated.plates == (), (
        "a rotational classification must gate all three away — otherwise migrating them "
        "reintroduced the scan the deferral existed to avoid"
    )
    assert ungated.plates, "fixture stopped producing plates, so the gate proves nothing here"
    # And nothing else moved: the gate is narrow, not a blanket suppression.
    assert ungated.holes == gated.holes
    assert ungated.risers == gated.risers
    assert ungated.step_levels == gated.step_levels


def test_the_plates_gate_needs_both_halves_not_just_the_rotational_one():
    """`plates` gates on a CONJUNCTION — not rotational AND no turned profile — and the
    profile half needs its own counterexample.

    The manifest's exclusion test drives both its turned fixtures through `build_drawing`,
    where they classify rotational, so weakening the gate to `if prismatic` alone still
    skipped plates on both and that guard stayed green (Codex #1033 r1). The half that was
    untested is exactly the documented "caller has no classification" path: a stepped shaft
    with `rotational=False`, where only the profile keeps the scan away.

    Asserted by COUNTING the call, not by checking the inventory came back empty:
    `recognise_plates` naturally finds nothing on a shaft, so an empty result proves the scan
    was skipped only by coincidence. The cost this gate exists to avoid is the scan itself.
    """
    shaft = Cylinder(20, 30) + Pos(0, 0, 30) * Cylinder(14, 30)

    with counting_calls({"plates": recognise_plates}) as counts:
        rec = build_recognition_result(shaft, rotational=False)

    assert rec.turned_steps, "fixture stopped producing a turned profile — the gate's other half"
    assert counts.get("plates", 0) == 0, (
        "recognise_plates ran for a part with a turned profile even though the caller said "
        "not-rotational — the conjunction has collapsed to its rotational half, and every "
        "unclassified stepped-shaft aggregate now pays for a scan the model discards"
    )
    assert rec.plates == ()
