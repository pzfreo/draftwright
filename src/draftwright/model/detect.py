"""detect — build the part-model IR by running the feature detectors (ADR 0008).

The front-end of the compiler. Each detector is an existing recognition heuristic
(:func:`recognise_holes`, :func:`recognise_turned_steps`, :func:`recognise_bosses`) adapted to
*emit* IR `Feature` objects — their B-rep logic is unchanged; only their output
shape is normalised into the waist. New shapes plug in here as new detectors
emitting new `Feature` types.

Turned profile and bosses are complementary, not competing (the #191 review): a
turned part is described by its `StepFeature`s (length + OD per segment); a
non-turned part's external diameters come from `BossFeature`s. Holes are detected
for any part.
"""

from __future__ import annotations

from draftwright._geometry import _axis_letter, _xyz
from draftwright.model.ir import (
    AUTHORED_DIMENSION_KINDS,
    AuthoredDimension,
    BossFeature,
    ChamferFeature,
    Datum,
    EnvelopeFeature,
    Feature,
    FilletFeature,
    FlatFeature,
    Frame,
    GrooveFeature,
    HoleFeature,
    PartModel,
    PatternFeature,
    PlateFeature,
    PmiFeature,
    PocketFeature,
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
    TurnedProfile,
    recognise_bosses,
    recognise_chamfers,
    recognise_countersinks,
    recognise_fillets,
    recognise_flats,
    recognise_grooves,
    recognise_hole_patterns,
    recognise_holes,
    recognise_plates,
    recognise_pockets,
    recognise_slots,
    recognise_step_shoulders,
    recognise_turned_steps,
)


