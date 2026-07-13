"""ADR 0013 recogniser-contract tests.

Enforces the uniform contract mechanically (epic #584 WP3):

- **Immutable records** — every recogniser returns frozen dataclasses.
- **Uniform serialization** — each record has ``.to_dict()`` (the :class:`Record`
  mixin) that yields a *JSON-serializable* nested dict. This is the invariant with
  teeth: a leaked build123d / OCP object would make ``json.dumps`` raise, so the
  test proves the "geometry-only records, no build123d type leaks out" rule.
- **Signature shape** — a part-based recogniser takes ``part`` then keyword-only
  args; a derived recogniser takes a single positional inventory.
"""

from __future__ import annotations

import dataclasses
import inspect
import json

import pytest
from build123d import Axis, Box, Cylinder, Pos, Rot, chamfer, fillet

from draftwright.recognition import (
    BoltCircle,
    BossRecord,
    Chamfer,
    CounterSink,
    FaceLevel,
    Fillet,
    HoleRecord,
    LinearArray,
    Plate,
    Pocket,
    RectGrid,
    Slot,
    StepShoulder,
    TurnedStep,
    recognise_bosses,
    recognise_chamfers,
    recognise_countersinks,
    recognise_face_levels,
    recognise_fillets,
    recognise_hole_patterns,
    recognise_holes,
    recognise_plates,
    recognise_pockets,
    recognise_slots,
    recognise_step_shoulders,
    recognise_turned_steps,
)
from draftwright.recognition._record import Record

# Every record class a recogniser returns. The coverage test asserts the drive-parts
# below actually emit one of each — so a record type that silently stops being produced
# (or a new recogniser added without contract coverage) fails the test loudly, rather
# than slipping through a bare count. (CounterBore/HoleSpec/TurnedProfile are sub-records
# / aggregates, not recogniser returns, so they are exercised nested, not listed here.)
_EXPECTED_RECORD_TYPES = {
    HoleRecord,
    CounterSink,
    BossRecord,
    BoltCircle,
    LinearArray,
    RectGrid,
    Chamfer,
    Fillet,
    Slot,
    Pocket,
    Plate,
    FaceLevel,
    StepShoulder,
    TurnedStep,
}


def _csk_plate():
    from build123d import Cone

    plate = Box(90, 60, 12)
    for x, y in [(-30, -15), (5, 12), (30, -8)]:
        plate -= Pos(x, y, 0) * Cylinder(3, 12)
        plate -= Pos(x, y, 4) * Cone(3, 7, 4)
    return plate


def _stepped():
    return Box(80, 40, 10) + Pos(-20, 0, 10) * Box(40, 40, 12)


def _turned_shaft():
    return Rot(0, 90, 0) * (Cylinder(10, 30) + Pos(0, 0, 20) * Cylinder(6, 10))


def _linear_array_plate():
    part = Box(120, 40, 10)
    for i in range(5):
        part -= Pos(-40 + i * 20, 0, 0) * Cylinder(3, 10)
    return part


def _grid_plate(nx=3, ny=3, px=25, py=25):
    part = Box(px * (nx + 1), py * (ny + 1), 10)
    for i in range(nx):
        for j in range(ny):
            part -= Pos((i - (nx - 1) / 2) * px, (j - (ny - 1) / 2) * py, 0) * Cylinder(3, 10)
    return part


def _bolt_circle_plate(n=6, r=30):
    from math import cos, radians, sin

    part = Box(100, 100, 12)
    for i in range(n):
        a = radians(360 / n * i + 15.0)
        part -= Pos(r * cos(a), r * sin(a), 0) * Cylinder(4, 12)
    return part


def _chamfered_box():
    box = Box(30, 30, 30)
    edge = box.edges().filter_by(Axis.Z).sort_by(Axis.X)[-1]
    return chamfer(edge, 3)


def _filleted_box():
    box = Box(30, 30, 30)
    edge = box.edges().filter_by(Axis.Z).sort_by(Axis.X)[-1]
    return fillet(edge, 3)


