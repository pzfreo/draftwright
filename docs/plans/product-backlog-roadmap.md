# Product backlog roadmap

- **Status:** Active product strategy
- **Last reviewed:** 2026-08-07
- **Live execution:** [Draftwright: Trustworthy Manufacturing
  Drawings](https://github.com/users/pzfreo/projects/3)
- **Current delivery plan:** [Trust and coverage, August-October
  2026](2026-08-trust-and-coverage-plan.md)

## Product promise

Given a part or caller-declared model, draftwright produces a complete,
deterministic drawing or clearly reports why it cannot. Work that undermines
completeness, parity, deterministic diagnosis, or truthful lint outranks new
surface area.

This document records the product promise and durable prioritisation rules. ADRs
remain the source of architectural decisions, the dated delivery plan records the
current outcome and gates, and the GitHub Project is the source of live work
status. Historical issue state and completed-task ledgers do not belong here.

## Operating model

- At most two delivery issues and one bounded discovery are in **Now**.
- An umbrella coordinates work but is never assigned as an implementation task.
- A bug that produces a clean but incomplete drawing outranks a visible failure.
- Every bug fix gains a reduced fast-tier fixture where practical.
- Work expected to exceed one reviewable PR is split before implementation.
- An issue stays in **Now** only with a named outcome, an owner, and an
  unblocked next action.
- Speculative features stay parked until they have a named user, milestone, or
  experiment.

Scheduling uses the Project's `Horizon` and `Iteration` fields. Severity continues
to use the existing `priority:P0`-`P3` labels. Workstreams use `stream:*`;
`Blocked` records an external dependency or decision that prevents progress. The
former `roadmap:*` labels are retired after migration because duplicate scheduling
state is a drift source.

## Workstreams and current WIP

| Workstream | Objective | Now | Next |
| --- | --- | --- | --- |
| Trust and correctness | Never certify or silently emit an incomplete drawing | [#958](https://github.com/pzfreo/draftwright/issues/958) duplicate pad/phantom-slot span | [#1000](https://github.com/pzfreo/draftwright/issues/1000), [#955](https://github.com/pzfreo/draftwright/issues/955), [#1004](https://github.com/pzfreo/draftwright/issues/1004) |
| Reliability and diagnostics | Make failures fast, reproducible, and representative | [#1067](https://github.com/pzfreo/draftwright/issues/1067) v0.4.1 and weekly release decision | No additional item until a Now slot clears |
| Architecture | Remove boundaries only when they block a product outcome | No active implementation | Pull only from a failing product slice |
| Manufacturing coverage | Expand independently verified feature coverage | No active implementation until the trust gate clears | [#676](https://github.com/pzfreo/draftwright/issues/676), then [#623](https://github.com/pzfreo/draftwright/issues/623) |

The table is intentionally small. The Project records ownership, blockers, and
movement; the dated delivery plan records the multi-week sequence and gates.

## Milestone 1 — Trustworthy 0.3.x

**Status: EXITED (2026-07-21, v0.3.6).** All initial scope delivered (#632, #707,
#630, #661, #631, #692, #737) and all four exit criteria met. The last gate — the
fast-tier flake — resolved on investigation: #669's two `test_make_drawing.py`
observations are non-reproducible on current `main` (byte-identity is tautological
since the strip-sizing routed through the annotation boxes; the pitch test clears
its boundaries by millimetres, not ULPs; 15 stress runs clean), so it was closed as
not-reproducible. Focus moves to Milestone 2.

Off-milestone quality polish shipped afterwards in **v0.3.7** (turned ⌀ leader
placement — centre-on-feature, the `leader_crosses_silhouette` lint, and
clear-side re-routing of body-crossing leaders); it did not change milestone scope
or exit criteria. See the archived tracker (#758) for the delivered ledger.

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

**Status: EXITED (2026-07-23).** All five scope items delivered (#754, #746, #752,
#523, #741), each through independent adversarial review, and all four exit criteria
met. The follow-on state-bus endgame (#830) subsequently reached its designed end
state: the engine no longer mutates build state onto a live `Drawing` outside a named
two-method layout seam, which the guard now enforces as an allowlist. Focus moves to
Milestone 3.

Off-milestone work shipped alongside in **v0.3.8** (the #817 placement-API
privatisation, obround through-slot recognition) and **v0.3.9** (pocket/slot pattern
kinds, `Sheet.section()`/`.detail()`, external threads, blind obround pockets); none
of it changed milestone scope or exit criteria.

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

## Active milestone — Trustworthy manufacturing drawings

**Target: 2026-10-02.** The former open-ended Coverage expansion milestone is
consolidated into this time-bound outcome. Correctness fixes lead; manufacturing
coverage follows only after the trust gate in the current delivery plan.

### Outcome

Known silent or misleading incompleteness is removed, then selected manufacturing
features are recognised or imported, lowered into drafting concepts, rendered,
and checked by independent coverage lint.

### Initial scope

- [#958](https://github.com/pzfreo/draftwright/issues/958),
  [#1000](https://github.com/pzfreo/draftwright/issues/1000),
  [#955](https://github.com/pzfreo/draftwright/issues/955), and
  [#1004](https://github.com/pzfreo/draftwright/issues/1004) — current trust
  defects and their measurement-identity enabler.
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

These are valid product ideas, but they do not enter **Now** until a named
user, milestone, or experiment changes the priority (Milestones 1 and 2 have
both exited):

- [#71](https://github.com/pzfreo/draftwright/issues/71) — shaded pictorial.
- [#276](https://github.com/pzfreo/draftwright/issues/276) — rich CLI/event
  stream.
- [#492](https://github.com/pzfreo/draftwright/issues/492) — assembly/GA mode.
- [#486](https://github.com/pzfreo/draftwright/issues/486) — published API
  reference (umbrella; its concrete deliverable
  [#846](https://github.com/pzfreo/draftwright/issues/846), a mkdocs/mkdocstrings
  site, is flagged by the maintainer as a priority and is a scheduling candidate,
  not parked).
- [#54](https://github.com/pzfreo/draftwright/issues/54) — advanced detail-view
  capabilities beyond the correctness work in Milestone 1.
- [#488](https://github.com/pzfreo/draftwright/issues/488) — replica-oriented
  downstream customisation beyond its already-landed foundations.

## Session protocol

At the start of a delivery session:

1. Read the current delivery plan and open the Project's Current Delivery view.
2. Verify the three **Now** slots against current `main` and issue state.
3. Resume the first unblocked issue; do not pull **Next** work while its stream's
   **Now** item remains active.

At the end of a delivery session, update issue and Project status directly. On
Friday, run `scripts/project-audit` and publish a short Project status update with
completed evidence, current blockers, the next iteration, and any scope decision.

Review the backlog weekly. Update this document only when the product promise or
operating rules change; update the dated plan when its outcome, sequence, gates,
or milestone scope changes.
