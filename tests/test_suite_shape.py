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
rather than the file name. No new `test_issue_*` module. The existing ones fold into
behaviour modules over time (`tests/issues/` was considered and rejected: it relocates the
duplication instead of removing it).

So this pins today's count and lets it go only down. Adding an issue-named module fails
here; folding one in and forgetting to lower `_MAX_ISSUE_MODULES` also fails, so the pin
cannot drift away from the measurement. Mirrors `test_private_test_imports.py`.
Dependency-free (stdlib `pathlib`), no build.
"""

from __future__ import annotations

from pathlib import Path

from _unit_manifest import UNIT_MODULES, unit_paths

_TESTS = Path(__file__).resolve().parent

# Measured at fee4931 with `ls tests | grep -c '^test_issue'`. MAY ONLY SHRINK.
_MAX_ISSUE_MODULES = 151


def test_unit_manifest_names_existing_test_modules_once():
    """Direct paths and marker selection share one complete module manifest."""
    paths = unit_paths(_TESTS)

    assert len(paths) == len(UNIT_MODULES)
    assert all(Path(path).is_file() for path in paths)
    assert all(Path(path).name.startswith("test_") for path in paths)


def _issue_named_modules() -> list[str]:
    """Every top-level `tests/test_issue_*.py` module, sorted."""
    return sorted(p.name for p in _TESTS.glob("test_issue_*.py"))


def test_issue_named_modules_only_shrink():
    """The suite's issue-named modules match the pin, which may only go down (#1637)."""
    found = _issue_named_modules()
    assert len(found) <= _MAX_ISSUE_MODULES, (
        f"{len(found)} issue-named test modules, pinned at {_MAX_ISSUE_MODULES} and allowed "
        "only to shrink (#1637). Put the regression test in the module named after the "
        "behaviour it defends, as `test_<behaviour>_issue_NNNN`, rather than adding a new "
        "`test_issue_*` file."
    )
    assert len(found) == _MAX_ISSUE_MODULES, (
        f"Only {len(found)} issue-named test modules remain against a pin of "
        f"{_MAX_ISSUE_MODULES} — good, the ratchet is shrinking. Lower "
        "`_MAX_ISSUE_MODULES` to match so the pin stays honest (#1637)."
    )
