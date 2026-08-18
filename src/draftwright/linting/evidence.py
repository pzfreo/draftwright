"""Verify that a claimed representation actually renders the value it claims (#1217).

Coverage checks answer "did this requirement reach the sheet?" by walking the registry and
reading **provenance riders** the annotations carry about themselves. That is a claim, not an
observation: an annotation asserting it carries hole 3's diameter is believed, and nothing
checks that it renders that diameter — or renders anything at all.

The failure mode is not hypothetical. The benchmark's ``drawing_consumer`` metric was inverted
— 16/16 ``unsupported`` against a true 16/16 ``supported`` — because it read only ``hc_``
callout names and so scored every hole on a table-escalated sheet as lost. The *fix* for that
then read ``covers_hole_representations_by_feature``, a rider no caller populates, and looked
correct only because a hand-built stub in the tests supplied it.

(An earlier draft of this paragraph said the dead rider caused the inversion, and that it went
unnoticed "for months". Neither is true: the rider made the attempted fix look correct, and it
existed for four days — introduced 2026-08-14, caught by the next review cycle. Corrected here
because a module explaining why claims must be checked is a poor place to leave an unchecked
one.)

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

    Narrowed to the approved ``axis`` where the compiler declares one. Accepting all three
    components repeated the mistake this docstring already rejects for the 3-D distance ("a
    number no renderer draws"): on a pocket located in a Z-normal plane the Z component is
    drawn by nothing, and admitting it confirmed a deliberately relabelled X offset (#1218
    review). Never the 3-D distance, for the original reason.

    All three remain acceptable when no axis is declared, because then nothing says which the
    renderer took. That residue is named in :func:`verify_measurement_claims`'s limits rather
    than papered over.
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
    axis = getattr(approved, "axis", None)
    # EXCLUDE the declared axis, do not restrict to it — but only because of WHICH producer
    # reaches here. `_compile_locations` sets `axis` to the FEATURE's axis, so a pocket normal
    # to Z is located by its X and Y and the Z component is drawn by nothing. Restricting to
    # the axis was this fix's first cut and it removed exactly the components that ARE drawn,
    # turning three correct NIST dimensions into mismatches.
    #
    # It is NOT a general rule about `axis`. `_shoulder_span` and `_compile_slot_positions`
    # set it to the MEASURED direction, where excluding it would remove the only drawn
    # component — those are safe today solely because they set a numeric `value_text` and
    # return above. A future producer that emits an empty `value_text` with a measured-axis
    # `axis` would break silently here, so the discriminator wants to be the producer's
    # intent rather than this inference (#1218 review round 2).
    excluded = "xyz".index(axis) if axis in ("x", "y", "z") else None
    indices = [i for i in range(3) if i != excluded]
    return frozenset(
        float(_fmt(abs(float(end[index]) - float(start[index])))) for index in indices
    )


#: Every number a label renders. Deliberately ALL of them, not the leading one: a compound hole
#: callout reads ``⌀8 THRU ⌴ ⌀14 ↧ 7`` and claims three separate measurements, so
#: ``structural._label_value`` — which answers "what does this label assert about the path it is
#: drawn on", a different question — sees only the 8.
#: Unsigned, deliberately. A round-2 draft admitted a leading ``-`` for a hypothetical negative
#: ``value_text``; measured, no producer emits one, and the sign turned ordinary hyphenated text
#: into numbers — the gear table's ``ISO 21771-2:2025`` yielded ``-2.0`` where the drawn glyph is
#: ``2``. Should a producer ever emit a negative value it reports `value_absent` rather than
#: silently confirming, which is the safe direction to be wrong in (#1218 review round 2).
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

#: A leading ``4× ⌀`` repeat count, stripped before numbers are read. A count is not a
#: measurement, and leaving it in let ``4× ⌀9 THRU`` confirm a claimed bore diameter of 4
#: (#1218 review). Unlike the compound-label residue below, this was never within the stated
#: limit — the number is not a measurement at all.
#:
#: The ⌀/R lookahead is load-bearing and is `structural._label_value`'s own discriminator:
#: ``N× ⌀d`` counts d-diameter features, while ``N× v`` is a pitch span. Without it this
#: pattern also matched a pocket's ``44 × 93.4 × 10 DEEP`` and deleted the width — turning
#: two correct dimensions on `issue_915_case_study_2` into mismatches, which is how a
#: safety fix becomes the defect it was guarding against.
#:
#: The ``N× v`` pitch form is deliberately left alone, so a claim whose value equals the
#: count could still be confirmed by it. Named in the limits rather than guessed at.
_REPEAT_RE = re.compile(r"^\s*\d+\s*[×x]\s*(?=[ø⌀Rr])")


@dataclass(frozen=True)
class ClaimOutcome:
    """One annotation's claim on one measurement, resolved against what it renders."""

    annotation: str
    parameter_id: str
    state: ClaimState
    expected: tuple[str, ...] = ()
    rendered: str | None = None
    #: The claim itself, so a consumer can join on the FEATURE as well as the parameter
    #: name. `parameter_id` alone says `bore.diameter` without saying whose (#1217 PR 2).
    measurement: object | None = None


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


#: Table columns whose cells are counts or identifiers rather than measurements, matched on the
#: header row a table carries as its first entry.
#:
#: ``QTY`` is the reason this exists. `add_hole_table` emits ``TAG | ⌀ | DEPTH | QTY``, and a
#: table drawing ``ø99`` for a ⌀4 hole was confirmed by its own quantity cell — the same defect
#: `_REPEAT_RE` fixes for ``4× ⌀9 THRU``, surviving in the path this module calls the one that
#: matters most, because the strip was applied to the label and not to row cells (#1218 review
#: round 2). ``TAG`` carries no digits today and is listed so a future numeric tag cannot
#: quietly become a measurement.
_NON_MEASURING_COLUMNS = frozenset({"QTY", "TAG", "ITEM", "REF"})


