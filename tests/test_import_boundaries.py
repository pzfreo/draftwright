"""Import-boundary guards — the whole-package DAG, machine-enforced (#640 / ADR 0005/0008).

CLAUDE.md's **## Architecture** section declares a layered DAG: leaf modules →
``_core`` → the core-consumers (``linting``/``pmi``/``export``/``repair``/``projection``/
``compose``) → ``analysis`` → the ``annotations`` render layer → ``drawing`` → ``builder``
→ the user-facing facades/``cli``. No lower layer may import an upper one. Before #640 this
was asserted in prose but machine-enforced only for the ``model/`` IR waist; a real
regression (an upward import) could land and only be noticed by a human reading the map.

This file enforces the whole DAG. The import extractor is deliberately thorough — a guard
with a false-negative is worse than none, because it grants false confidence:

- Every import FORM is resolved: ``import draftwright.a.b``, ``from draftwright.a import b``,
  ``from draftwright import b`` (the root-package form), and relative imports
  (``from .b import x`` / ``from ..a import b``) resolved against the file's own package.
- Imports are classified by the context that actually executes them: **module-level runtime**
  (top level, incl. inside a module-scope ``try``/``if``/``with``/``for``/class body),
  **TYPE_CHECKING** (inside ``if TYPE_CHECKING:`` only — ``if not TYPE_CHECKING:`` is runtime),
  and **lazy** (inside a function/method body — the sanctioned cycle-breakers).
- The cycle detector runs at FULL-MODULE granularity (``draftwright.annotations.holes``), so
  an intra-package cycle can't hide behind a collapsed ``annotations → annotations`` self-edge.

Guards: no upward runtime import (:func:`test_no_upward_runtime_imports`), no runtime import
cycle (:func:`test_no_module_level_import_cycles`), upward TYPE_CHECKING refs allowlisted
(:data:`_TC_UPWARD_ALLOW`), upward lazy imports documented (:data:`_LAZY_UPWARD_EXEMPT`),
fail-closed ranking (:func:`test_every_module_is_ranked`). The ``model/`` waist checks (the
original #584 WP2) are kept for their relative-import rejection.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "draftwright"
_MODEL_DIR = _SRC / "model"

# ── The declared DAG (mirrors CLAUDE.md ## Architecture) ─────────────────────────────────
# Rank each top-level submodule (and subpackage) by its layer; a file may import only names
# at its own rank or lower. Keep this in step with CLAUDE.md ## Architecture — the two are the
# same source of truth, and test_every_module_is_ranked fails if a module here is missing so
# the table can't silently drift from the tree.
_LAYERS: dict[str, int] = {
    # 0 — leaves: import nothing from draftwright (or only same-rank leaves / the IR waist)
    "_geometry": 0,
    "fits": 0,
    "fonts": 0,
    "layout": 0,
    "registry": 0,
    "intents": 0,
    "recognition": 0,
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
    "sheet_dsl": 7,  # frozen deprecation shim — re-exports the sheet facade (renamed, #640)
    "sheet_emit": 7,
    "cli": 7,
    "score": 7,
    # 8 — the package root: the public API surface, above everything
    "__init__": 8,
}

# TYPE_CHECKING-only imports that point UP the DAG. No runtime dependency (the import never
# executes), but recorded explicitly so the upward *type* reference is deliberate and reviewed.
_TC_UPWARD_ALLOW: dict[tuple[str, str], str] = {
    ("_core", "compose"): (
        "_core type-annotates Analysis.layout_strips as compose.StripDepths; StripDepths is a "
        "compose (outer-layout) concept, so the reference is type-only under TYPE_CHECKING. "
        "Move it down or keep this documented (#640)."
    ),
}

# Lazy (in-function) imports that point UP the DAG — the sanctioned cycle-breakers. Recorded so
# a NEW upward lazy import (a would-be hidden cycle) forces a documented decision, not silence.
_LAZY_UPWARD_EXEMPT: dict[tuple[str, str], str] = {
    ("builder", "cli"): (
        "builder._cli (a compat shim) launches the Typer app lazily so a bare "
        "`import draftwright.builder` doesn't pull Typer; cli imports builder lazily too, so "
        "there is no module-level cycle (#313/#523)."
    ),
}

_RUN, _TC, _LAZY = 0, 1, 2


def _module_full(path: Path) -> tuple[str, ...]:
    """The dotted module path of a source file, e.g. ``('draftwright','annotations','holes')``
    (an ``__init__.py`` names its package)."""
    rel = path.relative_to(_SRC)
    parts = ["draftwright", *rel.parts]
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return tuple(parts)


def _package_parts(path: Path) -> list[str]:
    """The package a file lives in (its containing dir), for resolving relative imports."""
    return ["draftwright", *path.relative_to(_SRC).parts[:-1]]


def _module_exists(dotted: tuple[str, ...]) -> bool:
    """Whether *dotted* names an actual module/package under src (used to tell a submodule from
    a symbol in ``from pkg import name``)."""
    p = _SRC.joinpath(*dotted[1:])
    return p.with_suffix(".py").exists() or (p / "__init__.py").exists()


def _resolve(node: ast.AST, pkg: list[str]) -> set[tuple[str, ...]]:
    """Full draftwright module tuples an import node references — every form, absolute and
    relative. ``from pkg import name`` yields ``pkg.name`` when that is a real module, else
    ``pkg`` (name is a symbol, so the dependency is on the module that defines it)."""
    out: set[tuple[str, ...]] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] == "draftwright":
                out.add(tuple(parts))
    elif isinstance(node, ast.ImportFrom):
        if node.level == 0:
            if not node.module or node.module.split(".")[0] != "draftwright":
                return out
            base = node.module.split(".")
        else:  # relative: anchor at the package, stripping (level-1) trailing components
            anchor = pkg[: len(pkg) - (node.level - 1)]
            base = anchor + (node.module.split(".") if node.module else [])
            if not base or base[0] != "draftwright":
                return out
        for alias in node.names:
            cand = (*base, alias.name)
            out.add(cand if _module_exists(cand) else tuple(base))
    return out


def _classify(path: Path) -> dict[int, set[tuple[str, ...]]]:
    """Split a file's draftwright imports into {runtime, TYPE_CHECKING, lazy} full-module sets,
    by the context that actually executes each import (see the module docstring)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    pkg = _package_parts(path)
    res: dict[int, set[tuple[str, ...]]] = {_RUN: set(), _TC: set(), _LAZY: set()}

    def _is_type_checking(test: ast.expr) -> bool:
        return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )

    def walk(node: ast.AST, ctx: int) -> None:
        if isinstance(node, ast.Import | ast.ImportFrom):
            res[ctx] |= _resolve(node, pkg)
            return
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            inner = _LAZY if ctx == _RUN else ctx  # a def body is lazy (unless already TC)
            for child in node.body:
                walk(child, inner)
            return
        if isinstance(node, ast.If) and ctx == _RUN and _is_type_checking(node.test):
            for child in node.body:
                walk(child, _TC)
            for child in node.orelse:  # the `else:` of `if TYPE_CHECKING` runs at runtime
                walk(child, _RUN)
            return
        # any other statement (module scope, or a try/if/with/for/class body) keeps its context
        for child in ast.iter_child_nodes(node):
            walk(child, ctx)

    walk(tree, _RUN)
    return res


