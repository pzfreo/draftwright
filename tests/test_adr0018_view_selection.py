"""ADR 0018: building a CHOSEN set of principal views, and what it costs.

The ADR's headline. Its delivery gate names two evidence items this module answers:

- *"Removing a truly redundant view retains every requirement and reduces the selected
  footprint."*
- *"Removing a visually similar but semantically necessary view is rejected by an asymmetric
  counterexample."*

Three things had to be true before either could be asked. A view's space had to be reclaimed
when it is dropped, or a smaller view set costs its annotations and stays on the same paper.
An extent had to be able to move to another view, because the overall width was pinned to
plan and vanished with it. And the decision had to be carried through every rebuild.

Automatic selection now proposes the turned part's profile + end-view pair. `_views` remains
an engine seam for testing authored topologies; the automatic proposal must pass the same
pre-projection requirement check and finished-drawing loss gate exercised below.
"""

import math

import pytest
from build123d import Box, Cylinder, Pos, Rot

from draftwright import ViewPlanIncomplete, build_drawing
from draftwright.builder import detect_part_model
from draftwright.compose import _layout_geometry
from draftwright.view_plan import VIEW_AXES, VIEWS_SHOWING, views_showing

ALL_THREE = ("front", "plan", "side")


def _plain_box():
    return Box(90, 60, 25)


def _z_hole():
    """A part whose only feature reads face-on in PLAN — so plan is necessary."""
    return Box(90, 60, 25) - Pos(20, 15, 0) * Cylinder(4, 30)


@pytest.mark.parametrize(
    "part_fn",
    (_plain_box, _z_hole),
    ids=("plain-box", "z-hole"),
)
def test_direct_detection_matches_built_model_features_for_view_planning_parts(part_fn):
    built = tuple(build_drawing(part_fn()).model().features)
    detected = tuple(detect_part_model(part_fn()).features)
    assert detected == built


def _x_hole():
    """The asymmetric twin: same box, same one hole, drilled along X instead of Z.

    Visually similar and semantically opposite — here SIDE carries the feature and plan
    carries nothing that another view could not.
    """
    return Box(90, 60, 25) - Rot(0, 90, 0) * Cylinder(5, 120)


def _lint(drawing):
    return {issue.code for issue in drawing.lint()}


class TestTheObservabilityModel:
    """Which views COULD carry a requirement — the question droppability needs."""

    def test_every_view_lays_out_two_distinct_axes(self):
        for view, (horizontal, vertical) in VIEW_AXES.items():
            assert horizontal != vertical, view
            assert {horizontal, vertical} <= {"x", "y", "z"}

    def test_the_three_views_between_them_show_every_axis_twice(self):
        seen = [axis for axes in VIEW_AXES.values() for axis in axes]
        assert {axis: seen.count(axis) for axis in "xyz"} == {"x": 2, "y": 2, "z": 2}

    def test_views_showing_agrees_with_the_page_axes(self):
        # The derived map may not drift from the primitive it is derived from.
        for axis, views in VIEWS_SHOWING.items():
            assert set(views) == {v for v, axes in VIEW_AXES.items() if axis in axes}

    def test_the_horizontal_filter_excludes_a_view_that_shows_the_axis_vertically(self):
        # The distinction that matters for a below-strip extent dim: plan CONTAINS y, but
        # lays it out vertically, and dimensioning it there horizontally collapses the span
        # to zero length — measured as a degenerate-border ValueError.
        assert "plan" in VIEWS_SHOWING["y"]
        # Unfiltered, plan is a legitimate answer — it genuinely shows the y extent.
        assert views_showing("y", ("plan",)) == "plan"
        # Filtered, it is not, and that difference is the whole reason the flag exists.
        assert views_showing("y", ("plan",), horizontal=True) is None
        assert views_showing("y", ALL_THREE, horizontal=True) == "side"

    def test_it_prefers_the_view_each_extent_has_always_used(self):
        # Consulting the model may not move anything while all three principals are planned.
        assert views_showing("x", ALL_THREE, horizontal=True) == "plan"
        assert views_showing("y", ALL_THREE, horizontal=True) == "side"

    def test_it_falls_back_when_the_preferred_view_is_gone(self):
        assert views_showing("x", ("front", "side"), horizontal=True) == "front"


