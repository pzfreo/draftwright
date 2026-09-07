"""A cheap CI path must prove the entire change is only a development version bump."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_workflows import _bash, _job, _literal_run, _workflow

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="The CI verifier uses stdlib tomllib on Python 3.11+"
)

ROOT = Path(__file__).resolve().parents[1]


def _git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def bump_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "ci@example.invalid")
    _git(tmp_path, "config", "user.name", "CI test")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "draftwright"\nversion = "0.4.21.dev0"\n'
        'dependencies = ["quiddity==0.2.4"]\n'
    )
    (tmp_path / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "draftwright"\nversion = "0.4.21.dev0"\n'
        'source = { editable = "." }\n'
        '[[package]]\nname = "quiddity"\nversion = "0.2.4"\n'
    )
    (tmp_path / "engine.py").write_text("answer = 42\n")
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/check-version-bump", tmp_path / "scripts/check-version-bump")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    for name in ("pyproject.toml", "uv.lock"):
        path = tmp_path / name
        path.write_text(path.read_text().replace("0.4.21.dev0", "0.4.22.dev0"))
    return tmp_path, base


def _classify(root, base, head):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-version-bump"), base, head],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() in {"true", "false"}
    return result.stdout.strip() == "true"


def _commit(root):
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "candidate")
    return _git(root, "rev-parse", "HEAD")


def test_exact_next_development_version_qualifies(bump_repo):
    root, base = bump_repo
    head = _commit(root)
    assert _git(root, "diff", "--name-only", base, head).splitlines() == [
        "pyproject.toml",
        "uv.lock",
    ]
    assert _classify(root, base, head)


@pytest.mark.parametrize(
    ("path", "old", "new"),
    [
        ("pyproject.toml", "0.4.22.dev0", "0.4.23.dev0"),
        ("uv.lock", "0.4.22.dev0", "0.4.21.dev0"),
        ("pyproject.toml", "quiddity==0.2.4", "quiddity==0.2.5"),
        ("uv.lock", 'version = "0.2.4"', 'version = "0.2.5"'),
        ("uv.lock", 'editable = "."', 'editable = "elsewhere"'),
        ("pyproject.toml", "[project]", "[project]\nversion = 123"),
        ("pyproject.toml", 'name = "draftwright"', 'name = "different"'),
        ("engine.py", "answer = 42", "answer = 0"),
    ],
    ids=[
        "skip-patch",
        "lock-mismatch",
        "dependency",
        "resolution",
        "source",
        "malformed",
        "name",
        "code",
    ],
)
def test_other_changes_require_normal_ci(bump_repo, path, old, new):
    root, base = bump_repo
    target = root / path
    text = target.read_text()
    assert text.count(old) == 1
    target.write_text(text.replace(old, new))
    assert not _classify(root, base, _commit(root))


@pytest.mark.parametrize(
    "change", ["extra-file", "deleted-file", "rename", "comment", "mode", "duplicate-owner"]
)
def test_complete_tree_proof_rejects_non_version_changes(bump_repo, change):
    root, base = bump_repo
    if change == "extra-file":
        (root / ".hidden-config").write_text("changed\n")
    elif change == "deleted-file":
        (root / "engine.py").unlink()
    elif change == "rename":
        (root / "engine.py").rename(root / "renamed.py")
    elif change == "comment":
        with (root / "pyproject.toml").open("a") as f:
            f.write("# extra edit\n")
    elif change == "mode":
        _git(root, "config", "core.filemode", "true")
        _git(root, "add", "-A")
        _git(root, "update-index", "--chmod=+x", "pyproject.toml")
        # Commit the index directly: Windows has no POSIX executable filesystem bit.
        _git(root, "commit", "-qm", "mode change")
        assert not _classify(root, base, _git(root, "rev-parse", "HEAD"))
        return
    else:
        with (root / "uv.lock").open("a") as f:
            f.write('[[package]]\nname = "draftwright"\nversion = "0.4.22.dev0"\n')
    assert not _classify(root, base, _commit(root))


def test_missing_or_unrelated_history_and_no_diff_require_normal_ci(bump_repo):
    root, base = bump_repo
    head = _commit(root)
    assert not _classify(root, "0" * 40, head)
    assert not _classify(root, "--help", head)
    assert not _classify(root, base, base)
    assert not _classify(root, head, base)
    unrelated = _git(
        root, "commit-tree", _git(root, "rev-parse", f"{base}^{{tree}}"), "-m", "unrelated"
    )
    assert _git(root, "diff", "--exit-code", base, unrelated) == ""
    assert not _classify(root, unrelated, head)


@pytest.mark.skipif(os.name == "nt", reason="The classification shell runs on Ubuntu only")
@pytest.mark.parametrize(
    "scenario",
    [
        "pr",
        "push",
        "dispatch",
        "stale-head",
        "foreign-repo",
        "closed-pr",
        "other-base",
        "ordinary-manual",
        "schedule",
        "changed-verifier",
        "full-matrix",
        "api-failure",
        "malformed-response",
        "missing-base",
    ],
)
def test_workflow_classification_binds_dispatch_and_executes_the_base_verifier(
    bump_repo, scenario
):
    root, base = bump_repo
    if scenario == "changed-verifier":
        (root / "scripts/check-version-bump").write_text('print("true")\n')
    head = _commit(root)
    _git(root, "remote", "add", "origin", str(root))
    event = "workflow_dispatch"
    if scenario in {"pr", "changed-verifier", "full-matrix"}:
        event = "pull_request"
    elif scenario in {"push", "schedule"}:
        event = scenario
    metadata = {
        "state": "closed" if scenario == "closed-pr" else "open",
        "head": {
            "sha": base if scenario == "stale-head" else head,
            "repo": {"full_name": "other/repo" if scenario == "foreign-repo" else "test/repo"},
        },
        "base": {
            "sha": "0" * 40 if scenario == "missing-base" else base,
            "ref": "other" if scenario == "other-base" else "main",
        },
    }
    # Outputs and mocked API response live outside the tracked candidate tree.
    runtime = root / "runtime"
    runtime.mkdir()
    output = runtime / "classification-output"
    response = runtime / "pr-metadata.json"
    response.write_text(
        "invalid JSON" if scenario == "malformed-response" else json.dumps(metadata)
    )
    env = {
        **os.environ,
        "EVENT_NAME": event,
        "BASE_SHA": base if event in {"pull_request", "push"} else "",
        "HEAD_SHA": head,
        "GITHUB_SHA": head,
        "GITHUB_REPOSITORY": "test/repo",
        "POST_RELEASE": "false" if scenario == "ordinary-manual" else "true",
        "PR_NUMBER": "1",
        "FULL_MATRIX": "true" if scenario == "full-matrix" else "false",
        "RUNNER_TEMP": str(runtime),
        "GITHUB_OUTPUT": str(output),
        "PR_METADATA": str(response),
        "TEST_PYTHON": sys.executable,
    }
    shell = 'gh() { cat "$PR_METADATA"; }\npython3() { "$TEST_PYTHON" "$@"; }\n'
    if scenario == "api-failure":
        shell = "gh() { return 1; }\n"
    result = subprocess.run(
        [
            _bash(),
            "-eu",
            "-o",
            "pipefail",
            "-c",
            shell + _literal_run(_job(_workflow("ci.yml"), "changes")),
        ],
        env=env,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = "true" if scenario in {"pr", "push", "dispatch"} else "false"
    assert output.read_text().splitlines() == [f"version_only={expected}", f"head={head}"]


@pytest.mark.parametrize("target", ["0.4.20.dev0", "0.4.23.dev0", "0.5.0.dev0", "0.4.22"])
def test_matching_versions_still_require_exact_next_patch_dev0(bump_repo, target):
    root, base = bump_repo
    for name in ("pyproject.toml", "uv.lock"):
        path = root / name
        text = path.read_text()
        assert text.count("0.4.22.dev0") == 1
        path.write_text(text.replace("0.4.22.dev0", target))
    assert not _classify(root, base, _commit(root))
