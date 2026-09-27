# #1866 — Global annotation conflict policy (design proposal)

**Status: proposed, not implemented.** This records the decision that the
existing placement mechanisms need to share. It does not change the drawing
engine or authorize the candidate-first default. ADR 1 owns the one compiler
pipeline, ADR 2 the placement solve and caller constraints, ADR 4 declared
intent, and ADR 5 independent evidence and honest failure.

## What exists, and the missing seam

| Existing mechanism | Current owner | What it cannot decide alone |
| --- | --- | --- |
| Ranked strip solve, bounded ink alternatives and trace | `layout.py`, `annotations/_common.py` | Whether another representation preserves a displaced requirement. |
| Routed leaders, shared GD&T ink and feature schedules/tables | `annotations/` | Whether a table or another view should replace competing inline ink across the drawing. |
| View, scale, page and detail planning | `builder.py`, `compose.py`, `annotations/sections.py` | Whether the whole requirement set is better served by a permitted second sheet. |
| Exact annotation ownership, measurement/satisfaction and table-cell identities | `registry.py`, `reporting.py`, `document_evidence.py` | Which alternative carrier was eligible *before* rendering, and why it won. |
| Recognition/declared provenance, drops and independent lint | `reporting.py`, `linting/`, `document.py` | A pre-render choice trace; their existing post-render coverage remains authoritative. |

`Document` already supports *authored* sheets sharing one source authority; it
does not automatically partition a part. The local priority rungs (AUTO,
PRINCIPAL, AUTHORED, MANDATORY) remain useful inside a corridor, but are not a global
answer: a glyph can disappear because an equivalent table row now carries its
claim, or a clean sheet can still be missing a required measurement.

## Decision contract

The policy operates on one normalized obligation set: automatic builds retain
recognition-owned requirement identities; authored `Sheet` builds use declared
intent identities and provenance rather than pretending they have a raw
recognition requirement ledger. Each obligation has an identity, owner,
origin (authored/imported/automatic), required or optional disposition,
declared `priority`/`pin`, and eligible
representations. A representation names exactly the requirements it satisfies,
its feature and datum references, eligible views/sheets, and the typed placement
candidates it would submit to the *existing* solve. A table row may replace a
callout only when its feature identifier and all of that callout's required
claims survive. There is one canonical carrier per requirement unless an
explicitly justified cross-reference or intentional repeated statement is
recorded. No conclusion is inferred from an annotation's display name.
The existing registry's feature, measurement, satisfaction and exact table-cell
identities, together with report/document carrier attribution, are the
post-render evidence seam. Reuse them; only eligible *pre-render alternatives*
and the reason for choosing one are missing. Do not create a second coverage
ledger or change lint to trust planner-selected carriers.

For the caller's fixed constraints, compare complete plans in this order:

1. **Feasibility and truth.** Preserve every supported required obligation,
   source ownership, datum relationship, witness, and caller-fixed page, scale,
   projection and sheet-count constraint. An infeasible plan cannot become
   complete by dropping a required glyph, changing its meaning, or silently
   enlarging the sheet. A `pin` anchors its natural candidate and raises its
   local survival rank; it is not an arbitrary coordinate or a guarantee that
   infeasible ink is drawn.
2. **Legibility.** Among complete feasible plans, prefer clear view and text
   ink, valid view separation and readable leaders. Reuse existing corridors,
   routes, schedules/tables and justified details/sections before omitting
   optional ink. A crowded baseline is no excuse for introducing a new
   conflict; an impossible fixed-one-sheet request returns an explicitly
   incomplete result with the conflicts named.
3. **Restraint and economy.** Only after required meaning and legibility are
   protected, remove redundant or optional *generated* statements. Respect
   authored priority among otherwise equivalent choices; prefer the least
   duplication and sheet cost under ADR 2's scale-before-sheet ordering. Do
   not promote an annotation type, such as GD&T or a dimension, by type alone.

Tie-breaking is deterministic: compare the ordered set of satisfied requirement
identities, then declared rank, measured ink/crossing cost, representation cost,
and finally stable source/candidate keys. Never use iteration arrival, object
address, or platform-dependent raster output. The planning layer may choose
among typed representations; it must not become a second coordinate placer or
modify the local solver's exact tie convention.

If two required obligations conflict under every permitted plan, neither is
silently dropped. The result names both source identities, attempted
representations, blocker ink/constraint, and the unsatisfied outcomes. The
same applies to an infeasible pin. Lint still judges the *placed* drawing
independently; the planner's own coverage claim is not lint's denominator.
For a useful best-effort drawing when no complete plan exists, maximize the
number of fulfilled required identities, then compare their declared
priorities and the legibility/tie rules above. No annotation type supplies a
hidden importance score. That drawing remains
explicitly **incomplete**; best-effort rank cannot turn it into a candidate
win or production admission.

## Conditional multi-sheet option

A second sheet is considered **only** when the caller permits it, and before
discarding required content. The proposed planning input defaults to a
one-sheet maximum; an explicit caller policy may permit two and constrain
each sheet's page and scale. A fixed one-sheet or fixed page/scale request is
never relaxed. The second sheet is a coherent representation choice, not a
late overflow bin. Plan views together with dependent dimensions, callouts,
tables, notes, datum references, cutting-plane/detail markers, and sheet
identity. Prefer a primary geometry/datum/overall-control sheet and a
feature-specific detail or section sheet when that grouping tells a clearer
manufacturing story; this is a preference, not a mandatory template. Keep a
table with its feature identifiers, a GD&T frame with an unambiguous controlled
feature and referenced datums, and cross-sheet references explicit. Evaluate
coverage and ink over the drawing *set*; a clean lint result on each member
does not prove set-level completeness. Fixed-one-sheet callers retain an
honest incomplete result instead of an added sheet.

The currently built `Document` source authority and report are the foundation,
not a second recognition run. The planner must not copy provider objects across
the IR boundary or infer a requirement from a rendered label. Stable sheet
numbers and shared identity/revision metadata are required before automatic
two-sheet output can be claimed.

## Evidence and delivery tests

Every choice should record a compact trace: requirement identities and origins,
eligible representations, selected carrier, rejected alternatives and why,
local ink witnesses, any optional omission, and every unresolved required
outcome. A report must distinguish “represented elsewhere,” “optional
omission,” “required but unplaced,” and “not recognised/unsupported.” A clean
lint on a sparse page cannot be reported as complete.

Implementation should be reviewed in small slices, each with the unchanged
mechanism as a regression floor: (1) pre-render carrier-option and choice trace
using the existing registry/report identities; (2) same-batch and cross-view
conflict decisions using the shared solve;
(3) table/detail alternatives; (4) permitted two-sheet planning and document
identity. Focused tests need an authored pin that cannot fit, equivalent
callout-to-table coverage, two competing required obligations, and a crowded
real part at a fixed one-sheet constraint. A separate two-sheet real-part test
must show a coherent partition and set-level coverage improvement without
repeating unrelated annotations. Do not rerun an unchanged expensive baseline
to prove a planner-only or report-only change.

The drafting standards cited in #1866 constrain drawing presentation and
interpretation; they do **not** prescribe this product's universal drop order
or semantic sheet partition. Those rankings and the permitted-sheet policy
are Draftwright decisions and should be documented as such, not attributed
to ISO or ASME.
