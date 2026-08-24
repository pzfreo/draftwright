"""Output contract for analytical machined-feature leaders (#1308)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from draftwright import build_drawing
from draftwright.annotations._common import analytical_leader_lands_clear

FIXTURES = Path(__file__).parent / "fixtures"

# Recorded from the OCC-measured path immediately before #1308.  This is intentionally
# semantic rather than SVG-byte exact: every annotation, its rendered type, its 3-decimal
# ink box, and the lint-code inventory must remain identical.
EXPECTED = {
    "grm03_thumbwheel_drive_screw.step": (
        {
            "centerline_front": ("Centerline", (31.89, 69.925, 159.39, 70.075)),
            "centerline_plan": ("Centerline", (31.89, 139.925, 159.39, 140.075)),
            "m_cm0": ("CenterMark", (201.28, 65.0, 211.28, 75.0)),
            "m_dia_x1": ("Leader", (44.39, 32.875, 55.618, 45.0)),
            "m_dia_x2": ("Leader", (56.074, 32.893, 62.876, 57.5)),
            "m_dia_x3": ("Leader", (108.49, 32.875, 114.603, 62.5)),
            "m_dia_x0": ("Leader", (24.623, 53.893, 38.14, 56.107)),
            "m_steplen0": ("Dimension", (31.49, 97.0, 44.79, 107.083)),
            "m_steplen1": ("Dimension", (39.34, 97.0, 49.44, 108.0)),
            "m_steplen2": ("Dimension", (49.34, 97.0, 64.44, 108.0)),
            "m_steplen3": ("Dimension", (64.34, 97.0, 154.44, 108.0)),
            "dim_height": ("Dimension", (158.39, 44.95, 166.39, 95.05)),
            "hc_side0": ("Leader", (210.28, 68.665, 253.305, 71.335)),
            "m_chamfer_x0": ("Leader", (12.236, 94.25, 40.14, 101.339)),
            "m_chamfer_x1": ("Leader", (153.14, 69.829, 177.598, 76.25)),
            "title_block": ("TitleBlock", (165.925, 10.925, 286.075, 27.075)),
        },
        {},
    ),
    "issue_1058_wheel_rh.step": (
        {
            "m_cm0": ("CenterMark", (73.245, 124.5, 92.745, 144.0)),
            "hc_plan0": ("Leader", (14.865, 132.903, 76.745, 135.597)),
            "m_locx0": ("Dimension", (63.945, 136.25, 83.045, 165.25)),
            "m_locy0": ("Dimension", (155.807, 97.25, 174.907, 107.25)),
            "dim_height": ("Dimension", (105.862, 56.7, 113.862, 95.3)),
            "m_env_width": ("Dimension", (63.945, 103.25, 101.912, 111.25)),
            "m_env_depth": ("Dimension", (155.807, 44.75, 193.907, 52.75)),
            "title_block": ("TitleBlock", (165.925, 10.925, 286.075, 27.075)),
            "note_iso_nts": ("Note", (155.693, 115.549, 180.026, 118.243)),
        },
        {"gear_semantics_missing": 1, "unrecognised_defining_geometry": 1},
    ),
    "nist_ctc_01_asme1_ap242.stp": (
        {
            "m_cm0": ("CenterMark", (82.275, 240.5, 89.275, 247.5)),
            "m_cm1": ("CenterMark", (146.275, 240.5, 153.275, 247.5)),
            "m_cm2": ("CenterMark", (82.275, 222.5, 89.275, 229.5)),
            "m_cm3": ("CenterMark", (146.275, 222.5, 153.275, 229.5)),
            "m_cm4": ("CenterMark", (178.275, 265.5, 187.275, 274.5)),
            "m_cm5": ("CenterMark", (48.275, 265.5, 57.275, 274.5)),
            "m_cm6": ("CenterMark", (178.275, 195.5, 187.275, 204.5)),
            "m_cm7": ("CenterMark", (48.275, 195.5, 57.275, 204.5)),
            "m_cm8": ("CenterMark", (120.775, 152.0, 126.775, 158.0)),
            "m_cm9": ("CenterMark", (108.775, 152.0, 114.775, 158.0)),
            "hc_front0": ("Leader", (122.875, 128.665, 146.85, 153.0)),
            "m_polygonal_boss_z0": ("Leader", (62.943, 175.663, 112.775, 226.34)),
            "m_slot0_width": ("Dimension", (25.775, 225.6, 46.775, 244.4)),
            "m_slot1_width": ("Dimension", (184.775, 229.95, 209.775, 240.05)),
            "m_slot0_length": ("Dimension", (48.725, 241.0, 72.825, 292.0)),
            "m_slot1_length": ("Dimension", (166.725, 242.0, 182.825, 301.5)),
            "m_slot0_pos": ("Dimension", (37.725, 241.0, 48.825, 311.0)),
            "m_locx0": ("Dimension", (37.725, 272.0, 52.825, 320.5)),
            "m_locx1": ("Dimension", (37.725, 246.0, 85.825, 330.0)),
            "m_locx2": ("Dimension", (37.725, 246.0, 149.825, 339.5)),
            "m_slot1_pos": ("Dimension", (37.725, 242.0, 166.825, 349.0)),
            "m_locx3": ("Dimension", (37.725, 272.0, 182.825, 358.5)),
            "m_locy0": ("Dimension", (250.725, 172.0, 260.825, 182.0)),
            "m_locy1": ("Dimension", (250.725, 172.0, 286.825, 191.5)),
            "m_locy2": ("Dimension", (250.725, 172.0, 304.825, 201.0)),
            "m_locy3": ("Dimension", (250.725, 172.0, 330.825, 210.5)),
            "dim_step_0": ("Dimension", (201.775, 139.95, 209.775, 160.05)),
            "m_bossheight_z0": ("Dimension", (119.775, 159.95, 219.275, 170.05)),
            "dim_loc_front_z7500": ("Dimension", (199.775, 139.95, 228.775, 155.05)),
            "dim_height": ("Dimension", (209.775, 139.95, 238.275, 170.05)),
            "m_env_depth": ("Dimension", (250.725, 128.0, 340.825, 136.0)),
            "dim_loc_front_x37000": ("Dimension", (37.725, 117.15, 111.825, 136.0)),
            "dim_loc_front_x43000": ("Dimension", (37.725, 104.3, 123.825, 136.0)),
            "m_env_width": ("Dimension", (37.725, 284.0, 197.825, 372.35)),
            "hc_plan0": ("Leader", (151.436, 188.665, 220.148, 242.131)),
            "hc_plan1": ("Leader", (186.275, 268.665, 220.199, 271.335)),
            "m_chamfer_y0": ("Leader", (89.433, 169.491, 108.266, 183.115)),
            "m_chamfer_z1": ("Leader", (192.775, 275.0, 213.032, 283.142)),
            "m_fillet_z0": ("Leader", (15.101, 184.659, 40.704, 192.929)),
            "title_block": ("TitleBlock", (432.925, 10.925, 583.075, 27.075)),
            "note_iso_nts": ("Note", (460.221, 103.057, 484.554, 105.751)),
        },
        {"pmi_present_but_ignored": 1, "step_dim_withheld": 1},
    ),
}


def test_analytical_producer_floor_matches_label_and_full_geometry_clearance():
    candidate = SimpleNamespace(
        label_box=(10.0, 10.0, 12.0, 12.0),
        ink_polygons=(((5.0, 5.0), (7.0, 5.0), (7.0, 7.0), (5.0, 7.0)),),
    )
    page = (0.0, 0.0, 100.0, 100.0)
    silhouette = (20.0, 20.0, 30.0, 30.0)
    shaft_obstacle = ((5.5, 5.5, 6.5, 6.5),)

    assert analytical_leader_lands_clear(candidate, shaft_obstacle, silhouette, page, label="R1")
    assert not analytical_leader_lands_clear(
        candidate,
        shaft_obstacle,
        silhouette,
        page,
        label="R1",
        geom_clear=True,
    )
    assert not analytical_leader_lands_clear(candidate, (), silhouette, page, label="")
    assert not analytical_leader_lands_clear(
        SimpleNamespace(label_box=None, ink_polygons=()),
        (),
        silhouette,
        page,
        label="R1",
    )


@pytest.mark.parametrize("fixture", tuple(EXPECTED))
def test_analytical_machined_leaders_preserve_the_occ_measured_drawing(fixture):
    drawing = build_drawing(FIXTURES / fixture)
    actual = {}
    for name, annotation in drawing.iter_annotations():
        box = annotation.bounding_box()
        actual[name] = (
            type(annotation).__name__,
            tuple(round(value, 3) for value in (box.min.X, box.min.Y, box.max.X, box.max.Y)),
        )

    expected_annotations, expected_lint = EXPECTED[fixture]
    assert actual == expected_annotations
    assert drawing.lint_summary()["by_code"] == expected_lint
