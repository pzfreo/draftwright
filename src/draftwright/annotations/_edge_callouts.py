"""Compiler-approved chamfer, fillet, and Blend leader job producers.

This owner builds labels and physical candidate inventories. Public passes in
``from_model`` submit them to the existing shared late leader assignment.
"""

from __future__ import annotations

import math

from draftwright._core import _tol_suffix
from draftwright._geometry import (
    _blend_profile_arcs,
    _fmt_chamfer,
    _straight_blend_faces,
    _turned_profile_site,
    material_span,
)
from draftwright.annotation_layout_profile import layout_flag
from draftwright.annotations._common import _ray_exit_dist
from draftwright.annotations.leaders import FeatureLeaderCandidate, view_material
from draftwright.model.compiled import FeatureInstanceIndex


def _chamfer_label(leg_text, leg, ch) -> str:
    """The chamfer callout string: ``C{leg}`` for an equal-leg 45° chamfer, else
    ``{leg} × {angle}°`` (#560).

    Takes the leg TWICE, on purpose, because printing it and testing its form are different
    jobs: *leg_text* is the compiler's own `value_text` and is what appears on the sheet,
    while *leg* is the number the equal-leg comparison needs. The feature supplies only the
    geometric form discriminators (``leg2``/``angle``), and a ``ChamferFeature`` stays pure
    data (ADR 3 (was 0013 §7))."""
    # `ch.angle` is a form discriminator, not a planned parameter.
    # `ChamferFeature.parameters()` emits only the leg, so the angle has no approved
    # text to consume. That is the IR gap `_FACTS` records, and it is why this
    # line stays in the provenance budget.
    return _fmt_chamfer(leg_text, leg, ch.leg2, ch.angle)


def _corner_candidates(
    dwg, view, vb, members, reach, *, provenances=None, cylinders=None, sites=None
):
    """Lead candidates for a corner-sitting feature (chamfer/fillet/flat): from each member's
    projected origin (or supplied semantic surface site), a diagonal from the view centre out
    through the corner, *reach* beyond the tip — a corner clears the silhouette this way. Yields
    ``(tip, elbow, member)`` in the given member order, which is the stable tie-break after #740's
    cardinality/length solve; a single-feature callout passes a one-element *members*."""
    x0, y0, x1, y1 = vb
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    members = list(members)
    owners = list(provenances if provenances is not None else members)
    candidate_sites = list(sites if sites is not None else (m.frame.origin for m in members))
    for m, owner, site in zip(members, owners, candidate_sites, strict=True):
        if cylinders is not None and getattr(m, "turned", False):
            site = _turned_profile_site(site, m.axis, view, cylinders)
        tip = dwg.at(view, *site)
        dx, dy = tip[0] - cx, tip[1] - cy
        d = math.hypot(dx, dy) or 1.0
        elbow = (tip[0] + dx / d * reach, tip[1] + dy / d * reach, 0)
        yield (tip, elbow, owner)


def _corner_escape_candidates(
    dwg, view, vb, members, reach, *, provenances=None, cylinders=None, sites=None
):
    """Corner leaders plus silhouette-outward horizontal/vertical escapes.

    The diagonal remains the stable first choice.  The two axis-aligned rays
    leave the same corner away from the view centre, so they add boundary/lane
    alternatives without introducing #798's through-silhouette routing problem.
    """

    members = list(members)
    owners = list(provenances if provenances is not None else members)
    candidate_sites = list(sites if sites is not None else (m.frame.origin for m in members))
    yield from _corner_candidates(
        dwg,
        view,
        vb,
        members,
        reach,
        provenances=owners,
        cylinders=cylinders,
        sites=candidate_sites,
    )
    x0, y0, x1, y1 = vb
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for member, owner, site in zip(members, owners, candidate_sites, strict=True):
        if cylinders is not None and getattr(member, "turned", False):
            site = _turned_profile_site(site, member.axis, view, cylinders)
        tip = dwg.at(view, *site)
        # Limit each site to two axis escapes. More routes consume the
        # per-view candidate budget and can prevent the joint solve from running.
        directions = (
            (1.0 if tip[0] >= cx else -1.0, 0.0),
            (0.0, 1.0 if tip[1] >= cy else -1.0),
        )
        for ux, uy in directions:
            exit_distance = _ray_exit_dist(tip[0], tip[1], ux, uy, vb)
            yield (
                tip,
                (
                    tip[0] + ux * (exit_distance + reach),
                    tip[1] + uy * (exit_distance + reach),
                    0,
                ),
                owner,
            )


