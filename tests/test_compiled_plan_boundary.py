"""ADR 0016's boundary: a dimensional renderer draws only what the compiler approved.

The rule — *renderers may emit dimensional content only from the compiled plan* — exists
because the previous arrangement made honouring suppression a convention each renderer had
to opt into. Eight adversarial review rounds on #921 found eight that had not. Fixing them
one at a time produced four different mechanisms for saying the same thing and no reason to
believe the ninth renderer would be different.

These tests pin the property rather than the instances:

- the compiler decides WHAT (`TestTheCompilerOwnsContent`);
- the renderer decides WHERE, and cannot reach back for content
  (`TestTheRendererCannotSeeContent`);
- an omission is not a drop, and the two report differently (`TestOmissionIsNotADrop`);
- the migration is real and does not silently stall (`TestTheBoundaryIsLoadBearing`).

`render_height_ladder` is the first slice on purpose: it exercises every hard case at once —
correlated rungs, a geometry-derived overall dimension, spans that must survive projection,
and suppression. If the boundary holds here it will hold for the flatter renderers.
"""

from __future__ import annotations

import ast
import copy
import inspect
import pathlib
import pickle
from dataclasses import replace

import pytest
from build123d import Box, Cylinder, Pos, Rot

from draftwright import Sheet
from draftwright.annotations.from_model import render_height_ladder
from draftwright.builder import build_drawing, detect_part_model
from draftwright.model.compiled import RenderableDimensionPlan, compile_dimensions

#: Marks that carry NO value, and so legitimately survive an empty compiled plan.
#: Each needs that reason; "the test failed otherwise" is not one.
_VALUE_FREE = (
    "centerline",  # shows where an axis is
    "m_cm",  # centre marks — sized off the hole they mark (#875)
    "note_",  # the ISO NTS note
    "title_block",
    "section_",  # cutting-plane arrows and label
    "hatch",
    "detail_marker",
    "detail_caption",
)

#: Marks that DO carry a value and still survive an empty plan — i.e. renderers that have
#: not crossed the boundary yet. Listed individually rather than lumped in with furniture,
#: because calling them furniture is how the pitch dim hid: annotation names are not a
#: semantic type system, and a prefix list quietly became the definition of "dimensional"
#: (#923 review round 4). Each entry is a pending migration, and the ADR names it too.
_PENDING_VALUE_CARRYING = (
    # Hole callouts still take legacy `DimensionGroup`s. They DO honour `suppressed`
    # (model/callout.py checks it at every term), so this is a structural gap rather than a
    # behavioural one — but the whole point of the boundary is that "the renderer checks"
    # is not a guarantee, so it stays on the list until the contract changes.
    "hc_",
    "pmi_",  # raw AP242 PMI — the one documented permanent exception
)


def _staircase():
    return Box(120, 60, 15) + Pos(-20, 0, 15) * Box(80, 60, 15) + Pos(-40, 0, 30) * Box(40, 60, 15)


def _crowded_staircase():
    """A tall narrow stacked-tier block whose shoulders sit 3 mm apart in Z.

    At the auto sheet scale the legibility gate drops at least one rung, which is the
    trigger for the enlarged detail view — the only way to exercise the detail redraw at
    all. Narrow in X/Y so the detail footprint actually fits the sheet; a fixture whose
    detail is `detail_unplaceable` draws nothing and would make this test vacuous.
    (Mirrors `_crowded_shoulder_part` in tests/test_make_drawing.py.)
    """
    part = Pos(0, 0, 3) * Box(20, 16, 6)
    z = 6.0
    for w in (16, 13, 10, 7, 5):
        part = part + Pos(0, 0, z + 1.5) * Box(w, 12, 3)
        z += 3
    return part


def _side_drilled():
    """A block with X- and Y-drilled bores and a Z one, so the empty-plan guard actually
    walks the off-axis location pass.

    `_staircase()` has no holes at all, which is why the guard passed for a whole review
    round while `_locate_off_axis_holes` rebuilt every side-drilled position from raw IR
    (#925 review). A behavioural guard only covers the paths its fixture reaches.
    """
    return (
        Box(100, 60, 20)
        - Pos(0, 5, 2) * Rot(0, 90, 0) * Cylinder(2, 200)
        - Pos(10, 0, 8) * Rot(90, 0, 0) * Cylinder(2, 200)
        - Pos(-30, -15, 0) * Cylinder(3, 60)
    )


def _uniform_staircase():
    return (
        Box(90, 60, 10)
        + Pos(0, 0, 10) * Box(75, 60, 10)
        + Pos(0, 0, 20) * Box(60, 60, 10)
        + Pos(0, 0, 30) * Box(45, 60, 10)
    )


