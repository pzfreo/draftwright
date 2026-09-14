"""Axial step recognition and coverage-lint behavior."""

from _parts import x_stepped_shaft as _x_stepped_shaft
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing


class TestStepLadderRecognition:
    """ADR 1 (was 0008) step 1: the Z step-height ladder draws its step levels from the
    unified turned-step model, which filters by the OD silhouette."""

    def test_blind_bore_floor_is_not_a_phantom_shoulder(self):

        # Two OD steps (one real interior shoulder at z=15) + a blind axial bore
        # whose flat floor sits at z=30. The floor must NOT be dimensioned as a
        # step height — that was the area-filter phantom the model removes.
        shaft = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
        part = shaft - Pos(0, 0, 45) * Cylinder(5, 30)
        dwg = build_drawing(part, number="D-1")
        # The turned part is now dimensioned by the unified IR step-length chain
        # (#223): two real OD segments (each length 30), and crucially NO '45'
        # bore-floor phantom — recognise_turned_steps excludes the internal bore.
        labels = [o.label for n, o in dwg.iter_annotations() if n.startswith("m_steplen")]
        assert labels == ["30", "30"]  # both real segments
        assert "45" not in labels  # no bore-floor phantom

    def test_plain_z_stepped_shaft_dimensioned_by_ir_chain(self):

        # A Z-turned stepped shaft is now located by the unified IR step-length
        # chain (#223), not the old engine ladder. Both segments are dimensioned.
        dwg = build_drawing(Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30), number="D-1")
        labels = [o.label for n, o in dwg.iter_annotations() if n.startswith("m_steplen")]
        assert labels == ["30", "30"]
        assert not any(
            n.startswith("dim_step") for n in dwg.annotations()
        )  # ladder retired for turned


class TestAxialCoverageLint:
    """lint_axial_coverage — the scoring signal for undimensioned turned steps,
    now counted from the drawing (not the CoverageState side channel, #219)."""

    def test_flags_uncovered_turned_part(self):
        from draftwright.linting import lint_axial_coverage

        # A bare scaffold (views, no step-length dims) → all steps uncovered.
        part = _x_stepped_shaft()
        dwg = build_drawing(part, number="D-1", auto_dims=False)
        issues = lint_axial_coverage(part, dwg)
        assert [i.code for i in issues] == ["axial_length_missing"]
        assert issues[0].severity == "warning"

    def test_clean_when_all_steps_covered(self):
        from draftwright.linting import lint_axial_coverage

        # The engine places the full step-length chain → drawing-derived coverage
        # finds every step located.
        part = _x_stepped_shaft()
        dwg = build_drawing(part, number="D-1")
        assert lint_axial_coverage(part, dwg) == []

    def test_silent_for_non_turned_part(self):
        from draftwright.linting import lint_axial_coverage

        part = Box(80, 60, 20)
        dwg = build_drawing(part, number="D-1", auto_dims=False)
        assert lint_axial_coverage(part, dwg) == []

    def test_z_turned_chain_is_covered(self):
        # A Z-turned shaft is now located by the vertical IR chain (#223), so axial
        # coverage must recognise it (no false positive on a correctly chained Z part).

        from draftwright.linting import lint_axial_coverage

        part = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
        dwg = build_drawing(part, number="D-1")
        assert lint_axial_coverage(part, dwg) == []

    def test_z_turned_flags_when_uncovered(self):
        # The X-only restriction is gone (#223): a Z-turned shaft with no chain
        # (bare scaffold) is flagged, not silently under-dimensioned.

        from draftwright.linting import lint_axial_coverage

        part = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
        dwg = build_drawing(part, number="D-1", auto_dims=False)
        assert [i.code for i in lint_axial_coverage(part, dwg)] == ["axial_length_missing"]

    def test_coverage_survives_repair_and_is_idempotent(self):
        # Drawing-derived coverage must stay clean after the repair loop re-places
        # dims (witnesses stay anchored to geometry) and across repeated lint()s.
        part = _x_stepped_shaft()
        dwg = build_drawing(part, number="D-1")  # repair on
        first = [i.code for i in dwg.lint() if i.code == "axial_length_missing"]
        again = [i.code for i in dwg.lint() if i.code == "axial_length_missing"]
        assert first == [] and again == []

    def test_axial_length_missing_is_geometry_aware(self):
        # It is a completeness/standards code, so lint_summary must count it under
        # geometry_issues, not as layout (#226 review follow-through).
        from draftwright.drawing import _GEOMETRY_AWARE_CODES

        assert "axial_length_missing" in _GEOMETRY_AWARE_CODES
