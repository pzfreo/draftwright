"""Plural body-local turned-profile compiler waist for #1357."""

from __future__ import annotations

import typing
from dataclasses import replace

import pytest
from build123d import Align, Axis, Box, Compound, Cylinder, Pos, Rot

from draftwright import Sheet, build_drawing
from draftwright.linting.coverage import _dim_vertices, lint_axial_coverage
from draftwright.model.detect import build_part_model
from draftwright.sheet_emit import emit_sheet_script


def _stepped_shaft():
    return Cylinder(15, 20) + Pos(0, 0, 20) * Cylinder(10, 20)


def _parallel_shafts():
    return Compound(children=[Pos(-50, 0, 0) * _stepped_shaft(), Pos(50, 0, 0) * _stepped_shaft()])


def _depth_separated_shafts():
    return Compound(children=[Pos(0, -50, 0) * _stepped_shaft(), Pos(0, 50, 0) * _stepped_shaft()])


def _three_shaft_l():
    return Compound(
        children=[
            _stepped_shaft(),
            Pos(0, 100, 0) * _stepped_shaft(),
            Pos(100, 0, 0) * _stepped_shaft(),
        ]
    )


def _crowded_x_shaft():
    shaft = None
    z = 0.0
    for diameter, length in ((4, 1.5), (6, 2.0), (4, 2.5), (3, 25.0)):
        segment = Pos(0, 0, z) * Cylinder(
            diameter / 2,
            length,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        shaft = segment if shaft is None else shaft + segment
        z += length
    return Rot(0, 90, 0) * shaft


def _three_axially_disjoint_shafts():
    return Compound(children=[Pos(0, 0, z) * _stepped_shaft() for z in (0.0, 100.0, 200.0)])


def test_standalone_compiler_keeps_every_body_local_turned_profile():
    model = build_part_model(_parallel_shafts())
    steps = [feature for feature in model.features if feature.kind == "step"]

    assert len(steps) == 4
    assert model.orientation == "z"
    assert len({step.profile for step in steps}) == 2
    assert all(step.profile is not None for step in steps)
    assert {step.frame.origin[:2] for step in steps} == {(-50.0, 0.0), (50.0, 0.0)}
    assert {(step.span[0][2], step.span[1][2], step.diameter) for step in steps} == {
        (-10.0, 10.0, 30.0),
        (10.0, 30.0, 20.0),
    }
    assert not [
        feature for feature in model.features if feature.kind in ("envelope", "step_level")
    ]


def test_explicit_plural_input_is_the_same_compiler_contract_as_aggregate_discovery():
    from b123d_recognisers import build_raw_recognition_result

    part = _parallel_shafts()
    recognition = build_raw_recognition_result(part, rotational=True)

    detected = build_part_model(part)
    injected = build_part_model(
        part,
        profiles=recognition.turned_profiles,
        holes=recognition.holes,
        double_d_bores=recognition.double_d_bores,
        patterns=recognition.hole_patterns,
        bosses=recognition.bosses,
        polygonal_bosses=recognition.polygonal_bosses,
        polygonal_stock=recognition.polygonal_stock,
        channels=recognition.channels,
        slots=recognition.slots,
        slot_patterns=recognition.slot_patterns,
        risers=recognition.risers,
        chamfers=recognition.chamfers,
        fillets=recognition.fillets,
        circular_blind_steps=recognition.circular_blind_steps,
        paired_ramp_steps=recognition.paired_ramp_steps,
        through_steps=recognition.through_steps,
        plates=recognition.plates,
        grooves=recognition.grooves,
        flats=recognition.flats,
        pockets=recognition.pockets,
        pocket_patterns=recognition.pocket_patterns,
        pads=recognition.pads,
        step_zs=recognition.step_ladder_for_z_span(
            part.bounding_box().min.Z, part.bounding_box().max.Z
        ),
        face_levels=recognition.step_levels,
        cyls=recognition.cylinders,
    )

    assert injected.orientation == detected.orientation
    assert injected.features == detected.features
    assert injected.datums == detected.datums
    assert injected.decorations == detected.decorations

    with pytest.raises(ValueError, match="supply profiles= or the compatible singular prof="):
        build_part_model(part, prof=None, profiles=recognition.turned_profiles)


def test_parallel_profiles_render_independent_chains_with_body_local_provenance():
    drawing = build_drawing(_parallel_shafts(), title="PARALLEL SHAFTS", number="1357")
    names = [name for name in drawing.annotations() if name.startswith("m_steplen")]

    assert names == ["m_steplen0", "m_steplen1", "m_steplen2", "m_steplen3"]
    assert [
        tuple(identity.feature.frame.origin for identity in drawing.registry.measurement_of(name))
        for name in names
    ] == [
        ((-50.0, 0.0, 0.0),),
        ((-50.0, 0.0, 20.0),),
        ((50.0, 0.0, 0.0),),
        ((50.0, 0.0, 20.0),),
    ]
    assert not [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]


def test_one_parallel_profile_cannot_supply_another_profiles_axial_coverage():
    drawing = build_drawing(_parallel_shafts(), title="PARALLEL SHAFTS", number="1357")
    drawing.remove("m_steplen2")
    drawing.remove("m_steplen3")

    issues = [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]

    assert len(issues) == 1
    assert "z-axis line (50.0, 0.0)" in issues[0].message
    assert "only 0 step length(s) dimensioned" in issues[0].message


def test_reused_declared_group_token_still_requires_the_same_physical_axis_line():
    sheet = Sheet(_parallel_shafts(), title="REUSED GROUP", number="1357-RG")
    for x in (-50.0, 50.0):
        for diameter, z in ((30.0, 0.0), (20.0, 20.0)):
            step = sheet.step(
                diameter=diameter,
                length=20,
                at=(x, 0, z),
                axis="z",
                profile_group="same",
            )
            sheet.dimension(step, "step.length")
    drawing = sheet.build()
    far_names = [
        name
        for name in drawing.annotations()
        if name.startswith("m_steplen")
        and any(
            identity.feature.frame.origin[0] == 50.0
            for identity in drawing.registry.measurement_of(name)
        )
    ]
    assert len(far_names) == 2
    for name in far_names:
        drawing.remove(name)

    issues = [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]
    assert len(issues) == 1
    assert "z-axis line (50.0, 0.0)" in issues[0].message


def test_reused_group_token_does_not_merge_nearby_submillimetre_axis_lines():
    tiny = Cylinder(0.05, 10) + Pos(0, 0, 10) * Cylinder(0.04, 10)
    part = Compound(children=[tiny, Pos(0.4, 0, 0) * tiny])
    sheet = Sheet(part, title="NEAR AXES", number="1357-NEAR")
    for x in (0.0, 0.4):
        for diameter, z in ((0.1, 0.0), (0.08, 10.0)):
            step = sheet.step(
                diameter=diameter,
                length=10,
                at=(x, 0, z),
                axis="z",
                profile_group="same",
            )
            sheet.dimension(step, "step.length")
    drawing = sheet.build()
    far_names = [
        name
        for name in drawing.annotations()
        if name.startswith("m_steplen")
        and any(
            identity.feature.frame.origin[0] == pytest.approx(0.4)
            for identity in drawing.registry.measurement_of(name)
        )
    ]
    assert len(far_names) == 2
    for name in far_names:
        drawing.remove(name)

    issues = [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]
    assert len(issues) == 1
    assert "z-axis line (0.4, 0.0)" in issues[0].message


def test_parallel_profiles_use_the_orthographic_view_that_separates_their_axis_lines():
    drawing = build_drawing(_depth_separated_shafts(), title="DEPTH SHAFTS", number="1357-Y")

    front = [
        name
        for name, _annotation in drawing.annotations_in_view("front")
        if name.startswith("m_steplen")
    ]
    side = [
        name
        for name, _annotation in drawing.annotations_in_view("side")
        if name.startswith("m_steplen")
    ]

    assert len(front) == 2
    assert len(side) == 2
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in ("annotation_overlap", "annotation_ink_overlap", "axial_length_missing")
    ]


