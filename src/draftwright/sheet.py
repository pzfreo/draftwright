"""sheet — the fluent feature-referencing drawing surface (ADR 0011, #445/#446).

(Renamed from ``sheet_dsl.py`` 2026-07-15, #640: ADR 0001 decided *against* an
editable DSL — this is a fluent Python facade, so the old name contradicted the
project's founding decision; it now owns the natural name. The layout engine
that previously held ``sheet.py`` is ``compose.py``.)

Reference the build123d objects you built, declare the drawing aspects they need,
export. Geometry supplies the size (⌀ read off the object); you supply only the
intent. Built on the ``model=`` seam (:func:`draftwright.build_drawing`), so
detection is skipped. A build must also say where its dimensions come from (ADR
0016); authored is the recommended source here, and `auto_dimensions()` is soft
deprecated (#1043)::

    sheet = Sheet(part, title="PLATE", number="DWG-001")
    env = sheet.envelope()
    bore = sheet.hole(h1)
    sheet.hole(h2).depth(5)          # a blind hole — adds a depth callout
    sheet.diameter(boss_cyl)

    sheet.authored_dimensions()      # THIS is the complete set (ADR 0016)
    sheet.dimension(env, "width.length")
    sheet.dimension(bore, "bore.diameter")

    sheet.export("plate")

**Scope (this module):** the *feature-declaration* surface over the renderers the
engine has today — dimensions, ⌀ callouts, holes (through / blind), circular and polygonal
bosses, turned steps, slots, patterns, the overall envelope, the declaration-only external-spur-
gear requirements table, and the auto section — plus the P2a
**``.tolerance``** (a ± / limit tolerance on a diameter, a step, or a hole bore) and
**``.fit``** (fit-class → ISO 286 deviation, P2a.2) aspects, the P2c GD&T side-layer
(**``.finish``** surface symbols, **``sheet.datum``** feature symbols, and
**``sheet.control(...)``** feature control frames — all 14 ISO 1101 characteristics, ADR 0011
#479 — which derive their target view/strip from the referenced feature or planar face), and
**``sheet.table()``** / **``sheet.notes()``** corner-block tables (notes / revision / BOM /
schedule, over the engine's auto-placed ``Drawing.add_table``, #488), and **``sheet.note()``** /
**``.note()``** anchored free-text manufacturing-note leaders (``DEBURR``, chip-relief,
knurl — the shop callouts detection can't infer, placed via the GD&T corridor machinery,
#488), and the structured **``.thread``** aspect (#764/#445 — a tap/thread spec folded onto
the hole's own compound callout, so it round-trips and ``.thread(...).finish(...)`` gives
Ra-on-thread; a declaration-only aspect, no recogniser).

**Hybrid.** :meth:`Sheet.from_part` seeds the declared set from *detection*, so you
can start from the detected model and override specific features (declaration is for
where you know better than detection, not everywhere — ADR 0011 §3); :meth:`Sheet.of`
returns a fluent handle onto one of those generated features (by object, index, or the
feature itself) so you can ``.fit(...)`` / ``.tolerance(...)`` it without re-declaring (#463).
"""

from __future__ import annotations

import inspect
import math
import warnings
from collections.abc import MutableSequence
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from draftwright._geometry import _solids_body
from draftwright._warnings import SoftDeprecationWarning
from draftwright.builder import _coerce_model, build_drawing, detect_part_model
from draftwright.compose import _est_table_size
from draftwright.fits import fit_class
from draftwright.model import DimensionParameterId, Feature
from draftwright.model import boss as _boss
from draftwright.model import chamfer as _chamfer
from draftwright.model import channel as _channel
from draftwright.model import control_frame as _declare_control
from draftwright.model import datum as _declare_datum
from draftwright.model import double_d_bore as _double_d_bore
from draftwright.model import envelope as _envelope
from draftwright.model import external_spur_gear as _external_spur_gear
from draftwright.model import fillet as _fillet
from draftwright.model import finish as _declare_finish
from draftwright.model import flat as _flat
from draftwright.model import groove as _groove
from draftwright.model import hole as _hole
from draftwright.model import measured_dimension as _measured_dimension
from draftwright.model import note as _declare_note
from draftwright.model import pad as _pad
from draftwright.model import pattern as _pattern
from draftwright.model import plate as _plate
from draftwright.model import pocket as _pocket
from draftwright.model import pocket_pattern as _pocket_pattern
from draftwright.model import polygonal_boss as _polygonal_boss
from draftwright.model import polygonal_stock as _polygonal_stock
from draftwright.model import rotational as _rotational
from draftwright.model import slot as _slot
from draftwright.model import slot_pattern as _slot_pattern
from draftwright.model import step as _step
from draftwright.model import step_level as _step_level
from draftwright.model.declare import (
    _norm_axis,
    _read_cylinder,
    _require_csink,
    _require_positive,
    gdt_target,
)
from draftwright.model.declare import read_bore_step as _read_bore_step
from draftwright.model.declare import read_countersink as _read_countersink
from draftwright.model.ir import RequestedDimension, ToleranceDecoration
from draftwright.model.planner import LOCATION_ROLE as _LOCATION_ROLE
from draftwright.model.planner import location_role as _location_role
from draftwright.view_plan import (
    ConstraintSource,
    ViewConstraint,
    ViewConstraints,
    ViewPin,
    ViewPlanIncomplete,
    ViewRelation,
    ViewSpec,
)

#: "Not supplied" for `Sheet.dimension`'s positional parameters. They cannot simply be
#: required: a keyword-only legacy call has to reach the removal message rather than die on
#: "missing 2 required positional arguments" (#720).
_UNSET = object()


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


def _tolerance_decoration(lo, hi, *, source, source_ids):
    """Keep ordinary Sheet tolerances source-free; preserve imported provenance explicitly."""
    value = _tol_value(lo, hi)
    ids = tuple(dict.fromkeys(str(source_id) for source_id in source_ids if source_id))
    if source is None:
        if ids:
            raise ValueError("tolerance() source_ids require source=")
        return value
    source = str(source).strip()
    if not source:
        raise ValueError("tolerance() source must be a non-empty string")
    return ToleranceDecoration(value=value, source=source, source_ids=ids)


class _FeatureView(MutableSequence):
    """The public ``Sheet.features`` list — a view over ``(token, feature)`` entries.

    Handles address their feature by **token**, not position (#908), and this view is
    what keeps the two in step. Every mutation moves entries, so the token travels with
    the feature it names:

    - ``append`` mints a new token — a declaration;
    - ``features[i] = f`` mints a **new** token: assignment cannot express "move this
      feature here", so inheriting the slot's identity would silently hand every
      reference to whatever was assigned. Stale references fail loudly instead. The
      identity-preserving rebuild a size verb needs is :meth:`_rebind`;
    - ``reverse`` / ``sort`` / ``insert`` reorder entries, so a handle simply finds its
      feature at the new position rather than silently naming a neighbour;
    - ``del`` drops the token, so a handle for a removed feature raises when used.

    Before this, everything addressed by index into a plain list, and seven review rounds
    on #872 found seven ways for a long-lived reference — a tolerance, a GD&T origin, a
    section, a dimension intent — to end up pointing at the wrong feature. Each fix
    detected one route and opened another. Carrying identity makes the class impossible
    instead of detectable.
    """

    __slots__ = ("_entries", "_next_token")

    def __init__(self, entries: list) -> None:
        self._entries = entries
        # A plain int, not `itertools.count`: counters lose pickle/copy support in
        # Python 3.14 and this package supports >=3.11, so a Sheet carrying one would
        # stop being copyable. Per-sheet rather than global because tokens appear in
        # internal keys and this project holds output to be deterministic — a
        # process-wide counter would make those keys depend on how many other sheets
        # happened to be built first.
        self._next_token = 0

    def _mint(self) -> int:
        token = self._next_token
        self._next_token += 1
        return token

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [e[1] for e in self._entries[i]]
        return self._entries[i][1]

    def __setitem__(self, i, value) -> None:
        # Public assignment always mints a NEW token, scalar or slice.
        #
        # This is the distinction an earlier draft got wrong. Keeping the destination
        # slot's token is right for the INTERNAL rebuild a size verb does — `.depth()`
        # replaces the frozen dataclass with an updated copy of the same feature — but on
        # the public view, assignment cannot tell "move this feature here" from "put a
        # different feature here". Preserving identity across it silently transferred
        # every reference (handles, tolerances, GD&T origins, sections, intents) onto
        # whatever was assigned, which is the exact bug this class exists to remove:
        #
        #     features[0], features[1] = features[1], features[0]   # a tuple swap
        #     features[:] = features[::-1]                          # a slice reversal
        #
        # both of which move VALUES between slots. Minting fresh tokens makes those
        # references fail loudly instead. To reorder while keeping identity, use
        # `reverse()` / `sort()`, which move whole entries. Internal rebuilding goes
        # through `_rebind`, which is the only path that preserves a token.
        if isinstance(i, slice):
            self._entries[i] = [(self._mint(), f) for f in value]
            return
        self._entries[i] = (self._mint(), value)

    def _rebind(self, index: int, feature) -> None:
        """Replace the feature at *index* KEEPING its token — the same feature, rebuilt.

        The only identity-preserving write. Used by the size verbs, whose frozen
        dataclasses are replaced wholesale on every `.depth()` / `.cbore()` / `.thread()`.
        """
        self._entries[index] = (self._entries[index][0], feature)

    def __delitem__(self, i) -> None:
        del self._entries[i]

    def __len__(self) -> int:
        return len(self._entries)

    def insert(self, i, value) -> None:
        self._entries.insert(i, (self._mint(), value))

    # Reordering must move ENTRIES, not values. `MutableSequence` implements `reverse`
    # in terms of `__setitem__`, which here keeps each slot's token — right for a size
    # verb replacing a feature in place, wrong for a reorder, where it would swap the
    # features and leave every token pointing at its old position. Overriding both is
    # what makes a reorder transparent to handles rather than silently retargeting them.
    def reverse(self) -> None:
        self._entries.reverse()

    def sort(self, *, key=None, reverse: bool = False) -> None:
        self._entries.sort(
            key=(lambda e: e[1]) if key is None else (lambda e: key(e[1])), reverse=reverse
        )

    def __repr__(self) -> str:
        return repr([e[1] for e in self._entries])

    def __eq__(self, other) -> bool:
        return [e[1] for e in self._entries] == list(other)


class _Nameable:
    """`dimension_ids()` for a declared-feature handle — the measurements it can be asked for.

    Named for what it returns. An earlier cut called this `roles()` and the alias below
    `DimensionRole`, which described neither: both carry parameter IDS, and the bare role is
    the deprecated family spelling they deliberately exclude (#965 review). Both names were
    new, so renaming cost nothing here and would have been a compat burden a release later.
    The `role` PARAMETER keeps its name — it predates this change, so moving it would break
    keyword callers; it exits with the other 0.4.0 renames (#720, #966).

    The runtime answer to "what can I write here", and the reason the generated script can
    point at something that works (#963/#965 review). A handle is not an IR `Feature`, so
    `feature.parameters()` — the route the header first advertised — raises on one; the
    working alternative went through a private index. Returns the CANONICAL spellings, so
    what it lists is what `dimension()` wants: the parameter id for every measurement —
    discriminated ones included, since their id carries the variant and resolves on its own —
    plus `location` when this feature is eligible for one (which the planner decides, so it
    cannot be read off the type).

    Every entry must resolve. That is the whole contract, and it was broken once: this
    listed a bare `"grid_pitch"` for a grid pattern, which `dimension()` then refused as
    ambiguous, while the guard test passed because it reconstructed the expected answer
    instead of calling this method (#965 review).
    """

    # Declared, not defined: every handle already implements these — `_i` as a token-resolved
    # property — so a body here would be unreachable code the mixin does not need. Under
    # TYPE_CHECKING because a bare `_i: int` is a WRITEABLE attribute, which a read-only
    # property may not override.
    _sheet: Sheet

    if TYPE_CHECKING:

        @property
        def _i(self) -> int: ...

    def dimension_ids(self) -> tuple[str, ...]:
        feature = self._sheet.features[self._i]
        names = {p.parameter_id for p in feature.parameters()}
        if _location_role(feature) is not None:
            names.add(_LOCATION_ROLE)
        return tuple(sorted(names))


