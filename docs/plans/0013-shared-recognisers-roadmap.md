# 0013 — `b123d-recognisers` roadmap (uniform recognition, extraction-ready)

Execution record for [ADR 0013](../adr/0013-uniform-recognition-and-shared-package.md).
**Phase 1 and the code portion of Draftwright-leading Phase 2 are complete.** The standalone
Apache package is live at <https://github.com/pzfreo/b123d-recognisers>; `v0.1.0a1` is the
golden-backed migration release and Draftwright consumes it with its duplicate implementation
removed. mcp remains a slow follower until it chooses to adopt.

## Phase 1 — uniform, geometry-only, extraction-ready `recognition/` (now)

Delivers #568's value standalone. No new repo, no external dependency, no mcp coupling.

- **1a — the contract.** Adopt one recogniser shape (ADR 0013 §2):
  `recognise_<feature>(part, *, ...) -> list[<Feature>Record]`. Rename existing entry
  points onto `recognise_*` (from `find_*`/`analyse_*`); return typed frozen dataclass
  lists (retire `TurnedProfile | None` singular-optional and the bare untyped
  `-> list` in `_features.py`). **Keep dependency injection** for derived features
  (`recognise_patterns(part, *, holes)`, `recognise_step_shoulders(part, *, levels)`) —
  make it *keyword-only and typed*, not remove it; the orchestrator threads the single
  shared inventory (ADR 0008 Am5), recognisers never re-recognise a dependency. British
  spelling; add **codespell** to CI with the convention pinned.
- **1b — geometry-only records + `.to_dict()`.** Give each recogniser a plain-geometry
  record (points/axes/radii/angles, no build123d types in the output) carrying
  `.to_dict()`. These sit *below* the IR — they are the future shared-package surface.
- **1c — a uniform `detect.py` adapter protocol. ✅ DONE (#752).** The ad-hoc
  per-feature translators are replaced by a typed registry of per-record converters
  (geometry-record → IR `Feature`) dispatched one way through `convert(record, ctx)`;
  the per-type mapping stays (hole→`HoleFeature` ≠ chamfer→`ChamferFeature`), its
  inconsistency is gone. Completeness/uniqueness is fail-closed
  (`tests/test_detect_registry.py`): every record type has exactly one home across the
  uniform / derived / orchestrated tiers. (The `equal_leg`-on-both mirroring was already
  removed during #560 review.)
- **1d — the `callout()` crack.** Move callout formatting off `ChamferFeature` into the
  dimensioning layer (planner/IR) uniformly; geometric records carry no callout.
- **Keep `recognition/` dependency-self-contained** — build123d/OCP only, no upward
  coupling — so Phase 2's *code* move is a mechanical import swap. Licensing is a
  separate axis (ADR 0013 §7): files stay AGPL through Phase 1 and are relicensed to
  Apache at the Phase 2 gate (file-by-file, pzfreo owns copyright).

### Pilots (drive Phase 1 from real work, not a big-bang rewrite)

- **#558 (countersink)** — written to the §2 contract now, geometry mirroring mcp's
  `recognise_countersinks`, so it lifts to the package unchanged. First customer.
- **#561 (fillet)** — second pilot; first recogniser authored to the contract from
  birth. build123d's `fillet()` anchors the vocabulary.

Migrate the remaining seed-set recognisers (holes, bosses, cylinders, patterns,
chamfer) onto the contract **incrementally**, one per PR, as issues touch them — not
as a forced sweep.

## Phase 2 — extract to `b123d-recognisers` (✅ deployed, 2026-08-15)

Trigger: a second consumer commits to depend (in practice, mcp deciding to follow, or
another tool appearing). Until then, do not spin the repo.

- **2a — stand up the repo. ✅** New Apache-2.0 `b123d-recognisers`; move the seed-set
  recognisers + their geometry records + their tests (the geometric counter-examples —
  gusset/ramp/hex-prism etc. — are geometry truths and belong here). Fast CI (build123d/
  OCP only). Licensing (the Phase 2 gate, ADR 0013 §7): relicense each migrated file to
  Apache in its header (pzfreo owns copyright). Countersink seeds from mcp's
  already-Apache code, so it needs no relicense.
- **2b — publish + wire. ✅** `v0.1.0a1` was published from exact commit
  `4ba34fdcffac117dccc16c818485e779a92e2e29`; the same reviewed wheel and sdist were promoted
  TestPyPI-first through Trusted Publishing. Draftwright pins `b123d-recognisers==0.1.0a1`
  from PyPI for the cutover, with both artifact hashes captured in `uv.lock`.
- **2c — migrate draftwright imports. ✅** The internal→package swap is atomic; the
  uniform `detect.py` seam is unchanged.
- **2d — governance. ✅** Geometry-only shareability is enforced by package contract,
  import-boundary, serialization, public-inventory, and golden tests. Only shareable
  recognisers live in the package;
  domain-flavoured ones (Plate/Envelope/StepLevel/Turned; mcp's `locate`) stay home
  until a second consumer wants them.

## mcp (slow follower — not on the critical path)

When and only when mcp chooses to adopt: replace its deprecated
`build123d_drafting.find_holes`/`find_bosses` usage and its local `countersink.py`
with `b123d-recognisers`; keep its thin `find_*(session, name)` MCP wrappers over the
shared pure functions. No timeline; mcp's user base makes stability worth more than DRY
until it decides.

## Out of scope

Reconstructable construction ops / feature-tree recovery (a possible later provenance
layer); brep-pure recognisers (see ADR 0013 *Alternatives*).
