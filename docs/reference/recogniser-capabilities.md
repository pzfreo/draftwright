# Recogniser capability contract

Draftwright consumes the public capability manifest installed with `b123d-recognisers`; it never
reads a sibling checkout or a package-private file. The matching Draftwright-owned declaration is
implemented in `draftwright.recogniser_contract` and validated in CI.

The package manifest describes geometry evidence. Draftwright separately declares, for every
package family, its IR adapter, fluent `Sheet` surface, generated-Sheet behavior, drawing consumer,
completeness treatment, and documentation. A stage is either `supported`, `deferred`,
`not-applicable`, or `unsupported`, with the evidence required by that state. This separation lets
other CAD consumers apply different policy without making Draftwright policy part of the geometry
library.

Declared-feature geometry reads use a different public boundary. The installed package's
inspection manifest format 1 is joined to the separate Draftwright-owned declaration in
`draftwright.inspection_contract`. That contract admits inspection API major 1 and exactly the
consumed `b123d_recognisers.inspection` symbol schemas: the five geometry readers, their public
result/error types, the cylindrical surface parameter layout, bevel rejection reasons, and the
semantic names and units of the Double-D tuple. It also checks the exact installed package
version. Recognition-family policy does not leak into this smaller measurement contract, and an
additive inspection symbol does not pretend to be a new recogniser family.

## What failures mean

Validation fails closed and names the family and boundary whenever possible:

- **family inventory mismatch (`stale=`)** — Draftwright declares a family the installed package
  no longer ships, so a declared adapter would call a recogniser that is gone. Remove or repoint
  the declaration.

  The opposite direction does **not** fail this validation. A family the package ships and
  Draftwright has not declared cannot reach any Draftwright code path, and blocking on it made the
  package unreleasable until its consumer caught up. It is reported by
  `pending_family_declarations` and fails `tests/test_recogniser_adoption.py` in Draftwright's own
  CI instead — adoption is still required, and is enforced where the decision is made. Provide the
  downstream behavior or an explicit non-supported state as before.
- **record schema mismatch** — a consumed record schema changed. Review the record fields and
  compatibility notes, update the relevant adapter and tests, then explicitly list the accepted
  schema version. A bounded dual-readable list may name the current and next additive schemas
  during a provider-first release; it is never an open-ended range.
- **stale implementation** — a declared adapter, `Sheet` method, emitter, renderer, or completeness
  function no longer resolves. Repair the implementation reference and its independent behavior
  evidence; do not merely rename the string.
- **evidence is missing** — a supported claim points at a deleted test or document. Restore or
  replace the behavior evidence before changing the path.
- **unknown state or unsupported format** — the installed contract uses semantics this Draftwright
  version does not understand. Upgrade the validator before accepting the package version.
- **geometry-only family invents semantics** — geometry used for critique was incorrectly given an
  inferred IR/DSL/code-generation/drawing path. Keep it geometry-only unless a separately reviewed
  Draftwright feature introduces authored semantics and compatibility evidence.
- **reserved family claims supported downstream semantics** — a family-level `deferred` or
  `unsupported` disposition still advertises an implemented downstream stage. Either make the
  family disposition supported with behavior evidence, or defer/disable every semantic stage and
  retain its tracking issue.
- **state transition lacks evidence** — a capability was strengthened or weakened without a named
  Draftwright version, release notes, and compatibility tests. Supply all three as one reviewed
  transition.

## Adding or changing a recogniser

For an additive field that increments an existing record schema, update the relevant Draftwright
adapter and declaration when advancing the exact `b123d-recognisers` dependency pin. Tests must
prove the installed schema is accepted while later schemas still fail. The package version belongs
only in `pyproject.toml` and `uv.lock`; the capability declaration derives runtime identities from
installed package metadata.

For each applicable stage, add the adapter, declaration, emitted-Sheet round trip, drawing behavior,
and completeness evidence; for an inapplicable or deferred stage, state why and link its tracking
issue. The contract test derives package record outputs, converter registries, live `Sheet` methods,
and emitter branches independently, so copying a new name into the declaration alone cannot make CI
pass.