def _surface_normal_candidates(dwg, view, members, sizes, reach, *, kind, provenances):
    """Prove a visible bevel/arc tangent before offering straight normal leaders.

    The physical attachment remains the compiled feature's projected site. A
    matching projected edge supplies its local normal; the filled material field
    chooses the outward sign. If that evidence is absent, the caller retains the
    established corner candidates instead of guessing a surface direction.
    """
    placed = dwg.views.get(view)
    if not placed or placed[0] is None:
        return []
    field = view_material(dwg, view)
    edges = tuple(placed[0].edges())
    result = []
    for member, size, owner in zip(members, sizes, provenances, strict=True):
        if getattr(member, "turned", False):
            continue
        tip = dwg.at(view, *member.frame.origin)
        matches = []
        for edge in edges:
            if kind == "fillet" and edge.geom_type.name == "CIRCLE":
                try:
                    centre = edge.arc_center
                    radius = float(edge.radius)
                except Exception:  # noqa: BLE001 — an unmeasurable edge is not evidence
                    continue
                expected = float(size) * float(dwg.scale)
                residual = abs(radius - expected) + abs(
                    math.hypot(tip[0] - centre.X, tip[1] - centre.Y) - radius
                )
                if residual <= 0.1 and edge.distance_to((tip[0], tip[1], 0)) <= 0.05:
                    matches.append((residual, (tip[0] - centre.X, tip[1] - centre.Y)))
            elif kind == "chamfer" and edge.geom_type.name == "LINE":
                vertices = edge.vertices()
                if len(vertices) != 2:
                    continue
                first, second = vertices
                dx, dy = second.X - first.X, second.Y - first.Y
                length2 = dx * dx + dy * dy
                if length2 <= 1e-9:
                    continue
                station = ((tip[0] - first.X) * dx + (tip[1] - first.Y) * dy) / length2
                residual = math.hypot(
                    tip[0] - first.X - station * dx,
                    tip[1] - first.Y - station * dy,
                )
                expected = math.hypot(size, member.leg2) * float(dwg.scale)
                if (
                    0.1 <= station <= 0.9
                    and residual <= 0.05
                    and abs(math.sqrt(length2) - expected) <= 0.1
                ):
                    matches.append((residual, (-dy, dx)))
        if not matches:
            continue
        _, direction = min(matches, key=lambda item: item[0])
        length = math.hypot(*direction)
        if length <= 1e-9:
            continue
        nx, ny = direction[0] / length, direction[1] / length
        if field:
            origin = (tip[0], tip[1])
            positive = material_span(origin, (tip[0] + nx * reach, tip[1] + ny * reach), field)
            negative = material_span(origin, (tip[0] - nx * reach, tip[1] - ny * reach), field)
            if negative < positive:
                nx, ny = -nx, -ny
        for distance in (reach, reach * 1.5, reach * 2.0):
            result.append((tip, (tip[0] + nx * distance, tip[1] + ny * distance, 0), owner))
    return result


_BLEND_POINT_TOL = 2e-3
_BLEND_DIRECTION_TOL = 2e-6


def _blend_faces_by_ref(analysis):
    """Index exact run-local Blend faces by the compiler's opaque provenance handle.

    ``RecognitionOwnership`` is the only lawful accepted-occurrence -> IR join. Add its exact
    owner to ``FeatureInstanceIndex`` and query that index with the opaque ``FeatureRef`` already
    carried by each approved dimension. Structural ``FeatureRef`` equality deliberately merges
    equal-valued marks, so it is not physical occurrence authority. Dimensional renderers still
    never resolve that handle back to model content.
    """
    evidence = analysis.recognition_evidence
    ownership = analysis.recognition_ownership
    if evidence is None or ownership is None or ownership.evidence is not evidence:
        return None
    indexed = FeatureInstanceIndex()
    for binding in ownership.bindings:
        if evidence.family(binding.occurrence) != "blends":
            continue
        faces = tuple(evidence.face(ref) for ref in evidence.defining_faces(binding.occurrence))
        for feature in binding.features:
            indexed.extend(feature, faces)
    return indexed


