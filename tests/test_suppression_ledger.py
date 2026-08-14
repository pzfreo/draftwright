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


def test_nearby_distinct_locations_retain_both_semantic_identities():
    """Nearby holes remain separately addressable through planning and compilation."""
    from build123d import Box as _Box

    from draftwright.model import Datum, Frame, HoleFeature, PartModel
    from draftwright.model.compiled import compile_dimensions
    from draftwright.model.planner import plan_locations

    bbox = _Box(80, 50, 20).bounding_box()
    datum = Datum(id="datum_xy", kind="point", at=(bbox.min.X, bbox.min.Y, bbox.min.Z))
    near, also_near = (10.0, 5.0, 10.0), (10.2, 5.1, 18.0)
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
    assert len(planned) == 2
    assert not [p for p in planned if p.suppressed]

    locations = compile_dimensions(model).locations
    assert len(locations) == 4  # X + Y for each distinct feature
    assert {entry.id.feature for entry in locations} == set(model.features)
    assert {entry.id.parameter for entry in locations} == {"location.location"}
    assert {entry.discriminator for entry in locations} == {"x", "y"}


def test_an_edge_anchored_pocket_records_why_it_has_no_location():
    """The second completeness hole, same shape as the dedup one (Codex #996 r2).

    An edge-anchored pocket passes the location eligibility check and is then `continue`d
    out of the loop — a rule decision, silently. Worse than a missing audit row: an authored
    `dimension(pocket, "location")` that `_check_authored_targets` had ACCEPTED produced
    nothing at all, breaking that check's guarantee that an accepted entry cannot silently
    yield nothing.

    Found by asking the reviewer, twice, for *other* paths that drop a measurement without an
    `Omission` — a completeness claim is only worth what the search behind it was.
    """
    from build123d import Box as _Box

    from draftwright.model import Datum, Frame, PartModel, PocketFeature
    from draftwright.model.planner import plan_locations

    bbox = _Box(80, 50, 20).bounding_box()
    pocket = PocketFeature(
        frame=Frame((10.0, 5.0, 20.0), "z"),
        width_axis="y",
        long_axis="x",
        width=10.0,
        length=20.0,
        depth=6.0,
        w_center=5.0,
        lo=0.0,
        hi=20.0,
        edge_anchored=True,
    )
    model = PartModel(
        bbox=bbox,
        orientation="prismatic",
        features=[pocket],
        datums=[Datum(id="datum_xy", kind="point", at=(bbox.min.X, bbox.min.Y, bbox.min.Z))],
    )

    planned = plan_locations(model)
    assert len(planned) == 1, "the skipped location must be recorded, not dropped"
    assert planned[0].suppressed
    assert "edge-anchored" in planned[0].reason


def test_a_pattern_with_no_members_records_why_it_has_no_location():
    """The third silent drop, found by sweeping the loop rather than waiting for a report.

    A non-bolt-circle pattern with no members has no point to locate FROM, so it fell through
    the elif chain. Like the edge-anchored pocket, it had already passed the eligibility check
    — considered, then dropped, with nothing to show for it.
    """
    from build123d import Box as _Box

    from draftwright.model import Datum, Frame, HoleFeature, PartModel, PatternFeature
    from draftwright.model.planner import plan_locations

    bbox = _Box(80, 50, 20).bounding_box()
    member = HoleFeature(Frame((10.0, 5.0, 10.0), "z"), 6.0, depth=None, through=True)
    model = PartModel(
        bbox=bbox,
        orientation="prismatic",
        features=[
            PatternFeature(
                Frame((10.0, 5.0, 10.0), "z"),
                pattern="linear",
                count=2,
                member=member,
                members=(),
            )
        ],
        datums=[Datum(id="datum_xy", kind="point", at=(bbox.min.X, bbox.min.Y, bbox.min.Z))],
    )
    planned = plan_locations(model)
    assert [p.suppressed for p in planned] == [True]
    assert "no members" in planned[0].reason


