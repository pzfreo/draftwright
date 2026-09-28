"""Stable import path for feature-leader region policy.

The policy is owned by :mod:`draftwright.annotation_layout_profile`.
"""

from draftwright.annotation_layout_profile import (
    LeaderRegionPolicy,
    effective_leader_region_policy,
    leader_region_policy,
)

__all__ = ["LeaderRegionPolicy", "leader_region_policy", "effective_leader_region_policy"]
