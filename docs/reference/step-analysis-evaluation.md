# STEP analysis evaluation

`draftwright.evaluation.step_analysis` measures STEP-analysis capability against a versioned,
independently authored corpus. It is an engineering evaluation surface, not a drawing-lint score
and not a replacement for per-drawing diagnostics.

## Evidence boundary

The corpus JSON owns the denominator, physical identity fields, expected parameter values,
tolerances, required downstream stages, fixture hashes, licence, and provenance. None is generated
from `RecognitionResult`, `feature_census()`, a capability declaration, or the drawing under test.
An observer may only normalize actual evidence; `ObservedFact` deliberately has no oracle/case fact
identifier. Expected and observed facts are matched by family plus authored physical identity fields.

Format 1 currently proves independent `holes`, `hole-patterns`, `flats`, `pockets`,
`pocket-patterns`, `grooves`, `rectangular-pads`, `polygonal-bosses`, `plates`, `face-levels`,
`chamfers` and `fillets` vertical slices. Each observer reads released `b123d-recognisers`
geometry records, builds one drawing, and reads downstream outcomes from that build through the
public IR, `Sheet`, generated-code and ADR 0010 provenance seams. A FaceLevel that supplies a
global rung requires all four boundaries; a record retained as exact Plate, side-pad, edge-pocket,
ThroughStep or turned-profile substrate requires its automatic-IR and generated-code owners but
deliberately has no second FaceLevel declaration or drawing requirement.

