"""Verify that a claimed representation actually renders the value it claims (#1217).

Coverage checks answer "did this requirement reach the sheet?" by walking the registry and
reading **provenance riders** the annotations carry about themselves. That is a claim, not an
observation: an annotation asserting it carries hole 3's diameter is believed, and nothing
checks that it renders that diameter — or renders anything at all.

The failure mode is not hypothetical. ``covers_hole_representations_by_feature`` was read in two
places and populated by nothing, which inverted the benchmark's ``drawing_consumer`` metric
(16/16 ``unsupported`` against a true 16/16 ``supported``) and went unnoticed, because the only
thing that could have noticed was the metric itself.

So the ledger becomes a **pointer to the claimed representation, not final proof** (#1206). This
module resolves each pointer against the built drawing and checks the rendered content carries
the value the compiler approved.

**Shared, not hole-specific.** The claim is ``registry.measurement_of(name)``, which every
feature family populates through the same seam (ADR 0010), so nothing here knows what a hole is.
The expected value comes from the compiled plan (ADR 0016 Amendment 1), never from a renderer's
own formatting — comparing rendered output against rendered output would prove only that the
renderer is self-consistent.

**Two consumers.** ``evaluation`` gets an observation it can score without trusting the engine's
self-report; lint gets a self-consistency check it can act on. One implementation, because two
would drift — which is the state #1206 was opened about.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from draftwright._geometry import _fmt
from draftwright.linting.issues import LintIssue

#: What became of one claim.
#:
#: ``unresolved`` is the interesting one: an annotation claiming a measurement the compiled plan
#: has no entry for means a renderer emitted content the compiler never approved, which ADR 0016
#: Amendment 1 exists to prevent. It is reported rather than skipped.
ClaimState = Literal["confirmed", "value_absent", "unresolved", "unreadable", "no_expected_value"]

#: Values are compared as floats at this absolute tolerance, not as strings. A string compare
#: would fail on ``"8"`` against ``"8.0"``; a substring compare would pass ``⌀8`` against a label
#: reading ``⌀18``, which is the trap this module exists to avoid rather than introduce.
_VALUE_TOL = 1e-6


def _expected_numbers(approved) -> frozenset[float]:
    """Every number the sheet may legitimately show for *approved*.

    From ``value_text``, NOT ``value``. The compiler formats for display — a 13.649 mm extent
    is approved as ``"13.6"`` and drawn as ``13.6`` — so comparing the raw float reports a
    correctly drawn dimension as wrong. Measured: the first corpus run flagged 13 claims, and
    ten were exactly this, including ``expected=('13.6',) rendered=13.6``.

    Falling back to the **span** where there is no text. A location carries ``value=0.0`` and
    ``value_text=""`` because its magnitude lives in ``span = (datum, located point)``; the
    renderer takes one axis component of that span, so the acceptable numbers are the
    per-axis components — formatted the same way, since a 29.35 mm offset is drawn as 29.4.

    That fallback is the difference between verifying a location and declining to. The first
    cut reported these as unverifiable, which was true of the implementation and not of the
    drawing: all four cases on the corpus are ordinary, correct location dimensions whose
    value the compiler did supply, in the field this function had not looked at.

    Axis components only, never the 3-D distance between the span's ends — that is a number no
    renderer draws, and admitting it would widen what counts as a match for nothing.
    """
    text = getattr(approved, "value_text", "") or ""
    try:
        return frozenset({float(text)})
    except ValueError:
        pass
    span = getattr(approved, "span", None)
    if not span or len(span) != 2:
        return frozenset()
    start, end = span
    return frozenset(float(_fmt(abs(float(end[axis]) - float(start[axis])))) for axis in range(3))


#: Every number a label renders. Deliberately ALL of them, not the leading one: a compound hole
#: callout reads ``⌀8 THRU ⌴ ⌀14 ↧ 7`` and claims three separate measurements, so
#: ``structural._label_value`` — which answers "what does this label assert about the path it is
#: drawn on", a different question — sees only the 8.
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class ClaimOutcome:
    """One annotation's claim on one measurement, resolved against what it renders."""

    annotation: str
    parameter_id: str
    state: ClaimState
    expected: tuple[str, ...] = ()
    rendered: str | None = None


def compiled_values(plan) -> dict:
    """``{measurement id: (ApprovedDimension, ...)}`` for everything the compiler approved.

    A **multimap**, deliberately. A ``DimensionId`` is not unique per rendered dimension: one
    hole's ``location.location`` yields both the X and the Y offset, and the overall height
    appears in a group and in the ladder. Keyed as a single value it silently drops one of each
    pair, and the verifier then reports a true dimension as a mismatch — measured while building
    this, on a two-hole plate where the Y locations read 15 and 45 against a lookup insisting on
    20 and 60.
    """
    values: dict = defaultdict(list)
    for group in getattr(plan, "groups", ()):
        for approved in group.dims:
            values[approved.id].append(approved)
    for ladder in getattr(plan, "ladders", ()):
        for approved in ladder.rungs:
            values[approved.id].append(approved)
    for approved in getattr(plan, "locations", ()):
        values[approved.id].append(approved)
    return {key: tuple(entries) for key, entries in values.items()}


