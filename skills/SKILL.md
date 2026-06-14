# Automated Engineering Drawing with draftwright

Use this skill when asked to create an engineering drawing automatically from a
build123d solid or STEP file. It requires the **draftwright** package (AGPL-3.0)
to be installed in the execution environment.

> **License notice**: draftwright is AGPL-3.0. If you deploy code that uses it
> as part of a network service you must make your application source available.
> For Apache 2.0 annotation primitives only (`Dimension`, `Leader`, `TitleBlock`,
> etc.), use `build123d-drafting-helpers` instead without this obligation.

**There are two paths — start with the automatic one.**

1. **Automatic (`make_drawing`)** — one call turns a part (or STEP file) into a
   four-view SVG + DXF with dimensions, centrelines, and an ISO 7200 title
   block.
2. **Builder (`build_drawing`)** — the same pipeline, but it hands back a live
   `Drawing` you can edit before export.

Requires `draftwright >= 0.1.0` and `build123d-drafting-helpers >= 0.9.1`.
Install: `pip install draftwright`.

---

## Step 0 — Understand the part first

```
mcp__build123d-mcp__execute  — build the part in the session
mcp__build123d-mcp__measure  — confirm volume, bbox, face count
mcp__build123d-mcp__render_view (save_to='/tmp/preview.png') — visual sanity check
```

Register the part under a stable name with `show(part, "part")`.

---

## Step 1 — Generate the drawing automatically (start here)

```python
from draftwright import make_drawing

svg, dxf = make_drawing(
    part,                       # an in-session build123d object, OR a "path/to/part.step"
    out="drawings/bracket",     # output stem; ".svg"/".dxf" are appended
    title="BRACKET",            # ISO 7200 document title
    number="DWG-042",           # ISO 7200 document identifier
    tolerance="ISO 2768-f",     # general tolerance
    drawn_by="Your Name",
)
```

`make_drawing` chooses the scale + ISO page size, projects front/plan/side/iso
views, and annotates automatically — then lints and writes both SVG and DXF.

Automatic annotation covers **prismatic parts in full**: every recognised hole
gets a grouped callout ("4× ø10 THRU", counterbore/depth symbols), bolt circles
get "EQ SP ON øD BC" callouts with a pitch-circle centreline, linear arrays get
pitch dims, every hole gets a centre mark and baseline X/Y location dims from the
min-X/Y datum corner, and blind/counterbored holes trigger an automatic SECTION
A–A with ISO 128-44 solid filled arrows and ISO 128-50 45° hatching. Turned
parts get OD/length dims, centrelines, and bore leaders.

Then verify (Step 3). For most parts you are done here.

---

## Step 2 — Customise with the Drawing builder

```python
from draftwright import build_drawing
from build123d_drafting import Leader

dwg = build_drawing(part, out="drawings/bracket", title="BRACKET",
                    number="DWG-042", tolerance="ISO 2768-f", drawn_by="Your Name")

# Available on dwg:
#   dwg.views        {"front","plan","side","iso"} → (visible, hidden) compounds
#   dwg.annotations  mutable list of annotation objects
#   dwg.draft / dwg.scale / dwg.page_w / dwg.page_h
#   dwg.at(view, x, y, z)    → page point (px, py, 0) mapped from world coordinates

# Add a dimension/leader the automatic pass missed:
dwg.add(Leader(tip=dwg.at("front", 10, 0, 5), elbow=(8, 40, 0),
               label="ø4 BORE", draft=dwg.draft), "ldr_bore")

# Drop an automatic annotation by name:
dwg.remove("dim_od")

# Re-lint after edits, then export:
issues = dwg.lint()                       # list of LintIssue; [] when clean
svg, dxf = dwg.export("drawings/bracket")
```

`make_drawing(...)` is exactly `build_drawing(...).export()`.

**Add a section or auxiliary view** with `add_view()`:

```python
look = dwg.look_at
bottom = (look[0], look[1], look[2] - dwg.dist)
vc = dwg.add_view("bottom", part, bottom, (0, 1, 0), (260.0, 60.0))
px, py = vc.pp(world_x, world_y, world_z)
```

---

## Step 3 — Verify

```
mcp__build123d-mcp__render_drawing(svg_path='drawings/part_name.svg', save_to='/tmp/dwg.png')
mcp__build123d-mcp__save_drawing_annotations(svg_path='drawings/part_name.svg')
mcp__build123d-mcp__inspect_drawing(svg_path='drawings/part_name.svg')
```

---

## Step 4 — Save a standalone regeneration script (default)

**A — Drawing from a STEP file**:

```python
from draftwright import generate_script

generate_script(
    "path/to/part.step",
    out="scripts/drawings/bracket",
    title="BRACKET", number="DWG-042",
    tolerance="ISO 2768-f", drawn_by="Your Name",
)
```

**B — Drawing an in-session object** (hand-write the script):

```python
#!/usr/bin/env python3
"""BRACKET — regenerates drawings/bracket.svg + .dxf in one run."""
from draftwright import make_drawing
from myproject.bracket import build_bracket

part = build_bracket()
make_drawing(part, out="drawings/bracket", title="BRACKET",
             number="DWG-042", tolerance="ISO 2768-f", drawn_by="Your Name")
```

---

## Scale and page control

```python
from draftwright import choose_scale

SCALE, PAGE_W, PAGE_H, TB_W = choose_scale(x_size, y_size, z_size)
make_drawing(part, out="drawing", scale=2.0, page="A2")
```

---

## Lint and coverage

```python
from draftwright import build_drawing, lint_feature_coverage

dwg = build_drawing(part)
issues = dwg.lint()
for i in issues:
    print(i.severity, i.message)
```

---

## Using this skill with build123d-mcp

This skill requires `draftwright` to be in the server's import allowlist.
The build123d-mcp server allows it by default from v0.3.51 onwards. If you see
`ImportError: draftwright is not in the import allowlist`, pass
`--allow-imports draftwright` to the server CLI or set
`BUILD123D_ALLOW_IMPORTS=draftwright` in the environment.

For annotation primitives (`Dimension`, `Leader`, `TitleBlock`, `lint_drawing`,
`ViewCoordinates`, etc.) continue to import from `build123d_drafting`.
