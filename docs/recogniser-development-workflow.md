# Recogniser development and release workflow

Draftwright and `b123d-recognisers` land independently. Geometry algorithms and immutable records
live in the package; Draftwright owns the IR adapter, declarations, generated code, drawing policy,
and completeness. Neither repository needs a mutable production dependency to test a candidate.

## Local candidate check

Keep two committed checkouts beside each other and run this one command from Draftwright:

```bash
scripts/check-recogniser-candidate --package ../b123d-recognisers
```

The command delegates to the package-owned `tools/check_downstream.py` harness. That harness tests
package contract/golden evidence, builds a wheel, exports the committed Draftwright checkout to a
temporary directory, syncs Draftwright's released lock, replaces only the installed wheel, and runs
the focused consumer contract/import tests. Neither checkout is edited. Use `--plan` to inspect the
bounded command plan. Never commit a path or Git dependency; those sources are neither immutable nor
representative of a release installation.

The package's hosted downstream canary runs the same wheel harness against the resolved commit at
Draftwright `main`. Its job summary records that SHA, so a failure is reproducible even if `main`
moves later. This is deliberately a narrow contract canary, not another full Draftwright matrix.
The package matrix proves geometry/platform behavior; Draftwright's own PR and release gates prove
consumer/platform behavior. For an approved schema transition, the harness passes the wheel's exact
version as `candidate_version=` to the consumer validator via the focused test seam. That explicit
argument may match only the single reviewed transition release; normal validation never reads an
environment override and remains on the exact production pin.

## Compatibility and landing order

### Exact production window

Draftwright supports exactly the stable version named by its `b123d-recognisers==X.Y.Z` dependency,
consumer declaration, release record, and `uv.lock`. `.github/recogniser-release.json` records the
PyPI wheel/sdist SHA-256 values and canonical capability-manifest digest. The executable
`scripts/check-recogniser-release` gate rejects version drift, mutable origins, missing hashes,
manifest drift, or a non-registry installation. An open version interval is not implied.

### Additive package change

1. Freeze independent package fixtures, negative cases, goldens, schema, and performance evidence.
2. If an existing record's schema increments, first land a Draftwright declaration that accepts
   exactly the installed and candidate schemas while retaining the previous exact dependency pin.
   The package canary must then pass against that committed compatibility point. An additive family
   or behavior change whose existing schemas remain valid needs no preparatory consumer PR.
3. Land the package change while released Draftwright remains on its exact previous pin.
4. Publish an `X.Y.ZrcN` only when consumer validation genuinely needs a prerelease; otherwise
   publish the audited stable patch directly.
5. Add Draftwright policy/support against the candidate wheel, then publish the stable package
   version and dispatch **Bump recogniser dependency** with that version. Narrow any temporary
   dual-schema declaration to the released schema in that PR. The dependency updater performs and
   tests this narrowing when its target matches the reviewed transition release; it also disables
   the candidate-version seam so the compatibility window cannot remain open after cutover.
6. Merge the generated Draftwright PR only after its exact hashes, manifest, focused join tests,
   canonical coverage run, Codecov checks, and `ci-ok` are green.

### Breaking package change

A record removal, identifier/schema break, or manifest-format break requires a major package
release. Draftwright support for the replacement lands before removal, using a prerelease where an
immutable candidate is needed. The old stable package and current Draftwright remain green until
the replacement consumer is released. A breaking change is never hidden in cleanup or a patch.

### Deprecation window

On the current `0.2.x` line, a compatibility alias remains for the rest of that line and is removed
no earlier than `1.0.0`; its warning names the replacement and removal version. A shorter window
requires a separately approved compatibility decision and evidence from every supported consumer.

### Release cadence

Use patch releases on the current minor line for compatible, audited changes. Do not cut empty
releases merely to exercise automation, and do not bump the minor line while the current contract
can evolve compatibly. Batch no unrelated behavior into a release: each artifact must map to
reviewed package evidence and release notes.

## Automation, rollback, and triage

The **Bump recogniser dependency** workflow accepts a stable PyPI version. Its generator updates the
exact dependency, lock, consumer manifest version, changelog, artifact hashes, and capability digest
as one bounded diff; installs from the registry; runs focused compatibility tests; opens a PR; and
dispatches `ci.yml` with `maintenance_bump=true`. That mode runs static checks plus one canonical
regular coverage suite. It skips the four duplicate compatibility copies and never runs the slow
tier. Codecov project/patch and `ci-ok` still protect `main`.

Draftwright's release workflow likewise opens a post-release `.dev0` bump PR and dispatches the
same narrow gate. It never pushes directly to protected `main`. This repairs the failure observed
after v0.4.6, where publication succeeded but a direct push was correctly rejected by branch
protection.

### Rollback

- Revert a Draftwright bump PR to restore the previous exact PyPI version and hashes.
- Correct package behavior by publishing a new patch; PyPI files and Git tags are immutable and are
  never replaced.
- Withdraw a bad candidate PR/release from the landing sequence, not by switching production to a
  branch URL. PyPI does not support deleting a mistake into safety; yanking may discourage new
  resolution but does not replace a consumer rollback.

### Failure ownership

- Package geometry, golden, public-record, manifest, or performance failure: package owner.
- Draftwright IR, DSL, code generation, drawing, completeness, or release-lock failure: Draftwright
  owner.
- Package CI passes but the canary fails: keep the package PR/release candidate unpromoted, attach
  the resolved Draftwright SHA and failing focused test, and triage at the join. Geometry truth stays
  package-owned and downstream meaning stays Draftwright-owned; neither side copies the other's
  implementation to make the check pass.
- A transient infrastructure failure may be rerun only after its logs show no semantic/test failure.

## Measured proof and CI budget

The no-recognition-behavior 0.2.0 consumer update in
[pzfreo/draftwright#1175](https://github.com/pzfreo/draftwright/pull/1175) proved the release join:
Draftwright installed the public PyPI wheel with exact wheel/sdist hashes, independently validated
all 22 manifest families, and round-tripped the fully consumed boss family while retaining an
intentional geometry-only family. Its five Linux regular-suite jobs ran from 20:37:49 to 20:58:18,
a 20m29s wall-time gate with four duplicate full-suite executions.

Maintenance dispatch keeps the canonical coverage execution required by branch protection but
eliminates those four copies; the package canary runs only the focused cross-repository contract.
Record the hosted run URL, resolved Draftwright SHA, wall time, package version, artifact hashes, and
manifest digest in every generated dependency PR. Expand to a full platform matrix only when the
package changes kernel/platform resolution or the focused join exposes platform-specific behavior.

The package-owned tests-first protocol remains the authoritative per-recogniser checklist:
<https://github.com/pzfreo/b123d-recognisers/blob/main/docs/delivery-protocol.md>.
