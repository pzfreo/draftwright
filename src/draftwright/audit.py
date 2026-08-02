"""audit — diff two builds and ask what went missing (#996 WP1 step 2).

The suppression ledger (`Drawing.suppressions()`) says which rule removed a measurement. It
answers *why* a dimension is absent, and it does so well — but only for absences some code
path remembered to record. Three consecutive review rounds on #999 found a suppression that
recorded nothing, each caught by a person hunting for it rather than by the ledger noticing
its own gap. A completeness claim is worth exactly the last search behind it.

**The differential does not depend on that.** Build a part twice, change one property, and
compare: if a dimension present in one build is absent in the other, something removed it —
whether or not anything wrote an `Omission`. It catches unknown-unknowns; the ledger reports
known-knowns.

That is how #997 was actually found. Not by reading code, and not from four issue reports
describing its symptoms, but from one comparison:

    50 x 50 -> no plan size at all
    50 x 40 -> width and depth both stated

Two builds, one property changed, and the rule fell out. This module is that experiment,
written down.

A leaf by construction: it reads the public surface of two finished ``Drawing``s and imports
nothing from the engine, so it can never become a dependency of the thing it measures.
"""

from __future__ import annotations


def _labelled(dwg) -> dict[str, str]:
    """``{annotation name: label}`` for every labelled annotation on a drawing."""
    out: dict[str, str] = {}
    for name in dwg.annotations():
        label = getattr(dwg.get_annotation(name), "label", None)
        if label is not None:
            out[name] = str(label)
    return out


def _rows(dwg) -> set[tuple]:
    return {(r["feature"], r["parameter_id"], r["reason"]) for r in dwg.suppressions()}


def diff_builds(before, after) -> dict:
    """Compare two finished drawings: what was drawn, and what the compiler declined.

    *before* and *after* are two builds you expect to differ in one property — a square part
    and a near-square one, a feature added, a dimension authored. Returns:

    - ``dimensions_lost`` / ``dimensions_gained`` — ``{name: label}``, by annotation name;
    - ``dimensions_changed`` — ``{name: (before, after)}`` where the annotation survived but
      its label did not. Reported but NOT alarmed: in a perturbation study a changed value is
      the expected result of the change, so treating it as a finding would bury the losses in
      noise. Silence was worse, though — an 80 mm width becoming 90 produced no output at all,
      and a surface claiming to report "what was drawn" has to be able to see that;
    - ``suppressions_gained`` / ``suppressions_lost`` — ledger rows as
      ``(feature, parameter_id, reason)``;
    - ``unexplained_losses`` — the subset of ``dimensions_lost`` that **no** newly-gained
      suppression accounts for.

    That last key is the point. A loss with a matching new suppression is a rule doing its
    job, and the ledger names it. A loss with nothing claiming it is a measurement that left
    the drawing without any part of the engine recording that it did — which is the class of
    failure #997 belonged to, and the class the ledger alone cannot detect.

    Matching is deliberately coarse: a lost annotation is "explained" when a gained
    suppression mentions the same parameter stem (``m_env_depth`` ↔ ``depth.length``). It is a
    triage signal for a human or a harness, not a proof — a precise link would need
    provenance threaded from the planner to the placed annotation, which is ADR 0010's seam
    and a much larger change than a comparison deserves.
    """
    before_dims, after_dims = _labelled(before), _labelled(after)
    lost = {n: v for n, v in before_dims.items() if n not in after_dims}
    gained = {n: v for n, v in after_dims.items() if n not in before_dims}
    changed = {n: (v, after_dims[n]) for n, v in before_dims.items() if after_dims.get(n, v) != v}

    before_rows, after_rows = _rows(before), _rows(after)
    gained_supp = sorted(after_rows - before_rows)
    lost_supp = sorted(before_rows - after_rows)

    stems = {str(row[1]).split(".")[0] for row in gained_supp if row[1]}
    unexplained = {
        name: label
        for name, label in lost.items()
        if not any(stem and stem in name for stem in stems)
    }

    return {
        "dimensions_lost": lost,
        "dimensions_gained": gained,
        "dimensions_changed": changed,
        "suppressions_gained": gained_supp,
        "suppressions_lost": lost_supp,
        "unexplained_losses": unexplained,
    }


def explain(diff: dict) -> list[str]:
    """The diff as lines a human or an LLM can read, most alarming first.

    Ordering is the whole value: an unexplained loss is a possible engine defect, a explained
    one is a rule working. Printing them together in dict order buries the first in the second
    — which is how a wrong suppression stayed invisible across four issue reports.
    """
    out: list[str] = []
    for name, label in sorted(diff["unexplained_losses"].items()):
        out.append(f"UNEXPLAINED: {name} ({label}) vanished — no suppression claims it")
    for feature, parameter, reason in diff["suppressions_gained"]:
        out.append(f"suppressed: {parameter} on {feature} — {reason}")
    for name, label in sorted(diff["dimensions_gained"].items()):
        out.append(f"gained: {name} ({label})")
    for name, (was, now) in sorted(diff.get("dimensions_changed", {}).items()):
        out.append(f"changed: {name} {was} -> {now}")
    return out
