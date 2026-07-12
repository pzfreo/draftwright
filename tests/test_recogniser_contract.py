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
from build123d import Box, Cylinder, Pos, Rot

from draftwright.recognition import (
    recognise_bosses,
    recognise_chamfers,
    recognise_countersinks,
    recognise_face_levels,
    recognise_hole_patterns,
    recognise_holes,
    recognise_plates,
    recognise_slots,
    recognise_step_shoulders,
    recognise_turned_steps,
)
from draftwright.recognition._record import Record


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


def _records_from_recognisers():
    """(name, record) pairs across every recogniser, on parts that trigger them."""
    csk = _csk_plate()
    stepped = _stepped()
    holes = recognise_holes(csk, csinks=recognise_countersinks(csk))
    bc = Box(80, 80, 10)
    for i in range(6):
        a = i * 60
        from math import cos, radians, sin

        bc -= Pos(25 * cos(radians(a)), 25 * sin(radians(a)), 0) * Cylinder(2, 10)
    slotted = Box(60, 40, 20) - Pos(0, 0, 0) * Box(30, 8, 20)

    out: list[tuple[str, object]] = []
    for name, recs in [
        ("recognise_holes", holes),
        ("recognise_countersinks", recognise_countersinks(csk)),
        ("recognise_bosses", recognise_bosses(Cylinder(10, 20))),
        ("recognise_hole_patterns", recognise_hole_patterns(recognise_holes(bc))),
        ("recognise_chamfers", recognise_chamfers(Box(20, 20, 20))),  # may be empty
        ("recognise_slots", recognise_slots(slotted)),
        ("recognise_plates", recognise_plates(Box(80, 60, 4))),
        ("recognise_face_levels", recognise_face_levels(stepped)),
        ("recognise_step_shoulders", recognise_step_shoulders(stepped, levels=[10.0])),
        ("recognise_turned_steps", recognise_turned_steps(_turned_shaft())),
    ]:
        for r in recs:
            out.append((name, r))
    return out


def test_records_are_frozen_and_json_serializable():
    """Every record from every recogniser is a frozen, JSON-serializable ``Record``."""
    records = _records_from_recognisers()
    # Sanity: the drive-parts actually exercised a broad spread of recognisers.
    assert len({name for name, _ in records}) >= 6

    for name, rec in records:
        assert isinstance(rec, Record), f"{name}: {type(rec).__name__} is not a Record"
        assert dataclasses.is_dataclass(rec) and rec.__dataclass_params__.frozen, (
            f"{name}: {type(rec).__name__} must be a frozen dataclass"
        )
        d = rec.to_dict()
        assert isinstance(d, dict)
        # The teeth: a leaked build123d/OCP object makes this raise.
        json.dumps(d)


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
        recognise_slots,
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
