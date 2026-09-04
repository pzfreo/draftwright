# ADR consolidation plan

*Status: implemented 2026-09-04 in one PR, as specified below. Kept as the record of why.*

## Decisions this plan is built on

Four answers, taken 2026-09-04:

1. The records are organised around **five core aspects**: the compiler pipeline, sheet
   layout and view planning, the recognition boundary, declared intent, and trust / honest
   failure. Layout was originally folded into the pipeline; ADR 2 (was 0018) is important enough to
   stand, and once it does, 0004 and 0014 belong beside it rather than in the pipeline record.
2. The audience is **AI agents first**. Every session reads these before changing anything, so
   they must be short, current, and load-bearing — statements an agent must not violate, each
   with the test that proves it. Narrative and history go elsewhere.
3. History is **archived, not deleted**: superseded records and collapsed amendment trails move
   to `docs/adr/archive/`, out of the reading path but still linkable.
4. Going forward an ADR changes **only when a stated boundary or invariant changes, and only
   with the maintainer's sign-off** before any text is written.

## Diagnosis

Measured against `main` at `2fed6e46`.

| | |
| --- | --- |
| ADR files | 21 (17 accepted, 1 proposed, 3 superseded/retired) |
| Total lines | 8,787 |
| Lines in superseded/retired files still in the live folder | 1,611 (0003, 0008, 0009) |
| Six largest files | 0016 (1,428), 0017 (1,144), 0009 (845), 0018 (770), 0014 (587), 0008 (491) — 73% of the total |
| Amendments on 0017 | 28, against the index's own rule of "at roughly four, write a successor" |
| Overlapping architecture prose outside `adr/` | `architecture.md` 638, CLAUDE.md 273, AGENTS.md 137, `target-architecture.md` 106 |
| Roadmaps in `docs/plans/` | 10, several referencing ADRs 0008/0009/0013 by their pre-supersession numbers |

Three findings the numbers make plain.

**0017 is a changelog wearing an ADR badge.** Most of its 28 amendments record one provider
version adopted or one feature family's ownership captured. Each is a PR body. The cause is
structural: CLAUDE.md says "amend the ADR in the same PR", so every session amends and none
removes. The index's four-amendment rule exists and was not followed, because nothing enforces it.

**The dead records are the most cited.** Citations from `src/`, `tests/`, CLAUDE.md, AGENTS.md
and the non-ADR docs: superseded 0009 has **103**, superseded 0008 has **70** — more than most
live records (0015, which supersedes 0008, has 30). The why-trail in the codebase points at
documents the index tells a reader to skip.

**The ADRs are the agent's operating manual, and it is 8,800 lines long.** CLAUDE.md's
instruction is "READ `docs/adr/` FIRST". At that size an agent skims, and a skimming agent
adds a paragraph rather than taking one away. The growth is a consequence of the size.

## Target shape

Five live records, one per core aspect, plus the index. Everything else is archive.

```
docs/adr/
  README.md                       index + the rules of engagement (≈60 lines)
  0001-compiler-pipeline.md
  0002-sheet-layout-and-view-planning.md
  0003-recognition-boundary.md
  0004-declared-intent.md
  0005-trust-and-honest-failure.md
  archive/
    0001-… 0020-…                 frozen, filenames unchanged, status header rewritten to
                                  "Archived; current decision in ADR n §m"
```

The numbers restart at 0001. The archive keeps its filenames, so every existing link into an
old record still resolves; the 173 code and test citations of 0008/0009 are repointed in the same
change, so no live text ever refers to an old number. The one residual cost is that a historical
commit message saying "ADR 4 (was 0001)"–"ADR 1 (was 0005)" is ambiguous between the old record and the new one
with that number; the archived file's status header says which it was.

### What one live record contains

Each is capped at **200 lines** and has exactly these sections:

1. **Decision** — the position, in the present tense, in under a page. No history, no "we
   considered". If it takes longer than a page to say, it is two decisions.
