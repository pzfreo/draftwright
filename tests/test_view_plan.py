"""ADR 0018 slice 1: the view plan is a value, it is the one owner, and it changed nothing.

The ADR's required evidence for this slice is three claims, and this file is each of them:

    A no-behaviour-change slice represents the current four views through `ViewSpec` and
    `ResolvedViewPlan` and preserves representative rendered semantics.

    Authored `ViewConstraints` cannot be mistaken for an immutable `ResolvedViewPlan`.

    `ResolvedViewPlan` has one typed `BuildState` attachment and a read-only `Drawing` surface;
    structural guards reject another writer or ad-hoc private cache.

The second is partly ahead of this slice — `ViewConstraints` does not exist yet — so what is
guarded here is the half that does: a resolved plan cannot be edited in place or rebound, which
is the property that makes the request/result split hold when constraints arrive.
"""

from __future__ import annotations

import dataclasses

import pytest
from build123d import Box, Cylinder, Pos, Rot

from draftwright.builder import build_drawing
from draftwright.view_plan import (
    ResolvedViewPlan,
    ViewPlacement,
    ViewSpec,
    resolve_from_analysis,
    third_angle_principals,
)


@pytest.fixture(scope="module")
def built():
    return build_drawing(
        Box(120, 80, 20) - Pos(-30, 10, 0) * Cylinder(4, 40), title="T", number="N"
    )


class TestThePlanDescribesWhatWasActuallyBuilt:
    def test_every_view_the_drawing_has_is_in_the_plan_and_the_reverse(self, built):
        """The claim that makes the plan worth having: it is not a parallel description.

        A record of the topology that can disagree with the topology is worse than no record,
        because the later slice that varies the plan would vary a fiction. The iso is the one
        spec with no placement — it is fitted after the sheet is settled — so it is asserted
        present as a spec and absent from the placements, rather than quietly excluded.
        """
        plan = built.view_plan
        assert plan is not None, "a built drawing has no view plan"

        # The comparison is to the views resolved AT BUILD TIME, and the filter is a stated
        # limitation rather than a convenience: sections and details are created later by
        # annotation passes (`_add_view("section_aa", …)`, `_resolve_details`) and never enter
        # the plan, so a plain `== set(built.views)` would fail on any sectioned part. The first
        # version of this test filtered silently, which made "every view is in the plan" true by
        # construction. `test_sections_and_details_are_not_in_the_plan_yet` pins the gap instead.
        planned_at_build = {
            name for name in built.views if name in {"front", "plan", "side", "iso"}
        }
        assert {spec.name for spec in plan.specs} == planned_at_build

        assert plan.principal_names == ("front", "plan", "side")
        assert [spec.name for spec in plan.of_kind("pictorial")] == ["iso"]
        assert set(plan.placements) == {"front", "plan", "side"}, (
            "the iso has a placement, but it is fitted after the sheet is settled, so any "
            "placement recorded for it at resolve time is a claim the engine cannot honour"
        )

    def test_each_placement_is_where_the_view_was_actually_drawn(self, built):
        """The numbers, not just the names.

        `view_bounds` is the projected silhouette and the placement is the reserved block, so
        they are not equal — the block is sized for the view's extent at scale. What must hold
        is that the drawn geometry sits inside the block its plan reserved; a placement that
        does not contain its own view is a bookkeeping error that would mislead every later
        layout decision.
        """
        plan = built.view_plan
        for name, place in plan.placements.items():
            x0, y0, x1, y1 = place.bounds
            bx0, by0, bx1, by1 = built.view_bounds(name)
            assert x0 - 0.5 <= bx0 and bx1 <= x1 + 0.5, f"{name} overflows its block in x"
            assert y0 - 0.5 <= by0 and by1 <= y1 + 0.5, f"{name} overflows its block in y"

    def test_the_plan_records_the_sheet_and_scale_the_drawing_was_built_at(self, built):
        plan = built.view_plan
        assert plan.scale == built.scale
        assert plan.page == (built.page_w, built.page_h)


