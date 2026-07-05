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

**Phase 1 scope (this module):** the *feature-declaration* surface over the
renderers the engine has today — dimensions, ⌀ callouts, holes (through / blind),
turned steps, slots, patterns, the overall envelope, and the auto section. The
richer aspect verbs from the #445 vision that need *new* rendering — ``.fit`` /
``.tolerance`` (toleranced dims), ``.thread``, ``.finish`` (surface symbols) and
``control(...)`` (GD&T) — are Phase 2 (roadmap #446) and are deliberately **not**
stubbed here, so the surface only exposes what actually draws.

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


class _Hole:
    """A fluent handle for one declared hole — the only feature with a Phase-1 aspect
    (through vs blind, which changes the callout)."""

    def __init__(self, sheet: Sheet, index: int) -> None:
        self._sheet = sheet
        self._i = index

    def through(self) -> _Hole:
        """A through hole (the default) — ⌀ only."""
        return self._set(through=True, depth=None)

    def depth(self, d: float) -> _Hole:
        """A blind hole *d* mm deep — adds a depth callout."""
        return self._set(through=False, depth=d)

    def _set(self, **kw) -> _Hole:
        self._sheet._features[self._i] = replace(self._sheet._features[self._i], **kw)
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

    def diameter(self, obj=None, **kw) -> Sheet:
        """Declare an external cylindrical diameter (a boss / OD) — the ⌀ is read off the
        object. The turned/external ⌀-callout verb of the #445 vision."""
        self._features.append(_boss(obj, **kw))
        return self

    def boss(self, obj=None, **kw) -> Sheet:
        """Alias of :meth:`diameter` — an external cylindrical boss / OD."""
        return self.diameter(obj, **kw)

    def step(self, obj=None, **kw) -> Sheet:
        """Declare one axial segment of a turned profile (its OD + length). A model with
        any step renders as a turned part."""
        self._features.append(_step(obj, **kw))
        return self

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
        return build_drawing(self._part, model=self._features, **self._opts)

    def export(self, stem=None):
        """Build and export the drawing (SVG + DXF). *stem* defaults to the drawing
        number, lower-cased."""
        stem = stem or self._opts["out"] or self._opts["number"].lower()
        return self.build().export(stem)
