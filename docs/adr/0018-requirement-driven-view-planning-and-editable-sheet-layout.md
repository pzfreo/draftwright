# ADR 0018 — Requirement-driven view planning and editable sheet layout

- **Status:** **Accepted** (2026-08-16). **Nothing here is implemented yet** — no
  `ViewSpec`, `ViewConstraints` or `ResolvedViewPlan` exists in the code, and the engine
  still builds the fixed front/plan/side/iso topology. Read this as the decided
  direction, not as a description of current behaviour; delivery is phased through
  #1130, first slice a no-behaviour-change `ViewSpec`/`ResolvedViewPlan` representation.
  The "Required evidence before acceptance" list below is retained verbatim as the
  **delivery gate** each slice is measured against — accepting the direction does not
  waive it, and automatic semantic view selection lands only once those invariants are
  guarded.
- **Date:** 2026-08-11 (proposed), 2026-08-16 (accepted)
- **Deciders:** Paul Fremantle (pzfreo)

## Why now — the evidence that converged (2026-08-16)

Proposed from one case (a thin planetary plate where the fixed four-view topology
drove an A1 sheet at 1:1). Two independent investigations then landed on the same
missing decision from opposite directions, neither looking for it:

- **#1187 (leader routing).** After #798 and #1188, ten leaders still cut the part
  across the corpus. Sweeping each one's elbow through 64 directions × 6 shaft lengths
  — far more freedom than any producer offers — found **hundreds of routes clear of
  the material and, for five of six distinct cases, zero clear of everything else**.
  The part's shape is not what traps them: the sheet is full. Every remedy that
  survives that finding is compositional — fewer things on the sheet, more room, or a
  different view set — and none of them is a router's decision to make.
- **#1190 (section A–A).** The section is not part of the scale/layout decision at
  all. ADR 0004 picks a scale by packing view blocks; the section is then placed
  opportunistically into whatever is left, which is why its presence tracked leftover
  space rather than need. Making it a required scale outcome was tried and reverted —
  it turned an optional view into a scale blocker that could raise
  `ScaleIncompatibilityError`. The honest fix was to record the outcome, which leaves
  the structural gap exactly where this ADR says it is.

Both reduce to the same thing: **which views should exist is a decision nothing
currently owns.** That is what tipped this from a good idea to a gap with measurements
attached.

## Context

Draftwright currently builds a fixed front/plan/side/isometric topology, estimates the
annotation strips around those views, and then selects a standard sheet and scale that can
carry the composed blocks.  ADR 0004 and ADR 0014 made the important inner guarantees sound:
a view is packed with its annotation footprint, and feature annotations are collected and
solved rather than hand-placed.  They do not decide **which views should exist**.

That missing decision is visible on a user-supplied thin planetary plate.  Its bounding box is
43 × 217 × 217 mm and its dominant rotational axis is X.  The axial view carries two hole
patterns, several coaxial diameters and a keyway; the conventional front and plan projections
are both thin edge-on views.  The current automatic result chooses A1 landscape at 1:1.  It
retains more definitions than the smaller candidates, but leaves large unused regions because
the fixed four-view arrangement—not the semantic drawing problem—drives the decision.

Experiments characterise the failure:

| constraint | result |
| --- | --- |
| automatic | A1 at 1:1; sparse/unbalanced, but least incomplete |
| A2 automatic | 1:2; still sparse and loses diameter/axial definitions |
| A3 automatic | 1:5; geometry unnecessarily small and more definitions lost |
| A3 forced to 1:2 | visually compact, but supported requirements are dropped |
| A2 forced to 1:1 | one view is 74 mm out of bounds and four callouts drop |

The scale jumps are not a typography defect.  Projected geometry changes with drawing scale;
font height, arrows, gaps, line weights and furniture are physical paper-space quantities and
must remain legible.  A long pattern label can therefore be wider than a 1:5 view even though
the model geometry “fits”.  A candidate cannot be accepted from geometry bounds alone.