def test_deferred_profile_replay_keeps_surviving_sibling_in_view_assignment():
    drawing = build_drawing(
        _depth_separated_shafts(), title="DEFERRED PROFILE", number="1357-EDIT"
    )
    side_names = [
        name
        for name, _annotation in drawing.annotations_in_view("side")
        if name.startswith("m_steplen")
    ]
    assert len(side_names) == 2
    replay_features = {
        identity.feature
        for name in side_names
        for identity in drawing.registry.measurement_of(name)
    }
    assert len(replay_features) == 2
    for name in side_names:
        drawing.remove(name)

    with drawing.deferred():
        for feature in replay_features:
            drawing.dimension(feature, "length", role="step")

    replayed_names = {
        name
        for name in drawing.annotations()
        if name.startswith("m_steplen")
        and any(
            identity.feature in replay_features
            for identity in drawing.registry.measurement_of(name)
        )
    }
    assert len(replayed_names) == 2
    assert {drawing.view_of(name) for name in replayed_names} == {"side"}
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in ("annotation_overlap", "annotation_ink_overlap", "axial_length_missing")
    ]


def test_profile_view_assignment_uses_both_views_and_lint_keeps_exact_body_ownership():
    drawing = build_drawing(_three_shaft_l(), title="L SHAFTS", number="1357-L")
    chain_views = {
        name: drawing.view_of(name)
        for name in drawing.annotations()
        if name.startswith("m_steplen")
    }

    assert len(chain_views) == 6
    assert set(chain_views.values()) == {"front", "side"}
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in ("annotation_overlap", "annotation_ink_overlap", "axial_length_missing")
    ]

    target_names = [
        name
        for name in chain_views
        if any(
            identity.feature.frame.origin[:2] == (0.0, 100.0)
            for identity in drawing.registry.measurement_of(name)
        )
    ]
    assert len(target_names) == 2
    for name in target_names:
        drawing.remove(name)

    issues = [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]
    assert len(issues) == 1
    assert "z-axis line (0.0, 100.0)" in issues[0].message


