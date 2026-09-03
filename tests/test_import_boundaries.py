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

Known limits (static analysis can't fully model these; none occurs in a way that could hide a
DAG violation today, so they are accepted rather than chased):

- **Dynamic imports.** ``importlib.import_module(x)`` / ``__import__(x)`` with a non-literal
  argument can't be resolved statically. The current uses (``__init__`` lazy public-API
  loader, ``sheet_emit``'s emitter, and ``recogniser_contract``'s declaration validator) pass a
  variable and live at the top layer (L7/L8), where an upward edge is impossible anyway. A
  future dynamic import of an internal
  module from a lower layer would not be seen — prefer a static import there.
- **A function called during module init.** Imports inside a ``def`` are treated as lazy
  (cycle-breakers). If a module defined such a function *and called it at module scope*, the
  import would run at init but be excluded from the cycle graph. No module does this; if one
  is added, hoist the import to module scope so the guard sees it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import b123d_recognisers
import b123d_recognisers.evidence as recogniser_evidence
import b123d_recognisers.inspection as recogniser_inspection
import pytest

_SRC = Path(__file__).resolve().parent.parent / "src" / "draftwright"
_MODEL_DIR = _SRC / "model"
_RECOGNISER_PUBLIC = frozenset(b123d_recognisers.__all__)
_RECOGNISER_EVIDENCE_PUBLIC = frozenset(recogniser_evidence.__all__)
_RECOGNISER_INSPECTION_PUBLIC = frozenset(recogniser_inspection.__all__)

