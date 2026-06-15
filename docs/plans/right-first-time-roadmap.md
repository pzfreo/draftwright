# "Right first time" roadmap — hardening the deterministic core

_Status: living plan. Last updated alongside PR #33 (#32)._

## Why this exists

draftwright gets good results when driven interactively (Claude Code): render →
eyeball → read lint → adjust → re-render. The same engine driven one-shot via an
API underwhelms — not because of the model, but because the API call doesn't get
those laps. The fix is two-pronged and both prongs meet at `lint()`:

1. **Push the deterministic boundary outward** so fewer decisions need an LLM at
   all (the engine self-corrects).
2. **Give a non-interactive caller the same scaffolding a human has** — the lint
   critic as a machine-readable signal, plus the primitives to act on it.

Treat "the API gives less impressive results" as a signal pointing at *engine
gaps*, not a prompt-quality problem. Every gap closed in Python is permanent and
free per run; every gap left to the API is paid for, non-deterministic, and
worse on novel input.

## The two clusters of open issues

The open issues are the same goal approached from two ends, meeting at lint:

**Cluster A — make the engine self-correcting**
- #31 — derive bare layout constants (remove the generalization tax)
- #32 — lint score + surface silent annotation drops  ← **in progress (PR #33)**
- #30 — lint→repair loop (act on violations, don't just report)
- #13 — tests that pin the *general* behaviour (the verification layer)

**Cluster B — primitives so a script/LLM can fix things**
- #26 — `dwg.features(view)` — expose the geometry analysis
- #25 — `Drawing.place_dim()` — layout-strip-aware stacking dimension
- #29 — lint findings carry a `suggestion` code snippet
- #27 — `dwg.annotations()` — query placed annotations
- #28 — `dwg.view_bounds(view)` — page bbox of a projected view

## How they depend on each other

- **#32 is the foundation.** It turns lint into structured output (`lint_summary()`)
  and makes every layout failure a machine-readable lint code instead of a log
  line. Everything downstream consumes this.
- **#29 extends #32 directly** — a `suggestion` field is just another key on the
  issue dict `lint_summary()` already emits. A computable suggestion *is* a
  repair recipe, so #29 is the bridge from #32 (surface) to #30 (auto-apply).
- **#26 and #25 are prerequisites for #30**, not just neighbours:
  - repairing `feature_not_dimensioned` / `callout_dropped` needs the feature
    geometry back → `dwg.features()` (#26)
  - repairing `annotation_overlap` needs strip re-stacking → `place_dim` (#25)
  Both also stand alone as API wins, so they are low-regret to land early.
- **#13 verifies #31 and #32 together** — its "4+ bores: all annotated or
  overflow surfaced via lint" assertion is only expressible because of #32's drop
  codes; its cap/threshold cases are exactly what #31 must derive. Land #13's
  tests *with* #31 so the overfitting #31 removes can't creep back.

## Recommended sequence

```
#31 + #13      constants + the tests that pin general behaviour
   ↓
#26, #25       primitives (features, place_dim) — also standalone API wins
   ↓
#29            lint suggestions, building on #32's issue dict
   ↓
#30            repair loop, consuming features + place_dim + suggestions
```
#27 / #28 slot in opportunistically wherever an API caller needs them.

## Done / in progress

### #32 — lint score + surface silent drops (PR #33)
- `Drawing.lint_summary()` — JSON-friendly aggregate of `lint()`: `passed`,
  coarse 0–1 `score`, severity counts, `by_code`, `geometry_issues`, full issue
  list. One signal to gate/optimise on without rendering.
- `Drawing._record_build_issue()` records build-time drops; `lint()` surfaces them.
- New drop codes: `callout_dropped` (per-view cap), `location_ref_dropped`
  (per-part cap), `placement_unsatisfiable` (no room / strip full),
  `step_dim_dropped` (step_zs[:3] cap). No layout change — only surfacing.
- Score weights (`_SCORE_ERROR_PENALTY`, `_SCORE_WARNING_PENALTY`) and the
  geometry-aware code set (`_GEOMETRY_AWARE_CODES`) are single-sourced module
  constants, consistent with #31's intent.

## Notes / gaps to keep in mind

- The lint *score* is itself a heuristic with tunable weights. It's named and
  single-sourced, but the severity/code counts are the authoritative output —
  callers should prefer counts over the scalar where it matters.
- Full location-dimension *coverage* lint (flagging under-located parts that were
  never even attempted) is still out of scope — #32 surfaces the *drops*, not the
  never-attempted gaps. Candidate follow-up under #13/#30.
- `_MAX_CALLOUTS_PER_VIEW=4`, `_MAX_LOCATION_REFS=4`, `step_zs[:3]` remain
  uncalibrated caps — surfaced now (#32), to be *derived* under #31.
- Naming drift: #29's examples say `LintFinding`; the class is `LintIssue`. Fix
  when #29 is implemented.
