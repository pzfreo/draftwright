# Machine-readable reports

`Drawing.report()` returns a JSON-compatible Draftwright report. Version 1 is deliberately a
bounded first contract: it projects the accepted occurrences from one raw automatic recognition
run, their exact consumer dispositions and final IR owners, and `Drawing.lint_summary()`.

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

Occurrence IDs and owner IDs are deterministic **within one report**. They are allocated from the
provider's accepted-occurrence order and Draftwright's final IR order; they are not persistent
topology IDs and must not be stored as identities across recognition runs. `record` is the public
recogniser record's JSON projection, and `record_schema_version` comes from Draftwright's installed
consumer capability declaration.

The six dispositions are `represented`, `absorbed`, `unsupported`, `deferred`, `evidence_only`,
and `unexpectedly_missing`. A known unsupported, deferred, evidence-only, or missing occurrence
makes top-level `status` be `needs-attention`. Otherwise it is `bounded-clear`.

`bounded-clear` does **not** mean manufacturing-ready or physically complete. Version 1 marks every
occurrence's requirement coverage as `not-projected`; later evidence-gated slices will add the
feature → requirement → annotation outcomes needed for readiness. It also covers accepted
recogniser output, not every physical feature a recogniser might fail to find.

Version 1 refuses declared, provider-framed, foreign-result, and bare drawings with
`ReportUnavailableError` because those paths do not carry exact run-local occurrence ownership.
It also refuses a raw automatic drawing when any accepted occurrence remains unclassified; the
report never silently removes that occurrence from its denominator. It does not reconstruct
ownership from values, labels, rendered coordinates, topology traversal, or a second recognition
scan. Declared reconciliation and framed evidence remain explicit later contracts rather than
holes disguised as an empty report.

`generate_sheet_script(...)` also embeds
`DRAFTWRIGHT_RECOGNITION_SNAPSHOT`, a version-1 JSON-compatible Python dictionary containing only
the actionable accepted occurrences that were not represented by ordinary feature declarations
when the script was generated: `unsupported`, `deferred`, `evidence_only`, and
`unexpectedly_missing`. Each gap retains its report-local ID, provider family, public record,
record schema version, disposition, deterministic reason, and tracking issue. Represented and
absorbed occurrences remain expressed by the existing semantic Sheet declarations and are not
duplicated into this compact block.

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

The fresh runtime report and its future reconciliation with this embedded snapshot remain a later
slice, as do output manifests and CLI/script sidecars. Calling `report()` or `write_report()` does
not alter PDF, SVG, DXF, or PNG content; library export does not write a report unless the caller
requests one.
