import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "annotation-scheme-corpus"
MANIFEST = Path(__file__).parent / "fixtures" / "annotation-layout-corpus-v1.json"
EXPANDED_MANIFEST = Path(__file__).parent / "fixtures" / "annotation-layout-corpus-v2.json"


def _load_script():
    loader = importlib.machinery.SourceFileLoader("annotation_scheme_corpus_script", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _result(verdict, *, parity=True, affected=True):
    return {
        "parity": {"passed": parity},
        "quality_comparison": {
            "verdict": verdict,
            "baseline_key": (int(affected), 0, 0, 0, 0, -0.2, 124740.0),
        },
    }


def test_versioned_layout_corpus_names_fixed_sheet_cases():
    corpus = _load_script()._load_manifest(MANIFEST)

    assert (corpus["corpus_version"], corpus["metric_version"]) == ("1.0.0", 1)
    assert [case["id"] for case in corpus["cases"]] == ["ctc01-a3-1to5", "ctc02-a2-1to5"]
    assert all(case["page"].startswith("A") and case["scale"] > 0 for case in corpus["cases"])


def test_contender_selects_only_proven_wins_and_falls_back_for_everything_else():
    aggregate = _load_script()._aggregate

    mixed = aggregate(
        [
            _result("candidate"),
            _result("tie"),
            _result("baseline"),
            _result("ineligible", parity=False),
        ]
    )
    assert mixed["production_contender"]
    assert mixed["selected"] == {"candidate": 1, "baseline": 3}
    assert not aggregate([_result("tie"), _result("ineligible", parity=False)])[
        "production_contender"
    ]


def test_expanded_corpus_tracks_majority_of_baselines_with_layout_defects():
    corpus = _load_script()._load_manifest(EXPANDED_MANIFEST)
    assert (corpus["corpus_version"], len(corpus["cases"])) == ("2.0.0", 15)

    aggregate = _load_script()._aggregate
    summary = aggregate(
        [
            _result("candidate"),
            _result("candidate"),
            _result("candidate"),
            _result("ineligible", parity=False),
            _result("tie"),
            _result("tie", affected=False),
        ]
    )
    assert summary["affected_baselines"] == 5
    assert summary["improved_affected"] == 3
    assert summary["affected_majority"] is True
    assert aggregate([_result("candidate"), _result("tie")])["affected_majority"] is False


def test_manifest_rejects_duplicate_case_ids(tmp_path):
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    document["cases"][1]["id"] = document["cases"][0]["id"]
    damaged = tmp_path / "corpus.json"
    damaged.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="unique"):
        _load_script()._load_manifest(damaged)


@pytest.mark.parametrize("case_id", ["../outside", "CON", "COM1.foo", "trailing."])
def test_manifest_rejects_unsafe_case_report_name(tmp_path, case_id):
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    document["cases"][0]["id"] = case_id
    damaged = tmp_path / "corpus.json"
    damaged.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="safe file names"):
        _load_script()._load_manifest(damaged)


def test_public_corpus_requires_the_selected_trial_to_be_verified():
    convert = _load_script()._public_result
    decision = {
        "selected_trial": "columns",
        "baseline_quality_key": [2, 0, 2, 1, 1, -1.0, 62370.0],
        "selected_quality_key": [1, 0, 1, 1, 0, -1.0, 62370.0],
        "trials": [{"name": "columns", "verdict": "candidate", "semantic_parity": True}],
    }

    result = convert({"layout_decision": decision})
    assert result["quality_comparison"]["verdict"] == "candidate"
    assert result["parity"]["passed"]
    assert result["selected_trial"] == "columns"

    decision["trials"][0]["semantic_parity"] = False
    with pytest.raises(ValueError, match="unverified"):
        convert({"layout_decision": decision})


def test_candidate_first_summary_keeps_rendered_candidates_separate_from_wins():
    aggregate = _load_script()._aggregate_preview
    results = [
        {
            **_result("candidate"),
            "candidate": {
                "layout_decision": {"safety_evidence": {"checks_passed": True}},
                "cost": {"process_seconds": 2.0, "build_seconds": 1.0, "peak_rss_mib": 100.0},
            },
            "baseline": {
                "cost": {"process_seconds": 3.0, "build_seconds": 2.0, "peak_rss_mib": 150.0}
            },
        },
        {
            **_result("ineligible", parity=False),
            "candidate": {
                "layout_decision": {"safety_evidence": {"checks_passed": False}},
                "cost": {"process_seconds": 4.0, "build_seconds": 3.0, "peak_rss_mib": 200.0},
            },
            "baseline": {
                "cost": {"process_seconds": 5.0, "build_seconds": 4.0, "peak_rss_mib": 250.0}
            },
        },
    ]

    summary = aggregate(results)

    assert summary["rendered_candidate"] == 2
    assert summary["selected"] == {"candidate": 1, "baseline": 1}
    assert summary["production_selected"] == 0
    assert summary["verified_wins"] == 1
    assert summary["semantic_parity"] == 1
    assert summary["safety_checks_passed"] == 1
    assert summary["safety_admission_ready"] is False
    assert summary["production_contender"] is False
    assert summary["cost"]["candidate_process_seconds"] == {
        "samples": 2,
        "median": 3.0,
        "p95_nearest_rank": 4.0,
    }
    assert summary["cost"]["baseline_process_seconds"]["median"] == 4.0
    assert summary["cost"]["baseline_build_seconds"]["median"] == 3.0
    assert summary["cost"]["candidate_peak_rss_mib"]["p95_nearest_rank"] == 200.0
    assert summary["cost"]["baseline_peak_rss_mib"] == {
        "samples": 2,
        "median": 200.0,
        "p95_nearest_rank": 250.0,
    }
    assert summary["cost"]["fallback_rate"] is None