def test_the_ledger_is_filled_when_auto_dimensions_are_off():
    """`auto_dims=False` reported an EMPTY ledger while the compiler really had suppressed
    measurements (Codex #996 r1) — the fill sat inside the auto branch.

    That is the worst failure this surface can have: not a wrong answer but a confident empty
    one, on a supported path. Both paths must agree about what the compiler decided, because
    the compiler decided the same thing either way; only the rendering differs.
    """
    part = Rot(0, 90, 0) * Cylinder(10, 40)
    auto = build_drawing(part, number="X", auto_dims=True).suppressions()
    manual = build_drawing(part, number="X", auto_dims=False).suppressions()

    assert manual, "auto_dims=False must not report an empty ledger"
    assert [r["parameter_id"] for r in auto] == [r["parameter_id"] for r in manual]
    assert [r["reason"] for r in auto] == [r["reason"] for r in manual]


def test_a_model_with_no_datum_records_the_locations_it_cannot_measure():
    """The fourth hole, and the third round in a row to find one (Codex #996 r3).

    `plan_locations` returned `[]` outright when the model carried no `datum_xy`. Every
    otherwise-eligible feature still HAD a position to lose, so the drawing simply had no
    locations and the audit said nothing.

    It breaks the same guarantee as the edge-anchored pocket: `_check_authored_targets`
    accepts `dimension(hole, "location")` on feature eligibility alone, so an author could
    name a position, pass validation, and receive neither the dimension nor a reason. It bites
    a caller-supplied `PartModel` (ADR 0011), which `build_drawing` preserves verbatim —
    datums included, or not.

    Not fixed by defaulting a datum into model coercion: that would hide malformed compiler
    input behind a plausible-looking drawing rather than reporting it.
    """
    from build123d import Box as _Box

    from draftwright.model import Frame, HoleFeature, PartModel
    from draftwright.model.planner import plan_locations

    bbox = _Box(80, 50, 20).bounding_box()
    model = PartModel(
        bbox=bbox,
        orientation="prismatic",
        features=[HoleFeature(Frame((10.0, 5.0, 10.0), "z"), 6.0, depth=None, through=True)],
        datums=[],  # the point: a model that never got a datum_xy
    )

    planned = plan_locations(model)
    assert [p.suppressed for p in planned] == [True], "an unmeasurable location must be recorded"
    assert "no datum_xy" in planned[0].reason
    assert planned[0].datum is None  # there is none — the row says so rather than inventing one

    # ...and it survives the FULL build, not just the planner. The unit assertions above all
    # passed while `build_drawing` raised AssertionError on this very model: `_compile_locations`
    # asserted a datum → ref span before checking `suppressed`, so the diagnostic that closed
    # the hole crashed the build instead — a silent omission traded for a hard failure.
    # Testing the planner in isolation could not see that; the public path is the contract.
    drawing = build_drawing(_Box(80, 50, 20), model=model, number="X")
    rows = [r for r in drawing.suppressions() if "no datum_xy" in r["reason"]]
    assert rows, "the missing-datum diagnostic must reach Drawing.suppressions()"


def test_one_row_per_fact_not_per_sighting():
    """An authored set records the overall height TWICE — `_compile_overall_height`'s bespoke
    branch and the general group traversal both notice it, since the envelope's `height`
    parameter is in its group too.

    Both are right about the same fact. A duplicated row makes a consumer over-count
    suppressions, and a build-diff show churn that never happened — the false signal this
    audit exists to remove. (The lead Codex was chasing when its final run timed out; found
    by counting rows rather than reading them.)
    """
    from collections import Counter

    sheet = Sheet(Box(120, 80, 25) - Pos(0, 0, 15) * Box(30, 20, 12), title="T", number="N")
    sheet.dimension(sheet.envelope(), "width.length")  # authored: everything else omitted
    rows = sheet.build().suppressions()

    keys = [(r["feature"], r["parameter_id"], r["reason"]) for r in rows]
    dupes = {k: n for k, n in Counter(keys).items() if n > 1}
    assert not dupes, f"the same omission reported more than once: {dupes}"
    # ...and the fact itself survives the dedup rather than being dropped with its twin.
    assert any(r["parameter_id"] == "height.length" for r in rows)


