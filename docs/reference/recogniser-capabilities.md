# Recogniser capability contract

Draftwright consumes the public capability manifest installed with `b123d-recognisers`; it never
reads a sibling checkout or a package-private file. The matching Draftwright-owned declaration is
implemented in `draftwright.recogniser_contract` and validated in CI.

The package manifest describes geometry evidence. Draftwright separately declares, for every
package family, its IR adapter, fluent `Sheet` surface, generated-Sheet behavior, drawing consumer,
completeness treatment, and documentation. A stage is either `supported`, `deferred`,
`not-applicable`, or `unsupported`, with the evidence required by that state. This separation lets
other CAD consumers apply different policy without making Draftwright policy part of the geometry
library.

## What failures mean

Validation fails closed and names the family and boundary whenever possible:

- **family inventory mismatch (`stale=`)** — Draftwright declares a family the installed package
  no longer ships, so a declared adapter would call a recogniser that is gone. Remove or repoint
  the declaration.

  The opposite direction does **not** fail this validation. A family the package ships and
  Draftwright has not declared cannot reach any Draftwright code path, and blocking on it made the
  package unreleasable until its consumer caught up. It is reported by
  `pending_family_declarations` and fails `tests/test_recogniser_adoption.py` in Draftwright's own
  CI instead — adoption is still required, and is enforced where the decision is made. Provide the
  downstream behavior or an explicit non-supported state as before.
- **record schema mismatch** — a consumed record schema changed. Review the record fields and
  compatibility notes, update the relevant adapter and tests, then explicitly list the accepted
  schema version. A bounded dual-readable list may name the current and next additive schemas
  during a provider-first release; it is never an open-ended range.
- **stale implementation** — a declared adapter, `Sheet` method, emitter, renderer, or completeness
  function no longer resolves. Repair the implementation reference and its independent behavior
  evidence; do not merely rename the string.
- **evidence is missing** — a supported claim points at a deleted test or document. Restore or
  replace the behavior evidence before changing the path.
- **unknown state or unsupported format** — the installed contract uses semantics this Draftwright
  version does not understand. Upgrade the validator before accepting the package version.
- **geometry-only family invents semantics** — geometry used for critique was incorrectly given an
  inferred IR/DSL/code-generation/drawing path. Keep it geometry-only unless a separately reviewed
  Draftwright feature introduces authored semantics and compatibility evidence.
- **reserved family claims supported downstream semantics** — a family-level `deferred` or
  `unsupported` disposition still advertises an implemented downstream stage. Either make the
  family disposition supported with behavior evidence, or defer/disable every semantic stage and
  retain its tracking issue.
- **state transition lacks evidence** — a capability was strengthened or weakened without a named
  Draftwright version, release notes, and compatibility tests. Supply all three as one reviewed
  transition.

## Adding or changing a recogniser

For an additive field that increments an existing record schema, first land a narrow Draftwright
declaration accepting both the installed schema and the named candidate schema. The existing adapter
must remain valid when the new field is ignored, and tests must prove the next schema is accepted
only by that explicit declaration while later schemas still fail. The package canary then proves
the real candidate wheel against that committed consumer point. After the package release, the
Draftwright dependency/behavior PR locks the immutable artifact, adopts the field, and narrows the
declaration to the new schema.

For each applicable stage, add the adapter, declaration, emitted-Sheet round trip, drawing behavior,
and completeness evidence; for an inapplicable or deferred stage, state why and link its tracking
issue. The contract test derives package record outputs, converter registries, live `Sheet` methods,
and emitter branches independently, so copying a new name into the declaration alone cannot make CI
pass.

`bosses` is the fully consumed reference family. `repeating-radial-profiles` is the opposite
reference: it remains geometry-only critique evidence for a separately authored gear declaration,
with no inferred gear feature added to fill the table.