A human would likely use an axial view, a longitudinal section and a smaller NTS isometric
orientation view.  Draftwright cannot currently propose that alternative, nor can the `Sheet`
DSL or generated Python script edit the resolved automatic view/layout plan.  Users can request
a section or detail, and can constrain page/scale, but cannot declaratively remove a redundant
view, replace it with a section, or rearrange complete view blocks.

Adding raw view or annotation coordinates to generated scripts would solve the wrong problem.
It would freeze incidental output, bypass recomposition when labels change, and conflict with
the semantic-editing direction of ADR 0001/0012/0016.  Conversely, hiding the automatic plan
behind `build()` denies users legitimate drafting control.  The missing public boundary is an
editable, semantic **view plan**.

## Decision

**Draftwright will introduce one view-planning model between drawing requirements and
projection.  Authored `ViewConstraints` and the automatic planner use the same semantic
`ViewSpec` / layout-constraint vocabulary; the planner produces one immutable
`ResolvedViewPlan`.  Neither owns feature-annotation coordinates.  Page, preferred standard
scale, view set and arrangement are evaluated jointly against complete view blocks measured
with fixed paper-space typography.  A result is feasible only when the real shared annotation
solve preserves every supported requirement and all blocks remain in bounds.**

The conceptual pipeline becomes:

```text
recognition evidence / declared features
                  │
                  ▼
PartModel + applicable drawing requirements + projection convention
                  │
                  ▼
       authored ViewConstraints / automatic defaults
                  │
                  ▼
 candidate ViewSpecs → projection / observability / estimates
                  │
                  ▼
 planner: view set × standard scale × sheet × arrangement
                  │
                  ▼
 exact labels + complete blocks + ADR 0014 placement solve
                  │
                  ▼
 immutable ResolvedViewPlan + outcomes, diagnostics and lint
```

### 1. One value vocabulary, distinct request and result states

The public boundary distinguishes lifecycle states so planning does not become circular:

- `ViewSpec` is a stable semantic value describing one principal orthographic view, section,
  detail or orientation/isometric view.  It contains direction, up vector, section/detail target
  and scale policy—not projected edge geometry or annotation positions.
- `ViewConstraints` is the authored request: required/forbidden specs, relational layout,
  page/scale/style constraints and optional whole-block pins.  `Sheet.views` / `Sheet.layout`
  build this value before projection.
- `ResolvedViewPlan` is the immutable planner result: selected `ViewSpec`s, resolved page and
  scale, block anchors, requirement eligibility/assignment, feasibility and diagnostics.  It is
  produced only after candidate projection and the exact solve; it is never mutated in place.

Automatic and authored modes therefore share one planner and one value vocabulary without
pretending its input and output are the same state.  A generated resolved script converts a
`ResolvedViewPlan` into explicit `ViewConstraints`; it does not feed a result object back into
the builder or mutate a built `Drawing`.

The exact Python spelling is deliberately left to the implementation issue, but the intended
capability is:

```python
s = Sheet(part, page="A2", scale=1.0).auto_dimensions()

s.views.remove("plan")
s.views.section("section_A", through=pocket, replace="front")
s.views.resize("iso", factor=0.75)
s.layout.row("section_A", "side", "iso")

dwg = s.build()
```

The unchanged automatic front door stays clean:

```python
dwg = Sheet(part).auto_dimensions().build()
```

Automatic view selection is the absence of authored view-selection/layout constraints, not a
separate engine; page, scale, style or projection convention may still be authored.  View edits
constrain or replace candidates before projection and annotation compilation.

### 2. Users edit complete view blocks, never feature-annotation coordinates

Relational layout is the primary surface: rows, columns, above/right-of relationships,
alignment and paper-space gaps.  These constraints survive changes in label length, precision,
tolerance and font metrics better than stored coordinates.

An advanced absolute pin may anchor a **whole view block** at a page-space position:

```python
s.layout.pin("side", at=(210, 160))
```

