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

- **family inventory mismatch** — the installed package added, removed, or renamed a family.
  Update the package pin deliberately, add or remove the Draftwright declaration, and provide the
  downstream behavior or an explicit non-supported state.
- **record schema mismatch** — a consumed record schema changed. Review the record fields and
  compatibility notes, update the relevant adapter and tests, then pin the accepted schema version.
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

Land the package record, independent geometry evidence, manifest update, and compatible package
release first. Then update Draftwright against that immutable release. For each applicable stage,
add the adapter, declaration, emitted-Sheet round trip, drawing behavior, and completeness evidence;
for an inapplicable or deferred stage, state why and link its tracking issue. The contract test
derives package record outputs, converter registries, live `Sheet` methods, and emitter branches
independently, so copying a new name into the declaration alone cannot make CI pass.

`bosses` is the fully consumed reference family. `repeating-radial-profiles` is the opposite
reference: it remains geometry-only critique evidence for a separately authored gear declaration,
with no inferred gear feature added to fill the table.
