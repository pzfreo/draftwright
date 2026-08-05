"""ir — the part-drawing compiler's intermediate representation (ADR 0008).

The narrow waist between recognition and dimensioning. Two protocols carry the
weight:

- `DimParameter` — the universal currency of dimensioning: one measurable quantity
  with a `kind` (diameter / length / depth / …), a semantic `role` (bore /
  counterbore / step / boss / …), the value, the model-space extent it spans, and
  the datums it is measured from. **It carries no rendered label** — formatting
  (and GD&T symbols, which are drawn as geometry, not font text — the pinned font
  has no ⌴/⌵/↧ glyphs) is a renderer concern. `display()` gives a font-safe text
  form for debug/tests.
- `Feature` — anything dimensionable. It exposes `parameters()` and `references()`.
  **Adding a new shape is adding a new `Feature` type** (Open/Closed).

`PartModel` is the whole-part IR the planner consumes. The planner groups a
feature's parameters so compound callouts (a hole's bore + counterbore + depth)
stay one callout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, runtime_checkable

from draftwright._geometry import (
    _axis_direction_is_aligned,
    _canonical_axis_direction,
    _canonical_axis_span,
    _fmt,
)

if TYPE_CHECKING:
    from draftwright.fits import FitClass

Point = tuple[float, float, float]
ParamKind = Literal["diameter", "length", "depth", "radius", "angle", "location", "thread"]
AUTHORED_DIMENSION_KINDS = frozenset(
    {
        "linear",
        "diameter",
        "radius",
        "angular",
        "curved_dist",
        "oriented",
        "curve_length",
        "thickness",
    }
)
# `role` is the semantic origin of the measurement (open set — new features add
# roles): "bore", "counterbore", "spotface", "od", "step", "boss", "thread",
# "pattern", "slot", "envelope", "location", …
Role = str
# The semantic identity of one addressable measurement (ADR 0016). A readable string,
# not an opaque token: it surfaces in diagnostics, lint messages and (later) emitted
# `dimension(...)` lines, and must stay stable across re-detection and planner changes.
# See :attr:`DimParameter.parameter_id` for how it is derived.
ParameterId = str
#: The measurement vocabulary a caller can name — what `Sheet.dimension(feature, role)` and
#: `add_dimension` accept, spelled as PARAMETER IDS, which is what the name says (#963).
#: Distinct from `ParameterId` above: that one is the open IR alias, this is the closed
#: set a caller may write. What follows is the rest of the rationale.
#: `add_dimension` accept (#963). Closed where `Role` above is open, and deliberately so: the
#: IR must stay extensible for new detectors, but a *caller* can only name a measurement that
#: exists today, and annotating that as a bare `str` gave no completion, no type checking and
#: no way to see the options without running the code.
#:
#: **The parameter id is the canonical spelling.** The bare role (`"bore"`) also resolves, but
#: it is the FAMILY spelling — it selects every parameter carrying that role, which on `step`
#: meant `dimension(step, "step")` silently declared both `step.length` and `step.diameter`.
#: In an authored set, whose whole semantics is that omission means suppression, quietly
#: declaring an extra measurement is the mirror image of the rule. So `_resolve_measurement`
#: now refuses a role naming more than one, deprecates the rest, and normalises what it stores
#: to the id — which also means an emitted script no longer changes dialect with how its
#: source model was authored. Bare roles are therefore NOT listed here: they are tolerated
#: input on the way out, not the spelling to reach for.
#:
#: `"location"` is here but is not a `DimParameter`: it is synthesised from the planner plus a
#: datum, and only exists on features `planner.location_datum` deems eligible (#876), so the
#: runtime check in `_resolve_measurement` stays authoritative. This alias is an authoring aid,
#: not a second decision site.
#:
#: Kept in step with the IR by `tests/test_dimension_role_vocabulary.py`, which derives the
#: truth from the `DimParameter(...)` construction sites rather than trusting this list.
#: A discriminated variant is spelled in full (`grid_pitch.length.row`); `axis=` still
#: works with the bare role, for scripts that already wrote it that way.
DimensionParameterId = Literal[
    "bolt_circle.diameter",
    "bore.depth",
    "bore.diameter",
    "boss.diameter",
    "boss_height.length",
    "chamfer.length",
    "counterbore.depth",
    "counterbore.diameter",
    "countersink.angle",
    "countersink.diameter",
    "depth.length",
    "fillet.radius",
    "flat.length",
    "grid_pitch.length.col",
    "grid_pitch.length.row",
    "groove.diameter",
    "groove.length",
    "height.length",
    "od.diameter",
    "pad_length.length",
    "pad_width.length",
    "pitch.length",
    "pocket_depth.length",
    "pocket_length.length",
    "pocket_width.length",
    "slot_length.length",
    "slot_width.length",
    "spotface.depth",
    "spotface.diameter",
    "step.diameter",
    "step.length",
    "step_height.length",
    "step_position.length",
    "thickness.length",
    "width.length",
    # synthesised, not a DimParameter (see above)
    "location",
]


@dataclass(frozen=True)
class Frame:
    """A feature's location and dominant orientation in part space (the axis
    letter is enough for now; it generalises to a direction vector)."""

    origin: Point
    axis: str


@dataclass(frozen=True)
class Datum:
    """A reference the planner measures from (a face/axis/point)."""

    id: str
    kind: Literal["plane", "axis", "point"]
    at: Point


@dataclass(frozen=True)
class DimParameter:
    """One measurable quantity a drawing must show. No rendered label — see module
    docstring; use :func:`display` for font-safe text."""

    kind: ParamKind
    role: Role
    value: float
    span: tuple[Point, Point] | None = None
    refs: tuple[str, ...] = ()
    # An authored ± tolerance (ADR 0011 §4 / P2a): a symmetric ``float`` or an
    # ``(lower, upper)`` limit pair; or a resolved fit class (``FitClass``, P2a.2) that
    # renders its own class-code / deviation suffix; ``None`` when untoleranced. Set by the
    # planner from the caller's ``decorations`` — geometry never supplies it.
    tolerance: float | tuple[float, float] | FitClass | None = None
    # A semantic discriminator for measurements ``(role, kind)`` cannot tell apart
    # (ADR 0016 identity, tier 2). Today's sole instance is a grid pattern's two pitches
    # (``"row"`` / ``"col"``). ``None`` wherever role + kind already identify the thing.
    discriminator: str | None = None

    @property
    def parameter_id(self) -> ParameterId:
        """This parameter's semantic identity (ADR 0016) — ``role.kind``, plus the
        discriminator where one is needed: ``bore.diameter``, ``bore.depth``,
        ``grid_pitch.length.row``.

        **Derived, never hand-authored**, so the ~40 `DimParameter(...)` construction
        sites cannot drift a literal away from the ``role=`` beside it.

        ``kind`` is included *always*, not only where it disambiguates. Dropping it when
        a role happens to be unique within its feature would make an id depend on its
        **sibling** parameters — so adding a new parameter to a feature would silently
        repoint every intent aimed at an existing one, destroying the stability the id
        exists for. Uniform costs some prettiness (``step_height.length``) and buys the
        one property that is load-bearing.
        """
        base = f"{self.role}.{self.kind}"
        return f"{base}.{self.discriminator}" if self.discriminator else base


def display(p: DimParameter) -> str:
    """A font-safe text form of a parameter (uses only glyphs the pinned font has;
    GD&T symbols are the renderer's job). For debug and tests, not output."""
    if p.kind == "diameter":
        return f"ø{_fmt(p.value)}"
    if p.kind == "depth":
        return f"{_fmt(p.value)} deep"
    return _fmt(p.value)


@runtime_checkable
class Feature(Protocol):
    """Anything dimensionable. Implementations are frozen dataclasses, so ``kind``
    is a class variable and ``frame`` is read-only."""

    kind: ClassVar[str]

    @property
    def frame(self) -> Frame: ...

    def parameters(self) -> list[DimParameter]: ...

    def references(self) -> list[Datum]: ...


@dataclass(frozen=True)
class HoleFeature:
    """A drilled hole — bore + optional counterbore / spotface steps. The bore,
    counterbore, and spotface share one feature so the planner renders them as one
    compound callout. ``cbore``/``spotface`` are ``(diameter, depth)`` or ``None``
    (plain tuples — the IR stays decoupled from the recogniser's types)."""

    #: The compiled stem this feature's position is minted under (#966). Declared HERE,
    #: beside the feature, rather than in a table in the planner, because the planner's
    #: table was one of TWO owners: the mint sites in `model/compiled.py` and the readers
    #: in `annotations/` spelled the same name again as a literal, so renaming the table
    #: made a dimension VANISH rather than change name. One owner of the stem.
    #:
    #: The SUFFIX is still chosen at each mint site, which is how
    #: `location_pocket.location` and `location_slot.length` came to disagree — that half
    #: of #966 is not fixed here. This declaration owns the stem and nothing more.
    LOCATION_STEM: ClassVar[str] = "location"

    #: The stem a SIDE-DRILLED bore's two positions are minted under — a separate
    #: measurement from the Z-normal ladder above (bounding-box datum, one entry per
    #: measured axis, compiled in `_compile_off_axis_hole_locations`), so it is a separate
    #: declaration rather than a suffix rule over `LOCATION_STEM`. Declared for the same
    #: reason: compiler and renderer both read it instead of restating the literal.
    LOCATION_OFF_AXIS_STEM: ClassVar[str] = "location_off_axis"

    frame: Frame
    diameter: float
    depth: float | None
    through: bool
    count: int = 1
    # Member locations when identical holes are grouped by machining spec into one
    # ``count×`` callout (the engine's grouped-callout rule). Empty for a singleton
    # (the one hole sits at ``frame.origin``). Consumers iterate ``members or
    # (frame.origin,)`` so a centre mark / location dim lands on every hole.
    members: tuple[Point, ...] = ()
    cbore: tuple[float, float] | None = None
    spotface: tuple[float, float] | None = None
    # A countersink (flat-head screw seat): ``(major_diameter, included_angle°)`` or
    # ``None`` — plain tuple, the IR stays decoupled from the recogniser's type (#558).
    csink: tuple[float, float] | None = None
    # A thread spec (tap/thread), e.g. ``"M3x0.5"`` — free text folded onto the hole's
    # compound callout (#764). A declaration-only aspect (ADR 0011 side-layer): threads
    # are cosmetic, rarely modelled as geometry, so there is no recogniser — declare + emit.
    thread: str | None = None
    kind: ClassVar[str] = "hole"

    def parameters(self) -> list[DimParameter]:
        # Location is the group's anchor (Feature.frame), not a parameter.
        ps = [DimParameter("diameter", "bore", self.diameter)]
        if not self.through and self.depth is not None:
            ps.append(DimParameter("depth", "bore", self.depth))
        if self.cbore is not None:
            cd, cdp = self.cbore
            ps.append(DimParameter("diameter", "counterbore", cd))
            ps.append(DimParameter("depth", "counterbore", cdp))
        if self.spotface is not None:
            sd, sdp = self.spotface
            ps.append(DimParameter("diameter", "spotface", sd))
            ps.append(DimParameter("depth", "spotface", sdp))
        if self.csink is not None:
            csd, csa = self.csink
            ps.append(DimParameter("diameter", "countersink", csd))
            ps.append(DimParameter("angle", "countersink", csa))
        return ps

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class StepFeature:
    """One axial segment of a turned profile — its length and its OD."""

    frame: Frame
    length: float
    diameter: float
    span: tuple[Point, Point]
    # An EXTERNAL thread spec, e.g. ``"M3x0.5"`` — free text appended to the OD (⌀) callout
    # (#859). The turned analog of ``HoleFeature.thread``: a declaration-only aspect, threads
    # are cosmetic and rarely modelled as geometry, so there is no recogniser — declare + render.
    thread: str | None = None
    kind: ClassVar[str] = "step"

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "step", self.length, span=self.span),
            DimParameter("diameter", "step", self.diameter),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PatternFeature:
    """A recognised hole pattern (bolt circle / linear array / rect grid) =
    ``count`` × a `member` hole arranged by the pattern. It composes the member
    `HoleFeature` (so the member's bore + counterbore/spotface/depth all come
    along — a counterbored bolt circle keeps its counterbore) and adds the
    pattern-defining dims (BCD / pitch / grid pitches). The member holes are NOT
    emitted individually (the engine's grouped ``n× ø`` callout)."""

    #: The compiled stem this feature's position is minted under — see
    #: :attr:`HoleFeature.LOCATION_STEM` for why it is declared here (#966).
    LOCATION_STEM: ClassVar[str] = "location_pattern"

    frame: Frame
    pattern: str  # "bolt_circle" | "linear" | "grid"
    count: int
    member: HoleFeature
    members: tuple[Point, ...] = ()  # ordered member-hole centres (raw arrangement)
    bcd: float | None = None  # bolt-circle diameter
    pitch: float | None = None  # linear pitch
    direction: tuple[float, float, float] | None = None  # linear array axis
    grid: tuple[float, float] | None = None  # (row_pitch, col_pitch)
    rows: int | None = None
    cols: int | None = None
    angle: float | None = None  # grid lattice rotation (degrees)
    kind: ClassVar[str] = "pattern"

    def parameters(self) -> list[DimParameter]:
        ps = list(self.member.parameters())  # bore (+ counterbore / spotface / depth)
        if self.bcd is not None:
            ps.append(DimParameter("diameter", "bolt_circle", self.bcd))
        if self.pitch is not None:
            ps.append(DimParameter("length", "pitch", self.pitch))
        if self.grid is not None:
            rp, cp = self.grid
            # Same role AND kind, semantically distinct — the ADR 0016 tier-2 case that
            # forces a discriminator. "row"/"col" (not "x"/"y"): `angle` may rotate the
            # lattice, so a row pitch is not an X pitch in general. Mapping a user-facing
            # `axis=` selector onto these is the facade's job, not the IR's.
            ps.append(DimParameter("length", "grid_pitch", rp, discriminator="row"))
            ps.append(DimParameter("length", "grid_pitch", cp, discriminator="col"))
        return ps

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class EnvelopeFeature:
    """The part's overall bounding dimensions — width (X), height (Z), depth (Y) —
    for a prismatic part. Each is a length parameter whose span is a bbox edge, so
    the renderer places it outside the matching view."""

    frame: Frame
    width: float
    height: float
    depth: float
    bbox_min: Point
    bbox_max: Point
    kind: ClassVar[str] = "envelope"

    def parameters(self) -> list[DimParameter]:
        x0, y0, z0 = self.bbox_min
        x1, y1, z1 = self.bbox_max
        return [
            DimParameter("length", "width", self.width, span=((x0, y0, z0), (x1, y0, z0))),
            DimParameter("length", "height", self.height, span=((x1, y0, z0), (x1, y0, z1))),
            DimParameter("length", "depth", self.depth, span=((x0, y0, z0), (x0, y1, z0))),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class SlotFeature:
    """A milled slot / reduced across-flats section — width (the defining size,
    across ``width_axis``) + length (along ``long_axis``). Carries the slot's
    in-plane geometry so the renderer can place the size + position dims in the
    view the two axes span (the recogniser's `Slot`, normalised into the IR)."""

    #: The compiled stem this feature's position is minted under — see
    #: :attr:`HoleFeature.LOCATION_STEM` for why it is declared here (#966).
    LOCATION_STEM: ClassVar[str] = "location_slot"

    frame: Frame
    width_axis: str
    long_axis: str
    width: float
    length: float
    w_center: float
    lo: float
    hi: float
    kind: ClassVar[str] = "slot"

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "slot_width", self.width),
            DimParameter("length", "slot_length", self.length),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PocketFeature:
    """A blind rectangular recess — a floored slot/pocket (#148a). A `SlotFeature`
    with a third defining size, the ``depth`` from the open face to the floor; the
    in-plane geometry (width/length/position) mirrors a slot so the renderer places
    the callout in the view the two in-plane axes span (the recogniser's `Pocket`,
    normalised into the IR)."""

    #: The compiled stem this feature's position is minted under — see
    #: :attr:`HoleFeature.LOCATION_STEM` for why it is declared here (#966).
    LOCATION_STEM: ClassVar[str] = "location_pocket"

    frame: Frame
    width_axis: str
    long_axis: str
    width: float
    length: float
    depth: float
    w_center: float
    lo: float
    hi: float
    edge_anchored: bool = False
    kind: ClassVar[str] = "pocket"

    @property
    def depth_axis(self) -> str:
        """The axis normal to the opening (into the material) — the view the callout
        reads in is the one normal to it."""
        return next(a for a in "xyz" if a not in (self.width_axis, self.long_axis))

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "pocket_width", self.width),
            DimParameter("length", "pocket_length", self.length),
            DimParameter("length", "pocket_depth", self.depth),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PadFeature:
    """A bounded rectangular raised island.

    The footprint mirrors the slot vocabulary so the shared in-plane dimension
    renderer can place its two sizes. Height remains owned by the correlated
    prismatic level ladder, avoiding double-dimensioning the same Z rise.
    """

    #: The compiled stem this feature's position is minted under — see
    #: :attr:`HoleFeature.LOCATION_STEM` for why it is declared here (#966).
    LOCATION_STEM: ClassVar[str] = "location_pad"

    frame: Frame
    width_axis: str
    long_axis: str
    width: float
    length: float
    w_center: float
    lo: float
    hi: float
    z0: float
    z1: float
    kind: ClassVar[str] = "pad"

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "pad_width", self.width),
            DimParameter("length", "pad_length", self.length),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PocketPatternFeature:
    """``count`` × an identical blind pocket in a linear/grid array — the recess analog
    of `PatternFeature` (#841). Composes a representative `member` `PocketFeature` (its
    width × length × depth come along) and adds the array-defining pitch dims. The member
    pockets are NOT emitted individually: one grouped ``N× W × L × D DEEP`` callout plus
    the ``(n-1)× pitch`` dim(s). ``frame.axis`` is the opening-normal (depth) axis, so the
    callout reads in the view normal to it (``_END_ON`` z→plan / x→side / y→front — the
    same map `render_pockets` uses)."""

    #: The compiled stem this feature's position is minted under — see
    #: :attr:`HoleFeature.LOCATION_STEM` for why it is declared here (#966).
    LOCATION_STEM: ClassVar[str] = "location_pocket_pattern"

    frame: Frame
    pattern: str  # "linear" | "grid"
    count: int
    member: PocketFeature
    members: tuple[Point, ...] = ()  # ordered member-pocket centres
    pitch: float | None = None  # linear pitch
    direction: tuple[float, float, float] | None = None  # linear array axis
    grid: tuple[float, float] | None = None  # (row_pitch, col_pitch)
    rows: int | None = None
    cols: int | None = None
    angle: float | None = None  # grid lattice rotation (degrees)
    kind: ClassVar[str] = "pocket_pattern"

    def parameters(self) -> list[DimParameter]:
        ps = list(self.member.parameters())  # pocket width + length + depth
        if self.pitch is not None:
            ps.append(DimParameter("length", "pitch", self.pitch))
        if self.grid is not None:
            rp, cp = self.grid
            # Same role AND kind, semantically distinct — the ADR 0016 tier-2 case that
            # forces a discriminator. "row"/"col" (not "x"/"y"): `angle` may rotate the
            # lattice, so a row pitch is not an X pitch in general. Mapping a user-facing
            # `axis=` selector onto these is the facade's job, not the IR's.
            ps.append(DimParameter("length", "grid_pitch", rp, discriminator="row"))
            ps.append(DimParameter("length", "grid_pitch", cp, discriminator="col"))
        return ps

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class SlotPatternFeature:
    """``count`` × an identical milled slot in a linear/grid array — the through-slot analog of
    `PocketPatternFeature` (#841). Composes a representative `member` `SlotFeature` (its width ×
    length come along; a slot has NO depth) and adds the array pitch dims. The member slots are
    NOT emitted individually: one grouped ``N× SLOT W × L`` leader plus the ``(n-1)× pitch``
    dim(s). ``frame.axis`` is the slot's THROUGH axis (the one neither width nor long), so the
    callout reads in the view normal to it (the same view the member slots' dims read in)."""

    #: The compiled stem for the pattern's datum location (#1018 Gate 2). A Z-normal slot
    #: pattern is located in X and Y; the compiler gives those page dimensions distinct
    #: discriminators so one direction cannot certify the other.
    LOCATION_STEM: ClassVar[str] = "location_slot_pattern"

    frame: Frame
    pattern: str  # "linear" | "grid"
    count: int
    member: SlotFeature
    members: tuple[Point, ...] = ()  # ordered member-slot centres
    pitch: float | None = None  # linear pitch
    direction: tuple[float, float, float] | None = None  # linear array axis
    grid: tuple[float, float] | None = None  # (row_pitch, col_pitch)
    rows: int | None = None
    cols: int | None = None
    angle: float | None = None  # grid lattice rotation (degrees)
    kind: ClassVar[str] = "slot_pattern"

    def parameters(self) -> list[DimParameter]:
        ps = list(self.member.parameters())  # slot width + length (no depth)
        if self.pitch is not None:
            ps.append(DimParameter("length", "pitch", self.pitch))
        if self.grid is not None:
            rp, cp = self.grid
            # Same role AND kind, semantically distinct — the ADR 0016 tier-2 case that
            # forces a discriminator. "row"/"col" (not "x"/"y"): `angle` may rotate the
            # lattice, so a row pitch is not an X pitch in general. Mapping a user-facing
            # `axis=` selector onto these is the facade's job, not the IR's.
            ps.append(DimParameter("length", "grid_pitch", rp, discriminator="row"))
            ps.append(DimParameter("length", "grid_pitch", cp, discriminator="col"))
        return ps

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class BossFeature:
    """An external cylindrical boss/OD — its diameter and optional axial height."""

    frame: Frame
    diameter: float
    height: float | None = None
    span: tuple[Point, Point] | None = None
    # An external thread spec appended to the OD callout (#859) — see ``StepFeature.thread``.
    thread: str | None = None
    kind: ClassVar[str] = "boss"

    def parameters(self) -> list[DimParameter]:
        params = [DimParameter("diameter", "boss", self.diameter)]
        if self.height is not None:
            params.append(DimParameter("length", "boss_height", self.height, span=self.span))
        return params

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class ChamferFeature:
    """A chamfered (bevelled) edge (#560), called out ``C{leg}`` for an equal-leg 45°
    chamfer or ``{leg} × {angle}°`` otherwise. ``axis`` is the chamfered edge's direction;
    ``leg1``/``leg2`` are the cut depths into the two adjacent faces (equal for 45°);
    ``angle`` is the chamfer angle (degrees). The recogniser recovers both legs from the
    geometry, so an asymmetric chamfer is distinguished from an equal-leg one — the size
    is not estimated from the rendered view (#560 acceptance)."""

    frame: Frame
    axis: str
    leg1: float
    leg2: float
    angle: float
    kind: ClassVar[str] = "chamfer"

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("length", "chamfer", self.leg1)]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class FilletFeature:
    """A rounded (filleted) edge (#561), called out ``R{radius}`` (grouped ``n× R{radius}``
    for equal radii). ``axis`` is the rounded edge's direction; ``radius`` is the fillet
    radius, recovered from the cylinder geometry — not estimated from the rendered view
    (#561 acceptance). The arc analog of :class:`ChamferFeature`."""

    frame: Frame
    axis: str
    radius: float
    kind: ClassVar[str] = "fillet"

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("radius", "fillet", self.radius)]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class FlatFeature:
    """A machined flat on round stock (#148b), called out by its across-flats size.
    ``axis`` is the turning axis the stock is coaxial about; ``across`` is the across-flats
    size — flat-to-flat for a face opposed across the axis (double-D / hex A/F), else
    flat-to-opposite-OD (the D height). Recovered from the geometry, not the rendered
    view."""

    frame: Frame
    axis: str
    across: float
    #: The stock axis line's canonical in-plane position — see
    #: :class:`~draftwright.recognition.flats.Flat`. Two flats share an A/F definition only if
    #: they share direction, line and span; the axis letter alone cannot tell a double-D's
    #: two faces from two parallel or slanted regions (#1013/#1036).
    axis_line: tuple[float, float] = (0.0, 0.0)
    #: The owning stock's axial extent along ``axis_direction``. Coaxial stacked stock shares
    #: a direction and line, so those alone merged independent definitions.
    stock_span: tuple[float, float] = (0.0, 0.0)
    #: Real stock direction, canonicalised with the named dominant component positive. With
    #: the perpendicular-foot ``axis_line`` and axial ``stock_span``, this distinguishes
    #: slanted stock regions without presentation inference (#1036).
    axis_direction: Point | None = None
    kind: ClassVar[str] = "flat"

    def __post_init__(self) -> None:
        direction = self.axis_direction
        object.__setattr__(
            self,
            "stock_span",
            _canonical_axis_span(self.axis, direction, self.stock_span),
        )
        object.__setattr__(
            self,
            "axis_direction",
            _canonical_axis_direction(self.axis, direction),
        )

    @property
    def axis_aligned(self) -> bool:
        return _axis_direction_is_aligned(self.axis, self.axis_direction)

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("length", "flat", self.across)]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class GrooveFeature:
    """A turned / circlip groove on round stock (#148c) — an annular channel dimensioned by
    its ``width`` (axial span) and floor ``diameter``. ``axis`` is the turning axis the stock
    is coaxial about. Recovered from the OD band geometry (a strict local-minimum diameter),
    not the rendered view; distinct from a slot (radial walls) and a plain step (monotonic
    OD)."""

    frame: Frame
    axis: str
    width: float
    diameter: float
    kind: ClassVar[str] = "groove"

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "groove", self.width),
            DimParameter("diameter", "groove", self.diameter),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class StepLevelFeature:
    """The prismatic height profile — horizontal face levels (Z) dimensioned from the
    base, stacked right of the front view (#237). The turned analogue is `StepFeature`
    (length + OD per segment); this is the prismatic *height* ladder. ``levels`` are the
    interior step Z-coords (ascending); ``base`` is the part's bottom (bbox min Z).

    ``shoulders`` are the in-plane step POSITIONS (#555) — ``(axis, position)`` where a
    step/rebate changes height — so the part is fully constrained (a step is located
    along its axis, not just given two heights). ``datum`` is the part-space min corner
    each shoulder position is measured from (a shoulder at ``pos`` on ``axis`` shows
    ``pos - datum[axis]``)."""

    frame: Frame
    base: float
    levels: tuple[float, ...]
    shoulders: tuple[tuple[str, float], ...] = ()
    datum: Point = (0.0, 0.0, 0.0)
    kind: ClassVar[str] = "step_level"

    def parameters(self) -> list[DimParameter]:
        # Both height and position are correlated SETS routed as a whole through their
        # auto-pass renderers (render_height_ladder / render_step_positions), like the
        # turned-step chain — never flattened into per-value span dims. So neither carries
        # a span; a single `role=` intent rebuilds the whole ladder / all shoulders.
        _di = {"x": 0, "y": 1, "z": 2}
        return [DimParameter("length", "step_height", z - self.base) for z in self.levels] + [
            DimParameter("length", "step_position", pos - self.datum[_di[axis]])
            for axis, pos in self.shoulders
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PlateFeature:
    """A thin slab's thickness on a multi-plate prismatic (#559) — the base plate of
    an L-bracket, an upright wall, a rib. ``axis`` is the thin (thickness) axis; the
    slab runs from ``lo`` to ``hi`` along it (``hi - lo`` is the thickness), centred at
    ``u``/``v`` on the other two axes (in axis order). Unlike ``StepLevelFeature`` (Z
    staircase heights from the base) and ``EnvelopeFeature`` (the full bbox), this is a
    *partial* extent along any axis — a plate that spans less than the whole part on its
    thin axis, so ``dim_height``/the envelope do not already cover it."""

    frame: Frame
    axis: str
    lo: float
    hi: float
    u: float
    v: float
    kind: ClassVar[str] = "plate"

    def _span(self) -> tuple[Point, Point]:
        i = "xyz".index(self.axis)
        oi = [j for j in (0, 1, 2) if j != i]
        p0 = [0.0, 0.0, 0.0]
        p1 = [0.0, 0.0, 0.0]
        p0[i], p1[i] = self.lo, self.hi
        p0[oi[0]] = p1[oi[0]] = self.u
        p0[oi[1]] = p1[oi[1]] = self.v
        return (tuple(p0), tuple(p1))  # type: ignore[return-value]

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("length", "thickness", self.hi - self.lo, span=self._span())]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class RotationalFeature:
    """A turned/rotational part's axial furniture (#237): the outer diameter, the
    rotation-axis centrelines, and the concentric bore diameters (dimensioned by
    centred leaders). Its presence marks the part rotational — the renderer places the
    OD dim + centrelines + bore leaders from it."""

    frame: Frame  # at the rotation axis
    od: float
    bores: tuple[float, ...] = ()  # concentric bore diameters, in display order
    kind: ClassVar[str] = "rotational"

    def __post_init__(self):
        """Bores are Z-axis only, enforced HERE because the IR is the one waist (ADR 0015).

        The rule is real: detection carries bores only when `od_axis == "z"`, and
        `render_rotational` leaders them in that branch alone — on a part turned about X or Y
        the bore is dimensioned by the hole pass instead. A non-Z `bores=` therefore produced
        `bore.diameter` parameters that planned, mirrored into a generated script, and drew
        nothing, with lint clean (#949 review r4/r5).

        It started life in `declare.rotational`, which left the sanctioned ADR 0011 route —
        a hand-built `PartModel` through `Sheet.add` or `build_drawing(model=…)` — wide open,
        and let the emitter write a `sheet.rotational(bores=…, axis="x")` line that the
        declare layer would then reject. Guarding the constructor rather than the type is the
        two-places-to-decide defect ADR 0016 names, so the invalid state is simply not
        representable and every route inherits one answer. #952 tracks lifting the engine
        restriction (or recording the hole-pass split in ADR 0015 and keeping this forever).
        """
        if self.bores and self.frame.axis != "z":
            raise ValueError(
                f"a rotational feature carries concentric bores only when turned about Z "
                f"(got axis={self.frame.axis!r} with bores={self.bores}): nothing draws them "
                "on a cross-axis part, so the dimensions would plan and then vanish. Declare "
                "the bore with sheet.hole(...), which is what dimensions it there — see #952."
            )

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("diameter", "od", self.od),
            *[DimParameter("diameter", "bore", b) for b in self.bores],
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class AuthoredDimension:
    """A pre-authored drafting dimension imported from an external semantic source.

    This is the concept-shaped IR for AP242 dimensional PMI: the source file may call it
    PMI, but the drawing model sees an authored linear/diameter/radius/etc. dimension with
    baked label and referenced geometry. The normal dimension planner does not derive or
    duplicate it, so ``parameters()`` is empty; renderers consume it directly while keeping
    the source/provenance fields for round-trip and diagnostics."""

    frame: Frame
    dimension_kind: str  # "linear" | "diameter" | "radius" | "angular" | ...
    value: float
    label: str
    dominant_axis: str
    upper_tol: float | None = None
    lower_tol: float | None = None
    ref_bbox: tuple[float, float, float, float, float, float] | None = None
    ref_pts: tuple[Point, ...] = ()
    source: str = "ap242_pmi"
    source_kind: str | None = None
    kind: ClassVar[str] = "authored_dimension"

    @property
    def pmi_kind(self) -> str:
        """Compatibility alias for the existing AP242 renderer until it is renamed."""
        return self.dimension_kind

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PmiFeature:
    """Raw AP242 PMI fallback for records not yet lowered to drafting concepts.

    Dimensional AP242 PMI should become :class:`AuthoredDimension`; GD&T/datum/surface
    records should eventually lower to ``ControlFrame`` / ``DatumRef`` / ``Finish``. This
    type remains as an explicit provenance-preserving escape hatch so unsupported records
    are visible instead of silently lost."""

    frame: Frame
    pmi_kind: str  # the PMI category: "linear" | "diameter" | "radius" | "angular" | ...
    value: float
    label: str
    dominant_axis: str
    ref_bbox: tuple[float, float, float, float, float, float] | None = None
    ref_pts: tuple[Point, ...] = ()
    kind: ClassVar[str] = "pmi"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class ControlFrame:
    """A geometric-tolerance feature control frame (ISO 1101) declared on the drawing
    (ADR 0011 §4 aspect side-layer, #61). Placed as a first-class ADR 0009 corridor
    candidate by ``render_gdt`` — NOT through the dimension planner, so ``parameters()``
    is empty (like :class:`PmiFeature`). The target ``(view, side)`` strip and the
    model-space site (``frame.origin``) the leader hangs from are carried explicitly:
    render-core places into that strip's corridor; the Sheet layer (P2c) computes them
    from a build123d face."""

    frame: Frame  # the site the leader hangs from + its axis
    characteristic: str  # ISO 1101 lowercase name: "position" | "flatness" | ...
    tolerance: str  # the tolerance value text, e.g. "0.1"
    view: str  # target view: "front" | "side" | "plan"
    side: str  # target strip: "above" | "below" | "left" | "right"
    datums: tuple[str, ...] = ()
    diameter: bool = False  # ⌀ prefix on the tolerance zone
    modifier: str | None = None  # material-condition modifier: "M" | "L" | "P" | ...
    # The IR feature this frame decorates — recorded as provenance (ADR 0010); ``None``
    # leaves it feature-less. Untyped to avoid an import cycle with the geometric features.
    origin: object | None = None
    kind: ClassVar[str] = "control_frame"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class DatumRef:
    """A datum feature symbol (ISO 5459) — a boxed letter tagging a surface/axis as a
    datum (#61). Placed as an ADR 0009 corridor candidate by ``render_gdt``, not through
    the dimension planner (``parameters()`` is empty)."""

    frame: Frame
    letter: str
    view: str
    side: str
    origin: object | None = None
    kind: ClassVar[str] = "datum_ref"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class Finish:
    """A surface-finish symbol (ISO 1302) — a roughness callout on a surface (#61).
    Placed as an ADR 0009 corridor candidate by ``render_gdt``, not through the
    dimension planner (``parameters()`` is empty)."""

    frame: Frame
    ra: str  # roughness value text, e.g. "3.2" (Ra, µm)
    view: str
    side: str
    origin: object | None = None
    kind: ClassVar[str] = "finish"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class Note:
    """A free-text manufacturing note (#488) hung on a leader to a feature/site — the shop
    callouts detection can't infer (thread specs, ``DEBURR``, chip-relief, knurl). Placed like
    the GD&T items — a first-class ADR 0009 corridor candidate via ``render_gdt`` (its glyph is a
    single-line ``TextBlock``), NOT the dimension planner (``parameters()`` is empty)."""

    frame: Frame
    text: str
    view: str
    side: str
    origin: object | None = None
    kind: ClassVar[str] = "note"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class RequestedDimension:
    """A caller's ``add_dimension(...)`` — *augment the planner's set with this
    measurement* (ADR 0016).

    Referential like every ADR 0016 intent: it names a feature and a role and carries
    **no number**, so the value still comes from the geometry. What it changes is
    *selection*, not derivation — if the planner suppressed this parameter the request
    un-suppresses it; if the planner already emits it the request is a no-op. Overlap
    is deliberately not an error (the #872 idempotence gate): a script should be able to
    ask for a measurement without first knowing whether the rule set already volunteers
    it, or the verb would leak the planner's internals into every caller.

    Emphasis (ADR 0012's ``pin`` / ``priority``) is deliberately NOT here. The engine
    already carries two spellings of "keep this dimension put" — ``Drawing.pin(name)``
    on a placed annotation, and the post-build ``intents`` route that reaches the solve
    through ``render_locations(pinned=…)`` for location dims only. Adding a third,
    pre-build spelling would scatter one concept across three layers, the same way the
    corridor priority scale was scattered before #894 consolidated it. The convergence
    is tracked separately; this type stays pure *selection*.
    """

    feature: Feature
    role: Role
    #: Discriminator for a role a feature carries more than once — today only a grid
    #: pattern's two pitches (``"row"`` / ``"col"``). ``None`` where the role identifies
    #: the measurement on its own.
    discriminator: str | None = None