def test_unassignable_profile_grid_drops_ambiguous_chains_and_lint_fails_closed():
    part = Compound(
        children=[
            Pos(x, y, 0) * _stepped_shaft()
            for x in (-100.0, 0.0, 100.0)
            for y in (-100.0, 0.0, 100.0)
        ]
    )
    drawing = build_drawing(part, title="SHAFT GRID", number="1357-G")
    relevant = [
        issue
        for issue in drawing.lint()
        if issue.code
        in (
            "step_dim_dropped",
            "axial_length_missing",
            "annotation_overlap",
            "annotation_ink_overlap",
        )
    ]

    assert len([name for name in drawing.annotations() if name.startswith("m_steplen")]) == 12
    assert [issue.code for issue in relevant].count("step_dim_dropped") == 3
    assert [issue.code for issue in relevant].count("axial_length_missing") == 3
    assert not [issue for issue in relevant if issue.code.endswith("overlap")]


def test_authored_front_only_view_drops_a_superimposed_profile_instead_of_overlapping():
    sheet = Sheet(_depth_separated_shafts(), title="FRONT ONLY", number="1357-F")
    for group, y in (("near", -50.0), ("far", 50.0)):
        for diameter, length, z in ((30.0, 20.0, 0.0), (20.0, 20.0, 20.0)):
            step = sheet.step(
                diameter=diameter,
                length=length,
                at=(0.0, y, z),
                axis="z",
                profile_group=group,
            )
            sheet.dimension(step, "step.length")
    sheet.view("front")
    drawing = sheet.build()

    relevant = [
        issue
        for issue in drawing.lint()
        if issue.code in ("step_dim_dropped", "axial_length_missing", "annotation_overlap")
    ]
    assert len([name for name in drawing.annotations() if name.startswith("m_steplen")]) == 2
    assert [issue.code for issue in relevant].count("step_dim_dropped") == 1
    assert [issue.code for issue in relevant].count("axial_length_missing") == 1
    assert not [issue for issue in relevant if issue.code == "annotation_overlap"]

    # Deferred replay still solves against the complete roster. Requesting the hidden profile
    # alone must not make it look assignable by forgetting the sibling that already owns this
    # sole longitudinal view.
    assigned_names = {name for name in drawing.annotations() if name.startswith("m_steplen")}
    hidden_features = {
        feature
        for feature in drawing.model().features
        if feature.kind == "step" and feature.frame.origin[1] == 50.0
    }
    assert len(assigned_names) == 2
    assert len(hidden_features) == 2
    with drawing.deferred():
        for feature in hidden_features:
            drawing.dimension(feature, "length", role="step")
    assert {
        name for name in drawing.annotations() if name.startswith("m_steplen")
    } == assigned_names
    post_replay = [
        issue
        for issue in drawing.lint()
        if issue.code in ("axial_length_missing", "step_dim_dropped", "annotation_overlap")
    ]
    assert [issue.code for issue in post_replay].count("axial_length_missing") == 1
    assert [issue.code for issue in post_replay].count("step_dim_dropped") == 2
    assert not [issue for issue in post_replay if issue.code == "annotation_overlap"]
    assert "z-axis line (0.0, 50.0)" in next(
        issue.message for issue in post_replay if issue.code == "axial_length_missing"
    )


