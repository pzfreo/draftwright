# ADR 0016 — Declared dimensioning intent: capture what to measure, let the engine place it

- **Status:** Accepted
- **Date:** 2026-07-26 (accepted 2026-07-27; implementation epic **#867**)
- **Deciders:** Paul Fremantle (pzfreo)

> **Scheduling reality (corrected 2026-07-27).** Phase 1 (identity + `add_dimension`)
> is reachable on today's machinery. Phases 2–4 were written as sitting behind "the
> global recompose (#426/#707), the longest-open item on the roadmap" — **that premise
> was stale when this ADR merged.** #426 and #661 closed 2026-07-19, #707 on 07-21, all
> as completed; there is no open recompose issue.
>
> Closed-as-umbrella is not the same as capability-landed, so the blocker is now an
> open question rather than a stated fact (#867): #426's closing comment says the parity
> work "remains observably incomplete", the #743 parity suite is skipped with strict
> xfails underneath, and `drain_and_reconcile` solves registered corridors and reconciles
> witness labels without reconstructing the automatic candidate population. There is also
> a cheaper question inside it — **suppression by omission in a `Sheet` script happens
> before `build()`**, when the set is known at plan time, so it may need no recompose at
> all; only `Drawing.finalize()` dropping an *automatic* dimension does. Phase 2 may
> therefore be substantially less blocked than this ADR assumed. The constraint below is
> flagged in step.

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

The referential form itself is **not new — only its absence from the declarative surface
is.** The imperative emitter (`--style imperative`, `builder._feature_listing`) already
writes a referential reconstruction — `dwg.dimension(f, "length", role="step_height")`,
`dwg.callout(f)`, `dwg.locate(f)` inside `with dwg.deferred():` over a
`build_drawing(..., auto_dims=False)` build. Each line names a feature and a role and
carries no number. So this ADR generalises proven ground to the `Sheet` façade rather than
inventing a mechanism (see "One generated output").

The missing axis sits between (1) and (3): a way to declare **which measurements the
drawing should carry** — expressed in feature/world terms, never in page coordinates or
strip assignments, and routed through the same planner and corridor solve as the automatic
dimensions. Today you cannot declare "dimension the pitch between these two bosses", "give
this wall an overall-thickness dimension", or "do **not** auto-dimension this hole's
location" as *intent* and let the engine place the result.

The near-miss primitive is today's `sheet.dimension(...)` (`model.declare.authored_dimension`),
used for imported AP242 PMI. But it is *materialized* — it carries `ref_pts` / `ref_bbox` /
`at` — so it leans toward the hardcoded-geometry form ADR 0001 rejected. It is the escape
hatch for "a source already measured this", not the intent layer for "this measurement
matters".

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
naming a feature (or the envelope) and the measurement to show, and **carrying no number**.
That verb name is a decision, not a placeholder: `dimension` means *referential* on both
`Sheet` and `Drawing`, and today's materialized Sheet verb is renamed to
`measured_dimension` to free it (see "One name, one contract"). The nominal value is always
read from the referenced geometry, so a size lives in exactly one place — the feature
declaration (a detected snapshot for a STEP part, the build123d object for a live one) — and
a dimension can never drift from it. That is what dissolves the dual-source-of-truth
objection to a per-dimension mirror: the mirror is single-source because the lines reference
rather than restate (how *complete* it is, is bounded — see "What the mirror does and does
not cover"). One referential contract covers three behaviours, all scale- and
placement-independent (anchored in feature / world terms, per the ADR 0012 constraint):

- **Surface & drop** — the emitter emits one `dimension(...)` line per dimension the drawing
  carries, so the script mirrors the sheet; commenting a line out **suppresses** that
  dimension, keyed by its semantic dimension identity (see "Dimension identity"), never a
  page name or coordinate.
- **Add** — a measurement the planner would not auto-produce (a span between two features, a
  pattern pitch, a wall thickness, an angle) enters the shared solve as a new
  `CorridorCandidate`. This is a **second verb, `add_dimension(...)`**, sharing the
  referential contract and the handle but not the meaning: it *augments* the planner's set,
  where `dimension(...)` *is* the set. Splitting the two is what keeps omission unambiguous.
- **Emphasise** — `.pin()` / `.priority()` chained onto either line ranks or anchors it in
  the solve (already ADR 0012), without fixing a coordinate.

The `.thread` / `.finish` aspect is the template and the invariant: a `dimension` line edits
the dimension *set* the drawing carries; the corridor solve still decides ordering, dedup,
priority-drop, and position. **A dimension line references; the engine places.**

Only *dimensions* are surfaced as lines. Low-level furniture the engine derives — centre
marks, section arrows, hatching, the NTS caption — stays automatic; surfacing each would
explode the mirror without adding any editable intent.

### What `dimension(...)` returns: the dimension-intent handle

The referential verb returns a **`DimensionIntent` handle**, not `Sheet`, so `.pin()` and
`.priority()` chain — `sheet.dimension(bore, "location").pin().priority(2.0)`. This follows
the pattern the façade already uses for anything carrying aspects (`hole` → `_Hole`,
`diameter` → `_Dim`, `slot` → `_Params`) while verbs with nothing to decorate (`chamfer`,
`fillet`, `plate`) return `Sheet`.

The handle is the one place the dimension's semantic identity lives, and what the rest of
the design keys on:

- it carries the `DimensionId` defined in the next section — the identity used for
  suppression, for the planner input, and for matching an emitted line back to its intent;
- it is the ADR 0010 provenance anchor: intent → the annotation names the render seam
  produced, so `drop` / `annotations_of` resolve through it;
- it is *not* a placement handle. It exposes no coordinate, no strip, and no view
  assignment beyond the derived-with-override `view=` / `side=` arguments.

One transitional wart follows: during the migration release the single `dimension` verb
returns a handle for a referential call and `Sheet` for a legacy materialized one. That
ends at 0.4.0 when the legacy branch is deleted.

### Dimension identity: `(feature, role)` addresses a dimension; it does not key one

Suppression, dedup, provenance and the emitter mirror all key on a dimension's identity, so
the identity model has to be exact — and **`(feature, role)` is not it**. It is how a caller
*addresses* a dimension; the key underneath needs more. `model/ir.py` shows three tiers of
same-role collision, and they are not the same problem:

1. **Same role, different kind — `kind` resolves it.** A blind hole carries
   `DimParameter("diameter", "bore", …)` *and* `DimParameter("depth", "bore", …)`;
   counterbore, spotface and countersink each carry a diameter + depth (or angle) pair under
   one role. `(feature, kind, role)` separates these.
2. **Same role *and* kind, semantically distinct — needs a discriminator.** A grid pattern
   emits two `DimParameter("length", "grid_pitch", …)`: the row pitch and the column pitch.
   No combination of `kind` and `role` tells them apart. This is the case that forces a third
   component into the key.
3. **Same role and kind, deliberately *one* addressable thing — needs nothing.** `FaceLevels`
   emits one `step_height` per level and one `step_position` per shoulder; `Rotational` emits
   one `bore` diameter per concentric bore. These are the correlated sets ADR 0015 routes
   through whole-model passes, and `ir.py` says so at the source: *"correlated SETS routed as
   a whole … a single `role=` intent rebuilds the whole ladder."* Here the addressable unit
   **is** the set, so the role already is the whole identity — and minting per-member keys
   would be actively wrong, because the grouped-drop edge below says commenting one member
   still redraws the group. *Provisional for one case:* `Rotational`'s OD/bore group was
   the residual planner-coverage debt ADR 0015 tracked as **#754** — now closed
   (2026-07-22), so rotational diameters route through planner output and whether its
   bores stay one identity or split into addressable members is settled when the
   addressable units are built (#870), not here. The ladders are definite.

So identity is per **addressable dimension**, which is a planner-level notion, not a raw
`DimParameter` count. That unit has to be **first-class in the model, not inferred from key
collisions** — otherwise nothing can distinguish an intentional correlated set from an
accidental duplicate, and every consumer (dicts, provenance, suppression, the uniqueness
audit) has to guess.

**It lives on the planner's output, not on the IR.** `DimParameter` is what a feature knows
about itself; by the time anything can be suppressed, deduped or emitted, the planner has
enriched each parameter into a `PlannedDimension` carrying `convention`, `suppressed`,
`reason`, `datum` and the provenance feature. Grouping raw `DimParameter`s would either lose
that metadata or force a second, parallel grouping after planning — so the addressable unit
holds `PlannedDimension`s, and `DimensionGroup.dims` is where it goes:

```python
@dataclass(frozen=True)
class AddressableDimension:
    id: ParameterId                        # "bore.diameter", "grid.pitch.x", "step_height"
    members: tuple[PlannedDimension, ...]  # usually one; N for a correlated set


@dataclass(frozen=True)
class DimensionGroup:                      # existing type, one field retyped
    feature: Feature
    view: str
    dims: tuple[AddressableDimension, ...]  # was: tuple[PlannedDimension, ...]


@dataclass(frozen=True)
class DimensionId:
    feature: FeatureId
    parameter: ParameterId                 # the AddressableDimension's id
```

**One type at one boundary, not two.** An IR-side `ParameterGroup` paired with a
planner-side unit was the alternative; it has nothing to hold, because grouping is a
*planner* decision (below) — `Feature.parameters()` returns a flat list and should keep
doing so. The migration cost is real and worth naming: retyping `dims` touches ~25 read
sites across `from_model.py` / `holes.py` / `compose.py`, nearly all of the shape
`next(pd for pd in g.dims if …)`. A flattening accessor keeps that mechanical — readers
that do not care about grouping never learn about it.

Most addressable dimensions hold exactly one member. A correlated set holds N, and those
members are **not** separately addressable — that is the whole content of tier 3, now stated
in the model rather than as a caveat about the key. The planner declares the grouping when
it builds the addressable set; it is never deduced from two parameters happening to share a
derived key.

`ParameterId` is a readable semantic string, not an opaque token: it appears in diagnostics,
lint messages and snapshots, and is stable across re-detection and planner changes. Two
candidates are **rejected**: the parameter's *list position* (any reorder silently repoints
every intent) and a random UUID (unreadable in a diff, unstable across runs — what makes a
script safe to version-control is exactly that its keys do not churn).

**It is derived, not hand-authored** — composed as `role` + `kind` + an optional semantic
discriminator, so the forty `DimParameter(...)` construction sites do not each grow a
literal that can drift from the `role` beside it. Only the discriminator is new data, and
only tier 2 needs one (`axis="x"` / `"y"` on the grid pitches, today's sole instance). An
explicit `id=` field on every parameter was the alternative; it is more direct, but restates
`(kind, role)` wherever it adds nothing, and a site whose `id=` disagrees with its `role=`
is a new class of silent bug. Derived keeps one source of truth — the same argument this ADR
makes about numbers. Deriving the key is safe *because* grouping is declared separately: two
parameters landing on the same derived id is an error the audit catches, never a silent
merge into a set.

**The selectors stay clean**, with the discriminator surfacing as a keyword only where it is
needed:

```python
sheet.dimension(hole,    "diameter")            # -> DimensionId(hole,    "bore.diameter")
sheet.dimension(hole,    "depth")               # -> DimensionId(hole,    "bore.depth")
sheet.dimension(pattern, "pitch", axis="x")     # -> DimensionId(pattern, "grid.pitch.x")
sheet.dimension(pattern, "pitch", axis="y")     # -> DimensionId(pattern, "grid.pitch.y")
sheet.dimension(steps,   "step_height")         # -> the whole ladder, one identity
```

Omitting a needed discriminator is an error, not a guess: `dimension(pattern, "pitch")` on a
grid raises and names the two choices, rather than silently picking one.

**Inter-feature measurements get their own identity, not a borrowed one.** A distance, angle
or offset between two features belongs to neither; forcing it into one feature's parameter
space would make the key depend on which subject the caller named first:

```python
@dataclass(frozen=True)
class RelationDimensionId:
    relation: Literal["distance", "angle", "offset"]
    subjects: tuple[FeatureId, ...]        # normalized per relation — see below
    axis: Literal["x", "y", "z"] | None = None
```

**Subject normalization is relation-specific, not a blanket sort.** A distance is symmetric,
so `(a, b)` and `(b, a)` must key once and sorting is right. A *signed* offset and an
oriented angle are not: reordering their subjects negates the measurement, so sorting them
would silently key two different dimensions as one. Each relation therefore declares its own
normalization — symmetric relations canonicalize by sort, oriented ones preserve subject
order as part of the identity.

This makes the "not every dimension is nameable" edge below a *bounded* gap rather than an
open one: inter-feature spans are nameable, just not as `(feature, role)`.

**Feature identity is a separate sub-problem this ADR does not solve.** There is no
`FeatureId` in the IR today — the plan carries features by object (`DimensionGroup.feature`)
— and within one script run that is sufficient: the script holds the variable, so
`sheet.dimension(bore, "diameter")` resolves by object identity with no key at all. A
*durable* `FeatureId` is only needed where an intent must survive re-detection, which is the
`of(...)` question left open below. So `FeatureId` above reads as "whatever identifies a
feature", not as a new type this decision mints.

**The governing principle**, which is what keeps this stable as the planner evolves:
*identity describes the engineering measurement, not the annotation or the planning pass
that produced it.* `role` and `kind` remain useful metadata — grouping, presentation,
renderer routing — but the identity is the measurement.

### Identity-layer grouping is not render-layer grouping

These are easy to conflate, and an earlier draft did. A hole's bore ⌀, depth, counterbore,
spotface and countersink parameters all collapse into **one** `HoleCallout` — `⌀20 THRU ⌴
⌀32 ↓ 1.5` is a single annotation (`from_model.hole_callout_spec`). By the governing
principle above that is a *rendering* aggregation of several addressable dimensions sharing
one leader, **not** one addressable dimension:

```python
sheet.dimension(bore, "diameter")           # ⌀20
sheet.dimension(bore, "spotface_diameter")  # ⌴ ⌀32     — three lines,
sheet.dimension(bore, "spotface_depth")     # ↓ 1.5       one rendered callout
```

Dropping the spotface lines leaves `⌀20 THRU`; the callout builder already takes `None` for
every segment. So the model needs both notions: a correlated set is **one** identity with N
members, a compound callout is **N** identities sharing one annotation.

### Suppression changes what is shown, never what the drawing asserts

`hole_callout_spec` today derives `through = depth is None` — a blind hole's depth parameter
is the *only* signal that it is blind. Filter that parameter out to honour an authored set
and the callout reads `THRU`: a display choice has silently changed a manufacturing fact.
This is a live defect independent of this ADR (`HoleFeature.through` is a first-class IR
field and the group carries the feature, so the renderer is re-deriving what it can simply
read), and it generalizes into a rule this decision depends on:

> **No renderer may infer an engineering fact from the presence or absence of a dimension
> parameter.** Parameters carry values *for display*; facts live on the feature.

Two mechanics follow.

**Suppression marks, it does not filter.** `PlannedDimension` already carries `suppressed` /
`reason` and thirteen render sites already honour it, so a dimension omitted from an authored
set is *marked* and the group keeps its full engineering data. The compound-callout path
(`_first` / `hole_callout_spec`) is the one reader that ignores the flag today; closing that
gap belongs to the suppression phase rather than to implementation discretion.

**The head of a compound callout is a declared dependency.** The bore ⌀ is the callout's head
— `hole_callout_spec` returns nothing without it — so suppressing it while spotface or
counterbore intents remain is an authoring error and **raises**, naming the dependency. Not
lint-and-drop (silently discards authored intent), not implicit restore (makes the script say
something it does not say). So "every segment separately suppressible" holds *except for the
head* — an asymmetry the addressable unit declares, rather than one scattered through the
renderer.

### Two explicit sources of dimension intent

"Absence of a line means suppressed" cannot hold on its own: every existing script contains
no referential dimension lines and relies on automatic planning, so the engine could not
tell a legacy script asking for normal dimensions from a new one deliberately suppressing
every dimension. **Omission is only meaningful inside a set declared complete.** So the
dimension set has exactly two sources, and the script always says which:

```python
dwg = Sheet(part).auto_dimensions().build()   # the planner-selected set, requested

sheet = Sheet(part)                           # or: these declarations ARE the set
sheet.dimension(bore, "diameter")
sheet.dimension(bore, "location")
dwg = sheet.build()
```

- `auto_dimensions()` requests the planner-selected set (ADR 0015 `plan_dimensions`);
  `dimension(...)` declarations form the **complete** authored set — which is what makes
  omission mean suppression, with no hidden mode flag.
- **Mixing the two raises** (`ValueError: cannot mix automatic and authored dimension
  sets`), because a reader could no longer tell a complete authored set from an automatic
  plan plus one extra request.
- **Augmenting the automatic plan is `add_dimension(...)`** — a distinct verb, so the
  distinction stays visible at the call site. It shares the referential contract exactly
  (names a feature and a role, carries no number, returns a `DimensionIntent`, chains
  `.pin()` / `.priority()`) and differs only in what its presence claims: it adds one member
  to a set the planner still owns, where `dimension` declares the set.

  ```python
  sheet.auto_dimensions()
  sheet.add_dimension(pattern, "pitch", axis="x")   # planner's set + the row-pitch dim
  ```

  It **requires** `auto_dimensions()` — there is nothing to augment in an authored set — so
  `add_dimension` without it raises the same way mixing does. Overlap with something the
  plan already covers is *not* an error: the identity key makes it idempotent (see
  "Preventing duplicate dimensions"). What raises is reusing the *same* verb for both
  sources. This split is also why `add_dimension` is the verb the phasing ships first: a
  verb must not mean "augment the plan" in one release and "be the plan" in the next.
- **A build that requested neither raises** — `Sheet(part).build()` does not silently
  produce an undimensioned drawing (`ValueError: no dimension set requested — call
  auto_dimensions() …`). This is the one point where explicitness alone is not enough: a
  silently undimensioned sheet is *plausible-looking and unbuildable*, the "clean but
  incomplete drawing" this project ranks as worse than a visible failure and the same case
  the completeness lint (#632) exists to stop passing. It is consistent with #631 (a boss
  and coincident step raise rather than silently dropping a height) and #630 (an unplaceable
  detail reports rather than no-ops), and costs nothing in practice — the emitter always
  writes one of the two forms.

A third design is rejected outright: **implicit-by-usage** — "if the script declares any
dimension, the automatic set turns off". It needs no flag and reads cleanly, but a
hand-author who adds one pitch dimension would silently lose every ⌀ callout on the sheet.
Action at a distance from a line that looks additive is worse than an explicit source.

This is a **breaking change**: `Sheet(part).build()` is automatic today. The package is
alpha with few users, and the break buys an unambiguous omission rule with no hidden mode,
no parallel surface, and natural handling of the empty set. It ships in the same window as
the `dimension` → `measured_dimension` rename so callers absorb one migration, not two.

### Preventing duplicate dimensions

The two-source rule stops the ambiguous case at the door, but duplicates can still arise
within a set — two authored declarations of the same span, or an `add_dimension(...)`
augmenting something the plan already covers. Three layers handle it, and only the middle
one exists today:

1. **`DimensionId` identity** — the handle's key (see "Dimension identity") makes a repeated
   declaration *idempotent* rather than doubled. Load-bearing rather than optional, because
   `CorridorCandidate.dedup` is documented as `None` for **size dims**, so a ⌀ callout does
   not participate in coincidence dedup at all.
2. **Coincident-span dedup** — `CorridorCandidate.dedup`, the coincidence key
   `(view, meas-origin, meas-endpoint)` on the measured axis, with `precedence` ranking the
   survivor and displaced candidates tracked so none starves. **Exists today**; catches two
   different features measuring the same span.
3. **A redundancy lint** — for over-dimensioning that is neither identical nor coincident,
   e.g. carrying both a pattern's per-hole locations *and* the pitch that determines them.
   **This does not exist**: the current codes are `*_dropped` and `feature_not_dimensioned`;
   nothing reports duplicate or redundant dimensioning. Adding it is part of this work.

### Dimensions are sheet-level; the view is derived placement

The API is **flat** — `sheet.dimension(<feature>, <role>)` on the sheet — never
`sheet.view(v).dimension(...)`. A dimension's identity is its `DimensionId` — feature plus
parameter — and carries **no view**; the view it renders in is a placement the engine
already owns (ADR 0014/0015), not intent the caller supplies.

- **The load-bearing reason: a single feature's dimensions scatter across views.** A
  hole's ⌀ callout renders in its end-on (plan) view while its axial location joins the
  front ladder (the #636 Y-hole case — same feature, two views). Grouping the API by view
  would fragment one feature across two or three `view` blocks and repeat its reference;
  grouping by feature keeps each feature's intent whole and lets each dimension route
  independently.
- **A view is still first-class — for view-level concerns** (presence / scale, section and
  detail *definition*), just not as an owner of dimensions. That surface is out of scope
  here.
- **The view is a derived-with-override target**, mirroring the GD&T aspects
  (`view=` / `side=` on `sheet.control` / `sheet.finish`, ADR 0011 P2c):
  `sheet.dimension(feature, role, view="front", side="below")` is the escape hatch when the
  caller disagrees with the routing; omit it and the engine derives.
- **"Across views" holds only at declaration.** A dimension is declared once and rendered in
  exactly one view; dimensioning a feature in two views would be redundant. So flat-declare
  + engine-route is the natural split — and the emitter may still group emitted lines under
  per-view comment headers for readability without the API owning that structure.

### One name, one contract: `dimension` is referential everywhere

**`dimension` means *referential* on both `Sheet` and `Drawing`; the materialized verb is
renamed to `measured_dimension`, with `model.declare.authored_dimension` renamed in step so
one vocabulary spans the façade and the model layer.**

The referential verb is **not new**: `Drawing.dimension(feature, param, *, role=, side=,
view=, pin=, priority=)` already has exactly this shape — takes an IR feature plus a
parameter kind, derives the value (`_derive_span`), carries no number, places into free
strip space, and pairs with `Drawing.drop(feature)` / `annotations_of(feature)`. What is
missing is the *Sheet-side twin*, the planner intent input, and the emitter mirror.

The obstacle is a collision that exists today: **`dimension` means opposite things on the
two user-facing surfaces.** Both of these are real calls from the current test suite —

```python
dwg.dimension(step, "length", role="step_position")     # REFERENTIAL: which measurement
sheet.dimension(kind="linear", value=40, label="40",    # MATERIALIZED: 40, here
                dominant_axis="X", ref_pts=[(-20, 0, 0), (20, 0, 0)], upper_tol=0.1)
```

— and after the rename the second becomes `sheet.measured_dimension(...)`, unchanged in
shape, while `sheet.dimension(bore, "diameter")` becomes the Sheet-side twin of the first.
The emitter change is one string (`"sheet.dimension("` → `"sheet.measured_dimension("`);
generated AP242 scripts pick it up by regeneration.

**The shim must be a transitional overload, not a `@deprecated` wrapper.** Because the name
is *reused* rather than retired, an old keyword call after the rename would raise
`TypeError`, not warn. So for one release `Sheet.dimension` dispatches on how it was called:

```python
def dimension(self, feature=None, role=None, /, **kw) -> DimensionIntent | Sheet:
    if feature is None and kw:              # legacy materialized call
        warn("Sheet.dimension(kind=…, value=…) is deprecated: use "
             "measured_dimension(). Removed at 0.4.0.", DeprecationWarning)
        return self.measured_dimension(**kw)     # -> Sheet
    ...                                     # referential intent -> DimensionIntent
```

Why rename rather than add a distinct name such as `sheet.dim`:

- **The project already took this decision on the other surface.** #817 privatized
  `Drawing.place_dim` → `_place_dim` with a deprecation message naming
  `Drawing.dimension(feature, param, …)` as the replacement and `place_dim` as "only a raw
  page-coordinate escape hatch". The referential verb is already the public default on
  `Drawing`; `Sheet` has not caught up. This applies an existing decision rather than
  opening a second one.
- **A short alias would entrench the collision** — three verbs under two names, with
  `sheet.dim` equal to `Drawing.dimension` while `sheet.dimension` means something else.
- **The cost is at its minimum now**: alpha package, and the affected callers are
  *generated* AP242 scripts that regeneration fixes. The shim retires at 0.4.0 alongside the
  ADR 0005 §4 alias removals (#720).

**Rejected: making that overload permanent.** `Sheet.dimension` is entirely keyword-only
today, so a positional `(feature, role)` form could be overloaded onto it forever — no
rename, no shim, no regeneration. It is cheaper, and rejected anyway, because the cost lands
on *reading* a script rather than writing one: a line reading `sheet.dimension(...)` would
tell the reader nothing about whether its number is derived from the geometry or hardcoded
in the file. That is precisely the distinction this ADR exists to make legible, and it would
stay invisible exactly when a reader is scanning a generated script deciding what is safe to
edit. `measured_dimension` reads as a flag: *this line carries a number that will not follow
the geometry.* The adopted shim is the same mechanism — the difference is only that it
expires.

### The script records intent, not what got placed

A `dimension(...)` line states *that this measurement matters*. Whether it lands on the sheet
is a downstream outcome of the ADR 0014 solve, which may drop a candidate for want of room
and say so in lint. **The line stays in the source either way** — a dropped dimension is a
`placed = False` outcome on a line that is still there, not a missing line. Two load-bearing
consequences follow:

- **The emitter generates from the planner's intent set, never by walking the drawing's
  placed annotations.** Walking annotations is the obvious way to build a mirror and it is
  wrong: a dimension the solver dropped would vanish from the regenerated script, so
  re-running could never recover it, and an unrelated layout change would silently rewrite
  the author's source. Emitting from intent makes the script stable under layout churn —
  which is what makes it safe to keep in version control.
- **Inside the mirrored set, absence of a line means "suppressed".** The mirror is over the
  planner's intents, whose key space is `DimensionId` — exactly `dimension(...)`'s key
  space, so over *that* space the emit vocabulary is complete by construction and a missing
  line cannot mean "the emitter had no way to say this". What bounds the claim is the
  boundary of the mirrored set, not the emitter's vocabulary.

### What the mirror does and does not cover

The mirror is **over round-trippable, semantically identified planner intent** — not over
every mark on the sheet. Stating it that way is not a hedge; it keeps the contract from
constraining planner internals that are still moving. The gaps are known and finite:

| Not mirrored as a `dimension(...)` line | Why | Where it goes instead |
| --- | --- | --- |
| Correlated sets, per member | `step_height` / `step_position` ladders, rotational bores and off-axis `locate` are one `AddressableDimension` holding N members | **One** line per set; suppress the set, not a member |
| Inter-feature spans and angles | No `(feature, role)` form — needs `RelationDimensionId`, whose selector spelling is still open | Comment floor until the relation selector lands |
| Imported AP242 PMI | Materialized: carries `ref_pts` / `ref_bbox` / `at`, so there is nothing to reference | `sheet.measured_dimension(...)` — still one editable line |
| Low-level furniture | Centre marks, section arrows, hatching, the NTS caption carry no editable intent | Engine-automatic, by decision |
| Anything the emitter cannot re-solve | The fidelity floor `emit_sheet_script` already holds for features | Self-describing comment |

Two of those five are the identity model's boundary and shrink as it grows (relations,
future correlated-set splits); the other three are decisions, and stay. **The property the
mirror actually promises is that within the identified set, a line's presence and its
absence both mean something exact** — which is all suppression-by-omission needs.

### Constraints this forces (the honest edges)

- **Auto dimensions must be semantically nameable.** Suppression and override require a
  stable identity for "the location dimension of hole H" that survives a re-solve at a
  different scale. That identity is the `DimensionId` above, not a page-keyed annotation
  name — this leans on ADR 0010 provenance and on the ADR 0015 planner keeping parameter
  roles stable. Both intents need the key — `add_dimension` for its handle and for
  idempotence against the plan — but **suppressive intent additionally needs that identity
  to be stable across re-detection and recomposition**, which is why identity lands *with*
  the augmenting verb while suppression waits for the set boundary.
- **Honest reconciliation needs the full recompose** *(status open — see the corrected
  scheduling note above; #426/#707 are closed but the capability is unverified)*.
  Suppressing or re-emphasizing an *automatic* dimension means the finalize path must
  reconstruct the automatic candidate population and co-solve it with the declared
  intents — the global recompose ADR 0012 Amendment 1 records as still open. Note this
  applies to the **post-build** `Drawing.finalize()` path; a `Sheet` script's authored
  set is known before the solve. Until it lands, `Drawing.finalize()`
  drains *recorded* intents against already-committed annotations as obstacles; it does not
  reconcile against the auto-plan. So **augmenting intent (`add_dimension`) is reachable on
  today's machinery — it is simply a new candidate. Suppression splits into two paths that
  this ADR previously conflated:**
  - **pre-build**, a `Sheet` script's authored set — known before the solve, so it plausibly
    needs no reconstruction at all, and phase 2 can carry it;
  - **post-build**, `Drawing.finalize()` dropping an *automatic* dimension — this is what
    needs the candidate-population reconstruction.

  The second is the open question on **#867**, not a dependency on the closed #426/#707.
  This ADR therefore *motivates* settling that question rather than routing around it.
- **Intent stays declarative and order-independent.** Two intents competing for one span
  dedup like coincident auto candidates; ties break by deterministic key (ADR 0001). An
  infeasible intent (off page) drops with lint like any candidate — declaring a dimension
  is a strong request, not a bounds override.
- **Grouped passes drop set-wise, so they get one line.** `builder._feature_listing` already
  documents this for the imperative emit: commenting *some* of a ladder's lines still
  redraws the whole group. The identity model turns that wart into a rule — a correlated set
  is **one** addressable dimension (tier 3 above), so the declarative mirror emits one line
  and commenting it drops the set. That is a deliberate divergence from the imperative
  reconstruction's per-member lines, so the `--style imperative` parity gate is **coverage**
  parity, not line-for-line.
- **Referential removes dimension-vs-feature drift, not callout-vs-geometry drift.** For a
  live build123d part the feature reads its size off the object, so the chain is airtight.
  For a **detected** (STEP) part the feature line is a *snapshot* decoupled from the imported
  solid: editing `diameter=20` → `25` changes the callout while the projected circle still
  measures 20. One source of truth for the dimension; not for the geometry.
- **Not every dimension is nameable as one feature plus one parameter** — inter-feature
  spans, face-to-face distances, angles between unrelated surfaces. They need the
  `RelationDimensionId` shape above, whose *selector spelling* is still open, so until that
  lands the mirror is bounded by what the identity model can name.

### One generated output

`--style imperative` exists today only because it carries the referential reconstruction the
declarative emitter lacks (`builder._feature_listing`). Once `sheet.dimension(...)` plus the
emitter mirror reach its coverage — rotational bodies, the `step_height` / `step_position`
ladders, off-axis `locate`, the machined-callout kinds, pocket / slot patterns, the gap-kind
comments — the two styles are capability-equivalent and it is **retired** (deprecation
warning first, removal at 0.4.0 with the other compat exits, #720).

Parity is a hard gate — retiring it earlier would regress the parts whose dimensions only
the imperative reconstruction can express. Two things follow: the low-level `Drawing` verbs
(`at`, `place_dim`, `items`, `view_bounds`) stop being a *generated* surface and remain a
hand-use API (ADR 0001 §3, and consistent with ADR 0012 already deprecating `place_dim`),
and the generated file deliberately loses that raw-coordinate escape hatch.

### Out of scope: the view / section surface

Everything above concerns the **dimension** layer. A declarative surface for views and
sections — `sheet.view("side")` for presence and scale, and a named, feature-targeted
`sheet.section("A-A", through=bore)` — is adjacent and appears in the worked example below
for realism, but it is **not decided by this ADR** and accepting 0016 does not accept it.

It is also a live compatibility question rather than a blank sheet: `Sheet.section` already
shipped in v0.3.9 (#847) as `section(feature=None, *, at=None)` — unnamed and
single-section — so multi-section naming is a *reshape* of an existing verb needing its own
overload-versus-rename call, while `sheet.view(...)` does not exist at all. Both belong in a
follow-up ADR, which the emitter phase depends on only for the non-dimension parts of
the script.

## Worked example — the mounting plate

> **This example is the END STATE — the script as it reads once the emitter mirror
> (phase 4) has landed.**
> It is not today's API and will not run against the current release: `sheet.dimension(...)`
> in its referential form does not exist yet, and the view / section lines are
> **illustrative only — not decided by this ADR** (see "Out of scope"). The feature lines
> (`hole`, `spotface`, `envelope`) are real today.

An 80 × 50 × 8 plate: a central ⌀20 bore with a ⌀32 × 1.5 spotface (which auto-triggers
section A–A) and four ⌀5 corner holes. Today `emit_sheet_script` writes the four
*features* and leaves the dimensions, views, and section as comments. Under this ADR the
emitter mirrors the drawing as **referential dimension lines** — one line per addressable
dimension, and no line restates a number:

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
# These three render as ONE callout — ⌀20 THRU ⌴ ⌀32 ↓ 1.5 — but are three
# addressable dimensions: drop the two spotface lines and it reads ⌀20 THRU.
sheet.dimension(bore,    "diameter")           # ⌀20   (read off `bore`)
sheet.dimension(bore,    "spotface_diameter")  # ⌴ ⌀32
sheet.dimension(bore,    "spotface_depth")     # ↓ 1.5

sheet.dimension(corners, "diameter")           # 4× ⌀5 THRU  (read off `corners`)
sheet.dimension(corners, "location")           # location ladder  ← comment out to drop
sheet.dimension(bore,    "location")           # bore on centre
sheet.dimension(env,     "width")              # 80    (read off the bbox)
sheet.dimension(env,     "depth")              # 50
sheet.dimension(env,     "height")             # 8     (thickness)
# sheet.dimension(corners, "pitch", axis="x")  # ← uncomment for the 64 grid pitch
# sheet.dimension(corners, "pitch", axis="y")  # ← and the 36 — two identities, two lines

# ── Views & section — ILLUSTRATIVE; surface not decided by this ADR ────────────
sheet.view("front"); sheet.view("plan"); sheet.view("side"); sheet.view("iso")
sheet.section("A-A", through=bore)    # cut the spotfaced bore (else auto-triggered)

sheet.export("plate")
```

Reading it against the rendered sheet:

- **This is an authored set, so it never calls `auto_dimensions()`.** The nine
  `dimension(...)` lines *are* the drawing's dimension set — which is what makes the
  commented-out pitch lines mean "suppressed" rather than "not mentioned". A script wanting
  the planner's choices instead would call `auto_dimensions()` and carry no `dimension(...)`
  lines at all, augmenting it with `sheet.add_dimension(corners, "pitch", axis="x")`. It
  cannot do both.
- **One rendered callout, three addressable dimensions.** The bore's three lines collapse
  into a single `HoleCallout` on one leader, but each is separately suppressible — that is
  the identity-layer / render-layer split, and it is why the mirror has three lines here
  rather than one. (The bore ⌀ is the callout's head: dropping it while the spotface lines
  remain raises — drop all three to drop the callout.)
- **`⌀20` appears once.** The number lives on the `bore` feature line; the dimension line
  only says *show `bore`'s diameter*. Change `diameter=20` → `25` (or edit the build123d
  object, for a live part) and the callout follows — no second copy to sync.
- **Dropping a dimension is not dropping the hole.** Comment out `sheet.dimension(corners,
  "location")` and the location ladder vanishes; the four ⌀5 *circles* stay, because they
  are geometry projected from the part, not annotations. Only editing the part removes a hole.
- **The commented `pitch` lines show the discriminator carrying its weight.** A grid emits
  two `grid_pitch` parameters of the same kind and role, so they are two identities and two
  lines — `sheet.dimension(corners, "pitch")` with no `axis=` raises rather than guessing.
- **A line the solver cannot fit still stays in the script.** If the sheet is too crowded for
  the bore's location ladder, that dimension drops with a lint warning and its line remains
  exactly where it is — a later scale or page change can make it fit again with no edit.

The A/B "features imply dimensions" vs "every dimension is a line" fork explored during
design collapses here: the referential form gives the per-dimension addressability of the
second with the single-source-of-truth of the first — over the identified set, bounded by it.

## Consequences

- **Two referential verbs sharing one contract**: `sheet.dimension(<feature>, <role>)`
  declares a member of the complete authored set, and `sheet.add_dimension(<feature>,
  <role>)` augments the planner's set (exact role names deferred to the roadmap). Both read
  their value from the referenced geometry and carry none, and both return a
  **`DimensionIntent` handle** — not `Sheet` — which carries the dimension's `DimensionId`
  and is the face `.pin()` / `.priority()` chain from. Suppression needs no verb at all: a
  surfaced `dimension` line commented out is the drop.
- **The addressable dimension becomes a first-class model type, on the planner's output.**
  `AddressableDimension(id, members)` is what one `dimension(...)` line addresses — usually
  one member, N for a correlated set (the ladders, rotational bores), with the grouping
  *declared* by the planner rather than inferred from key collisions. Members are
  `PlannedDimension`s, not raw `DimParameter`s, so `convention` / `suppressed` / `reason` /
  `datum` / provenance survive the grouping; `DimensionGroup.dims` is retyped accordingly
  (~25 mechanical read sites). One type at one boundary — an IR-side parameter group would
  have nothing to hold, since grouping is a planner decision. Its identity is
  `DimensionId(feature, parameter)`, where `(feature, role)` is only the call-site *address*:
  the key needs `kind` and, for genuinely distinct same-role parameters (grid row vs column
  pitch), a discriminator surfaced as a keyword (`axis="x"`). `ParameterId` is derived from
  `(role, kind, discriminator)`, not hand-written on every `DimParameter`; list position and
  UUIDs are rejected. Inter-feature measurements get a separate `RelationDimensionId` whose
  subject normalization is relation-specific (symmetric relations sort; oriented ones keep
  order). Durable *feature* identity is not minted here.
- **Identity-layer grouping is not render-layer grouping.** A compound `HoleCallout`
  (`⌀20 THRU ⌴ ⌀32 ↓ 1.5`) is **N** addressable dimensions sharing one leader, each
  separately suppressible — not one addressable dimension. A correlated set is the converse:
  one identity, N members, none separately addressable. The model carries both notions
  because the two cases are genuinely different.
- **Suppression may change what is shown, never what the drawing asserts.** No renderer may
  infer an engineering fact from a parameter's presence or absence — `hole_callout_spec`'s
  `through = depth is None` is a live defect by that rule, since filtering a blind hole's
  depth would print `THRU`. So suppression **marks** (`PlannedDimension.suppressed`, already
  honoured at thirteen sites) rather than filtering, the compound-callout reader learns to
  honour the flag, and suppressing a callout's head while its dependents remain raises.
- **The dimension set has two explicit sources, and a build must request one.**
  `auto_dimensions()` selects the planner's set; `dimension(...)` declarations form a
  complete authored set; `add_dimension(...)` augments the planner's set and requires it.
  Mixing `auto_dimensions()` with `dimension(...)` raises, as does a `build()` that requested
  neither. This is a **breaking change** to today's implicit-automatic `Sheet(part).build()`,
  taken in the same window as the rename below. It is what makes omission mean suppression
  without a hidden mode flag, and it keeps a forgotten dimension set a loud failure rather
  than a plausible, unbuildable sheet.
- **`dimension` means *referential* on both surfaces; the materialized verb is renamed to
  `measured_dimension`** — a rename, not a new name; `Drawing.dimension` already has the
  target shape.
- `plan_dimensions` (ADR 0015) grows an intent input: declared augmenting measurements join
  the planned `DimensionGroup`s; declared suppressions mark members suppressed rather than
  removing them; an authored set replaces them. The corridor solve (ADR 0014) is unchanged —
  it still receives one candidate population per strip.
- `intents.py` (ADR 0012) is the recording home; `Drawing.finalize()` /
  `_PASS_SEQUENCE` the drain. Augmenting intent lands there first; **pre-build** authored-set
  suppression lands with the set boundary, while **post-build** suppression of an automatic
  dimension waits on the reconstruction question (#867) — not on the closed #426/#707.
- `sheet_emit` gains a dimension-mirroring pass: after the feature basis, one referential
  `dimension(...)` line per **planned** dimension, led by the explicit dimension-source call
  — each commentable and editable, none restating a number, with low-level furniture still
  produced automatically on re-run. The pass reads the planner's `DimensionGroup`s, **not**
  the drawing's placed annotations, so a solver-dropped dimension keeps its line. What it
  promises is a mirror of round-trippable, semantically identified planner intent, not of
  every mark — the gaps are tabulated in "What the mirror does and does not cover".
- **Imported AP242 PMI stays materialized.** It carries `ref_pts` / `ref_bbox` / `at` and
  has no referential form, so it emits as `sheet.measured_dimension(...)` — still one
  editable line, so the intent-mirror property holds; the two verbs stay distinct precisely
  because one references and the other restates.
- **A duplicate/redundancy lint code joins `linting/structural.py`** — the third protection
  layer; nothing reports redundant dimensioning today.
- **`--style imperative` retires once the declarative mirror reaches its coverage**, leaving
  one generated output. The low-level `Drawing` verbs stay a hand-use API but stop being a
  generated surface (ADR 0001 §3), and the generated file deliberately loses its
  raw-coordinate escape hatch.
- **The view / section surface is explicitly NOT decided here.** Accepting this ADR commits
  to the dimension layer only.
- Extends ADR 0011 (declare features) to declare *dimensioning intent*; extends ADR 0012
  (edit one dimension) to declaring the dimension *set*; consumes ADR 0015 (planner) and
  ADR 0014 (solve); fulfils ADR 0001 §2. Does **not** reintroduce the ADR 0001 hardcoded
  DSL — intent carries no geometry.

## Proposed phased work

> **A verb never changes meaning between phases**, and **no phase ships a verb whose stated
> contract it cannot yet honour.** The order below follows from those two rules:
> `add_dimension` returns a handle carrying a `DimensionId` and is idempotent against the
> plan, so it cannot precede identity — hence identity and the augmenting verb land
> **together** as phase 1. And `dimension(...)` does not exist until phase 2 gives it its
> meaning, rather than turning from additive into set-defining under a shipped name.

1. **Semantic identity, then augmenting intent — one phase.** Land the addressable-dimension
   model first: `AddressableDimension` / `DimensionId` / `ParameterId` (derived keys, the
   `axis=` discriminator, correlated sets as one identity with N members), exposed as a
   handle on planned dimensions (ADR 0010 provenance + ADR 0015 roles) so intent can
   *reference* an auto dimension. Then `add_dimension(...)` on top of it: a
   scale-independent augmenting measurement recorded on the model and entered as a
   `CorridorCandidate` alongside the planner's set; reachable on today's solve. Reuses /
   narrows `authored_dimension` so intent and materialized-PMI stay distinct.
   **Feature-addressable measurements only** — pitch, thickness, overall, a feature's own
   parameters. Inter-feature spans wait for the relation selector (open below); shipping
   them here would mean guessing that syntax. `dimension(...)` is deliberately not
   introduced: it has no meaning before phase 2.
2. **The dimension-set boundary, then suppression by omission.** Land
   `auto_dimensions()` / authored-set semantics first — `dimension(...)` itself, the two
   mixing errors, the no-set-requested error, and the emitter always writing one of the two
   forms — since omission only becomes meaningful once a set is declared complete. Then a
   surfaced referential `dimension` line, when commented out, marks that `DimensionId`
   suppressed in `plan_dimensions` output — **marked, not filtered**, so no engineering fact
   is inferred from the parameter's absence; the compound-callout reader learns to honour
   `suppressed`, and head-without-dependents raises. No separate verb. This step carries the
   breaking change and should ship with the `measured_dimension` rename so callers migrate
   once.
3. **Full recompose** *(scope open — #867; the old #426/#707 references are closed)*.
   Reconstruct the automatic population at finalize and co-solve with declared dimensions,
   making **post-build** suppression / emphasis honest and script/direct output convergent.
   How much of this remains to do is the question #867 settles.
4. **Emitter dimension-mirror.** Emit one round-trippable referential `dimension(...)` line
   per **planned dimension intent** — never per *placed* dimension, which would let solver
   pressure rewrite version-controlled source (see "The script records intent"). The
   emitted script leads with `auto_dimensions()` or the authored set, so its dimension
   source is always explicit. Keep the self-describing comment as the floor for anything
   not yet mirrorable.
5. **Redundancy lint.** The third duplicate-protection layer — report over-dimensioning that
   is neither identical nor coincident (a pattern's per-hole locations *and* its pitch).
6. **Retire `--style imperative`** once the mirror reaches its reconstruction coverage
   (rotational, the ladders, off-axis `locate`, machined callouts, pocket / slot patterns),
   leaving the declarative script as the single generated output.

## Open questions

- *(The naming decision itself is settled — see "One name, one contract": `dimension` is
  referential on both surfaces, the materialized Sheet verb becomes `measured_dimension`,
  `authored_dimension` renames in step, and a one-release transitional overload carries the
  old call form to 0.4.0.)* What remains open is only the spelling: `measured_dimension`
  versus a shorter `measured`, and whether the model-layer constructor keeps a `_dimension`
  suffix the façade drops.
- The `role` vocabulary for `sheet.dimension(feature, role)`: which measurements to support
  first (`"diameter"`, `"location"`, `"pitch"`, `"width"`/`"depth"`/`"height"`, `"angle"`,
  `"radius"`), and how the call-site role maps onto the `ParameterId` space (`"depth"` →
  `"bore.depth"`) when a feature has counterbore and spotface depths as well.
- **The relation selector.** `RelationDimensionId` settles the *identity* of an
  inter-feature measurement; how it reads at the call site does not —
  `sheet.dimension(a, b)` / `sheet.dimension((a, b), "span")` / a feature-handle method.
  Until this is decided, inter-feature spans stay at the comment floor.
- **Durable feature identity**, deferred deliberately above: how `sheet.dimension(...)`
  composes with `of(...)` on a detected model (referencing an auto-detected feature by
  object/index to drop or emphasise its dimension). Object identity covers a single script
  run; an intent that must survive re-detection needs a real `FeatureId`, and that is the
  question to answer before intents are persisted anywhere but a script. Also open: whether
  a bare `sheet.dimension(feature)` (no role) means "all of that feature's dimensions".
- Guards to add when this lands: a fidelity test that an emitted `dimension` line re-solves to
  the same dimension (mirroring `test_sheet_emit` parity), an audit that a `dimension` line
  carries no number or page geometry (the reference-not-restate / scale-independence
  invariant), tests that the three source errors fire (mixing, no set requested, and
  `add_dimension` without `auto_dimensions()`), and an idempotence test that an augmenting
  declaration overlapping the plan yields exactly one dimension. Identity gets its own
  guards: a uniqueness audit that no feature yields two **`AddressableDimension`s** with the
  same `ParameterId` — over addressable units, *not* raw `DimParameter`s, since a correlated
  set deliberately holds N members under one id, and an audit phrased over parameters
  would reject exactly the grouping the design permits (it is still the check that would have
  caught the grid-pitch collision, because two ungrouped pitches are two addressable units);
  a test that an ambiguous selector raises rather than picking; and a stability test that
  re-detecting a part yields the same `ParameterId`s.