2. **Invariants** — a numbered list. Each is one sentence an agent must not violate, followed by
   the test that fails if it is violated. An invariant with no test is listed under "Unguarded"
   with an issue number, never silently mixed in. This section is the reason the document exists.
3. **Boundaries** — one short paragraph per neighbouring record: what this one owns, what it
   explicitly leaves to the other. This replaces the current web of cross-citations (every ADR
   cites four to nine others today).
4. **Superseded** — one line per prior decision this record replaces, pointing into the archive.
   The amendment trail compresses here: "Amendment 24 embedded a snapshot in generated Python;
   reversed by Amendment 28 — evidence is a sidecar." One line. The archive has the rest.
5. **Open** — decisions deliberately not taken yet, each with its gating evidence or issue.

No "Context", no "Consequences", no "Rejected alternatives" in the live record. Those are the
narrative an agent does not need to act correctly, and they are exactly what the archive keeps.

### Mapping of current records

From the index's own one-line decisions; the mapping is confirmed record by record during
execution, not assumed from this table.

| Current | Goes to | Notes |
| --- | --- | --- |
| 0001 deterministic generation over editable DSL | ADR 4 declared intent | Its "no imperative editable script" invariant is declared intent's founding rule. |
| 0002 lint critique and domain repair | ADR 5 trust | Repair is a safety net, not a design tool — a trust invariant. |
| 0003 constraint-based layout | archive only | Already retired; nothing to carry. |
| 0004 compose-then-pack | ADR 2 layout | Outer layout. 0018 already supersedes its fixed-topology assumption; carry the surviving half. |
| 0005 module boundaries + build-state ownership | ADR 1 pipeline | The DAG and single-owner `BuildState`. Its guard tests are the model for every invariant. |
| 0006 pinned fonts | ADR 5 trust | One invariant: deterministic text measurement. Three lines. |
| 0007 draftwright owns recognition and linting | ADR 3 recognition | The ownership split with helpers and the recogniser package. |
| 0008 feature IR + planner | archive only | Superseded by 0015; 70 code citations to repoint. |
| 0009 boundary labeling | archive only | Superseded by 0014; 103 code citations to repoint. |
| 0010 annotation provenance seam | ADR 5 trust | Provenance recorded once, at one seam. |
| 0011 IR as public input | ADR 4 declared intent | The `Sheet` façade and declare-don't-only-detect. |
| 0012 edits as ranked corridor candidates | ADR 4 declared intent | Editing model; its placement half references ADR 2. |
| 0013 uniform recogniser contract | ADR 3 recognition | The fail-closed capability join. |
| 0014 collect-then-solve placement | ADR 2 layout | Inner placement. Largest surviving block; the invariants are few, the prose is not. |
| 0015 compiler as built | ADR 1 pipeline | The IR waist. This is ADR 1's spine. |
| 0016 declared dimensioning intent | ADR 4 declared intent | 1,428 lines; its invariants fit on one page. Amendment 6's converse rule is one of them. |
| 0017 one recognition result per run | ADR 3 recognition (lifecycle, ownership, contracts) and ADR 5 trust (reports, evidence, sidecars) | The split is the point: 0017 grew because it held both. 28 amendments collapse to roughly six lines. |
| 0018 requirement-driven view planning | ADR 2 layout — its spine | Accepted, partly built. The record states which invariants hold today and lists the rest under **Open** with their gating evidence, so the design is kept whole while the record stays honest about what is enforced. |
| 0019 display-complete labels (proposed) | ADR 4 declared intent, under **Open** | A proposal is an open item, not a record. |
| 0020 provider-owned frame boundary | ADR 3 recognition | Raw vs framed; typed refusal; no fallback. |

### The other architecture prose

- `docs/target-architecture.md` (106 lines) becomes the **Decision** section of ADR 1 and is
  deleted.