class TestTheLayoutReclaimsADroppedView:
    def test_dropping_the_plan_view_frees_its_height(self):
        # Without this the whole exercise is pointless: the drawing loses a view and stays on
        # the same paper.
        full = _layout_geometry(90.0, 60.0, 25.0, 1.0, 297.0, 210.0, 120.0, None, 0)
        two = _layout_geometry(
            90.0, 60.0, 25.0, 1.0, 297.0, 210.0, 120.0, None, 0, views=("front", "side")
        )
        assert two.FV_Y > full.FV_Y, "the front view did not move into the reclaimed band"

    def test_omitting_the_side_view_reclaims_its_width(self):
        # Only the plan case was handled when this first landed: a set omitting front or
        # side built a sheet without the view and reserved its paper anyway (#1130 review).
        full = _layout_geometry(90.0, 60.0, 25.0, 1.0, 297.0, 210.0, 120.0, None, 0)
        no_side = _layout_geometry(
            90.0, 60.0, 25.0, 1.0, 297.0, 210.0, 120.0, None, 0, views=("front", "plan")
        )
        assert no_side.auto_row_w < full.auto_row_w
        # Specifically the side view's own width, at this scale.
        assert full.auto_row_w - no_side.auto_row_w == pytest.approx(60.0)

    def test_omitting_the_front_view_keeps_the_column_the_plan_still_needs(self):
        # The asymmetry worth pinning: front and plan SHARE a column and both project the x
        # extent across the page, so dropping one of them reclaims height, not width.
        full = _layout_geometry(90.0, 60.0, 25.0, 1.0, 297.0, 210.0, 120.0, None, 0)
        no_front = _layout_geometry(
            90.0, 60.0, 25.0, 1.0, 297.0, 210.0, 120.0, None, 0, views=("plan", "side")
        )
        assert no_front.auto_row_w == full.auto_row_w

    def test_omitting_both_column_views_reclaims_the_column(self):
        # Front and plan share the column, so its width is only reclaimed when BOTH go.
        full = _layout_geometry(90.0, 60.0, 25.0, 1.0, 297.0, 210.0, 120.0, None, 0)
        side_only = _layout_geometry(
            90.0, 60.0, 25.0, 1.0, 297.0, 210.0, 120.0, None, 0, views=("side",)
        )
        assert full.auto_row_w - side_only.auto_row_w >= 90.0

    def test_the_candidate_records_the_views_it_was_judged_on(self, monkeypatch):
        # First-class infeasibility data that disagreed with the layout `_fits` actually
        # evaluated would be worse than none. Observed at the predicate, because the
        # rejected candidates are not yet exposed on a result.
        import draftwright.compose as compose_mod

        seen = []
        original = compose_mod.candidate_is_feasible

        def spy(candidate, fits):
            seen.append(candidate.views)
            return original(candidate, fits)

        monkeypatch.setattr(compose_mod, "candidate_is_feasible", spy)
        compose_mod.choose_scale(90.0, 60.0, 25.0, views=("front", "side"))
        assert seen, "no candidate was judged"
        assert set(seen) == {("front", "side")}

    def test_the_candidate_defaults_to_the_third_angle_three(self, monkeypatch):
        import draftwright.compose as compose_mod

        seen = []
        original = compose_mod.candidate_is_feasible
        monkeypatch.setattr(
            compose_mod,
            "candidate_is_feasible",
            lambda candidate, fits: (seen.append(candidate.views), original(candidate, fits))[1],
        )
        compose_mod.choose_scale(90.0, 60.0, 25.0)
        assert set(seen) == {("front", "plan", "side")}

    def test_the_default_is_unchanged(self):
        explicit = _layout_geometry(
            90.0, 60.0, 25.0, 1.0, 297.0, 210.0, 120.0, None, 0, views=ALL_THREE
        )
        implied = _layout_geometry(90.0, 60.0, 25.0, 1.0, 297.0, 210.0, 120.0, None, 0)
        assert explicit.FV_Y == implied.FV_Y
        assert explicit.auto_fits == implied.auto_fits


