# ADR 0017 — One recognition result per run; correspondence is evidence-gated

- **Status:** Accepted; narrowed after phase 1 (Amendment 1, 2026-08-05), with
  external-package/cache ownership clarified by Amendment 2 (2026-08-15) and turned
  edge-treatment applicability widened by Amendment 3 (2026-08-22), and the 0.4.6
  prismatic step inventories incorporated by Amendment 4 (2026-08-30). Amendment 5
  (2026-09-01) incorporates the 0.4.10 prismatic blind-slot inventories; Amendment 6
  (2026-09-02) adopts the rectangular family's compiler semantics while retaining an
  evidence gate around completeness. Amendment 7 (2026-09-02) closes that rectangular
  completeness gate with independent physical evidence and semantic outcomes; Amendment 8 does
  the same independently for the round-bottom family. Amendment 9 (2026-09-02) adopts the 0.4.12
  additive inventories fail closed while retaining the existing paired-ramp consumer meaning.
  Amendment 11 (2026-09-02) retains the provider's raw accepted-occurrence evidence in the same
  consumer-owned cache without adding a second recognition run. Amendment 12 (2026-09-03)
  records the first conversion-time, run-local occurrence→IR ownership bindings. Amendment 13
  (2026-09-03) records explicit member ownership for hole, slot, and pocket groupings. Amendment
  14 records settled ownerless consumer-policy outcomes per accepted occurrence. Amendment 15
  records each accepted countersink as absorbed by its exact same-run hole owner. Amendment 16
  records the conditional direct-or-step-ladder owner of every accepted channel occurrence.
  Amendment 17 records each turned-profile step as directly represented or absorbed by its exact
  groove owner. Amendment 18 records direct and multi-feature legacy ownership for accepted
  through-step occurrences. Amendment 19 records the direct, consolidated, turned-step, or groove
  owner selected for every accepted round-boss occurrence. Amendment 20 gives every accepted
  face-level and riser evidence occurrence an explicit evidence-only outcome. Amendment 21
  publishes the first bounded, versioned JSON projection
  of the completed raw occurrence-disposition ledger.
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

This widens the shared geometry inventory; it does not move drafting policy into the package.
Draftwright's separate record-to-IR, Sheet emission, placement, provenance, and completeness
work landed one family at a time: #1254 consumes turned chamfers and #1281 consumes turned
fillets. Profile-view routing for turned edge-treatment leaders remains Draftwright policy:
#1276 carries that presentation state through IR and emitted Sheet declarations, then lets the
shared placement solve choose the page position. The ownership rule is unchanged: consumers
reuse the aggregate records and must not rescan the solid when they add support.

Because the package records intentionally share one type across planar/conical chamfers and
cylindrical/toroidal fillets, schema-2 records carry the surface-family discriminator on each
record as ``turned``. Standalone model detection applies the same rotational family filters
itself. Draftwright never guesses the surface subtype from a matching axis letter; for a turned
record it may rotate the physical circumferential anchor about the nearest external-cylinder
axis from the shared substrate onto the selected profile plane, preserving its axial station
and radius.

## Amendment 4 — 0.4.6 adds three prismatic-only step inventories

`b123d-recognisers` 0.4.6 adds circular blind steps, paired ramp steps, and through steps to
the aggregate. Like plates and angled prismatic steps, those inventories are inapplicable to a
turned build and remain gated inside the one orchestration. At 0.4.6, a prismatic aggregate
therefore ran 28 public families once each; a turned aggregate ran 23, with five prismatic-only
families gated out by design.

Ownership does not imply invented drafting semantics. Draftwright consumes the three new
immutable inventories from the shared result. All three now have evidence-gated record-to-IR,
declaration, generated-code, annotation, and two-requirement completeness paths under #1382.
Circular blind steps retain their oriented terminal-to-open centreline and transverse quarter
arc as correspondence evidence; radius and depth share one solver-owned end-view leader while
remaining independently addressable. This preserves the one-inventory contract without
re-performing recognition or parsing rendered labels for completeness.

Consumer ordering remains a drafting-policy decision. Automatic through-step lowering supports
every principal run axis. For X/Y runs, face levels plus shoulders/plates retain ownership only
when their coordinate intervals (including an envelope-defined complement) prove both physical
legs. That complete alternate projection is `inapplicable`; axis or family presence alone is not
evidence, and the alternate owner's exact measurement identities must themselves be placed or
structurally satisfied. Its authored suppression, placement drop, or missing ink remains visible
as that outcome instead of becoming `inapplicable`. A partially covered occurrence instead lowers
as the aggregate owner and removes exact matching legacy fragments. The explicit Sheet declaration
permits the same local two-leg grammar on every principal axis. This is a pure projection of the
shared aggregate, not a second recognition scan.

## Amendment 5 — 0.4.10 adds two prismatic-only blind-slot inventories

Recognisers 0.4.10 adds rectangular and round-bottom blind slots as two more prismatic-only
physical families. The current aggregate runs 30 families for a prismatic part and 24 for a turned
part, with six gated out: the two new gated families are offset by Plate becoming applicable to
turned builds. At adoption, Draftwright kept both additions visible but deferred under #1421 until
their depth, open-end, bottom-form, declaration, annotation, and completeness semantics are
independently reviewed. At that point, a live occurrence was reported as an unscored recognised
family; it was not coerced into the existing through-slot or rectangular-pocket grammar and did
not enter the audited completeness denominator.