def test_candidate_first_case_dispatches_the_preview_comparison(monkeypatch, tmp_path):
    script = _load_script()
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    **_result("candidate"),
                    "candidate": {"layout_decision": {"safety_evidence": {}}},
                }
            ),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(script.subprocess, "run", run)
    case = _load_script()._load_manifest(MANIFEST)["cases"][0]
    case_report_dir = tmp_path / "case-reports"
    result = script._run_case(
        case,
        tmp_path,
        candidate_first=True,
        case_report_dir=case_report_dir,
        worker_timeout_seconds=600.0,
    )

    assert commands[0][-4:] == [
        "--mode",
        "candidate-preview",
        "--worker-timeout-seconds",
        "600.0",
    ]
    assert result["case_id"] == case["id"]
    assert (
        json.loads((case_report_dir / f"{case['id']}.json").read_text(encoding="utf-8")) == result
    )
    assert list(case_report_dir.glob("*.tmp")) == []


def test_candidate_first_summary_keeps_failed_worker_latency_separate():
    result = {
        **_result("ineligible", parity=False),
        "candidate": {"error": "build failed", "cost": {"process_seconds": 6.0}},
        "baseline": {"cost": {"process_seconds": 4.0}},
    }

    summary = _load_script()._aggregate_preview([result])

    assert summary["rendered_candidate"] == 0
    assert summary["cost"]["candidate_process_seconds"]["samples"] == 0
    assert summary["cost"]["failed_candidate_process_seconds"]["median"] == 6.0


def test_timed_out_baseline_is_not_counted_as_selected_or_successful_cost():
    script = _load_script()
    failed = script._aggregate_preview(
        [
            {
                **_result("ineligible", parity=False, affected=False),
                "baseline": {"error": "timeout", "cost": {"process_seconds": 600.0}},
                "candidate": {"error": "not_attempted_after_baseline_failure"},
            }
        ]
    )

    assert failed["failed_baseline"] == 1
    assert failed["selected"] == {"candidate": 0, "baseline": 0}
    assert failed["semantic_parity"] == 0
    assert failed["cost"]["baseline_process_seconds"]["samples"] == 0
    assert failed["cost"]["failed_baseline_process_seconds"]["median"] == 600.0
    assert not failed["affected_majority"]
    assert not failed["production_contender"]


def test_failed_baseline_case_is_persisted_even_with_nonzero_compare_exit(monkeypatch, tmp_path):
    script = _load_script()
    failure = {
        **_result("ineligible", parity=False, affected=False),
        "baseline": {"error": "timeout", "cost": {"process_seconds": 600.0}},
        "candidate": {"error": "not_attempted_after_baseline_failure"},
    }
    monkeypatch.setattr(
        script.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(failure), stderr="", returncode=1
        ),
    )
    case = _load_script()._load_manifest(MANIFEST)["cases"][0]
    report_dir = tmp_path / "case-reports"

    result = script._run_case(case, tmp_path, candidate_first=True, case_report_dir=report_dir)

    assert result["comparison_exit_code"] == 1
    assert json.loads((report_dir / f"{case['id']}.json").read_text(encoding="utf-8")) == result


def test_corpus_exits_nonzero_for_a_baseline_timeout_without_optional_gates(
    monkeypatch, capsys, tmp_path
):
    script = _load_script()

    def failed_case(case, *_args, **_kwargs):
        return {
            **_result("ineligible", parity=False, affected=False),
            "case_id": case["id"],
            "baseline": {"error": "timeout", "cost": {"process_seconds": 600.0}},
            "candidate": {"error": "not_attempted_after_baseline_failure"},
        }

    monkeypatch.setattr(script, "_run_case", failed_case)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "corpus",
            "--manifest",
            str(MANIFEST),
            "--output",
            str(tmp_path),
            "--candidate-first",
            "--worker-timeout-seconds",
            "600",
        ],
    )

    assert script.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["failed_baseline"] == len(report["results"])
    assert report["summary"]["selected"] == {"candidate": 0, "baseline": 0}
