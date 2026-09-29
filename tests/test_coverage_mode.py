"""Coverage selection keeps fast PRs cheap without weakening broad changes."""

import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts/coverage-mode"
LOADER = SourceFileLoader("coverage_mode", str(SCRIPT))
SPEC = spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
coverage_mode = module_from_spec(SPEC)
LOADER.exec_module(coverage_mode)
Change = coverage_mode.Change

MOVE_SCRIPT = ROOT / "scripts/diff-cover-moves"
MOVE_LOADER = SourceFileLoader("diff_cover_moves", str(MOVE_SCRIPT))
MOVE_SPEC = spec_from_loader(MOVE_LOADER.name, MOVE_LOADER)
assert MOVE_SPEC is not None
diff_cover_moves = module_from_spec(MOVE_SPEC)
MOVE_LOADER.exec_module(diff_cover_moves)


@pytest.mark.parametrize(
    "changes",
    [
        [Change("A", ("tests/test_new_contract.py",))],
        [Change("A", ("tests/fixtures/new.step",))],
        [Change("M", ("tests/test_export.py",))],
        [Change("D", ("tests/fixtures/source.step",))],
        [Change("M", (".github/workflows/ci.yml",))],
        [Change("M", ("docs/guide.md",))],
    ],
)
def test_additive_source_neutral_changes_skip_coverage(changes):
    assert coverage_mode.classify(changes) == "skip"


@pytest.mark.parametrize(
    "changes",
    [
        [Change("M", ("src/draftwright/export.py",))],
        [Change("M", ("src/draftwright/annotations/from_model.py",))],
        [Change("M", ("src/draftwright/reporting.py",))],
        [Change("D", ("src/draftwright/export.py",))],
        [Change("M", ("src/draftwright/builder.py",))],
        [Change("A", ("src/draftwright/new_area.py",))],
        [Change("R", ("src/draftwright/export.py", "src/draftwright/exporter.py"))],
    ],
)
def test_every_production_change_uses_changed_area_coverage(changes):
    assert coverage_mode.classify(changes) == "changed"


def test_full_coverage_label_overrides_a_source_neutral_diff():
    changes = [Change("A", ("tests/fixtures/new.step",))]

    assert coverage_mode.classify(changes, force_full=True) == "full"


def test_coverage_exempts_only_an_exact_block_deleted_from_its_source():
    block = ["one", "two", "three", "four", "five"]
    old = ["before", *block, "after"]
    added = ["new", *block, "changed"]
    current = ["before", "after"]

    removed = diff_cover_moves._deleted_indices(old, current)
    assert diff_cover_moves._moved_lines(old, current, removed, added) == set(range(2, 7))
    assert diff_cover_moves._moved_lines(old, old, set(), added) == set()
    assert diff_cover_moves._moved_lines(old, current, removed, ["new", *block[:4]]) == set()


def test_changed_or_duplicated_code_stays_in_the_coverage_gate():
    block = ["one", "two", "three", "four", "five"]
    old = ["before", *block, "after"]
    current = ["before", "after", *block]

    assert diff_cover_moves._moved_lines(old, current, set(range(1, 6)), block) == set()
    assert (
        diff_cover_moves._moved_lines(
            old, ["before", "after"], set(range(1, 6)), ["one", "two", "new", "four", "five"]
        )
        == set()
    )


def test_diff_parser_keeps_added_lines_that_resemble_file_headers():
    patch = (
        "diff --git a/src/draftwright/sample.py b/src/draftwright/sample.py\n"
        "--- a/src/draftwright/sample.py\n"
        "+++ b/src/draftwright/sample.py\n"
        "@@ -1,0 +2,2 @@\n"
        "+++ b/src/draftwright/fake.py\n"
        "+value = 1\n"
    )

    assert diff_cover_moves._changed_lines(patch) == {"src/draftwright/sample.py": {2, 3}}


@pytest.mark.parametrize(
    ("name", "existing"),
    [
        ("with space.py", False),
        ("café.py", False),
        ("has\ttab.py", False),
        ('has"quote.py', False),
        ("space \t.py", False),
        ("has\ttab.py", True),
    ],
)
def test_quoted_git_paths_fail_closed_in_changed_line_gate(tmp_path, monkeypatch, name, existing):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Coverage Test")
    git("config", "user.email", "coverage@example.test")
    git("config", "core.quotePath", "true")
    source = tmp_path / "src" / "draftwright" / name
    source.parent.mkdir(parents=True)
    (tmp_path / "README.md").write_text("baseline\n", encoding="utf-8")
    if existing:
        source.write_text("value = 0\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    source.write_text("value = 1\n", encoding="utf-8")
    if not existing:
        git("add", "--", str(source.relative_to(tmp_path)))

    monkeypatch.setattr(diff_cover_moves, "ROOT", tmp_path)
    patch = diff_cover_moves._git(
        "diff", "--no-color", "--no-ext-diff", "--no-renames", "-U0", base
    )
    changed = diff_cover_moves._changed_lines(patch)
    added, changed_old = diff_cover_moves._source_changes(base)
    assert str(source.relative_to(tmp_path)) in (changed_old if existing else added)
    assert changed == {}
    with pytest.raises(ValueError, match="Git diff omitted changed production paths"):
        diff_cover_moves._require_all_changed_sources(changed, added, changed_old)
    monkeypatch.setattr(
        sys,
        "argv",
        ["diff-cover-moves", "coverage.xml", "--compare-branch", base, "--fail-under=90"],
    )
    with pytest.raises(ValueError, match="Git diff omitted changed production paths"):
        diff_cover_moves.main()
