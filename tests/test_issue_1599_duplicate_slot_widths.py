"""The frame's coincident size and location statements share visible dimensions."""

import pytest

from draftwright import build_drawing


def test_the_frame_shares_its_identical_slot_width_without_losing_either_requirement():
    dwg = build_drawing("tests/fixtures/issue_1595_whistle_key_frame.step")
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


def test_the_frame_shares_coincident_hole_and_seat_locations():
    dwg = build_drawing("tests/fixtures/issue_1595_whistle_key_frame.step")
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
