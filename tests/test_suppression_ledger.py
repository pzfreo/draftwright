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


def test_the_coincident_location_dedup_records_its_rejection():
    """Completeness: a rule that drops a measurement must produce an `Omission`.

    `plan_locations` dedups references within 0.5 of one another. That filter ran *before*
    the compiler saw the candidate, so the rejection produced no diagnostic at all and the
    audit could not see it (Codex #996 r1). An audit claiming completeness while a
    suppression path is invisible is worse than no audit — its silence reads as "nothing was
    suppressed", which is the exact false confidence this surface exists to remove.

    Unit-level on purpose: the dedup compares feature *origins* in X/Y, so provoking it from
    real geometry needs two features stacked at one plan position, and the fixture would then
    be testing recognition rather than the rule.
    """
    from build123d import Box as _Box

    from draftwright.model import Datum, Frame, HoleFeature, PartModel
    from draftwright.model.planner import plan_locations

    bbox = _Box(80, 50, 20).bounding_box()
    datum = Datum(id="datum_xy", kind="point", at=(bbox.min.X, bbox.min.Y, bbox.min.Z))
    near, also_near = (10.0, 5.0, 10.0), (10.2, 5.1, 18.0)  # within the 0.5 window in X/Y
    model = PartModel(
        bbox=bbox,
        orientation="prismatic",
        features=[
            HoleFeature(Frame(near, "z"), 6.0, depth=None, through=True),
            HoleFeature(Frame(also_near, "z"), 4.0, depth=5.0, through=False),
        ],
        datums=[datum],
    )

    planned = plan_locations(model)
    suppressed = [p for p in planned if p.suppressed]
    assert len(suppressed) == 1, "the deduped reference must be recorded, not dropped"
    # ...and it names the winner, so an auditor can see WHICH location absorbed it rather
    # than only that something vanished.
    assert "coincident" in suppressed[0].reason
    assert "10.000" in suppressed[0].reason and "5.000" in suppressed[0].reason


def test_the_ledger_is_plain_data():
    """A harness, a generated script or an LLM has to diff two builds without importing IR
    types — that is the whole point of an audit surface — so the rows are plain dicts."""
    dwg = build_drawing(Rot(0, 90, 0) * Cylinder(10, 40), number="X")
    rows = dwg.suppressions()
    assert rows and all(isinstance(r, dict) for r in rows)
    assert set(rows[0]) == {"feature", "parameter_id", "value", "reason", "authored"}
    import json

    json.dumps(rows)  # must round-trip; a harness will serialise these


def test_the_feature_key_distinguishes_two_instances_of_one_kind():
    """A diff has to say WHICH feature lost a measurement.

    The key was the class name, so two holes both read `"HoleFeature"` and a diff could not
    tell whether the same hole was suppressed in both builds, or whether a suppression had
    moved between instances (Codex #996 r1). It is derived from the geometry now, so it also
    survives a rebuild that reorders the feature list — list position would not.
    """
    from draftwright.drawing import feature_key
    from draftwright.model import Frame, HoleFeature

    a = HoleFeature(Frame((10.0, 5.0, 10.0), "z"), 6.0, depth=None, through=True)
    b = HoleFeature(Frame((10.2, 5.1, 18.0), "z"), 4.0, depth=5.0, through=False)

    assert feature_key(a) != feature_key(b), "two distinct holes must not share a key"
    assert feature_key(a) == "hole@(10.000,5.000,10.000)/z"
    assert feature_key(a) == feature_key(
        HoleFeature(Frame((10.0, 5.0, 10.0), "z"), 6.0, depth=None, through=True)
    ), "the same geometry must key the same across builds — that is what a diff relies on"
    assert feature_key(None) is None  # a model-level omission has no feature
