"""Lint-side build state (#138 / ADR 0005, Step 3).

ADR 0005 §2 assigns the drawing's *coverage signal* a single owner on the lint
side: which features a placed pattern callout already documents, and which
diameters the per-view callout cap dropped. `lint_feature_coverage` consumes this
to avoid double-reporting a hole that a grouped ``n× ⌀`` callout covers (#92) and
to suppress the redundant ``feature_not_dimensioned`` for capped diameters.

`Drawing` delegates here and keeps `_pattern_callouts` / `_patterned_holes` /
`_dropped_callout_diams` reachable as properties during the migration (ADR
0005 §4). This module is the designated home for the lint functions
(`lint_feature_coverage`, `_suggest_fix`, scoring) that move out of
`make_drawing.py` in a later step; for now it owns just the state they read.

It sits at the bottom of the import DAG — it depends on nothing in draftwright.
"""

from __future__ import annotations


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
