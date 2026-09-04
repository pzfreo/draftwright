"""The five live ADRs stay short, guarded and the only cited authority.

The previous corpus reached 21 files and 8,787 lines, and its own "write a successor at
roughly four amendments" rule was a convention nothing enforced — one record reached 28. These
are the rules `docs/adr/README.md` states, made executable. Sizes are prose lines: the
Superseded and Open sections are excluded so history pointers and honest gaps never compete
with invariants for space.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_ADR = _ROOT / "docs" / "adr"
_ARCHIVE = _ADR / "archive"
_TESTS = _ROOT / "tests"

_LIVE = sorted(p for p in _ADR.glob("000[1-5]-*.md"))
_PER_RECORD_CAP = 200
_TOTAL_CAP = 1_000

# A bare citation of an archived number. `ADR 3 (was 0017 Amdt 12)` is the sanctioned pointer
# form and does not match, because the number is not directly preceded by "ADR".
_BARE_OLD = re.compile(r"\bADR[ -]?00(?:0[1-9]|1[0-9]|20)\b")
_TEST_MODULE = re.compile(r"`(test_[a-z0-9_]+\.py)`")
_TEST_FUNC = re.compile(r"`(test_[a-z0-9_]+)`(?!\.py)")


def _prose_lines(text: str) -> int:
    """Lines outside the Superseded and Open sections, which are exempt from the cap."""

    count = 0
    exempt = False
    for line in text.splitlines():
        if line.startswith("## "):
            exempt = line.startswith(("## Superseded", "## Open"))
        if not exempt:
            count += 1
    return count


def test_exactly_five_live_records_exist():
    assert [p.name[:4] for p in _LIVE] == ["0001", "0002", "0003", "0004", "0005"]
    stray = sorted(p.name for p in _ADR.glob("*.md") if p not in _LIVE and p.name != "README.md")
    assert not stray, f"only the five records and README live in docs/adr/: {stray}"


@pytest.mark.parametrize("record", _LIVE, ids=lambda p: p.name[:4])
def test_each_live_record_is_within_its_cap(record: Path):
    count = _prose_lines(record.read_text(encoding="utf-8"))
    assert count <= _PER_RECORD_CAP, f"{record.name}: {count} prose lines > {_PER_RECORD_CAP}"


def test_the_five_together_are_within_the_total_cap():
    total = sum(_prose_lines(p.read_text(encoding="utf-8")) for p in _LIVE)
    assert total <= _TOTAL_CAP, f"{total} prose lines across the five records > {_TOTAL_CAP}"


@pytest.mark.parametrize("record", _LIVE, ids=lambda p: p.name[:4])
def test_each_live_record_has_the_required_sections(record: Path):
    text = record.read_text(encoding="utf-8")
    for heading in ("## Decision", "## Invariants", "## Boundaries", "## Superseded", "## Open"):
        assert heading in text, f"{record.name} lacks {heading}"


@pytest.mark.parametrize("record", _LIVE, ids=lambda p: p.name[:4])
def test_every_cited_guard_exists(record: Path):
    text = record.read_text(encoding="utf-8")
    modules = set(_TEST_MODULE.findall(text))
    missing_modules = sorted(m for m in modules if not (_TESTS / m).exists())
    assert not missing_modules, f"{record.name} cites absent test modules: {missing_modules}"

    functions = set(_TEST_FUNC.findall(text))
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in _TESTS.glob("test_*.py"))
    missing_functions = sorted(
        f for f in functions if not re.search(rf"\bdef {re.escape(f)}\b", corpus)
    )
    assert not missing_functions, f"{record.name} cites absent tests: {missing_functions}"


@pytest.mark.parametrize("record", _LIVE, ids=lambda p: p.name[:4])
def test_every_numbered_invariant_names_a_guard(record: Path):
    """An invariant with no test belongs under *Unguarded*, not in the numbered list."""

    text = record.read_text(encoding="utf-8")
    section = text.split("## Invariants", 1)[1].split("## Boundaries", 1)[0]
    body = section.split("**Unguarded.**", 1)[0]
    items = re.split(r"\n(?=\d+\. \*\*)", body.strip())
    numbered = [item for item in items if re.match(r"\d+\. \*\*", item)]
    assert numbered, f"{record.name} has no numbered invariants"
    unguarded = [item.split("**")[1] for item in numbered if not _TEST_MODULE.search(item)]
    assert not unguarded, f"{record.name} numbered invariants without a guard: {unguarded}"


def test_no_live_document_cites_an_archived_record_as_authority():
    offenders: list[str] = []
    roots = [
        _ROOT / "src",
        _ROOT / "tests",
        _ROOT / "docs",
        _ROOT / "AGENTS.md",
        _ROOT / "CLAUDE.md",
        _ROOT / "README.md",
    ]
    for root in roots:
        files = root.rglob("*") if root.is_dir() else [root]
        for path in files:
            if not path.is_file() or path.suffix not in {".py", ".md"}:
                continue
            if _ARCHIVE in path.parents:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _BARE_OLD.search(line):
                    offenders.append(f"{path.relative_to(_ROOT)}:{number}: {line.strip()[:80]}")
    assert not offenders, "cite ADR 1-5, with `(was 00NN)` for history:\n" + "\n".join(offenders)


def test_the_archive_is_complete_and_stamped():
    names = sorted(p.name[:4] for p in _ARCHIVE.glob("00*.md"))
    assert names == [f"{n:04d}" for n in range(1, 21)]
    for path in _ARCHIVE.glob("00*.md"):
        head = path.read_text(encoding="utf-8").split("\n", 4)
        assert any("**Archived 2026-09-04.**" in line for line in head), path.name
