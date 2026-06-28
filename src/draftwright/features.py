"""Compatibility facade — feature recognition moved to :mod:`draftwright.recognition`.

ADR 0007 consolidated all feature recognition under
:mod:`draftwright.recognition`; the slot recogniser that lived here is now
:mod:`draftwright.recognition.slots`. This shim re-exports it from the old path
so existing ``from draftwright.features import find_slots`` / ``Slot`` imports
keep working. Import from :mod:`draftwright.recognition` instead.
"""

from __future__ import annotations

import warnings

from draftwright.recognition import Slot, find_slots

warnings.warn(
    "draftwright.features is deprecated; import find_slots/Slot from "
    "draftwright.recognition instead (ADR 0007).",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["Slot", "find_slots"]
