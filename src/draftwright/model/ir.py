"""ir — the part-drawing compiler's intermediate representation (ADR 1 (was 0008)).

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
from math import atan2, cos, hypot, isclose, isfinite, pi
from numbers import Real
from typing import TYPE_CHECKING as TYPE_CHECKING
from typing import ClassVar, Literal, cast
from typing import Protocol as Protocol
from typing import runtime_checkable as runtime_checkable

from draftwright import contract_values as contract_values
from draftwright._geometry import (
    _axis_direction_is_aligned,
    _canonical_axis_direction,
    _canonical_axis_span,
    quantised_radius_agrees,
    quantised_span_agrees,
)
from draftwright._geometry import (
    _fmt as _fmt,
)
from draftwright.blend_contract import register_blend_ir_types, validate_blend_fields
from draftwright.feature_identity import (
    register_envelope_feature_type as register_envelope_feature_type,
)
from draftwright.feature_identity import (
    register_oriented_slot_feature_type,
)
from draftwright.model.dimension_intent import (
    AUTHORED_DIMENSION_KINDS as AUTHORED_DIMENSION_KINDS,
)
from draftwright.model.dimension_intent import (
    PLACEMENT_SIDES as PLACEMENT_SIDES,
)
from draftwright.model.dimension_intent import (
    PLACEMENT_VIEWS as PLACEMENT_VIEWS,
)
from draftwright.model.dimension_intent import (
    DimensionParameterId,
    Role,
    _authored_dimension_target_view,
    _validate_authored_dimension_placement,
)
from draftwright.model.dimension_intent import (
    ParameterId as ParameterId,
)
from draftwright.model.dimension_intent import (
    ParamKind as ParamKind,
)
from draftwright.model.dimension_intent import (
    _linear_projection_view as _linear_projection_view,
)
from draftwright.model.dimension_intent import (
    validate_placement_intent as _validate_placement_intent,
)
from draftwright.model.ir_foundation import (
    AngularReference as AngularReference,
)
from draftwright.model.ir_foundation import (
    CircularReference as CircularReference,
)
from draftwright.model.ir_foundation import (
    CylinderSense as CylinderSense,
)
from draftwright.model.ir_foundation import (
    CylindricalReference as CylindricalReference,
)
from draftwright.model.ir_foundation import (
    Datum as Datum,
)
from draftwright.model.ir_foundation import (
    DimParameter as DimParameter,
)
from draftwright.model.ir_foundation import (
    EnvelopeFeature as EnvelopeFeature,
)
from draftwright.model.ir_foundation import (
    Feature as Feature,
)
from draftwright.model.ir_foundation import (
    Frame as Frame,
)
from draftwright.model.ir_foundation import (
    HoleFeature as HoleFeature,
)
from draftwright.model.ir_foundation import (
    KnurlRequirement as KnurlRequirement,
)
from draftwright.model.ir_foundation import (
    NominalRequirement as NominalRequirement,
)
from draftwright.model.ir_foundation import (
    PatternFeature as PatternFeature,
)
from draftwright.model.ir_foundation import (
    PocketFeature as PocketFeature,
)
from draftwright.model.ir_foundation import (
    Point as Point,
)
from draftwright.model.ir_foundation import (
    SlotFeature as SlotFeature,
)
from draftwright.model.ir_foundation import (
    StepFeature as StepFeature,
)
from draftwright.model.ir_foundation import (
    ThreadOperation as ThreadOperation,
)
from draftwright.model.ir_foundation import (
    ThreadRequirement as ThreadRequirement,
)
from draftwright.model.ir_foundation import (
    ToleranceDecoration as ToleranceDecoration,
)
from draftwright.model.ir_foundation import (
    TurnedProfileIdentity as TurnedProfileIdentity,
)
from draftwright.model.ir_foundation import (
    _finite_point3 as _finite_point3,
)
from draftwright.model.ir_foundation import (
    _require_source_identity as _require_source_identity,
)
from draftwright.model.ir_foundation import (
    _strict_finite_real as _strict_finite_real,
)
from draftwright.model.ir_foundation import (
    display as display,
)
from draftwright.model.ir_foundation import grid_has_centre_datum
from draftwright.model.oriented_slot_geometry import validate_feature, validate_passage
from draftwright.section_recess_contract import (
    circular_channel_geometry,
    hex_pocket_geometry,
)
from draftwright.section_recess_contract import (
    validate_pocket_mouth as validate_pocket_mouth,
)


def validate_placement_intent(view: str | None, side: str | None, *, owner: str) -> None:
    """Validate the shared declarative view/strip vocabulary."""
    _validate_placement_intent(view, side, owner=owner)


def validate_authored_dimension_placement(
    dimension_kind: str,
    dominant_axis: str,
    view: str | None,
    side: str | None,
    *,
    owner: str,
    angular_reference: AngularReference | None = None,
    cylindrical_refs=(),
    ref_pts=(),
) -> None:
    """Reject a view/side pair for which the authored-dimension renderer has no candidate."""
    _validate_authored_dimension_placement(
        dimension_kind,
        dominant_axis,
        view,
        side,
        owner=owner,
        angular_reference=angular_reference,
        cylindrical_refs=cylindrical_refs,
        ref_pts=ref_pts,
    )


def authored_dimension_target_view(
    dimension_kind: str,
    dominant_axis: str,
    view: str | None,
    side: str | None,
    angular_reference: AngularReference | None = None,
    ref_pts=(),
) -> str | None:
    """Resolve the principal view selected by an explicit measured-dimension hint."""
    return _authored_dimension_target_view(
        dimension_kind, dominant_axis, view, side, angular_reference, ref_pts
    )


@dataclass(frozen=True)
class OrientedSlotPassage:
    """Kernel-free geometric witness for the through-passage supporting an oriented slot.

    These public geometric facts support declaration, rendering and physical critique.
    Same-run occurrence ownership belongs to the separate recognition ownership ledger;
    equality of these values is not provider occurrence identity.
    """

    origin: Point
    run: Point
    u: Point
    v: Point
    run_interval: tuple[float, float]
    boundary: tuple[tuple[tuple[float, float], float], ...]
    low_capped: bool
    high_capped: bool
    body_key: tuple[float, ...] | None

    def __post_init__(self) -> None:
        validate_passage(self, _strict_finite_real)


@dataclass(frozen=True)
class OrientedSlotFeature:
    """A rectangular through slot with free in-plane width and long directions."""

    frame: Frame
    width_direction: Point
    long_direction: Point
    run_direction: Point
    width: float
    length: float
    passage: OrientedSlotPassage
    kind: ClassVar[str] = "oriented_slot"

    def __post_init__(self) -> None:
        validate_feature(self, Frame, OrientedSlotPassage, _strict_finite_real)

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "oriented_slot_width", self.width),
            DimParameter("length", "oriented_slot_length", self.length),
        ]

    def references(self) -> list[Datum]:
        return []


register_oriented_slot_feature_type(OrientedSlotFeature, OrientedSlotPassage, Frame)


@dataclass(frozen=True)
class ChannelFeature:
    """A floored rectangular channel open through both longitudinal ends.

    The envelope and plate/level scheme own its longitudinal and depth extents.
    This feature owns only the wall-to-wall width, while retaining the full
    geometry needed to identify the two profile transitions it corresponds to.
    """

    frame: Frame
    width_axis: str
    long_axis: str
    width: float
    w_center: float
    lo: float
    hi: float
    d_lo: float
    d_hi: float
    open_sign: int = 1
    kind: ClassVar[str] = "channel"

    @property
    def depth_axis(self) -> str:
        return next(a for a in "xyz" if a not in (self.width_axis, self.long_axis))

    def _span(self) -> tuple[Point, Point]:
        point = {
            self.long_axis: (self.lo + self.hi) / 2,
            self.width_axis: self.w_center,
            self.depth_axis: self.d_hi if self.open_sign > 0 else self.d_lo,
        }
        lo = dict(point)
        hi = dict(point)
        lo[self.width_axis] -= self.width / 2
        hi[self.width_axis] += self.width / 2
        return (
            (lo["x"], lo["y"], lo["z"]),
            (hi["x"], hi["y"], hi["z"]),
        )

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("length", "channel_width", self.width, span=self._span())]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class RectangularBlindSlotFeature:
    """A capped, edge-open rectangular U-section slot.

    This is deliberately distinct from both :class:`SlotFeature` (through, with no floor)
    and :class:`PocketFeature` (closed in-plane). ``axis`` is the penetration/run direction;
    ``open_sign`` selects its source-envelope mouth. ``depth_sign`` selects the material-
    outward opening of the flat-bottomed U section along ``depth_axis``. The provider's
    ``at`` point becomes ``frame.origin`` and is the centre of all three measured spans.
    """

    frame: Frame
    axis: str
    open_sign: int
    width_axis: str
    depth_axis: str
    depth_sign: int
    width: float
    length: float
    depth: float
    kind: ClassVar[str] = "rectangular_blind_slot"

    def __post_init__(self) -> None:
        raw_origin = self.frame.origin
        if (
            not isinstance(raw_origin, tuple)
            or len(raw_origin) != 3
            or any(
                isinstance(component, bool) or not isinstance(component, Real)
                for component in raw_origin
            )
        ):
            raise ValueError("rectangular blind slot frame.origin must be a finite 3-vector")
        origin = _finite_point3("rectangular blind slot frame.origin", self.frame.origin)
        axes = (self.axis, self.width_axis, self.depth_axis)
        if (
            any(not isinstance(axis, str) or axis not in {"x", "y", "z"} for axis in axes)
            or len(set(axes)) != 3
        ):
            raise ValueError(
                "rectangular blind slot axis, width_axis and depth_axis must be a permutation "
                f"of 'xyz' (got {axes!r})"
            )
        if self.frame.axis != self.axis:
            raise ValueError(
                "rectangular blind slot frame axis must equal its run axis "
                f"(got {self.frame.axis!r} and {self.axis!r})"
            )
        object.__setattr__(self, "frame", Frame(origin, self.frame.axis))
        for name, sign in (("open_sign", self.open_sign), ("depth_sign", self.depth_sign)):
            if not isinstance(sign, int) or isinstance(sign, bool) or sign not in (-1, 1):
                raise ValueError(f"rectangular blind slot {name} must be -1 or 1 (got {sign!r})")
        for name in ("width", "length", "depth"):
            raw = getattr(self, name)
            if isinstance(raw, bool) or not isinstance(raw, Real):
                raise ValueError(f"rectangular blind slot {name} must be finite and positive")
            try:
                value = float(raw)
            except (OverflowError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"rectangular blind slot {name} must be finite and positive"
                ) from exc
            if not isfinite(value) or value <= 0:
                raise ValueError(f"rectangular blind slot {name} must be finite and positive")
            object.__setattr__(self, name, value)

    def _span(self, axis: str, value: float) -> tuple[Point, Point]:
        lo = list(self.frame.origin)
        hi = list(self.frame.origin)
        index = "xyz".index(axis)
        lo[index] -= value / 2
        hi[index] += value / 2
        return ((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]))

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter(
                "length",
                "rectangular_blind_slot_width",
                self.width,
                span=self._span(self.width_axis, self.width),
            ),
            DimParameter(
                "length",
                "rectangular_blind_slot_length",
                self.length,
                span=self._span(self.axis, self.length),
            ),
            DimParameter(
                "length",
                "rectangular_blind_slot_depth",
                self.depth,
                span=self._span(self.depth_axis, self.depth),
            ),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class RoundBottomBlindSlotFeature:
    """A capped, edge-open slot with a flat floor joined by equal round sides.

    This is a separate manufacturing family from both a through slot and a rectangular
    blind slot. ``axis``/``open_sign`` identify the mouth-to-terminal run;
    ``width_axis``/``depth_axis``/``depth_sign`` identify the U-section orientation.
    ``flat_width`` is the straight floor between the two equal ``radius`` arcs.  Together
    those two independent measurements define the derived opening width
    ``flat_width + 2 * radius`` and profile depth ``radius``.
    """

    frame: Frame
    axis: str
    open_sign: int
    width_axis: str
    depth_axis: str
    depth_sign: int
    length: float
    radius: float
    flat_width: float
    kind: ClassVar[str] = "round_bottom_blind_slot"

    def __post_init__(self) -> None:
        raw_origin = self.frame.origin
        if (
            not isinstance(raw_origin, tuple)
            or len(raw_origin) != 3
            or any(
                isinstance(component, bool) or not isinstance(component, Real)
                for component in raw_origin
            )
        ):
            raise ValueError("round-bottom blind slot frame.origin must be a finite 3-vector")
        origin = _finite_point3("round-bottom blind slot frame.origin", raw_origin)
        axes = (self.axis, self.width_axis, self.depth_axis)
        if (
            any(not isinstance(axis, str) or axis not in {"x", "y", "z"} for axis in axes)
            or len(set(axes)) != 3
        ):
            raise ValueError(
                "round-bottom blind slot axis, width_axis and depth_axis must be a "
                f"permutation of 'xyz' (got {axes!r})"
            )
        if self.frame.axis != self.axis:
            raise ValueError(
                "round-bottom blind slot frame axis must equal its run axis "
                f"(got {self.frame.axis!r} and {self.axis!r})"
            )
        object.__setattr__(self, "frame", Frame(origin, self.frame.axis))
        for name, sign in (("open_sign", self.open_sign), ("depth_sign", self.depth_sign)):
            if not isinstance(sign, int) or isinstance(sign, bool) or sign not in (-1, 1):
                raise ValueError(f"round-bottom blind slot {name} must be -1 or 1 (got {sign!r})")
        for name in ("length", "radius", "flat_width"):
            raw = getattr(self, name)
            if isinstance(raw, bool) or not isinstance(raw, Real):
                raise ValueError(f"round-bottom blind slot {name} must be finite and positive")
            try:
                value = float(raw)
            except (OverflowError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"round-bottom blind slot {name} must be finite and positive"
                ) from exc
            if not isfinite(value) or value <= 0:
                raise ValueError(f"round-bottom blind slot {name} must be finite and positive")
            object.__setattr__(self, name, value)

    @property
    def width(self) -> float:
        return self.flat_width + 2 * self.radius

    @property
    def depth(self) -> float:
        return self.radius

    def _span(self, axis: str, value: float, *, floor: bool = False) -> tuple[Point, Point]:
        lo = list(self.frame.origin)
        hi = list(self.frame.origin)
        if floor:
            depth_index = "xyz".index(self.depth_axis)
            lo[depth_index] -= self.depth_sign * self.radius / 2
            hi[depth_index] -= self.depth_sign * self.radius / 2
        index = "xyz".index(axis)
        lo[index] -= value / 2
        hi[index] += value / 2
        return ((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]))

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter(
                "length",
                "round_bottom_blind_slot_length",
                self.length,
                span=self._span(self.axis, self.length),
            ),
            DimParameter(
                "length",
                "round_bottom_blind_slot_flat_width",
                self.flat_width,
                span=self._span(self.width_axis, self.flat_width, floor=True),
            ),
            DimParameter("radius", "round_bottom_blind_slot_radius", self.radius),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PadFeature:
    """A bounded, principal-axis rectangular raised island.

    The footprint mirrors the slot vocabulary so the shared in-plane dimension
    renderer can place its two sizes.  ``normal_lo``/``normal_hi`` are the ascending
    attachment-axis bounds and ``direction`` says which end is terminal/material-outward.
    Every orientation owns an explicit ``pad_height.length`` parameter.  A Z pad may also
    create an attachment level in the general prismatic profile, but that level measures the
    body from the drawing datum rather than the pad's terminal-to-attachment height; it is
    not a substitute for the pad requirement.

    The historical ``z0``/``z1`` dataclass fields are retained for public-IR compatibility.
    They name the attachment-axis bounds (and therefore remain literal Z bounds for the
    historical Z-normal case); ``normal_lo``/``normal_hi`` are semantic read-only aliases.
    Orientation-neutral callers use :meth:`bounds` for world-coordinate bounds.
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
    direction: int = 1
    kind: ClassVar[str] = "pad"

    def __post_init__(self) -> None:
        axes = {self.frame.axis, self.width_axis, self.long_axis}
        if axes != {"x", "y", "z"}:
            raise ValueError("PadFeature frame/width/long axes must be distinct x/y/z axes")
        if isinstance(self.direction, bool) or self.direction not in (-1, 1):
            raise ValueError("PadFeature direction must be -1 or 1")
        values = (
            ("width", float(self.width)),
            ("length", float(self.length)),
            ("w_center", float(self.w_center)),
            ("lo", float(self.lo)),
            ("hi", float(self.hi)),
            ("z0", float(self.z0)),
            ("z1", float(self.z1)),
        )
        if not all(isfinite(value) for _name, value in values):
            raise ValueError("PadFeature dimensions and bounds must be finite")
        numeric = dict(values)
        if (
            numeric["width"] <= 0
            or numeric["length"] <= 0
            or not numeric["lo"] < numeric["hi"]
            or not numeric["z0"] < numeric["z1"]
        ):
            raise ValueError("PadFeature dimensions and bounds must increase")
        for name, value in values:
            object.__setattr__(self, name, value)

    @property
    def normal_lo(self) -> float:
        return self.z0

    @property
    def normal_hi(self) -> float:
        return self.z1

    def bounds(self, axis: str) -> tuple[float, float]:
        """Return this occurrence's ascending bounds on one world axis."""
        if axis == self.long_axis:
            return self.lo, self.hi
        if axis == self.width_axis:
            half = self.width / 2
            return self.w_center - half, self.w_center + half
        if axis == self.frame.axis:
            return self.z0, self.z1
        raise ValueError(f"unknown pad axis {axis!r}")

    @property
    def height(self) -> float:
        return self.z1 - self.z0

    def _normal_span(self) -> tuple[Point, Point]:
        start = list(self.frame.origin)
        end = list(self.frame.origin)
        axis_index = "xyz".index(self.frame.axis)
        start[axis_index] = self.z0 if self.direction > 0 else self.z1
        end[axis_index] = self.z1 if self.direction > 0 else self.z0
        return (tuple(start), tuple(end))  # type: ignore[return-value]

    def parameters(self) -> list[DimParameter]:
        parameters = [
            DimParameter("length", "pad_width", self.width),
            DimParameter("length", "pad_length", self.length),
        ]
        parameters.append(
            DimParameter("length", "pad_height", self.height, span=self._normal_span())
        )
        return parameters

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
            # Same role AND kind, semantically distinct — the ADR 4 (was 0016) tier-2 case that
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
        ps = list(self.member.parameters())  # slot width + length + optional end radius (no depth)
        if self.pitch is not None:
            ps.append(DimParameter("length", "pitch", self.pitch))
        if self.grid is not None:
            rp, cp = self.grid
            # Same role AND kind, semantically distinct — the ADR 4 (was 0016) tier-2 case that
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
    thread: str | ThreadRequirement | None = None
    knurl: KnurlRequirement | None = None
    kind: ClassVar[str] = "boss"

    def parameters(self) -> list[DimParameter]:
        params = [DimParameter("diameter", "boss", self.diameter)]
        if self.height is not None:
            params.append(DimParameter("length", "boss_height", self.height, span=self.span))
        return params

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PolygonalBossFeature:
    """A bounded regular polygonal prism boss.

    Unlike :class:`BossFeature`, its defining transverse measurement is across flats rather
    than diameter. ``flat_directions`` preserve the ordered definition and ``flat_centres``
    are physical side-face anchors, so placement need not reconstruct geometry from the
    measurement.
    """

    frame: Frame
    side_count: int
    across_flats: float
    height: float
    span: tuple[Point, Point]
    flat_directions: tuple[Point, ...]
    flat_centres: tuple[Point, ...]
    kind: ClassVar[str] = "polygonal_boss"

    def __post_init__(self) -> None:
        """Keep the regular-prism evidence complete at the public IR waist.

        ``flat_directions`` and ``flat_centres`` are placement evidence, not optional
        presentation hints.  Accepting a partial record would let its measurements plan and
        then disappear in the renderer, so detection, declaration and direct IR construction
        all inherit the same fail-closed contract here.
        """
        if not (
            isinstance(self.side_count, int)
            and not isinstance(self.side_count, bool)
            and self.side_count >= 4
            and self.side_count % 2 == 0
        ):
            raise ValueError("a polygonal boss needs an even side_count >= 4")
        if not isinstance(self.frame, Frame):
            raise ValueError("a polygonal boss needs a Frame")
        if (
            not isinstance(self.frame.axis, str)
            or self.frame.axis not in "xyz"
            or len(self.frame.axis) != 1
        ):
            raise ValueError("a polygonal boss axis must be 'x', 'y', or 'z'")
        if isinstance(self.across_flats, bool) or isinstance(self.height, bool):
            raise ValueError("a polygonal boss needs finite positive across_flats and height")
        try:
            across_flats = float(self.across_flats)
            height = float(self.height)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError(
                "a polygonal boss needs finite positive across_flats and height"
            ) from exc
        if not (isfinite(across_flats) and across_flats > 0 and isfinite(height) and height > 0):
            raise ValueError("a polygonal boss needs finite positive across_flats and height")
        try:
            direction_values = tuple(self.flat_directions)
        except TypeError as exc:
            raise ValueError(
                "flat_directions must contain one direction per polygon side"
            ) from exc
        try:
            centre_values = tuple(self.flat_centres)
        except TypeError as exc:
            raise ValueError(
                "flat_centres must contain one physical anchor per polygon side"
            ) from exc
        if len(direction_values) != self.side_count:
            raise ValueError("flat_directions must contain one direction per polygon side")
        if len(centre_values) != self.side_count:
            raise ValueError("flat_centres must contain one physical anchor per polygon side")

        def point(name: str, value) -> tuple[float, float, float]:
            try:
                components = tuple(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a finite 3-vector") from exc
            if len(components) != 3 or any(
                isinstance(component, bool) for component in components
            ):
                raise ValueError(f"{name} must be a finite 3-vector")
            try:
                result = tuple(float(component) for component in components)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a finite 3-vector") from exc
            if len(result) != 3 or not all(isfinite(component) for component in result):
                raise ValueError(f"{name} must be a finite 3-vector")
            return result

        origin = point("frame.origin", self.frame.origin)
        try:
            span_values = tuple(self.span)
        except TypeError as exc:
            raise ValueError("span must contain two finite 3-vector endpoints") from exc
        span = tuple(point("span endpoint", endpoint) for endpoint in span_values)
        if len(span) != 2:
            raise ValueError("span must contain two finite 3-vector endpoints")
        axis_i = "xyz".index(self.frame.axis)
        in_plane = [index for index in range(3) if index != axis_i]
        # Recognition admits 0.2 mm / 2 degree modelling noise and generated scripts write
        # coordinates to 3 decimal places.  The public waist may reject weaker evidence, but
        # it must not narrow either producer's documented precision contract.
        point_tol = 2e-3
        support_tol = 0.202
        angle_tol = pi / 90 + 2e-3
        if any(
            abs(endpoint[index] - origin[index]) > point_tol
            for endpoint in span
            for index in in_plane
        ):
            raise ValueError("span endpoints must lie on the polygonal boss axis line")
        if any(
            abs((span[0][index] + span[1][index]) / 2 - origin[index]) > point_tol
            for index in range(3)
        ):
            raise ValueError("span must be centred on frame.origin")
        if abs(abs(span[1][axis_i] - span[0][axis_i]) - height) > point_tol:
            raise ValueError("span length along the boss axis must equal height")

        directions = tuple(point("flat direction", direction) for direction in direction_values)
        centres = tuple(point("flat centre", centre) for centre in centre_values)
        span_lo, span_hi = sorted((span[0][axis_i], span[1][axis_i]))
        angles: list[float] = []
        for direction, centre in zip(directions, centres, strict=True):
            norm = hypot(*direction)
            if abs(norm - 1.0) > 1e-3 or abs(direction[axis_i]) > 1e-6:
                raise ValueError(
                    "each flat direction must be a unit vector perpendicular to the boss axis"
                )
            support = sum((centre[index] - origin[index]) * direction[index] for index in in_plane)
            if not isclose(support, across_flats / 2, rel_tol=1e-3, abs_tol=support_tol):
                raise ValueError("each flat centre must lie on its outward A/F support plane")
            if not span_lo - 1e-6 <= centre[axis_i] <= span_hi + 1e-6:
                raise ValueError("each flat centre must lie within the polygonal boss span")
            angles.append(atan2(direction[in_plane[1]], direction[in_plane[0]]) % (2 * pi))
        gaps = [
            (angles[(index + 1) % self.side_count] - angles[index]) % (2 * pi)
            for index in range(self.side_count)
        ]
        expected = 2 * pi / self.side_count
        counter_clockwise = all(abs(gap - expected) <= angle_tol for gap in gaps)
        clockwise = all(abs(gap - (2 * pi - expected)) <= angle_tol for gap in gaps)
        opposed = all(
            sum(
                directions[index][component] * directions[index + self.side_count // 2][component]
                for component in range(3)
            )
            <= -cos(angle_tol)
            * hypot(*directions[index])
            * hypot(*directions[index + self.side_count // 2])
            for index in range(self.side_count // 2)
        )
        if not ((counter_clockwise or clockwise) and opposed):
            raise ValueError(
                "flat_directions must be ordered as one regular polygon ring with opposed pairs"
            )

        object.__setattr__(self, "frame", Frame(origin, self.frame.axis))
        object.__setattr__(self, "across_flats", across_flats)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "span", span)
        object.__setattr__(self, "flat_directions", directions)
        object.__setattr__(self, "flat_centres", centres)

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "polygon_across_flats", self.across_flats),
            DimParameter("length", "boss_height", self.height, span=self.span),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PolygonalStockFeature:
    """A whole regular polygonal-prism stock definition, not an attached boss."""

    frame: Frame
    side_count: int
    across_flats: float
    length: float
    span: tuple[Point, Point]
    flat_directions: tuple[Point, ...]
    flat_centres: tuple[Point, ...]
    kind: ClassVar[str] = "polygonal_stock"

    def __post_init__(self) -> None:
        # The geometric invariants are identical to a polygonal boss's prism, while the IR
        # identity and axial measurement semantics remain deliberately distinct.
        try:
            prism = PolygonalBossFeature(
                frame=self.frame,
                side_count=self.side_count,
                across_flats=self.across_flats,
                height=self.length,
                span=self.span,
                flat_directions=self.flat_directions,
                flat_centres=self.flat_centres,
            )
        except ValueError as exc:
            message = str(exc).replace("polygonal boss", "polygonal stock")
            message = message.replace("boss axis", "stock axis")
            raise ValueError(message) from exc
        object.__setattr__(self, "frame", prism.frame)
        object.__setattr__(self, "across_flats", prism.across_flats)
        object.__setattr__(self, "length", prism.height)
        object.__setattr__(self, "span", prism.span)
        object.__setattr__(self, "flat_directions", prism.flat_directions)
        object.__setattr__(self, "flat_centres", prism.flat_centres)

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "polygon_across_flats", self.across_flats),
            DimParameter("length", "stock_length", self.length, span=self.span),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class ExternalSpurGearFeature:
    """Authored requirements for one metric external spur involute gear.

    This is deliberately a manufacturing-requirement feature, not a geometry
    recogniser.  Its fixed standards identify the one supported class; the
    numeric fields are all authored and are never recovered from a tooth-like
    boundary.  The standard table is its renderer, so it exposes no ordinary
    linear-dimension parameters to the dimension planner.
    """

    GEOMETRY_STANDARD: ClassVar[str] = "ISO 21771-1:2024"
    TOOTH_THICKNESS_STANDARD: ClassVar[str] = "ISO 21771-2:2025"
    BASIC_RACK_STANDARD: ClassVar[str] = "ISO 53:1998"
    MODULE_STANDARD: ClassVar[str] = "ISO 54:1996"
    FLANK_TOLERANCE_STANDARD: ClassVar[str] = "ISO 1328-1:2013"

    frame: Frame
    tooth_count: int
    module: float
    pressure_angle: float
    profile_shift: float
    face_width: float
    tooth_thickness: float
    tooth_thickness_tolerance: tuple[float, float]
    flank_tolerance_class: int
    kind: ClassVar[str] = "external_spur_gear"

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(float(value))
            for value in self.frame.origin
        ):
            raise ValueError("gear frame origin must contain three finite numbers")
        if isinstance(self.tooth_count, bool) or not isinstance(self.tooth_count, int):
            raise ValueError("tooth_count must be an integer")
        if not 5 <= self.tooth_count <= 1000:
            raise ValueError("tooth_count must be from 5 to 1000 for ISO 1328-1")
        for name in ("module", "face_width", "tooth_thickness"):
            raw = getattr(self, name)
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                raise ValueError(f"{name} must be a finite positive number")
            value = float(raw)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
            object.__setattr__(self, name, value)
        if not 0.5 <= self.module <= 70:
            raise ValueError("module must be from 0.5 mm to 70 mm for ISO 1328-1")
        if not 4 <= self.face_width <= 1200:
            raise ValueError("face_width must be from 4 mm to 1200 mm for ISO 1328-1")
        reference_diameter = self.module * self.tooth_count
        if not 5 <= reference_diameter <= 15000:
            raise ValueError(
                "module × tooth_count must give a 5 mm to 15000 mm reference diameter "
                "for ISO 1328-1"
            )
        if not isinstance(self.pressure_angle, (int, float)) or isinstance(
            self.pressure_angle, bool
        ):
            raise ValueError("pressure_angle must be a finite number")
        pressure_angle = float(self.pressure_angle)
        if not isfinite(pressure_angle) or not 0 < pressure_angle < 90:
            raise ValueError("pressure_angle must be finite and between 0 and 90 degrees")
        object.__setattr__(self, "pressure_angle", pressure_angle)
        if not isinstance(self.profile_shift, (int, float)) or isinstance(
            self.profile_shift, bool
        ):
            raise ValueError("profile_shift must be a finite number")
        profile_shift = float(self.profile_shift)
        if not isfinite(profile_shift):
            raise ValueError("profile_shift must be finite")
        object.__setattr__(self, "profile_shift", profile_shift)
        raw_tolerance = self.tooth_thickness_tolerance
        if not isinstance(raw_tolerance, (tuple, list)) or len(raw_tolerance) != 2:
            raise ValueError("tooth_thickness_tolerance must contain two finite deviations")
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(float(value))
            for value in raw_tolerance
        ):
            raise ValueError("tooth_thickness_tolerance must contain two finite deviations")
        tolerance = tuple(float(value) for value in raw_tolerance)
        lower, upper = tolerance
        if lower > upper:
            raise ValueError("tooth_thickness_tolerance lower deviation must not exceed upper")
        if self.tooth_thickness + lower <= 0:
            raise ValueError("tooth_thickness tolerance must retain a positive lower limit")
        object.__setattr__(self, "tooth_thickness_tolerance", tolerance)
        if (
            isinstance(self.flank_tolerance_class, bool)
            or not isinstance(self.flank_tolerance_class, int)
            or not 1 <= self.flank_tolerance_class <= 11
        ):
            raise ValueError("flank_tolerance_class must be an integer from 1 to 11")

    @property
    def span(self) -> tuple[float, float]:
        coordinate = self.frame.origin["xyz".index(self.frame.axis)]
        half = self.face_width / 2
        return (coordinate - half, coordinate + half)

    @property
    def requirement_values(self) -> tuple:
        """The exact authored payload represented by the gear data table."""
        return (
            self.tooth_count,
            self.module,
            self.pressure_angle,
            self.profile_shift,
            self.face_width,
            self.tooth_thickness,
            self.tooth_thickness_tolerance,
            self.flank_tolerance_class,
        )

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class ChamferFeature:
    """A chamfered (bevelled) edge (#560), called out ``C{leg}`` for an equal-leg 45°
    chamfer or ``{leg} × {angle}°`` otherwise. For a prismatic chamfer, ``axis`` is the
    chamfered edge's direction; for a turned conical chamfer, it is the shaft's rotational
    axis. ``leg1``/``leg2`` are the cut depths into the two adjacent faces (equal for 45°);
    ``angle`` is the chamfer angle (degrees). The recogniser recovers both legs from the
    geometry, so an asymmetric chamfer is distinguished from an equal-leg one — the size
    is not estimated from the rendered view (#560/#1254 acceptance)."""

    frame: Frame
    axis: str
    leg1: float
    leg2: float
    angle: float
    # A conical edge treatment on a turned profile reads with the shaft axis in-plane. The
    # same ``axis`` field is the bevel-edge direction for a prismatic chamfer, so this semantic
    # discriminator is required for faithful view selection and Sheet round-trip (#1276).
    turned: bool = False
    source_ids: tuple[str, ...] = ()
    part21_id: str = ""
    shape_aspect_ids: tuple[str, ...] = ()
    reference_item_ids: tuple[str, ...] = ()
    kind: ClassVar[str] = "chamfer"

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("length", "chamfer", self.leg1)]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class FilletFeature:
    """A rounded (filleted) edge (#561), called out ``R{radius}`` (grouped ``n× R{radius}``
    for equal radii). For a prismatic cylindrical blend, ``axis`` is the rounded edge's
    direction; for a turned toroidal fillet, it is the shaft's rotational axis. ``radius`` is
    recovered from the blend geometry — not estimated from the rendered view (#561/#1281
    acceptance). The arc analog of :class:`ChamferFeature`."""

    frame: Frame
    axis: str
    radius: float
    # Toroidal turned rounds read in shaft profile; cylindrical prismatic blends read end-on.
    # See the dual ``axis`` contract in the class docstring and #1276.
    turned: bool = False
    kind: ClassVar[str] = "fillet"

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("radius", "fillet", self.radius)]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class BlendFeature:
    """One complete straight or circular rolling-ball path from released schema v3.

    ``frame.origin`` is the straight-path anchor or circular-path centre; ``axis_direction``
    is the straight direction or circular normal. ``axis`` is its canonical first-maximum x/y/z
    component so detected and declared correspondence uses one deterministic routing identity.
    ``path_radius`` is present only for a circular path. Aggregate reconciliation has already
    removed paths owned by dimension-worthy Fillets.
    """

    frame: Frame
    axis: str
    radius: float
    side: str
    axis_direction: tuple[float, float, float]
    path_kind: str = "straight"
    path_radius: float | None = None
    kind: ClassVar[str] = "blend"

    def __post_init__(self) -> None:
        if type(self.frame) is not Frame:
            raise TypeError("blend frame must be an exact Frame value")
        axis, radius, at, side, direction, path_kind, path_radius = validate_blend_fields(
            axis=self.axis,
            radius=self.radius,
            at=self.frame.origin,
            side=self.side,
            axis_direction=self.axis_direction,
            path_kind=self.path_kind,
            path_radius=self.path_radius,
        )
        if self.frame.axis != axis:
            raise ValueError("blend frame axis must match its dominant axis")
        object.__setattr__(self, "radius", radius)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "axis_direction", direction)
        object.__setattr__(self, "path_kind", path_kind)
        object.__setattr__(self, "path_radius", path_radius)

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("radius", "blend", self.radius)]

    def references(self) -> list[Datum]:
        return []


