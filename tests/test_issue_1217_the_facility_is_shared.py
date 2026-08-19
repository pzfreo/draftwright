"""#1217 PR 3 — the verification facility is family-agnostic, and stays that way.

"Shared" is a claim, and the only thing that proves it is a second consumer. A facility
exercised by holes alone is a hole verifier with general-sounding names.

Measured: it costs **nothing**. Slots, pockets, bosses, chamfers and turned steps all produce
verifiable claims through `registry.measurement_of` — the one ADR 0010 seam — with zero changes
to `linting/evidence.py`. The epic's PR-3 gate was "it costs a producer and zero changes to the
facility"; the producers already existed, because provenance is threaded once and not per family.

So the useful content of this slice is not the second family. It is the RATCHET: a new family
whose annotations state a measured value without threading provenance would be invisible to the
verifier, and nothing would say so. `TestNoMeasuredAnnotationEscapesUnclaimed` is what says so.
"""

from __future__ import annotations

from build123d import Align, Box, Cylinder, Pos

from draftwright.builder import build_drawing
from draftwright.linting.evidence import rendered_numbers, verify_measurement_claims
from draftwright.model.compiled import compile_dimensions

_C = (Align.CENTER, Align.CENTER, Align.CENTER)

#: Annotations that legitimately render digits while carrying no measurement claim. One entry,
#: measured across four rich fixtures: the detail-view caption, whose numbers are a SCALE RATIO
#: ("DETAIL A — SCALE 2.5:1") and not a fact about the part. Registered by annotation-name stem
#: so a second such case has to be argued rather than absorbed.
_NON_MEASURING_ANNOTATIONS = ("detail_caption",)


def _claims(drawing):
    return verify_measurement_claims(drawing.registry, compile_dimensions(drawing.model()))


def _families(drawing) -> set[str]:
    return {claim.parameter_id.split(".")[0] for claim in _claims(drawing)}


class TestASecondFamilyCostsNothing:
    """Each of these was verified with zero edits to the facility. If one ever needs an edit,
    the facility was not shared and this is the class that says so."""

    def test_slots_are_verified(self):
        part = (
            Box(90, 50, 12, align=_C)
            - Pos(-20, 0, 0) * Box(24, 8, 30, align=_C)
            - Pos(20, 0, 0) * Box(24, 8, 30, align=_C)
        )
        drawing = build_drawing(part, title="T", number="N-1")
        claims = [c for c in _claims(drawing) if "slot" in c.parameter_id]
        assert {c.parameter_id for c in claims} >= {
            "slot_length.length",
            "slot_width.length",
        }, {c.parameter_id for c in claims}
        assert {c.state for c in claims} == {"confirmed"}, [
            (c.annotation, c.parameter_id, c.state) for c in claims if c.state != "confirmed"
        ]

    def test_pockets_are_verified(self):
        part = Box(90, 60, 20, align=_C) - Pos(10, 5, 6) * Box(40, 25, 10, align=_C)
        drawing = build_drawing(part, title="T", number="N-1")
        assert {"pocket_depth", "pocket_length", "pocket_width"} <= _families(drawing)
        assert {c.state for c in _claims(drawing)} == {"confirmed"}

    def test_bosses_and_bores_are_verified(self):
        part = (
            Box(60, 60, 10, align=_C)
            + Pos(0, 0, 7) * Cylinder(8, 4, align=_C)
            - Cylinder(3, 40, align=_C)
        )
        drawing = build_drawing(part, title="T", number="N-1")
        assert {"bore", "boss", "boss_height"} <= _families(drawing)
        assert {c.state for c in _claims(drawing)} == {"confirmed"}

    def test_a_turned_profile_is_verified(self):
        part = Cylinder(10, 30, align=_C) + Pos(0, 0, 20) * Cylinder(6, 10, align=_C)
        drawing = build_drawing(part, title="T", number="N-1")
        assert "step" in _families(drawing)
        assert {c.state for c in _claims(drawing)} == {"confirmed"}

    def test_the_families_are_genuinely_different_parameters(self):
        # Guards the tests above from passing on a shared parameter. If every family resolved
        # to the same handful of ids, "five families" would be one family five times.
        parts = {
            "slot": Box(90, 50, 12, align=_C) - Pos(0, 0, 0) * Box(24, 8, 30, align=_C),
            "pocket": Box(90, 60, 20, align=_C) - Pos(10, 5, 6) * Box(40, 25, 10, align=_C),
            "boss": Box(60, 60, 10, align=_C) + Pos(0, 0, 7) * Cylinder(8, 4, align=_C),
        }
        seen = {
            name: _families(build_drawing(p, title="T", number="N-1")) for name, p in parts.items()
        }
        for name, families in seen.items():
            others = set().union(*(f for other, f in seen.items() if other != name))
            assert families - others, f"{name} contributed no parameter the others do not"


class TestNoMeasuredAnnotationEscapesUnclaimed:
    """The ratchet, and the real content of this slice.

    The facility can only check an annotation that claims a measurement. A new family whose
    annotations state a value without threading ADR 0010 provenance is not *wrongly* verified —
    it is not verified at all, and silently. This asserts the property that makes the reach
    limit honest: every annotation that renders a number claims something.
    """

    FIXTURES = (
        "tests/fixtures/nist_ctc_02_asme1_ap203.stp",
        "tests/fixtures/nist_ctc_04_asme1_ap203.stp",
        "tests/fixtures/issue_915_case_study_2.step",
        "tests/fixtures/tuner_jig_blind_obround_pockets.step",
    )

    def test_every_annotation_that_states_a_number_carries_a_claim(self):
        escapes = []
        checked = 0
        for fixture in self.FIXTURES:
            drawing = build_drawing(fixture, title="T", number="N-1")
            for name in sorted(drawing.registry.names()):
                if drawing.registry.measurement_of(name):
                    continue
                checked += 1
                if name.startswith(_NON_MEASURING_ANNOTATIONS):
                    continue
                if rendered_numbers(drawing.registry.named(name)):
                    escapes.append((fixture.rsplit("/", 1)[-1], name))
        assert checked > 100, (
            f"only {checked} unclaimed annotations examined; too few to mean much"
        )
        assert not escapes, (
            f"{escapes} render a measured value and claim nothing, so the verifier cannot "
            "see them. Thread ADR 0010 provenance on the producer, or register the "
            "annotation in _NON_MEASURING_ANNOTATIONS with the reason."
        )

    def test_the_registered_exemption_is_real_and_not_a_blanket(self):
        # An exemption nobody can see is a hole. This pins that the one entry exists, that it
        # really does render digits, and that those digits are a scale ratio rather than a
        # fact about the part.
        drawing = build_drawing("tests/fixtures/issue_915_case_study_2.step")
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

    def test_the_furniture_really_is_silent(self):
        # The other side of the same claim: the ~175 unclaimed annotations are centre marks,
        # bolt circles, notes, title blocks and section furniture, and they assert no number.
        # If a centre mark ever starts carrying a value, the test above must catch it.
        drawing = build_drawing("tests/fixtures/nist_ctc_02_asme1_ap203.stp")
        silent = [
            type(drawing.registry.named(n)).__name__
            for n in drawing.registry.names()
            if not drawing.registry.measurement_of(n)
            and not rendered_numbers(drawing.registry.named(n))
        ]
        assert len(silent) > 50, f"too little furniture to characterise: {len(silent)}"
        assert set(silent) <= {
            "CenterMark",
            "CenterlineCircle",
            "Note",
            "TitleBlock",
            "Compound",
            "Centerline",
            "ArrowHead",
        }, set(silent)
