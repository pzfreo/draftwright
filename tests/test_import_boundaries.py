"""Import-boundary guards — the whole-package DAG, machine-enforced (#640 / ADR 0005/0008).

CLAUDE.md's **## Architecture** section declares a layered DAG: leaf modules →
``_core`` → the core-consumers (``linting``/``pmi``/``export``/``repair``/``projection``/
``compose``) → ``analysis`` → the ``annotations`` render layer → ``drawing`` → ``builder``
→ the user-facing facades/``cli``. No lower layer may import an upper one. Before #640 this
was asserted in prose but machine-enforced only for the ``model/`` IR waist; a real
regression (an upward import) could land and only be noticed by a human reading the map.

This file enforces the whole DAG:

- :func:`test_no_upward_runtime_imports` — every MODULE-LEVEL runtime import a file makes
  must land in its own layer or below (:data:`_LAYERS`). Fail-closed: a module absent from
  the layer table trips :func:`test_every_module_is_ranked`, so a new top-level module forces
  a placement decision rather than sliding in unranked.
- :func:`test_no_module_level_import_cycles` — the module-level runtime import graph is
  acyclic. Lazy (in-function) imports are excluded by construction: they are the sanctioned
  cycle-breakers (``builder`` ↔ ``cli``, both lazy — #313/#523), not invisible edges.
- :func:`test_type_checking_upward_refs_are_allowlisted` — a ``TYPE_CHECKING`` import that
  points UP the DAG (no runtime dependency, but still a design smell) must be an explicit,
  reasoned entry in :data:`_TC_UPWARD_ALLOW`. Today there is exactly one: ``_core`` type-
  references ``compose.StripDepths``.

The ``model/`` guards below (the original #584 WP2 waist checks) are kept: they add the
relative-import rejection the layer guard does not, and pin the IR-waist leaf allowlist.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "draftwright"
_MODEL_DIR = _SRC / "model"

# ── The declared DAG (mirrors CLAUDE.md ## Architecture) ─────────────────────────────────
# Rank each top-level submodule (and subpackage) by its layer; a file may import only names
# at its own rank or lower. Keep this in step with the CLAUDE.md architecture section — the
# two are the same source of truth, and test_every_module_is_ranked fails if a module here is
# missing so the table can't silently drift from the tree.
_LAYERS: dict[str, int] = {
    # 0 — leaves: import nothing from draftwright (or only same-rank leaves / the IR waist)
    "_geometry": 0,
    "fits": 0,
    "fonts": 0,
    "layout": 0,
    "registry": 0,
    "intents": 0,
    "recognition": 0,
    "sheet_dsl": 0,  # frozen deprecation shim (renamed → sheet.py, #640)
    "model": 0,  # the ADR 0008 IR waist — depends only on rank-0 leaves (guarded below too)
    # 1 — the shared drawing/layout primitives
    "_core": 1,
    # 2 — core-consumers: depend on _core, sit below the stages
    "linting": 2,
    "pmi": 2,
    "export": 2,
    "repair": 2,
    "projection": 2,
    "compose": 2,
    # 3 — analysis (feature/geometry analysis over the model + core-consumers)
    "analysis": 3,
    # 4 — the annotation render layer (+ the thin annotate re-export facade)
    "annotations": 4,
    "annotate": 4,
    # 5 — the Drawing result object
    "drawing": 5,
    # 6 — build orchestration
    "builder": 6,
    # 7 — the user-facing surfaces
    "make_drawing": 7,
    "sheet": 7,
    "sheet_emit": 7,
    "cli": 7,
    "score": 7,
    # 8 — the package root: the public API surface, above everything
    "__init__": 8,
}

# TYPE_CHECKING-only imports that point UP the DAG. No runtime dependency (the import never
# executes), but recorded explicitly so the upward *type* reference is a deliberate, reviewed
# exception, not an invisible one. Each entry is (importer_submodule, imported_submodule).
_TC_UPWARD_ALLOW: dict[tuple[str, str], str] = {
    ("_core", "compose"): (
        "_core type-annotates Analysis.layout_strips as compose.StripDepths; StripDepths is a "
        "compose (outer-layout) concept, so the reference is type-only under TYPE_CHECKING. "
        "Move it down or keep this documented (#640)."
    ),
}


def _submodule_of(path: Path) -> str:
    """The layer key for a source file: its top-level submodule name (the ``<name>`` in
    ``draftwright.<name>``), or ``"__init__"`` for the package root."""
    rel = path.relative_to(_SRC)
    first = rel.parts[0]
    return first[:-3] if first.endswith(".py") else first


def _names(node: ast.AST) -> set[str]:
    """The ``draftwright.<name>`` submodules an import node references (absolute only)."""
    out: set[str] = set()
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        parts = node.module.split(".")
        if parts[0] == "draftwright" and len(parts) > 1:
            out.add(parts[1])
    elif isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] == "draftwright" and len(parts) > 1:
                out.add(parts[1])
    return out


def _module_imports(path: Path) -> tuple[set[str], set[str], set[str]]:
    """Split a file's draftwright imports into (module-level runtime, TYPE_CHECKING, lazy):

    - **module-level runtime** — top-level ``import`` statements that execute at import time;
      these are the edges the layer DAG and cycle guards police.
    - **TYPE_CHECKING** — inside an ``if TYPE_CHECKING:`` block; never executed.
    - **lazy** — inside a function/method body; deferred, the sanctioned cycle-breakers.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    type_checking: set[str] = set()
    module_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            for n in ast.walk(node):
                type_checking |= _names(n)
        else:
            module_level |= _names(node)
    lazy: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for n in ast.walk(node):
                lazy |= _names(n)
    return module_level, type_checking, lazy


