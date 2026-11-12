"""Requirements and drawings stated in build123d code (0.6.0-alpha.2, Python half).

``Part`` records what the author says about their own faces as Specify answers
(``datum.*``, ``part.*``, ``extra.*``), names those faces in the exported STEP
(``f:`` namespace, specify-core ``docs/face-names.md``) and writes the answers
beside it in Specify's answer-file format. build123d and OCP are imported only
when a method needs them; ``import draftwright`` never imports either.

Waits for the Rust half (Specify bindings): ``check()``, ``answer()``, hole
answers keyed to recognised features (``hole.function:*``, ``hole.position:*``,
``hole.frame:*``) and the "is this one recognised feature" reference check.
"""
import json
import os
import re
import tempfile
from pathlib import Path

from . import _native

_RUST_HALF = (
    "needs Specify's questions and recognition, which arrive with the Rust half "
    "of the build123d API in 0.6.0-alpha.2 (pyo3 bindings after the next quiddity/"
    "Specify release); the Python half only records answers and exports them"
)
_FIT = re.compile(r"^(JS|js|H|[fghkmnp])(\d{1,2})$")  # specify-core src/extras.rs
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
        self._part._extra(self, "flatness", _number(tolerance))
        return self

    def finish(self, ra):
        self._part._extra(self, "finish", _finish(ra))
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
        self._set_function("fit", fit)
        self._part._extra(self, "fit", str(fit), self.bores)
        return self

    def finish(self, ra):
        self._part._extra(self, "finish", _finish(ra), self.bores)
        return self

    def thread(self, size):
        """A tapped hole (``hole.function: tapped:M6``). Written to ``.pending.json`` for now."""
        self._set_function("thread", size)
        self._part._pending.append(
            {"hole": self.label, "faces": self._part._names_of(self.faces),
             "answer": "hole.function", "value": f"tapped:{size}"})
        return self

    def position(self, tolerance, *datums):
        """Position tolerance relative to datums (``hole.position`` / ``hole.frame``). Pending."""
        letters = [d.letter if isinstance(d, Datum) else str(d) for d in datums]
        for letter in letters:
            if letter not in self._part._datums:
                raise ValueError(f"{self.label}: datum {letter} has not been declared")
        self._part._pending.append(
            {"hole": self.label, "faces": self._part._names_of(self.faces),
             "answer": "hole.position", "value": _number(tolerance), "datums": letters})
        return self


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
        self._part = {}         # part.* answers
        self._datums = {}       # letter -> face indices
        self._extras = []       # extra.* answers (face indices)
        self._pending = []      # intent that waits for the Rust half

    # ---- part data -------------------------------------------------------
    def material(self, value):
        self._part["part.material"] = str(value)
        return self

    def process(self, value):
        self._part["part.process"] = str(value)
        return self

    def general_tolerance(self, value):
        self._part["part.general_tolerance"] = str(value)
        return self

    # ---- faces -----------------------------------------------------------
    def datum(self, letter, obj, label=None):
        if letter not in tuple("ABCDEFGH"):
            raise ValueError(f"datum letters are A to H, not {letter!r}")
        if letter in self._datums:
            raise ValueError(f"datum {letter} is already declared")
        faces = self._resolve(obj, label or f"datum_{letter}", f"datum {letter}")
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
        groups = self._resolve(obj, label or self._auto("hole"), "hole", split=True)
        if single and len(groups) > 1:
            raise ValueError(
                f"hole(): the object holds {len(groups)} separate holes; use holes() for several")
        faces = [i for g in groups for i in g]
        bores = []
        for g in groups:
            cylinders = [i for i in g if self._faces[i].geom_type.name == "CYLINDER"]
            if not cylinders:
                found = sorted({self._faces[i].geom_type.name for i in g})
                raise ValueError(f"hole(): no cylindrical face among the {len(g)} faces found ({', '.join(found)})")
            smallest = min(self._faces[i].radius for i in cylinders)
            bores += [i for i in cylinders if abs(self._faces[i].radius - smallest) < _TOL]
        return HoleRef(self, faces, self._last_label, bores)

    def _auto(self, role):
        self._groups[role] = self._groups.get(role, 0) + 1
        return f"{role}{self._groups[role]}"

    def _extra(self, ref, kind, value, faces=None):
        if kind in ("flatness", "finish") and faces is None:
            faces = ref.faces
        self._extras.append({"label": ref.label, "kind": kind, "value": value, "faces": list(faces)})

    # ---- resolving build123d objects to faces of this part ---------------
    def _resolve(self, obj, label, what, split=False):
        """Faces of this part that ``obj`` denotes, named ``f:<name>.<label>.<n>``.

        Faces of the part are taken as given. Any other object (a hole's cutter,
        a solid) expands to its faces, matched to the part's faces by geometry.
        """
        is_list = isinstance(obj, (list, tuple))
        given = list(obj) if is_list else list(obj.faces())
        groups = self._exact(given)
        # A boolean keeps some of a cutter's faces as the part's own, so a partial
        # overlap means "a tool", not "faces of the part": match it geometrically.
        if not is_list and (not groups or len(groups[0]) < len(given)):
            groups = self._geometric(obj) or groups
        groups = [sorted(g) for g in groups if g]
        if not groups:
            raise ValueError(
                f"{what}: none of the faces of {type(obj).__name__} is a face of the part "
                f"(or coincides with one); the part has {len(self._faces)} faces")
        label = _safe(label)
        self._last_label = label
        flat = sorted({i for g in groups for i in g})
        for n, i in enumerate(flat, 1):
            name = f"f:{self.name}.{label}.{n}"
            if i in self._names:
                continue
            clash = [j for j, existing in self._names.items() if existing == name]
            if clash:
                raise ValueError(
                    f"{what}: the name {name} is already on face {clash[0]}; give this "
                    f"reference its own label")
            self._names[i] = name
        return groups if split else flat

    def _index(self, face):
        for i, own in enumerate(self._faces):
            if own.is_same(face):
                return i
        return None

    def _exact(self, faces):
        found = [i for i in (self._index(f) for f in faces) if i is not None]
        return [found] if found else []

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
            return abs(own.radius - tool.radius) < _TOL
        if kind == "PLANE":
            a, b = own.normal_at(), tool.normal_at()
            return abs(abs(a.X * b.X + a.Y * b.Y + a.Z * b.Z) - 1) < 1e-6
        return True

    def _names_of(self, indices):
        return [self._names[i] for i in sorted(indices)]

    # ---- answers ---------------------------------------------------------
    def _answers(self, number):
        out = dict(self._part)
        for letter, faces in sorted(self._datums.items()):
            out[f"datum.{letter}"] = sorted(number[i] for i in faces)
        for e in self._extras:
            key = f"extra.{e['label']}.{e['kind']}"
            n = 1
            while key in out:
                n += 1
                key = f"extra.{e['label']}.{e['kind']}{n}"
            out[key] = {"faces": sorted(number[i] for i in e["faces"]), "kind": e["kind"], "value": e["value"]}
        return out

    def check(self, *args, **kwargs):
        raise NotImplementedError(f"Part.check() {_RUST_HALF}")

    def answer(self, *args, **kwargs):
        raise NotImplementedError(f"Part.answer() {_RUST_HALF}")

    # ---- STEP ------------------------------------------------------------
    def export_step(self, path):
        """Write the STEP with the referenced faces named, and the answers beside it.

        Returns ``{"step": ..., "answers": ..., "pending": ... or None}``. The answers
        file is ``<stem>.answers.json``, a plain Specify answer map (no ``_specify``
        evidence: that needs Specify's analysis). Thread and position intent, which
        Specify keys to recognised features, goes to ``<stem>.pending.json``.
        """
        path = Path(os.fspath(path))
        names = write_step(self.shape, path, self._names, self._faces, self.name)
        number = {i: names[self._names[i]] for i in self._names}
        answers = self._answers(number)
        side = path.with_suffix("")
        answers_path = side.with_name(side.name + ".answers.json")
        answers_path.write_text(json.dumps(answers, indent=2, sort_keys=True) + "\n")
        pending_path = None
        pending_file = side.with_name(side.name + ".pending.json")
        if self._pending:
            pending_path = pending_file
            pending_path.write_text(json.dumps(
                {"note": "intent that Specify keys to recognised features; applied by the Rust half",
                 "requirements": self._pending}, indent=2) + "\n")
        elif pending_file.exists():
            pending_file.unlink()
        return {"step": path, "answers": answers_path, "pending": pending_path}

    # ---- drawing ---------------------------------------------------------
    def drawing(self, sheet=None, scale=None, units="mm"):
        return Drawing(self, sheet=sheet, scale=scale, units=units)


