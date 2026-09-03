# Machine-readable reports

`Drawing.report()` returns a JSON-compatible Draftwright report. Version 1 is deliberately a
bounded first contract: it projects the accepted occurrences from one raw or successful
provider-framed automatic recognition run, their exact consumer dispositions and final IR owners,
the recognition-owned semantic requirement ledger, and `Drawing.lint_summary()`.

```python
from draftwright import build_drawing

drawing = build_drawing("part.step")
report = drawing.report()
drawing.write_report("part.draftwright.json")
```

`write_report(path)` writes the same report as deterministic, indented UTF-8 JSON with a trailing
newline and returns the destination path as a string. The write is atomic within the destination
directory: Draftwright first flushes a sibling temporary file, then replaces the destination. A
report or filesystem failure leaves an existing destination unchanged. Temporary-file cleanup is
best-effort when the filesystem itself refuses it, and a cleanup error never masks the primary
failure. Parent directories are not created implicitly.

The closed top-level schema is published as
[`draftwright-report-v1.schema.json`](draftwright-report-v1.schema.json). `schema` is always
`"draftwright-report"`; consumers must check `schema_version` before interpreting the document.
The schema deliberately closes its report-owned objects. Adding a field to one of those objects,
changing a meaning, or removing a field requires a new schema version. Only the explicitly open
payload containers (`record`, `outputs`, and `lint`) can gain producer-owned fields under version 1.

Occurrence, owner, and requirement IDs are deterministic **within one report**. They are allocated
from the provider's accepted-occurrence order, Draftwright's final IR order, and the existing typed
semantic completeness ledgers; they are not persistent topology IDs and must not be stored as
identities across recognition runs. `record` is the public recogniser record's JSON projection,
and `record_schema_version` comes from Draftwright's installed consumer capability declaration.

`recognition.coordinates` makes the record coordinate authority explicit. Raw reports use
`record_space="caller"` with an identity transform. Framed reports use
`record_space="provider-working"` and carry the exact rigid `caller_from_record` frame—origin,
three basis axes, and provider gauge—needed to interpret local record geometry in caller space.
This is coordinate provenance, not a persistent feature identity or permission to place drawing
annotations at those coordinates.

`recognition.requirements` contains each auditable physical requirement once. Its
`occurrence_ids` point to the exact accepted records that establish the requirement and its
`owner_ids` point to the final IR consumers. Grouped hole/slot/pocket members and nested
countersinks therefore share requirement IDs instead of duplicating a physical denominator. Each
row reports the semantic state (`placed`, structured-note satisfaction, `suppressed`, `dropped`,
`missing`, `unverifiable`, or `unsupported`) and any annotation names that carry exact registry
measurement/satisfaction provenance. An empty `annotations` list does not mean no ink exists: some
compound renderer facts have typed semantic evidence without an independently addressable
annotation identity.

An occurrence's `requirements.coverage` is `ledger` when it references those rows,
`not-applicable` for evidence-only or already-conveyed physical evidence, `deferred` when the
consumer requirement grammar is undecided, `unavailable` when an expected owner disappeared, and
`not-projected` for a supported family that does not yet have a typed semantic outcome ledger.
The report reuses the same family-specific outcomes as `lint_summary().quality.completeness`; it
never reconstructs the physical denominator from final IR parameters or the compiled plan.

The six dispositions are `represented`, `absorbed`, `unsupported`, `deferred`, `evidence_only`,
and `unexpectedly_missing`. A known unsupported, deferred, evidence-only, or missing occurrence,
an unprojected/unavailable requirement boundary, or any non-credit requirement outcome makes
top-level `status` be `needs-attention`. Otherwise it is `bounded-clear`.

`bounded-clear` does **not** mean manufacturing-ready or physically complete. A non-credit semantic
requirement state makes the report require attention, but the report still covers accepted
recogniser output rather than every physical feature a recogniser might fail to find. Material,
thread, fit, tolerance, finish, and process intent also remain separately authored readiness facts;
the report never invents them.

Version 1 refuses declared, foreign-result, bare, and framed-evidence-refused drawings with
`ReportUnavailableError` because those paths do not carry exact run-local occurrence ownership.
It also refuses any automatic drawing when an accepted occurrence remains unclassified; the report
never silently removes that occurrence from its denominator. It does not reconstruct ownership
from values, labels, rendered coordinates, topology traversal, or a second recognition scan.
Declared reconciliation remains a later contract rather than a hole disguised as an empty report.

`generate_sheet_script(...)` also embeds
`DRAFTWRIGHT_RECOGNITION_SNAPSHOT`, a version-1 JSON-compatible Python dictionary containing only
the actionable accepted occurrences that were not represented by ordinary feature declarations
when the script was generated: `unsupported`, `deferred`, `evidence_only`, and
`unexpectedly_missing`. Each gap retains its report-local ID, provider family, public record,
record schema version, disposition, deterministic reason, and tracking issue. Represented and
absorbed occurrences remain expressed by the existing semantic Sheet declarations and are not
duplicated into this compact block.

The snapshot carries the same `coordinates` object as the report projection. Today the public
generated-script path uses raw caller-space recognition; retaining the coordinate contract keeps a
future explicitly framed generator from silently emitting local record geometry as caller geometry.

The snapshot is generation-time evidence, not current authority. For a STEP source it records the
original input basename and SHA-256 of one immutable byte snapshot. Recognition, PMI, and any
semantic-correction build use a private copy of those exact bytes, preventing path or symlink
changes during recognition from splitting the model and its provenance. Immediately before
writing, generation also makes a best-effort check and fails if the resolved replay target is then
missing or has a different digest. The pathname is not locked against later or concurrent
replacement: the embedded hash is the comparison boundary for fresh runtime reconciliation, not a
claim that a mutable path remains current. A live build123d object has no stable source-file hash
and says so with `None`. The
snapshot contains no `FeatureRef`, `FaceRef`, topology index, object address, annotation coordinate,
or Python object representation. An empty snapshot says
`no_unrepresented_accepted_occurrences`; it does not claim physical completeness.

Direct CLI rendering writes `<output>.draftwright.json` after the requested visual formats by
default and prints the sidecar path after the visual paths. `--no-report` opts out. The CLI calls
the same explicit atomic `Drawing.write_report(...)` surface; `Drawing.export(...)` itself still
does not write a report. A visual-export failure does not create or update the sidecar; any
pre-existing sidecar remains untouched and is not evidence of that failed invocation. Report-only
generation, cross-run freshness validation, and output manifests remain later slices.

The visual/report sequence is not a transaction: each successfully exported visual path is printed
before the report write is attempted. If that write fails, the command exits nonzero without
printing a report path; the visual files and any pre-existing sidecar remain. Until an output
manifest binds one invocation's artefacts, consumers must not associate that old sidecar with the
failed run.

The fresh runtime report and its future reconciliation with this embedded snapshot remain a later
slice, as do output manifests and generated-script sidecars. Exact reconciliation is tracked
upstream because a later editable run needs a released source-bound replay receipt; Draftwright
does not substitute record equality, ordering, topology IDs, or geometric proximity. Calling
`report()` or `write_report()` does not alter PDF, SVG, DXF, or PNG content; library export does not
write a report unless the caller requests one.
