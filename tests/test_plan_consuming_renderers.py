"""Fail-closed ratchet: a renderer fed the plan must honour the plan's suppression.

`PlannedDimension.suppressed` is the planner's decision that a measurement is not
drawn — a square footprint's redundant depth, a turned part's doubled extent, or a
measurement omitted from an authored set (ADR 0016 / #876). Marking is only half the
mechanism: it means nothing unless the renderer *reads* it.

Three review rounds on #921 each found renderers that did not. The first round found
the authored set never reaching the planner at all; the second found
`render_height_ladder` and `render_step_positions` rebuilding their marks from the
feature and the bounding box; the third found the whole turned family —
`render_diameters`, `render_boss_diameters`, `render_step_lengths`, `render_rotational`
— selecting parameters with no suppression check, so authoring one step length still
drew every diameter on the part.

Each was found by a fixture that happened to cover it, which is exactly the wrong way
to find the fourth. This guard is structural instead: every renderer handed the planned
groups must consult suppression somewhere, and a new one that forgets fails here rather
than in whichever drawing a user notices first.
"""

from __future__ import annotations

import ast
import pathlib

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "draftwright"
_FROM_MODEL = _SRC / "annotations" / "from_model.py"

# Helpers that encapsulate a suppression reading. A renderer delegating to one of these
# has consulted the plan just as surely as one testing `pd.suppressed` itself.
_SANCTIONED = {"env_dim_placed", "set_dim_placed"}

# Renderers that legitimately read the FEATURE rather than the planned dimension, with
# the reason each is not a dimension. Anything added here needs the same kind of reason:
# "it was easier" is how the defect above got in.
_NOT_DIMENSIONS = {
    # Centre marks are furniture sizing themselves off the hole they mark. #875 made
    # them read `hole.diameter` on purpose: sized from the suppressible parameter, a
    # suppressed ⌀20 collapsed its mark from 42 mm to the 2.5 mm floor. A centre mark
    # shows where an axis is; it prints no value, so there is nothing to suppress.
    "render_centermarks",
}


def _plan_consuming_renderers() -> dict[str, ast.FunctionDef]:
    """Every `render_*` in `from_model.py` that is handed the planned groups."""
    tree = ast.parse(_FROM_MODEL.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("render_"):
            continue
        args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        if "groups" in args:
            out[node.name] = node
    return out


def _consults_suppression(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr == "suppressed":
            return True
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name in _SANCTIONED:
                return True
    return False


def test_the_guard_is_looking_at_something():
    """A ratchet that matches nothing passes forever. Pin the population so a rename or
    a moved module fails loudly instead of quietly guarding an empty set."""
    found = _plan_consuming_renderers()
    assert len(found) >= 12, f"expected the renderer family, found {sorted(found)}"
    assert "render_diameters" in found and "render_rotational" in found


def test_every_plan_fed_renderer_honours_suppression():
    offenders = sorted(
        name
        for name, fn in _plan_consuming_renderers().items()
        if name not in _NOT_DIMENSIONS and not _consults_suppression(fn)
    )
    assert not offenders, (
        f"{offenders} take the planned groups but never consult `suppressed`. A planner "
        "decision the renderer ignores is not a decision: the plan will say a dimension "
        "is omitted while the drawing carries it. Read `pd.suppressed` (or a sanctioned "
        f"helper: {sorted(_SANCTIONED)}), or add the renderer to _NOT_DIMENSIONS with a "
        "reason it prints no value."
    )


def test_the_exemptions_still_exist():
    """An exemption for a deleted function silently widens the guard."""
    found = _plan_consuming_renderers()
    assert not (_NOT_DIMENSIONS - set(found)), "stale exemption in _NOT_DIMENSIONS"
