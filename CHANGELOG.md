# Changelog

## Unreleased

### Changed

- Updated the exact `b123d-recognisers` production lock from 0.2.6 to 0.2.9.
  The immutable PyPI wheel/sdist hashes and capability-manifest digest `805876905d548fd0ef1301e2cf24427a86d34bc2e592b8d2d0cb43db94e1d3bd` are
  recorded in `.github/recogniser-release.json`; focused compatibility evidence and the
  normal consumer gates run on the generated PR. The aggregate now inventories chamfers and
  fillets on turned parts, and the fixed coaxial-post slot regression retires its downstream
  strict xfail.

### Fixed

- Turned chamfers returned by the shared recognition inventory now lower into the same
  one-feature-per-edge-treatment IR as prismatic chamfers. Equal specifications render as one
  `n×` callout carrying every member's measurement provenance; different chamfer forms do not
  collapse merely because their first legs match, and distinct tolerances split into truthful
  requirements instead of being lost or applied to untoleranced siblings (#1254).

- Recogniser dependency automation now starts a fresh `Unreleased` section when the previous
  Draftwright release has finalized the changelog, so a published recogniser can enter through
  the documented immutable update workflow instead of failing before it opens a PR (#1279).

## v0.4.9 — 2026-08-21

### Added

- Lint now verifies that an annotation claiming to carry a measurement actually renders it
  (#1217). Coverage checks read provenance riders the annotations carry about themselves and
  believed them; nothing checked the claim against what the annotation draws. Four new codes —
  `claimed_value_absent`, `claimed_measurement_not_compiled`, and two that report an
  unverifiable claim rather than passing it — and `Drawing.add_table` now keeps the rows it
  draws so a table's claims are readable back. Measured over all 21 STEP fixtures: 1,081
  claims, 1,078 confirmed, and three findings — all instances of one genuine provenance
  defect, see #1219.

- `lint_summary()["quality"]` gains a fourth component, `fidelity`: whether what the drawing
  says is TRUE. The existing three ask whether required content landed (completeness),
  whether there is too much of it (restraint), and whether a reader can make it out
  (legibility) — none asked whether it is correct, so a drawing labelled 99 over a 16 mm
  path reported a perfect score on every component available to it. Also covers a callout
  drawn for a feature the part does not have, and a gear data table stating a tooth count
  the geometry contradicts — the last of which reports `passed: True` and zero errors, so
  the only existing channel that names it is `by_code`. Completeness, restraint and fidelity
  report `available: False` with a `reason` rather than a flattering number when they have no
  evidence, and every lint code the engine emits is classified onto exactly one component,
  an explicit unscored register, or a register of codes whose component depends on the
  issue's `outcome_stage`. `quality["unscored"]` reports the findings that reached no
  component at all and flags any whose classification nobody made — `section_dropped` was
  found scoring on nothing while a comment beside its emission said the opposite, and eleven
  more `*_dropped` codes reach the engine as `drop_code` data with the same shape (#1176).

### Changed

- Updated the exact `b123d-recognisers` production lock from 0.2.4 to 0.2.6.
  The immutable PyPI wheel/sdist hashes and capability-manifest digest `aa52bc04216cd1eccc05796d2262547c7f476b80a3ec77334a77b9d8e3241a58` are
  recorded in `.github/recogniser-release.json`; focused compatibility evidence and the
  normal consumer gates run on the generated PR.
- `HoleRequirementOutcome` gains `members` and `features`, `ClaimOutcome` gains `measurement`,
  and `linting.hole_coverage.canonical_hole_sites` is public (#1217). A consumer attributing a
  requirement outcome to a specific recognised hole needs the evidence the outcome accounts
  for, and needs to key into the canonical space the ledger publishes it in — a through hole's
  own-axis coordinate is zeroed there.
- The STEP-analysis benchmark's `drawing_consumer` boundary now consumes
  `hole_requirement_outcomes` instead of a second correspondence implementation of its own
  (#1206), and follows the ledger's pointer through `linting.evidence` rather than trusting it.
  Holes the ledger reports `unverifiable` now score `unknown` where the duplicate credited them
  by guessing: 26 holes, five on each CTC-03 fixture and eight on each CTC-04 fixture. The
  five-case evaluation corpus is unchanged, and one lint message on CTC-03 now reports its
  coordinates in the ledger's own canonical space.

- `Drawing.suppressions()` rows gain a `conveyed_by` key: the dimension that states a
  withheld measurement instead, or `None` when nothing takes the fact over (#1154). It is not
  a synonym for the existing `authored` flag — that says whose decision it was, this says
  where the measurement went.

- Updated the exact `b123d-recognisers` production lock from 0.2.2 to 0.2.4.
  The immutable PyPI wheel/sdist hashes and capability-manifest digest `d0023d30e583c03abc55f09dfeaaa56fddf56334afa0417818480b2fa2ce3f0f` are
  recorded in `.github/recogniser-release.json`; focused compatibility evidence and the
  normal consumer gates run on the generated PR.
- Updated the exact `b123d-recognisers` production lock from 0.2.1 to 0.2.2.
  The immutable PyPI wheel/sdist hashes and capability-manifest digest `7985befc8572bfe6d1c0805dfdf690d74a5a4a5f14d5988cc29c366315ff04ca` are
  recorded in `.github/recogniser-release.json`; focused compatibility evidence and the
  normal consumer gates run on the generated PR.

### Fixed

- An authored tolerance the compiler approved now reaches the sheet wherever the measurement
  is drawn (#1215, #1216). `build123d_drafting` resolves a dimension's text as
  `label if label is not None else _number_with_units(measured, tolerance)`, and every
  dimension this engine emits passes a label — so a forwarded `tolerance=` rendered nothing
  while type-checking and reading back correctly from `Dimension.label`. Ten render paths
  discarded the band that way: envelope extents, the height ladder, the turned-step chain, two
  public `Drawing` verbs, the deferred corridor route, the detail-view redraw, the short-rise
  escape, step shoulders, pattern pitch, compound-callout recess terms, grid pitch, and both
  hole tables. A general guard now decorates every parameter of every feature across a corpus,
  through both `decorations=` key shapes, and joins what the compiler approved against what the
  claiming annotation renders.

- Collapsed `N×` marks no longer state a tolerance their members do not share (#1216). A
  pattern's pitch claimed the authored band of every gap although the recogniser admits 2%
  jitter — holes at −30, −10, 10.3, 30 printed `3× 20 ±0.1` over a 20.3 mm gap — and fillet and
  flat collapses took the "first-authored" band, so one of four fillets at ±0.1 printed
  `4× R5 ±0.1`. Equality is now judged at the drawn precision, and a withheld band is reported
  (`pattern_pitch_tolerance_withheld`, `collapsed_tolerance_withheld`) rather than dropped
  silently.

- An approved dimension that cannot be placed is now reported instead of vanishing (#1216).
  The overall extents' corridor candidate carried a no-op drop handler — the only drop in the
  engine that recorded nothing — and the step ladder's legibility floor skipped rungs without
  incrementing any counter. Both now record against the measurement they concern, and a
  withholding is withdrawn if a later pass draws the measurement after all.

- A feature leader filling a view's below corridor no longer costs the part its overall extent
  (#1236). The mandatory width or depth now retries on the view's above strip after every
  corridor has drained, the fallthrough starved slot and plate dimensions already use. Live on
  three NIST CTC fixtures, which drew no overall width.

- The iso view and the above strips are no longer invisible to each other (#1240). Strip
  placement never saw the iso, and the iso fit re-scaled after annotation without consulting
  what had been placed — so growth could invade dimension ink, which it did on CTC-01. The fit
  is now capped by measured re-projection rather than by a linear model of it: the projected
  bbox translates as it scales, by up to 7.35 mm on a part carrying a Location, which no
  prediction about the page centre can express.


- A part whose feature-local extent runs between the same two faces as an overall extent
  is no longer dimensioned twice (#1154). GRM-04's drive plate printed `4.5` twice — once
  as the hub's height and once as the overall thickness — because two detected records
  claim one physical fact. The overall extent keeps it; the feature-local one is withheld
  and records which dimension the reader finds it on. Reconciliation is by exact
  support-plane coincidence, never by value: a square part's two equal extents run between
  different faces and remain two facts (#997). A measurement is handed over only when the
  extent receiving it is actually drawn — by the planner's rules, by the compiler's
  overall-height rules, and by an authored set — and never when the yielding dimension
  carries a tolerance, since a toleranced dimension and an untoleranced one are not the same
  requirement. (A tolerance on the *receiving* extent is the same requirement on the same two
  faces, and does not prevent the consolidation.)

## v0.4.8 — 2026-08-16

### Fixed

- Compatible automatic/deferred sparse ordinary side/plan hole and post-drain
  machined-feature callouts now share one bounded same-view leader inventory
  (#1166). Pattern, profiled-bore, and dense table-eligible hole queues retain
  their specialised established paths because their winners affect downstream
  furniture/table semantics or await #798 silhouette-aware routing. The solve
  preserves required/priority-ranked evidence, rejects new-leader crossings,
  checks complete leader ink against actual-width fixed dimension/witness,
  arrow, label, and centre-furniture components (including rendered arrows on
  shifted dimensions and component-local dashed circular centre furniture);
  fixed residual faces and the selected survivor both require each tessellation
  triangle to fit one same analytical component rather than merely the union,
  plus exact continuous OCC containment for curved faces; residual lowering
  also retains filled datum/GD&T faces absent from segment metadata; if candidate
  or selected-survivor construction raises, or a survivor fails validation, the
  bad alternative fails closed and the canonical lazy producer tail is replayed
  instead of falsely dropping an otherwise valid callout; an all-invalid tail is
  reported as validation-stage geometry failure rather than no clear room (so
  scale fallback does not retry a non-spatial helper defect), while compiler
  invariant violations such as a missing semantic callout label remain loud;
  only explicitly marked global turning axes permit an arrow-local leader-tip
  attachment; near-collinear shaft travel and ownerless section/cutting lines
  remain fixed ink. The solve prefers
  clear routes over explicit Policy-B fallbacks, and then minimises real leader
  length. Provisional sections cannot veto that primary result: one separately
  bounded refinement may choose an equally complete leader inventory that clears
  their exact components, after which the final section performs one bounded
  end-symbol repair and yields if exact ink still conflicts. The selected OCC survivor is validated against the same analytical
  ink contract. A retained fixed-ink crossing now emits the machine-readable
  `feature_leader_crossing` info finding instead of appearing lint-clean. Candidate,
  fixed-component lowering, fixed-obstacle-probe, pair, and exact-search work is
  bounded; the fixed inventory is lowered once and reused with the producer's
  legacy lazy result as the floor, including state-cap exhaustion; page,
  own-silhouette, and title-reservation constraints remain hard in every fallback.
  A replay that exhausts exact fixed-probe classification is retained visibly as
  `feature_leader_fixed_ink_unverified` rather than rescanning past the cap, and
  that uncertainty participates in the legibility quality inventory.
  Trace output preserves an abandoned fully admitted inventory when state search
  exhausts, records the producer-floor replay separately, and names exact committed/provisional components,
  refinement status, and competing blockers; live
  single-callout edits remain immediate.

## v0.4.7 — 2026-08-16

### Added

- Recogniser co-development now has a one-command, wheel-based two-checkout check; an exact
  registry-release evidence record; automated dependency PR preparation; and documented landing,
  compatibility, deprecation, rollback, ownership, and CI-budget rules. Dependency PRs retain the
  canonical coverage/Codecov gate while avoiding four duplicate platform suites and the slow tier
  (#1170).
- A versioned, independently authored STEP-analysis corpus and evaluator now report detection
  recall/false positives, parameter fidelity, and downstream usefulness as separate evidence
  layers. Hash-pinned positive, negative, ambiguous, compound, and topology-order-variant fixtures,
  per-layer diagnostics, and an anti-self-validation mutation prevent recogniser output from
  defining its own denominator (#1169).
- Draftwright now validates the released `b123d-recognisers` 0.2.0 capability manifest against
  an exhaustive consumer-owned declaration of IR, DSL, generated-code, drawing, completeness,
  and documentation states. Unknown families, stale implementations, record-schema drift, and
  unevidenced state transitions fail closed; intentionally geometry-only repeating-profile
  evidence remains usable without acquiring invented drafting semantics.

### Changed

- Updated the exact `b123d-recognisers` production lock from 0.2.0 to 0.2.1.
  The immutable PyPI wheel/sdist hashes and capability-manifest digest
  `383a196329ed6a3d1bf88f18ae4ff75613c032f35d40f2b53302b6744da4d19e` are
  recorded in `.github/recogniser-release.json`; focused compatibility evidence and the
  normal consumer gates run on the generated PR.

### Fixed

- Stable release and TestPyPI snapshot builds now update package metadata and the independently
  validated Draftwright consumer-contract identity atomically. This prevents stripped `.dev0`
  and numbered development artifacts from failing their own capability-contract validation.
- Installed wheels validate every portable recogniser-contract property without requiring the
  repository-only behavior-test files whose existence remains a strict source/CI gate. This keeps
  the packaged STEP-analysis evaluator usable without weakening evidence checks in development.
- Post-release development-version bumps now update the consumer-contract identity on a branch,
  open a protected PR, and dispatch the narrow maintenance gate instead of attempting a rejected
  direct push to protected `main` (#1170).
- Machined-feature leader callouts now collect all clear alternatives within each
  post-drain pass and use a bounded maximum-cardinality, minimum-leader-length
  assignment instead of taking corners greedily in feature order (#740). If a
  deterministic alternative-count, pass-size, candidate-pair, or search budget is
  reached, placement retains the legacy greedy result or a result no worse than it
  rather than losing additional callouts. Pre-drain diameter clearance ranking and
  its Policy-B fallback remain unchanged.

## v0.4.6 — 2026-08-15

### Changed

- **Geometry recognition is supplied by the standalone Apache-2.0
  [`b123d-recognisers`](https://github.com/pzfreo/b123d-recognisers) package.** The cutover
  pins the published stable release `v0.1.0` (built from commit
  `9e622716c14d491729b5191aa6e5d8351c982a51`), removes the
  duplicate embedded implementation, and keeps only identity-preserving
  `draftwright.recognition` / `draftwright.score` compatibility re-exports until 0.6.0.
  Draftwright continues to own the one-result build/lint cache, record→IR conversion, drafting
  policy, placement, and critique. The package migration is manifest-pinned and golden-backed;
  its sole normalization makes an exact dominant-axis tie deterministic across operating
  systems without changing feature policy.

### Fixed

- Hole-callout obstacle checks now account for the rendered leader shaft width and
  tip-local arrowhead flare instead of treating the leader as a zero-width centreline
  (#367). The analytical check remains geometry-only and does not over-reserve the
  arrowhead's clearance along the whole shaft.
- Exact X/Z slanted flats retain their established lint-clean side-view callout after the
  recogniser package's cross-platform semantic-axis normalization. The normalized recognition
  record remains unchanged; `FlatFeature.presentation_axis` carries the downstream drawing-view
  choice, preventing a leader/silhouette crossing without duplicating recognition.

## v0.4.5 — 2026-08-15

**A focused technical-drawing correctness patch.** Cross-drilled hole locations now remain
complete and ordered, datum-starting pocket annotations avoid redundant dimensions, and repeated
equal-radius fillets collapse into one manufacturing callout.

### Fixed

- Side-drilled hole heights that relocate around callout leaders now join the alternate
  view's shared dimension ladder before placement, preserving every height and keeping
  feature locations inside the overall dimension. Datum-starting open pockets no longer
  print a redundant half-length centre offset, and equal-radius fillets across different
  edge axes collapse into one counted ``n× R`` callout.

## v0.4.4 — 2026-08-15

**A semantic-completeness and fail-closed layout patch release.** Hole requirements and drawing
quality are now machine-readable, hole-table replacement is transactional, explicit scales
preserve required output, notes tables use all viable sheet space, and gear declarations,
recognition evidence, and callout metadata improve diagnostic trust.

### Added

- **External spur gears can be declared through the `Sheet` façade** (#1086). The declaration
  records the correlated ISO 1328-1 manufacturing requirements, renders a structured gear-data
  table, and survives script emission, declaration, export, and lint without inferring gear data
  that the caller did not supply.

- **Recognition carries serialisable repeating radial-profile evidence** (#1087). Exact
  rotational whole-wire proof can reconcile a declared gear with its physical profile while
  preserving ambiguity across separate bodies and without inventing module, pressure angle, or
  other manufacturing intent.

- **Explicit drawing scales now protect required annotation completeness** (#1146).
  `build_drawing(...)`, `make_drawing(...)`, `Sheet(...)`, and the CLI accept
  `scale_policy="fallback"|"strict"|"permissive"`. The safe default retries preferred ISO
  5455 reductions and returns the largest complete scale; strict mode raises with structured
  requirement blockers, while permissive mode is an explicit warned opt-in to the historical
  best-effort result. Every returned `Drawing.scale_decision` reports the requested and
  effective scales, status, attempted scales, and any semantic placement blockers.

- **Hole and hole-pattern requirements now participate in semantic completeness accounting**
  (#1143). `lint_summary()["quality"]["completeness"]` reconciles recognition-owned bore,
  depth/through, grouping, pattern, and location requirements to placed, suppressed, dropped,
  missing, or explicitly unverifiable outcomes. Ambiguous declared/automatic correspondence
  fails closed without shrinking the physical denominator. Z-normal hole locations retain ADR
  0016's feature-level `location.location` addressability while structured physical evidence
  distinguishes the independently required X and Y ordinates for critique.

- **`lint_summary()` exposes drawing-quality evidence as separate completeness, restraint,
  and legibility components** (#1127). The legacy `score` remains byte-compatible and is also
  returned as the honestly named `diagnostic_score`; no composite quality verdict is invented.
  Completeness scores recognition-owned requirements in the families with semantic outcome
  ledgers and states that conditional scope explicitly; recognized families not yet scored are
  listed separately. Its scalar is `audited_score`, not `score`, because a feature recognition
  missed never became a requirement — so a part with a recogniser gap can reach 1.0, and the
  qualifier belongs where it survives being quoted. The block also lists what its denominator
  `excludes` and counts `unrecognised_geometry_reports` (a floor on that gap, not a measure).
  It is not a completion gate. Legibility includes only layout and placement diagnostics, and
  scores information-severity ones — "place what fits" drops, a leader crossing a silhouette —
  against the warning floor, so it cannot read 1.0 while itemising output it calls unreadable.
  A drop is identified by its `*_dropped` code suffix unless its producer records an explicit
  `outcome_stage`, so a drop code added later counts without being registered anywhere.
  Restraint fails closed as unavailable until measurement provenance can classify every
  annotation.

### Fixed

- **Generic notes/data tables use every viable free sheet region and explain failed placement**
  (#1145). A drawn sheet frame or zone ruler now defines the usable inset instead of becoming a
  page-sized solid obstacle, so a measured four-row notes block can use clear lower-left A4
  space. Tables retain the drafting preset's external text clearance from named and anonymous
  existing annotations. Failed placement diagnostics report the measured page-space footprint,
  attempted candidate regions, and the named obstacles or clearance bands that blocked the
  nearest candidates without materialising the candidate Cartesian product.

- **Automatic hole-table escalation is transactional per semantic requirement** (#1144). The
  engine suppresses replaceable callouts and location dimensions only after the table fits and
  every visible row has its required feature-owned balloon. A failed or partial balloon attempt
  rolls the shared table back as a unit and restores the exact annotations, ordering, registry
  identities, coverage, and issues. Precise ring, glyph, and leader-segment tests keep placement
  clear of retained callout labels, leader shafts, and existing public-balloon components without
  changing the existing additive `add_hole_table()`/`add_balloons()` behavior; malformed component
  metadata—including Boolean cardinalities or coordinate payloads—falls back conservatively or fails the
  optional replacement closed. New balloon shafts must also remain mutually crossing-free.
  Compound, thread,
  profile, pattern, tolerance, and fit facts the table cannot state remain active, while
  independently replaceable X/Y locations may still move to the table. Guarded interval solving
  has deterministic pre-lane, pre-carve, and work/state budgets; an adversarially fragmented label
  inventory or band fails the replacement closed before excessive work or allocation. Required row-key
  balloons take priority over optional non-certifying pattern markers, so an independently complete
  table cannot be displaced by an auxiliary marker.
  Hole outcomes identify the winning representation per requirement;
  mixed table/feature evidence deliberately claims no single winner. Exceptions restore those
  semantic markers, and automatic escalation fails closed rather than overwrite an existing
  annotation that owns a reserved table or balloon name.

- **Generated hole-callout leaders expose their exact rendered semantic text through the public
  `Leader.label` field** (#1142). Geometry and placement are unchanged, and structured coverage
  remains authoritative instead of requiring downstream tools to parse display text.

- **Leader placement probes use analytic footprint arithmetic instead of constructing temporary
  OCCT geometry** (#1138). Representative probe-heavy layouts are substantially faster while
  preserving custom-style containment and placement behavior.

### Changed

- **Pairwise lint detail no longer multiplies the legibility score penalty** (#1147). Raw
  severity and code counts retain their existing finding-level semantics, and every offending
  pair remains in `issues`. The legibility component now also reports its primary issue counts
  and scores one producer-identified annotation/failure mechanism once, with `affected_pairs`
  exposing how many raw pair findings were aggregated. The legacy top-level `score` and
  `diagnostic_score` remain unchanged.

- **Pull-request validation now reports a stable aggregate `ci-ok` check and runs the supported
  Python matrix on Linux** (#1134/#1136). Weekly, manual, and labelled workflows retain the
  cross-platform escape hatch, reducing routine Actions cost without narrowing supported Python
  coverage.

## v0.4.3 — 2026-08-09

**A trust and validation-throughput patch release.** Whole-part hexagonal stock now receives its
manufacturing definition, assembly recognition no longer combines evidence across bodies, and
performance validation is both more stable and faster to run.

### Added

- **Whole-part regular hexagonal stock is recognised independently of an attached boss and
  dimensioned as `HEX … A/F` plus its axial length** (#1082). In-plane rotation preserves the
  geometric across-flats size instead of substituting an orientation-dependent bounding-box
  width. Automatic and declared paths share the same compiler and placement machinery, while
  completeness lint distinguishes placed, authored-suppressed, dropped, missing, and ambiguous
  outcomes.

### Fixed

- **Slot and pocket patterns cannot group members from separate assembly bodies** (#1073).
  Recognition carries solid ownership through grouping and fails closed when ownership is
  unavailable, preventing plausible-looking phantom patterns across disconnected parts.

- **The annotation-obstacle performance guard uses deterministic work-count evidence instead of
  a wall-clock threshold** (#1071). The regression test remains load-bearing under mutation but
  no longer flakes because of machine load or suite ordering.

### Changed

- **The post-merge slow tier distributes individual tests across workers** (#1108), using
  `pytest-xdist`'s load scheduling after local benchmark evidence showed a material speed-up
  without weakening fixture coverage. Hosted slow validation remains the single post-merge gate.

## v0.4.2 — 2026-08-09

**The trustworthy manufacturing drawings release.** AP242 PMI is now accounted for from its
source records through lowering and rendering, imported datum faces retain an auditable topology
identity, and several drawing paths that previously omitted, invented, or stalled on defining
geometry now fail closed or produce the required annotation.

### Added

- **AP242 geometric tolerances and datum features survive as source-backed manufacturing
  requirements** (#62/#675/#1094/#1095/#1099/#1100). The importer recovers range-encoded
  dimensional tolerances, tolerance characteristics, magnitudes, datum chains, and supported
  all-around/all-over modifiers. Datum representation-item faces are resolved to imported
  topology through OCCT's exact transfer relationship rather than geometric resemblance, so the
  NIST CTC-01 datum definitions `A`, `B`, and `C` lower and render without guessing.

- **PMI completeness lint reconciles every AP242 source record through extraction, projection,
  lowering, and rendering** (#623). Unsupported or incomplete requirements remain explicit with
  source-specific outcomes instead of allowing a partial PMI transcription to report clean.

- **Hexagonal and other regular polygonal bosses are recognised and dimensioned by their
  across-flats size** (#676). Automatic and declared paths share the same feature model, and
  mutation tests reject incomplete, unequal, non-planar, and ambiguous face sets.

- **Generated `Sheet` scripts preserve references to named source objects where correspondence
  is proven** (#1041). The `--script` path emits declarations such as
  `sheet.hole(features.tap)` instead of copying detected numbers; ambiguous, stale, non-shape, or
  unused mappings fail closed.

- **A curated API reference is published at
  <https://pzfreo.github.io/draftwright/>** (#846), covering the supported entry points,
  `Sheet` declaration surface and fluent handles, `Drawing` results, and feature constructors.

### Fixed

- **Recognition no longer combines faces from different solids into phantom slots or pads**
  (#958). Each physical solid is recognised independently before the results are combined.

- **`Sheet.from_part(...).envelope()` reuses the detected whole-part envelope** (#1000) instead
  of appending a duplicate feature whose dimensions were then silently deduplicated on the page.

- **Crowded Z-turned step chains recover the independently approved overall height when the
  chain cannot be placed** (#955), and placed step lengths retain measurement identity for
  differential audit (#1004).

- **All-over GD&T leaders and complex STEP B-spline paths have bounded, export-safe DXF
  conversion** (#1097/#1070). All-over scope now reaches the existing export-safe leader
  representation, while spline conversion reads OCCT knots, poles, and weights through its
  indexed API instead of expensive sequence wrappers; visible curve geometry is preserved.

### Changed

- **Hosted CI retains every supported Python version and both dependency/kernel generations on
  macOS and Windows with a smaller compatibility matrix** (#1103). PRs still require lint,
  coverage, Codecov, and the compatibility gates; the slow standards tier runs once after merge.

- **The pinned wheel fixture now records mutation-gated evidence for a repeating 13-fold radial
  profile without claiming gear semantics** (#1085). Runtime recognition remains fail-closed
  until a separate feature contract has independently sufficient evidence.

## v0.4.1 — 2026-08-07

**The recognition trust release.** Automatic drawings now fail closed on unsupported inner
profiles, prove semantic coverage for flats, slots and patterns, and retain the measurement
identity needed to explain placed, suppressed, dropped and missing definitions. Several real
STEP case studies also move from clean-looking but incomplete output to complete drawings or
actionable lint.

### Discouraged (supported)

- **`Sheet.auto_dimensions()` and `Sheet.add_dimension()` now emit
  `SoftDeprecationWarning`** (#1043). They remain supported with no removal planned; the
  warning steers declared scripts toward `authored_dimensions()` plus explicit
  `dimension(feature, parameter_id)` lines, where omission can mean suppression. Automatic
  `build_drawing(part)` and the `Sheet.from_part(part)` on-ramp remain silent. See
  `docs/deprecations.md` for filtering and migration guidance.

### Added

- **Finished drawings expose why measurements were omitted and, on covered placement paths,
  what an annotation measures** (#996/#1002). `Drawing.suppressions()` returns the compiler's
  omission ledger, `Drawing.measurement_keys(name)` exposes recorded measurement provenance, and
  `draftwright.audit.diff_builds(before, after)` reports losses, substitutions and candidate
  suppression explanations. The differential is deliberately a triage aid, not a proof where
  renderer identity remains incomplete.

- **Physical-completeness lint now follows semantic correspondence for flats, lone slots and
  slot patterns** (#1018). Coverage is joined through recognition, IR and compiler identities
  rather than inferred from label text or page geometry, and distinguishes authored
  suppression, placement drops, missing definitions and unverifiable provenance.

- **Full-span floored open channels are recognised and dimensioned as first-class features**
  (#917). Automatic and declared drawings state the independent overall extent, one wall and
  channel width, suppress the derived opposite wall, and report any missing member of that
  defining chain.

### Fixed

- **Square and near-square parts retain both independent planar extents** (#997). A plain
  square plate could previously lose both plan dimensions, while parts up to five percent off
  square could lose one and be silently represented as square. The unsafe suppression rule is
  removed; explicit square notation remains tracked separately in #918.

- **Machined flats retain their physical stock identity from recognition through placement
  and completeness lint** (#1013/#1015/#1034/#1036). Flats on separate parallel, coaxial or
  slanted stock no longer collapse into one definition or borrow an opposite face from another
  stock region. Independent callouts receive clear-margin candidates, while double-D faces on
  one stock still form one A/F requirement.

- **Dense callout and step-detail cases keep their complete defining set** (#915). Hole
  callout batches are reconciled as a queue, detail views fit their actual aspect, step levels
  retain the supporting-face correspondence needed for truthful crops, and a detail redraws
  only rungs omitted from its parent view.

- **Pocket leaders start on the physical opening rim** (#916), removing the spurious inner
  silhouette crossing while preserving the solved label corridor and approved dimensions on
  X-, Y- and Z-depth pockets.

- **Wedge-mounted raised pads survive recognition** (#909). Lower ledges that touch only in
  plan no longer make the recogniser discard a valid upper pad; true staircase tiers remain
  excluded.

- **Annotation-dense detail placement no longer stalls the build** (#1065). Detail views
  avoid every decomposed annotation shaft, witness, label and item footprint; CTC-02
  supplies 571 such boxes. The free-rectangle search previously treated that set as tiny
  and admitted roughly 34 billion candidate rectangles, exhausting the slow tier's
  ten-minute test budget before export. A compressed-coordinate sweep preserves the same
  deterministic winning rectangle while completing the real search in seconds.

- **Through double-D bores are recognised and called out as first-class hole profiles**
  (#1061). Automatic and declared builds preserve the parent-circle diameter, A/F size,
  orientation and depth, and render one compound `DOUBLE-D ... A/F` bore callout. Physical
  critique now accepts that supported inner profile while retaining a separate warning for
  13 evenly spaced common-circle arcs on the unresolved outer boundary; it does not infer a
  repeating full profile or a gear standard.

- **Unsupported internal profiles no longer pass physical-completeness lint silently**
  (#1058). Inner loops on principal boundary faces are checked against the current
  circular-hole, rectangular-opening, true-obround, and proven through-double-D vocabulary.
  Profiles outside it now produce
  `unrecognised_defining_geometry` and reduce the lint score instead of allowing an
  envelope-only drawing to score 1.0.

### Changed

- **Crowded prismatic step dimensions recover into an enlarged detail view by default**
  (#909). The automatic `build_drawing` / `make_drawing` / CLI paths and generated
  `Sheet` scripts now preserve the omitted dimensions instead of returning
  `step_dim_dropped` unless the caller remembered `detail_view=True`. Pass
  `detail_view=False` to retain the parent-view-only behavior and its lint warning.

## v0.4.0 — 2026-08-02

**The compat-exit release.** Every surface whose documented removal target was 0.4.0 is gone,
and every deprecation that remains now says when it goes — in the message the caller sees, not
only in a release note. ADR 0005 §4's rule is that an alias carries a tracking issue *and* a
removal date, because "a facade with no exit date is a failure mode"; this release is that rule
being kept rather than restated. `docs/deprecations.md` is the new index, and
`tests/test_deprecation_dates.py` fails any deprecation that names no removal.

Also lands ADR 0016's declared-dimensioning work (#867): a `dimension(feature, role)` is
referential — it names a measurement and carries no number — and an authored set means omission
is suppression, enforced at the compiled-plan boundary rather than by a flag renderers check.

**Read this before upgrading:** two removals break *without a release that warned you*, because
their deprecation never appeared in a shipped version. See *Removed (breaking)* below and
`docs/deprecations.md`.

### Removed (breaking)

Compat surfaces whose documented removal target was 0.4.0 (#720, ADR 0005 §4 — every alias
carries a tracking issue *and* a removal date, or the facade is permanent by accident):

- **The seven `Drawing` compat aliases** — `_named` / `_anno_view` / `_pinned` /
  `_build_issues`, and `_pattern_callouts` / `_patterned_holes` / `_dropped_callout_diams`.
  These were private, and the public reads have existed since #699: use `dwg.registry`
  (`in reg`, `names()`, `issues`, `pinned_names()`) and `dwg.coverage`, or the `Drawing` verbs
  `annotations()` / `iter_annotations()` / `get_annotation()` / `view_of()`.
  **Note the asymmetry.** A *read* — `dwg._named` — now raises `AttributeError`. A *write* —
  `dwg._pinned = {...}` — does **not**: these were properties with setters, and `Drawing` has
  no `__slots__`, so assignment now quietly creates an unrelated instance attribute that no
  longer reaches the registry or coverage owner. Writes therefore fail silently rather than
  loudly. Grep for `\._(named|anno_view|pinned|build_issues|pattern_callouts|patterned_holes|dropped_callout_diams)\b`
  before upgrading; mutate through `registry.add()` / `pin()` / `record_issue()` /
  `restore_issues()` and the `CoverageState` methods instead.
- **The `draftwright.sheet_dsl` module** — an import alias for `draftwright.sheet` since the
  #640 rename. Import `Sheet` from `draftwright` or `draftwright.sheet`.
- **`generate_script`**, including its `draftwright.__all__` entry. It has raised since #940
  retired the imperative emitter; it is now simply absent, so the failure is an `ImportError`
  at the top of a script rather than a `RuntimeError` part-way through one. Use
  `--script` / `emit_sheet_script`.
- **The bespoke `--style imperative` error message.** `--style` itself is unchanged and still
  accepts its sole value `sheet`; `imperative` is now an unrecognised value like any typo,
  rather than one carrying its own explanation of the #940 retirement.

- **Bare dimension-role spellings** — `sheet.dimension(f, "width")`. Use the parameter id,
  `"width.length"`; `dimension_ids()` on a handle lists the valid ones. The bare role is the
  *family* spelling: it selects every parameter carrying it, which is how
  `dimension(step, "step")` quietly declared two measurements. In an authored set, where
  omission means suppression, silently declaring an extra one is the mirror image of the rule
  — so it now raises rather than resolving. (A *discriminated* bare role, used with `axis=`,
  is unaffected: that is how variants like `grid_pitch.length.row` are addressed, and it never
  warned.)
- **The `Sheet.dimension(kind=…, value=…)` call shape** — use `Sheet.measured_dimension(...)`.
  `dimension` is now solely the ADR 0016 referential verb: it names a feature and a parameter
  id and reads the value off the geometry.

Those last two break **without a release that warns you**. They were deprecated after v0.3.9
and removed in 0.4.0, so the `DeprecationWarning` never appeared in a released version —
upgrading from v0.3.9 or earlier goes straight from working to a raise. That is deliberate
(ADR 0016); this entry and `docs/deprecations.md` are the only notice you get, so both
failures name their replacement rather than raising about argument counts.

### Deprecated

- **The legacy `Drawing.export` shapes now emit `DeprecationWarning`** (#987) — they were
  announced as deprecated in v0.3.1 and then said nothing at runtime for four minor releases,
  which would have made their 0.5.0 removal a silent break. Three cases warn:
  - `export()` with `formats=` **omitted or `None`** — the legacy default, which writes
    SVG + DXF and returns an `(svg, dxf)` **tuple** rather than the `{format: path}` dict.
  - the **`svg=` / `dxf=` booleans**. The suggested replacement names the formats *that call*
    selected, so following it cannot change what gets written.
  - passing a boolean **alongside** `formats=`, where it is silently ignored — `formats` wins,
    and now says so.

  **If you promote `DeprecationWarning` to an error, previously-passing exports will now
  raise.** Migration: `export(out, formats=("svg", "dxf"))` and read the dict. `make_drawing`
  is unaffected — it returns the same tuple and does not warn.

### Added

- **`sheet.add_dimension(feature, role)` — ask the planner to carry one more
  measurement** (ADR 0016 / #872). Referential: it names a feature and a role and
  carries **no number**, so the value still comes from the geometry and a size lives in
  exactly one place. It changes *selection*, not derivation — a request can never
  introduce a number the part does not have. Requesting something the planner already
  emits is a deliberate no-op, so a script can ask without first knowing the rule set's
  mind. `sheet.auto_dimensions()` states the source explicitly (optional in this
  release; #874 makes it mandatory).
- **Suppressing a dimension marks it; it no longer leaks into the callout** (ADR 0016 /
  #875). `PlannedDimension.suppressed` was honoured at thirteen render sites but not by
  the compound hole-callout path, so a suppressed counterbore still printed. The group
  keeps its engineering data either way — what changes is whether a value reaches the
  page. Suppressing the bore ⌀ while a counterbore, spotface or countersink segment
  remains now **raises** and names the orphan: `⌀20 THRU ⌴ ⌀32 ↓ 1.5` has no reading with
  its leading term removed, and silently dropping the segments would discard authored
  intent while silently restoring the head would make the drawing say something the
  script does not.
- **Addressable dimension identity** (#869/#870/#871): every planned measurement now has
  a stable `DimensionId(feature, parameter)`, and correlated measurements that must be
  named as one — a grid pattern's row and column pitch, a step ladder — group into an
  `AddressableDimension`. This is what a `dimension(...)` line will name, and what
  suppression and provenance key on.

### Changed

- **`Sheet.dimension(kind=…, value=…)` is now `Sheet.measured_dimension(...)`**
  (ADR 0016 / #873), and `model.declare.authored_dimension` is
  `model.declare.measured_dimension` to match. This **reserves** the name `dimension` for
  the referential verb ADR 0016 defines — name a feature and a role, carry no number, let
  the engine read the value off the geometry — which `Drawing.dimension` already is and
  `Sheet.dimension` will become. The verb that carries an explicit number needed a name
  saying so first. The old spelling was a transitional overload during development and is
  **removed in this same release** (#720) rather than shipping — `Sheet.dimension` is solely
  the referential verb, and the old keyword call raises with `measured_dimension` named. See
  *Removed (breaking)* above. Generated AP242 scripts emit `measured_dimension` and so arrive
  un-deprecated.
- **`Sheet` handles address features by identity, not position** (#908/#910/#912). A
  handle, tolerance, GD&T origin, section request or dimension intent now follows *its*
  feature through a `features` reorder instead of naming whatever took the slot.
  Reordering with `reverse()`/`sort()` preserves every reference; deleting or replacing
  a referenced feature raises rather than silently retargeting a neighbour. `of()` now
  accepts a handle, and negative indices resolve uniformly across `of()`,
  `add_dimension()` and the GD&T verbs.

### Fixed

- **`sheet.envelope()` measured the file, not the part** (#977). An AP242 STEP import is a
  compound of the solid *plus its PMI presentation geometry* — annotation planes, leader
  curves — and the declared verb measured all of it. On the NIST CTC-01 fixture it declared
  1170 × 650 where the part is 800 × 450: an envelope 370 mm too wide, silently, in anything
  declared by hand. `step_level()` had the same exposure, offsetting every step position by
  the annotation overhang. Both now measure the solid body, sharing the helper the engine
  already used for exactly this (`_analyse` and `Sheet.model`).
- **A declared envelope's frame origin sat a half-height below a detected one** (#977). It
  used `bbox_min.Z` where the detector uses the bbox centre. **Dimension output is unchanged** —
  envelope sizes derive from `bbox_min`/`bbox_max`, and the overall height is compiled from the
  model bbox — but `frame.origin` is the generic feature *site*, so **a GD&T control frame,
  surface finish or note targeting a hand-declared `sheet.envelope()` was anchored at the
  bottom face rather than the centre, and its leader now moves to the centre**. That is a
  visible change on drawings carrying such an annotation; drawings without one are unaffected.
  Scripts generated by `--script` were never affected either way: they bake the detected frame
  explicitly rather than calling the verb.


- **Front-view dimensions join the placement solve instead of committing to the strip**
  (#894): they previously took strip space before the global solve ran, so a
  higher-ranked dimension could find its space already gone. Priority only ranks
  candidates that are *in* the solve.
- **A hole callout reads the feature's own `through` fact** (#868) rather than inferring
  it from whether a depth parameter happens to be present — so a suppressed depth can no
  longer make a blind hole print `THRU`.
- **The balloon ring clears the view it annotates after a hole-table escalation**
  (#901/#903).

## v0.3.9 — 2026-07-26

### Added

- **Repeated recesses are dimensioned as one pattern, not N competing callouts**
  (#841): identical blind pockets and identical through-slots now collapse to a single
  grouped `5× 7.9 × 13.6 × 19 DEEP` / `4× SLOT 8 × 30` leader plus `(n-1)× pitch`
  dim(s). Previously each member competed for its own size dims and some silently
  dropped for want of strip room — five declared slots rendered only three length dims.
  Both new kinds (`PocketPatternFeature`, `SlotPatternFeature`) round-trip every
  surface: declare (`sheet.pocket_pattern(...)` / `sheet.slot_pattern(...)`),
  recognition from a real solid (coplanar, same-facing members only), the emitted
  Sheet script, and the editable surface (`dwg.callout(feature)` + `finalize()`).
- **`Sheet.section()` and `Sheet.detail()` — ask for the view you need** (#841/#847):
  a section A–A auto-fires only for a Z-axis hole with a counterbore, spotface, or
  blind bottom, so a blind pocket's floor and depth stayed hidden-line-only with no
  supported way to request a cut. `sheet.section(feature)` / `section(at=y)` /
  bare `section()` force one (the cut plane is validated to lie strictly inside the
  part), and `sheet.detail()` exposes the existing `detail_view=True` opt-in as a
  chainable verb.
- **External threads are a first-class declared aspect** (#859): the turned analog of
  the #764 internal thread — `sheet.step(shaft).thread("M3x0.5")`,
  `sheet.diameter(...).thread(...)`, and `sheet.boss(...).thread(...)` append the
  spec to the ⌀ callout (`ø3 M3x0.5`) instead of needing a free-text `.note()`. It
  composes with `.finish()` for Ra-on-thread, keys the callout bucket on `(⌀, thread)`
  so a threaded and a plain ø6 stay distinct, and now round-trips through the Sheet
  emitter — including pattern-member holes, closing the symmetric latent #764 gap.
  Declaration-only: a plain cylinder is geometrically indistinguishable from a
  threaded one, so there is no recogniser. *(Known limitation: the incremental
  `callout`/`finalize(only=…)` paths still dedup thread-blind — #863.)*
- **Blind obround pocket recognition, including imported STEP** (#837): the blind
  counterpart of the #816 through-slot work. A stubby floored obround has side walls
  too short to pair, so it is recovered from its semicircular end caps — clustering
  faces by axis proximity so the quarter-cylinder split a STEP importer commonly
  produces recognises as well as build123d's half-cylinder. A sealed internal void
  (capped at both ends) is correctly *not* a pocket. A real tuner-jig STEP with five
  blind pockets ships as a fixture.
- **`sheet.slot(...).note(...)` / `sheet.pocket(...).note(...)`** (#841/#845): the
  slot/pocket handle now anchors a note to its own feature, with no `ref=` needed
  (an explicit `ref` still forwards, as before).

### Changed

- **An anchored note, GD&T frame, or finish relaxes its side instead of silently
  vanishing** (#841/#855): an annotation declared with an explicit `view=`/`side=`
  used to drop with only a warning when that strip was full, while the export still
  reported success. The requested side is now a *preference* — when the strip has no
  room the placer tries the opposite side, then the two perpendicular sides, taking
  the first with room (via the same bounds and title-block checks, so it never
  overshoots). A relaxed placement records an INFO `gdt_side_relaxed` issue naming
  requested versus actual. Only when no strip fits does it drop.

### Internal

- **The state-bus endgame is closed to its permanent seam** (#830/#840): the
  solve trace is filled into `BuildState` at the one construction site, the redundant
  part-model re-attach is gone, and the detail view is now *transactional* — geometry
  is projected at the detail scale and committed only if dimensions land, so
  `_drop_view_coordinates` has zero engine callers. With those removed, the #817
  decision-D method-call exemption narrows from a blanket pass to a named
  `_LAYOUT_SEAM` allowlist (`_add_view`, `_set_view_coordinates`), accepted as the
  permanent interactive-layout seam; every other private-method call on the drawing
  is now flagged.
- **A draftwright logo set** (#843) — drafting-idiom mark, all text outlined so the
  SVGs render font-independently; the README now leads with the lockup.
- **A worked multi-feature object-reference example** (#853) in
  `docs/multi-feature-object-reference-workflow.md`.

## v0.3.8 — 2026-07-23

### Added

- **`Drawing.note()` — a public free-text note verb** (#817/#820): place a free-form
  `Note` at a page point without reaching for the low-level placement primitive —
  `dwg.note("SEE NOTE 1", at=(x, y))`. This is the safe door for free text that the
  privatisation below needs.
- **Obround (racetrack) through-slot recognition** (#816): a part whose only features
  are obround through-slots is now recognised, dimensioned, and referenceable for GD&T.
  Previously such slots were silently undetected — and lint falsely reported the drawing
  complete. Stubby obrounds, whose straight side walls are too short to pair, are recovered
  from their two semicircular end caps (a round hole is never mistaken for a slot end).

### Changed

- **The generated Sheet script is now self-describing** (#833): `--script --style sheet`
  emits a feature-census header, section sub-headers with repeat tallies (`8× R50`), and a
  plain-language comment on each verb line (`# ⌀8 THRU ×4`, `# slot 40 × 120`, `# R50`).
  All additions are comment-only, so re-run fidelity is unchanged.

### Deprecated

- **The low-level `Drawing` placement API is now private — the public surface is
  safe-by-default** (#817): `add`, `place_dim`, and the view-coordinate / build-state
  plumbing (`set_view_coordinates`, `drop_view_coordinates`, `attach_part_model`,
  `attach_solve_trace`, `add_view`, `clear_annotations`) are deprecated in favour of the
  feature-backed verbs (`callout` / `dimension` / `locate` / `note` / …), which route
  through the layout solve. Each keeps a `@deprecated` (PEP 702) shim for one release —
  so type-checkers and IDEs flag call sites, not just a runtime warning — and is slated
  for removal in ~0.5.0.

### Internal

- **`AGENTS.md` — an AI-agent usage guide** (#818) with an executable check, steering
  agents toward the safe declarative surface rather than the low-level primitives.
- **Coverage reporting is governed and a Codecov badge added** (#825/#832).

## v0.3.7 — 2026-07-21

### Fixed

- **Turned ⌀ leaders point at the feature, not a corner or across the body**
  (#794/#798): the diameter callouts on a turned part now centre on the step's
  mid-length (a boss keeps its frame origin; a ⌀ shared by disjoint steps centres
  on the longest run), so the arrow lands on the middle of the feature's edge
  rather than a step corner. A leader that would otherwise cut diagonally through
  the body — a nested or end feature such as a ⌀6 boss stub — is re-routed out to
  the nearest clear margin (axis-aware: X-turned → left/right, Z-turned → top/
  bottom), pointing straight at the feature instead of clipping the silhouette. A
  re-route that can't be placed clear-and-safe is restored and flagged, and a
  pinned leader is never moved.

### Added

- **`leader_crosses_silhouette` lint notice** (#796): an info-level structural
  notice when a leader shaft passes *through* the projected part body (as opposed
  to a hole callout legitimately exiting an internal hole), so the gap is
  observable even where routing cannot clear it.

## v0.3.6 — 2026-07-21

### Fixed

- **`Sheet.step(boss)` on a prismatic part fails loudly instead of silently
  dropping the height** (#631): declaring a turned `.step()` at a cylinder that is
  really an external boss set `orientation="z"`, which made `render_height_ladder`
  suppress the overall-height dim on the assumption the OD/step chain conveyed it —
  but on a prismatic box that chain never renders, so the height vanished and
  nothing replaced it (11 → 10 annotations). The build now detects a `StepFeature`
  coincident with a `BossFeature` (same axis, origin, diameter) — a combination
  that can never be legitimate — and raises a clear `ValueError` pointing to
  `.boss()` (which renders its own ø and height per #632). Legitimate turned shafts
  have steps but no coincident boss, so they are unaffected.
- **`detail_view=True` no longer a silent no-op when the detail can't be placed**
  (#630): a part whose lint recommends a detail view (`step_dim_dropped`) but whose
  crowded band is full-width (e.g. a shelled cover's stacked face levels) can't be
  enlarged legibly *and* still fit alongside the main views — so `detail_view=True`
  produced a drawing byte-identical to `False`, silently ignoring the opt-in. It
  now records a `detail_unplaceable` warning naming the reason and an actionable
  remedy (dimension manually / move onto its own sheet), so the opt-in is
  observable. Parts whose detail *does* fit are unchanged.
- **Imperative `--script` round-trips the title-block fields + sheet furniture**
  (#775): the imperative flavour reproduced only title/number/tolerance/drawn_by/
  scale/page, dropping the `material`/`date`/`revision`/`company` fields (#766) and
  the `frame`/`zones`/`projection` furniture (#767/#768/#769) — so a regenerated
  imperative script didn't match the direct CLI. They now ride the emitted script's
  cog config block and its `build_drawing(...)` call (the Sheet flavour already
  round-tripped them), and the CLI forwards them to `generate_script`.

### Internal

- **Fast-tier dense-sheet canary for the #733/#734 strip-pressure regression**
  (#737): a synthetic contended-front-strip fixture that drops a step-height
  principal dim on the pre-#734 commit and stays clean on `main` — closing the
  fast-tier gap the #733 regression fell through (only the CI-only slow CTC
  fixtures exercised real strip pressure before).
- **Layout-hypothesis fuzz tier is now a reproducible PR gate** (#692):
  `derandomize=True` makes every run generate the same fixed example set (with
  `print_blob` for paste-able reproduction), so a failure recurs deterministically
  instead of surfacing on a seed-dependent minority of runs; exploratory fuzzing
  moves behind `DRAFTWRIGHT_FUZZ_EXPLORE=1`. The two `test_make_drawing.py` xdist
  observations (#669) were investigated and found non-reproducible — both tests are
  non-flaky by construction/measurement on current `main`.

## v0.3.5 — 2026-07-20

### Fixed

- **Generated `Sheet` script round-trips `--frame` / `--zones` / `--projection`**:
  the sheet-furniture flags added in #767/#768/#769 were not emitted into the
  generated `Sheet(...)` constructor (nor forwarded by the CLI's `--script` path),
  so a regenerated script dropped the border / zone ruler / projection symbol —
  the script no longer matched the direct CLI drawing. The emitter now emits
  `frame=True` / `zones=True` / `projection=…` when set (a plain drawing keeps a
  clean constructor), and the CLI forwards them to `generate_sheet_script`.

### Added

- **ISO 5457 zone-grid border ruler** (#768): `build_drawing(zones=True)` /
  `Sheet(zones=True)` / `--zones` draws the grid reference system — numbers 1..
  along the top/bottom edges, letters A.. (skipping I/O) down the sides — in the
  band between the frame and the page edge, with the standard division count per
  A-series page (~50 mm zones). Implies a frame (the ticks sit on it). Off by
  default → byte-identical. The border ticks are lint-exempt like the frame; the
  labels are exempt only from the page-bounds check (they legitimately sit outside
  the drawable), still covered by overlap lint.
- **Projection-method symbol** (#769): `build_drawing(projection="third"|"first")` /
  `Sheet(projection=…)` / `--projection` draws the ISO 5456-2 third-/first-angle
  glyph in the reserved title-block band (above the title block). Uses the new
  `ProjectionSymbol` primitive from build123d-drafting-helpers **0.14.1** (the pin
  is bumped). Off by default → byte-identical. Unlike the page-spanning frame, the
  small well-placed glyph is *not* lint-exempt — lint covers it so a future
  mispositioning is caught.
- **Opt-in drawn sheet frame / border** (#767): `build_drawing(frame=True)` /
  `Sheet(frame=True)` / `--frame` draws a border rectangle at the page margin. It's
  the *content boundary* (Option B), not a rectangle over the drawing: turning it on
  raises the effective content margin, and because `compose._layout_geometry` is the
  single authority shared by scale/page selection **and** placement, the reservation
  flows through `choose_scale` — so a framed drawing may pick a smaller scale / larger
  page (ADR 0004), with content clearing the border. Default `frame=False` is
  byte-identical (guarded by the golden + layout-cleanliness suites). The page-spanning
  border carries an `is_sheet_frame` rider so lint skips it.

## v0.3.4 — 2026-07-20

### Changed

- **Imperative `--script` output defaults to PDF and the modern export form**
  (#709). **Breaking** for regenerated imperative scripts: without `-f`, a
  regenerated script now writes `<stem>.pdf` (was SVG+DXF via the deprecated
  legacy tuple), and the script-visible variables changed from
  `svg_path, dxf_path` to a `paths` dict printed in the requested-format order.
  This aligns the imperative flavour with the Sheet flavour and the direct CLI,
  which have defaulted to PDF since #702/#288. Migration: regenerate with
  `--format svg,dxf` to keep the old outputs. Previously-generated scripts are
  unaffected (they carry their own export lines).
- **`finalize()` drains in the auto-pass's order** (#699 slice b): both build
  paths now execute the orchestrator's one canonical `_PASS_SEQUENCE` (via the
  shared `run_stages`), removing the hand-mirrored second orchestration in
  `Drawing._drain_intents`. For the deferred/finalize path this reorders three
  things toward auto-pass parity: the turned diameter/step-length set-solves now
  place *before* the corridor drain (matching the auto-pass's obstacle
  visibility); slots register after them; and the register-only height-ladder /
  step-position stages register *after* locations — observable too, since
  registration order decides corridor key-creation (= drain) order and
  same-priority tie-breaks (Codex review; the new order is exactly the
  auto-pass's). Recompose output may shift accordingly; the auto-pass order is
  unchanged.
- **`Sheet.export` speaks the modern export API** (#702): it now takes
  `formats=` (any of svg/dxf/pdf/png, default **PDF** — matching the CLI
  default) and `dpi=`, and returns the `{format: path}` dict. Previously it
  rode the deprecated legacy path, could only write SVG+DXF — the flagship
  facade couldn't produce a PDF — and returned the old `(svg_path, dxf_path)`
  tuple. **Breaking** for callers unpacking that tuple: use
  `sheet.export(stem, formats=("svg", "dxf"))` and read the dict.

### Added

- **Solve-trace / explain mode** (#736, from the #733 post-mortem): opt-in
  observability for strip placement. `build_drawing(trace=…)` or
  `DRAFTWRIGHT_TRACE=<path-or-dir>` writes ONE JSON file per build (schema v2)
  with two record types: `solves` — every corridor solve's key, strip bounds,
  candidate set, the obstacles that carved the strip (with owning annotation
  names), the free segments, and each candidate's outcome (placed /
  dropped-with-reason / deduped / promoted / deferred-to-post-drain) — and
  `pass_events` — the standalone strip passes plus the *immediate* placers
  (post-drain machined-feature leader callouts, the turned diameter row/column
  and step-length set-solves), each with per-item placed/dropped outcomes. So
  "why did X drop" is one `jq` query (`.solves[].outcomes[]` /
  `.pass_events[].items[]`). A `placement_unsatisfiable` strip-full drop now
  also names the top occupants in its lint message (`…strip full; occupied
  by: …`). Recording-only: an unwritable path logs a warning (never aborts a
  build), writes are atomic, and the recorder joins `finalize()`'s #647
  rollback. Default off; zero output change, nil cost when off.

- **`draftwright.model.authored_dimension`** (#704): the IR constructor behind
  `Sheet.dimension()`, extracted so `build_drawing(model=…)` callers can author
  a pre-measured drafting dimension without the `Sheet` façade.

### Added

- **Object-reference script workflow: docs + an inline tip** (#770/#771): a
  generated `Sheet` script from a **live-source** input (a `module:attr` /
  `file.py:attr` object spec, or a build123d object) now carries an inline tip
  showing how to swap each detected-numbers line for a reference to your object
  (ADR 0011 declare — the size is read off the object). A STEP-sourced script is
  unchanged (byte-stable — no object to reference). The README documents the full
  from-a-part-to-an-object-referenced-script workflow.
- **Knurl callout verb** (#765): `sheet.diameter(shaft).knurl("0.8")` →
  `KNURL 0.8 STRAIGHT` (or `.knurl("0.8", "DIAMOND")`). Named sugar over
  `.note()` with canonical formatting — a knurl is a text callout on a leader,
  not modelled geometry, so it flows through the existing note path (no new
  IR/render).
- **Standing ISO 7200 title-block fields** (#766): `material`, `date`, `revision`,
  and `company` (legal owner) are now first-class — settable on `build_drawing` /
  `make_drawing`, the `Sheet(...)` constructor, and the CLI (`--material` /
  `--date` / `--revision` / `--company`), and emitted into the generated `Sheet`
  script when non-default so a re-run reproduces them. Defaults preserve the prior
  output (revision `A`, the rest blank). The `revision`/`legal_owner` cells the
  renderer previously hardcoded now flow from these. (The imperative `--script`
  flavour's title-block round-trip of these fields is a follow-up.)
- **First-class thread/tap callout on a hole** (#764): ``sheet.hole(...).thread("M3x0.5")``
  (and ``declare.hole(..., thread=)``) folds a tap/thread spec onto the hole's existing
  compound leader (e.g. ``ø2.5 THRU M3x0.5``) — a structured aspect that round-trips, so
  ``.thread(...).finish(...)`` yields Ra-on-thread. A declaration-only aspect (ADR 0011
  side-layer: threads are cosmetic, not modelled geometry — no recogniser). The layout
  width estimator (``_est_planned_bore_callout_width``) now accounts for the thread text so
  the wider callout is reserved room and not dropped (the #261 estimator/render agreement).

### Fixed

- **Generated imperative script reconstructs side-drilled hole locations**
  (#426/#133): an X/Y-axis (side-drilled) bore gets `dim_loc_*` position dims in
  the direct build, but the `--script` emitter emitted a gap comment instead of a
  `locate()` for non-Z holes (because `locate()`/`render_locations` are Z-plan
  only, #133). The emitter now emits `dwg.locate(f)` for holes on any axis;
  `finalize()` routes by the feature's axis — Z-plan holes drain through
  `render_locations` as before, side-drilled bores through the whole-model
  `_locate_off_axis_holes` pass (registered at the auto-pass's own
  `off_axis_across`/`off_axis_along` stages, placed at the shared drain). The
  Z-only `locate()` live contract is untouched (side-drilled locates never reach
  it — they route to the off-axis stages). Closes the last direct-vs-generated
  script parity gap tracked by #426.
- **Generated imperative script reconstructs rotational furniture** (#426/#424):
  a turned/cylindrical part's `dwg.model()` carries a `RotationalFeature`, but the
  `--script` emitter parked it in the gap-comment list, so an executed
  reconstruction of e.g. a plain cylinder was missing `dim_od` and the
  `centerline_front`/`centerline_side` marks the direct build draws. A new
  `Drawing.rotational(feature)` add-verb records the intent, and `finalize()`
  drains it through the shared whole-model `render_rotational` at the auto-pass's
  own `"rotational"` stage — byte-identical to the direct build (no `only=` subset,
  fixed literal names, so no naming-seam or `⊇`-vs-`==` gap). The emitter now emits
  `dwg.rotational(f)` for rotational features. Closes the rotational half of the
  direct-vs-generated-script convergence.
- **Emitted `Sheet` script now faithfully reproduces the direct CLI drawing**
  (#707): the divergence reported against 0.3.3 on the Maquetto GRM-03 part was
  closed by #709 (script `--format` forwarding) + #661 (finalize detail drain).
  Locked with a vendored-fixture regression that asserts the full invariant the
  issue named — same views, annotation inventory, page, scale **and** lint
  between `build_drawing(...)` and the executed emitted `Sheet` — not just the
  annotation signature the synthetic parity cases check.
- **`_largest_empty_rect` no longer blows up on crowded detail views** (#661):
  the largest-empty-rectangle placer enumerated every candidate rectangle
  (O(N⁴) in the coordinate-cut count) — fine for the iso view's handful of
  obstacles, but the detail-view placer feeds it *every* placed-annotation
  footprint, so a two-detail turned part reached ~70 obstacles and spent ~13 s
  in a single call (multi-minute on the slower Windows CI runners, which tipped
  some jobs over their wall-clock budget). It now skips candidates that provably
  can't beat the best found so far (a `bisect` past every too-narrow pair, an
  early `break` once even the widest is too small): the same exact result and
  tie-break — verified identical to the naïve search across 3 000 random cases —
  at ~85× the speed on the pathological input (12.6 s → 0.15 s).
- **Detail-view height demotion is pin- and provenance-safe** (#661, user
  review): when a crowded prismatic detail needs the overall-height dim's room it
  is demoted and the detail retried, but (a) the canonical `dim_height` name was
  handed to that retry unconditionally — a *pinned* or user-replaced height could
  be removed; it now passes the same pin/label guards as the generalised
  `dim_length{n}` path (a pin, ADR 0012, is never demoted), and (b) if the retry
  also failed, the restore re-added only object/name/view, orphaning the dim from
  its envelope feature (lost `annotations_of`/`drop`/re-discovery); it now
  snapshots and restores the feature provenance and pin too.
- **`finalize()` resolves queued detail requests — detail views now exist on the
  edit path** (#661): the finalize drain was missing the auto pass's
  `detail_request`/`details` stages, so a crowded turned head's queued
  `DetailRequest` died with the per-run context and a prismatic
  `detail_view=True` build never even queued one — generated scripts silently
  lost every DETAIL A/B view. `build_drawing(detail_view=…)` is now persisted on
  the build state so the drain gates the prismatic request exactly as the auto
  pass does; the details stage re-projects the iso at sheet scale for the
  free-rectangle search and refits it after (mirroring the auto pass's ordering,
  where details place before the iso is fitted); and the overall-height demotion
  retry now finds the height dim by its envelope attribution, not only the
  auto-pass `dim_height` name. Three script-parity characterisation tests
  (single detail, two details, turned head) flip from xfail to passing.
- **Y-turned stepped shafts no longer crash** (#661): `render_step_lengths`
  projected every step span into the front view, where a Y-axis span is end-on
  (a point) — `Dimension` then raised "start and end points must be different"
  on BOTH the direct build and a generated script's finalize. Y-axis steps are
  now skipped (no step render pipeline consumes Y, #731 — mirroring
  `render_diameters`' x/z bucketing), the emitter flags the gap as a comment
  instead of emitting an unreplayable `dimension()` verb, and a hand-written
  Y step-length intent live-replays (surfacing the verb's own behaviour) rather
  than vanishing into the routed no-op. The Y-axis script-parity
  characterisation test flips from xfail to passing.
- **Boss heights are modeled, rendered, and coverage-checked** (#632): detected
  and object-declared cylindrical bosses now carry their axial extent through
  `BossFeature` and the dimension planner. Prismatic bosses receive a linear
  height dimension in a profile view in addition to their end-on diameter
  leader. `Drawing.lint()` reconciles that modeled height against the live
  feature-owned `Dimension`, reporting `boss_height_missing` if it is removed
  or otherwise absent (demoted to `info` on assembly drawings). This closes the
  declarative false-negative where a boss's diameter and the overall envelope
  were documented but its own independent height was not.

- **`--format` now reaches `--script` output** (#709, from the #702 adversarial
  review): `--script -f svg` used to silently emit a PDF-producing script — the
  CLI parsed the flag but never forwarded it. Both emitters now thread it
  through: the Sheet script emits `sheet.export(stem, formats=(…,))` when
  non-default (bare call for the PDF default), the imperative template embeds
  the requested tuple and prints in the **requested** order, and
  `generate_script` / `generate_sheet_script` grow a `formats=` parameter. The
  dormant script-parity characterisation module
  (`test_script_detail_parity.py`) is also un-skipped so its strict xfails give
  the remaining gaps CI signal.

- **Authored fillet, flat, groove, pocket, plate and slot tolerances now render**
  (#725/#726/#727/#728/#729/#730 / #698): the four leader-callout kinds join
  `_CONVENTION` and their renderers (`render_fillets`/`render_flats`/
  `render_grooves`/`render_pockets`) — and the plate-thickness and slot
  width/length linear passes
  (`render_plates`/`render_slots`, explicit `"linear"` entries) — consume the
  planner's `DimensionGroup`s, binding each
  planned dim explicitly by `(role, kind)` — so a `decorations`-authored ± /
  limit tolerance reaches the placed callout (`R8 ±0.1`, `17 ±0.2 A/F`,
  `4 ±0.1 WIDE × ø16 ±0.5`, `18 ±0.2 × 30 ±0.2 × 5 ±0.2 DEEP`, a `10 ±0.1`
  thickness dim, an `8 ±0.1` slot width; on a
  multi-value label each suffix rides its own number). Previously all six
  passes formatted raw feature fields and silently dropped it — the latent #629
  bug class the ADR 0015 bypass list documents. The fillet `n× R` and flat
  double-D/hex collapses stay render-side (first-authored tolerance wins, the
  `render_diameters` precedent); a pocket's three values — and a slot's
  width + length — share one authored
  tolerance (all kind `"length"`). The slot's model-derived datum position dim
  and its corridor/immediate placement mechanics are untouched. Untolerated
  labels/views/placement are unchanged.
- **Authored chamfer tolerances now render** (#724 / #698): `render_chamfers`
  consumes the planner's `DimensionGroup` (chamfer joins `_CONVENTION` as a
  leader), so a `decorations`-authored ± / limit tolerance on a chamfer leg
  reaches the placed callout (`C12 ±0.2`). Previously the pass formatted raw
  feature fields and silently dropped it — the latent #629 bug class the ADR
  0015 bypass list documents. Untolerated chamfer labels/views are unchanged.
- **`model.chamfer(face=…)` rejects a non-planar face** (#704): the declared
  front-end silently accepted a curved face (`normal_at()` does not fail on
  one) and read garbage legs off it — e.g. a fillet or countersink face became
  a bogus `ChamferFeature`. It now raises the documented `ValueError`, using
  the recogniser's own planarity gate (`classify_bevel`), so declared and
  detected chamfers classify identically by construction.

### Fixed

- **CTC-02/04 no longer drop step-height / overall-height dims** (#733): when
  the height ladder joined the corridor solve (#689) it silently moved from
  "places early" to "registers early, places at the drain" — so the immediate
  machined-feature leader callouts (pockets on CTC-04) filled the front-right
  strip first and the FORCED principal dims hard-dropped
  (`placement_unsatisfiable`), a priority inversion the old stage ordering had
  prevented implicitly. The leader-callout stages (chamfers/fillets/flats/
  pockets) now sit **after the corridor drain** in `_PASS_SEQUENCE` (joining
  grooves, which already placed post-drain for exactly this reason): principal
  dims win by construction, and a callout with no clear room yields with a
  warning, never a principal-dim error. Callout leaders may shift (they now
  route around the drained dims); on the `hex_bar` golden this *recovers* a
  silently starved `m_env_width` envelope dim.

### Removed

- **Three orphaned root exports** (#704): `draftwright.recognise_face_levels`
  (one recogniser of ~12 — incoherent as a top-level surface; import it from
  `draftwright.recognition`), `draftwright.dedup_diams` (an analysis internal),
  and `draftwright.fix_svg_page_size` (still available from `draftwright.export`
  and the `draftwright.make_drawing` facade). None had any internal or test
  consumer via the root API.

### Deprecated

- **The seven `Drawing` compat aliases** (`_named`/`_anno_view`/`_pinned`/
  `_build_issues`/`_pattern_callouts`/`_patterned_holes`/
  `_dropped_callout_diams`) are formally on notice (#699 slice c, tracked by
  #720): removal target **0.4.0**, per ADR 0005 §4. Read through the public
  surface instead — `annotations()`, `iter_annotations()`, `get_annotation()`,
  `view_of()`, `registry.pinned_names()`/`is_pinned()`, and the new
  `registry.issues`. All external production and test call sites in this repo
  are already redirected (`drawing.py` internals ride the aliases until the
  #720 deletion).
- **`draftwright.sheet_dsl`** (belated announcement — the alias shim shipped
  in 0.3.1 when #640 renamed the module to `draftwright.sheet`): importing it
  warns; it will be removed in **0.4.0**. Import from `draftwright.sheet`, or
  just `from draftwright import Sheet`.

## v0.3.3 — 2026-07-18

The **layout-quality release**: the occupancy model gets honest about ink shape,
the last solver-invisible pass joins the corridor solve, and crossed labels shift
themselves clear. Also completes the #635 consolidation epic (ADR 0005 fully
executed; ADR 0009's no-invisible-occupant guarantee now holds by construction
for every auto-pass).

### Added

- **Witness-crossing label shifts** (#690): a dimension label crossed by another
  dimension's witness stroke now shifts along its own line to clear it — a
  deterministic post-drain pass using the repair machinery (destination-aware,
  axis-aligned only, pinned dims exempt).

### Changed

- **L-shaped occupancy** (#685): placement obstacles decompose into per-stroke
  boxes + label box (helpers ≥0.14 `segments`) instead of one hull, with
  preset-aware arrowhead pads. View-corner contention between perpendicular
  strips disappears by construction (a plate-thickness dim returns to its
  natural strip); packing tightens. The cleanliness ratchet mirrors the model —
  only transverse stroke crossings are exempt, so parallel overprints and label
  contacts are detected honestly (twelve hull-artifact allowlist entries burned
  down; the legitimate shared-datum running-dimension pairs are documented).
- **Height ladder joins the corridor solve** (#636): step heights + overall
  height are corridor candidates (the leapfrog witness cursor survives as a
  deterministic build-time chain; the overall height stacks outermost by
  construction and packs tighter). An explicitly-requested crowded-step detail
  view now *deliberately* demotes the overall-height dim when the sheet cannot
  hold both (previously the carve dropped it silently); a Y-drilled hole's Z
  location joins the front running ladder per the documented routing.
- **One typed `BuildState`** (#639): the drawing's build context (analysis,
  part model, lint geometry caches) consolidates into a single object filled at
  one builder site, single-writer-guarded; the render passes make zero private
  `Drawing` reads (fail-closed empty allowlist).

### Fixed

- A prismatic detail view no longer loses both the detail *and* the height dim
  when space is tight (transactional demotion).
- The GD&T alternate-strip fallback defers until every corridor has drained, so
  it can never preempt a sibling strip's reserved corner.

## v0.3.2 — 2026-07-18

The **performance release**: the #602 perf loop (six PRs) plus the
build123d-drafting-helpers 0.14 upgrade. A dimension-dense drawing now builds
roughly **14× faster** than v0.3.1 (grid-plate benchmark 40 s → 2.8 s; NIST
CTC-02 ~107 s → well under half); the full test tier dropped ~14 min → ~3.5 min.

### Performance

- **Placement measures, then builds** (#678, #679): corridor/strip candidates are
  evaluated on analytical footprints (`dim_footprint`) or a single probe box; an
  accepted candidate builds its OCC geometry exactly once and re-validates the
  real box. Dimension constructions on the scattered-plate benchmark: 59 → 21
  (= the placed count).
- **Detect once** (#682): the sizing model `_analyse` builds pre-scale is stored
  on `Analysis.model` and reused as the render model — restoring ADR 0008
  Amendment 5 ("one inventory, detected once"). ~13 s off CTC-02.
- **Fillet recognition de-quadratised** (#683): edge→faces adjacency map replaces
  the O(faces²×edges²) `IsSame` sweep (3.7 M calls on CTC-02); 10.8 s → 4.6 s,
  byte-identical records.
- **Lint bounding boxes memoised** (#681): `Drawing.lint()` 0.88 s → 0.07 s per
  call (identity+location-token-checked cache beside the #143 view-edge cache).
- **DXF viewport from the known page window** (#680): skips `ezdxf.zoom.extents`'
  per-entity walk; ~2.4 s per DXF export.
- **helpers 0.14** (#684): boolean-free dimension ink (upstream #177, filed and
  fixed same-day) — ~25 ms per `Dimension`, was ~234 ms.

### Changed

- **Tight-span dimensions now render honestly** (helpers 0.14): ISO outside
  arrows where 0.13 silently degraded to a bare line. Ink extents grow on such
  dims; placement models the flip analytically. Two placement-golden fixtures
  re-blessed (ink extent only).
- Diameter-row leader labels space by **measured text width** and repair
  helpers' shelf-flip collisions (crowded rows only; ordinary rows unchanged).
- A plate-thickness dim whose home strip corner is structurally contested falls
  through to the opposite strip / side view instead of dropping (#684; the
  underlying one-box-occupancy artifact is tracked as #685).

### Fixed

- `recognise_fillets` neighbour tie-break is order-independent (#683 review).
- Lint's annotation-box cache prunes departed objects and detects in-place
  relocation (#681 review).

## v0.3.1 — 2026-07-17

Also lands the **#635 consolidation epic** (P1–P6, all six children closed): the render
layer's state bus is retired (`annotations/` reads zero `Drawing` privates,
machine-enforced), the three complexity hotspots are split (nesting ≤2), the whole-package
import DAG + a test-import ratchet are enforced, and the test suite gains a Hypothesis fuzz
tier + relational cross-kernel placement gates. All internal / behaviour-preserving
(byte-identical golden) — no output change.

### Added

- **PNG raster export** — `--format png` on the CLI, or `Drawing.export(formats=("png",))`.
  Rendered SVG → PDF → PNG via **pypdfium2** (Apache-2.0, Google PDFium BSD-3) + **Pillow**
  (HPND) — both permissively licensed and pure-wheel, so PNG works cross-platform with **no
  native cairo** (ADR 0006) and keeps the render path dual-license-friendly. `dpi=` sets the
  raster resolution.

### Changed

- **Unified export API** — `Drawing.export(out, *, formats=("pdf",)) → {format: path}` is now
  the single entry point over `svg`/`dxf`/`pdf`/`png`; it returns a dict and internally handles
  the SVG→PDF→PNG intermediate chain (writing/cleaning up intermediates it wasn't asked to keep).
  The CLI `--format` gains `png`.

### Deprecated

- `Drawing.export_pdf()` — use `export(formats=("pdf",))["pdf"]`; the method now warns.
- The legacy `export(svg=, dxf=)` boolean keywords and their `(svg_path, dxf_path)` tuple return
  — kept for back-compat; prefer `formats=[...]` (the dict API).

## v0.3.0 — 2026-07-14

**Breaking — feature-recogniser API renamed** (ADR 0013 / #568): *deliberate* breaks with
**no compatibility aliases**, hence the minor bump. Only import paths and callable
signatures change — but note this release also **changes drawing output**: new machined-
feature callouts, corrected leader anchors, and a reoriented isometric (see Added / Fixed).

### Added

- **Machined-feature recognition — the #148 epic.** #135 deliberately handled only enclosed
  rectangular through-slots; this broadens recognition, and each feature is recognised **and**
  dimensioned **and** authorable (recognise + emit + declare, ADR 0011):
  - **Blind slots + pockets** — floored recesses, `W × L × D DEEP` (#603).
  - **Cross / intersecting slots** collapse into single continuous channels (#604).
  - **Machined flats** on round stock — `{across} A/F` (#605).
  - **Turned / circlip grooves** — `{width} WIDE × ø{floor}` (#606).
  - **Slots / pockets in curved stock** — arc-bounded walls (#607).
  - **Radiused-end (obround) slots** now report their **overall** length (#613).
- **Chamfer callouts** — `C{leg}` / `{leg}×{angle}°` off each bevel (#560).
- **Fillet radius callouts** — `R{radius}` off each rounded edge (#561).
- **Plate / wall thickness** dimensions on multi-plate prismatics (#559).
- **Prismatic step-position** dimensions locating each shoulder (#555).
- **Countersink callouts** — `ø6 THRU ⌵ ø14 × 90°` (major-ø + included angle), like the
  counterbore `⊔ ø.. ↧..` (#558). The countersink rides on `HoleRecord`/`HoleFeature`, so
  grouping and the callout-width layout estimate account for it.
- **Every recognised feature round-trips all three ADR-0011 surfaces (#574).** Beyond
  detection, each feature now **emits** into the generated `--script` Sheet and is
  **declarable** on the fluent `Sheet` surface — authored from scratch or read off a
  build123d object: chamfer (#576), countersink (#575), plate (#577), step-position (#585),
  fillet (#561), plus all the #148 features above.
- **`draftwright.score.feature_census(part)`** — a per-kind recognised-feature census, a
  measurement tool for tracking recognition coverage (#608).

### Changed

- **Feature recognisers renamed `find_`/`analyse_` → `recognise_`** and their
  tuning/dependencies made keyword-only: `find_holes`→`recognise_holes`,
  `find_bosses`→`recognise_bosses`, `find_hole_patterns`→`recognise_hole_patterns`,
  `find_slots`→`recognise_slots`, `find_plates`→`recognise_plates`,
  `find_chamfers`→`recognise_chamfers`, `find_turned_steps`→`recognise_turned_steps`,
  `find_step_shoulders`→`recognise_step_shoulders`, `analyse_face_levels`→
  `recognise_face_levels` (the last was also re-exported at top level as
  `draftwright.analyse_face_levels` → now `draftwright.recognise_face_levels`).
  Cylinder-analysis substrate (`analyse_cylinders`, `full_cylinders`,
  `feature_diameters`) keeps its names.
- **`recognise_step_shoulders` now returns `list[StepShoulder]`** (a frozen dataclass)
  instead of raw `(axis, position)` tuples; `levels` is keyword-only.
- **`recognise_turned_steps` now returns `list[TurnedStep]`** (empty for a non-turned
  part) instead of `TurnedProfile | None`. `TurnedStep` gained an `axis` field (each step
  is now a self-contained record); `TurnedProfile` remains only as a pipeline aggregate
  (`TurnedProfile.from_steps(steps)`), no longer a recogniser return.
- **`recognise_face_levels` now returns `list[FaceLevel]`** (a frozen `FaceLevel(z)`
  record) instead of `list[float]`.
- **Recognition record classes renamed to avoid clashing with the IR `Feature` types:**
  `draftwright.recognition.HoleFeature` → `HoleRecord`, `BossFeature` → `BossRecord`
  (the IR `draftwright.model.ir.HoleFeature`/`BossFeature` are unchanged).
- **Page/scale sizing now reads the IR, not recognition records (ADR 0008 / #588).** The
  part model is built up front and drives sheet sizing, so detected and declared parts share
  one path and no recogniser record reaches the sheet estimators. Byte-identical to the old
  estimators except a hole pattern that shares a machining spec with loose holes now sizes
  separately (no phantom merged `N×`), which can shift a tightly-packed such part's layout.

### Fixed

- **Isometric view orientation (#620).** The iso camera viewed the *rear* (+Y) side against a
  front-view orthographic set, mirroring asymmetric features; it now views from the same
  front (−Y) / plan (+Z) / right (+X) combination.
- **Chamfer leaders (#621)** anchor on the bevel-face centroid (the diagonal middle), not the
  supporting plane's parametric origin (which projected to a corner/endpoint).
- **Fillet leaders (#622)** anchor on the radius surface, not the face bounding-box centre
  (which sat off the round, near the virtual sharp corner).
- **Authored `ref_pts`-only dimensions** now render (with Z-strip sizing) (#562).
- **Pitch-dim labels** are cleared off centrelines / bolt circles at creation time, not via
  the repair loop (#129).

### Removed

- **`draftwright.features` compatibility module** — deleted. Import `recognise_slots`/
  `Slot` from `draftwright.recognition` instead.

## v0.2.13 — 2026-07-09

**Compose-then-pack is now the layout authority.** This patch finishes the
ViewBlock footprint migration: estimated and measured layout paths now use the
same composed block model, furniture declarations reserve their real footprint,
and measure/repack iterates until stable instead of trusting one measured pass.

### Changed

- **ViewBlock footprints now drive the orthographic stack.** The old
  estimator-only plan-view balloon lift is gone; plan balloon headroom is part
  of the plan block in both estimated and measured/repack layout. (#112)
- **Measure/repack iterates to a fixed point.** Repack now keeps measuring and
  repacking while view-owned footprints continue to grow, with bounded
  convergence and tests for multi-pass growth. (#302)
- **Estimated view-block composition is explicit.** Scale/page fitness, repack,
  and furniture placement share the same composed block semantics instead of
  rebuilding corridor estimates independently.

### Fixed

- **Declared callout furniture reserves layout footprint.** User-declared
  furniture is now measured and fed into the layout model, reducing post-hoc
  collisions from declarations that previously existed only at render time.
- **Annotation-box composition owns strip sizing.** Strip depths reduce from
  composed annotation boxes, making footprint sizing less dependent on divergent
  scalar estimates.

### Documentation

- **Layout architecture docs reflect the current state.** ADR 0004 and the
  layout primer now mark the core layout-authority tranche complete and separate
  remaining work into hardening, coverage, detail-view, and manual-intent phases.

## v0.2.12 — 2026-07-09

**Sheet scripts now round-trip authored layout intent.** This patch finishes the next
slice of the layout-authority work: below/right ladders, pinned edits, height ladders,
detected envelopes, and AP242 authored dimensions now survive the declarative Sheet path
without being invalidated by post-hoc placement or raw imported metadata.

### Added

- **Pinned edit intents route through the corridor solve.** `locate(..., pin=True)` and
  pinned dimensions now become priority-ranked candidates in the shared corridor instead
  of fixed post-hoc edits, so user intent participates in the same ordering/spacing model
  as automatic dimensions. (#511)
- **AP242 dimensional PMI lowers to authored drafting dimensions.** Imported AP242
  size/location dimensions now become `AuthoredDimension` IR and generated Sheet scripts
  emit `sheet.dimension(...)`; unsupported PMI records remain explicit raw fallbacks.
  (#503, #422, #62)

### Changed

- **`place_dim` is deprecated.** Manual dimensions should use pinned candidate/dimension
  intent instead of the old incremental edit path.
- **Prismatic height ladders and detected envelopes round-trip through Sheet scripts.**
  Generated scripts now preserve `StepLevelFeature` and detected `EnvelopeFeature`
  fallbacks, preventing CTC01-style ladder swaps and raw STEP-envelope remeasurement
  drift.

### Fixed

- **Below/right corridors now share one solve.** Side-hole locations, height ladders,
  PMI/GD&T, envelope dims, and related below/right strip occupants negotiate in the shared
  corridor rather than competing through separate late passes. (#477)
- **Sheet script parity is tighter.** Generated scripts preserve member positions, step
  levels, AP242 authored dimensions, raw PMI fallbacks, and detected envelopes so
  direct-vs-script CTC01 output has matching annotation names.

## v0.2.11 — 2026-07-08

**Automatic layout now has one authority.** Page/scale selection, section placement,
furniture footprints, and table/balloon escalation now negotiate through the same layout
fitness model instead of using separate fixed offsets or first-fit policies after the main
solve.

### Fixed

- **Solver placement paths are more consistent.** STEP PMI dimensions now queue into
  the shared ADR 0009 corridor solve instead of carving after the drain; front-view
  hole callouts use the strip solver instead of fixed row stepping; pitch-dimension
  fallback searches bounded, obstacle-aware positions; repair no longer hides
  `annotation_overlap` with a fixed-step nudge; and step-count sizing now handles
  non-convergence conservatively. (#524)
- **Page/scale fitness is shared across initial selection and repack.** Later layout
  passes now compare candidates with the same model as the page chooser, preventing a
  nominally "better" repack from invalidating the original layout decision. (#519)
- **Section A-A participates in layout selection.** Section placement is measured as
  part of page/scale fitness, so a section view is no longer a fixed-offset afterthought
  that can disappear on dense sheets. (#515)
- **Furniture placement reserves full rendered footprints.** Section arrows, detail
  views, balloons, hole/data tables, and other furniture now reserve/check their true
  rendered footprint instead of just their labels. (#518)
- **Hole/data tables and balloon rings escalate through the layout model.** Dense table
  and balloon outputs can move or negotiate for available room rather than dropping from
  greedy first-fit placement when a small adjustment would fit. (#516, #517)

## v0.2.10 — 2026-07-07

**Declarative-surface fidelity and layout-engine unification.** Editing and re-running a
declarative `Sheet` script now reproduces more of the drawing (title block, layout, PMI on the
declared path), a new lint catches stale declarations, and an intentional scale is respected.
Internally the annotation placer moves further onto the single collect-then-solve (ADR 0009),
and `kiwisolver` is dropped as a dependency.

### Added

- **Declaration-vs-geometry reconciliation lint** — `lint()` now flags a declared cylindrical
  feature (hole/boss/step) with **no matching geometry** in the part (`declared_feature_absent`):
  a callout drawn over empty space because the part was edited but the declaration went stale.
  Closes the last gap in the "did my edit break the drawing?" loop (over-declaration; coverage
  lint already caught under-declaration). Matches on axis + ⌀ + in-plane position + bore/boss
  polarity. Gated on a caller-supplied `model=` (detection can't over-declare). (#487)
- **Title-block + layout aspects on the `Sheet` DSL** — `Sheet(drawn_by=…, tolerance=…, scale=…,
  page=…)`; the generated `--script` reproduces them, and the CLI forwards
  `--drawn-by`/`--tolerance`/`--scale`/`--page` on the sheet path (no more inert-flag warning). (#474)
- **PMI reproduced on the declared-model path** — `build_drawing(path, model=…, pmi="annotate")`
  synthesises the STEP AP242 PMI into the declared model, so a declared build draws the same PMI
  dimensions as the detection path. (#472)

### Changed

- **An intentional explicit `scale=` below the legibility floor is honoured with a warning**
  rather than rejected. A part deliberately drawn at 1:1 (or `Sheet(scale="1:10")`) whose smallest
  feature falls under the 10 mm legibility floor now renders (annotations may crowd) instead of
  raising `ValueError`; only a genuinely degenerate scale (< 0.1 mm projected, where OCCT arcs
  collapse) is rejected. The floor now binds only the *automatic* scale. (#489)
- **`kiwisolver` is no longer a dependency.** The 1-D strip solve delegates to the built-in
  deterministic minimum-total-leader-length PAVA algorithm, which supersedes the Cassowary
  solver — same placement contract, one fewer third-party dependency. (#507)

### Fixed

- **Dimension placement is more robust under contention.** Candidate **priority** is plumbed
  through the shared corridor solve, so an authored GD&T frame is kept over a lower-value
  automatic dimension when a strip is over capacity (#357). Rotational concentric-bore leaders
  are now bounded to the front-view height with ranked drops, and pitch dimensions are placed
  onto the obstacle-aware per-side zone-strip solve — retiring the last fixed-offset,
  count-based stacking placers (the shape behind earlier dense-sheet overruns). (#374)

## v0.2.9 — 2026-07-06

**Declarative GD&T, datums, and surface finish (ADR 0011 Phase 2b/2c).** The `Sheet` DSL can
now author the drawing information geometry can't carry — geometric tolerances, datum feature
symbols, and surface-finish marks — by pointing at a build123d feature or face. They render
through the auto-layout engine as first-class ADR 0009 corridor candidates, placed and spaced
crossing-free alongside the automatic dimensions (not a post-hoc overlay).

```python
sheet.datum("A", base_face)
sheet.control(bore).position(0.1, to="A", diameter=True).perpendicularity(0.05, to="A")
sheet.diameter(journal).finish("0.8")
```

### Added

- **Feature control frames (ISO 1101)** — `sheet.control(ref)` returns a chainable builder with
  one method per **all 14** characteristics (`position`, `flatness`, `perpendicularity`,
  `cylindricity`, `circular_runout`, `total_runout`, …); `to=` names the referenced datum(s),
  `diameter=` prefixes the ⌀ tolerance zone, `modifier=` adds a material-condition symbol (Ⓜ/Ⓛ/Ⓟ).
- **Datum feature symbols (ISO 5459)** — `sheet.datum("A", face_or_feature)`.
- **Surface finish (ISO 1302, Ra)** — `.finish("1.6")` on a hole / diameter / step handle, or
  `sheet.finish(ra, face)`.
- The target view + strip side are **derived from the referenced geometry** (a feature's axis →
  face-on view, a planar face's normal → edge-on view); `view=`/`side=` override.
- Render core: `ControlFrame` / `DatumRef` / `Finish` IR features placed by `render_gdt` as
  first-class corridor candidates, with the real-footprint plumbing needed to space wide frames.

### Fixed

- A control frame referencing an **undeclared datum letter** now warns at build.
- GD&T placement is **title-block aware** and **falls through to the opposite strip side** before
  dropping a frame, so stacked control frames place robustly rather than vanishing or overlapping
  the title block.
- An invalid glyph spec / degenerate leader in a caller-supplied model **drops the one item with a
  warning** instead of crashing the whole build (the IR is public input).
- Turned-shaft rotational furniture now reproduces correctly through the declared-model path
  (`model=` / `Sheet`) (#476).

## v0.2.8 — 2026-07-06

**`--script` now defaults to the declarative `Sheet` DSL.** The editable script the CLI
writes is the beautiful-Python surface — one commentable line per feature — for both STEP and
build123d-object input.

### Changed

- **`draftwright … --script` now emits the declarative `Sheet` script by default** (was the
  imperative edit-verb reconstruction). The imperative reconstruction is still available via
  `--script --style imperative`. For prismatic parts the generated script reproduces the same
  drawing as a direct build; a turned shaft's centre lines and base-diameter style are not yet
  identical (the remaining validity gap, tracked separately). `--style imperative` now errors
  clearly on a `module:attr` object spec (it reads a STEP file; use the default `sheet`).
  The Sheet DSL doesn't yet model the title-block / layout aspects the imperative script
  embeds, so `--script --style sheet` **warns** when `--drawn-by`, `--tolerance`, `--scale`
  or `--page` is set rather than silently ignoring it (use `--style imperative` to embed them).

## v0.2.7 — 2026-07-05

**The three authoring modes (ADR 0011 Amendment 1).** Naming how you drive draftwright —
*just do it* / *auto then tweak* / *generate an editable script* — and filling in the third:
a declarative `Sheet`-DSL generator, and number-free authoring against the build123d objects
you built.

### Added

- **`draftwright part.step --script --style sheet`** — generate an **editable declarative
  `Sheet` script** from a detected part: one commentable line per feature (hole / diameter /
  step / slot / pattern / envelope), views noted, the auto-section flagged. Comment a line out
  to drop that feature; the script re-runs to the drawing. The existing `--script` (imperative
  edit-verb reconstruction) stays the default (#461).
- **Object-reading `.cbore(tool)` / `.spotface(tool)`** on a declared hole — read the
  counterbore/spotface's ⌀ **and** depth off the tool object + part (depth measured from the
  local open face, so a rib/wall elsewhere doesn't skew it). A `Sheet`-declared drawing can now
  carry **zero magic numbers** and track the geometry parametrically (#462).
- **`Sheet.of(feature)`** — a fluent handle onto an **existing / detection-seeded** feature (by
  the build123d object, an index, or the `Feature` itself), so you can `.fit(...)` /
  `.tolerance(...)` a feature from `Sheet.from_part()` without re-declaring it (#463).

### Docs

- **ADR 0011 Amendment 1** — the three authoring modes and the mode-3 generation surface;
  records the decision to emit **honest detected numbers** for a detected part rather than
  fabricate a build123d reconstruction that misrepresents the geometry (#464).

## v0.2.6 — 2026-07-05

**ADR 0011 — the IR as a public input: declare features, don't only detect them.**
Detection stays the default, but the caller can now *supply* the feature model, so a
part you built parametrically reads as its own drawing and misdetection becomes
recoverable by construction. Plus the first Phase-2 aspect layer (tolerances + fits).

### Added

- **`build_drawing(part, model=…)`** accepts a caller-supplied `PartModel` (or a
  `Sequence[Feature]`); when given, **detection is skipped** and the auto-pass dimensions
  the declared features. Detection and declaration are two producers of the same IR (#447,
  ADR 0011 Phase 0).
- **Object → feature constructors** `draftwright.model.hole` / `boss` / `step` / `slot` /
  `pattern` / `envelope` — read a feature's geometry off the build123d object you built (⌀
  from the cylindrical face, axis/location from the bbox), or supply explicit values (#447).
- **The fluent `Sheet` façade** (`draftwright.Sheet`) — reference the objects you built,
  declare their drawing aspects, `.build()` / `.export()`. `Sheet.from_part()` seeds the
  hybrid override mode from detection (#447, Phase 1).
- **Toleranced dimensions** — a `±` / limit tolerance on a diameter, step, or hole bore,
  rendered on both the linear and ⌀-callout paths (`Sheet.tolerance(...)`, or a
  `decorations=` side-layer) (#28, Phase 2 P2a).
- **Fit-class deviation** — `Sheet.fit("H7")` resolves an ISO 286 fit class to its limit
  deviation for the feature's nominal ⌀, rendered as the class code (`ø20 H7`, default) or
  the signed deviations (`show="deviation"` → `ø20 +0.021/0`). Common classes; fails loud
  outside its table (#29, Phase 2 P2a.2).

### Changed

- **Object constructors honour explicit overrides** — a passed object supplies *defaults*;
  each explicit keyword overrides that field independently, and invalid public input fails
  at declaration with a clear `ValueError` (#451, #452).
- **A declared hole/pattern renders at its declared position** even where detection missed
  it — the callout membership is sourced from the declared model, not only detection (#448).
- **`Sheet.model()` / `Sheet.from_part()`** no longer build a full drawing just to return
  the IR — feature inspection/seeding is now a cheap, no-render path (#453).

### Fixed

- A narrow diameter band hidden under a larger OD silhouette is no longer silently
  undimensioned (the two feature inventories agreed) (#298).

## v0.2.5 — 2026-07-04

**The editable write API and record-then-finalize.** A detected drawing became an editable
object, and a generated `--script` became a runnable reconstruction that reaches auto-pass
quality.

### Added

- **`dwg.model()`** exposes the detected `PartModel` as a read surface, plus feature-
  referenced add verbs `dimension()` / `callout()` / `locate()` / `furniture()` /
  `section()` and `drop(feature)`, with a machine-checked completeness audit (#400).
- **Record-then-finalize** (#426): the verbs record intents in deferred mode and
  `dwg.finalize()` (auto-run by export) drains them through the auto-pass's own solvers, so
  a reconstruction reaches auto-pass quality; `--script` now emits a runnable detect-only
  reconstruction.

### Changed

- **Dimension-line spacing now follows ISO 129-1 / ASME Y14.5 convention** (#347): a wider
  first-line gap (8 → 10 mm) and tighter, uniform parallel stacking (between-line clear gap
  4 → 2.5 mm), with the inter-view corridor widened in step. Re-drifts every drawing.

## v0.2.4 — 2026-07-03

A follow-up patch on the ADR 0009 placement rebuild in 0.2.3: it finishes unifying
the shared "above-view" dimension corridor, adds a layout-overflow safety net, and
makes two more drop paths non-silent.

### Changed

- **Plan-view X location dimensions, side-view Y location dimensions, and a
  coincident slot-position dimension now share one collect-then-solve pass** (ADR
  0009 Amendment 6, #345/#346). Previously each pass carved the strip independently,
  so a hole location and a slot position measuring the same datum span could both be
  drawn, and the location ladder could come out non-monotonic. One solve now dedups
  the coincident span (keeping the higher-priority location dimension) and orders the
  whole ladder as segregated, monotonic runs — feature-size dimensions nearest the
  view, datum locations nesting outward by distance.

### Fixed

- **`choose_scale` never returns an overflowing layout** (#350). Scale selection
  could pick a scale whose composed block layout exceeded the drawable area; it now
  rejects any overflowing candidate.
- **A hole location and a coincident slot position are no longer drawn twice** (#345),
  including at fractional datum distances where a display-value snap gap previously let
  the duplicate escape deduplication.
- **The plan-view location ladder is monotonic** (#346) — running dimensions off a
  shared datum stack outward in ascending order instead of interleaving.
- **A dropped balloon is non-silent** (#387). A balloon that cannot be placed now
  reports the drop and clears its `callout_dropped` state precisely, instead of
  vanishing with no on-sheet signal.

## v0.2.3 — 2026-07-03

A large patch release: the **annotation-placement engine was rebuilt** as a
collect-then-solve *boundary-labeling* stage (ADR 0009). Placement is now
deterministic and minimises total leader length, and the recurring class of
overlaps where a label was drawn on top of an "invisible" occupant — a leader
shaft, a witness/extension line, the section hatch — is removed by construction.
Drawing output changes for many turned, cross-drilled, and multi-feature parts.

### Changed

- **Every annotation in a view's margin is now placed by one collect-then-solve
  pass** (ADR 0009, #317–#323). Dimensions, hole callouts, turned-diameter
  leaders, and the section hatch share a single occupancy model instead of several
  independent passes each blind to the others. When a strip is over capacity it now
  drops the *lowest-priority* annotation (smallest bore first) rather than whichever
  pass happened to run last. The legacy strip cursor is retired.
- **Leader placement minimises total leader length, deterministically** (P4,
  #318). A per-strip solve places each label at the shortest-leader position that
  keeps the labels in order and clear of keep-out rows (a view centre-line, a
  dimension's extension line); central/coaxial callouts are anchored to the
  view-centre row. Output is reproducible across platforms and Python versions.
- **`scipy` is no longer a dependency** — the leader solve is a small deterministic
  algorithm (weighted-median isotonic regression), not a linear program.
- **Output changes** for turned, cross-drilled, and multi-feature parts whose
  margin annotations are now positioned by the unified solver.

### Fixed

- **A PMI bore-diameter dimension spans the bore radius, not the full diameter**
  (#360). A `pmi="annotate"` diameter callout drew its witness lines at ±diameter
  from the centre — twice too wide, missing the bore edges.
- **A bore coaxial with a rotational part's turning axis is no longer
  over-dimensioned** (#309). It carried a redundant offset *and* height location
  dimension even though its centre mark already locates it.
- **A dropped turned step-length chain is no longer silent** (#362). When a turned
  head's shoulders are too crowded to dimension, the drop is now reported
  (`step_dim_dropped`) instead of vanishing with no lint or on-sheet signal.
- **A diameter callout can no longer overprint a bore callout's leader shaft**
  (#358). The turned-diameter column now avoids the *full* footprint of existing
  annotations, not just their text boxes.
- **The balloon ring hugs its dimensions on a cramped sheet** (#349) — its band
  depth is clamped to the drawable area.
- **Dimension detection is robust to `SafeDimension`** (#335/#349) — the corridor
  and balloon-ring filters test the dimension *type*, not a class-name string, so a
  future dimension subclass can't slip through.

## v0.2.2 — 2026-06-30

A patch release of turned-part dimension-placement fixes and a CLI start-up
speed-up. Drawing output changes for the affected turned/cross-drilled parts.

### Fixed

- **A coaxial bore callout on a *stepped* turned shaft is now lifted off the round
  view's centre axis** (#305). The earlier fix only triggered for a uniform
  (`is_rotational`) cylinder; a stepped shaft (e.g. the gramel GRM-03 drive screw)
  has a turned step profile but isn't classified rotational, so its `⌀… ↓…` bore
  callout was still leadered straight along the centreline, with the centre mark
  running through the text. The lift now also fires for a turned-profile part.
- **A side-drilled hole's location dimension now stacks *inside* the overall
  envelope dimension** (ISO order — overall dim outermost, feature/location dims
  nearer the view). It was placed *outside* the envelope, which forced the shorter
  location dim's arrowheads to flip outward and clash (seen on GRM-01 and GRM-02).
  The mandatory overall dimension is still guaranteed placement.

### Changed

- **CLI shell completion and `--help` are fast again** (#313). The Typer CLI and
  the heavy CAD engine are now imported lazily, so tab-completion and `--help` no
  longer pay a ~6 s engine-import cost; a real drawing run is unaffected.

## v0.2.1 — 2026-06-30

A patch release focused on **turned-part dimensioning legibility**: crowded
step-length chains and fine turned heads are now drawn legibly instead of crammed.
Drawing output changes for affected turned parts.

### Added

- **Automatic enlarged detail view for a crowded turned head** (#304). A turned
  part with a fine cluster of steps near one end and a long shaft (e.g. a thumbwheel
  drive screw) cannot have its head dimensioned legibly in line at any sensible
  scale. The head is now located as one block on the main view and broken out into
  an enlarged **DETAIL A — SCALE n:1** — the textbook treatment — firing
  automatically when a head's shoulders fall below the page legibility floor.

### Changed

- **Crowded turned step-length chains stagger across two tiers** (#293) instead of
  cramming or being skipped. When the labels would collide on one line, the ISO
  129-1 staggered convention alternates them between a near and a far tier so every
  step length stays legible at the drawing's own scale — no rescale needed. A roomy
  chain stays on a single tier.
- **Detail views are now one unified pipeline** (#307). The prismatic step-height
  detail and the new turned-head detail flow through a single
  detect → request → render path; several crowded regions become DETAIL A/B/…
- **Output changes** for turned parts whose step chains were previously crammed, or
  whose fine heads are now broken out into a detail view.

### Fixed

- **A coaxial bore callout no longer overlaps the round view's centreline** (#305):
  its leader is angled off the centre axis so the callout text sits in clear space.

### Internal

- A new **layout-cleanliness invariant test** asserts that finished drawings have no
  view/annotation collisions across representative part archetypes, and the
  measure-and-repack pass gained a trigger for an annotation growing into a
  neighbouring view's line-work (so the views spread to make room).

## v0.2.0 — 2026-06-30

A major release. draftwright took ownership of feature recognition and linting
(ADR 0007) and was re-architected onto a feature-IR + dimensioning-planner
"compiler" (ADR 0008), gaining a Typer CLI and a portable, pure-Python PDF path
along the way. **Generated drawings change** versus v0.1.13: the new pipeline
dimensions parts more completely and consistently, so placement and the set of
dimensions can differ — output is not byte-compatible with prior releases.

### Changed

- **Re-architected onto a feature-IR + dimensioning planner** (ADR 0008). The
  engine is now a compiler: detectors build one feature inventory → a typed IR /
  `PartModel` → a dimensioning planner emits render-intents → shared
  layout/projection/export. Orientation and feature-kind are *data in the IR*,
  not code branches, and every feature class (holes, patterns, counterbores,
  slots, turned diameters/steps, centre marks, location dims, envelope/OD,
  section A–A, PMI/GD&T) was migrated onto this one path; the old parallel
  recognisers and placement passes were deleted as each was replaced. Net effect
  for users: more complete, more consistent drawings — but output differs from
  v0.1.13.
- **draftwright owns feature recognition and linting** (ADR 0007). The hole/
  boss/cylinder/pattern recognisers, the slot/turned-step recognisers, and the
  feature-coverage lint engine are vendored into `recognition/` and `linting.py`;
  `build123d-drafting-helpers` is now purely the rendering library.
- **The CLI writes a PDF by default** and takes a `--format` selector (#288).
  Previously `draftwright part.step` emitted SVG + DXF; it now emits a single PDF.
  Choose outputs with `--format` (a comma-list, with an `all` alias) —
  `--format pdf,dxf`, `--format svg`, `--format all`. The library API is
  unchanged: `make_drawing(...)` / `Drawing.export()` still write SVG + DXF.
- **PDF export is now pure-Python and a core capability** (#288). The renderer
  moved from `cairosvg` (which `dlopen`s the native `libcairo` system library —
  absent on stock macOS/Windows, so PDF-by-default would have crashed there) to
  `svglib` + `reportlab`, both pip-installable wheels with no system dependency.
  PDF therefore works out of the box on every platform; output is visually
  identical to the cairo renderer.

### Added

- **A Typer command-line interface** (#289/#291): shell completion
  (`--install-completion` / `--show-completion`), rich `--help`, and `--version`
  (reports the installed distribution version). All existing flags are preserved.
- **`--format` output selector** (#288) — `pdf` (default), `svg`, `dxf`, or `all`,
  as a comma-list.
- **Turned-part dimensioning**: axial step-length recognition and chains
  (#188/#189/#231), step-diameter callouts, collapse of a uniform step
  staircase to an "N× length" note (#290), and OD of a horizontal (X/Y) round
  body dimensioned on the profile view (#292).
- **Slot recognition and dimensioning** converged onto the IR as `SlotFeature`
  (#242), and **section A–A** is now triggered by the planner (#271).
- **A Contributor License Agreement** (#183).

### Removed

- **The `--pdf` flag** (use `--format pdf`, the new default), the **`[pdf]`
  install extra**, and the **`cairosvg` dependency** (#288).
- The byte-exact golden-output test harness (#190) — regression coverage rests on
  the geometry-level and standards suites (ADR 0005 §3 / ADR 0007).

### Fixed

- Locate **every** side-drilled (off-axis) hole, not just the first (#225/#286).
- Don't mis-detect a prismatic part with incidental cylinders as a turned part
  (#293/#294); drop phantom zero-diameter turned steps (#279/#284); skip an
  illegibly-dense step-length chain rather than cram it (#293/#296).
- Degraded hole-pattern callout consistency (#262/#274).
- Windows `python -m draftwright.make_drawing` CLI smoke / entrypoint (#181/#182).

## v0.1.13 — 2026-06-27

### Changed

- **Requires `build123d-drafting-helpers>=0.13.0`; text pinned to bundled fonts**
  (#149, ADR 0006). draftwright now vendors IBM Plex (OFL-1.1) and renders and
  measures all text via `font_path` — IBM Plex Mono for dimensions/callouts/notes,
  IBM Plex Sans Condensed for the title block — instead of resolving the system
  font name `"Arial"`. Resolving a name substitutes a different font on Linux,
  which shifted the whole sheet ~1 mm; pinning a bundled font file makes generated
  layout **deterministic across Linux/macOS/Windows** and gives a consistent
  typeface. **Drawing output changes**: positions shift slightly from prior
  releases and labels render in IBM Plex (helpers #172).

### Internal

- **Compiler-pipeline module split** (#138, ADR 0005). The two large modules
  `make_drawing.py` (3,907 lines) and `annotate.py` (2,587) were decomposed into a
  DAG of focused stage modules — `projection`, `sheet`, `analysis`, `drawing`,
  `builder`, the `annotations/` subpackage (sections/turned/pmi/holes/orchestrator),
  alongside the existing `registry`/`linting`/`repair`/`export`/`fonts`. Annotation
  identity, the lint coverage signal, and the deterministic repair loop each gained a
  single owner; `make_drawing.py` / `annotate.py` are now thin compat facades, so all
  existing imports and the `draftwright` CLI entry point keep working. A golden-output
  regression gate verified every step is behaviour-preserving (output byte-identical),
  and mypy was tightened on the settled contracts. No public API or drawing-output
  change. (Phases #160–#166.)

## v0.1.12 — 2026-06-21

### Changed

- **Requires `build123d-drafting-helpers>=0.12.0`** (#92, #122). draftwright now
  consumes the new sub-clustered hole-pattern recognition, the
  `feature_diameters()` coverage inventory, the persistent `view_edge_cache`,
  and the `ViewCoordinates.from_viewport()` ISO projection basis.
- **Grouped hole-pattern callouts** (#92, #111, #114). A recognised perimeter,
  grid, or bolt circle collapses to a single `n× ⌀ …` callout plus its pattern
  dimensions instead of a balloon on every hole. A spec group now sub-clusters
  into multiple patterns (a perimeter → its edge `LinearArray` rows, a filled
  lattice → one `RectGrid` with a `(rows×cols)` callout and both pitch
  dimensions); only genuinely unpatterned holes fall back to the per-hole table.
  On NIST CTC-02 the table shrinks from 61 rows to the unpatterned remainder.
- **Layout overhaul — compose-then-pack** (#121, #112, ADR 0004). Each view owns
  the annotations created against it, and the resulting view blocks are packed
  disjoint with automatic page/scale escalation. This eliminates cross-view
  overlap — most visibly, plan-view balloons landing on front-view dimensions.
- **Drawing attribution** (#120). The title block records the author, the SVG
  and PDF carry a clickable draftwright hyperlink, and a "generated by
  draftwright" note is written to the SVG/DXF/PDF file metadata.
- **Gap between wrapped hole-table column blocks** (#123) so a chart that wraps
  into several blocks reads as distinct columns.

### Fixed

- **Plan-view top balloon ring no longer floats over a phantom corridor** (#125).
  The hole-table escalation deletes the X-location dimensions but left their
  stale depth in the strip cursor, so the top balloons were parked far above the
  view. The ring is now sized to the real dimension stack, so the top-side
  leaders are short like the other three sides.
- **No more phantom `feature_not_dimensioned` warnings** on slot-ends and shallow
  recesses, via the helpers 0.12.0 `feature_diameters()` coverage inventory
  (#92).

### Internal

- `AnnoBox` box-model footprint foundation and the four-side balloon ring placed
  in a reserved view halo (#111, #112); the title block is pinned as a
  first-class layout block (#112).

## v0.1.11 — 2026-06-19

### Changed

- **Feature-coverage lint is assembly-aware.** A general-arrangement drawing of
  a multi-solid part deliberately omits each part's bores (they belong on detail
  sheets), so `feature_not_dimensioned` / `feature_count_mismatch` are now
  emitted at `info` rather than `warning` when the part is multi-solid — out of
  the warning count and quality score, but still queryable. Auto-detected;
  override with `build_drawing(..., assembly=True/False)` or
  `lint_feature_coverage(..., assembly=...)` (#69).

### Fixed

- **`place_dim` now labels the real-world length, not the page distance**, at
  non-1:1 scale. Previously a dimension placed at a scale other than 1:1 showed
  the on-page millimetre span instead of the true model dimension (#104).

### Internal

- **`make_drawing.py` decomposed (#98).** The per-view projection math and the
  analysis namespace were deduplicated and typed (the namespace is now a frozen
  `Analysis` dataclass), and the annotation passes were extracted into a new
  `draftwright.annotate` module on top of a shared `draftwright._core`. The
  module graph is a DAG (`layout → _core → {make_drawing, annotate}`) and
  `make_drawing.py` shrank from ~5,270 to ~2,930 lines. No public API or
  behaviour change.

## v0.1.10 — 2026-06-18

### Added

- **Constraint-based layout engine (ADR 0003).** A new `draftwright.layout`
  module with a `Placeable` protocol and a `LayoutSolver`: a 1D Cassowary strip
  solver (`solve_strip`, with per-pair gaps) and a 2D free-rectangle placer
  (`place_box` / `fit_box`) that positions a box in a free part of the page
  clear of the views, title block, and existing annotations. Hole-callout and
  turned-diameter placement now run on the solver. The engine grows per real
  consumer; a monolithic global 2D solve is deferred (see the ADR).
- **Hole table + balloons (#93).** `dwg.add_table(rows)` places a generic data
  table (gear data, BOM, revision block, …) in a free corner via `place_box`;
  `dwg.add_hole_table(view)` builds a hole chart from the detected holes with a
  circled balloon tag at each hole. A **too-dense plan view now auto-escalates**:
  a part the layout cannot legibly dimension hole-by-hole is replaced by a
  complete per-instance hole chart (`TAG | ⌀ | X | Y`, datum-relative) plus
  balloons, instead of silently dropping callouts and location dims. The chart
  wraps into multiple column-blocks to fit the page.
- **External turned diameters (#77).** A turned part lying along the X axis now
  gets ø leader-callouts for its external stepped diameters, with thread/worm
  patches collapsed into a single boss.
- **Pin / manual override (#89).** `dwg.pin(name)` / `dwg.unpin(name)` fix an
  annotation's position so `repair()` — and the layout engine — never move it; a
  deliberate (human or AI) placement wins over automatic layout.

### Changed

- Hole-callout and turned-diameter placement is deconflicted through the shared
  `LayoutSolver` instead of ad-hoc per-pass logic (no output change).

### Fixed

- **Exact circles recovered for revolution silhouettes.** `project_to_viewport`'s
  HLR returns the on-axis silhouette of a turned feature (or a concentric
  gear-tooth-tip arc) as an approximating spline, not a true circle — splines in
  the DXF where CAM expects `CIRCLE`/`ARC`, and fitted rather than exact radii.
  `add_view` now refits any silhouette whose samples are equidistant from a
  recognised revolution axis back to an exact circle/arc (#67).
- **Blind-hole depth no longer measured across solid boundaries.** On a
  multi-solid assembly, coaxial bores in different bodies were merged into one
  hole, reporting a depth spanning the inter-body gap (the ⌀9.8 ↓111.4 symptom).
  Fixed upstream in `build123d-drafting-helpers` 0.10.1; the dependency pin is
  bumped to `>=0.10.1` to pick it up (#68).

### Docs

- The skill and generated-script header now lead with the domain API
  (`features` / `place_dim` / `repair` / `lint_summary`) and the
  build → critique → fix loop. ADR 0003 records the layout architecture; ADRs
  0001/0002 remain the editing-model and lint→repair foundations.

## v0.1.9 — 2026-06-16

### Added

- **Domain-semantic editing API.** `dwg.features(view)` returns detected holes
  and features grouped by machining spec in page coordinates, and
  `dwg.place_dim(p1, p2, side, view, draft, name=…)` places a dimension from
  domain inputs — the vocabulary a script (or an AI assistant) needs to edit a
  drawing without hand-computing page geometry (#25, #26).
- **`dwg.annotations()` and `dwg.get_annotation(name)`.** Introspect what is
  already on the drawing — a `{name: type}` map and a name lookup — so a script
  can make incremental edits without risking a silent name-collision replace
  (#27).
- **`dwg.view_bounds(view)`.** Returns `(x_min, y_min, x_max, y_max)`, the page
  bounding box of a view's projected geometry (or `None` for an unknown view),
  so free-form notes and leader elbows can be placed just outside a view without
  guessing offsets from `dwg.at()` (#28).
- **Lint findings carry a suggested fix.** Each repairable lint issue now
  includes a ready-to-run domain-API call snippet, so acting on a finding is one
  copy-paste away (#29).
- **Lint→repair loop.** `Drawing.repair()` — run by default in `build_drawing` —
  mechanically resolves the lint codes that have a deterministic placement fix:
  overlapping labels are pushed apart and wrong-side dimensions are flipped. A
  pass that would net-increase the issue count is rolled back, so repair never
  makes a drawing worse (#30).
- **TYP / representative dimensioning for uniform step patterns.** A run of
  equal-rise, equal-going steps is dimensioned once and labelled representative
  (TYP) instead of repeating identical dimensions down the ladder (#45).
- **Enlarged detail view for crowded step clusters (MVP).** When shoulders are
  too closely spaced to dimension legibly at sheet scale, an opt-in
  (`detail_view=True`) detail view re-draws them at a larger scale (#42).

### Changed

- **BREAKING: the annotation list `dwg.annotations` is renamed to `dwg.items`.**
  `dwg.annotations` is now a method (see Added); the ordered, mutable list of
  annotation objects it used to be is now `dwg.items`. Pre-1.0 with no published
  consumers, so the clearer name was taken now rather than spelling the new query
  method awkwardly (#27).

### Documentation

- ADRs 0001 (editing model) and 0002 (iteration loop) record the design
  direction behind the domain API and the lint→repair loop (#51).

## v0.1.8 — 2026-06-16

### Changed

- **Automatic scale selection now minimises the sheet size.** The preference
  ladder is page-major: every standard scale on the smallest sheet is tried
  before the next sheet up, so a part lands on the smallest sheet it fits at the
  largest scale that sheet allows. A 20 × 15 × 10 mm part is now drawn 2:1 on A4
  instead of 5:1 on A3 — a smaller sheet is preferred over a larger enlargement
  scale. Reductions keep their legibility-first balance, so a too-big part is
  not over-reduced onto a small sheet.
- **A specified page now enlarges to the best fitting scale.** When the caller
  fixes the page (`--page A3`) or scale, scale selection packs the isometric
  view into the largest empty rectangle the placement engine actually uses (it
  may sit in vertical headroom above the views), instead of charging it a column
  in the view row. A long, short part — e.g. a 100 × 10 × 11 mm staircase — now
  fills a requested A3 at 2:1 where it was previously under-scaled to 1:1.
  Automatic selection (no page/scale given) keeps the conservative row model,
  which reserves enough room to place every annotation rather than dropping some
  onto a tighter sheet (staircase review).
- **Isometric view growth is capped.** The iso is fitted to fill its zone but no
  longer grows past 1.3× sheet scale; on an oversized sheet it could previously
  balloon to ~8× and dwarf the dimensioned orthographic views. Shrinking to fit
  a small zone is unchanged.
- **Step heights are dimensioned only where legibly separable.** After the
  adaptive cap (#36), a part with many closely-spaced shoulders (e.g. NIST
  CTC-02 at 1:5) tried to dimension faces only ~1 mm apart on the page. A step
  is now dimensioned only if it is both tall enough from the base *and* at least
  one legible step-height above the previously dimensioned one; the rest surface
  as `step_dim_dropped` (use a detail view). "Fits" is not the same as
  "legible" (#41).
- **Hole-location dimensions are gated for legibility.** A hole-dense part (e.g.
  NIST CTC-02, ~38 distinct hole locations) previously stacked every location
  reference into a tall, busy tower above the views — "fits" is not "legible".
  Each axis's references are now gated by inter-dimension page spacing
  (`_legible_locations`, analogous to the step-height gate #41): only locations
  at least one value-label footprint apart on the page are dimensioned; the rest
  surface as `location_ref_dropped` (full fidelity belongs in a detail view,
  #42). Sparse parts are unchanged (#43).
- **Tighter location-dimension tier pitch.** The vertical pitch between stacked
  X/Y location dimensions is now derived from the label footprint
  (`font_size + 2·pad_around_text`, ≈7 mm) instead of a looser `font_size·3`,
  so location stacks pack closer (#41).

### Fixed

- **Phantom step corridor no longer blocks a larger scale.** Page/scale
  selection reserved a step-ladder corridor sized for *every* candidate
  horizontal face, including ones the legibility gate would never dimension. A
  part with many sub-legible faces (e.g. a staircase with 15 tiny treads) was
  forced onto an oversized sheet at 1:1. Scale selection now iterates so the
  reserved corridor matches the step count actually placed, freeing the part to
  pick a tighter sheet (staircase.step review).
- **Engraved-text faces are no longer dimensioned as steps.** `analyse_face_levels`
  gained a `min_area_frac` filter; a horizontal face counts as a step only if
  its area is at least 1% of the part's plan footprint. This drops sub-feature
  faces (fragments of engraved numbers/text) that were surfacing as phantom
  shoulders — e.g. a 0.57 mm² digit face dimensioned as z=6.4 on staircase.step.
- **Overall-height dimension nests outside the step dims.** The overall height
  is now placed last on the front view's right ladder so it sits outermost, with
  the step-height dims inside it; extension lines nest instead of leapfrogging
  (staircase.step review).

## v0.1.7 — 2026-06-15

### Added

- `Drawing.lint_summary()` — a JSON-friendly aggregate of `lint()` for
  non-interactive callers (scripts, or an LLM via the API): severity counts,
  per-code counts, a `geometry_issues` tally (standards/geometry checks vs pure
  layout), a `passed` flag, a coarse 0–1 `score`, and the full issue list. Gives
  a single signal to gate and optimise on without rendering the SVG (#32).

### Changed

- **Adaptive annotation placement.** The three hard-coded cardinality caps —
  four hole callouts per view, four hole location references per part, and three
  step-height dimensions — are removed. The engine now places as many as the
  available strip/corridor space allows (callouts largest-first, locations
  nearest-datum-first, every legible step), so a part with room is dimensioned
  completely instead of dropped to an arbitrary count. An annotation that
  genuinely doesn't fit is never force-placed; it surfaces via lint
  (`callout_dropped` / `location_ref_dropped`, warning severity). On the NIST
  CTC parts this raises coverage substantially (e.g. CTC-02: 4 → 36 location
  dimensions, 4 → 9 callouts) with no error-severity lint (#36).
- **No silent annotation drops.** Every place the layout has to drop an
  annotation now records a machine-readable lint issue, surfaced by `lint()`,
  so a short drawing always carries a reason. A dropped callout names its
  diameter and is excluded from `feature_not_dimensioned` (no double-report).
  `placement_unsatisfiable` (error severity) is reserved for the degenerate
  case where space was reserved but an annotation still could not be placed
  (#32).
- **Layout constants derived from first principles.** Bare, fixture-tuned
  constants (strip slot widths, callout label widths, isometric fit factor) are
  now computed from text metrics and page size rather than hard-coded, so the
  layout generalises to unseen geometry instead of fitting the test cases (#31).
- `_auto_annotate` clears its build-time lint records on re-entry, and repeated
  `lint()` calls are stable (#32).

### Fixed

- AP242 / PMI STEP import segfault: STEP geometry is now read directly via
  `STEPControl_Reader`, avoiding the XCAF/PMI read that crashed (SIGSEGV) on
  with-PMI files such as NIST CTC-02 (#20).

### Tests

- Overfitting guards pin the general layout behaviour on turned/hybrid parts
  (flange OD + bolt circle), multi-bore parts, and the step-legibility boundary
  (#13).
- The full NIST CTC set (AP203 and AP242) builds and is covered by the slow
  end-to-end tier.

## v0.1.6 — 2026-06-15

### Fixed

- Section-view boolean cut on cast geometry: the exact `body - Box(...)` boolean
  raised an uncatchable `Standard_DomainError` (C++ abort, SIGABRT) on some parts
  (NIST CTC-04), crashing the whole drawing. `_fuzzy_cut()` now runs
  `BRepAlgoAPI_Cut` with a small fuzzy tolerance and keeps solids-only, making
  the section cut robust (#20, #22).

### Tests

- NIST CTC-04 (both AP203 and AP242) now build with a clean section view and are
  covered by the CTC build tests.
- Known: CTC-02 AP242 still segfaults inside OCCT's AP242/PMI STEP read (#20),
  excluded from build tests.

## v0.1.5 — 2026-06-15

### Fixed

- CTC-02 spurious full-page line: build123d's `ExportSVG` projected
  circle-edge-on edges (hole/fillet rims seen edge-on) as elliptical arcs with
  a near-zero minor radius, which renderers blow up into full-page lines.
  `sanitize_svg_arcs()` rewrites any arc with a sub-1e-3 mm radius into the
  straight line it actually is, leaving real arcs untouched (#19). Not a PMI
  issue — the file is AP203 geometry-only.

### Tests

- Added the full NIST CTC set (01–05) as fixtures, both AP203 geometry-only and
  AP242 (with-PMI) variants.
- Heavy end-to-end CTC fixture builds are marked `slow` and deselected from the
  default `pytest` run (fast normal run, ~4.5 min); CI runs the fast tier across
  the OS/Python matrix and the slow tier once.
- Known: AP242 CTC-02 and both CTC-04 variants crash OCCT on import (#20); their
  fixtures are excluded from build tests.

## v0.1.4 — 2026-06-15

### Changed

- Feature annotations (hole callouts, location dimensions, section view) now
  fire on feature presence independent of the turned/prismatic classification,
  so turned-and-drilled parts (e.g. flanges) get both the OD/centreline base
  set and per-hole callouts plus bolt-circle furniture (#10).
- Isometric view placement now uses a general largest-empty-rectangle search in
  place of the wide/flat-on-A3 special case (#11).
- Concentric bore-leader stacking is generalised beyond three, and the
  step-height dimension gate is now a single derived constant (#10, #12).

### Internal

- Single-sourced duplicated geometry constants from the draft preset (#12).
- Minor comment and logging cleanups.

## v0.1.0 — 2026-06-14

Initial release — spun out of `build123d-drafting-helpers` v0.9.1.

The automated drawing engine (`make_drawing`, `build_drawing`, `Drawing`)
was previously part of `build123d-drafting-helpers`. It is now a separate
AGPL-licensed package that depends on `build123d-drafting-helpers>=0.9.1`
for annotation primitives.

### Migration from build123d-drafting-helpers

```python
# Before
from build123d_drafting import make_drawing, Drawing, build_drawing

# After
from draftwright import make_drawing, Drawing, build_drawing
```

### Features (carried over from build123d-drafting-helpers)

- **`make_drawing`** / **`build_drawing`** — automatic multi-view technical
  drawing from a build123d solid: view layout, scale selection, orthographic
  projection, dimension placement, title block.
- **`Drawing`** — composable drawing object with `.lint()`, `.add()`,
  `.export_svg()`, `.export_dxf()`.
- **`choose_scale`** — ISO/ASME standard scale selection.
- **`lint_feature_coverage`** — checks annotation coverage against detected
  part features (holes, bosses, bolt circles).
- **Section A–A views** — automatic section view for blind/stepped holes,
  with ISO 128-44 solid filled cutting-plane arrows and ISO 128-50 45°
  hatching on the cut face.
- **`generate_script`** — generates a standalone drawing script from a STEP
  file.
