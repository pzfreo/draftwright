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

## Countersink completeness evidence

The `countersinks` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-countersinks-v1.json`. Its seven construction-authored cases own
seven physical conical seats, 28 provider-parameter checks, and 28 downstream checks. The
negative controls distinguish solid external cone transitions and shallow deburr chamfers from an
internal functional seat; the positive cases include a mixed-size ownership pair, equal seats
sharing one grouped callout, and a bijectively renumbered, reverse-serialized topology pair.

Identity is the signed mouth axis plus physical opening centre. Opening diameter, drill diameter,
included angle, and geometric depth are detection parameters, not four new drawing requirements.
The provider aggregate must reuse each accepted `CounterSink` object exactly once on its owning
`HoleRecord.csink`; Draftwright validates that chosen owner with the provider's public semantic
predicate and fails closed on mismatches, multiplicity, or canonical-site collisions. It then
follows that identity through the existing hole IR, public
`Sheet.hole(..., csink=...)`, generated code, and the two exact compiler measurements
`countersink.diameter` / `countersink.angle`. Confirmed solver-placed, role-specific `⌵ ⌀… × …°`
ink is required for drawing credit, so diameter and angle cannot confirm each other merely because
both numbers occur in the label. Bore diameter, through/depth, grouping, and location remain owned
once by the hole ledger.

The current singular hole waist cannot attribute both seats of a two-sided countersunk bore. That
limitation remains fail-visible: the attached seat is followed normally, while the other physical
seat contributes explicit `unverifiable` diameter and angle outcomes with no invented canonical
member key. Completeness support means every aggregate seat receives an honest outcome; it does
not claim every drawing scores 100%. Removing provider seats, deleting the IR or generated csink,
corrupting the Sheet declaration, or removing/corrupting finished ink reduces the corresponding
independent evidence.

## Double-D completeness evidence

The `double-d-bores` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-double-d-bores-v1.json`. Eleven construction-authored cases
contribute ten physical through-profile occurrences, 50 provider-parameter checks and 40
downstream checks. Principal X/Y/Z axes, a 30° flat rotation, disconnected coaxial bodies,
heterogeneous compounds and a reverse-serialized topology pair are positive controls; round,
blind, opposed-blind and solid-stock cases keep the family boundary fail-closed.

Axis plus full location identifies each physical frame, so disconnected coaxial bodies cannot
collapse by dropping the through-axis coordinate. Major diameter, A/F, depth, through state and
the unoriented flat line are scored parameters. The complete provider inventory must correspond
one-to-one, with exact multiplicity, to the automatic IR, public `Sheet.double_d_bore`
declaration and executed generated Sheet code. Drawing credit requires compiler-confirmed
`bore.diameter` and `profile_across_flats.length` identities together on exact role-specific
`⌀major THRU DOUBLE-D across A/F` ink. The provider's fixed ownership excludes the same void
from ordinary `holes`, so this profile fact does not recount a circular bore.

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

## Rectangular-pad completeness evidence

The `rectangular-pads` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-rectangular-pads-v1.json`. Twelve construction-authored cases
contribute twelve physical pads, 36 parameter checks and 48 downstream checks. The corpus covers
all six signed principal orientations, equal pads on separate bodies, reversed Boolean order,
and full-span ledge, nested-staircase and detached-body negatives.

Axis, signed material direction and attachment-plane centre identify each occurrence; footprint
width/length and terminal-to-attachment height are scored parameters. Each source must join one
exact `PadFeature` through automatic IR, public `Sheet.pad`, executed generated Sheet code, and
five compiler-owned drawing requirements: both footprint sizes, height and two directional
locations. The final observer follows compiler identities and structured directional facts to
the exact placed ink without using annotation names, views or page coordinates. Removing provider
pads, changing an identity or measurement, corrupting declaration or generated code, or severing
a measurement claim or directional fact reduces the corresponding independent layer.

## Polygonal-boss completeness evidence

The `polygonal-bosses` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-polygonal-bosses-v1.json`. Eleven construction-authored
cases contribute ten physical attached hexagonal prisms, 40 parameter checks and 40 downstream checks.
The corpus covers all three principal axes, in-plane rotation, equal occurrences on separate
bodies, reversed Boolean order, and recess, detached-prism, whole-stock, circular-boss and
rectangular-pad negatives.

