# ADR 0021 — The public inspection surface and its versioned evidence document

- **Status:** Accepted; version 1 implemented for STEP sources, raw/caller coordinates only.
  Framed inspection is deferred pending b123d-recognisers#493.
- **Date:** 2026-09-04
- **Deciders:** Paul Fremantle (pzfreo)

## Context

Every Draftwright surface so far answers "draw this part". Epic #1256 needs a different question
answered — *what does Draftwright actually see in this file?* — for automated callers that will
never render a sheet. #1460 is its first slice.

The evidence such a caller needs already exists inside a build: the accepted recognition
occurrences, Draftwright's consumer disposition for each, the provider's face/area association
accounting, and the AP242 PMI census. ADR 0017 already governs how that evidence comes into
being — one aggregate run per build, ownership captured at conversion time, no persistent
topology identity — and Amendment 21 already publishes part of it as the schema-v1 drawing
report.

What ADR 0017 does not own is a **new public versioned contract**: what an inspection document
may state, what it must never state, how it is versioned, and what happens when it cannot be
produced truthfully. Those are decisions about Draftwright's public surface, not about the
recognition lifecycle, and appending them to a 28-amendment lifecycle ADR would bury a contract
readers must be able to find.

The specific hazard is that this document is designed to be read by software rather than a
draughtsperson. A human reading a drawing knows a clear sheet is not a certificate. An agent
reading `"status": "bounded-recognition-evidence"` will do whatever the field name licenses.

## Decision

**1. Inspection is a projection, never a build.** `inspect_step(path)` returns evidence. No
`Drawing`, compiled dimension plan, annotation placement, render, export, or physical lint score
may be required to obtain it, and none may influence what it says.

**2. The document is closed and versioned.** `schema` names it, `schema_version` gates it. Every
Draftwright-owned object is closed; only the producer-owned payloads — the provider's `record`
and each `pmi.records[]` entry — may gain fields within a version. Adding a field to a closed
object, changing a meaning, or removing a field requires a new version.

**3. Four kinds of fact stay apart, and each section names its own provenance and coverage.**
Measured STEP geometry, recogniser inference, Draftwright consumer policy, and source-authored
PMI are different claims with different reliability. A caller must never have to infer which it
is holding.

**4. A clear document states its own limits.** The clear status is named
`bounded-recognition-evidence`, not `pass` or `clear`, and a stable `qualifiers` array carries
the negative claims as branchable codes: not physical completeness, not manufacturing readiness,
no inferred material, process, finish, thread, fit, or tolerance intent. `unassociated` faces
carry their own `not_evidence_of_missed_feature` qualification, because association coverage is
neither recall nor accuracy. These codes are part of the closed schema precisely so that
weakening them is a version change rather than a wording change.

**5. Identity is document-local.** IDs come from the provider's accepted-occurrence order and
Draftwright's final IR order. Provider `FeatureRef`/`FaceRef` values, topology indexes, object
addresses, absolute source paths, and page coordinates are never serialized. Faces are described
by bounded geometry — surface kind, area, centroid, bounding box — ordered by their own
serialized values, because the provider hands them back unordered.

**6. One byte snapshot, one aggregate run, and a pure projector.** The source is resolved once
and read once; geometry, recognition, and PMI all consume a private copy of those hashed bytes,
so replacing a mutable or symlinked source mid-inspection cannot split the document across two
files. The projector itself never recognises, imports geometry, or reads the filesystem, which
is what lets a consumer that has already paid for a detect run — script generation — emit the
same document without a second aggregate (ADR 0017).

**7. Version 1 is raw/caller-coordinate only.** A run that recognised in a provider working
frame is refused rather than reported under `"coordinates": "caller"`, which would be exactly
the lie ADR 0020 exists to prevent.

**8. Refuse rather than mislead — but never at the cost of the caller's primary job.**
`InspectionUnavailableError` covers everything that would make the document untruthful: no solid
body, an unclassified ownership ledger, an absent aggregate, a non-raw frame, an unknown PMI
extraction outcome, a value JSON cannot state. A consumer emitting the document as a *sidecar*
logs and skips instead, because a missing sidecar must not fail script generation.

## Consequences

### Positive

- An automated caller gets one document instead of three partial views, and cannot mistake
  recogniser inference for measured geometry or for authored PMI.
- The evidence is free of persistent identity, so nothing here can be stored and later compared
  against a different run as though it were stable.
- Because the projector is pure, a second front door costs no second recognition run — verified,
  not assumed.

### Costs and risks

- **The shared detect seam does more work than the document uses.** `_detect_part_model_analysis`
  sizes the part while detecting: `compose` picks a page and scale, `model/planner`'s
  `plan_dimensions` runs, `model/callout` builds hole-callout specs, and `view_plan` arranges
  views. All of it is discarded. This is accepted deliberately rather than overlooked: ADR 0017
  makes one run per source the invariant, and a leaner inspect-only detect path would be a second
  seam whose divergence from the drawing path could not be checked. The cost is real and
  measured — an inspection of `nist_ctc_02_asme1_ap242.stp` takes about 14 s against about 1.5 s
  for a plain block. A guard test pins the exact set of engine modules an inspection executes, so
  this set cannot grow silently.
- The closed schema means genuinely additive evidence still costs a version bump.
- `qualifiers` protects a caller that reads it. It cannot protect one that reads only `status`.

## Rejected alternatives

**Forward the provider's own report.** `b123d_recognisers.explanations.build_recognition_report`
exists, but it is a Python object tree rather than JSON, and its `coverage`/`families` are the
provider's explanation of its own run — not Draftwright's consumer dispositions, ownership, or
PMI census, which are the substance of this document. The provider's per-record `to_dict()` is
forwarded verbatim inside `record`; nothing above that is the provider's to state.

**Reuse the schema-v1 drawing report.** It requires a built drawing, a lint summary, and a
requirement ledger — precisely the machinery decision 1 excludes. The two documents share the
occurrence projector and deliberately not their scope.

**Emit a partial document when evidence is missing.** An inspection that quietly drops an
occurrence it cannot classify is worse than no inspection, because it reads as completeness.

**Add a leaner inspect-only detect path** to avoid the sizing work in Costs. Deferred, not
dismissed: it trades a measured cost for a second seam and a divergence risk, and that trade
should be made against profiling of real inspect traffic rather than in advance.

## Relationship to existing ADRs

ADR 0017 keeps the recognition lifecycle: one run per source, ownership captured at conversion
time, no second scan, no persistent identity. Its Amendment 28 records that the same one-run
evidence now has a read-only projection and points here for the public contract. ADR 0013 keeps
recognition geometry-only — inspection reads PMI with lowering off, and records
`recognition.pmi_mode` so a reader can tell. ADR 0020 keeps the frame boundary. ADRs 0010, 0011,
0014, 0015, and 0016 are not engaged: no registry, no placement, no compiled plan, no
compiler-owned completeness denominator, and the IR's field shapes are not re-exported — only
`{"id", "kind"}` reaches the document.
