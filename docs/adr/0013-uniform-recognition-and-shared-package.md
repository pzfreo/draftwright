# ADR 0013 — A uniform recogniser/feature contract (with `b123d-recognisers` as its deferred shared deployment)

- **Status:** Accepted; Phase 1 is in progress (the core contract is enforced, typed adapter registry pending), and Phase 2 package extraction is deferred.
- **Date:** 2026-07-12
- **Deciders:** Paul Fremantle (pzfreo)

**Amendment 1 (2026-07-12, #584 WP3) — the contract is now mechanically enforced.**
The three claims the #584 audit found asserted-but-unenforced are real as of this PR:
(a) **no sibling re-recognition** — `recognise_holes` no longer calls
`recognise_countersinks` internally; the caller (`analysis.py` / `detect.py`) owns the
single inventory and injects `csinks=` (§2, ADR 0008 Am5); (b) **uniform serialization** —
every record inherits a `Record` mixin giving `.to_dict()`, and a contract test
(`tests/test_recogniser_contract.py`) proves each recogniser's records are frozen and
JSON-serializable (no build123d type leaks); (c) **codespell-enforced** — wired into the
CI lint job. The recogniser-signature shape (part + keyword-only) is asserted by the same
contract test.

**Amendment 2 (2026-07-18) — the *derived* shape is part-less.** §2's derived-feature
example (`recognise_patterns(part, *, holes)`) predates the implementation. A derived
recogniser is a pure function of already-recognised records and never touches the
solid, so `part` would be a dead argument. The sanctioned derived shape is
`recognise_hole_patterns(holes)` — the inventory positional, no `part` — exactly as
`recognition/__init__.py` documents and `tests/test_recogniser_contract.py` enforces.
The base-feature shape (`part` first, keyword-only tuning/deps) is unchanged.

## Context

Two findings, one from inside draftwright and one from a sibling project, meet here.
The **first is the reason** (draftwright's own recogniser intake is inconsistent and
should be fixed regardless); the **second is the opportunity** (a sibling project needs
the same recognisers, so the fix is worth extracting once it has proven out).

**1. The recogniser layer has drifted; the feature layer has not.** draftwright's
IR `Feature` types (`model/ir.py`) are uniform and enforced: every one is a frozen
dataclass conforming to the `Feature` Protocol (`kind: ClassVar[str]`, `frame`,
`parameters() -> list[DimParameter]`, `references() -> list[Datum]`). That is ADR
0008's "one inventory" waist, and it holds. The **recognisers that feed it do not**.
They have accreted with no shared contract:

- **naming** — `find_chamfers` / `find_plates` / `find_holes` / `find_slots` vs
  `analyse_cylinders` / `analyse_face_levels`. Two verbs, no rule.
- **return shape** — most return `list[SomeDataclass]`, but `find_turned_steps`
  returns `TurnedProfile | None` (singular-optional) and the vendored
  `_features.py` recognisers return bare untyped `-> list`.
- **signature** — some take just `part`; others take tuning knobs (`tol`,
  `max_leg_frac`, `min_area_frac`); others take precomputed inputs (`cyls=`,
  `levels`, `holes`).
- **provenance** — `_features.py` (holes/bosses/cylinders) is vendored from
  build123d-drafting in an older untyped style (ADR 0007); the native recognisers
  (chamfers, plates, slots, turned, levels) use typed frozen dataclasses. Two
  authoring styles coexist.
- **structural duplication** — each recogniser has its own dataclass (`Chamfer`,
  `Plate`, `Slot`…) that `detect.py` hand-translates into a mirror IR `Feature`.
  Fields and logic get duplicated across the two layers; the dead `equal_leg`
  removed from `recognition.Chamfer` during #560 review was exactly this — defined
  on both the recognition dataclass and `ChamferFeature`, only the latter used.

The asymmetry is the insight: ADR 0008 got the *waist* right and the *intake*
wrong. #568 was filed to pull the intake up to the standard the features already
meet.

**2. A second project independently needs the same recognisers.**
`pzfreo/build123d-mcp` gives an LLM CAD "eyes". Modelling is fine — the LLM builds
with build123d and *knows* the features it created. **Editing** is where recognisers
come in: to edit a solid the LLM did not just build (imported, or handed over), the
tool must recover feature intent from geometry. mcp has already started: a
`tools/recognizers/` package whose `__init__` says, verbatim, it is "kept
self-contained (build123d/OCP only, no session coupling in the pure recognizer
functions) so they can be repatriated into a shared permissive recognition package
later without a rewrite." It contains a `recognise_countersinks(part)` written
because the external hole recogniser reports countersinks as plain openings.

The duplication is **live, not hypothetical**:

- draftwright's **#558 (countersunk hole)** is next-but-one in the current
  dimensioning batch — a second countersink recogniser, same geometry (a cone
  flaring from a coaxial bore), being written in the same month.
- mcp's `find_features.py` still wraps the **deprecated** `build123d_drafting.find_holes`
  / `find_bosses` — the very helpers recognisers ADR 0007 moved draftwright *away*
  from (draftwright vendored its own copy into `_features.py`). Holes/bosses have
  already forked: mcp on the old helpers copy, draftwright on its vendored copy.

**Why build123d does not already solve this.** build123d has the *vocabulary*
(`Hole`, `CounterBoreHole`, `CounterSinkHole`, `chamfer()`, `fillet()`, `Slot*`) but
as *construction operations*, not a persistent feature model. It is procedural: it
runs the op, mutates the B-rep, and discards the recipe. The resulting `Solid` is
dumb faces and edges — no "this is a countersink" tag survives. Parametric CAD keeps
a feature tree; build123d does not. That discard is precisely why recognisers exist:
to recover the intent build123d threw away. The one case where intent survives is
when the caller still holds the build-time Python objects — which is ADR 0011's
declare-path (`build_drawing(part, model=…)` skips recognition). Recognisers are the
*fallback* for when the recipe is gone: imported STEP, or a bare solid. mcp's editing
case and draftwright's STEP-import case are the same fallback.

So features can reach the IR from **three provenances**: recognition-from-BRep (this
ADR), caller declaration (ADR 0011), and STEP AP242 PMI (`pmi.py`). One feature
model, three sources.

## Decision

**Read in priority order.** The load-bearing decision is the **uniform
recogniser/feature contract (§2)** and the **two-layer model (§3)** — they make the
recogniser intake as consistent as the IR `Feature` inventory already is (ADR 0008),
and they stand on their own (Phase 1 / #568) *even if the shared package never ships*.
Consistency is the goal; it is not contingent on extraction. The **shared package
(§1)** is the *deployment* of that consistency to a second consumer — important, but
secondary, deferred, and gated (§5). Sections are numbered for stable cross-referencing,
not by priority; §2/§3 are the decision, §1/§5/§6 are how and when it travels.

### 1. The shared package `b123d-recognisers` (the deferred deployment)

The eventual home for BRep feature recognition is a standalone package:

- **Name** `b123d-recognisers` (dist), `b123d_recognisers` (import). Deliberately
  informal — a recogniser toolbox, not a framework.
- **License Apache-2.0.** Forced by the consumers: mcp is Apache and cannot depend
  on AGPL; draftwright (AGPL) depends on Apache freely, not the reverse.
- **Input: a build123d object; BRep-powered inside; geometry-only output.** The
  recognisers already are this — build123d for traversal (`.faces()`,
  `filter_by(GeomType…)`, `.bounding_box()`), raw OCP for the hard predicates
  (`BRepAdaptor_Surface`, `BRepClass3d_SolidClassifier`). Identity, input and
  vocabulary are build123d (the round-trip: build with `Hole`, recognise a `hole`);
  the *implementation* drops to BRep where it must. See *Alternatives* for why not
  brep-pure.
- **Output records are plain geometry** — points, axes, radii, angles; **no
  build123d types leak into the output**, so downstream consumers are decoupled from
  build123d in what they receive. Each record carries `.to_dict()` for JSON
  (mcp's need) and stays a typed frozen dataclass (draftwright's need).
- **Scope: measurement records, not reconstructable construction ops.** A recogniser
  reports *what is there* (a countersink: opening ⌀, drill ⌀, included angle, depth,
  axis, location). It never rebuilds a build123d object or recovers a feature tree.
  mcp's LLM does the reconstruction from the record; draftwright dimensions from it.
  This keeps every parametric-CAD hard problem (feature ordering, dependency, the
  original sketch) *out of scope*.
- **British spelling throughout** (`recognise_*`), **codespell-enforced** so the
  convention does not depend on anyone remembering it.

### 2. The uniform recogniser/feature contract — the primary decision (subsumes #568)

This is the point of the ADR. Every recogniser, shared or not, conforms to one shape:

```
recognise_<feature>(part, **tuning) -> list[<Feature>Record]
```

- verb `recognise_` (not `find_`/`analyse_`); one naming rule;
- always a `list` of typed frozen dataclass records (no `Optional`-singular, no bare
  `list`); empty when absent. Where a recogniser's record first looks "too thin" for a
  list (a face level, a turned step), the fix is the **record** (make it self-contained —
  `TurnedStep` carries its `axis`), not an exception to the rule;
- `part` first, then **keyword-only** args: tuning knobs *and* injected shared
  inventory. A *base* feature (holes, chamfers, fillets, bosses, cylinders) derives
  only from `part`. A *derived* feature takes the canonical inventory it depends on
  by keyword — `recognise_patterns(part, *, holes)`, `recognise_step_shoulders(part,
  *, levels)`. **Dependency injection stays; the recogniser never re-recognises a
  dependency internally.** ADR 0008 Amendment 5 mandates one inventory detected once;
  recomputing holes inside `recognise_patterns` would produce a *second*, possibly
  divergent hole set and break pattern grouping (which relies on shared hole member
  identity). The orchestrator owns the single inventory and threads it. What the
  contract forbids is *inconsistent* signatures (positional precomputed inputs, ad-hoc
  ordering), not injection itself.
- **pure** — build123d/OCP only, no session, no drawing, no dimensioning types.

### 3. Two-layer decoration

The shared record is the **geometric** feature; each consumer **decorates** it with
its own domain concerns:

- **draftwright** adapts the geometric record → its dimensioning IR (`Feature` with
  `parameters()`/`references()`). This is **not** one universal converter: a
  hole→`HoleFeature` (bore/cbore/spotface + `DimParameter` semantics) is genuinely
  different from a chamfer→`ChamferFeature`, so a per-record-type conversion is
  irreducible. What `detect.py` gains is a **uniform adapter protocol — a typed
  registry of per-record converters** dispatched one way — replacing today's ad-hoc,
  each-different bespoke translators. Normalization removes the *inconsistency* and
  the recognition-dataclass ↔ IR-`Feature` mirroring duplication, not the per-type
  mapping.
- **mcp** serialises the record to JSON for the LLM to reason over when editing.

Same countersink, different decoration: mcp wants a measurement record; draftwright
wants `HoleFeature` + `DimParameter`s. The *shared* layer is the geometry both agree
on.

### 4. Governance boundary — what is shared vs what stays home

**The shared package is geometry-only. Domain interpretation stays per-consumer.**
That line is what stops it accreting the union of two wishlists.

- **Seed set (shareable, overlapping):** holes, bosses, cylinders, patterns,
  countersink, chamfer, fillet.
- **Stays home until proven shareable:** draftwright's dimensioning-flavoured
  Plate / Envelope / StepLevel / Turned recognisers; mcp's editing-specific
  `locate`. A recogniser is promoted to the shared package only when a second
  consumer actually wants it — never speculatively.

### 5. Sequencing — Phase 1 now (internal), Phase 2 deferred (extraction)

With mcp a slow follower (§6), `b123d-recognisers` would have exactly **one**
consumer today. Standing up a separate repo + CI + PyPI release for a single consumer
is premature — extract on the *second committed* consumer, not the first.

- **Phase 1 — now, inside draftwright (no repo, no external dependency, no mcp
  coupling).** Make `recognition/` the uniform (§2), geometry-only,
  **extraction-ready** subpackage: apply the §2 contract, introduce geometry-only
  records *below* the IR, replace `detect.py`'s bespoke translators with the uniform
  adapter protocol (§3), and fix the callout-crack (§7). "Extraction-ready" is a
  **dependency** claim, not a licensing one: keep the subpackage self-contained
  (build123d/OCP only, no upward coupling) so the *code* lift-out is a mechanical
  internal→package import swap. **Licensing is a separate axis** — the files are AGPL
  as draftwright code today (ADR 0007 §4); Apache licensing is handled per §7, not by
  self-containment. This mirrors exactly the "repatriate later" note mcp already wrote
  on *its* `recognizers/` package. **Phase 1 delivers #568's value on its own,
  independent of whether Phase 2 ever happens.**
- **Phase 2 — deferred, gated on a second committed consumer.** Spin the standalone
  Apache repo; publish `0.1.0` to PyPI once (so external users resolve a real dep);
  wire both consumers via **uv `[tool.uv.sources]`** — an editable local path
  (`{ path = "../b123d-recognisers", editable = true }`) or pinned git rev during
  co-development, flipping to a plain `b123d-recognisers>=0.1` PyPI spec only when
  cutting a release. That "dev against a local checkout, release against PyPI"
  workflow keeps day-to-day velocity unchanged from single-repo work. Migrate one
  recogniser per PR, driven by the next issue that touches it — never a
  stop-the-world migration.

**Pilots:** #558 (countersink) is written now to the §2 contract with geometry
mirroring mcp's `recognise_countersinks`, so it is liftable unchanged; #561 (fillet)
is the second, and the first recogniser authored to the contract from birth rather
than retrofitted.

### 6. mcp is a designated slow follower

mcp is more mature, has more users, and its editing feature is a side-shot. It takes
**no dependency on `b123d-recognisers` now**; it keeps its current recognisers
(helpers-based `find_holes`, its own `countersink.py`) untouched and adopts the shared
package **later, at its own cadence and risk**. draftwright is the lead adopter and
absorbs the interface-churn risk on behalf of a codebase with fewer users. When mcp
does follow, it retires its deprecated `find_holes` usage and its local countersink
copy onto the shared source.

### 7. Licensing and the callout crack

- **Licensing is an explicit act, not a side effect of self-containment.** Every
  file in `recognition/` is **AGPL as a draftwright file today** — the native
  recognisers (chamfers.py, plates.py, slots.py, turned.py) as new AGPL code, and even
  `_features.py` which, though vendored from Apache helpers, became AGPL inside
  draftwright (ADR 0007 §4). The target package is Apache, so extraction candidates
  must be relicensed. **Decision: relicense at the Phase 2 gate.** Files stay AGPL
  through Phase 1; when a recogniser is extracted its file header is relicensed to
  Apache — a one-line change (pzfreo owns the copyright), but a **deliberate, tracked
  step**, so extraction is an explicit licensing **gate**, not a purely "mechanical"
  move. (The alternative — dual-licensing each file `Apache-2.0 OR AGPL-3.0` during
  Phase 1 to make Phase 2 turnkey — was considered and not taken; keeping Phase 1
  licensing untouched is simpler and the relicense is cheap at the gate.) The
  countersink seed is lifted from mcp's *already-Apache* `countersink.py`, so the
  shared-package copy of that one needs no relicense regardless.
- **Fix the `callout()` crack as part of Phase 1.** `callout()` currently lives only
  on `ChamferFeature` (added in #560); every other feature leaves label formatting to
  the planner/`DimParameter`. Decide it *once*: callout formatting lives uniformly in
  the dimensioning layer (planner/IR), not accreted per-feature. Geometric records in
  the shared package carry no callout — formatting is a draftwright dimensioning
  concern, not a geometry fact.

## Consequences

- **The intake reaches the standard the features already meet.** One recogniser
  contract; `detect.py` becomes a uniform adapter protocol (a typed registry of
  per-record converters — the per-type mapping stays, its ad-hoc inconsistency goes);
  the recognition-dataclass ↔ IR mirroring duplication is removed.
- **The live countersink duplication is resolved at the pilot,** and mcp's fork onto
  the deprecated helpers `find_holes` gets a reconciliation path (when it follows).
- **draftwright takes no new dependency in Phase 1** — recognition stays internal but
  shaped for extraction. The extraction *code* move is a mechanical import swap when
  justified; its *licensing* is handled per §7.
- **The shared surface is small and low-churn by construction** (geometry only;
  dimensioning and editing churn stay in the consumers), so the three-repo
  coordination tax Phase 2 introduces is naturally bounded.

## Risks

- **Interface not yet validated by two consumers.** Deferring extraction (Phase 2
  gate) is the mitigation: the contract proves itself in draftwright first; mcp
  stress-tests it only when it chooses to follow.
- **Phase 1 could stall at "extraction-ready" forever.** Acceptable: Phase 1 *is*
  #568 and delivers value alone. Phase 2 is a bonus unlocked by a second consumer,
  not a debt.
- **Relicensing.** File-by-file relicense of AGPL-native recognisers to Apache is
  deliberate and must be explicit per file; pzfreo owns the copyright, so it is a
  header change, not a legal negotiation.
- **Slow-follower drift.** While mcp stays on the old helpers recognisers, holes/bosses
  remain forked. Accepted: mcp's larger user base makes stability worth more than DRY
  until it chooses to reconcile.

## Alternatives considered

- **Brep-pure recognisers (input `TopoDS_Shape`, OCP-only).** More fundamental, bigger
  theoretical audience (any OCC tool). Rejected: both real consumers feed build123d
  objects (even STEP imports arrive build123d-wrapped); going brep-pure means
  rewriting traversal against raw `TopExp`/`TopoDS`/`BRep_Tool` — *more* code, more
  fragile, against "lightweight"; and it forfeits the build123d vocabulary round-trip.
  The one real argument (depend on stable OCP, not churny build123d) evaporates because
  both consumers pin build123d anyway. Escape hatch kept: geometry-only output plus a
  one-line input normalise (`getattr(part, "wrapped", part)`) leaves the door open to a
  brep consumer *if one ever appears*, without building for it now.
- **Extract the package now (Phase 2 immediately).** Rejected: one committed consumer;
  premature repo/CI/PyPI overhead; locks the interface before a second consumer tests
  it.
- **Keep duplicating per project.** Rejected: the duplication is live (countersink) and
  compounding (holes/bosses already forked).
- **Put recognition (back) into build123d-drafting-helpers.** Rejected: ADR 0007
  deliberately moved recognition *out* of helpers because helpers is the *rendering*
  library. A shared *recognition* package is a different thing; helpers stays
  render-only.
- **Reconstructable construction ops (round-trip to editable build123d code).**
  Rejected as out of scope: mcp's LLM reconstructs from a measurement record; it does
  not need the lib to rebuild objects. Full feature-tree recovery could be a later
  provenance layer, not part of this ADR.

## Impact on other ADRs

- **0007** (draftwright owns recognition + lint) — **amended** (Amendment 1): the
  long-term home for recognition sharpens from "vendored inside draftwright" to "an
  extraction-ready subpackage (dependency-self-contained; licensing per §7) destined
  for the standalone `b123d-recognisers` package"; helpers stays render-only; mcp is a
  slow follower. Not
  a reversal — 0007's point was "not in the *rendering* library", and it still is not.
- **0008** (the Feature/DimParameter IR waist) — **amended** (Amendment 7): the "one
  inventory" waist is now two tiers — a shared *geometric* recognition record (lower)
  feeding draftwright's *dimensioning* IR (upper) through a uniform `detect.py` adapter
  protocol. The IR `Feature` Protocol stays draftwright-side; no recognition object crosses
  the boundary (Amendment 6 preserved — the geometric record is not a recognition
  *object*, it is a decoupled geometry fact-sheet the seam adapts).
- **0011** (IR as public input) — unaffected in decision; noted: recognition output now
  anchors on build123d construction vocabulary, matching 0011 P0's object→feature
  constructors, giving a build ↔ recognise ↔ dimension round-trip on one vocabulary.
- **0005** (pipeline DAG) — unchanged: `recognition/` stays the bottom layer (build123d
  only, below `_core`); Phase 2 turns it into an external dependency at the same DAG
  position.
- **0010** (annotation provenance) — unaffected.

## Related

- ADR 0007 (own recognition), 0008 (the IR waist), 0011 (IR as input), 0001 (declare/
  detect converge at the IR).
- Issues: #568 (uniform recogniser pattern — subsumed by Phase 1), #558 (countersink
  pilot), #561 (fillet — second pilot).
- Sibling: `pzfreo/build123d-mcp` `tools/recognizers/` (the "repatriate later"
  package this ADR formalises the target for).
- Roadmap: [`docs/plans/0013-shared-recognisers-roadmap.md`](../plans/0013-shared-recognisers-roadmap.md).
