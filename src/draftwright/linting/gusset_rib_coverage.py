"""Semantic completeness for Quiddity gusset-rib requirements (#1705)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from draftwright.linting._registry import satisfaction_ids, with_measurement_carriers
from draftwright.linting.issues import LintIssue, is_placement_drop
from draftwright.measurement_support import RequirementCarrier

GussetState = Literal[
    "placed", "satisfied_by_structured_note", "suppressed", "dropped", "missing", "unverifiable"
]


@dataclass(frozen=True)
class GussetRibRequirementOutcome:
    parameter_id: str
    state: GussetState
    requirement_count: int = 1
    features: tuple = ()
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)
    carriers: tuple[RequirementCarrier, ...] = field(default=(), kw_only=True)


def gusset_rib_requirement_outcomes(
    recognition, features, registry, omissions, *, evidence, ownership
):
    """Follow physical rib sizes and provider-proven group placement to final ink."""
    if recognition is None:
        return []
    refs = tuple(ref for ref in evidence.features if evidence.family(ref) == "gusset_ribs")
    grouped: dict[int, tuple[object, list[object]]] = {}
    unresolved = []
    for ref in refs:
        record = evidence.record(ref)
        binding = ownership.binding_for(ref)
        if binding is None or getattr(binding.feature, "kind", None) != "gusset_rib":
            unresolved.append(record)
            continue
        entry = grouped.setdefault(id(binding.feature), (binding.feature, []))
        entry[1].append(record)

    placed = {
        (identity.feature, identity.parameter)
        for name in registry.names()
        for identity in registry.measurement_of(name)
    }
    satisfied = {
        (identity.feature, identity.parameter)
        for identity in satisfaction_ids(registry)
        if identity.feature is not None
    }
    dropped = {
        (identity.feature, identity.parameter)
        for issue in registry.issues
        if is_placement_drop(issue)
        for identity in issue.measurement_ids
    }
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes = [
        GussetRibRequirementOutcome(
            "gusset_thickness.length", "unverifiable", source_records=(record,)
        )
        for record in unresolved
    ]
    for feature, records in grouped.values():
        parameters = tuple(parameter.parameter_id for parameter in feature.parameters())
        for parameter in parameters:
            identity = (feature, parameter)
            if identity in placed:
                state: GussetState = "placed"
            elif identity in satisfied:
                state = "satisfied_by_structured_note"
            elif identity in suppressed:
                state = "suppressed"
            elif identity in dropped:
                state = "dropped"
            else:
                state = "missing"
            count = len(records) if parameter.startswith(("gusset_thickness", "gusset_leg")) else 1
            outcomes.append(
                GussetRibRequirementOutcome(
                    parameter,
                    state,
                    requirement_count=count,
                    features=(feature,),
                    source_records=tuple(records),
                )
            )
    return with_measurement_carriers(outcomes, registry)


def lint_gusset_rib_coverage(
    part, *, recognition, features, registry, omissions=(), evidence, ownership, assembly=None
):
    """Report gusset requirements without duplicating explicit placement-drop issues."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped callout outcome",
        "unverifiable": "cannot be joined to its same-run IR owner",
    }
    return [
        LintIssue(
            severity=severity,
            code=f"gusset_rib_requirement_{outcome.state}",
            message=f"gusset-rib {outcome.parameter_id} {messages[outcome.state]}",
        )
        for outcome in gusset_rib_requirement_outcomes(
            recognition, features, registry, omissions, evidence=evidence, ownership=ownership
        )
        if outcome.state in messages
    ]
