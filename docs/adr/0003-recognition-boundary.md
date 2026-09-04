# ADR 3 — The recognition boundary

- **Status:** Accepted (2026-09-04). Consolidates archived 0007, 0013, 0017 (lifecycle half)
  and 0020. Reporting and evidence documents are ADR 5.
- **Deciders:** Paul Fremantle (pzfreo)

## Decision

Feature recognition is not Draftwright's to implement. `b123d-recognisers` (Apache-2.0) turns a
build123d solid into deterministic, immutable, geometry-only records; Draftwright turns those
records into a drawing. The line between them is *reason about geometry* versus *reason about
drafting*, and it is held in both directions: the provider ships no drafting policy, and
Draftwright never rescans topology to second-guess a record.

A drawing build recognises **at most once**. One aggregate run produces one `RecognitionResult`
with its `RecognitionEvidence`, owned by the build's `RecognitionCache`; every consumer — model
construction, lint, reports, inspection — reads that one result or a pure projection of it. A
declared model recognises nothing until physical critique explicitly asks, and then once.

Every provider record type has exactly one home in Draftwright, decided fail-closed: a typed
adapter into the IR, an explicit consumer disposition (`unsupported`, `deferred`,
`evidence_only`), or a refusal. A new provider family cannot appear silently. Which occurrence
became which IR feature is recorded at the conversion site from same-run object identity — never
reconstructed later from values, order, labels, coordinates, faces or topology — and an
occurrence Draftwright cannot account for is reported as such, never dropped from the count.

Recognition may run in the caller's coordinates (raw) or in a provider-prepared local frame
(framed). A framed run consumes the provider's exact working solid end to end; a refusal is
typed and visible, never a silent fallback inside the adapter.

## Invariants

Each names the test that fails when it is broken. "Unguarded" lists the ones that do not yet.

1. **Recognition executes in the provider, never in Draftwright.** No engine module calls a
   public `recognise_*` function outside the aggregate; `src/draftwright/recognition/` is a
   re-export facade with no implementation.
   `test_external_recognition_boundary.py`, `test_counting_calls.py`.
2. **One aggregate per build; zero for a declared render.** An automatic build calls
   `build_recognition_evidence` exactly once. A declared build/render calls nothing; its first
   physical critique or export obtains at most one cached aggregate, and repeated lint adds no
   call. `test_detect_once.py`, `test_declared_recognition_gate.py`, `test_recognition_result.py`.
3. **Consumers project; they do not rescan.** Lint, model construction and reports read the
   cached result or a pure function of it. Shared substrates (the cylinder inventory, risers)
   are scanned once and reused. `test_detect_once.py`, `test_issue_958_cross_solid_recognition.py`.
4. **The provider join is fail-closed.** Draftwright's capability declaration — every consumed
   family, record type and schema version — is validated against the installed package's
   manifest, and a mismatch fails the build. The exact provider version is pinned.
   `test_recogniser_capabilities.py`, `test_inspection_contract.py`.
5. **Every record type has exactly one home.** The record universe is derived from the
   provider's public return annotations, not a hand list; the adapter registry partitions it
   with no gap and no overlap. `test_detect_registry.py`.
6. **No provider object crosses the IR waist.** Records reach the `PartModel` only through
   `model/detect.py`; `linting/` imports no `draftwright.model`; `FeatureRef`/`FaceRef` never
   enter the IR, the compiled plan, placement, generated scripts or output.
   `test_import_boundaries.py` (`test_linting_does_not_import_model`),
   `test_issue_1438_occurrence_ownership.py`.
7. **Occurrence ownership is captured at conversion, by identity.** Each accepted occurrence is
   bound to its exact IR owner where the adapter creates it; equal-valued copies, feature order,
   labels, coordinates, face overlap and topology indices establish nothing. An expected owner
   that did not reach the final model is `unexpectedly_missing`, never silently represented.
   `test_issue_1438_occurrence_ownership.py`, `test_issue_1438_pattern_ownership.py`,
   `test_issue_1438_nested_ownership.py`, `test_issue_1438_channel_ownership.py`,
   `test_issue_1438_turned_step_ownership.py`, `test_issue_1438_through_step_ownership.py`,
   `test_issue_1438_boss_ownership.py`, `test_issue_1438_plate_ownership.py`.