def _l_bracket():
    return Box(80, 40, 8) + Pos(-36, 0, 24) * Box(8, 40, 40)


def _records_from_recognisers():
    """(name, record) pairs across every recogniser, on parts that actually trigger them."""
    csk = _csk_plate()
    stepped = _stepped()
    holes = recognise_holes(csk, csinks=recognise_countersinks(csk))
    slotted = Box(60, 40, 20) - Pos(0, 0, 0) * Box(30, 8, 20)
    pocketed = Box(60, 40, 20) - Pos(0, 0, 7) * Box(30, 18, 6)
    levels = [f.z for f in recognise_face_levels(stepped)]

    out: list[tuple[str, object]] = []
    for name, recs in [
        ("recognise_holes", holes),
        ("recognise_countersinks", recognise_countersinks(csk)),
        ("recognise_bosses", recognise_bosses(Cylinder(10, 20))),
        ("hole_patterns:bolt", recognise_hole_patterns(recognise_holes(_bolt_circle_plate()))),
        ("hole_patterns:linear", recognise_hole_patterns(recognise_holes(_linear_array_plate()))),
        ("hole_patterns:grid", recognise_hole_patterns(recognise_holes(_grid_plate()))),
        ("recognise_chamfers", recognise_chamfers(_chamfered_box())),
        ("recognise_fillets", recognise_fillets(_filleted_box())),
        ("recognise_slots", recognise_slots(slotted)),
        ("recognise_pockets", recognise_pockets(pocketed)),
        ("recognise_plates", recognise_plates(_l_bracket())),
        ("recognise_face_levels", recognise_face_levels(stepped)),
        ("recognise_step_shoulders", recognise_step_shoulders(stepped, levels=levels)),
        ("recognise_turned_steps", recognise_turned_steps(_turned_shaft())),
    ]:
        for r in recs:
            out.append((name, r))
    return out


def test_records_are_frozen_and_json_serializable():
    """Every record from every recogniser is a frozen, JSON-serializable ``Record``."""
    records = _records_from_recognisers()

    for name, rec in records:
        assert isinstance(rec, Record), f"{name}: {type(rec).__name__} is not a Record"
        assert dataclasses.is_dataclass(rec) and rec.__dataclass_params__.frozen, (
            f"{name}: {type(rec).__name__} must be a frozen dataclass"
        )
        d = rec.to_dict()
        assert isinstance(d, dict)
        # The teeth: a leaked build123d/OCP object makes this raise.
        json.dumps(d)


def test_every_record_type_is_actually_exercised():
    """The drive-parts must emit *each* recogniser record type — no silent under-coverage.

    Guards against the count-only trap: a record type whose drive-part stops producing it
    (or a new record added without coverage) fails here instead of passing on a bare tally.
    """
    seen = {type(rec) for _, rec in _records_from_recognisers()}
    missing = _EXPECTED_RECORD_TYPES - seen
    assert not missing, f"contract test never exercised these record types: {missing}"


def test_frozen_records_reject_mutation():
    """A record is immutable — assigning a field raises (frozen dataclass)."""
    hole = recognise_holes(_csk_plate())[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        hole.diameter = 99.0  # type: ignore[misc]


def test_part_based_recognisers_are_keyword_only_after_part():
    """Part-based recognisers take ``part`` then keyword-only args (ADR 0013)."""
    for fn in (
        recognise_holes,
        recognise_bosses,
        recognise_countersinks,
        recognise_chamfers,
        recognise_fillets,
        recognise_slots,
        recognise_pockets,
        recognise_plates,
        recognise_face_levels,
        recognise_step_shoulders,
        recognise_turned_steps,
    ):
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == "part"
        assert params[0].kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        for p in params[1:]:
            assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"{fn.__name__}: '{p.name}' must be keyword-only (injected dep / tuning)"
            )


def test_derived_recogniser_takes_single_positional_inventory():
    """A derived recogniser (``recognise_hole_patterns``) takes one positional arg."""
    params = list(inspect.signature(recognise_hole_patterns).parameters.values())
    assert params[0].name == "holes"
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
