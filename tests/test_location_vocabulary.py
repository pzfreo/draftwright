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


def _locatable_instances():
    """One instance per locatable kind, with geometry the compiler actually mints for.

    Written out rather than generated from field introspection: a constructor that guesses
    values fails silently on whichever kind it guesses wrong, and a silent failure here is
    indistinguishable from the defect under test. `members` on the pattern and `datum_xy` on
    the model are both load-bearing for exactly that reason — without either, every case
    below mints nothing and the whole parametrisation passes while proving nothing.
    """
    from draftwright.model import (
        Frame,
        HoleFeature,
        PadFeature,
        PatternFeature,
        PocketFeature,
        PocketPatternFeature,
        SlotFeature,
    )

    z = Frame((10.0, 10.0, 0.0), "z")
    hole = HoleFeature(z, 6.0, depth=None, through=True)
    pocket = PocketFeature(z, "y", "x", 8.0, 20.0, 4.0, 10.0, 4.0, 24.0)
    members = ((5.0, 10.0, 0.0), (15.0, 10.0, 0.0), (25.0, 10.0, 0.0))
    return [
        pytest.param(hole, id="hole"),
        pytest.param(PatternFeature(z, "linear", 3, hole, members, pitch=10.0), id="pattern"),
        pytest.param(pocket, id="pocket"),
        pytest.param(
            PocketPatternFeature(z, "linear", 3, pocket, members, pitch=10.0), id="pocket_pattern"
        ),
        pytest.param(PadFeature(z, "y", "x", 8.0, 20.0, 10.0, 4.0, 24.0, 0.0, 5.0), id="pad"),
        pytest.param(SlotFeature(z, "y", "x", 8.0, 20.0, 10.0, 4.0, 24.0), id="slot"),
    ]


@pytest.mark.parametrize("feature", _locatable_instances())
def test_a_subclass_is_not_silently_locatable(feature):
    """Membership is by EXACT type, as the `dict[type, str]` this replaced was.

    An `isinstance` check reads as the friendlier spelling and is wrong here: a subclass
    INHERITS `LOCATION_STEM`, so accepting it would mint its position under its parent's
    stem — two features minting one name, which is the collision the declaration exists to
    prevent. It is also a behaviour change from main, and the review that caught it
    (Codex #1010 r3) caught it as an *undocumented* one. Declaring its own stem and joining
    `_LOCATABLE` is the way in.

    Parametrised over **every** locatable kind, asserting on the COMPILED PLAN rather than
    on `location_role` alone, and asserting the PARENT mints first — because the first cut
    did none of the three. It tested a `HoleFeature` subclass and inferred the policy
    generally, while `_compile_slot_positions` reached its mint site through a bare
    `isinstance` and never asked: a slot subclass the planner refused minted
    `location_slot.length` anyway (Codex #1010 r4). And the same test without the
    parent-mints assertion was **vacuous for five of six kinds** — the model it built had no
    `datum_xy`, so nothing was minted for anyone and every case passed.
    """
    from build123d import Box

    from draftwright.model import Datum, PartModel
    from draftwright.model.compiled import compile_dimensions

    sub_cls = dataclasses.dataclass(frozen=True)(
        type(f"Custom{type(feature).__name__}", (type(feature),), {})
    )
    sub = sub_cls(**{f.name: getattr(feature, f.name) for f in dataclasses.fields(feature)})
    assert sub_cls.LOCATION_STEM == type(feature).LOCATION_STEM, "inherits its parent's name"
    assert location_datum(sub) is None, "so it must not be planned under that name"
    assert location_role(sub) is None

    bb = Box(60, 60, 20).bounding_box()
    datums = [Datum(id="datum_xy", kind="point", at=(bb.min.X, bb.min.Y, bb.min.Z))]

    def _minted(f):
        return [
            loc.id.parameter
            for loc in compile_dimensions(PartModel(bb, None, [f], datums)).locations
        ]

    assert _minted(feature), "the parent must mint here, or the subclass minting nothing is free"
    assert _minted(sub) == [], (
        f"a {sub_cls.__name__} the planner refused still minted {_minted(sub)} — a compiler "
        "reached its mint site without asking the eligibility question"
    )


