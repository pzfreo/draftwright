# ADR R5: Multi-sheet drawings

**Status:** Accepted (maintainer, 2026-10-13). The allocation policy in the first section was
accepted on 2026-10-11; the technical design on 2026-10-13, with three decisions recorded in
"Decided at sign-off" below. Stage I builds it.

**Builds on:** R1 (the document model: the spec stores intent, renderers draw only from the plan,
IDs are derived), the layout recovery (ISSUE-1299-a, `model::recover`) and drop-and-escalate
(`src/hole_table.rs`, the maintainer's decision of 2026-10-09).

**Translates:** Python draftwright's `Document` (`src/draftwright/document.py`). There a document
is a set of separately authored `Sheet`s sharing one drawing number and revision, and each sheet's
title-block `sheet` cell gets `"n/m"`, or else all sheets get custom labels. Here, the planner
allocates the sheets from one spec. The cell reads ISO 7200's `n/m` (maintainer, 2026-10-13).

**Words.** This record says a view or table is *assigned* to a sheet (the spec's `sheet` fields).
It never says *pinned*: that word belongs to the existing placement intents (`Place.pinned`,
`ViewPlace.pinned`, `placement_intent::has_pins`), which this record calls *placement pins*.

## Maximum printable paper amendment (2026-10-21)

`sheet/preferred_max_size` (default A3) and `sheet/preferred_max_sheets`
(default 2) are soft preferences; `sheets.max` remains hard. Try one sheet at each
size through the preferred paper, then additional sheets at that paper, then
larger papers with the same single-then-multiple order. More than the preferred
count is tried only at A0. Whole-view allocation includes principal projections
and keeps all annotations with their view. Clipping is a hard rejection, and
readability/required information are compared with the unrestricted larger result.
One advisory explains exceeded preferences. See [the model](../model.md#sheet-allocation-r5-stage-i)
for the full ordering and usability definition. Explicit choices and keep_layout win.

## Small-part overflow amendment (2026-10-16)

The maintainer's practical-paper rule overrides the original paper-first search below:
when a part's full-size view envelope fits A3, and no valid complete single A4/A3
candidate exists, try R5 allocation on A4 and then A3 before allowing A2 or larger.
The implicit allocation may use up to nine sheets, the existing supported bound;
it packs each sheet before opening another. It uses the same overflow prefixes,
keep-together groups, principal-view restriction, table rules and two-way section
references as explicit allocation. Working paper and sheet-count controls are not
written into the authored spec.

A candidate must retain the seed's supported measurements, have no required drops
or hard layout findings, fit its actual drawn bounds on every sheet, and respect
scale floors. If neither practical-paper allocation succeeds, the larger-paper
search remains available and the sheet basis records why both attempts failed.
Parts whose full-size views need larger paper retain the previous search. Explicit
paper, scale, placement and sheet-count controls remain binding; in particular,
`sheets.max = 1` prohibits this automatic overflow. Unspecified sheet counts retain
the original single-sheet path whenever it produces a valid practical-paper drawing.

### Settled sections and auxiliary-page composition

A second sheet is allowed only after a complete single-sheet layout really fails.
For unpinned section layouts without page-anchored annotations, recovery measures
view ink, owned dimensions, centrelines, cutting-plane letters and section captions.
It replaces conservative bands with these complete measured blocks, moves a free
section into available space when necessary, and fits the orientation view around
them. It accepts the correction only when the complete drawing passes the existing
bounds, title-block and disjoint-view checks. Already fitting layouts stay intact.

Auxiliary pages first try evenly spaced, centred cells above the title block, with
extra frame clearance. Dense or table-constrained pages retain wrapping packing.
Reduced isometrics use the same `ISO VIEW (NTS)` caption as sheet 1, rather than an
independent scale claim. Section references address the section view's centre;
the reciprocal reference addresses the actual cutting-plane letter. Each uses its
destination page's grid. Structural lint verifies the view or letter actually drawn,
so a caption in an adjacent zone cannot validate an incorrect address.

## The agreed policy (maintainer's decision, 2026-10-11, verbatim)

> # Sheet allocation policy (agreed by the maintainer, 2026-10-11)
>
> ## Default allocation
> | Content | Default sheet | Rule |
> |---|---|---|
> | Title block, general notes, general tolerance, projection symbol | 1 | Notes on sheet 1 only; every sheet repeats the title block with "SHEET n OF m". |
> | Principal views (front, plan, side) and overall sizes | 1 | Principal views are never split across sheets. |
> | Datum feature symbols | where the datum feature is shown best, normally sheet 1 | Every datum a feature control frame references must be identified on sheet 1 or on the same sheet as that frame. |
> | Feature control frames | with the feature's dimension, on the view that shows the feature best, on whatever sheet that view is (ASME Y14.5 / ISO 129-1 practice) | A frame never leaves its feature. |
> | Hole table | 1, with tags on the plan view | A long table moves to its own sheet; tags stay on the views. |
> | Sections and enlarged details | the next sheet with room | Cross-referenced both ways with sheet and grid zone ("SECTION A-A, SHEET 2, ZONE C4" / "FROM SHEET 1, ZONE B3"). |
> | Dense regions | a detail view on a later sheet | Preferred to shrinking the whole sheet below the smallest legible scale. |
>
> ## Controls
> - Spec: maximum sheets, sheet size, smallest scale allowed; pin a view or item to a sheet; keep-together groups (e.g. a section with its parent view); "all GD&T on sheet 1" (forces frames onto sheet-1 views, for customers or inspectors who require it).
> - MCP and editor: "move view to sheet n" op; sheet-qualified item addresses. Editor: no sheet UI in these stages (the maintainer will redesign the editor after them); keep the editor building and working on sheet 1 only.
> - Checks (problems): frame references a datum not identified on sheet 1 or the frame's sheet; section/detail missing its cross-reference; a sheet below the smallest scale allowed.

### How this design reads the policy (interpretations the maintainer can overturn)

- **"Sections and enlarged details: the next sheet with room"** is read as *sheet 1 unless sheet
  1 overflows*: sheet 1 is the first sheet, and a section that fits there stays there. Any other
  reading moves today's section A–A off sheet 1 and breaks invariant 1 (single-sheet specs
  unchanged). A maintainer who wants sections on sheet 2 by default can say so; it is a change to
  step 1 of the allocation, not to its structure.
- **Datum symbols, frames and dimensions in Stage I.** Today only the principal views carry them:
  `dims::gdt::plan` takes its carriers from `["front", "plan", "side"]`
  (`src/dims/gdt.rs`, the `frames` map; `PRINCIPALS` for the demand) and
  `DimensionSpec.view` names `front`, `plan` or `side` (`src/model.rs`). The principal views never
  leave sheet 1, so in Stage I every frame, datum symbol and dimension is on sheet 1. The datum
  rule, "all GD&T on sheet 1" and "a frame never leaves its feature" therefore **hold by
  construction** in Stage I. They become live decisions only when a non-principal view carries
  items, which is the detail-view stage (Stage J, below). Stage I asserts them (section 3.6); it
  does not build machinery that cannot run.
- **"Pin a view or item to a sheet"** is delivered in Stage I for views, the hole table and corner
  tables, the items that can be off sheet 1. Assigning a dimension or annotation to a sheet comes
  with Stage J. The blocker is concrete: no view off sheet 1 can carry one, so the field could
  only ever hold `1`.
- **"Dense regions: a detail view on a later sheet"** is Stage J. Until it lands, a dense region
  falls back to the paper ladder and, if the floor is still not met, `sheet_below_smallest_scale`.

## Decision (technical design)

### 1. The spec

Everything is additive and absent by default. `draftwright.spec/2` is kept: an absent field
serialises as nothing, so an existing spec's bytes are unchanged. A build without sheets refuses a
spec that has them (`deny_unknown_fields`), as it should.

```json
{
  "sheet":  { "size": "A3", "title": { "number": "DW-100" } },
  "sheets": {
    "max": 3,
    "smallest_scale": "1:2",
    "gdt_on_sheet_1": true,
    "keep_together": [["side", "section_aa"]],
    "hole_table": 2
  },
  "views":  { "iso": { "sheet": 2 } },
  "tables": [ { "id": "t:1", "rows": [], "sheet": 2 } ]
}
```

| Field | Meaning | Absent |
| --- | --- | --- |
| `sheets.max` | The number of sheets the planner may use, 1 to 9 | 1: today's planner, unchanged |
| `sheet.size` | One size for every sheet of the document (existing field) | As today: the recovery's ladder picks it, on sheet 1, and every sheet shares it |
| `sheets.smallest_scale` | The floor, an ISO 5455 scale. No sheet's views may be planned below it without the problem `sheet_below_smallest_scale` | No floor |
| `views.<name>.sheet` | Assigns the view to a sheet. A principal view only to 1 | The planner decides |
| `tables[k].sheet` | Assigns a corner table to a sheet | Sheet 1 |
| `sheets.hole_table` | Assigns the hole table, *if the plan makes one*, to a sheet. It does not force a table (section 3.4) | Sheet 1; a long table moves |
| `sheets.keep_together` | Groups of view names (and `hole_table`) that share one sheet | None |
| `sheets.gdt_on_sheet_1` | Every frame and datum symbol on a sheet-1 view | `false`. In Stage I it is always met (section 3.6) |

`edit` validates each of these as it validates every path:

- A sheet number above `sheets.max` is refused; the choices are `1..max`, and its `fix` also
  offers `set sheets/max`.
- An assignment against a keep-together group is refused, naming the group.
- **Placement intents.** A view with `place.relative_to = <other>` (`ViewPlace`) belongs with
  that view: an explicit assignment that puts the two on different sheets is refused, with the
  choices "assign `<other>`'s sheet" and `remove views/<name>/place`. Without an explicit
  assignment the allocation treats the pair as an implicit keep-together group (section 3.2). A
  view with a placement pin (`place.pinned`) is never moved by the allocation; it can still be
  assigned explicitly, and the same `relative_to` rule applies. Dimension and annotation
  `place.view` cannot clash in Stage I: their carriers are principal views, all on sheet 1.
- **`sheet.scale` below `sheets.smallest_scale` is refused**, because the two intents contradict.
  The choices are `remove sheet/scale` and `set sheets/smallest_scale <the scale>`.
- An assignment is an *intent*, never a page coordinate, so R1 invariant 1 holds.

### 2. The plan

- `plan.sheet` stays as it is and is always **sheet 1**, with ID `sheet`. Every existing reader,
  the editor included, keeps reading it.
- `plan.sheets: Vec<PlannedSheet>` holds sheets 2..m, with IDs `sheet:2` … `sheet:m`. It is
  omitted when empty. Every sheet has `plan.sheet`'s `size` and `page`.
- `PlannedSheet` gains `number: u32` (omitted when 1) and `zones: Option<Zones>` (the grid,
  section 5, present only when m > 1).
- `PlannedView`, `PlannedTable` and `HoleTable` gain `sheet: u32`, omitted when it is 1.
  Dimensions, frames, datum symbols, labels and balloons take the sheet of their carrying view.
  Notes and drawing-wide requirements are on sheet 1. `items::Item` gains `sheet`, derived the
  same way, so nothing is stored twice.
- `PlannedView` gains `scale_note: Option<String>` (for example `"SCALE 1:2"`) when its scale is
  not its sheet's, and `xref: Option<CrossRef>` (section 5).
- **IDs do not change** when an item moves between sheets, just as a dimension's ID does not
  change when it moves between views (docs/model.md, IDs). R1 invariant 5 holds.

### 3. Allocation (`src/sheets.rs`, new; called from `model::plan_recovered`)

#### 3.1 The floor in the paper ladder

`sheets.smallest_scale` enters the existing recovery, not a loop around it, so that **a larger
paper still comes before a further sheet**:

- `model::recover` gains a `floor: Option<f64>` (from `sheets.smallest_scale`). Its `findings`
  count a plan whose `sheet.scale_factor` is below the floor as one more hard finding. So a first
  plan below the floor starts the ladder even when it is otherwise clean, and a try below the
  floor is never "clean", so it is never taken.
- `layout::recover` gains the same floor and does not try scales below it: with a floor, "the
  nearest smaller scale" is skipped when it is below the floor, and the ladder goes on to the
  larger scales and then the larger sheets.
- With `floor = None` both functions behave exactly as today (invariant 1).
- `recover`'s early returns are kept: with `sheet.scale` set, or with placement pins
  (`has_pins`), it re-plans nothing. With `sheet.scale` set the floor cannot be violated (the
  edit refuses it, section 1). With placement pins and a floor, sheet 1 can still be relieved by
  moving content (3.2); the paper is then the first plan's.
- `sheet.size` set: the ladder never leaves that paper, as today.

This is all the ladder change. The allocation never picks paper itself; it only decides what
sheet 1 holds, and asks `recover` for the best sheet 1 of that content.

#### 3.2 The search

The **candidates**, in the policy's overflow order, are at most three in Stage I:

1. the isometric (`iso`);
2. section A–A (`section_aa`, the only section the port draws), together with its keep-together
   group if it has one;
3. the hole table, when it is *long* (3.4).

A view assigned to a sheet, a view with a placement pin, and a principal view are never
candidates. A keep-together group, explicit or implicit (a `relative_to` pair), is a candidate
only as a whole, and only if it contains no principal view: a group containing a principal view
stays on sheet 1. Content assigned to a sheet above 1 is *forced*: it is off sheet 1 in every
step, including step 0.

The **steps are cumulative prefixes** of the candidate list. Step 0 moves only the forced
content; step k moves the forced content and candidates 1..k. Each step is one
`plan_recovered`-equivalent run of sheet 1 (`plan_escalated` then `recover` with the floor) with
the moved views omitted (`ViewSpec.omit` on a working copy of the spec, never written back; the
problems that omission itself reports, such as a section not shown, are not carried into the
plan) and, for the long table, `escalate` told where the table goes (3.4). So:

- With `sheets.max` absent or 1: step 0 only, with nothing forced (an assignment above 1 is
  refused at edit when `max` is 1). This is today's code path, plus the floor when one is set.
- With `sheets.max` > 1: step 0 is taken when its sheet 1 is **clean**: no hard finding, no
  required drop, and its scale at or above the floor. Otherwise steps 1, 2, 3 follow, and the
  first clean one is taken.
- A step whose moved content would need more sheets than `sheets.max` when packed (3.3) is not
  run. The search records it as "blocked by `sheets.max`", for the fix in section 7.
- **The bound:** at most 4 runs of sheet 1 (step 0 and three steps), each at most 1 + 6
  `plan_escalated` calls as now, so at most 28 `plan_escalated` calls against today's 7. Only a
  spec with `sheets.max` > 1 whose step 0 is not clean pays more than today.

**When no step is clean**, the final plan is the step whose sheet 1 is best by, in order: fewest
hard findings (the floor counted, as above), fewest required drops, the largest sheet-1 scale,
and then the fewest moves. Ties go to the earlier step, so step 0 (no extra sheets) wins any tie:
the planner never adds a sheet that does not help. The sheet's `basis` records every step tried
and its findings, as `layout_replanned` does now, and the remaining findings and
`sheet_below_smallest_scale` are reported as usual.

#### 3.3 Packing sheets 2..m

`layout::plan_sheet(blocks, tables, page, scale)` (new) fills one sheet:

1. The title block is reserved at the lower right as on sheet 1, with no notes block and no
   projection symbol.
2. Corner tables assigned to the sheet are placed next, each by the existing
   `tables::place` towards its `prefer` corner, clear of the title block and the tables already
   placed. A table that has no place gets the existing `table_not_placed` warning.
3. The moved view blocks, each with its annotation bands, are packed in candidate order as a row
   that wraps, clear of the title block and the tables.

The moved content goes onto sheets 2..m in order, each sheet filled before the next ("the next
sheet with room"). A section keeps its parent view's scale. A view that does not fit at its
parent's scale on the current sheet first goes to a fresh sheet of its own (when `sheets.max`
allows); if it still does not fit, it takes the largest standard scale that fits and gets a
`scale_note`. A long hole table always gets a sheet of its own.

Every sheet has sheet 1's paper. The ladder is not re-run for sheets 2..m: a sheet-n view that
does not fit is shrunk, never given larger paper, because the policy fixes one format for the
document.

**Numbering.** Assigned content is placed first on its sheet. Moved content then fills sheets
from 2 upwards, using any sheet left empty. If a sheet is still empty (for example, `iso`
assigned to sheet 3 and nothing else off sheet 1), the sheets are **renumbered** to close the gap,
and an info problem `sheet_renumbered` says so: subject the content, with the fix
`set views/iso/sheet 2`. An empty sheet is never drawn. Refusing instead would make a spec's
validity depend on what the planner moves, which `edit` cannot know.

#### 3.4 The hole table

Today `hole_table::escalate` makes a table only after hole items are dropped on a plan view with
at least `MIN_HOLES` (16) holes outside a pattern. If `place_table` finds no place for it, or not
every row's balloon lands, the table is withdrawn whole (`table_dropped`) and the replaced
callouts and locations are restored. The corpus has no part that keeps a table: CTC-02's is
withdrawn for want of balloon room, even on A0 (`tests/hole_tables.rs`).

The design adds one parameter, `escalate(analysis, plan, table_sheet: Option<u32>)`:

- `None` is today's behaviour, unchanged.
- `Some(n)` (n > 1) runs the same escalation: rows, tags, the replacement of the plan view's
  callouts and locations (`Covered { by: "hole_table" }`), and the balloons on the plan view on
  sheet 1. Only `place_table` is skipped: the table gets `sheet: n` and empty `blocks`, and the
  balloons' fixed obstacles leave it out (it is not on sheet 1). The balloons must all land, as
  today. If they do not, the table is withdrawn exactly as today (`table_dropped`, callouts
  restored), because moving the table does not create balloon room.
- `plan_sheet` then places the table on its own sheet n, towards the top right in up to four
  blocks, as `place_table` does, clear of the title block. A table that does not fit even there
  is withdrawn as today, with the `table_dropped` reason "it has no place even on a sheet of its
  own".

**A long table** is one that `escalate(…, None)` withdrew because `place_table` found no place.
`escalate` returns which outcome happened (`TableOutcome::{Kept, NoPlace, Balloons, None}`),
so the allocation reads neither message text nor the plan. The `table_dropped` problem itself is
unchanged, so single-sheet plan bytes are too. Only `NoPlace` makes the table a candidate.

**`sheets.hole_table = n`** passes `Some(n)` to every step's `escalate`. When the plan makes no
table (fewer than 16 loose holes, or no hole item dropped), the assignment does nothing: no table
is forced, the plan is single-sheet if nothing else moves, and an info problem
`sheet_assignment_unused` (subject `sheets/hole_table`, fix `remove sheets/hole_table`) says so.
Forcing a table would be new escalation behaviour that the policy does not ask for.

#### 3.5 Checks run per sheet

Collision and off-sheet audits (`dims::placement`, `dims::audit`, `dims::gdt`,
`hole_table::escalate`) compare only items on the same sheet, against that sheet's page, title
block and tables. Sheet-1 items are audited as today. Sheets 2..m hold only view geometry, the
labels and cross-references of their views, and tables. The new audit there is
`plan_sheet`'s own: off-sheet, overlap with the title block, and overlap between blocks.

#### 3.6 GD&T and dimensions in Stage I: assertions, not allocation rules

Because the carriers are the principal views (see the interpretations above), Stage I does not
filter candidates by the datum rule and does not restrict carriers for `gdt_on_sheet_1`.
Instead:

- `sheets::check` runs the datum rule over the finished plan generically: for each frame, every
  datum letter it references must have a datum symbol on sheet 1 or on the frame's own sheet.
  It is written against `sheet` fields, not view names, so it needs no change when carriers
  widen. In Stage I it never fires.
- `sheets::check` also asserts, with `debug_assert!` and a test, that every dimension, frame and
  datum symbol is carried by a sheet-1 view. A failure is a planner defect.
- `gdt_on_sheet_1` is accepted and stored. In Stage I it is met by construction, and the test
  checks this on the AP242 fixtures.

### 4. The title block on every sheet

Every sheet draws the full title block (`title_block::cells`) with the same title values.
`format` is the shared size. The projection symbol and the general notes are drawn on sheet 1
only. When m > 1:

- the `SHEET` cell holds ISO 7200's `n/m` (maintainer, 2026-10-13: the policy's "SHEET n OF m"
  is read as this form). `1/2` is about 3.95 mm at 3 mm, so it fits today's cell (6 % of the block:
  7.2 mm on A4, 9.0 mm on A3 and larger), and the title-block grid does not change for any
  document. A value that would not fit (`10/12` and up on A4) is reported by the existing
  `title_field_overflow` warning and drawn whole;
