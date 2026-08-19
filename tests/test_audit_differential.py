"""`draftwright.audit` — diff two builds and ask what went missing (#996 WP1 step 2).

The ledger says which rule removed a measurement, which is diagnosis. It cannot say that a
measurement left without any rule recording it — three consecutive review rounds on #999 found
suppression paths that recorded nothing, each caught by a person hunting for it rather than by
the ledger noticing its own gap.

The differential does not depend on anything having been recorded. If a dimension is present
in one build and absent in another, something removed it. That is how #997 was actually found:
not from four issue reports describing its symptoms, but from `50x50` vs `50x40`.

Most of these tests use duck-typed stand-ins rather than real builds. The subject is the
COMPARISON, and a real part would make the test about recognition instead — slower, and
failing for reasons that have nothing to do with the thing under test. One end-to-end case
keeps it honest about the real `Drawing` surface.
"""

from __future__ import annotations

from build123d import Box, BuildPart, Cylinder, Hole, Locations, Pos, Rot

from draftwright import Sheet, build_drawing
from draftwright.audit import diff_builds, explain


class _FakeDrawing:
    """The three public reads `diff_builds` uses, and nothing else."""

    def __init__(
        self,
        dims: dict[str, str],
        suppressions: list[dict] | None = None,
        types: dict[str, str] | None = None,
        identities: dict[str, tuple] | None = None,
    ):
        self._dims = dims
        self._supp = suppressions or []
        self._types = types or {}
        self._ids = identities or {}

    def annotations(self):
        return {n: self._types.get(n, "Dimension") for n in self._dims}

    def get_annotation(self, name):
        return type("A", (), {"label": self._dims[name]})()

    def suppressions(self):
        return list(self._supp)

    def measurement_keys(self, name):
        # A LIST of identities, because a real annotation can draw several (a grouped `4× R5`
        # fillet callout draws four). The first cut returned at most one, so the differential
        # tests could not express the compound case at all — and did not notice the audit
        # collapsing tuples to a set (Codex #1002 r5). A single `(feature, parameter)` pair is
        # still accepted for the many one-measurement cases.
        ident = self._ids.get(name)
        if ident is None:
            return []
        pairs = ident if isinstance(ident, list) else [ident]
        return [{"feature": f, "parameter_id": p} for f, p in pairs]


def _supp(parameter, reason, feature="envelope@(0,0,0)/z"):
    return {
        "feature": feature,
        "parameter_id": parameter,
        "value": None,
        "reason": reason,
        "authored": False,
    }


def test_a_loss_a_rule_accounts_for_is_not_unexplained():
    """The #997 shape, and the good case: a dimension vanished AND the ledger names why.

    That is the audit working — a human reads "square footprint (single overall dim
    suffices)" and can ask whether a 50x50 part should really have no plan size. The
    measurement is gone, but nothing is hidden.
    """
    # The two builds describe DIFFERENT geometry — that is the whole point of a
    # differential — so the feature keys differ in their scalars. An exact join on the
    # ledger key finds nothing here and reports "nothing claims it" for a suppression that
    # plainly does claim it (Codex #1002 r1, reproduced on a real build). The cross-build
    # correspondence key is what makes this match.
    before_env = "envelope@(0,0,0)/z[depth=40.000,width=50.000]"
    after_env = "envelope@(0,0,0)/z[depth=50.000,width=50.000]"
    before = _FakeDrawing(
        {"m_env_width": "50", "m_env_depth": "40", "dim_height": "30"},
        identities={
            "m_env_width": (before_env, "width.length"),
            "m_env_depth": (before_env, "depth.length"),
        },
    )
    after = _FakeDrawing(
        {"dim_height": "30"},
        [
            _supp("width.length", "square footprint", feature=after_env),
            _supp("depth.length", "square footprint", feature=after_env),
        ],
    )

    diff = diff_builds(before, after)
    assert set(diff["dimensions_lost"]) == {"m_env_width", "m_env_depth"}
    # The ledger's account appears as a HINT on each loss — it does not remove the loss.
    assert set(diff["candidate_explanations"]) == {"m_env_width", "m_env_depth"}
    assert all(line.startswith("LOST") for line in explain(diff)[:2])
    assert any("square footprint" in line for line in explain(diff))


