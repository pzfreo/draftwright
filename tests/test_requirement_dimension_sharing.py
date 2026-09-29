"""Coincident requirements share one dimension with every owner preserved."""

import pytest

from draftwright import build_drawing


@pytest.fixture(scope="module")
def whistle_frame_drawing():
    dwg = build_drawing("tests/fixtures/issue_1595_whistle_key_frame.step")
    kinds = [feature.kind for feature in dwg.model().features]
    assert kinds.count("slot") == 2
    assert "circular_channel" in kinds
    return dwg


def test_shared_slot_width_keeps_both_requirements_issue_1599(whistle_frame_drawing):
    dwg = whistle_frame_drawing
    slots = [feature for feature in dwg.model().features if feature.kind == "slot"]
    assert len(slots) == 2
    assert slots[0].width == pytest.approx(slots[1].width, abs=0.001)
    assert round(slots[0].width, 2) == 17.35

    widths = [
        name for name in dwg.annotations() if name.startswith("m_slot") and name.endswith("_width")
    ]
    assert len(widths) == 1
    name = widths[0]
    assert dwg.get_annotation(name).label == "2× 17.4"
    assert {mid.feature for mid in dwg.registry.measurement_of(name)} == set(slots)
    assert set(dwg.registry.features_of(name)) == set(slots)
    assert all(name in dwg.annotations_of(slot) for slot in slots)
    assert not any(issue.code == "label_vs_measured" for issue in dwg.lint())


def test_coincident_hole_and_seat_locations_share_dimension_issue_1599(whistle_frame_drawing):
    dwg = whistle_frame_drawing
    for view, label in (("side", "3.5"), ("side", "27.9"), ("side", "68.1"), ("front", "11.1")):
        matching = [
            name
            for name in dwg.annotations()
            if dwg.view_of(name) == view
            and dwg.get_annotation(name).__class__.__name__ == "Dimension"
            and dwg.get_annotation(name).label == label
        ]
        assert len(matching) == 1, (view, label, matching)
        assert any(
            mid.feature.kind == "circular_channel"
            for mid in dwg.registry.measurement_of(matching[0])
        )