register_blend_ir_types(BlendFeature, Frame)


@dataclass(frozen=True)
class HexPocketFeature:
    """A blind regular hex retaining its physical mouth section and opening side."""

    frame: Frame
    depth: float
    open_sign: int
    section: tuple[tuple[float, float], ...]
    kind: ClassVar[str] = "hex_pocket"
    side_count: ClassVar[int] = 6

    def __post_init__(self) -> None:
        if type(self.frame) is not Frame:
            raise TypeError("hex pocket frame must be an exact Frame")
        data = hex_pocket_geometry(
            self.frame.axis, self.depth, self.open_sign, self.frame.origin, self.section
        )
        object.__setattr__(self, "frame", Frame(data["origin"], self.frame.axis))
        object.__setattr__(self, "depth", data["depth"])
        object.__setattr__(self, "section", data["section"])

    def _geometry(self):
        return hex_pocket_geometry(
            self.frame.axis, self.depth, self.open_sign, self.frame.origin, self.section
        )

    @property
    def across_flats(self) -> float:
        return float(self._geometry()["across_flats"])

    @property
    def flat_centres(self) -> tuple[Point, ...]:
        return cast(tuple[Point, ...], self._geometry()["flat_centres"])

    @property
    def flat_directions(self) -> tuple[Point, ...]:
        return cast(tuple[Point, ...], self._geometry()["flat_directions"])

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "polygon_across_flats", self.across_flats),
            DimParameter("length", "pocket_depth", self.depth),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class CircularChannelFeature:
    """A cylindrical seat open at both run ends and along its actual circular arc.

    ``section`` retains three physical arc points (start, angular midpoint, end),
    in ascending transverse world axes. ``centreline`` names the increasing run
    endpoints on the cylinder axis. The frame origin anchors a leader on the wall;
    locations refer to the cylinder axis, not that wall anchor.
    """

    frame: Frame
    axis: str
    radius: float
    length: float
    centreline: tuple[Point, Point]
    section: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    kind: ClassVar[str] = "circular_channel"
    LOCATION_STEM: ClassVar[str] = "seat_location"

    def __post_init__(self) -> None:
        data = circular_channel_geometry(
            self.axis, self.radius, self.length, self.centreline, self.section
        )
        origin = _finite_point3("circular channel frame origin", self.frame.origin)
        if self.frame.axis != self.axis or any(
            abs(a - b) > 1e-7 for a, b in zip(origin, data["origin"], strict=True)
        ):
            raise ValueError("circular channel frame must anchor on its physical arc midpoint")
        for name in ("radius", "length", "centreline", "section"):
            object.__setattr__(self, name, data[name])
        object.__setattr__(self, "frame", Frame(data["origin"], self.axis))

    @property
    def sweep(self) -> float:
        return float(
            circular_channel_geometry(
                self.axis, self.radius, self.length, self.centreline, self.section
            )["sweep"]
        )

    @property
    def axis_origin(self) -> Point:
        first, last = self.centreline
        return (
            (first[0] + last[0]) / 2,
            (first[1] + last[1]) / 2,
            (first[2] + last[2]) / 2,
        )

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("diameter", "seat_diameter", 2 * self.radius),
            DimParameter("length", "seat_run", self.length, span=self.centreline),
            DimParameter("angle", "seat_sweep", self.sweep),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class CircularBlindStepFeature:
    """One quarter-cylindrical corner cut with a blind terminal (#1382).

    ``centreline`` is ordered from the blind terminal to the open stock envelope.
    ``section`` is the provider's canonical transverse arc endpoint, cylinder centre and
    other arc endpoint.  Together they retain the occupied quadrant and run direction
    without exposing provider topology.  Radius and blind depth are independently
    addressable requirements carried by one compound callout in the axis end view.
    """

    frame: Frame
    axis: str
    radius: float
    length: float
    centreline: tuple[Point, Point]
    section: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    kind: ClassVar[str] = "circular_blind_step"

    def __post_init__(self) -> None:
        if self.axis not in ("x", "y", "z") or self.frame.axis != self.axis:
            raise ValueError(
                "circular-blind-step axis must be x, y, or z and agree with the feature frame"
            )
        if type(self.radius) not in (int, float):
            raise ValueError("circular-blind-step radius must be finite and positive")
        try:
            radius = float(self.radius)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("circular-blind-step radius must be finite and positive") from exc
        if not isfinite(radius) or radius <= 0:
            raise ValueError("circular-blind-step radius must be finite and positive")
        if type(self.length) not in (int, float):
            raise ValueError("circular-blind-step depth must be finite and positive")
        try:
            length = float(self.length)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("circular-blind-step depth must be finite and positive") from exc
        if not isfinite(length) or length <= 0:
            raise ValueError("circular-blind-step depth must be finite and positive")
        try:
            if any(
                type(value) not in (int, float) for point in self.centreline for value in point
            ):
                raise ValueError
            centreline = tuple(tuple(float(value) for value in point) for point in self.centreline)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "circular-blind-step centreline must contain two finite 3D points"
            ) from exc
        if (
            len(centreline) != 2
            or any(len(point) != 3 for point in centreline)
            or not all(isfinite(value) for point in centreline for value in point)
        ):
            raise ValueError("circular-blind-step centreline must contain two finite 3D points")
        run_index = "xyz".index(self.axis)
        if any(
            not isclose(
                centreline[0][index],
                centreline[1][index],
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            for index in range(3)
            if index != run_index
        ) or not quantised_span_agrees(centreline[0][run_index], centreline[1][run_index], length):
            raise ValueError(
                "circular-blind-step centreline must be an axis-aligned terminal-to-open span matching depth"
            )
        try:
            if any(type(value) not in (int, float) for point in self.section for value in point):
                raise ValueError
            section = tuple(tuple(float(value) for value in point) for point in self.section)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "circular-blind-step section must contain three finite 2D points"
            ) from exc
        if (
            len(section) != 3
            or any(len(point) != 2 for point in section)
            or not all(isfinite(value) for point in section for value in point)
        ):
            raise ValueError("circular-blind-step section must contain three finite 2D points")
        first, centre, last = section
        first_delta = (first[0] - centre[0], first[1] - centre[1])
        last_delta = (last[0] - centre[0], last[1] - centre[1])
        first_changes = [
            index
            for index, value in enumerate(first_delta)
            if not isclose(value, 0.0, rel_tol=0.0, abs_tol=1e-9)
        ]
        last_changes = [
            index
            for index, value in enumerate(last_delta)
            if not isclose(value, 0.0, rel_tol=0.0, abs_tol=1e-9)
        ]
        canonical = (
            len(first_changes) == len(last_changes) == 1
            and first_changes[0] != last_changes[0]
            and quantised_radius_agrees(first, centre, radius)
            and quantised_radius_agrees(last, centre, radius)
        )
        if not canonical:
            raise ValueError(
                "circular-blind-step section must be a canonical quarter arc matching radius"
            )
        transverse = [index for index in range(3) if index != run_index]
        if any(
            not isclose(
                centreline[0][axis],
                centre[pair],
                rel_tol=0.0,
                abs_tol=1e-6,
            )
            for pair, axis in enumerate(transverse)
        ):
            raise ValueError("circular-blind-step section centre must agree with the centreline")
        object.__setattr__(self, "centreline", centreline)
        object.__setattr__(self, "section", section)
        object.__setattr__(self, "radius", radius)
        object.__setattr__(self, "length", length)
        try:
            origin = tuple(self.frame.origin)
        except TypeError as exc:
            raise ValueError(
                "circular-blind-step frame origin must contain three finite numeric coordinates"
            ) from exc
        if len(origin) != 3 or any(type(value) not in (int, float) for value in origin):
            raise ValueError(
                "circular-blind-step frame origin must contain three finite numeric coordinates"
            )
        try:
            numeric_origin = tuple(float(value) for value in origin)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "circular-blind-step frame origin must contain three finite numeric coordinates"
            ) from exc
        if not all(isfinite(value) for value in numeric_origin):
            raise ValueError(
                "circular-blind-step frame origin must contain three finite numeric coordinates"
            )
        if any(
            not isclose(a, b, rel_tol=0.0, abs_tol=1e-6)
            for a, b in zip(numeric_origin, self.arc_anchor, strict=True)
        ):
            raise ValueError(
                "circular-blind-step frame origin must be the curved-wall leader anchor"
            )

    @property
    def arc_anchor(self) -> Point:
        """A physical point halfway around and along the quarter-cylindrical wall."""
        return self.anchor_for(self.axis, self.radius, self.centreline, self.section)

    @staticmethod
    def anchor_for(axis, radius, centreline, section) -> Point:
        """Derive the curved-wall leader anchor from canonical public record facts."""
        first, centre, last = section
        radial = (
            (first[0] - centre[0]) + (last[0] - centre[0]),
            (first[1] - centre[1]) + (last[1] - centre[1]),
        )
        radial_scale = max(abs(radial[0]), abs(radial[1]))
        if not isfinite(radial_scale) or radial_scale == 0:
            raise ValueError("circular-blind-step section cannot define a finite wall anchor")
        unit = (radial[0] / radial_scale, radial[1] / radial_scale)
        unit_norm = hypot(*unit)
        radial_distance = radius / unit_norm
        section_point = (
            centre[0] + unit[0] * radial_distance,
            centre[1] + unit[1] * radial_distance,
        )
        run_index = "xyz".index(axis)
        transverse = [index for index in range(3) if index != run_index]
        point = [
            a + (b - a) / 2 if (a >= 0) == (b >= 0) else (a + b) / 2
            for a, b in zip(centreline[0], centreline[1], strict=True)
        ]
        point[transverse[0]], point[transverse[1]] = section_point
        if not all(isfinite(value) for value in point):
            raise ValueError("circular-blind-step section cannot define a finite wall anchor")
        return tuple(point)

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("radius", "circular_step_radius", self.radius),
            DimParameter("length", "circular_step_depth", self.length, span=self.centreline),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PairedRampStepFeature:
    """One mirror-symmetric two-ramp side step.

    ``frame.origin`` is the midpoint of the original shared ridge and ``axis`` is the
    ridge/run direction.  The public recogniser record proves two equal ramp angles and the
    open-to-terminal run length; Draftwright communicates those two requirements with one
    compound leader in the end-on view where the V profile is visible (#1382).
    """

    frame: Frame
    axis: str
    angle: float
    length: float
    kind: ClassVar[str] = "paired_ramp_step"

    def __post_init__(self) -> None:
        if self.axis not in ("x", "y", "z") or self.frame.axis != self.axis:
            raise ValueError(
                "paired-ramp axis must be x, y, or z and agree with the feature frame"
            )
        if isinstance(self.angle, bool) or not isfinite(self.angle) or not 0 < self.angle < 90:
            raise ValueError("paired-ramp angle must be a finite acute angle")
        if isinstance(self.length, bool) or not isfinite(self.length) or self.length <= 0:
            raise ValueError("paired-ramp run length must be finite and positive")

    @property
    def span(self) -> tuple[Point, Point]:
        """The original ridge's open-to-terminal run, centred on ``frame.origin``."""
        index = "xyz".index(self.axis)
        lo = list(self.frame.origin)
        hi = list(self.frame.origin)
        lo[index] -= self.length / 2
        hi[index] += self.length / 2
        return (tuple(lo), tuple(hi))  # type: ignore[return-value]

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("angle", "ramp_angle", self.angle),
            DimParameter("length", "ramp_run", self.length, span=self.span),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class GussetRibFeature:
    """A triangular reinforcing rib or one provider-proven rib pattern."""

    frame: Frame
    axis: str
    supports: tuple[tuple[str, float], tuple[str, float]]
    legs: tuple[float, float]
    directions: tuple[int, int]
    member_bounds: tuple[tuple[float, float], ...]
    datum: float
    pattern: Literal["single", "linear", "mirror"] = "single"
    pitch: float | None = None
    mirror_plane: tuple[str, float] | None = None
    kind: ClassVar[str] = "gusset_rib"

    def __post_init__(self) -> None:
        if self.axis not in "xyz" or self.frame.axis != self.axis:
            raise ValueError("gusset-rib axis must be x, y, or z and agree with its frame")
        transverse = tuple(axis for axis in "xyz" if axis != self.axis)
        if tuple(axis for axis, _ in self.supports) != transverse:
            raise ValueError("gusset-rib supports must name the two transverse axes in order")
        if any(not isfinite(value) or isinstance(value, bool) for _axis, value in self.supports):
            raise ValueError("gusset-rib supports must be finite")
        if any(
            not isfinite(value) or isinstance(value, bool) or value <= 0 for value in self.legs
        ):
            raise ValueError("gusset-rib legs must be finite and positive")
        if any(direction not in (-1, 1) for direction in self.directions):
            raise ValueError("gusset-rib directions must be signed unit directions")
        if not self.member_bounds or any(
            not isfinite(lo) or not isfinite(hi) or hi <= lo for lo, hi in self.member_bounds
        ):
            raise ValueError("gusset-rib member bounds must be finite positive intervals")
        if not isfinite(self.datum) or isinstance(self.datum, bool):
            raise ValueError("gusset-rib location datum must be finite")
        widths = tuple(hi - lo for lo, hi in self.member_bounds)
        if any(not isclose(width, widths[0], abs_tol=1e-6) for width in widths[1:]):
            raise ValueError("a gusset-rib pattern must have one common thickness")
        centres = self.member_centres
        if any(right <= left for left, right in zip(centres, centres[1:], strict=False)):
            raise ValueError("gusset-rib members must be ordered along the thickness axis")
        if self.pattern == "single":
            valid = (
                len(self.member_bounds) == 1 and self.pitch is None and self.mirror_plane is None
            )
        elif self.pattern == "linear":
            valid = (
                len(self.member_bounds) >= 2
                and self.pitch is not None
                and self.pitch > 0
                and self.mirror_plane is None
                and all(
                    isclose(right - left, self.pitch, abs_tol=1e-6)
                    for left, right in zip(centres, centres[1:], strict=False)
                )
            )
        elif self.pattern == "mirror":
            valid = (
                len(self.member_bounds) == 2
                and self.pitch is None
                and self.mirror_plane is not None
                and self.mirror_plane[0] == self.axis
                and isfinite(self.mirror_plane[1])
                and isclose(
                    (centres[0] + centres[1]) / 2,
                    self.mirror_plane[1],
                    abs_tol=1e-6,
                )
            )
        else:
            valid = False
        if not valid:
            raise ValueError("gusset-rib pattern facts are inconsistent")

    @property
    def thickness(self) -> float:
        lo, hi = self.member_bounds[0]
        return hi - lo

    @property
    def member_centres(self) -> tuple[float, ...]:
        return tuple((lo + hi) / 2 for lo, hi in self.member_bounds)

    @property
    def member_count(self) -> int:
        return len(self.member_bounds)

    @property
    def leader_anchor(self) -> Point:
        """A point inside the first triangular member, derived only from public facts."""
        point = list(self.frame.origin)
        point["xyz".index(self.axis)] = self.member_centres[0]
        for (support_axis, _), length, direction in zip(
            self.supports, self.legs, self.directions, strict=True
        ):
            point["xyz".index(support_axis)] += direction * length / 3
        return tuple(point)  # type: ignore[return-value]

    def parameters(self) -> list[DimParameter]:
        run = "xyz".index(self.axis)
        first = list(self.frame.origin)
        second = list(first)
        first[run], second[run] = self.member_bounds[0]
        params = [
            DimParameter(
                "length",
                "gusset_thickness",
                self.thickness,
                span=(cast(Point, tuple(first)), cast(Point, tuple(second))),
            )
        ]

        def leg_parameter(
            support_axis: str, length: float, span: tuple[Point, Point]
        ) -> DimParameter:
            if support_axis == "x":
                return DimParameter("length", "gusset_leg", length, span=span, discriminator="x")
            if support_axis == "y":
                return DimParameter("length", "gusset_leg", length, span=span, discriminator="y")
            return DimParameter("length", "gusset_leg", length, span=span, discriminator="z")

        for (support_axis, _), length, direction in zip(
            self.supports, self.legs, self.directions, strict=True
        ):
            start = list(self.frame.origin)
            end = list(start)
            end["xyz".index(support_axis)] += direction * length
            params.append(
                leg_parameter(
                    support_axis,
                    length,
                    (cast(Point, tuple(start)), cast(Point, tuple(end))),
                )
            )
        if self.pattern in {"linear", "mirror"}:
            centres = self.member_centres
            start = list(self.frame.origin)
            end = list(start)
            start[run], end[run] = centres[0], centres[1]
            span = (cast(Point, tuple(start)), cast(Point, tuple(end)))
            if self.pattern == "linear":
                assert self.pitch is not None
                params.append(DimParameter("length", "gusset_pitch", self.pitch, span=span))
            else:
                params.append(
                    DimParameter(
                        "length", "gusset_spacing", abs(centres[1] - centres[0]), span=span
                    )
                )
        target = (
            self.mirror_plane[1]
            if self.pattern == "mirror" and self.mirror_plane is not None
            else self.member_centres[0]
        )
        start = list(self.frame.origin)
        end = list(start)
        start[run], end[run] = self.datum, target
        params.append(
            DimParameter(
                "length",
                "gusset_location",
                abs(target - self.datum),
                span=(cast(Point, tuple(start)), cast(Point, tuple(end))),
            )
        )
        return params

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class ThroughStepFeature:
    """One rectangular open-profile step spanning the part along ``axis`` (#1382).

    The provider's canonical ``section`` is ``(envelope endpoint, concave corner,
    envelope endpoint)`` in the two non-run coordinates.  Its two orthogonal legs are
    independently dimensioned in the end-on view.  ``length`` and ``frame.origin`` retain
    exact physical correspondence to the removed through prism; the full-span run is already
    stated by the part envelope and is therefore structural rather than a third requirement.
    """

    frame: Frame
    axis: str
    length: float
    section: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    kind: ClassVar[str] = "through_step"

    def __post_init__(self) -> None:
        if self.axis not in ("x", "y", "z") or self.frame.axis != self.axis:
            raise ValueError(
                "through-step axis must be x, y, or z and agree with the feature frame"
            )
        if isinstance(self.length, bool) or not isfinite(self.length) or self.length <= 0:
            raise ValueError("through-step run length must be finite and positive")
        try:
            if any(isinstance(value, bool) for point in self.section for value in point):
                raise ValueError
            section = tuple(tuple(float(value) for value in point) for point in self.section)
        except (TypeError, ValueError) as exc:
            raise ValueError("through-step section must contain three finite 2D points") from exc
        if (
            len(section) != 3
            or any(len(point) != 2 for point in section)
            or not all(isfinite(value) for point in section for value in point)
        ):
            raise ValueError("through-step section must contain three finite 2D points")
        first_changes = [i for i in (0, 1) if section[0][i] != section[1][i]]
        second_changes = [i for i in (0, 1) if section[1][i] != section[2][i]]
        if (
            len(first_changes) != 1
            or len(second_changes) != 1
            or first_changes[0] == second_changes[0]
        ):
            raise ValueError("through-step section must be two non-zero orthogonal legs")
        object.__setattr__(self, "section", section)

    @property
    def transverse_axes(self) -> tuple[str, str]:
        return tuple(axis for axis in "xyz" if axis != self.axis)  # type: ignore[return-value]

    @property
    def section_points(self) -> tuple[Point, Point, Point]:
        run_index = "xyz".index(self.axis)
        transverse = [index for index in (0, 1, 2) if index != run_index]
        points = []
        for pair in self.section:
            point = list(self.frame.origin)
            point[transverse[0]], point[transverse[1]] = pair
            points.append(tuple(point))
        return tuple(points)  # type: ignore[return-value]

    @property
    def exterior_corner(self) -> Point:
        first, corner, last = self.section_points
        return tuple(a + b - c for a, b, c in zip(first, last, corner, strict=True))  # type: ignore[return-value]

    @property
    def outside_directions(self) -> tuple[tuple[str, int], tuple[str, int]]:
        """Topology-only signs from the concave corner toward the missing rectangle.

        Unlike :attr:`exterior_corner`, these signs carry no distance. They are safe compiled
        placement facts when an authored set withholds either dimensional leg (ADR 4 (was 0016)).
        """
        first, corner, last = self.section
        directions = []
        for index, axis in enumerate(self.transverse_axes):
            delta = next(
                point[index] - corner[index]
                for point in (first, last)
                if point[index] != corner[index]
            )
            directions.append((axis, 1 if delta > 0 else -1))
        return tuple(directions)  # type: ignore[return-value]

    def parameters(self) -> list[DimParameter]:
        points = self.section_points
        axes = self.transverse_axes
        parameters = []
        for start, end in zip(points, points[1:], strict=False):
            changed = next(
                axis for axis in axes if start["xyz".index(axis)] != end["xyz".index(axis)]
            )
            index = "xyz".index(changed)
            value = abs(end[index] - start[index])
            # Spell the closed discriminator vocabulary at the construction sites. Besides
            # making the public Literal statically auditable, this rejects an accidental
            # non-principal discriminator instead of laundering it through a free string.
            if changed == "x":
                parameter = DimParameter(
                    "length", "through_step_leg", value, span=(start, end), discriminator="x"
                )
            elif changed == "y":
                parameter = DimParameter(
                    "length", "through_step_leg", value, span=(start, end), discriminator="y"
                )
            else:
                parameter = DimParameter(
                    "length", "through_step_leg", value, span=(start, end), discriminator="z"
                )
            parameters.append(parameter)
        return parameters

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
    #: :class:`~quiddity.flats.Flat`. Two flats share an A/F definition only if
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

    @property
    def presentation_axis(self) -> str:
        """Principal axis used to select the drafting view for this flat.

        Recognition's ``axis`` is semantic identity.  The external recogniser deliberately
        normalises an exact dominant-component tie Z/Y-first for cross-platform stability.
        Draftwright historically drew an exact X/Z slant in the X end-on (side) view, where
        its leader is silhouette-safe.  Keep only that presentation exception here, without
        changing or re-performing recognition.  Every other record retains its semantic or
        explicitly declared axis.
        """
        if self.axis_aligned or self.axis_direction is None:
            return self.axis
        x, y, z = map(abs, self.axis_direction)
        if x > y and z > y and isclose(x, z, rel_tol=0.0, abs_tol=1e-12):
            return "x"
        return self.axis

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
    #: Same body-local provenance and authored replacement token used by StepFeature.
    profile: TurnedProfileIdentity | None = None
    profile_group: str | None = None
    kind: ClassVar[str] = "groove"

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "groove", self.width),
            DimParameter("diameter", "groove", self.diameter),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class LevelSupport:
    """The in-plane horizontal-face support for one prismatic height level (#915)."""

    level: float
    x_span: tuple[float, float]
    y_span: tuple[float, float]


