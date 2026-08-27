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

## View handle

::: draftwright.sheet._View
    options:
      filters: public

## Dimension intent handle

`dimension(feature, parameter_id)` and `add_dimension(feature, parameter_id)` return a
referential `DimensionIntent`. The handle never carries a replacement nominal and never chooses
page coordinates. Use `format(decimals=n)` to preserve between 0 and 15 decimal places in the
printed nominal while reconciliation, tolerance, suppression and provenance continue to read the
numeric parameter from the feature. Trailing zeroes are intentional manufacturing display text:

```python
sheet.authored_dimensions()
sheet.dimension(envelope, "width.length").format(decimals=2)  # 13.5 prints as 13.50
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

::: draftwright.sheet._Params
    options:
      filters: public

## GD&T control builder

::: draftwright.sheet._Control
    options:
      filters: public
