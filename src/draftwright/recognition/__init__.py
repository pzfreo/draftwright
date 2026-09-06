"""Compatibility re-export for :mod:`quiddity`.

Draftwright's embedded implementation was removed by the extraction cutover.  New code imports
``quiddity`` directly.  This package-level surface remains for one compatibility window
and is scheduled for removal in Draftwright 0.6.0; private submodule imports were never part of
the sanctioned surface and have no shims.
"""

from __future__ import annotations

import quiddity as _external

__all__ = _external.__all__

globals().update({name: getattr(_external, name) for name in __all__})
