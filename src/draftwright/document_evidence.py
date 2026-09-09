"""Run-local document agreement over confirmed, physically addressed measurement claims."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from numbers import Real
from types import SimpleNamespace

from draftwright._core import _decode_hole_location_fact
from draftwright.fits import FitClass
from draftwright.linting.evidence import verify_measurement_claims
from draftwright.registry import AnnotationRegistry


@dataclass(frozen=True)
class DocumentClaim:
    """One confirmed annotation bound to one of its approved measurement meanings."""

    sheet: str
    owner: object
    parameter: str
    annotation: str
    address: tuple
    meaning: tuple
    rendered: tuple
    witnesses: tuple


@dataclass(frozen=True)
class DocumentClaimSnapshot:
    claims: tuple[DocumentClaim, ...]
    unknown: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class DocumentConflict:
    claims: tuple[DocumentClaim, ...]


def _point(value):
    return (
        isinstance(value, tuple)
        and len(value) == 3
        and all(
            isinstance(item, Real) and not isinstance(item, bool) and isfinite(item)
            for item in value
        )
    )


def _span(value):
    return isinstance(value, tuple) and len(value) == 2 and all(_point(point) for point in value)


def _same_point(first, second, *, normal=None):
    # Binding tolerates only arithmetic noise. Approved values/spans are retained
    # verbatim for semantic comparison; the physical-ledger 0.5 mm join is not used.
    return all(
        abs(a - b) <= 1e-9
        for axis, a, b in zip("xyz", first, second, strict=True)
        if axis != normal
    )


def _same_span(first, second):
    return (
        _span(first)
        and _span(second)
        and all(_same_point(a, b) for a, b in zip(first, second, strict=True))
    )


def _normalised_entry(entry):
    value, tolerance, span, axis, discriminator, member, angular = entry
    if isinstance(tolerance, FitClass):
        tolerance = replace(tolerance, show="class")
    return value, tolerance, span, axis, discriminator, member, angular


def _normalised_meanings(claim):
    result = []
    for entry in claim.meaning:
        if len(entry) != 7:
            return ()
        meaning = _normalised_entry(entry)
        if meaning not in result:
            result.append(meaning)
    return tuple(result)


def _location_address(claim, meaning, axis):
    if len(claim.approved) != len(claim.meaning):
        return None
    components = {
        approved.physical_location_component or f"{approved.role}.location.{axis}"
        for raw, approved in zip(claim.meaning, claim.approved, strict=True)
        if _normalised_entry(raw) == meaning and approved.is_location_measurement
    }
    if len(components) != 1 or axis not in {"x", "y", "z"} or not _span(meaning[2]):
        return None
    component = next(iter(components))
    # A location measures in the feature's transverse plane. The normal coordinate
    # may differ between the rendered opening witness and compiler's reference point;
    # exact feature/member identity and the complete approved meaning remain retained.
    point = tuple(
        0.0 if direction == meaning[3] else value
        for direction, value in zip("xyz", meaning[2][1], strict=True)
    )
    return "location", component, point


def _bind_claim(claim):
    meanings = _normalised_meanings(claim)
    if not meanings:
        return (), "compiled_meaning_unavailable"
    span, locations = claim.witnesses if len(claim.witnesses) == 2 else (None, ())
    location = any(entry.is_location_measurement for entry in claim.approved)
    if location and not locations:
        if len(meanings) != 1 or meanings[0][4] not in {"x", "y", "z"}:
            return (), "location_component_unavailable"
        address = _location_address(claim, meanings[0], meanings[0][4])
        if address is None:
            return (), "location_meaning_unbound"
        return ((address, meanings[0]),), None
    if locations:
        bound = []
        for component, point in locations:
            decoded = _decode_hole_location_fact((claim.owner, component, point))
            if (
                decoded is None
                or not _point(point)
                or component.rsplit(".", 1)[-1] not in {"x", "y", "z"}
            ):
                return (), "location_witness_invalid"
            measured_axis = component.rsplit(".", 1)[-1]
            candidates = tuple(
                meaning
                for meaning in meanings
                if _span(meaning[2])
                and meaning[4] in {None, measured_axis}
                and _same_point(meaning[2][1], point, normal=meaning[3])
            )
            if len(candidates) != 1:
                return (), "location_meaning_unbound"
            address = _location_address(claim, candidates[0], measured_axis)
            if address is None or address[1] != component:
                return (), "location_meaning_unbound"
            bound.append((address, candidates[0]))
        return tuple(bound), None
    if span is not None:
        if not _span(span):
            return (), "span_witness_invalid"
        candidates = tuple(meaning for meaning in meanings if _same_span(meaning[2], span))
        if len(candidates) != 1:
            return (), "span_meaning_unbound"
        return ((("span", candidates[0][2]), candidates[0]),), None
    if len(meanings) != 1:
        return (), "measurement_meaning_ambiguous"
    # A unique compiled interval has the same address with or without the optional
    # rendered-span rider. Otherwise dropping that rider could evade a conflict.
    address = ("span", meanings[0][2]) if _span(meanings[0][2]) else ()
    return ((address, meanings[0]),), None


def _narrowed_claim_confirmed(claim, address, meaning, registry):
    location_axis = address[1].rsplit(".", 1)[-1] if address and address[0] == "location" else None
    if len(_normalised_meanings(claim)) == 1 and location_axis is None:
        return True
    if registry is None or len(claim.approved) != len(claim.meaning):
        return False
    approved = tuple(
        entry
        for raw, entry in zip(claim.meaning, claim.approved, strict=True)
        if _normalised_entry(raw) == meaning
    )
    if not approved:
        return False
    isolated = AnnotationRegistry()
    isolated.add(
        registry.named(claim.annotation),
        claim.annotation,
        registry.view_of(claim.annotation),
        feature=claim.owner,
        measurement=approved[0].id,
    )
    outcomes = verify_measurement_claims(
        isolated,
        SimpleNamespace(locations=approved),
        location_components={claim.annotation: location_axis},
    )
    return bool(outcomes) and all(outcome.state == "confirmed" for outcome in outcomes)


def bind_document_claims(sheet, snapshot, registry=None) -> DocumentClaimSnapshot:
    """Narrow the existing measurement snapshot without changing edit comparison.

    A location component or recorded interval supplies an address within a coarse
    parameter. Labels, nominal equality and annotation names never supply that address.
    """
    claims: list[DocumentClaim] = []
    unknown = [(sheet, name, reason) for name, reason in snapshot.unknown]
    for claim in snapshot.claims:
        if not any(owner is claim.owner for owner in snapshot.owners):
            unknown.append((sheet, claim.annotation, "physical_owner_unavailable"))
            continue
        bindings, reason = _bind_claim(claim)
        if reason is not None:
            unknown.append((sheet, claim.annotation, reason))
            continue
        if not all(
            _narrowed_claim_confirmed(claim, address, meaning, registry)
            for address, meaning in bindings
        ):
            unknown.append((sheet, claim.annotation, "bound_claim_unconfirmed"))
            continue
        claims.extend(
            DocumentClaim(
                sheet,
                claim.owner,
                claim.parameter,
                claim.annotation,
                address,
                meaning,
                claim.rendered,
                claim.witnesses,
            )
            for address, meaning in bindings
        )
    return DocumentClaimSnapshot(tuple(claims), tuple(dict.fromkeys(unknown)))


def document_conflicts(snapshots) -> tuple[DocumentConflict, ...]:
    """Retain every disagreeing confirmed claim; no sheet or tighter tolerance wins."""
    groups: dict[tuple, list[DocumentClaim]] = {}
    for snapshot in snapshots:
        for claim in snapshot.claims:
            key = (id(claim.owner), claim.parameter, claim.address)
            groups.setdefault(key, []).append(claim)
    return tuple(
        DocumentConflict(tuple(claims))
        for claims in groups.values()
        if any(claim.meaning != claims[0].meaning for claim in claims[1:])
    )


@dataclass(frozen=True)
class DocumentSupportProof:
    """One complete producer-issued conjunction, retaining every carrying claim."""

    alternative: int
    carriers: tuple[tuple[DocumentClaim, ...], ...]
    witnesses: tuple[DocumentClaim, ...]


def _supports_claim(support, claim):
    if claim.owner is not support.feature or claim.parameter != support.parameter_id:
        return False
    if support.location_point is not None:
        if not claim.address or claim.address[0] != "location":
            return False
        if not _same_point(claim.address[2], support.location_point, normal=claim.meaning[3]):
            return False
        if support.axis is not None and not claim.address[1].endswith("." + support.axis):
            return False
    if support.lo is not None or support.hi is not None:
        from draftwright.linting.through_step_coverage import _interval_matches

        # This is the producer's attribution rule, not equality of engineering meaning.
        # The claim was separately bound and confirmed against its exact compiled span.
        return (
            bool(claim.address)
            and claim.address[0] == "span"
            and _interval_matches(support, claim.meaning[2])
        )
    if support.axis is not None and support.location_point is None:
        return claim.meaning[3] == support.axis
    return True


def _distinct_support_witnesses(carriers):
    # Match terms to physical claim addresses, not annotation names. Repeating one
    # dimension on another sheet cannot supply a missing second interval.
    assigned: dict[tuple, int] = {}
    selected: dict[int, DocumentClaim] = {}

    def assign(term, seen):
        for claim in carriers[term]:
            key = (id(claim.owner), claim.parameter, claim.address)
            if key in seen:
                continue
            seen.add(key)
            if key not in assigned or assign(assigned[key], seen):
                assigned[key] = term
                selected[term] = claim
                return True
        return False

    if all(assign(term, set()) for term in range(len(carriers))):
        return tuple(selected[term] for term in range(len(carriers)))
    return ()


def document_support_proofs(
    alternatives, snapshots, conflicts
) -> tuple[DocumentSupportProof, ...]:
    """Evaluate complete alternatives over confirmed ink, never over other credits.

    Reading only actual claims makes cyclic requirement recipes incapable of proving
    themselves. Empty rich recipes are explicitly unproved, not vacuous conjunctions.
    """
    conflicted = {id(claim) for conflict in conflicts for claim in conflict.claims}
    available = tuple(
        claim for snapshot in snapshots for claim in snapshot.claims if id(claim) not in conflicted
    )
    proofs = []
    for index, alternative in enumerate(alternatives):
        supports = tuple(getattr(alternative, "supports", alternative))
        if not supports:
            continue
        carriers = tuple(
            tuple(claim for claim in available if _supports_claim(support, claim))
            for support in supports
        )
        witnesses = _distinct_support_witnesses(carriers)
        if witnesses:
            proofs.append(DocumentSupportProof(index, carriers, witnesses))
    return tuple(proofs)
