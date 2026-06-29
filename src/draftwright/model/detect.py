"""detect — build the part-model IR by running the feature detectors (ADR 0008).

The front-end of the compiler. Each detector is an existing recognition heuristic
(:func:`find_holes`, :func:`find_turned_steps`, :func:`find_bosses`) adapted to
*emit* IR `Feature` objects — their B-rep logic is unchanged; only their output
shape is normalised into the waist. New shapes plug in here as new detectors
emitting new `Feature` types.

Turned profile and bosses are complementary, not competing (the #191 review): a
turned part is described by its `StepFeature`s (length + OD per segment); a
non-turned part's external diameters come from `BossFeature`s. Holes are detected
for any part.
"""

from __future__ import annotations

from draftwright._core import _axis_letter, _xyz
from draftwright.model.ir import (
    BossFeature,
    EnvelopeFeature,
    Feature,
    Frame,
    HoleFeature,
    PartModel,
    PatternFeature,
    SlotFeature,
    StepFeature,
)
from draftwright.recognition import (
    BoltCircle,
    LinearArray,
    RectGrid,
    find_bosses,
    find_hole_patterns,
    find_holes,
    find_slots,
    find_turned_steps,
)


def _member_hole(h, frame: Frame) -> HoleFeature:
    """A recogniser hole → an IR `HoleFeature` (bore + counterbore/spotface)."""
    return HoleFeature(
        frame=frame,
        diameter=h.diameter,
        depth=h.depth,
        through=(h.bottom == "through"),
        cbore=(h.cbore.diameter, h.cbore.depth) if h.cbore else None,
        spotface=(h.spotface.diameter, h.spotface.depth) if h.spotface else None,
    )


def _pattern_feature(pat, members) -> PatternFeature:
    """Map a recognised pattern + its member holes to a `PatternFeature`,
    composing a representative member hole so its counterbore/spotface survive."""
    axis = _axis_letter(members[0])
    n = len(members)
    locs = tuple(_xyz(m.location) for m in members)  # raw arrangement — never discarded
    if isinstance(pat, BoltCircle):
        frame = Frame(_xyz(pat.center), axis)
        return PatternFeature(
            frame,
            "bolt_circle",
            n,
            _member_hole(members[0], frame),
            members=locs,
            bcd=pat.diameter,
        )
    if isinstance(pat, LinearArray):
        c = (
            sum(m.location[0] for m in members) / n,
            sum(m.location[1] for m in members) / n,
            sum(m.location[2] for m in members) / n,
        )
        frame = Frame(c, axis)
        return PatternFeature(
            frame,
            "linear",
            n,
            _member_hole(members[0], frame),
            members=locs,
            pitch=pat.pitch,
            direction=tuple(pat.direction),
        )
    if isinstance(pat, RectGrid):
        frame = Frame(_xyz(pat.center), axis)
        return PatternFeature(
            frame,
            "grid",
            n,
            _member_hole(members[0], frame),
            members=locs,
            grid=(pat.row_pitch, pat.col_pitch),
            rows=pat.rows,
            cols=pat.cols,
            angle=pat.angle,
        )
    frame = Frame(_xyz(members[0].location), axis)  # unknown type — plain count× callout
    return PatternFeature(frame, "other", n, _member_hole(members[0], frame), members=locs)


def _distinct_by_diameter(bosses, tol: float = 0.15):
    """One representative boss per distinct external diameter."""
    out: dict[float, object] = {}
    for b in bosses:
        key = next((k for k in out if abs(k - b.diameter) <= tol), b.diameter)
        out.setdefault(key, b)
    return list(out.values())


_UNSET = object()  # sentinel: distinguishes "not supplied" from a valid prof=None


