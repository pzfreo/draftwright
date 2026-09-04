"""Drift guard for documents that present current architecture.

Frozen ADRs and historical roadmaps are deliberately outside this scope.

One live editorial rule survives the #1222 guard audit: current-architecture
documents must not cite source line numbers, which rot on every edit. The
former phrase-absence assertions (stale ADR 1 (was 0008) / ADR 2 (was 0009) references) guarded
already-won battles — the phrasing they policed has been gone for weeks and
could only return through a deliberate edit that review would see — and were
retired by the audit.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_current_architecture_docs_have_no_source_line_anchors():
    for relative in (
        "docs/architecture.md",
        "docs/adr/0001-compiler-pipeline.md",
        "docs/adr/0002-sheet-layout-and-view-planning.md",
        "docs/adr/0003-recognition-boundary.md",
        "docs/adr/0004-declared-intent.md",
        "docs/adr/0005-trust-and-honest-failure.md",
    ):
        text = (_ROOT / relative).read_text(encoding="utf-8")
        assert "orchestrator.py:" not in text, relative
