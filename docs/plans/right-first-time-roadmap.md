# "Right first time" roadmap — hardening the deterministic core

_Status: living plan. Last updated 2026-06-17. The original "right-first-time"
arc (deterministic self-correction + a domain-semantic editing API) closed with
**v0.1.9**; this revision re-baselines on that and sequences the next arc._

## Why this exists

draftwright gets good results when driven interactively (Claude Code): render →
eyeball → read lint → adjust → re-render. The same engine driven one-shot via an
API used to underwhelm — not because of the model, but because the API call
didn't get those laps. The fix was three-pronged:

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

## Done — the right-first-time arc (through v0.1.9)

The three clusters that defined this arc are **complete and released**.

### Cluster A — the engine self-corrects (meets at `lint()`)
- **#32 — lint score + surface silent drops** (v0.1.7). `lint_summary()`:
  JSON-friendly aggregate — `passed`, 0–1 `score`, severity counts, `by_code`,
  `geometry_issues`, full issue list. One signal to gate/optimise on without
  rendering.
- **#29 — lint findings carry a `suggestion`** (v0.1.9). Each repairable issue
  ships a ready-to-paste domain-API snippet (`_suggest_fix`).
- **#30 — lint→repair loop** (v0.1.9). `Drawing.repair()`, run by default in
  `build_drawing`, mechanically fixes the codes with a deterministic placement
  fix (overlap push-apart, wrong-side flip); a pass that would net-increase
  issues is rolled back.

### Cluster B — primitives so a script/LLM can fix things
- **#26 — `dwg.features(view)`** (v0.1.9). Detected holes/features grouped by
  machining spec, in page coordinates.
- **#25 — `dwg.place_dim(p1, p2, side, view, draft, …)`** (v0.1.9).
  Layout-strip-aware stacking dimension from domain inputs.
- **#27 — `dwg.annotations()` / `dwg.get_annotation(name)`** (v0.1.9).
  Introspect placed annotations; the old `dwg.annotations` list is now
  `dwg.items` (breaking, pre-1.0).
- **#28 — `dwg.view_bounds(view)`** (v0.1.9). Page bbox of a projected view.

### Cluster C — default drawing quality (staircase / NIST CTC-02 review)
- **#36 — adaptive cardinality caps** (v0.1.7). Hard 4/4/3 caps removed; the
  engine places as many as space allows, drops surface via lint.
- **#41 — step-height legibility gate** (v0.1.8). "Fits" ≠ "legible".
- **#43 — location-dimension legibility gate** (v0.1.8). The tall-tower fix,
  with datum-edge correctness.
- **Page-aware scale selection** (v0.1.8). Specified page enlarges to best
  fitting scale (2D iso packing); automatic selection minimises sheet size
  (page-major ladder); iso growth capped at 1.3× sheet scale.
- **#45 — TYP / representative dimensioning** (v0.1.9). A uniform step run is
  dimensioned once and labelled TYP.
- **#42 — enlarged detail view (MVP)** (v0.1.9). Opt-in `detail_view=True`
  re-draws crowded shoulders at a larger scale; per-view-scale lint.

### Design record
- **ADR 0001** — deterministic generation over an editable-code DSL.
- **ADR 0002** — iterate via lint critique and domain repair.

## The next arc — correctness, then GD&T, then output polish

With the right-first-time scaffolding in place, the open backlog falls into four
themes. The ordering principle: **a wrong dimension is worse than a missing or
ugly one**, so geometry-correctness bugs lead.

### 1. Geometry correctness (do first — these emit *wrong* drawings)
- **#68 — blind-hole depth measured across solid boundaries.** On a multi-solid
  assembly a bore reports `⌀9.8 ↓111.4` — a depth ~4× the bore-axis extent,
  because the opposite-face search crosses into a neighbouring solid. Fix:
  restrict the bottom-face search to the entry face's own solid, and never emit
  a depth exceeding that solid's bore-axis extent.
- **#67 — recover exact circles for revolved/NURBS on-axis silhouettes.**
  Imported-STEP turned features come back from HLR as approximating B-splines,
  not circles → spline DXF entities and fitted (not exact) radii. Fix: a
  post-projection silhouette conic-refit pass, grouping candidate faces *per
  revolution axis* (pooling all axes cross-contaminates neighbours). A working
  implementation exists in gib-tuners to port.

### 2. GD&T / PMI (the next capability arc — mirrors the domain-API arc)
- **#61 — GD&T placement API: `dwg.place_fcf()` / `dwg.place_datum()`.** The
  domain primitives, analogous to `place_dim`. Land before auto-annotation so
  there is a target to place into.
- **#62 — auto-annotate GD&T from STEP PMI (Phase 4).** Read PMI feature control
  frames / datums and place them via the #61 primitives.

### 3. Assembly-awareness
- **#69 — assembly-aware `feature_not_dimensioned`.** A general-arrangement
  drawing shouldn't demand a callout on every bore; the coverage lint needs an
  assembly/GA mode. (Pairs with #68 — both are multi-solid gaps.)

### 4. Output polish & ergonomics
- **#70 — auto-place / collision-resolve leaders, notes, text blocks.** Extends
  the repair loop (#30) to free-form annotations.
- **#73 — report the auto-selected page size.** Surface the chosen sheet back to
  the caller (currently silent).
- **#54 — enlarged detail view beyond the MVP.** Sub-region selection, multiple
  details, location/callout dims fed in, ISO detail-circle convention,
  `add_detail(region, scale)` domain API.
- **#56 — suppress the `detail_view=True` suggestion once a detail view exists**
  (small; acceptance #2 of the issue — #1 already shipped with #29).
- **#72 — generic auto-placed table primitive** (gear data / BOM / rev history).
- **#71 — optional shaded (raster) isometric pictorial.**
- **#57 — AnalysisSitus SDK for feature recognition** (larger, exploratory).

## Recommended sequence

```
#68  blind-hole depth across solids   ← do next (wrong dimension)
#67  exact-circle silhouette refit       (wrong DXF entities / fitted radii)
   ↓
#61  place_fcf() / place_datum()         GD&T primitives
#62  auto-annotate GD&T from PMI         (consumes #61)
   ↓
#69  assembly-aware coverage lint        (pairs with #68's multi-solid work)
   ↓
output polish: #70, #73, #54, #56, #72, #71 — slot in opportunistically
#57  AnalysisSitus — separate exploratory track
```

## Notes / gaps to keep in mind

- The lint *score* is a heuristic with tunable weights; severity/code counts are
  the authoritative output — prefer counts over the scalar where it matters.
- Full location-dimension *coverage* lint (flagging never-attempted gaps, not
  just drops) is still out of scope.
- `_ISO_MIN_FIT_FRAC` (0.6) and `_ISO_MAX_GROW` (1.3) are named single-sourced
  constants but not yet derived from first principles — revisit if iso sizing
  looks off on unseen geometry.
- #68 and #67 both originate from the gib-tuners drawing pipeline, which has
  switched to `make_drawing`; that pipeline is a good real-world regression
  source for the multi-solid and turned-feature paths.
