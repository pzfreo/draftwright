# Sheet facts: draftwright's sheets as data, for a differential check

The port is checked against Python draftwright fact by fact, not pixel by pixel (see
[the plan](plan.md), "Iterate with differential checks"). `tools/capture_sheet_facts.py` builds
a sample of corpus parts in Python draftwright, with its default options, and records what each
sheet says: the sheet and its scale, the views and where they sit, each measurement with its
identity, value and printed text, the notes, the title block and the lint findings. The facts
are written to `tests/fixtures/sheets/<name>.json.gz`.

- `tests/common/facts.rs` reads them back (`facts::names()`, `facts::load(name)`).
- `tests/facts.rs` checks that every captured sheet loads and has what the comparison relies on,
  and that each view's camera puts the features its annotations name inside the view's drawn box.
- `examples/facts_compare.rs` plans each sampled part with the port and reports the sheet size,
  the scale, the view set, each view's camera and scale, and where each view sits, against the
  facts:

  ```
  cargo run --release --example facts_compare -- ~/repos/quiddity-python/tests/corpus [name ...]
  ```

  It is a report; `tests/views.rs` is the same comparison as a test (below).

## Views: the layout's arithmetic and its inputs

The port's sheet, scale and view placement (`src/layout.rs`) is draftwright's layout authority,
`compose.choose_scale` and `compose._layout_geometry`, ported whole. It is checked in two parts:

- **The arithmetic, exactly.** `tools/capture_layout.py` builds the same sample in Python
  draftwright and records every top-level call of those two functions, with all their inputs
  (extents, annotation strip depths, views, sections, tables, derived views, gutters) and
  results, at full precision, into `tests/fixtures/layout/<name>.json.gz`.
  `tests/layout.rs` replays all of them (about 190 scale choices and 90 layouts) and holds the
  port to 1e-9: the same scale, sheet and arrangement, view centres, isometric rectangle and fit
  verdicts.

  ```
  PYTHONPATH=<draftwright>/src <draftwright>/.venv/bin/python tools/capture_layout.py \
      <draftwright>/src ~/repos/quiddity-python/tests/corpus [name ...]
  ```

- **The result, against the facts.** `tests/views.rs` plans each sampled part and compares the
  sheet, the scale, the view set, and each view's scale and centre with the captured sheet.
  Every difference is listed in `tests/fixtures/known_views.json` with a verdict and its reason,
  and a listed difference that goes away must come off the list. The parts are read with their
  features: Python quiddity's recognition for each is kept in
  `tests/fixtures/recognition/corpus/` (written by `tools/recognise.py`; ctc04 has none, since the
  bridge refuses it on one face's area). Today the differences come from the layout's inputs, not
  its arithmetic: the port does not reserve the hole-callout band, angular footprints or the
  annotation-scheme bands, has no detail views, does not choose draftwright's annotation-demand
  arrangement profile, and cannot repack on measured annotations or re-plan on a finished
  drawing's lint as draftwright does (ADR 2 invariants 3 and 16). The test reads the corpus from
  `DRAFTWRIGHT_CORPUS` (by default `~/repos/quiddity-python/tests/corpus`) and says it is skipped when
  that is absent.
- **The bands, against draftwright's.** `tests/bands.rs` compares the bands the port reserves
  from the features (`src/demand.rs`) and its step count with draftwright's first placement (the
  first `_layout_geometry` call in `tests/fixtures/layout/`). Its differences are listed, with
  verdicts, in `tests/fixtures/known_bands.json`.

## Lint: the port's checks on Python's sheets

`tests/lint.rs` runs the port's lint (`src/lint/`) on each captured sheet and compares its
findings (holes, boss heights, chamfers, fillets, blends, through steps and slots) with Python's (`lint` above). The Analysis is the part read with the recognition
draftwright drew from: `tests/fixtures/recognition/sheets/<name>.json.gz`, written by
`tools/recognise.py` with draftwright's own quiddity (0.3.9):

```
~/repos/draftwright/.venv/bin/python tools/recognise.py <corpus>/<file> -o <name>.json
```

