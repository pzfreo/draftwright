# Shared drawing documents

`Document` builds explicitly named sheets against one immutable STEP snapshot and one
recognition inventory. Use it when dimensions intentionally live on different sheets.
The member sheets use the existing Sheet declarations, compiler and placement solver.

```python
from pathlib import Path
from draftwright import Document

package = Document.from_part("part.step")
general = package.sheet("general", detail_view=False)
holes = package.sheet("holes", detail_view=False)
for sheet in (general, holes):
    sheet.authored_dimensions().authored_views()
    for view in ("front", "plan", "side"):
        sheet.view(view)

for feature in package.features:
    if feature.kind == "envelope":
        for parameter in feature.parameters():
            general.dimension(feature, parameter.parameter_id)
    elif feature.kind == "hole":
        holes.dimension(feature, "bore.diameter")

result = package.build()
report = result.report()
print(report["assessment"])
Path("out").mkdir(exist_ok=True)
result.write_report("out/document.json")
for name, drawing in result.sheets.items():
    drawing.export(f"out/{name}", formats=("pdf", "svg"))
```

This example selects envelope measurements and hole diameters. Other recognized requirements,
including omitted depths and locations, remain visible in the report. Use the discovered
measurements and operation identities to choose the dimensions needed for your part.

`package.features` holds the exact common IR features. Pass those objects to each member's
`dimension()` or `of()` methods. Physical membership is sealed: deleting a feature, replacing it
with an equal clone, or changing its geometry through an aspect verb is refused. Dimension
selection, supported decorations, notes and GD&T remain member declarations. Existing
single-sheet authoring is unchanged.

A build snapshots every member's declarations before building the first member. A failure names
the member through `DocumentBuildError.sheet_name`; cancellation names it in
`BuildCancelled.diagnostic["sheet"]`. Neither returns a complete partial package.

## Read the document report

`result.report()` uses `scope: "document"`. Ordinary documents return schema version **4**
([schema](draftwright-report-v4.schema.json)). A document with a declared feature schedule returns
version **5** ([schema](draftwright-report-v5.schema.json)), including when the table could not be
placed or was removed. Individual `Drawing.report()` calls retain version 3. The document report
contains:

- One source hash, recognition inventory and physical requirement catalog. Repeated annotations
  receive at most one coverage credit for each obligation.
- Sheet-local outcomes and lint, with carrying annotation references identifying the sheet and
  annotation. A structured-note satisfaction remains distinct from a verified measurement.
- Complete dependency proofs where an existing producer permits several measurements to
  establish another requirement. Missing, conflicting or unconfirmed prerequisites do not
  establish such a proof.
- Separate coverage, layout, fidelity and manufacturing assessments. Conflicting confirmed
  engineering meanings retain both claims. Matching nominal numbers on different owners do
  not join their operations.

`bounded-clear` names the report's recognition and verification limits. Unsupported geometry,
unknown cardinality, unconfirmed claims and unresolved dispositions remain explicit.
Manufacturing readiness is unassessed. Free-text notes do not acquire measurement authority.

## Feature schedules

A schedule declares measurements through exact feature owners and canonical parameter IDs.
The compiler supplies the values and their formatting; the existing table placement path fits
and places the result. The `location` selector expands the feature's addressable member
coordinates. Discover supported parameters with `sheet.of(feature).dimension_ids()` and member
selectors with `sheet.dimension_options(feature, "location")["location_components"]` when choosing only some components.

```python
from pathlib import Path
from draftwright import Document

package = Document.from_part("part.step")
features = [feature for feature in package.features if feature.kind == "hole"]
assert features, "select recognized hole operations before building this recipe"
sheet = package.sheet("features", page="A3", detail_view=False).authored_views()
sheet.view("front")
sheet.view("plan")
sheet.schedule(
    [(feature, ("bore.diameter", "location")) for feature in features],
    name="holes",
    prefer="br",
)
result = package.build()
Path("out").mkdir(exist_ok=True)
result.sheets["features"].export("out/features", formats=("pdf", "svg"))
result.write_report("out/document.json")
```

This example declares hole diameter and location cells; other requirements remain in the
report. A schedule selects an authored dimension set. Add ordinary `sheet.dimension(...)`
statements when the same measurement should also appear beside a view. Schedules do not mix
with `auto_dimensions()` or augmenting `add_dimension(...)` intent. A table-only measurement
requires no leader view; an ordinary dimension still needs a view that can show it.

Each measured cell retains its exact owner and parameter, plus a one-based data-row index
(the header is row zero) and a zero-based column index. V5 claims, carrying annotations and
cell-specific uncertainties include these addresses. Ordinary annotations and uncertainties
without a recoverable cell use `cell: null`. Header, owner, axis and quantity context are part
of cell verification. Changing a nominal, tolerance or relevant context withdraws the affected
proof; equal numbers elsewhere in the table cannot replace it.

Use `result.sheets["features"].remove("holes")` to remove the whole table. If a schedule also
contains other features, `drop(feature)` refuses to remove only that feature's rows; change the
source recipe and rebuild.
Failed placement reports the scheduled measurements without silently shrinking the table's
text. Structured notes earn only their explicitly declared satisfaction; descriptive table
cells and ordinary prose earn no dimensional credit.

## Edits, persistence and replay

Member Drawings remain editable through their public verbs. Read `result.report()` again after
editing: deleting the last carrying annotation loses its credit without removing its obligation.
A live callout respects the member's authored dimension set; author its destination measurement
before building a sheet that will carry it. Use `deferred()` for batches of supported edits.
If physical membership is corrupted after the build, document reporting refuses.

A returned report is detached JSON. Calls and edits must be serialized by the caller; the API
makes no concurrent-mutation snapshot promise. `write_report()` uses the existing atomic writer
and requires its parent directory to exist. Export remains explicit, per member.

The source hash identifies the retained STEP bytes. Report-local owner, occurrence, sheet,
requirement and claim IDs expire with that report; they are not reusable feature handles.
The report records member build options, dimension selection, view constraints, ordinary table
text and resolved layout decisions. V5 also records the captured feature-schedule selectors,
using report-local owner IDs. Preserve your source recipe for decorations, GD&T,
feature-linked notes, measured dimensions, member PMI declarations and live edits. Replaying a recipe means loading the source and selecting
current exact features with operation/cardinality assertions. It never means deserializing old
IDs or treating an independently built Drawing or PDF as common authority.

The [two-sheet frame example](frame-document.md) executes this workflow on a pinned real part,
with a canary checking operation identities, section material, and loss of coverage after edits.
