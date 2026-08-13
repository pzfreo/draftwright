"""audit — diff two builds and ask what went missing (#996 WP1 step 2).

The suppression ledger (`Drawing.suppressions()`) says which rule removed a measurement. It
answers *why* a dimension is absent — but only for absences some code path remembered to
record. Three consecutive review rounds on #999 found suppression paths that recorded nothing,
each caught by a person hunting for it rather than by the ledger noticing its own gap.

**A diff does not depend on that.** If a dimension is present in one build and absent in
another, something removed it, whether or not anything wrote it down. That is how #997 was
actually found: not from the four issue reports describing its symptoms, but from `50x50` vs
`50x40`.

## What this cannot do, stated first

A placed annotation carries measurement identity only where the renderer recorded one
(#1002) — otherwise its name is an engine-assigned registry slot, not a handle on what it
measures. **Identity is partial, and every limit below is a consequence of where it is
missing.** Which renderers record it is not described here in prose, because a prose
description of that set has now been wrong TWICE: first it named only the direct-placing
group while four compiled-plan renderers dropped their ids (Codex r1), then it listed a
four-item exception set while the entire shared machined-feature renderer — chamfers,
fillets, flats, pockets, grooves, boss diameters — recorded nothing (Codex r3). The prose
was believable both times, which is the point.

The answer lives in `tests/test_audit_differential.py`, whose ratchet scans both placement
paths (corridor candidates and direct `ctx.place`) across the whole `annotations/` package,
and whose EXEMPT map IS the exception inventory — every entry carrying its reason, and a
stale entry failing so the list cannot outlive the gaps it describes. Read that, not a
second prose inventory here.

Even where identity IS recorded, matching it across two builds is approximate. The ledger's
key embeds the feature's origin and scalars, so it cannot join two builds that differ — the
join uses `_correspondence` instead, which keeps the feature's KIND and drops its
coordinates. Two features of one kind are therefore not separated: a same-count swap of one
slot's width for another's is invisible (**#1006**). Multiplicity IS compared — as a multiset,
so a grouped callout dropping a member is caught — but a like-for-like exchange is not.

- **A replaced measurement hides where identity is unrecorded.** Move a hole and `m_locx0`
  goes from `70` to `90`: a different measurement reused the slot. With identity recorded
  that is caught and reported as ``measurements_substituted``. Without it the old hole
  remains — and if the replacement renders the same label, **every result map is empty**. So
  a clean diff still does not establish that the measurements were preserved; it establishes
  that nothing observable at this resolution moved (Codex #1001).
- **A loss is attributed only where identity exists, and only by kind.** Where identity is
  recorded the join is on ``(feature kind, parameter_id)`` — precise enough to separate an
  envelope's width from a slot's, not precise enough to separate two slots. Where it is not
  recorded the loss reads "nothing claims it" whether or not a rule removed it: unknown,
  deliberately, rather than guessed. The first cut inferred attribution from annotation
  NAMES by substring and cancelled real alarms with unrelated suppressions.
- **An unnamed annotation is invisible.** ``Drawing.annotations()`` returns only *named*
  annotations by contract, so anything placed without a name cannot be compared here at all.
- **A legacy or external labelless callout exposes presence only.** Generated hole leaders
  retain their geometric callout's semantic text, so a changed diameter/depth is observable.
  A user-supplied or older ``Leader`` may still have ``label == ""``; its loss is reported,
  but two such present annotations remain indistinguishable by content.

Closing the rest needs the remaining renderers to record identity too (#754). Until then this
is a **triage aid, not a proof** — and shaped so its weakest part cannot silence its strongest:
a candidate suppression annotates a loss, it never removes it.
An earlier cut let a weak substring match cancel the alarm outright, so any newly-suppressed
``width.*`` excused every lost annotation whose name contained "width", across unrelated
features (Codex #1001). That is the exact false confidence this epic exists to remove.

A leaf by construction: it reads the public surface of two finished ``Drawing``s and imports
nothing from the engine, so the thing it measures can never come to depend on it.
"""

from __future__ import annotations

from collections import Counter

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

    The hint does not subtract from ``dimensions_lost``. The join is by feature KIND, so it
    cannot separate two features of one kind, and a weak match that cancels an alarm is worse
    than no match at all — it manufactures the confidence this epic exists to remove.
    """
    before_dims, after_dims = _measurements(before), _measurements(after)
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
    # A hole's X and Y location dims share ONE id (`location.location`), because `location`
    # is addressable per feature and per-member identity is still open (ADR 0016 / #883), so
    # an X↔Y swap is invisible here. That is the compiler's addressing granularity, not a
    # limit of this comparison.
    substituted: dict[str, tuple] = {}
    for name in set(before_dims) & set(after_dims):
        b_ids, a_ids = _identities(before, name), _identities(after, name)
        if b_ids and a_ids and b_ids != a_ids:
            substituted[name] = (sorted(b_ids.elements()), sorted(a_ids.elements()))

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
    for feature, parameter, reason in diff["suppressions_gained"]:
        out.append(f"suppressed: {parameter} on {feature} — {reason}")
    for name, label in sorted(diff["dimensions_gained"].items()):
        out.append(f"gained: {name} ({label})")
    for name, (was, now) in sorted(diff.get("dimensions_changed", {}).items()):
        out.append(f"changed: {name} {was} -> {now}")
    return out