def test_declared_parallel_steps_keep_their_caller_coordinate_axis_lines():
    part = _parallel_shafts()
    drawing = build_drawing(
        part,
        model=build_part_model(part),
        title="DECLARED PARALLEL SHAFTS",
        number="1357-D",
    )

    steps = [feature for feature in drawing.model().features if feature.kind == "step"]
    assert {step.frame.origin[:2] for step in steps} == {(-50.0, 0.0), (50.0, 0.0)}
    assert len([name for name in drawing.annotations() if name.startswith("m_steplen")]) == 4
    assert not [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]


def test_generated_script_reconstructs_plural_grouping_without_provider_keys():
    part = _parallel_shafts()
    source = emit_sheet_script(
        build_part_model(part),
        "part",
        "plural",
        title="SCRIPT PARALLEL SHAFTS",
        number="1357-S",
    )
    namespace = {"part": part}
    exec(  # noqa: S102 - the generated public Sheet source is the contract under test
        compile(source[: source.index("drawing = sheet.build()")], "<issue-1357>", "exec"),
        namespace,
    )
    drawing = namespace["sheet"].build()

    assert "TurnedProfileKey" not in source
    assert "profile_group='detected-profile-" in source
    assert len([name for name in drawing.annotations() if name.startswith("m_steplen")]) == 4
    assert not [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]


