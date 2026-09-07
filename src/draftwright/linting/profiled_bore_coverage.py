"""Semantic completeness for through profiled-bore requirements (#1061).

Physical cardinality comes from the run-owned recognition result. Coverage comes from
structured metadata on placed hole leaders, never from IR presence, emitted glyph geometry,
annotation names, or label text.
"""

from __future__ import annotations

from collections import Counter
from math import hypot, isfinite
from typing import Literal

from quiddity import RecognitionResult

from draftwright._geometry import _fmt, _is_principal_axis
from draftwright.linting._registry import annotation_owner, satisfaction_ids
from draftwright.linting.issues import LintIssue

_SITE_TOL = 0.5


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


def profiled_bore_key(profile, axis, through, major, across, direction, *, precision=3) -> tuple:
    """Canonical physical/callout identity for one double-D specification."""
    return (
        str(profile),
        _axis_letter(axis),
        bool(through),
        round(float(major), precision),
        round(float(across), precision),
        _canonical_direction(direction),
    )


def _site(point, axis, *, precision=3) -> tuple[float, float, float]:
    """Physical profile site with the through-axis coordinate made irrelevant."""
    result = [round(float(component), precision) for component in point]
    result["xyz".index(_axis_letter(axis))] = 0.0
    return (result[0], result[1], result[2])


def _feature_sites(feature, *, precision=3) -> tuple[tuple[float, float, float], ...]:
    members = tuple(getattr(feature, "members", ()) or ())
    points = members or (feature.frame.origin,)
    return tuple(_site(point, feature.frame.axis, precision=precision) for point in points)


def profiled_bore_target_sources(features, records):
    """Unambiguous declared owners at provider precision, for physical target checks."""
    candidates = []
    for feature in features:
        if getattr(feature, "profile", None) != "double_d":
            continue
        key = profiled_bore_key(
            feature.profile,
            feature.frame.axis,
            feature.through,
            feature.diameter,
            feature.across_flats,
            feature.profile_direction,
            precision=6,
        )
        sites = Counter(_feature_sites(feature, precision=6))
        if sum(sites.values()) == feature.count:
            candidates.append((feature, key, sites))
    matches: dict[int, list] = {}
    physical_sites: dict[int, Counter] = {}
    for record in records:
        if not _is_principal_axis(record.axis):
            continue
        key = profiled_bore_key(
            "double_d",
            record.axis,
            record.through,
            record.major_diameter,
            record.across_flats,
            record.flat_direction,
            precision=6,
        )
        site = _site(record.location, record.axis, precision=6)
        owners = [
            feature for feature, expected, sites in candidates if key == expected and site in sites
        ]
        if len(owners) == 1:
            owner_id = id(owners[0])
            matches.setdefault(owner_id, []).append(record)
            physical_sites.setdefault(owner_id, Counter())[site] += 1
    return {
        id(feature): tuple(matches[id(feature)])
        for feature, _, sites in candidates
        if physical_sites.get(id(feature)) == sites
    }


def _same_site(first, second) -> bool:
    return all(abs(a - b) <= _SITE_TOL for a, b in zip(first, second, strict=True))


def lint_profiled_bore_coverage(
    part,
    annotations,
    *,
    recognition: RecognitionResult | None,
    features=(),
    registry=None,
    dropped_profiles=(),
    dropped_profile_evidence=None,
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
    physical = tuple(
        (
            profiled_bore_key(
                "double_d",
                bore.axis,
                bore.through,
                bore.major_diameter,
                bore.across_flats,
                bore.flat_direction,
            ),
            _site(bore.location, bore.axis),
        )
        for bore in recognition.double_d_bores
    )
    required = Counter(key for key, _site_key in physical)
    if not required:
        return []

    covered: set[int] = set()
    unowned: Counter = Counter()

    def cover_owner(owner, key, count) -> None:
        if owner is None:
            unowned[key] += int(count or 1)
            return
        remaining = int(count or 1)
        for owner_site in _feature_sites(owner):
            match = next(
                (
                    index
                    for index, (physical_key, physical_site) in enumerate(physical)
                    if index not in covered
                    and physical_key == key
                    and _same_site(owner_site, physical_site)
                ),
                None,
            )
            if match is not None:
                covered.add(match)
                remaining -= 1
                if remaining <= 0:
                    break

    for annotation in annotations:
        count = int(getattr(annotation, "covers_count", 1) or 1)
        for profile, axis, through, major, across, direction in getattr(
            annotation, "covers_profiles", ()
        ):
            cover_owner(
                annotation_owner(registry, annotation),
                profiled_bore_key(profile, axis, through, major, across, direction),
                count,
            )

    # A placed structured note may carry the two addressable dimensional requirements of a
    # double-D profile. Join them back to the recognition-owned physical key; neither prose nor
    # mere IR presence contributes. Pattern owners retain their physical member count (#1351).
    if registry is not None:
        satisfied: dict[object, set[str]] = {}
        for identity in satisfaction_ids(registry):
            satisfied.setdefault(identity.feature, set()).add(identity.parameter)
        required_parameters = {"bore.diameter", "profile_across_flats.length"}
        for feature in features:
            if not required_parameters <= satisfied.get(feature, set()):
                continue
            bore = getattr(feature, "member", feature)
            if getattr(bore, "profile", None) != "double_d":
                continue
            cover_owner(
                feature,
                profiled_bore_key(
                    "double_d",
                    feature.frame.axis,
                    bore.through,
                    bore.diameter,
                    bore.across_flats,
                    bore.profile_direction,
                ),
                getattr(feature, "count", 1),
            )

    provided = Counter(unowned)
    provided.update(physical[index][0] for index in covered)

    dropped_covered: set[int] = set()
    unowned_dropped: Counter = Counter()
    evidence = (
        tuple(dropped_profile_evidence)
        if dropped_profile_evidence is not None
        else tuple((profile, None) for profile in dropped_profiles)
    )
    for profile, owner in evidence:
        profile_name, axis, through, major, across, direction = profile
        key = profiled_bore_key(profile_name, axis, through, major, across, direction)
        if owner is None:
            unowned_dropped[key] += 1
            continue
        for owner_site in _feature_sites(owner):
            match = next(
                (
                    index
                    for index, (physical_key, physical_site) in enumerate(physical)
                    if index not in covered
                    and index not in dropped_covered
                    and physical_key == key
                    and _same_site(owner_site, physical_site)
                ),
                None,
            )
            if match is not None:
                dropped_covered.add(match)
                break
    dropped = Counter(unowned_dropped)
    dropped.update(physical[index][0] for index in dropped_covered)
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    issues: list[LintIssue] = []
    for key, need in sorted(required.items()):
        _profile, axis, _through, major, across, direction = key
        have = provided[key]
        # Legacy callers may still supply specification-only drops. They can suppress a
        # duplicate report when nothing landed, but cannot safely combine with owner-resolved
        # authority. Run-owned evidence carries exact owner/site and may combine normally.
        drop_credit = dropped[key] if dropped_profile_evidence is not None or have == 0 else 0
        if have + drop_credit >= need:
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