- a spec `sheet.title.sheet` value is replaced by `n/m`, with a `sheet_label_replaced` info
  problem. Python's custom page labels are not carried.

### 5. Grid zones and cross-references

- **Zones (ISO 5457):** in a multi-sheet document only, each sheet's border is divided into an
  even number of fields of about 50 mm: A4 6×4, A3 8×6, A2 12×8, A1 16×12, A0 24×16. Numbers run
  left to right and letters run top to bottom (no I or O). Ticks and labels are drawn in the
  10 mm margin outside the border, so the drawable area and every layout result are unchanged.
  A point's zone is `<letter><number>` (`sheets::zone(page, p)`).
- **Cross-references:** they are needed only when a section is on a different sheet from the view
  that marks it (and, in Stage J, a detail view).
  - On the marking view, beside the cutting-plane letter, the text reads `SECTION A–A, SHEET 2,
    ZONE C4`, the zone being that of the section's label.
  - Under the section's label, the text reads `FROM SHEET 1, ZONE B3`, the zone being that of
    the cutting-plane letter.
  - Both are `CrossRef { sheet, zone, at }` on the `PlannedView` and the `Cut`. They are placed
    and collision-checked like any label, and drawn as text runs so that lint and PDF search can
    read them. A view on the same sheet is drawn as today.

