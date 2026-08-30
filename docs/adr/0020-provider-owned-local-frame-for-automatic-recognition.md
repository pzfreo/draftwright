# ADR 0020 — Provider-owned local frame for automatic recognition

- **Status:** Accepted for explicit opt-in rollout; legacy automatic recognition remains the
  default until the supported platform matrix and representative real-part canaries approve a
  default transition.
- **Date:** 2026-08-30
- **Deciders:** Paul Fremantle (pzfreo)

## Context

Recognition records contain positions, axes, spans, face/body ownership, and finite geometric
evidence. Historically those facts used the caller's STEP/world coordinates. Translating a part
does not change their meaning, but an arbitrary rotation changes which principal-axis vocabulary
the recognisers can use and can therefore change detection, view planning, and the finished
drawing.

`b123d-recognisers` 0.4.6 provides the immutable public
`build_framed_recognition_result(part)` contract. A successful result carries:

- the inferred `PartFrame`;
- the exact topology-preserving local working solid used by the provider; and
- the aggregate `RecognitionResult` expressed against that solid.

It may instead return a typed `RefusedPartFrame`. The older
`build_recognition_result(part)` API is unchanged.

Adopting only the local records would mix them with caller-space bounding boxes and projection
geometry. Reconstructing the provider's normalization in Draftwright would create a second frame
authority and could not guarantee topology/provenance identity. Transforming every record back to
world coordinates is also not representable by the current principal-axis IR: an arbitrary
world-space direction is not one of the IR's `x`/`y`/`z` axes.

## Decision

### 1. Automatic framed recognition compiles one coherent local unit

`recognition_frame.adapt_recognition` is the only Draftwright-owned selection boundary. On a
successful framed run, the provider's exact `part` and `result` remain paired. Bounding-box
analysis, classification, record-to-IR conversion, planning, projection, rendering, and physical
lint all use that working solid and its local coordinates.

No downstream stage recreates the transform, applies it record by record, or combines local
records with the caller-space solid. The provider owns frame inference and topology-preserving
normalization; Draftwright owns the consumer decision and every drafting policy after the
recognition-to-IR adapter.

### 2. The caller solid and working solid have distinct public meanings

For a framed automatic build:

- `Drawing.part` is the caller-space source body;
- `Drawing.working_part` is the exact local solid compiled and projected;
- `Drawing.recognition_frame` is the caller-to-working `PartFrame`; and
- `Drawing.recognition_frame_decision` states `framed`, `legacy_fallback`, `legacy`, or
  `declared`, including gauge and refusal reason where applicable.

Keeping both solids prevents a public source-coordinate property from silently changing meaning
while making the geometry used by the compiler inspectable. `working_part` is read-only. Scale
retry/repack analyses reuse the same solid, aggregate, frame, and decision rather than running a
second recognition universe (ADR 0017).

### 3. Declaration remains caller-coordinate authority

Supplying `model=` and every `Sheet` build remain in the caller's coordinates and perform no
automatic recognition during rendering (ADR 0011). `detect_part_model` and `Sheet.from_part`
retain their legacy caller-coordinate contract: returning a naked local `PartModel` without its
paired working solid and frame would make it unsafe to pass back to a declared build.

A caller that deliberately wants to re-declare framed output may use
`build_drawing(..., framed_recognition=True)`, then pass both `drawing.working_part` and
`drawing.model()` to a declared build. That coordinate pairing is explicit.

### 4. AP242 geometry crosses the boundary once

AP242 nominal values and source identities are requirements and remain unchanged. Geometry used
to correlate those requirements—reference points, axis-aligned support bounds, principal-axis
hints, and finite cylindrical references—is mapped once from source coordinates into the working
frame before PMI lowering. Bounds are transformed through all eight corners; directions do not
receive the frame translation; finite cylinders are rebuilt from transformed endpoints.

The source extraction report remains a source census. Only the records passed into the local IR
lowering step are reframed.

### 5. Gauge and refusal behavior are explicit

`FULL`, `ORTHOGONAL`, and `AXIAL` successful frames use the same paired-solid contract and are
covered by fixtures. `AXIAL` establishes a stable gauge for a rotational part, but Draftwright
does not interpret its arbitrary roll as authored material direction or manufacturing intent.

