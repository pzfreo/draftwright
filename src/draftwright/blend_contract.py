"""Strict shared boundary for released schema-v3 ``Blend`` path records (#1433/#1438)."""

from __future__ import annotations

from math import isfinite

from b123d_recognisers import Blend, CircularBlendPath, StraightBlendPath

_BLEND_FEATURE_TYPE: type | None = None
_BLEND_FRAME_TYPE: type | None = None


def register_blend_ir_types(feature_type: type, frame_type: type) -> None:
    """Publish exact compiler identities once without creating a lint-to-model import."""
    global _BLEND_FEATURE_TYPE, _BLEND_FRAME_TYPE
    if _BLEND_FEATURE_TYPE is None and _BLEND_FRAME_TYPE is None:
        _BLEND_FEATURE_TYPE = feature_type
        _BLEND_FRAME_TYPE = frame_type
    elif _BLEND_FEATURE_TYPE is not feature_type or _BLEND_FRAME_TYPE is not frame_type:
        raise RuntimeError("Blend IR types are already registered")


def is_exact_blend_feature(value) -> bool:
    """Whether *value* and its frame have the exact registered compiler types."""
    return (
        _BLEND_FEATURE_TYPE is not None
        and _BLEND_FRAME_TYPE is not None
        and type(value) is _BLEND_FEATURE_TYPE
        and type(getattr(value, "frame", None)) is _BLEND_FRAME_TYPE
    )


def _number(value, *, name: str, positive: bool = False) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be an exact non-boolean int or float")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else "")) from exc
    if not isfinite(result) or (positive and result <= 0.0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return result


def validate_blend_fields(
    *, axis, radius, at, side, axis_direction, path_kind="straight", path_radius=None
) -> tuple[
    str,
    float,
    tuple[float, float, float],
    str,
    tuple[float, float, float],
    str,
    float | None,
]:
    """Validate semantic Blend IR without imposing provider publication precision."""
    if type(axis) is not str or axis not in ("x", "y", "z"):
        raise ValueError("blend axis must be exactly 'x', 'y', or 'z'")
    if type(side) is not str or side not in ("convex", "concave"):
        raise ValueError("blend side must be exactly 'convex' or 'concave'")
    if type(path_kind) is not str or path_kind not in ("straight", "circular"):
        raise ValueError("blend path_kind must be exactly 'straight' or 'circular'")
    if type(at) is not tuple or len(at) != 3:
        raise ValueError("blend at must be an immutable 3-vector")
    if type(axis_direction) is not tuple or len(axis_direction) != 3:
        raise ValueError("blend axis_direction must be an immutable 3-vector")
    point_values = tuple(_number(value, name="blend at component") for value in at)
    point = (point_values[0], point_values[1], point_values[2])
    direction_values = tuple(
        _number(value, name="blend axis_direction component") for value in axis_direction
    )
    direction = (direction_values[0], direction_values[1], direction_values[2])
    value = _number(radius, name="blend radius", positive=True)
    path: StraightBlendPath | CircularBlendPath
    try:
        if path_kind == "straight":
            if path_radius is not None:
                raise ValueError("a straight blend path cannot carry path_radius")
            path = StraightBlendPath(point, direction)
            clean_path_radius = None
        else:
            clean_path_radius = _number(path_radius, name="blend path_radius", positive=True)
            path = CircularBlendPath(point, direction, clean_path_radius)
        rebuilt = Blend(value, side, path)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("blend fields violate the released public record contract") from exc
    if type(rebuilt.path) is StraightBlendPath:
        clean_direction = rebuilt.path.direction
    elif type(rebuilt.path) is CircularBlendPath:
        clean_direction = rebuilt.path.normal
    else:  # pragma: no cover - guarded by the released provider record constructor
        raise RuntimeError("Blend rebuilt with an unknown path record")
    axis_index = "xyz".index(axis)
    dominant = max(range(3), key=lambda index: abs(clean_direction[index]))
    if axis_index != dominant:
        raise ValueError("blend axis must match the canonical dominant axis_direction component")
    return axis, value, point, side, clean_direction, path_kind, clean_path_radius


def blend_provider_key(record) -> tuple:
    """Validate one exact public record and return its lossless occurrence key."""
    if type(record) is not Blend:
        raise TypeError("blend inventory members must be exact Blend records")
    path = record.path
    if type(path) is StraightBlendPath:
        at = path.at
        direction = path.direction
        path_kind = "straight"
        path_radius = None
    elif type(path) is CircularBlendPath:
        at = path.center
        direction = path.normal
        path_kind = "circular"
        path_radius = path.radius
    else:
        raise TypeError("Blend.path must be an exact StraightBlendPath or CircularBlendPath")
    if type(direction) is not tuple or len(direction) != 3:
        raise ValueError("blend path direction must be an immutable 3-vector")
    # Validate exact primitive values before selecting the provider record's deterministic
    # routing axis.  In particular, do not execute an arbitrary component's ``__float__``
    # protocol before the strict public-record boundary has refused it.
    clean_components = tuple(
        _number(value, name="blend path direction component") for value in direction
    )
    dominant = max(range(3), key=lambda index: abs(clean_components[index]))
    axis = "xyz"[dominant]
    published_direction = direction
    axis, radius, at, side, direction, path_kind, path_radius = validate_blend_fields(
        axis=axis,
        radius=record.radius,
        at=at,
        side=record.side,
        axis_direction=direction,
        path_kind=path_kind,
        path_radius=path_radius,
    )
    if at != tuple(round(component, 3) for component in at):
        raise ValueError("blend at must use the released three-decimal serialization")
    if radius != round(radius, 3):
        raise ValueError("blend radius must use the released three-decimal serialization")
    if published_direction != direction:
        raise ValueError("blend path direction must already be canonical")
    if direction != tuple(round(component, 6) for component in direction):
        raise ValueError("blend axis_direction must use the released six-decimal serialization")
    if path_radius is not None and path_radius != round(path_radius, 3):
        raise ValueError("blend path_radius must use the released three-decimal serialization")
    return axis, radius, at, side, direction, path_kind, path_radius
