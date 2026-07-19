"""Small drift guards for documents that present current architecture.

Frozen ADRs and historical roadmaps are deliberately outside this scope.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (_ROOT / relative).read_text()


def _prose(relative: str) -> str:
    """Read prose with Markdown wrapping made irrelevant to phrase assertions."""
    return " ".join(_read(relative).split())


def test_live_architecture_docs_name_current_compiler_authority():
    target = _prose("docs/target-architecture.md")
    assert "current compiler contract; ADR 0008 is its frozen historical" in target
    assert "target state defined by [ADR 0008]" not in target


def test_live_architecture_docs_record_planner_convergence_complete():
    target = _prose("docs/target-architecture.md")
    adr = _prose("docs/adr/0015-part-drawing-compiler-as-built.md")
    claude = _prose("CLAUDE.md")
    assert "#698 completed" in target
    assert "Planner convergence #698 is complete" in claude
    assert "convergence is now complete" in adr
    assert "while #698 proceeds" not in adr
    assert "convergence tracked by #698" not in claude


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
    assert "amending ADR 0014" in guard
    assert "amendment in ADR 0009" not in guard.lower()
