"""ADR 0017 phase 1: one explicit recognition result."""

from dataclasses import FrozenInstanceError

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.model import build_part_model
from draftwright.recognition import RecognitionResult, build_recognition_result


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
    monkeypatch.setattr(result_module, "recognise_slots", counted("slots", slots))
    monkeypatch.setattr(result_module, "recognise_pockets", counted("pockets", pockets))
    monkeypatch.setattr(
        result_module, "recognise_pocket_patterns", derived("pocket_patterns", pockets, [])
    )
    monkeypatch.setattr(result_module, "recognise_rectangular_pads", counted("pads", []))
    monkeypatch.setattr(result_module, "recognise_turned_steps", cyl_consumer("turned_steps", []))
    # `step_level_zs` is the area-filtered gate over `recognise_face_levels`, migrated into the
    # aggregate by #1022 so declared-path critique reads one ladder instead of rescanning per
    # lint. It takes the part only — no injected substrate to assert identity on.
    monkeypatch.setattr(result_module, "step_level_zs", counted("step_levels", [4.0, 9.0]))
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

    built = result_module.build_recognition_result(object())

    # ONCE, not merely "at least once": a second call is a rediscovered substrate wearing
    # a correct answer's clothes. Both halves — WHICH families ran, and how often.
    assert set(calls) == {
        "cylinders",
        "countersinks",
        "holes",
        "patterns",
        "bosses",
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
    }, f"the orchestration ran a different set of families: {sorted(calls)}"
    assert set(calls.values()) == {1}, f"a family ran more than once: {calls}"
    assert built.holes == tuple(holes)
    assert built.step_levels == (4.0, 9.0)


def _grooved_flatted_shaft():
    """A turned shaft carrying both a groove and a machined flat."""
    part = Cylinder(20, 30) + Pos(0, 0, 30) * Cylinder(14, 30)
    part -= Pos(0, 0, 10) * (Cylinder(20, 5) - Cylinder(16, 5))  # groove
    part -= Pos(20, 0, 45) * Box(20, 40, 30)  # flat
    return part


def _slot_lattice_plate():
    """A 2x3 slot lattice — `rect_grid` needs n >= 6 to form a slot pattern."""
    part = Box(180, 130, 20)
    for i in range(2):
        for j in range(3):
            part -= Pos((i - 0.5) * 44, (j - 1) * 34, 0) * Box(24, 8, 20)
    return part


@pytest.mark.parametrize(
    ("name", "build"),
    [("grooved+flatted shaft", _grooved_flatted_shaft), ("slot lattice", _slot_lattice_plate)],
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
        step_zs=list(rec.step_levels),
        risers=list(rec.risers),
        cyls=rec.cylinders,
    )

    assert [f.kind for f in injected.features] == [f.kind for f in detected.features], (
        f"{name}: injecting the aggregate changed WHICH features the model carries"
    )
    assert injected.features == detected.features, (
        f"{name}: injecting the aggregate changed the features' values"
    )
