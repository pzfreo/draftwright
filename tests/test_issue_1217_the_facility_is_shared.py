"""#1217 PR 3 — the verification facility is family-agnostic, and stays that way.

"Shared" is a claim, and the only thing that proves it is a second consumer. A facility
exercised by holes alone is a hole verifier with general-sounding names.

Measured: it costs **nothing**. Slots, pockets, bosses and bores, chamfers and turned steps all
produce verifiable claims through `registry.measurement_of` — the one ADR 0010 seam — with zero
changes to `linting/evidence.py`. The epic's PR-3 gate was "it costs a producer and zero changes
to the facility"; the producers already existed, because provenance is threaded once and not per
family.

So the useful content of this slice is not the second family. It is the RATCHET: a new family
whose annotations state a measured value without threading provenance would be invisible to the
verifier, and nothing would say so. `TestNoMeasuredAnnotationEscapesUnclaimed` is what says so.
"""

from __future__ import annotations

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot, chamfer

# `_claims` has ONE definition, in the file that introduced it (#1225 review, finding 10). Two
# byte-identical copies is how two sides drift apart, which is the whole subject of this epic.
from test_issue_1217_claimed_representations import _verified as _claims

from draftwright.builder import build_drawing
from draftwright.linting.evidence import rendered_numbers

_C = (Align.CENTER, Align.CENTER, Align.CENTER)

#: Annotations that legitimately render digits while carrying no measurement claim. One entry,
#: measured across every fixture below: the detail-view caption, whose numbers are a SCALE RATIO
#: ("DETAIL A — SCALE 2.5:1") and not a fact about the part. Registered by annotation-name stem
#: so a second such case has to be argued rather than absorbed.
_NON_MEASURING_ANNOTATIONS = ("detail_caption",)

#: Annotations that render a real measured value carrying no compiled identity to thread — the
#: `ctx.place` seam cannot fix these because there is no id to pass. All three are #1230:
#:
#:   * `m_steplen0` on grm03 — a synthetic step head-block drawing the SUM of two steps
#:     (0.5 + 2.0 = 2.5). No plan entry holds 2.5, and claiming the two members would be a
#:     false claim: the annotation does not draw either of them.
#:   * `dim_height` on grm03 and on the bored tube — measured, `plan.ladder("overall_height")`
#:     yields a rung with `id=None` whenever the model has NO `EnvelopeFeature`, and a rung
#:     carrying an `EnvelopeFeature` id when it has one (`compiled.py` builds the id from that
#:     feature, and `_dim_id` returns None for a missing one). "Rotational" is the wrong rule
#:     and an earlier draft used it: grm03 is not rotational — it recognises as four steps, a
#:     boss and a hole with no envelope — while `if_step_flat_across_cylinder` IS rotational,
#:     HAS an envelope, and does not escape. Deciding what the rung means where there is no
#:     envelope is a modelling call, not a threading edit (#1225 review, finding 7).
#:
#: Pinned exactly, per part and name, so a fourth occurrence fails rather than being absorbed —
#: this register is a defect ledger, not an exemption list, and it should shrink to nothing.
#: Claims the drawing does NOT bear out, pinned the same way and for the same reason. One entry:
#: `m_locy7` on nist_ctc_02 claims `location_slot.length` (94.1) and renders 430.0, which is
#: **#1219** and reproduces identically on `main`. It is registered rather than tolerated because
#: the fast sweep asserts `not unconfirmed` outright and the slow sweep must not be the weaker
#: gate — a wrong id on a CTC-only path would otherwise survive exactly as one did in fast-tier
#: code until round 2 of this PR's review (#1225 review, round 3, finding 1).
_UNCONFIRMED_CLAIMS = {
    ("nist_ctc_02_asme1_ap203.stp", "m_locy7", "location_slot.length"),
}

_UNPLANNED_VALUES = {
    ("grm03_thumbwheel_drive_screw.step", "m_steplen0"),
    ("grm03_thumbwheel_drive_screw.step", "dim_height"),
    ("bored tube", "dim_height"),
}


def _slot_part():
    return (
        Box(90, 50, 12, align=_C)
        - Pos(-20, 0, 0) * Box(24, 8, 30, align=_C)
        - Pos(20, 0, 0) * Box(24, 8, 30, align=_C)
    )