@dataclass(frozen=True)
class StepLevelFeature:
    """The prismatic height profile — horizontal face levels (Z) dimensioned from the
    base, stacked right of the front view (#237). The turned analogue is `StepFeature`
    (length + OD per segment); this is the prismatic *height* ladder. ``levels`` are the
    interior step Z-coords (ascending); ``base`` is the part's bottom (bbox min Z).

    ``level_supports`` retain the horizontal faces each level came from. They give the
    compiler a real witness station and the detailer a truthful crop instead of forcing both
    to use the whole-part envelope (#915).

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
    level_supports: tuple[LevelSupport, ...] = ()
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
        """Bores are Z-axis only, enforced HERE because the IR is the one waist (ADR 1 (was 0015)).

        The rule is real: detection carries bores only when `od_axis == "z"`, and
        `render_rotational` leaders them in that branch alone — on a part turned about X or Y
        the bore is dimensioned by the hole pass instead. A non-Z `bores=` therefore produced
        `bore.diameter` parameters that planned, mirrored into a generated script, and drew
        nothing, with lint clean (#949).

        It started life in `declare.rotational`, which left the sanctioned ADR 4 (was 0011) route —
        a hand-built `PartModel` through `Sheet.add` or `build_drawing(model=…)` — wide open,
        and let the emitter write a `sheet.rotational(bores=…, axis="x")` line that the
        declare layer would then reject. Guarding the constructor rather than the type is the
        two-places-to-decide defect ADR 4 (was 0016) names, so the invalid state is simply not
        representable and every route inherits one answer. #952 tracks lifting the engine
        restriction (or recording the hole-pass split in ADR 1 (was 0015) and keeping this forever).
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
class BlindAxialBoreSupport:
    """One cached complete cylinder proves a hole and turned bore share a surface."""

    hole: HoleFeature
    rotational: RotationalFeature
    cylinder_interval: tuple[float, float]


@dataclass(frozen=True)
class AngleFeature:
    """One included-angle requirement derived from explicit oriented supports."""

    angular_reference: AngularReference
    kind: ClassVar[str] = "angle"

    def __post_init__(self) -> None:
        if not isinstance(self.angular_reference, AngularReference):
            raise ValueError("angle requires an AngularReference")
        if self.angular_reference.principal_axis == "?":
            raise ValueError(
                "angle requires a true-angle principal projection; oblique is unsupported"
            )

    @property
    def frame(self) -> Frame:
        return Frame(self.angular_reference.vertex, self.angular_reference.principal_axis.lower())

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter(
                "angle",
                "included",
                self.angular_reference.angle_degrees,
                angular_reference=self.angular_reference,
            )
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class AnglePatternFeature:
    """Declared repeated corners, each with an independently addressable angle.

    The member order is part of the declaration: omitting or tolerancing one
    measurement does not remove or renumber members. Recognition may create
    this form only after proving the physical profile repetition.
    """

    members: tuple[AngularReference, ...]
    kind: ClassVar[str] = "angle"

    def __post_init__(self) -> None:
        object.__setattr__(self, "members", tuple(self.members))
        if len(self.members) < 2 or not all(
            isinstance(member, AngularReference) for member in self.members
        ):
            raise ValueError("angle pattern requires at least two AngularReference members")
        if len({member.vertex for member in self.members}) != len(self.members):
            raise ValueError("angle pattern requires distinct corner references")
        first = self.members[0]
        if first.principal_axis == "?":
            raise ValueError("angle pattern requires a true-angle principal projection")
        axis = "XYZ".index(first.principal_axis)
        if any(
            member.principal_axis != first.principal_axis
            or member.sector != first.sector
            or abs(member.vertex[axis] - first.vertex[axis]) > 1e-6
            or abs(member.angle_degrees - first.angle_degrees) > 1e-6
            for member in self.members
        ):
            raise ValueError("angle pattern members must share a plane, sector and angle")

    @property
    def angular_reference(self) -> AngularReference:
        """The first member establishes the common true-angle view."""
        return self.members[0]

    @property
    def frame(self) -> Frame:
        return Frame(self.angular_reference.vertex, self.angular_reference.principal_axis.lower())

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter(
                "angle",
                "included",
                member.angle_degrees,
                discriminator=f"member{index + 1}",
                angular_reference=member,
            )
            for index, member in enumerate(self.members)
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class AuthoredDimension:
    """A pre-authored drafting dimension imported from an external semantic source.

    This is the concept-shaped IR for AP242 dimensional PMI: the source file may call it
    PMI, but the drawing model sees an authored linear/diameter/radius/etc. dimension with
    baked label and referenced geometry. Deviation tolerances use ``upper_tol``/``lower_tol``;
    limit dimensions use the mutually exclusive ``lower_bound``/``upper_bound`` pair. The
    normal dimension planner does not derive or duplicate it, so ``parameters()`` is empty;
    renderers consume it directly while keeping the source/provenance fields, including
    ``source_id``, for round-trip and diagnostics."""

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
    source_id: str = ""
    # Appended to preserve positional construction of the original IR record.
    lower_bound: float | None = None
    upper_bound: float | None = None
    # A supported source dimension remains materialised only when it cannot safely enrich a
    # canonical geometry feature.  The reason is structured and round-trips, rather than
    # disappearing into a log line or an emitter-only comment (#1116).
    lowering_blockers: tuple[str, ...] = ()
    # Geometry-evidence failures that prevent truthful rendering. These are distinct from
    # ``lowering_blockers``: failure to correlate to a canonical owner still permits the
    # standalone authored-dimension fallback promised by #1116 (#1209).
    rendering_blockers: tuple[str, ...] = ()
    # Finite-cylinder topology proven by the STEP extractor for Size_Diameter. A single
    # value is enough to render or correlate a diameter; generic linear dimensions still
    # require two reference stations.
    cylindrical_refs: tuple[CylindricalReference, ...] = ()
    # Declarative projection/strip intent. These select ordinary corridor candidates;
    # they are not page coordinates and do not bypass placement solving (ADR 2 (was 0012/0014)).
    view: str | None = None
    side: str | None = None
    angular_reference: AngularReference | None = None
    circular_refs: tuple[CircularReference, ...] = ()
    angular_references: tuple[AngularReference, ...] = ()
    angular_member_ids: tuple[str, ...] = ()
    angular_reference_item_groups: tuple[tuple[str, ...], ...] = ()
    kind: ClassVar[str] = "authored_dimension"

    def __post_init__(self) -> None:
        if self.angular_reference is not None:
            if self.dimension_kind != "angular":
                raise ValueError("angular_reference requires an angular dimension")
            if not isinstance(self.angular_reference, AngularReference):
                raise ValueError("angular_reference must be an AngularReference")
            reference = self.angular_reference
            if self.ref_pts != (reference.first, reference.vertex, reference.second):
                raise ValueError("angular ref_pts must agree with first, vertex, second")
        object.__setattr__(self, "angular_references", tuple(self.angular_references))
        object.__setattr__(self, "angular_member_ids", tuple(self.angular_member_ids))
        object.__setattr__(
            self,
            "angular_reference_item_groups",
            tuple(tuple(group) for group in self.angular_reference_item_groups),
        )
        has_angular_pattern_metadata = bool(
            self.angular_member_ids or self.angular_reference_item_groups
        )
        if has_angular_pattern_metadata and self.dimension_kind != "angular":
            raise ValueError("angular pattern provenance requires an angular dimension")
        if has_angular_pattern_metadata and self.angular_reference is not None:
            raise ValueError("singular angular_reference cannot carry angular pattern provenance")
        if (
            self.angular_member_ids
            and self.angular_reference_item_groups
            and len(self.angular_member_ids) != len(self.angular_reference_item_groups)
        ):
            raise ValueError("angular_member_ids must align with angular_reference_item_groups")
        if self.angular_references:
            if self.dimension_kind != "angular":
                raise ValueError("angular_references require an angular dimension")
            if self.angular_reference is not None:
                raise ValueError(
                    "angular dimension cannot combine singular and pattern references"
                )
            if len(self.angular_references) < 2 or not all(
                isinstance(reference, AngularReference) for reference in self.angular_references
            ):
                raise ValueError(
                    "angular_references require at least two AngularReference members"
                )
            count = len(self.angular_references)
            if self.angular_member_ids and len(self.angular_member_ids) != count:
                raise ValueError("angular_member_ids must align with angular_references")
            if (
                self.angular_reference_item_groups
                and len(self.angular_reference_item_groups) != count
            ):
                raise ValueError(
                    "angular_reference_item_groups must align with angular_references"
                )
            first = self.angular_references[0]
            if first.principal_axis == "?" or any(
                reference.principal_axis != first.principal_axis
                or reference.sector != first.sector
                or abs(reference.angle_degrees - first.angle_degrees) > 1e-6
                for reference in self.angular_references[1:]
            ):
                raise ValueError(
                    "angular_references must share one principal projection, sector and angle"
                )
        validate_authored_dimension_placement(
            self.dimension_kind,
            self.dominant_axis,
            self.view,
            self.side,
            owner="authored dimension",
            angular_reference=(
                self.angular_reference
                or (self.angular_references[0] if self.angular_references else None)
            ),
            cylindrical_refs=self.cylindrical_refs,
            ref_pts=self.ref_pts,
        )

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
    are visible instead of silently lost. ``source_id`` retains the external record identity
    needed to report that missing concept lowering; ``source_ids`` represents the repeated
    source occurrences of one projected datum definition; ``gtol_modifiers`` (including
    tolerance-zone and material-condition qualifiers) and
    ``lowering_blockers`` retain source facts and the explicit reason it stayed raw."""

    frame: Frame
    pmi_kind: str  # the PMI category: "linear" | "diameter" | "radius" | "angular" | ...
    value: float
    label: str
    dominant_axis: str
    ref_bbox: tuple[float, float, float, float, float, float] | None = None
    ref_pts: tuple[Point, ...] = ()
    source_id: str = ""
    datum_refs: tuple[str, ...] = ()
    part21_id: str = ""
    source_category: str = ""
    gtol_modifiers: tuple[str, ...] = ()
    lowering_blockers: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    datum_contexts: tuple[str, ...] = ()
    reference_item_ids: tuple[str, ...] = ()
    reference_axis: str = ""
    semantic_name: str = ""
    shape_aspect_ids: tuple[str, ...] = ()
    cylindrical_refs: tuple[CylindricalReference, ...] = ()
    reference_bboxes: tuple[tuple[float, float, float, float, float, float], ...] = ()
    structured_fields: tuple[tuple[str, str | float], ...] = ()
    kind: ClassVar[str] = "pmi"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class GeneralTolerance:
    """A document-wide dimensional tolerance requirement carried by the title block."""

    frame: Frame
    designation: str
    statement: str = ""
    source_id: str = ""
    part21_id: str = ""
    source_ids: tuple[str, ...] = ()
    kind: ClassVar[str] = "general_tolerance"

    def __post_init__(self) -> None:
        if not isinstance(self.designation, str) or not self.designation.strip():
            raise ValueError("general tolerance needs a non-empty designation")
        if self.designation != self.designation.strip():
            raise ValueError("general tolerance designation cannot contain surrounding whitespace")

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class DefaultSurfaceFinish:
    """A document-wide ISO 1302 surface-texture requirement.

    Unlike :class:`Finish`, this requirement has no geometric target and therefore renders as
    sheet furniture without a leader.  ``statement`` retains the source wording while ``ra`` is
    the normalized micrometre value shown by the drafting symbol.
    """

    frame: Frame
    ra: str
    statement: str = ""
    source_id: str = ""
    part21_id: str = ""
    kind: ClassVar[str] = "default_surface_finish"

    def __post_init__(self) -> None:
        if not isinstance(self.ra, str) or not self.ra.strip():
            raise ValueError("default surface finish needs a non-empty Ra value")
        if self.ra != self.ra.strip():
            raise ValueError(
                "default surface finish Ra value cannot contain surrounding whitespace"
            )

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class DocumentNote:
    """A source-proven document statement with no geometric attachment.

    ``on_drawing=False`` retains source/model provenance for representation
    metadata without presenting that metadata as a manufacturing instruction.
    """

    frame: Frame
    text: str
    note_kind: str
    source_id: str = ""
    part21_id: str = ""
    on_drawing: bool = True
    # A source-authored datum explanation omitted only after these source-owned
    # datum symbols prove every claim. The source text remains in the IR/audit.
    represented_by_source_ids: tuple[str, ...] = ()
    kind: ClassVar[str] = "document_note"

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("document note needs non-empty text")
        if self.text != self.text.strip():
            raise ValueError("document note text cannot contain surrounding whitespace")
        if self.note_kind not in ("datum_scheme", "model_representation", "edge_condition"):
            raise ValueError(f"unsupported document-note kind {self.note_kind!r}")
        if not isinstance(self.on_drawing, bool):
            raise ValueError("document-note on_drawing must be a bool")
        if not isinstance(self.represented_by_source_ids, tuple) or any(
            not isinstance(source_id, str) or not source_id
            for source_id in self.represented_by_source_ids
        ):
            raise ValueError("document-note represented_by_source_ids must be source IDs")

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class ControlFrame:
    """A geometric-tolerance feature control frame (ISO 1101) declared on the drawing
    (ADR 4 (was 0011 §4) aspect side-layer, #61). Placed as a first-class ADR 2 (was 0009) corridor
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
    # The IR feature this frame decorates — recorded as provenance (ADR 5 (was 0010)); ``None``
    # leaves it feature-less. Untyped to avoid an import cycle with the geometric features.
    origin: object | None = None
    # An imported AP242 scope symbol belongs at the leader kink, not in the tolerance cell's
    # material-condition ``modifier`` field.
    all_around: bool = False
    source_id: str = ""
    part21_id: str = ""
    # Appended after the existing public fields to preserve positional ControlFrame construction.
    all_over: bool = False
    # Compiler-owned display text for imported numeric PMI.  ``None`` keeps an authored
    # tolerance string exact; AP242 lowering retains the source magnitude in ``tolerance``
    # while deciding the sheet-normalized text once, before rendering.
    display_tolerance: str | None = None
    spherical_diameter: bool = False  # S⌀ prefix on the tolerance zone
    kind: ClassVar[str] = "control_frame"

    def __post_init__(self) -> None:
        if self.diameter and self.spherical_diameter:
            raise ValueError("a tolerance zone cannot be both diametral and spherical diametral")

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class DatumRef:
    """A datum feature symbol (ISO 5459) — a boxed letter tagging a surface/axis as a
    datum (#61). Placed as an ADR 2 (was 0009) corridor candidate by ``render_gdt``, not through
    the dimension planner (``parameters()`` is empty). Imported datums retain every source
    occurrence in ``source_ids`` while rendering the physical datum feature once."""

    frame: Frame
    letter: str
    view: str
    side: str
    origin: object | None = None
    source_id: str = ""
    source_ids: tuple[str, ...] = ()
    part21_id: str = ""
    reference_surface_kind: str = ""
    kind: ClassVar[str] = "datum_ref"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class Finish:
    """A surface-finish symbol (ISO 1302) — a roughness callout on a surface (#61).
    Placed as an ADR 2 (was 0009) corridor candidate by ``render_gdt``, not through the
    dimension planner (``parameters()`` is empty)."""

    frame: Frame
    ra: str  # roughness value text, e.g. "3.2" (Ra, µm)
    view: str
    side: str
    origin: object | None = None
    source_id: str = ""
    part21_id: str = ""
    kind: ClassVar[str] = "finish"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class Note:
    """A free-text manufacturing note (#488) hung on a leader to a feature/site — the shop
    callouts detection can't infer (thread specs, ``DEBURR``, chip-relief, knurl). Placed like
    the GD&T items — a first-class ADR 2 (was 0009) corridor candidate via ``render_gdt`` (its glyph is a
    single-line ``TextBlock``), NOT the dimension planner (``parameters()`` is empty)."""

    frame: Frame
    text: str
    view: str
    side: str
    origin: object | None = None
    # Canonical ParameterIds whose manufacturing requirements this authored note explicitly
    # carries (#1351). Kept separate from ``parameters()``: the note does not become a
    # dimension, and coverage can distinguish a drawn measurement from an authored semantic
    # assertion instead of parsing prose. Imported labels append their source identities so
    # placement and the AP242 ledger can account for the same note.
    satisfies: tuple[DimensionParameterId, ...] = ()
    source_id: str = ""
    source_ids: tuple[str, ...] = ()
    part21_id: str = ""
    kind: ClassVar[str] = "note"

    def __post_init__(self) -> None:
        if not self.satisfies:
            return
        if not isinstance(self.satisfies, tuple) or any(
            not isinstance(parameter, str) or not parameter.strip() for parameter in self.satisfies
        ):
            raise ValueError("note satisfies= must be a tuple of non-empty parameter ids")
        if len(set(self.satisfies)) != len(self.satisfies):
            raise ValueError("note satisfies= contains duplicate parameter ids")
        if not isinstance(self.origin, Feature):
            raise ValueError("a note with satisfies= must target a dimensionable feature")
        available = {parameter.parameter_id for parameter in self.origin.parameters()}
        # ``location`` is the one addressable dimension with no DimParameter. IR can validate
        # its spelling here; the Sheet/compiler boundaries validate planner-owned eligibility
        # without reversing the IR -> planner dependency (ADR 1 (was 0015)).
        if "location" in self.satisfies:
            available.add("location")
        invalid = sorted(set(self.satisfies) - available)
        if invalid:
            raise ValueError(
                f"note satisfies= names invalid parameter id(s) {invalid} for "
                f"{type(self.origin).__name__}; choose from {sorted(available)}"
            )

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class RequestedDimension:
    """A caller's ``add_dimension(...)`` — *augment the planner's set with this
    measurement* (ADR 4 (was 0016)).

    Referential like every ADR 4 (was 0016) intent: it names a feature and a role and carries
    **no number**, so the value still comes from the geometry. What it changes is
    *selection*, not derivation — if the planner suppressed this parameter the request
    un-suppresses it; if the planner already emits it the request is a no-op. Overlap
    is deliberately not an error (the #872 idempotence gate): a script should be able to
    ask for a measurement without first knowing whether the rule set already volunteers
    it, or the verb would leak the planner's internals into every caller.

    Emphasis (ADR 2 (was 0012)'s ``pin`` / ``priority``) is deliberately NOT here. The engine
    already carries two spellings of "keep this dimension put" — ``Drawing.pin(name)``
    on a placed annotation, and the post-build ``intents`` route that reaches the solve
    through ``render_locations(pinned=…)`` for location dims only. Adding a third,
    pre-build spelling would scatter one concept across three layers, the same way the
    corridor priority scale was scattered before #894 consolidated it. The convergence
    is tracked separately. ``view``/``side`` are not emphasis or frozen placement: they
    select a semantic corridor whose candidates still enter the normal solve. This type
    carries selection plus optional display/corridor policy, while the measurement itself
    remains referential and coordinates remain absent.
    """

    feature: Feature
    role: Role
    #: Discriminator for a role a feature carries more than once — today only a grid
    #: pattern's two pitches (``"row"`` / ``"col"``). ``None`` where the role identifies
    #: the measurement on its own.
    discriminator: str | None = None
    #: Explicit display precision for this referential dimension.  The numeric value and
    #: identity still come from ``feature``; this controls only the compiler-owned text at
    #: the rendering boundary (#1349). ``None`` preserves automatic formatting.
    #: An imported knurl's numeric maximum sets a minimum number of decimal places,
    #: so a coarser request cannot round its referenced diameter away from that value.
    display_decimals: int | None = None
    #: Optional semantic projection/strip preference. The planner validates renderer
    #: compatibility; the placement engine still owns coordinates.
    view: str | None = None
    side: str | None = None
    #: Declaration-local hole member, or the centre of a bolt-circle pattern.
    #: Paired with the measured-axis discriminator to select one location component.
    member: int | Literal["centre"] | None = None
    #: One-based drafting-spaced lane from this dimension's physical witness. This is
    #: a relative rank, never a page-space distance; the measured-candidate solve
    #: resolves the physical position and may drop an infeasible request honestly.
    #: Appended after the established positional fields to preserve their constructor ABI.
    lane: int | None = None

    def __post_init__(self) -> None:
        validate_placement_intent(self.view, self.side, owner="requested dimension")
        if self.lane is not None and (
            isinstance(self.lane, bool)
            or not isinstance(self.lane, int)
            or not 1 <= self.lane <= 8
        ):
            raise ValueError("requested dimension lane must be an integer from 1 to 8")
        if self.member is not None and self.role != "location":
            raise ValueError("member selects only a location measurement")
        if self.role == "location" and (self.member is not None or self.discriminator is not None):
            feature = self.feature
            if not isinstance(feature, HoleFeature | PatternFeature):
                raise ValueError("member/axis location selection requires a hole or hole pattern")
            if self.member is None or self.discriminator is None:
                raise ValueError("select a location component with both member and axis")
            if self.discriminator not in tuple(
                axis for axis in "xyz" if axis != feature.frame.axis
            ):
                raise ValueError("location axis must be transverse to the hole axis")
            if self.member == "centre":
                if not isinstance(feature, PatternFeature) or not (
                    feature.pattern == "bolt_circle" or grid_has_centre_datum(feature)
                ):
                    raise ValueError("only a bolt-circle or proved grid has a centre location")
            elif (
                isinstance(self.member, bool)
                or not isinstance(self.member, int)
                or not 0
                <= self.member
                < len(
                    feature.members
                    if isinstance(feature, PatternFeature)
                    else feature.members or (feature.frame.origin,)
                )
            ):
                raise ValueError("location member must index the declared member tuple")
        if self.role == "location" and (self.view is not None or self.side is not None):
            raise ValueError(
                "placement intent is unavailable for location dimensions: one location "
                "may compile into multiple directional values"
            )
        if self.role == "location" and self.lane is not None:
            raise ValueError(
                "lane intent is unavailable for location dimensions: one location may "
                "compile into multiple directional values"
            )
        decimals = self.display_decimals
        if decimals is None:
            return
        if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 15:
            raise ValueError("display_decimals must be an integer from 0 to 15")


@dataclass(frozen=True)
class ScheduleRow:
    """An exact feature and measurement selectors, with no authored numeric content.

    ``location`` expands to addressable member components in the planner. Other
    selectors are canonical parameter IDs, rather than potentially ambiguous roles.
    """

    feature: Feature
    parameters: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.parameters, str):
            raise ValueError("schedule parameters must be a sequence of parameter IDs")
        object.__setattr__(self, "parameters", tuple(self.parameters))
        if not self.parameters or any(
            not isinstance(parameter, str) or not parameter.strip()
            for parameter in self.parameters
        ):
            raise ValueError("schedule row requires nonempty parameter IDs")
        if len(set(self.parameters)) != len(self.parameters):
            raise ValueError("schedule row repeats a parameter selector")


@dataclass(frozen=True)
class FeatureSchedule:
    """Authored table representation; the compiler supplies its measured cells."""

    name: str
    rows: tuple[ScheduleRow, ...]
    prefer: Literal["tr", "tl", "br", "bl"] = "tr"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("schedule requires a nonempty name")
        object.__setattr__(self, "rows", tuple(self.rows))
        if not self.rows or any(not isinstance(row, ScheduleRow) for row in self.rows):
            raise ValueError("schedule requires at least one ScheduleRow")
        if self.prefer not in ("tr", "tl", "br", "bl"):
            raise ValueError("schedule prefer must be tr, tl, br or bl")


@dataclass(frozen=True)
class LayoutOverride:
    """One append-only layout-only edit against a build-scoped declaration.

    The record carries either a feature corridor side or one exact dimension's relative
    lane, never page coordinates. The matching feature/request in :class:`PartModel`
    contains the resolved value used by the renderer; retaining this separate intent
    record makes generated replay and assessment evidence explicit without turning layout
    policy into engineering meaning.
    """

    declaration_id: str
    side: str | None = None
    parameter_id: str | None = None
    lane: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.declaration_id, str)
            or not self.declaration_id.strip()
            or self.declaration_id != self.declaration_id.strip()
        ):
            raise ValueError(
                "layout override requires a non-empty declaration_id without "
                "surrounding whitespace"
            )
        if (self.side is None) == (self.lane is None):
            raise ValueError("layout override requires exactly one of side or lane")
        if self.side is not None:
            if self.parameter_id is not None:
                raise ValueError("a side override targets a declaration, not a parameter")
            validate_placement_intent(None, self.side, owner="layout override")
            return
        if not isinstance(self.parameter_id, str) or not self.parameter_id.strip():
            raise ValueError("a lane override requires a non-empty parameter_id")
        if self.parameter_id != self.parameter_id.strip():
            raise ValueError("layout override parameter_id cannot contain surrounding whitespace")
        if (
            isinstance(self.lane, bool)
            or not isinstance(self.lane, int)
            or not 1 <= self.lane <= 8
        ):
            raise ValueError("layout override lane must be an integer from 1 to 8")


