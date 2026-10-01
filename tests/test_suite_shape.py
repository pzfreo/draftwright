"""Ratchet: the count of issue-named test modules may only SHRINK (#1637).

64% of this suite's modules are named `test_issue_NNNN*`, and `git log --diff-filter=R`
shows none has ever been renamed. The cost is not cosmetic: the same behaviour ends up
tested in a dozen places that never see each other, which is how thirteen copies of one
three-statement body reached `CLONE_BUDGET` unnoticed. One issue family alone (#1438) is
13 modules and 4,823 lines, and release 0.4.29 added twelve more issue-named modules in a
single release (182 at `v0.4.28`, 194 at `v0.4.29`, measured with
`git ls-tree --name-only <tag> tests/ | grep -c '^tests/test_issue'`).

**The rule (maintainer decision, 2026-09-13):** a regression test goes in the module named
after the BEHAVIOUR it defends — `tests/test_channels.py`, `tests/test_strip_layout.py` —
as `test_<behaviour>_issue_NNNN`, with the issue number in the test name or docstring
rather than the file name. No new issue-named module in either spelling. Existing ones fold into
behaviour modules over time (`tests/issues/` was considered and rejected: it relocates the
duplication instead of removing it).

So this pins today's names and count across both `test_issue_NNNN*.py` and
`test_<behaviour>_issue_NNNN.py` and lets them only go down. A new name fails even
when another issue-named module is removed in the same change. Mirrors
`test_private_test_imports.py`. No geometry build.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from _unit_manifest import UNIT_MODULES, unit_paths

_TESTS = Path(__file__).resolve().parent

# Measured with `_issue_named_modules()` across both issue-named file spellings.
# MAY ONLY SHRINK.
_MAX_ISSUE_MODULES = 159
_ISSUE_SUFFIX = re.compile(r"_issue_\d+$")
_ISSUE_MODULE_BASELINE = _TESTS / "_issue_module_baseline.txt"
_BOOTSTRAP_ISSUE_MODULE_SHA256 = "63aa1468578467eaada65ae8978a1f2db97eefac79a3d912823da09842a0fb8f"


def test_unit_manifest_names_existing_test_modules_once():
    """Direct paths and marker selection share one complete module manifest."""
    paths = unit_paths(_TESTS)

    assert len(paths) == len(UNIT_MODULES)
    assert all(Path(path).is_file() for path in paths)
    assert all(Path(path).name.startswith("test_") for path in paths)


def _issue_named_modules() -> list[str]:
    """Every top-level issue-named module, including issue-suffixed files."""
    return sorted(
        path.name
        for path in _TESTS.glob("test_*.py")
        if path.name.startswith("test_issue_") or _ISSUE_SUFFIX.search(path.stem)
    )


def _committed_issue_module_ceiling() -> set[str] | None:
    """Read the baseline at the branch point with main, when it exists."""
    repository = _TESTS.parent
    for main_ref in ("origin/main", "main"):
        merge_base = subprocess.run(
            ["git", "merge-base", "HEAD", main_ref],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if merge_base.returncode != 0:
            continue
        committed = subprocess.run(
            ["git", "show", f"{merge_base.stdout.strip()}:tests/{_ISSUE_MODULE_BASELINE.name}"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if committed.returncode == 0:
            return set(committed.stdout.splitlines())
    return None


def test_issue_named_modules_only_shrink():
    """The suite's issue-named modules match the pin, which may only go down (#1637)."""
    found = _issue_named_modules()
    reviewed = _ISSUE_MODULE_BASELINE.read_text(encoding="utf-8").splitlines()
    assert reviewed == sorted(set(reviewed)), "Issue-module baseline must have unique sorted names"
    assert len(reviewed) == _MAX_ISSUE_MODULES
    assert found == reviewed, (
        "Issue-named modules changed. Fold old modules into behaviour-named files, "
        "then lower the reviewed filename baseline and count."
    )
    if (ceiling := _committed_issue_module_ceiling()) is None:
        fingerprint = hashlib.sha256("\n".join(reviewed).encode()).hexdigest()
        assert fingerprint == _BOOTSTRAP_ISSUE_MODULE_SHA256, (
            "Initial issue-module baseline changed; review its names and fingerprint together"
        )
    else:
        assert not (added := set(reviewed) - ceiling), (
            f"New issue-named modules are forbidden: {sorted(added)}"
        )
