# Target architecture

draftwright is a **part-drawing compiler**: it turns a build123d B-rep solid or
caller-declared feature model into a deterministic, standards-aware technical
drawing. [ADR 0015](adr/0015-part-drawing-compiler-as-built.md) is the current
compiler contract; ADR 0008 is its frozen historical why-trail.

## The pipeline

```text
 recognised geometry                 declared features
 (ADR 0013 records)                  (ADR 0011 constructors)
          │                              │
          └──────────────┐    ┌──────────────┘
                         ▼    ▼
                   PartModel IR waist
                 (one inventory per build)
                         │
          ┌───────────────┴───────────────┐
          ▼                               ▼
 dimension planners                 sanctioned model-routed
 (groups and section plans)         furniture / aspect passes
          └───────────────┐   ┌───────────────┘
                          ▼   ▼
                    render intents
                          │
          shared placement / projection / export
                          │
                          ▼
                       Drawing
```

Detection runs once for a build. `model/detect.py` adapts geometry-only
recognition records into frozen IR features; declaration enters through the same
waist. Recognition records do not cross that boundary. ADR 0013 still owns the
pending typed adapter-registry refinement.

## Planning and rendering

The planner maps a feature's typed `DimParameter`s to `DimensionGroup`s,
choosing conventions and views from semantic roles and feature frames. Issue
#698 completed its planned migrations of dimension-bearing feature passes.
Holes/patterns, locations, turned and boss diameters,
envelopes, step lengths, chamfers, fillets, flats, grooves, pockets, plates,
slots, and section triggers consume planner output.

Some rendering remains model-routed:

- rotational centrelines are furniture, but rotational OD/bore dimensions still
  discard computed planner groups; that residual debt is tracked by #754;
- pre-authored PMI exposes no dimension parameters and is model-routed by design;
- correlated step-height and step-position sets, which must not be flattened
  into independent dimensions, are model-routed by design even though their
  computed groups are not consumed;
- GD&T and finish placement intents, which are not `DimParameter`s.

Adding a feature kind therefore means adding the applicable recognition and/or
declaration surface, IR conversion, planner convention when it has dimension
parameters, renderer or stage support, coverage, and tests. Orientation and view
selection remain data-driven; “new feature” does not mean “zero back-end work.”

## Placement and page composition

- **Inner placement** is ADR 0014's collect-then-solve corridor model. Measured
  candidates sharing a strip are selected, ordered, and spaced together; its
  small `carve_free_position` exemption set is guarded fail-closed.
- **Outer layout** is ADR 0004's compose-then-pack model. Projected geometry and
  annotation footprints form disjoint view blocks before page and scale are
  chosen.
- Text measurement uses bundled, path-pinned fonts (ADR 0006), so layout is
  deterministic across platforms.

## Verification

Lint is an independent judge, not another consumer of the dimensioning plan.
Structural and standards checks inspect the placed drawing. Coverage lint
deliberately runs recognition to establish geometry ground truth, then compares
that truth with witnesses on the placed drawing. Reading the plan or `PartModel`
would be circular: anything omitted upstream would also disappear from the
judge's input.

The boundary is machine-checked: `linting/` may not import `draftwright.model`.
This is ADR 0015's intentional lint/coverage carve-out, not a second production
feature inventory.

## Load-bearing rules

- One feature inventory per build; detect once.
- Detection and declaration converge on the same frozen `PartModel` IR.
- Recognition records cross only at the sanctioned `model/detect.py` adapter
  boundary.
- Dimension-bearing feature passes use planner output unless an ADR records a
  correlated-set exception; #754 tracks the remaining rotational exception.
- Orientation and view choice are derived from feature data, not duplicated by
  producer or axis.
- Render intents feed shared placement, projection, tables, sections, and export;
  the IR does not absorb that infrastructure.
- Correctness is judged by lint and standards, not byte identity with an older
  drawing.
- Generation and repair are deterministic; repair is a safety net, not the
  primary placement strategy.

For rationale and precise boundaries, read the
[ADR index](adr/README.md), especially ADRs 0011–0015. Historical migration
roadmaps under `docs/plans/` explain how the architecture arrived here; they are
not current implementation trackers.
