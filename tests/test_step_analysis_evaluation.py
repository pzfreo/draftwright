"""Independent STEP-analysis evaluation cannot validate recognition with itself (#1169)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from draftwright.evaluation.step_analysis import (
    BenchmarkCase,
    CorpusError,
    ExpectedFact,
    ObservedFact,
    ParameterExpectation,
    evaluate_case,
    evaluate_corpus,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-v1.json"


def _hole(*, diameter: float = 10.0) -> ObservedFact:
    return ObservedFact(
        family="holes",
        identity={"axis": (0.0, 0.0, 1.0), "centre": (20.0, 10.0, 0.0)},
        parameters={"diameter": diameter, "depth": 8.0},
        downstream={
            "ir_adapter": "supported",
            "dsl_declaration": "supported",
            "generated_code": "supported",
            "drawing_consumer": "supported",
        },
    )


def _case() -> BenchmarkCase:
    return BenchmarkCase(
        case_id="blind-hole",
        classification="positive",
        expected=(
            ExpectedFact(
                family="holes",
                identity={
                    "axis": ParameterExpectation((0.0, 0.0, 1.0), absolute_tolerance=1e-9),
                    "centre": ParameterExpectation((20.0, 10.0, 0.0), absolute_tolerance=1e-6),
                },
                parameters={
                    "diameter": ParameterExpectation(10.0, absolute_tolerance=1e-6),
                    "depth": ParameterExpectation(8.0, absolute_tolerance=1e-6),
                },
                required_downstream=(
                    "ir_adapter",
                    "dsl_declaration",
                    "generated_code",
                    "drawing_consumer",
                ),
            ),
        ),
        provenance={
            "fixture": "tests/fixtures/evaluation/blind-hole.step",
            "sha256": "0" * 64,
            "license": "CC0-1.0",
            "expected_facts_author": "hand-authored from the construction drawing",
        },
    )


def test_deleting_the_only_recognised_fact_reduces_recall() -> None:
    complete = evaluate_case(_case(), observations=(_hole(),))
    deleted = evaluate_case(_case(), observations=())

    assert complete.detection.recall == 1.0
    assert deleted.detection.recall == 0.0
    assert deleted.detection.missed == 1
    assert deleted.parameter_fidelity.score is None
    assert deleted.downstream_usefulness.score is None


def test_a_false_positive_is_visible_on_an_independent_negative_case() -> None:
    negative = BenchmarkCase(
        case_id="plain-block",
        classification="negative",
        expected=(),
        provenance={
            "fixture": "tests/fixtures/evaluation/plain-block.step",
            "sha256": "1" * 64,
            "license": "CC0-1.0",
            "expected_facts_author": "hand-authored from the construction drawing",
        },
    )

    result = evaluate_case(negative, observations=(_hole(),))

    assert result.detection.false_positives == 1
    assert result.detection.false_positive_rate == 1.0
    assert result.diagnostics[0].layer == "detection"


def test_parameter_credit_is_per_fact_not_tolerance_interpolation() -> None:
    result = evaluate_case(_case(), observations=(_hole(diameter=10.000002),))

    assert result.detection.recall == 1.0
    assert result.parameter_fidelity.passed == 1
    assert result.parameter_fidelity.total == 2
    assert result.parameter_fidelity.score == 0.5
    assert any(
        diagnostic.layer == "parameter_fidelity"
        and diagnostic.parameter == "diameter"
        and diagnostic.expected == 10.0
        for diagnostic in result.diagnostics
    )


def test_downstream_failure_is_reported_without_changing_detection_or_fidelity() -> None:
    observed = _hole()
    weakened = ObservedFact(
        family=observed.family,
        identity=observed.identity,
        parameters=observed.parameters,
        downstream={**observed.downstream, "generated_code": "deferred"},
    )

    result = evaluate_case(_case(), observations=(weakened,))

    assert result.detection.recall == result.parameter_fidelity.score == 1.0
    assert result.downstream_usefulness.score == 0.75
    assert any(
        diagnostic.layer == "downstream_usefulness"
        and diagnostic.parameter == "generated_code"
        and diagnostic.observed == "deferred"
        for diagnostic in result.diagnostics
    )


def test_unknown_and_unsupported_outcomes_never_count_as_complete() -> None:
    for outcome in ("unknown", "unsupported"):
        result = evaluate_case(_case(), observations=(), outcome=outcome)
        assert result.detection.recall == 0.0
        assert result.outcome == outcome
        assert result.conformant is False
        assert result.complete is False


def test_an_ambiguous_case_can_expect_unknown_without_claiming_completeness() -> None:
    ambiguous = BenchmarkCase(
        case_id="interrupted-near-cylinder",
        classification="ambiguous",
        expected=(),
        expected_outcome="unknown",
        provenance=_case().provenance,
    )

    result = evaluate_case(ambiguous, observations=(), outcome="unknown")

    assert result.conformant is True
    assert result.complete is False


def test_results_do_not_depend_on_observation_or_expected_fact_order() -> None:
    second_expected = ExpectedFact(
        family="holes",
        identity={
            "axis": ParameterExpectation((0.0, 0.0, 1.0), absolute_tolerance=1e-9),
            "centre": ParameterExpectation((-20.0, 10.0, 0.0), absolute_tolerance=1e-6),
        },
        parameters={"diameter": ParameterExpectation(6.0, absolute_tolerance=1e-6)},
        required_downstream=("ir_adapter",),
    )
    case = BenchmarkCase(
        case_id="compound-two-holes",
        classification="compound",
        expected=(_case().expected[0], second_expected),
        provenance=_case().provenance,
    )
    second_observed = ObservedFact(
        family="holes",
        identity={"axis": (0.0, 0.0, 1.0), "centre": (-20.0, 10.0, 0.0)},
        parameters={"diameter": 6.0},
        downstream={"ir_adapter": "supported"},
    )

    forward = evaluate_case(case, observations=(_hole(), second_observed))
    reversed_result = evaluate_case(
        BenchmarkCase(
            case_id=case.case_id,
            classification=case.classification,
            expected=tuple(reversed(case.expected)),
            provenance=case.provenance,
        ),
        observations=(second_observed, _hole()),
    )

    assert forward == reversed_result


def test_matching_counts_repeated_equal_instances_by_position_not_object_identity() -> None:
    expected = _case().expected[0]
    observed = _hole()
    repeated = BenchmarkCase(
        case_id="synthetic-repeated-instances",
        classification="synthetic",
        expected=(expected, expected),
        provenance=_case().provenance,
    )

    result = evaluate_case(repeated, observations=(observed, observed))

    assert result.detection.matched == 2
    assert result.detection.missed == result.detection.false_positives == 0


def test_corpus_aggregation_keeps_layers_and_denominators_separate() -> None:
    complete = evaluate_case(_case(), observations=(_hole(),))
    missed = evaluate_case(_case(), observations=())

    corpus = evaluate_corpus((complete, missed))

    assert corpus.detection.recall == 0.5
    assert corpus.detection.matched == 1
    assert corpus.detection.missed == 1
    assert corpus.parameter_fidelity.score == 1.0
    assert corpus.parameter_fidelity.total == 2
    assert corpus.downstream_usefulness.score == 1.0
    assert corpus.downstream_usefulness.total == 4
    assert corpus.conformant_cases == 1
    assert corpus.complete_cases == 1
    assert not hasattr(corpus, "score"), "the three evidence layers must not be composited"


def test_versioned_corpus_has_independent_provenance_and_required_case_classes() -> None:
    corpus = load_corpus(CORPUS)

    assert corpus.corpus_version == "1.0.0"
    assert corpus.metric_version == 1
    assert corpus.scope == ("holes",)
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {"positive", "negative", "ambiguous", "compound", "topology-order-variant"} <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)
    assert all(len(case.provenance["sha256"]) == 64 for case in corpus.cases)


def test_real_step_corpus_scores_all_layers_and_topology_variants_deterministically() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert (first.corpus_version, first.metric_version) == ("1.0.0", 1)
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.parameter_fidelity.score == 1.0
    assert first.downstream_usefulness.score == 1.0
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_real_corpus_denominator_does_not_shrink_when_the_observer_is_deleted() -> None:
    corpus = load_corpus(CORPUS)

    damaged = evaluate_step_corpus(corpus, observers={"holes": lambda _part: ()})

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 5
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(corpus.cases)


@pytest.mark.parametrize(
    "mutation",
    [
        "not-object",
        "unknown-field",
        "bad-format",
        "bad-semver",
        "bad-metric",
        "scope-not-array",
        "empty-scope",
        "empty-cases",
        "duplicate-tags",
        "empty-tags",
        "missing-fixture",
        "bad-hash-syntax",
        "wrong-hash",
        "bad-outcome",
        "facts-not-array",
        "facts-with-unknown",
        "family-outside-scope",
        "empty-identity",
        "unknown-boundary",
        "empty-vector",
        "nonfinite-vector",
        "nonfinite-value",
        "unsupported-value",
        "negative-tolerance",
        "nonfinite-tolerance",
        "string-tolerance",
        "overlapping-identities",
        "duplicate-case-id",
    ],
)
def test_corpus_validation_fails_closed(tmp_path: Path, mutation: str) -> None:
    root = tmp_path / "evaluation"
    shutil.copytree(CORPUS.parent, root)
    corpus_path = root / CORPUS.name
    raw = json.loads(corpus_path.read_text(encoding="utf-8"))
    if mutation == "not-object":
        raw = []
    elif mutation == "unknown-field":
        raw["derived_from_recogniser"] = True
    elif mutation == "bad-format":
        raw["format_version"] = 2
    elif mutation == "bad-semver":
        raw["corpus_version"] = "v1"
    elif mutation == "bad-metric":
        raw["metric_version"] = 2
    elif mutation == "scope-not-array":
        raw["scope"] = "holes"
    elif mutation == "empty-scope":
        raw["scope"] = []
    elif mutation == "empty-cases":
        raw["cases"] = []
    elif mutation == "duplicate-tags":
        raw["cases"][0]["tags"] = ["negative", "negative"]
    elif mutation == "empty-tags":
        raw["cases"][0]["tags"] = []
    elif mutation == "missing-fixture":
        raw["cases"][0]["fixture"] = "missing.step"
    elif mutation == "bad-hash-syntax":
        raw["cases"][0]["sha256"] = "not-a-hash"
    elif mutation == "wrong-hash":
        raw["cases"][0]["sha256"] = "0" * 64
    elif mutation == "bad-outcome":
        raw["cases"][0]["expected_outcome"] = "maybe"
    elif mutation == "facts-not-array":
        raw["cases"][0]["facts"] = {}
    elif mutation == "facts-with-unknown":
        raw["cases"][1]["expected_outcome"] = "unknown"
    elif mutation == "family-outside-scope":
        raw["cases"][1]["facts"][0]["family"] = "slots"
    elif mutation == "empty-identity":
        raw["cases"][1]["facts"][0]["identity"] = {}
    elif mutation == "unknown-boundary":
        raw["cases"][1]["facts"][0]["required_downstream"].append("teleport")
    elif mutation == "empty-vector":
        raw["cases"][1]["facts"][0]["identity"]["axis"]["value"] = []
    elif mutation == "nonfinite-vector":
        raw["cases"][1]["facts"][0]["identity"]["axis"]["value"] = [0.0, float("nan")]
    elif mutation == "nonfinite-value":
        raw["cases"][1]["facts"][0]["parameters"]["diameter"]["value"] = float("inf")
    elif mutation == "unsupported-value":
        raw["cases"][1]["facts"][0]["parameters"]["bottom"]["value"] = {}
    elif mutation == "negative-tolerance":
        raw["cases"][1]["facts"][0]["parameters"]["diameter"]["absolute_tolerance"] = -1
    elif mutation == "nonfinite-tolerance":
        raw["cases"][1]["facts"][0]["parameters"]["diameter"]["absolute_tolerance"] = float("inf")
    elif mutation == "string-tolerance":
        raw["cases"][1]["facts"][0]["parameters"]["bottom"]["absolute_tolerance"] = 1
    elif mutation == "overlapping-identities":
        raw["cases"][3]["facts"][1]["identity"] = raw["cases"][3]["facts"][0]["identity"]
    else:
        raw["cases"][1]["id"] = raw["cases"][0]["id"]
    corpus_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(CorpusError):
        load_corpus(corpus_path)


def test_corpus_read_errors_are_wrapped(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(CorpusError, match="cannot read corpus"):
        load_corpus(missing)

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(CorpusError, match="cannot read corpus"):
        load_corpus(malformed)


def test_runtime_input_contracts_reject_contradictory_outcomes_and_observer_sets() -> None:
    with pytest.raises(ValueError, match="invalid analysis outcome"):
        evaluate_case(_case(), observations=(), outcome="maybe")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cannot also claim"):
        evaluate_case(_case(), observations=(_hole(),), outcome="unknown")

    corpus = load_corpus(CORPUS)
    with pytest.raises(CorpusError, match="exactly cover"):
        evaluate_step_corpus(corpus, observers={})
    with pytest.raises(CorpusError, match="unknown corpus cases"):
        evaluate_step_corpus(
            corpus,
            observers={"holes": lambda _part: ()},
            outcomes={"not-a-case": "unknown"},
        )
