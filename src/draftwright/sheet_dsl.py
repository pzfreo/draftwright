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
**``.tolerance``** aspect (a ± / limit tolerance on a diameter, a step, or a hole
bore). The remaining #445 aspect verbs that still need new rendering — ``.fit``
(fit-class → ISO 286 deviation), ``.thread``, ``.finish`` (surface symbols) and
``control(...)`` (GD&T) — are the later Phase-2 items (roadmap #446) and are
deliberately **not** stubbed here, so the surface only exposes what actually draws.

**Hybrid.** :meth:`Sheet.from_part` seeds the declared set from *detection*, so you
can start from the detected model and override specific features (declaration is for
where you know better than detection, not everywhere — ADR 0011 §3).
"""

from __future__ import annotations

from dataclasses import replace

from draftwright.builder import build_drawing
from draftwright.model import boss as _boss
from draftwright.model import envelope as _envelope
from draftwright.model import hole as _hole
from draftwright.model import pattern as _pattern
from draftwright.model import slot as _slot
from draftwright.model import step as _step


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


class Sheet:
    """Reference features, declare their drawing aspects, export.

    Each declaration method mirrors a :mod:`draftwright.model` constructor: pass the
    build123d object to read its geometry, or explicit values. :meth:`hole` returns a
    chainable :class:`_Hole` (``.through()`` / ``.depth()``); the others return the
    ``Sheet`` so declarations can chain. :meth:`build` / :meth:`export` hand the declared
    features to the engine with detection skipped.
    """

    def __init__(self, part, *, title=None, number="DWG-001", scale=None, page=None, out=None):
        self._part = part
        self._features: list = []
        # P2a ± tolerances, keyed by (feature index, ParamKind) so a handle survives a later
        # feature replacement (e.g. hole().depth()); materialized to (feature, kind) at build.
        self._tolerances: dict = {}
        self._opts = dict(
            title=title, number=number, scale=_parse_scale(scale), page=page, out=out
        )

    @classmethod
    def from_part(cls, part, **opts) -> Sheet:
        """Seed the declared set from *detection* (the hybrid mode, ADR 0011 §3): start
        from the model the detector recovers, then override specific features (edit the
        list via :attr:`features`, or re-declare) before :meth:`build`."""
        sheet = cls(part, **opts)
        sheet._features = list(build_drawing(part).model().features)
        return sheet

    # -- feature declaration --------------------------------------------------

    def add(self, feature) -> Sheet:
        """Append a pre-built IR :class:`~draftwright.model.Feature` (escape hatch for
        the constructors this façade does not surface directly, e.g. PMI)."""
        self._features.append(feature)
        return self

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

    # -- inspection / output --------------------------------------------------

    @property
    def features(self) -> list:
        """The declared IR features (mutable — override or drop before :meth:`build`)."""
        return self._features

    def model(self):
        """The IR the engine will draw (detection skipped) — for inspection."""
        return self.build().model()

    def build(self):
        """Build the :class:`~draftwright.drawing.Drawing` — detection skipped; only the
        declared features are drawn."""
        # Materialize the index-keyed tolerances against the final features (a handle may
        # have been recorded before a later .depth()/… replaced the feature) → the
        # (feature, kind) decoration map the planner reads (P2a).
        decorations = {
            (self._features[i], kind): tol for (i, kind), tol in self._tolerances.items()
        }
        return build_drawing(
            self._part, model=self._features, decorations=decorations, **self._opts
        )

    def export(self, stem=None):
        """Build and export the drawing (SVG + DXF). *stem* defaults to the drawing
        number, lower-cased."""
        stem = stem or self._opts["out"] or self._opts["number"].lower()
        return self.build().export(stem)
