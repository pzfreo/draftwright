"""coverage — feature-coverage completeness check and its state (#138 / ADR 0005).

Part of the :mod:`draftwright.linting` package (ADR 0007):

- `lint_feature_coverage` — the completeness check that reports part diameters
  with no callout (#80), avoiding double-reporting a hole a grouped ``n× ⌀``
  callout covers (#92) and suppressing the redundant ``feature_not_dimensioned``
  for capped diameters.
- `CoverageState` — the coverage signal the passes record and the checks read
  (pattern callouts, patterned holes, dropped callout diameters). `Drawing`
  delegates to it via `dwg.coverage`; the `_pattern_callouts` /
  `_patterned_holes` / `_dropped_callout_diams` aliases that also reached it
  were deleted at their ADR 0005 §4 removal date (#720).

(`_suggest_fix` now lives in :mod:`.suggest`; `lint_drawing` in
:mod:`.structural`.) Depends only on `_core` + recognition + the rendering
``TitleBlock``; never on `make_drawing`/`annotate`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from math import atan2, pi, tau
from typing import Literal

from build123d import GeomType
from build123d_drafting.helpers import CenterMark, Dimension, TitleBlock

from draftwright._core import (
    _END_ON,
    HoleRef,
    _annotation_diameter_sources,
    _axis_letter,
    _fmt,
    _xyz,
)
from draftwright.linting.issues import LintIssue
from draftwright.linting.profiled_bore_coverage import profiled_bore_key
from draftwright.recognition import (
    RecognitionResult,
    TurnedProfile,
    analyse_cylinders,
    build_recognition_result,
    feature_diameters,
    project_step_shoulders,
    recognise_double_d_bores,
    recognise_hole_patterns,
    recognise_holes,
    recognise_pockets,
    recognise_rectangular_pads,
    recognise_turned_steps,
)
from draftwright.recognition.profiled_bores import (
    double_d_bores_from_openings,
    double_d_profile,
    principal_boundary_plane,
)

_UNSET = object()  # sentinel: distinguishes "not supplied" from a valid prof=None

# Reconciliation tolerances (#487) mirror sheet._match_object (⌀ ≤ 0.2 mm, in-plane ≤ 0.5 mm):
# a declared feature matches a recognised cylinder within these. Kept in sync by comment — linting/
# sits below sheet in the DAG, so the literals cannot be shared by import.
_RECON_DIA_TOL = 0.2
_RECON_POS_TOL = 0.5

# Declared feature kinds with a single defining cylinder to confirm against geometry, mapped to the
# cylinder polarity that confirms them: a hole is a bore (external=False); a boss / turned step is
# external material (external=True). Checking polarity stops a phantom hole being silenced by a
# coaxial boss/OD of the same ⌀ (and vice-versa) — a callout over the wrong material (#487 review).
# Envelope always exists; patterns/slots and aspects are out of scope (#499).
_RECON_EXTERNAL = {"hole": False, "boss": True, "step": True}
_RECON_KINDS = tuple(_RECON_EXTERNAL)  # derive to keep the kind list and polarity map in sync


def _dim_vertices(ann) -> list[tuple[float, float]]:
    """A ``Dimension``'s witness endpoints as ``(x, y)`` page points; ``[]`` if they
    won't evaluate. The shared, error-tolerant harvest both drawing-derived coverage
    checks use to read placed dimensions back off the drawing.

    Prefers the recorded ``_dw_spec`` endpoints (the two points the dimension was
    built from — the shoulder/feature positions) over ``ann.vertices()``: the latter
    returns *every* geometry vertex, including the text-glyph outline, whose points
    scatter across the span and can falsely satisfy a shoulder match for a wide dim
    (e.g. a head-block dim whose centred label sits over interior shoulders, #304/#307).
    The endpoints may be tuples or build123d ``Vector``s, so both are read safely."""
    spec = getattr(ann, "_dw_spec", None)
    if spec is not None:
        try:
            return [_pt(spec.p1), _pt(spec.p2)]
        except Exception:  # noqa: BLE001 — odd point types fall through to vertices()
            pass
    try:
        return [(p.X, p.Y) for p in ann.vertices()]
    except Exception:  # noqa: BLE001 — a dim whose vertices won't evaluate is skipped
        return []


def _pt(p) -> tuple[float, float]:
    """A 2-D page point from either a ``(x, y, ...)`` tuple/sequence or a build123d
    ``Vector`` (``.X``/``.Y``). Lets coverage read ``_dw_spec`` endpoints regardless of
    how the caller constructed the dimension (the public ``place_dim`` DSL may pass
    ``Vector``s, which are not subscriptable — #307 review)."""
    try:
        return (p[0], p[1])
    except (TypeError, KeyError, IndexError):
        return (p.X, p.Y)


class CoverageState:
    """What the annotation passes covered or dropped, for lint to read."""

    def __init__(self) -> None:
        # Names of bore callouts that document a recognised hole pattern (a
        # grouped ``n× ⌀`` callout), and the holes those placed callouts cover.
        # The hole-table escalation keeps these callouts and tabulates only the
        # holes no placed pattern callout documents (#92).
        self._pattern_callouts: set = set()
        self._patterned_holes: set = set()
        # Names of placed plan-view hole callouts / X/Y location dims that are NOT
        # part of a recognised pattern — the scattered-hole table (#93) replaces
        # exactly these. Registered at placement time (holes.py/from_model.py) so
        # the resolver reads structured coverage state instead of inferring
        # "table-replaceable" from annotation NAME PREFIXES (#351 PR-4c).
        self._scattered_hole_docs: set = set()
        # Diameters dropped by the per-view callout cap, so lint can suppress the
        # redundant feature_not_dimensioned for them. Reset at the top of
        # _auto_annotate so re-annotation does not accumulate.
        self._dropped_callout_diams: list = []
        # Exact profiled-bore specifications dropped with those callouts. Diameter alone
        # cannot distinguish equal-major profiles with different A/F or orientation (#1061).
        self._dropped_profiles: list[tuple] = []

    # -- pattern coverage -----------------------------------------------------

    def cover_pattern(self, callout_name, refs: Iterable[HoleRef]) -> None:
        """Record that placed *callout_name* documents the holes at *refs* (a grouped
        pattern callout) — so neither becomes a table row or per-hole balloon. *refs*
        are :class:`HoleRef` position keys, not recogniser ``Hole`` objects, so the
        shared escalation stays IR-typed (ADR 0008 Amendment 6)."""
        self._pattern_callouts.add(callout_name)
        self._patterned_holes.update(refs)

    def is_pattern_callout(self, name) -> bool:
        """Is *name* a placed pattern (grouped ``n× ⌀``) callout?"""
        return name in self._pattern_callouts

    def is_hole_patterned(self, ref: HoleRef) -> bool:
        """Is the hole at *ref* already documented by a placed pattern callout?"""
        return ref in self._patterned_holes

    def cover_scattered_hole_doc(self, name) -> None:
        """Record that placed *name* is a scattered (unpatterned) plan-view hole
        callout or X/Y location dim — a candidate the hole table may replace."""
        self._scattered_hole_docs.add(name)

    def is_scattered_hole_doc(self, name) -> bool:
        """Is *name* a placed scattered hole callout / location dim (#351 PR-4c)?"""
        return name in self._scattered_hole_docs

    # -- dropped diameters ----------------------------------------------------

    def reset_dropped(self) -> None:
        """Clear dropped-diameter tracking (top of _auto_annotate)."""
        self._dropped_callout_diams = []
        self._dropped_profiles = []

    def drop_diam(self, diam) -> None:
        """Record a diameter dropped by the per-view callout cap."""
        self._dropped_callout_diams.append(diam)

    def drop_profile(self, profile: tuple) -> None:
        """Record an exact profiled-bore specification whose callout was dropped."""
        self._dropped_profiles.append(profile)

    @property
    def dropped_diams(self) -> list:
        """Diameters dropped by the cap (passed to lint_feature_coverage)."""
        return self._dropped_callout_diams

    @property
    def dropped_profiles(self) -> list[tuple]:
        """Profile specifications already reported by ``callout_dropped``."""
        return self._dropped_profiles

    # -- transactional snapshot (#647) ----------------------------------------

    def snapshot(self) -> tuple:
        """Capture the mutable coverage collections so finalize's transaction can
        roll a partial mutation back on a raise (#647) — the passes it replays
        mutate coverage (drop_diam / cover_scattered_hole_doc / cover_pattern)."""
        return (
            set(self._pattern_callouts),
            set(self._patterned_holes),
            set(self._scattered_hole_docs),
            list(self._dropped_callout_diams),
            list(self._dropped_profiles),
        )

    def restore(self, snap: tuple) -> None:
        """Restore the collections captured by :meth:`snapshot`."""
        pc, ph, shd, dropped, dropped_profiles = snap
        self._pattern_callouts = set(pc)
        self._patterned_holes = set(ph)
        self._scattered_hole_docs = set(shd)
        self._dropped_callout_diams = list(dropped)
        self._dropped_profiles = list(dropped_profiles)


def lint_feature_coverage(
    part,
    annotations,
    tol: float = 0.15,
    cyls=None,
    exclude=None,
    assembly=None,
    holes=None,
    bosses=None,
) -> list:
    """Coarse completeness check: report part diameters with no callout (#80).

    ``exclude`` is an optional iterable of diameters already accounted for by a
    more specific build-time lint (e.g. the per-view callout cap's
    ``callout_dropped``); these are skipped here so a dropped callout is not
    double-reported as ``feature_not_dimensioned``.

    ``assembly`` controls severity for a general-arrangement drawing of a
    multi-body part. A GA deliberately omits each part's bores (they belong on
    detail sheets), so demanding a callout for every cylinder is noise. When
    ``assembly`` is ``True`` the coverage codes (``feature_not_dimensioned`` /
    ``feature_count_mismatch``) are emitted at ``info`` severity instead of
    ``warning`` — kept queryable but out of the warning count and quality score.
    ``None`` (the default) auto-detects: a multi-solid ``part`` is treated as an
    assembly. Pass ``False`` to force strict single-part severity (#69).

    Builds a feature inventory from *part*'s hole/boss diameters (cylinder
    patches spanning at least ~half a turn around their axis in total, so
    fillets are ignored) and diffs it against either the structured
    ``covers_diameters`` metadata on annotations that draw their values
    geometrically (e.g. ``HoleCallout``), or every ø value mentioned in an
    unstructured annotation's label. A structured annotation is never parsed as
    free text too: suffix diameters such as a bolt-circle definition are not
    physical-feature coverage, and ``covers_count`` remains authoritative.
    Radius callouts are *not* counted — "R5 TYP" fillet notes would otherwise
    mask an undimensioned ø10 bore. Title blocks are skipped — part numbers
    like "BRACKET R8" are not callouts. Each uncovered diameter yields one
    ``feature_not_dimensioned`` warning.

    ``cyls`` accepts a precomputed ``analyse_cylinders(part)`` result so
    repeated lint runs need not re-scan the solid.

    Counts are checked too (#92): the part's holes (via ``recognise_holes``) give
    a required count per diameter (each bore, counterbore, and spotface
    occurrence counts one), and structured callouts declare how many holes
    they dimension (``covers_count`` — the ``n×`` prefix). A shortfall
    yields a ``feature_count_mismatch`` warning. A diameter covered by any
    free-text ø-label is exempt from the count check — text labels carry no
    count semantics. Location coverage remains out of scope (#93).
    """
    z_cyls, cross_cyls = cyls if cyls is not None else analyse_cylinders(part)
    if holes is None:
        holes = recognise_holes(part, cyls=(z_cyls, cross_cyls))
    # Coverage inventory: the *recognised* dimensionable diameters (bores,
    # cbore/spotface steps, bosses) from feature_diameters — built via
    # recognise_holes/recognise_bosses, so slot ends and interrupted recesses (partial
    # cylinders that an angle-only test mistakes for full bores) are excluded.
    # Replaces the raw full_cylinders patch list, which over-reported those as
    # undimensioned features (helpers #158/#159). Both *holes* and *bosses* reuse
    # the single feature inventory (#244/#264) — no detector runs twice here.
    inventory = feature_diameters(part, cyls=(z_cyls, cross_cyls), holes=holes, bosses=bosses)

    if assembly is None:
        assembly = len(part.solids()) > 1
    coverage_severity: Literal["info", "warning"] = "info" if assembly else "warning"

    mentioned: set[float] = set()
    text_mentioned: set[float] = set()
    provided: dict[float, int] = {}
    for ann in annotations:
        if isinstance(ann, TitleBlock):
            continue
        structured_diameters, text_diameters = _annotation_diameter_sources(ann)
        # A geometric HoleCallout now exposes equivalent semantic text (#1142), but the
        # shared source policy excludes it here: BCD suffixes are not physical coverage,
        # and structured ``covers_count`` must remain authoritative.
        mentioned.update(text_diameters)
        text_mentioned.update(text_diameters)
        count = getattr(ann, "covers_count", 1)
        for v in structured_diameters:
            mentioned.add(v)
            provided[v] = provided.get(v, 0) + count

    exclude = exclude or ()
    issues = [
        LintIssue(
            severity=coverage_severity,
            code="feature_not_dimensioned",
            message=f"cylindrical feature ø{_fmt(d)} has no diameter callout on the sheet",
        )
        for d in inventory
        if not any(abs(d - v) <= tol for v in mentioned)
        and not any(abs(d - e) <= tol for e in exclude)
    ]

    required: dict[float, int] = {}
    for h in holes:
        for d in (h.diameter, *(s.diameter for s in (h.cbore, h.spotface) if s)):
            key = next((k for k in required if abs(k - d) <= tol), d)
            required[key] = required.get(key, 0) + 1
    for d, need in sorted(required.items(), reverse=True):
        if any(abs(d - v) <= tol for v in text_mentioned):
            continue  # free-text coverage carries no count to check against
        have = sum(c for v, c in provided.items() if abs(d - v) <= tol)
        if 0 < have < need:
            issues.append(
                LintIssue(
                    severity=coverage_severity,
                    code="feature_count_mismatch",
                    message=(
                        f"{need} ø{_fmt(d)} features on the part but callouts account for {have}"
                    ),
                )
            )
    return issues


def lint_location_coverage(
    part,
    dwg,
    cyls=None,
    assembly=None,
    tol: float = 0.6,
    holes=None,
    patterns=None,
    profiled_bores=None,
) -> list:
    """Report holes with no **centre mark** or no **locating dimension**, derived
    from the drawing itself (not a build-time side channel — so it judges any
    producer, the engine or the model pipeline alike). Closes the location-coverage
    gap left out of :func:`lint_feature_coverage` (#218).

    *dwg* is the drawing, duck-typed: it must offer ``at(view, x, y, z)`` projection,
    ``iter_annotations()``, and ``view_of()``. For each hole, project its centre into
    the view normal to its axis (:data:`_END_ON`) and check the placed annotations:

    - **centre mark** — a ``CenterMark`` whose centre coincides with the projected
      hole centre (every hole, including pattern members, gets one);
    - **location** — some ``Dimension`` whose witness is aligned to the hole centre
      (a witness sits *on* the hole's projected coordinate; envelope dims sit at the
      part edges, so they don't false-match). **Patterned holes are exempt** — a
      bolt circle / array is located by its BCD / pitch, not per-hole dims.

    Coarse by design (a hole with *no* locating witness at all is the signal); severity
    mirrors :func:`lint_feature_coverage` (``info`` for an assembly, else ``warning``).
    """
    if holes is None:
        holes = recognise_holes(part, cyls=cyls) if cyls is not None else recognise_holes(part)
    profiled_bores = recognise_double_d_bores(part) if profiled_bores is None else profiled_bores
    source_holes = holes
    if not source_holes and not profiled_bores:
        return []
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    if patterns is None:
        patterns = recognise_hole_patterns(source_holes)
    holes = [*source_holes, *profiled_bores]
    # Position-keyed (HoleRef), not id()-keyed: the old identity set only worked because
    # recognise_hole_patterns reuses the same HoleRecord objects; a position key is robust
    # to any hole source and matches the rest of the engine's patterned-membership tests.
    patterned = {HoleRef.of(h.location) for pat in patterns for h in pat.holes}

    marks: dict[str, list] = {}
    dim_verts: dict[str, list] = {}
    for name, ann in dwg.iter_annotations():
        view = dwg.view_of(name)
        if view is None:
            continue
        if isinstance(ann, CenterMark):
            c = ann.center()
            marks.setdefault(view, []).append((c.X, c.Y))
        elif isinstance(ann, Dimension):
            dim_verts.setdefault(view, []).extend(_dim_vertices(ann))

    bb = part.bounding_box()
    centre = (bb.center().X, bb.center().Y, bb.center().Z)

    no_mark = no_loc = 0
    for h in holes:
        x, y, z = _xyz(h.location)
        axis = _axis_letter(h)
        view = _END_ON.get(axis, "plan")
        px, py, *_ = dwg.at(view, x, y, z)
        if not any(abs(cx - px) <= tol and abs(cy - py) <= tol for cx, cy in marks.get(view, ())):
            no_mark += 1
        # A hole coaxial with the part centre (the turning axis / a symmetry axis)
        # is located by centrelines, not a position dim — exempt from location.
        perp = [(c, q) for ax, c, q in zip("xyz", (x, y, z), centre) if ax != axis]
        coaxial = all(abs(c - q) <= 1.0 for c, q in perp)
        if (
            HoleRef.of(h.location) not in patterned
            and not coaxial
            and not any(
                abs(vx - px) <= tol or abs(vy - py) <= tol for vx, vy in dim_verts.get(view, ())
            )
        ):
            no_loc += 1

    issues = []
    if no_mark:
        issues.append(
            LintIssue(
                severity=severity,
                code="feature_no_centermark",
                message=f"{no_mark} hole(s) have no centre mark",
            )
        )
    if no_loc:
        issues.append(
            LintIssue(
                severity=severity,
                code="feature_not_located",
                message=f"{no_loc} hole(s) have no locating dimension",
            )
        )
    return issues


def _dimension_endpoint_pairs(dwg, view: str) -> list:
    """Placed dimension witness pairs + feature owner in *view*."""
    pairs = []
    for _name, ann in dwg.annotations_in_view(view):
        if not isinstance(ann, Dimension):
            continue
        pts = _dim_vertices(ann)
        if len(pts) >= 2:
            pairs.append((pts[0], pts[1], dwg.registry.feature_of(_name)))
    return pairs


def _pair_covers(
    pairs: list,
    axis: int,
    a: float,
    b: float,
    tol: float,
    owner=None,
) -> bool:
    """Whether a dimension's two witnesses span coordinates *a* and *b*."""
    return any(
        (abs(p[axis] - a) <= tol and abs(q[axis] - b) <= tol)
        or (abs(p[axis] - b) <= tol and abs(q[axis] - a) <= tol)
        for p, q, pair_owner in pairs
        if owner is None or pair_owner is owner
    )


def _supported_inner_profile(
    wire,
    plane_axes: tuple[str, str],
    tol: float,
    *,
    axis: str | None = None,
    double_d_bores=(),
    profile=_UNSET,
) -> bool:
    """Whether an inner boundary loop has a profile the current IR can describe.

    Circular holes, axis-aligned rectangles, true obrounds and proven double-D bores are the
    supported principal opening vocabulary. The double-D correspondence is delegated to its
    recogniser's profile reader, so critique cannot accept a shape the IR then omits.
    """
    edges = list(wire.edges())
    if not edges:
        return False
    types = [edge.geom_type for edge in edges]
    wbb = wire.bounding_box()
    ext = {axis: float(getattr(wbb.size, axis.upper())) for axis in plane_axes}
    profile = double_d_profile(wire, plane_axes, tol=tol) if profile is _UNSET else profile
    if profile is not None and axis is not None:
        axis_i = "xyz".index(axis)
        for bore in double_d_bores:
            bore_axis_i = max(range(3), key=lambda i: abs(float(bore.axis[i])))
            if bore_axis_i != axis_i:
                continue
            if (
                abs(float(bore.major_diameter) - profile.major_diameter) <= tol
                and abs(float(bore.across_flats) - profile.across_flats) <= tol
                and all(
                    abs(float(bore.location[i]) - profile.centre[i]) <= tol
                    for i in range(3)
                    if i != axis_i
                )
                and all(
                    abs(float(a) - b) <= max(tol, 1e-6)
                    for a, b in zip(
                        bore.flat_direction,
                        profile.flat_direction,
                        strict=True,
                    )
                )
            ):
                return True
    if all(kind == GeomType.CIRCLE for kind in types):
        try:
            first = edges[0]
            radius = float(first.radius)
            centre = first.arc_center
            same_circle = all(
                abs(float(edge.radius) - radius) <= tol
                and all(
                    abs(
                        float(getattr(edge.arc_center, axis.upper()))
                        - float(getattr(centre, axis.upper()))
                    )
                    <= tol
                    for axis in plane_axes
                )
                for edge in edges[1:]
            )
        except (AttributeError, ValueError):
            return False
        return same_circle and all(
            abs(ext[axis] - 2.0 * radius) <= max(8 * tol, radius * 1e-3) for axis in plane_axes
        )

    perimeter = 2.0 * sum(ext.values())
    if all(kind == GeomType.LINE for kind in types):
        # Split collinear edges are harmless, but every segment must lie on one of the four
        # bounding-box sides. The perimeter check alone would also accept an L-shaped loop.
        on_boundary = True
        for edge in edges:
            ebb = edge.bounding_box()
            varying = [axis for axis in plane_axes if float(getattr(ebb.size, axis.upper())) > tol]
            if len(varying) != 1:
                on_boundary = False
                break
            fixed_axis = next(axis for axis in plane_axes if axis != varying[0])
            fixed = float(getattr(ebb.center(), fixed_axis.upper()))
            if (
                min(
                    abs(fixed - float(getattr(wbb.min, fixed_axis.upper()))),
                    abs(fixed - float(getattr(wbb.max, fixed_axis.upper()))),
                )
                > tol
            ):
                on_boundary = False
                break
        return on_boundary and abs(float(wire.length) - perimeter) <= max(
            8 * tol, perimeter * 1e-3
        )

    if any(kind not in (GeomType.LINE, GeomType.CIRCLE) for kind in types):
        return False
    lines = [edge for edge in edges if edge.geom_type == GeomType.LINE]
    arcs = [edge for edge in edges if edge.geom_type == GeomType.CIRCLE]
    if len(lines) not in (2, 4) or len(arcs) < 2:
        return False

    short_axis, long_axis = sorted(plane_axes, key=lambda axis: ext[axis])
    short = ext[short_axis]
    radius_tol = max(8 * tol, short * 1e-3)
    try:
        radius = float(arcs[0].radius)
        arc_centres = [edge.arc_center for edge in arcs]
        if radius <= tol or any(abs(float(edge.radius) - radius) > radius_tol for edge in arcs):
            return False
    except (AttributeError, ValueError):
        return False

    profile_tol = max(radius_tol, max(ext.values()) * 1e-3)

    def coord(obj, axis: str) -> float:
        return float(getattr(obj, axis.upper()))

    def line_matches(edge, varying_axis: str, corner_radius: float) -> bool:
        ebb = edge.bounding_box()
        fixed_axis = next(axis for axis in plane_axes if axis != varying_axis)
        if (
            float(getattr(ebb.size, varying_axis.upper())) <= tol
            or float(getattr(ebb.size, fixed_axis.upper())) > tol
        ):
            return False
        fixed = coord(ebb.center(), fixed_axis)
        if (
            min(
                abs(fixed - coord(wbb.min, fixed_axis)),
                abs(fixed - coord(wbb.max, fixed_axis)),
            )
            > profile_tol
        ):
            return False
        return (
            abs(coord(ebb.min, varying_axis) - (coord(wbb.min, varying_axis) + corner_radius))
            <= profile_tol
            and abs(coord(ebb.max, varying_axis) - (coord(wbb.max, varying_axis) - corner_radius))
            <= profile_tol
        )

    def lines_match_both_sides(varying_axis: str) -> bool:
        matched = [edge for edge in lines if line_matches(edge, varying_axis, radius)]
        if len(matched) != 2:
            return False
        fixed_axis = next(axis for axis in plane_axes if axis != varying_axis)
        positions = [coord(edge.bounding_box().center(), fixed_axis) for edge in matched]
        return all(
            any(abs(position - bound) <= profile_tol for position in positions)
            for bound in (coord(wbb.min, fixed_axis), coord(wbb.max, fixed_axis))
        )

    def arcs_match(expected: list[tuple[float, float]], expected_length: float) -> bool:
        lengths = [0.0] * len(expected)
        for edge, centre in zip(arcs, arc_centres):
            matches = [
                i
                for i, point in enumerate(expected)
                if all(
                    abs(coord(centre, axis) - point[j]) <= profile_tol
                    for j, axis in enumerate(plane_axes)
                )
            ]
            if len(matches) != 1:
                return False
            lengths[matches[0]] += float(edge.length)
        return all(abs(length - expected_length) <= profile_tol for length in lengths)

    if len(lines) == 2:
        # A true obround has two long sides, semicircular ends, and cap radius equal to half
        # the short overall extent. The double-D in #1058 fails that last correspondence.
        if abs(radius - short / 2.0) > radius_tol or not lines_match_both_sides(long_axis):
            return False
        mid_short = coord(wbb.center(), short_axis)
        long_lo = coord(wbb.min, long_axis) + radius
        long_hi = coord(wbb.max, long_axis) - radius
        expected = (
            [(long_lo, mid_short), (long_hi, mid_short)]
            if plane_axes[0] == long_axis
            else [(mid_short, long_lo), (mid_short, long_hi)]
        )
        return arcs_match(expected, pi * radius)

    # Axis-aligned rounded rectangles are already represented by pocket/slot plus fillet IR
    # (the real #915 case). Four side runs terminate at four quarter-circle corner groups.
    if radius * 2.0 >= short - radius_tol:
        return False
    if not all(lines_match_both_sides(axis) for axis in plane_axes):
        return False
    axis0_centres = (
        coord(wbb.min, plane_axes[0]) + radius,
        coord(wbb.max, plane_axes[0]) - radius,
    )
    axis1_centres = (
        coord(wbb.min, plane_axes[1]) + radius,
        coord(wbb.max, plane_axes[1]) - radius,
    )
    expected = [(axis0, axis1) for axis0 in axis0_centres for axis1 in axis1_centres]
    return arcs_match(expected, pi * radius / 2.0)


def _has_unsupported_principal_inner_profile(part, bbox) -> bool:
    """Does a principal extremal face prove an internal profile outside the IR vocabulary?"""
    part_extent = (float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z))
    tol = max(1e-5, max(part_extent) * 1e-5)
    wires = []
    openings = []
    for face in part.faces():
        boundary = principal_boundary_plane(face, bbox)
        if boundary is None:
            continue
        axis, plane_axes, at = boundary
        for wire in face.inner_wires():
            profile = double_d_profile(wire, plane_axes, tol=tol)
            wires.append((axis, plane_axes, wire, profile))
            if profile is not None:
                openings.append((axis, at, profile, wire))
    double_d_bores = double_d_bores_from_openings(openings, bbox, part=part, tol=tol)
    return any(
        not _supported_inner_profile(
            wire,
            plane_axes,
            tol,
            axis=axis,
            double_d_bores=double_d_bores,
            profile=profile,
        )
        for axis, plane_axes, wire, profile in wires
    )


def _radial_outer_arc_count(part, bbox) -> int | None:
    """Return a proven radial arc count for an otherwise unsupported outer boundary.

    Equal tip arcs alone are not enough. They must share one circle, have equal angular
    length and be equally spaced around the full boundary. This proves a cyclic arc pattern,
    not that the intervening boundary segments repeat, and no module, pressure angle or gear
    standard is inferred.
    """
    part_scale = max(float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z))
    tol = max(1e-5, part_scale * 1e-5)
    best = None
    for face in part.faces():
        boundary = principal_boundary_plane(face, bbox)
        if boundary is None:
            continue
        _axis, plane_axes, _at = boundary
        edges = list(face.outer_wire().edges())
        arcs = [edge for edge in edges if edge.geom_type == GeomType.CIRCLE]
        # A plain circle and common rounded outlines are not a cyclic tooth-like boundary.
        if len(arcs) < 6 or len(arcs) == len(edges):
            continue
        try:
            radius = float(arcs[0].radius)
            centre = arcs[0].arc_center
            length = float(arcs[0].length)
            metric_tol = max(8 * tol, radius * 1e-3)
            if any(
                abs(float(edge.radius) - radius) > metric_tol
                or abs(float(edge.length) - length) > metric_tol
                or any(
                    abs(
                        float(getattr(edge.arc_center, a.upper()))
                        - float(getattr(centre, a.upper()))
                    )
                    > metric_tol
                    for a in plane_axes
                )
                for edge in arcs[1:]
            ):
                continue
            angles = sorted(
                atan2(
                    float(getattr(edge.position_at(0.5), plane_axes[1].upper()))
                    - float(getattr(centre, plane_axes[1].upper())),
                    float(getattr(edge.position_at(0.5), plane_axes[0].upper()))
                    - float(getattr(centre, plane_axes[0].upper())),
                )
                % tau
                for edge in arcs
            )
        except (AttributeError, ValueError):
            continue
        gaps = [(angles[(i + 1) % len(angles)] - angles[i]) % tau for i in range(len(angles))]
        expected = tau / len(angles)
        if all(abs(gap - expected) <= 1e-3 for gap in gaps):
            best = max(best or 0, len(arcs))
    return best


