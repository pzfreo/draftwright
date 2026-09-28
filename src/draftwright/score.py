"""Compatibility home for the external recognition census.

New code imports :func:`quiddity.feature_census`.  The historical
``draftwright.score`` path remains through Draftwright 0.5.x and is scheduled for removal in
0.6.0 together with the package-level recognition re-export.
"""

import warnings

from quiddity import feature_census

warnings.warn(
    "draftwright.score is deprecated (#1936); import quiddity.feature_census. Removed in 0.6.0.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["feature_census"]
