"""Lint summaries, dropped annotations, and incomplete-plan reporting."""

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.linting import LintIssue


class TestLintSummaryAndDrops:
    def test_summary_shape_is_consistent_with_lint(self):
        dwg = build_drawing(Box(80, 60, 20) - Cylinder(5, 20))
        issues = dwg.lint()
        s = dwg.lint_summary()

        assert set(s) == {
            "passed",
            "score",
            "diagnostic_score",
            "quality",
            "review",
            "errors",
            "warnings",
            "infos",
            "by_code",
            "geometry_issues",
            "issues",
        }
        assert s["errors"] + s["warnings"] + s["infos"] == len(issues)
        assert s["passed"] is (s["errors"] == 0)
        assert 0.0 <= s["score"] <= 1.0
        assert s["diagnostic_score"] == s["score"]
        # `fidelity` joined in #1176: completeness asks whether required content landed,
        # restraint whether there is too much of it, legibility whether a reader can make
        # it out, and fidelity whether what it says is TRUE. A drawing can pass the first
        # three and still assert a measurement the part does not have.
        # `unscored` is not a fifth axis — it is the inventory of findings that reached
        # NONE of the four, reported for the same reason completeness reports `excludes`
        # (#1176 review r4): four components all saying "fine" would otherwise conceal that
        # some of this drawing's findings were scored by nothing at all.
        assert set(s["quality"]) == {
            "completeness",
            "restraint",
            "legibility",
            "fidelity",
            "unscored",
        }
        completeness = s["quality"]["completeness"]
        assert completeness["available"] is True
        assert completeness["audited_score"] == 1.0
        assert completeness["coverage"] == "partial"
        assert completeness["scope"] == "audited_recognized_requirements"
        assert completeness["unscored_recognized_families"] == []
        assert completeness["reason"] == (
            "audited_score covers recognized requirements in audited families only; it is "
            "not evidence that the drawing is complete"
        )
        assert completeness["requirements"] == completeness["placed"] == 4
        assert completeness["by_family"]["holes"] == 4
        assert sum(s["by_code"].values()) == len(issues)
        assert len(s["issues"]) == len(issues)
        # A single-hole plate doesn't overflow the per-view callout cap.
        assert "callout_dropped" not in s["by_code"]

    def test_quality_components_do_not_mix_semantic_and_layout_diagnostics(self):

        dwg = build_drawing(Box(60, 40, 30))
        dwg.registry.record_issue(
            LintIssue(severity="warning", code="callout_dropped", message="layout")
        )
        dwg.registry.record_issue(
            LintIssue(severity="warning", code="feature_not_dimensioned", message="completeness")
        )
        dwg.registry.record_issue(
            LintIssue(severity="info", code="slot_dim_dropped", message="info placement drop")
        )
        dwg.registry.record_issue(
            LintIssue(
                severity="warning",
                code="gdt_dropped",
                message="invalid characteristic",
                outcome_stage="validation",
            )
        )

        summary = dwg.lint_summary()
        legibility = summary["quality"]["legibility"]
        assert summary["score"] == summary["diagnostic_score"] == 0.85
        assert legibility == {
            "available": True,
            "score": 0.9,
            "errors": 0,
            "warnings": 1,
            "infos": 1,
            "placement_drops": 2,
            "by_code": {"callout_dropped": 1, "slot_dim_dropped": 1},
            "raw_issues": 2,
            "primary_issues": 2,
            "primary_errors": 0,
            "primary_warnings": 1,
            "primary_infos": 1,
            "primary_by_code": {"callout_dropped": 1, "slot_dim_dropped": 1},
            "affected_pairs": 0,
            "basis": "layout_issue_severity_with_info_floor",
            "score_inventory": "primary_issues",
        }
        assert summary["quality"]["restraint"] == {
            "available": False,
            "score": None,
            "reason": (
                "measurement provenance and physical requirement equivalence are incomplete"
            ),
        }

    def test_recorded_build_issue_surfaces_and_counts(self):

        dwg = build_drawing(Box(60, 40, 30))
        before = dwg.lint_summary()
        dwg.registry.record_issue(
            LintIssue(severity="warning", code="callout_dropped", message="synthetic drop")
        )

        codes = {i.code for i in dwg.lint()}
        assert "callout_dropped" in codes

        after = dwg.lint_summary()
        assert after["warnings"] == before["warnings"] + 1
        assert after["by_code"]["callout_dropped"] == 1
        # callout_dropped is a geometry-aware code, so it lifts that count too.
        assert after["geometry_issues"] == before["geometry_issues"] + 1

    def test_dropped_callout_diameter_excluded_from_feature_lint(self):
        # The de-dup contract: a diameter recorded as a dropped callout is
        # excluded from feature_not_dimensioned, so a callout the layout could
        # not place (#36) is surfaced once (as callout_dropped) and not
        # double-reported.

        from draftwright.make_drawing import lint_feature_coverage

        part = Box(60, 40, 20) - Cylinder(5, 20)  # one undimensioned ø10 bore
        base = lint_feature_coverage(part, [])
        assert any(i.code == "feature_not_dimensioned" for i in base)
        excluded = lint_feature_coverage(part, [], exclude=[10.0])
        assert not any(i.code == "feature_not_dimensioned" for i in excluded)

    @pytest.mark.timeout(120)
    def test_step_dims_are_adaptive_not_capped(self):
        # #36: no fixed 3-step cap; #45: five equal ledges form a uniform
        # staircase → one representative dim_step_typ labelled "N× rise",
        # no error-severity lint.

        tower = Box(120, 120, 15)
        for i in range(1, 6):
            side = 120 - i * 18
            tower += Pos(0, 0, i * 15) * Box(side, side, 15)
        dwg = build_drawing(tower)
        assert "dim_step_typ" in dwg.annotations(), "uniform staircase should get a TYP dim"
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    def test_legible_steps_gate_drops_closely_spaced(self):
        # #41/#565: closely-spaced shoulders are dropped (surfaced via lint),
        # but a short first rise remains dimensionable with external arrows.
        from draftwright._core import (
            _MIN_STEP_DIM_MM,
            _MIN_STEP_SEP_MM,
            _legible_steps,
        )

        base = _MIN_STEP_DIM_MM + 5.0  # all comfortably tall enough from z=0
        zs = [base, base + 0.5, base + 1.0, base + _MIN_STEP_SEP_MM + 1.0]
        kept, n_too_close = _legible_steps(zs, 0.0, scale=1.0)
        assert kept == [base, base + _MIN_STEP_SEP_MM + 1.0]
        assert n_too_close == 2
        # A short first step survives; DimensionLine moves text/arrows outside.
        kept2, n2 = _legible_steps([1.0, base], 0.0, scale=1.0, allow_short=True)
        assert kept2 == [1.0, base]
        assert n2 == 0

    @pytest.mark.timeout(120)
    def test_location_dims_are_adaptive_not_capped(self):
        # #36: location dims have no fixed cap. Six scattered holes (distinct X
        # and Y, varied diameters so no array collapses them) get far more than
        # the old cap of four location dims, with nothing dropped — they fit.

        plate = Box(140, 90, 8)
        for x, y, r in [
            (-55, -35, 2.0),
            (-33, -12, 2.5),
            (-11, 15, 3.0),
            (12, -20, 3.5),
            (34, 28, 2.0),
            (55, 5, 2.5),
        ]:
            plate -= Pos(x, y, 0) * Cylinder(r, 8)
        dwg = build_drawing(plate)
        n_loc = len([n for n in dwg.annotations() if n.startswith(("m_locx", "m_locy"))])
        assert n_loc > 4, f"expected adaptive >4 location dims, got {n_loc}"
        assert "location_ref_dropped" not in {i.code for i in dwg.lint()}

    def test_legible_locations_gate_drops_closely_spaced(self):
        # #43: a location is dimensioned only if it is at least _MIN_LOC_SEP_MM
        # (page-mm) from the previously kept one; closer ones read as one busy
        # cluster and are dropped (surfaced via lint).
        from draftwright._core import _MIN_LOC_SEP_MM
        from draftwright.annotations.holes import _legible_locations

        sep = _MIN_LOC_SEP_MM
        positions = [0.0, 1.0, 2.0, sep + 2.0, sep + 2.5, 2 * sep + 5.0]
        kept, n_too_close = _legible_locations(positions, scale=1.0)
        assert kept == [0.0, sep + 2.0, 2 * sep + 5.0]
        assert n_too_close == 3
        # At a larger scale the same world spacing reads fine — nothing dropped.
        kept2, n2 = _legible_locations([0.0, 1.0, 2.0], scale=10.0)
        assert kept2 == [0.0, 1.0, 2.0]
        assert n2 == 0

    @pytest.mark.timeout(120)
    def test_location_tower_trimmed_to_legible_set(self):
        # #43: many unpatterned holes with near-coincident X/Y positions trim to
        # a legible set; the rest surface as location_ref_dropped.
        #
        # That drop is a REQUIRED placement failure, so since #1250 it also carries a
        # `plan_incomplete` error — this test previously asserted "no error lint", which was
        # the very combination #1250 names: a required annotation dropped and the drawing
        # reporting success. The subject here is that the tower trims to a legible set, and
        # that is unchanged; what changed is that the loss is no longer silent.

        plate = Box(80, 60, 8)
        pts = [
            (-30, -20),
            (-28, 18),
            (-26, -5),
            (-10, 22),
            (-8, -22),
            (6, 10),
            (9, -15),
            (24, 20),
            (27, -8),
            (30, 4),
        ]
        for x, y in pts:
            plate -= Pos(x, y, 0) * Cylinder(1.2, 8)
        dwg = build_drawing(plate)
        codes = {i.code for i in dwg.lint()}
        n_locx = len([n for n in dwg.annotations() if n.startswith("m_locx")])
        n_locy = len([n for n in dwg.annotations() if n.startswith("m_locy")])
        assert "location_ref_dropped" in codes  # closely-spaced refs were trimmed
        assert {i.code for i in dwg.lint() if i.severity == "error"} == {"plan_incomplete"}
        # The kept set is strictly fewer than the ten holes per axis.
        assert 0 < n_locx < 10
        assert 0 < n_locy < 10

    @pytest.mark.timeout(120)
    def test_short_location_does_not_displace_its_legible_neighbour(self):
        # A nonzero 0.7 mm location is too short to draw; report it honestly without
        # anchoring the spacing cluster on it and dropping its legible neighbour.

        part = Box(80, 60, 20)
        part -= Pos(-39.3, 0, 0) * Cylinder(0.4, 20)  # ~0.7 mm from datum_x: skipped
        part -= Pos(-36.5, 0, 0) * Cylinder(1.5, 20)  # ~2.8 mm from the edge hole
        # A4 at 1:1 pinned, because that is where the 0.7 mm location is too short to draw
        # and this test is about what the gate does then. Left automatic, #1590 now escalates
        # this part to A2 at 2:1 — the overall depth was withheld here, and the larger sheet
        # places it, locates the second hole, AND makes the 0.7 mm location drawable, so the
        # skip under test stops happening. `permissive` because the A4 plan is incomplete;
        # that is the premise, not a surprise.
        dwg = build_drawing(part, page="A4", scale=1.0, scale_policy="permissive")
        # The real neighbour is dimensioned...
        assert any(n.startswith("m_locx") for n in dwg.annotations())
        # ...and the gate did not record a spurious X spacing drop.
        x_spacing_drops = [
            i for i in dwg.lint() if i.code == "location_ref_dropped" and "X location" in i.message
        ]
        assert len(x_spacing_drops) == 1
        issue = x_spacing_drops[0]
        assert "less than 1 mm" in issue.message
        assert issue.measurement_ids
        assert all(abs(mid.feature.frame.origin[0] + 39.3) < 1e-6 for mid in issue.measurement_ids)

    @pytest.mark.timeout(120)
    def test_auto_annotate_clears_stale_build_issues(self):
        # Re-annotating starts build-time lint tracking from a clean slate:
        # stale drop records from a prior pass are cleared, not accumulated.
        # (A full second pass is not idempotent — strip cursors advance — but
        # the records always reflect only the latest pass.)

        from draftwright.annotate import _auto_annotate

        dwg = build_drawing(Box(60, 40, 30))
        dwg.registry.record_issue(
            LintIssue(severity="warning", code="callout_dropped", message="stale")
        )
        assert any(i.message == "stale" for i in dwg.registry.issues)
        _auto_annotate(dwg, dwg._analysis)
        assert not any(i.message == "stale" for i in dwg.registry.issues)
        assert dwg.coverage.dropped_diams == []

    def test_repeated_lint_is_stable(self):
        # lint()/lint_summary() are idempotent — repeated calls return the same
        # issues and never accumulate the build-time drop records.

        plate = Box(120, 60, 8)
        for x, r in zip((-48, -24, 0, 24, 48), (2.0, 2.5, 3.0, 3.5, 4.0)):
            plate -= Pos(x, 0, 0) * Cylinder(r, 8)
        dwg = build_drawing(plate)
        first, second = dwg.lint(), dwg.lint()
        assert len(first) == len(second)
        assert dwg.lint_summary()["by_code"] == dwg.lint_summary()["by_code"]

    def test_placement_unsatisfiable_is_error_severity(self):
        # placement_unsatisfiable (engine could not place a wanted annotation)
        # is error-severity, so it fails the `passed` gate.

        dwg = build_drawing(Box(60, 40, 30))
        assert dwg.lint_summary()["passed"] is True
        dwg.registry.record_issue(
            LintIssue(severity="error", code="placement_unsatisfiable", message="synthetic")
        )
        s = dwg.lint_summary()
        assert s["passed"] is False
        assert s["errors"] >= 1
        assert s["by_code"]["placement_unsatisfiable"] == 1
