"""Encapsulation guards — the ``annotations/`` render layer may not reach into ``Drawing``
privates (#639 / ADR 0005 §2).

ADR 0005 §2 says ``Drawing`` stops being the implicit state bus: the annotation passes take
the drawing duck-typed as ``dwg`` and must not treat its private attributes as a shared
back-channel. Two habits are now fail-closed by AST-walking every module under
``src/draftwright/annotations/``:

- **No private WRITES.** An ``annotations/`` module must never assign ``dwg._<name> = ...`` —
  build state flows in through parameters or a named method (e.g. ``dwg.attach_part_model``),
  never a poke at a private field (:func:`test_no_dwg_private_attribute_writes`).
- **No model/analysis PROBING.** The build model and analysis are threaded as parameters, so
  ``getattr(dwg, "_analysis"/"_part_model", ...)`` probes are gone
  (:func:`test_no_analysis_or_part_model_probing`).

What private *reads* remain is a documented, shrinking allowlist
(:data:`_DWG_PRIVATE_READ_ALLOW`) — the compat surface #639 will drive to zero. The allowlist
may only SHRINK: removing a name (as a read is threaded through the API) is fine; adding one is
a conscious re-coupling that needs a rationale here (:func:`test_private_reads_are_a_documented_shrinking_allowlist`).

Dependency-free (stdlib ``ast`` + ``pathlib``), matching test_import_boundaries.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "draftwright"
_ANNO_DIR = _SRC / "annotations"

# Distinct ``dwg._<name>`` private reads (attribute access + ``getattr(dwg, "_name", …)`` probes)
# the annotations layer still relies on. This is the compat surface #639 will shrink to zero;
# it may only SHRINK. One-line rationale per entry:
_DWG_PRIVATE_READ_ALLOW: frozenset[str] = frozenset(
    {
        "_add_balloons",  # balloon-render helper the hole pass calls back through
        "_coords",  # cached per-view projected coordinates
        "_feature_of_hole_at",  # feature lookup by hole location
        "_named",  # the name→annotation index (free-name probing)
        "_part_model",  # the built PartModel (read; write now via attach_part_model, #639)
        "_model_declared",  # whether the model was declared (vs detected) — getattr probe
    }
)


def _anno_sources() -> list[Path]:
    return [p for p in sorted(_ANNO_DIR.rglob("*.py")) if "__pycache__" not in p.parts]


def _is_dwg(node: ast.AST) -> bool:
    """Whether *node* is the bare ``dwg`` name (the duck-typed drawing)."""
    return isinstance(node, ast.Name) and node.id == "dwg"


def _dwg_private_writes(tree: ast.Module) -> list[tuple[int, str]]:
    """Every ``dwg._<name>`` MUTATION as ``(lineno, name)``. Detected by AST *context* rather
    than by statement kind, so it catches plain/annotated/augmented assignment
    (``dwg._x = …`` / ``dwg._x: T = …`` / ``dwg._x += …``) and ``del dwg._x`` uniformly (all
    carry ``Store``/``Del`` context), plus constant-name ``setattr``/``delattr(dwg, "_x", …)``.
    (Aliasing — ``a = dwg; a._x = …`` — is out of scope; no code does it and review would catch
    it. Noted rather than solved, #639.)"""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and _is_dwg(node.value)
            and node.attr.startswith("_")
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            out.append((node.lineno, node.attr))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("setattr", "delattr")
            and len(node.args) >= 2
            and _is_dwg(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value.startswith("_")
        ):
            out.append((node.lineno, node.args[1].value))
    return out


def _dwg_getattr_probes(tree: ast.Module) -> list[str]:
    """Private names probed via ``getattr(dwg, "_name", …)`` — 1st arg ``dwg``, 2nd a
    constant string starting with ``_``."""
    out: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and _is_dwg(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value.startswith("_")
        ):
            out.append(node.args[1].value)
    return out


def _dwg_private_reads(tree: ast.Module) -> set[str]:
    """Distinct ``dwg._<name>`` private READS: attribute accesses in ``Load`` context (so a
    write/delete target — ``Store``/``Del`` — is never miscounted as a read), plus
    ``getattr(dwg, "_name", …)`` probes."""
    reads: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and _is_dwg(node.value)
            and node.attr.startswith("_")
            and isinstance(node.ctx, ast.Load)
        ):
            reads.add(node.attr)
    reads |= set(_dwg_getattr_probes(tree))
    return reads


