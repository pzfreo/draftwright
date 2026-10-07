"""Declaration-scoped layout controls for the Sheet facade (ADR 4)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, cast

from draftwright.model.ir import (
    PLACEMENT_SIDES,
    DeclarationIdentity,
    DimensionParameterId,
    LayoutOverride,
)
from draftwright.model.planner import dimension_lane_supported, location_lane_supported

if TYPE_CHECKING:
    from draftwright.sheet import Sheet


def _identify(sheet: Sheet, token: int, identity: DeclarationIdentity) -> None:
    """Bind one validated identity to a live declaration token."""

    sheet._index_of_token(token)
    live_tokens = {live_token for live_token, _feature in sheet._entries}
    for other_token, existing in sheet._declaration_identities.items():
        if (
            other_token != token
            and existing.declaration_id == identity.declaration_id
            and other_token in live_tokens
        ):
            raise ValueError(f"duplicate declaration_id {identity.declaration_id!r}")
    sheet._declaration_identities[token] = identity


def _declaration_token(sheet: Sheet, declaration_id: str) -> int:
    """Resolve one build-scoped declaration ID to its live token."""

    live_tokens = {token for token, _feature in sheet._entries}
    matches = [
        token
        for token, identity in sheet._declaration_identities.items()
        if identity.declaration_id == declaration_id and token in live_tokens
    ]
    if len(matches) != 1:
        raise ValueError(
            f"by_declaration({declaration_id!r}) requires one live declaration; "
            f"found {len(matches)}"
        )
    return matches[0]


def _dimension_entry_for_layout(
    sheet: Sheet,
    token: int,
    parameter_id: str,
    axis: str | None = None,
    member: int | Literal["centre"] | None = None,
) -> tuple[dict, str] | None:
    """Return the one declared dimension entry addressed by a lane override."""

    for entries in (sheet._authored, sheet._added_dimensions):
        matches = [
            entry
            for entry in entries
            if entry["token"] == token
            and entry["role"] == parameter_id
            and entry.get("discriminator") == axis
            and entry.get("member") == member
        ]
        if len(matches) > 1:
            raise ValueError(
                f"dimension {parameter_id!r} is declared more than once; "
                "remove the conflicting intent before overriding its lane"
            )
        if matches:
            return matches[0], parameter_id
    return None


def layout_options(
    sheet: Sheet,
    declaration_id: str,
    *,
    parameter: DimensionParameterId | None = None,
    axis: str | None = None,
    member: int | Literal["centre"] | None = None,
    handle_factory: Callable[[Sheet, int], object],
) -> dict[str, object]:
    """Describe the bounded layout-only controls supported by one declaration.

    This is a pre-build capability query, not a feasibility promise: the shared layout
    solve still decides whether the requested corridor can be used on the final sheet.
    """

    token = sheet._declaration_token(declaration_id)
    feature = sheet._features[sheet._index_of_token(token)]
    if parameter is None and (axis is not None or member is not None):
        raise ValueError("axis and member require an exact location parameter selector")
    if parameter is not None:
        handle = handle_factory(sheet, sheet._index_of_token(token))
        _resolved_token, target, discriminator, canonical = sheet._resolve_measurement(
            handle, parameter, axis, "layout_options", member=member
        )
        parameter_id = next(
            (
                item.parameter_id
                for item in target.parameters()
                if canonical in (item.role, item.parameter_id)
                and item.discriminator == discriminator
            ),
            canonical,
        )
        entry = sheet._dimension_entry_for_layout(token, parameter_id, axis, member)
        if entry is None:
            raise ValueError(
                f"declaration {declaration_id!r} has no declared dimension "
                f"{parameter_id!r} to override"
            )
        controls: dict[str, object] = {}
        if dimension_lane_supported(feature, parameter_id) or (
            parameter_id == "location" and location_lane_supported(feature, axis, member)
        ):
            controls["lane"] = {
                "current": entry[0].get("lane"),
                "minimum": 1,
                "maximum": 8,
                "meaning": "one-based drafting-spaced lane from the feature witness",
            }
        # Only advertise sides whose renderer consumes the authored corridor. Other
        # planner-accepted sides are not an editor actuator until that renderer uses them.
        if parameter_id != "location" and feature.kind in {
            "hole",
            "pattern",
            "envelope",
            "step",
            "boss",
            "pocket",
            "slot",
        }:
            placements = sheet.dimension_options(handle, parameter)["placements"]
            sides = sorted(
                {
                    placement["side"]
                    for placement in placements
                    if placement["side"] is not None
                    and (entry[0].get("view") is None or placement["view"] == entry[0]["view"])
                }
            )
            if sides:
                controls["side"] = {"current": entry[0].get("side"), "supported_values": sides}
        if not controls:
            raise ValueError(
                f"dimension {parameter_id!r} on {feature.kind} does not expose a layout control"
            )
        result = {
            "schema": "draftwright.layout-options",
            "schema_version": 1,
            "scope": "single-dimension-layout-controls",
            "requires_build_validation": True,
            "declaration_id": declaration_id,
            "feature_kind": feature.kind,
            "parameter_id": parameter_id,
            "controls": controls,
        }
        if parameter_id == "location":
            result["axis"] = axis
            result["member"] = member
        return result
    side = getattr(feature, "side", None)
    if feature.kind == "authored_dimension":
        supported_sides = sorted(
            candidate
            for candidate in PLACEMENT_SIDES
            if _authored_dimension_accepts_side(feature, candidate)
        )
    else:
        supported_sides = sorted(PLACEMENT_SIDES) if side in PLACEMENT_SIDES else []
    if not supported_sides:
        raise ValueError(
            f"declaration {declaration_id!r} does not expose a supported side control"
        )
    return {
        "schema": "draftwright.layout-options",
        "schema_version": 1,
        "scope": "single-declaration-layout-controls",
        "requires_build_validation": True,
        "declaration_id": declaration_id,
        "feature_kind": feature.kind,
        "controls": {
            "side": {
                "current": side,
                "supported_values": supported_sides,
            }
        },
    }


def _authored_dimension_accepts_side(feature, side: str) -> bool:
    """Use the IR's renderer-compatible placement validator, not a second side table."""

    if feature.dimension_kind not in {"linear", "thickness", "diameter", "radius", "angular"}:
        return False
    if feature.dimension_kind == "angular" and not (
        feature.angular_reference or feature.angular_references
    ):
        return False
    try:
        replace(feature, side=side)
    except (TypeError, ValueError):
        return False
    return True