def _step_names(path):
    text = Path(path).read_text(errors="replace")
    return re.findall(r"ADVANCED_FACE\('([^']*)'", text)


def write_step(shape, path, names, faces, product):
    """Write ``shape`` as STEP with ``names`` (face index -> name) on its faces.

    build123d's ``export_step`` names the product and its assembly nodes but not
    sub-shapes, so this is the short XCAF writer the proposal asked about: the
    same OCCT path with a name label on each sub-shape and
    ``write.stepcaf.subshapes.name = 1``. Returns ``{name: face index in the STEP}``,
    read back from the file, and raises unless every name landed on exactly one face.
    """
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Message import Message, Message_Gravity
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Controller
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    for printer in Message.DefaultMessenger_s().Printers():  # as build123d: keep OCCT quiet
        printer.SetTraceLevel(Message_Gravity.Message_Fail)
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    app = XCAFApp_Application.GetApplication_s()
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    app.InitDocument(doc)
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    top = tool.AddShape(shape.wrapped, False)
    TDataStd_Name.Set_s(top, TCollection_ExtendedString(product))
    for index, name in sorted(names.items()):
        label = tool.AddSubShape(top, faces[index].wrapped)
        if label.IsNull():
            raise RuntimeError(f"OCCT would not take {name} as a sub-shape of the part")
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
    # The static is only honoured once the controllers are initialised.
    STEPCAFControl_Controller.Init_s()
    STEPControl_Controller.Init_s()
    key = "write.stepcaf.subshapes.name"
    before = Interface_Static.IVal_s(key)
    Interface_Static.SetIVal_s(key, 1)
    try:
        writer = STEPCAFControl_Writer()
        writer.SetNameMode(True)
        writer.Transfer(doc, STEPControl_AsIs)
        status = writer.Write(os.fspath(path))
    finally:
        Interface_Static.SetIVal_s(key, before)
    if status != IFSelect_RetDone:
        raise RuntimeError("OCCT failed to write the STEP file")
    written = _step_names(path)
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
        z-axis section through holes. A build123d object is not accepted yet: naming
        its recognised feature needs the Rust half."""
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
            raise NotImplementedError(f"Drawing.section(through=<object>) {_RUST_HALF}; pass a feature id string")
        return self

    def spec(self):
        return json.loads(json.dumps(self._spec))

    def render(self, out=None):
        """Render with the Rust engine; returns ``(svg_path, dxf_path)`` like ``make_drawing``."""
        with tempfile.TemporaryDirectory(prefix="draftwright-") as temporary:
            step = Path(temporary) / "part.step"
            part = self._part
            write_step(part.shape, step, dict(part._names), part._faces, part.name)
            return _native.render(
                str(step), os.fsdecode(os.fspath(out)) if out is not None else part.name,
                json.dumps(self._spec), self._scale, None)