def _all_sources() -> list[Path]:
    return [p for p in sorted(_SRC.rglob("*.py")) if "__pycache__" not in p.parts]


def test_every_module_is_ranked():
    """Fail-closed: every submodule (and every submodule anything imports) is in _LAYERS, so
    a new top-level module can't slip in unranked and dodge the DAG guard."""
    seen: set[str] = set()
    for path in _all_sources():
        seen.add(_submodule_of(path))
        ml, tc, lazy = _module_imports(path)
        seen |= ml | tc | lazy
    missing = seen - set(_LAYERS)
    assert not missing, (
        "Unranked submodule(s) — add them to _LAYERS (and CLAUDE.md ## Architecture) so the "
        f"DAG guard covers them: {sorted(missing)}"
    )


def test_no_upward_runtime_imports():
    """No file imports a module ABOVE its own layer at module scope (the DAG, machine-checked)."""
    offenders: list[str] = []
    for path in _all_sources():
        sm = _submodule_of(path)
        my = _LAYERS[sm]
        module_level, _tc, _lazy = _module_imports(path)
        for imp in sorted(module_level):
            if imp == sm:
                continue  # same-submodule (intra-package) import
            if _LAYERS[imp] > my:
                offenders.append(
                    f"{path.relative_to(_SRC)} ({sm}, L{my}) imports {imp} (L{_LAYERS[imp]}) — upward"
                )
    assert not offenders, (
        "Upward cross-layer import(s) break the declared DAG (CLAUDE.md ## Architecture / ADR "
        "0005). Move the dependency down, defer it to a lazy in-function import (a documented "
        "cycle-breaker), or re-layer with a reason:\n  " + "\n  ".join(offenders)
    )


def test_no_module_level_import_cycles():
    """The module-level runtime import graph is acyclic. Lazy in-function imports are excluded
    — they are the sanctioned cycle-breakers (builder ↔ cli, #313/#523), not hidden edges."""
    graph: dict[str, set[str]] = {}
    for path in _all_sources():
        sm = _submodule_of(path)
        module_level, _tc, _lazy = _module_imports(path)
        graph.setdefault(sm, set()).update(i for i in module_level if i != sm)

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = dict.fromkeys(graph, WHITE)
    cycles: list[list[str]] = []

    def visit(node: str, stack: list[str]) -> None:
        colour[node] = GREY
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if colour.get(nxt, WHITE) == GREY:
                cycles.append(stack[stack.index(nxt) :] + [nxt])
            elif colour.get(nxt, WHITE) == WHITE:
                visit(nxt, stack)
        stack.pop()
        colour[node] = BLACK

    for node in sorted(graph):
        if colour[node] == WHITE:
            visit(node, [])
    assert not cycles, (
        "Module-level import cycle(s) — break with a lazy in-function import at one edge "
        f"(the documented pattern): {cycles}"
    )


