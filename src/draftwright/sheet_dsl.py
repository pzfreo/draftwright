"""sheet_dsl — the fluent feature-referencing drawing surface (ADR 0011, #445/#446).

Reference the build123d objects you built, declare the drawing aspects they need,
export. Geometry supplies the size (⌀ read off the object); you supply only the
intent. Built on the ``model=`` seam (:func:`draftwright.build_drawing`), so
detection is skipped and the auto-pass dimensions exactly the declared features::

    sheet = Sheet(part, title="PLATE", number="DWG-001")
    sheet.envelope()
    sheet.hole(h1)
    sheet.hole(h2).depth(5)          # a blind hole — adds a depth callout
    sheet.diameter(boss_cyl)
    sheet.export("plate")

**Scope (this module):** the *feature-declaration* surface over the renderers the
engine has today — dimensions, ⌀ callouts, holes (through / blind), turned steps,
slots, patterns, the overall envelope, and the auto section — plus the P2a
**``.tolerance``** (a ± / limit tolerance on a diameter, a step, or a hole bore) and
**``.fit``** (fit-class → ISO 286 deviation, P2a.2) aspects, the P2c GD&T side-layer
(**``.finish``** surface symbols, **``sheet.datum``** feature symbols, and
**``sheet.control(...)``** feature control frames — all 14 ISO 1101 characteristics, ADR 0011
#479 — which derive their target view/strip from the referenced feature or planar face), and
**``sheet.table()``** / **``sheet.notes()``** corner-block tables (notes / revision / BOM /
schedule, over the engine's auto-placed ``Drawing.add_table``, #488), and **``sheet.note()``** /
**``.note()``** anchored free-text manufacturing-note leaders (thread specs, ``DEBURR``,
chip-relief, knurl — the shop callouts detection can't infer, placed via the GD&T corridor
machinery, #488). The remaining #445 aspect verb that still needs wiring — a structured
``.thread`` — is deliberately **not** stubbed here yet, so the surface only exposes what
actually draws.

**Hybrid.** :meth:`Sheet.from_part` seeds the declared set from *detection*, so you
can start from the detected model and override specific features (declaration is for
where you know better than detection, not everywhere — ADR 0011 §3); :meth:`Sheet.of`
returns a fluent handle onto one of those generated features (by object, index, or the
feature itself) so you can ``.fit(...)`` / ``.tolerance(...)`` it without re-declaring (#463).
"""

from __future__ import annotations

import warnings
from dataclasses import replace

from draftwright.analysis import _solids_body
from draftwright.builder import _coerce_model, build_drawing, detect_part_model
from draftwright.fits import fit_class
from draftwright.model import AuthoredDimension, Feature, Frame
from draftwright.model import boss as _boss
from draftwright.model import control_frame as _declare_control
from draftwright.model import datum as _declare_datum
from draftwright.model import envelope as _envelope
from draftwright.model import finish as _declare_finish
from draftwright.model import hole as _hole
from draftwright.model import note as _declare_note
from draftwright.model import pattern as _pattern
from draftwright.model import slot as _slot
from draftwright.model import step as _step
from draftwright.model.declare import _norm_axis, _read_cylinder, _require_positive, gdt_target
from draftwright.model.declare import read_bore_step as _read_bore_step
from draftwright.model.ir import AUTHORED_DIMENSION_KINDS, Point


def _point3(name: str, p) -> Point:
    vals = tuple(float(c) for c in p)
    if len(vals) != 3:
        raise ValueError(f"dimension() {name} must be a 3-tuple")
    return (vals[0], vals[1], vals[2])


def _parse_datums(to) -> tuple[str, ...]:
    """The datum letters a ``to=`` argument names: ``None`` → ``()``; a sequence →
    stripped letters; a string split on spaces / ``|`` / ``,`` (``"A B"`` / ``"A|B"``)."""
    if to is None:
        return ()
    if isinstance(to, (tuple, list)):
        return tuple(str(d).strip() for d in to if str(d).strip())
    return tuple(str(to).replace("|", " ").replace(",", " ").split())


