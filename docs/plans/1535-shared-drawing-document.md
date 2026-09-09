# Shared requirement ownership for a drawing document — #1535

Status: proposed for independent design review. `Document` and `schedule` below are proposed
spellings, not shipped APIs. This document changes no ADR text and does not complete #1529.

## Decision proposed

A document owns one source snapshot, working solid, recognition evidence and conversion-time
feature ownership. Member sheets author different dimensions and notes against that common
inventory. They compile and place through the existing engine. A document report reconciles
claims from those members against the existing recognition-owned requirement ledger.

Choose this common-build boundary over importing independently built drawings. An independent
sheet needs a verified cross-run mapping before it can contribute authority; identical STEP
hashes, coordinates, dimensions or report-local IDs do not supply that mapping. Implementing
such correspondence now would add a new identity problem to the field report's authoring
problem. The common-build approach costs one source intake and recognition, plus each sheet's
existing compile/projection/placement/critique. It also requires keeping the common inventory
alive. It does not promise a shared projection cache or reduced placement cost.

No automatic rear views, automatic sheet splitting or invented manufacturing decisions are
introduced. Each sheet's authored constraints remain binding; infeasibility is reported.

## Source, inventory and lifecycle

1. `Document.from_part(path, pmi=...)` reads and hashes one immutable byte snapshot, using the
   existing source-snapshot/intake facility. It retains the complete result of the existing
   detect/analysis seam, including exact conversion-time ownership. Do not call
   `Sheet.from_part()` separately for each member: that surface currently retains only features.
2. The common physical feature inventory is sealed before sheet authoring. Expose an immutable
   sequence of the existing IR Feature objects. A member Sheet contains that full inventory,
   with identical objects, plus its own nonphysical annotation features. Its authored dimension
   set and decorations are separate PartModel inputs. Omitting a dimension does not remove its
   owner from the common physical inventory.
3. A member accepts an exact common Feature through the existing `dimension(feature, id)` and
   `of(feature)` resolution paths. A foreign handle, equal-valued clone or independent Drawing
   is refused. A member cannot replace, append or delete a physical feature, including through
   aspect verbs that replace its Feature. This first delivery authors presentation/measurement
   selection, notes and controls over sealed detected geometry; common physical-feature editing
   is not implicitly implemented by sharing a mutable list. Supported non-replacing decorations
   remain per sheet. Refusals must identify the offending operation before partial mutation.
4. `build()` snapshots member intents and builds all members with the same source/ownership
   authority, seeding the existing recognition cache. It must not use private Drawing fields as
   a communication bus. Each Drawing still owns its annotation/placement state. Failure or
   cancellation of a member yields a named diagnostic; no complete package result is returned.
5. The returned document result owns its member Drawings and common authority. Reports recompute
   current outcomes on every call; no cached coverage survives edits. Public Drawing annotation
   edits may change coverage. If a member's physical model no longer contains exactly the
   common inventory, package reporting refuses. Calls and edits are serialized by the caller;
   concurrent mutation/reporting is not an atomic-snapshot promise.
6. First delivery uses the raw evidence boundary supported by the current strict report.
   Framed/bare/foreign models without exact ownership are explicit refusals, with no hidden raw
   fallback or second recognition. Ordinary single-sheet declared builds remain supported and
   keep their existing report limitations. A changed source needs a new document input.

The immutable byte snapshot and sealed membership are new *document lifecycle* responsibilities,
not durable topology identity. In-memory references expire with the document. Serialized IDs
are allocated once per report and cannot be passed back into another run as feature handles.

## Proposed Sheet DSL

This example shows the intended two-sheet authoring surface. The Python selection below reads
features from the one inventory; it does not join independently detected records. Both a
HoleFeature group and a PatternFeature may contribute to a physical operation. In particular,
the six through bolts must not be assumed to be one IR owner.

