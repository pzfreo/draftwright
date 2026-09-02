"""Run-local ownership between provider occurrences and Draftwright IR features.

This is consumer-owned correspondence state below and beside the ADR-0015 IR waist.  It keeps
the provider's opaque :class:`FeatureRef` only for the lifetime of one recognition run and never
turns object addresses, feature order, record values, or topology indices into persistent IDs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from b123d_recognisers.evidence import FeatureRef, RecognitionEvidence

# These aggregate families have an unconditional one-record -> one-feature adapter in
# model.detect.  Grouped, nested, absorbed, classification-only, and intentionally deferred
# families stay unclassified until a later slice can state their N:1 / 1:0 ownership honestly.
DIRECT_FAMILIES = frozenset(
    {
        "blends",
        "chamfers",
        "circular_blind_steps",
        "double_d_bores",
        "fillets",
        "flats",
        "grooves",
        "pads",
        "paired_ramp_steps",
        "polygonal_bosses",
        "polygonal_stock",
        "rectangular_blind_slots",
        "round_bottom_blind_slots",
    }
)


@dataclass(frozen=True)
class OccurrenceBinding:
    """One exact accepted occurrence represented by one exact IR feature object."""

    occurrence: FeatureRef
    feature: object


@dataclass(frozen=True)
class RecognitionOwnership:
    """Immutable run-local direct-ownership ledger paired with one evidence authority."""

    evidence: RecognitionEvidence
    expected_direct: tuple[FeatureRef, ...]
    bindings: tuple[OccurrenceBinding, ...]

    def binding_for(self, occurrence: FeatureRef) -> OccurrenceBinding | None:
        """Return the direct binding for an occurrence, validating its authority first."""

        self.evidence.family(occurrence)
        return next(
            (binding for binding in self.bindings if binding.occurrence is occurrence), None
        )

    def status(
        self, occurrence: FeatureRef
    ) -> Literal["represented", "unexpectedly_missing", "unclassified"]:
        """Classify only the direct contract this slice can prove.

        ``unclassified`` is deliberately not a report disposition.  It is the migration state
        for grouped/absorbed/deferred families whose ownership rules are not implemented yet.
        """

        if self.binding_for(occurrence) is not None:
            return "represented"
        if any(expected is occurrence for expected in self.expected_direct):
            return "unexpectedly_missing"
        return "unclassified"

    @property
    def unexpectedly_missing(self) -> tuple[FeatureRef, ...]:
        """Direct occurrences for which the adapter failed to record an IR owner."""

        return tuple(
            occurrence
            for occurrence in self.expected_direct
            if self.binding_for(occurrence) is None
        )


class RecognitionOwnershipBuilder:
    """Mutable conversion-time collector; freeze before attaching it to a drawing."""

    def __init__(self, evidence: RecognitionEvidence) -> None:
        if type(evidence) is not RecognitionEvidence:
            raise TypeError("evidence must be an exact RecognitionEvidence")
        self.evidence = evidence
        self._expected_direct = tuple(
            occurrence
            for occurrence in evidence.features
            if evidence.family(occurrence) in DIRECT_FAMILIES
        )
        self._by_record_identity: dict[int, list[tuple[FeatureRef, object]]] = {}
        for occurrence in self._expected_direct:
            record = evidence.record(occurrence)
            self._by_record_identity.setdefault(id(record), []).append((occurrence, record))
        self._bindings: list[OccurrenceBinding] = []
        self._bound_occurrence_ids: set[int] = set()
        self._owned_feature_ids: set[int] = set()

    @property
    def result(self):
        """The exact aggregate paired with this builder's evidence authority."""

        return self.evidence.result

    def bind(self, record: object, feature: object) -> None:
        """Bind at the adapter decision site using exact run-local record identity."""

        matches = [
            occurrence
            for occurrence, candidate in self._by_record_identity.get(id(record), ())
            if candidate is record
        ]
        if len(matches) != 1:
            reason = (
                "does not belong to a direct evidence occurrence"
                if not matches
                else "is ambiguous"
            )
            raise ValueError(f"recognition record {reason}")
        occurrence = matches[0]
        if id(occurrence) in self._bound_occurrence_ids:
            raise ValueError("recognition occurrence already has an IR owner")
        if id(feature) in self._owned_feature_ids:
            raise ValueError("IR feature already owns a recognition occurrence")
        self._bound_occurrence_ids.add(id(occurrence))
        self._owned_feature_ids.add(id(feature))
        self._bindings.append(OccurrenceBinding(occurrence, feature))

    def freeze(self) -> RecognitionOwnership:
        """Seal the current ledger without manufacturing owners for missing occurrences."""

        return RecognitionOwnership(
            evidence=self.evidence,
            expected_direct=self._expected_direct,
            bindings=tuple(self._bindings),
        )
