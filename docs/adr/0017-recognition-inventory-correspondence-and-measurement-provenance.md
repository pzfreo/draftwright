# ADR 0017 — Recognition inventory, feature correspondence, and measurement provenance

- **Status:** Proposed
- **Date:** 2026-08-03
- **Deciders:** Paul Fremantle (pzfreo)

## Context

ADR 0013 standardised an individual recogniser: geometry-only input, a deterministic list of
typed frozen records, injected dependencies, and no drafting concerns. ADR 0015 then adapted
those records into the `PartModel` IR and deliberately allowed completeness lint to read
recognised geometry independently of the planner. Both decisions remain sound.

What neither ADR decides is how a physical feature keeps its identity after recognition.
Today the relevant facts are spread across several representations:

```text
recognition record → IR Feature → approved dimension → render candidate → annotation
```

Each arrow preserves enough values to draw the usual case, but not a stable answer to:

> Does this particular placed (or dropped) annotation define this particular physical
> requirement recovered from the solid?

The absence is now observable in several independent areas:

- recognition records for flats, slots, pads, plates, steps and grooves can describe
  overlapping regions, with ownership settled by bespoke pairwise exclusions in
  `model/detect.py`;
- pattern membership is excluded by Python identity for some records and value identity for
  others;
- analysis owns a subset of the recognised inventory and passes it as optional arguments to
  `build_part_model`, which recognises or derives the remainder;
- coverage lint correctly refuses to trust only the `PartModel`, but then has to associate
  physical features with the finished drawing by parsing labels, witness geometry and leader
  positions;
- a rendered annotation does not uniformly carry the identity of the world-space measurement
  it states, so cross-build fidelity and redundancy checks infer it from page geometry;
- an empty recogniser result cannot distinguish “absent” from “ambiguous”, “unsupported”, or
  “rejected because another interpretation owns this region”.

PR #1011 made the architectural gap especially clear. A check for whether a machined flat's
only A/F definition survived placement grew successive heuristics for label grammar, leader
type, leader-tip proximity, projected face extent, cylinder radius, axis line and axial stock
extent. The edge cases were real; the repeated failure was trying to reconstruct semantic
association after the pipeline had discarded it.

This is broader than flat recognition. #1002, #1004, #1005, #1006 and #1009 record the same
missing correspondence at annotation, correlated-dimension and completeness boundaries;
#1013 and #1015 show that the recognised feature itself also needs stable stock/region
identity. Fixing each consumer with another matcher would create several competing answers to
one semantic question.

## Decision

**A recognition run produces one first-class recognition result. It contains the shared
geometry inventory, accepted feature records, physical measurables, and
recognition diagnostics. A named reconciliation stage resolves overlapping candidate claims.
Drafting policy turns measurables into applicable requirements. Deterministic identities for
accepted features, measurables and requirements propagate through the IR, compiler and
placement result, so completeness lint compares recognised requirements with explicit
downstream outcomes instead of reconstructing association from presentation.**

The conceptual pipeline becomes:

```text
                           declared Feature input (ADR 0011)
                                      │
B-rep → geometry inventory → candidates → reconciliation → accepted recognition records
                                      │                         │
                                      │                         ├─ diagnostics
                                      │                         └─ physical measurables
                                      ▼
                              PartModel / compiler
                                      │
                             applicable requirements
                                      │
                           approved or suppressed intent
                                      │
                             placed or dropped result
                                      │
                                completeness lint
```

The exact Python dataclasses and identifier spelling are implementation details. The following
ownership rules are the decision.

### 1. One recognition result per recognition run

Recognition orchestration has one explicit result rather than an informal bundle of local
variables and optional `build_part_model(...)` arguments. It is the owner of:

- reusable geometric substrate such as cylinders, planar regions, adjacency and profile facts;
- feature candidates and the evidence/regions they claim;
- accepted geometry-only recognition records;
- physical measurables recovered from those records;
- ambiguity, unsupported-topology and rejection diagnostics.

Individual `recognise_<feature>` functions keep the ADR 0013 contract. The aggregate sits above
them; it does not move drafting semantics into `recognition/` or require every recogniser to be
rewritten at once.

`RecognitionResult` owns the recognition portion of today's `Analysis`; it does not replace
layout/sizing state such as the bounding box, zones, page/scale classification or view facts.
`analysis`, `model/detect.py`, `score.py` and geometry-based lint consume the result or a
documented projection of it. They do not independently assemble subtly different feature
universes.

