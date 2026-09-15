"""Opt-in pytest report for the operations that dominate suite cost."""

from __future__ import annotations

import inspect
import json
import platform
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

SCHEMA_VERSION = 1
_STATE = pytest.StashKey()
_PROFILE = pytest.StashKey()
_ACTIVE_RECIPES: list[dict[str, Any]] | None = None


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("test burden")
    group.addoption(
        "--burden-report",
        metavar="PATH",
        help="write per-test durations and expensive-operation counts as JSON",
    )


@dataclass
class _State:
    path: Path
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    workers: set[str] = field(default_factory=set)


def pytest_configure(config: pytest.Config) -> None:
    if path := config.getoption("burden_report"):
        config.stash[_STATE] = _State(Path(path))


def _targets() -> dict[object, str]:
    from build123d import Shape, export_step, import_step
    from quiddity import build_raw_recognition_result
    from quiddity.evidence import build_recognition_evidence

    from draftwright import analysis, build_drawing
    from draftwright.drawing import Drawing
    from draftwright.model.compiled import compile_dimensions

    functions = {
        "shape_constructions": Shape.__init__,
        "step_imports": import_step,
        "step_exports": export_step,
        "raw_recognition_runs": build_raw_recognition_result,
        "recognition_evidence_runs": build_recognition_evidence,
        "analysis_runs": analysis._analyse,
        "compilation_runs": compile_dimensions,
        "drawing_builds": build_drawing,
        "lint_runs": Drawing.lint,
        "format_exports": Drawing.export,
    }
    by_code: dict[object, str] = {}
    for name, function in functions.items():
        code = inspect.unwrap(function).__code__
        if code in by_code:
            raise RuntimeError(f"burden counters {name!r} and {by_code[code]!r} share code")
        by_code[code] = name
    return by_code


@dataclass
class _Profile:
    previous: Any
    previous_recipes: list[dict[str, Any]] | None
    counts: Counter[str] = field(default_factory=Counter)
    recipes: list[dict[str, Any]] = field(default_factory=list)


def _start_profile(item: pytest.Item) -> None:
    global _ACTIVE_RECIPES
    previous = sys.getprofile()
    if previous is not None and not callable(previous):
        raise RuntimeError("--burden-report cannot chain to the installed C-level profiler")
    active = _Profile(previous=previous, previous_recipes=_ACTIVE_RECIPES)
    by_code = _targets()

    def profile(frame, event, arg):
        if event == "call" and (name := by_code.get(frame.f_code)) is not None:
            active.counts[name] += 1
        if active.previous is not None:
            active.previous(frame, event, arg)

    item.stash[_PROFILE] = active
    _ACTIVE_RECIPES = active.recipes
    sys.setprofile(profile)


def _stop_profile(item: pytest.Item) -> None:
    global _ACTIVE_RECIPES
    active = item.stash.get(_PROFILE, None)
    if active is None:
        return
    # Fixture teardown has restored any sys.setprofile monkeypatch installed by the test.
    sys.setprofile(active.previous)
    _ACTIVE_RECIPES = active.previous_recipes


def record_recipe(recipe: str, options: dict[str, Any], *, cache_hit: bool) -> None:
    """Record a shared-drawing request when a burden-report phase is active."""
    if _ACTIVE_RECIPES is None:
        return
    _ACTIVE_RECIPES.append(
        {
            "recipe": recipe,
            "options": {name: repr(value) for name, value in sorted(options.items())},
            "cache_hit": cache_hit,
        }
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item: pytest.Item):
    if _STATE in item.config.stash:
        _start_profile(item)
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None):
    yield
    if _STATE in item.config.stash:
        _stop_profile(item)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    if _STATE not in item.config.stash:
        return
    active = item.stash.get(_PROFILE, None)
    payload: dict[str, Any] = {
        "duration": report.duration,
        "outcome": report.outcome,
        "counts": {},
        "recipes": [],
    }
    if report.when == "teardown" and active is not None:
        payload["counts"] = dict(active.counts)
        payload["recipes"] = active.recipes
    report.user_properties.append(("draftwright_burden", json.dumps(payload, sort_keys=True)))


def _collect_report(state: _State, report: pytest.TestReport) -> None:
    for name, encoded in report.user_properties:
        if name != "draftwright_burden":
            continue
        state.workers.add(str(getattr(report, "worker_id", "local")))
        test = state.reports.setdefault(
            report.nodeid,
            {"nodeid": report.nodeid, "counts": {}, "phases": {}, "recipes": []},
        )
        payload = json.loads(encoded)
        test["phases"][report.when] = {
            "duration": payload["duration"],
            "outcome": payload["outcome"],
        }
        if report.when == "teardown":
            test["counts"] = payload["counts"]
            test["recipes"] = payload["recipes"]


def _document(state: _State) -> dict[str, Any]:
    tests = sorted(state.reports.values(), key=lambda row: row["nodeid"])
    duration = 0.0
    counts: Counter[str] = Counter()
    recipes: Counter[tuple[str, str, bool]] = Counter()
    for test in tests:
        for phase in test["phases"].values():
            duration += phase["duration"]
        counts.update(test["counts"])
        for recipe in test["recipes"]:
            options = json.dumps(recipe["options"], sort_keys=True)
            recipes[(recipe["recipe"], options, recipe["cache_hit"])] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "python": platform.python_version(),
        "workers": sorted(state.workers),
        "totals": {
            "duration": duration,
            "counts": dict(sorted(counts.items())),
            "recipe_requests": [
                {
                    "recipe": recipe,
                    "options": json.loads(options),
                    "cache_hit": cache_hit,
                    "count": count,
                }
                for (recipe, options, cache_hit), count in sorted(recipes.items())
            ],
        },
        "tests": tests,
    }


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    if _STATE not in config.stash or hasattr(config, "workerinput"):
        return
    state = config.stash[_STATE]
    # In a local run logreport lacks config; collect reports retained by terminal reporter.
    terminal = config.pluginmanager.getplugin("terminalreporter")
    if terminal is not None and not state.reports:
        for reports in terminal.stats.values():
            for report in reports:
                if isinstance(report, pytest.TestReport):
                    _collect_report(state, report)
    state.path.parent.mkdir(parents=True, exist_ok=True)
    state.path.write_text(json.dumps(_document(state), indent=2, sort_keys=True) + "\n")
