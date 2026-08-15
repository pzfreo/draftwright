# Architecture decision records

This is the front door to draftwright's ADR corpus. Start with the **Current
architecture** table; open retired or superseded records only for design history.

## Maintenance rules

- Keep the status header to one load-bearing statement.
- At roughly four amendments, write a successor ADR and freeze the old record.
  The superseded file remains the historical why-trail; do not rewrite it.
- Cite symbols and test names, never source line numbers.
- If an invariant can be checked mechanically, guard it with a test. A claim
  that something is machine-checked must name the real check.
- Preserve reversals in the frozen record. The successor compresses each to one
  line and points back to the history.
- Update this index whenever an ADR is accepted, retired, or superseded, or when
  its guarding test changes.

## Current architecture

| ADR | Title | Decision | Status | Representative guards |
| --- | --- | --- | --- | --- |
| [0001](0001-deterministic-generation-over-editable-dsl.md) | Deterministic generation and domain-semantic editing over a bespoke editable-code DSL | Prefer deterministic generation and domain-semantic editing over a primitive editable-code DSL. | Accepted | `test_sheet_emit.py`, `test_make_drawing.py` |
| [0002](0002-iterate-via-lint-critique-and-domain-repair.md) | Iterate via lint critique and domain-semantic repair, not by editing generated code | Refine drawings through machine-readable lint and narrowly allowlisted domain repair. | Accepted | `test_linting.py`, `test_lint_structural.py`, `test_make_drawing.py` |
| [0004](0004-compose-then-pack-view-blocks.md) | Compose-then-pack: views as blocks carrying their annotation footprint | Compose each view with its annotation footprint, then pack fixed-topology blocks. | Accepted | `test_layout.py`, `test_layout_cleanliness.py`, `test_refactor_golden.py` |
| [0005](0005-pipeline-architecture-and-state-ownership.md) | Compiler-pipeline module boundaries and single-owner build state | Give compiler stages explicit module homes and build-time state explicit owners. | Accepted; implemented; compatibility aliases tracked by #720 | `test_import_boundaries.py`, `test_drawing_encapsulation.py`, `test_registry.py` |
| [0006](0006-deterministic-layout-via-bundled-fonts.md) | Deterministic cross-platform layout via bundled, path-pinned fonts | Pin bundled font files so text measurement and layout are cross-platform deterministic. | Accepted | `test_refactor_golden.py`, `test_layout_cleanliness.py` |
| [0007](0007-own-recognition-and-linting.md) | draftwright owns feature recognition and linting; helpers becomes the rendering library | Draftwright owns linting, recognition lifecycle, IR conversion, and drafting policy; `b123d-recognisers` owns geometry recognition; helpers remains the rendering library. | Accepted; extraction deployed by Amendment 2 | `test_import_boundaries.py`, `test_external_recognition_boundary.py`, `test_linting.py` |
| [0010](0010-annotation-provenance-seam.md) | Annotation provenance: record intent → annotation once, at the render seam | Record annotation provenance once at the render/add seam. | Accepted; landed | `test_render_seam.py`, `test_registry.py` |
| [0011](0011-ir-as-public-input.md) | The IR as a public input: declare features, don't only detect them | Accept the feature IR as public input through declarations and the `Sheet` façade. | Accepted; core landed; #62/#462/#495 remain | `test_declare.py`, `test_object_aspects.py`, `test_sheet_gdt.py` |
| [0012](0012-edits-as-pinned-priority-candidates-in-the-global-solve.md) | User annotation edits are pinned, priority-ranked corridor candidates | Drain recorded semantic edits through corridor placement with pin and priority. | Accepted; partially landed; full recomposition/parity remains #426/#707 (#661 detail views landed) | `test_make_drawing.py`, `test_sheet_emit.py` |
| [0013](0013-uniform-recognition-and-shared-package.md) | A uniform recogniser/feature contract and shared `b123d-recognisers` deployment | Enforce the geometry-only contract in the shared package and consume it through Draftwright's record→IR boundary. | Accepted; Phases 1–2 deployed | `test_external_recognition_boundary.py`, `test_detect_registry.py`, package semantic goldens |
| [0014](0014-collect-then-solve-annotation-placement.md) | Collect-then-solve annotation placement (as built) | Collect, select, assign, and deterministically solve annotations per corridor before rendering. | Accepted; supersedes 0009 | `test_carve_free_position_callers.py`, `test_strip_layout.py`, `test_layout_property.py`, `test_import_boundaries.py` |
| [0015](0015-part-drawing-compiler-as-built.md) | The part-drawing compiler, as built | Use one detected-or-declared feature IR and planner-fed dimension groups as the compiler waist. | Accepted; supersedes 0008 | `test_part_model.py`, `test_detect_once.py`, `test_import_boundaries.py` |
| [0016](0016-declared-dimensioning-intent.md) | Declared dimensioning intent: capture what to measure, let the engine place it | Declare which measurements matter as scale-independent intent routed through the planner and corridor solve; never hardcode dimension geometry. | Accepted; epic #867 complete; phase 6 landed (#940) | `test_compiled_plan_boundary.py`, `test_label_provenance.py`, `test_sheet_emit.py`, `test_add_dimension.py` |
| [0017](0017-recognition-inventory-correspondence-and-measurement-provenance.md) | One recognition result per run; correspondence is evidence-gated | Produce one external immutable recognition result held by Draftwright's per-run cache; require vertical-slice evidence before generalising correspondence, identity, requirements, outcomes, or reconciliation. | Accepted; external cache ownership clarified, extensions gated by #1018 | `test_external_recognition_boundary.py`, `test_recognition_manifest.py`, `test_declared_recognition_gate.py` |
| [0018](0018-requirement-driven-view-planning-and-editable-sheet-layout.md) | Requirement-driven view planning and editable sheet layout | Use one `ViewSpec` vocabulary and planner with distinct authored `ViewConstraints` and immutable `ResolvedViewPlan`; jointly validate views, typography, convention, scale, paper and layout against requirement survival. If accepted, supersedes ADR 0004's fixed-topology assumption while retaining compose-then-pack. | Proposed; tracked by #1130 | Required guards are listed in the ADR |