def test_emitted_groove_band_retains_detected_axial_denominator_after_mutation():
    shaft = Cylinder(12, 100, align=(Align.CENTER, Align.CENTER, Align.MIN))
    shaft -= Pos(0, 0, 45) * (
        Cylinder(12, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
        - Cylinder(9, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    )
    model = build_part_model(shaft)
    direct = build_drawing(shaft, title="GROOVED", number="1357-GROOVE-DIRECT")

    source = emit_sheet_script(
        model,
        "part",
        "grooved",
        title="GROOVED",
        number="1357-GROOVE-EMITTED",
    )
    namespace = {"part": shaft}
    exec(  # noqa: S102 - the generated public Sheet source is the contract under test
        compile(source[: source.index("drawing = sheet.build()")], "<issue-1357-groove>", "exec"),
        namespace,
    )
    emitted = namespace["sheet"].build()

    messages = []
    for drawing in (direct, emitted):
        step_name = next(name for name in drawing.annotations() if name.startswith("m_steplen"))
        drawing.remove(step_name)
        issues = [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]
        assert len(issues) == 1
        messages.append(issues[0].message)

    assert all("3 axial steps but only 2 step length(s) dimensioned" in msg for msg in messages)


def test_generated_script_preserves_adjacent_coaxial_body_partition():
    part = Compound(children=[_stepped_shaft(), Pos(0, 0, 40) * _stepped_shaft()])
    source = emit_sheet_script(
        build_part_model(part),
        "part",
        "coaxial",
        title="COAXIAL SHAFTS",
        number="1357-C",
    )
    namespace = {"part": part}
    exec(  # noqa: S102 - the generated public Sheet source is the contract under test
        compile(source[: source.index("drawing = sheet.build()")], "<issue-1357-coaxial>", "exec"),
        namespace,
    )
    drawing = namespace["sheet"].build()

    assert source.count("profile_group='detected-profile-1'") == 2
    assert source.count("profile_group='detected-profile-2'") == 2
    names = [name for name in drawing.annotations() if name.startswith("m_steplen")]
    assert names == ["m_steplen0", "m_steplen1", "m_steplen2", "m_steplen3"]
    assert not [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]


def test_axially_disjoint_coaxial_profiles_reuse_a_view_lane_and_round_trip():
    part = _three_axially_disjoint_shafts()
    direct = build_drawing(part, title="SEPARATED COAXIAL SHAFTS", number="1357-3C")

    assert len([name for name in direct.annotations() if name.startswith("m_steplen")]) == 6
    assert not [
        issue
        for issue in direct.lint()
        if issue.code in ("step_dim_dropped", "axial_length_missing", "annotation_overlap")
    ]

    source = emit_sheet_script(
        build_part_model(part),
        "part",
        "three_coaxial",
        title="SEPARATED COAXIAL SHAFTS",
        number="1357-3C",
    )
    namespace = {"part": part}
    exec(  # noqa: S102 - the generated public Sheet source is the contract under test
        compile(source[: source.index("drawing = sheet.build()")], "<issue-1357-3c>", "exec"),
        namespace,
    )
    rebuilt = namespace["sheet"].build()

    assert len([name for name in rebuilt.annotations() if name.startswith("m_steplen")]) == 6
    assert not [issue for issue in rebuilt.lint() if issue.code == "axial_length_missing"]


def test_global_step_misuse_guard_is_scoped_to_one_physical_solid():
    compound = Compound(children=[_stepped_shaft(), Pos(0, 0, 100) * Box(10, 10, 10)])
    drawing = build_drawing(compound, model=build_part_model(compound), number="1357-GUARD")
    assert len([feature for feature in drawing.model().features if feature.kind == "step"]) == 2

    solid = Pos(0, 0, 20) * Cylinder(10, 40)
    sheet = Sheet(solid, number="1357-GUARD-ONE").auto_dimensions()
    sheet.step(
        diameter=20,
        length=15,
        at=(0, 0, 0),
        axis="z",
        profile_group="caller-a",
    )
    sheet.step(
        diameter=20,
        length=15,
        at=(0, 0, 25),
        axis="z",
        profile_group="caller-b",
    )
    with pytest.raises(ValueError, match="don't span this part's full height"):
        sheet.build()


def test_generated_profile_tokens_cannot_collide_with_authored_group_namespace():
    part = Compound(children=[_stepped_shaft(), Pos(0, 0, 40) * _stepped_shaft()])
    detected = build_part_model(part)
    provider_groups = {feature.profile for feature in detected.features if feature.kind == "step"}
    authored_provider_group = min(provider_groups, key=repr)
    mixed = replace(
        detected,
        features=[
            replace(feature, profile=None, profile_group="detected-profile-1")
            if feature.kind == "step" and feature.profile == authored_provider_group
            else feature
            for feature in detected.features
        ],
    )

    source = emit_sheet_script(
        mixed,
        "part",
        "mixed_groups",
        title="MIXED PROFILE GROUPS",
        number="1357-T",
    )
    namespace = {"part": part}
    exec(  # noqa: S102 - the generated public Sheet source is the contract under test
        compile(source[: source.index("drawing = sheet.build()")], "<issue-1357-token>", "exec"),
        namespace,
    )
    rebuilt = namespace["sheet"].build()
    rebuilt_steps = [feature for feature in rebuilt.model().features if feature.kind == "step"]

    assert source.count("profile_group='detected-profile-1'") == 2
    assert source.count("profile_group='detected-profile-2'") == 2
    assert {feature.profile_group for feature in rebuilt_steps} == {
        "detected-profile-1",
        "detected-profile-2",
    }
    assert len([name for name in rebuilt.annotations() if name.startswith("m_steplen")]) == 4


def test_emitted_coaxial_profiles_keep_exact_lint_ownership_after_chain_removal():
    rod = Cylinder(5, 20) + Pos(0, 0, 20) * Cylinder(4, 20)
    part = Compound(children=[_stepped_shaft(), rod])
    source = emit_sheet_script(
        build_part_model(part),
        "part",
        "coaxial_nested",
        title="COAXIAL NESTED SHAFTS",
        number="1357-LINT",
    )
    namespace = {"part": part}
    exec(  # noqa: S102 - the generated public Sheet source is the contract under test
        compile(source[: source.index("drawing = sheet.build()")], "<issue-1357-lint>", "exec"),
        namespace,
    )
    drawing = namespace["sheet"].build()
    rod_chains = [
        name
        for name in drawing.annotations()
        if name.startswith("m_steplen")
        and any(
            identity.feature.diameter <= 10 for identity in drawing.registry.measurement_of(name)
        )
    ]
    assert len(rod_chains) == 2
    for name in rod_chains:
        drawing.remove(name)

    issues = [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]
    assert len(issues) == 1


def test_ambiguous_nested_groove_ownership_refuses_missing_upstream_identity():
    from b123d_recognisers import Groove, TurnedProfile, TurnedProfileKey, TurnedStep

    from draftwright.recognition_frame import AmbiguousTurnedOwnershipError

    def profile(radius, specs):
        key = TurnedProfileKey(
            "z",
            (0, 0, 0),
            (-radius, radius, -radius, radius, -10, 10),
        )
        return TurnedProfile.from_steps(
            TurnedStep("z", lo, hi, diameter, key) for lo, hi, diameter in specs
        )

    outer = profile(15, [(-10, 0, 30), (0, 2, 30), (2, 10, 25)])
    inner = profile(5, [(-10, 0, 10), (0, 2, 8), (2, 10, 10)])
    groove = Groove(axis="z", width=2, diameter=20, at=(0, 0, 1))
    part = Compound(children=[Cylinder(15, 20), Cylinder(5, 20)])

    with pytest.raises(AmbiguousTurnedOwnershipError, match="issues/354"):
        build_part_model(
            part,
            profiles=(outer, inner),
            grooves=(groove,),
            step_zs=(),
            face_levels=(),
        )


def test_groove_owner_uses_published_axis_precision_for_nearby_profiles():
    from b123d_recognisers import TurnedProfile, TurnedProfileKey, TurnedStep

    from draftwright.recognition_frame import profiles_owning_axial_band

    def profile(x):
        key = TurnedProfileKey("z", (x, 0, 0), (x - 0.05, x + 0.05, -0.05, 0.05, 0, 20))
        result = TurnedProfile.from_steps(
            (
                TurnedStep("z", 0, 9, 0.1, key),
                TurnedStep("z", 9, 11, 0.08, key),
                TurnedStep("z", 11, 20, 0.1, key),
            )
        )
        assert result is not None
        return result

    near, neighbour = profile(0.0), profile(0.4)
    owners = profiles_owning_axial_band(
        (near, neighbour), axis="z", centre=(0.0, 0.0, 10.0), width=2.0
    )

    assert owners == (near,)


def test_ambiguous_groove_cannot_lint_credit_both_profiles_if_compile_guard_is_bypassed(
    monkeypatch,
):
    import draftwright.analysis as analysis

    outer = Cylinder(15, 20) - Pos(0, 0, 1) * (Cylinder(15, 2) - Cylinder(10, 2))
    inner = (
        Pos(0, 0, -5) * Cylinder(5, 10)
        + Pos(0, 0, 1) * Cylinder(4, 2)
        + Pos(0, 0, 6) * Cylinder(5, 8)
    )
    part = Compound(children=[outer, inner])
    sheet = Sheet(part, title="AMBIGUOUS GROOVE LINT", number="1357-GROOVE").auto_dimensions()
    for group, specs in (
        ("outer", ((30.0, 10.0, -5.0), (25.0, 8.0, 6.0))),
        ("inner", ((10.0, 10.0, -5.0), (8.0, 2.0, 1.0), (10.0, 8.0, 6.0))),
    ):
        for diameter, length, z in specs:
            sheet.step(
                diameter=diameter,
                length=length,
                at=(0, 0, z),
                axis="z",
                profile_group=group,
            )
    sheet.groove(axis="z", width=2, diameter=20, at=(0, 0, 1))

    # Simulate an external producer bypassing Draftwright's compile-time refusal. Lint is an
    # independent consumer and must still refuse to credit one groove to both physical profiles.
    monkeypatch.setattr(
        analysis,
        "require_unambiguous_groove_owner",
        lambda _groove, profiles: tuple(profiles)[:1],
    )
    drawing = sheet.build()
    inner_middle = next(
        name
        for name in drawing.annotations()
        if name.startswith("m_steplen")
        and any(
            identity.feature.diameter == 8 for identity in drawing.registry.measurement_of(name)
        )
    )
    drawing.remove(inner_middle)

    assert [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]


def test_mixed_axis_profiles_remain_local_instead_of_inventing_one_orientation():
    part = Compound(
        children=[
            Pos(-50, 0, 0) * _stepped_shaft(),
            (Pos(50, 0, 0) * _stepped_shaft()).rotate(Axis.Y, 90),
        ]
    )
    drawing = build_drawing(part, title="MIXED SHAFTS", number="1357-M")
    steps = [feature for feature in drawing.model().features if feature.kind == "step"]

    assert drawing.model().orientation is None
    assert [step.frame.axis for step in steps].count("x") == 2
    assert [step.frame.axis for step in steps].count("z") == 2
    assert len([name for name in drawing.annotations() if name.startswith("m_steplen")]) == 4
    assert not [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]


@pytest.mark.parametrize("order", (("x", "z"), ("z", "x")))
def test_mixed_declared_orientation_and_step_misuse_are_order_independent(order):
    lower = Pos(0, 0, 15) * Cylinder(35, 10)
    upper = Pos(0, 0, 25) * Cylinder(25, 10)
    part = Box(100, 100, 20) + lower + upper
    assert len(part.solids()) == 1

    sheet = Sheet(part, title="MIXED ORDER", number="1357-ORDER").auto_dimensions()
    sheet.envelope()
    for axis in order:
        if axis == "x":
            sheet.step(
                diameter=10,
                length=10,
                at=(0, 0, 0),
                axis="x",
                profile_group="x-profile",
            )
        else:
            sheet.step(lower, profile_group="z-profile")
            sheet.step(upper, profile_group="z-profile")

    model = sheet.model()
    assert model.orientation is None
    # Exercise the public PartModel front door with deliberately stale derived metadata too;
    # both layout sizing and render-time coercion must derive the set, never trust first order.
    stale = replace(model, orientation=order[0])
    with pytest.raises(ValueError, match="don't span this part's full height"):
        build_drawing(part, model=stale, number="1357-ORDER-PM")
    with pytest.raises(ValueError, match="don't span this part's full height"):
        sheet.build()


def test_step_profile_annotation_is_runtime_resolvable_and_provider_neutral():
    from draftwright.model import StepFeature, TurnedProfileIdentity

    hint = typing.get_type_hints(StepFeature)["profile"]

    assert set(typing.get_args(hint)) == {TurnedProfileIdentity, type(None)}


def test_perpendicular_profile_axes_do_not_compete_for_one_longitudinal_lane():
    part = Compound(children=[_stepped_shaft(), Rot(0, 90, 0) * _stepped_shaft()])
    sheet = Sheet(part, title="PERPENDICULAR CHAINS", number="1357-PERP")
    for axis in ("x", "z"):
        for diameter, station in ((30.0, 0.0), (20.0, 20.0)):
            at = (station, 0.0, 0.0) if axis == "x" else (0.0, 0.0, station)
            step = sheet.step(
                diameter=diameter,
                length=20,
                at=at,
                axis=axis,
                profile_group=axis,
            )
            sheet.dimension(step, "step.length")
    sheet.view("front").pin((100, 100))
    drawing = sheet.build()

    assert len([name for name in drawing.annotations() if name.startswith("m_steplen")]) == 4
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in ("step_dim_dropped", "annotation_overlap", "axial_length_missing")
    ]


def test_alternate_plan_view_owns_crowded_x_profile_blocks_and_details():
    part = Compound(
        children=[
            Pos(0, -50, 0) * _crowded_x_shaft(),
            Pos(0, 50, 0) * _crowded_x_shaft(),
        ]
    )
    drawing = build_drawing(
        part,
        page="A2",
        scale=2.0,
        title="CROWDED X SHAFTS",
        number="1357-X",
    )
    principal_chains = {
        name: drawing.view_of(name)
        for name in drawing.annotations()
        if name.startswith("m_steplen")
    }

    assert principal_chains
    assert set(principal_chains.values()) == {"front", "plan"}
    plan_names = [name for name, chain_view in principal_chains.items() if chain_view == "plan"]
    assert plan_names
    plan_owners = {
        identity.feature.frame.origin[1]
        for name in plan_names
        for identity in drawing.registry.measurement_of(name)
    }
    detail_names = [
        name
        for name in drawing.annotations()
        if name.startswith("dim_detail_") and "steplen" in name
    ]
    detail_owners = {
        identity.feature.frame.origin[1]
        for name in detail_names
        for identity in drawing.registry.measurement_of(name)
    }
    assert {"detail_a", "detail_b"} <= set(drawing.views)
    assert len(detail_names) == 6
    assert plan_owners and plan_owners <= detail_owners
    assert all(len(drawing.registry.measurement_of(name)) == 1 for name in detail_names)
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in ("annotation_overlap", "view_annotation_overlap", "axial_length_missing")
    ]