def _pocket_part():
    return Box(90, 60, 20, align=_C) - Pos(10, 5, 6) * Box(40, 25, 10, align=_C)


def _boss_and_bore_part():
    return (
        Box(60, 60, 10, align=_C)
        + Pos(0, 0, 7) * Cylinder(8, 4, align=_C)
        - Cylinder(3, 40, align=_C)
    )


def _turned_part():
    return Cylinder(10, 30, align=_C) + Pos(0, 0, 20) * Cylinder(6, 10, align=_C)


def _bored_tube():
    # #1227's exact part. Every annotation on this sheet was unclaimed when that issue was
    # filed; `dim_od` and `ldr_z0` are fixed here, and `dim_height` is registered below (#1230).
    return Cylinder(20, 60, align=_C) - Cylinder(6, 70, align=_C)


def _chamfer_part():
    return chamfer(Box(80, 50, 20, align=_C).edges().sort_by()[0:2], 3)


#: The five families this slice claims, each with the cheapest part that exercises it, plus
#: `bored tube` — #1227's own part, which is not a sixth family but the regression guard for the
#: threading fixed here. The ratchet sweeps all six.
_FAMILY_PARTS = {
    "slot": _slot_part,
    "pocket": _pocket_part,
    "boss and bore": _boss_and_bore_part,
    "turned step": _turned_part,
    "chamfer": _chamfer_part,
    "bored tube": _bored_tube,
}


#: Module-scoped build cache. The ratchet and the register test sweep the SAME corpus, and the
#: exemption test rebuilds a fixture the sweep already built; without this the file builds
#: `issue_915_case_study_2` three times. (The family parts are still built once per
#: `TestASecondFamilyCostsNothing` test, which deliberately does not share state with the sweep.)
#: Drawings are read-only here
#: — nothing below mutates one — so sharing is safe and roughly halves the file's wall clock,
#: which is what keeps it inside the fast tier on ubuntu (#1225 review, findings 1 and 10).
_BUILT: dict[str, object] = {}


def _drawing(key: str, source):
    # Keep `title` digit-free (`number` never reaches `label`, so "N-1" is fine — measured).
    # A TitleBlock bakes its text into geometry, so
    # `rendered_numbers` sees nothing and it counts as silent furniture — but `label` holds the
    # title verbatim, so a title like "T1" would make the title block a ratchet ESCAPE with a
    # thoroughly confusing message (#1225 review, finding 11).
    if key not in _BUILT:
        _BUILT[key] = build_drawing(
            source() if callable(source) else source, title="T", number="N-1"
        )
    return _BUILT[key]


def _families(drawing) -> set[str]:
    return {claim.parameter_id.split(".")[0] for claim in _claims(drawing)}