**Migration baseline at proposal time.** Of roughly seventeen recognisers, seven inventories
are carried on `Analysis`, eight are injectable into `build_part_model`, and eight are neither:
countersinks, grooves, plates, chamfers, fillets, flats, step shoulders and face levels. “Neither”
does not necessarily mean “run twice” — some run only inside detection — but it proves that one
explicit orchestration result does not yet exist. This is a current-state ratchet for the
implementation plan, not a permanent target encoded by this ADR.

### 2. Candidate discovery and ownership reconciliation are separate

A local recogniser may propose a geometrically plausible interpretation. It does not silently
win ownership over competing interpretations. A named reconciliation stage decides conflicts
such as:

- a groove floor versus a boss or turned step;
- a pocket floor versus a global step level;
- a plate versus a staircase level;
- a pad profile versus a slot;
- a pattern versus its member features;
- flats on parallel or coaxial stock regions.

Candidates identify the physical regions/evidence they claim. Reconciliation records why one
candidate was accepted, combined, or rejected. The existing domain rules in
`model/detect.py` migrate into this stage incrementally; this ADR does not require a universal
constraint solver.

### 3. Identity is deterministic semantic data

Feature, measurable and requirement identity must not depend on Python object identity,
traversal order, annotation names, formatted labels, or page coordinates. In particular, an
enumeration index such as `solid_idx` is not stock identity: adding or reordering one solid can
renumber every later record. A stock/region identity is derived positively from geometry — for
example its axis direction and in-plane position, axial extent, diameter and other stable
relations needed to distinguish the material region — plus the feature kind, measurement role
and any required discriminator.

The identity need only be stable for equivalent recognition of the same part under the
project's geometric tolerances. It is not a promise of persistence across arbitrary topology-
changing CAD edits. Exact identifiers, quantisation and collision handling are settled by the
implementation plan and guarded with redetection tests.

A physical feature may create more than one measurable; drafting policy may combine or reject
measurables when forming requirements; a correlated requirement may render as several marks.
Therefore feature identity, measurable identity, requirement identity and annotation identity
remain distinct types rather than one overloaded key.

They are also distinct **Python types** — provisionally `FeatureId`, `MeasurableId`,
`RequirementId` and `AnnotationId`, implemented as frozen value types rather than four aliases
for a bare tuple or string. Passing a measurable identity where a requirement identity is
expected must be a static/runtime type error, not a valid lookup that happens to return the
wrong result. Their internal fields may reuse smaller typed geometry keys; consumers do not
assemble or unpack those fields independently.

This is not a flat-only correction. `Flat`, `Chamfer`, `Fillet`, `Groove` and `CounterSink`
currently carry an axis letter plus a point but cannot by themselves answer “which material
region am I on?”. `TurnedStep`, `Plate` and `Slot` already carry useful extents. Migration must
strengthen every thin record whose identity depends on material/region ownership rather than
inventing a special key for flats.

### 4. Measurables become requirements in drafting policy

A recognised **measurable** is a geometry fact: this flat has an across-flats of 25, this pocket
has a depth of 6, or these members form a grid with a given pitch. It may live in the
geometry-only recognition result and, eventually, in the shared recogniser package.

A drafting **requirement** is policy: this measurable must be stated for this drawing to be
manufacturing-complete. Drafting convention decides applicability, correlation and redundancy.
For example, several step measurables may become one correlated length-chain requirement, while
a groove-owned diameter may make a coincident boss measurable redundant. Recognition does not
make those drafting decisions.

That policy has one explicit, low-level home, provisionally `draftwright.requirements`: a pure
measurable→requirement mapping below both `model/` and `linting/` in the dependency DAG. It takes
geometry-only measurable data plus explicit drafting-policy inputs and returns typed
requirements; it imports neither the compiler/planner nor placed annotations. `model/` consumes
it when compiling intent, and `linting/` consumes the same mapping when forming its independent
physical-completeness inventory. The two callers may select different output projections, but
they may not reimplement applicability, correlation or redundancy rules.

The compiler records the fate of each applicable requirement:

- approved;
- deliberately suppressed by declared intent;
- not representable pending a named capability;
- rejected as redundant under a named owner;
- approved but dropped during placement;
- placed, with the resulting annotation identity or identities.