class TestTheAsymmetricCounterexample:
    """Same box, same single hole, different axis — opposite verdicts."""

    def test_a_view_the_features_need_is_refused(self):
        with pytest.raises(ViewPlanIncomplete) as caught:
            build_drawing(_z_hole(), _views=("front", "side"))
        assert caught.value.planned == ("front", "side")
        assert {item.identity.parameter for item in caught.value.uncovered} == {
            "bore.diameter",
            "location.location",
        }
        assert all(item.preferred_view == "plan" for item in caught.value.uncovered)
        assert "hole_1.bore.diameter" in str(caught.value)
        assert "add `plan`" in str(caught.value)

    def test_an_authored_location_names_its_constraint_before_projection(self):
        from draftwright.model import Datum, Frame, HoleFeature, PartModel
        from draftwright.model.ir import RequestedDimension

        part = _z_hole()
        bbox = part.bounding_box()
        hole = HoleFeature(Frame((20.0, 15.0, 12.5), "z"), 8.0, depth=None, through=True)
        model = PartModel(
            bbox=bbox,
            orientation="prismatic",
            features=[hole],
            datums=[Datum("datum_xy", "point", (bbox.min.X, bbox.min.Y, bbox.min.Z))],
            authored_dimensions=(RequestedDimension(hole, "location"),),
        )

        with pytest.raises(ViewPlanIncomplete) as caught:
            build_drawing(part, model=model, _views=("front", "side"))

        assert [item.identity.parameter for item in caught.value.uncovered] == [
            "location.location"
        ]
        assert "hole_1.location" in str(caught.value)
        assert "bore.diameter" not in str(caught.value), "the authored omission stays omitted"

        drawing = build_drawing(part, model=model, _views=("front", "plan"))
        y_names = [name for name in drawing.annotations() if name.startswith("m_locy")]
        assert y_names, "the approved Y member must re-home rather than disappear"
        assert {drawing.view_of(name) for name in y_names} == {"plan"}

    def test_the_adrs_two_requirement_diagnostic_is_executable(self):
        from dataclasses import replace

        from draftwright.model.ir import RequestedDimension

        part = _z_hole()
        detected = detect_part_model(part)
        assert detected is not None
        envelope = next(feature for feature in detected.features if feature.kind == "envelope")
        hole = next(feature for feature in detected.features if feature.kind == "hole")
        model = replace(
            detected,
            authored_dimensions=(
                RequestedDimension(envelope, "depth.length"),
                RequestedDimension(hole, "location"),
            ),
        )

        with pytest.raises(ViewPlanIncomplete) as caught:
            build_drawing(part, model=model, _views=("front",))

        assert [(item.label, item.preferred_view) for item in caught.value.uncovered] == [
            ("envelope.depth.length", "side"),
            ("hole_1.location", "plan"),
        ]

    def test_the_same_view_is_droppable_when_the_feature_turns(self):
        # The asymmetry: identical geometry apart from the hole's axis. Dropping plan is
        # refused above and clean here, so the verdict tracks what the view CARRIES rather
        # than what it looks like.
        drawing = build_drawing(_x_hole(), _views=("front", "side"))
        assert sorted(drawing.views) == ["front", "iso", "side"]
        assert not [code for code in _lint(drawing) if code.endswith("_dropped")]

    def test_dropping_the_view_the_x_hole_needs_is_refused_in_turn(self):
        # The other half of the asymmetry, so neither result is an accident of which view
        # happens to be named: for this part SIDE is the necessary one.
        with pytest.raises(ViewPlanIncomplete) as caught:
            build_drawing(_x_hole(), _views=("front", "plan"))
        assert caught.value.planned == ("front", "plan")
        # Collected, not first-failure-only: the same missing side view strands both the
        # asymmetric bore and the mandatory overall depth, and neither may be discarded.
        assert {item.identity.parameter for item in caught.value.uncovered} == {
            "bore.diameter",
            "depth.length",
            "location_off_axis.y",
            "location_off_axis.z",
        }
        assert all(item.preferred_view == "side" for item in caught.value.uncovered)

    def test_an_authored_off_axis_location_is_checked_at_the_same_boundary(self):
        from draftwright.model import Frame, HoleFeature, PartModel
        from draftwright.model.ir import RequestedDimension

        part = _x_hole()
        hole = HoleFeature(Frame((45.0, 30.0, 12.5), "x"), 10.0, depth=None, through=True)
        model = PartModel(
            bbox=part.bounding_box(),
            orientation="prismatic",
            features=[hole],
            authored_dimensions=(RequestedDimension(hole, "location"),),
        )
        with pytest.raises(ViewPlanIncomplete) as caught:
            build_drawing(part, model=model, _views=("front", "plan"))
        assert {item.identity.parameter for item in caught.value.uncovered} == {
            "location_off_axis.y",
            "location_off_axis.z",
        }


