"""Exact compiler-type identities shared without a lint-to-model import (#1432)."""

from __future__ import annotations

_ORIENTED_SLOT_FEATURE_TYPE: type | None = None


def register_oriented_slot_feature_type(feature_type: type) -> None:
    """Register the compiler class so independent observers can require exact identity."""
    global _ORIENTED_SLOT_FEATURE_TYPE
    if _ORIENTED_SLOT_FEATURE_TYPE is None:
        _ORIENTED_SLOT_FEATURE_TYPE = feature_type
    elif _ORIENTED_SLOT_FEATURE_TYPE is not feature_type:
        raise RuntimeError("oriented slot feature type is already registered")


def is_exact_oriented_slot_feature(value) -> bool:
    """Whether *value* has the exact registered compiler type (not a duck/spoof)."""
    return _ORIENTED_SLOT_FEATURE_TYPE is not None and type(value) is _ORIENTED_SLOT_FEATURE_TYPE
