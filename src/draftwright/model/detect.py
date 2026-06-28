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

from draftwright._core import _axis_letter
from draftwright.model.ir import (
    BossFeature,
    Feature,
    Frame,
    HoleFeature,
    PartModel,
    PatternFeature,
    Point,
    StepFeature,
)
from draftwright.recognition import (
    BoltCircle,
    LinearArray,
    RectGrid,
    find_bosses,
    find_hole_patterns,
    find_holes,
    find_turned_steps,
)


def _pt(loc) -> Point:
    """A build123d Vector or sequence → an (x, y, z) tuple."""
    if hasattr(loc, "X"):
        return (loc.X, loc.Y, loc.Z)
    x, y, z = loc
    return (float(x), float(y), float(z))


def _pattern_feature(pat, members) -> PatternFeature:
    """Map a recognised pattern + its member holes to a `PatternFeature`."""
    axis = _axis_letter(members[0])
    dia = members[0].diameter
    n = len(members)
    if isinstance(pat, BoltCircle):
        return PatternFeature(
            frame=Frame(_pt(pat.center), axis),
            pattern="bolt_circle",
            count=n,
            hole_diameter=dia,
            bcd=pat.diameter,
        )
    if isinstance(pat, LinearArray):
        cx = sum(m.location[0] for m in members) / n
        cy = sum(m.location[1] for m in members) / n
        cz = sum(m.location[2] for m in members) / n
        return PatternFeature(
            frame=Frame((cx, cy, cz), axis),
            pattern="linear",
            count=n,
            hole_diameter=dia,
            pitch=pat.pitch,
        )
    if isinstance(pat, RectGrid):
        return PatternFeature(
            frame=Frame(_pt(pat.center), axis),
            pattern="grid",
            count=pat.rows * pat.cols,
            hole_diameter=dia,
            grid=(pat.row_pitch, pat.col_pitch),
        )
    # Unknown pattern type — represent it as a plain count× callout.
    return PatternFeature(
        frame=Frame(_pt(members[0].location), axis), pattern="other", count=n, hole_diameter=dia
    )


def _distinct_by_diameter(bosses, tol: float = 0.15):
    """One representative boss per distinct external diameter."""
    out: dict[float, object] = {}
    for b in bosses:
        key = next((k for k in out if abs(k - b.diameter) <= tol), b.diameter)
        out.setdefault(key, b)
    return list(out.values())


def build_part_model(part) -> PartModel:
    """Run the detectors and assemble the :class:`PartModel` IR for *part*."""
    bbox = part.bounding_box()
    features: list[Feature] = []

    # Holes and hole patterns. A recognised pattern becomes one PatternFeature
    # (count× member-diameter + pattern dims); its member holes are NOT also
    # emitted individually — the grouped-callout rule the engine uses.
    holes = find_holes(part)
    patterns = find_hole_patterns(holes)
    patterned: set[int] = set()
    for pat in patterns:
        members = list(pat.holes)
        patterned.update(id(h) for h in members)
        features.append(_pattern_feature(pat, members))
    for h in holes:
        if id(h) in patterned:
            continue
        features.append(
            HoleFeature(
                frame=Frame(origin=_pt(h.location), axis=_axis_letter(h)),
                diameter=h.diameter,
                depth=h.depth,
                through=(h.bottom == "through"),
                cbore=(h.cbore.diameter, h.cbore.depth) if h.cbore else None,
                spotface=(h.spotface.diameter, h.spotface.depth) if h.spotface else None,
            )
        )

    # Turned profile → step segments; else external bosses → diameters.
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
        for b in _distinct_by_diameter(find_bosses(part)):
            features.append(
                BossFeature(
                    frame=Frame(origin=_pt(b.location), axis=_axis_letter(b)),
                    diameter=b.diameter,
                )
            )

    return PartModel(bbox=bbox, orientation=orientation, features=features)