# ── The declared DAG (mirrors CLAUDE.md ## Architecture) ─────────────────────────────────
# Rank each top-level submodule (and subpackage) by its layer; a file may import only names
# at its own rank or lower. Keep this in step with CLAUDE.md ## Architecture — the two are the
# same source of truth, and test_every_module_is_ranked fails if a module here is missing so
# the table can't silently drift from the tree.
_LAYERS: dict[str, int] = {
    # 0 — leaves: import nothing from draftwright (or only same-rank leaves / the IR waist)
    "_geometry": 0,
    # Structured ISO 10303-21 facts only; XCAF correspondence remains in rank-2 pmi.py.
    "_pmi_part21": 0,
    # Warning categories only. Imports nothing at all — deliberately, because a category
    # users are told to filter must not cost the CAD kernel to reach (#1043): defined beside
    # `_core` it dragged build123d, and the pytest filterwarnings entry naming it paid ~6 s on
    # every invocation.
    "_warnings": 0,
    "fits": 0,
    "fonts": 0,
    "layout": 0,
    "registry": 0,
    # ADR 0018's view representation: describes views, imports nothing that draws them.
    "view_plan": 0,
    "intents": 0,
    "recognition": 0,
    "recognition_cache": 0,
    "recognition_ownership": 0,
    "recognition_frame": 0,
    # Strict shared validator for the released provider Blend record and its occurrence key.
    "blend_contract": 0,
    "score": 0,  # census over recognition/ only — a leaf beside the recognisers (#704)
    # audit: diffs two FINISHED drawings through their public reads (#996). A leaf by
    # construction — it imports nothing from the engine, so the thing it measures can never
    # come to depend on it.
    "audit": 0,
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
    # Developer-only pytest/runner support. It patches the user-facing builder bindings at
    # runtime and is therefore a top-layer consumer, never an engine dependency.
    "_build_profile": 7,
    # Cross-repository CI contract: dynamically resolves declared implementations at every
    # lower layer, so it deliberately sits above the whole engine beside the user surfaces.
    "recogniser_contract": 7,
    # Separate cross-repository inspection contract. It currently imports no engine module,
    # but remains a top-layer consumer policy boundary rather than an engine dependency.
    "inspection_contract": 7,
    # Versioned, independently-authored recognition benchmark. It may validate through the
    # cross-repository contract, but the drawing engine must never depend on its evaluator.
    "evaluation": 7,
    "cli": 7,
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
    # Empty (#523): the last exempt edge, builder→cli, is gone — the `_cli` compat shim
    # moved to `cli.py` (beside the Typer `app`), so `builder` no longer imports `cli`
    # and the builder→cli→sheet_emit cycle is broken. Keep this closed; a new upward
    # lazy import must earn its entry with a rationale, not inherit one.
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
    """The package a file lives in (its containing dir), for resolving relative imports. A path
    outside the source tree (a synthetic probe in a test) has no package — its absolute imports
    still resolve; relative ones aren't exercised."""
    try:
        rel = path.relative_to(_SRC)
    except ValueError:
        return ["draftwright"]
    return ["draftwright", *rel.parts[:-1]]


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


def _typing_tc_names(tree: ast.Module) -> set[str]:
    """The names in this module that resolve to ``typing.TYPE_CHECKING`` — the bare/aliased
    ``from typing import TYPE_CHECKING [as X]`` bindings, plus the ``typing.TYPE_CHECKING``
    attribute form for ``import typing [as t]``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "typing":
            for alias in node.names:
                if alias.name == "TYPE_CHECKING":
                    names.add(alias.asname or "TYPE_CHECKING")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "typing":
                    names.add(f"{alias.asname or 'typing'}.TYPE_CHECKING")
    return names


def _classify(path: Path) -> dict[int, set[tuple[str, ...]]]:
    """Split a file's draftwright imports into {runtime, TYPE_CHECKING, lazy} full-module sets,
    by the context that actually executes each import (see the module docstring)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    pkg = _package_parts(path)
    res: dict[int, set[tuple[str, ...]]] = {_RUN: set(), _TC: set(), _LAZY: set()}
    tc_names = _typing_tc_names(tree)  # names actually bound to typing.TYPE_CHECKING here

    def _is_type_checking(test: ast.expr) -> bool:
        # Binding-aware: only a name/attr that resolves to typing.TYPE_CHECKING counts, so a
        # `from typing import TYPE_CHECKING as TC` alias is honoured and an unrelated
        # `settings.TYPE_CHECKING` is not mistaken for it.
        return ast.unparse(test) in tc_names

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
            # Importing draftwright.a.b.c first runs a's and a.b's __init__, so those parent
            # packages are real init-time edges too — record them so a cycle passing through a
            # package initializer can't hide (the bare `draftwright` root has no runtime
            # out-edges, so it can't close a cycle; skip it as noise).
            for k in range(2, len(target) + 1):
                dst = ".".join(target[:k])
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
    # Report only cycles that span ≥2 distinct top-level submodules — the ARCHITECTURAL ones
    # (a real cross-layer cycle, possibly mediated by a package __init__). A cycle contained
    # in one submodule (a package ↔ its own submodule, the normal re-export/init-order pattern
    # Python resolves by partial initialization) is not what the DAG guard is about.
    cross = [c for c in cycles if len({_submodule(tuple(m.split("."))) for m in c}) > 1]
    assert not cross, (
        "Cross-submodule import cycle(s) — break with a lazy in-function import at one edge "
        f"(the documented pattern): {cross}"
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


def test_classifier_is_binding_aware_and_context_correct(tmp_path):
    """The context classifier: a `TYPE_CHECKING as TC` alias is honoured, a `settings.TYPE_
    CHECKING` lookalike is NOT, a module-scope `try:` import is runtime (not missed), and a
    `def` body is lazy."""
    src = (
        "from typing import TYPE_CHECKING as TC\n"
        "if TC:\n"
        "    from draftwright.builder import build_drawing\n"  # type-only (aliased TC)
        "class S:\n"
        "    TYPE_CHECKING = True\n"
        "if S.TYPE_CHECKING:\n"
        "    from draftwright.drawing import Drawing\n"  # NOT typing.TYPE_CHECKING → runtime
        "try:\n"
        "    from draftwright.export import to_svg\n"  # module-scope try → runtime
        "except Exception:\n"
        "    pass\n"
        "def load():\n"
        "    from draftwright.cli import app\n"  # def body → lazy
    )
    p = tmp_path / "probe.py"
    p.write_text(src, encoding="utf-8")
    # _classify keys off the file path, not location under src, so point _package_parts at it
    # by placing it where relative resolution isn't exercised (all imports here are absolute).
    res = _classify(p)
    run = {t[1] for t in res[_RUN]}
    tc = {t[1] for t in res[_TC]}
    lazy = {t[1] for t in res[_LAZY]}
    assert "builder" in tc and "builder" not in run  # aliased TYPE_CHECKING honoured
    assert "drawing" in run  # settings.TYPE_CHECKING lookalike NOT treated as type-only
    assert "export" in run  # module-scope try import is runtime, not missed
    assert lazy == {"cli"}  # only the def-body import is lazy


# ── model/ IR-waist guards (original #584 WP2 — kept; add the relative-import rejection) ──

_MODEL_MAY_IMPORT = {
    "_geometry",
    "blend_contract",
    "fits",
    "fonts",
    "layout",
    "model",
    "recognition",
    "recognition_frame",
    # ADR 0017 Amendment 12: detect records exact run-local occurrence→IR ownership at the
    # conversion site. The leaf ledger depends on neither the model nor any upper stage.
    "recognition_ownership",
    # ADR 0018: the dimension planner resolves requirement ownership against the selected
    # semantic view set.  `view_plan` is a rank-0, drawing-independent leaf.
    "view_plan",
}


def _draftwright_imports(path: Path) -> tuple[set[str], list[str]]:
    """The top-level ``draftwright.<name>`` submodules a source file imports, and any relative
    imports it uses (which the model waist forbids so the resolver need never interpret them)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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


def test_linting_does_not_import_model():
    """``linting/`` must not import ``draftwright.model`` (ADR 0015's lint/coverage
    carve-out): coverage reads recognised geometry + the placed drawing for ground
    truth, never the dimensioning IR — sourcing coverage from the plan would be
    circular (a feature the planner omitted would never be flagged). The general
    `_LAYERS` DAG rule alone would PERMIT linting→model (model ranks below), so
    this dedicated assertion pins the carve-out (#697 review)."""
    for path in sorted((_SRC / "linting").glob("*.py")):
        submodules, _ = _draftwright_imports(path)
        assert "model" not in submodules, (
            f"{path.name} imports draftwright.model — the lint/coverage carve-out "
            "(ADR 0015) forbids IR coupling in linting/"
        )


def _private_recogniser_imports(path: Path) -> list[str]:
    offenders: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("b123d_recognisers."):
                offenders.append(f"{path.name}:{node.lineno} imports {node.module}")
            elif node.module == "b123d_recognisers":
                offenders.extend(
                    f"{path.name}:{node.lineno} imports non-public {alias.name}"
                    for alias in node.names
                    if alias.name not in _RECOGNISER_PUBLIC
                )
        elif isinstance(node, ast.Import):
            offenders.extend(
                f"{path.name}:{node.lineno} imports {alias.name}"
                for alias in node.names
                if alias.name == "b123d_recognisers" or alias.name.startswith("b123d_recognisers.")
            )
    return offenders


def test_linting_consumes_recognisers_only_through_the_public_root():
    """Lint may project public aggregate records, never provider implementation modules."""
    offenders = [
        offender
        for path in sorted((_SRC / "linting").glob("*.py"))
        for offender in _private_recogniser_imports(path)
    ]
    assert not offenders, (
        "linting/ must consume the released b123d-recognisers contract through its public "
        f"package root (ADRs 0013/0017; #1411). Private submodule imports: {offenders}"
    )


_ALLOWED_PRIVATE_RECOGNISER_REFERENCES = {
    (
        "test_grid_lattice_convention.py",
        "b123d_recognisers._features",
        "_plane_uv",
    ),
    (
        "test_grid_lattice_convention.py",
        "b123d_recognisers._features",
        "_rect_grid",
    ),
    (
        "test_slanted_blind_step.py",
        "b123d_recognisers._recess_core",
        "_Face",
    ),
    (
        "test_slanted_blind_step.py",
        "b123d_recognisers._recess_core",
        "_recognise_corner_notches",
    ),
}
_RECOGNISER_POLICY_MODULE = "b123d_recognisers.<policy>"
_PROVIDER_ROOT = "b123d_recognisers"
_PROVIDER_EVIDENCE = "b123d_recognisers.evidence"
_PROVIDER_INSPECTION = "b123d_recognisers.inspection"
_PROVIDER_PUBLIC_MODULES = {_PROVIDER_ROOT, _PROVIDER_EVIDENCE, _PROVIDER_INSPECTION}
_LITERAL_PREDICATES = {"endswith", "removeprefix", "removesuffix", "startswith"}
_PROVIDER_MEMBER_CALLS = {"delattr", "object", "setattr"}


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def _recogniser_contract_references(path: Path, *, relative_to: Path) -> set[tuple[str, str, str]]:
    """Return statically visible non-public provider references.

    This is deliberately a provider-boundary check, not a Python data-flow interpreter. It
    covers imports, package attributes through straightforward aliases, literal getattr calls,
    direct provider-object member calls, and literal dotted provider targets in assignments or
    calls. General reflection and dynamically constructed names remain code-review concerns.
    """

    relative = path.relative_to(relative_to).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    references: set[tuple[str, str, str]] = set()
    module_aliases: dict[str, str] = {}

    def policy(label: str) -> None:
        references.add((relative, _RECOGNISER_POLICY_MODULE, label))

    def record(actual: str) -> None:
        if actual == _PROVIDER_ROOT or not actual.startswith(f"{_PROVIDER_ROOT}."):
            return
        components = actual.removeprefix(f"{_PROVIDER_ROOT}.").split(".")
        if components[0] == "evidence":
            if len(components) == 1:
                return
            member = components[1]
            if member == "__all__":
                references.add((relative, _PROVIDER_EVIDENCE, member))
            elif member not in _RECOGNISER_EVIDENCE_PUBLIC:
                references.add((relative, _PROVIDER_EVIDENCE, member))
            return
        if components[0] == "inspection":
            if len(components) == 1:
                return
            member = components[1]
            if member == "__all__":
                references.add((relative, _PROVIDER_INSPECTION, member))
            elif member not in _RECOGNISER_INSPECTION_PUBLIC:
                references.add((relative, _PROVIDER_INSPECTION, member))
            return

        member = components[0]
        if relative == "_recogniser_public_contract.py" and member in {"__all__", "__dict__"}:
            return
        if member == "__all__" or member not in _RECOGNISER_PUBLIC:
            references.add((relative, _PROVIDER_ROOT, member))

    def resolve(dotted: str) -> str | None:
        for binding in sorted(module_aliases, key=len, reverse=True):
            if dotted == binding or dotted.startswith(f"{binding}."):
                return f"{module_aliases[binding]}{dotted[len(binding) :]}"
        return None

    def resolve_expr(node: ast.expr) -> str | None:
        dotted = _dotted_name(node)
        return resolve(dotted) if dotted is not None else None

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == _PROVIDER_ROOT:
                references.update(
                    (relative, module, alias.name)
                    for alias in node.names
                    if alias.name not in _RECOGNISER_PUBLIC | {"*"}
                )
            elif module == _PROVIDER_INSPECTION:
                references.update(
                    (relative, module, alias.name)
                    for alias in node.names
                    if alias.name not in _RECOGNISER_INSPECTION_PUBLIC | {"*"}
                )
            elif module == _PROVIDER_EVIDENCE:
                references.update(
                    (relative, module, alias.name)
                    for alias in node.names
                    if alias.name not in _RECOGNISER_EVIDENCE_PUBLIC | {"*"}
                )
            elif module.startswith(f"{_PROVIDER_ROOT}."):
                references.update((relative, module, alias.name) for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                if module == _PROVIDER_ROOT:
                    module_aliases[alias.asname or module] = module
                elif module == _PROVIDER_EVIDENCE:
                    binding = alias.asname or _PROVIDER_ROOT
                    module_aliases[binding] = module if alias.asname else binding
                elif module == _PROVIDER_INSPECTION:
                    binding = alias.asname or _PROVIDER_ROOT
                    module_aliases[binding] = module if alias.asname else binding
                elif module.startswith(f"{_PROVIDER_ROOT}."):
                    references.add((relative, module, "*"))
                    if alias.asname:
                        module_aliases[alias.asname] = module

    assignments: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            dotted = _dotted_name(node.value)
            if dotted is not None:
                assignments.extend(
                    (target.id, dotted) for target in node.targets if isinstance(target, ast.Name)
                )
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            dotted = _dotted_name(node.value)
            if dotted is not None:
                assignments.append((node.target.id, dotted))

    changed = True
    while changed:
        changed = False
        for binding, value in assignments:
            if binding in module_aliases:
                continue
            actual = resolve(value)
            if actual is not None:
                module_aliases[binding] = actual
                changed = True

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            actual = resolve_expr(node)
            if actual is not None:
                record(actual)

    def literal(node: ast.expr | None) -> str | None:
        return (
            node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None
        )

    def target_argument(node: ast.Call) -> ast.expr | None:
        if node.args:
            return node.args[0]
        targets = [item.value for item in node.keywords if item.arg == "target"]
        return targets[0] if len(targets) == 1 else None

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            dotted_target = literal(value)
            if dotted_target is not None and dotted_target.startswith(f"{_PROVIDER_ROOT}."):
                record(dotted_target)
        if not isinstance(node, ast.Call):
            continue

        terminal = (_dotted_name(node.func) or "").rsplit(".", 1)[-1]
        if terminal not in _LITERAL_PREDICATES:
            for value in [*node.args, *(item.value for item in node.keywords)]:
                dotted_target = literal(value)
                if dotted_target is not None and dotted_target.startswith(f"{_PROVIDER_ROOT}."):
                    record(dotted_target)

        if terminal == "getattr" and len(node.args) >= 2:
            provider = resolve_expr(node.args[0])
            if provider in _PROVIDER_PUBLIC_MODULES:
                member = literal(node.args[1])
                if member is None:
                    policy("dynamic-provider-member")
                else:
                    record(f"{provider}.{member}")

        target_keywords = [item.value for item in node.keywords if item.arg == "target"]
        member_keywords = [
            item.value for item in node.keywords if item.arg in {"attribute", "name"}
        ]
        if terminal in _PROVIDER_MEMBER_CALLS:
            # Keep positional object/member pairs adjacent, so a string replacement is not
            # mistaken for another member. Explicit member keywords may combine with a
            # positional provider in bound or unbound patch calls.
            for provider_node, member_node in zip(node.args, node.args[1:]):
                provider = resolve_expr(provider_node)
                member = literal(member_node)
                if provider in _PROVIDER_PUBLIC_MODULES and member is not None:
                    record(f"{provider}.{member}")

            providers = {
                provider
                for value in [*node.args, *target_keywords]
                if (provider := resolve_expr(value)) in _PROVIDER_PUBLIC_MODULES
            }
            members = {
                member for value in member_keywords if (member := literal(value)) is not None
            }
            for provider in providers:
                for member in members:
                    record(f"{provider}.{member}")

        if terminal == "multiple":
            target = target_argument(node)
            provider = resolve_expr(target) if target is not None else None
            target_name = literal(target)
            if provider is None and target_name in _PROVIDER_PUBLIC_MODULES:
                provider = target_name
            if provider in _PROVIDER_PUBLIC_MODULES:
                for item in node.keywords:
                    if item.arg not in {None, "create", "spec", "spec_set", "target"}:
                        record(f"{provider}.{item.arg}")

    return references


def _consumer_recogniser_contract_violations(
    tests: Path,
) -> tuple[set[tuple[str, str, str]], set[tuple[str, str, str]]]:
    references = {
        reference
        for path in sorted(tests.rglob("*.py"))
        if path.name != "test_import_boundaries.py"
        for reference in _recogniser_contract_references(path, relative_to=tests)
    }
    return (
        references - _ALLOWED_PRIVATE_RECOGNISER_REFERENCES,
        _ALLOWED_PRIVATE_RECOGNISER_REFERENCES - references,
    )


def test_consumer_tests_use_only_released_recogniser_contract_or_explicit_blockers():
    """Consumer evidence must not become a second home for provider implementation tests."""

    tests = Path(__file__).resolve().parent
    violations, stale_exceptions = _consumer_recogniser_contract_violations(tests)

    # The two exact underscore-module seams are immutable upstream blockers (#400/#408).
    # Fail when a new reference appears or either explicit exception becomes stale.
    assert not violations and not stale_exceptions, (
        "consumer tests must use released b123d-recognisers contracts; only the exact "
        "documented upstream blockers may use private members. "
        f"Violations: {violations}; stale exceptions: {stale_exceptions}"
    )


def test_consumer_recogniser_contract_guard_covers_static_provider_seams(tmp_path):
    (tmp_path / "test_grid_lattice_convention.py").write_text(
        "from b123d_recognisers._features import _plane_uv, _rect_grid, _new_private\n",
        encoding="utf-8",
    )
    (tmp_path / "test_slanted_blind_step.py").write_text(
        "from b123d_recognisers._recess_core import _Face, _recognise_corner_notches\n",
        encoding="utf-8",
    )
    (tmp_path / "private_static.py").write_text(
        "import b123d_recognisers as br\n"
        "alias = br\n"
        "private = alias.profiled_bores.double_d_profile\n",
        encoding="utf-8",
    )
    (tmp_path / "private_inspection.py").write_text(
        "from b123d_recognisers.inspection import _FaceGraph\n",
        encoding="utf-8",
    )
    (tmp_path / "private_evidence_import.py").write_text(
        "from b123d_recognisers.evidence import _PrivateEvidence\n",
        encoding="utf-8",
    )
    (tmp_path / "private_evidence_alias.py").write_text(
        "import b123d_recognisers.evidence as evidence\nprivate = evidence._private_builder\n",
        encoding="utf-8",
    )
    (tmp_path / "private_evidence_getattr.py").write_text(
        "import b123d_recognisers.evidence as evidence\n"
        "private = getattr(evidence, '_private_builder')\n",
        encoding="utf-8",
    )
    (tmp_path / "private_evidence_patch.py").write_text(
        "import b123d_recognisers.evidence as evidence\n"
        "monkeypatch.setattr(evidence, '_private_builder', replacement)\n",
        encoding="utf-8",
    )
    (tmp_path / "private_getattr.py").write_text(
        "import b123d_recognisers as br\nprivate = getattr(br, 'polygonal_bosses')\n",
        encoding="utf-8",
    )
    (tmp_path / "private_literal_target.py").write_text(
        "from unittest import mock\n"
        "target = 'b123d_recognisers.profiled_bores.double_d_profile'\n"
        "mock.patch(target, replacement)\n",
        encoding="utf-8",
    )
    (tmp_path / "private_unbound_patch.py").write_text(
        "import b123d_recognisers as br\n"
        "pytest.MonkeyPatch.setattr(monkeypatch, br, 'profiled_bores', replacement)\n",
        encoding="utf-8",
    )
    (tmp_path / "private_mixed_setattr.py").write_text(
        "import b123d_recognisers as br\n"
        "monkeypatch.setattr(br, name='profiled_bores', value=replacement)\n",
        encoding="utf-8",
    )
    (tmp_path / "private_mixed_delattr.py").write_text(
        "import b123d_recognisers as br\nmonkeypatch.delattr(br, name='profiled_bores')\n",
        encoding="utf-8",
    )
    (tmp_path / "private_unbound_delattr.py").write_text(
        "import b123d_recognisers as br\n"
        "pytest.MonkeyPatch.delattr(monkeypatch, br, name='profiled_bores')\n",
        encoding="utf-8",
    )
    (tmp_path / "private_mixed_object_patch.py").write_text(
        "from unittest import mock\n"
        "import b123d_recognisers as br\n"
        "mock.patch.object(br, attribute='profiled_bores', new=replacement)\n",
        encoding="utf-8",
    )
    (tmp_path / "private_multiple_patch.py").write_text(
        "from unittest.mock import patch\n"
        "patch.multiple('b123d_recognisers', profiled_bores=replacement)\n",
        encoding="utf-8",
    )
    (tmp_path / "publication_mutation.py").write_text(
        "import b123d_recognisers as br\nbr.__all__.append('profiled_bores')\n",
        encoding="utf-8",
    )
    (tmp_path / "public_and_unrelated_controls.py").write_text(
        "import importlib.util\n"
        "import b123d_recognisers as br\n"
        "import b123d_recognisers.evidence as evidence\n"
        "from b123d_recognisers import Flat\n"
        "from b123d_recognisers.evidence import RecognitionEvidence\n"
        "public = br.Flat\n"
        "public_evidence = evidence.build_recognition_evidence\n"
        "also_public_evidence = getattr(evidence, 'RecognitionEvidence')\n"
        "monkeypatch.setattr(evidence, 'build_recognition_evidence', replacement)\n"
        "also_public = getattr(br, 'Flat')\n"
        "monkeypatch.setattr(br, 'Flat', 'replacement')\n"
        "assert_context(br, 'consumer fixture')\n"
        "target = 'draftwright.layout._solve_strip_1d'\n"
        "monkeypatch.setattr(target, replacement)\n"
        "setter = config.setattr\n"
        "setter('theme', 'dark')\n"
        "find = importlib.util.find_spec\n"
        "find('draftwright.layout')\n",
        encoding="utf-8",
    )

    violations, stale_exceptions = _consumer_recogniser_contract_violations(tmp_path)

    assert violations == {
        ("test_grid_lattice_convention.py", "b123d_recognisers._features", "_new_private"),
        ("private_static.py", _PROVIDER_ROOT, "profiled_bores"),
        ("private_inspection.py", _PROVIDER_INSPECTION, "_FaceGraph"),
        ("private_evidence_import.py", _PROVIDER_EVIDENCE, "_PrivateEvidence"),
        ("private_evidence_alias.py", _PROVIDER_EVIDENCE, "_private_builder"),
        ("private_evidence_getattr.py", _PROVIDER_EVIDENCE, "_private_builder"),
        ("private_evidence_patch.py", _PROVIDER_EVIDENCE, "_private_builder"),
        ("private_getattr.py", _PROVIDER_ROOT, "polygonal_bosses"),
        ("private_literal_target.py", _PROVIDER_ROOT, "profiled_bores"),
        ("private_unbound_patch.py", _PROVIDER_ROOT, "profiled_bores"),
        ("private_mixed_setattr.py", _PROVIDER_ROOT, "profiled_bores"),
        ("private_mixed_delattr.py", _PROVIDER_ROOT, "profiled_bores"),
        ("private_unbound_delattr.py", _PROVIDER_ROOT, "profiled_bores"),
        ("private_mixed_object_patch.py", _PROVIDER_ROOT, "profiled_bores"),
        ("private_multiple_patch.py", _PROVIDER_ROOT, "profiled_bores"),
        ("publication_mutation.py", _PROVIDER_ROOT, "__all__"),
    }
    assert not stale_exceptions


def test_public_recogniser_member_is_an_immutable_public_snapshot(monkeypatch):
    import _recogniser_public_contract as contract

    with pytest.raises(KeyError):
        contract.public_recogniser_member("profiled_bores")

    assert not hasattr(contract, "recognition")
    assert getattr(contract.public_recogniser_member, "__closure__", None) is None
    with pytest.raises(AttributeError):
        contract.public_recogniser_member._root
    with pytest.raises(AttributeError):
        contract.public_recogniser_member._root = {"profiled_bores": object()}

    contract._PUBLIC_RECOGNISER_NAMES = frozenset({"profiled_bores"})
    try:
        with pytest.raises(KeyError):
            contract.public_recogniser_member("profiled_bores")
    finally:
        del contract._PUBLIC_RECOGNISER_NAMES

    with monkeypatch.context() as patch:
        patch.setattr(
            contract,
            "_PUBLIC_RECOGNISER_NAMES",
            frozenset({"profiled_bores"}),
            raising=False,
        )
        with pytest.raises(KeyError):
            contract.public_recogniser_member("profiled_bores")


def test_public_root_guard_rejects_module_alias_loopholes(tmp_path):
    probe = tmp_path / "private_recogniser_imports.py"
    probe.write_text(
        "from b123d_recognisers import profiled_bores, _features\n"
        "import b123d_recognisers.profiled_bores\n"
        "import b123d_recognisers as br\n"
        "private = br.profiled_bores.double_d_profile\n",
        encoding="utf-8",
    )

    offenders = _private_recogniser_imports(probe)

    assert len(offenders) == 4
    assert any("non-public profiled_bores" in offender for offender in offenders)
    assert any("non-public _features" in offender for offender in offenders)
    assert any("b123d_recognisers.profiled_bores" in offender for offender in offenders)
    assert any("imports b123d_recognisers" in offender for offender in offenders)
