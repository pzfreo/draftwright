"""Ratchet: tests may import only a DOCUMENTED, shrinking set of annotation-layer privates.

The big annotation modules (``from_model``/``holes``/``sections``/``orchestrator``) carry many
private helpers. Tests that import them directly (white-box) break on a harmless rename and
under-pin the *public* seam relative to the helpers (#641 gap 2). Now that #639 built the
``PlacementContext`` seam, this pins today's white-box test-imports as an allowlist that may only
**SHRINK**: a NEW private-helper test-import fails here (nudging the author toward the public/ctx
seam), and removing one (as a test is retargeted) must be reflected by dropping the entry — so the
count can only go down.

Some pinned entries are legitimate pure-helper unit tests (label formatters, span math) whose
direct coverage is worth keeping; the ratchet's job is to stop the surface *growing*, not to force
every helper test onto the public seam. Mirrors ``test_import_boundaries`` / ``test_drawing_encapsulation``.
Dependency-free (stdlib ``ast`` + ``pathlib``).
"""

from __future__ import annotations

import ast
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
# The big annotation modules whose privates tests reach into (under draftwright.annotations.*).
_MODULES = {"from_model", "holes", "sections", "orchestrator"}

# Pinned (module, private_name) white-box test-imports. May only SHRINK (#641 gap 2).
_ALLOW: frozenset[tuple[str, str]] = frozenset(
    {
        # Pure label/format helpers — legitimate unit tests (formatting logic, not coupling).
        ("from_model", "_chamfer_label"),
        ("from_model", "_fillet_label"),
        ("from_model", "_flat_label"),
        ("from_model", "_groove_label"),
        # Pure geometry/selection helpers with unit-level coverage.
        ("from_model", "_bore_half_span"),
        ("from_model", "_diameter_column_left"),
        ("from_model", "_renderable_pmi_records"),
        ("from_model", "_envelope_tier"),
        ("holes", "_legible_locations"),
        # Pass-level helpers exercised directly (candidates to retarget onto the ctx seam later).
        ("from_model", "_draw_step_chain"),
        ("from_model", "_record_slot_drop"),
        ("from_model", "_record_pmi_drop"),
        ("holes", "_record_callout_drop"),
        ("holes", "_place_pitch_dim"),
        ("sections", "_section_hatch_edges"),
        ("sections", "_request_prismatic_detail"),
        ("orchestrator", "_maybe_tabulate_holes"),
    }
)


def _private_anno_test_imports() -> set[tuple[str, str]]:
    """Every ``from draftwright.annotations.<mod> import _name`` in the test suite, where *mod*
    is one of the big modules and *_name* is private (leading underscore)."""
    found: set[tuple[str, str]] = set()
    for path in sorted(_TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module):
                continue
            if not node.module.startswith("draftwright.annotations."):
                continue
            mod = node.module.split(".")[-1]
            if mod not in _MODULES:
                continue
            for alias in node.names:
                if alias.name.startswith("_"):
                    found.add((mod, alias.name))
    return found


def test_private_anno_test_imports_only_shrink():
    """The white-box private-helper imports tests make from the big annotation modules match the
    documented allowlist, which may only SHRINK (#641 gap 2)."""
    found = _private_anno_test_imports()
    new = found - _ALLOW
    assert not new, (
        "New white-box private import(s) from annotations/ in tests — prefer the public / "
        "PlacementContext seam (#641 gap 2). This allowlist may only shrink; if a direct import is "
        f"truly warranted, add it with a rationale in _ALLOW: {sorted(new)}"
    )
    stale = sorted(_ALLOW - found)
    assert not stale, (
        "Allowlisted private test-import(s) no longer used — good, the ratchet is shrinking. "
        f"Remove them from _ALLOW to keep it honest (#641 gap 2): {stale}"
    )
