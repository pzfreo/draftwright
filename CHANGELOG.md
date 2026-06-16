# Changelog

## Unreleased

### Changed

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

### Changed

- **Step heights are dimensioned only where legibly separable.** After the
  adaptive cap (#36), a part with many closely-spaced shoulders (e.g. NIST
  CTC-02 at 1:5) tried to dimension faces only ~1 mm apart on the page. A step
  is now dimensioned only if it is both tall enough from the base *and* at least
  one legible step-height above the previously dimensioned one; the rest surface
  as `step_dim_dropped` (use a detail view). "Fits" is not the same as
  "legible" (#41).
- **Tighter location-dimension tier pitch.** The vertical pitch between stacked
  X/Y location dimensions is now derived from the label footprint
  (`font_size + 2·pad_around_text`, ≈7 mm) instead of a looser `font_size·3`,
  so location stacks pack closer (#41).

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
