# ADR 0008 — The part-drawing compiler: a Feature/DimParameter IR and a dimensioning planner

- **Status:** Accepted — **migration complete; one path** (2026-06-30). The
  correctness-judged strangler converged on ONE feature→dimension path (Amendment 3),
  which *feeds* the engine's shared layout/table/section/projection/export
  infrastructure rather than reabsorbing it (Amendment 4), **through an IR-typed
  interface — no recognition objects cross the boundary** (Amendment 6). Not
  reproduce-and-swap (Amendment 2), not two permanent paths — each step deleted the
  per-feature engine pass it replaced. **One path, one feature inventory**
  (Amendment 5): `_analyse` detects once and the IR consumes its products. Every
  feature pass — holes, sections, turned dims/lengths, slots, centre marks, envelope,
  the prismatic step-ladder + rotational OD/bore furniture (#237), and PMI (#208) — is
  now on the IR; the orchestrator is `build model → plan → render`. The equivalence
  golden gate is retired. (Deferred *enhancements*, not migrations: #230, #222, #279.)
- **Date:** 2026-06-28
- **Deciders:** Paul Fremantle (pzfreo)
- **Supersedes the original 0008** ("unified feature model") with a concrete
  architecture. Step 1 (unify Z step recognition, #191/#193) stands.
- **Amendment 7** (2026-07-12): the "one inventory" waist is now two tiers — a
  shared *geometric* recognition record feeding the *dimensioning* IR through a uniform
  `detect.py` adapter protocol (typed per-record converters, not one universal
  converter) (ADR 0013). Amendment 6 (no recognition object crosses the boundary) is
  preserved.
- **Amendment 8** (2026-07-13, epic #584 WP1): Amendment 6 is enforced across the
  **render** path — the hole/pattern/boss recognition records no longer survive into the
  annotation/table/section/callout renderers. `annotations/` (sections, hole callouts,
  the hole table + balloons, the off-axis location dims, the scattered-hole tabulation)
  now reads the IR (`model.features`), and so does **page/scale sizing** — `_analyse`
  builds the IR once, up front, and the `sheet.py` estimators size off it (subsystem A);
  detected and declared parts share one sizing path. The only place records cross is the
  sanctioned `build_part_model` boundary itself. **One path deliberately keeps reading
  recognition records, and this is correct — not a boundary violation:**
  - **lint / coverage** (`linting/coverage.py`) validates the drawing against
    **recognised geometry** ("is every feature that physically *exists* dimensioned?").
    That ground-truth check must read recognition, not the IR: sourcing coverage from the
    dimensioning *plan* would be circular — a feature the planner omitted would never be
    flagged. `linting/` also stays a leaf (no `model` import) by design. Coverage reading
    recognition is the check working, not a leak.

## Current decision (as amended, 2026-07-12)

*The part-drawing engine is a **compiler**.* detectors → a **Feature / DimParameter IR**
(`PartModel`) → a dimensioning **planner** → **render-intents** → the shared
layout / table / section / projection / export infrastructure. **One feature inventory,
detected once** (`_analyse`); the orchestrator is **build model → plan → render**.
Orientation / feature-kind are *data in the IR*, not code branches. The migration is
**complete — one path**: each step deleted the per-feature engine pass it replaced (not
reproduce-and-swap, not two permanent paths — Am 2/3). The IR↔infra boundary is
**IR-typed** — no recognition object crosses it (Am 6). The waist is now **two tiers**
(Am 7): a shared **geometric recognition record** feeds the dimensioning IR through a
uniform `detect.py` adapter protocol (a typed registry of per-record converters).

*The amendments below are the reasoning trail (contract refinement → out-grow strategy →
one-path convergence → IR/infra boundary → one-inventory foundation → IR-typed interface →
two-tier waist) — read them for the* why, *not to reconstruct the current state.*

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

> **Plan of record: [`docs/plans/0008-convergence-roadmap.md`](../plans/0008-convergence-roadmap.md)**
> (Amendment 3). The original scoped-golden-gate roadmap is retired; the section
> below records the initial thinking.

Initial execution sketch (all detectors + drawing components, originally with a
scoped golden gate and X/Z parity as standing criteria):
[`docs/plans/0008-compiler-migration-roadmap.md`](../plans/0008-compiler-migration-roadmap.md).

1. **Define the waist** (`DimParameter`, `Feature`, `Datum`, `PartModel`) and
   **prototype** a vertical slice: build the model from a real part via adapted
   detectors and run a minimal planner, proving diverse features (holes + steps +
   bosses) flow through one pipeline. *(Landed — see `src/draftwright/model/` and
   `tests/test_part_model.py`; this initial slice was not yet wired into
   `build_drawing` — the later amendments completed that, and the IR is now the
   production path. See the header and Amendment 3.)*
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

## Amendment 3 (2026-06-29) — converge on ONE path (corrects Amendment 2)

Amendment 2 fixed the *reproduce-and-swap* mistake but, read literally ("the engine
keeps its responsibilities and is migrated only opportunistically"), it implied a
**second, permanent failure mode: two divergent paths** — the accreted per-feature
engine passes *and* a parallel IR pipeline doing overlapping work, drifting apart
forever. That betrays the entire point of ADR 0008, which is **one consistent
architecture** replacing the N×M coupled mess. Two permanent paths are worse than
either pure option.

The destination is, and always was, **a single path**:

```
detectors → IR (PartModel) → planner → render-intents → [shared layout / projection / export]
```

The per-feature annotation passes (`annotations/{holes,turned,sections,pmi,slots}`,
the inline envelope/OD/centre-mark/step-ladder code in the orchestrator) **all
migrate onto this path and are deleted.** The orchestrator's end state is
`build model → plan → render` — no per-feature pile. The shared layout/projection/
export stack is *not* rewritten (ADR 0008 always fed the existing layout); what
disappears is the duplicated feature→dimensioning logic. *(How that shared layout
itself places annotations into a view's strips is settled separately by
[ADR 0009](0009-boundary-labeling-strip-placement.md): the render-intents become
the candidates its collect-then-solve stage consumes per strip.)*

**Migration is a strangler, governed by three rules:**

1. **Convergence, not divergence — each migration DELETES the engine pass it
   replaces.** This is the load-bearing discipline. Adding an IR renderer while
   leaving the engine equivalent in place (as `render_into` currently sits, used
   only in tests) is the divergence smell and is *not* a completed migration. A
   step is done only when the old code is gone. #231 (turned step lengths) is the
   template: it deleted `_annotate_turned_lengths` and bypassed the Z ladder.
2. **Judged by correctness, not equivalence** (kept from Amendment 2). The new path
   is held to lint-clean + ISO/ASME-compliant + coverage-complete (now a real bar,
   since lint is drawing-derived — #218/#219), *not* byte-identity with the engine.
   The output may be *better* (the Z step chain is). No standing equivalence gate.
3. **Ordered worst-handled-first.** Migrate where the IR adds the most value and
   de-risks fastest (the engine's asymmetric/awkward features) before the
   already-clean ones — but *all* of it migrates; "pain-point ordering" is sequence,
   never a licence to leave a feature on the engine permanently.

Net effect vs the two rejected approaches: not a big-bang equivalence swap
(Amendment 1), not two forever-paths (a literal reading of Amendment 2) — an
incremental, correctness-judged convergence that ends with one architecture and the
engine's per-feature passes deleted. Tracked in
[`docs/plans/0008-convergence-roadmap.md`](../plans/0008-convergence-roadmap.md).

## Amendment 4 (2026-06-29) — the IR/infrastructure boundary

"One path" (Amendment 3) is about the **feature→dimension-intent** path, not a
rewrite of the drawing engine's *shared infrastructure*. Reviewing the remaining
migrations showed several were mis-scoped as "model X in the IR" when X is actually
shared infra the IR should **feed**, not absorb. Draw the line explicitly:

- **Belongs in the IR path (migrate + delete the per-feature code):** feature
  *recognition* (detectors → `Feature`s) and *what to dimension* (`DimParameter`s,
  the planner's convention rules, the render intent — which callout/dim, which
  view, which datum).
- **Stays as shared infrastructure (the IR feeds it; do NOT reabsorb or rewrite):**
  the zone-strip layout allocators (ADR 0003), the hole-table / balloon escalation
  (`add_table`/`add_hole_table`/`_maybe_tabulate_holes`), the section/detail-view
  *rendering* machinery, projection (`Drawing.at`), and export. These are
  feature-agnostic services. A migrated renderer *calls* them (e.g. the zone-aware
  `render_envelope` allocates from `fv/pv/sv` strips); it does not replace them.

Consequences for scope:
- **Definition of done** is "the per-feature *recognition + placement* passes
  (`annotations/{holes,sections,pmi}`, inline envelope/OD/step-ladder) are deleted
  and the orchestrator is `build model → plan → render`" — **with the shared
  layout/table/section/projection/export infrastructure intact.**
- A feature that needs a *section* contributes a **trigger** to the planner; the
  existing section machinery renders it. A dense hole field contributes its **hole
  set**; the existing escalation tabulates it. The IR decides *what/where*; the
  infra decides *how it is drawn*.

This shrinks the holes (#238) and sections (#207) epics from "rebuild the
infrastructure" to "model the feature intent and feed the existing services."

## Amendment 5 (2026-06-29) — ONE feature inventory; foundation hardening (#241)

A mid-migration review (#241) surfaced that "one path" (Amendment 3) needs a
companion invariant: **one feature inventory.** As production passes migrate onto
the IR, the engine's `_analyse()` and the IR's `build_part_model()` were left
*both* running feature detection — `find_holes`/`find_turned_steps`/`find_bosses`/
`find_slots` are now run by `_analyse`, by `build_part_model`, *and* (for turned
steps) by the orchestrator — detecting some features 3×. That is a performance
cost and, worse, a **divergence risk**: two inventories that can disagree while
both are live. With the engine slot/diameter/length passes now deleted, parts of
`_analyse`'s feature set (`a.slots`) have no remaining reader — pure duplication.

**Invariant:** there is **one** feature inventory per build. `_analyse()` and
`build_part_model()` must not independently re-detect; the IR is built *from*
`_analyse`'s products (or `_analyse` builds and caches the `PartModel` once and
threads it). This is the **keystone** of the remaining migration — the unmigrated
epics (#237, #238) will consume the model, so the model must be the single source
first.

The review also named three foundation smells to hold the line on as migration
continues (tracked in #241):

- **No new private-`Drawing`-state reads.** `_named`/`_anno_view`/`_pinned`/
  `_build_issues`/`_pattern_callouts`/… remain as ADR-0005 migration aliases;
  production code (incl. the new renderers) should reach annotation
  ownership/iteration/build-issues through a small **registry-backed accessor**,
  not the raw aliases. Don't widen the state-bus surface.
- **The planner grows render *intents*, not layout.** Suppression reason,
  preferred view, semantic datum/reference, and feature grouping belong in the
  planner's output; *how* it is drawn stays in layout/render infra (Amendment 4).
  This is the next planner-contract increment (after the inventory is unified).
- **Docs/comments track the live state.** The architecture is moving fast; stale
  "prototype / not yet wired" / Amendment-2 framing must be swept after each
  convergence PR so it does not misdirect the next one.

Order: **unify the inventory first** (keystone), then the docs sweep, the accessor
boundary, the planner-intent increment, and finally delete the `render_into`
test-only parallel once the holes epic supersedes it.

## Amendment 6 (2026-06-29) — the IR→infra interface is IR-typed

Amendment 4 drew the *boundary* (the IR decides *what/where*; the shared infra
decides *how it is drawn*) but did not specify the **interface** across it. The
holes migration exposed the gap: the shared hole-table / balloon escalation
(`cover_pattern` / `_maybe_tabulate_holes`) matches on the recogniser's `Hole`
objects, so the IR-driven callout loop (#260) has to map IR groups **back** to
recognition `Hole`/`Pattern` objects (`loc_to_hole` / `pat_by_key`) to feed it.
That leaves recognition objects **load-bearing downstream of the IR** — which
contradicts the whole point: the IR is supposed to be the *single representation*
after detection.

**Decision.** The data that crosses the IR→infrastructure boundary is **IR-typed** —
model-space locations, `DimParameter`s, feature kinds, and stable feature/datum
keys — **not recognition objects.** Concretely:

- The hole-table / cover bookkeeping matches by **location key**, not `Hole`
  identity, so the escalation no longer needs `Hole` objects.
- Placement consumes member **locations** + the feature **diameter**, not `Hole`s.
- Sheet furniture (bolt-circle centre-line, pitch dims) reads the **`PatternFeature`**
  (members / bcd / pitch / rows / cols), not the recognition `Pattern`.
- End state: `a.holes` / `found_patterns` are **not threaded into the render layer**;
  detection feeds the IR, and only the IR flows downstream.

The shared *services* still stay shared (Amendment 4 stands — we do not reabsorb
the layout solver, projector, exporter). This amendment only fixes **what type of
data** the renderer hands them.

**Enforcement — by the type system, not a written rule.** The boundary is enforced
by **IR-typed signatures** (mypy): a shared service accepts IR types only, so a
recognition object simply *cannot be passed* — the norm becomes a compile-time
guarantee. Where the data is structured and identity-bearing, use a **small frozen
value type** (e.g. a `HoleRef`/feature key for the cover/table bookkeeping), not a
raw `Hole`. Do **not** invent a single universal "render-item" dataclass spanning the
whole boundary: the sub-channels are genuinely different services (allocate a float,
project a point, place a primitive, record coverage) — type each at its own
signature. Grow a shared emit-type only if the renderers later converge and a real
consumer demands it (the ADR's anti-over-abstraction rule, below).

**Standing norm:** a migration **may not add new recognition-object coupling across
the boundary.** Where it exists today (the holes/table path) it is **debt on a
defined path to removal (#263)** — not an acceptable steady state. Per the project
stance at this stage: *do it right or not at all — incrementally, no accumulated
debt.*

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

## Amendment 7 (2026-07-12) — the intake becomes a uniform lower tier (ADR 0013)

This ADR got the **waist** right (a uniform IR `Feature` inventory) but left the
**intake** — the recognisers that feed it — un-contracted: mixed `find_`/`analyse_`
naming, `list` vs `Optional`-singular vs bare-`list` returns, and per-feature bespoke
`detect.py` translators that mirror each recognition dataclass into an IR `Feature`
(duplicating fields and logic). ADR 0013 pulls the intake up to the waist's standard.

The waist is now **two tiers**:

- a **shared, geometry-only recognition record** (the lower tier — points/axes/radii/
  angles, `.to_dict()`, no build123d types out), produced by uniform
  `recognise_<feature>(part) -> list[Record]` recognisers; and
- draftwright's **dimensioning IR `Feature`** (the upper tier, this ADR's inventory),

joined by a **uniform `detect.py` adapter protocol** — a typed registry of per-record
converters (record → `Feature`) dispatched one way. This is *not* one universal
converter: hole→`HoleFeature` and chamfer→`ChamferFeature` carry different `DimParameter`
semantics, so the per-type mapping is irreducible; what the protocol removes is the
ad-hoc, each-different bespoke translators of today. It *strengthens* Amendment 6: no
recognition *object*
crosses the boundary — the geometric record is a decoupled fact-sheet the seam adapts,
not a recogniser's internal type. The IR `Feature` Protocol stays draftwright-side; the
lower tier is destined for the standalone `b123d-recognisers` package (ADR 0013, Phase
2). Also folds in the `callout()`-on-`ChamferFeature` crack: label formatting lives
uniformly in the dimensioning tier, never on the geometric record.
