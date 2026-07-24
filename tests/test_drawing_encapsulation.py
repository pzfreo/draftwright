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
# the annotations layer still relies on. #639 drove this to ZERO: the annotation render layer no
# longer reads any Drawing private — the model/model-declared flag/hole-feature index ride the
# per-run PlacementContext, the balloon render + view-coordinate mutations go through public
# Drawing methods (``add_balloons``/``set_view_coordinates``/``drop_view_coordinates``), and the
# name→annotation index is read through ``ctx.registry`` (``__contains__``/``names()``). The
# allowlist may only SHRINK; it is now empty and must stay so.
_DWG_PRIVATE_READ_ALLOW: frozenset[str] = frozenset()


def _anno_sources() -> list[Path]:
    return [p for p in sorted(_ANNO_DIR.rglob("*.py")) if "__pycache__" not in p.parts]


def _is_dwg(node: ast.AST) -> bool:
    """Whether *node* is a bare conventional drawing-receiver name (``dwg``, or
    ``drawing`` since the #699 slice-d whole-engine guard — a rename must not be
    the evasion; see _DRAWING_RECEIVER_NAMES)."""
    return isinstance(node, ast.Name) and node.id in _DRAWING_RECEIVER_NAMES


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
            # Subscript mutation THROUGH a private: ``dwg._coords["iso"] = …`` /
            # ``del dwg._x[k]``. The attribute itself is only ever in Load context
            # here (the dict is loaded, then stored into), so the plain-attribute
            # branch above misses it — the exact form projection.py used to poke
            # the iso ViewCoordinates past ``set_view_coordinates`` (#699 slice d).
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and isinstance(node.value, ast.Attribute)
            and _is_dwg(node.value.value)
            and node.value.attr.startswith("_")
        ):
            out.append((node.lineno, node.value.attr))
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


# The sanctioned in-build LAYOUT SEAM: the ONLY Drawing privates an engine module may invoke as
# methods on the duck-typed drawing. The section/detail layout is inherently interactive — it reads
# its own live output (placed views + occupancy), provisionally places a view, and can roll it back
# — so these three cannot become "return data for the builder to assemble" without a parallel
# canvas. #830 Path A accepts them as the PERMANENT seam, and in doing so narrows decision D's
# method-call exemption (#817 PR4) from a blanket pass to this NAMED allowlist: every OTHER private
# method call from an engine module is now flagged as a state-bus poke, so the engine cannot
# re-grow a private-method back-channel beyond the seam. May only SHRINK (e.g. #830 Path B removing
# the detail rollback would drop _drop_view_coordinates).
_LAYOUT_SEAM: frozenset[str] = frozenset(
    {"_add_view", "_set_view_coordinates", "_drop_view_coordinates"}
)