### 6. Rendering, lint and review

- `model::render` keeps its signature and draws sheet 1. `model::render_sheets` (new) returns
  `Vec<Drawing>`, one per sheet. Every `Drawing` embeds the whole spec (R1 invariant 8).
- **SVG:** one file per sheet. The sheet-1 file keeps its name; sheet n is `<stem>.sheet<n>.svg`.
  `api::rendered` adds the entries `sheet<n>.svg`, and `api::write` writes them like any format.
- **PDF:** one file, one page per sheet (`Drawing::pdf_pages(&[Drawing], fonts)`). With one
  sheet it is `pdf()`, byte for byte.
- **DXF:** none. DXF export is disabled until it is fixed (draftwright-rust PR #7); when it
  returns, it gets one file per sheet.
- **PNG:** one per sheet, `<stem>.sheet<n>.png`. `view_sheet` and `render` take `sheet`
  (default 1).
- **Lint across sheets.** `lint::lint(analysis, &Drawing)` stays for one drawing.
  `lint::lint_sheets(analysis, &[Drawing])` (new) runs the coverage rules (`lint::check`) **once,
  over the marks of every sheet together**: a feature is covered if any sheet covers it. So a
  hole whose callout is replaced by a table row on sheet 2 is covered by that row and its balloon
  on sheet 1, as it is today with the table on sheet 1. Marks need no sheet field: view names
  are unique across sheets. The structural checks (`lint::structural::check`), which compare
  positions, run **per sheet**, and each issue carries the sheet number (omitted when 1). With one
  drawing, `lint_sheets` is `lint`, issue for issue.
- **Review.** `api::reviewed` returns sheet 1's SVG as `svg` (unchanged), plus `sheets: [svg…]`
  for sheets 2..m (omitted when empty), `lint` from `lint_sheets`, and `structural` from every
  sheet.
- **The CI quality baseline** (`tools/quality.py`, `tools/quality_gate.py`: 30 parts, default
  spec) plans with `sheets.max` absent, so its output is single-sheet and unchanged by
  construction. Stage I does not add multi-sheet rows to it. `tools/quality.py` reads plan
  problems, which are plan-wide, so a multi-sheet plan run through it would be measured whole.
  Multi-sheet quality is held instead by `tests/sheets.rs`: on every allocation case, no
  `annotation_off_sheet`, `views_overlap` or `view_on_title_block` on any sheet, and the
  `lint_sheets` coverage issues no more than the same part's single-sheet plan.

### 7. Checks (new problem codes, `src/sheets.rs::check`)

| Code | Severity | Subjects / refs | Fix |
| --- | --- | --- | --- |
| `datum_not_identified` | error | the frame / `ref` the missing letter | Stage I: never raised (3.6). Stage J: assign the datum's view to the frame's sheet, or the frame's item to sheet 1 |
| `cross_reference_missing` | error | the section, its marking view | none (the planner always writes it; this guards a drawing that lost it). Also a lint structural check over each sheet's drawn text |
| `sheet_below_smallest_scale` | warning | `sheet` or `sheet:n`, ref the views below the floor | see below |
| `sheet_renumbered` | info | the assigned content | assign it to the sheet it landed on |
| `sheet_assignment_unused` | info | `sheets/hole_table` | `remove sheets/hole_table` |
| `sheet_label_replaced` | info | `sheet`, ref `title_block/sheet` | `remove sheet/title/sheet` |

`gdt_not_on_sheet_1` (a frame no sheet-1 view can carry) is not defined in Stage I, because it
cannot happen there. It comes with Stage J.

**The fix for `sheet_below_smallest_scale`** names what would actually help, from what the
search recorded:

- **sheet 1, a further step was blocked by `sheets.max`:** `set sheets/max <m+1>` (when m < 9);
- **sheet 1, every candidate already moved (or none exists), so the principal views alone are
  below the floor:** `set sheet/size <next larger>` when `sheet.size` fixes the paper and it is not
  A0; otherwise (the ladder already reached its largest paper) `set sheets/smallest_scale
  <the scale reached>`. More sheets cannot help, because principal views never move;
- **sheet n > 1, a view shrunk to fit:** `set sheet/size <next larger>` when the paper is fixed
  and not A0, else `set sheets/smallest_scale <the scale reached>`.

The problem is also raised on a single-sheet plan that sets `smallest_scale` and whose ladder
found nothing at or above it. Its fix is then `set sheets/max 2` when a candidate exists,
otherwise as above.

### 8. MCP, sessions and the library

- **Sheet-qualified addresses:** `sheet:<n>/<id>` is accepted anywhere an item ID is
  (`get_item`, `item_options`, overlays and `list_items` `near`). If the item is on another
  sheet, the request is refused with "`<id>` is on sheet 2", and the choices include the correct
  address. Bare IDs keep working: IDs are unique across sheets.
- **Move view to sheet n:** this is the edit `{"op": "set", "path": "views/<name>/sheet",
  "value": n}`. RFC 6902 already has a `move` op with other meaning, so no new op kind is added.
  `item_options` for a view lists it as the operation `move_to_sheet`, with choices `1..max` and
  the keep-together and `relative_to` consequences.
- **Results:** `list_items`, `get_item` and delta items carry `sheet`. It is omitted when the plan
  has one sheet, which keeps today's MCP baselines and the 4096-byte pages unchanged; the tool
  descriptions say "absent means sheet 1". `list_items` takes a `filter.sheet`. `inspect_part`'s
  summary gains `sheets: m` and a per-sheet size and scale when m > 1. The analysis itself has no
  sheets. The schemas under `schemas/mcp/` are regenerated with
  `tools/generate_mcp_schemas.py`.
- **Deltas and replay state.** Ink stays in page coordinates, and the sheet travels beside it:
  - `PlanDelta.collections` gains `sheets`. A view that moves lists the view and each of its
    items as `changed`, because their `sheet` field changed. `removed` and `added` are
    unaffected. No new delta kind.
  - `RenderedItem` (the ink in `Delta.annotations` and `State.annotations`) holds its
    `items::Item`s, which carry `sheet` (section 2), so it needs no field of its own.
  - `GeometryLayer` entries are keyed by view ID, and the view's `sheet` is in the plan, so
    geometry needs no field of its own.
  - `State::render` and `Session::svg_fragments` draw sheet 1 by default. They take a sheet
    number, and filter ink and geometry to it.
- **Planner budgets.** The allocation runs *inside* `model::plan_recovered`, so one planner
  invocation is still one, whatever it does inside: `Delta.plans`, `Stats.plans`, the corrective
  planner's "one candidate preview" (`src/mcp.rs`, `CORRECTION_ATTEMPTS = 8` discovery steps,
  one preview) and `MAX_OPS` keep their meaning and their numbers. What grows is the time of one
  invocation, and only for a spec with `sheets.max` > 1 whose step 0 is not clean: at most 28
  `plan_escalated` calls instead of 7 (3.2). Stage I adds one multi-sheet case to the MCP latency
  tests (the CI latency environment, Stage H) to measure it. If that case misses the existing
  latency bound, the step count is what gets reduced, not the budgets that are raised.
  `Stats` gains `sheet_steps` (sheet-1 runs, summed), so the cost is visible without changing
  `plans`.

