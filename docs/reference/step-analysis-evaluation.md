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

Format 1 currently proves the `holes` vertical slice. The observer reads released
`b123d-recognisers` geometry records, builds the drawing, and reads the `drawing_consumer`
outcome off that build through the ADR 0010 provenance seam — `supported` when the hole's
size reached the sheet as a callout or a hole-table row, `unsupported` when a feature
accounts for it and carries neither, `unknown` when no feature accounts for it (#1202). The
other three downstream boundaries still come from the independently enforced Draftwright
capability declaration, which is a claim that a code path exists rather than an
observation. Adding another family requires its own independently authored fixtures, facts,
identity fields, observer, downstream evidence, and corpus version change; copying a manifest name
alone cannot enlarge the denominator.

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

The initial corpus lives in `tests/fixtures/evaluation/corpus-v1.json`. It contains positive,
negative, ambiguous, compound, and topology-order-variant cases. Each STEP file is hash-pinned and
CC0-licensed, and its construction-derived oracle is documented beside it. The topology pair has
identical geometry and expected facts but bijectively renumbered, reverse-serialized Part 21
entities. CI runs the same evaluator on every supported Python version; repeated evaluation and the
pair must produce identical layer results.

The anti-self-validation mutation replaces the real hole observer with an empty observer. Expected
facts remain five, matches fall to zero, and recall falls from `1.0` to `0.0`. Weakening or deleting
a recogniser therefore cannot shrink the benchmark denominator and preserve a perfect result.

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
