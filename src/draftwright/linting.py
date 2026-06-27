"""The lint module (#138 / ADR 0005).

Holds the lint-side logic and state:

- `lint_feature_coverage` — the completeness check that reports part diameters
  with no callout (#80), avoiding double-reporting a hole a grouped ``n× ⌀``
  callout covers (#92) and suppressing the redundant ``feature_not_dimensioned``
  for capped diameters.
- `_suggest_fix` — the per-issue ready-to-paste fix snippet (#29).
- `CoverageState` — the coverage signal the passes record and the checks read
  (pattern callouts, patterned holes, dropped callout diameters). `Drawing`
  delegates to it and keeps `_pattern_callouts` / `_patterned_holes` /
  `_dropped_callout_diams` reachable as properties during the migration (§4).

It sits low in the import DAG — it depends only on `_core` (and build123d_drafting),
never on `make_drawing`/`annotate`. `Drawing.lint()` calls these via re-imports.
"""

from __future__ import annotations

import re

from build123d_drafting.features import (
    analyse_cylinders,
    feature_diameters,
    find_holes,
)
from build123d_drafting.helpers import LintIssue, TitleBlock

from draftwright._core import _DIAM_RE, _QUOTED_RE, _fmt


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
    coverage_severity = "info" if assembly else "warning"

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


# Tolerance for matching a lint message's reported diameter (dedup representative
# at tol 0.15, formatted to 1 dp) back to a raw feature diameter when generating a
# fix snippet (#29).
_DIAM_MATCH_TOL = 0.2


def _suggest_fix(issue, dwg) -> str | None:
    """Return a ready-to-paste code snippet that addresses *issue*, or None.

    The snippet is a hint, not necessarily runnable verbatim (``...`` stands in
    for args the engine cannot infer). It uses the public domain API
    (:meth:`Drawing.features`, :meth:`Drawing.at`, :meth:`Drawing.place_dim`)
    so a caller or LLM can paste and fill the gaps trivially (#29).
    """
    code = issue.code

    if code == "feature_not_dimensioned":
        # Message: "cylindrical feature ø8 has no diameter callout on the sheet".
        m = _DIAM_RE.search(issue.message)
        if m is None:
            return None
        d = float(m.group(1))
        # The reported diameter is the dedup representative (tol 0.15) formatted
        # to 1 dp, so match raw feature diameters with that combined slack — a
        # 1e-6 match would silently miss every non-integer bore.
        for view in ("plan", "front", "side"):
            if any(abs(f.diameter - d) < _DIAM_MATCH_TOL for f in dwg.features(view)):
                tag = _fmt(d).replace(".", "_")
                return (
                    f"# ø{_fmt(d)} has no callout. Locate it via features() and add a leader:\n"
                    f'for f in dwg.features("{view}"):\n'
                    f"    if abs(f.diameter - {_fmt(d)}) < {_DIAM_MATCH_TOL}:\n"
                    f"        callout = HoleCallout(f.diameter, count=f.count,\n"
                    f"                              through=f.through, depth=f.depth, draft=dwg.draft)\n"
                    f"        elbow = (f.page_pos[0] + 15, f.page_pos[1] + 10, 0)\n"
                    f'        leader = Leader((*f.page_pos, 0), elbow, "", dwg.draft, callout=callout)\n'
                    f'        dwg.add(leader, name="hole_{tag}")'
                )
        return None

    if code == "feature_count_mismatch":
        # Message: "4 ø8 features on the part but callouts account for 1".
        # `need` is the leading count; anchor it so diameter digits never
        # interfere regardless of message word order.
        m = _DIAM_RE.search(issue.message)
        need_m = re.match(r"\s*(\d+)", issue.message)
        if m is None or need_m is None:
            return None
        need = need_m.group(1)
        return (
            f"# Only some ø{m.group(1)} holes are counted. Set count={need} on the "
            f"callout so it covers them all:\n"
            f"# HoleCallout(..., count={need}, draft=dwg.draft)"
        )

    if code == "annotation_overlap":
        # Message: "labels 'A' and 'B' overlap by ...".
        labels = _QUOTED_RE.findall(issue.message)
        first = labels[0] if labels else "<dim>"
        return (
            f"# Re-add the dimension with place_dim so it auto-stacks in the "
            f"layout strip instead of overlapping:\n"
            f'dwg.remove("{first}")  # if it was named\n'
            f'dwg.place_dim(p1, p2, "below", "plan", dwg.draft, name="{first}")'
        )

    if code == "dim_inside_part":
        # Message: "Dim 'X': annotation bbox overlaps part outline by ...".
        labels = _QUOTED_RE.findall(issue.message)
        first = labels[0] if labels else "<dim>"
        return (
            f"# The dim sits inside the view — its offset is on the wrong side. "
            f"Re-place it on the opposite side via place_dim (auto-stacks clear "
            f"of the part):\n"
            f'dwg.remove("{first}")  # if it was named\n'
            f'dwg.place_dim(p1, p2, "right", "front", dwg.draft, name="{first}")'
        )

    if code == "step_dim_dropped":
        # Steps too closely spaced to dimension at sheet scale (#41/#42).
        return (
            "# Re-build with an enlarged detail view so the crowded shoulders are "
            "dimensionable:\n"
            "dwg = build_drawing(part, detail_view=True)"
        )

    return None