`at` names the projected view origin (the point from which its `ViewCoordinates` are derived),
not the annotation-dependent block centre or corner.  That anchor remains stable when a label
changes the block bounds.  This is not hand-placement of a feature annotation.  The view's
projected geometry and owned annotations are recomposed as one block, and ADR 0014 still decides
the dimension, callout and GD&T positions.  An infeasible pin produces a diagnostic; it does not
silently drop content or move the authored anchor.

Post-build view mutation is not sanctioned.  It could leave projection transforms,
registration, requirement outcomes and annotation geometry stale.  The user edits `Sheet` or
its `ViewConstraints`, then rebuilds the `Drawing`.

### 3. View removal is requirement-aware

Silhouette similarity is not proof that a view is redundant.  Two edge-on projections can
look alike while only one exposes an asymmetric feature.  A view may be removed automatically
only when every requirement it can communicate remains adequately covered by another selected
view.  An authored removal has the same consequence: requirements are rerouted, made visible by
a replacement section/detail, or reported uncovered/unverifiable.

The durable correspondence is ADR 0017's requirement identity and outcome ledger.  Migration
may begin with existing feature/parameter identities, but absence of proven correspondence must
fail conservatively; it must not certify completeness from a similar-looking projection.

Projection/HLR supplies geometry and observability evidence.  It does not decide whether a
diameter, depth or location is a drawing requirement, and it does not own the selected view set.

### 4. Projection convention constrains principal-view layout

First-angle and third-angle projection give principal-view positions semantic meaning.  The
projection convention is therefore a hard planning input, not a title-block symbol added after
packing.  By default, principal orthographic views retain the conventional side and alignment
relationships required by that convention.  A relational constraint that contradicts them is
infeasible; it does not silently override the convention.

Sections, details and NTS orientation views have the placement freedom allowed by drafting
convention.  An explicitly requested unconventional principal-view arrangement must opt out of
the conventional constraint and carry whatever labelling is required to remain unambiguous.  It
is never inferred merely because an unconventional row packs more tightly.

### 5. Page, scale, views and arrangement are one constrained choice

The planner evaluates complete alternatives rather than fixing four views and changing only
paper/scale:

```text
candidate semantic view sets
× preferred ISO 5455 scales
× standard sheets
× plausible relational arrangements
```

A candidate is feasible only if every hard gate holds; feasibility is not a weighted score:

1. preserve every supported requirement or reject the candidate;
2. keep all view blocks and required annotations in bounds and conflict-free;
3. satisfy minimum legibility and selected drawing style;
4. obey projection convention and every authored view/layout/page/scale constraint.

Among feasible candidates, the default page/scale policy is deterministic:

1. choose the largest appropriate preferred ISO 5455 scale admitted by a feasible candidate;
   “appropriate” is bounded by the authored scale policy and drawing style, not by opportunistic
   use of a larger sheet merely to magnify already-legible geometry;
2. at that scale choose the smallest standard sheet that admits a feasible candidate;
3. reduce redundant views and wasted area;
4. prefer conventional projection/section arrangements when otherwise equivalent.

This makes scale preference explicit instead of hiding it in a minimum-legibility threshold.
The default preferred-scale order starts at 1:1 when the model and style make full scale
appropriate, then considers the next smaller preferred scales.  A drafting policy may provide
a different bounded preferred-scale sequence—for example, for very small or very large parts—
without making paper economy the implicit reason to shrink otherwise appropriate geometry.

Drafting policy may replace this ordering explicitly, but neither paper economy nor scale may
outrank requirement survival, legibility, projection convention or authored constraints.  A
smaller sheet whose geometry fits while dimensions drop is not a successful plan.

Planning uses two levels of evidence.  A cheap estimate rejects clearly infeasible candidates
from requirement counts and expected annotation grammar.  The survivor is then compiled with
the actual labels, actual bundled-font metrics and real ADR 0014 placement solve.  Only that
second pass proves feasibility.  If it fails, the planner tries the next candidate or returns an
explicit infeasibility diagnostic.

