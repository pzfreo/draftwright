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
registry slot, not a handle on what it measures. Two consequences, both real:

- **A replaced measurement can hide as a changed one.** Move a hole and `m_locx0` goes from
  `70` to `90`: a different measurement reused the slot. This reports it under
  ``dimensions_changed``, which is honest but weaker than "the 70 measurement is gone".
- **A loss cannot be attributed to a suppression with confidence.** The ledger row names a
  feature; the annotation does not. So a candidate explanation is a *hint*, never a verdict.

Closing either needs ADR 0010 provenance threaded from the planner to the placed annotation.
Until then this is a **triage aid, not a proof** — and deliberately shaped so its weakest part
cannot silence its strongest: a candidate suppression annotates a loss, it never removes it.
An earlier cut let a weak substring match cancel the alarm outright, so any newly-suppressed
``width.*`` excused every lost annotation whose name contained "width", across unrelated
features (Codex #1001). That is the exact false confidence this epic exists to remove.

A leaf by construction: it reads the public surface of two finished ``Drawing``s and imports
nothing from the engine, so the thing it measures can never come to depend on it.
"""

from __future__ import annotations

#: Annotation types that carry a MEASUREMENT. Everything else a drawing names — the title
#: block, notes, centre marks — is furniture: it has a label, it changes between builds for
#: reasons that are not dimensional, and counting it drowns the signal in exactly the noise
#: this module exists to lift out (Codex #1001). `Leader` is in: a hole callout is dimensional
#: content, it just renders as a leader.
_DIMENSIONAL = frozenset({"Dimension", "Leader"})


def _measurements(dwg) -> dict[str, str]:
    """``{annotation name: label}`` for the DIMENSIONAL annotations only."""
    out: dict[str, str] = {}
    for name, type_name in dwg.annotations().items():
        if type_name not in _DIMENSIONAL:
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

    - ``dimensions_lost`` / ``dimensions_gained`` — ``{name: label}``. **Every** loss appears
      here; nothing filters this list. It is the alarm.
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