def lint_principal_profile_coverage(part, *, assembly=None) -> list:
    """Report principal inner or outer profiles outside the current feature vocabulary.

    The scan owns its physical extent: there is deliberately no caller-supplied bounding box
    that could narrow the answer. The double-D correspondence is the recogniser's shared pure
    correspondence proof over openings collected during this scan; lint accepts no foreign
    aggregate nor reruns a public recogniser. :class:`Drawing` caches the returned issues
    against the source part identity so repeated lint does not rescan the B-rep.
    """
    solids = list(part.solids())
    sources = solids if len(solids) > 1 else [part]
    unsupported_inner = any(
        _has_unsupported_principal_inner_profile(solid, solid.bounding_box()) for solid in sources
    )
    radial_arc_count = max(
        (
            count
            for solid in sources
            if (count := _radial_outer_arc_count(solid, solid.bounding_box())) is not None
        ),
        default=None,
    )
    if not unsupported_inner and radial_arc_count is None:
        return []
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    issues = []
    if unsupported_inner:
        issues.append(
            LintIssue(
                severity=severity,
                code="unrecognised_defining_geometry",
                message=(
                    "dimension-relevant source geometry is absent from recognised IR: "
                    "unsupported internal profile on a principal boundary face"
                ),
            )
        )
    if radial_arc_count is not None:
        issues.append(
            LintIssue(
                severity=severity,
                code="unrecognised_defining_geometry",
                message=(
                    "dimension-relevant source geometry is absent from recognised IR: "
                    "unsupported outer boundary on a principal face contains "
                    f"{radial_arc_count} evenly spaced common-circle arcs"
                ),
            )
        )
    return issues


