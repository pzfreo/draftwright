"""Every printed number in `annotations/` must come from the compiler, not the renderer.

ADR 0016's boundary says a renderer may emit dimensional content only from the compiled
plan. `tests/test_compiled_plan_boundary.py` checks that as *behaviour* — an empty plan
draws nothing. This file checks the **cause**, because behaviour tests only cover the paths
their fixtures reach, and twice in #925 a whole dimensional path stayed outside the boundary
because no fixture walked it.

The cause has one shape. A renderer that calls `_fmt(x)` is turning a number into printed
text, which means the number reached it as a *number* rather than as the compiler's own
`value_text`. Every defect of this class in #921/#923/#925 looked exactly like that:

- the height ladder rebuilding rung labels from `StepLevelFeature.levels`;
- `render_slots` formatting `s.width` past a suppression flag;
- `_add_furniture` printing `feat.pitch`;
- `_locate_off_axis_holes` computing `abs(loc[i] - bb.min[i])` beside a compiler that had
  already computed it.

So this is a **shrink-only budget**, in the style of `test_private_test_attr_reads` (#741):
migrate a site onto `value_text`, lower the number, delete the entry at zero. A new or
grown entry fails. Nothing here is a target for its own sake — the point is that the set
cannot quietly grow, and that each survivor carries a reason someone wrote down.

Deliberately NOT counted: `_fmt` inside an f-string or a log/lint call (that text is prose
about a drawing, not text *on* one) and inside `_text_size` (measuring a label's width is a
layout question). Those exclusions are why the number is 15 rather than 61 — narrow enough
to read, which a 61-entry table would not be.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

#: Per-function ceiling for renderer-side number formatting. **Shrink only.**
#:
#: Each entry needs a reason, and "it was there already" is not one. The two groups below
#: are different kinds of debt and should not be confused: the first is a compiler contract
#: that has not been split yet, the second is a value that never enters the plan at all.
_FMT_BUDGET: dict[str, tuple[int, str]] = {
    # --- Locations: the Z-normal ladder is the one location entry with no per-axis value.
    # `render_locations` groups refs ACROSS features and dedups per axis before it knows
    # which dims exist, so an entry per axis would approve a mark whose existence the
    # renderer decides. Splitting it needs that grouping to move into the compiler. Every
    # other location family (off-axis holes, non-Z pockets, slots) is already per-axis.
    "from_model.render_locations": (4, "Z ladder groups across features before deduping"),
    "holes.add_feature_location": (2, "the live verb onto the same Z ladder"),
    # --- The turned step chain composes labels from run-length-collapsed segments, so the
    # text is decided during the collapse rather than per approved entry.
    "from_model.render_step_lengths": (4, "labels decided during the repeat-run collapse"),
    "from_model._draw_step_chain": (1, "same collapse, drawing half"),
    "from_model._redraw_y": (2, "same collapse, detail redraw"),
    # --- `hole_callout_spec` hands the callout builder floats, so the #261 "every value
    # crosses as a _fmt string" rule is enforced there. Fixing this means the spec carrying
    # approved text, which is the hole-callout migration still named in ADR 0016.
    "from_model.callout_from_spec": (1, "spec carries floats — the hc_ migration"),
    "from_model.f": (1, "the same builder's formatter"),
    # --- Not dimensional plan content at all.
    "orchestrator._maybe_tabulate_holes": (2, "hole-table cell text, not a placed dim"),
    "sections._overall_height_name": (1, "detail-view caption from the analysis"),
}

_ANNOTATIONS = pathlib.Path(__file__).resolve().parents[1] / "src" / "draftwright" / "annotations"

#: Calls whose arguments are prose or measurement, not text drawn on the sheet.
_NOT_A_DRAWN_LABEL = frozenset({"record_issue", "info", "warning", "debug", "_text_size"})


def _excused(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):  # an f-string: a message about a drawing
        return True
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return name in _NOT_A_DRAWN_LABEL
    return False


def _fmt_counts() -> dict[str, int]:
    """``{module.function: count}`` of renderer-side number formatting."""
    counts: dict[str, int] = {}
    for path in sorted(_ANNOTATIONS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            total = 0
            stack: list[tuple[ast.AST, bool]] = [(fn, False)]
            while stack:
                node, skip = stack.pop()
                skipping = skip or _excused(node)
                if (
                    not skipping
                    and isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_fmt"
                ):
                    total += 1
                stack.extend((child, skipping) for child in ast.iter_child_nodes(node))
            if total:
                counts[f"{path.stem}.{fn.name}"] = total
    return counts


def test_no_renderer_formats_a_number_the_compiler_did_not():
    """The ratchet. A new site, or a grown one, fails here."""
    counts = _fmt_counts()
    grown = {
        name: (n, _FMT_BUDGET.get(name, (0, ""))[0])
        for name, n in counts.items()
        if n > _FMT_BUDGET.get(name, (0, ""))[0]
    }
    assert not grown, (
        f"{grown} formats a number the compiler did not — the printed value should be the "
        "approved entry's `value_text`. If the entry genuinely has no text yet, that is a "
        "gap in the COMPILER, not a licence to format here: add the value to the approved "
        "entry. (name: (found, allowed))"
    )


def test_the_budget_has_no_stale_entries():
    """Shrink-only means the table tracks reality downward, not that it is decoration.

    A ceiling above the real count is how a ratchet stops ratcheting: the next regression
    fits inside the slack and never fails. Every entry must be exactly its site count."""
    counts = _fmt_counts()
    stale = {
        name: (counts.get(name, 0), allowed)
        for name, (allowed, _why) in _FMT_BUDGET.items()
        if counts.get(name, 0) != allowed
    }
    assert not stale, (
        f"{stale} — lower the entry to the real count (or delete it at zero); slack in a "
        "shrink-only budget silently absorbs the next regression. (name: (real, allowed))"
    )


@pytest.mark.parametrize("name", sorted(_FMT_BUDGET))
def test_every_survivor_carries_a_reason(name):
    """A budget entry without a stated reason is a TODO nobody will read as one."""
    _allowed, why = _FMT_BUDGET[name]
    assert len(why) > 20, f"{name} needs a reason saying why the value is not compiled yet"
