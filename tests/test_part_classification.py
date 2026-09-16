"""Rotational and prismatic part-classification behavior."""

import math

import pytest
from build123d import Box, Compound, Cylinder, Pos

from draftwright import build_drawing
from draftwright.analysis import (
    _is_rotational,
)
from draftwright.drawing import analyse_cylinders


class TestIsRotational:
    def test_plain_cylinder(self):
        assert _is_rotational(30.0, 30.0, 30.0, 0.0)

    def test_prismatic_envelope(self):
        assert not _is_rotational(100.0, 60.0, 24.0, 0.0)

    def test_small_boss_on_square_plate(self):
        assert not _is_rotational(100.0, 100.0, 40.0, 0.0)

    def test_off_centre_boss(self):
        assert not _is_rotational(100.0, 100.0, 84.0, 8.0)

    def test_no_external_cylinder(self):
        # Bores never qualify as an OD — od_diam is None for hole-only parts
        assert not _is_rotational(100.0, 100.0, None, 0.0)

    @pytest.mark.timeout(60)
    def test_square_plate_with_big_bore_is_prismatic(self):
        # ø85 bore in a 100-square plate: fills the envelope and is
        # concentric, but it is a hole — not an OD.
        part = Box(100, 100, 10) - Cylinder(42.5, 12)
        dwg = build_drawing(part)
        assert "dim_od" not in dwg.annotations()

    @pytest.mark.timeout(60)
    def test_off_centre_bore_is_prismatic(self):
        part = Box(100, 100, 20) - Pos(8, 0, 0) * Cylinder(42, 30)
        dwg = build_drawing(part)
        assert "dim_od" not in dwg.annotations()

    @pytest.mark.timeout(60)
    def test_mirrored_turned_part_stays_rotational(self):
        # Mirroring flips face orientations AND the cylinder frame handedness;
        # the external/bore split must survive it.
        from build123d import Plane, mirror

        part = mirror(Cylinder(30, 40) - Cylinder(10, 40), about=Plane.XZ)
        z_cyls, _ = analyse_cylinders(part)
        flags = {c["diameter"]: c["external"] for c in z_cyls}
        assert flags[60.0] is True and flags[20.0] is False
        dwg = build_drawing(part)
        assert "dim_od" in dwg.annotations()

    @pytest.mark.timeout(60)
    def test_dim_od_uses_the_external_cylinder(self):
        # An internal recess wider than the boss must not be labelled as the
        # OD: dim_od comes from the classified external cylinder.
        part = (
            Box(100, 100, 20)
            + Pos(0, 0, 20) * Cylinder(42.5, 20)
            - Pos(0, 0, -7.5) * Cylinder(45, 5)
        )
        dwg = build_drawing(part)
        assert dwg.get_annotation("dim_od").label == "ø85"

    def test_rotational_od_bore_labels_are_planner_fed(self):
        # #754: render_rotational consumes the feature's planned DimensionGroup, so an
        # authored tolerance folded by plan_dimensions reaches the OD AND bore labels.
        # Pre-#754 it read the raw RotationalFeature fields and the decoration was lost.
        from draftwright.model.ir import Frame, RotationalFeature

        part = Cylinder(15, 10) - Cylinder(4, 20)  # ø30 OD, ø8 bore, Z axis
        rot = RotationalFeature(frame=Frame((0.0, 0.0, 0.0), "z"), od=30.0, bores=(8.0,))
        dwg = build_drawing(part, model=[rot], decorations={(rot, "diameter"): 0.2})
        assert str(dwg.get_annotation("dim_od").label) == "ø30 ±0.2"
        assert [str(o.label) for n, o in dwg.iter_annotations() if n.startswith("ldr_z")] == [
            "ø8 ±0.2"
        ]

    def test_rotational_plain_labels_unchanged(self):
        # No authored decoration → labels are the plain planned value, identical to the
        # pre-#754 raw-field output (placement/centrelines are furniture, unchanged).
        dwg = build_drawing(Cylinder(15, 10) - Cylinder(4, 20))
        assert str(dwg.get_annotation("dim_od").label) == "ø30"
        assert [str(o.label) for n, o in dwg.iter_annotations() if n.startswith("ldr_z")] == ["ø8"]

    @pytest.mark.timeout(60)
    def test_unrounded_od_does_not_duplicate_a_bore_leader(self, monkeypatch):
        # analyse_cylinders rounds diameters at source today, which masks the
        # #86 scenario — but the OD/bore exclusion must not depend on that:
        # feature records may carry raw OCCT diameters after the #87 lift.
        # With an unrounded OD (59.9999999 vs the dedup'd 60.0), a float !=
        # leaks the OD into the bore leaders as a duplicate ø60 callout.
        import draftwright.analysis as md

        real = md.analyse_cylinders

        def unrounded(part):
            z_cyls, cross_cyls = real(part)
            for c in z_cyls:
                if c["external"]:
                    c["diameter"] = 59.9999999
            return z_cyls, cross_cyls

        monkeypatch.setattr(md, "analyse_cylinders", unrounded)
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert dwg.get_annotation("dim_od").label == "ø60"
        leader_labels = [a.label for n, a in dwg.iter_annotations() if n.startswith("ldr_z")]
        assert leader_labels == ["ø20"]

    @pytest.mark.timeout(60)
    def test_lint_reuses_build_drawing_cylinder_analysis(self, monkeypatch):
        # build_drawing seeds the cache, so lint()/export() must not re-scan
        # the solid with analyse_cylinders.
        # (the package re-exports the make_drawing *function*, shadowing the
        # submodule attribute; analysis has no such name collision.)
        import draftwright.analysis as md

        dwg = build_drawing(Box(30, 20, 10))
        calls = {"n": 0}
        real = md.analyse_cylinders

        def counting(part):
            calls["n"] += 1
            return real(part)

        monkeypatch.setattr(md, "analyse_cylinders", counting)
        dwg.lint()
        dwg.lint()
        assert calls["n"] == 0


