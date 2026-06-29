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

from typing import Literal

from build123d_drafting.helpers import CenterMark, Dimension, TitleBlock

from draftwright._core import _DIAM_RE, _axis_letter, _fmt
from draftwright.linting.issues import LintIssue
from draftwright.recognition import (
    analyse_cylinders,
    feature_diameters,
    find_hole_patterns,
    find_holes,
    find_turned_steps,
)

# A hole/circular feature is dimensioned end-on in the view normal to its axis.
_END_ON = {"x": "side", "y": "front", "z": "plan"}


def _loc_xyz(loc) -> tuple[float, float, float]:
    """A recogniser hole location (Vector or sequence) → an (x, y, z) tuple."""
    if hasattr(loc, "X"):
        return (loc.X, loc.Y, loc.Z)
    x, y, z = loc
    return (float(x), float(y), float(z))


class CoverageState:
    """What the annotation passes covered or dropped, for lint to read."""

    def __init__(self) -> None:
        # Names of bore callouts that document a recognised hole pattern (a
        # grouped ``n× ⌀`` callout), and the holes those placed callouts cover.
        # The hole-table escalation keeps these callouts and tabulates only the
        # holes no placed pattern callout documents (#92).
        self._pattern_callouts: set = set()
        self._patterned_holes: set = set()
        # Diameters dropped by the per-view callout cap, so lint can suppress the
        # redundant feature_not_dimensioned for them. Reset at the top of
        # _auto_annotate so re-annotation does not accumulate.
        self._dropped_callout_diams: list = []

    # -- pattern coverage -----------------------------------------------------

    def cover_pattern(self, callout_name, holes) -> None:
        """Record that placed *callout_name* documents *holes* (a grouped
        pattern callout) — so neither becomes a table row or per-hole balloon."""
        self._pattern_callouts.add(callout_name)
        self._patterned_holes.update(holes)

    def is_pattern_callout(self, name) -> bool:
        """Is *name* a placed pattern (grouped ``n× ⌀``) callout?"""
        return name in self._pattern_callouts

    def is_hole_patterned(self, hole) -> bool:
        """Is *hole* already documented by a placed pattern callout?"""
        return hole in self._patterned_holes

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
    part, annotations, tol: float = 0.15, cyls=None, exclude=None, assembly=None
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
    # Coverage inventory: the *recognised* dimensionable diameters (bores,
    # cbore/spotface steps, bosses) from feature_diameters — built via
    # find_holes/find_bosses, so slot ends and interrupted recesses (partial
    # cylinders that an angle-only test mistakes for full bores) are excluded.
    # Replaces the raw full_cylinders patch list, which over-reported those as
    # undimensioned features (helpers #158/#159).
    inventory = feature_diameters(part, cyls=(z_cyls, cross_cyls))

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
    for h in find_holes(part, cyls=(z_cyls, cross_cyls)):
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


def lint_location_coverage(part, dwg, cyls=None, assembly=None, tol: float = 0.6) -> list:
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
    holes = find_holes(part, cyls=cyls) if cyls is not None else find_holes(part)
    if not holes:
        return []
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    patterned = {id(h) for pat in find_hole_patterns(holes) for h in pat.holes}

    marks: dict[str, list] = {}
    dim_verts: dict[str, list] = {}
    for name, ann in dwg._named.items():
        view = dwg._anno_view.get(name)
        if view is None:
            continue
        if isinstance(ann, CenterMark):
            c = ann.center()
            marks.setdefault(view, []).append((c.X, c.Y))
        elif isinstance(ann, Dimension):
            try:
                pts = [(p.X, p.Y) for p in ann.vertices()]
            except Exception:  # noqa: BLE001 — a dim whose vertices won't evaluate is skipped
                pts = []
            dim_verts.setdefault(view, []).extend(pts)

    bb = part.bounding_box()
    centre = (bb.center().X, bb.center().Y, bb.center().Z)

    no_mark = no_loc = 0
    for h in holes:
        x, y, z = _loc_xyz(h.location)
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
    """How many of an X-turned part's step lengths are dimensioned **in the
    drawing** — a step counts as covered when some front-view ``Dimension`` has
    witnesses at both of its shoulders' page-x positions. Drawing-derived, so it
    judges any producer (not the engine's :class:`CoverageState` side channel)."""
    bb = part.bounding_box()
    y_ref, z_top = bb.center().Y, bb.max.Z
    shoulder_x = {s: dwg.at("front", s, y_ref, z_top)[0] for s in prof.shoulders}
    dim_xsets: list[set[float]] = []
    for name, ann in dwg._named.items():
        if dwg._anno_view.get(name) != "front" or not isinstance(ann, Dimension):
            continue
        try:
            dim_xsets.append({p.X for p in ann.vertices()})
        except Exception:  # noqa: BLE001 — a dim whose vertices won't evaluate is skipped
            pass
    covered = 0
    for step in prof.steps:
        xlo, xhi = shoulder_x[step.lo], shoulder_x[step.hi]
        if any(
            any(abs(x - xlo) <= tol for x in xs) and any(abs(x - xhi) <= tol for x in xs)
            for xs in dim_xsets
        ):
            covered += 1
    return covered


def lint_axial_coverage(part, dwg, assembly=None) -> list:
    """Report a stepped turned part whose axial step lengths are undimensioned.

    A turned part can have every diameter called out yet be unmanufacturable: with
    no shoulder located, the lengths are unknown (the drive-screw gap). A complete
    chain dimensions all ``n`` steps; coverage is counted **from the drawing**
    (:func:`_axial_covered_from_drawing`), not a build-time side channel — so it
    judges any producer. A shortfall yields one ``axial_length_missing`` issue.

    *dwg* is the drawing, duck-typed (needs ``at``/``_named``/``_anno_view``).

    Scoped to **X-axis** turning (a shaft drawn on its side) — the gap the
    step-length pass fills. The other orientations are covered elsewhere, so
    flagging them here would be a false positive: a **Z-axis** (vertical) stepped
    shaft is dimensioned by the orchestrator's existing step-height ladder
    (``dim_step_*``, with its own ``step_dim_dropped`` signal), and a **Y-axis**
    part is drawn end-on (no view shows its length). Severity mirrors
    :func:`lint_feature_coverage`: ``info`` for an assembly, else ``warning``.
    """
    prof = find_turned_steps(part)
    if prof is None or prof.axis != "x":
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
