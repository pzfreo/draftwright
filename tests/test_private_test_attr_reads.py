"""Ratchet: tests may read only a DOCUMENTED, shrinking set of ``Drawing`` private attributes.

The #720/#721 family closed test reach-through into the *aliased* privates (the seven with a
public read equivalent, 361 sites) and #722 froze the **src**-side ``dwg._*`` reads to zero. The
last unpoliced quadrant (#741) is test-side **attribute reads** of ``Drawing`` internals that have
no public read yet — ``_analysis``/``_intents``/… They coupled the tests to build-state internals
with no ratchet, so they could silently multiply.

This pins today's read-sites as a per-name ceiling that may only **SHRINK**: thread a read through a
public surface (``_registry`` → :pyattr:`Drawing.registry`), lower its count; at zero, delete the
entry. A NEW private name, or a GROWN count on an existing one, fails here. The remaining entries are
NOT all latent public surface: the #741 triage found most are *intentional white-box* (the #647
transaction/rollback tests, and unit tests of internal render/layout helpers that take the raw
``Analysis``) — pinned with the rationale in :data:`_ALLOW`, exactly as ``test_private_test_imports``
keeps its legitimate helper tests. The ceiling stops the surface *growing*; it does not oblige
exposing engine internals (adding an accessor for an internal value no caller wants would just
rename the coupling — the anti-pattern #741 explicitly warns against).

Scope is **reads** (``Load``-context attribute access, ``getattr(_, "_name")`` probes, and
``AugAssign`` targets). Private *writes* (chiefly ``dwg._defer_intents = …``) are NOT counted here:
they are the transaction-test cluster above and legitimately drive the flag directly (``deferred()``
auto-finalizes, so it can't express "fail mid-drain, inspect state"), so there is nothing to migrate.

Keyed on the attribute *name* (∈ :data:`_DRAWING_PRIVATES`), not the receiver — test receivers
vary (``dwg``/``d``/``direct``/``scripted``/…), unlike the src guard's ``dwg``/``drawing``. A stray
same-named private on a non-Drawing object would be pinned too (harmless, fail-closed). Mirrors
``test_private_test_imports`` / ``test_drawing_encapsulation``; stdlib ``ast`` + ``pathlib`` only
(:func:`test_drawing_privates_set_is_current` alone imports ``Drawing``, to keep the name-set honest).

**A ratchet, not a sandbox** (the sibling guards' stance). Two limits are accepted by design:

- *Static reflection is unresolvable.* ``getattr(dwg, name)`` with a non-literal ``name``,
  ``dwg.__dict__["_analysis"]``, ``vars(dwg)[…]``, ``object.__getattribute__`` — the scanner sees
  only the *common, honest* forms (attribute access, ``getattr`` with a constant, ``+=`` targets).
  Reflective escapes can't be caught statically; they are rare and would surface in review.
- *This is a CARDINALITY ratchet.* The guarantee is that the **net per-name read count never
  grows** — you cannot add reach-through without the total rising. It does not pin individual
  sites, so migrating one read while adding another of the same name (net zero) is permitted; that
  is deliberate (net non-increasing coupling), and it keeps the allowlist line-number-churn-free.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

_TESTS = Path(__file__).resolve().parent

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
#
# The #741 triage (2026-07): most of these reads are NOT latent public surface waiting to be
# threaded — they are *intentional white-box*, the same category the sibling import-guard keeps
# rather than forcing onto a public seam. Migratable reads with a real public equivalent were
# already threaded (``_registry`` → ``registry`` in PR 1); the reads that remain either drive
# internal machinery a public API can't express (the transaction cluster) or read internal values
# no caller wants (layout geometry, the chosen scale, classification). They are pinned WITH the
# rationale below so the count is a documented policy, not a TODO. The ratchet's job is to stop the
# surface *growing*; it does not oblige exposing engine internals.
_ALLOW: dict[str, int] = {
    # The #647 transaction/rollback test cluster: set defer, record intents, monkeypatch a
    # mid-drain pass to raise, then inspect the half-drained _intents/_coverage to assert rollback.
    # `with deferred():` auto-finalizes cleanly and CANNOT express "fail mid-drain + inspect" —
    # so these legitimately drive the low-level machinery. White-box by nature.
    "_intents": 25,
    "_coverage": 4,
    "_defer_intents": 3,
    # Analysis (build context, ADR 0005): tests that unit-test an internal render/layout helper by
    # passing it the whole `Analysis`, or that read layout internals (PV_X/cx/margin/zones/proj) or
    # assert classification/metadata/chosen-scale state. Not user-facing surface — even the
    # seemingly-public reads are internal: the *chosen* scale (`_analysis.SCALE`) differs from the
    # already-public *requested* `dwg.scale`, and part classification/title-block metadata have no
    # user-facing read. Accessors are deferred until a real caller needs them, not added
    # speculatively to zero this number (#741 triage — the anti-pattern the issue itself warns of).
    "_analysis": 82,
    # View-coordinate internals (set_view_coordinates writes; these reads inspect the mapping).
    "_coords": 5,
    # Private build-issue recorder driven directly by lint/repair tests.
    "_record_build_issue": 5,
    # Annotation bounding-box cache internals.
    "_ann_box_cache": 3,
    # Private DXF export path exercised directly.
    "_write_dxf": 2,
    # Stragglers — a declared-model flag, the balloon add path, a doc-classification flag.
    "_model_declared": 1,
    "_add_balloon": 1,
    "_is_scattered_hole_doc": 1,
}


def _read_counts(tree: ast.Module) -> Counter[str]:
    """Count ``<recv>._<name>`` reads (``Load`` context) + ``getattr(<recv>, "_name", …)`` probes
    for every ``_name`` in :data:`_DRAWING_PRIVATES`. A plain assignment/``del`` target (``Store``/
    ``Del``) is excluded, but an ``AugAssign`` target (``dwg._intents += …``) IS counted — ``+=``
    reads the old value before writing, so it couples like a read (Codex #814)."""
    counts: Counter[str] = Counter()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in _DRAWING_PRIVATES
            and isinstance(node.ctx, ast.Load)
        ):
            counts[node.attr] += 1
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Attribute)
            and node.target.attr in _DRAWING_PRIVATES
        ):
            # The target carries Store context (missed by the Load branch), but ``x._p += y``
            # reads ``x._p`` first — a read-modify-write reach-through.
            counts[node.target.attr] += 1
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
    # This guard file is NOT exempted (Codex #814 L5): it only references the private names as
    # string constants (in _DRAWING_PRIVATES / _ALLOW), which are not attribute reads, so it scans
    # to zero — and a future real reach-through added here is then policed like anywhere else.
    for path in sorted(_TESTS.rglob("*.py")):
        if "__pycache__" in path.parts:
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


