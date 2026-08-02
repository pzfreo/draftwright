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

A placed annotation carries **no measurement identity** — its name is an engine-assigned
registry slot, not a handle on what it measures. Three consequences, all real:

- **A replaced measurement can hide completely.** Move a hole and `m_locx0` goes from `70` to
  `90`: a different measurement reused the slot, and this reports it as a *change*. Worse, if
  the replacement happens to render the same label, **every result map is empty** — the
  substitution is wholly invisible. So a clean diff does **not** establish that the
  measurements were preserved; it establishes only that nothing observable at this resolution
  moved (Codex #1001).
- **A loss cannot be attributed to a suppression with confidence.** Feature provenance is
  PARTIAL, not absent: ``registry.feature_of(name)`` returns the owning feature for a hole
  callout, centre mark or location, and ``None`` for an envelope dim (measured). And even a
  known feature does not identify *which* of its measurements an annotation is. So a candidate
  explanation is a *hint*, never a verdict — though tightening it to feature identity where
  provenance exists is a real improvement available today (#1002).
- **An unnamed annotation is invisible.** ``Drawing.annotations()`` returns only *named*
  annotations by contract, so anything placed without a name cannot be compared here at all.
- **A hole callout's CONTENT is invisible; only its presence is seen.** It renders as a
  ``Leader`` whose ``label`` is ``""`` — the text is built at draw time and never exposed on
  the object. Measured: changing a bore from ⌀8 to ⌀12 produces an identical diff. So a lost
  callout is reported, and a callout that starts saying something different is not.

Closing these needs stable MEASUREMENT identity on a placed annotation — feature provenance
alone is not enough, and is in any case only partial today.
Until then this is a **triage aid, not a proof** — and deliberately shaped so its weakest part
cannot silence its strongest: a candidate suppression annotates a loss, it never removes it.
An earlier cut let a weak substring match cancel the alarm outright, so any newly-suppressed
``width.*`` excused every lost annotation whose name contained "width", across unrelated
features (Codex #1001). That is the exact false confidence this epic exists to remove.

A leaf by construction: it reads the public surface of two finished ``Drawing``s and imports
nothing from the engine, so the thing it measures can never come to depend on it.
"""

from __future__ import annotations

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
        # NO non-empty-label requirement. A hole callout renders as a `Leader` whose own
        # `label` is "" — its text lives on an attached callout object, and on some paths
        # nowhere readable at all. Requiring a label dropped those from the comparison
        # entirely, so a vanished hole callout produced NO loss: the one thing this must
        # never do (#996). Presence is the signal; the label is extra detail on it.
        label = getattr(dwg.get_annotation(name), "label", None)
        out[name] = "" if label is None else str(label)
    return out


def _rows(dwg) -> set[tuple]:
    return {(r["feature"], r["parameter_id"], r["reason"]) for r in dwg.suppressions()}


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
      newly-gained suppression might account for a loss, by parameter stem.

    The hint does not subtract from ``dimensions_lost``. An annotation carries no feature
    identity, so the match cannot be trusted, and a weak match that cancels an alarm is worse
    than no match at all — it manufactures the confidence this epic exists to remove.
    """
    before_dims, after_dims = _measurements(before), _measurements(after)
    lost = {n: v for n, v in before_dims.items() if n not in after_dims}
    gained = {n: v for n, v in after_dims.items() if n not in before_dims}
    changed = {n: (v, after_dims[n]) for n, v in before_dims.items() if after_dims.get(n, v) != v}

    before_rows, after_rows = _rows(before), _rows(after)
    gained_supp = sorted(after_rows - before_rows)
    lost_supp = sorted(before_rows - after_rows)

    candidates: dict[str, list[str]] = {}
    for name in lost:
        hits = [
            reason
            for _feature, parameter, reason in gained_supp
            if parameter and str(parameter).split(".")[0] in name
        ]
        if hits:
            candidates[name] = hits

    return {
        "dimensions_lost": lost,
        "dimensions_gained": gained,
        "dimensions_changed": changed,
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
    for feature, parameter, reason in diff["suppressions_gained"]:
        out.append(f"suppressed: {parameter} on {feature} — {reason}")
    for name, label in sorted(diff["dimensions_gained"].items()):
        out.append(f"gained: {name} ({label})")
    for name, (was, now) in sorted(diff.get("dimensions_changed", {}).items()):
        out.append(f"changed: {name} {was} -> {now}")
    return out
