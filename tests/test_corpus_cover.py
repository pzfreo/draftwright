"""The corpus reducer chooses a true minimum without dropping named regressions."""

from _corpus_cover import CoverageSignature, minimum_coverage_cases


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
