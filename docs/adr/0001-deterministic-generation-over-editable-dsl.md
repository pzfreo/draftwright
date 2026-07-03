# ADR 0001 — Deterministic generation and domain-semantic editing over a bespoke editable-code DSL

- **Status:** Accepted
- **Date:** 2026-06-16
- **Deciders:** Paul Fremantle (pzfreo)

## Context

draftwright was originally conceived to *generate code*: a build123d +
`build123d-drafting` script that, when run, produces a fully-annotated technical
drawing. The premise was that emitting code (rather than only SVG/DXF) would make
the result easy for humans **and AIs** to edit.

In practice two things became clear:

1. **The generated "code" is not what the premise assumed.** `generate_script`
   emits a thin wrapper around the engine — `dwg = build_drawing(STEP_FILE, …)`
   plus a "customise here" surface (`dwg.add` / `dwg.remove` / `dwg.at` /
   `dwg.add_view`) — not fully-expanded primitive calls that spell out every view
   and dimension.

2. **The deeper problem is fluency, not verbosity.** The drawing *domain* —
   orthographic views, ISO/ASME dimensioning, section views, title blocks — is
   well represented in public material; humans and AIs reason about it readily.
   What is unfamiliar is draftwright's *DSL for expressing a drawing*: the
   `Drawing` object, the strip/zone layout model, `dwg.at`, `_legible_steps`, and
   so on. There is essentially **no public corpus of "technical-drawing-as-code,"**
   so a model cannot be pretrained-fluent in this API; it must relearn it from
   context on every call. This is the root cause of the observed gap where
   API-driven (one-shot) output underperforms interactive sessions: in a session a
   fluent operator reasons in the domain and merely *drives* the tool; via the API
   the model is asked to *author an unfamiliar DSL* from scratch.

There is also **no dominant public "drawing-as-code" library to adopt** in place
of our own convention (FreeCAD TechDraw and similar are not meaningfully present
in training data), so "ride a familiar convention" is not an available option.
The closest universally-understood targets are SVG (fluent, but semantics-free —
a dimension is just lines + text) and the build123d **solid** model (genuinely
fluent, but it describes the part, not the drawing).

## Decision

Treat **deterministic correctness as the primary interface**, and make any
necessary editing happen in a vocabulary the editor already speaks. Concretely:

1. **The best convention is no convention.** Invest in making the engine "get it
   right the first time" — scale/page selection, legibility gates, placement —
   so that in the common case nobody needs to touch draftwright code at all. Each
   such improvement makes the unfamiliar DSL *unnecessary* rather than better
   documented.

2. **Expose editing in domain terms, not layout-engine terms.** Where a caller
   must adjust the drawing, the API surface should be the **domain** vocabulary a
   model already knows (features such as holes/bores/sections; intent such as
   "dimension this bore's depth"), not the internal strip/zone machinery. The
   roadmap's "Cluster B" primitives (`dwg.features()`, `place_dim()`,
   `annotations()`, lint `suggestion`s, the lint→repair loop) are reframed as a
   **domain-semantic layer**, not a layout layer.

3. **De-emphasise bespoke editable-code generation.** `generate_script` remains a
   convenience for reproducible builds, but it is **not** the strategic path to
   AI/human editability and should not accrue investment aimed at that goal. Full
   literal-primitive expansion (every view and dimension as code with computed
   page coordinates) is explicitly **rejected**: it converts a black box into a
   brittle transcript of dead coordinates that is harder, not easier, to edit
   safely (move one view and nothing follows; an editor has no constraints to
   reason with).

4. **Use familiar vocabularies for the cases they fit.** When a part needs to
   change, prefer editing the **build123d solid** (a familiar API) and
   re-drafting. Reserve raw **SVG** editing for last-mile visual nudges, not
   semantic changes.

## Consequences

**Positive**
- Effort concentrates on the highest-leverage gap (deterministic quality + a
  machine-readable lint/repair loop), which is permanent and free per run.
