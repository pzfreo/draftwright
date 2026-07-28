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
        assert intent._entry["index"] == 0

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
        assert sheet.add_dimension(handle, "bore")._entry["index"] == 0

    def test_an_index_resolves(self):
        sheet, _ = self._sheet_with_hole()
        assert sheet.add_dimension(0, "bore")._entry["index"] == 0

    def test_the_ir_feature_itself_resolves(self):
        sheet, _ = self._sheet_with_hole()
        assert sheet.add_dimension(sheet.features[0], "bore")._entry["index"] == 0

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

    def test_reordering_features_after_declaring_an_intent_raises(self):
        """`features` is public and mutable, so an intent's stored index can come to
        point at a different feature. Dimensioning the wrong one silently is the failure
        this catches — so the real scenario is reproduced, not simulated."""
        from draftwright.model import ChamferFeature

        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.add_dimension(bore, "bore")  # records index 0

        # A later edit pushes the hole to index 1; index 0 is now a chamfer, which has
        # no "bore" measurement at all.
        sheet.features.insert(0, ChamferFeature(Frame((0, 0, 0), "z"), "z", 2.0, 2.0, 45.0))

        with pytest.raises(ValueError, match="reordered"):
            sheet._requested_dimensions()

    def test_truncating_the_feature_list_raises(self):
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.add_dimension(bore, "bore")
        sheet.features.clear()
        with pytest.raises(ValueError, match="reordered"):
            sheet._requested_dimensions()

    def test_swapping_two_same_kind_features_raises(self):
        """The case a role check cannot catch, and the reason targeting is identity-based:
        two holes both carry `bore`, so swapping them passes any role test while silently
        moving the request from the 12 mm hole to the 7 mm one."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        deep = sheet.hole(diameter=10, at=(-20, 0, 14), axis="z").depth(12)
        sheet.hole(diameter=10, at=(20, 0, 14), axis="z").depth(7)
        sheet.add_dimension(deep, "bore.depth")

        sheet.features[0], sheet.features[1] = sheet.features[1], sheet.features[0]

        with pytest.raises(ValueError, match="reordered"):
            sheet._requested_dimensions()

    def test_a_handle_from_another_sheet_is_rejected(self):
        """A handle's index is only meaningful on the sheet that issued it — accepting a
        foreign one silently dimensions whatever this sheet holds at that index."""
        other = Sheet(_part(), title="T", number="N").auto_dimensions()
        foreign = other.hole(diameter=3, at=(0, 0, 14), axis="z").depth(4)

        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)

        with pytest.raises(ValueError, match="different Sheet"):
            sheet.add_dimension(foreign, "bore")

    def test_a_size_verb_after_the_intent_is_still_legal(self):
        """The flow identity-targeting must NOT break: `.depth()` rebuilds the frozen
        feature, and the intent has to follow it rather than reject it."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z")
        sheet.add_dimension(bore, "bore")
        bore.depth(12)
        (request,) = sheet._requested_dimensions()
        assert request.feature is sheet.features[0]
        assert request.feature.depth == 12

    def test_a_reorder_followed_by_a_size_verb_still_raises(self):
        """The laundering path. It now fails at the size verb rather than later at
        materialisation — the earlier the better, since the verb is where the mistake
        actually is, and letting the write through is what allowed the mismatch to be
        refreshed away in the first place."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        first = sheet.hole(diameter=10, at=(-20, 0, 14), axis="z").depth(12)
        sheet.hole(diameter=10, at=(20, 0, 14), axis="z").depth(7)
        sheet.add_dimension(first, "bore.depth")

        sheet.features[0], sheet.features[1] = sheet.features[1], sheet.features[0]

        with pytest.raises(ValueError, match="stale"):
            first.thread("M10")  # stale handle, would write to the OTHER hole's slot

    def test_an_intent_declared_after_a_reorder_raises(self):
        """Round 4's escape: the intent is declared *after* the swap, so the target is
        captured post-shuffle and no identity comparison at materialisation can see the
        problem. Only noticing that the list itself moved catches this one."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        first = sheet.hole(diameter=10, at=(-20, 0, 14), axis="z").depth(12)
        sheet.hole(diameter=10, at=(20, 0, 14), axis="z").depth(7)
        sheet.add_dimension(first, "bore.depth")  # takes the shadow

        sheet.features[0], sheet.features[1] = sheet.features[1], sheet.features[0]

        with pytest.raises(ValueError, match="reordered"):
            sheet.add_dimension(first, "bore")

    def test_declaring_more_features_after_an_intent_stays_legal(self):
        """An append cannot disturb an existing index, so the check must not fire on the
        ordinary flow of declaring more geometry after asking for a dimension."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z").depth(12)
        sheet.add_dimension(bore, "bore")
        sheet.hole(diameter=4, at=(30, 0, 14), axis="z")
        sheet.hole(diameter=6, at=(-30, 0, 14), axis="z")
        assert sheet.build() is not None

    def test_a_handle_stale_from_a_reorder_is_rejected(self):
        """Round 5's escape. The reorder happens BEFORE any intent, so it breaks no
        intent contract — but it leaves the handle's index naming a different feature.
        Resolving it silently would dimension the neighbour."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        first = sheet.hole(diameter=10, at=(-20, 0, 14), axis="z").depth(12)
        sheet.hole(diameter=10, at=(20, 0, 14), axis="z").depth(7)

        sheet.features.reverse()

        with pytest.raises(ValueError, match="stale"):
            sheet.add_dimension(first, "bore.depth")

    def test_a_size_verb_keeps_its_handle_fresh(self):
        """The contrast: rebuilding the frozen feature through the handle is legitimate
        and must not make it look stale."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        bore = sheet.hole(diameter=10, at=(0, 0, 14), axis="z")
        bore.depth(12)
        bore.thread("M10")
        intent = sheet.add_dimension(bore, "bore")
        assert intent._entry["target"] is sheet.features[0]

    def test_a_size_verb_cannot_launder_a_stale_handle(self):
        """Round 6's variant. The staleness check has to run BEFORE the write, or the
        write refreshes `_declared` and the mismatch disappears — the handle then
        resolves cleanly onto the wrong feature."""
        sheet = Sheet(_part(), title="T", number="N").auto_dimensions()
        first = sheet.hole(diameter=10, at=(-20, 0, 14), axis="z").depth(12)
        sheet.hole(diameter=10, at=(20, 0, 14), axis="z").depth(7)

        sheet.features.reverse()

        with pytest.raises(ValueError, match="stale"):
            first.thread("M10")


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