def test_a_loss_no_rule_claims_is_flagged():
    """The alarm. A dimension left the drawing and NOTHING recorded that it did.

    This is the class the ledger alone cannot detect, and the reason this module exists: the
    coincident-location dedup dropped candidates before the compiler saw them, so no
    `Omission` was ever written. A diff notices anyway, because it compares outcomes rather
    than trusting the engine's account of itself.
    """
    before = _FakeDrawing({"m_locx0": "33", "dim_height": "30"})
    after = _FakeDrawing({"dim_height": "30"})  # m_locx0 gone, ledger silent

    diff = diff_builds(before, after)
    assert diff["dimensions_lost"] == {"m_locx0": "33"}
    assert diff["candidate_explanations"] == {}, "nothing in the ledger claims it"
    assert explain(diff)[0] == "LOST: m_locx0 (33) — nothing claims it"


def test_the_report_puts_the_alarm_first():
    """Ordering is the value, not decoration. An unexplained loss is a possible engine defect;
    an explained one is a rule working. Interleaved in dict order, the first hides among the
    second — which is how a wrong suppression survived four issue reports."""
    before = _FakeDrawing({"m_locx0": "33", "m_env_depth": "40"})
    after = _FakeDrawing({"m_new": "12"}, [_supp("depth.length", "square footprint")])

    lines = explain(diff_builds(before, after))
    assert lines[0].startswith("LOST")
    assert sum(line.startswith("LOST") for line in lines) == 2  # both losses alarm


def test_a_changed_value_is_reported_but_not_alarmed():
    """A dimension can change without disappearing, and the first cut could not see it: an
    80 mm width becoming 90 produced NO output at all, because the diff compared annotation
    names and the name survived.

    Reported now — a surface claiming to show "what was drawn" has to see a changed value. But
    deliberately not an alarm: in a perturbation study a changed value is the expected result
    of the perturbation, so ranking it with the losses would bury the signal in the noise the
    experiment itself creates.
    """
    before = _FakeDrawing({"m_env_width": "80", "dim_height": "20"})
    after = _FakeDrawing({"m_env_width": "90", "dim_height": "20"})

    diff = diff_builds(before, after)
    assert diff["dimensions_changed"] == {"m_env_width": ("80", "90")}
    assert diff["dimensions_lost"] == {} and diff["dimensions_gained"] == {}

    lines = explain(diff)
    assert lines == ["changed: m_env_width 80 -> 90"]
    assert not lines[0].startswith("LOST"), "a value change is not an alarm"


def test_a_loss_outranks_a_change_in_the_report():
    """Ordering again: a vanished dimension is a possible defect, a changed one is usually the
    experiment working. The alarm must not sit below the noise."""
    before = _FakeDrawing({"m_locx0": "33", "m_env_width": "80"})
    after = _FakeDrawing({"m_env_width": "90"})

    lines = explain(diff_builds(before, after))
    assert lines[0].startswith("LOST")
    assert lines[-1].startswith("changed:")


def test_a_weak_hint_never_cancels_the_alarm():
    """The dangerous direction, and the one my own tests could not have found (Codex #1001).

    The first cut cancelled a loss outright when any newly-gained suppression's parameter stem
    appeared in the lost annotation's name. Feature identity was discarded, so an unrelated
    ENVELOPE `width.length` suppression silently excused a lost SLOT width — an alarm removed
    by a coincidence of substrings.

    An annotation carries no feature identity, so this match cannot be made reliable here; the
    fix is that it no longer decides anything. It annotates the loss and the loss still shows.
    """
    before = _FakeDrawing({"m_slot_width0": "8"})
    after = _FakeDrawing({}, [_supp("width.length", "square footprint", feature="envelope@z")])

    diff = diff_builds(before, after)
    assert diff["dimensions_lost"] == {"m_slot_width0": "8"}, "the loss must survive the hint"
    assert explain(diff)[0].startswith("LOST: m_slot_width0")


def test_sheet_furniture_is_not_a_measurement():
    """A title block, a note and a centre mark all have labels and all change between builds
    for reasons that are not dimensional. Counting them drowns the signal in the noise this
    module exists to lift out (Codex #1001), so the diff filters on annotation TYPE."""
    types = {
        "title_block": "TitleBlock",
        "note_iso_nts": "Note",
        "m_cm0": "CenterMark",
        "m_env_width": "Dimension",
        "hc_plan0": "Leader",  # a hole callout IS dimensional content
    }
    dims = {
        "title_block": "DRAWING",
        "note_iso_nts": "ISO VIEW (NTS)",
        "m_cm0": "x",
        "m_env_width": "50",
        "hc_plan0": "4x ø8 THRU",
    }
    before = _FakeDrawing(dims, types=types)
    after = _FakeDrawing({"m_env_width": "50"}, types={"m_env_width": "Dimension"})

    diff = diff_builds(before, after)
    assert set(diff["dimensions_lost"]) == {"hc_plan0"}, (
        "only the callout is a measurement; the title block, note and centre mark are furniture"
    )


