# Epic #1018 / ADR 0017 — status and next steps

Working state as of **2026-08-05**. ADR 0017 is `Proposed`; this records what has landed
against it, what implementation taught, and what to pick up next.

Read `docs/adr/0017-recognition-inventory-correspondence-and-measurement-provenance.md`
first — this file is the progress view, not the decision.

## Where it stands

**Phase 1 is complete.** Every public `recognise_*` family is owned by one orchestration.

| | |
|---|---|
| `MIGRATED` families | 17 |
| `DEFERRED` | **empty** |
| phase-0 guards | 3 of 3 green |

Measured per family, by counting calls at each recogniser's code object:

| path | families run |
|---|---|
| automatic prismatic build | 17, once each |
| automatic turned build | 14 (the three classification-gated excluded **by design**) |
| declared build | **0** |
| repeated `lint()` of a built drawing | **0** |

### Merged

| PR | issue | what |
|---|---|---|
| #1021 | #1019 | the fail-closed MIGRATED/DEFERRED manifest |
| #1027 | — | call counting by code object (`tests/conftest.py`) |
| #1029 | #1022 | a declared build recognises nothing |
| #1030 | #1026 | migrate the `BUILD_MODEL_ONLY` families |
| #1031 | #1025 | split step shoulders into evidence + per-consumer projection |
| #1033 | #1028 | gate classification-dependent families inside the orchestration |
| #1035 | #1013 | flats carry stock identity (`axis_line` + `stock_span`) |
| #1038 | — | correct the part-classification docstring claim |

### How each deferral ended

Each went when its stated constraint stopped being true, not when it came up on a list.
Every `Deferral` enum member survives for a future family; what did not survive was a
*deferral* justified by a constraint that had expired.

