# Machine-readable reports

`Drawing.report()` returns a JSON-compatible Draftwright report. Version 1 is deliberately a
bounded first contract: it projects the accepted occurrences from one raw automatic recognition
run, their exact consumer dispositions and final IR owners, the recognition-owned semantic
requirement ledger, and `Drawing.lint_summary()`.

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

Version 1 refuses declared, provider-framed, foreign-result, and bare drawings with
`ReportUnavailableError` because those paths do not carry exact run-local occurrence ownership.
It also refuses a raw automatic drawing when any accepted occurrence remains unclassified; the
report never silently removes that occurrence from its denominator. It does not reconstruct
ownership from values, labels, rendered coordinates, topology traversal, or a second recognition
scan. Declared reconciliation and framed evidence remain explicit later contracts rather than
holes disguised as an empty report.

`generate_sheet_script(...)` writes its recognition evidence to
`<stem>.draftwright-inspection.json` beside the generated script — see
[Recognition evidence](inspection.md). It used to embed a `DRAFTWRIGHT_RECOGNITION_SNAPSHOT`
literal in the Python instead; #1460 moved it to a file that can be diffed and re-read without
parsing Python, and widened it from the gaps alone to every accepted finding with the outcome
Draftwright gave it.
