"""#1154 — one physical measurement, one owner.

GRM-04's drive plate prints `4.5` twice. A ø8 hub is flush with both faces of a 4.5 mm
plate, so the detected `boss_height.length` and the detected `envelope.width.length` run
between the SAME two faces, and the sheet states one fact through two feature owners:

    boss.boss_height.length   4.5   span x −1.5 → 3   (drawn along the boss axis)
    envelope.width.length     4.5   span x −1.5 → 3   (drawn along a bbox edge)

This is not #650's coordinate-key dedup — the two records are semantically different
features with different anchors and different witness lines — and it is emphatically not
value dedup, which #997 deleted for good reasons this fixes rather than reopens (see
`_same_support_planes`).

The fixtures are synthetic because the source STEP is not in this repo. They are chosen for
the geometry that produces the defect, not for the outline: a hub flush on both faces of a
plate, which is the only way a feature-local height comes to equal an overall extent.
"""

from __future__ import annotations

import dataclasses

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot

from draftwright import Sheet
from draftwright.builder import build_drawing, detect_part_model
from draftwright.drawing import feature_key
from draftwright.model.compiled import compile_dimensions
from draftwright.model.planner import DimensionId, _same_support_planes, _support_planes

_C = (Align.CENTER, Align.CENTER, Align.CENTER)


def _x_hub_plate():
    """GRM-04's *shape*, not its dimensions: a 4.5 mm plate with a hub flush on both X faces.

    The hub's height and the overall X thickness are the same two faces, so this draws
    `4.5` twice before the reconciliation.
    """
    # The hub must protrude beyond the plate outline in Y, not sit tangent to it: at
    # `Box(4.5, 10, 25)` with r=5 the cylinder is exactly flush with the plate's Y faces and
    # is not recognised as a boss at all — which made the first two tests below pass while
    # asserting nothing, because a duplicate that never existed cannot be found twice.
    return Box(4.5, 8, 24, align=_C) + Pos(0, 0, -8) * (Rot(0, 90, 0) * Cylinder(5, 4.5, align=_C))


def _z_hub_plate():
    """The same defect one axis over — a Z hub flush with both faces of an 8 mm plate.

    A Z boss height renders in the front view's RIGHT strip while an X one renders ABOVE
    it (`render_boss_heights`' `specs` table), so this and `_x_hub_plate` exercise the
    reconciliation through two different eligible views.
    """
    return Box(20, 8, 8, align=_C) + Cylinder(7, 8, align=_C)


def _proud_boss_plate():
    """The discriminating control: a boss standing PROUD, whose height EQUALS an extent.

    The 4 mm boss height (z 4 → 8) and the 4 mm overall depth (y −2 → 2) are the same
    number between different pairs of faces. A rule keyed on the value collapses them; the
    rule keyed on support planes must not. Chosen deliberately over a boss whose height
    matches nothing, which cannot tell the two rules apart.
    """
    return Box(20, 4, 8, align=_C) + Pos(0, 0, 6) * Cylinder(1.5, 4, align=_C)


def _labels(drawing):
    return sorted(
        str(label)
        for _name, item in drawing.iter_annotations()
        if (label := getattr(item, "label", None))
    )


def _assert_a_candidate_exists(model, *, owner):
    """A non-envelope length parameter really does share *owner*'s two support planes.

    The positive anchor for the refusal tests. "Nothing was consolidated" is trivially true
    when no consolidation candidate exists, and shrinking the declared boss in any of the
    three so it no longer spans the extent left all three passing (#1154 review r2) — the
    same vacuity `_assert_the_duplicate_exists` was written for, unapplied in the class added
    to fix it.
    """
    extent = next(p for p in _envelope(model).parameters() if p.parameter_id == owner)
    planes = _support_planes(extent)
    assert planes is not None
    # `_same_support_planes`, the production predicate — not `pytest.approx`, whose default
    # relative tolerance is looser than `_PLANE_TOL` at these coordinates and could admit a
    # candidate the rule itself would reject (#1154 review r3).
    candidates = [
        (f.kind, p.parameter_id)
        for f in model.features
        if f.kind != "envelope"
        for p in f.parameters()
        if _same_support_planes(_support_planes(p), planes)
    ]
    assert candidates, (
        f"no feature measures {owner}'s two support planes, so a refusal proves nothing"
    )
    # Necessary but not sufficient: a candidate can exist and be refused by a DIFFERENT
    # authority than the one under test — adding a tolerance to a fixture moved the refusal
    # to the tolerance rule and left all three refusal tests passing (review r3). Each caller
    # pairs this with a relaxation that shows the named authority is the one refusing.
    return candidates