def lint_prismatic_coverage(
    part,
    dwg,
    *,
    pads=None,
    pockets=None,
    bbox=None,
    assembly=None,
    tol: float = 0.6,
    features=(),
    recognition=None,
) -> list:
    """Report undefined prismatic features.

    Ground truth comes directly from geometry, while coverage comes from placed
    dimension witnesses (ADR 0015).  This intentionally does not trust the part
    model: the defect being detected is geometry that recognition/planning omitted.
    """
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    pairs_by_view: dict[str, list] = {}

    def pairs(view: str):
        return pairs_by_view.setdefault(view, _dimension_endpoint_pairs(dwg, view))

    issues = []
    pad_inventory = recognise_rectangular_pads(part) if pads is None else pads
    if pad_inventory:
        bb = bbox if bbox is not None else part.bounding_box()
        undefined = 0
        for pad in pad_inventory:
            yc = (pad.y0 + pad.y1) / 2
            xc = (pad.x0 + pad.x1) / 2
            x0, _, *_ = dwg.at("plan", pad.x0, yc, pad.z1)
            x1, _, *_ = dwg.at("plan", pad.x1, yc, pad.z1)
            xc_page, _, *_ = dwg.at("plan", xc, yc, pad.z1)
            _, y0, *_ = dwg.at("plan", xc, pad.y0, pad.z1)
            _, y1, *_ = dwg.at("plan", xc, pad.y1, pad.z1)
            bx0, _, *_ = dwg.at("plan", bb.min.X, yc, pad.z1)
            bx1, _, *_ = dwg.at("plan", bb.max.X, yc, pad.z1)
            sy0, _, *_ = dwg.at("side", xc, pad.y0, pad.z1)
            sy1, _, *_ = dwg.at("side", xc, pad.y1, pad.z1)
            syc, _, *_ = dwg.at("side", xc, yc, pad.z1)
            sby0, _, *_ = dwg.at("side", xc, bb.min.Y, pad.z1)
            sby1, _, *_ = dwg.at("side", xc, bb.max.Y, pad.z1)
            ps = pairs("plan")
            owner = next(
                (
                    f
                    for f in getattr(dwg.model(), "features", ())
                    if getattr(f, "kind", None) == "pad"
                    and abs(f.lo - pad.x0) <= tol
                    and abs(f.hi - pad.x1) <= tol
                    and abs(f.w_center - yc) <= tol
                    and abs(f.width - (pad.y1 - pad.y0)) <= tol
                ),
                None,
            )
            size_x = owner is not None and _pair_covers(ps, 0, x0, x1, tol, owner=owner)
            size_y = owner is not None and _pair_covers(ps, 1, y0, y1, tol, owner=owner)
            located_x = (
                abs(pad.x0 - bb.min.X) <= tol
                or abs(pad.x1 - bb.max.X) <= tol
                or any(
                    _pair_covers(ps, 0, edge, bound, tol)
                    for edge in (bx0, bx1)
                    for bound in (x0, x1, xc_page)
                )
            )
            located_y = (
                abs(pad.y0 - bb.min.Y) <= tol
                or abs(pad.y1 - bb.max.Y) <= tol
                or any(
                    _pair_covers(pairs("side"), 0, edge, bound, tol)
                    for edge in (sby0, sby1)
                    for bound in (sy0, sy1, syc)
                )
            )
            if not (size_x and size_y and located_x and located_y):
                undefined += 1
        if undefined:
            issues.append(
                LintIssue(
                    severity=severity,
                    code="pad_footprint_not_defined",
                    message=(
                        f"{undefined} rectangular raised pad(s) lack footprint size "
                        "or X/Y location dimensions"
                    ),
                )
            )

    pocket_inventory = recognise_pockets(part) if pockets is None else pockets
    model_pockets = []
    for feature in features:
        if getattr(feature, "kind", None) == "pocket":
            model_pockets.append((feature, (feature.frame.origin,), False))
        elif getattr(feature, "kind", None) == "pocket_pattern":
            model_pockets.append((feature.member, tuple(feature.members), True))
    missing_ir = 0

    def pocket_owner(pocket):
        source_location = pocket.location
        return next(
            (
                f
                for f, locations, is_pattern in model_pockets
                if f.width_axis == pocket.width_axis
                and f.long_axis == pocket.long_axis
                and abs(f.width - pocket.width) <= tol
                and abs(f.length - pocket.length) <= tol
                and abs(f.depth - pocket.depth) <= tol
                and (
                    is_pattern
                    or (
                        abs(f.w_center - pocket.w_center) <= tol
                        and abs(f.lo - pocket.lo) <= tol
                        and abs(f.hi - pocket.hi) <= tol
                    )
                )
                and any(
                    all(
                        abs(actual - expected) <= tol
                        for actual, expected in zip(at, source_location)
                    )
                    for at in locations
                )
            ),
            None,
        )

    unlocated = 0
    bb = bbox if bbox is not None else part.bounding_box()
    centre = bb.center()
    for pocket in pocket_inventory:
        if pocket_owner(pocket) is None:
            missing_ir += 1
            continue
        if getattr(pocket, "edge_anchored", False):
            continue
        view = _END_ON.get(pocket.depth_axis, "plan")
        x, y, z = pocket.location
        if pocket.depth_axis == "z":
            datum_plan, target_plan = (
                dwg.at("plan", bb.min.X, y, z),
                dwg.at("plan", x, y, z),
            )
            datum_side, target_side = (
                dwg.at("side", x, bb.min.Y, z),
                dwg.at("side", x, y, z),
            )
            covered_x = abs(x - centre.X) <= 1.0 or _pair_covers(
                pairs("plan"), 0, datum_plan[0], target_plan[0], tol
            )
            covered_y = abs(y - centre.Y) <= 1.0 or _pair_covers(
                pairs("side"), 0, datum_side[0], target_side[0], tol
            )
            if not (covered_x and covered_y):
                unlocated += 1
            continue
        ps = pairs(view)
        # Projection axes by principal view: plan=(x,y), front=(x,z), side=(y,z).
        coordinates = {
            "plan": ((x, centre.X, bb.min.X), (y, centre.Y, bb.min.Y)),
            "front": ((x, centre.X, bb.min.X), (z, centre.Z, bb.min.Z)),
            "side": ((y, centre.Y, bb.min.Y), (z, centre.Z, bb.min.Z)),
        }[view]
        datum_page = dwg.at(view, bb.min.X, bb.min.Y, bb.min.Z)
        target_page = dwg.at(view, x, y, z)
        covered = []
        for axis, (coord, mid, _datum) in enumerate(coordinates):
            symmetric = abs(coord - mid) <= 1.0
            witnessed = _pair_covers(
                ps,
                axis,
                datum_page[axis],
                target_page[axis],
                tol,
            )
            covered.append(symmetric or witnessed)
        if not all(covered):
            unlocated += 1
    if unlocated:
        issues.append(
            LintIssue(
                severity=severity,
                code="pocket_not_located",
                message=f"{unlocated} blind pocket(s) have no complete X/Y location scheme",
            )
        )
    # BOTH halves come from recognition, never from a caller argument (#1025). The old
    # `step_zs=` parameter fully determined the answer — `step_zs=[]` yielded no shoulders, so
    # `missing_transitions` was structurally zero and this check could never fire. The engine
    # never passed that, but a false-negative door in a completeness check is the one place a
    # clean absence is indistinguishable from a clean part, so it is closed by construction:
    # `recognition` is the run's aggregate, and an absent one is re-derived from the solid
    # rather than defaulted to something narrower.
    # Fail-closed on the TYPE, not just the name: `recognition=` replaced the old `step_zs=`,
    # and a duck-typed stand-in (`SimpleNamespace(risers=(), step_levels=())`) would silence
    # this check exactly as `step_zs=[]` did — the same false-negative door wearing a new
    # parameter (Codex #1031 r1). Only recognition's own frozen result is accepted.
    if recognition is not None and not isinstance(recognition, RecognitionResult):
        raise TypeError(
            f"lint_prismatic_coverage(recognition=) takes the run's RecognitionResult, got "
            f"{type(recognition).__name__}. A completeness check must not accept a "
            "caller-assembled inventory: an empty stand-in silences it."
        )
    _rec = build_recognition_result(part) if recognition is None else recognition
    source_shoulders = project_step_shoulders(
        _rec.risers, levels=_rec.step_ladder(bbox if bbox is not None else part.bounding_box())
    )
    model_shoulders = {
        (axis, round(pos, 3))
        for f in features
        if getattr(f, "kind", None) == "step_level"
        for axis, pos in getattr(f, "shoulders", ())
    }

    def _channel_key(channel):
        return (
            channel.width_axis,
            channel.long_axis,
            round(float(channel.width), 3),
            round(float(channel.w_center), 3),
            round(float(channel.lo), 3),
            round(float(channel.hi), 3),
            round(float(channel.d_lo), 3),
            round(float(channel.d_hi), 3),
            int(channel.open_sign),
        )

    source_channels = {_channel_key(channel) for channel in _rec.channels}
    model_shoulders.update(
        (feature.width_axis, round(position, 3))
        for feature in features
        if getattr(feature, "kind", None) == "channel" and _channel_key(feature) in source_channels
        for position in (
            feature.w_center - feature.width / 2,
            feature.w_center + feature.width / 2,
        )
    )
    # A lone vertical transition can legitimately be owned by a declared plate
    # thickness scheme. Two or more stations describe a stepped/slanted profile
    # chain and must survive into correlated step IR (#898).
    missing_transitions = (
        sum(
            1
            for shoulder in source_shoulders
            if (shoulder.axis, round(shoulder.position, 3)) not in model_shoulders
        )
        if len(source_shoulders) >= 2
        else 0
    )
    if missing_ir or missing_transitions:
        parts = []
        if missing_ir:
            parts.append(f"{missing_ir} bounded blind recess(es)")
        if missing_transitions:
            parts.append(f"{missing_transitions} slanted/stepped profile transition(s)")
        issues.append(
            LintIssue(
                severity=severity,
                code="unrecognised_defining_geometry",
                message=(
                    "dimension-relevant source geometry is absent from recognised IR: "
                    + ", ".join(parts)
                ),
            )
        )
    return issues