- `docs/architecture.md` (638 lines) is cut to the module-by-module map only — the part
  `test_import_boundaries.py` keeps honest — and loses its per-ADR status commentary, which is
  what the five records now are.
- CLAUDE.md's ADR index paragraph shrinks to four lines, and "READ `docs/adr/` FIRST" becomes
  "READ the five live records; the archive is history".
- `docs/plans/` is out of scope for this plan except to note that the ten roadmaps predate
  most of the supersessions and need their own audit.

## Rules of engagement (goes into README.md)

These replace the current maintenance rules and are the part that stops the regrowth.

1. **An ADR changes only when a boundary or invariant changes.** Adopting a provider version,
   adding a feature family, recording ownership for one more record type, adding a report field:
   PR body. If the PR does not change the Invariants section, it does not touch the ADR.
2. **No ADR text is written without the maintainer's sign-off.** An agent or contributor who
   believes a change is needed says so in the PR in two sentences and waits. A reviewer
   recommending an ADR is a recommendation, not an authorisation.
3. **Each live record is capped at 200 lines and the five together at 1,000.** Enforced by a
   test, not a convention — the four-amendment rule was a convention.
4. **No live document cites an archived record.** Code comments, tests and docs cite ADR 1–5 or
   a specific test name. Enforced by a test.
5. **Every invariant names its guard.** An invariant without one is listed as unguarded with an
   issue, and the list of unguarded invariants is expected to shrink, never grow.

## Execution

**One PR.** Nothing in it changes behaviour, so there is no running system to protect with
small steps — and the thing under review is the coherence of five records that reference each
other, which cannot be judged one record at a time. Interleaving old and new records across
several merges would leave `main` in states where neither set is authoritative.

The PR contains, in this order of drafting:

1. `docs/adr/archive/` with all twenty current records moved in, filenames unchanged, each
   status header rewritten to point at its successor section.
2. The five records, drafted in the order recognition → trust → declared intent → layout →
   pipeline. Recognition first because 0017 is the worst case and settles the method; pipeline
   last because its Boundaries section depends on the other four being settled. Each draft is
   reviewed before the next begins — that is where the sign-off rule bites — but nothing merges
   until all five stand together.
3. The 173 citations of 0008/0009 in code and tests repointed to ADR 1 or 2 or to the guard test
   they actually mean; the remaining citations of 0001–0020 likewise.
4. `test_adr_corpus.py` with the size cap and no-archive-citations guards, passing.
5. README rules of engagement; CLAUDE.md and AGENTS.md pointers; `target-architecture.md`
   deleted; `architecture.md` cut to the module map.

Each record is written by reading its source records in full — not the index — extracting every
sentence that states an invariant, and checking each one against a test by running it. An
invariant that turns out to have no test, or a test that passes for a different reason, is
recorded as unguarded rather than carried forward as if it were enforced. Given this repo's
history the number of those is expected to be non-trivial and is itself a finding of the PR.

## Verification

- `test_adr_corpus.py` (new): each live record ≤ 200 lines; the five together ≤ 1,000; every
  `## Invariants` entry names a test that exists in `tests/`; nothing under `src/`, `tests/`,
  or `docs/` outside `archive/` cites an ADR number that lives in `archive/`.
- `mkdocs build --strict` stays green through every PR.
- Before merge, a fresh agent session is given only CLAUDE.md and the five records and asked
  to make a small change in each of the five areas. If it needs to open the archive to act
  correctly, the live record is missing an invariant.

## Open questions

1. **What is the 200-line cap measured against?** Proposal: prose lines excluding the
   Superseded and Open sections, so history pointers and honest gaps never compete with
   invariants for space.
2. **Who drafts the five records?** It is judgement-heavy — deciding what is an invariant and what is
   description is the whole task. An agent can draft; the maintainer has to decide. The plan
   assumes a draft-then-review loop per record inside the one PR, with the sign-off rule
   applying to every line.
