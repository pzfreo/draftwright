---
name: draftwright-step
description: Produce drawings from any STEP file with the draftwright CLI or MCP server, either a drawing from geometry alone or a manufacturing drawing whose requirements are AP242 PMI written with specify-core, with layout in the drawing Spec. Use when someone has a STEP part or assembly (from any CAD tool) and wants a drawing, a drawing pack, or changes to an existing drawing's requirements or layout.
---

# Drawings from STEP files with draftwright

Covers **draftwright 0.6.0** and **specify-core 0.1.18**. Everything below works on those
releases; anything newer is marked "after 0.6.0". Over MCP this file is the resource
`draftwright://skill/draftwright-step`.

## Rules (always)

1. **Requirements live in the STEP as PMI, written with `specify-core`; layout and judgement
   live in the Spec.** Material, fits, threads, GD&T, datums and finish never go in the Spec.
2. **Never invent a requirement.** No material, finish, fit, tolerance or heat treatment the
   person did not give you. Leave it open and say so (`UNSPECIFIED`).
3. **A general tolerance you choose is an assumption.** Prefer `UNSPECIFIED`; if you do write
   one (e.g. ISO 2768-m), list it as an assumption for the person to confirm.
4. **Never override a dimension's value.** `text` may add a prefix or suffix, nothing else.
5. **Specify defaults are written only when the person has seen and approved them.** Never use
   `--accept-defaults` on your own.
6. **Incomplete is normal; silent is not.** Finish with a report of assumptions and open items.
7. **Look at the sheet.** The problem list has blind spots.

## Two routes; say which you took

| Route | When | Steps |
| --- | --- | --- |
| Geometry only | no requirements given, a quick drawing, a review | the loop; title block says `UNSPECIFIED` where unknown |
| Requirements | the person gives material, tolerance, finish, fits, datums | Specify writes PMI into a new STEP, then the loop on that file |

PMI already in the STEP (from its CAD tool) is drawn on either route; do not restate it.
If the person is not there to answer Specify's questions, take the geometry route and list
the questions in your report. Do not answer them for the person.

## Setup and versions

```sh
pipx install draftwright==0.6.0          # or: uvx draftwright@0.6.0 ...
pipx install specify-core==0.1.18        # only for the requirements route
draftwright --version                    # draftwright 0.6.0 (quiddity-rust 0.5.4)
pipx list --short                        # specify-core has no --version flag
```

Output is JSON on stdout. **On 0.6.0 a refusal is also JSON on stdout**,
`{"ok": false, "error": {"code", "message", "choices"?}}`, exit 2 (invalid) or 1 (failed).
Never redirect over your only copy, and print the error when a command fails:

```sh
draftwright edit spec.json ops.json > next.json || cat next.json
```

(After 0.6.0 refusals move to a plain `error:` line on stderr.) `render -o DIR/stem` needs
`DIR` to exist: `mkdir -p out` first ("cannot write …: No such file or directory").

## The working loop

```sh
mkdir -p out
draftwright parts part.step                          # an assembly? see "Assemblies"
draftwright analyse part.step > analysis.json        # declared_length_unit, references
draftwright render part.step -o out/part --formats svg,pdf,png > r.json || cat r.json
draftwright spec out/part.pdf > spec.json            # the Spec embedded in the drawing
# loop: read problems -> write ops.json -> edit -> render with --spec -> open the PNG
draftwright edit spec.json ops.json > next.json || cat next.json
draftwright render part.step --spec next.json -o out/part --formats svg,pdf,png > r.json
draftwright plan part.step --spec next.json > plan.json  # ids, bases, view states; no files
```

Keep the latest Spec (`next.json` becomes `spec.json`). Each problem has `tier`, `code`,
`message`, `subjects` (ids) and often `fix`/`fixes` (ready ops; read them before applying):

- `fix_before_printing`: wrong or missing. Fix it, or report why you could not.
- `tidy`: crowded or crossing. Fix what a reader would trip over.
- `information`: context. `pmi_not_extracted` about "attribute sets" is normal after Specify.

