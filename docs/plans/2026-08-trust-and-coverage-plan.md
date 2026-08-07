# Trust and coverage delivery plan

- **Window:** 2026-08-03 to 2026-10-02
- **Status:** Active
- **Live execution:** [Draftwright: Trustworthy Manufacturing Drawings](https://github.com/users/pzfreo/projects/3)
- **Outcome milestone:** [Trustworthy manufacturing drawings](https://github.com/pzfreo/draftwright/milestone/4)
- **Strategy:** [Product backlog roadmap](product-backlog-roadmap.md)

## Objective

Make draftwright trustworthy before making it broader: prevent known silent
incompleteness, prove the fixes with independent evidence, then expand
manufacturing coverage through the same compiler and lint contracts.

By 2 October, the selected real and synthetic fixtures should either produce a
complete drawing or a specific diagnostic for every known unsupported defining
feature. A clean lint result must never be the consequence of failed recognition.

## Product outcomes

1. The current duplicate, missing, and unlocated-dimension defects are fixed and
   mutation-guarded.
2. Polygonal bosses reach recognition, drafting IR, annotation, and independent
   coverage lint without claiming unsupported manufacturing semantics.
3. AP242 PMI records are reconciled from source through lowering and rendering;
   missing or fragmented output is diagnosed independently.
4. The editable declared surface preserves source identity where it can do so
   unambiguously.
5. Users can find current API documentation, and accumulated releasable work does
   not sit on `main` without an explicit weekly release decision.

## Non-goals

- Do not turn ADR 0017's unproven later phases into a programme without a driving
  defect or experiment.
- Do not infer gear standards, intent, or parameters from suggestive geometry.
- Do not expand annotation placement or declaration APIs by bypassing the shared
  solve.
- Do not use the milestone as a container for unrelated cleanup.

## Delivery sequence

The sequence is outcome-led. An iteration boundary is a review point, not a reason
to merge work that lacks evidence. Work that exceeds one reviewable PR is split
before implementation.

| Iteration | Primary outcome | Planned issues | Exit evidence |
| --- | --- | --- | --- |
| W1, 3-9 Aug | Stabilise and release | [#1067](https://github.com/pzfreo/draftwright/issues/1067) | Full local and hosted tiers green; v0.4.1 installed from the registry |
| W2, 10-16 Aug | Remove known false/duplicate completeness output | [#958](https://github.com/pzfreo/draftwright/issues/958), [#1000](https://github.com/pzfreo/draftwright/issues/1000) | Reduced fixtures and adversarial mutations fail before each fix and pass after it |
| W3, 17-23 Aug | Make turned-height placement truthful | [#955](https://github.com/pzfreo/draftwright/issues/955), [#1004](https://github.com/pzfreo/draftwright/issues/1004) | Overall height is suppressed only when the surviving chain measures it |
| W4, 24-30 Aug | Establish polygonal-boss evidence | [#676](https://github.com/pzfreo/draftwright/issues/676) | Recognition record and rejection mutations prove what geometry establishes |
| W5, 31 Aug-6 Sep | Complete polygonal-boss vertical slice | [#676](https://github.com/pzfreo/draftwright/issues/676); discovery [#1062](https://github.com/pzfreo/draftwright/issues/1062) | Across-flats/corners definition renders and missing output lints independently; gear discovery ends in a decision |
| W6, 7-13 Sep | Reconcile source PMI | [#623](https://github.com/pzfreo/draftwright/issues/623) | Source, lowered, rendered, missing, and suppressed outcomes are distinguishable |
| W7, 14-20 Sep | Lower selected AP242 PMI | [#62](https://github.com/pzfreo/draftwright/issues/62), [#675](https://github.com/pzfreo/draftwright/issues/675) | Values and references lower to shared IR; paired tolerances render as one semantic requirement |
| W8, 21-27 Sep | Preserve declared source identity | [#1041](https://github.com/pzfreo/draftwright/issues/1041) | Unique matches emit references; ambiguous matches fail safe to numeric declarations |
| W9, 28 Sep-2 Oct | Document and release the outcome | [#846](https://github.com/pzfreo/draftwright/issues/846) | Browsable API reference is published; milestone evidence and release decision recorded |

## Control points

Continue from trust fixes into coverage only when:

- the fast and slow tiers are green on `main`;
- the selected defect mutations prove each guard is load-bearing;
- no known fixture receives a clean lint result after recognition loses a defining
  feature; and
- release v0.4.1 has either shipped or has a recorded blocker and owner.

Continue from polygonal bosses into AP242 PMI only when the new feature has one
owned recognition path, semantic measurement identity, and independent coverage
critique. Do not accept emitted text or an empty recognition result as evidence.

## Project operating model

The GitHub Project is the source of truth for live execution state. This document
changes only when outcomes, sequence, gates, or operating rules change.

### Fields

- `Status`: Backlog, Ready, In progress, In review, Blocked, Done.
- `Horizon`: Now, Next, Later, Parked.
- `Iteration`: weekly, 3 August through 2 October.
- `Size`: S, M, or L; split work larger than L before it enters Now.
- Native `Assignees`, `Milestone`, and `Labels` retain ownership, outcome, priority,
  area, and workstream metadata.

### Views

- **Current Delivery:** Now and Next, board by status.
- **Eight-Week Roadmap:** committed items on the iteration timeline.
- **Triage:** items missing a horizon.
- **Trust Bugs:** open correctness work in `stream:trust`.
- **Coverage Milestone:** the active outcome milestone.
- **Parked:** explicitly deferred product expansion.

### WIP and entry rules

- Keep at most two delivery items and one bounded discovery in Now.
- An item enters Now only with an owner, iteration, acceptance evidence, and an
  unblocked next action.
- Next holds no more than five validated follow-ons. Later is the default for
  accepted backlog. Parked requires a reason to resume.
- `Blocked` requires a named dependency or decision and is reviewed after seven
  days. A blocked item does not retain a scarce Now slot by default.
- Close an issue only when its acceptance evidence is linked. Done is automated
  from closure; merged PRs are not a substitute for closing the outcome issue.

### Cadence

- **Daily while delivering:** move status and link the implementing PR.
- **Friday:** run `scripts/project-audit`, review blocked work and WIP, choose the
  next iteration, and record a Project status update.
- **At each iteration boundary:** decide whether to release; record the decision
  even when no release is cut.
- **At milestone close:** compare the product outcome with evidence, not issue
  count, and record any scope change here.

`scripts/project-audit` requires `gh` authenticated with a classic token carrying
Project read access. GitHub's user-owned Project item endpoint does not accept the
repository `GITHUB_TOKEN`, so the audit is a named weekly maintainer control rather
than a scheduled Action that would silently skip or require a broad stored token.

GitHub's native Project workflows set newly added items to Backlog, move closed
items to Done, add linked issues, and complete merged PRs. Initial backlog import
is complete. New top-level issues still require triage into the Project; the Triage
view and weekly audit make omissions visible.

## Risks and responses

| Risk | Response |
| --- | --- |
| Architecture work resumes without a user-visible defect | Keep it in Later until an issue supplies the failing fixture and acceptance evidence |
| Recognition gains names unsupported by B-rep evidence | Separate geometric inventory from declared manufacturing semantics; require rejection mutations |
| Slow CI lengthens every review loop | Keep reduced fixtures in the fast tier and run the slow tier at PR and release gates |
| The Project becomes another stale tracker | One live source, weekly audit, WIP limits, and status updates; no duplicate session ledger |
| Scope expands past one maintainer's capacity | Pull only from Next when a Now slot clears; replan rather than carry hidden WIP |

## Decision log

- **2026-08-05:** ADR 0017 phase 1 is treated as evidence to test, not automatic
  authority to execute phases 2-6.
- **2026-08-07:** GitHub Project 3 replaces issue #758 as the live execution layer.
  Repository plans retain strategy, outcome gates, and durable decisions.
- **2026-08-07:** The former Coverage expansion milestone is consolidated into one
  time-bound trust-and-coverage outcome so correctness remains ahead of breadth.