Each annotation becomes a mark: its text runs, and the measurements its registry claims. A claim
names its feature by geometry (`hole@(x,y,z)/axis[sizes]`, `pattern@(centre)/axis`), so it is
joined to the Analysis only where exactly one hole (or hole group) fits: the axis, the opening on
the axis line within 0.01 mm and within the hole's reach, and the diameter. Python's own findings
name a group by its quantity, diameter and a point on its axis. Every difference is listed in
`tests/fixtures/known_lint.json` with a verdict and the evidence; Python's "all N requirements
unverifiable" counts against each requirement the port checks.

## Dimensions: item by item

`tests/dimensions.rs` plans each sampled part with its features (Python quiddity 0.3.9's
recognition, the recogniser draftwright drew these sheets with, written by `tools/recognise.py`
into `tests/fixtures/recognition/sheets/`) and matches draftwright's annotations in the port's
families (overall extents, hole callouts, hole and pattern locations, pattern pitches) with the
plan's items: the same family (a location's names its axis), view and printed label, and the same
measurement identity (value within half the printed unit; for a callout, draftwright's features
where the item's holes or pattern are). Every difference is listed with its count, a verdict and
the evidence in `tests/fixtures/known_dimensions.json`. ctc04 is not compared: the bridge refuses
its recognition (face 367's area differs by 0.15% from OpenCascade's). `DIMS_REPORT=1` prints
the differences.

## Recapturing

```
PYTHONPATH=<draftwright>/src <draftwright>/.venv/bin/python tools/capture_sheet_facts.py \
    <draftwright>/src ~/repos/quiddity-python/tests/corpus [name ...]
```

- With no names, the tool captures every part in its `PARTS` table. A part takes 5 to 60 s.
- The output is deterministic. Floats are rounded to 1e-6, and lists keep draftwright's own
  order, which is itself deterministic. Recapturing an unchanged draftwright gives the same bytes.
- A part that draftwright cannot draw is reported. It is not written, and the run exits non-zero.
- The current fixtures come from draftwright `origin/main` 586628b (`producer` says
  `draftwright 0.5.0.dev0`, `quiddity 0.3.9`).

### The sample

| Name | Corpus file | Why |
| --- | --- | --- |
| `m10127` | `mfcadpp/10127.step` | prismatic: a hole, a boss, a hex pocket and steps |
| `m1000` | `mfcadpp/1000.step` | prismatic |
| `m10046` | `mfcadpp/10046.step` | prismatic, with angular dimensions |
| `string_post` | `gramel/string_post.step` | turned |
| `grm03` | `gramel/GRM-03_thumbwheel_drive_screw.step` | turned, front and side only |
| `grm05` | `gramel/GRM-05_depth_lock_bolt.step` | turned |
| `spool132` | `cadgenbench/flanged_spool_132.step` | turned, with bolt-circle patterns |
| `connector109` | `cadgenbench/threaded_connector_109.step` | turned, threaded |
| `cgb245` | `cadgenbench_inputs/cgb245.step` | holes, a pattern, slots and blends |
| `ctc01` | `nist/nist_ctc_01_asme1_rd.stp` | NIST test case |
| `ctc04` | `nist/nist_ctc_04_asme1_rd.stp` | NIST test case: many holes, on A1 |
| `ftc06` | `nist/nist_ftc_06_asme1_rd.stp` | NIST test case, with section A–A |
| `hanger` | `ttt_inputs/sm-hanger.step` | sheet metal, with a detail view |

- `cadgenbench_inputs/cgb207.step` was tried and left out. Python draftwright fails on it: "project_to_viewport returned empty geometry for view 'plan'".
- Gzipped corpus files were left out when the sample was chosen, as the port then read plain
  STEP only; it reads gzip since 2026-10-15 (maintainer decision), and the sample is unchanged.
- No part in the sample draws a table.
- No part in the sample carries a tolerance other than the general one. Draftwright's default
  options import no PMI tolerances onto these sheets.

## Schema: `draftwright.sheet-facts/1`

All lengths are in mm. Page coordinates have their origin at the sheet's lower left. World
coordinates are the STEP file's own.

```json
{
  "schema": "draftwright.sheet-facts/1",
  "binding": { "file": "mfcadpp/10127.step", "sha256": "4774…" },
  "producer": { "draftwright": "0.5.0.dev0", "quiddity": "0.3.9" },
  "sheet": { "format": "A3", "page": [420.0, 297.0], "scale": "2:1", "scale_factor": 2.0,
             "projection": "third" },
  "decisions": { "scale": {…}, "views": {…}, "arrangement": {…}, "section": {…}, "details": […] },
  "views": [ … ],
  "annotations": [ … ],
  "unconfirmed": [ { "annotation": "m_bossdia_y0", "reason": "compiled_claim_unconfirmed" } ],
  "title_block": { "title": "10127", "drawing_number": "DWG-001", "format": "A3", … },
  "suppressions": [ … ],
  "lint": [ … ]
}
```

### `binding`, `sheet`, `decisions`

- **`binding`:** the corpus-relative path and the file's sha256. The comparison refuses a file
  whose hash differs.
- **`sheet.format`:** the name draftwright prints in its title block (`A4` … `A0`, or
  `<w>x<h>`).
- **`sheet.scale`:** the scale as printed (`format_drawing_scale`).
- **`sheet.projection`:** the view plan's convention, `third` or `first`.
- **`decisions`:** `Drawing.scale_decision`, `view_decision`, `arrangement_decision`,
  `section_decision` and `detail_decisions`, kept whole. They record what draftwright tried
  (scales, pages, arrangements) and why it chose as it did.

### `views`

One entry per projected view, in `Drawing.views` order:

```json
{ "name": "front", "kind": "principal", "scale": 2.0,
  "direction": [0, -1, 0], "x_axis": [1, 0, 0], "up": [0, 0, 1],
  "look_at": [14.397, 16.134, 22.365], "origin": [67.391, 119.729],
  "bbox": [38.597, 75.0, 96.186, 164.459], "centre": [67.391, 119.729] }
```

- **`kind`:** `principal`, `pictorial` (the iso), `section` or `detail`. It is taken from the
  view plan, or for a derived view from the section mark or detail decision that names it.
- **`x_axis` and `up`:** the world directions of the page's x and y, read from the view's
  `ViewCoordinates` basis. **`direction`** is `x_axis × up`, pointing towards the viewer. These
  correspond to the port's `View { x, y, toward }`.
- **`scale`:** the view's own scale. The iso view and detail views have one of their own.
- **`look_at`** is the world point the camera looks at, and **`origin`** is where that point lands
  on the page. Together with the basis and the scale they place any world point:
  `origin + scale · ((p − look_at)·x_axis, (p − look_at)·up)`. The loader's `View::page` does
  this calculation.
- **`bbox` and `centre`:** the box of the placed visible and hidden geometry
  (`Drawing.view_bounds`), and the box's centre.

### `annotations`

There is one entry per annotation the drawing's registry names, in registry order. Furniture is
included: the title block, the projection symbol, the scale note, section lines and captions.

```json
{ "name": "hc_front0", "type": "Leader", "view": "front",
  "text": ["ø", "13.0", "5.7"], "label": "⌀13.0 ↧ 5.7", "label_bbox": [60.46, 63.67, 82.19, 66.34],
  "features": [ { "key": "hole@(9.929,11.513,21.368)/y[diameter=12.990,depth=5.730]",
                  "kind": "hole", "origin": [9.929, 11.513, 21.368], "axis": "y" } ],
  "measurements": [
    { "feature": { … }, "parameter": "bore.diameter", "value": 12.9898, "tolerance": null,
      "compiled": [ { "value": 12.9898, "tolerance": null, "span": null } ] } ] }
```

- **`type`:** the drawing class. The classes are `Dimension`, `AngularDimension`, `Leader` (hole
  callouts and feature callouts), `Note`, `CenterMark`, `Centerline`, `CenterlineCircle`,
  `TitleBlock`, `ProjectionSymbol`, `ArrowHead` and `Compound` (section wings and hatching).
- **`view`:** the orthographic view that owns it. It is null for sheet furniture and for
  section marks.
- **`text`:** the printed text runs exactly as the PDF's text layer carries them
  (`pdf_text_runs`). A symbol drawn as a path splits a label into several runs, and the path-only symbol (here the depth mark) is not in any of them.
  **`label`** is the annotation's own label string. **`label_bbox`** is the label's box on the
  page.
- **`features`:** the features the annotation is attached to (`registry.features_of`).
  - The `key` is draftwright's `feature_key`: `kind@(x,y,z)/axis[sizes]`. It describes the
    geometry and is not a handle: two features of one kind with the same origin, axis and sizes
    share a key.
- **`measurements`:** the measurement identities the annotation draws (`registry.measurement_of`).
  Each one has its `feature`, and a `parameter` in the role vocabulary (`role.kind[.discriminator]`,
  e.g. `location_off_axis.location.member.0.z`).
  - **`compiled`:** draftwright's compiled meanings for that identity, from
    `Drawing.measurement_snapshot`: each meaning's nominal value, tolerance and span.
  - **`value`** and **`tolerance`:** the meaning this annotation draws.
    - A correlated ladder shares one identity across its rungs, so its meanings are told apart by
      the annotation's own `span`.
    - `value` is null where draftwright's meanings could not settle one, and `compiled` is null
      where draftwright could not confirm the claim (the annotation is then listed in
      `unconfirmed`).
- **`cells`:** a table's measured cells, each with its `[row, column]` and a measurement as above.
  **`rows`** holds the table's text, header row first.
- **`section_mark`:** on a cutting-plane line, the cut position and the section view it makes.
- **`span`:** the measured span in world coordinates, where draftwright records one.

#### Identity collisions: why some `value`s are null

draftwright compiles its meanings per measurement identity, the feature (by `feature_key`) and
the `parameter`. When two drawn quantities share one identity, the measurement snapshot gives
every annotation that draws it all of their meanings, and the capture keeps them all in
`compiled` (`_claims`). It then picks this annotation's meaning (`_measurement`): if the
meanings differ in value and the annotation records a `span`, it keeps those with that span; if
exactly one value and tolerance remain, they are `value` and `tolerance`, and otherwise both are
null. Nothing is guessed.

In the current fixtures identities collide in two ways, four measurements in all:

- **One parameter for two quantities.** m1000's pocket locations `m_locx0` (39.3) and `m_locy0`
  (10.2) both claim `location_pocket.location` of the same pocket, with no axis discriminator,
  so both carry the meanings 39.336 and 10.187. Neither annotation records a span (and both
  meanings carry the same one), so the span cannot tell them apart.
- **One key for two features.** spool132's leaders `ldr_z0` (`ø70 BORE`) and `ldr_z1` (`ø62`)
  claim `bore.diameter` of `rotational@(0.000,0.000,70.000)/z`. That key has no sizes, so the
  two bores share it, and both annotations carry 70 and 62.

The other null `value`s (8 on ftc06, 1 each on connector109 and grm03) have `compiled` null too:
unconfirmed claims, not collisions. Two tests read `value`:

- `tests/dimensions.rs` takes an annotation's first non-null `value`. A family matched by value
  (envelope, locations, pitches, slots, bosses) accepts any port value when it is null, so the
  printed label, which must match exactly, decides. Callouts, fillets and chamfers are matched
  by where their features are, not by value. Today none of the four collisions reaches a value
  match: a pocket location is not a compared family, and spool132's bores are callouts.
- `tests/lint.rs` reads `value` only for `bore.diameter` on a hole or a pattern, to join a claim
  to its feature. A hole's diameter is taken from its key, so a null changes nothing there; a
  pattern with a null diameter is joined by its axis line and reach alone, still only where
  exactly one hole group fits.

### `unconfirmed`, `suppressions`, `lint`

- **`unconfirmed`:** annotations whose measurement claim draftwright's own verifier could not
  confirm, with its reason (`measurement_identity_unavailable`, `compiled_claim_unconfirmed`).
- **`suppressions`:** `Drawing.suppressions()`, the measurements draftwright deliberately left
  off, with the reason for each.
- **`lint`:** the findings of `Drawing.lint()` (the full critique, placement and coverage). Each
  finding has its `code`, `severity`, `message`, the annotation it names and the related ones,
  its `view`, the measurement IDs and source IDs it carries, and its outcome `stage`.

### Not captured

- **Annotation geometry:** dimension lines, extension lines, leader paths and arrow positions,
  and the exact text positions. Only label boxes are recorded. Layout is judged by rules, not
  by matching Python's positions.
- **Feature identities that last across builds:** draftwright's are local to one process. The
  feature `key` is the closest thing to one, and it is a description of the geometry.
- **The iso view's camera:** draftwright fits the iso after the sheet is settled, so it has its
  own scale. The capture records that scale; it does not record why the scale was chosen.
- **Hatching and section geometry:** these are recorded only as named annotations of type
  `Compound`.
