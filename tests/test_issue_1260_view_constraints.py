"""ADR 0018 Phase 2: typed authored view requests and Sheet verbs (#1260)."""

from __future__ import annotations

import dataclasses
import warnings
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder

import draftwright.builder as builder_mod
import draftwright.projection as projection_mod
from draftwright import Sheet, SoftDeprecationWarning, ViewConstraints
from draftwright.analysis import _apply_principal_view_pins
from draftwright.builder import _validate_authored_view_layout
from draftwright.model import plan_sections
from draftwright.view_plan import (
    ConstraintSource,
    ResolvedViewPlan,
    ViewPin,
    ViewRelation,
    ViewSpec,
)


def _sheet(part=None):
    return Sheet(part or Box(50, 30, 10)).authored_dimensions()


class TestTypedRequestState:
    def test_constraints_are_immutable_and_not_a_resolved_plan(self):
        sheet = _sheet()
        sheet.authored_views().view("front")

        constraints = sheet.view_constraints
        assert isinstance(constraints, ViewConstraints)
        assert not isinstance(constraints, ResolvedViewPlan)
        assert constraints.principal_source == "authored"
        assert [item.spec.name for item in constraints.principals] == ["front"]
        with pytest.raises(dataclasses.FrozenInstanceError):
            constraints.principal_source = "automatic"  # type: ignore[misc]

    def test_every_principal_verb_changes_the_corresponding_request(self):
        authored = _sheet()
        front = authored.view("front")
        authored.view("iso")
        assert front.name == "front"
        assert [item.spec.name for item in authored.view_constraints.principals] == [
            "front",
            "iso",
        ]

        automatic = _sheet()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SoftDeprecationWarning)
            automatic.auto_views()
        automatic.add_view("side")
        assert automatic.view_constraints.principal_source == "automatic"
        assert [item.spec.name for item in automatic.view_constraints.added_principals] == ["side"]

    def test_derived_verbs_retain_semantic_targets_and_scale(self):
        sheet = _sheet()
        hole = sheet.hole(diameter=6, at=(0, 0, 0), axis="z")
        section = sheet.section_view("A", through=hole)
        detail = sheet.detail_view("B", around=hole).scale(2)

        constraints = sheet.view_constraints
        assert section.name == "section_aa"
        assert detail.name == "detail_b"
        assert [item.spec.kind for item in constraints.derived] == ["section", "detail"]
        assert constraints.derived[0].spec.target[0] == "feature"
        assert constraints.derived[1].spec.scale_factor == 2

    def test_relations_rows_columns_and_pins_are_whole_view_constraints(self):
        sheet = _sheet()
        front = sheet.view("front")
        plan = sheet.view("plan")
        side = sheet.view("side")
        plan.above(front, gap=3).align_x(front)
        sheet.row(front, side, gap=4)
        sheet.column(front, plan)
        side.pin((120, 80))

        constraints = sheet.view_constraints
        assert [item.relation for item in constraints.relations] == [
            "above",
            "align_x",
            "right_of",
            "above",
        ]
        assert constraints.pins[0].view == "side"
        assert constraints.pins[0].at == (120.0, 80.0)


