"""Executable guards for the hosted-runner budget and protected release topology."""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text()


def _job(workflow: str, name: str) -> str:
    lines = workflow.splitlines()
    start = lines.index(f"  {name}:")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if re.fullmatch(r"  [a-z][a-z0-9-]*:", lines[index])
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_pr_matrix_preserves_compatibility_dimensions_with_nine_test_jobs():
    workflow = _workflow("ci.yml")
    test_job = _job(workflow, "test")
    entries = set(re.findall(r'- \{os: ([^,]+), python-version: "([^"]+)"\}', test_job))

    assert "if: github.event_name == 'pull_request'" in test_job
    assert "if: github.event_name == 'pull_request'" in _job(workflow, "coverage")
    assert len(entries) + 1 == 9  # the separate Ubuntu 3.13 coverage job
    assert {version for os_name, version in entries if os_name == "ubuntu-latest"} | {"3.13"} == {
        "3.10",
        "3.11",
        "3.12",
        "3.13",
        "3.14",
    }
    for os_name in ("macos-latest", "windows-latest"):
        assert {version for os_entry, version in entries if os_entry == os_name} == {
            "3.12",
            "3.14",
        }


def test_main_runs_static_and_slow_gates_without_repeating_fast_matrix():
    workflow = _workflow("ci.yml")

    assert "push:\n    branches: [main]" in workflow
    assert "if:" not in _job(workflow, "lint")
    assert "if: github.event_name == 'push'" in _job(workflow, "test-slow")
    assert "if: github.event_name == 'pull_request'" in _job(workflow, "test")
    assert "if: github.event_name == 'pull_request'" in _job(workflow, "coverage")


def test_testpypi_snapshot_build_and_publish_share_one_job():
    workflow = _workflow("publish.yml")
    snapshot = _job(workflow, "publish-testpypi")

    assert "if: github.event_name == 'push'" in snapshot
    assert "environment: testpypi" in snapshot
    assert "contents: read\n      id-token: write" in snapshot
    assert "- run: uv build" in snapshot
    assert "- uses: pypa/gh-action-pypi-publish@release/v1" in snapshot
    assert "repository-url: https://test.pypi.org/legacy/" in snapshot
    assert "if: github.event_name == 'release'" in _job(workflow, "build-release")
    assert "needs: build-release" in _job(workflow, "publish-pypi")
