"""suggest — ready-to-paste fix snippets for lint issues (#29).

Split out of the coverage module (ADR 3 (was 0007)). Maps a :class:`LintIssue` to a
hint a caller or LLM can paste and fill in via the public domain API.
"""

from __future__ import annotations

from draftwright._core import _QUOTED_RE
from draftwright.linting.issues import LintIssue  # noqa: F401 — re-exported for callers


def _suggest_fix(issue, dwg) -> str | None:
    """Return a ready-to-paste code snippet that addresses *issue*, or None.

    The snippet is a hint, not necessarily runnable verbatim (``...`` stands in
    for args the engine cannot infer). It prefers the semantic edit API
    (:meth:`Drawing.model`, :meth:`Drawing.dimension`, :meth:`Drawing.locate`)
    and mentions raw coordinate helpers only as fallback escape hatches (#29).
    """
    code = issue.code

    if code in {"feature_not_dimensioned", "feature_count_mismatch"}:
        return (
            "# Diameter totals do not identify drilling operations. Inspect the separate\n"
            "# hole_requirement_* findings and their feature provenance before editing.\n"
            "# Keep distinct axes, through/blind depths, threads and fits separate;\n"
            "# do not increase a callout count to cover a different operation."
        )

    if code == "hole_requirement_missing":
        identities = issue.hole_requirement_ids
        model = dwg.model()
        if (
            not identities
            or any(parameter != "bore.diameter" for _feature, parameter in identities)
            or model is None
            or model.authored_dimensions is not None
        ):
            return (
                "# Inspect this requirement on its declared feature. Preserve authored\n"
                "# omissions and correct any conflicting existing callout before rebuilding;\n"
                "# a missing count is not permission to add a larger or duplicate callout."
            )
        indices = []
        for feature, _parameter in identities:
            index = next(
                (i for i, current in enumerate(model.features) if current is feature), None
            )
            if index is None or getattr(feature, "kind", None) not in {"hole", "pattern"}:
                return None
            # Existing diameter ink could carry contradictory quantity evidence. Adding
            # another callout is not a repair for that claim.
            if any(
                getattr(identity, "feature", None) is feature
                and getattr(identity, "parameter", None) == "bore.diameter"
                for name in dwg.registry.names()
                for identity in dwg.registry.measurement_of(name)
            ):
                return None
            if index not in indices:
                indices.append(index)
        return (
            "# These exact features belong to this Drawing; re-run lint after rebuilding.\n"
            "# Restore their own callouts through the shared placement solver.\n"
            "with dwg.deferred():\n"
            + "\n".join(f"    dwg.callout(dwg.model().features[{i}])" for i in indices)
        )

    if code == "hole_requirement_unverifiable":
        return (
            "# This recognised operation has no verified measurement ownership. Inspect\n"
            "# its members and declare the separate hole/group in Sheet with its own\n"
            "# axis, through/blind depth and known thread/fit intent, then rebuild.\n"
            "# Do not change another operation's count or infer manufacturing intent."
        )

    if code == "annotation_ink_overlap":
        return (
            "# Try bounded shared-solver repair for axis-aligned dimension ink.\n"
            "# Pins and measurements are preserved; infeasible crossings remain visible.\n"
            "dwg.repair()\n"
            "issues = dwg.lint()"
        )

    if code == "annotation_overlap":
        # Message: "labels 'A' and 'B' overlap by ...".
        if "move what is drawn" in issue.message:
            # #1321/#1332: this pair ALSO draws line-work through one of the
            # labels, and the message says so. Re-placing the text is what that
            # message tells the reader will not fix it, so offering a snippet
            # that does exactly that is worse than offering none — the snippet is
            # the surface a caller pastes.
            return None
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
        # This code covers two different measurement families. Prismatic
        # height ladders have automatic detail recovery; turned axial chains
        # need an authored profile detail. Use provenance, not message wording.
        kinds = {identity.feature.kind for identity in issue.measurement_ids}
        if kinds == {"step_level"}:
            return (
                "# Recover crowded prismatic heights in an enlarged detail view:\n"
                "dwg = build_drawing(part, detail_view=True)"
            )
        if kinds != {"step"}:
            return None
        return (
            "# In the declared Sheet script, target the affected step handle with an\n"
            "# authored profile detail. Its scale must fit the available sheet space:\n"
            'sheet.detail_view("A", around=shoulder).scale(3)\n'
            "dwg = sheet.build()"
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
