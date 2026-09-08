# Sheet and fluent handles

`Sheet` is the declared authoring façade. Feature verbs return fluent handles; those handles
are documented here because they are part of normal use even though their Python names begin
with an underscore.

## Projection convention

Drawings currently use third-angle layout. `Sheet(part, projection="third")` adds its
matching projection symbol; omitting `projection` keeps that layout without a symbol.
`projection="first"` is refused before building or generating a script because first-angle
layout is not yet supported. This is a safeguard against a misleading drawing, not
first-angle support. The same restriction applies to `build_drawing` and the CLI's
`--projection first`. Full convention-aware layout is tracked in
[#1515](https://github.com/pzfreo/draftwright/issues/1515).

## Grooves on coaxial bodies

When different turned bodies share an axis line, give their steps distinct `profile_group`
values and use the owning body's value on `s.groove(..., profile_group="outer")`.
The token associates features; it does not specify an annotation position or add a measurement.
Generated scripts preserve this association using Draftwright-owned tokens instead of provider
keys. A groove can still be declared when the authored set omits all of that body's steps.

## Checking dimension placement rules

Call `handle.dimension_ids()` to find the measurements a declared feature exposes, then
query a measurement before choosing its placement controls:

```python
options = s.dimension_options(hole, "bore.diameter")
# options["placements"] contains pairs accepted by the planner's placement rules.
# None means leave that override unspecified.

check = s.validate_dimension(hole, "bore.diameter", view="plan", side="left")
if check["supported"]:
    s.dimension(hole, "bore.diameter", view="plan", side="left")
else:
    print(check["issues"], check["options"])
```

Both queries return versioned JSON-ready dictionaries without recording intent, preparing the
Sheet, recognising geometry or rendering. Use complete view/side pairs: a side supported in one
view may be unavailable in another. The `axis` argument follows `dimension()`'s parameter-variant
rules; full parameter ids already name their variant. Location intent currently admits no view
or side override. Invalid references, unsupported controls and unsupported placement pairs have
separate structured refusal codes in `validate_dimension()`; `dimension_options()` raises on an
invalid reference.

Hole-size callouts in the plan and side views accept `left` or `right`. The overall
envelope height accepts `left` or `right` in the front view. These hints can separate
hole sizes from vertical location dimensions without moving annotation coordinates:

```python
sheet.dimension(lower_hole, "bore.diameter", side="left")
sheet.dimension(upper_hole, "bore.diameter", side="left")
sheet.dimension(envelope, "height.length", side="left")
sheet.dimension(lower_hole, "location")
sheet.dimension(upper_hole, "location")
```

Each side belongs to its named view: the front-view height and side-view hole sizes
occupy different boundaries. The layout reserves space for the left height and short
step rises together before choosing scale and page. The shared solver places the
annotations within those boundaries; generated scripts retain the authored sides.
An explicit side is a constraint: an infeasible placement reports the affected
measurement instead of silently choosing the opposite boundary. Repair preserves an
authored overall-height side, including after a label adjustment.

The explicit `single_dimension_placement_rules` scope means `supported` reports acceptance by
the current planner placement rules. Both documents set `requires_build_validation: true`:
whole-part classification can select a different renderer, so this query does not prove actual
rendered support. For example, a boss on a turned part can use a different diameter renderer
from a boss on a prismatic part. Agreement with other authored dimensions, visibility in an
authored view set, available space, completeness and collision-free placement also require a
build. Build and lint still assess the resulting Sheet. A
query never replaces an existing dimension request. Keep the original feature handle for edits;
these reports do not introduce persistent feature identities or new lane, route or pin controls.

## Angular measurements

`Sheet.angle()` derives an included angle from a vertex and two witness points
in model coordinates. Select its canonical `included.angle` measurement with
`dimension()`. The engine chooses a true-angle principal view and places the arc,
arrows and complete compiler-owned label through the shared corridor solver.

```python
from math import sqrt
from build123d import Polygon, extrude
from draftwright import Sheet

part = extrude(Polygon((0, 0), (30, 0), (15, 15 * sqrt(3)), align=None), amount=3)
sheet = Sheet(part).authored_dimensions()
angle = sheet.angle(
    vertex=(0, 0, 3), first=(15, 0, 3), second=(7.5, 7.5 * sqrt(3), 3),
)
angle.tolerance(0.05, on="included.angle")
sheet.dimension(angle, "included.angle")
drawing = sheet.build()
for finding in drawing.lint():
    print(finding.code, finding.message)
```

The default `sector="minor"` is the non-reflex angle towards the two witness
points. `sector="opposite"` selects the vertically opposite sector, extending
both supports through the vertex. It preserves the numerical angle and can keep
an exterior dimension beside its corner. The engine never silently switches
between these sectors or substitutes a supplementary angle.
Reversing witness order preserves the selected angle. For extended supports meeting beyond
a rounded corner, use `virtual_vertex=True`; this declares the virtual
intersection without asserting that the vertex lies on a physical edge.
Zero-length or collinear rays, oblique planes and unsupported sectors are
rejected. Insufficient page space produces `angular_dimension_dropped` against
the named measurement. Symmetric tolerances and lower/upper deviation magnitudes
retain degree units, including small deviations when the nominal display is
coarse. Omitting the dimension request suppresses the angle; generated scripts
retain its reference geometry and tolerances.

After building, `drawing.dimension(feature, "included.angle", pin=True, priority=2)`
uses the shared corridor in live and deferred edits. As with other edits, use an
exact feature from `drawing.model()`. Measurement comparison records the angular
references, so equal-valued support substitutions cannot appear preserved merely
because the labels match.

`Sheet.angle_pattern(*references)` declares repeated coplanar corners using
`AngularReference` values from `draftwright.model`. Each corner remains independently
addressable as `included.angle.member1`, `included.angle.member2`, and so on, in
declaration order. Request every member with `dimension()` to permit one quantity
label such as `3× 60°`. Different member tolerances or side preferences produce
individual labels; omitting one member suppresses that measurement without renumbering
the others. The generated script retains the full member geometry and each request.

After the conservative strip solve, curved dimensions have up to three
corner-local radius alternatives at the existing tier spacing. The shared
placement stage accepts a shorter radius or recovers a dropped candidate only
when its actual lines and label clear fixed and same-batch ink and fit the page.
Pinned dimensions retain their solved position.

The lower-level `measured_dimension(kind="angular", ...)` route retains nominal
author-supplied text. Its explicit `AngularReference` supplies the same ray
geometry; plain Sheet-authored three-point `ref_pts` use `(first, vertex, second)`.
Imported PMI requires explicit ordering. Structured tolerances on that raw route
remain unsupported; use the canonical declaration for new authored angles.
Lint compares degree values at their displayed resolution, inspects the visible
angular ink and checks complete canonical labels against the compiler. Physical
critique checks each claimed corner against finite supports in the cached provider
evidence, including members represented by a quantity label. Unprovable correspondence
produces `angular_support_unverifiable`; contradictory supports produce
`angular_support_mismatch`.

Automatic drawings derive outside-profile angular requirements from ordered face
supports in Quiddity 0.2.6. Angles already defined by a recognised chamfer or
regular-polygon callout do not add duplicate automatic requirements. This requires
the same run's exact face or shared-edge evidence, not matching numerical angles;
explicit angle declarations remain available on those corners.
Verified profile repetitions can share a quantity label;
equal numerical angles alone do not establish a pattern. The requirement audit retains
one outcome per physical corner even when several share one mark.

## Selecting a hole location component

Use `location` alone to request the usual location set. To select one component of a hole
group or pattern, pair its declared member index with the measured world axis:

```python
options = sheet.dimension_options(holes, "location")
print(options["location_components"])  # valid member/axis pairs and their parameter ids
sheet.dimension(holes, "location", member=1, axis="y")
```

Member indices start at zero and refer to the declared `members` tuple. A singleton hole
has member 0. The axis is transverse to the hole's own axis: an X-directed hole can have
Y and Z location dimensions. A bolt-circle pattern also accepts `member="centre"`.
Both selectors are required for a fine request; invalid members or axes are refused.
The existing `validate_dimension()` query accepts the same selectors.

The coarse pattern request keeps its automatic anchor policy: the member nearest the
datum, or the centre of a bolt circle. Adding a fine request selects that extra component
without selecting its sibling axis. Repeating a request does not duplicate a measurement.
Generated scripts carry the member and axis so removing one location declaration omits
that component. Coincident dimensions can share one mark while recording every approved
owner.

These indices address one declaration and its generated script. They do not establish
correspondence across unrelated recognition runs. The shared solver still chooses where
to place the dimension; location selection supplies no page coordinates.

## Sheet

### Comparing measurements after a declaration edit

`compare_measurements()` compares named compiled measurements, including their owner,
parameter, nominal value, tolerance, directional span and rendered claim text. Linear
dimensions also retain their measured path lengths in model units, so exchanging labels
between two spans cannot hide behind an unchanged label multiset. Recorded per-annotation
spans and location components also distinguish equal-valued axes under a coarse parameter
id. These are provenance claims; independent lint must still check physical targets.
Reuse the feature objects in a declaration when editing placement intent:

```python
from dataclasses import replace
from draftwright import build_drawing
from draftwright.audit import compare_measurements

original_model = sheet.model()
edited_model = replace(
    original_model,
    authored_dimensions=tuple(
        replace(request, side="left") if request.role == "height.length" else request
        for request in original_model.authored_dimensions
    ),
)
before = build_drawing(part, model=original_model)
after = build_drawing(part, model=edited_model)
result = compare_measurements(before, after)
print(result["status"], result["lost"], result["changed"], result["unknown"])
```

The status is `preserved`, `changed` or `unknown`. An unresolved owner or unconfirmed
claim prevents `preserved`. For an in-place edit, capture
`before = drawing.measurement_snapshot()` before changing the drawing, then compare
that snapshot with the edited drawing. Snapshots retain process-local feature references.

Separately rebuilt declarations do not acquire correspondence from equal geometry or
inventory order. If the caller knows the correspondence, pass
`feature_pairs=((old_feature, new_feature), ...)`. Each pair must name exact owners in
the respective snapshots and the correspondence must be one-to-one. These are caller
assertions; the comparison checks measurement meaning under them. Output owner numbers
are positions in the before snapshot for presentation, not durable identities.

`diff_builds()` includes this result as `measurement_comparison` and detects same-kind
substitutions between shared declared owners. A clear annotation diff can still have
unknown measurement correspondence. Use independent `lint_summary()["quality"]`
evidence alongside the comparison: named-measurement preservation does not establish
physical completeness, pin preservation or release readiness.

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

A detail around a turned step with an approved `step.length` uses a profile view
and redraws that length through the shared dimension pass. For example,
`s.detail_view("A", around=shoulder).scale(3)` retains its measurement identity and
tolerance at three times the sheet scale. Omitted measurements stay omitted. An
authored recovery detail that cannot place its dimension raises an error.

Principal orthographic views share the sheet scale and reject `.scale(...)`. Detail and
isometric handles may carry an independent positive factor. Infeasible relations, scales and
pins raise with their declaration source; unsupported pins on derived or pictorial views are
refused at declaration and constraints are never silently relaxed. The immutable authored
snapshot is available as `sheet.view_constraints`, while `drawing.view_plan` is the distinct
resolved result.

Layout advisories are also available as structured findings from `Drawing.lint()` and
`lint(physical=False)`. `legibility_floor_breached` reports an explicit scale below the
legibility floor when a legible automatic fit exists; `page_fit_uncertain` reports an
unsuccessful layout fit; `layout_repack_stalled` reports unresolved measured-repack
triggers; and `scale_fallback_applied` reports a computed scale or a completeness-driven
scale fallback. These warnings contribute to the legibility component of `lint_summary()`.
A successful measured fit replaces the earlier estimated fit warning.

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

### Straight and circular Blend chains

When recognition accepts a complete schema-v3 straight or circular rolling-ball Blend path that is
not superseded by a dimension-worthy Fillet, it becomes one radius requirement. Declare the same
meaning explicitly with the dedicated word:

```python
blend = sheet.blend(
    axis="z",                         # canonical dominant component (x/y/z tie-break)
    axis_direction=(0.321394, 0.383022, 0.866025),
    radius=0.2,
    at=(12.345, -4.5, 6.789),         # straight anchor / circular centre in part coordinates
    side="convex",
)
sheet.dimension(blend, "blend.radius")
```

For a straight path, `at` is a point on its analytic line and `axis_direction` is the canonical
line direction. A circular path uses `path_kind="circular"`, treats `at` as its centre and
`axis_direction` as its normal, and requires `path_radius=` for the centre-line circle's major
radius. `radius` remains the rolling-ball radius; `side` may be `"convex"` or `"concave"`.
`axis` must match the same first-maximum `x`/`y`/`z` tie-break used by automatic conversion, so an
explicit declaration has the same exact occurrence identity and view routing as its released
record.
Generated Sheet code preserves every field.

Straight-path radius leaders target curved boundaries of the physical cylindrical patch.
Their `at` value locates the analytic axis, not the arrow tip. If a declared support is missing
or ambiguous, the engine reports `blend_dropped`. Physical lint reports
`radius_leader_target_mismatch` when a placed tip misses its trimmed boundary, or
`radius_leader_target_unverifiable` when that boundary cannot be established; these findings
lower fidelity. Circular-path toroidal blends retain their separate tangency selection.

These are part-space feature coordinates, never page positions. The annotation placement solve
owns the final leader and label coordinates. The word is explicit-only because detached surface
geometry cannot prove a complete chain or the provider aggregate's Fillet precedence.

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

Circular-blind, paired-ramp and through steps use a multi-parameter handle because their
requirements remain separately addressable:

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

circular = sheet.circular_blind_step(
    axis="x",
    radius=4,
    length=25,
    centreline=((-5, 15, 10), (20, 15, 10)),  # blind terminal → open envelope
    section=((11, 10), (15, 10), (15, 6)),   # arc endpoint, centre, endpoint
)
sheet.dimension(circular, "circular_step_radius.radius")
sheet.dimension(circular, "circular_step_depth.length")

step = sheet.through_step(
    axis="z",
    length=20,
    at=(12.5, 7.5, 0),
    section=((5, 15), (5, 0), (20, 0)),
)
sheet.dimension(step, "through_step_leg.length.x")
sheet.dimension(step, "through_step_leg.length.y")

blind = sheet.rectangular_blind_slot(
    axis="z",                 # capped penetration/run direction
    open_sign=-1,             # source-envelope mouth along that run
    length=20,
    width_axis="x",
    depth_axis="y",
    depth_sign=1,             # material-outward U-section opening
    width=10,
    depth=5,
    at=(0, 7.5, 10),
)
sheet.dimension(blind, "rectangular_blind_slot_width.length")
sheet.dimension(blind, "rectangular_blind_slot_length.length")
sheet.dimension(blind, "rectangular_blind_slot_depth.length")

round_bottom = sheet.round_bottom_blind_slot(
    axis="z",
    open_sign=1,
    length=20,
    width_axis="x",
    depth_axis="y",
    depth_sign=1,
    radius=3,
    flat_width=4,
    at=(0, -1.5, 10),
)
sheet.dimension(round_bottom, "round_bottom_blind_slot_length.length")
sheet.dimension(round_bottom, "round_bottom_blind_slot_flat_width.length")
sheet.dimension(round_bottom, "round_bottom_blind_slot_radius.radius")
```

These declarations are explicit-only. A detached face or cutter cannot prove the aggregate
material-removal topology. Placement remains solver-owned; the engine selects the end-on view
and positions the compound leader or linear section dimensions.

`rectangular_blind_slot(...)` is not an alias for `slot(...)` or `pocket(...)`. Its dedicated
feature retains the open source-envelope end, capped terminal wall and flat-bottomed U-section,
and the solver places one `OPEN SLOT width × length × depth DEEP` leader carrying all three
compiler-approved measurement identities. In authored-dimension mode, any non-empty subset is
valid and is role-labelled (`WIDE`, `LONG`, `DEEP`) so every requested identity remains visible.
`round_bottom_blind_slot(...)` is a separate word, not a flag on the rectangular declaration.
Its independent dimensions are capped run length, straight bottom-flat width and equal side radius;
the total opening width and profile depth are derived from the latter two and are not duplicated in
the dimension plan. The solver places one `ROUND-BOTTOM OPEN SLOT …` leader. Authored subsets remain
role-explicit (`LONG`, `BOTTOM FLAT`, `R`) and never reconstruct an omitted sibling value.

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

Imported AP242 nominal-diameter ownership requires absolute agreement within `1e-6`
with the canonical feature diameter, using the same rule as planning. A larger mismatch
keeps the original source value and identity in a blocked dimension; it does not replace
the canonical value or crash planning. Generated Sheet scripts retain the blocker, and
`authored_dim_source_unresolved` reports the withheld source dimension through lint.

### Oriented through-slots

`Sheet.oriented_slot(...)` declares a rectangular through-slot with explicit width, long and
run directions and its retained passage geometry. Its two independently addressable
measurements are `oriented_slot_width.length` and `oriented_slot_length.length`; generated
Sheet scripts preserve those directions and the passage. The current drawing grammar requires
both run ends to be open and perpendicular to the run. Automatic conversion rejects a source
with a nonzero end-plane gradient, because the IR cannot retain that slope and a flat opening
would put the leader on the wrong physical rim.
