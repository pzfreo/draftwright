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

The comparison scope is named compiled measurements. It checks their recorded owner,
parameter, nominal value, tolerance, directional span and rendered claim text. It does not
establish physical completeness, inspect unnamed annotations or certify an engineering
release. Use independent lint/requirement evidence alongside it. Claim verification retains
its own attribution limits, described in ``linting.evidence.verify_measurement_claims``.

The module imports no engine code. Drawings supply their public snapshots and registry
reads; no cross-run provider identity is reconstructed or serialized.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class MeasurementClaim:
    """A named rendered claim and its compiled meaning, retained in one process."""

    owner: object
    parameter: str
    annotation: str
    meaning: tuple
    rendered: tuple


@dataclass(frozen=True)
class MeasurementSnapshot:
    """A captured drawing read, with declaration-local references rather than durable IDs."""

    owners: tuple
    claims: tuple[MeasurementClaim, ...]
    unknown: tuple[tuple[str, str], ...] = ()


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


#: Sheet FURNITURE — the annotation types that carry no measurement. Everything else counts.
#:
#: A denylist, not an allowlist, and the polarity is the point (Codex #1001). An allowlist of
#: {"Dimension", "Leader"} silently dropped `SafeDimension`, a real measurement-bearing class,
#: and would drop every future dimensional type and subclass the same way. For a tool whose
#: one job is not to hide a loss, an unknown type must fail toward NOISE — reported and
#: dismissed by a reader — never toward silence. Adding a genuinely new furniture type here is
#: a deliberate act; forgetting to add a new measurement type to an allowlist was an accident
#: waiting to happen, and had already happened once.
_FURNITURE = frozenset({"TitleBlock", "Note", "CenterMark"})


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