### 9. Migration

A spec without `sheets` and without any `sheet` assignment plans, renders and serialises **byte
for byte** as before. Each new field is `skip_serializing_if` its single-sheet value. Step 0 of
the allocation is today's code path, and `floor = None` leaves `recover` unchanged. Zones and
cross-references exist only when m > 1, and `pdf()` is unchanged. The plan schema stays
`draftwright.plan/1`. Old plans read back, because every new field is `#[serde(default)]`.

### 10. The editor meanwhile

**Base branch: `cxg-int/ed`** in `draftwright-editor`, the current integration line (at
465f928, "Re-pin draftwright to fd08c1d (Stage G)"). It pins draftwright by git revision in the
workspace `Cargo.toml` (`draftwright = { git = "https://github.com/pzfreo/draftwright", rev
= "…" }`), has no `vendor/` directory, and has `crates/engine/tests/session.rs` and
`web/smoke.cjs`. The editor's `main` (which vendors alpha.1 under `vendor/draftwright` and has
no `session.rs`) is not the base and is not changed in Stage I.

On that line the engine (`crates/engine/src/lib.rs`) drives draftwright's `Session`: it calls
`drawing().svg()` (`render_svg`), `svg_fragments`, `items` with the default filter, and
`all_options`. It makes no `dims::plan` or `dims::audit` calls of its own. The editor gets no
sheet UI in these stages. Stage I's change there:

