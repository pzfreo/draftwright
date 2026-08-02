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

Requires `draftwright >= 0.4.0` and `build123d-drafting-helpers >= 0.14.1`.
Install: `pip install draftwright`.

(The old floor said 0.1.9, which cannot run this guide: none of the feature-backed
verbs existed then, and the dotted parameter ids in Step 2 are new in 0.4.0.)

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

`build_drawing(...)` returns a live `Drawing`. **Edit it in *domain* vocabulary —
name a detected *feature* and the measurement you want; the engine decides the
offset, stacking, and strip slot.** You give *what*, never *where on the page*:
placement is automatic and constraint-based.

Prefer the feature-backed verbs (`dimension` / `locate` / `callout` / `note` /
`drop`) over the raw page-coordinate primitives. The low-level API (`place_dim`,
`add`, `add_view`, the view-coordinate plumbing) is **deprecated** — see
`docs/deprecations.md` — because a raw coordinate does not route through the
layout solve, so it cannot be re-flowed when anything around it moves.

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

**Add a dimension by naming a feature and a measurement** — the engine derives the
value from the geometry and owns the placement:

```python
# side ∈ {"above","below","left","right"}; view ∈ {"front","plan","side"}.
env  = next(f for f in dwg.model().features if f.kind == "envelope")
hole = next(f for f in dwg.model().features if f.kind == "hole")

dwg.dimension(env, "length", role="width", side="above", view="front")
dwg.locate(hole)                         # locating dimensions for the feature
dwg.callout(hole)                        # a ⌀ callout the auto-pass missed

dwg.drop(hole)                           # stop dimensioning this feature

name = next(iter(dwg.annotations()))     # names come from annotations(); they are
dwg.remove(name)                         # engine-assigned, so don't guess one
```

**Mind the two spellings.** On `Drawing.dimension` the second argument is the
parameter *kind* (`"length"`, `"diameter"`) and `role=` discriminates between
same-kind parameters — an envelope has three `length` params with roles `width` /
`height` / `depth`, so `dimension(env, "length")` alone is ambiguous and raises,
naming them. `feature.parameters()` returns `DimParameter` objects carrying
`.kind` and `.role`, so `[(p.kind, p.role) for p in feature.parameters()]` is how
to see what a feature accepts.

On the `Sheet` facade the same measurement is one dotted **parameter id** —
`"width.length"` — and `dimension_ids()` on a handle lists them. Use the id there
rather than the bare family role (`"width"`): the bare spelling is deprecated,
because it is what let a single call silently declare two dimensions.

`place_dim(p1, p2, side, view, …)` still exists for raw page coordinates, but it is
the **escape hatch of last resort** (ADR 0012) and is deprecated: it bypasses the
layout solve, so nothing re-flows around it.

**Add a diameter callout on a hole the auto-pass missed** — find the bore in the
model IR and hand it to `callout()`. This is what the `feature_not_dimensioned`
lint suggestion hands you (it emits `dwg.callout(f)` — say *what*, not *where*):

```python
for f in dwg.model().features:
    if f.kind == "hole" and abs(f.diameter - 4.0) < 0.2:
        dwg.callout(f)                   # engine picks the view, leader and elbow
```

**Free text** at a chosen point is `note()` — a domain verb, not an escape hatch:

```python
dwg.note("ø4 BORE", dwg.at("front", 10, 0, 5), view="front")
```

Then re-lint and export:

```python
issues = dwg.lint()                       # list of LintIssue; [] when clean
paths = dwg.export("drawings/bracket", formats=("svg", "dxf", "pdf"))
svg, dxf = paths["svg"], paths["dxf"]
```

Pass `formats=` and read the `{format: path}` dict. Calling `export()` with no
`formats` — or with the `svg=`/`dxf=` booleans — takes the legacy path and returns
a `(svg, dxf)` tuple. Both are deprecated (v0.3.1) and removed in 0.5.0, and both
warn from 0.4.0. See `docs/deprecations.md`.

`make_drawing(...)` is unaffected and does not warn: it still returns
`(svg_path, dxf_path)`, and passes `formats=` internally to get them.

`make_drawing(...)` is `build_drawing(...).export(formats=("svg", "dxf"))`, unpacked to a
tuple — not a bare `.export()`, which is the deprecated shape above.

**Section views** come from the section verb rather than from projecting a view by
hand. The two entry points are *not* equivalent:

```python
from draftwright import Sheet

# Sheet.section() FORCES a cut, wherever you point it.
cut = Sheet.from_part(part, number="DWG-042").section().build()
"section_aa" in cut.views                 # True
# section(feature) cuts through that feature; section(at=y) at an explicit Y.

# Drawing.section() adds only the AUTOMATIC A–A, which fires just for qualifying
# hidden internal geometry (a counterbore, spotface, or blind bottom). It returns
# the placed annotation names, or [] when no section is warranted — so on a plain
# through-holed block it does nothing at all.
dwg.section()
```

Arbitrary **auxiliary** views have no public verb: `add_view()` was the way to
project one and is deprecated, so a custom viewing direction is not currently part
of the supported surface.

`add_view()` and the view-coordinate plumbing (`set_view_coordinates`,
`drop_view_coordinates`, and the `vc.pp(...)` projector) are **deprecated** (#817):
view projection is engine plumbing, and hand-placed views do not participate in the
compose-then-pack layout.

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
from draftwright.sheet_emit import generate_sheet_script

generate_sheet_script(
    "path/to/part.step",
    out="scripts/drawings/bracket",
    title="BRACKET", number="DWG-042",
    tolerance="ISO 2768-f", drawn_by="Your Name",
)
```

This writes a declarative `Sheet` script: one named line per detected feature, then the
dimension set it draws. Comment a feature line out to drop that feature; edit a value
freely; re-run the file. (`generate_script`, the older imperative flavour, was retired in
#940 and deleted in 0.4.0 — importing it now fails; use `generate_sheet_script` above.) The
CLI equivalent is `draftwright part.step
--script --out scripts/drawings/bracket`.

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
        print("  fix:", i.suggestion)   # e.g. dwg.callout(...) / dwg.dimension(...)

# 3. Self-repair — auto-applies the mechanically-fixable issues (overlapping
#    labels pushed apart, wrong-side dims flipped). Runs by default inside
#    build_drawing; call again after manual edits. It never makes a sheet worse.
dwg.repair()

# Pin a deliberate placement so repair won't move it (the constraint solver will
# honour it too as it lands — ADR 0003):
dwg.pin(name)             # name from dwg.annotations(); dwg.unpin(name) to release
```

Codes are domain-meaningful (`feature_not_dimensioned`, `feature_count_mismatch`,
`callout_dropped`, `location_ref_dropped`, `step_dim_dropped`, …), so a fix is
always expressible through the domain API (`dimension`, `locate`, `callout`,
`features`), never the page-layout internals. Loop until `passed` (or the score
plateaus).

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