def validate_layout_override(
    sheet: Sheet,
    declaration_id: str,
    *,
    side: str | None = None,
    parameter: DimensionParameterId | None = None,
    lane: int | None = None,
    axis: str | None = None,
    member: int | Literal["centre"] | None = None,
    **unsupported_controls,
) -> dict[str, object]:
    """Preflight a layout override without mutating the sheet.

    A supported result means the declaration and bounded vocabulary are valid.  It still
    requires :meth:`build` to establish geometric feasibility on the composed sheet.
    """

    result: dict[str, object] = {
        "schema": "draftwright.layout-validation",
        "schema_version": 1,
        "scope": "single-declaration-layout-controls",
        "requires_build_validation": True,
        "declaration_id": declaration_id,
        "supported": False,
        "issues": [],
        "options": None,
    }
    try:
        token = sheet._declaration_token(declaration_id)
    except ValueError as error:
        result["issues"] = [
            {
                "code": "invalid_declaration",
                "message": str(error),
            }
        ]
        return result
    if unsupported_controls:
        result["issues"] = [
            {
                "code": "unsupported_control",
                "controls": sorted(unsupported_controls),
                "message": "layout_override accepts only side, or parameter with side or lane",
            }
        ]
        return result
    if (side is None) == (lane is None):
        result["issues"] = [
            {
                "code": "invalid_control_combination",
                "message": "specify exactly one of side or lane",
            }
        ]
        return result
    if lane is not None and parameter is None:
        result["issues"] = [
            {
                "code": "invalid_control_combination",
                "message": "lane requires an exact parameter selector",
            }
        ]
        return result
    if (axis is not None or member is not None) and parameter != "location":
        result["issues"] = [
            {"code": "invalid_selector", "message": "axis and member select only location"}
        ]
        return result
    feature = sheet._features[sheet._index_of_token(token)]
    try:
        options = sheet.layout_options(
            declaration_id, parameter=parameter, axis=axis, member=member
        )
    except (TypeError, ValueError, IndexError) as error:
        result["issues"] = [
            {
                "code": "unsupported_declaration",
                "feature_kind": feature.kind,
                "message": str(error),
            }
        ]
        return result
    result["options"] = options
    controls = cast(dict[str, object], options["controls"])
    if side is not None:
        side_options = controls.get("side")
        if side_options is None:
            result["issues"] = [
                {
                    "code": "unsupported_control",
                    "message": "dimension does not expose a side control",
                }
            ]
            return result
        supported = cast(list[str], cast(dict[str, object], side_options)["supported_values"])
        if side not in supported:
            result["issues"] = [
                {
                    "code": "unsupported_value",
                    "control": "side",
                    "value": side,
                    "supported_values": supported,
                    "message": f"side must be a supported side: {supported} (got {side!r})",
                }
            ]
            return result
    else:
        assert parameter is not None
        if "lane" not in controls:
            result["issues"] = [
                {
                    "code": "unsupported_control",
                    "message": "dimension does not expose a lane control",
                }
            ]
            return result
        if isinstance(lane, bool) or not isinstance(lane, int) or not 1 <= lane <= 8:
            result["issues"] = [
                {
                    "code": "unsupported_value",
                    "control": "lane",
                    "value": lane,
                    "minimum": 1,
                    "maximum": 8,
                    "message": f"lane must be an integer from 1 to 8 (got {lane!r})",
                }
            ]
            return result
    result["supported"] = True
    return result


