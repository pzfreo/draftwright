"""Pure source-geometry blocker policy for AP242 PMI extraction.

Direct XCAF failures are removed only after exact Part21 support replaces them.
"""

from __future__ import annotations


def _dimension_geometry_blockers(
    kind: str,
    reference_reasons: tuple[str, ...],
    station_reasons: tuple[str, ...],
) -> tuple[str, ...]:
    """Rendering blockers owned by incomplete source geometry, not later correlation.

    A group station computed from the measurable subset is not proof that the authored
    relationship is complete when another referenced shape was unavailable. Keep those XCAF
    extraction failures in ``lowering_blockers`` for source accounting *and* in this distinct
    render gate. Correlation blockers added later by model lowering never pass through here,
    so #1116's standalone fallback remains drawable (#1209).
    """
    if kind not in ("linear", "thickness"):
        return ()
    return tuple(dict.fromkeys((*reference_reasons, *station_reasons)))


def _is_direct_xcaf_reference_failure(reason: str) -> bool:
    exact = {"one referenced shape is unavailable", "referenced geometry is unavailable"}
    return reason in exact or reason.startswith("one referenced shape could not be measured (")


def _without_direct_xcaf_reference_failures(reasons: tuple[str, ...]) -> tuple[str, ...]:
    """Drop only geometry failures superseded by an exact Part21 support overlay."""
    return tuple(reason for reason in reasons if not _is_direct_xcaf_reference_failure(reason))


def _is_direct_xcaf_diameter_failure(reason: str) -> bool:
    prefixes = (
        "one diameter reference shape is unavailable",
        "one diameter reference is not a face",
        "one diameter reference face is not cylindrical",
        "one cylindrical reference has unsupported face orientation",
        "one cylindrical reference could not be measured (",
        "diameter reference geometry is unavailable",
    )
    return reason.startswith(prefixes)


def _without_direct_xcaf_diameter_failures(reasons: tuple[str, ...]) -> tuple[str, ...]:
    """Drop face-only XCAF failures superseded by exact Part21 diameter supports."""
    return tuple(reason for reason in reasons if not _is_direct_xcaf_diameter_failure(reason))


def _is_direct_xcaf_angular_failure(reason: str) -> bool:
    prefixes = (
        "angular dimension needs one planar face in each reference group",
        "angular dimension needs two measurable authored reference groups",
        "one angular reference is not a face",
        "one angular reference face is not planar",
        "one angular reference plane could not be measured (",
        "angular reference planes are parallel or coincident",
        "one angular reference face does not select a support half-ray",
        "angular reference is invalid (",
        "angular reference angle ",
    )
    return reason.startswith(prefixes)


def _without_direct_xcaf_angular_failures(reasons: tuple[str, ...]) -> tuple[str, ...]:
    """Drop angular XCAF failures superseded by exact Part21 member supports."""
    return tuple(reason for reason in reasons if not _is_direct_xcaf_angular_failure(reason))