def _axial_covered_from_drawing(part, dwg, prof, tol: float = 0.6) -> int:
    """How many of a turned part's step lengths are dimensioned **in the drawing**
    — a step counts as covered when some profile-view ``Dimension`` has witnesses
    at both of its shoulders' page positions. Drawing-derived, so it judges any
    producer (not the engine's :class:`CoverageState` side channel).

    Works for every turning axis (orientation is data): X- and Y-turned chains
    are horizontal in their respective front/side profile views, while a
    Z-turned chain is vertical in the front view."""
    bb = part.bounding_box()
    c = bb.center()
    idx = "xyz".index(prof.axis)
    base = [c.X, c.Y, c.Z]
    use_x = prof.axis in ("x", "y")

    def shoulder_coord(view: str, s: float) -> float:
        pt = list(base)
        pt[idx] = s
        px, py, *_ = dwg.at(view, *pt)
        return float(px if use_x else py)

    # A crowded X-turned head or Y-turned side chain can be dimensioned in an
    # enlarged detail view (#304/#307/#892), not the principal profile — so a
    # shoulder counts as located when matched in EITHER source or detail view.
    views = ["side"] if prof.axis == "y" else ["front"]
    if prof.axis in ("x", "y"):
        views += sorted(v for v in dwg.views if v.startswith("detail_"))
    covered_steps: set[int] = set()
    for view in views:
        shoulder_c = {s: shoulder_coord(view, s) for s in prof.shoulders}
        dims = [
            (
                name,
                str(getattr(ann, "label", "") or ""),
                {(x if use_x else y) for x, y in _dim_vertices(ann)},
            )
            for name, ann in dwg.annotations_in_view(view)
            if isinstance(ann, Dimension)
        ]
        for i, step in enumerate(prof.steps):
            clo, chi = shoulder_c.get(step.lo), shoulder_c.get(step.hi)
            if clo is None or chi is None:
                # Defence-in-depth (#797): `TurnedProfile.shoulders` now includes every
                # step endpoint (so a non-contiguous profile's interior end face is a
                # shoulder), and this branch should be unreachable — but a lint pass must
                # never crash on an unguarded lookup, so skip rather than KeyError.
                continue
            for name, label, cs in dims:
                if not cs:
                    continue
                # A plain dim locates the step when it has a witness at each shoulder.
                if any(abs(v - clo) <= tol for v in cs) and any(abs(v - chi) <= tol for v in cs):
                    covered_steps.add(i)
                    break
                # A collapsed uniform-staircase dim ("N× v", #230) carries witnesses only
                # at the extremes of its run yet locates *every* shoulder within that run
                # (the collapse fires only when all steps are equal). Credit a step whose
                # both shoulders fall within the dim's span — but ONLY for an actual
                # step-length chain dim (name contains "steplen"), never an unrelated
                # "n× pitch" hole-array dim that happens to span the shoulders (#307 review).
                if (
                    "steplen" in name
                    and re.match(r"^\s*\d+\s*×", label)
                    and min(cs) - tol <= clo
                    and chi <= max(cs) + tol
                ):
                    covered_steps.add(i)
                    break
    return len(covered_steps)


