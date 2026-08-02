"""`Drawing.suppressions()` — the audit read (#996 WP1).

A finished drawing shows what was drawn. Nothing showed what was **not**, or why, because the
compiled plan carrying that record was a local in the orchestrator: built, consumed by the
renderers, discarded. So an absent dimension could only be inferred from the sheet, and
inference cannot tell "correctly omitted" from "a suppression rule is wrong".

That gap has a measured cost. #997's square-footprint rule suppressed BOTH envelope extents on
a square part and fired on parts up to 5% off square. The drawing was silent, lint was clean,
and it produced **four separate issue reports** (#916, #917, #918, and the shape of #909) none
of which named the cause. What finally identified it was building one part twice with a single
property changed and diffing the dimensions — the manual form of this read.

The distinction this surface has to preserve is ADR 0016's: an **authored** omission is the
script's own (omission means suppression, recoverable with a `dimension(...)` line), while a
rule suppression is the engine's decision and must name the rule. They look identical on paper
and mean opposite things.
"""

from __future__ import annotations

from build123d import Box, Cylinder, Pos, Rot

from draftwright import Sheet, build_drawing


def _by_param(dwg) -> dict[str, dict]:
    return {o["parameter_id"]: o for o in dwg.suppressions()}


def test_a_rule_suppression_names_the_rule_that_made_it():
    """The X-turned rotational rule is a *correct* suppression — the OD conveys the cross-axis
    extent, so restating it would double-dimension. The point is not that it fires, but that
    the drawing can now say it fired and why."""
    dwg = build_drawing(Rot(0, 90, 0) * Cylinder(10, 40), number="X")
    led = _by_param(dwg)

    assert "depth.length" in led, "the suppressed extent must appear in the ledger"
    entry = led["depth.length"]
    assert entry["authored"] is False, "a planner rule, not the script's omission"
    assert "rotational OD" in entry["reason"], f"unattributed suppression: {entry['reason']!r}"

    # ...and the drawing really is missing it, so the ledger describes the sheet rather than
    # some parallel bookkeeping.
    assert "m_env_depth" not in dwg.annotations()


def test_a_part_with_nothing_suppressed_has_an_empty_ledger():
    """No false positives: a plain prismatic plate states its extents and the audit is quiet.

    This is also the #997 regression from the audit's side — a square plate reported two rule
    suppressions before that fix, and the ledger is where that would now be visible.
    """
    assert build_drawing(Box(60, 40, 20), number="X").suppressions() == []
    assert build_drawing(Box(50, 50, 30), number="X").suppressions() == []


def test_an_authored_omission_is_distinguished_from_a_rule_suppression():
    """ADR 0016: omission from an authored set is the AUTHOR's decision. Conflating it with a
    rule suppression would make the audit unusable — every authored script would look like the
    engine dropping dimensions."""
    sheet = Sheet(Box(80, 50, 20) - Pos(10, 5, 0) * Cylinder(4, 30), title="T", number="N")
    hole = sheet.hole(diameter=8, at=(10, 5, 10), axis="z", depth=20)
    sheet.dimension(hole, "bore.diameter")  # authored set: everything else is omitted
    dwg = sheet.build()

    authored = [o for o in dwg.suppressions() if o["authored"]]
    assert authored, "an authored set that omits measurements must record them as authored"
    assert all(o["reason"] for o in dwg.suppressions()), "every omission carries a reason"


def test_the_ledger_is_plain_data():
    """A harness, a generated script or an LLM has to diff two builds without importing IR
    types — that is the whole point of an audit surface — so the rows are plain dicts."""
    dwg = build_drawing(Rot(0, 90, 0) * Cylinder(10, 40), number="X")
    rows = dwg.suppressions()
    assert rows and all(isinstance(r, dict) for r in rows)
    assert set(rows[0]) == {"feature", "parameter_id", "value", "reason", "authored"}
    import json

    json.dumps(rows)  # must round-trip; a harness will serialise these