def _consolidates_when(model) -> bool:
    """Whether *model* consolidates anything — the positive half of the refusal anchors."""
    return bool([o for o in compile_dimensions(model).diagnostics if o.conveyed_by])


def _assert_the_duplicate_exists(model):
    """The fixture must actually contain the defect, or every assertion below is vacuous.

    Written after the first cut used a hub tangent to the plate's Y faces: no boss was
    recognised, so "the thickness appears once" and "the boss height is not placed" were
    both trivially true and the tests passed against completely unfixed code.
    """
    boss, param = _boss_height(model)
    extent = next(
        p
        for p in _envelope(model).parameters()
        if p.span is not None and abs(p.value - param.value) < 1e-6
    )
    assert param.span is not None and extent.span is not None
    # `approx`, not `==`: the two records reach 4.5 by different routes and differ in the
    # last bit (4.5 vs 4.500000000000002). Worth knowing — a dedup keyed on the printed or
    # stored VALUE would not even fire here, quite apart from being the wrong rule.
    assert param.value == pytest.approx(extent.value), (
        f"{param.value} and {extent.value} are no longer the same measurement"
    )
    return boss, param, extent


def _boss_height(model):
    boss = next(f for f in model.features if f.kind == "boss")
    param = next(p for p in boss.parameters() if p.parameter_id == "boss_height.length")
    return boss, param


@pytest.fixture(scope="module")
def x_hub_build():
    """One flush-hub build shared by the read-only critiques below (#656).

    Returns (part, drawing) so a consumer needing the solid gets the SAME object the
    drawing was built from. Every consumer only READS the drawing (labels, registry,
    suppressions, coverage lint); a test that mutates it must build its own.
    """
    part = _x_hub_plate()
    return part, build_drawing(part, title="T", number="N-1")


@pytest.fixture
def x_hub_drawing(x_hub_build):
    return x_hub_build[1]


