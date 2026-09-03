# ADR 0020 — Provider-owned frame boundary for detected compilation

- **Status:** Accepted; prepared boundary and explicit opt-in activation implemented. Amendment 1
  adopts the released 0.4.14 framed-evidence lifecycle. Raw remains the rollout default pending
  platform and corpus canaries.
- **Date:** 2026-08-31
- **Deciders:** Paul Fremantle (pzfreo)

## Context

Recognition records express points, bounds, directions, spans, and principal axes. The historical
automatic path asks `b123d-recognisers` to interpret the caller/world placement directly. A rigid
rotation can therefore change which feature families are recognised and which semantic views
Draftwright plans, even though the physical part did not change.

`b123d-recognisers` 0.4.9 introduced the public preparation seam needed to remove that dependence;
0.4.14 retains it and adds exact framed accepted-occurrence evidence.
`prepare_framed_part` either returns a typed `RefusedPartFrame` or one
`PreparedFramedPart(frame, part, cylinders)`: the provider-inferred `PartFrame`, the exact
topology-preserving local working solid, and its reusable local cylinder inventory. Draftwright
must classify that local solid before the provider runs its classification-gated aggregate, then
call `PreparedFramedPart.recognise(rotational=...)`. Calling the convenience
`build_framed_recognition_result` cannot satisfy that ordering because it needs `rotational`
before it exposes the local solid.

Copying local record coordinates back into arbitrary caller space is not viable. Draftwright's IR
uses principal-axis letters, not arbitrary vectors. Reconstructing the provider's normalized solid
would create a second topology and frame authority. Compiling local records against the caller
solid would mix coordinate systems.

## Decision

### 1. One owned prepared-frame boundary

`recognition_frame.prepare_framed_detection` is Draftwright's sole prepared-frame intake. It:

1. calls the provider's public `prepare_framed_part` once;
2. derives Draftwright's rotational classification from the returned local solid and exact
   prepared cylinders;
3. calls `PreparedFramedPart.recognise_evidence` once with that classification, using the
   established result-only call only after a typed pre-recognition evidence refusal; and
4. returns an immutable `FramedDetection` that keeps the caller-space source, prepared unit,
   classification, and framed result/evidence authority together.

The boundary verifies by identity that preparation and recognition retain the same frame, working
solid, and cylinder records. It does not import provider-private normalization or graph machinery.

### 2. Detected compilation stays local end to end

For an explicitly opted-in activation, the detected record→IR adapter, bbox lowering, compiler,
requirement-driven view planner, projection, annotation placement, physical lint, and rendering
will all consume the exact local working solid and local evidence. The record→IR coordinate
operation is deliberately identity: records remain in the coordinate system in which they were
proved. No downstream stage may combine them with caller-space geometry or independently
reconstruct a transform.

The caller solid remains source provenance. Public `Sheet` declarations and a caller-supplied
`PartModel` remain in the caller's coordinates and retain ADR 0011's no-recognition build path.
Framed detection must not silently rotate or reinterpret a declaration.

Source-coordinate evidence that must join a detected local build, notably AP242 correlation
geometry, crosses one explicit source→local boundary in `extract_pmi_report(..., frame=...)`:

- points use `PartFrame.to_local`;
- vectors use the frame basis without origin translation;
- referenced topology receives the same rigid source→local placement before its local
  axis-aligned box is measured; and
- scalar lengths and angles remain unchanged.

The default `frame=None` route remains byte-for-byte caller-space compatible. Transformation
happens while XCAF topology is measured, before principal-axis, reference-station, cylinder, and
datum validation; post-processing an already-classified `PmiRecord` would lose oblique or
frame-aligned evidence. Transforming an already-axis-aligned source box would also retain empty
space introduced by the source axes under arbitrary rotation, weakening correlation ownership;
the imported shape is therefore rigidly located first without rebuilding topology. Part21-only
scalars, labels, identities, and source census do not move.

### 3. Gauge is capability, not material meaning

`FramePolicy` gives every successful `FrameGauge` a conservative consumer interpretation:

- `FULL` establishes a directed, ordered basis. Axis-sensitive facts may be consumed once their
  rigid-motion parity is proved.
- `ORTHOGONAL` establishes local principal geometry but leaves sign or axis interchange
  unobservable. Its representative axes do not establish authored side or material-axis identity.
