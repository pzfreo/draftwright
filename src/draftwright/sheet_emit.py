"""sheet_emit — the declarative ``Sheet``-DSL emitter (ADR 0011 Amendment 1, #461).

Mode 3 of the three authoring modes: *generate an editable beautiful-Python script*. Walk a
**detected** :class:`PartModel` and print a :class:`~draftwright.Sheet` script — one commentable
line per feature — that the user edits / comments-out / extends, then re-runs.

**Detected input only writes numbers (the part-seam form, ADR 0011 Amdt 1 decision).** For a STEP
file or a recovered solid the number *is* the ground truth, so a detected value is honest. We never
fabricate a build123d part to chase a number-free layer — a synthesised solid silently drops what
detection didn't model (chamfers, fillets, turned profiles) yet reads as authoritative. A caller who
*has* the objects (mode 3b) wires their real part into the seam and swaps the number lines for
``sheet.hole(obj)`` references — the emitter's numbers are a starting point, not a ceiling.

Kinds with no declarative verb yet (``step_level``/``rotational``/``pmi``) are flagged inline —
never silently dropped — and left to the auto-pass that runs over the declared model on re-run.
Fidelity: the script reproduces a lint-clean drawing of the same features, not a byte copy of the
detected sheet; some aspects a declarative verb would pin are not yet reproduced faithfully — a
turned shaft drops its centre lines and renders the base ⌀ as a leader (the #472 validity gap).
"""

from __future__ import annotations

from pathlib import Path

from build123d import Shape

from draftwright.builder import detect_part_model


def _n(v) -> float | int:
    """A tidy number: int when integral, else rounded to 3 dp."""
    v = round(float(v), 3)
    return int(v) if v == int(v) else v


def _pt(p) -> str:
    return "(" + ", ".join(str(_n(c)) for c in p) + ")"


def _hole_line(f) -> str:
    kw = [f"diameter={_n(f.diameter)}", f"at={_pt(f.frame.origin)}", f'axis="{f.frame.axis}"']
    if f.count and f.count > 1:
        kw.append(f"count={f.count}")
        # A count-group carries its member positions; without them the render collapses to a
        # single hole at the anchor (fidelity loss). Patterns recompute members from the
        # arrangement, so only a plain count-group needs them spelled out.
        if f.members:
            kw.append("members=[" + ", ".join(_pt(m) for m in f.members) + "]")
    if f.cbore:
        kw.append(f"cbore=({_n(f.cbore[0])}, {_n(f.cbore[1])})")
    if f.spotface:
        kw.append(f"spotface=({_n(f.spotface[0])}, {_n(f.spotface[1])})")
    line = f"sheet.hole({', '.join(kw)})"
    if not f.through and f.depth is not None:
        line += f".depth({_n(f.depth)})"
    return line


def _member_hole_str(m) -> str:
    """The ``hole(...)`` template for a pattern member — carries its ⌀ AND its
    counterbore / spotface / blind-depth so a counterbored bolt circle keeps those
    callouts on re-run (declare.hole takes depth=/through=/cbore=/spotface= kwargs)."""
    kw = [f"diameter={_n(m.diameter)}", f"at={_pt(m.frame.origin)}", f'axis="{m.frame.axis}"']
    if m.cbore:
        kw.append(f"cbore=({_n(m.cbore[0])}, {_n(m.cbore[1])})")
    if m.spotface:
        kw.append(f"spotface=({_n(m.spotface[0])}, {_n(m.spotface[1])})")
    if not m.through and m.depth is not None:
        kw.append(f"depth={_n(m.depth)}")
        kw.append("through=False")
    return f"hole({', '.join(kw)})"


