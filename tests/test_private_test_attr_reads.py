"""Ratchet: tests may read only a DOCUMENTED, shrinking set of ``Drawing`` private attributes.

The #720/#721 family closed test reach-through into the *aliased* privates (the seven with a
public read equivalent, 361 sites) and #722 froze the **src**-side ``dwg._*`` reads to zero. The
last unpoliced quadrant (#741) is test-side **attribute reads** of ``Drawing`` internals that have
no public read yet — ``_analysis``/``_intents``/… They coupled the tests to build-state internals
with no ratchet, so they could silently multiply.

This pins today's read-sites as a per-name ceiling that may only **SHRINK**: thread a read through
a new/existing public surface, lower its count; when it reaches zero, delete the entry. A NEW
private name, or a GROWN count on an existing one, fails here — nudging the author onto the public
seam (as ``_registry`` → :pyattr:`Drawing.registry`, ``_part_model`` → :pymeth:`Drawing.model`).

Scope is **reads** (attribute access in ``Load`` context + ``getattr(_, "_name")`` probes), the
#741 title; private *writes* (chiefly ``dwg._defer_intents = …``, which should become
``with dwg.deferred():``) are the separate follow-on and are eliminated, not allowlisted.

Keyed on the attribute *name* (∈ :data:`_DRAWING_PRIVATES`), not the receiver — test receivers
vary (``dwg``/``d``/``direct``/``scripted``/…), unlike the src guard's ``dwg``/``drawing``. A stray
same-named private on a non-Drawing object would be pinned too (harmless, fail-closed). Mirrors
``test_private_test_imports`` / ``test_drawing_encapsulation``; stdlib ``ast`` + ``pathlib`` only
(:func:`test_drawing_privates_set_is_current` alone imports ``Drawing``, to keep the name-set honest).
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_SELF = Path(__file__).name

# ``Drawing``'s private attributes — ``__init__`` instance attrs ∪ class-level privates
# (properties/methods). Hardcoded so the scanner stays import-light;
# :func:`test_drawing_privates_set_is_current` imports ``Drawing`` and fails if this drifts, so a
# newly added private cannot escape the guard by being absent from the name-set.
_DRAWING_PRIVATES: frozenset[str] = frozenset(
    {
        # instance attributes (set in Drawing.__init__)
        "_build",
        "_coords",
        "_coverage",
        "_cyl_cache",
        "_defer_intents",
        "_intents",
        "_model_declared",
        "_registry",
        # class-level privates (properties / methods)
        "_add_balloon",
        "_add_shapes",
        "_analysis",
        "_ann_box_cache",
        "_anno_view",
        "_build_issues",
        "_classify_intents",
        "_derive_span",
        "_drain_intents",
        "_dropped_callout_diams",
        "_hole_spec_groups",
        "_is_scattered_hole_doc",
        "_lint_and_log",
        "_named",
        "_part_model",
        "_pattern_callouts",
        "_patterned_holes",
        "_pinned",
        "_queue_dimension_intent",
        "_record_build_issue",
        "_replay_intent",
        "_resolve_dimension_span",
        "_user_dim_uses_corridor",
        "_view_edge_cache",
        "_write_dxf",
        "_write_svg",
    }
)

# Per-name READ-site ceiling — shrink-only (#741). Migrate a read onto the public surface, lower
# the number; delete the entry at zero. A new/grown read fails :func:`test_no_new_or_grown_...`.
_ALLOW: dict[str, int] = {
    "_analysis": 82,
    "_intents": 25,
    "_coords": 5,
    "_record_build_issue": 5,
    "_coverage": 4,
    "_ann_box_cache": 3,
    "_defer_intents": 3,
    "_write_dxf": 2,
    "_model_declared": 1,
    "_add_balloon": 1,
    "_is_scattered_hole_doc": 1,
}


def _read_counts(tree: ast.Module) -> Counter[str]:
    """Count ``<recv>._<name>`` reads (``Load`` context) + ``getattr(<recv>, "_name", …)`` probes
    for every ``_name`` in :data:`_DRAWING_PRIVATES`. Store/Del targets (writes) are excluded."""
    counts: Counter[str] = Counter()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in _DRAWING_PRIVATES
            and isinstance(node.ctx, ast.Load)
        ):
            counts[node.attr] += 1
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _DRAWING_PRIVATES
        ):
            counts[node.args[1].value] += 1
    return counts


def _scan_all() -> Counter[str]:
    total: Counter[str] = Counter()
    for path in sorted(_TESTS.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == _SELF:
            continue
        total.update(_read_counts(ast.parse(path.read_text(encoding="utf-8"))))
    return total


def test_no_new_or_grown_drawing_private_reads() -> None:
    """No test may read a Drawing private the allowlist does not already sanction (a new name),
    nor add reads beyond a name's pinned ceiling (a grown count)."""
    actual = _scan_all()
    over = {n: (c, _ALLOW.get(n, 0)) for n, c in actual.items() if c > _ALLOW.get(n, 0)}
    assert not over, (
        "new or grown test-side reads of Drawing privates (#741) — thread them through the public "
        f"read surface, do not add reach-through:\n{over}\n(name: (found, allowed))"
    )


def test_allowlist_is_tight_no_stale_or_slack_entries() -> None:
    """The ratchet only ratchets if it stays exact: an entry whose real count dropped (a migrated
    read) must be lowered here, and a fully-migrated name deleted — so the ceiling tracks reality."""
    actual = _scan_all()
    slack = {
        n: (actual.get(n, 0), allowed)
        for n, allowed in _ALLOW.items()
        if actual.get(n, 0) < allowed
    }
    assert not slack, (
        "reads were migrated but the allowlist was not lowered (#741) — set each entry to the real "
        f"count, delete it at zero:\n{slack}\n(name: (found, allowed))"
    )


def test_drawing_privates_set_is_current() -> None:
    """Guard the name-set: every real ``Drawing`` private must be in :data:`_DRAWING_PRIVATES`, so a
    newly added private is covered by the scanner rather than silently escaping it."""
    import inspect
    import re

    from draftwright.drawing import Drawing

    init_attrs = set(
        re.findall(r"self\.(_[a-z][a-z_]*)\s*[:=]", inspect.getsource(Drawing.__init__))
    )
    class_privates = {n for n in vars(Drawing) if n.startswith("_") and not n.startswith("__")}
    actual = {
        n for n in (init_attrs | class_privates) if not n.isupper()
    }  # drop constants (_EXPORT_FORMATS)
    missing = actual - _DRAWING_PRIVATES
    assert not missing, (
        f"Drawing gained private(s) not in _DRAWING_PRIVATES — add them so reads are policed: {missing}"
    )