class TestTheCompilerOwnsContent:
    def test_the_rung_set_arrives_decided(self):
        plan = compile_dimensions(detect_part_model(_staircase()))
        rungs = plan.ladder("step_height")
        assert rungs is not None
        assert [r.final_label for r in rungs.rungs] == ["15", "30"]
        assert all(r.span is not None for r in rungs.rungs), "spans travel in PART space"

    def test_a_uniform_staircase_collapses_in_the_compiler(self):
        """`n× rise` is a decision about what the drawing SAYS, so it is made once, before
        any renderer sees the set — not rediscovered beside the legibility gate, which reads
        similarly but is genuinely about the page."""
        rungs = compile_dimensions(detect_part_model(_uniform_staircase())).ladder("step_height")
        assert rungs is not None and rungs.representative
        assert len(rungs.rungs) == 1 and "×" in rungs.rungs[0].final_label

    @pytest.mark.parametrize(
        "make_part", [_staircase, _uniform_staircase], ids=["stepped", "uniform"]
    )
    def test_a_label_always_measures_its_own_span(self, make_part):
        """The invariant that keeps a dimension honest: the number printed IS the distance
        between the two points the line runs between.

        It broke the moment the compiler started measuring from `StepLevelFeature.base`
        while the renderer still anchored every witness at the view's bottom edge — a
        declared base above the part's bottom made the line span the whole part and read
        the shorter figure (#923 review). Checking it at the source catches the
        disagreement for every renderer rather than one fixture at a time.

        Distance along the span's OWN varying axis, not the Z delta: a height rung runs
        along Z and a shoulder along X, and a Z-only check silently passes every shoulder
        while claiming to be general (#923 review round 2). A representative rung is
        included rather than exempted — it measures one rise, so it obeys the same rule;
        what differs is only that its LABEL reports that rise n times.
        """
        plan = compile_dimensions(detect_part_model(make_part()))
        for ladder in plan.ladders:
            for rung in ladder.rungs:
                if rung.span is None:
                    continue
                lo, hi = rung.span
                measured = max(abs(b - a) for a, b in zip(lo, hi))
                assert measured == pytest.approx(rung.value, rel=0.1), (
                    f"{ladder.kind} {rung.final_label!r} spans {measured} but claims {rung.value}"
                )
                if ladder.representative:
                    # The one deliberate difference, asserted rather than skipped: the span
                    # is a single rise and the label multiplies it.
                    assert "×" in rung.final_label, "a representative mark must say how many"

    def test_a_declared_base_is_measured_from_that_base(self):
        """`StepLevelFeature.base` is the IR's own statement of what the rungs measure from.
        The planner always used it; the renderer used the bounding-box minimum, so for a
        declared base the plan and the drawing disagreed about the number. One source now."""
        from draftwright.model.ir import Frame, PartModel, StepLevelFeature

        step = StepLevelFeature(Frame((0, 0, 5), "z"), base=5.0, levels=(10.0, 20.0))
        model = PartModel(
            bbox=Box(90, 60, 30).bounding_box(),
            orientation="prismatic",
            features=[step],
            datums=[],
        )
        rungs = compile_dimensions(model).ladder("step_height")
        assert [r.value for r in rungs.rungs] == [5.0, 15.0], "measured from base=5, not bbox"
        assert all(r.span[0][2] == 5.0 for r in rungs.rungs), "and the LINE starts there too"

    def test_the_overall_height_is_an_approved_entry_not_a_bbox_read(self):
        plan = compile_dimensions(detect_part_model(Box(90, 60, 20)))
        overall = plan.ladder("overall_height")
        assert overall is not None and overall.rungs[0].value == pytest.approx(20)

    def test_drawing_state_is_an_input_to_the_compile(self):
        """`include_overall` is the finalize drain's explicit-envelope-height request —
        drawing state, not model state — so it decides membership of the SET rather than
        being applied by the renderer afterwards."""
        model = detect_part_model(Box(90, 60, 20))
        assert compile_dimensions(model, include_overall=False).ladder("overall_height") is None
        assert compile_dimensions(model, include_overall=True).ladder("overall_height") is not None

    def test_a_rotational_od_conveying_the_height_is_settled_once(self):
        """Two rules for this used to live apart — the planner suppressed a Z-turned
        envelope height, the renderer independently suppressed that AND an X/Y rotational
        OD — and neither knew about the other. Both are here now."""
        model = detect_part_model(Cylinder(radius=20, height=40))
        plan = compile_dimensions(model)
        assert plan.ladder("overall_height") is not None  # Z body: the ladder carries it
        reasons = [o.reason for o in plan.diagnostics]
        assert all("rotational OD" not in r for r in reasons)


class TestTheRendererCannotSeeContent:
    def test_the_signature_takes_the_plan_and_the_frame(self):
        """The structural half of the rule. A renderer that cannot name `model` or `a`
        cannot reconstruct a dimension the compiler withheld — the failure mode is removed
        rather than tested for."""
        params = set(inspect.signature(render_height_ladder).parameters)
        assert {"plan", "frame"} <= params
        assert not ({"model", "a"} & params), (
            "a dimensional renderer must not take the PartModel or the Analysis — both "
            "carry the feature inventory and the bounding box it would rebuild content from"
        )

    def test_the_body_never_touches_the_feature_inventory(self):
        """Reads the source rather than trusting the signature: a renderer could still
        reach content through an argument that legitimately carries it."""
        src = inspect.getsource(render_height_ladder)
        tree = ast.parse(src)
        reads = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
        }
        # `_feature` is the FeatureRef escape hatch: resolving a provenance handle back to
        # its feature inside a dimensional renderer is a boundary violation, and this is
        # where it gets caught.
        forbidden = {
            "features",
            "bb",
            "levels",
            "shoulders",
            "orientation",
            "z_size",
            "base",
            "_feature",
        }
        assert not (reads & forbidden), (
            f"{sorted(reads & forbidden)} is model content — the ladder is drawn from the "
            "compiled plan's approved entries, whose spans carry every coordinate it needs"
        )

    def test_the_provenance_handle_exposes_no_measurement(self):
        """Carrying the `Feature` on an approved entry left the bypass one attribute access
        away — `.feature.levels` rebuilds exactly what the compiler withheld (#923 review).
        The handle exposes identity and category, and no measurement."""
        from draftwright.model.compiled import FeatureRef

        ladder = compile_dimensions(detect_part_model(_staircase())).ladder("step_height")
        assert isinstance(ladder.ref, FeatureRef)
        assert ladder.ref.kind == "step_level", "category is fine — it is not a measurement"
        for content in ("levels", "shoulders", "base", "datum", "parameters"):
            assert not hasattr(ladder.ref, content), f"FeatureRef exposes {content}"

    def test_identities_survive_the_compile(self):
        """`DimensionId` is ADR 0016's stable addressable identity; a renderer-facing result
        that discarded it would create identity debt on the boundary meant to remove it."""
        plan = compile_dimensions(detect_part_model(_staircase()))
        for ladder in plan.ladders:
            for rung in ladder.rungs:
                assert rung.id is not None, f"{ladder.kind} rung lost its DimensionId"

    def test_zero_length_shoulder_keeps_its_explicit_axis(self):
        """A degenerate datum→shoulder span cannot reveal whether it is X or Y."""
        from draftwright.model.ir import Frame, PartModel, StepLevelFeature

        part = Box(90, 60, 30)
        step = StepLevelFeature(
            Frame((0, 0, 0), "z"),
            base=0.0,
            levels=(10.0,),
            shoulders=(("x", -45.0),),
            datum=(-45.0, -30.0, 0.0),
        )
        model = PartModel(
            bbox=part.bounding_box(),
            orientation=None,
            features=[step],
        )
        ladder = compile_dimensions(model).ladder("step_position")
        assert ladder is not None
        assert len(ladder.rungs) == 1
        assert ladder.rungs[0].span[0] == ladder.rungs[0].span[1]
        assert ladder.rungs[0].axis == "x"

    def test_the_layout_frame_carries_no_part_geometry(self):
        from draftwright._core import LayoutFrame

        fields = set(LayoutFrame.__dataclass_fields__)
        assert not (fields & {"part", "bb", "holes", "x_size", "y_size", "z_size", "model"}), (
            "LayoutFrame is the page geometry a renderer may use; part geometry belongs to "
            "the compiler, which is the only place allowed to turn it into a dimension"
        )

    def test_layout_frame_edges_guarantee_page_order_under_reversed_projection(self):
        from types import SimpleNamespace

        from draftwright._core import LayoutFrame

        frame = LayoutFrame(
            proj=SimpleNamespace(),
            scale=1.0,
            front=(40.0, 10.0, 30.0, 5.0),
            plan=(0.0, 1.0, 2.0, 3.0),
            side=(0.0, 1.0, 2.0, 3.0),
            fv_zones=SimpleNamespace(),
            pv_zones=SimpleNamespace(),
            sv_zones=SimpleNamespace(),
        )
        assert frame.edges("front") == (10.0, 40.0, 5.0, 30.0)

    def test_compiler_rejects_groups_planned_from_an_equal_but_different_model(self):
        """Suppression must fail closed rather than miss an identity-keyed lookup."""
        first = detect_part_model(_staircase())
        second = detect_part_model(_staircase())
        assert first is not second
        from draftwright.model.planner import plan_dimensions

        with pytest.raises(ValueError, match="exact PartModel"):
            compile_dimensions(first, groups=plan_dimensions(second))

    def test_feature_facts_and_compiled_plan_support_standard_composition(self):
        """Strict slot access must survive copy, snapshot and process boundaries."""
        from draftwright.model.compiled import FeatureFacts

        plan = compile_dimensions(detect_part_model(_staircase()))
        facts = plan.groups[0].facts
        assert isinstance(facts, FeatureFacts)

        for clone in (
            copy.copy(facts),
            copy.deepcopy(facts),
            pickle.loads(pickle.dumps(facts)),
        ):
            assert repr(clone) == repr(facts)

        for clone in (
            copy.copy(plan),
            copy.deepcopy(plan),
            pickle.loads(pickle.dumps(plan)),
        ):
            assert clone.ladder("step_height") is not None

    def test_every_existing_ir_kind_is_explicitly_classified(self):
        import draftwright.model.ir as ir
        from draftwright.model.compiled import _FACTS

        ir_kinds = {
            value.kind
            for value in vars(ir).values()
            if isinstance(value, type) and isinstance(getattr(value, "kind", None), str)
        }
        assert set(_FACTS) == ir_kinds

    def test_numeric_fragments_and_complete_correlated_labels_are_distinct(self):
        model = detect_part_model(Cylinder(10, 10))
        plan = compile_dimensions(model)
        ordinary = next(d for g in plan.groups for d in g.dims)
        assert ordinary.value_text
        assert ordinary.rendered_label is None
        with pytest.raises(AttributeError, match="numeric value_text only"):
            _ = ordinary.final_label

        ladder = compile_dimensions(detect_part_model(_staircase())).ladder("step_height")
        assert ladder is not None
        assert all(r.final_label for r in ladder.rungs)