Principal axis and physical prism centre identify each occurrence. The released provider family
guarantees a six-side schema invariant; that invariant, A/F, height and
the canonical set of ordered flat-direction/physical-centre pairs are scored parameters. Each
source must join one exact `PolygonalBossFeature` through automatic IR, public
`Sheet.polygonal_boss`, executed generated Sheet code, and two compiler-owned drawing
requirements: the solver-placed A/F leader retains its semantic identity and lands on one
physical flat support, while attached height survives as its own dimension identity. Removing
provider records, changing identity or prism evidence, corrupting declaration or generated code,
moving the leader, changing either statement, or severing either measurement claim reduces the
corresponding independent layer. The older generic boss-height fallback no longer owns polygonal
prisms; the family ledger distinguishes placed, structured-note, suppressed, dropped, missing
and unverifiable outcomes without duplicate diagnostics.

## Polygonal-stock completeness evidence

The `polygonal-stock` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-polygonal-stock-v1.json`. Thirteen construction-authored cases
contribute six physical whole-stock hexagonal prisms, 24 parameter checks and 24 downstream
checks. The corpus covers all three principal axes, in-plane rotation, non-origin bounds and
two equivalent topology orders. Circular and rectangular stock, an irregular six-sided prism,
a regular octagon, an attached hexagonal boss, recessed extra topology and a compound remain
outside this exact single-solid family.

Principal axis and physical prism centre identify the occurrence. The released provider family
owns exactly six-sided stock; side count, A/F, axial length and the canonical ring of coupled
flat-direction/physical-centre pairs are scored parameters. Each source must join one exact
`PolygonalStockFeature` through automatic IR, public `Sheet.polygonal_stock`, executed generated
Sheet code, and two compiler-owned drawing requirements. The A/F statement must be a confirmed
solver-placed leader with its tip on a retained physical flat, while stock length must retain its
own confirmed Dimension identity. Arbitrary rigid motion is exercised through the public framed
recognition route without changing the default raw rollout policy. The public declaration remains
capable of expressing other even-sided regular stock; only the provider-owned automatic family is
narrowed to its released hexagonal schema.

## Plate completeness evidence

The `plates` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-plates-v1.json`. Eleven construction-authored cases contribute
twenty body-local slab occurrences, twenty thickness checks and eighty downstream checks. The
corpus covers all three principal orientations, additive and cut U-channels, equal occurrences on
separate bodies, reverse-ordered topology, cross-family ambiguity, and single-slab, detached,
thick-block and rotational negatives.

A Plate occurrence is a thin material slab in a multi-plate prismatic body; a lone flat plate is
owned by the whole-part envelope and is intentionally outside this denominator. Exact axis,
axial interval and both in-plane witness coordinates join each provider record to one
`PlateFeature`; the independently authored identity pins axis, physical axial station and both
transverse witness coordinates for every occurrence. Thickness is the
single scored parameter. Each occurrence must survive automatic IR, public `Sheet.plate`, executed
generated Sheet code and the compiler's `thickness.length` drawing identity. A planner-derived
opposite U-channel wall is explicitly `inapplicable` only because the envelope, first wall and
channel-width chain states it; other requirements need real solver-placed `Dimension` ink.
Material spans already owned by an exact envelope, step-level/shoulder chain, slot pattern or
attached polygonal boss likewise remain inapplicable rather than becoming duplicate Plate
requirements. Raw polygonal-boss ownership requires a valid support record, a single-solid part,
and a boss-plus-slab span equal to the complete envelope axis. Plural-solid inventories remain
unverifiable because Plate carries no body provenance; malformed or partial-span ownership fails
closed.
Derived drawing credit additionally requires every dependency's compiler-approved finished claim
or structured satisfaction; provenance attached to incorrect ink is not enough.
Annotation names, views and page coordinates never establish correspondence. Removing or
malforming provider records, changing the interval or witness, corrupting declaration/generated
code, changing the printed value, or severing measurement provenance reduces the corresponding
independent layer.

## Chamfer completeness evidence

