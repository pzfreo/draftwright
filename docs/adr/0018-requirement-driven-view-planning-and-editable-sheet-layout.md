# ADR 0018 — Requirement-driven view planning and editable sheet layout

- **Status:** **Accepted** (2026-08-16), **partially implemented** — see Amendment 1
  (2026-08-21) for the authored surface. Delivery is phased through #1130. What exists:
  `ViewSpec`, `ResolvedViewPlan` and `ViewCoverage` (`view_plan.py`); the layout choice as a
  candidate over scale x sheet x **arrangement**, with §5's first hard gate applied by real
  compile; a view-set-aware dimension plan that re-homes eligible requirements or raises
  `ViewPlanIncomplete` before projection; and a chosen view set that is buildable, refusable
  and reclaims the paper it frees.
  What does not: `ViewConstraints` and the `Sheet` verbs, automatic view SELECTION (nothing
  drops a view on its own — the case study reaches its A2 target and costs six annotations, so
  a gate weighing it refuses), and any planning of sections or details. `_views` is an engine
  seam, not a public option.
  The "Required evidence before acceptance" list below is retained verbatim as the
  **delivery gate** each slice is measured against — accepting the direction does not
  waive it, and automatic semantic view selection lands only once those invariants are
  guarded.
- **Date:** 2026-08-11 (proposed), 2026-08-16 (accepted)
- **Deciders:** Paul Fremantle (pzfreo)

## Amendment 1 — the authored surface mirrors the dimension model, and bans one combination (2026-08-21)

**`Sheet` gains view intent through the same three-verb structure as ADR 0016's dimensions,
and authored views with automatic dimensions is refused rather than resolved.**

