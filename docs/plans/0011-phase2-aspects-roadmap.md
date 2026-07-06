# ADR 0011 Phase 2 — aspect renderers roadmap

Execution roadmap for **Phase 2** of [ADR 0011](../adr/0011-ir-as-public-input.md)
(the IR as a public input). Phase 0 (the `model=` seam + object→feature
constructors) and Phase 1 (the fluent `Sheet` façade over today's renderers) have
landed; Phase 2 is the **aspect layer** — the drawing information geometry cannot
carry: tolerance, fit, surface finish, and GD&T. Vision: **#445**. Parent
roadmap: **#446**. Each work item below is one PR (split if it grows).

## The reframing: Phase 2 is wiring + placement, not primitive authoring

ADR 0011 §4 and the #446 north-star describe GD&T / finish as "the genuinely new
engine work." A survey of the installed `build123d-drafting-helpers` **0.13.0**
(the pinned floor) shows that is **no longer true at the primitive level** — every
glyph already exists:

| Aspect | Helpers primitive (0.13.0) | Status |
|---|---|---|
| Bilateral ± tolerance | `Dimension(tolerance=float)` → `"20.00 ±0.05"` | renders today |
| Limit tolerance | `Dimension(tolerance=(lo, hi))` → `"20 +0.1 -0.0"` | renders today |
| Basic (boxed) dim | `Dimension(basic=True)` | renders today |
| Surface finish (Ra check-mark, ISO 1302) | `SurfaceFinish(ra_value, position)` | renders today |
| Feature control frame (all 14 ISO 1101 chars, ⌀, Ⓜ/Ⓛ/Ⓟ) | `FeatureControlFrame`, `CompositeFeatureControlFrame` | renders today |
| Datum feature / datum target (ISO 5459) | `DatumFeature`, `DatumTarget` | renders today |
| Fit-class `⌀20 H7` → ± deviation | — | **the one real gap (ISO 286 table)** |

So Phase 2 is **carrying the authored intent through the compiler and placing the
symbol**, not drawing it. The current state confirms the work is threading + one
placement API, because **neither the IR nor the renderer has any hook today**:

- **IR** — `DimParameter` (`model/ir.py:52`) and every `Feature` subclass are
  `@dataclass(frozen=True)` and **value-only**; no `tolerance`/`fit`/GD&T/`finish`
  field anywhere. The one "tolerance" in the system is the drawing-level general
  tolerance string in the title block (`_core.py:512`, `_add_title_block`).
- **Planner** — `plan_dimensions` (`model/planner.py:201`) wraps each `DimParameter`
  in a frozen `PlannedDimension` (`planner.py:64`) whose only reserved-for-future
  intent field is `datum` (for #238 location work). No tolerance path.
- **Renderer** — every dimension label is a bare `_fmt(value)` string passed to the
  `_dim(...)` helper (`_core.py:149`) → `Dimension(..., label=…)`. There is **no
  tolerance-suffix or symbol hook** in the label chain.
- **Registry** — the ADR 0010 provenance sink `AnnotationRegistry._anno_feature`
  (`registry.py:43`, name→feature, post-render) proves feature-keyed side maps work
  (value-equality keying on frozen features, `names_for_feature` `registry.py:68`),
  but it points the *opposite* direction from what an authored decoration needs
  (feature→aspect, pre-render). A decoration side-layer is a **new but structurally
  identical peer map** that slots into the same snapshot/restore/clear machinery.
- **Lint** — `linting/structural.py:134-138` already exempts datum targets, datum
  features, and *surface-finish marks* from view-overlap linting — anticipatory
  carve-outs for renderers that don't exist yet. So placed GD&T/finish participates
  in `lint()` for free.

## Where aspects live (ADR 0011 §4, confirmed against the code)

1. **Tolerance / fit → the dimension.** A tolerance is a property of a
   `DimParameter` (a dimension is toleranced). It rides as an optional field on the
   IR's value carrier — *not* on a `Feature`, keeping the frozen feature schema
   clean.
2. **GD&T / finish → a decoration side-layer keyed to a feature/face.** A new peer
   map on `AnnotationRegistry`, authored pre-render, consumed by a render pass that
   calls the placement API. The placed annotations then get `feature=`-tagged
   through the existing `add(...)` seam, so they flow into the same provenance sink
   with zero new plumbing.
3. **Authored intent enters via a `decorations=` input**, threaded alongside
   `model=` through `build_drawing` → `_assemble` → `_repack` (both passes), exactly
   as `model=` is today (`builder.py:208/295/482/484`).

## Work items

Ordered to front-load the cheapest, highest-value item and isolate the
placement-hard GD&T behind a reusable primitive.

### P2a — Toleranced dimensions (bilateral / limit) · #28 · **DONE**

The self-contained value-shipping PR. **Full-uniform** scope (per the user's call):
the tolerance renders on the linear `Dimension` path AND the `Leader` / `HoleCallout`
⌀ path, so every P2a verb shows a ±.

- `DimParameter.tolerance: float | tuple[float, float] | None` (`model/ir.py`) — a
  symmetric float or an `(lower, upper)` limit pair.
- A `decorations` side-layer `{(feature, kind) → tolerance}` on `PartModel`, threaded
  via `build_drawing(part, model=…, decorations=…)` and through **both** assemble passes
  (`_repack`). **Key is `(feature, kind)`, not `(feature, role)`** — a step's length and
  diameter share `role="step"`, so `kind` is what disambiguates them.
- `plan_dimensions` reads `model.decorations` (zero call-site changes) and
  `replace(param, tolerance=…)`.
- Linear dims: `_core._dim` forwards `tolerance=` to `Dimension(tolerance=…)` (already
  splats `**kwargs`; also survives repair/repack). Wired in `render_step_lengths` /
  `_draw_step_chain` (a uniform `N× v` collapse carries no ± — can't tolerance N steps).
- ⌀ callouts: helpers' `Leader`/`HoleCallout` take no `tolerance=`, so draftwright owns
  **`_core._tol_suffix`** — the `±t` / `+hi -lo` suffix baked into the label string,
  byte-matching helpers' `Dimension` `_format_label` (same draft precision). Wired in
  `render_diameters` (the boss/step OD leaders) and `hole_callout_spec` /
  `callout_from_spec` (the bore string; `HoleCallout` accepts a diameter carrying tol text).
- `Sheet`: `.tolerance(x)` / `.tolerance(lo, hi)` on `hole` (bore ⌀), `diameter`/`boss`
  (OD), and `step` (length by default, `on="diameter"` for the OD). Keyed by feature
  index so a handle survives a later `.depth()` feature replacement.

**Shipped caveats (document, follow up):**
- **Precision.** The suffix rounds to the sheet's `decimal_precision` (1 dp today, to
  match `Dimension`), so a `±0.05` renders `±0.1`. Fine tolerances (≤0.05) need a
  per-dimension precision knob — a follow-up (likely a helpers change so both paths agree).
- **A toleranced ⌀ callout is wider** and, in the iso-bounded plan-view strip, can drop
  via the existing place-what-fits (`callout_dropped` warning) where a plain one fit —
  the same behaviour as any wide callout. The estimate uses the real `callout_width`, so
  there is no silent overflow. Tracked as **#450** (prefer the left strip / escalate the
  sheet for a deliberately-wider toleranced callout — engine layout work).
- **Extract to helpers.** `_tol_suffix` exists only because `Leader`/`HoleCallout` lack a
  `tolerance=` param; file an upstream issue to add one, then delete the suffix and pass
  the tolerance through like `Dimension` does. Tracked as **#449**.

### P2a.2 — Fit-class deviation (`.fit("h6")`) · #29 · **DONE**

The lone genuine (c) gap — helpers has no fit-code semantics.

- **`draftwright/fits.py`** — the ISO 286 table `fit_deviation(code, nominal) → (lower,
  upper)` signed deviations (mm), computed from the standard IT-grade + fundamental-
  deviation tables over the common classes (holes `H`/`G`/`F` via the EI=−es mirror;
  shafts `h`/`g`/`f`/`js`/`k`/`n`/`p`) and ⌀ ≤ 250 mm. **Fails loud** outside coverage —
  never a silent wrong number (the delta-rule K/N/P *holes* are intentionally out). 20
  tests pin every value against published ISO 286 deviations.
- **`FitClass`** (in `fits.py`) is a resolved fit that rides `DimParameter.tolerance` as
  an aspect marker, so it reuses **all** of P2a's threading — `_core._tol_suffix`
  dispatches it, zero planner / render-tuple changes. It carries the deviations for tooling
  and renders its own suffix.
- **`Sheet`**: `.fit("H7")` on a `hole` (bore ⌀) or a `diameter`/`boss`/`step` (the OD — a
  fit is always diametral). Resolved + validated at declaration against the feature's
  nominal ⌀.
- **Label form (decided):** **default the fit-class code** (`ø20 H7`) — the compact,
  unambiguous, always-correct single-line form — with **`show="deviation"`** for the signed
  deviations (`ø20 +0.021/0`, both-negative like `g6` → `-0.007/-0.020`). *(Amends the
  original "default deviation" note: on a single-line ⌀ callout the class code reads
  cleaner and a shared-sign fit can't use P2a's `+hi/-lo` formatter; the deviation form
  needs its own precision — fit deviations show 3–4 dp, not the sheet's 1 dp.)*

### P2b — GD&T + finish placement API (#61) · #30 · **DONE**

The build-time render core both the declarative verbs (P2c) and the PMI auto-path
(P2d) render through.

**Amended (2026-07-06): build-time corridor candidate, NOT an imperative primitive.**
The original bullets below proposed `dwg.place_fcf(target, …)` routing through
`Strip.allocate` mirroring `place_dim`. Two problems surfaced in a first cut and were
rejected by the user:

1. **Imperative post-build placement is blind to the shared cross-view corridor.** A
   frame placed *after* `build_drawing` returns (past `_auto_annotate`, past the
   measure-and-repack) never carves around the other view's dims and never triggers a
   repack — it overlapped a plan-view dimension exactly where ADR 0004's compose-then-pack
   is supposed to prevent it. GD&T must be placed *during* the build, like every other
   annotation, so `_measure_blocks` folds it into its `ViewBlock` and the repack net
   separates cross-view (ADR 0004).
2. **`Strip.allocate` is the legacy cursor ADR 0009 retires.** Routing new work through
   it would add to the deprecated path. New annotations join the **collect-then-solve
   corridor** (ADR 0009), the target architecture.

**Delivered design (Tier 1):**
- Three frozen IR items — `ControlFrame` / `DatumRef` / `Finish` (`model/ir.py`), peers
  of `PmiFeature` (`parameters()` empty, so they bypass the dimension planner). Each
  carries its target `(view, side)` strip + model-space site; the Sheet layer (P2c)
  computes those from a build123d face.
- `render_gdt` (`annotations/from_model.py`) — builds each glyph, hangs it on a
  `Leader`, and **registers a `CorridorCandidate` into the target strip before
  `drain_corridors`**, so the one ADR 0009 solve orders and spaces frames crossing-free
  *with* the dims (a first-class candidate, not a leftover first-fit like `render_pmi`).
  Wired into `_auto_annotate` after `render_slots`, before the drain.
- **Real-footprint plumbing (the ADR 0009 down-payment):** `CorridorCandidate.size`
  carries the glyph's own box (a frame is ~24×6 mm); `solve_corridor` forwards a
  `sizes` map into `place_strip_candidates`, which now feeds it to the `StripCandidate`
  instead of the `(tier, tier)` label-height hardcode. Absent → `(tier, tier)`, so every
  existing dimension stays byte-identical. The footprint is the *glyph* box, not the
  leader+glyph box — the shaft back to the feature would inflate the stacking extent
  (the same reason dims reserve one label-height). See ADR 0009 Amendment 7.
- A declared frame is **force-kept** (policy B) — no alternate view — so a full strip
  drops it with a first-class `gdt_dropped` warning rather than a silent vanish; the
  placed frame gets `feature=`-tagged through `add(...)` into the ADR 0010 provenance
  sink for free.

**Deferred to P2c / follow-ups:** left/right strips render but the common case is
above/below; the read-side `datum_candidates()` helper (surface the part's natural
datum edges so a script anchors without guessing coordinates) moves to P2c where the
face→site projection lives.

### P2c — Sheet declarative aspect verbs · #479 · **DONE**

The #445 vision surface over P2a + P2b. Shipped in two PRs (#31 in the original heading
was a stale ref to a closed layout issue; the plan lived in **#479**).

**P2c.1 (#480) — `.finish()` / `sheet.datum()` + the target derivation.** The genuinely-new
work: `declare.gdt_target(ref, part) → (view, side, site, axis)` resolves a GD&T target
geometrically at *declaration* time (no `Analysis`): a **feature** → its axis site, face-on
view (`z→plan`, `x→side`, `y→front`); a build123d **planar face** → its centre, normal→axis,
edge-on view. `view=`/`side=` always override. `declare.datum()`/`finish()` build the P2b IR
items; the Sheet verbs `_Hole/_Dim.finish`, `sheet.datum`, `sheet.finish` append them (a
handle-sourced item records `origin` by feature **index** and re-binds at build, mirroring
P2a — so a later `.depth()` doesn't strand provenance).

**P2c.2 (#482) — `sheet.control()` + the feature-control-frame builder.** `_Control` exposes
one method per **all 14 ISO 1101 characteristics** (form tolerances take no `to=`;
position/concentricity default `⌀`); `_parse_datums` accepts `"A"`/`"A B"`/`"A|B"`/`("A","B")`;
`diameter=`/`modifier=` pass through. Datum-letter **validation** warns at build on a `to="A"`
with no declared `sheet.datum("A", …)`. A **view-aware default side** (`_FEATURE_SIDE`:
plan→above, front/side→below — the roomiest per view) so the flagship two-frame stack places
without an override.

- Aspects are standalone IR **features** appended to `Sheet._features` (not a `decorations`
  peer map — that's P2a's tolerance path); consumed by the already-wired `render_gdt`.
- **No fake verbs** — both shipped only because #478's renderer exists.
- Four adversarial-review rounds across P2b+P2c fixed 3 real defects (public-IR crash,
  off-sheet overshoot, degenerate-leader crash, provenance staleness).
- **Follow-up #481** — `render_gdt` side-fallthrough: on a congested default side, try the
  other side before dropping (the view-aware default is the current stopgap).

### P2d — Auto-GD&T from STEP PMI (#62) · later · **the last Phase-2 item**

Complementary, not on the P2a→P2c critical path.

- Wire `pmi.py`'s already-extracted `gtol` / `datum` `PmiRecord`s into the P2b IR items
  (`ControlFrame` / `DatumRef`) under `pmi="annotate"` (today they hit a "not yet
  annotatable (Phase 4)" debug log in `render_pmi`) — a detector that emits the same IR
  the declarative verbs do, so `render_gdt` places them with no new plumbing.
- Second producer, same placement path — the read/auto complement to P2c's declarative
  authoring.

## Dependency graph

```
P2a (#28, DONE) ─┬─ P2a.2 (#29, DONE)
                 └─ P2c (#479, DONE) ── (needs) ── P2b (#478, DONE) ── P2d (#62, next)
```

Landing order (as executed): **P2a → P2a.2** → **P2b (#478) → P2c.1 (#480) → P2c.2 (#482)**.
Remaining: **P2d (#62)** whenever PMI-carrying STEP input matters, and the **#481**
placement-fallthrough quality follow-up.

## Non-goals for Phase 2

- Surface-finish variants beyond the basic Ra check-mark (no-removal circle, lay
  direction, machining allowance) — helpers doesn't model them; defer.
- Composite/exotic GD&T modifiers beyond the common set already in helpers.
- The two Phase-0 caveats (sheet estimation + coverage lint still detect
  independently of `model=`) — tracked separately; not aspect work.
