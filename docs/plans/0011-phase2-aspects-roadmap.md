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

### P2b — GD&T + finish placement API (#61) · #30

The reusable low-level primitive both the declarative verbs (P2c) and the PMI
auto-path (P2d) render through. Purely manual placement (caller supplies the target
point) per #61 v1.

- `dwg.place_fcf(target, characteristic, tolerance, datums=…, diameter=…,
  modifier=…)`, `dwg.place_datum(letter, target, side=…)`,
  `dwg.place_finish(ra_value, target)`.
- Each builds a `Leader(callout=FeatureControlFrame | DatumFeature |
  SurfaceFinish)` (the helpers `Leader.callout=` param hangs any sketch at the shelf
  end) and routes through the **`Strip`** layout so frames stack clear of the part
  and of each other — mirroring `place_dim()` (#25).
- The hard part is anchoring + leader routing + strip stacking; the glyphs are all
  helpers primitives.
- Read-side pairing (#61 note): a `datum_candidates()` helper surfacing the part's
  natural datum edges/faces (the min-X / min-Y corner is already the dimension
  datum) so a script can anchor `place_datum` without guessing coordinates.

### P2c — Sheet declarative aspect verbs · #31

The #445 vision surface over P2a + P2b.

- `sheet.datum("A", face)`, `diameter(journal).finish("Ra 0.8")`,
  `sheet.control(target).cylindricity(0.02).circular_runout(0.05, to=A)
  .perpendicular(0.05, to=B)` — each verb returns a small chainable handle.
- Records into the new `AnnotationRegistry` decoration peer map keyed to
  feature/face; a render pass reads it and calls the P2b placement API.
- **No fake verbs** — a verb ships only once the renderer behind it exists
  (the Phase-1 discipline).

### P2d — Auto-GD&T from STEP PMI (#62) · #32 · later

Complementary, not on the P2a→P2c critical path.

- Wire `pmi.py`'s already-extracted `gtol` / `datum` `PmiRecord`s into P2b's
  `place_fcf` / `place_datum` under `pmi="annotate"` (today they hit a "not yet
  annotatable (Phase 4)" debug log).
- Second producer, same placement path — the read/auto complement to P2c's
  declarative authoring.

## Dependency graph

```
P2a (#28) ─┬─ P2a.2 (#29)
           └─ P2c (#31) ── (also needs) ── P2b (#30) ── P2d (#32)
```

Suggested landing order: **P2a → P2a.2** (tolerance is a small, self-contained PR
that ships visible value fast), then **P2b → P2c** (the GD&T / finish stack),
**P2d** whenever PMI-carrying STEP input matters.

## Non-goals for Phase 2

- Surface-finish variants beyond the basic Ra check-mark (no-removal circle, lay
  direction, machining allowance) — helpers doesn't model them; defer.
- Composite/exotic GD&T modifiers beyond the common set already in helpers.
- The two Phase-0 caveats (sheet estimation + coverage lint still detect
  independently of `model=`) — tracked separately; not aspect work.