class TestAnExtentMovesOrIsReported:
    def test_the_overall_width_re_homes_to_the_front_when_plan_goes(self):
        # It was pinned to plan and would have vanished with it.
        drawing = build_drawing(_x_hole(), _views=("front", "side"))
        assert "m_env_width" in drawing.annotations()
        assert "m_env_depth" in drawing.annotations()

    def test_the_planner_record_itself_re_homes_instead_of_only_the_renderer(self):
        from draftwright.model.planner import plan_dimensions

        model = detect_part_model(_plain_box())
        assert model is not None
        envelope = next(
            group
            for group in plan_dimensions(model, planned_views=("front", "side"))
            if group.feature.kind == "envelope"
        )
        assert envelope.view == "front"

    def test_the_compiler_cannot_silently_replan_against_the_fixed_topology(self):
        from draftwright.model.compiled import compile_dimensions

        model = detect_part_model(_z_hole())
        assert model is not None
        with pytest.raises(ViewPlanIncomplete) as caught:
            compile_dimensions(model, planned_views=("front", "side"))
        assert {item.identity.parameter for item in caught.value.uncovered} >= {
            "bore.diameter",
            "location.location",
        }

    @pytest.mark.parametrize(
        ("shoulder", "views", "missing"),
        [(("x", 30.0), ("front", "side"), "plan"), (("y", 20.0), ("front", "plan"), "side")],
    )
    def test_a_correlated_step_position_keeps_each_directional_member(
        self, shoulder, views, missing
    ):
        from draftwright.model import Frame, PartModel, StepLevelFeature
        from draftwright.model.planner import plan_dimensions

        model = PartModel(
            bbox=Box(90, 60, 25).bounding_box(),
            orientation="prismatic",
            features=[
                StepLevelFeature(
                    Frame((0.0, 0.0, 0.0), "z"),
                    base=0.0,
                    levels=(10.0,),
                    shoulders=(shoulder,),
                )
            ],
        )
        with pytest.raises(ViewPlanIncomplete) as caught:
            plan_dimensions(model, planned_views=views)
        position = next(
            item
            for item in caught.value.uncovered
            if item.identity.parameter == "step_position.length"
        )
        assert position.preferred_view == missing

    def test_an_extent_no_planned_view_can_show_is_rejected_before_projection(self):
        # Depth reads horizontally ONLY in side. Without it the extent cannot be drawn, and
        # ADR 0016 Amdt 6 says it must be reported against its measurement rather than
        # disappear. Phase 5.5 moves that feasibility decision above projection, so a
        # plausible-looking incomplete drawing is no longer returned.
        with pytest.raises(ViewPlanIncomplete) as caught:
            build_drawing(Box(90, 60, 25), _views=("front", "plan"))
        assert [item.identity.parameter for item in caught.value.uncovered] == ["depth.length"]
        assert "envelope.depth.length" in str(caught.value)

    def test_a_width_diagnostic_lists_both_semantically_eligible_views(self):
        from draftwright.model.planner import plan_dimensions

        model = detect_part_model(_plain_box())
        assert model is not None
        with pytest.raises(ViewPlanIncomplete) as caught:
            plan_dimensions(model, planned_views=("side",))
        width = next(
            item for item in caught.value.uncovered if item.identity.parameter == "width.length"
        )
        assert width.eligible_views == ("plan", "front")
        assert "add one of `plan`, `front`" in str(caught.value)

    def test_an_authored_slot_position_uses_the_slot_plane_not_its_long_axis(self):
        from draftwright.model import Frame, PartModel, SlotFeature
        from draftwright.model.ir import RequestedDimension
        from draftwright.model.planner import plan_dimensions

        slot = SlotFeature(
            Frame((30.0, 20.0, 10.0), "x"),
            width_axis="y",
            long_axis="x",
            width=8.0,
            length=30.0,
            w_center=20.0,
            lo=15.0,
            hi=45.0,
        )
        model = PartModel(
            bbox=Box(60, 40, 20).bounding_box(),
            orientation="prismatic",
            features=[slot],
            authored_dimensions=(RequestedDimension(slot, "location"),),
        )
        with pytest.raises(ViewPlanIncomplete) as caught:
            plan_dimensions(model, planned_views=("front", "side"))
        assert [
            (item.identity.parameter, item.preferred_view) for item in caught.value.uncovered
        ] == [("location_slot.length", "plan")]

    def test_every_renderer_supported_feature_family_has_semantic_view_ownership(self):
        """Mutation guard for the conservative ownership table, including uncommon IR arms."""
        from types import SimpleNamespace

        from draftwright.model import ChannelFeature, Frame, PadFeature, PocketFeature, SlotFeature
        from draftwright.model.ir import DimParameter
        from draftwright.model.planner import PlannedDimension, _parameter_view_preferences

        def preferences(feature, role):
            planned = PlannedDimension(DimParameter("length", role, 1.0), "linear")
            return _parameter_view_preferences(feature, planned)

        planar = dict(
            frame=Frame((0.0, 0.0, 0.0), "z"),
            width_axis="y",
            long_axis="x",
            width=8.0,
            length=30.0,
            w_center=20.0,
            lo=15.0,
            hi=45.0,
        )
        cases = [
            (
                SimpleNamespace(kind="boss", frame=Frame((0.0, 0.0, 0.0), "x")),
                "boss_height",
                ("front",),
            ),
            (SimpleNamespace(kind="step", frame=Frame((0.0, 0.0, 0.0), "y")), "step", ("side",)),
            (SlotFeature(**planar), "slot_width", ("plan",)),
            (PadFeature(**planar, z0=0.0, z1=5.0), "pad_width", ("plan",)),
            (
                PocketFeature(
                    Frame((0.0, 0.0, 0.0), "x"),
                    width_axis="y",
                    long_axis="z",
                    width=8.0,
                    length=30.0,
                    depth=5.0,
                    w_center=20.0,
                    lo=15.0,
                    hi=45.0,
                ),
                "pocket_depth",
                ("side",),
            ),
            (
                SimpleNamespace(kind="pocket_pattern", frame=Frame((0.0, 0.0, 0.0), "z")),
                "pitch",
                ("plan",),
            ),
            (
                ChannelFeature(
                    Frame((0.0, 0.0, 0.0), "z"),
                    width_axis="x",
                    long_axis="y",
                    width=8.0,
                    w_center=20.0,
                    lo=0.0,
                    hi=30.0,
                    d_lo=0.0,
                    d_hi=5.0,
                ),
                "channel_width",
                ("front",),
            ),
        ]
        assert [preferences(feature, role) for feature, role, _expected in cases] == [
            expected for _feature, _role, expected in cases
        ]

    def test_an_off_axis_pocket_location_uses_its_opening_normal(self):
        from draftwright.model import Datum, Frame, PartModel, PocketFeature
        from draftwright.model.ir import RequestedDimension
        from draftwright.model.planner import plan_dimensions

        pocket = PocketFeature(
            Frame((30.0, 20.0, 10.0), "x"),
            width_axis="y",
            long_axis="z",
            width=8.0,
            length=20.0,
            depth=5.0,
            w_center=20.0,
            lo=0.0,
            hi=20.0,
        )
        model = PartModel(
            bbox=Box(60, 40, 20).bounding_box(),
            orientation="prismatic",
            features=[pocket],
            datums=[Datum("datum_xy", "point", (0.0, 0.0, 0.0))],
            authored_dimensions=(RequestedDimension(pocket, "location"),),
        )
        with pytest.raises(ViewPlanIncomplete) as caught:
            plan_dimensions(model, planned_views=("front", "plan"))
        assert [
            (item.identity.parameter, item.preferred_view) for item in caught.value.uncovered
        ] == [("location_pocket.location", "side")]


