"""Prismatic boss-diameter recognition and annotation behavior."""

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing


class TestPrismaticBossDiameter:
    """#629: a boss on a PRISMATIC part gets its ø as a plan-view leader to the boss
    circle (names ``m_bossdia_*``), free to exit into clear margin — not the turned
    column-left strip (``m_dia_z``), which strands the ø when that narrow strip is
    tight even on a half-empty sheet. Turned parts keep the OD-stack column."""

    @staticmethod
    def _box_boss():
        return Box(90, 64, 38) + Pos(0, 0, 24) * Cylinder(14, 10)

    @staticmethod
    def _shelled_cover():
        # The #629 report: a shelled cover whose front view hugs the left margin, so
        # the column-left ø strip has no room and the boss ø28 was dropped.
        return (
            Box(90, 64, 38)
            - Pos(0, 0, -3) * Box(84, 58, 38)
            + Pos(0, 0, 24) * Cylinder(14, 10)
            - Pos(0, 0, 15) * Cylinder(6, 40)
        )

    def test_prismatic_boss_diameter_is_a_plan_leader(self):
        dwg = build_drawing(self._box_boss())
        # the boss ø routes to the plan-view leader path, not the turned column
        assert any(n.startswith("m_bossdia_") for n in dwg.annotations())
        boss = next(o for n, o in dwg.iter_annotations() if n.startswith("m_bossdia_"))
        assert boss.label == "ø28"
        # and it is not ALSO emitted by the turned column (no double-dimensioning)
        assert not any(
            o.label == "ø28" for n, o in dwg.iter_annotations() if n.startswith("m_dia_z")
        )

    def test_prismatic_boss_height_is_dimensioned_in_profile(self):
        # #632: the boss's own 10 mm axial extent is independent of the 48 mm
        # overall envelope and must be modeled and rendered explicitly.
        dwg = build_drawing(self._box_boss())
        heights = [(n, o) for n, o in dwg.iter_annotations() if n.startswith("m_bossheight_")]
        assert len(heights) == 1
        name, dim = heights[0]
        assert dim.label == "10"
        assert dwg.view_of(name) == "front"
        assert not any(i.code == "boss_height_missing" for i in dwg.lint())

    def test_removed_boss_height_is_linted_from_live_drawing(self):
        # Coverage is drawing-derived: deleting the rendered height must expose the
        # gap even though the overall envelope and boss diameter remain present.
        dwg = build_drawing(self._box_boss())
        height_name = next(n for n in dwg.annotations() if n.startswith("m_bossheight_"))
        dwg.remove(height_name)
        issues = [i for i in dwg.lint() if i.code == "boss_height_missing"]
        assert len(issues) == 1
        assert issues[0].severity == "warning"

    def test_unplaceable_boss_height_reports_one_authoritative_issue(self):
        # Adversarial review of #632: a physically absent corridor forces the
        # candidate's drop path. Reconciliation must report the omission once,
        # rather than adding boss_height_dropped + boss_height_missing for one gap.
        from types import SimpleNamespace

        from draftwright.annotations._common import PlacementContext, drain_corridors
        from draftwright.annotations.from_model import render_boss_heights
        from draftwright.model import plan_dimensions
        from draftwright.model.compiled import compile_dimensions

        dwg = build_drawing(self._box_boss(), auto_dims=False)
        analysis = dwg._analysis
        constrained = SimpleNamespace(
            **{
                **vars(analysis),
                "fv_zones": SimpleNamespace(**{**vars(analysis.fv_zones), "right": None}),
            }
        )
        ctx = PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items)
        groups = plan_dimensions(dwg.model())
        render_boss_heights(
            dwg, compile_dimensions(dwg.model(), groups=groups), constrained, ctx=ctx
        )
        drain_corridors(ctx, dwg)

        boss_issues = [i for i in dwg.lint() if i.code.startswith("boss_height_")]
        assert [i.code for i in boss_issues] == ["boss_height_missing"]

    def test_declared_boss_object_carries_height_into_the_model(self):
        from draftwright import Sheet

        boss_obj = Pos(0, 0, 24) * Cylinder(14, 10)
        sheet = Sheet.from_part(Box(90, 64, 38) + boss_obj)
        sheet.boss(boss_obj)
        feature = next(f for f in sheet.build().model().features if f.kind == "boss")
        assert feature.height == pytest.approx(10)
        assert feature.span is not None

    def test_issue_632_declarative_cover_reconciles_rendered_boss_height(self):
        """The original #632 repro stays covered end-to-end: declaration → IR →
        planner → renderer → lint, including its shell, bore, and pocket declarations."""
        from draftwright import Sheet

        wall, length, width, height = 3.0, 90, 64, 38
        pocket = Pos(0, 0, -wall) * Box(length - 2 * wall, width - 2 * wall, height)
        boss = Pos(0, 0, height / 2 + 5) * Cylinder(14, 10)
        bore = Pos(0, 0, 15) * Cylinder(6, 40)
        cover = Box(length, width, height) - pocket + boss - bore

        sheet = Sheet(cover, title="C").auto_dimensions()
        sheet.envelope()
        sheet.boss(boss)
        sheet.hole(bore).through()
        sheet.pocket(pocket)
        dwg = sheet.build()

        height_name = next(n for n in dwg.annotations() if n.startswith("m_bossheight_"))
        assert dwg.get_annotation(height_name).label == "10"
        assert dwg.lint_summary()["by_code"].get("boss_height_missing", 0) == 0

        dwg.remove(height_name)
        assert dwg.lint_summary()["by_code"]["boss_height_missing"] == 1

    def test_issue_631_step_on_boss_is_reported(self):
        # #1132: this reports instead of raising. `generate_sheet_script` settles its
        # layout through the same predicate, so raising meant a part whose recognised
        # profile does not tile produced no script and no drawing at all. A generated
        # script is indistinguishable from hand-written code when re-run, so there is
        # no provenance to branch on. #631's substance is preserved: the misuse is
        # still detected and still named — loudly in lint rather than as an exception.
        # #631: reaching for .step(boss) declared a z-turned segment at the boss cylinder,
        # which flipped the height ladder into turned-suppression and silently dropped the
        # overall height (net −1 annotation, no height dim). The wrong-verb misuse must fail
        # loudly instead — .boss() is the verb for a boss. Covers both the lone .step(boss)
        # and the .boss(boss)+.step(boss) collision (both z-oriented on a prismatic body).
        from draftwright import Sheet

        boss = Pos(0, 0, 24) * Cylinder(14, 10)
        part = Box(90, 64, 38) + boss
        for extra_boss in (False, True):
            sheet = Sheet(part, title="C").auto_dimensions()
            sheet.envelope()
            if extra_boss:
                sheet.boss(boss)
            sheet.step(boss)
            codes = sheet.build().lint_summary()["by_code"]
            assert codes.get("turned_profile_not_spanned") == 1

    def test_issue_631_stepped_boss_on_plate_is_reported(self):
        # #1132: this reports instead of raising. `generate_sheet_script` settles its
        # layout through the same predicate, so raising meant a part whose recognised
        # profile does not tile produced no script and no drawing at all. A generated
        # script is indistinguishable from hand-written code when re-run, so there is
        # no provenance to branch on. #631's substance is preserved: the misuse is
        # still detected and still named — loudly in lint rather than as an exception.
        # The guard keys on the exact suppression premise (do the steps span the full
        # height?), not a rotational classifier — so even a stepped boss whose two stacked
        # cylinders read as a turned PROFILE, sat on a square plate, is caught: the steps
        # cover only the boss, not the plate below, so the overall height would be dropped.
        from draftwright import Sheet

        # Exact face contact makes this one physical solid: the old 25/35 placements left a
        # 10 mm air gap above the plate and accidentally tested a valid multi-solid compound.
        lower = Pos(0, 0, 15) * Cylinder(35, 10)
        upper = Pos(0, 0, 25) * Cylinder(25, 10)
        part = Box(100, 100, 20) + lower + upper
        sheet = Sheet(part, title="C").auto_dimensions()
        sheet.envelope()
        sheet.step(lower)
        sheet.step(upper)
        codes = sheet.build().lint_summary()["by_code"]
        assert codes.get("turned_profile_not_spanned") == 1

    def test_issue_631_interior_gap_between_steps_is_reported(self):
        # #1132: this reports instead of raising. `generate_sheet_script` settles its
        # layout through the same predicate, so raising meant a part whose recognised
        # profile does not tile produced no script and no drawing at all. A generated
        # script is indistinguishable from hand-written code when re-run, so there is
        # no provenance to branch on. #631's substance is preserved: the misuse is
        # still detected and still named — loudly in lint rather than as an exception.
        # Coverage is a union tiling, not a reach-to-each-end check: two z-steps that touch
        # both ends of a z=[0,40] body but leave a 15..25 interior gap do NOT convey the
        # full height (the gap length is unmeasured), so this must still raise.
        from draftwright.model.declare import step

        part = Pos(0, 0, 20) * Cylinder(10, 40)  # z-extent [0, 40]
        model = [
            step(diameter=20, length=15, at=(0, 0, 7.5), axis="z"),  # z [0, 15]
            step(diameter=20, length=15, at=(0, 0, 32.5), axis="z"),  # z [25, 40]
        ]
        codes = build_drawing(part, model=model, number="X").lint_summary()["by_code"]
        assert codes.get("turned_profile_not_spanned") == 1

    def test_shelled_cover_boss_diameter_not_dropped(self):
        # The regression: even forced onto A4 (scale 0.5, front view against the left
        # margin) the boss ø28 places into the clear sheet, so it never lints uncovered.
        dwg = build_drawing(self._shelled_cover(), page="A4")
        assert any(
            o.label == "ø28" for _, o in dwg.iter_annotations() if getattr(o, "label", None)
        )
        assert dwg.lint_summary()["by_code"].get("feature_not_dimensioned", 0) == 0

    def test_boss_diameter_carries_authored_tolerance(self):
        # The pass must consume the planner's DimParameter (value + tolerance/fit), not raw
        # geometry — formatting b.diameter directly dropped an authored ⌀ tolerance, and then
        # blocked render_diameters via `mentioned`, losing it silently (gpt-5.6-sol review).
        from draftwright import Sheet

        s = Sheet.from_part(Box(90, 64, 38) + Pos(0, 0, 24) * Cylinder(14, 10))
        s.of(Pos(0, 0, 24) * Cylinder(14, 10)).tolerance(0.0, 0.1)  # tolerance the boss ⌀28
        dwg = s.build()
        labels = [o.label for n, o in dwg.iter_annotations() if n.startswith("m_bossdia_")]
        assert labels and any("0.1" in str(lbl) for lbl in labels), labels

    def test_turned_part_boss_stays_in_the_column(self):
        # A rotational shaft's step/OD diameters keep the m_dia column — render_boss_diameters
        # is a prismatic-only pass and must not fire on a turned body.
        dwg = build_drawing(Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(10, 30))
        assert not any(n.startswith("m_bossdia_") for n in dwg.annotations())
        assert any(n.startswith("m_dia_z") for n in dwg.annotations())
        assert not any(i.code == "boss_height_missing" for i in dwg.lint())
