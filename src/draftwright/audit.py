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
missing**, not of its absence everywhere: the renderers that consume the compiled plan record
a `DimensionId`; the ones that place directly (the rotational OD/bore group, #754) do not.

- **A replaced measurement hides where identity is unrecorded.** Move a hole and `m_locx0`
  goes from `70` to `90`: a different measurement reused the slot. With identity recorded
  that is caught and reported as ``measurements_substituted``. Without it the old hole
  remains — and if the replacement renders the same label, **every result map is empty**. So
  a clean diff still does not establish that the measurements were preserved; it establishes
  that nothing observable at this resolution moved (Codex #1001).
- **A loss is attributed only where identity exists.** Where it does, the join to the ledger
  is exact — the same ``(feature, parameter_id)`` key both halves use. Where it does not, the
  loss reads "nothing claims it" whether or not a rule removed it: unknown, deliberately,
  rather than guessed. The first cut inferred attribution from annotation NAMES by substring
  and cancelled real alarms with unrelated suppressions.
- **An unnamed annotation is invisible.** ``Drawing.annotations()`` returns only *named*
  annotations by contract, so anything placed without a name cannot be compared here at all.
- **A hole callout's CONTENT is invisible; only its presence is seen.** It renders as a
  ``Leader`` whose ``label`` is ``""`` — the text is built at draw time and never exposed on
  the object. Measured: changing a bore from ⌀8 to ⌀12 produces an identical diff. So a lost
  callout is reported, and a callout that starts saying something different is not.

Closing the rest needs the remaining renderers to record identity too (#754), and a callout to
expose its own text. Until then this is a **triage aid, not a proof** — and shaped so its weakest part
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


def _identity(dwg, name):
    """``(feature, parameter_id)`` for *name*, or ``None`` where the renderer recorded none."""
    key = dwg.measurement_key(name) if hasattr(dwg, "measurement_key") else None
    return None if key is None else (key["feature"], key["parameter_id"])


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

    # A name present in BOTH builds that now draws a DIFFERENT measurement (#1002) — the
    # module's worst blind spot closed. An annotation name is an engine-assigned slot, so a
    # substitution under the same name (and, if the labels agree, under the same label)
    # previously produced an entirely empty diff.
    #
    # BOTH halves of the id are compared, and the two mean different things — `explain`
    # ranks them apart rather than this splitting them into two maps:
    #
    # - the PARAMETER changed → the name draws a different kind of measurement. Always an
    #   alarm. Rare in practice, because a slot is usually reused by its own pass.
    # - the FEATURE changed → the name draws the same measurement OF SOMETHING ELSE.
    #   Reported, not alarmed: `feature_key` is positional, so any experiment that moves a
    #   feature legitimately changes it. Alarming on that would fire on every perturbation.
    #
    # Comparing the parameter alone would have been nearly inert: a hole's X and Y location
    # dims share ONE id (`location.location`), because `location` is addressable per feature
    # and per-member identity is still open (ADR 0016 / #883). So an X↔Y swap is invisible
    # here whichever half is compared — a limit of the compiler's addressing granularity,
    # not of this comparison.
    substituted: dict[str, tuple] = {}
    for name in set(before_dims) & set(after_dims):
        b_id, a_id = _identity(before, name), _identity(after, name)
        if b_id is not None and a_id is not None and b_id != a_id:
            substituted[name] = (b_id, a_id)

    # Attribution, exact where identity exists. The first cut matched a suppression's
    # parameter stem against the annotation's NAME by substring, so a newly-suppressed
    # `width.length` claimed every lost annotation whose name contained "width" — across
    # unrelated features (Codex #1001 r1). With identity recorded the join is on the same
    # `(feature, parameter_id)` key both halves use, and a mismatch stays unexplained.
    candidates: dict[str, list[str]] = {}
    for name in lost:
        ident = _identity(before, name)
        if ident is None:
            continue  # unknown identity — no attribution rather than a guessed one
        hits = [
            reason for feature, parameter, reason in gained_supp if (feature, parameter) == ident
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
    # gained, disguised as neither (#1002) — so a changed PARAMETER ranks with the losses.
    # A changed FEATURE goes below them: it is the expected result of moving a feature, and
    # ranking it as an alarm would bury the real ones in every perturbation study.
    subs = sorted(diff.get("measurements_substituted", {}).items())
    for name, (was, now) in subs:
        if was[1] != now[1]:
            out.append(f"SUBSTITUTED: {name} now draws {now[1]}, was {was[1]}")
    for name, (was, now) in subs:
        if was[1] == now[1]:
            out.append(f"reattributed: {name} ({now[1]}) now measures {now[0]}, was {was[0]}")
    for feature, parameter, reason in diff["suppressions_gained"]:
        out.append(f"suppressed: {parameter} on {feature} — {reason}")
    for name, label in sorted(diff["dimensions_gained"].items()):
        out.append(f"gained: {name} ({label})")
    for name, (was, now) in sorted(diff.get("dimensions_changed", {}).items()):
        out.append(f"changed: {name} {was} -> {now}")
    return out
