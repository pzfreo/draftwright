# Stateless HTTP model evaluation

Run from the draftwright-rust repository root with Node 22+ and the release binary.
The committed `tasks.json` and `check.mjs` retain S0's seven task conditions. A real
model receives the task prompt and the served tool descriptions/schemas, then uses
these commands; it does not need the source. The scripted agent knows the answers
and is a separate instrument, not evidence of real-model performance.

Start the server in a separate terminal, with a fresh persistent evaluation cache
(separate from native-recognition caches because these tasks use S0 feature files):

```sh
export DRAFTWRIGHT_CACHE="$PWD/.cache/mcp-eval/http"
target/release/draftwright mcp-http 127.0.0.1:8765
```

List tools and select a task:

```sh
node tools/mcp-eval/call.mjs tools --out /tmp/tools.json
node tools/mcp-eval/call.mjs tasks plate-callouts-right-pinned
export MCP_EVAL_TASK=plate-callouts-right-pinned
export MCP_EVAL_LOG="$PWD/.cache/mcp-eval/model-calls.jsonl"
node tools/mcp-eval/call.mjs inspect_part '{"step":"tests/fixtures/parts/mounting_plate.step","features":"tools/mcp-eval/features/plate.features.json","drawing_ref":"plate-callouts-right-pinned"}'
```

`inspect_part` returns `part_ref` and the initial `spec_version`. Register the other
parts using `$DRAFTWRIGHT_CORPUS/mfcadpp/10127.step`,
`$DRAFTWRIGHT_CORPUS/gramel/GRM-03_thumbwheel_drive_screw.step` and
`$DRAFTWRIGHT_CORPUS/nist/nist_ctc_01_asme1_rd.stp`, with the matching files in
`tools/mcp-eval/features/`. Shell variables inside single-quoted JSON are literal;
put expanded paths into an argument file instead.

Make one tool call per command. Arguments may be JSON or `@file.json`. Substitute
returned handles in these examples:

```sh
node tools/mcp-eval/call.mjs list_items '{"part_ref":"PART","spec_version":"VERSION"}'
node tools/mcp-eval/call.mjs get_item '{"part_ref":"PART","spec_version":"VERSION","id":"d:envelope/width.length","detail":true}'
node tools/mcp-eval/call.mjs item_options '{"part_ref":"PART","spec_version":"VERSION","id":"d:envelope/width.length"}'
node tools/mcp-eval/call.mjs preview_ops @/tmp/preview.json
node tools/mcp-eval/call.mjs apply_ops @/tmp/apply.json --out /tmp/applied.json
```

An operation is `{"op":"set","path":"dimensions/d:envelope~1width.length/place",
"value":{"view":"plan","side":"above","lane":0,"pinned":true}}`.
Use `~1` for a slash and `~0` for a tilde within a Spec key. Preview arguments are
`{part_ref,spec_version,ops}`; apply arguments are
`{part_ref,drawing_ref,parent,ops}`. Use the current version as `parent` and keep the
new returned version. An absent query version uses the default Spec. `get_spec`
without a version reads the named drawing head. Follow `next_cursor` for lists.
To page a committed delta, read its artifact through MCP `resources/read`, rather
than repeating `apply_ops`. `view_sheet` returns PNG image content plus an artifact
handle. Save the image for a vision model's image reader while printing the text result:

```sh
node tools/mcp-eval/call.mjs view_sheet '{"part_ref":"PART","spec_version":"VERSION","overlay_ids":true}' --image-out /tmp/sheet.png
```

Score the finished task through the same HTTP adapter:

```sh
tools/mcp-eval/check plate-callouts-right-pinned --part-ref PART --spec-version FINAL_VERSION
```

The online checker verifies the handle names the task's STEP file, fetches the final
Spec/Plan and the default baseline, then evaluates
all unchanged conditions. Judge calls are excluded from the model log, as in S0.
Exit codes: checker 0 success, 1 failed conditions, 2 invalid invocation/transport;
call client 0 success, 2 tool refusal, 1 invocation/transport error. An offline
check requires all three documents:

```sh
tools/mcp-eval/check TASK --spec /tmp/spec.json --plan /tmp/plan.json --baseline /tmp/default-plan.json
```

