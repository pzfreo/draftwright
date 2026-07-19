"""Fail-closed guard on ``carve_free_position`` callers (ADR 0014; #636).

``carve_free_position`` (`annotations/_common.py`) returns a single free tier
*without* joining the shared corridor solve — the "solver-invisible" placement
the ADR 0014 collect-then-solve model exists to retire. Its guarantee (the
invisible-occupant collision class removed **by construction**) only holds once
every auto-pass strip occupant is a candidate in the solve. #636 migrates the
remainder; this guard stops the legacy path from silently attracting **new**
callers (two 0.3.0 features, plates #559 and step positions #555, already
regressed onto it before the migration — that is the failure mode this test
prevents from recurring).

The guard is a **fail-closed allowlist** of ``(file, function)`` pairs. Every
``carve_free_position`` call anywhere under ``annotations/`` is attributed to a caller
— a top-level function, a class method, a nested closure's owning function, or
``"<module>"`` for a bare module-scope call — and any caller outside the allowlist
trips the test. The fix is to make the site a corridor candidate (the ADR 0014
pattern), not to widen the allowlist. The allowlist may only grow for a site recorded
as an explicit exemption in ADR 0014. Import aliases
(``from ._common import carve_free_position as carve``) and module-qualified calls
(``_common.carve_free_position(...)``) are both tracked; only a runtime rebinding
(``carve = carve_free_position``) escapes, which is contrived rather than a foot-gun.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ANNO_DIR = Path(__file__).resolve().parent.parent / "src" / "draftwright" / "annotations"

# The only ``annotations/`` functions permitted to call ``carve_free_position``.
# Each is either a permanent exemption or a site still tracked by #636 for
# migration. Removing an entry as its site migrates is the intended direction;
# the allowlist must never grow for un-migrated new work.
_ALLOWED_CALLERS = {
    # Permanent exemption (ADR 0014): the pitch-dim fallback searches an
    # arbitrary diagonal outward vector — a diagonal dim cannot occupy a 1-D
    # axis-aligned strip tier, so it cannot be a solve candidate at all.
    ("holes.py", "_place_pitch_dim"),
    # Pending migration (#636), each with a genuine design fork:
    ("from_model.py", "render_gdt"),  # alt-strip fallback DEFERRED via ctx.post_drain (#636)
    # Beyond render_gdt's pattern (helpers ≥0.14): the primary placement IS the
    # corridor candidate; the carve runs in a ctx.post_drain-DEFERRED drop fallthrough,
    # after EVERY corridor has drained (#684 review — a mid-drain carve could preempt
    # a later sibling's reserved corner), retrying the opposite/side-view strip.
    ("from_model.py", "render_plates"),
    # Manual post-build verbs (the #426 half of the convergence): a single user-driven
    # annotation onto a FINISHED sheet, where every occupant is already placed — the
    # carve is the correct tool and there is no shared drain to join. Retained as an
    # explicit exemption by ADR 0014 (#636 close-out).
    ("holes.py", "add_feature_callout"),
    ("holes.py", "add_feature_location"),
}


def _carve_local_names(tree: ast.Module) -> set[str]:
    """Every local name that refers to ``carve_free_position`` in this module: the canonical
    name plus any ``from ._common import carve_free_position as X`` alias. This closes the
    import-alias evasion (the realistic one). A runtime rebinding (``carve =
    carve_free_position``) is still not followed — but that is contrived, not a foot-gun."""
    names = {"carve_free_position"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "carve_free_position" and a.asname:
                    names.add(a.asname)
    return names


def _carve_callers(path: Path) -> set[str]:
    """Names of the functions in *path* that call ``carve_free_position`` — every carve
    call, wherever it lives, maps to a caller so the guard is genuinely fail-closed. A
    call is attributed to its OUTERMOST enclosing function within the current module or
    class scope: a nested closure's carve counts against the top-level function that owns
    it (``render_plates``, not ``_build``), a class method's against the method name, and
    a bare module-scope call against ``"<module>"``. Any of these outside the allowlist
    trips the test."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names = _carve_local_names(tree)
    callers: set[str] = set()

    def visit(node: ast.AST, owner: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, None)  # a class body's methods each become their own owner
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, owner or child.name)  # the outermost fn owns nested closures
            else:
                if _is_carve_call(child, names):
                    callers.add(owner or "<module>")
                visit(child, owner)

    visit(tree, None)
    return callers


def _is_carve_call(node: ast.AST, names: set[str]) -> bool:
    """True for a call whose callee resolves to ``carve_free_position`` in this module —
    a bare or aliased local name (``names``), or a module-qualified attribute
    (``_common.carve_free_position(...)``)."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id in names
    if isinstance(fn, ast.Attribute):
        return fn.attr == "carve_free_position"
    return False


def test_carve_free_position_callers_are_allowlisted():
    """No ``annotations/`` function calls ``carve_free_position`` outside the allowlist."""
    found: set[tuple[str, str]] = set()
    for path in sorted(_ANNO_DIR.rglob("*.py")):  # rglob: covers any future subpackage too
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(_ANNO_DIR).as_posix()  # not path.name — a subpackage file
        for fn in _carve_callers(path):  # sharing a basename must not inherit the exemption
            found.add((rel, fn))
    new = found - _ALLOWED_CALLERS
    assert not new, (
        "New carve_free_position caller(s) in annotations/ — place geometry through the "
        "shared corridor solve (register_corridor + CorridorCandidate, the ADR 0014 "
        f"pattern), not the solver-invisible carve. Offenders: {sorted(new)}. "
        "A genuine exemption must first be recorded by amending ADR 0014."
    )
    # The allowlist must not carry stale entries: a migrated site is removed, not left.
    gone = _ALLOWED_CALLERS - found
    assert not gone, f"Allowlisted callers no longer exist — prune them from #636: {sorted(gone)}"


def test_guard_detects_a_synthetic_caller():
    """The detector is not a tautology: it flags a fresh carve call in any function."""
    src = "def render_new(dwg):\n    pos = carve_free_position(dwg, s, v, 'y', t, p)\n    return pos\n"
    tree = ast.parse(src)
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    assert any(_is_carve_call(c, {"carve_free_position"}) for c in ast.walk(fn))


def test_migrated_passes_have_no_carve_caller():
    """Positive control: the #636-migrated passes are clean (regression tripwire)."""
    callers = _carve_callers(_ANNO_DIR / "from_model.py")
    # render_plates' PRIMARY placement stays corridor-registered; its allowlisted carve
    # is the post-drain on_drop fallthrough only (as render_gdt) — helpers ≥0.14.
    assert "render_step_positions" not in callers


def test_carve_callers_covers_methods_qualified_aliased_and_module_scope(tmp_path):
    """The attribution is fail-closed beyond top-level bare calls: a class method, a
    module-qualified callee, an import alias, a nested closure, and a bare module-scope
    call each surface."""
    src = (
        "from ._common import carve_free_position as carve\n"  # alias import
        "carve_free_position(a)\n"  # module scope → "<module>"
        "class R:\n"
        "    def m(self):\n"
        "        return _common.carve_free_position(a)\n"  # method + qualified callee
        "def render_outer():\n"
        "    def _inner():\n"
        "        return carve_free_position(a)\n"  # closure → attributed to render_outer
        "    return _inner\n"
        "def render_aliased():\n"
        "    return carve(a)\n"  # aliased local name → still caught
    )
    p = tmp_path / "probe.py"
    p.write_text(src)
    assert _carve_callers(p) == {"<module>", "m", "render_outer", "render_aliased"}
