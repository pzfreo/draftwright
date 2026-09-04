"""Deployed boundary for the standalone recognition package (epic extraction #1)."""

from __future__ import annotations

import re
from pathlib import Path

import b123d_recognisers as external
import b123d_recognisers.evidence as external_evidence
import pytest
from _recogniser_public_contract import public_recogniser_member, public_recogniser_names
from build123d import Box

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

import draftwright.recognition as compatibility
from draftwright.drawing import BuildState
from draftwright.recognition_cache import RecognitionCache
from draftwright.score import feature_census

ROOT = Path(__file__).parents[1]
RECOGNITION_DIR = ROOT / "src" / "draftwright" / "recognition"


def test_dependency_is_pinned_to_the_published_stable_release() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependency = next(d for d in project["dependencies"] if d.startswith("b123d-recognisers"))

    match = re.fullmatch(r"b123d-recognisers==(\d+\.\d+\.\d+)", dependency)
    assert match is not None
    pinned_version = match.group(1)
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    package = lock.split('name = "b123d-recognisers"', 1)[1].split("[[package]]", 1)[0]
    assert f'version = "{pinned_version}"' in package
    assert 'source = { registry = "https://pypi.org/simple" }' in package
    assert "git+" not in package


def test_consumed_evidence_api_is_the_released_public_major() -> None:
    manifest = external_evidence.evidence_api_manifest()

    assert manifest["format"] == "b123d-recognisers-evidence-api"
    assert manifest["format_version"] == 1
    assert manifest["api"]["major"] == 1
    assert manifest["api"]["namespace"] == "b123d_recognisers.evidence"
    assert {"RecognitionEvidence", "build_recognition_evidence"} <= set(manifest["api"]["symbols"])


def test_embedded_implementation_is_gone_and_compatibility_is_identity_preserving() -> None:
    assert {path.name for path in RECOGNITION_DIR.glob("*.py")} == {"__init__.py"}
    assert frozenset(compatibility.__all__) == public_recogniser_names()
    for name in public_recogniser_names():
        assert getattr(compatibility, name) is public_recogniser_member(name)
    assert feature_census is external.feature_census


def test_recognition_cache_is_consumer_owned_and_runs_once(monkeypatch) -> None:
    calls = 0
    original = external_evidence.build_recognition_evidence

    def counting_build(part, *, cylinders=None, rotational=False):
        nonlocal calls
        calls += 1
        return original(part, cylinders=cylinders, rotational=rotational)

    monkeypatch.setattr("draftwright.recognition_cache.build_recognition_evidence", counting_build)
    cache = RecognitionCache()
    part = Box(10, 10, 10)

    first = cache.ensure(part)
    assert cache.ensure(part) is first
    assert cache.result is first
    assert cache.evidence is not None
    assert cache.evidence.result is first
    assert calls == 1


def test_bare_seed_never_reruns_to_backfill_evidence(monkeypatch) -> None:
    part = Box(10, 10, 10)
    result = external.build_raw_recognition_result(part)
    cache = RecognitionCache(result=result)

    def forbidden(*args, **kwargs):
        raise AssertionError("a bare result must not create a second recognition universe")

    monkeypatch.setattr("draftwright.recognition_cache.build_recognition_evidence", forbidden)

    assert cache.ensure(part) is result
    assert cache.evidence is None


def test_cache_rejects_result_and_evidence_from_different_runs() -> None:
    part = Box(10, 10, 10)
    first = external_evidence.build_recognition_evidence(part)
    second = external_evidence.build_recognition_evidence(part)

    with pytest.raises(ValueError, match="same run"):
        RecognitionCache(result=first.result, evidence=second)

    cache = RecognitionCache(evidence=first)
    assert cache.result is first.result
    with pytest.raises(ValueError, match="same run"):
        cache.seed(first.result, evidence=second)

    seeded = RecognitionCache()
    seeded.seed(None, evidence=first)
    assert seeded.result is first.result
    assert seeded.evidence is first


def test_build_state_attaches_one_recognition_source_atomically() -> None:
    evidence = external_evidence.build_recognition_evidence(Box(10, 10, 10))
    state = BuildState()

    state.recognition = evidence.result
    assert state.recognition is evidence.result
    assert state.recognition_evidence is None

    with pytest.raises(ValueError, match="both a recognition cache and a new acquisition"):
        state.attach_recognition(
            evidence.result,
            evidence=evidence,
            cache=RecognitionCache(evidence=evidence),
        )
