"""#1217 — a claimed representation must be borne out by the drawing.

Coverage answers "did this requirement reach the sheet?" by reading provenance riders the
annotations carry about *themselves*. That is a claim, not an observation. Before this, nothing
checked that an annotation claiming to carry hole 3's diameter rendered that diameter, or
rendered anything at all — and `covers_hole_representations_by_feature` was read in two places
while no caller populated it, which inverted the benchmark's `drawing_consumer` metric (16/16
`unsupported` against a true 16/16 `supported`) and went unnoticed for months.

@pzfreo's decision on #1206: the ledger is a **pointer to the claimed representation, not final
proof**. This is the verifier that follows the pointer.
"""

from __future__ import annotations

from types import SimpleNamespace

from build123d import Align, Box, Cylinder, Pos

from draftwright.builder import build_drawing
from draftwright.linting.evidence import (
    _expected_numbers,
    lint_claimed_representations,
    rendered_numbers,
    verify_measurement_claims,
)
from draftwright.model.compiled import compile_dimensions

_C = (Align.CENTER, Align.CENTER, Align.CENTER)


def _two_hole_plate():
    return (
        Box(80, 60, 20, align=_C)
        - Pos(20, 15, 0) * Cylinder(4, 40, align=_C)
        - Pos(-20, -15, 0) * Cylinder(3, 40, align=_C)
    )


def _verified(drawing):
    return verify_measurement_claims(drawing.registry, compile_dimensions(drawing.model()))


class TestTheDrawingBearsOutItsOwnClaims:
    def test_every_claim_on_an_ordinary_drawing_is_confirmed(self):
        # The baseline the rest depends on. If a correct drawing cannot pass its own
        # verification, every finding below is noise.
        outcomes = _verified(build_drawing(_two_hole_plate(), title="T", number="N-1"))
        assert outcomes, "no claims were checked, so nothing here proves anything"
        assert {o.state for o in outcomes} == {"confirmed"}, [
            (o.annotation, o.parameter_id, o.state, o.expected, o.rendered)
            for o in outcomes
            if o.state != "confirmed"
        ]

    def test_a_claim_the_annotation_does_not_render_is_reported(self):
        # THE case that distinguishes a pointer from proof. Without it the verifier only
        # checks that the annotation exists, which the ledger already implied.
        drawing = build_drawing(_two_hole_plate(), title="T", number="N-1")
        name = next(n for n in drawing.registry.names() if n.startswith("hc_"))
        annotation = drawing.registry.named(name)
        before = _verified(drawing)
        assert all(o.state == "confirmed" for o in before if o.annotation == name)

        annotation.label = "⌀999 THRU"
        after = [o for o in _verified(drawing) if o.annotation == name]
        assert [o.state for o in after] == ["value_absent"], after
        codes = [
            i.code
            for i in lint_claimed_representations(
                drawing.registry, compile_dimensions(drawing.model())
            )
        ]
        assert codes == ["claimed_value_absent"]

    def test_a_near_miss_is_not_accepted_by_a_substring(self):
        # `⌀8` reads as a substring of `⌀18`. Comparing text rather than numbers would call
        # this confirmed, which is the trap this check exists to avoid rather than introduce.
        drawing = build_drawing(_two_hole_plate(), title="T", number="N-1")
        name = next(
            n
            for n in drawing.registry.names()
            if n.startswith("hc_") and "8" in str(getattr(drawing.registry.named(n), "label", ""))
        )
        drawing.registry.named(name).label = "⌀18 THRU"
        states = [o.state for o in _verified(drawing) if o.annotation == name]
        assert states == ["value_absent"], "a digit-substring match was accepted as proof"

    def test_a_claim_on_an_uncompiled_measurement_is_reported(self):
        # A renderer emitting content the compiler never approved is an ADR 0016 Amdt 1
        # violation. The verifier sees it because it resolves claims against the plan.
        drawing = build_drawing(_two_hole_plate(), title="T", number="N-1")
        plan = compile_dimensions(drawing.model())
        empty = SimpleNamespace(groups=(), ladders=(), locations=())
        states = {o.state for o in verify_measurement_claims(drawing.registry, empty)}
        assert states == {"unresolved"}
        assert {o.state for o in verify_measurement_claims(drawing.registry, plan)} == {
            "confirmed"
        }


