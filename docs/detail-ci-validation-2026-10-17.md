Validation of the post-review CI fixes on `cx/detail-views`, following `20d81bf0`.

Fetched origin and ran `git merge --no-edit origin/main`: already up to date. Fetched main was
`eab7fad6`. No push was performed.

The drawing choice for 10046 is its recovered principal-only drawing, with zero ink findings.
A bare depth pin previously restored an optional seed detail into the isometric's occupied area,
producing `annotation_over_view` and `annotation_line_over_view`. Pin planning now preserves
recovery's automatic detail selection and its existing refusal diagnostics. Explicit authored
detail requests remain effective. The regression pins each of 10046's five dimensions and
compares measurements, annotation ink and view geometry against the canonical drawing.

The string_post own-span gate now protects the retained 1 mm terminal-band length,
`d:f:turned_step:f9/step.length`. The inspection-end baseline replaced the old 1.1 mm chain
link; this short span still exercises locality beside its own arrow tips. The check explains
the replacement and retains the same detachment threshold.

The thread/knurl helper now names scratch directories with the process ID, test name and
per-process counter. Both the scratch file and directory are removed after analysis.

All commands ran serially with `CARGO_BUILD_JOBS=4`, `RUST_TEST_THREADS=4`,
`DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus`, and
`DRAFTWRIGHT_CORPUS_REQUIRED=1`. No latency gate was disabled. No existing test assertion,
threshold, acceptance baseline or fixture baseline was weakened.

| Check | Outcome |
| --- | --- |
| Initial fmt | PASS |
| Initial clippy | PASS |
| Final fmt (`cargo fmt --all --check`) | PASS |
| Final clippy (`cargo clippy --workspace --all-targets --release -- -D warnings`) | PASS |
| Release binary build | PASS |
| Focused 10046 bare-pin regression | PASS |
| MCP diagnostic regression, unchanged strict assertions | PASS |
| PMI thread/knurl nextest (19 passed, zero skipped) | PASS |
| MCP acceptance (7/7 tasks; Python quality and acceptance tests, Node client tests) | PASS |
| Plan-time gate | PASS |
| Render-tail gate | PASS |
| Repeated-spans 1/4 | PASS |
| Repeated-spans 2/4 | PASS |
| Repeated-spans 3/4 | PASS |
| Repeated-spans 4/4 | PASS |
| Leader routes (119 callouts, 30 parts; 14 unchanged pre-existing runs) | PASS |
| Schema regeneration (no diff) | PASS |
| Schema validation (13 tools, 26 published/served schemas) | PASS |
| Four drawing comparisons | PASS |
| Final full workspace release suite | PASS; 1253 passed, 0 failed, 0 ignored across 121 targets |

The initial full suite had 1,252 passed, one failed and zero ignored. Its only failure was
`mcp_efficiency::one_feature_problem_query_returns_all_member_fixes`: withholding a rejected
crop had also removed its canonical `detail_unplaceable` warning, so releasing a pin made that
existing refusal appear newly added. The follow-up preserves that warning. The focused test
and full suite were then rerun; their final outcomes are above. The initial session latency
gates passed; the final suite and acceptance timing gates use their original bounds.

The full-suite command was `cargo test --workspace --release --no-fail-fast`. The focused
cross-process check was `cargo nextest run --release --test core pmi_thread_knurl:: --test-threads 4`.

Every quality-pins cell ran exactly as CI, with a fresh baseline:

```sh
python3 -I tools/ci_acceptance.py --job quality-pins --pin-cell CELL --out target/acceptance
```

| Cell | Overall | Baseline | Leader routes | Label locality | Deliberate regression rejected | Pinned ink total | Off-sheet | 10046 ink before/after |
| --- | --- | --- | --- | --- | --- | ---: | ---: | --- |
| 1-1 | GO (30/30) | PASS | PASS | PASS | PASS | 17 | 0 | 0 / 0 |
| 1-3 | GO (30/30) | PASS | PASS | PASS | PASS | 17 | 0 | 0 / 0 |
| 1-8 | GO (30/30) | PASS | PASS | PASS | PASS | 15 | 0 | 0 / 0 |
| 2-1 | GO (30/30) | PASS | PASS | PASS | PASS | 15 | 0 | 0 / 0 |
| 2-3 | GO (30/30) | PASS | PASS | PASS | PASS | 15 | 0 | 0 / 0 |
| 2-8 | GO (30/30) | PASS | PASS | PASS | PASS | 14 | 0 | 0 / 0 |
| 3-1 | GO (30/30) | PASS | PASS | PASS | PASS | 18 | 0 | 0 / 0 |
| 3-3 | GO (30/30) | PASS | PASS | PASS | PASS | 18 | 0 | 0 / 0 |
| 3-8 | GO (30/30) | PASS | PASS | PASS | PASS | 10 | 0 | 0 / 0 |

All 270 pinned part runs are GO. Every fresh baseline has 17 ink findings and zero off-sheet
items. The per-part pin bar is unchanged. The separate leader-route invocation was
`python3 -I tools/check_leader_routes.py target/ci-fixes/quality-pins-1-1/quality-pins/quality --report target/ci-fixes/leader-routes.json`.

The other CI acceptance commands used the same output path, one at a time:

```sh
python3 -I tools/ci_acceptance.py --job mcp --out target/acceptance
python3 -I tools/ci_acceptance.py --job plan-time --out target/acceptance
python3 -I tools/ci_acceptance.py --job render-tail --out target/acceptance
python3 -I tools/ci_acceptance.py --job repeated-spans --survey-shard K/4 --out target/acceptance
```

All four survey slices passed (17, 16, 16 and 16 fixtures; 65 total), each with zero repeated
spans. Schema commands were `python3 tools/generate_mcp_schemas.py` and
`uv run --with jsonschema==4.25.1 python tools/check_mcp_schemas.py target/release/draftwright`.

The four default drawing plans and 150 dpi PNGs were compared against the saved pre-fix
binary. Both plans and PNG hashes are identical:

| Drawing | Sheet | Scale | Plan equal | PNG equal | PNG SHA-256 |
| --- | --- | --- | --- | --- | --- |
| string_post | A4 | 5:1 | yes | yes | `2f612ddf508c5d7b0d80631061c038ba8a8fa65469ceecf7000c7b4b6374af21` |
| GRM-03 | A3 | 10:1 | yes | yes | `008f9814ba7295caaf881da42fd222f789e3ba20f4a9f8637825573c9d204e55` |
| sm-hanger | A3 | 1:2 | yes | yes | `ff54bdd40bd5eb3e0b9b7ec9646e4046c2ad5127f4e9a6a26129e2fc9e4f9320` |
| connector | A3 | 1:5 | yes | yes | `f5433dce48b048085466f23187ef88427f3e73ce22214117e95aac81a5038982` |

Local evidence is retained under `target/ci-fixes/`: `checks.json`, `final-checks.json`, both
full-suite logs, per-job logs, all acceptance summaries and artifacts, and drawing comparisons.
Each CI invocation used `target/acceptance`; its complete output directory was moved to
`target/ci-fixes/<job-or-cell>/` after completion so the next invocation could use a fresh output.
