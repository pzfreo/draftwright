"""build123d shapes with caller-owned haecceity PMI, lowered directly by Rust."""
import json
import os
import re
import io
from pathlib import Path

from . import _native

_FIT = re.compile(r"^(JS|js|H|[fghkmnp])(\d{1,2})$")
_TOL = 1e-4


def _number(value):
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def _safe(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_.") or "part"


class _Ref:
    """Fluent handle on one group of faces; every method returns ``self``."""

    def __init__(self, part, faces, label):
        self._part, self.faces, self.label = part, faces, label


class Datum(_Ref):
    def __init__(self, part, faces, label, letter):
        super().__init__(part, faces, label)
        self.letter = letter

    def __repr__(self):
        return f"Datum({self.letter!r})"


class FaceRef(_Ref):
    def flatness(self, tolerance):
        self._part._pmi.flatness(self.faces, _number(tolerance))
        return self

    def finish(self, ra):
        self._part._pmi.finish(self.faces, _finish(ra))
        return self


class HoleRef(_Ref):
    def __init__(self, part, faces, label, bores):
        super().__init__(part, faces, label)
        self.bores = bores  # the smallest-radius cylinders of each hole: what a fit or finish controls
        self._function = None

    def _set_function(self, kind, value):
        if self._function not in (None, kind):
            raise ValueError(f"{self.label}: a hole is either fitted or threaded, not both")
        self._function = kind

    def fit(self, fit):
        if not _FIT.match(str(fit)):
            raise ValueError(f"{self.label}: {fit!r} is not an ISO 286 fit class such as H7")
        if not str(fit)[0].isupper():
            raise ValueError(f"{self.label}: {fit!r} is a shaft fit; a hole takes a capital class such as H7")
        self._set_function("fit", fit)
        diameter = 2 * _radius(self._part._faces[self.bores[0]])
        self._part._pmi.fit(self.bores, _number(diameter), str(fit))
        return self

    def finish(self, ra):
        self._part._pmi.finish(self.bores, _finish(ra))
        return self

    def thread(self, size):
        """State an internal ISO metric thread (e.g. M3x0.5-6H)."""
        self._set_function("thread", size)
        self._part._pmi.thread(self.bores, str(size))
        return self

    def position(self, tolerance, *datums):
        """A diametral position tolerance relative to the named datums."""
        if any(isinstance(d, Datum) and d._part is not self._part for d in datums):
            raise ValueError(f"{self.label}: a datum belongs to another Part")
        letters = [d.letter if isinstance(d, Datum) else str(d) for d in datums]
        for letter in letters:
            if letter not in self._part._datums:
                raise ValueError(f"{self.label}: datum {letter} has not been declared")
        self._part._pmi.position(self.bores, _number(tolerance), letters)
        return self


def _axis(face):
    """(point, unit direction) of a cylindrical face's axis, in world coordinates."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    ax = BRepAdaptor_Surface(face.wrapped).Cylinder().Axis()
    p, d = ax.Location(), ax.Direction()
    return (p.X(), p.Y(), p.Z()), (d.X(), d.Y(), d.Z())


def _radius(face):
    # build123d may return None on a rectangular-trimmed cylindrical surface.
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    return BRepAdaptor_Surface(face.wrapped).Cylinder().Radius()


def _finish(ra):
    return ra if isinstance(ra, str) else f"Ra {_number(ra)}"


class Part:
    """A build123d shape plus the requirements its author states about it.

    ``Part(shape, name=..., number=..., title=...)``. ``name`` prefixes face names
    (``f:<name>.<label>.<n>``) and defaults to the shape's label or ``part``.
    """

    def __init__(self, shape, name=None, *, number=None, title=None):
        self.shape = shape
        self.name = _safe(name or getattr(shape, "label", "") or "part")
        self.number, self.title = number, title
        self._faces = list(shape.faces())
        if not self._faces:
            raise ValueError("Part needs a shape with faces")
        self._names = {}        # face index -> STEP name
        self._groups = {}      # role -> how many auto-labelled so far
        self._datums = {}
        self._pmi = _native.Pmi(len(self._faces))
        self._geometry_cache = None

    # ---- part data -------------------------------------------------------
    def material(self, value):
        self._pmi.material(str(value))
        return self

    def process(self, value):
        self._pmi.process(str(value))
        return self

    def general_tolerance(self, value):
        self._pmi.general_tolerance(str(value))
        return self

    # ---- faces -----------------------------------------------------------
    def datum(self, letter, obj, label=None):
        if letter not in tuple("ABCDEFGH"):
            raise ValueError(f"datum letters are A to H, not {letter!r}")
        if letter in self._datums:
            raise ValueError(f"datum {letter} is already declared")
        faces = self._resolve(obj, label or f"datum_{letter}", f"datum {letter}")
        self._pmi.datum(letter, faces)
        self._datums[letter] = faces
        return Datum(self, faces, self._last_label, letter)

    def face(self, obj, label=None):
        faces = self._resolve(obj, label or self._auto("face"), "face")
        return FaceRef(self, faces, self._last_label)

    def hole(self, obj, label=None):
        """One hole (counterbore and all). More than one is an error: use ``holes``."""
        return self._hole(obj, label, single=True)

    def holes(self, obj, label=None):
        """One or more holes (e.g. the cutters of a ``GridLocations``)."""
        return self._hole(obj, label, single=False)

    def _hole(self, obj, label, single):
        groups = self._find(obj, "hole")
        if single and len(groups) > 1:
            raise ValueError(
                f"hole(): the object holds {len(groups)} separate holes; use holes() for several")
        bores = []
        for g in groups:
            cylinders = [i for i in g if self._faces[i].geom_type.name == "CYLINDER"]
            if not cylinders:
                found = sorted({self._faces[i].geom_type.name for i in g})
                raise ValueError(f"hole(): no cylindrical face among the {len(g)} faces found ({', '.join(found)})")
            walls = [i for i in cylinders if self._is_hole_wall(self._faces[i])]
            if not walls:
                raise ValueError(
                    "hole(): the cylindrical faces found are bosses or shafts (material inside), not holes")
            smallest = min(_radius(self._faces[i]) for i in walls)
            bores += [i for i in walls if abs(_radius(self._faces[i]) - smallest) < _TOL]
        flat = self._assign(groups, label or self._auto("hole"), "hole")
        return HoleRef(self, flat, self._last_label, bores)

    @staticmethod
    def _is_hole_wall(face):
        """The outward normal of a hole's wall points at its axis; a boss's points away."""
        point = face.position_at(0.5, 0.5)
        axis = _axis(face)
        origin, direction = axis
        offset = [point.X - origin[0], point.Y - origin[1], point.Z - origin[2]]
        along = sum(o * d for o, d in zip(offset, direction))
        radial = [o - along * d for o, d in zip(offset, direction)]  # axis -> point
        normal = face.normal_at(point)
        return normal.X * radial[0] + normal.Y * radial[1] + normal.Z * radial[2] < 0

    def _auto(self, role):
        self._groups[role] = self._groups.get(role, 0) + 1
        return f"{role}{self._groups[role]}"

    # ---- resolving build123d objects to faces of this part ---------------
    def _find(self, obj, what):
        """Groups of face indices of this part that ``obj`` denotes; nothing is named yet.

        Faces of the part are taken as given (all of them, or it is an error). Any other
        object (a hole's cutter, a solid) expands to its faces, matched to the part's
        faces by geometry.
        """
        is_list = isinstance(obj, (list, tuple))
        given = list(obj) if is_list else list(obj.faces())
        found = [self._index(f) for f in given]
        if is_list and None in found:
            missing = [type(f).__name__ + f" #{k}" for k, (f, i) in enumerate(zip(given, found)) if i is None]
            raise ValueError(
                f"{what}: {len(missing)} of {len(given)} faces given are not faces of the part "
                f"({', '.join(missing)}); nothing is guessed")
        groups = [[i for i in found if i is not None]] if any(i is not None for i in found) else []
        # A boolean keeps some of a cutter's faces as the part's own, so a partial
        # overlap means "a tool", not "faces of the part": match it geometrically.
        if not is_list and (not groups or len(groups[0]) < len(given)):
            groups = self._geometric(obj) or groups
        groups = [sorted(set(g)) for g in groups if g]
        if not groups:
            raise ValueError(
                f"{what}: none of the faces of {type(obj).__name__} is a face of the part "
                f"(or coincides with one); the part has {len(self._faces)} faces")
        return groups

    def _assign(self, groups, label, what):
        """Name the faces ``f:<part>.<label>.<n>``; all checks first, so a failure names nothing."""
        label = _safe(label)
        flat = sorted({i for g in groups for i in g})
        new = {}
        taken = {v: k for k, v in self._names.items()}
        for n, i in enumerate(flat, 1):
            if i in self._names:
                continue
            name = f"f:{self.name}.{label}.{n}"
            if name in taken or name in new.values():
                raise ValueError(
                    f"{what}: the name {name} is already on face {taken.get(name, i)}; give this "
                    f"reference its own label")
            new[i] = name
        self._names.update(new)
        self._last_label = label
        return flat

    def _resolve(self, obj, label, what):
        return self._assign(self._find(obj, what), label, what)

    def _index(self, face):
        for i, own in enumerate(self._faces):
            if own.is_same(face):
                return i
        return None

    def _geometric(self, obj):
        solids = list(obj.solids()) if hasattr(obj, "solids") else []
        parts = solids or [obj]
        groups = []
        for s in parts:
            box, tools, group = s.bounding_box(), list(s.faces()), []
            for i, own in enumerate(self._faces):
                inner = own.bounding_box()
                if not (all(getattr(inner.min, a) >= getattr(box.min, a) - _TOL for a in "XYZ")
                        and all(getattr(inner.max, a) <= getattr(box.max, a) + _TOL for a in "XYZ")):
                    continue
                if any(self._coincident(own, t) for t in tools):
                    group.append(i)
            if group:
                groups.append(group)
        return groups

    @staticmethod
    def _coincident(own, tool):
        kind = own.geom_type.name
        if kind != tool.geom_type.name:
            return False
        if own.distance_to(tool) > _TOL:
            return False
        if kind == "CYLINDER":
            if abs(_radius(own) - _radius(tool)) > _TOL:
                return False
            (po, pd), (to, td) = _axis(own), _axis(tool)
            cross = [pd[1] * td[2] - pd[2] * td[1], pd[2] * td[0] - pd[0] * td[2], pd[0] * td[1] - pd[1] * td[0]]
            if sum(c * c for c in cross) > 1e-12:
                return False  # not parallel
            gap = [t - p for t, p in zip(to, po)]
            along = sum(g * d for g, d in zip(gap, pd))
            return sum((g - along * d) ** 2 for g, d in zip(gap, pd)) < _TOL * _TOL  # same axis line
        if kind == "PLANE":
            a, b = own.normal_at(), tool.normal_at()
            return abs(abs(a.X * b.X + a.Y * b.Y + a.Z * b.Z) - 1) < 1e-6
        return True

    def _names_of(self, indices):
        return [self._names[i] for i in sorted(indices)]

    def _geometry(self):
        # Transfer geometry once; the PMI remains the native object above and can change
        # without serializing or re-importing the geometry.
        names = dict(self._names)
        for i in range(len(self._faces)):
            names.setdefault(i, f"f:{self.name}.__geometry.{i + 1}")
        signature = tuple(sorted(names.items()))
        if self._geometry_cache is None or self._geometry_cache[0] != signature:
            stream = io.BytesIO()
            written = write_step(self.shape, stream, names, self._faces, self.name,
                                 number=self.number, title=self.title)
            order = [written[names[i]] for i in range(len(self._faces))]
            geometry = _native.Geometry(stream.getvalue(), self.name, order)
            self._geometry_cache = signature, geometry
        return self._geometry_cache[1]

    def _keys(self, obj):
        if isinstance(obj, _Ref):
            if obj._part is not self:
                raise ValueError("reference belongs to another Part")
            faces = obj.bores if isinstance(obj, HoleRef) else obj.faces
        else:
            faces = self._resolve(obj, self._auto("ref"), "reference")
        return json.loads(self._geometry().keys(faces))

    def feature_id(self, face):
        """Drawing Spec feature ID for the named face or fluent reference; ambiguity is an error."""
        result = self._keys(face)
        if result["feature_id"] is None:
            raise ValueError(f"faces {result['faces']} do not identify one feature; found {result['features_found']}")
        return result["feature_id"]

    def reference_key(self, face):
        """Stable ``face:v1:...`` drawing geometry key for exactly one face."""
        result = self._keys(face)
        keys = result["reference_keys"]
        if len(keys) != 1:
            raise ValueError(f"faces {result['faces']} identify {len(keys)} geometry references, expected one")
        return keys[0]

    # ---- STEP ------------------------------------------------------------
    def export_step(self, path):
        """Export plain geometry with face names and product metadata.

        PMI stays in memory; AP242 PMI export is a later stream. No sidecars are written.
        Returns ``{step: Path, faces: {name: zero_based_face_number}}``.
        """
        path = Path(os.fspath(path))
        names = write_step(self.shape, path, self._names, self._faces, self.name,
                           number=self.number, title=self.title)
        return {"step": path, "faces": names}

    # ---- drawing ---------------------------------------------------------
    def drawing(self, sheet=None, scale=None, units="mm"):
        return Drawing(self, sheet=sheet, scale=scale, units=units)


def _step_names(path):
    text = Path(path).read_text(errors="replace")
    return re.findall(r"ADVANCED_FACE\('([^']*)'", text)


def write_step(shape, path, names, faces, product, *, number=None, title=None):
    """Write ``shape`` as STEP with ``names`` (face index -> name) on its faces.

    build123d's ``export_step`` names assemblies and nodes but not sub-shapes, so
    this writes the STEP with OCCT's writer and sets the name of each referenced
    face's ADVANCED_FACE entity directly, found as the writer's own face lookup
    finds it. (The XCAF route, ``write.stepcaf.subshapes.name``, silently loses the
    names of faces that carry a TopLoc location, e.g. the planes of a filleted
    box.) Returns ``{name: face index in the STEP}``, read back from the file, and
    raises unless every name landed on exactly one face.
    """
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Message import Message, Message_Gravity
    from OCP.StepBasic import StepBasic_Product
    from OCP.STEPConstruct import STEPConstruct
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.TCollection import TCollection_HAsciiString
    from OCP.TopLoc import TopLoc_Location

    for printer in Message.DefaultMessenger_s().Printers():  # as build123d: keep OCCT quiet
        printer.SetTraceLevel(Message_Gravity.Message_Fail)
    writer = STEPControl_Writer()
    writer.Transfer(shape.wrapped, STEPControl_AsIs)
    model = writer.Model()
    for i in range(1, model.NbEntities() + 1):
        item = model.Value(i)
        if isinstance(item, StepBasic_Product):
            item.SetId(TCollection_HAsciiString(number or product))
            item.SetName(TCollection_HAsciiString(title or product))
    finder = writer.WS().TransferWriter().FinderProcess()
    for index, name in sorted(names.items()):
        item = STEPConstruct.FindEntity_s(finder, faces[index].wrapped, TopLoc_Location())
        if item is None:
            raise RuntimeError(f"OCCT wrote no STEP face for the face named {name}")
        item.SetName(TCollection_HAsciiString(name))
    memory = hasattr(path, "write")
    status = writer.WriteStream(path) if memory else writer.Write(os.fspath(path))
    if status != IFSelect_RetDone:
        raise RuntimeError("OCCT failed to write the STEP file")
    written = re.findall(r"ADVANCED_FACE\('([^']*)'", path.getvalue().decode()) if memory else _step_names(path)
    numbers = {}
    for name in names.values():
        where = [i for i, n in enumerate(written) if n == name]
        if len(where) != 1:
            raise RuntimeError(f"face name {name} is on {len(where)} faces in the written STEP, not 1")
        numbers[name] = where[0]
    return numbers


class Drawing:
    """Layout only: sheet, scale, views, sections. Requirements never go here."""

    def __init__(self, part, sheet=None, scale=None, units="mm"):
        self._part = part
        self._spec = {"units": units, "sheet": {"title": {}}}
        title = self._spec["sheet"]["title"]
        if part.title is not None:
            title["title"] = part.title
        if part.number is not None:
            title["number"] = part.number
        if sheet is not None:
            self._spec["sheet"]["size"] = sheet
        self.problems = []
        self._scale = None
        if scale is not None:
            if isinstance(scale, str):
                self._spec["sheet"]["scale"] = scale
            else:
                self._scale = float(scale)

    def views(self, shaded=None, omit=()):
        """``shaded=True`` shades the isometric; ``omit=("side",)`` drops named views."""
        views = self._spec.setdefault("views", {})
        if shaded is not None:
            views.setdefault("iso", {})["style"] = "shaded" if shaded else "wireframe"
        for name in omit:
            views.setdefault(name, {})["omit"] = True
        return self

    def section(self, id="A", through=None):
        """A section ``A``..``H`` through a feature id (a string such as
        ``"f:section_recess:f133"``). ``through=None`` on ``"A"`` asks for the plain
        z-axis section through holes. A fluent reference or build123d object is
        resolved to the drawing's recognised feature."""
        letter = str(id).split("-")[0].upper()
        if len(letter) != 1 or letter not in "ABCDEFGH":
            raise ValueError(f"section ids are A to H, not {id!r}")
        views = self._spec.setdefault("views", {})
        if through is None:
            if letter != "A":
                raise ValueError("a section through nothing in particular is only available as A")
            views["section_aa"] = {}
        elif isinstance(through, str):
            views.setdefault("sections", []).append({"id": letter, "through": through})
        else:
            views.setdefault("sections", []).append({"id": letter, "through": self._part.feature_id(through)})
        return self

    def spec(self):
        return json.loads(json.dumps(self._spec))

    def update(self, spec_dict):
        """Deep merge a layout patch, validating the Spec schema. Lists replace whole."""
        self._spec = json.loads(_native.layout_spec(json.dumps(self._spec), json.dumps(spec_dict)))
        return self

    def title(self, **fields):
        """Set title-block fields, e.g. drawn_by, date, revision, company, approved_by."""
        return self.update({"sheet": {"title": fields}})

    def render(self, out=None, *, formats=("svg", "dxf"), spec=None):
        """Return every requested output path; a PDF contains all sheets.

        ``spec`` is a one-render layout patch. Findings are in ``drawing.problems``.
        PMI stays in memory and is never written to the drawing Spec.
        """
        if isinstance(formats, str):
            raise TypeError("formats must be a sequence such as ('svg', 'pdf')")
        formats = tuple(formats)
        if not formats or len(set(formats)) != len(formats) or any(f not in ("svg", "pdf", "png", "dxf") for f in formats):
            raise ValueError("formats must be distinct members of svg, pdf, png, dxf")
        layout = _native.layout_spec(json.dumps(self._spec), json.dumps({} if spec is None else spec))
        part = self._part
        out = Path(os.fsdecode(os.fspath(out))) if out is not None else Path(part.name)
        if self._scale is not None:
            if not self._scale > 0:
                raise ValueError("scale must be positive")
            layout = json.loads(layout)
            layout.setdefault("sheet", {})["scale"] = f"{self._scale:g}:1" if self._scale >= 1 else f"1:{1 / self._scale:g}"
            layout = json.dumps(layout)
        result = json.loads(part._geometry().render(part._pmi, layout, str(out), list(formats)))
        self.problems = result["problems"]
        return tuple(result["written"])