## Historical records

| ADR | Title | Historical decision | Status | Read instead |
| --- | --- | --- | --- | --- |
| [0003](0003-constraint-based-layout.md) | Constraint-based layout: one solver for every placeable | Explored a universal `Placeable`/`LayoutSolver` and page-global constraint solve. | Retired; carrier deleted and #94 closed as unnecessary | [0004](0004-compose-then-pack-view-blocks.md) for outer layout and [0014](0014-collect-then-solve-annotation-placement.md) for inner placement |
| [0008](0008-unified-feature-model-and-dimensioning-planner.md) | The part-drawing compiler: a Feature/DimParameter IR and a dimensioning planner | Introduced the feature/parameter IR and dimensioning-planner direction. | Superseded by 0015; frozen | [0015](0015-part-drawing-compiler-as-built.md) |
| [0009](0009-boundary-labeling-strip-placement.md) | Boundary labeling: collect-then-solve per-strip annotation placement | Developed collect-then-solve boundary-label placement through nine amendments. | Superseded by 0014; frozen | [0014](0014-collect-then-solve-annotation-placement.md) |

## Reading paths

- Compiler and state ownership: 0001 → 0005 → 0015.
- Recognition and public declaration: 0007 → 0013 → 0011 → 0015.
- Layout and placement: 0004 → 0014 → 0012 → 0018.
- Declared intent and the editable surface: 0001 → 0011 → 0012 → 0016 → 0018.
- Quality and correction: 0002, with provenance from 0010.
- Recognition correspondence and completeness: 0007 → 0013 → 0015 → 0017.

Tracking issue: #745.