def test_an_unknown_annotation_type_counts_as_a_measurement():
    """The filter is a DENYLIST, and the polarity is the point (Codex #1001 r2).

    An allowlist of {"Dimension", "Leader"} silently dropped `SafeDimension` — a real
    measurement-bearing class — and would drop every future dimensional type the same way. For
    a tool whose one job is not to hide a loss, an unknown type must fail toward NOISE, which a
    reader can dismiss, never toward silence, which nobody can.

    This test deliberately does NOT use the implementation's own type names: it asserts that a
    type the module has never heard of still counts. The previous version supplied the exact
    strings the code checks and asserted their classification, which could only ever confirm
    the implementation to itself.
    """
    before = _FakeDrawing(
        {"sd0": "25", "whatever0": "9", "title_block": "DRAWING"},
        types={
            "sd0": "SafeDimension",
            "whatever0": "SomeFutureDimensionKind",
            "title_block": "TitleBlock",
        },
    )
    after = _FakeDrawing({"title_block": "DRAWING"}, types={"title_block": "TitleBlock"})

    lost = diff_builds(before, after)["dimensions_lost"]
    assert "sd0" in lost, "SafeDimension is a measurement"
    assert "whatever0" in lost, "an unknown type must fail toward noise, not silence"
    assert "title_block" not in lost, "known furniture is still excluded"


def test_a_same_name_same_label_replacement_is_invisible_without_identity():
    """The limit the module documents, pinned so the documentation cannot drift from the
    behaviour (Codex #1001 r2) — now scoped to where it actually still applies.

    A name is a registry slot. If a different measurement takes the slot AND renders the same
    label, every result map is empty. #1002 closed this for annotations whose renderer records
    a `DimensionId` (see the test below); it remains true for the renderers that record none —
    the rotational OD/bore group, #754 — so a clean diff still does not establish that the
    measurements were preserved.

    Asserting a KNOWN BLIND SPOT, not desired behaviour. Delete it when every renderer records
    identity.
    """
    before = _FakeDrawing({"dim_od": "40"})  # no identity: the direct-placing renderers
    after = _FakeDrawing({"dim_od": "40"})  # different measurement, same slot, same text

    diff = diff_builds(before, after)
    assert diff["dimensions_lost"] == {}
    assert diff["dimensions_gained"] == {}
    assert diff["dimensions_changed"] == {}
    assert diff["measurements_substituted"] == {}
    assert explain(diff) == [], "invisible — and the docstring says so rather than implying it"


def test_a_same_name_same_label_replacement_is_caught_with_identity():
    """The blind spot above, closed wherever the renderer recorded what it drew (#1002).

    Same name, same label, so every other map in the diff is empty — the only thing that
    differs is what the annotation IS. This is the case the module previously could not see at
    all, and the reason measurement identity was worth threading.
    """
    before = _FakeDrawing({"m_x0": "70"}, identities={"m_x0": ("hole@(10,5,5)/z", "location")})
    after = _FakeDrawing({"m_x0": "70"}, identities={"m_x0": ("slot@(10,5,5)/z", "width.length")})

    diff = diff_builds(before, after)
    assert diff["dimensions_lost"] == {} and diff["dimensions_changed"] == {}
    assert diff["measurements_substituted"] == {
        "m_x0": ([("hole", "location")], [("slot", "width.length")])
    }
    assert explain(diff)[0].startswith("SUBSTITUTED: m_x0"), "a real substitution is an alarm"


def test_moving_a_feature_is_not_a_substitution():
    """A perturbation study MOVES features, and `feature_key` embeds the origin and the
    scalars — so the full ledger key changes on every run of the experiment this module
    exists to run.

    Comparing that key reported EVERY dimension of EVERY perturbed feature as changed
    identity: on a real 50x40 -> 50x50 box, three noise lines on a three-dimension drawing
    (Codex #1002 r1). The correspondence key is the fix — same hole, same measurement, so
    nothing is reported but the value change.
    """
    before = _FakeDrawing({"m_x0": "70"}, identities={"m_x0": ("hole@(10,5,5)/z", "location")})
    after = _FakeDrawing({"m_x0": "90"}, identities={"m_x0": ("hole@(30,5,5)/z", "location")})

    diff = diff_builds(before, after)
    assert diff["measurements_substituted"] == {}, "the same measurement, moved"
    assert explain(diff) == ["changed: m_x0 70 -> 90"]