def test_authored_lengths_separate_parallel_chains_without_leaking_withheld_diameters():
    sheet = Sheet(_parallel_shafts(), title="AUTHORED PARALLEL SHAFTS", number="1357-A")
    steps = []
    for x in (-50.0, 50.0):
        steps.extend(
            (
                sheet.step(diameter=30, length=20, at=(x, 0, 0), axis="z"),
                sheet.step(diameter=20, length=20, at=(x, 0, 20), axis="z"),
            )
        )
    for step in steps:
        sheet.dimension(step, "step.length")

    drawing = sheet.build()
    chains = [
        annotation
        for name, annotation in drawing.annotations_in_view("front")
        if name.startswith("m_steplen")
    ]

    assert len(chains) == 4
    assert len({round(_dim_vertices(chain)[0][0], 3) for chain in chains}) == 2
    assert not [
        name for name in drawing.annotations() if name.startswith(("m_dia", "dim_od", "ldr_"))
    ]
    assert not [issue for issue in drawing.lint() if issue.code == "annotation_overlap"]


def test_authored_y_profiles_use_plan_cells_without_reading_withheld_diameters():
    shaft = _stepped_shaft().rotate(Axis.X, 90)
    part = Compound(children=[Pos(-50, 0, 0) * shaft, Pos(50, 0, 0) * shaft])
    sheet = Sheet(part, title="AUTHORED Y SHAFTS", number="1357-YP")
    steps = []
    for x in (-50.0, 50.0):
        steps.extend(
            (
                sheet.step(diameter=30, length=20, at=(x, 0, 0), axis="y"),
                sheet.step(diameter=20, length=20, at=(x, 20, 0), axis="y"),
            )
        )
    for step in steps:
        sheet.dimension(step, "step.length")

    drawing = sheet.build()

    assert (
        len(
            [
                name
                for name in drawing.annotations_in_view("plan")
                if name[0].startswith("m_steplen")
            ]
        )
        == 2
    )
    assert (
        len(
            [
                name
                for name in drawing.annotations_in_view("side")
                if name[0].startswith("m_steplen")
            ]
        )
        == 2
    )
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in ("annotation_overlap", "view_annotation_overlap")
    ]


