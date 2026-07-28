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
