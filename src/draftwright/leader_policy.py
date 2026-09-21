"""Public, coordinate-free policy for feature-leader placement regions."""

from __future__ import annotations

from enum import Enum


class LeaderRegionPolicy(str, Enum):
    """Which proven page region feature-leader labels may occupy.

    ``AUTO`` preserves Draftwright's normal solve: interior-capable families offer
    their proved interior candidates and retain their established exterior fallback.
    ``INTERIOR`` keeps only the interior candidates of those eligible, unconstrained
    families.  Families without an interior proof, and a declaration carrying an
    explicit ``side=``, remain exterior.  ``EXTERIOR`` suppresses every interior
    candidate and restores the historical exterior-only layout.
    """

    AUTO = "auto"
    INTERIOR = "interior"
    EXTERIOR = "exterior"


def leader_region_policy(value: str | LeaderRegionPolicy) -> LeaderRegionPolicy:
    """Return a validated public leader-region policy."""

    try:
        return LeaderRegionPolicy(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"leader_region must be 'auto', 'interior', or 'exterior', got {value!r}"
        ) from error


def effective_leader_region_policy(
    family: str | LeaderRegionPolicy,
    document: str | LeaderRegionPolicy,
) -> LeaderRegionPolicy:
    """Combine a family's proved eligibility with the document-level policy.

    A document may remove interior candidates globally.  It may require them only
    where the producer opted in; it cannot manufacture interior authority for an
    exterior-only family or override an authored side constraint.
    """

    family_policy = leader_region_policy(family)
    document_policy = leader_region_policy(document)
    if document_policy is LeaderRegionPolicy.EXTERIOR:
        return LeaderRegionPolicy.EXTERIOR
    if document_policy is LeaderRegionPolicy.INTERIOR and family_policy is LeaderRegionPolicy.AUTO:
        return LeaderRegionPolicy.INTERIOR
    return family_policy
