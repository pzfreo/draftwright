---
name: draftwright-build123d
description: Produce drawings from build123d parts and assemblies with the draftwright Python API (Part, Drawing, Pack), fit for the purpose the person states (review, manufacture, inspection, assembly). Use when a build123d script needs a drawing, requirements the person gave (fits, threads, GD&T, finish) stated on its faces, or an assembly drawing pack.
---

# Drawings from build123d with draftwright

You turn a build123d model into a drawing fit for its purpose: a review drawing needs
geometry; a manufacturing drawing also needs the requirements the person gives. The engine
chooses views, scale and dimensions; you state those requirements on the faces you
modelled, render, read the problems, and look at every sheet. Covers the draftwright
Python package **0.6.0 and later** (checked on 0.6.0, the newest on PyPI when written). The person
guide and the full reference (not installed) are
https://github.com/pzfreo/draftwright/blob/v0.6.1/docs/build123d.md and
https://github.com/pzfreo/draftwright/blob/v0.6.1/docs/python.md. Spec ids, paths, sections,
breaks and authored dimensions are as in the `draftwright-step` skill.

## Ground rules

- **Know what the drawing is for.** If the brief states the purpose (manufacture,
  inspection, review or communication, assembly, other), use it and don't ask again. If
  not, and nobody can answer (unattended), draw geometry only with material and general
  tolerance `UNSPECIFIED` and list the open questions; otherwise ask first. A review drawing
  needs geometry, not GD&T. State the purpose in your report (no Spec field for it yet).
- **Write the script a person would write.** One line per requirement, on the
  objects the model already has. No wrapper layers, sidecar files, STEP-name parsing
  or helper frameworks to make output look complete. If the API cannot state
  something, leave the gap visible and report it as an issue.
- **Requirements go on the `Part`; layout goes on the `Drawing`.** Never put
  material, fits, threads, GD&T, finish or notes into `Drawing.update()` or
  `render(spec=...)`; they are refused there.
- **Add only the requirements the purpose needs, and never invent them.** State only the
  material, tolerances, fits, threads and finish the person gave you or the design states;
  when the purpose needs one you don't have, ask. Where none was given, say so:
  `p.material("UNSPECIFIED")`, `p.general_tolerance("UNSPECIFIED")`. Never choose a general
  tolerance yourself to make the drawing look complete.
- **Never override a dimension's value.** `text` may add a prefix or suffix only.
- **A sheet is not done until you have looked at it.** The problem list misses
  things (see "Check by eye").

## Setup

```sh
uv run --no-project --python 3.12 \
  --with "draftwright>=0.6.0" --with "build123d>=0.13" python script.py
```

- build123d 0.10 or later. Unpinned, uv can resolve 0.10 when another package
  (bd-warehouse) caps it; then also add `--with "ocp-gordon<0.3"`.
- Import draftwright under its own name. `from build123d import *` defines a `Part`
  too, and `from draftwright import Part` silently replaces it:
  ```python
  from build123d import *
  import draftwright as dw
  ```
- Do not name your script after a standard-library module (`bisect.py`,
  `random.py`): build123d imports those and gets your file instead.

## Model for drawing

- Have the builder return the body **and the named tool solids** that made each
  feature (the cylinder that cut each hole, each turned step), for example in a
  small dataclass. Requirements then read like the design intent:
  `p.hole(f.crossbore).fit("H7")`.
- For faces with no named solid, filter and sort, then **print the list once** to
  confirm the index:
  `body.faces().filter_by(Axis.X).sort_by(Axis.X)`. On trimmed cylinders
  `SortBy.RADIUS` fails; sort by `SortBy.AREA`.
- Model holes and threads as plain machined geometry (the tap drill, the major
  diameter). Do not model thread helices; state the thread with `thread()`.
- One `Part` is one solid. Two disjoint solids raise "Part requires one product".
- Watch for features that close each other: a boss that covers a tapped hole's
  mouth makes that hole's PMI undrawable, and the error names PMI, not the boss.

## State requirements

Only what the purpose needs and the person stated; every value below stands for one the
person gave you. Unknown: `UNSPECIFIED`, and list it as open.

```python
p = dw.Part(body, "bracket", number="DWG-210", title="Motor bracket")
p.material(MATERIAL_THE_PERSON_GAVE)          # else p.material("UNSPECIFIED")
p.general_tolerance(TOLERANCE_THE_PERSON_GAVE) # else p.general_tolerance("UNSPECIFIED")
p.note(NOTE_THE_PERSON_GAVE)                  # e.g. their edge-break note

A = p.datum("A", bottom_face)                 # declare datums before use
bearing = p.hole(f.bore).fit("H7").position(0.05, A, B, C)
p.holes(f.mounts).thread("M6", depth=12)      # minimum full-thread depth
p.distance(left_end, right_end).tolerance(plus_minus=0.1)
p.face(top_face).flatness(0.05)
```

