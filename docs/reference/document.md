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

`result.report()` returns schema version **4**, with `scope: "document"`. The schema is published as
[`draftwright-report-v4.schema.json`](draftwright-report-v4.schema.json). Individual
`Drawing.report()` calls retain version 3. The document report contains:

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
text and resolved layout decisions. Preserve your source recipe for decorations, GD&T,
feature-linked notes, measured dimensions, member PMI declarations and live edits. Replaying a recipe means loading the source and selecting
current exact features with operation/cardinality assertions. It never means deserializing old
IDs or treating an independently built Drawing or PDF as common authority.

Typed feature schedules and the complete two-sheet frame recipe are tracked separately in
#1543 and #1544.