Since #1217 that outcome comes from the engine's own requirement ledger
(`linting.hole_coverage.hole_requirement_outcomes`) rather than from a second correspondence
implementation here, and the ledger is treated as a **pointer, not proof**: `supported` requires
both that the engine recorded an annotation as carrying the hole's size *and* that the annotation
renders the value the compiler approved, checked through `linting.evidence`. `unsupported` means
the requirement was not placed, or the annotation contradicts it. `unknown` means the ledger
declined to join the hole to a feature without guessing — it scores as a miss, so it is an honest
label rather than an exemption (#1202, #1206). Pattern arrangement dimensions follow the same
ledger-pointer rule: placement provenance is insufficient unless the rendered pitch or BCD agrees
with the compiler-approved value. Adding another family requires its own independently authored
fixtures, facts, identity fields, observer, downstream evidence, and corpus version change; copying
a manifest name alone cannot enlarge the denominator.

## Scores and units

The evaluator reports three layers and never manufactures a composite:

1. **Detection** uses a deterministic maximum bipartite match. Recall is matched expected physical
   facts divided by all expected physical facts. The reported false-positive rate is unmatched
   observations divided by all observations; when nothing was observed it is `0.0`. Recall is
   unavailable (`None`) for a genuinely empty negative-case denominator.
2. **Parameter fidelity** checks each authored parameter on a matched fact. A value receives one
   unit only when it is present and within the authored absolute tolerance. There is no tolerance
   interpolation: partial credit exists only across independently listed parameter units. The
   score is passed units divided by all checked units, or `None` when detection produced no matched
   parameter-bearing fact.
3. **Downstream usefulness** checks each independently required IR-adapter, `Sheet` declaration,
   generated-code, and drawing-consumer boundary. `supported` receives one unit; missing,
   `deferred`, `unsupported`, and unknown states receive zero. The score is passed boundaries over
   required boundaries, or `None` when no matched fact has a downstream requirement.

Corpus aggregation is micro-averaged from raw units, not an average of case percentages, so many
small cases cannot outweigh a missed compound case. Per-case diagnostics identify the layer,
family, parameter/boundary, expected value, and observation. Inputs and diagnostics are sorted by
canonical content, so neither expected-fact order, recogniser output order, nor STEP entity order
changes a result.

`unknown` and `unsupported` are explicit analysis outcomes. They never count as complete. A corpus
may independently expect one for an ambiguous or intentionally unsupported case, in which case the
case is *conformant*—the system answered honestly—but still not *complete*. This distinction avoids
rewarding fabricated certainty. A supported negative case with no expected and no observed facts
can be complete within the corpus's stated family scope.

## Corpus and determinism

The initial hole corpus lives in `tests/fixtures/evaluation/corpus-v1.json`; the independent
hole-pattern arrangement corpus lives beside it as `corpus-hole-patterns-v1.json`. Each contains
positive, negative, ambiguous, compound, and topology-order-variant cases. Every STEP file is
hash-pinned and CC0-licensed, and its construction-derived oracle is documented beside it. Each
topology pair has identical geometry and expected facts but bijectively renumbered,
reverse-serialized Part 21 entities. CI runs the same evaluator on every supported Python version;
repeated evaluation and each pair must produce identical layer results.

The anti-self-validation mutation replaces the real hole observer with an empty observer. Expected
facts remain five, matches fall to zero, and recall falls from `1.0` to `0.0`. Weakening or deleting
a recogniser therefore cannot shrink the benchmark denominator and preserve a perfect result.

The pattern corpus owns one arrangement fact for each accepted group. Its identity contains the
canonical member sites, while its scored parameters contain only the group grammar: count and BCD,
linear pitch/direction, or grid rows/columns/pitches/angle/centre. It deliberately does not repeat
member diameter, depth, bottom or individual-location requirements from the hole corpus. Provider
patterns must reference the exact accepted aggregate `HoleRecord` members, and those member sets
must be disjoint, so N:1 grouping cannot become a second physical-hole denominator.

The flat corpus owns one physical across-flats fact per stock axis line and connected axial span.
Opposed provider faces on one Double-D body therefore group into one fact, while parallel lobes and
disjoint coaxial bodies remain independent. Axis, canonical direction, axis line and stock span
form identity; across-flats size, contributing face count and face anchors are scored parameters.
The observer reads the build-owned recognition aggregate once and follows each group through the
automatic IR, public `Sheet.flat` declaration, executed generated Sheet code and placed semantic
measurement provenance.

The groove corpus owns one annular recess per shaft axis line and station, scoring axial width and
floor diameter through one exact `WIDE × ø` statement. The chamfer corpus owns one planar or
conical bevel per axis, physical anchor and surface form, scoring both legs and angle through exact
`C` or `leg × angle` ink. The fillet corpus owns one cylindrical or toroidal round per axis, physical
surface anchor and form, scoring radius through exact `R` or grouped `n× R` ink. All three verify a
live physical leader target and include compound and topology-order controls; the chamfer corpus
additionally pins AngledStep ownership, while the fillet corpus pins CircularBlindStep ownership.

The polygonal-boss corpus owns one attached regular hexagonal prism per principal axis and physical
centre. It scores the provider's six-side schema invariant, across-flats, height and canonical
physical flat-support pairs while keeping
whole polygonal stock, recesses, detached prisms, circular bosses and rectangular pads outside the
family denominator. Both A/F and height must survive through exact compiler identities. The
finished A/F arrow is checked against a retained flat centre only after source-to-IR semantic
correspondence is established, so rendered page geometry validates usefulness but never selects
the feature owner.

The Plate corpus owns one thin material slab per body-local multi-plate prismatic occurrence. A
single flat slab is envelope-owned and contributes no duplicate Plate fact; detached single slabs,
thick block-scale spans and rotational bodies are negative controls. Axis, axial midpoint and both
independently authored transverse witness coordinates form every occurrence identity; thickness is
the scored parameter. Exact provider-to-IR correspondence retains
the full axis, interval and both witness coordinates. The drawing boundary accepts either a
compiler-confirmed, solver-placed `thickness.length` Dimension or the explicit derived opposite
wall of a complete U-channel chain. Exact envelope, step-level/shoulder, slot-pattern and attached
polygonal-boss ownership prevents derived material spans from inflating the Plate denominator and
fails closed when full-witness body-local support cannot be established. Raw boss ownership
additionally requires a valid support ring, a single-solid part, and a complete boss-plus-slab
envelope span. Plural-solid inventories remain unverifiable because Plate carries no body
provenance. Drawing credit for a
derived span requires verified finished claims for every dependency. It never infers coverage from annotation
names, labels, views or page coordinates. The 11 cases own 20 physical facts, 20 parameter units and 80 downstream
units; deleting every provider Plate therefore leaves 20 misses rather than shrinking the
denominator.

```python
from pathlib import Path

from draftwright.evaluation.step_analysis import evaluate_step_corpus, load_corpus

corpus = load_corpus(Path("tests/fixtures/evaluation/corpus-v1.json"))
result = evaluate_step_corpus(corpus)
print(result.detection.recall)
print(result.parameter_fidelity.score)
print(result.downstream_usefulness.score)
```

## Versioning and compatibility

Every result is meaningful only with both versions:

- `metric_version` is an integer protocol version. Change it for matching, units, denominators,
  aggregation, partial-credit, or outcome semantics. Results across metric versions are not
  comparable.
- `corpus_version` follows SemVer. Patch releases may correct prose/provenance without changing
  fixtures, facts, tolerances, scope, or scores. Minor releases may add cases or independently
  evaluated families and establish a new additive baseline. Major releases remove cases or change
  existing geometry, expected facts, identity fields, tolerances, or required downstream states.

A fixture hash change is never silent: it requires a corpus version decision and review of the
construction oracle. CI and reports must retain both versions and per-layer raw counts. Thresholds
must name an exact metric version and corpus compatibility range rather than comparing anonymous
percentages.

## Relationship to drawing quality

This metric asks whether STEP geometry was detected faithfully and can reach Draftwright's owned
consumer boundaries. `Drawing.lint()` asks whether one concrete authored/generated drawing is
semantically and visually acceptable. Its `quality.completeness.audited_score` is conditional on
requirements the current recognition run already found, so it can diagnose downstream omission but
cannot measure physical recall. The independent corpus can measure recall but cannot certify an
arbitrary production part or a rendered sheet. Keep the feature census descriptive, use this
evaluation for regression/coverage claims, and use lint codes plus separate drawing completeness,
restraint, legibility, and fidelity components for a drawing decision (#1176 added
fidelity: whether what the drawing says is true, which the other three do not ask).

## API

::: draftwright.evaluation.step_analysis
