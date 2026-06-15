# Changelog

## Unreleased

### Changed

- **Hole callouts are no longer capped at four per view.** Every distinct bore
  is attempted; the per-view placement bounds (front-view shaft rows, plan/side
  strip Y-solver) are the real limit, and a callout that genuinely doesn't fit
  surfaces as `callout_dropped` (a warning, with its diameter, excluded from
  `feature_not_dimensioned`). Three previously-silent front-view skip paths now
  surface too. The bore-callout "no room"/"strip full" drops are reclassified
  from error (`placement_unsatisfiable`) to this warning, since under the
  adaptive model an unplaceable callout is an incomplete — not invalid —
  drawing. Completes the adaptive-caps work (#36); NIST CTC parts now place
  5–9 callouts (vs a capped 4) with no error-severity lint.
- **Step-height dimensions are no longer capped at three.** The `fv_zones.right`
  corridor is now sized for every legible step (`_est_right_strip_depth` no
  longer caps the count), and a step dim is placed for each legible level. A
  part with several shoulders gets them all dimensioned instead of an arbitrary
  three; the strip allocator remains the real bound (an unplaceable step
  surfaces as `placement_unsatisfiable`). Verified the NIST CTC parts (8–16
  step faces each) build with no error-severity lint. Second step of the
  adaptive-caps work (#36); the per-view callout cap follows. The
  `step_dim_dropped` lint code is removed (the cap that produced it is gone).
- Hole **location dimensions are no longer capped at four** per part: they are
  placed nearest-datum-first (baseline practice) until the above-view tier
  strips fill, so a part with room gets all its holes located instead of an
  arbitrary four. Refs that genuinely don't fit are skipped (never
  force-placed) and surface as `location_ref_dropped` (#36). First step of the
  adaptive-caps work; step-height and per-view callout caps follow.

### Added

- `Drawing.lint_summary()` — a JSON-friendly aggregate of `lint()` for
  non-interactive callers (scripts, or an LLM via the API): severity counts,
  per-code counts, a `geometry_issues` tally (standards/geometry checks vs pure
  layout), a `passed` flag, a coarse 0–1 `score`, and the full issue list. Gives
  a single signal to gate and optimise on without rendering the SVG (#32).

### Fixed

- Annotations the layout had to drop (hole callouts past the per-view cap,
  location references past the per-part cap, step-height dimensions past the
  first three, bore callouts with no room or an unsatisfiable strip) are no
  longer silent: each is recorded during the build and surfaced by `lint()`
  under a dedicated code (`callout_dropped`, `location_ref_dropped`,
  `step_dim_dropped`, `placement_unsatisfiable`), so a short drawing always
  carries a machine-readable reason (#32).
- `placement_unsatisfiable` (the engine could not place an annotation it wanted
  to, as opposed to a deliberate cap) is **error** severity, so it fails the
  `lint_summary()` `passed` gate; the deliberate-cap drops stay warnings (#32).
- A callout dropped by the per-view cap is no longer double-reported: the
  dropped diameters are named in the `callout_dropped` message and excluded from
  `feature_not_dimensioned` (#32).
- `_auto_annotate` is idempotent for build-time lint records — re-annotating a
  drawing no longer accumulates duplicate drop reports (#32).

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