class TestTurnedPlusDrilledFlange:
    """A flange is turned (square envelope, dominant OD) yet carries discrete
    off-axis holes — the most common turned-and-drilled part. The binary
    turned/prismatic split (#10) classifies it rotational and then withholds
    every hole callout, location dim, and bolt-circle furniture, leaving the
    bolt holes with bare centre marks.
    """

    @staticmethod
    def _flange():
        # ø100 × 20 disc, ø30 central bore, 6 × ø8 holes on an ø80 bolt circle.
        flange = Cylinder(50, 20) - Cylinder(15, 20)
        for i in range(6):
            ang = 2 * math.pi * i / 6
            flange -= Pos(40 * math.cos(ang), 40 * math.sin(ang), 0) * Cylinder(4, 20)
        return flange

    @pytest.mark.timeout(60)
    def test_flange_classifies_rotational_with_od(self):
        # The turned base set is correct today and must stay so.
        dwg = build_drawing(self._flange())
        assert dwg._analysis.is_rotational
        assert "dim_od" in dwg.annotations()
        assert "centerline_front" in dwg.annotations()

    @pytest.mark.timeout(60)
    def test_flange_composes_od_with_bolt_circle_furniture(self):
        dwg = build_drawing(self._flange())
        # Turned base set — already works.
        assert "dim_od" in dwg.annotations()
        # Feature-driven furniture for the bolt circle — withheld today.
        assert any(n.startswith("hc_") for n in dwg.annotations()), "expected hole callouts"
        assert any(n.startswith("m_loc") for n in dwg.annotations()), "expected location dims"
        assert any(n.startswith("bc_") for n in dwg.annotations()), (
            "expected bolt-circle furniture"
        )


class TestTurnedMultiBoreOverflow:
    """A turned part with 4+ distinct concentric bores. The leader stack caps
    at three (`bores[:3]`); the overflow must not vanish silently — it should
    be annotated or surfaced through the coverage lint (#10).
    """

    @staticmethod
    def _telescoping():
        # ø80 OD with four concentric counterbore steps: ø60 / ø44 / ø30 / ø16.
        part = Cylinder(40, 80)
        part -= Pos(0, 0, 30) * Cylinder(30, 20)
        part -= Pos(0, 0, 10) * Cylinder(22, 30)
        part -= Pos(0, 0, -10) * Cylinder(15, 30)
        part -= Pos(0, 0, -30) * Cylinder(8, 20)
        return part

    @pytest.mark.timeout(60)
    def test_no_bore_silently_dropped(self):
        dwg = build_drawing(self._telescoping())
        a = dwg._analysis
        bores = {d for d in a.z_diams if d != a.od_diam}
        assert bores == {60.0, 44.0, 30.0, 16.0}
        annotated = {
            float(ann.label.lstrip("ø"))
            for n, ann in dwg.iter_annotations()
            if n.startswith("ldr_z")
        }
        # Acceptance (#10): annotate all, or surface the overflow via lint —
        # never drop a bore with no trace.
        if annotated != bores:
            assert any(i.code == "feature_not_dimensioned" for i in dwg.lint()), (
                f"bores {bores - annotated} dropped with no lint coverage"
            )


