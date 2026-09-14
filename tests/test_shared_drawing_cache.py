"""#1637: the shared built-drawing driver hands out one build, read-only, and proves it.

The suite's cost lever is that hundreds of tests build the same plain block to critique a
pass that has nothing to do with the block. `conftest.shared_drawing` builds each
(recipe, options) pair once per worker session. That is only safe if a cache hit really is
free and a mutating borrower really is caught, so both are asserted here rather than
assumed — and the first of them is also the ADR 3 evidence: a hit runs no recognition,
because it runs no builder code at all.
"""

from __future__ import annotations

import pytest
from _parts import PART_RECIPES, part
from conftest import recognition_consumer_calls


def test_every_recipe_name_states_the_solid_it_builds():
    """The name is the key AND the description, so a wrong pairing must fail here."""
    sizes = {}
    for name in PART_RECIPES:
        size = part(name).bounding_box().size
        sizes[name] = (size.X, size.Y, size.Z)
        assert name == "box_{:g}x{:g}x{:g}".format(*sizes[name])
    assert len(set(sizes.values())) == len(sizes), sizes


def test_an_unknown_recipe_names_the_known_ones():
    with pytest.raises(KeyError, match="box_60x40x20"):
        part("box_1x1x1")


def test_a_cache_hit_returns_the_same_object_and_recognises_nothing(shared_drawing):
    """ADR 3: sharing a build cannot add a recognition run, because a hit runs no code."""
    # Precondition: the counter this test relies on must be able to see a provider call
    # at all. A miss builds, so it must register; otherwise an empty `counts` below would
    # prove nothing about the hit.
    # `box_10x10x10` is requested by no other test, so this test is always the first
    # requester of the key and the miss below is a real miss whatever the run order.
    with recognition_consumer_calls() as on_miss:
        first = shared_drawing("box_10x10x10", auto_dims=False)
    assert on_miss, "the cache miss recorded no provider call — the counter sees nothing"

    with recognition_consumer_calls() as counts:
        second = shared_drawing("box_10x10x10", auto_dims=False)
    assert second is first
    assert counts == {}, f"a cache hit reached the provider: {counts}"


def test_distinct_build_options_are_distinct_cache_entries(shared_drawing):
    plain = shared_drawing("box_20x20x20", auto_dims=False)
    a3 = shared_drawing("box_20x20x20", auto_dims=False, page="A3")
    assert plain is not a3
    assert shared_drawing("box_20x20x20", auto_dims=False, page="A3") is a3


def test_a_mutated_shared_drawing_is_refused_at_the_next_handout(shared_drawing):
    drawing = shared_drawing("box_40x40x10", auto_dims=False)
    drawing.items.append(drawing.get_annotation("title_block"))
    try:
        with pytest.raises(AssertionError, match="was mutated after it was built"):
            shared_drawing("box_40x40x10", auto_dims=False)
    finally:
        drawing.items.pop()
    # Restoring membership restores the entry: the check is on the fingerprint, not a flag.
    assert shared_drawing("box_40x40x10", auto_dims=False) is drawing


def test_the_mutating_fixture_hands_out_a_private_build(
    shared_drawing, unshared_drawing_for_mutation
):
    shared = shared_drawing("box_40x40x10", auto_dims=False)
    mine = unshared_drawing_for_mutation("box_40x40x10", auto_dims=False)
    assert mine is not shared
    mine.items.append(mine.get_annotation("title_block"))
    assert shared_drawing("box_40x40x10", auto_dims=False) is shared
