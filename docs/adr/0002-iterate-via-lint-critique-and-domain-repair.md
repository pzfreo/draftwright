# ADR 0002 — Iterate via lint critique and domain-semantic repair, not by editing generated code

- **Status:** Accepted
- **Date:** 2026-06-16
- **Deciders:** Paul Fremantle (pzfreo)

## Context

ADR 0001 establishes *what* draftwright optimises for: deterministic correctness
plus a domain-semantic edit surface, rather than a bespoke editable-code DSL.
This ADR records *how* a caller — human or, especially, an AI via the API — gets
from the automatic first output to a correct drawing.

The motivating observation (the one that opened this line of work): draftwright
produces good results when driven *interactively* — render → eyeball → read the
issues → adjust → re-render — but a one-shot API call underperforms. The
difference is not model quality; it is that the interactive operator gets
**laps** (a critique after each attempt) and reasons in the *domain*, whereas a
one-shot caller gets neither a critique nor a vocabulary it is fluent in.

So the design question is: what replaces the human's "eyeball → adjust" lap for a
non-interactive caller, without requiring that caller to be fluent in
draftwright's internals?

## Decision

Adopt an explicit **build → critique → domain-fix** loop as the supported
refinement model, with the lint system as the machine-readable critic.

1. **`build` — automatic first pass.** `build_drawing(...)` applies all
   deterministic intelligence and returns both the drawing *and* a critique
   (`lint_summary()`): `passed`, a coarse diagnostic score, separate quality components,
   severity counts, and per-issue records with domain-meaningful codes (`feature_not_dimensioned`,
   `callout_dropped`, `location_ref_dropped`, …). Because of ADR 0001's
   deterministic investment, the common case ends here — no iteration needed.

2. **`critique` — lint is the machine version of the eyeball lap.** When issues
   remain, they are surfaced two ways:
   - **Machine channel (primary for an AI):** `lint_summary()` codes name exactly
     what is wrong, in domain terms.
   - **Visual channel:** things lint cannot know (e.g. "the section should cut the
     other row") come from a human, or from an AI reading the **SVG** (a format
     models are genuinely fluent in).

3. **`domain-fix` — repair in domain vocabulary, never the layout DSL.** Each fix
   is expressed as domain intent — `dimension(feature=…)`, `section("A",
   through=…)` — not strip/zone/`dwg.at` mechanics. Then re-build and re-critique;
   loop until the relevant issue codes are resolved. The legacy scalar must not be an
   optimisation target: #1126 demonstrated that sparse authored input can improve it while
   making a geometry-only drawing less complete.

4. **Make the loop cheap to close.** Two mechanisms reduce the fix step to near
   review-only:
   - **Per-issue `suggestion` (#29):** supported lint issues can carry a computed
     domain-API call for a caller to review and apply. Suggestions are advisory;
     they are not a general executable repair program.
   - **lint→repair loop (#30):** automatic repair is deliberately allowlisted.
     Today `repair.py` handles only the mechanically clear `dim_inside_part`
     case; all other suggestions and judgement-bearing issues remain with the
     caller.

The loop is the API-side equivalent of the interactive laps: the engine gives the
first lap free, lint gives the critique, and the domain API + repair loop give the
same iterate-and-fix laps — with no fluency in draftwright internals required.

## Consequences

**Positive**
- A non-interactive caller gets the scaffolding an interactive operator has;
  this directly addresses the "API less impressive than interactive" gap.
- The critique contract (`lint_summary()`) is small, stable, and domain-shaped —
  a good integration point for external tools and agents.
- Cleanly separates concerns: deterministic quality shrinks step-1→step-2
  traffic; the loop handles the residue.

**Negative / costs**
- The loop is only as good as the critic: lint must keep growing toward
  "flags everything a competent reviewer would," including *never-attempted*
  coverage gaps, not just drops. Blind spots become silent failures.
- The legacy `score` is only a severity-weighted diagnostic heuristic. `diagnostic_score`
  exposes the same value under its honest name; callers should gate on severity/code counts
  and inspect `quality.completeness`, `quality.restraint`, and `quality.legibility` separately.
  Components state when their evidence is partial or unavailable, and no composite
  drawing-quality score is provided. Completeness is deliberately conditional on requirements
  Draftwright recognized and can audit; it reports that scope and any recognized-but-unscored
  families instead of treating unknown physical features as a reason to suppress the useful
  score (#1127).
- Completeness cannot infer that deleting an authored dimension was an engineering-correct
  redundancy decision. Such an omission remains a visible `suppressed` requirement and scores
  as unplaced. Counting every authored omission as complete would make the metric trivially
  gameable; an accepted waiver would need a distinct, explicit, reviewable provenance state.
- **Completeness is bounded by recognition, and its scalar says so in its own name.** The
  component reports `audited_score` — placed over *recognised and auditable* requirements — not
  `score`. A feature recognition never identified never became a requirement, so it is absent
  from the ledger rather than counted as missing, and a part with a recogniser gap can reach
  1.0. The qualifier lives in the field name because a caveat kept in a sibling key is lost the
  moment the number is quoted, and the primary consumer of this API is an agent summarising it.
  The block also emits `excludes` and an `unrecognised_geometry_reports` count (a floor on the
  gap, from the `unrecognised_defining_geometry` check — zero means nothing was *noticed*).
  **Neither is a completion gate**: closing the loop still means driving issue codes to zero,
  and closing a recogniser gap is recognition work, not a metric change.
- Requires building the domain-semantic API, suggestions, and repair loop
  (roadmap Cluster B / #25–#30) — the loop is aspirational until they exist.

**Neutral / follow-ups**
- Visual-channel issues remain outside lint by definition; SVG round-trip or a
  vision-capable reviewer is the path for those, not more lint codes.
- Keep lint codes domain-meaningful (not layout-internal) so suggestions and the
  repair loop stay expressible in the domain API.

## Current state vs target

*(Refreshed 2026-07-18 — the original "mostly unbuilt" roadmap here long predated
the build-out.)* The loop is **built**: the read/query API (#25–28 — `place_dim()`/
`features()`/`annotations()`/`view_bounds()`) plus the domain-semantic edit
verbs (`dimension()`/`callout()`/`locate()`/`drop()`, shipped under the
ADR 0010/0012 editable-surface work),
per-issue advisory suggestions (#29, `linting/suggest.py`), and a narrow
deterministic repair hook (#30, `repair.py`, with `Drawing.repair()` as the thin
wrapper) all shipped. The hook currently repairs only `dim_inside_part` and
does not execute arbitrary `Issue.suggestion` values. Repair remains the
*safety net*, not the primary placement mechanism — ADR 0014's
collect-then-solve contract is the structural cure for
the collision classes repair used to mop up.

## Related

- [ADR 0001](0001-deterministic-generation-over-editable-dsl.md) — the *what*
  this ADR's *how* depends on.
- `docs/plans/right-first-time-roadmap.md` — Cluster A (self-correction) and
  Cluster B (domain primitives) are the build-out of this loop.
- Issues #25, #26, #27, #28 (domain API), #29 (suggestions), #30 (repair loop),
  #32 (`lint_summary`, done).