class TestTheResolvedPlanCannotBeMistakenForARequest:
    def test_a_resolved_plan_is_frozen(self, built):
        with pytest.raises(dataclasses.FrozenInstanceError):
            built.view_plan.scale = 2.0  # type: ignore[misc]

    def test_its_placements_cannot_be_edited_in_place(self, built):
        """The mapping too, not just the dataclass.

        A frozen dataclass wrapping a plain dict is frozen in name only — `plan.placements[...]
        = ...` would edit the resolved answer through the immutable object, which is exactly the
        "one mutable object for authored constraints and resolved output" ADR 0018 rejects.
        """
        with pytest.raises(TypeError):
            built.view_plan.placements["front"] = ViewPlacement(0, 0, 1, 1)  # type: ignore[index]

    def test_the_drawing_surface_is_read_only(self, built):
        with pytest.raises(AttributeError):
            built.view_plan = None  # type: ignore[misc]

    def test_a_plan_refuses_duplicate_view_names(self):
        spec = ViewSpec(name="front", kind="principal", page_axes=("x", "z"))
        with pytest.raises(ValueError, match="duplicate view names"):
            ResolvedViewPlan(specs=(spec, spec), placements={}, scale=1.0, page=(420.0, 297.0))

    def test_a_spec_refuses_an_unknown_kind(self):
        """The kind is what makes "which views should exist" answerable, so it is closed.

        An open string would let a later planner invent a kind nothing knows how to weigh, and
        the first thing that reads `kind` to decide whether a view may be dropped would treat
        the unknown one as droppable or as required, silently.
        """
        with pytest.raises(ValueError, match="unknown view kind"):
            ViewSpec(name="x", kind="decorative")


class TestTheRepresentationChangedNothing:
    def test_the_specs_are_the_third_angle_set_the_engine_has_always_built(self):
        specs = third_angle_principals()
        assert [spec.name for spec in specs] == ["front", "plan", "side"]
        assert [spec.page_axes for spec in specs] == [("x", "z"), ("x", "y"), ("y", "z")]
        assert {spec.kind for spec in specs} == {"principal"}

    def test_resolving_reads_the_analysis_the_engine_already_computed(self, monkeypatch):
        """The bridge, asserted: the plan is the same numbers `Analysis` already held.

        Worth stating as a test rather than as a docstring claim, because when the resolver
        starts CHOOSING rather than reading, this is what has to change and say so.

        The `Analysis` is captured at the seam that is handed it, NOT read off the drawing:
        `test_private_test_attr_reads` ratchets test-side `_analysis` reads strictly downward,
        and this would have grown the ceiling by one.
        """
        from draftwright import builder as builder_module

        seen: dict = {}
        real = builder_module.resolve_from_analysis

        def capture(analysis):
            seen["analysis"] = analysis
            return real(analysis)

        monkeypatch.setattr(builder_module, "resolve_from_analysis", capture)
        drawing = build_drawing(Box(80, 60, 20), title="T", number="N")
        analysis = seen["analysis"]

        assert resolve_from_analysis(analysis).placements == {
            "front": ViewPlacement(analysis.FV_X, analysis.FV_Y, analysis.fv_hw, analysis.fv_hh),
            "plan": ViewPlacement(analysis.PV_X, analysis.PV_Y, analysis.fv_hw, analysis.pv_hh),
            "side": ViewPlacement(analysis.SV_X, analysis.SV_Y, analysis.sv_hw, analysis.fv_hh),
        }
        assert resolve_from_analysis(analysis) == drawing.view_plan


