# Product backlog roadmap

- **Status:** Active delivery plan
- **Last reviewed:** 2026-07-19
- **Live tracker:** [#758 — trustworthy drawing pipeline and backlog
  burn-down](https://github.com/pzfreo/draftwright/issues/758)

## Product promise

Given a part or caller-declared model, draftwright produces a complete,
deterministic drawing or clearly reports why it cannot. Work that undermines
completeness, parity, deterministic diagnosis, or truthful lint outranks new
surface area.

This document records delivery intent and prioritisation. ADRs remain the source
of architectural decisions; GitHub issues remain the source of live work status.
Historical issue state and completed-task ledgers do not belong here.

## Operating model

- At most four implementation issues are active, one in each workstream.
- An umbrella coordinates work but is never assigned as an implementation task.
- A bug that produces a clean but incomplete drawing outranks a visible failure.
- Every bug fix gains a reduced fast-tier fixture where practical.
- Work expected to exceed one reviewable PR is split before implementation.
- An issue stays in **Now** only with a named outcome, an owner, and an
  unblocked next action.
- Speculative features stay parked until they have a named user, milestone, or
  experiment.

Scheduling uses `roadmap:now`, `roadmap:next`, `roadmap:later`, and
`roadmap:parked`. Severity continues to use the existing `priority:P0`–`P3`
labels. Workstreams use `stream:*`; `blocked` records an external dependency or
decision that prevents progress.

## Workstreams and current WIP

| Workstream | Objective | Now | Next |
| --- | --- | --- | --- |
| Trust and correctness | Never certify or silently emit an incomplete drawing | [#632](https://github.com/pzfreo/draftwright/issues/632) truthful dimensional coverage | [#707](https://github.com/pzfreo/draftwright/issues/707) direct/generated Sheet parity |
| Reliability and diagnostics | Make failures fast, reproducible, and representative | [#692](https://github.com/pzfreo/draftwright/issues/692) deterministic Hypothesis generation | [#737](https://github.com/pzfreo/draftwright/issues/737) dense-sheet fast canaries |
| Architecture | Remove boundaries that make correctness work risky | [#754](https://github.com/pzfreo/draftwright/issues/754) rotational dimensions through planner output | [#752](https://github.com/pzfreo/draftwright/issues/752) typed recognition adapter registry |
| Manufacturing coverage | Expand independently verified feature coverage | No active implementation until a WIP slot clears | [#676](https://github.com/pzfreo/draftwright/issues/676) polygonal boss recognition |

The table is intentionally small. The live roadmap issue records ownership,
blockers, and session-to-session movement.

## Milestone 1 — Trustworthy 0.3.x

**Status: EXITED (2026-07-21, v0.3.6).** All initial scope delivered (#632, #707,
#630, #661, #631, #692, #737) and all four exit criteria met. The last gate — the
fast-tier flake — resolved on investigation: #669's two `test_make_drawing.py`
observations are non-reproducible on current `main` (byte-identity is tautological
since the strip-sizing routed through the annotation boxes; the pitch test clears
its boundaries by millimetres, not ULPs; 15 stress runs clean), so it was closed as
not-reproducible. Focus moves to Milestone 2.

### Outcome

Direct and generated paths agree semantically, lint cannot pass the known
incomplete fixtures, detail recovery behaves consistently, and fast CI failures
replay deterministically.

### Initial scope

- [#632](https://github.com/pzfreo/draftwright/issues/632) — truthful
  dimensional-completeness lint.
- [#707](https://github.com/pzfreo/draftwright/issues/707) — generated Sheet
  versus direct-build parity.
- [#630](https://github.com/pzfreo/draftwright/issues/630) and
  [#661](https://github.com/pzfreo/draftwright/issues/661) — consistent detail
  recovery on direct and edit/finalize paths.
- [#631](https://github.com/pzfreo/draftwright/issues/631) — declared steps do
  not silently lose defining dimensions.
- [#692](https://github.com/pzfreo/draftwright/issues/692),
  [#737](https://github.com/pzfreo/draftwright/issues/737), and
  [#669](https://github.com/pzfreo/draftwright/issues/669) — reproducible,
  representative, non-flaky fast feedback.

### Exit criteria

- Known under-dimensioned declarative fixtures cannot receive a clean lint
  result.
- Direct and emitted Sheet paths match on the agreed semantic signatures.
- Requested details either resolve or produce an explicit actionable failure.
- The fast CI tier has no known unreproducible or full-suite-only flake.

## Milestone 2 — Architectural closure

### Outcome

Authored dimension intent reaches output through explicit typed seams, and
package/test boundaries no longer encourage reach-through or cyclic ownership.

### Initial scope

- [#754](https://github.com/pzfreo/draftwright/issues/754) — rotational OD/bore
  dimensions consume planner output.
- [#746](https://github.com/pzfreo/draftwright/issues/746) — decorations can
  target one parameter of a multi-parameter kind.
- [#752](https://github.com/pzfreo/draftwright/issues/752) — typed recognition
  record adapter registry.
- [#523](https://github.com/pzfreo/draftwright/issues/523) — remove the
  builder/CLI/sheet-emitter import cycle.
- [#741](https://github.com/pzfreo/draftwright/issues/741) — reduce test-side
  private state reach-through in deliberate slices.

### Exit criteria

- Planner-supported authored decorations are observable in every applicable
  renderer.
- Recognition-to-model conversion has one typed, completeness-guarded dispatch
  seam.
- The builder/CLI/emitter import graph is acyclic.
- Test-side private reads have a shrink-only guard and a documented residual.

## Milestone 3 — Coverage expansion

### Outcome

Important manufacturing features are recognised or imported, lowered into
drafting concepts, rendered, and checked by independent coverage lint.

### Initial scope

- [#676](https://github.com/pzfreo/draftwright/issues/676) — polygonal boss
  recognition and across-flats/corners definition.
- [#675](https://github.com/pzfreo/draftwright/issues/675) — paired, grouped
  bilateral PMI tolerances.
- [#62](https://github.com/pzfreo/draftwright/issues/62) — AP242 GD&T/datum
  lowering.
- [#623](https://github.com/pzfreo/draftwright/issues/623) — PMI completeness
  reconciliation.

### Exit criteria

- The selected CTC/manufacturing fixtures expose the intended feature census.
- Imported semantics lower to the same IR used by declared concepts.
- A missing rendered feature is detected independently of the plan that should
  have produced it.

## Parked product expansion

These are valid product ideas, but they do not enter **Now** until Milestone 1's
trust criteria are met or a named user/experiment changes the priority:

- [#71](https://github.com/pzfreo/draftwright/issues/71) — shaded pictorial.
- [#276](https://github.com/pzfreo/draftwright/issues/276) — rich CLI/event
  stream.
- [#492](https://github.com/pzfreo/draftwright/issues/492) — assembly/GA mode.
- [#486](https://github.com/pzfreo/draftwright/issues/486) — published API
  reference.
- [#54](https://github.com/pzfreo/draftwright/issues/54) — advanced detail-view
  capabilities beyond the correctness work in Milestone 1.
- [#488](https://github.com/pzfreo/draftwright/issues/488) — replica-oriented
  downstream customisation beyond its already-landed foundations.

## Session protocol

At the start of a delivery session:

1. Read this roadmap and the latest handoff on the live tracker.
2. Verify the four **Now** slots against current `main` and issue state.
3. Resume the first unblocked issue; do not pull **Next** work while its stream's
   **Now** item remains active.

At the end of a delivery session, add this comment to the live tracker:

```markdown
## Session handoff — YYYY-MM-DD

Completed:
- #123 — outcome and PR

Evidence:
- tests, checks, or user-visible result

Current:
- #456 — state and blocker

Next:
1. #789
2. #790

Roadmap changes:
- why anything moved between Now, Next, Later, or Parked
```

Review the backlog weekly. Update this document only when product promise,
milestone scope, exit criteria, or operating rules change; record routine issue
movement on the live tracker.
