# "Right first time" roadmap — hardening the deterministic core

_Status: living plan. Last updated 2026-06-16 after #42 (detail view) and the
ADRs. Tracks the work toward "API-driven output as good as interactive."_

> **Direction is now formalised in `docs/adr/`** (ADR 0001 — deterministic
> generation over a bespoke editable-code DSL; ADR 0002 — the build→critique→
> domain-fix loop). Cluster B below is the **domain-semantic** layer those ADRs
> call for. See `docs/HANDOVER.md` for the current state snapshot.

## Why this exists

draftwright gets good results when driven interactively (Claude Code): render →
eyeball → read lint → adjust → re-render. The same engine driven one-shot via an
API underwhelms — not because of the model, but because the API call doesn't get
those laps. The fix is three-pronged:

1. **Push the deterministic boundary outward** so fewer decisions need an LLM at
   all (the engine self-corrects).
2. **Make the default output good without any laps** — scale, page, and
   annotation choices that look right first time on novel geometry.
3. **Give a non-interactive caller the same scaffolding a human has** — the lint
   critic as a machine-readable signal, plus the primitives to act on it.

Treat "the API gives less impressive results" as a signal pointing at *engine
gaps*, not a prompt-quality problem. Every gap closed in Python is permanent and
free per run; every gap left to the API is paid for, non-deterministic, and
worse on novel input.

## The three clusters of open issues

**Cluster A — make the engine self-correcting** (meets at `lint()`)
- #30 — lint→repair loop (act on violations, don't just report)
- #29 — lint findings carry a `suggestion` code snippet

**Cluster B — primitives so a script/LLM can fix things**
- #26 — `dwg.features(view)` — expose the geometry analysis
- #25 — `Drawing.place_dim()` — layout-strip-aware stacking dimension
- #27 — `dwg.annotations()` — query placed annotations
- #28 — `dwg.view_bounds(view)` — page bbox of a projected view

**Cluster C — default drawing quality** (the staircase / NIST CTC-02 review)
- ~~#43 — location-dimension count/legibility gate~~ ✅ done
- ~~#42 — enlarged detail view for fine / closely-spaced features~~ ✅ done (MVP)
- #45 — representative / "TYP" dimensioning for repeated features

## How they depend on each other