- **re-pin** the `rev` to the draftwright-rust `main` commit that lands I1 to I3 (the same kind of
  commit as 465f928);
- in `placement_options_json`, list items with `ItemFilter { sheet: Some(1), .. }`; `render_svg`
  and `svg_fragments` already draw sheet 1 by default (section 8);
- its Paper and Scale selectors keep writing `sheet.size` and `sheet.scale`. `set_spec` diffs
  whole top-level keys, so a multi-sheet spec written elsewhere keeps its `sheets` and `sheet`
  assignments unchanged and is shown as sheet 1 only.

The maintainer redesigns the editor after these stages.

## Invariants

1. **A single-sheet spec is unchanged byte for byte:** its plan JSON, SVG, PDF and PNG.
   Guards: `tests/sheets.rs::single_sheet_documents_are_byte_identical`, together with the
   existing `tests/fixtures/known_*.json` and the CI quality baseline, unchanged.
   Since 2026-10-16 the byte guard is opt-in (`DRAFTWRIGHT_SINGLE_SHEET=1`) and its fixtures
   are no longer maintained: this invariant held for Stage I, and the drawings are now being
   improved on purpose. It returns as a gate when stabilising for a release.
2. **The principal views are on sheet 1, never split.** Guard:
   `sheets::principal_views_stay_on_sheet_1`.