- `AXIAL` additionally leaves roll unobservable. Only roll-invariant facts may be consumed until
  a specific transverse/asymmetric requirement has reviewed evidence.

The local working geometry remains valid under every successful gauge. The policy limits semantic
claims Draftwright may derive from the representative basis; it does not relabel a gauge as FULL.

### 4. Refusal is closed and typed

A provider refusal becomes `FramedDetectionRefusal(source_part, reason)`. The boundary returns no
working solid, result, or guessed axes and never invokes the raw aggregate. If a supported product
later offers raw fallback, that is an explicit caller or top-level build-policy choice with a
visible decision—not hidden recovery inside the frame adapter.

### 5. Plural turned profiles are one compiler input, never a selected body

Recognisers 0.4.9 and 0.4.10 preserve body-local turned-profile membership. `Analysis.profiles` and
`build_part_model(..., profiles=...)` now carry that public tuple through the record→IR waist;
`Analysis.prof` and the compatible `prof=` input remain zero/one projections only for behavior
that genuinely requires one coaxial stack. No production consumer selects the first profile or
merges disjoint bodies. `single_turned_profile` remains an explicit legacy accessor and still
raises `MultipleTurnedProfilesError` when a caller asks for one profile but the result owns several.

Each `TurnedStep` lowers against its `TurnedProfileKey.axis_origin`, so its `StepFeature.frame` and
span stay on the physical body's axis line rather than the compound bounding-box centre; the
immutable key also crosses the IR as structural body provenance. Grooves are joined only to the
profile on the same line. The renderer groups step chains by exact profile identity, with an
axis-line/disconnected-run fallback only for declared or legacy features without that provenance,
before any repeated-run collapse. It anchors each chain to that body's silhouette; equal lengths
on parallel shafts therefore retain separate marks and `DimensionId` provenance. The renderer
matches overlapping physical occurrences of the same turning axis to conflict-free projected lanes
across the two orthographic longitudinal views. Occurrences on the same projected axis line may
reuse a lane only when their projected axial intervals are disjoint; perpendicular profile axes
remain independent candidates for the common placement/overlap stages. If the available authored
views or a dense profile grid provide no conflict-free assignment, that profile's chain is dropped
with a required-outcome diagnostic instead of being drawn over a sibling. Physical axial lint searches
the same pair of profile views and evaluates each profile independently, prioritising exact
registry measurement provenance and rejecting tied unowned geometric witnesses. Structured
claims, groove bands, drawing witnesses, and overall-drop contingency remain body-local; a sibling
with equal shoulder stations cannot certify a missing chain. A deferred subset edit repeats view
assignment against the complete physical-profile roster, then emits only the requested refs; a
surviving sibling therefore keeps its auto-pass lane rather than being forgotten during replay.

Declared `StepFeature` inputs follow the same plural compiler shape without detection. Their
profiles are grouped in caller coordinates by axis line and disconnected axial run, preserving
ADR 0011's coordinate authority. Generated scripts do not expose or serialize the provider's
`TurnedProfileKey`; the emitter replaces each detected occurrence with a stable Draftwright-owned
opaque `profile_group=` token, allocated outside every caller-authored token already present in the
model. This preserves even adjacent or overlapping coaxial body partitions without making provider
types part of the public `Sheet` surface. Synthetic declared profiles retain that opaque membership
alongside their geometrically valid provider-shaped key, so lint can join placed measurements to
the exact declaration group **and axis line** instead of relying on either ambiguous witness alone.
Token-backed and provider-key axis lines are compared at their published/script precision, never
with the generic 0.5 mm geometric-witness tolerance; a reused token therefore cannot merge two
nearby physical axes. Ordinary declarations that omit a group still reconstruct physical grouping
from axis lines, step spans, and intervening groove bands, with a tolerance limited to the script's
documented 0.001 mm coordinate precision. Aggregate `PartModel.orientation` is derived from the
set of declared step axes: one unique axis retains that orientation, while a mixed-axis inventory
uses `None` regardless of declaration order. The legacy global-height misuse guard applies only to
its single-solid domain and does not treat a caller-owned group token as evidence of another body;
profiles in a multi-solid compound need not tile that compound's global axial envelope. Existing model,
annotation, dimension, and lint round-trip parity tests guard that this provenance translation does
not hide a drawing divergence.