class TestTheSameTwoFacesAreDimensionedOnce:
    def test_a_flush_hub_no_longer_states_the_thickness_twice(self, x_hub_drawing):
        drawing = x_hub_drawing
        _assert_the_duplicate_exists(drawing.model())
        labels = _labels(drawing)
        assert labels.count("4.5") == 1, (
            f"the overall X thickness is stated {labels.count('4.5')} times: {labels}"
        )

    def test_the_surviving_owner_is_the_overall_extent(self, x_hub_drawing):
        # Direction matters and is not arbitrary: a part has one overall thickness and it is
        # what a machinist reads first, so the envelope keeps the fact and the feature-local
        # height yields. The converse rule would delete a required envelope dimension
        # whenever some feature happened to grow to full depth.
        drawing = x_hub_drawing
        _assert_the_duplicate_exists(drawing.model())
        placed = {
            (getattr(measurement.feature, "kind", None), measurement.parameter)
            for name in drawing.registry.names()
            for measurement in drawing.registry.measurement_of(name)
        }
        assert ("envelope", "width.length") in placed
        assert ("boss", "boss_height.length") not in placed

    def test_the_reconciliation_reaches_a_boss_on_the_other_axis(self):
        # A Z boss height renders in the front view's RIGHT strip while an X one renders
        # ABOVE it (`render_boss_heights`' `specs` table), so this is the cross-view half of
        # the acceptance criteria rather than a second copy of the case above. It builds the
        # drawing as well as the plan: asserting the omission alone left the "across
        # different eligible views" claim resting on the specs table rather than on output
        # (#1154 review).
        labels = _labels(build_drawing(_z_hub_plate(), title="T", number="N-1"))
        assert labels.count("8") == 1, (
            f"the overall Z thickness is stated {labels.count('8')} times: {labels}"
        )
        model = detect_part_model(_z_hub_plate())
        boss, param = _boss_height(model)
        assert param.value == 8.0 and param.span is not None
        omissions = [o for o in compile_dimensions(model).diagnostics if o.feature == boss]
        assert [(o.parameter_id, o.conveyed_by) for o in omissions] == [
            ("boss_height.length", DimensionId(_envelope(model), "height.length"))
        ]

    def test_a_proud_boss_keeps_both_dimensions(self):
        # The rule must not collapse two genuinely different spans. This boss stands 4 mm
        # above the plate, so its height and the 12 mm overall height are different pairs of
        # faces and both are required.
        model = detect_part_model(_proud_boss_plate())
        _boss, param = _boss_height(model)
        assert param.value == 4.0
        assert not [
            o for o in compile_dimensions(model).diagnostics if o.conveyed_by is not None
        ], "a boss standing proud of the plate lost its height"
        labels = _labels(build_drawing(_proud_boss_plate(), title="T", number="N-1"))
        # Counts, not membership: this fixture draws `4` twice (the boss height and the
        # overall depth), so `"4" in labels` would still hold with one of them consolidated
        # away — the exact loss this test exists to catch (#1154 review).
        assert labels.count("4") == 2 and labels.count("12") == 1, labels


def _envelope(model):
    return next(f for f in model.features if f.kind == "envelope")


class TestTheCollapseIsPlaneIdentityNotValueEquality:
    def test_a_height_equal_to_an_extent_between_other_faces_survives(self):
        # The #997 lesson, stated the other way round, and the ONLY test here that
        # distinguishes the two candidate rules. A boss 4 mm tall in Z on a part 4 mm deep
        # in Y states two independent facts that happen to share a number; #997 deleted a
        # rule that collapsed equal extents and so read a 100 x 95 part as square.
        #
        # Written after the first cut used a square 30 x 30 x 10 block here and a
        # value-equality mutation SURVIVED it: `_consolidated_owner` never compares an
        # envelope extent against another envelope extent, so a square part is protected by
        # that exclusion and proves nothing about how the comparison is made.
        model = detect_part_model(_proud_boss_plate())
        _boss, height = _boss_height(model)
        depth = next(p for p in _envelope(model).parameters() if p.parameter_id == "depth.length")
        assert height.value == depth.value == 4.0, (
            "the fixture no longer has two equal measurements, so a value rule and a plane "
            "rule would agree on it"
        )
        assert _support_planes(height) == ("z", 4.0, 8.0)
        assert _support_planes(depth) == ("y", -2.0, 2.0)
        assert not [
            o for o in compile_dimensions(model).diagnostics if o.conveyed_by is not None
        ], "a measurement was consolidated onto an extent between different faces"

    def test_a_square_footprint_keeps_both_of_its_equal_extents(self):
        # Not a test of the comparison — see above — but the #997 regression itself, which
        # this slice must not reopen from the other end.
        model = detect_part_model(Box(30, 30, 10, align=_C))
        assert not [o for o in compile_dimensions(model).diagnostics if o.conveyed_by is not None]
        labels = _labels(build_drawing(Box(30, 30, 10, align=_C), title="T", number="N-1"))
        assert labels.count("30") == 2, f"a square part lost one of its two extents: {labels}"

    def test_an_oblique_span_measures_no_pair_of_planes(self):
        from draftwright.model.ir import DimParameter

        assert _support_planes(DimParameter("length", "boss_height", 5.0, span=None)) is None
        assert (
            _support_planes(
                DimParameter("length", "boss_height", 5.0, span=((0, 0, 0), (3, 4, 0)))
            )
            is None
        )
        assert (
            _support_planes(DimParameter("diameter", "boss", 5.0, span=((0, 0, 0), (5, 0, 0))))
            is None
        ), "a diameter is not a distance between two support planes"
        assert _support_planes(
            DimParameter("length", "boss_height", 5.0, span=((3, 9, 9), (-2, 9, 9)))
        ) == ("x", -2.0, 3.0), "an axis-aligned span is normalised low-to-high"


