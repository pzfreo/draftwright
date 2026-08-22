# ADR 0017 — One recognition result per run; correspondence is evidence-gated

- **Status:** Accepted; narrowed after phase 1 (Amendment 1, 2026-08-05), with
  external-package/cache ownership clarified by Amendment 2 (2026-08-15) and turned
  edge-treatment applicability widened by Amendment 3 (2026-08-22)
- **Date:** 2026-08-03
- **Deciders:** Paul Fremantle (pzfreo)

## Context

ADR 0013 standardised an individual recogniser: geometry-only input, a deterministic list of
typed frozen records, injected dependencies, and no drafting concerns. ADR 0015 then adapted
those records into the `PartModel` IR and deliberately allowed completeness lint to read
recognised geometry independently of the planner. Both decisions remain sound.

At proposal time, recognition was not one inventory. Some records were found in analysis,
some in `build_part_model`, and some again in completeness lint. The relevant facts were
spread across several representations:

```text
recognition record → IR Feature → approved dimension → render candidate → annotation
```

Two problems were combined in the original proposal:

1. **Recognition ownership:** which run owns the geometric inventory, how consumers reuse it,
   and how automatic and declared builds differ.
2. **Semantic correspondence:** whether a physical requirement can be followed through the
   compiler to a placed, suppressed, dropped, missing, or unverifiable outcome.

The first problem was observable and bounded. The second was also real — PR #1011 had to
reconstruct a flat's association from label grammar, leader type, leader-tip proximity,
projected face extent, cylinder radius, axis line, and stock extent — but the original ADR
specified a whole identity, requirements, outcome, reconciliation, and diagnostics programme
before one end-to-end slice had shown which of those mechanisms was necessary.

Phase 1 resolved recognition ownership. It did not resolve semantic correspondence, and the
user-facing completeness problem did not move. Amendment 1 therefore accepts the proved
ownership contract and makes the remaining architecture evidence-gated rather than treating
it as an approved implementation sequence.

## Decision

**A recognition run produces one explicit, immutable `RecognitionResult`. One orchestration
owns every public recognition family and the reusable dependencies between them. Automatic
model construction and physical critique consume that result or a documented projection of
it; they do not independently assemble competing recognition universes. The result belongs
to Draftwright's `RecognitionCache`, held by the existing typed `BuildState`; its controlled
build/lazy-critique path contains the cache's only fill sites. The orchestration and immutable
result type live in `b123d-recognisers`, while cache lifetime remains a consumer concern.**

This is the accepted decision. It is intentionally narrower than the original proposal.

The following are **not** accepted by this ADR:

- a universal `FeatureId` / `MeasurableId` / `RequirementId` / `AnnotationId` taxonomy;
- a shared `draftwright.requirements` module;
- a general requirement-outcome ledger;
- a named reconciliation stage for every current pairwise exclusion;
- a complete recognition-diagnostics model.

Those remain candidate responses to the semantic-correspondence problem. Epic #1018 gates
them behind two end-to-end completeness slices, beginning with flats and then slots/patterns.
An extension is adopted only when a failing or unverifiable fixture demonstrates the missing
contract and a targeted mutation proves it is load-bearing.

## Amendment 1 — Scope Correction

The original record remained `Proposed` while phase 1 was implemented. It treated recognition
ownership and a general correspondence architecture as one programme. Phase 1 established
the ownership contract but did not establish that the proposed identity, requirements,
outcome, reconciliation, and diagnostics layers were necessary or sufficient for the
user-facing completeness defect.

Amendment 1 is this ADR's first acceptance. It accepts the implemented ownership rules below
and reclassifies the rest as hypotheses behind evidence gates. It does not erase a previously
accepted commitment or claim that phase 1 delivered semantic completeness.

## Amendment 2 — external orchestration, consumer-owned cache

ADR 0013 Phase 2 moved `RecognitionResult` and `build_recognition_result` unchanged into
`b123d-recognisers`; stable release `v0.1.0` now supplies them. It did not move build lifecycle
into the geometry package.
`BuildState.recognition_cache` owns a Draftwright `RecognitionCache`; `ensure(part)` calls the
external orchestration at most once, and the compatibility `BuildState.recognition` property
delegates to that state during the migration window. Automatic analysis fills it before
record→IR conversion. A declared render remains recognition-free, while later physical critique
may fill it lazily once. This preserves every landed guard below without a second recogniser
implementation or package-level global cache.

## Amendment 3 — turned edge treatments are applicable recognition families