def _feature_line(f) -> str:
    k = f.kind
    if k == "envelope":
        return f"sheet.envelope()   # {_n(f.width)} × {_n(f.height)} × {_n(f.depth)}"
    if k == "hole":
        return _hole_line(f)
    if k == "boss":
        return f'sheet.diameter(diameter={_n(f.diameter)}, at={_pt(f.frame.origin)}, axis="{f.frame.axis}")'
    if k == "step":
        return (
            f"sheet.step(diameter={_n(f.diameter)}, length={_n(f.length)}, "
            f'at={_pt(f.frame.origin)}, axis="{f.frame.axis}")'
        )
    if k == "slot":
        lo, hi = _n(f.lo), _n(f.hi)
        # Derive length from the EMITTED lo/hi so hi - lo == length exactly — declare.slot()
        # rejects the recogniser's independently-rounded (lo, hi, length) with a 1e-6 tolerance.
        length = _n(round(float(hi) - float(lo), 3))
        return (
            f"sheet.slot(width={_n(f.width)}, length={length}, "
            f'long_axis="{f.long_axis}", width_axis="{f.width_axis}", '
            f"lo={lo}, hi={hi}, w_center={_n(f.w_center)})"
        )
    if k == "pattern":
        # Defining dims for the furniture (BCD centreline / pitch / grid dims) PLUS the exact
        # member positions. The arrangement alone can't be recomputed faithfully — the
        # detector records no bolt-circle START ANGLE (nor a linear direction reliably) — so
        # spelling out members= is the only fidelity-safe form (declare uses them as-is).
        parts = [f'kind="{f.pattern}"', f"count={f.count}"]
        if f.pattern == "bolt_circle" and f.bcd:
            parts.append(f"bcd={_n(f.bcd)}")
        elif f.pattern == "linear" and f.pitch:
            parts.append(f"pitch={_n(f.pitch)}")
        elif f.pattern == "grid" and f.grid:
            parts.append(f"grid=({_n(f.grid[0])}, {_n(f.grid[1])}), rows={f.rows}, cols={f.cols}")
        if f.members:
            parts.append("members=[" + ", ".join(_pt(p) for p in f.members) + "]")
        return f"sheet.pattern({_member_hole_str(f.member)}, " + ", ".join(parts) + ")"
    # kinds with no declarative verb yet — the auto-pass still dimensions them.
    return f"# {k} @ {_pt(f.frame.origin)} — auto-dimensioned (no declarative verb yet)"


def _needs_section(model) -> bool:
    """Mirror planner.plan_sections' trigger so the emitted comment matches what the drawing
    actually does: any Z-axis hole/pattern whose bore has a counterbore, spotface, or blind
    bottom. A pattern carries the bore on its ``member`` hole, so a counterbored bolt circle
    counts too (checking only top-level holes missed it)."""
    for f in model.features:
        if f.kind not in ("hole", "pattern") or f.frame.axis != "z":
            continue
        bore = f.member if f.kind == "pattern" else f
        if bore.cbore or bore.spotface or not bore.through:
            return True
    return False


_HEADER = '''"""Editable drawing — generated by draftwright (declarative Sheet DSL).

Each line below declares one feature. Comment a line out to drop that feature; edit a
value freely; chain .tolerance(lo, hi) / .fit("H7") onto any diameter. Then re-run this file.

The values are DETECTED off the geometry (honest for a STEP / recovered solid). If you
built the part yourself, wire your object into the `part = …` seam and swap a numbered
line for a reference — e.g.  sheet.hole(my_bore)  — to read the size off the object.
"""'''


def emit_sheet_script(model, part_expr: str, stem: str, *, title: str, number: str) -> str:
    """The generated declarative ``Sheet`` script text for a detected *model*.

    *part_expr* is the Python that binds ``part`` (a STEP ``import_step`` or a ``part = …``
    seam); *stem* is the output basename the script exports to."""
    needs_hole = any(f.kind in ("hole", "pattern") for f in model.features)
    lines = [
        _HEADER,
        "from draftwright import Sheet",
        *(["from draftwright.model import hole"] if needs_hole else []),
        "",
        part_expr,
        "",
        f"sheet = Sheet(part, title={title!r}, number={number!r})",
        "",
        "# ── Features (each line is one declared feature) ──────────────────────────────",
        *(_feature_line(f) for f in model.features),
        "",
        "# ── Views ─────────────────────────────────────────────────────────────────────",
        "# front / plan / side / iso are always produced.",
    ]
    if _needs_section(model):
        lines.append("# Section A–A auto-triggers from the counterbore/blind bore above.")
    lines += ["", f"sheet.export({stem!r})"]
    return "\n".join(lines) + "\n"


