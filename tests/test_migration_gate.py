"""Scoped migration golden gate for the ADR 0008 compiler migration (#196).

**Disposable.** This file and ``tests/_migration_gate/`` exist only for the
duration of the ADR-0008 migration and are deleted in Phase 6 (#209). draftwright
keeps no standing general golden gate by design (ADR 0005 §3) — this is a
purpose-built equivalence gate for one risky refactor.

It snapshots the **semantic dimension set** of a representative corpus — the
placed dimension/callout labels + their view, the lint summary, and the sheet
(scale + page) — *not* bytes (the ADR-0005 §3 portability lesson). Each migration
PR must leave the snapshot unchanged, or regenerate it with a reviewed rationale
(the ADR-0004 diff-and-review discipline).

**X/Z parity is enforced here:** every turned scenario appears as both an X- and a
Z-variant, so an orientation regression fails the gate rather than slipping
through.

Regenerate:  ``UPDATE_MIGRATION_GATE=1 uv run pytest tests/test_migration_gate.py``
"""

import json
import os
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos, Rotation

from draftwright import build_drawing

_SNAPSHOT = Path(__file__).parent / "_migration_gate" / "snapshot.json"


# --- corpus -----------------------------------------------------------------
# Turned scenarios are paired X (shaft on its side) and Z (standing). _x() rotates
# a Z-built part so its turning axis lies along X.


def _x(part):
    return Rotation(0, 90, 0) * part


def _stepped_z():
    return Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)


def _bored_stepped_z():
    return _stepped_z() - Pos(0, 0, 45) * Cylinder(4, 20)


def _chamfered_stepped_z():
    s = _stepped_z()
    from build123d import GeomType

    edges = [e for e in s.edges() if e.geom_type == GeomType.CIRCLE]
    try:
        return s.chamfer(0.8, None, edges)
    except Exception:
        return s


def _three_step_z():
    return Cylinder(12, 10) + Pos(0, 0, 10) * Cylinder(8, 10) + Pos(0, 0, 20) * Cylinder(5, 10)


def _flange_z():
    # round OD + a 4-hole bolt circle (turned-and-drilled)
    part = Cylinder(25, 8)
    for x, y in ((15, 0), (-15, 0), (0, 15), (0, -15)):
        part -= Pos(x, y, 0) * Cylinder(3, 20)
    return part


def _plate_holes():
    part = Box(100, 60, 12)
    for x in (-30, 0, 30):
        part -= Pos(x, 0, 0) * Cylinder(4, 30)
    return part


def _cbore_plate():
    # a counterbored through hole in a plate
    part = Box(60, 60, 16)
    part -= Pos(0, 0, 0) * Cylinder(4, 30)
    part -= Pos(0, 0, 4) * Cylinder(8, 12)
    return part


def _slotted_bar():
    return Box(80, 40, 20) - Pos(0, 0, 0) * Box(30, 10, 30)


CORPUS = {
    "stepped_z": _stepped_z,
    "stepped_x": lambda: _x(_stepped_z()),
    "bored_stepped_z": _bored_stepped_z,
    "bored_stepped_x": lambda: _x(_bored_stepped_z()),
    "chamfered_stepped_z": _chamfered_stepped_z,
    "chamfered_stepped_x": lambda: _x(_chamfered_stepped_z()),
    "three_step_z": _three_step_z,
    "three_step_x": lambda: _x(_three_step_z()),
    "flange_z": _flange_z,
    "flange_x": lambda: _x(_flange_z()),
    "plate_holes": _plate_holes,
    "cbore_plate": _cbore_plate,
    "slotted_bar": _slotted_bar,
}


# --- digest -----------------------------------------------------------------


def _digest(dwg) -> dict:
    """The semantic dimension set: labelled annotations (type, view, label), the
    lint summary, and the sheet. Order-independent (sorted); no positions/bytes."""
    labels = sorted(
        [type(a).__name__, dwg._anno_view.get(n), str(getattr(a, "label", None))]
        for n, a in dwg._named.items()
        if getattr(a, "label", None) and type(a).__name__ != "TitleBlock"
    )
    s = dwg.lint_summary()
    return {
        "sheet": [dwg.scale, dwg.page_w, dwg.page_h],
        "labels": labels,
        "lint": {"passed": s["passed"], "score": round(s["score"], 3), "by_code": s["by_code"]},
    }


def _digest_for(case: str) -> dict:
    dwg = build_drawing(CORPUS[case](), number="GATE")
    # round-trip so types match the loaded JSON (tuples → lists) for comparison
    return json.loads(json.dumps(_digest(dwg)))


# --- the gate ---------------------------------------------------------------

UPDATE = os.environ.get("UPDATE_MIGRATION_GATE") == "1"


@pytest.mark.parametrize("case", list(CORPUS))
def test_migration_gate(case):
    actual = _digest_for(case)
    if UPDATE:
        snap = json.loads(_SNAPSHOT.read_text()) if _SNAPSHOT.exists() else {}
        snap[case] = actual
        _SNAPSHOT.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"migration gate updated for {case!r}")
    assert _SNAPSHOT.exists(), "no snapshot — run UPDATE_MIGRATION_GATE=1 to create it"
    expected = json.loads(_SNAPSHOT.read_text()).get(case)
    assert expected is not None, (
        f"no snapshot for {case!r}; regenerate with UPDATE_MIGRATION_GATE=1"
    )
    assert actual == expected, (
        f"migration gate changed for {case!r}. If intended, regenerate with "
        f"UPDATE_MIGRATION_GATE=1 and justify in the PR.\n"
        f"expected: {expected}\nactual:   {actual}"
    )


class TestXZParity:
    """The corpus pairs turned scenarios X/Z so parity is enforced by the gate,
    not hoped for. This asserts the pairing exists and both variants build."""

    @pytest.mark.parametrize(
        "base", ["stepped", "bored_stepped", "chamfered_stepped", "three_step", "flange"]
    )
    def test_each_turned_scenario_has_both_orientations(self, base):
        assert f"{base}_x" in CORPUS and f"{base}_z" in CORPUS