class _ReceiverStores(ast.NodeVisitor):
    """Collect ``<recv>._x = …`` / ``del`` private-attribute stores in a method body, where
    *recv* is that method's first parameter (its ``self``). SCOPE-AWARE (Codex #814 r2): does
    NOT descend into a nested ``class`` — a helper class defined inside a method has its own
    ``self``, and its stores are not ``Drawing`` privates. Nested *functions* (closures) still
    close over the method's ``self``, so their stores are kept."""

    def __init__(self, recv: str) -> None:
        self.recv = recv
        self.names: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass  # a nested class rebinds `self`; its stores aren't Drawing's — don't recurse

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == self.recv
            and node.attr.startswith("_")
            and not node.attr.startswith("__")
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            self.names.add(node.attr)
        self.generic_visit(node)


def test_drawing_privates_set_is_current() -> None:
    """Guard the name-set: every real ``Drawing`` private must be in :data:`_DRAWING_PRIVATES`, so a
    newly added private is covered by the scanner rather than silently escaping it.

    Discovers ``self._x = …`` Store attributes across ``Drawing``'s own methods — the WHOLE class,
    not only ``__init__`` (Codex #814 H1: ``Drawing`` sets ``_build_issues`` in ``finalize`` etc.),
    but scope-aware so a nested helper class's ``self`` is not miscredited (r2) — plus every
    class-level private in ``vars(Drawing)``. Still a best-effort SYNTACTIC scan: a private reached
    only via a ``self`` alias (``d = self; d._x = …``), ``setattr``/``__dict__``, or inheritance
    can't be discovered statically — the same accepted static-analysis limit as the read scanner."""
    import inspect

    from draftwright.drawing import Drawing

    cls = next(
        n
        for n in ast.walk(ast.parse(inspect.getsource(Drawing)))
        if isinstance(n, ast.ClassDef) and n.name == "Drawing"
    )
    self_attrs: set[str] = set()
    for fn in cls.body:
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.args.args:
            visitor = _ReceiverStores(fn.args.args[0].arg)  # the method's receiver (its `self`)
            for stmt in fn.body:
                visitor.visit(stmt)
            self_attrs |= visitor.names
    class_privates = {n for n in vars(Drawing) if n.startswith("_") and not n.startswith("__")}
    actual = {
        n for n in (self_attrs | class_privates) if not n.isupper()
    }  # drop constants (_EXPORT_FORMATS)
    missing = actual - _DRAWING_PRIVATES
    assert not missing, (
        f"Drawing gained private(s) not in _DRAWING_PRIVATES — add them so reads are policed: {missing}"
    )
