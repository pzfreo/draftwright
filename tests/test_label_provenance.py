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

Deliberately NOT counted: `_fmt` inside a log/lint call (prose *about* a drawing, not text
*on* one) and inside `_text_size` (measuring a label's width is a layout question). Those
are call-site facts, which is the only kind of exemption that means anything here.

**An earlier version also excused every f-string, and that was wrong.** Renderers build most
of their labels with f-strings — `f"C{...}"`, `f"ø{...}"`, `f"{n - 1}× {...}"` — so the
exemption hid the majority of the very sites the ratchet exists to find, and
`label = f"ø{_fmt(raw.diameter)}"` passed while the identical `label = _fmt(raw.diameter)`
failed (#925 review). **Syntax used to decorate a value cannot decide whether the value is
compiler-owned.** Both forms are canaried below so the hole cannot reopen.
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
    # --- Locations. The Z-normal ladder is the one location family without per-axis values:
    # `render_locations` groups refs ACROSS features and dedups per axis before it knows
    # which dims exist, so an entry per axis would approve a mark whose existence the
    # renderer decides. Splitting it means moving that grouping into the compiler. Every
    # other family (off-axis holes, non-Z pockets, slots) is already per-axis.
    "from_model.render_locations": (4, "Z ladder groups across features before deduping"),
    "holes.add_feature_location": (2, "the live verb onto that same Z ladder"),
    # --- The turned step chain composes its labels while collapsing repeat runs, so the
    # text is decided by the collapse rather than per approved entry.
    "from_model.render_step_lengths": (3, "labels decided during the repeat-run collapse"),
    "from_model.render_step_lengths._redraw_y": (3, "the same collapse, detail redraw"),
    "from_model._draw_step_chain": (2, "the same collapse, drawing half"),
    # --- The ø-leader placers take `(anchor, dia, feature, tol, thread)` items, where `dia`
    # is a float BOTH printed and used for geometry (`az - dia / 2`). Carrying the approved
    # text alongside it is a five-call-site change to the item tuple; named here rather than
    # rushed, because the budget's job is to make it visible.
    "from_model._diameter_row_below": (2, "item tuple carries a float for tip geometry"),
    "from_model._diameter_column_left": (2, "item tuple carries a float for tip geometry"),
    "from_model.render_diameters": (1, "builds those items from approved entries"),
    "from_model.render_boss_diameters": (1, "builds those items from approved entries"),
    # --- `hole_callout_spec` hands the callout builder floats, and the #261 invariant
    # ("every value crosses as a _fmt string") is enforced there. Fixing it means the spec
    # carrying approved text — the hole-callout migration ADR 0016 still names as pending.
    "from_model.callout_from_spec.f": (1, "the spec carries floats — the hc_ migration"),
    # --- A chamfer's ANGLE is a form discriminator, not a planned parameter:
    # `ChamferFeature.parameters()` emits only the leg, so the angle has no approved text to
    # consume. That is the IR gap `_FACTS` records; the leg itself now uses `value_text`.
    "from_model._chamfer_label": (1, "ch.angle is a fact with no DimParameter (IR gap)"),
    # --- Not dimensional plan content at all.
    "orchestrator._maybe_tabulate_holes": (3, "hole-table cell text, not a placed dim"),
    "sections._overall_height_name": (1, "detail-view caption from the analysis"),
}

_ANNOTATIONS = pathlib.Path(__file__).resolve().parents[1] / "src" / "draftwright" / "annotations"

#: Calls whose arguments are prose or measurement, not text drawn on the sheet.
_NOT_A_DRAWN_LABEL = frozenset({"record_issue", "info", "warning", "debug", "_text_size"})


def _prose_call(node: ast.AST) -> bool:
    """Is this a call whose arguments are prose or measurement rather than a drawn label?

    The ONLY exemption shape, deliberately. An exemption keyed on syntax — "it is inside an
    f-string" — says nothing about where the number came from, and hid most of the real
    sites (#925 review)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name in _NOT_A_DRAWN_LABEL


def _fmt_counts() -> dict[str, int]:
    """``{module.outer.inner: count}`` of renderer-side number formatting.

    Attribution is LEXICAL: traversal stops at a nested `def`, which then gets its own
    dotted key. The first version walked nested functions from both their own entry and
    their parent's — double-counting — and keyed on the bare name with assignment, so nine
    duplicated names (`_place`, `_build`, `_drop` ×6) silently overwrote one another. A
    ceiling that the wrong function's count can satisfy is not a ratchet (#925 review).
    """
    counts: dict[str, int] = {}

    def walk(node: ast.AST, scope: str, excused: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                walk(child, f"{scope}.{child.name}", False)  # its own scope, fresh context
                continue
            child_excused = excused or _prose_call(child)
            if (
                not child_excused
                and isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "_fmt"
            ):
                counts[scope] = counts.get(scope, 0) + 1
            walk(child, scope, child_excused)

    for path in sorted(_ANNOTATIONS.glob("*.py")):
        walk(ast.parse(path.read_text(encoding="utf-8")), path.stem, False)
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


@pytest.mark.parametrize(
    "snippet",
    [
        "def _canary(x):\n    return _fmt(x)\n",
        # The form the first version missed entirely. Renderers build most labels this way,
        # so a guard blind to it is blind to the majority of what it claims to cover.
        'def _canary(x):\n    return f"ø{_fmt(x)}"\n',
        # Nested, to prove lexical attribution: this must be found under its own key rather
        # than folded into (or overwritten by) another function of the same name.
        "def _canary(x):\n    def _inner(y):\n        return _fmt(y)\n    return _inner(x)\n",
    ],
    ids=["direct", "inside-f-string", "nested"],
)
def test_the_ratchet_actually_bites(tmp_path, monkeypatch, snippet):
    """A guard nobody has seen fail is a guard nobody has tested.

    The first version was canaried on the direct form only, and passed a regression written
    as `label = f"ø{_fmt(raw.diameter)}"` — the form production code actually uses
    (#925 review). Each shape it must catch is now exercised."""
    package = tmp_path / "annotations"
    package.mkdir()
    (package / "probe.py").write_text(snippet, encoding="utf-8")
    monkeypatch.setattr("test_label_provenance._ANNOTATIONS", package)
    assert _fmt_counts(), f"the ratchet does not see this shape:\n{snippet}"


def test_prose_is_still_exempt(tmp_path, monkeypatch):
    """The false-positive half: a log line about a drawing is not a label on one."""
    package = tmp_path / "annotations"
    package.mkdir()
    (package / "probe.py").write_text(
        'def _canary(x):\n    _log.info("skipped %s", _fmt(x))\n', encoding="utf-8"
    )
    monkeypatch.setattr("test_label_provenance._ANNOTATIONS", package)
    assert not _fmt_counts()
