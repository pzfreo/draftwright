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
from math import asin, atan2, isfinite, pi, sqrt, tau
from numbers import Real
from typing import Literal

from b123d_recognisers import (
    RecognitionResult,
    TurnedProfile,
    analyse_cylinders,
    build_raw_recognition_result,
    feature_diameters,
    project_step_shoulders,
    recognise_double_d_bores,
    recognise_hole_patterns,
    recognise_holes,
    recognise_pockets,
    recognise_rectangular_pads,
    recognise_turned_steps,
)
from build123d import GeomType
from build123d_drafting.helpers import CenterMark, Dimension, TitleBlock

from draftwright._core import (
    _END_ON,
    HoleRef,
    _annotation_diameter_sources,
    _axis_letter,
    _decode_hole_location_fact,
    _fmt,
    _xyz,
)
from draftwright.linting._registry import annotation_owner, satisfaction_ids
from draftwright.linting.issues import LintIssue
from draftwright.linting.profiled_bore_coverage import profiled_bore_key
from draftwright.recognition_frame import (
    groove_owns_turned_step_band,
    profiles_owning_axial_band,
)
from draftwright.view_plan import VIEW_AXES

_UNSET = object()  # sentinel: distinguishes "not supplied" from a valid prof=None

# Reconciliation tolerances (#487) mirror sheet._match_object (⌀ ≤ 0.2 mm, in-plane ≤ 0.5 mm):
# a declared feature matches a recognised cylinder within these. Kept in sync by comment — linting/
# sits below sheet in the DAG, so the literals cannot be shared by import.
_RECON_DIA_TOL = 0.2
_RECON_POS_TOL = 0.5
_LOCATION_AXIS_TOL = 1.0


def _location_ref(owner, point) -> HoleRef:
    """Location identity with an irrelevant through-axis coordinate removed.

    Declared through holes use the cutter origin while recognition reports the
    bore midpoint.  Those are the same hole for location purposes: only the two
    coordinates perpendicular to its axis can require locating dimensions.
    Blind/opposed holes retain their axial coordinate so distinct features do not
    acquire one another's semantic evidence.
    """
    axis = getattr(getattr(owner, "frame", None), "axis", None) or _axis_letter(owner)
    coords = list(_xyz(point))
    hole = getattr(owner, "member", owner)
    if getattr(hole, "through", False) or getattr(hole, "bottom", None) == "through":
        coords["xyz".index(axis)] = 0.0
    return HoleRef.of(coords)


# Declared feature kinds with a single defining cylinder to confirm against geometry, mapped to the
# cylinder polarity that confirms them: a hole is a bore (external=False); a boss / turned step is
# external material (external=True). Checking polarity stops a phantom hole being silenced by a
# coaxial boss/OD of the same ⌀ (and vice-versa) — a callout over the wrong material (#487 review).
# Envelope always exists; patterns/slots and aspects are out of scope (#499).
_RECON_EXTERNAL = {"hole": False, "boss": True, "step": True}
_RECON_KINDS = tuple(_RECON_EXTERNAL)  # derive to keep the kind list and polarity map in sync

#: Declared feature kinds any TRUTH check can examine against the geometry — this module's
#: `_RECON_KINDS`, plus the gear that `gear_coverage` reconciles. Exported because
#: `lint_summary`'s fidelity component must know whether a declaration is examinable at all,
#: and re-listing the kinds there let it report "checked, nothing false" over a declared slot
#: no check looks at (#1176 review r5).
#:
#: A first cut also listed ``"double_d_bore"``. There is no such `kind`: `declare.double_d_bore`
#: returns a `HoleFeature`, and `lint_declaration_reconciliation` reaches it as
#: ``kind == "hole" and profile == "double_d"``. Dead data, and the docstring's "plus the
#: profiled bores" described a distinction the tuple did not make (#1176 review r6).
#:
#: `_RECON_KINDS` is derived; ``"external_spur_gear"`` is a literal that must match
#: `gear_coverage`'s own filter. A new gear kind there would narrow this silently — in the
#: fail-CLOSED direction, and the `bool(issues)` override in `quality.py` catches the
#: consequence, so it is a real seam rather than a hazard.
EXAMINABLE_DECLARED_KINDS = (*_RECON_KINDS, "external_spur_gear")


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
        # The same events with their compiler-owned feature when available. The legacy
        # spec-only list remains for compatibility; critique uses this richer identity to
        # avoid combining a drop on A with placed authority on A to certify B (#1351).
        self._dropped_profile_evidence: list[tuple[tuple, object | None]] = []

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
        self._dropped_profile_evidence = []

    def drop_diam(self, diam) -> None:
        """Record a diameter dropped by the per-view callout cap."""
        self._dropped_callout_diams.append(diam)

    def drop_profile(self, profile: tuple, owner=None) -> None:
        """Record an exact profiled-bore specification whose callout was dropped."""
        self._dropped_profiles.append(profile)
        self._dropped_profile_evidence.append((profile, owner))

    @property
    def dropped_diams(self) -> list:
        """Diameters dropped by the cap (passed to lint_feature_coverage)."""
        return self._dropped_callout_diams

    @property
    def dropped_profiles(self) -> list[tuple]:
        """Profile specifications already reported by ``callout_dropped``."""
        return self._dropped_profiles

    @property
    def dropped_profile_evidence(self) -> list[tuple[tuple, object | None]]:
        """Dropped profile specifications paired with their exact IR owner when known."""
        return self._dropped_profile_evidence

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
            list(self._dropped_profile_evidence),
        )

    def restore(self, snap: tuple) -> None:
        """Restore the collections captured by :meth:`snapshot`."""
        pc, ph, shd, dropped, dropped_profiles, dropped_profile_evidence = snap
        self._pattern_callouts = set(pc)
        self._patterned_holes = set(ph)
        self._scattered_hole_docs = set(shd)
        self._dropped_callout_diams = list(dropped)
        self._dropped_profiles = list(dropped_profiles)
        self._dropped_profile_evidence = list(dropped_profile_evidence)