class TestWhatCountsAsTheExpectedValue:
    def test_the_formatted_value_is_expected_not_the_raw_float(self):
        # The compiler formats for display: a 13.649 mm extent is approved as "13.6" and drawn
        # as 13.6. Comparing `value` reported ten correct dimensions as wrong on the first
        # corpus run — including `expected=('13.6',) rendered=13.6`.
        approved = SimpleNamespace(value=13.649, value_text="13.6", span=None)
        assert _expected_numbers(approved) == frozenset({13.6})

    def test_a_location_is_verified_from_its_span(self):
        # A location carries `value=0.0` and `value_text=""`; its magnitude lives in
        # `span = (datum, located point)` and the renderer draws one axis component. Declining
        # to check these was true of the implementation and false of the drawing — all four
        # corpus cases are ordinary, correct location dimensions.
        approved = SimpleNamespace(
            value=0.0, value_text="", span=((-26.5, -13.0, -13.0), (0.0, 16.35, 16.5))
        )
        # |dx| = 26.5, |dy| = 29.35 -> drawn as 29.4, |dz| = 29.5
        assert _expected_numbers(approved) == frozenset({26.5, 29.4, 29.5})

    def test_the_three_dimensional_distance_is_not_accepted(self):
        # No renderer draws it, so admitting it would widen what counts as a match for nothing.
        approved = SimpleNamespace(value=0.0, value_text="", span=((0, 0, 0), (3, 4, 0)))
        assert 5.0 not in _expected_numbers(approved)

    def test_a_compound_callout_renders_every_number_it_claims(self):
        # `⌀8 THRU ⌴ ⌀14 ↧ 7` claims three measurements. Reading only the leading number —
        # which is what `structural._label_value` answers, for a different question — would
        # confirm the bore and report the counterbore as absent.
        part = (
            Box(80, 60, 20, align=_C)
            - Cylinder(4, 40, align=_C)
            - Pos(0, 0, 7) * Cylinder(7, 8, align=_C)
        )
        drawing = build_drawing(part, title="T", number="N-1")
        name = next(n for n in drawing.registry.names() if n.startswith("hc_"))
        claimed = {m.parameter for m in drawing.registry.measurement_of(name)}
        assert len(claimed) >= 3, f"the fixture no longer makes a compound callout: {claimed}"
        assert {o.state for o in _verified(drawing) if o.annotation == name} == {"confirmed"}


class TestATableIsReadableToo:
    def test_a_hole_table_carries_the_rows_it_draws(self):
        # A table renders as compound geometry with no label. Without `table_rows` its claims
        # are unverifiable — and those are exactly the claims that matter, because the engine
        # WITHDRAWS the individual callouts when it escalates to a table.
        from test_issue_1202_observed_drawing_consumer import _dense_scattered_plate

        drawing = build_drawing(_dense_scattered_plate(), page="A3")
        names = {n for n, _o in drawing.iter_annotations()}
        table = next((n for n in names if n.startswith("hole_table")), None)
        assert table is not None, "fixture no longer escalates to a table; this tests nothing"
        assert not any(n.startswith("hc_") for n in names), (
            "individual callouts survived, so the table route is not under test"
        )
        assert rendered_numbers(drawing.registry.named(table)), (
            "the table renders no readable numbers, so its claims cannot be verified"
        )
        assert {o.state for o in _verified(drawing) if o.annotation == table} == {"confirmed"}


class TestTheDeadFieldIsGone:
    def test_no_caller_and_no_reader_remain(self):
        # `covers_hole_representations_by_feature` was built from a `representation_features`
        # argument no caller ever passed, so the tuple was always empty and the branch reading
        # it was unreachable. Removed rather than populated: the live route is
        # `covers_hole_representations_by_requirement`.
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "draftwright"
        offenders = [
            f"{path.relative_to(root)}:{n}"
            for path in root.rglob("*.py")
            for n, line in enumerate(path.read_text().splitlines(), 1)
            if "covers_hole_representations_by_feature" in line
            and "#1217" not in line
            and not line.lstrip().startswith(("#", "*", "The failure mode"))
        ]
        assert not offenders, f"the dead field is back: {offenders}"


class TestLintSurfacesIt:
    def test_an_ordinary_drawing_reports_nothing(self):
        drawing = build_drawing(_two_hole_plate(), title="T", number="N-1")
        assert not [i for i in drawing.lint() if i.code.startswith("claimed_")], (
            "a correct drawing reported a claim defect"
        )

    def test_the_check_runs_inside_lint_not_only_as_a_helper(self):
        # Guards the wiring, not the function: deleting the `_lint` call site leaves every
        # test above passing.
        drawing = build_drawing(_two_hole_plate(), title="T", number="N-1")
        name = next(n for n in drawing.registry.names() if n.startswith("hc_"))
        drawing.registry.named(name).label = "⌀999 THRU"
        assert [i.code for i in drawing.lint() if i.code.startswith("claimed_")] == [
            "claimed_value_absent"
        ]