`bosses` is the fully consumed reference family. `repeating-radial-profiles` is the opposite
reference: it remains geometry-only critique evidence for a separately authored gear declaration,
with no inferred gear feature added to fill the table.

## Hole completeness evidence

The `holes` completeness boundary is `supported` from the versioned format-1 corpus in
`tests/fixtures/evaluation/corpus-v1.json`. Its expected holes, identity fields, nominal values,
tolerances and fixture hashes are independently authored. Neither `RecognitionResult`,
`feature_census`, the compiled plan nor the hole-requirement ledger supplies the denominator.
The corpus includes positive, negative, ambiguous, compound and topology-order-variant cases and
reports detection recall/false positives, parameter fidelity and downstream usefulness as separate
layers; there is deliberately no composite score.

For each observed provider hole, the evaluation follows four real artifacts: the automatic
build's `PartModel`, an explicit public `Sheet.hole` declaration, an executed
`emit_sheet_script` result and the placed drawing's measurement provenance. The existing
fail-closed hole-requirement correspondence supplies the join from a provider record to each IR
artifact; it never creates expected facts. A missing or ambiguous correspondence scores
`unknown`, a placement/ink loss scores `unsupported`, and only `supported` earns downstream
credit. Mutations that delete provider holes, corrupt a declaration, remove generated feature
lines or erase drawing measurement provenance reduce the corresponding layer.

## Hole-pattern completeness evidence

The `hole-patterns` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-hole-patterns-v1.json`. The corpus covers rectangular grids,
bolt circles, linear arrays, a near-pattern negative, compound arrangements and a
reverse-serialized topology pair. It scores one arrangement occurrence per provider group rather
than treating its member holes as new physical occurrences. Member sizes, depths, bottoms and
individual locations remain exclusively in the hole corpus.

Each provider pattern must contain the exact accepted `RecognitionResult.holes` objects and no
member may belong to two aggregate patterns. The observer follows the existing fail-closed hole
ledger to one exact `PatternFeature` through the automatic IR, public `Sheet.pattern` declaration,
executed generated Sheet code and placed grouping/pitch/BCD evidence. Mutations that delete the
derived provider inventory, corrupt the declaration, remove generated pattern lines or remove a
placed pitch dimension reduce the corresponding independent layer.

## Flat completeness evidence

The `flats` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-flats-v1.json`. Its seven construction-authored cases contribute
nine physical across-flats requirements, 27 parameter checks and 36 downstream checks. The scope
covers a non-round negative, a lone D-flat, axis-aligned and slanted Double-D stock, independent
parallel lobes, independent coaxial stock and a reverse-serialized topology pair.

The denominator represents physical stock requirements, not provider face records: the two
opposed faces of one Double-D stock line form one across-flats fact, while parallel lobes and
disjoint coaxial spans remain distinct facts. Identity uses the canonical axis, axis line and stock
span; across-flats size, face count and anchors are scored parameters. Each fact must survive the
automatic IR, public `Sheet.flat` declaration, executed generated Sheet code and placed
measurement-provenance boundary. Removing provider flats, weakening a nominal, corrupting a
declaration or generated script, or losing the placed callout/provenance reduces the corresponding
independent score.

## Pocket completeness evidence

