"""Dimension selector vocabulary and semantic view/strip validation."""

from __future__ import annotations

from math import hypot
from typing import Any, Literal

from draftwright.view_plan import PRINCIPAL_VIEW_NAMES

PLACEMENT_VIEWS = frozenset(PRINCIPAL_VIEW_NAMES)
PLACEMENT_SIDES = frozenset({"above", "below", "left", "right"})


def validate_placement_intent(view: str | None, side: str | None, *, owner: str) -> None:
    """Validate the shared declarative view/strip vocabulary.

    Compatibility with a particular renderer is checked where the dimension kind and
    projection are known.  Keeping the vocabulary check here also protects hand-built IR.
    """
    if view is not None and view not in PLACEMENT_VIEWS:
        raise ValueError(f"{owner} view must be one of {sorted(PLACEMENT_VIEWS)} (got {view!r})")
    if side is not None and side not in PLACEMENT_SIDES:
        raise ValueError(f"{owner} side must be one of {sorted(PLACEMENT_SIDES)} (got {side!r})")


def _linear_projection_view(ref_pts) -> str | None:
    """Return the principal view that preserves an exact two-point oblique span."""
    if len(ref_pts) != 2:
        return None
    delta = tuple(right - left for left, right in zip(*ref_pts, strict=True))
    if hypot(*delta) <= 1e-9:
        return None
    primary = max(abs(component) for component in delta)
    projection_tolerance = max(0.005, primary * 1e-3)
    zeros = [
        index for index, component in enumerate(delta) if abs(component) <= projection_tolerance
    ]
    if len(zeros) != 1:
        return None
    return ("side", "front", "plan")[zeros[0]]


def _validate_authored_dimension_placement(
    dimension_kind: str,
    dominant_axis: str,
    view: str | None,
    side: str | None,
    *,
    owner: str,
    angular_reference: Any = None,
    cylindrical_refs=(),
    ref_pts=(),
) -> None:
    """Reject a view/side pair for which the authored-dimension renderer has no candidate."""
    validate_placement_intent(view, side, owner=owner)
    if view is None and side is None:
        return
    valid_pairs: tuple[tuple[str, str], ...]
    if dimension_kind == "angular" and angular_reference is not None:
        axis = angular_reference.principal_axis
        end_view = {"X": "side", "Y": "front", "Z": "plan"}.get(axis)
        first, second = angular_reference.rays
        bisector = tuple(a + b for a, b in zip(first, second, strict=True))
        length = hypot(*bisector)
        components = {"X": (1, 2), "Y": (0, 2), "Z": (0, 1)}.get(axis, ())
        valid_pairs = tuple(
            (end_view, (("left", "right"), ("below", "above"))[index][bisector[component] > 0])
            for index, component in enumerate(components)
            if end_view is not None and abs(bisector[component]) / length >= 1e-6
        )
    elif dimension_kind in ("diameter", "radius"):
        end_view = {"X": "side", "Y": "front", "Z": "plan"}.get(dominant_axis)
        if end_view is None and dimension_kind == "diameter" and cylindrical_refs:
            directions = {
                tuple(round(component, 9) for component in reference.axis_direction)
                for reference in cylindrical_refs
            }
            if len(directions) == 1:
                direction = next(iter(directions))
                end_view = next(
                    (
                        candidate
                        for component, candidate in zip(
                            direction, ("side", "front", "plan"), strict=True
                        )
                        if abs(component) <= 1e-6
                    ),
                    None,
                )
        if end_view == "plan" and dominant_axis == "?":
            valid_pairs = ((end_view, "right"), (end_view, "left"))
        else:
            valid_pairs = () if end_view is None else ((end_view, "above"), (end_view, "below"))
    elif dominant_axis == "?" and dimension_kind == "linear":
        projection = _linear_projection_view(ref_pts)
        valid_pairs = (
            ()
            if projection is None
            else tuple(
                (projection, candidate) for candidate in ("above", "below", "right", "left")
            )
        )
    else:
        valid_pairs = {
            "X": (("front", "above"), ("front", "below"))
            + (
                (("plan", "above"), ("plan", "below"))
                if dimension_kind in ("linear", "thickness")
                else ()
            ),
            "Z": (("front", "right"), ("front", "left")),
            "Y": (
                ("side", "above"),
                ("side", "below"),
                ("plan", "left"),
                ("plan", "right"),
            ),
        }.get(dominant_axis, ())
    if not any(
        (view is None or candidate_view == view) and (side is None or candidate_side == side)
        for candidate_view, candidate_side in valid_pairs
    ):
        choices = ", ".join(f"{v}/{s}" for v, s in valid_pairs) or "none"
        raise ValueError(
            f"{owner} cannot render {dimension_kind}/{dominant_axis} at "
            f"{view or '*'}/{side or '*'}; supported placement(s): {choices}"
        )