```python
from math import isclose
from draftwright import Document                  # proposed
from draftwright.model.ir import HoleFeature, PatternFeature

package = Document.from_part("frame-reference.step")

def operation(axis, diameter, through, expected_count, depth=None):
    selected = []
    for feature in package.features:
        bore = feature.member if isinstance(feature, PatternFeature) else feature
        if not isinstance(bore, HoleFeature):
            continue
        if (bore.frame.axis == axis and bore.through is through
                and isclose(bore.diameter, diameter, abs_tol=1e-6)
                and (depth is None or (bore.depth is not None
                     and isclose(bore.depth, depth, abs_tol=1e-6)))):
            selected.append(feature)
    assert sum(feature.count for feature in selected) == expected_count
    return tuple(selected)

bolts = operation("z", 2.4, True, 6)
sockets = operation("x", 2.4, False, 3, depth=1.5)
(hinge_path,) = operation("y", 1.1, True, 1)

general = package.sheet("general", page="A2", projection="third")  # proposed factory
features = package.sheet("features", page="A2", projection="third")
for sheet in (general, features):
    sheet.authored_dimensions().authored_views()
    for view in ("front", "plan", "side"):
        sheet.view(view)

for feature in package.features:
    if feature.kind == "envelope":
        for parameter in feature.parameters():
            general.dimension(feature, parameter.parameter_id)
general.section_view("A", at=0)

for feature in bolts:
    features.dimension(feature, "bore.diameter")
features.dimension(hinge_path, "bore.diameter")
features.of(hinge_path).note("SIX BEARING SEGMENTS — FIT / PIN RETENTION TBD")
for feature in sockets:
    features.dimension(feature, "bore.diameter")
    features.dimension(feature, "bore.depth")

# Proposed typed schedule: cells name measurements, never author numeric values.
# Location rows below expand the existing compiled per-member location vocabulary.
features.schedule(
    [(feature, ("location",)) for feature in bolts + sockets],
    name="hole_locations",
)
features.notes([
    "HINGE FIT / PIN RETENTION: ENGINEERING DECISION REQUIRED",
    "QUOTATION / DFM REVIEW — NOT RELEASED",
])
general.table([
    ["Pocket operation", "Quantity", "AF / depth", "Status"],
    ["Underside hex pockets", "6", "SEE ENGINEERING INPUT", "UNSUPPORTED BY DRAWING GRAMMAR"],
], name="pocket_schedule")

result = package.build()
report = result.report()
```

The pocket table is deliberately an authored engineering schedule with no coverage claim.
Entering AF/depth values there does not resolve six unsupported recess requirements. They stay
in the package denominator and report. Extending their drafting grammar is separate work, not
an accidental side effect of table text. The supported hole schedule is different: its cells
name existing addressable measurements and get values, units and tolerance content from the
compiler. The operation-selection predicate must be strengthened for any fixture with matched
axes/diameters but different counterbores, profiles, threads or other operation attributes; the
frame canary asserts the full observed operation facts, not just the count.

On the pinned datum-aligned frame, Quiddity 0.2.6 represents the six interrupted bearing
segments as one Ø1.1 through bore-path, with depth 53.2 mm. Its recognized count stays one.
The explicit six-segment note above is authored context and grants no additional credit.
The path's transverse location does not locate six axial bearing intervals. Individual segment
locations and tolerances are unavailable through this owner; supporting those requires
provider-issued segment identity, not a Draftwright topology scan or coordinate split.

Authored notes retain existing spelling, for example
`features.of(socket).note("DEPTH CONTROLLED BY PROCESS SHEET", satisfies=("bore.depth",))`.
That is explicitly authored semantic satisfaction, distinct from a drawn measured value. It
does not prove the literal numeric truth of arbitrary prose or a released manufacturing plan.
A note without `satisfies` grants no coverage; an unplaced note grants none. Foreign origins
and invalid parameter IDs remain errors. The sample leaves other known requirements visible;
selecting some operations is not a promise of package completeness.

## Typed schedule lowering

Add one schedule request to the existing IR/compiler, rather than giving tables a parallel
numeric evaluator. Each row contains an exact feature reference and canonical parameter IDs.
The existing `location` selector expands into the compiler's addressable per-member dimensions;
a bare ambiguous role is refused. This preserves identity for individual location changes.

A schedule request is part of the authored dimension set. The compiler approves its measurement
entries under the same formatting/tolerance/through-blind rules as feature annotations and
produces typed renderable cells. No renderer computes a diameter, count, depth or tolerance from
text or parameter absence. Unsupported or unaddressable cells fail explicitly; they cannot
become empty cells that still claim coverage. Cells that carry only descriptions are uncredited.

