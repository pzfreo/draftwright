"""Compatibility home for the external recognition census.

New code imports :func:`b123d_recognisers.feature_census`.  The historical
``draftwright.score`` path remains through Draftwright 0.5.x and is scheduled for removal in
0.6.0 together with the package-level recognition re-export.
"""

from b123d_recognisers import feature_census

__all__ = ["feature_census"]