class _Hole(_Nameable):
    """A fluent handle for one declared hole — through vs blind (which changes the callout),
    and the P2a ± tolerance on its bore ⌀."""

    def __init__(self, sheet: Sheet, index: int) -> None:
        self._sheet = sheet
        self._token = sheet._token_at(index)

    @property
    def _i(self) -> int:
        """This handle's CURRENT index — resolved from its token, so the handle follows
        its feature through a reorder rather than naming whatever took its slot (#908)."""
        return self._sheet._index_of_token(self._token)

    def through(self) -> _Hole:
        """A through hole (the default) — ⌀ only."""
        return self._set(through=True, depth=None)

    def depth(self, d: float) -> _Hole:
        """A blind hole *d* mm deep — adds a depth callout."""
        return self._set(through=False, depth=d)

    def tolerance(
        self,
        lo: float,
        hi: float | None = None,
        *,
        source: str | None = None,
        source_ids: tuple[str, ...] = (),
    ) -> _Hole:
        """A ± tolerance on the bore ⌀: symmetric ``.tolerance(0.05)`` (→ ``±0.05``) or a
        limit pair ``.tolerance(0.0, 0.1)`` (→ ``+0.1 -0.0``). Generated import scripts use
        ``source`` / ``source_ids`` to retain external requirement provenance."""
        self._sheet._tolerances[(self._token, "diameter")] = _tolerance_decoration(
            lo, hi, source=source, source_ids=source_ids
        )
        return self

    def fit(self, code: str, *, show: str = "class") -> _Hole:
        """An ISO 286 fit class on the bore ⌀ — ``.fit("H7")`` renders ``ø8 H7`` (the class,
        default) or, with ``show="deviation"``, the signed deviations ``ø8 +0.015/0`` resolved
        for the bore's nominal ⌀. Raises for a class/size outside the built-in table (#29)."""
        self._sheet._tolerances[(self._token, "diameter")] = fit_class(
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

    def countersink(
        self, obj=None, *, major: float | None = None, angle: float | None = None
    ) -> _Hole:
        """A countersink on this hole (a flat-head screw seat, #575). ``.countersink(cone_tool)``
        reads the major ⌀ + included angle off the build123d ``Cone`` you subtracted, or explicit
        ``.countersink(major=14, angle=90)``. Renders ``⌵ Ø.. × ..°`` on the callout. An object
        supplies defaults; explicit kwargs override (#451)."""
        if obj is not None:
            r_major, r_angle = _read_countersink(obj)
            major = r_major if major is None else major
            angle = r_angle if angle is None else angle
        if major is None or angle is None:
            raise ValueError("countersink() needs a cone tool, or explicit major= and angle=")
        _require_csink("countersink", (major, angle))
        return self._set(csink=(major, angle))

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

    def thread(self, spec: str) -> _Hole:
        """A thread/tap spec folded onto this hole's callout (#764). ``.thread("M3x0.5")``
        renders the tap/thread on the bore leader (e.g. ``ø2.5 THRU M3x0.5``) — a structured
        aspect that round-trips, so ``.thread(...).finish(...)`` gives Ra-on-thread. A
        declaration-only aspect (threads are cosmetic, not modelled geometry — no recogniser)."""
        if not (isinstance(spec, str) and spec.strip()):
            raise ValueError('thread() needs a non-empty spec string, e.g. "M3x0.5"')
        return self._set(thread=spec.strip())

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
        updated = replace(self._sheet._features[self._i], **kw)
        self._sheet._replace_feature(self._i, updated)
        return self


class _Dim(_Nameable):
    """A fluent handle for a declared dimension-bearing feature (a diameter / boss OD, or a
    turned step), carrying the P2a ``.tolerance`` aspect. ``default_kind`` is the parameter a
    bare ``.tolerance(...)`` targets — ``"diameter"`` for an OD, ``"length"`` for a step."""

    def __init__(self, sheet: Sheet, index: int, default_kind: str) -> None:
        self._sheet = sheet
        self._token = sheet._token_at(index)
        self._kind = default_kind

    @property
    def _i(self) -> int:
        """See :attr:`_Hole._i` — token-resolved, not stored (#908)."""
        return self._sheet._index_of_token(self._token)

    def tolerance(
        self,
        lo: float,
        hi: float | None = None,
        *,
        on: str | None = None,
        source: str | None = None,
        source_ids: tuple[str, ...] = (),
    ) -> _Dim:
        """A ± tolerance on this dimension: symmetric ``.tolerance(0.05)`` (→ ``±0.05``) or a
        limit pair ``.tolerance(0.0, 0.1)`` (→ ``+0.1 -0.0``). ``on`` picks the parameter for
        a multi-dim feature — a step's ``"length"`` (default) vs its ``"diameter"`` (OD).
        ``source`` / ``source_ids`` retain provenance on generated imported requirements."""
        self._sheet._tolerances[(self._token, on or self._kind)] = _tolerance_decoration(
            lo, hi, source=source, source_ids=source_ids
        )
        return self

    def fit(self, code: str, *, show: str = "class") -> _Dim:
        """An ISO 286 fit class on this feature's ⌀ (always the diameter — a fit is diametral,
        so a step's fit is on its OD, not its length). ``.fit("h6")`` renders ``ø12 h6`` (the
        class, default) or ``show="deviation"`` the signed deviations ``ø12 0/-0.011`` resolved
        for the nominal ⌀. Raises for a class/size outside the built-in table (#29)."""
        self._sheet._tolerances[(self._token, "diameter")] = fit_class(
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

    def knurl(
        self, pitch, pattern: str = "STRAIGHT", *, view: str | None = None, side: str | None = None
    ) -> _Dim:
        """A knurl callout on this diameter (#765) — ``diameter(shaft).knurl("0.8")`` →
        ``KNURL 0.8 STRAIGHT``, or ``.knurl("0.8", "DIAMOND")``. Named sugar over :meth:`note`
        (canonical formatting + discoverability): knurl is a text callout on a leader, not
        modelled geometry, so no IR/render — it flows through the same note path. ``view``/
        ``side`` override the derived strip."""
        text = f"KNURL {pitch} {pattern}".strip()
        self._sheet._gdt_note(text, self._i, view=view, side=side)
        return self

    def thread(self, spec: str) -> _Dim:
        """An EXTERNAL thread spec appended to this OD's ⌀ callout (#859) — the turned analog of
        :meth:`_Hole.thread`. ``step(shaft).thread("M3x0.5")`` renders ``ø3 M3x0.5``; a structured
        aspect on the feature, so ``.thread(...).finish(...)`` gives Ra-on-thread. Declaration-only
        (threads are cosmetic, rarely modelled as geometry — no recogniser)."""
        if not (isinstance(spec, str) and spec.strip()):
            raise ValueError('thread() needs a non-empty spec string, e.g. "M3x0.5"')
        return self._set(thread=spec.strip())

    def _set(self, **kw) -> _Dim:
        updated = replace(self._sheet._features[self._i], **kw)
        self._sheet._replace_feature(self._i, updated)
        return self


class DimensionIntent:
    """The handle :meth:`Sheet.add_dimension` returns (ADR 0016).

    It is **not** a placement handle: it exposes no coordinate, no strip and no tier.
    What it carries is the dimension's semantic identity — *a dimension line references;
    the engine places*.

    ADR 0012's ``.pin()`` / ``.priority()`` are deliberately absent for now. The engine
    already has two spellings of "keep this put" at different layers, and adding a third
    that no renderer consumes would ship a chainable verb doing nothing. This handle is
    the extension point for them once that concept is converged.

    Every other attribute forwards to the owning :class:`Sheet`, so the declare-then-chain
    contract holds (``sheet.add_dimension(bore, "depth").hole(...)``) despite this
    returning a handle rather than the sheet — the same rule :class:`_Params` follows.
    """

    def __init__(self, sheet: Sheet, entry: dict) -> None:
        self._sheet = sheet
        self._entry = entry

    def __getattr__(self, name):  # declare-then-chain: forward to the sheet
        return getattr(self._sheet, name)


class _Params(_Nameable):
    """A fluent handle for a declared MULTI-parameter feature — a pocket
    (width/length/depth), slot (width/length) or envelope (width/height/depth) — whose
    parameters share a KIND but have distinct ROLES. ``.tolerance(..., on=role)``
    tolerances ONE parameter by role (#746: e.g. a pocket's depth without touching its
    width/length); a bare ``.tolerance(...)`` (no ``on``) folds onto every parameter of
    the feature (the back-compat kind-keyed form). ``on`` accepts the full role
    (``"pocket_depth"``) or its short tail (``"depth"``).

    Every other attribute forwards to the owning :class:`Sheet`, so these verbs stay
    chainable (``sheet.pocket(...).hole(...).build()``) despite returning a handle —
    the module's declare-then-chain contract holds (#807 review)."""

    def __init__(self, sheet: Sheet, index: int) -> None:
        self._sheet = sheet
        self._token = sheet._token_at(index)

    @property
    def _i(self) -> int:
        """This handle's CURRENT index — resolved from its token, so the handle follows
        its feature through a reorder rather than naming whatever took its slot (#908)."""
        return self._sheet._index_of_token(self._token)

    def __getattr__(self, name: str):
        # Only reached for attributes _Params doesn't define (every Sheet verb): forward
        # to the owning sheet so the fluent chain is unbroken. Guard the two real fields:
        # if they aren't set yet (an instance built WITHOUT __init__ — copy/pickle), raise
        # rather than recurse forever resolving self._sheet (#807 review).
        if name in ("_sheet", "_token"):
            raise AttributeError(name)
        return getattr(self._sheet, name)

    def _roles(self) -> dict:
        # {role: kind} for the feature's dimensioned params (skip the locations).
        return {
            p.role: p.kind
            for p in self._sheet._features[self._i].parameters()
            if p.kind != "location"
        }

    def tolerance(
        self,
        lo: float,
        hi: float | None = None,
        *,
        on: str | None = None,
        source: str | None = None,
        source_ids: tuple[str, ...] = (),
    ) -> _Params:
        """A ± tolerance: symmetric ``.tolerance(0.05)`` (→ ``±0.05``) or a limit pair
        ``.tolerance(0.0, 0.1)`` (→ ``+0.1 -0.0``). ``on`` targets ONE parameter by role
        (``on="depth"`` on a pocket → a role-keyed decoration); omit ``on`` to tolerance
        every parameter of the feature alike (the kind-keyed form). ``source`` /
        ``source_ids`` retain provenance on generated imported requirements."""
        val = _tolerance_decoration(lo, hi, source=source, source_ids=source_ids)
        roles = self._roles()
        if not roles:
            # A feature with no dimensioned parameters has nothing to tolerance, and
            # accepting the call would drop a drafting instruction in silence — the failure
            # this codebase ranks below a visible raise (#630/#631). Reachable since #922
            # made every declaration verb hand back a handle: `add(PmiFeature(...))` and
            # `measured_dimension(...)` both produce parameterless features, and before that
            # they returned the Sheet so `.tolerance()` could not be called at all (Codex
            # review of #931).
            kind = self._sheet._features[self._i].kind
            extra = (
                " — a measured dimension carries its own tolerance: pass upper_tol=/lower_tol="
                " to measured_dimension()"
                if kind == "authored_dimension"
                else ""
            )
            raise ValueError(
                f"tolerance(): a {kind} exposes no dimensioned parameter to tolerance{extra}"
            )
        if on is None:
            # A whole-feature tolerance supersedes any earlier per-role override on this
            # feature — bare means "all alike", so it is order-independent (#807 review):
            # drop this feature's role-keyed (3-tuple) entries, then set the kind keys.
            for key in [k for k in self._sheet._tolerances if len(k) == 3 and k[0] == self._token]:
                del self._sheet._tolerances[key]
            for kind in set(roles.values()):
                self._sheet._tolerances[(self._token, kind)] = val
            return self
        cands = [r for r in roles if r == on or r.rsplit("_", 1)[-1] == on]
        if len(cands) != 1:
            raise ValueError(
                f"on={on!r} must name one parameter role of this feature; "
                f"choose from {sorted(roles)}"
            )
        role = cands[0]
        self._sheet._tolerances[(self._token, roles[role], role)] = val
        return self

    def note(self, text, ref=None, *, view: str | None = None, side: str | None = None) -> _Params:
        """A free-text manufacturing note (#841). With no *ref* the note anchors to THIS feature —
        ``sheet.slot(...).note("5X OBROUND SLOT")`` — mirroring :meth:`_Hole.note` / :meth:`_Dim.note`
        (previously this raised, because the forwarded ``Sheet.note`` needs a target). An explicit
        *ref* (a face or feature) still forwards to :meth:`Sheet.note`, preserving the handle's
        forwarding contract for ``sheet.slot(...).note("DEBURR", face)``. Returns the handle
        (chainable); ``view``/``side`` override the derived strip."""
        if ref is None:
            self._sheet._gdt_note(text, self._i, view=view, side=side)
        else:
            self._sheet.note(text, ref, view=view, side=side)
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

    def __init__(self, sheet: Sheet, target, src_token, view: str, side: str) -> None:
        self._sheet = sheet
        self._target = target
        # A token, not an index: this builder outlives the `control()` call that made it, so a
        # reorder between `control(bore)` and `.position(0.1)` must not retarget it (#908).
        self._src = src_token
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
        """Apply straightness tolerance *tol*; no datum reference is permitted."""
        return self._add("straightness", tol, modifier=modifier)

    def flatness(self, tol, *, modifier=None) -> _Control:
        """Apply flatness tolerance *tol*; no datum reference is permitted."""
        return self._add("flatness", tol, modifier=modifier)

    def circularity(self, tol, *, modifier=None) -> _Control:
        """Apply circularity tolerance *tol*; no datum reference is permitted."""
        return self._add("circularity", tol, modifier=modifier)

    def cylindricity(self, tol, *, modifier=None) -> _Control:
        """Apply cylindricity tolerance *tol*; no datum reference is permitted."""
        return self._add("cylindricity", tol, modifier=modifier)

    # Profile ------------------------------------------------------------------------------
    def profile_line(self, tol, *, to=None, modifier=None) -> _Control:
        """Apply line-profile tolerance *tol*, optionally relative to datum(s) *to*."""
        return self._add("profile_line", tol, to=to, modifier=modifier)

    def profile_surface(self, tol, *, to=None, modifier=None) -> _Control:
        """Apply surface-profile tolerance *tol*, optionally relative to datum(s) *to*."""
        return self._add("profile_surface", tol, to=to, modifier=modifier)

    # Orientation --------------------------------------------------------------------------
    def angularity(self, tol, *, to=None, modifier=None) -> _Control:
        """Apply angularity tolerance *tol* relative to datum(s) *to*."""
        return self._add("angularity", tol, to=to, modifier=modifier)

    def perpendicularity(self, tol, *, to=None, modifier=None) -> _Control:
        """Apply perpendicularity tolerance *tol* relative to datum(s) *to*."""
        return self._add("perpendicularity", tol, to=to, modifier=modifier)

    def parallelism(self, tol, *, to=None, modifier=None) -> _Control:
        """Apply parallelism tolerance *tol* relative to datum(s) *to*."""
        return self._add("parallelism", tol, to=to, modifier=modifier)

    # Location (a position/concentricity zone is diametral by default) ---------------------
    def position(self, tol, *, to=None, diameter=True, modifier=None) -> _Control:
        """Apply position tolerance *tol* relative to *to*; *diameter* selects the zone."""
        return self._add("position", tol, to=to, diameter=diameter, modifier=modifier)

    def concentricity(self, tol, *, to=None, diameter=True, modifier=None) -> _Control:
        """Apply concentricity tolerance *tol* relative to *to*; *diameter* selects the zone."""
        return self._add("concentricity", tol, to=to, diameter=diameter, modifier=modifier)

    def symmetry(self, tol, *, to=None, modifier=None) -> _Control:
        """Apply symmetry tolerance *tol* relative to datum(s) *to*."""
        return self._add("symmetry", tol, to=to, modifier=modifier)

    # Runout -------------------------------------------------------------------------------
    def circular_runout(self, tol, *, to=None, modifier=None) -> _Control:
        """Apply circular-runout tolerance *tol* relative to datum(s) *to*."""
        return self._add("circular_runout", tol, to=to, modifier=modifier)

    def total_runout(self, tol, *, to=None, modifier=None) -> _Control:
        """Apply total-runout tolerance *tol* relative to datum(s) *to*."""
        return self._add("total_runout", tol, to=to, modifier=modifier)


def _constraint_source() -> ConstraintSource:
    """Return the first caller frame outside this façade module."""

    frame = inspect.currentframe()
    try:
        caller = frame.f_back if frame is not None else None
        while caller is not None and caller.f_code.co_filename == __file__:
            caller = caller.f_back
        if caller is None:
            return ConstraintSource("<unknown>", 0)
        return ConstraintSource(caller.f_code.co_filename, caller.f_lineno)
    finally:
        del frame


class _View:
    """A fluent handle for one semantic whole-view constraint.

    Its layout verbs relate or pin the complete view block.  They never address a feature
    annotation, so ADR 0014 remains the sole owner of dimension/callout/GD&T coordinates.
    """

    def __init__(self, sheet: Sheet, bucket: str, index: int) -> None:
        self._sheet = sheet
        self._bucket = bucket
        self._index = index

    @property
    def _record(self) -> dict:
        return cast(dict, getattr(self._sheet, self._bucket)[self._index])

    @property
    def name(self) -> str:
        return cast(str, self._record["name"])

    def _relation(self, relation: str, other, gap=None) -> _View:
        other_name = other.name if isinstance(other, _View) else str(other)
        self._sheet._view_relations.append(
            ViewRelation(
                self.name,
                relation,
                other_name,
                None if gap is None else float(gap),
                _constraint_source(),
            )
        )
        return self

    def left_of(self, other, *, gap=None) -> _View:
        """Keep this whole view block left of *other*."""
        return self._relation("left_of", other, gap)

    def right_of(self, other, *, gap=None) -> _View:
        """Keep this whole view block right of *other*."""
        return self._relation("right_of", other, gap)

    def above(self, other, *, gap=None) -> _View:
        """Keep this whole view block above *other*."""
        return self._relation("above", other, gap)

    def below(self, other, *, gap=None) -> _View:
        """Keep this whole view block below *other*."""
        return self._relation("below", other, gap)

    def align_x(self, other) -> _View:
        """Align this view's projection origin horizontally with *other*."""
        return self._relation("align_x", other)

    def align_y(self, other) -> _View:
        """Align this view's projection origin vertically with *other*."""
        return self._relation("align_y", other)

    def pin(self, at) -> _View:
        """Pin a principal view's projection origin at ``(x, y)`` page millimetres."""
        if self._record["kind"] != "principal":
            raise ValueError(
                "projection-origin pins currently support principal front/plan/side views only; "
                f"cannot pin {self.name!r}"
            )
        values = tuple(float(value) for value in at)
        if len(values) != 2:
            raise ValueError(f"view pin needs two page coordinates, got {at!r}")
        point = (values[0], values[1])
        self._sheet._view_pins = [pin for pin in self._sheet._view_pins if pin.view != self.name]
        self._sheet._view_pins.append(ViewPin(self.name, point, _constraint_source()))
        return self

    def scale(self, factor) -> _View:
        """Set an independent detail/orientation scale; principal views reject it."""
        record = self._record
        if record["kind"] == "principal":
            raise ValueError(
                f"principal view {self.name!r} cannot have an independent scale; "
                "front/plan/side share the drawing scale"
            )
        factor = float(factor)
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError(f"view scale factor must be finite and positive, got {factor!r}")
        record["scale_factor"] = factor
        return self


class Sheet:
    """Reference features, declare their drawing aspects, export.

    Each declaration method mirrors a :mod:`draftwright.model` constructor: pass the
    build123d object to read its geometry, or explicit values. :meth:`hole` returns a
    chainable :class:`_Hole` (``.through()`` / ``.depth()``), :meth:`diameter` / :meth:`step`
    a :class:`_Dim`, for their own aspects; :meth:`pocket` / :meth:`slot` / :meth:`envelope`
    a :class:`_Params` (``.tolerance(on=…)``) which forwards unknown attributes to the
    ``Sheet`` so those verbs still chain to any further declaration (preserving their prior
    return-``Sheet`` behaviour); the remaining verbs return the ``Sheet``. :meth:`build` /
    :meth:`export` hand the declared features to the engine with detection skipped.
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
        scale_policy="fallback",
        page=None,
        out=None,
        material=None,
        date=None,
        revision=None,
        company=None,
        frame=None,
        projection=None,
        zones=None,
        detail_view=None,
    ):
        self._part = part
        # (token, feature) entries — identity, not position (#908). `_features` is the
        # view; handles hold tokens and resolve through it, so a reorder of the public
        # list moves each token with its feature instead of stranding references.
        self._entries: list[tuple[int, object]] = []
        self._features = _FeatureView(self._entries)
        # P2a ± tolerances, keyed by (feature index, ParamKind) so a handle survives a later
        # feature replacement (e.g. hole().depth()); materialized to (feature, kind) at build.
        self._tolerances: dict = {}
        # P2c GD&T provenance: (gdt_feature_token -> source_feature_token). A finish/datum stores
        # its origin by TOKEN, not the object, so a later size verb replacing the source feature
        # (hole().depth()) doesn't strand the link, and a reorder cannot retarget it (#910);
        # materialized to the FINAL object at build.
        self._gdt_src: list = []
        # Corner-block tables (notes / revision / BOM / schedule) — applied at build() via the
        # engine's generic auto-placed Drawing.add_table, AFTER the drawing is built so they sit
        # clear of the views + title block (like the hole table). Each: {rows, prefer, name}.
        self._tables: list = []
        # ADR 0016 augmenting dimension intents (#872), token-keyed for the same reason as
        # `_tolerances`: a handle may be recorded before a later size verb replaces the
        # feature, and a position would then name whatever moved into the slot.
        # Materialized to `RequestedDimension` against the FINAL features at build.
        # Each entry: {"token", "role", "discriminator"}.
        self._added_dimensions: list[dict] = []
        # The COMPLETE authored dimension set (#874/#876) — the other of the model's two
        # dimension sources, mutually exclusive with `_auto_dimensions`. Token-keyed like
        # every other feature reference on this class.
        self._authored: list[dict] = []
        #: Did the script SAY the authored set is its source? `dimension(...)` implies it;
        #: this carries the case with no lines to imply it from (an empty complete set).
        self._authored_source: bool = False
        # Where the dimensions come from, and WHO said so — `None` (nobody has yet),
        # ``"explicit"`` (an `auto_dimensions()` line) or ``"implicit"`` (`from_part`, which
        # chooses on the caller's behalf). MANDATORY unless the sheet authors its own set
        # instead (#874): `_check_dimension_source` refuses a build that names neither, so
        # the drawing never falls back to a source nobody chose.
        #
        # One tri-state rather than a flag plus an "was it explicit" flag: the pair could
        # be set to a combination that means nothing, and clearing the source then took two
        # assignments — which is how the first cut of this broke the identity suite (#921
        # review round 7). Only ``"explicit"`` conflicts with an authored set.
        self._auto_dimensions: str | None = None
        # ADR 0018 authored view input.  These mutable declaration records are private
        # construction state; :attr:`view_constraints` exposes a fresh immutable snapshot so
        # request state cannot be confused with or edited like a ResolvedViewPlan.
        self._principal_view_source: str | None = None
        self._derived_view_source: str | None = None
        self._principal_view_source_at: ConstraintSource | None = None
        self._derived_view_source_at: ConstraintSource | None = None
        self._principal_views: list[dict] = []
        self._added_principal_views: list[dict] = []
        self._derived_views: list[dict] = []
        self._added_derived_views: list[dict] = []
        self._view_relations: list[ViewRelation] = []
        self._view_pins: list[ViewPin] = []
        # A requested section A–A (#841): ``None`` = no request, else a resolver tuple
        # (``kind``, ``payload``) materialized to a cut-plane Y in ``_decorations`` — ``at``
        # a literal Y, ``feature`` a declared-feature index, ``auto`` the part-centre Y.
        self._section: tuple | None = None
        self._opts = dict(
            title=title,
            number=number,
            scale=_parse_scale(scale),
            scale_policy=scale_policy,
            page=page,
            out=out,
        )
        # drawn_by / tolerance (title block, #474) forward to build_drawing only when set, so an
        # unset value keeps build_drawing's own defaults ("" / "ISO 2768-m") rather than None.
        if drawn_by is not None:
            self._opts["drawn_by"] = drawn_by
        if tolerance is not None:
            self._opts["tolerance"] = tolerance
        # Standing ISO 7200 title-block fields (#766) — forward only when set, so an unset
        # value keeps build_drawing's defaults ("" / revision "A").
        for _k, _v in (
            ("material", material),
            ("date", date),
            ("revision", revision),
            ("company", company),
            ("frame", frame),
            ("projection", projection),
            ("zones", zones),
            # The last build option the facade did not forward (#940). It matters now that the
            # Sheet script is the only generated script: the imperative one put a raw
            # `build_drawing(...)` call in the file, so a reader could add any engine kwarg by
            # editing it. Retiring that surface without this would take `detail_view` away from
            # everyone generating a script, which is the capability loss #940's gate forbids.
            ("detail_view", detail_view),
        ):
            if _v is not None:
                self._opts[_k] = _v

    @classmethod
    def from_part(cls, part, **opts) -> Sheet:
        """Seed the declared set from *detection* (the hybrid mode, ADR 0011 §3): start
        from the model the detector recovers, then override specific features (edit the
        list via :attr:`features`, or re-declare) before :meth:`build`.

        This states the **automatic** dimension source (#874), because that is what asking for
        detection means: you have asked for the engine's reading of the part, features and
        dimensions alike. It is not the implicit default the breaking change removed — a
        `Sheet(part)` still has to say — it is `from_part`'s own meaning.

        Because the choice is `from_part`'s rather than a script line's, adding
        `dimension(...)` declarations **overrides** it rather than conflicting: detect the
        features, then declare exactly which of their measurements to draw. That is the
        natural way to take over a detected drawing, and requiring the caller to redeclare
        every feature by hand to reach it would be a poor trade (#921 review round 6). An
        explicit `auto_dimensions()` still conflicts — there the script has said both things.
        """
        sheet = cls(part, **opts)
        sheet._features.extend(detect_part_model(part).features)  # detect only, no render (#453)
        sheet._auto_dimensions = "implicit"
        return sheet

    # -- feature declaration --------------------------------------------------

    def add(self, feature) -> _Params:
        """Append a pre-built IR :class:`~draftwright.model.Feature` (escape hatch for
        the constructors this façade does not surface directly, e.g. PMI).

        Returns a handle, like every declaration verb (#922). It matters here more than it
        looks: the ENVELOPE is emitted through this escape hatch rather than through
        :meth:`envelope`, so while `add` returned the sheet, `sheet.dimension(env, "width")`
        — ADR 0016's own worked example — could not be written against a generated script.
        Naming would have been uniform across the verbs and silently absent for one feature
        in the middle of the file, which is worse than being absent everywhere. A raw
        ``ControlFrame`` or ``DatumRef`` may name a handle as its ``origin``; ``add`` resolves
        and token-binds that provenance exactly like the public GD&T verbs."""
        src_token = None
        if (
            getattr(feature, "kind", None) in ("control_frame", "datum_ref")
            and feature.origin is not None
        ):
            src_token = self._declared_token(feature.origin, verb=f"add() {feature.kind} origin")
            if src_token is not None:
                feature = replace(
                    feature,
                    origin=self._features[self._index_of_token(src_token)],
                )
        self._features.append(feature)
        self._bind_gdt_source(self._token_at(len(self._features) - 1), src_token)
        return _Params(self, len(self._features) - 1)

    #: The keyword set that identifies a call to the pre-rename `dimension(...)`. `kind` alone
    #: would do today, but the referential form may grow keywords of its own, so the test is the
    #: intersection of what only a measured call can supply.
    _MEASURED_KEYWORDS = frozenset({"kind", "value", "label", "dominant_axis", "ref_pts"})

    def dimension(
        self,
        feature=_UNSET,
        role: DimensionParameterId = _UNSET,  # type: ignore[assignment]
        *,
        axis: str | None = None,
        **removed,
    ) -> DimensionIntent:
        """`dimension(feature, role)` — the ADR 0016 referential verb. See
        :meth:`_authored_dimension` for the semantics.

        The signature is the real one again (#720): the transitional call-shape dispatch to
        :meth:`measured_dimension` was removed at 0.4.0, so `dimension` means one thing. That
        restores most of what the `@overload` pair provided — the parameter names and the
        `DimensionIntent` return a caller sees (#963) — without the dual shape.

        One thing it does NOT restore: `**removed` means a type checker accepts any keyword
        rather than rejecting an unknown one (Codex #720 r1). That is the price of catching the
        legacy call at runtime to name its replacement; the alternative is a static error whose
        text is about argument counts. Worth revisiting once the break is old news, at which
        point `**removed` can go and the signature becomes exact.

        ``feature``/``role`` default to a sentinel rather than being required so that a
        keyword-only legacy call (`dimension(kind=…, value=…)`) reaches the message below
        instead of a bare "missing 2 required positional arguments". The old shape never
        appeared in a release, so this refusal is the only notice it gets — a documented
        break (`docs/deprecations.md`).
        """
        if removed:
            legacy = sorted(self._MEASURED_KEYWORDS & set(removed))
            if legacy:
                raise TypeError(
                    f"Sheet.dimension({', '.join(f'{k}=…' for k in legacy)}) was removed at "
                    "0.4.0 (#720) — use Sheet.measured_dimension(...) for a measurement that "
                    "restates a value. `dimension` is the referential verb: it names a feature "
                    "and a parameter id and reads the value off the geometry (ADR 0016)."
                )
            raise TypeError(f"dimension() got unexpected keyword(s) {sorted(removed)}")
        if feature is _UNSET or role is _UNSET:
            raise TypeError("dimension() requires a feature and a parameter id")
        return self._authored_dimension(feature, role, axis=axis)

    def _authored_dimension(
        self, feature, role: DimensionParameterId, *, axis: str | None = None
    ) -> DimensionIntent:
        """`dimension(feature, role)` — declare one member of the COMPLETE authored set.

        Referential, like every ADR 0016 intent: it names a feature and a role and carries no
        number, so the value still comes from the geometry. What distinguishes it from
        :meth:`add_dimension` is not how it addresses a measurement but what naming one MEANS —
        `add_dimension` augments the planner's set, so a measurement you don't name keeps
        whatever the rule set decided; `dimension` declares the set, so a measurement you don't
        name is omitted.

        That is why the source has to be stated (#874): omission is only meaningful inside a set
        declared complete. A script that mixed the two would be saying "everything the planner
        chooses, plus these" and "only these" at once.
        """
        token, target, discriminator, role = self._resolve_measurement(
            feature, role, axis, "dimension"
        )
        # The CANONICAL spelling is stored, not what was typed (#963). Otherwise a generated
        # script's dialect depended on how its source model was authored — mirrored sets wrote
        # parameter ids, hand-authored sets echoed back whatever the author used.
        self._authored.append({"token": token, "role": role, "discriminator": discriminator})
        return DimensionIntent(self, self._authored[-1])

    def measured_dimension(
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
        lower_bound: float | None = None,
        upper_bound: float | None = None,
        source: str = "sheet",
        source_kind: str | None = None,
        source_id: str = "",
        lowering_blockers: tuple[str, ...] = (),
    ) -> _Params:
        """Declare a drafting dimension from explicit **measured** values.

        Named for what it carries (ADR 0016 / #873). ``dimension`` is *referential* on
        ``Drawing`` today and becomes so on :class:`Sheet` in #874 — it names a feature and a
        role, and the engine reads the value off the geometry. The verb that carries a number of
        its own needed a name saying so before that name could be reused. A measured dimension is
        the one place a value does NOT come from the part.

        This is the concept-shaped Sheet API used by generated AP242 scripts: the source file
        may call the record PMI, but the editable script declares a dimension category, value,
        label, referenced model points, and optional structured tolerances. For ordinary
        geometry-backed edits prefer feature handles such as ``sheet.hole(...).tolerance(...)``.
        ``lower_bound`` and ``upper_bound`` are the mutually exclusive alternative to
        ``upper_tol``/``lower_tol`` for a limit range. ``source_id`` is the external record
        identity retained by generated imported-PMI scripts; hand-authored dimensions normally
        leave it blank. ``lowering_blockers`` carries the explicit reason a supported imported
        requirement could not safely enrich a canonical feature parameter.
        Delegates to :func:`draftwright.model.declare.measured_dimension` (#704), so
        ``build_drawing(model=…)`` callers can author the same feature without the façade.
        """
        self._features.append(
            _measured_dimension(
                kind=kind,
                value=value,
                label=label,
                dominant_axis=dominant_axis,
                ref_pts=ref_pts,
                ref_bbox=ref_bbox,
                at=at,
                axis=axis,
                upper_tol=upper_tol,
                lower_tol=lower_tol,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
                source=source,
                source_kind=source_kind,
                source_id=source_id,
                lowering_blockers=lowering_blockers,
            )
        )
        # A handle like every other declaration verb (#922). A measured dimension carries its
        # own number, so a referential `dimension(handle, role)` on it will correctly raise —
        # but it is still a declared feature in the emitted script, and exempting it would put
        # one unnameable line back in the middle of a file where naming is otherwise uniform.
        return _Params(self, len(self._features) - 1)

    def of(self, ref) -> _Hole | _Dim:
        """A decoratable handle onto an **existing** feature — the hybrid seam (#463).

        *ref* is a fluent handle this sheet issued, a feature index, a :class:`Feature` already
        in :attr:`features` (e.g. seeded by :meth:`from_part`), or the build123d **object** you
        built (matched by ⌀ + in-plane position). Returns the same fluent handle the declaration
        verbs do, so you can ``.fit(...)`` / ``.tolerance(...)`` — and, for a hole, ``.cbore(...)``
        — a feature you did not declare from scratch. Raises if the object matches no feature or
        is ambiguous."""
        i = self._index_of(ref)
        kind = self._features[i].kind
        if kind == "hole":
            return _Hole(self, i)
        if kind in ("boss", "step"):
            return _Dim(self, i, "diameter" if kind == "boss" else "length")
        raise ValueError(f"of(): no aspect handle for a {kind!r} feature (holes / bosses / steps)")

    def _declared_token(self, ref, *, verb: str) -> int | None:
        """The token of the declared feature *ref* names, or ``None`` if it names none.

        The ONE identity-bearing resolution path (#912). Before this, three of them had grown
        independently — ``of()``'s, the GD&T seam's and ``add_dimension``'s — accepting
        different subsets of ref kinds, disagreeing on negative indices, and reporting the
        cross-sheet error in two different wordings. `_gdt_ref`'s was the one that leaked
        identity in #910, which is the argument for having a single one to audit.

        Returns a **token**, never an index: a caller that stores the result cannot then be
        invalidated by a reorder. ``None`` means *not a reference to a declared feature* — a
        build123d face, or a :class:`Feature` from elsewhere. Each caller decides what that
        means: :meth:`of` falls through to matching the object, the GD&T verbs treat it as a
        bare geometric target, :meth:`add_dimension` raises.
        """
        if isinstance(ref, bool):  # bool is an int subclass; `of(True)` is a mistake, not index 1
            raise TypeError(f"{verb}: ref must be a handle, an index, a Feature, or an object")
        if isinstance(ref, (_Hole, _Dim, _Params)):
            if ref._sheet is not self:
                raise ValueError(
                    f"{verb}: {type(ref).__name__} belongs to a different Sheet — a handle "
                    "names a feature on the sheet that issued it, and this is not that sheet"
                )
            return ref._token
        if isinstance(ref, int):
            n = len(self._features)
            if not -n <= ref < n:
                raise IndexError(f"{verb}: feature index {ref} out of range (have {n})")
            return self._token_at(ref % n)
        if isinstance(ref, Feature):
            for i, f in enumerate(self._features):
                if f is ref:  # identity — an EQUAL feature from elsewhere is not this one
                    return self._token_at(i)
            return None  # a Feature this sheet does not manage
        return None  # build123d geometry, or something else entirely

    def _index_of(self, ref) -> int:
        token = self._declared_token(ref, verb="of()")
        if token is not None:
            return self._index_of_token(token)
        if isinstance(ref, Feature):
            raise ValueError("of(): that Feature is not in this sheet's features")
        if not hasattr(ref, "bounding_box"):
            # Anything else must be the build123d object to match by ⌀ + position, and
            # `_match_object` assumes that without checking — so a wrong argument surfaced as
            # a leaked `AttributeError: 'function' object has no attribute 'bounding_box'`.
            #
            # Now reachable by an obvious route (#922): the emitted script binds `hole1` and
            # imports `hole`, so a user who comments a feature out gets Python's own
            # "Did you mean: 'hole'?" and lands here with the constructor function.
            raise ValueError(
                f"of(): expected a handle, a feature index, a Feature from this sheet, or the "
                f"build123d object you built — got {type(ref).__name__}"
            )
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

    def double_d_bore(self, obj=None, **kw) -> _Params:
        """Declare a double-D bore from its cutter or explicit major ⌀ and A/F values."""
        self._features.append(_double_d_bore(obj, **kw))
        return _Params(self, len(self._features) - 1)

    def diameter(self, obj=None, **kw) -> _Dim:
        """Declare an external cylindrical diameter (a boss / OD) — the ⌀ is read off the
        object. Returns a handle: chain ``.tolerance(...)`` for a ± on the ⌀ (P2a)."""
        self._features.append(_boss(obj, **kw))
        return _Dim(self, len(self._features) - 1, "diameter")

    def boss(self, obj=None, **kw) -> _Dim:
        """Alias of :meth:`diameter` — an external cylindrical boss / OD."""
        return self.diameter(obj, **kw)

    def polygonal_boss(self, **kw) -> _Params:
        """Declare a regular polygonal-prism boss sized across flats and by height."""
        self._features.append(_polygonal_boss(**kw))
        return _Params(self, len(self._features) - 1)

    def polygonal_stock(self, **kw) -> _Params:
        """Declare whole regular polygonal-prism stock sized A/F and axially."""
        self._features.append(_polygonal_stock(**kw))
        return _Params(self, len(self._features) - 1)

    def external_spur_gear(self, **kw) -> _Params:
        """Declare one complete metric external spur involute gear requirement."""
        self._features.append(_external_spur_gear(**kw))
        return _Params(self, len(self._features) - 1)

    def step(self, obj=None, **kw) -> _Dim:
        """Declare one axial segment of a turned profile (its OD + length). A model with any
        step renders as a turned part. Returns a handle: ``.tolerance(...)`` tolerances the
        step *length* by default, ``.tolerance(..., on="diameter")`` its OD (P2a)."""
        self._features.append(_step(obj, **kw))
        return _Dim(self, len(self._features) - 1, "length")

    def slot(self, obj=None, **kw) -> _Params:
        """Declare a milled slot / reduced across-flats section (width + length)."""
        self._features.append(_slot(obj, **kw))
        return _Params(self, len(self._features) - 1)

    def pocket(self, obj=None, **kw) -> _Params:
        """Declare a blind rectangular recess — a floored slot/pocket (width × length ×
        depth). From an object the depth axis defaults to the shortest bbox span; pass
        ``depth_axis=`` for a recess deeper than it is wide."""
        self._features.append(_pocket(obj, **kw))
        return _Params(self, len(self._features) - 1)

    def channel(self, **kw) -> _Params:
        """Declare a full-span floored channel (wall-to-wall width only)."""
        self._features.append(_channel(**kw))
        return _Params(self, len(self._features) - 1)

    def pad(self, obj=None, **kw) -> _Params:
        """Declare a bounded rectangular raised pad (footprint + X/Y location).

        Its Z height is shared with the prismatic level ladder, so it is not
        independently double-dimensioned.
        """
        self._features.append(_pad(obj, **kw))
        return _Params(self, len(self._features) - 1)

    def chamfer(self, obj=None, **kw) -> _Params:
        """Declare a chamfer (bevelled edge) — ``sheet.chamfer(bevel_face)`` reads axis, legs
        and a point on the bevel off the oblique chamfer face, or explicit
        ``sheet.chamfer(axis="z", leg=6, at=(x, y, z))``. ``leg`` = equal-leg 45° (``C{leg}``);
        ``leg1``/``leg2`` = asymmetric."""
        self._features.append(_chamfer(obj, **kw))
        return _Params(self, len(self._features) - 1)

    def fillet(self, obj=None, **kw) -> _Params:
        """Declare a fillet (rounded edge) — ``sheet.fillet(round_face)`` reads axis, radius
        and a point on the round off the cylindrical blend face, or explicit
        ``sheet.fillet(axis="z", radius=3, at=(x, y, z))``. Called out ``R{radius}`` (grouped
        ``n× R`` for equal radii)."""
        self._features.append(_fillet(obj, **kw))
        return _Params(self, len(self._features) - 1)

    def flat(self, obj=None, **kw) -> _Params:
        """Declare a machined flat on round stock (#148b) — ``sheet.flat(flat_face)`` reads the
        leader point off the planar flat face (``axis=`` and ``across=`` still required), or
        fully explicit ``sheet.flat(axis="z", across=15, at=(x, y, z))``. Called out
        ``{across} A/F`` (across flats — flat-to-flat for a double-D / hex, the D height for a
        lone flat)."""
        self._features.append(_flat(obj, **kw))
        return _Params(self, len(self._features) - 1)

    def groove(self, obj=None, **kw) -> _Params:
        """Declare a turned / circlip groove on round stock (#148c) — ``sheet.groove(floor_face)``
        reads axis, width, diameter and the leader point off the reduced-OD floor face, or fully
        explicit ``sheet.groove(axis="z", width=3, diameter=16, at=(x, y, z))``. Called out
        ``{width} WIDE × ø{diameter}`` (groove width + floor diameter)."""
        self._features.append(_groove(obj, **kw))
        return _Params(self, len(self._features) - 1)

    def plate(self, obj=None, **kw) -> _Params:
        """Declare a thin slab's thickness (a base plate / wall / rib) — ``sheet.plate(slab_box)``
        reads the thin axis + extent + centre off the slab, or explicit ``sheet.plate(axis="z",
        lo=0, hi=4, u=10, v=5)``. Only a *multi-plate* part dimensions plates (a single slab is
        the envelope)."""
        self._features.append(_plate(obj, **kw))
        return _Params(self, len(self._features) - 1)

    def rotational(self, **kw) -> _Params:
        """Declare a turned body's axial furniture — OD, rotation axis, concentric bores
        (#945): ``sheet.rotational(od=30, bores=(16,), axis="z")``.

        The last recognised kind without a declarative surface, which is why a generated
        script for a turned part could not declare its dimensions at all (#938).

        Keyword-only, unlike its object-capable siblings — see
        :func:`draftwright.model.rotational` for why an object form would disagree with
        detection (#950)."""
        self._features.append(_rotational(**kw))
        return _Params(self, len(self._features) - 1)

    def step_level(self, obj=None, **kw) -> _Params:
        """Declare a prismatic height ladder + step-position shoulders (a rebated / stepped
        block) — ``sheet.step_level(part)`` reads ``base`` / interior ``levels`` / the ``(axis,
        position)`` ``shoulders`` / ``datum`` off the part, or explicit ``sheet.step_level(base=0,
        levels=(10,), shoulders=(("x", 30),))``. A shoulder locates *where* a step changes
        height along a horizontal axis, so a stepped block is fully constrained (#555/#578)."""
        self._features.append(_step_level(obj, **kw))
        return _Params(self, len(self._features) - 1)

    def pattern(self, member, **kw) -> _Params:
        """Declare a hole pattern (bolt circle / linear array / grid) — build the
        *member* with :func:`draftwright.model.hole`."""
        self._features.append(_pattern(member, **kw))
        return _Params(self, len(self._features) - 1)

    def pocket_pattern(self, member, **kw) -> _Params:
        """Declare a linear/grid array of identical blind pockets (#841) — build the
        representative *member* with :func:`draftwright.model.pocket`. Renders as one grouped
        ``N× W × L × D DEEP`` callout + ``(n-1)× pitch`` dim(s), not N competing size dims."""
        self._features.append(_pocket_pattern(member, **kw))
        return _Params(self, len(self._features) - 1)

    def slot_pattern(self, member, **kw) -> _Params:
        """Declare a linear/grid array of identical milled slots (#841) — build the
        representative *member* with :func:`draftwright.model.slot`. Renders as one grouped
        ``N× SLOT W × L`` leader + ``(n-1)× pitch`` dim(s), not N competing size dims."""
        self._features.append(_slot_pattern(member, **kw))
        return _Params(self, len(self._features) - 1)

    def envelope(self, obj=None) -> _Params:
        """Declare the overall bounding dimensions. Defaults to the whole part.

        The no-argument form reuses an equal whole-part envelope already seeded by
        :meth:`from_part`; its returned handle therefore decorates and dimensions that
        detected feature instead of appending a duplicate. An explicit *obj* remains a
        distinct declaration, including when another envelope is already present.
        """
        feature = _envelope(obj if obj is not None else self._part)
        if obj is None:
            for index, existing in enumerate(self._features):
                if existing == feature:
                    return _Params(self, index)
        self._features.append(feature)
        return _Params(self, len(self._features) - 1)

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

    # -- view declaration (ADR 0018) ---------------------------------------

    @staticmethod
    def _principal_view_name(name) -> tuple[str, str]:
        name = str(name).strip().lower()
        kinds = {
            "front": "principal",
            "plan": "principal",
            "side": "principal",
            "iso": "pictorial",
        }
        if name not in kinds:
            raise ValueError(
                f"unknown view {name!r}; expected one of {tuple(kinds)}. "
                "Use section_view()/detail_view() for derived views."
            )
        return name, kinds[name]

    @staticmethod
    def _derived_view_name(kind: str, label) -> str:
        label = str(label).strip()
        if len(label) != 1 or not label.isalnum():
            raise ValueError(f"{kind}_view() needs one alphanumeric drawing label, got {label!r}")
        slug = label.lower()
        return f"section_{slug}{slug}" if kind == "section" else f"detail_{slug}"

    def _all_view_names(self) -> set[str]:
        return {
            record["name"]
            for bucket in (
                self._principal_views,
                self._added_principal_views,
                self._derived_views,
                self._added_derived_views,
            )
            for record in bucket
        }

    def _append_view_record(
        self, bucket: str, *, name: str, kind: str, target=None, source: ConstraintSource
    ) -> _View:
        if name in self._all_view_names():
            raise ValueError(f"view {name!r} is declared more than once")
        records = getattr(self, bucket)
        records.append(
            {
                "name": name,
                "kind": kind,
                "target": target,
                "scale_factor": None,
                "source": source,
            }
        )
        return _View(self, bucket, len(records) - 1)

    def _reject_authored_view_auto_dimensions(self, verb: str) -> None:
        if self._auto_dimensions is not None:
            raise ValueError(
                f"{verb} authors the view set, but this sheet already called "
                "auto_dimensions(). ADR 0018 makes requirements determine views, not the "
                "reverse: use authored_dimensions() with explicit dimension(...) lines, or "
                "keep auto_views() and use add_view()/add_section_view()/add_detail_view()."
            )

    def authored_views(self) -> Sheet:
        """Declare that subsequent :meth:`view` lines are the complete principal set.

        Calling this with no ``view(...)`` lines makes the empty authored set explicit.  It
        remains a request even when a later build finds that no projectable drawing can satisfy
        it; absence of the verb retains the behaviourally-compatible automatic default.
        """
        self._reject_authored_view_auto_dimensions("authored_views()")
        if self._principal_view_source == "automatic":
            raise ValueError(
                "a sheet has one principal-view source: authored_views()/view(...) or "
                "auto_views()/add_view(), not both"
            )
        self._principal_view_source = "authored"
        self._principal_view_source_at = self._principal_view_source_at or _constraint_source()
        return self

    def auto_views(self) -> Sheet:
        """Select automatic principal and derived views, optionally augmented by add verbs."""
        if self._principal_view_source == "authored" or self._derived_view_source == "authored":
            raise ValueError(
                "auto_views() cannot be combined with an authored principal or derived view "
                "set; use add_view()/add_section_view()/add_detail_view() to augment automatic views"
            )
        warnings.warn(
            "Sheet.auto_views() is soft deprecated: still supported and NOT scheduled for "
            "removal, but authored_views() plus view(...) lines is the editable surface.",
            SoftDeprecationWarning,
            stacklevel=2,
        )
        self._principal_view_source = "automatic"
        self._derived_view_source = "automatic"
        source = _constraint_source()
        self._principal_view_source_at = self._principal_view_source_at or source
        self._derived_view_source_at = self._derived_view_source_at or source
        return self

    def view(self, name) -> _View:
        """Add one view to the complete authored principal/orientation set."""
        self._reject_authored_view_auto_dimensions("view()")
        if self._principal_view_source == "automatic":
            raise ValueError(
                "view() defines the complete authored set and cannot follow auto_views(); "
                "use add_view() to augment the automatic set"
            )
        self._principal_view_source = "authored"
        self._principal_view_source_at = self._principal_view_source_at or _constraint_source()
        name, kind = self._principal_view_name(name)
        return self._append_view_record(
            "_principal_views", name=name, kind=kind, source=_constraint_source()
        )

    def add_view(self, name) -> _View:
        """Require one additional principal/orientation view in an automatic set."""
        if self._principal_view_source == "authored":
            raise ValueError("add_view() augments auto_views(); use view() inside an authored set")
        name, kind = self._principal_view_name(name)
        return self._append_view_record(
            "_added_principal_views", name=name, kind=kind, source=_constraint_source()
        )

    def _derived_target(self, verb: str, *, feature=None, at=None):
        if (feature is None) == (at is None):
            raise ValueError(f"{verb} needs exactly one of its feature target or at=")
        if at is not None:
            value = float(at)
            if not math.isfinite(value):
                raise ValueError(f"{verb}(at=…) needs a finite Y, got {at!r}")
            return ("at", value)
        _target, token = self._gdt_ref(feature)
        if token is None:
            raise ValueError(
                f"{verb} needs a declared feature handle/index/Feature; use at= for a bare cut"
            )
        return ("feature", token)

    def section_view(self, label, through=None, *, at=None) -> _View:
        """Author a named section view through a declared feature or explicit Y cut plane."""
        self._reject_authored_view_auto_dimensions("section_view()")
        if self._derived_view_source == "automatic":
            raise ValueError(
                "section_view() defines the authored derived set and cannot follow auto_views(); "
                "use add_section_view() to augment automatic derived views"
            )
        self._derived_view_source = "authored"
        self._derived_view_source_at = self._derived_view_source_at or _constraint_source()
        return self._append_view_record(
            "_derived_views",
            name=self._derived_view_name("section", label),
            kind="section",
            target=self._derived_target("section_view()", feature=through, at=at),
            source=_constraint_source(),
        )

    def add_section_view(self, label, through=None, *, at=None) -> _View:
        """Augment automatic derived views with one named section."""
        if self._derived_view_source == "authored":
            raise ValueError(
                "add_section_view() augments auto_views(); use section_view() in an authored set"
            )
        return self._append_view_record(
            "_added_derived_views",
            name=self._derived_view_name("section", label),
            kind="section",
            target=self._derived_target("add_section_view()", feature=through, at=at),
            source=_constraint_source(),
        )

    def detail_view(self, label, around) -> _View:
        """Author a named detail view around a declared feature."""
        self._reject_authored_view_auto_dimensions("detail_view()")
        if self._derived_view_source == "automatic":
            raise ValueError(
                "detail_view() defines the authored derived set and cannot follow auto_views(); "
                "use add_detail_view() to augment automatic derived views"
            )
        self._derived_view_source = "authored"
        self._derived_view_source_at = self._derived_view_source_at or _constraint_source()
        return self._append_view_record(
            "_derived_views",
            name=self._derived_view_name("detail", label),
            kind="detail",
            target=self._derived_target("detail_view()", feature=around, at=None),
            source=_constraint_source(),
        )

    def add_detail_view(self, label, around) -> _View:
        """Augment automatic derived views with one named detail around a declared feature."""
        if self._derived_view_source == "authored":
            raise ValueError(
                "add_detail_view() augments auto_views(); use detail_view() in an authored set"
            )
        return self._append_view_record(
            "_added_derived_views",
            name=self._derived_view_name("detail", label),
            kind="detail",
            target=self._derived_target("add_detail_view()", feature=around, at=None),
            source=_constraint_source(),
        )

    def section(self, feature=None, *, at=None) -> Sheet:
        """Request a full **section A–A** (#841) — the part-level verb behind the auto section.

        A section fires automatically only when a Z-axis hole/pattern has a counterbore,
        spotface, or blind bottom; a blind pocket has no such driving hole, so its floor
        and depth stay hidden-line-only. This forces a cut so that internal profile reads.

        The cut plane is normal to Y. *feature* — a fluent handle / :class:`Feature` /
        index — cuts through that feature's centre (the natural "section through this
        pocket"); ``at=<y>`` cuts at an explicit Y; bare ``section()`` cuts through the
        part centre. The section renders last (its room check clears the right-of-side-view
        band), so declare it after the per-feature verbs. Chainable."""
        warnings.warn(
            "Sheet.section() is deprecated; use add_section_view('A', through=...) or "
            "add_section_view('A', at=...). Removal target 0.6.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        if at is not None:
            if not math.isfinite(at):
                raise ValueError(f"section(at=…) needs a finite Y, got {at!r}")
            self._section = ("at", float(at))
        elif feature is not None:
            _target, src = self._gdt_ref(feature)
            if src is None:
                raise ValueError(
                    "section(feature=…) needs a declared feature (a handle/index/Feature "
                    "on this sheet) — pass at=<y> for a bare cut-plane position"
                )
            self._section = ("feature", src)
        else:
            self._section = ("auto", None)
        return self

    def detail(self) -> Sheet:
        """Ensure enlarged **detail-view** recovery is enabled (#42/#307/#841).

        Automatic builds enable it by default; this verb is useful after constructing a
        ``Sheet(..., detail_view=False)`` or when an emitted declaration should state the
        choice explicitly. Adds a magnified crop of the step-height region when warranted
        (a no-op otherwise). Chainable. Not feature-targeted — for a blind pocket's
        floor/depth prefer :meth:`section`."""
        warnings.warn(
            "Sheet.detail() is deprecated; use add_detail_view('A', around=feature). "
            "Removal target 0.6.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._opts["detail_view"] = True
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
        self._append_gdt(item, self._token_at(src_index))

    def _gdt_note(self, text, src_index: int, *, view=None, side=None) -> None:
        """A note declared through a fluent handle — like :meth:`_gdt_finish`, sources provenance
        from the feature INDEX so a later size verb on the same handle can't strand it."""
        item = _declare_note(text, self._features[src_index], self._part, view=view, side=side)
        self._append_gdt(item, self._token_at(src_index))

    def _append_gdt(self, item, src_token) -> None:
        """Append a GD&T IR item, recording its source feature's **token** for build-time
        provenance re-materialization (``None`` for a bare face — no source feature to track).

        Takes an already-resolved token rather than an index so that a caller which stores the
        value between resolve and append — :class:`_Control` — cannot hand over a stale slot."""
        self._features.append(item)
        self._bind_gdt_source(self._token_at(len(self._features) - 1), src_token)

    def _bind_gdt_source(self, gdt_token, src_token) -> None:
        """Remember the source of a GD&T item when it names a declared feature."""
        if src_token is not None:
            self._gdt_src.append((gdt_token, src_token))

    def _gdt_ref(self, ref):
        """Resolve a GD&T target to ``(target, source_token)``: a fluent handle / index / a
        :class:`Feature` already in :attr:`features` → its feature + token (the token re-binds
        provenance at build); a build123d face or an external Feature → ``(ref, None)``.

        A **token**, not an index (#908). :class:`_Control` holds this value across later
        ``.position(...)`` calls, so an index here would name whatever occupied the slot at
        *append* time rather than the feature the caller passed to :meth:`control`."""
        token = self._declared_token(ref, verb="the GD&T target")
        if token is None:
            return ref, None  # a build123d face / an external Feature — no source feature
        return self._features[self._index_of_token(token)], token

    def _materialize_gdt(self) -> None:
        """Re-bind each handle-sourced GD&T item's ``origin`` to the FINAL source feature (a
        size verb may have replaced it since declaration). Idempotent; mirrors P2a's
        :meth:`_decorations`. Called before handing features to the engine."""
        for gt, st in self._gdt_src:
            gi, si = self._index_of_token(gt), self._index_of_token(st)
            # Through `_replace_feature`, not a raw write: this is a legitimate internal
            # rebind, and a raw write would desync the identity shadow and make an
            # ordinary `hole.note(...)` + `add_dimension(...)` script look like an
            # unsupported list edit (#872 review, round 6).
            self._replace_feature(gi, replace(self._features[gi], origin=self._features[si]))

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
        the header); cells are stringified. *prefer* ranks the free candidates by the page corner
        to sit nearest (``"tr"``/``"tl"``/``"br"``/``"bl"``); it does not exclude viable interior
        or opposite-corner space. A table with no fitting free region records an inspectable
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

    def _materialized_view_constraint(self, record: dict) -> ViewConstraint:
        target = record["target"]
        if target is not None and target[0] == "feature":
            target = ("feature", self._features[self._index_of_token(target[1])])
        return ViewConstraint(
            ViewSpec(
                name=record["name"],
                kind=record["kind"],
                target=target,
                scale_factor=record["scale_factor"],
            ),
            record["source"],
        )

    @property
    def view_constraints(self) -> ViewConstraints:
        """The immutable pre-projection view request authored on this sheet."""

        materialize = self._materialized_view_constraint
        return ViewConstraints(
            principal_source=self._principal_view_source,
            principal_source_location=self._principal_view_source_at,
            principals=tuple(map(materialize, self._principal_views)),
            added_principals=tuple(map(materialize, self._added_principal_views)),
            derived_source=self._derived_view_source,
            derived_source_location=self._derived_view_source_at,
            derived=tuple(map(materialize, self._derived_views)),
            added_derived=tuple(map(materialize, self._added_derived_views)),
            relations=tuple(self._view_relations),
            pins=tuple(self._view_pins),
        )

    def row(self, *views, gap=None) -> Sheet:
        """Constrain complete view blocks into a left-to-right row."""
        names = [view.name if isinstance(view, _View) else str(view) for view in views]
        if len(names) < 2:
            raise ValueError("row() needs at least two views")
        source = _constraint_source()
        for left, right in zip(names, names[1:]):
            self._view_relations.append(
                ViewRelation(right, "right_of", left, None if gap is None else float(gap), source)
            )
        return self

    def column(self, *views, gap=None) -> Sheet:
        """Constrain complete view blocks into a bottom-to-top column."""
        names = [view.name if isinstance(view, _View) else str(view) for view in views]
        if len(names) < 2:
            raise ValueError("column() needs at least two views")
        source = _constraint_source()
        for below, above in zip(names, names[1:]):
            self._view_relations.append(
                ViewRelation(above, "above", below, None if gap is None else float(gap), source)
            )
        return self

    def _view_build_request(self) -> tuple[tuple[str, ...] | None, bool]:
        """Validate source coherence and lower the principal request to the engine seam."""

        if self._added_principal_views and self._principal_view_source != "automatic":
            source = self._added_principal_views[0]["source"]
            raise ValueError(
                f"add_view() at {source} augments the automatic set; call auto_views() first"
            )
        if self._added_derived_views and self._derived_view_source != "automatic":
            source = self._added_derived_views[0]["source"]
            raise ValueError(
                f"add_section_view()/add_detail_view() at {source} augment automatic derived "
                "views; call auto_views() first"
            )
        if self._principal_view_source != "authored":
            return None, True
        names = tuple(record["name"] for record in self._principal_views)
        principals = tuple(name for name in names if name in {"front", "plan", "side"})
        if not principals:
            source = (
                self._principal_views[0]["source"]
                if self._principal_views
                else self._principal_view_source_at or "the authored_views() declaration"
            )
            raise ValueError(
                f"the authored view set from {source} has no principal orthographic view; "
                "add view('front'), view('plan'), or view('side')"
            )
        return principals, "iso" in names

    def _view_source_description(self) -> str:
        sources = [record["source"] for record in self._principal_views]
        if not sources:
            return f"authored at {self._principal_view_source_at}"
        return "authored at " + ", ".join(str(source) for source in sources)

    def _derived_build_request(self) -> tuple[tuple | None, bool]:
        """Validate derived targets and return legacy decoration/detail compatibility state."""

        records = [*self._derived_views, *self._added_derived_views]
        sections = [record for record in records if record["kind"] == "section"]
        if self._section is not None and records:
            raise ValueError(
                "deprecated section() cannot be combined with section_view()/detail_view() "
                "constraints; migrate the legacy call to add_section_view()"
            )
        for record in sections:
            target = record["target"]
            if target[0] == "at":
                self._section_cut_y(target)
        detail_auto = self._derived_view_source != "authored"
        return None, detail_auto

    @property
    def features(self) -> MutableSequence:
        """The declared IR features — mutable: override, drop or reorder before
        :meth:`build`.

        Each feature carries an identity token (#908), so a **reorder** via
        :meth:`~_FeatureView.reverse` or :meth:`~_FeatureView.sort` moves every reference
        with it — a handle, a tolerance, a GD&T origin, a section, an `add_dimension`
        intent all follow their feature to its new position.

        **Assignment is not a move.** ``features[i] = f`` and slice assignment mint a new
        identity, because assignment cannot distinguish "move this feature here" from
        "put a different feature here" — so references to the displaced feature fail
        loudly rather than silently transferring to whatever replaced it. The same holds
        for deletion. Use ``reverse``/``sort`` to reorder while keeping references.
        """
        return self._features

    def auto_dimensions(self) -> Sheet:
        """**Soft deprecated** (#1043) — prefer :meth:`authored_dimensions` in new code.

        Supported, and **not scheduled for removal**: existing scripts keep working. But on
        this surface, authored dimensions are the right default, for three reasons:

        - it is what draftwright itself emits — ``--script`` writes ``authored_dimensions()``
          with a line per dimension, so the automatic path is the only form the tool never
          produces;
        - *omission means suppression* (ADR 0016) only holds for an authored set. Under an
          automatic one you cannot express "not that one";
        - an authored list is editable text. That is what makes "generate a script, then
          refine it" work, for a person or a model.

        If you want the planner's choices as a starting point, generate them —
        ``draftwright yourmodule:part --script`` — and edit the result, rather than asking
        for them at runtime where they cannot be seen or changed.

        Still asks for the planner's automatic set (ADR 0016). A build must say where its
        dimensions come from rather than defaulting silently (#874), and this is one of the
        two ways to say it. It is also what :meth:`add_dimension` augments.

        ``build_drawing(part)``'s automatic path is unaffected and carries no warning — that
        is the detected front door, and being automatic is its whole point.
        """
        if self._principal_view_source == "authored" or self._derived_view_source == "authored":
            raise ValueError(
                "auto_dimensions() cannot be combined with authored views. ADR 0018 makes "
                "requirements determine views, not views determine requirements: use "
                "authored_dimensions() with explicit dimension(...) lines, or keep "
                "auto_views() and augment it with add_view()/add_section_view()/add_detail_view()."
            )
        warnings.warn(
            "Sheet.auto_dimensions() is soft deprecated: still supported and NOT scheduled "
            "for removal, but authored dimensions are the going-forward surface. Prefer "
            "authored_dimensions() plus dimension(feature, role) lines — or generate them "
            "with `--script` and edit the result. build_drawing()'s automatic path is "
            "unaffected.",
            SoftDeprecationWarning,
            stacklevel=2,
        )
        self._auto_dimensions = "explicit"
        return self

    def authored_dimensions(self) -> Sheet:
        """Declare that the :meth:`dimension` lines in this script ARE the complete set.

        The other half of :meth:`auto_dimensions`, and until #933 it did not exist: the
        authored source was entered implicitly, as a side effect of calling `dimension()` at
        least once. That left one thing unsayable — **the complete set is empty** — because
        absence of `dimension(...)` lines is also what a script with no source at all looks
        like, which `_check_dimension_source` must reject. So a valid `PartModel` carrying
        `authored_dimensions=()` built directly but could not be written as a script.

        Calling `dimension(...)` still selects this source on its own; the verb only makes
        the choice sayable when there is nothing else to say it. Emitted scripts write it
        unconditionally, so an authored script states its source with a verb rather than with
        a comment a reader has to trust (ADR 0016 / #874).
        """
        self._authored_source = True
        return self

    def add_dimension(self, feature, role: DimensionParameterId, *, axis: str | None = None):
        """Augment the planner's set with one more measurement (ADR 0016 / #872).

        *feature* is a declared-feature handle (what :meth:`hole`, :meth:`boss`, … return),
        an index into :attr:`features`, or the IR feature itself. *role* names the
        measurement — ``"bore.diameter"``, ``"grid_pitch"``, … — and carries **no number**: the
        value is read from the geometry, so the size still lives in exactly one place.

        Returns a :class:`DimensionIntent`.

        **Soft deprecated** (#1043) — supported and not scheduled for removal, but it exists
        only to augment :meth:`auto_dimensions`, which is itself discouraged here. To add a
        measurement to an authored set, write a :meth:`dimension` line: same effect, and the
        result is visible in the script rather than computed at runtime.

        Requesting a measurement the planner already emits is a deliberate no-op, not an
        error: a script should be able to ask without first knowing the rule set's mind.

        ``axis`` disambiguates a role a feature carries more than once — today a grid
        pattern's two pitches. Omitting it there raises rather than picking one, because
        a silent coin toss between the row and column pitch is the kind of wrong a reader
        cannot see.
        """
        warnings.warn(
            "Sheet.add_dimension() is soft deprecated: still supported and NOT scheduled for "
            "removal, but it augments the automatic set, which is itself discouraged here. "
            "Prefer authored_dimensions() plus a dimension(feature, role) line — the same "
            "measurement, visible in the script rather than added at runtime.",
            SoftDeprecationWarning,
            stacklevel=2,
        )
        token, _target, discriminator, role = self._resolve_measurement(
            feature, role, axis, "add_dimension"
        )
        entry = {"token": token, "role": role, "discriminator": discriminator}
        self._added_dimensions.append(entry)
        return DimensionIntent(self, entry)

    def _check_dimension_source(self) -> None:
        """A build must say where its dimensions come from (ADR 0016 / #874).

        The set has exactly two sources and they are mutually exclusive:

        - :meth:`auto_dimensions` — the planner's selected set, optionally augmented by
          :meth:`add_dimension`;
        - :meth:`dimension` declarations — the complete authored set.

        Three errors follow, and all three are checked HERE rather than in the verbs, so intent
        stays order-independent: declaring an augment before its source must read the same as
        declaring it after.

        Requiring a source at all is the breaking change. It is the project's standing
        preference for a loud failure over a plausible-looking wrong drawing (#630/#631, the
        #632 completeness lint): a sheet that asked for neither would otherwise build silently,
        and *which* set it got would depend on a default the script never mentions.

        Rejected: implicit-by-usage — "any `dimension` line turns the automatic set off". A
        hand-author adding one pitch dimension would silently lose every ⌀ callout.
        """
        authored = bool(self._authored) or self._authored_source
        if authored and self._auto_dimensions == "explicit":
            raise ValueError(
                "a sheet has ONE dimension source: auto_dimensions() for the planner's set, or "
                "dimension(...) declarations for the complete authored set. This sheet asks for "
                "both, which cannot be honoured — omission is only meaningful inside a set "
                "declared complete, and the automatic set has no omissions to read."
            )
        if authored:
            # `from_part` chose the automatic source on the caller's behalf; declaring an
            # authored set is the script overriding that choice, not contradicting itself.
            # (An explicit `auto_dimensions()` line already raised above.)
            self._auto_dimensions = None
        if self._added_dimensions and not self._auto_dimensions:
            raise ValueError(
                "add_dimension() augments the planner's automatic dimension set, so the sheet "
                "must request one — call auto_dimensions(). To declare the complete set "
                "instead, use dimension(...) lines and drop the add_dimension() calls."
            )
        if not self._auto_dimensions and not authored:
            raise ValueError(
                "this sheet does not say where its dimensions come from. Call "
                "authored_dimensions() and then add zero or more dimension(feature, role) "
                "lines to declare the complete set — that is the recommended surface, and "
                "what `--script` generates. (A dimension(...) line selects the authored "
                "source on its own; the verb is how a COMPLETE-BUT-EMPTY set says so, since "
                "it has no line to say it with.) auto_dimensions() also works and is "
                "supported, but is soft deprecated on this surface (#1043). Building without "
                "either used to mean the automatic set; ADR 0016 makes the source explicit "
                "so that omitting a dimension can mean something."
            )

    def _resolve_measurement(
        self, feature, role: DimensionParameterId, axis: str | None, verb: str
    ):
        """Resolve ``(feature, role, axis)`` to ``(token, feature, discriminator)``, or raise.

        Shared by :meth:`add_dimension` and :meth:`dimension` — the two verbs ADDRESS a
        measurement identically and differ only in what naming one means, so the addressing
        lives in one place. A second copy is how the callout reading drifted in #875."""
        token = self._feature_token(feature)
        target = self._features[self._index_of_token(token)]
        params = target.parameters()
        roles = {p.role for p in params} | {p.parameter_id for p in params}
        # A datum-referenced position is a dimension, but it is SYNTHESIZED (planner +
        # datum) rather than carried by the feature, so it has no `DimParameter` to match.
        # The planner owns which kinds have one; asking it here is what lets an authored
        # set name a location — and therefore omit one (#925).
        if _location_role(target) is not None:
            roles.add(_LOCATION_ROLE)
        if role not in roles:
            raise ValueError(
                f"{verb}: {type(target).__name__} has no {role!r} measurement "
                f"(it carries {sorted(roles)})"
            )
        matching = [p for p in params if role in (p.role, p.parameter_id)]
        # ── canonical spelling: the parameter id (#963) ──────────────────────────────
        # A role spelling ("bore") and a parameter id ("bore.diameter") both resolve, and
        # they are not synonyms: the role selects EVERY parameter carrying it, the id selects
        # one. On a role with a single parameter that difference is invisible, which is why it
        # went unnoticed; on `step` it is not — `dimension(step, "step")` quietly declared both
        # `step.length` and `step.diameter`. In an authored set, whose whole semantics is that
        # omission means suppression, silently declaring an extra measurement is the mirror
        # image of the rule. So the id is canonical, and a role that names more than one is now
        # refused rather than resolved to a set the author did not ask for.
        # Compare UNDISCRIMINATED ids. `grid_pitch.length.row` and `.col` are two variants of
        # one measurement, told apart by `axis=` a few lines below — counting them as two
        # would fire this refusal in place of that older, more useful error.
        bases = sorted({f"{p.role}.{p.kind}" for p in matching})
        bare = role not in {p.parameter_id for p in matching} and bool(matching)
        if bare and len(bases) > 1:
            raise ValueError(
                f"{verb}({role!r}) names {len(bases)} measurements on this feature "
                f"({', '.join(bases)}) — the role is the family, not one of them. Name the "
                f"one you mean, or declare each."
            )
        # A DISCRIMINATED parameter is named by its full id like any other (#965 review). It
        # was the one exception — the bare role plus `axis=` — which meant `dimension_ids()` listed a
        # spelling that then raised "ambiguous", breaking the contract the generated header
        # points people at. The id already carries the variant, so it is self-sufficient; the
        # bare role keeps working with `axis=` because that is what older scripts wrote.
        exact = next((p for p in matching if p.parameter_id == role), None)
        if exact is not None and exact.discriminator:
            if axis is not None and axis != exact.discriminator:
                raise ValueError(
                    f"{verb}({role!r}, axis={axis!r}): that id already names the "
                    f"{exact.discriminator!r} variant"
                )
            return token, target, exact.discriminator, role
        discriminated = any(p.discriminator for p in matching)
        if bare and not discriminated:
            # Deprecated in #963, REMOVED at 0.4.0 (#720). This warned for one development
            # cycle but never appeared in a release, so the break is documented rather than
            # warned — see docs/deprecations.md and the 0.4.0 CHANGELOG. Raising (not
            # normalising) is the point: an authored set means omission is suppression, and a
            # spelling that selects the whole family is how `dimension(step, "step")` quietly
            # declared two measurements the author never named.
            raise ValueError(
                f"{verb}({role!r}): name the measurement by its id, {bases[0]!r}. The bare "
                "role is the family spelling, not one of its measurements; it was removed at "
                "0.4.0 (#720). dimension_ids() lists the valid ids for a feature."
            )
        # A discriminated parameter keeps the BARE role: its full id carries the variant
        # (`grid_pitch.length.row`), which `axis=` supplies separately, so normalising to the
        # id here would hand the planner a spelling that matches no parameter. This is NOT the
        # removed spelling — it is how variants are addressed, and it never warned.
        canonical = role
        discs = {p.discriminator for p in matching}
        if len(discs) > 1 and axis is None:
            raise ValueError(
                f"{verb}({role!r}) is ambiguous on this feature — it carries "
                f"{len(discs)} of them ({sorted(d for d in discs if d)}). Name one with "
                f"axis=."
            )
        if axis is not None and axis not in discs:
            raise ValueError(
                f"{verb}({role!r}, axis={axis!r}): this feature has no such "
                f"variant ({sorted(d for d in discs if d)})"
            )
        return token, target, axis, canonical

    def _replace_feature(self, index: int, feature) -> None:
        """Swap the frozen feature at *index* for an updated copy.

        A plain slot write: :class:`_FeatureView` keeps that slot's token, so every
        reference naming it — a tolerance, a GD&T origin, a dimension intent — follows
        the replacement without bookkeeping. Before #908 this method had to advance each
        intent by hand, and getting that wrong was two of the seven #872 review findings.
        """
        self._features._rebind(index, feature)

    def _token_at(self, index: int) -> int:
        """The token of the feature currently at *index*."""
        return self._entries[index][0]

    def _index_of_token(self, token: int) -> int:
        """Where the feature named by *token* currently sits.

        Raises when it has been removed — a handle for a dropped feature must not
        silently resolve to whatever moved into its old position (#908).
        """
        for i, (tok, _f) in enumerate(self._entries):
            if tok == token:
                return i
        raise ValueError(
            "this handle's feature is no longer on the sheet — it was removed from "
            "`features` after the handle was issued"
        )

    def _feature_token(self, feature) -> int:
        """Resolve a handle / index / IR feature to its **token**, or raise.

        :meth:`add_dimension`'s flavour of :meth:`_declared_token`: a dimension request must
        name a feature this sheet plans, so "not a declared reference" is an error here rather
        than the fallthrough it is for :meth:`of` and the GD&T verbs."""
        token = self._declared_token(feature, verb="add_dimension")
        if token is None:
            raise ValueError(f"add_dimension: {feature!r} is not a feature declared on this sheet")
        return token

    def _requested_dimensions(self) -> tuple:
        """Materialize the token-keyed intents against the CURRENT features.

        Tokens make this straightforward: an intent names a feature, not a slot, so a
        reorder of the public list is transparent and a removal raises through
        :meth:`_index_of_token` rather than silently retargeting (#908).
        """
        return tuple(
            RequestedDimension(
                feature=self._features[self._index_of_token(e["token"])],
                role=e["role"],
                discriminator=e["discriminator"],
            )
            for e in self._added_dimensions
        )

    def _authored_set(self) -> tuple | None:
        """The complete authored set, or ``None`` when the planner's automatic set is in use.

        ``None`` and ``()`` mean different things here and the distinction is load-bearing:
        ``None`` is "the planner chooses", ``()`` is "the author chose nothing" — a complete
        set that happens to be empty, which is a legitimate drawing.

        That second case was unreachable until #933, because the authored source could only
        be selected by writing a `dimension(...)` line, and this docstring said so. It is
        reachable now through :meth:`authored_dimensions`, which is what that verb exists
        for; what :meth:`_check_dimension_source` still rejects is naming NO source at all."""
        if not self._authored:
            # `()` when the script SAID the authored set is its source and named nothing —
            # "the author chose no dimensions", which is a legitimate drawing. `None` only
            # when no source was declared at all, which `_check_dimension_source` rejects.
            return () if self._authored_source else None
        return tuple(
            RequestedDimension(
                feature=self._features[self._index_of_token(e["token"])],
                role=e["role"],
                discriminator=e["discriminator"],
            )
            for e in self._authored
        )

    def _decorations(self, section_request=_UNSET, *, suppress_auto_sections=False) -> dict:
        """Materialize the token-keyed ± tolerances against the FINAL features (a handle may
        have been recorded before a later .depth()/… replaced the feature) → the
        ``(feature, kind)`` (or role-keyed ``(feature, kind, role)``, #746) decoration map
        the planner reads (P2a). The tail of the key (``kind`` or ``kind, role``) passes
        through unchanged; only the leading token becomes the feature."""
        deco: dict = {
            (self._features[self._index_of_token(tok)], *rest): tol
            for (tok, *rest), tol in self._tolerances.items()
        }
        if self._section is not None:
            deco["section"] = self._section_cut_y()  # the #841 cut-plane Y (scalar key)
        if section_request is not _UNSET and section_request is not None:
            deco["section"] = self._section_cut_y(section_request)
        if suppress_auto_sections and "section" not in deco:
            deco["auto_sections"] = False
        return deco

    def _section_cut_y(self, request=None) -> float:
        """Resolve the requested :meth:`section` to a cut-plane Y (materialized at build so a
        handle recorded before a later size verb resolves against the FINAL feature)."""
        kind, payload = self._section if request is None else request  # type: ignore[misc]
        if kind == "feature":
            return float(self._features[self._index_of_token(payload)].frame.origin[1])
        if kind == "auto":
            return float(self._part.bounding_box().center().Y)  # bare section() → part centre
        # An explicit at= is untrusted: a plane outside the part's Y extent leaves the body
        # uncut (a plain projection mislabelled "SECTION A–A") or clears it (a section dropped
        # after layout already reserved its row). Reject it here (#841 review).
        cut_y = float(payload)
        bb = self._part.bounding_box()
        if not (bb.min.Y < cut_y < bb.max.Y):  # strictly inside — a grazing plane cuts nothing
            raise ValueError(
                f"section(at={cut_y:.3g}) is not strictly inside the part Y extent "
                f"({bb.min.Y:.3g}, {bb.max.Y:.3g}) — the plane would not cut the solid"
            )
        return cut_y

    def model(self):
        """The IR the engine will draw (detection skipped) — for inspection. Wraps the
        declared features into a :class:`PartModel` **without** rendering a drawing (#453):
        the same wrapping :meth:`build` hands the engine (part bbox + corner datum + step-
        inferred orientation + the P2a decorations), so inspection pays no projection/anno
        cost and can't hit a layout/render failure. Wraps the *solids body* (as :func:`_analyse`
        does), so the bbox/datum match what ``build()`` draws even when the part carries
        bbox-extending non-solid geometry.

        Gated on the dimension source like :meth:`build` (#874): this is the model the engine
        WOULD draw, so a sheet that cannot be built must not hand one out — otherwise
        ``build_drawing(part, model=sheet.model())`` is a way around the check, and the two
        public surfaces disagree about the same sheet (the #707 class of divergence)."""
        self._prepare()
        self._check_dimension_source()
        return _coerce_model(
            self._features,
            _solids_body(self._part),
            self._decorations(),
            self._requested_dimensions(),
            self._authored_set(),
        )

    def build(self):
        """Build the :class:`~draftwright.drawing.Drawing` — detection skipped; only the
        declared features are drawn. Declared corner-block tables (:meth:`table`/:meth:`notes`)
        are placed last, clear of everything already on the sheet."""
        self._prepare()
        # `add_dimension` augments the planner's set, so the sheet must have asked for
        # one (ADR 0016 / #872). Checked HERE rather than in the verb so intent stays
        # order-independent: declaring the augment before the source must read the same
        # as declaring it after.
        self._check_dimension_source()
        principal_views, include_iso = self._view_build_request()
        section_request, automatic_details = self._derived_build_request()
        constraints = self.view_constraints
        required_tables = tuple(
            (size, table["prefer"])
            for table in self._tables
            if (
                size := _est_table_size(
                    table["rows"],
                    block_cols=table["block_cols"],
                )
            )
            is not None
        )

        def place_declared_tables(dwg):
            """Place authored sheet furniture before explicit-scale completeness is decided."""
            used = set(dwg.annotations())
            for table_index, table in enumerate(self._tables):
                name = table["name"]
                if name in used:
                    base, k = name, 1
                    while f"{base}_{k}" in used:
                        k += 1
                    name = f"{base}_{k}"
                    warnings.warn(
                        f"table name {table['name']!r} is already taken — placed as {name!r}",
                        stacklevel=3,
                    )
                placed = dwg.add_table(
                    table["rows"],
                    prefer=table["prefer"],
                    name=name,
                    block_cols=table["block_cols"],
                    _source_id=f"sheet.table:{table_index}:{table['name']}",
                )
                if placed is not None:
                    used.add(name)
            return dwg

        opts = dict(self._opts)
        if not automatic_details:
            opts["detail_view"] = False
        try:
            return build_drawing(
                self._part,
                model=self._features,
                decorations=self._decorations(
                    section_request,
                    suppress_auto_sections=self._derived_view_source == "authored",
                ),
                requested=self._requested_dimensions(),
                authored=self._authored_set(),
                _post_build=place_declared_tables,
                _required_tables=required_tables,
                _views=principal_views,
                _include_iso=include_iso,
                _view_constraints=constraints,
                **opts,
            )
        except ViewPlanIncomplete as exc:
            if self._principal_view_source != "authored":
                raise
            raise ViewPlanIncomplete(
                exc.planned,
                exc.uncovered,
                source=self._view_source_description(),
            ) from None

    def export(self, stem=None, *, formats=("pdf",), dpi=150):
        """Build the drawing and write the requested *formats* — a format name or an
        iterable from ``("svg", "dxf", "pdf", "png")``, default PDF (matching the
        CLI); return ``{format: path}``. *dpi* sets the PNG raster resolution.
        *stem* defaults to the drawing number, lower-cased.

        ``formats=None`` means "unspecified", so it takes this method's default rather than
        being forwarded. On :meth:`Drawing.export` a ``None`` selects the deprecated legacy
        path, which would have returned a *tuple* — breaking the dict return documented above —
        and raised its warning against this line instead of the caller's (#987, Codex r5). Same
        attribution problem that moved ``make_drawing`` off that path.
        """
        stem = stem or self._opts["out"] or self._opts["number"].lower()
        return self.build().export(stem, formats=("pdf",) if formats is None else formats, dpi=dpi)
