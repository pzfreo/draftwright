"""#1073: derived patterns preserve physical-body ownership."""

from dataclasses import replace

from b123d_recognisers import (
    recognise_pocket_patterns,
    recognise_pockets,
    recognise_slot_patterns,
    recognise_slots,
)
from build123d import Box, Compound, Pos

from draftwright.model.detect import build_part_model


def _separate_bodies(feature):
    return Compound(children=[Pos(0, y, 0) * feature for y in (0, 60, 120)])


def _slotted_body():
    return Box(60, 30, 10) - Box(30, 8, 20)


def _pocketed_body():
    return Box(60, 30, 20) - Pos(0, 0, 7) * Box(30, 8, 6)


def test_separate_body_slots_do_not_form_one_pattern():
    slots = recognise_slots(_separate_bodies(_slotted_body()))

    assert len(slots) == 3
    assert len({slot.body_key for slot in slots}) == 3
    assert recognise_slot_patterns(slots) == []

    # Targeted mutation: discarding the owning-body evidence recreates the false array.
    assert len(recognise_slot_patterns([replace(slot, body_key=()) for slot in slots])) == 1


def test_separate_body_pockets_do_not_form_one_pattern():
    pockets = recognise_pockets(_separate_bodies(_pocketed_body()))

    assert len(pockets) == 3
    assert len({pocket.body_key for pocket in pockets}) == 3
    assert recognise_pocket_patterns(pockets) == []

    # Targeted mutation: discarding the owning-body evidence recreates the false array.
    assert len(recognise_pocket_patterns([replace(pk, body_key=()) for pk in pockets])) == 1


def test_real_patterns_within_one_body_still_group():
    slotted = Box(60, 150, 10)
    pocketed = Box(60, 150, 20)
    for y in (-30, 0, 30):
        slotted -= Pos(0, y, 0) * Box(30, 8, 20)
        pocketed -= Pos(0, y, 7) * Box(30, 8, 6)

    slots = recognise_slots(slotted)
    pockets = recognise_pockets(pocketed)
    assert len({slot.body_key for slot in slots}) == 1
    assert len({pocket.body_key for pocket in pockets}) == 1
    assert len(recognise_slot_patterns(slots)) == 1
    assert len(recognise_pocket_patterns(pockets)) == 1


def test_aggregate_model_keeps_cross_body_members_independent():
    slotted = build_part_model(_separate_bodies(_slotted_body()))
    pocketed = build_part_model(_separate_bodies(_pocketed_body()))

    assert [feature.kind for feature in slotted.features].count("slot") == 3
    assert [feature.kind for feature in slotted.features].count("slot_pattern") == 0
    assert [feature.kind for feature in pocketed.features].count("pocket") == 3
    assert [feature.kind for feature in pocketed.features].count("pocket_pattern") == 0


def test_compound_traversal_order_does_not_change_body_correspondence():
    body = _slotted_body()
    forward = Compound(children=[Pos(0, y, 0) * body for y in (0, 60, 120)])
    reverse = Compound(children=[Pos(0, y, 0) * body for y in (120, 60, 0)])

    assert recognise_slots(forward) == recognise_slots(reverse)
    assert recognise_slot_patterns(recognise_slots(forward)) == []
    assert recognise_slot_patterns(recognise_slots(reverse)) == []


def test_ambiguous_body_signature_fails_closed(monkeypatch):
    from b123d_recognisers import slots as slots_module

    monkeypatch.setattr(slots_module, "_body_signature", lambda _solid: (1.0,))
    slots = recognise_slots(_separate_bodies(_slotted_body()))
    pockets = recognise_pockets(_separate_bodies(_pocketed_body()))

    assert len(slots) == 3 and all(slot.body_key is None for slot in slots)
    assert len(pockets) == 3 and all(pocket.body_key is None for pocket in pockets)
    assert recognise_slot_patterns(slots) == []
    assert recognise_pocket_patterns(pockets) == []