@pytest.mark.parametrize(
    ("kind", "feature_name", "build"),
    [
        pytest.param("slot", "SlotFeature", lambda: Box(60, 30, 10) - Box(30, 8, 20), id="slot"),
        pytest.param(
            "pocket",
            "PocketFeature",
            lambda: Box(80, 60, 20) - Pos(0, 0, 12) * Box(30, 20, 10),
            id="pocket-z",
        ),
        # A pocket in a VERTICAL face, whose position compiles to two discriminated entries
        # and is read back by a second branch in `from_model`. Added because mutating that
        # branch to a literal left the Z-normal case above green — the reader it guards was
        # never reached, and a fixture that does not reach a path says nothing about it.
        pytest.param(
            "pocket",
            "PocketFeature",
            lambda: Box(80, 60, 40) - Pos(38, 0, 5) * Box(10, 20, 15),
            id="pocket-x",
        ),
        pytest.param(
            "hole",
            "HoleFeature",
            lambda: Box(80, 60, 10) - Pos(20, 10, 0) * Cylinder(4, 20),
            id="hole-z",
        ),
        pytest.param(
            "pattern",
            "PatternFeature",
            lambda: (
                Box(80, 80, 10)
                - Pos(25, 25, 0) * Cylinder(3, 20)
                - Pos(-25, 25, 0) * Cylinder(3, 20)
                - Pos(25, -25, 0) * Cylinder(3, 20)
                - Pos(-25, -25, 0) * Cylinder(3, 20)
            ),
            id="pattern-z",
        ),
    ],
)
def test_a_real_build_mints_each_location_from_its_declaration(kind, feature_name, build):
    """Rename the declaration; the real build's ledger must follow.

    This is the assertion that matters, because the name is a CONTRACT between the compiler
    that mints it and the renderer that reads it. When only one end derived it, renaming did
    not rename the dimension — it made the dimension VANISH, since the two ends disagreed
    about what to look for.

    **Scope, stated as narrowly as the evidence.** What passing proves: for a *slot*, a
    *pocket in each of its two compiled shapes*, a *Z-normal hole* and a *Z-normal pattern*,
    on an automatic `build_drawing`, both the mint site and the reader derive the stem from
    the declaration. It does **not** cover `PadFeature` or `PocketPatternFeature` (no fixture
    here draws either's position), nor the two paths outside this parametrisation — the
    side-drilled bore's own stem and the `locate()` edit verb, each canaried separately
    below. Every case here was added after mutating the reader it guards and watching this
    test go red; the two that were assumed instead (`pocket-x`, `locate()`) had both been
    green against a broken reader. The recurring defect in this branch is to verify the one
    path just changed and then describe the result more broadly than was tested.
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


def test_the_side_drilled_path_mints_from_its_own_declaration():
    """The off-axis bore, which the canary above does not reach.

    Its two positions are a different measurement from the Z-normal ladder — bounding-box
    datum, one entry per measured axis — so they carry their own declaration,
    `LOCATION_OFF_AXIS_STEM`, rather than a suffix rule over `LOCATION_STEM`. Before this
    the name was a literal in the compiler and the same literal in `holes.py`'s filter: the
    branch's own two-owners defect, still open at r3.

    Asserted at TWO levels because they fail differently, and asserting one would miss the
    other: the compiled ids show the *mint* followed the rename; the drawn `dim_loc_*`
    annotations surviving it show the *reader* did. A reader still holding the literal
    matches nothing, so the dims vanish while the compiled ids look perfect.

    Not via `measurement_keys` like the canary above — the off-axis dims record no
    measurement identity yet (#1005), so that ledger is empty here and an assertion over it
    would pass vacuously.
    """
    from build123d import Rot

    from draftwright import build_drawing
    from draftwright.builder import detect_part_model
    from draftwright.model import HoleFeature
    from draftwright.model.compiled import compile_dimensions

    part = Box(12, 40, 30) - Pos(0, 8, 6) * Rot(0, 90, 0) * Cylinder(3, 12)
    model = detect_part_model(part)
    original = HoleFeature.LOCATION_OFF_AXIS_STEM

    def _params():
        return {loc.id.parameter for loc in compile_dimensions(model).locations}

    def _drawn():
        return {n for n, _a in build_drawing(part).iter_annotations() if n.startswith("dim_loc_")}

    baseline_params, baseline_drawn = _params(), _drawn()
    assert any(p.startswith(f"{original}.") for p in baseline_params), (
        "fixture must reach the off-axis compiler, or the rename proves nothing"
    )
    assert baseline_drawn, "fixture must actually DRAW the off-axis positions"

    try:
        HoleFeature.LOCATION_OFF_AXIS_STEM = "location_canary"
        mutated_params, mutated_drawn = _params(), _drawn()
    finally:
        HoleFeature.LOCATION_OFF_AXIS_STEM = original

    assert {p for p in mutated_params if p.startswith("location_canary.")} == {
        p.replace(original, "location_canary") for p in baseline_params
    }, "the off-axis mint site still owns the name"
    assert mutated_drawn == baseline_drawn, (
        "renaming the declaration dropped the side-hole position dims — the renderer holds "
        "a literal copy of the role and now matches nothing"
    )


def test_the_on_axis_bore_skip_reads_the_declaration_on_both_surfaces():
    """The two readers that compare the role in order to NOT draw something.

    A ROTATIONAL part with an on-axis bore, deliberately: that is the one branch where
    `render_locations` (the auto pass) and `Drawing.locate()` (the edit verb) consult the
    role at all — each skips the position dim because the centreline already locates the
    bore. On a plate neither comparison is reached, so a plate fixture says nothing about
    these readers however plausible it reads. This test's first cut used one and a stale
    literal sailed straight through it.

    So the observable is the SKIP, asserted on both surfaces because they hold separate
    copies of the comparison. With a stale literal the reader stops recognising the renamed
    role, the skip does not fire, and a redundant position dim appears on a part whose bore
    is located by its centreline.

    The precondition is that the COMPILER approved a position for this bore — otherwise both
    readers receive nothing and drawing nothing is free. That is the meaningful check here,
    and a stronger one than asking the analysis whether the part is rotational.
    """
    from draftwright import build_drawing
    from draftwright.model import HoleFeature
    from draftwright.model.compiled import compile_dimensions

    part = Cylinder(20, 40) - Cylinder(6, 60)
    original = HoleFeature.LOCATION_STEM
    try:
        HoleFeature.LOCATION_STEM = "location_canary"
        auto = build_drawing(part)
        auto_ids = {
            key["parameter_id"]
            for n, _a in auto.iter_annotations()
            for key in auto.measurement_keys(n)
            if key["feature"].startswith("hole")
        }
        dwg = build_drawing(part, auto_dims=False)
        model = dwg.model()
        hole = next(f for f in model.features if isinstance(f, HoleFeature))
        approved = [loc.id.parameter for loc in compile_dimensions(model).locations]
        names = dwg.locate(hole)
    finally:
        HoleFeature.LOCATION_STEM = original

    assert any(p.startswith("location_canary.") for p in approved), (
        f"the compiler approved {approved} — with no position to skip, neither reader is "
        "reached and this test proves nothing"
    )

    assert auto_ids == set(), (
        f"the auto pass drew {sorted(auto_ids)} for an on-axis bore on a rotational part — "
        "`render_locations` no longer recognises the renamed role"
    )
    assert names == [], (
        f"locate() placed {names} for the same bore — the edit surface holds a literal copy "
        "of the role"
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
