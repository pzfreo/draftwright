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
from build123d import Box, import_step

from draftwright import Sheet
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

#: Producer call sites that forward a code held in a variable, so the AST walk cannot read
#: the string. Shrink-only. Seven, down from the first cut's ten once conditional
#: expressions were walked to their leaves; six are the pass-through parameters of the drop
#: recorders in `annotations/`, and the seventh is `Drawing._record_build_issue` forwarding
#: its own argument.
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
        assert not [item for _n, item in drawing.iter_annotations() if _is_dimension_like(item)], (
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

    def test_a_finding_outranks_the_availability_gate(self):
        # The gate must never DISCARD a falsehood. This drawing draws no measured quantity,
        # so the predicate says "asserts nothing" — and it carries a `declared_feature_absent`
        # for a hole the part lacks. Reported as `{available: False, score: None}`, the round-2
        # fix would have failed closed over a detected lie, which is worse than the fail-open
        # it replaced (#1176 review r3). Mutation: drop `available = available or bool(issues)`.
        sheet = Sheet(Box(20, 20, 20), title="T", number="N-1", page="A2")
        sheet.hole(at=(5, 5, 0), axis="z", diameter=3, through=True)
        sheet.authored_dimensions()
        drawing = sheet.build()
        summary = drawing.lint_summary()
        assert not [item for _n, item in drawing.iter_annotations() if _is_dimension_like(item)], (
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
        # 0.95 there (`test_a_finding_outranks_the_availability_gate` uses exactly that).
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
        assert component["score"] == 1.0
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


def _emitted_codes():
    """Every lint code the engine can produce, by AST over its THREE producer forms.

    `LintIssue(code=…)`, `ctx.record_issue(severity, code, …)` and
    `Drawing._record_build_issue(severity, code, …)`, each accepting the code positionally or
    by keyword. Read from the source rather than by running the engine, because a code
    emitted only on a path no test reaches is exactly the one that would slip through
    unclassified.

    The first cut of this walk had three demonstrated escapes, each of which let a brand-new
    code pass all four audits (#1176 review r4): it required `len(args) >= 2` and so skipped
    the keyword form of the very producer it modelled; it did not know about
    `_record_build_issue` at all; and it treated a conditional expression as opaque, so
    widening `"pmi_dropped" if source_ids else "gdt_dropped"` to a third branch hid a new
    code inside a call site already counted. Conditional branches are now walked to their
    constant leaves.
    """
    literals: set[str] = set()
    prefixes: set[str] = set()
    indirect: list[str] = []
    stages: dict[str, set[str | None]] = {}
    root = Path(__file__).resolve().parents[1] / "src" / "draftwright"

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

    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in ("LintIssue", "record_issue", "_record_build_issue"):
                continue
            expr = next((k.value for k in node.keywords if k.arg == "code"), None)
            if expr is None and name != "LintIssue" and len(node.args) >= 2:
                expr = node.args[1]
            if expr is None:
                continue
            stage = next((k.value for k in node.keywords if k.arg == "outcome_stage"), None)
            if stage is None:
                staged: str | None = None
            elif isinstance(stage, ast.Constant):
                staged = stage.value
            else:
                staged = "<conditional>"
            found: list = []
            leaves(expr, path.relative_to(root), found)
            for code in found:
                if code is None:
                    continue
                if code.endswith("{}"):
                    prefixes.add(code[:-2])
                else:
                    literals.add(code)
                    stages.setdefault(code, set()).add(staged)
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
            and ("validation" in seen or "<conditional>" in seen)
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
        stale = sorted((_FIDELITY_CODES | _UNSCORED_CODES | _STAGE_ROUTED_CODES) - literals)
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
