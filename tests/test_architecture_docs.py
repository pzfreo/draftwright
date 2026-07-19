"""Small drift guards for documents that present current architecture.

Frozen ADRs and historical roadmaps are deliberately outside this scope.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (_ROOT / relative).read_text()


def test_live_architecture_docs_name_current_compiler_authority():
    target = _read("docs/target-architecture.md")
    readme = _read("README.md")
    assert "target state defined by [ADR 0008]" not in target
    assert "part-drawing compiler** (ADR 0008)" not in readme


def test_live_architecture_docs_record_planner_convergence_complete():
    adr = _read("docs/adr/0015-part-drawing-compiler-as-built.md")
    claude = _read("CLAUDE.md")
    assert "while #698 proceeds" not in adr
    assert "convergence tracked by #698" not in claude
    assert "deliberately does **not** claim that work done" not in adr


def test_current_architecture_docs_have_no_source_line_anchors():
    for relative in (
        "docs/target-architecture.md",
        "docs/adr/0011-ir-as-public-input.md",
        "docs/adr/0015-part-drawing-compiler-as-built.md",
    ):
        text = _read(relative)
        assert "orchestrator.py:" not in text, relative


def test_carve_guard_points_to_current_placement_adr():
    guard = _read("tests/test_carve_free_position_callers.py")
    assert "ADR 0009's remaining-migration note" not in guard
    assert "Pending migration (#636)" not in guard