@pytest.mark.slow  # a CTC fixture build, twice (#153)
@pytest.mark.timeout(600)  # Two OCC builds exceed the global 300 s cap under xdist load (#1266).
def test_the_repack_loop_really_consumes_the_plan():
    """The one consumer whose re-routing the golden corpus does NOT cover.

    `compose._view_geom` feeds only the measure-and-repack loop, and no golden fixture triggers
    a repack — so pointing it at the view plan was, at first, a change nothing could verify:
    emptying its result left every golden and every repack-seam test passing. That is precisely
    the kind of change this repository has learned to distrust, so it is proven here instead.

    Two claims, and both are needed:

    1. the map is load-bearing at all — CTC-03 AP203 is the fixture where the repack actually
       uses it, and its drawing changes if the map is emptied. Without this the second claim
       would be satisfied by a function whose output nothing reads;
    2. reading it from the plan produces the same drawing as reading it from the `Analysis`
       fields directly. Verified against `main` at the time of writing by comparing page, scale,
       every annotation name and label, and every view's bounds: byte-identical, 3093 bytes of
       signature. This test re-checks the first claim, which is the half that can rot.
    """
    from draftwright import builder as builder_module

    fixture = "tests/fixtures/nist_ctc_03_asme1_ap203.stp"

    def signature(drawing):
        return (
            round(drawing.page_w),
            round(drawing.page_h),
            drawing.scale,
            tuple(sorted(drawing.registry.names())),
            tuple(
                (name, tuple(round(v, 4) for v in (drawing.view_bounds(name) or ())))
                for name in sorted(drawing.views)
            ),
        )

    real = signature(build_drawing(fixture))

    # PERTURB the values, do not empty the map. An empty map raises `KeyError` inside
    # `_measure_blocks` — which proves the function is called, not that its ANSWER is used, and
    # the first version of this test mistook one for the other. Halving the half-extents keeps
    # every key and every consumer working, and changes only what the repack measures.
    original = builder_module._view_geom

    def halved(analysis):
        return {
            name: (cx, cy, hw * 0.5, hh * 0.5)
            for name, (cx, cy, hw, hh) in original(analysis).items()
        }

    builder_module._view_geom = halved
    try:
        perturbed = signature(build_drawing(fixture))
    finally:
        builder_module._view_geom = original

    assert perturbed != real, (
        "changing the measured view geometry changes nothing on this fixture, so it no longer "
        "exercises the repack loop and the plan's only uncovered consumer is unverified again"
    )


