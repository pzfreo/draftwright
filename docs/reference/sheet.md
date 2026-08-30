# Sheet and fluent handles

`Sheet` is the declared authoring façade. Feature verbs return fluent handles; those handles
are documented here because they are part of normal use even though their Python names begin
with an underscore.

## Sheet

::: draftwright.sheet.Sheet
    options:
      filters: public

### Authored views and layout

View declarations are semantic constraints. They name projections, relationships and whole-view
anchors; the layout solve owns the resulting page positions. Authored views must be paired with
authored dimensions so a deliberately omitted view cannot strand planner-selected annotations:

```python
s = Sheet(part).authored_dimensions().authored_views()
front = s.view("front")
plan = s.view("plan").above(front, gap=4).align_x(front)
s.view("iso").scale(0.75)

section = s.section_view("A", through=hole)
detail = s.detail_view("B", around=hole).scale(2)
dwg = s.build()
```

`view(...)`, `section_view(...)` and `detail_view(...)` define complete authored sets;
omission suppresses a view. `add_view(...)`, `add_section_view(...)` and
`add_detail_view(...)` augment an explicitly selected `auto_views()` source. `row(...)` and
`column(...)` are shorthand for whole-view relations. A principal-view handle's `pin((x, y))`
anchors its projection origin in page millimetres; it never positions an annotation.

Principal orthographic views share the sheet scale and reject `.scale(...)`. Detail and
isometric handles may carry an independent positive factor. Infeasible relations, scales and
pins raise with their declaration source; unsupported pins on derived or pictorial views are
refused at declaration and constraints are never silently relaxed. The immutable authored
snapshot is available as `sheet.view_constraints`, while `drawing.view_plan` is the distinct
resolved result.

### Taking over a detected baseline

`Sheet.from_part(part)` retains the detector's feature set and starts with implicit automatic
dimensions. Use `take_over(...)` to adopt that same Sheet as an explicit editable request without
copying features into a second Sheet:

```python
s = Sheet.from_part(part)
hole = s.of(next(feature for feature in s.features if feature.kind == "hole"))
s.take_over(
    dimensions="authored",
    principal_views="automatic",
    derived_views="authored",
)
s.dimension(hole, "bore.diameter")
s.section_view("A", through=hole)
```

The three sources are chosen atomically. In this example the principal planner keeps the
automatic front/plan/side/isometric baseline, while the authored derived set replaces inferred
sections. Leaving that authored set empty suppresses inferred derived views; selecting
`derived_views="automatic"` accepts them. Matching declarations may appear before or after
`take_over(...)` with the same result. Explicit source contradictions fail without partially
changing the Sheet, and automatic dimensions remain incompatible with authored views under ADR
0018.

To emit editable Python from an adopted Sheet, pass both request halves to the script emitter;
the model carries the feature/dimension semantics and `view_constraints` carries the independent
view sources and semantic targets:

```python
from draftwright.sheet_emit import emit_sheet_script

source = emit_sheet_script(
    s.model(),
    "part = make_part()",
    "drawing",
    title="BRACKET",
    number="DWG-001",
    view_constraints=s.view_constraints,
)
```

### Refining a staged hole into manufacturing authority

A detected hole remains a live feature handle after takeover. Enrich that handle instead of
declaring a second, unrelated annotation. Counterbore/spotface tools can supply their geometry;
thread and fit are authored manufacturing intent:

```python
s = Sheet.from_part(part, page="A2", scale=1)
stack = s.of(next(feature for feature in s.features if feature.kind == "hole"))
s.take_over(
    dimensions="authored",
    principal_views="automatic",
    derived_views="authored",
)

stack.cbore(collar_tool)                 # diameter and depth re-read from the tool
stack.thread("M6x1", depth=12).fit("H8")
for parameter_id in stack.dimension_ids():
    intent = s.dimension(stack, parameter_id)
    if parameter_id == "counterbore.diameter":
        intent.format(decimals=2)        # 6.35 remains 6.35, still feature-linked

s.section_view("A", through=stack)       # replaces inferred derived views
drawing = s.build()
assert not [issue for issue in drawing.lint() if issue.severity != "info"]
drawing.export("quote/grm01", formats=("pdf", "svg", "dxf"))
```

An explicit tap depth is a real `thread.depth` parameter: it appears in
`dimension_ids()`, participates in authored-set suppression and placement, and round-trips
through generated Sheet code. `fit("H8")` applies only to `bore.diameter`; it does not leak
onto a counterbore diameter. A plain `thread("M6x1")` remains valid when no independent depth is
specified.

This surface models the common bore + recess + tap-depth stack in one solver-participating
callout. More general ordered operation stacks remain tracked by issue #1360. Until a physical
requirement has a structured parameter, use a feature-linked `note(..., satisfies=(...))` only
for parameter ids the handle actually exposes; free prose does not satisfy coverage.

## View handle

::: draftwright.sheet._View
    options:
      filters: public

## Dimension intent handle

`dimension(feature, parameter_id)` and `add_dimension(feature, parameter_id)` return a
referential `DimensionIntent`. The handle never carries a replacement nominal and never chooses
page coordinates. Use `format(decimals=n)` to preserve between 0 and 15 decimal places in the
printed nominal while reconciliation, tolerance, suppression and provenance continue to read the
numeric parameter from the feature. Optional `view="front|plan|side"` and
`side="above|below|left|right"` arguments select a supported semantic corridor when authored
routing must override the derived default; the normal placement solve still chooses coordinates
and reports capacity/crossing failures. Trailing zeroes are intentional manufacturing display text:

```python
sheet.authored_dimensions()
sheet.dimension(envelope, "width.length").format(decimals=2)  # 13.5 prints as 13.50
sheet.dimension(tapped_hole, "bore.diameter", view="plan", side="left")
```

::: draftwright.sheet.DimensionIntent
    options:
      filters: public

## Hole handle

::: draftwright.sheet._Hole
    options:
      filters: public

## Diameter and step handle

::: draftwright.sheet._Dim
    options:
      filters: public

## Multi-parameter handle

Paired-ramp and through steps use a multi-parameter handle because their requirements remain
separately addressable:

```python
ramp = sheet.paired_ramp_step(
    axis="y",
    angle=51.34,
    length=25,
    at=(10, 7.5, 0),  # midpoint of the shared ridge
)
sheet.authored_dimensions()
sheet.dimension(ramp, "ramp_angle.angle")
sheet.dimension(ramp, "ramp_run.length")

step = sheet.through_step(
    axis="z",
    length=20,
    at=(12.5, 7.5, 0),
    section=((5, 15), (5, 0), (20, 0)),
)
sheet.dimension(step, "through_step_leg.length.x")
sheet.dimension(step, "through_step_leg.length.y")
```

Both declarations are explicit-only. A detached face or cutter cannot prove the aggregate
material-removal topology. Placement remains solver-owned; the engine selects the end-on view
and positions the compound leader or linear section dimensions.

An explicit through-step may use any principal run axis, and automatic detection supports the
same three axes. Where an X/Y-run record's two exact physical intervals are already proved by the
face-level plus shoulder/plate grammar and the envelope, that established grammar remains the
owner. If even one leg is not proved, the aggregate local-leg grammar owns the occurrence and its
exact matching lower-level fragments are removed, so one physical requirement reaches the sheet
once. Completeness follows the chosen legacy dimensions too: their authored omission, placement
drop, or missing ink is not hidden by the ownership choice.

::: draftwright.sheet._Params
    options:
      filters: public

## GD&T control builder

::: draftwright.sheet._Control
    options:
      filters: public
