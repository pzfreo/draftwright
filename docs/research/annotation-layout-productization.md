# Annotation layout productization assessment

This assessment records the original API names. The current planning algorithms
are `estimated-strips` (formerly `baseline`) and `demand-guided` (formerly
`candidate-preview`); `compare` (formerly `best`) builds alternatives and selects
one. The original names remain accepted aliases. Rollout policy is tracked in
issue #1813, which supersedes this document's earlier fallback proposal.

## What the candidate currently is

The candidate uses the existing annotation renderer and placement solve. A typed
`AnnotationScheme` estimates corridor demand before rendering; a build-scoped profile
can cap selected legacy reservations and enable the staggered side arrangement,
exterior dimensions, bounded leader recovery, plan X routing, horizontal
dimension tier sharing and vacant-tier compaction where the rendered ink is
clear, verified normal leaders on bevels and arcs, radial hole recovery, and
measured isometric growth with 5 mm clearance from neighbouring ink and view
outlines. The dense column arrangement retains its established tier and leader
choices after the new compaction displaced two CTC05 location dimensions. The typed lane
order remains a separate experimental path. This is one engine with alternative
planning inputs, rather than a replacement renderer.

The scheme explicitly reports requests it cannot yet plan. On the fixed-sheet
corpus, CTC05 has 21 unplanned requests and CTC02 has 76. Those gaps should be
closed before the scheme alone controls all annotation lanes.

## Mergeable product slice

`annotation_layout="best"` is available through `build_drawing`, `make_drawing`,
`Sheet`, generated scripts, and `--annotation-layout best` in the CLI. It first
settles the existing drawing. Candidate trials use that exact page and scale, and
the selector accepts the first strict improvement only after checking the
finished drawing for semantic annotation parity, no loss of required coverage,
no new required blocker, and no newly introduced interior dimension. A clean
baseline can gain a larger isometric view if its orthographic bounds and other
quality measures are unchanged. Failed or inferior proposals retain baseline.
`Drawing.annotation_scheme_decision` records the trials and the chosen layout.

The default remains `"baseline"` in this PR. The opt-in `"best"` mode is a
comparative gate: it needs at least two drawing solves and can need four on a
crowded part. Only the selected drawing is exported. Builds are scoped with a
`ContextVar`, so process environment switches are not part of the public API.
The established build still enforces the caller's `scale_policy` before any
comparison. A declared script that already fails under `"fallback"` at an
explicit scale needs a feasible scale or an explicit `"permissive"` policy;
layout selection does not silently relax that contract.

## Evidence and limits

The 15-case versioned corpus holds the sheet and scale fixed. After the routed
CTC05 callout was given the semantic label it already rendered, the comparison
runner found 13 measured wins and two ties, with no selected semantic loss. The
metric puts hard layout defects, required blockers, overlap, interior dimensions,
and crossings ahead of compactness. It credits a larger isometric view only on
an otherwise clean sheet.
The same 15 cases run through public `build_drawing(annotation_layout="best")`
also pass the 12-win gate with 13 wins, two ties, and semantic parity on every
selection. A generated CTC01 `Sheet` script using the corpus's explicit
`"permissive"` scale policy reproduces the selected quality key.

The result is relative. CTC02 still has 43 required blockers after its measured
improvement, and CTC04 still has 38. CTC02's selected columns view also has a
smaller isometric and a long leader; visual review is mixed. This corpus proves
that a guarded selector often improves the established drawing. It does not prove
that all selected drawings are manufacturing-ready or that the candidate alone
is safe as the default.

Some front/plan view pairs use the 20 mm minimum geometry gap, which can look
tight after annotations occupy their bands. A uniform 5 mm increase caused
CTC02 to lose required content and reduced the public corpus from 13 wins to
11. More breathing room needs a per-drawing slack check rather than a global
gap increase.