class TestTheConsolidationIsRecordedAndVerified:
    def test_the_omission_names_the_dimension_that_now_carries_the_fact(self):
        # Provenance: a reader of `plan.diagnostics` must be able to answer "where did this
        # measurement go?", not merely "it is not drawn".
        model = detect_part_model(_x_hub_plate())
        boss, _param = _boss_height(model)
        omission = next(
            o
            for o in compile_dimensions(model).diagnostics
            if o.parameter_id == "boss_height.length"
        )
        assert omission.feature == boss
        assert omission.value == 4.5
        assert omission.conveyed_by == DimensionId(_envelope(model), "width.length")
        assert not omission.authored, "a rule-set consolidation is not the author's doing"
        assert "support planes" in omission.reason

    def test_the_audit_read_distinguishes_withheld_from_consolidated(self, x_hub_drawing):
        # `Drawing.suppressions()` is the surface a harness or a script actually reads, and
        # "this is not drawn" and "this is drawn over there" are different answers. Reporting
        # a consolidation as a bare omission would read a de-duplication as a gap.
        drawing = x_hub_drawing
        _assert_the_duplicate_exists(drawing.model())
        row = next(r for r in drawing.suppressions() if r["parameter_id"] == "boss_height.length")
        assert row["authored"] is False
        assert row["conveyed_by"] == {
            "feature": feature_key(_envelope(drawing.model())),
            "parameter_id": "width.length",
        }
        # `_x_hub_plate` produces exactly ONE suppression row, so an "everything else is
        # None" generator over the rest is empty and trivially true — which is what the
        # first cut asserted (#1154 review). Say the real thing instead.
        assert [r["parameter_id"] for r in drawing.suppressions()] == ["boss_height.length"]

    def test_coverage_accepts_a_consolidated_height_only_while_its_owner_is_placed(
        self, x_hub_build
    ):
        # `boss_height_missing` exists to demand the axial extent. A consolidation satisfies
        # it because the fact is on the sheet — but ONLY on that condition, so the check
        # verifies the owner landed rather than trusting the compiler's intent. Consolidating
        # onto a dimension the placer then drops is a missing measurement.
        from draftwright.linting.coverage import lint_boss_height_coverage

        part, drawing = x_hub_build
        model = drawing.model()
        features = model.features
        # Recompiled through the public model rather than read off `Drawing._build`: the
        # compiler is deterministic over the model, so this is the same inventory the build
        # used, and the encapsulation ratchet (#741) forbids the reach-through.
        omissions = compile_dimensions(model).diagnostics
        assert not lint_boss_height_coverage(part, drawing, features, omissions=omissions)

        class _Registry:
            """The same drawing with the owner absent from the placed inventory."""

            def __init__(self, inner):
                self._inner = inner

            def names(self):
                return [n for n in self._inner.names() if n != "m_env_width"]

            def measurement_of(self, name):
                return self._inner.measurement_of(name)

        class _Dwg:
            def __init__(self, inner):
                self.registry = _Registry(inner.registry)
                self._inner = inner

            def annotations_of(self, feature):
                return self._inner.annotations_of(feature)

        assert "m_env_width" in drawing.registry.names(), (
            "the owner annotation was renamed, so this stub proves nothing"
        )
        issues = lint_boss_height_coverage(part, _Dwg(drawing), features, omissions=omissions)
        assert [i.code for i in issues] == ["boss_height_missing"]

    def test_an_authored_set_that_keeps_the_owner_critiques_the_same_as_the_automatic_build(self):
        # Round-trip parity (epic #964). The author's list decides which dimensions are
        # drawn; it does not decide where the geometry states a fact. Nulling `conveyed_by`
        # for an authored omission made an emitted script warn `boss_height_missing` where
        # the drawing it was generated from did not.
        from draftwright.sheet_emit import emit_sheet_script

        part = _z_hub_plate()
        direct = build_drawing(part, title="T", number="N-1")
        source = emit_sheet_script(detect_part_model(part), "part", "s", title="T", number="N-1")
        assert "boss_height" not in source, (
            "the emitter now declares the consolidated height, so parity is not under test"
        )
        captured: dict = {"part": part}
        from unittest.mock import patch

        from draftwright import Drawing

        with patch.object(
            Drawing, "export", lambda self, *a, **k: captured.setdefault("dwg", self)
        ):
            exec(compile(source, "<emit>", "exec"), captured)  # noqa: S102

        def codes(dwg):
            return sorted((i.code, i.severity) for i in dwg.lint())

        assert codes(captured["dwg"]) == codes(direct)


