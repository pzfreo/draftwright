"""#1250 review findings, as executable claims.

The review of PR #1265 found that the coverage guard did not protect the case the change was
written for. `_placed_requirements` reads `registry.measurement_of`, and authored/PMI
dimensions do not thread identities through it — so for the whole ADR 0011 declared surface
both sides of the comparison are empty and `frozenset() >= frozenset()` waves every candidate
through. The search walked past 1:5 (five of six dimensions placed) to 1:10, where the
dimensions had gone DEGENERATE rather than dropped — no placement failure to see — and
returned a sheet carrying a title block and an NTS note, reporting `status: fallback`.

The rule that catches it is the ADR 0018 arrangement gate's: a candidate may not INTRODUCE a
fault the incumbent did not have.
"""

from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos, Rot

import draftwright.builder as builder_mod
from draftwright import Sheet, build_drawing
from draftwright.linting import LintIssue


def _authored_sheet(n: int = 6):
    """`n` authored dimensions with long labels on a small box — crowded enough to drop."""
    sheet = Sheet(Box(40, 30, 20), title="authored").authored_dimensions()
    for index in range(n):
        sheet.measured_dimension(
            kind="linear",
            value=20,
            label=f"20 SPEC-{index} NOTE XXXXXXXXXXXXXXXXXXXXXXXX",
            dominant_axis="X",
            ref_pts=[(-10, -15 - 3 * index, 0), (10, -15 - 3 * index, 0)],
            source_id=f"d:{index}",
        )
    return sheet.build()


class TestTheFallbackMayNotIntroduceAFault:
    def test_an_authored_sheet_is_not_shrunk_until_its_dimensions_degenerate(self):
        drawing = _authored_sheet()
        # The precondition: this fixture really does lose something at its natural scale, so
        # the completeness pass runs at all rather than returning early.
        assert drawing.scale_decision["status"] == "incomplete"
        # And the answer is the natural scale with the loss REPORTED, not a blank smaller one.
        assert drawing.scale == 2.0
        assert "plan_incomplete" in {i.code for i in drawing.lint() if i.severity == "error"}

    def test_the_returned_sheet_still_carries_dimensions(self):
        # The shape of the regression, stated directly: it returned a drawing whose only
        # annotations were the title block and the NTS caption.
        drawing = _authored_sheet()
        furniture = {"title_block", "note_iso_nts"}
        assert set(drawing.annotations()) - furniture, "the sheet came back with no content"

    def test_a_degenerate_authored_dim_never_reads_as_complete(self):
        # `authored_dim_degenerate` is an error the incumbent does not have, so a candidate
        # that produces it is refused. Without the introduced-fault rule this code was the
        # signature of the accepted 1:10 sheet.
        drawing = _authored_sheet()
        assert "authored_dim_degenerate" not in {i.code for i in drawing.lint()}


class TestTheSearchStopsWhenShrinkingStopsHelping:
    @staticmethod
    def _assemblies(part, **kwargs):
        import draftwright.builder as builder_mod

        count = 0
        real = builder_mod._assemble

        def counting(*args, **inner):
            nonlocal count
            count += 1
            return real(*args, **inner)

        builder_mod._assemble = counting
        try:
            build_drawing(part, **kwargs)
        finally:
            builder_mod._assemble = real
        return count

    def test_a_clean_part_never_searches(self):
        assert self._assemblies(Box(80, 60, 25)) == 1

    def test_a_part_whose_loss_no_scale_fixes_stops_early(self):
        # The review measured 86 such parts running the ladder 5-15 deep, 53 of them to the
        # rendering floor at scales like 1:500, to learn nothing. The blocker set has to
        # SHRINK for the search to continue.
        side_drilled = (
            Box(80, 40, 30)
            - Pos(-20, 0, 5) * Rot(90, 0, 0) * Cylinder(2.5, 50)
            - Pos(25, 0, -5) * Rot(90, 0, 0) * Cylinder(4, 50)
        )
        # This one DOES improve on the first candidate, so it builds exactly twice.
        assert self._assemblies(side_drilled) == 2


