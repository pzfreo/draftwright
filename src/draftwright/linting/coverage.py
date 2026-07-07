"""coverage — feature-coverage completeness check and its state (#138 / ADR 0005).

Part of the :mod:`draftwright.linting` package (ADR 0007):

- `lint_feature_coverage` — the completeness check that reports part diameters
  with no callout (#80), avoiding double-reporting a hole a grouped ``n× ⌀``
  callout covers (#92) and suppressing the redundant ``feature_not_dimensioned``
  for capped diameters.
- `CoverageState` — the coverage signal the passes record and the checks read
  (pattern callouts, patterned holes, dropped callout diameters). `Drawing`
  delegates to it and keeps `_pattern_callouts` / `_patterned_holes` /
  `_dropped_callout_diams` reachable as properties during the migration (§4).

(`_suggest_fix` now lives in :mod:`.suggest`; `lint_drawing` in
:mod:`.structural`.) Depends only on `_core` + recognition + the rendering
``TitleBlock``; never on `make_drawing`/`annotate`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

from build123d_drafting.helpers import CenterMark, Dimension, TitleBlock

from draftwright._core import _DIAM_RE, _END_ON, HoleRef, _axis_letter, _fmt, _xyz
from draftwright.linting.issues import LintIssue
from draftwright.recognition import (
    analyse_cylinders,
    feature_diameters,
    find_hole_patterns,
    find_holes,
    find_turned_steps,
)

_UNSET = object()  # sentinel: distinguishes "not supplied" from a valid prof=None

# Reconciliation tolerances (#487) mirror sheet_dsl._match_object (⌀ ≤ 0.2 mm, in-plane ≤ 0.5 mm):
# a declared feature matches a recognised cylinder within these. Kept in sync by comment — linting/
# sits below sheet_dsl in the DAG, so the literals cannot be shared by import.
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

    Counts are checked too (#92): the part's holes (via ``find_holes``) give
    a required count per diameter (each bore, counterbore, and spotface
    occurrence counts one), and structured callouts declare how many holes
    they dimension (``covers_count`` — the ``n×`` prefix). A shortfall
    yields a ``feature_count_mismatch`` warning. A diameter covered by any
    free-text ø-label is exempt from the count check — text labels carry no
    count semantics. Location coverage remains out of scope (#93).
    """
    z_cyls, cross_cyls = cyls if cyls is not None else analyse_cylinders(part)
    if holes is None:
        holes = find_holes(part, cyls=(z_cyls, cross_cyls))
    # Coverage inventory: the *recognised* dimensionable diameters (bores,
    # cbore/spotface steps, bosses) from feature_diameters — built via
    # find_holes/find_bosses, so slot ends and interrupted recesses (partial
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
    a ``_named`` mapping, and ``_anno_view``. For each hole, project its centre into
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
        holes = find_holes(part, cyls=cyls) if cyls is not None else find_holes(part)
    if not holes:
        return []
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    if patterns is None:
        patterns = find_hole_patterns(holes)
    patterned = {id(h) for pat in patterns for h in pat.holes}

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
            id(h) not in patterned
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


def _axial_covered_from_drawing(part, dwg, prof, tol: float = 0.6) -> int:
    """How many of a turned part's step lengths are dimensioned **in the drawing**
    — a step counts as covered when some front-view ``Dimension`` has witnesses at
    both of its shoulders' page positions. Drawing-derived, so it judges any
    producer (not the engine's :class:`CoverageState` side channel).

    Works for both turning axes (orientation is data): an X-turned shaft's chain is
    horizontal, so shoulders separate along page-x; a Z-turned shaft's chain is
    vertical, so they separate along page-y. We match along whichever the chain
    runs (page-x for X, page-y for Z)."""
    bb = part.bounding_box()
    c = bb.center()
    idx = "xyz".index(prof.axis)
    base = [c.X, c.Y, c.Z]
    use_x = prof.axis == "x"  # horizontal chain → match page-x; else vertical → page-y

    def shoulder_coord(view: str, s: float) -> float:
        pt = list(base)
        pt[idx] = s
        px, py, *_ = dwg.at(view, *pt)
        return float(px if use_x else py)

    # A crowded X-turned head is dimensioned in an enlarged detail view (#304/#307),
    # not the front chain — so a shoulder counts as located when matched in EITHER the
    # front view or any detail view.
    views = ["front"] + sorted(v for v in dwg.views if v.startswith("detail_"))
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
            clo, chi = shoulder_c[step.lo], shoulder_c[step.hi]
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

    *dwg* is the drawing, duck-typed (needs ``at``/``_named``/``_anno_view``).

    Covers **X- and Z-axis** turning: both are now located by the unified IR
    step-length chain (ADR 0008 #223), so a missing chain on either is a real gap
    (e.g. the chain skipped for want of page room). Only **Y-axis** turning is
    excluded — it is drawn end-on, so no view shows its length. Severity mirrors
    :func:`lint_feature_coverage`: ``info`` for an assembly, else ``warning``.
    *prof* may be supplied (the single inventory, #244) to skip re-detection;
    omitted, it is detected here. A sentinel distinguishes "not supplied" from a
    valid ``prof=None`` (non-turned part).
    """
    if prof is _UNSET:
        prof = find_turned_steps(part)
    if prof is None or prof.axis == "y":
        return []
    if assembly is None:
        assembly = len(part.solids()) > 1
    n = len(prof.steps)
    covered = _axial_covered_from_drawing(part, dwg, prof)
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
    ⌀ within ``_RECON_DIA_TOL`` and in-plane position within ``_RECON_POS_TOL`` — the same test
    ``sheet_dsl._match_object`` uses. Non-fatal: every issue is a ``warning``.
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