class TestConsolidationMovesTheFactOrDoesNotHappen:
    """`_owner_drawn`'s three authorities, none of which had a test (#1154 review).

    Replacing the whole function with `return True` passed the full fast tier, so the guard
    the commit message opens with — "the consolidation must move a fact to another dimension,
    never to nowhere" — was asserted only in prose.
    """

    def test_a_toleranced_height_is_not_handed_to_an_untoleranced_extent(self):
        # The one case where the feature-local dimension is the one a machinist needs. The
        # overall extent carries no tolerance, so consolidating DELETED a ±0.05 the author
        # wrote — silently, with nothing in lint, and recoverable only by knowing to add an
        # `add_dimension` line. A toleranced dimension and an untoleranced one are not the
        # same requirement.
        part = Box(20, 8, 8, align=_C) + Cylinder(7, 8, align=_C)
        sheet = Sheet(part, title="T", number="N-1", page="A2")
        sheet.envelope()
        sheet.diameter(
            diameter=14, height=8, span=((0, 0, -4), (0, 0, 4)), at=(0, 0, 4), axis="z"
        ).tolerance(0.05, on="length")
        sheet.auto_dimensions()
        drawing = sheet.build()
        assert not [o for o in compile_dimensions(drawing.model()).diagnostics if o.conveyed_by], (
            "a toleranced height was consolidated onto an extent that cannot carry it"
        )
        assert "8 ±0.1" in _labels(drawing), _labels(drawing)

    def test_an_owner_the_rule_set_withholds_is_refused(self):
        # A z-rotational part suppresses its envelope width and depth (the OD conveys them),
        # so a boss spanning the full X extent has no drawn owner to hand its height to.
        # Asserted on the compiled plan because `render_boss_heights` skips rotational parts
        # anyway — the loss would be invisible in the drawing and permanent in the plan.
        part = Cylinder(10, 30, align=_C)
        sheet = Sheet(part, title="T", number="N-1", page="A2")
        sheet.rotational(od=20, at=(0, 0, 0), axis="z")
        sheet.envelope()
        sheet.diameter(
            diameter=8, height=20, span=((-10, 0, 0), (10, 0, 0)), at=(10, 0, 0), axis="x"
        )
        sheet.auto_dimensions()
        model = sheet.build().model()
        _assert_a_candidate_exists(model, owner="width.length")
        # Sufficiency: relaxing THIS authority — the rotational rule that withholds the
        # envelope width — makes the same geometry consolidate. Without it, a fixture drifted
        # in some other direction (a tolerance, say) would move the refusal elsewhere and the
        # test would pass having proved nothing (#1154 review r3).
        assert _consolidates_when(
            dataclasses.replace(
                model, features=tuple(f for f in model.features if f.kind != "rotational")
            )
        ), "the rotational rule is not what refuses this consolidation"
        plan = compile_dimensions(model)
        assert {o.parameter_id for o in plan.diagnostics if o.conveyed_by} == set(), (
            "the height was handed to an envelope extent the rotational rule withholds"
        )
        assert {o.reason for o in plan.diagnostics} == {
            "rotational OD (z-axis) conveys this extent"
        }, "the fixture no longer suppresses the envelope, so nothing is under test"

    def test_an_owner_the_compiler_withholds_is_refused(self):
        # The THIRD authority. `compiled._compile_overall_height` withholds the overall
        # height for an X/Y rotational OD, and the first cut consulted only the planner's own
        # rules — so a declared X-rotational part consolidated its boss height onto a
        # dimension that same compiler was independently removing, and the drawing carried
        # neither. `overall_height_withheld` is now the one owner both consult.
        from draftwright.model.planner import overall_height_withheld

        part = Rot(0, 90, 0) * Cylinder(10, 30, align=_C)
        sheet = Sheet(part, title="T", number="N-1", page="A2")
        sheet.rotational(od=20, at=(0, 0, 0), axis="x")
        sheet.envelope()
        sheet.diameter(
            diameter=8, height=20, span=((0, 0, -10), (0, 0, 10)), at=(0, 0, 10), axis="z"
        )
        sheet.auto_dimensions()
        model = sheet.build().model()
        _assert_a_candidate_exists(model, owner="height.length")
        # Sufficiency: with the rotational feature gone the compiler no longer withholds the
        # overall height, and the same geometry consolidates.
        assert _consolidates_when(
            dataclasses.replace(
                model, features=tuple(f for f in model.features if f.kind != "rotational")
            )
        ), "the compiler's overall-height rule is not what refuses this consolidation"
        assert overall_height_withheld(model), (
            "the fixture no longer withholds the overall height, so nothing is under test"
        )
        assert not [o for o in compile_dimensions(model).diagnostics if o.conveyed_by], (
            "the height was handed to an overall height the compiler withholds"
        )

    def test_an_owner_the_authored_set_omits_is_refused(self):
        # And the second authority, from the other side of the parity rule: `conveyed_by`
        # survives an authored omission only when the author's set KEEPS the owner.
        part = _z_hub_plate()
        sheet = Sheet(part, title="T", number="N-1", page="A2")
        boss = sheet.diameter(
            diameter=14, height=8, span=((0, 0, -4), (0, 0, 4)), at=(0, 0, 4), axis="z"
        )
        envelope = sheet.envelope()
        sheet.authored_dimensions()
        sheet.dimension(envelope, "width.length")
        sheet.dimension(boss, "boss.diameter")
        model = sheet.build().model()
        _assert_a_candidate_exists(model, owner="height.length")
        # Sufficiency: drop the authored set and the same geometry consolidates, so it is
        # the authored omission of the OWNER that refuses and not some other authority.
        assert _consolidates_when(dataclasses.replace(model, authored_dimensions=None)), (
            "the authored set is not what refuses this consolidation"
        )
        rows = [o for o in compile_dimensions(model).diagnostics if o.conveyed_by]
        assert not rows, (
            "the height was handed to an overall height the authored set leaves out: "
            f"{[(o.parameter_id, o.conveyed_by) for o in rows]}"
        )