def test_plural_none_and_positional_singular_lint_compatibility():
    from b123d_recognisers import TurnedProfile, TurnedStep, build_raw_recognition_result

    assert build_part_model(Box(1, 1, 1), profiles=None).orientation is None

    part = _stepped_shaft()
    recognition = build_raw_recognition_result(part, rotational=True)
    drawing = build_drawing(part)
    assert lint_axial_coverage(part, drawing, recognition=recognition) == []
    assert (
        lint_axial_coverage(
            part,
            drawing,
            None,
            recognition.turned_profiles[0],
            recognition,
        )
        == []
    )

    legacy = TurnedProfile.from_steps(
        (
            TurnedStep("z", -10, 10, 30),
            TurnedStep("z", 10, 30, 20),
        )
    )
    assert legacy is not None and legacy.profile is None
    legacy_model = build_part_model(part, profiles=(legacy,))
    assert len([feature for feature in legacy_model.features if feature.kind == "step"]) == 2
    legacy_drawing = build_drawing(part, model=legacy_model)
    assert lint_axial_coverage(part, legacy_drawing, profiles=(legacy,)) == []
    with pytest.raises(
        ValueError, match="plural turned profiles require body-local profile identity"
    ):
        build_part_model(part, profiles=(legacy, legacy))


@pytest.mark.parametrize("profile_group", ("", "   ", 7))
def test_declared_profile_group_rejects_empty_or_non_string_tokens(profile_group):
    sheet = Sheet(Box(2, 2, 2))
    with pytest.raises(ValueError, match="profile_group must be a non-empty string"):
        sheet.step(
            diameter=2,
            length=2,
            at=(0, 0, 0),
            axis="z",
            profile_group=profile_group,
        )


