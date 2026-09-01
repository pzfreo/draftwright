"""#1370 — Double-D completeness crosses the public declaration and drawing seams."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Compound, Cylinder, Pos, import_step

from draftwright.evaluation.step_analysis import (
    _default_observers,
    _double_d_axis,
    _double_d_callout_has_roles,
    _double_d_direction,
    _double_d_exclusive_owners,
    _double_d_feature_key,
    _double_d_model_outcomes,
    _double_d_record_key,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-double-d-bores-v1.json"
CENTER = (Align.CENTER, Align.CENTER, Align.CENTER)


def _part(name: str = "double-d-single-z.step"):
    return import_step(CORPUS.parent / name)


def _states(boundary: str) -> set[str]:
    observed = _default_observers()["double-d-bores"](_part())
    assert len(observed) == 1, "fixture must produce one Double-D observation"
    return {fact.downstream[boundary] for fact in observed}


def _cutter():
    return Cylinder(5, 20, align=CENTER) & Box(7.2, 30, 20, align=CENTER)


def _double_d_part(depth: float):
    cutter = Cylinder(5, depth + 10, align=CENTER) & Box(7.2, 30, depth + 10, align=CENTER)
    return Box(30, 30, depth, align=CENTER) - cutter


def test_versioned_double_d_corpus_covers_each_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.1.0", 1)
    assert corpus.scope == ("double-d-bores",)
    assert len(corpus.cases) == 11
    assert sum(len(case.expected) for case in corpus.cases) == 10
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {"positive", "negative", "ambiguous", "compound", "topology-order-variant"} <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)
    fixtures = sorted(CORPUS.parent.glob("double-d-*.step"))
    assert len(fixtures) == 10
    assert all(
        "'2000-01-01T00:00:00'" in fixture.read_text(encoding="utf-8").split("ENDSEC;", 1)[0]
        for fixture in fixtures
    )


def test_real_double_d_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert (first.parameter_fidelity.passed, first.parameter_fidelity.total) == (50, 50)
    assert first.parameter_fidelity.score == 1.0
    assert (first.downstream_usefulness.passed, first.downstream_usefulness.total) == (40, 40)
    assert first.downstream_usefulness.score == 1.0
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_provider_aggregate_owns_double_d_once_and_does_not_recount_a_round_hole() -> None:
    from b123d_recognisers import build_raw_recognition_result

    recognition = build_raw_recognition_result(_part())

    assert len(recognition.double_d_bores) == 1
    assert recognition.holes == ()
    observed = _default_observers()["double-d-bores"](_part())
    assert len(observed) == 1
    assert observed[0].parameters == {
        "major_diameter": 10.0,
        "across_flats": 7.2,
        "depth": 10.0,
        "through": True,
        "flat_direction": (1.0, 0.0, 0.0),
    }


def test_a_disjoint_coaxial_round_hole_keeps_independent_double_d_ownership() -> None:
    from b123d_recognisers import build_raw_recognition_result

    remote_round_hole = Pos(0, 0, 30) * (
        Box(30, 30, 10, align=CENTER) - Cylinder(3, 20, align=CENTER)
    )
    part = Compound(children=[_part(), remote_round_hole])
    recognition = build_raw_recognition_result(part)

    assert len(recognition.double_d_bores) == 1
    assert len(recognition.holes) == 1
    assert _double_d_exclusive_owners(recognition.double_d_bores, recognition.holes) == [True]
    observed = _default_observers()["double-d-bores"](part)
    assert len(observed) == 1
    assert set(observed[0].downstream.values()) == {"supported"}


@pytest.mark.parametrize("depth", [10.003, 50.003])
@pytest.mark.parametrize("opening", ["high", "low", "near-unit-high"])
def test_an_ordinary_hole_on_the_same_span_invalidates_only_double_d_credit(
    monkeypatch, depth: float, opening: str
) -> None:
    from b123d_recognisers import HoleRecord

    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def with_overlapping_hole(*args, **kwargs):
        recognition = original(*args, **kwargs)
        bore = recognition.double_d_bores[0]
        if opening == "low":
            axis = bore.axis
            location = tuple(
                bore.location[index] - bore.axis[index] * bore.depth for index in range(3)
            )
        else:
            axis = tuple(-component for component in bore.axis)
            if opening == "near-unit-high":
                axis = tuple(component * 1.0000001 for component in axis)
            location = bore.location
        overlap = HoleRecord(
            axis=axis,
            location=location,
            diameter=bore.major_diameter,
            depth=round(bore.depth, 2),
            bottom="through",
            cbore=None,
            spotface=None,
            csink=None,
        )
        return replace(recognition, holes=(*recognition.holes, overlap))

    monkeypatch.setattr(analysis, "build_raw_recognition_result", with_overlapping_hole)
    observed = _default_observers()["double-d-bores"](_double_d_part(depth))

    assert len(observed) == 1
    assert set(observed[0].downstream.values()) == {"unknown"}


def test_public_depth_quantization_allowance_is_independently_bracketed() -> None:
    from b123d_recognisers import HoleRecord, build_raw_recognition_result

    source = build_raw_recognition_result(_part()).double_d_bores[0]
    ordinary = HoleRecord(
        axis=tuple(-component for component in source.axis),
        location=source.location,
        diameter=source.major_diameter,
        depth=10.0,
        bottom="through",
        cbore=None,
        spotface=None,
        csink=None,
    )
    # Independent public-contract arithmetic: four-decimal DoubleDBore depths and two-decimal
    # HoleRecord depths can differ by at most 0.00505 mm after their half-quanta combine.
    inside = replace(source, depth=10.005)
    outside = replace(source, depth=10.0052)

    assert _double_d_exclusive_owners((inside,), (ordinary,)) == [False]
    assert _double_d_exclusive_owners((outside,), (ordinary,)) == [True]


def test_every_double_d_boundary_is_supported_on_the_real_public_path() -> None:
    for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"):
        assert _states(boundary) == {"supported"}


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ((1, 0), "finite 3-vector"),
        ((0, 0, 0), "non-zero"),
        ((1, 1, 0), "principal"),
        ((2, 0, 0), "unit length"),
    ],
)
def test_double_d_axis_validation_fails_closed(value, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _double_d_axis(value)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ((1, 0), "finite 3-vector"),
        ((0, 0, 0), "non-zero"),
    ],
)
def test_double_d_flat_direction_validation_fails_closed(value, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _double_d_direction(value)


def test_double_d_flat_direction_is_an_unoriented_line() -> None:
    assert _double_d_direction((-1, 0, 0)) == (1.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "changes",
    [
        {"location": (0, 0)},
        {"axis": ("0", "0", "1")},
        {"location": (False, 0, 5)},
        {"axis": (0.0, 0.0, -1.0)},
        {"major_diameter": "10"},
        {"depth": True},
        {"flat_direction": (2.0, 0.0, 0.0)},
        {"major_diameter": 0},
        {"through": 1},
        {"flat_direction": (0, 0, 1)},
    ],
)
def test_malformed_provider_meaning_is_not_a_correspondence_key(changes) -> None:
    from b123d_recognisers import build_raw_recognition_result

    bore = replace(build_raw_recognition_result(_part()).double_d_bores[0], **changes)
    with pytest.raises(ValueError):
        _double_d_record_key(bore)


def test_a_foreign_lookalike_is_not_a_public_double_d_record() -> None:
    from b123d_recognisers import build_raw_recognition_result

    bore = build_raw_recognition_result(_part()).double_d_bores[0]
    lookalike = SimpleNamespace(**vars(bore))

    with pytest.raises(ValueError, match="public DoubleDBore"):
        _double_d_record_key(lookalike)


@pytest.mark.parametrize(
    "feature",
    [
        pytest.param(SimpleNamespace(kind="envelope", profile=None), id="not-a-profile"),
        pytest.param(
            SimpleNamespace(
                kind="hole",
                profile="double_d",
                frame=SimpleNamespace(axis="xy", origin=(0, 0, 5)),
                diameter=10,
                across_flats=7.2,
                depth=10,
                through=True,
                profile_direction=(1, 0, 0),
            ),
            id="non-principal-axis",
        ),
        pytest.param(
            SimpleNamespace(
                kind="hole",
                profile="double_d",
                frame=SimpleNamespace(axis="z", origin=(0, 0, 5)),
                diameter=0,
                across_flats=7.2,
                depth=10,
                through=True,
                profile_direction=(1, 0, 0),
            ),
            id="non-positive-size",
        ),
        pytest.param(
            SimpleNamespace(
                kind="hole",
                profile="double_d",
                frame=SimpleNamespace(axis="z", origin=(0, 0, 5)),
                diameter=10,
                across_flats=7.2,
                depth=10,
                through=True,
                profile_direction=(0, 0, 1),
            ),
            id="axial-flat-direction",
        ),
        pytest.param(
            SimpleNamespace(
                kind="hole",
                profile="double_d",
                frame=SimpleNamespace(axis="z", origin=("0", "0", "5")),
                diameter=10,
                across_flats=7.2,
                depth=10,
                through=True,
                profile_direction=(1, 0, 0),
            ),
            id="coercible-location",
        ),
        pytest.param(
            SimpleNamespace(
                kind="hole",
                profile="double_d",
                frame=SimpleNamespace(axis="z", origin=(0, 0, 5)),
                diameter="10",
                across_flats=7.2,
                depth=10,
                through=True,
                profile_direction=(1, 0, 0),
            ),
            id="coercible-size",
        ),
        pytest.param(
            SimpleNamespace(
                kind="hole",
                profile="double_d",
                frame=SimpleNamespace(axis="z", origin=(0, 0, 5)),
                diameter=10,
                across_flats=7.2,
                depth=10,
                through=True,
                profile_direction=(True, False, False),
            ),
            id="boolean-direction",
        ),
        pytest.param(
            SimpleNamespace(
                kind="hole",
                profile="double_d",
                frame=SimpleNamespace(axis="z", origin=(0, 0, 5)),
                diameter=10,
                across_flats=7.2,
                depth=10,
                through=True,
                profile_direction=(2.0, 0.0, 0.0),
            ),
            id="non-unit-direction",
        ),
    ],
)
def test_malformed_ir_profile_has_no_correspondence_key(feature) -> None:
    assert _double_d_feature_key(feature) is None


def test_unparseable_role_value_cannot_claim_double_d_ink() -> None:
    annotation = SimpleNamespace(label="⌀10 THRU DOUBLE-D 7.2 A/F")
    feature = SimpleNamespace(diameter="bad", across_flats=7.2)
    assert not _double_d_callout_has_roles(annotation, feature)


def test_public_sheet_word_and_generated_program_preserve_double_d_semantics() -> None:
    from draftwright import Sheet, build_drawing
    from draftwright.sheet_emit import emit_sheet_script

    part = _part()
    drawing = build_drawing(part)
    source = emit_sheet_script(drawing.model(), "part", "double_d", title="TEST", number="T-1")

    assert callable(getattr(Sheet, "double_d_bore"))
    assert "sheet.double_d_bore(" in source
    assert "major_diameter=10" in source
    assert "across_flats=7.2" in source
    assert "profile_direction=(1, 0, 0)" in source


def test_disconnected_coaxial_bodies_keep_two_full_frame_occurrences() -> None:
    observed = _default_observers()["double-d-bores"](_part("double-d-coaxial-compound.step"))

    assert len(observed) == 2
    assert {fact.identity["location"] for fact in observed} == {
        (0.0, 0.0, 5.0),
        (0.0, 0.0, 35.0),
    }
    assert all(set(fact.downstream.values()) == {"supported"} for fact in observed)


def test_coincident_equal_records_still_require_equal_ir_multiplicity() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.model.detect import build_part_model

    part = Compound(
        children=[
            Box(30, 30, 10, align=CENTER) - _cutter(),
            Box(30, 30, 10, align=CENTER) - _cutter(),
        ]
    )
    recognition = build_raw_recognition_result(part)
    model = build_part_model(part, double_d_bores=recognition.double_d_bores)

    assert len(recognition.double_d_bores) == 2
    assert _double_d_model_outcomes(recognition.double_d_bores, model.features) == [
        "supported",
        "supported",
    ]
    # Construct the reduction explicitly: retain only the first profiled owner and all furniture.
    first_profile = next(
        feature for feature in model.features if getattr(feature, "profile", None) == "double_d"
    )
    one_owner = [
        feature for feature in model.features if getattr(feature, "profile", None) != "double_d"
    ] + [first_profile]
    assert _double_d_model_outcomes(recognition.double_d_bores, one_owner) == [
        "unknown",
        "unknown",
    ]


def test_repeated_object_references_cannot_manufacture_multiplicity() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.model.detect import build_part_model

    part = _part()
    bore = build_raw_recognition_result(part).double_d_bores[0]
    feature = next(
        candidate
        for candidate in build_part_model(part, double_d_bores=(bore,)).features
        if getattr(candidate, "profile", None) == "double_d"
    )
    distinct_bore = replace(bore)
    distinct_feature = replace(feature)
    assert id(distinct_bore) != id(bore)
    assert id(distinct_feature) != id(feature)

    assert _double_d_model_outcomes((bore, bore), (feature, distinct_feature)) == [
        "unknown",
        "unknown",
    ]
    assert _double_d_model_outcomes((bore, distinct_bore), (feature, feature)) == [
        "unknown",
        "unknown",
    ]


def test_missing_or_extra_profile_owner_fails_the_complete_boundary() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.model import double_d_bore
    from draftwright.model.detect import build_part_model

    part = _part()
    recognition = build_raw_recognition_result(part)
    model = build_part_model(part, double_d_bores=recognition.double_d_bores)
    profiles = [
        feature for feature in model.features if getattr(feature, "profile", None) == "double_d"
    ]
    assert len(profiles) == 1

    assert _double_d_model_outcomes(recognition.double_d_bores, ()) == ["unknown"]
    foreign = double_d_bore(
        major_diameter=12,
        across_flats=8,
        at=(20, 0, 5),
        axis="z",
        depth=10,
        profile_direction=(1, 0, 0),
    )
    assert _double_d_model_outcomes(recognition.double_d_bores, (*model.features, foreign)) == [
        "unknown"
    ]


def test_corrupting_built_ir_loses_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def with_wrong_across_flats(self):
        model = original(self)
        return replace(
            model,
            features=[
                replace(feature, across_flats=feature.across_flats + 1)
                if getattr(feature, "profile", None) == "double_d"
                else feature
                for feature in model.features
            ],
        )

    monkeypatch.setattr(Drawing, "model", with_wrong_across_flats)
    assert _states("ir_adapter") == {"unknown"}


def test_corrupting_public_double_d_word_loses_declaration_credit(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.double_d_bore

    def with_wrong_major(self, obj=None, **kw):
        kw["major_diameter"] += 1
        return original(self, obj, **kw)

    monkeypatch.setattr(Sheet, "double_d_bore", with_wrong_major)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_corrupting_generated_double_d_argument_loses_generated_credit(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def with_wrong_across(*args, **kwargs):
        source = original(*args, **kwargs)
        damaged = source.replace("across_flats=7.2", "across_flats=7.3")
        assert damaged != source, "fixture must emit the Double-D A/F argument"
        return damaged

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", with_wrong_across)
    assert _states("ir_adapter") == {"supported"}
    assert _states("generated_code") == {"unknown"}


def test_removing_placed_double_d_callout_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_profile_callout(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(
            name
            for name in drawing.annotations()
            if "DOUBLE-D" in str(getattr(drawing.registry.named(name), "label", ""))
        )
        drawing.remove(name)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_profile_callout)
    assert _states("drawing_consumer") == {"unsupported"}


def test_role_swapped_double_d_ink_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_swapped_roles(*args, **kwargs):
        drawing = original(*args, **kwargs)
        annotation = next(
            drawing.registry.named(name)
            for name in drawing.annotations()
            if "DOUBLE-D" in str(getattr(drawing.registry.named(name), "label", ""))
        )
        assert "⌀10 THRU DOUBLE-D 7.2 A/F" in annotation.label
        annotation.label = annotation.label.replace(
            "⌀10 THRU DOUBLE-D 7.2 A/F", "⌀7.2 THRU DOUBLE-D 10 A/F"
        )
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_swapped_roles)
    assert _states("drawing_consumer") == {"unsupported"}


@pytest.mark.parametrize("replacement", ["7.2", "7.2 A/Fextra"])
def test_missing_or_corrupt_across_flats_unit_loses_drawing_credit(
    monkeypatch, replacement: str
) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_bad_unit(*args, **kwargs):
        drawing = original(*args, **kwargs)
        annotation = next(
            drawing.registry.named(name)
            for name in drawing.annotations()
            if "DOUBLE-D" in str(getattr(drawing.registry.named(name), "label", ""))
        )
        annotation.label = annotation.label.replace("7.2 A/F", replacement)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_bad_unit)
    assert _states("drawing_consumer") == {"unsupported"}


def test_deleting_provider_records_cannot_shrink_the_independent_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def without_double_d(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, double_d_bores=())

    monkeypatch.setattr(analysis, "build_raw_recognition_result", without_double_d)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 10
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


def test_double_d_observer_fails_closed_when_build_or_recognition_is_unavailable(
    monkeypatch,
) -> None:
    import draftwright.builder as builder

    def broken_build(*_args, **_kwargs):
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(builder, "build_drawing", broken_build)
    observer = _default_observers()["double-d-bores"]
    assert observer(_part()) == ()

    class DrawingWithoutRecognition:
        def recognition(self):
            return None

    monkeypatch.setattr(
        builder, "build_drawing", lambda *_args, **_kwargs: DrawingWithoutRecognition()
    )
    assert observer(_part()) == ()


def test_a_boundary_with_missing_per_bore_outcomes_fails_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_double_d_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


@pytest.mark.parametrize(
    "corruption",
    [
        "nonnumeric-location",
        "numeric-string-axis",
        "boolean-direction",
        "numeric-string-size",
        "boolean-size",
        "non-unit-axis",
        "non-unit-direction",
        "foreign-lookalike",
        "missing-direction",
    ],
)
def test_malformed_public_records_become_missed_instead_of_aborting(
    monkeypatch, corruption: str
) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.recognition

    def with_malformed_record(self):
        recognition = original(self)
        assert recognition is not None
        first = recognition.double_d_bores[0]
        if corruption == "nonnumeric-location":
            malformed = replace(first, location=("bad", first.location[1], first.location[2]))
        elif corruption == "numeric-string-axis":
            malformed = replace(first, axis=("0", "0", "1"))
        elif corruption == "boolean-direction":
            malformed = replace(first, flat_direction=(True, False, False))
        elif corruption == "numeric-string-size":
            malformed = replace(first, major_diameter="10")
        elif corruption == "boolean-size":
            malformed = replace(first, depth=True)
        elif corruption == "non-unit-axis":
            malformed = replace(first, axis=(0.0, 0.0, 1.001))
        elif corruption == "non-unit-direction":
            malformed = replace(first, flat_direction=(2.0, 0.0, 0.0))
        elif corruption == "foreign-lookalike":
            malformed = SimpleNamespace(**vars(first))
        else:
            malformed = SimpleNamespace(
                axis=first.axis,
                location=first.location,
                major_diameter=first.major_diameter,
                across_flats=first.across_flats,
                depth=first.depth,
                through=first.through,
            )
        return replace(recognition, double_d_bores=(malformed,))

    monkeypatch.setattr(Drawing, "recognition", with_malformed_record)
    assert _default_observers()["double-d-bores"](_part()) == ()


@pytest.mark.parametrize(
    "fixture",
    ["double-d-round-bore.step", "double-d-blind.step", "double-d-opposed-blind.step"],
)
def test_negative_controls_create_no_profile_or_ordinary_hole_duplication(fixture: str) -> None:
    from b123d_recognisers import build_raw_recognition_result

    recognition = build_raw_recognition_result(_part(fixture))
    assert recognition.double_d_bores == ()
