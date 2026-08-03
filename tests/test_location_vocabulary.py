"""The location half of the dimension vocabulary is declared, not restated (#966).

`location_pocket.location` and `location_slot.length` — two spellings for one concept —
arose because the stem was written in a planner table while the suffix was chosen at each
mint site. No single place owned the name, so nothing could notice they disagreed. These
tests pin the declaration as the one source, fail-closed.
"""

import dataclasses

import pytest

from draftwright.model.planner import _LOCATION_ROLE, location_datum, location_role

pytestmark = pytest.mark.smoke


def _feature_classes():
    """Every IR feature type exported by `model`.

    NOT by walking `Feature.__subclasses__()`: `Feature` is a runtime-checkable Protocol, so
    the feature dataclasses satisfy it structurally and inherit nothing. That walk returned
    an EMPTY list, which silently made the bidirectional check below vacuous — a discovery
    mechanism that finds nothing proves nothing.
    """
    import draftwright.model as model

    return [
        obj
        for name in dir(model)
        if isinstance(obj := getattr(model, name), type) and dataclasses.is_dataclass(obj)
    ]


def test_the_planner_table_is_a_projection_not_a_second_source():
    """Every entry must equal the feature's own declaration. A table that could drift from
    the declaration would recreate the exact defect this issue exists to remove."""
    for cls, stem in _LOCATION_ROLE.items():
        assert stem == cls.LOCATION_STEM, f"{cls.__name__}: table says {stem!r}"


def test_every_locatable_feature_declares_its_stem():
    """Fail-closed: a feature the planner will plan a location for must declare the name
    that location is minted under. Adding a locatable feature without a declaration fails
    here rather than silently inventing a spelling at the mint site."""
    missing = [
        cls.__name__
        for cls in _LOCATION_ROLE
        if not isinstance(getattr(cls, "LOCATION_STEM", None), str)
    ]
    assert missing == [], f"locatable features with no LOCATION_STEM declaration: {missing}"


def test_a_declared_stem_is_never_silently_unused():
    """The other direction. A feature declaring a stem that the planner never consults is a
    name nobody mints — the stale half of the vocabulary problem, and exactly what a
    hand-maintained list hides."""
    declared = {c.__name__ for c in _feature_classes() if hasattr(c, "LOCATION_STEM")}
    routed = {c.__name__ for c in _LOCATION_ROLE}
    assert declared == routed, (
        f"declared but never routed: {sorted(declared - routed)}; "
        f"routed but not declared: {sorted(routed - declared)}"
    )


def test_stems_are_unique_so_two_features_cannot_share_a_name():
    stems = [c.LOCATION_STEM for c in _LOCATION_ROLE]
    assert len(stems) == len(set(stems)), f"duplicate location stems: {stems}"


def test_a_real_build_mints_the_slot_location_from_the_declaration():
    """The assertion the first cut was missing (Codex #1010 r1).

    Every other test here compares the planner table with the declaration — table against
    table. None of them touched a compiled plan, so **all of them passed while the
    declaration was decorative**: the mint site in `compiled.py` still held its own
    hardcoded `"location_slot.length"`, and renaming the declaration changed no output at
    all. A vocabulary nothing consumes is not a vocabulary.

    So this mutates the declaration and asserts a REAL build's ledger follows it.
    """
    from build123d import Box

    from draftwright import build_drawing
    from draftwright.model import SlotFeature

    def _slot_ids(dwg):
        return {
            key["parameter_id"]
            for name, _a in dwg.iter_annotations()
            for key in dwg.measurement_keys(name)
            if key["feature"].startswith("slot")
        }

    part = Box(60, 30, 10) - Box(30, 8, 20)
    assert "location_slot.length" in _slot_ids(build_drawing(part))

    original = SlotFeature.LOCATION_STEM
    try:
        SlotFeature.LOCATION_STEM = "location_canary"
        mutated = _slot_ids(build_drawing(part))
    finally:
        SlotFeature.LOCATION_STEM = original

    assert "location_canary.length" in mutated, (
        "the compiled ledger ignored the declaration — the mint site still owns the name"
    )
    assert "location_slot.length" not in mutated


def test_location_role_answers_for_an_eligible_feature_and_none_for_an_ineligible_one():
    """Both directions, unlike the first cut — which was named "...answers none where no
    location is planned" and then asserted the opposite on a feature that IS locatable,
    ending with `assert model is not None`, which cannot fail after ordinary construction."""
    from draftwright.model import Frame, HoleFeature, PatternFeature

    side = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 6.0, depth=None, through=True)
    assert location_datum(side) == "bbox"  # eligible, from the bounding box
    assert location_role(side) == "location"

    member = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 6.0, depth=None, through=True)
    off_axis = PatternFeature(Frame((0.0, 0.0, 0.0), "x"), "linear", 3, member)
    assert location_datum(off_axis) is None, "an off-axis pattern has never been drawn"
    assert location_role(off_axis) is None, "so the vocabulary must not accept it"
