# ADR 0016 — Declared dimensioning intent: capture what to measure, let the engine place it

- **Status:** Accepted
- **Date:** 2026-07-26 (accepted 2026-07-27; implementation epic **#867**)
- **Deciders:** Paul Fremantle (pzfreo)

> **Scheduling reality — settled by implementation (2026-07-30).** Phases 1–3 have
> shipped as epic **#867** (PR0–PR8, #868–#876). The question this header carried is
> answered, and the answer was the cheap one it floated:
>
> **Suppression by omission needed no recompose at all.** In a `Sheet` script the authored
> set is known *before* `build()`, so `plan_dimensions` marks the omissions and the
> compiled plan withholds them — nothing has to be un-drawn afterwards. Only
> `Drawing.finalize()` dropping an *automatic* dimension would need the global recompose,
> and that is not what the authored set does. So phases 2–3 were never blocked on it, and
> the "longest-open item on the roadmap" framing was doubly stale: the issues were closed
> AND the dependency was not real.
>
> What the implementation did need was different and unanticipated: a **compiled-plan
> boundary** (Amendment 1). Marking a dimension suppressed and trusting sixteen renderers
> to check the flag failed sixteen times, so the mark became content renderers never
> receive. That was #923's work, not scheduling.
>
> **Phase 4 (the emitter dimension-mirror) SHIPPED in #922** (this paragraph previously said
> it had not, and stayed stale past the fact — #957 review round 3). Its blocker was that
> emitted features had no names, so a `dimension(...)` line in a generated script would have
> addressed a feature by position and broken the moment a feature line was commented out —
> the documented editing workflow. #931/#932 removed positional addressing from the artefact,
> so the emitter now binds a name per feature and writes the declarations honestly; it no
> longer refuses an authored model. **Phase 6 (retire `--style imperative`) landed in #940** —
> see "One generated output" below for what shipped and where it departed from the plan.

## Amendment 1 — the compiled-plan boundary (2026-07-29)

**Renderers may emit dimensional content only from the compiled plan.**

A dimensional renderer receives approved entries and decides *where* and *how* to draw
them. It does not decide *what*, and it is not given the feature inventory or the bounding
box it would need to decide otherwise. The corollary is the part worth stating outright:
**suppression is not a flag renderers check, it is content they never receive.**

### Why this is a rule and not a review note

Everything above this amendment says the planner decides suppression. It did — and the
decision was routinely ignored, because `PlannedDimension.suppressed` was advisory. A
renderer got the planned groups *and* the `PartModel` *and* the `Analysis`, so honouring the
plan was a convention it had to opt into, and reconstructing a suppressed dimension from
`feature.levels` or `a.bb` stayed one attribute access away.

Eight adversarial review rounds on #921 found eight renderers that had not opted in: the
height ladder and step positions rebuilding their marks from the feature and the bounding
box; `render_diameters`, `render_boss_diameters`, `render_step_lengths` and
`render_rotational` selecting parameters with no suppression check at all; the compound
callout, the deferred-edit path, the script emitter, and `from_part` each needing separate
enforcement. Every one was a real omission reaching a real drawing.

Fixing them individually produced four mechanisms for saying the same thing — `pd.suppressed`
reads, `env_dim_placed`, `set_dim_placed`, and a `_CALLOUT_PARAM_KINDS` policy table — and
no reason to think the ninth renderer would be different. That is the signature of a missing
boundary, not of missing tests.

### The shape

```
PartModel
  → compile_dimensions()            # one place: rules, requests, authored sets
      → RenderableDimensionPlan     # APPROVED entries only
      → diagnostics                 # what was omitted, and why
  → renderers consume approved entries; audit/lint consume diagnostics
```

- `ApprovedDimension` has **no `suppressed` field**. There is nothing to forget.
- Withheld measurements leave through `diagnostics`. The builder retains them as
  `Drawing.suppressions()`, coverage consumes their family outcomes, and selected validation
  omissions additionally surface through lint. They never enter a renderer.
- The feature a dimension came from travels as an **opaque `FeatureRef`**: identity and
  category, no measurement. Carrying the `Feature` itself would have left the bypass one
  attribute access away — `.feature.levels` rebuilds exactly what the compiler withheld.
  The two seams that legitimately need the object (ADR 0010 provenance tagging, escalation
  grouping) resolve it explicitly; a dimensional renderer doing so is a violation the
  boundary guard catches.
- Correlated sets (a step-height ladder, a shoulder chain) arrive as explicit
  `ApprovedLadder` groups, so a renderer never rebuilds one from a feature. This preserves
  the tier-3 identity rule — the set is approved or omitted whole, never half a staircase.
  A datum-coincident shoulder is the validation exception, not a partial authored decision:
  it measures no distance and cannot form border geometry, so the compiler removes that
  non-measurement before the correlated set forms, retains every non-degenerate sibling under
  the same tier-3 identity, and emits `step_position_coincident_with_datum` through both the
  omission ledger and lint. A renderer never receives identical endpoints.
- Spans travel in **part space**; the renderer projects them. That is the split in one line:
  the compiler says "this measurement, this value, between these two points"; the renderer
  says which view, which strip, which side, and what happens when it does not fit.
- Dimensional renderers take a `LayoutFrame` — projectors, view rectangles in page space,
  strips, scale — rather than the `Analysis`. View *edges* are page-space rectangles rather
  than projected bbox extents, so "just right of the front view" needs no part geometry.
- **Omission is not a drop.** The compiler's omission never arrives and is reported through
  diagnostics; the placer's drop arrived and did not fit, and is reported as
  `placement_unsatisfiable`. Conflating them is how a deliberate suppression came to look
  like a layout failure.

### What the compiler may do that renderers may not

Read the bounding box. A model with no `EnvelopeFeature` — a round body, or a `Sheet` that
never called `.envelope()` — has no parameter naming its overall height, and the value falls
back to the bbox. That fallback is legitimate and now lives in exactly one place, instead of
being a thing the height-ladder renderer did while claiming to be doing layout.

### Scope, stated so the exceptions cannot be mistaken for completeness

**The rule is the destination. The migration is substantially advanced but not complete;
the inventory below is the honest state of it.**

**Every `render_*` renderer of generated dimensions has crossed** (#925). No renderer takes
the legacy `DimensionGroup` surface any more, so `suppressed` is no longer an advisory
boolean anyone can ignore — which is exactly what eight #921 rounds found happening.

Two earlier versions of this section understated the gap, each time because the guard
behind it measured the wrong thing (#923 reviews). Counting renderers that take `model`
reported the migration nearly complete while sixteen sat on the advisory surface; not
naming a parameter `model` is not the same as having crossed the boundary.
`tests/test_compiled_plan_boundary.py` classifies by **contract** and pins all three lists,
so this inventory cannot drift from the code:

| Contract | Meaning | Renderers |
|---|---|---|
| `plan` | approved entries only — inside the rule | `render_height_ladder`, `render_step_positions`, `render_plates`, `render_chamfers`, `render_fillets`, `render_flats`, `render_grooves`, `render_boss_diameters`, `render_polygonal_bosses`, `render_boss_heights`, `render_envelope`, `render_pockets`, `render_pocket_patterns`, `render_slot_patterns`, `render_diameters`, `render_rotational`, `render_step_lengths`, `render_locations`, `render_slots` (+ the prismatic detail redraw) |
| `groups` | advisory `suppressed` | *(empty — the guard fails if anything reappears)* |
| `model` | author-supplied text, not a generated measurement | `render_gdt`, `render_pmi` |

`render_gdt` and `render_pmi` take the model on purpose and permanently. A control frame's
tolerance and a PMI record's label are written by the script or by the STEP file and
rendered verbatim; their `parameters()` are empty by design, so there is nothing to plan,
suppress or approve. An authored dimension set does not govern them because they were never
the engine's choice to make.

Two paths outside `render_*` also print values, and both are now compiled:

- **Pattern pitch** (`_add_furniture` → `_place_pitch_dim`) is grouped with furniture in the
  code but prints a VALUE, which is what makes something dimensional under this rule. Its
  pitch/grid values come from the approved group; its bolt-circle centreline still reads
  `feat.bcd`, because a centreline is geometry, like a centre mark. `_furnish_uncalled_
  patterns` draws the pitch for a pattern with no callout — furniture used to be a side
  effect of placing one, which an authored set separates.
- **Slot positions** measure from the bounding box rather than from `datum_xy`, so they are
  compiled by `_compile_slot_positions` rather than by `plan_locations`, and gated by the
  same `location_role` table.
- **Side-drilled hole positions** likewise: an X/Y-drilled bore's in-plane offset and its
  height are measured from the bounding box in its end-on view, so
  `_compile_off_axis_hole_locations` owns them. Two approved entries per member, not one —
  `dim_loc_side_y3500` and `dim_loc_front_z1200` are separate dimensions on the page, and a
  single "this hole is located" approval would leave the renderer deciding which of the two
  it covered.

**Two ratchets, on the symptom and on the cause.** `test_compiled_plan_boundary.py` checks
behaviour — an empty plan draws nothing — and `test_label_provenance.py` checks the cause:
a renderer that calls `_fmt(x)` is turning a number into printed text, which means the
number reached it as a number rather than as the compiler's `value_text`. Every defect of
this class looked exactly like that. A behavioural guard only covers the paths its fixtures
reach (`_staircase()` has no holes, which is how two dimensional paths stayed outside the
boundary unnoticed); the source ratchet covers every path whether a fixture walks it or not.
It is shrink-only, and each survivor carries a written reason.

**Once a semantic owner is designated, parallel INFERENCE paths must be deleted, not
redirected.** The rule that most reliably held while this ADR was implemented, and the one
that explains why some fixes stuck and others did not:

- #921 fixed eight renderers to check `suppressed`. The ninth leaked.
- #923 **deleted the field**. No renderer has leaked it since — `ApprovedDimension` has
  nothing to forget.
- #925 removed `_ir_off_axis_holes` rather than leaving it beside `_approved_off_axis_holes`;
  #933 removed the emitter's blanket refusal rather than adding a flag beside it.

A redirect leaves the trap armed: the next author reaches for the reader that still exists,
because it still exists. Fixing N call sites is O(N) work that must be repeated for every
new call site; deleting the thing they call is O(1) and permanent.

**Scoped to competing readers of one semantic fact — not to compatibility forwarding.** Two
ways to *ask* for an answer is an API convenience and this codebase keeps several on purpose
(`make_drawing`'s facade, `dimension`'s transitional overload; the `sheet_dsl` alias was one
until #720 deleted it at 0.4.0 — dated, as §4 of ADR 0005 requires, not permanent). Two
ways to *decide* an answer is the defect: `location_role` and `plan_locations` and the
off-axis pass each independently concluding whether a hole has a position. A forwarding shim
computes nothing, so it cannot disagree; a second inference path exists precisely to
conclude, and will.

A path that is not deleted is named, and there are **three** reasons a path survives. They
are different mechanisms, trustworthy for different reasons, and conflating them is how a
list stops meaning anything:

- **Unresolved debt** — inventoried AND **shrink-only**, so it can only ever get smaller.
  `_FMT_BUDGET`, and the `hc_` member of `_PENDING_VALUE_CARRYING` (#926).
- **A permanent exception** — inventoried AND **argued**, because it will never shrink and a
  reader must be able to tell that from a pending item. The `pmi_` member of
  `_PENDING_VALUE_CARRYING`, and `render_pmi` / `render_gdt` in the contract table above:
  they render author-supplied text rather than inferring a generated measurement, so there
  is no inference path to delete. *(That those two live in one tuple named "pending" is
  itself the conflation this paragraph warns about — folded into #926.)*
- **An intentional shared route** — inventoried AND **behaviourally verified**, so the
  listing stays a checked claim rather than an assertion. `_SAME_PATH_AS_ENVELOPE`, whose
  members are proven by `test_the_same_path_verbs_really_share_the_route` to reach their
  handle by the same two lines `envelope` does.

The corollary is why this ADR keeps producing *tables* rather than checks: a check must be
remembered at each site, a table is consulted from one. `location_datum`, `_FACTS` and
`_LOCATION_ROLE` are each the deletion of a rule that had been restated in three places.

**Hole callouts (`hc_`) remain on the legacy surface.** They honour `suppressed` at every
term (`model/callout.py` checks it for each segment, head and dependent), so this is a
structural gap rather than a behavioural one — but "the renderer checks" is exactly the
guarantee this boundary exists to replace, so it stays named rather than assumed safe.

### Locations are addressable (#925, and #883 is not a blocker)

A location prints a number, so it is a dimension and belongs inside the boundary. It had no
`DimParameter` — it is synthesized from the feature and the datum — so before #925
`dimension(hole, "location")` raised and an authored set could neither include nor exclude
one. **A dimension the author cannot address is a dimension the author cannot omit**, and
every location was drawn regardless of what the script declared.

`planner._LOCATION_ROLE` is now the single statement of which kinds have a position;
`location_role()` derives both `plan_locations` and the authored vocabulary from it, and
`compile_dimensions().locations` is the approved set every location renderer reads.

**Eligibility is one answer, read three times.** `planner.location_datum(feature)` returns
`"datum_xy"`, `"bbox"` or `None` — where a position is measured from, or that the feature
has none — and `plan_locations`, the bbox compilers and the authored vocabulary all read
it. Re-deriving that answer is what broke twice: the kind table said a hole is locatable
while `plan_locations` said only a Z-normal one is (side-drilled positions drawn outside
the plan), and then it said a *pattern* is locatable while neither compiler emits one
off-axis, so `dimension(x_pattern, "location")` was accepted and silently drew nothing.

An off-axis pattern is `None` because the engine has never drawn one — the off-axis pass
excluded patterns by construction. Compiling one would be new output with its own layout
consequences, not a boundary fix, so the vocabulary tells the truth about today's engine
and the author gets an error rather than a blank drawing.

**A position is compiled wherever it is measured from.** `plan_locations` owns the Z-normal
ladder, which measures from `datum_xy`; the compiler owns the two that measure from the
BOUNDING BOX in a feature's own view — a slot's near-end offset and a side-drilled hole's
offset + height. Splitting by datum rather than by feature kind is what keeps
`_LOCATION_ROLE` a single answer: a hole is locatable, full stop, and where its span comes
from is a separate question. Before #925 those three disagreed — the table said locatable,
`plan_locations` said Z-normal only, and `_locate_off_axis_holes` drew the X/Y ones from raw
IR anyway.

The authored role is the coarse `"location"` — one unit per feature. #883 asks whether a
patterned hole's position is one addressable thing or one per member, which is a question
about NAMING. Omission is well-formed at either granularity, and a finer id
(`location.member.3`) refines this one later without contradicting it, so the completeness
contract does not wait on #883.

- **Furniture is not dimensional content.** Centrelines, centre marks and section arrows
  print no value; they are sized off the geometry they mark and stay outside this rule.
  `render_centermarks` therefore takes explicitly named `furniture_groups`: it is not a
  dimensional renderer pretending to have crossed the compiled-plan boundary.

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

The near-miss primitive is today's `sheet.measured_dimension(...)` (`model.declare.measured_dimension`
— both renamed from `dimension`/`authored_dimension` by #873, so the referential verb could take
the plain name),
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
`.priority()` chain — `sheet.dimension(bore, "diameter").pin().priority(2.0)`. This follows
the pattern the façade already uses for anything carrying aspects (`hole` → `_Hole`,
`diameter` → `_Dim`, `slot` → `_Params`) while verbs with nothing to decorate (`chamfer`,
`fillet`, `plate`) return `Sheet`.

The handle is the one place the dimension's semantic identity lives, and what the rest of
the design keys on:

- it carries the `DimensionId` defined in the next section — the identity used for
  suppression, for the planner input, and for matching an emitted line back to its intent;
- it is the ADR 0010 provenance anchor: intent → the annotation names the render seam
  produced, so `drop` / `annotations_of` resolve through it. **Not yet wired (#886):**
  the seam records `name → one feature`, while a compound callout is one annotation
  rendering N addressable dimensions — so the channel has to become
  `name → tuple[DimensionId, ...]` before per-dimension resolution is possible at all;
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
    id: ParameterId                        # "bore.diameter", "grid_pitch.length.row"
    members: tuple[PlannedDimension, ...]  # usually one; N for a correlated set


@dataclass(frozen=True)
class DimensionGroup:                      # existing type, one field added
    feature: Feature
    view: str
    units: tuple[AddressableDimension, ...]     # the identity layer
    # dims -> tuple[PlannedDimension, ...]      # property: the flattened view


@dataclass(frozen=True)
class DimensionId:
    feature: Feature       # the IR feature, compared STRUCTURALLY (#871) — not a
                           # minted FeatureId, and not `is`: a re-plan builds new
                           # objects, so identity must survive reconstruction
    parameter: ParameterId                 # the AddressableDimension's id
```

**One type at one boundary, not two.** An IR-side `ParameterGroup` paired with a
planner-side unit was the alternative; it has nothing to hold, because grouping is a
*planner* decision (below) — `Feature.parameters()` returns a flat list and should keep
doing so.

***Amended 2026-07-27 (#870, as built).*** *The names came out the other way round.* *The
plan was to retype `dims` to hold units and add a flattening accessor beside it, at a cost
of ~25 mechanical read sites. Building it, the same semantics fall out of adding `units` as
the field and making `dims` the flattening **property** — so all 22 source readers and ~30
test readers of `g.dims` are untouched. The stated goal — "readers that do not care about
grouping never learn about it" — is better served by the inversion than by the retype.*

*Two honest corrections to that, from review:*

- ***The cost did not go to zero; it moved from readers to constructors.*** `DimensionGroup`
  *is publicly exported, and its constructor changed from `dims=` to `units=` — so
  `DimensionGroup(..., dims=…)` and `dataclasses.replace(g, dims=…)` now raise `TypeError`.
  Taken as an **intentional API break** rather than shimmed: there is exactly one
  construction site in the repo (the planner), the type is planner **output** that callers
  consume rather than build, and an alpha package already carrying the phase-2 breaking
  change should not grow a compatibility initializer for a call form nobody uses.*
- ***The identity layer covers locations at feature granularity (#925).*** *`plan_dimensions`
  still skips `location`-kind parameters and `plan_locations` still returns a flat
  cross-feature list, so a location has no `AddressableDimension`. What it does have is a
  role in `planner._LOCATION_ROLE`, which is enough for `sheet.dimension(bore, "location")`
  to address it and for an authored set to omit it. **#883 remains open** for the finer
  question — one unit per feature or one per member — which affects naming, not omission.*

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

**It is derived, not hand-authored** — `role` + `kind`, plus a semantic discriminator where
one is needed, so the forty `DimParameter(...)` construction sites do not each grow a
literal that can drift from the `role` beside it. Only the discriminator is new data, and
only tier 2 needs one (the grid pitches, today's sole instance). An
explicit `id=` field on every parameter was the alternative; it is more direct, but restates
`(kind, role)` wherever it adds nothing, and a site whose `id=` disagrees with its `role=`
is a new class of silent bug. Derived keeps one source of truth — the same argument this ADR
makes about numbers. Deriving the key is safe *because* grouping is declared separately: two
parameters landing on the same derived id is an error the audit catches, never a silent
merge into a set.

***Amended 2026-07-27 (#869, as built).*** *Two details this section originally got wrong:*

- ***`kind` is included always, not only where it disambiguates.*** *A rule that dropped it
  when a role happened to be unique — giving the prettier `step_height` — would make an id
  depend on its **sibling** parameters, so adding a field to a feature would silently
  repoint every intent aimed at an existing one. That destroys the stability the id exists
  for, and stability outranks prettiness. Ids are therefore uniform:
  `bore.diameter`, `bore.depth`, `step_height.length`, `grid_pitch.length.row`.*
- ***The discriminator is `row` / `col`, not `x` / `y`.*** *`PatternFeature.angle` may rotate
  the lattice, so a row pitch is not an X pitch in general and the IR must key on what it
  actually knows. The user-facing `axis=` selector maps onto row/col **at the façade**, which
  is where the lattice angle can be consulted — so how `axis=` reads on a rotated grid joins
  the open questions below.*

**The selectors stay clean**, with the discriminator surfacing as a keyword only where it is
needed (the call-site spelling; the derived key is beneath it). The `axis=` lines below read
an **unrotated** grid, where `x`→row and `y`→col; what an axis-named selector means on a
rotated lattice is open below:

```python
sheet.dimension(hole,    "diameter")         # -> DimensionId(hole,    "bore.diameter")
sheet.dimension(hole,    "depth")            # -> DimensionId(hole,    "bore.depth")
sheet.dimension(pattern, "pitch", axis="x")  # -> DimensionId(pattern, "grid_pitch.length.row")
sheet.dimension(pattern, "pitch", axis="y")  # -> DimensionId(pattern, "grid_pitch.length.col")
sheet.dimension(steps,   "step_height")      # -> the whole ladder, one identity
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
`FeatureId` in the IR today — the plan carries the feature itself (`DimensionGroup.feature`),
and the **structural** equality every frozen-dataclass `Feature` already has is sufficient:
`sheet.dimension(bore, "diameter")` resolves with no minted key at all, and — because it is
structural rather than `is` — an id still resolves after a re-plan rebuilds the feature
objects (#871). A *durable* `FeatureId` is only needed where an intent must survive
re-**detection**, where the feature's own values may shift; that is the `of(...)` question
left open below. So `FeatureId` elsewhere in this ADR reads as "whatever identifies a
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

**Dependents and riders are different things, and only riders are waived.** A compound
callout string carries two kinds of trailing content:

- **Dimensional dependents** — a counterbore ⌀, a countersink angle, and a bolt circle's
  `bolt_circle.diameter`. Each is a planned, addressable `DimParameter` that the drawing
  prints. Losing one silently is losing a dimension, so the head rule applies: approved
  without its head **raises**.
- **Non-dimensional riders** — the `n×` multiplier, a thread spec, a grid's `(3×3)`. These
  live on the FEATURE with no parameter to suppress, so they survive any amount of
  parameter suppression and have no independent existence outside the string.

For a rider, whether silence is acceptable turns on **who decided**, which is what
`Omission.authored` carries: a planner rule discarding a thread spec is the engine quietly
dropping manufacturing intent (#920's refusal stands), while an author who omits the bore is
declining the string, not orphaning its prefix — and refusing there made a pattern the one
feature whose callout could not be omitted at all.

That escape must not extend to a dependent. The BCD renders *only* as the
`EQ SP ON ø50 BC` suffix, which made it look like a rider; classifying it as one let an
authored set naming `bolt_circle.diameter` and omitting `bore.diameter` produce neither the
BCD nor a diagnostic — the requested dimension vanished (#925 review). **The test is whether
the term has a `DimParameter`, not where it appears in the string.**

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
sheet.dimension(bore, "depth")
dwg = sheet.build()

sheet = Sheet(part).authored_dimensions()     # ...and this one is complete and EMPTY
dwg = sheet.build()                           # a drawing with no generated dimensions
```

**Each source has a verb (#933).** `dimension(...)` selects the authored source on its own,
so `authored_dimensions()` is redundant for a non-empty set — but a set that is complete and
*empty* has no line to select it with, and absence of `dimension(...)` lines is also what a
script with no source at all looks like. Without the verb, `authored_dimensions=()` was a
valid `PartModel` that could be built directly and not written as a script. Emitted scripts
write the verb unconditionally, so an authored script states its source the same way an
automatic one does rather than in a comment.

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
| Correlated sets, per member | `step_height` / `step_position` ladders and rotational bores are one `AddressableDimension` holding N members | **One** line per set; suppress the set, not a member |
| Location dimensions, per member | Addressable per FEATURE since #925 (`dimension(hole, "location")`); whether a patterned hole's position splits per member is **#883** | **One** line per feature |
| Inter-feature spans and angles | No `(feature, role)` form — needs `RelationDimensionId`, whose selector spelling is still open | Comment floor until the relation selector lands |
| Imported AP242 PMI | Materialized: carries `ref_pts` / `ref_bbox` / `at`, so there is nothing to reference | `sheet.measured_dimension(...)` — still one editable line |
| Low-level furniture | Centre marks, section arrows, hatching, the NTS caption carry no editable intent | Engine-automatic, by decision |
| Anything the emitter cannot re-solve | The fidelity floor `emit_sheet_script` already holds for features | Self-describing comment |

Three of those six are the identity model's boundary and shrink as it grows (relations,
locations, future correlated-set splits); the other three are decisions, and stay. **The property the
mirror actually promises is that within the identified set, a line's presence and its
absence both mean something exact** — which is all suppression-by-omission needs.

### Constraints this forces (the honest edges)

- **Auto dimensions must be semantically nameable.** Suppression and override require a
  stable identity for "the bore diameter of hole H" that survives a re-solve at a
  different scale. That identity is the `DimensionId` above, not a page-keyed annotation
  name — it leans on the ADR 0015 planner keeping parameter roles stable, and on ADR 0010
  provenance growing an N-ids channel (#886) before an id can resolve to the annotations
  it produced. (*Location* dims are nameable per feature since #925; per member is #883.) Both intents need the key — `add_dimension` for its handle and for
  idempotence against the plan — but **suppressive intent additionally needs that identity
  to be stable across re-detection and recomposition**, which is why identity lands *with*
  the augmenting verb while suppression waits for the set boundary.
- **Honest reconciliation needs the full recompose** *(the pre-build half is settled —
  it needed no recompose; the post-build half is still open, see the scheduling note)*.
  Suppressing or re-emphasizing an *automatic* dimension means the finalize path must
  reconstruct the automatic candidate population and co-solve it with the declared
  intents — the global recompose ADR 0012 Amendment 1 records as still open. Note this
  applies to the **post-build** `Drawing.finalize()` path; a `Sheet` script's authored
  set is known before the solve. Until it lands, `Drawing.finalize()`
  drains *recorded* intents against already-committed annotations as obstacles; it does not
  reconcile against the auto-plan. So **augmenting intent (`add_dimension`) is reachable on
  today's machinery — it is simply a new candidate. Suppression splits into two paths that
  this ADR previously conflated:**
  - **pre-build**, a `Sheet` script's authored set — known before the solve, so it needs no
    reconstruction at all. **Confirmed by #876**: the planner marks the omissions and the
    compiled plan withholds them, so nothing is ever drawn to be un-drawn;
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

**Landed 2026-07-31 (#940), with one deliberate departure.** "Deprecation warning first"
assumed the surface could keep working while callers moved off it. It could not: the point of
the change is that a second emitter stops existing, and an emitter kept alive to emit a
warning is still a second emitter. So both entry points *fail* rather than warn —
`generate_script` raises with the replacement in the message, `--style imperative` errors with
its own text rather than the generic bad-value branch — and the stubs themselves exit at 0.4.0
with #720 as planned.

**Both stubs are now gone (2026-08-01, #720).** `generate_script` is deleted from
`draftwright.__all__`, and `--style imperative` is simply an unrecognised value — the generic
bad-value branch, no bespoke explanation. `--style` itself survives with its sole value `sheet`,
since scripts passing it must keep working; that is a live option, not a compat stub. The gate was met as **coverage** parity per this section, not
line-for-line: the four regressions asserted only against the imperative script (#555, #881,
#889, #133) were retargeted and hold as full annotation-set parity.

Retiring it also exposed the cost of the "untested" classification in the mirror-coverage
roster. Pocket- and slot-pattern scripts had been emitting `pocket(...)` / `slot(...)` member
templates without importing them, so they raised `NameError` on their first feature line — a
kind marked untested turned out to be a kind that was broken. Every geometric kind is now in
the corpus that *executes* what it emits, which is what makes "per kind" a checkable claim
rather than a maintained list.

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

- **This is an authored set, so it never calls `auto_dimensions()`.** The seven active
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
  "diameter")` and the `4× ⌀5 THRU` callout vanishes; the four *circles* stay, because they
  are geometry projected from the part, not annotations. Only editing the part removes a hole.
- **The commented `pitch` lines show the discriminator carrying its weight.** A grid emits
  two `grid_pitch` parameters of the same kind and role, so they are two identities and two
  lines — `sheet.dimension(corners, "pitch")` with no `axis=` raises rather than guessing.
- **A line the solver cannot fit still stays in the script.** If the sheet is too crowded for
  the envelope's width dim, that dimension drops with a lint warning and its line remains
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
  `datum` / provenance survive the grouping; `DimensionGroup` gains a `units` field, with
  `dims` becoming the flattening property, so no *reader* changes — at the cost of an
  intentional break to its constructor, and with location dimensions still outside the
  identity layer (#870, #883). One type at one
  boundary — an IR-side parameter group would
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
  `dimension(...)` line per **addressable dimension** — the unit, not the member, so a
  correlated set gets one line — led by the explicit dimension-source call
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
  raw-coordinate escape hatch. **Done** (#940, 2026-07-31; ADR 0001 Amendment 2). The lost
  escape hatch cost one real capability — the imperative file's raw `build_drawing(...)`
  call let a reader add any engine kwarg by editing it — so `detail_view` became a `Sheet`
  argument in the same change rather than disappearing from the generated surface.
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
   `axis=` discriminator, correlated sets as one identity with N members), derivable from
   the plan (ADR 0015 roles) so intent can *reference* an auto dimension. Per-dimension
   ADR 0010 provenance is **split out to #886** — it needs an N-ids channel, since one
   compound callout renders several addressable dimensions. Then `add_dimension(...)` on top of it: a
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
   per **planned `AddressableDimension`** — the unit, so an N-member correlated set still
   emits one line — and never per *placed* dimension, which would let solver pressure
   rewrite version-controlled source (see "The script records intent"). The
   emitted script leads with `auto_dimensions()` or the authored set, so its dimension
   source is always explicit. Keep the self-describing comment as the floor for anything
   not yet mirrorable.
5. **Redundancy lint.** The third duplicate-protection layer — report over-dimensioning that
   is neither identical nor coincident (a pattern's per-hole locations *and* its pitch).
6. **Retire `--style imperative`** once the mirror reaches its reconstruction coverage
   (rotational, the ladders, off-axis `locate`, machined callouts, pocket / slot patterns),
   leaving the declarative script as the single generated output. **Landed** (#940). The
   gate was met as annotation-set parity per kind, not textual similarity: the coverage
   regressions asserted only against the imperative script (#555, #881, #889, #133) were
   retargeted onto the Sheet script and hold as full parity, and every part family in the
   round-trip corpus emits, runs and lints clean through it.

## Amendment 3 — the parameter id is the canonical spelling; the bare role was a second granularity (2026-07-31)

`dimension(feature, role)` accepted two spellings, and they were never synonyms. The bare role
(`"bore"`) selects **every** parameter carrying it; the parameter id (`"bore.diameter"`) selects
one. On a role with a single parameter the difference is invisible, which is why it went
unremarked for the whole epic. On `step` it is not:

```
dimension(step, "step")           ->  ø30  AND  20
dimension(step, "step.length")    ->  20
```

That is a direct contradiction of this ADR's own rule. An authored set means *omission is
suppression*; a spelling that quietly declares a measurement the author did not name is the
mirror image of it, and nothing reported that it had happened.

**Decision.** The parameter id is canonical.

- A bare role naming more than one measurement is **refused**, naming them. The existing
  ambiguity check compared `discriminator`, so it never fired here.
- A bare role naming exactly one is deprecated (warns, expires 0.4.0 with #720) and
  **normalised** to the id before it is stored — so an emitted script no longer changes dialect
  with how its source model was authored.
- `DimensionParameterId` (`model/ir.py`) types the authoring verbs as a `Literal` of the canonical
  spellings — named for what it holds, ids rather than roles, as is the handles'
  `dimension_ids()` discovery method — which is how this codebase already spells a closed
  string vocabulary (`ParamKind`,
  `Axis`, `pmi=`, `severity=`). Bare roles are deliberately absent: listing them would recommend
  the thing being retired. `Role` above it stays `str` — the IR must remain open to new
  detectors; only what a *caller* may name is closed.
- **Discriminated parameters are named in full, like every other.** `grid_pitch.length.row`
  and `.col` resolve on their own; the id already carries the variant, so nothing extra is
  needed to reach one. An earlier cut of this amendment made `grid_pitch` an exception — the
  bare role plus `axis=` — and that exception was the defect: `dimension_ids()`, which the generated
  script tells readers to call, listed a spelling `dimension()` then refused as ambiguous
  (#965 review). Removing the exception is what makes "the parameter id is canonical" true
  without a footnote. The bare role with `axis=` still resolves, for scripts already written
  that way, and an `axis=` contradicting the id is refused rather than silently preferred.
  The family refusal compares undiscriminated ids, so it does not fire on variants of one
  measurement.

**Not closed by this.** Normalisation happens at the `Sheet` facade. A hand-built
`RequestedDimension` through `build_drawing(model=…)` still carries whatever spelling it was
given, so the raw-IR route can still put a bare role into an emitted script. The planner accepts
both, so this is a spelling preference rather than a validity rule — which is why it sits at the
facade and not on the IR type. Worth revisiting if the raw route grows users.

## Open questions

- *(The naming decision itself is settled — see "One name, one contract": `dimension` is
  referential on both surfaces, the materialized Sheet verb becomes `measured_dimension`,
  `authored_dimension` renames in step, and a one-release transitional overload carries the
  old call form to 0.4.0.)* What remains open is only the spelling: `measured_dimension`
  versus a shorter `measured`, and whether the model-layer constructor keeps a `_dimension`
  suffix the façade drops.
- **How `axis=` reads on a *rotated* grid** (#869). The IR keys a grid pattern's two pitches
  by `row` / `col`, because `PatternFeature.angle` may rotate the lattice and a row pitch is
  then not an X pitch. The façade must therefore map `axis="x"` onto row/col using the angle
  — and decide what an axis-named selector means at 30°: resolve to the nearer axis, raise,
  or offer a `row=`/`col=` spelling alongside. Settled with the selector (#872).
- The `role` vocabulary for `sheet.dimension(feature, role)`: which measurements to support
  first (`"diameter"`, `"pitch"`, `"width"`/`"depth"`/`"height"`, `"angle"`, `"radius"`), and
  how the call-site role maps onto the `ParameterId` space (`"depth"` → `"bore.depth"`) when a
  feature has counterbore and spotface depths as well. `"location"` is supported at feature
  granularity (#925); splitting a patterned hole's position per member is **#883**.
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

## Amendment 4 — authored is the preferred source on `Sheet`; automatic is soft deprecated there (2026-08-05)

This ADR presents the two dimension sources as co-equal alternatives: *either*
`auto_dimensions()` *or* an authored set, mutually exclusive, and a build must say which.
The mutual exclusion and the requirement to say stand. The even-handedness does not.

**On the `Sheet` surface, authored dimensions are the recommended source, and
`auto_dimensions()` / `add_dimension()` are soft deprecated** (#1043) — discouraged, still
supported, and **not scheduled for removal**.

### Why the surfaces differ

The choice reads as symmetric in the abstract and is not, once you look at what each surface
is for.

- **It is what draftwright emits.** `--script` writes `authored_dimensions()` and one
  `dimension(...)` line per measurement. The automatic path is the only form the tool itself
  never produces, so a hand-written script using it diverges from every generated one.
- **Amendment/§ "omission means suppression" only holds for an authored set.** Under the
  automatic source there are no omissions to read, so an author cannot say "not that one" —
  the very expressiveness this ADR added is unavailable.
- **An authored list is editable text.** That is what makes "generate a script, then refine
  it" work, for a person or a model. An automatic set is decided at runtime, cannot be seen
  in the file, and cannot be edited line by line.

A caller who has already chosen the declaration surface has chosen to be explicit about
*features*. Being implicit about *dimensions* in the same script is the inconsistency.

### What is explicitly unaffected

`build_drawing(part)`'s automatic dimensioning — the detected front door. Point the CLI at a
STEP file or a build123d object and get a fully dimensioned drawing with no ceremony. That
path is automatic by design and carries no warning; a guard asserts it.

`Sheet.from_part(part)` also keeps its implicit automatic source. It is the on-ramp — detect
the features, get a drawing, then author over it — and adding `dimension(...)` lines
*overrides* rather than conflicts (#921). Warning there would scold the recommended
migration.

### Why soft, and why not a removal date

`docs/deprecations.md` carries ADR 0005 §4: a compat surface names a tracking issue *and* a
removal target, because "a facade with no exit date is a failure mode, not a success".

That rule governs surfaces kept alive only so old code keeps working. This is a different
thing: `auto_dimensions()` works, is supported, and there is simply a better way to say the
same thing. Attaching a removal date we did not intend would be the exact failure §4 names,
wearing a date.

So it raises `SoftDeprecationWarning` (a `UserWarning` subclass), not `DeprecationWarning`,
and lives in a separate "Discouraged" table with no removal target. A guard asserts the class
relationship, so a future change to `DeprecationWarning` fails at the definition rather than
silently acquiring an obligation.

Incidentally the softer label is the louder signal: `DeprecationWarning` is filtered by
default in notebooks, several test runners and library-internal call paths, whereas a
`UserWarning` shows unconditionally.

## Amendment 5 — placement may release a compiler-approved contingency (2026-08-07)

**A plan may carry an approved alternative that stays inactive unless the primary
representation places no marks.** The alternative is dimensional content, not permission for
a renderer to derive content after placement.

#955 supplied the concrete case. A Z-turned part normally states its axial extent through the
step-length chain, so the overall height is omitted. Whether that chain survives is known only
at placement: a crowded chain can be dropped transactionally. The compiler cannot predict the
drop, while the renderer must not recover the height from the bounding box. Treating either
layer as the owner breaks Amendment 1's WHAT/WHERE split.

`RenderableDimensionPlan.contingencies` therefore carries the compiler-built
`ApprovedLadder` for the overall height, paired with the primary representation and its
inactive diagnostic. The orchestrator observes only the primary renderer's placement result:
when the complete chain places zero marks, it releases the already-approved ladder and removes
the now-stale omission. `render_height_ladder` receives an ordinary approved entry and remains
unable to read the model or bounding box. If the released height also cannot fit, that is an
ordinary placement drop, not suppression.

Three constraints keep this from becoming a conditional-suppression flag under another name:

- an authored omission prevents the contingency from being compiled at all;
- a surviving primary leaves the fallback inactive and the omission diagnostic intact;
- contingencies are addressable, so a generated authored `Sheet` script carries the fallback
  intent and makes the same runtime selection as the detected build.

## Amendment 6 — the converse of the boundary: nothing the plan approves may vanish (2026-08-20)

**A renderer must emit everything the compiled plan approves, and where it cannot, it must
say so.** Amendment 1 is one direction of the boundary — content the plan withheld must not
appear. The other direction was never written down, and cost eleven review rounds across
#1215, #1234 and #1216.

Two distinct ways a plan entry fails to reach the sheet, with different fixes:

**1. The annotation is drawn and states less than the plan approved.** An authored tolerance
is the case. The seam is not draftwright's: `build123d_drafting.helpers` resolves a
dimension's text as `label if label is not None else _number_with_units(measured, tolerance)`,
so an explicit `label=` **discards** a forwarded `tolerance=`. Every dimension this engine
emits passes a label, because the compiler owns the value text (Amendment 1) — so `tolerance=`
is unreachable by construction and the suffix must be composed into the label:
`f"{approved.value_text}{_tol_suffix(approved.tolerance, draft)}"`.

That is a deviation from the helper's own API and it should be read as one. It is deliberate:
it is the only route that also renders a `FitClass` (which the ink path raises on), and it is
what makes the sheet internally consistent about limit-pair order, since one formatter serves
every site. It is also fragile in a specific way — passing `tolerance=` looks correct, type-
checks, and renders nothing. The first fix for #1215 did exactly that, and was measured as
working by reading `Dimension.label` back, which reports the same string whether the tolerance
renders separately or is discarded. Only glyph counts and exported paths distinguished them.

**2. The annotation is not drawn at all.** A mandatory overall extent starved out of a full
corridor; a step rung the legibility gate discards. Both were silent: no annotation, no build
issue, no lint. A drawing missing a dimension the author explicitly toleranced was
indistinguishable, to every automated reader, from one carrying it.

This amendment does **not** rule that either must be drawn — whether a feature leader may
starve a mandatory extent, and whether a too-short rung should escalate to a detail view, are
ADR 0014 placement questions and are open (#1236). It rules that the absence must be **reported,
and reported against the measurement it is about**. Silence is not a policy, and it prevents the
policy question from even being asked.

Three constraints keep the report from being a worse cure than the disease:

- **It must not gate the build.** The first cut reported through `placement_unsatisfiable`,
  which `builder._is_required_scale_drop` treats as a required scale drop — so an omission that
  had existed for as long as the code did began raising `ScaleIncompatibilityError` from
  `build_drawing(part, scale=…)` under the default policy, after rebuilding the whole ISO ladder
  to no effect. A drawing that no longer builds is not an improvement on a drawing missing a
  dimension. `overall_dim_withheld` and `step_dim_withheld` report without gating.
- **It must carry the measurement.** An unattributed issue cannot be joined to what is missing,
  so any check asking "was this absence reported" can be satisfied by an unrelated issue.
- **It must be retracted if a later pass draws the measurement after all.** The height ladder
  runs long before the detail view exists; reported-and-never-revisited, its withholding fired
  on a part whose rungs were all dimensioned in the detail. `_retract_resolved_withholdings`
  applies the rule `solve_corridor` already applies to a deduped loser.

**Scope, stated because the guard's is narrower than "everything the plan approves".** The
converse is asserted over `plan.groups` and `plan.ladders`. It is NOT asserted over
`plan.contingencies` — Amendment 5 makes a contingency an approved alternative that is
*deliberately* undrawn unless its primary places nothing, so "approved and unclaimed" is its
normal state — nor over `plan.locations`, which carry no tolerance to drop (there is no
location parameter to author one against). Both are gaps in coverage, not exemptions from the
rule.

### Why a rule rather than more review rounds

The same shape as Amendment 1: #1215's fix took ten sites, found one review round at a time,
each round's sweep scoped to the shape of the site it had just seen. What ended it was not the
tenth fix but the general guard — `tests/test_issue_1215_no_approved_tolerance_is_dropped.py`
decorates every parameter of every feature across a corpus, through both spellings of a
`decorations=` key, and joins what the compiler approved against what the claiming annotation
renders, via the ADR 0010 provenance seam. It found three live sites in its first run and now
asserts both directions of this amendment.

Its own history is the caution about guards, not about renderers. Three predicates reported
green over live drops before the fourth was exact — "contains a space", a regex whose fit-class
alternative matched `C3` on every chamfer, and a substring test one term's suffix could satisfy
on behalf of another's. A guard for a boundary needs to compare against the compiler, not
against a pattern that looks like what the compiler would have produced.
