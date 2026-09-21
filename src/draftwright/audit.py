"""Compare finished drawings and captured measurement claims.

``diff_builds`` reports named annotation losses, gains, changed labels and substitutions.
It never lets a candidate suppression explanation cancel a loss. The legacy explanation
join is approximate: feature kind plus parameter, sufficient for a hint but not ownership.

``compare_measurements`` checks named compiled claims using exact feature references shared
by the two declarations, or explicit caller-supplied feature pairs. Equal geometry, labels
and inventory order establish no correspondence. Unmatched owners and unconfirmed claims
produce unknown results. A caller-supplied pair asserts correspondence; the comparison
checks the measurement meaning under that assertion and does not verify physical identity.
Capture ``Drawing.measurement_snapshot()`` before mutating a live drawing.

``compare_assessments`` is the persisted cross-replay counterpart. It accepts only compatible
v2 replay assessments for the same STEP bytes, producer, and run options, then reports separate
requirement, lint, completeness, fidelity, layout, and availability deltas. A caller-supplied
fixed denominator exposes omissions shared by both inputs. It returns an evidence-derived
Pareto relationship for those independent axes, plus a deprecated policy decision for v1
consumers; it never constructs a composite score or inferred topology correspondence.

The comparison scope is named compiled measurements. It checks their recorded owner,
parameter, nominal value, tolerance, directional span and rendered claim text. A linear
dimension also retains its scale-normalised measured path length, keeping its text tied
to the quantity that annotation draws even when several claims share one coarse id.
Recorded per-annotation spans and location components retain distinctions hidden by a
coarse parameter id. These records remain source claims, not proof of a physical target.
It does not establish physical completeness, inspect unnamed annotations or certify an engineering
release. Use independent lint/requirement evidence alongside it. Claim verification retains
its own attribution limits, described in ``linting.evidence.verify_measurement_claims``.

The module imports no engine code. Drawings supply their public snapshots and registry
reads; no cross-run provider identity is reconstructed or serialized.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MeasurementClaim:
    """A named rendered claim and its compiled meaning, retained in one process."""

    owner: object
    parameter: str
    annotation: str
    meaning: tuple
    rendered: tuple
    witnesses: tuple = ()
    approved: tuple = field(default=(), repr=False, compare=False, kw_only=True)
    cell: tuple[int, int] | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class MeasurementCellUncertainty:
    """An unconfirmed measured cell; its table-level unknown remains compatible."""

    annotation: str
    cell: tuple[int, int]
    reason: str


@dataclass(frozen=True)
class MeasurementSnapshot:
    """A captured drawing read, with declaration-local references rather than durable IDs."""

    owners: tuple
    claims: tuple[MeasurementClaim, ...]
    unknown: tuple[tuple[str, str], ...] = ()
    cell_unknown: tuple[MeasurementCellUncertainty, ...] = field(default=(), kw_only=True)


@dataclass(frozen=True, order=True)
class ExpectedRequirement:
    """One fixed-denominator requirement expected in both replay assessments."""

    declaration_id: str
    parameter_id: str


@dataclass(frozen=True, order=True)
class IntentionalChange:
    """A caller-authorised semantic change, kept separate from incidental regressions."""

    declaration_id: str
    parameter_id: str
    reason: str


@dataclass(frozen=True)
class LayoutFindingIdentity:
    """Stable-enough selection of one reported layout defect across two replays."""

    code: str
    declaration_ids: tuple[str, ...] = ()
    annotation_names: tuple[str, ...] = ()


def compare_measurements(before, after, *, feature_pairs=()) -> dict:
    """Compare captured or finished drawings within an explicit declaration correspondence.

    Shared feature objects correspond automatically. ``feature_pairs`` may explicitly pair
    an owner from each snapshot; these pairs are caller assertions, never inferred from
    geometry or inventory order. Unknown ownership or unreadable claims prevent preservation.
    The scope is named compiled measurements, not physical completeness of the whole part.
    """
    if not isinstance(before, MeasurementSnapshot):
        before = before.measurement_snapshot()
    if not isinstance(after, MeasurementSnapshot):
        after = after.measurement_snapshot()
    before_owners = {id(owner): index for index, owner in enumerate(before.owners)}
    after_owners = {id(owner): index for index, owner in enumerate(after.owners)}
    correspondence = {key: key for key in before_owners.keys() & after_owners.keys()}
    paired_after = set(correspondence.values())
    for old, new in feature_pairs:
        old_id, new_id = id(old), id(new)
        if old_id not in before_owners or new_id not in after_owners:
            raise ValueError("feature pairs must name exact owners in the two snapshots")
        if correspondence.get(old_id) == new_id:
            continue
        if old_id in correspondence or new_id in paired_after:
            raise ValueError("feature correspondence must be one-to-one and unambiguous")
        correspondence[old_id] = new_id
        paired_after.add(new_id)

    unknown = [
        {"side": side, "annotation": name, "reason": reason}
        for side, snapshot in (("before", before), ("after", after))
        for name, reason in snapshot.unknown
    ]
    reverse = {new: old for old, new in correspondence.items()}

    def index_claims(snapshot, side):
        indexed: dict[tuple, list] = {}
        for claim in snapshot.claims:
            owner = id(claim.owner)
            paired = owner if side == "before" and owner in correspondence else reverse.get(owner)
            if paired is None:
                unknown.append(
                    {
                        "side": side,
                        "annotation": claim.annotation,
                        "reason": "owner_correspondence_unknown",
                    }
                )
                continue
            indexed.setdefault((paired, claim.parameter), []).append(claim)
        return indexed

    old_claims, new_claims = index_claims(before, "before"), index_claims(after, "after")

    def description(key, claims):
        return {
            "owner": before_owners[key[0]],
            "parameter_id": key[1],
            "annotations": sorted(claim.annotation for claim in claims),
        }

    lost, gained, changed = [], [], []
    old_unknown = {name for name, _reason in before.unknown}
    new_unknown = {name for name, _reason in after.unknown}
    for key in sorted(
        old_claims.keys() | new_claims.keys(), key=lambda key: (before_owners[key[0]], key[1])
    ):
        old, new = old_claims.get(key, []), new_claims.get(key, [])
        if not new:
            if not any(claim.annotation in new_unknown for claim in old):
                lost.append(description(key, old))
        elif not old:
            if not any(claim.annotation in old_unknown for claim in new):
                gained.append(description(key, new))
        else:
            remaining = list(new)
            for claim in old:
                match = next(
                    (
                        index
                        for index, candidate in enumerate(remaining)
                        if claim.meaning == candidate.meaning
                        and claim.rendered == candidate.rendered
                        and claim.witnesses == candidate.witnesses
                    ),
                    None,
                )
                if match is None:
                    break
                remaining.pop(match)
            else:
                if not remaining:
                    continue
            changed.append(description(key, old))
    if not before.claims and not after.claims:
        unknown.append({"side": "both", "annotation": None, "reason": "no_compiled_measurements"})
    return {
        "status": "changed"
        if lost or gained or changed
        else "unknown"
        if unknown
        else "preserved",
        "scope": "named_compiled_measurements",
        "lost": lost,
        "gained": gained,
        "changed": changed,
        "unknown": unknown,
    }


_GOOD_REQUIREMENT_STATES = frozenset({"confirmed", "satisfied"})
_ADVERSE_COMPLETENESS_STATES = (
    "dropped",
    "missing",
    "unsupported",
    "unverifiable",
)


def _frozen(value: Any) -> Any:
    """A deterministic, hashable form of JSON-like evidence."""

    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _frozen(item)) for key, item in value.items()))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_frozen(item) for item in value)
    return value


def _requirement_key(item) -> tuple[str, str]:
    declaration_id: object
    parameter_id: object
    if isinstance(item, (ExpectedRequirement, IntentionalChange)):
        declaration_id, parameter_id = item.declaration_id, item.parameter_id
    elif isinstance(item, Mapping):
        declaration_id, parameter_id = item.get("declaration_id"), item.get("parameter_id")
    elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) == 2:
        declaration_id, parameter_id = item
    else:
        raise TypeError("requirements must provide declaration_id and parameter_id")
    if not isinstance(declaration_id, str) or not declaration_id:
        raise ValueError("requirement declaration_id must be a non-empty string")
    if not isinstance(parameter_id, str) or not parameter_id:
        raise ValueError("requirement parameter_id must be a non-empty string")
    return declaration_id, parameter_id


def _compatibility_reasons(before: Mapping, after: Mapping) -> list[str]:
    reasons = []
    for side, document in (("baseline", before), ("candidate", after)):
        if document.get("schema") != "draftwright-replay-assessment":
            reasons.append(f"{side}_assessment_schema_unsupported")
        if document.get("schema_version") != 2:
            reasons.append(f"{side}_assessment_version_unsupported")
        drawing = document.get("drawing")
        if not isinstance(drawing, Mapping) or (
            drawing.get("schema") != "draftwright-report"
            or drawing.get("schema_version") != 8
            or drawing.get("scope") != "declared-sheet"
        ):
            reasons.append(f"{side}_drawing_report_incompatible")
        measurements = document.get("measurements")
        if not isinstance(measurements, Mapping) or (
            measurements.get("authority") != "confirmed-compiled-claims"
            or measurements.get("identity_scope") != "build-local-declarations"
        ):
            reasons.append(f"{side}_measurement_authority_incompatible")
    before_source, after_source = before.get("source"), after.get("source")
    if not isinstance(before_source, Mapping) or not isinstance(after_source, Mapping):
        reasons.append("source_identity_missing")
    elif before_source.get("kind") != "step" or after_source.get("kind") != "step":
        reasons.append("immutable_step_source_required")
    elif not before_source.get("sha256") or not after_source.get("sha256"):
        reasons.append("source_hash_missing")
    elif before_source.get("sha256") != after_source.get("sha256"):
        reasons.append("source_hash_mismatch")
    if before.get("producer") != after.get("producer"):
        reasons.append("producer_versions_mismatch")
    if before.get("run") != after.get("run"):
        reasons.append("run_options_mismatch")
    return sorted(set(reasons))


def _declarations(document: Mapping) -> dict[str, Mapping]:
    drawing = document["drawing"]
    rows = drawing["declarations"]["entries"]
    result = {}
    for row in rows:
        key = row["id"]
        if key in result:
            raise ValueError(f"duplicate declaration identity {key!r}")
        result[key] = row
    return result


def _measurement_rows(document: Mapping) -> dict[tuple[str, str], list[Mapping]]:
    result: dict[tuple[str, str], list[Mapping]] = {}
    for row in document["measurements"]["entries"]:
        key = (row["declaration_id"], row["parameter_id"])
        result.setdefault(key, []).append(row)
    for rows in result.values():
        rows.sort(key=_frozen)
    return result


def _carrier_rows(declarations: Mapping[str, Mapping]) -> dict[tuple[str, str], list[dict]]:
    result: dict[tuple[str, str], list[dict]] = {}
    for declaration_id, declaration in declarations.items():
        for representation in declaration["representations"]:
            for parameter in representation["measurements"]:
                result.setdefault((declaration_id, parameter), []).append(
                    {
                        "name": representation["name"],
                        "type": representation["type"],
                        "view": representation["view"],
                        "pinned": representation["pinned"],
                        "role": "measurement",
                    }
                )
            for parameter in representation["satisfactions"]:
                result.setdefault((declaration_id, parameter), []).append(
                    {
                        "name": representation["name"],
                        "type": representation["type"],
                        "view": representation["view"],
                        "pinned": representation["pinned"],
                        "role": "satisfaction",
                    }
                )
    for rows in result.values():
        rows.sort(key=_frozen)
    return result


def _requirement_side(
    key: tuple[str, str],
    declarations: Mapping[str, Mapping],
    measurements: Mapping[tuple[str, str], list[Mapping]],
    carriers: Mapping[tuple[str, str], list[dict]],
) -> dict:
    declaration = declarations.get(key[0])
    claims = measurements.get(key, [])
    representations = carriers.get(key, [])
    if claims:
        state = "confirmed"
    elif any(row["role"] == "satisfaction" for row in representations):
        state = "satisfied"
    elif representations:
        state = "unverifiable"
    elif declaration is not None and key[1] in declaration["parameters"]:
        state = "unrepresented"
    else:
        state = "absent"
    occurrence_ids = (
        [] if declaration is None else list(declaration["recognition"]["occurrence_ids"])
    )
    return {
        "state": state,
        "feature_kind": None if declaration is None else declaration["feature_kind"],
        "owner_id": None if declaration is None else declaration["owner"]["id"],
        "occurrence_ids": occurrence_ids,
        "identity": "physical-occurrences" if occurrence_ids else "declaration-only",
        "meanings": [row["meaning"] for row in claims],
        "rendered": [row["rendered"] for row in claims],
        "carriers": representations,
    }


def _finding_key(row: Mapping) -> tuple:
    return (
        row.get("code"),
        tuple(sorted(row.get("declaration_ids", ()))),
        tuple(sorted(row.get("annotation_names", ()))),
    )


def _layout_selection_key(selected: LayoutFindingIdentity | Mapping | None) -> tuple | None:
    if selected is None:
        return None
    code: object
    declarations: object
    annotations: object
    if isinstance(selected, LayoutFindingIdentity):
        code = selected.code
        declarations = selected.declaration_ids
        annotations = selected.annotation_names
    elif isinstance(selected, Mapping):
        code = selected.get("code")
        declarations = selected.get("declaration_ids", ())
        annotations = selected.get("annotation_names", ())
    else:
        raise TypeError("selected layout finding must be a LayoutFindingIdentity or mapping")
    if not isinstance(code, str) or not code:
        raise ValueError("selected layout finding code must be a non-empty string")
    if (
        not isinstance(declarations, Sequence)
        or isinstance(declarations, (str, bytes))
        or any(not isinstance(item, str) or not item for item in declarations)
    ):
        raise ValueError("selected layout declaration_ids must be non-empty strings")
    if (
        not isinstance(annotations, Sequence)
        or isinstance(annotations, (str, bytes))
        or any(not isinstance(item, str) or not item for item in annotations)
    ):
        raise ValueError("selected layout annotation_names must be non-empty strings")
    return code, tuple(sorted(declarations)), tuple(sorted(annotations))


def _lint_delta(before: Mapping, after: Mapping) -> dict:
    old_rows = before["drawing"]["lint"]["issues"]
    new_rows = after["drawing"]["lint"]["issues"]
    old, new = Counter(map(_frozen, old_rows)), Counter(map(_frozen, new_rows))
    examples: dict[Any, Mapping] = {}
    for row in (*old_rows, *new_rows):
        examples.setdefault(_frozen(row), row)

    def expanded(counts: Counter) -> list[Mapping]:
        return [examples[key] for key in sorted(counts, key=repr) for _index in range(counts[key])]

    return {
        "resolved": expanded(old - new),
        "introduced": expanded(new - old),
        "unchanged": expanded(old & new),
    }


def _component_delta(before: Mapping, after: Mapping, name: str) -> dict:
    old = before["drawing"]["lint"]["quality"][name]
    new = after["drawing"]["lint"]["quality"][name]
    return {"baseline": old, "candidate": new, "changed": old != new}


def _code_count_delta(before: Mapping, after: Mapping) -> dict:
    old = before.get("by_code", {})
    new = after.get("by_code", {})
    rows = [
        {"code": code, "baseline": int(old.get(code, 0)), "candidate": int(new.get(code, 0))}
        for code in sorted(set(old) | set(new))
    ]
    return {
        "resolved": [row for row in rows if row["candidate"] < row["baseline"]],
        "introduced": [row for row in rows if row["candidate"] > row["baseline"]],
        "unchanged": [row for row in rows if row["candidate"] == row["baseline"]],
    }


def _uncertainty_index(document: Mapping) -> dict[Any, dict[str, Any]]:
    """Index unresolved claims by the identity actually serialized in an assessment."""

    result: dict[Any, dict[str, Any]] = {}

    def add(identity: dict[str, Any], claim: Mapping) -> None:
        key = _frozen(identity)
        entry = result.setdefault(key, {"identity": identity, "claims": []})
        entry["claims"].append(dict(claim))

    measurements = document["measurements"]
    for row in measurements["unknown"]:
        identity: dict[str, Any] = {
            "kind": "measurement",
            "annotation": row["annotation"],
        }
        if row.get("cell") is not None:
            identity["cell"] = dict(row["cell"])
        add(identity, row)
    for row in measurements["unavailable_owner_claims"]:
        add(
            {
                "kind": "ownerless-claim",
                "annotation": row["annotation"],
                "parameter_id": row["parameter_id"],
            },
            row,
        )
    for entry in result.values():
        entry["claims"].sort(key=_frozen)
    return result


def _uncertainty_delta(before: Mapping, after: Mapping) -> dict:
    """Match uncertainty without treating array order or scalar equality as identity."""

    old, new = _uncertainty_index(before), _uncertainty_index(after)
    result: dict[str, list[dict[str, Any]]] = {
        "carried": [],
        "resolved": [],
        "introduced": [],
        "changed": [],
        "ambiguous": [],
    }
    for key in sorted(old.keys() | new.keys(), key=repr):
        old_entry, new_entry = old.get(key), new.get(key)
        entry = old_entry if old_entry is not None else new_entry
        assert entry is not None  # key came from the union of the two indexes
        identity = dict(entry["identity"])
        old_claims = [] if old_entry is None else old_entry["claims"]
        new_claims = [] if new_entry is None else new_entry["claims"]
        if len(old_claims) > 1 or len(new_claims) > 1:
            result["ambiguous"].append(
                {
                    "identity": identity,
                    "baseline_count": len(old_claims),
                    "candidate_count": len(new_claims),
                }
            )
        elif old_claims and new_claims:
            if _frozen(old_claims[0]) == _frozen(new_claims[0]):
                result["carried"].append({"identity": identity, "claim": old_claims[0]})
            else:
                result["changed"].append(
                    {
                        "identity": identity,
                        "baseline": old_claims[0],
                        "candidate": new_claims[0],
                    }
                )
        elif old_claims:
            result["resolved"].append({"identity": identity, "claim": old_claims[0]})
        else:
            result["introduced"].append({"identity": identity, "claim": new_claims[0]})
    for rows in result.values():
        rows.sort(key=_frozen)
    return result


def _axis_result(
    *,
    improvements: Sequence[Mapping] = (),
    regressions: Sequence[Mapping] = (),
    unavailable_reasons: Sequence[str] = (),
) -> dict:
    """Describe one independently evidenced comparison axis without a numeric score."""

    improved = sorted((dict(row) for row in improvements), key=_frozen)
    regressed = sorted((dict(row) for row in regressions), key=_frozen)
    unavailable = sorted(set(unavailable_reasons))
    if improved and regressed:
        relation = "incomparable"
    elif regressed:
        relation = "regressed"
    elif improved:
        relation = "improved"
    elif unavailable:
        relation = "unavailable"
    else:
        relation = "unchanged"
    return {
        "relation": relation,
        "improvements": improved,
        "regressions": regressed,
        "unavailable_reasons": unavailable,
    }


def _pareto_result(axes: Mapping[str, Mapping] | None, limitations: Sequence[str]) -> dict:
    """Return the partial order across comparable axes; never weight or average them."""

    names_by_relation = {
        relation: sorted(
            name for name, axis in (axes or {}).items() if axis["relation"] == relation
        )
        for relation in (
            "improved",
            "regressed",
            "incomparable",
            "unchanged",
            "unavailable",
        )
    }
    limitations = sorted(set(limitations))
    if limitations or axes is None:
        relation = "unavailable"
    elif names_by_relation["incomparable"] or (
        names_by_relation["improved"] and names_by_relation["regressed"]
    ):
        relation = "incomparable"
    elif names_by_relation["regressed"]:
        relation = "dominated"
    elif names_by_relation["improved"]:
        relation = "dominates"
    elif names_by_relation["unchanged"]:
        relation = "equivalent"
    else:
        relation = "unavailable"
    return {
        "relation": relation,
        "basis": "evidence-vector-no-scalar",
        "orientation": "candidate-versus-baseline",
        "improved_axes": names_by_relation["improved"],
        "regressed_axes": names_by_relation["regressed"],
        "incomparable_axes": names_by_relation["incomparable"],
        "unchanged_axes": names_by_relation["unchanged"],
        "unavailable_axes": names_by_relation["unavailable"],
        "limitations": limitations,
    }


def compare_assessments(
    baseline: Mapping,
    candidate: Mapping,
    *,
    expected_requirements: Sequence[ExpectedRequirement | Mapping | tuple] = (),
    intentional_changes: Sequence[IntentionalChange | Mapping] = (),
    selected_layout_finding: LayoutFindingIdentity | Mapping | None = None,
) -> dict:
    """Compare two exact replay assessments using separate axes and a Pareto relation.

    Compatibility is fail-closed: both documents must be v2 assessments for the same immutable
    STEP bytes, producer versions, and run options. ``expected_requirements`` is the caller's
    fixed denominator and is the only way to expose a requirement omitted from both drawings.
    Authorised changes are reported separately; they do not excuse unrelated regressions.
    Unresolved claims match only by their serialized annotation/cell or annotation/parameter
    identity; duplicates refuse comparison instead of pairing by order.
    """

    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        raise TypeError("assessment comparison requires two mappings")
    compatibility = _compatibility_reasons(baseline, candidate)
    base = {
        "schema": "draftwright-assessment-comparison",
        "schema_version": 2,
        "scope": "same-source-replay-delta",
        "decision": "incomparable" if compatibility else "no-preference",
        "reasons": compatibility,
        "compatibility": {
            "comparable": not compatibility,
            "reasons": compatibility,
        },
        "restraint": {
            "availability": "unavailable",
            "reason": "physical requirement equivalence is not established by this comparison",
        },
        "manufacturing_readiness": {
            "availability": "unavailable",
            "reason": "material, process, finish, fit, and tolerance intent are not certified",
        },
    }
    if compatibility:
        return {
            **base,
            "axes": None,
            "pareto": _pareto_result(None, compatibility),
            "uncertainty": None,
            "lint": None,
            "requirements": None,
            "completeness": None,
            "fidelity": None,
            "layout": None,
            "unscored": None,
            "unavailable": {"reasons": compatibility, "measurement_claims": []},
            "intentional_changes": [],
            "policy": None,
        }

    before_declarations, after_declarations = (
        _declarations(baseline),
        _declarations(candidate),
    )
    before_measurements, after_measurements = (
        _measurement_rows(baseline),
        _measurement_rows(candidate),
    )
    before_carriers, after_carriers = (
        _carrier_rows(before_declarations),
        _carrier_rows(after_declarations),
    )
    expected = {_requirement_key(item) for item in expected_requirements}
    authorised = {}
    for item in intentional_changes:
        key = _requirement_key(item)
        reason = item.reason if isinstance(item, IntentionalChange) else item.get("reason")
        if not isinstance(reason, str) or not reason:
            raise ValueError("intentional change reason must be a non-empty string")
        if key in authorised:
            raise ValueError(f"duplicate intentional change for {key!r}")
        authorised[key] = reason
    keys = sorted(
        expected
        | before_measurements.keys()
        | after_measurements.keys()
        | before_carriers.keys()
        | after_carriers.keys()
    )

    transitions: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    unavailable: list[str] = []
    applied_changes: list[dict[str, Any]] = []
    for key in keys:
        old = _requirement_side(key, before_declarations, before_measurements, before_carriers)
        new = _requirement_side(key, after_declarations, after_measurements, after_carriers)
        changes = []
        if old["state"] != new["state"]:
            changes.append("state")
        if old["feature_kind"] != new["feature_kind"]:
            changes.append("feature_kind")
        if old["occurrence_ids"] != new["occurrence_ids"]:
            changes.append("physical_owner")
        if old["meanings"] != new["meanings"]:
            changes.append("engineering_meaning")
        if old["rendered"] != new["rendered"]:
            changes.append("rendered_claim")
        old_pins = {row["name"]: row["pinned"] for row in old["carriers"]}
        new_pins = {row["name"]: row["pinned"] for row in new["carriers"]}
        if any(old_pins[name] != new_pins[name] for name in old_pins.keys() & new_pins.keys()):
            changes.append("pin_state")
        old_representations = [
            {key: value for key, value in row.items() if key != "pinned"}
            for row in old["carriers"]
        ]
        new_representations = [
            {key: value for key, value in row.items() if key != "pinned"}
            for row in new["carriers"]
        ]
        if old_representations != new_representations:
            changes.append("representation")
        authorised_reason = authorised.get(key)
        row: dict[str, Any] = {
            "declaration_id": key[0],
            "parameter_id": key[1],
            "expected": key in expected,
            "baseline": old,
            "candidate": new,
            "changes": changes,
            "intentional_change": authorised_reason,
        }
        transitions.append(row)
        retained_claim = bool(old["meanings"] and new["meanings"])
        retained_requirement = (
            old["state"] in _GOOD_REQUIREMENT_STATES and new["state"] in _GOOD_REQUIREMENT_STATES
        )
        semantic_regression = (
            (
                old["state"] in _GOOD_REQUIREMENT_STATES
                and new["state"] not in _GOOD_REQUIREMENT_STATES
            )
            or (key in expected and new["state"] not in _GOOD_REQUIREMENT_STATES)
            or (retained_requirement and "physical_owner" in changes)
            or (retained_requirement and "feature_kind" in changes)
            or (retained_claim and "engineering_meaning" in changes)
            or "pin_state" in changes
        )
        if authorised_reason is not None and changes:
            applied_changes.append(
                {
                    "declaration_id": key[0],
                    "parameter_id": key[1],
                    "reason": authorised_reason,
                    "changes": changes,
                }
            )
        elif semantic_regression:
            blockers.append(
                {
                    "code": "requirement_regression",
                    "declaration_id": key[0],
                    "parameter_id": key[1],
                    "changes": changes or ["fixed_denominator_omission"],
                }
            )
        elif (
            old["state"] not in _GOOD_REQUIREMENT_STATES
            and new["state"] in _GOOD_REQUIREMENT_STATES
        ):
            improvements.append(
                {
                    "code": "requirement_resolved",
                    "declaration_id": key[0],
                    "parameter_id": key[1],
                }
            )
    lint = _lint_delta(baseline, candidate)
    for issue in lint["introduced"]:
        if issue.get("severity") == "error":
            blockers.append({"code": "error_lint_introduced", "finding": issue})

    before_quality = baseline["drawing"]["lint"]["quality"]
    after_quality = candidate["drawing"]["lint"]["quality"]
    before_completeness = before_quality["completeness"]
    after_completeness = after_quality["completeness"]
    completeness_improvements: list[dict[str, Any]] = []
    completeness_regressions: list[dict[str, Any]] = []
    completeness_changes: dict[str, dict[str, int]] = {}
    for state in _ADVERSE_COMPLETENESS_STATES:
        old_count = int(before_completeness.get(state, 0))
        new_count = int(after_completeness.get(state, 0))
        completeness_changes[state] = {"baseline": old_count, "candidate": new_count}
        if new_count > old_count:
            regression = {
                "code": "adverse_completeness_outcome_introduced",
                "state": state,
                "count": new_count - old_count,
            }
            blockers.append(regression)
            completeness_regressions.append(regression)
        elif new_count < old_count:
            completeness_improvements.append(
                {
                    "code": "adverse_completeness_outcome_resolved",
                    "state": state,
                    "count": old_count - new_count,
                }
            )
    old_known = int(before_completeness.get("known_requirement_count", 0))
    new_known = int(after_completeness.get("known_requirement_count", 0))
    if new_known < old_known:
        regression = {
            "code": "recognized_requirement_denominator_shrank",
            "baseline": old_known,
            "candidate": new_known,
        }
        blockers.append(regression)
        completeness_regressions.append(regression)
    old_unscored_families = set(before_completeness.get("unscored_recognized_families", ()))
    new_unscored_families = set(after_completeness.get("unscored_recognized_families", ()))
    if new_unscored_families - old_unscored_families:
        regression = {
            "code": "recognized_family_became_unscored",
            "families": sorted(new_unscored_families - old_unscored_families),
        }
        blockers.append(regression)
        completeness_regressions.append(regression)
    old_unrecognised = int(before_completeness.get("unrecognised_geometry_reports", 0))
    new_unrecognised = int(after_completeness.get("unrecognised_geometry_reports", 0))
    if new_unrecognised > old_unrecognised:
        regression = {
            "code": "unrecognised_geometry_reports_increased",
            "baseline": old_unrecognised,
            "candidate": new_unrecognised,
        }
        blockers.append(regression)
        completeness_regressions.append(regression)
    before_completeness_available = bool(before_completeness.get("available"))
    after_completeness_available = bool(after_completeness.get("available"))
    completeness_unavailable: list[str] = []
    if before_completeness_available and not after_completeness_available:
        completeness_regressions.append({"code": "completeness_became_unavailable"})
    elif not before_completeness_available and after_completeness_available:
        completeness_improvements.append({"code": "completeness_became_available"})
    elif not before_completeness_available:
        completeness_unavailable.append("completeness unavailable in both assessments")
    old_unknown_cardinality = int(before_completeness.get("unknown_cardinality_rows", 0))
    new_unknown_cardinality = int(after_completeness.get("unknown_cardinality_rows", 0))
    if new_unknown_cardinality > old_unknown_cardinality:
        completeness_regressions.append(
            {
                "code": "requirement_cardinality_uncertainty_introduced",
                "count": new_unknown_cardinality - old_unknown_cardinality,
            }
        )
    elif new_unknown_cardinality < old_unknown_cardinality:
        completeness_improvements.append(
            {
                "code": "requirement_cardinality_uncertainty_resolved",
                "count": old_unknown_cardinality - new_unknown_cardinality,
            }
        )
    if not after_completeness.get("available"):
        unavailable.append("candidate completeness is unavailable")
    if after_completeness.get("unknown_cardinality_rows", 0):
        unavailable.append("candidate requirement cardinality is unknown")

    fidelity = _component_delta(baseline, candidate, "fidelity")
    old_fidelity, new_fidelity = fidelity["baseline"], fidelity["candidate"]
    fidelity["target_validation"] = _code_count_delta(old_fidelity, new_fidelity)
    fidelity_improvements = [
        {"code": "fidelity_finding_resolved", "finding": row}
        for row in fidelity["target_validation"]["resolved"]
    ]
    fidelity_regressions = [
        {"code": "fidelity_finding_introduced", "finding": row}
        for row in fidelity["target_validation"]["introduced"]
    ]
    fidelity_unavailable: list[str] = []
    before_fidelity_available = bool(old_fidelity.get("available"))
    after_fidelity_available = bool(new_fidelity.get("available"))
    if before_fidelity_available and not after_fidelity_available:
        fidelity_regressions.append({"code": "fidelity_became_unavailable"})
    elif not before_fidelity_available and after_fidelity_available:
        fidelity_improvements.append({"code": "fidelity_became_available"})
    elif not before_fidelity_available:
        fidelity_unavailable.append("fidelity unavailable in both assessments")
    if not new_fidelity.get("available"):
        unavailable.append("candidate fidelity is unavailable")
    elif fidelity["target_validation"]["introduced"]:
        blockers.append({"code": "fidelity_regression"})

    unscored = _component_delta(baseline, candidate, "unscored")
    old_unclassified = set(unscored["baseline"].get("unclassified", ()))
    new_unclassified = set(unscored["candidate"].get("unclassified", ()))
    if new_unclassified - old_unclassified:
        blockers.append(
            {
                "code": "unclassified_lint_introduced",
                "codes": sorted(new_unclassified - old_unclassified),
            }
        )

    old_layout = {_finding_key(row): row for row in baseline["drawing"]["layout"]["findings"]}
    new_layout = {_finding_key(row): row for row in candidate["drawing"]["layout"]["findings"]}
    selected = _layout_selection_key(selected_layout_finding)
    layout: dict[str, Any] = {
        "selected": None
        if selected is None
        else {
            "code": selected[0],
            "declaration_ids": list(selected[1]),
            "annotation_names": list(selected[2]),
        },
        "resolved": [old_layout[key] for key in sorted(old_layout.keys() - new_layout.keys())],
        "introduced": [new_layout[key] for key in sorted(new_layout.keys() - old_layout.keys())],
        "unchanged": [old_layout[key] for key in sorted(old_layout.keys() & new_layout.keys())],
        "selected_transition": "not-selected",
    }
    for finding in layout["introduced"]:
        blockers.append({"code": "layout_finding_introduced", "finding": finding})
    if selected is not None:
        was, now = selected in old_layout, selected in new_layout
        layout["selected_transition"] = (
            "resolved"
            if was and not now
            else "introduced"
            if now and not was
            else "unchanged"
            if was and now
            else "absent"
        )
        if was and not now:
            improvements.append({"code": "selected_layout_finding_resolved"})

    uncertainty = _uncertainty_delta(baseline, candidate)
    candidate_unknown = [
        {"side": "candidate", **row}
        for field in ("unknown", "unavailable_owner_claims")
        for row in candidate["measurements"][field]
    ]
    candidate_unknown.sort(key=_frozen)
    if candidate_unknown:
        unavailable.append("one or more compiled measurement claims are unresolved")
    if uncertainty["introduced"]:
        blockers.append(
            {
                "code": "measurement_uncertainty_introduced",
                "claims": uncertainty["introduced"],
            }
        )
    if uncertainty["changed"]:
        blockers.append(
            {
                "code": "measurement_uncertainty_changed",
                "claims": uncertainty["changed"],
            }
        )
    if uncertainty["ambiguous"]:
        unavailable.append("measurement uncertainty identity is ambiguous")
        blockers.append(
            {
                "code": "measurement_uncertainty_identity_ambiguous",
                "claims": uncertainty["ambiguous"],
            }
        )

    blockers.sort(key=_frozen)
    improvements.sort(key=_frozen)
    unavailable = sorted(set(unavailable))
    applied_keys = {(row["declaration_id"], row["parameter_id"]) for row in applied_changes}
    applied_changes.extend(
        {
            "declaration_id": key[0],
            "parameter_id": key[1],
            "reason": reason,
            "changes": [],
            "status": "no-observed-change",
        }
        for key, reason in authorised.items()
        if key not in applied_keys
    )
    for row in applied_changes:
        row.setdefault("status", "applied")
    applied_changes.sort(key=_frozen)

    requirement_regressions = [
        row
        for row in blockers
        if row["code"] == "requirement_regression"
        and row.get("changes") != ["fixed_denominator_omission"]
    ]
    requirement_improvements = [
        row for row in improvements if row["code"] == "requirement_resolved"
    ]
    axes = {
        "requirements": _axis_result(
            improvements=requirement_improvements,
            regressions=requirement_regressions,
        ),
        "completeness": _axis_result(
            improvements=completeness_improvements,
            regressions=completeness_regressions,
            unavailable_reasons=completeness_unavailable,
        ),
        "fidelity": _axis_result(
            improvements=fidelity_improvements,
            regressions=fidelity_regressions,
            unavailable_reasons=fidelity_unavailable,
        ),
        "legibility": _axis_result(
            improvements=layout["resolved"],
            regressions=layout["introduced"],
        ),
        "restraint": _axis_result(
            unavailable_reasons=(
                "physical requirement equivalence is not established by this comparison",
            )
        ),
    }
    fidelity_introduced_codes = {
        row["code"] for row in fidelity["target_validation"]["introduced"]
    }
    layout_introduced_codes = {row["code"] for row in layout["introduced"]}
    pareto_limitations = [
        row["code"]
        for row in blockers
        if row["code"]
        in {
            "measurement_uncertainty_changed",
            "measurement_uncertainty_identity_ambiguous",
            "measurement_uncertainty_introduced",
            "unclassified_lint_introduced",
        }
        or (
            row["code"] == "error_lint_introduced"
            and row["finding"].get("code")
            not in fidelity_introduced_codes | layout_introduced_codes
        )
    ]
    pareto = _pareto_result(axes, pareto_limitations)
    if blockers:
        decision = "rejected"
        reasons: list[str] = [row["code"] for row in blockers]
    elif improvements and not unavailable:
        decision = "preferred"
        reasons = [row["code"] for row in improvements]
    else:
        decision = "no-preference"
        reasons = unavailable or ["no_evidence-backed_improvement"]
    return {
        **base,
        "decision": decision,
        "reasons": reasons,
        "axes": axes,
        "pareto": pareto,
        "uncertainty": uncertainty,
        "lint": lint,
        "policy": {"blockers": blockers, "improvements": improvements},
        "requirements": {
            "denominator": "caller-fixed-plus-observed",
            "expected": [
                {"declaration_id": declaration, "parameter_id": parameter}
                for declaration, parameter in sorted(expected)
            ],
            "transitions": transitions,
            "blockers": [row for row in blockers if row["code"] == "requirement_regression"],
            "improvements": [row for row in improvements if row["code"] == "requirement_resolved"],
        },
        "completeness": {
            "baseline": before_completeness,
            "candidate": after_completeness,
            "adverse_outcome_changes": completeness_changes,
            "known_requirement_count": {"baseline": old_known, "candidate": new_known},
        },
        "fidelity": fidelity,
        "layout": layout,
        "unscored": unscored,
        "unavailable": {"reasons": unavailable, "measurement_claims": candidate_unknown},
        "intentional_changes": applied_changes,
    }


#: Sheet FURNITURE — the annotation types that carry no measurement. Everything else counts.
#:
#: A denylist, not an allowlist, and the polarity is the point (Codex #1001). An allowlist of
#: {"Dimension", "Leader"} silently dropped `SafeDimension`, a real measurement-bearing class,
#: and would drop every future dimensional type and subclass the same way. For a tool whose
#: one job is not to hide a loss, an unknown type must fail toward NOISE — reported and
#: dismissed by a reader — never toward silence. Adding a genuinely new furniture type here is
#: a deliberate act; forgetting to add a new measurement type to an allowlist was an accident
#: waiting to happen, and had already happened once.
_FURNITURE = frozenset({"TitleBlock", "Note", "CenterMark", "ProjectionSymbol"})


def _measurements(dwg) -> dict[str, str]:
    """``{annotation name: label}`` for everything that is not furniture."""
    out: dict[str, str] = {}
    for name, type_name in dwg.annotations().items():
        if type_name in _FURNITURE:
            continue
        # NO non-empty-label requirement. Generated hole leaders carry semantic labels, but
        # an external/legacy callout may still expose only presence. Requiring a label dropped
        # those from the comparison entirely, so a vanished callout produced NO loss: the one
        # thing this must never do (#996). Presence is the floor; the label is extra detail.
        label = getattr(dwg.get_annotation(name), "label", None)
        out[name] = "" if label is None else str(label)
    return out


def _rows(dwg) -> set[tuple]:
    return {(r["feature"], r["parameter_id"], r["reason"]) for r in dwg.suppressions()}


def _suppression_sort_key(row: tuple) -> tuple:
    """Total presentation order for public suppression rows, whose feature is nullable."""
    feature, parameter, reason = row
    return (
        (feature is not None, feature or ""),
        parameter,
        (reason is not None, reason or ""),
    )


def _correspondence(feature, parameter) -> tuple:
    """The cross-build key: ``(feature KIND, parameter_id)``.

    The full ledger key is ``(feature_key, parameter_id)``, and `feature_key` embeds the
    feature's ORIGIN AND SCALARS — by design, so two holes in one drawing are distinct. That
    makes it useless for comparing two DIFFERENT builds, which is the only thing this module
    does: widen a box 40→50 and the envelope's key changes, so an exact join finds nothing
    and every real suppression reads "nothing claims it" (Codex #1002 r1, reproduced).

    The kind survives the perturbation; the parameter is already stable. Weaker than full
    identity — two features of one kind share a key — but a weak key that MATCHES ACROSS
    BUILDS beats an exact key that cannot. It is safe here precisely because attribution
    only ever annotates a loss; nothing downstream cancels an alarm on it.
    """
    return (str(feature).split("@")[0], parameter)


def _identities(dwg, name) -> Counter:
    """Cross-build correspondence keys for everything *name* draws; empty if unrecorded.

    A **multiset**, not a set (Codex #1002 r5). The whole reason the registry stores a tuple
    is that one annotation can draw several measurements — a grouped ``4× R5`` fillet callout
    draws four. Deduplicating them here threw that away: a grouped callout dropping from four
    members to three keeps the same *distinct* key, so the change vanished and every result
    map came back empty. The counts survive the cross-build key even though the coordinates
    do not, so multiplicity is exactly the part of the tuple worth keeping.
    """
    if not hasattr(dwg, "measurement_keys"):
        return Counter()
    return Counter(
        _correspondence(k["feature"], k["parameter_id"]) for k in dwg.measurement_keys(name)
    )


def diff_builds(before, after) -> dict:
    """Compare two finished drawings: what was drawn, and what the compiler declined.

    *before* and *after* are two builds differing in one property — a square part and a
    near-square one, a feature added, a dimension authored. Returns:

    - ``dimensions_lost`` / ``dimensions_gained`` — ``{name: label}``. Nothing *downstream*
      filters this list; it is the alarm. It is not a completeness guarantee — see the
      admission limits at the top of the module, which bound what reaches it at all.
    - ``dimensions_changed`` — ``{name: (before, after)}`` where the annotation survived but
      its label did not. Reported, not alarmed: in a perturbation study a changed value is the
      expected result of the change, so ranking it with the losses would bury them in noise
      the experiment itself creates.
    - ``suppressions_gained`` / ``suppressions_lost`` — ledger rows as
      ``(feature, parameter_id, reason)``.
    - ``candidate_explanations`` — ``{lost name: [reason, ...]}``, a **hint** at which
      newly-gained suppression might account for a loss, joined on ``(feature kind,
      parameter_id)`` where the renderer recorded identity, and absent where it did not.
    - ``measurement_comparison`` — the bounded ``compare_measurements`` result for real
      drawings; ``None`` for protocol stand-ins without measurement snapshots. An empty
      annotation diff does not establish preservation when this result is unknown.

    The hint does not subtract from ``dimensions_lost``. The join is by feature KIND, so it
    cannot separate two features of one kind, and a weak match that cancels an alarm is worse
    than no match at all — it manufactures the confidence this epic exists to remove.
    """
    before_dims, after_dims = _measurements(before), _measurements(after)
    comparison = None
    exact_owners = {}
    if hasattr(before, "measurement_snapshot") and hasattr(after, "measurement_snapshot"):
        before_snapshot, after_snapshot = (
            before.measurement_snapshot(),
            after.measurement_snapshot(),
        )
        comparison = compare_measurements(before_snapshot, after_snapshot)
        after_owners = {id(owner) for owner in after_snapshot.owners}
        exact_owners = {
            id(owner): f"{getattr(owner, 'kind', type(owner).__name__)}#{index}"
            for index, owner in enumerate(before_snapshot.owners)
            if id(owner) in after_owners
        }
    lost = {n: v for n, v in before_dims.items() if n not in after_dims}
    gained = {n: v for n, v in after_dims.items() if n not in before_dims}
    changed = {n: (v, after_dims[n]) for n, v in before_dims.items() if after_dims.get(n, v) != v}

    before_rows, after_rows = _rows(before), _rows(after)
    gained_supp = sorted(after_rows - before_rows, key=_suppression_sort_key)
    lost_supp = sorted(before_rows - after_rows, key=_suppression_sort_key)

    # A name present in BOTH builds that now draws a DIFFERENT measurement (#1002) — the
    # module's worst blind spot closed. An annotation name is an engine-assigned slot, so a
    # substitution under the same name (and, if the labels agree, under the same label)
    # previously produced an entirely empty diff.
    #
    # Compared on the CORRESPONDENCE key, so a `width.length` on an envelope that merely got
    # wider is not a substitution. An earlier cut compared the full ledger key and reported
    # every envelope dim of every perturbed build as "reattributed" — three noise lines on a
    # three-dimension drawing, in the one experiment this module exists to run.
    #
    # Hole location IDs carry the declared member and measured axis. This detects component
    # substitutions even when the rendered name and value agree. The feature-kind join still
    # cannot distinguish same-kind owners. Exact shared declaration references below can;
    # unrelated builds retain an explicitly unknown measurement comparison.
    substituted: dict[str, tuple] = {}
    for name in set(before_dims) & set(after_dims):
        b_ids, a_ids = _identities(before, name), _identities(after, name)
        if b_ids and a_ids and b_ids != a_ids:
            substituted[name] = (sorted(b_ids.elements()), sorted(a_ids.elements()))
        elif exact_owners:

            def exact(drawing):
                identities = drawing.registry.measurement_of(name)
                if not identities or any(
                    id(item.feature) not in exact_owners for item in identities
                ):
                    return None
                return Counter(
                    (exact_owners[id(item.feature)], item.parameter) for item in identities
                )

            exact_before, exact_after = exact(before), exact(after)
            if (
                exact_before is not None
                and exact_after is not None
                and exact_before != exact_after
            ):
                substituted[name] = (
                    sorted(exact_before.elements()),
                    sorted(exact_after.elements()),
                )

    # Attribution, on the cross-build correspondence key. The first cut matched a
    # suppression's parameter stem against the annotation's NAME by substring, so a
    # newly-suppressed `width.length` claimed every lost annotation whose name contained
    # "width" — across unrelated features (Codex #1001 r1). The second joined on the exact
    # ledger key, which cannot match across two builds at all (Codex #1002 r1). This joins
    # on what the two builds genuinely share: the feature's kind and the parameter.
    candidates: dict[str, list[str]] = {}
    for name in lost:
        idents = _identities(before, name)
        if not idents:
            continue  # unknown identity — no attribution rather than a guessed one
        hits = [
            reason
            for feature, parameter, reason in gained_supp
            if _correspondence(feature, parameter) in idents
        ]
        if hits:
            candidates[name] = hits

    return {
        "dimensions_lost": lost,
        "dimensions_gained": gained,
        "dimensions_changed": changed,
        "measurements_substituted": substituted,
        "suppressions_gained": gained_supp,
        "suppressions_lost": lost_supp,
        "candidate_explanations": candidates,
        "measurement_comparison": comparison,
    }


def explain(diff: dict) -> list[str]:
    """The diff as lines a human or an LLM can read, most alarming first.

    Ordering is the value, not decoration. A lost dimension is a possible defect; a changed
    one is usually the experiment working. Printed in dict order the first hides among the
    second — which is how a wrong suppression stayed invisible across four issue reports.

    Every loss gets a line. Where a suppression might account for it, that appears **on** the
    line as a possibility, never instead of it.
    """
    out: list[str] = []
    candidates = diff.get("candidate_explanations", {})
    for name, label in sorted(diff["dimensions_lost"].items()):
        hint = candidates.get(name)
        why = f" — possibly: {'; '.join(hint)}" if hint else " — nothing claims it"
        out.append(f"LOST: {name} ({label}){why}")
    # A name that silently changed what it measures is a measurement lost and another
    # gained, disguised as neither (#1002) — so it ranks with the losses.
    for name, (was, now) in sorted(diff.get("measurements_substituted", {}).items()):
        out.append(f"SUBSTITUTED: {name} now draws {now}, was {was}")
    comparison = diff.get("measurement_comparison")
    if comparison:
        for measurement in comparison["changed"]:
            out.append(
                f"MEASUREMENT CHANGED: {measurement['parameter_id']} "
                f"on owner {measurement['owner']}"
            )
    if comparison and comparison["unknown"]:
        out.append("UNKNOWN: measurement preservation is unresolved for some named claims")
    for feature, parameter, reason in diff["suppressions_gained"]:
        out.append(f"suppressed: {parameter} on {feature} — {reason}")
    for name, label in sorted(diff["dimensions_gained"].items()):
        out.append(f"gained: {name} ({label})")
    for name, (was, now) in sorted(diff.get("dimensions_changed", {}).items()):
        out.append(f"changed: {name} {was} -> {now}")
    return out
