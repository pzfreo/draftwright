"""Keep the reviewed source-size limits from growing back (#1939)."""

from __future__ import annotations

import ast
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[1] / "src" / "draftwright"
_MAX_MODULE_LINES = 3_000
_MAX_ANNOTATION_MODULE_LINES = 2_500
_MAX_FUNCTION_LINES = 300
_MAX_PLACEMENT_MEGA_FUNCTION_LINES = 199
_PLACEMENT_MEGA_FUNCTIONS = {
    "model/detect.py": "build_part_model",
    "annotations/leaders.py": "place_feature_leader_jobs",
    "builder.py": "build_drawing",
    "analysis.py": "_analyse",
    "annotations/holes.py": "_place_queue",
    "annotations/_common.py": "place_strip_candidates",
}


def _source_files() -> list[Path]:
    files = sorted(_SOURCE.rglob("*.py"))
    assert _SOURCE / "__init__.py" in files, f"Missing draftwright source tree: {_SOURCE}"
    return files


def test_source_modules_stay_within_the_size_limit():
    oversized = []
    for path in _source_files():
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > _MAX_MODULE_LINES:
            oversized.append(f"{path.relative_to(_SOURCE)}: {lines} lines")
    assert not oversized, "Source modules over 3,000 lines:\n" + "\n".join(oversized)


def test_annotation_modules_stay_within_2500_lines():
    annotations = _SOURCE / "annotations"
    files = sorted(annotations.glob("*.py"))
    assert annotations / "__init__.py" in files, f"Missing annotation source tree: {annotations}"
    oversized = []
    for path in files:
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > _MAX_ANNOTATION_MODULE_LINES:
            oversized.append(f"{path.relative_to(_SOURCE)}: {lines} lines")
    assert not oversized, "Annotation modules over 2,500 lines:\n" + "\n".join(oversized)


def test_source_functions_stay_within_the_size_limit():
    oversized = []
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno is not None
                lines = node.end_lineno - node.lineno + 1
                if lines > _MAX_FUNCTION_LINES:
                    oversized.append(
                        f"{path.relative_to(_SOURCE)}:{node.lineno} {node.name}: {lines} lines"
                    )
    assert not oversized, "Source functions over 300 lines:\n" + "\n".join(oversized)


def test_placement_mega_functions_stay_under_200_lines():
    oversized = []
    for module, name in _PLACEMENT_MEGA_FUNCTIONS.items():
        path = _SOURCE / module
        matches = [
            node
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ]
        assert len(matches) == 1, f"Expected one {module}:{name}, found {len(matches)}"
        node = matches[0]
        assert node.end_lineno is not None
        lines = node.end_lineno - node.lineno + 1
        if lines > _MAX_PLACEMENT_MEGA_FUNCTION_LINES:
            oversized.append(f"{module}:{node.lineno} {name}: {lines} lines")
    assert not oversized, "Placement functions at or over 200 lines:\n" + "\n".join(oversized)


def test_nested_callbacks_capture_fewer_than_five_defaults():
    oversized = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            defaults = len(node.args.defaults) + sum(
                value is not None for value in node.args.kw_defaults
            )
            if defaults < 5:
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(parent, ast.Module):
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    oversized.append(
                        f"{path.relative_to(_SOURCE)}:{node.lineno} "
                        f"{getattr(node, 'name', '<lambda>')}: {defaults} defaults"
                    )
                    break
                parent = parents.get(parent)
    assert not oversized, "Nested callbacks with five or more defaults:\n" + "\n".join(oversized)