`general_tolerance_missing` is `fix_before_printing`; its offered fix writes `UNSPECIFIED`
(rule 3). Open the PNG after every render that changes layout.

## Ids and Spec paths

Edit ops: `{"op": "set" | "remove" | "add", "path": "a/b/c", "value": ...}`. In a path, a
`/` inside an id is `~1`. `add` appends to the list the path names (`notes`, `tables`).

| What | Id / path | Note |
| --- | --- | --- |
| Views | `views/front`, `plan`, `side`, `iso`, `section_aa`, `detail_a` | the plan shows `v:front`; the Spec and `place.view` use the bare name |
| Dimensions | `dimensions/d:envelope~1width.length` | ids in `plan.json`; each `basis` names its path after `Spec:` |
| Features | `f:hole:f9`, `f:hole_pattern:f6` | `plan.json` `ids` maps ordinal aliases to these |
| Notes | `{"op":"add","path":"notes","value":{"text":"..."}}` | ids are assigned `n:1`, `n:2` (not `n1`) |
| Title block | `sheet/title/<key>` | `title`, `number`, `material`, `tolerance`, `drawn_by`, `approved_by`, `date`, `revision`, `company`, `document_type` |
| Sheet | `sheet/size` (`A4`…`A0`, `ANSI A`…`E`), `sheet/scale` (`"1:1"`, `"2:1"`), `sheet/paper` (`iso`/`ansi`), `sheet/projection` (`first`/`third`) | size and paper must be the same family |
| Units | `units` (`mm` or `in`) | or `render --units in` |

List the dimensions (id, text, view, state):

```sh
python3 -c "import json;[print(d['id'],d['label'],d.get('view'),d['state']['kind']) for d in json.load(open('plan.json'))['dimensions']]"
```

Full schema: `draftwright spec_schema` prints `{"spec": {...}, "spec_read": {...}}`; fields are
in `.spec.properties`, shapes in `.spec.$defs` (`view`, `break`, `dimension`, `place`, `sheet`).
Prose reference (not installed): https://github.com/pzfreo/draftwright/blob/v0.6.0/docs/model.md

Render flags on 0.6.0: `--title --number --material --tolerance --drawn-by --revision --date
--company --approved-by --units --paper --part --formats --dpi`, and `--scale` as a number
(`1`, `0.5`, `2`). `--size`, `--max-size` and `--set PATH=VALUE` are after 0.6.0; on 0.6.0
put sheet size and everything else in the Spec.

## Common edits (ops.json)

```json
[ {"op":"set","path":"sheet/title/title","value":"MOUNTING PLATE"},
  {"op":"set","path":"sheet/title/number","value":"MP-001"},
  {"op":"set","path":"sheet/title/material","value":"UNSPECIFIED"},
  {"op":"set","path":"sheet/title/tolerance","value":"UNSPECIFIED"},
  {"op":"set","path":"sheet/size","value":"A3"},
  {"op":"set","path":"sheet/scale","value":"1:1"},
  {"op":"set","path":"views/iso/omit","value":true},
  {"op":"set","path":"dimensions/d:envelope~1width.length/omit","value":true},
  {"op":"set","path":"dimensions/d:envelope~1height.length/place","value":{"view":"side","side":"left"}},
  {"op":"set","path":"dimensions/d:envelope~1depth.length/style","value":"reference"},
  {"op":"add","path":"notes","value":{"text":"MATERIAL AND FINISH NOT SPECIFIED - CONFIRM BEFORE MANUFACTURE."}} ]
```

- `place`: `view`, `side` (`above`/`below`/`left`/`right`), `lane`, `along`, or
  `{"pinned": true, "offset": [dx, dy]}` in paper mm. Prefer `side`; re-check after pinning.
