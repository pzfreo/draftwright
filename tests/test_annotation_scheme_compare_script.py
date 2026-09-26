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


def test_compare_ignores_compiler_facts_owner_but_keeps_real_feature_owners():
    compare = _load_script()._compare
    flat = {"feature_index": 4, "kind": "flat", "source_id": ""}
    compiler_facts = {"feature_index": None, "kind": "FeatureFacts", "source_id": ""}
    before = {"flat": {"type": "Leader", "label": "6 A/F", "owners": [flat, compiler_facts]}}
    after = {"flat": {"type": "Leader", "label": "6 A/F", "owners": [flat]}}

    assert compare(_result(before), _result(after))["parity"]["passed"]
    assert not compare(_result(after), _result({"flat": {**after["flat"], "owners": []}}))[
        "parity"
    ]["passed"]


def test_compare_rejects_a_sheet_or_scale_change():
    compare = _load_script()._compare
    baseline = _result({})
    candidate = _result({})
    baseline["page"], baseline["scale"] = [420.0, 297.0], 2.0
    candidate["page"], candidate["scale"] = [594.0, 420.0], 2.0

    assert not compare(baseline, candidate)["parity"]["passed"]
    candidate["page"] = baseline["page"]
    candidate["scale"] = 1.0
    assert not compare(baseline, candidate)["parity"]["passed"]


def test_compare_counts_a_substantial_iso_gain_on_the_same_fixed_sheet():
    compare = _load_script()._compare
    baseline = _result({})
    candidate = _result({})
    for result in (baseline, candidate):
        result["page"], result["scale"] = [420.0, 297.0], 1.0
        result["manifest"]["orthographic_bounds"] = {"front": [20.0, 20.0, 80.0, 80.0]}
    baseline["manifest"]["iso_bounds"] = [100.0, 100.0, 200.0, 200.0]
    candidate["manifest"]["iso_bounds"] = [100.0, 100.0, 115.0, 115.0]
    assert compare(baseline, candidate)["quality_comparison"]["verdict"] == "tie"

    candidate["manifest"]["iso_bounds"] = [100.0, 100.0, 220.0, 200.0]
    quality = compare(baseline, candidate)["quality_comparison"]
    assert quality["verdict"] == "candidate"
    assert quality["reason"] == "larger_iso"
    candidate["manifest"]["orthographic_bounds"]["front"][0] = 21.0
    assert compare(baseline, candidate)["quality_comparison"]["verdict"] == "tie"


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
    assert [item["label"] for item in parity["introduced_interior_dimensions"]] == ["11"]


def test_compare_keeps_an_existing_interior_dimension_without_penalty():
    compare = _load_script()._compare
    annotation = {"type": "LinearDimension", "label": "12", "view": "plan", "region": "interior"}
    baseline = _result({"base_name": annotation}, quality_key=(2, 1, 2, 1, 0, -0.2, 124740.0))
    candidate = _result({"new_name": annotation}, quality_key=(1, 1, 1, 1, 0, -0.2, 124740.0))
    baseline["manifest"]["interior_dimensions"] = ["base_name"]
    candidate["manifest"]["interior_dimensions"] = ["new_name"]

    result = compare(baseline, candidate)
    assert result["parity"]["passed"]
    assert result["parity"]["introduced_interior_dimensions"] == []
    assert result["quality_comparison"]["verdict"] == "candidate"


def test_compare_allows_nts_caption_to_leave_only_for_a_to_scale_iso():
    compare = _load_script()._compare
    baseline = _result({"note_iso_nts": {"type": "Note", "label": "ISO VIEW (NTS)"}})
    candidate = _result({})
    candidate["views"] = ["front", "plan", "side", "iso"]
    candidate["manifest"]["iso_to_sheet_scale"] = False

    assert not compare(baseline, candidate)["parity"]["passed"]
    candidate["manifest"]["iso_to_sheet_scale"] = True
    assert compare(baseline, candidate)["parity"]["passed"]
    assert compare(baseline, candidate)["parity"]["obsolete_nts_caption_removed"]
    candidate["views"] = ["front", "plan", "side"]
    assert not compare(baseline, candidate)["parity"]["passed"]


def test_parse_routes():
    parse = _load_script()._parse_routes

    assert parse("plan/below,side/right") == {("plan", "below"), ("side", "right")}
    assert parse("none") == set()