- **`BUILD_MODEL_ONLY`** (#1026) — #1022 removed the cost, so ADR 0017 completeness was
  reason enough alone.
- **`CALLER_SPECIFIC_INPUT`** (#1025) — it was a *shape* problem, not a fact about the
  feature. The scan never depended on the caller; only the filter did. Separating them gave
  the aggregate something single-valued to own. **Prefer that split before reaching for this
  reason again.**
- **`CLASSIFICATION_GATED`** (#1028) — the gate belonged *inside* the orchestration.
  **Owning a family and always running it are different things**: the aggregate owns
  `recognise_chamfers` and still does not run it for a turned part.

## What implementation taught the ADR

These are candidates for a single amendment. None invalidates the ADR; all four sharpen it.

1. **§2 may be over-specified.** It lists "flats on parallel or coaxial stock regions" as a
   conflict for a *named reconciliation stage*. #1013 solved it via ADR 0013's "fix the
   record" instead — two fields on `Flat` — and it was small, complete and testable. Some of
   §2's listed conflicts look like record-shape problems rather than reconciliation problems.
   Worth reconsidering before building the stage.

2. **§3 named a component #1013 omitted.** §3 specifies identity from "axis **direction** and
   in-plane position, axial extent". #1013 shipped *(axis letter, in-plane position, axial
   extent)*. The letter is not the direction, which is exactly the slanted-stock gap in
   #1036 — a knowing divergence from a written decision, not an unanticipated one.

3. **§3's "not a flat-only correction" — checked, and flats were the only live defect.**
   The observation is true of all five thin records, but only `Flat` produced a wrong
   drawing, because only `Flat` grouped *without a count*:

   | record | callout mechanism | two equal features on separate stock |
   |---|---|---|
   | `Flat` | grouped, no count | 1 callout for 2 definitions — the defect, fixed |
   | `Fillet` | grouped **with** `n×` | `2× R5` — documents both |
   | `Chamfer` | per-feature | two `C4` callouts |
   | `Groove` | per-feature | two callouts |
   | `CounterSink` | nested on its hole callout | documented by its hole |

   So strengthening the remaining records is an identity-model concern (§3's typed
   identities), **not an outstanding correctness bug**.

4. **§6 already answers the correspondence question; the implementation diverged from it.**
   §6 puts the lazily-obtained result in `BuildState` with a single writer, which makes
   provenance structural. `lint_prismatic_coverage(recognition=...)` is an injection channel
   outside that path — see #1032, which was originally mis-filed as an ADR gap and has been
   rewritten.

   Also: §6's phrase "without physical completeness critique" carries the same ambiguity that
   made #1022's acceptance need amending, because `export()` runs the critique. Worth
   clarifying in the same amendment.

## Next steps, in the order I would take them

1. **#1037 — derive the classification in the aggregate.** Move the recognition half of
   `analysis._classify_geometry` into `recognition/` so `RecognitionResult` derives
   `rotational` rather than receiving it. Verified as contained: single call site (`_analyse`),
   no layout coupling anywhere including the #222 horizontal-round-body fallback, bbox-only
   signature, and the move is *down* the DAG so no `_LAYERS` exemption is needed. One wrinkle:
   the trailing `_log.info(... skipping OD/centreline/bore annotations)` describes a drafting
   consequence — it should stay in `analysis` or be reworded, not carried down.

2. **The ADR amendment** covering the four points above. There is an argument for doing this
   *before* more phase-2 code, since §2 and §3 are what that code would be built against.

3. **Phase 2's typed identities** — `FeatureId` / `MeasurableId` / `RequirementId` /
   `AnnotationId` as distinct frozen types where cross-domain use fails rather than silently
   missing, plus "redetection of an unchanged solid produces identical identities".
   **Needs a scoping decision first:** one PR or split, and whether the public `Sheet` /
   emitter surfaces adopt the typed IDs or stay on plain strings. Unlike #1013 there is no
   user-visible defect driving it, and it touches every identity-carrying surface.

## Open, recorded rather than absorbed

| issue | what | note |
|---|---|---|
| #1032 | `lint_prismatic_coverage` takes an inventory it cannot vouch for | diverges from §6; fix is to remove the channel, not to build provenance machinery |
| #1034 | second A/F callout on parallel stock has no corner anchor | placement/ADR 0014; the loss is *reported*, not silent |
| #1036 | slanted flats do not render + their identity key is not canonical | filed together because fixing either alone is untestable |
| #1037 | part classification should be derived, not passed in | see next steps |

## Pre-existing open PRs, untouched by this epic

- **#1011** — flat A/F completeness. Draft, stalled. Carries `lint_flat_coverage`, which is
  why that function is not on `main`. **Affected by #1013:** when it lands, its coverage
  grouping must use `axis_line` + `stock_span`, or it reproduces the collapse on the lint side.
- **#1007** — per-feature completeness check, self-declared blocked on #1009.
- **#959** — redundant-dimension lint. Oldest, no recorded blocker.

**#1014** (flat recognition re-runs on every lint) is effectively resolved — both objections
that made it an issue are dissolved, and measurement confirms zero rescans. Left open pending
a decision to close it or fold the remainder into #1011.

## The recurring lesson, worth carrying into phase 2

**Seven guards written during this epic asserted more than they established. Every one was
caught by a mutation, by CI, or by an existing oracle — none by a green suite.**

Each measured a *proxy* for its claim:

- a module binding standing in for a function (`test_detect_once`, and the manifest's own
  orchestration guard);
- an empty result standing in for a skipped scan (the plates conjunction — `recognise_plates`
  naturally finds nothing on a shaft, so emptiness proved nothing);
- a parameter's existence standing in for non-narrowability;
- a whole-build call count standing in for one specific consumer;
- emitted script *text* standing in for a round-tripped *feature* — that one was in a test
  written to fix this very class of bug, and the #964 fidelity oracle caught it.

Running the mutation is the only thing that has caught any of them. **Write the mutation
before believing the guard.**
