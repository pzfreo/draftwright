"""The corpus reducer chooses a true minimum without dropping named regressions."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from _corpus_cover import (
    CoverageSignature,
    case_coverage_signature,
    corpus_subset,
    minimum_coverage_cases,
)


def test_signature_records_every_evidence_dimension_without_cross_category_collisions():
    signature = CoverageSignature(
        recognizer_families=frozenset({"groove"}),
        topology_variants=frozenset({"axial"}),
        requirement_outcomes=frozenset({"complete"}),
        compiler_paths=frozenset({"leader"}),
        lint_codes=frozenset({"clean"}),
        mutation_kills=frozenset({"drop-width"}),
    )

    assert signature.tokens() == {
        "recognizer_families:groove",
        "topology_variants:axial",
        "requirement_outcomes:complete",
        "compiler_paths:leader",
        "lint_codes:clean",
        "mutation_kills:drop-width",
    }


def test_same_value_in_two_dimensions_remains_two_coverage_obligations():
    signature = CoverageSignature(
        compiler_paths=frozenset({"leader"}),
        mutation_kills=frozenset({"leader"}),
    )

    assert signature.tokens() == {
        "compiler_paths:leader",
        "mutation_kills:leader",
    }


def test_case_signature_combines_oracle_and_explicit_runtime_evidence():
    case = SimpleNamespace(
        classification="positive+rotated",
        expected_outcome="supported",
        expected=(
            SimpleNamespace(
                family="grooves",
                identity={"axis": SimpleNamespace(value="x")},
                parameters={"width": object(), "diameter": object()},
                required_downstream=("ir_adapter", "drawing_consumer"),
            ),
        ),
    )

    signature = case_coverage_signature(
        case,
        scope=("grooves",),
        topology_variants=("axis:x",),
        lint_codes=("clean",),
        mutation_kills=("delete-provider-groove",),
    )

    assert signature.recognizer_families == {"grooves"}
    assert signature.topology_variants == {
        "classification:positive",
        "classification:rotated",
        "declared:axis:x",
        "fact-cardinality:1",
        "grooves:identity-fields:axis",
    }
    assert signature.requirement_outcomes == {
        "case:supported",
        "grooves:ir_adapter:supported",
        "grooves:drawing_consumer:supported",
    }
    assert signature.compiler_paths == {"grooves:width", "grooves:diameter"}
    assert signature.lint_codes == {"clean"}
    assert signature.mutation_kills == {"delete-provider-groove"}


def test_case_signature_requires_lint_and_mutation_evidence():
    case = SimpleNamespace(classification="negative", expected_outcome="supported", expected=())

    with pytest.raises(TypeError):
        case_coverage_signature(case, scope=("grooves",))


def test_case_signature_can_limit_classifications_to_a_specific_mutation_campaign():
    case = SimpleNamespace(
        classification="compound+positive", expected_outcome="supported", expected=()
    )

    signature = case_coverage_signature(
        case,
        scope=("pocket-patterns",),
        topology_variants=("kind:grid",),
        include_classifications=False,
        lint_codes=("clean",),
        mutation_kills=("grid-pitch",),
    )

    assert "classification:compound" not in signature.tokens()
    assert "topology_variants:declared:kind:grid" in signature.tokens()


def test_corpus_subset_preserves_source_order_and_refuses_unknown_cases():
    @dataclass(frozen=True)
    class Corpus:
        cases: tuple

    cases = tuple(SimpleNamespace(case_id=name) for name in ("b", "a", "c"))
    corpus = Corpus(cases)

    assert [case.case_id for case in corpus_subset(corpus, ("c", "b")).cases] == ["b", "c"]
    with pytest.raises(KeyError, match="missing"):
        corpus_subset(corpus, ("missing",))


def test_exact_cover_avoids_the_greedy_largest_case_trap():
    signatures = {
        "broad": frozenset({"a", "b", "c", "d"}),
        "left": frozenset({"a", "b", "e"}),
        "right": frozenset({"c", "d", "f"}),
        "tail_e": frozenset({"e"}),
        "tail_f": frozenset({"f"}),
    }

    assert minimum_coverage_cases(signatures) == ("left", "right")


def test_equal_minima_use_case_id_order_independent_of_mapping_order():
    forward = {
        "z_case": frozenset({"only"}),
        "a_case": frozenset({"only"}),
    }

    assert minimum_coverage_cases(forward) == ("a_case",)
    assert minimum_coverage_cases(dict(reversed(tuple(forward.items())))) == ("a_case",)


def test_required_named_regression_survives_equivalent_coverage():
    signatures = {
        "ordinary": frozenset({"recognizer:groove", "topology:axial"}),
        "regression-1372": frozenset({"recognizer:groove", "topology:axial"}),
    }

    assert minimum_coverage_cases(signatures, required=("regression-1372",)) == (
        "regression-1372",
    )


def test_unknown_required_case_is_refused():
    try:
        minimum_coverage_cases({"known": frozenset({"x"})}, required=("missing",))
    except KeyError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("an absent named regression was silently ignored")