A typed `RefusedPartFrame` takes exactly one documented legacy aggregate run and records
`legacy_fallback` plus the provider reason. It does not pretend the caller axes are an inferred
part frame. The explicit comparison/default route records `legacy`.

### 6. Rollout is opt-in

`build_drawing(..., framed_recognition=True)` and the equivalent `make_drawing` argument opt into
the framed route. The default stays on legacy automatic recognition for this decision's first
delivery.

This is not a permanent preference for world coordinates. Initial default-on testing showed that
provider normalization can legitimately permute even an unrotated part's principal axes. That
changes view selection, arrangement, ladders, and layout snapshots across the existing corpus.
Rigid-motion parity fixtures prove the new route's internal invariance, but they do not by
themselves approve every legacy-to-framed transition. Default promotion requires the supported
Python/platform matrix and representative real-part canaries to enumerate and review those
transitions. The old route remains available during that rollout, as issue #1357 requires.

### 7. Frame-induced axis permutations must preserve requirements

An ordinary Z-normal hole pattern can become X- or Y-normal in the working frame. Such a pattern
still has two perpendicular location requirements. The compiler therefore treats off-axis
patterns like the corresponding off-axis hole path: it compiles bounding-box-referenced location
components under the stable `location_pattern.location` identity, and the shared renderer places
only those approved entries. This extends compiler vocabulary; it does not hand-place dimensions
or bypass the collect-then-solve placement architecture (ADRs 0014 and 0016).

Feature/audit keys canonicalize rounded signed zero so kernel noise cannot make two rigidly
equivalent local builds appear semantically different.

## Consequences

- Rigid translation and rotation can be tested against finished requirements and outcomes in one
  stable local coordinate system.
- Face/body provenance survives because Draftwright consumes the provider's exact normalized
  topology instead of reconstructing a lookalike solid.
- Public source geometry remains available without being used accidentally by local lint or
  projection.
- A successful opt-in build may choose different principal views from the legacy build; that is a
  rollout transition to review, not a promise of byte-identical output (ADRs 0004 and 0012).
- The framed aggregate is requested without pre-classifying in source coordinates. Draftwright
  classifies from the returned local cylinder inventory and gates its IR consumers there. This
  preserves one aggregate run and avoids using arbitrary caller axes to decide package inventory.
- A new provider fact/API is not required for this decision. The exact local solid added by
  b123d-recognisers PR #292 and released in 0.4.6 is the necessary seam.

## Rejected alternatives

- **Transform local records back into caller coordinates.** The principal-axis IR cannot express
  arbitrary world-space directions without a larger architecture change.
- **Normalize the solid independently in Draftwright.** That creates two authorities and loses
  the provider's topology/provenance guarantee.
- **Return frame-local IR from `detect_part_model`.** A `PartModel` alone cannot prove which solid
  shares its coordinates; `Sheet.from_part` would then compile it against the wrong geometry.
- **Enable framed recognition by default immediately.** The focused invariance evidence is not a
  disposition for every corpus-wide view/layout transition.
- **Treat refusal as success in caller axes.** That erases the diagnostic distinction between a
  proved frame and no frame.

## Verification

`tests/test_issue_1357_framed_recognition.py` guards successful gauges, typed refusal, the legacy
default, source/working separation, rigid-motion requirement and finished-build parity,
off-axis-pattern locations, compound topology ownership, AP242 transforms, declared-build
coordinates, and audit-key stability. `tests/test_part_model.py` retains the one-aggregate-per-run
guard. `tests/test_declared_recognition_gate.py` retains the declared no-recognition rule.
`tests/test_import_boundaries.py` keeps the adapter at the leaf rank.

## Relationship to prior decisions

- **ADR 0011:** preserved; declarations and `Sheet` stay in caller coordinates.
- **ADR 0013:** preserved; normalization remains geometry-package policy and Draftwright adapts
  public records into its own IR.
- **ADR 0015:** refined at the recognition input: the detected compiler unit is now
  working-solid plus aggregate, still converging on the same `PartModel` waist.
- **ADR 0017:** preserved; one immutable aggregate is shared by automatic model construction and
  critique. Framed selection does not create a second run.
- **ADR 0018:** preserved; view planning reads the resulting requirement-bearing IR and does not
  infer a frame itself.
- **ADRs 0014/0016:** preserved; new off-axis pattern dimensions are compiler-approved candidates,
  never raw-coordinate placements.