class TestTheDecisionSurvivesEveryRebuild:
    def test_a_rebuild_does_not_revert_the_view_set(self):
        # The arrangement gate's fallback rebuild silently restored four views on a larger
        # sheet, because it re-entered the builder without the view set. Same defect class
        # as the carried arrangement, one stage further out.
        drawing = build_drawing(_x_hole(), _views=("front", "side"))
        assert "plan" not in drawing.views
        assert len(drawing.arrangement_decision["attempts"]) >= 1


class TestTheCaseStudy:
    """ADR 0018's motivating part, measured rather than asserted from the ADR's prose."""

    @staticmethod
    def _plate():
        part = Rot(0, 90, 0) * Cylinder(108.5, 12)
        part += Rot(0, 90, 0) * Pos(0, 0, 12) * Cylinder(45, 18)
        part += Rot(0, 90, 0) * Pos(0, 0, 28.75) * Cylinder(28, 15.5)
        part -= Rot(0, 90, 0) * Cylinder(16, 60)
        part -= Pos(30, 0, 0) * Box(60, 8, 20)
        for index in range(6):
            angle = index * math.pi / 3
            part -= (
                Rot(0, 90, 0)
                * Pos(85 * math.cos(angle), 85 * math.sin(angle), 0)
                * Cylinder(6, 40)
            )
        for index in range(4):
            angle = index * math.pi / 2 + math.pi / 4
            part -= (
                Rot(0, 90, 0)
                * Pos(60 * math.cos(angle), 60 * math.sin(angle), 0)
                * Cylinder(4.5, 40)
            )
        return part

    @pytest.mark.slow
    def test_the_smaller_view_set_reaches_a2_and_what_it_costs(self):
        part = self._plate()
        full = build_drawing(part)
        reduced = build_drawing(part, _views=("front", "side"))

        # The ADR's failure: the fixed four-view topology drives A1 at 1:1.
        assert (full.page_w, full.page_h) == (841.0, 594.0)
        assert full.scale == 1.0

        # Dropping the redundant plan reaches the ADR's target sheet at the same scale.
        assert (reduced.page_w, reduced.page_h) == (594.0, 420.0)
        assert reduced.scale == full.scale

        # And this is why nothing selects it automatically yet. The smaller sheet loses
        # annotations, so a requirement gate weighing this candidate would reject it. The
        # remaining work is re-homing those to the axial view — not the layout, which now
        # does its part. This test is written to change shape when that lands.
        assert len(reduced.annotations()) < len(full.annotations())
        assert {"callout_dropped", "annotation_out_of_bounds"} & _lint(reduced)

    @pytest.mark.slow
    def test_dropping_the_front_view_refuses_by_name_rather_than_crashing(self):
        # The diameter row is anchored under the front elevation and unpacked
        # `view_bounds("front")` directly — safe under a fixed four-view topology, a
        # `TypeError: cannot unpack non-iterable NoneType` the moment a view set omits it.
        # `view_bounds` has documented `None` for an absent view since #28.
        #
        # This part is the cheapest thing that reaches that code with the front view gone:
        # it needs enough diameters for the row to be attempted at all, and the parts small
        # enough to be fast return early on an empty item list. The distinction the
        # assertion draws is TypeError (crashed inside a pass) vs a pre-projection planning
        # refusal naming the semantic requirements — a view set must be refusable for a gate
        # to weigh it.
        with pytest.raises(ViewPlanIncomplete) as caught:
            build_drawing(self._plate(), _views=("plan", "side"))
        assert caught.value.uncovered
        assert all(item.preferred_view == "front" for item in caught.value.uncovered)
