"""#1217 — a claimed representation must be borne out by the drawing.

Coverage answers "did this requirement reach the sheet?" by reading provenance riders the
annotations carry about *themselves*. That is a claim, not an observation. Before this, nothing
checked that an annotation claiming to carry hole 3's diameter rendered that diameter, or
rendered anything at all. The benchmark's `drawing_consumer` metric was inverted — 16/16
`unsupported` against a true 16/16 `supported` — by reading only `hc_` names; the *fix* for
that then read `covers_hole_representations_by_feature`, a rider no caller populates, and
passed only because a stub in the tests supplied it.

@pzfreo's decision on #1206: the ledger is a **pointer to the claimed representation, not final
proof**. This is the verifier that follows the pointer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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
        # Code, not prose. Matching any mention flagged this module's own docstring the
        # moment it was reworded — a guard that fires on its own explanation is a guard
        # people delete. An attribute is used by being assigned to or read off something,
        # so both forms need a preceding dot or a `getattr` quote.
        used = re.compile(
            r"(\.covers_hole_representations_by_feature)"
            r"|(getattr\([^)]*[\"']covers_hole_representations_by_feature)"
        )
        offenders = [
            f"{path.relative_to(root)}:{n}"
            for path in root.rglob("*.py")
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if used.search(line)
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


@dataclass(frozen=True)
class _Claim:
    """A hashable stand-in for `DimensionId` — the verifier only uses it as a dict key."""

    parameter: str


class _Registry:
    """The two reads the verifier makes, over a hand-built annotation."""

    def __init__(self, annotation, claims):
        self._annotation = annotation
        self._claims = claims

    def names(self):
        return {"probe"}

    def named(self, _name):
        return self._annotation

    def measurement_of(self, _name):
        return self._claims


def _plan(*approved):
    return SimpleNamespace(groups=(SimpleNamespace(dims=approved),), ladders=(), locations=())


class TestEveryOutcomeReachesLint:
    """Three of the four codes were emitted by no test: mutating their branch to `confirmed`,
    and deleting the `unresolved` arm of the lint mapping outright, left the suite green
    (#1218 review). A code nothing exercises is a code nothing has checked the wording,
    severity or existence of."""

    def test_a_claim_the_compiler_never_approved_reaches_lint(self):
        claim = _Claim("bore.diameter")
        registry = _Registry(SimpleNamespace(label="⌀8"), (claim,))
        issues = lint_claimed_representations(registry, _plan())
        assert [i.code for i in issues] == ["claimed_measurement_not_compiled"]
        assert issues[0].severity == "warning"

    def test_an_unreadable_annotation_reaches_lint(self):
        claim = _Claim("bore.diameter")
        approved = SimpleNamespace(id=claim, value_text="8", value=8.0, span=None, axis=None)
        registry = _Registry(SimpleNamespace(), (claim,))
        issues = lint_claimed_representations(registry, _plan(approved))
        assert [i.code for i in issues] == ["claimed_representation_unreadable"]
        assert issues[0].severity == "info"

    def test_an_approval_with_no_displayable_value_reaches_lint(self):
        claim = _Claim("location.location")
        approved = SimpleNamespace(id=claim, value_text="", value=0.0, span=None, axis=None)
        registry = _Registry(SimpleNamespace(label="20"), (claim,))
        issues = lint_claimed_representations(registry, _plan(approved))
        assert [i.code for i in issues] == ["claimed_representation_no_expected_value"]
        assert issues[0].severity == "info"

    def test_every_unconfirmed_state_carries_its_measurement(self):
        # `measurement` is what lets a consumer join a claim back to its feature — the
        # benchmark's `_borne_out` reads exactly that. Only the `unreadable` branch had a
        # test, so dropping it from `unresolved` and `no_expected_value` changed nothing any
        # test checked, even though the comment justifying the widening names `unresolved`
        # (#1223 review). Neither state occurs on a real fixture, hence the direct drive.
        claim = _Claim("bore.diameter")
        cases = {
            "unresolved": (SimpleNamespace(label="⌀8"), _plan()),
            "no_expected_value": (
                SimpleNamespace(label="20"),
                _plan(SimpleNamespace(id=claim, value_text="", value=0.0, span=None, axis=None)),
            ),
            "unreadable": (
                SimpleNamespace(),
                _plan(SimpleNamespace(id=claim, value_text="8", value=8.0, span=None, axis=None)),
            ),
        }
        for state, (annotation, plan) in cases.items():
            outcomes = verify_measurement_claims(_Registry(annotation, (claim,)), plan)
            assert [o.state for o in outcomes] == [state], (state, outcomes)
            assert outcomes[0].measurement is claim, (
                f"a {state} claim dropped its measurement, so nothing can join it to a feature"
            )

    def test_a_contradicted_claim_is_a_warning_not_an_info(self):
        # Severity is not cosmetic: `claimed_value_absent` is classified as fidelity, and the
        # fidelity score is computed from severity. Demoting it to info leaves the drawing
        # scoring 1.0 on the axis that just caught it.
        drawing = build_drawing(_two_hole_plate(), title="T", number="N-1")
        name = next(n for n in drawing.registry.names() if n.startswith("hc_"))
        drawing.registry.named(name).label = "⌀999 THRU"
        issue = next(i for i in drawing.lint() if i.code == "claimed_value_absent")
        assert issue.severity == "warning"
        assert drawing.lint_summary()["quality"]["fidelity"]["score"] < 1.0

    def test_ladder_rungs_are_part_of_the_expected_set(self):
        # `compiled_values` reads groups, ladders and locations. Dropping ladders left the
        # suite green because no test claimed a rung.
        from draftwright.linting.evidence import compiled_values

        rung = SimpleNamespace(id="rung", value_text="7", value=7.0, span=None, axis=None)
        plan = SimpleNamespace(groups=(), ladders=(SimpleNamespace(rungs=(rung,)),), locations=())
        assert compiled_values(plan) == {"rung": (rung,)}


class TestTheAcceptanceSetIsNarrowedWhereItCanBe:
    def test_a_repeat_count_cannot_confirm_a_diameter(self):
        # `4× ⌀9 THRU` renders {4, 9}. Reading the count as a measurement confirmed a claimed
        # bore diameter of 4 against a callout showing 9 (#1218 review). A count is not a
        # measurement, so this was never inside the stated presence limit.
        claim = _Claim("bore.diameter")
        approved = SimpleNamespace(id=claim, value_text="4", value=4.0, span=None, axis=None)
        registry = _Registry(SimpleNamespace(label="4× ⌀9 THRU"), (claim,))
        states = [o.state for o in verify_measurement_claims(registry, _plan(approved))]
        assert states == ["value_absent"]

    def test_a_dimension_pair_is_not_mistaken_for_a_repeat_count(self):
        # `44 × 93.4 × 10 DEEP` is width × length × depth. The first cut of the repeat strip
        # matched any leading `N ×` and deleted the 44, turning two correct pocket dimensions
        # on `issue_915_case_study_2` into mismatches — a safety fix becoming the defect it
        # was guarding against. The ⌀ lookahead is `structural._label_value`'s own
        # discriminator between a count and a size.
        claim = _Claim("pocket_width.length")
        approved = SimpleNamespace(id=claim, value_text="44", value=44.0, span=None, axis=None)
        registry = _Registry(SimpleNamespace(label="44 × 93.4 × 10 DEEP"), (claim,))
        states = [o.state for o in verify_measurement_claims(registry, _plan(approved))]
        assert states == ["confirmed"]

    def test_the_feature_axis_component_is_not_accepted(self):
        # A pocket normal to Z is located by its X and Y offsets; the Z component of the span
        # is drawn by nothing. Accepting all three confirmed a relabelled X offset.
        approved = SimpleNamespace(
            value=0.0, value_text="", span=((-60, -40, -10), (15, 10, 5.5)), axis="z"
        )
        assert _expected_numbers(approved) == frozenset({75.0, 50.0})

    def test_all_components_stay_acceptable_when_no_axis_is_declared(self):
        # Nothing then says which one the renderer took, and guessing would produce false
        # mismatches on correct drawings. Named as a residual limit rather than hidden.
        approved = SimpleNamespace(
            value=0.0, value_text="", span=((-60, -40, -10), (15, 10, 5.5)), axis=None
        )
        assert _expected_numbers(approved) == frozenset({75.0, 50.0, 15.5})


class TestTheOutputIsDeterministic:
    def test_issue_order_does_not_depend_on_set_iteration(self):
        # `registry.names()` is a set. Without `sorted` the emitted order varied run to run on
        # one drawing — five orderings in five processes — which is against ADR 0006 and makes
        # two lint runs undiffable, the defect #1196 fixed for lint TEXT.
        drawing = build_drawing(_two_hole_plate(), title="T", number="N-1")
        for name in [n for n in drawing.registry.names() if n.startswith(("hc_", "m_loc"))]:
            drawing.registry.named(name).label = "⌀999"
        plan = compile_dimensions(drawing.model())
        issues = lint_claimed_representations(drawing.registry, plan)
        assert len(issues) > 1, "the fixture produced too few issues to have an order"
        # Assert the order IS sorted, not that it repeats. Repeating within one process is
        # true of an unsorted set too — string hashing is stable for a given PYTHONHASHSEED —
        # so a same-process comparison passes on the broken code, which is what the first cut
        # of this test did (#1218 review round 2, found by mutation).
        names = [i.message.split()[0] for i in issues]
        assert names == sorted(names), names


class TestATableIsNotOneFlatNumberPool:
    def test_a_quantity_column_cannot_confirm_a_diameter(self):
        # `add_hole_table` emits TAG | ⌀ | DEPTH | QTY. A table drawing `ø99` for a ⌀4 hole was
        # confirmed by its own quantity cell — the same defect `_REPEAT_RE` fixes for
        # `4× ⌀9 THRU`, surviving in the table path because the strip was applied to the label
        # and not to row cells (#1218 review round 2).
        claim = _Claim("bore.diameter")
        approved = SimpleNamespace(id=claim, value_text="4", value=4.0, span=None, axis=None)
        rows = (("TAG", "⌀", "DEPTH", "QTY"), ("A", "ø99", "THRU", "4"))
        registry = _Registry(SimpleNamespace(table_rows=rows), (claim,))
        states = [o.state for o in verify_measurement_claims(registry, _plan(approved))]
        assert states == ["value_absent"], "a QTY cell confirmed a wrong diameter"

    def test_the_header_row_is_not_read_as_data(self):
        # Defensive, and it survived its own mutation because no header in the tree carries a
        # digit today. A column heading like `⌀ 2` is a caption, not a measurement, and a
        # header that could satisfy a claim would confirm every row in the table at once.
        claim = _Claim("bore.diameter")
        approved = SimpleNamespace(id=claim, value_text="2", value=2.0, span=None, axis=None)
        rows = (("TAG", "⌀ 2", "DEPTH"), ("A", "ø99", "THRU"))
        registry = _Registry(SimpleNamespace(table_rows=rows), (claim,))
        assert [o.state for o in verify_measurement_claims(registry, _plan(approved))] == [
            "value_absent"
        ]

    def test_a_measuring_column_still_supplies_numbers(self):
        # The automatic escalated table has no QTY: TAG | ⌀ | DEPTH | X | Y. Dropping too much
        # would make every table claim unverifiable, which is the opposite failure.
        claim = _Claim("bore.diameter")
        approved = SimpleNamespace(id=claim, value_text="2", value=2.0, span=None, axis=None)
        rows = (("TAG", "⌀", "DEPTH", "X", "Y"), ("A", "ø2", "THRU", "17.3", "12.6"))
        registry = _Registry(SimpleNamespace(table_rows=rows), (claim,))
        assert [o.state for o in verify_measurement_claims(registry, _plan(approved))] == [
            "confirmed"
        ]


class TestTheRemainingSmallGuards:
    def test_a_span_is_measured_as_a_magnitude(self):
        # Both earlier span tests used increasing spans, so dropping `abs()` passed the whole
        # fast tier. A datum above the located point is an ordinary drawing.
        approved = SimpleNamespace(
            value=0.0, value_text="", span=((15, 10, 5.5), (-60, -40, -10)), axis="z"
        )
        assert _expected_numbers(approved) == frozenset({75.0, 50.0})

    def test_a_radius_repeat_count_is_stripped_too(self):
        # `4× R25` and `10× R10` carry claims on four corpus fixtures, so the `R` half of the
        # lookahead is live — and was unpinned, surviving its own mutation.
        claim = _Claim("fillet.radius")
        approved = SimpleNamespace(id=claim, value_text="4", value=4.0, span=None, axis=None)
        registry = _Registry(SimpleNamespace(label="4× R25"), (claim,))
        assert [o.state for o in verify_measurement_claims(registry, _plan(approved))] == [
            "value_absent"
        ]
