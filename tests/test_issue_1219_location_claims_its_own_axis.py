"""#1219 — a location dimension must claim a measurement that runs where it draws.

`render_locations` draws the plan X/Y ladders: a datum→feature offset in the page plane. It
selects what to draw from `plan.locations` with ``if loc.axis != "z": continue``, reading `axis`
as "this feature opens along Z, so its position is an in-plane pair".

`_compile_slot_positions` did not use `axis` that way. It passed the slot's LONG axis and no
discriminator at all, which is indistinguishable from the plan-location shape — so a slot that
happens to run along Z fell into BOTH ladders, and each minted a dimension claiming
`location_slot.length`, a measurement running along Z:

    without the fix:  m_locx2 location_slot.length value_absent
                      m_locy1 location_slot.length value_absent   + `claimed_value_absent` lint
    with the fix:     (none)

Found by the claim verifier (#1218) on `nist_ctc_02`, where only the Y ladder showed it — the X
one merged the slot's ordinate into a neighbour's. The synthetic part below shows both and does
not need a slow-tier CTC fixture.

**The dimension it was drawing was not the slot's position at all.** `model/detect.py::_convert_slot`
builds a slot's frame as the part's BOUNDING-BOX CENTRE, overwriting only the long-axis
component, so the compiled entry's other two coordinates are the part centreline. Measured on
`main`, moving the slot along its width axis leaves the drawn labels unchanged::

    slot x=35  w_center=35.0  ladder m_locx2='70' m_locy1='30'
    slot x=50  w_center=50.0  ladder m_locx2='70' m_locy1='30'
    slot x=20  w_center=20.0  ladder m_locx2='70' m_locy1='30'

70 and 30 are half of 140 and 60 — the part's own centre, from the datum. On `nist_ctc_02` the
same holds: `430` is half the part's Y extent, not the slot at y=390. #1219's own parenthetical
("correct — the slot sits at y=390") is therefore wrong, and so was the first draft of this
docstring. The ladder was not mislabelling the slot's position; it was drawing the part's
centreline and attributing it to the slot.

Two halves to the fix, and a guard for each: the compiler states the direction its measurement
runs (`discriminator`), and `render_locations` leaves `location_slot` entries to `render_slots`,
which draws that measurement and is the only annotation entitled to claim it. Before, whether a
slot got a spurious plan-location dim depended on which way it happened to run — X- and Y-long
slots were filtered out by `loc.axis != "z"` and Z-long ones were not.
"""

from __future__ import annotations

import pytest
from build123d import Align, Box, Cylinder, Pos

from draftwright.builder import build_drawing
from draftwright.linting.evidence import verify_measurement_claims
from draftwright.model.compiled import compile_dimensions
from draftwright.model.ir import SlotFeature

_C = (Align.CENTER, Align.CENTER, Align.CENTER)
_CTC02 = "tests/fixtures/nist_ctc_02_asme1_ap203.stp"
_SLOT_POSITION = f"{SlotFeature.LOCATION_STEM}.length"


def _z_long_slot_and_located_holes():
    """A Z-long slot AND two located holes — both are needed to reach the defect.

    The holes are not decoration. `render_locations` takes its datum from ``approved[0]``, so a
    slot on its own is measured from itself, the offset is zero, and the ladder draws nothing at
    all. Only once another location entry establishes the bounding-box corner datum does the
    slot's ordinate become a drawable offset — which is why this reproduces and a bare slotted
    block does not.
    """
    return (
        Box(140, 60, 80, align=_C)
        - Pos(35, 0, 10) * Box(14, 90, 80, align=_C)
        - Pos(-50, 18, 0) * Cylinder(5, 120, align=_C)
        - Pos(-20, -18, 0) * Cylinder(5, 120, align=_C)
    )


def _x_long_slots_and_located_holes():
    """Two X-long slots and two located holes — the same shape with the slot running the OTHER
    way. Before the fix these were filtered out by `loc.axis != "z"` and got no plan-location
    dim; the point of the renderer half is that every slot is now handled the one way."""
    return (
        Box(120, 70, 14, align=_C)
        - Pos(-25, 0, 0) * Box(30, 10, 40, align=_C)
        - Pos(25, 0, 0) * Box(30, 10, 40, align=_C)
        - Pos(-45, 25, 0) * Cylinder(4, 40, align=_C)
        - Pos(40, -25, 0) * Cylinder(4, 40, align=_C)
    )


