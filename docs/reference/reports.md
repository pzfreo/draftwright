# Machine-readable reports

For common-source multi-sheet reports, see [Shared drawing documents](document.md).
`DocumentResult.report()` uses document scope/version 4 or 5. A raw automatic
`Drawing.report()` uses version 3; a declared `Sheet` drawing uses version 8.

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

## Declared Sheet reports (version 8)

A drawing built from `Sheet` or an injected `PartModel` uses `scope: "declared-sheet"` and
`declarations.authority: "final-ir"`. Version 8 retains the complete `lint_summary()` payload,
the final IR inventory, build-scoped declaration selectors, and exact annotation names, views,
measurement identities, and structured-note satisfactions from the drawing registry. A generated
script can also carry occurrence references from its exact generation sidecar. Those references
remain generation-run-local claims: a consumer must corroborate them against that sidecar's source
hash before relying on them. A hand-authored or replayed declaration without that evidence states
the correspondence is unavailable. The report never reconstructs links from values, list order,
Python variable spelling, annotation names, or coordinates.

`layout` adds page/scale/margin facts, resolved view bounds, and each registered annotation's
full-ink bounds, label bounds, public 2D line segments, view, and exact semantic links. Its
`findings` point back to the raw `lint.issues` rows by index; pair findings retain both named
annotations. Remedies are limited to supported semantic controls (`page`, `scale`, `view`,
`section`, `schedule`, `side`, `lane`, `priority`, and `pin`). Page coordinates are evidence for review,
not an editing API.

When a generated or hand-authored sheet uses `sheet.layout_override(...)`,
`layout.overrides` records the declaration, control, authored and resolved value,
`status: "applied"`, and
`intent_class: "layout-only"`. The corresponding feature still carries its complete semantic
content and the ordinary shared placement solve still owns coordinates and feasibility. The
side form records a corridor; the dimension form additionally records the exact `parameter_id`
and one-based feature-relative `lane`. The field is emitted as an empty array when no override
exists; it is optional in the version-8
schema so previously written version-8 documents remain valid.

Detailed solver outcomes are opt-in. Without `build_drawing(trace=...)`,
`layout.placement.availability` is `unavailable` with a reason rather than an empty successful
solve. With tracing enabled, corridor outcomes retain placed/dropped/deduplicated/promoted/deferred
states, blockers and rejection reasons; dropped corridor candidates are joined to final IR and
declaration authority when the route retained it. Routes that do not retain that provenance say
so explicitly. A recorder failure produces `partial`, never a complete-looking trace.

The closed contract is
[`draftwright-report-v8.schema.json`](draftwright-report-v8.schema.json). Version 7 remains
published for existing documents. A
`bounded-clear` declared report means only that the available declared-drawing critique requires
no attention. It is not recognition recall, physical completeness, or manufacturing readiness.
The generated script embeds only build-scoped selectors and run-local occurrence references; its
adjacent inspection sidecar remains the authority for what that generation run recognised.

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
When a producer cannot state an outcome's physical cardinality, version 3 retains one aggregate
requirement row with `reason_code: "requirement_cardinality_unknown"`; it does not expand the
producer's placeholder count. The open `lint.quality.completeness` payload reports the affected
family/parameter outcomes, sets `coverage` to `"indeterminate"`, and returns no numeric
`requirements` denominator or `audited_score`.

The six dispositions are `represented`, `absorbed`, `unsupported`, `deferred`, `evidence_only`,
and `unexpectedly_missing`. A known unsupported, deferred, evidence-only, or missing occurrence,
an unprojected/unavailable requirement boundary, or any non-credit requirement outcome makes
top-level `status` be `needs-attention`. Otherwise it is `bounded-clear`.

For raw automatic reports, `lint.assessment` is the primary compact status summary. Its
`basis` is `evidence-vector-no-scalar`; independent recognition, requirement, completeness,
fidelity, legibility, restraint, and lint-diagnostic axes retain affected counts,
denominators where one exists, and unavailable reasons. `status_reasons` names every predicate that selected
`needs-attention`, its responsible axis, affected count and denominator, plus at most five
deterministically ordered typed evidence references. One unresolved occurrence out of 177 is therefore
distinguishable from 177 out of 177 without inventing a weighted severity score. The human
`summary` is rendered from those same structured reasons.

`lint.assessment.remediation` is the compact responsibility projection over that evidence. It
groups findings and adverse occurrence/requirement outcomes into `contradiction`,
`missing-carrier`, `layout`, `importer-lowering`, `source-ambiguity`, or
`evidence-unavailable`; anything outside the bounded classifier remains visible as
`unclassified`. Items retain their report-local lint, occurrence, requirement, declaration,
owner, annotation, and source identities where those identities exist. Requirement subjects also
carry family, parameter, and reason fields, so an agent need not infer “plate thickness” from an
opaque requirement number.

Remediation order is severity, then the documented domain order, then evidence identity. It never
reads a quality score. `supported_actions` is deliberately narrow: an action names an allowlisted
public verb, its arguments, and the exact evidence to which it applies. When no such bounded action
is supported, `no_supported_action_reason` says so; unknown codes never acquire a remedy from their
message text. Every item names the measurement ownership, parameter meaning, source provenance,
and unrelated requirement outcomes an edit must preserve. This is diagnosis and bounded edit
capability, not authorization to release a drawing.

The surrounding lint payload still carries `score` and `diagnostic_score` as legacy diagnostic
compatibility aliases. Neither is a drawing-quality result, and the assessment projection does
not read them. Consumers should start with `lint.assessment`, then inspect the linked occurrence,
requirement, quality-component, and lint evidence.

Claim fidelity compares compiler-owned presentation evidence rather than requiring the nominal
token literally. In particular, an imported upper/lower limit dimension retains its typed
absolute bounds through compilation; both bounds together can bear out the nominal measurement.
One changed or missing bound fails, and two untyped numbers surrounding a nominal are never
promoted to a limit dimension by midpoint inference. Count and through state remain separately
owned structured requirements rather than numbers borrowed by this equivalence rule.

`bounded-clear` does **not** mean manufacturing-ready or physically complete. A non-credit semantic
requirement state makes the report require attention, but the report still covers accepted
recogniser output rather than every physical feature a recogniser might fail to find. Material,
thread, fit, tolerance, finish, and process intent also remain separately authored readiness facts;
the report never invents them.

The version-3 recognised projection refuses provider-framed, foreign-result, and bare drawings
when they do not carry exact run-local occurrence ownership. It also refuses a raw automatic
drawing when any accepted occurrence remains unclassified; the report never silently removes
that occurrence from its denominator. Declared drawings route to version 8 instead. Neither
contract reconstructs ownership from values, labels, rendered coordinates, topology traversal,
or a second recognition scan.



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

For declared drawings, version 6 preserves this summary without claiming recognised occurrence
ownership. On supported automatic drawings, version 3
`report["recognition"]["requirements"]` supplies the existing occurrence/owner/annotation links
for individual outcomes. This is single-sheet review, not cross-sheet reconciliation.

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

Running that generated Python writes
`<stem>.draftwright-assessment.json`, whose separate
[`draftwright-replay-assessment` schema](replay-assessment.md) binds the exact script, STEP,
exported files, and this strict declared-sheet report. The inspection says what generation-time
recognition saw; the replay assessment says what the current editable declaration built and
drew. Read both without merging their scopes or treating build-local declaration IDs as durable
topology identity.