# --- the search rules, driven directly ------------------------------------------------
#
# Two of these could not be pinned by a fixture. A part whose incumbent ALREADY errors and
# whose smaller scale keeps that same error does not occur in the corpus, and neither does one
# that both departs from the preferred arrangement AND falls back on scale. Stubs stand in for
# the builds because what is under test is the ACCEPTANCE RULE, not the compiler.


class _FakeRegistry:
    def __init__(self):
        self.issues = []

    def record_issue(self, issue):
        self.issues.append(issue)

    def measurement_of(self, _name):
        return ()


def _fake(scale, *, errors=(), blockers=(), arrangement=None):
    """A stand-in drawing. `blockers` are `*_dropped` codes, `errors` are error-severity codes."""
    issues = [
        LintIssue(severity="warning", message=f"{code} here", code=code) for code in blockers
    ] + [LintIssue(severity="error", message=f"{code} here", code=code) for code in errors]
    drawing = SimpleNamespace(
        scale=scale,
        registry=_FakeRegistry(),
        annotations=lambda: (),
        lint=lambda **_kw: issues,
        scale_decision={"status": "automatic"},
        arrangement_decision=arrangement
        or {"chosen": "columns", "attempts": ({"arrangement": "columns", "status": "chosen"},)},
    )
    return drawing


class TestTheAcceptanceRule:
    def test_a_candidate_repeating_the_incumbents_error_is_still_acceptable(self):
        # `introduced` is a DIFFERENCE against the incumbent, not a bare "has errors". Without
        # the subtraction a part that already reports an error could never fall back at all,
        # however much the fallback improved. No corpus fixture exercises this, which is why
        # it is driven here.
        incumbent = _fake(
            1.0, errors=("leader_crosses_silhouette",), blockers=("slot_dim_dropped",)
        )
        better = _fake(0.5, errors=("leader_crosses_silhouette",))
        out = builder_mod._complete_automatic_plan(incumbent, lambda _sc: better)
        assert out is better
        assert out.scale_decision["status"] == "fallback"

    def test_a_candidate_adding_an_error_is_refused(self):
        incumbent = _fake(1.0, blockers=("slot_dim_dropped",))
        degenerate = _fake(0.5, errors=("authored_dim_degenerate",))
        out = builder_mod._complete_automatic_plan(incumbent, lambda _sc: degenerate)
        assert out is incumbent
        assert out.scale_decision["status"] == "incomplete"
        assert [i.code for i in incumbent.registry.issues] == ["plan_incomplete"]

    def test_a_candidate_that_does_not_shrink_the_loss_stops_the_search(self):
        builds = []

        def build(scale):
            builds.append(scale)
            return _fake(scale, blockers=("slot_dim_dropped",))

        incumbent = _fake(1.0, blockers=("slot_dim_dropped",))
        out = builder_mod._complete_automatic_plan(incumbent, build)
        assert out is incumbent
        assert len(builds) == 1, f"the ladder kept going: {builds}"
        assert [a["status"] for a in out.scale_decision["attempts"]] == [
            "incomplete",
            "no_improvement",
        ]

    def test_the_fallback_keeps_the_arrangement_record(self):
        # ADR 0018 §6: a rejected alternative arrangement is a first-class result. Returning
        # the fallback dropped it, because a fresh build carries the constructor default.
        rejected = {
            "chosen": "columns",
            "attempts": (
                {"arrangement": "stacked-iso", "status": "rejected", "blockers": ({"code": "x"},)},
                {"arrangement": "columns", "status": "chosen", "blockers": ()},
            ),
        }
        incumbent = _fake(1.0, blockers=("slot_dim_dropped",), arrangement=rejected)
        better = _fake(0.5)
        out = builder_mod._complete_automatic_plan(incumbent, lambda _sc: better)
        assert out is better
        assert out.arrangement_decision == rejected, "the rejection vanished with the fallback"


class TestTheSearchTrailIsRecorded:
    @pytest.mark.slow
    def test_the_case_study_stops_at_the_first_unhelpful_candidate(self):
        from test_issue_1130_view_planning_evidence import thin_rotational_plate

        drawing = build_drawing(thin_rotational_plate())
        assert [(a["scale"], a["status"]) for a in drawing.scale_decision["attempts"]] == [
            (1.0, "incomplete"),
            (0.5, "no_improvement"),
        ]