def _authored_dimension_target_view(
    dimension_kind: str,
    dominant_axis: str,
    view: str | None,
    side: str | None,
    angular_reference: Any = None,
    ref_pts=(),
) -> str | None:
    """Resolve the principal view selected by an explicit measured-dimension hint.

    A side can uniquely select a view even when ``view`` is omitted (for example a
    Y-linear ``right`` strip can only be the plan view). ``None`` means both placement
    fields were omitted and the renderer remains free to derive/fall back as before.
    Callers validate the pair with
    :func:`draftwright.model.ir.validate_authored_dimension_placement` first.
    """
    if view is not None:
        return view
    if dimension_kind == "angular" and angular_reference is not None:
        return {"X": "side", "Y": "front", "Z": "plan"}.get(angular_reference.principal_axis)
    if dimension_kind == "linear" and dominant_axis == "?":
        return _linear_projection_view(ref_pts)
    if side is None:
        return None
    if dimension_kind in ("diameter", "radius"):
        return {"X": "side", "Y": "front", "Z": "plan"}.get(dominant_axis)
    if dominant_axis in {"X", "Z"}:
        return "front"
    if dominant_axis == "Y":
        return "side" if side in {"above", "below"} else "plan"
    return None


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
# The semantic identity of one addressable measurement (ADR 4 (was 0016)). A readable string,
# not an opaque token: it surfaces in diagnostics, lint messages and (later) emitted
# `dimension(...)` lines, and must stay stable across re-detection and planner changes.
# See :attr:`draftwright.model.ir.DimParameter.parameter_id` for how it is derived.
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
    "blend.radius",
    "bolt_circle.diameter",
    "bore.depth",
    "bore.diameter",
    "boss.diameter",
    "boss_height.length",
    "chamfer.length",
    "seat_diameter.diameter",
    "seat_run.length",
    "seat_sweep.angle",
    "circular_step_depth.length",
    "circular_step_radius.radius",
    "channel_width.length",
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
    "gusset_leg.length.x",
    "gusset_leg.length.y",
    "gusset_leg.length.z",
    "gusset_location.length",
    "gusset_pitch.length",
    "gusset_spacing.length",
    "gusset_thickness.length",
    "height.length",
    "included.angle",
    "od.diameter",
    "oriented_slot_length.length",
    "oriented_slot_width.length",
    "pad_height.length",
    "pad_length.length",
    "pad_width.length",
    "pitch.length",
    "pocket_depth.length",
    "pocket_max_depth.length",
    "pocket_length.length",
    "pocket_width.length",
    "polygon_across_flats.length",
    "ramp_angle.angle",
    "ramp_run.length",
    "rectangular_blind_slot_depth.length",
    "rectangular_blind_slot_length.length",
    "rectangular_blind_slot_width.length",
    "round_bottom_blind_slot_flat_width.length",
    "round_bottom_blind_slot_length.length",
    "round_bottom_blind_slot_radius.radius",
    "stock_length.length",
    "profile_across_flats.length",
    "slot_length.length",
    "slot_end_radius.radius",
    "slot_width.length",
    "spotface.depth",
    "spotface.diameter",
    "step.diameter",
    "step.length",
    "step_height.length",
    "step_position.length",
    "thread.depth",
    "thickness.length",
    "through_step_leg.length.x",
    "through_step_leg.length.y",
    "through_step_leg.length.z",
    "width.length",
    # synthesised, not a DimParameter (see above)
    "location",
]
