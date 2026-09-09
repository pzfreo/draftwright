# Machine-readable reports

For common-source multi-sheet reports, see [Shared drawing documents](document.md).
`DocumentResult.report()` uses document scope/version 4; the single-sheet version-3
contract described below remains unchanged.

`Drawing.report()` returns a JSON-compatible Draftwright report. Version 3 is a
bounded contract: it projects the accepted occurrences from one raw automatic recognition
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

Version 3 adds exact outer-profile support sources for angular requirements.
[Version 2](draftwright-report-v2.schema.json) named the actual recognition provider in `producer.quiddity`.
[Version 1](draftwright-report-v1.schema.json) used `producer.b123d-recognisers`
and remains available for existing documents. Readers must select the schema using
`schema_version`; a Quiddity version is not a b123d-recognisers version.

The closed top-level schema is published as
[`draftwright-report-v3.schema.json`](draftwright-report-v3.schema.json). `schema` is always
`"draftwright-report"`; consumers must check `schema_version` before interpreting the document.
The schema deliberately closes its report-owned objects. Adding a field to one of those objects,
changing a meaning, or removing a field requires a new schema version. Only the explicitly open
payload containers (`record`, `outputs`, and `lint`) can gain producer-owned fields under version 3.

Occurrence, owner, and requirement IDs are deterministic **within one report**. They are allocated
from the provider's accepted-occurrence order, Draftwright's final IR order, and the existing typed
semantic completeness ledgers; they are not persistent topology IDs and must not be stored as
identities across recognition runs. `record` is the public recogniser record's JSON projection,
and `record_schema_version` comes from Draftwright's installed consumer capability declaration.
The open `record` payload preserves the provider's public JSON, including any run-local body or
face indices. Those values are evidence from that run, not document IDs or references that a
consumer may reuse across runs.

`recognition.requirements` contains each auditable physical requirement once. Its
`occurrence_ids` point to the exact accepted records that establish the requirement and its
`owner_ids` point to the final IR consumers. Grouped hole/slot/pocket members and nested
countersinks therefore share requirement IDs instead of duplicating a physical denominator. Each
row reports the semantic state (`placed`, structured-note satisfaction, `suppressed`, `dropped`,
`missing`, `unverifiable`, or `unsupported`) and any annotation names that carry exact registry
measurement/satisfaction provenance. An empty `annotations` list does not mean no ink exists: some
compound renderer facts have typed semantic evidence without an independently addressable
annotation identity.

Outer-profile angles use `profile_source` instead of an accepted occurrence: its
`kind` is `planar_outer_profile`, with a report-local `profile_id` and two
`support_ids`. Their `occurrence_ids` are empty. These IDs are allocated on first
use; no opaque provider reference or topology index is serialized. The physical
corner remains a requirement when its IR owner or annotation disappears; a missing
conversion binding is `unverifiable`, and a bound owner absent from the final model
is `missing`. `recognition.owners` lists all final IR owners so profile-derived
requirements can reference owners without inventing accepted occurrences.
Version 1 and 2 schemas remain unchanged for existing documents.

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

Version 2 refuses declared, provider-framed, foreign-result, and bare drawings with
`ReportUnavailableError` because those paths do not carry exact run-local occurrence ownership.
It also refuses a raw automatic drawing when any accepted occurrence remains unclassified; the
report never silently removes that occurrence from its denominator. It does not reconstruct
ownership from values, labels, rendered coordinates, topology traversal, or a second recognition
scan. Declared reconciliation and framed evidence remain explicit later contracts rather than
holes disguised as an empty report.



## Reviewing one sheet

`drawing.lint_summary()` works on both automatic and declared drawings. Its `review`
field explains the existing observations together:

```python
summary = drawing.lint_summary()
for topic, explanation in summary["review"].items():
    print(topic, explanation)
```

`passed` only tests whether error-severity findings are absent. Warnings can therefore
reduce the legacy `score` (also named `diagnostic_score`) to zero while `passed` remains
true. This penalty is not a composite drawing-quality score. The separate `quality`
components retain their existing meanings; the explanatory text derives from them without
changing their scores or the recognized requirement denominator.

Coverage lists placed, structured-note satisfaction, suppressed, dropped, missing,
unverifiable and unsupported outcomes separately. Its scope remains the audited recognized
requirements, with exclusions in `quality.completeness`. Legibility and fidelity explain the
checks performed; neither a clean layout nor an absence of detected contradictions proves
manufacturing readiness. The report does not assess whether all material, process, finish,
thread, fit or tolerance decisions have been authored. Ordinary notes receive no inferred
coverage, and layout repair cannot supply missing physical ownership.

For declared drawings, use this summary and `drawing.lint()`; `drawing.report()` retains its
stricter raw automatic ownership requirement. On supported automatic drawings,
`report["recognition"]["requirements"]` supplies the existing occurrence/owner/annotation
links for individual outcomes. This is single-sheet review, not cross-sheet reconciliation.

### Inspecting a leader target

Diameter and straight-blend radius leader-target findings carry `annotation_name`, an available `view`, and an
`evidence_reason` explaining a mismatch or the missing physical evidence. Their existing
`measurement_ids` on `LintIssue` retain feature/parameter references for live inspection;
these object references are not serialized as persistent IDs. The annotation name belongs
to the current Drawing registry and must not be reused across builds.

```python
for issue in drawing.lint():
    if "leader_target_" in issue.code and issue.annotation_name:
        print(issue.annotation_name, issue.view, issue.evidence_reason)
        print(issue.measurement_ids)
        drawing.preview_annotation(issue.annotation_name, "leader-review.svg")
        break
```

`preview_annotation(name, path)` writes an SVG crop including the annotation and its owning
view when known. Orange marks identify the current ink bounds and drawn leader tip; the
caption explicitly says the physical target is not certified. A drawn tip remains inspectable
even when physical ownership is unavailable. The preview does not infer a correct target,
move annotations, change coverage, finalize queued edits or update the Drawing's export paths.
Finish a deferred edit first. Unknown names raise `KeyError`; unavailable ink bounds or a
non-SVG destination raise `ValueError`. The destination's parent directory must exist.

The `review` and diagnostic reference fields are additive inside version 3's open `lint`
payload. The report-owned schema, existing scores and ownership refusals are unchanged.

`generate_sheet_script(...)` writes its recognition evidence to
`<stem>.draftwright-inspection.json` beside the generated script — a different document with a
different schema; see [Recognition evidence](inspection.md). It used to embed a
`DRAFTWRIGHT_RECOGNITION_SNAPSHOT` literal in the Python instead; #1460 moved it to a file that
can be diffed and re-read without parsing Python, and widened it from the gaps alone to every
accepted finding with the outcome Draftwright gave it. The one thing that document does not
carry, which the embedded snapshot did, is each finding's tracking issue: it states a stable
`reason` code, and where a decision is tracked is repository detail rather than evidence about
the part.