This is outcome/provenance data, not page placement input. It does not bypass ADR 0014 or let
recognition choose strips and coordinates.

### 5. Completeness lint stays independent without rerunning semantics backwards

ADR 0015's lint carve-out remains: completeness cannot take the compiled dimension set as its
only inventory, because an omission before planning would then be invisible. Its independent
ground truth is requirements formed from recognised measurables by the shared requirements
policy — independently of what the compiler claims to have approved.

Independence from the plan does **not** require independence from the recognition result, nor
does it justify recovering association from rendered text when provenance exists. Lint compares:

```text
requirements(recognised measurables, drafting policy) ↔ compiler/placement outcomes
```

It reports at least these distinct conditions:

- no intent was approved for a requirement;
- intent was deliberately suppressed;
- intent was approved but placement dropped it;
- an annotation states a conflicting value or identity;
- association is unverifiable because an external/manual annotation has no provenance.

Label and leader-tip inference remains a conservative compatibility fallback for annotations
not produced through the compiler seam. It must not silently certify a requirement when the
association is ambiguous. “Unverifiable” is preferable to a plausible wrong match.

### 6. Declared models keep their no-detection contract

ADR 0011 remains the second front door. Supplying a declared model does not cause automatic
feature detection merely to build or render it.

Physical completeness critique is a separate concern. A caller may explicitly request it, or
`Drawing.lint()` may lazily obtain and cache a recognition result when the documented lint
policy calls for geometry reconciliation. The cache belongs in the existing typed `BuildState`
(ADR 0005 / #639), whose builder fill site remains the single writer; it must not become another
ad-hoc private attribute on `Drawing`. That recognition is evidence for critique, not a
replacement for the declared model and not an input that widens its authored dimension set.

The implementation must make the distinction observable and testable: declared build/render
without physical critique performs no recognition; requesting physical completeness may do so
once.

### 7. Ambiguity is output, not absence

A recogniser that cannot distinguish two interpretations, or does not support a topology, may
still return no accepted record. The aggregate result also carries a diagnostic explaining the
gap and the evidence involved. A clean empty list is reserved for a confidently absent feature
within the recogniser's supported domain.

Diagnostics are geometry facts and remain below drafting policy. Their severity and user-facing
lint wording belong to draftwright's critique layer.

## Boundaries with existing ADRs

- **ADR 0013 remains authoritative for individual recognisers.** This ADR adds orchestration,
  evidence, reconciliation and identity above that contract. A future `b123d-recognisers`
  package remains geometry-only: it may expose measurable facts, but applicability,
  requirements, compiler outcomes and annotation provenance do not move into it.
- **ADR 0015 remains authoritative for the compiler waist.** Its “one inventory detected once”
  is clarified to mean one explicit recognition result per recognition run, not an informal
  collection split across `_analyse()` and `build_part_model()`.
- **ADR 0015's lint carve-out remains.** Lint is independent of the compiler's claimed
  completeness. It may consume the same recognition result as independent physical evidence
  and the shared requirements policy, but does not import `model/` or trust compiler output as
  its inventory.
- **ADR 0010 remains the annotation provenance seam.** This ADR specifies the semantic identity
  that provenance must carry; it does not create a second registration seam.
- **ADR 0016 remains the owner of authored dimensioning intent.** Suppression is a recorded
  compiler outcome and never inferred merely from a missing parameter or annotation.
- **ADR 0014 remains the owner of placement.** Requirement identity observes placement outcome;
  it does not constrain page coordinates.

## Consequences

### Positive

- Completeness checks stop reverse-engineering engine-produced annotations.
- Recognition conflicts become one explicit reconciliation problem instead of pairwise
  exclusions scattered through consumers.
- Sizing, detection, scoring and lint can share one coherent feature universe.
- Repeated recognition and its performance policy become visible and enforceable.
- New recognisers must state physical identity, claimed regions, measurables and conflicts,
  making their downstream drafting obligations discoverable.
- Ambiguous and unsupported geometry no longer looks indistinguishable from a feature-free part.
- Cross-build fidelity, redundancy and code-generation checks gain a world-space identity source
  rather than relying on page geometry.

### Costs and risks

- Stable geometric identity is tolerance-sensitive and requires explicit collision tests.
- Reconciliation introduces a new stage and migration work for existing exclusions.
- Carrying provenance through compound callouts and correlated sets is not always one-to-one.
- Recognition diagnostics can become noisy unless “unsupported” and “ambiguous” are narrowly
  defined.
- A large-bang rewrite would be risky. The aggregate and identifiers must be introduced around
  existing recognisers, then adopted feature family by feature family.

## Rejected alternatives

### Keep adding feature-specific coverage matchers

Rejected. PR #1011 demonstrates that labels and page geometry do not contain enough semantic
information to prove association generally. Each matcher would become a second feature
recogniser over the rendered page.

### Make lint trust `PartModel` or the approved dimension plan

Rejected. A recognition-to-IR or planner omission would disappear from both the drawing and the
inventory used to judge it, producing a clean false negative. The physical-requirement inventory
must remain independently derived from geometry.

### Rerun every recogniser inside every completeness check

Rejected. It duplicates expensive work and still does not solve correspondence. Independent
critique requires independent evidence, not repeated computation of the same evidence.

### Store build123d/OCP face objects as identity

Rejected. They are process-local implementation objects, do not satisfy ADR 0013's serializable
record contract, and are unstable across import/redetection. Records may derive deterministic
signatures from topology and measurements without leaking kernel objects.

### Put drafting requirements into the future shared recogniser package

Rejected. Recognition supplies geometry, evidence and measurables. Drafting convention turns
them into applicable requirements and decides which are redundant or presentational. The shared
boundary remains geometry-only.

## Migration constraints

This ADR deliberately does not approve an immediate rewrite. The implementation plan should:

1. define a minimal `RecognitionResult` around today's inventories without changing output;
2. introduce geometry-derived stock/region identity using the fixtures and evidence from
   #1013/#1015, explicitly replacing traversal-derived `solid_idx` rather than promoting it;
3. define the shared measurable→requirement policy below `model/` and `linting/`;
4. define the four distinct identity types and outcome records, including
   correlated/N-annotation cases;
5. thread those identities through the existing ADR 0010 registration seam;
6. move the conflict rules already in `build_part_model` into a named reconciliation layer;
7. migrate completeness checks from presentation matching to requirement outcomes one family at
   a time;
8. retain explicit fallbacks for external/manual annotations;
9. delete superseded matchers and duplicate recognition calls as each migration lands.

Each phase must preserve the declared-model no-detection path and must include a mutation or
counterexample demonstrating that the new guard observes the semantic failure it claims to
cover.

## Required guards before acceptance

- one recognition orchestration call per automatic build, with shared substrates not rescanned;
- a declared build without physical completeness critique performs no recognition;
- redetecting an unchanged part yields the same feature and measurable identities; applying the
  same drafting policy yields the same requirement identities; recompiling and placing it yields
  deterministic annotation identities;
- `FeatureId`, `MeasurableId`, `RequirementId` and `AnnotationId` are distinct Python types, and
  the public/internal APIs that exchange them reject a key of the wrong identity domain;
- both compiler and lint requirements are produced by the one shared policy; changing a policy
  rule changes both inventories, while deleting either call site's use of it fails a guard;
- two parallel or coaxial stock regions receive distinct identities, while two faces of one
  double-D/hex feature reconcile as intended;
- every accepted record has one converter/reconciliation home and every applicable requirement
  has a compiler outcome;
- a suppressed requirement is distinguishable from a missing and a placement-dropped one;
- compound and correlated dimensions retain one requirement identity across all rendered marks;
- removing an intent-to-annotation provenance link makes completeness report unverifiable or
  missing rather than matching by coincident text;
- an ambiguous/unsupported fixture emits a recognition diagnostic rather than a clean absence;
- linting the same built drawing twice does not rerun recognition.

The reusable evidence from #1011 is its geometry fixtures and stock/region counterexamples.
Its downstream label/tip matching machinery is not phase-zero implementation: it is the
presentation reconstruction this ADR is intended to retire.

## Related work

- #1002 — placed annotations have no reliable measurement identity
- #1004 / #1005 / #1006 — measurement-identity gaps in correlated and cross-build checks
- #1009 — recognition↔compiler correspondence model
- #1012 — authored suppression versus physical completeness
- #1013 / #1015 — flat stock identity and recognition ownership
- #1014 — repeated flat recognition and declared-model constraints
- #1011 — the completeness matcher that exposed the missing correspondence