The existing table measurement, reservation and placement path receives those compiled cells.
Provenance includes the actual row/cell and measurement, and is registered only after successful
rendering. An overflow/drop carries the original identities into existing diagnostics. Reuse
existing placed-representation and structured-satisfaction registry mechanisms where their
semantics fit; a measured table cell is a measured representation, not an authored-note claim.
A table that displaces annotation ink must participate in the same preservation/rollback gates.
No coordinate positioning, automatic font shrinking or duplicate table placement engine.

## Requirement reconciliation and reporting

The common requirement catalog is a projection of `recognized_requirement_outcomes()` and
existing unsupported-occurrence handling. Establish it from the common evidence/model before
combining sheet claims. It is not a denominator reconstructed from authored dimensions or IR
parameters. Reuse the existing ledger producers and extend their projection seam where needed.

An internal requirement key has the family, exact same-run source record set and canonical
parameter identity. Profile-angle requirements instead retain the issued profile object and
its ordered support pair, as the existing report validates. Keys retain strong references and
validate `is`, not just integer object addresses. Placeholder rows with unknown parameter IDs
retain their full cardinality as opaque unresolved obligations; do not invent parameter names
or use a sheet's row position to join them. Retain whether cardinality is actually known:
a producer's single aggregate contract-failure row does not mean one physical measurement.
A corrupt aggregate without exact source authority continues to refuse strict reporting.
Unsupported occurrences synthesized by the current report remain in the same catalog.
Explicitly account for `inapplicable` outcomes too.

Every sheet must project onto this catalog without changing source partitions, multiplicities
or intrinsic applicability. A contradictory source-owned catalog shape is a
`ReportUnavailableError`, not a union, intersection or best-effort match. This is also the guard
against a member losing a physical feature or a provider family gaining requirements the
projector does not understand. Local evidence-dependent outcomes may differ legitimately.

In particular, the current plate ledger marks a wall thickness `inapplicable` when other
placed dimensions or structured notes establish it. On `plate-u-additive.step`, the exact same
recognition and physical objects produce `placed/inapplicable/placed` with supporting ink and
`missing/missing/missing` with an empty registry. The middle row's three dependencies are
reported only in the first case. The step-level alternate can even change the local parameter
from `thickness.length` to `?` when its supporting ink is removed. Neither the current v3 JSON
(which omits inapplicable rows) nor a catalog built by copying those local outcomes is enough.

Extend the existing ledger producers to expose source-owned obligations and their permitted
dependency alternatives before evaluating ink. Keep intrinsic non-applicability distinct from
evidence-dependent derivation. Canonical physical identity, unknown cardinality and the exact
dependency conjunction must remain available even on a member carrying none of the supporting
facts. Derive these from the ledger's existing rules and common source/ownership, not a new
parameter guess or numeric evaluator in the document projector. Preserve current single-sheet
results through the existing outcome projection.

Retain complete alternative recipes, not a flattened set of measurement IDs. The through-step
ledger also has alternative representations whose conjunction terms carry axis and interval
proof; flattening multiple alternatives loses which terms must hold together. Keep those
producer-owned supports and the OR-of-AND structure. For grouped holes, preserve each required
member's location components rather than mistaking one member's carrying claim for the whole
group. Pattern-anchor rules retain their own producer semantics. The document evaluator may
reuse these typed rules across member evidence; it must not invent geometry correspondence or
weaken the producer's support checks.

A conditional obligation stays in the common catalog. If every prerequisite in one
producer-issued alternative has confirmed carrying evidence in the document, report that
obligation separately as dependency-derived, with the rule and all supporting sheet/claim
identities. It receives at most one coverage credit and is not relabelled as a directly placed
measurement. Missing support removes that credit, not the obligation. Never infer a relation
from text or matching numbers, and never let circular/unresolved prerequisites establish their
own coverage. Intrinsic exclusions require a common source-owned reason; an isolated local
`inapplicable` flag cannot shrink the denominator. Retain conflicts and uncertainty in the
supporting engineering meanings alongside this coverage assessment.

The implementation gate must cover every currently emitted ledger family, profile sources,
unknown-cardinality placeholders, conditional plate and through-step alternatives, and group
member obligations; the frame alone does not prove that generality.