class TestOmissionIsNotADrop:
    """Two different "not drawn" meet in this renderer and must stay distinguishable: the
    compiler's omission (never arrived) and the placer's drop (arrived, did not fit)."""

    def test_an_omission_is_reported_through_diagnostics(self):
        model = detect_part_model(Box(90, 60, 20))
        plan = compile_dimensions(model, include_overall=False)
        assert plan.ladder("overall_height") is None
        assert not any(o.authored for o in plan.diagnostics), "no author involved here"

    def test_a_planner_reason_is_not_an_authored_omission(self):
        """`Omission.authored` is the distinction three #921 attempts kept blurring: only an
        author's omission makes an empty result the script's own doing."""
        from draftwright.model.compiled import Omission

        assert Omission(None, "height.length", 1.0, "square footprint").authored is False
        assert Omission(None, "height.length", 1.0, "not in the authored dimension set").authored


class TestAPositionIsADimension:
    """A location prints a number, so the authored set governs it like any other dimension.

    It did not before #925, for a reason worth stating: a location has no `DimParameter` —
    it is synthesized from the feature and the datum — so it was not *addressable*, and a
    dimension the author cannot address is a dimension the author cannot omit. The set could
    neither name nor exclude one, and every location was drawn regardless.
    """

    @staticmethod
    def _plate_with_a_hole():
        return Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(3, 20)

    @staticmethod
    def _authored(part, roles):
        from draftwright.model import hole as declare_hole

        sheet = Sheet(part)
        sheet.hole(Pos(20, 10, 0) * Cylinder(3, 20))
        assert declare_hole is not None  # the verb above is the declaration under test
        for role in roles:
            sheet.dimension(sheet.features[0], role)
        return {n for n, _ in sheet.build().iter_annotations()}

    def test_an_omitted_location_is_not_drawn(self):
        drawn = self._authored(self._plate_with_a_hole(), ["bore.diameter"])
        assert not [n for n in drawn if n.startswith(("m_locx", "m_locy"))], (
            f"the authored set named only the bore ⌀, so its position dims are an omission: "
            f"{sorted(drawn)}"
        )

    def test_a_named_location_IS_drawn(self):
        """The other half, and the one that makes omission meaningful rather than a way of
        saying locations never work under an authored set."""
        drawn = self._authored(self._plate_with_a_hole(), ["bore.diameter", "location"])
        assert [n for n in drawn if n.startswith("m_locx")]
        assert [n for n in drawn if n.startswith("m_locy")]

    def test_the_auto_set_is_unchanged(self):
        """Nothing above may cost a location on a drawing that authored nothing."""
        auto = {
            n for n, _ in Sheet.from_part(self._plate_with_a_hole()).build().iter_annotations()
        }
        assert [n for n in auto if n.startswith("m_locx")]
        assert [n for n in auto if n.startswith("m_locy")]

    def test_the_dedup_runs_over_the_SURVIVORS(self):
        """Two features whose refs coincide, one authored and one not.

        Deduping before the authored filter would let the omitted feature's ref absorb the
        approved one's and take the drawing's position dim down with it — the author names a
        measurement and gets nothing, which is the failure `_check_authored_targets` exists
        to prevent, arriving by a different route.
        """
        part = Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(3, 20) - Pos(20, 10, 0) * Cylinder(6, 4)
        sheet = Sheet(part)
        sheet.hole(Pos(20, 10, 0) * Cylinder(3, 20))
        sheet.hole(Pos(20, 10, 0) * Cylinder(6, 4))
        # Name the SECOND hole's location only. Its ref coincides with the first's, and the
        # first is the one the undeduped order would have kept.
        sheet.dimension(sheet.features[1], "location")
        drawn = {n for n, _ in sheet.build().iter_annotations()}
        assert [n for n in drawn if n.startswith("m_locx")], (
            "the surviving feature's position was deduped away against an OMITTED sibling"
        )

    @pytest.mark.parametrize("axis", ["x", "y"])
    def test_a_side_drilled_holes_positions_obey_the_authored_set(self, axis):
        """Off-axis positions were the last dimensional path outside the boundary.

        Three statements of one fact disagreed: `location_role` said a hole is locatable,
        `plan_locations` said only a Z-normal one is, and `_locate_off_axis_holes` drew the
        X/Y ones from raw IR regardless — so an authored set naming only a side-drilled
        bore's ⌀ still produced its offset and height dims, with no diagnostic, because they
        were successfully PLACED dimensions that simply sat outside the plan (#925 review).
        """
        from draftwright.model.ir import Frame, HoleFeature, RequestedDimension

        hole = HoleFeature(Frame((10, 5, 2), axis), 4.0, depth=None, through=True)

        def drawn(roles):
            return {
                n
                for n, _ in build_drawing(
                    Box(100, 60, 20),
                    model=[hole],
                    authored=tuple(RequestedDimension(hole, r) for r in roles),
                ).iter_annotations()
                if n.startswith("dim_loc_")
            }

        assert not drawn(["bore.diameter"]), "the author named the ⌀ only"
        # Both halves: omission is only meaningful if naming it still works. A side-drilled
        # hole carries TWO positions (its in-plane offset and its height), and the coarse
        # `location` role approves both.
        assert len(drawn(["bore.diameter", "location"])) == 2

    @pytest.mark.parametrize("order", [("x", "y"), ("y", "x")], ids=["x-first", "y-first"])
    def test_cross_drilled_holes_at_one_point_keep_both_planar_positions(self, order):
        """Two features can share a centre, so the coordinate does not identify a feature.

        A cross-drilled pair — an X bore and a Y bore through the same point — produces four
        approved entries. Grouping them by the member coordinate alone merged all four into
        one occurrence whose axis was whichever feature came first, so the other's planar
        dimension was silently dropped and the output flipped with the model's feature order
        (#925 review). Grouping is by identity AND occurrence; the intentional GEOMETRIC
        dedup happens downstream, per axis.

        Parametrised over both orders because that is what pins this as a property rather
        than as list-order luck.
        """
        from draftwright.model.ir import Frame, HoleFeature, RequestedDimension

        holes = {
            axis: HoleFeature(Frame((10, 5, 2), axis), 4.0, depth=None, through=True)
            for axis in ("x", "y")
        }
        features = [holes[axis] for axis in order]
        dwg = build_drawing(
            Box(100, 60, 20),
            model=features,
            authored=tuple(RequestedDimension(h, "location") for h in features),
        )
        # `dim_loc_{view}_{axis}{value×100}` — compare the MEASUREMENT (axis + value), not
        # the view. Which view carries the shared height is legitimately order-dependent:
        # one Z dim is drawn on whichever bore's natural strip took it, and both read `12`.
        measured = {
            n.rsplit("_", 1)[1] for n, _ in dwg.iter_annotations() if n.startswith("dim_loc_")
        }
        assert measured == {"y3500", "x6000", "z1200"}, (
            "both bores were authored; each keeps its own planar position and the two share "
            f"one height — got {sorted(measured)}"
        )

    @pytest.mark.parametrize("axis", ["x", "y"])
    @pytest.mark.parametrize(
        "roles", [("location",), ("bore.diameter", "location")], ids=["alone", "with-bore"]
    )
    def test_an_off_axis_pattern_location_is_refused_not_accepted_and_lost(self, axis, roles):
        """Eligibility must match what a compiler will actually produce.

        `_LOCATION_ROLE` said every pattern is locatable; `plan_locations` planned only
        Z-normal ones and the bbox compiler handled only holes, so an X/Y pattern fell
        between them — `dimension(pattern, "location")` was ACCEPTED and produced nothing,
        with no diagnostic. That is precisely the blank-drawing failure
        `_check_authored_targets` exists to prevent (#925 review).

        The engine has never drawn an off-axis pattern position (the off-axis pass excluded
        patterns by construction), so the honest fix is for the vocabulary to say so rather
        than for the compiler to invent output. `location_datum` is now the single answer
        that `plan_locations`, the bbox compiler and this check all read.

        The `with-bore` case is the one that survived the first fix: the target check asked
        whether the FEATURE matched anything, so a valid `bore.diameter` entry vouched for
        the invalid `location` beside it.
        """
        from draftwright.model.ir import Frame, HoleFeature, PatternFeature, RequestedDimension

        member = HoleFeature(Frame((10, 5, 2), axis), 4.0, depth=None, through=True)
        pattern = PatternFeature(
            Frame((10, 5, 2), axis),
            "linear",
            3,
            member,
            members=((10, -10, 2), (10, 5, 2), (10, 20, 2)),
            pitch=15,
            direction=(0, 1, 0),
        )
        with pytest.raises(ValueError, match="draws no position"):
            build_drawing(
                Box(100, 60, 20),
                model=[pattern],
                authored=tuple(RequestedDimension(pattern, r) for r in roles),
            )

    def test_a_Z_normal_pattern_location_still_works(self):
        """The false-positive half: narrowing eligibility must not cost the case that works."""
        from draftwright.model.ir import Frame, HoleFeature, PatternFeature, RequestedDimension

        member = HoleFeature(Frame((-40, 0, 0), "z"), 3.0, depth=None, through=True)
        pattern = PatternFeature(
            Frame((0, 0, 0), "z"),
            "linear",
            3,
            member,
            members=((-40, 0, 0), (0, 0, 0), (40, 0, 0)),
            pitch=40,
            direction=(1, 0, 0),
        )

        def drawn(roles):
            return {
                n
                for n, _ in build_drawing(
                    Box(120, 60, 12),
                    model=[pattern],
                    authored=tuple(RequestedDimension(pattern, r) for r in roles),
                ).iter_annotations()
                if n.startswith("m_loc")
            }

        assert not drawn(["bore.diameter"])
        assert drawn(["bore.diameter", "location"])

    def test_eligibility_and_the_compilers_cannot_disagree(self):
        """The property behind both tests above, checked directly rather than per fixture.

        Every feature `location_role` accepts must be produced by one of the two compilers,
        and nothing else may be. Stated over a model carrying every locatable kind in every
        orientation, so a new kind or a new orientation rule cannot land on one side only —
        which is how this defect arrived twice.
        """
        from draftwright.model.ir import (
            Frame,
            HoleFeature,
            PatternFeature,
            PocketFeature,
            SlotFeature,
        )
        from draftwright.model.planner import location_datum, location_role

        member = HoleFeature(Frame((0, 0, 0), "x"), 4.0, depth=None, through=True)
        features = [
            HoleFeature(Frame((0, 0, 0), ax), 4.0, depth=None, through=True) for ax in "xyz"
        ]
        features += [
            PatternFeature(
                Frame((0, 0, 0), ax),
                "linear",
                2,
                member,
                members=((0, 0, 0),),
                pitch=5,
                direction=(0, 1, 0),
            )
            for ax in "xyz"
        ]
        features += [
            PocketFeature(Frame((0, 0, 0), ax), "x", "y", 10.0, 20.0, 5.0, 0.0, 0.0, 10.0)
            for ax in "xyz"
        ]
        features.append(SlotFeature(Frame((0, 0, 0), "z"), "x", "y", 8.0, 20.0, 0.0, 0.0, 20.0))

        for feature in features:
            assert (location_role(feature) is None) == (location_datum(feature) is None), (
                f"{feature.kind}/{feature.frame.axis}: a role without a datum is a target "
                "the vocabulary accepts and no compiler produces"
            )

    def test_a_pattern_pitch_obeys_the_authored_set_both_ways(self):
        """`4× 20` is a value, so it is dimensional however the code groups it.

        Authoring only the pitch also has to WORK, not merely not-crash: furniture used to be
        a side effect of placing a bore callout, so a pattern with no callout drew no pitch
        dim and the measurement the script named vanished silently (#925 review).
        """
        from draftwright.model import hole as declare_hole

        def build(roles):
            sheet = Sheet(Box(120, 60, 12))
            sheet.pattern(
                declare_hole(Pos(-40, 0, 0) * Cylinder(3, 20)),
                kind="linear",
                count=5,
                pitch=20,
                direction=(1, 0, 0),
                at=(0, 0, 0),
            )
            for role in roles:
                sheet.dimension(sheet.features[0], role)
            return {n for n, _ in sheet.build().iter_annotations()}

        assert not [n for n in build(["bore.diameter"]) if n.startswith("dim_pitch")]
        assert [n for n in build(["pitch.length"]) if n.startswith("dim_pitch")], (
            "the script named the pitch and the drawing has no pitch dim"
        )

    @staticmethod
    def _bolt_circle():
        import math

        from draftwright.model.ir import Frame, HoleFeature, PatternFeature

        hole = HoleFeature(Frame((0, 0, 10), "z"), 4.0, depth=None, through=True)
        members = tuple(
            (25 * math.cos(i * math.pi / 2), 25 * math.sin(i * math.pi / 2), 10) for i in range(4)
        )
        return PatternFeature(
            Frame((0, 0, 10), "z"), "bolt_circle", 4, hole, members=members, bcd=50.0
        )

    def _bolt_circle_drawing(self, roles):
        from draftwright.model.ir import RequestedDimension

        pattern = self._bolt_circle()
        return build_drawing(
            Box(100, 60, 20),
            model=[pattern],
            authored=tuple(RequestedDimension(pattern, r) for r in roles),
        )

    def test_an_authored_BCD_without_its_bore_is_an_orphaned_term_not_a_silent_loss(self):
        """A bolt circle's BCD is a planned dimension, so the authored escape must not waive it.

        `bolt_circle.diameter` renders ONLY as the `EQ SP ON ø50 BC` suffix on the bore
        callout, which makes it a dimensional dependent of the head exactly as a counterbore
        ⌀ is — not a rider like the `4×` multiplier. Classifying it as a rider let the
        authored-omission escape swallow the dependency error, `hole_callout_spec` return
        `None`, and the bolt-circle branch of the furniture sweep skip it: the author named a
        50 mm BCD and the drawing contained neither it nor a diagnostic (#925 review).
        """
        with pytest.raises(ValueError, match="EQ SP ON ø50 BC"):
            self._bolt_circle_drawing(["bolt_circle.diameter"])

    def test_the_BCD_prints_when_its_bore_is_authored_too(self):
        """The false-positive half. Refusing the pair as well would trade a silent loss for
        a bolt circle nobody can dimension."""
        drawn = {
            n: getattr(o, "label", None)
            for n, o in self._bolt_circle_drawing(
                ["bore.diameter", "bolt_circle.diameter"]
            ).iter_annotations()
        }
        assert [n for n in drawn if n.startswith("hc_")]

    def test_an_authored_set_still_governs_the_BCD_suffix(self):
        """Naming the bore alone drops the suffix rather than raising — the BCD is omitted,
        which orphans nothing, and asserting on the spec is what makes this about the STRING
        rather than about which annotations happen to be placed."""
        from draftwright.model.ir import PartModel, RequestedDimension
        from draftwright.model.planner import plan_dimensions

        pattern = self._bolt_circle()

        def suffix(roles):
            model = PartModel(
                bbox=Box(100, 60, 20).bounding_box(),
                orientation=None,
                features=[pattern],
                authored_dimensions=(
                    None if roles is None else tuple(RequestedDimension(pattern, r) for r in roles)
                ),
            )
            (group,) = [g for g in plan_dimensions(model) if g.feature_kind == "pattern"]
            from draftwright.model.callout import hole_callout_spec

            return hole_callout_spec(group)["suffix"]

        assert suffix(None) == "EQ SP ON ø50 BC", "the auto set carries it"
        assert suffix(["bore.diameter"]) is None, "omitted, so it must not print"
        assert suffix(["bore.diameter", "bolt_circle.diameter"]) == "EQ SP ON ø50 BC"

    def test_omitting_a_whole_bolt_circle_is_still_coherent(self):
        """Nothing authored on the pattern at all: no callout, no suffix, and no raise —
        the orphan rule fires on a dependent that WOULD print, not on an empty set."""
        from draftwright.model.ir import EnvelopeFeature as _Env
        from draftwright.model.ir import Frame, RequestedDimension

        pattern = self._bolt_circle()
        env = _Env(
            Frame((0, 0, 0), "z"),
            width=100.0,
            depth=60.0,
            height=20.0,
            bbox_min=(-50.0, -30.0, -10.0),
            bbox_max=(50.0, 30.0, 10.0),
        )
        dwg = build_drawing(
            Box(100, 60, 20),
            model=[pattern, env],
            authored=(RequestedDimension(env, "width"),),
        )
        drawn = {n for n, _ in dwg.iter_annotations()}
        assert not [n for n in drawn if n.startswith("hc_")]

    def test_omitting_a_pattern_bore_is_not_an_orphaned_multiplier(self):
        """The `n×` multiplier lives on the FEATURE, so it survives any suppression, and
        #920 refuses to discard it silently. That refusal must not extend to an AUTHORED
        omission: an author who leaves the bore out is not orphaning a multiplier, they are
        declining the string it prefixes — and refusing there made a pattern the one feature
        whose callout could not be omitted at all."""
        from draftwright.model import hole as declare_hole

        sheet = Sheet(Box(120, 60, 12))
        sheet.pattern(
            declare_hole(Pos(-40, 0, 0) * Cylinder(3, 20)),
            kind="linear",
            count=5,
            pitch=20,
            direction=(1, 0, 0),
            at=(0, 0, 0),
        )
        sheet.dimension(sheet.features[0], "pitch.length")
        drawn = {n for n, _ in sheet.build().iter_annotations()}  # must not raise
        assert not [n for n in drawn if n.startswith("hc_")]