def _parse_scale(scale):
    """Accept a float multiplier, a ratio string (``"2:1"`` → 2.0, ``"1:2"`` → 0.5),
    a bare numeric string, or ``None`` (auto). The engine's ``scale=`` is a raw float;
    the ratio string is the drawing-sheet spelling. Raises ``ValueError`` on a malformed
    string so a bad scale fails here, not deep in the engine with a str where a float is
    expected."""
    if scale is None or isinstance(scale, (int, float)):
        return scale
    if isinstance(scale, str):
        if ":" in scale:
            num, den = scale.split(":", 1)
            denom = float(den)
            if denom == 0:
                raise ValueError(f"invalid scale ratio {scale!r}: zero denominator")
            return float(num) / denom
        return float(scale)  # a bare numeric string; ValueError if not a number
    raise TypeError(f"scale must be a number, ratio string, or None — got {type(scale).__name__}")


def _tol_value(lo, hi):
    """A ± tolerance value from the handle args: a symmetric ``float`` (``hi is None``) or
    an ``(lower, upper)`` limit pair. The pair renders ``+upper -lower`` (helpers'
    convention), so ``.tolerance(0.0, 0.1)`` → ``+0.1 -0.0`` — both magnitudes positive."""
    return lo if hi is None else (lo, hi)


class _Hole:
    """A fluent handle for one declared hole — through vs blind (which changes the callout),
    and the P2a ± tolerance on its bore ⌀."""

    def __init__(self, sheet: Sheet, index: int) -> None:
        self._sheet = sheet
        self._i = index

    def through(self) -> _Hole:
        """A through hole (the default) — ⌀ only."""
        return self._set(through=True, depth=None)

    def depth(self, d: float) -> _Hole:
        """A blind hole *d* mm deep — adds a depth callout."""
        return self._set(through=False, depth=d)

    def tolerance(self, lo: float, hi: float | None = None) -> _Hole:
        """A ± tolerance on the bore ⌀: symmetric ``.tolerance(0.05)`` (→ ``±0.05``) or a
        limit pair ``.tolerance(0.0, 0.1)`` (→ ``+0.1 -0.0``)."""
        self._sheet._tolerances[(self._i, "diameter")] = _tol_value(lo, hi)
        return self

    def fit(self, code: str, *, show: str = "class") -> _Hole:
        """An ISO 286 fit class on the bore ⌀ — ``.fit("H7")`` renders ``ø8 H7`` (the class,
        default) or, with ``show="deviation"``, the signed deviations ``ø8 +0.015/0`` resolved
        for the bore's nominal ⌀. Raises for a class/size outside the built-in table (#29)."""
        self._sheet._tolerances[(self._i, "diameter")] = fit_class(
            code, self._sheet._features[self._i].diameter, show
        )
        return self

    def cbore(
        self, obj=None, *, diameter: float | None = None, depth: float | None = None
    ) -> _Hole:
        """A counterbore on this hole. ``.cbore(cbore_cyl)`` reads its ⌀ + depth off the
        counterbore tool object (⌀ from the cylindrical face, depth from the part + tool along
        the hole axis — no numbers restated), or pass explicit ``.cbore(diameter=…, depth=…)``.
        An object supplies defaults; explicit kwargs override (#462)."""
        return self._set(cbore=self._read_step("cbore", obj, diameter, depth))

    def spotface(
        self, obj=None, *, diameter: float | None = None, depth: float | None = None
    ) -> _Hole:
        """A spotface on this hole — same as :meth:`cbore` but a shallow facing (#462)."""
        return self._set(spotface=self._read_step("spotface", obj, diameter, depth))

    def _read_step(self, kind, obj, diameter, depth) -> tuple[float, float]:
        if obj is not None:
            rd, rdp = _read_bore_step(
                self._sheet._part, obj, self._sheet._features[self._i].frame.axis
            )
            diameter = rd if diameter is None else diameter
            depth = rdp if depth is None else depth
        if diameter is None or depth is None:
            raise ValueError(f"{kind} needs a tool object, or explicit diameter= and depth=")
        # same positivity guard declare.hole() applies to cbore/spotface (#452/#462 review)
        _require_positive(**{f"{kind} diameter": diameter, f"{kind} depth": depth})
        return (diameter, depth)

    def finish(self, ra, *, view: str | None = None, side: str | None = None) -> _Hole:
        """A surface-finish symbol (Ra) on this hole's bore (ADR 0011 P2c). ``.finish("1.6")``
        — the roughness text; ``view``/``side`` override the derived strip."""
        self._sheet._gdt_finish(ra, self._i, view=view, side=side)
        return self

    def note(self, text, *, view: str | None = None, side: str | None = None) -> _Hole:
        """A free-text manufacturing note on a leader to this hole (#488). ``.note("M3x0.5 TAP")``
        — the shop callout; ``view``/``side`` override the derived strip."""
        self._sheet._gdt_note(text, self._i, view=view, side=side)
        return self

    def _set(self, **kw) -> _Hole:
        self._sheet._features[self._i] = replace(self._sheet._features[self._i], **kw)
        return self