The released 0.4.10 `Groove` record does not carry `TurnedProfileKey`. A groove band that overlaps
multiple nested coaxial profiles therefore has no exact public owner: position-only suppression can
delete a sibling's legitimate step and position-only lint can falsely credit both bodies. The
compiler refuses that ambiguous join rather than guessing or rescanning topology, and physical lint
independently refuses to credit the groove to either profile if an external producer bypasses the
compile gate. The missing provider contract is tracked upstream as
[`b123d-recognisers#354`](https://github.com/pzfreo/b123d-recognisers/issues/354); independent plural
work proceeds without treating the blocked case as supported. When the public records identify one
owner unambiguously, the declared/emitted synthetic profile includes that groove's narrow physical
band in the same denominator as the detected profile. Lint maps an evidenced groove-width claim to
that exact band index rather than adding a scalar count. Removing either surrounding step therefore
lowers direct and generated-program completeness identically; neither a groove nor a sibling profile
can fill an unrelated missing band.
The raw and explicit framed paths both compile the provider's plural groups through this same waist; the
framed path pairs them with the provider's exact local solid.

### 6. Activation is opt-in and source/working ownership stays visible

`build_drawing(..., framed_recognition=True)` selects the prepared local unit above analysis,
compilation, projection, placement and physical lint. `Drawing.part` remains caller-coordinate
provenance; `Drawing.working_part` is the exact solid consumed downstream. A typed provider refusal
is handled once by `Analysis`, the product-policy owner, as a visible `raw_fallback` decision. The
leaf boundary still performs no fallback. Declared models never prepare or recognise a frame.

The raw route remains the default through the reviewed rollout. Opt-in evidence covers stable
requirement and annotation identity under rigid motion, exact PMI point/vector/box transforms,
one preparation across scale retries, off-axis pattern locations, physical lint against the
working solid, and measurement-complete multi-diameter X/Y shafts. Framed classification adds the
correct rotational-envelope requirement for such a shaft; its step diameters and lengths remain
equal to raw, so that extra requirement is intentional rather than a lost measurement. Platform
and representative corpus canaries remain release gates, not hidden conditions in the adapter.

The provider's 0.4.14 accepted-occurrence evidence API covers the framed route. A successful framed
build retains one `FramedRecognitionEvidence` whose records and working faces share the local
compiler space and whose caller-face resolver uses the provider's exact topology-preserving map.
Draftwright does not run a second raw acquisition or synthesize references across authority
universes. The former gap tracked by
[`b123d-recognisers#463`](https://github.com/pzfreo/b123d-recognisers/issues/463) is resolved by that
released contract.

## Amendment 1 — Framed evidence remains one coordinate-explicit authority

Successful framed evidence enters the existing build-owned recognition cache and exact occurrence
ownership conversion. Report schema v1 labels every public record as `provider-working` and carries
the exact `PartFrame` origin, ordered basis, and gauge as `caller_from_record`; raw reports label the
same relationship as caller-space identity. The frame is interpretation metadata, not a durable
feature identity, a coordinate transformation performed by Draftwright, or an annotation-placement
surface. Compilation, completeness, reporting, and rendering continue to consume the local working
part and local records coherently.

`RefusedFramedEvidence` is distinct from `RefusedPartFrame`. It means preparation succeeded but an
exact caller-face bijection could not be proved before the aggregate ran. Draftwright preserves the
established framed drawing by invoking the result-only aggregate once, records the evidence-refusal
reason, and refuses ownership/report projection. It neither invokes raw recognition nor emits a
partial/empty occurrence denominator. A preparation refusal retains the existing explicit
top-level raw-fallback policy.

## Consequences

- Draftwright can now exercise the correct normalize→classify→recognise ordering without a second
  cylinder scan or provider-private API.
- The provider's exact local topology and body-local FaceLevel, RiserEvidence, and TurnedProfile
  identities stay paired with the records they justify.
- Frame refusal remains visible, while plural turned bodies retain their own IR frames, rendered
  chains, and completeness outcomes rather than selecting or merging a body.
- AP242 points, vectors, AABBs, finite cylinders, and datum geometry can now share the provider's
  exact local coordinate system without altering the default caller-space API.
- The 0.4.9-and-later prepared contract and plural compiler intentionally correct raw compound interpretation:
  disjoint turned bodies no longer form an invented cross-body profile or disappear behind a
  singular projection; each body contributes its own step IR and chain.
- Framed recognition is a supported explicit `build_drawing` option and uses the same plural
  compiler waist; raw remains the default while canary evidence accumulates under #1357.

## Rejected alternatives

- **Transform every recognition record into caller coordinates.** Arbitrary directions cannot be
  represented by the principal-axis IR, and a family-by-family transform would duplicate policy.
- **Recreate normalization in Draftwright.** That loses the provider's exact topology/provenance
  authority and creates two answers for one frame.
- **Classify the caller solid before normalization.** That makes classification placement-dependent
  and repeats the ordering defect fixed upstream by the prepared seam.
- **Call the convenience framed builder with `rotational=False`.** It silently changes
  classification-gated inventories for normalized shafts.
- **Fall back to raw recognition inside the adapter.** That erases typed refusal and produces two
  coordinate contracts behind one call.
- **Select the first turned profile.** Provider ordering is not manufacturing ownership; doing so
  would silently discard valid bodies.

## Verification

`tests/test_issue_1357_framed_boundary.py` guards exact preparation/result pairing, cylinder reuse,
all gauges and refusal reasons, local multi-diameter classification, body-local levels/risers/turned
profiles, explicit singular-accessor refusal, and unchanged raw coordinate selection. The
fail-closed manifest join
in `tests/test_recogniser_capabilities.py` guards the 0.4.14 record schemas (including the unchanged
0.4.9 body-local schemas, each blind-slot family, and the explicitly deferred additive families).
Existing
`tests/test_declared_recognition_gate.py` and `tests/test_part_model.py` retain declared no-recognition
and one-aggregate lifecycle evidence; `tests/test_import_boundaries.py` keeps the boundary at the
leaf rank. `tests/test_issue_1357_pmi_frame.py` proves point/vector distinction, tight local AABBs
from arbitrarily rotated non-square topology, default caller-space compatibility, and exact framed
AP242 dimension, datum, finite-cylinder, and manufacturing-topology evidence on real CTC-03 and
GRM-03 fixtures.
`tests/test_issue_1438_framed_evidence.py` guards successful result/evidence/cache/ownership/report
identity, exact working/caller face resolution, coordinate-space serialization, and typed evidence
refusal without a raw or duplicate aggregate scan.
`tests/test_issue_1357_framed_activation.py` guards explicit selection/fallback, source versus
working ownership, scale-retry reuse, rigid-motion build parity, cross-axis turned measurement
completeness, arbitrary-frame PMI evidence, and off-axis pattern location completeness.
`tests/test_issue_1357_plural_turned_profiles.py` proves aggregate/injected compiler
equivalence, body-local IR origins, separate rendered chains and measurement identities, independent
axial lint, generated-script reconstruction, orthographic profile-view selection, and declared
caller-coordinate parity for parallel shafts, adjacent coaxial emission, ambiguous-grid drops,
front-only fail-closed behavior, disjoint coaxial lane reuse and script replay, collision-free mixed
token namespaces, group-plus-axis lint ownership, single-solid guard scope, perpendicular-axis lane
independence, explicit refusal of the upstream-blocked groove ambiguity, exact emitted-group lint
ownership, alternate-view detail ownership, and remove/deferred-replay lane preservation. The framed
activation suite composes raw/framed plural compilation with a forced scale retry, proving that two
profiles and four chains survive reuse while frame preparation still occurs once.

## Relationship to prior decisions

- **ADR 0005:** the boundary is a rank-zero immutable intake unit, not new Drawing-owned state.
- **ADR 0007:** the provider retains geometry recognition; Draftwright retains classification,
  lifecycle, record→IR conversion, drafting policy, and lint.
- **ADR 0011:** declarations remain caller-coordinate authority and recognition-free at build time.
- **ADR 0013:** only public provider records cross the geometry→Draftwright adapter.
- **ADR 0015:** detected compilation changes its coherent input unit while the PartModel compiler
  waist remains plural and coordinate-consistent.
- **ADR 0017:** preparation and classification reuse one cylinder substrate and one aggregate;
  correspondence remains evidence-gated.
- **ADRs 0004/0014:** no annotation placement API or raw page coordinate is introduced.