`--url` or `MCP_EVAL_URL` selects an endpoint (default
`http://127.0.0.1:8765/mcp`). Each client call appends one JSONL record, including
refusals and transport errors, tagged with `--task` or `MCP_EVAL_TASK`. `wire`
counts exact UTF-8 JSON-RPC body bytes sent/received (excluding HTTP headers);
`content` counts tool arguments and text result, matching S0's accounting.
`tokens` estimates each content count as `ceil(bytes/4)`, not tokenizer usage.
`tools/list` records fixed context separately (`method` differs from `tools/call`).
Keep actual provider token usage and model/version with the real-model report.

Run the independent scripted evaluation and instrument tests:

```sh
node tools/mcp-eval/run-scripted.mjs --out target/d1-mcp-eval.json
node --test tools/mcp-eval/test-client.mjs
python3 -I tools/check_engine_cache.py
python3 -I tools/test_quality.py
```

Random-pin go/no-go measurement (all 30 parts, seed 1, N=3):

```sh
C=/Users/paul/repos/quiddity/tests/corpus
sed "s#^#$C/#" tools/quality_parts.txt | tr '\n' '\0' | xargs -0 python3 -I tools/quality.py --seed 1 --pins 3 target/release/draftwright target/pins-1-3
```

Run the complete grid using a baseline from the same binary:

```sh
for seed in 1 2 3; do
  for n in 1 3 8; do
    sed "s#^#$C/#" tools/quality_parts.txt | tr '\n' '\0' | xargs -0 python3 -I tools/quality.py --seed "$seed" --pins "$n" --baseline target/quality/baseline.json target/release/draftwright "target/pins-$seed-$n"
  done
done
```

Generate `target/quality/baseline.json` with the ordinary quality invocation first.
`random-pins.json` reports selected IDs, before
and after ink/off-sheet counts, and the bar per part and overall. The generated
`pinned/*.spec.json` and SVGs preserve the experiment. To reuse a baseline from
the same binary and exact part set, pass `--baseline PATH/baseline.json`.
Measurements exit 0 even for a measured no-go; add `--require-go` to enforce the
bar (nonzero for any failing part). Missing parts, failed commands and insufficient
pin candidates always fail. For zero unpinned ink, the 1.5× bar requires zero
pinned ink. The aggregate passes only if every part passes.

Sampling chooses random subsets of existing drawable placements. Dimensions and PMI
use bare pins that retain automatic geometry; views use the nearest named relative
placement to a fixed root. It does not measure random manual relocation to placements
from `item_options`. This narrower interpretation is recorded as overturnable in
`docs/next-session.md`; a passing grid would not establish relocation quality.

E2 reduces discovery calls: `inspect_part` includes a bounded `key_items` index and
accepts `problem_filter`, for example `{"feature":"f:slot:f4"}`, to return a paged
`problem_page` during registration. `list_problems` accepts the same feature filter;
default problem rows include corrective ops, so `detail` is only needed for explanations.
`item_options.settable` gives escaped Spec paths, accepted choices/ranges and tolerance
forms. A descriptor's `paths` shares its choices across those paths. Fits belong to
features, tolerance strings to dimensions, and view paths use bare names.

Compare scripted efficiency against a saved pre-change report:

```sh
node tools/mcp-eval/run-scripted.mjs --out target/e2-mcp-after.json
node tools/mcp-eval/check-efficiency.mjs target/e2-mcp-before.json target/e2-mcp-after.json
```

The comparison requires fewer calls and preserves every previously successful task;
it does not replace the runner's stricter all-seven-tasks gate. The existing plate
placement failure still makes the runner exit 1 until E1 fixes layout. The E2 runner
uses summary IDs and offered slot fixes; H7 remains the requested scripted input,
as before E2. Fit options are covered by Rust tests. GRM-03 uses two sequential
applies, preserving its keep-intent coverage. The efficiency comparator requires both.
The plate script requires all three task callouts and fails if an index hint is missing.

To isolate the registration/problem-page saving with the same script and binary, run
`node tools/mcp-eval/run-scripted.mjs --separate-problems --out target/e2-separate.json`
and compare it to the ordinary run with `check-efficiency.mjs`. This ablation uses
the identical fixes and task conditions, but obtains the slot page in a separate call.
No real-model performance is inferred from this scripted instrument.

S1 review fix: extension-through-feature warnings are report-only. For the slot task,
the scripted editor reads the affected dimension with `get_item(detail=true)` and
makes an explicit opposite-strip edit from its extension geometry. This is a scripted
editing decision, not a suggested Plan fix; it costs an additional tool call. The
unchanged task checker must still find no problems near the slot after re-planning.