def _overall_axial_extent_is_dimensioned(part, dwg, prof, tol: float = 0.6) -> bool:
    """Whether a profile-view dimension witnesses the turned profile's two outer ends."""
    view = "side" if prof.axis == "y" else "front"
    use_x = prof.axis in ("x", "y")

    def projected(station: float) -> float:
        c = part.bounding_box().center()
        point = [c.X, c.Y, c.Z]
        point["xyz".index(prof.axis)] = station
        x, y, *_ = dwg.at(view, *point)
        return float(x if use_x else y)

    lo, hi = projected(min(prof.shoulders)), projected(max(prof.shoulders))
    for _name, annotation in dwg.annotations_in_view(view):
        if not isinstance(annotation, Dimension):
            continue
        coords = {(x if use_x else y) for x, y in _dim_vertices(annotation)}
        if any(abs(value - lo) <= tol for value in coords) and any(
            abs(value - hi) <= tol for value in coords
        ):
            return True
    return False


def lint_axial_coverage(part, dwg, assembly=None, prof=_UNSET) -> list:
    """Report a stepped turned part whose axial step lengths are undimensioned.

    A turned part can have every diameter called out yet be unmanufacturable: with
    no shoulder located, the lengths are unknown (the drive-screw gap). A complete
    chain dimensions all ``n`` steps; coverage is counted **from the drawing**
    (:func:`_axial_covered_from_drawing`), not a build-time side channel — so it
    judges any producer. A shortfall yields one ``axial_length_missing`` issue.

    *dwg* is the drawing, duck-typed (needs ``at``/``annotations``/``view_of``).

    Covers **X-, Y-, and Z-axis** turning through the unified IR step-length
    chain (ADR 0008 #223), so a missing chain on any axis is a real gap
    (e.g. the chain skipped for want of page room). Severity mirrors
    :func:`lint_feature_coverage`: ``info`` for an assembly, else ``warning``.
    *prof* may be supplied (the single inventory, #244) to skip re-detection;
    omitted, it is detected here. A sentinel distinguishes "not supplied" from a
    valid ``prof=None`` (non-turned part).
    """
    if prof is _UNSET:
        prof = TurnedProfile.from_steps(recognise_turned_steps(part))
    if prof is None:
        return []
    if assembly is None:
        assembly = len(part.solids()) > 1
    n = len(prof.steps)
    covered = _axial_covered_from_drawing(part, dwg, prof)
    # A groove band's axial extent is dimensioned by its width callout, not a step length, so
    # detect.py leaves it out of the step-length chain (#606). Count each *rendered* groove-width
    # callout on the turning axis as covering its band — so a fully-dimensioned grooved shaft
    # (N−1 step lengths + the groove width) is not flagged (#628); a *dropped* groove callout
    # leaves its band uncovered, so a genuine gap still fires (reconcile rendered, not intent).
    covered += sum(1 for name in dwg.annotations() if name.startswith(f"m_groove_{prof.axis}"))
    if covered >= n:
        return []
    # #955: when placement drops the complete chain, its specific warning already says the
    # shoulders remain unresolved. If the compiler-approved overall fallback survived, the
    # generic "axial length absent" warning would duplicate that diagnosis even though the
    # part's total extent is now stated. The drop is a required half of this condition: an
    # authored drawing that chooses only the overall extent still lacks shoulder locations
    # and must continue to receive ``axial_length_missing``.
    registry = getattr(dwg, "registry", None)
    chain_drop_reported = any(
        issue.code == "step_dim_dropped" for issue in getattr(registry, "issues", ())
    )
    if chain_drop_reported and _overall_axial_extent_is_dimensioned(part, dwg, prof):
        return []
    return [
        LintIssue(
            severity="info" if assembly else "warning",
            code="axial_length_missing",
            message=(
                f"turned part has {n} axial steps but only {covered} step length(s) "
                f"dimensioned — shoulders cannot be located"
            ),
        )
    ]