def test_type_checking_upward_refs_are_allowlisted():
    """A TYPE_CHECKING import pointing up the DAG must be an explicit, reasoned exception."""
    offenders: list[str] = []
    for path in _all_sources():
        sm = _submodule_of(path)
        my = _LAYERS[sm]
        _ml, tc, _lazy = _module_imports(path)
        for imp in sorted(tc):
            if imp != sm and _LAYERS[imp] > my and (sm, imp) not in _TC_UPWARD_ALLOW:
                offenders.append(f"{sm} → {imp} (TYPE_CHECKING, upward)")
    assert not offenders, (
        "Undocumented upward TYPE_CHECKING reference(s) — move the type down or add a reasoned "
        f"entry to _TC_UPWARD_ALLOW: {offenders}"
    )


def test_layer_guard_detects_a_synthetic_upward_import():
    """The DAG guard is not a tautology: a fabricated upward import is caught."""
    # export (L2) importing builder (L6) would be a gross upward violation.
    src = "from draftwright.builder import build_drawing\n"
    ml = {n for node in ast.parse(src).body for n in _names(node)}
    assert "builder" in ml and _LAYERS["builder"] > _LAYERS["export"]


# ── model/ IR-waist guards (original #584 WP2 — kept; add the relative-import rejection) ──

_MODEL_MAY_IMPORT = {
    "_geometry",
    "fits",
    "fonts",
    "layout",
    "model",
    "recognition",
}


def _draftwright_imports(path: Path) -> tuple[set[str], list[str]]:
    """The top-level ``draftwright.<name>`` submodules a source file imports, and any
    relative imports it uses (which the resolver deliberately refuses to interpret)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    submodules: set[str] = set()
    relative: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level > 0:
                relative.append(node.module or "(bare relative import)")
            elif node.module:
                submodules |= _names(node)
        elif isinstance(node, ast.Import):
            submodules |= _names(node)
    return submodules, relative


def test_model_imports_only_allowed_leaves():
    """No file under ``model/`` imports outside the leaf allowlist (fail-closed)."""
    offenders: dict[str, set[str]] = {}
    relatives: dict[str, list[str]] = {}
    for path in sorted(_MODEL_DIR.glob("*.py")):
        submodules, relative = _draftwright_imports(path)
        bad = submodules - _MODEL_MAY_IMPORT
        if bad:
            offenders[path.name] = bad
        if relative:
            relatives[path.name] = relative
    assert not offenders, (
        "model/ (the IR waist) may only import leaf modules "
        f"{sorted(_MODEL_MAY_IMPORT)} (ADR 0008; #584 WP2). Disallowed: {offenders}"
    )
    assert not relatives, (
        "model/ must use absolute imports so the boundary guard can resolve them "
        f"(#584 WP2). Relative imports found: {relatives}"
    )


def test_guard_catches_absolute_and_relative_forbidden_imports():
    """The extractor is not a tautology: it flags every form a regression could take."""
    src = (
        "from draftwright._core import HoleRef\n"
        "import draftwright.sheet\n"
        "from .._core import _xyz\n"
        "from .. import analysis\n"
    )
    submodules: set[str] = set()
    relative: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            if node.level > 0:
                relative.append(node.module or "(bare)")
            else:
                submodules |= _names(node)
        elif isinstance(node, ast.Import):
            submodules |= _names(node)
    assert submodules == {"_core", "sheet"}  # both absolute forms caught
    assert len(relative) == 2  # both relative forms flagged, not silently missed


def test_geometry_is_a_leaf():
    """``_geometry`` is the bottom of the DAG — it imports nothing from draftwright."""
    submodules, relative = _draftwright_imports(_SRC / "_geometry.py")
    assert submodules == set()
    assert relative == []