class TestASecondFamilyCostsNothing:
    """Each of these was verified with zero edits to the facility. If one ever needs an edit,
    the facility was not shared and this is the class that says so."""

    def test_slots_are_verified(self):
        drawing = build_drawing(_slot_part(), title="T", number="N-1")
        claims = [c for c in _claims(drawing) if "slot" in c.parameter_id]
        assert {c.parameter_id for c in claims} >= {
            "slot_length.length",
            "slot_width.length",
        }, {c.parameter_id for c in claims}
        assert {c.state for c in claims} == {"confirmed"}, [
            (c.annotation, c.parameter_id, c.state) for c in claims if c.state != "confirmed"
        ]

    def test_pockets_are_verified(self):
        drawing = build_drawing(_pocket_part(), title="T", number="N-1")
        assert {"pocket_depth", "pocket_length", "pocket_width"} <= _families(drawing)
        assert {c.state for c in _claims(drawing)} == {"confirmed"}

    def test_bosses_and_bores_are_verified(self):
        drawing = build_drawing(_boss_and_bore_part(), title="T", number="N-1")
        assert {"bore", "boss", "boss_height"} <= _families(drawing)
        assert {c.state for c in _claims(drawing)} == {"confirmed"}

    def test_a_turned_profile_is_verified(self):
        drawing = build_drawing(_turned_part(), title="T", number="N-1")
        assert "step" in _families(drawing)
        assert {c.state for c in _claims(drawing)} == {"confirmed"}

    def test_chamfers_are_verified(self):
        # The prose says five families; four tests would make that four. #1225 review, finding 8.
        drawing = build_drawing(_chamfer_part(), title="T", number="N-1")
        assert "chamfer" in _families(drawing)
        assert {c.state for c in _claims(drawing)} == {"confirmed"}

    def test_a_hand_placed_callout_carries_its_claim_too(self):
        # `sheet.callout()` builds the placer's item tuple itself rather than going through
        # `_items`, so it is a second, independent threading site. Dropping its id survived the
        # whole callout suite until this test existed (#1225 review, mutation M13) — the auto
        # pass and the manual pass have to be verifiable on the same terms, or a drawing's
        # coverage depends on how its dimensions were authored.
        shaft = Rot(0, 90, 0) * (Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        drawing = build_drawing(shaft, auto_dims=False, title="T", number="N-1")
        step = next(f for f in drawing.model().features if f.kind == "step")
        name = drawing.callout(step)
        assert name.startswith("m_dia_x")
        assert drawing.registry.measurement_of(name), (
            f"{name} was placed by hand and claims nothing, so no verifier can see it"
        )
        placed = [c for c in _claims(drawing) if c.annotation == name]
        assert placed and {c.state for c in placed} == {"confirmed"}, placed

    def test_the_families_are_genuinely_different_parameters(self):
        # Guards the tests above from passing on a shared parameter. If every family resolved
        # to the same handful of ids, "five families" would be one family five times.
        parts = {
            "slot": Box(90, 50, 12, align=_C) - Pos(0, 0, 0) * Box(24, 8, 30, align=_C),
            "pocket": _pocket_part(),
            "boss": Box(60, 60, 10, align=_C) + Pos(0, 0, 7) * Cylinder(8, 4, align=_C),
        }
        seen = {
            name: _families(build_drawing(p, title="T", number="N-1")) for name, p in parts.items()
        }
        for name, families in seen.items():
            others = set().union(*(f for other, f in seen.items() if other != name))
            assert families - others, f"{name} contributed no parameter the others do not"


def _sweep(drawings) -> tuple[list[tuple[str, str]], int, int, list]:
    """Returns (escapes, unclaimed examined, claimed annotations rendering a readable number,
    claims that did not confirm, claims verified)."""
    escapes: list[tuple[str, str]] = []
    unconfirmed: list = []
    unclaimed = readable = claims = 0
    for label, drawing in drawings:
        checked = _claims(drawing)
        claims += len(checked)
        unconfirmed += [
            (label, c.annotation, c.parameter_id, c.state)
            for c in checked
            if c.state != "confirmed"
        ]
        for name in sorted(drawing.registry.names()):
            annotation = drawing.registry.named(name)
            if drawing.registry.measurement_of(name):
                readable += bool(rendered_numbers(annotation))
                continue
            unclaimed += 1
            if name.startswith(_NON_MEASURING_ANNOTATIONS):
                continue
            if rendered_numbers(annotation):
                escapes.append((label, name))
    return escapes, unclaimed, readable, unconfirmed, claims


_UNCONFIRMED_MESSAGE = (
    "claim a compiled dimension whose value the annotation does not render. A wrong id is "
    "worse than a missing one — it is a fidelity lint code and it lowers the quality score. "
    "Check the id threaded at the producer against the value the label is built from."
)

_ESCAPE_MESSAGE = (
    "render a measured value and claim nothing, so the verifier cannot see them. Thread "
    "ADR 0010 provenance on the producer, or register the annotation in "
    "_NON_MEASURING_ANNOTATIONS with the reason."
)


class TestNoMeasuredAnnotationEscapesUnclaimed:
    """The ratchet, and the real content of this slice.

    The facility can only check an annotation that claims a measurement. A new family whose
    annotations state a value without threading ADR 0010 provenance is not *wrongly* verified —
    it is not verified at all, and silently. This asserts the property that makes the reach
    limit honest: every annotation that renders a number claims something.

    The fast-tier corpus is the FAMILY PARTS plus every non-CTC STEP fixture. An earlier draft
    justified the family parts by saying those families appear in no fixture, which is false:
    measured over all 16, bosses are in `grm03`, `if_step_flat_across_cylinder` and both
    `nist_ctc_05`; turned steps in `grm03`; chamfers in `nist_ctc_01`, `03` and `04`.

    The true reason is coverage in the FAST tier specifically: chamfers appear only in CTC
    fixtures, which are slow-tier, and no fixture at all contains the bored tube this PR's
    threading fix turns on. Family parts cost ~1.4 s for all six and make the ratchet's reach
    independent of what the fixture corpus happens to hold (#1225 review, finding 3). The CTC
    sweep is the same property over a far richer corpus and is slow-tier,
    because a CTC build that takes ~40 s here has timed out at 300 s on ubuntu (#1202, and
    `tests/test_issue_1202_observed_drawing_consumer.py`).
    """

    #: Every non-CTC STEP fixture. The slowest, `issue_915_case_study_2`, builds in ~5.9 s
    #: here and ~42 s on ubuntu — the whole fast sweep is ~14 s locally, ~99 s there.
    FIXTURES = (
        "tests/fixtures/grm03_thumbwheel_drive_screw.step",
        "tests/fixtures/if_step_flat_across_cylinder.step",
        "tests/fixtures/issue_1058_wheel_rh.step",
        "tests/fixtures/issue_909_basic_part_design_017_body.step",
        "tests/fixtures/issue_915_case_study_2.step",
        "tests/fixtures/tuner_jig_blind_obround_pockets.step",
    )

    SLOW_FIXTURES = (
        "tests/fixtures/nist_ctc_02_asme1_ap203.stp",
        "tests/fixtures/nist_ctc_04_asme1_ap203.stp",
    )

    def _corpus(self, fixtures, *, parts: bool):
        built = [
            (name, _drawing(name, make)) for name, make in (_FAMILY_PARTS.items() if parts else ())
        ]
        built += [(path.rsplit("/", 1)[-1], _drawing(path, path)) for path in fixtures]
        return built

    def test_every_annotation_that_states_a_number_carries_a_claim(self):
        escapes, unclaimed, readable, unconfirmed, claims = self._sweep_fast()
        escapes = [e for e in escapes if e not in _UNPLANNED_VALUES]
        # Preconditions. The corpus must be big enough to mean something, and — the part the
        # author's own mutation run missed — `rendered_numbers` must still be able to SEE a
        # number on it. Neutering the reader makes this test pass vacuously otherwise, which it
        # did (#1225 review, finding 5).
        assert unclaimed > 50, f"only {unclaimed} unclaimed annotations examined; too few"
        assert readable > 50, (
            f"only {readable} claimed annotations render a readable number; the reader is "
            "blind and the assertion below is vacuous"
        )
        # The `unconfirmed` assertion needs its own floor for the same reason the escape half
        # does: `verify_measurement_claims` returning [] makes it pass on an empty set, and
        # measured, that mutation is killed only by SIBLING tests — the exact shape round 2
        # caught one level down (#1225 review, round 3, finding 4).
        assert claims > 100, f"only {claims} claims verified; `not unconfirmed` means little"
        assert not escapes, f"{escapes} {_ESCAPE_MESSAGE}"
        # Presence is not correctness, and the ratchet above only checks presence. Threading the
        # WRONG id — a bore\'s where the OD\'s belongs — passed the entire fast tier, because no
        # test checked the STATE of a claim on a part outside `TestASecondFamilyCostsNothing`
        # (#1225 review, finding 4). A claim the verifier reports as `value_absent` is worse than
        # no claim: it is a fidelity lint code and it lowers the quality score.
        assert not unconfirmed, f"{unconfirmed} {_UNCONFIRMED_MESSAGE}"

    def _sweep_fast(self):
        return _sweep(self._corpus(self.FIXTURES, parts=True))

    @pytest.mark.slow
    @pytest.mark.parametrize("fixture", SLOW_FIXTURES)
    def test_the_property_holds_on_the_ctc_corpus_too(self, fixture):
        # SLOW-tier: the fast sweep above is the PR gate; this is the same property over the
        # richest parts in the repo. CTC builds are slow-tier by policy (#153), and putting one
        # in the fast tier timed out ubuntu 3.10 at 300 s while taking ~53 s on macOS.
        #
        # ONE fixture per test, not both. The slow job runs `-m slow -n auto --dist load`, which
        # distributes individual tests, so a two-build test pays for both builds on one worker:
        # 47.8 s locally, and at the 5.2-7.2x ubuntu ratio measured on run 32230860825 that is
        # 251-343 s against the same 300 s timeout. Splitting also lets the two builds run
        # concurrently (#1225 review, finding 1).
        escapes, unclaimed, readable, unconfirmed, claims = _sweep(
            self._corpus((fixture,), parts=False)
        )
        escapes = [e for e in escapes if e not in _UNPLANNED_VALUES]
        unconfirmed = [u for u in unconfirmed if u[:3] not in _UNCONFIRMED_CLAIMS]
        assert unclaimed > 40, f"only {unclaimed} unclaimed annotations examined; too few"
        assert readable > 40, f"only {readable} claimed annotations render a readable number"
        assert claims > 100, f"only {claims} claims verified; `not unconfirmed` means little"
        assert not escapes, f"{escapes} {_ESCAPE_MESSAGE}"
        assert not unconfirmed, f"{unconfirmed} {_UNCONFIRMED_MESSAGE}"

    def test_the_unplanned_value_register_is_exactly_the_live_defect(self):
        # A defect ledger that outlives its defect is a hole in the ratchet: whatever #1230 fixes
        # must delete its entry here, and nothing else may be added without an issue. Asserts
        # both directions — every registered pair still escapes, and no unregistered pair does.
        escapes, _unclaimed, _readable, _unconfirmed, _claims_seen = self._sweep_fast()
        assert set(escapes) == _UNPLANNED_VALUES, (
            f"registered {_UNPLANNED_VALUES}, measured {set(escapes)}. An entry that no longer "
            "escapes is fixed — delete it (see #1230). A pair that escapes unregistered is a new "
            "defect — file it, do not add it here."
        )

    def test_the_registered_exemption_is_real_and_not_a_blanket(self):
        # An exemption nobody can see is a hole. This pins that the one entry exists, that it
        # really does render digits, and that those digits are a scale ratio rather than a
        # fact about the part.
        drawing = _drawing(
            "tests/fixtures/issue_915_case_study_2.step",
            "tests/fixtures/issue_915_case_study_2.step",
        )
        captions = [
            n for n in drawing.registry.names() if n.startswith(_NON_MEASURING_ANNOTATIONS)
        ]
        assert captions, "the exemption no longer matches anything; delete it"
        for name in captions:
            annotation = drawing.registry.named(name)
            assert not drawing.registry.measurement_of(name)
            assert rendered_numbers(annotation), f"{name} renders no number; it needs no exemption"
            assert "SCALE" in str(getattr(annotation, "label", "")).upper(), (
                f"{name} is exempted as a scale caption but does not read as one"
            )

    @pytest.mark.slow
    def test_the_furniture_really_is_silent(self):
        # The other side of the same claim: on nist_ctc_02 the 89 unclaimed annotations are
        # centre marks, bolt circles, notes and the title block. The allow-list is exactly the
        # four types measured there — a wider set asserts nothing about types that never occur
        # (#1225 review, finding 7).
        #
        # "Silent" here means silent TO THE READER, and the title block is the honest edge of
        # that: it draws a scale ratio, a drawing number and a date, none of which live in
        # `label` or `table_rows`, so `rendered_numbers` cannot see them. It is in the allow-list
        # because the reader is blind to it, not because the sheet shows no digits — which is
        # exactly the limit `evidence.py` now states rather than the stronger one it claimed.
        #
        # Built with a title on purpose: without `title=`/`number=` no TitleBlock is placed at
        # all (88 silent annotations, not 89), and the test would then characterise a sheet the
        # ratchet never sweeps.
        drawing = _drawing(
            "tests/fixtures/nist_ctc_02_asme1_ap203.stp",
            "tests/fixtures/nist_ctc_02_asme1_ap203.stp",
        )
        silent = [
            type(drawing.registry.named(n)).__name__
            for n in drawing.registry.names()
            if not drawing.registry.measurement_of(n)
            and not rendered_numbers(drawing.registry.named(n))
        ]
        assert len(silent) > 50, f"too little furniture to characterise: {len(silent)}"
        assert set(silent) == {"CenterMark", "CenterlineCircle", "Note", "TitleBlock"}, set(silent)
