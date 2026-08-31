# ADR 0020 — Provider-owned frame boundary for detected compilation

- **Status:** Accepted; prepared boundary and explicit opt-in activation implemented. Raw remains
  the rollout default pending platform and corpus canaries.
- **Date:** 2026-08-31
- **Deciders:** Paul Fremantle (pzfreo)

## Context

Recognition records express points, bounds, directions, spans, and principal axes. The historical
automatic path asks `b123d-recognisers` to interpret the caller/world placement directly. A rigid
rotation can therefore change which feature families are recognised and which semantic views
Draftwright plans, even though the physical part did not change.

`b123d-recognisers` 0.4.9 provides the public preparation seam needed to remove that dependence.
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
3. calls `PreparedFramedPart.recognise` once with that classification; and
4. returns an immutable `FramedDetection` that keeps the caller-space source, prepared unit,
   classification, and `FramedRecognitionResult` together.

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

### 5. Plural turned profiles fail explicitly at the current singular waist

Recognisers 0.4.9 preserves body-local turned-profile membership. Draftwright's current `Analysis`
stores one `TurnedProfile`, so selecting the first of several or merging disjoint bodies would be
false. `single_turned_profile` consumes the public `RecognitionResult.turned_profiles` grouping
and raises `MultipleTurnedProfilesError` when a caller requires one physical profile but receives
several. A standalone `build_part_model` therefore fails explicitly instead of inventing global
ownership.

The unchanged raw production path has an older supported compound behavior to preserve: parallel
grooved shafts build without a global turned profile. `_raw_compatible_turned_profile` retains that
no-global-profile result when 0.4.9 now exposes several body-local groups and logs the #1357
deferral. It neither selects nor merges a profile. Framed activation must make the compiler input
plural before consuming those new TurnedSteps; it may not inherit this compatibility projection.

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

## Consequences

- Draftwright can now exercise the correct normalize→classify→recognise ordering without a second
  cylinder scan or provider-private API.
- The provider's exact local topology and body-local FaceLevel, RiserEvidence, and TurnedProfile
  identities stay paired with the records they justify.
- Frame refusal and singular-IR limitations remain visible rather than changing coordinate
  authority, selecting a body, or merging disjoint profiles.
- AP242 points, vectors, AABBs, finite cylinders, and datum geometry can now share the provider's
  exact local coordinate system without altering the default caller-space API.
- The framed boundary does not activate framed production. The 0.4.9 dependency does intentionally
  correct one raw compound interpretation: disjoint coaxial bodies no longer form an invented
  cross-body turned profile, so the existing raw path preserves them as independent bosses with no
  global step chain.
- Framed recognition is a supported explicit `build_drawing` option; raw remains the default while
  canary evidence accumulates under #1357.

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
profiles, singular-waist refusal, and unchanged production selection. The fail-closed manifest join
in `tests/test_recogniser_capabilities.py` guards the 0.4.9 record schemas. Existing
`tests/test_declared_recognition_gate.py` and `tests/test_part_model.py` retain declared no-recognition
and one-aggregate lifecycle evidence; `tests/test_import_boundaries.py` keeps the boundary at the
leaf rank. `tests/test_issue_1357_pmi_frame.py` proves point/vector distinction, tight local AABBs
from arbitrarily rotated non-square topology, default caller-space compatibility, and exact framed
AP242 dimension, datum, finite-cylinder, and manufacturing-topology evidence on real CTC-03 and
GRM-03 fixtures.
`tests/test_issue_1357_framed_activation.py` guards explicit selection/fallback, source versus
working ownership, scale-retry reuse, rigid-motion build parity, cross-axis turned measurement
completeness, arbitrary-frame PMI evidence, and off-axis pattern location completeness.

## Relationship to prior decisions

- **ADR 0005:** the boundary is a rank-zero immutable intake unit, not new Drawing-owned state.
- **ADR 0007:** the provider retains geometry recognition; Draftwright retains classification,
  lifecycle, record→IR conversion, drafting policy, and lint.
- **ADR 0011:** declarations remain caller-coordinate authority and recognition-free at build time.
- **ADR 0013:** only public provider records cross the geometry→Draftwright adapter.
- **ADR 0015:** future detected compilation changes its coherent input unit, not the PartModel
  compiler waist.
- **ADR 0017:** preparation and classification reuse one cylinder substrate and one aggregate;
  correspondence remains evidence-gated.
- **ADRs 0004/0014:** no annotation placement API or raw page coordinate is introduced.
