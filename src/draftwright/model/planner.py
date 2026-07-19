"""planner — the dimensioning back-end over the IR (ADR 0008).

One rule set over `DimParameter`s, regardless of which feature produced them. The
contract was tightened twice under adversarial review of the counterbore work:

- **Grouping with an anchor and one view.** A feature's parameters form one
  `DimensionGroup` carrying the feature's `anchor` (so it can be placed) and a
  single `view` (so a compound callout — a hole's bore + counterbore + depth —
  renders as one callout in one place, not split across views/kinds).
- **No value-blind collapse.** The planner does *not* de-duplicate parameters by
  value: a `counterbore` ø16 and a `boss` ø16 are distinct, and a 10×10 pocket's
  two orthogonal 10 mm lengths are distinct. Features own their parameters; they
  do not emit spurious duplicates. Genuine redundancy/count of *repeated identical
  features* ("3× ø8") is upstream (pattern detection), not here.

Current scope: convention + group view selection. Richer render intents
(suppression / datum / grouping) are the next planned increment (#250); the full
ISO/ASME rule set grows here as real features demand it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from draftwright._geometry import _END_ON, HoleRef
from draftwright.model.ir import (
    Datum,
    DimParameter,
    Feature,
    HoleFeature,
    PartModel,
    PatternFeature,
    Point,
)

# How each (role, kind) is drawn. Defaults keep the table small.
_CONVENTION = {
    ("step", "length"): "chain",
    ("step", "diameter"): "leader",
    ("bore", "diameter"): "leader",
    ("bore", "depth"): "leader",
    ("counterbore", "diameter"): "leader",
    ("counterbore", "depth"): "leader",
    ("spotface", "diameter"): "leader",
    ("spotface", "depth"): "leader",
    ("boss", "diameter"): "leader",
    ("bolt_circle", "diameter"): "leader",  # BCD (a pitch-circle diameter)
    ("pitch", "length"): "pitch",  # linear-array pitch — distinct from a plain linear dim
    ("grid_pitch", "length"): "pitch",
    ("chamfer", "length"): "leader",  # C{leg} / {leg}×{angle}° leader callout (#724)
    ("fillet", "radius"): "leader",  # R{radius} (grouped n× R) leader callout (#725)
    ("flat", "length"): "leader",  # {across} A/F across-flats leader callout (#726)
    # One groove callout carries BOTH params: {width} WIDE × ø{diameter} (#727)
    ("groove", "length"): "leader",
    ("groove", "diameter"): "leader",
    # One pocket callout carries all THREE params: W × L × D DEEP (#728)
    ("pocket_width", "length"): "leader",
    ("pocket_length", "length"): "leader",
    ("pocket_depth", "length"): "leader",
}


@dataclass(frozen=True)
class PlannedDimension:
    """A parameter plus its render *intent* — the planner's decision about how/whether
    it is drawn, leaving the layout (zones/sides/coordinates) to the renderer
    (ADR 0008 Amendment 4). `suppressed`/`reason` carry *model-level* suppression
    (the planner sees the model, not the drawing-in-progress, so render-state
    suppression like "diameter already mentioned" stays in the renderer). `datum` is
    the reference a positional dim measures from, resolved from `param.refs` against
    `model.datums` — reserved for the location-dimension work (#238); ``None`` until
    a feature emits datum-referenced params."""

    param: DimParameter
    convention: str  # "chain" | "ordinate" | "leader" | "linear" | "pitch"
    suppressed: bool = False
    reason: str | None = None
    datum: Datum | None = None
    # The source IR feature this dim locates — carried so the renderer can record
    # provenance (ADR 0010). ``None`` for dims not tied to a single feature.
    feature: Feature | None = None


@dataclass(frozen=True)
class DimensionGroup:
    """A feature's planned dimensions, kept together with the source feature and a
    single view so a compound callout renders as one callout in one place.

    Carrying the source `feature` (rather than copying selected fields) keeps the
    plan Open/Closed: a grouped renderer reads whatever metadata it needs —
    `count`/`pattern` for a pattern, a thread spec later — without the plan
    contract growing a field per feature type."""

    feature: Feature
    view: str  # one view for the whole group
    dims: tuple[PlannedDimension, ...]

    @property
    def feature_kind(self) -> str:
        return self.feature.kind

    @property
    def anchor(self) -> Point:
        return self.feature.frame.origin


def _group_view(feature: Feature) -> str:
    """The single view a feature's callout lands on — derived from the feature's
    axis, never hardcoded, so X and Z are handled by the same rule (parity). A
    turned step's length + OD read on the lengthwise (front) profile view; a
    diameter callout (hole / boss) reads on the view where the cylinder is end-on
    (z→plan, x→side, y→front)."""
    if feature.kind == "step":
        return "front"
    return _END_ON.get(feature.frame.axis, "plan")


def _square_footprint(model: PartModel) -> bool:
    """In-plane width ≈ depth (within 5%) — a single overall dim suffices."""
    size = model.bbox.size  # type: ignore[attr-defined]  # build123d BoundBox
    w, d = float(size.X), float(size.Y)
    return abs(w - d) <= max(w, d) * 0.05


def _suppression(model: PartModel, feature: Feature, param: DimParameter):
    """Model-level suppression intent → ``(suppressed, reason)``. Decisions the
    planner can make from the model alone (ISO 129 no-double-dimensioning):
    a square footprint needs one overall dim, not width+depth; a turned part's
    step-length chain already conveys the length (X) / height (Z), so the envelope
    dim along the turning axis is redundant."""
    if feature.kind != "envelope":
        return False, None
    # A rotational part's OD already conveys its cross-axis extent(s); the envelope
    # dim(s) perpendicular to the turning axis would double-dimension it (#222). The
    # axis-aligned envelope dim (the length) is kept. (The overall *height* dim is the
    # height-ladder renderer's call — it skips it for an X/Y rotational part likewise.)
    rot = next((f for f in model.features if f.kind == "rotational"), None)
    if rot is not None:
        od_perp = {"x": {"depth"}, "y": {"width"}, "z": {"width", "depth"}}[rot.frame.axis]
        if param.role in od_perp:
            return True, f"rotational OD ({rot.frame.axis}-axis) conveys this extent"
    if param.role in ("width", "depth") and _square_footprint(model):
        return True, "square footprint (single overall dim suffices)"
    if param.role == "width" and model.orientation == "x":
        return True, "X-turned (step-length chain conveys the length)"
    if param.role == "height" and model.orientation == "z":
        return True, "Z-turned (step-length chain conveys the height)"
    return False, None


def _datum_for(model: PartModel, param: DimParameter) -> Datum | None:
    """The datum a positional param measures from — resolved from ``param.refs``
    against ``model.datums``. ``None`` until a feature emits datum-referenced
    params (the location-dimension work, #238)."""
    if not param.refs:
        return None
    return next((d for d in model.datums if d.id in param.refs), None)


def plan_locations(model: PartModel) -> list[PlannedDimension]:
    """Plan hole **location** dimensions — the *intent*: which features get located
    and from which datum. The renderer owns the tier/legibility/zone layout
    (Amendment 4). One ref per un-patterned Z-hole + one per Z-pattern (bolt-circle
    centre, else the array member nearest the datum); coincident refs deduped. Each
    returned `PlannedDimension` carries the datum and a `span` of datum → ref; the
    renderer derives the X (plan) and Y (side) distances from it (#238)."""
    datum = next((d for d in model.datums if d.id == "datum_xy"), None)
    if datum is None:
        return []
    dx, dy, dz = datum.at
    # (ref_point, role): role distinguishes a hole ref from a pattern ref — the
    # renderer's concentric-bore exclusion applies to holes only (a bolt circle on
    # the axis is still located by its centre), matching the engine.
    # (ref_point, role, source feature): the feature is carried so the renderer can
    # record provenance on the placed location dim (ADR 0010).
    refs: list[tuple[Point, str, Feature]] = []
    for f in model.features:
        if f.frame.axis != "z":
            continue
        if isinstance(f, HoleFeature):
            # un-patterned holes — a HoleFeature may group identical holes
            for m in f.members or (f.frame.origin,):
                refs.append((m, "location", f))
        elif isinstance(f, PatternFeature):
            if f.pattern == "bolt_circle":
                refs.append((f.frame.origin, "location_pattern", f))
            elif f.members:
                near = min(f.members, key=lambda m: (m[0] - dx) ** 2 + (m[1] - dy) ** 2)
                refs.append((near, "location_pattern", f))
    unique: list[tuple[Point, str, Feature]] = []
    for r, role, feat in refs:
        if not any(abs(r[0] - u[0]) < 0.5 and abs(r[1] - u[1]) < 0.5 for u, _, _ in unique):
            unique.append((r, role, feat))
    return [
        PlannedDimension(
            param=DimParameter(
                kind="location",
                role=role,
                value=0.0,  # a location is a 2-D offset; the renderer reads the span
                span=((dx, dy, dz), (r[0], r[1], r[2])),
                refs=(datum.id,),
            ),
            convention="location",
            datum=datum,
            feature=feat,
        )
        for r, role, feat in unique
    ]


def plan_dimensions(model: PartModel) -> list[DimensionGroup]:
    """Plan each feature's parameters into one `DimensionGroup` (anchor + single
    view + planned dims, each carrying its render intent — convention, model-level
    suppression, datum). No cross- or within-feature value de-duplication."""
    groups: list[DimensionGroup] = []
    for feature in model.features:
        dims = []
        for p in feature.parameters():
            if p.kind == "location":
                continue
            # An authored ± tolerance (ADR 0011 §4 / P2a) rides on the decorations side-
            # layer keyed by (feature, kind); fold it onto the param so every renderer
            # sees one carrier. `kind` (not `role`) is the key — a step's length and
            # diameter share role="step", so role alone can't tell them apart.
            tol = model.decorations.get((feature, p.kind))
            if tol is not None:
                p = replace(p, tolerance=tol)
            suppressed, reason = _suppression(model, feature, p)
            dims.append(
                PlannedDimension(
                    param=p,
                    convention=_CONVENTION.get((p.role, p.kind), "linear"),
                    suppressed=suppressed,
                    reason=reason,
                    datum=_datum_for(model, p),
                )
            )
        if dims:
            groups.append(
                DimensionGroup(feature=feature, view=_group_view(feature), dims=tuple(dims))
            )
    return groups


@dataclass(frozen=True)
class SectionPlan:
    """A planned full section A–A: the plane is normal to Y at ``cut_y``, parallel to
    the front view. The *intent* (does the part need a section, and where the plane
    sits) — the rendering machinery (cut / hatch / cutting-plane arrows) stays shared
    infrastructure that consumes this (ADR 0008 Amendment 4 / #207)."""

    cut_y: float


def plan_sections(model: PartModel, feature_keys: set[HoleRef]) -> SectionPlan | None:
    """Decide whether a part needs a full section A–A, and where the plane cuts.

    Trigger: any Z-axis hole/pattern whose bore has a counterbore, spotface, or a
    non-through bottom — its internal profile is hidden-line-only in every standard
    view. The cut plane passes through the **densest row** of qualifying hole axes
    (ISO practice), tie-broken toward the part centre. Only holes whose positions are
    in *feature_keys* count (so a rotational part's concentric bores, dimensioned by
    the centreline leaders, don't drive a section). ``None`` when no section is
    warranted."""
    qual_ys: list[float] = []
    for f in model.features:
        if not isinstance(f, HoleFeature | PatternFeature) or f.frame.axis != "z":
            continue
        bore = f.member if isinstance(f, PatternFeature) else f  # the bore-carrying HoleFeature
        if bore.cbore is None and bore.spotface is None and bore.through:
            continue
        for m in f.members or (f.frame.origin,):
            if HoleRef.of(m) in feature_keys:
                qual_ys.append(m[1])
    if not qual_ys:
        return None
    cy = model.bbox.center().Y  # type: ignore[attr-defined]  # build123d BoundBox
    cut_y = max(
        {round(y, 1) for y in qual_ys},
        key=lambda v: (sum(1 for y in qual_ys if abs(y - v) <= 0.5), -abs(v - cy)),
    )
    return SectionPlan(cut_y=cut_y)
