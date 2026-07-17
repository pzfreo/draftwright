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

Catches two white-box forms across ALL test-suite modules (not just ``test_*.py``): the direct
``from draftwright.annotations.<mod> import _name`` import, and module-alias attribute access
(``from draftwright.annotations import holes as h; h._name`` / ``import ....holes as h; h._name``).
**Known static-analysis gap:** fully *dynamic* access — ``sys.modules["...from_model"]._name``,
``importlib.import_module``, ``__import__`` — can't be resolved statically. One such reach exists
(``from_model._solve_strip_ys`` / ``_greedy_strip_ys``, monkeypatched via ``sys.modules`` in
``test_make_drawing``); it is documented here rather than pinned, since the scanner can't detect it.
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
        # Module-alias attribute access (`from ... import holes as h; h._annotate_holes`).
        ("holes", "_annotate_holes"),
    }
)

_ANNO = "draftwright.annotations"


def _module_aliases(tree: ast.Module) -> dict[str, str]:
    """Map each local name bound to one of the big annotation modules → that module's short name.
    Covers ``import draftwright.annotations.holes as h`` and ``from draftwright.annotations import
    holes as h`` (or ``... import holes`` — the plain name binds to the module)."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:  # e.g. draftwright.annotations.holes [as h]
                if (
                    a.name.startswith(f"{_ANNO}.")
                    and a.name.split(".")[-1] in _MODULES
                    and a.asname
                ):
                    aliases[a.asname] = a.name.split(".")[-1]
        elif isinstance(node, ast.ImportFrom) and node.module == _ANNO:
            for a in node.names:  # from draftwright.annotations import holes [as h]
                if a.name in _MODULES:
                    aliases[a.asname or a.name] = a.name
    return aliases


def _private_anno_test_imports() -> set[tuple[str, str]]:
    """Every white-box reach into a big annotation module's PRIVATE from the whole test suite:
    a ``from draftwright.annotations.<mod> import _name`` import, OR a module-alias attribute
    access ``<alias>._name`` where *alias* is bound to one of the big modules."""
    found: set[tuple[str, str]] = set()
    for path in sorted(_TESTS.glob("*.py")):
        if path.name == Path(__file__).name:
            continue  # don't scan this ratchet (it names the modules as strings)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # (a) direct `from ...annotations.<mod> import _name`
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith(f"{_ANNO}.")
                and node.module.split(".")[-1] in _MODULES
            ):
                mod = node.module.split(".")[-1]
                for alias in node.names:
                    if alias.name.startswith("_"):
                        found.add((mod, alias.name))
        # (b) module-alias attribute access `<alias>._name`
        aliases = _module_aliases(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
                and node.attr.startswith("_")
            ):
                found.add((aliases[node.value.id], node.attr))
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
