"""The annotation orchestrator (#138 / ADR 0005, P5e).

`_auto_annotate` is the single entry point: it builds the IR (`build_part_model`),
plans the dimensions once, and drives the IR renderers (`from_model.render_*`) +
the capability passes (annotations.{sections,turned,pmi,holes}) + the title block.
Takes a duck-typed `dwg` and the `Analysis` namespace `a`. Imports only `_core`,
`layout`, the annotations passes, and third-party libs -- never make_drawing --
so the module graph stays a DAG.

The OD/centreline/bore furniture and the prismatic step-height + overall-height
ladder are now IR renderers (`render_rotational`/`render_height_ladder`, #237), not
inline. What remains inline is the orchestration/classification glue (concentric
bore set, side-drilled locations, the hole table) + the section/PMI passes.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

from draftwright._core import (
    _CONCENTRIC_TOL_MM,
    _SLOT_DIM_DEPTH,
    _TABULATE_MIN_HOLES,
    Analysis,
    HoleRef,
    _add_title_block,
    _axis_letter,
    _fmt,
    _iso_bbox,
    _log,
    _tag_sequence,
)
from draftwright.annotations.from_model import (
    _env_pd,
    env_dim_placed,
    envelope_group,
    render_centermarks,
    render_diameters,
    render_envelope,
    render_height_ladder,
    render_locations,
    render_pmi,
    render_rotational,
    render_slots,
    render_step_lengths,
)
from draftwright.annotations.holes import (
    _annotate_holes,
    _locate_off_axis_holes,
)
from draftwright.annotations.sections import (
    _add_section_view,
    _request_prismatic_detail,
    _reserve_section_row,
    _resolve_details,
)
from draftwright.model import PatternFeature, build_part_model, plan_dimensions, plan_sections
from draftwright.recognition import (
    full_cylinders,
)


def _wrap_rows(header, data, ncols):
    """Reshape *data* rows into *ncols* side-by-side blocks (a wider, shorter
    table), each block headed by *header* — so a long hole chart fits the page.
    """
    per = math.ceil(len(data) / ncols)
    blank = ("",) * len(header)
    wide = [tuple(header) * ncols]
    for r in range(per):
        row: tuple = ()
        for c in range(ncols):
            idx = c * per + r
            row += data[idx] if idx < len(data) else blank
        wide.append(row)
    return wide


def _is_concentric_hole(h, a: Analysis) -> bool:
    """True when *h* is an axial bore on the part centreline (turned base set)."""
    if _axis_letter(h) != "z":
        return False
    return math.hypot(h.location[0] - a.cx, h.location[1] - a.cy) <= _CONCENTRIC_TOL_MM


def _concentric_bore_diams(a: Analysis) -> list:
    """Distinct bore diameters on the rotation axis, in z_diams order (#10).

    ``a.z_diams`` carries every Z cylinder diameter — including off-axis ones
    such as a bolt circle's holes — so the bore-leader set is restricted to
    diameters that actually have an *internal* Z cylinder whose axis sits on
    the part centreline.  The OD is excluded.  Returned in z_diams order so
    label ordering is stable.
    """
    z_cyls, _ = a.cyls
    concentric = {
        c["diameter"]
        for c in full_cylinders(z_cyls)
        if not c["external"]
        and math.hypot(c["axis_xyz"][0] - a.cx, c["axis_xyz"][1] - a.cy) <= _CONCENTRIC_TOL_MM
    }
    return [d for d in a.z_diams if d != a.od_diam and any(abs(d - c) <= 0.15 for c in concentric)]


def _auto_annotate(dwg, a: Analysis, *, detail_view: bool = False):
    """Add the standard automatic dimensions, centrelines, and title block."""
    # Idempotent: clear build-time lint state so a second annotation pass does
    # not accumulate duplicate drop records.
    dwg._reset_build_issues()
    dwg._reset_dropped_callout_diams()
    dwg._detail_requests = []  # renderers queue enlarged-detail requests here (#307)
    dwg._escalations = []  # placers collect Escalation objects here (ADR 0009 Amdt 1, #351)

    FX = a.proj.front_x
    FZ = a.proj.front_z
    SX = a.proj.side_x
    SZ = a.proj.side_z
    PX = a.proj.plan_x
    PY = a.proj.plan_y

    # Tighten right-strip outer_limits to the actual iso view left edge now
    # that the iso has been projected and fitted.  Always apply so that any
    # future allocations are bounded; warn when the cursor has already passed
    # the limit (dims already placed may overlap the iso view).
    _iso_x0, _iso_y0, _, _iso_y1 = _iso_bbox(dwg)
    _iso_x_limit = _iso_x0 - 4
    # Only tighten a right strip when the iso shares the strip's y-range: a strip
    # that abuts the iso horizontally would otherwise lose annotation space, while
    # one sitting entirely above/below the iso (e.g. the SV strip when the iso is
    # in an upper-right zone) must keep its full width — capping it could push the
    # outer_limit below the strip anchor and break all its allocations.
    _right_strips = []
    for _rs, _y0, _y1 in (
        (a.fv_zones.right, a.FV_Y - a.fv_hh, a.FV_Y + a.fv_hh),
        (a.pv_zones.right, a.PV_Y - a.pv_hh, a.PV_Y + a.pv_hh),
        (a.sv_zones.right, a.SV_Y - a.fv_hh, a.SV_Y + a.fv_hh),
    ):
        if _y0 < _iso_y1 and _iso_y0 < _y1:
            _right_strips.append(_rs)
    for _rs in _right_strips:
        _rs.outer_limit = min(_rs.outer_limit, _iso_x_limit)

    # Per-hole annotations from the feature records (#91, #92, #95): each
    # hole is annotated in the view its axis is normal to.
    # to_page maps a model-space *location* (x, y, z) → page coords (IR-typed, not a
    # recogniser Hole — ADR 0008 Amendment 6).
    view_of_axis = {
        "z": ("plan", lambda loc: (PX(loc[0]), PY(loc[1]))),
        "y": ("front", lambda loc: (FX(loc[0]), FZ(loc[2]))),
        "x": ("side", lambda loc: (SX(loc[1]), SZ(loc[2]))),
    }

    # The part model — built once and rendered from for the IR-migrated passes
    # (centre marks, turned diameters/lengths); ADR 0008 convergence / #229.
    # Concentric bore leaders are a Z-axis construction (bores on the vertical
    # rotation axis); a horizontal (X/Y) round body gets OD + centrelines only
    # (#222), so its bore set is empty.
    _bores = tuple(_concentric_bore_diams(a)) if a.is_rotational and a.od_axis == "z" else ()
    _model = build_part_model(
        a.part,
        holes=a.holes,
        patterns=a.patterns,
        bosses=a.bosses,
        slots=a.slots,
        prof=a.prof,
        step_zs=a.step_zs,
        rotational=(a.od_diam, _bores, a.od_axis) if a.is_rotational else None,
        pmi=a.pmi,
    )
    # Plan the dimensions ONCE and thread the groups to every renderer that reads them
    # (was recomputed per renderer, #275). One rule set over DimParameters, literally.
    _groups = plan_dimensions(_model)

    # Rotational furniture — OD dim + axis centrelines + concentric bore leaders — IR
    # renderer (#237), placed early like the engine's inline block it replaces.
    render_rotational(dwg, _model, a)

    # Centre marks for every hole (all part classes) — IR renderer.
    render_centermarks(dwg, _groups)

    # Hole callouts, location dims, and the section view fire on *feature
    # presence*, independent of the turned/prismatic class (#10): the
    # classification only selects the base set (OD+centreline+ldr_z vs envelope
    # dims).  A turned flange (round OD + a bolt circle) must get BOTH.
    #
    # On a turned part the concentric, axis-aligned bores are already
    # dimensioned by the ldr_z leaders, so they are excluded here to avoid a
    # duplicate hole callout; only the off-axis features get callouts.  On a
    # prismatic part every hole flows through unchanged.
    feature_holes = a.holes
    if a.is_rotational:
        feature_holes = [h for h in a.holes if not _is_concentric_hole(h, a)]
    # The surviving feature-hole *positions* (concentric bores excluded on rotational
    # parts) — the IR gates callouts/furniture/sections on membership in this set, so
    # no recogniser Hole object crosses into the renderers (Amendment 6, #263/#207).
    feature_keys = {HoleRef.of(h.location) for h in feature_holes}
    # Decide the section trigger + cut-plane row now (pure function of _model/
    # feature_keys, no placement dependency) and reserve its cutting-plane arrows'
    # row BEFORE the plan-view hole callouts place (ADR 0009 P5 strand 3) — the
    # section itself still renders last (its own room check clears everything else
    # placed), this only gives the (now strip_obstacles-aware) callout carve a
    # real obstacle to see and, where a cheap relocation exists, avoid — instead
    # of an invisible one it could never even detect. When avoiding would cost a
    # large relocation, policy B keeps the callout at its natural position and
    # accepts the crossing rather than pay that cost or drop it (holes.py); the
    # `bracket` fixture's known hc_plan0/section_arrow_right overlap
    # (tests/test_layout_cleanliness.py) is exactly this accepted case.
    _section = plan_sections(_model, feature_keys)
    _reserve_section_row(dwg, a, _section)
    if feature_holes:
        _annotate_holes(dwg, a, view_of_axis, _groups, feature_keys)
    # Hole location dims — IR renderer (planner picks the refs + datum, #238); placed
    # through the existing above-view strips. Replaces the engine's _add_location_dims.
    render_locations(dwg, _model, a)

    if a.cross_diams and a.is_rotational and not feature_holes:
        _log.info(
            "Cross-hole ø%s detected but not annotated (requires section view)",
            _fmt(a.cross_diams[0]),
        )

    # Front-view right ladder: prismatic step heights + overall height — IR renderer,
    # through fv_zones.right preserving the leapfrog cursor (#237). Replaces the inline
    # dim_step_* + dim_height; the turned step-length chain (render_step_lengths) handles
    # turned parts, and a Z-turned overall height is suppressed there (ISO 129).
    render_height_ladder(dwg, _model, a)

    # Side-drilled holes' in-plane (side-below) locations FIRST, so the overall
    # envelope depth lands OUTSIDE them — ISO stacks the overall dim outermost,
    # feature/location dims nearer the view (matches the plan view, where location
    # dims precede the envelope). To keep the #133 guarantee that the MANDATORY
    # envelope dim is never starved, reserve its tier in the side-below strip first
    # (shrink the strip by one depth slot for the location pass, then restore) — so
    # the best-effort locations fill inner tiers and the envelope always gets the
    # outermost. The height (right-strip) locations stay in the "along" phase.
    if feature_holes:
        # Only reserve when render_envelope will actually place the depth dim — the
        # planner suppresses it for a square footprint / X-turned part (#250); reserving
        # a tier it never claims would needlessly shrink the strip and drop a location
        # dim that would otherwise fit (#316 review). Uses render_envelope's own
        # place-predicate (env_dim_placed) so the two can never drift.
        _env_g = envelope_group(_groups)
        _reserve = _env_g is not None and env_dim_placed(_env_pd(_env_g, "depth"))
        _below = a.sv_zones.below
        _saved_limit = _below.outer_limit
        if _reserve:
            _below.outer_limit -= _below.direction * (_SLOT_DIM_DEPTH + _below.spacing)
        _locate_off_axis_holes(dwg, a, holes_in=feature_holes, which="across")
        _below.outer_limit = _saved_limit

    # Overall width (plan, below) + depth (side, below) envelope dims — IR renderer,
    # placed through the same below-strip zone allocators the engine used (zone-aware
    # render stage, ADR 0008). Suppression (square footprint / X-turned width) is now
    # the planner's decision (#250); the renderer just skips suppressed dims.
    render_envelope(dwg, _groups, a)

    # The section view goes last: its room check clears every annotation already
    # placed right of the side view (callout labels, height/step dim ladders). The
    # *trigger* + cut-plane row were already decided (`_section`, above — reused so
    # the pure planner call isn't repeated); the renderer just draws the planned
    # section. Concentric bores on a turned part are excluded via feature_keys (the
    # ldr_z leaders cover them).
    section = _section
    if section is not None:
        _add_section_view(dwg, a, section)

    # Prismatic step-height detail: queue it (only when build_drawing(detail_view=True))
    # — resolved with every other detail request below (#307).
    if detail_view:
        _request_prismatic_detail(dwg, a)

    # Turned-part dimensions via the IR (ADR 0008 convergence). The model is built
    # once and fed to both renderers (#229 — no per-pass rebuild):
    #  - diameters: ø leaders, row below (X) / column left (Z), one path by frame
    #    axis. Replaces _annotate_turned_diameters.
    #  - step lengths: the chain that locates every shoulder, X and Z from one path
    #    (#223). A crowded X-turned head queues an enlarged detail request (#304/#307)
    #    instead of cramming; the envelope dim along the turning axis was suppressed
    #    so the chain does not double-dimension the length.
    render_diameters(dwg, _groups)
    if a.prof is not None:
        render_step_lengths(dwg, _groups)

    # Side-drilled (X/Y-axis) hole HEIGHT locations — last, so the envelope and
    # turned-diameter dims claim their (contended right) strip space first and are
    # never evicted (#133). The in-plane locations were placed before the envelope.
    if feature_holes:
        _locate_off_axis_holes(dwg, a, holes_in=feature_holes, which="along")

    # Non-cylindrical machined features: slots / reduced across-flats sections
    # (#135) — IR renderer, placed through the zone strips (shared infra). Runs
    # after every hole/diameter pass so it claims strip space last.
    render_slots(dwg, _model, a)

    # Resolve every queued enlarged-detail request (#307) — prismatic step bands and
    # crowded turned heads alike — through the one generic detailer, now that all
    # views and main-view annotations are placed (so the detail avoids them).
    _resolve_details(dwg, a)

    if a.pmi_mode == "annotate":
        render_pmi(dwg, _model, a)

    _add_title_block(dwg, a)

    # Escalate to a hole table when the plan view is too dense to dimension
    # every hole — runs last so the table avoids every placed annotation
    # including the title block (#93).
    _maybe_tabulate_holes(dwg, a)


def _maybe_tabulate_holes(dwg, a: Analysis):
    """Escalate to a per-instance hole table + balloons when the plan view is too
    dense to dimension every hole individually (#93); a dropped ISO pattern
    callout gets one grouped balloon of its own (#351 PR-3, ADR 0009 Amdt 1
    decision 1 — the #348 fix).

    When callouts or location references had to be dropped, the individual
    plan-view callouts and X/Y location dims are removed and replaced by a
    complete **hole chart** — one row per hole (``TAG | ⌀ | X | Y``, X/Y from the
    min-corner datum) and a uniquely-tagged balloon at each hole. The table
    carries ``covers_diameters`` so the coverage lint still counts the holes.
    Sparse parts drop nothing, so this is a no-op for them — unchanged.

    If the table itself will not fit, nothing is removed and the drop lint is
    kept — the sheet is never left with neither.

    Independent of that density gate: a recognised pattern (bolt circle / linear
    array / grid) whose own grouped ``n×`` callout could not be placed inline
    gets **one balloon tagging the whole pattern**, not one balloon per member —
    a dropped pattern is a real coverage gap on any part, not just a dense one.
    Both kinds of balloon share one strip-solved band per side (one
    ``_add_balloons`` call) so they never overlap each other.
    """
    # Trigger on the first-class Escalation objects the hole placers collect (ADR 0009
    # Amdt 1, #351 PR-2), not by grepping the `*_dropped` lint strings. Byte-identical:
    # a "callout"/"location" Escalation is emitted 1:1 with each callout_dropped/
    # location_ref_dropped code. `getattr` default guards the auto_dims=False path (which
    # skips this whole pass anyway). The lint codes stay as the coverage surface.
    escalations = getattr(dwg, "_escalations", ())
    if not any(e.kind in ("callout", "location") for e in escalations):
        return

    # A "callout" escalation's feature is the dropped group's PatternFeature only when
    # it is a fully-surviving recognised pattern (_annotate_holes's `pat`, holes.py) —
    # scoped to the plan view, the only one `_add_balloons`'s halo covers.
    pattern_feats = [
        e.feature
        for e in escalations
        if e.kind == "callout" and e.view == "plan" and isinstance(e.feature, PatternFeature)
    ]

    # Tabulate only the genuinely UNpatterned plan-view holes: holes in a
    # recognised pattern are documented by their grouped ``n× ⌀`` callout +
    # pattern dimension, so they must not become table rows or per-hole balloons
    # (#92).  Excluding them is also what keeps a densely-but-regularly drilled
    # part (e.g. NIST CTC-02) off the 61-row escalation (#111).
    holes = [
        h
        for h in a.holes
        if _axis_letter(h) == "z" and not dwg._is_hole_patterned(HoleRef.of(h.location))
    ]
    # A chart is warranted only for a *genuinely* dense plan view — a part that
    # merely dropped one too-close location ref keeps its individual dims (the
    # legibility gate already handled it). #93.
    tabulate_scattered = len(holes) >= _TABULATE_MIN_HOLES
    if not tabulate_scattered and not pattern_feats:
        return

    dx, dy = a.bb.min.X, a.bb.min.Y
    n_scattered = len(holes) if tabulate_scattered else 0
    tags = _tag_sequence(n_scattered + len(pattern_feats))
    scattered_tags, pattern_tags = tags[:n_scattered], tags[n_scattered:]
    # One balloon per pattern, tagged with its member count so the ring reads
    # "6×A" rather than one glyph per member (#348) — no table row needed, the
    # count + diameter travel with the balloon itself.
    pattern_specs = [
        (
            f"{feat.count}×{tag}",
            0,
            # Anchor on an actual member hole, not `feat.frame.origin` — for a
            # bolt circle / grid that's the pattern's geometric centre, which
            # isn't a hole, so the leader would point at solid material instead
            # of the pattern it documents.
            SimpleNamespace(location=feat.members[0], diameter=feat.member.diameter),
        )
        for tag, feat in zip(pattern_tags, pattern_feats, strict=True)
    ]

    scattered_specs: list = []
    table_placed = False
    if tabulate_scattered:
        header = ("TAG", "⌀", "X", "Y")
        data = [
            (tag, f"ø{_fmt(h.diameter)}", _fmt(h.location[0] - dx), _fmt(h.location[1] - dy))
            for tag, h in zip(scattered_tags, holes, strict=True)
        ]
        # Remove the callouts and location dims the table replaces FIRST: it frees
        # their space for the table and shrinks the obstacle set fit_box scans (the
        # dense parts have dozens), which is the dominant cost on heavy sheets (#93).
        # Structured coverage state (registered at placement time, #351 PR-4c), not
        # an annotation-name-prefix grep — the last stringly-typed inference this
        # resolver relied on (ADR 0009 Amdt 1).
        replaced = {n: o for n, o in list(dwg.iter_annotations()) if dwg._is_scattered_hole_doc(n)}
        replaced_view = {n: dwg.view_of(n) for n in replaced}
        for n in replaced:
            dwg.remove(n)

        # Widen the chart into more column-blocks until it fits the page.
        table = None
        for ncols in (1, 2, 3, 4):
            table = dwg.add_table(
                _wrap_rows(header, data, ncols), name="hole_table_plan", block_cols=len(header)
            )
            if table is not None:
                break
        dwg._drop_build_issues("table_dropped")
        if table is None:
            # Even wrapped it will not fit — restore the callouts/dims and keep the
            # drop lint, so the sheet is never left with neither. The pattern
            # balloons below are unaffected — nothing of theirs was removed.
            for n, obj in replaced.items():
                dwg.add(obj, n, view=replaced_view.get(n))
            dwg._record_build_issue("warning", "table_dropped", "hole table did not fit the sheet")
        else:
            # One entry per hole (with repeats) so the coverage *count* check sees
            # that the table documents every instance, not just each distinct
            # diameter.
            table.covers_diameters = tuple(h.diameter for h in holes)
            scattered_specs = [(tag, 0, h) for tag, h in zip(scattered_tags, holes, strict=True)]
            table_placed = True
            # The table's X/Y columns document every scattered hole's location, so the
            # location refs ARE resolved here. `callout_dropped`, however, is cleared
            # below — only after the balloons are placed and per feature — because a
            # pattern balloon sharing this resolver's combined band can still drop
            # (review follow-up: this used to clear callout_dropped wholesale here,
            # masking a dropped pattern balloon).
            dwg._drop_build_issues("location_ref_dropped")

    balloon_specs = scattered_specs + pattern_specs
    placed_names: set = set()
    if balloon_specs:
        # One call: the strip solver must see every band member together, or two
        # independent _add_balloons calls could stack a pattern balloon on a
        # per-hole one in the same band.
        dwg._add_balloons("plan", balloon_specs)
        placed_names = {n for n, _ in dwg.iter_annotations()}

    # Clear `callout_dropped` only when EVERY dropped plan-view callout is now
    # documented — one unified check across both remedies (the scattered table and the
    # pattern balloons), so neither hides the other. A plan-view callout escalation is
    # resolved iff:
    #   - it is a pattern whose balloon actually LANDED on the sheet (a crowded band
    #     can drop the tail — _place_band's strip-solver prefix fallback — leaving the
    #     pattern undocumented), or
    #   - it is a scattered hole and the table was placed (its X/Y row documents it).
    # A drop this resolver does not cover — a table that didn't fit, a balloon that
    # didn't land, or any callout dropped in a non-plan view — leaves the lint standing.
    resolved_feats = {
        feat
        for (full_tag, _, _), feat in zip(pattern_specs, pattern_feats, strict=True)
        if f"balloon_plan_{full_tag}_0" in placed_names
    }

    def _resolved(e) -> bool:
        if e.view != "plan":
            return False
        if isinstance(e.feature, PatternFeature):
            return e.feature in resolved_feats
        return table_placed  # a scattered plan-hole callout is documented by the table

    unresolved = [e for e in escalations if e.kind == "callout" and not _resolved(e)]
    if not unresolved:
        dwg._drop_build_issues("callout_dropped")
