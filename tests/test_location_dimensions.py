"""Automatic feature-location dimensions and section interaction."""

import pytest
from _drawing_helpers import ink_crossings_named as _ink_crossings_named
from build123d import Box, Compound, Cylinder, Edge, Pos

from draftwright import build_drawing


@pytest.fixture(scope="module")
def plate_drawing():
    # corners (a square → bolt-circle group) + centre cbore stack +
    # off-centre blind hole: refs are the BC centre (= cbore hole,
    # deduped) and the blind hole
    part = (
        Box(100, 100, 20)
        - Pos(35, 35, 0) * Cylinder(5, 20)
        - Pos(-35, 35, 0) * Cylinder(5, 20)
        - Pos(35, -35, 0) * Cylinder(5, 20)
        - Pos(-35, -35, 0) * Cylinder(5, 20)
        - Cylinder(4, 20)
        - Pos(0, 0, 7) * Cylinder(8, 6)
        - Pos(-20, -10, 6) * Cylinder(6, 8)
    )
    return build_drawing(part)


class TestLocationDimsAndSection:
    """Baseline location dims (#93) and auto section views (#94)."""

    @pytest.mark.timeout(120)
    def test_x_dims_above_the_plan_view(self, plate_drawing):
        labels = {a.label for n, a in plate_drawing.iter_annotations() if n.startswith("m_locx")}
        assert labels == {"50", "30"}
        plan_top = plate_drawing.views["plan"][0].bounding_box().max.Y
        assert all(
            a.dim_level_y > plan_top
            for n, a in plate_drawing.iter_annotations()
            if n.startswith("m_locx")
        )

    @pytest.mark.timeout(120)
    def test_y_dims_above_the_side_view(self, plate_drawing):
        labels = {a.label for n, a in plate_drawing.iter_annotations() if n.startswith("m_locy")}
        assert labels == {"50", "40"}
        side_top = plate_drawing.views["side"][0].bounding_box().max.Y
        assert all(
            a.dim_level_y > side_top
            for n, a in plate_drawing.iter_annotations()
            if n.startswith("m_locy")
        )

    @pytest.mark.timeout(120)
    def test_section_view_with_cutting_plane_markers(self, plate_drawing):
        assert "section_aa" in plate_drawing.views
        assert plate_drawing.get_annotation("section_caption").label == "SECTION A–A"
        assert plate_drawing.get_annotation("section_line").is_centerline
        assert plate_drawing.get_annotation("section_a_left").label == "A"
        assert plate_drawing.get_annotation("section_a_right").label == "A"

    @pytest.mark.timeout(120)
    def test_section_end_arrows_present(self, plate_drawing):
        # ISO 128-44: cutting-plane ends must have wings + solid filled arrowheads
        for side in ("left", "right"):
            wing = plate_drawing.get_annotation(f"section_wing_{side}")
            arrow = plate_drawing.get_annotation(f"section_arrow_{side}")
            # wing is a single-edge Compound (the perpendicular stub stroke)
            assert len(wing.edges()) == 1
            # arrow is a filled solid (Arrow produces faces, not open barbs)
            assert len(list(arrow.faces())) >= 1
        # Stems sit below the cutting line; the arrow tips point toward retained +Y.
        sl_y = plate_drawing.get_annotation("section_line").bounding_box().min.Y
        wl_y = plate_drawing.get_annotation("section_wing_left").bounding_box().min.Y
        assert wl_y < sl_y

    @pytest.mark.timeout(120)
    def test_section_hatch_present_and_45_degrees(self, plate_drawing):
        # ISO 128-50: 45° hatching on the cut face
        assert "section_hatch" in plate_drawing.annotations()
        hatch = plate_drawing.get_annotation("section_hatch")
        edges = list(hatch.edges())
        assert len(edges) > 0
        # Each hatch edge should be at approximately 45° (slope ≈ 1)
        for e in edges:
            p0, p1 = e.position_at(0), e.position_at(1)
            dx, dy = p1.X - p0.X, p1.Y - p0.Y
            if abs(dx) > 0.01:
                assert abs(dy / dx - 1.0) < 0.05  # slope ≈ 1 → 45°

    @pytest.mark.timeout(120)
    def test_sheet_is_lint_clean(self, plate_drawing):
        assert [i for i in plate_drawing.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_through_only_plate_gets_no_section(self):
        part = Box(80, 60, 10) - Pos(20, 10, 0) * Cylinder(5, 10)
        dwg = build_drawing(part)
        assert "section_aa" not in dwg.views
        assert "section_line" not in dwg.annotations()
        # but it still gets located
        assert any(n.startswith("m_locx") for n in dwg.annotations())

    @pytest.mark.timeout(120)
    def test_underside_cbore_triggers_a_section(self):
        # The issue's acceptance case: a blind cbore from the underside is
        # hidden-line-only everywhere — the section shows it as line-work.
        part = Box(80, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)
        dwg = build_drawing(part)
        assert "section_aa" in dwg.views
        vis, _hid = dwg.views["section_aa"]
        assert len(vis.edges()) > 0
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_declared_blind_hole_reserves_section_layout(self):
        from draftwright.model import hole

        part = Box(40, 10, 20)
        dwg = build_drawing(
            part,
            model=[hole(diameter=4, at=(0, 0, 10), axis="z", through=False, depth=6)],
        )
        assert dwg._analysis.layout_section is True
        assert dwg.scale == 1.0
        assert "section_aa" in dwg.views

    @pytest.mark.timeout(120)
    def test_rotational_concentric_bore_does_not_reserve_section_layout(self):
        part = Cylinder(20, 20) - Cylinder(4, 20) - Pos(0, 0, 7) * Cylinder(8, 6)
        dwg = build_drawing(part)
        assert dwg._analysis.is_rotational is True
        assert dwg._analysis.layout_section is False
        assert "section_aa" not in dwg.views

    @pytest.mark.timeout(120)
    def test_section_clears_the_step_dim_ladder(self):
        # Step dims are placed before the section; the section's room check
        # must clear their labels (here: no room at all → skip, never a
        # section with a dim ladder through it).
        part = (
            Box(40, 12, 40)
            - Pos(10, 0, 20) * Box(20, 12, 40)
            - Pos(-10, 0, 0) * Cylinder(3, 40)
            - Pos(-10, 0, 16) * Cylinder(5, 8)
        )
        dwg = build_drawing(part)
        if "section_aa" in dwg.views:
            sb = dwg.views["section_aa"][0].bounding_box()
            for name, ann in dwg.iter_annotations():
                if name.startswith("dim_step") and getattr(ann, "label_bbox", None):
                    x0, y0, x1, y1 = ann.label_bbox
                    assert not (
                        x1 > sb.min.X and x0 < sb.max.X and y1 > sb.min.Y and y0 < sb.max.Y
                    )
        # `section_dropped` is expected here and is the POINT of #1190: this part's
        # cutting-plane ink crosses a required hole-callout leader, so the section is
        # withheld. That was previously silent — the assertion below passed because
        # nothing recorded the omission, not because nothing was omitted.
        assert dwg.section_decision["status"] == "skipped"
        assert dwg.section_decision["reason"] == "leader_conflict"
        assert [
            i for i in dwg.lint() if i.severity != "info" and i.code != "section_dropped"
        ] == []

    @pytest.mark.timeout(120)
    def test_side_drilled_callouts_survive_capacity_aware_carve(self):
        # Each unpatterned side-drilled hole reserves its own row as a keep-out
        # band (#318), so its callout is pushed off its own natural Y into the
        # nearest carved segment. Four holes packed close enough together that
        # their bands merge push the callout selection past a single tight
        # segment: a per-segment-only assignment (the #381 regression) can drop
        # one that overflows its nearest segment even though a farther segment
        # has spare room — the global priority-ordered assignment must not.
        part = Box(60, 40, 80)
        for z, r in ((-20, 1.0), (-12, 1.2), (-4, 1.4), (20, 1.6)):
            part -= Pos(0, 0, z) * Cylinder(r, 60, rotation=(0, 90, 0))
        dwg = build_drawing(part)
        assert len([n for n in dwg.annotations() if n.startswith("hc_side")]) == 4
        # Measured view-block clearance gives the four callouts clear candidates instead of
        # letting the envelope length `80` run through every label.
        rest = _ink_crossings_named(dwg, [])
        assert [i for i in rest if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_fully_blocked_plan_strip_defers_instead_of_unsafe_snap(self):
        # A stepped part whose bands+obstacles carve leaves the plan view's left
        # strip with no free segment at all: unlike the band-only carve (which may
        # safely snap to the nearest strip edge), a snap here isn't rechecked
        # against every other blocking interval and can land inside a different
        # one. The bands+obstacles call must defer cleanly to the bands-only
        # baseline instead of snapping (distinct geometry from
        # test_section_clears_the_step_dim_ladder, which exercises the same
        # defer but wasn't written to assert it).
        part = (
            Box(44, 12, 44)
            - Pos(11, 0, 22) * Box(22, 12, 44)
            - Pos(-11, 0, 0) * Cylinder(3, 44)
            - Pos(-11, 0, 18) * Cylinder(5, 8)
        )
        dwg = build_drawing(part)
        # See the sibling test above: the withheld section is now reported rather than
        # silent (#1190), and this assertion previously passed on that silence.
        assert dwg.section_decision["status"] == "skipped"
        assert [
            i for i in dwg.lint() if i.severity != "info" and i.code != "section_dropped"
        ] == []

    @pytest.mark.timeout(120)
    def test_linear_array_locates_its_nearest_member(self):
        # The baseline dim goes to the hole nearest the datum corner; the
        # pitch dim chains the rest outward.
        part = Box(100, 50, 10)
        for x in (-30, -10, 10, 30):
            part = part - Pos(x, 0, 6) * Cylinder(4, 8)
        dwg = build_drawing(part)
        labels = sorted(a.label for n, a in dwg.iter_annotations() if n.startswith("m_locx"))
        assert labels == ["20"]

    @pytest.mark.timeout(120)
    def test_section_letters_clear_the_bolt_circle(self, plate_drawing):
        # The corner-hole bolt circle sweeps wider than the part; the
        # cutting-plane letters must sit outside it (lint flags the overlap
        # otherwise).
        codes = [i.code for i in plate_drawing.lint() if i.severity != "info"]
        assert "label_centerline_overlap" not in codes

    @pytest.mark.timeout(120)
    def test_y_dims_tier_past_side_pitch_dims(self):
        # An x-axis array's pitch dim lives above the side view too — the
        # Y-location ladder must start beyond it, not on top of it.
        part = Box(60, 40, 30) - Pos(0, 5, 11) * Cylinder(3, 8)
        for y in (-12, 0, 12):
            part = part - Pos(15, y, 8) * Cylinder(2, 60, rotation=(0, 90, 0))
        dwg = build_drawing(part)
        locy = [a.dim_level_y for n, a in dwg.iter_annotations() if n.startswith("m_locy")]
        pitch = [
            a.dim_level_y for n, a in dwg.iter_annotations() if n.startswith("dim_pitch_side")
        ]
        assert locy and pitch
        assert min(abs(ly - py) for ly in locy for py in pitch) >= 8

    @pytest.mark.timeout(120)
    def test_pmi_compound_draws_the_solid_only(self):
        # AP242 STEP with PMI imports as a Compound of solid + annotation
        # geometry (plane border wires, leader curves). The drawing is of
        # the solids: no phantom rectangles in the views, no bbox inflation
        # corrupting the scale and envelope dims, and the section cut works.
        solid = Box(80, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)
        pmi = Edge.make_line((-80, 0, 40), (80, 0, 40))  # well outside the part
        part = Compound(children=[solid, pmi])
        dwg = build_drawing(part)
        assert "section_aa" in dwg.views
        assert dwg.get_annotation("dim_height").label == "20"  # not the PMI z-extent
        # the views contain no line-work above the solid's top
        for vis, hid in dwg.views.values():
            assert vis.bounding_box().size.Y < 200  # sanity: no 160mm phantom

    @pytest.mark.timeout(60)
    def test_rotational_part_gets_neither(self):
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert "section_aa" not in dwg.views
        assert not any(n.startswith(("m_loc", "dim_loc")) for n in dwg.annotations())