def test_a_suppression_on_a_different_kind_of_feature_does_not_explain_this_loss():
    """The canary for the attribution fix (#1002), on the shape the historical bug had.

    The first cut matched a suppression's parameter STEM against the annotation's NAME by
    substring, so an ENVELOPE `width.length` suppression claimed a lost SLOT width — the
    stem "width" appears in the name (Codex #1001 r1). Identity separates them because the
    feature kinds differ, which is exactly what a name-based match cannot see.
    """
    before = _FakeDrawing(
        {"m_slot_width0": "20"},
        identities={"m_slot_width0": ("slot@(0,0,0)/z", "width.length")},
    )
    after = _FakeDrawing({}, [_supp("width.length", "square footprint", feature="envelope@z")])

    diff = diff_builds(before, after)
    assert "m_slot_width0" in diff["dimensions_lost"]
    assert diff["candidate_explanations"] == {}, "another KIND's rule explains nothing"
    assert explain(diff)[0].endswith("nothing claims it")


def test_two_features_of_one_kind_are_not_separated_by_the_cross_build_key():
    """The price of matching across builds, asserted rather than left to be discovered.

    The cross-build key is `(feature KIND, parameter_id)` — it must be, because the full
    ledger key embeds coordinates and scalars that any perturbation changes. So a rule that
    fired on a DIFFERENT slot still reads as a candidate explanation for this one.

    Tolerable only because attribution never cancels an alarm: the loss is still reported,
    with a hint that may be the wrong slot. Narrowing it needs a cross-build feature
    correspondence the engine does not have (ADR 0016 leaves a durable `FeatureId` open).
    """
    before = _FakeDrawing(
        {"m_slot0_width": "20"},
        identities={"m_slot0_width": ("slot@(0,0,0)/z", "width.length")},
    )
    after = _FakeDrawing({}, [_supp("width.length", "coincident", feature="slot@(99,0,0)/z")])

    diff = diff_builds(before, after)
    assert diff["candidate_explanations"] == {"m_slot0_width": ["coincident"]}, "same kind"
    assert "m_slot0_width" in diff["dimensions_lost"], "hinted, never cancelled"


def test_a_suppression_on_the_same_measurement_does_explain_the_loss():
    """The other half: an exact `(feature, parameter_id)` match IS the attribution, and the
    hint still annotates the loss rather than removing it."""
    ident = ("slot@(0,0,0)/z", "width.length")
    before = _FakeDrawing({"m_width0": "20"}, identities={"m_width0": ident})
    after = _FakeDrawing({}, [_supp("width.length", "coincident", feature="slot@(0,0,0)/z")])

    diff = diff_builds(before, after)
    assert diff["candidate_explanations"] == {"m_width0": ["coincident"]}
    assert "m_width0" in diff["dimensions_lost"], "explained, still not cancelled"


def test_a_labelless_callout_loss_is_still_detected():
    """A legacy/external leader may expose no semantic label even when it is a callout.

    Generated hole leaders now retain their callout content (#1142), but audit remains
    compatible with older or user-supplied labelless leaders. The first cut required a
    non-empty label, so those were dropped from the comparison entirely and a vanished
    callout produced NO loss. That is the single thing this module must never do, and the
    type filter added to remove furniture is what introduced it.

    Presence is the signal; the label is extra detail on it.
    """
    before = _FakeDrawing(
        {"hc_plan0": "", "m_env_width": "90"},
        types={"hc_plan0": "Leader", "m_env_width": "Dimension"},
    )
    after = _FakeDrawing({"m_env_width": "90"}, types={"m_env_width": "Dimension"})

    diff = diff_builds(before, after)
    assert "hc_plan0" in diff["dimensions_lost"], "a labelless callout still counts"
    assert explain(diff)[0].startswith("LOST: hc_plan0")


def test_a_generated_callout_content_change_is_observable():
    """Generated hole leaders expose their geometric callout's semantic content (#1142)."""
    before = _FakeDrawing({"hc_plan0": "⌀8 THRU"}, types={"hc_plan0": "Leader"})
    after = _FakeDrawing({"hc_plan0": "⌀12 THRU"}, types={"hc_plan0": "Leader"})

    diff = diff_builds(before, after)
    assert diff["dimensions_changed"] == {"hc_plan0": ("⌀8 THRU", "⌀12 THRU")}
    assert explain(diff) == ["changed: hc_plan0 ⌀8 THRU -> ⌀12 THRU"]


def test_no_difference_reports_nothing():
    """No false positives: two identical builds produce an empty report, or the signal is
    noise and nobody reads it."""
    dwg = _FakeDrawing({"m_env_width": "50"}, [_supp("depth.length", "rotational OD")])
    diff = diff_builds(dwg, dwg)
    assert diff["dimensions_lost"] == diff["dimensions_gained"] == {}
    assert diff["suppressions_gained"] == diff["suppressions_lost"] == []
    assert explain(diff) == []


