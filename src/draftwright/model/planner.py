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

from dataclasses import dataclass

from draftwright._core import _END_ON
from draftwright.model.ir import Datum, DimParameter, Feature, PartModel, Point

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
    refs: list[tuple[Point, str]] = []
    for f in model.features:
        if f.frame.axis != "z":
            continue
        if f.kind == "hole":
            # un-patterned holes — a HoleFeature may group identical holes
            for m in getattr(f, "members", ()) or (f.frame.origin,):
                refs.append((m, "location"))
        elif f.kind == "pattern":
            members = getattr(f, "members", ())
            if getattr(f, "pattern", None) == "bolt_circle":
                refs.append((f.frame.origin, "location_pattern"))
            elif members:
                near = min(members, key=lambda m: (m[0] - dx) ** 2 + (m[1] - dy) ** 2)
                refs.append((near, "location_pattern"))
    unique: list[tuple[Point, str]] = []
    for r, role in refs:
        if not any(abs(r[0] - u[0]) < 0.5 and abs(r[1] - u[1]) < 0.5 for u, _ in unique):
            unique.append((r, role))
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
        )
        for r, role in unique
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
