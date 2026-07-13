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
    _TABULATE_MIN_HOLES,
    Analysis,
    HoleRef,
    _add_title_block,
    _fmt,
    _iso_bbox,
    _log,
    _tag_sequence,
)
from draftwright.analysis import _sizing_bores
from draftwright.annotations._common import drain_corridors
from draftwright.annotations.from_model import (
    render_centermarks,
    render_chamfers,
    render_diameters,
    render_envelope,
    render_fillets,
    render_gdt,
    render_height_ladder,
    render_locations,
    render_plates,
    render_pmi,
    render_rotational,
    render_slots,
    render_step_lengths,
    render_step_positions,
)
from draftwright.annotations.holes import (
    _annotate_holes,
    _locate_off_axis_holes,
    build_view_of_axis,
)
from draftwright.annotations.sections import (
    _add_section_view,
    _request_prismatic_detail,
    _reserve_section_row,
    _resolve_details,
    feature_hole_keys,
)
from draftwright.model import (
    Frame,
    HoleFeature,
    PatternFeature,
    RotationalFeature,
    build_part_model,
    plan_dimensions,
    plan_sections,
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


def _concentric_bore_diams(a: Analysis) -> list:
    """Distinct bore diameters on the rotation axis, in z_diams order (#10).

    Delegates to :func:`analysis._sizing_bores` so the render-time model built here
    uses the SAME bore set the pre-scale sizing model used (#584 WP1 A). ``a.z_diams``
    carries every Z cylinder diameter (incl. off-axis ones); the set is restricted to
    diameters with an *internal* centreline Z cylinder, OD excluded, in z_diams order.
    """
    z_cyls, _ = a.cyls
    return _sizing_bores(z_cyls, a.z_diams, a.od_diam, a.cx, a.cy)


def build_model(a: Analysis):
    """Build the ADR-0008 :class:`PartModel` from an analysis — the detected feature
    inventory (a pure function of *a*).

    Extracted from :func:`_auto_annotate` so the pipeline can build the model
    **regardless of** ``auto_dims`` (#398): the read surface :meth:`Drawing.model` and
    feature-referenced edits must work even in manual mode, where the annotation pass
    never runs. Concentric bore leaders are a Z-axis construction (bores on the vertical
    rotation axis); a horizontal (X/Y) round body gets OD + centrelines only (#222), so
    its bore set is empty.
    """
    _bores = tuple(_concentric_bore_diams(a)) if a.is_rotational and a.od_axis == "z" else ()
    return build_part_model(
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


def build_rotational_feature(a: Analysis):
    """The rotational furniture feature (OD + centrelines + concentric bores) for *a*,
    or ``None`` when the part isn't rotational. Mirrors the ``rotational`` branch of
    :func:`build_model` / ``detect.build_part_model`` (detect.py) so the declared-model
    path can synthesise the same furniture detection produces: a declared turned shaft
    otherwise renders with no centrelines and its OD as a leader, not a dimension (#472).
    Concentric bores are a Z-axis construction (as in :func:`build_model`)."""
    if not a.is_rotational or a.od_diam is None:
        return None
    bores = tuple(_concentric_bore_diams(a)) if a.od_axis == "z" else ()
    c = a.part.bounding_box().center()
    return RotationalFeature(frame=Frame((c.X, c.Y, c.Z), a.od_axis), od=a.od_diam, bores=bores)


def _declared_feature_keys(groups, a: Analysis) -> set:
    """The :class:`HoleRef` position keys of every DECLARED hole/pattern member (ADR 0011
    #448), so a caller-declared hole/pattern renders at its declared position even where
    detection missed it. Mirrors the member source (``feat.members or g.anchor``) and the
    rotational concentric-bore exclusion of the ``_annotate_holes`` filter so the callout
    gate matches exactly — an on-axis bore stays excluded (dimensioned by the ldr_z
    centreline)."""
    keys: set = set()
    for g in groups:
        feat = g.feature
        if not isinstance(feat, HoleFeature | PatternFeature):
            continue
        for m in feat.members or (g.anchor,):
            if (
                a.is_rotational
                and feat.frame.axis == "z"
                and math.hypot(m[0] - a.cx, m[1] - a.cy) <= _CONCENTRIC_TOL_MM
            ):
                continue
            keys.add(HoleRef.of(m))
    return keys


def _auto_annotate(dwg, a: Analysis, *, detail_view: bool = False):
    """Add the standard automatic dimensions, centrelines, and title block."""
    # Idempotent: clear build-time lint state so a second annotation pass does
    # not accumulate duplicate drop records.
    dwg._reset_build_issues()
    dwg._reset_dropped_callout_diams()
    dwg._detail_requests = []  # renderers queue enlarged-detail requests here (#307)
    dwg._escalations = []  # placers collect Escalation objects here (ADR 0009 Amdt 1, #351)
    dwg._corridor_batch = {}  # passes register CorridorCandidates here; one drain solves each strip (#345/#346)

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
    view_of_axis = build_view_of_axis(a)

    # The part model — the IR-migrated passes (centre marks, turned diameters/lengths)
    # render from it (ADR 0008 convergence / #229). Built once by the pipeline
    # (:func:`build_model`, attached to the drawing before this pass) so the read surface
    # (dwg.model()) and feature edits work even in manual mode (#398); fall back to
    # building it here for any direct caller that skipped _assemble.
    _model = dwg._part_model if dwg._part_model is not None else build_model(a)
    dwg._part_model = _model
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
    # The surviving feature holes' *positions* (concentric bores excluded on rotational
    # parts) — the IR gates callouts/furniture/sections on membership in this set, so no
    # recogniser Hole object crosses into the renderers (Amendment 6, #263/#207).
    # feature_hole_keys reads the IR (`_model.features`) — the single source shared with
    # the section() add verb (#420 / #584 WP1) and the off-axis location pass, which now
    # derives its own side-drilled holes from the IR too (subsystem B3).
    feature_keys = feature_hole_keys(_model, a)
    # ADR 0011 #448: when the caller DECLARED the model (model=), a hole/pattern renders at
    # its declared position even where detection missed it — source the callout membership
    # set from the declared IR groups too, not only a.holes. A no-op for the detection-only
    # path (gated on the declared flag; and on a fully-detected declared part the declared
    # keys already coincide with the detected ones).
    declared_keys: set = set()
    if getattr(dwg, "_model_declared", False):
        declared_keys = _declared_feature_keys(_groups, a)
        feature_keys = feature_keys | declared_keys
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
    # Any hole/pattern member (declared holes render even where detection missed them).
    if feature_keys:
        _annotate_holes(dwg, a, view_of_axis, _groups, feature_keys)
    # Hole location dims — IR renderer (planner picks the refs + datum, #238); placed
    # through the existing above-view strips. Replaces the engine's _add_location_dims.
    render_locations(dwg, _model, a)

    if a.cross_diams and a.is_rotational and not feature_keys:
        _log.info(
            "Cross-hole ø%s detected but not annotated (requires section view)",
            _fmt(a.cross_diams[0]),
        )

    # Front-view right ladder: prismatic step heights + overall height — IR renderer,
    # through fv_zones.right preserving the leapfrog cursor (#237). Replaces the inline
    # dim_step_* + dim_height; the turned step-length chain (render_step_lengths) handles
    # turned parts, and a Z-turned overall height is suppressed there (ISO 129).
    render_height_ladder(dwg, _model, a)

    # Plate/wall thicknesses on a multi-plate prismatic (#559): the thin extent of each
    # recognised slab, placed in the view where its thin axis is visible. A single flat
    # plate has none (its thickness IS the envelope height).
    render_plates(dwg, _model, a)

    # Prismatic step POSITIONS (#555): where each shoulder sits along its axis, so a
    # stepped block is fully constrained (the heights alone leave the shoulder implicit).
    render_step_positions(dwg, _model, a)

    # Chamfer callouts (#560): C{leg} / {leg}×{angle}° via a leader off each chamfer face.
    render_chamfers(dwg, _model, a)
    render_fillets(dwg, _model, a)

    # Side-drilled holes' in-plane (side-below) locations share the below corridor with
    # the overall envelope depth. They now queue into the same batch; the envelope's
    # later subchain + mandatory priority keeps ISO outermost stacking and prevents
    # best-effort locations from starving the principal depth dimension (#477).
    if feature_keys:
        _locate_off_axis_holes(dwg, a, which="across")

    # Overall width (plan, below) + depth (side, below) envelope dims — IR renderer,
    # queued into the shared corridor instead of claiming a post-hoc carve tier.
    # Suppression (square footprint / X-turned width) is the planner's decision (#250).
    render_envelope(dwg, _groups, a)

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

    # Side-drilled (X/Y-axis) hole HEIGHT locations — queued after the mandatory envelope
    # candidates so below/right corridors solve them together with GD&T/PMI at the drain.
    # The front-right prismatic height ladder remains immediate because its later witness
    # bases depend on earlier placed tiers (#477).
    if feature_keys:
        _locate_off_axis_holes(dwg, a, which="along")

    # Non-cylindrical machined features: slots / reduced across-flats sections
    # (#135) — IR renderer, placed through the zone strips (shared infra). Runs
    # after every hole/diameter pass so it claims strip space last.
    render_slots(dwg, _model, a)

    # Declared GD&T frames / datum symbols / surface finishes (ADR 0011 §4, #61) and
    # authored STEP PMI dims (#393) register into the same strips as first-class
    # candidates BEFORE the drain, so the one solve orders and spaces them crossing-free
    # with locations/slots rather than consuming leftovers as first-fit placements.
    render_gdt(dwg, _model, a)
    if a.pmi_mode == "annotate" or (
        getattr(dwg, "_model_declared", False)
        and any(f.kind in ("authored_dimension", "pmi") for f in _model.features)
    ):
        render_pmi(dwg, _model, a)

    # Now every corridor feeder pass has registered; solve each shared strip once
    # (ADR 0009 end state, #345/#346/#393) — dedup coincident spans, order the ladder —
    # BEFORE the section/detail views so they see the placed ladder as an obstacle.
    drain_corridors(dwg)

    # The section view renders after the corridor-drained furniture exists, so
    # its full strip_obstacles room check can see side callouts, envelope dims,
    # slots, GD&T/PMI, and drained ladder outputs as one occupancy set. Details
    # still render after it and avoid the section view.
    section = _section
    if section is not None:
        _add_section_view(dwg, a, section)

    # Resolve every queued enlarged-detail request (#307) — prismatic step bands and
    # crowded turned heads alike — through the one generic detailer, now that all
    # views and main-view annotations are placed (so the detail avoids them).
    _resolve_details(dwg, a)

    _add_title_block(dwg, a)

    # Escalate to a hole table when the plan view is too dense to dimension
    # every hole — runs last so the table avoids every placed annotation
    # including the title block (#93).
    _maybe_tabulate_holes(dwg, a)
    # Don't leave the consumed escalations on the drawing: a later deferred edit
    # (`build_drawing(part)` then `with dwg.deferred(): …`) would otherwise inherit
    # them and re-fire finalize's leg D against stale drops, relocating the table
    # (#440). Nothing reads _escalations after the table pass.
    dwg._escalations = []


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
    # part (e.g. NIST CTC-02) off the 61-row escalation (#111). Sourced from the IR —
    # a loose z-axis HoleFeature is by construction not a pattern member, so no
    # HoleRecord crosses here (ADR 0008 Am6; #584 WP1 B4).
    _model = dwg._part_model
    holes = [
        SimpleNamespace(location=pos, diameter=f.diameter)
        for f in (_model.features if _model is not None else ())
        if f.kind == "hole" and f.frame.axis == "z"
        for pos in (f.members or (f.frame.origin,))
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
            # of the pattern it documents.  Fall back to the centre only if a
            # declared pattern left `members` empty (detected ones never do).
            SimpleNamespace(
                location=(feat.members or (feat.frame.origin,))[0],
                diameter=feat.member.diameter,
            ),
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
