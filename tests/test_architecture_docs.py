"""Drift guard for documents that present current architecture.

Frozen ADRs and historical roadmaps are deliberately outside this scope.

One live editorial rule survives the #1222 guard audit: current-architecture
documents must not cite source line numbers, which rot on every edit. The
former phrase-absence assertions (stale ADR 0008/0009 references) guarded
already-won battles — the phrasing they policed has been gone for weeks and
could only return through a deliberate edit that review would see — and were
retired by the audit.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_current_architecture_docs_have_no_source_line_anchors():
    for relative in (
        "docs/target-architecture.md",
        "docs/adr/0011-ir-as-public-input.md",
        "docs/adr/0015-part-drawing-compiler-as-built.md",
    ):
        text = (_ROOT / relative).read_text(encoding="utf-8")
        assert "orchestrator.py:" not in text, relative