The §1 sketch below (`s.views.remove("plan")`, `s.layout.row(...)`) predates the shipped
`Sheet` DSL and does not match it. `Sheet` has no sub-namespaces: every declaration is a verb
on the sheet returning a handle (#922), and aspects chain onto that handle. More importantly,
ADR 0016 already settled how an authored set relates to an automatic one, and view selection
must not invent a second answer to the same question.

### The verb structure

| dimensions (ADR 0016) | views (this ADR) | meaning |
| --- | --- | --- |
| `dimension(...)` | `view(...)` | these lines **are** the set; omission suppresses |
| `add_dimension(...)` | `add_view(...)` | augments the planner's set; requires the `auto_` form |
| `authored_dimensions()` | `authored_views()` | states the source with a verb; makes the EMPTY set sayable |
| `auto_dimensions()` | `auto_views()` | the planner's set; soft-deprecated in favour of authored |
| requesting neither raises | same | a silently unplanned sheet is plausible-looking and wrong |

The soft-deprecation of the `auto_` form carries over for the same three reasons ADR 0016
gives: authored is what `--script` emits, omission-means-suppression only holds for an
authored set, and an authored list is editable text.

`view(...)` selects the authored source on its own, exactly as `dimension(...)` does. That is
not the "implicit-by-usage" design this ADR's predecessor rejected — what makes it safe is the
verb split. A user who wants to *add* one view reaches for `add_view`, so `view` is
unambiguous. Recycling one verb for both sources is the rejected design; two verbs is not.

Cardinality argues for this rather than against it. A drawing carries three or four views and
dozens of dimensions, so "authored means all of them" costs almost nothing here — it is cheap
in precisely the case where the dimension version was expensive enough to need an emitter.

### The ban: authored views with automatic dimensions

`authored_views()` (or any `view(...)` line) together with `auto_dimensions()` raises, at the
point the second verb is called, so the error names the line that created the contradiction.

The reason is this ADR's own thesis stated as an API constraint. **The dependency is one-way:
requirements determine views, and views do not determine requirements.** Of the four
combinations, three are coherent and one is inverted:

- automatic views + automatic dimensions — the planner owns both.
- authored views + authored dimensions — the author owns both, and every line that cannot be
  placed is reported against its own statement.
- automatic views + authored dimensions — **the best case**, and the one to lead with in
  documentation. The planner knows exactly which requirements exist, so it can select the
  minimal view set that carries them. Requirement-driven planning works best when the
  requirements are stated.
- authored views + automatic dimensions — inverted. The user fixes the views and then asks
  the planner to produce requirements that happen to fit them. It cannot, and nobody owns the
  conflict: the failure surfaces as dimensions the compiler assigned to views that are not
  there. Measured during #1130 — a view set the layout accepts still costs annotations, and
  the drawing loses six of them on this ADR's own case study.

Refusing it removes an entire policy from the design. Without the ban, this ADR would owe a
re-homing rule for planner-chosen dimensions landing in author-chosen views, plus a way to
report what could not be re-homed, plus a decision about whether the planner may override the
authored set to save a requirement. With it, the requirement gate keeps exactly one job:
proving that an **automatically** dropped view costs nothing.

The workflow the ban implies is better than the thing it forbids. "I want two views" becomes:
generate the script, delete the `view("plan")` line, rebuild. Both sets are text in front of
you, so stranding a dimension by dropping a view is visible in the diff instead of discovered
at build time.

**The honest cost.** The ban is transitive: authored views now imply accepting the authored-
dimension mirror's gaps, tabulated under "What the mirror does and does not cover" in ADR
0016. The one that bites is inter-feature spans and angles, which have no `(feature, role)`
form and emit as a comment until `RelationDimensionId` lands — so a part whose automatic
drawing carries such a span loses it on authoring views. That is a pre-existing property of
authored dimensions rather than something the ban creates, but the ban propagates it to a new
population and couples authored views to the relation-selector roadmap. Two things make it
acceptable: three of the six gaps shrink as the identity model grows, and the emitter writes a
self-describing comment for anything it cannot re-solve, so the loss is visible in the script
rather than silent on the sheet.

### How a user finds out their view set does not work

An authored view set can be wrong in a way an authored dimension set cannot: omitting a
dimension omits one thing, but omitting a *view* can strand dimensions the compiler assigned
to it. The surface must therefore say so, at the earliest moment that can.

Before the Phase 5.5 planner boundary, it said so at the worst one. A caller whose views could
not carry their dimensions got `ViewNotPlanned` raised from inside `render_centermarks` — an
exception naming the view but not their line, not the dimension that needed it, and not what
to add. That is an internal invariant escaping to a user.

**Three moments, and the rule is to fail at the earliest one that can give a complete answer.**

1. **At the verb.** `view("elevation")` raises immediately, naming the valid view names. No
   build required, and no reason to defer it.

2. **At `build()`, before projection — the one that matters.** The authored view set and the
   authored dimension set are both known before `build()`; that is the same property that
   makes ADR 0016's suppression-by-omission work without a recompose. So the planner resolves
   every approved dimension's view against the authored set and collects those with nowhere
   to go, using no geometry, no projection and no rendering.

   Because the observability map knows which views *could* carry a requirement — not merely
   which one it was assigned — the diagnostic is actionable rather than descriptive. It names
   what is unshowable, why, and which view would show it:

   ```
   ViewPlanIncomplete: 2 authored dimensions cannot be shown by the authored view set
     ('front',):

     envelope.depth    reads horizontally only in `side` — add view("side")
     hole_1.location   reads face-on only in `plan`      — add view("plan")

     View declared at part.py:14 view("front").
   ```

   That example is derived from the maps rather than sketched: with `("front",)` authored,
   `views_showing("y", horizontal=True)` is `None` and only `side` qualifies, `_END_ON["z"]`
   is `plan`, and `envelope.width` resolves to `front` and is therefore absent from the
   report. A diagnostic example that defines a public contract has to be executable, not
   illustrative — the first draft of this block declared `("front", "side")` and then told
   the reader to add `side`.

   Named against the ADR 0016 dimension identity rather than a page-keyed annotation name, so
   it survives a re-solve at another scale and matches the line in an emitted script.

3. **After `build()`.** `dwg.view_decision` is always present, on the model of
   `section_decision` — a caller must not have to infer the outcome from a log line one code
   path emits and another does not. Lint carries anything that passed the coherence check and
   failed anyway. That split is real and worth keeping: *no view can show this* is knowable
   statically, *it did not fit* needs measurement (ADR 0014 Amdt 3).

**There is no `fallback` policy for an authored view set.** `scale_policy` has three settings
because the engine can choose a different scale; here the request IS the answer, so the
choices collapse to raise or report. The default is to raise, on ADR 0016's reasoning that a
plausible-looking incomplete drawing is worse than a visible failure, and on §6 above:
authored constraints are never silently relaxed. A `permissive` opt-in may return the
incomplete drawing, and must warn.

**Consequence for `ViewNotPlanned`.** The pre-projection check now makes that exception
unreachable for a selected set whose approved dimensional requirements were planned. It is an
internal invariant: a user who sees one has found a hole in the check, and that is the bug, not
the drawing. The authored view verbs and their source-line provenance remain #1260; Phase 5.5
provides the compiler result they consume.

**This is gated on the compiler, not the DSL.** The check asks "which view does this dimension
need, given this view set". Phase 5.5 delivered that prerequisite: `planner._group_view` takes
the planned principal set, the planner retains ADR 0016 identity while collecting uncovered
requirements, and analysis runs the check before scale selection or projection. The DSL in
#1260 supplies authored `ViewConstraints` and source-line provenance to this boundary; it does
not reimplement the feasibility decision.

### Sections and details are views, and their verbs are reshaped

`Sheet.section(feature=None, *, at=None)` and `Sheet.detail()` shipped in v0.3.9 (#841/#847)
and cannot express what this ADR needs. Three defects: they return `Sheet` where every
declaration verb returns a handle (#922); `_section` is a single slot, so A–A and B–B cannot
coexist; and `detail()` takes no target at all — it is an enable flag, not a declaration.

They are **hard-deprecated** (`DeprecationWarning`, removal target 0.6.0, batching with the
existing 0.6.0 cohort) rather than soft. Soft deprecation is for a surface that works and is
staying; these are compat surfaces, which ADR 0005 §4 requires to carry an exit date.

The replacements are named, targeted and return handles:

```python
sec = s.section_view("A", through=bore)          # or at=12.0 for a bare cut plane
det = s.detail_view("B", around=hole).scale(2.0)
```

Being views, they join the authored view set: declaring one puts it on the sheet, and omitting
it from an authored set means it is absent. The automatic section trigger (`plan_sections`)
belongs to `auto_views()`.

New names rather than an overload, on two precedents. #720 removed `dimension`'s transitional
dual call-shape because "`dimension` means one thing", splitting `measured_dimension` out as a
separate verb. And ADR 0016 holds that a verb must not mean one thing in one release and
another in the next — which forbids recycling `section` for a different arity and return type.
So `section` retires; it is not reused.

`docs/deprecations.md` currently tells `Drawing.add_view()` callers to "use the section verb",
a pointer that must name the replacement or it directs people at something being removed.

### Derived views augment; only authoring verbs define a set

`Sheet.section(feature)` today forces a section onto an otherwise automatic drawing, and that
capability must survive. Under the rules above it would not: `section_view(...)` joins the
authored view set, which selects the authored source, which triggers the ban — so forcing one
section would cost a user their automatic principal views AND their automatic dimensions.
That is a real loss of expressiveness introduced by this amendment, and the fix is to say
which verbs define a set and which only add to one.

**Derived views are their own set**, separate from the principal views, with the same
three-verb structure:

| verb | meaning |
| --- | --- |
| `section_view(...)` / `detail_view(...)` | these lines **are** the derived set; omission suppresses the automatic section |
| `add_section_view(...)` / `add_detail_view(...)` | augments the automatic derived set; requires `auto_views()` |

The `plan_sections` trigger is the automatic derived set, so omission has something to mean:
an authored derived set with no `section_view(...)` line is how a user says "no section, even
though the counterbore would fire one".

**The ban is on authoring, not on adding.** The general rule, which the principal-view ban is
one case of:

- an **authoring** verb defines a set, so omission is significant, so it can strand a
  planner-chosen dimension in a view that is not there. Authored views of either kind with
  `auto_dimensions()` raises.
- an **augmenting** verb is purely additive. It removes nothing, so it can strand nothing, and
  it is legal with `auto_dimensions()`.

That restores the lost capability exactly, and gives `Sheet.section(feature)` a like-for-like
replacement rather than a lossy one:

```python
s = Sheet(part).auto_views().auto_dimensions()
s.add_section_view("A", through=bore)      # today's Sheet.section(bore)
```

### What this amendment does not change

The value vocabulary of §1 stands: `ViewSpec` as the semantic description, `ViewConstraints`
as the authored request, `ResolvedViewPlan` as the immutable result. What changes is how the
request is spelled on `Sheet` — verbs and handles, not namespaces — and that one combination
of the two sources is refused rather than reconciled.

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

> **Superseded by Amendment 1 (2026-08-21).** The `s.views.*` / `s.layout.*` spelling below
> predates the shipped `Sheet` DSL and does not match it — `Sheet` has no sub-namespaces. Read
> the amendment for the verb structure that replaces it. The lifecycle distinction this section
> draws (request vs result) is unchanged and still holds.

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