@dataclass(frozen=True)
class DeclarationIdentity:
    """Build-scoped identity for one editable declaration.

    The identifier names declared intent inside one script/build.  It is deliberately not a
    geometry, topology, or cross-run feature identifier.  ``occurrence_ids`` may only contain
    report-local IDs from the recognition run that generated the declaration.
    """

    declaration_id: str
    provenance: Literal["authored", "detected-geometry", "pmi", "structured-note", "derived"] = (
        "authored"
    )
    occurrence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.declaration_id, str)
            or not self.declaration_id.strip()
            or self.declaration_id != self.declaration_id.strip()
        ):
            raise ValueError(
                "declaration identity requires a non-empty declaration_id without "
                "surrounding whitespace"
            )
        if self.provenance not in {
            "authored",
            "detected-geometry",
            "pmi",
            "structured-note",
            "derived",
        }:
            raise ValueError(f"unsupported declaration provenance {self.provenance!r}")
        if not isinstance(self.occurrence_ids, tuple) or any(
            not isinstance(value, str) or not value.strip() or value != value.strip()
            for value in self.occurrence_ids
        ):
            raise ValueError("declaration occurrence_ids must be a tuple of non-empty strings")
        if len(set(self.occurrence_ids)) != len(self.occurrence_ids):
            raise ValueError("declaration occurrence_ids must be unique")
        if self.occurrence_ids and self.provenance not in {"detected-geometry", "pmi"}:
            raise ValueError(
                "only detected-geometry or pmi declarations may name recognition occurrences"
            )


