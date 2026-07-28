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
- **the handle survives a later size verb** — intents are index-keyed and materialised
  against the FINAL features, like `_tolerances`;
- **ambiguity raises** rather than guessing between two same-role measurements.
"""

from __future__ import annotations

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet
from draftwright.model import Frame, HoleFeature, PartModel
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
        intent = sheet.add_dimension(bore, "bore")
        assert intent._entry["role"] == "bore"
        assert intent._entry["token"] == sheet._token_at(0)

    def test_the_handle_forwards_unknown_attributes_to_the_sheet(self):
        """Declare-then-chain: returning a handle must not break the fluent surface,
        the same contract `_Params` holds."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        chained = sheet.add_dimension(bore, "bore").hole(diameter=4, at=(20, 0, 20), axis="z")
        assert chained is not None
        assert len(sheet.features) == 2

    def test_the_intent_survives_a_later_size_verb(self):
        """Intents are index-keyed and materialised at build, so a handle recorded
        before `.depth()` replaces the feature still resolves against the final one."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z")
        sheet.add_dimension(bore, "bore")
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
        sheet = Sheet(_part(), title="T", number="N")
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.add_dimension(bore, "bore")
        with pytest.raises(ValueError, match="call auto_dimensions"):
            sheet.build()

    def test_the_source_may_be_declared_after_the_augment(self):
        """Order independence (ADR 0016). The gate is at build, not in the verb, so
        these two scripts must not disagree."""
        sheet = Sheet(_part(), title="T", number="N")
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.add_dimension(bore, "bore")
        sheet.auto_dimensions()
        assert sheet.build() is not None

    def test_a_sheet_without_augments_still_builds_without_the_source_verb(self):
        """`auto_dimensions()` is optional in this phase — making it mandatory is the
        #874 breaking change. A plain declarative script must keep working."""
        sheet = Sheet(_part(), title="T", number="N")
        sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        assert sheet.build() is not None

    def test_the_model_surface_reflects_requests(self):
        """`Sheet.model()` must agree with what `build()` plans — a divergence here is
        the #707 class of bug, where the emitted script and the drawing disagree."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.add_dimension(bore, "bore")
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
        assert sheet.add_dimension(handle, "bore")._entry["token"] == sheet._token_at(0)

    def test_an_index_resolves(self):
        sheet, _ = self._sheet_with_hole()
        assert sheet.add_dimension(0, "bore")._entry["token"] == sheet._token_at(0)

    def test_the_ir_feature_itself_resolves(self):
        sheet, _ = self._sheet_with_hole()
        assert sheet.add_dimension(sheet.features[0], "bore")._entry["token"] == sheet._token_at(0)

    def test_an_out_of_range_index_raises(self):
        sheet, _ = self._sheet_with_hole()
        with pytest.raises(IndexError, match="out of range"):
            sheet.add_dimension(7, "bore")

    def test_a_feature_from_another_sheet_raises(self):
        """A feature that was never declared here cannot be augmented — silently
        matching nothing would drop the request without a word."""
        sheet, _ = self._sheet_with_hole()
        stranger = HoleFeature(Frame((99, 99, 0), "z"), 3.0, depth=None, through=True)
        with pytest.raises(ValueError, match="not a feature declared on this sheet"):
            sheet.add_dimension(stranger, "bore")


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

    A square footprint is the lever: the planner suppresses the envelope depth when
    width and depth are equal (it would restate the width), which is a real
    rule-set decision a caller might legitimately want to override.
    """

    @staticmethod
    def _square_part():
        return Box(20, 20, 40)

    def _env_dims(self, *, request: bool):
        sheet = Sheet(self._square_part(), title="T", number="N").auto_dimensions()
        env = sheet.envelope()
        if request:
            sheet.add_dimension(env, "depth")
        drawing = sheet.build()
        return sorted(n for n in drawing.annotations() if n.startswith("m_env"))

    def test_the_planner_suppresses_it_without_a_request(self):
        assert self._env_dims(request=False) == []

    def test_a_request_puts_the_dimension_on_the_sheet(self):
        assert self._env_dims(request=True) == ["m_env_depth"]

    def test_the_dotted_identity_works_end_to_end_too(self):
        sheet = Sheet(self._square_part(), title="T", number="N").auto_dimensions()
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
            sheet.add_dimension(twin, "bore")


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
        sheet.add_dimension(bore, "bore")

        del sheet.features[0]

        with pytest.raises(ValueError, match="no longer on the sheet"):
            sheet._requested_dimensions()

    def test_a_tolerance_also_follows_its_feature(self):
        """The bug that motivated #908, and it predates add_dimension entirely:
        `_tolerances` was index-keyed since P2a, so a reorder moved a tolerance onto a
        neighbouring feature."""
        sheet = Sheet(_part(), title="T", number="N")
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
    sheet.add_dimension(bore, "bore")
    assert sheet.model() is not None
    assert sheet.build() is not None
    assert sheet.build() is not None, "and again — repeated builds must stay legal"


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
        sheet = Sheet(_part(), title="T", number="N")
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
        sheet = Sheet(_part(), title="T", number="N")
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
        sheet = Sheet(_part(), title="T", number="N")
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
        sheet = Sheet(_part(), title="T", number="N")
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
