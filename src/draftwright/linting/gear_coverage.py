"""Independent completeness checks for declared external spur gears (#1086)."""

from __future__ import annotations

from math import isclose
from typing import Literal

from draftwright.linting.issues import LintIssue


def _near_point(left, right, *, tol: float = 1e-6) -> bool:
    return len(left) == len(right) == 3 and all(
        isclose(float(a), float(b), abs_tol=tol, rel_tol=0) for a, b in zip(left, right)
    )


def _near_span(left, right, *, tol: float = 1e-6) -> bool:
    return len(left) == len(right) == 2 and all(
        isclose(float(a), float(b), abs_tol=tol, rel_tol=0) for a, b in zip(left, right)
    )


def _authored(value: float) -> str:
    value = float(value)
    return str(int(value)) if value.is_integer() else repr(value)


def _signed(value: float) -> str:
    if value == 0:
        return "0"
    return ("+" if value > 0 else "-") + _authored(abs(value))


def _expected_rows(feature) -> tuple[tuple[str, str, str], ...]:
    """Independent semantic oracle for the placed table's actual row snapshot."""
    lower, upper = feature.tooth_thickness_tolerance
    return (
        ("GEAR DATA", "VALUE", "BASIS"),
        ("TYPE", "METRIC EXTERNAL SPUR", feature.GEOMETRY_STANDARD),
        ("BASIC RACK", "STANDARD", feature.BASIC_RACK_STANDARD),
        ("TEETH z", str(feature.tooth_count), feature.GEOMETRY_STANDARD),
        ("MODULE m", f"{_authored(feature.module)} mm", feature.MODULE_STANDARD),
        (
            "PRESSURE ANGLE",
            f"{_authored(feature.pressure_angle)} deg",
            feature.GEOMETRY_STANDARD,
        ),
        ("PROFILE SHIFT x", _authored(feature.profile_shift), feature.GEOMETRY_STANDARD),
        ("FACE WIDTH b", f"{_authored(feature.face_width)} mm", feature.GEOMETRY_STANDARD),
        (
            "TOOTH THICKNESS s",
            f"{_authored(feature.tooth_thickness)} {_signed(upper)}/{_signed(lower)} mm",
            feature.TOOTH_THICKNESS_STANDARD,
        ),
        ("FLANK CLASS", str(feature.flank_tolerance_class), feature.FLANK_TOLERANCE_STANDARD),
    )


def lint_declared_gear_coverage(
    *,
    features=(),
    registry=None,
    profiles=None,
    assembly=None,
) -> list[LintIssue]:
    """Reconcile authored gear requirements with tables and independent profile facts.

    ``profiles=None`` means that the run did not supply the production evidence inventory;
    an empty tuple means it ran and proved no repeating profile.  Both states remain
    fail-closed rather than treating unavailable geometry as correspondence.
    """
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    gears = [f for f in features if getattr(f, "kind", None) == "external_spur_gear"]
    issues: list[LintIssue] = []

    if registry is not None:
        for feature in gears:
            names = registry.names_for_feature(feature)
            if len(names) != 1:
                state = "missing" if not names else "ambiguous"
                issues.append(
                    LintIssue(
                        severity=severity,
                        code=f"gear_requirement_{state}",
                        message=(
                            "declared external spur gear does not have exactly one "
                            "provenance-bound standards data table"
                        ),
                    )
                )
                continue
            table = registry.named(names[0])
            if getattr(
                table, "gear_requirement_values", None
            ) != feature.requirement_values or getattr(
                table, "gear_requirement_rows", None
            ) != _expected_rows(feature):
                issues.append(
                    LintIssue(
                        severity=severity,
                        code="gear_requirement_mismatch",
                        message=(
                            "placed gear data table does not preserve the current authored "
                            "normative requirement values"
                        ),
                    )
                )

    if profiles is None:
        for _feature in gears:
            issues.append(
                LintIssue(
                    severity=severity,
                    code="gear_correspondence_unverifiable",
                    message=(
                        "declared gear requirements could not be reconciled because the run "
                        "did not supply full-wire repeating-profile evidence"
                    ),
                )
            )
        return issues

    profiles = tuple(profiles)
    if profiles and not gears:
        issues.append(
            LintIssue(
                severity=severity,
                code="gear_semantics_missing",
                message=(
                    "a repeating radial profile is present but no gear type, geometry, tooth "
                    "thickness, or flank-tolerance requirements were declared"
                ),
            )
        )
        return issues

    for feature in gears:
        # Centre and axial extent are the correspondence key.  Axis is deliberately checked
        # after correspondence so an authored axis error produces a specific diagnostic
        # instead of making the same physical profile disappear.
        matches = [
            profile
            for profile in profiles
            if _near_point(profile.centre, feature.frame.origin)
            and _near_span(profile.span, feature.span)
        ]
        if len(matches) != 1:
            issues.append(
                LintIssue(
                    severity=severity,
                    code="gear_correspondence_unverifiable",
                    message=(
                        "declared gear target has no unique full-wire repeating-profile "
                        "correspondence by centre and axial span"
                    ),
                )
            )
            continue
        profile = matches[0]
        if profile.axis != feature.frame.axis:
            issues.append(
                LintIssue(
                    severity=severity,
                    code="gear_axis_mismatch",
                    message=(
                        f"declared gear axis {feature.frame.axis!r} disagrees with independently "
                        f"proved profile axis {profile.axis!r}"
                    ),
                )
            )
        if profile.repeat_count != feature.tooth_count:
            issues.append(
                LintIssue(
                    severity=severity,
                    code="gear_repeat_count_mismatch",
                    message=(
                        f"declared tooth count {feature.tooth_count} disagrees with independently "
                        f"proved repeat count {profile.repeat_count}"
                    ),
                )
            )
    return issues


__all__ = ["lint_declared_gear_coverage"]