def _blend_surface_sites(
    blend,
    cylinders,
    rolling_radius: float,
    *,
    defining_faces=None,
) -> tuple[tuple[float, float, float], ...]:
    """Return natural sites on a straight profile arc or circular Blend surface.

    A circular path stores the rolling-ball centre trajectory, not a surface point. Its proved
    supports include one coaxial finite cylinder whose radius differs from the path radius by
    the rolling radius. Reuse the analysis-owned cylinder inventory to find candidate tangencies,
    then use the accepted occurrence's exact defining face to select the physical one. A declared
    build has no occurrence authority: one unambiguous support remains usable, but competing
    support radii refuse placement rather than borrowing another body's cylinder. A declaration
    without a matching support retains the outer analytic fallback; completeness independently
    exposes any source mismatch. These are part-space facts, not page coordinates: the shared
    solve still owns placement.
    """
    if blend.path_kind != "circular":
        faces = _straight_blend_faces(
            blend, cylinders, rolling_radius, defining_faces=defining_faces
        )
        arcs = _blend_profile_arcs(faces, rolling_radius)
        if not arcs:
            return ()
        arc = min(arcs, key=lambda edge: (-edge.length, tuple(edge.position_at(0.5))))
        return tuple(tuple(arc.position_at(fraction)) for fraction in (0.5, 0.25, 0.75))
    origin = blend.frame.origin
    normal: tuple[float, float, float] = blend.axis_direction
    seed_index = min(range(3), key=lambda index: (abs(normal[index]), index))
    seed = tuple(1.0 if index == seed_index else 0.0 for index in range(3))
    radial = (
        normal[1] * seed[2] - normal[2] * seed[1],
        normal[2] * seed[0] - normal[0] * seed[2],
        normal[0] * seed[1] - normal[1] * seed[0],
    )
    length = math.hypot(*radial)
    unit = tuple(component / length for component in radial)
    path_radius: float | None = blend.path_radius
    assert path_radius is not None  # validated by BlendFeature
    outer_radius = path_radius + rolling_radius
    inner_radius = path_radius - rolling_radius
    matches = []
    for family in cylinders:
        for cylinder in family:
            direction = cylinder["dir_xyz"]
            alignment = abs(sum(normal[index] * direction[index] for index in range(3)))
            # Blend points/radii are released at 0.001 and directions at 0.000001. These
            # bounds cover their worst coordinate/vector round-trip without admitting a
            # materially different support.
            if abs(alignment - 1.0) > _BLEND_DIRECTION_TOL:
                continue
            axis_point = cylinder["axis_xyz"]
            delta = tuple(origin[index] - axis_point[index] for index in range(3))
            cross = (
                delta[1] * direction[2] - delta[2] * direction[1],
                delta[2] * direction[0] - delta[0] * direction[2],
                delta[0] * direction[1] - delta[1] * direction[0],
            )
            if math.hypot(*cross) > _BLEND_POINT_TOL:
                continue
            radius = cylinder["diameter"] / 2.0
            radius_error = abs(abs(radius - path_radius) - rolling_radius)
            along = sum(origin[index] * direction[index] for index in range(3))
            end_error = min(abs(along - cylinder["s_lo"]), abs(along - cylinder["s_hi"]))
            if radius_error <= _BLEND_POINT_TOL and end_error <= _BLEND_POINT_TOL:
                matches.append((radius_error, end_error, radius))

    def site(radius: float) -> tuple[float, float, float]:
        return (
            origin[0] + radius * unit[0],
            origin[1] + radius * unit[1],
            origin[2] + radius * unit[2],
        )

    if defining_faces is not None:
        # Automatic recognition has an exact face authority. If its ownership or surface is
        # absent, fail closed; scalar cylinders must not silently replace missing evidence.
        if not defining_faces:
            return ()
        radii = sorted(
            {
                *(candidate[2] for candidate in matches),
                *(radius for radius in (inner_radius, outer_radius) if radius >= 0.0),
            }
        )
        candidates = [
            (min(float(face.distance_to(site(radius))) for face in defining_faces), radius)
            for radius in radii
        ]
        distance, surface_radius = min(candidates)
        return (site(surface_radius),) if distance <= _BLEND_POINT_TOL else ()

    if matches:
        support_radii = sorted(candidate[2] for candidate in matches)
        if support_radii[-1] - support_radii[0] > _BLEND_POINT_TOL:
            return ()
        return (site(min(matches)[2]),)
    return (site(outer_radius),)


