import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "annotation-scheme-compare"


def _load_script():
    loader = importlib.machinery.SourceFileLoader("annotation_scheme_compare_script", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _result(
    annotations,
    *,
    quality_key=(0, 0, 0, 0, 0, -0.2, 124740.0),
    blockers=(),
    coverage=None,
):
    return {
        "manifest": {
            "annotations": annotations,
            "interior_dimensions": [],
            "coverage": coverage
            or {
                "requirements": 2,
                "placed": 2,
                "missing": 0,
                "unsupported": 0,
                "unverifiable": 0,
            },
            "drops": {},
            "arrangement_quality": {"selection_key": quality_key},
            "blocker_identities": list(blockers),
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


def test_compare_treats_an_equivalent_projection_as_semantic_parity():
    compare = _load_script()._compare
    front = {"dim": {"type": "Dimension", "label": "800", "view": "front", "region": None}}
    plan = {"dim": {"type": "Dimension", "label": "800", "view": "plan", "region": None}}

    assert compare(_result(front), _result(plan))["parity"]["passed"] is True


def test_compare_treats_leader_routing_as_layout_not_semantics():
    compare = _load_script()._compare
    common = {
        "label": "⌀35 THRU",
        "view": "plan",
        "region": "exterior",
        "owners": [{"feature_index": 3, "kind": "hole", "source_id": ""}],
        "measurements": [],
        "satisfactions": [],
    }

    baseline = {"hole": {**common, "type": "RoutedLeader"}}
    candidate = {"hole": {**common, "type": "Leader"}}

    parity = compare(_result(baseline), _result(candidate))["parity"]

    assert parity["passed"] is True
    assert parity["missing"] == []
    assert parity["added"] == []


def test_compare_rejects_same_text_attached_to_a_different_feature():
    compare = _load_script()._compare
    common = {
        "type": "Leader",
        "label": "2x ø35 THRU",
        "view": "front",
        "region": None,
        "measurements": [],
        "satisfactions": [],
    }
    baseline = {
        "holes": {
            **common,
            "owners": [{"feature_index": 3, "kind": "pattern", "source_id": ""}],
        }
    }
    candidate = {
        "holes": {
            **common,
            "owners": [{"feature_index": 4, "kind": "hole", "source_id": ""}],
        }
    }

    parity = compare(_result(baseline), _result(candidate))["parity"]

    assert parity["passed"] is False
    assert parity["missing"][0]["owners"][0]["feature_index"] == 3
    assert parity["added"][0]["owners"][0]["feature_index"] == 4


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
    assert parse("none") == set()


def test_uncapped_trial_can_restore_semantics_and_win():
    script = _load_script()
    annotation = {
        "dim": {"type": "Dimension", "label": "10", "view": "front", "region": "exterior"}
    }
    baseline = _result(annotation, quality_key=(2, 0, 2, 0, 0, -0.2, 124740.0))
    capped = script._compare(baseline, _result({}, quality_key=(0, 0, 0, 0, 0, -0.2, 124740.0)))
    uncapped = script._compare(
        baseline, _result(annotation, quality_key=(1, 0, 1, 0, 0, -0.2, 124740.0))
    )

    assert capped["quality_comparison"]["verdict"] == "ineligible"
    assert script._prefer_candidate(capped, uncapped) is uncapped
    assert script._trial_summary("legacy-depth", "none", uncapped)["semantic_parity"]


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


def test_candidate_may_remove_but_not_introduce_requirement_blockers():
    compare = _load_script()._compare

    improved = compare(_result({}, blockers=("a", "b")), _result({}, blockers=("a",)))
    regressed = compare(_result({}, blockers=("a",)), _result({}, blockers=("a", "b")))

    assert improved["parity"]["passed"] is True
    assert regressed["parity"]["passed"] is False
    assert regressed["parity"]["introduced_blockers"] == ["b"]


def test_candidate_may_restore_additional_approved_annotations():
    compare = _load_script()._compare
    baseline = _result({})
    candidate = _result(
        {"dim": {"type": "Dimension", "label": "450", "view": "side", "region": None}}
    )

    result = compare(baseline, candidate)

    assert result["parity"]["passed"] is True
    assert result["parity"]["added"] == [{"type": "Dimension", "label": "450"}]


@pytest.mark.scheduled
def test_issue915_clear_hole_route_preserves_every_annotation(tmp_path):
    source = Path(__file__).parent / "fixtures" / "issue_915_case_study_2.step"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--output",
            str(tmp_path / "issue915"),
            "--page",
            "A2",
            "--scale",
            "0.5",
            "--title",
            "issue915",
            "--number",
            "issue915",
            "--formats",
            "svg",
            "--arrangement",
            "staggered-side",
            "--exterior-dimensions",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout[-2000:]
    comparison = json.loads(completed.stdout)
    assert comparison["quality_comparison"]["verdict"] == "candidate"
    assert comparison["parity"]["passed"]
    assert comparison["parity"]["missing"] == []
    assert comparison["parity"]["introduced_blockers"] == []
    assert comparison["quality_comparison"]["baseline_key"][:5] == [0, 0, 0, 0, 1]
    assert comparison["quality_comparison"]["candidate_key"][:5] == [0, 0, 0, 0, 0]
    assert comparison["baseline"]["page"] == comparison["candidate"]["page"]
    assert comparison["baseline"]["scale"] == comparison["candidate"]["scale"]


def test_issue915_candidate_routes_a_hole_on_the_fixed_sheet(monkeypatch, capsys, tmp_path):
    from draftwright import analysis
    from draftwright.annotations import holes

    script = _load_script()
    original_measure_strips = analysis._measure_strips
    original_fallback = holes._sheet_leader_fallback
    successful_routes = []

    def observe_fallback(*args):
        annotation = original_fallback(*args)
        if annotation is not None:
            successful_routes.append(annotation)
        return annotation

    monkeypatch.setattr(analysis, "_measure_strips", original_measure_strips)
    monkeypatch.setattr(holes, "_sheet_leader_fallback", observe_fallback)
    monkeypatch.setenv("DRAFTWRIGHT_EXPERIMENTAL_CROSSING_RECOVERY", "1")
    monkeypatch.setenv("DRAFTWRIGHT_EXPERIMENTAL_ARRANGEMENT", "staggered-side")
    monkeypatch.setenv("DRAFTWRIGHT_EXPERIMENTAL_EXTERIOR_DIMENSIONS", "1")
    args = SimpleNamespace(
        mode="candidate",
        source=Path(__file__).parent / "fixtures" / "issue_915_case_study_2.step",
        output=tmp_path / "candidate",
        page="A2",
        scale=0.5,
        title="issue915",
        number="issue915",
        formats="svg",
        candidate_routes="none",
        arrangement="staggered-side",
        exterior_dimensions=True,
    )
    assert script._worker(args) == 0
    candidate = json.loads(capsys.readouterr().out)
    assert successful_routes
    assert candidate["manifest"]["coverage"]["missing"] == 0
    assert candidate["manifest"]["arrangement_quality"]["crossings"] == 0
