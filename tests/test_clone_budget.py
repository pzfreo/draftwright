"""The suite may not grow by copying a test body into another module.

Between 2026-08-21 and 2026-09-07 the corpus went from 3,347 to 4,934 test
functions and pull-request CI wall-clock went from ~31 to ~63 minutes. Sharding
absorbed that once; it cannot absorb it again, because sharding is a one-time
constant factor and cloning is not.

The growth is not people writing new assertions. It is one templated module
being copied per feature family: `test_missing_per_hole_boundary_outcomes_fail_closed`,
`..._per_pad_...`, `..._per_plate_...` — twelve hand-written copies of one three-line
body whose only difference is which `_<family>_model_outcomes` symbol it patches.
Each copy costs a real `build_drawing`, forever, on every CI run.

The two obvious ways to detect that both fail here, which is why it went unnoticed:
identical test NAMES miss it (every clone is renamed for its family) and identical
ASTs miss it (every clone substitutes its family's identifiers and literals). So this
guard compares test bodies with identifiers, attributes, literals and argument names
erased — the SHAPE — and only across modules, since same-module repetition is a much
weaker smell and often a deliberate readability choice.

The fix when this fails is not to raise the budget. It is to parametrize over the
one thing that varies, which keeps every assertion and deletes the copies. Lower the
budget whenever you do. Raising it needs a reason in the pull request body, the same
way `fail_under` in `[tool.coverage.report]` does.
"""

from __future__ import annotations

import ast
import collections
import hashlib
from pathlib import Path

TESTS = Path(__file__).parent

#: Cross-module structurally identical test bodies, counted as `group size - 1`.
#: Measured at 94 on 2026-09-07, after collapsing the thirteen `_<family>_model_outcomes`
#: copies and the eleven one-recognition copies into `tests/_evidence_contract.py`.
#: RATCHET DOWN ONLY — see the module docstring.
CLONE_BUDGET = 94

#: A body must have at least this many statements, and this many AST nodes, before it
#: counts. Short bodies (`build; assert`) collide by chance and would make the guard
#: noise rather than signal.
#:
#: The size gate counts NODES rather than the length of `ast.dump`. CI runs Python
#: 3.10 through 3.14, and `ast.dump` text gains and loses optional fields between
#: versions; 22 bodies sit within 15% of the equivalent character threshold, so any of
#: them could be counted on one interpreter and skipped on another and fail the gate on
#: one matrix leg only. A node count moves only when the grammar does.
_MIN_STATEMENTS = 3
_MIN_NODES = 16


class _Shape(ast.NodeTransformer):
    """Erase everything nameable, keep control flow and call structure."""

    def visit_Name(self, node: ast.Name) -> ast.Name:
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        self.generic_visit(node)
        return ast.copy_location(ast.Attribute(value=node.value, attr="_", ctx=node.ctx), node)

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        return ast.copy_location(ast.Constant(value="_"), node)

    def visit_arg(self, node: ast.arg) -> ast.arg:
        return ast.copy_location(ast.arg(arg="_"), node)

    def visit_keyword(self, node: ast.keyword) -> ast.keyword:
        self.generic_visit(node)
        return ast.copy_location(ast.keyword(arg="_", value=node.value), node)


def _clone_groups() -> dict[str, list[tuple[str, str]]]:
    """Structurally identical test bodies that span more than one module."""
    shapes: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
                continue
            body = [
                statement
                for statement in node.body
                # Drop the docstring: prose is where a clone legitimately differs.
                if not (
                    isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                )
            ]
            if len(body) < _MIN_STATEMENTS:
                continue
            normalised = _Shape().visit(ast.Module(body=body, type_ignores=[]))
            if sum(1 for _ in ast.walk(normalised)) < _MIN_NODES:
                continue
            digest = hashlib.md5(ast.dump(normalised).encode(), usedforsecurity=False).hexdigest()
            shapes[digest].append((path.name, node.name))
    return {
        digest: members
        for digest, members in shapes.items()
        if len({module for module, _ in members}) > 1
    }


def test_the_suite_does_not_grow_by_cloning_test_bodies_across_modules() -> None:
    groups = _clone_groups()
    redundant = sum(len(members) - 1 for members in groups.values())

    worst = sorted(groups.values(), key=len, reverse=True)[:5]
    report = "\n".join(
        f"  x{len(members)}  {members[0][1]}\n"
        + "\n".join(f"        {module}" for module, _ in members[:4])
        + ("\n        ..." if len(members) > 4 else "")
        for members in worst
    )
    assert redundant <= CLONE_BUDGET, (
        f"{redundant} cross-module clones, budget {CLONE_BUDGET}. The largest groups:\n"
        f"{report}\n\n"
        "A test body copied into another module with only its family's identifiers "
        "changed is not new evidence — it is the same assertion paid for again on every "
        "CI run. Parametrize over the symbol that varies and delete the copies, then "
        "lower CLONE_BUDGET. Raising it needs a reason in the PR body."
    )


def test_the_budget_is_not_slack() -> None:
    """A budget far above the real count silently permits the next wave of copies."""
    redundant = sum(len(members) - 1 for members in _clone_groups().values())

    assert redundant >= CLONE_BUDGET - 10, (
        f"only {redundant} cross-module clones against a budget of {CLONE_BUDGET}: "
        "consolidation happened without ratcheting the budget down, so the guard now "
        f"permits {CLONE_BUDGET - redundant} new copies before it complains. "
        f"Set CLONE_BUDGET = {redundant}."
    )
