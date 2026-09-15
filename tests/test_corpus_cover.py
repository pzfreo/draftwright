"""The corpus reducer chooses a true minimum without dropping named regressions."""

from _corpus_cover import minimum_coverage_cases


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
