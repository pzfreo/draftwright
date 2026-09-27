# Requirement-aware annotation conflict policy (#1866)

This is the proposed decision contract for coordinating Draftwright's *existing*
placement mechanisms. It is not a new placer, a standards-conformance claim, or
permission to add sheets behind the caller's back. ADR 2 still owns view/page
planning and the collect-then-solve geometry; ADR 5 still owns independent
coverage and honest failure.

## Existing machinery to reuse

| Decision or evidence | Existing home | Boundary |
| --- | --- | --- |
| Recognized and declared obligations | recognition ledger, compiled dimensions, authored intent | A missing source fact is not made optional by failing to compile it. |
| Candidate geometry and same-batch conflicts | corridor/leader candidate solves and measured ink | These choose positions; they do not decide which manufacturing meaning is expendable. |
| Whole-view/page/scale options | compose and resolved view plan | Fixed caller page/scale and authored view constraints are never relaxed. |
| Callout-to-hole-table replacement | reversible table transaction and registry coverage | A table replaces only facts its cells and feature identifiers actually carry. |
| Result authority | live annotation registry, independent lint, drawing report | A carrier trace is evidence, not proof that a requirement was satisfied. |

The current strip solver drops the lowest local numeric priority over capacity.
That rank is useful *within* one representation and corridor, but is not a
drawing-wide comparison of required meaning versus optional or redundant ink.
The carrier trace joins demand to exact measurements, table cells, structured
notes, and source-owned PMI after placement; source ownership alone remains
`coverage_authority=false`. No new policy may turn an unverified source link into
a satisfied requirement.

## Decision contract

For each conflict, form a stable set of obligations from the recognized and
authored ledgers. A supported required obligation is never silently converted
to optional. Unknown coverage fails closed: it cannot justify dropping a glyph
as redundant. For each obligation, enumerate *only* existing admissible
representations (for example a corridor candidate, a routed leader, or a hole
table cell with its required feature tag). Record any dependency such as a
controlled feature, datum reference, balloon, or view. An authored pin remains
an anchor preference at its natural site, not a raw page coordinate or a licence
to overlap.

Choose among geometrically feasible outcomes in this order:

1. Preserve the caller's fixed page, scale, projection, authored constraints,
   and the semantic identity of every required obligation. A failed requirement
   stays in the result as an explicit unmet obligation.
2. Among outcomes preserving the same required obligations, honour feasible
   authored priority and pins before automatic preferences. An infeasible pin
   is named and reported; it does not force invalid ink.
3. Prefer legible existing alternatives that clear hard ink and view conflicts.
   A representation swap is committed only as an atomic transaction: snapshot
   and temporarily stash the original to free its ink, place the replacement,
   verify its exact claims, then commit; otherwise restore the original and
   its diagnostics.
4. Drop optional or provably redundant *generated* ink before sacrificing a
   required obligation. Retain a named reason and the displaced candidate IDs.
   Do not erase authored content or a source-only PMI item merely because its
   live carrier is hard to place.
5. Break equal choices by stable obligation identity, then stable candidate
   identity. Never use renderer arrival order, object address, platform-specific
   iteration order, or a PDF/image score as the tie-break.

If no feasible choice preserves all supported required meaning, return the
best bounded attempt **and** an explicit incomplete result naming each unmet
obligation, its provenance, the attempted representations, and the blocking
ink or caller constraint. Do not enlarge a fixed sheet, relax an explicit
scale, silently rerender with the other algorithm, or call a clean lint result
complete when the recognized inventory is missing.

This ordering is Draftwright product policy, not a claim of conformance with
ISO 129-1, ISO 1101, ASME Y14.5, or ASME Y14.2. Standards-relevant content,
presentation, and interpretation of each surviving annotation require separate
checks.

## Implementation and review sequence

1. At the existing compiler/collector seam, attach exact obligation identities
   and a required/optional/unknown classification to alternatives. Keep the
   geometry solver a leaf with no import of the IR or recognition model.
2. Make the existing local solves consume the policy rank and emit a common
   decision trace. Include both winning and rejected options and distinguish
   `not_attempted`, `infeasible`, `replaced`, and `unmet`.
3. Coordinate one real representation swap through the existing reversible
   table transaction; prove that removing a glyph cannot remove its measurement,
   datum, fit, tolerance, or feature identifier. Then extend the same contract
   to other proven alternatives, rather than inventing parallel placement code.
4. Test a same-batch conflict, a cross-view conflict, an infeasible authored
   pin, and a crowded real part. Assert exact requirement coverage and trace
   reasons as well as visual legibility. Reuse saved baseline evidence where the
   placement code has not changed; do not demand a perfect one-page CTC result.

## Extra sheets reuse the explicit document boundary

`Document`/`DocumentResult` already provide explicitly authored sheets over one
sealed physical inventory, one raw recognition acquisition, and a live
cross-sheet requirement evaluation (`document.py`, `reporting.py`,
`test_document_build.py`, `test_document_report.py`). This is a real document
model and coverage authority, not something to recreate in the layout solver.
It does **not** yet make automatic multi-sheet output a one-sheet overflow
fallback: `Document.build()` compiles the caller's named `Sheet` members in
sequence, while `DocumentResult` has report/write-report but no drawing-set
export, sheet-number policy, common title/revision identity, or automatic
assignment of dependent views and annotations.

Before a second sheet becomes an automatic representation option, add an
explicit sheet-count/page policy and a joint assignment plan above member
`Sheet.build()` calls. The plan must keep views, controlled annotations, datum
references, tables, notes, and their feature tags in coherent groups; use the
existing cross-sheet requirement evaluation as the final authority. Numbered
pages, common revision/title data, and cross-sheet references need one drawing-
set identity and export contract. A caller-fixed one-sheet drawing stays one
sheet and reports incompleteness if required content cannot fit. ADR 2 needs
an amendment for this new automatic planner, with tests; it is not a late
overflow action inside the one-sheet solver.
