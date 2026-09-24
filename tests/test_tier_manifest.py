"""Cost-aware tier manifests remain complete and conservative."""

import os
import runpy
from pathlib import Path
from types import SimpleNamespace

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the Python 3.10 CI legs
    import tomli as tomllib

from _tier_manifest import (
    BROAD_SOURCE_PATTERNS,
    CONTRACT_GROUPS,
    CRITICAL_CONTRACT_MODULES,
    FULL_EXPRESSION,
    PR_CORE_MODULES,
    PR_POLICY_MODULES,
    pr_modules,
    selected_groups,
)
from _unit_manifest import UNIT_MODULES

_TESTS = Path(__file__).resolve().parent


def test_each_named_contract_group_resolves_to_existing_modules():
    all_modules = {path.name for path in _TESTS.glob("test_*.py")}
    probes = {
        "recognition": "src/draftwright/recognition_frame.py",
        "compilation": "src/draftwright/intents.py",
        "placement": "src/draftwright/layout.py",
        "reporting": "src/draftwright/reporting.py",
        "export": "src/draftwright/export.py",
    }
    assert set(probes) == set(CONTRACT_GROUPS)
    for name, path in probes.items():
        assert name in selected_groups([path])
        selected = set(pr_modules(_TESTS, [path]))
        assert selected <= all_modules
        assert selected - PR_CORE_MODULES - PR_POLICY_MODULES - UNIT_MODULES, name


def test_unknown_production_module_selects_every_contract_group():
    assert selected_groups(["src/draftwright/a_new_area.py"]) == frozenset(CONTRACT_GROUPS)
    for pattern in BROAD_SOURCE_PATTERNS:
        assert selected_groups([pattern]) == frozenset(CONTRACT_GROUPS)


def test_nonproduction_changes_do_not_expand_the_core():
    assert set(pr_modules(_TESTS, ["docs/guide.md"])) == (
        set(PR_CORE_MODULES) | set(PR_POLICY_MODULES) | set(UNIT_MODULES)
    )


def test_repository_policy_guards_are_pr_only():
    assert PR_POLICY_MODULES.isdisjoint(UNIT_MODULES)


def test_changed_test_module_is_selected_directly():
    assert "test_tier_manifest.py" in pr_modules(_TESTS, ["tests/test_tier_manifest.py"])


def test_deleted_test_module_is_not_reintroduced_as_a_missing_path():
    selected = pr_modules(_TESTS, ["tests/test_deleted_contract.py"])

    assert "test_deleted_contract.py" not in selected


def test_tier_runner_includes_deletions_in_each_git_diff():
    runner = _TESTS.parent / "scripts" / "test-tier"
    namespace = runpy.run_path(str(runner))
    calls = []

    def record_git_paths(*arguments):
        calls.append(arguments)
        return set()

    namespace["changed_paths"].__globals__["_git_paths"] = record_git_paths
    namespace["changed_paths"]("base")

    diff_calls = [arguments for arguments in calls if arguments[0] == "diff"]
    assert len(diff_calls) == 3
    assert all("--diff-filter=ACMRD" in arguments for arguments in diff_calls)


def test_critical_contracts_are_fast_and_exist():
    available = {path.name for path in _TESTS.glob("test_*.py")}
    assert CRITICAL_CONTRACT_MODULES <= available
    for module in CRITICAL_CONTRACT_MODULES:
        source = (_TESTS / module).read_text()
        assert "pytest.mark.slow" not in source
        assert "pytest.mark.scheduled" not in source


def test_tier_runner_is_executable():
    runner = _TESTS.parent / "scripts" / "test-tier"
    assert runner.is_file()
    assert os.access(runner, os.X_OK)


def test_scheduled_selection_is_intersected_with_the_tier(monkeypatch):
    runner = _TESTS.parent / "scripts" / "test-tier"
    namespace = runpy.run_path(str(runner))
    captured = []

    monkeypatch.setattr(namespace["pytest"], "main", lambda args: captured.extend(args) or 0)

    assert namespace["main"](["scheduled", "--selection", "ctc04", "--workers", "1"]) == 0
    marker = captured[captured.index("-m") + 1]
    assert marker == "(slow or scheduled) and (ctc04)"


def test_isolated_scheduled_runner_restarts_pytest_per_test(monkeypatch):
    runner = _TESTS.parent / "scripts" / "test-tier"
    namespace = runpy.run_path(str(runner))
    calls = []

    def completed(command, **kwargs):
        calls.append(command)
        if "--collect-only" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "tests/test_one.py::test_a\n"
                    "tests/test_one.py::test_b\n"
                    "tests/test_two.py::test_c\n"
                ),
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(namespace["subprocess"], "run", completed)

    assert namespace["main"](["scheduled", "--workers", "0", "--isolate-tests"]) == 0
    assert "--collect-only" in calls[0]
    assert calls[1][3:4] == ["tests/test_one.py::test_a"]
    assert calls[2][3:4] == ["tests/test_one.py::test_b"]
    assert calls[3][3:4] == ["tests/test_two.py::test_c"]
    assert (
        calls[1][-5:]
        == calls[2][-5:]
        == calls[3][-5:]
        == [
            "-m",
            "slow or scheduled",
            "-n",
            "0",
            "--timeout=600",
        ]
    )


def test_default_pytest_selection_matches_the_full_tier():
    config = tomllib.loads((_TESTS.parent / "pyproject.toml").read_text(encoding="utf-8"))

    assert f"-m '{FULL_EXPRESSION}'" in config["tool"]["pytest"]["ini_options"]["addopts"]
