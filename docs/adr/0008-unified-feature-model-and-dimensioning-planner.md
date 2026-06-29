# ADR 0008 — The part-drawing compiler: a Feature/DimParameter IR and a dimensioning planner

- **Status:** Accepted — architecture stands; **migration strategy pivoted**
  (Amendment 2): out-grow the engine, don't reproduce-and-swap it. The equivalence
  golden gate is retired.
- **Date:** 2026-06-28
- **Deciders:** Paul Fremantle (pzfreo)
- **Supersedes the original 0008** ("unified feature model") with a concrete
  architecture. Step 1 (unify Z step recognition, #191/#193) stands.

## Context

draftwright recognises a part's features and dimensions them. Both halves grew by
accretion, and the failure vector is now clear: **N recognisers × M dimensioning
passes, directly coupled, with orientation encoded as code branches.** Every new
shape (keyway, taper, groove, gear, knurl, T-slot, chamfered-counterbored bore…)
tempts another recogniser + another placement pass + another `is_rotational` /
`axis == "x"` gate. That is quadratic coupling growth — the ball of mud.

Concrete symptoms already seen: the `analyse_face_levels` vs `find_turned_steps`
duplication (#191); a phantom bore-floor shoulder from two recognisers filtering
differently; "dimension a turned part's steps" implemented three times by
orientation; and the realisation (the `find_bosses` review) that recognisers are
*plural and complementary*, not a thing to collapse into one function.

The original 0008 ("one model, retire `find_bosses`") had the right instinct but
named the wrong abstraction. The thing that actually stops the growth is a
**stable intermediate representation** between recognition and dimensioning — the
compiler/LLVM hourglass: many front-ends → one narrow IR → many back-ends.

## Decision

Build draftwright as a **part-drawing compiler** with four layers and a stable IR
at the waist. Orientation and feature *kind* become **data in the IR**, never
branches in the back-end.

```
  Geometry-query layer        faces/edges/cylinders/axes/silhouettes, computed once
        │
  Feature detectors (plug-in) find_holes, find_bosses, find_turned_steps, find_slots…
        │   each adapts its heuristics to emit typed Feature objects; none rescans
        ▼
  ┌────────────────────────┐
  │  PART MODEL  (the IR)   │  oriented part + Feature set + Datums  ← the narrow waist
  └────────────────────────┘
        │
  Dimensioning planner        consumes the IR; applies ISO/ASME convention rules
        │   asks each Feature for its DimParameters + references
        ▼
  Layout / render             EXISTING (ADR 0003 Placeable + solver, 0004 pack, helpers)
```

### 1. Two small protocols are the waist (this is the load-bearing decision)

```python
class Feature(Protocol):              # hole, step, boss, slot, chamfer, gear, …
    kind: str
    frame: Frame                      # position + orientation in part space
    def parameters(self) -> list[DimParameter]: ...   # what a drawing MUST show
    def references(self) -> list[Datum]: ...           # datums it measures from/provides

@dataclass(frozen=True)
class DimParameter:                   # the universal currency of dimensioning
    kind: Literal["diameter","length","depth","radius","angle","location","thread"]
    role: str                         # semantic origin: bore/counterbore/step/boss/od/…
    value: float
    span: tuple[Point, Point] | None  # model-space extent, for placement
    refs: tuple[str, ...] = ()        # datum ids it is measured from
```

A `PartModel` holds the oriented part, the `Feature` list, and the `Datum` set.
**`DimParameter` carries a semantic `role`, not a rendered label** — see the
amendment below; formatting and GD&T symbols are a renderer concern.

### 2. Why this survives arbitrary new shapes — Open/Closed by construction

- A new shape = **a new `Feature` subtype + a detector that emits it**, exposing
  `DimParameter`s of *existing kinds*. **Zero changes** to the planner, the layout,
  or any other feature. The 30th feature type costs the same as the 3rd.
- **Orientation is data, not branches.** `frame` carries it; the planner reasons
  geometrically, so X/Y/Z/turned/prismatic are inputs, not `if`s. The
  orientation-gate proliferation cannot recur.
- **Recognisers are front-ends.** `find_bosses` (diameters/bosses) and
  `find_turned_steps` (axial profile) are complementary detectors emitting into the
  IR — exactly as the #191 review concluded. The duplicate-recogniser problem
  cannot recur because dimensioning consumes the IR, not the recognisers.

### 3. The planner is one rule set over DimParameters

For each feature, for each `DimParameter`, the planner applies convention rules —
chain vs ordinate, datum selection, redundancy/duplication avoidance, view choice —
**uniformly**. "A turned part's step lengths", "a hole's depth", "a slot's width"
are all `DimParameter`s flowing through one planner. This is the logic currently
reimplemented per-pass; centralising it is where the back-end stops rotting.

The planner emits one **`DimensionGroup` per feature** (carrying the source
feature + a single view), not a flat dimension list — see the amendment below.

### 4. The layout layer is untouched

The planner emits placement *intents*; the existing `Placeable`/solver (ADR 0003)
+ compose-then-pack (0004) + helpers primitives place them. No change there.

## Migration — strangler, anchored on the protocol (no rewrite)

Full execution plan (all detectors + drawing components, with a scoped golden gate
and X/Z parity as standing criteria):
[`docs/plans/0008-compiler-migration-roadmap.md`](../plans/0008-compiler-migration-roadmap.md).

1. **Define the waist** (`DimParameter`, `Feature`, `Datum`, `PartModel`) and
   **prototype** a vertical slice: build the model from a real part via adapted
   detectors and run a minimal planner, proving diverse features (holes + steps +
   bosses) flow through one pipeline. *(Landed — see `src/draftwright/model/` and
   `tests/test_part_model.py`; not yet wired into `build_drawing`.)*
2. **Adapt, don't rewrite, the heuristics.** Wrap `find_holes`/`find_bosses`/
   `find_turned_steps`/`find_slots` so they emit `Feature` objects; their B-rep
   logic stays.
3. **Introduce the planner in production** for one feature end-to-end (holes — the
   most mature), proving it carries real placement through the existing layout.
4. **Move features onto the planner one PR at a time**, retiring each
   orientation-gated pass as its feature lands.
5. **Generalise planner rules only after ~3 feature types** have stressed the
   `DimParameter` set — don't build a speculative rule engine (that is just
   framework-shaped spaghetti).

## Amendment (2026-06-28) — contract refined by the prototype + reviews (#211, #212)

Building the prototype and reviewing it (counterbore, then patterns) hardened the
waist. The decisions, now in `src/draftwright/model/`:

1. **`DimParameter` carries a semantic `role`, never a rendered label.** The pinned
   font has no GD&T glyphs (`⌴`/`⌵`/`↧`), and the engine draws those as *geometry*,
   not text — so baking a label into the IR is both wrong and font-fragile.
   Formatting is a renderer concern; `display()` gives font-safe debug text. `role`
   (bore / counterbore / step / boss / od / …) is what the planner reasons on.
2. **The planner emits `DimensionGroup`s, not a flat list.** One group per feature,
   carrying the **source feature** + a **single view** + its planned dims. This is
   what lets a *compound* callout (a hole's bore + counterbore + depth) render as
   one callout, lets a `PatternFeature` keep its `count`/`pattern` metadata across
   the waist, and keeps the plan Open/Closed (a renderer reads whatever feature
   metadata it needs without the plan growing a field per feature type).
3. **Redundancy is feature-aware, never value-blind.** The planner does not
   collapse two parameters merely because they share a value (a counterbore ø16
   and a boss ø16; a 10×10 pocket's two 10 mm lengths both survive). Count of
   *repeated identical features* (`6× ø8`) is upstream — a `PatternFeature`, not a
   planner dedup.
4. **View + anchor are group-level and axis-derived.** A group's view comes from
   the feature's axis by one rule for all axes (a diameter callout end-on:
   `z→plan`, `x→side`, `y→front`; a turned step's length+OD on the lengthwise
   `front`) — orientation is data, so X and Z are symmetric. The anchor is the
   feature's frame origin.

Still open (tracked as out-grow issues — *not* gate-protected; the gate is retired):
the view/routing must become *model-aware* for turned concentric bores (front
bore-leader + section, not the generic end-on rule — #207) and for a rotational OD
on a single-OD turned part (profile view, not end-on — #222).

## Amendment 2 (2026-06-28) — strategy pivot: out-grow, don't reproduce-and-swap

The original migration plan (reproduce each engine pass on the IR, swap it in under
a byte-equivalence golden gate) was wrong, and building it exposed why:

- **The equivalence gate enforced the wrong goal.** A byte-semantic gate demands the
  new path *match the existing engine exactly*, which forces the clean framework to
  clone the accreted engine's quirks bug-for-bug. You cannot build a robust
  framework by mimicking the thing you are trying to replace. (This is the standing
  wariness of golden gates, ADR 0005 §3, made concrete: a gate for a *refactor*
  preserves behaviour; a gate for a *re-architecture* freezes the improvement that
  is the entire point.)
- **It front-loaded large, low-value, high-risk work.** Reproducing holes →
  location dims → sections → turned-bore routing → layout → tables *just to
  re-achieve what already works* is parity-first, value-last. The renderer-seam
  spike (#213) made this plain: replacing `holes.py` means re-modelling almost the
  whole engine before any new capability ships.

**Revised strategy — out-grow, not replace:**

1. **The IR/planner/render pipeline is the path for *new* and *poorly-handled*
   shape work.** It earns its place by making the *next* shape clean, not by
   re-achieving parity on shapes the engine already draws.
2. **The existing engine keeps its current responsibilities** and is migrated only
   *opportunistically* — when a pass would otherwise need a new orientation branch,
   route it through the IR instead. No big-bang reproduce-everything.
3. **Success is correctness, not equivalence.** The new path is judged by
   lint-clean + ISO/ASME-compliant + coverage-complete output (the geometry-level
   and `test_e2e_standards` suites), *not* by matching the old engine byte-for-byte.
   This lets the new path be cleaner/better.
4. **The scoped migration golden gate is retired** (`tests/_migration_gate/` +
   `test_migration_gate.py` removed). It was the mechanism enforcing equivalence;
   it served its purpose proving step 1 and is now counter-productive. Regression
   coverage rests where it always should (ADR 0005 §3 / 0007): the geometry-level +
   property-based standards suites, plus targeted behavioural tests.
5. **Validate the *whole* pipeline end-to-end** (one part → a complete, correct
   drawing via detect → model → plan → render → layout) before grinding feature by
   feature — so integration gaps surface in context, judged by correctness.

The earlier roadmap (`docs/plans/0008-compiler-migration-roadmap.md`) and the
reproduce-and-swap framing of issues #197–#209 are superseded by this; the epic
(#195) is re-scoped to value-first deployment.

## Consequences

- New shapes are **new types, never new branches** — the property the product
  needs to absorb complex shape requirements over time.
- One source of truth (the IR), one dimensioning rule set; the duplicate-recogniser
  and orientation-gate bug classes are designed out.
- Incremental and low-risk: each step ships value; the engine keeps working; the
  prototype de-risks the protocol before any production rewiring.

## Risks

- **Recognition stays heuristic.** A chamfered bore in a tapered section will not
  recognise itself cleanly. The architecture *contains* that mess inside detectors
  behind the IR; it does not eliminate it. That is the realistic goal.
- **Over-abstraction.** Resist a speculative planner rule engine or a maximal
  `DimParameter` taxonomy before real features demand them. Grow the IR from
  consumers, not from imagination.
- **The sizing path is load-bearing** (`step_zs` → scale/page). Migrate it late;
  if a step is genuinely risky, stand up a *scoped, disposable* golden gate for
  that step only and delete it — draftwright keeps no standing general gate by
  design (ADR 0005 §3), since a permanent one freezes improvement.

## Impact on other ADRs

- **0003 / 0004** — unchanged; the planner feeds the existing layout/pack.
- **0005** — extended: this carries the compiler-pipeline separation into the
  recognition/dimensioning stages 0005 left as an `analysis`/`annotations` lump.
- **0007** — built on: the IR and detectors live in the now-owned `recognition/`;
  the planner alongside `annotations/`.

## Related

- Issue #191 (step 1 — unify Z step recognition; landed #193).
- The drive-screw thread (ADR 0007 PR-C) that made the accretion obvious.
- Prototype: `src/draftwright/model/` (`ir`, `detect`, `planner`),
  `tests/test_part_model.py`.
