"""ADR 0017 phase 1: one explicit recognition result."""

from dataclasses import FrozenInstanceError

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
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
    }, f"the orchestration ran a different set of families: {sorted(calls)}"
    assert set(calls.values()) == {1}, f"a family ran more than once: {calls}"
    assert built.holes == tuple(holes)