class TestLiveAndDeferredEditsAgree:
    """One edit, two paths, one answer.

    `Drawing.callout()` used to answer this with a hand-written prediction of what a callout
    would draw. Three versions were each wrong for some kind (#921 rounds 6–8); the last
    asked "does this feature have ANY approved dimension?", which a turned step with its
    length authored answers yes — and the live path then drew the omitted diameter while the
    deferred path drew nothing (#925 review). There is no prediction now: the renderers
    consume approved content, so drawing nothing is what they DO.
    """

    @staticmethod
    def _boss_with_only_its_height_authored():
        part = Box(80, 60, 12) + Pos(0, 0, 12) * Cylinder(10, 8)
        sheet = Sheet.from_part(part)
        for feature in [f for f in sheet.features if f.kind == "boss"]:
            sheet.dimension(feature, "boss_height.length")
        dwg = sheet.build()
        return dwg, next(f for f in dwg.model().features if f.kind == "boss")

    def test_the_live_path_does_not_draw_an_omitted_diameter(self):
        dwg, boss = self._boss_with_only_its_height_authored()
        before = set(dwg.annotations())
        assert dwg.callout(boss) == "", "a callout with nothing approved is a drop, not a draw"
        assert set(dwg.annotations()) == before

    def test_the_drop_says_why(self):
        """Silence is what makes the deferred path's behaviour indefensible on its own, so
        the live path records the reason rather than matching the silence."""
        dwg, boss = self._boss_with_only_its_height_authored()
        dwg.callout(boss)
        assert any(i.code == "authored_omission" for i in dwg.lint())

    def test_the_deferred_path_agrees(self):
        dwg, boss = self._boss_with_only_its_height_authored()
        with dwg.deferred():
            assert dwg.callout(boss) == ""