class _Dim:
    """A fluent handle for a declared dimension-bearing feature (a diameter / boss OD, or a
    turned step), carrying the P2a ``.tolerance`` aspect. ``default_kind`` is the parameter a
    bare ``.tolerance(...)`` targets — ``"diameter"`` for an OD, ``"length"`` for a step."""

    def __init__(self, sheet: Sheet, index: int, default_kind: str) -> None:
        self._sheet = sheet
        self._i = index
        self._kind = default_kind

    def tolerance(self, lo: float, hi: float | None = None, *, on: str | None = None) -> _Dim:
        """A ± tolerance on this dimension: symmetric ``.tolerance(0.05)`` (→ ``±0.05``) or a
        limit pair ``.tolerance(0.0, 0.1)`` (→ ``+0.1 -0.0``). ``on`` picks the parameter for
        a multi-dim feature — a step's ``"length"`` (default) vs its ``"diameter"`` (OD)."""
        self._sheet._tolerances[(self._i, on or self._kind)] = _tol_value(lo, hi)
        return self

    def fit(self, code: str, *, show: str = "class") -> _Dim:
        """An ISO 286 fit class on this feature's ⌀ (always the diameter — a fit is diametral,
        so a step's fit is on its OD, not its length). ``.fit("h6")`` renders ``ø12 h6`` (the
        class, default) or ``show="deviation"`` the signed deviations ``ø12 0/-0.011`` resolved
        for the nominal ⌀. Raises for a class/size outside the built-in table (#29)."""
        self._sheet._tolerances[(self._i, "diameter")] = fit_class(
            code, self._sheet._features[self._i].diameter, show
        )
        return self

    def finish(self, ra, *, view: str | None = None, side: str | None = None) -> _Dim:
        """A surface-finish symbol (Ra) on this feature's surface (ADR 0011 P2c).
        ``diameter(journal).finish("0.8")``; ``view``/``side`` override the derived strip."""
        self._sheet._gdt_finish(ra, self._i, view=view, side=side)
        return self

    def note(self, text, *, view: str | None = None, side: str | None = None) -> _Dim:
        """A free-text manufacturing note on a leader to this feature (#488).
        ``diameter(knurl).note("KNURL 0.8 STRAIGHT")``; ``view``/``side`` override the strip."""
        self._sheet._gdt_note(text, self._i, view=view, side=side)
        return self


