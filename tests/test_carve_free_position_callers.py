"""Fail-closed guard on ``carve_free_position`` callers (ADR 0009; #636).

``carve_free_position`` (`annotations/_common.py`) returns a single free tier
*without* joining the shared corridor solve — the "solver-invisible" placement
the ADR 0009 collect-then-solve model exists to retire. Its guarantee (the
invisible-occupant collision class removed **by construction**) only holds once
every auto-pass strip occupant is a candidate in the solve. #636 migrates the
remainder; this guard stops the legacy path from silently attracting **new**
callers (two 0.3.0 features, plates #559 and step positions #555, already
regressed onto it before the migration — that is the failure mode this test
prevents from recurring).

The guard is a **fail-closed allowlist** of ``(file, top-level function)`` pairs.
Adding a new ``carve_free_position`` call anywhere in ``annotations/`` trips the
test; the fix is to make the site a corridor candidate (the Amendment 8 pattern),
not to widen the allowlist. The allowlist may only grow for a site recorded as an
explicit exemption in ADR 0009's remaining-migration note.
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
    # Permanent exemption (ADR 0009 Amendment 8): the pitch-dim fallback searches an
    # arbitrary diagonal outward vector — a diagonal dim cannot occupy a 1-D
    # axis-aligned strip tier, so it cannot be a solve candidate at all.
    ("holes.py", "_place_pitch_dim"),
    # Pending migration (#636), each with a genuine design fork:
    ("from_model.py", "render_height_ladder"),  # leapfrog witness cursor — needs rework
    ("from_model.py", "render_gdt"),  # PMI alt-strip fallback runs inside on_drop, post-drain
    ("holes.py", "add_feature_callout"),  # detect-only verb — no shared corridor drain
    ("holes.py", "add_feature_location"),  # detect-only verb — no shared corridor drain
}


def _carve_callers(path: Path) -> set[str]:
    """Names of the top-level functions in *path* that call ``carve_free_position``
    (directly or from a nested closure)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    callers: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if any(_is_carve_call(c) for c in ast.walk(node)):
                callers.add(node.name)
    return callers


def _is_carve_call(node: ast.AST) -> bool:
    """True for a call whose callee is *named* ``carve_free_position`` — whether bare
    (``carve_free_position(...)``, the actual import style here) or module-qualified
    (``_common.carve_free_position(...)``). A rename-on-import alias (``import ... as
    carve``) would still slip through — static name-matching can't follow that — but
    the direct and qualified forms cover every real caller and the obvious evasion."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id == "carve_free_position"
    if isinstance(fn, ast.Attribute):
        return fn.attr == "carve_free_position"
    return False


def test_carve_free_position_callers_are_allowlisted():
    """No ``annotations/`` function calls ``carve_free_position`` outside the allowlist."""
    found: set[tuple[str, str]] = set()
    for path in sorted(_ANNO_DIR.glob("*.py")):
        for fn in _carve_callers(path):
            found.add((path.name, fn))
    new = found - _ALLOWED_CALLERS
    assert not new, (
        "New carve_free_position caller(s) in annotations/ — place geometry through the "
        "shared corridor solve (register_corridor + CorridorCandidate, the ADR 0009 "
        f"Amendment 8 pattern), not the solver-invisible carve. Offenders: {sorted(new)}. "
        "A genuine exemption must first be recorded in ADR 0009's remaining-migration note "
        "(#636)."
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
    assert any(_is_carve_call(c) for c in ast.walk(fn))


def test_migrated_passes_have_no_carve_caller():
    """Positive control: the #636-migrated passes are clean (regression tripwire)."""
    callers = _carve_callers(_ANNO_DIR / "from_model.py")
    assert "render_plates" not in callers
    assert "render_step_positions" not in callers
