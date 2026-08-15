"""Executable guards for the hosted-runner budget and protected release topology."""

import json
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


def _matrix_variants(test_job: str) -> tuple[list[dict], list[dict]]:
    """The pull-request matrix and the full matrix, as the workflow expression defines them."""
    literals = re.findall(r"'(\[\{.*?\}\])'", test_job, re.S)
    assert len(literals) == 2, "expected exactly two matrix variants in the include expression"
    return tuple(json.loads(re.sub(r"\s+", " ", literal)) for literal in literals)


def test_pull_requests_run_linux_only_to_stay_inside_the_runner_budget():
    workflow = _workflow("ci.yml")
    test_job = _job(workflow, "test")
    pr_matrix, full_matrix = _matrix_variants(test_job)

    # macOS meters at 10x and Windows at 2x. Measured on run 31570091735, the four
    # non-Linux jobs were 43 of 100 wall-clock minutes but 236 of 294 billed ones. At the
    # observed ~234 pull-request CI runs a month that is ~55,000 billed minutes, and it
    # exhausted the account's Actions allowance. Hence: no non-Linux runner on a plain PR.
    assert {entry["os"] for entry in pr_matrix} == {"ubuntu-latest"}

    # Every supported Python still runs on every PR; 3.13 is the separate coverage job.
    assert {entry["python-version"] for entry in pr_matrix} | {"3.13"} == {
        "3.10",
        "3.11",
        "3.12",
        "3.13",
        "3.14",
    }

    # The full surface still exists and still covers both kernels on both non-Linux OSes:
    # 3.12 is the last release carrying VTK, 3.14 the current no-VTK kernel.
    for os_name in ("macos-latest", "windows-latest"):
        assert {e["python-version"] for e in full_matrix if e["os"] == os_name} == {
            "3.12",
            "3.14",
        }

    assert "if: github.event_name != 'push'" in test_job
    assert "if: github.event_name == 'pull_request'" in _job(workflow, "coverage")


def test_the_full_matrix_stays_reachable_without_editing_the_workflow():
    """Linux-only PRs are only defensible while the rest of the surface still runs."""
    workflow = _workflow("ci.yml")
    test_job = _job(workflow, "test")

    assert "schedule:" in workflow and "cron:" in workflow  # weekly sweep
    assert "workflow_dispatch:" in workflow  # on demand, any branch
    assert "full-matrix" in test_job  # opt in on a single pull request


def test_main_runs_static_and_slow_gates_without_repeating_fast_matrix():
    workflow = _workflow("ci.yml")

    assert "push:\n    branches: [main]" in workflow
    assert "if:" not in _job(workflow, "lint")
    assert "if: github.event_name == 'push'" in _job(workflow, "test-slow")
    # `test` also serves the weekly sweep and workflow_dispatch, so it is gated on "not a
    # merge to main" rather than on "is a pull request".
    assert "if: github.event_name != 'push'" in _job(workflow, "test")
    assert "if: github.event_name == 'pull_request'" in _job(workflow, "coverage")


def test_slow_gate_distributes_individual_cases_instead_of_serialising_modules():
    slow_job = _job(_workflow("ci.yml"), "test-slow")

    assert re.findall(r"run: (uv run pytest tests/ -m slow[^\n]*)", slow_job) == [
        "uv run pytest tests/ -m slow -n auto --dist load"
    ]


def test_project_coverage_allows_only_a_small_refactor_fluctuation():
    """Deleting above-average covered modules must not block a tested extraction."""
    policy = (ROOT / "codecov.yml").read_text(encoding="utf-8")

    assert policy == (
        "coverage:\n"
        "  status:\n"
        "    project:\n"
        "      default:\n"
        "        target: auto\n"
        "        threshold: 0.5%\n"
    )


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