This ADR plans one physical sheet.  Multi-sheet drawing documents require sheet numbering,
cross-sheet requirement assignment and document-level title/revision semantics that do not
exist today; they are a future extension, not an implicit fallback.  When no one-sheet candidate
is feasible, the planner follows the terminal behavior below.

### 6. Infeasibility is a first-class result, not a silent relaxation

Automatic planning explores a bounded, deterministic candidate set and may escalate page,
preferred scale, semantic view set and unconstrained arrangement within policy.  It never
relaxes an authored page, scale, projection convention, required/forbidden view or pin.

If no candidate is feasible but at least one candidate is renderable, `build()` returns the
least-bad diagnostic `Drawing` so Draftwright's lint-and-repair workflow remains inspectable.
Its `ResolvedViewPlan.feasible` is false and lint contains an error-severity `plan_infeasible`
issue plus the particular uncovered, dropped, conflicting or out-of-bounds outcomes.  Candidate
ranking among infeasible results first minimises lost supported requirements, then
unverifiable associations, then geometric/layout violations; unsupported capabilities remain
explicit outcomes rather than becoming fictional plan choices.  The ordering is deterministic
and is not presented as success.

If no candidate can be projected/rendered at all, `build()` raises the existing class of build
error with the accumulated planning diagnostics.  Export keeps today's lint-first behavior and
may export a diagnostic drawing; callers that require a gate use the existing lint/quality
surface and fail on `plan_infeasible`.  A future strict convenience may wrap that gate, but this
ADR does not create a second build/export path.

### 7. Typography is fixed paper-space input

Drawing scale transforms model geometry into paper geometry.  It does not scale ordinary text,
arrowheads, extension gaps, line weights or title-block furniture.  A style is an authored or
default planning input expressed in paper units, conceptually:

```python
DrawingStyle(
    font_size=3.0,
    arrow_length=3.0,
    text_gap=1.0,
    extension_offset=1.0,
)
```

The actual font face is part of measurement because equal nominal heights do not imply equal
label widths.  ADR 0006's bundled, path-pinned fonts remain authoritative for deterministic
measurement and export.

The optimiser must not progressively shrink text until a sheet fits.  It first reflows,
changes view selection, introduces an appropriate section/detail, selects another standard
scale or enlarges the sheet.  A different valid font size is selected only through an explicit
drawing style/policy; multi-sheet output is outside this ADR.

Principal orthographic views normally share the drawing scale.  Independently enlarged detail
views carry their scale, and orientation/isometric views whose size is adjusted independently
are labelled NTS as appropriate.  Arbitrary unequal principal-view scales are rejected rather
than producing a misleading sheet.

### 8. Generated scripts have explicit automatic and resolved semantics

The generated Python surface must support two distinct promises:

**Automatic semantic plan.**  The script preserves requirements and authored constraints, then
lets the current planner choose again:

```python
s = Sheet(part).auto_dimensions()
dwg = s.build()
```

An automatically selected page, scale or arrangement is not emitted as authored intent.

**Editable resolved plan.**  The script emits the selected named semantic views and relational
layout as explicit `ViewConstraints` so a user can modify them.  It may emit authored or
deliberately frozen page/scale/style constraints, but never raw projected edges or
feature-annotation coordinates.  Feature-targeted sections/details refer to stable Sheet feature
handles.  If the emitter cannot name the recognised feature or express an equivalent semantic
section target, resolved-plan emission fails with a named unsupported capability.  It does not
silently substitute an incidental cut coordinate and claim semantic round-trip.  A raw cut
coordinate is emitted only when that coordinate was itself the user's authored constraint.

The script mode must state which promise it makes.  Explicit user constraints—page, scale,
style, sections/details and view/layout edits—round-trip in either mode.  A resolved script can
return to automatic planning explicitly rather than by deleting mysterious generated state.

### 9. The finished plan and its failures are inspectable

