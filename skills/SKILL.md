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

Requires `draftwright >= 0.1.9` and `build123d-drafting-helpers >= 0.10.1`.
Install: `pip install draftwright`.

**Design model (worth knowing before you edit):** the engine is *deterministic*
— no AI inside it — and you refine a drawing by **stating domain intent**
(dimension this feature, section through here) and letting the engine **place
everything automatically** (placement is constraint-based; you never compute page
coordinates). When the first pass isn't perfect, you drive a **build → critique
→ fix** loop (Step "Lint → critique → fix" below), not a hand-layout edit. The
rationale lives in `docs/adr/`: 0001 (deterministic generation over an editable
DSL), 0002 (the lint critique → domain-repair loop), 0003 (the constraint-based
layout engine). Edit through the domain API; treat `Placeable`/page mechanics as
internals.

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
parts get OD/length dims, centrelines, bore leaders, and — for parts turned
about a horizontal (X) axis — their external stepped diameters as ø
leader-callouts.

Then verify (Step 3). For most parts you are done here.

---

## Step 2 — Customise with the Drawing builder

`build_drawing(...)` returns a live `Drawing`. **Edit it in *domain* vocabulary
— locate things with `features()`, add dimensions with `place_dim()`, choose a
side and view.** You give *what* and *where on the part*; the engine decides the
*offset, stacking, and strip slot* (placement is automatic and constraint-based).
You still pass page-point endpoints, but you get them from `features()` or
`dwg.at(...)` — you never compute offsets or pick a strip. Hand-building a raw
`Leader` at `dwg.at(...)` coordinates is the escape hatch, not the default.

```python
from draftwright import build_drawing

dwg = build_drawing(part, out="drawings/bracket", title="BRACKET",
                    number="DWG-042", tolerance="ISO 2768-f", drawn_by="Your Name")
```

**Inspect what the engine found and placed (read APIs):**

```python
dwg.features("plan")        # detected features in a view → [FeatureInfo(...)]
                            #   each: .type .diameter .through .depth .count .page_pos
dwg.annotations()           # {name: type} of every named annotation already on the sheet
dwg.get_annotation(name)    # the named annotation object, or None
dwg.view_bounds("front")    # (x_min, y_min, x_max, y_max) page bbox of a view, or None
dwg.items                   # the ordered, mutable list of annotation objects
dwg.views                   # {"front","plan","side","iso"} → (visible, hidden) compounds
dwg.draft / dwg.scale / dwg.page_w / dwg.page_h
```

**Add a linear dimension with `place_dim`** — it allocates the offset and stacks
clear of existing dims; you give two page-point endpoints and a side/view:

```python
# side ∈ {"above","below","left","right"}; view ∈ {"front","plan","side"}.
p1 = dwg.at("front", 0, 0, 0)          # world → page point
p2 = dwg.at("front", 40, 0, 0)
dwg.place_dim(p1, p2, "above", "front", dwg.draft, name="dim_len")

dwg.remove("dim_od")                    # drop an automatic annotation by name
```

**Add a diameter callout on a hole the auto-pass missed** — locate it with
`features()` and attach a `HoleCallout` (this is what the `feature_not_dimensioned`
lint suggestion hands you verbatim — see the loop below):

```python
from build123d_drafting import HoleCallout, Leader

for f in dwg.features("plan"):          # plan→Z holes, front→Y, side→X
    if abs(f.diameter - 4.0) < 0.2:
        callout = HoleCallout(f.diameter, count=f.count, through=f.through,
                              depth=f.depth, draft=dwg.draft)
        elbow = (f.page_pos[0] + 15, f.page_pos[1] + 10, 0)
        dwg.add(Leader((*f.page_pos, 0), elbow, "", dwg.draft, callout=callout),
                name="hole_4")
```

**Escape hatch** — only when no domain verb fits (e.g. a free-form note at an
exact spot). Prefer the above; this couples you to page mechanics:

```python
from build123d_drafting import Leader
dwg.add(Leader(tip=dwg.at("front", 10, 0, 5), elbow=(8, 40, 0),
               label="ø4 BORE", draft=dwg.draft), "ldr_bore")
```

Then re-lint and export:

```python
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

## Lint → critique → fix (the loop to drive as an AI)

draftwright is built to be *iterated*: build, read a machine-readable critique,
apply a domain-level fix, re-build. This is the supported refinement model —
prefer it over eyeballing the SVG and hand-placing annotations.

```python
dwg = build_drawing(part)

# 1. Critique — the machine channel. JSON-friendly aggregate of lint().
crit = dwg.lint_summary()
#   {"passed": bool, "score": 0..1, "errors": n, "warnings": n, "infos": n,
#    "by_code": {code: n}, "issues": [{code, severity, message, suggestion?}, ...]}
# Gate on the severity/code COUNTS, not the scalar score.

# 2. Each issue names the problem in DOMAIN terms and (when computable) carries a
#    ready-to-apply suggestion — a domain-API call you paste in, not page maths.
for i in dwg.lint():
    print(i.severity, i.code, i.message)
    if getattr(i, "suggestion", None):
        print("  fix:", i.suggestion)   # e.g. dwg.place_dim(...) / dwg.add_view(...)

# 3. Self-repair — auto-applies the mechanically-fixable issues (overlapping
#    labels pushed apart, wrong-side dims flipped). Runs by default inside
#    build_drawing; call again after manual edits. It never makes a sheet worse.
dwg.repair()
```

Codes are domain-meaningful (`feature_not_dimensioned`, `feature_count_mismatch`,
`callout_dropped`, `location_ref_dropped`, `step_dim_dropped`, …), so a fix is
always expressible through the domain API (`place_dim`, `features`, `add_view`),
never the page-layout internals. Loop until `passed` (or the score plateaus).

Coverage-only check, standalone:

```python
from draftwright import build_drawing, lint_feature_coverage
issues = build_drawing(part).lint()   # geometry lint + feature-coverage check
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
