import importlib.machinery
import importlib.util
from copy import deepcopy
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "annotation-scheme-compare"


def _load_script():
    loader = importlib.machinery.SourceFileLoader("annotation_scheme_compare_script", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _result(annotations, *, quality_key=(0, 0, 0, 0, 0, -0.2, 124740.0)):
    return {
        "manifest": {
            "annotations": annotations,
            "interior_dimensions": [],
            "coverage": {"requirements": 2, "placed": 2, "missing": 0},
            "drops": {},
            "arrangement_quality": {"selection_key": quality_key},
        }
    }


def test_compare_treats_annotation_renumbering_as_semantic_parity():
    compare = _load_script()._compare
    first = {"hc_plan0": {"type": "Leader", "label": "A", "view": "plan", "region": None}}
    second = {"hc_plan1": deepcopy(first["hc_plan0"])}

    parity = compare(_result(first), _result(second))["parity"]

    assert parity["passed"] is True
    assert parity["missing"] == []
    assert parity["added"] == []


def test_compare_rejects_semantic_content_changes_and_interior_dimensions():
    compare = _load_script()._compare
    baseline = _result(
        {"dim_a": {"type": "LinearDimension", "label": "10", "view": "front", "region": None}}
    )
    candidate = _result(
        {
            "dim_b": {
                "type": "LinearDimension",
                "label": "11",
                "view": "front",
                "region": "interior",
            }
        }
    )
    candidate["manifest"]["interior_dimensions"] = ["dim_b"]

    parity = compare(baseline, candidate)["parity"]

    assert parity["passed"] is False
    assert [item["label"] for item in parity["missing"]] == ["10"]
    assert [item["label"] for item in parity["added"]] == ["11"]
    assert parity["candidate_interior_dimensions"] == ["dim_b"]


def test_parse_routes():
    parse = _load_script()._parse_routes

    assert parse("plan/below,side/right") == {("plan", "below"), ("side", "right")}


def test_compare_selects_better_quality_only_after_semantic_parity():
    compare = _load_script()._compare
    annotation = {
        "dim_a": {"type": "LinearDimension", "label": "10", "view": "front", "region": None}
    }

    result = compare(
        _result(annotation, quality_key=(0, 0, 0, 0, 2, -0.2, 124740.0)),
        _result(annotation, quality_key=(0, 0, 0, 0, 1, -0.2, 124740.0)),
    )

    assert result["quality_comparison"]["verdict"] == "candidate"


def test_compare_makes_semantically_changed_candidate_ineligible():
    compare = _load_script()._compare
    baseline = _result(
        {"dim_a": {"type": "Dimension", "label": "10", "view": "front", "region": None}},
        quality_key=(0, 0, 1, 1, 1, -0.1, 249480.0),
    )
    candidate = _result(
        {"dim_b": {"type": "Dimension", "label": "11", "view": "front", "region": None}},
        quality_key=(0, 0, 0, 0, 0, -1.0, 62370.0),
    )

    assert compare(baseline, candidate)["quality_comparison"]["verdict"] == "ineligible"


def test_candidate_build_failure_is_a_machine_readable_ineligible_result():
    failed = _load_script()._failed_candidate_comparison(_result({}), "iso does not fit")

    assert failed["parity"]["reason"] == "candidate_build_failed"
    assert failed["quality_comparison"]["verdict"] == "ineligible"
    assert failed["quality_comparison"]["candidate_key"] is None
    assert failed["candidate"]["error"] == "iso does not fit"
