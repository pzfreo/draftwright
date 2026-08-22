"""The two-repository workflow is executable, reproducible, and branch-protection safe."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path, PurePath

import pytest

ROOT = Path(__file__).parents[1]
GUIDE = ROOT / "docs" / "recogniser-development-workflow.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
DEPENDENCY_BUMP = ROOT / ".github" / "workflows" / "bump-recogniser-dependency.yml"
PUBLISH = ROOT / ".github" / "workflows" / "publish.yml"


def test_policy_defines_compatibility_landing_rollback_and_failure_ownership() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    for required in (
        "scripts/check-recogniser-candidate",
        "Exact production window",
        "Additive package change",
        "Breaking package change",
        "Deprecation window",
        "Release cadence",
        "Rollback",
        "Failure ownership",
        "Package CI passes but the canary fails",
        "20m29s",
        "pzfreo/draftwright#1175",
    ):
        assert required in guide
    assert "path or Git dependency" in guide
    assert "PyPI" in guide and "SHA-256" in guide


def test_one_command_candidate_entry_point_delegates_to_package_owned_harness() -> None:
    result = subprocess.run(
        [
            # Through the interpreter, not the shebang: Windows cannot exec a
            # `#!/usr/bin/env python3` script directly (WinError 193).
            sys.executable,
            str(ROOT / "scripts" / "check-recogniser-candidate"),
            "--package",
            str(ROOT),
            "--plan",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(result.stdout)
    assert plan["owner"] == "b123d-recognisers"
    assert plan["draftwright"] == str(ROOT.resolve())
    assert plan["command"][-3:] == ["--draftwright", str(ROOT.resolve()), "--plan"]
    # PurePath, not a substring: the emitted path is native (backslashes on Windows).
    assert PurePath(plan["command"][2]).parts[-2:] == ("tools", "check_downstream.py")


def test_maintenance_dispatch_runs_one_coverage_suite_and_never_the_slow_tier() -> None:
    workflow = CI.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "maintenance_bump:" in workflow
    assert "inputs.maintenance_bump" in workflow
    assert "github.event_name == 'push'" in workflow, "slow remains post-merge only"
    assert "github.event_name == 'pull_request' || inputs.maintenance_bump" in workflow
    assert "github.event_name != 'push' && !inputs.maintenance_bump" in workflow
    assert "maintenance_pr_number" in workflow
    assert "override_pr:" in workflow
    assert "statuses: write" in workflow
    assert "statuses/${GITHUB_SHA}" in workflow
    assert "context=ci-ok" in workflow
    assert "if: inputs.maintenance_bump" in workflow


def test_dependency_bump_automation_opens_a_pr_and_dispatches_narrow_ci() -> None:
    workflow = DEPENDENCY_BUMP.read_text(encoding="utf-8")
    for permission in ("actions: write", "contents: write", "pull-requests: write"):
        assert permission in workflow
    assert "workflow_dispatch:" in workflow
    assert "scripts/update-recogniser-dependency" in workflow
    assert "scripts/check-recogniser-release" in workflow
    assert "gh pr create" in workflow
    assert "gh workflow run ci.yml" in workflow
    assert "maintenance_bump=true" in workflow
    assert 'maintenance_pr_number="$pr_number"' in workflow
    assert "git push origin HEAD:" in workflow
    assert "git push origin HEAD:main" not in workflow
    assert "git+" not in workflow and "editable = true" not in workflow


def test_post_release_version_bump_is_a_pr_not_a_protected_main_push() -> None:
    workflow = PUBLISH.read_text(encoding="utf-8")
    bump = workflow.split("  bump-version:", 1)[1]
    assert "pull-requests: write" in bump and "actions: write" in bump
    assert "gh pr create" in bump
    assert "gh workflow run ci.yml" in bump
    assert "maintenance_bump=true" in bump
    assert 'maintenance_pr_number="$pr_number"' in bump
    assert "git push origin HEAD:" in bump
    assert "git push\n" not in bump


@pytest.mark.parametrize("target", ("0.4.7", "0.4.8.dev0", "0.4.8.dev123"))
def test_version_updater_keeps_release_and_development_identity_atomic(
    tmp_path: Path, target: str
) -> None:
    for relative in (
        "pyproject.toml",
        "uv.lock",
        "src/draftwright/recogniser_contract.py",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)

    loader = SourceFileLoader(
        "draftwright_version_update",
        str(ROOT / "scripts/update-draftwright-version"),
    )
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    module.update(tmp_path, target)

    assert f'version = "{target}"' in (tmp_path / "pyproject.toml").read_text()
    lock = (tmp_path / "uv.lock").read_text()
    package = lock.split('[[package]]\nname = "draftwright"', 1)[1].split("[[package]]", 1)[0]
    assert f'version = "{target}"' in package
    contract = (tmp_path / "src/draftwright/recogniser_contract.py").read_text()
    assert contract.count(f'"consumer": {{"name": "draftwright", "version": "{target}"}}') == 1


def test_current_release_lock_has_machine_checked_registry_evidence() -> None:
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert lock.count('name = "b123d-recognisers"') > 1, (
        "the checker must distinguish the package block from dependency-table references"
    )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check-recogniser-release")], cwd=ROOT, check=True
    )


@pytest.mark.parametrize("heading", ("## Unreleased", "## [Unreleased]"))
def test_dependency_updater_reuses_existing_unreleased_changed_heading(heading: str) -> None:
    loader = SourceFileLoader(
        "draftwright_recogniser_dependency_update",
        str(ROOT / "scripts/update-recogniser-dependency"),
    )
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        changelog = root / "CHANGELOG.md"
        changelog.write_text(
            f"# Changelog\n\n{heading}\n\n### Changed\n\n- Existing change.\n\n"
            "### Fixed\n\n- Existing fix.\n\n## [0.1.0]\n",
            encoding="utf-8",
        )
        module._add_changelog(root, "0.2.0", "0.2.1", "a" * 64)
        updated = changelog.read_text(encoding="utf-8")
        assert updated.count("### Changed") == 1
        assert "0.2.0 to 0.2.1" in updated
        assert updated.index("0.2.0 to 0.2.1") < updated.index("### Fixed")


def test_dependency_updater_starts_the_next_unreleased_section_after_a_release() -> None:
    loader = SourceFileLoader(
        "draftwright_recogniser_dependency_update_after_release",
        str(ROOT / "scripts/update-recogniser-dependency"),
    )
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        changelog = root / "CHANGELOG.md"
        changelog.write_text(
            "# Changelog\n\n## v0.4.9 — 2026-08-21\n\n### Fixed\n\n- Released fix.\n",
            encoding="utf-8",
        )

        module._add_changelog(root, "0.2.6", "0.2.8", "a" * 64)
        module._add_changelog(root, "0.2.6", "0.2.8", "a" * 64)

        updated = changelog.read_text(encoding="utf-8")
        assert updated.count("## Unreleased") == 1
        assert updated.count("0.2.6 to 0.2.8") == 1
        assert updated.index("## Unreleased") < updated.index("## v0.4.9")
        assert updated.index("0.2.6 to 0.2.8") < updated.index("## v0.4.9")


@pytest.mark.parametrize("target", ("0.3.0", "0.3.1", "1.0.0"))
def test_dependency_updater_closes_an_adopted_schema_transition(
    tmp_path: Path, target: str
) -> None:
    loader = SourceFileLoader(
        "draftwright_recogniser_schema_transition_update",
        str(ROOT / "scripts/update-recogniser-dependency"),
    )
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    contract = tmp_path / "recogniser_contract.py"
    contract.write_text(
        '_CANDIDATE_PACKAGE_VERSION: str | None = "0.3.0"\n'
        "_RECORD_SCHEMA_VERSIONS = {\n"
        '    ("chamfers", "Chamfer"): (1, 2),\n'
        '    ("fillets", "Fillet"): (1, 2),\n'
        "}\n",
        encoding="utf-8",
    )

    module._close_schema_transition(contract, "0.2.10")
    assert '("chamfers", "Chamfer"): (1, 2)' in contract.read_text(encoding="utf-8")

    module._close_schema_transition(contract, target)
    narrowed = contract.read_text(encoding="utf-8")
    assert "_CANDIDATE_PACKAGE_VERSION: str | None = None" in narrowed
    assert '("chamfers", "Chamfer"): (2,)' in narrowed
    assert '("fillets", "Fillet"): (2,)' in narrowed

    module._close_schema_transition(contract, "0.3.0")
    assert contract.read_text(encoding="utf-8") == narrowed


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("bad-version", "invalid version"),
        ("bad-distribution", "invalid distribution"),
        ("bad-index", "canonical PyPI index"),
        ("bad-manifest-digest", "invalid capability-manifest digest"),
        ("missing-wheel", "wheel and sdist"),
        ("dependency-drift", "pyproject dependency"),
        ("contract-drift", "consumer capability"),
        ("mutable-source", "registry-backed"),
        ("hash-drift", "artifact names/hashes"),
        ("duplicate-block", "exactly one recogniser package block"),
        ("editable-install", "installed registry package"),
        ("manifest-drift", "installed registry package"),
    ],
)
def test_release_evidence_fails_closed_at_each_independent_boundary(
    tmp_path: Path, mutation: str, message: str
) -> None:
    for relative in (
        ".github/recogniser-release.json",
        "pyproject.toml",
        "src/draftwright/recogniser_contract.py",
        "uv.lock",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)

    evidence_path = tmp_path / ".github/recogniser-release.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    current = evidence["version"]
    installed = {
        "version": evidence["version"],
        "direct_url": None,
        "manifest_sha256": evidence["capability_manifest_sha256"],
    }
    if mutation == "bad-version":
        evidence["version"] = "v0.2"
    elif mutation == "bad-distribution":
        evidence["distribution"] = "lookalike"
    elif mutation == "bad-index":
        evidence["index"] = "https://example.invalid/simple"
    elif mutation == "bad-manifest-digest":
        evidence["capability_manifest_sha256"] = "not-a-digest"
        installed["manifest_sha256"] = "not-a-digest"
    elif mutation == "missing-wheel":
        evidence["artifacts"] = {
            name: digest
            for name, digest in evidence["artifacts"].items()
            if not name.endswith(".whl")
        }
    elif mutation == "dependency-drift":
        path = tmp_path / "pyproject.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                f'"b123d-recognisers=={current}"',
                '"b123d-recognisers @ git+https://invalid"',
            ),
            encoding="utf-8",
        )
    elif mutation == "contract-drift":
        path = tmp_path / "src/draftwright/recogniser_contract.py"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                f'_PACKAGE_VERSION = "{current}"', '_PACKAGE_VERSION = "99.99.99"'
            ),
            encoding="utf-8",
        )
    elif mutation == "mutable-source":
        path = tmp_path / "uv.lock"
        lock = path.read_text(encoding="utf-8")
        start = lock.index('[[package]]\nname = "b123d-recognisers"')
        tail = lock[start:]
        tail = tail.replace(
            'source = { registry = "https://pypi.org/simple" }',
            'source = { git = "https://invalid" }',
            1,
        )
        path.write_text(lock[:start] + tail, encoding="utf-8")
    elif mutation == "hash-drift":
        evidence["artifacts"][next(iter(evidence["artifacts"]))] = "0" * 64
    elif mutation == "duplicate-block":
        path = tmp_path / "uv.lock"
        lock = path.read_text(encoding="utf-8")
        start = lock.index('[[package]]\nname = "b123d-recognisers"')
        end = lock.index("\n[[package]]", start + 1)
        path.write_text(lock + "\n" + lock[start:end] + "\n", encoding="utf-8")
    elif mutation == "editable-install":
        installed["direct_url"] = '{"dir_info":{"editable":true}}'
    else:
        installed["manifest_sha256"] = "f" * 64
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    loader = SourceFileLoader(
        "draftwright_recogniser_release_check",
        str(ROOT / "scripts/check-recogniser-release"),
    )
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    with pytest.raises(SystemExit, match=message):
        module.verify(tmp_path, installed)