def _method_call_attrs(tree: ast.Module) -> set[int]:
    """``id()``s of Attribute nodes that are the DIRECT callee of a Call — i.e. the ``dwg._m`` in a
    ``dwg._m(...)`` METHOD INVOCATION. The caller (:func:`_dwg_private_reads`) exempts only the ones
    whose name is in :data:`_LAYOUT_SEAM`. Data reads are never callees here: in
    ``dwg._registry.add(…)`` the private ``_registry`` is the call func's *value* (not the func
    itself), and ``dwg._coords[k]`` is a subscript."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            out.add(id(node.func))
    return out


def _dwg_private_reads(tree: ast.Module) -> set[str]:
    """Distinct ``dwg._<name>`` private READS: attribute accesses in ``Load`` context (so a
    write/delete target — ``Store``/``Del`` — is never miscounted as a read), plus
    ``getattr(dwg, "_name", …)`` probes. Exempt: a call to one of the sanctioned
    :data:`_LAYOUT_SEAM` methods (``dwg._add_view(...)`` etc., #830 Path A). A call to any OTHER
    private method is NOT exempt — it is flagged like a data read, so the exemption is a named seam,
    not a blanket pass (#817 PR4 / decision D, narrowed by #830)."""
    call_funcs = _method_call_attrs(tree)
    reads: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and _is_dwg(node.value)
            and node.attr.startswith("_")
            and isinstance(node.ctx, ast.Load)
            and not (id(node) in call_funcs and node.attr in _LAYOUT_SEAM)
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
        'dwg._coords["iso"] = v',  # subscript mutation through a private (#699 slice d)
        'del dwg._coords["iso"]',
        'dwg._coords["iso"] += v',
        "drawing._part_model = m",  # the rename evasion (#699 slice d, Codex review)
    ):
        writes = _dwg_private_writes(ast.parse(snippet))
        assert writes, f"guard missed a mutation form: {snippet!r}"
    # A pure read must NOT register as a write (else every read is a false positive).
    assert not _dwg_private_writes(ast.parse("use(dwg._named)"))
    # #830 Path A: a call to one of the sanctioned LAYOUT-SEAM methods is exempt from the read
    # scan (the engine's only legitimate private-method calls) — and ONLY those three.
    assert _dwg_private_reads(ast.parse("dwg._add_view(...)")) == set()
    assert _dwg_private_reads(ast.parse("dwg._set_view_coordinates(v)")) == set()
    assert _dwg_private_reads(ast.parse("dwg._drop_view_coordinates(v)")) == set()
    # A call to any OTHER private method is NOT exempt — flagged like a data read, so decision D's
    # exemption (#817 PR4) is a named seam, not a blanket pass (#830 Path A).
    assert _dwg_private_reads(ast.parse("dwg._add(x)")) == {"_add"}
    assert _dwg_private_reads(ast.parse("drawing._clear_annotations()")) == {"_clear_annotations"}
    # Every DATA read stays caught (the state-bus back-channel #722 targets).
    assert _dwg_private_reads(ast.parse("use(dwg._coords)")) == {"_coords"}
    assert _dwg_private_reads(ast.parse("dwg._registry.add(x)")) == {"_registry"}  # func.value
    assert _dwg_private_reads(ast.parse("dwg._coords[k]")) == {"_coords"}  # subscript
    assert _dwg_private_reads(ast.parse('getattr(dwg, "_analysis")')) == {"_analysis"}
    # The aliasing evasion is caught as the alias itself (#699 slice d, Codex review):
    assert _drawing_aliases(ast.parse("d = dwg"))
    assert _drawing_aliases(ast.parse("if (d := drawing): pass"))
    assert _drawing_aliases(ast.parse("d: object = dwg"))  # annotated form (review r2)
    assert not _drawing_aliases(ast.parse("d = other"))  # unrelated binds don't flag


def test_no_build_context_probing():
    """No ``annotations/`` module probes the drawing for build context via
    ``getattr(dwg, "_analysis"/"_part_model"/"_model_declared", …)`` — the model, analysis, and
    model-declared flag are all threaded in as parameters / on the PlacementContext now (#639)."""
    offenders: list[str] = []
    for path in _anno_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _dwg_getattr_probes(tree):
            if name in ("_analysis", "_part_model", "_model_declared"):
                offenders.append(f"{path.relative_to(_SRC)}: getattr(dwg, {name!r}, …)")
    assert not offenders, (
        "annotations/ must not probe the drawing for the model/analysis/model-declared flag — "
        "pass them in as parameters or on the PlacementContext (#639 / ADR 0005 §2):\n  "
        + "\n  ".join(offenders)
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


# The whole-engine guard's sanctioned exceptions (#699 slice d). Keys are file names
# under src/draftwright/, keyed by _SRC-RELATIVE posix path (not basename — a future
# nested builder.py must not inherit the root builder's exemption, Codex review);
# drawing.py itself — the owner — is exempt from the scan. Values are the private
# names that file may READ or WRITE on the duck-typed ``dwg``, each with a
# rationale. May only shrink, like _DWG_PRIVATE_READ_ALLOW.
_ENGINE_DWG_PRIVATE_ALLOW: dict[str, frozenset[str]] = {
    # builder._assemble is THE sanctioned build-state fill site (#639): it fills
    # dwg._build.analysis/part_model (a _build READ + field store, pinned exactly by
    # test_build_state_has_a_single_construction_and_fill_site below) and sets the
    # ADR 0011 #448 model-declared flag pending its move into BuildState.
    "builder.py": frozenset({"_build", "_model_declared"}),
}

# The drawing-receiver naming convention the engine guard matches. The scan is
# receiver-NAME based (static analysis cannot type a duck-typed parameter), so its
# guarantee is exactly: no engine module touches ``<receiver>._<name>`` under these
# conventional names, and no module REBINDS one of them (the aliasing check below
# fail-closes the ``d = dwg; d._x`` evasion by flagging the alias itself). A
# drawing passed in under a novel parameter name remains out of static reach —
# review's job, as the annotations guard has always noted (Codex review, #699 d).
_DRAWING_RECEIVER_NAMES = ("dwg", "drawing")


def _drawing_aliases(tree: ast.Module) -> list[tuple[int, str]]:
    """Every simple rebinding of a conventional drawing receiver —
    ``x = dwg`` / ``x = drawing`` (plain or walrus) — as ``(lineno, alias)``.
    Flagged wholesale in the engine guard: aliasing is the cheap evasion of a
    receiver-name scan, so the alias itself is the offence."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        value = getattr(node, "value", None)
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
            and isinstance(value, ast.Name)
            and value.id in _DRAWING_RECEIVER_NAMES
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    out.append((node.lineno, t.id))
    return out


def test_no_engine_module_touches_drawing_privates():
    """#699 slice d: the state-bus guard, widened from ``annotations/`` to the whole
    engine. No module under src/draftwright/ except ``drawing.py`` (the owner) may
    read or mutate ``dwg._<name>``/``drawing._<name>`` privates on the duck-typed
    drawing — the audit found the coupling class regrowing exactly where the #639
    ratchet didn't police (_core's link-rect expando, projection's
    ``_coords["iso"]`` poke, repair's ``_registry`` reads). Fail-closed with a
    rationale-carrying allowlist; rebinding a receiver name (``d = dwg``) is
    itself an offence, so the scan can't be evaded by a one-line alias. The
    residual static limit — a drawing arriving under a novel parameter name — is
    documented at _DRAWING_RECEIVER_NAMES."""
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "drawing.py":
            continue
        rel = path.relative_to(_SRC).as_posix()
        allow = _ENGINE_DWG_PRIVATE_ALLOW.get(rel, frozenset())
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, attr in _dwg_private_writes(tree):
            if attr not in allow:
                offenders.append(f"{rel}:{lineno}: dwg.{attr} mutated")
        for attr in sorted(_dwg_private_reads(tree) - allow):
            offenders.append(f"{rel}: dwg.{attr} read")
        for lineno, alias in _drawing_aliases(tree):
            offenders.append(f"{rel}:{lineno}: drawing receiver rebound to {alias!r}")
    assert not offenders, (
        "Engine modules must not touch Drawing privates — the drawing is not the state "
        "bus (#699 slice d / ADR 0005 §2). Use the public surface (registry, "
        "set_view_coordinates, annotation riders) or thread the value as a parameter; "
        "a truly-needed exception goes in _ENGINE_DWG_PRIVATE_ALLOW with a rationale:\n  "
        + "\n  ".join(offenders)
    )


def test_build_state_has_a_single_construction_and_fill_site():
    """#639: the writer inventory for the build context, fail-closed and AST-based
    (#691 review — a regex missed augmented assignment / setattr / tuple targets).

    Detected uniformly by Store/Del context + constant-name setattr on ANY receiver:
    every write whose target attribute is ``_build``, ``_build.<field>``, or one of
    the four legacy names. The sanctioned inventory: Drawing.__init__ constructs;
    builder._assemble fills analysis+part_model once; the ``_analysis`` compat
    setter and ``attach_part_model`` route through BuildState (drawing.py). The
    three cache/model legacy attrs are GETTER-ONLY by design — a wholesale
    replacement must go through BuildState, so an accidental one fails loudly
    rather than silently forking the single-writer story. (Aliasing —
    ``state = dwg._build; state.x = …`` — is out of scope here as in
    ``_dwg_private_writes`` above: no code does it, review would catch it.)
    """
    from pathlib import Path

    watched = {"_build", "_analysis", "_part_model", "_view_edge_cache", "_ann_box_cache"}
    src = Path(__file__).parent.parent / "src" / "draftwright"
    writers: dict[str, list[str]] = {}

    def _attr_root(node):
        # dwg._build.part_model → ("_build", "part_model"); dwg._analysis → ("_analysis", None)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
            if node.value.attr in watched:
                return f"{node.value.attr}.{node.attr}"
        if isinstance(node, ast.Attribute) and node.attr in watched:
            return node.attr
        return None

    for py in sorted(src.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            hit = None
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
                hit = _attr_root(node)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("setattr", "delattr")
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and (
                    node.args[1].value in watched
                    # setattr(dwg._build, "analysis", …) — BuildState field names
                    # count too when the receiver is a _build attribute (#691 r2).
                    or (
                        node.args[1].value
                        in ("analysis", "part_model", "view_edge_cache", "ann_box_cache", "trace")
                        and isinstance(node.args[0], ast.Attribute)
                        and node.args[0].attr == "_build"
                    )
                )
            ):
                hit = node.args[1].value
            if hit is not None:
                writers.setdefault(py.name, []).append(hit)

    assert writers == {
        # _build.detail_view: the caller's build_drawing(detail_view=…) opt-in,
        # persisted at the one fill site so the finalize drain gates the
        # prismatic detail request exactly as the auto pass does (#661).
        # _build.trace: the #736 solve-trace recorder now rides BuildState filled
        # directly at the one construction site (#830 — the engine constructs the
        # build state, it does not mutate a live Drawing through attach_solve_trace).
        "builder.py": [
            "_build.analysis",
            "_build.part_model",
            "_build.detail_view",
            "_build.trace",
        ],
        # drawing.py still writes _build.trace via the deprecated attach_solve_trace
        # shim/primitive (kept until 0.5.0) — no engine caller reaches it now. The
        # recorder also participates in finalize()'s #647 transaction: finalize
        # snapshots it beside the registry/coverage snapshots and restores it on
        # rollback, so a failed drain leaves no trace records for placements that no
        # longer exist.
        "drawing.py": ["_build", "_build.analysis", "_build.part_model", "_build.trace"],
    }, writers