class TestSourceCoherence:
    def test_unknown_view_fails_at_the_verb(self):
        with pytest.raises(ValueError, match="expected one of"):
            _sheet().view("elevation")

    def test_principal_independent_scale_fails_at_the_handle(self):
        with pytest.raises(ValueError, match="principal.*independent scale"):
            _sheet().view("front").scale(2)

    def test_authored_views_then_auto_dimensions_fails_at_the_second_verb(self):
        sheet = Sheet(Box(30, 20, 10))
        sheet.view("front")
        with pytest.raises(ValueError, match="auto_dimensions.*authored views"):
            sheet.auto_dimensions()

    def test_auto_dimensions_then_authored_views_fails_at_the_second_verb(self):
        sheet = Sheet(Box(30, 20, 10))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SoftDeprecationWarning)
            sheet.auto_dimensions()
        with pytest.raises(ValueError, match="authors the view set"):
            sheet.view("front")

    def test_from_part_implicit_automatic_dimensions_also_refuse_authored_views(self):
        sheet = Sheet.from_part(Box(30, 20, 10))
        with pytest.raises(ValueError, match="authors the view set"):
            sheet.view("front")

    def test_add_forms_require_auto_views_at_build(self):
        sheet = _sheet()
        sheet.add_view("side")
        with pytest.raises(ValueError, match=r"call auto_views\(\) first"):
            sheet.build()

    def test_source_location_reaches_an_infeasibility(self):
        sheet = _sheet()
        sheet.view("front").above("plan")
        sheet.view("plan")
        with pytest.raises(ValueError) as caught:
            sheet.build()
        assert __file__ in str(caught.value)

    def test_handle_and_value_validation_is_fail_closed(self):
        sheet = _sheet()
        front, plan = sheet.view("front"), sheet.view("plan")
        front.left_of(plan).right_of(plan).below(plan).align_y(plan)

        with pytest.raises(ValueError, match="two page coordinates"):
            front.pin((1,))
        with pytest.raises(ValueError, match="finite and positive"):
            sheet.view("iso").scale(float("nan"))
        with pytest.raises(ValueError, match="one alphanumeric"):
            sheet.section_view("A-A", at=0)
        with pytest.raises(ValueError, match="more than once"):
            sheet.view("front")
        with pytest.raises(ValueError, match="relative to itself"):
            ViewRelation("front", "above", "front")
        with pytest.raises(ValueError, match="unknown view relation"):
            ViewRelation("front", "diagonal", "plan")
        with pytest.raises(ValueError, match="non-negative"):
            ViewRelation("front", "above", "plan", gap=-1)
        with pytest.raises(ValueError, match="finite page coordinates"):
            ViewPin("front", (float("inf"), 0))
        with pytest.raises(ValueError, match="finite and positive"):
            ViewSpec("iso", "pictorial", scale_factor=0)
        with pytest.raises(ValueError, match="only valid"):
            ViewSpec("front", "principal", scale_factor=2)
        with pytest.raises(ValueError, match="principal_source"):
            ViewConstraints(principal_source="guess")
        assert ViewConstraints().is_empty
        assert not ViewConstraints(principal_source="automatic").is_empty

    def test_view_source_and_target_conflicts_fail_at_the_second_verb(self):
        automatic = _sheet()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SoftDeprecationWarning)
            automatic.auto_views()
        with pytest.raises(ValueError, match="one principal-view source"):
            automatic.authored_views()
        with pytest.raises(ValueError, match="cannot follow auto_views"):
            automatic.view("front")
        with pytest.raises(ValueError, match="cannot follow auto_views"):
            automatic.section_view("A", at=0)
        with pytest.raises(ValueError, match="cannot follow auto_views"):
            automatic.detail_view("A", object())

        authored = _sheet()
        authored.view("front")
        with pytest.raises(ValueError, match="cannot be combined"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SoftDeprecationWarning)
                authored.auto_views()
        with pytest.raises(ValueError, match="augments auto_views"):
            authored.add_view("side")

        derived = _sheet()
        derived.section_view("A", at=0)
        with pytest.raises(ValueError, match="augments auto_views"):
            derived.add_section_view("B", at=1)
        with pytest.raises(ValueError, match="augments auto_views"):
            derived.add_detail_view("B", object())

        for call in (
            lambda: _sheet().section_view("A"),
            lambda: _sheet().section_view("A", object(), at=0),
            lambda: _sheet().section_view("A", at=float("inf")),
            lambda: _sheet().detail_view("A", object()),
        ):
            with pytest.raises(ValueError):
                call()

    def test_row_column_and_build_source_refusals_are_explicit(self):
        with pytest.raises(ValueError, match="at least two"):
            _sheet().row("front")
        with pytest.raises(ValueError, match="at least two"):
            _sheet().column("front")
        with pytest.raises(ValueError, match="no principal orthographic"):
            _sheet().authored_views().build()
        only_iso = _sheet()
        only_iso.view("iso")
        with pytest.raises(ValueError, match="no principal orthographic"):
            only_iso.build()
        added = _sheet()
        added.add_detail_view("A", added.hole(diameter=4, at=(0, 0, 0), axis="z"))
        with pytest.raises(ValueError, match=r"call auto_views\(\) first"):
            added.build()
        legacy = _sheet()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            legacy.section(at=0)
        legacy.section_view("A", at=0)
        with pytest.raises(ValueError, match="deprecated section"):
            legacy.build()


