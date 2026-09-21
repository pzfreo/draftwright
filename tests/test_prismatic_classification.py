"""Prismatic-part classification and annotation routing."""

import pytest
from _drawing_helpers import ink_crossings_named as _ink_crossings_named
from _kernel import B123D_GE_011, SKIP_011
from build123d import Axis, Box, Cylinder, Pos

from draftwright import build_drawing

_skip_011 = pytest.mark.skipif(B123D_GE_011, reason=SKIP_011)


class TestPrismaticClassification:
    @pytest.mark.timeout(60)
    def test_prismatic_part_with_bores_skips_turned_annotations(self):
        # A housing-like plate: Z-axis bores exist, but they are holes — not
        # an OD. dim_od / centrelines / ldr_z* would all be wrong.
        part = (
            Box(100, 60, 20)
            - Pos(20, 10, 0) * Cylinder(5, 30)
            - Pos(-30, -15, 0) * Cylinder(8, 30)
        )
        dwg = build_drawing(part)
        assert "dim_od" not in dwg.annotations()
        assert "centerline_front" not in dwg.annotations()
        assert "centerline_side" not in dwg.annotations()
        assert not any(name.startswith("ldr_z") for name in dwg.annotations())

    @pytest.mark.timeout(60)
    def test_rotational_part_keeps_turned_annotations(self):
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert "dim_od" in dwg.annotations()
        assert "centerline_front" in dwg.annotations()
        assert "ldr_z0" in dwg.annotations()

    @pytest.mark.timeout(60)
    def test_z_axis_stepped_shaft_calls_out_step_diameters(self):
        # A vertical (Z-axis) stepped shaft: dim_od dimensions the OD, and the
        # intermediate step diameter gets a ø callout in the left-hand column
        # (#131 — the page-Y mirror of the X-axis #77 row-below). Without it the
        # ⌀20 step surfaces only as feature_not_dimensioned.
        shaft = Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(10, 30)
        dwg = build_drawing(shaft)
        diam_labels = {o.label for o in dwg.items if getattr(o, "label", "") and "ø" in o.label}
        assert "ø30" in diam_labels and "ø20" in diam_labels
        # The step diameter is placed by the IR renderer's left-hand column (#131,
        # migrated to from_model.render_diameters → m_dia_z names).
        assert any(name.startswith("m_dia_z") for name in dwg.annotations())
        assert not [i for i in dwg.lint() if i.code == "feature_not_dimensioned"]

    @pytest.mark.timeout(60)
    def test_locates_side_drilled_holes(self):
        # A side-drilled (X-axis) hole appears as a circle in the side view and
        # must be located THERE. _add_location_dims was plan-view (z-hole) only,
        # so off-axis holes got a diameter callout but no position (#133).
        from build123d import Rot

        part = Box(12, 40, 30) - Pos(0, 8, 6) * Rot(0, 90, 0) * Cylinder(3, 12)
        dwg = build_drawing(part)
        loc = {name for name in dwg.annotations() if name.startswith("dim_loc")}
        # Fully located: the in-plane (Y) offset below the side view AND the
        # height (Z) offset to its right (#133). The Z routes to whichever right
        # strip is free — side here (no section view to contend it).
        assert any(n.startswith("dim_loc_side_y") for n in loc), "in-plane offset missing"
        assert any(n.endswith("_z2100") for n in loc), "height offset missing"
        # The location dims must never overprint the callouts/section that share
        # the right strips — the sheet stays lint-clean (#133 rework).
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_side_view_location_dim_stacks_inside_the_envelope(self):
        # ISO stacking: the overall (envelope) dim sits OUTERMOST, the feature/location
        # dim nearer the view. A side-drilled hole's in-plane location must therefore
        # be CLOSER to the side view than the envelope-depth dim. The inverted stack
        # (envelope innermost) forced the shorter location dim's arrows to flip outward
        # and clash with the envelope (GRM-01 / GRM-02).
        from build123d import Rot

        # Off-centre hole (y=2, not the centreline) so the location dim is real, not a
        # redundant centred one — this isolates the stacking order.
        part = Box(12, 11, 40) - Pos(0, 2, 6) * Rot(0, 90, 0) * Cylinder(3, 12)
        dwg = build_drawing(part)
        env = dwg.get_annotation("m_env_depth")
        loc = [o for n, o in dwg.iter_annotations() if n.startswith("dim_loc_side_y")]
        assert env is not None and loc, "expected an envelope-depth dim and a side location dim"

        def ymid(o):
            bb = o.bounding_box()
            return (bb.min.Y + bb.max.Y) / 2

        # The below strip extends downward from the side view, so nearer the view =
        # higher Y. The location dim must sit nearer the view than the overall dim.
        assert min(ymid(o) for o in loc) > ymid(env), "location must stack inside the envelope"
        # Measured block repacking now gives the callout enough room to take its clear
        # candidate; retain the exact-ink assertion so this cannot regress to the old
        # `40`-through-`⌀6 THRU` crossing unnoticed.
        rest = _ink_crossings_named(dwg, [])
        assert [i for i in rest if i.severity != "info"] == []

    def test_envelope_depth_survives_many_side_location_dims(self):
        # The mandatory overall depth dim must always be placed, even when several
        # side-drilled holes fill the side-below strip with location dims. The
        # location pass now queues with the envelope (for ISO stacking), and the
        # envelope's corridor priority means best-effort location dims can never starve it
        # (the #316-review regression, now enforced without a manual reservation).
        from build123d import Cylinder, Pos, Rot

        part = Box(12, 24, 60)
        for y, z in [(-9, -20), (-5, -8), (7, 4), (10, 16)]:
            part -= Pos(0, y, z) * Rot(0, 90, 0) * Cylinder(1.5, 12)
        dwg = build_drawing(part)
        ylocs = [n for n in dwg.annotations() if n.startswith("dim_loc_side_y")]
        assert len(ylocs) >= 2, "expected several side-below location dims for strip pressure"
        assert "m_env_depth" in dwg.annotations(), "the mandatory overall depth dim was starved"
        assert dwg.lint_summary()["by_code"].get("missing_principal_dimension", 0) == 0

        def ymid(o):
            bb = o.bounding_box()
            return (bb.min.Y + bb.max.Y) / 2

        env = dwg.get_annotation("m_env_depth")
        assert all(ymid(dwg.get_annotation(n)) > ymid(env) for n in ylocs), (
            "locations must stack inside"
        )

    def test_a_suppressed_envelope_tier_is_not_reserved(self):
        # When the planner suppresses the depth dim, the side-below envelope-tier reservation
        # must NOT fire — reserving a tier render_envelope never claims would needlessly
        # shrink the strip and drop a side location that otherwise fits (#316 review).
        #
        # The lever used to be a square footprint. #997 removed that suppression (a square
        # part states both extents), so this now uses the X-turned rotational rule, which is
        # still a live suppression: the OD conveys the cross-axis extent. The subject of the
        # test — don't reserve space for a dim that will never be drawn — is unchanged.
        from build123d import Cylinder, Pos, Rot

        part = Rot(0, 90, 0) * Cylinder(10, 40) - Pos(0, 4, 0) * Rot(0, 90, 0) * Cylinder(2, 50)
        dwg = build_drawing(part)
        assert "m_env_depth" not in dwg.annotations(), "X-turned → depth suppressed by the OD"
        assert [n for n in dwg.annotations() if n.startswith("dim_loc_side_y")], (
            "location was dropped"
        )
        assert dwg.lint_summary()["by_code"].get("off_axis_location_dropped", 0) == 0

    @pytest.mark.timeout(60)
    def test_locates_every_side_drilled_hole_not_just_the_first(self):
        # Two side-drilled (Y-axis) holes at distinct x: each must get its own
        # in-plane (X) location dim. The first hole's own front-view callout sits
        # in the below strip, so the location dim collided and was DROPPED after a
        # single tier — only the first hole ended up located (#225). _place now
        # retries the next tier past the callout, so both are located.
        from build123d import Rot

        part = (
            Box(80, 40, 30)
            - Pos(-20, 0, 5) * Rot(90, 0, 0) * Cylinder(2.5, 50)
            - Pos(25, 0, -5) * Rot(90, 0, 0) * Cylinder(4, 50)
        )
        dwg = build_drawing(part)
        xlocs = {n for n in dwg.annotations() if n.startswith("dim_loc_front_x")}
        assert len(xlocs) == 2, f"both side-drilled holes must be located, got {xlocs}"
        # The X offsets are the #225 subject and both land. The shared placement solve now
        # also admits both Z-height companions, so no completeness error remains.
        issues = dwg.lint()
        assert not [i for i in issues if i.severity == "error"]
        assert not [i for i in issues if i.code == "off_axis_location_dropped"]

    @pytest.mark.timeout(60)
    def test_corner_fillets_do_not_make_a_plate_rotational(self):
        # Big quarter-cylinder corner fillets on a square plate must not be
        # mistaken for an OD.
        from build123d import fillet

        box = Box(60, 60, 20)
        part = fillet(box.edges().filter_by(Axis.Z), 25)
        dwg = build_drawing(part)
        assert "dim_od" not in dwg.annotations()