- Remove a duplicate with `omit`; keep a useful duplicate as `style: reference`.
- Forcing size or scale can create overlaps; when the engine chose a smaller scale, find what
  pushed it before overriding. After omitting a view, check the views in `plan.json`: one that
  a section or dimension depends on may be kept or moved.
- An edit naming an id that no longer exists is reported (`dimension_edit_unapplied`). Fix it.
- Notes carry drawing information ("BORE POSITION: TAKE FROM part.step"), not requirements.

## Sections, breaks, details

```json
[ {"op":"set","path":"views/sections","value":[{"id":"A","through":"f:hole:f9"}]},
  {"op":"set","path":"views/front/break","value":{}},
  {"op":"set","path":"views/front/break","value":{"axis":"x","remove":[60,540]}},
  {"op":"set","path":"views/detail_a","value":{"detail":{"around":"f:hole:f12","scale_factor":3}}} ]
```

- `through` names a recognised **feature id** (a hole, pocket or recess); the cut runs through
  it. Find ids in `plan.json` `ids` or in dimension ids. Section letters `A`–`H`.
- A break `{}` picks the longest uniform span; `remove` is a model-mm interval along `axis`.
  It carries to the aligned views. Start with `{}` and read the applied `remove` in
  `plan.json` (there `axis` is a view index, not x/y/z). `break_not_applied` says why a
  request was refused. Use a
  break for long bars and extrusions so the features read at a useful scale. Dimensions
  across a break keep their true values.

## Dimensions you add from geometry

`dimensions/d:authored:<name>` adds a dimension between model references. Keys come from
`analysis.json` `edge_ink.references` (`face:v1:…`, `edge:v1:…`, `vertex:v1:…`; a key with
more than one record is ambiguous and refused). `axis:face:v1:…` or `axis:f:hole:f12` names a
cylinder's axis. List the keys with their geometry:

```sh
python3 -c "
import json;r=json.load(open('analysis.json'))['edge_ink']['references']
for k,v in r.items():
  x=v[0];print(k,x['kind'],x.get('normal') or x.get('axis') or '',x.get('point') or x.get('ends') or x.get('bounds'),x['stock_sides'],x['features'],'AMBIGUOUS' if len(v)>1 else '')"
```

```json
[ {"op":"set","path":"dimensions/d:authored:edge_to_hole","value":
    {"between":["face:v1:<right face key>","axis:f:hole:f12"],"along":"x","view":"plan","place":{"side":"above"}}},
  {"op":"set","path":"dimensions/d:authored:corner","value":
    {"kind":"angle","between":["edge:v1:<key>","edge:v1:<key>"],"view":"front"}} ]
```

- `kind`: `linear` (default), `diameter`, `radius`, `angle`; `along`: `x`, `y`, `z` or `true`.
- `x`/`y`/`z` need a unique station on each reference: a plane normal to that axis, an edge or
  axis square to it, or a vertex. An angle needs two planar faces or two coplanar edges.
- A duplicate of a drawn dimension is refused, naming its owner: edit that one instead.

## Units

`analysis.json` `declared_length_unit` is what the STEP declares; a declared-inch part is drawn
in inches on ANSI paper. If a millimetre STEP has round inch sizes (50.8, 63.5, 12.7: multiples
of 25.4), it was probably designed in inches: consider `{"op":"set","path":"units","value":"in"}`
with `sheet/paper` `ansi`, and say why. A size that is round in neither unit (e.g. 11.993) is
drawn as modelled: report it, never round it by hand.

## Requirements with Specify (specify-core 0.1.18)

```sh
specify-core analyse -o sa.json part.step
specify-core questions sa.json --open > q.json      # id, prompt, options/suggestions, default, attention
specify-core questions sa.json --answers answers.json --open   # repeat until it prints []
specify-core write -o part-pmi.step part.step sa.json answers.json > w.json
draftwright render part-pmi.step -o out/part --formats svg,pdf,png
```