| Member outcomes for a catalog requirement | Package coverage | Information retained |
|---|---|---|
| One or more `placed` claims | One placed credit | All sheet/annotation/row identities |
| No placed claim; a rendered structured note satisfies it | One authored-note credit | Author and exact satisfied parameter |
| A producer-issued dependency alternative is fully carried | One dependency-derived credit | Exact relation and every supporting claim; no direct-placement claim |
| Supported but no carrying claim | Uncovered, no credit | Every suppressed/dropped/missing/unverifiable reason |
| Unsupported, deferred, or unprojected provider occurrence | Unresolved, no credit | Full count, disposition and limitation |
| Contradictory or unverifiable repeated ink alongside a carrying claim | Coverage may exist; fidelity needs attention | The conflicting claim is never excused |

A sheet's local `suppressed` state stays suppressed even when another sheet carries the fact.
The document records the relation to that other claim rather than rescoring the first sheet.
Repeated dimensions contribute at most one credit, while all claims remain inspectable. An
intrinsically inapplicable requirement is excluded only by the common source-owned rule,
not by omission or conditional derivation on a member. No `max(score)` or averaging of
single-sheet diagnostics.

Reconciliation also checks engineering meaning across members; retaining each sheet's lint
alone is insufficient. For each exact common owner and canonical parameter, collect confirmed
measurement claims through the existing `measurement_snapshot()` / `MeasurementClaim` seam.
Compare their approved semantic meanings: nominal value, tolerance, span, axis, discriminator,
location member and angular reference. Extend that seam for typed schedule cells. Do not compare
raw rendered text, annotation names, paper coordinates or whole before/after snapshots: sheets
intentionally carry different subsets and may present the same fact differently. The comparison
contributes no requirement denominator and performs no geometric correspondence search.

Normalize presentation-only fields through the existing typed measurement seam before comparing
engineering meanings. For example, confirmed `H7` class and signed-deviation displays retain
the same fit code and limits, but the current snapshot's `FitClass.show` makes raw tuple
equality differ. Ignore that display choice for document agreement while retaining the fit
code and limits; retain rendered text separately for its existing verification and diagnostics.
Do not loosen the existing edit comparison, which deliberately also detects presentation
changes, or use label parsing to manufacture semantic equivalence.

Different confirmed meanings for the same owner/parameter produce a document fidelity conflict,
attributed to both sheets and all participating annotations/cells. For example, Ø2.4 ±0.02 on
one sheet and Ø2.4 ±0.05 on another must conflict even when both are confirmed against their own
local approved model. Preserve both claims; neither wins by sheet order or tighter tolerance.
Coverage may record a carrying claim, but any such conflict prevents `bounded-clear`.
Unconfirmed or semantically unaddressable engineering ink remains an explicit uncertainty and
cannot establish cross-sheet agreement. Authored note prose is not parsed to prove agreement.
Controls or engineering decorations not represented by verified typed claims must likewise
retain an unassessed scope rather than inherit a clean result from the dimension comparison.

Extend the existing report schema with a version-4 **document scope**, keeping current
single-sheet version-3 output unchanged. Reuse `reporting.py` for projection/strict JSON/writes.
The v4 envelope has one source/producer/recognition catalog, explicit `identity_scope` of
`document-local`, stable-within-report sheet IDs, sheet-local requirement outcomes and lint,
and per-requirement carrying claims. References point into the one catalog; no JSON report
merger decides correspondence. Claims name sheet, annotation and optional table row/cell.

Package layout validity, audited recognition coverage, fidelity findings and unassessed
manufacturing intent remain separate fields. `bounded-clear` retains its existing limit and
requires all catalog obligations to be carried, no unresolved dispositions, and no known
layout/fidelity blockers on any member. Neither a 100% audited fraction nor a clean layout
certifies unrecognized geometry or a manufacturing release. If a typed identity, cardinality,
source snapshot or member is missing, the package report refuses rather than becoming smaller.

## Compatibility, serialization and replay

- Existing `Sheet`, `Drawing`, `build_drawing`, CLI and v3 reports retain their contracts.
  The document is an additional façade, not a replacement engine or reinterpretation of
  `Sheet.from_part`. Do not make ordinary declared rendering recognize eagerly.