def _submodule(full: tuple[str, ...]) -> str:
    """The layer key for a full module tuple: the top-level submodule, or ``"__init__"`` for
    the package root itself."""
    return full[1] if len(full) > 1 else "__init__"


def _all_sources() -> list[Path]:
    return [p for p in sorted(_SRC.rglob("*.py")) if "__pycache__" not in p.parts]


def test_every_module_is_ranked():
    """Fail-closed: every submodule (importer or imported, any context) is in _LAYERS, so a new
    top-level module can't slip in unranked and dodge the DAG guard."""
    seen: set[str] = set()
    for path in _all_sources():
        seen.add(_submodule(_module_full(path)))
        res = _classify(path)
        for targets in res.values():
            seen |= {_submodule(t) for t in targets}
    missing = seen - set(_LAYERS)
    assert not missing, (
        "Unranked submodule(s) — add them to _LAYERS (and CLAUDE.md ## Architecture) so the "
        f"DAG guard covers them: {sorted(missing)}"
    )


def test_no_upward_runtime_imports():
    """No file imports a module ABOVE its own layer at runtime (the DAG, machine-checked)."""
    offenders: list[str] = []
    for path in _all_sources():
        sm = _submodule(_module_full(path))
        for target in sorted(_classify(path)[_RUN]):
            tsm = _submodule(target)
            if tsm != sm and _LAYERS[tsm] > _LAYERS[sm]:
                offenders.append(
                    f"{path.relative_to(_SRC)} ({sm}, L{_LAYERS[sm]}) imports "
                    f"{'.'.join(target)} ({tsm}, L{_LAYERS[tsm]}) — upward"
                )
    assert not offenders, (
        "Upward cross-layer import(s) break the declared DAG (CLAUDE.md ## Architecture / ADR "
        "0005). Move the dependency down, defer it to a lazy in-function import (a documented "
        "cycle-breaker), or re-layer with a reason:\n  " + "\n  ".join(offenders)
    )


