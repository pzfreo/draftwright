"""ADR 0016 augmenting intent — `Sheet.add_dimension()` (#872).

`add_dimension(feature, role)` asks the planner to carry one more measurement. It is
*referential*: it names a feature and a role and carries no number, so the value still
comes from the geometry and a size lives in exactly one place. What it changes is
**selection**, not derivation — a request can never introduce a number the part does
not have.

The invariants worth pinning, in the order they bite:

- **idempotence** — requesting what the planner already emits is a no-op, not an error
  (a script must be able to ask without first knowing the rule set's mind);
- **order independence** — declaring the augment before the source reads the same as
  after, so the gate lives at `build()` rather than in the verb;
- **the handle survives a later size verb** — intents are token-keyed and materialised
  against the FINAL features, like `_tolerances`;
- **ambiguity raises** rather than guessing between two same-role measurements.
"""

from __future__ import annotations

import pytest
from build123d import Box, Cylinder, Pos, Rot

from draftwright import Sheet
from draftwright.model import ControlFrame, Frame, HoleFeature, PartModel
from draftwright.model.ir import RequestedDimension
from draftwright.model.planner import plan_dimensions

_BBOX = Box(80, 50, 12).bounding_box()


def _part():
    return Box(90, 60, 20) - Pos(0, 0, 4) * Cylinder(5, 30)


def _plan(*requests, feature=None):
    """Plan one hole, optionally with `add_dimension`-style requests against it."""
    hole = feature or HoleFeature(Frame((0, 0, 6), "z"), 8.0, depth=10.0, through=False)
    model = PartModel(
        bbox=_BBOX,
        orientation="prismatic",
        features=[hole],
        requested_dimensions=tuple(RequestedDimension(hole, *r) for r in requests),
    )
    (group,) = plan_dimensions(model)
    return group


class TestPlannerIntentInput:
    def test_a_request_changes_selection_not_derivation(self):
        """The value always comes from the geometry — a request can only decide whether
        a measurement is carried, never what it reads."""
        plain = _plan()
        asked = _plan(("bore",))
        assert [m.param.value for u in plain.units for m in u.members] == [
            m.param.value for u in asked.units for m in u.members
        ]

    def test_requesting_what_the_planner_already_emits_is_a_no_op(self):
        """The #872 idempotence gate. A caller should be able to ask for a dimension
        without first knowing whether the rule set volunteers it — otherwise the verb
        leaks the planner's internals into every script that uses it."""
        plain = _plan()
        asked = _plan(("bore",))
        assert [u.id for u in plain.units] == [u.id for u in asked.units]
        assert all(not m.suppressed for u in asked.units for m in u.members)

    def test_a_request_resolves_by_bare_role_or_by_dotted_identity(self):
        """Both call-site vocabularies reach the planner. ADR 0016 leaves the role
        vocabulary open; the dotted form is the identity #871 built, the bare role is
        what a caller reaches for when it is unambiguous."""
        from draftwright.model.planner import _request_for

        hole = HoleFeature(Frame((0, 0, 6), "z"), 8.0, depth=10.0, through=False)
        params = {p.parameter_id: p for p in hole.parameters()}

        def resolved(role):
            model = PartModel(
                bbox=_BBOX,
                orientation="prismatic",
                features=[hole],
                requested_dimensions=(RequestedDimension(hole, role),),
            )
            return {pid: _request_for(model, hole, p) is not None for pid, p in params.items()}

        assert resolved("bore") == {"bore.diameter": True, "bore.depth": True}
        assert resolved("bore.depth") == {"bore.diameter": False, "bore.depth": True}