class TestBuildEffects:
    def test_no_constraints_preserve_the_automatic_four_view_sheet(self):
        drawing = _sheet().build()
        assert set(drawing.views) == {"front", "plan", "side", "iso"}
        assert not drawing.lint()

    def test_authored_omission_removes_views_and_replans_before_projection(self):
        sheet = _sheet()
        sheet.view("front")
        drawing = sheet.build()
        assert set(drawing.views) == {"front"}
        assert drawing.view_plan.principal_names == ("front",)
        assert drawing.view_plan.of_kind("pictorial") == ()

    def test_a_natural_relation_is_honoured(self):
        sheet = _sheet()
        front = sheet.view("front")
        plan = sheet.view("plan")
        plan.above(front).align_x(front)
        drawing = sheet.build()
        assert set(drawing.views) == {"front", "plan"}

    def test_named_sections_coexist_and_enter_the_resolved_plan(self):
        sheet = Sheet(Box(50, 30, 10), page="A2").authored_dimensions()
        sheet.section_view("A", at=-8)
        sheet.section_view("B", at=8)
        drawing = sheet.build()

        assert {"section_aa", "section_bb"} <= set(drawing.views)
        assert {spec.name for spec in drawing.view_plan.of_kind("section")} == {
            "section_aa",
            "section_bb",
        }
        assert not [issue for issue in drawing.lint() if issue.severity != "info"]

    def test_feature_section_targets_and_parent_rows_are_hard_constraints(self):
        through = _sheet()
        hole = through.hole(diameter=6, at=(0, 0, 0), axis="z")
        through.section_view("A", through=hole)
        assert "section_aa" in through.build().views

        outside = _sheet()
        remote_hole = outside.hole(diameter=6, at=(0, 1000, 0), axis="z")
        outside.section_view("A", through=remote_hole)
        with pytest.raises(ValueError, match="outside the part interior"):
            outside.build()

        parentless = _sheet()
        parentless.view("plan")
        parentless.section_view("A", at=0)
        with pytest.raises(ValueError, match="needs front or side as its parent row"):
            parentless.build()

    def test_a_feature_targeted_detail_uses_its_authored_name_and_scale(self):
        part = Box(50, 30, 10) - Cylinder(3, 20)
        sheet = Sheet(part, page="A3").authored_dimensions()
        hole = sheet.hole(diameter=6, at=(0, 0, 0), axis="z")
        sheet.dimension(hole, "bore.diameter")
        sheet.dimension(hole, "location")
        sheet.detail_view("B", around=hole).scale(2)
        drawing = sheet.build()

        assert "detail_b" in drawing.views
        detail = drawing.view_plan.spec("detail_b")
        assert detail is not None
        assert detail.kind == "detail"
        assert detail.scale_factor == 2
        assert not [issue for issue in drawing.lint() if issue.severity != "info"]

    def test_an_authored_iso_scale_is_rendered_exactly_and_never_auto_fitted(self, monkeypatch):
        projected_scales = []

        def project(drawing, analysis, scale, shape_s=None):
            projected_scales.append(scale)
            return projection_mod._project_iso(drawing, analysis, scale, shape_s=shape_s)

        def unexpected_fit(*_args, **_kwargs):
            raise AssertionError("an authored iso scale must not enter the automatic fit")

        monkeypatch.setattr(builder_mod, "_project_iso", project)
        monkeypatch.setattr(builder_mod, "_fit_iso_view", unexpected_fit)

        sheet = Sheet(Box(50, 30, 10), page="A2").authored_dimensions()
        sheet.view("front")
        sheet.view("iso").scale(1.25)
        drawing = sheet.build()

        front_origin = drawing.at("front", 0, 0, 0)
        front_x = drawing.at("front", 10, 0, 0)
        sheet_scale = abs(front_x[0] - front_origin[0]) / 10
        assert projected_scales == pytest.approx([sheet_scale * 1.25])
        assert drawing.view_plan.spec("iso").scale_factor == 1.25

    def test_a_pin_is_never_relaxed(self):
        baseline_sheet = _sheet()
        baseline_sheet.view("front")
        baseline = baseline_sheet.build()
        origin = baseline.at("front", 0, 0, 0)

        exact = _sheet()
        exact.view("front").pin(origin[:2])
        exact_drawing = exact.build()
        assert exact_drawing.at("front", 0, 0, 0)[:2] == pytest.approx(origin[:2])

        moved = _sheet()
        moved.view("front").pin((origin[0] + 1, origin[1]))
        moved_drawing = moved.build()
        assert moved_drawing.at("front", 0, 0, 0)[:2] == pytest.approx((origin[0] + 1, origin[1]))
        assert not moved_drawing.lint()

        impossible = _sheet()
        impossible.view("front").pin((-1000, origin[1]))
        with pytest.raises(ValueError, match="pin.*infeasible.*not relaxed"):
            impossible.build()

    def test_contradictory_and_nonprincipal_pins_are_refused(self):
        baseline_sheet = _sheet()
        baseline_sheet.view("front")
        baseline_sheet.view("plan")
        baseline = baseline_sheet.build()

        contradictory = _sheet()
        contradictory.view("front").pin(baseline.at("front", 0, 0, 0)[:2])
        plan_at = baseline.at("plan", 0, 0, 0)
        contradictory.view("plan").pin((plan_at[0] + 1, plan_at[1]))
        with pytest.raises(ValueError, match="contradict.*translations"):
            contradictory.build()

        nonprincipal = _sheet()
        nonprincipal.view("front")
        with pytest.raises(ValueError, match="principal front/plan/side views only"):
            nonprincipal.view("iso").pin((100, 100))

    def test_derived_views_require_their_semantic_parent_views(self):
        section = _sheet()
        section.view("front")
        section.section_view("A", at=0)
        with pytest.raises(ValueError, match="needs the plan view"):
            section.build()

        detail = _sheet()
        hole = detail.hole(diameter=6, at=(0, 0, 0), axis="z")
        detail.view("front")
        detail.detail_view("A", around=hole)
        with pytest.raises(ValueError, match="cannot be shown"):
            detail.build()

    def test_exact_view_scales_fail_instead_of_relaxing(self, monkeypatch):
        monkeypatch.setattr(builder_mod, "_bbox_within", lambda *_args, **_kwargs: False)
        iso = Sheet(Box(50, 30, 10), page="A2").authored_dimensions()
        iso.view("front")
        iso.view("iso").scale(1.25)
        with pytest.raises(ValueError, match="authored iso scale.*not reduced"):
            iso.build()

        detail = Sheet(Box(50, 30, 10), page="A4").authored_dimensions()
        hole = detail.hole(diameter=6, at=(0, 0, 0), axis="z")
        detail.detail_view("A", around=hole).scale(100)
        with pytest.raises(ValueError, match="authored detail.*not relaxed"):
            detail.build()


