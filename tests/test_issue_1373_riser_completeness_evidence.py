"""#1373 — projected riser completeness uses independent shoulder-position facts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Align, Box, Pos, import_step

from draftwright.evaluation.step_analysis import (
    ObservationError,
    _default_observers,
    _riser_drawing_outcomes,
    _riser_facts,
    _riser_model_outcomes,
    _riser_source_facts,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-risers-v1.json"
_MIN = (Align.MIN, Align.MIN, Align.MIN)


def _rebated_block():
    return Box(60, 40, 20, align=_MIN) - Pos(30, 0, 10) * Box(30, 40, 10, align=_MIN)


def _states(boundary: str) -> set[str]:
    return {fact.downstream[boundary] for fact in _default_observers()["risers"](_rebated_block())}


def test_versioned_riser_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("risers",)
    assert len(corpus.cases) == 12
    assert sum(len(case.expected) for case in corpus.cases) == 25
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "ambiguous",
        "compound",
        "multiple",
        "multiple-equal",
        "negative",
        "overlapping-family",
        "positive",
        "principal-orientation",
        "slanted",
        "topology-order-variant",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)
    assert all(
        "'1970-01-01T00:00:00'"
        in (CORPUS.parent / case.provenance["fixture"]).read_text().splitlines()[3]
        for case in corpus.cases
    )


def test_real_riser_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 25
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 25
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 100
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


@pytest.mark.parametrize(
    ("fixture", "axis", "distance"),
    (("riser-vertical-x.step", "x", 30.0), ("riser-vertical-y.step", "y", 30.0)),
)
def test_every_principal_riser_boundary_is_observed(fixture, axis, distance) -> None:
    (fact,) = _default_observers()["risers"](import_step(CORPUS.parent / fixture))

    assert fact.identity["axis"] == axis
    assert fact.parameters["distance"] == distance
    assert set(fact.downstream.values()) == {"supported"}


def test_raw_translation_preserves_the_shoulder_distance_and_finished_dimension() -> None:
    from draftwright import build_drawing

    baseline = build_drawing(_rebated_block())
    moved = build_drawing(Pos(91, -37, 48) * _rebated_block())

    baseline_facts = [
        (fact.axis, fact.distance) for fact in _riser_facts(baseline.model().features)
    ]
    moved_facts = [(fact.axis, fact.distance) for fact in _riser_facts(moved.model().features)]
    assert moved_facts == baseline_facts
    assert _riser_drawing_outcomes(_riser_facts(moved.model().features), moved) == ["supported"]


def test_equal_compound_occurrences_retain_count_through_every_boundary() -> None:
    observed = _default_observers()["risers"](
        import_step(CORPUS.parent / "riser-compound-equal.step")
    )

    assert len(observed) == 6
    assert sorted(fact.parameters["distance"] for fact in observed) == [
        24.0,
        24.0,
        32.0,
        32.0,
        40.0,
        40.0,
    ]
    assert all(set(fact.downstream.values()) == {"supported"} for fact in observed)


@pytest.mark.parametrize(
    ("fixture", "expected_positions"),
    (
        ("riser-throughstep-remote.step", {("x", -5.0), ("x", 5.0)}),
        ("riser-throughstep-remote-z.step", {("x", -5.0), ("x", 5.0)}),
        (
            "riser-oblique-throughstep-remote.step",
            {("x", 1.0), ("x", 5.0), ("y", 12.0)},
        ),
    ),
)
def test_remote_throughstep_cannot_steal_a_disconnected_riser(fixture, expected_positions) -> None:
    observed = _default_observers()["risers"](import_step(CORPUS.parent / fixture))

    assert {(fact.identity["axis"], fact.identity["position"]) for fact in observed} == (
        expected_positions
    )
    assert all(set(fact.downstream.values()) == {"supported"} for fact in observed)


def test_collapsing_equal_compound_body_support_lowers_the_score() -> None:
    corpus = load_corpus(CORPUS)
    compound = next(case for case in corpus.cases if case.case_id == "riser-compound-equal")
    observed = list(
        _default_observers()["risers"](import_step(CORPUS.parent / "riser-compound-equal.step"))
    )
    first_support = observed[0].identity["support"]
    collapsed = [
        replace(fact, identity={**fact.identity, "support": first_support}) for fact in observed
    ]

    report = evaluate_step_corpus(
        replace(corpus, cases=(compound,)),
        observers={"risers": lambda _part: collapsed},
    )

    assert report.detection.recall == 0.5
    assert report.detection.false_positives == 3
    assert report.complete_cases == report.conformant_cases == 0


def test_model_correspondence_is_occurrence_counted_and_fails_closed() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(import_step(CORPUS.parent / "riser-compound-equal.step"))
    source = _riser_facts(drawing.model().features)
    feature = next(item for item in drawing.model().features if item.kind == "step_level")
    damaged = replace(
        feature,
        shoulders=feature.shoulders[1:],
        shoulder_supports=feature.shoulder_supports[1:],
    )
    features = tuple(damaged if item is feature else item for item in drawing.model().features)

    outcomes = _riser_model_outcomes(source, features)
    assert outcomes.count("unknown") == 1
    assert outcomes.count("supported") == 5


def test_source_projection_is_independent_of_the_target_ir() -> None:
    from draftwright import build_drawing

    part = import_step(CORPUS.parent / "riser-compound-equal.step")
    drawing = build_drawing(part)
    baseline = _riser_source_facts(part, drawing.recognition())
    feature = next(item for item in drawing.model().features if item.kind == "step_level")
    erased = replace(feature, shoulders=(), shoulder_supports=())

    assert len(baseline) == 6
    assert _riser_source_facts(part, drawing.recognition()) == baseline
    assert _riser_model_outcomes(baseline, (erased,)) == ["unknown"] * 6


def test_body_support_corruption_and_extra_target_shoulders_fail_closed() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(import_step(CORPUS.parent / "riser-compound-equal.step"))
    source = _riser_facts(drawing.model().features)
    feature = next(item for item in drawing.model().features if item.kind == "step_level")
    relocated = tuple(
        replace(
            support,
            levels=tuple(replace(level, y_span=(999.0, 1000.0)) for level in support.levels),
        )
        for support in feature.shoulder_supports
    )
    extra = replace(feature, shoulders=(*feature.shoulders, ("x", 45.0)))

    assert (
        _riser_model_outcomes(source, (replace(feature, shoulder_supports=()),)) == ["unknown"] * 6
    )
    assert (
        _riser_model_outcomes(source, (replace(feature, shoulder_supports=relocated),))
        == ["unknown"] * 6
    )
    assert _riser_model_outcomes(source, (extra,)) == ["unknown"] * 6


def test_surplus_support_and_zero_source_target_content_fail_closed() -> None:
    from draftwright import build_drawing
    from draftwright.model.declare import step_level

    drawing = build_drawing(import_step(CORPUS.parent / "riser-compound-equal.step"))
    source = _riser_facts(drawing.model().features)
    feature = next(item for item in drawing.model().features if item.kind == "step_level")
    surplus = replace(feature.shoulder_supports[0], position=999.0)
    malformed = replace(feature, shoulder_supports=(*feature.shoulder_supports, surplus))

    assert _riser_model_outcomes(source, (malformed,)) == ["unknown"] * 6
    with pytest.raises(ValueError, match="no source occurrence"):
        _riser_model_outcomes([], (step_level(base=0, levels=(5,), shoulders=(("x", 7),)),))


def test_axis_station_datum_and_distance_are_all_correspondence_significant() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(_rebated_block())
    source = _riser_facts(drawing.model().features)
    feature = next(item for item in drawing.model().features if item.kind == "step_level")
    corruptions = (
        replace(feature, shoulders=(("y", feature.shoulders[0][1]),)),
        replace(feature, shoulders=(("x", feature.shoulders[0][1] + 1),)),
        replace(feature, datum=(feature.datum[0] - 1, *feature.datum[1:])),
    )

    assert all(
        _riser_model_outcomes(source, (corrupted,)) == ["unknown"] for corrupted in corruptions
    )


def test_drawing_credit_requires_the_exact_compiler_span() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(_rebated_block())
    source = _riser_facts(drawing.model().features)
    annotation = drawing.registry.named("dim_shoulder_x0")
    assert _riser_drawing_outcomes(source, drawing) == ["supported"]

    annotation._dw_measurement_span = ((0.0, 0.0, 0.0), (31.0, 0.0, 0.0))
    assert _riser_drawing_outcomes(source, drawing) == ["unsupported"]


def test_drawing_credit_consumes_body_local_compiler_occurrences() -> None:
    from draftwright import build_drawing

    part = import_step(CORPUS.parent / "riser-compound-equal.step")
    drawing = build_drawing(part)
    source = _riser_source_facts(part, drawing.recognition())
    for left, right in ((0, 1), (2, 3), (4, 5)):
        first = drawing.registry.named(f"dim_shoulder_x{left}")
        second = drawing.registry.named(f"dim_shoulder_x{right}")
        second._dw_measurement_span = first._dw_measurement_span

    outcomes = _riser_drawing_outcomes(source, drawing)
    assert outcomes.count("supported") == 3
    assert outcomes.count("unsupported") == 3


@pytest.mark.parametrize(
    ("fixture", "expected"),
    (
        ("riser-slanted-x.step", {24.0: 25.0, 32.0: 19.0, 40.0: 14.0}),
        ("riser-bounded-two-axis.step", {5.0: 15.0, 12.0: 15.0, 15.0: 25.0}),
    ),
)
def test_compiler_spans_use_each_shoulder_physical_witness_height(fixture, expected) -> None:
    from draftwright import build_drawing
    from draftwright.model.compiled import compile_dimensions

    drawing = build_drawing(import_step(CORPUS.parent / fixture))
    ladder = next(
        item
        for item in compile_dimensions(drawing.model()).ladders
        if item.kind == "step_position"
    )

    assert {
        rung.span[1]["xyz".index(rung.axis)]: rung.span[1][2] for rung in ladder.rungs
    } == expected


def test_malformed_public_riser_support_is_an_observed_unknown() -> None:
    from draftwright import build_drawing

    part = _rebated_block()
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    riser = recognition.risers[0]
    malformed = replace(recognition, risers=(replace(riser, body_levels=(object(),)),))

    with pytest.raises(ObservationError, match="riser source access failed|malformed"):
        _riser_source_facts(part, malformed)


@pytest.mark.parametrize(
    "fixture", ("plate-t-yz.step", "plate-u-cut.step", "plate-u-additive.step")
)
def test_plate_owned_riser_substrate_is_excluded_without_consulting_target_ir(fixture) -> None:
    from draftwright import build_drawing

    part = import_step(CORPUS.parent / fixture)
    drawing = build_drawing(part)

    assert drawing.recognition().risers
    assert _riser_source_facts(part, drawing.recognition()) == []
    assert _default_observers()["risers"](part) == ()


def test_zero_source_boundary_failure_cannot_pass_a_negative_case(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    part = import_step(CORPUS.parent / "riser-plain-negative.step")
    monkeypatch.setattr(
        step_analysis,
        "_riser_model_outcomes",
        lambda *_args: (_ for _ in ()).throw(ValueError("injected target")),
    )

    with pytest.raises(ObservationError, match="target content without a source"):
        _default_observers()["risers"](part)


def test_missing_per_occurrence_boundary_outcomes_fail_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_riser_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_observer_uses_one_build_owned_recognition_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", counted)
    assert _default_observers()["risers"](_rebated_block())
    assert calls == 1


def test_observer_fails_closed_when_build_or_recognition_is_unavailable(monkeypatch) -> None:
    import draftwright.builder as builder

    monkeypatch.setattr(
        builder,
        "build_drawing",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("probe")),
    )
    with pytest.raises(ObservationError, match="drawing build failed: probe"):
        _default_observers()["risers"](_rebated_block())


def test_observer_failure_cannot_pass_a_zero_riser_negative_case(monkeypatch) -> None:
    import draftwright.builder as builder

    corpus = load_corpus(CORPUS)
    negative = next(case for case in corpus.cases if not case.expected)
    monkeypatch.setattr(
        builder,
        "build_drawing",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("negative probe")),
    )

    damaged = evaluate_step_corpus(replace(corpus, cases=(negative,)))

    assert damaged.complete_cases == damaged.conformant_cases == 0
    assert damaged.cases[0].outcome == "unknown"
    assert [(issue.layer, issue.family) for issue in damaged.cases[0].diagnostics] == [
        ("analysis", "risers")
    ]


def test_removing_one_expected_shoulder_lowers_recall() -> None:
    corpus = load_corpus(CORPUS)
    positive = next(case for case in corpus.cases if case.case_id == "riser-slanted-x")
    observed = list(
        _default_observers()["risers"](import_step(CORPUS.parent / "riser-slanted-x.step"))
    )

    report = evaluate_step_corpus(
        replace(corpus, cases=(positive,)),
        observers={"risers": lambda _part: observed[1:]},
    )

    assert report.detection.recall == pytest.approx(2 / 3)
    assert report.complete_cases == report.conformant_cases == 0
