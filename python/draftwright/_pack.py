"""Assembly drawing packs through the Rust pack pipeline; requirements stay native."""
import copy
import io
import json
import os
from pathlib import Path

from . import _native
from ._part import Part, Drawing


class Pack:
    """A placed build123d assembly, STEP path, or sequence of Parts.

    Product labels match Part.name. Parts supply caller-owned in-memory PMI;
    other products draw from geometry. Findings never prevent output.
    """

    def __init__(self, assembly, *, parts=(), title=None, number=None):
        if isinstance(parts, (str, bytes)):
            raise TypeError("parts must be a sequence of Part objects")
        parts = tuple(parts)
        if isinstance(assembly, (list, tuple)):
            if parts:
                raise ValueError("give Parts as the assembly list or parts=, not both")
            parts = tuple(assembly)
            if not parts:
                raise ValueError("Pack needs at least one Part")
        elif not isinstance(assembly, (str, os.PathLike)):
            if not hasattr(assembly, "children") or not assembly.children:
                raise TypeError("assembly must be a build123d Compound with children, a STEP path, or a list of Parts")
        if any(not isinstance(p, Part) for p in parts):
            raise TypeError("parts must contain only Part objects")
        if len({p.name for p in parts}) != len(parts):
            raise ValueError("Part names must be unique within a Pack")
        for field, value in (("title", title), ("number", number)):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field} must be a string or None")
        self._assembly = assembly
        self._assembly_step = None
        self._parts = {p.name: p for p in parts}
        self._drawings = {p.name: p.drawing() for p in parts}
        self._defaults = {}
        self._ga = Drawing(None)
        self._ga.title(**{k: v for k, v in (("title", title), ("number", number)) if v is not None})
        self.rows, self.findings = [], []
        self.report = {}

    def title(self, **fields):
        """Set shared title-block defaults; TITLE and DWG NO. identify the GA."""
        if any(k in fields for k in ("title", "number")):
            raise ValueError("pack title/number are per sheet; set the GA identity in Pack(..., title=, number=)")
        # Validate types and vocabulary through the same native title validation
        # as Drawing, while allowing a shared general tolerance/material default.
        if any(not isinstance(v, str) for v in fields.values()):
            raise TypeError("title-block values must be strings")
        self._defaults = json.loads(_native.pack_title(json.dumps(self._defaults), json.dumps(fields)))
        return self

    def part(self, name):
        """Return a layout Drawing for a product (also for geometry-only products)."""
        if not isinstance(name, str):
            raise TypeError("part name must be a string")
        if name not in self._drawings:
            if name not in self._product_names():
                raise ValueError(f"unknown product {name!r}")
            self._drawings[name] = Drawing(None)
        return self._drawings[name]

    def _product_names(self):
        if isinstance(self._assembly, (str, os.PathLike)):
            analysis = json.loads(_native.inspect_step(os.fsdecode(os.fspath(self._assembly))))
            if analysis.get("part_keys"):
                return set(analysis["part_keys"])
            return {analysis.get("product_name", "")}
        if isinstance(self._assembly, (list, tuple)):
            return set(self._parts)
        names = []

        def visit(node):
            if node.children:
                for child in node.children:
                    visit(child)
            else:
                names.append(node.label)

        visit(self._assembly)
        if any(not n for n in names) or len(set(names)) != len(names):
            raise ValueError("assembly leaf product labels must be nonempty and unique")
        return set(names)

    def _step(self):
        if isinstance(self._assembly, (str, os.PathLike)):
            path = Path(self._assembly)
            try:
                return str(path), path.read_bytes()
            except OSError as error:
                raise _native.DraftwrightError(str(error)) from error
        if self._assembly_step is not None:
            return self._assembly_step
        from build123d import Compound, export_step
        names = self._product_names()
        missing = set(self._parts) - names
        if missing:
            raise ValueError(f"Parts have no matching assembly product: {', '.join(sorted(missing))}")
        assembly = self._assembly
        if isinstance(assembly, (list, tuple)):
            children = []
            for part in self._parts.values():
                shape = copy.copy(part.shape)
                shape.label = part.name
                children.append(shape)
            assembly = Compound(children=children)
        stream = io.BytesIO()
        try:
            export_step(assembly, stream)
        except (RuntimeError, ValueError) as error:
            raise _native.DraftwrightError(str(error)) from error
        self._assembly_step = "assembly.step", stream.getvalue()
        return self._assembly_step

    def render(self, out="pack", *, formats=("pdf", "csv", "json")):
        """Write requested pack files; return paths, and retain report dictionaries."""
        if isinstance(formats, str):
            raise TypeError("formats must be a sequence such as ('pdf', 'csv', 'json')")
        formats = tuple(formats)
        if not formats or len(set(formats)) != len(formats) or any(f not in ("pdf", "csv", "json") for f in formats):
            raise ValueError("formats must be distinct members of pdf, csv, json")
        name, data = self._step()
        spec = {"title": self._defaults, "ga": {"drawing": self._ga.spec()},
                "parts": {key: {"drawing": drawing.spec()} for key, drawing in self._drawings.items()}}
        # Authored Parts are made products, even if their names resemble stock.
        for key in self._parts:
            spec["parts"][key]["supply"] = "make"
        products = [(p.name, p._pmi, p._geometry(product_local=True)) for p in self._parts.values()]
        result = json.loads(_native.render_pack(data, name, json.dumps(spec), products,
                                               os.fsdecode(os.fspath(out)), list(formats)))
        self.report = result["report"]
        self.rows, self.findings = self.report["rows"], self.report["findings"]
        return tuple(result["written"])