def build_part_model(part, *, holes=None, patterns=None, slots=None, prof=_UNSET) -> PartModel:
    """Run the detectors and assemble the :class:`PartModel` IR for *part*.

    The detected feature sets may be **supplied** by the caller (from `_analyse`,
    which already ran them) so detection happens **once per build** — the single
    feature inventory (ADR 0008 Amendment 5, #244). Omitted sets are detected here,
    so a standalone ``build_part_model(part)`` still works. ``prof`` uses a sentinel
    because ``None`` is a valid value (a non-turned part)."""
    bbox = part.bounding_box()
    features: list[Feature] = []

    # Holes and hole patterns. A recognised pattern becomes one PatternFeature
    # (count× member-diameter + pattern dims); its member holes are NOT also
    # emitted individually — the grouped-callout rule the engine uses.
    if holes is None:
        holes = find_holes(part)
    if patterns is None:
        patterns = find_hole_patterns(holes)
    patterned: set[int] = set()
    for pat in patterns:
        members = list(pat.holes)
        patterned.update(id(h) for h in members)
        features.append(_pattern_feature(pat, members))
    for h in holes:
        if id(h) in patterned:
            continue
        features.append(_member_hole(h, Frame(origin=_xyz(h.location), axis=_axis_letter(h))))

    # Milled slots / reduced across-flats sections (detected for any part).
    if slots is None:
        slots = find_slots(part)
    for sl in slots:
        idx = "xyz".index(sl.long_axis)
        origin = [bbox.center().X, bbox.center().Y, bbox.center().Z]
        origin[idx] = (sl.lo + sl.hi) / 2
        features.append(
            SlotFeature(
                frame=Frame(origin=(origin[0], origin[1], origin[2]), axis=sl.long_axis),
                width_axis=sl.width_axis,
                long_axis=sl.long_axis,
                width=sl.width,
                length=sl.length,
                w_center=sl.w_center,
                lo=sl.lo,
                hi=sl.hi,
            )
        )

    # Turned profile → step segments; else external bosses → diameters.
    if prof is _UNSET:
        prof = find_turned_steps(part)
    orientation = prof.axis if prof is not None else None
    if prof is not None:
        idx = "xyz".index(prof.axis)
        c = bbox.center()
        base = [c.X, c.Y, c.Z]
        for s in prof.steps:
            lo = list(base)
            hi = list(base)
            lo[idx] = s.lo
            hi[idx] = s.hi
            mid = list(base)
            mid[idx] = (s.lo + s.hi) / 2
            features.append(
                StepFeature(
                    frame=Frame(origin=(mid[0], mid[1], mid[2]), axis=prof.axis),
                    length=s.length,
                    diameter=s.diameter,
                    span=((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])),
                )
            )
    else:
        bosses = _distinct_by_diameter(find_bosses(part))
        for b in bosses:
            features.append(
                BossFeature(
                    frame=Frame(origin=_xyz(b.location), axis=_axis_letter(b)),
                    diameter=b.diameter,
                )
            )
        # Overall envelope dims for a *prismatic* part — not a round single-OD body
        # (a boss whose diameter fills the footprint is the body, dimensioned by its
        # OD, not a box).
        if not _is_round(bbox, bosses):
            c = bbox.center()
            features.append(
                EnvelopeFeature(
                    frame=Frame((c.X, c.Y, c.Z), "z"),
                    width=bbox.size.X,
                    height=bbox.size.Z,
                    depth=bbox.size.Y,
                    bbox_min=(bbox.min.X, bbox.min.Y, bbox.min.Z),
                    bbox_max=(bbox.max.X, bbox.max.Y, bbox.max.Z),
                )
            )

    return PartModel(bbox=bbox, orientation=orientation, features=features)


def _is_round(bbox, bosses, tol: float = 0.5) -> bool:
    """True when a boss's OD fills the part footprint — a round body of revolution,
    dimensioned by its OD rather than a width×depth box."""
    return any(
        abs(b.diameter - bbox.size.X) <= tol and abs(b.diameter - bbox.size.Y) <= tol
        for b in bosses
    )