def test_it_works_on_real_drawings():
    """One end-to-end case, so the duck types above cannot drift from the real surface.

    An X-turned shaft suppresses its cross-axis extent via a LIVE rule (the OD conveys it), so
    this exercises a genuine ledger entry rather than a fabricated one.
    """
    prismatic = build_drawing(Box(60, 40, 20), number="X")
    turned = build_drawing(Rot(0, 90, 0) * Cylinder(10, 40), number="X")

    diff = diff_builds(prismatic, turned)
    assert "m_env_depth" in diff["dimensions_lost"], "the turned part states no depth"
    reasons = " ".join(r for _, _, r in diff["suppressions_gained"])
    assert "rotational OD" in reasons, "and the ledger says which rule took it"


def test_real_mixed_featureless_and_feature_suppressions_have_a_total_order():
    """#1077: an authored set can omit both a featureless overall dimension and a
    feature-owned one. Those public ledger rows carry ``feature=None`` and ``feature=str``;
    sorting the raw tuples crashes before the audit can report the loss.

    Exercise both directions: gained suppressions in the authored build and lost
    suppressions in the inverse comparison must preserve the public values unchanged.
    """
    part = Rot(0, 90, 0) * Cylinder(4, 20) + Pos(15, 0, 0) * Rot(0, 90, 0) * Cylinder(6, 10)
    automatic = Sheet.from_part(part).build()
    sheet = Sheet.from_part(part)
    steps = [feature for feature in sheet.features if feature.kind == "step"]
    assert len(steps) == 2
    sheet.dimension(steps[0], "step.length")
    authored = sheet.build()

    forward = diff_builds(automatic, authored)
    assert forward["dimensions_lost"]["m_steplen1"] == "10"
    assert forward["candidate_explanations"]["m_steplen1"] == ["not in the authored dimension set"]
    assert any(feature is None for feature, _parameter, _reason in forward["suppressions_gained"])

    reverse = diff_builds(authored, automatic)
    assert reverse["suppressions_lost"] == forward["suppressions_gained"]
    assert any(feature is None for feature, _parameter, _reason in reverse["suppressions_lost"])


def test_a_real_build_records_which_measurement_its_location_dims_draw():
    """End-to-end (#1002): the duck types above encode what `diff_builds` *reads*, so they
    cannot show that the engine actually WRITES any of it. This does.

    Two things matter and both are asserted: identity reaches the registry from a real render
    pass, and it is the SAME key shape `suppressions()` reports — that shared shape is the
    whole point, since it is what lets a drawn measurement and a suppressed one be compared
    without matching engine-assigned names by substring.
    """
    with BuildPart() as p:
        Box(50, 40, 10)
        with Locations((10, 5, 0)):
            Hole(4)
    dwg = build_drawing(p.part)

    keyed = {n: dwg.measurement_keys(n) for n in dwg.annotations()}
    identified = {n: k for n, k in keyed.items() if k}
    assert identified, "the compiled-plan renderers must record what they drew"
    for name, keys in identified.items():
        for key in keys:
            assert set(key) == {"feature", "parameter_id"}, f"{name}: the ledger's key shape"

    # The two threaded groups, named rather than counted: a count would keep passing if a
    # renderer stopped recording and another started. And the PARAMETER is asserted, not just
    # the feature (Codex #1002 r5): checking only the feature prefix would let an envelope
    # renderer record its *depth* under `m_env_width` and still pass, which is precisely the
    # mis-threading the audit would then report as exact.
    assert identified["m_locx0"] == [
        {
            "feature": identified["m_locx0"][0]["feature"],
            "parameter_id": "location.location",
        }
    ]
    assert identified["m_locx0"][0]["feature"].startswith("hole@"), "located hole"
    assert identified["m_env_width"][0]["parameter_id"] == "width.length"
    assert identified["m_env_depth"][0]["parameter_id"] == "depth.length"
    assert identified["dim_height"][0]["parameter_id"] == "height.length"
    assert identified["m_env_width"][0]["feature"].startswith("envelope@"), "overall extent"

    assert dwg.measurement_keys("title_block") == [], "furniture measures nothing"
    assert [key["parameter_id"] for key in dwg.measurement_keys("hc_plan0")] == ["bore.diameter"]