- First delivery supports sequential explicit member-sheet builds and structured reports.
  Combining exported pages is presentation only and cannot establish package authority.
  Independent existing PDFs/Drawings/reports are not accepted as authoritative members.
- A report is an immutable JSON value when returned/written. It describes that read of the
  members. Keeping the Python result object does not freeze later Drawing edits.
- A source recipe can recreate the common source and reselect exact current inventory objects
  with explicit cardinality/operation assertions, as above. It does not replay old report IDs.
  A changed source or changed recognition partition must fail the recipe's assertions rather
  than silently reassigning edits. Durable cross-version feature identity and arbitrary
  document-script emission are not promised in this milestone.
- Input options and member declarations are reproducible recipe data. Automatic export or
  JSON deserialization must not reconstruct features from rendered annotations. The document
  report uses the existing atomic writer; artifact export retains its explicit failure limits.

## Delivery and acceptance gates

After design review, create focused implementation children under #1535 and link them in #1529:

1. **Common source and member builds, with shared outcome reporting.** Add the document input
   owner and Sheet binding, one recognition/ownership lifecycle, live-member reconciliation
   and v4 scope through the existing reporting module. The first useful delivery is a two-sheet
   small mixed-axis/through-blind part: move a dimension between sheets without changing the
   common denominator; remove it from both and lose the credit; retain a bad duplicate as a
   fidelity finding. Test locally confirmed ±0.02/±0.05 cross-sheet conflict, equal engineering
   meaning with different presentation, and unknown claims that cannot establish agreement.
   Refuse foreign/equal clones and stale physical membership. Exercise all ledger families
   and unknown-cardinality/profile counterexamples before claiming genericity. For the additive
   U-channel and step-level alternate, preserve the exact source-owned catalog with and without
   supporting ink, including when its prerequisite measurements are distributed across members.
   Remove a prerequisite everywhere and lose derived credit without losing the obligation;
   preserve local outcomes and retain conflicting/unconfirmed supporting claims. Verify H7
   class/deviation agreement separately from changed fit limits and changed tolerances.
   Through-step alternatives must not gain credit from incomplete terms taken from different
   alternatives or wrong axis/interval support; a grouped location must retain every member.
2. **Typed schedules and linked-note integration.** Extend the existing compiler and table
   placement/provenance. Verify per-member locations, toleranced values, through versus blind
   content, authored-note distinction, table overflow/drop and interrupted placement. Mutating
   a numeric cell or omitting a row must fail a named fidelity/coverage regression.
3. **Real-frame authoring and review canary.** Use the pinned datum-aligned frame and supported
   document recipe, six through bolts, three blind sockets, one recognized hinge bore-path
   with an uncredited six-bearing-segment note, and six retained
   unsupported pocket obligations. Execute the example, inspect exported sheets and compare
   exact requirement membership before/after moving a measurement between sheets. Document
   source/replay/ID limits and the unresolved engineering inputs. No private renderer patches.

Each child uses independent Google-style review against all five ADRs, fixes substantive
findings, passes `scripts/pr-check --full`, and merges only with required CI green. Design
approval alone does not close #1529. Performance and export-size work remains in #1534/#1537;
this delivery does not claim that sharing recognition optimizes every pipeline stage.

## ADR assessment and maintainer decision

- ADR 1: one IR/compiler, full common inventory, explicitly owned input and per-Drawing state;
  reporting remains beside lint. No provider objects cross into compiled schedules.
- ADR 2: this supplies the document model required before multi-sheet output. Existing sheet
  selection, authored constraints, typography and placement policy are unchanged.
- ADR 3: one exact recognition/evidence authority; no topology rescan or cross-run value join.
- ADR 4: existing feature/parameter vocabulary and authored sets; schedules are a representation
  of compiler-approved intent, not numerical text masquerading as a dimension.
- ADR 5: independent ledgers, preserved uncertainty and provenance; package aggregation cannot
  improve a score by hiding local omissions, unsupported geometry or contradictory claims.

This proposal supplies the explicit document model required by ADR 2; no live ADR text change
is proposed. Review must check that this remains within the existing invariants. If review
identifies an invariant/boundary change requiring an ADR amendment, obtain the maintainer's
explicit sign-off before writing that text, as required by CLAUDE.md. Design review does not
substitute for that sign-off.
