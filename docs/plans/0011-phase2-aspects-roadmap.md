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

### P2a — Toleranced dimensions (bilateral / limit) · #28

The self-contained value-shipping PR.

- Add optional `tolerance: float | tuple[float, float] | None = None` to
  `DimParameter` (`model/ir.py:52`). ADR 0011 §4 sanctions "tolerance is a property
  of a `DimParameter`."
- A `decorations` side-layer `{(feature, role) → tolerance}` (keyed by frozen
  feature value-equality + `Role`), threaded via
  `build_drawing(part, model=…, decorations=…)` and through `_repack`.
- `plan_dimensions` consults it to set the tolerance on the `PlannedDimension` /
  param (`planner.py:207-220`).
- `_core._dim` gains a `tolerance` passthrough to `Dimension(tolerance=…)`
  (`_core.py:149`) — the helpers primitive does the formatting.
- `Sheet`: `.tolerance(x)` / `.tolerance(lo, hi)` chainable handle on the
  `diameter` / `step` / `hole` declarations.
- Tests: bilateral + limit render into the label; decoration survives repack;
  no-decoration path byte-unchanged.

### P2a.2 — Fit-class deviation (`.fit("h6")`) · #29

The lone genuine (c) gap — helpers has no fit-code semantics.

- A small ISO 286 table in draftwright: `(fit_code, nominal_⌀) → (lower, upper)`
  deviation (h6/H7/g6/js… — the common shaft/hole classes), feeding P2a's
  tolerance path.
- `Sheet`: `.fit("h6")` on a diameter handle.
- Optional label form: render `⌀20 H7` (class) vs `⌀20 ±dev` (deviation) — config,
  default deviation.

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