- The public API we ask callers/AIs to learn shrinks to domain concepts they
  already understand, reducing the in-context learning burden per call.
- Avoids over-investing in a code-generation path whose value proposition is
  undermined by the lack of API fluency.

**Negative / costs**
- The "edit the generated code directly" story is intentionally weakened; users
  expecting fully-expanded primitive scripts will not get them.
- A domain-semantic API is more design work than dumping primitive calls, and it
  must be kept genuinely domain-shaped (resisting leakage of strip/zone concepts).
- Deterministic correctness has a long tail; "no convention needed" is an
  asymptote, and there will always be parts that need manual intervention.

**Neutral / follow-ups**
- Revisit if a widely-known technical-drawing-as-code convention emerges in
  public training corpora — adopting it could change the calculus.
- The lint score/`lint_summary()` is the contract the repair loop and any
  external editor consume; keep it stable and domain-meaningful.

## Amendment 1 — Both inputs converge at the detected IR; the edit surface is the model, not the emitted script

- **Status:** Accepted
- **Date:** 2026-07-03

**Why.** draftwright has two input scenarios: a STEP file (the CLI path) and
build123d objects (the library path). Revisiting the "editable script" question
(#388) surfaced that these should not have *separate* edit stories — and the
architecture already unifies them. `make_drawing(step)` lifts the STEP to a
build123d solid; `build_drawing(solid)` takes one directly; both then run the
ADR-0008 hourglass `detect → PartModel (IR) → plan → render`. So by the time
anything is dimensioned, **both inputs are the same detected feature model.**

**Decision — reaffirm and sharpen §2/§3.** The editable surface is neither the
rejected primitive dump (§3) nor a bespoke serialized DSL, but the **detected
`PartModel` IR itself**, exposed read-only on the `Drawing`:

- **One representation for both inputs.** Normalize any input to a solid → detect
  once → the `PartModel` is the provenance-agnostic "what is here and why." This
  is deliberately *not* raw build123d topology: a STEP import yields anonymous
  B-rep faces/edges (no tags/names), semantically poorer than authored objects;
  the detected model is uniform and semantic for both. (This is the concrete form
  of "get me from a STEP input to somewhere like if I had build123d objects.")
- **Edit against the model, re-solve deterministically.** Tweaks reference feature
  handles from `dwg.model()` (read half, #397) via a feature-targeted write API
  (#398), and `finalize_drawing(dwg)` (#388) re-runs the ADR-0009 layout. No dead
  coordinates; edits survive a re-draft — the property §3 rejected primitive dumps
  for lacking.
- **Topology is an optional accelerant, not a dependency.** When the caller
  authored the part (the b123d scenario) they may *also* reference a raw
  `Face`/`Edge`/tag (§4's "edit the solid" made concrete for annotation);
  STEP-in never depends on it, so it stays first-class.

**Consequence — recognition breadth becomes dual-purpose.** The edit surface can
only name what the recognizers detect, so `recognition/` breadth *equals*
editable-vocabulary breadth for **both** scenarios. Feature recognition (ADR 0007)
is thereby reframed as serving both auto-dimensioning quality *and* the semantic
edit surface. This does not reopen §3's primitive-expansion rejection; it
specifies *what* the domain-semantic layer (§2) is anchored to.

## Related

- `docs/plans/right-first-time-roadmap.md` — the deterministic-core + Cluster B
  work that this ADR motivates and reframes.
- Issues #26 (`features()`), #25 (`place_dim()`), #27 (`annotations()`),
  #29 (lint suggestions), #30 (lint→repair loop) — the domain-semantic /
  self-correction layer.
- **Amendment 1 surface:** #388 (`--script`/`finalize`), #397 (`dwg.model()` read
  surface), #398 (edit-by-feature write surface), #396 (`place_dim` limits);
  ADR 0008 (the `PartModel` IR both inputs converge on).
