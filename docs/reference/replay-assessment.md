# Generated-script replay assessment

`draftwright --script` produces two evidence artifacts with deliberately different timing and
authority:

- `<stem>.draftwright-inspection.json` is the immutable generation-time account of what the
  STEP recogniser saw. It follows the
  [`draftwright-step-inspection` schema](inspection.md).
- `<stem>.draftwright-assessment.json` is written only when that exact generated or edited
  Python script successfully builds and exports. It follows the
  [`draftwright-replay-assessment` v2 schema](draftwright-replay-assessment-v2.schema.json).

Do not substitute one for the other. Editing the Python does not rewrite the inspection file.
It changes the script hash in the next assessment, and the nested declared-sheet report records
the current declarations, representations, layout evidence, and lint result.

The assessment records the STEP name and SHA-256 hash, exact Python script hash, Draftwright and
Quiddity versions, PMI/export options, and each returned output path, size, and hash. Its
`drawing` member is the strict [declared-sheet report v8](reports.md), produced from the same
finalized `Drawing` that was exported. Version 2 also records confirmed compiled measurement
claims under `measurements`: declaration owner, parameter, engineering value, tolerance, span,
axis/member meaning, rendered claim, and witnesses. Unconfirmed claims and claims whose owner
lacks a declaration identity remain explicit instead of disappearing. `semantic_links` names
the report paths containing the build-local declaration and representation identities; it does
not turn them into persistent topology IDs. The [v1 schema](draftwright-replay-assessment-v1.schema.json)
remains published for readers of older artifacts.

## Comparing an edit

`draftwright.audit.compare_assessments(baseline, candidate, ...)` compares two v2 assessments
only when they name the same immutable STEP hash, producer versions, and run options. It returns
the versioned [`draftwright-assessment-comparison` v2](draftwright-assessment-comparison-v2.schema.json)
evidence vector. The v1 schema remains published for readers of older comparison artifacts. The
result keeps lint, requirement transitions, confirmed meanings, carriers, carrier pin state,
completeness, fidelity, layout, unscored findings, and unavailable evidence separate; it never
constructs a composite quality score.

V2 adds an `axes` projection and `pareto.relation`, oriented as candidate versus baseline.
`dominates` means the candidate improved at least one comparable axis and regressed none;
`dominated` is the inverse; `equivalent` means every
comparable axis is unchanged; and `incomparable` preserves a real trade-off instead of choosing
one with weights. `unavailable` means unclassified or incompatible evidence prevents the
requested comparison. Each axis retains its concrete improvements, regressions, and unavailable
reasons. The calculation never reads `score`, `diagnostic_score`, component `score`, or
`audited_score`. The top-level `decision` is retained temporarily as the deprecated v1 policy
result; new consumers use the Pareto relation and the separate certification limitations.

The `requirements` axis describes exact declared requirement/measurement transitions against
the caller-fixed denominator. The `completeness` axis separately describes the
recognition-owned outcome ledger and its bounded denominator. Keeping both prevents a change in
what recognition counted from masquerading as an improvement to what the drawing represents.

Pass `ExpectedRequirement(declaration_id, parameter_id)` values as a fixed denominator. This is
how a caller detects a requirement omitted from both drawings: neither drawing can rediscover an
expectation that both scripts deleted. Pass `IntentionalChange(...)` to separate an authorised
design change from incidental regressions, and `LayoutFindingIdentity(...)` when one layout
defect is the edit target. Semantic loss, physical-owner substitution, newly adverse
completeness/fidelity evidence, unclassified lint, or deletion used to clear layout remains
visible in its own axis or limitation. A layout improvement coupled to a coverage regression is
therefore `incomparable`, not numerically ranked. Incompatible authority makes the Pareto
relation unavailable. Restraint and manufacturing readiness remain explicitly unavailable and
cannot silently become a pass.

The bounded CTC-01 real-part canary exercises that loop before merge: generate an inspected
script, replay a traced baseline, make one sanctioned `Sheet` layout edit, replay once, and
compare against a reviewed 78-claim denominator. It deliberately proves that deleting the
crossed measurement or substituting another recognised owner is rejected. The counterfactuals
operate on the two captured documents, so the CI lane pays for exactly two CAD builds rather
than rebuilding the part for every policy failure.

The generated script removes an older Draftwright-owned assessment before importing or building
the part. Build, export, strict-report, identity, and write failures therefore leave no old
success document. A foreign or malformed file at the derived assessment path is preserved and
the replay refuses to overwrite it. The final JSON write is atomic.

The default generated script uses reproducible export and prints the assessment path after the
write succeeds. Replaying unchanged source and script bytes on one installed version therefore
produces byte-identical drawing outputs and assessment content. This is the narrow reproducible
export promise, not stability across Draftwright versions.

Pass `--no-report` while generating the script to omit both evidence behaviors. From Python,
`generate_sheet_script(..., assessment=False)` controls the replay artifact independently;
unless explicitly set, it follows `inspect=`. Regeneration
also removes an older tool-owned assessment because it describes the previous script; unrelated
files are never removed. A build123d object-source script can still assess its script and drawing,
but `source` explicitly says that no immutable STEP byte identity is available.