def test_identity_survives_the_repair_loops_snapshot_and_restore():
    """A repair pass may roll back a worse placement by restoring a registry snapshot. The
    view and feature tags are snapshotted for exactly this reason — a restore that dropped
    them would leave provenance referencing names the rollback removed.

    Measurement identity is a peer of those and must round-trip the same way. A stale or
    missing id is worse here than elsewhere: the audit treats a recorded id as EXACT.
    """
    from draftwright.registry import AnnotationRegistry

    reg = AnnotationRegistry()
    obj = object()
    reg.add(obj, "m_x0", "plan", feature="f", measurement="mid")
    snap = reg.snapshot()

    reg.add(object(), "m_x0", "plan", feature="g", measurement="other")
    assert reg.measurement_of("m_x0") == ("other",)

    reg.restore(snap)
    assert reg.measurement_of("m_x0") == ("mid",), "a rollback restores what was drawn"


def test_re_adding_a_name_without_an_identity_clears_the_old_one():
    """The rule the view and feature tags already follow, and it matters more here: a
    replacement draws whatever the new caller says it draws, including nothing identifiable.

    Inheriting the displaced annotation's id would hand the audit a STALE identity it then
    treats as exact — worse than the honest `None` that reads as "unknown".
    """
    from draftwright.registry import AnnotationRegistry

    reg = AnnotationRegistry()
    reg.add(object(), "m_x0", "plan", feature="f", measurement="mid")
    reg.add(object(), "m_x0", "plan")

    assert reg.measurement_of("m_x0") == (), "cleared, not inherited"


def test_an_annotation_can_draw_several_measurements():
    """The registry stores a TUPLE, and the relationship really is one-to-many: a compound
    hole callout renders bore diameter, depth and counterbore as ONE annotation, each
    independently suppressible.

    ADR 0016 states this outright — the provenance channel "has to become
    `name -> tuple[DimensionId, ...]` before per-dimension resolution is possible at all"
    (#886). Storing one id would make the audit's "exact when present" promise false for
    every other measurement the annotation carries, so the storage models it from the start
    even though today's threaded renderers each supply exactly one.
    """
    from draftwright.registry import AnnotationRegistry

    reg = AnnotationRegistry()
    reg.add(object(), "hc0", "plan", measurement=["bore.diameter", "bore.depth"])

    assert reg.measurement_of("hc0") == ("bore.diameter", "bore.depth")


def test_a_bare_id_and_a_sequence_are_both_accepted():
    """The common renderer draws one measurement and passes the `DimensionId` it already
    holds — `measurement=pd.id`. Normalising at the boundary keeps that call site honest
    without the storage pretending the relationship is one-to-one."""
    from draftwright.registry import AnnotationRegistry

    reg = AnnotationRegistry()
    reg.add(object(), "a", "plan", measurement="one")
    reg.add(object(), "b", "plan", measurement=("one", "two"))
    reg.add(object(), "c", "plan", measurement=[None])

    assert reg.measurement_of("a") == ("one",)
    assert reg.measurement_of("b") == ("one", "two")
    assert reg.measurement_of("c") == (), "a sequence of nothing is still unknown"


def test_a_grouped_callout_losing_a_member_is_a_substitution():
    """Multiplicity is part of the identity (Codex #1002 r5).

    A `4× R5` fillet callout collapses four equal fillets into one annotation drawing four
    measurements. Lose one and it becomes `3× R5` — same name, same feature kind, same
    parameter. Comparing DISTINCT keys made that invisible: every result map came back empty
    while a measurement had silently left the drawing, which is the exact failure this module
    exists to prevent. A multiset sees it.
    """
    fillet_a = "fillet@(-28,-18,0)/z[radius=5.000]"
    fillet_b = "fillet@(-28,18,0)/z[radius=5.000]"
    before = _FakeDrawing(
        {"m_fillet_z0": "4× R5"},
        identities={"m_fillet_z0": [(fillet_a, "fillet.radius"), (fillet_b, "fillet.radius")]},
    )
    after = _FakeDrawing(
        {"m_fillet_z0": "4× R5"},  # label unchanged too — identity is the only signal
        identities={"m_fillet_z0": [(fillet_a, "fillet.radius")]},
    )

    diff = diff_builds(before, after)
    assert diff["dimensions_lost"] == {} and diff["dimensions_changed"] == {}
    assert "m_fillet_z0" in diff["measurements_substituted"], "a dropped member is a change"
    assert any(line.startswith("SUBSTITUTED") for line in explain(diff))


