# ADR 0018 — Requirement-driven view planning and editable sheet layout

- **Status:** Proposed; tracked by #1130
- **Date:** 2026-08-11
- **Deciders:** Paul Fremantle (pzfreo)

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

**Draftwright will introduce one first-class `ViewPlan` between drawing requirements and
projection.  The automatic planner and the `Sheet` DSL operate on the same plan.  It owns named
view specifications and authored layout constraints; it does not own feature-annotation
coordinates.  Page, preferred standard scale, view set and arrangement are evaluated jointly
against complete view blocks measured with fixed paper-space typography.  A plan is feasible
only when the real shared annotation solve preserves every supported requirement and all blocks
remain in bounds.**

The conceptual pipeline becomes:

```text
recognition evidence / declared features
                  │
                  ▼
PartModel + applicable drawing requirements
                  │
                  ▼
     automatic proposal + authored ViewPlan edits
                  │
                  ▼
 view projection / observability / block estimation
                  │
                  ▼
 candidate view set × standard scale × sheet × arrangement
                  │
                  ▼
 exact labels + complete blocks + ADR 0014 placement solve
                  │
                  ▼
 requirement outcomes, layout diagnostics and lint
```

### 1. `ViewPlan` is the editable boundary

A plan contains stable named `ViewSpec` values and layout constraints.  A specification may
describe a principal orthographic view, section, detail or orientation/isometric view.  It
contains semantic projection inputs—direction, up vector, section target, detail target and
scale policy—not projected edge geometry or annotation positions.

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

Automatic planning is the default proposal, not a separate engine.  Authored edits constrain
or replace parts of that proposal before projection and annotation compilation.

### 2. Users edit complete view blocks, never feature-annotation coordinates

Relational layout is the primary surface: rows, columns, above/right-of relationships,
alignment and paper-space gaps.  These constraints survive changes in label length, precision,
tolerance and font metrics better than stored coordinates.

An advanced absolute pin may anchor a **whole view block** at a page-space position:

```python
s.layout.pin("side", at=(210, 160))
```

That is not hand-placement of a feature annotation.  The view's projected geometry and owned
annotations are recomposed as one block, and ADR 0014 still decides the dimension, callout and
GD&T positions.  An infeasible pin produces a diagnostic; it does not silently drop content.

Post-build view mutation is not sanctioned.  It could leave projection transforms,
registration, requirement outcomes and annotation geometry stale.  The user edits `Sheet` or
`ViewPlan`, then rebuilds the `Drawing`.

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

### 4. Page, scale, views and arrangement are one constrained choice

The planner evaluates complete alternatives rather than fixing four views and changing only
paper/scale:

```text
candidate semantic view sets
× preferred ISO 5455 scales
× standard sheets
× plausible relational arrangements
```

Selection is lexicographic, not a single opaque weighted score:

1. preserve every supported requirement or reject the candidate;
2. keep all view blocks and required annotations in bounds and conflict-free;
3. satisfy minimum legibility and selected drawing style;
4. prefer useful standard scales;
5. prefer economical paper;
6. reduce redundant views and wasted area;
7. prefer conventional projection/section arrangements when otherwise equivalent.

The exact ordering between scale and paper may be configurable as drafting policy, but neither
may outrank requirement survival or legibility.  A smaller sheet whose geometry fits while
dimensions drop is not a successful plan.

Planning uses two levels of evidence.  A cheap estimate rejects clearly infeasible candidates
from requirement counts and expected annotation grammar.  The survivor is then compiled with
the actual labels, actual bundled-font metrics and real ADR 0014 placement solve.  Only that
second pass proves feasibility.  If it fails, the planner tries the next candidate or returns an
explicit infeasibility diagnostic.

### 5. Typography is fixed paper-space input

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
scale, enlarges the sheet or uses another sheet.  A different valid font size is selected only
through an explicit drawing style/policy.

Principal orthographic views normally share the drawing scale.  Independently enlarged detail
views carry their scale, and orientation/isometric views whose size is adjusted independently
are labelled NTS as appropriate.  Arbitrary unequal principal-view scales are rejected rather
than producing a misleading sheet.

### 6. Generated scripts have explicit automatic and resolved semantics

The generated Python surface must support two distinct promises:

**Automatic semantic plan.**  The script preserves requirements and authored constraints, then
lets the current planner choose again:

```python
s = Sheet(part).auto_dimensions()
dwg = s.build()
```

An automatically selected page, scale or arrangement is not emitted as authored intent.

**Editable resolved plan.**  The script emits the selected named semantic views and relational
layout so a user can modify them.  It may emit authored or deliberately frozen page/scale/style
constraints, but never raw projected edges or feature-annotation coordinates.  Feature-targeted
sections/details refer to stable Sheet feature handles where possible rather than incidental
cut coordinates.

The script mode must state which promise it makes.  Explicit user constraints—page, scale,
style, sections/details and view/layout edits—round-trip in either mode.  A resolved script can
return to automatic planning explicitly rather than by deleting mysterious generated state.

### 7. The finished plan and its failures are inspectable

`Drawing` exposes the resolved plan as a read-only value for tooling, lint and explanation.  It
includes selected views, page, scale, arrangement, requirement assignments and diagnostics; it
is not a post-build mutator.

At minimum, diagnostics distinguish:

- an authored page/scale/layout constraint is infeasible;
- no supported candidate view exposes a requirement;
- a requirement was view-eligible but its annotation was dropped during placement;
- correspondence is not yet verifiable;
- a non-standard/NTS view scale is deliberate and labelled.

## Boundaries with existing ADRs

- **ADR 0001 remains authoritative for generated code.**  The resolved plan is an editable
  domain-semantic representation, not a primitive edge/coordinate DSL.
- **ADR 0004 remains authoritative for outer packing.**  The new stage chooses and constrains
  blocks; compose-then-pack still measures a view with its annotation footprint.
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
- Users can make conventional drafting choices in the same `Sheet` script without bypassing the
  placement engine.
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
planners would drift.  Both authoring modes produce or modify the same `ViewPlan`.

## Required evidence before acceptance

- A synthetic thin rotational plate reproduces the A1/fixed-four-view failure without relying on
  a proprietary or externally supplied STEP file.
- A no-behaviour-change slice represents the current four views through `ViewSpec`/`ViewPlan` and
  preserves representative rendered semantics.
- Mutation tests prove deleting each authored view/layout constraint changes the named plan
  decision or fails explicitly.
- Removing a visually similar but semantically necessary view is rejected by an asymmetric
  counterexample.
- Removing a truly redundant view retains every requirement and reduces the selected footprint.
- Fixed paper-space font metrics participate in candidate feasibility; scaling geometry down
  cannot make an overlong label disappear from the estimate.
- A forced small sheet/large scale that drops a requirement is rejected, not accepted with a
  warning-only incomplete drawing.
- Generated automatic and resolved scripts round-trip their distinct promises.
- Principal unequal-scale misuse fails; detail/NTS scale differences remain explicit.
- The worm-style synthetic case selects a materially better complete plan, targeting A2 at 1:1
  if final feasibility supports it.

## Delivery

The phased implementation and case-study measurements are tracked in #1130.  The first slice is
an explicit plan reproducing current behaviour; automatic semantic view selection comes only
after the editable plan, script round-trip and requirement-coverage invariants are guarded.
