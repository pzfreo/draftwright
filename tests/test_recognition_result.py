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


def test_built_drawing_exposes_its_recognition_result_without_private_state():
    drawing = build_drawing(_plate_with_holes())

    result = drawing.recognition()
    assert isinstance(result, RecognitionResult)
    assert len(result.holes) == 2
    assert result.cylinders


def test_orchestrator_injects_each_shared_dependency_once(monkeypatch):
    import draftwright.recognition.result as result_module

    calls = {"cylinders": 0, "countersinks": 0, "holes": 0, "patterns": 0}
    cylinders = ([{"axis": "z"}], [{"axis": "x"}])
    countersinks = [object()]
    holes = [object()]

    def fake_cylinders(part):
        calls["cylinders"] += 1
        return cylinders

    def fake_countersinks(part):
        calls["countersinks"] += 1
        return countersinks

    def fake_holes(part, *, cyls=None, csinks=None):
        calls["holes"] += 1
        assert cyls[0] is cylinders[0] and cyls[1] is cylinders[1]
        assert csinks is countersinks
        return holes

    def fake_patterns(records):
        calls["patterns"] += 1
        assert records is holes
        return []

    monkeypatch.setattr(result_module, "analyse_cylinders", fake_cylinders)
    monkeypatch.setattr(result_module, "recognise_countersinks", fake_countersinks)
    monkeypatch.setattr(result_module, "recognise_holes", fake_holes)
    monkeypatch.setattr(result_module, "recognise_hole_patterns", fake_patterns)
    monkeypatch.setattr(result_module, "recognise_bosses", lambda *args, **kwargs: [])
    monkeypatch.setattr(result_module, "recognise_slots", lambda part: [])
    monkeypatch.setattr(result_module, "recognise_pockets", lambda part: [])
    monkeypatch.setattr(result_module, "recognise_pocket_patterns", lambda records: [])
    monkeypatch.setattr(result_module, "recognise_rectangular_pads", lambda part: [])
    monkeypatch.setattr(result_module, "recognise_turned_steps", lambda *args, **kwargs: [])

    built = result_module.build_recognition_result(object())

    assert calls == {"cylinders": 1, "countersinks": 1, "holes": 1, "patterns": 1}
    assert built.holes == tuple(holes)
