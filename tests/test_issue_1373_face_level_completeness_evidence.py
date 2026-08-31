"""#1373 — FaceLevel evidence preserves occurrences without duplicating height requirements."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Align, Box, Compound, Cylinder, Pos, Rot, import_step

from draftwright.evaluation.step_analysis import (
    _default_observers,
    _face_level_disposition,
    _face_level_drawing_outcomes,
    _face_level_model_outcomes,
    _face_level_owner_key,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-face-levels-v1.json"


def _fixture(name: str):
    return import_step(CORPUS.parent / name)


def _single():
    aligned = (Align.CENTER, Align.CENTER, Align.MIN)
    return Box(60, 40, 10, align=aligned) + Pos(-15, 0, 10) * Box(30, 40, 15, align=aligned)


def _uniform_staircase():
    part = None
    for index in range(4):
        height = (index + 1) * 10
        width = (4 - index) * 20
        tread = Pos(width / 2, 0, height / 2) * Box(width, 30, height)
        part = tread if part is None else part + tread
    return part


def _nested_aabb_staircase(*, y: float) -> object:
    aligned = (Align.MIN, Align.MIN, Align.MIN)
    return Pos(11, y, 11) * (
        Box(3, 3, 1, align=aligned) + Box(2, 3, 2, align=aligned) + Box(1, 3, 3, align=aligned)
    )


def test_versioned_face_level_corpus_covers_occurrences_owners_and_negatives() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("face-levels",)
    assert len(corpus.cases) == 11
    assert sum(len(case.expected) for case in corpus.cases) == 16
    hashes = [case.provenance["sha256"] for case in corpus.cases]
    assert len(hashes) == len(set(hashes))
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "compound",
        "inapplicable-substrate",
        "multiple",
        "multiple-equal",
        "negative",
        "overlapping-family",
        "positive",
        "rotational",
        "side-normal",
        "threshold",
        "topology-order-variant",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)
    assert all(
        "'1970-01-01T00:00:00'" in Path(case.provenance["fixture"]).read_text().splitlines()[3]
        for case in corpus.cases
    )


def test_real_face_level_corpus_scores_all_independent_layers() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 16
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 32
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 50
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)


@pytest.mark.parametrize(
    "fixture",
    ("face-level-single.step", "face-level-topology-a.step", "face-level-compound-equal.step"),
)
def test_global_rungs_cross_every_public_boundary(fixture: str) -> None:
    observed = _default_observers()["face-levels"](_fixture(fixture))

    assert observed
    assert {fact.parameters["disposition"] for fact in observed} == {"global-height-rung"}
    assert all(set(fact.downstream.values()) == {"supported"} for fact in observed)


def test_equal_body_local_occurrences_share_global_rungs_without_disappearing() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(_fixture("face-level-compound-equal.step"))
    recognition = drawing.recognition()
    assert recognition is not None
    ladder = next(feature for feature in drawing.model().features if feature.kind == "step_level")
    observed = _default_observers()["face-levels"](_fixture("face-level-compound-equal.step"))

    assert len(recognition.step_levels) == len(observed) == 4
    assert [level.z for level in recognition.step_levels] == [10.0, 10.0, 20.0, 20.0]
    assert ladder.levels == (10.0, 20.0)
    assert len([name for name in drawing.annotations() if name.startswith("dim_step")]) == 2
    assert all(fact.downstream["drawing_consumer"] == "supported" for fact in observed)


@pytest.mark.parametrize(
    ("fixture", "disposition", "count"),
    (
        ("face-level-through-owned.step", "through-step-owned", 2),
        ("plate-t-yz.step", "plate-owned", 1),
        ("face-level-turned-owned.step", "turned-profile-owned", 1),
        ("pad-x-positive.step", "side-pad-owned", 2),
        ("pocket-edge-anchored.step", "edge-pocket-owned", 1),
    ),
)
def test_substrate_occurrences_retain_one_exact_alternate_owner(
    fixture: str, disposition: str, count: int
) -> None:
    observed = _default_observers()["face-levels"](_fixture(fixture))

    assert len(observed) == count
    assert {fact.parameters["disposition"] for fact in observed} == {disposition}
    assert all(fact.downstream["ir_adapter"] == "supported" for fact in observed)
    assert all(fact.downstream["generated_code"] == "supported" for fact in observed)
    # A substrate record has no second FaceLevel drawing/declaration requirement.
    assert all(fact.downstream["dsl_declaration"] == "unknown" for fact in observed)
    assert all(fact.downstream["drawing_consumer"] == "unknown" for fact in observed)


def test_plural_turned_profiles_retain_each_body_local_face_level_owner() -> None:
    aligned = (Align.CENTER, Align.CENTER, Align.MIN)
    left = Cylinder(15, 20, align=aligned) + Pos(0, 0, 20) * Cylinder(10, 20, align=aligned)
    right = Pos(80, 0, 0) * (
        Cylinder(12, 15, align=aligned) + Pos(0, 0, 15) * Cylinder(8, 25, align=aligned)
    )

    observed = _default_observers()["face-levels"](Compound(children=[left, right]))

    assert len(observed) == 2
    assert {fact.parameters["disposition"] for fact in observed} == {"turned-profile-owned"}
    assert {tuple(fact.identity["support"]) for fact in observed} == {
        (-15.0, 15.0, -15.0, 15.0),
        (68.0, 92.0, -12.0, 12.0),
    }
    assert all(fact.downstream["ir_adapter"] == "supported" for fact in observed)
    assert all(fact.downstream["generated_code"] == "supported" for fact in observed)
    assert all(fact.downstream["dsl_declaration"] == "unknown" for fact in observed)
    assert all(fact.downstream["drawing_consumer"] == "unknown" for fact in observed)


def test_mixed_global_and_pad_levels_keep_body_local_ownership_at_shared_z() -> None:
    pad = Pos(200, 100, 0) * _fixture("pad-x-positive.step")
    part = Compound(children=[_uniform_staircase(), pad])

    observed = _default_observers()["face-levels"](part)
    global_facts = [
        fact for fact in observed if fact.parameters["disposition"] == "global-height-rung"
    ]
    pad_facts = [fact for fact in observed if fact.parameters["disposition"] == "side-pad-owned"]

    assert len(global_facts) == 3
    assert all(set(fact.downstream.values()) == {"supported"} for fact in global_facts)
    assert len(pad_facts) == 2
    assert all(fact.downstream["ir_adapter"] == "supported" for fact in pad_facts)
    assert all(fact.downstream["generated_code"] == "supported" for fact in pad_facts)
    assert all(fact.downstream["dsl_declaration"] == "unknown" for fact in pad_facts)
    assert all(fact.downstream["drawing_consumer"] == "unknown" for fact in pad_facts)


@pytest.mark.parametrize(
    ("fixture", "placement", "alternate_disposition", "alternate_count"),
    (
        ("plate-t-yz.step", Pos(200, 100, 0), "plate-owned", 1),
        ("pocket-edge-anchored.step", Pos(200, 100, 6), "edge-pocket-owned", 1),
        ("face-level-through-owned.step", Pos(200, 100, 0), "through-step-owned", 2),
        ("face-level-turned-owned.step", Pos(200, 100, -10), "turned-profile-owned", 1),
    ),
)
def test_every_alternate_owner_is_body_local_at_a_shared_z(
    fixture: str,
    placement,
    alternate_disposition: str,
    alternate_count: int,
) -> None:
    part = Compound(children=[_uniform_staircase(), placement * _fixture(fixture)])

    observed = _default_observers()["face-levels"](part)
    global_facts = [
        fact for fact in observed if fact.parameters["disposition"] == "global-height-rung"
    ]
    alternate_facts = [
        fact for fact in observed if fact.parameters["disposition"] == alternate_disposition
    ]

    assert len(global_facts) == 3
    assert all(set(fact.downstream.values()) == {"supported"} for fact in global_facts)
    assert len(alternate_facts) == alternate_count
    assert all(fact.downstream["ir_adapter"] == "supported" for fact in alternate_facts)
    assert all(fact.downstream["generated_code"] == "supported" for fact in alternate_facts)


@pytest.mark.parametrize("transverse", (False, True))
def test_turned_profile_aabb_is_not_body_identity(transverse: bool) -> None:
    aligned = (Align.CENTER, Align.CENTER, Align.MIN)
    shaft = Cylinder(15, 20, align=aligned) + Pos(0, 0, 20) * Cylinder(10, 20, align=aligned)
    if transverse:
        shaft = Rot(90, 0, 0) * shaft
        stair = _nested_aabb_staircase(y=-10)
    else:
        stair = _nested_aabb_staircase(y=11)
    part = Compound(children=[shaft, stair])

    observed = _default_observers()["face-levels"](part)
    staircase = [fact for fact in observed if fact.parameters["z"] in {12.0, 13.0}]

    assert len(staircase) == 2
    assert {fact.parameters["disposition"] for fact in staircase} == {"global-height-rung"}
    assert all(fact.downstream["ir_adapter"] == "supported" for fact in staircase)
    assert all(fact.downstream["dsl_declaration"] == "supported" for fact in staircase)
    assert all(fact.downstream["generated_code"] == "supported" for fact in staircase)
    # The transverse compound can exhaust placement space, but it must be an explicit layout
    # failure rather than the former silent ownership gap at every boundary.
    assert all(fact.downstream["drawing_consumer"] != "unknown" for fact in staircase)


def test_non_three_decimal_global_level_round_trips_at_public_precision() -> None:
    aligned = (Align.CENTER, Align.CENTER, Align.MIN)
    z = 10.0004
    part = Box(60, 40, z, align=aligned) + Pos(-15, 0, z) * Box(30, 40, 15, align=aligned)

    (observed,) = _default_observers()["face-levels"](part)

    assert observed.parameters == {"z": 10.0, "disposition": "global-height-rung"}
    assert set(observed.downstream.values()) == {"supported"}


def test_non_three_decimal_alternate_owner_round_trips_at_public_precision() -> None:
    part = Pos(0.0004, 0.0004, 0.0004) * _fixture("pad-x-positive.step")

    observed = _default_observers()["face-levels"](part)

    assert len(observed) == 2
    assert {fact.parameters["disposition"] for fact in observed} == {"side-pad-owned"}
    assert all(fact.downstream["ir_adapter"] == "supported" for fact in observed)
    assert all(fact.downstream["generated_code"] == "supported" for fact in observed)


def test_tiny_floor_and_plain_block_are_honest_empty_inventories() -> None:
    observer = _default_observers()["face-levels"]

    assert observer(_fixture("face-level-tiny-negative.step")) == ()
    assert observer(_fixture("plain-block.step")) == ()


def test_deleting_provider_levels_cannot_shrink_the_independent_denominator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import draftwright.analysis as analysis

    corpus = load_corpus(CORPUS)
    baseline = evaluate_step_corpus(corpus)
    real = analysis.build_raw_recognition_result

    def without_levels(*args, **kwargs):
        return replace(real(*args, **kwargs), step_levels=())

    monkeypatch.setattr(analysis, "build_raw_recognition_result", without_levels)
    weakened = evaluate_step_corpus(corpus)

    assert baseline.detection.matched == 16
    assert weakened.detection.matched == 0
    assert weakened.detection.missed == 16
    assert weakened.detection.recall == 0.0


def test_removing_the_global_ladder_loses_ir_adapter_credit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from draftwright.drawing import Drawing
    from draftwright.model import PartModel

    real = Drawing.model

    def without_ladder(self):
        model = real(self)
        return PartModel(
            bbox=model.bbox,
            orientation=model.orientation,
            features=[feature for feature in model.features if feature.kind != "step_level"],
            datums=list(model.datums),
            decorations=dict(model.decorations),
            requested_dimensions=model.requested_dimensions,
            authored_dimensions=model.authored_dimensions,
        )

    monkeypatch.setattr(Drawing, "model", without_ladder)
    observed = _default_observers()["face-levels"](_single())

    assert len(observed) == 1
    assert observed[0].downstream["ir_adapter"] == "unknown"


def test_a_generated_model_that_drops_the_ladder_loses_only_that_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import draftwright.evaluation.step_analysis as evaluation
    from draftwright.model import PartModel

    real = evaluation._generated_sheet_model

    def without_ladder(part, model):
        generated = real(part, model)
        return PartModel(
            bbox=generated.bbox,
            orientation=generated.orientation,
            features=[feature for feature in generated.features if feature.kind != "step_level"],
            datums=list(generated.datums),
            decorations=dict(generated.decorations),
            requested_dimensions=generated.requested_dimensions,
            authored_dimensions=generated.authored_dimensions,
        )

    monkeypatch.setattr(evaluation, "_generated_sheet_model", without_ladder)
    (observed,) = _default_observers()["face-levels"](_single())

    assert observed.downstream["generated_code"] == "unknown"
    assert observed.downstream["ir_adapter"] == "supported"
    assert observed.downstream["dsl_declaration"] == "supported"
    assert observed.downstream["drawing_consumer"] == "supported"


@pytest.mark.parametrize("damage", ("base", "adjacent-base-bin", "supports"))
def test_global_ladder_requires_its_independent_base_and_support_roster(damage: str) -> None:
    from draftwright import build_drawing

    drawing = build_drawing(_single())
    recognition = drawing.recognition()
    assert recognition is not None
    levels = tuple(recognition.step_levels)
    features = list(drawing.model().features)
    owners = tuple(_face_level_disposition(level, features) for level in levels)
    dispositions = tuple(disposition for disposition, _owner in owners)
    owner_keys = tuple(_face_level_owner_key(disposition, owner) for disposition, owner in owners)
    index = next(i for i, feature in enumerate(features) if feature.kind == "step_level")
    ladder = features[index]
    if damage == "base":
        features[index] = replace(ladder, base=-123.0)
    elif damage == "adjacent-base-bin":
        features[index] = replace(ladder, base=0.0005)
    else:
        features[index] = replace(ladder, level_supports=())

    assert _face_level_model_outcomes(
        levels,
        features,
        dispositions,
        owner_keys,
        base=0.0,
    ) == ["unknown"]


def test_alternate_owner_credit_requires_the_complete_plate_interval() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(_fixture("plate-t-yz.step"))
    recognition = drawing.recognition()
    assert recognition is not None
    levels = tuple(recognition.step_levels)
    features = list(drawing.model().features)
    owners = tuple(_face_level_disposition(level, features) for level in levels)
    dispositions = tuple(disposition for disposition, _owner in owners)
    owner_keys = tuple(_face_level_owner_key(disposition, owner) for disposition, owner in owners)
    owner = owners[0][1]
    index = next(i for i, feature in enumerate(features) if feature is owner)
    features[index] = replace(owner, lo=-50.0)

    assert _face_level_model_outcomes(
        levels,
        features,
        dispositions,
        owner_keys,
        base=0.0,
    ) == ["unknown"]


def test_severing_measurement_provenance_loses_drawing_credit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from draftwright.registry import AnnotationRegistry

    real = AnnotationRegistry.measurement_of

    def without_step_claim(self, name):
        return tuple(
            identity for identity in real(self, name) if identity.parameter != "step_height.length"
        )

    monkeypatch.setattr(AnnotationRegistry, "measurement_of", without_step_claim)
    (observed,) = _default_observers()["face-levels"](_single())

    assert observed.downstream["drawing_consumer"] == "unsupported"
    assert observed.downstream["ir_adapter"] == "supported"


def test_moving_the_dimension_span_to_another_rung_loses_exact_drawing_credit() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(_single())
    recognition = drawing.recognition()
    assert recognition is not None
    (name,) = [name for name in drawing.annotations() if name.startswith("dim_step")]
    annotation = drawing.registry.named(name)
    old = annotation._dw_measurement_span
    annotation._dw_measurement_span = (
        old[0],
        (old[1][0], old[1][1], old[1][2] + 1.0),
    )

    assert _face_level_drawing_outcomes(tuple(recognition.step_levels), drawing) == ["unsupported"]


def test_moving_a_representative_rise_span_loses_every_collapsed_rung() -> None:
    from draftwright import build_drawing
    from draftwright.model.compiled import compile_dimensions

    drawing = build_drawing(_uniform_staircase())
    recognition = drawing.recognition()
    assert recognition is not None
    ladder = compile_dimensions(drawing.model()).ladder("step_height")
    assert ladder is not None and ladder.representative
    assert _face_level_drawing_outcomes(tuple(recognition.step_levels), drawing) == [
        "supported"
    ] * len(recognition.step_levels)

    annotation = drawing.registry.named("dim_step_typ")
    old = annotation._dw_measurement_span
    annotation._dw_measurement_span = (
        old[0],
        (old[1][0], old[1][1], old[1][2] + 100.0),
    )

    assert _face_level_drawing_outcomes(tuple(recognition.step_levels), drawing) == [
        "unsupported"
    ] * len(recognition.step_levels)


def test_through_step_owner_requires_the_exact_body_local_support() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(_fixture("face-level-through-owned.step"))
    recognition = drawing.recognition()
    assert recognition is not None
    levels = tuple(recognition.step_levels)
    features = list(drawing.model().features)
    owners = tuple(_face_level_disposition(level, features) for level in levels)
    dispositions = tuple(disposition for disposition, _owner in owners)
    owner_keys = tuple(_face_level_owner_key(disposition, owner) for disposition, owner in owners)
    index = next(i for i, feature in enumerate(features) if feature.kind == "through_step")
    owner = features[index]
    shifted = tuple((x + 1.0, z) for x, z in owner.section)
    features[index] = replace(owner, section=shifted)

    outcomes = _face_level_model_outcomes(levels, features, dispositions, owner_keys)

    assert outcomes.count("unknown") == 1
    assert outcomes.count("supported") == 1


def test_face_level_evidence_uses_only_public_provider_records() -> None:
    source = Path("src/draftwright/evaluation/step_analysis.py").read_text()

    assert "b123d_recognisers.levels" not in source
    assert "b123d_recognisers._" not in source
