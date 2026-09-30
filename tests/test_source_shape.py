"""Keep the reviewed source-size limits from growing back (#1939)."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[1] / "src" / "draftwright"
_MAX_MODULE_LINES = 3_000
_MAX_ANNOTATION_MODULE_LINES = 2_500
_MAX_FUNCTION_LINES = 300
_MAX_COMPLEXITY = 15
_COMPLEXITY_BASELINE = Path(__file__).with_name("_complexity_baseline.json")
_COMPLEXITY_MESSAGE = re.compile(r"^`[^`]+` is too complex \((\d+) > 15\)$")
# First-landing ceiling: the JSON introduced with this guard is not yet on main,
# so a source+JSON increase must also change this separately reviewed fingerprint.
_BOOTSTRAP_COMPLEXITY_SHA256 = "01f313fc4c2e765501e49b10983819f6cbc97fc08e9ed97229b207bf8ea2abf2"
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


def _function_owners(path: Path) -> dict[int, str]:
    """Map definition lines to class-qualified names, including nested functions."""
    owners = {}

    def walk(node: ast.AST, parents: tuple[str, ...] = ()) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = (*parents, child.name)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owners[child.lineno] = ".".join(qualified)
                walk(child, qualified)
            else:
                walk(child, parents)

    walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return owners


def _complexity_findings() -> dict[str, int]:
    """Ask the locked Ruff for every function above the reviewed threshold."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            str(_SOURCE),
            "--isolated",
            "--select",
            "C901",
            "--config",
            f"lint.mccabe.max-complexity = {_MAX_COMPLEXITY}",
            "--output-format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    issues = json.loads(result.stdout)
    owners_by_path = {}
    findings = {}
    for issue in issues:
        assert issue["code"] == "C901", issue
        path = Path(issue["filename"])
        if path not in owners_by_path:
            owners_by_path[path] = _function_owners(path)
        owners = owners_by_path[path]
        line = issue["location"]["row"]
        assert line in owners, f"Ruff finding has no function at {path}:{line}"
        match = _COMPLEXITY_MESSAGE.fullmatch(issue["message"])
        assert match is not None, issue["message"]
        key = f"{path.relative_to(_SOURCE)}:{owners[line]}"
        assert key not in findings, f"Duplicate qualified complexity identity: {key}"
        findings[key] = int(match.group(1))
    return findings


def _committed_complexity_ceiling() -> dict[str, int] | None:
    """Read the reviewed baseline at the branch point with main, if it exists."""
    repository = _SOURCE.parents[1]
    for main_ref in ("origin/main", "main"):
        merge_base = subprocess.run(
            ["git", "merge-base", "HEAD", main_ref],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if merge_base.returncode != 0:
            continue
        committed = subprocess.run(
            ["git", "show", f"{merge_base.stdout.strip()}:tests/_complexity_baseline.json"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if committed.returncode == 0:
            return json.loads(committed.stdout)["functions"]
    return None


def test_c901_complexity_budget_only_shrinks():
    """New complexity >15 fails; reductions must lower the per-function baseline."""
    baseline = json.loads(_COMPLEXITY_BASELINE.read_text(encoding="utf-8"))
    assert baseline["limit"] == _MAX_COMPLEXITY
    budgets = baseline["functions"]
    assert all(value > _MAX_COMPLEXITY for value in budgets.values())
    observed = _complexity_findings()
    new_or_grown = {
        name: (budgets.get(name), value)
        for name, value in observed.items()
        if name not in budgets or value > budgets[name]
    }
    assert not new_or_grown, f"New or increased C901 complexity: {new_or_grown}"
    reduced_or_removed = {
        name: (value, observed.get(name))
        for name, value in budgets.items()
        if name not in observed or observed[name] < value
    }
    assert not reduced_or_removed, (
        f"Lower the reviewed C901 baseline after complexity shrinks: {reduced_or_removed}"
    )
    # PR test jobs fetch full history. Main's committed budget is the lasting
    # ceiling; the fingerprint anchors the initial PR and shallow checkouts.
    if (ceiling := _committed_complexity_ceiling()) is None:
        fingerprint = hashlib.sha256(
            json.dumps(budgets, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert fingerprint == _BOOTSTRAP_COMPLEXITY_SHA256, (
            "Initial C901 baseline changed; review its ceiling and fingerprint together"
        )
    else:
        increased_budget = {
            name: (ceiling.get(name), value)
            for name, value in budgets.items()
            if name not in ceiling or value > ceiling[name]
        }
        assert not increased_budget, f"C901 baseline may only shrink: {increased_budget}"


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
