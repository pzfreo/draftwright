"""Shared semantic survival order for annotation placement (#1866).

This is product policy, not an ISO/ASME-mandated hierarchy. Unknown is kept
distinct from optional so unattributed ink cannot be silently called redundant.
"""

from __future__ import annotations

from typing import Literal

ObligationClass = Literal["required", "optional", "unknown"]

_RANK: dict[ObligationClass, int] = {"optional": 0, "unknown": 1, "required": 2}


def obligation_rank(classification: ObligationClass) -> int:
    """Return the deterministic over-capacity survival rank of one obligation."""

    try:
        return _RANK[classification]
    except KeyError as exc:
        raise ValueError(f"invalid annotation obligation class: {classification!r}") from exc
