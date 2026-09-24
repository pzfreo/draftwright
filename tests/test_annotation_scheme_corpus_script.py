import importlib.machinery
import importlib.util
import json
from pathlib import Path

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
