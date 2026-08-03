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

import math
import re
from collections.abc import Iterable
from typing import Literal

from build123d_drafting.helpers import CenterMark, Dimension, Leader, TitleBlock

from draftwright._core import _DIAM_RE, _END_ON, HoleRef, _axis_letter, _fmt, _xyz
from draftwright.linting.issues import LintIssue
from draftwright.recognition import (
    TurnedProfile,
    analyse_cylinders,
    feature_diameters,
    recognise_flats,
    recognise_hole_patterns,
    recognise_holes,
    recognise_pockets,
    recognise_rectangular_pads,
    recognise_step_shoulders,
    recognise_turned_steps,
    step_level_zs,
)

_UNSET = object()  # sentinel: distinguishes "not supplied" from a valid prof=None

# An across-flats callout and nothing else (#914): the size, an optional tolerance, then the
# "A/F" token. ANCHORED at both ends — a label is a callout or it is prose, and
# "USE 25 A/F SPANNER" is prose that happens to contain a size (Codex #1011 r4).
#
# The tolerance alternatives ARE `_tol_suffix`'s branches, one for one: nothing, symmetric
# `±n`, asymmetric limits, a fit's deviation pair, a fit's class code. Enumerating them was
# the right answer all along and I reached it three times over: a single token could not read
# the asymmetric limit (r8), so I widened it to any non-letter run, which then accepted
# `25 123 A/F` (r9) and `25 +0.2 $$$ 999 A/F` (r19). Enumeration is only safe because
# `test_the_parser_reads_every_label_the_renderer_can_write` builds its input by CALLING
# `_tol_suffix` for every tolerance kind — so a new branch there fails the test rather than
# silently becoming unreadable here.
#
# NO `n×` quantity prefix: `_flat_label` never writes a count (unlike `_fillet_label`), and
# accepting one let `0× 25 A/F` — a label denying the flat — certify it as defined (r16).
_NUM = r"\d+(?:\.\d+)?"
_TOLERANCE = (
    rf"±{_NUM}"  # a symmetric tolerance
    rf"|\+{_NUM}\s+-{_NUM}"  # asymmetric limits
    rf"|[+-]?{_NUM}/[+-]?{_NUM}"  # a resolved fit shown as deviations
    r"|[A-Za-z]{1,3}\d+"  # a resolved fit shown as its class code (H7, js6)
)
_AF_RE = re.compile(
    rf"^\s*(?:({_NUM})(?:\s+(?:{_TOLERANCE}))?\s*A/F|A/F\s*({_NUM}))\s*$",
    re.IGNORECASE,
)

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

    def drop_diam(self, diam) -> None:
        """Record a diameter dropped by the per-view callout cap."""
        self._dropped_callout_diams.append(diam)

    @property
    def dropped_diams(self) -> list:
        """Diameters dropped by the cap (passed to lint_feature_coverage)."""
        return self._dropped_callout_diams

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
        )

    def restore(self, snap: tuple) -> None:
        """Restore the collections captured by :meth:`snapshot`."""
        pc, ph, shd, dropped = snap
        self._pattern_callouts = set(pc)
        self._patterned_holes = set(ph)
        self._scattered_hole_docs = set(shd)
        self._dropped_callout_diams = list(dropped)


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
    fillets are ignored) and diffs it against every ø value mentioned in the
    annotations' labels, plus the structured ``covers_diameters`` metadata on
    annotations that draw their values geometrically (e.g. ``HoleCallout``).
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
        label = getattr(ann, "label", None) or ""
        for m in _DIAM_RE.finditer(label):
            mentioned.add(float(m.group(1)))
            text_mentioned.add(float(m.group(1)))
        count = getattr(ann, "covers_count", 1)
        for v in getattr(ann, "covers_diameters", ()):
            mentioned.add(float(v))
            provided[float(v)] = provided.get(float(v), 0) + count

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
    part, dwg, cyls=None, assembly=None, tol: float = 0.6, holes=None, patterns=None
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
    if not holes:
        return []
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    if patterns is None:
        patterns = recognise_hole_patterns(holes)
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
    step_zs=None,
) -> list:
    """Report undefined raised-pad footprints and blind-pocket locations.

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
    source_shoulders = recognise_step_shoulders(
        part, levels=step_level_zs(part) if step_zs is None else step_zs
    )
    model_shoulders = {
        (axis, round(pos, 3))
        for f in features
        if getattr(f, "kind", None) == "step_level"
        for axis, pos in getattr(f, "shoulders", ())
    }
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


def _stock_key(axis: str, axis_at, stock):
    """Which piece of stock, as one value — the axis LINE plus its EXTENT.

    Two independent parts, because neither alone is enough: parallel lobes share an axial
    span and differ only in line; disjoint coaxial regions share a line and differ only in
    span; one double-D matches on both. `axis` alone is a letter and separates neither.

    ONE function, called by both the flat grouping and the stock-radius lookup. They are the
    same question, and when only the grouping was corrected to use the extent (r21) the radius
    lookup kept keying on `axis_at` alone — so two coaxial regions of different diameters
    shared whichever radius was read last, and a correct callout on the larger one was
    reported missing (Codex #1011 r22). A shared identity cannot drift out of step; two
    spellings of it did.
    """
    idx = "xyz".index(axis)
    line = tuple(round(axis_at[i], 3) for i in range(3) if i != idx)
    return (axis, line, stock)


def _face_chord(flat, radius):
    """The flat face's two extreme points in part space — its chord across the stock.

    A flat face is a RECTANGLE, and end-on it projects to a chord: a leader may legitimately
    target any point on it. Treating the face as the single point ``Flat.at`` made a declared
    flat with an explicit off-centre ``at=`` — the documented meaning of that parameter, "the
    leader point" — read as undimensioned (Codex #1011 r18).

    *radius* is the stock's, from the cylinder inventory. Returns ``(at, at)`` when it is
    unknown or the geometry degenerates: the old point behaviour, erring toward the tighter
    test rather than inventing extent.
    """
    idx = "xyz".index(flat.axis)
    plane = [i for i in range(3) if i != idx]
    off = [flat.at[i] - flat.axis_at[i] for i in plane]
    span = math.hypot(*off)
    half = math.sqrt(max(radius * radius - span * span, 0.0)) if radius else 0.0
    if not half or not span:
        return (flat.at, flat.at)
    tangent = (-off[1] / span, off[0] / span)  # along the face, perpendicular to axis→face
    ends = []
    for sign in (-1.0, 1.0):
        point = list(flat.at)
        point[plane[0]] += sign * half * tangent[0]
        point[plane[1]] += sign * half * tangent[1]
        ends.append(tuple(point))
    return tuple(ends)


def _point_to_segment(point, a, b) -> float:
    """Distance from *point* to segment *a*-*b* in the page plane."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    if not length:
        return math.dist(point, a)
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length))
    return math.dist(point, (a[0] + t * dx, a[1] + t * dy))