@dataclass
class PartModel:
    """The whole-part IR: the oriented part plus its features and datums."""

    bbox: object  # build123d BoundBox
    orientation: str | None  # turning axis if rotational, else None
    features: list[Feature] = field(default_factory=list)
    datums: list[Datum] = field(default_factory=list)
    # Recognition can silently omit a body envelope. Declared models can omit it
    # by intent, so diagnostics about that gap apply only to detected inventories.
    detected: bool = False
    # A generated Sheet script mirrors an automatic plan as authored dimensions.
    # Preserve recognition-origin omission checks across that replay boundary.
    replayed_recognition: bool = False
    # Authored aspects the frozen features can't carry (ADR 4 (was 0011 §4)). P2a uses it for
    # per-dimension tolerances: ``{(feature, ParamKind) -> float | (lo, hi)}``. Imported
    # requirements wrap that value in :class:`ToleranceDecoration` so source identities
    # survive without widening renderer tolerance types (#1116). The planner consults those
    # tolerance entries. Otherwise empty on a detected model.
    decorations: dict = field(default_factory=dict)
    # Caller-requested augmenting measurements (ADR 4 (was 0016) / #872) — the planner's
    # *intent input*. Kept distinct from `decorations` on purpose: a decoration enriches
    # a dimension the planner already chose, a request changes WHICH dimensions it
    # chooses. Empty on a detected model.
    requested_dimensions: tuple[RequestedDimension, ...] = ()
    # The COMPLETE authored dimension set (ADR 4 (was 0016) / #874/#876), or ``None`` for the
    # planner's automatic set. The two are the model's only dimension sources and are
    # mutually exclusive — omission is only meaningful inside a set declared complete,
    # which is exactly why the source has to be stated rather than inferred.
    #
    # Suppression by omission MARKS rather than filters (#875): a parameter outside the
    # authored set is planned with ``suppressed=True`` and keeps its value, so the group
    # retains its engineering data and a later pass can still see what was left out.
    authored_dimensions: tuple[RequestedDimension, ...] | None = None
    # Table representations belong to authored intent, not the physical inventory.
    schedules: tuple[FeatureSchedule, ...] = ()
    # Build-scoped agent/editing provenance aligned exactly with ``features``. Alignment rather
    # than feature-keyed decorations is essential: two independent frozen feature values may
    # compare equal and must not collapse into one declaration (#1710). Empty means the caller
    # supplied no declaration identity inventory; otherwise there is one slot per feature.
    declaration_identities: tuple[DeclarationIdentity | None, ...] = ()
    # Explicit layout-only edits, addressed through the declaration inventory.  Appended to
    # preserve positional PartModel construction.  The feature carries the resolved side;
    # this ledger records why that side differs from the authored/generated default (#1757).
    layout_overrides: tuple[LayoutOverride, ...] = ()
    # Derived from the build's cached cylinder substrate before planning. It carries
    # physical correspondence, not a rendering decision or a second feature inventory.
    blind_axial_bore_supports: tuple[BlindAxialBoreSupport, ...] = ()
    # Report-only PMI stays in the inventory for diagnostics but must not take
    # precedence over dimensions that will actually reach the sheet.
    pmi_annotations_enabled: bool = True
    # A Document member may also contain caller-declared PMI. Hide only source
    # annotations excluded by that member's mode, using their exact feature identity.
    hidden_authored_dimension_ids: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if self.declaration_identities and len(self.declaration_identities) != len(self.features):
            raise ValueError(
                "PartModel.declaration_identities must be empty or align one-for-one with features"
            )
        if any(
            identity is not None and not isinstance(identity, DeclarationIdentity)
            for identity in self.declaration_identities
        ):
            raise ValueError(
                "PartModel.declaration_identities entries must be DeclarationIdentity or None"
            )
        declaration_ids = [
            identity.declaration_id
            for identity in self.declaration_identities
            if identity is not None
        ]
        if len(set(declaration_ids)) != len(declaration_ids):
            raise ValueError("PartModel.declaration_identities requires unique declaration IDs")
        if not isinstance(self.layout_overrides, tuple) or any(
            not isinstance(override, LayoutOverride) for override in self.layout_overrides
        ):
            raise ValueError("PartModel.layout_overrides requires LayoutOverride entries")
        override_targets = [
            (override.declaration_id, override.parameter_id) for override in self.layout_overrides
        ]
        if len(set(override_targets)) != len(override_targets):
            raise ValueError("PartModel.layout_overrides requires unique layout targets")
        identities_by_id = {
            identity.declaration_id: index
            for index, identity in enumerate(self.declaration_identities)
            if identity is not None
        }
        for override in self.layout_overrides:
            index = identities_by_id.get(override.declaration_id)
            if index is None:
                raise ValueError(
                    "PartModel.layout_overrides must target one declared feature; "
                    f"found none for {override.declaration_id!r}"
                )
            feature = self.features[index]
            if override.side is not None:
                resolved = getattr(feature, "side", None)
                if resolved != override.side:
                    raise ValueError(
                        f"layout override {override.declaration_id!r} records side "
                        f"{override.side!r}, but the resolved feature side is {resolved!r}"
                    )
                continue
            matching = [
                request
                for request in (*self.requested_dimensions, *(self.authored_dimensions or ()))
                if request.feature is feature
                and request.role == override.parameter_id
                and request.lane == override.lane
            ]
            if len(matching) != 1:
                raise ValueError(
                    f"layout override {override.declaration_id!r} records lane "
                    f"{override.lane!r} for {override.parameter_id!r}, but found "
                    f"{len(matching)} matching resolved dimension intents"
                )
        self._validate_structured_note_origins()
        self._validate_schedule_origins()

    def _validate_schedule_origins(self) -> None:
        """Check exact ownership again at planning, since features is mutable."""
        names: set[str] = set()
        for schedule in self.schedules:
            if not isinstance(schedule, FeatureSchedule):
                raise ValueError("PartModel.schedules requires FeatureSchedule entries")
            if schedule.name in names:
                raise ValueError(f"duplicate schedule name {schedule.name!r}")
            names.add(schedule.name)
            for row in schedule.rows:
                if not any(feature is row.feature for feature in self.features):
                    raise ValueError(
                        f"schedule {schedule.name!r} row must target the identical feature "
                        "in PartModel.features"
                    )

    def _validate_structured_note_origins(self) -> None:
        """Require every authority-bearing note to target this exact mutable inventory."""
        # Structured authority must terminate at a feature in this exact IR inventory. Value
        # equality is unsafe when two identical holes are distinct physical requirements, and
        # an external origin cannot round-trip or join recognition evidence (#1351).
        for feature in self.features:
            if (
                isinstance(feature, Note)
                and feature.satisfies
                and not any(candidate is feature.origin for candidate in self.features)
            ):
                raise ValueError(
                    "structured note origin must be the identical feature in PartModel.features"
                )
