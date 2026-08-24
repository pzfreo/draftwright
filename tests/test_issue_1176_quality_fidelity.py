"""#1176 — a drawing that says something false must not score perfect.

The reported case is a PPP0101 bracket scoring lint 1.0 while omitting its defining
radii and the whole dovetail. Its STEP is not in this repo, so this slice takes the half
that is reproducible here and is the same defect one axis over: `quality` had no component
asking whether the drawing is TRUE.

Measured before this change, on a drawing labelled 99 over a 16 mm path — a 518%
contradiction:

    completeness  available=False   (no auditable recognised requirements)
    restraint     available=False   (provenance incomplete)
    legibility    1.0

`legibility` is 1.0 correctly — its own basis field says
`layout_issue_severity_with_info_floor`, it scores LAYOUT, and a false dimension is
perfectly legible.

An earlier draft of this docstring said "1.0 was the only number a caller saw". Measured, it
is not: that drawing already reported `passed: False`, `errors: 1` and `score: 0.8`, so a
caller following ADR 0002's own advice (gate on severity and code counts, not the scalar)
caught it. The signal fidelity actually adds is the case where those gates say nothing —
`TestAGearTableCanBeFalseWhileTheDrawingPasses` below, where a normative ISO data table
prints twelve teeth on a thirteen-tooth part and `passed` is True.

So the four axes now answer four different questions: did required content land
(completeness), is there too much of it (restraint), can a reader make it out (legibility),
and is what it says true (fidelity). A drawing can pass the first three and still lie.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos, import_step

from draftwright import Sheet
from draftwright.linting.coverage import EXAMINABLE_DECLARED_KINDS
from draftwright.linting.quality import (
    _FIDELITY_CODES,
    _LEGIBILITY_CODES,
    _STAGE_ROUTED_CODES,
    _UNSCORED_CODE_PREFIXES,
    _UNSCORED_CODES,
    _is_legibility_issue,
)
from draftwright.linting.structural import is_dimension_like as _is_dimension_like

_REFS = ((0, -25, 0), (0, -9, 0))  # a 16 mm span

#: Producer call sites where the code is held in a variable the AST walk cannot resolve.
#: Shrink-only — a new one has to be argued rather than silently widening the blind spot.
#:
#: The count is measured, not described: earlier comments here miscounted what the sites
#: were, which is its own small instance of this issue's failure mode. Assert on the number;
#: run the test to see the list.
_INDIRECT_PRODUCER_SITES = 7


def _quality_of(sheet):
    sheet.authored_dimensions()
    return sheet.build().lint_summary()["quality"]


def _quality(value, label, page="A2"):
    sheet = Sheet(Box(115, 50, 68), title="T", number="T-1", page=page, scale=1)
    sheet.measured_dimension(
        kind="linear", value=value, label=label, dominant_axis="y", ref_pts=_REFS
    )
    sheet.authored_dimensions()
    return sheet.build().lint_summary()["quality"]


class TestFidelityIsReportedSeparately:
    def test_a_false_dimension_lowers_fidelity(self):
        # Mutation: drop `label_vs_measured` from `_FIDELITY_CODES` and this returns 1.0.
        quality = _quality(99, "99")
        assert quality["fidelity"]["available"] is True
        assert quality["fidelity"]["score"] < 1.0, (
            "a drawing asserting 99 mm over a 16 mm path scored perfect on fidelity"
        )

    def test_a_truthful_dimension_keeps_fidelity_perfect(self):
        # The axis must be narrow: an ordinary correct drawing is unaffected.
        quality = _quality(16, "16")
        assert quality["fidelity"]["score"] == 1.0

    def test_each_axis_publishes_its_own_basis(self):
        # `basis` is what tells a caller WHAT a component measured, and the argument for a
        # separate fidelity axis rests on legibility's basis naming layout. Shipping
        # fidelity with the same string would have made the field stop distinguishing them
        # — the PR contradicting its own evidence.
        quality = _quality(99, "99")
        assert quality["legibility"]["basis"] == "layout_issue_severity_with_info_floor"
        assert quality["fidelity"]["basis"] == "drawn_assertion_contradicted_by_its_source"

    def test_fidelity_has_no_answer_when_the_drawing_asserts_nothing(self):
        # No evidence is DATA, not a perfect score — the same contract completeness and
        # restraint already follow, and the fail-open this module's docstring argues
        # against. Unlike legibility, where an empty sheet genuinely IS legible,
        # truthfulness of an empty utterance is not the same kind of 1.0.
        #
        # Through `lint_summary()`, not `quality_components(...)`. The first cut called the
        # helper directly with `has_asserted_content=False` and so tested the helper's `if`
        # rather than the predicate: replacing the whole predicate in `drawing.py` with the
        # constant `True` — deleting the fix outright — passed every test in this file
        # (#1176 review r3). It also reported 1.0 for exactly this drawing, because the
        # predicate asked whether any item had a `label_bbox`, which a title block has.
        sheet = Sheet(Box(115, 50, 68), title="T", number="T-1", page="A2", scale=1)
        sheet.authored_dimensions()
        drawing = sheet.build()
        # `drawing.items`, which is what production reads — `iter_annotations()` yields only
        # the NAMED ones, so a precondition over it can hold while the predicate is false.
        assert not [item for item in drawing.items if _is_dimension_like(item)], (
            "the fixture now draws a measured quantity, so it does assert something"
        )
        component = drawing.lint_summary()["quality"]["fidelity"]
        assert component["available"] is False
        assert component["score"] is None
        assert component["reason"]

    def test_an_unavailable_fidelity_still_publishes_the_whole_shape(self):
        # README reads `quality["legibility"]["by_code"]`; a caller reading fidelity's the
        # same way must get an empty mapping, not a KeyError, on the drawing above. Every
        # count is genuinely zero — a finding would have made the component available.
        #
        # The whole dict is pinned, not two keys of it: the AVAILABLE shape was pinned and
        # the unavailable one was not, which is the asymmetry that let the KeyError exist in
        # the first place.
        component = _quality_of(Sheet(Box(115, 50, 68), page="A2", scale=1))["fidelity"]
        available = _quality(99, "99")["fidelity"]
        assert set(component) == set(available) | {"reason"}, (
            "the unavailable component no longer publishes the available component's keys"
        )
        assert component["available"] is False and component["score"] is None
        assert component["reason"]
        assert all(
            component[key] in (0, {})
            for key in component
            if key not in ("available", "score", "reason", "basis", "score_inventory")
        )
        assert component["basis"] == available["basis"], (
            "an unavailable component must still say WHAT it would have measured"
        )

    def test_a_detected_model_is_not_treated_as_a_declaration(self):
        # The predicate's THIRD term. `lint_declaration_reconciliation` is gated on
        # `model_declared`, so a DETECTED `kind="hole"` feature is examined by nothing —
        # dropping `self._model_declared` re-creates the declared-slot fail-open for every
        # detected drawing. It survived the whole tier because `is_dimension_like` is true for
        # essentially every detected build, so this fixture removes that cover: a detected
        # part whose dimensions have all been stripped from the drawing.
        from draftwright.builder import build_drawing

        part = Box(60, 40, 20) - Pos(15, 10, 0) * Cylinder(3, 40)
        drawing = build_drawing(part, title="T", number="N-1")
        assert not drawing.model_declared
        assert any(f.kind in EXAMINABLE_DECLARED_KINDS for f in drawing.model().features), (
            "the fixture detects nothing examinable, so the term is not under test"
        )
        for name in [
            name for name, item in drawing.iter_annotations() if _is_dimension_like(item)
        ]:
            drawing.remove(name)
        component = drawing.lint_summary()["quality"]["fidelity"]
        assert component["available"] is False, (
            "a detected model was treated as a declaration this axis can examine"
        )

    def test_a_detected_drawing_that_dimensions_the_part_is_answerable(self):
        # The `is_dimension_like` half of the predicate, which nothing exercised: deleting it
        # passed the entire 4,214-test tier, because every other fixture here reaches
        # availability through the DECLARED half (#1176 review r5). A detected build declares
        # nothing, so only the drawn dimensions can make this answerable.
        from draftwright.builder import build_drawing

        drawing = build_drawing(Box(60, 40, 20), title="T", number="N-1")
        assert not drawing.model_declared, "the fixture must reach availability the other way"
        assert any(_is_dimension_like(item) for _n, item in drawing.iter_annotations())
        component = drawing.lint_summary()["quality"]["fidelity"]
        assert component["available"] is True and component["score"] == 1.0

    def test_a_declaration_no_truth_check_examines_is_not_certified(self):
        # The predicate accepted ANY declared feature, so a declared slot — which
        # `declared_feature_absent` does not look at (`_RECON_KINDS`) and no gear check
        # touches — was reported as "checked, nothing false" by a drawing that drew nothing
        # (#1176 review r5). That is the fail-open this module's docstring forbids, reached
        # from the opposite side to round 3's fail-closed.
        sheet = Sheet(Box(80, 60, 20), title="T", number="N-1", page="A2")
        sheet.slot(
            width=6,
            length=20,
            long_axis="x",
            width_axis="y",
            depth_axis="z",
            w_center=0,
            lo=-10,
            hi=10,
            at=(0, 0, 10),
        )
        sheet.authored_dimensions()
        drawing = sheet.build()
        assert drawing.model_declared and drawing.model().features, (
            "the fixture must declare something, or it proves nothing about the second term"
        )
        component = drawing.lint_summary()["quality"]["fidelity"]
        assert component["available"] is False and component["score"] is None

    def test_a_finding_always_outranks_the_availability_gate(self):
        # A direct test of `_issue_component`, deliberately. The override is a FAIL-SAFE
        # against the predicate drifting from the checks' domains — which has happened three
        # times in this issue's review history — so a consumer-level test would have to
        # reproduce the drift it exists to survive. Round 4's consumer-level attempt did not:
        # widening the predicate made its fixture available by the other term, and deleting
        # the override then passed the whole tier while the test's comment said otherwise.
        from draftwright.linting.issues import LintIssue
        from draftwright.linting.quality import _issue_component

        component = _issue_component(
            [LintIssue(severity="error", code="label_vs_measured", message="m")],
            error_penalty=0.2,
            warning_penalty=0.05,
            available=False,
            unavailable_reason="the caller said there was nothing to check",
        )
        assert component["available"] is True, "a detected falsehood was discarded unscored"
        assert component["score"] < 1.0
        assert component["by_code"] == {"label_vs_measured": 1}

    def test_a_declared_falsehood_with_nothing_drawn_is_still_scored(self):
        # The r3 case, kept because it is the user-facing shape: a `⌀6 THRU` declared over
        # solid material, no measured quantity drawn, and fidelity must still say 0.95.
        #
        # It reaches availability through the DECLARED term — `sheet.hole(...)` is a
        # `kind == "hole"` feature, which is examinable — NOT through the `bool(issues)`
        # override. Round 5's commit message identified that this comment claimed otherwise
        # and rewrote a different test, leaving this one saying the untrue thing (review r6).
        # The override's own guard is `test_a_finding_always_outranks_the_availability_gate`.
        sheet = Sheet(Box(20, 20, 20), title="T", number="N-1", page="A2")
        sheet.hole(at=(5, 5, 0), axis="z", diameter=3, through=True)
        sheet.authored_dimensions()
        drawing = sheet.build()
        summary = drawing.lint_summary()
        assert not [item for item in drawing.items if _is_dimension_like(item)], (
            "the fixture now asserts a measured quantity, so the gate is not under test"
        )
        assert [i for i in summary["issues"] if i["code"] == "declared_feature_absent"]
        component = summary["quality"]["fidelity"]
        assert component["available"] is True, "a detected falsehood was discarded unscored"
        assert component["score"] < 1.0

    def test_the_full_fidelity_shape_is_pinned(self):
        # Legibility's complete dict is pinned in `test_make_drawing.py`; fidelity's was
        # not, so mutating its error penalty to the warning penalty, or its `available` to
        # `bool(issues)`, passed the entire 4,197-test suite.
        component = _quality(99, "99")["fidelity"]
        assert set(component) == {
            "available",
            "score",
            "errors",
            "warnings",
            "infos",
            "placement_drops",
            "by_code",
            "raw_issues",
            "primary_issues",
            "primary_errors",
            "primary_warnings",
            "primary_infos",
            "primary_by_code",
            "affected_pairs",
            "basis",
            "score_inventory",
        }
        assert component["errors"] == 1 and component["warnings"] == 0
        assert component["score"] == pytest.approx(0.8), (
            "the error penalty changed; a material contradiction must not be priced as a warning"
        )
        assert component["by_code"] == {"label_vs_measured": 1}

    def test_legibility_is_unchanged_and_still_says_the_drawing_is_readable(self):
        # The point of a separate axis. A false dimension IS legible, so legibility is
        # right to report 1.0 — folding fidelity into it would have made a layout score
        # answer a truthfulness question, which is the category error that produced the
        # hole in the first place.
        quality = _quality(99, "99")
        assert quality["legibility"]["score"] == 1.0
        assert quality["legibility"]["basis"] == "layout_issue_severity_with_info_floor"

    def test_the_axes_do_not_share_codes(self):
        # A code counted twice would penalise one defect on two axes and make them look
        # correlated when they are independent observations.
        assert not (_FIDELITY_CODES & _LEGIBILITY_CODES), (
            f"{sorted(_FIDELITY_CODES & _LEGIBILITY_CODES)} is scored on two axes"
        )
        # And nothing may be both scored and registered as unscored — the audit below
        # would still pass, while the register would be describing the opposite of the truth.
        both = (_FIDELITY_CODES | _LEGIBILITY_CODES) & _UNSCORED_CODES
        assert not both, f"{sorted(both)} are registered as unscored and also scored"

    def test_a_placement_drop_cannot_also_be_a_fidelity_code(self):
        # `_is_legibility_issue` accepts a code the set does not name when the issue carries
        # `outcome_stage="placement"` or a `_dropped` suffix. `test_the_axes_do_not_share_codes`
        # compares the two SETS and so cannot see that branch: a fidelity code arriving with
        # a placement stage would be scored twice.
        from draftwright.linting.issues import LintIssue

        # `outcome_stage="placement"` must be SET on the probe. Without it the field
        # defaults to None, `is_placement_drop` falls back to the suffix, and the branch this
        # test exists for is never executed — the test reduced to the set comparison above
        # while its comment claimed otherwise (#1176 review r4).
        doubled = [
            code
            for code in sorted(_FIDELITY_CODES)
            if _is_legibility_issue(
                LintIssue(severity="error", code=code, message="", outcome_stage="placement")
            )
        ]
        assert not doubled, f"{doubled} would be scored on legibility as well as fidelity"


class TestPlacedContentContradictedByGeometryIsAlsoFalse:
    def test_a_callout_for_a_feature_the_part_lacks_lowers_fidelity(self):
        # The PR's first cut claimed `label_vs_measured` was "currently the only such
        # code". This is worse than the case it was built for: a `⌀6 THRU` leader and a
        # centre mark are DRAWN, pointing at solid material, because the declared hole is
        # not in the part. The sheet tells a machinist to drill something the model does
        # not have, and it scored 1.0.
        sheet = Sheet(Box(80, 60, 20), title="T", number="N-1")
        sheet.hole(at=(10, 10, 0), axis="z", diameter=6, through=True)
        # `auto_dimensions`, because it is the path that draws the LABELLED callout. An
        # earlier comment here claimed the authored path "draws NOTHING for the absent hole,
        # so nothing false reaches the sheet". Measured, that is wrong twice: the authored
        # path still draws a `CenterMark` at the phantom hole, and fidelity still drops to
        # 0.95 there (`test_a_declared_falsehood_with_nothing_drawn_is_still_scored` uses
        # exactly that).
        # `declared_feature_absent` is a declaration-vs-geometry check that never inspects
        # what was placed, so the difference between the paths is how loudly the sheet
        # states the lie, not whether the check fires.
        sheet.auto_dimensions()
        drawing = sheet.build()
        summary = drawing.lint_summary()
        assert [i for i in summary["issues"] if i["code"] == "declared_feature_absent"], (
            "the fixture no longer declares a hole the part lacks"
        )
        drawn = [str(getattr(o, "label", "")) for _n, o in drawing.iter_annotations()]
        assert any("⌀6" in d for d in drawn), (
            f"nothing was drawn for the absent hole, so nothing false reached the sheet: {drawn}"
        )
        assert summary["quality"]["fidelity"]["score"] < 1.0


class TestFidelityIsAboutFalsehoodNotAbsence:
    @pytest.mark.parametrize(
        "code",
        ["dimension_kind_unsupported", "callout_dropped", "feature_not_located"],
    )
    def test_a_missing_thing_is_not_a_false_thing(self, code):
        # An omission is a gap, not a lie. Note this says where those codes do NOT belong,
        # not where they go: measured, `callout_dropped` scores on legibility (via the
        # placement-drop suffix) and `dimension_kind_unsupported` and `feature_not_located`
        # score on NOTHING. No lint code routes to completeness at all — it builds its
        # ledger from requirement outcomes. An earlier comment here claimed they "belong to
        # completeness", which was an invented mechanism.
        assert code not in _FIDELITY_CODES

    def test_an_unsupported_dimension_does_not_lower_fidelity(self):
        # #1207 refuses to draw an angular dimension rather than drawing it as a linear
        # one. Nothing false reaches the sheet, so fidelity is intact; the content is
        # missing, which is a different axis.
        sheet = Sheet(Box(115, 50, 68), title="T", number="T-1", page="A2", scale=1)
        sheet.measured_dimension(
            kind="angular",
            value=60,
            label="60°",
            dominant_axis="y",
            ref_pts=((0, -25, 0), (0, -9, 0), (0, -13.619, 8)),
        )
        sheet.authored_dimensions()
        summary = sheet.build().lint_summary()
        assert [i for i in summary["issues"] if i["code"] == "dimension_kind_unsupported"], (
            "the fixture no longer produces a refused dimension"
        )
        component = summary["quality"]["fidelity"]
        assert component["by_code"] == {}, (
            "refusing to draw something was scored as drawing something false"
        )
        # And the refusal leaves nothing this axis can examine: no measured quantity is
        # drawn, and a `measured_dimension` declaration is not a feature any truth check
        # looks at. "No answer" rather than a perfect one.
        assert component["available"] is False and component["score"] is None
        # And the refusal is itself scored by nothing — an omission is not a legibility
        # fault either. That is reported rather than left implicit.
        assert summary["quality"]["unscored"]["by_code"]["dimension_kind_unsupported"] == 1
        assert not summary["quality"]["unscored"]["unclassified"]


_WHEEL = Path(__file__).parent / "fixtures" / "issue_1058_wheel_rh.step"
_WHEEL_TEETH = 13  # independently proved by `recognise_repeating_radial_profiles`


def _wheel_gear_sheet(tooth_count, axis="z"):
    """The 13-tooth wheel with a declared metric external spur gear over it.

    `at` and `face_width` must reproduce the proved profile's centre and axial span exactly
    or `lint_declared_gear_coverage` reports `gear_correspondence_unverifiable` and never
    reaches the reconciliation this exercises — which is what a first attempt using the bbox
    CENTRE (x = -0.013) did.
    """
    part = import_step(str(_WHEEL))
    sheet = Sheet(part, title="T", number="N-1", page="A2", scale=1)
    sheet.external_spur_gear(
        at=(0, 0, 0),
        axis=axis,
        tooth_count=tooth_count,
        module=1.0,
        pressure_angle=20.0,
        profile_shift=0.0,
        face_width=float(part.bounding_box().size.Z),
        tooth_thickness=1.5,
        tooth_thickness_tolerance=(-0.05, 0.0),
        flank_tolerance_class=8,
    )
    sheet.authored_dimensions()
    return sheet


class TestAGearTableCanBeFalseWhileTheDrawingPasses:
    """The case that justifies the axis, and the one three of its five codes had no test for.

    Deleting `gear_repeat_count_mismatch`, `gear_axis_mismatch` and `gear_requirement_mismatch`
    from `_FIDELITY_CODES` passed the entire 4,201-test fast tier (#1176 review r3). The
    existing gear tests call `lint_declared_gear_coverage` with synthetic profile objects and
    never reach `lint_summary()`.
    """

    def test_a_data_table_stating_the_wrong_tooth_count_lowers_fidelity(self):
        drawing = _wheel_gear_sheet(_WHEEL_TEETH - 1).build()
        summary = drawing.lint_summary()
        rows = [
            row
            for _n, item in drawing.iter_annotations()
            for row in getattr(item, "gear_requirement_rows", None) or ()
        ]
        assert ("TEETH z", "12", "ISO 21771-1:2024") in rows, (
            f"the sheet no longer PRINTS the false tooth count, so nothing asserts it: {rows}"
        )
        assert summary["passed"] is True, (
            "the severity gates now catch this, so it no longer shows what fidelity adds"
        )
        assert summary["quality"]["fidelity"]["by_code"] == {"gear_repeat_count_mismatch": 1}
        assert summary["quality"]["fidelity"]["score"] < 1.0

    def test_the_true_tooth_count_keeps_fidelity_perfect(self):
        summary = _wheel_gear_sheet(_WHEEL_TEETH).build().lint_summary()
        assert not [i for i in summary["issues"] if i["code"].startswith("gear_")], (
            "the correspondence broke, so the mismatch test above proves nothing"
        )
        assert summary["quality"]["fidelity"]["score"] == 1.0

    def test_a_table_that_no_longer_carries_the_authored_values_lowers_fidelity(self):
        # `gear_requirement_mismatch` was the last member with no behavioural guard: moving
        # it into `_UNSCORED_CODES` — declaring a stale normative table not to be a falsehood
        # — passed the entire fast tier (#1176 review r4).
        #
        # It fires when the PLACED table stops matching the requirement it claims to carry,
        # which no ordinary build produces, so the table is edited after placement. That is
        # the defect exactly: a sheet whose data table and its source have diverged. This is
        # also the member whose source of truth is the authored requirement rather than the
        # geometry, which is why the published basis says "its source".
        drawing = _wheel_gear_sheet(_WHEEL_TEETH).build()
        table = next(
            item
            for _n, item in drawing.iter_annotations()
            if getattr(item, "gear_requirement_values", None) is not None
        )
        stale = list(table.gear_requirement_values)
        stale[0] = _WHEEL_TEETH - 1
        table.gear_requirement_values = tuple(stale)
        summary = drawing.lint_summary()
        assert summary["quality"]["fidelity"]["by_code"] == {"gear_requirement_mismatch": 1}
        assert summary["quality"]["fidelity"]["score"] < 1.0

    def test_a_data_table_bound_to_the_wrong_axis_lowers_fidelity(self):
        summary = _wheel_gear_sheet(_WHEEL_TEETH, axis="x").build().lint_summary()
        assert summary["quality"]["fidelity"]["by_code"] == {"gear_axis_mismatch": 1}
        assert summary["quality"]["fidelity"]["score"] < 1.0


#: Producer callables whose ``code`` argument is a lint code. `LintIssue`'s is the FOURTH
#: positional field (`severity, message, location, code`), which is why the positional form
#: has to be read by index rather than assumed absent (#1176 review r5).
_PRODUCERS = {"LintIssue": 3, "record_issue": 1, "_record_build_issue": 1}

#: Callables that take a lint code as ``drop_code`` DATA and record it later. Eleven codes
#: reach the engine only this way and were invisible to an audit that read literals at
#: producer calls alone. Only the two entry points are listed: the forwarding layers
#: (`_render_polygonal_prisms` among them) are DISCOVERED by resolving to a fixed point, so a
#: new one is followed rather than needing to be remembered here.
_STAGED_RECORDERS = {
    "_leader_callout_pass",  # historical mutation probes below
    "place_machined_leader_jobs",
    "FeatureLeaderJob",
}


def _is_forwarded_parameter(expr, tree, node) -> bool:
    """Whether *expr* is a parameter of some function enclosing *node*.

    Those are the forwarding layers the fixed point already follows to their callers, so
    counting them again would ratchet on plumbing rather than on a hidden code. ANY enclosing
    scope, not just the innermost: `holes.py`'s pitch helper forwards its outer function's
    `drop_code` from inside a nested closure, where the name is a free variable rather than a
    parameter of the `def` it sits in.
    """
    if not isinstance(expr, ast.Name):
        return False
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not func.lineno <= node.lineno <= (func.end_lineno or func.lineno):
            continue
        args = func.args
        if expr.id in {a.arg for a in (*args.args, *args.posonlyargs, *args.kwonlyargs)}:
            return True
    return False


def _producer_aliases(trees) -> dict[str, str]:
    """``{local name: producer name}`` for aliased imports of a producer.

    The walk matches producers by NAME, so `from ...issues import LintIssue as _LI` made
    every `_LI(...)` call invisible — not counted as a producer and not counted as indirect
    (#1176 review r6). Simple assignment aliases (`_mk = LintIssue`) are followed too,
    iterated so a chain resolves.

    **The limit, measured rather than asserted.** Attribute access is *not* a blind spot when
    the attribute is named after a producer: `mod.LintIssue(...)` and `ctx.record_issue(...)`
    are both matched on the attribute name, and the latter is the only way `record_issue` is
    ever reached. What IS invisible: a `functools.partial`, a dict entry, a subclass, and —
    measured while writing this — an annotated binding (`_mk: object = LintIssue`) or a
    tuple-unpack binding, neither of which this function follows. There is nothing to count
    for any of them, because the walk cannot tell such a call from any other call in the
    package.

    So "every code the engine emits is classified" holds for producers spelled as themselves,
    reached as an attribute of that name, or bound by a plain assignment. It is not a proof
    against a determined author. What it is proof against is the accident, which is what has
    actually happened six times. The first version of this paragraph claimed one gap that is
    not one and missed two that are (#1176 re-check) — which is the failure mode this whole
    slice is about, in the paragraph written to be honest about limits.
    """
    aliases: dict[str, str] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.asname and alias.name in _PRODUCERS:
                        aliases[alias.asname] = alias.name
    while True:
        before = len(aliases)
        for tree in trees.values():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Name):
                    continue
                source = aliases.get(node.value.id, node.value.id)
                if source not in _PRODUCERS:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = source
        if len(aliases) == before:
            break
    return aliases


def _enclosing_function(tree, node):
    best = None
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) and func.lineno <= (
            node.lineno
        ) <= (func.end_lineno or func.lineno):
            if best is None or func.lineno > best.lineno:
                best = func
    return best


def _emitted_codes(sources=None):
    """Every lint code the engine can produce, by AST over its producer forms.

    *sources* is a ``{name: text}`` mapping for the smuggling tests below, which feed the
    walk a snippet instead of the package. Default: every module under ``src/draftwright``.

    Three direct producers — `LintIssue`, `ctx.record_issue` and
    `Drawing._record_build_issue`, each taking the code positionally or by keyword — plus
    every ``drop_code=`` argument, which is a lint code handed to a recorder as data.

    Read from the source rather than by running the engine, because a code emitted only on a
    path no test reaches is exactly the one that would slip through unclassified.

    Successive rounds of #1176 review found five escapes from earlier versions of this walk,
    each of which let a brand-new code pass every audit below: it required two positional
    args and so skipped the keyword form of the producer it modelled; it did not know
    `_record_build_issue` existed; it treated a conditional expression as opaque, so a third
    branch hid a new code inside a site already counted; it read `LintIssue`'s code only as a
    keyword, though its positional slot is reachable; and it did not follow ``drop_code``,
    which is how eleven `*_dropped` codes reach `record_issue`. Each of the five is a
    regression test in `TestTheAuditCannotBeSmuggledPast`.

    Returns ``(literals, prefixes, indirect, stages)`` where ``stages`` maps a code to the
    set of ``outcome_stage`` values its producers can give it — ``"<staged>"`` for a code
    handed to a leader job, whose one recorder stages it ``"validation"`` on a
    rendered-geometry failure.
    """
    literals: set[str] = set()
    prefixes: set[str] = set()
    indirect: list[str] = []
    stages: dict[str, set[str | None]] = {}
    root = Path(__file__).resolve().parents[1] / "src" / "draftwright"
    if sources is None:
        trees = {path: ast.parse(path.read_text()) for path in sorted(root.rglob("*.py"))}
    else:
        trees = {root / name: ast.parse(text) for name, text in sources.items()}

    def leaves(expr, path, out):
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            out.append(expr.value)
        elif isinstance(expr, ast.IfExp):
            leaves(expr.body, path, out)
            leaves(expr.orelse, path, out)
        elif isinstance(expr, ast.JoinedStr) and isinstance(expr.values[0], ast.Constant):
            out.append(expr.values[0].value + "{}")
        else:
            out.append(None)
            indirect.append(f"{path}:{expr.lineno}")

    def record(expr, path, staged):
        found: list = []
        leaves(expr, path, found)
        for code in found:
            if code is None:
                continue
            if code.endswith("{}"):
                prefixes.add(code[:-2])
            else:
                literals.add(code)
                stages.setdefault(code, set()).add(staged)

    aliases = _producer_aliases(trees)

    # Which functions forward a `drop_code` parameter into a staged recorder. Resolved to a
    # fixed point rather than listed, so a new forwarding layer is followed instead of
    # hiding its codes.
    recorders = set(_STAGED_RECORDERS)
    while True:
        before = len(recorders)
        for path, tree in trees.items():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name not in recorders:
                    continue
                arg = next((k for k in node.keywords if k.arg == "drop_code"), None)
                if arg is not None and isinstance(arg.value, ast.Name):
                    enclosing = _enclosing_function(tree, node)
                    if enclosing is not None:
                        recorders.add(enclosing.name)
        if len(recorders) == before:
            break

    for path, tree in trees.items():
        rel = path.relative_to(root)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # A `drop_code` DEFAULT is a lint code too. The walk read call keywords only,
                # so introducing a code as a forwarding helper's default hid it in exactly the
                # way a module constant did one round earlier — and the shape already exists
                # (`holes.py` carries `drop_code=None` defaults on two staged forwarders), so
                # changing one to a string is the accident this guards (#1176 re-check).
                args = node.args
                positional = [*args.posonlyargs, *args.args]
                pairs = [
                    *zip(positional[len(positional) - len(args.defaults) :], args.defaults),
                    *zip(args.kwonlyargs, args.kw_defaults),
                ]
                for arg, default in pairs:
                    if arg.arg != "drop_code" or default is None:
                        continue
                    if isinstance(default, ast.Constant) and isinstance(default.value, str):
                        record(default, rel, "<staged>" if node.name in recorders else None)
                    elif not isinstance(default, ast.Constant):
                        indirect.append(f"{rel}:{default.lineno}")
                continue
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            name = aliases.get(name, name)
            if any(k.arg is None for k in node.keywords) and name in _PRODUCERS:
                # `LintIssue(**kwargs)` — the code is not readable and must not pass as
                # "no code here".
                indirect.append(f"{rel}:{node.lineno}")
                continue
            if name in recorders and any(k.arg is None for k in node.keywords):
                # `_leader_callout_pass(..., **{"drop_code": …})` — unreadable, so counted.
                indirect.append(f"{rel}:{node.lineno}")
            drop = next((k for k in node.keywords if k.arg == "drop_code"), None)
            if drop is not None:
                if isinstance(drop.value, ast.Constant):
                    record(drop.value, rel, "<staged>" if name in recorders else None)
                elif not _is_forwarded_parameter(drop.value, tree, node):
                    # A `drop_code` that is neither a literal nor the enclosing function's own
                    # parameter hides a code the walk cannot read — a module constant, say,
                    # which is ordinary refactoring rather than an adversarial act. Counting
                    # it as indirect is what makes the ratchet bite (#1176 review r6).
                    indirect.append(f"{rel}:{drop.value.lineno}")
            if name not in _PRODUCERS:
                continue
            expr = next((k.value for k in node.keywords if k.arg == "code"), None)
            if expr is None:
                index = _PRODUCERS[name]
                expr = node.args[index] if len(node.args) > index else None
            if expr is None:
                continue
            stage = next((k.value for k in node.keywords if k.arg == "outcome_stage"), None)
            if stage is None:
                staged: str | None = None
            elif isinstance(stage, ast.Constant):
                staged = stage.value
            else:
                staged = "<conditional>"
            record(expr, rel, staged)
    return literals, prefixes, indirect, stages


class TestEveryLintCodeIsClassified:
    """The fail-closed half of the classification, which the first cut only apologised for.

    `_FIDELITY_CODES`' own note says a new truth-class code "will score as perfectly
    truthful until somebody adds it". Admitting a hole is not closing one — the precedent is
    `test_quality_components.py`'s recognition-inventory audit, and ADR 0017's manifest,
    which is fail-closed for exactly this reason.
    """

    def test_no_lint_code_scores_on_an_unstated_axis(self):
        literals, _prefixes, _indirect, _stages = _emitted_codes()
        classified = _FIDELITY_CODES | _LEGIBILITY_CODES | _UNSCORED_CODES | _STAGE_ROUTED_CODES
        unclassified = sorted(
            code for code in literals if code not in classified and not code.endswith("_dropped")
        )
        assert not unclassified, (
            f"{unclassified} score on no quality component and are not registered as "
            "unscored — a new truth-class code would be reported as perfectly truthful. Add "
            "each to _FIDELITY_CODES, _LEGIBILITY_CODES or _UNSCORED_CODES."
        )

    def test_a_dropped_suffix_is_only_trusted_where_it_is_true(self):
        # The audit above exempts `*_dropped` because the suffix routes to legibility. That
        # is true only where no emission site sets `outcome_stage`, which takes precedence
        # (`issues.py::is_placement_drop`) — and `section_dropped` is emitted ONLY as
        # `"validation"`, so it reaches no component at all while a comment beside it
        # asserted the opposite (#1176 review r4). The exemption is now conditional on the
        # thing it assumes.
        _literals, _prefixes, _indirect, stages = _emitted_codes()
        unexamined = sorted(
            code
            for code, seen in stages.items()
            if code.endswith("_dropped")
            and not seen.isdisjoint({"validation", "<conditional>", "<staged>"})
            and code not in (_UNSCORED_CODES | _STAGE_ROUTED_CODES)
        )
        assert not unexamined, (
            f"{unexamined} are emitted with a validation outcome_stage, so the `_dropped` "
            "suffix does NOT put them in the legibility inventory. Register each in "
            "_UNSCORED_CODES (always unscored) or _STAGE_ROUTED_CODES (scored per issue)."
        )

    def test_the_registers_describe_codes_that_exist(self):
        # The other direction: a renamed code must not leave a dead entry behind, which
        # would make the audit above pass while the real code went unclassified.
        literals, _prefixes, _indirect, _stages = _emitted_codes()
        registered = _FIDELITY_CODES | _UNSCORED_CODES | _STAGE_ROUTED_CODES | _LEGIBILITY_CODES
        stale = sorted(registered - literals)
        assert not stale, f"{stale} are registered but no longer emitted anywhere"

    def test_every_interpolated_code_family_is_registered(self):
        _literals, prefixes, _indirect, _stages = _emitted_codes()
        assert sorted(prefixes) == sorted(_UNSCORED_CODE_PREFIXES)

    def test_the_indirect_producer_sites_do_not_grow(self):
        # Call sites that forward a code held in a variable, so the audit cannot see the
        # string. A shrink-only ratchet (the `_ALLOW` pattern from
        # `test_private_test_imports.py`): a new indirection has to be argued rather than
        # silently widening the blind spot. Walking conditional expressions to their leaves
        # took this from ten sites to the genuinely opaque ones.
        _literals, _prefixes, indirect, _stages = _emitted_codes()
        assert len(indirect) <= _INDIRECT_PRODUCER_SITES, (
            f"a new lint code is passed through a variable, so the classification audit "
            f"cannot see it: {indirect}"
        )

    def test_a_stage_routed_code_is_not_reported_as_unclassified(self):
        # A stage-routed code scores on legibility for its placement instances and on nothing
        # for its validation ones, so a validation instance reaching `unscored` is EXPECTED,
        # not an unclassified code. Dropping `_STAGE_ROUTED_CODES` from the filter passed
        # both quality test files (#1176 review r5).
        from draftwright.linting.issues import LintIssue
        from draftwright.linting.quality import _unscored_component

        report = _unscored_component(
            [
                LintIssue(
                    severity="warning",
                    code="chamfer_dropped",
                    message="m",
                    outcome_stage="validation",
                ),
                LintIssue(severity="warning", code="not_a_registered_code", message="m"),
            ]
        )
        assert report["by_code"] == {"chamfer_dropped": 1, "not_a_registered_code": 1}
        assert report["unclassified"] == ["not_a_registered_code"], (
            "a registered stage-routed code was reported as nobody's decision"
        )

    def test_the_unscored_inventory_reads_like_the_components_beside_it(self):
        # It sits under `quality` next to four components, so `for c in quality.values():
        # c["available"]` must not raise on it — the same ergonomic contract this file
        # dedicates a test to for the unavailable fidelity shape (#1176 review r5).
        quality = _quality(99, "99")
        assert all("available" in component for component in quality.values())
        assert quality["unscored"]["available"] is True
        assert quality["unscored"]["score"] is None, "the inventory must not become a score"

    def test_the_runtime_report_agrees_with_the_static_register(self):
        # `quality["unscored"]["unclassified"]` is the same question asked of one drawing's
        # own findings, which is what gives `_UNSCORED_CODES` a production consumer rather
        # than making it test data shipped in `src/` (#1176 review r4).
        sheet = Sheet(Box(115, 50, 68), title="T", number="T-1", page="A2", scale=1)
        sheet.measured_dimension(
            kind="linear", value=99, label="99", dominant_axis="y", ref_pts=_REFS
        )
        sheet.authored_dimensions()
        quality = sheet.build().lint_summary()["quality"]
        assert quality["unscored"]["unclassified"] == []
        assert quality["unscored"]["issues"] == sum(quality["unscored"]["by_code"].values())


class TestTheAuditCannotBeSmuggledPast:
    """One regression test per escape found in a previous version of `_emitted_codes`.

    Each ran green against the walk of its day. They feed the walk a snippet rather than
    mutating `src/`, so they are fast and deterministic; the source-wide audits above are
    what tie the walk to the real package.
    """

    def _codes(self, body):
        literals, _prefixes, indirect, stages = _emitted_codes({"snippet.py": body})
        return literals, indirect, stages

    def test_a_keyword_code_on_record_issue_is_seen(self):
        # The walk read `record_issue`'s code positionally and required two positional args,
        # so the keyword form of the very producer it modelled was skipped (review r4).
        literals, _indirect, _stages = self._codes(
            'ctx.record_issue(severity="warning", code="smuggled_kw", message="m")'
        )
        assert "smuggled_kw" in literals

    def test_the_build_issue_producer_is_seen(self):
        # A third live producer the walk did not know about (review r4).
        literals, _indirect, _stages = self._codes(
            'self._record_build_issue("warning", "smuggled_build", "m")'
        )
        assert "smuggled_build" in literals

    def test_every_branch_of_a_conditional_code_is_seen(self):
        # Widening `"a" if p else "b"` to a third branch hid a new code inside a call site
        # already counted by the indirect ratchet (review r4).
        literals, _indirect, _stages = self._codes(
            'ctx.record_issue("warning", "smuggled_if" if p else ("a" if q else "b"), "m")'
        )
        assert {"smuggled_if", "a", "b"} <= literals

    def test_a_positional_code_on_lintissue_is_seen(self):
        # `LintIssue`'s code is its FOURTH field, and the walk read it only as a keyword
        # (review r5).
        literals, _indirect, _stages = self._codes(
            'LintIssue("error", "m", None, "smuggled_positional")'
        )
        assert "smuggled_positional" in literals

    def test_a_splatted_producer_call_is_counted_not_ignored(self):
        # `LintIssue(**kwargs)` matched no `code=` keyword, so it was skipped AND not
        # counted as indirect — invisible twice over (review r5).
        literals, indirect, _stages = self._codes("LintIssue(**kwargs)")
        assert not literals
        assert indirect, "a splatted producer call passed as 'no code here'"

    def test_a_code_handed_over_as_drop_code_data_is_seen(self):
        # Eleven `*_dropped` codes reach `record_issue` only as a `drop_code` argument, and
        # the leader recorder can stage them "validation" — the `section_dropped` defect,
        # times eleven, invisible to a walk that read literals at producer calls (review r5).
        literals, _indirect, stages = self._codes(
            '_leader_callout_pass(dwg, a, jobs, drop_code="smuggled_drop", ctx=ctx)'
        )
        assert "smuggled_drop" in literals
        assert stages["smuggled_drop"] == {"<staged>"}

    def test_a_drop_code_in_a_parameter_default_is_seen(self):
        # The same class as the module constant one round earlier: the walk read call
        # KEYWORDS only, so a code introduced as a forwarding helper's default was in neither
        # register. The shape already exists — `holes.py` carries `drop_code=None` defaults on
        # two staged forwarders, so changing one to a string is the accident (#1176 re-check).
        literals, _indirect, stages = self._codes(
            "def _probe(dwg, a, jobs, ctx, drop_code='x_dropped'):\n"
            "    return _leader_callout_pass(dwg, a, jobs, drop_code=drop_code, ctx=ctx)\n"
        )
        assert "x_dropped" in literals
        assert stages["x_dropped"] == {"<staged>"}

    def test_a_splatted_drop_code_is_counted(self):
        # `_leader_callout_pass(..., **{"drop_code": …})`. The `**kwargs` guard covered the
        # three producers and not the recorders.
        _literals, indirect, _stages = self._codes(
            '_leader_callout_pass(dwg, a, jobs, **{"drop_code": "y_dropped"}, ctx=ctx)\n'
        )
        assert indirect, "a splatted recorder call passed as 'no code here'"

    def test_a_drop_code_held_in_a_constant_is_counted(self):
        # `drop_code` was recorded only when it was a literal; a `Name` was used to grow the
        # recorder set and then silently dropped — neither in `literals` nor in `indirect`.
        # Factoring a repeated code into a module constant is ordinary refactoring, so this
        # was accident-plausible, and at runtime the leader recorder stages it "validation"
        # on a geometry failure: `section_dropped`'s defect, invisible (#1176 review r6).
        literals, indirect, _stages = self._codes(
            '_MUT = "x_dropped"\n_leader_callout_pass(dwg, a, jobs, drop_code=_MUT, ctx=ctx)\n'
        )
        assert not literals
        assert indirect, "a code held in a module constant passed as 'no code here'"

    def test_an_aliased_producer_import_is_still_a_producer(self):
        # The walk matched producers by NAME, so `LintIssue as _LI` made every call
        # invisible — not a producer, and not counted as indirect either (#1176 review r6).
        literals, _indirect, _stages = self._codes(
            "from draftwright.linting.issues import LintIssue as _LI\n"
            '_LI("error", "m", None, "smuggled_alias")\n'
        )
        assert "smuggled_alias" in literals

    def test_a_producer_bound_to_another_name_is_still_a_producer(self):
        # `_mk = LintIssue` then `_mk(...)`, resolved by iterating the assignments so a chain
        # resolves too. A producer reached through an attribute, a `partial` or a dict entry
        # stays invisible — `_producer_aliases` says so plainly rather than implying the walk
        # is proof against a determined author (#1176 review r6).
        literals, _indirect, _stages = self._codes(
            '_mk = LintIssue\n_mk2 = _mk\n_mk2("error", "m", None, "smuggled_var")\n'
        )
        assert "smuggled_var" in literals

    def test_the_recorder_resolution_converges_rather_than_giving_up(self):
        # `for _ in range(4)` silently exempted a code behind five forwarding layers: staged
        # `None` instead of `<staged>`, with `indirect` still zero (#1176 review r6).
        # REVERSE dependency order, deliberately: defined innermost-first, one pass over the
        # source resolves the whole chain and a bounded loop looks fine. Written outermost
        # first, each pass advances the frontier by one layer, which is the shape that
        # exposed `range(4)`.
        layers = "".join(
            f"def _w{i}(drop_code):\n    return _w{i - 1}(drop_code=drop_code)\n"
            for i in range(6, 0, -1)
        )
        body = (
            layers + "def _w0(drop_code):\n"
            "    return _leader_callout_pass(dwg, a, jobs, drop_code=drop_code, ctx=ctx)\n"
            + '_w6(drop_code="smuggled_deep")\n'
        )
        _literals, _indirect, stages = self._codes(body)
        assert stages["smuggled_deep"] == {"<staged>"}

    def test_a_forwarded_drop_code_makes_its_caller_a_recorder(self):
        # The forwarding layer is resolved to a fixed point, not listed: a helper that passes
        # its own `drop_code` parameter into a job makes ITS callers' codes staged too.
        literals, _indirect, stages = self._codes(
            "def _wrapper(drop_code):\n"
            "    return _leader_callout_pass(dwg, a, jobs, drop_code=drop_code, ctx=ctx)\n"
            '_wrapper(drop_code="smuggled_forwarded")\n'
        )
        assert stages["smuggled_forwarded"] == {"<staged>"}
        assert "smuggled_forwarded" in literals