`b123d-recognisers` 0.2.9 recognises conical chamfers and toroidal fillets on turned parts.
Those two families therefore no longer share the rotational applicability gate: the one
orchestration runs them for both prismatic and turned solids and carries their immutable
records in `RecognitionResult`. Plates and angled prismatic steps remain classification-gated.

This widens the shared geometry inventory; it does not move drafting policy into the package
or claim that the consumer already renders every returned record. Draftwright's separate
record-to-IR, Sheet emission, placement, provenance, and completeness work lands one family at
a time: #1254 covers turned chamfers and #1281 tracks turned fillets. The ownership rule is
unchanged: consumers reuse the aggregate records and must not rescan the solid when they add
support.

## Accepted Contract

### 1. One orchestration owns the recognition universe

`RecognitionResult` is the explicit result of one recognition run. It owns the inventories
and shared geometry/evidence that current consumers reuse, including the cylinder substrate,
accepted feature records, classification-gated inventories, face levels, and riser evidence.

Individual `recognise_<feature>` functions keep the ADR 0013 contract. The aggregate sits
above them; it does not move drafting policy into `recognition/` and it does not replace
layout/sizing state such as the bounding box, zones, page/scale selection, or view facts.

Every public `recognise_*` family is classified by the fail-closed `MIGRATED` / `DEFERRED`
manifest. A new family cannot appear without an ownership decision. A deferral carries a
reason code and, where applicable, the issue that removes its constraint.

Owning a family is distinct from always running it. Applicability gates live inside the one
orchestration. Since Amendment 3, chamfers and fillets run for both prismatic and turned
solids; plates and angled prismatic steps remain gated away from turned parts.

### 2. Consumers reuse the result; they do not rescan per concern

Analysis, automatic model construction, and geometry-based critique consume the run's
`RecognitionResult` or an explicit projection of it. They may apply pure consumer-specific
policy to shared evidence; they may not repeat the underlying recognition scan merely because
their projection differs.

The step-shoulder split is the reference shape: `recognise_risers` owns the scan, while
`project_step_shoulders` applies a caller's level set without touching the solid again.
Prefer separating shared evidence from a pure projection before declaring a recogniser
inherently caller-specific.

Standalone tools outside the build pipeline may run recognition independently. The decision
is one result **per recognition run**, not one process-global result for a solid.

### 3. `BuildState` owns result-to-build provenance

The result is stored in typed `BuildState`, whose builder/lazy-critique path is the single
writer. This makes the result's relationship to its drawing structural: engine consumers do
not accept an arbitrary aggregate and then attempt to prove it came from the same part.

This answers **result-to-build provenance only**. It does not answer which recognition record
became which IR feature, requirement, or annotation. That record-to-feature correspondence is
the subject of the evidence gates below.