An initial CTC05 selection took 101.2 seconds for baseline plus three trials;
baseline alone took 13.9 seconds with the same build options. Trying the
semantically promising columns variant first when hard layout defects and
interior dimensions coexist reduced the final selector to 36.6 seconds and
one candidate trial. Large native CAD models still make simultaneous
in-process drawings a memory concern.

## Intended default path: candidate first

The chosen product direction is one candidate build first, with a baseline build
only when the candidate fails a standalone safety check. Once offline evidence
shows that fallback has become rare and the standalone gate catches its failures,
remove the fallback and run one candidate build on every normal request. The two-build `"best"`
mode remains useful as an opt-in comparison and shadow evaluation path. A
candidate-first build should normally pay for one drawing solve; the fallback
case pays for two. Measure that distribution rather than treating the CTC05
two-build timing as the default-path cost.

This path needs a profile decision before a finished baseline exists. The 15
current selections comprise five `planned`, three `columns`, three
`legacy-depth`, two `iso-growth`, and two retained baselines. The present
selector uses baseline quality and interior dimensions to choose among those
profiles. A candidate-first policy must choose from typed annotation demand,
view planning, fixed page/scale, and other pre-render facts. It cannot rely on
the finished baseline's quality key. Sheet and scale resolution should be
shared between candidate and fallback, so fallback does not silently change
the caller's scale policy or gain room from a larger sheet.

The candidate-only safety check must use independent obligations: recognized
and authored feature coverage, required annotation outcomes, lint blockers,
off-sheet ink, overlap, crossing and leader legibility limits, and a minimum
view-size test. A failed candidate triggers the established build under the
same caller policy, with the reason recorded. The fallback can itself have
unresolved requirements; CTC02 and CTC04 show why an absolute zero-defect
threshold would reject some relative gains without producing a complete
fallback. Define that behavior explicitly and test it. Without running both
drawings, the engine cannot prove a relative improvement or semantic parity to
baseline on that individual call; paired offline and sampled shadow runs must
continue to measure those regressions.

### Offline cost evidence

The candidate-first corpus worker records build, export, whole-worker, and isolated-process
wall time for each case, plus peak resident memory and host OS, architecture, Python version,
logical CPU/physical core counts, and physical RAM. The corpus summary reports sample counts, median, and
nearest-rank p95 separately for candidate process/build time, baseline process time, and
candidate peak memory. A failed candidate retains its process time in a separate distribution.
Linux/macOS use `resource.ru_maxrss`; Windows uses `psutil`'s peak working set. The isolated
process time includes interpreter startup and report transfer; build time does not. Comparisons
must use the same fixed page/scale, export formats, and host class, and must record the runner's
concurrency because concurrent CAD processes compete for memory and CPU. In preview mode the
fallback rate is `null`, not zero: automatic fallback has not been implemented or measured.
These measurements are evidence inputs, not a production budget or admission verdict; numerical
latency and memory budgets still need to be set before changing defaults.

## Gates for a default switch

1. Implement and version the pre-render profile chooser and candidate-only
   safety verdict. Record the chosen profile, gate results, fallback reason,
   and resolved page/scale in the machine-readable drawing report.
2. Run that actual candidate-first policy on the fixed-sheet 15-part corpus
   and a broader user-part corpus. Use paired builds offline to prove at least
   12 of 15 verified gains, no semantic loss, and safe fallback on failed,
   sparse, authored, and recognition-gap drawings. The present `"best"` result
   does not establish that candidate-first result.
3. Add legibility evidence for long routed leaders, isometric shrinkage, and
   minimum view size; visually review borderline cases such as CTC02.
4. Measure candidate-only build time, fallback frequency, total latency, and
   peak memory across typical and large parts on every supported platform.
   Reuse recognition and projection where possible and set a cost budget.
5. Expand typed scheme coverage until unplanned annotation families are rare,
   and prove the lane-order path against the finished renderer and lint.
   Roll out with shadow comparisons and a reversible policy switch, then
   change the API, Sheet, and CLI defaults together after exact-head full and
   slow CI passes.