## Amendment 6 — rectangular blind-slot semantics cross the compiler waist

The rectangular family is no longer deferred at every consumer boundary. Its released schema-v1
record lowers one-to-one into a dedicated `RectangularBlindSlotFeature`: the penetration/run axis,
source-envelope mouth sign, U-section width/depth axes and material-opening sign are immutable
structural facts; width, capped run and flat-bottom depth are three independently addressable
measurements. `Sheet.rectangular_blind_slot(...)` declares the same exact contract, generated code
round-trips it, and one `OPEN SLOT W × L × D DEEP` leader carries only compiler-approved values
through the shared post-drain placement solve. The aggregate inventory is consumed directly and
ordinary slots, pockets and channels do not regain ownership.

Automatic planning approves all three measurements. Authored planning may approve any non-empty
subset; its callout spells the surviving roles as `WIDE`, `LONG`, and `DEEP`, so an approved
identity cannot silently vanish or be mistaken for another position in the compound grammar.

This amendment strengthens the rectangular family's IR, declaration, generated-code and drawing
stages only. Its independent physical completeness denominator and outcome ledger remain deferred
under #1421, so occurrences stay visible as an unscored recognised family rather than entering the
audited score on annotation presence. The round-bottom family remains fully deferred until its
separate flat-width and floor-radius semantics are delivered. Native raw and rigidly moved framed
tests establish measurement/callout parity without treating an ORTHOGONAL frame representative as
authored material-axis identity.

## Amendment 7 — rectangular blind-slot completeness is evidence-backed

Each aggregate rectangular blind-slot occurrence now contributes three independent physical
requirements: U-section width, capped mouth-to-terminal run and flat-bottom depth. Correspondence
uses the complete released structural record—axis, both opening signs, transverse axis roles, three
sizes and centre—at the generated Sheet program's documented 0.001 mm precision. Duplicate sources,
duplicate IR matches, malformed records, near neighbours and parameter/span disagreement are
unverifiable rather than guessed.

The requirements then follow their exact `DimensionId` values through the ADR 0010 registry seam to
`placed`, `satisfied_by_structured_note`, authored `suppressed`, placement `dropped`, `missing`, or
`unverifiable`. The ledger does not parse labels or names and does not use views, projected geometry,
leader tips, page coordinates or the approved plan as its physical denominator. Compiler omissions
are read only to distinguish authored suppression after source-to-IR correspondence is established.

An independently authored seven-case corpus fixes all six principal run-axis/open-side orientations
and a separately sized specimen: 7 physical occurrences, 21 parameters and 21 finished drawing
outcomes. Removing the rectangular inventory produces zero observed outcomes while the authored
21-requirement benchmark remains, so recognition loss cannot self-certify. Direct declarations and
executed generated declarations earn the same outcomes, and all three approved identities must be
present for a fully complete automatic callout. The rectangular family now enters the audited
completeness denominator; round-bottom blind slots remain separately deferred.

## Amendment 8 — round-bottom blind-slot semantics and completeness are evidence-backed

The round-bottom family now lowers one-to-one into its own `RoundBottomBlindSlotFeature`; it is
not a through slot, pocket, channel or rectangular blind slot with a rendering flag. The complete
released structural identity—run axis/open sign, width/depth axes, material-opening sign, run,
radius, straight bottom-flat width and centre—crosses the record-to-IR boundary unchanged.
`Sheet.round_bottom_blind_slot(...)` declares that same explicit contract and generated code
round-trips it. No private recogniser geometry or new provider API is required.

Each occurrence contributes exactly three independent manufacturing requirements: capped
mouth-to-terminal run length, straight bottom-flat width and the equal side radius. Total opening
width (`flat_width + 2 × radius`) and profile depth (`radius`) are derived values, so counting them
again would inflate the physical denominator. One compiler-fed
`ROUND-BOTTOM OPEN SLOT …` leader carries only approved parameters and their own tolerances through
the shared post-drain solver. An authored subset keeps explicit `LONG`, `BOTTOM FLAT` and `R` roles;
structural facts cannot reconstruct a suppressed measurement. Candidate arrows target proved
terminal/floor/round-side material, never raw page coordinates or the open mouth.

Correspondence uses the full public record at the generated program's 0.001 mm precision and then
requires the exact parameter-id-to-value/span map. Duplicate source or IR keys, malformed records,
near neighbours, duplicate/unknown/missing parameters and span disagreement are unverifiable.
Outcome evidence comes only from the annotation registry, structured satisfaction, authored
omissions and recorded placement drops; labels, names, views, projections, tips and page positions
cannot certify completeness.

An independently authored seven-case corpus fixes all six signed principal orientations plus one
separately sized occurrence: 7 physical slots, 21 parameters and 21 finished drawing outcomes.
Removing the provider inventory produces zero observed outcomes while the authored denominator
remains 21. Direct declarations, executed generated declarations, native raw recognition and
arbitrarily moved framed recognition preserve the same manufacturing meaning. The family therefore
enters the audited completeness denominator independently of rectangular blind slots.

## Amendment 9 — 0.4.12 additive inventories are visible before they are semantic

