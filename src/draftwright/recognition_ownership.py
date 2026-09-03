"""Run-local consumer outcomes for provider occurrences and Draftwright IR features.

This is consumer-owned correspondence state below and beside the ADR-0015 IR waist. It keeps
the provider's opaque :class:`FeatureRef` only for the lifetime of one recognition run and never
turns object addresses, feature order, record values, or topology indices into persistent IDs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from b123d_recognisers.evidence import FeatureRef, RecognitionEvidence

from draftwright.recogniser_policy import (
    OwnerlessDisposition,
    ownerless_occurrence_policy,
)

# These aggregate families have an unconditional one-record -> one-feature adapter in
# model.detect. Remaining nested and classification-only families stay unclassified until a later
# slice can state their ownership honestly. Consumer-policy-only occurrences are classified
# separately below because they deliberately have no IR owner.
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

# The aggregate exposes these as authoritative physical member occurrences.  Draftwright may
# lower one member to one feature or absorb several members into one grouped/pattern feature.
# The derived pattern records are deliberately not FeatureRefs and must not be promoted into
# invented persistent occurrences.
GROUPABLE_FAMILIES = frozenset({"holes", "pockets", "slots"})

# These accepted occurrences are nested records carried by a supported parent occurrence. They
# must retain their own outcome while sharing the parent's final IR owner rather than creating a
# duplicate feature or requirement.
NESTED_FAMILIES = frozenset({"countersinks"})

OwnershipDisposition = Literal["represented", "absorbed"]
PolicyDisposition = OwnerlessDisposition
OccurrenceStatus = Literal[
    "represented",
    "absorbed",
    "unsupported",
    "deferred",
    "evidence_only",
    "unexpectedly_missing",
    "unclassified",
]

_REPRESENTED_REASON_CODES = frozenset(
    {
        "direct_adapter",
        "hole_adapter",
        "pmi_split_member",
        "pocket_adapter",
        "slot_adapter",
    }
)
_ABSORBED_REASON_CODES = frozenset(
    {
        "grouped_hole_member",
        "hole_pattern_member",
        "pocket_pattern_member",
        "slot_pattern_member",
    }
)
_REASON_FAMILY = {
    "countersink_hole_owner": "countersinks",
    "grouped_hole_member": "holes",
    "hole_adapter": "holes",
    "hole_pattern_member": "holes",
    "pmi_split_member": "holes",
    "pocket_adapter": "pockets",
    "pocket_pattern_member": "pockets",
    "slot_adapter": "slots",
    "slot_pattern_member": "slots",
}
_NESTED_OWNER_FAMILY = {"countersink_hole_owner": "holes"}
_NESTED_OWNER_FIELD = {"countersink_hole_owner": "csink"}


@dataclass(frozen=True)
class OccurrenceBinding:
    """One exact accepted occurrence consumed by one exact final IR feature object."""

    occurrence: FeatureRef
    feature: object
    disposition: OwnershipDisposition = "represented"
    reason_code: str = "direct_adapter"
    # Run-local position within a grouped IR feature.  This is explicit lineage used to follow
    # later IR splits; it is not a topology index or a persistent report identifier.
    member_index: int | None = None


@dataclass(frozen=True)
class OccurrencePolicyOutcome:
    """One exact accepted occurrence with an explicit ownerless consumer policy."""

    occurrence: FeatureRef
    disposition: PolicyDisposition
    reason_code: str
    tracking: str | None


def _policy_outcomes(evidence: RecognitionEvidence) -> tuple[OccurrencePolicyOutcome, ...]:
    """Project the existing consumer capability declaration onto exact occurrences."""

    outcomes: list[OccurrencePolicyOutcome] = []
    for occurrence in evidence.features:
        policy = ownerless_occurrence_policy(evidence.family(occurrence))
        if policy is None:
            continue
        outcomes.append(
            OccurrencePolicyOutcome(
                occurrence=occurrence,
                disposition=policy.disposition,
                reason_code=policy.reason_code,
                tracking=policy.tracking,
            )
        )
    return tuple(outcomes)


@dataclass(frozen=True)
class RecognitionOwnership:
    """Immutable run-local ownership ledger paired with one evidence authority."""

    evidence: RecognitionEvidence
    expected_direct: tuple[FeatureRef, ...]
    expected_groupable: tuple[FeatureRef, ...]
    expected_nested: tuple[FeatureRef, ...]
    bindings: tuple[OccurrenceBinding, ...]
    policy_outcomes: tuple[OccurrencePolicyOutcome, ...]

    @property
    def owner_expected_occurrences(self) -> tuple[FeatureRef, ...]:
        """Supported occurrences whose adapters must record an IR owner."""

        return self.expected_direct + self.expected_groupable + self.expected_nested

    @property
    def expected_occurrences(self) -> tuple[FeatureRef, ...]:
        """Occurrences whose implemented consumer paths must produce an explicit outcome."""

        return self.owner_expected_occurrences + tuple(
            outcome.occurrence for outcome in self.policy_outcomes
        )

    def binding_for(self, occurrence: FeatureRef) -> OccurrenceBinding | None:
        """Return the ownership binding for an occurrence, validating its authority first."""

        self.evidence.family(occurrence)
        return next(
            (binding for binding in self.bindings if binding.occurrence is occurrence), None
        )

    def policy_for(self, occurrence: FeatureRef) -> OccurrencePolicyOutcome | None:
        """Return an ownerless policy outcome, validating its evidence authority first."""

        self.evidence.family(occurrence)
        return next(
            (outcome for outcome in self.policy_outcomes if outcome.occurrence is occurrence),
            None,
        )

    def outcome_for(
        self, occurrence: FeatureRef
    ) -> OccurrenceBinding | OccurrencePolicyOutcome | None:
        """Return the implemented outcome for one exact accepted occurrence."""

        binding = self.binding_for(occurrence)
        return binding if binding is not None else self.policy_for(occurrence)

    def status(self, occurrence: FeatureRef) -> OccurrenceStatus:
        """Classify only the ownership contracts implemented so far.

        ``unclassified`` is deliberately not a report disposition.  It is the migration state
        for remaining nested and classification-only families whose ownership rules are not
        implemented.
        """

        outcome = self.outcome_for(occurrence)
        if outcome is not None:
            return outcome.disposition
        if any(expected is occurrence for expected in self.owner_expected_occurrences):
            return "unexpectedly_missing"
        return "unclassified"

    @property
    def unexpectedly_missing(self) -> tuple[FeatureRef, ...]:
        """Supported occurrences for which conversion failed to record an IR owner."""

        return tuple(
            occurrence
            for occurrence in self.owner_expected_occurrences
            if self.binding_for(occurrence) is None
        )


class RecognitionOwnershipBuilder:
    """Mutable conversion-time collector; snapshot before attaching it to a drawing."""

    def __init__(self, evidence: RecognitionEvidence) -> None:
        if type(evidence) is not RecognitionEvidence:
            raise TypeError("evidence must be an exact RecognitionEvidence")
        self.evidence = evidence
        self._expected_direct = tuple(
            occurrence
            for occurrence in evidence.features
            if evidence.family(occurrence) in DIRECT_FAMILIES
        )
        self._expected_groupable = tuple(
            occurrence
            for occurrence in evidence.features
            if evidence.family(occurrence) in GROUPABLE_FAMILIES
        )
        self._expected_nested = tuple(
            occurrence
            for occurrence in evidence.features
            if evidence.family(occurrence) in NESTED_FAMILIES
        )
        self._by_record_identity: dict[int, list[tuple[FeatureRef, object]]] = {}
        for occurrence in self._expected_direct + self._expected_groupable + self._expected_nested:
            record = evidence.record(occurrence)
            self._by_record_identity.setdefault(id(record), []).append((occurrence, record))
        self._bindings: list[OccurrenceBinding] = []
        self._policy_outcomes = _policy_outcomes(evidence)
        expected_ids = {
            id(occurrence)
            for occurrence in (
                self._expected_direct + self._expected_groupable + self._expected_nested
            )
        }
        if any(id(outcome.occurrence) in expected_ids for outcome in self._policy_outcomes):
            raise RuntimeError("recognition occurrence has conflicting owner and policy contracts")
        self._bound_occurrence_ids: set[int] = set()
        self._owned_feature_ids: set[int] = set()

    @property
    def result(self):
        """The exact aggregate paired with this builder's evidence authority."""

        return self.evidence.result

    def _occurrence_for(self, record: object) -> FeatureRef:
        """Resolve only exact records issued by this evidence authority."""

        matches = [
            occurrence
            for occurrence, candidate in self._by_record_identity.get(id(record), ())
            if candidate is record
        ]
        if len(matches) != 1:
            reason = (
                "does not belong to a supported evidence occurrence"
                if not matches
                else "is ambiguous"
            )
            raise ValueError(f"recognition record {reason}")
        return matches[0]

    def bind(
        self,
        record: object,
        feature: object,
        *,
        reason_code: str = "direct_adapter",
        member_index: int | None = None,
    ) -> None:
        """Bind at the adapter decision site using exact run-local record identity."""

        occurrence = self._occurrence_for(record)
        if type(reason_code) is not str or reason_code not in _REPRESENTED_REASON_CODES:
            raise ValueError("unknown represented ownership reason_code")
        expected_family = _REASON_FAMILY.get(reason_code)
        actual_family = self.evidence.family(occurrence)
        if reason_code == "direct_adapter" and actual_family not in DIRECT_FAMILIES:
            raise ValueError("direct ownership reason_code requires a direct occurrence family")
        if expected_family is not None and actual_family != expected_family:
            raise ValueError("represented ownership reason_code does not match occurrence family")
        if id(occurrence) in self._bound_occurrence_ids:
            raise ValueError("recognition occurrence already has an IR owner")
        if id(feature) in self._owned_feature_ids:
            raise ValueError("IR feature already owns a recognition occurrence")
        if member_index is not None and member_index < 0:
            raise ValueError("member_index must be non-negative")
        self._bound_occurrence_ids.add(id(occurrence))
        self._owned_feature_ids.add(id(feature))
        self._bindings.append(
            OccurrenceBinding(
                occurrence,
                feature,
                reason_code=reason_code,
                member_index=member_index,
            )
        )

    def absorb(self, records: tuple[object, ...], feature: object, *, reason_code: str) -> None:
        """Record an explicit N:1 aggregate decision for exact member records."""

        if not records:
            raise ValueError("an absorbed aggregate needs at least one member record")
        if type(reason_code) is not str or reason_code not in _ABSORBED_REASON_CODES:
            raise ValueError("unknown absorbed ownership reason_code")
        reason = reason_code
        occurrences = tuple(self._occurrence_for(record) for record in records)
        expected_family = _REASON_FAMILY[reason]
        if any(self.evidence.family(occurrence) != expected_family for occurrence in occurrences):
            raise ValueError("absorbed ownership reason_code does not match occurrence family")
        if len({id(occurrence) for occurrence in occurrences}) != len(occurrences):
            raise ValueError("an absorbed aggregate repeats a recognition occurrence")
        if any(id(occurrence) in self._bound_occurrence_ids for occurrence in occurrences):
            raise ValueError("recognition occurrence already has an IR owner")
        if id(feature) in self._owned_feature_ids:
            raise ValueError("IR feature already owns a recognition occurrence")
        self._owned_feature_ids.add(id(feature))
        for member_index, occurrence in enumerate(occurrences):
            self._bound_occurrence_ids.add(id(occurrence))
            self._bindings.append(
                OccurrenceBinding(
                    occurrence,
                    feature,
                    disposition="absorbed",
                    reason_code=reason,
                    member_index=member_index,
                )
            )

    def absorb_nested(
        self,
        record: object,
        owner_record: object,
        *,
        reason_code: str,
    ) -> None:
        """Bind one exact nested occurrence to its exact parent's existing IR owner."""

        if type(reason_code) is not str or reason_code not in _NESTED_OWNER_FAMILY:
            raise ValueError("unknown nested ownership reason_code")
        occurrence = self._occurrence_for(record)
        owner_occurrence = self._occurrence_for(owner_record)
        if self.evidence.family(occurrence) != _REASON_FAMILY[reason_code]:
            raise ValueError("nested ownership reason_code does not match occurrence family")
        if self.evidence.family(owner_occurrence) != _NESTED_OWNER_FAMILY[reason_code]:
            raise ValueError("nested ownership reason_code does not match owner family")
        if getattr(owner_record, _NESTED_OWNER_FIELD[reason_code]) is not record:
            raise ValueError("nested recognition occurrence does not belong to its exact parent")
        if id(occurrence) in self._bound_occurrence_ids:
            raise ValueError("recognition occurrence already has an IR owner")
        owner_binding = next(
            (binding for binding in self._bindings if binding.occurrence is owner_occurrence),
            None,
        )
        if owner_binding is None:
            raise ValueError("nested recognition occurrence requires an existing parent owner")
        self._bound_occurrence_ids.add(id(occurrence))
        self._bindings.append(
            OccurrenceBinding(
                occurrence,
                owner_binding.feature,
                disposition="absorbed",
                reason_code=reason_code,
                member_index=owner_binding.member_index,
            )
        )

    def remap_feature(
        self,
        source: object,
        replacements: tuple[object, ...],
        source_member_groups: tuple[tuple[int, ...], ...] | None = None,
    ) -> None:
        """Follow one explicit IR-lowering lineage without reconstructing correspondence."""

        matches = [
            index for index, binding in enumerate(self._bindings) if binding.feature is source
        ]
        if not matches:
            return
        if source_member_groups is not None and len(source_member_groups) != len(replacements):
            raise ValueError("IR replacement groups must align with replacements")
        if source_member_groups is not None and len({id(item) for item in replacements}) != len(
            replacements
        ):
            raise ValueError("IR replacement groups must use distinct replacement objects")
        externally_owned_ids = {
            id(binding.feature) for binding in self._bindings if binding.feature is not source
        }
        if any(id(replacement) in externally_owned_ids for replacement in replacements):
            raise ValueError("lowered IR feature already owns a recognition occurrence")

        if source_member_groups is None:
            if len(replacements) == 1:
                replacement = replacements[0]
                for index in matches:
                    binding = self._bindings[index]
                    self._bindings[index] = OccurrenceBinding(
                        binding.occurrence,
                        replacement,
                        binding.disposition,
                        binding.reason_code,
                        binding.member_index,
                    )
            else:
                self._bindings = [
                    binding for binding in self._bindings if binding.feature is not source
                ]
        else:
            member_lineage: dict[int, tuple[object, int, int]] = {}
            for replacement, group in zip(replacements, source_member_groups, strict=True):
                for replacement_index, source_index in enumerate(group):
                    if source_index in member_lineage:
                        raise ValueError("IR replacement groups repeat a source member")
                    member_lineage[source_index] = (
                        replacement,
                        replacement_index,
                        len(group),
                    )
            rewritten: list[OccurrenceBinding] = []
            source_bindings = [self._bindings[index] for index in matches]
            singleton_source_index = (
                next(iter(member_lineage))
                if len(source_bindings) == 1 and len(member_lineage) == 1
                else None
            )
            for binding in self._bindings:
                if binding.feature is not source:
                    rewritten.append(binding)
                    continue
                binding_source_index = binding.member_index
                if binding_source_index is None:
                    binding_source_index = singleton_source_index
                lineage = (
                    member_lineage.get(binding_source_index)
                    if binding_source_index is not None
                    else None
                )
                if lineage is None:
                    continue
                replacement, replacement_index, replacement_group_size = lineage
                disposition = binding.disposition
                reason_code = binding.reason_code
                if binding.reason_code == "grouped_hole_member" and replacement_group_size == 1:
                    disposition = "represented"
                    reason_code = "pmi_split_member"
                rewritten.append(
                    OccurrenceBinding(
                        binding.occurrence,
                        replacement,
                        disposition,
                        reason_code,
                        replacement_index,
                    )
                )
            self._bindings = rewritten

        self._bound_occurrence_ids = {id(binding.occurrence) for binding in self._bindings}
        self._owned_feature_ids = {id(binding.feature) for binding in self._bindings}

    def snapshot(self) -> RecognitionOwnership:
        """Copy the current ledger without manufacturing owners for missing occurrences."""

        return RecognitionOwnership(
            evidence=self.evidence,
            expected_direct=self._expected_direct,
            expected_groupable=self._expected_groupable,
            expected_nested=self._expected_nested,
            bindings=tuple(self._bindings),
            policy_outcomes=self._policy_outcomes,
        )