`answers.json` is `{question id: value}`, e.g. `{"part.material": "Aluminium 6082-T6",
"part.general_tolerance": "ISO 2768-m", "hole.function:holes:12": "tapped:M5",
"datum.B": [], "datum.A.flatness": "none", "part.notes": []}`.

- On 0.1.18 `write` refuses (exit 2, "incomplete: N question(s) unanswered") until **every**
  question is answered, and general tolerance and surface finish must be one of the listed
  values: there is no "unspecified" for them. So this route needs the person's answers for
  material, general tolerance and finish at least. Without them, stay on the geometry route
  and list the open questions.
- Answers that state nothing extra: `"none"` for position/flatness/control choices, `[]` for a
  datum or `part.notes`, `"general"` for a hole's function, `"No edge note"` for edges.
- `--accept-defaults` writes every remaining default (Ra 3.2, ISO 2768-m, coating, datums,
  positions) as PMI. Use it only after showing the person that full list and getting a yes.
- Report `w.json` `not_written`. Check that each written requirement appears on the sheet.

## When the model changes

```sh
draftwright diff old.step new.step --spec spec.json -o changes   # carries the Spec, draws, report.md
specify-core diff -o pmi-changes --answers answers.json old.step new.step
```

Read the `verdict` (`ok`, `decide`, `problems`) and every item that needs a decision.

## Assemblies

```sh
draftwright parts asm.step                         # index and key of each distinct part
draftwright render asm.step --part 1 -o out/pin    # one part (index or key)
draftwright pack asm.step -o pack                  # GA + parts list + a sheet per made part
```

A pack is clean when `pack/pack.json` findings and each row's `problems`/`error` are clear.
Bought parts are listed, not drawn. `specify-core write` takes one `analysis answers` pair per part.

## MCP (`draftwright mcp-stdio`)

Same engine, stateless. `inspect_part {step}` gives `part_ref` and `spec_version`;
`list_problems`; `item_options {part_ref, id}` for valid paths and choices; `preview_ops`, then
`apply_ops {part_ref, parent: spec_version, ops}`; `view_sheet` (PNG to look at); `render_pdf`;
`get_spec`; `spec_schema`. Read artifacts with `resources/read`. Ids, paths and ops are as above.
Save the final Spec to a file with your deliverables.

## Known limits on 0.6.0 (state them; do not work around them)

- No auxiliary views; no dimension to an inclined axis or to a point along it; no angle to a
  cylinder's axis. An inclined bore cannot be fully dimensioned: leave
  `feature_not_dimensioned` open, add a note such as "BORE LOCATION AND DIRECTION NOT
  DIMENSIONED - TAKE FROM part.step" without inventing values, and tell the person.
- No datum or face label on an inclined face ("not axis-aligned").
- A section view does not carry its own dimensions; they stay on the principal views.
- `views/iso/direction` turns a pictorial, but pictorials take no dimensions.
- MCP `render` gives PNG or PDF; SVG and DXF come from the CLI `render --formats`.
- Nothing is reported for: features with no size or location (open slots), angle labels
  touching the part, a dimension through a section arrowhead, or a requirement that did not
  reach the sheet. Check these by eye.

## Never

- Requirements in the Spec (the schema has `features/*/fit`, `controls`, `datum`, `finish`:
  use Specify instead), or layout in Specify answers.
- Hand-editing the STEP text, sidecar files, or dimension text that replaces the value.
- Calling a drawing done from the problem list alone.

## Finish

Deliver the drawing (`.pdf`, plus `.svg`/`.png` if asked), the final Spec (`spec.json`), and the
PMI STEP if you wrote one. Then a short report:

```
Route: geometry only | requirements (PMI in part-pmi.step)
Sheet: A3 1:1; views ...; changes from the automatic drawing: ...
Assumptions (please confirm): general tolerance ISO 2768-m (my choice) ...
Open: material, finish, fits not given; remaining problems by code; tool limits hit
```

Done means: no `fix_before_printing` left unexplained, every sheet looked at, and on the
requirements route every written requirement visible on the sheet.