def chamfer_jobs(dwg, plan, a, *, ctx, only=None, leader_callout_reach):
    """Prepare compiler-approved chamfer leaders and their source outcomes."""
    draft = dwg.draft
    reach = leader_callout_reach(draft)
    collapse: dict = {}
    for g in plan.of_kind("chamfer"):
        pd = next(
            (d for d in g.dims if (d.role, d.kind) == ("chamfer", "length")),
            None,
        )
        if pd is None:
            continue
        ch = g.facts
        if ctx.document_member and a.pmi_mode != "annotate" and ch.source_ids:
            continue
        # Equal printed values are not enough: two chamfers with the same first leg but a
        # different second leg/angle state different manufacturing requirements.
        # A tolerance is part of the rendered requirement. Splitting by its rendered suffix
        # lets one authored member keep its precision without claiming that band for otherwise
        # identical untoleranced siblings. Values that print identically may safely share ink.
        spec = (
            round(pd.value, 3),
            round(ch.leg2, 3),
            round(ch.angle, 2),
            _tol_suffix(pd.tolerance, draft),
        )
        collapse.setdefault(spec, []).append((g, pd))

    jobs = []
    source_ids_by_name = {}
    straight_only_names = set()
    for gi, (_spec, members) in enumerate(sorted(collapse.items())):
        if only is not None:
            # Filter after enumerating the full collapse so a surviving group keeps the same
            # public annotation name during deferred or subset finalization.
            members = [gp for gp in members if gp[0].ref in only]
            if not members:
                continue
        # A grouped label may count identical chamfers on several axes. Point its leader at
        # one coherent visible set: the most populous axis/view pair, with deterministic
        # tie-breaking. Turned and prismatic treatments can share an axis but not a view.
        by_presentation: dict[tuple[str, str], list] = {}
        for gp in members:
            key = (gp[0].facts.axis, gp[0].view)
            by_presentation.setdefault(key, []).append(gp)
        (axis, _view), visible = min(
            by_presentation.items(), key=lambda item: (-len(item[1]), item[0])
        )
        ordered = sorted(visible, key=lambda gp: gp[0].facts.frame.origin)
        representative, representative_pd = ordered[0]
        ch = representative.facts
        view = representative.view
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        label = _chamfer_label(representative_pd.value_text, representative_pd.value, ch)
        if len(members) > 1:
            label = f"{len(members)}× {label}"
        name = f"m_chamfer_{axis}{gi}"
        facts = [g.facts for g, _ in ordered]
        provenances = [g.ref for g, _ in ordered]
        candidates = _corner_escape_candidates(
            dwg, view, vb, facts, reach, provenances=provenances, cylinders=a.cyls
        )
        if layout_flag("normal_feature_leaders", "DRAFTWRIGHT_EXPERIMENT_NORMAL_LEADERS"):
            normal = _surface_normal_candidates(
                dwg,
                view,
                facts,
                [pd.value for _, pd in ordered],
                reach,
                kind="chamfer",
                provenances=provenances,
            )
            if normal:
                candidates = [
                    *(
                        FeatureLeaderCandidate(tip=tip, elbow=elbow, feature=owner)
                        for tip, elbow, owner in normal
                    ),
                    *(
                        FeatureLeaderCandidate(
                            tip=tip, elbow=elbow, feature=owner, preference_penalty=50.0
                        )
                        for tip, elbow, owner in candidates
                    ),
                ]
                straight_only_names.add(name)
        source_ids_by_name[name] = tuple(
            dict.fromkeys(
                source_id
                for member, _pd in members
                for source_id in getattr(member.facts, "source_ids", ())
            )
        )
        jobs.append(
            (
                name,
                view,
                vb,
                label + _tol_suffix(representative_pd.tolerance, draft),
                candidates,
                tuple(pd.id for _, pd in members),
            )
        )
    return jobs, source_ids_by_name, straight_only_names


def _fillet_label(radius_text, count) -> str:
    """The fillet callout string: ``R{radius}``, prefixed ``{count}×`` when a set of equal
    fillets shares one callout (#561). Formatting lives in the render layer (ADR 3 (was 0013 §7))."""
    r = f"R{radius_text}"
    return f"{count}× {r}" if count > 1 else r