def test_isolated_worker_emits_cost_and_machine_evidence(monkeypatch, capsys, tmp_path):
    import draftwright

    script = _load_script()
    drawing = SimpleNamespace(
        page_w=297.0,
        page_h=210.0,
        scale=1.0,
        views={"front": object()},
        annotation_scheme_decision={},
        export=lambda *_args, **_kwargs: {"svg": tmp_path / "part.svg"},
    )
    monkeypatch.setattr(draftwright, "build_drawing", lambda *_args, **_kwargs: drawing)
    monkeypatch.setattr(script, "_manifest", lambda _drawing: {})
    args = SimpleNamespace(
        mode="baseline",
        source=tmp_path / "part.step",
        output=tmp_path / "part",
        page="A4",
        scale=1.0,
        title="part",
        number="part",
        formats="svg",
    )

    assert script._worker(args) == 0
    cost = json.loads(capsys.readouterr().out)["cost"]
    assert 0 <= cost["build_seconds"] <= cost["worker_seconds"]
    assert 0 <= cost["export_seconds"] <= cost["worker_seconds"]
    assert cost["machine"]["logical_cpus"] is not None
    assert "physical_cores" in cost["machine"]
    assert cost["machine"]["python"]


def test_windows_peak_working_set_is_reported_without_resource(monkeypatch):
    script = _load_script()
    monkeypatch.setattr(script, "resource", None)
    monkeypatch.setattr(script.sys, "platform", "win32")
    monkeypatch.setattr(
        script,
        "psutil",
        SimpleNamespace(
            Process=lambda: SimpleNamespace(
                memory_info=lambda: SimpleNamespace(peak_wset=256 * 1024**2)
            ),
            virtual_memory=lambda: SimpleNamespace(total=8 * 1024**3),
            cpu_count=lambda logical: 4 if not logical else 8,
        ),
    )

    assert script._peak_rss_mib() == 256.0
    assert script._peak_rss_source() == "psutil.peak_wset"
    assert script._machine()["physical_ram_mib"] == 8192.0
    assert script._machine()["physical_cores"] == 4


def test_failed_candidate_retains_isolated_process_latency():
    result = _load_script()._failed_candidate_comparison(
        _result({}), "cannot fit", mode="candidate-preview", process_seconds=12.5
    )

    assert result["candidate"]["cost"]["process_seconds"] == 12.5
    assert result["parity"]["passed"] is False


def test_isolated_worker_timeout_is_a_typed_failure(monkeypatch, tmp_path):
    script = _load_script()
    args = SimpleNamespace(
        source=tmp_path / "part.step",
        page="A4",
        scale=1.0,
        title="part",
        number="part",
        formats="svg",
        candidate_routes="none",
        arrangement=None,
        exterior_dimensions=False,
        iso_growth=False,
        worker_timeout_seconds=0.25,
    )

    def timeout(_command, **kwargs):
        assert kwargs["timeout"] == 0.25
        raise subprocess.TimeoutExpired("worker", 0.25)

    monkeypatch.setattr(script.subprocess, "run", timeout)
    with pytest.raises(script.WorkerBuildError, match="time bound") as caught:
        script._run_worker(args, "baseline", tmp_path / "baseline")
    assert caught.value.timed_out
    assert caught.value.process_seconds >= 0


@pytest.mark.parametrize(
    "pair_mode,unattempted_mode",
    [("candidate-preview", "candidate-preview"), ("baseline", "candidate")],
)
def test_baseline_timeout_is_ineligible_and_never_attempts_candidate(
    monkeypatch, capsys, tmp_path, pair_mode, unattempted_mode
):
    script = _load_script()
    modes = []

    def worker(_args, mode, _output):
        modes.append(mode)
        raise script.WorkerBuildError("baseline timed out", 600.0, timed_out=True)

    monkeypatch.setattr(script, "_run_worker", worker)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare",
            "--source",
            str(tmp_path / "part.step"),
            "--output",
            str(tmp_path / "out"),
            "--mode",
            pair_mode,
            "--worker-timeout-seconds",
            "600",
        ],
    )

    assert script.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert modes == ["baseline"]
    assert result["baseline"]["error_kind"] == "timeout"
    assert result["baseline"]["cost"]["process_seconds"] == 600.0
    assert result["candidate"]["error"] == "not_attempted_after_baseline_failure"
    assert result["candidate"]["mode"] == unattempted_mode
    assert result["quality_comparison"]["verdict"] == "ineligible"
    assert result["parity"]["reason"] == "baseline_build_failed"


def test_candidate_timeout_is_ineligible_even_with_a_good_baseline():
    script = _load_script()
    result = script._failed_candidate_comparison(
        _result({}),
        "candidate timed out",
        mode="candidate-preview",
        process_seconds=60.0,
        timed_out=True,
    )

    assert result["baseline"]["manifest"]
    assert result["candidate"]["error_kind"] == "timeout"
    assert result["candidate"]["cost"]["process_seconds"] == 60.0
    assert not result["parity"]["passed"]