class TestPerViewRequirementCoverage:
    """ADR 0018 slice 2: what each view carries, and what would go with it.

    Selection cannot happen until something can answer "what is lost if this view goes", and
    nothing could. This is that answer, and the ADR names the two cases it must separate:

        Removing a truly redundant view retains every requirement and reduces the selected
        footprint.

        Removing a visually similar but semantically necessary view is rejected by an
        asymmetric counterexample.

    The pair below is that asymmetry. Both parts have three principal views showing broadly
    similar silhouettes; on one, a view carries nothing of its own, and on the other every view
    does. A rule that cannot tell them apart would either strip a needed view or never strip
    anything.
    """

    def test_a_rotational_plate_has_one_view_carrying_nothing_of_its_own(self):
        """The redundant case, and the reason the thin plate needs an A1.

        On an X-axis rotational part the front and plan are the same edge-on projection. The
        engine draws both because the topology is fixed, and the plan ends up carrying no
        measurement at all — 217 mm of sheet for a repeat of its neighbour.
        """
        from test_issue_1130_view_planning_evidence import thin_rotational_plate

        from draftwright.view_plan import view_coverage, views_carrying_nothing_exclusively

        drawing = build_drawing(thin_rotational_plate(), title="T", number="N")
        coverage = view_coverage(drawing)

        assert views_carrying_nothing_exclusively(drawing) == ("plan",)
        assert coverage["plan"].carries == frozenset(), (
            f"the plan view now carries {len(coverage['plan'].carries)} measurements, so this "
            "is no longer the redundant-view case"
        )
        # The other two are not candidates, and carry real content — otherwise "one candidate"
        # would be satisfied by a drawing that had simply lost its dimensions.
        assert coverage["front"].exclusive and coverage["side"].exclusive
        assert len(coverage["side"].carries) > 5

    @pytest.mark.parametrize(
        ("name", "build", "expected"),
        [
            # A plain turned part needs ONE longitudinal view: the diameters carry the ⌀
            # symbol, so nothing requires an end view, and BOTH radial views come back empty.
            (
                "stepped shaft",
                lambda: Rot(0, 90, 0) * (Cylinder(10, 40) + Pos(0, 0, 40) * Cylinder(6, 30)),
                ("plan", "side"),
            ),
            (
                "grooved shaft",
                lambda: (
                    (Rot(0, 90, 0) * Cylinder(12, 80))
                    - Rot(0, 90, 0) * Pos(0, 0, 20) * (Cylinder(12, 6) - Cylinder(9, 6))
                ),
                ("plan", "side"),
            ),
            # Turned about Z rather than X: the redundant pair follows the AXIS, which is what
            # a rule keyed on view NAMES would get wrong.
            (
                "Z-axis shaft",
                lambda: Cylinder(10, 60) + Pos(0, 0, 60) * Cylinder(6, 20),
                ("plan", "side"),
            ),
        ],
    )
    def test_a_turned_part_leaves_its_radial_views_empty(self, name, build, expected):
        """The redundancy is structural for turned parts, not a property of one plate.

        A body of revolution looks the same from every radial direction, so the two views that
        differ only in radial direction carry nothing between them — measured here on three
        shafts, and the count is the drafting answer: a plain turned part is conventionally
        dimensioned on one longitudinal view.

        The contrast with the fixtures below is the useful part. A turned part carrying RADIAL
        features (a bolt circle, a hole pattern) needs its end view and only one candidate
        appears; a plain shaft has no radial content and both appear. Coverage separates those
        two without being told which is which.
        """
        from draftwright.view_plan import view_coverage, views_carrying_nothing_exclusively

        # Inspect the full baseline explicitly. Automatic planning now consumes exactly this
        # evidence and removes one of these empty radial projections, so observing the
        # candidate set requires retaining all three principals at this diagnostic seam.
        drawing = build_drawing(build(), title="T", number="N", _views=("front", "plan", "side"))
        assert views_carrying_nothing_exclusively(drawing) == expected
        coverage = view_coverage(drawing)
        assert coverage["front"].exclusive, (
            f"{name}: the longitudinal view carries nothing either, so this part is not "
            "dimensioned at all and the assertion above is vacuous"
        )

    def test_a_turned_part_with_radial_features_keeps_its_end_view(self):
        """The asymmetry WITHIN turned parts, which is the sharper of the two.

        The thin plate and the shafts are all rotational; the plate's end view carries its bolt
        circle and hole pattern, so only one radial view is spare rather than both. A rule that
        said "turned parts need one view" would strip the end view off this one and lose the
        pattern.
        """
        from test_issue_1130_view_planning_evidence import thin_rotational_plate

        from draftwright.view_plan import view_coverage, views_carrying_nothing_exclusively

        drawing = build_drawing(thin_rotational_plate(), title="T", number="N")
        coverage = view_coverage(drawing)

        assert views_carrying_nothing_exclusively(drawing) == ("plan",)
        assert len(coverage["side"].exclusive) > 5, (
            "the end view no longer carries the radial features, so it no longer distinguishes "
            "this case from a plain shaft"
        )

    def test_a_view_carrying_no_annotations_at_all_is_reported(self):
        """The bug this class had until turned parts were tried.

        Coverage was built by walking annotations, so a view with NO annotations never entered
        the map and could never be reported — silently dropping exactly the most redundant
        views. On a stepped shaft the `side` view carries not one measurement, and only `plan`
        was reported. The map is seeded from the drawing's views now.
        """
        from draftwright.view_plan import view_coverage

        drawing = build_drawing(
            Rot(0, 90, 0) * (Cylinder(10, 40) + Pos(0, 0, 40) * Cylinder(6, 30)),
            title="T",
            number="N",
            _views=("front", "plan", "side"),
        )
        coverage = view_coverage(drawing)

        assert {"front", "plan", "side"} <= set(coverage), (
            f"a view the drawing has is missing from its coverage: {sorted(map(str, coverage))}"
        )
        assert coverage["side"].carries == frozenset()

    def test_a_prismatic_plate_has_none(self):
        """The counterexample. Three views, every one carrying something only it carries.

        Without this the redundancy rule could be "drop the plan view", which is true of the
        fixture above and false in general.
        """
        from draftwright.view_plan import view_coverage, views_carrying_nothing_exclusively

        part = (
            Box(120, 80, 12)
            - Pos(-30, 10, 0) * Cylinder(4, 40)
            - Pos(30, -10, 0) * Cylinder(4, 40)
        )
        drawing = build_drawing(part, title="T", number="N")
        coverage = view_coverage(drawing)

        assert views_carrying_nothing_exclusively(drawing) == ()
        for name in ("front", "plan", "side"):
            assert coverage[name].exclusive, (
                f"{name} carries nothing exclusively on the counterexample, so the asymmetry "
                f"this pair exists to prove is gone: "
                f"{ {v: len(c.exclusive) for v, c in coverage.items()} }"
            )

    def test_coverage_is_read_from_what_the_sheet_actually_carries(self):
        """Through the ADR 0010 seam, not from the compiler's intentions.

        A measurement the compiler approved and no annotation drew must not appear as covered —
        that is the difference between "the plan wanted this" and "the drawing says this", and
        the whole value of the answer depends on it being the second.
        """
        from draftwright.view_plan import view_coverage

        drawing = build_drawing(Box(80, 60, 20), title="T", number="N")
        coverage = view_coverage(drawing)

        claimed_by_annotations = set()
        for name in drawing.registry.names():
            claimed_by_annotations |= set(drawing.registry.measurement_of(name) or ())
        from_coverage = set().union(*(cover.carries for cover in coverage.values()))
        assert from_coverage == claimed_by_annotations

    def test_an_exclusive_measurement_is_one_no_other_view_draws(self):
        """The arithmetic, on a constructed drawing rather than on whatever a part produces."""
        from types import SimpleNamespace

        from draftwright.view_plan import view_coverage

        shared, only_front = object(), object()
        registry = SimpleNamespace(
            names=lambda: ["a", "b", "c"],
            measurement_of=lambda n: {
                "a": (shared, only_front),
                "b": (shared,),
                "c": (),
            }[n],
        )
        drawing = SimpleNamespace(
            registry=registry,
            view_of=lambda n: {"a": "front", "b": "side", "c": "plan"}[n],
            view_plan=None,
        )
        coverage = view_coverage(drawing)

        assert coverage["front"].carries == frozenset({shared, only_front})
        assert coverage["front"].exclusive == frozenset({only_front})
        assert coverage["side"].exclusive == frozenset()
        assert coverage["plan"].carries_nothing_exclusively