The `chamfers` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-chamfers-v1.json`. Ten construction-authored cases contribute
twelve physical bevels, 36 parameter checks and 48 downstream checks. The corpus covers a plain
negative, equal planar bevels on all three principal axes, an asymmetric bevel, a turned conical
treatment, equal occurrences on separate bodies, reversed Boolean-operation order and the
AngledStep/Chamfer ownership contest.

Axis, physical bevel anchor and planar/turned form identify each occurrence; both legs and the
derived angle are scored parameters. Each source must join exactly one `ChamferFeature` through
automatic IR, public `Sheet.chamfer`, executed generated Sheet code and a placed semantic callout
carrying its compiler identity. The drawing observation checks `C` versus `leg × angle` syntax and
the live leader's physical bevel or turned-profile station independently of annotation names.
Equal specifications may share one `n×` callout only when every member measurement remains on that
ink. Removing provider records, weakening a leg/angle, corrupting a declaration or generated line,
moving the leader, changing its text or severing provenance reduces the corresponding layer.

## Fillet completeness evidence

The `fillets` completeness boundary is independently `supported` from
`tests/fixtures/evaluation/corpus-fillets-v1.json`. Ten construction-authored cases contribute
fourteen physical rounds, fourteen parameter checks and 56 downstream checks. The corpus covers a
plain negative, equal planar rounds on all three principal axes, four repeated edges on one body, a
turned toroidal treatment, equal occurrences on separate bodies, reversed Boolean-operation order
and the CircularBlindStep/Fillet ownership contest.

Axis, physical surface anchor and planar/turned form identify each occurrence; radius is the scored
parameter. Each source must join exactly one `FilletFeature` through automatic IR, public
`Sheet.fillet`, executed generated Sheet code and a placed semantic callout carrying its compiler
identity. The drawing observation checks `R`/`n× R` syntax and the live leader's physical round or
turned-profile station independently of annotation names. Equal radii may share one callout only
when every member measurement remains on that ink. Removing provider records, changing radius,
corrupting a declaration or generated line, moving the leader, changing its text or severing
provenance reduces the corresponding layer.

## Circular-blind-step completeness evidence

The `circular-blind-steps` family is supported from the public `CircularBlindStep` record.
Exact principal run axis, quarter-cylinder radius, terminal-to-open centreline and canonical
transverse arc endpoint/centre/endpoint section identify one occurrence. Draftwright retains the
complete oriented geometry in one `CircularBlindStepFeature`, exposes the explicit-only
`Sheet.circular_blind_step(...)` declaration, and preserves the same correspondence through
generated Sheet code without rescanning topology.

Radius and stopped depth become independently addressable `circular_step_radius.radius` and
`circular_step_depth.length` requirements. One solver-placed leader points to the physical curved
wall in the axis end view and communicates `R… × … DEEP`; an authored set may retain either term
without resurrecting the other, and each numeric value keeps its own tolerance. The fail-closed
ledger follows each requirement to placed, structured-note, suppressed, dropped, missing or
unverifiable outcomes. Duplicate or malformed source/IR correspondence is never paired by order.
The aggregate ownership contest is preserved: the curved wall belongs to CircularBlindStep rather
than also yielding a Fillet requirement. Partial model construction preserves that atomic decision:
a caller may supply either or both inventories when they agree with the aggregate, but every
divergent override fails closed instead of producing duplicate or missing radius semantics. The two
public record families are independently quantised and carry no shared provider owner identity, so
Draftwright does not guess a cross-family correspondence from nearby scalar anchors. On a part whose
aggregate owns neither family, explicit one-family injection remains available, but simultaneous
non-empty Fillet and CircularBlindStep inventories are refused for the same reason.

## Paired-ramp-step completeness evidence

The `paired-ramp-steps` family is supported as a complete consumer path from the public
`PairedRampStep` record. Exact principal axis, shared-ridge midpoint, equal acute angle and
open-to-terminal run identify one occurrence. Draftwright lowers it to one
`PairedRampStepFeature` with independently addressable `ramp_angle.angle` and
`ramp_run.length` requirements, exposes the explicit-only `Sheet.paired_ramp_step(...)`
declaration, and preserves the same feature through generated Sheet code.

One solver-placed compound leader in the axis end view communicates `2× angle × run RUN` and
carries both compiler measurement identities. An authored set may retain either requirement
without resurrecting the omitted one, and tolerances remain attached to their own numeric value.
The completeness ledger follows each requirement independently to placed, structured-note,
suppressed, dropped, missing or unverifiable outcomes; duplicate source/IR correspondence fails
closed rather than choosing by order.

## Through-step completeness evidence

The `through-steps` family is supported from the public `ThroughStep` record. Exact principal
axis, removed-prism midpoint, through length and the complete three-point open section identify
one occurrence. Automatic lowering supports every principal run axis. An X/Y-run occurrence is
left with the established face-level plus shoulder/plate grammar only when that grammar proves
both exact physical leg intervals (including an envelope-defined complementary interval); a
partially covered occurrence instead lowers as one aggregate and replaces its exact matching
legacy fragments. Explicit `Sheet.through_step(...)` declarations choose the same local two-leg
grammar on any principal axis. Both paths preserve their correspondence facts in generated Sheet
code without a second geometry scan.

The two orthogonal section legs become independently addressable
`through_step_leg.length.<axis>` requirements. The through length remains structural because the
part envelope already states that extent; printing it again would double-dimension the part.
Both legs are solver-placed in the axis end view, outside the missing corner, and carry their own
compiler identities and tolerances. The fail-closed ledger follows each selected leg through
placed, structured-note, suppressed, dropped, missing or unverifiable outcomes; duplicate or
mutated source/IR correspondence is never paired by order. A coordinate-proven complete legacy
projection is `inapplicable` only while its exact alternate measurement identities are actually
placed or structurally satisfied. Authored omission and placement failure remain `suppressed` or
`dropped`; missing alternate ink remains `missing`. Axis or family presence alone can never claim
an outcome.

## Turned-step completeness evidence

The `turned-steps` completeness boundary is independently `supported` by
`tests/fixtures/evaluation/corpus-turned-steps-v1.json`. Eleven hash-pinned STEP cases contribute
26 physical outside-diameter bands, 52 parameter checks and 104 downstream checks. The corpus
covers all three principal axes, a translated blind-bored shaft, repeated equal lengths, separate
body-local axis lines, a coaxial through bore, groove overlap, a plain negative and a distinct-hash
topology-order pair. Axis line plus axial station identifies a band; its axial length and diameter
are scored parameters rather than identity, so weakening either lowers fidelity instead of hiding
as a missed occurrence.

Every retained band must cross the automatic IR, the existing public `Sheet.step(...)` word,
executed generated Sheet code and compiler-confirmed drawing evidence. Drawing credit requires an
exact physical shoulder-to-shoulder length witness and exact OD surface evidence, together with the
complete compiler-approved printed value. Equal adjacent lengths may share `n× value` ink only when
that annotation carries every member identity, prints the exact multiplier and value, and spans the
complete claimed run. Generated declarations retain coupled length/midpoint precision for
odd-thousandth spans rather than rounding the two facts independently. Automatic IR must also
retain the source band's complete validated `TurnedProfileKey` ownership at the key's full
published precision. One unique largest band
in one body-local profile may use the rotational profile's equivalent `od.diameter`
representation, but only when axis line and exact diameter identify that single physical band; one
part-global OD never multiplies across equal or disjoint bands. Each uniquely
correlated groove consumes its own narrow floor band once; ambiguous profile ownership fails
closed instead of guessing, and malformed groove evidence makes the affected groove and raw
turned-band rosters unverifiable. Groove coverage and floor-band ownership share one strict schema:
principal axis, immutable three-finite-real location, and positive finite-real width and diameter;
coercible strings, booleans, mutable points, infinities and NaNs cannot suppress a band. The
axis-line join admits only the half-quantum boundary between a 0.001 mm `Groove.at` and an
eight-decimal `TurnedProfileKey.axis_origin`. The root `turned_steps` and `grooves` inventories must
remain immutable tuples; a mutable or one-shot substitute is snapshotted once to preserve its
observable cardinality but receives only unverifiable outcomes. If even cardinality is unavailable,
one aggregate unverifiable contract outcome remains visible rather than being mistaken for a valid
empty family. The shared ownership predicate is used by IR lowering, physical lint and the
completeness ledger so their denominators cannot drift.

## Framed step-family evidence

The released 0.4.10 framed route is exercised for `circular-blind-steps`, `paired-ramp-steps` and
`through-steps` as one consumer boundary. Each construction-authored part is built through the raw
route, the provider-owned local frame, and the same local frame after a combined non-principal
rotation and translation of the source solid. The two framed builds must return identical family
records and IR, an empty semantic build diff, identical compiler-backed ink, and clean physical
lint. Raw and framed principal-axis names may differ because the latter are local coordinates;
the radius/depth, angle/run, and unequal two-leg manufacturing requirements must not.

All three local records still pass through the already-public declaration vocabulary —
`Sheet.circular_blind_step(...)`, `Sheet.paired_ramp_step(...)` and `Sheet.through_step(...)` — and
executed generated Sheet code must reconstruct the exact framed feature. No framed-only feature or
placement API is introduced. `CircularBlindStep` endpoints, radius and depth are independently
quantised by the provider to six significant figures, so derived spans can differ by a few final
places—especially when absolute coordinates are subtracted. IR and completeness validation sum
the relevant published values' decimal half-cells, admitting exactly that bounded uncertainty
while rejecting outside-cell spans and sections. This is consumer normalization of an existing
released schema, not a changed recogniser contract.

Remaining supported-family completeness work is tracked by family group rather than the closed
shared design issue: #1371 covers channels, slots and slot patterns, while #1373 retains face
levels and risers. #1374's chamfer, fillet and turned-step slices are supported by independent
physical corpora.

## Historical: recognisers 0.4.8 raw boundary and RaisedPad v2

Draftwright pinned the published `b123d-recognisers==0.4.8` wheel in the preceding release.
At that time the default route deliberately called `build_raw_recognition_result`; records
remained in the caller/world coordinate system. #1357 now provides a reviewed explicit
framed-result activation added with 0.4.9. The 0.4.8 tests used the public framed API as release
evidence for upstream #331, #332, and #334, comparing each aggregate inventory with the
corresponding public family call on the exact returned local solid. Production added no fallback,
second aggregate, or family rescan. Face levels,
risers, and turned profiles remained outside framed production pending their subsequently released
upstream fixes.

`RaisedPad` schema v2 makes a pad normal and its material-outward sign explicit. Draftwright maps
the six signed principal orientations into one `PadFeature`: world bounds and direction survive
automatic recognition, `Sheet.pad(axis=..., direction=...)`, and executed generated code. The
compiler assigns one end-on semantic view and stable identities for footprint width, footprint
length, terminal-to-attachment height, and both in-plane locations. Footprint/location dimensions
and the `… HIGH` leader are placed by the ordinary shared solve; no annotation accepts a raw page
position. Removing any one of those five physical facts produces
`pad_footprint_not_defined`. This is complete consumer semantics, but #1372 still owns the
independently authored rectangular-pad detection/parameter/downstream benchmark corpus required
before claiming family-level completeness.

## Recognisers 0.4.10 adoption and blind-slot boundary

Draftwright exactly pins `b123d-recognisers==0.4.10`. The 28 family record schemas already
consumed from 0.4.9 are unchanged. The inspection namespace remains format 1 / API major 1;
Draftwright advances its exact package join without widening the set of inspection symbols it
consumes. The additive `b123d_recognisers.evidence` API is public provider capability, but this
adoption does not consume it before a concrete correspondence slice demonstrates that need.

Unchanged record schemas do not mean byte-identical recognition output. The 0.4.10 provider closes
slot-depth, subdivided paired-ramp/AngledStep, and noisy stubby-pocket gaps and fixes Double-D/Hole
ownership, external-cone countersink false positives, Plate tie covariance, and turned-step
translation covariance. Draftwright accepts those public aggregate outcomes: its consumer tests pin
that an external cone no longer creates a countersink requirement and that an edge-open rectangular
recess now yields to the dedicated blind-slot owner instead of retaining a false `Pocket` callout.
The provider's immutable
[0.4.10 release](https://github.com/pzfreo/b123d-recognisers/releases/tag/v0.4.10) owns the lower-level
recognition predicates and counterexamples; Draftwright does not duplicate private provider
algorithms to restate them.

The release adds `rectangular-blind-slots` and `round-bottom-blind-slots` to the one aggregate.
Both now have dedicated Draftwright feature types and public Sheet words. The rectangular family
uses `RectangularBlindSlotFeature` and
`Sheet.rectangular_blind_slot(...)` declaration, generated-code round trip and solver-owned
`OPEN SLOT width × capped-run × depth DEEP` callout. Its axes and opening signs remain structural
correspondence facts while all printed sizes cross the approved compiler boundary. Native raw and
rigidly moved framed builds preserve the same three measurements and drawing semantics.

This is intentionally not an ordinary through `SlotFeature`, which cannot state a blind terminal
wall or section depth, and not a rectangular pocket, which cannot state the open end. Independent
physical completeness is now supported by a seven-case authored corpus covering all six run/open
orientations and a separately sized specimen. Each recognised occurrence contributes three
requirements—width, capped run and flat-bottom depth—and exact released structural facts join it to
one IR feature before measurement identities establish placed, structured-note, authored-
suppressed, dropped, missing or unverifiable outcomes. Labels, annotation names, views, projections,
leader tips and page coordinates cannot certify correspondence. The rectangular family therefore
participates in `audited_score`.

The round-bottom family lowers one-to-one to `RoundBottomBlindSlotFeature` and declares through
`Sheet.round_bottom_blind_slot(...)`. Its three addressable requirements are capped run length,
straight bottom-flat width and the equal round-side radius. Total opening width
(`flat_width + 2 × radius`) and profile depth (`radius`) are derived geometry, not extra independent
requirements. Generated code preserves every structural fact and role-specific decoration; one
solver-owned `ROUND-BOTTOM OPEN SLOT …` leader carries only compiler-approved values. Its separate
seven-case corpus covers all signed principal orientations and an independently sized specimen,
with exact full-record correspondence and parameter/outcome provenance under the same fail-closed
rules as the rectangular family. It now participates independently in `audited_score`; ordinary
slots, pockets, channels and rectangular blind slots do not share ownership.

## Recognisers 0.4.12 adoption and additive-family boundary

Draftwright exactly pins `b123d-recognisers==0.4.12`, including the immutable 0.4.11 `Blend`
addition and the 0.4.12 `OrientedSlot`, `OrientedSlotArray`, and `OrientedSlotGrid` records. The
provider capability manifest therefore contains 33 families. Every family and public record has an
explicit consumer declaration and registry home; the inspection API remains format 1 / major 1.

The adoption initially deferred all three additive families. #1433 has now reviewed and promoted
schema-v1 `blends`: provider reconciliation is the sole Fillet/Blend precedence authority, and each
remaining accepted convex chain owns one radius requirement. Dedicated `BlendFeature` IR retains
the complete canonical `axis_direction`; `Sheet.blend(...)`, generated replay and the shared
solver-owned `n× R` leader preserve it without pretending it is a legacy Fillet. Exact occurrence
and measurement provenance place Blend in `audited_score`.

`oriented_slots` and `oriented_slot_patterns` remain separately registered as undecided under
#1430. Their real occurrences are visible in `unscored_recognized_families`, but contribute no
invented requirement. The free-axis slot record retains vector width/long directions and its
authoritative `SectionPassage` source; array/grid records retain those member identities and their
vector lattice. Coercing either into principal-axis `SlotFeature` / `SlotPatternFeature` would
discard that contract, so no Sheet word or completeness claim exists for them on main.

The existing schema-v1 `PairedRampStep` record is unchanged, while 0.4.12 expands provider
recognition to shallow nonzero ramp pairs. A released shallow specimen now crosses the already
supported consumer path without changing meaning: exact record-to-IR lowering, the existing
`Sheet.paired_ramp_step(...)` declaration, executed generated code, two compiler-approved angle/run
requirements, one solver-owned compound leader, and the established completeness ledger. Existing
raw and provider-framed contract suites continue to guard the ADR 0020 boundary. Draftwright uses no
provider-private geometry or sibling checkout for any of these claims.

#1438 now consumes the release's public raw `b123d_recognisers.evidence` acquisition. One
`RecognitionEvidence` owns the accepted physical occurrence/face references and projects the same
`RecognitionResult` the existing compiler and lint consumers already read. Draftwright retains
both in its per-drawing `RecognitionCache`, including across scale/view retries; declared builds
remain recognition-free until physical critique, and a bare result is never rescanned merely to
backfill evidence. The framed route remains evidence-less pending upstream
[`b123d-recognisers#463`](https://github.com/pzfreo/b123d-recognisers/issues/463). This foundation
does not yet claim a report disposition or change any rendered artefact.

