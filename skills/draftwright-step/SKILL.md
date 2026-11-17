---
name: draftwright-step
description: Produce drawings from any STEP file with the draftwright CLI, either a basic drawing from geometry alone or a full manufacturing drawing with requirements stated as AP242 PMI through specify-core, with layout in the drawing Spec. Use when someone has a STEP part or assembly (from any CAD tool) and wants a drawing, a drawing pack, or changes to an existing drawing's requirements or layout.
---

# Drawings from STEP files with draftwright

You turn a STEP file into a drawing. There are two routes; pick the one the person
needs, and say which you took:

- **Basic drawing** (steps 1, 2 and 4): geometry alone. The engine recognises the
  features and dimensions them; the title block and layout come from the Spec. Every
  size is drawn, but nothing says how accurate it must be beyond what the title
  block states. Right for a quick drawing, a review, or a part whose requirements
  live elsewhere.
- **Drawing with requirements** (add step 3): fits, threads, GD&T, finish and
  material are written into the STEP as AP242 PMI with Specify, then drawn. Right
  when the drawing is what the part will be made and inspected to.

If the STEP already carries PMI from its CAD tool, draftwright draws it on either
route; do not restate it.

When you add requirements, two tools hold two kinds of fact:

| Fact | Changes whether the part passes inspection? | Lives in | Tool |
| --- | --- | --- | --- |
| Requirement: material, fit, thread, position/flatness, finish, datums, making notes | yes | the STEP file, as AP242 PMI | `specify-core` |
| Layout: sheet, scale, title block, views, sections, where a dimension sits, drawing-only notes | no | the drawing Spec (`part.drawing.json`) | `draftwright` |

Never put a requirement into the Spec to save a step: it would not travel with the
model.

## Setup

```sh
pipx install draftwright                    # or: uvx draftwright ...
pipx install specify-core                   # only for requirements (step 3)
draftwright --version
```

A command's result is JSON on stdout. Read it; do not scrape the SVG. A refusal is a plain
`error:` line on stderr, with empty stdout and exit 2 (invalid) or 1 (failed).

## 1. Look before drawing

```sh
draftwright parts part.step           # one part, or an assembly's distinct parts
```

- An assembly drawn with `render` draws one part. If it reports
  `assembly_principal_drawn`, the largest part was chosen; pick another with
  `--part K`, or draw the whole assembly with `pack` (section 6).
- The engine keeps the model's orientation. It does not rotate parts to look better.

## 2. A first drawing

```sh
draftwright render part.step -o part --title "Mounting plate" --number MP-001 \
  --formats svg,pdf,png
```

The output is `{"written": [...], "problems": [...]}`. Every problem has a `tier`:

- `fix_before_printing`: wrong or ambiguous. Fix it.
- `tidy`: crowded or crossing, but readable. Fix what a reader would trip over.
- `information`: context, such as a missing requirement or why the scale changed.

On the basic route that can be the finished drawing: fix layout problems with the
Spec (step 4). Requirement gaps such as `general_tolerance_missing` are
`information`; on the basic route, state the general tolerance in the title block
(`--tolerance "ISO 2768-m"`) or leave it, as the person prefers. On the requirements
route they are answered in Specify (step 3).

Open the PNG (or PDF) and look at it. The problem list has blind spots (section 7).

## 3. State requirements with Specify (requirements route)

```sh
specify-core analyse -o analysis.json part.step
specify-core questions analysis.json --open          # what still needs an answer
```

Each question has an `id`, `prompt`, `options` or `suggestions`, a `default`, the
`faces` it concerns, and `attention` (`required`, `check`, `routine`). Answer what the
person has told you or what the design obviously needs, in a JSON file keyed by id:

```json
{ "part.material": "Aluminium 6082-T6", "hole.function:holes:22": "fit:H7" }
```

Re-run `questions --answers answers.json --open` until no `required` question is
left, then write the PMI into a new STEP file:

```sh
specify-core write -o part-pmi.step --accept-defaults part.step analysis.json answers.json
```

The output lists everything `written` and anything `not_written`; report the latter.
`--accept-defaults` takes Specify's defaults (general tolerance, finish, positions
from the fastener formula, datums) for the rest. Tell the person which you accepted;
do not invent answers to `check` questions the person would want to decide.

Draw from the written file: `draftwright render part-pmi.step ...`.

## 4. Change layout with the Spec

The Spec is embedded in every drawing. Start from it:

```sh
draftwright spec part.pdf > part.drawing.json
```

Edit with path operations (`~1` escapes `/` inside an id), then render with it:

```json
[ { "op": "set", "path": "sheet/title/drawn_by", "value": "PA" },
  { "op": "set", "path": "dimensions/d:f:hole:f22~1location.length.x/place",
    "value": { "side": "below" } } ]
```

```sh
draftwright edit part.drawing.json ops.json > next.json
draftwright render part-pmi.step --spec next.json -o part
```

- Ids are in each problem's `subjects` (`d:...` dimensions, `f:...` features,
  `v:...` views). A problem may carry `fix`/`fixes`: ready-made operations to apply.
- `place` takes `view`, `side`, `lane`, `along`, or `{"pinned": true, "offset": [dx, dy]}`
  in paper mm. Prefer `side`: the engine still finds a clear spot. A pinned label is
  never moved off other ink, so re-check problems after pinning.
- Sheet and scale: `--size A3`, `--max-size A3`, `--scale 1:1` (also numeric `1`);
  units `--units in`. Size/max-size and repeatable `--set PATH=VALUE` also work on
  review and plan. Values parse as JSON, otherwise text; paths use `/`, with `~1`
  for a slash in a key. Example: `--set views/iso/omit=true`. Flags/edits override
  `--spec`; overlapping named flags and `--set` are refused in either order.
  On pack, size/scale affect only the GA; max-size affects GA and part drawings.
  Pack edits address its Spec, e.g. `--set ga/drawing/sheet/size=A3`.
  Forcing a scale can create overlaps; when the engine drops the scale, look for what
  pushed it before overriding.
- An edit naming an id that no longer exists is kept but reported
  (`dimension_edit_unapplied`). Fix the id; do not ignore it.
- Every field: `draftwright spec_schema`, and `docs/model.md` (Placement, Spec).

## 5. When the model changes

```sh
draftwright diff old.step new.step --spec part.drawing.json -o changes
```

This carries the Spec to the new revision, draws it, and writes what changed. On the
requirements route, re-run Specify's `analyse` and `questions --answers` on the new STEP: answers are kept,
new features get new questions.

## 6. Assemblies

```sh
draftwright pack assembly.step -o pack-dir
```

One PDF (a general-assembly sheet with balloons and a parts list, then a sheet per
made part), plus CSV and JSON. Read the JSON report's findings and each row's
`problems` and `error`; a pack is not clean until both are. Bought parts (screws,
washers) are listed, not drawn. Requirements for each part go into the assembly STEP
with `specify-core write`, one `analysis answers` pair per part.

## 7. Check by eye

Open every sheet. Nothing is reported for:

- features with no size or location, notably open slots (#503, #500);
- angle labels touching the part (#505);
- a dimension drawn through a section arrowhead (#498);
- a stated requirement that did not reach the sheet: check each one you wrote.

Done means: no `fix_before_printing`, a visual pass over every sheet, and on the
requirements route also no `not_written` you have not explained and every
requirement visible on its sheet.

## Never

- Requirements in the Spec, or layout in Specify answers.
- Editing the STEP text by hand, or writing sidecar files to work around a missing
  option: report the gap as an issue.
- Declaring a drawing done from the problem list alone.