def layout_override(
    sheet: Sheet,
    declaration_id: str,
    *,
    side: str | None = None,
    parameter: DimensionParameterId | None = None,
    lane: int | None = None,
    axis: str | None = None,
    member: int | Literal["centre"] | None = None,
) -> Sheet:
    """Append one bounded declaration-scoped layout override.

    ``side`` selects a feature corridor. ``parameter`` + ``lane`` selects a one-based
    parallel lane for one declared referential dimension. Neither form accepts page
    coordinates; the shared placement solve resolves and validates the physical offset.
    """

    validation = sheet.validate_layout_override(
        declaration_id, side=side, parameter=parameter, lane=lane, axis=axis, member=member
    )
    if not validation["supported"]:
        issues = cast(list[dict[str, object]], validation["issues"])
        raise ValueError(str(issues[0]["message"]))
    options = cast(dict[str, object], validation["options"])
    parameter_id = cast(str | None, options.get("parameter_id"))
    key = (declaration_id, parameter_id, axis, member)
    if any(
        (row.declaration_id, row.parameter_id, row.axis, row.member) == key
        for row in sheet._layout_overrides
    ):
        raise ValueError(f"layout target {key!r} already has a layout override")
    token = sheet._declaration_token(declaration_id)
    if side is not None:
        if parameter is None:
            index = sheet._index_of_token(token)
            sheet._replace_feature(index, replace(sheet._features[index], side=side))
            sheet._layout_overrides.append(LayoutOverride(declaration_id, side))
        else:
            assert parameter_id is not None
            entry = sheet._dimension_entry_for_layout(token, parameter_id, axis, member)
            assert entry is not None
            entry[0]["side"] = side
            sheet._layout_overrides.append(
                LayoutOverride(
                    declaration_id, side=side, parameter_id=parameter_id, axis=axis, member=member
                )
            )
    else:
        assert parameter_id is not None
        entry = sheet._dimension_entry_for_layout(token, parameter_id, axis, member)
        assert entry is not None and lane is not None
        entry[0]["lane"] = lane
        sheet._layout_overrides.append(
            LayoutOverride(
                declaration_id, parameter_id=parameter_id, lane=lane, axis=axis, member=member
            )
        )
    return sheet