def test_a_location_dim_two_features_share_records_both_measurements():
    """The dedup path, which the single-hole canary never reached (#1002 r4).

    Two distinct holes at the same X collapse to ONE `m_locx0`. That dim is deliberately
    feature-*unowned* so `drop(feature)` cannot strip a sibling's dimension (ADR 0010) — but
    the first cut let the measurement follow the feature and recorded nothing, conflating an
    ownership rule with an identity one. A shared dim carries BOTH holes' feature-level
    location identities; directional X evidence is separate while #883 remains open.

    Asserted end to end because no source scan can see it: the renderer passes a variable, so
    `measurement=_xmid` satisfies the syntax ratchet whether or not it holds anything.
    """
    with BuildPart() as p:
        Box(60, 50, 10)
        with Locations((10, 5, 0)):
            Hole(3)
        with Locations((10, 15, 0)):
            Hole(5)
    dwg = build_drawing(p.part)

    assert dwg.registry.feature_of("m_locx0") is None, "shared dim stays unowned (ADR 0010)"
    keys = dwg.measurement_keys("m_locx0")
    assert len(keys) == 2, "it measures BOTH holes' feature location, not neither"
    assert {k["parameter_id"] for k in keys} == {"location.location"}
    assert len({k["feature"] for k in keys}) == 2, "two distinct holes, not one repeated"


def test_a_real_build_records_identity_for_the_machined_feature_callouts():
    """Behavioural counterpart to the two syntax ratchets (#1002 r3).

    The shared `_leader_callout_pass` renders chamfers, fillets, flats, pockets, grooves and
    boss diameters, and recorded nothing for any of them while the module docstring listed a
    four-item exception set. Source scans alone would not have caught that — the keyword was
    simply absent from a path nothing scanned — so this reads a real build's output.

    The fillet case is the important one: four equal fillets collapse to ONE `4× R5` callout,
    so the annotation genuinely draws four measurements. That is the one-to-many relationship
    ADR 0016 requires the channel to carry (#886), exercised end to end rather than asserted
    on the registry in isolation.
    """
    from build123d import Axis, Box, Mode, chamfer, fillet

    part = Box(60, 40, 20)
    chamfered = build_drawing(chamfer(part.edges().filter_by(Axis.Z), 3))
    keys = chamfered.measurement_keys("m_chamfer_z0")
    assert [k["parameter_id"] for k in keys] == ["chamfer.length"]
    assert keys[0]["feature"].startswith("chamfer@")

    filleted = build_drawing(fillet(part.edges().filter_by(Axis.Z), 5))
    grouped = filleted.measurement_keys("m_fillet_z0")
    assert len(grouped) == 4, "one grouped callout, four collapsed members"
    assert {k["parameter_id"] for k in grouped} == {"fillet.radius"}
    assert len({k["feature"] for k in grouped}) == 4, "four distinct fillets, not one repeated"

    # A pocket is the compound case with DISTINCT parameters, so it is the one that would
    # expose a swapped width/depth id — the failure a presence-only check cannot see.
    with BuildPart() as pk:
        Box(80, 60, 20)
        with Locations((0, 0, 10)):
            Box(30, 20, 6, mode=Mode.SUBTRACT)
    pocket = build_drawing(pk.part).measurement_keys("m_pocket_yx0")
    assert [k["parameter_id"] for k in pocket] == [
        "pocket_width.length",
        "pocket_length.length",
        "pocket_depth.length",
    ], "width, length and depth in the order the label prints them"
    assert {k["feature"].split("@")[0] for k in pocket} == {"pocket"}


def _records_a_measurement(call) -> bool:
    """True when *call* passes a ``measurement=`` that could actually carry an id.

    A literal satisfies the keyword while recording nothing — ``measurement=None`` and
    ``measurement=()`` both leave the annotation unidentified — so testing only that the
    KEYWORD IS PRESENT yields a ratchet that cannot fail, which is precisely what the first
    cut did (Codex #1002 r3). Constants and the empty-tuple literal are rejected; a name,
    attribute or call is accepted.

    Syntax, not dataflow: a variable that happens to hold ``None`` still passes here. That
    limit is stated rather than papered over — the behavioural counterpart is
    `test_a_real_build_records_which_measurement_its_location_dims_draw`, which reads a real
    build's output rather than its source.
    """
    import ast

    for kw in call.keywords:
        if kw.arg != "measurement":
            continue
        if isinstance(kw.value, ast.Constant):
            return False
        return not (isinstance(kw.value, ast.Tuple) and not kw.value.elts)
    return False


