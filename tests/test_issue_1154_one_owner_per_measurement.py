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

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot

from draftwright.builder import build_drawing, detect_part_model
from draftwright.drawing import feature_key
from draftwright.model.compiled import compile_dimensions
from draftwright.model.planner import DimensionId, _support_planes

_C = (Align.CENTER, Align.CENTER, Align.CENTER)


def _x_hub_plate():
    """GRM-04's shape: a 4.5 mm plate with a hub flush on both X faces.

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


class TestTheSameTwoFacesAreDimensionedOnce:
    def test_a_flush_hub_no_longer_states_the_thickness_twice(self):
        drawing = build_drawing(_x_hub_plate(), title="T", number="N-1")
        _assert_the_duplicate_exists(drawing.model())
        labels = _labels(drawing)
        assert labels.count("4.5") == 1, (
            f"the overall X thickness is stated {labels.count('4.5')} times: {labels}"
        )

    def test_the_surviving_owner_is_the_overall_extent(self):
        # Direction matters and is not arbitrary: a part has one overall thickness and it is
        # what a machinist reads first, so the envelope keeps the fact and the feature-local
        # height yields. The converse rule would delete a required envelope dimension
        # whenever some feature happened to grow to full depth.
        drawing = build_drawing(_x_hub_plate(), title="T", number="N-1")
        _assert_the_duplicate_exists(drawing.model())
        placed = {
            (getattr(measurement.feature, "kind", None), measurement.parameter)
            for name in drawing.registry.names()
            for measurement in drawing.registry.measurement_of(name)
        }
        assert ("envelope", "width.length") in placed
        assert ("boss", "boss_height.length") not in placed

    def test_the_reconciliation_reaches_a_boss_on_the_other_axis(self):
        # A Z boss height renders in a different strip of a different view, so this is the
        # cross-view half of the acceptance criteria rather than a second copy of the case
        # above.
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
        assert "4" in labels and "12" in labels, labels


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

    def test_the_audit_read_distinguishes_withheld_from_consolidated(self):
        # `Drawing.suppressions()` is the surface a harness or a script actually reads, and
        # "this is not drawn" and "this is drawn over there" are different answers. Reporting
        # a consolidation as a bare omission would read a de-duplication as a gap.
        drawing = build_drawing(_x_hub_plate(), title="T", number="N-1")
        _assert_the_duplicate_exists(drawing.model())
        row = next(r for r in drawing.suppressions() if r["parameter_id"] == "boss_height.length")
        assert row["authored"] is False
        assert row["conveyed_by"] == {
            "feature": feature_key(_envelope(drawing.model())),
            "parameter_id": "width.length",
        }
        assert all(
            r["conveyed_by"] is None
            for r in drawing.suppressions()
            if r["parameter_id"] != "boss_height.length"
        )

    def test_coverage_accepts_a_consolidated_height_only_while_its_owner_is_placed(self):
        # `boss_height_missing` exists to demand the axial extent. A consolidation satisfies
        # it because the fact is on the sheet — but ONLY on that condition, so the check
        # verifies the owner landed rather than trusting the compiler's intent. Consolidating
        # onto a dimension the placer then drops is a missing measurement.
        from draftwright.linting.coverage import lint_boss_height_coverage

        part = _x_hub_plate()
        drawing = build_drawing(part, title="T", number="N-1")
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