Round-boss ownership is now explicit at that same run-local boundary. Each accepted `BossRecord`
retains the exact consumer-selected final owner: its singleton `BossFeature`, the one existing
same-diameter representative, its body-local turned step, or the groove that absorbs that step or
the profile-gate fallback. The occurrence's own geometry remains available even when the drawing
uses a consolidated owner. Ambiguous selection fails closed as `unexpectedly_missing`; it cannot be
certified by equal values, ordering, rendered labels, or topology identity.

## Recognisers 0.4.9 prepared frame boundary

Draftwright's 0.4.9 boundary accepted `RiserEvidence` v2,
`TurnedStep`/`TurnedProfile` v2, and the nested `TurnedProfileKey` v1. These records preserve
same-solid levels and physical turned-profile membership in compounds. The fail-closed capability
join rejects an older, future, missing, or malformed schema instead of treating it as equivalent.

ADR 0020 adds `prepare_framed_detection`, the one Draftwright-owned intake for the public
`prepare_framed_part` seam. A successful unit retains the caller-space source as provenance while
pairing the exact local working solid, provider frame, prepared cylinders, Draftwright's local
rotational classification, and aggregate. Classification reuses the prepared cylinder objects;
it does not scan the local topology again. Provider refusal returns its exact typed reason and no
recognition result. There is no hidden raw fallback.