class TestTheBoundaryIsLoadBearing:
    @pytest.mark.parametrize(
        "make_part", [_staircase, _side_drilled], ids=["staircase", "side-drilled"]
    )
    def test_an_empty_plan_draws_nothing_dimensional_in_a_REAL_build(self, monkeypatch, make_part):
        """The behavioural statement, over a fixture set that reaches every pass.

        Parametrised because a single fixture is how a whole dimensional path stayed
        outside the boundary unnoticed: `_staircase()` has no holes, so it never entered
        `_locate_off_axis_holes`, which was rebuilding side-drilled positions from raw IR
        (#925 review). The guard is only as wide as what its parts exercise.
        """
        part = make_part()
        before = {n for n, _ in Sheet.from_part(part).build().iter_annotations()}
        assert [n for n in before if not n.startswith(_VALUE_FREE)], (
            "the fixture must draw something dimensional to be worth emptying"
        )

        monkeypatch.setattr(
            "draftwright.annotations.orchestrator.compile_dimensions",
            lambda *a, **kw: RenderableDimensionPlan(),
        )
        empty = {n for n, _ in Sheet.from_part(part).build().iter_annotations()}
        leaked = sorted(
            n for n in empty if not n.startswith(_VALUE_FREE + _PENDING_VALUE_CARRYING)
        )
        assert not leaked, (
            f"{leaked} reached the page from an EMPTY compiled plan — something is "
            "rebuilding dimensions from somewhere other than the plan, and it is not on "
            "the pending list"
        )

    def test_an_empty_plan_draws_no_ladder_in_a_REAL_build(self, monkeypatch):
        """The behavioural statement, and it has to render to mean anything.

        The first version of this test built an empty `RenderableDimensionPlan` and asserted
        its lookups returned `None` — which is a fact about a dataclass, not about the
        engine. It would have passed with `render_height_ladder` ignoring the plan entirely
        and rebuilding every dimension from the model, i.e. with the exact defect the
        boundary exists to prevent (#923 review). Patching the compiler inside a real build
        is what makes it load-bearing.
        """
        part = _staircase()
        drawn = {n for n, _ in Sheet.from_part(part).build().iter_annotations()}
        assert [n for n in drawn if n.startswith(("dim_step", "dim_height"))], (
            "the fixture must draw a ladder to be worth emptying"
        )

        monkeypatch.setattr(
            "draftwright.annotations.orchestrator.compile_dimensions",
            lambda *a, **kw: RenderableDimensionPlan(),
        )
        empty = {n for n, _ in Sheet.from_part(part).build().iter_annotations()}
        # Anything that is neither value-free furniture nor a NAMED pending migration.
        # Filtering to `dim_step`/`dim_height` let `dim_shoulder_*` through for a whole
        # round, and folding value-carrying marks into a "furniture" list hid the pitch dim
        # for another — a guard that only looks where you already know to look is not a
        # guard, and a prefix list must not become the definition of "dimensional".
        leaked = sorted(
            n for n in empty if not n.startswith(_VALUE_FREE + _PENDING_VALUE_CARRYING)
        )
        assert not leaked, (
            f"{leaked} reached the page from an EMPTY compiled plan — something is "
            "rebuilding dimensions from somewhere other than the plan, and it is not on "
            "the pending list"
        )

    def test_the_detail_view_emits_exactly_the_approved_rungs(self):
        """The detail escalation obeys the boundary too — the last dimensional bypass.

        `_request_prismatic_detail` used to re-derive the step from `dwg.model()` and
        rebuild the ladder from `step.levels`, so a rung the compiler withheld still
        reached the detail view: an approved three-rung plan drew five (#923 review).
        Restricting the direct renderer while its escalation reconstructed the same content
        left the rule true only of the path anyone happened to look at.

        This replaces a test that asserted the *source text* of the defect — which passed
        only while the bug existed, required it, and said nothing about behaviour. Here a
        deliberately PARTIAL plan goes through a real build and the detail view must carry
        those rungs and no others."""
        part = _crowded_staircase()
        model = detect_part_model(part)
        full = compile_dimensions(model).ladder("step_height")
        assert full is not None and not full.representative and len(full.rungs) >= 3, (
            f"the fixture must have individual rungs to withhold (got {full})"
        )

        # Three of five: enough rungs left that the legibility gate still drops one and the
        # escalation still fires. Withholding more suppresses the detail request itself, and
        # the test then passes by drawing nothing — which is how its first version was
        # vacuous against the very bug it targets.
        keep = full.rungs[:3]
        partial = RenderableDimensionPlan(ladders=(replace(full, rungs=tuple(keep)),))

        def _partial(*_a, **_kw):
            return partial

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("draftwright.annotations.orchestrator.compile_dimensions", _partial)
            dwg = build_drawing(part, title="T", number="N", detail_view=True)

        detail = {
            n: getattr(o, "label", None) for n, o in dwg.iter_annotations() if "dim_detail" in n
        }
        assert detail, "the fixture must actually place a detail view, or this proves nothing"
        assert len(detail) <= len(keep), (
            f"the detail view drew {len(detail)} step dims from a plan approving "
            f"{len(keep)} — it is rebuilding the ladder from the model: {sorted(detail)}"
        )
        approved_labels = {r.final_label for r in keep}
        assert set(detail.values()) <= approved_labels, (
            f"the detail view drew {sorted(set(detail.values()) - approved_labels)}, which "
            "the compiler did not approve"
        )

    def test_rotational_furniture_cannot_rebuild_withheld_diameters(self, monkeypatch):
        """Centrelines survive, but an OD and bore absent from the plan stay absent.

        The legacy renderer asserted that every raw rotational diameter remained in
        `DimensionGroup`, then used `rot.od` / `rot.bores` to build its geometry. That
        made suppression an error rather than a supported compiler decision."""
        part = Cylinder(15, 10) - Pos(0, 0, 5) * Cylinder(4, 5) - Pos(0, 0, -5) * Cylinder(2.5, 20)
        model = detect_part_model(part)
        full = compile_dimensions(model)
        rotational = full.of_kind("rotational")
        assert len(rotational) == 1
        group = rotational[0]
        bores = [d for d in group.dims if d.role == "bore"]
        assert len(bores) >= 2, "fixture must provide an OD and two independently withheld bores"

        # Withhold the OD and the first bore; approve only the second bore.
        partial_group = replace(group, dims=(bores[1],))
        partial = replace(
            full,
            groups=tuple(partial_group if g is group else g for g in full.groups),
        )
        monkeypatch.setattr(
            "draftwright.annotations.orchestrator.compile_dimensions",
            lambda *_a, **_kw: partial,
        )

        dwg = build_drawing(part)
        assert "dim_od" not in dwg.annotations()
        assert [o.label for n, o in dwg.iter_annotations() if n.startswith("ldr_z")] == [
            f"ø{bores[1].value_text}"
        ]
        assert {"centerline_front", "centerline_side"} <= set(dwg.annotations())

    def test_the_migration_guard_measures_the_CONTRACT_not_the_signature(self):
        """How far the boundary actually reaches, by what each renderer is HANDED.

        The first version of this counted `render_*` functions taking `model` and reported
        the migration nearly complete. That measured the wrong property: a renderer taking
        legacy `groups` receives `PlannedDimension`s whose `suppressed` is still an advisory
        boolean it can ignore — which is the exact surface eight #921 rounds found being
        ignored. Not naming a parameter `model` is not the same as having crossed the
        boundary (#923 review round 4).

        So classification is by contract: `plan` means approved entries only; `groups`
        means the advisory surface; `model` means raw inventory. The pending lists are
        pinned so the count cannot drift, and so finishing is a visible event rather than
        an assumption.
        """
        by_contract: dict[str, list[str]] = {"plan": [], "groups": [], "model": []}
        for mod in ("from_model", "holes"):
            src = (
                pathlib.Path(__file__).resolve().parents[1]
                / "src"
                / "draftwright"
                / "annotations"
                / f"{mod}.py"
            ).read_text(encoding="utf-8")
            for node in ast.parse(src).body:
                if not isinstance(node, ast.FunctionDef) or not node.name.startswith("render_"):
                    continue
                args = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
                for contract in ("plan", "groups", "model"):
                    if contract in args:
                        by_contract[contract].append(node.name)
                        break

        assert sorted(by_contract["plan"]) == [
            "render_boss_diameters",
            "render_boss_heights",
            "render_chamfers",
            "render_diameters",
            "render_envelope",
            "render_fillets",
            "render_flats",
            "render_grooves",
            "render_height_ladder",
            "render_locations",
            "render_plates",
            "render_pocket_patterns",
            "render_pockets",
            "render_rotational",
            "render_slot_patterns",
            "render_slots",
            "render_step_lengths",
            "render_step_positions",
        ], "the migrated set changed — update this and the ADR's inventory together"

        assert by_contract["groups"] == [], (
            f"no renderer may take the advisory surface: {sorted(by_contract['groups'])}"
        )

        # `render_gdt` and `render_pmi` draw AUTHOR-SUPPLIED text, not generated measurements:
        # a control frame's tolerance and a PMI record's label are written by the script (or
        # by the STEP file) and rendered verbatim, with no `DimParameter` to plan or suppress.
        # They take the model because that is where those records live, which is a different
        # thing from reconstructing a withheld measurement.
        assert sorted(by_contract["model"]) == [
            "render_gdt",
            "render_pmi",
        ], f"the raw-inventory set changed: {sorted(by_contract['model'])}"

    def test_the_pending_dimensional_paths_are_the_ones_the_adr_names(self):
        """The ADR and the code must agree about what has NOT crossed.

        A prefix list of annotation names is not a semantic type system, so the two places
        that record the remaining gap — `_PENDING_VALUE_CARRYING` here and the ADR's
        inventory — are checked against each other. Naming the exception is what stops
        "everything else is covered" becoming an assumption nobody rechecks."""
        adr = (
            pathlib.Path(__file__).resolve().parents[1]
            / "docs"
            / "adr"
            / "0016-declared-dimensioning-intent.md"
        ).read_text(encoding="utf-8")
        assert "Hole callouts (`hc_`) remain on the legacy surface" in adr, (
            "the ADR stopped naming the one renderer still on the advisory surface — it "
            "must list what has NOT crossed the boundary, not just what has"
        )
        assert "Pattern pitch" in adr, "pitch dims dropped out of the inventory"
        assert "Slot positions" in adr, "slot positions dropped out of the inventory"
        assert "#883 is not a blocker" in adr, (
            "the ADR must say why locations no longer wait on #883 — otherwise the next "
            "reader re-derives the blocker that was removed"
        )
        assert set(_PENDING_VALUE_CARRYING) == {"hc_", "pmi_"}, (
            "the pending list changed; update the ADR inventory in the same commit"
        )


