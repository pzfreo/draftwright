"""Slot completeness follows semantic provenance, never presentation (#1018 Gate 2)."""

from build123d import Box, Pos

from draftwright import build_drawing


def _off_centre_slot():
    return Box(100, 70, 10) - Pos(22, -11, 0) * Box(30, 8, 20)


def _slot_grid():
    part = Box(176, 136, 20)
    for x in (-22, 22):
        for y in (-34, 0, 34):
            part -= Pos(x, y, 0) * Box(24, 8, 20)
    return part


def _slot_requirement_codes(dwg):
    return [issue.code for issue in dwg.lint() if issue.code.startswith("slot_requirement_")]


def test_removing_an_off_centre_slots_location_is_detected():
    """The recogniser centroid and IR frame differ on the transverse axis by design.

    Removing only the compiler-identified location dimension must therefore be found through
    semantic slot correspondence, not a fuzzy kind-plus-frame-origin join.
    """
    dwg = build_drawing(_off_centre_slot())
    (slot,) = [feature for feature in dwg.model().features if feature.kind == "slot"]
    assert slot.frame.origin[1] == 0.0
    assert dwg.recognition().slots[0].location[1] == -11.0

    (location_name,) = [
        name
        for name in dwg.registry.names_for_feature(slot)
        if any(
            key["parameter_id"] == "location_slot.length"
            for key in dwg.measurement_keys(name)
        )
    ]
    dwg.remove(location_name)

    assert _slot_requirement_codes(dwg) == ["slot_requirement_missing"]


def test_grid_pitch_annotations_retain_distinct_directional_provenance():
    """One placed pitch direction must not satisfy the other by set membership."""
    dwg = build_drawing(_slot_grid())
    pitch_names = sorted(name for name in dwg.annotations() if "slotpat_pitch" in name)
    assert len(pitch_names) == 2

    pitch_ids = {
        dwg.measurement_keys(name)[0]["parameter_id"]
        for name in pitch_names
        if dwg.measurement_keys(name)
    }
    assert pitch_ids == {"grid_pitch.length.row", "grid_pitch.length.col"}

    dwg.remove(pitch_names[0])
    assert _slot_requirement_codes(dwg) == ["slot_requirement_missing"]
