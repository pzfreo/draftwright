# draftwright-rust: an API-shaped model for the CLI and MCP (replacing the DSL and compilation model)

## Context
You suspect that draftwright's authoring DSL and compilation model haven't paid off. You want a model that works well for a CLI and an MCP server, in the style of pzfreo/specify-core.

**What draftwright has today** (read from the source; the ADRs themselves weren't in the snapshot):
- **Three authoring layers:**
  - a fluent Python `Sheet` facade (`sheet.py`, 3k lines, about 35 feature verbs plus aspect chains, GD&T and views);
  - `model/declare.py` (2.8k lines);
  - `build_drawing`, which takes about 40 parameters, including `model`, `decorations`, `requested` and `authored`.
- **A script emitter** (`sheet_emit.py`, 2.2k lines) that turns a detected part back into editable Python. Judging by the hosted tool names, an LLM interacts by editing that script and resubmitting it; the source doesn't confirm this.
- **Deferred-intent replay** (`intents.py`, `intent_routing.py`, `intent_drain.py`). Finalising snapshots and restores about 10 collections inside a hand-rolled transaction. The auto path and the replay path are kept in step by a shared sequence of about 30 passes.
- **A compiled IR:** `PartModel` → `DimensionGroup`s → `RenderableDimensionPlan`. Renderers may draw only from the compiled plan, a rule retrofitted after eight renderers bypassed it (#921).
- **Size:** about 12.5k lines of authoring surface and 35k of compile and orchestration code, out of 133k.

**What specify-core shows:**
- one stateless core API (`analyse → questions → apply → write`) with thin adapters (a CLI printing JSON, a JSON-lines `serve`; no MCP yet);
- plain JSON documents, with the answers as the editable source of truth;
- explicit, stable IDs anchored on face indices, and a binding hash to the source file;
- defaults that explain themselves (`basis`, `why`, `attention`);
- errors that say how to fix them ("reads as …", missing IDs);
- `preview` and `problems` before writing, and a verified write report;
- the answers stored inside the output, so work can be resumed.

## My assessment
You're mostly right.
- **Drop:** the authoring layers (fluent facade, script emitter, deferred-intent replay with its transactions). They cost the most and give an LLM the least: it has to write or edit code, run it in a sandbox and resubmit the whole script to change one thing.
- **Keep:** the narrow-waist idea in a simpler form. Features → a dimension and annotation plan → renderers that draw only from the plan. That boundary fixed a real class of bugs (#921), and an API needs it just as much: the plan is the thing a caller inspects and edits by ID.
- **What an API loses** is a program's loops and conditionals. Recognised features and patterns cover what drawings need, and anything else can be an explicit item in the spec.

## Proposed model (draftwright-rust)

**Three JSON documents, as in specify-core:**
1. **Analysis.**
   - Contents: built by `analyse(step)`. It holds the binding (source sha256, part, face count, loader version), the frame and envelope, the views available, and the features (from Rust quiddity, or Python quiddity's JSON through the bridge), each with a stable ID anchored on face indices.
   - Freshness: regenerable at any time.
2. **Spec, the source of truth, small and editable.**
   - Settings: sheet (size, scale, projection, title-block fields) and views (which, placement overrides, sections and details).
   - Overrides by stable ID: per feature (callout form, fit, thread, finish, notes) and per dimension (keep, drop, move, tolerance, format).
   - Additions: dimensions, notes, tables, GD&T and datums. Datum and GD&T content can come from specify-core's intent.
   - Defaults: anything absent takes its default.
3. **Plan.**
   - Contents: `plan(analysis, spec)` returns every view, dimension and annotation with a stable ID, its resolved value, where it is placed, and `default: true` plus a `basis` sentence wherever the spec was silent.
   - Problems: structured `problems` (overlaps, missing dimensions, unreachable callouts), each with an ID and a suggested edit.

**Operations** (one core in Rust, stateless, deterministic):
- `analyse(step) → Analysis`
- `plan(analysis, spec) → Plan` (the preview; returns its problems too)
- `edit(spec, ops) → Spec`: small patch operations by ID (`set`, `remove`, `add`), each validated with "choose one of …"-style errors. A caller can equally replace the whole spec.
- `render(plan, formats) → files`, using the existing `output::Drawing` and its SVG, DXF and PDF writers. The spec is embedded in the output's metadata (SVG metadata, PDF XMP or a document attribute, a DXF XDATA or a header comment) so it can be resumed.
- `review(plan) → image + problems`, for an LLM's visual check.

**Adapters, thin over the core, as specify-core's are:**
- **CLI:** `draftwright analyse part.step`, `draftwright plan part.step --spec s.json`, `draftwright render … -o out.pdf`, `draftwright serve` (JSON lines). A command's result is JSON on stdout; a refusal is a plain `error:` line on stderr with empty stdout and exit 2 for invalid or incomplete input or 1 for a failure. The JSON-lines service keeps JSON replies.
- **MCP server:** tools map one-to-one onto the operations (analyse, plan, edit, render, review). A tool call sends the spec, so the server keeps nothing; it can also hold a session spec by handle for convenience.

**Fit with the existing plan:** this replaces stage 2's "views only" target with "views only through the API". Stage 2 builds `analyse`, `plan` (views, scale, placement) and `render` end to end, with the CLI first and MCP as soon as the CLI works. Annotations (stage 4) are then added as plan items with IDs, defaults and basis, never as code.

## When the STEP changes: carrying drawing edits to a new revision

**The problem:** the spec's edits are keyed to features and faces. Face indices and quiddity's refs are only stable within one file: specify-core anchors on face indices and *refuses* a mismatched file ("analyse the file again"). Neither draftwright nor specify-core matches features across revisions today (draftwright's `feature_identity.py` covers type identity only). So the new model needs a rebinding step.

**Principle: the spec stores intent, never values that come from the geometry.**
- An override says "this hole's callout uses H7" or "this dimension sits 8 mm above its feature, to the left". It never says "Ø6.6 at x = 41.2".
- After a revision, every value is re-measured from the new part. The changed geometry flows into the drawing automatically, and only the *decisions* need carrying over.
- Placement overrides are relative to their anchor (an offset from the feature in that view, a side, an order in a stack), so they move with the feature. Collisions then show up as ordinary problems.

**Anchors carry a fingerprint.** Each spec entry keyed to a feature or face also records, when it is made, a geometric signature of its anchor in the part frame:
- **Features:** kind, axis, position, key sizes, pattern count and pitch.
- **Faces** (datums, GD&T targets): surface type and parameters, centroid, area, normal.

The ID is for speed within one file; the fingerprint is what survives a revision.

**Where it lives: in quiddity, not draftwright.** Recognising features and knowing which feature in a new revision is which belong together. Both consumers need it: draftwright for drawing edits, and specify-core, which today refuses a changed file outright. So quiddity(-rust) gains:
- **Fingerprints:** each recognised feature and face gets one in its result: a canonical geometric signature in the part frame, as described above. It is deterministic, serialisable and versioned.
- **`correspond(old_result, new_result) → Correspondence`:** a pure function over two recognition results (or over saved fingerprints, so the old STEP isn't needed). It returns:
  - the frame alignment between the two parts;
  - for each old feature or face, `carried`, `adapted` (what changed), `ambiguous` (the candidates) or `orphaned`;
  - the features that are new.
- **Exposure:** in the quiddity CLI (`quiddity correspond old.json new.json`) and in its JSON-lines and MCP surface, beside recognition.
- **Tests:** in quiddity-rust, against the revision pairs and invariance variants below, in the same capture-and-verdict style as the other families.

**What the literature says** (from a review with verified citations):
- **History-based persistent naming** (Capoyleas, Chen & Hoffmann 1996; Kripac 1997; Marcheix & Pierra's 2002 survey; OCCT TNaming; FreeCAD 1.0's element maps) needs construction history. We have none: only two STEP files.
- **History-free matching is our case.** Onshape solves it on every re-import with a deterministic matcher (Baran & Schulz, US 11,288,411):
  - bodies matched by a small signature and aligned;
  - faces matched first by exact coincidence;
  - then by overlap (the same surface, at least 80% overlapping);
  - then iteratively by adjacency signatures;
  - ties broken by heuristics, and the rest left unmatched.
- **Learned matching:** Jones et al. (SIGGRAPH 2023) learn the scoring on top of that matcher. They report the residue that stays genuinely ambiguous, where even users disagree, and leave it unmatched rather than guessing.
- **Anchoring on features:** Bidarra et al. (2005) argue for naming feature faces rather than boundary faces. Our recognised features are exactly such persistent, semantic entities, coarser and much less ambiguous than faces.
- **Rigid motion:** none of the published matchers handles it beyond translation. Vandenbrande et al. (US 8,576,224) find a rigid transform by matching distinctive planar faces with the Hungarian algorithm.

**So `correspond` works in two levels:**
1. **Align:** from features with distinctive signatures (unique holes, planar faces), solve a rigid transform with Kabsch or RANSAC, and accept it by the fraction of inliers. Otherwise use the identity and mark the result unaligned, which down-weights position.
2. **Features:** compare type and subtype, intrinsic sizes, axis, pattern count and pitch, and parent-face type; positions only once aligned. Assign per feature type with Hungarian over a cost matrix that has an explicit "unmatched" column. Classify:
   - **carried** when the cost is low and the winner clearly beats the runner-up;
   - **adapted** for the same identity with changed parameters;
   - **ambiguous** when the margin is small (typically pattern instances and symmetric features), reporting the candidates rather than guessing;
   - **orphaned** and **new** for what is left over.
3. **Faces** (datums, GD&T targets): seed from faces owned by matched features, plus exact or overlapping matches once aligned, then propagate by adjacency signature over the attributed adjacency graph (Joshi & Chang 1988), as Onshape's matcher does. This is deterministic and needs no training data.
4. **Learned matching:** left out. There is no labelled data, and feature-level anchors cover most of what drawings and PMI need.

**Thresholds:** the cost cut-off, the margin and the inlier fraction have no transferable published values. They are tuned on our own revision pairs.

Consumers stay thin:
- draftwright's `rebind` applies a Correspondence to its spec;
- specify-core can do the same to its answers map, replacing its refusal with a rebind and a list of questions.

Anchors in a spec or answers map then store quiddity's fingerprint, not a fingerprint of their own.

**`rebind(spec, correspondence) → (spec', report)`** in draftwright, run automatically by `plan` whenever the source hash differs from the spec's binding, using quiddity's correspondence:
quiddity performs steps 1–3; draftwright performs step 4 and applies the classes to its spec.
1. **Frame:** align the two parts' frames first (the same as both files' principal frame, or the rigid motion that best matches the features). This lets a moved or reoriented revision rebind too, and the corpus's `prismatic_*` variants test it.
2. **Matching:** match each fingerprint to the new analysis's features of the same kind, scored on position, axis and size within tolerances. Assignment is one-to-one (Hungarian, or greedy by score) so two holes can't both claim one.
3. **Classification:** each spec entry falls into one of five classes:
   - **carried:** an exact match; the edit applies unchanged.
   - **adapted:** matched, but the feature moved or resized. The edit applies; the report says what changed (e.g. "hole H3 Ø6.6 → Ø8.4, callout fit H7 kept"). A fit or thread that is now invalid for the new size becomes a problem with a suggested replacement, never a silent change.
   - **ambiguous:** several candidates score close. The entry is kept and a question is raised, listing the candidates and their differences. The caller answers through `edit`.
   - **orphaned:** the feature is gone. The entry is kept in the spec, marked inactive with the reason, so a later revision that restores the feature (or a manual re-anchor) can revive it. It is reported, not dropped.
   - **new:** a feature in the new part with no spec entries takes its defaults, and is listed so the caller can review it.
4. **Binding:** the new hash and analysis are recorded in the spec, with the report kept beside it in the output.

**For an LLM or a reviewer,** this works as specify-core's interview does. After `plan` on a new revision, the problems list shows the ambiguous and orphaned entries, and anything invalid after adaptation, each with IDs and suggested edits. Everything else carries through silently, but every item in the plan stays labelled carried, adapted or default, so nothing changes without a trace.

**Where the spec lives:**
- primarily as its own JSON file beside the STEP (`part.drawing.json`);
- also embedded in each rendered output, so a drawing can be re-opened from the PDF or SVG alone.

Optionally it could go inside the STEP as well, as specify-core stores its answers, so that one file carries both the PMI and the drawing intent. That is worth deciding once specify-core's intent and draftwright's spec are joined; datums and GD&T would then come from specify-core's intent rather than being duplicated.

**Testing rebinding:**
- **A revision capture tool:** build pairs of revisions in build123d (move a hole, resize a boss, add or remove a feature, translate or rotate the whole part), with the expected carried, adapted, orphaned and new classification for each.
- **The corpus's invariance variants:** each must rebind with every entry carried.

## Carrying over what Python draftwright learnt, and iterating while the model changes

**Where the knowledge is:**
1. **ADRs 1–5:** about 50 numbered invariants, each citing its tests, plus 20 archived ADRs.
2. **`linting/`** (about 20k lines): per-feature coverage rules, saying what a complete drawing of each feature must state, plus ink overlap, PMI coverage and the issue codes.
3. **`tests/`** (429 files, 159 issue-numbered regressions): concrete cases where a rule was learnt the hard way.

Most of it is drafting knowledge, but some of it is about the old compiler's own machinery.

**1. One rules catalogue, independent of the implementation** (`draftwright-rust/docs/rules/`, one YAML or Markdown entry per rule). Each entry records:
- an ID and a one-sentence statement in drafting terms, not code (e.g. "a blind hole's callout never reads THRU because its depth is unknown"; "an approved tolerance reaches the ink verbatim");
- where it came from: the ADR invariant, issue number, lint code and Python tests;
- its class:
  - **drafting rule:** keep and port;
  - **trust rule:** determinism, honest failure, lint independent of the plan; keep;
  - **old-machinery rule:** the pass sequence, the deferred-intent drain, two dimension sources, Sheet identity; retire, or translate into the new model's terms;
- its status: **pending**, **ported** (with the Rust test that checks it), **translated** (the new rule), or **retired** (why).

**How the catalogue is built:** a one-off extraction pass over the ADRs, the lint codes and the issue tests, done by agents reading the Python. Every entry cites its source, and you review the classifications, which are where judgement is needed.

Even before any Rust exists, this is the checklist of what the port owes.

**2. Iterate with differential checks, not pixel parity.** Changing the model means Rust's sheets won't match Python's layout exactly, and they shouldn't have to. So the comparison is semantic, in the same verdict style as the kernel spikes:
- **A semantic capture:** a capture tool runs Python draftwright over the corpus and records each sheet as facts:
  - the views and their scale;
  - each dimension's measurement identity (feature, parameter), value, tolerance and label text, and which view it is in;
  - callouts, notes and tables;
  - the lint findings.
- **The comparison:** Rust's plan is compared fact by fact, and every difference gets a verdict (rust-correct, rust-wrong, equivalent, undetermined), as `known_drawings.json` does today.
  - Layout itself (positions, overlaps, clearances) is checked by rules, not by matching Python.
- **The lints as an independent checker:** the coverage lints are ported to run on a rendered plan, so they can judge Python's sheets and Rust's alike.
  - Run on Python's sheets, the port should find what Python's own lints find. That validates the port.
  - Run on Rust's sheets, they check the new model's output.
  - This keeps ADR 5's invariant that lint never takes its denominator from the plan.

**The loop while the model changes:**
- **A rule progress report:** CI reports how many rules are ported and passing, how many are pending, and the differential verdicts. A model change that breaks an old lesson then shows up as a specific rule or a corpus difference, not as a vague regression.
- **Porting issue regressions:** each one becomes a small scenario test against the API (an analysis or STEP, plus a spec, then a property of the plan or of the problems found), so it survives later changes to the model.

**3. ADRs: new ones for the new model; the Python ones kept as they are.**
- **The Python ADRs stay in the Python repo,** unedited. They are the record of why things were done.
- **draftwright-rust starts its own short ADR series,** each of which says what it adopts, translates or supersedes:
  - **R1, the document model:** analysis, spec and plan; operations; CLI, JSON-lines and MCP adapters. It supersedes Python ADR 4's authoring surface. ADR 4's sound invariants carry over in new terms, for example "no authoring surface carries page geometry" becomes "the spec stores intent, never geometry values", and "nothing approved vanishes silently" carries over as it is.
  - **R2, the kernel:** haecceity, with accuracy over OpenCascade parity and the verdict system. It supersedes the recognition and geometry assumptions in ADRs 1 and 3.
  - **R3, revisions:** quiddity's correspondence and the rebind.
  - **R4, carried invariants:** a table of every Python ADR invariant, marked kept, translated or retired. It links to the rules catalogue, so nothing is dropped by accident.
- **ADRs 2 (layout) and 5 (trust):** mostly carried over through R4, since they are largely implementation-independent. They get their own Rust ADRs only when the Rust layout work starts.

## Deliverables of this design step
1. **A design note** in draftwright-rust, `docs/model.md`, recording:
   - the decision and its reasons (the evidence above);
   - the three documents with JSON examples;
   - the operations, the ID scheme (feature IDs anchored on face indices; annotation IDs derived from feature, parameter and view), the error and problem shapes, and how the spec is embedded in the output for resuming;
   - rebinding across revisions: how quiddity's correspondence (fingerprints, matching, the classes) is applied to the spec, and the report.
2. **ADR R1–R4 and the rules catalogue:**
   - the extraction run over ADRs 1–5, the lint codes and the issue tests;
   - entries classified with their sources, for your review;
   - R4's invariants table.
3. **A semantic capture tool** in draftwright-rust (`tools/capture_sheet_facts.py`), and the fact-level differential comparison with its verdict list. It is used from stage 2 on.
4. **In quiddity-rust,** a short design note (`docs/correspondence.md`) on fingerprints and `correspond`, shared with specify-core's needs. It is to be implemented once draftwright has feature overrides to carry (stage 4), or sooner if specify-core needs it first.
2. **Rust types** for Analysis, Spec, Plan, Problem and EditOp in `src/model.rs`, with serde, plus a skeleton of `analyse`, `plan` and `render` covering views only. This reuses `haecceity::hlr::project` and `output::projected`, with the view placement from `examples/views.rs` as a first version.
3. **A CLI binary** `src/bin/draftwright.rs` with `analyse`, `plan` and `render` (with `serve` and MCP to follow in stage 2).

## Verification
- `cargo fmt`, clippy and tests pass.
- `draftwright analyse` → `plan` → `render` on m10127, string_post and nist_ctc_01 produces files. Their views match draftwright's views-only sheets geometrically, as in stage 2.
- Round trip: edit a view override through `edit`, re-plan, render, and read the spec back out of the rendered file.
- Rebinding, once feature overrides exist (stage 4): every corpus invariance variant rebinds with all entries carried. Built revision pairs classify as expected.

## Next steps (2026-10-08)

Reviews of both repositories (`docs/review-2026-10-08.md` here and in quiddity-rust) set the
order. State at review: quiddity-rust at 2fa7207 (28 recogniser entry points, correspondence in
the library and CLI, CI red on a float-dependent pin); draftwright-rust at 971e17e (document
model, layout, sections, first dimensions, lint for holes).

1. **Fix what the reviews found first.** In quiddity-rust: face areas on sphere and B-spline
   faces (and the red CI), volume probes that read a failure as air, NURBS ray seeds dropped
   silently, broken STEP files accepted, fingerprints missing family sizes, corpus checks
   skipped on 8 files, test runtime. Here: lint not seeing the port's own callouts, countersink
   keys, the zero-span NURBS panic, degree ≥ 4 Bézier output, DXF bytes depending on the time
   zone, corpus tests that skip silently, CI, and stale docs and catalogue statuses.
2. **Make the next ports cheaper.** quiddity-rust: shared FaceGraph and chain helpers, policy
   constants out of the kernel, one families table. Here: honest model boundaries (revision
   mismatch, dropped section, edit and output paths), an info problem for every feature kind
   without a dimension planner, side-view bands.
3. **Port the remaining quiddity families** in Python's dependency order: slots, pockets and
   channels with their patterns; prismatic pockets; passages and section passages, then oriented
   slots and section recesses; blends; freeform surfaces and sheet metal (on thin walls);
   repeating radial profiles; rectangular pads, polygonal bosses and stock.
4. **Drafting work here,** each step with its rules from `docs/rules/` set to their true status
   and compared with the captured sheet facts: dimensions for the next families (fillets,
   chamfers, slots, bosses, steps), placement and collision rules, notes and tables, shrinking
   `known_views.json` and `known_dimensions.json` with evidence, searchable ⌀/↧/⌴ text (done:
   PDF and SVG, ISSUE-1352-a), PNG
   output (done: `src/png.rs`), PDF font subsetting (done: `src/output.rs`).
5. **Recognition from quiddity-rust directly.** quiddity-rust publishes a versioned recognition
   document (schema version, family, record type, defining faces, parameters); draftwright then
   reads it family by family instead of Python's JSON, and correspondence is exposed through the
   JSON-lines and MCP surfaces for the rebind (R3) once feature overrides exist.

Decisions these steps need are listed as questions in the two review documents; nothing above
assumes their answers.
