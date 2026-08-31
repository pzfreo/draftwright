# ADR 0020 — Provider-owned local frame for automatic recognition

- **Status:** Accepted for explicit opt-in rollout
- **Date:** 2026-08-30
- **Deciders:** Paul Fremantle (pzfreo)

## Context

Recognition records contain positions, principal axes, spans, topology ownership, and finite
geometry evidence. Historically they used caller/world coordinates. Arbitrary rigid rotation can
therefore change detection and finished drawing meaning even though it does not change the part.

`b123d-recognisers` 0.4.9 exposes a two-stage public seam: `prepare_framed_part` returns either a
typed refusal or the exact topology-preserving local solid and frozen cylinders; Draftwright can
classify that exact solid before requesting its one aggregate. Consuming local records beside the
caller-space shape would mix coordinate systems. Recreating normalization in Draftwright would
create a second authority. Transforming records back to world coordinates cannot fit the current
principal-axis IR after arbitrary rotation.

## Decision

### One coordinate-coherent compiler unit

`recognition_frame.adapt_recognition` is the sole selection boundary. A successful framed run
classifies the provider's prepared solid and cylinders, requests exactly one aggregate, and keeps
the resulting working solid, records, classification, and `PartFrame` inseparable. Bounding-box
analysis, IR lowering, planning, projection, rendering, and physical lint all use working
coordinates. Scale retries reuse that unit.

The provider owns frame inference and topology-preserving normalization. Draftwright owns the
selection and all drafting policy. No downstream stage imports frame internals or guesses which
coordinates a record uses.

### Source and working geometry have distinct meanings

For a framed automatic build, `Drawing.part` remains caller-space source geometry;
`Drawing.working_part` is the exact local compiler solid; `Drawing.recognition_frame` records the
caller-to-working frame; and `Drawing.recognition_frame_decision` records status, gauge, and any
refusal reason. Declared and raw builds use the same object for source and working geometry.

### Declarations retain authority

Supplying `model=` and every `Sheet` build remain in caller coordinates and perform no automatic
recognition (ADR 0011). A caller deliberately re-declaring detected framed output must pair
`drawing.working_part` with `drawing.model()`.

### AP242 evidence crosses once

Nominal values and source identities do not change. Correlation points, all eight support-bounds
corners, exact datum directions, and finite cylindrical references move once into working
coordinates before PMI lowering. Exact direction vectors are retained during extraction even
when not world-principal, then projected to the local principal-axis vocabulary. The canonical
extraction report remains a source-space census; the analysis stores a separate working-record
projection for compilation and scale reuse.

### Gauges, refusal, and rollout are explicit

`FULL`, `ORTHOGONAL`, and `AXIAL` use the same paired-unit contract. AXIAL roll is a stable gauge,
not inferred manufacturing intent. A typed `RefusedPartFrame` performs exactly one raw aggregate
run and reports `raw_fallback` plus the provider reason. The comparison route reports `raw`.

The public rollout begins as `framed_recognition=True`; raw remains the default until supported
platform CI and representative real-part canaries enumerate and review raw-to-framed transitions.
This is a staging gate, not an architectural preference for world coordinates.

Frame-induced axis permutations do not erase requirements. In particular, X/Y-normal hole
patterns compile their two bbox-referenced locations under the stable
`location_pattern.location` identity and enter the existing shared placement solve. Rectangular
grid pitch axes come from the recognised lattice frame, not floating-point member ordering.
Audit identities canonicalize rounded signed zero.

Multi-diameter X/Y round stock remains on Draftwright's measurement-complete boss/envelope path
until #1402 supplies axis-covariant turned-length furniture. This is an explicit consumer rollout
limit: prematurely classifying it rotational loses approved axial and boss measurements, as the
display-completeness sweep proves. It does not narrow the provider's covariant evidence.

## Consequences

- Rigid rotation and translation can be assessed in one stable local coordinate system.
- Body/face provenance survives because consumers use the provider's exact normalized topology.
- Public source geometry remains inspectable without leaking into local projection or lint.
- A framed build may intentionally select different principal views than a raw build; those are
  rollout transitions requiring evidence, not byte-for-byte regressions.

## Rejected alternatives

- Transform local records back into caller coordinates: arbitrary directions do not fit the IR.
- Normalize independently in Draftwright: this duplicates authority and weakens provenance.
- Return a naked framed `PartModel`: it cannot prove which solid shares its coordinates.
- Enable framing by default before matrix/canary evidence: covariance tests do not disposition
  every raw-to-framed layout transition.
- Treat refusal as a proved caller-axis frame: this erases a meaningful diagnostic.

## Conformance

`tests/test_issue_1357_framed_recognition.py` covers all gauges, typed refusal, source/working
separation, compound locality, exact PMI direction transfer, off-axis pattern requirements,
declared ownership, one-run scale reuse, and rigid-motion parity through requirements,
annotations, suppressions, and lint. Existing declared-recognition, import-boundary, platform,
and real-part suites remain rollout gates.

This preserves ADRs 0011, 0013, 0014–0018: declarations remain authoritative; normalization stays
in the provider; all dimensions remain compiled solve candidates; one aggregate feeds the shared
IR and critique; and view planning reads requirements rather than inferring a second frame.