- `hole()` takes one hole, `holes()` a group. Calls chain and return a handle
  (`bearing`); keep it for sections, callout moves and more controls.
- Repeating a call on the same faces replaces it.
- Mistakes raise at the call. Read the message: it names the measured size, the
  datum, or the right call.

Forbidden arguments: `teeth=` on `knurl()` (derived from diameter and pitch),
`depth=` on an external thread, `min=` on `thread()`.

## Render and check

```python
dwg = p.drawing(sheet="A3", scale=1)
svg, pdf = dwg.render("bracket", formats=("svg", "pdf"))
for f in dwg.problems:
    if f["tier"] != "information":
        print(f["tier"], f["code"], f["message"], f.get("subjects"))
```

- A problem's `fix` is written in Spec paths. A `sheet/title/material`,
  `sheet/title/tolerance` or `notes` fix is a requirement: make it with the `Part` call
  (`update()` refuses it and names the call). Layout fixes go through `dwg.update()`.
- `dwg.problems` exists only after `render()`, and any later Part or layout edit
  clears it until you render again.
- Act on every `fix_before_printing`. Read `tidy` (leader crossings, crowded
  labels) and fix what a reader would trip over. `information` explains choices,
  such as why the scale changed.
- If the engine drops the scale and leaves the sheet sparse, find what pushed it
  (often one long leader, such as a single face finish) before forcing `scale=`;
  forcing the scale can create overlaps. Re-check `problems` after any forcing.

### Layout: title, views, breaks, units, added dimensions

```python
dwg = p.drawing(sheet="A4", scale=1, units="in")       # units "mm" or "in"
dwg.title(drawn_by="PF", date="2026-11-18", revision="A")   # not material/tolerance
dwg.views(omit=("iso",))
dwg.update({"views": {"front": {"break": {}}, "plan": {"break": {}}}})   # long bars
ends = body.faces().filter_by(Axis.X).sort_by(Axis.X)
dwg.update({"dimensions": {"d:authored:overall": {
    "between": [p.reference_key(ends[0]), p.reference_key(ends[-1])],
    "along": "x", "view": "front"}}})
```

- `update()` validates and raises with the accepted fields; read the message.
- A model in mm whose sizes are round inches (25.4, 50.8 …) was probably meant in inches:
  consider `units="in"` and say why.

### Moving a callout

Ids are in each problem's `subjects`. A hole callout is
`"d:" + p.feature_id(handle) + "/bore.diameter"` (note the `d:` prefix):

```python
cid = "d:" + p.feature_id(bearing) + "/bore.diameter"
dwg.update({"dimensions": {cid: {"place": {"view": "plan", "side": "below"}}}})
```

Prefer `side` (the engine still finds a clear spot) over a pinned `offset`, which
is never moved off other ink. Sections take a handle: `dwg.section("A-A",
through=bearing)`; a raw counterbore object may not resolve to one feature.

## Assemblies

```python
pack = dw.Pack(assembly, parts=[base, lid], title="Enclosure", number="ASM-100")
pdf, csv = pack.render("enclosure", formats=("pdf", "csv"))   # a file stem, not a directory
for f in pack.findings:
    print(f.get("part", "GA"), f["severity"], f["message"])
```

- Each assembly leaf's `label` must equal its `Part.name` exactly, or that part is
  drawn from geometry without its requirements.
- `pack.findings` includes every part sheet's problems and every failed row
  (`severity` `error`). Also check `pack.rows` for `error`.
- The GA keeps the model's orientation. Pass `horizontal_axis="x"|"y"|"z"` only
  when the person asks for a different one.

## Known limits

No auxiliary views or dimensions to an inclined axis; no datum on an inclined face; a section
view carries no dimensions of its own; PMI stays in memory (`export_step` writes plain
geometry). Tell the person what could not be stated rather than working around it.

## Check by eye

Open the PDF and look at every sheet. Known blind spots, where nothing is reported:

- Features with no size or location, notably open slots (#503, #500).
- Angle labels touching the part edge (#505).
- A dimension drawn through a section arrowhead (#498).
- Every thread, fit, knurl, finish and note you stated actually appears.

A clean problem list does not mean ready to make: until checks follow the purpose, check
the title block yourself for missing material, tolerance and finish. A corner cut is stated by
its start point and its acute angle (25°, not 155°); `C` is only for small 45° chamfers. Check
a cut that slopes on two axes against the model: each view states the angle of the face it
shows square-on. To state one yourself, author an `angle` dimension between two coplanar edges
plus the start distance.

Done means: fit for its stated purpose, no `fix_before_printing` left unexplained, no
row errors, page count matches the made parts plus the GA for a pack, every stated
requirement found on its sheet, and a visual pass over each sheet. Finish with a short
report: the purpose, the files, what you changed from the automatic drawing, assumptions
to confirm, and what is still open (requirements the purpose needs but were not given;
remaining problems; tool limits hit).
