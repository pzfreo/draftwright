"""End-to-end contract for released schema-v1 convex Blend chains (#1433)."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace

import pytest
from b123d_recognisers import (
    Blend,
    Fillet,
    build_raw_recognition_result,
    recognise_blends,
)
from build123d import Axis, Box, Compound, Cylinder, Pos, Rot, fillet

from draftwright import Sheet, build_drawing
from draftwright.blend_contract import blend_provider_key, register_blend_ir_types
from draftwright.linting.blend_coverage import (
    blend_feature_key,
    blend_requirement_outcomes,
)
from draftwright.linting.issues import LintIssue
from draftwright.model import BlendFeature, Frame
from draftwright.model.compiled import DimensionId
from draftwright.model.detect import build_part_model
from draftwright.registry import AnnotationRegistry
from draftwright.sheet_emit import _feature_line, emit_sheet_script


def _small_blends(radius: float = 0.2):
    stock = Box(40, 30, 20)
    return fillet(list(stock.edges().filter_by(Axis.Z)), radius)


def _single_blend(radius: float = 0.4):
    stock = Box(27, 19, 13)
    return fillet([stock.edges().filter_by(Axis.Z)[0]], radius)


def _blend_features(model):
    return tuple(feature for feature in model.features if feature.kind == "blend")


def test_public_aggregate_precedence_and_dedicated_ir_are_singular() -> None:
    part = _small_blends()
    recognition = build_raw_recognition_result(part, rotational=False)
    model = build_part_model(part)

    assert len(recognition.blends) == 4
    assert recognition.fillets == ()
    assert len(_blend_features(model)) == 4
    assert all(
        tuple(parameter.parameter_id for parameter in feature.parameters()) == ("blend.radius",)
        for feature in _blend_features(model)
    )

    # A dimension-worthy radius is reconciled to Fillet by the provider; Draftwright does not
    # rerun or second-guess that exact defining-face precedence.
    owned = build_raw_recognition_result(_small_blends(2.0), rotational=False)
    assert owned.blends == ()
    assert len(owned.fillets) == 4
    assert _blend_features(build_part_model(_small_blends(2.0))) == ()


def test_partial_inventory_overrides_preserve_atomic_fillet_blend_ownership() -> None:
    foreign = build_raw_recognition_result(_small_blends(), rotational=False)
    with pytest.raises(TypeError, match="unexpected keyword argument 'recognition_result'"):
        build_part_model(Box(5, 5, 5), recognition_result=foreign)

    fillet_owned = _small_blends(2.0)
    standalone_blends = tuple(recognise_blends(fillet_owned))
    assert standalone_blends
    with pytest.raises(ValueError, match="fillets and blends"):
        build_part_model(fillet_owned, blends=standalone_blends)

    blend_owned = _small_blends()
    owned_blend = recognise_blends(blend_owned)[0]
    standalone_fillets = (Fillet(owned_blend.axis, owned_blend.radius, owned_blend.at),)
    with pytest.raises(ValueError, match="fillets and blends"):
        build_part_model(blend_owned, fillets=standalone_fillets)
    with pytest.raises(ValueError, match="preserve aggregate ownership"):
        build_part_model(blend_owned, blends=())
    with pytest.raises(ValueError, match="preserve aggregate ownership"):
        build_part_model(fillet_owned, fillets=())
    with pytest.raises(ValueError, match="preserve aggregate ownership"):
        build_part_model(
            blend_owned,
            blends=(*foreign.blends, foreign.blends[0]),
        )

    fillet_aggregate = build_raw_recognition_result(fillet_owned, rotational=False)
    with pytest.raises(ValueError, match="preserve aggregate ownership"):
        build_part_model(
            fillet_owned,
            fillets=(*fillet_aggregate.fillets, fillet_aggregate.fillets[0]),
        )

    aggregate = fillet_aggregate
    with pytest.raises(ValueError, match="preserve aggregate ownership"):
        build_part_model(
            fillet_owned,
            fillets=aggregate.fillets,
            blends=standalone_blends,
        )

    # Scalar anchors are not provider defining-face evidence: shifted and coincident sites
    # both fail because neither pair is the aggregate-owned partition.
    shifted_fillet = Fillet(owned_blend.axis, owned_blend.radius, (0.001, 0.0, 0.0))
    for candidate in (standalone_fillets, (shifted_fillet,)):
        with pytest.raises(ValueError, match="preserve aggregate ownership"):
            build_part_model(
                blend_owned,
                fillets=candidate,
                blends=(owned_blend,),
            )

    with pytest.raises(ValueError, match="preserve aggregate ownership"):
        build_part_model(fillet_owned, fillets=(), blends=standalone_blends)


def test_partial_inventory_generators_are_materialised_once() -> None:
    mixed = Compound([_small_blends(), Pos(100, 0, 0) * _small_blends(2.0)])
    recognition = build_raw_recognition_result(mixed, rotational=False)
    assert len(recognition.fillets) == len(recognition.blends) == 4

    with_fillet_generator = build_part_model(
        mixed,
        fillets=(record for record in reversed(recognition.fillets)),
    )
    with_blend_generator = build_part_model(
        mixed,
        blends=(record for record in reversed(recognition.blends)),
    )
    for model in (with_fillet_generator, with_blend_generator):
        assert [feature.kind for feature in model.features].count("fillet") == 4
        assert len(_blend_features(model)) == 4

    class ExplosiveEquality:
        def __eq__(self, _other):
            raise RuntimeError("user equality must not execute")

    malformed = recognition.blends[0]
    object.__setattr__(malformed, "side", ExplosiveEquality())
    with pytest.raises(ValueError, match="side must be exactly"):
        build_part_model(mixed, blends=(malformed, *recognition.blends[1:]))


def test_fully_supplied_competing_inventory_requires_internal_provenance() -> None:
    part = _small_blends()
    recognition = build_raw_recognition_result(part, rotational=False)
    source = recognition.blends[0]
    fabricated = Fillet(source.axis, source.radius, source.at)
    supplied = {
        "holes": recognition.holes,
        "double_d_bores": recognition.double_d_bores,
        "patterns": recognition.hole_patterns,
        "bosses": recognition.bosses,
        "polygonal_bosses": recognition.polygonal_bosses,
        "polygonal_stock": recognition.polygonal_stock,
        "channels": recognition.channels,
        "slots": recognition.slots,
        "slot_patterns": recognition.slot_patterns,
        "risers": recognition.risers,
        "chamfers": recognition.chamfers,
        "fillets": (fabricated,),
        "blends": (),
        "circular_blind_steps": recognition.circular_blind_steps,
        "paired_ramp_steps": recognition.paired_ramp_steps,
        "through_steps": recognition.through_steps,
        "plates": recognition.plates,
        "grooves": recognition.grooves,
        "flats": recognition.flats,
        "pockets": recognition.pockets,
        "pocket_patterns": recognition.pocket_patterns,
        "rectangular_blind_slots": recognition.rectangular_blind_slots,
        "round_bottom_blind_slots": recognition.round_bottom_blind_slots,
        "pads": recognition.pads,
        "profiles": recognition.turned_profiles,
        "step_zs": (),
        "face_levels": recognition.step_levels,
    }
    with pytest.raises(ValueError, match="aggregate recognition_result provenance"):
        build_part_model(part, **supplied)
    supplied["fillets"] = ()
    with pytest.raises(ValueError, match="aggregate recognition_result provenance"):
        build_part_model(part, **supplied)


def test_internal_aggregate_handoff_reuses_its_cylinders_without_rescan(monkeypatch) -> None:
    import draftwright.model.detect as detect

    part = _small_blends()
    recognition = build_raw_recognition_result(part, rotational=False)

    def unexpected_scan(_part):
        raise AssertionError("a completed aggregate must prevent a second cylinder scan")

    monkeypatch.setattr(detect, "analyse_cylinders", unexpected_scan)
    model = detect._build_part_model_from_recognition(part, recognition)
    assert len(_blend_features(model)) == 4


def test_model_boundary_rejects_non_records_and_malformed_fillet_primitives() -> None:
    blank = Box(5, 5, 5)
    with pytest.raises(TypeError, match="exact Fillet"):
        build_part_model(blank, fillets=(object(),))
    with pytest.raises(TypeError, match="exact CircularBlindStep"):
        build_part_model(blank, circular_blind_steps=(object(),))

    malformed_fields = (
        ("axis", "q", "axis"),
        ("turned", 1, "turned"),
        ("radius", Fraction(1, 5), "radius"),
        ("at", [0.0, 0.0, 0.0], "immutable"),
        ("at", (Fraction(0), 0.0, 0.0), "components"),
        ("radius", 10**400, "finite"),
        ("radius", 0.0, "positive"),
    )
    for field, value, message in malformed_fields:
        record = Fillet("z", 0.2, (0.0, 0.0, 0.0))
        object.__setattr__(record, field, value)
        with pytest.raises(ValueError, match=message):
            build_part_model(blank, fillets=(record,))


def test_duplicate_explicit_circular_steps_fail_closed_without_aggregate_owners() -> None:
    stepped = Box(40, 30, 20) - Pos(7.5, 15, 10) * Rot(0, 90, 0) * Cylinder(4, 25)
    source = build_raw_recognition_result(stepped, rotational=False).circular_blind_steps[0]
    blank = Box(5, 5, 5)

    with pytest.raises(ValueError, match="duplicate an ownership occurrence"):
        build_part_model(blank, circular_blind_steps=(source, source))
    with pytest.raises(ValueError, match="duplicate an ownership occurrence"):
        build_part_model(blank, fillets=(), circular_blind_steps=(source, source))


def test_sheet_word_and_generated_line_preserve_every_released_field() -> None:
    part = Rot(17, 31, 43) * _single_blend()
    source = _blend_features(build_part_model(part))[0]
    sheet = Sheet(part).authored_dimensions()
    sheet.blend(
        axis=source.axis,
        radius=source.radius,
        at=source.frame.origin,
        side=source.side,
        axis_direction=source.axis_direction,
    )
    assert sheet.model().features[0] == source

    replay = Sheet(part).authored_dimensions()
    exec(  # noqa: S102 - repository-generated public Sheet source is under test
        compile(_feature_line(source), "<blend-sheet>", "exec"),
        {"sheet": replay},
    )
    assert replay._features == [source]


def test_authored_blend_precision_survives_generated_replay_losslessly() -> None:
    sheet = Sheet(_single_blend()).authored_dimensions()
    sheet.blend(
        axis="z",
        radius=0.2001,
        at=(0.0001, -0.0002, 0.0003),
        axis_direction=(0.0, 0.0, 0.9999999),
    )
    source = sheet.model().features[0]
    assert source.axis_direction == (0.0, 0.0, 1.0)
    line = _feature_line(source)
    assert "radius=0.2001" in line
    assert "at=(0.0001, -0.0002, 0.0003)" in line

    replay = Sheet(_single_blend()).authored_dimensions()
    exec(  # noqa: S102 - repository-generated public Sheet source is under test
        compile(line, "<authored-blend-sheet>", "exec"),
        {"sheet": replay},
    )
    assert replay.model().features == [source]


def test_sheet_default_direction_and_ir_invariants_are_explicit() -> None:
    sheet = Sheet(_single_blend()).authored_dimensions()
    handle = sheet.blend(axis="x", radius=0.2, at=(0.0, 0.0, 0.0))
    feature = sheet.model().features[0]
    assert feature.axis_direction == (1.0, 0.0, 0.0)
    assert feature.references() == []
    assert handle.dimension_ids() == ("blend.radius",)

    with pytest.raises(ValueError, match="frame axis"):
        BlendFeature(Frame((0.0, 0.0, 0.0), "y"), "x", 0.2, "convex", (1.0, 0.0, 0.0))


def test_complete_generated_sheet_program_rebuilds_every_blend_requirement() -> None:
    part = _small_blends()
    source = emit_sheet_script(
        build_part_model(part), "part", "blend", title="BLENDS", number="1433"
    )
    namespace = {"part": part}
    exec(  # noqa: S102 - generated public Sheet source is the product under test
        compile(source[: source.index("drawing = sheet.build()")], "<blend-sheet>", "exec"),
        namespace,
    )
    drawing = namespace["sheet"].build()

    assert source.count(" = sheet.blend(") == 4
    assert source.count("sheet.dimension(blend") == 4
    assert len(_blend_features(drawing.model())) == 4
    issues = drawing.lint()  # declared builds acquire their one physical inventory on critique
    assert not [issue for issue in issues if issue.code.startswith("blend_")]
    assert [
        outcome.state
        for outcome in blend_requirement_outcomes(
            drawing.recognition(),
            _blend_features(drawing.model()),
            drawing.registry,
            drawing.suppressions(),
        )
    ] == ["placed"] * 4


def test_detected_drawing_places_one_grouped_callout_with_four_exact_credits() -> None:
    drawing = build_drawing(_small_blends())
    features = _blend_features(drawing.model())
    outcomes = blend_requirement_outcomes(
        drawing.recognition(), features, drawing.registry, drawing.suppressions()
    )

    assert len(features) == len(outcomes) == 4
    assert [outcome.state for outcome in outcomes] == ["placed"] * 4
    measurements = drawing.registry.measurement_of("m_blend_z0")
    assert [measurement.parameter for measurement in measurements] == ["blend.radius"] * 4
    assert drawing.get_annotation("m_blend_z0").label == "4× R0.2"
    assert not [issue for issue in drawing.lint() if issue.code.startswith("blend_")]


def test_authored_distinct_display_radii_never_share_false_group_credit() -> None:
    sheet = Sheet(_single_blend()).authored_dimensions()
    first = sheet.blend(axis="z", radius=0.2001, at=(-2.0, 0.0, 0.0))
    second = sheet.blend(axis="z", radius=0.2002, at=(2.0, 0.0, 0.0))
    sheet.dimension(first, "blend.radius").format(decimals=4)
    sheet.dimension(second, "blend.radius").format(decimals=4)
    drawing = sheet.build()

    labels_and_values = sorted(
        (
            drawing.get_annotation(name).label,
            tuple(
                measurement.feature.radius for measurement in drawing.registry.measurement_of(name)
            ),
        )
        for name in drawing.annotations()
        if name.startswith("m_blend_")
    )
    assert labels_and_values == [("R0.2001", (0.2001,)), ("R0.2002", (0.2002,))]

    # The shared Fillet renderer keeps its established numeric group/index order.
    fillet_sheet = Sheet(Box(30, 30, 20)).authored_dimensions()
    small = fillet_sheet.fillet(axis="z", radius=2, at=(-5.0, 0.0, 0.0))
    large = fillet_sheet.fillet(axis="z", radius=10, at=(5.0, 0.0, 0.0))
    fillet_sheet.dimension(small, "fillet.radius")
    fillet_sheet.dimension(large, "fillet.radius")
    fillet_drawing = fillet_sheet.build()
    assert fillet_drawing.get_annotation("m_fillet_z0").label == "R2"
    assert fillet_drawing.get_annotation("m_fillet_z1").label == "R10"


def test_live_and_deferred_callout_verbs_reuse_the_blend_renderer() -> None:
    signatures = []
    for mode in ("live", "deferred"):
        drawing = build_drawing(_single_blend())
        feature = _blend_features(drawing.model())[0]
        drawing.drop(feature)

        if mode == "live":
            name = drawing.callout(feature)
        else:
            with drawing.deferred():
                assert drawing.callout(feature) == ""
            name = next(iter(drawing.annotations_of(feature)))

        assert name.startswith("m_blend_")
        annotation = drawing.get_annotation(name)
        assert annotation.label == "R0.4"
        assert [key["parameter_id"] for key in drawing.measurement_keys(name)] == ["blend.radius"]
        signatures.append((annotation.label, drawing.view_of(name)))

    assert signatures[0] == signatures[1]


def test_missing_grouped_callout_loses_every_occurrence_credit() -> None:
    drawing = build_drawing(_small_blends())
    drawing.remove("m_blend_z0")
    outcomes = blend_requirement_outcomes(
        drawing.recognition(),
        _blend_features(drawing.model()),
        drawing.registry,
        drawing.suppressions(),
    )
    assert [outcome.state for outcome in outcomes] == ["missing"] * 4
    assert [issue.code for issue in drawing.lint()].count("blend_requirement_missing") == 4


def test_raw_and_framed_arbitrary_rigid_motion_keep_four_radius_requirements() -> None:
    moved = Rot(20, 30, 40) * _small_blends()
    raw = build_drawing(moved)
    framed = build_drawing(moved, framed_recognition=True)

    for drawing in (raw, framed):
        features = _blend_features(drawing.model())
        outcomes = blend_requirement_outcomes(
            drawing.recognition(), features, drawing.registry, drawing.suppressions()
        )
        assert len(features) == 4
        assert {feature.radius for feature in features} == {0.2}
        assert [outcome.state for outcome in outcomes] == ["placed"] * 4
        assert not [issue for issue in drawing.lint() if issue.code.startswith("blend_")]


@pytest.mark.parametrize("motion", (Rot(0, 90, 0), Rot(90, 0, 0), Rot(17, 31, 43)))
def test_independently_authored_single_chain_corpus_survives_orientation(motion) -> None:
    for drawing in (
        build_drawing(motion * _single_blend()),
        build_drawing(motion * _single_blend(), framed_recognition=True),
    ):
        features = _blend_features(drawing.model())
        assert len(features) == 1
        assert features[0].radius == 0.4
        outcomes = blend_requirement_outcomes(
            drawing.recognition(), features, drawing.registry, drawing.suppressions()
        )
        assert [outcome.state for outcome in outcomes] == ["placed"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("radius", Fraction(1, 5)),
        ("at", [0.0, 0.0, 0.0]),
        ("axis_direction", [0.0, 0.0, 1.0]),
        ("side", "concave"),
        ("axis", "q"),
        ("radius", float("nan")),
        ("at", (0.0001, 0.0, 0.0)),
        ("radius", 0.2001),
        ("axis_direction", (0.0, 0.0, 0.9999999)),
        ("axis_direction", (0.0, 0.0, 0.0)),
        ("axis_direction", (1.0, 1.0, 1.0)),
        ("at", (10**400, 0.0, 0.0)),
        ("axis_direction", (10**400, 0.0, 0.0)),
    ),
)
def test_public_boundary_refuses_values_outside_released_schema_v1(field, value) -> None:
    genuine = Blend("z", 0.2, (1.0, 2.0, 3.0), "convex", (0.0, 0.0, 1.0))
    object.__setattr__(genuine, field, value)
    with pytest.raises((TypeError, ValueError)):
        blend_provider_key(genuine)


def test_record_and_ir_subclasses_cannot_publish_completeness_identity() -> None:
    class BlendSubclass(Blend):
        pass

    class FeatureSubclass(BlendFeature):
        pass

    record = BlendSubclass("z", 0.2, (1.0, 2.0, 3.0), "convex", (0.0, 0.0, 1.0))
    feature = FeatureSubclass(
        frame=Frame((1.0, 2.0, 3.0), "z"),
        axis="z",
        radius=0.2,
        side="convex",
        axis_direction=(0.0, 0.0, 1.0),
    )
    with pytest.raises(TypeError):
        blend_provider_key(record)
    with pytest.raises(TypeError):
        blend_feature_key(feature)


def test_exact_ir_identity_and_frame_registration_cannot_be_spoofed() -> None:
    class FakeFrame:
        origin = (1.0, 2.0, 3.0)
        axis = "z"

    with pytest.raises(TypeError, match="exact Frame"):
        BlendFeature(FakeFrame(), "z", 0.2, "convex", (0.0, 0.0, 1.0))  # type: ignore[arg-type]

    FakeFeature = type(
        "BlendFeature",
        (),
        {
            "__module__": "draftwright.model.ir",
            "frame": Frame((1.0, 2.0, 3.0), "z"),
            "axis": "z",
            "radius": 0.2,
            "side": "convex",
            "axis_direction": (0.0, 0.0, 1.0),
            "kind": "blend",
        },
    )
    with pytest.raises(TypeError, match="exact BlendFeature"):
        blend_feature_key(FakeFeature())
    register_blend_ir_types(BlendFeature, Frame)
    with pytest.raises(RuntimeError, match="already registered"):
        register_blend_ir_types(FakeFeature, Frame)

    forged = object.__new__(BlendFeature)
    for name, value in (
        ("frame", Frame((1.0, 2.0, 3.0), "y")),
        ("axis", "x"),
        ("radius", 0.2),
        ("side", "convex"),
        ("axis_direction", (1.0, 0.0, 0.0)),
    ):
        object.__setattr__(forged, name, value)
    with pytest.raises(ValueError, match="frame axis disagrees"):
        blend_feature_key(forged)


def test_hostile_or_overflowing_fields_refuse_without_executing_user_protocols() -> None:
    class ExplosiveSide:
        def __ne__(self, _other):
            raise RuntimeError("comparison must not execute")

    malformed = Blend("z", 0.2, (1.0, 2.0, 3.0), "convex", (0.0, 0.0, 1.0))
    object.__setattr__(malformed, "side", ExplosiveSide())
    recognition = replace(build_drawing(_single_blend()).recognition(), blends=(malformed,))
    outcomes = blend_requirement_outcomes(recognition, (), AnnotationRegistry())
    assert [(outcome.source_at, outcome.state) for outcome in outcomes] == [
        ((0.0, 0.0, 0.0), "unverifiable")
    ]

    huge = Blend("z", 0.2, (1.0, 2.0, 3.0), "convex", (0.0, 0.0, 1.0))
    object.__setattr__(huge, "radius", 10**400)
    with pytest.raises(ValueError, match="finite"):
        blend_provider_key(huge)
    with pytest.raises(ValueError, match="finite"):
        Sheet(_single_blend()).blend(axis="z", radius=10**400, at=(0, 0, 0))


def test_mutated_recognition_or_ir_is_unverifiable_not_false_credit() -> None:
    drawing = build_drawing(_small_blends())
    recognition = drawing.recognition()
    bad_source = recognition.blends[0]
    object.__setattr__(bad_source, "at", (Fraction(0), 0.0, 0.0))
    bad_recognition = replace(recognition, blends=(bad_source,))
    assert (
        blend_requirement_outcomes(
            bad_recognition, _blend_features(drawing.model()), drawing.registry
        )[0].state
        == "unverifiable"
    )

    genuine = _blend_features(build_part_model(_small_blends()))[0]
    forged = object.__new__(BlendFeature)
    for name, value in (
        ("frame", genuine.frame),
        ("axis", genuine.axis),
        ("radius", Fraction(1, 5)),
        ("side", genuine.side),
        ("axis_direction", genuine.axis_direction),
    ):
        object.__setattr__(forged, name, value)
    with pytest.raises(ValueError):
        blend_feature_key(forged)


def test_parameter_introspection_failure_is_unverifiable() -> None:
    drawing = build_drawing(_single_blend())
    recognition = drawing.recognition()
    feature = _blend_features(drawing.model())[0]

    object.__setattr__(feature, "parameters", None)
    outcomes = blend_requirement_outcomes(recognition, (feature,), AnnotationRegistry())
    assert [outcome.state for outcome in outcomes] == ["unverifiable"]


def test_blend_ledger_distinguishes_every_explicit_outcome() -> None:
    drawing = build_drawing(_single_blend())
    recognition = drawing.recognition()
    feature = _blend_features(drawing.model())[0]

    assert blend_requirement_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="exact run RecognitionResult"):
        blend_requirement_outcomes(object(), (), AnnotationRegistry())

    malformed_inventory = replace(recognition)
    object.__setattr__(malformed_inventory, "blends", [])
    with pytest.raises(TypeError, match="immutable tuple"):
        blend_requirement_outcomes(malformed_inventory, (), AnnotationRegistry())

    omission = SimpleNamespace(feature=feature, parameter_id="blend.radius", authored=True)
    suppressed = blend_requirement_outcomes(
        recognition, drawing.model().features, AnnotationRegistry(), (omission,)
    )
    assert [outcome.state for outcome in suppressed] == ["suppressed"]

    dropped_registry = AnnotationRegistry()
    dropped_registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="blend_dropped",
            measurement_ids=(DimensionId(feature, "blend.radius"),),
            outcome_stage="placement",
        )
    )
    dropped = blend_requirement_outcomes(recognition, drawing.model().features, dropped_registry)
    assert [outcome.state for outcome in dropped] == ["dropped"]

    satisfied_registry = AnnotationRegistry()
    satisfied_registry.add(
        object(),
        "structured_note",
        "plan",
        feature=feature,
        satisfaction=DimensionId(feature, "blend.radius"),
    )
    satisfied = blend_requirement_outcomes(
        recognition, drawing.model().features, satisfied_registry
    )
    assert [outcome.state for outcome in satisfied] == ["satisfied_by_structured_note"]

    malformed_feature = SimpleNamespace(kind="blend")
    unverifiable = blend_requirement_outcomes(
        recognition, (malformed_feature,), AnnotationRegistry()
    )
    assert [outcome.state for outcome in unverifiable] == ["unverifiable"]


def test_equal_occurrences_keep_distinct_object_owned_measurement_credit() -> None:
    drawing = build_drawing(_single_blend())
    source = drawing.recognition().blends[0]
    first = _blend_features(drawing.model())[0]
    second = replace(first)
    recognition = replace(drawing.recognition(), blends=(source, source))
    registry = AnnotationRegistry()
    registry.add(
        object(),
        "one_radius",
        "plan",
        feature=first,
        measurement=DimensionId(first, "blend.radius"),
    )

    outcomes = blend_requirement_outcomes(recognition, (first, second), registry)
    assert [outcome.state for outcome in outcomes] == ["placed", "missing"]

    repeated = blend_requirement_outcomes(recognition, (first, first), registry)
    assert [outcome.state for outcome in repeated] == ["unverifiable", "unverifiable"]