def test_legacy_ungrouped_steps_join_a_real_gap_only_to_its_collinear_groove():
    centre_min = (Align.CENTER, Align.CENTER, Align.MIN)
    part = Cylinder(15, 200, align=centre_min)
    part -= Pos(0, 0, 95) * (
        Cylinder(15, 10, align=centre_min) - Cylinder(12, 10, align=centre_min)
    )

    def build_with_groove(at, *, axis="z"):
        sheet = Sheet(part, title="UNGROUPED GROOVE", number="1357-UG")
        steps = [
            sheet.step(diameter=30, length=95, at=(0, 0, 47.5), axis="z"),
            sheet.step(diameter=30, length=95, at=(0, 0, 152.5), axis="z"),
        ]
        for step in steps:
            sheet.dimension(step, "step.length")
        groove = sheet.groove(axis=axis, width=10, diameter=24, at=at)
        sheet.dimension(groove, "groove.length")
        sheet.dimension(groove, "groove.diameter")
        return sheet.build()

    collinear = build_with_groove((0, 0, 100))
    off_line = build_with_groove((50, 0, 100))
    with pytest.raises(ValueError, match="declared steps don't span this part's full height"):
        build_with_groove((0, 0, 100), axis="x")

    for drawing in (collinear, off_line):
        assert len([name for name in drawing.annotations() if name.startswith("m_steplen")]) == 2
        assert len([name for name in drawing.annotations() if name.startswith("m_groove_")]) == 1
    assert not [
        issue
        for issue in collinear.lint()
        if issue.code
        in (
            "axial_length_missing",
            "step_dim_dropped",
            "groove_requirement_unverifiable",
        )
    ]
    unverifiable = [
        issue for issue in off_line.lint() if issue.code == "groove_requirement_unverifiable"
    ]
    assert len(unverifiable) == 1
    assert "groove at (0.0, 0.0, 100.0)" in unverifiable[0].message