class TestSheetSurface:
    def test_the_handle_records_the_request(self):
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        intent = sheet.add_dimension(bore, "bore.diameter")
        # The CANONICAL spelling is recorded, not what was typed (#963).
        assert intent._entry["role"] == "bore.diameter"
        assert intent._entry["token"] == sheet._token_at(0)

    def test_the_handle_forwards_unknown_attributes_to_the_sheet(self):
        """Declare-then-chain: returning a handle must not break the fluent surface,
        the same contract `_Params` holds."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        chained = sheet.add_dimension(bore, "bore.diameter").hole(
            diameter=4, at=(20, 0, 20), axis="z"
        )
        assert chained is not None
        assert len(sheet.features) == 2

    def test_the_intent_survives_a_later_size_verb(self):
        """Intents are token-keyed and materialised at build, so a handle recorded
        before `.depth()` replaces the feature still resolves against the final one."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z")
        sheet.add_dimension(bore, "bore.diameter")
        bore.depth(12)  # replaces the frozen feature at that index
        (request,) = sheet._requested_dimensions()
        assert request.feature is sheet.features[0]
        assert request.feature.depth == 12

    def test_an_unknown_measurement_raises_and_names_what_is_available(self):
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        with pytest.raises(ValueError, match="no 'nonesuch' measurement"):
            sheet.add_dimension(bore, "nonesuch")

    def test_add_dimension_requires_a_dimension_source(self):
        """`add_dimension` augments the planner's set, so there must be one."""
        sheet = Sheet(_part(), title="T", number="N")  # deliberately no source
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.add_dimension(bore, "bore.diameter")
        with pytest.raises(ValueError, match="call auto_dimensions"):
            sheet.build()

    def test_the_source_may_be_declared_after_the_augment(self):
        """Order independence (ADR 0016). The gate is at build, not in the verb, so
        these two scripts must not disagree."""
        sheet = Sheet(_part(), title="T", number="N")  # the source arrives below
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.add_dimension(bore, "bore.diameter")
        sheet.auto_dimensions()
        assert sheet.build() is not None

    def test_a_sheet_with_no_source_at_all_raises(self):
        """#874's third error, and the breaking one. This test previously asserted the
        OPPOSITE — that `auto_dimensions()` was optional — and the mechanical migration
        rewrote its setup to call the verb while leaving its name and docstring intact, so it
        went on passing while proving nothing (#921 review). A gate with no test is worse
        than no gate, because the suite claims otherwise."""
        sheet = Sheet(_part(), title="T", number="N")
        sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        with pytest.raises(ValueError, match="does not say where its dimensions come from"):
            sheet.build()

    def test_the_no_source_error_names_BOTH_source_verbs(self):
        """The error is the only place a caller learns how to fix it, so it has to name every
        way to do so.

        It said "declare the complete set with dimension(feature, role) lines" — advice that
        is impossible to follow for a complete set that happens to be EMPTY, which is exactly
        why `authored_dimensions()` exists (#933 review). Accurate before that verb, stale
        after it, and stale error text about an API is worse than none: it reads as an
        exhaustive list.

        Asserted rather than left to review, so the verb stays discoverable from the failure
        a caller actually hits.
        """
        sheet = Sheet(_part(), title="T", number="N")
        sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        with pytest.raises(ValueError) as excinfo:
            sheet.build()
        message = str(excinfo.value)
        assert "auto_dimensions()" in message
        assert "authored_dimensions()" in message, "the error hides the empty-set route"

    def test_the_model_surface_is_gated_too(self):
        """`Sheet.model()` is the model the engine WOULD draw, so a sheet that cannot be built
        must not hand one out — otherwise `build_drawing(part, model=sheet.model())` walks
        straight around the check and the two public surfaces disagree about one sheet."""
        sheet = Sheet(_part(), title="T", number="N")
        sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        with pytest.raises(ValueError, match="does not say where its dimensions come from"):
            sheet.model()

    def test_declaring_both_sources_raises(self):
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.dimension(bore, "bore.diameter")
        with pytest.raises(ValueError, match="ONE dimension source"):
            sheet.build()

    def test_a_detected_build_refuses_an_authored_set(self):
        """`authored=` names DECLARED feature objects; detection builds its own, which no
        authored entry can match. Dropping it silently would revert the build to exactly
        the automatic dimensions the author was replacing (#921 review) — the `requested=`
        guard beside it has always said so, and this is the worse of the two to lose."""
        from draftwright import build_drawing
        from draftwright.model import declare
        from draftwright.model.ir import RequestedDimension

        part = _part()
        request = RequestedDimension(
            feature=declare.envelope(part), role="width", discriminator=None
        )
        with pytest.raises(ValueError, match="authored= names declared features"):
            build_drawing(part, authored=(request,))

    @pytest.mark.parametrize(
        "on_model,as_kwarg",
        [("requested_dimensions", "authored"), ("authored_dimensions", "requested")],
    )
    def test_both_sources_are_refused_however_they_arrive(self, on_model, as_kwarg):
        """The exclusion is a property of the EFFECTIVE model, not of one call's arguments.
        Either source can arrive two ways — as a keyword, or already carried by a supplied
        `PartModel` — so an argument-level guard sees only half of each combination and
        both of these mixed forms slipped through it (#921 review)."""
        from draftwright.builder import _coerce_model
        from draftwright.model import declare
        from draftwright.model.ir import PartModel, RequestedDimension

        part = _part()
        env = declare.envelope(part)
        request = RequestedDimension(feature=env, role="width", discriminator=None)
        model = PartModel(
            bbox=part.bounding_box(),
            orientation=None,
            features=[env],
            datums=[],
            **{on_model: (request,)},
        )
        with pytest.raises(ValueError, match="cannot have both"):
            _coerce_model(model, part, None, **{as_kwarg: (request,)})

    def test_a_partmodel_already_naming_both_sources_is_refused(self):
        """The public boundary rejects an invalid model even when no keyword adds to it."""
        from draftwright.builder import _coerce_model
        from draftwright.model import declare
        from draftwright.model.ir import PartModel, RequestedDimension

        part = _part()
        env = declare.envelope(part)
        request = RequestedDimension(feature=env, role="width", discriminator=None)
        model = PartModel(
            bbox=part.bounding_box(),
            orientation=None,
            features=[env],
            datums=[],
            requested_dimensions=(request,),
            authored_dimensions=(request,),
        )
        with pytest.raises(ValueError, match="cannot have both"):
            _coerce_model(model, part)

    def test_an_authored_entry_naming_a_feature_copy_raises(self):
        """Matching is by object identity on purpose — two equal-valued features are two
        distinct targets, so a part with two identical holes can dimension one of them
        (see `_request_for`). The cost is that an equal-but-distinct COPY matches nothing,
        and for an authored set a miss is not a no-op: it declares the complete set, so
        every measurement on that feature goes silently blank (#921 review round 4).

        Unreachable through `Sheet`, which materialises entries against the final
        features; reachable through the ADR 0011 public input, which is exactly where a
        caller assembles a `PartModel` by hand. So the miss says so."""
        from dataclasses import replace

        from draftwright.model import Frame, HoleFeature, PartModel, plan_dimensions
        from draftwright.model.ir import RequestedDimension

        hole = HoleFeature(Frame((0, 0, 10), "z"), 12.0, depth=8.0, through=False)
        clone = replace(hole)
        assert clone == hole and clone is not hole, "the fixture must be an equal COPY"
        model = PartModel(
            bbox=_part().bounding_box(),
            orientation="prismatic",
            features=[hole],
            authored_dimensions=(RequestedDimension(clone, "bore.diameter"),),
        )
        with pytest.raises(ValueError, match="not in model.features"):
            plan_dimensions(model)

    def test_an_authored_entry_naming_a_measurement_that_does_not_exist_raises(self):
        from draftwright.model import Frame, HoleFeature, PartModel, plan_dimensions
        from draftwright.model.ir import RequestedDimension

        hole = HoleFeature(Frame((0, 0, 10), "z"), 12.0, depth=8.0, through=False)
        model = PartModel(
            bbox=_part().bounding_box(),
            orientation="prismatic",
            features=[hole],
            authored_dimensions=(RequestedDimension(hole, "nonesuch"),),
        )
        with pytest.raises(ValueError, match="no 'nonesuch' measurement"):
            plan_dimensions(model)

    def test_a_valid_authored_entry_still_plans(self):
        """The control: the guard above must not reject the ordinary case."""
        from draftwright.model import Frame, HoleFeature, PartModel, plan_dimensions
        from draftwright.model.ir import RequestedDimension

        hole = HoleFeature(Frame((0, 0, 10), "z"), 12.0, depth=8.0, through=False)
        model = PartModel(
            bbox=_part().bounding_box(),
            orientation="prismatic",
            features=[hole],
            authored_dimensions=(RequestedDimension(hole, "bore.diameter"),),
        )
        marked = {pd.param.parameter_id: pd.suppressed for pd in plan_dimensions(model)[0].dims}
        assert marked == {"bore.diameter": False, "bore.depth": True}

    def test_from_part_can_be_taken_over_by_an_authored_set(self):
        """Detect the features, then declare exactly which of their measurements to draw.
        `from_part` chooses the automatic source on the caller's behalf, so this is the
        script changing its mind, not contradicting itself — and it is the natural way to
        take over a detected drawing. Requiring every feature to be redeclared by hand to
        reach suppression by omission would be a poor trade (#921 review round 6)."""
        from build123d import Box, Cylinder, Pos

        part = Box(100, 60, 20) - Pos(0, 0, 10) * Cylinder(5, 20)
        sheet = Sheet.from_part(part)
        hole = next(f for f in sheet.features if f.kind == "hole")
        sheet.dimension(hole, "bore.diameter")

        drawn = {n for n, _ in sheet.build().iter_annotations()}
        assert "hc_plan0" in drawn, "the authored callout"
        assert not [n for n in drawn if n.startswith(("dim_height", "m_env"))], (
            f"the automatic envelope survived the takeover: {sorted(drawn)}"
        )
        assert "m_env_width" in {n for n, _ in Sheet.from_part(part).build().iter_annotations()}, (
            "the control: from_part alone still auto-dimensions"
        )

    def test_an_explicit_auto_dimensions_still_conflicts(self):
        """The distinction that makes the override safe: a SCRIPT LINE asking for the
        automatic set and then declaring an authored one has said both things, and one of
        them must be wrong. Only `from_part`'s implicit choice is overridable."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.dimension(bore, "bore.diameter")
        with pytest.raises(ValueError, match="ONE dimension source"):
            sheet.build()

    def test_a_callout_for_an_omitted_feature_draws_nothing_and_says_so(self):
        """A post-build edit naming something the authored set left out draws nothing —
        audibly, on both paths.

        The defect being guarded is SILENCE, not the absence of a raise: the deferred path
        used to record the intent, draw nothing at the drain, drop the intent
        unconditionally and report success, so the edit vanished with no annotation, no
        pending intent and no warning (#921 review round 6).

        It was fixed by refusing, from a pre-check that predicted what a callout would
        draw. Three versions of that prediction were each wrong for some kind, and the last
        one drew an explicitly omitted diameter for a feature with SOME measurements
        authored (#925 review). There is no prediction now — the renderers consume approved
        content, so drawing nothing is what they do — and the report moved to where it is
        observed. What the test pins is unchanged: the edit does not disappear quietly.
        """
        from build123d import Box, Cylinder, Pos

        part = Box(100, 60, 20) - Pos(0, 0, 10) * Cylinder(5, 20)
        sheet = Sheet.from_part(part)
        env = next(f for f in sheet.features if f.kind == "envelope")
        hole = next(f for f in sheet.features if f.kind == "hole")
        sheet.dimension(env, "width.length")
        dwg = sheet.build()

        before = set(dwg.annotations())
        assert dwg.callout(hole) == ""
        assert set(dwg.annotations()) == before
        assert [i for i in dwg.lint() if i.code == "authored_omission"], (
            "the live edit drew nothing and reported nothing"
        )

    def test_a_step_callout_behaves_identically_live_and_deferred(self):
        """The answer must not depend on whether you are inside `deferred()`.

        It did once, in both directions: the pre-check sat in the deferred branch only, so
        the live step/boss path drew an explicitly omitted ø while the identical deferred
        call refused it (#921 round 7); and after the check moved above the split it still
        permitted the live draw for a step whose LENGTH was authored, because "has any
        approved dimension" is not "this callout draws" (#925 review). Both paths now reach
        the same nothing through the same approved content."""
        from build123d import Cylinder, Pos, Rot

        part = Rot(0, 90, 0) * Cylinder(4, 20) + Pos(15, 0, 0) * Rot(0, 90, 0) * Cylinder(6, 10)
        sheet = Sheet(part, title="T", number="N")
        sheet.step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        sheet.dimension(sheet.envelope(), "width.length")
        dwg = sheet.build()
        step = next(f for f in dwg.model().features if f.kind == "step")

        before = set(dwg.annotations())
        assert dwg.callout(step) == ""
        with dwg.deferred():
            assert dwg.callout(step) == ""
        dwg.finalize()
        assert set(dwg.annotations()) == before, "an omitted ø reached the page on some path"
        assert [i for i in dwg.lint() if i.code == "authored_omission"]

    @pytest.mark.parametrize("authored", ["bore.diameter", None], ids=["authored", "automatic"])
    def test_a_drawable_callout_is_never_refused(self, authored):
        """The false-positive guard. A refusal that fires on a legitimate edit is as much a
        defect as one that misses an illegitimate one, and this check runs on every
        `callout()` call now that it sits above the split."""
        from build123d import Box, Cylinder, Pos

        part = Box(100, 60, 20) - Pos(0, 0, 10) * Cylinder(5, 20)
        sheet = Sheet.from_part(part)
        if authored:
            sheet.dimension(next(f for f in sheet.features if f.kind == "hole"), authored)
        dwg = sheet.build()
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        with dwg.deferred():
            dwg.callout(hole)  # must not raise

    def test_the_refusal_asks_the_PLAN_not_a_per_kind_table(self):
        """This replaces a test of `_CALLOUT_PARAM_KINDS`, a hand-written table of which
        parameters each kind's callout prints. Three versions of that table were each wrong
        for some feature (#921 rounds 6-8) — a hole's callout survives losing an optional
        segment, a pocket's does not survive losing a required one, a pattern's pitch was
        never a callout term.

        The compiled-plan boundary removed the need to predict: a renderer receives approved
        entries, so "no approved dimensions for this feature" is the whole question. The
        table is gone and nothing replaced it."""
        from draftwright.model import callout as callout_mod

        assert not hasattr(callout_mod, "_CALLOUT_PARAM_KINDS"), (
            "the per-kind callout table is back — the boundary makes it unnecessary, and "
            "every hand-written version of it was wrong for some feature kind"
        )
        assert hasattr(callout_mod, "authored_omission_in"), "the WHY predicate is still needed"

    def test_from_part_states_the_automatic_source(self):
        """Detection IS a request for the engine's reading of the part, so `from_part` says so
        rather than leaving the caller to. Not the implicit default returning by the back
        door — a plain `Sheet(part)` still has to say."""
        from build123d import Box, Cylinder, Pos

        sheet = Sheet.from_part(Box(90, 60, 20) - Pos(0, 0, 12) * Cylinder(6, 20))
        assert sheet.build() is not None

    def test_the_model_surface_reflects_requests(self):
        """`Sheet.model()` must agree with what `build()` plans — a divergence here is
        the #707 class of bug, where the emitted script and the drawing disagree."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.add_dimension(bore, "bore.diameter")
        assert len(sheet.model().requested_dimensions) == 1


def test_an_ambiguous_role_raises_rather_than_guessing():
    """A grid pattern carries two `grid_pitch` measurements. Picking one silently is
    the kind of wrong a reader cannot see on the sheet, so the verb refuses and names
    the choices (ADR 0016 identity, tier 2)."""
    from draftwright.model import PatternFeature

    sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
    member = HoleFeature(Frame((0, 0, 14), "z"), 4.0, depth=None, through=True)
    grid = PatternFeature(
        Frame((0, 0, 14), "z"), "grid", 4, member, grid=(30.0, 40.0), rows=2, cols=2
    )
    sheet.features.append(grid)
    with pytest.raises(ValueError, match="ambiguous"):
        sheet.add_dimension(len(sheet.features) - 1, "grid_pitch")


def test_naming_the_axis_resolves_the_ambiguity():
    from draftwright.model import PatternFeature

    sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
    member = HoleFeature(Frame((0, 0, 14), "z"), 4.0, depth=None, through=True)
    grid = PatternFeature(
        Frame((0, 0, 14), "z"), "grid", 4, member, grid=(30.0, 40.0), rows=2, cols=2
    )
    sheet.features.append(grid)
    intent = sheet.add_dimension(len(sheet.features) - 1, "grid_pitch", axis="row")
    assert intent._entry["discriminator"] == "row"


def test_an_unknown_axis_variant_raises():
    from draftwright.model import PatternFeature

    sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
    member = HoleFeature(Frame((0, 0, 14), "z"), 4.0, depth=None, through=True)
    grid = PatternFeature(
        Frame((0, 0, 14), "z"), "grid", 4, member, grid=(30.0, 40.0), rows=2, cols=2
    )
    sheet.features.append(grid)
    with pytest.raises(ValueError, match="no such"):
        sheet.add_dimension(len(sheet.features) - 1, "grid_pitch", axis="x")


class TestFeatureResolution:
    """`add_dimension` accepts a handle, an index, or the IR feature itself — the three
    ways a caller can name a declared feature. Each resolves to the same intent."""

    def _sheet_with_hole(self):
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        handle = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        return sheet, handle

    def test_a_handle_resolves(self):
        sheet, handle = self._sheet_with_hole()
        assert sheet.add_dimension(handle, "bore.diameter")._entry["token"] == sheet._token_at(0)

    def test_an_index_resolves(self):
        sheet, _ = self._sheet_with_hole()
        assert sheet.add_dimension(0, "bore.diameter")._entry["token"] == sheet._token_at(0)

    def test_the_ir_feature_itself_resolves(self):
        sheet, _ = self._sheet_with_hole()
        assert sheet.add_dimension(sheet.features[0], "bore.diameter")._entry[
            "token"
        ] == sheet._token_at(0)

    def test_an_out_of_range_index_raises(self):
        sheet, _ = self._sheet_with_hole()
        with pytest.raises(IndexError, match="out of range"):
            sheet.add_dimension(7, "bore.diameter")

    def test_a_feature_from_another_sheet_raises(self):
        """A feature that was never declared here cannot be augmented — silently
        matching nothing would drop the request without a word."""
        sheet, _ = self._sheet_with_hole()
        stranger = HoleFeature(Frame((99, 99, 0), "z"), 3.0, depth=None, through=True)
        with pytest.raises(ValueError, match="not a feature declared on this sheet"):
            sheet.add_dimension(stranger, "bore.diameter")


def test_a_verbatim_partmodel_carries_requests_through_the_builder():
    """ADR 0011's public-input path: a caller-supplied `PartModel` is used verbatim, so
    requests merged onto it must survive into the plan without mutating the caller's
    reusable model."""
    from draftwright.builder import _coerce_model

    hole = HoleFeature(Frame((0, 0, 6), "z"), 8.0, depth=10.0, through=False)
    original = PartModel(bbox=_BBOX, orientation="prismatic", features=[hole])
    request = RequestedDimension(hole, "bore")

    coerced = _coerce_model(original, _part(), None, (request,))

    assert coerced.requested_dimensions == (request,)
    assert original.requested_dimensions == (), "the caller's model must not be mutated"


class TestItActuallyChangesTheDrawing:
    """The payoff, not the plumbing.

    Every other test here verifies a request *reaches* the planner. That is necessary
    and not sufficient: an adversarial review deleted the un-suppression logic outright
    and all of them still passed. What matters is a dimension appearing on a sheet that
    otherwise lacks it, so this asserts exactly that, end to end through the public
    surface.

    An **X-turned** part is the lever: its OD already conveys the cross-axis extent, so the
    planner suppresses the envelope depth — a real rule-set decision a caller might
    legitimately want to override.

    That lever used to be a square footprint. #997 deleted that suppression, and the way it
    failed is worth keeping in view here: this class asserted `_env_dims() == []` — *no*
    envelope dimensions — while the docstring above it said the rule suppressed "the envelope
    depth ... it would restate the width". The stated intent and the asserted behaviour
    disagreed, the suite passed, and a square plate shipped drawn with its height alone. A
    lever test has to assert what survives, not only what disappears.
    """

    @staticmethod
    def _turned_part():
        return Rot(0, 90, 0) * Cylinder(10, 40)

    def _env_dims(self, *, request: bool):
        sheet = Sheet(self._turned_part(), title="T", number="N").auto_dimensions()
        env = sheet.envelope()
        if request:
            sheet.add_dimension(env, "depth.length")
        drawing = sheet.build()
        return sorted(n for n in drawing.annotations() if n.startswith("m_env"))

    def test_the_planner_suppresses_it_without_a_request(self):
        dims = self._env_dims(request=False)
        assert "m_env_depth" not in dims, "the OD conveys the cross-axis extent"
        assert dims, "something must survive — the part still needs a stated size"

    def test_a_request_puts_the_dimension_on_the_sheet(self):
        assert "m_env_depth" in self._env_dims(request=True)

    def test_the_dotted_identity_works_end_to_end_too(self):
        sheet = Sheet(self._turned_part(), title="T", number="N").auto_dimensions()
        env = sheet.envelope()
        sheet.add_dimension(env, "depth.length")
        drawing = sheet.build()
        assert "m_env_depth" in set(drawing.annotations())


class TestRequestTargeting:
    """Referential intent must name ONE feature. Structural equality is the wrong
    relation here: two equal-valued features are two distinct targets."""

    def test_two_equal_features_are_distinct_targets(self):
        """`DimensionId` compares structurally (#871) so an id survives a re-plan that
        rebuilds the objects. A *request* is the opposite case — it targets one declared
        instance within a single build — so it matches on identity."""
        from draftwright.model.planner import _request_for

        a = HoleFeature(Frame((-20, 0, 6), "z"), 8.0, depth=10.0, through=False)
        b = HoleFeature(Frame((-20, 0, 6), "z"), 8.0, depth=10.0, through=False)
        assert a == b and a is not b

        model = PartModel(
            bbox=_BBOX,
            orientation="prismatic",
            features=[a, b],
            requested_dimensions=(RequestedDimension(a, "bore"),),
        )
        param = a.parameters()[0]
        assert _request_for(model, a, param) is not None
        assert _request_for(model, b, param) is None, "the request must not leak to its twin"

    def test_an_equal_feature_from_another_sheet_is_rejected(self):
        """Value-equal but never declared here — accepting it would silently dimension
        a feature this sheet does not own."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        twin = HoleFeature(**vars(sheet.features[0]))
        assert twin == sheet.features[0]
        with pytest.raises(ValueError, match="not a feature declared on this sheet"):
            sheet.add_dimension(twin, "bore.diameter")


class TestInvalidCombinations:
    def test_requests_without_a_declared_model_are_rejected(self):
        """A request names a declared feature object; detection builds its own. Dropping
        the request silently would leave `add_dimension` with no effect and no
        diagnostic — worse than a visible error (#630/#631/#632)."""
        from draftwright import build_drawing

        hole = HoleFeature(Frame((0, 0, 6), "z"), 8.0, depth=10.0, through=False)
        with pytest.raises(ValueError, match="needs model="):
            build_drawing(_part(), requested=(RequestedDimension(hole, "bore"),))

    def test_an_intent_follows_its_feature_through_a_reorder(self):
        """Since #908 a reorder is transparent, not an error. Features carry identity
        tokens and handles resolve through them, so the intent stays on the feature it
        named instead of the slot it happened to occupy.

        This replaces four tests that asserted a reorder RAISES — the behaviour #872's
        scaffolding could manage before the addressing model was fixed."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        deep = sheet.hole(diameter=10, at=(-20, 0, 14), axis="z").depth(12)
        sheet.hole(diameter=10, at=(20, 0, 14), axis="z").depth(7)
        sheet.add_dimension(deep, "bore.depth")

        sheet.features.reverse()

        (request,) = sheet._requested_dimensions()
        assert request.feature.depth == 12, "the intent must follow the 12mm hole"

    def test_a_size_verb_after_a_reorder_hits_the_right_feature(self):
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        first = sheet.hole(diameter=10, at=(-20, 0, 14), axis="z").depth(12)
        sheet.hole(diameter=4, at=(20, 0, 14), axis="z").depth(7)

        sheet.features.reverse()
        first.thread("M10")

        threaded = [f for f in sheet.features if getattr(f, "thread", None) == "M10"]
        assert [f.diameter for f in threaded] == [10.0]

    def test_removing_the_targeted_feature_raises(self):
        """Following a feature only works while it is there. A removed one must raise
        rather than resolve to whatever moved into its position."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.hole(diameter=4, at=(30, 0, 14), axis="z")
        sheet.add_dimension(bore, "bore.diameter")

        del sheet.features[0]

        with pytest.raises(ValueError, match="no longer on the sheet"):
            sheet._requested_dimensions()

    def test_a_tolerance_also_follows_its_feature(self):
        """The bug that motivated #908, and it predates add_dimension entirely:
        `_tolerances` was index-keyed since P2a, so a reorder moved a tolerance onto a
        neighbouring feature."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        big = sheet.hole(diameter=10, at=(-20, 0, 14), axis="z").depth(12)
        sheet.hole(diameter=4, at=(20, 0, 14), axis="z").depth(7)
        big.tolerance(0.05)

        sheet.features.reverse()

        toleranced = [f for f, *_ in sheet._decorations() if hasattr(f, "diameter")]
        assert [f.diameter for f in toleranced] == [10.0]


def test_a_gdt_aspect_alongside_a_dimension_intent_is_legitimate():
    """Round 6's false positive: `_materialize_gdt` rebinds each GD&T item's origin at
    build, which is an internal replacement — routing it through `_replace_feature`
    keeps the identity shadow honest, so an ordinary note-plus-dimension script does not
    look like an unsupported list edit."""
    sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
    bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
    bore.note("HONE")
    sheet.add_dimension(bore, "bore.diameter")
    assert sheet.model() is not None
    assert sheet.build() is not None
    assert sheet.build() is not None, "and again — repeated builds must stay legal"


def test_a_raw_control_frame_added_against_a_handle_tracks_the_final_feature():
    sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
    bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
    sheet.add(
        ControlFrame(
            frame=Frame((0, 0, 14), "z"),
            characteristic="position",
            tolerance="0.1",
            view="plan",
            side="above",
            origin=bore,
        )
    )

    sheet.features.reverse()
    bore.depth(8)
    sheet._prepare()

    frame = next(feature for feature in sheet.features if feature.kind == "control_frame")
    assert frame.origin.diameter == 10
    assert frame.origin.depth == 8


class TestFeatureViewIdentity:
    """`features` carries identity (#908), and assignment is not a move.

    The distinction an earlier draft got wrong: keeping a slot's token is right for the
    INTERNAL rebuild a size verb does — `.depth()` replaces the frozen dataclass with an
    updated copy of the same feature — but on the public view, assignment cannot tell
    "move this feature here" from "put a different feature here". Preserving identity
    across it silently transferred every reference onto whatever was assigned.

    So assignment mints fresh tokens and stale references fail loudly; `reverse()` and
    `sort()` move whole entries and are the identity-preserving way to reorder.
    """

    @staticmethod
    def _two_holes():
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        big = sheet.hole(diameter=10, at=(-20, 0, 14), axis="z").depth(12)
        sheet.hole(diameter=4, at=(20, 0, 14), axis="z").depth(7)
        big.tolerance(0.05)
        return sheet, big

    def test_a_tuple_swap_invalidates_rather_than_retargets(self):
        sheet, _big = self._two_holes()
        sheet.features[0], sheet.features[1] = sheet.features[1], sheet.features[0]
        with pytest.raises(ValueError, match="no longer on the sheet"):
            sheet._decorations()

    def test_a_slice_permutation_invalidates_rather_than_retargets(self):
        """`features[:] = features[::-1]` is the idiomatic in-place reversal, and it
        moves values between slots exactly like a swap."""
        sheet, _big = self._two_holes()
        sheet.features[:] = sheet.features[::-1]
        with pytest.raises(ValueError, match="no longer on the sheet"):
            sheet._decorations()

    def test_replacing_a_feature_wholesale_invalidates_its_references(self):
        """A different feature in the slot is a different thing — the old feature's
        tolerance must not transfer to it."""
        sheet, _big = self._two_holes()
        sheet.features[0] = HoleFeature(Frame((9, 9, 9), "z"), 1.0, depth=None, through=True)
        with pytest.raises(ValueError, match="no longer on the sheet"):
            sheet._decorations()

    def test_reverse_preserves_identity(self):
        """The identity-safe way to reorder: entries move, so references follow."""
        sheet, _big = self._two_holes()
        sheet.features.reverse()
        toleranced = [f.diameter for f, *_ in sheet._decorations() if hasattr(f, "diameter")]
        assert toleranced == [10.0]

    def test_a_size_verb_preserves_identity(self):
        """The internal rebuild path, which must NOT mint a new token."""
        sheet, big = self._two_holes()
        big.thread("M10")
        toleranced = [f.diameter for f, *_ in sheet._decorations() if hasattr(f, "diameter")]
        assert toleranced == [10.0]

    def test_a_control_builder_survives_a_reorder(self):
        """`control()` returns a builder that OUTLIVES the call which resolved its target, so
        it is the one place a stored index could still retarget: `.position(0.1)` appends the
        frame later, and an index would name whatever occupied the slot by then (PR #910
        review). Everything else — `datum`/`finish`/`note` — resolves and appends in a single
        expression, so only this seam can span a mutation."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        big = sheet.hole(diameter=10, at=(-20, 0, 14), axis="z").depth(12)
        sheet.hole(diameter=4, at=(20, 0, 14), axis="z").depth(7)

        control = sheet.control(big)
        sheet.features.reverse()
        control.position(0.1)
        sheet._prepare()

        frame = next(f for f in sheet.features if f.kind == "control_frame")
        assert frame.origin.diameter == 10.0

    def test_a_control_builder_spans_a_mint_too(self):
        """The same seam against an *appending* mutation, which shifts no slot the builder
        already holds but does grow the view — a token stays correct either way."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        big = sheet.hole(diameter=10, at=(-20, 0, 14), axis="z").depth(12)

        control = sheet.control(big)
        sheet.hole(diameter=4, at=(20, 0, 14), axis="z").depth(7)
        sheet.features.reverse()
        control.flatness(0.02)
        sheet._prepare()

        frame = next(f for f in sheet.features if f.kind == "control_frame")
        assert frame.origin.diameter == 10.0


class TestFeatureViewContract:
    """The complete mutable-sequence surface, as executable specification.

    Three times during #908 a fix for the identity problem reintroduced it through an
    operation nobody had enumerated — `MutableSequence` derives more methods than one
    tends to hold in mind. A systematic audit found the current behaviour correct; this
    pins it so the next change cannot quietly regress a route.

    The rule each case checks: an operation either PRESERVES identity (references follow
    the feature) or DROPS it (references fail loudly). Never transfers it silently.
    """

    @staticmethod
    def _sheet_with(n=3):
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        for i in range(n):
            sheet.hole(diameter=4 + i, at=(-20 + 20 * i, 0, 14), axis="z").depth(5 + i)
        return sheet

    def _tokens(self, sheet):
        return [t for t, _f in sheet._entries]

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda s, f: s.features.append(f), id="append"),
            pytest.param(lambda s, f: s.features.extend([f]), id="extend"),
            pytest.param(lambda s, f: s.features.insert(1, f), id="insert"),
            pytest.param(lambda s, f: s.features.__iadd__([f]), id="iadd"),
        ],
    )
    def test_additions_mint_fresh_tokens(self, mutate):
        sheet = self._sheet_with()
        before = self._tokens(sheet)
        mutate(sheet, HoleFeature(Frame((50, 0, 14), "z"), 2.0, depth=None, through=True))
        after = self._tokens(sheet)
        assert len(after) == len(before) + 1
        assert len(set(after)) == len(after), "tokens must stay unique"
        assert set(before) <= set(after), "an addition must not disturb existing identity"

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda s: s.features.pop(), id="pop"),
            pytest.param(lambda s: s.features.remove(s.features[1]), id="remove"),
            pytest.param(lambda s: s.features.__delitem__(0), id="del"),
            pytest.param(lambda s: s.features.clear(), id="clear"),
        ],
    )
    def test_removals_drop_tokens(self, mutate):
        sheet = self._sheet_with()
        before = set(self._tokens(sheet))
        mutate(sheet)
        after = set(self._tokens(sheet))
        assert after < before, "a removal must drop identity, not renumber"

    def test_sort_preserves_identity(self):
        """Like `reverse`, `sort` moves whole entries — references follow."""
        sheet = self._sheet_with()
        pairs = {t: f.diameter for t, f in sheet._entries}
        sheet.features.sort(key=lambda f: -f.diameter)
        assert {t: f.diameter for t, f in sheet._entries} == pairs
        assert [f.diameter for f in sheet.features] == sorted(pairs.values(), reverse=True)

    def test_extended_slice_assignment_mints_for_the_touched_slots_only(self):
        """`features[::2] = …` assigns positions 0 and 2, so those drop identity while
        the untouched position 1 keeps its own — assignment is per-slot, not wholesale."""
        sheet = self._sheet_with()
        before = self._tokens(sheet)
        sheet.features[::2] = [sheet.features[0], sheet.features[2]]
        after = self._tokens(sheet)
        assert after[1] == before[1], "an untouched slot keeps its identity"
        assert after[0] not in before and after[2] not in before, "assigned slots mint"
        assert len(set(after)) == len(after)

    def test_negative_indices_resolve(self):
        sheet = self._sheet_with()
        assert sheet.features[-1] is sheet.features[len(sheet.features) - 1]

    def test_a_sheet_survives_deepcopy(self):
        """The allocator must be copyable — `itertools.count` loses pickle/copy support
        in Python 3.14, and this package supports >=3.11."""
        import copy

        sheet = self._sheet_with()
        clone = copy.deepcopy(sheet)
        assert [f.diameter for f in clone.features] == [f.diameter for f in sheet.features]
        clone.hole(diameter=9, at=(60, 0, 14), axis="z")
        assert len(set(t for t, _ in clone._entries)) == len(clone._entries)

    def test_detection_seeded_features_carry_identity(self):
        """`from_part` seeds through `extend`, so detected features are tokenised too and
        a tolerance on one survives a reorder."""
        from build123d import Box, Cylinder, Pos

        part = Box(80, 50, 8) - Pos(20, 10, 4) * Cylinder(3, 8)
        sheet = Sheet.from_part(part)
        holes = [i for i, f in enumerate(sheet.features) if f.kind == "hole"]
        assert holes, "expected detection to seed a hole"
        sheet.of(holes[0]).tolerance(0.05)
        sheet.features.reverse()
        toleranced = [f for f, *_ in sheet._decorations() if getattr(f, "kind", None) == "hole"]
        assert len(toleranced) == 1


class TestOmissionReachesTheDrawing:
    """#876 end to end, through the façade a caller actually uses.

    Every unit test of the authored set passed while `build()` silently dropped it — one
    `_coerce_model` call forwarded `requested` and not `authored`, so the planner never took
    the authored branch (#921 review). Nothing asserted that omission changed a DRAWING, only
    that the model carried the intent, and the gap between those two is exactly where the bug
    lived. These assert the observable consequence.
    """

    @staticmethod
    def _sheet():
        from build123d import Box, Cylinder, Pos

        part = Box(90, 60, 20) - Pos(0, 0, 12) * Cylinder(6, 20)
        sheet = Sheet(part, title="T", number="N")
        return sheet, sheet.envelope()

    def test_an_authored_set_survives_the_build(self):
        sheet, env = self._sheet()
        sheet.dimension(env, "width.length")
        # Normalised to the parameter id on the way in (#963), so the set the build sees
        # reads the same however the script spelled it.
        assert [d.role for d in sheet.build().model().authored_dimensions] == ["width.length"]

    def test_an_omitted_measurement_is_marked_suppressed(self):
        sheet, env = self._sheet()
        sheet.dimension(env, "width.length")
        groups = plan_dimensions(sheet.model())
        marked = {pd.param.role: pd.suppressed for g in groups for pd in g.dims}
        assert marked["width"] is False, "the named measurement prints"
        assert marked["height"] is True and marked["depth"] is True, "the rest are omitted"

    def test_an_omitted_measurement_keeps_its_value(self):
        """Marked, NOT filtered (#875): the group retains its engineering data, so a later
        pass can still see what was left out and why."""
        sheet, env = self._sheet()
        sheet.dimension(env, "width.length")
        groups = plan_dimensions(sheet.model())
        omitted = [pd for g in groups for pd in g.dims if pd.suppressed]
        assert omitted and all(pd.param.value is not None for pd in omitted)
        assert all(pd.reason == "not in the authored dimension set" for pd in omitted)

    def test_the_automatic_set_is_unaffected(self):
        """The contrast: with no authored set nothing is suppressed by omission, so the
        assertions above cannot be passing for some unrelated reason."""
        sheet, _env = self._sheet()
        sheet.auto_dimensions()
        groups = plan_dimensions(sheet.model())
        assert not [
            pd for g in groups for pd in g.dims if pd.reason == "not in the authored dimension set"
        ]


def _names(dwg) -> set:
    return {name for name, _obj in dwg.iter_annotations()}


class TestOmittedDimensionsDoNotRender:
    """The class above marks the plan; this one reads the PAGE (#921 review round 2).

    Marking a `PlannedDimension` suppressed only matters if some renderer consults it,
    and three auto passes did not: `render_height_ladder` rebuilt the overall height
    and the step rungs from the bbox and the feature, and `render_step_positions` did
    the same for the shoulders. The plan said "omitted" and the drawing drew it anyway,
    which is why asserting on `plan_dimensions` could not catch this. Every assertion
    here inspects annotations that actually reached the drawing.
    """

    @staticmethod
    def _plate():
        from build123d import Box, Cylinder, Pos

        return Box(90, 60, 20) - Pos(0, 0, 12) * Cylinder(6, 20)

    @staticmethod
    def _staircase():
        """Three tiers, so the rung set has TWO members. A one-rung part cannot tell
        "the whole set is addressed" from "one member happened to survive"."""
        from build123d import Box, Pos

        return (
            Box(120, 60, 15)
            + Pos(-20, 0, 15) * Box(80, 60, 15)
            + Pos(-40, 0, 30) * Box(40, 60, 15)
        )

    @staticmethod
    def _shouldered():
        """One rebate — a `step_position` shoulder to omit."""
        from build123d import Box, Pos

        return Box(120, 60, 20) + Pos(-30, 0, 20) * Box(60, 60, 25)

    def test_an_omitted_overall_height_is_not_drawn(self):
        sheet = Sheet(self._plate(), title="T", number="N")
        sheet.dimension(sheet.envelope(), "width.length")
        assert "dim_height" not in _names(sheet.build())

    def test_the_authored_overall_height_is_drawn(self):
        """The control for the test above: the same sheet, naming height as well, still
        gets it. Without this, deleting the height ladder outright would pass."""
        sheet = Sheet(self._plate(), title="T", number="N")
        env = sheet.envelope()
        sheet.dimension(env, "width.length")
        sheet.dimension(env, "height.length")
        assert "dim_height" in _names(sheet.build())

    def test_the_automatic_set_still_draws_the_overall_height(self):
        sheet = Sheet(self._plate(), title="T", number="N").auto_dimensions()
        assert "dim_height" in _names(sheet.build())

    @staticmethod
    def _rungs(dwg):
        return sorted(n for n in _names(dwg) if n.startswith("dim_step_"))

    @staticmethod
    def _shoulders(dwg):
        return sorted(n for n in _names(dwg) if n.startswith("dim_shoulder_"))

    def test_omitted_step_rungs_are_not_drawn(self):
        part = self._staircase()
        sheet = Sheet(part, title="T", number="N")
        sheet.step_level(part)
        sheet.dimension(sheet.envelope(), "width.length")
        assert self._rungs(sheet.build()) == []

    def test_a_correlated_set_is_addressed_whole(self):
        """ADR 0016 identity tier 3: one `role=` line keeps the WHOLE ladder. With two
        rungs in play this distinguishes "the set was addressed" from "one member
        happened to survive" — a per-member reading of the plan would draw neither, and
        a half-honoured one would draw a partial staircase."""
        part = self._staircase()
        authored = Sheet(part, title="T", number="N")
        authored.step_level(part)
        authored.dimension(authored.features[-1], "step_height.length")
        auto = Sheet(part, title="T", number="N")
        auto.step_level(part)
        auto.auto_dimensions()
        drawn = self._rungs(authored.build())
        assert len(drawn) > 1, "the fixture must exercise a MULTI-member set"
        assert drawn == self._rungs(auto.build())

    def test_omitted_step_positions_are_not_drawn(self):
        part = self._shouldered()
        sheet = Sheet(part, title="T", number="N")
        sheet.step_level(part)
        sheet.dimension(sheet.features[-1], "step_height.length")
        assert self._shoulders(sheet.build()) == [], "step_position was not authored"
        assert self._rungs(sheet.build()), "but its sibling set on the same feature was"

    def test_authored_step_positions_are_drawn(self):
        part = self._shouldered()
        sheet = Sheet(part, title="T", number="N")
        sheet.step_level(part)
        sheet.dimension(sheet.features[-1], "step_position.length")
        assert self._shoulders(sheet.build())

    def test_an_unaddressable_overall_height_is_refused_not_drawn(self):
        """A sheet that never declared an envelope has no parameter naming the overall
        height, so an authored set cannot ask for it — and must not silently get it.
        The bbox fallback belongs to automatic builds only."""
        from build123d import Cylinder, Pos

        part = self._plate()
        sheet = Sheet(part, title="T", number="N")
        hole = sheet.hole(Pos(0, 0, 12) * Cylinder(6, 20))
        sheet.dimension(hole, "bore.diameter")
        assert "dim_height" not in _names(sheet.build())

    def test_one_authored_line_leaves_only_that_dimension_on_the_page(self):
        """The whole contract in one assertion, against a part with several competing
        feature kinds. Each earlier test names a pass that leaked; this one would catch
        the NEXT such pass without knowing its name, which is the property that matters
        — the P0 was a class of defect (a renderer rebuilding its marks from geometry
        instead of the plan), not a single site.

        What legitimately remains is furniture, not dimensioning: centre marks, the
        NTS note, the title block, and the location ladder, which stays outside the
        authored set until it has addressable identity (#883).
        """
        from build123d import Box, Cylinder, Pos

        part = (
            Box(120, 80, 25)
            - Pos(-30, 0, 17) * Cylinder(5, 20)
            - Pos(30, 20, 17) * Cylinder(4, 20)
        )
        sheet = Sheet(part, title="T", number="N")
        env = sheet.envelope()
        sheet.hole(Pos(-30, 0, 17) * Cylinder(5, 20))
        sheet.hole(Pos(30, 20, 17) * Cylinder(4, 20))
        sheet.dimension(env, "width.length")

        drawn = _names(sheet.build())
        assert "m_env_width" in drawn, "the one authored measurement"
        furniture = ("m_cm", "m_locx", "m_locy", "note_", "title_block")
        assert not [n for n in drawn if n != "m_env_width" and not n.startswith(furniture)], (
            f"an unauthored dimension reached the page: {sorted(drawn)}"
        )

        auto = Sheet(part, title="T", number="N")
        auto.envelope()
        auto.hole(Pos(-30, 0, 17) * Cylinder(5, 20))
        auto.hole(Pos(30, 20, 17) * Cylinder(4, 20))
        auto.auto_dimensions()
        assert len(_names(auto.build())) > len(drawn), (
            "the automatic build must carry MORE — otherwise the assertion above holds "
            "for a part that was never richly dimensioned in the first place"
        )

    def test_a_turned_part_authors_one_dimension_too(self):
        """The prismatic case above passed while the whole TURNED family ignored
        suppression — `render_diameters`, `render_step_lengths`, `render_rotational` and
        `render_boss_diameters` all selected parameters with no suppression check, so
        authoring one step length still drew every diameter on the part (#921 review
        round 3). One fixture per renderer family, because a fixture only guards the
        family it exercises.

        Centrelines survive by design: they show where the turning axis is and print no
        value, so a sheet that authors one length still reads as a turned part."""
        from build123d import Cylinder, Pos, Rot

        part = Rot(0, 90, 0) * Cylinder(4, 20) + Pos(15, 0, 0) * Rot(0, 90, 0) * Cylinder(6, 10)
        sheet = Sheet(part, title="T", number="N")
        first = sheet.step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        sheet.step(diameter=12, length=10, at=(15, 0, 0), axis="x")  # same-kind neighbour
        sheet.dimension(first, "step.length")

        drawn = _names(sheet.build())
        assert "m_steplen0" in drawn, "the one authored measurement"
        assert not [n for n in drawn if n.startswith(("m_dia", "dim_od", "ldr_"))], (
            f"an unauthored turned dimension reached the page: {sorted(drawn)}"
        )
        assert "m_steplen1" not in drawn, "the neighbouring step's length was not authored"
        assert {"centerline_front", "centerline_plan"} <= drawn, "furniture is not a dimension"

    def test_an_omitted_rotational_od_and_bore_are_not_drawn(self):
        """`render_rotational` places the OD and each concentric bore leader directly.
        A bore omitted from the set must not even become a strip candidate — it was not
        wanted, which is different from not fitting."""
        from build123d import Cylinder

        from draftwright.model.ir import Frame, RotationalFeature

        part = Cylinder(radius=20, height=40) - Cylinder(radius=6, height=40)

        def _built(role):
            sheet = Sheet(part, title="T", number="N")
            sheet.add(RotationalFeature(frame=Frame((0, 0, 0), "z"), od=40.0, bores=(12.0,)))
            sheet.dimension(sheet.features[-1], role)
            return _names(sheet.build())

        od_only = _built("od.diameter")
        assert "dim_od" in od_only and "ldr_z0" not in od_only
        bore_only = _built("bore.diameter")
        assert "ldr_z0" in bore_only and "dim_od" not in bore_only


class TestEveryFeatureVerbIsNameable:
    """A `dimension(...)` line names a feature, so every declaration verb has to hand one back.

    Eleven did not before #922: nine returned the `Sheet` (`chamfer`, `fillet`, `flat`,
    `groove`, `plate`, `step_level`, and the three pattern verbs), plus `add` and
    `measured_dimension`. Those features could not be referenced at all. `add` mattered most:
    the ENVELOPE is emitted through it, so naming would have been uniform across the verbs and
    silently absent for one feature in the middle of a generated script.

    The contract is checked BEHAVIOURALLY, per verb, over a set discovered from the source.
    The first version asserted that no verb's source ends in `return self` — which a verb
    ending in `return None`, or falling off the end, or returning anything unrelated would
    pass while leaving its feature unnameable (Codex review of #931). Matching source text is
    a proxy for the property; resolving the handle is the property.
    """

    #: A minimal call for each declaration verb. Every discovered verb must appear — a verb
    #: with no entry FAILS rather than being skipped, so the table cannot silently fall behind
    #: the API the way the first version's six-verb parametrization did.
    _CALLS: dict = {
        "hole": dict(diameter=6, at=(0, 0, 0), axis="z"),
        "double_d_bore": dict(
            major_diameter=10,
            across_flats=7,
            at=(0, 0, 0),
            axis="z",
            depth=8,
        ),
        "diameter": dict(diameter=20, at=(0, 0, 0), axis="z"),
        "boss": dict(diameter=20, height=5, at=(0, 0, 0), axis="z"),
        "polygonal_boss": dict(
            side_count=4,
            across_flats=20,
            height=5,
            at=(0, 0, 0),
            axis="z",
            flat_directions=((1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)),
            flat_centres=((10, 0, 0), (0, 10, 0), (-10, 0, 0), (0, -10, 0)),
        ),
        "step": dict(diameter=20, length=10, at=(0, 0, 0), axis="z"),
        "slot": dict(width=8, length=20, long_axis="x", width_axis="y", w_center=0, lo=-10, hi=10),
        "pocket": dict(
            width=8, length=20, depth=4, long_axis="x", width_axis="y", w_center=0, lo=-10, hi=10
        ),
        "channel": dict(
            width=8,
            long_axis="x",
            width_axis="y",
            w_center=0,
            lo=-10,
            hi=10,
            d_lo=0,
            d_hi=4,
        ),
        "pad": dict(x0=-10, x1=10, y0=-4, y1=4, z0=0, z1=3),
        "envelope": {},
        "chamfer": dict(axis="z", leg=2, at=(0, 0, 0)),
        "fillet": dict(axis="z", radius=2, at=(0, 0, 0)),
        "flat": dict(axis="z", across=15, at=(0, 0, 0)),
        "groove": dict(axis="z", width=3, diameter=16, at=(0, 0, 0)),
        "plate": dict(axis="z", lo=0, hi=4, u=10, v=5),
        "step_level": dict(base=0, levels=(4.0,), at=(0, 0, 0)),
        "rotational": dict(od=30, bores=(16,), at=(0, 0, 0), axis="z"),
        "measured_dimension": dict(
            kind="linear", value=5, label="5", dominant_axis="X", ref_pts=((0, 0, 0), (5, 0, 0))
        ),
    }

    @staticmethod
    def _declaration_verbs() -> set:
        import inspect

        from draftwright.sheet import Sheet as _Sheet

        return {
            name
            for name, fn in inspect.getmembers(_Sheet, inspect.isfunction)
            if not name.startswith("_") and "self._features.append(" in inspect.getsource(fn)
        }

    def _invoke(self, sheet, verb):
        """Call *verb* on *sheet* with a minimal argument set, returning its handle."""
        from draftwright.model import hole as _mk_hole
        from draftwright.model.ir import EnvelopeFeature, Frame

        if verb == "add":
            return sheet.add(
                EnvelopeFeature(
                    Frame((0, 0, 0), "z"),
                    width=80.0,
                    depth=50.0,
                    height=8.0,
                    bbox_min=(-40.0, -25.0, -4.0),
                    bbox_max=(40.0, 25.0, 4.0),
                )
            )
        if verb.endswith("pattern"):
            member = {
                "pattern": lambda: _mk_hole(diameter=6, at=(-20, 0, 0), axis="z"),
                "pocket_pattern": lambda: _pocket_member(),
                "slot_pattern": lambda: _slot_member(),
            }[verb]()
            return getattr(sheet, verb)(
                member, kind="linear", count=3, pitch=20, direction=(1, 0, 0), at=(0, 0, 0)
            )
        return getattr(sheet, verb)(**self._CALLS[verb])

    def test_the_call_table_covers_every_declaration_verb(self):
        """The ratchet. A verb added later with no entry fails HERE, so the behavioural
        contract below cannot quietly stop covering the API."""
        covered = set(self._CALLS) | {"add", "pattern", "pocket_pattern", "slot_pattern"}
        missing = self._declaration_verbs() - covered
        assert not missing, (
            f"{sorted(missing)} declare a feature but have no entry in _CALLS — add one so "
            "the nameability contract actually covers them"
        )

    @pytest.mark.parametrize(
        "verb", sorted(_CALLS) + ["add", "pattern", "pocket_pattern", "slot_pattern"]
    )
    def test_the_handle_resolves_and_survives_a_reorder(self, verb):
        """The property, for every verb: the returned handle names its feature, and keeps
        naming it after the feature list moves.

        Reordering is the half that matters. A handle carrying a bare INDEX would pass a
        resolve-once check and then silently name a neighbour the moment a script comments a
        feature out — the positional-addressing defect this work removes from the generated
        artefact (#908/#922).
        """
        from build123d import Box

        sheet = Sheet(Box(80, 50, 8))
        sheet.hole(diameter=4, at=(-30, 0, 0), axis="z")  # a decoy that the reorder moves
        handle = self._invoke(sheet, verb)
        assert handle is not sheet, f"Sheet.{verb} hands back no reference to its feature"
        declared = sheet.features[handle._i]
        sheet.features.reverse()
        assert sheet.features[handle._i] is declared, (
            f"Sheet.{verb}'s handle stopped naming its feature after a reorder"
        )

    @pytest.mark.parametrize("verb", sorted(_CALLS) + ["add", "pattern"])
    def test_a_feature_with_parameters_can_be_dimensioned_by_its_handle(self, verb):
        """Resolving is necessary but not sufficient — the handle has to be accepted by the
        verb it exists for. Parameterless kinds are exercised by the raise below instead."""
        from build123d import Box

        sheet = Sheet(Box(80, 50, 8))
        handle = self._invoke(sheet, verb)
        # The CANONICAL spelling (#963): a bare role names the whole family, so on a kind
        # carrying two — `groove` is `groove.diameter` + `groove.length` — it is refused.
        # `parameter_id` is what a caller should write and what this must therefore exercise.
        params = sheet.features[handle._i].parameters()
        if not params:
            pytest.skip(f"{verb} declares a parameterless feature — covered by the raise test")
        sheet.dimension(handle, params[0].parameter_id)

    def test_a_parameterless_feature_refuses_a_tolerance_rather_than_dropping_it(self):
        """`_Params` is handed out for every kind now, including ones with no dimensioned
        parameter, so its aspect verbs must not accept an instruction they cannot record.

        `tolerance()` iterated an empty role set and returned successfully, dropping the
        instruction in silence (Codex review of #931) — the failure this codebase ranks below
        a visible raise. Reachable only since #922: these verbs used to return the Sheet, so
        `.tolerance()` could not be called on them at all.
        """
        from build123d import Box

        sheet = Sheet(Box(10, 10, 10))
        handle = self._invoke(sheet, "measured_dimension")
        with pytest.raises(ValueError, match="no dimensioned parameter"):
            handle.tolerance(0.2)
        assert sheet._tolerances == {}

    def test_a_declaration_verb_still_chains(self):
        """The false-positive half. These verbs returned `Sheet` so calls could chain, and the
        handles keep that working by forwarding — otherwise making them nameable would have
        broken every existing script that chains off one."""
        from build123d import Box

        chained = (
            Sheet(Box(80, 50, 8))
            .chamfer(axis="z", leg=2, at=(0, 0, 0))
            .hole(diameter=6, at=(0, 0, 0), axis="z")
        )
        assert len(chained._sheet.features) == 2


def _pocket_member():
    from draftwright.model import pocket as _mk

    return _mk(
        width=8, length=20, depth=4, long_axis="x", width_axis="y", w_center=0, lo=-10, hi=10
    )


def _slot_member():
    from draftwright.model import slot as _mk

    return _mk(width=8, length=20, long_axis="x", width_axis="y", w_center=0, lo=-10, hi=10)