Recognisers 0.4.12 expands the one aggregate with physical `blends` and `oriented_slots`
inventories plus derived `oriented_slot_patterns`. The capability manifest also adds three family
declarations and four public records. Draftwright adopts those structural contracts without
claiming a drawing interpretation: every record has an explicit unconsumed registry home, every
inventory is classified as undecided under #1430, and every semantic boundary is declared
`deferred`. A non-empty inventory is reported as recognised-but-unscored rather than silently
ignored or admitted to the audited denominator.

That disposition is necessary evidence gating, not a permanent non-requirement classification.
Blend records may overlap the existing Fillet family's physical ownership. Oriented slots carry
free-axis directions and authoritative `SectionPassage` sources that the axis-letter slot IR cannot
preserve, while their pattern records carry member and vector-lattice identity. Separate review
must decide those semantics before adding dedicated IR, Sheet vocabulary, generated code, drawing
requirements, or completeness outcomes.

The release also widens recognition behavior for the unchanged `PairedRampStep` schema to shallow
nonzero angles. That is compatible with its existing acute-angle IR and two-requirement consumer
contract. A released shallow specimen follows the existing aggregate-to-IR, declaration,
generated-code, compiler, solver, and completeness path without a new Sheet word or placement API.

## Amendment 10 — Released convex Blend chains are independent radius requirements

Review under #1433 settles the schema-v1 `Blend` disposition. The provider aggregate already owns
the only lawful precedence decision: an accepted dimension-worthy `Fillet` suppresses a Blend only
when exact defining-face evidence covers the complete chain. Draftwright consumes that reconciled
inventory once and neither rescans topology nor repeats face ownership policy.

Each remaining accepted Blend is one complete convex cylindrical rolling-ball chain and one
`blend.radius` requirement. It lowers to dedicated `BlendFeature` IR retaining `axis`, canonical
full `axis_direction`, subdivision-invariant `at`, radius and `side="convex"`. It does not lower to
`FilletFeature`: the dominant axis letter is not a substitute for a non-principal direction.
`Sheet.blend(...)` is the explicit declaration word; generated programs replay every released
field. A single shared-solver leader carries `R{radius}` or groups equal radii as `n× R`; the
feature anchor is physical geometry, never a page coordinate.

The completeness observer independently validates exact public record and exact IR types, retains
occurrence multiplicity, and joins only the complete released value before following
`blend.radius` measurement identity to placed, structured-note, authored-suppressed, dropped,
missing or unverifiable. Raw-default and provider-framed arbitrary rigid-motion tests preserve the
same radius requirements. `blends` therefore moves from deferred to supported and joins the
audited denominator. Unreleased concave, toroidal and path schemas are not latent support.

## Amendment 11 — Raw accepted-occurrence evidence shares the one run-owned cache

The released `b123d_recognisers.evidence.build_recognition_evidence` entry point returns one
run-scoped `RecognitionEvidence` whose `.result` is the existing immutable aggregate and whose
opaque feature/face references authorise provider-owned accepted-occurrence queries. Raw automatic
analysis and lazy physical critique now acquire recognition through that entry point once and keep
the evidence beside its exact result in `RecognitionCache`. Scale, page and view retries carry the
complete cache forward; they do not retain the aggregate while orphaning its reference authority.

