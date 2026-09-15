# Test burden reduction plan

- **Status:** Ready
- **Baseline:** 8,809 collected items; 412 unit-tier items; 151 issue-named modules
- **Coverage policy:** 90% combined line-and-branch coverage and 90% changed-line coverage
- **Primary constraint:** preserve fault detection while reducing CAD construction and review latency

## Objective

Reduce the cost of the suite rather than its apparent size. The work succeeds when normal
pull requests receive faster, more specific feedback while the tests still detect the
semantic, recognition, placement, reporting, and export failures they detect today.

File count and raw line count are diagnostic information, not targets. The controlling
measures are wall time, CPU time, OCC shape constructions, repeated pipeline stages,
collected integration items, and retained fault detection.

## Quality floor

Every reduction must preserve all of these:

1. Global combined line-and-branch coverage remains at or above 90%.
2. Changed source lines remain at or above 90% line coverage.
3. The mutation corpus and the regressions relevant to the edited behavior still fail when
   their protected production rule is deliberately broken.
4. Public schemas, deterministic exports, refusal behavior, and capability evidence retain
   their contract tests.
5. Removing a test must not remove the only example of a distinct topology, boundary,
   failure mode, public format, or previously observed regression.

Coverage alone cannot approve a deletion. It proves execution, while the mutation and
behavior checks prove that the surviving assertions remain sensitive to faults.

## Measurements

The first change adds a machine-readable benchmark collected under the canonical Python
3.13 environment. Record, per test and cumulatively:

- setup, call, teardown, and total duration;
- OCC shape constructions;
- STEP imports and exports;
- recognition, analysis, compilation, drawing-build, lint, and format-export calls;
- part-recipe identity and build options;
- worker count and cache hit rate.

Store summaries as CI artifacts. Check in only the schema, collection code, and a small
human-readable baseline; do not commit volatile timing dumps. Compare medians from at least
three runs for performance decisions. A slice must improve its selected cost measure by at
least 10% or remove a demonstrated source of duplicate work.

## Delivery sequence

### 1. Make the unit loop collect only unit modules

`pytest -m unit` currently collects all 8,809 items before selecting 412 and takes about
81 seconds in this workspace. Generate the invocation from the central unit-module manifest
so pytest receives those paths directly. Add a guard that the direct-path set and
the marker-selected set have identical node IDs.

**Exit:** the unit command constructs no shapes, selects the same tests, and completes in
under 30 seconds on the canonical runner.

### 2. Eliminate repeated immutable pipeline work

Use the measurements to identify repeated `(part recipe, build options)` work. Cache safe,
immutable stages in this order:

1. STEP bytes and imported solids;
2. recognition evidence;
3. analysis and compiled plans;
4. immutable export-independent projections.

Do not share mutable `Drawing`, annotation registry, or repair state. Tests that mutate a
result must request an explicit fresh object. Add counters that prove each cache boundary is
used and tests that prove mutations cannot leak between consumers.

**Exit:** at least 30% fewer repeated recognition/drawing pipeline executions in the pull
request tier, with identical collected items and focused behavior results.

### 3. Minimize the real-geometry corpus

Pilot on the former #1372 completeness family, which currently costs roughly 27 minutes for
309 focused and capability tests. For every part, record a coverage signature containing
recognizer families, topology variants, requirement outcomes, compiler paths, lint codes,
and mutation kills. Select the smallest deterministic set that preserves the union of those
signatures.

Delete a corpus case only when another retained case covers its complete signature or when
the omitted dimension is represented by a cheaper pure test. Keep named regressions whose
historical fault is not reproduced by the covering case.

**Exit:** at least 20% fewer CAD builds in the pilot, unchanged contract coverage, and no
lost kills in its selected mutation set.

### 4. Replace Cartesian matrices with boundary and pairwise coverage

Inventory parametrized dimensions such as geometry, orientation, tolerance, authored mode,
failure disposition, and output format. Preserve every single-dimension boundary and use a
deterministic pairwise set for interactions. Retain a small number of complete end-to-end
combinations.

Each reduction records the old and new item counts and maps every removed combination to a
surviving boundary, pair, or pure-logic assertion.

**Exit:** at least 20% fewer collected integration items in the selected families without a
coverage or mutation regression.

### 5. Separate exporter semantics from format smoke tests

Test drawing semantics once before serialization. Give each exporter a compact contract for
format-specific entities, metadata, reproducibility, and failure handling. Run the complete
part-by-format product only in the scheduled compatibility tier.

**Exit:** each format retains direct contract and reproducibility evidence while pull-request
export calls fall by at least 50%.

### 6. Establish cost-aware execution tiers

Define three explicit manifests:

- **Pull request:** unit tests plus the minimal integration covering set selected by changed
  production areas.
- **Full:** all supported behavior and exporter contracts.
- **Scheduled corpus:** compatibility, stress, broad topology, and complete format matrices.

Changes to recognition, compilation, placement, reporting, or an exporter must select the
corresponding full contract group automatically. A manifest guard fails if a test belongs to
no tier or if a critical contract appears only in the scheduled tier.

**Exit:** the pull-request tier completes within 10 minutes with available parallel workers;
the full and scheduled tiers remain reproducible and independently runnable.

## Autonomous execution protocol

Work proceeds one independently reviewable commit at a time:

1. Reproduce and record the current metric.
2. Make one instrumentation, caching, corpus, matrix, exporter, or tiering change.
3. Review the diff for a single purpose and inspect every removed test individually.
4. Run focused tests, the affected mutation probes, coverage, collection, clone and suite
   shape guards, then the applicable execution tier.
5. Fix every finding before committing and record the before/after metric in the commit
   message or durable plan evidence.
6. Continue only when the change meets its exit condition. Revert reductions that lose a
   distinct behavior signal rather than compensating with a weaker assertion.

Run the sequence in order because instrumentation supplies evidence for every later choice.
Within a stage, choose the highest cumulative-cost duplication first. Stop and update this
plan if two consecutive slices fail to achieve a measurable reduction or expose an
unrepresented behavior; that indicates the model or quality floor needs revision.

## Completion criteria

The plan is complete when all of the following hold on the canonical runner:

- unit loop under 30 seconds;
- pull-request tier under 10 minutes;
- at least 30% fewer repeated CAD pipeline executions;
- at least 20% fewer collected integration items;
- at least 50% fewer pull-request exporter calls;
- global and changed-line coverage at or above 90%;
- no regression in the selected mutation corpus or retained historical regressions.