- **Cluster A is downstream of B.** Repairing `feature_not_dimensioned` /
  `callout_dropped` needs the feature geometry back → `dwg.features()` (#26);
  repairing `annotation_overlap` / stacking needs strip re-stacking →
  `place_dim` (#25). #29 (computable `suggestion`) is the bridge from the lint
  surface (#32, done) to auto-apply (#30). Both B primitives also stand alone as
  API wins, so they are low-regret to land early.
- **Cluster C is the immediate sequel to PR #47.** #47 makes automatic selection
  prefer the *smallest* sheet, so parts now sit on tighter pages → less room for
  stacked location dims → higher risk of silent drops. **#43 is effectively part
  two of #47**: a legibility/count gate so a tight sheet drops dims *visibly and
  gracefully* (or re-tiers) instead of overflowing. #42 then gives the dropped
  fine detail somewhere to live (a detail view); #45 cuts clutter for repeated
  features (e.g. the staircase treads, where 1 mm steps can't be individually
  dimensioned at sheet scale).

## Recommended sequence

Cluster C (deterministic drawing quality) is essentially done (#41/#43/#42/#46/#47);
the focus now shifts to the **domain-semantic layer** (ADR 0002's build→critique→
domain-fix loop):

```
#26, #25       domain API: features() + place_dim()   ← do next (frame in DOMAIN terms)
   ↓
#29            lint suggestions — each issue carries a ready domain-API call
   ↓
#30            repair loop — auto-apply computable suggestions, surface the rest
```
#45 (TYP dimensioning) slots in opportunistically. #27 / #28 land wherever an API
caller needs them. Keep #26/#25's surface in **domain vocabulary**
(holes/bores/sections/dimensions), never strip/zone internals (ADR 0001).

## Done

### Foundations (Cluster A surface + verification)
- **#32 — lint score + surface silent drops** (PR #33). `Drawing.lint_summary()`:
  JSON-friendly aggregate of `lint()` — `passed`, coarse 0–1 `score`, severity
  counts, `by_code`, `geometry_issues`, full issue list. `_record_build_issue()`
  records build-time drops; `lint()` surfaces them. One signal to gate/optimise on
  without rendering. Score weights and the geometry-aware code set are
  single-sourced module constants.
- **#31 — derive bare layout constants** (PR #34). Strip slot widths, callout
  label widths, the isometric fit factor are computed from text metrics and page
  size instead of fixture-tuned magic numbers.
- **#13 — overfitting guard tests**. Pin the *general* layout behaviour on
  turned/hybrid parts (flange OD + bolt circle), multi-bore parts, and the
  step-legibility boundary.

### Default drawing quality (Cluster C — done bar #45)
- **#36 — adaptive cardinality caps**. Removed the hard 4 callouts / 4 location
  refs / 3 step-dims caps; the engine now places as many as the available
  strip/corridor space allows, surfacing genuine drops via lint.
- **#41 — step-height legibility gate**. A step is dimensioned only if it is both
  tall enough from the base *and* a legible step-height above the previous one;
  the rest surface as `step_dim_dropped`. "Fits" ≠ "legible".
- **Staircase review fixes** (PR #46). Phantom step corridor no longer blocks a
  larger scale; engraved-text faces no longer dimensioned as phantom steps
  (`min_area_frac` filter); overall-height dim nests outside the step dims.
- **Page-aware scale selection** (#47, released v0.1.8). A specified page enlarges
  to the best fitting scale (iso packed into 2D empty space, e.g. staircase 2:1
  on A3); automatic selection now minimises sheet size (page-major ladder, e.g.
  20×15×10 → 2:1 on A4 not 5:1 on A3); iso growth capped at 1.3× sheet scale.
  Shared `_layout_geometry` so fit and placement can't diverge.
- **Location-dimension legibility gate** (#43, released v0.1.8). `_legible_locations`
  drops hole-location refs whose witness lines would be page-coincident, per axis;
  the rest surface via `location_ref_dropped`. The sequel to #47's tighter sheets.
- **Enlarged detail view** (#42 MVP, #52). When the step gate drops a crowded
  shoulder cluster, auto-generate one enlarged detail view that recovers the
  dropped dims (`_add_detail_view`); non-sheet scale, so `lint()` partitions dims
  by `_dw_scale`. Follow-ups: legibility on already-reduced parts; redundant
  `step_dim_dropped`; the `is_centerline` marker exemption (see `docs/HANDOVER.md`).

### Architecture decisions
- **ADR 0001 / 0002** (`docs/adr/`, PR #51). Deterministic generation + a
  domain-semantic edit API over a bespoke editable-code DSL; the
  build→critique→domain-fix loop as the supported refinement model.

### Earlier groundwork
- #10 turned+drilled classification; #11 free-rectangle iso placement; #12
  single-sourced geometry constants; #20 AP242/PMI STEP import segfault.

## Notes / gaps to keep in mind

- The lint *score* is a heuristic with tunable weights. It's named and
  single-sourced, but the severity/code counts are the authoritative output —
  callers should prefer counts over the scalar where it matters.
- Full location-dimension *coverage* lint (flagging under-located parts that were
  never even attempted) is still out of scope — the drop codes surface *drops*,
  not never-attempted gaps. Candidate for #43/#30.
- With #47's smaller-sheet preference, watch for parts that now drop location
  dims on a tighter page — that is precisely what #43 must gate.
- Naming drift: #29's examples say `LintFinding`; the class is `LintIssue`. Fix
  when #29 is implemented.
- `_ISO_MIN_FIT_FRAC` (0.6) and `_ISO_MAX_GROW` (1.3) are named single-sourced
  constants but not yet derived from first principles — revisit if iso sizing
  looks off on unseen geometry.
