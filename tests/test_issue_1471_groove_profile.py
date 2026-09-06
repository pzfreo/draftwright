"""Published body membership survives groove lowering and generated declarations."""

from dataclasses import replace

import pytest
from build123d import Compound, Cylinder, Pos
from quiddity import build_raw_recognition_result

from draftwright import Sheet, build_drawing
from draftwright.model.detect import build_part_model
from draftwright.recognition_frame import (
    AmbiguousTurnedOwnershipError,
    profiles_owning_axial_band,
    require_unambiguous_groove_owner,
)
from draftwright.sheet_emit import emit_sheet_script


def _nested_shafts():
    outer = Cylinder(15, 20) - Cylinder(10, 20) - (Cylinder(15, 2) - Cylinder(13, 2))
    inner = Pos(0, 0, -5) * Cylinder(5, 10) + Pos(0, 0, 5) * Cylinder(4, 10)
    return Compound(children=[outer, inner])


@pytest.fixture(scope="module")
def source():
    result = build_raw_recognition_result(_nested_shafts(), rotational=True)
    assert len(result.turned_profiles) == 2
    assert len(result.grooves) == 1
    assert result.grooves[0].profile is not None
    return result


def test_published_profile_resolves_two_geometric_groove_candidates(source):
    groove = source.grooves[0]
    profiles = source.turned_profiles
    candidates = profiles_owning_axial_band(profiles, axis="z", centre=(0, 0, 0), width=2)
    assert len(candidates) == 2
    (owner,) = require_unambiguous_groove_owner(groove, profiles)
    assert owner is next(p for p in profiles if p.profile == groove.profile)
    assert [step.diameter for step in owner.steps] == [30, 26, 30]
    with pytest.raises(AmbiguousTurnedOwnershipError, match="multiple body-local"):
        require_unambiguous_groove_owner(replace(groove, profile=None), source.turned_profiles)


@pytest.mark.parametrize("change", [{"at": (0, 0, 50)}, {"axis": "x"}])
def test_profile_identity_cannot_override_contradictory_geometry(source, change):
    with pytest.raises(AmbiguousTurnedOwnershipError, match="membership does not match"):
        require_unambiguous_groove_owner(
            replace(source.grooves[0], **change), source.turned_profiles
        )


@pytest.mark.parametrize(
    "change",
    [
        {"axis": True},
        {"axis_origin": (False, 0, 0)},
        {"axis_origin": [0, 0, 0]},
        {"axis_origin": (0, 0, 1)},
        {"body_bounds": (-15, 15, -15, 15, -10, float("inf"))},
        {"body_bounds": (-15, 15, -15, 15, 0, 0)},
        {"body_key": (float("nan"),) * 8},
        {"body_key": [0] * 8},
    ],
)
def test_malformed_profile_keys_cannot_establish_groove_membership(source, change):
    groove = source.grooves[0]
    malformed = replace(groove.profile, **change)
    with pytest.raises((TypeError, ValueError)):
        require_unambiguous_groove_owner(
            replace(groove, profile=malformed), source.turned_profiles
        )


def test_duplicate_profile_membership_is_not_resolved_by_inventory_order(source):
    groove = source.grooves[0]
    owner = next(p for p in source.turned_profiles if p.profile == groove.profile)
    with pytest.raises(AmbiguousTurnedOwnershipError, match="multiple body-local"):
        require_unambiguous_groove_owner(groove, (owner, owner))


def test_generated_nested_groove_preserves_body_group_and_independent_step_ledger():
    part = _nested_shafts()
    model = build_part_model(part)
    (groove,) = [f for f in model.features if f.kind == "groove"]
    assert groove.profile is not None
    assert groove.width == 2 and groove.diameter == 26
    steps = [f for f in model.features if f.kind == "step"]
    assert sorted((f.length, f.diameter) for f in steps) == [(9, 30), (9, 30), (10, 8), (10, 10)]
    source = emit_sheet_script(
        model, "part", "nested-groove", title="Nested groove", number="1471"
    )
    namespace = {"part": part}
    exec(
        compile(source[: source.index("drawing = sheet.build()")], "<nested-groove>", "exec"),
        namespace,
    )
    rebuilt = namespace["sheet"].model()
    (replayed_groove,) = [f for f in rebuilt.features if f.kind == "groove"]
    assert replayed_groove.profile is None
    assert replayed_groove.profile_group is not None
    owning_steps = [
        f
        for f in rebuilt.features
        if f.kind == "step" and f.profile_group == replayed_groove.profile_group
    ]
    assert len(owning_steps) == 2
    assert {f.diameter for f in owning_steps} == {30}
    assert "TurnedProfileKey" not in source
    automatic = build_drawing(part)
    replay = namespace["sheet"].build()
    for drawing in (automatic, replay):
        completeness = drawing.lint_summary()["quality"]["completeness"]
        assert completeness["by_family"]["turned_steps"] == 8
        assert completeness["by_family"]["grooves"] == 2
        assert len([f for f in drawing.model().features if f.kind == "step"]) == 4


def test_omitting_every_step_keeps_a_grouped_grooves_own_declaration():
    sheet = Sheet(_nested_shafts())
    groove = sheet.groove(axis="z", at=(0, 0, 0), width=2, diameter=26, profile_group="outer")
    sheet.dimension(groove, "groove.length")
    sheet.dimension(groove, "groove.diameter")
    drawing = sheet.build()
    assert [f.kind for f in drawing.model().features] == ["groove", "rotational"]
    assert any(name.startswith("m_groove") for name in drawing.annotations())


@pytest.mark.parametrize("group", ["", "  ", 1, False])
def test_declared_profile_group_must_be_a_nonempty_name(group):
    sheet = Sheet(_nested_shafts())
    with pytest.raises(ValueError, match="profile_group"):
        sheet.groove(axis="z", at=(0, 0, 0), width=2, diameter=26, profile_group=group)


def test_groove_does_not_adopt_another_body_when_its_profile_is_absent(source):
    groove = source.grooves[0]
    other = tuple(p for p in source.turned_profiles if p.profile != groove.profile)
    assert len(other) == 1
    assert require_unambiguous_groove_owner(groove, other) == ()


def test_axial_lint_credits_a_keyed_groove_only_to_its_own_coaxial_profile():
    sheet = Sheet.from_part(_nested_shafts()).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    for feature in sheet.features:
        if feature.kind == "step":
            sheet.note("AXIAL SEGMENT", feature, satisfies=("step.length",))
        elif feature.kind == "groove":
            sheet.note("2 WIDE GROOVE", feature, satisfies=("groove.length",))
    drawing = sheet.build()
    assert not [i for i in drawing.lint() if i.code == "axial_length_missing"]


def test_structurally_similar_profile_is_not_public_membership(source):
    from types import SimpleNamespace

    groove = source.grooves[0]
    forged = SimpleNamespace(**vars(groove.profile))
    with pytest.raises(TypeError, match="exact public TurnedProfileKey"):
        require_unambiguous_groove_owner(replace(groove, profile=forged), source.turned_profiles)


@pytest.mark.parametrize("group", ("", " ", 1))
def test_owner_join_rejects_malformed_retained_author_group(source, group):
    from types import SimpleNamespace

    groove = source.grooves[0]
    retained = SimpleNamespace(
        axis=groove.axis,
        at=groove.at,
        width=groove.width,
        diameter=groove.diameter,
        profile_group=group,
    )
    with pytest.raises(ValueError, match="profile_group must be a non-empty string"):
        require_unambiguous_groove_owner(retained, source.turned_profiles)
