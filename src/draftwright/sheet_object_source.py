"""Live object-source resolution and correspondence for Sheet script generation.

The script emitter owns its public front door; this module owns the source seam and
mutual one-to-one geometry evidence used by the generated declarations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path

from build123d import Shape


@dataclass(frozen=True)
class _ObjectSource:
    """A resolved live-source seam and the named geometry it can reference."""

    part: Shape
    seam: str
    candidates: Mapping[str, Shape]


_REFERENCE_EXTERNAL = {"hole": False, "boss": True, "step": True}
_REFERENCE_DIA_TOL = 0.2
_REFERENCE_POS_TOL = 0.5


def _candidate_external(part: Shape, candidate: Shape) -> bool | None:
    """Whether *candidate* is material in *part*, ``False`` for a removed tool.

    A source object is independently useful only when the Boolean relationship is decisive:
    wholly present means an external boss/step; wholly absent means an internal cutter.  A
    partial overlap could be a construction solid, an unfinished Boolean, or a composite tool,
    so it deliberately has no polarity and cannot acquire a reference.
    """
    volume = abs(float(candidate.volume))
    if volume <= 1e-9:
        return None
    try:
        intersection = part & candidate
        if not isinstance(intersection, Shape):
            return None
        overlap = abs(float(intersection.volume))
    except Exception:  # noqa: BLE001 — an unavailable Boolean means unavailable evidence
        return None
    fraction = overlap / volume
    if fraction <= 1e-7:
        return False
    if fraction >= 1 - 1e-7:
        return True
    return None


def _reference_geometry_matches(feature, candidate: Shape) -> bool:
    """Whether a candidate can rebuild *feature* without restating its defining geometry."""
    from draftwright.model.declare import _norm_axis, _read_cylinder

    if getattr(feature, "kind", None) not in _REFERENCE_EXTERNAL:
        return False
    if feature.kind == "hole" and getattr(feature, "profile", None) not in (None, "circular"):
        return False
    if feature.kind == "hole" and getattr(feature, "count", 1) != 1:
        return False
    try:
        axis, diameter, centre = _read_cylinder(candidate)
        axis = _norm_axis(axis)
    except Exception:  # noqa: BLE001 — an unreadable candidate is simply not evidence
        return False
    if axis != _norm_axis(feature.frame.axis):
        return False
    if abs(float(feature.diameter) - float(diameter)) > _REFERENCE_DIA_TOL:
        return False
    axis_index = "xyz".index(axis)
    perpendicular = [index for index in range(3) if index != axis_index]
    if any(
        abs(float(feature.frame.origin[index]) - float(centre[index])) > _REFERENCE_POS_TOL
        for index in perpendicular
    ):
        return False

    # The broad correspondence deliberately ignores axial position, as Sheet._match_object
    # does. Emission has a stronger obligation: the object form must reconstruct the same IR.
    # If its centre/length differ, keep the complete numeric declaration rather than smuggling
    # overrides beside a reference that appears authoritative.
    if any(
        abs(float(feature.frame.origin[index]) - float(centre[index])) > 5e-4 for index in range(3)
    ):
        return False
    if abs(float(feature.diameter) - float(diameter)) > 5e-4:
        return False
    bb = candidate.bounding_box()
    axial_length = (bb.size.X, bb.size.Y, bb.size.Z)[axis_index]
    measured_length = getattr(feature, "length", None)
    if measured_length is None and feature.kind == "boss":
        measured_length = getattr(feature, "height", None)
    return measured_length is None or abs(float(measured_length) - axial_length) <= 5e-4


def _object_references(
    features, part: Shape | None, candidates: Mapping[str, Shape] | None
) -> dict[int, str]:
    """Mutual one-to-one feature→source expressions, with ambiguity failing closed."""
    if part is None or not candidates:
        return {}

    feature_edges: dict[int, list[str]] = {}
    candidate_edges: dict[str, list[int]] = {name: [] for name in candidates}
    polarities = {
        name: _candidate_external(part, candidate) for name, candidate in candidates.items()
    }
    for feature in features:
        kind = getattr(feature, "kind", None)
        if not isinstance(kind, str):
            continue
        expected_external = _REFERENCE_EXTERNAL.get(kind)
        if expected_external is None:
            continue
        for name, candidate in candidates.items():
            if polarities[name] != expected_external:
                continue
            if not _reference_geometry_matches(feature, candidate):
                continue
            feature_edges.setdefault(id(feature), []).append(name)
            candidate_edges[name].append(id(feature))

    return {
        feature_id: names[0]
        for feature_id, names in feature_edges.items()
        if len(names) == 1 and len(candidate_edges[names[0]]) == 1
    }


def _resolve_object_source(spec: str) -> _ObjectSource:
    """Resolve a live Shape or a dataclass carrying ``body`` plus named Shape fields.

    The features-container form is the source-identity seam from #1041: it binds the factory
    result once as ``features``, detects from ``features.body``, and makes each other public
    Shape field available to the correspondence guard. A plain Shape keeps the exact #469
    behaviour and supplies no candidates.

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
        # Force the helper file's OWN directory to the FRONT of sys.path, with the invocation cwd
        # just behind it, so its repo-relative / sibling imports resolve like `python file.py` (the
        # script dir wins a name clash) — spec_from_file_location adds NEITHER (#488). Remove any
        # existing occurrence first, then re-insert in a fixed order: a plain `not in sys.path`
        # guard can't reorder a dir that's ALREADY on the path (e.g. a driver run as
        # `python tools/driver.py` puts the helper dir on sys.path), so cwd could otherwise land
        # ahead of it and win the clash, AND the in-process build would diverge from the standalone
        # re-run seam (#491 review). Removing then front-inserting makes the order deterministic and
        # identical between build and re-run; the seam bakes both as resolve-time absolute literals.
        file_dir = str(path.parent)
        cwd = os.getcwd()
        for _p in (cwd, file_dir):
            while _p in sys.path:
                sys.path.remove(_p)
        for _p in (cwd, file_dir):  # cwd first, file_dir last -> file_dir at index 0 (wins)
            sys.path.insert(0, _p)
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
            f"for _p in ({cwd!r}, {file_dir!r}):\n"
            "    while _p in _sys.path:\n        _sys.path.remove(_p)\n"
            f"for _p in ({cwd!r}, {file_dir!r}):\n"
            "    _sys.path.insert(0, _p)\n"
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

    target = f"{ref}{'()' if called else ''}"
    if isinstance(obj, Shape):
        return _ObjectSource(obj, f"{seam}\npart = {target}", {})

    body = getattr(obj, "body", None)
    if is_dataclass(obj) and isinstance(body, Shape):
        candidates = {
            f"features.{field.name}": value
            for field in fields(obj)
            if field.name != "body"
            and not field.name.startswith("_")
            and isinstance((value := getattr(obj, field.name)), Shape)
        }
        return _ObjectSource(
            body,
            f"{seam}\nfeatures = {target}\npart = features.body",
            candidates,
        )

    raise ValueError(
        f"{spec!r}: resolved to {type(obj).__name__}, not a build123d Shape or a dataclass "
        "with a Shape-valued body"
    )
