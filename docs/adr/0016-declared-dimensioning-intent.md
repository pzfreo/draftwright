# ADR 0016 — Declared dimensioning intent: capture what to measure, let the engine place it

- **Status:** Proposed
- **Date:** 2026-07-26
- **Deciders:** Paul Fremantle (pzfreo)

> **Scheduling reality.** Only phase 1 (additive intent) is reachable on today's
> machinery. Phases 3–5 — suppression, honest reconciliation, and the emitter's
> dimension mirror — sit behind the global recompose (#426/#707), the longest-open
> item on the roadmap. Accepting this ADR is therefore also a decision to schedule
> that recompose; it is the prerequisite, not an adjacent nicety.

## Context

A user reading a generated `Sheet` script today sees the part's **features** — one
declarative line per hole / slot / pattern / chamfer / step — and, since the
self-describing-emitter change, a comment mirroring each. What they do **not** see is
the drawing's *dimensioning*: the ⌀ callouts, the location ladders, the overall
envelope spans, the pitch dims. Those are derived by the planner (`plan_dimensions` →
one `DimensionGroup` per feature, ADR 0015) and placed by the collect-then-solve
corridor solver (ADR 0014). They are real, dominant marks on the sheet, yet they are
invisible in the code and cannot be adjusted from it.

Three ways to express drawing intent already exist, and they bracket the gap:

1. **Feature declarations** (`sheet.hole(...)`, ADR 0011): declare geometry; the engine
   decides which dimensions it earns. You cannot say *which* of a feature's possible
   measurements matter — the planner's rule set decides.
2. **Aspect declarations that ride a feature** (`.thread("M3x0.5")`, `.finish("1.6")`,
   `.note(...)`, `.tolerance(...)`): declare an *enrichment* semantically; it folds onto
   the feature's callout and **does not touch the layout engine**. This is the archetype
   the user pointed at — adding a thread detail enriches the drawing without denying
   auto-placement.
3. **Edit emphasis** (`dimension(..., pin=, priority=)`, ADR 0012): rank or anchor a
   dimension in the global solve without hardcoding its position (`intents.py`, drained
   by `Drawing.finalize()`).

The missing axis sits between (1) and (3): a way to declare **which measurements the
drawing should carry, and why** — a *dimensioning-selection intent* — expressed in
feature/world terms, never in page coordinates or strip assignments, routed through the
same planner and corridor solve as the automatic dimensions. Concretely, today you
cannot declare "dimension the pitch between these two bosses", "give this wall an
overall-thickness dimension", or "do **not** auto-dimension this hole's location" as
*intent* and let the engine place the result.

The near-miss primitive is today's `sheet.dimension(...)` (`model.declare.authored_dimension`),
used for imported AP242 PMI. But it is a *materialized* dimension — it carries
`ref_pts` / `ref_bbox` / `at` — so it leans toward the hardcoded-geometry form ADR 0001
deliberately rejected, not toward scale-independent intent. It is the escape hatch for
"a source already measured this", not the intent layer for "this measurement matters".

**This is not in tension with ADR 0001 — it is its unfinished half.** ADR 0001 §2 says
editing should be exposed as "the domain vocabulary a model already knows (features
such as holes/bores/sections; **intent such as 'dimension this bore's depth'**), not the
internal strip/zone machinery." Feature and aspect intent landed; *dimensioning-selection*
intent did not. ADR 0001 rejected only the fully-expanded, place-everything editable DSL.

## Decision

**Add a declared dimensioning-intent layer: semantic statements of _what to measure_
(and what to suppress), resolved by the planner and ADR 0014 solver into placed
dimensions. Intent never carries page geometry, never assigns a strip, and never
overrides the layout engine — it enters the same population the automatic dimensions
do.** The generated script becomes a mirror of the drawing's *intent*, not its geometry:
every line is a droppable / editable declaration; the engine still owns derivation and
placement; re-running re-solves.

The one surface is a **referential dimension line** — `sheet.dimension(<feature>, <role>)` —
that names a feature (or the envelope) and the measurement to show, and **carries no
number**. The nominal value is always read from the referenced geometry, so a size lives
in exactly one place — the feature declaration (for a STEP part, its detected snapshot;
for a live part, the build123d object it reads from) — and a dimension can never drift
from it. This is what dissolves the dual-source-of-truth objection to a complete
per-dimension mirror: the mirror is complete *and* single-source because the lines
reference rather than restate. The same verb covers three behaviours, all scale- and
placement-independent (anchored in feature / world terms, per the ADR 0012 constraint):

- **Surface & drop** — the emitter emits one `dimension(...)` line per dimension the drawing
  carries, so the script mirrors the sheet; commenting a line out **suppresses** that
  dimension, keyed by its semantic `feature + DimParameter role` identity, never a page
  name or coordinate.
- **Add** — a `dimension(...)` line for a measurement the planner would not auto-produce (a
  span between two features, a pattern pitch, a wall thickness, an angle) enters the
  shared solve as a new `CorridorCandidate`.
- **Emphasise** — `.pin()` / `.priority()` chained onto a `dimension(...)` line ranks or
  anchors it in the solve (already ADR 0012), without fixing a coordinate.

The `.thread` / `.finish` aspect is the template and the invariant: a `dimension` line edits
the dimension *set* the drawing carries; the corridor solve still decides ordering,
dedup, priority-drop, and position. **A dimension line references; the engine places.**

Only *dimensions* are surfaced as lines. Low-level furniture the engine derives — centre
marks, section arrows, hatching, the NTS caption — stays automatic; surfacing each as a
line would explode the mirror without adding any editable intent.

### Dimensions are sheet-level; the view is derived placement

The API is **flat** — `sheet.dimension(<feature>, <role>)` on the sheet — never
`sheet.view(v).dimension(...)`. A dimension's identity is `(feature, role)`, not
`(view, feature, role)`; the view it renders in is a placement the engine already owns
(ADR 0014/0015), not intent the caller supplies.

- **The load-bearing reason: a single feature's dimensions scatter across views.** A
  hole's ⌀ callout renders in its end-on (plan) view while its axial location joins the
  front ladder (the #636 Y-hole case — same feature, two views). Grouping the API by view
  would fragment one feature across two or three `view` blocks and repeat its reference;
  grouping by feature keeps each feature's intent whole and lets each dimension route
  independently.
- **A view is still first-class — for view-level concerns**, not as an owner of
  dimensions: presence / scale, and section / detail *definition* (`sheet.view("side")`,
  `sheet.section("A-A", through=bore)`).
- **The view is a derived-with-override target**, mirroring the GD&T aspects
  (`view=` / `side=` on `sheet.control` / `sheet.finish`, ADR 0011 P2c):
  `sheet.dimension(feature, role, view="front", side="below")` is the escape hatch when the
  caller disagrees with the routing; omit it and the engine derives.
- **"Across views" holds only at declaration.** A dimension is declared once and rendered
  in exactly one view — dimensioning a feature in two views is redundant and a lint smell.
  So flat-declare + engine-route is the natural split; the emitter may still group the
  emitted `dimension` lines under per-view comment headers for readability without the API
  owning that structure.

### One name, one contract: `dimension` is referential everywhere

The referential verb is **not new** — `Drawing.dimension(feature, param, *, role=, side=,
view=, pin=, priority=)` already exists and already has this shape: it takes an IR feature
plus a parameter kind, derives the value from the feature (`_derive_span`), carries no
number, places into free strip space, tags the annotation by feature, and pairs with
`Drawing.drop(feature)` / `annotations_of(feature)`. That is this ADR's verb, built, on the
Drawing surface. What is missing is the *Sheet-side twin*, the planner intent input, and
the emitter mirror — not the verb itself.

The obstacle is a collision that already exists today: **`dimension` means opposite things
on the two user-facing surfaces.** On `Drawing` it is referential (above); on `Sheet` it is
`dimension(*, kind, value, label, dominant_axis, ref_pts, ref_bbox=, at=, …)` — the
materialized AP242 form that carries the number and, optionally, a page position.

**Decision: `dimension` means referential on both surfaces. The materialized Sheet verb is
renamed to `measured_dimension`, and `model.declare.authored_dimension` renames in step so
one vocabulary spans the façade and the model layer.** The name says what distinguishes it:
a source already measured this, and the script restates the measurement.

Three reasons this is the right end state rather than adding a distinct name such as
`sheet.dim`:

- **The project already took this decision on the other surface.** #817 privatized
  `Drawing.place_dim` → `_place_dim`: the coordinate-carrying form became the deprecated
  escape hatch and the referential verb became the public default. `Sheet` simply has not
  caught up. Renaming applies an existing decision consistently instead of opening a second
  one.
- **A short alias would entrench the collision.** `sheet.dim` alongside `sheet.dimension`
  would leave three verbs under two names, where `sheet.dim` equals `Drawing.dimension`
  while `sheet.dimension` means something else — the least learnable of the options.
- **The cost is at its minimum now.** The package is alpha (`Development Status :: 3 -
  Alpha`), and the affected callers are *generated* AP242 scripts, which regeneration
  fixes. A `@deprecated` (PEP 702) shim carries the old name for one release and is removed
  at 0.4.0 alongside the ADR 0005 §4 alias removals (#720) — the shim eases the transition
  without changing the end state.

`Sheet.dimension` is entirely keyword-only today (it accepts no positional arguments), so a
positional `(feature, role)` referential form could also be *overloaded* onto it without any
break. That was considered and rejected: it preserves one verb with two contracts
indefinitely, which is the ambiguity this decision exists to remove. It remains the
fallback if the rename proves disruptive in practice.

### The script records intent, not what got placed

A `dimension(...)` line states *that this measurement matters*. Whether it lands on the sheet
is a downstream outcome of the ADR 0014 solve, which may drop a candidate for want of
room and say so in lint. **The line stays in the source either way** — including when
the author already knows the sheet is crowded enough that it may not fit. A dropped
dimension is a `placed = False` outcome on a line that is still there, not a missing
line.

Two consequences follow, and they are load-bearing:

- **The emitter generates from the planner's intent set, never by walking the drawing's
  placed annotations.** Walking annotations is the obvious way to build a mirror and it
  is wrong: a dimension the solver dropped would vanish from the regenerated script, so
  re-running could never recover it, and an unrelated layout change would silently
  rewrite the author's source. Emitting from intent makes the script stable under layout
  churn — which is what makes it safe to keep in version control.
- **Absence of a line unambiguously means "suppressed".** The mirror is over the
  planner's intents, whose key space is `(feature, DimParameter role)` — exactly
  `dimension(...)`'s key space. The emit vocabulary is therefore complete over the thing being
  mirrored *by construction*, so a missing line can never mean "the emitter had no way to
  say this". (The one exception is materialized imported PMI; see Consequences.)

### Constraints this forces (the honest edges)

- **Auto dimensions must be semantically nameable.** Suppression and override require a
  stable identity for "the location dimension of hole H" that survives a re-solve at a
  different scale. That identity is the planner's `(feature, DimParameter role)`, not a
  page-keyed annotation name — this leans on ADR 0010 provenance and the ADR 0015
  planner keeping parameter roles stable. Additive intent needs no such key; **suppressive
  intent is gated on it.**
- **Honest reconciliation needs the full recompose (#426/#707).** Suppressing or
  re-emphasizing an *automatic* dimension means the finalize path must reconstruct the
  automatic candidate population and co-solve it with the declared intents — the global
  recompose ADR 0012 Amendment 1 records as still open. Until it lands, `Drawing.finalize()`
  drains *recorded* intents against already-committed annotations as obstacles; it does not
  reconcile against the auto-plan. So **additive intent is reachable on today's machinery
  (it is a new candidate); suppressive / full-mirror intent depends on #426/#707.** This
  ADR therefore *motivates* completing that recompose rather than routing around it.
- **Intent stays declarative and order-independent.** Two intents competing for one span
  dedup like coincident auto candidates; ties break by deterministic key (ADR 0001). An
  infeasible intent (off page) drops with lint like any candidate — declaring a dimension
  is a strong request, not a bounds override.
- **The emitter only mirrors intent it can round-trip.** The self-describing comment stays
  the honest floor: the generated script gains dimensioning-intent lines only for the
  intents the engine can faithfully re-solve, never decorative lines that re-run cannot
  reproduce (the same fidelity contract `emit_sheet_script` holds for features).

## Worked example — the mounting plate

> **This example is the END STATE — the script as it reads once phase 5 has landed.**
> It is not today's API and will not run against the current release: `sheet.dimension(...)`
> does not exist yet, `sheet.view(...)` does not exist at all, and `sheet.section(...)`
> is shown in a proposed future form (see Consequences). The feature lines
> (`hole`, `spotface`, `envelope`) are real today.

An 80 × 50 × 8 plate: a central ⌀20 bore with a ⌀32 × 1.5 spotface (which auto-triggers
section A–A) and four ⌀5 corner holes. Today `emit_sheet_script` writes the four
*features* and leaves the dimensions, views, and section as comments. Under this ADR the
emitter mirrors the drawing as **referential dimension lines** — every callout on the
sheet is one line, and no line restates a number:

```python
from draftwright import Sheet
from build123d import import_step

part = import_step("plate.step")
sheet = Sheet(part, title="PLATE", number="DWG-001")

# ── Features — geometry; each size lives here, once ────────────────────────────
corners = sheet.hole(diameter=5,  at=(-32, -18, 4), axis="z", count=4, members=[...])
bore    = sheet.hole(diameter=20, at=(0, 0, 4), axis="z").spotface(diameter=32, depth=1.5)
env     = sheet.envelope()                     # the overall bounding geometry

# ── Dimensions — each REFERENCES a feature; no numbers restated ────────────────
sheet.dimension(bore,    "diameter")    # ⌀20 THRU ⌴ ⌀32 ↓ 1.5   (read off `bore`)
sheet.dimension(corners, "diameter")    # 4× ⌀5 THRU              (read off `corners`)
sheet.dimension(corners, "location")    # the hole-location ladder      ← comment out to drop
sheet.dimension(bore,    "location")    # bore on centre
sheet.dimension(env,     "width")       # 80    (read off the bbox)
sheet.dimension(env,     "depth")       # 50
sheet.dimension(env,     "height")      # 8     (thickness)
# sheet.dimension(corners, "pitch")     # ← uncomment to ADD a 64 × 36 grid-pitch dim

# ── Views & section — editable lines; the engine still packs them ──────────────
sheet.view("front"); sheet.view("plan"); sheet.view("side"); sheet.view("iso")
sheet.section("A-A", through=bore)    # cut the spotfaced bore (else auto-triggered)

sheet.export("plate")
```

Reading it against the rendered sheet:

- **`⌀20` appears once.** The number lives on the `bore` feature line; `sheet.dimension(bore,
  "diameter")` only says *show `bore`'s diameter callout*. Change `diameter=20` → `25`
  (or edit the build123d object, for a live part) and the callout follows — there is no
  second copy to keep in sync.
- **Dropping a dimension is not dropping the hole.** Comment out `sheet.dimension(corners,
  "location")` and the location ladder vanishes; the four ⌀5 *circles* stay, because they
  are geometry projected from the part, not annotations. Geometry and dimensions are
  separate layers — only editing the part removes a hole.
- **The commented `pitch` line is the additive case** — a measurement the planner did not
  place, added by reference and still ordered / placed by the corridor solve.
- **A line the solver cannot fit still stays in the script.** If the sheet is too crowded
  for, say, the bore's location ladder, that dimension drops with a lint warning and
  `sheet.dimension(bore, "location")` remains exactly where it is — the source records the
  intent, and a later scale or page change can make it fit again with no edit.

The A/B "features imply dimensions" vs "every dimension is a line" fork explored during
design collapses here: the referential form gives the completeness of the second with the
single-source-of-truth of the first.

## Consequences

- One referential `sheet.dimension(<feature>, <role>)` verb on `Sheet` / `Drawing` (exact role
  names deferred to the roadmap), reading its value from the referenced geometry and
  carrying none itself; `.pin()` / `.priority()` chain onto it as the emphasis face.
  Suppression needs no separate verb — a surfaced `dimension` line commented out is the drop.
- **`dimension` means *referential* on both surfaces; the materialized verb is renamed to
  `measured_dimension`.** See "One name, one contract" below — the Sheet-side verb is a
  rename, not a new name, and `Drawing.dimension` already has the target shape.
- `plan_dimensions` (ADR 0015) grows an intent input: declared additive measurements join
  the planned `DimensionGroup`s; declared suppressions filter them. The corridor solve
  (ADR 0014) is unchanged — it still receives one candidate population per strip.
- `intents.py` (ADR 0012) is the recording home; `Drawing.finalize()` /
  `_PASS_SEQUENCE` the drain. Additive intent lands there first; suppression follows the
  #426/#707 recompose.
- `sheet_emit` gains a dimension-mirroring pass: after the feature basis, emit one
  referential `dimension(...)` line per **planned** dimension (plus views / sections,
  ADR-0016-adjacent config work) — each commentable and editable, none restating a number,
  with low-level furniture still produced automatically by the engine on re-run. The pass
  reads the planner's `DimensionGroup`s, **not** the drawing's placed annotations, so a
  solver-dropped dimension keeps its line (see "The script records intent").
- **Imported AP242 PMI stays materialized.** A PMI-sourced dimension carries `ref_pts` /
  `ref_bbox` / `at` and has no `(feature, role)` referential form, so it is emitted as
  `sheet.measured_dimension(...)` (the renamed materialized verb) rather than as a
  referential line. It is still one editable line in the script, so the intent-mirror property holds; the two verbs stay
  distinct precisely because one references and the other restates.
- **`sheet.section(...)` would change shape.** The example's named, feature-targeted form
  (`sheet.section("A-A", through=bore)`) is *not* the verb shipped in v0.3.9 (#847),
  which is `section(feature=None, *, at=None)` — unnamed, single-section. Multi-section
  naming is a proposed extension this ADR implies but does not itself decide; it needs
  its own compatibility call (overload versus rename) before phase 5. `sheet.view(...)`
  is wholly new.
- Extends ADR 0011 (declare features) to declare *dimensioning intent*; extends ADR 0012
  (edit one dimension) to declaring the dimension *set*; consumes ADR 0015 (planner) and
  ADR 0014 (solve); fulfils ADR 0001 §2. Does **not** reintroduce the ADR 0001 hardcoded
  DSL — intent carries no geometry.

## Proposed phased work

1. **Additive intent, feature-referenced.** A scale-independent additive-measurement
   intent (span-between-features, pitch, thickness, overall) recorded on the model and
   entered as a `CorridorCandidate`; reachable on today's solve. Reuses / narrows
   `authored_dimension` so intent and materialized-PMI stay distinct.
2. **Semantic identity for auto dimensions.** Expose a stable `(feature, role)` handle for
   planned dimensions (ADR 0010 provenance + ADR 0015 roles) so intent can *reference* an
   auto dimension.
3. **Suppression by omission.** A surfaced referential `dimension` line, when commented out,
   filters that `(feature, role)` from `plan_dimensions` output — no separate verb.
4. **Full recompose (#426/#707).** Reconstruct the automatic population at finalize and
   co-solve with declared dimensions, making suppression / emphasis honest and
   script/direct output convergent.
5. **Emitter dimension-mirror.** Emit one round-trippable referential `dimension(...)` line per
   placed dimension (with views / sections); keep the self-describing comment as the floor
   for anything not yet mirrorable.

## Open questions

- *(Settled — see "One name, one contract": `dimension` is referential on both surfaces;
  the materialized Sheet verb becomes `measured_dimension`, with `authored_dimension`
  renamed in step and a one-release `@deprecated` shim.)* What remains open is only the
  spelling: `measured_dimension` versus a shorter `measured`, and whether the model-layer
  constructor keeps a `_dimension` suffix the façade drops.
- The `role` vocabulary for `sheet.dimension(feature, role)`: which measurements to support
  first (`"diameter"`, `"location"`, `"pitch"`, `"width"`/`"depth"`/`"height"`, `"angle"`,
  `"radius"`), and how a *two-feature* span reads fluently — `sheet.dimension(a, b)` /
  `sheet.dimension((a, b), "span")` vs a feature-handle method.
- How `sheet.dimension(...)` composes with `of(...)` on a detected model (referencing an
  auto-detected feature by object/index to drop or emphasise its dimension), and whether a
  bare `sheet.dimension(feature)` (no role) means "all of that feature's dimensions".
- Guards to add when this lands: a fidelity test that an emitted `dimension` line re-solves to
  the same dimension (mirroring `test_sheet_emit` parity), and an audit that a `dimension` line
  carries no number or page geometry (the reference-not-restate / scale-independence
  invariant).