class TestDefensiveTypedBoundaries:
    def test_pin_lowering_rejects_unsupported_and_absent_views(self):
        def geometry():
            return SimpleNamespace(
                FV_X=40.0,
                FV_Y=40.0,
                PV_X=40.0,
                PV_Y=80.0,
                SV_X=80.0,
                SV_Y=40.0,
            )

        for source in (ConstraintSource("test.py", 1), None):
            constraints = ViewConstraints(pins=(ViewPin("iso", (10, 10), source),))
            with pytest.raises(ValueError, match="principal orthographic"):
                _apply_principal_view_pins(
                    geometry(),
                    constraints,
                    scale=1,
                    centre=(0, 0, 0),
                    page=(297, 210),
                    margin=10,
                    views=("front",),
                )

        absent = ViewConstraints(pins=(ViewPin("front", (10, 10)),))
        with pytest.raises(ValueError, match="absent view"):
            _apply_principal_view_pins(
                geometry(),
                absent,
                scale=1,
                centre=(0, 0, 0),
                page=(297, 210),
                margin=10,
                views=("plan",),
            )

    def test_resolved_layout_validation_rejects_absent_views_and_moved_pins(self):
        bounds = {"front": (20, 20, 40, 40), "plan": (20, 50, 40, 70)}
        drawing = SimpleNamespace(
            view_plan=SimpleNamespace(placements={}),
            views=bounds,
            view_bounds=bounds.__getitem__,
            at=lambda *_args: (1.0, 0.0, 0.0),
        )

        missing = ViewConstraints(relations=(ViewRelation("front", "above", "side"),))
        with pytest.raises(ValueError, match="absent view"):
            _validate_authored_view_layout(drawing, missing)

        absent_pin = ViewConstraints(pins=(ViewPin("side", (0, 0)),))
        with pytest.raises(ValueError, match="pin names absent view"):
            _validate_authored_view_layout(drawing, absent_pin)

        for source in (ConstraintSource("test.py", 2), None):
            moved = ViewConstraints(pins=(ViewPin("front", (0, 0), source),))
            with pytest.raises(ValueError, match="pin.*infeasible"):
                _validate_authored_view_layout(drawing, moved)

    def test_auto_section_suppression_reaches_the_planner_boundary(self):
        model = SimpleNamespace(decorations={"auto_sections": False}, features=[])
        assert plan_sections(model, set()) is None


class TestLegacyDerivedVerbs:
    @pytest.mark.parametrize("verb", ["section", "detail"])
    def test_legacy_verbs_warn_with_the_removal_target(self, verb):
        sheet = _sheet()
        with pytest.warns(DeprecationWarning, match="0.6.0"):
            getattr(sheet, verb)()