class _Control:
    """A fluent GD&T feature-control-frame builder (ADR 0011 P2c.2). One method per ISO 1101
    characteristic — each appends a control frame on the same target, so chained calls stack::

        sheet.control(bore).position(0.1, to="A B").perpendicularity(0.05, to="A")

    ``to=`` names the referenced datum letter(s) (``"A"`` / ``"A B"`` / ``("A", "B")``);
    ``diameter=`` prefixes the zone with ``⌀`` (the default for position/concentricity);
    ``modifier=`` a material-condition symbol (``"M"``/``"L"``/``"P"``). The target view + strip
    are derived once (from the feature/face) when :meth:`Sheet.control` runs; ``view=``/``side=``
    there override them."""

    def __init__(self, sheet: Sheet, target, src, view: str, side: str) -> None:
        self._sheet = sheet
        self._target = target
        self._src = src
        self._view = view
        self._side = side

    def _add(self, characteristic, tol, *, to=None, diameter=False, modifier=None) -> _Control:
        item = _declare_control(
            characteristic,
            tol,
            self._target,
            self._sheet._part,
            datums=_parse_datums(to),
            diameter=diameter,
            modifier=modifier,
            view=self._view,
            side=self._side,
        )
        self._sheet._append_gdt(item, self._src)
        return self

    # Form tolerances (no datum reference) --------------------------------------------------
    def straightness(self, tol, *, modifier=None) -> _Control:
        return self._add("straightness", tol, modifier=modifier)

    def flatness(self, tol, *, modifier=None) -> _Control:
        return self._add("flatness", tol, modifier=modifier)

    def circularity(self, tol, *, modifier=None) -> _Control:
        return self._add("circularity", tol, modifier=modifier)

    def cylindricity(self, tol, *, modifier=None) -> _Control:
        return self._add("cylindricity", tol, modifier=modifier)

    # Profile ------------------------------------------------------------------------------
    def profile_line(self, tol, *, to=None, modifier=None) -> _Control:
        return self._add("profile_line", tol, to=to, modifier=modifier)

    def profile_surface(self, tol, *, to=None, modifier=None) -> _Control:
        return self._add("profile_surface", tol, to=to, modifier=modifier)

    # Orientation --------------------------------------------------------------------------
    def angularity(self, tol, *, to=None, modifier=None) -> _Control:
        return self._add("angularity", tol, to=to, modifier=modifier)

    def perpendicularity(self, tol, *, to=None, modifier=None) -> _Control:
        return self._add("perpendicularity", tol, to=to, modifier=modifier)

    def parallelism(self, tol, *, to=None, modifier=None) -> _Control:
        return self._add("parallelism", tol, to=to, modifier=modifier)

    # Location (a position/concentricity zone is diametral by default) ---------------------
    def position(self, tol, *, to=None, diameter=True, modifier=None) -> _Control:
        return self._add("position", tol, to=to, diameter=diameter, modifier=modifier)

    def concentricity(self, tol, *, to=None, diameter=True, modifier=None) -> _Control:
        return self._add("concentricity", tol, to=to, diameter=diameter, modifier=modifier)

    def symmetry(self, tol, *, to=None, modifier=None) -> _Control:
        return self._add("symmetry", tol, to=to, modifier=modifier)

    # Runout -------------------------------------------------------------------------------
    def circular_runout(self, tol, *, to=None, modifier=None) -> _Control:
        return self._add("circular_runout", tol, to=to, modifier=modifier)

    def total_runout(self, tol, *, to=None, modifier=None) -> _Control:
        return self._add("total_runout", tol, to=to, modifier=modifier)


