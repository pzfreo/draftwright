# ADR 0014 — Collect-then-solve annotation placement (as built)

- **Status:** Accepted (2026-07-18). **Supersedes
  [ADR 0009](0009-boundary-labeling-strip-placement.md)** (#697): after 9
  amendments + 2 dated notes, 0009 no longer functioned as a decision record —
  its base Decision text described a solver that was later retired and a phase
  that shipped differently. This ADR states the collect-then-solve placement
  model **as it exists in the code today**, in one pass. 0009 is frozen as the
  historical why-trail; read it for *why* each piece evolved, never for current
  state. Changes go here.
- **Deciders:** Paul Fremantle (pzfreo)

**Amendment 1 (2026-08-15, #740) — post-drain leader callouts assign jointly
within each pass.** The best-effort chamfer, fillet, flat, pocket, and groove
passes keep their post-drain stage boundary, but no longer commit the first clear
alternative in job order. Each pass measures all fixed-obstacle-clear candidates,
lowers only numeric leader costs and pairwise conflicts into `layout.py`, and
solves for maximum placed jobs, then minimum total leader length. Feature-specific
physical rules remain hard eligibility at the annotation boundary. Alternative
collection, candidate-pair construction, and the exact search have deterministic
work budgets; an alternative-count, pass-size, candidate-pair, or search-state
limit falls back to, or retains a result no worse than, the former greedy result,
so the amendment cannot place fewer callouts under resource pressure. Pre-drain
diameter and grouped pocket/slot
pattern consumers of the shared placement helper retain their legacy greedy
selection because their winners affect later semantic passes. Cross-pass leader
unification remains separate work (#1166).

**Amendment 2 (2026-08-15, #1166) — compatible same-view feature leaders
share one late inventory.** Automatic and deferred sparse ordinary side/plan
hole callouts and the six post-drain machined-feature leader families no longer
commit in separate local passes. They collect semantic jobs into
`PlacementContext.feature_leaders`; the one `"feature_leaders"` stage after the
corridor drain and before section/detail composition lowers them through
`annotations/leaders.py` into the geometry-only assignment in `layout.py`, then
emits each selected annotation once with its original feature ownership and
`DimensionId`s. The lexicographic objective is maximum placed jobs, maximum
summed semantic priority, minimum fixed-obstacle Policy-B penalty, minimum real
leader length, then stable candidate order. Candidate-to-candidate label/shaft
conflicts are hard. Every shared candidate tests its exact label, shaft, shelf,
and arrow ink against the completed fixed annotation inventory, including
actual-width dimension/witness strokes, their local arrow ink, labels, centre
furniture. Strip-carving AABB inflation is not collision truth. Fixed rejections
name stable annotation components (`:segment:n`, `:arrow:n`, `:label`) in the
bounded trace. A leader-tip attachment is exempt only on an explicitly marked
global turning-axis centreline; an ownerless section/cutting line is ordinary
fixed ink, not an implicit axis exemption. The exemption is limited to the
arrow-sized local tip neighbourhood; near-collinear travel and any later
shelf/label crossing that same axis remain conflicts. Fixed curved helper ink that has no
linear-segment metadata is
lowered once from its rendered planar faces into deterministic component-local
containing polygons before the geometry-only assignment: dashed
`CenterlineCircle` arcs remain separate rather than flooding the ring interior,
and shifted `Dimension` labels retain every arrow that actually rendered instead
of relying on a fixed segment count/order. A fixed rendered face is considered
already represented only when each tessellation triangle fits within one same
known component. The one rendered survivor uses the same rule, so neither side
can bridge separate shaft/shelf/arrow/label regions. Curved faces additionally
retain no exact OCC area outside the analytical union, closing the space between
tessellation stations. Residual-face lowering applies to every rendered fixed
annotation, including filled datum/GD&T glyphs that expose no corresponding
segment metadata. Candidate and selected rendered-survivor construction are
fail-closed per alternative. If selected construction raises or violates this
contract, the joint result is abandoned and the canonical lazy producer floor
validates its tail in order; one bad helper survivor therefore cannot turn a
valid later alternative into a false drop, and an all-invalid tail is diagnosed
as a validation-stage geometry failure rather than spatial exhaustion, so it is
not treated as a scale-remediable placement drop. Compiler invariant
violations (for example, rendered hole ink without its semantic label) remain
loud rather than being reclassified as optional placement failure. The complete mandatory
title band remains a hard reservation even after its strokes and glyphs render;
blank title cells are not placement space. Established Policy B remains explicit for both
ordinary-hole and machined-feature leaders: a candidate crossing late fixed
furniture stays eligible with a penalty, while page, label-over-own-silhouette,
and new-leader conflicts remain infeasible. If such a candidate wins because
every clear relocation is worse, the drawing persists a
`feature_leader_crossing` info finding with semantic measurement provenance; trace is
additional explanation, not the only record. Shaft routing through a part
silhouette is now priced by Amendment 3. This preserves a required callout when every relocation
is worse, without allowing a shorter crossing to beat a clear route or silently
certifying the retained crossing as clean.

Pattern queues and dense table-eligible loose-hole inventories retain their
specialised immediate whole-queue placement because their winners control later
pattern furniture and transactional table replacement. Profiled-bore queues
also retain their established placement; Amendment 3 prices silhouette routing
for the shared population but deliberately does not enter these placers, whose
winners carry downstream semantics.
This compatibility boundary keeps Amendment 2 out of those downstream
semantic decisions rather than predicting their future footprint. Ordinary-hole
alternatives lower label/shaft geometry analytically and materialise only the
selected OCC `Leader`; the rendered survivor is checked against that geometry
before it is committed.

The inventory is bounded before candidate OCC construction by job and
measured candidate-work caps. Fixed rendered ink is itself component-bounded while
it is lowered, cached once across joint/fallback use, and then guarded by the
candidate×fixed-obstacle probe cap. Quadratic candidate geometry has a separate
pair cap, and the exact solve retains its existing state cap. A cap
replays the original lazy candidate stream in
canonical pass order, retaining each producer's former greedy/queue floor and
recording any Policy-B blockers while exact classification remains within the
same fixed-probe budget. If that budget is exhausted, the replay performs no
unbounded fixed-component scan and persists a
`feature_leader_fixed_ink_unverified` info finding instead. That uncertainty is
part of the legibility quality inventory, so an unverified replay cannot certify
a perfect legibility score. Page bounds,
label-over-own-silhouette, and the
mandatory title reservation remain hard under every resource fallback. A
state-cap incumbent is not emitted directly because its new exact-ink seed is
not necessarily that producer floor; the trace nevertheless retains every fully
admitted joint candidate as abandoned, plus the separately replayed producer
selection, so exhaustion remains explainable. Early
section rows are marked provisional and excluded from the primary assignment:
an optional not-yet-committed section cannot veto a required leader. After an
optimal primary result exists, one separately probe/state-bounded refinement may
prefer an equally complete, equally important result with no worse committed-ink
penalty that also clears the provisional exact components. Resource exhaustion
retains the primary result. The section pass then checks its exact final
cutting-plane ink against landed leaders, performs at most one bounded outward
end-symbol repair, and yields if a conflict remains.
The mandatory not-yet-rendered
title-block band remains a hard fixed obstacle. The solve trace records the one
shared inventory, every bounded candidate and its outcome, named committed and
provisional blockers, refinement status, objective tuple, selected alternatives, and bounded
fallback. A single live post-build
`Drawing.callout()` remains the documented finished-sheet exemption; a deferred
batch enters the shared stage.

**Amendment 3 (2026-08-16, #798) — a shaft through the part body is a priced
Policy-B cost, measured on one filled lowering shared with the critique.** Leader
routing and the `leader_crosses_silhouette` critique now solve against the same
per-view *filled projected material*: the part's faces are tessellated once per
build in model space (`projection.part_material_mesh`) and each view projects that
one mesh into its own page plane (`view_material_field`), held on `BuildState` and
built at most once. Both consumers call `_geometry.material_reentry_span`, so a
route the solver accepts cannot be one the critique then reports — one predicate by
construction, not by agreement.

The measured quantity is **re-entry**, not traversal. A leader is attached to the
feature it names, so its first passage out of the body is the legitimate exit every
⌀, hole, bore and pocket callout makes; charging it would price every correct leader
on the sheet as defective. A second traversal means the shaft left the body and cut
into something else. This is the filled form of the superseded outline-crossing rule
(one crossing is an exit, two is a cut) and it inherits none of that rule's
exemptions: a shaft passing over a through-hole re-enters no material, so the
`covers_diameters` escape is gone. Removing it exposed real cuts the outline form
could not see, because it exempted the whole annotation rather than the exit.

The lowering is taken under explicit control (copy → `BRepTools.Clean_s` →
`BRepMesh_IncrementalMesh`) rather than by tessellating the shape where it lies. OCC
caches a triangulation and returns it for any later request, even a strictly finer
one, so `face.tessellate(deflection)` alone yields whatever mesh an earlier
operation left behind — making the field a function of build *history*, the silent
cross-platform layout variable ADR 0006 exists to remove. The page-space chord
tolerance is pinned and the probe is exact half-plane clipping with a fixed-point
interval union: no sampling, no rasterisation, no tolerance sweep (ADR 0001).

Material is a **cost, never an eligibility gate, and never an acceptance test**. It
joins the existing fixed-obstacle Policy-B penalty at a stated exchange rate — one
unit per visible stroke width of buried shaft — because neither strict ordering
survives the range: a 0.3 mm graze is not worse than crossing a dimension line, and
a 63 mm cut through three lobes is far worse than crossing several. A cut the sheet
cannot show does not steer the solve. It also joins the committed major component of
the provisional refinement, so optional section furniture cannot be bought with a
real cut. A nested feature may have no clear route at all; dropping its callout to
keep the outline tidy would trade a required measurement for a cosmetic one.

**The floor is where this actually bites.** Measured across every fixture, the
Amendment 2 joint assignment runs only on modest inventories — a twenty-job part
expands past the candidate cap — and every observed cut was on a part that had
fallen back. Raising the cap moves the bottleneck to the next guard and multiplies
build time; that is an architectural boundary of the exact solve, not a tuning knob.
So the floor gained a bounded lookahead: a job whose first acceptable route cuts
looks a little further for one that does not, and otherwise keeps the least-cutting
candidate. A job whose first acceptable route already clears the body selects it in
the same place the pre-#798 floor did, so the blast radius is only the defective
jobs. Because the floor is sequential, a re-chosen winner is a different obstacle for
later jobs; the placed count is unchanged on every fixture and is pinned per part by
test, rather than guaranteed by a comparison sweep.

A post-placement **relocation** pass was built and rejected. Moving an already-placed
leader instead of re-placing it makes cardinality invariant by construction and
reaches placers the shared inventory never owned. It is weaker alone (it must clear
the complete final inventory, by which time the clear space is taken) and it breaks
the hole-table transaction: relocating a hole callout after the fact invalidates the
bookkeeping this ADR fences off precisely because pattern callout winners carry
downstream furniture and table semantics. Recorded so it is not re-derived.

The material term reaches every placer that weighs routing, not only the shared
inventory: the within-pass assignment and the legacy first-clear scope in
`place_machined_leader_jobs` takes the same preference through the same
`material_penalty_units`, so boss diameters and the machined families cannot drift from
the critique either (#1187). The pre-drain diameter and grouped pattern consumers keep
their pure legacy selection, as Amendment 1 requires.

What remains crossing after all of this is **not** unrouted work. Measured on the
finished sheet, the retained crossings have hundreds of routes clear of the *material*
and **none** clear of everything else: every alternative collides with committed ink,
the page margin, or the silhouette. The sheet is full, and Policy B keeps the callout at
a logged cost rather than dropping a dimension for tidiness. That claim is measured per
case rather than asserted (`tests/test_issue_1187_unroutable_leaders.py`), so it moves
with the drawing instead of going stale.

Widening a producer's candidate fan to reach those routes was tried and rejected:
richer candidates push dense parts back over the per-view budget and out of the exact
solve, which cost more than it bought. Candidate richness trades against solve reach,
and #1188 records where that balance currently sits.

**Amendment 4 (2026-08-16) — a work budget must bound measured work, not predict
it.** Three guards were found in one session silently disabling the feature they
protect, on ordinary input:

| guard | predicted | actual | outcome |
| --- | --- | --- | --- |
| feature-leader candidate cap (#1188) | 20 jobs × ~72 candidates vs a 512 cap | — | the joint assignment never ran on ANY dense part, so Amendment 2's guarantees applied precisely nowhere they were needed |
| balloon top-lane probe bound | 73,712 vs a 50,000 cap | — | 1.5× over; every hole-table balloon refused |
| balloon carve probe bound | 10,546,848 vs a 5,000,000 cap | **21,228** | 497× over; a hole table refused at 0.4% of its real cost |

Each was correct in intent and wrong in effect. The common fault is a **conservative
pre-estimate**: a closed-form worst case computed before the work, sized by multiplying
maxima that never co-occur. The carve bound assumed every retained box contributes its
full complement of criticals and that every one survives to be probed; on the very input
its own test called pathological it was wrong by three orders of magnitude.

The rule this ADR now records:

- **Prefer counting real work to predicting it.** A live counter that stops at the cap
  bounds the work just as a pre-estimate does, and cannot refuse work that fits.
- **Where a pre-check is genuinely needed, it must be EXACT, not conservative.** The
  top-lane gate now multiplies the actual candidate-lane count (built in one linear
  pass) by the obstacle count, because that product *is* the loop's cost.
- **A budget that fires is a capability loss, and must be observable as one.** All three
  degraded silently: the drawing simply came back missing a table, a joint solve or a
  route, with a lint line at most. A guard firing should say which capability it cost.

This is a placement-layer instance of the same discipline ADR 0001 applies to solvers:
the bound has to be explainable, and "the estimate said it might be expensive" is not an
explanation a user can act on.

**Amendment 5 (2026-08-24, #1308) — machined-feature leaders use the existing
analytical tier, and candidate budgets buy measured work.** The shared
`FeatureLeaderJob` seam now lowers the plain-text label box, shaft and shelf for
chamfer, fillet, flat, pocket, groove, boss, polygonal boss/stock and Y-axis step
diameter callouts through the same `leader_callout_geometry` contract already used
by ordinary holes. The generic pass measures each distinct label once through the
cached renderer-faithful `_text_size`; candidate exploration constructs no OCC
`Leader`. Only a selected survivor is materialised, and its rendered label and ink
must match the analytical candidate before commit. A mismatch remains a
validation-stage failure and replays the canonical producer tail. The six
post-drain adapters enter the canonical late exact inventory. Y-axis diameters,
boss diameters and polygonal boss/stock callouts still emit at their pre-drain
semantic stage because their winners feed downstream passes. A finished-sheet
single-feature `Drawing.callout()` likewise has no pending late inventory to join.
Those immediate consumers invoke the same `FeatureLeaderJob` measurement,
materialisation and rendered-validation machinery through its lazy first-clear
producer floor. Thus every listed family uses one placement implementation while
the established compatibility boundary continues to decide whether a job
participates in late exact assignment or its immediate producer floor. No second
placement mechanism or new candidate lane is introduced.

The old 512-candidate cap treated every candidate as equally expensive. Measurement
on the #1308 corpus showed an OCC candidate at about 14.8 ms and an analytical one
at about 0.1 ms. The joint-inventory guard is therefore denominated in 0.1 ms work
units: analytical measurement costs one, OCC measurement costs 150, and the
per-view allowance is 76,800 units. That preserves the former worst-case allowance
of 512 OCC probes while allowing the cheap tier to explore more alternatives for
the same measured-work ceiling. Trace records the projected joint work by view and
the per-view limit, so a fallback states the capability it lost. Fixed-ink inventory
components and candidate×component tests remain directly counted work (one unit per
component/probe) under their 100,000-unit cap; they were renamed as work rather than
relaxed. Pair and search-state guards retain their own direct-operation units.

This does not supersede ADR 0014: the collect → solve → emit decision and all prior
compatibility boundaries remain current. The change completes the analytical tier
described by Amendment 2 and applies Amendment 4's budget rule to the now-observed
two-tier cost model.

**Amendment 6 (2026-08-25, #1334) — same-batch dimension ink participates
before commit.** Strip assignment prevents collisions with previously committed
occupancy, but dimensions surviving one corridor batch were formerly invisible to
their siblings until emission. Immediate turned-part step chains had the same gap.
After strip positions are assigned, the annotation layer now measures the complete
natural dimension batch and selects bounded, along-dimension-line label alternatives
before committing it. The lint backstop and this selector share the exact-label-region,
connected-stroke crossing measure and `MIN_CROSSING_MM`; filled terminators participate
through their attachment tips because the helper does not expose arrow polygons.
Pinned candidates are immutable, page bounds use the common drawable margin, and an
immediate chain may not introduce contact with already committed annotation ink.

The selector is a deterministic, strictly improving local solve. At most sixteen
nearest derived centres plus the two association bounds per involved candidate are
considered for at most twice the batch size in iterations. A clean batch returns the
already-built objects unchanged. A
conflicted batch rebuilds only cached alternatives because moving a label also moves
the helper's line cutout: translating label boxes analytically would miss the coupled
change in line-work that resolves the reported defect. Every trial is rescored against
the complete batch. Semantic evidence riders are copied to a selected rebuilt object;
its fresh placement specification is retained. If no bounded improvement exists, the
natural result survives and `annotation_ink_overlap` reports the infeasible layout.
This is a refinement inside **Emit**, not a second placement engine or permission for
raw-coordinate placement.

## Context (short — the full story is 0009's)

A recurring defect class (#133/#225/#305: the "invisible occupant" — one strip,
several placers, no shared occupancy model) motivated a control-flow inversion:
render passes stop committing geometry as they run, and instead **collect**
every strip occupant as a candidate so **one solve per strip** places the whole
set. This is boundary labeling (Bekos et al. 2007); the research backing is
[`research/annotation-placement-boundary-labeling.md`](../research/annotation-placement-boundary-labeling.md).
The migration is complete (#636, epic #635): the guarantee below now holds for
every automatic-pass strip occupant **by construction**.

## Decision — the model as built

Per view, per strip: **collect → solve → emit.**

- **Collect.** Each render pass measures its render-intents (ADR 0008 / ADR
  0015) into candidates and *registers* them — `register_corridor` queues a
  `CorridorCandidate` into the run's `PlacementContext.corridor_batch`
  (`annotations/_common.py`), keyed by `(view, side)` — instead of calling
  `dwg.add(...)` mid-flight. Failures are first-class `Escalation` objects on
  `ctx.escalations` (kind/view/feature/reason/remedies), resolved by one
  resolver pass (`_maybe_tabulate_holes`, `annotations/orchestrator.py`) — not
  stringly-typed lint greps.
- **Solve.** `drain_corridors` runs once, after every corridor-feeding pass has
  registered, and executes one `solve_corridor` per strip:
  - **Select** — dedup coincident spans by `(dedup, precedence)` (the
    higher-precedence measurement survives; a dropped winner promotes its top
    loser); over capacity, `plan_strip` (`layout.py`) drops the lowest
    `(priority, key)` — a ranked selection, not an arrival-order drop.
  - **Assign** — the original multi-side assign step was evaluated (P2/#322)
    and **not needed**: each pass picks its candidate's strip before solving,
    with alternate-side fallthroughs in `on_drop` callbacks. What survives as
    assignment is (a) segment assignment within a carved strip
    (`carve_free_segments` + innermost-first fill in
    `place_strip_candidates`), and (b) the balloon pass's genuinely global
    band assignment — a deterministic max-cardinality flow solve
    (`_assign_balloon_bands`, `layout.py`, #516).  A dense scattered-hole
    table escalation requests perimeter coverage (#901): within the maximum
    flow, the first use of each preferred usable band receives a bounded
    distance credit before leader length is minimised. This is deliberately a
    preference rather than a lexicographic override—a remote band stays empty
    instead of creating a cross-part leader. The render pass supplies only band
    names, capacities, and numeric costs, keeping the solver geometry-only;
    ordinary and manually requested balloons retain pure minimum-cost
    assignment. The perimeter render pass measures candidate
    lanes at obstacle boundaries and carves each into free horizontal segments;
    a geometry-only dynamic-programming solve assigns ordered members across
    those segments at minimum L1 leader cost.  This keeps a local remote
    obstacle from pushing the entire ring beyond its outer edge (#125), while
    preserving crossing-free member order.  The selected lane must hold both
    its balanced share and any member-count deficit left by the other bands, so
    lane selection cannot weaken the downstream solver's maximum-cardinality
    guarantee whenever such a lane exists. If no lane meets the target, the
    nearest lane wins rather than recreating the remote beyond-all-obstacles
    geometry; any resulting capacity loss is surfaced as `balloon_dropped`.
  - **Order** — label order along the strip = site/feature order (candidates
    sort by anchor coordinate), so leaders between **distinct** strip-axis
    coordinates are **crossing-free by construction**; coincident sites
    tie-break by key for determinism, which is not crossing-optimal
    (`plan_strip`'s own docstring carries the same qualifier). Corridor
    candidates additionally carry an `order` key segregating size dims from
    the monotonic datum-location ladder (#346).
  - **Space** — the deterministic minimum-total-leader-length **L1 solve**:
    `_solve_strip_1d_pava` (`layout.py`), weighted-median PAVA with per-pair
    gaps, a global box clamp, and a fixed *lower-median* tie convention —
    deterministic by construction (ADR 0001), pure standard library.
    **Anchoring** (`StripCandidate.anchored` → `_ANCHOR_WEIGHT`) pins a
    candidate at its natural position while the rest flow around it; it is a
    spacing hint, not a drop immunity.
- **Emit.** `place_strip_candidates` carves the strip around the *complete*
  occupancy (`strip_obstacles` — full rendered footprints, decomposed per
  stroke since #685, not label boxes), evaluates candidates on analytical or
  probe footprints (`dim_footprint`, #602 — no OCC build per probe), then
  builds each natural survivor **once** and re-validates its real box (corridor
  blockers, the `forbid` title-block box, out-of-band obstacles) — a
  prediction miss degrades to a later-segment retry, never a collision. A
  dimension batch with peer ink conflicts may exceptionally rebuild the bounded,
  cached label alternatives defined by Amendment 6; unchanged survivors are not
  rebuilt or revalidated.
  Feature provenance is recorded at this drain seam
  (`CorridorCandidate.feature` → `dwg.add(..., feature=)`, ADR 0010).

**One stage order, two entry paths.** `_PASS_SEQUENCE`
(`annotations/orchestrator.py`, #699 slice b) is the single canonical stage
tuple; both `_auto_annotate` and the finalize drain (`Drawing._drain_intents`)
hand their stage dicts to the shared `run_stages` executor, so neither path can
run a stage the sequence does not name, nor in a private order. The `"drain"`
stage is `drain_and_reconcile` — `drain_corridors` followed by
`reconcile_witness_labels` (#690, label shifts for witness crossings) — shared
verbatim by both paths. `drain_corridors` also coordinates view corners:
before each strip solves, the innermost-tier footprints of not-yet-drained
same-view siblings' **force** candidates join its obstacles, and `on_drop`
fallthroughs are deferred via `ctx.post_drain` until every strip has drained.

**Best-effort leader decoration places after the drain** (#733, generalising
the grooves precedent): the machined-feature leader-callout passes
(chamfers/fillets/flats/pockets/grooves) sit *after* the `"drain"` stage in
`_PASS_SEQUENCE`, so a principal dim that registers early but places only at
the drain can never have its strip stolen by an immediate callout — the
callouts' clear-room check sees the full drained occupancy and yields (a
warning-level drop) where a principal dim now sits. Pre-#636 the ladder's
early *placement* enforced this implicitly; once it became register-then-drain,
a pocket callout could fill the front-right strip and hard-drop the forced
overall-height dim (CTC-04). Ordering, not reservation: a predicted-footprint
reserve was tried and rejected — phantom reservations displaced callouts into
exactly the space other principals needed.

Compatible automatic/deferred feature leaders are likewise
**collect → assign → emit** (#740/#1166). The side/plan hole renderer and the
six `place_machined_leader_jobs` adapters register rich `FeatureLeaderJob`s in
`annotations/leaders.py`; the canonical late stage lowers analytical ordinary-
hole alternatives and bounded rendered machined-feature alternatives against
the completed fixed inventory, derives conflicts from exact leader ink plus
preset-aware stroke/label occupancy, and calls the geometry-only
`_assign_leader_candidates` in `layout.py`. The bounded solve uses the
Amendment-2 objective above. Deterministic candidate-count, pass-size,
pair-probe, and search-state caps retain the old lazy first-clear floor (or a
strictly better incumbent). Front, pattern, profiled, and dense table-eligible
hole callouts, pre-drain diameter/pattern consumers, and the finished-sheet live
verb retain their specialised immediate paths because they do not belong to
this compatible late population.

**Policy B** (two-precedent pattern, ratified 2026-07-02 — 0009 Amendment 2):
when avoiding an occupant would cost more than a bounded relocation, keep the
annotation at its natural position and accept a visible, logged crossing —
never an unbounded search, never a silent drop of a real annotation for a
placement reason alone. Realised as `force=True` candidates (a second
`place_strip_candidates` pass that skips the corridor check) and the permanent
`BENIGN`/`SPACE-CONSTRAINED` entries in `tests/test_layout_cleanliness.py`'s
`_KNOWN_OVERLAPS` (only `PENDING` entries are debt).

## The by-construction guarantee and its exemptions

Every **non-exempt** automatic-pass strip occupant is a candidate in the shared
solve; the exempt placements below run only *after* the drain (or off the strip
axes entirely), so they see the completed occupancy rather than racing it. That
is what removes the invisible-occupant collision class — *provided* no pass
regresses onto the solver-invisible single-position carve
(`carve_free_position`, `annotations/_common.py`). A **fail-closed guard**
(`tests/test_carve_free_position_callers.py`, `_ALLOWED_CALLERS`) pins the
only permitted callers; any new caller anywhere under `annotations/` trips it.
The current allowlist, each an explicit exemption:

- **`_place_pitch_dim`** (`holes.py`) — *permanent*: the pitch fallback
  searches an arbitrary diagonal outward vector, so its dim cannot occupy a
  1-D axis-aligned strip tier and cannot be a solve candidate at all.
- **`render_gdt`** and **`render_plates`** (`from_model.py`) — primary
  placement *is* a corridor candidate; the carve runs only in a
  `ctx.post_drain`-deferred drop fallthrough, after every corridor has drained
  (so it cannot preempt a sibling's reserved corner).
- **`add_feature_callout`** / **`add_feature_location`** (`holes.py`) — manual
  post-build verbs: a single user-driven annotation onto a *finished* sheet,
  where every occupant is already placed and there is no shared drain to join
  (the #426 manual-edit half; cousins of the ADR 0012-exempt `place_dim`).

A genuine new exemption must be recorded **in this list first**, then added to
the allowlist (the guard's failure message cites the ADR 0009 note this
section replaces).

## Glossary — one concept, three names

- **Strip** — the geometric record: `_core.Strip`, a 1-D annotation band
  adjacent to a view (`anchor`/`outer_limit`/`direction`/`gap`/`spacing`).
  The mutable `allocate` cursor is retired (#150); placers read its bounds via
  `strip_free_span` and carve.
- **Zone** — the per-view grouping of strips: `_core.ViewZones`
  (`right`/`above`/`below`/`left`), instantiated by `analysis.py` as
  `fv_zones`/`pv_zones`/`sv_zones`. "Zone" names the collection, "strip" the
  individual band — same objects, two vantage points.
- **Corridor** — a strip viewed as a *shared solve domain across passes*: the
  `(view, side)`-keyed batch in `PlacementContext.corridor_batch`. The name
  entered with the first cross-pass unification (#345/#346, 0009 Amendment 6)
  and stuck for the register/drain machinery. Caution: `corridor_blockers`
  uses the same word for a different thing — the 2-D *witness corridor* a
  right/below dim occupies between the view edge and its dim line, which the
  1-D strip carve cannot represent and which is checked separately.

Two candidate types, layered — the outer wraps the inner:

- **`StripCandidate`** (`layout.py`) — the geometry-only solver input: `key`,
  `anchor`, `size`, `priority`, `anchored`. It *is* a measured render-intent:
  the collect step (in `annotations/`, which may depend on the IR) projects
  intent → page geometry and hands the solver only that, so `layout.py` stays
  a leaf with no IR dependency. A few passes build these directly (the
  concentric-bore leader stack in `from_model.py`).
- **`CorridorCandidate`** (`annotations/_common.py`) — the rich render-intent
  carrier for the cross-pass corridors: a `build(pos) → Dimension` closure,
  the ladder `order` key, `dedup`/`precedence`, `priority`,
  `anchored`/`natural` (how ADR 0012 pinned edits join), `force` (policy B),
  `feature` provenance, real `size` footprint (wide occupants — a ~24×6 mm
  GD&T frame — reserve their true extent, #61), the `forbid` box, the
  analytical `footprint`, and `on_place`/`on_drop` callbacks carrying each
  pass's own bookkeeping.

The boundary: `solve_corridor` → `place_strip_candidates` constructs
per-segment `StripCandidate`s → `plan_strip` → `_solve_strip_1d_pava`. Above
that line the code knows the drawing, closures, and lint; from `plan_strip`
down it knows only numbers.

## Dropped from 0009, deliberately

- **The solver-library evaluation** (0009 Amendment 3's 10-row table): the
  history in one line — the original Cassowary/`kiwisolver`
  constraint-satisfaction solve and its `scipy.optimize.linprog` replacement
  were *both* retired (non-unique L1 optima broke cross-platform determinism
  on day one) for the dependency-free weighted-median PAVA that shipped
  (`_solve_strip_1d_pava`). Neither `kiwisolver` nor `scipy` is a dependency.
  0009 Amendments 3–4 hold the full evaluation and post-mortem.
- **The banded-PAVA DP** (0009 Amendment 5): retired by Amendment 9 (#381).
  `plan_strip` has no keep-out-band parameter; a reserved row (centre-line,
  location-dim extension) is an ordinary interval in the caller's
  `carve_free_segments` carve, like any other obstacle.
- **Rotted line references.** 0009 cited `holes.py` line numbers that no
  longer resolve. This ADR cites files and symbol/test names only.
- **The amendment trail.** The nine amendments and the migration phase issues
  (#317–#323, #351, #318, #345/#346, #61, #524, #381, #636) stay in 0009 as
  the why-trail; this ADR does not restate them.

## Relationships

- **ADR 0001 / 0002** — determinism is why the solve is an exact, explainable
  algorithm ("label *i* sits here because order + min-gap + shortest leader"),
  never a metaheuristic; repair stays a peephole safety net (it no longer
  touches `annotation_overlap`).
- **ADR 0003** (retired) — the historical constraint-based inner-layout frame.
  This ADR is its implemented successor for strips. The carrier is
  `StripCandidate`/`plan_strip`, not 0003's deleted
  `Placeable`/`LayoutSolver`. The page-global 2-D solve in #94 was closed as
  superseded and unnecessary; ADR 0004 owns the fixed-topology outer layout.
- **ADR 0004** — the **outer** layer: compose-then-pack keeps view blocks
  disjoint; this ADR is the **inner** per-view layer whose deterministic
  annotation boxes are exactly the block footprints 0004 packs.
- **ADR 0008 / [ADR 0015](0015-part-drawing-compiler-as-built.md)** — the
  planner's render-intents are what the collect phase measures into
  candidates; 0015 restates that compiler shape (superseding 0008 in the same
  #697 sweep).
- **ADR 0010** — provenance is recorded once at the drain seam
  (`CorridorCandidate.feature`), not tagged through every pass.
- **ADR 0012** — the user-edit layer **on this same solve**: `dimension(...,
  pin=, priority=)` intents become corridor candidates (`anchored=pin`), re-run
  by `Drawing.finalize()` through the same `_PASS_SEQUENCE`/drain. Not
  restated here — 0012 is current.
- Roadmap: [`plans/strip-layout-boundary-labeling-roadmap.md`](../plans/strip-layout-boundary-labeling-roadmap.md);
  research note as above. Guard tests:
  `tests/test_carve_free_position_callers.py`,
  `tests/test_layout_cleanliness.py`.

## Consequences (standing, not aspirational)

- The invisible-occupant collision class is removed by construction; the
  fail-closed guard keeps it removed.
- Deterministic, explainable, dependency-free placement; over-capacity is a
  priority-ranked selection with first-class escalation, not an arrival-order
  drop.
- Compatible sparse ordinary side/plan hole and post-drain machined-feature
  leaders are no longer arrival-order dependent across passes: one bounded late
  assignment maximises semantic survival, prefers clear/priority-ranked routes,
  and then minimises leader length, with the legacy greedy result as its
  resource-cap floor (#740/#1166).
- The explanation is **recordable** (#736, from the #733 post-mortem): the
  opt-in solve trace — `build_drawing(trace=…)` or `DRAFTWRIGHT_TRACE=<path>` —
  dumps one JSON file per build with two distinct record types: `solves` (each
  corridor solve's candidates, carving obstacles (named), free segments, and
  per-candidate outcomes) and `pass_events` (the standalone strip passes plus
  the *immediate* placers — the post-drain machined-feature leader callouts and
  the turned diameter/step-length set-solves — each with per-item
  placed/dropped outcomes, so the pre-#734 drain-time occupants stay visible
  post-drain). `SolveTrace` is threaded as `PlacementContext.trace` (default
  off, nil cost), participates in `finalize()`'s #647 transaction
  (snapshot/restore on rollback; the file is rewritten only after a successful
  drain), and is recording-only — an unwritable path logs a warning, never
  aborts a build. A `placement_unsatisfiable` strip-full drop names the
  occupants that filled the strip. Diagnosing a drop is a `jq` query
  (`.solves[].outcomes[]` for corridor dims, `.pass_events[].items[]` for the
  rest), not a custom-script rebuild.
- Honest edges that remain: placement is per-view (cross-view contention rests
  on ADR 0004); AABB occupancy is deliberately conservative for diagonal
  leaders (the geometry-only `_geometry._leader_ink_crosses_box` test covers
  the rendered shaft width and tip-local arrowhead flare without claiming the
  diagonal AABB's empty triangle, #367); and a perpendicular-axis conflict — a
  witness line crossing a fixed-height label — is outside the 1-D tier solve's
  reach, handled by the post-drain `reconcile_witness_labels` shift (#690).