class TestSectionsAndDetailsAreNotPlannedYet:
    """The gap this slice does not close, pinned so it cannot be forgotten or overstated.

    ADR 0018's `ViewSpec.kind` already admits `section` and `detail`, and the ADR's own "why
    now" cites #1190: the section "is not part of the scale/layout decision at all… placed
    opportunistically into whatever is left, which is why its presence tracked leftover space
    rather than need." That is still true. These views are created by annotation passes long
    after the plan is resolved, so the plan under-describes the drawing, and bringing them in is
    the work #1190 pointed at.
    """

    def test_a_section_view_exists_outside_the_plan(self):
        part = (
            Box(120, 80, 40)
            - Pos(0, 0, 10) * Cylinder(18, 40)
            - Pos(-40, 0, 0) * Cylinder(6, 60)
            - Pos(40, 0, 0) * Cylinder(6, 60)
        )
        drawing = build_drawing(part, title="T", number="N")
        assert "section_aa" in drawing.views, "precondition: this part is no longer sectioned"
        assert drawing.view_plan.spec("section_aa") is None, (
            "the section is in the plan now — good, but this test states the opposite and must "
            "be rewritten to assert the section participates in the layout decision"
        )

    def test_a_detail_view_that_redraws_dropped_marks_is_never_a_candidate(self):
        """The trap, and the reason coverage fails closed.

        A detail exists to redraw the marks the main view could not fit. Those marks share their
        parameter's `DimensionId` with the ones that DID fit (ADR 0016 Amdt 3 — an id names a
        parameter, not a mark), so by id alone the detail carries nothing exclusively. On
        `_crowded_staircase` it draws three step heights and every one of them reads as already
        covered by the front view.

        Answering "droppable" there would lose three dimensions off the sheet — precisely ADR
        0018's "visually similar but semantically necessary view", reached by arithmetic. So
        coverage reports the ids as INDETERMINATE and the view as not a candidate. Per-mark
        identity (ADR 0019 §3) is what would let this be answered rather than declined.
        """
        from test_issue_1215_no_approved_tolerance_is_dropped import _PARTS

        from draftwright.view_plan import view_coverage, views_carrying_nothing_exclusively

        # Pin the small sheet that makes the recovery detail necessary. Automatic layout may
        # otherwise escalate to a larger standard sheet and correctly eliminate that detail.
        drawing = build_drawing(_PARTS["crowded_staircase"](), title="T", number="N", page="A4")
        assert "detail_a" in drawing.views, "precondition: this part no longer details"

        cover = view_coverage(drawing)["detail_a"]
        drawn_in_detail = [
            name for name in drawing.registry.names() if drawing.view_of(name) == "detail_a"
        ]
        assert len(drawn_in_detail) >= 3, (
            f"the detail draws {len(drawn_in_detail)} marks; fewer than three and it no longer "
            "demonstrates several marks collapsing onto one id"
        )
        assert len(cover.carries) == 1, (
            f"{len(drawn_in_detail)} marks no longer collapse to one id ({len(cover.carries)}), "
            "so per-mark identity may have landed and this can become an exclusivity assertion"
        )
        assert cover.exclusive == frozenset(), "precondition: by id alone it looks droppable"
        assert cover.indeterminate, "the collapse is not being reported as unanswerable"
        assert not cover.carries_nothing_exclusively
        assert "detail_a" not in views_carrying_nothing_exclusively(drawing)