The lone `pockets` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-pockets-v1.json`. Ten construction-authored cases contribute 13
physical pockets, 52 parameter checks and 52 downstream checks. The corpus covers a through-slot
negative, one off-centre pocket, an edge-anchored corner interruption, equal independent pockets,
opposite openings, a principal side opening, a PrismaticPocket ownership negative, separate
compound bodies and a reverse-serialized topology pair.

Identity uses width/long/depth axes, opening sign and physical location; width, length, depth and
edge anchoring are scored parameters. Interior pockets require three size outcomes plus two
directional datum locations. Edge-anchored pockets intentionally require only their three sizes
because the adjacent stock edges establish position. Pattern members are excluded rather than
double-counted. Each fact must survive automatic IR, public `Sheet.pocket`, executed generated
Sheet code and placed size/location provenance. The IR now retains provider `open_sign`, and the
shared location renderer records which physical X/Y ordinate landed while preserving ADR 0016's
single public `location` authoring unit.

## Pocket-pattern completeness evidence

The `pocket-patterns` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-pocket-patterns-v1.json`. Seven construction-authored cases
cover 30° linear and rectangular-grid positives, the two-member threshold, unequal-spacing
ambiguity, a plain negative, an axis-aligned underside compound case and a reverse-serialized
topology pair. The four expected arrangements contribute 41 parameter checks and 16 downstream
checks; hard-coding the linear direction or grid angle lowers parameter fidelity.

Each pattern is one physical grouping requirement: its member pockets are excluded from the lone
pocket denominator. Identity retains arrangement kind, axes, opening side and exact member sites;
count, member width/length/depth, edge anchoring, centre and pitch/lattice values are scored
parameters. The observer follows one exact `PocketPatternFeature` through automatic IR, public
`Sheet.pocket_pattern`, executed generated code and placed count/size/pitch/location evidence.
Compiler identities and structured X/Y location facts establish ownership, while the final
observer also checks exact compiler-approved ink, including an approved pitch tolerance only when
every physical gap supports the collapsed claim. Diagonal pocket-pattern pitch dimensions use
exact segment-versus-label clearance rather than a false-blocking whole-ink hull. Removing
provider patterns, weakening a nominal or orientation, corrupting a declaration or generated
script, or deleting/corrupting placed evidence reduces the corresponding independent layer.

## Groove completeness evidence