def radius_jobs(
    dwg,
    plan,
    a,
    *,
    ctx,
    only,
    kind: str,
    role: str,
    name_stem: str,
    noun: str,
    drop_code: str,
    collapsed_tolerance,
    leader_callout_reach,
):
    """Prepare rounded-edge jobs for the public shared-solve pass."""
    draft = dwg.draft
    reach = leader_callout_reach(draft)
    blend_faces = _blend_faces_by_ref(a) if kind == "blend" else None
    collapse: dict = {}
    for g in plan.of_kind(kind):
        pd = next(
            (d for d in g.dims if (d.role, d.kind) == (role, "radius")),
            None,
        )
        if pd is None:
            continue
        # Group by what the drawing will actually print. Authored Blend radii can carry
        # more precision than provider geometry, and two distinct display values must
        # never share one n× label while receiving separate measurement credit.
        collapse.setdefault((pd.value_text, _tol_suffix(pd.tolerance, draft)), []).append((g, pd))
    jobs = []
    straight_only_names = set()
    ordered_groups = sorted(
        collapse.items(),
        key=lambda item: (min(pd.value for _g, pd in item[1]), item[0]),
    )
    for gi, (_value_text, members) in enumerate(ordered_groups):
        if only is not None:
            # Filter a finalize subset AFTER enumerating the collapse so
            # gi stays the full-drawing group index — a survivor keeps its m_fillet name even
            # when a sibling group is dropped. The n× count reflects survivors.
            members = [gp for gp in members if gp[0].ref in only]
            if not members:
                continue
        tol = collapsed_tolerance(members, ctx=ctx, noun=noun)
        callout_label = _fillet_label(members[0][1].value_text, len(members)) + _tol_suffix(
            tol, draft
        )
        # Point the leader at one coherent visible set. Members on other edge axes/views still
        # contribute to the printed count and semantic measurements, but mixing their 3-D
        # origins into this view could point at unrelated projected corners. An ineligible Blend
        # surface is one lost alternative, not grounds to discard safe siblings before ADR 2 (was 0014)'s
        # shared solve. Prefer the most populous remaining axis/view pair; ties are deterministic.
        by_presentation: dict[tuple[str, str], list] = {}
        for group, dimension in members:
            site = None
            if kind == "blend":
                defining_faces = None if blend_faces is None else blend_faces.values_for(group.ref)
                site = _blend_surface_sites(
                    group.facts,
                    a.cyls,
                    dimension.value,
                    defining_faces=defining_faces,
                )
                if not site:
                    continue
            key = (group.facts.axis, group.view)
            by_presentation.setdefault(key, []).append((group, dimension, site))
        if not by_presentation:
            ctx.record_issue(
                "warning",
                drop_code,
                f"{noun} callout {callout_label} not placed "
                "(physical surface could not be selected without guessing)",
                measurement=tuple(pd.id for _, pd in members),
                outcome_stage="placement",
            )
            continue
        (axis, _view), visible = min(
            by_presentation.items(), key=lambda item: (-len(item[1]), item[0])
        )
        visible.sort(key=lambda item: item[0].facts.frame.origin)
        if kind == "blend":
            visible = [
                (group, dimension, point)
                for group, dimension, points in visible
                for point in points
            ]
        ordered = [(group, dimension) for group, dimension, _site in visible]
        sites = [site for _group, _dimension, site in visible] if kind == "blend" else None
        view = ordered[0][0].view
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        name = f"m_{name_stem}_{axis}{gi}"
        facts = [g.facts for g, _ in ordered]
        provenances = [g.ref for g, _ in ordered]
        candidates = _corner_escape_candidates(
            dwg,
            view,
            vb,
            facts,
            reach,
            provenances=provenances,
            cylinders=a.cyls,
            sites=sites,
        )
        if kind == "fillet" and layout_flag(
            "normal_feature_leaders", "DRAFTWRIGHT_EXPERIMENT_NORMAL_LEADERS"
        ):
            normal = _surface_normal_candidates(
                dwg,
                view,
                facts,
                [pd.value for _, pd in ordered],
                reach,
                kind="fillet",
                provenances=provenances,
            )
            if normal:
                candidates = [
                    *(
                        FeatureLeaderCandidate(tip=tip, elbow=elbow, feature=owner)
                        for tip, elbow, owner in normal
                    ),
                    *(
                        FeatureLeaderCandidate(
                            tip=tip, elbow=elbow, feature=owner, preference_penalty=50.0
                        )
                        for tip, elbow, owner in candidates
                    ),
                ]
                straight_only_names.add(name)
        jobs.append(
            (
                name,
                view,
                vb,
                callout_label,
                candidates,
                # One `n× R` callout stands for EVERY collapsed member, so it draws all of
                # their radii — the tuple storage exists for exactly this.
                tuple(pd.id for _, pd in members),
            )
        )
    return jobs, straight_only_names