def lint_boss_height_coverage(part, dwg, features, assembly=None) -> list:
    """Report modeled boss heights that have no rendered linear dimension (#632).

    Coverage is reconciled from the drawing registry's feature provenance, not a
    renderer side channel: a boss is covered only when one of its live annotations
    is a ``Dimension``. Boss diameter annotations are leaders, so they cannot mask a
    missing axial height. Bosses without a modeled height retain the historical
    diameter-only contract and are outside this check.
    """
    bosses = [
        feature
        for feature in features
        if getattr(feature, "kind", None) in ("boss", "polygonal_boss")
        and getattr(feature, "height", None) is not None
    ]
    missing = sum(
        1
        for boss in bosses
        if not any(isinstance(ann, Dimension) for ann in dwg.annotations_of(boss).values())
    )
    if not missing:
        return []
    if assembly is None:
        assembly = len(part.solids()) > 1
    return [
        LintIssue(
            severity="info" if assembly else "warning",
            code="boss_height_missing",
            message=f"{missing} boss height(s) are not dimensioned",
        )
    ]


def lint_declaration_reconciliation(features, cyls, *, recognition=None) -> list:
    """Flag a *declared* cylindrical feature with no matching geometry in the part (#487).

    On the declarative path (``Sheet`` / ``build_drawing(part, model=…)``) a declaration can go
    **stale**: the part is edited to remove a hole while the script still declares it, so a callout
    renders over solid material yet coverage lint (which checks *detected → dimensioned*) stays
    clean. This is the reverse direction — *declared → exists* — cross-checking each declared
    feature against recognised geometry.

    Only meaningful for a caller-DECLARED model; the detection path cannot over-declare, so the
    caller gates on ``_model_declared``. ``features`` is the declared ``PartModel.features``, read
    duck-typed (``.kind``/``.diameter``/``.frame`` — linting/ must not import ``model``); ``cyls``
    is the ``(z_cyls, cross_cyls)`` from :func:`analyse_cylinders`. Scope is the cylindrical
    singletons (hole/boss/step); a declared feature matches a recognised cylinder on same axis,
    ⌀ within ``_RECON_DIA_TOL`` and in-plane position within ``_RECON_POS_TOL`` (the axis + ⌀ +
    in-plane test ``sheet._match_object`` uses) **plus** a polarity check (``_RECON_EXTERNAL``)
    that ``_match_object`` lacks — a hole reconciles against a bore, a boss/step against external
    material. Non-fatal: every issue is a ``warning``.
    """
    records = [*cyls[0], *cyls[1]]
    if recognition is not None and not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "lint_declaration_reconciliation(recognition=) requires the run's "
            f"RecognitionResult; got {type(recognition).__name__}"
        )
    issues = []
    for f in features:
        if getattr(f, "kind", None) not in _RECON_KINDS:
            continue
        dia = getattr(f, "diameter", None)
        frame = getattr(f, "frame", None)
        if dia is None or frame is None:
            continue
        axis = str(frame.axis).lower()
        origin = frame.origin
        perp = [k for k in range(3) if k != "xyz".index(axis)]
        if f.kind == "hole" and getattr(f, "profile", None) == "double_d":
            across = getattr(f, "across_flats", None)
            direction = getattr(f, "profile_direction", None)
            matched = (
                recognition is not None
                and across is not None
                and direction is not None
                and any(
                    profiled_bore_key(
                        "double_d",
                        bore.axis,
                        bore.through,
                        bore.major_diameter,
                        bore.across_flats,
                        bore.flat_direction,
                    )
                    == profiled_bore_key(
                        f.profile,
                        axis,
                        f.through,
                        dia,
                        across,
                        direction,
                    )
                    and all(
                        abs(float(origin[k]) - float(bore.location[k])) <= _RECON_POS_TOL
                        for k in perp
                    )
                    for bore in recognition.double_d_bores
                )
            )
            if matched:
                continue
            across_label = "?" if across is None else _fmt(float(across))
            issues.append(
                LintIssue(
                    severity="warning",
                    code="declared_feature_absent",
                    message=(
                        f"declared double-D bore {_fmt(dia)} major × {across_label} A/F at "
                        f"({_fmt(origin[0])}, {_fmt(origin[1])}, {_fmt(origin[2])}) has no "
                        "matching through double-D profile in the part — stale declaration "
                        "or the feature was removed"
                    ),
                )
            )
            continue
        want_external = _RECON_EXTERNAL[f.kind]
        matched = any(
            str(c["axis"]).lower() == axis
            and bool(c["external"]) == want_external
            and abs(c["diameter"] - dia) <= _RECON_DIA_TOL
            and all(abs(origin[k] - c["axis_xyz"][k]) <= _RECON_POS_TOL for k in perp)
            for c in records
        )
        if matched:
            continue
        issues.append(
            LintIssue(
                severity="warning",
                code="declared_feature_absent",
                message=(
                    f"declared {f.kind} ⌀{_fmt(dia)} at "
                    f"({_fmt(origin[0])}, {_fmt(origin[1])}, {_fmt(origin[2])}) has no matching "
                    f"{axis}-axis cylinder in the part — stale declaration or the feature was removed"
                ),
            )
        )
    return issues
