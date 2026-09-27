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
also passed the historical 12-win metric gate with 13 wins, two ties, and
semantic parity on every selection. This predates #1813's current requirement
for 10 visually verified candidate-first improvements. A generated CTC01 `Sheet`
script using the corpus's explicit
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

The agreed #1813 direction is one candidate build on an ordinary request, with
an explicit baseline mode and the two-build `"best"` mode retained for offline
comparison. The pre-render chooser now selects a profile from typed demand and
settled page/scale before the candidate is drawn; the current
`"candidate-preview"` mode is observational and does **not** change the public
default. Its independent completeness checks report unmet requirements even
when baseline would have the same defect. Such a shared limitation is not a
candidate-specific regression and does not trigger a baseline rerender.

An automatic fallback is **not** a prerequisite for the default switch. Any
temporary exception needs offline evidence that baseline actually recovers a
candidate-specific failure under the same caller page, scale and policy. The
normal path must ultimately remain one build with no automatic fallback. A
single build cannot prove its relative parity to an unbuilt baseline, so
fixed-sheet paired and sampled shadow evidence remain separate rollout gates.
The independent checker must report what it cannot establish; its current
`admission_ready=false` is not a verdict that a shared CTC defect should reject
candidate. See [ADR 5](../adr/0005-trust-and-honest-failure.md) for honest
failure and the [#1813 epic](https://github.com/pzfreo/draftwright/issues/1813)
for the current completion criteria.

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
These measurements are evidence inputs, not an admission verdict. The
[pre-rollout numerical budget](1813-candidate-cost-budget.md) is now set; its
supported-platform, large-part and concurrency gates remain open. If no
temporary fallback is introduced, fallback frequency is not a required
production measurement.

## Gates for a default switch

1. Finish and validate the versioned pre-render chooser and independent
   completeness evidence. Record the chosen profile, failed checks, limitations,
   and resolved page/scale in the machine-readable drawing report for either
   algorithm. A shared defect is reported, not treated as a candidate regression.
2. Run that actual candidate-first policy on the fixed-sheet 15-part corpus
   and a broader user-part corpus. Use paired builds **offline** to prove at
   least **10 of 15 clear, visually verified improvements**, no material visual
   regressions, no selected semantic loss, and no introduced required blockers.
   CTC02/CTC04 may be explicit non-regressions rather than forced wins. Test
   failed, sparse, authored, and recognition-gap drawings under the same honest
   completeness policy; the present `"best"` result is not candidate-first proof.
3. Add legibility evidence for long routed leaders, isometric shrinkage, and
   minimum view size; visually review borderline cases such as CTC02.
4. Measure candidate-only end-to-end latency and peak memory across typical
   and large parts on every supported platform against the already-declared
   [cost budget](1813-candidate-cost-budget.md). Measure fallback frequency
   and fallback-inclusive latency only if a justified temporary fallback exists.
5. Expand typed scheme coverage until unplanned annotation families are rare,
   and prove the lane-order path against the finished renderer and lint.
   Roll out with shadow comparisons and a reversible policy switch, then
   change the API, Sheet, generated scripts, and CLI defaults together after
   exact-head full and slow CI passes. Keep normal requests one-build.
