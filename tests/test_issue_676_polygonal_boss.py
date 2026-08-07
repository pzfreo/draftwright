"""#676: evidence-safe recognition and rendering of polygonal prism bosses."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, Compound, Polygon, Pos, RegularPolygon, Rot, extrude, import_step

from draftwright import build_drawing
from draftwright.model import PolygonalBossFeature, build_part_model
from draftwright.model.ir import RequestedDimension
from draftwright.recognition import PolygonalBoss, recognise_polygonal_bosses
from draftwright.recognition.polygonal_bosses import _normal

CTC01 = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap203.stp"


def _hex_prism(radius: float = 20, height: float = 30):
    return extrude(RegularPolygon(radius, 6), height)


def _polygon_prism(sides: int, radius: float = 20, height: float = 30):
    return extrude(RegularPolygon(radius, sides), height)


def _fused_hex_boss(*, angle: float = 0):
    base = Box(100, 80, 10)
    boss = Pos(0, 0, 5) * Rot(0, 0, angle) * _hex_prism()
    return base + boss


def _polygon_from_supports(
    supports: tuple[float, ...], *, angles: tuple[float, ...] = (0, 60, 120, 180, 240, 300)
):
    normals = [(math.cos(math.radians(angle)), math.sin(math.radians(angle))) for angle in angles]
    points = []
    for index, ((ax, ay), a_support) in enumerate(zip(normals, supports, strict=True)):
        bx, by = normals[(index + 1) % len(normals)]
        b_support = supports[(index + 1) % len(supports)]
        determinant = ax * by - ay * bx
        points.append(
            (
                (a_support * by - ay * b_support) / determinant,
                (ax * b_support - a_support * bx) / determinant,
            )
        )
    return Polygon(*points)


def test_a_face_with_no_usable_normal_cannot_supply_boss_evidence():
    class UnusableFace:
        def center(self):
            raise ValueError("degenerate face")

    assert _normal(UnusableFace()) is None


def test_a_bounded_fused_hex_boss_carries_manufacturing_evidence():
    (boss,) = recognise_polygonal_bosses(_fused_hex_boss())

    assert isinstance(boss, PolygonalBoss)
    assert boss.axis == "z"
    assert boss.side_count == 6
    assert boss.center == pytest.approx((0, 0, 20))
    assert boss.across_flats == pytest.approx(40 * math.cos(math.pi / 6))
    assert (boss.base, boss.top, boss.height) == pytest.approx((5, 35, 30))
    assert len(boss.flat_directions) == 6
    assert len(boss.flat_centres) == 6


def test_recognition_lowers_once_to_a_dedicated_dimensioning_feature():
    part = _fused_hex_boss()
    records = recognise_polygonal_bosses(part)

    model = build_part_model(part, polygonal_bosses=records)
    (feature,) = [item for item in model.features if item.kind == "polygonal_boss"]

    assert isinstance(feature, PolygonalBossFeature)
    assert feature.side_count == 6
    assert [param.parameter_id for param in feature.parameters()] == [
        "polygon_across_flats.length",
        "boss_height.length",
    ]
    assert [param.value for param in feature.parameters()] == pytest.approx(
        [records[0].across_flats, records[0].height]
    )
    assert feature.references() == []
    assert feature.flat_directions == records[0].flat_directions


def test_in_plane_rotation_preserves_the_geometric_across_flats():
    plain = recognise_polygonal_bosses(_fused_hex_boss())[0]
    rotated = recognise_polygonal_bosses(_fused_hex_boss(angle=17))[0]

    assert rotated.across_flats == pytest.approx(plain.across_flats)
    assert rotated.flat_centres != plain.flat_centres


def test_axis_line_comes_from_support_planes_not_side_face_centroids():
    local_notch = Pos(15, 0, 10) * Box(20, 6, 30)

    (boss,) = recognise_polygonal_bosses(_fused_hex_boss() - local_notch)

    assert boss.center[:2] == pytest.approx((0, 0), abs=1e-4)


def test_a_six_sided_ring_without_regular_opposed_planes_is_rejected():
    irregular = Polygon((20, 0), (10, 17.32), (-10, 17.32), (-20, 0), (-10, -17.32), (14, -14))
    part = Box(100, 80, 10) + Pos(0, 0, 5) * extrude(irregular, 30)

    assert recognise_polygonal_bosses(part) == []


def test_a_sub_tolerance_projection_is_not_inferred_as_a_boss():
    shallow = Box(100, 80, 10) + Pos(0, 0, 5) * _hex_prism(height=0.1)

    assert recognise_polygonal_bosses(shallow) == []


def test_locally_regular_angles_do_not_substitute_for_opposed_flat_directions():
    almost_regular = _polygon_from_supports(
        (20, 20, 20, 20, 20, 20),
        angles=(0, 61.9, 123.8, 185.7, 243.8, 301.9),
    )
    part = Box(100, 80, 10) + Pos(0, 0, 5) * extrude(almost_regular, 30)

    assert recognise_polygonal_bosses(part) == []


def test_opposed_pairs_with_different_widths_do_not_define_one_across_flats():
    unequal_widths = _polygon_from_supports((20, 20, 20, 22, 20, 20))
    part = Box(100, 80, 10) + Pos(0, 0, 5) * extrude(unequal_widths, 30)

    assert recognise_polygonal_bosses(part) == []


def test_nonconcurrent_midplanes_do_not_define_a_regular_polygon_axis():
    inconsistent_midplanes = _polygon_from_supports((20, 19, 21, 20, 21, 19))
    part = Box(100, 80, 10) + Pos(0, 0, 5) * extrude(inconsistent_midplanes, 30)

    assert recognise_polygonal_bosses(part) == []


def test_a_blind_polygonal_recess_is_not_a_boss():
    stock = Box(100, 80, 40)
    recess = Pos(0, 0, 5) * _hex_prism(height=30)

    assert recognise_polygonal_bosses(stock - recess) == []


def test_a_standalone_polygonal_prism_is_not_an_attached_boss():
    assert recognise_polygonal_bosses(_hex_prism()) == []


def test_a_detached_polygonal_component_is_not_a_boss_on_another_solid():
    assembly = Compound(children=[Box(100, 80, 10), Pos(0, 0, 30) * _hex_prism()])

    assert recognise_polygonal_bosses(assembly) == []


def test_unproved_polygon_classes_are_not_inferred_as_bosses():
    base = Box(100, 80, 10)
    octagonal_boss = Pos(0, 0, 5) * _polygon_prism(8)

    assert recognise_polygonal_bosses(base + octagonal_boss) == []


def test_a_synthetic_hex_boss_is_directly_dimensioned_without_layout_loss():
    drawing = build_drawing(_fused_hex_boss(), repair=False)
    (feature,) = [item for item in drawing.model().features if item.kind == "polygonal_boss"]

    af = [
        (name, annotation)
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_polygonal_boss_")
    ]
    height = [
        (name, annotation)
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_bossheight_") and drawing.registry.feature_of(name) == feature
    ]

    assert [(drawing.view_of(name), annotation.label) for name, annotation in af] == [
        ("plan", "HEX 34.6 A/F")
    ]
    assert [(drawing.view_of(name), annotation.label) for name, annotation in height] == [
        ("front", "30")
    ]
    assert not [item for item in drawing.model().features if item.kind in {"boss", "pad"}]
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in {"polygonal_boss_dropped", "boss_height_missing", "annotation_overlap"}
    ]

    drawing.remove(height[0][0])
    assert [issue.code for issue in drawing.lint()].count("boss_height_missing") == 1


def test_an_unusable_flat_direction_is_refused_at_the_ir_boundary():
    part = _fused_hex_boss()
    model = build_part_model(part)
    feature = next(item for item in model.features if item.kind == "polygonal_boss")
    with pytest.raises(ValueError, match="unit vector perpendicular"):
        replace(
            feature,
            flat_directions=((0.0, 0.0, 0.0), *feature.flat_directions[1:]),
        )


def test_an_unsupported_declared_axis_is_refused_at_the_ir_boundary():
    part = _fused_hex_boss()
    model = build_part_model(part)
    feature = next(item for item in model.features if item.kind == "polygonal_boss")
    with pytest.raises(ValueError, match="axis must be"):
        replace(feature, frame=replace(feature.frame, axis="unsupported"))


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_ctc01_has_one_100_af_polygonal_boss_with_50_height():
    (boss,) = recognise_polygonal_bosses(import_step(CTC01))

    assert boss.axis == "z"
    assert boss.side_count == 6
    assert boss.center[:2] == pytest.approx((0, 0))
    assert boss.across_flats == pytest.approx(100)
    assert (boss.base, boss.top, boss.height) == pytest.approx((0, 50, 50))


@pytest.mark.slow
@pytest.mark.timeout(180)
def test_ctc01_drawing_directly_defines_the_hex_boss():
    drawing = build_drawing(CTC01, repair=False)
    (feature,) = [item for item in drawing.model().features if item.kind == "polygonal_boss"]

    af = [
        (name, annotation)
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_polygonal_boss_")
    ]
    height = [
        (name, annotation)
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_bossheight_") and drawing.registry.feature_of(name) == feature
    ]

    assert [(drawing.view_of(name), annotation.label) for name, annotation in af] == [
        ("plan", "HEX 100 A/F")
    ]
    assert [(drawing.view_of(name), annotation.label) for name, annotation in height] == [
        ("front", "50")
    ]
    assert not [item for item in drawing.model().features if item.kind in {"boss", "pad"}]
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in {"polygonal_boss_dropped", "boss_height_missing", "annotation_overlap"}
    ]


@pytest.mark.parametrize(
    ("kept", "present", "absent"),
    [
        ("polygon_across_flats.length", "m_polygonal_boss_", "m_bossheight_"),
        ("boss_height.length", "m_bossheight_", "m_polygonal_boss_"),
    ],
)
def test_an_authored_set_controls_each_polygonal_boss_measurement(kept, present, absent):
    part = _fused_hex_boss()
    model = build_part_model(part)
    feature = next(item for item in model.features if item.kind == "polygonal_boss")
    authored = replace(
        model,
        authored_dimensions=(RequestedDimension(feature=feature, role=kept),),
    )

    drawing = build_drawing(part, model=authored, repair=False)
    names = drawing.annotations()

    assert any(name.startswith(present) for name in names)
    assert not any(name.startswith(absent) for name in names)