def lint_flat_coverage(
    part, dwg, *, cyls=None, flats=None, assembly=None, tol: float = 0.15, pos_tol: float = 1.0
):
    """Report a recognised machined flat with no across-flats callout on the sheet (#914).

    A flat truncating round stock has exactly ONE size parameter — its A/F. Every other
    dimension on such a drawing describes the *stock*, so when the callout goes the
    feature's whole definition goes with it and the part cannot be made.

    That is why this exists alongside ``flat_dropped`` rather than instead of it. The
    drop signal reports a *placement* outcome ("no clear room") from inside the leader
    pass; nothing in it tells a reader that the drawing is no longer manufacturable, and
    it says nothing at all when the callout is lost by some other route. Both are emitted
    deliberately: one names the cause, the other the consequence (#914).

    Drawing-derived, like :func:`lint_axial_coverage`: the inventory comes from geometry
    (``recognise_flats``) and the coverage from what is actually on the sheet, so it judges
    any producer rather than trusting a build-time side channel.

    A flat is covered by a **leader tipped at that flat's own page position** whose whole
    label is an across-flats callout — ``25 A/F``, ``25 ±0.2 A/F``, ``A/F 25``.
    Three separate conditions, each earned:

    - *A leader*, because a size defines a feature only when something points at it. Text
      alone let the drawing title (r1) and any free-form note mentioning the size (r4)
      satisfy a flat; ``Note`` and ``TitleBlock`` are not leaders.
    - *Tipped at the flat*, because association by VALUE is not association at all. Matching
      labels to flats through a shared pool of numbers went wrong three times — one label
      covering two flats (r2), a greedy pairing that missed a valid one (r3), and two leaders
      on the *same* flat silencing a second, undefined one (r6). The tip is the association:
      ``render_flats`` leads from ``FlatFeature.frame.origin``, which IS the recogniser's
      ``Flat.at``, so the engine's own callouts land exactly here — and an authored leader
      that points at the flat is judged the same way, which pooling could never manage.
    - *An A/F callout*, anchored, because a label is a callout or it is prose.

    A leader at the flat stating a size the geometry does not corroborate is a different
    defect — a wrong dimension rather than a missing one — and gets its own
    ``flat_callout_mismatched`` code naming both numbers. Ground truth stays the geometry
    (ADR 0015: coverage deliberately does not trust the part model, because a stale or
    mistaken declaration is exactly what it must catch).

    Flats sharing an axis and size are ONE callout in ``render_flats``, so the inventory is
    grouped the same way: a double-D's two faces are one definition, satisfied by a leader at
    either face.

    Groups are keyed by the stock's **axis line** (``Flat.axis_at``) *and* its **extent**
    (``Flat.stock``), not the axis letter: parallel lobes differ in line, disjoint coaxial
    regions differ in extent, and a double-D's two faces match on both. Not by copying ``render_flats``'s ``(axis, across)`` collapse: a check that
    groups the way the renderer groups cannot see the renderer group wrongly, which is the
    whole reason coverage re-reads geometry (ADR 0015). The renderer still collapses, so a
    two-lobe part reports its undefined lobe here until #1013 fixes that end too.

    *cyls* accepts a precomputed ``analyse_cylinders(part)`` result, and *flats* a
    precomputed inventory, so repeated lint runs need not re-scan the solid. *pos_tol* is the
    page-mm window for "this leader points at that flat"; within it, each callout is assigned
    to the single NEAREST group, so the window never has to shrink with the drawing scale.
    """
    inventory = recognise_flats(part, cyls=cyls) if flats is None else flats
    if not inventory:
        return []
    if assembly is None:
        assembly = len(part.solids()) > 1
    if cyls is None:
        cyls = analyse_cylinders(part)
    # Stock radius per PIECE OF STOCK, so a face's chord extent is known — keyed by the same
    # `_stock_key` the grouping uses, since "which stock" is one question with one answer.
    radii = {
        _stock_key(
            c["axis"],
            c["axis_xyz"],
            (c.get("solid_idx", 0), round(c["s_lo"], 3), round(c["s_hi"], 3)),
        ): c["diameter"] / 2
        for c in (*cyls[0], *cyls[1])
        if c.get("external")
    }

    groups: dict = {}
    for flat in inventory:
        groups.setdefault(
            (_stock_key(flat.axis, flat.axis_at, flat.stock), round(flat.across, 3)), []
        ).append(flat)

    # Callouts are read PER VIEW, from the view the flat reads in. Page coordinates alone are
    # not identity: a front-view leader whose tip happens to land on a Z-flat's projected plan
    # position would otherwise define it, while pointing at unrelated geometry (Codex #1011
    # r12). `view_of`/`annotations_in_view` is the same duck-typed drawing surface
    # `lint_axial_coverage` already relies on.
    claimed: dict = {}
    by_view: dict = {}
    for key in groups:
        by_view.setdefault(_END_ON[key[0][0]], []).append(key)

    for view, keys in by_view.items():
        callouts = []
        for _name, ann in dwg.annotations_in_view(view):
            if not isinstance(ann, Leader):
                continue
            found = _AF_RE.match(getattr(ann, "label", None) or "")
            tip = getattr(ann, "tip", None)
            if found is not None and tip is not None:
                callouts.append(((tip[0], tip[1]), float(found.group(1) or found.group(2))))
        if not callouts:
            continue
        ordered_keys = sorted(keys, key=str)  # deterministic, so an exact tie resolves alike
        try:
            projected = {
                k: [
                    tuple(
                        dwg.at(view, *end)[:2]
                        for end in _face_chord(
                            flat, radii.get(_stock_key(flat.axis, flat.axis_at, flat.stock))
                        )
                    )
                    for flat in groups[k]
                ]
                for k in ordered_keys
            }
        except KeyError:
            # `Drawing.drop_view_coordinates` (deprecated, but public until 0.5.0) and the
            # internal view bailout both leave a view holding annotations while `at` can no
            # longer place a point in it. Leaving those groups unclaimed reports them as
            # undimensioned, which is true — a view the drawing cannot map cannot carry a
            # definition — whereas letting the KeyError out made `lint()` itself fail, and a
            # lint that raises is worse than one that is wrong (Codex #1011 r17).
            continue
        # Each callout is assigned to the ONE group it is nearest, not to every group within
        # `pos_tol`. A leader points at a single feature, and accepting it for all nearby
        # groups made the window an association: at 1:100 two lobes 100 mm apart project 1 mm
        # apart, so one leader sat inside both windows and certified the undefined lobe too
        # (Codex #1011 r10). Nearest-wins needs no scale-dependent tolerance.
        for (tx, ty), value in callouts:
            best = None
            for key in ordered_keys:
                for end_a, end_b in projected[key]:
                    gap = _point_to_segment((tx, ty), end_a, end_b)
                    if gap > pos_tol:
                        continue
                    # Distance decides; the stated size only breaks an exact POSITIONAL tie.
                    # The end-on view discards position along the stock axis, so two sections
                    # of one shaft at different stations project to the same point — a
                    # correct sheet carrying `18 A/F` and `28 A/F` there gave both leaders to
                    # the 18 group, reporting a mismatch and a gap on a drawing that had
                    # neither (Codex #1011 r13). Value ranks second, never first: position is
                    # still the association, which is the r6 lesson.
                    rank = (round(gap, 6), 0 if abs(value - key[1]) <= tol else 1)
                    if best is None or rank < best[0]:
                        best = (rank, key)
            if best is not None:
                claimed.setdefault(best[1], []).append(value)

    ordered = sorted(groups, key=str)
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    issues = []
    for key in ordered:
        (axis, _line, _stock), across = key
        stated = claimed.get(key, [])
        # EVERY disagreeing callout, not just the first, and not only when none agrees. A
        # right answer beside a wrong one does not make the wrong one right: two leaders on
        # one flat reading `25 A/F` and `12 A/F` are a contradiction a reader has to resolve
        # at the bench, and nothing else on the sheet checks leader text — `label_vs_measured`
        # reads Dimensions (Codex #1011 r11).
        issues += [
            LintIssue(
                severity=severity,
                code="flat_callout_mismatched",
                message=(
                    f"the {axis.upper()} stock's flat measures {_fmt(across)} A/F but the "
                    f"sheet calls it out as {_fmt(value)} A/F"
                ),
            )
            for value in stated
            if abs(value - across) > tol
        ]
        if not stated:
            issues.append(
                LintIssue(
                    severity=severity,
                    code="flat_not_dimensioned",
                    message=(
                        f"machined flat {_fmt(across)} A/F on the {axis.upper()} stock has no "
                        f"across-flats callout on the sheet — the flat's only size definition"
                    ),
                )
            )
    return issues


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
        if getattr(feature, "kind", None) == "boss"
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


def lint_declaration_reconciliation(features, cyls) -> list:
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
