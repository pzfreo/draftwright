"""Deprecated alias for :mod:`draftwright.sheet` (renamed 2026-07-15, #640).

The fluent ``Sheet`` facade was never a DSL (ADR 0001 decided against one), and
the module name collided with the ADR 0004 layout engine (now ``compose.py``).
Import from :mod:`draftwright.sheet` — or just ``from draftwright import Sheet``.
This frozen alias will be removed in a future release.
"""

import warnings

from draftwright import sheet as _sheet

warnings.warn(
    "draftwright.sheet_dsl is deprecated: the module was renamed to "
    "draftwright.sheet (#640). Import from draftwright.sheet, or use "
    "draftwright.Sheet.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name: str):
    return getattr(_sheet, name)