`Drawing.recognition()` remains the established result view, and
`Drawing.recognition_evidence()` is the matching experimental read-only evidence view. A bare
injected result is valid but has no evidence. Draftwright never reruns recognition to backfill one,
and rejects a result/evidence pair that did not come from the same run. The explicit framed path is
also evidence-less until the provider exposes framed accepted-occurrence evidence; that contract is
tracked upstream by
[`b123d-recognisers#463`](https://github.com/pzfreo/b123d-recognisers/issues/463) rather than
reconstructed from a second raw scan.

This amendment extends the accepted ownership unit, not the completeness denominator. It adds no
persistent topology identifier, report schema, record-to-IR inference, manufacturing intent,
compiled-plan dependency, placement path, or visual output. Later #1438 slices must demonstrate the
consumer disposition rules independently before this evidence can support an authoritative report.

## Amendment 12 — Direct occurrence ownership is captured at conversion time

Draftwright now retains a sibling `RecognitionOwnership` ledger for raw automatic builds. For each
aggregate family whose adapter unconditionally converts one accepted record into one IR feature,
`model.detect` binds the provider's opaque `FeatureRef` to that exact newly-created feature at the
conversion site. Resolution uses exact same-run record identity: equal-valued copies, feature
ordering, labels, coordinates, face overlap, object addresses and topology indices cannot establish
ownership. The immutable ledger is paired with the same `RecognitionEvidence`, survives scale/view
retries with the stored sizing model, and is attached beside the model in `BuildState`; provider
references do not enter `PartModel`, compilation, placement, generated programs, or visual output.

This first vertical slice covers only unconditional 1:1 adapters: blends, chamfers, circular blind
steps, double-D bores, fillets, flats, grooves, pads, paired ramp steps, polygonal bosses/stock, and
rectangular/round-bottom blind slots. An expected direct occurrence without a recorded owner is
retained as `unexpectedly_missing`. Every grouped, nested, absorbed, classification-only, or
deferred family remains explicitly `unclassified` until its N:1 or 1:0 consumer rule is implemented;
it is not falsely reported as missing. Declared builds and evidence-less framed/bare aggregates do
not invent ownership. `Drawing.recognition_ownership()` exposes the non-serializable ledger as an
experimental read-only view so consumers need not reach into private build state. This remains
reporting foundation: no public report schema or completeness claim is introduced by this amendment.

## Amendment 13 — Group and pattern members have explicit final owners

The provider evidence ledger exposes accepted physical holes, slots, and pockets as opaque
`FeatureRef` occurrences. Its derived hole/slot/pocket pattern records are not occurrences and
Draftwright does not manufacture occurrence identity for them. At the existing conversion decision
sites, a lone unpatterned member is now recorded as `represented`; every member folded into one
same-spec hole group or one derived pattern feature is recorded as `absorbed`, with a closed,
family-specific reason code and the exact shared final IR owner. Pattern membership supplied by the
same aggregate establishes this N:1 decision. Record equality, feature order, coordinates, face
overlap, and topology traversal do not.

A member's integer position within that run-local owner is retained only as transient lowering
lineage. When member-specific AP242 PMI splits a grouped `HoleFeature`, the lowering seam returns the
exact source-member partition alongside its replacement objects. Each occurrence is rebound to its
exact final owner; a resulting singleton becomes `represented`, a remaining multi-member owner stays
`absorbed`, and absent or ambiguous lineage fails closed rather than retaining a stale pre-lowering
object. This position is neither serialized nor presented as a persistent topology/report ID.

The amendment uses only the released public `RecognitionEvidence` surface and the existing
consumer-owned aggregate. It adds no recognition scan, manufacturing inference, compiled-plan
dependency, annotation placement, generated-script/report schema, or visual change. ADR 0010's
annotation provenance and ADR 0014's solve are untouched; ADRs 0011/0015 retain the IR/compiler waist;
ADR 0013 remains the provider boundary; ADR 0020's framed path remains evidence-less pending the
released upstream contract. Declared builds therefore remain recognition-free until physical
critique/report/export, and these ownership outcomes alone do not claim drawing completeness.

## Amendment 14 — Settled ownerless policy is explicit per occurrence

Five accepted physical families intentionally have no Draftwright IR owner. Each raw automatic
build now projects the already-reviewed top-level disposition in the consumer capability
declaration onto every exact same-run `FeatureRef`: angled steps, passages, and prismatic pockets
are `unsupported`; oriented slots are `deferred`; and repeating radial profiles are
`evidence_only` because they may critique separately authored gear intent but cannot create it.
Each outcome carries a closed reason code and, for unsupported/deferred policy, the declaration's
existing tracking issue. This is a pure Draftwright policy projection; the geometry provider does
not decide drafting support.

Occurrence identity remains the provider-issued opaque reference. Equal-valued oriented slots
therefore retain distinct outcomes, while records, faces, registry order, coordinates, and
topology are never used to reconstruct identity. These occurrences are not expected to produce an
IR feature and cannot become `unexpectedly_missing`; conversely, an ownerless policy outcome cannot
certify a drawing requirement as complete. Remaining supported nested/classification-only families
stay `unclassified` until their exact ownership rules are separately demonstrated.

This adds no provider API, recognition scan, persistent/report identifier, IR feature,
manufacturing inference, annotation, requirement, compiled-plan dependency, generated artefact, or
visual change. The policy is derived from the existing fail-closed consumer capability declaration
rather than copied into a second registry. ADRs 0010 and 0014 are untouched; ADRs 0011 and 0015
retain the declared/compiler boundaries; ADR 0013 retains provider geometry versus consumer policy;
and ADR 0020's framed path remains honestly evidence-less until a released provider contract exists.

## Amendment 15 — Nested countersinks retain their exact hole owner

Every accepted raw countersink occurrence now receives an explicit consumer result. When the public
aggregate attaches that exact record to a `HoleRecord.csink`, it receives an `absorbed` outcome and
shares that hole's final `HoleFeature` owner. The binding is made only after the parent hole has an
owner and retains the parent's transient member lineage, so single holes, same-spec groups,
patterns, and later PMI splits preserve one bore/countersink feature and one countersink requirement
rather than duplicating either.

The correspondence uses the released public evidence records and same-run object identity. It does
not match dimensions, coordinates, faces, record equality, or traversal order. A countersink that
is accepted but absent from its parent hole, whose parent is unowned, or whose binding is duplicated
fails closed as `unexpectedly_missing` or a conversion invariant violation; it cannot silently look
complete. No recogniser API change or second scan is required.

This adds no standalone countersink IR feature, manufacturing inference, compiled-plan dependency,
annotation placement, generated artefact, report ID, topology ID, or visual change. ADRs 0010 and
0014 retain the established hole annotation provenance and shared solve; ADRs 0011 and 0015 retain
the semantic Sheet/IR waist; ADR 0013 remains the released provider boundary; and ADR 0020's framed
path remains evidence-less pending the upstream correspondence contract.

## Amendment 16 — Channels retain the consumer's conditional owner

Every accepted raw channel occurrence now enters the supported-owner denominator. In a genuine
multi-axis plate assembly, the existing channel adapter produces a `ChannelFeature` and records that
exact object as `represented`. A monolithic Z-depth rebate continues to use the established
prismatic `StepLevelFeature`; the channel is `absorbed` only when that final ladder contains its
floor and both width-axis shoulders, coupled through one retained body-local face support and that
exact support occurrence's riser provenance over the channel's depth span. This records the
existing consumer classification where it is made rather than reconstructing ownership from a
finished model.

Direct cross-record coordinate comparisons allow half of the `Channel` record's two-decimal
publication cell. A shoulder reconstructed from independently published centre and width allows
the composed 0.0075 loss against raw support bounds, plus half of the shoulder projection's 0.001
publication cell against its three-decimal output (0.008 total). Comparisons add one scale-aware
float ULP only at the boundary; provider occurrence identity and final-support retention remain
exact. Thus ordinary
or large translations cannot erase honest ownership, while the tolerances cannot bridge a
different published channel coordinate.

The support/floor/shoulder check is deliberately fail closed. A cross-axis channel or disconnected
body cannot inherit an unrelated Z ladder merely because matching scalar facts exist, and a
filtered or absent defining support, level, or shoulder leaves the accepted occurrence
`unexpectedly_missing`. Multiple exact channel records may share one aggregate ladder when their
body-local provenance reaches it, but value-equal support occurrences, feature ordering, rendered
dimensions, labels, topology indices, or page coordinates never establish that relationship.

The implementation consumes the released same-run public `Channel` record and existing aggregate;
it adds no provider API, recognition scan, persistent/report identity, manufacturing inference,
compiled-plan dependency, annotation placement, generated artefact, or visual change. ADRs 0010 and
0014 remain untouched; ADRs 0011 and 0015 retain the semantic Sheet/IR waist and recognition-free
declared build; ADR 0013 keeps geometry recognition separate from consumer classification; and ADR
0020's framed evidence boundary is unchanged.

## Amendment 17 — Turned steps retain direct or groove ownership

Every accepted turned-step occurrence now enters the supported-owner denominator. An ordinary
profile band records the exact `StepFeature` created by its existing adapter as `represented`. When
the established groove-precedence rule suppresses a narrow floor band, that step is `absorbed` by
the exact `GrooveFeature` created from the same-run groove record. The groove retains its own
represented occurrence outcome; sharing its final feature does not manufacture a duplicate
requirement.

The relationship is captured where the consumer already evaluates
`groove_owns_turned_step_band()`. A band with no matching groove keeps its direct adapter. A
groove-suppressed band is absorbed only when exactly one groove from its unambiguously assigned
body-local profile owns it **and that groove owns exactly one accepted step band**. Ambiguity in
either direction leaves every affected accepted step `unexpectedly_missing`; in particular, a
sub-millimetre adjacent band inside the positional tolerance cannot silently share a groove whose
width and floor diameter do not represent it. Both the conversion decision and the ownership
ledger enforce the groove-to-step cardinality, so another builder caller cannot manufacture an
invalid snapshot. Feature order, equal diameters, labels, topology indices, and object addresses
are never used as persistent correspondence.

This consumes only released public `TurnedStep`, `Groove`, profile-key, and aggregate records. It
adds no provider API, recognition scan, persistent/report identity, manufacturing inference,
compiled-plan dependency, annotation placement, generated artefact, or visual change. ADRs 0010
and 0014 remain untouched; ADRs 0011 and 0015 retain the semantic Sheet/IR waist and
recognition-free declared build; ADR 0013 keeps provider recognition separate from consumer
precedence; and ADR 0020's framed evidence boundary is unchanged.

## Amendment 18 — Through steps retain direct or multi-feature legacy ownership

Every accepted through-step occurrence now enters the supported-owner denominator. A record that
crosses the aggregate adapter is `represented` by its exact `ThroughStepFeature`. When the existing
Z-up compatibility grammar intentionally preempts an X/Y-running aggregate, the occurrence remains
`represented` by the complete set of final IR features that jointly define both transverse legs:
the exact `PlateFeature` where a plate interval is direct, the `StepLevelFeature` for a retained
level or shoulder, and the `EnvelopeFeature` where the complementary maximum-side interval needs
the overall extent. One occurrence therefore has one disposition but may have multiple semantic
feature bindings.

The owner set is captured from the same fixed-point classification that decides whether the
aggregate lowers. The released evidence ledger limits plate candidates to exact same-run records
that share a defining face with the accepted through-step occurrence; the existing interval grammar,
not face overlap alone, still decides whether that candidate defines a leg. Equal spans in
disconnected bodies therefore cannot cross-own one another, while asymmetric plates remain valid.
Multiple scoped plate records matching one leg are ambiguous and force direct aggregate lowering.
Plate lineage then uses exact record identity; step-level and envelope claims resolve only to the
corresponding feature objects created by that build. The multi-feature owner set is conjunctive and
replacement validation is atomic: if any selected owner does not reach the final IR, no partial
binding is recorded and the accepted occurrence becomes `unexpectedly_missing`. Equal-valued
occurrences remain distinct, and the implementation does not reconstruct correspondence from
feature order, labels, rendered dimensions, topology indices, or object addresses.

The evidence-bearing aggregate is a classification input independent of whether an ownership
collector observes the build. The standalone detected-model entry obtains that same product in its
existing single aggregate run, while the ordinary build handoff carries it beside the projected
`RecognitionResult`; automatic and standalone model assembly therefore make the same decision
without a second recognition scan.

Supported paths that deliberately lack same-run evidence — framed recognition and a caller-supplied
aggregate — retain the established unscoped compatibility classification. They do not pretend to
know occurrence correspondence, reject a projection merely because the observation boundary hid
that evidence, or acquire another recognition run. Evidence-specific ownership remains unavailable
there; the model itself does not change solely because evidence cannot cross that boundary. ADR
0020's independently selected provider frame may still change coordinate-dependent classification,
as it did before this amendment; this ownership slice does not redefine framed recognition.

This consumes only the released public `ThroughStep`, `Plate`, aggregate, level, and riser records.
It adds no provider API, recognition scan, persistent/report identity, manufacturing inference,
compiled-plan dependency, annotation placement, or generated artefact. It does not alter rendering
machinery or established unambiguous projections; a newly proven ambiguous projection may instead
take the truthful direct aggregate path. ADRs 0010 and 0014 remain untouched; ADRs 0011 and 0015
retain the semantic Sheet/IR waist and recognition-free declared build; ADR 0013 keeps provider
recognition separate from consumer classification; and ADR 0020's framed evidence boundary is
unchanged.

## Amendment 19 — Round bosses retain their exact consumer-selected owner

Every accepted round-boss occurrence now enters the supported-owner denominator. A singleton that
crosses the prismatic adapter is `represented` by its exact `BossFeature`. Where the established
prismatic projection intentionally keeps one representative per diameter, every same-diameter
occurrence is `absorbed` by that one feature with explicit transient member lineage. When a boss
occurrence matches an existing turned-profile step, it is absorbed through that exact same-run
`TurnedStep` occurrence to the step's final owner: ordinarily a `StepFeature`, or the
`GrooveFeature` that already absorbs a groove-floor step. When the profile gate is absent but the
existing groove-floor predicate still suppresses a boss, the occurrence is absorbed directly
through the exact same-run groove record.

These are records of the existing consumer decisions, not a new grouping or rendering policy. The
diameter representative still follows the established insertion-stable tolerance grouping, and no
extra boss feature, count prefix, annotation, requirement, or generated declaration is introduced.
The accepted occurrence retains its own provider geometry beside the final IR owner, so a later
report can distinguish two equal-diameter physical bosses even though the current drawing projects
one representative. Turned correspondence is captured from the exact step record selected by the
existing axis-line, diameter, and span decision; final ownership is followed through the step's
already-recorded binding rather than reconstructed from the resulting feature's coordinates.

The conditional paths fail closed. A boss that matches zero or multiple candidate steps, two boss
occurrences competing for one step, a groove-floor group that lacks a unique same-run groove, or a
missing intermediate owner remains `unexpectedly_missing`; equal values, feature order, labels,
rendered dimensions, topology indices, page coordinates, and object addresses cannot manufacture a
binding. Chained correspondence retains its exact intermediate lineage through IR remapping, and
at most one boss may depend on either an intermediate occurrence or its final IR owner. Where a
turned-step route and the direct groove fallback compete for one final groove, the more specific
turned-step route wins and the other occurrence remains missing. Declared builds and evidence-less
framed or bare-result paths retain their existing recognition behavior without inventing
occurrence ownership.

This consumes only released public `BossRecord`, `TurnedStep`, `Groove`, aggregate, and evidence
contracts. It adds no provider API, recognition scan, persistent/report identity, manufacturing
inference, compiled-plan dependency, annotation placement, generated artefact, or visual change.
ADRs 0010 and 0014 remain untouched; ADRs 0011 and 0015 retain the semantic Sheet/IR waist and
recognition-free declared build; ADR 0013 keeps geometry recognition separate from consumer
classification; and ADR 0020's framed evidence boundary is unchanged.

## Amendment 20 — Face levels and risers are explicit projection evidence

Every accepted raw `FaceLevel` and `RiserEvidence` occurrence now receives an `evidence_only`
consumer outcome with a closed, family-specific reason code. This follows the released provider
manifest rather than inferring a new ownership relation: both record types have role `evidence`,
are deliberately absent from the provider feature census, and are not independent drafting
requirements. `FaceLevel` records are body-local support for the correlated height ladder;
`RiserEvidence` is the shared scan product from which consumers project `StepShoulder` values.

The face-level and riser capability families remain supported. Their evidence contributes to the
existing `StepLevelFeature`, sizing, and physical critique paths, but an individual evidence record
is not itself a feature that should demand a one-to-one IR owner. Reporting it as
`unexpectedly_missing` would manufacture a requirement the provider explicitly excludes;
reporting it as `absorbed` would falsely promise exact per-record correspondence through consumer
filters that intentionally operate on correlated level sets. The evidence-only outcome states the
narrow truth without downgrading the aggregate feature grammar.

The outcome is projected from the same run-owned evidence authority as every other occurrence.
Equal-valued body-local records remain distinct, while record equality, scalar coordinates,
feature order, face indices, and object addresses never become persistent identity. It introduces
no IR adapter, Sheet DSL word, recognition scan, provider API, annotation, requirement,
compiled-plan dependency, generated artefact, or visual change. ADRs 0010 and 0014 are untouched;
ADRs 0011 and 0015 retain the declared/compiler waist; ADR 0013 retains the released provider
boundary; and ADR 0020's evidence-less framed path remains unchanged.

## Amendment 21 — The raw occurrence ledger has a bounded versioned report

`Drawing.report()` now projects one raw automatic build's exact `RecognitionEvidence` and
`RecognitionOwnership` into the closed top level of `draftwright-report` schema version 1. Every
accepted occurrence receives a deterministic report-local ID, its provider family, public
JSON-compatible record and consumer-declared record schema version, one of the six settled
dispositions, a closed reason, optional tracking issue, and zero or more report-local final IR
owners. The counts cover every provider-issued accepted occurrence; an unclassified family,
ambiguous record schema, foreign authority, absent model, or unavailable ownership boundary
refuses the report instead of shrinking its denominator.

Occurrence IDs follow the provider's documented accepted-occurrence order and owner IDs follow
the final IR order. They explain references within one document only. The projection does not
serialize `FeatureRef`, `FaceRef`, transient group-member positions, topology traversal, object
addresses, or any identifier intended to survive another recognition run. Records cross only via
their public `to_dict()` surface and are normalized to strict JSON primitives with NaN and Infinity
rejected. A recorded owner missing from the current final model becomes `unexpectedly_missing`;
it cannot retain represented credit.

The report incorporates `Drawing.lint_summary()` but deliberately marks per-occurrence requirement
coverage `not-projected`. `bounded-clear` therefore means only that this bounded accepted-occurrence
and lint projection found no known blocker; it is not physical completeness, manufacturing
readiness, or evidence that recognisers found everything. Unsupported, deferred, evidence-only,
unexpectedly-missing, or lint-failing evidence produces `needs-attention`. Feature→requirement→
annotation reconciliation remains an independent later evidence gate and never uses the compiled
plan as its completeness denominator.

Schema version 1 is raw-automatic and in-memory only. Declared, provider-framed, foreign-result,
and bare paths refuse with `ReportUnavailableError` because they do not carry the exact
conversion-time ownership authority; no value/proximity reconstruction or second scan fills that
gap. Atomic writing, source/output manifests, generated-Python gap snapshots, declared runtime
reconciliation, and CLI/script sidecars remain separate vertical slices. The report path invokes no
placement and changes no PDF/SVG/DXF/PNG content. ADR 0010's annotation registry, ADR 0011's
recognition-free declared build, ADR 0013's released provider boundary, ADR 0014's shared solve,
ADR 0015's compiler waist, and ADR 0020's framed boundary remain unchanged.

## Amendment 22 — Report persistence is explicit and atomic

`Drawing.write_report(path)` persists the same schema-v1 document returned by `Drawing.report()`
as deterministic, strict, indented UTF-8 JSON. It writes and flushes a uniquely named sibling
temporary file before atomically replacing the destination, and removes that temporary on any
failure when the filesystem permits cleanup. A cleanup error never masks the primary persistence
failure. Report construction happens before temporary-file creation, so an unavailable or invalid
report cannot disturb an existing destination. Parent directories remain caller-owned.

Persistence is an explicit library operation, not a side effect of `Drawing.export()`. It does not
render, place, or change PDF/SVG/DXF/PNG content. Output manifests, generated-Python snapshots,
declared runtime reconciliation, and CLI/script sidecars remain later slices. This amendment adds
no recogniser call, provider API, correspondence inference, persistent topology identity,
annotation path, or completeness claim; Amendment 21's raw-only fail-closed report boundary and
all ADR 0010/0011/0013/0014/0015/0020 constraints remain unchanged.

## Amendment 23 — Plate ownership is conditional and AAG-backed

Accepted `Plate` occurrences now enter the conditional ownership family. A genuine multi-axis
plate represented by Draftwright's existing direct adapter retains its exact `PlateFeature`.
Otherwise, an occurrence is absorbed only when released same-run AAG evidence proves one of the
existing compatibility projections: its defining face supports the exact retained `FaceLevel`,
its defining face supports the exact `RiserEvidence` shoulder, or its defining face is shared by
an exact `Slot` member whose existing ownership already names the retained slot-pattern feature.
The corresponding final owners are respectively the step level, the envelope plus step level, or
the envelope plus slot pattern.

These multi-owner bindings are conjunctive: every recorded owner must remain in the final model or
the occurrence becomes `unexpectedly_missing`; partial credit is forbidden. Numeric interval and
support predicates live in one pure leaf shared by model conversion and Plate-completeness lint,
but numbers, labels, ordering, equal-valued records, and topology traversal never establish report
ownership by themselves. Whole-envelope and polygonal-boss compatibility remain completeness-only
until a released recogniser relation proves exact occurrence lineage. Malformed, disconnected,
ambiguous, and otherwise unproved cases therefore remain visibly `unexpectedly_missing`.

This amendment adds no recognition scan, provider API, persistent topology ID, rendering or
placement behavior, or inferred requirement. The raw automatic path records the conversion-time
decision only; declared and provider-framed boundaries remain unchanged under ADRs 0011, 0013,
0015, and 0020.

## Amendment 24 — Generated Python carries a bounded recognition-gap snapshot

`generate_sheet_script(...)` now retains the exact `Analysis` that produced its detected model and
embeds `DRAFTWRIGHT_RECOGNITION_SNAPSHOT`, a versioned JSON-compatible Python dictionary projected
from that run's evidence and ownership ledger. The compact snapshot includes only accepted
occurrences with `unsupported`, `deferred`, `evidence_only`, or `unexpectedly_missing`
dispositions. Ordinary represented and absorbed facts remain the existing editable semantic Sheet
declarations rather than being duplicated into a second model.

The snapshot is explicitly generation-time evidence, not present authority. It carries the public
record and consumer schema version, deterministic report-local occurrence ID, disposition, reason,
and tracking issue, but no provider reference, face reference, topology index, object address,
annotation coordinate, or persistent identity. For a STEP source, the generator resolves the
replay seam once, reads one immutable byte snapshot, and hashes those exact bytes. Recognition,
PMI, and any semantic-correction build consume a private copy of the same bytes, so path mutation,
symlink retargeting, and A→B→A replacement cannot split provenance from the generated model. Only
the original input basename and byte SHA-256 are embedded; the private copy is discarded. Before
committing the script, the generator makes a best-effort final observation and refuses a replay
target that is then missing or has a different digest. It cannot lock that pathname against a
later replacement, including one concurrent with the output write; the embedded digest is the
comparison boundary for the later fresh-runtime reconciliation slice, not a freshness guarantee
for a mutable source path. A live build123d object truthfully has no file hash. An empty snapshot is
named
`no_unrepresented_accepted_occurrences`, never complete or manufacturing-ready.

This slice adds no recognition call: model and snapshot project the same build-owned analysis.
It does not yet make the generated declared build a raw-report authority or reconcile an edited
script with current recognition; the visible runtime `write_report(...)` call, fresh drift
comparison, output manifests, and CLI sidecar default remain later slices. The snapshot neither
compiles nor places annotations, changes no visual output, and preserves ADRs 0010, 0011, 0013,
0014, 0015, 0017's one-run lifecycle, and 0020's framed boundary.

## Accepted Contract

### 1. One orchestration owns the recognition universe

`RecognitionResult` is the explicit result of one recognition run. It owns the inventories
and shared geometry/evidence that current consumers reuse, including the cylinder substrate,
accepted feature records, classification-gated inventories, face levels, and riser evidence.

Individual `recognise_<feature>` functions keep the ADR 0013 contract. The aggregate sits
above them; it does not move drafting policy into `recognition/` and it does not replace
layout/sizing state such as the bounding box, zones, page/scale selection, or view facts.

Every public physical family is classified by the provider's fail-closed registry/manifest.
A new family cannot appear without an ownership decision. That contract and its applicability
tests live with `b123d-recognisers`; Draftwright consumes the released aggregate and does not
import the provider's private roster to re-certify its orchestration.

Owning a family is distinct from always running it. Applicability gates live inside the one
orchestration. In the current release, plates, chamfers, and fillets run for both prismatic and
turned solids; angled prismatic steps, circular blind steps, paired ramp steps, through steps,
rectangular blind slots, and round-bottom blind slots remain gated away from turned parts.

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

This originally answered **result-to-build provenance only**. Amendment 12 records exact
occurrence→IR ownership for unconditional 1:1 adapters, and Amendment 13 adds explicit singleton,
grouped, and pattern-member ownership for holes, slots, and pockets. Amendment 15 adds exact nested
countersink→hole ownership, Amendment 16 adds conditional direct-or-step-ladder channel ownership,
Amendment 17 adds direct-or-groove turned-step ownership, and Amendment 18 adds direct or
multi-feature legacy through-step ownership. Amendment 19 adds direct, diameter-consolidated,
turned-step, or groove ownership for round bosses. Remaining supported conditional families, and the
general feature→requirement→annotation correspondence, remain subjects of the evidence gates below.
Settled unsupported, evidence-only, and deferred policy is explicit per accepted occurrence under
Amendment 14.

`lint_prismatic_coverage(recognition=...)` is a known channel outside the structural ownership
path (#1032). The preferred correction is to remove or narrow the channel when that boundary
is next touched, not to add identity machinery solely to police a foreign aggregate that no
engine path supplies.

### 4. Recognition remains geometry-only

Part classification is recognition: `_is_rotational` reads bounding-box proportions, external
cylinder diameter, and concentricity. Drafting consumes the classification for view selection
just as it consumes a recognised hole diameter; that does not turn the geometric fact into
drafting policy.

`build_raw_recognition_result` currently receives that classification because
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
| automatic prismatic build | 30 families, once each |
| automatic turned build | 24 families; six prismatic-only families gated out by design |
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
- [x] Shared cylinder substrate is not rescanned by model construction, and no public physical
  recogniser is invoked outside the aggregate on guarded consumer paths.
- [x] The released provider suite owns registry completeness, applicability, and exactly-once
  family execution; Draftwright does not duplicate those tests through private imports.
- [x] Double-D, Passage, PrismaticPocket, and repeating-profile consumer tests read released
  aggregate records and exercise Draftwright-owned physical correlation; the provider suite
  owns its private profile predicates, attribution, and reconciliation algorithms.
- [x] Consumer tests use released provider contracts except for the exact, shrinking private
  seams tracked by upstream #400 and #408; a repository-wide guard rejects any new exception.
- [x] Declared build/render performs no recognition.
- [x] Physical critique of a declared drawing obtains at most one cached aggregate.
- [x] Repeated lint returns equivalent results without rerunning recognition.
- [x] Remaining classification-gated families are owned but skipped for inapplicable part
  classes.
- [x] A counterexample/mutation fails when the cache, gate, consumer bypass, or shared-evidence
  contract it protects is broken.
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
