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


_ENVELOPE_TYPE: type | None = None


def register_envelope_feature_type(feature_type: type) -> None:
    """Publish envelope identity without coupling physical lint to the compiler."""
    global _ENVELOPE_TYPE
    if _ENVELOPE_TYPE is None:
        _ENVELOPE_TYPE = feature_type
    elif _ENVELOPE_TYPE is not feature_type:
        raise RuntimeError("envelope feature type is already registered")


def is_exact_envelope_feature(value) -> bool:
    """Reject duck-typed or subclassed substitutes for an overall extent owner."""
    return _ENVELOPE_TYPE is not None and type(value) is _ENVELOPE_TYPE
