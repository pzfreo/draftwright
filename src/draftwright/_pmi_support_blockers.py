"""Pure source-support blocker policy for AP242 PMI extraction.

Direct XCAF failures are removed only after exact Part21 support replaces them.
Geometric-tolerance fields outside the lowered vocabulary remain explicit source blockers.
"""

from __future__ import annotations

from draftwright._pmi_schema import (
    _GTOL_MATERIAL_REQUIREMENT,
    _GTOL_MODIFIER,
    _GTOL_TYPE_OF_VALUE,
    _SUPPORTED_GTOL_SCOPE_MODIFIERS,
)


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


def _failure_reason(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _geometric_tolerance_modifiers(obj) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Inventory XCAF's modifier sequence and admit representable scope symbols."""
    try:
        codes = tuple(int(modifier) for modifier in obj.GetModifiers())
    except Exception as exc:
        return (), (f"geometric-tolerance modifiers are unavailable ({_failure_reason(exc)})",)

    names: list[str] = []
    reasons: list[str] = []
    for code in codes:
        name = _GTOL_MODIFIER.get(code)
        if name is None:
            names.append(f"unknown({code})")
            reasons.append(f"geometric-tolerance modifier {code} is unknown")
            continue
        names.append(name)
        if name not in _SUPPORTED_GTOL_SCOPE_MODIFIERS:
            reasons.append(f"geometric-tolerance modifier {name!r} is not supported")

    if len(names) > 1:
        reasons.append(
            f"geometric-tolerance modifier combination {tuple(names)!r} is not supported"
        )
    return tuple(names), tuple(dict.fromkeys(reasons))


def _geometric_tolerance_qualifiers(obj) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Preserve supported tolerance-zone shape and material-condition qualifiers."""
    names: list[str] = []
    reasons: list[str] = []
    fields = (
        ("GetTypeOfValue", "type-of-value", _GTOL_TYPE_OF_VALUE),
        (
            "GetMaterialRequirementModifier",
            "material-requirement modifier",
            _GTOL_MATERIAL_REQUIREMENT,
        ),
    )
    for accessor, description, vocabulary in fields:
        try:
            code = int(getattr(obj, accessor)())
        except Exception as exc:
            reasons.append(
                f"geometric-tolerance {description} is unavailable ({_failure_reason(exc)})"
            )
            continue
        if code == 0:
            continue
        name = vocabulary.get(code)
        if name is None:
            reasons.append(f"geometric-tolerance {description} {code} is unknown")
            continue
        names.append(name)
        if name == "spherical_diameter_zone":
            reasons.append("geometric-tolerance spherical-diameter zone is not supported")
    return tuple(names), tuple(reasons)


def _unpreserved_geometric_tolerance_fields(obj) -> tuple[str, ...]:
    """Keep a source partial when XCAF exposes requirement fields we do not yet carry."""
    reasons: list[str] = []
    enum_fields = (("GetZoneModifier", "zone modifier"),)
    for accessor, description in enum_fields:
        try:
            enum_value = int(getattr(obj, accessor)())
        except Exception as exc:
            reasons.append(
                f"geometric-tolerance {description} is unavailable ({_failure_reason(exc)})"
            )
        else:
            if enum_value != 0:
                reasons.append(f"geometric-tolerance {description} {enum_value} is not preserved")

    float_fields = (
        ("GetValueOfZoneModifier", "zone-modifier value"),
        ("GetMaxValueModifier", "maximum-value modifier"),
    )
    for accessor, description in float_fields:
        try:
            numeric_value = float(getattr(obj, accessor)())
        except Exception as exc:
            reasons.append(
                f"geometric-tolerance {description} is unavailable ({_failure_reason(exc)})"
            )
        else:
            if abs(numeric_value) > 1e-9:
                reasons.append(
                    f"geometric-tolerance {description} {numeric_value:g} is not preserved"
                )

    return tuple(reasons)
