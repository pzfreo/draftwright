# Changelog

## Unreleased

**Breaking — feature-recogniser API renamed.** Establishing the uniform recogniser
contract (ADR 0013 / #568). These are *deliberate* breaks with **no compatibility
aliases**, so this must ship in a **minor bump (0.3.0), not a patch**. Drawing output is
unchanged (byte-identical); only import paths and callable signatures change.

### Added

- **Countersink callouts (#558).** A countersunk hole is now recognised and called out
  `Ø6 THRU ⌵ Ø14 × 90°` (major-Ø + included angle), the way counterbores already get
  `⊔ Ø.. ↧..`. New `recognise_countersinks(part) -> list[CounterSink]` recogniser
  (geometry-mirrored with `build123d-mcp` for the shared `b123d-recognisers` package);
  the countersink rides on `HoleRecord`/`HoleFeature` so grouping and the callout-width
  layout estimate account for it.

### Changed

- **Feature recognisers renamed `find_`/`analyse_` → `recognise_`** and their
  tuning/dependencies made keyword-only: `find_holes`→`recognise_holes`,
  `find_bosses`→`recognise_bosses`, `find_hole_patterns`→`recognise_hole_patterns`,
  `find_slots`→`recognise_slots`, `find_plates`→`recognise_plates`,
  `find_chamfers`→`recognise_chamfers`, `find_turned_steps`→`recognise_turned_steps`,
  `find_step_shoulders`→`recognise_step_shoulders`, `analyse_face_levels`→
  `recognise_face_levels` (the last was also re-exported at top level as
  `draftwright.analyse_face_levels` → now `draftwright.recognise_face_levels`).
  Cylinder-analysis substrate (`analyse_cylinders`, `full_cylinders`,
  `feature_diameters`) keeps its names.
- **`recognise_step_shoulders` now returns `list[StepShoulder]`** (a frozen dataclass)
  instead of raw `(axis, position)` tuples; `levels` is keyword-only.
- **`recognise_turned_steps` now returns `list[TurnedStep]`** (empty for a non-turned
  part) instead of `TurnedProfile | None`. `TurnedStep` gained an `axis` field (each step
  is now a self-contained record); `TurnedProfile` remains only as a pipeline aggregate
  (`TurnedProfile.from_steps(steps)`), no longer a recogniser return.
- **`recognise_face_levels` now returns `list[FaceLevel]`** (a frozen `FaceLevel(z)`
  record) instead of `list[float]`.
- **Recognition record classes renamed to avoid clashing with the IR `Feature` types:**
  `draftwright.recognition.HoleFeature` → `HoleRecord`, `BossFeature` → `BossRecord`
  (the IR `draftwright.model.ir.HoleFeature`/`BossFeature` are unchanged).

### Removed

- **`draftwright.features` compatibility module** — deleted. Import `recognise_slots`/
  `Slot` from `draftwright.recognition` instead.

## v0.2.13 — 2026-07-09

**Compose-then-pack is now the layout authority.** This patch finishes the
ViewBlock footprint migration: estimated and measured layout paths now use the
same composed block model, furniture declarations reserve their real footprint,
and measure/repack iterates until stable instead of trusting one measured pass.

### Changed

- **ViewBlock footprints now drive the orthographic stack.** The old
  estimator-only plan-view balloon lift is gone; plan balloon headroom is part
  of the plan block in both estimated and measured/repack layout. (#112)
- **Measure/repack iterates to a fixed point.** Repack now keeps measuring and
  repacking while view-owned footprints continue to grow, with bounded
  convergence and tests for multi-pass growth. (#302)
- **Estimated view-block composition is explicit.** Scale/page fitness, repack,
  and furniture placement share the same composed block semantics instead of
  rebuilding corridor estimates independently.

### Fixed

- **Declared callout furniture reserves layout footprint.** User-declared
  furniture is now measured and fed into the layout model, reducing post-hoc
  collisions from declarations that previously existed only at render time.
- **Annotation-box composition owns strip sizing.** Strip depths reduce from
  composed annotation boxes, making footprint sizing less dependent on divergent
  scalar estimates.

### Documentation

- **Layout architecture docs reflect the current state.** ADR 0004 and the
  layout primer now mark the core layout-authority tranche complete and separate
  remaining work into hardening, coverage, detail-view, and manual-intent phases.

## v0.2.12 — 2026-07-09

**Sheet scripts now round-trip authored layout intent.** This patch finishes the next
slice of the layout-authority work: below/right ladders, pinned edits, height ladders,
detected envelopes, and AP242 authored dimensions now survive the declarative Sheet path
without being invalidated by post-hoc placement or raw imported metadata.

### Added

- **Pinned edit intents route through the corridor solve.** `locate(..., pin=True)` and
  pinned dimensions now become priority-ranked candidates in the shared corridor instead
  of fixed post-hoc edits, so user intent participates in the same ordering/spacing model
  as automatic dimensions. (#511)
- **AP242 dimensional PMI lowers to authored drafting dimensions.** Imported AP242
  size/location dimensions now become `AuthoredDimension` IR and generated Sheet scripts
  emit `sheet.dimension(...)`; unsupported PMI records remain explicit raw fallbacks.
  (#503, #422, #62)

### Changed

- **`place_dim` is deprecated.** Manual dimensions should use pinned candidate/dimension
  intent instead of the old incremental edit path.
- **Prismatic height ladders and detected envelopes round-trip through Sheet scripts.**
  Generated scripts now preserve `StepLevelFeature` and detected `EnvelopeFeature`
  fallbacks, preventing CTC01-style ladder swaps and raw STEP-envelope remeasurement
  drift.

### Fixed

- **Below/right corridors now share one solve.** Side-hole locations, height ladders,
  PMI/GD&T, envelope dims, and related below/right strip occupants negotiate in the shared
  corridor rather than competing through separate late passes. (#477)
- **Sheet script parity is tighter.** Generated scripts preserve member positions, step
  levels, AP242 authored dimensions, raw PMI fallbacks, and detected envelopes so
  direct-vs-script CTC01 output has matching annotation names.

## v0.2.11 — 2026-07-08

**Automatic layout now has one authority.** Page/scale selection, section placement,
furniture footprints, and table/balloon escalation now negotiate through the same layout
fitness model instead of using separate fixed offsets or first-fit policies after the main
solve.

### Fixed

- **Solver placement paths are more consistent.** STEP PMI dimensions now queue into
  the shared ADR 0009 corridor solve instead of carving after the drain; front-view
  hole callouts use the strip solver instead of fixed row stepping; pitch-dimension
  fallback searches bounded, obstacle-aware positions; repair no longer hides
  `annotation_overlap` with a fixed-step nudge; and step-count sizing now handles
  non-convergence conservatively. (#524)
- **Page/scale fitness is shared across initial selection and repack.** Later layout
  passes now compare candidates with the same model as the page chooser, preventing a
  nominally "better" repack from invalidating the original layout decision. (#519)
- **Section A-A participates in layout selection.** Section placement is measured as
  part of page/scale fitness, so a section view is no longer a fixed-offset afterthought
  that can disappear on dense sheets. (#515)
- **Furniture placement reserves full rendered footprints.** Section arrows, detail
  views, balloons, hole/data tables, and other furniture now reserve/check their true
  rendered footprint instead of just their labels. (#518)
- **Hole/data tables and balloon rings escalate through the layout model.** Dense table
  and balloon outputs can move or negotiate for available room rather than dropping from
  greedy first-fit placement when a small adjustment would fit. (#516, #517)

## v0.2.10 — 2026-07-07

**Declarative-surface fidelity and layout-engine unification.** Editing and re-running a
declarative `Sheet` script now reproduces more of the drawing (title block, layout, PMI on the
declared path), a new lint catches stale declarations, and an intentional scale is respected.
Internally the annotation placer moves further onto the single collect-then-solve (ADR 0009),
and `kiwisolver` is dropped as a dependency.

### Added

- **Declaration-vs-geometry reconciliation lint** — `lint()` now flags a declared cylindrical
  feature (hole/boss/step) with **no matching geometry** in the part (`declared_feature_absent`):
  a callout drawn over empty space because the part was edited but the declaration went stale.
  Closes the last gap in the "did my edit break the drawing?" loop (over-declaration; coverage
  lint already caught under-declaration). Matches on axis + ⌀ + in-plane position + bore/boss
  polarity. Gated on a caller-supplied `model=` (detection can't over-declare). (#487)
- **Title-block + layout aspects on the `Sheet` DSL** — `Sheet(drawn_by=…, tolerance=…, scale=…,
  page=…)`; the generated `--script` reproduces them, and the CLI forwards
  `--drawn-by`/`--tolerance`/`--scale`/`--page` on the sheet path (no more inert-flag warning). (#474)
- **PMI reproduced on the declared-model path** — `build_drawing(path, model=…, pmi="annotate")`
  synthesises the STEP AP242 PMI into the declared model, so a declared build draws the same PMI
  dimensions as the detection path. (#472)

### Changed

- **An intentional explicit `scale=` below the legibility floor is honoured with a warning**
  rather than rejected. A part deliberately drawn at 1:1 (or `Sheet(scale="1:10")`) whose smallest
  feature falls under the 10 mm legibility floor now renders (annotations may crowd) instead of
  raising `ValueError`; only a genuinely degenerate scale (< 0.1 mm projected, where OCCT arcs
  collapse) is rejected. The floor now binds only the *automatic* scale. (#489)
- **`kiwisolver` is no longer a dependency.** The 1-D strip solve delegates to the built-in
  deterministic minimum-total-leader-length PAVA algorithm, which supersedes the Cassowary
  solver — same placement contract, one fewer third-party dependency. (#507)

### Fixed

- **Dimension placement is more robust under contention.** Candidate **priority** is plumbed
  through the shared corridor solve, so an authored GD&T frame is kept over a lower-value
  automatic dimension when a strip is over capacity (#357). Rotational concentric-bore leaders
  are now bounded to the front-view height with ranked drops, and pitch dimensions are placed
  onto the obstacle-aware per-side zone-strip solve — retiring the last fixed-offset,
  count-based stacking placers (the shape behind earlier dense-sheet overruns). (#374)

## v0.2.9 — 2026-07-06

**Declarative GD&T, datums, and surface finish (ADR 0011 Phase 2b/2c).** The `Sheet` DSL can
now author the drawing information geometry can't carry — geometric tolerances, datum feature
symbols, and surface-finish marks — by pointing at a build123d feature or face. They render
through the auto-layout engine as first-class ADR 0009 corridor candidates, placed and spaced
crossing-free alongside the automatic dimensions (not a post-hoc overlay).

```python
sheet.datum("A", base_face)
sheet.control(bore).position(0.1, to="A", diameter=True).perpendicularity(0.05, to="A")
sheet.diameter(journal).finish("0.8")
```

### Added

- **Feature control frames (ISO 1101)** — `sheet.control(ref)` returns a chainable builder with
  one method per **all 14** characteristics (`position`, `flatness`, `perpendicularity`,
  `cylindricity`, `circular_runout`, `total_runout`, …); `to=` names the referenced datum(s),
  `diameter=` prefixes the ⌀ tolerance zone, `modifier=` adds a material-condition symbol (Ⓜ/Ⓛ/Ⓟ).
- **Datum feature symbols (ISO 5459)** — `sheet.datum("A", face_or_feature)`.
- **Surface finish (ISO 1302, Ra)** — `.finish("1.6")` on a hole / diameter / step handle, or
  `sheet.finish(ra, face)`.
- The target view + strip side are **derived from the referenced geometry** (a feature's axis →
  face-on view, a planar face's normal → edge-on view); `view=`/`side=` override.
- Render core: `ControlFrame` / `DatumRef` / `Finish` IR features placed by `render_gdt` as
  first-class corridor candidates, with the real-footprint plumbing needed to space wide frames.

### Fixed

- A control frame referencing an **undeclared datum letter** now warns at build.
- GD&T placement is **title-block aware** and **falls through to the opposite strip side** before
  dropping a frame, so stacked control frames place robustly rather than vanishing or overlapping
  the title block.
- An invalid glyph spec / degenerate leader in a caller-supplied model **drops the one item with a
  warning** instead of crashing the whole build (the IR is public input).
- Turned-shaft rotational furniture now reproduces correctly through the declared-model path
  (`model=` / `Sheet`) (#476).

## v0.2.8 — 2026-07-06

**`--script` now defaults to the declarative `Sheet` DSL.** The editable script the CLI
writes is the beautiful-Python surface — one commentable line per feature — for both STEP and
build123d-object input.

### Changed

- **`draftwright … --script` now emits the declarative `Sheet` script by default** (was the
  imperative edit-verb reconstruction). The imperative reconstruction is still available via
  `--script --style imperative`. For prismatic parts the generated script reproduces the same
  drawing as a direct build; a turned shaft's centre lines and base-diameter style are not yet
  identical (the remaining validity gap, tracked separately). `--style imperative` now errors
  clearly on a `module:attr` object spec (it reads a STEP file; use the default `sheet`).
  The Sheet DSL doesn't yet model the title-block / layout aspects the imperative script
  embeds, so `--script --style sheet` **warns** when `--drawn-by`, `--tolerance`, `--scale`
  or `--page` is set rather than silently ignoring it (use `--style imperative` to embed them).

## v0.2.7 — 2026-07-05

**The three authoring modes (ADR 0011 Amendment 1).** Naming how you drive draftwright —
*just do it* / *auto then tweak* / *generate an editable script* — and filling in the third:
a declarative `Sheet`-DSL generator, and number-free authoring against the build123d objects
you built.

### Added

- **`draftwright part.step --script --style sheet`** — generate an **editable declarative
  `Sheet` script** from a detected part: one commentable line per feature (hole / diameter /
  step / slot / pattern / envelope), views noted, the auto-section flagged. Comment a line out
  to drop that feature; the script re-runs to the drawing. The existing `--script` (imperative
  edit-verb reconstruction) stays the default (#461).
- **Object-reading `.cbore(tool)` / `.spotface(tool)`** on a declared hole — read the
  counterbore/spotface's ⌀ **and** depth off the tool object + part (depth measured from the
  local open face, so a rib/wall elsewhere doesn't skew it). A `Sheet`-declared drawing can now
  carry **zero magic numbers** and track the geometry parametrically (#462).
- **`Sheet.of(feature)`** — a fluent handle onto an **existing / detection-seeded** feature (by
  the build123d object, an index, or the `Feature` itself), so you can `.fit(...)` /
  `.tolerance(...)` a feature from `Sheet.from_part()` without re-declaring it (#463).

### Docs

- **ADR 0011 Amendment 1** — the three authoring modes and the mode-3 generation surface;
  records the decision to emit **honest detected numbers** for a detected part rather than
  fabricate a build123d reconstruction that misrepresents the geometry (#464).

## v0.2.6 — 2026-07-05

**ADR 0011 — the IR as a public input: declare features, don't only detect them.**
Detection stays the default, but the caller can now *supply* the feature model, so a
part you built parametrically reads as its own drawing and misdetection becomes
recoverable by construction. Plus the first Phase-2 aspect layer (tolerances + fits).

### Added

- **`build_drawing(part, model=…)`** accepts a caller-supplied `PartModel` (or a
  `Sequence[Feature]`); when given, **detection is skipped** and the auto-pass dimensions
  the declared features. Detection and declaration are two producers of the same IR (#447,
  ADR 0011 Phase 0).
- **Object → feature constructors** `draftwright.model.hole` / `boss` / `step` / `slot` /
  `pattern` / `envelope` — read a feature's geometry off the build123d object you built (⌀
  from the cylindrical face, axis/location from the bbox), or supply explicit values (#447).
- **The fluent `Sheet` façade** (`draftwright.Sheet`) — reference the objects you built,
  declare their drawing aspects, `.build()` / `.export()`. `Sheet.from_part()` seeds the
  hybrid override mode from detection (#447, Phase 1).
- **Toleranced dimensions** — a `±` / limit tolerance on a diameter, step, or hole bore,
  rendered on both the linear and ⌀-callout paths (`Sheet.tolerance(...)`, or a
  `decorations=` side-layer) (#28, Phase 2 P2a).
- **Fit-class deviation** — `Sheet.fit("H7")` resolves an ISO 286 fit class to its limit
  deviation for the feature's nominal ⌀, rendered as the class code (`ø20 H7`, default) or
  the signed deviations (`show="deviation"` → `ø20 +0.021/0`). Common classes; fails loud
  outside its table (#29, Phase 2 P2a.2).

### Changed

- **Object constructors honour explicit overrides** — a passed object supplies *defaults*;
  each explicit keyword overrides that field independently, and invalid public input fails
  at declaration with a clear `ValueError` (#451, #452).
- **A declared hole/pattern renders at its declared position** even where detection missed
  it — the callout membership is sourced from the declared model, not only detection (#448).
- **`Sheet.model()` / `Sheet.from_part()`** no longer build a full drawing just to return
  the IR — feature inspection/seeding is now a cheap, no-render path (#453).

### Fixed

- A narrow diameter band hidden under a larger OD silhouette is no longer silently
  undimensioned (the two feature inventories agreed) (#298).

## v0.2.5 — 2026-07-04

**The editable write API and record-then-finalize.** A detected drawing became an editable
object, and a generated `--script` became a runnable reconstruction that reaches auto-pass
quality.

### Added

- **`dwg.model()`** exposes the detected `PartModel` as a read surface, plus feature-
  referenced add verbs `dimension()` / `callout()` / `locate()` / `furniture()` /
  `section()` and `drop(feature)`, with a machine-checked completeness audit (#400).
- **Record-then-finalize** (#426): the verbs record intents in deferred mode and
  `dwg.finalize()` (auto-run by export) drains them through the auto-pass's own solvers, so
  a reconstruction reaches auto-pass quality; `--script` now emits a runnable detect-only
  reconstruction.

### Changed

- **Dimension-line spacing now follows ISO 129-1 / ASME Y14.5 convention** (#347): a wider
  first-line gap (8 → 10 mm) and tighter, uniform parallel stacking (between-line clear gap
  4 → 2.5 mm), with the inter-view corridor widened in step. Re-drifts every drawing.

## v0.2.4 — 2026-07-03

A follow-up patch on the ADR 0009 placement rebuild in 0.2.3: it finishes unifying
the shared "above-view" dimension corridor, adds a layout-overflow safety net, and
makes two more drop paths non-silent.

### Changed

- **Plan-view X location dimensions, side-view Y location dimensions, and a
  coincident slot-position dimension now share one collect-then-solve pass** (ADR
  0009 Amendment 6, #345/#346). Previously each pass carved the strip independently,
  so a hole location and a slot position measuring the same datum span could both be
  drawn, and the location ladder could come out non-monotonic. One solve now dedups
  the coincident span (keeping the higher-priority location dimension) and orders the
  whole ladder as segregated, monotonic runs — feature-size dimensions nearest the
  view, datum locations nesting outward by distance.

### Fixed

- **`choose_scale` never returns an overflowing layout** (#350). Scale selection
  could pick a scale whose composed block layout exceeded the drawable area; it now
  rejects any overflowing candidate.
- **A hole location and a coincident slot position are no longer drawn twice** (#345),
  including at fractional datum distances where a display-value snap gap previously let
  the duplicate escape deduplication.
- **The plan-view location ladder is monotonic** (#346) — running dimensions off a
  shared datum stack outward in ascending order instead of interleaving.
- **A dropped balloon is non-silent** (#387). A balloon that cannot be placed now
  reports the drop and clears its `callout_dropped` state precisely, instead of
  vanishing with no on-sheet signal.

## v0.2.3 — 2026-07-03

A large patch release: the **annotation-placement engine was rebuilt** as a
collect-then-solve *boundary-labeling* stage (ADR 0009). Placement is now
deterministic and minimises total leader length, and the recurring class of
overlaps where a label was drawn on top of an "invisible" occupant — a leader
shaft, a witness/extension line, the section hatch — is removed by construction.
Drawing output changes for many turned, cross-drilled, and multi-feature parts.

### Changed

- **Every annotation in a view's margin is now placed by one collect-then-solve
  pass** (ADR 0009, #317–#323). Dimensions, hole callouts, turned-diameter
  leaders, and the section hatch share a single occupancy model instead of several
  independent passes each blind to the others. When a strip is over capacity it now
  drops the *lowest-priority* annotation (smallest bore first) rather than whichever
  pass happened to run last. The legacy strip cursor is retired.
- **Leader placement minimises total leader length, deterministically** (P4,
  #318). A per-strip solve places each label at the shortest-leader position that
  keeps the labels in order and clear of keep-out rows (a view centre-line, a
  dimension's extension line); central/coaxial callouts are anchored to the
  view-centre row. Output is reproducible across platforms and Python versions.
- **`scipy` is no longer a dependency** — the leader solve is a small deterministic
  algorithm (weighted-median isotonic regression), not a linear program.
- **Output changes** for turned, cross-drilled, and multi-feature parts whose
  margin annotations are now positioned by the unified solver.

### Fixed

- **A PMI bore-diameter dimension spans the bore radius, not the full diameter**
  (#360). A `pmi="annotate"` diameter callout drew its witness lines at ±diameter
  from the centre — twice too wide, missing the bore edges.
- **A bore coaxial with a rotational part's turning axis is no longer
  over-dimensioned** (#309). It carried a redundant offset *and* height location
  dimension even though its centre mark already locates it.
- **A dropped turned step-length chain is no longer silent** (#362). When a turned
  head's shoulders are too crowded to dimension, the drop is now reported
  (`step_dim_dropped`) instead of vanishing with no lint or on-sheet signal.
- **A diameter callout can no longer overprint a bore callout's leader shaft**
  (#358). The turned-diameter column now avoids the *full* footprint of existing
  annotations, not just their text boxes.
- **The balloon ring hugs its dimensions on a cramped sheet** (#349) — its band
  depth is clamped to the drawable area.
- **Dimension detection is robust to `SafeDimension`** (#335/#349) — the corridor
  and balloon-ring filters test the dimension *type*, not a class-name string, so a
  future dimension subclass can't slip through.

## v0.2.2 — 2026-06-30

A patch release of turned-part dimension-placement fixes and a CLI start-up
speed-up. Drawing output changes for the affected turned/cross-drilled parts.

### Fixed

- **A coaxial bore callout on a *stepped* turned shaft is now lifted off the round
  view's centre axis** (#305). The earlier fix only triggered for a uniform
  (`is_rotational`) cylinder; a stepped shaft (e.g. the gramel GRM-03 drive screw)
  has a turned step profile but isn't classified rotational, so its `⌀… ↓…` bore
  callout was still leadered straight along the centreline, with the centre mark
  running through the text. The lift now also fires for a turned-profile part.
- **A side-drilled hole's location dimension now stacks *inside* the overall
  envelope dimension** (ISO order — overall dim outermost, feature/location dims
  nearer the view). It was placed *outside* the envelope, which forced the shorter
  location dim's arrowheads to flip outward and clash (seen on GRM-01 and GRM-02).
  The mandatory overall dimension is still guaranteed placement.

### Changed

- **CLI shell completion and `--help` are fast again** (#313). The Typer CLI and
  the heavy CAD engine are now imported lazily, so tab-completion and `--help` no
  longer pay a ~6 s engine-import cost; a real drawing run is unaffected.

## v0.2.1 — 2026-06-30

A patch release focused on **turned-part dimensioning legibility**: crowded
step-length chains and fine turned heads are now drawn legibly instead of crammed.
Drawing output changes for affected turned parts.

### Added

- **Automatic enlarged detail view for a crowded turned head** (#304). A turned
  part with a fine cluster of steps near one end and a long shaft (e.g. a thumbwheel
  drive screw) cannot have its head dimensioned legibly in line at any sensible
  scale. The head is now located as one block on the main view and broken out into
  an enlarged **DETAIL A — SCALE n:1** — the textbook treatment — firing
  automatically when a head's shoulders fall below the page legibility floor.

### Changed

- **Crowded turned step-length chains stagger across two tiers** (#293) instead of
  cramming or being skipped. When the labels would collide on one line, the ISO
  129-1 staggered convention alternates them between a near and a far tier so every
  step length stays legible at the drawing's own scale — no rescale needed. A roomy
  chain stays on a single tier.
- **Detail views are now one unified pipeline** (#307). The prismatic step-height
  detail and the new turned-head detail flow through a single
  detect → request → render path; several crowded regions become DETAIL A/B/…
- **Output changes** for turned parts whose step chains were previously crammed, or
  whose fine heads are now broken out into a detail view.

### Fixed

- **A coaxial bore callout no longer overlaps the round view's centreline** (#305):
  its leader is angled off the centre axis so the callout text sits in clear space.

### Internal

- A new **layout-cleanliness invariant test** asserts that finished drawings have no
  view/annotation collisions across representative part archetypes, and the
  measure-and-repack pass gained a trigger for an annotation growing into a
  neighbouring view's line-work (so the views spread to make room).

## v0.2.0 — 2026-06-30

A major release. draftwright took ownership of feature recognition and linting
(ADR 0007) and was re-architected onto a feature-IR + dimensioning-planner
"compiler" (ADR 0008), gaining a Typer CLI and a portable, pure-Python PDF path
along the way. **Generated drawings change** versus v0.1.13: the new pipeline
dimensions parts more completely and consistently, so placement and the set of
dimensions can differ — output is not byte-compatible with prior releases.

### Changed

- **Re-architected onto a feature-IR + dimensioning planner** (ADR 0008). The
  engine is now a compiler: detectors build one feature inventory → a typed IR /
  `PartModel` → a dimensioning planner emits render-intents → shared
  layout/projection/export. Orientation and feature-kind are *data in the IR*,
  not code branches, and every feature class (holes, patterns, counterbores,
  slots, turned diameters/steps, centre marks, location dims, envelope/OD,
  section A–A, PMI/GD&T) was migrated onto this one path; the old parallel
  recognisers and placement passes were deleted as each was replaced. Net effect
  for users: more complete, more consistent drawings — but output differs from
  v0.1.13.
- **draftwright owns feature recognition and linting** (ADR 0007). The hole/
  boss/cylinder/pattern recognisers, the slot/turned-step recognisers, and the
  feature-coverage lint engine are vendored into `recognition/` and `linting.py`;
  `build123d-drafting-helpers` is now purely the rendering library.
- **The CLI writes a PDF by default** and takes a `--format` selector (#288).
  Previously `draftwright part.step` emitted SVG + DXF; it now emits a single PDF.
  Choose outputs with `--format` (a comma-list, with an `all` alias) —
  `--format pdf,dxf`, `--format svg`, `--format all`. The library API is
  unchanged: `make_drawing(...)` / `Drawing.export()` still write SVG + DXF.
- **PDF export is now pure-Python and a core capability** (#288). The renderer
  moved from `cairosvg` (which `dlopen`s the native `libcairo` system library —
  absent on stock macOS/Windows, so PDF-by-default would have crashed there) to
  `svglib` + `reportlab`, both pip-installable wheels with no system dependency.
  PDF therefore works out of the box on every platform; output is visually
  identical to the cairo renderer.

### Added

- **A Typer command-line interface** (#289/#291): shell completion
  (`--install-completion` / `--show-completion`), rich `--help`, and `--version`
  (reports the installed distribution version). All existing flags are preserved.
- **`--format` output selector** (#288) — `pdf` (default), `svg`, `dxf`, or `all`,
  as a comma-list.
- **Turned-part dimensioning**: axial step-length recognition and chains
  (#188/#189/#231), step-diameter callouts, collapse of a uniform step
  staircase to an "N× length" note (#290), and OD of a horizontal (X/Y) round
  body dimensioned on the profile view (#292).
- **Slot recognition and dimensioning** converged onto the IR as `SlotFeature`
  (#242), and **section A–A** is now triggered by the planner (#271).
- **A Contributor License Agreement** (#183).

### Removed

- **The `--pdf` flag** (use `--format pdf`, the new default), the **`[pdf]`
  install extra**, and the **`cairosvg` dependency** (#288).
- The byte-exact golden-output test harness (#190) — regression coverage rests on
  the geometry-level and standards suites (ADR 0005 §3 / ADR 0007).

### Fixed

- Locate **every** side-drilled (off-axis) hole, not just the first (#225/#286).
- Don't mis-detect a prismatic part with incidental cylinders as a turned part
  (#293/#294); drop phantom zero-diameter turned steps (#279/#284); skip an
  illegibly-dense step-length chain rather than cram it (#293/#296).
- Degraded hole-pattern callout consistency (#262/#274).
- Windows `python -m draftwright.make_drawing` CLI smoke / entrypoint (#181/#182).

## v0.1.13 — 2026-06-27

### Changed

- **Requires `build123d-drafting-helpers>=0.13.0`; text pinned to bundled fonts**
  (#149, ADR 0006). draftwright now vendors IBM Plex (OFL-1.1) and renders and
  measures all text via `font_path` — IBM Plex Mono for dimensions/callouts/notes,
  IBM Plex Sans Condensed for the title block — instead of resolving the system
  font name `"Arial"`. Resolving a name substitutes a different font on Linux,
  which shifted the whole sheet ~1 mm; pinning a bundled font file makes generated
  layout **deterministic across Linux/macOS/Windows** and gives a consistent
  typeface. **Drawing output changes**: positions shift slightly from prior
  releases and labels render in IBM Plex (helpers #172).

### Internal

- **Compiler-pipeline module split** (#138, ADR 0005). The two large modules
  `make_drawing.py` (3,907 lines) and `annotate.py` (2,587) were decomposed into a
  DAG of focused stage modules — `projection`, `sheet`, `analysis`, `drawing`,
  `builder`, the `annotations/` subpackage (sections/turned/pmi/holes/orchestrator),
  alongside the existing `registry`/`linting`/`repair`/`export`/`fonts`. Annotation
  identity, the lint coverage signal, and the deterministic repair loop each gained a
  single owner; `make_drawing.py` / `annotate.py` are now thin compat facades, so all
  existing imports and the `draftwright` CLI entry point keep working. A golden-output
  regression gate verified every step is behaviour-preserving (output byte-identical),
  and mypy was tightened on the settled contracts. No public API or drawing-output
  change. (Phases #160–#166.)

## v0.1.12 — 2026-06-21

### Changed

- **Requires `build123d-drafting-helpers>=0.12.0`** (#92, #122). draftwright now
  consumes the new sub-clustered hole-pattern recognition, the
  `feature_diameters()` coverage inventory, the persistent `view_edge_cache`,
  and the `ViewCoordinates.from_viewport()` ISO projection basis.
- **Grouped hole-pattern callouts** (#92, #111, #114). A recognised perimeter,
  grid, or bolt circle collapses to a single `n× ⌀ …` callout plus its pattern
  dimensions instead of a balloon on every hole. A spec group now sub-clusters
  into multiple patterns (a perimeter → its edge `LinearArray` rows, a filled
  lattice → one `RectGrid` with a `(rows×cols)` callout and both pitch
  dimensions); only genuinely unpatterned holes fall back to the per-hole table.
  On NIST CTC-02 the table shrinks from 61 rows to the unpatterned remainder.
- **Layout overhaul — compose-then-pack** (#121, #112, ADR 0004). Each view owns
  the annotations created against it, and the resulting view blocks are packed
  disjoint with automatic page/scale escalation. This eliminates cross-view
  overlap — most visibly, plan-view balloons landing on front-view dimensions.
- **Drawing attribution** (#120). The title block records the author, the SVG
  and PDF carry a clickable draftwright hyperlink, and a "generated by
  draftwright" note is written to the SVG/DXF/PDF file metadata.
- **Gap between wrapped hole-table column blocks** (#123) so a chart that wraps
  into several blocks reads as distinct columns.

### Fixed

- **Plan-view top balloon ring no longer floats over a phantom corridor** (#125).
  The hole-table escalation deletes the X-location dimensions but left their
  stale depth in the strip cursor, so the top balloons were parked far above the
  view. The ring is now sized to the real dimension stack, so the top-side
  leaders are short like the other three sides.
- **No more phantom `feature_not_dimensioned` warnings** on slot-ends and shallow
  recesses, via the helpers 0.12.0 `feature_diameters()` coverage inventory
  (#92).

### Internal

- `AnnoBox` box-model footprint foundation and the four-side balloon ring placed
  in a reserved view halo (#111, #112); the title block is pinned as a
  first-class layout block (#112).

## v0.1.11 — 2026-06-19

### Changed

- **Feature-coverage lint is assembly-aware.** A general-arrangement drawing of
  a multi-solid part deliberately omits each part's bores (they belong on detail
  sheets), so `feature_not_dimensioned` / `feature_count_mismatch` are now
  emitted at `info` rather than `warning` when the part is multi-solid — out of
  the warning count and quality score, but still queryable. Auto-detected;
  override with `build_drawing(..., assembly=True/False)` or
  `lint_feature_coverage(..., assembly=...)` (#69).

### Fixed

- **`place_dim` now labels the real-world length, not the page distance**, at
  non-1:1 scale. Previously a dimension placed at a scale other than 1:1 showed
  the on-page millimetre span instead of the true model dimension (#104).

### Internal

- **`make_drawing.py` decomposed (#98).** The per-view projection math and the
  analysis namespace were deduplicated and typed (the namespace is now a frozen
  `Analysis` dataclass), and the annotation passes were extracted into a new
  `draftwright.annotate` module on top of a shared `draftwright._core`. The
  module graph is a DAG (`layout → _core → {make_drawing, annotate}`) and
  `make_drawing.py` shrank from ~5,270 to ~2,930 lines. No public API or
  behaviour change.

## v0.1.10 — 2026-06-18

### Added

- **Constraint-based layout engine (ADR 0003).** A new `draftwright.layout`
  module with a `Placeable` protocol and a `LayoutSolver`: a 1D Cassowary strip
  solver (`solve_strip`, with per-pair gaps) and a 2D free-rectangle placer
  (`place_box` / `fit_box`) that positions a box in a free part of the page
  clear of the views, title block, and existing annotations. Hole-callout and
  turned-diameter placement now run on the solver. The engine grows per real
  consumer; a monolithic global 2D solve is deferred (see the ADR).
- **Hole table + balloons (#93).** `dwg.add_table(rows)` places a generic data
  table (gear data, BOM, revision block, …) in a free corner via `place_box`;
  `dwg.add_hole_table(view)` builds a hole chart from the detected holes with a
  circled balloon tag at each hole. A **too-dense plan view now auto-escalates**:
  a part the layout cannot legibly dimension hole-by-hole is replaced by a
  complete per-instance hole chart (`TAG | ⌀ | X | Y`, datum-relative) plus
  balloons, instead of silently dropping callouts and location dims. The chart
  wraps into multiple column-blocks to fit the page.
- **External turned diameters (#77).** A turned part lying along the X axis now
  gets ø leader-callouts for its external stepped diameters, with thread/worm
  patches collapsed into a single boss.
- **Pin / manual override (#89).** `dwg.pin(name)` / `dwg.unpin(name)` fix an
  annotation's position so `repair()` — and the layout engine — never move it; a
  deliberate (human or AI) placement wins over automatic layout.

### Changed

- Hole-callout and turned-diameter placement is deconflicted through the shared
  `LayoutSolver` instead of ad-hoc per-pass logic (no output change).

### Fixed

- **Exact circles recovered for revolution silhouettes.** `project_to_viewport`'s
  HLR returns the on-axis silhouette of a turned feature (or a concentric
  gear-tooth-tip arc) as an approximating spline, not a true circle — splines in
  the DXF where CAM expects `CIRCLE`/`ARC`, and fitted rather than exact radii.
  `add_view` now refits any silhouette whose samples are equidistant from a
  recognised revolution axis back to an exact circle/arc (#67).
- **Blind-hole depth no longer measured across solid boundaries.** On a
  multi-solid assembly, coaxial bores in different bodies were merged into one
  hole, reporting a depth spanning the inter-body gap (the ⌀9.8 ↓111.4 symptom).
  Fixed upstream in `build123d-drafting-helpers` 0.10.1; the dependency pin is
  bumped to `>=0.10.1` to pick it up (#68).

### Docs

- The skill and generated-script header now lead with the domain API
  (`features` / `place_dim` / `repair` / `lint_summary`) and the
  build → critique → fix loop. ADR 0003 records the layout architecture; ADRs
  0001/0002 remain the editing-model and lint→repair foundations.

## v0.1.9 — 2026-06-16

### Added

- **Domain-semantic editing API.** `dwg.features(view)` returns detected holes
  and features grouped by machining spec in page coordinates, and
  `dwg.place_dim(p1, p2, side, view, draft, name=…)` places a dimension from
  domain inputs — the vocabulary a script (or an AI assistant) needs to edit a
  drawing without hand-computing page geometry (#25, #26).
- **`dwg.annotations()` and `dwg.get_annotation(name)`.** Introspect what is
  already on the drawing — a `{name: type}` map and a name lookup — so a script
  can make incremental edits without risking a silent name-collision replace
  (#27).
- **`dwg.view_bounds(view)`.** Returns `(x_min, y_min, x_max, y_max)`, the page
  bounding box of a view's projected geometry (or `None` for an unknown view),
  so free-form notes and leader elbows can be placed just outside a view without
  guessing offsets from `dwg.at()` (#28).
- **Lint findings carry a suggested fix.** Each repairable lint issue now
  includes a ready-to-run domain-API call snippet, so acting on a finding is one
  copy-paste away (#29).
- **Lint→repair loop.** `Drawing.repair()` — run by default in `build_drawing` —
  mechanically resolves the lint codes that have a deterministic placement fix:
  overlapping labels are pushed apart and wrong-side dimensions are flipped. A
  pass that would net-increase the issue count is rolled back, so repair never
  makes a drawing worse (#30).
- **TYP / representative dimensioning for uniform step patterns.** A run of
  equal-rise, equal-going steps is dimensioned once and labelled representative
  (TYP) instead of repeating identical dimensions down the ladder (#45).
- **Enlarged detail view for crowded step clusters (MVP).** When shoulders are
  too closely spaced to dimension legibly at sheet scale, an opt-in
  (`detail_view=True`) detail view re-draws them at a larger scale (#42).

### Changed

- **BREAKING: the annotation list `dwg.annotations` is renamed to `dwg.items`.**
  `dwg.annotations` is now a method (see Added); the ordered, mutable list of
  annotation objects it used to be is now `dwg.items`. Pre-1.0 with no published
  consumers, so the clearer name was taken now rather than spelling the new query
  method awkwardly (#27).

### Documentation

- ADRs 0001 (editing model) and 0002 (iteration loop) record the design
  direction behind the domain API and the lint→repair loop (#51).

## v0.1.8 — 2026-06-16

### Changed

- **Automatic scale selection now minimises the sheet size.** The preference
  ladder is page-major: every standard scale on the smallest sheet is tried
  before the next sheet up, so a part lands on the smallest sheet it fits at the
  largest scale that sheet allows. A 20 × 15 × 10 mm part is now drawn 2:1 on A4
  instead of 5:1 on A3 — a smaller sheet is preferred over a larger enlargement
  scale. Reductions keep their legibility-first balance, so a too-big part is
  not over-reduced onto a small sheet.
- **A specified page now enlarges to the best fitting scale.** When the caller
  fixes the page (`--page A3`) or scale, scale selection packs the isometric
  view into the largest empty rectangle the placement engine actually uses (it
  may sit in vertical headroom above the views), instead of charging it a column
  in the view row. A long, short part — e.g. a 100 × 10 × 11 mm staircase — now
  fills a requested A3 at 2:1 where it was previously under-scaled to 1:1.
  Automatic selection (no page/scale given) keeps the conservative row model,
  which reserves enough room to place every annotation rather than dropping some
  onto a tighter sheet (staircase review).
- **Isometric view growth is capped.** The iso is fitted to fill its zone but no
  longer grows past 1.3× sheet scale; on an oversized sheet it could previously
  balloon to ~8× and dwarf the dimensioned orthographic views. Shrinking to fit
  a small zone is unchanged.
- **Step heights are dimensioned only where legibly separable.** After the
  adaptive cap (#36), a part with many closely-spaced shoulders (e.g. NIST
  CTC-02 at 1:5) tried to dimension faces only ~1 mm apart on the page. A step
  is now dimensioned only if it is both tall enough from the base *and* at least
  one legible step-height above the previously dimensioned one; the rest surface
  as `step_dim_dropped` (use a detail view). "Fits" is not the same as
  "legible" (#41).
- **Hole-location dimensions are gated for legibility.** A hole-dense part (e.g.
  NIST CTC-02, ~38 distinct hole locations) previously stacked every location
  reference into a tall, busy tower above the views — "fits" is not "legible".
  Each axis's references are now gated by inter-dimension page spacing
  (`_legible_locations`, analogous to the step-height gate #41): only locations
  at least one value-label footprint apart on the page are dimensioned; the rest
  surface as `location_ref_dropped` (full fidelity belongs in a detail view,
  #42). Sparse parts are unchanged (#43).
- **Tighter location-dimension tier pitch.** The vertical pitch between stacked
  X/Y location dimensions is now derived from the label footprint
  (`font_size + 2·pad_around_text`, ≈7 mm) instead of a looser `font_size·3`,
  so location stacks pack closer (#41).

### Fixed

- **Phantom step corridor no longer blocks a larger scale.** Page/scale
  selection reserved a step-ladder corridor sized for *every* candidate
  horizontal face, including ones the legibility gate would never dimension. A
  part with many sub-legible faces (e.g. a staircase with 15 tiny treads) was
  forced onto an oversized sheet at 1:1. Scale selection now iterates so the
  reserved corridor matches the step count actually placed, freeing the part to
  pick a tighter sheet (staircase.step review).
- **Engraved-text faces are no longer dimensioned as steps.** `analyse_face_levels`
  gained a `min_area_frac` filter; a horizontal face counts as a step only if
  its area is at least 1% of the part's plan footprint. This drops sub-feature
  faces (fragments of engraved numbers/text) that were surfacing as phantom
  shoulders — e.g. a 0.57 mm² digit face dimensioned as z=6.4 on staircase.step.
- **Overall-height dimension nests outside the step dims.** The overall height
  is now placed last on the front view's right ladder so it sits outermost, with
  the step-height dims inside it; extension lines nest instead of leapfrogging
  (staircase.step review).

## v0.1.7 — 2026-06-15

### Added

- `Drawing.lint_summary()` — a JSON-friendly aggregate of `lint()` for
  non-interactive callers (scripts, or an LLM via the API): severity counts,
  per-code counts, a `geometry_issues` tally (standards/geometry checks vs pure
  layout), a `passed` flag, a coarse 0–1 `score`, and the full issue list. Gives
  a single signal to gate and optimise on without rendering the SVG (#32).

### Changed

- **Adaptive annotation placement.** The three hard-coded cardinality caps —
  four hole callouts per view, four hole location references per part, and three
  step-height dimensions — are removed. The engine now places as many as the
  available strip/corridor space allows (callouts largest-first, locations
  nearest-datum-first, every legible step), so a part with room is dimensioned
  completely instead of dropped to an arbitrary count. An annotation that
  genuinely doesn't fit is never force-placed; it surfaces via lint
  (`callout_dropped` / `location_ref_dropped`, warning severity). On the NIST
  CTC parts this raises coverage substantially (e.g. CTC-02: 4 → 36 location
  dimensions, 4 → 9 callouts) with no error-severity lint (#36).
- **No silent annotation drops.** Every place the layout has to drop an
  annotation now records a machine-readable lint issue, surfaced by `lint()`,
  so a short drawing always carries a reason. A dropped callout names its
  diameter and is excluded from `feature_not_dimensioned` (no double-report).
  `placement_unsatisfiable` (error severity) is reserved for the degenerate
  case where space was reserved but an annotation still could not be placed
  (#32).
- **Layout constants derived from first principles.** Bare, fixture-tuned
  constants (strip slot widths, callout label widths, isometric fit factor) are
  now computed from text metrics and page size rather than hard-coded, so the
  layout generalises to unseen geometry instead of fitting the test cases (#31).
- `_auto_annotate` clears its build-time lint records on re-entry, and repeated
  `lint()` calls are stable (#32).

### Fixed

- AP242 / PMI STEP import segfault: STEP geometry is now read directly via
  `STEPControl_Reader`, avoiding the XCAF/PMI read that crashed (SIGSEGV) on
  with-PMI files such as NIST CTC-02 (#20).

### Tests

- Overfitting guards pin the general layout behaviour on turned/hybrid parts
  (flange OD + bolt circle), multi-bore parts, and the step-legibility boundary
  (#13).
- The full NIST CTC set (AP203 and AP242) builds and is covered by the slow
  end-to-end tier.

## v0.1.6 — 2026-06-15

### Fixed

- Section-view boolean cut on cast geometry: the exact `body - Box(...)` boolean
  raised an uncatchable `Standard_DomainError` (C++ abort, SIGABRT) on some parts
  (NIST CTC-04), crashing the whole drawing. `_fuzzy_cut()` now runs
  `BRepAlgoAPI_Cut` with a small fuzzy tolerance and keeps solids-only, making
  the section cut robust (#20, #22).

### Tests

- NIST CTC-04 (both AP203 and AP242) now build with a clean section view and are
  covered by the CTC build tests.
- Known: CTC-02 AP242 still segfaults inside OCCT's AP242/PMI STEP read (#20),
  excluded from build tests.

## v0.1.5 — 2026-06-15

### Fixed

- CTC-02 spurious full-page line: build123d's `ExportSVG` projected
  circle-edge-on edges (hole/fillet rims seen edge-on) as elliptical arcs with
  a near-zero minor radius, which renderers blow up into full-page lines.
  `sanitize_svg_arcs()` rewrites any arc with a sub-1e-3 mm radius into the
  straight line it actually is, leaving real arcs untouched (#19). Not a PMI
  issue — the file is AP203 geometry-only.

### Tests

- Added the full NIST CTC set (01–05) as fixtures, both AP203 geometry-only and
  AP242 (with-PMI) variants.
- Heavy end-to-end CTC fixture builds are marked `slow` and deselected from the
  default `pytest` run (fast normal run, ~4.5 min); CI runs the fast tier across
  the OS/Python matrix and the slow tier once.
- Known: AP242 CTC-02 and both CTC-04 variants crash OCCT on import (#20); their
  fixtures are excluded from build tests.

## v0.1.4 — 2026-06-15

### Changed

- Feature annotations (hole callouts, location dimensions, section view) now
  fire on feature presence independent of the turned/prismatic classification,
  so turned-and-drilled parts (e.g. flanges) get both the OD/centreline base
  set and per-hole callouts plus bolt-circle furniture (#10).
- Isometric view placement now uses a general largest-empty-rectangle search in
  place of the wide/flat-on-A3 special case (#11).
- Concentric bore-leader stacking is generalised beyond three, and the
  step-height dimension gate is now a single derived constant (#10, #12).

### Internal

- Single-sourced duplicated geometry constants from the draft preset (#12).
- Minor comment and logging cleanups.

## v0.1.0 — 2026-06-14

Initial release — spun out of `build123d-drafting-helpers` v0.9.1.

The automated drawing engine (`make_drawing`, `build_drawing`, `Drawing`)
was previously part of `build123d-drafting-helpers`. It is now a separate
AGPL-licensed package that depends on `build123d-drafting-helpers>=0.9.1`
for annotation primitives.

### Migration from build123d-drafting-helpers

```python
# Before
from build123d_drafting import make_drawing, Drawing, build_drawing

# After
from draftwright import make_drawing, Drawing, build_drawing
```

### Features (carried over from build123d-drafting-helpers)

- **`make_drawing`** / **`build_drawing`** — automatic multi-view technical
  drawing from a build123d solid: view layout, scale selection, orthographic
  projection, dimension placement, title block.
- **`Drawing`** — composable drawing object with `.lint()`, `.add()`,
  `.export_svg()`, `.export_dxf()`.
- **`choose_scale`** — ISO/ASME standard scale selection.
- **`lint_feature_coverage`** — checks annotation coverage against detected
  part features (holes, bosses, bolt circles).
- **Section A–A views** — automatic section view for blind/stepped holes,
  with ISO 128-44 solid filled cutting-plane arrows and ISO 128-50 45°
  hatching on the cut face.
- **`generate_script`** — generates a standalone drawing script from a STEP
  file.