#: One build per part, shared. `test_issue_1217` established this for the same reason
#: (#1225 review, findings 1 and 10); nothing here mutates a drawing.
_BUILT: dict[str, object] = {}


def _drawing(which: str = "z"):
    if which not in _BUILT:
        make = {"z": _z_long_slot_and_located_holes, "x": _x_long_slots_and_located_holes}[which]
        _BUILT[which] = build_drawing(make(), title="T", number="N-1")
    return _BUILT[which]


def _claims(drawing):
    return verify_measurement_claims(drawing.registry, compile_dimensions(drawing.model()))


def _ladder(drawing):
    return sorted(n for n in drawing.registry.names() if n.startswith(("m_locx", "m_locy")))


class TestThePartReallyContainsTheDefectsShape:
    """Preconditions. Each assertion below is worthless on a part that cannot fail."""

    def test_the_slot_is_recognised_and_runs_along_z(self):
        slots = [f for f in _drawing().model().features if isinstance(f, SlotFeature)]
        assert slots, "no slot recognised; nothing here exercises the defect"
        assert any(s.long_axis == "z" for s in slots), [s.long_axis for s in slots]

    def test_the_plan_ladder_actually_draws(self):
        # An X- or Y-long slot never reached the ladder, so a part whose ladder is empty would
        # pass every assertion below without touching the code path at all.
        drawing = _drawing()
        names = _ladder(drawing)
        assert len(names) >= 4, names
        assert [n for n in names if drawing.registry.measurement_of(n)], (
            "no plan-location annotation claims anything; the ladder is not being exercised"
        )

    def test_the_slot_position_is_compiled(self):
        plan = compile_dimensions(_drawing().model())
        assert [loc for loc in plan.locations if loc.role == SlotFeature.LOCATION_STEM], (
            "no slot position compiled; there is no measurement to mis-bind"
        )


class TestTheSlotPositionIsClaimedOnlyByWhatDrawsIt:
    def test_no_plan_ladder_annotation_claims_the_slot_position(self):
        # The defect, stated exactly: without the fix, m_locx2 and m_locy1 both claim it.
        drawing = _drawing()
        offenders = {
            name: [str(i.parameter) for i in drawing.registry.measurement_of(name)]
            for name in _ladder(drawing)
            if any(
                str(i.parameter) == _SLOT_POSITION for i in drawing.registry.measurement_of(name)
            )
        }
        assert not offenders, (
            f"{offenders} are plan-plane offsets claiming the slot's position, which runs along "
            "the slot's long axis. Give the compiled entry a `discriminator` so it cannot be "
            "read as a plan location (#1219)."
        )

    def test_nothing_on_the_sheet_claims_a_value_it_does_not_render(self):
        outcomes = _claims(_drawing())
        assert len(outcomes) > 5, f"only {len(outcomes)} claims; too few to mean anything"
        assert not [o for o in outcomes if o.state != "confirmed"], [
            (o.annotation, o.parameter_id, o.state) for o in outcomes if o.state != "confirmed"
        ]

    def test_the_compiled_entry_states_the_direction_it_runs(self):
        # The compiler half of the fix. Without a discriminator the entry has the same shape as
        # a plan-location pair, and both ladders take it.
        plan = compile_dimensions(_drawing().model())
        entries = [loc for loc in plan.locations if loc.role == SlotFeature.LOCATION_STEM]
        assert entries, "no slot position compiled; the loop below is vacuous"
        for loc in entries:
            assert loc.discriminator is not None, (
                f"a {loc.role!r} entry with no discriminator reads as a plan location"
            )

    def test_no_plan_ladder_annotation_is_drawn_without_a_claim(self):
        # The RENDERER half, which the two assertions above do not reach: once the compiled
        # entry carries a discriminator the ladder mints no claim from it, but it still drew a
        # dimension — an unbacked plan offset for the slot, with nothing behind it. Reverting
        # only the renderer skip leaves every claim correct and every one of these annotations
        # silent, which is how that mutation survived until this test existed.
        drawing = _drawing()
        names = _ladder(drawing)
        assert names, "no plan-location annotations at all; the assertion below is vacuous"
        unclaimed = [n for n in names if not drawing.registry.measurement_of(n)]
        assert not unclaimed, (
            f"{unclaimed} draw a plan offset and claim nothing. `render_slots` owns the slot's "
            "position; the plan ladder must not mint a second, uncompiled dimension for it."
        )

    def test_x_long_slots_really_exist_on_the_other_part(self):
        # Precondition. The first version of the test below used a part that recognised NO slot
        # and whose ladder was empty, so `assert not []` passed against completely unfixed code
        # — the exact failure this file's other precondition class exists to prevent, in this
        # file (#1231 review, finding 2).
        drawing = _drawing("x")
        slots = [f for f in drawing.model().features if isinstance(f, SlotFeature)]
        assert slots, "no slot recognised; the assertion below cannot fail"
        assert all(s.long_axis == "x" for s in slots), [s.long_axis for s in slots]
        assert _ladder(drawing), "no plan-location annotations; the ladder is not exercised"

    def test_an_x_long_slot_behaves_the_same_way(self):
        # The incoherence the renderer half removes: before, a slot got a spurious plan-location
        # dim or not depending on which way it ran. Both must now be silent in the ladder, and
        # every plan-location annotation must still carry a claim.
        drawing = _drawing("x")
        assert not [
            n
            for n in _ladder(drawing)
            if any(str(i.parameter) == _SLOT_POSITION for i in drawing.registry.measurement_of(n))
        ]
        assert not [n for n in _ladder(drawing) if not drawing.registry.measurement_of(n)]


