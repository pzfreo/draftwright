# Semantic fidelity and honest failure delivery plan

- **Window:** 2026-08-17 to 2026-10-01
- **Status:** Active
- **Live execution:** [Draftwright: Semantic Fidelity and Honest Failure](https://github.com/users/pzfreo/projects/3)
- **Outcome milestone:** [Semantic fidelity and honest failure](https://github.com/pzfreo/draftwright/milestone/5)
- **Strategy:** [Product backlog roadmap](product-backlog-roadmap.md)

## Objective

Preserve one semantic owner for every authored or imported manufacturing fact,
reject invalid declarations explicitly, and make view planning preserve supported
requirements before projection and placement.

By 1 October, the selected real and synthetic fixtures must either retain their
requirement multiset through lowering, view selection and placement, or report a
specific diagnostic naming where and why the requirement could not be satisfied.

## Product outcomes

1. View eligibility and assignment are computed against the selected view set,
   before rendering.
2. Removing a view re-homes every eligible requirement or fails closed; it never
   silently deletes manufacturing information.
3. The `Sheet` surface exposes typed authored view constraints without annotation
   coordinates or silently relaxed constraints.
4. AP242 tolerances lower into one canonical declarative owner, with ambiguous or
   unsupported records preserved explicitly.
5. Invalid declarations become inspectable omissions rather than crashes.

## Non-goals

- Do not infer manufacturing requirements from view availability.
- Do not expose raw feature-annotation coordinates; whole-view constraints remain
  distinct from the shared annotation solve.
- Do not accept a smaller sheet or different arrangement by trading one missing
  requirement for another.
- Do not use the milestone as a container for unrelated architecture or cleanup.

## Delivery sequence

The sequence is outcome-led. An iteration boundary is a review point, not a reason
to merge work that lacks evidence. Work that exceeds one reviewable PR is split
before implementation.

| Iteration | Primary outcome | Planned issues | Exit evidence |
| --- | --- | --- | --- |
| W3, 17-23 Aug | Make dimension planning view-set aware | [#1259](https://github.com/pzfreo/draftwright/issues/1259) | Requirements are assigned to the selected set or rejected before rendering |
| W4, 24-30 Aug | Close queued semantic-safety defects | [#924](https://github.com/pzfreo/draftwright/issues/924), [#1116](https://github.com/pzfreo/draftwright/issues/1116) | Zero-length declarations omit explicitly; AP242 hole tolerances have one owner |
| W5, 31 Aug-6 Sep | Expose authored view constraints | [#1260](https://github.com/pzfreo/draftwright/issues/1260) | `Sheet` verbs build typed constraints and never relax them silently |
| W6, 7-13 Sep | Preserve requirements across view removal | [#1261](https://github.com/pzfreo/draftwright/issues/1261) | The three-view A2 candidate retains the four-view requirement multiset |
| W7, 14-20 Sep | Select semantic view sets automatically | [#1262](https://github.com/pzfreo/draftwright/issues/1262) | Rotational, asymmetric, GRM-01 and GRM-04 fixtures choose or refuse complete plans |
| W8-W9, 21 Sep-1 Oct | Close evidence gaps and release | Pull only after the W7 gate | Milestone evidence and an explicit release decision are recorded |

## Control points

Advance from one child to the next only when:

- the selected requirement multiset is stable and independently compared;
- ambiguity fails closed using semantic identity, never labels or page geometry;
- targeted mutations prove assignment, omission and placement guards are
  load-bearing;
- the applicable fast, slow and hosted tiers are green; and
- the completed child is closed with linked acceptance evidence and reconciled
  Project state.

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

- **Current Delivery:** open Now and Next work, board by status.
- **Eight-Week Roadmap:** committed items on the iteration timeline.
- **Triage:** items missing a horizon.
- **Trust Bugs:** open correctness work in `stream:trust`.
- **Outcome Milestone:** the active semantic-fidelity outcome milestone.
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
items to Done, add linked sub-issues, and complete merged PRs. The complete open
backlog was reconciled on 2026-08-21. Until repository-wide top-level auto-add is
enabled in the Project UI, new top-level issues still require explicit addition;
the weekly audit fails if any are omitted.

## Risks and responses

| Risk | Response |
| --- | --- |
| Architecture work resumes without a user-visible defect | Keep it in Later until an issue supplies the failing fixture and acceptance evidence |
| Recognition gains names unsupported by B-rep evidence | Separate geometric inventory from declared manufacturing semantics; require rejection mutations |
| Slow CI lengthens every review loop | Keep reduced fixtures in the fast tier and run the slow tier at PR and release gates |
| The Project becomes another stale tracker | One live source, weekly audit, WIP limits, and status updates; no duplicate session ledger |
| Scope expands past one maintainer's capacity | Pull only from Next when a Now slot clears; replan rather than carry hidden WIP |

## Decision log

- **2026-08-05:** ADR 3 (was 0017) phase 1 is treated as evidence to test, not automatic
  authority to execute phases 2-6.
- **2026-08-07:** GitHub Project 3 replaces issue #758 as the live execution layer.
  Repository plans retain strategy, outcome gates, and durable decisions.
- **2026-08-07:** The former Coverage expansion milestone is consolidated into one
  time-bound trust-and-coverage outcome so correctness remains ahead of breadth.
- **2026-08-10:** Trustworthy manufacturing drawings closed early with all 17
  scoped issues complete. Semantic fidelity and honest failure became the active
  outcome.
- **2026-08-21:** Reconciled every open issue into Project 3, retired the stale W2
  queue, split ADR 2 (was 0018) delivery into #1259-#1262, and made #1259 the sole W3 Now
  item. Current Delivery now excludes closed cards and the milestone view targets
  milestone 5.