def rendered_numbers(annotation) -> frozenset[float] | None:
    """Every number *annotation* puts on the sheet, or ``None`` when nothing can read it.

    Two sources: a ``label``, and the ROWS of a table. A table draws as compound geometry with
    no label, so reading rows is what makes a hole table's claims checkable at all — and those
    are precisely the claims that matter most, since the engine withdraws the individual
    callouts when it escalates to a table.

    ``None`` is a real answer and is reported as such. Silently counting an unreadable
    annotation as confirmed would rebuild the trust this module exists to remove.
    """
    try:
        label = getattr(annotation, "label", None) or getattr(annotation, "_annotate_label", None)
        rows = getattr(annotation, "table_rows", None) or getattr(
            annotation, "gear_requirement_rows", None
        )
    except Exception:  # noqa: BLE001 — a raising property on a caller's item must not kill lint
        return None
    texts = [str(label)] if label else []
    texts += [str(cell) for row in (rows or ()) for cell in row]
    if not texts:
        return None
    return frozenset(float(match.group()) for text in texts for match in _NUMBER_RE.finditer(text))


def verify_measurement_claims(registry, plan) -> list[ClaimOutcome]:
    """Resolve every annotation's measurement claims against what it renders.

    This is a **presence** check, and that limit is worth stating: it proves the approved value
    appears among the numbers the annotation draws, not that it appears in the right *position*
    within a compound label. Attributing part of ``⌀8 THRU ⌴ ⌀14 ↧ 7`` to a specific measurement
    would need the renderer to record spans as it formats. Presence is strictly stronger than
    what preceded it, which was nothing.
    """
    approved = compiled_values(plan)
    outcomes: list[ClaimOutcome] = []
    for name in registry.names():
        claims = registry.measurement_of(name)
        if not claims:
            continue
        numbers = rendered_numbers(registry.named(name))
        for claim in claims:
            entries = approved.get(claim, ())
            parameter = str(getattr(claim, "parameter", claim))
            expected = tuple(entry.value_text for entry in entries)
            wanted = (
                frozenset().union(*(_expected_numbers(entry) for entry in entries))
                if entries
                else frozenset()
            )
            if not entries:
                outcomes.append(ClaimOutcome(name, parameter, "unresolved"))
            elif not wanted:
                outcomes.append(ClaimOutcome(name, parameter, "no_expected_value", expected))
            elif numbers is None:
                outcomes.append(ClaimOutcome(name, parameter, "unreadable", expected))
            elif any(
                any(abs(want - number) <= _VALUE_TOL for number in numbers) for want in wanted
            ):
                outcomes.append(ClaimOutcome(name, parameter, "confirmed", expected))
            else:
                outcomes.append(
                    ClaimOutcome(
                        name,
                        parameter,
                        "value_absent",
                        expected,
                        ", ".join(str(number) for number in sorted(numbers)) or None,
                    )
                )
    return outcomes


def lint_claimed_representations(registry, plan) -> list[LintIssue]:
    """Report claims the drawing does not bear out."""
    issues: list[LintIssue] = []
    for outcome in verify_measurement_claims(registry, plan):
        if outcome.state == "confirmed":
            continue
        if outcome.state == "value_absent":
            issues.append(
                LintIssue(
                    severity="warning",
                    code="claimed_value_absent",
                    message=(
                        f"{outcome.annotation} claims {outcome.parameter_id} "
                        f"({'/'.join(outcome.expected)}) but renders {outcome.rendered}"
                    ),
                )
            )
        elif outcome.state == "unresolved":
            issues.append(
                LintIssue(
                    severity="warning",
                    code="claimed_measurement_not_compiled",
                    message=(
                        f"{outcome.annotation} claims {outcome.parameter_id}, which the "
                        "compiler did not approve"
                    ),
                )
            )
        elif outcome.state == "no_expected_value":
            issues.append(
                LintIssue(
                    severity="info",
                    code="claimed_representation_no_expected_value",
                    message=(
                        f"{outcome.annotation} claims {outcome.parameter_id}, which the "
                        "compiler approved with no displayable value, so the claim is "
                        "neither confirmed nor refuted"
                    ),
                )
            )
        else:
            issues.append(
                LintIssue(
                    severity="info",
                    code="claimed_representation_unreadable",
                    message=(
                        f"{outcome.annotation} claims {outcome.parameter_id} and renders no "
                        "readable text, so the claim is neither confirmed nor refuted"
                    ),
                )
            )
    return issues


__all__ = [
    "ClaimOutcome",
    "ClaimState",
    "compiled_values",
    "lint_claimed_representations",
    "rendered_numbers",
    "verify_measurement_claims",
]