`lint_prismatic_coverage(recognition=...)` is a known channel outside the structural ownership
path (#1032). The preferred correction is to remove or narrow the channel when that boundary
is next touched, not to add identity machinery solely to police a foreign aggregate that no
engine path supplies.

### 4. Recognition remains geometry-only

Part classification is recognition: `_is_rotational` reads bounding-box proportions, external
cylinder diameter, and concentricity. Drafting consumes the classification for view selection
just as it consumes a recognised hole diameter; that does not turn the geometric fact into
drafting policy.

`build_recognition_result` currently receives that classification because
`_classify_geometry` still lives in analysis. Moving its derivation is correct tidying (#1037),
but it is not required by the accepted ownership contract and carries no user outcome on its
own.

### 5. Completeness stays independent of the plan

ADR 0015's lint carve-out remains. Completeness cannot take the compiled dimension set as its
only physical inventory: a recognition-to-IR or planner omission would then disappear from
both the drawing and the inventory used to judge it.

Independence from the plan does not require rerunning recognition and does not justify
certifying engine output by matching formatted labels, leader tips, witnesses, or page
coordinates. A future migrated family must start from the shared recognition result and use
explicit semantic correspondence where the engine supplies it. An external/manual annotation
without such provenance is `unverifiable`, not silently covered.

Exactly how recognised facts become drafting requirements and how their downstream outcomes
are represented is left to the evidence gates. This ADR accepts the boundary, not a premature
universal data model for it.

### 6. Declared build/render remains recognition-free

Supplying a declared model (ADR 0011) does not trigger automatic feature recognition merely to
build or render it.

Physical completeness critique is separate. `Drawing.lint()` may lazily obtain one recognition
result in `BuildState` when physical critique is requested. `export()` also requests that
critique because it logs its diagnostics. Therefore the observable call contract is:

| path | recognition calls |
|---|---|
| automatic prismatic build | 25 families, once each |
| automatic turned build | 23 families; two prismatic-only families gated out by design |
| declared build/render | zero |
| first physical critique/export of a declared drawing | at most one aggregate |
| subsequent lint of the same drawing | zero additional calls |

The lazy result is evidence for critique. It does not replace the declared model or widen its
authored dimension set.

## Boundaries With Existing ADRs

- **ADR 0010 owns annotation provenance at the registry seam.** Evidence-gated correspondence
  should reuse that seam before introducing a parallel annotation registry.
- **ADR 0013 remains authoritative for individual recognisers.** The aggregate orchestrates
  their serializable, geometry-only records; it does not weaken that contract.
- **ADR 0014 remains authoritative for placement.** Recognition and correspondence may rank
  or account for intents, but never choose page coordinates or bypass the shared solve.
- **ADR 0015 remains authoritative for the compiler and lint independence.** This ADR supplies
  shared physical evidence; it does not make the approved plan its own completeness oracle.
- **ADR 0016 owns authored suppression.** A future outcome relationship must distinguish that
  deliberate intent from planner omission or placement drop without inventing a second
  suppression source.

## Evidence-Gated Extensions

The original sections on identity, requirements, outcomes, reconciliation, and diagnostics are
retained here as hypotheses, not commitments.

### Gate 1 — flat requirement to outcome

Use #1011's adversarial geometry, not its presentation matcher. Prove that a flat's independent
physical requirement can be followed to an engine outcome without label, tip, witness,
projection, annotation-name, or page-coordinate inference.

The slice must distinguish at least:

- placed;
- deliberately suppressed by authored intent;
- dropped during placement;
- missing;
- unverifiable because semantic provenance is absent.

It must keep parallel and disjoint coaxial stock regions distinct while combining the two
faces of one double-D/hex definition. Existing `DimensionId`, compiler omission data, and the
ADR 0010 registry seam are used before introducing new global identity types or another
registry.

Success proves only that the current contracts, plus any narrowly demonstrated addition, are
sufficient for flats. Failure identifies the missing fact; it does not by itself approve or
reject the original phases 2–6.

### Gate 2 — off-centre slot and N:1 pattern correspondence

The second slice must exercise shapes the flat slice does not:

- a lone off-centre slot whose recognition location and deliberate IR-frame convention differ;
- N recognised members compiled as one slot-pattern feature and compound annotations;
- directional/cardinality-sensitive location coverage, where one placed direction must not
  satisfy another.

The flat contract should be reused. Duplication or failure across these two structurally
different families is the evidence for a generic abstraction.

### Gate 3 — decide each abstraction separately

After both slices, decide independently:

- whether a stable recognition/source identity is required;
- whether feature, measurable, requirement, and annotation identities need distinct runtime
  types;
- whether duplicated applicability policy justifies a shared requirements module;
- whether placement outcomes need a general requirement ledger;
- which observed conflicts are competing interpretations needing named reconciliation;
- which cases are records too thin to express facts recognition already knows.

Every adopted abstraction must cite a failing or unverifiable fixture, the smaller alternatives
considered, the families/call sites it simplifies, and a mutation proving the contract.

## What Phase 1 Taught

### Fix a thin record before adding reconciliation

The original proposal listed flats on parallel or coaxial stock under a future named
reconciliation stage. #1013 solved the live defect by carrying the owning cylinder's
`axis_line` and `stock_span` on `Flat`, following ADR 0013's rule: when a recognition record
cannot express what the recogniser already knows, fix the record.

Before adding reconciliation, ask whether the case is a genuine contest between plausible
interpretations or merely a record that discarded decisive evidence. Groove-floor-versus-boss
and pattern-versus-member may still be genuine contests; flats were not.

### Identity detail must follow the supported geometry

The original identity sketch named axis **direction**. #1013 shipped an axis letter plus
in-plane position and axial extent. A dominant-axis letter is not a canonical direction for
slanted stock. That divergence remains paired with slanted-flat rendering in #1036 because
fixing either half alone would be untestable in a drawing.

`Flat`, `Chamfer`, `Fillet`, `Groove`, and `CounterSink` can all be described as thin records,
but only `Flat` had a demonstrated drawing defect: it grouped equal definitions without an
`n×` count. Fillets count, chamfers and grooves render per feature, and countersinks ride on
their holes. Strengthening the others is candidate identity work, not correctness debt.

### Guard the claim, not a proxy

Seven guards written during phase 1 initially asserted more than they established. Module
bindings stood in for functions, empty results stood in for skipped scans, a parameter's
existence stood in for non-narrowability, and whole-build call counts stood in for a specific
consumer.

CI and existing oracles exposed several defects; targeted mutations established whether the
named guard protected its claim. Every semantic guard added under the evidence gates must be
shown to fail under the mutation that breaks its claimed contract.

## Consequences

### Positive

- Automatic builds and physical critique share one coherent recognition universe.
- Repeated recognition and its cost are visible and mechanically guarded.
- Declared build/render preserves its no-detection contract without silencing physical
  critique when the caller requests it.
- New public recognisers fail closed until their orchestration ownership is classified.
- Consumer-specific filtering can evolve as pure projection over shared evidence.
- Correspondence architecture must now earn its shape through user-visible slices.

### Costs and risks

- The aggregate is a broad internal contract and must not become a dumping ground for drafting
  state.
- A classification or dependency error inside the one orchestration affects every consumer,
  so per-family call and semantic counterexamples remain necessary.
- The accepted ownership contract does not itself make completeness trustworthy; presenting
  phase 1 as that user outcome would recreate the false confidence this work is meant to remove.
- Deferring generic identities may produce one narrow interim relationship. Gate 2 exists to
  distinguish a useful small contract from duplication that warrants generalisation.

## Rejected Alternatives

### Keep adding feature-specific presentation matchers

Rejected for engine-produced annotations. PR #1011 demonstrates that labels and page geometry
do not contain enough semantic information to prove association generally. External/manual
annotations may remain unverifiable; they must not silently certify an ambiguous requirement.
Existing presentation-derived checks remain compatibility debt until an evidence-gated slice
migrates them; their presence does not establish that semantic correspondence has landed.

### Make lint trust `PartModel` or the approved plan as physical ground truth

Rejected. A recognition-to-IR or planner omission would disappear from both the drawing and
the inventory used to judge it, producing a clean false negative.

### Rerun recognisers inside each completeness check

Rejected. It duplicates expensive work and still does not solve semantic correspondence.
Independent critique requires independent evidence, not repeated computation of that evidence.

### Store build123d/OCP face objects as identity

Rejected. They are process-local implementation objects, violate ADR 0013's serializable-record
contract, and are unstable across import/redetection.

### Implement the original phases 2–6 as one programme

Rejected by Amendment 1. Phase 1 produced a useful ownership contract but no user-visible
completeness slice. The remaining abstractions are evaluated separately after Gates 1 and 2.

## Landed Guards

- [x] One orchestration call per automatic build.
- [x] Shared cylinder substrate and migrated families are not rescanned by model construction.
- [x] Every public `recognise_*` family is `MIGRATED` or carries a reason-coded deferral.
- [x] Declared build/render performs no recognition.
- [x] Physical critique of a declared drawing obtains at most one cached aggregate.
- [x] Repeated lint returns equivalent results without rerunning recognition.
- [x] Remaining classification-gated families are owned but skipped for inapplicable part
  classes.
- [x] A counterexample/mutation fails when the cache, gate, manifest, or shared-evidence contract
  it protects is broken.
- [x] Complete-wire repeating radial-profile evidence is scanned once by the orchestration;
  declared-gear critique projects axis/count correspondence from it without rescanning (#1087).

These guards accept the narrowed ADR. The former acceptance list for typed identities, shared
requirements, general outcomes, reconciliation, and diagnostics belongs to the evidence-gated
extensions and is not an uncompleted acceptance bar for the ownership decision.

## Related Work

- #1018 — evidence-gated implementation tracker
- #996 — manufacturing-completeness outcome
- #1009 — recognition-to-compiler correspondence failures
- #1002 — annotation measurement provenance already landed
- #1004 / #1005 / #886 — residual compound and correlated provenance gaps
- #1012 — authored suppression versus physical completeness
- #1013 / #1015 — flat stock identity and recognition ownership
- #1014 — repeated flat recognition and declared-model constraints, resolved by phase 1
- #1011 — discovery branch and adversarial flat fixtures
- #1032 — foreign recognition-result injection channel
- #1034 — independent flat-callout placement gap
- #1036 — slanted-flat rendering and canonical direction
- #1037 — classification derivation tidying