def resolve_object_spec(spec: str) -> tuple[Shape, str]:
    """Resolve a ``module:attr`` (or ``path/to/file.py:attr``) spec into ``(build123d object,
    import seam)`` (ADR 0011, #469). The attribute is imported; a zero-argument callable (a
    ``make_part()`` factory) is called. The returned seam is the Python that re-binds ``part``
    in the generated script, so the drawing references your **live parametric source**, not a
    frozen STEP.

    SECURITY: importing the target executes its module-level code — the same trust as running
    the file yourself."""
    import importlib
    import importlib.util
    import inspect
    import os
    import sys

    mod_ref, sep, name = spec.rpartition(":")
    if not sep or not name.isidentifier() or not mod_ref:
        raise ValueError(f"object spec must be 'module:attr' or 'file.py:attr' (got {spec!r})")

    if mod_ref.endswith(".py"):
        path = Path(mod_ref).resolve()
        ispec = importlib.util.spec_from_file_location(path.stem, path)
        if ispec is None or ispec.loader is None:
            raise ValueError(f"cannot load module from {mod_ref!r}")
        module = importlib.util.module_from_spec(ispec)
        # Register before exec so a self-referential target resolves (a dataclass whose
        # forward-ref annotations get typing.get_type_hints'd, a module reading
        # sys.modules[__name__], import-time pickling). The seam does the same on re-run.
        sys.modules[ispec.name] = module
        try:
            ispec.loader.exec_module(module)
        except Exception as e:
            raise ValueError(f"{spec!r}: importing {mod_ref!r} failed: {e}") from e
        seam = (
            "import importlib.util as _ilu, sys as _sys\n"
            f"_spec = _ilu.spec_from_file_location({path.stem!r}, {str(path)!r})\n"
            "_mod = _ilu.module_from_spec(_spec)\n_sys.modules[_spec.name] = _mod\n"
            "_spec.loader.exec_module(_mod)"
        )
        ref = f"_mod.{name}"
    else:
        cwd = os.getcwd()
        if cwd not in sys.path:
            sys.path.insert(0, cwd)  # allow a cwd-relative import
        try:
            module = importlib.import_module(mod_ref)
        except ImportError as e:
            raise ValueError(f"{spec!r}: cannot import module {mod_ref!r}: {e}") from e
        # Record the invocation cwd (where the module resolved) on the generated script's path,
        # so `from mod import …` works from any working directory — Python puts only the
        # *script's* dir on sys.path, not the cwd, so a bare import would otherwise fail.
        # (For an installed module the insert is harmless; the import works regardless.)
        seam = (
            f"import sys as _sys\nif {cwd!r} not in _sys.path:\n    _sys.path.insert(0, {cwd!r})\n"
            f"from {mod_ref} import {name} as _obj"
        )
        ref = "_obj"

    if not hasattr(module, name):  # `hasattr`, not a None sentinel: a name bound to None exists
        raise ValueError(f"{spec!r}: {name!r} not found in {mod_ref!r}")
    obj = getattr(module, name)

    called = False
    if callable(obj) and not isinstance(obj, Shape):
        required = [
            p
            for p in inspect.signature(obj).parameters.values()
            if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        if required:
            raise ValueError(
                f"{spec!r}: {name} needs arguments — reference a built object instead"
            )
        obj, called = obj(), True

    if not isinstance(obj, Shape):
        raise ValueError(f"{spec!r}: resolved to {type(obj).__name__}, not a build123d Shape")

    return obj, f"{seam}\npart = {ref}{'()' if called else ''}"


def generate_sheet_script(
    step_file: str | Shape,
    out: str | None = None,
    *,
    title: str | None = None,
    number: str = "DWG-001",
    pmi: str = "off",
    part_expr: str | None = None,
) -> str:
    """Write a declarative ``Sheet``-DSL script for *step_file* (a STEP path or a build123d
    object). Returns the path to the generated ``.py``. The mode-3 declarative counterpart of
    :func:`draftwright.builder.generate_script` (which emits the imperative reconstruction).

    ``pmi`` is threaded to detection so AP242 PMI features surface (flagged inline). *part_expr*,
    when given, overrides the ``part = …`` seam — e.g. the import seam from
    :func:`resolve_object_spec` so the script references a live module (#469)."""
    is_shape = isinstance(step_file, Shape)
    stem = out or ("drawing" if is_shape else Path(step_file).stem)
    for _ext in (".py", ".svg", ".dxf"):
        if stem.endswith(_ext):
            stem = stem[: -len(_ext)]
            break
    title = title or (Path(stem).name.replace("_", " ").upper() if not is_shape else "DRAWING")

    if part_expr is not None:
        pass  # caller-supplied seam (e.g. an import of a live module, #469)
    elif is_shape:
        part_expr = "part = ...   # ← wire in your build123d object (built above)"
    else:
        # absolute so the generated script runs from any working directory
        abspath = str(Path(step_file).resolve())
        part_expr = f"from build123d import import_step\npart = import_step({abspath!r})"

    model = detect_part_model(step_file, pmi=pmi)
    script = emit_sheet_script(model, part_expr, stem, title=title, number=number)
    py_path = f"{stem}.py"
    Path(py_path).write_text(script, encoding="utf-8")  # the script has box-drawing / × / ← glyphs
    return py_path
