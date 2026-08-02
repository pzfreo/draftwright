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

from build123d import Box, Cylinder, Rot

from draftwright import build_drawing
from draftwright.audit import diff_builds, explain


class _FakeDrawing:
    """The three public reads `diff_builds` uses, and nothing else."""

    def __init__(
        self,
        dims: dict[str, str],
        suppressions: list[dict] | None = None,
        types: dict[str, str] | None = None,
    ):
        self._dims = dims
        self._supp = suppressions or []
        self._types = types or {}

    def annotations(self):
        return {n: self._types.get(n, "Dimension") for n in self._dims}

    def get_annotation(self, name):
        return type("A", (), {"label": self._dims[name]})()

    def suppressions(self):
        return list(self._supp)


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
    before = _FakeDrawing({"m_env_width": "50", "m_env_depth": "40", "dim_height": "30"})
    after = _FakeDrawing(
        {"dim_height": "30"},
        [_supp("width.length", "square footprint"), _supp("depth.length", "square footprint")],
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


def test_a_same_name_same_label_replacement_is_invisible():
    """The limit the module documents, pinned so the documentation cannot quietly drift from
    the behaviour (Codex #1001 r2).

    A name is a registry slot. If a different measurement takes the slot AND renders the same
    label, every result map is empty — the substitution is wholly invisible. A clean diff
    therefore does not establish that the measurements were preserved.

    Asserting a KNOWN BLIND SPOT, not desired behaviour. It fails the day #1002 gives
    annotations real identity, which is exactly when someone should come back and delete it.
    """
    before = _FakeDrawing({"m_locx0": "70"})
    after = _FakeDrawing({"m_locx0": "70"})  # different measurement, same slot, same text

    diff = diff_builds(before, after)
    assert diff["dimensions_lost"] == {}
    assert diff["dimensions_gained"] == {}
    assert diff["dimensions_changed"] == {}
    assert explain(diff) == [], "invisible — and the docstring says so rather than implying it"


def test_a_labelless_callout_loss_is_still_detected():
    """A hole callout renders as a `Leader` whose own `label` is "" — its text lives on an
    attached callout object, and on some paths nowhere readable at all.

    The first cut required a non-empty label, so those were dropped from the comparison
    entirely and a vanished hole callout produced NO loss. That is the single thing this
    module must never do, and the type filter added to remove furniture is what introduced it.

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


def test_a_callout_content_change_is_a_documented_blind_spot():
    """Presence is seen; CONTENT is not, for hole callouts.

    A callout renders as a `Leader` whose `label` is "" — the text is built at draw time and
    never exposed on the object. Measured on real builds: changing a bore from 8 to 12
    produces an identical diff. Pinned as a KNOWN LIMIT so the docstring cannot drift from the
    behaviour, and so the day the text becomes readable this test fails and someone deletes it.
    """
    before = _FakeDrawing({"hc_plan0": ""}, types={"hc_plan0": "Leader"})
    after = _FakeDrawing({"hc_plan0": ""}, types={"hc_plan0": "Leader"})  # different bore

    diff = diff_builds(before, after)
    assert diff["dimensions_changed"] == {}, "no readable text, so no detectable change"
    assert explain(diff) == []


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
