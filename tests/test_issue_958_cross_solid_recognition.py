"""#958: recognition evidence must belong to one physical solid."""

from b123d_recognisers import (
    RaisedPad,
    recognise_channels,
    recognise_pockets,
    recognise_rectangular_pads,
    recognise_slots,
)
from build123d import Box, Compound, Pos

from draftwright import build_drawing


def _detached_pad_compound():
    return Compound(
        children=[
            Box(80, 60, 10),
            Pos(0, 0, 10) * Box(30, 20, 4),
        ]
    )


def _three_body_wall_pair():
    return Compound(
        children=[
            Box(80, 60, 10),
            Pos(0, 0, 10) * Box(30, 20, 4),
            Pos(0, -10.5, 6.5) * Box(30, 1, 3),
        ]
    )


def _three_body_full_span_wall_pair():
    return Compound(
        children=[
            Box(30, 60, 10),
            Pos(0, 0, 10) * Box(30, 20, 4),
            Pos(0, -10.5, 6.5) * Box(30, 1, 3),
        ]
    )


def test_detached_body_does_not_create_slot_evidence():
    part = _detached_pad_compound()
    assert len(part.solids()) == 2
    assert all(recognise_slots(solid) == [] for solid in part.solids())
    assert recognise_slots(part) == []


def test_detached_body_does_not_create_pad_evidence():
    part = _detached_pad_compound()
    assert len(part.solids()) == 2
    assert all(recognise_rectangular_pads(solid) == [] for solid in part.solids())
    assert recognise_rectangular_pads(part) == []


def test_detached_body_does_not_emit_plausible_but_false_dimensions():
    drawing = build_drawing(_detached_pad_compound())

    assert not [feature for feature in drawing.model().features if feature.kind in {"slot", "pad"}]
    assert not [name for name in drawing.annotations() if name.startswith(("m_slot", "m_pad"))]


def test_attached_pad_remains_recognised_once():
    part = Box(80, 60, 10) + Pos(0, 0, 7) * Box(30, 20, 4)
    assert len(part.solids()) == 1
    assert recognise_slots(part) == []
    assert recognise_rectangular_pads(part) == [RaisedPad(-15, 15, -10, 10, 5, 9)]

    drawing = build_drawing(part)
    assert [feature.kind for feature in drawing.model().features].count("pad") == 1
    assert {
        name: drawing.get_annotation(name).label
        for name in drawing.annotations()
        if name.startswith("m_pad")
    } == {"m_pad0_width": "20", "m_pad0_length": "30"}
    assert not [name for name in drawing.annotations() if name.startswith("m_slot")]
    assert drawing.lint() == []


def test_faces_from_three_bodies_do_not_form_a_pocket():
    part = _three_body_wall_pair()
    assert len(part.solids()) == 3
    assert all(recognise_pockets(solid) == [] for solid in part.solids())
    assert recognise_pockets(part) == []


def test_faces_from_three_bodies_do_not_form_a_channel():
    part = _three_body_full_span_wall_pair()
    assert len(part.solids()) == 3
    assert all(recognise_channels(solid) == [] for solid in part.solids())
    assert recognise_channels(part) == []


def test_real_slots_on_separate_bodies_are_each_preserved():
    slotted = Box(60, 30, 10) - Box(30, 8, 20)
    part = Compound(children=[slotted, Pos(0, 60, 0) * slotted])

    slots = recognise_slots(part)
    assert len(slots) == 2
    assert [(slot.width, slot.length, slot.w_center) for slot in slots] == [
        (8.0, 30.0, 0.0),
        (8.0, 30.0, 60.0),
    ]


def test_real_pads_on_separate_bodies_are_each_preserved():
    padded = Box(80, 60, 10) + Pos(0, 0, 7) * Box(30, 20, 4)
    part = Compound(children=[padded, Pos(120, 0, 0) * padded])

    assert all(len(recognise_rectangular_pads(solid)) == 1 for solid in part.solids())
    assert len(recognise_rectangular_pads(part)) == 2


def test_real_pockets_on_separate_bodies_are_each_preserved():
    pocketed = Box(80, 60, 20) - Pos(0, 0, 6) * Box(30, 20, 8)
    part = Compound(children=[pocketed, Pos(120, 0, 0) * pocketed])

    assert all(len(recognise_pockets(solid)) == 1 for solid in part.solids())
    assert len(recognise_pockets(part)) == 2


def test_real_channels_on_separate_bodies_are_each_preserved():
    channelled = (
        Box(50, 50, 12)
        + Pos(0, -18.75, 15) * Box(50, 12.5, 18)
        + Pos(0, 18.75, 15) * Box(50, 12.5, 18)
    )
    part = Compound(children=[channelled, Pos(0, 100, 0) * channelled])

    assert all(len(recognise_channels(solid)) == 1 for solid in part.solids())
    assert len(recognise_channels(part)) == 2


def test_channel_order_does_not_depend_on_compound_child_order():
    channelled = (
        Box(50, 50, 12)
        + Pos(0, -18.75, 15) * Box(50, 12.5, 18)
        + Pos(0, 18.75, 15) * Box(50, 12.5, 18)
    )
    upper = Pos(0, 0, 60) * channelled

    lower_first = recognise_channels(Compound(children=[channelled, upper]))
    upper_first = recognise_channels(Compound(children=[upper, channelled]))
    assert lower_first == upper_first
    assert [(channel.d_lo, channel.d_hi) for channel in lower_first] == [
        (6.0, 24.0),
        (66.0, 84.0),
    ]
