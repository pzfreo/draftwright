# Machine-readable reports

`Drawing.report()` returns a JSON-compatible Draftwright report. Version 1 is deliberately a
bounded first contract: it projects the accepted occurrences from one raw automatic recognition
run, their exact consumer dispositions and final IR owners, and `Drawing.lint_summary()`.

```python
from draftwright import build_drawing

drawing = build_drawing("part.step")
report = drawing.report()
```

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

The report is in-memory only in this slice. Atomic JSON writing, output manifests, generated-Python
gap snapshots, and CLI/script sidecars follow as separate vertical slices. Calling `report()` does
not alter PDF, SVG, DXF, or PNG content.