def test_the_dedup_keeps_repetitions_within_one_source():
    """The dedup must not eat genuine per-member facts (Codex #996 r6).

    `_compile_off_axis_hole_locations` emits one omission per member, and every member of a
    grouped hole shares the SAME `HoleFeature` — so a key of (feature, parameter, reason) is
    identical across members. Keyed that way, four real member positions collapsed to one and
    vanished from the audit. Losing a real row is worse than the duplicate it was meant to fix.

    So the dedup is cross-SOURCE only: two different compilers reporting one fact is a
    duplicate; one compiler reporting four members is four facts. My first test asserted only
    the envelope case and passed while this was broken.
    """
    from draftwright.model.compiled import Omission, _dedupe_omissions

    feature = object()  # one feature, as a grouped hole's members share theirs
    bespoke = [Omission(feature, "height.length", 25.0, "authored")]
    members = [Omission(feature, "location.location", v, "r") for v in (20.0, 12.0, 50.0, 18.0)]
    general = [Omission(feature, "height.length", 25.0, "authored")]  # the same fact again

    rows = _dedupe_omissions(bespoke, members, general)

    heights = [o for o in rows if o.parameter_id == "height.length"]
    positions = [o for o in rows if o.parameter_id == "location.location"]
    assert len(heights) == 1, "two compilers reporting one fact is a duplicate"

    # ...but two compilers DISAGREEING is not one fact. `value` is in the dedup key, so a
    # differing measurement survives as its own row instead of the earlier source silently
    # winning — an audit should surface a disagreement between compilers, not hide it.
    disagreeing = _dedupe_omissions(
        [Omission(feature, "height.length", 25.0, "authored")],
        [Omission(feature, "height.length", 30.0, "authored")],
    )
    assert len(disagreeing) == 2, "a value disagreement must not be deduped away"

    # ...but float NOISE is not a disagreement. These values carry real jitter — an X-turned
    # envelope reports its height as 20.000000000000007 by one route and 20.0 by another — so
    # the key rounds. Keying on the raw float would split a genuine duplicate back into two
    # rows on noise, re-creating the over-reporting this function exists to remove.
    jittered = _dedupe_omissions(
        [Omission(feature, "height.length", 20.0, "authored")],
        [Omission(feature, "height.length", 20.000000000000007, "authored")],
    )
    assert len(jittered) == 1, "float jitter must not read as two compilers disagreeing"

    # And an omission with no value at all (most location rows) still dedupes.
    valueless = _dedupe_omissions(
        [Omission(feature, "location.location", None, "r")],
        [Omission(feature, "location.location", None, "r")],
    )
    assert len(valueless) == 1
    assert [o.value for o in positions] == [20.0, 12.0, 50.0, 18.0], (
        "one compiler reporting four members is four facts, not one"
    )


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

    # Position alone is not identity. Two holes at ONE origin with different bores keyed the
    # same — and that is precisely the coincident-dedup case, where the ledger most needs to
    # say which instance lost its location (Codex #996 r4).
    same_spot_wide = HoleFeature(Frame((10.0, 5.0, 10.0), "z"), 6.0, depth=None, through=True)
    same_spot_narrow = HoleFeature(Frame((10.0, 5.0, 10.0), "z"), 4.0, depth=8.0, through=False)
    assert feature_key(same_spot_wide) != feature_key(same_spot_narrow)
    assert feature_key(a) == "hole@(10.000,5.000,10.000)/z[diameter=6.000]"
    assert feature_key(a) == feature_key(
        HoleFeature(Frame((10.0, 5.0, 10.0), "z"), 6.0, depth=None, through=True)
    ), "the same geometry must key the same across builds — that is what a diff relies on"
    assert feature_key(None) is None  # a model-level omission has no feature

    # The key is TOTAL by design. Every concrete IR feature carries a frame with a required
    # origin, so these fallbacks are unreachable today — but `feature_key` runs once per
    # ledger row, and one odd feature raising would take the whole audit read down with it.
    # A degraded-but-honest key beats an exception here, which is the opposite trade to the
    # builder's model guard (removed, because ITS fallback was a silent empty ledger).
    class _NoFrame:
        kind = "mystery"

    class _NoOrigin:
        kind = "mystery"
        frame = type("F", (), {"origin": None, "axis": "z"})()

    assert feature_key(_NoFrame()) == "mystery"
    assert feature_key(_NoOrigin()) == "mystery/z"
