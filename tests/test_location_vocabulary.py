"""The location half of the dimension vocabulary is declared, not restated (#966).

`location_pocket.location` and `location_slot.length` — two spellings for one concept —
arose because the stem was written in a planner table while the suffix was chosen at each
mint site. No single place owned the name, so nothing could notice they disagreed. These
tests pin the declaration as the one source, fail-closed.
"""

import dataclasses

import pytest
from build123d import Box, Cylinder, Pos

from draftwright.model.planner import _LOCATABLE, location_datum, location_role

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


def test_the_planner_holds_membership_but_never_a_name():
    """There is no second copy of the name to drift, because there is no copy at all.

    The first cut kept `_LOCATION_ROLE`, a `dict[type, str]` built by comprehension from the
    declarations. It read as derived, but it was an import-time SNAPSHOT: renaming a
    declaration afterwards left it stale, so the mint site went on using the old name while
    the declaration said otherwise — two owners again, one layer down (Codex #1010 r2).

    `_LOCATABLE` answers only WHICH features have a position; `location_role` reads the name
    live. This asserts the planner holds no name strings, so the snapshot cannot come back.
    """
    import draftwright.model.planner as planner

    assert all(isinstance(cls, type) for cls in _LOCATABLE), "membership only, not names"

    # No module-level container may hold a location stem: that is what a snapshot looks
    # like. `LOCATION_ROLE` is exempt — it is the AUTHORING role (what a script writes),
    # a different vocabulary from the compiled stems (see #966).
    stems = {cls.LOCATION_STEM for cls in _LOCATABLE}
    for name in dir(planner):
        if name == "LOCATION_ROLE" or name.startswith("__"):
            continue
        value = getattr(planner, name)
        if isinstance(value, dict):
            assert not (stems & set(map(str, value.values()))), f"planner.{name} caches stems"
        elif isinstance(value, (set, frozenset, list, tuple)):
            assert not (stems & {v for v in value if isinstance(v, str)}), (
                f"planner.{name} caches stems"
            )


def test_every_locatable_feature_declares_its_stem():
    """Fail-closed: a feature the planner will plan a location for must declare the name
    that location is minted under. Adding a locatable feature without a declaration fails
    here rather than silently inventing a spelling at the mint site."""
    missing = [
        cls.__name__
        for cls in _LOCATABLE
        if not isinstance(getattr(cls, "LOCATION_STEM", None), str)
    ]
    assert missing == [], f"locatable features with no LOCATION_STEM declaration: {missing}"


def test_a_declared_stem_is_never_silently_unused():
    """The other direction. A feature declaring a stem that the planner never consults is a
    name nobody mints — the stale half of the vocabulary problem, and exactly what a
    hand-maintained list hides."""
    declared = {c.__name__ for c in _feature_classes() if hasattr(c, "LOCATION_STEM")}
    routed = {c.__name__ for c in _LOCATABLE}
    assert declared == routed, (
        f"declared but never routed: {sorted(declared - routed)}; "
        f"routed but not declared: {sorted(routed - declared)}"
    )


def test_stems_are_unique_so_two_features_cannot_share_a_name():
    stems = [c.LOCATION_STEM for c in _LOCATABLE]
    assert len(stems) == len(set(stems)), f"duplicate location stems: {stems}"


@pytest.mark.parametrize(
    ("kind", "feature_name", "build"),
    [
        ("slot", "SlotFeature", lambda: Box(60, 30, 10) - Box(30, 8, 20)),
        ("pocket", "PocketFeature", lambda: Box(80, 60, 20) - Pos(0, 0, 12) * Box(30, 20, 10)),
        ("hole", "HoleFeature", lambda: Box(80, 60, 10) - Pos(20, 10, 0) * Cylinder(4, 20)),
    ],
)
def test_a_real_build_mints_each_location_from_its_declaration(kind, feature_name, build):
    """Rename the declaration; the real build's ledger must follow.

    **Parametrised over every kind whose readers this work has touched**, deliberately. The
    first cut tested the slot alone and its prose claimed canonical ownership generally
    (Codex #1010 r2) — which is the recurring defect in this branch's history: verify the
    path just changed, then describe the result more broadly than was tested.

    This is the assertion that matters, because the name is a CONTRACT between the compiler
    that mints it and the renderer that reads it. When only one end derived it, renaming did
    not rename the dimension — it made the dimension VANISH, since the two ends disagreed
    about what to look for. So a passing rename proves both ends follow the declaration.
    """
    import draftwright.model as model_pkg
    from draftwright import build_drawing

    feature_cls = getattr(model_pkg, feature_name)

    def _ids(dwg):
        return {
            key["parameter_id"]
            for name, _a in dwg.iter_annotations()
            for key in dwg.measurement_keys(name)
            if key["feature"].startswith(kind)
        }

    part = build()
    original = feature_cls.LOCATION_STEM
    baseline = _ids(build_drawing(part))
    assert any(i.startswith(f"{original}.") for i in baseline), (
        f"fixture must actually draw a {kind} location, or the rename proves nothing"
    )

    try:
        feature_cls.LOCATION_STEM = "location_canary"
        mutated = _ids(build_drawing(part))
    finally:
        feature_cls.LOCATION_STEM = original

    assert any(i.startswith("location_canary.") for i in mutated), (
        f"{kind}: renaming the declaration did not rename the minted id — a reader or a "
        "mint site still owns the name"
    )
    assert not any(i.startswith(f"{original}.") for i in mutated), (
        f"{kind}: the old name survived the rename, so something holds a parallel copy"
    )


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
