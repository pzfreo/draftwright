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
    Datum,
    EnvelopeFeature,
    Feature,
    Frame,
    HoleFeature,
    PartModel,
    PatternFeature,
    PmiFeature,
    RotationalFeature,
    SlotFeature,
    StepFeature,
    StepLevelFeature,
)
from draftwright.recognition import (
    BoltCircle,
    HoleSpec,
    LinearArray,
    RectGrid,
    find_bosses,
    find_hole_patterns,
    find_holes,
    find_slots,
    find_turned_steps,
)


def _member_hole(h, frame: Frame, members: tuple = (), count: int = 1) -> HoleFeature:
    """A recogniser hole → an IR `HoleFeature` (bore + counterbore/spotface). When
    *h* represents a machining-spec group of identical holes, *members* are their
    locations and *count* their number."""
    return HoleFeature(
        frame=frame,
        diameter=h.diameter,
        depth=h.depth,
        through=(h.bottom == "through"),
        count=count,
        members=members,
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


_DIA_TOL = 0.15  # two ø values within this (mm) are the same diameter (#298)
_UNSET = object()  # sentinel: distinguishes "not supplied" from a valid prof=None


def build_part_model(
    part,
    *,
    holes=None,
    patterns=None,
    bosses=None,
    slots=None,
    prof=_UNSET,
    step_zs=None,
    rotational=None,
    pmi=None,
) -> PartModel:
    """Run the detectors and assemble the :class:`PartModel` IR for *part*.

    The detected feature sets may be **supplied** by the caller (from `_analyse`,
    which already ran them) so detection happens **once per build** — the single
    feature inventory (ADR 0008 Amendment 5, #244). Omitted sets are detected here,
    so a standalone ``build_part_model(part)`` still works. ``prof`` uses a sentinel
    because ``None`` is a valid value (a non-turned part).

    ``step_zs`` (prismatic horizontal face levels) and ``rotational`` (``(od, bores)``
    or ``None``) are *classification* inputs from `_analyse` — the IR can't derive
    them from geometry alone — feeding the prismatic step ladder (#237) and the
    rotational OD/bore furniture (#237)."""
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
    # Un-patterned holes: group by machining spec so identical holes share one
    # count× callout (the engine's grouped-callout rule); HoleSpec keys on the
    # snapped axis too, so opposite-face drillings stay distinct.
    spec_groups: dict = {}
    for h in holes:
        if id(h) in patterned:
            continue
        spec_groups.setdefault(HoleSpec.from_hole(h), []).append(h)
    for grp in spec_groups.values():
        rep = grp[0]
        frame = Frame(origin=_xyz(rep.location), axis=_axis_letter(rep))
        mem_locs = tuple(_xyz(h.location) for h in grp)
        features.append(_member_hole(rep, frame, members=mem_locs, count=len(grp)))

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
        # A narrow external band nested under / beside a larger OD reads as that OD in
        # local_od's max(), so it never becomes a step diameter and goes silently
        # undimensioned (#298). Emit each band the silhouette steps miss as a boss, so
        # render_diameters still gives it a ø callout — aligning the callout inventory
        # with the feature_diameters inventory the coverage lint checks against.
        step_dias = [s.diameter for s in prof.steps]
        raw_bosses = find_bosses(part) if bosses is None else bosses
        for b in _distinct_by_diameter(raw_bosses):
            if all(abs(b.diameter - d) > _DIA_TOL for d in step_dias):
                features.append(
                    BossFeature(
                        frame=Frame(origin=_xyz(b.location), axis=_axis_letter(b)),
                        diameter=b.diameter,
                    )
                )
    else:
        raw_bosses = find_bosses(part) if bosses is None else bosses
        bosses_d = _distinct_by_diameter(raw_bosses)
        for b in bosses_d:
            features.append(
                BossFeature(
                    frame=Frame(origin=_xyz(b.location), axis=_axis_letter(b)),
                    diameter=b.diameter,
                )
            )
        # Overall envelope dims for a *prismatic* part — not a round single-OD body
        # (a boss whose diameter fills the footprint is the body, dimensioned by its
        # OD, not a box).
        if not _is_round(bbox, bosses_d):
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

    # Prismatic step-height ladder — horizontal face levels on a NON-turned part
    # (a turned part's steps are StepFeatures, dimensioned by the IR length chain).
    if prof is None and step_zs:
        c = bbox.center()
        features.append(
            StepLevelFeature(
                frame=Frame((c.X, c.Y, bbox.min.Z), "z"),
                base=bbox.min.Z,
                levels=tuple(sorted(step_zs)),
            )
        )

    # Rotational furniture — OD + centrelines + concentric bore leaders (#237). Its
    # presence marks the part rotational; emitted from the classification (od, bores).
    if rotational is not None:
        od, bores, rot_axis = rotational
        c = bbox.center()
        features.append(
            RotationalFeature(frame=Frame((c.X, c.Y, c.Z), rot_axis), od=od, bores=tuple(bores))
        )

    # Pre-authored PMI annotations (STEP AP242) — re-homed into the IR as features
    # (#208). Rendered directly by render_pmi (the planner adds nothing — see PmiFeature).
    if pmi:
        for r in pmi:
            if r.ref_bbox is not None:
                x0, y0, z0, x1, y1, z1 = r.ref_bbox
                pmi_origin = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
            else:
                pmi_origin = (bbox.center().X, bbox.center().Y, bbox.center().Z)
            ax = r.dominant_axis.lower() if r.dominant_axis in ("X", "Y", "Z") else "z"
            features.append(
                PmiFeature(
                    frame=Frame(origin=pmi_origin, axis=ax),
                    pmi_kind=r.kind,
                    value=r.value,
                    label=r.label,
                    dominant_axis=r.dominant_axis,
                    ref_bbox=r.ref_bbox,
                    ref_pts=tuple(r.ref_pts),
                )
            )

    # The default location datum — the part's min-X/min-Y/min-Z corner (lower-left
    # in the plan view), per inspection practice. Hole location dims measure from
    # it (#238); a human/LLM pass can re-anchor.
    datums = [Datum(id="datum_xy", kind="point", at=(bbox.min.X, bbox.min.Y, bbox.min.Z))]
    return PartModel(bbox=bbox, orientation=orientation, features=features, datums=datums)


def _is_round(bbox, bosses, tol: float = 0.5) -> bool:
    """True when a boss's OD fills the part footprint — a round body of revolution,
    dimensioned by its OD rather than a width×depth box."""
    return any(
        abs(b.diameter - bbox.size.X) <= tol and abs(b.diameter - bbox.size.Y) <= tol
        for b in bosses
    )