class TestADroppedSlotPositionSaysWhichMeasurementItLost:
    """`render_slots` has TWO drop paths, and only one of them named its measurement.

    The corridor path (`_far_or_drop`) passes `approved.id` and is already covered by
    `tests/test_slot_completeness.py::test_a_real_placement_failure_retains_the_dropped_measurement`,
    which also pins the ledger consequence: emptying `measurement_ids` degrades the outcome from
    `dropped` to `missing`. The NON-corridor path — the `else` after `_place` fails — passed
    nothing, and nothing covered it.

    I reverted this fix once, on the strength of "measured `main`, the record already carries
    `location_slot.length`". That measurement was of the corridor path: my synthetic part never
    reached the other one. Instrumenting which line fires shows 12 nameless position drops across
    `nist_ctc_03` and `nist_ctc_04`, every one from the uncovered branch (#1231 review, finding 1).

    So these use `nist_ctc_04`, which is where the uncovered path actually runs. Slow-tier: CTC
    builds are slow-tier by policy (#153).
    """

    FIXTURE = "tests/fixtures/nist_ctc_04_asme1_ap203.stp"

    @pytest.mark.slow
    def test_the_uncovered_drop_path_really_runs(self):
        # Precondition, and specifically that position drops HAPPEN here — the earlier version
        # of this test asserted only "some drop occurred", which the corridor path satisfies.
        issues = build_drawing(self.FIXTURE, title="T", number="N-1").lint()
        drops = [i for i in issues if i.code == "slot_dim_dropped" and "position" in i.message]
        assert len(drops) >= 5, f"only {len(drops)} position drops; too few to mean anything"

    @pytest.mark.slow
    def test_every_dropped_position_names_the_measurement_it_lost(self):
        issues = build_drawing(self.FIXTURE, title="T", number="N-1").lint()
        nameless = [
            i.message
            for i in issues
            if i.code == "slot_dim_dropped" and "position" in i.message and not i.measurement_ids
        ]
        assert not nameless, (
            f"{nameless} record no measurement, so the coverage ledger cannot tell a dropped "
            "slot position from one that was never planned — it reports `missing` where it "
            "should report `dropped`."
        )


class TestTheReportedFixture:
    """`nist_ctc_02` as filed. Slow-tier: CTC builds are slow-tier by policy (#153)."""

    @pytest.mark.slow
    def test_no_claim_on_the_reported_fixture_is_unconfirmed(self):
        drawing = build_drawing(_CTC02, title="T", number="N-1")
        outcomes = verify_measurement_claims(drawing.registry, compile_dimensions(drawing.model()))
        assert len(outcomes) > 100, f"only {len(outcomes)} claims; too few to mean anything"
        assert not [o for o in outcomes if o.state != "confirmed"], [
            (o.annotation, o.parameter_id, o.state) for o in outcomes if o.state != "confirmed"
        ]

    @pytest.mark.slow
    def test_the_slot_position_is_claimed_by_the_annotation_that_draws_it(self):
        drawing = build_drawing(_CTC02, title="T", number="N-1")
        claims = [c for c in _claims(drawing) if c.parameter_id == _SLOT_POSITION]
        assert claims, "the fixture compiles no slot position; the loop below is vacuous"
        for claim in claims:
            assert claim.annotation.startswith("m_slot"), claim
            assert claim.state == "confirmed", claim
