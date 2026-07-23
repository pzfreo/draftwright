"""suggest — ready-to-paste fix snippets for lint issues (#29).

Split out of the coverage module (ADR 0007). Maps a :class:`LintIssue` to a
hint a caller or LLM can paste and fill in via the public domain API.
"""

from __future__ import annotations

import re

from draftwright._core import _DIAM_RE, _QUOTED_RE, _fmt
from draftwright.linting.issues import LintIssue  # noqa: F401 — re-exported for callers

# Tolerance for matching a lint message's reported diameter (dedup representative
# at tol 0.15, formatted to 1 dp) back to a raw feature diameter when generating a
# fix snippet (#29).
_DIAM_MATCH_TOL = 0.2


def _suggest_fix(issue, dwg) -> str | None:
    """Return a ready-to-paste code snippet that addresses *issue*, or None.

    The snippet is a hint, not necessarily runnable verbatim (``...`` stands in
    for args the engine cannot infer). It prefers the semantic edit API
    (:meth:`Drawing.model`, :meth:`Drawing.dimension`, :meth:`Drawing.locate`)
    and mentions raw coordinate helpers only as fallback escape hatches (#29).
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
                return (
                    f"# ø{_fmt(d)} has no callout. Find the feature in the model IR and let the\n"
                    f"# engine place its callout (say WHAT, not WHERE):\n"
                    f"for f in dwg.model().features:\n"
                    f"    if getattr(f, 'diameter', None) and abs(f.diameter - {_fmt(d)}) < {_DIAM_MATCH_TOL}:\n"
                    f"        dwg.callout(f)  # feature-backed ø callout, auto-placed"
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
            f"# Prefer a feature-backed edit so the shared layout solve can place it:\n"
            f'dwg.remove("{first}")  # if it was named\n'
            f'# dwg.dimension(feature, "length", role="width", side="below", pin=True, '
            f'name="{first}")\n'
            f"# Fallback only when you truly have raw page-coordinate endpoints:\n"
            f'# dwg.place_dim(p1, p2, "below", "plan", dwg.draft, name="{first}")'
        )

    if code == "dim_inside_part":
        # Message: "Dim 'X': annotation bbox overlaps part outline by ...".
        labels = _QUOTED_RE.findall(issue.message)
        first = labels[0] if labels else "<dim>"
        return (
            f"# The dim sits inside the view — its offset is on the wrong side. "
            f"Prefer a feature-backed edit on the opposite side:\n"
            f'dwg.remove("{first}")  # if it was named\n'
            f'# dwg.dimension(feature, "length", role="height", side="right", pin=True, '
            f'name="{first}")\n'
            f"# Fallback only when you truly have raw page-coordinate endpoints:\n"
            f'# dwg.place_dim(p1, p2, "right", "front", dwg.draft, name="{first}")'
        )

    if code == "step_dim_dropped":
        # Steps too closely spaced to dimension at sheet scale (#41/#42).
        return (
            "# Re-build with an enlarged detail view so the crowded shoulders are "
            "dimensionable:\n"
            "dwg = build_drawing(part, detail_view=True)"
        )

    if code == "plate_thickness_dropped":
        # A recognised plate/wall thickness had no room in its target strip (#559).
        return (
            "# The plate thickness strip is full; free room by moving another dim,\n"
            "# or author the thickness explicitly on a clear side:\n"
            '# dwg.dimension(feature, "length", role="thickness", side="left", pin=True)'
        )

    if code == "chamfer_dropped":
        # A recognised chamfer callout had no clear room for its leader (#560).
        return (
            "# The chamfer leader found no clear margin; free room by relaxing a nearby\n"
            "# dim, or re-build with an enlarged detail view:\n"
            "dwg = build_drawing(part, detail_view=True)"
        )

    if code == "step_position_dropped":
        # A recognised step/shoulder position had no room in its target strip (#555).
        # The whole shoulder set rebuilds together (render_step_positions), so free strip
        # room or use an enlarged detail view rather than authoring one shoulder by hand.
        return (
            "# The step-position strip is full; free room by relaxing a crowding dim,\n"
            "# or re-build with an enlarged detail view:\n"
            "dwg = build_drawing(part, detail_view=True)"
        )

    return None
