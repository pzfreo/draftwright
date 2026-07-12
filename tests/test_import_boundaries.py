"""Import-boundary guards (ADR 0008; #584 WP2).

The IR waist (:mod:`draftwright.model`) is the neutral middle of the compiler
hourglass — it must not reach *up* into the stage-level drawing/layout modules
(``_core``, ``analysis``, ``sheet``, the annotation/render layers, …). It may
only depend on leaf modules (``_geometry``, ``fits``, ``recognition``, ``layout``).

This is what #584 WP2 fixed by extracting the model-neutral primitives
(``_END_ON``/``_xyz``/``HoleRef``/``_axis_letter``) out of ``_core`` into the leaf
``_geometry``. The test fails if the inversion returns.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MODEL_DIR = Path(__file__).resolve().parent.parent / "src" / "draftwright" / "model"

# Stage-level / upper drawing+layout modules the IR waist must never import.
_FORBIDDEN = {
    "_core",
    "analysis",
    "annotate",
    "annotations",
    "builder",
    "cli",
    "drawing",
    "export",
    "make_drawing",
    "projection",
    "registry",
    "repair",
    "sheet",
    "sheet_dsl",
    "sheet_emit",
}


def _imported_draftwright_submodules(path: Path) -> set[str]:
    """The top-level ``draftwright.<name>`` submodules imported by one source file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "draftwright" and len(parts) > 1:
                names.add(parts[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "draftwright" and len(parts) > 1:
                    names.add(parts[1])
    return names


def test_model_does_not_import_upper_layers():
    """No file under ``model/`` imports a stage-level drawing/layout module."""
    offenders: dict[str, set[str]] = {}
    for path in sorted(_MODEL_DIR.glob("*.py")):
        bad = _imported_draftwright_submodules(path) & _FORBIDDEN
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "model/ (the IR waist) must not import stage-level drawing/layout modules "
        f"(ADR 0008; #584 WP2): {offenders}"
    )


def test_geometry_is_a_leaf():
    """``_geometry`` is the bottom of the DAG — it imports nothing from draftwright."""
    path = _MODEL_DIR.parent / "_geometry.py"
    assert _imported_draftwright_submodules(path) == set()
