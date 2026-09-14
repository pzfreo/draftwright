"""Actionable lint suggestions for incomplete drawings."""

import pytest
from _parts import crowded_shoulder_part as _crowded_shoulder_part
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.linting import LintIssue


@pytest.fixture
def plain_box_dwg(shared_drawing):
    return shared_drawing("box_60x40x20")


class TestLintSuggestions:
    """#29: each LintIssue carries a `suggestion` (str | None) with a fix snippet."""

    def test_diameter_only_finding_has_advice_without_an_unproven_edit(self):
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(5, 20)
        dwg = build_drawing(part, auto_dims=False)
        issue = next(i for i in dwg.lint() if i.code == "feature_not_dimensioned")
        assert "separate" in issue.suggestion
        assert "dwg.callout(" not in issue.suggestion

    @pytest.mark.parametrize("pattern", [False, True])
    def test_missing_bore_suggestion_executes_for_its_verified_owner(self, pattern):
        part = Box(120, 40, 20)
        for x in (-40, -20, 0, 20, 40) if pattern else (15,):
            part -= Pos(x, 0, 0) * Cylinder(4, 20)
        dwg = build_drawing(part, auto_dims=False)
        issue = next(
            i
            for i in dwg.lint()
            if i.code == "hole_requirement_missing"
            and any(parameter == "bore.diameter" for _, parameter in i.hole_requirement_ids)
        )
        assert {f.kind for f, _ in issue.hole_requirement_ids} == {
            "pattern" if pattern else "hole"
        }
        assert "dwg.callout(" in issue.suggestion
        exec(issue.suggestion, {"dwg": dwg})
        assert not any(i.code == "feature_not_dimensioned" for i in dwg.lint())

    def test_clean_drawing_has_no_suggestions(self, plain_box_dwg):
        # A fully auto-dimensioned plain box should lint clean → no suggestions.
        for i in plain_box_dwg.lint():
            assert i.suggestion is None

    def test_lint_summary_omits_none_suggestion(self, plain_box_dwg):
        # A clean box: issue dicts (if any) must not carry a suggestion key.
        for d in plain_box_dwg.lint_summary()["issues"]:
            assert "suggestion" not in d

    def test_lint_summary_includes_present_suggestion(self):
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(5, 20)
        dwg = build_drawing(part, auto_dims=False)
        dicts = [d for d in dwg.lint_summary()["issues"] if d["code"] == "feature_not_dimensioned"]
        assert dicts
        assert "suggestion" in dicts[0]
        assert dicts[0]["suggestion"]

    def test_step_dim_dropped_suggestion_mentions_detail_view(self):
        dwg = build_drawing(_crowded_shoulder_part(), detail_view=False)
        issues = [i for i in dwg.lint() if i.code == "step_dim_dropped"]
        assert issues, "crowded shoulders should drop a step dim"
        assert "detail_view=True" in issues[0].suggestion

    def test_annotation_overlap_suggestion_prefers_dimension_with_place_dim_fallback(self):
        # Synthetic issue — exercise the _suggest_fix branch directly.
        from draftwright.linting import _suggest_fix

        dwg = build_drawing(Box(60, 40, 20))
        issue = LintIssue(
            severity="warning",
            message="labels 'dim_width' and 'dim_height' overlap by 3.0×2.0 mm",
            code="annotation_overlap",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "dwg.dimension" in sug
        assert "pin=True" in sug
        assert "place_dim" in sug
        assert "dim_width" in sug

    def test_dim_inside_part_suggestion_prefers_dimension_with_place_dim_fallback(
        self, plain_box_dwg
    ):
        from draftwright.linting import _suggest_fix

        dwg = plain_box_dwg
        issue = LintIssue(
            severity="warning",
            message="Dim 'dim_height': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "dwg.dimension" in sug
        assert "pin=True" in sug
        assert "place_dim" in sug
        assert "dim_height" in sug

    def test_plate_thickness_dropped_suggestion_authors_a_thickness_dim(self, plain_box_dwg):
        # #641 gap 4: assert the snippet CONTENT, not just its presence — a dropped plate
        # thickness must point at a feature-backed thickness dim, not a raw-coordinate hack.
        from draftwright.linting import _suggest_fix

        dwg = plain_box_dwg
        issue = LintIssue(
            severity="warning",
            message="plate thickness 5.0 dropped",
            code="plate_thickness_dropped",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "dwg.dimension" in sug
        assert 'role="thickness"' in sug
        assert "pin=True" in sug

    def test_chamfer_dropped_suggestion_mentions_detail_view(self, plain_box_dwg):
        # #641 gap 4: a dropped chamfer leader should steer the user to an enlarged detail view.
        from draftwright.linting import _suggest_fix

        dwg = plain_box_dwg
        issue = LintIssue(
            severity="warning", message="chamfer callout dropped", code="chamfer_dropped"
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "detail_view=True" in sug

    def test_step_position_dropped_suggestion_mentions_detail_view(self, plain_box_dwg):
        # #641 gap 4: a dropped shoulder position rebuilds as a set, so the fix is to free strip
        # room / use a detail view — NOT to author one shoulder by hand.
        from draftwright.linting import _suggest_fix

        dwg = plain_box_dwg
        issue = LintIssue(
            severity="warning", message="step position dropped", code="step_position_dropped"
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "detail_view=True" in sug

    def test_unknown_code_has_no_suggestion(self, plain_box_dwg):
        from draftwright.linting import _suggest_fix

        dwg = plain_box_dwg
        issue = LintIssue(severity="info", message="something", code="some_unhandled_code")
        assert _suggest_fix(issue, dwg) is None

    def test_non_integer_diameter_suggestion_uses_identity_not_rounded_text(self):
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(4.111, 20)
        dwg = build_drawing(part, auto_dims=False)
        issue = next(
            i
            for i in dwg.lint()
            if i.code == "hole_requirement_missing"
            and any(parameter == "bore.diameter" for _, parameter in i.hole_requirement_ids)
        )
        assert "ø8.2" in issue.message
        assert "dwg.callout(" in issue.suggestion
        exec(issue.suggestion, {"dwg": dwg})
        assert not any(i.code == "feature_not_dimensioned" for i in dwg.lint())

    def test_feature_count_mismatch_cannot_suggest_a_diameter_total(self, plain_box_dwg):
        from draftwright.linting import _suggest_fix

        issue = LintIssue(
            severity="warning",
            message="4 ø8.5 features on the part but callouts account for 1",
            code="feature_count_mismatch",
        )
        suggestion = _suggest_fix(issue, plain_box_dwg)
        assert "count=" not in suggestion
        assert "HoleCallout(" not in suggestion
        assert "distinct axes" in suggestion