The builder produces one immutable `ResolvedViewPlan` and attaches it exactly once to typed
`BuildState`, the existing ADR 0005 owner of per-build state.  `Drawing.view_plan()` is a
read-only projection of that value for tooling, lint and explanation.  No `_view_plan` or
planner cache is added as an ad-hoc `Drawing` private, and no later stage replaces the attached
result.  It includes selected views, page, scale, arrangement, requirement assignments,
feasibility and diagnostics; it is not a post-build mutator.

At minimum, diagnostics distinguish:

- an authored page/scale/layout constraint is infeasible;
- no supported candidate view exposes a requirement;
- a requirement was view-eligible but its annotation was dropped during placement;
- correspondence is not yet verifiable;
- a non-standard/NTS view scale is deliberate and labelled;
- no complete candidate exists (`plan_infeasible`), including which authored constraints could
  not be satisfied without relaxing them.

## Boundaries with existing ADRs

- **ADR 0001 remains authoritative for generated code.**  The resolved plan is an editable
  domain-semantic representation, not a primitive edge/coordinate DSL.
- **ADR 0004 remains authoritative for compose-then-pack, but this ADR supersedes its
  fixed-topology assumption if accepted.**  The new stage chooses and constrains which blocks
  exist; ADR 0004 still requires each selected view to be composed with its annotation footprint
  before outer packing.
- **ADR 0005 owns the resolved state.**  `ResolvedViewPlan` is attached once through typed
  `BuildState`; this ADR does not introduce a second mutable layout cache on `Drawing`.
- **ADR 0006 remains authoritative for deterministic text metrics.**  The planner consumes the
  same pinned font face the exporter renders.
- **ADR 0011 remains the public declared-model front door.**  `Sheet` gains view/layout intent;
  recognition is not reintroduced into declared build/render merely to choose views.
- **ADR 0012/0014 remain authoritative for annotation edits and placement.**  A view pin anchors
  a block, not individual dimensions, leaders or frames.
- **ADR 0016 remains authoritative for authored dimensioning intent.**  Choosing a view does not
  silently add or suppress the complete dimension set.
- **ADR 0017 supplies requirement identity and explicit outcomes.**  It is the durable basis for
  requirement-aware view coverage; this ADR does not create a competing completeness inventory.

## Consequences

### Positive

- Automatic planning can solve the actual communication problem rather than a fixed four-view
  packing problem.
- Users can make conventional drafting choices through `ViewConstraints` in the same `Sheet`
  script without bypassing the placement engine.
- Page and scale decisions become explainable from requirement survival, font metrics and real
  block feasibility.
- Generated scripts expose meaningful view/layout decisions while remaining resilient to label
  and style changes.
- Sections/details become first-class alternatives rather than additions that always enlarge the
  fixed topology.
- Smaller sheets cannot appear successful merely because dropped dimensions reduced their final
  footprint.

### Negative / cost

- View selection introduces a combinatorial candidate space; bounded domain-specific candidates
  and lexicographic pruning are required.
- Existing feature renderers and tests assume `front`/`plan`/`side` names in places; migration to
  stable `ViewSpec` identities is substantial.
- Requirement→view eligibility cannot be fully sound before ADR 0017 identity/outcome migration;
  early slices must be conservative.
- Resolved scripts add a second explicit script mode and need round-trip/mutation guards.
- Absolute whole-block pins can make a plan infeasible and require clear diagnostics.
- Request/result separation adds explicit conversion when a resolved automatic plan is frozen
  into editable authored constraints, but avoids a stateful half-resolved public object.

## Rejected alternatives

### Keep four views and improve only the packer

This can reduce whitespace but cannot replace redundant projections with a section or explain
which requirements justify each view.  The worm case remains the wrong fixed problem.

### Choose sheet and scale from projected geometry bounds

Rejected because text and annotation furniture do not scale with the model.  A geometrically
compact plan can be less feasible after the model is reduced.

### Let HLR choose views or infer completeness from projected edges