def test_candidate_preview_comparison_uses_one_candidate_trial(monkeypatch, capsys, tmp_path):
    script = _load_script()
    modes = []
    baseline = _result({}, quality_key=(1, 0, 1, 0, 0, -0.2, 124740.0))
    candidate = _result({}, quality_key=(0, 0, 0, 0, 0, -0.2, 124740.0))

    def worker(_args, mode, _output):
        modes.append(mode)
        return baseline if mode == "baseline" else candidate

    monkeypatch.setattr(script, "_run_worker", worker)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare",
            "--source",
            str(tmp_path / "part.step"),
            "--output",
            str(tmp_path / "out"),
            "--mode",
            "candidate-preview",
        ],
    )

    assert script.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert modes == ["baseline", "candidate-preview"]
    assert result["selected_candidate_trial"] is None
    assert len(result["candidate_trials"]) == 1


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


def test_issue915_candidate_covers_hole_routing_on_the_fixed_sheet(monkeypatch, capsys, tmp_path):
    from draftwright import analysis

    script = _load_script()
    original_measure_strips = analysis._measure_strips

    monkeypatch.setattr(analysis, "_measure_strips", original_measure_strips)
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
    assert sum(name.startswith("hc_") for name in candidate["manifest"]["annotations"]) == 5
    assert candidate["manifest"]["coverage"]["missing"] == 0
    assert candidate["manifest"]["arrangement_quality"]["crossings"] == 0


@pytest.mark.scheduled
def test_ctc05_public_selector_preserves_routed_hole_claims_on_a2():
    from draftwright import build_drawing

    drawing = build_drawing(
        Path(__file__).parent / "fixtures" / "nist_ctc_05_asme1_ap242.stp",
        page="A2",
        scale=0.2,
        scale_policy="permissive",
        title="ctc05-a2-1to5",
        number="ctc05-a2-1to5",
        pmi="annotate",
        _views=("front", "plan", "side"),
        _include_iso=True,
        annotation_layout="best",
    )

    assert drawing.annotation_scheme_decision["status"] == "candidate"
    labels = [
        str(annotation.label)
        for name, annotation in drawing.iter_annotations()
        if name.startswith("hc_")
    ]
    assert labels.count("3× ⌀10.7 ↧ 30.5") == 2


def test_frame_candidate_keeps_far_x_location_on_a3(monkeypatch, capsys, tmp_path):
    from draftwright import analysis

    script = _load_script()
    monkeypatch.setattr(analysis, "_measure_strips", analysis._measure_strips)
    monkeypatch.setenv("DRAFTWRIGHT_EXPERIMENTAL_CROSSING_RECOVERY", "1")
    monkeypatch.setenv("DRAFTWRIGHT_EXPERIMENTAL_PLAN_X_BELOW", "1")
    monkeypatch.setenv("DRAFTWRIGHT_EXPERIMENTAL_ARRANGEMENT", "staggered-side")
    monkeypatch.setenv("DRAFTWRIGHT_EXPERIMENTAL_EXTERIOR_DIMENSIONS", "1")
    args = SimpleNamespace(
        mode="candidate",
        source=Path(__file__).parent / "fixtures" / "issue_1595_whistle_key_frame.step",
        output=tmp_path / "frame",
        page="A3",
        scale=2.0,
        title="frame",
        number="frame",
        formats="svg",
        candidate_routes=(
            "front/right,plan/right,front/left,plan/left,plan/above,front/above,"
            "front/below,plan/below,side/above,side/below,side/right,rear/right"
        ),
        arrangement="staggered-side",
        exterior_dimensions=True,
    )
    assert script._worker(args) == 0
    candidate = json.loads(capsys.readouterr().out)
    locations = [
        annotation
        for annotation in candidate["manifest"]["annotations"].values()
        if annotation["label"] == "27.8"
    ]
    assert len(locations) == 1
    assert locations[0]["region"] != "interior"
    assert candidate["manifest"]["coverage"]["missing"] == 0


def test_grm04_iso_growth_preserves_the_fixed_sheet(monkeypatch, capsys, tmp_path):
    script = _load_script()
    common = dict(
        source=Path(__file__).parent / "fixtures" / "grm04_drive_plate.step",
        page="A3",
        scale=5.0,
        title="grm04",
        number="grm04",
        formats="svg",
        candidate_routes="none",
        arrangement=None,
        exterior_dimensions=False,
    )
    baseline_args = SimpleNamespace(**common, mode="baseline", iso_growth=False)
    baseline = script._run_worker(baseline_args, "baseline", tmp_path / "baseline")
    monkeypatch.setenv("DRAFTWRIGHT_EXPERIMENTAL_ISO_GROW", "1")
    candidate_args = SimpleNamespace(
        **common, mode="candidate", iso_growth=True, output=tmp_path / "candidate"
    )

    assert script._worker(candidate_args) == 0
    candidate = json.loads(capsys.readouterr().out)
    comparison = script._compare(baseline, candidate)
    assert comparison["parity"]["passed"]
    assert comparison["quality_comparison"]["verdict"] == "candidate"
    assert comparison["quality_comparison"]["reason"] == "larger_iso"
    assert comparison["quality_comparison"]["iso_area_ratio"] >= 1.10