The `grooves` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-grooves-v1.json`. Eight construction-authored cases contribute
ten physical annular recesses, twenty parameter checks and forty downstream checks. The corpus
covers a monotonic turned-step negative, all three principal axes, a narrow circlip ownership
case, equal grooves on separate bodies and a geometrically identical pair created with reversed
Boolean order.

Axis and the physical axis-line/station anchor identify each occurrence; axial width and floor
diameter are scored parameters. Each source must join exactly one `GrooveFeature` through
automatic IR, public `Sheet.groove`, executed generated Sheet code and one placed semantic
`{width} WIDE × ø{diameter}` callout carrying both compiler identities. Raw boss and turned-step
inventories may see a reduced band, but Draftwright suppresses that floor from their IR paths so
its diameter is drafted once. Removing provider grooves, changing width/diameter/axis/location,
corrupting declaration or generated code, or severing either measurement claim reduces the
corresponding independent layer.

Remaining supported-family completeness work is tracked by family group rather than the closed
shared design issue: #1370 retains countersinks and Double-D bores; #1371 covers
channels, slots, slot patterns and polygonal stock; #1372 retains rectangular pads and
polygonal bosses; #1373 covers plates, face levels and risers; and
#1374 covers turned steps, chamfers and fillets.

## Angled-step boundary

The installed aggregate reconciles a slanted face claimed by both `Chamfer` and `AngledStep` in
favour of `RecognitionResult.angled_steps`. Draftwright consumes that aggregate result and does not
repeat the provider's topological ownership decision. The corpus fixture
`tests/fixtures/issue_1247_angled_blind_step.step` pins a genuine partial-width ramp with a
triangular blind end beside an ordinary full-length chamfer: direct recognition sees two chamfers
and one angled step, while the aggregate keeps one of each.

An `AngledStep` record supplies its angle, two legs, run length, axis and slanted-face centre. Those
measurements are geometric evidence, not a reviewed manufacturing-annotation choice: selecting an
angle plus run, the two legs, a slanted-face dimension, or a section/detail view would invent a
Draftwright requirement grammar that the IR, Sheet surface and compiler do not currently carry.
Draftwright therefore keeps the family disposition `unsupported`, creates no inferred IR feature,
Sheet declaration, generated code or annotation, and emits
`angled_step_requirement_unsupported` at warning severity for every aggregate occurrence. Each
occurrence contributes one `unsupported` completeness requirement. Issue #1247 records this
consumer decision.

## Prismatic-pocket boundary

The installed aggregate reconciles the two pocket inventories before Draftwright sees them. A
candidate reported by both direct recognisers yields to the supported `Pocket` record;
`RecognitionResult.prismatic_pockets` therefore contains occurrences not owned by `Pocket`.
Draftwright consumes that aggregate policy and does not repeat provider reconciliation. This is an
ownership statement, not a shape classification: for example, a rotated four-sided recess can
remain a `PrismaticPocket` when the axis-paired `Pocket` recogniser does not accept it.

The remaining `PrismaticPocket.section` may be any planar polygon. The rectangular pocket grammar
`W × L × D DEEP` is false for a triangle, while an across-flats callout applies only to selected
regular polygons and cannot represent the general record. Draftwright therefore retains the family
disposition as `unsupported`: it creates no inferred IR feature, Sheet declaration, generated code,
or drawing annotation. Each aggregate occurrence instead emits
`prismatic_pocket_requirement_unsupported` at warning severity and contributes one `unsupported`
completeness requirement. Exact mouth-to-section correlation replaces the generic unsupported-
profile warning only for that occurrence, so an unrelated unrecognised profile remains visible.

## Passage compatibility boundary

The installed `b123d-recognisers==0.4.6` release contains the `passages` family introduced
in 0.2.6. Version 0.4.0 makes `SectionPassage` the authoritative physical output and retains
schema-v1 `Passage` as its compatibility projection. Draftwright declares all six public and nested
record schemas exhaustively but deliberately keeps the family `unsupported`, with the drafting
decision recorded by issue #1245. This is a truthful consumer disposition: both aggregate
inventories remain visible, but only authoritative `section_passages` contributes an explicitly
`unsupported` completeness requirement. Draftwright does not invent an IR feature, DSL
declaration, generated code, or drawing annotation for either inventory.

The 0.4 contract is explicit:

- `SectionPassage` will be the authoritative physical output and aggregate census source;
- legacy `Passage` values will be an accepted-only compatibility projection;
- `recognise_passages(..., ledger=...)` will be a fail-loud unavailable compatibility operation;
- the writer-free `recognise_passages` name will remain public but non-authoritative; and
- rich split-junction passages can supersede a Slot claim, moving physical ownership to the
  unsupported Passage family through `SLOT_SUPERSEDED_BY_PASSAGE`.

The exact 0.4.6 pin, manifest-v2 validator and explicit unsupported inventories make that limitation
fail-visible rather than silently treating rich passages as supported. Draftwright deliberately
does not claim that a regular-polygon `HEX … A/F THRU` callout covers the complete line/arc section
schema. Each authoritative `RecognitionResult.section_passages` occurrence therefore emits
`passage_requirement_unsupported` at warning severity and contributes an `unsupported` requirement
to the completeness component. The accepted-only legacy `.passages` projection contributes neither
a second issue nor a second requirement. Issue #1245 records this consumer decision.

## Step families introduced in recognisers 0.4.6

The 0.4.6 provider manifest adds `circular-blind-steps`, `paired-ramp-steps`, and
`through-steps`. Their typed records expose physical axes, locations, run lengths, and the relevant
section or angle; no provider API change is currently required. Those facts do not by themselves
choose Draftwright's manufacturing requirement, semantic view, or dimension grammar.

Draftwright therefore declares all three families unsupported at the IR, Sheet, generated-code,
and drawing-consumer boundaries, creates no inferred feature or annotation, and keeps their
completeness state explicitly `deferred`. Their aggregate inventories remain classified as
undecided physical families rather than being hidden among non-requirement substrate. Issue #1382
owns the reviewed downstream disposition for all three families.