def _table_texts(rows) -> list[str]:
    """A table's cells, minus the columns that do not carry measurements.

    The first row is the header — that is the shape both table producers emit — so the columns
    to drop are named rather than guessed at by position. A table with no recognisable header
    contributes every cell, which is the pre-existing behaviour and errs toward false
    confirmation; that residue is named in :func:`verify_measurement_claims`'s limits.
    """
    rows = list(rows or ())
    if not rows:
        return []
    header = [str(cell).strip().upper() for cell in rows[0]]
    dropped = {i for i, name in enumerate(header) if name in _NON_MEASURING_COLUMNS}
    return [str(cell) for row in rows[1:] for i, cell in enumerate(row) if i not in dropped]


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
        # `table_rows` only. A gear table carries `gear_requirement_rows` AND no measurement
        # claim at all (measured: `claims: ()`), so this is never called on one — and it now
        # carries `table_rows` regardless. Reading both was a second field read by nobody,
        # added inside the fix for the first one (#1218 review).
        rows = getattr(annotation, "table_rows", None)
    except Exception:  # noqa: BLE001 — a raising property on a caller's item must not kill lint
        return None
    texts = [_REPEAT_RE.sub("", str(label))] if label else []
    texts += _table_texts(rows)
    if not texts:
        return None
    return frozenset(float(match.group()) for text in texts for match in _NUMBER_RE.finditer(text))


def verify_measurement_claims(registry, plan) -> list[ClaimOutcome]:
    """Resolve every annotation's measurement claims against what it renders.

    **Four limits, all measured rather than reasoned about** (#1218 review found each of them
    by relabelling a real drawing and watching this function stay silent):

    1. **Presence, not attribution.** It proves the approved value appears among the numbers
       the annotation draws, not that it appears in the right *position*. Relabelling
       ``⌀8 THRU ⌴ ⌀14 ↧ 7`` to ``⌀14 THRU ⌴ ⌀14 ↧ 8`` still confirms the bore, from the
       counterbore's depth. **A table is the severe case**, not a footnote to this one: its
       cells are one flat pool, so a diameter claim can be satisfied by an X or Y coordinate
       in another column — measured, replacing every diameter cell on a 17-row table left 17
       of 32 claims confirmed. Matching a claim's parameter to a *column* would fix it and is
       a real piece of design, not a tightening.
    5. **One annotation may claim several different ids**, and one rendered number satisfies
       all of them — measured up to ten distinct ids on a single annotation. Limit 2 is about
       members *within* one id; this is across ids.
    2. **Members of one `DimensionId` confirm each other.** A hole's location is one id holding
       both the X and the Y offset, so swapping two location labels — two visibly wrong
       dimensions — confirms both. On a four-hole pattern all eight offsets share the id. The
       multimap is what stops false *mismatches*; this is its cost, and narrowing it needs
       per-member identity, which #883 deliberately leaves open.
    3. **Reach is bounded by ADR 0010 identity.** An annotation carrying no measurement claim
       is skipped entirely, and roughly half do — measured, 81 of 170 annotations on
       ``nist_ctc_02`` carry claims. "N claims, N confirmed" is a statement about the claimed
       part of the sheet, never the whole sheet.
    4. **Existence is assumed, not checked.** Claims are read by walking the registry, so an
       approved measurement that NO annotation claims is invisible here. That direction is
       coverage's job, and #1217's ``annotation_missing`` state is deliberately not
       implemented — there is nothing to point at.

    What the check compares is also worth being exact about: ``label`` is a rider the renderer
    attaches, not glyphs recovered from the sheet, so this is the compiled plan against a
    second self-report rather than against geometry. It is a real cross-source check — the
    label is built from the formatted arguments the renderer was handed — and ``table_rows`` is
    genuinely the drawn content. Verifying export-visible text is #1217's step 5.
    """
    approved = compiled_values(plan)
    outcomes: list[ClaimOutcome] = []
    # `sorted`, because `registry.names()` is a set: without it the emitted issue ORDER varies
    # run to run on the same drawing (measured: five orderings in five processes), against
    # ADR 0006's determinism posture — the defect #1196 fixed for lint text, reintroduced
    # through iteration order.
    for name in sorted(registry.names()):
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
                outcomes.append(ClaimOutcome(name, parameter, "unresolved", measurement=claim))
            elif not wanted:
                outcomes.append(
                    ClaimOutcome(name, parameter, "no_expected_value", expected, measurement=claim)
                )
            elif numbers is None:
                outcomes.append(
                    ClaimOutcome(name, parameter, "unreadable", expected, measurement=claim)
                )
            elif any(
                any(abs(want - number) <= _VALUE_TOL for number in numbers) for want in wanted
            ):
                outcomes.append(
                    ClaimOutcome(name, parameter, "confirmed", expected, measurement=claim)
                )
            else:
                outcomes.append(
                    ClaimOutcome(
                        name,
                        parameter,
                        "value_absent",
                        expected,
                        ", ".join(str(number) for number in sorted(numbers)) or None,
                        measurement=claim,
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
                        f"({'/'.join(outcome.expected)}) but renders "
                        f"{outcome.rendered or 'no number'}"
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