FULL frames establish a directed ordered basis. ORTHOGONAL representatives do not establish sign,
axis interchange, or material identity. AXIAL representatives additionally do not establish roll,
so only roll-invariant facts may be consumed until a particular asymmetric requirement is audited.
`Analysis.profiles` carries every body-local turned profile through the shared compiler waist on
both the raw and explicit framed routes. Its compatible `Analysis.prof` projection is populated
only for zero/one-profile consumers; `single_turned_profile()` likewise refuses plural cardinality
without implying that plural compilation is unsupported. Neither route selects, merges, or discards
a physical profile merely to satisfy that singular accessor.

`analysis._analyse` activates this boundary only for explicit `framed_recognition=True`. The exact
working solid then feeds detected compilation, projection and physical lint while caller geometry
remains provenance; AP242 correlation records cross one point/vector/box transform. Typed provider
refusal selects one visible raw fallback at the top-level product-policy boundary. Raw remains the
default while #1357 accumulates supported-platform CI and representative real-part canaries.

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

The installed `b123d-recognisers==0.4.8` release contains the `passages` family introduced
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

The exact 0.4.8 pin, manifest-v2 validator and explicit unsupported inventories make that limitation
fail-visible rather than silently treating rich passages as supported. Draftwright deliberately
does not claim that a regular-polygon `HEX … A/F THRU` callout covers the complete line/arc section
schema. Each authoritative `RecognitionResult.section_passages` occurrence therefore emits
`passage_requirement_unsupported` at warning severity and contributes an `unsupported` requirement
to the completeness component. The accepted-only legacy `.passages` projection contributes neither
a second issue nor a second requirement. Issue #1245 records this consumer decision.

## Step families introduced in recognisers 0.4.6

The 0.4.6 provider manifest adds `circular-blind-steps`, `paired-ramp-steps`, and
`through-steps`. Their typed records expose physical axes, locations, run lengths, and the relevant
section or angle. Those facts do not by themselves choose Draftwright's manufacturing requirement,
semantic view, or dimension grammar, so each family receives a separate reviewed disposition.

All three step families now have complete supported paths. Through-step automatic ownership
supports every principal run axis while retaining an
X/Y occurrence's existing Z-up grammar only when both physical legs and their actual outcomes
are proved; the explicit Sheet surface likewise supports all principal axes. Circular blind
steps retain their oriented centreline/quarter-arc correspondence and communicate independently
addressable radius and stopped-depth requirements in one end-view leader. Issue #1382 records
the reviewed downstream dispositions.
