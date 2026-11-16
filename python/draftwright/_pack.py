"""Assembly drawing packs through the Rust pack pipeline; requirements stay native."""
import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from . import _native
from ._part import Part, Drawing
from ._build123d import check_version


class Pack:
    """A placed build123d assembly, STEP path, or sequence of Parts.

    Product labels match Part.name. Parts supply caller-owned in-memory PMI;
    other products draw from geometry. Supplied Parts are stated as made.
    horizontal_axis is x, y or z (the source axis across the GA); None chooses
    the longest axis, as ga.horizontal_axis in the pack Spec does.
    After render, findings includes pack findings and every part sheet problem
    with its part name and original severity. Rows retain their problems too.
    Findings never prevent output.
    """

    def __init__(self, assembly, *, parts=(), title=None, number=None, horizontal_axis=None):
        if horizontal_axis is not None and horizontal_axis not in ("x", "y", "z"):
            raise ValueError("horizontal_axis must be x, y or z, or None")
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
            check_version()
        if any(not isinstance(p, Part) for p in parts):
            raise TypeError("parts must contain only Part objects")
        if len({p.name for p in parts}) != len(parts):
            raise ValueError("Part names must be unique within a Pack")
        for field, value in (("title", title), ("number", number)):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field} must be a string or None")
        self._assembly = assembly
        self.horizontal_axis = horizontal_axis
        self._assembly_step = None
        self._assembly_signature = None
        self._parts = {p.name: p for p in parts}
        self._drawings = {p.name: p.drawing() for p in parts}
        self._defaults = {}
        self._ga = Drawing(None)
        self._ga.title(**{k: v for k, v in (("title", title), ("number", number)) if v is not None})
        self.rows, self.findings = [], []
        self.report = {}

    def title(self, **fields):
        """Set shared title-block defaults; TITLE and DWG NO. identify the GA."""
        if any(k in fields for k in ("title", "number", "sheet")):
            raise ValueError("pack title/number/sheet are per sheet; set the GA identity in Pack(..., title=, number=)")
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
        name = name.strip()
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
                names.append(node.label.strip())

        visit(self._assembly)
        if any(not n for n in names):
            raise ValueError("assembly leaf product labels must be nonempty")
        return set(names)

    def _signature(self):
        """Track the export inputs without traversing faces or serializing geometry."""
        leaves = []

        def visit(node):
            if node.children:
                for child in node.children:
                    visit(child)
            else:
                transform = node.global_location.wrapped.Transformation()
                matrix = tuple(transform.Value(i, j) for i in range(1, 4) for j in range(1, 5))
                leaves.append((node.label, matrix, hash(node.wrapped)))

        if isinstance(self._assembly, (list, tuple)):
            for part in self._parts.values():
                visit(part.shape)
            # List mode exports Part names rather than the shapes' labels.
            return tuple(self._parts), tuple(leaves)
        visit(self._assembly)
        return tuple(leaves)

    def _step(self):
        if isinstance(self._assembly, (str, os.PathLike)):
            path = Path(self._assembly)
            try:
                return str(path), path.read_bytes()
            except OSError as error:
                raise _native.DraftwrightError(str(error)) from error
        signature = self._signature()
        if self._assembly_step is not None and self._assembly_signature == signature:
            return self._assembly_step
        check_version()
        from build123d import Compound, export_step
        names = self._product_names()
        missing = set(self._parts) - names
        if missing:
            raise ValueError(f"Parts have no matching assembly product: {', '.join(sorted(missing))}")
        assembly = self._assembly
        copied_shapes = []

        def clone(node):
            if not node.children:
                result = copy.deepcopy(node)
                # Repeated placed instances must still share a definition in
                # XCAF. Share the private copy, never the caller's TShape.
                for original, copied in copied_shapes:
                    if node.wrapped.IsPartner(original):
                        result.wrapped.TShape(copied.TShape())
                        break
                else:
                    copied_shapes.append((node.wrapped, result.wrapped))
                return result
            # Rebuild wrappers from their copied children: copying a whole
            # Compound separately breaks XCAF's exact shape-to-label lookup.
            result = Compound(children=[clone(child) for child in node.children])
            result.label = node.label
            result.location = node.location
            return result

        if isinstance(assembly, (list, tuple)):
            children = []
            for part in self._parts.values():
                # OCCT 7.8's XCAF transfer can repair shared TShapes in place.
                # A shallow copy would invalidate the caller's retained faces.
                shape = clone(part.shape)
                shape.label = part.name
                children.append(shape)
            assembly = Compound(children=children)
        else:
            assembly = clone(assembly)
        original_label = assembly.label
        try:
            # The STEP reader names a sole product after its assembly wrapper.
            # Keep that wrapper consistent with the actual product, including in
            # list mode, without changing the caller's assembly after export.
            if len(names) == 1:
                assembly.label = next(iter(names))
            with TemporaryDirectory(prefix="draftwright-pack-") as temporary:
                path = Path(temporary) / "assembly.step"
                if not export_step(assembly, path):
                    raise _native.DraftwrightError("build123d could not export the assembly to STEP")
                data = path.read_bytes()
        except (RuntimeError, ValueError) as error:
            raise _native.DraftwrightError(str(error)) from error
        finally:
            assembly.label = original_label
        self._assembly_signature = signature
        self._assembly_step = "assembly.step", data
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
        if self.horizontal_axis is not None:
            spec["ga"]["horizontal_axis"] = self.horizontal_axis
        # Authored Parts are made products, even if their names resemble stock.
        for key in self._parts:
            spec["parts"][key]["supply"] = "make"
        products = [(p.name, p._pmi, p._geometry(product_local=True)) for p in self._parts.values()]
        result = json.loads(_native.render_pack(data, name, json.dumps(spec), products,
                                               os.fsdecode(os.fspath(out)), list(formats)))
        self.report = result["report"]
        self.rows, self.findings = self.report["rows"], self.report["findings"]
        return tuple(result["written"])
