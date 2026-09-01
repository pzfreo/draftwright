"""Deployed boundary for the standalone recognition package (epic extraction #1)."""

from __future__ import annotations

import re
from pathlib import Path

import b123d_recognisers as external
from _recogniser_public_contract import public_recogniser_member, public_recogniser_names
from build123d import Box

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

import draftwright.recognition as compatibility
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


def test_embedded_implementation_is_gone_and_compatibility_is_identity_preserving() -> None:
    assert {path.name for path in RECOGNITION_DIR.glob("*.py")} == {"__init__.py"}
    assert frozenset(compatibility.__all__) == public_recogniser_names()
    for name in public_recogniser_names():
        assert getattr(compatibility, name) is public_recogniser_member(name)
    assert feature_census is external.feature_census


def test_recognition_cache_is_consumer_owned_and_runs_once(monkeypatch) -> None:
    calls = 0
    original = external.build_raw_recognition_result

    def counting_build(part):
        nonlocal calls
        calls += 1
        return original(part)

    monkeypatch.setattr(
        "draftwright.recognition_cache.build_raw_recognition_result", counting_build
    )
    cache = RecognitionCache()
    part = Box(10, 10, 10)

    first = cache.ensure(part)
    assert cache.ensure(part) is first
    assert cache.result is first
    assert calls == 1