class Sheet:
    """Reference features, declare their drawing aspects, export.

    Each declaration method mirrors a :mod:`draftwright.model` constructor: pass the
    build123d object to read its geometry, or explicit values. :meth:`hole` returns a
    chainable :class:`_Hole` (``.through()`` / ``.depth()``); the others return the
    ``Sheet`` so declarations can chain. :meth:`build` / :meth:`export` hand the declared
    features to the engine with detection skipped.
    """

    def __init__(
        self,
        part,
        *,
        title=None,
        number="DWG-001",
        drawn_by=None,
        tolerance=None,
        scale=None,
        page=None,
        out=None,
    ):
        self._part = part
        self._features: list = []
        # P2a ± tolerances, keyed by (feature index, ParamKind) so a handle survives a later
        # feature replacement (e.g. hole().depth()); materialized to (feature, kind) at build.
        self._tolerances: dict = {}
        # P2c GD&T provenance: (gdt_feature_index -> source_feature_index). A finish/datum stores
        # its origin by INDEX, not the object, so a later size verb replacing the source feature
        # (hole().depth()) doesn't strand the link; materialized to the FINAL object at build.
        self._gdt_src: list = []
        # Corner-block tables (notes / revision / BOM / schedule) — applied at build() via the
        # engine's generic auto-placed Drawing.add_table, AFTER the drawing is built so they sit
        # clear of the views + title block (like the hole table). Each: {rows, prefer, name}.
        self._tables: list = []
        self._opts = dict(
            title=title, number=number, scale=_parse_scale(scale), page=page, out=out
        )
        # drawn_by / tolerance (title block, #474) forward to build_drawing only when set, so an
        # unset value keeps build_drawing's own defaults ("" / "ISO 2768-m") rather than None.
        if drawn_by is not None:
            self._opts["drawn_by"] = drawn_by
        if tolerance is not None:
            self._opts["tolerance"] = tolerance

    @classmethod
    def from_part(cls, part, **opts) -> Sheet:
        """Seed the declared set from *detection* (the hybrid mode, ADR 0011 §3): start
        from the model the detector recovers, then override specific features (edit the
        list via :attr:`features`, or re-declare) before :meth:`build`."""
        sheet = cls(part, **opts)
        sheet._features = list(detect_part_model(part).features)  # detect only, no render (#453)
        return sheet

    # -- feature declaration --------------------------------------------------

    def add(self, feature) -> Sheet:
        """Append a pre-built IR :class:`~draftwright.model.Feature` (escape hatch for
        the constructors this façade does not surface directly, e.g. PMI)."""
        self._features.append(feature)
        return self

    def dimension(
        self,
        *,
        kind: str,
        value: float,
        label: str,
        dominant_axis: str,
        ref_pts,
        ref_bbox=None,
        at=None,
        axis: str | None = None,
        upper_tol: float | None = None,
        lower_tol: float | None = None,
        source: str = "sheet",
        source_kind: str | None = None,
    ) -> Sheet:
        """Declare a pre-authored drafting dimension from explicit measured values.

        This is the concept-shaped Sheet API used by generated AP242 scripts: the source file
        may call the record PMI, but the editable script declares a dimension category, value,
        label, referenced model points, and optional structured tolerances. For ordinary
        geometry-backed edits prefer feature handles such as ``sheet.hole(...).tolerance(...)``.
        """
        _require_positive(value=value)
        dim_kind = str(kind).lower()
        if dim_kind not in AUTHORED_DIMENSION_KINDS:
            allowed = ", ".join(sorted(AUTHORED_DIMENSION_KINDS))
            raise ValueError(f"dimension() kind must be one of: {allowed}")
        pts = tuple(_point3("ref_pts item", p) for p in ref_pts)
        if len(pts) < 2:
            raise ValueError("dimension() needs at least two ref_pts")
        bbox = None if ref_bbox is None else tuple(float(c) for c in ref_bbox)
        if bbox is not None and len(bbox) != 6:
            raise ValueError("dimension() ref_bbox must be a 6-tuple")
        dom = str(dominant_axis).upper()
        if dom not in ("X", "Y", "Z"):
            if not (dom == "?" and dim_kind in ("diameter", "radius") and bbox is not None):
                raise ValueError("dimension() dominant_axis must be X, Y, or Z")
        if at is None:
            if bbox is not None:
                x0, y0, z0, x1, y1, z1 = bbox
                at = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
            else:
                n = len(pts)
                at = tuple(sum(p[i] for p in pts) / n for i in range(3))
        origin = _point3("at", at)
        ax = _norm_axis(axis or (dom.lower() if dom in ("X", "Y", "Z") else "z"))
        self._features.append(
            AuthoredDimension(
                frame=Frame(origin, ax),
                dimension_kind=dim_kind,
                value=float(value),
                label=str(label),
                dominant_axis=dom,
                upper_tol=upper_tol,
                lower_tol=lower_tol,
                ref_bbox=bbox,
                ref_pts=pts,
                source=source,
                source_kind=source_kind or dim_kind,
            )
        )
        return self

    def of(self, ref) -> _Hole | _Dim:
        """A decoratable handle onto an **existing** feature — the hybrid seam (#463).

        *ref* is a feature index, a :class:`Feature` already in :attr:`features` (e.g. seeded by
        :meth:`from_part`), or the build123d **object** you built (matched by ⌀ + in-plane
        position). Returns the same fluent handle the declaration verbs do, so you can
        ``.fit(...)`` / ``.tolerance(...)`` — and, for a hole, ``.cbore(...)`` — a feature you
        did not declare from scratch. Raises if the object matches no feature or is ambiguous."""
        i = self._index_of(ref)
        kind = self._features[i].kind
        if kind == "hole":
            return _Hole(self, i)
        if kind in ("boss", "step"):
            return _Dim(self, i, "diameter" if kind == "boss" else "length")
        raise ValueError(f"of(): no aspect handle for a {kind!r} feature (holes / bosses / steps)")

    def _index_of(self, ref) -> int:
        if isinstance(ref, bool):
            raise TypeError("of(): ref must be an index, a Feature, or a build123d object")
        if isinstance(ref, int):
            n = len(self._features)
            if not -n <= ref < n:
                raise IndexError(f"of(): feature index {ref} out of range (have {n})")
            return ref % n
        if isinstance(ref, Feature):
            for i, f in enumerate(self._features):
                if f is ref:
                    return i
            raise ValueError("of(): that Feature is not in this sheet's features")
        return self._match_object(ref)

    def _match_object(self, obj) -> int:
        """The index of the declared feature the build123d *obj* refers to, by axis + ⌀ + the
        two in-plane coordinates (the axial position is where they legitimately differ)."""
        axis, dia, center = _read_cylinder(obj)
        axis = _norm_axis(axis)
        perp = [k for k in range(3) if k != "xyz".index(axis)]
        matches = [
            i
            for i, f in enumerate(self._features)
            if getattr(f, "diameter", None) is not None
            and _norm_axis(f.frame.axis) == axis  # same axis — a cross-hole must not match
            and abs(f.diameter - dia) <= 0.2
            and all(abs(f.frame.origin[k] - center[k]) <= 0.5 for k in perp)
        ]
        if not matches:
            raise ValueError("of(): no declared feature matches that object (⌀ + position)")
        if len(matches) > 1:
            raise ValueError("of(): the object matches several features — pass an index instead")
        return matches[0]

    def hole(self, obj=None, **kw) -> _Hole:
        """Declare a hole from the tool cylinder you subtracted (or explicit values).
        Returns a fluent handle: ``.through()`` (default) / ``.depth(d)``."""
        self._features.append(_hole(obj, **kw))
        return _Hole(self, len(self._features) - 1)

    def diameter(self, obj=None, **kw) -> _Dim:
        """Declare an external cylindrical diameter (a boss / OD) — the ⌀ is read off the
        object. Returns a handle: chain ``.tolerance(...)`` for a ± on the ⌀ (P2a)."""
        self._features.append(_boss(obj, **kw))
        return _Dim(self, len(self._features) - 1, "diameter")

    def boss(self, obj=None, **kw) -> _Dim:
        """Alias of :meth:`diameter` — an external cylindrical boss / OD."""
        return self.diameter(obj, **kw)

    def step(self, obj=None, **kw) -> _Dim:
        """Declare one axial segment of a turned profile (its OD + length). A model with any
        step renders as a turned part. Returns a handle: ``.tolerance(...)`` tolerances the
        step *length* by default, ``.tolerance(..., on="diameter")`` its OD (P2a)."""
        self._features.append(_step(obj, **kw))
        return _Dim(self, len(self._features) - 1, "length")

    def slot(self, obj=None, **kw) -> Sheet:
        """Declare a milled slot / reduced across-flats section (width + length)."""
        self._features.append(_slot(obj, **kw))
        return self

    def pattern(self, member, **kw) -> Sheet:
        """Declare a hole pattern (bolt circle / linear array / grid) — build the
        *member* with :func:`draftwright.model.hole`."""
        self._features.append(_pattern(member, **kw))
        return self

    def envelope(self, obj=None) -> Sheet:
        """Declare the overall bounding dimensions. Defaults to the whole part."""
        self._features.append(_envelope(obj if obj is not None else self._part))
        return self

    # -- GD&T / finish aspects (ADR 0011 P2c, #479) ---------------------------

    def datum(
        self, letter: str, ref, *, view: str | None = None, side: str | None = None
    ) -> Sheet:
        """Declare a datum feature symbol (ISO 5459). *ref* is a build123d **planar face**, or
        a feature handle / :class:`Feature` / index for a feature's axis. The target view + strip
        side are derived from the geometry; ``view``/``side`` override them (ADR 0011 P2c)."""
        target, src = self._gdt_ref(ref)
        self._append_gdt(_declare_datum(letter, target, self._part, view=view, side=side), src)
        return self

    def finish(self, ra, ref, *, view: str | None = None, side: str | None = None) -> Sheet:
        """Declare a surface-finish symbol (ISO 1302, Ra) on *ref* — a build123d planar face or
        a feature. ``sheet.finish("3.2", top_face)``; ``view``/``side`` override the strip."""
        target, src = self._gdt_ref(ref)
        self._append_gdt(_declare_finish(ra, target, self._part, view=view, side=side), src)
        return self

    def note(self, text, ref, *, view: str | None = None, side: str | None = None) -> Sheet:
        """Declare a free-text manufacturing note (#488) on a leader to *ref* — a build123d planar
        face or a feature. The shop callouts detection can't infer: thread specs
        (``sheet.note("M3x0.5 TAP", bore)``), ``DEBURR``, chip-relief, knurl. Placed like the GD&T
        items, clear of the views/title block; ``view``/``side`` override the derived strip."""
        target, src = self._gdt_ref(ref)
        self._append_gdt(_declare_note(text, target, self._part, view=view, side=side), src)
        return self

    def control(self, ref, *, view: str | None = None, side: str | None = None) -> _Control:
        """A GD&T feature-control-frame builder on *ref* — a feature handle / :class:`Feature` /
        index, or a build123d planar face. Chain one method per ISO 1101 characteristic
        (``.position(0.1, to="A B")`` …); each stacks a frame on the target. The target view +
        strip are derived from the geometry; ``view``/``side`` override them (ADR 0011 P2c.2)."""
        target, src = self._gdt_ref(ref)
        v, s, _site, _axis = gdt_target(target, self._part, view=view, side=side)
        return _Control(self, target, src, v, s)

    def _gdt_finish(self, ra, src_index: int, *, view=None, side=None) -> None:
        """A finish declared through a fluent handle — sources its provenance from the handle's
        feature INDEX (not the object), so a later size verb on the same handle can't strand it."""
        item = _declare_finish(ra, self._features[src_index], self._part, view=view, side=side)
        self._append_gdt(item, src_index)

    def _gdt_note(self, text, src_index: int, *, view=None, side=None) -> None:
        """A note declared through a fluent handle — like :meth:`_gdt_finish`, sources provenance
        from the feature INDEX so a later size verb on the same handle can't strand it."""
        item = _declare_note(text, self._features[src_index], self._part, view=view, side=side)
        self._append_gdt(item, src_index)

    def _append_gdt(self, item, src_index) -> None:
        """Append a GD&T IR item, recording its source-feature index for build-time provenance
        re-materialization (``None`` for a bare face — no source feature to track)."""
        self._features.append(item)
        if src_index is not None:
            self._gdt_src.append((len(self._features) - 1, src_index))

    def _gdt_ref(self, ref):
        """Resolve a GD&T target to ``(target, source_index)``: a fluent handle / index / a
        :class:`Feature` already in :attr:`features` → its feature + index (the index re-binds
        provenance at build); a build123d face or an external Feature → ``(ref, None)``."""
        if isinstance(ref, (_Hole, _Dim)):
            return self._features[ref._i], ref._i
        if isinstance(ref, int) and not isinstance(ref, bool):
            i = self._index_of(ref)
            return self._features[i], i
        if isinstance(ref, Feature):
            for i, f in enumerate(self._features):
                if f is ref:
                    return ref, i
            return ref, None  # an external feature this sheet does not manage
        return ref, None  # a build123d face — no source feature

    def _materialize_gdt(self) -> None:
        """Re-bind each handle-sourced GD&T item's ``origin`` to the FINAL source feature (a
        size verb may have replaced it since declaration). Idempotent; mirrors P2a's
        :meth:`_decorations`. Called before handing features to the engine."""
        for gi, si in self._gdt_src:
            self._features[gi] = replace(self._features[gi], origin=self._features[si])

    def _validate_datums(self) -> None:
        """Warn (non-fatal) if a control frame references a datum letter no ``sheet.datum`` on
        this sheet declared — a likely typo (``to="A"`` with no datum A). ADR 0011 P2c.2."""
        declared = {f.letter for f in self._features if getattr(f, "kind", None) == "datum_ref"}
        referenced = {
            d
            for f in self._features
            if getattr(f, "kind", None) == "control_frame"
            for d in f.datums
        }
        missing = sorted(referenced - declared)
        if missing:
            warnings.warn(
                f"control frame references undeclared datum(s) {missing} — declare each with "
                "sheet.datum(letter, ref)",
                stacklevel=3,
            )

    def _prepare(self) -> None:
        """Resolve deferred GD&T state before handing features to the engine."""
        self._materialize_gdt()
        self._validate_datums()

    # -- corner-block tables (notes / revision / BOM / schedule) --------------

    def table(
        self, rows, *, prefer: str = "tr", name: str | None = None, block_cols=None
    ) -> Sheet:
        """Declare a corner-block data table — positioned at :meth:`build` by the engine's generic
        auto-placer, clear of the views, title block, and annotations (the same machinery as the
        hole table), and lint-checked. *rows* is a sequence of equal-length row sequences (row 0
        the header); cells are stringified. *prefer* is the page corner to sit nearest
        (``"tr"``/``"tl"``/``"br"``/``"bl"``). A table with no free corner records a
        ``table_dropped`` lint — it never overlaps. Revision blocks, BOMs and schedules all use
        this; :meth:`notes` is the single-column convenience over it."""
        rows = list(rows)
        # A str/bytes row would iterate character-by-character into columns — a silent-garbage
        # trap, especially since notes() legitimately takes a flat list of strings. Reject it and
        # point at notes() (the single-column convenience).
        if any(isinstance(r, (str, bytes)) for r in rows):
            raise ValueError(
                "table rows must be sequences of cells, not strings — for a single-column table "
                "of text use sheet.notes([...])"
            )
        norm = [tuple(str(c) for c in r) for r in rows]
        if not norm:
            raise ValueError("table needs at least one row")
        width = len(norm[0])
        if width == 0 or any(len(r) != width for r in norm):
            raise ValueError("table rows must all have the same (non-zero) number of columns")
        self._tables.append(
            {
                "rows": norm,
                "prefer": prefer,
                "name": name or f"table{len(self._tables)}",
                "block_cols": block_cols,
            }
        )
        return self

    def notes(
        self, lines, *, title: str | None = "NOTES", number: bool = True, prefer="tr"
    ) -> Sheet:
        """Declare a manufacturing NOTES block — a single-column :meth:`table` of *lines* with a
        *title* header (``None`` to omit) and optional ``1  …`` auto-numbering::

            sheet.notes(["BREAK ALL EDGES 0.3", "DEBURR", "M3x0.5 TAP"])
        """
        rows = [(title,)] if title else []
        rows += [(f"{i}  {line}" if number else str(line),) for i, line in enumerate(lines, 1)]
        if len(rows) <= (1 if title else 0):
            raise ValueError("notes needs at least one line")
        return self.table(rows, prefer=prefer, name=f"notes{len(self._tables)}")

    # -- inspection / output --------------------------------------------------

    @property
    def features(self) -> list:
        """The declared IR features (mutable — override or drop before :meth:`build`)."""
        return self._features

    def _decorations(self) -> dict:
        """Materialize the index-keyed ± tolerances against the FINAL features (a handle may
        have been recorded before a later .depth()/… replaced the feature) → the
        ``(feature, kind)`` decoration map the planner reads (P2a)."""
        return {(self._features[i], kind): tol for (i, kind), tol in self._tolerances.items()}

    def model(self):
        """The IR the engine will draw (detection skipped) — for inspection. Wraps the
        declared features into a :class:`PartModel` **without** rendering a drawing (#453):
        the same wrapping :meth:`build` hands the engine (part bbox + corner datum + step-
        inferred orientation + the P2a decorations), so inspection pays no projection/anno
        cost and can't hit a layout/render failure. Wraps the *solids body* (as :func:`_analyse`
        does), so the bbox/datum match what ``build()`` draws even when the part carries
        bbox-extending non-solid geometry."""
        self._prepare()
        return _coerce_model(self._features, _solids_body(self._part), self._decorations())

    def build(self):
        """Build the :class:`~draftwright.drawing.Drawing` — detection skipped; only the
        declared features are drawn. Declared corner-block tables (:meth:`table`/:meth:`notes`)
        are placed last, clear of everything already on the sheet."""
        self._prepare()
        dwg = build_drawing(
            self._part, model=self._features, decorations=self._decorations(), **self._opts
        )
        # Add each declared table, uniquifying its name against everything already on the sheet
        # (feature annotations + earlier tables) so a table NEVER silently overwrites another
        # object via dwg.add (#493 review). A collision with an explicit name is warned; a table
        # that doesn't fit still records `table_dropped` lint inside add_table.
        used = set(dwg._named)
        for t in self._tables:
            name = t["name"]
            if name in used:
                base, k = name, 1
                while f"{base}_{k}" in used:
                    k += 1
                name = f"{base}_{k}"
                warnings.warn(
                    f"table name {t['name']!r} is already taken — placed as {name!r}", stacklevel=2
                )
            placed = dwg.add_table(
                t["rows"], prefer=t["prefer"], name=name, block_cols=t["block_cols"]
            )
            if placed is not None:  # a dropped table (didn't fit) frees its name (#493 review)
                used.add(name)
        return dwg

    def export(self, stem=None):
        """Build and export the drawing (SVG + DXF). *stem* defaults to the drawing
        number, lower-cased."""
        stem = stem or self._opts["out"] or self._opts["number"].lower()
        return self.build().export(stem)