@dataclass
class PartModel:
    """The whole-part IR: the oriented part plus its features and datums."""

    bbox: object  # build123d BoundBox
    orientation: str | None  # turning axis if rotational, else None
    features: list[Feature] = field(default_factory=list)
    datums: list[Datum] = field(default_factory=list)
    # Authored aspects the frozen features can't carry (ADR 0011 §4). P2a uses it for
    # per-dimension tolerances: ``{(feature, ParamKind) -> float | (lo, hi)}``. The
    # planner consults it to set ``DimParameter.tolerance``; empty on a detected model.
    decorations: dict = field(default_factory=dict)
    # Caller-requested augmenting measurements (ADR 0016 / #872) — the planner's
    # *intent input*. Kept distinct from `decorations` on purpose: a decoration enriches
    # a dimension the planner already chose, a request changes WHICH dimensions it
    # chooses. Empty on a detected model.
    requested_dimensions: tuple[RequestedDimension, ...] = ()
    # The COMPLETE authored dimension set (ADR 0016 / #874/#876), or ``None`` for the
    # planner's automatic set. The two are the model's only dimension sources and are
    # mutually exclusive — omission is only meaningful inside a set declared complete,
    # which is exactly why the source has to be stated rather than inferred.
    #
    # Suppression by omission MARKS rather than filters (#875): a parameter outside the
    # authored set is planned with ``suppressed=True`` and keeps its value, so the group
    # retains its engineering data and a later pass can still see what was left out.
    authored_dimensions: tuple[RequestedDimension, ...] | None = None