def test_no_module_level_import_cycles():
    """The runtime import graph is acyclic at FULL-MODULE granularity (so an intra-package cycle
    can't hide). Lazy in-function imports are excluded — the sanctioned cycle-breakers."""
    graph: dict[str, set[str]] = {}
    for path in _all_sources():
        src = ".".join(_module_full(path))
        graph.setdefault(src, set())
        for target in _classify(path)[_RUN]:
            dst = ".".join(target)
            if dst != src:
                graph[src].add(dst)

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {}
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
        if colour.get(node, WHITE) == WHITE:
            visit(node, [])
    assert not cycles, (
        "Module-level import cycle(s) — break with a lazy in-function import at one edge "
        f"(the documented pattern): {cycles}"
    )


def test_type_checking_upward_refs_are_allowlisted():
    """A TYPE_CHECKING import pointing up the DAG must be an explicit, reasoned exception."""
    offenders: list[str] = []
    for path in _all_sources():
        sm = _submodule(_module_full(path))
        for target in sorted(_classify(path)[_TC]):
            tsm = _submodule(target)
            if tsm != sm and _LAYERS[tsm] > _LAYERS[sm] and (sm, tsm) not in _TC_UPWARD_ALLOW:
                offenders.append(f"{sm} → {tsm} (TYPE_CHECKING, upward)")
    assert not offenders, (
        "Undocumented upward TYPE_CHECKING reference(s) — move the type down or add a reasoned "
        f"entry to _TC_UPWARD_ALLOW: {offenders}"
    )


def test_lazy_upward_imports_are_documented():
    """An upward LAZY (in-function) import — a would-be cycle-breaker — must be a documented
    _LAZY_UPWARD_EXEMPT entry, not an invisible edge."""
    offenders: list[str] = []
    for path in _all_sources():
        sm = _submodule(_module_full(path))
        for target in sorted(_classify(path)[_LAZY]):
            tsm = _submodule(target)
            if tsm != sm and _LAYERS[tsm] > _LAYERS[sm] and (sm, tsm) not in _LAZY_UPWARD_EXEMPT:
                offenders.append(f"{sm} → {tsm} (lazy, upward)")
    assert not offenders, (
        "Undocumented upward lazy import(s) — a lazy cycle-breaker must be recorded in "
        f"_LAZY_UPWARD_EXEMPT with a reason (ADR 0005; #640): {offenders}"
    )


def test_resolver_catches_every_import_form():
    """The resolver is not a tautology: absolute-dotted, root-package, and relative forms all
    resolve to the right module (a false-negative here would silently pass a real upward edge)."""
    holes = _SRC / "annotations" / "holes.py"
    pkg = _package_parts(holes)  # draftwright.annotations
    forms = {
        "import draftwright.builder": ("draftwright", "builder"),
        "from draftwright.builder import build_drawing": ("draftwright", "builder"),
        "from draftwright import sheet": ("draftwright", "sheet"),  # root-package, module name
        "from .._core import _fmt": ("draftwright", "_core"),  # relative up one package
        "from . import sections": ("draftwright", "annotations", "sections"),  # relative sibling
    }
    for src, expected in forms.items():
        node = ast.parse(src).body[0]
        assert expected in _resolve(node, pkg), f"{src!r} did not resolve to {expected}"


def test_layer_guard_detects_a_synthetic_upward_import():
    """The DAG guard is not a tautology: a fabricated upward import is over the rank line."""
    node = ast.parse("from draftwright.builder import build_drawing\n").body[0]
    targets = _resolve(node, ["draftwright"])
    assert any(_submodule(t) == "builder" for t in targets)
    assert _LAYERS["builder"] > _LAYERS["export"]


# ── model/ IR-waist guards (original #584 WP2 — kept; add the relative-import rejection) ──

_MODEL_MAY_IMPORT = {"_geometry", "fits", "fonts", "layout", "model", "recognition"}


def _draftwright_imports(path: Path) -> tuple[set[str], list[str]]:
    """The top-level ``draftwright.<name>`` submodules a source file imports, and any relative
    imports it uses (which the model waist forbids so the resolver need never interpret them)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    submodules: set[str] = set()
    relative: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level > 0:
                relative.append(node.module or "(bare relative import)")
            elif node.module and node.module.split(".")[0] == "draftwright":
                parts = node.module.split(".")
                if len(parts) > 1:
                    submodules.add(parts[1])
                else:  # from draftwright import <name>
                    submodules |= {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "draftwright" and len(parts) > 1:
                    submodules.add(parts[1])
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


def test_geometry_is_a_leaf():
    """``_geometry`` is the bottom of the DAG — it imports nothing from draftwright."""
    submodules, relative = _draftwright_imports(_SRC / "_geometry.py")
    assert submodules == set()
    assert relative == []