3. **Every sheet has the title block, and SHEET reads `n/m` without overflow on A4 to A0 (m < 10).**
   Guard: `sheets::every_sheet_has_its_title_block`.
4. **Every frame's datums are identified on sheet 1 or on the frame's own sheet.** In Stage I
   this holds by construction (every carrier is on sheet 1). Guards: `sheets::datum_rule` (the
   generic check never fires over the corpus and the AP242 fixtures, and fires on a hand-built
   plan with a frame on sheet 2) and `sheets::carriers_are_on_sheet_1`.
5. **A section on another sheet is cross-referenced both ways with the correct zones.** Guard:
   `sheets::cross_references_both_ways`.
6. **Larger paper first, then a further sheet, before a scale below `smallest_scale`.** Guard:
   `sheets::a_further_sheet_before_a_smaller_scale`.
7. **IDs are independent of sheets.** Guard: `sheets::moving_a_view_keeps_ids`.

## Tests that prove it (`tests/sheets.rs`, new, plus the existing suites)

- **Byte identity:** every slice part and AP242 fixture, with the default spec and with each
  existing test spec, gives **plan, SVG, PDF and PNG** bytes equal to the pre-change build.
  The SHA-256 of each is recorded in `tests/fixtures/known_single_sheet.json` before any Stage I
  code lands. They are recorded **on CI Linux** (`ubuntu-latest`, the only platform this repo's
  CI runs) at the pre-change `main` commit, by a one-off run of the test in record mode
  (`DRAFTWRIGHT_RECORD_SINGLE_SHEET=1`) whose output is committed. The comparison runs on Linux
  only (`cfg(target_os = "linux")`) and prints `SKIPPED: hashes are Linux-referenced` elsewhere,
  because PNG rasterisation and float formatting are not guaranteed to match across platforms.
  The CI quality baseline (30 parts) and the pin grid are unchanged.
