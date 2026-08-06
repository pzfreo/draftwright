"""Semantic completeness for through profiled-bore requirements (#1061).

Physical cardinality comes from the run-owned recognition result. Coverage comes from
structured metadata on placed hole leaders, never from IR presence, emitted glyph geometry,
annotation names, or label text.
"""

from __future__ import annotations

from collections import Counter
from math import hypot, isfinite
from typing import Literal

from draftwright._geometry import _fmt
from draftwright.linting.issues import LintIssue
from draftwright.recognition import RecognitionResult


def _axis_letter(value) -> str:
    if isinstance(value, str):
        return value.lower()
    direction = tuple(float(component) for component in value)
    return "xyz"[max(range(3), key=lambda i: abs(direction[i]))]


def _canonical_direction(value) -> tuple[float, float, float]:
    direction = tuple(float(component) for component in value)
    if len(direction) != 3:
        raise ValueError("profile coverage direction must be a finite non-zero 3-vector")
    norm = hypot(*direction)
    if not isfinite(norm) or norm <= 1e-12:
        raise ValueError("profile coverage direction must be a finite non-zero 3-vector")
    direction = tuple(component / norm for component in direction)
    first = next(component for component in direction if abs(component) > 1e-12)
    if first < 0:
        direction = tuple(-component for component in direction)
    clean = tuple(
        0.0 if abs(component) <= 1e-12 else round(component, 6) for component in direction
    )
    return (clean[0], clean[1], clean[2])


def profiled_bore_key(profile, axis, through, major, across, direction) -> tuple:
    """Canonical physical/callout identity for one double-D specification."""
    return (
        str(profile),
        _axis_letter(axis),
        bool(through),
        round(float(major), 3),
        round(float(across), 3),
        _canonical_direction(direction),
    )


def lint_profiled_bore_coverage(
    part,
    annotations,
    *,
    recognition: RecognitionResult | None,
    dropped_profiles=(),
    assembly=None,
) -> list[LintIssue]:
    """Report physical double-D requirements not documented by placed callouts.

    A diameter-only callout is insufficient: ``covers_profiles`` exists only when the
    compound callout includes the approved A/F value. Dropped callouts are already reported
    as ``callout_dropped`` and are counted here only to avoid reporting one defect twice.
    """
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "lint_profiled_bore_coverage() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    required = Counter(
        profiled_bore_key(
            "double_d",
            bore.axis,
            bore.through,
            bore.major_diameter,
            bore.across_flats,
            bore.flat_direction,
        )
        for bore in recognition.double_d_bores
    )
    if not required:
        return []

    provided: Counter = Counter()
    for annotation in annotations:
        count = int(getattr(annotation, "covers_count", 1) or 1)
        for profile, axis, through, major, across, direction in getattr(
            annotation, "covers_profiles", ()
        ):
            provided[profiled_bore_key(profile, axis, through, major, across, direction)] += count

    dropped = Counter(
        profiled_bore_key(profile, axis, through, major, across, direction)
        for profile, axis, through, major, across, direction in dropped_profiles
    )
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    issues: list[LintIssue] = []
    for key, need in sorted(required.items()):
        _profile, axis, _through, major, across, direction = key
        have = provided[key]
        if have + dropped[key] >= need:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code="profiled_bore_not_dimensioned",
                message=(
                    f"{need} through double-D bore(s) require {_fmt(major)} major diameter "
                    f"and {_fmt(across)} A/F about {axis.upper()} with flat direction "
                    f"{direction}, but placed callouts document {have}"
                ),
            )
        )
    return issues