HLR determines visible/hidden projected geometry.  It neither owns drafting requirements nor
provides a durable semantic feature→requirement→annotation correspondence.

### Emit raw view and annotation coordinates in Python

Rejected for feature annotations by ADR 0012/0014 and for views as the primary surface because it
freezes incidental output.  Optional whole-block pins are a bounded advanced constraint, not the
representation of an automatic plan.

### Shrink the font until the current layout fits

Rejected because it makes legibility depend on part complexity and hides a deficient view plan.

### Make automatic and authored view planning separate engines

Rejected for the same reason detected and declared feature paths converge on one compiler: two
planners would drift.  Both modes use the same `ViewSpec` / layout-constraint vocabulary and the
same planner to produce a `ResolvedViewPlan`.

### Use one mutable object for authored constraints and resolved output

Rejected because automatic selection needs projection/observability evidence that does not exist
when authored constraints are declared.  One object would acquire ambiguous “draft”, “partly
resolved” and “finished” states.  The modes share `ViewSpec` and layout-constraint values and one
planner, while `ViewConstraints` and immutable `ResolvedViewPlan` make lifecycle explicit.

### Fall back to another sheet when one sheet is infeasible

Rejected from this ADR's scope.  A multi-sheet drawing is a document model with sheet numbering,
cross-sheet requirement assignment and revision/title semantics.  Until that model exists, a
one-sheet failure remains an explicit infeasible result rather than an unmodelled extra page.

## Required evidence before acceptance

- A synthetic thin rotational plate reproduces the A1/fixed-four-view failure without relying on
  a proprietary or externally supplied STEP file.
- A no-behaviour-change slice represents the current four views through `ViewSpec` and
  `ResolvedViewPlan` and preserves representative rendered semantics.
- Authored `ViewConstraints` cannot be mistaken for an immutable `ResolvedViewPlan`; conversion
  of a resolved snapshot into editable constraints is explicit and round-trip guarded.
- `ResolvedViewPlan` has one typed `BuildState` attachment and a read-only `Drawing` surface;
  structural guards reject another writer or ad-hoc private cache.
- Mutation tests prove deleting each authored view/layout constraint changes the named plan
  decision or fails explicitly.
- Removing a visually similar but semantically necessary view is rejected by an asymmetric
  counterexample.
- Removing a truly redundant view retains every requirement and reduces the selected footprint.
- Fixed paper-space font metrics participate in candidate feasibility; scaling geometry down
  cannot make an overlong label disappear from the estimate.
- A forced small sheet/large scale that drops a requirement is rejected, not accepted with a
  warning-only incomplete drawing.
- Exhausting automatic candidates returns a deterministic diagnostic drawing with
  `plan_infeasible`; an unrenderable candidate set raises with the accumulated diagnostics, and
  authored constraints are never silently relaxed.
- First- and third-angle counterexamples preserve their conventional principal-view relationships;
  a contradictory relational constraint is infeasible rather than silently repacked.
- Generated automatic and resolved scripts round-trip their distinct promises.
- Resolved emission for an unnameable feature-targeted section fails with a named capability;
  deleting that refusal must make a counterexample falsely emit and fail the guard.
- Principal unequal-scale misuse fails; detail/NTS scale differences remain explicit.
- A whole-view pin anchors the projection origin and remains stable when annotation bounds change.
- The default largest-appropriate-preferred-scale → feasible-smallest-sheet ordering is deterministic
  on a fixture where each alternative would otherwise be feasible.
- The worm-style synthetic case selects a materially better complete plan, targeting A2 at 1:1
  if final feasibility supports it.

## Delivery

The phased implementation and case-study measurements are tracked in #1130.  The first slice is
an explicit `ViewSpec` / `ResolvedViewPlan` representation reproducing current behaviour; typed
`ViewConstraints` and script round-trip follow.  Automatic semantic view selection comes only
after request/result lifecycle, projection-convention, terminal-failure and
requirement-coverage invariants are guarded.
