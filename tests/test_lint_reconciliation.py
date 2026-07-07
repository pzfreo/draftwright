"""Declaration-vs-geometry reconciliation lint (#487).

The declarative path (`Sheet` / `build_drawing(part, model=…)`) has one blind spot: a declared
feature that no longer corresponds to real geometry (the part was edited to remove it) renders a
callout at empty space while coverage lint — which checks *detected → dimensioned* — stays clean.
`lint_declaration_reconciliation` closes the reverse direction (*declared → exists*).
"""

from __future__ import annotations

from types import SimpleNamespace

from build123d import Box, Cylinder, Pos

from draftwright import Sheet, build_drawing
from draftwright.linting import lint_declaration_reconciliation


def _feat(kind, dia, origin, axis="z"):
    return SimpleNamespace(
        kind=kind, diameter=dia, frame=SimpleNamespace(origin=origin, axis=axis)
    )


def _cyl(dia, axis_xyz, axis="z", external=False):
    # external defaults to False (a bore) so a plain hole matches; bosses/steps pass external=True.
    return {"diameter": dia, "axis": axis, "axis_xyz": axis_xyz, "external": external}


class TestMatcherUnit:
    """Fast unit tests of the matcher — no OCC build."""

    def test_matching_feature_no_issue(self):
        feats = [_feat("hole", 8.0, (0, 0, 0))]
        cyls = ([_cyl(8.0, (0, 0, 5))], [])  # same axis + ⌀, in-plane at origin
        assert lint_declaration_reconciliation(feats, cyls) == []

    def test_absent_feature_warns(self):
        feats = [_feat("hole", 8.0, (0, 0, 0))]
        issues = lint_declaration_reconciliation(feats, ([], []))
        assert len(issues) == 1
        assert issues[0].code == "declared_feature_absent"
        assert issues[0].severity == "warning"

    def test_diameter_out_of_tolerance_warns(self):
        # ⌀ off by 0.5 mm (> _RECON_DIA_TOL 0.2) → no match → warn
        feats = [_feat("hole", 8.0, (0, 0, 0))]
        cyls = ([_cyl(8.5, (0, 0, 0))], [])
        assert len(lint_declaration_reconciliation(feats, cyls)) == 1

    def test_diameter_within_tolerance_matches(self):
        feats = [_feat("hole", 8.0, (0, 0, 0))]
        cyls = ([_cyl(8.15, (0, 0, 0))], [])  # 0.15 <= 0.2
        assert lint_declaration_reconciliation(feats, cyls) == []

    def test_in_plane_offset_out_of_tolerance_warns(self):
        # moved 1 mm in-plane (> _RECON_POS_TOL 0.5) → no match → warn (a moved declaration)
        feats = [_feat("hole", 8.0, (0, 0, 0))]
        cyls = ([_cyl(8.0, (1.0, 0, 0))], [])
        assert len(lint_declaration_reconciliation(feats, cyls)) == 1

    def test_axial_position_ignored(self):
        # in-plane matches; the axis-position component (z) differs — must still match (mirrors
        # _match_object, which is in-plane only).
        feats = [_feat("hole", 8.0, (0, 0, 0))]
        cyls = ([_cyl(8.0, (0, 0, 999))], [])
        assert lint_declaration_reconciliation(feats, cyls) == []

    def test_different_axis_warns(self):
        feats = [_feat("hole", 8.0, (0, 0, 0), axis="z")]
        cyls = ([], [_cyl(8.0, (0, 0, 0), axis="x")])
        assert len(lint_declaration_reconciliation(feats, cyls)) == 1

    def test_non_cylindrical_kinds_skipped(self):
        # envelope / pattern / slot / aspects carry no single defining cylinder — never reconciled.
        feats = [
            _feat("envelope", None, (0, 0, 0)),
            _feat("pattern", 8.0, (0, 0, 0)),
            _feat("slot", None, (0, 0, 0)),
            _feat("control_frame", None, (0, 0, 0)),
        ]
        assert lint_declaration_reconciliation(feats, ([], [])) == []

    def test_boss_and_step_reconciled(self):
        feats = [_feat("boss", 30.0, (0, 0, 0)), _feat("step", 20.0, (0, 0, 0))]
        cyls = ([_cyl(30.0, (0, 0, 0), external=True)], [])  # boss present, step ⌀20 absent
        codes = [i.code for i in lint_declaration_reconciliation(feats, cyls)]
        assert codes == ["declared_feature_absent"]  # only the step is missing

    def test_hole_not_matched_by_external_cylinder(self):
        # #487 review: a phantom bore must NOT be silenced by a coaxial boss/OD of the same ⌀ — the
        # callout would render over solid material. A hole reconciles only against external=False.
        feats = [_feat("hole", 20.0, (0, 0, 0))]
        cyls = ([_cyl(20.0, (0, 0, 0), external=True)], [])  # only an OD/boss of the same ⌀ exists
        assert len(lint_declaration_reconciliation(feats, cyls)) == 1

    def test_boss_not_matched_by_bore(self):
        # The mirror: a phantom boss callout over a bore of the same ⌀ must warn — a boss reconciles
        # only against external=True.
        feats = [_feat("boss", 30.0, (0, 0, 0))]
        cyls = ([_cyl(30.0, (0, 0, 0), external=False)], [])  # only an internal bore exists
        assert len(lint_declaration_reconciliation(feats, cyls)) == 1


class TestEndToEnd:
    def test_phantom_hole_warns(self):
        # part is a solid box; the script still declares a hole (its Cylinder is only read for ⌀).
        s = Sheet(Box(120, 80, 20))
        s.envelope()
        s.hole(Pos(0, 0, 0) * Cylinder(4, 20))
        codes = [i.code for i in s.build().lint()]
        assert "declared_feature_absent" in codes

    def test_real_hole_clean(self):
        part = Box(120, 80, 20) - Pos(0, 0, 0) * Cylinder(4, 20)
        s = Sheet(part)
        s.envelope()
        s.hole(Pos(0, 0, 0) * Cylinder(4, 20))
        codes = [i.code for i in s.build().lint()]
        assert "declared_feature_absent" not in codes

    def test_phantom_hole_over_solid_od_warns(self):
        # #487 review: a declared bore coincident with the part's external OD (same ⌀ + axis) must
        # still warn — there is no bore, the callout renders over solid material. Exercises the real
        # analyse_cylinders external flag end-to-end.
        part = Cylinder(10, 40)  # solid shaft, OD ⌀20, no bore
        s = Sheet(part)
        s.envelope()
        s.hole(Cylinder(10, 40))  # declares a phantom ⌀20 bore
        assert "declared_feature_absent" in [i.code for i in s.build().lint()]

    def test_detection_path_never_reconciles(self):
        # No model= → _model_declared is False → the check is a no-op even on a featureless box.
        dwg = build_drawing(Box(120, 80, 20))
        assert "declared_feature_absent" not in [i.code for i in dwg.lint()]