class TestThePlaneToleranceIsAToleranceNotAWindow:
    def test_a_face_half_a_millimetre_short_is_a_different_plane(self):
        # `_PLANE_TOL` is the kernel's noise floor, not a matching window. Widening it to 0.5
        # or 2.0 passed the full fast tier (#1154 review), so nothing said which of those it
        # was — and #997's deleted rule failed precisely by being a window.
        part = Box(20, 8, 8, align=_C) + Pos(0, 0, 0.25) * Cylinder(7, 7.5, align=_C)
        model = detect_part_model(part)
        boss, height = _boss_height(model)
        assert boss is not None and height.value == pytest.approx(7.5)
        # The TOP face coincides exactly and the bottom is 0.5 mm short — the hardest case
        # for a windowed comparison, and the one that decides whether this is a tolerance or
        # a match radius.
        assert _support_planes(height) == pytest.approx(("z", -3.5, 4.0), abs=1e-9)
        assert _support_planes(
            next(p for p in _envelope(model).parameters() if p.parameter_id == "height.length")
        ) == pytest.approx(("z", -4.0, 4.0), abs=1e-9)
        assert not [o for o in compile_dimensions(model).diagnostics if o.conveyed_by], (
            "a boss 0.5 mm short of the lower face was consolidated onto the 8 mm extent"
        )


