# Annotation layout productization assessment

## What the candidate currently is

The candidate uses the existing annotation renderer and placement solve. A typed
`AnnotationScheme` estimates corridor demand before rendering; a build-scoped profile
can cap selected legacy reservations and enable the staggered side arrangement,
exterior dimensions, bounded leader recovery, and plan X routing. The typed lane
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

The default remains `"baseline"` in this PR. A full default switch would make
every build perform at least two drawing solves; a crowded part can use four.
Only the selected drawing is exported. Builds are scoped with a `ContextVar`,
so process environment switches are not part of the public API.
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

An initial CTC05 selection took 101.2 seconds for baseline plus three trials;
baseline alone took 13.9 seconds with the same build options. Trying the
semantically promising columns variant first when hard layout defects and
interior dimensions coexist reduced the final selector to 36.6 seconds and
one candidate trial. Large native CAD models still make simultaneous
in-process drawings a memory concern.

## Gates for a default switch

1. Check the public selector on the versioned corpus and a broader set of user
   parts, including drawings with no recognized features, authored views, and
   failures. Preserve same-sheet and same-scale parity on every selection.
2. Add legibility evidence for long routed leaders and isometric shrinkage, then
   review borderline wins such as CTC02 visually.
3. Reduce repeated recognition, projection, and placement work, and measure
   build time and peak memory across typical and large parts on each supported
   platform. The default must have an explicit cost budget.
4. Expand typed scheme coverage until unplanned annotation families are rare,
   and prove the lane-order path against the finished renderer and lint.
5. Include the selection decision in the versioned machine-readable report,
   document rollout and rollback, then change the API, Sheet, and CLI defaults
   together after the full and slow suites pass at that exact head.