def test_every_direct_placement_records_identity_or_says_why_not():
    """The `ctx.place(...)` paths — the half the candidate ratchet never saw (#1002 r3).

    The first ratchet scanned `CorridorCandidate(...)` only, so the shared machined-feature
    renderer `_leader_callout_pass` — chamfers, fillets, flats, pockets, grooves, boss
    diameters — placed with no identity at all while `audit.py` told readers the only gaps
    were hole callouts, PMI, GD&T and the rotational group. The claim was false, and nothing
    could have caught it.

    Scanned across the WHOLE `annotations/` package, not one module. The first version of
    this ratchet read `from_model.py` alone and so missed the two pattern-callout producers
    in `holes.py` — the same mistake one level up, caught only because the tuple arity change
    broke them at runtime. A guard narrower than its claim is the recurring defect here.

    Every direct place in the package now either records a measurement or is listed below
    with the reason it carries none. **The EXEMPT map IS the exception inventory** `audit.py`
    refuses to restate in prose — and a stale entry fails too, so it cannot rot into a list
    of excuses for renderers that have since been fixed.

    Its reach ends at the placement site. `_leader_callout_pass` receives identity down a job
    tuple, so a *producer* that passed `()` would satisfy this while recording nothing — the
    job-tuple arity catches a dropped element, and
    `test_a_real_build_records_identity_for_the_machined_feature_callouts` catches an empty
    one for the kinds it builds. Stated because a canary for that case did NOT fire here.
    """
    import ast
    import pathlib

    EXEMPT = {
        # -- furniture: nothing here measures anything ------------------------------
        ("from_model.py", "render_centermarks"): "a centre mark measures nothing",
        (
            "from_model.py",
            "render_local_turned_centerlines._place",
        ): "centrelines are furniture",
        ("holes.py", "add_feature_furniture"): "centre marks are furniture",
        ("holes.py", "_add_furniture"): "bolt-circle centre-cross is furniture",
        ("balloons.py", "_render_balloon"): "a balloon tags a hole, it does not measure it",
        ("sections.py", "_add_section_view"): "section geometry + its caption",
        ("sections.py", "_add_section_letters"): "the 'A' identification letters",
        ("sections.py", "_add_cutting_plane_arrows"): "ISO 128-44 arrows are not dimensions",
        (
            "sections.py",
            "_reserve_section_row",
        ): "a provisional cutting-plane line is layout furniture",
        ("sections.py", "_place_cutting_plane"): "final cutting-plane layout furniture",
        ("sections.py", "_render_detail"): "detail-view geometry, caption and circle",
        # -- the direct-placing rotational group (#754) -----------------------------
        # `_diameter_row_below`, `_diameter_column_left` and the `dim_od` placements in
        # `render_rotational` USED to sit here. They now thread the compiled claim, so the
        # exemption is stale and deleted rather than left to cover a case it no longer
        # describes (#1227). `render_rotational` still places centre lines, which are not
        # measurements — that is the only reason it remains.
        ("from_model.py", "render_rotational"): "centre lines are not measurements",
        # -- identity arrives by another route --------------------------------------
        ("from_model.py", "_reroute_crossing_diameters"): "reapplies the saved identity",
        ("sections.py", "_resolve_details"): "reapplies the saved identity",
        # -- not a measurement (ADR 0011) -------------------------------------------
        ("from_model.py", "_drop._retry"): "GD&T relaxed-side retry places a control frame",
        ("from_model.py", "_pmi_queue_options"): "PMI records are not compiled dimensions",
        ("from_model.py", "render_gdt"): "a control frame is not a measurement",
    }

    unrecorded, used = [], set()
    for path in sorted(pathlib.Path("src/draftwright/annotations").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        fns = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)]

        def enclosing(line, fns=fns):
            """The DOTTED path of enclosing functions, e.g. ``locate._place``.

            Bare names are not unique: `holes.py` has two nested `_place` helpers, one for
            location dims and one for pitch dims, so a single `("holes.py", "_place")` entry
            exempted both — and silently covered the one that had just been fixed. Caught by
            a canary that failed to fire, which is the only way that class of hole shows up.
            """
            chain = sorted(
                (f for f in fns if f.lineno <= line <= (f.end_lineno or 0)),
                key=lambda f: f.end_lineno - f.lineno,
                reverse=True,
            )
            return ".".join(f.name for f in chain[-2:]) if chain else "?"

        for node in ast.walk(tree):
            is_place = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "place"
            )
            is_cand = isinstance(node, ast.Call) and getattr(node.func, "id", None) == (
                "CorridorCandidate"
            )
            if not (is_place or is_cand) or _records_a_measurement(node):
                continue
            key = (path.name, enclosing(node.lineno))
            if key in EXEMPT:
                used.add(key)
                continue
            unrecorded.append((path.name, node.lineno, key[1]))

    assert not unrecorded, (
        f"these place an annotation with no measurement identity: {unrecorded}. "
        "Pass measurement=<the ApprovedDimension>.id, or add the enclosing renderer to "
        "EXEMPT with the reason it carries no compiled measurement."
    )
    assert set(EXEMPT) == used, (
        f"stale EXEMPT entries — these renderers no longer need an exemption: "
        f"{sorted(set(EXEMPT) - used)}. Delete them, or the inventory becomes a list of "
        "excuses for code that has since been fixed."
    )