class TestStepHeightThreshold:
    """The step-height gate dimensions a step only when it projects to ≥20 mm
    on the page (`(z - bb.min.Z) * SCALE >= 20`). That page-mm cutoff is
    incidental: a genuine, well-separated step should be dimensioned whatever
    its scaled height (#13).
    """

    @staticmethod
    def _stepped(base_h):
        # Prismatic two-level block: a base of height ``base_h`` (bottom at
        # z=0) with a smaller platform on top. The single interior step face
        # sits ``base_h`` above the part bottom, so at 1:1 it projects to
        # exactly ``base_h`` mm on the page.
        base = Pos(0, 0, base_h / 2) * Box(100, 100, base_h)
        platform = Pos(0, 0, base_h + 5) * Box(60, 60, 10)
        return base + platform

    @pytest.mark.timeout(60)
    def test_step_above_page_gate_is_dimensioned(self):
        # 21 mm of page height — dimensioned. Guards the gate's upper side.
        dwg = build_drawing(self._stepped(21), scale=1.0, page="A2")
        assert any(n.startswith("dim_step") for n in dwg.annotations())

    @pytest.mark.timeout(60)
    def test_real_step_just_below_page_gate_still_dimensioned(self):
        dwg = build_drawing(self._stepped(19), scale=1.0, page="A2")
        assert any(n.startswith("dim_step") for n in dwg.annotations())


class TestSilhouetteCircleRefit:
    """Imported-STEP turned features (and concentric arc features like gear-tooth
    tips) project via HLR as faceted BSpline silhouette polylines, not true
    circles (#67). ``add_view`` refits any silhouette whose vertices are
    equidistant from a recognised revolution axis back to an exact circle/arc, so
    DXF carries CIRCLE entities and feature radii read exactly. A silhouette with
    no supporting revolution axis is left untouched."""

    @staticmethod
    def _nurbs(shape):
        # NurbsConvert erases analytic surface types (Cylinder -> BSplineSurface),
        # mimicking a STEP whose turned features come in as NURBS — and forcing
        # the silhouette to project as a spline rather than a native circle.
        from build123d import Solid
        from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert

        return Solid(BRepBuilderAPI_NurbsConvert(shape.wrapped, True).Shape())

    @staticmethod
    def _circle_radii(view_compound):
        from build123d import GeomType

        return sorted(
            round(e.radius, 2) for e in view_compound.edges() if e.geom_type == GeomType.CIRCLE
        )

    @pytest.mark.timeout(120)
    def test_analytic_revolution_silhouette_is_circle(self):
        # Baseline: build123d already recovers an analytic cylinder's on-axis
        # silhouette as a circle. The refit pass must not regress this.
        from build123d import GeomType

        dwg = build_drawing(Cylinder(8, 30), page="A4")
        vis, _ = dwg.views["plan"]
        assert any(e.geom_type == GeomType.CIRCLE for e in vis.edges())

    @pytest.mark.timeout(120)
    def test_concentric_nurbs_ring_silhouette_refit_to_circle(self):
        # The inner analytic cylinder supplies the Z axis; the outer NURBS ring's
        # silhouette (a faceted BSpline at R18) is concentric, so it refits to an
        # exact circle at the true radius instead of staying a spline.
        inner = Cylinder(5, 12)
        outer = self._nurbs(Cylinder(18, 4))
        dwg = build_drawing(Compound([inner, outer]), page="A4")
        vis, _ = dwg.views["plan"]
        radii = [round(r / dwg.scale, 2) for r in self._circle_radii(vis)]
        assert 18.0 in radii  # outer NURBS rim recovered as an exact circle
        assert 5.0 in radii  # inner analytic bore

    @pytest.mark.timeout(120)
    def test_no_axis_silhouette_left_untouched(self):
        # A lone NURBS cylinder has no recognised revolution face (NurbsConvert
        # erased its analytic type), so there is no axis to refit against. The
        # silhouette must stay a spline rather than fabricate a circle.
        from build123d import GeomType

        part = self._nurbs(Cylinder(8, 30))
        dwg = build_drawing(part, page="A4")
        vis, _ = dwg.views["plan"]
        assert all(e.geom_type != GeomType.CIRCLE for e in vis.edges())
