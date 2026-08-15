"""Deployed boundary for the standalone recognition package (epic extraction #1)."""

from __future__ import annotations

from pathlib import Path

import b123d_recognisers as external
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
PINNED_VERSION = "0.2.0"
RELEASE_HASHES = {
    "16f6e13160310069c2ab610233af3b8da3c6ffb03b481aa38e532e8743381eda",
    "2ea7e0220bcfc590a2a36dd4eeb5c8ba30ab94141a1eac26094f7917e998f034",
}


def test_dependency_is_pinned_to_the_published_stable_release() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependency = next(d for d in project["dependencies"] if d.startswith("b123d-recognisers"))

    assert dependency == f"b123d-recognisers=={PINNED_VERSION}"
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    package = lock.split('name = "b123d-recognisers"', 1)[1].split("[[package]]", 1)[0]
    assert f'version = "{PINNED_VERSION}"' in package
    assert 'source = { registry = "https://pypi.org/simple" }' in package
    assert "git+" not in package
    assert RELEASE_HASHES == {
        line.split("sha256:")[1].split('"')[0]
        for line in package.splitlines()
        if 'hash = "sha256:' in line
    }


def test_embedded_implementation_is_gone_and_compatibility_is_identity_preserving() -> None:
    assert {path.name for path in RECOGNITION_DIR.glob("*.py")} == {"__init__.py"}
    assert compatibility.__all__ == external.__all__
    for name in external.__all__:
        assert getattr(compatibility, name) is getattr(external, name)
    assert feature_census is external.feature_census


def test_recognition_cache_is_consumer_owned_and_runs_once(monkeypatch) -> None:
    calls = 0
    original = external.build_recognition_result

    def counting_build(part):
        nonlocal calls
        calls += 1
        return original(part)

    monkeypatch.setattr("draftwright.recognition_cache.build_recognition_result", counting_build)
    cache = RecognitionCache()
    part = Box(10, 10, 10)

    first = cache.ensure(part)
    assert cache.ensure(part) is first
    assert cache.result is first
    assert calls == 1