# ─── #946: one addressable result, and nothing outside it ────────────────────────────────


def test_every_approved_collection_is_addressable():
    """A new compiler-owned dimension category cannot skip generated scripts.

    `RenderableDimensionPlan._ADDRESSABLE` names the fields `addressable()` walks, and
    `sheet_emit._mirrored_requests` is a serialisation of that walk. Before #946 the emitter
    assembled its answer from three sources, so a category the compiler grew rendered
    correctly and stayed invisible to scripts until someone remembered to extend it — the
    failure mode stated in the issue, one level up from where ADR 0016 Amendment 1 fixed it
    for renderers.

    This makes the roster fail closed: add a collection of approved content and it must be
    walked, or excluded here on purpose with a reason. `diagnostics` is the one exclusion —
    it is what was NOT approved and has its own contract.
    """
    import dataclasses

    from draftwright.model.compiled import RenderableDimensionPlan

    excluded = {"diagnostics": "what was NOT approved; #939's floor consumes it separately"}
    roster = RenderableDimensionPlan._ADDRESSABLE
    carried = {f.name for f in dataclasses.fields(RenderableDimensionPlan)}

    unaccounted = carried - set(roster) - set(excluded)
    assert not unaccounted, (
        f"{sorted(unaccounted)} carry compiled content that `addressable()` never walks, so "
        "a script would silently omit them; add them to `_ADDRESSABLE` or exclude them here "
        "with a reason"
    )
    # ...and the roster names REAL fields with REAL adapters. Comparing name sets alone let a
    # listed-but-unimplemented field through: adding one only made `unaccounted` smaller, and
    # a canary that added `"callouts"` with no adapter passed this test while `addressable()`
    # would raise on the first call (#975 review, caught by canarying the guard itself).
    invented = set(roster) - carried
    assert not invented, f"{sorted(invented)} are in `_ADDRESSABLE` but are not plan fields"
    missing_adapters = [
        n for n in roster if not hasattr(RenderableDimensionPlan, f"_addressable_{n}")
    ]
    assert not missing_adapters, (
        f"{missing_adapters} are rostered with no `_addressable_<name>` adapter, so "
        "`addressable()` raises rather than walking them"
    )
    # And it runs — the roster is dispatched through, so this is what proves the wiring.
    assert isinstance(RenderableDimensionPlan().addressable(), tuple)