8. **Ownerless policy is explicit per occurrence, from one source.** Families Draftwright does
   not draw carry `unsupported`, `deferred` or `evidence_only` with a closed reason code,
   projected from the same capability declaration the contract validates — never a second
   registry. `test_issue_1438_policy_dispositions.py`, `test_issue_1245_passage_disposition.py`,
   `test_issue_1246_prismatic_pocket_disposition.py`, `test_issue_1247_angled_step_disposition.py`.
9. **Recognition is geometry-only.** Records are frozen, JSON-serializable geometry with no
   build123d type inside; drafting policy never enters the provider; PMI lowering may change
   which IR feature owns a record but never what was recognised.
   `test_recogniser_contract.py`, `test_external_recognition_boundary.py`.
10. **The framed boundary is one owned intake with typed refusal.** `prepare_framed_detection`
    calls the provider once, classifies the returned local solid, recognises once, and returns
    frame, solid and result together or a `FramedDetectionRefusal` — never a guessed axis, never
    a raw run inside the adapter. Records stay in the frame in which they were proved.
    `test_issue_1357_framed_boundary.py`, `test_issue_1357_framed_activation.py`,
    `test_issue_1357_pmi_frame.py`.
11. **Frame gauge limits semantic claims, not geometry.** `ORTHOGONAL` and `AXIAL` frames do
    not establish authored side or material-axis identity. `test_issue_1357_framed_boundary.py`.
12. **Plural turned profiles are one compiler input.** No production consumer selects the first
    profile or merges disjoint bodies; a groove that cannot name one owner is refused.
    `test_issue_1357_plural_turned_profiles.py`.

**Unguarded.** The provider's own explanation of a run — what it proposed and rejected, which
families it did not evaluate — is on a separate entry point that re-runs recognition, so no
Draftwright consumer reads it; b123d-recognisers#494 asks for an API over an already-completed
result.

## Boundaries

- **ADR 1 (pipeline).** Records enter the IR through one adapter registry; the IR is a
  drafting model, not a recognition model. Classification (`_is_rotational`) is a geometric fact
  the pipeline consumes like any other record.
- **ADR 2 (layout).** Recognition ranks nothing and places nothing. Frame choice is made above
  every layout stage so projection, placement and lint all consume one working solid.
- **ADR 4 (declared intent).** A declaration is a second producer of the same IR and is
  recognition-free at build time. Framed detection never rotates or reinterprets a declaration.
- **ADR 5 (trust).** This ADR owns *what was recognised and what Draftwright did with it*. How
  that is written down — reports, sidecars, inspection documents, completeness — is ADR 5's.

## Superseded

- 0007 — recognition vendored into Draftwright from helpers (2026-06); then extracted to
  `b123d-recognisers` (2026-08). helpers renders; it holds no recognition, lint or layout.
- 0013 — the uniform `recognise_<feature>(part, **kw) -> list[Record]` contract; derived
  recognisers are part-less; extraction gated on a second consumer, then delivered as v0.1.0.
- 0017 Amendments 1–20 — one result per run, cache ownership, and per-family ownership capture,
  one family at a time. Amendment 1 narrowed a five-phase correspondence programme to the
  ownership contract plus evidence gates; the programme's remaining abstractions were never
  adopted.
- 0017 Amendments 21–25 — the report, its persistence, the generated-Python gap snapshot and the
  CLI sidecar are ADR 5.
- 0020 — provider-owned frame preparation, typed refusal, gauge policy, plural profiles.

## Open

- **Framed evidence.** A framed build exposes a `RecognitionResult` but no `RecognitionEvidence`
  or ownership; adopting `build_framed_recognition_evidence` waits on the one-run refusal contract
  (b123d-recognisers#493).
- **Raw remains the rollout default** pending platform and corpus canaries (#1357).
- **`lint_prismatic_coverage(recognition=...)`** is a channel that accepts a foreign aggregate;
  remove or narrow it when next touched (#1032).
- **Compatibility re-exports** `draftwright.recognition` and `draftwright.score` expire at 0.6.0.
