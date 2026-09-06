"""Exact compiler-type identities shared without a lint-to-model import (#1432)."""

from __future__ import annotations

_ORIENTED_SLOT_TYPES: tuple[type, type, type] | None = None


def register_oriented_slot_feature_type(
    feature_type: type, passage_type: type, frame_type: type
) -> None:
    """Register the compiler class so independent observers can require exact identity."""
    global _ORIENTED_SLOT_TYPES
    if _ORIENTED_SLOT_TYPES is None:
        _ORIENTED_SLOT_TYPES = (feature_type, passage_type, frame_type)
    elif _ORIENTED_SLOT_TYPES != (feature_type, passage_type, frame_type):
        raise RuntimeError("oriented slot feature type is already registered")


def is_exact_oriented_slot_feature(value) -> bool:
    """Whether *value* has the exact registered compiler type (not a duck/spoof)."""
    return (
        _ORIENTED_SLOT_TYPES is not None
        and type(value) is _ORIENTED_SLOT_TYPES[0]
        and type(getattr(value, "passage", None)) is _ORIENTED_SLOT_TYPES[1]
        and type(getattr(value, "frame", None)) is _ORIENTED_SLOT_TYPES[2]
    )
