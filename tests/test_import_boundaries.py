"""Import-boundary guards (ADR 0008; #584 WP2).

The IR waist (:mod:`draftwright.model`) is the neutral middle of the compiler
hourglass — it must not reach *up* into the stage-level drawing/layout modules
(``_core``, ``analysis``, ``sheet``, ``linting``, the annotation/render layers, …).
It may only depend on leaf modules (``_geometry``, ``fits``, ``recognition``,
``layout``, ``fonts``) and its own subpackage.

This is what #584 WP2 fixed by extracting the model-neutral primitives
(``_END_ON``/``_xyz``/``HoleRef``/``_axis_letter``) out of ``_core`` into the leaf
``_geometry``. The test fails if the inversion returns.

The guard is a **fail-closed allowlist**: any draftwright submodule a ``model/``
file imports must be in :data:`_MODEL_MAY_IMPORT`, so a future upper-layer import
(e.g. ``linting``, which itself imports ``_core``) trips the test even though it is
not literally a drawing/layout stage. Relative imports are rejected outright — the
codebase is 100%-absolute, and a relative import would slip past the resolver.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "draftwright"
_MODEL_DIR = _SRC / "model"

# The only draftwright submodules the IR waist may depend on: leaf modules that
# sit below it in the DAG, plus its own subpackage. Fail-closed — anything else
# (a stage module, or a leaf like `linting` that transitively pulls in `_core`)
# is a boundary violation.
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
                parts = node.module.split(".")
                if parts[0] == "draftwright" and len(parts) > 1:
                    submodules.add(parts[1])
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


def test_guard_catches_absolute_and_relative_forbidden_imports():
    """The extractor is not a tautology: it flags every form a regression could take."""
    src = (
        "from draftwright._core import HoleRef\n"
        "import draftwright.sheet\n"
        "from .._core import _xyz\n"
        "from .. import analysis\n"
    )
    tree = ast.parse(src)
    # Reuse the same logic against synthetic source.
    submodules: set[str] = set()
    relative: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level > 0:
                relative.append(node.module or "(bare)")
            elif node.module:
                submodules.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                submodules.add(alias.name.split(".")[1])
    assert submodules == {"_core", "sheet"}  # both absolute forms caught
    assert len(relative) == 2  # both relative forms flagged, not silently missed


def test_geometry_is_a_leaf():
    """``_geometry`` is the bottom of the DAG — it imports nothing from draftwright."""
    submodules, relative = _draftwright_imports(_SRC / "_geometry.py")
    assert submodules == set()
    assert relative == []