def test_no_dwg_private_attribute_writes():
    """No ``annotations/`` module mutates ``dwg._<name>`` (ADR 0005 §2 / #639): build state
    flows in via parameters or a named method (``dwg.attach_part_model``), never a private poke.
    Covers assignment / annotated / augmented / ``del`` / ``setattr`` / ``delattr`` forms."""
    offenders: list[str] = []
    for path in _anno_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, attr in _dwg_private_writes(tree):
            offenders.append(f"{path.relative_to(_SRC)}:{lineno}: dwg.{attr} mutated")
    assert not offenders, (
        "annotations/ must not write Drawing privates — the drawing is not the state bus "
        "(#639 / ADR 0005 §2). Thread the value in as a parameter or route it through a named "
        "Drawing method (e.g. attach_part_model):\n  " + "\n  ".join(offenders)
    )


def test_write_guard_catches_every_mutation_form():
    """Pin the guard itself: each way to mutate a ``dwg`` private is detected, so the
    encapsulation check can't be evaded by reaching for ``setattr``/``+=``/``del`` (#662 review)."""
    for snippet in (
        "dwg._part_model = m",
        "dwg._part_model: int = m",
        "dwg._named += x",
        "dwg._named |= x",
        "del dwg._part_model",
        'setattr(dwg, "_part_model", m)',
        'delattr(dwg, "_part_model")',
        "(dwg._a, y) = pair",  # tuple-unpack target
    ):
        writes = _dwg_private_writes(ast.parse(snippet))
        assert writes, f"guard missed a mutation form: {snippet!r}"
    # A pure read must NOT register as a write (else every read is a false positive).
    assert not _dwg_private_writes(ast.parse("use(dwg._named)"))


def test_no_analysis_or_part_model_probing():
    """No ``annotations/`` module probes ``getattr(dwg, "_analysis"/"_part_model", …)`` — the
    model and analysis are threaded in as parameters now (#639)."""
    offenders: list[str] = []
    for path in _anno_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _dwg_getattr_probes(tree):
            if name in ("_analysis", "_part_model"):
                offenders.append(f"{path.relative_to(_SRC)}: getattr(dwg, {name!r}, …)")
    assert not offenders, (
        "annotations/ must not probe the drawing for the model/analysis — pass them in as "
        "parameters (#639 / ADR 0005 §2):\n  " + "\n  ".join(offenders)
    )


def test_private_reads_are_a_documented_shrinking_allowlist():
    """Every distinct ``dwg._<name>`` private READ across annotations/ is in the documented
    allowlist — the compat surface #639 will shrink to zero. The allowlist may only SHRINK:
    removing a name (as its read is threaded through the API) is fine; adding one is a conscious
    re-coupling that needs a one-line rationale in _DWG_PRIVATE_READ_ALLOW.

    Note (#662 review): this asserts current-state equality, not cross-revision monotonicity —
    adding a read AND its allowlist entry in one change passes in-process. True shrink-only
    enforcement would need CI to diff the allowlist against the base revision; here it rests on
    the stale-entry assertion (below) + the allowlist growth being visible in review."""
    reads: set[str] = set()
    for path in _anno_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        reads |= _dwg_private_reads(tree)
    new = reads - _DWG_PRIVATE_READ_ALLOW
    assert not new, (
        "New Drawing-private read(s) in annotations/ — this allowlist may only SHRINK "
        "(#639 / ADR 0005 §2). Prefer threading the value through a parameter or a named "
        f"Drawing method; if a read is truly needed, add it with a rationale: {sorted(new)}"
    )
    stale = _DWG_PRIVATE_READ_ALLOW - reads
    assert not stale, (
        "Allowlisted private read(s) no longer used — good, #639 is shrinking. Remove them "
        f"from _DWG_PRIVATE_READ_ALLOW to keep it honest: {sorted(stale)}"
    )