class TestTheConsolidationDoesNotDisturbTheHeightCompiler:
    def test_a_rotational_part_without_an_envelope_keeps_its_suppression_record(self):
        # Sharing the overall-height conditions with the planner collapsed two of them into
        # one early return, which swallowed the richer `Omission` naming the OD whenever the
        # part had no `.envelope()`. The audit row did not flatten — it vanished — from the
        # surface whose own docstring says an audit that flattened them "would read a
        # de-duplication as a gap" (#1154 review r2). The conditions are now two named
        # predicates, each applied where it belongs.
        sheet = Sheet(Rot(0, 90, 0) * Cylinder(10, 30), title="T", number="N-1", page="A2")
        sheet.rotational(od=20, at=(0, 0, 0), axis="x")
        sheet.auto_dimensions()
        rows = [
            row for row in sheet.build().suppressions() if row["parameter_id"] == "height.length"
        ]
        assert [row["reason"] for row in rows] == ["rotational OD (x-axis) conveys the height"]
        assert rows[0]["conveyed_by"] is None, "nothing took this fact over; it is withheld"


class TestTheToleranceRuleIsAsymmetric:
    """Which side carries the tolerance decides the answer, and the two are not alike."""

    def _z_hub_labels(self, *, on_boss=False, on_envelope=False):
        part = Box(20, 8, 8, align=_C) + Cylinder(7, 8, align=_C)
        sheet = Sheet(part, title="T", number="N-1", page="A2")
        envelope = sheet.envelope()
        boss = sheet.diameter(
            diameter=14, height=8, span=((0, 0, -4), (0, 0, 4)), at=(0, 0, 4), axis="z"
        )
        if on_boss:
            boss.tolerance(0.05, on="length")
        if on_envelope:
            envelope.tolerance(0.05, on="height")
        sheet.auto_dimensions()
        return [label for label in _labels(sheet.build()) if label.startswith("8")]

    def test_tolerancing_the_yielding_dimension_keeps_both(self):
        assert self._z_hub_labels(on_boss=True) == ["8", "8 ±0.1"]

    def test_tolerancing_the_owner_still_consolidates(self):
        # The first cut compared the two tolerances and refused whenever they differed, so an
        # author who toleranced the OVERALL THICKNESS got the duplicate `8` back — #1154's own
        # defect, re-opened by its fix (review r2). A tolerance on the owner is the same
        # requirement on the same two faces; it does not make them two facts.
        assert self._z_hub_labels(on_envelope=True) == ["8"]

    def test_equal_tolerances_on_both_sides_still_keep_both(self):
        # The r2 correction then went one step too far and admitted "equal tolerances", on
        # the premise that the receiving extent states the same requirement. Measured, an
        # envelope decoration is not rendered on ANY axis while `render_boss_heights` appends
        # `_tol_suffix`, so an identically toleranced boss height lost its ± anyway — the
        # exact silent loss the tolerance rule was added to stop, re-created by the fix for
        # its own over-correction (review r3). The rule is about the yielder alone.
        assert self._z_hub_labels(on_boss=True, on_envelope=True) == ["8", "8 ±0.1"]
