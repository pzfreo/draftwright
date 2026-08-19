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
one merged the slot's ordinate into a neighbour's. The synthetic part below shows both, builds in
about a second, and does not need a slow-tier CTC fixture.

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


def _drawing():
    return build_drawing(_z_long_slot_and_located_holes(), title="T", number="N-1")


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

    def test_an_x_long_slot_behaves_the_same_way(self):
        # The incoherence the renderer half removes: before, a slot got a spurious plan-location
        # dim or not depending on which way it ran. Both must now be silent in the ladder.
        part = Box(90, 50, 12, align=_C) - Pos(0, 0, 4) * Box(120, 10, 6, align=_C)
        drawing = build_drawing(part, title="T", number="N-1")
        assert not [
            n
            for n in _ladder(drawing)
            if any(str(i.parameter) == _SLOT_POSITION for i in drawing.registry.measurement_of(n))
        ]


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