def test_the_emitter_reads_only_the_addressable_result():
    """`_mirrored_requests` is a serialisation of ONE compiler result (#946 acceptance).

    Checked structurally rather than behaviourally: the behavioural corpus already proves the
    emitted set matches, and would keep passing if a second source were reintroduced that
    happened to agree today. What must not come back is the ASSEMBLY.
    """
    import ast as _ast
    import inspect as _inspect
    import textwrap

    from draftwright import sheet_emit

    tree = _ast.parse(textwrap.dedent(_inspect.getsource(sheet_emit._mirrored_requests)))
    # Names the function actually USES, from the AST — not a substring search over the source,
    # which matched the comment explaining what this replaced and so failed on its own history.
    used = {n.id for n in _ast.walk(tree) if isinstance(n, _ast.Name)} | {
        n.attr for n in _ast.walk(tree) if isinstance(n, _ast.Attribute)
    }
    assert "addressable" in used, "the mirror no longer reads the compiler's addressable set"
    reassembled = used & {"plan_dimensions", "plan_locations", "locations", "units"}
    assert not reassembled, (
        f"`_mirrored_requests` reads {sorted(reassembled)} again — that is the three-source "
        "assembly #946 replaced, and it is how a compiler-owned category goes missing"
    )


def test_the_completeness_gate_compiles_the_original_not_the_mirror_model():
    """The `model` / `declared` asymmetry in `sheet_emit`, pinned directly (#975 review).

    `_mirrored_requests` compiles the DECLARED mirror model, so a synthesised envelope can
    supply a nameable `height.length`. `unmirrored_dimensions` compiles the ORIGINAL, because
    the approved drawing content is what it checks — compiling the mirror model would treat
    that envelope's width and depth as obligations the emitter deliberately never names.

    Right today, and exactly the kind of invariant that survives a careless edit while the
    corpus stays green: the flange fixtures cover it only as a consequence.
    """
    # The mirror corpus's `flange (no envelope feature)` — a part with no EnvelopeFeature that
    # still gets an overall-height ladder, which is the only shape that reaches the synthesis.
    # A plain turned body does NOT: its rotational OD conveys the height, so no ladder exists.
    from build123d import Align, Axis

    from draftwright.model.compiled import compile_dimensions, resolve_feature
    from draftwright.sheet_emit import mirror_model, unmirrored_dimensions

    part = Cylinder(21, 4)
    for x in (-18, 18):
        for y in (-18, 18):
            part += Pos(x, y, 2) * Box(10, 10, 4, align=(Align.CENTER,) * 3)
    part = part + Pos(0, 0, 2) * Cylinder(15.5, 12) + Pos(0, 0, 10) * Cylinder(12.5, 12)
    part -= Cylinder(8, 30)
    for x in (-18, 18):
        for y in (-18, 18):
            part -= Pos(x, y, 0) * Cylinder(2, 10)
    model = detect_part_model(part.rotate(Axis.X, 90))
    assert not any(f.kind == "envelope" for f in model.features), "fixture stopped proving this"

    declared, synthesised = mirror_model(model)
    assert synthesised is not None, "the fixture no longer exercises the synthesised envelope"

    # Compiling the MIRROR model demands the synthesised envelope's width and depth...
    roles = {
        i.role
        for i in compile_dimensions(declared).addressable()
        if resolve_feature(i.ref) is synthesised
    }
    assert {"width.length", "depth.length"} <= roles, (
        "the synthesised envelope no longer carries width/depth, so this test no longer "
        "distinguishes the two models — re-point it at whatever now differs"
    )
    # ...which the emitter never names, so the gate must not ask for them.
    assert unmirrored_dimensions(model) == [], (
        "the completeness gate reports the synthesised envelope's width/depth as missing — "
        "it is compiling the mirror model rather than the original"
    )
