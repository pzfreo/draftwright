# ADR 0010 — Annotation provenance: record intent → annotation once, at the render seam

- **Status:** Accepted (decision; work pending — re-plans #398c–e, enables #400)
- **Date:** 2026-07-03
- **Deciders:** Paul Fremantle (pzfreo)

## Context

The editable-surface epic (ADR 0001 Amendment 1 — *edit the detected model*) needs
to answer one question repeatedly: **"which annotations did this feature/intent
produce?"** It underlies `drop(feature)`, `dimension(feature, …)`, finer param/datum
edits, the `finalize` recompose (#388 Ph2), and the expanded-semantic-script emitter
(#400).

#398b established the *sink* for that answer: **feature provenance as a first-class
axis of annotation identity** in the registry (`_anno_feature`: name → source IR
feature, peer to `_anno_view`, snapshot/restored so a repack/repair preserves it).

But *populating* it turned out to be cross-cutting. The feature link is lost at several
layer boundaries before `dwg.add`:

- the **ADR-0009 corridor/strip placer** (`place_strip_candidates`) works on
  `(name, build)` tuples — the feature is gone (slots, locations);
- the **turned-diameter passes** flatten IR steps into `(tip, label)` specs before
  rendering;
- **`holes.py` callouts/balloons** work from *recognition* `Hole` objects, and the IR
  stores **no back-link** to the recognition objects it was built from, so neither side
  can name the other's feature.

Tagging each pass in turn (398c, 398d, …) treats the symptom pass by pass. The common
**root** is that there is no single seam recording, per planned intent, the annotation
names it emitted. The planner already produces one `DimensionGroup` per feature
(ADR 0008), and the render layer consumes groups and calls `dwg.add` — but the
`group → names` mapping is discarded at the moment it is known.

## Decision

Install **one provenance seam at the intent → render boundary**, rather than tagging
each render pass:

1. **IR features carry an `origin`** — a stable back-link to the recognition object (or
   a stable key) they were detected from. This closes the recognition ↔ IR gap so any
   layer holding a recognition object can name its IR feature, and vice versa.

2. **The render layer records `intent → [annotation names]` centrally** — at the point
   each `DimensionGroup` / `DimParameter` is rendered (and at *drain* time for
   corridor-placed dims, where the add is deferred), the emitted names are recorded
   against their source intent, which carries the feature (and datum / param). The
   registry's `_anno_feature` (#398b) remains the sink; the seam is the **automatic
   populator**, complementing explicit `dwg.add(feature=…)`.

3. **Provenance is intent resolution, never string convention** — the feature / param /
   datum of an annotation is recovered from the recorded intent link, never parsed back
   out of the annotation *name* (`m_locx0`, `hc_plan1`, …). Names stay an internal
   detail.

## Consequences

**Positive**
- `drop(feature)` / `annotations_of(feature)` get **complete coverage from one change**
  — 398c and 398d collapse into the seam instead of being separate per-pass patches.
- **Param-level** ("drop the depth, keep the diameter") and **datum-level** edits fall
  out: the intent already carries its `DimParameter` and datum.
- The **#400 expanded-semantic-script emitter** is enabled — it needs exactly
  intent → names to emit each intent as a `dwg.dimension(feature, …)` call.
- **finalize / repack** (#388 Ph2) can re-apply edits, because edits become intent
  operations rather than opaque page mutations that a re-assemble discards.

**Negative / costs**
- Larger upfront than per-pass tagging: it touches the planner/render boundary and the
  IR feature types (the `origin` field).
- The corridor layer (`place_strip_candidates`) still must carry the intent/feature
  through to the drain-time add — the seam *formalises* that carry rather than avoiding
  it.
- Must stay **additive**: provenance is metadata; no drawing output may change (the
  layout-snapshot + cleanliness suites are the guard).

**Re-plan (supersedes the per-pass staging of #398c–e):**
- **#398c** — add `origin` to IR features + record `intent → names` at the render/drain
  seam. One move populates provenance for corridor-placed dims *and* everything else.
- **#398d** — fold `holes.py` callouts/balloons into the seam via `origin`
  (recognition → IR), retiring the bespoke map.
- **#398e** — `dimension(feature/param, datum)` add verb, recorded through the same seam.
- **#400** — the emitter reads the seam.

`#398b` stands unchanged: its registry `_anno_feature` store and the explicit
`dwg.add(feature=…)` form are the foundation the seam populates automatically; the
centre-mark tagging it shipped is simply the first (manual) population.

## Related

- [ADR 0001](0001-deterministic-generation-over-editable-dsl.md) Amendment 1 — edit the
  detected model; this is the mechanism that makes drop/dimension/emit possible.
- [ADR 0005](0005-pipeline-architecture-and-state-ownership.md) — the registry is the
  single owner of annotation identity/ownership; provenance is the new axis, and its
  sink.
- [ADR 0008](0008-unified-feature-model-and-dimensioning-planner.md) — the IR/planner
  intent boundary is where the seam lives; `origin` extends the IR feature types.
- [ADR 0009](0009-boundary-labeling-strip-placement.md) — the corridor placer is a
  layer that must carry the intent through to its deferred add.
- Issues: #398 (edit-by-feature), #400 (expanded semantic script), #388 (finalize).