def _member_hole(h, frame: Frame, members: tuple = (), count: int = 1) -> HoleFeature:
    """A recogniser hole → an IR `HoleFeature` (bore + counterbore/spotface/countersink).
    When *h* represents a machining-spec group of identical holes, *members* are their
    locations and *count* their number. The countersink rides on the HoleRecord (#558)."""
    return HoleFeature(
        frame=frame,
        diameter=h.diameter,
        depth=h.depth,
        through=(h.bottom == "through"),
        count=count,
        members=members,
        cbore=(h.cbore.diameter, h.cbore.depth) if h.cbore else None,
        spotface=(h.spotface.diameter, h.spotface.depth) if h.spotface else None,
        csink=(h.csink.major_diameter, h.csink.included_angle) if h.csink else None,
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


def _boss_is_groove_floor(b, grooves) -> bool:
    """A recognised boss coinciding with a groove floor — same turning axis and (floor) ø — is
    that floor. The groove callout already dimensions it, so it must not also get a boss ø
    (#148c review; applies whether or not the part read as a turned profile)."""
    ax = _axis_letter(b)
    return any(abs(b.diameter - g.diameter) <= _DIA_TOL and g.axis == ax for g in grooves)


_DIA_TOL = 0.15  # two ø values within this (mm) are the same diameter (#298)
_GROOVE_STEP_TOL = (
    0.1  # pad (mm) for a groove centre lying within its own turned-step span (#148c)
)
_STEP_LEN_PAD = 1.0  # a groove's step is no longer than its width + this (mm); guards merged runs
_UNSET = object()  # sentinel: distinguishes "not supplied" from a valid prof=None


def build_pmi_features(pmi, bbox) -> list[AuthoredDimension | PmiFeature]:
    """Re-home extracted STEP AP242 PMI records into drafting-concept IR (#208).

    Shared by :func:`build_part_model` (the detection path) and the declared-model PMI
    synthesis in ``builder._assemble`` (#472) so both construct features identically.
    Dimensional PMI becomes :class:`AuthoredDimension`, because users edit drafting
    dimensions rather than source-format PMI. Unsupported GD&T/datum records remain raw
    :class:`PmiFeature` fallbacks until their concept lowering lands. Empty/``None``
    ``pmi`` → ``[]``."""
    out: list[AuthoredDimension | PmiFeature] = []
    for r in pmi or ():
        if r.ref_bbox is not None:
            x0, y0, z0, x1, y1, z1 = r.ref_bbox
            pmi_origin = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
        else:
            pmi_origin = (bbox.center().X, bbox.center().Y, bbox.center().Z)
        ax = r.dominant_axis.lower() if r.dominant_axis in ("X", "Y", "Z") else "z"
        if r.kind in AUTHORED_DIMENSION_KINDS:
            out.append(
                AuthoredDimension(
                    frame=Frame(origin=pmi_origin, axis=ax),
                    dimension_kind=r.kind,
                    value=r.value,
                    label=r.label,
                    dominant_axis=r.dominant_axis,
                    upper_tol=r.upper_tol,
                    lower_tol=r.lower_tol,
                    ref_bbox=r.ref_bbox,
                    ref_pts=tuple(r.ref_pts),
                    source_kind=r.kind,
                )
            )
            continue
        out.append(
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
    return out


def build_part_model(
    part,
    *,
    holes=None,
    patterns=None,
    bosses=None,
    slots=None,
    pockets=None,
    prof=_UNSET,
    step_zs=None,
    rotational=None,
    pmi=None,
    cyls=None,
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
    rotational OD/bore furniture (#237).

    ``cyls`` is a precomputed ``analyse_cylinders(part)`` result threaded into every
    cylinder-substrate recogniser called here (holes/bosses/turned/grooves/flats), so
    the solid is scanned once per build (#703); omitted, each recogniser scans for
    itself."""
    bbox = part.bounding_box()
    features: list[Feature] = []

    # Holes and hole patterns. A recognised pattern becomes one PatternFeature
    # (count× member-diameter + pattern dims); its member holes are NOT also
    # emitted individually — the grouped-callout rule the engine uses.
    if holes is None:
        holes = recognise_holes(part, cyls=cyls, csinks=recognise_countersinks(part))
    if patterns is None:
        patterns = recognise_hole_patterns(holes)
    patterned: set[int] = set()
    for pat in patterns:
        members = list(pat.holes)
        patterned.update(id(h) for h in members)
        features.append(_pattern_feature(pat, members))
    # Un-patterned holes: group by machining spec so identical holes share one
    # count× callout (the engine's grouped-callout rule); HoleSpec keys on the
    # snapped axis and the countersink too, so opposite-face drillings and csk-vs-plain
    # holes stay distinct.
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
        slots = recognise_slots(part)
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

    # Blind rectangular recesses — floored slots/pockets (#148a).
    if pockets is None:
        pockets = recognise_pockets(part)
    for pk in pockets:
        # Frame at the recess centroid — in-plane centre + mid-depth. The render
        # leader projects into the view normal to the depth axis, so the depth coord
        # is inert, but a true centroid keeps the frame honest.
        c = {
            pk.long_axis: (pk.lo + pk.hi) / 2,
            pk.width_axis: pk.w_center,
            pk.depth_axis: (pk.d_lo + pk.d_hi) / 2,
        }
        features.append(
            PocketFeature(
                frame=Frame(origin=(c["x"], c["y"], c["z"]), axis=pk.long_axis),
                width_axis=pk.width_axis,
                long_axis=pk.long_axis,
                width=pk.width,
                length=pk.length,
                depth=pk.depth,
                w_center=pk.w_center,
                lo=pk.lo,
                hi=pk.hi,
            )
        )

    # Turned / circlip grooves (#148c) — recognised up front so the turned-step chain can
    # exclude any band a groove already dimensions: a groove floor is an annular band, and
    # its two walls read as shoulders, so recognise_turned_steps also delimits it as a
    # middle "step". Emitting both a StepFeature and a GrooveFeature for one band would
    # double-dimension the floor ø (ISO 129) and break ADR 0008's one-band-one-owner waist.
    grooves = recognise_grooves(part, cyls=cyls)

    # Turned profile → step segments; else external bosses → diameters.
    if prof is _UNSET:
        prof = TurnedProfile.from_steps(recognise_turned_steps(part, cyls=cyls))
    orientation = prof.axis if prof is not None else None
    if prof is not None:
        idx = "xyz".index(prof.axis)
        c = bbox.center()
        base = [c.X, c.Y, c.Z]
        groove_bands = [(g.at[idx], g.width) for g in grooves if g.axis == prof.axis]
        for s in prof.steps:
            s_mid = (s.lo + s.hi) / 2
            # Skip the band a groove owns (its callout dimensions width + floor ø). Match on
            # axial POSITION, not diameter: a narrow groove's step is reported at the WALL OD
            # (local_od's pad engulfs both walls when the groove is < ~1.4 mm), so a floor-ø
            # match would silently miss the common circlip case. The groove centre lies within
            # its own step span; the short-length guard keeps a merged shaft run from matching.
            if any(
                s.lo - _GROOVE_STEP_TOL <= gc <= s.hi + _GROOVE_STEP_TOL
                and s.length <= gw + _STEP_LEN_PAD
                for gc, gw in groove_bands
            ):
                continue
            lo = list(base)
            hi = list(base)
            lo[idx] = s.lo
            hi[idx] = s.hi
            mid = list(base)
            mid[idx] = s_mid
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
        # with the feature_diameters inventory the coverage lint checks against. A groove
        # floor is likewise a narrow reduced band, but the groove callout already carries its
        # ø, so it is suppressed here (_boss_is_groove_floor) to avoid a duplicate boss ø.
        step_dias = [s.diameter for s in prof.steps]
        raw_bosses = recognise_bosses(part, cyls=cyls) if bosses is None else bosses
        for b in _distinct_by_diameter(raw_bosses):
            if all(
                abs(b.diameter - d) > _DIA_TOL for d in step_dias
            ) and not _boss_is_groove_floor(b, grooves):
                features.append(
                    BossFeature(
                        frame=Frame(origin=_xyz(b.location), axis=_axis_letter(b)),
                        diameter=b.diameter,
                    )
                )
    else:
        raw_bosses = recognise_bosses(part, cyls=cyls) if bosses is None else bosses
        bosses_d = _distinct_by_diameter(raw_bosses)
        for b in bosses_d:
            # A grooved round body can still fail the turned-step squareness gate (e.g. a
            # shaft with a rectangular flange) and land here with prof=None. Suppress the
            # groove-floor boss so its ø is not dimensioned twice — boss ø + groove callout
            # (#148c 3rd-pass review).
            if _boss_is_groove_floor(b, grooves):
                continue
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

    # Plate/wall thicknesses on a multi-plate prismatic (#559) — the thin extent of a
    # slab that no other prismatic dim recovers (a wall along X/Y, or a Z base plate too
    # thin for the step-ladder legibility gate). Skipped for turned/rotational parts,
    # whose extents are the OD/length chain, not plate thicknesses.
    #
    # Scope guard: only a GENUINE multi-plate part — slabs on ≥2 distinct axes (a base +
    # an upright wall, i.e. an L/T/U bracket) — is dimensioned this way. A single-axis
    # stack (a base slab under a smaller stacked block) is a *staircase*, owned by the
    # step-height ladder; treating its base as a "plate" would wrongly suppress the step
    # dim (#559 review). This keeps the plate feature to the issue's stated domain.
    plate_zs_at_base: set = set()
    if prof is None and rotational is None:
        plates = recognise_plates(part)
        if len({pl.axis for pl in plates}) >= 2:
            c = bbox.center()
            for pl in plates:
                features.append(
                    PlateFeature(
                        frame=Frame((c.X, c.Y, c.Z), pl.axis),
                        axis=pl.axis,
                        lo=pl.lo,
                        hi=pl.hi,
                        u=pl.u,
                        v=pl.v,
                    )
                )
                # A Z base plate (bottom == part base) IS the first step level; suppress
                # it from the step ladder so the two don't both dimension base→hi.
                if pl.axis == "z" and abs(pl.lo - bbox.min.Z) < 0.5:
                    plate_zs_at_base.add(round(pl.hi, 3))

    # Prismatic step-height ladder — horizontal face levels on a NON-turned part
    # (a turned part's steps are StepFeatures, dimensioned by the IR length chain).
    if prof is None and step_zs:
        c = bbox.center()
        _levels = tuple(sorted(z for z in step_zs if round(z, 3) not in plate_zs_at_base))
        if _levels:
            # The in-plane step POSITIONS (#555) — where each shoulder sits along its
            # axis — so the part is fully constrained, not just given two heights. Scoped
            # to a SINGLE step level (a rebate/shoulder, the issue's domain): a multi-level
            # staircase is owned by the height ladder's typ-collapse / detail-view path,
            # and adding position dims to already-crowded shoulders would worsen it.
            _shoulders = (
                tuple(
                    (s.axis, s.position)
                    for s in recognise_step_shoulders(part, levels=list(_levels))
                )
                if len(_levels) == 1
                else ()
            )
            features.append(
                StepLevelFeature(
                    frame=Frame((c.X, c.Y, bbox.min.Z), "z"),
                    base=bbox.min.Z,
                    levels=_levels,
                    shoulders=_shoulders,
                    datum=(bbox.min.X, bbox.min.Y, bbox.min.Z),
                )
            )

    # Chamfers (#560) — oblique planar faces on a non-turned part, called out C{leg} /
    # {leg}×{angle}°. A turned part's chamfers are conical (recognise_chamfers finds none).
    if rotational is None:
        for ch in recognise_chamfers(part):
            at = ch.at
            features.append(
                ChamferFeature(
                    frame=Frame((at[0], at[1], at[2]), ch.axis),
                    axis=ch.axis,
                    leg1=ch.leg1,
                    leg2=ch.leg2,
                    angle=ch.angle,
                )
            )

        # Fillets (#561) — external edge rounds on a non-turned part, called out R{radius}
        # (grouped n× at render). Same non-rotational guard as chamfers.
        for fl in recognise_fillets(part):
            at = fl.at
            features.append(
                FilletFeature(
                    frame=Frame((at[0], at[1], at[2]), fl.axis),
                    axis=fl.axis,
                    radius=fl.radius,
                )
            )

    # Machined flats on round stock (#148b) — a planar face truncating a cylinder,
    # called out by its across-flats size. Detected UNCONDITIONALLY (not gated by the
    # rotational branch): a D-shaft / hex head IS round stock and classifies rotational,
    # yet its flat still needs a callout. The recogniser self-gates on OD adjacency, so a
    # part with no round stock yields none.
    for flat in recognise_flats(part, cyls=cyls):
        at = flat.at
        features.append(
            FlatFeature(
                frame=Frame((at[0], at[1], at[2]), flat.axis),
                axis=flat.axis,
                across=flat.across,
            )
        )

    # Turned / circlip grooves on round stock (#148c) — an annular channel (a strict
    # local-minimum OD band) dimensioned by width + floor diameter, recognised above so the
    # turned-step chain can exclude the coincident band. Also UNCONDITIONAL: a grooved shaft
    # is round stock and classifies rotational, yet the groove still needs its own callout.
    # The recogniser self-gates on external OD bands, so a prismatic part yields none.
    for groove in grooves:
        at = groove.at
        features.append(
            GrooveFeature(
                frame=Frame((at[0], at[1], at[2]), groove.axis),
                axis=groove.axis,
                width=groove.width,
                diameter=groove.diameter,
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

    # STEP AP242 PMI — re-homed into drafting-concept IR where possible (#208).
    # Rendered directly by render_pmi; the planner adds nothing.
    features.extend(build_pmi_features(pmi, bbox))

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