class TestTheLayoutCandidate:
    """ADR 0018 §5: page, scale, views and arrangement as ONE constrained choice.

    `compose.choose_scale` has always been the planner's candidate loop — build a list of
    possibilities, return the first that fits — but the possibility was an anonymous
    `(scale, page_w, page_h, tb_w)` tuple, so two of the ADR's four dimensions had nowhere to
    live: the view set stayed fixed across three modules and the arrangement stayed a sentence
    in a docstring.

    These pin the structure, not the values. Nothing varies yet — every candidate is the
    third-angle three in the `columns` arrangement — and that is deliberate: this slice makes
    varying them an addition to a generator rather than a rewrite of the loop.
    """

    def test_a_candidate_carries_all_four_dimensions(self):
        from draftwright.view_plan import LayoutCandidate, third_angle_view_names

        candidate = LayoutCandidate(
            views=third_angle_view_names(), scale=1.0, page=(420.0, 297.0), title_block_width=150.0
        )
        assert candidate.views == ("front", "plan", "side")
        assert candidate.arrangement == "columns"
        assert candidate.legacy_tuple == (1.0, 420.0, 297.0, 150.0)

    def test_the_candidate_view_set_is_the_one_the_resolver_uses(self):
        """One source for "which views", so a candidate and a resolved plan cannot disagree.

        If the generator's idea of the view set drifted from the resolver's, a candidate could
        be judged feasible for a layout the builder never produces — which is the failure mode
        of having the topology written down in more than one place, and the reason this slice
        exists.
        """
        from draftwright.view_plan import third_angle_principals, third_angle_view_names

        assert third_angle_view_names() == tuple(spec.name for spec in third_angle_principals())

    def test_an_infeasible_candidate_says_why_rather_than_returning_false(self):
        """ADR 0018 §6, the half of it this slice delivers.

        The terminal behaviour is unchanged — `choose_scale` still falls back to the last
        candidate with a warning, which the ADR wants replaced by a `plan_infeasible` result.
        What changes is that a rejection is now a value carrying its reason, which is what a
        diagnostic would have to print; a bare `False` could never become one.
        """
        from draftwright.view_plan import LayoutCandidate, candidate_is_feasible

        candidate = LayoutCandidate(
            views=("front", "plan", "side"),
            scale=1.0,
            page=(210.0, 297.0),
            title_block_width=120.0,
        )
        assert candidate_is_feasible(candidate, lambda _c: True) is None

        verdict = candidate_is_feasible(candidate, lambda _c: False)
        assert verdict is not None
        assert verdict.reason == "layout_does_not_fit"
        assert verdict.candidate is candidate
        assert "columns" in verdict.detail and "3 views" in verdict.detail

    def test_the_fit_predicate_is_the_callers_not_this_leafs(self):
        """The rank-0 boundary, asserted.

        Feasibility needs the strip estimates and font metrics that live in `compose`; this
        module must not reach up for them. So the predicate arrives as an argument, and the leaf
        stays a leaf — the same reason `view_coverage` is duck-typed on the drawing.
        """
        from draftwright.view_plan import LayoutCandidate, candidate_is_feasible

        seen = []
        candidate = LayoutCandidate(
            views=("front",), scale=2.0, page=(297.0, 210.0), title_block_width=120.0
        )
        candidate_is_feasible(candidate, lambda c: seen.append(c) or True)
        assert seen == [candidate], "the predicate was not given the candidate to judge"