def lint_feature_coverage(
    part,
    annotations,
    tol: float = 0.15,
    cyls=None,
    exclude=None,
    assembly=None,
    holes=None,
    bosses=None,
    registry=None,
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

    A placed feature-linked note may instead carry explicit ``DimensionId``
    satisfaction provenance. Only canonical diameter parameters on that exact
    feature contribute; the note's prose is never parsed for this purpose (#1351).
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
    # One physical owner can carry the same requirement in several representations (for
    # example, both a callout and a manufacturing note). Union those authorities by exact
    # owner/value before counting them; summing annotations lets one documented bore certify
    # an identical undocumented sibling (#1351 review).
    owned_provided: dict[tuple[int, float], int] = {}
    unowned_provided: dict[float, int] = {}

    def provide(value, count, owner=None) -> None:
        value = float(value)
        count = int(count or 1)
        if owner is None:
            unowned_provided[value] = unowned_provided.get(value, 0) + count
            return
        key = (id(owner), value)
        owned_provided[key] = max(owned_provided.get(key, 0), count)

    if registry is not None:
        # Structured note authority is a semantic assertion, not prose parsing. Resolve only
        # canonical diameter parameters on the exact feature carried by each DimensionId;
        # malformed identities contribute nothing rather than guessing (#1351).
        identities = satisfaction_ids(registry)
        for identity in identities:
            feature = getattr(identity, "feature", None)
            parameter_id = getattr(identity, "parameter", None)
            parameter = next(
                (
                    item
                    for item in getattr(feature, "parameters", lambda: ())()
                    if item.parameter_id == parameter_id and item.kind == "diameter"
                ),
                None,
            )
            if parameter is None:
                continue
            value = float(parameter.value)
            mentioned.add(value)
            provide(value, getattr(feature, "count", 1), feature)
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
            provide(v, count, annotation_owner(registry, ann))

    provided: dict[float, int] = dict(unowned_provided)
    for (_owner_id, value), count in owned_provided.items():
        provided[value] = provided.get(value, 0) + count

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
    """Report holes with no **centre mark** or no **locating dimension**.

    Compiler-owned annotations carry semantic feature/axis evidence; external
    producers are judged from their placed witness geometry.  The two paths are
    deliberately independent: structured evidence must name the matching IR hole
    and location axis, while a geometric witness must align on that projected axis.
    Closes the location-coverage gap left out of :func:`lint_feature_coverage` (#218).

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
    patterned = {_location_ref(h, h.location) for pat in patterns for h in pat.holes}

    marks: dict[str, list] = {}
    dim_verts: dict[str, list] = {}
    structured_locations: set[tuple[object, str, HoleRef]] = set()
    registry = getattr(dwg, "registry", None)
    satisfied_locations = {
        identity.feature
        for identity in satisfaction_ids(registry)
        if getattr(identity, "parameter", None) == "location"
    }
    for name, ann in dwg.iter_annotations():
        view = dwg.view_of(name)
        if view is None:
            continue
        if isinstance(ann, CenterMark):
            c = ann.center()
            marks.setdefault(view, []).append((c.X, c.Y))
        elif isinstance(ann, Dimension):
            facts = tuple(getattr(ann, "covers_hole_locations", ()))
            # Structured compiler evidence is authoritative.  Letting the same
            # annotation fall back to its witness geometry would allow a wrong
            # feature/axis tag to self-certify merely by sharing an ordinate.
            decoded = tuple(
                parsed
                for fact in facts
                if (parsed := _decode_hole_location_fact(fact)) is not None
            )
            if not decoded:
                dim_verts.setdefault(view, []).extend(_dim_vertices(ann))
            for feature, parameter, point in decoded:
                if getattr(feature, "kind", None) == "hole":
                    structured_locations.add(
                        (feature, str(parameter), _location_ref(feature, point))
                    )

    # Index only semantic owners that placed facts actually carry. This avoids an
    # O(holes × model-features × members) rescan and preserves the documented
    # external-producer contract: no ``Drawing.model()`` method is required.
    feature_index: dict[tuple[str, HoleRef], set[object]] = {}
    semantic_owners = {owner for owner, _parameter, _point in structured_locations}
    for feature in semantic_owners | satisfied_locations:
        frame = getattr(feature, "frame", None)
        if frame is None:
            continue
        members = getattr(feature, "members", ()) or (frame.origin,)
        for point in members:
            feature_index.setdefault((frame.axis, _location_ref(feature, point)), set()).add(
                feature
            )

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
        perp = [(ax, c, q) for ax, c, q in zip("xyz", (x, y, z), centre) if ax != axis]
        ref = _location_ref(h, h.location)
        required_axes = [ax for ax, c, q in perp if abs(c - q) > _LOCATION_AXIS_TOL]
        if ref in patterned or not required_axes:
            continue

        features = feature_index.get((axis, ref), set())
        page_axes = VIEW_AXES[view]

        def _axis_covered(model_axis):
            semantic = any(owner in features for owner in satisfied_locations) or any(
                owner in features and parameter.endswith(f".{model_axis}") and point == ref
                for owner, parameter, point in structured_locations
            )
            page_index = page_axes.index(model_axis)
            geometric = any(
                abs((vx, vy)[page_index] - (px, py)[page_index]) <= tol
                for vx, vy in dim_verts.get(view, ())
            )
            return semantic or geometric

        if not all(_axis_covered(ax) for ax in required_axes):
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


def _passage_matches_principal_wire(
    passage,
    wire,
    axis: str,
    plane_axes: tuple[str, str],
    at: float,
    tol: float,
) -> bool:
    """Whether *wire* is an endpoint of this authoritative Passage record.

    Matching the endpoint geometry, rather than merely noticing that the part has some
    Passage, lets the specific Passage diagnostic replace the generic profile diagnostic
    without hiding an unrelated unsupported opening on the same part.
    """

    try:
        frame = passage.frame
        run = tuple(float(value) for value in frame.run)
        axis_i = "xyz".index(axis)
        if abs(abs(run[axis_i]) - 1.0) > tol or any(
            abs(value) > tol for i, value in enumerate(run) if i != axis_i
        ):
            return False
        matching_run = next(
            (
                float(run_at)
                for run_at in passage.run_interval
                if abs(float(frame.origin[axis_i]) + run[axis_i] * float(run_at) - at)
                <= max(8 * tol, 1e-3)
            ),
            None,
        )
        if matching_run is None:
            return False
        boundary = tuple(passage.section.boundary)
        vertices = tuple(wire.vertices())
        edges = tuple(wire.edges())
        if len(boundary) != len(vertices) or not boundary:
            return False
        if any(edge.geom_type not in (GeomType.LINE, GeomType.CIRCLE) for edge in edges):
            return False
        expected_arcs = sum(abs(float(vertex.bulge)) > tol for vertex in boundary)
        actual_arcs = sum(edge.geom_type == GeomType.CIRCLE for edge in edges)
        if expected_arcs != actual_arcs:
            return False

        expected: list[tuple[float, float]] = []
        for vertex in boundary:
            section_u, section_v = (float(value) for value in vertex.point)
            world = tuple(
                float(frame.origin[i])
                + run[i] * matching_run
                + float(frame.u[i]) * section_u
                + float(frame.v[i]) * section_v
                for i in range(3)
            )
            expected.append(
                (
                    world["xyz".index(plane_axes[0])],
                    world["xyz".index(plane_axes[1])],
                )
            )
        actual = [
            tuple(float(getattr(vertex, name.upper())) for name in plane_axes)
            for vertex in vertices
        ]
    except (AttributeError, TypeError, ValueError):
        return False

    match_tol = max(8 * tol, 1e-3)
    unmatched = list(actual)
    for point in expected:
        match = next(
            (
                i
                for i, candidate in enumerate(unmatched)
                if all(abs(a - b) <= match_tol for a, b in zip(point, candidate, strict=True))
            ),
            None,
        )
        if match is None:
            return False
        unmatched.pop(match)
    return not unmatched


def _prismatic_pocket_matches_principal_wire(
    pocket,
    wire,
    axis: str,
    plane_axes: tuple[str, str],
    at: float,
    tol: float,
) -> bool:
    """Whether *wire* is the open mouth of *pocket* on this principal face.

    ``PrismaticPocket.section`` is expressed in the two non-depth axes, in axis order.  The
    aggregate has already removed candidates also owned by ``Pocket``; this correlation
    only replaces the generic unsupported-profile report for the exact surviving polygonal
    recess.  It does not perform cross-family reconciliation itself.
    """

    try:
        if pocket.axis != axis:
            return False
        axis_i = "xyz".index(axis)
        open_sign = int(pocket.open_sign)
        if open_sign not in (-1, 1):
            return False
        mouth = float(pocket.at[axis_i]) + open_sign * float(pocket.depth) / 2
        match_tol = max(8 * tol, 1e-3)
        if abs(mouth - at) > match_tol:
            return False
        section = tuple(tuple(float(value) for value in point) for point in pocket.section)
        vertices = tuple(wire.vertices())
        edges = tuple(wire.edges())
        if (
            not section
            or int(pocket.sides) != len(section)
            or len(section) != len(vertices)
            or len(edges) != len(section)
            or any(edge.geom_type != GeomType.LINE for edge in edges)
        ):
            return False
        actual = [
            tuple(float(getattr(vertex, name.upper())) for name in plane_axes)
            for vertex in vertices
        ]
    except (AttributeError, TypeError, ValueError):
        return False

    unmatched = list(actual)
    for point in section:
        match = next(
            (
                i
                for i, candidate in enumerate(unmatched)
                if all(abs(a - b) <= match_tol for a, b in zip(point, candidate, strict=True))
            ),
            None,
        )
        if match is None:
            return False
        unmatched.pop(match)
    return not unmatched


def _principal_boundary_plane(face, bbox) -> tuple[str, tuple[str, str], float] | None:
    """Return a solid-extremal principal plane as independent lint evidence.

    This is intentionally a small Draftwright-owned physical predicate rather than a
    provider helper: principal boundary faces are part of lint's independent denominator,
    while recognised occurrences arrive separately through the build-owned aggregate.
    """
    if face.geom_type != GeomType.PLANE:
        return None
    fbb = face.bounding_box()
    extent = {axis: float(getattr(fbb.size, axis.upper())) for axis in "xyz"}
    part_extent = (float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z))
    tol = max(1e-5, max(part_extent) * 1e-5)
    flat = [axis for axis, value in extent.items() if value <= tol]
    if len(flat) != 1:
        return None
    axis = flat[0]
    attr = axis.upper()
    at = float(getattr(fbb.center(), attr))
    if (
        min(
            abs(at - float(getattr(bbox.min, attr))),
            abs(at - float(getattr(bbox.max, attr))),
        )
        > tol
    ):
        return None
    plane_axes = tuple(candidate for candidate in "xyz" if candidate != axis)
    return axis, (plane_axes[0], plane_axes[1]), at


def _double_d_bore_matches_principal_wire(
    bore,
    wire,
    axis: str,
    plane_axes: tuple[str, str],
    at: float,
    bbox,
    tol: float,
) -> bool:
    """Correlate one physical mouth with one public ``DoubleDBore`` occurrence.

    The aggregate proves that the full profile prism is void. Lint independently proves
    that this exact extremal wire is one of that occurrence's two mouths; neither side
    alone can certify support. The metric checks reject topology-similar lenses, obrounds,
    and arbitrary line/arc loops without importing the provider's private classifier.
    """

    def number(value) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError("correspondence evidence must be a real number")
        result = float(value)
        if not isfinite(result):
            raise ValueError("non-finite correspondence evidence")
        return result

    def coord(obj, name: str) -> float:
        return number(getattr(obj, name.upper()))

    try:
        if type(bore.through) is not bool or not bore.through:
            return False
        axis_i = "xyz".index(axis)
        axis_vector = tuple(number(value) for value in bore.axis)
        if len(axis_vector) != 3:
            return False
        axis_component = axis_vector[axis_i]
        if abs(abs(axis_component) - 1.0) > 1e-6 or any(
            abs(component) > 1e-6 for i, component in enumerate(axis_vector) if i != axis_i
        ):
            return False

        location = tuple(number(value) for value in bore.location)
        if len(location) != 3:
            return False
        depth = number(bore.depth)
        if depth <= tol:
            return False
        record_ends = sorted((location[axis_i], location[axis_i] - axis_component * depth))
        solid_ends = sorted((coord(bbox.min, axis), coord(bbox.max, axis)))
        span_tol = max(8 * tol, depth * 1e-5, 1e-4)
        if any(
            abs(actual - expected) > span_tol for actual, expected in zip(record_ends, solid_ends)
        ):
            return False
        matching_ends = [
            i for i, endpoint in enumerate(record_ends) if abs(at - endpoint) <= span_tol
        ]
        if len(matching_ends) != 1:
            return False

        major_diameter = number(bore.major_diameter)
        across_flats = number(bore.across_flats)
        radius = major_diameter / 2.0
        half_af = across_flats / 2.0
        if radius <= tol or half_af <= tol or half_af >= radius - tol:
            return False

        flat_direction = tuple(number(value) for value in bore.flat_direction)
        if len(flat_direction) != 3 or abs(flat_direction[axis_i]) > 1e-6:
            return False
        flat_norm = sqrt(sum(flat_direction[i] ** 2 for i in range(3) if i != axis_i))
        if abs(flat_norm - 1.0) > 1e-6:
            return False

        edges = tuple(wire.edges())
        lines = tuple(edge for edge in edges if edge.geom_type == GeomType.LINE)
        arcs = tuple(edge for edge in edges if edge.geom_type == GeomType.CIRCLE)
        if len(edges) != 4 or len(lines) != 2 or len(arcs) != 2:
            return False

        centre_values = list(location)
        centre_values[axis_i] = at
        centre = (centre_values[0], centre_values[1], centre_values[2])
        wire_scale = max(coord(wire.bounding_box().size, name) for name in plane_axes)
        metric_tol = max(8 * tol, wire_scale * 1e-3, 1e-4)
        for arc in arcs:
            if abs(number(arc.radius) - radius) > metric_tol:
                return False
            if any(
                abs(coord(arc.arc_center, name) - centre["xyz".index(name)]) > metric_tol
                for name in plane_axes
            ):
                return False

        expected_chord = 2.0 * sqrt(radius * radius - half_af * half_af)
        offsets: list[float] = []
        for line in lines:
            vertices = tuple(line.vertices())
            if len(vertices) != 2 or abs(number(line.length) - expected_chord) > metric_tol:
                return False
            ends = [
                tuple(number(getattr(vertex, name.upper())) for name in "xyz")
                for vertex in vertices
            ]
            if any(abs(end[axis_i] - at) > metric_tol for end in ends):
                return False
            if any(
                abs(sqrt(sum((end[i] - centre[i]) ** 2 for i in range(3))) - radius) > metric_tol
                for end in ends
            ):
                return False
            delta = tuple(ends[1][i] - ends[0][i] for i in range(3))
            length = sqrt(sum(component * component for component in delta))
            if length <= tol:
                return False
            direction = tuple(component / length for component in delta)
            if abs(sum(direction[i] * flat_direction[i] for i in range(3))) > 1e-4:
                return False
            midpoint = tuple((ends[0][i] + ends[1][i]) / 2.0 for i in range(3))
            offsets.append(sum((midpoint[i] - centre[i]) * flat_direction[i] for i in range(3)))
        offsets.sort()
        if abs(offsets[0] + half_af) > metric_tol or abs(offsets[1] - half_af) > metric_tol:
            return False

        expected_arc = 2.0 * radius * asin(half_af / radius)
        return all(abs(number(arc.length) - expected_arc) <= metric_tol for arc in arcs)
    except (AttributeError, OverflowError, TypeError, ValueError):
        return False


def _claim_double_d_bore_mouth(
    bores,
    claimed: set[tuple[int, int]],
    wire,
    axis: str,
    plane_axes: tuple[str, str],
    at: float,
    bbox,
    tol: float,
) -> bool:
    """Claim one aggregate occurrence endpoint without reusing it for another mouth."""
    axis_i = "xyz".index(axis)
    for record_i, bore in enumerate(bores):
        if not _double_d_bore_matches_principal_wire(
            bore,
            wire,
            axis,
            plane_axes,
            at,
            bbox,
            tol,
        ):
            continue
        axis_component = float(bore.axis[axis_i])
        ends = (
            float(bore.location[axis_i]),
            float(bore.location[axis_i]) - axis_component * float(bore.depth),
        )
        mouth_i = min(range(2), key=lambda i: abs(at - ends[i]))
        key = (record_i, mouth_i)
        if key in claimed:
            continue
        claimed.add(key)
        return True
    return False


def _supported_inner_profile(
    wire,
    plane_axes: tuple[str, str],
    tol: float,
    *,
    axis: str | None = None,
    at: float | None = None,
    solid_bbox=None,
    double_d_bores=(),
    claimed_double_d_mouths: set[tuple[int, int]] | None = None,
    prismatic_pockets=(),
    section_passages=(),
) -> bool:
    """Whether an inner boundary loop has a specific semantic owner.

    Circular holes, axis-aligned rectangles, true obrounds and proven double-D bores are the
    supported principal opening vocabulary. Authoritative SectionPassage and aggregate-
    reconciled PrismaticPocket occurrences are also accounted for, but remain unsupported and
    are reported by their specific coverage checks. Exact boundary matching prevents either
    diagnostic from silencing an unrelated profile.
    """
    edges = list(wire.edges())
    if not edges:
        return False
    types = [edge.geom_type for edge in edges]
    wbb = wire.bounding_box()
    ext = {axis: float(getattr(wbb.size, axis.upper())) for axis in plane_axes}
    if (
        axis is not None
        and at is not None
        and any(
            _prismatic_pocket_matches_principal_wire(pocket, wire, axis, plane_axes, at, tol)
            for pocket in prismatic_pockets
        )
    ):
        return True
    if (
        axis is not None
        and at is not None
        and any(
            _passage_matches_principal_wire(passage, wire, axis, plane_axes, at, tol)
            for passage in section_passages
        )
    ):
        return True
    if (
        axis is not None
        and at is not None
        and solid_bbox is not None
        and _claim_double_d_bore_mouth(
            double_d_bores,
            claimed_double_d_mouths if claimed_double_d_mouths is not None else set(),
            wire,
            axis,
            plane_axes,
            at,
            solid_bbox,
            tol,
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


def _has_unsupported_principal_inner_profile(
    part,
    bbox,
    *,
    double_d_bores=(),
    claimed_double_d_mouths: set[tuple[int, int]] | None = None,
    prismatic_pockets=(),
    section_passages=(),
) -> bool:
    """Does a principal extremal face prove an internal profile outside the IR vocabulary?"""
    part_extent = (float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z))
    tol = max(1e-5, max(part_extent) * 1e-5)
    wires = []
    for face in part.faces():
        boundary = _principal_boundary_plane(face, bbox)
        if boundary is None:
            continue
        axis, plane_axes, at = boundary
        for wire in face.inner_wires():
            wires.append((axis, plane_axes, at, wire))
    return any(
        not _supported_inner_profile(
            wire,
            plane_axes,
            tol,
            axis=axis,
            at=at,
            solid_bbox=bbox,
            double_d_bores=double_d_bores,
            claimed_double_d_mouths=claimed_double_d_mouths,
            prismatic_pockets=prismatic_pockets,
            section_passages=section_passages,
        )
        for axis, plane_axes, at, wire in wires
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
        boundary = _principal_boundary_plane(face, bbox)
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


def lint_principal_profile_coverage(
    part,
    *,
    double_d_bores,
    assembly=None,
    prismatic_pockets=(),
    section_passages=(),
) -> list:
    """Report principal inner or outer profiles outside the current feature vocabulary.

    The scan owns its physical extent: there is deliberately no caller-supplied bounding box
    that could narrow the answer. ``double_d_bores``, ``section_passages`` and
    ``prismatic_pockets`` are explicit projections of the drawing's cached authoritative public
    aggregate. The Double-D projection is required so an omitted inventory fails loudly instead
    of changing the physical diagnosis. These records replace a generic profile finding only
    when independent physical evidence matches the exact occurrence; lint neither imports
    provider-private helpers nor reruns a recogniser. :class:`Drawing` caches the returned issues
    against the source part identity so repeated lint does not rescan the B-rep.
    """
    solids = list(part.solids())
    sources = solids if len(solids) > 1 else [part]
    claimed_double_d_mouths: set[tuple[int, int]] = set()
    unsupported_inner = any(
        _has_unsupported_principal_inner_profile(
            solid,
            solid.bounding_box(),
            double_d_bores=double_d_bores,
            claimed_double_d_mouths=claimed_double_d_mouths,
            prismatic_pockets=prismatic_pockets,
            section_passages=section_passages,
        )
        for solid in sources
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
    registry = getattr(dwg, "registry", None)
    placed_ids = (
        {identity for name in registry.names() for identity in registry.measurement_of(name)}
        if registry is not None
        else set()
    )
    structured_ids = satisfaction_ids(registry)
    satisfied_ids = structured_ids | placed_ids

    def satisfied(feature, parameter: str) -> bool:
        return any(
            identity.feature == feature
            and (
                identity.parameter == parameter
                or (
                    identity.parameter == "location"
                    and parameter.startswith(f"{feature.LOCATION_STEM}.")
                )
            )
            for identity in satisfied_ids
        )

    def location_note_satisfied(feature) -> bool:
        return any(
            identity.feature == feature and identity.parameter == "location"
            for identity in structured_ids
        )

    def pairs(view: str):
        return pairs_by_view.setdefault(view, _dimension_endpoint_pairs(dwg, view))

    issues = []
    pad_inventory = recognise_rectangular_pads(part) if pads is None else pads
    if pad_inventory:
        bb = bbox if bbox is not None else part.bounding_box()
        undefined = 0
        for pad in pad_inventory:
            record_bounds = {
                "x": (pad.x0, pad.x1),
                "y": (pad.y0, pad.y1),
                "z": (pad.z0, pad.z1),
            }
            owner = next(
                (
                    f
                    for f in getattr(dwg.model(), "features", ())
                    if getattr(f, "kind", None) == "pad"
                    and f.frame.axis == pad.axis
                    and f.direction == pad.direction
                    and all(
                        abs(actual - expected) <= tol
                        for axis in "xyz"
                        for actual, expected in zip(
                            f.bounds(axis), record_bounds[axis], strict=True
                        )
                    )
                ),
                None,
            )
            if owner is None:
                undefined += 1
                continue
            if pad.axis != "z":
                complete = all(
                    satisfied(owner, parameter)
                    for parameter in (
                        "pad_width.length",
                        "pad_length.length",
                        "pad_height.length",
                        f"{owner.LOCATION_STEM}.{owner.long_axis}",
                        f"{owner.LOCATION_STEM}.{owner.width_axis}",
                    )
                )
                if not complete:
                    undefined += 1
                continue

            # Legacy Z-normal drawings may predate structured satisfaction authority, so
            # retain the geometric fallback for their footprint/location marks. The local
            # pad height is new compiler-owned evidence and must retain its exact identity.
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
            size_x = _pair_covers(ps, 0, x0, x1, tol, owner=owner) or satisfied(
                owner, "pad_length.length"
            )
            size_y = _pair_covers(ps, 1, y0, y1, tol, owner=owner) or satisfied(
                owner, "pad_width.length"
            )
            located_x = location_note_satisfied(owner) or (
                abs(pad.x0 - bb.min.X) <= tol
                or abs(pad.x1 - bb.max.X) <= tol
                or any(
                    _pair_covers(ps, 0, edge, bound, tol)
                    for edge in (bx0, bx1)
                    for bound in (x0, x1, xc_page)
                )
            )
            located_y = location_note_satisfied(owner) or (
                abs(pad.y0 - bb.min.Y) <= tol
                or abs(pad.y1 - bb.max.Y) <= tol
                or any(
                    _pair_covers(pairs("side"), 0, edge, bound, tol)
                    for edge in (sby0, sby1)
                    for bound in (sy0, sy1, syc)
                )
            )
            height = satisfied(owner, "pad_height.length")
            if not (size_x and size_y and height and located_x and located_y):
                undefined += 1
        if undefined:
            issues.append(
                LintIssue(
                    severity=severity,
                    code="pad_footprint_not_defined",
                    message=(
                        f"{undefined} rectangular raised pad(s) lack footprint size, "
                        "attachment-axis height, or in-plane location dimensions"
                    ),
                )
            )

    pocket_inventory = recognise_pockets(part) if pockets is None else pockets
    model_pockets = []
    for feature in features:
        if getattr(feature, "kind", None) == "pocket":
            model_pockets.append((feature, feature, (feature.frame.origin,), False))
        elif getattr(feature, "kind", None) == "pocket_pattern":
            model_pockets.append((feature.member, feature, tuple(feature.members), True))
    missing_ir = 0

    def pocket_owner(pocket):
        source_location = pocket.location
        return next(
            (
                owner
                for f, owner, locations, is_pattern in model_pockets
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
        owner = pocket_owner(pocket)
        if owner is None:
            missing_ir += 1
            continue
        if satisfied(owner, "location"):
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
        # A datum-starting pocket needs no redundant long-axis centre dimension: the
        # coincident edge plus the length callout defines it. Ordinary inset pockets
        # retain their established centre-location scheme. Keep critique on the same
        # conditional target as the compiler.
        target_world = {"x": x, "y": y, "z": z}
        long_datum = float(getattr(bb.min, pocket.long_axis.upper()))
        if abs(pocket.lo - long_datum) <= tol:
            target_world[pocket.long_axis] = pocket.lo
        # Projection axes by principal view: plan=(x,y), front=(x,z), side=(y,z).
        coordinates = {
            "plan": (
                (target_world["x"], centre.X, bb.min.X),
                (target_world["y"], centre.Y, bb.min.Y),
            ),
            "front": (
                (target_world["x"], centre.X, bb.min.X),
                (target_world["z"], centre.Z, bb.min.Z),
            ),
            "side": (
                (target_world["y"], centre.Y, bb.min.Y),
                (target_world["z"], centre.Z, bb.min.Z),
            ),
        }[view]
        datum_page = dwg.at(view, bb.min.X, bb.min.Y, bb.min.Z)
        target_page = dwg.at(
            view,
            target_world["x"],
            target_world["y"],
            target_world["z"],
        )
        covered = []
        for axis, (coord, mid, datum) in enumerate(coordinates):
            symmetric = abs(coord - mid) <= 1.0
            datum_coincident = abs(coord - datum) <= tol
            witnessed = _pair_covers(
                ps,
                axis,
                datum_page[axis],
                target_page[axis],
                tol,
            )
            covered.append(symmetric or datum_coincident or witnessed)
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
    _rec = build_raw_recognition_result(part) if recognition is None else recognition
    ladder_bounds = bbox if bbox is not None else part.bounding_box()
    source_shoulders = project_step_shoulders(
        _rec.risers,
        levels=_rec.step_ladder_for_z_span(ladder_bounds.min.Z, ladder_bounds.max.Z),
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


def _turned_axis_origin(part, prof) -> tuple[float, float, float]:
    """The profile's body-local axis line, with a legacy whole-part fallback."""
    key = getattr(prof, "profile", None)
    if key is not None:
        return (
            float(key.axis_origin[0]),
            float(key.axis_origin[1]),
            float(key.axis_origin[2]),
        )
    centre = part.bounding_box().center()
    return (float(centre.X), float(centre.Y), float(centre.Z))


def _feature_on_turned_axis(feature, prof, tol: float = 0.5) -> bool:
    """Whether an IR feature belongs to this body-local turned axis line."""
    profile_group = getattr(prof, "profile_group", None)
    feature_group = getattr(feature, "profile_group", None)
    if profile_group is not None and getattr(feature, "kind", None) == "step":
        # Declared/emitted coaxial occurrences may share an axis, span, and even diameter.
        # Their opaque declaration token is one exact ownership witness. Do not geometrically
        # credit one group's placed measurement to another group (#1357).
        if feature_group != profile_group:
            return False
        # Tokens are caller-chosen and may be reused on another physical axis line. Exact
        # ownership requires both the opaque relationship and the geometry below.
    key = getattr(prof, "profile", None)
    if key is None:
        return True
    feature_profile = getattr(feature, "profile", None)
    if feature_profile is not None:
        return bool(feature_profile == key)
    axis = getattr(feature, "axis", None) or getattr(getattr(feature, "frame", None), "axis", None)
    origin = getattr(getattr(feature, "frame", None), "origin", None)
    if axis != prof.axis or origin is None:
        return False
    axis_index = "xyz".index(prof.axis)
    axis_tol = 1e-9 if profile_group is not None else tol
    if not all(
        abs(float(origin[index]) - float(key.axis_origin[index])) <= axis_tol
        for index in range(3)
        if index != axis_index
    ):
        return False
    profile_lo, profile_hi = float(min(prof.shoulders)), float(max(prof.shoulders))
    span = getattr(feature, "span", None)
    if span is not None:
        feature_lo, feature_hi = sorted(float(point[axis_index]) for point in span)
        return feature_lo >= profile_lo - tol and feature_hi <= profile_hi + tol
    if getattr(feature, "kind", None) == "groove":
        centre = float(origin[axis_index])
        half_width = float(feature.width) / 2.0
        return centre - half_width >= profile_lo - tol and centre + half_width <= profile_hi + tol
    return True


def _turned_profile_views(axis: str) -> tuple[str, str]:
    """The two principal views that show *axis* longitudinally."""
    return {
        "x": ("front", "plan"),
        "y": ("side", "plan"),
        "z": ("front", "side"),
    }[axis]


def _profile_projection(dwg, view: str, origin, axis: str) -> tuple[bool, float]:
    """Whether the axis is horizontal on *view*, and the axis line's cross coordinate."""
    point = list(origin)
    px, py, *_ = dwg.at(view, *point)
    point["xyz".index(axis)] += 1.0
    ax, ay, *_ = dwg.at(view, *point)
    horizontal = abs(float(ax) - float(px)) >= abs(float(ay) - float(py))
    return horizontal, float(py if horizontal else px)


def _step_length_owners(dwg, name: str) -> tuple[object, ...]:
    """Exact step features whose compiled length claim produced annotation *name*."""
    registry = getattr(dwg, "registry", None)
    if registry is None:
        return ()
    return tuple(
        identity.feature
        for identity in registry.measurement_of(name)
        if getattr(identity, "parameter", None) == "step.length"
        and getattr(identity.feature, "kind", None) == "step"
    )


def _axial_covered_from_drawing(
    part, dwg, prof, *, sibling_profiles=(), tol: float = 0.6
) -> set[int]:
    """Which turned-profile step lengths are dimensioned **in the drawing**
    — a step counts as covered when some profile-view ``Dimension`` has witnesses
    at both of its shoulders' page positions. Drawing-derived, so it judges any
    producer (not the engine's :class:`CoverageState` side channel).

    Works for every turning axis (orientation is data) and both orthographic profile views,
    so parallel bodies can use whichever projection visibly separates their axis lines."""
    idx = "xyz".index(prof.axis)
    base = list(_turned_axis_origin(part, prof))

    def profile_cross(profile, view: str) -> float:
        _horizontal, cross = _profile_projection(
            dwg, view, _turned_axis_origin(part, profile), profile.axis
        )
        return cross

    def shoulder_coord(view: str, s: float, *, horizontal: bool) -> float:
        pt = list(base)
        pt[idx] = s
        px, py, *_ = dwg.at(view, *pt)
        return float(px if horizontal else py)

    # A crowded X-turned head or Y-turned side chain can be dimensioned in an
    # enlarged detail view (#304/#307/#892), not the principal profile — so a
    # shoulder counts as located when matched in EITHER source or detail view.
    views = [view for view in _turned_profile_views(prof.axis) if view in dwg.views]
    if prof.axis in ("x", "y"):
        views += sorted(v for v in dwg.views if v.startswith("detail_"))
    covered_steps: set[int] = set()
    for view in views:
        horizontal, _own_cross = _profile_projection(dwg, view, base, prof.axis)
        shoulder_c = {s: shoulder_coord(view, s, horizontal=horizontal) for s in prof.shoulders}
        dims = [
            (
                name,
                str(getattr(ann, "label", "") or ""),
                {(x if horizontal else y) for x, y in _dim_vertices(ann)},
                {(y if horizontal else x) for x, y in _dim_vertices(ann)},
                _step_length_owners(dwg, name),
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
            for name, label, cs, cross_coords, owners in dims:
                if not cs:
                    continue
                if owners and not any(_feature_on_turned_axis(owner, prof) for owner in owners):
                    continue
                # Parallel profiles can have identical axial ordinates. A dimension belongs to
                # the nearest visible axis line in this profile view; without this gate one
                # shaft's two witnesses would certify every sibling at the same stations.
                if not owners and getattr(prof, "profile", None) is not None and cross_coords:
                    dim_cross = sum(cross_coords) / len(cross_coords)
                    own_distance = abs(dim_cross - profile_cross(prof, view))
                    sibling_distances = [
                        abs(dim_cross - profile_cross(sibling, view))
                        for sibling in sibling_profiles
                        if sibling is not prof and sibling.axis == prof.axis
                    ]
                    if any(distance < own_distance - tol for distance in sibling_distances) or any(
                        abs(distance - own_distance) <= tol for distance in sibling_distances
                    ):
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
    return covered_steps


def _overall_axial_extent_is_dimensioned(
    part, dwg, prof, *, sibling_profiles=(), tol: float = 0.6
) -> bool:
    """Whether a profile-view dimension witnesses the turned profile's two outer ends."""
    origin = _turned_axis_origin(part, prof)
    for view in _turned_profile_views(prof.axis):
        if view not in dwg.views:
            continue
        horizontal, own_cross = _profile_projection(dwg, view, origin, prof.axis)

        def projected(station: float) -> float:
            point = list(origin)
            point["xyz".index(prof.axis)] = station
            x, y, *_ = dwg.at(view, *point)
            return float(x if horizontal else y)

        lo, hi = projected(min(prof.shoulders)), projected(max(prof.shoulders))

        def sibling_cross(profile) -> float:
            _horizontal, cross = _profile_projection(
                dwg, view, _turned_axis_origin(part, profile), profile.axis
            )
            return cross

        for name, annotation in dwg.annotations_in_view(view):
            if not isinstance(annotation, Dimension):
                continue
            vertices = _dim_vertices(annotation)
            coords = {(x if horizontal else y) for x, y in vertices}
            cross_coords = {(y if horizontal else x) for x, y in vertices}
            owners = _step_length_owners(dwg, name)
            if owners and not any(_feature_on_turned_axis(owner, prof) for owner in owners):
                continue
            if not owners and getattr(prof, "profile", None) is not None and cross_coords:
                dim_cross = sum(cross_coords) / len(cross_coords)
                own_distance = abs(dim_cross - own_cross)
                sibling_distances = [
                    abs(dim_cross - sibling_cross(sibling))
                    for sibling in sibling_profiles
                    if sibling is not prof and sibling.axis == prof.axis
                ]
                if any(distance < own_distance - tol for distance in sibling_distances) or any(
                    abs(distance - own_distance) <= tol for distance in sibling_distances
                ):
                    continue
            if any(abs(value - lo) <= tol for value in coords) and any(
                abs(value - hi) <= tol for value in coords
            ):
                return True
    return False


def _lint_one_axial_profile(
    part,
    dwg,
    prof,
    *,
    assembly,
    recognition,
    sibling_profiles=(),
    profile_label="",
) -> list:
    """Report missing axial coverage for one body-local turned profile.

    A turned part can have every diameter called out yet be unmanufacturable: with
    no shoulder located, the lengths are unknown (the drive-screw gap). A complete
    chain dimensions all ``n`` steps; coverage is counted from placed drawing witnesses or
    placed structured-note provenance joined to the same physical spans, not a build-time
    side channel — so it judges any producer. A shortfall yields one
    ``axial_length_missing`` issue.

    *dwg* is the drawing, duck-typed (needs ``at``/``annotations``/``view_of``).

    Covers **X-, Y-, and Z-axis** turning through the unified IR step-length
    chain (ADR 0008 #223), so a missing chain on any axis is a real gap
    (e.g. the chain skipped for want of page room). Severity mirrors
    :func:`lint_feature_coverage`: ``info`` for an assembly, else ``warning``.
    ``recognition`` supplies the run-owned groove inventory used only to evidence-gate a
    structured ``groove.length`` claim; an unrelated declared groove cannot fill the count.
    """
    n = len(prof.steps)
    covered_steps = _axial_covered_from_drawing(part, dwg, prof, sibling_profiles=sibling_profiles)
    registry = getattr(dwg, "registry", None)
    placed_ids = (
        {identity for name in registry.names() for identity in registry.measurement_of(name)}
        if registry is not None
        else set()
    )
    satisfied_ids = satisfaction_ids(registry)
    # Match structured step-length authority back to the recognition-owned physical band by
    # its axial span. This preserves the denominator and prevents an unrelated declared step
    # from certifying one merely because both share the same role (#1351, ADR 0017).
    axis_index = "xyz".index(prof.axis)
    for identity in satisfied_ids:
        feature = getattr(identity, "feature", None)
        if (
            getattr(identity, "parameter", None) != "step.length"
            or getattr(feature, "kind", None) != "step"
        ):
            continue
        span = getattr(feature, "span", None)
        if span is None or not _feature_on_turned_axis(feature, prof):
            continue
        lo, hi = sorted(float(point[axis_index]) for point in span)
        for index, step in enumerate(prof.steps):
            if abs(lo - float(step.lo)) <= 1e-3 and abs(hi - float(step.hi)) <= 1e-3:
                covered_steps.add(index)
                break
    # A groove band's axial extent is dimensioned by its width callout, not a step length, so
    # detect.py leaves it out of the step-length chain (#606). Count each *rendered* groove-width
    # callout on the turning axis as covering its band — so a fully-dimensioned grooved shaft
    # (N−1 step lengths + the groove width) is not flagged (#628); a *dropped* groove callout
    # leaves its band uncovered, so a genuine gap still fires (reconcile rendered, not intent).
    physical_grooves = {
        (
            groove.axis,
            round(float(groove.width), 3),
            round(float(groove.diameter), 3),
            tuple(round(float(value), 3) for value in groove.at),
        )
        for groove in getattr(recognition, "grooves", ())
    }

    def groove_key(feature):
        return (
            feature.axis,
            round(float(feature.width), 3),
            round(float(feature.diameter), 3),
            tuple(round(float(value), 3) for value in feature.frame.origin),
        )

    def groove_belongs_exactly(feature) -> bool:
        owners = profiles_owning_axial_band(
            (prof, *(sibling for sibling in sibling_profiles if sibling is not prof)),
            axis=feature.axis,
            centre=feature.frame.origin,
            width=feature.width,
        )
        return len(owners) == 1 and owners[0] is prof

    placed_grooves = {
        identity.feature
        for identity in placed_ids
        if getattr(identity, "parameter", None) == "groove.length"
        and _feature_on_turned_axis(identity.feature, prof)
        and groove_belongs_exactly(identity.feature)
        and (recognition is None or groove_key(identity.feature) in physical_grooves)
    }
    satisfied_grooves = {
        identity.feature
        for identity in satisfied_ids
        if getattr(identity, "parameter", None) == "groove.length"
        and getattr(identity.feature, "kind", None) == "groove"
        and identity.feature not in placed_grooves
        and _feature_on_turned_axis(identity.feature, prof)
        and groove_belongs_exactly(identity.feature)
    }
    credited_grooves = placed_grooves | {
        feature for feature in satisfied_grooves if groove_key(feature) in physical_grooves
    }
    for feature in credited_grooves:
        for index, step in enumerate(prof.steps):
            # Match the same physical narrow band detect.py delegates from StepFeature to
            # GrooveFeature. The provider may report its wall OD, so diameter is not a join.
            if groove_owns_turned_step_band(feature, step):
                covered_steps.add(index)
                break
    covered = len(covered_steps)
    if covered >= n:
        return []
    # #955: when placement drops the complete chain, its specific warning already says the
    # shoulders remain unresolved. If the compiler-approved overall fallback survived, the
    # generic "axial length absent" warning would duplicate that diagnosis even though the
    # part's total extent is now stated. The drop is a required half of this condition: an
    # authored drawing that chooses only the overall extent still lacks shoulder locations
    # and must continue to receive ``axial_length_missing``.
    chain_drop_reported = any(
        issue.code == "step_dim_dropped" for issue in getattr(registry, "issues", ())
    )
    if chain_drop_reported and _overall_axial_extent_is_dimensioned(
        part, dwg, prof, sibling_profiles=sibling_profiles
    ):
        return []
    return [
        LintIssue(
            severity="info" if assembly else "warning",
            code="axial_length_missing",
            message=(
                f"turned part has {n} axial steps but only {covered} step length(s) "
                f"dimensioned{profile_label} — shoulders cannot be located"
            ),
        )
    ]


def lint_axial_coverage(
    part,
    dwg,
    assembly=None,
    prof=_UNSET,
    recognition=None,
    *,
    profiles=_UNSET,
) -> list:
    """Report every body-local turned profile whose axial chain is incomplete.

    ``profiles`` is the plural compiler inventory. The compatible ``prof`` input remains for
    callers with a known zero/one profile; supplying both is an error. If neither is supplied,
    the run-owned recognition aggregate is preferred and standalone callers detect the same
    grouped profiles directly. Each profile is judged independently, so one parallel shaft's dimensions cannot
    certify another shaft with equal axial spans (#1357).
    """
    if prof is not _UNSET and profiles is not _UNSET:
        raise ValueError("supply profiles= or the compatible singular prof=, not both")
    if profiles is _UNSET:
        if prof is _UNSET:
            if recognition is not None:
                profiles = recognition.turned_profiles
            else:
                profile_steps: dict[object, list] = {}
                for step in recognise_turned_steps(part):
                    profile_steps.setdefault(step.profile or (step.axis, None), []).append(step)
                detected_profiles = []
                for steps in profile_steps.values():
                    detected = TurnedProfile.from_steps(steps)
                    if detected is not None:
                        detected_profiles.append(detected)
                profiles = tuple(detected_profiles)
        else:
            profiles = () if prof is None else (prof,)
    profiles = tuple(profiles)
    if not profiles:
        return []
    if assembly is None:
        assembly = len(part.solids()) > 1
    plural = len(profiles) > 1
    issues = []
    for profile in profiles:
        key = getattr(profile, "profile", None)
        label = ""
        if plural and key is not None:
            axis_index = "xyz".index(profile.axis)
            line = tuple(
                round(float(value), 3)
                for index, value in enumerate(key.axis_origin)
                if index != axis_index
            )
            span = (
                round(float(min(profile.shoulders)), 3),
                round(float(max(profile.shoulders)), 3),
            )
            label = f" on {profile.axis}-axis line {line}, span {span}"
        issues.extend(
            _lint_one_axial_profile(
                part,
                dwg,
                profile,
                assembly=assembly,
                recognition=recognition,
                sibling_profiles=profiles,
                profile_label=label,
            )
        )
    return issues


def lint_boss_height_coverage(part, dwg, features, assembly=None, omissions=()) -> list:
    """Report modeled boss heights that have no rendered linear dimension (#632).

    Coverage is reconciled from the drawing registry's feature provenance, not a
    renderer side channel: a boss is covered only when one of its live annotations
    is a ``Dimension``. Boss diameter annotations are leaders, so they cannot mask a
    missing axial height. Bosses without a modeled height retain the historical
    diameter-only contract and are outside this check.

    A height the compiler **consolidated** onto an overall extent (#1154 — the boss spans
    the full thickness, so its height and the envelope width measure the same two faces)
    counts as covered, but only once that owner is verifiably on the sheet. The owner is
    read from the omission's ``conveyed_by`` and looked up among the registry's placed
    measurements: consolidating onto a dimension the placer then drops would otherwise turn
    a measurement this check exists to demand into silence.

    The exemption follows ``conveyed_by``, never ``authored``: an authored omission whose set
    keeps the owner carries one, and takes the exemption, because the author chooses which
    dimensions are drawn and not where the geometry states a fact (#964 parity). Only a
    suppression that hands the fact to nobody — a turned-part rule, an authored set that
    drops the owner too — is left to report.
    """
    bosses = [
        feature
        for feature in features
        if getattr(feature, "kind", None) == "boss"
        and getattr(feature, "height", None) is not None
    ]
    registry = getattr(dwg, "registry", None)
    placed = (
        {measurement for name in registry.names() for measurement in registry.measurement_of(name)}
        if registry is not None
        else set()
    )
    satisfied = satisfaction_ids(registry)
    conveyed = {
        omission.feature
        for omission in omissions
        if omission.conveyed_by is not None and omission.conveyed_by in placed
    }
    missing = sum(
        1
        for boss in bosses
        if boss not in conveyed
        and not any(
            identity.feature == boss and identity.parameter == "boss_height.length"
            for identity in satisfied
        )
        and not any(isinstance(ann, Dimension) for ann in dwg.annotations_of(boss).values())
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