- **Allocation** (on a corpus part with section A–A):
  - `views/section_aa/sheet = 2` gives two sheets, with the section on sheet 2 at its parent's
    scale.
  - `keep_together [side, section_aa]` refuses that assignment.
  - `views/iso/place.relative_to = front` with `views/iso/sheet = 2` is refused.
  - `sheets.max = 2` with a `smallest_scale` above what sheet 1 reaches with every view: the
    ladder runs first (larger paper, floor-aware), then step 1 moves the isometric and step 2
    the section, and sheet 1 lands at or above the floor. The steps are cumulative, and each is
    in the basis.
  - The same spec without `max` reports `sheet_below_smallest_scale` with the fix
    `set sheets/max 2`. With `sheet.size` fixed and principal views alone below the floor, the
    fix is `set sheet/size`, not `sheets/max`.
  - `views/iso/sheet = 3` with nothing else moved gives two sheets and `sheet_renumbered`.
  - A step that cannot reach the floor leaves the plan at step 0 (no extra sheet) when no step
    is better.
- **Hole table:**
  - On `tests/fixtures/parts/scattered_holes.step` with the stand-in drop of
    `tests/hole_tables.rs`, `escalate(…, Some(2))` puts the table on sheet 2 (empty blocks on
    sheet 1, placed whole on sheet 2), keeps the callouts `Covered` and lands 18 balloons on the
    plan view on sheet 1. `lint_sheets` credits every hole.
  - The same plate with the default spec and `sheets.hole_table = 2` makes no table (nothing is
    dropped). It reports `sheet_assignment_unused` and is otherwise the unassigned plan.
  - CTC-02 with `sheets.hole_table = 2` still withdraws its table with the `balloons` reason.
- **GD&T (assertions):** on NIST FTC-07 and CTC-01 (AP242), a two-sheet plan (isometric and
  section moved) has every frame and datum symbol on sheet 1, no `datum_not_identified`, and
  `gdt_on_sheet_1` met. A hand-built plan with a frame moved to `sheet: 2` and its datum on
  sheet 1 passes; one with the datum symbol also on sheet 2 and the frame on sheet 3 gives
  `datum_not_identified`.
- **Title block and zones:** each sheet's `SHEET` cell reads `n/m`, and the title-block grid is
  unchanged. Zones are 6×4 on A4 up to 24×16 on A0. `sheets::zone`
  maps known points. The cross-reference texts appear in the drawn text of both sheets.
- **Corner tables:** a table assigned to sheet 2 is placed towards its corner there, clear of
  the title block and the views.
- **Formats:** the SVG count is m, the PDF page count is m, the PNG count is
  m, and each file's embedded spec round-trips through `draftwright spec`.
- **Lint and review:** `lint_sheets` over one drawing equals `lint`. `review` of a two-sheet plan
  returns both SVGs and per-sheet structural issues.
- **MCP:**
  - A qualified address is accepted on its own sheet and refused with the correct address on
    another.
  - The move op is listed in `item_options`, applied by `apply_ops`, and shown in the delta as
    the view and its items `changed`. The moved ink's items carry `sheet: 2`.
  - `list_items` `filter.sheet` works.
  - The schema check (`tools/generate_mcp_schemas.py` and the jsonschema validation) passes.
  - The scripted MCP evaluation stays at 7/7, and the multi-sheet latency case is measured.
- **Editor:** on `cxg-int/ed` re-pinned, `cargo build`, `crates/engine/tests/session.rs` and
  `web/smoke.cjs` pass, and the editor shows sheet 1 of a two-sheet spec and keeps the spec's
  `sheets` fields through a round trip.

## Build plan: Stage I

Every stream runs on its own branch from `main` and keeps invariant 1. Stream I1 lands first; I2
and I3 build on it and can run in parallel; I4 follows I1 to I3 on the editor. Detail views are
**not** in Stage I (Stage J, below).

| Stream | Changes | Acceptance |
| --- | --- | --- |
| **I1: model and allocation** | `known_single_sheet.json` recorded first (CI Linux). `src/model.rs` (`Spec.sheets`, `sheet` on `ViewSpec` and `TableSpec`; `Plan.sheets`; `PlannedView.sheet`, `scale_note`, `xref`; `recover`'s floor; edit validation), `src/layout.rs` (`recover`'s floor, `plan_sheet`), `src/sheets.rs` (new: the search, packing, numbering, `zone`, `check`), `src/hole_table.rs` (`table_sheet`, `TableOutcome`), `src/items.rs` (`Item.sheet`), `src/dims/{placement,mod,gdt}.rs` (per-sheet audits), `src/tables.rs` (placement on sheets 2..m), `docs/model.md` | `known_single_sheet.json` unchanged; the allocation, hole-table, GD&T-assertion and check tests above pass; the full corpus suite, fmt and strict clippy pass |
| **I2: sheet furniture, rendering, lint** | `src/title_block.rs` (`n/m` in the SHEET cell), `src/model.rs` render (`render_sheets`, border zones, cross-reference text), `src/section.rs` (cross-reference under the label), `src/output.rs` (`pdf_pages`), `src/png.rs`, `src/api.rs` (sheet files, `write`, `review`), `src/lint/mod.rs` (`lint_sheets`), `src/lint/structural.rs` (`cross_reference_missing`, per-sheet) | Title block, zone, cross-reference, format and lint tests pass; single-sheet bytes unchanged; a two-sheet PDF opens with 2 pages, and its text search finds both cross-references |
| **I3: MCP and sessions** | `src/session.rs` (delta `sheets`, `filter.sheet`, qualified addresses, `svg_fragments(sheet)`, `Stats.sheet_steps`), `src/stateless.rs` (`render(sheet)`, summary), `src/mcp.rs`, `schemas/mcp/*.json`, `tools/generate_mcp_schemas.py`, `docs/mcp.md`, the latency test | The MCP tests above pass; existing MCP baselines and the 4096-byte budgets unchanged for single-sheet parts; the scripted evaluation stays 7/7 |
| **I4: editor compatibility** | In `draftwright-editor`, branch from `cxg-int/ed`: re-pin the draftwright `rev` in `Cargo.toml` to the commit landing I1 to I3; `crates/engine/src/lib.rs` (sheet-1 item filter). No UI change | Editor build, `crates/engine/tests/session.rs` and `web/smoke.cjs` pass; a two-sheet spec shows sheet 1 and survives a round trip unchanged |

## Stage J: detail views of dense regions (its own stage, after Stage I)

Detail views are new to the port, and they make GD&T and dimension placement off sheet 1 real.
So they are a stage of their own, with this design to be confirmed before it starts:

- **The region.** The demand findings that today force a larger scale or a drop at the floor
  (`src/demand.rs`: hole-location spacing, `layout::crowded_locations`, step legibility, and
  `location_ref_dropped` / `annotation_off_sheet` drops) name features. The region is the
  page-space bounding box of the features those findings name on one principal view, grown by
  a margin of 2× `layout::MIN_LOC_SEP`, and drawn as its circumscribed circle. One region per
  plan in Stage J; a second dense region falls back to the floor warning.
- **What moves.** Every dimension, frame and datum symbol on that view whose features *all* lie
  inside the circle moves into the detail. An item with a feature outside stays on the parent.
  The parent keeps the circle and the label `B`, the items outside it, and its overall sizes.
- **Carriers.** A detail is a new carrier kind: `dims::plan` and `dims::gdt` accept `detail_b`
  with the parent's projection (`Frame` of the parent, scaled by the detail's factor and
  translated). `DimensionSpec.view` and `place.view` then accept `detail_b`. This is also when
  `dimensions/<id>/sheet` and `annotations/<id>/sheet` arrive, and when `datum_not_identified`
  and `gdt_not_on_sheet_1` can fire. The datum rule becomes an allocation rule then: a detail
  is not made if moving a frame into it would leave a referenced datum unidentified on sheet 1
  and on the detail's sheet, unless a datum symbol moves with it.
- **Line work.** No new hidden-line run: the parent's projected curves are reused and clipped in
  2D to the circle. The boundary is a thin continuous line. The detail is labelled
  `DETAIL B` and `SCALE n:1`, with cross-references both ways as for sections.
- **Allocation.** The detail is a fourth candidate after the long table. It is packed like any
  moved view.
- **Acceptance.** On a part whose plan today goes below a set `smallest_scale`, or drops items,
  because of one dense region, the plan instead keeps sheet 1 at the floor and puts the detail on
  sheet 2, enlarged and cross-referenced. Every item that was reported dropped, or counted
  crowded, inside the region at the floor is placed on the detail, and `lint_sheets` credits it.

## Decided at sign-off (maintainer, 2026-10-13)

- The technical design is accepted as the Stage I plan.
- Sections stay on sheet 1 unless sheet 1 overflows ("the next sheet with room" read as above).
- The SHEET cell reads ISO 7200's `n/m`; the title-block grid does not change (section 4).
- DXF is out of Stage I while its export is disabled.

## Maintainer decision (2026-10-14)

With `sheets.max > 1`, sheet 1's legibility floor applies even when
`sheets.smallest_scale` is absent. The absent explicit floor does not make an
illegible sheet 1 a clean allocation candidate. Single-sheet defaults stay unchanged.

## Open

- **Stage J's region rule** (one region, the margin, items wholly inside) is a first design, to
  be confirmed before that stage starts.
