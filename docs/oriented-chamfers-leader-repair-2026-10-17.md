# Boss leader repair after the native-recess merge

The quality output at `f704b88b` reproduces both CI failures on 10020:
`d:f:boss:f51/boss.diameter` gains one hidden-edge crossing and one
arrowhead/extension contact. The boss leader leaves upward and left through the
hidden-line bundle, instead of using main's clear route to the right.

The established drawing excluded native recess annotations but already included
the new notch bands. Consequently the displaced boss route could become its own
comparison recipe. The existing seeding and refinement safeguards then lacked
the route from before the notch additions.

Commit `d6323a0` gives that internal comparison scope the pre-notch annotation
inventory. Its geometry remains available; the final solve still reads the
original Analysis and every notch requirement. No leader scoring, checker,
clearance, threshold, quality baseline or route fixture changes. The regression
test checks the actual rendered hidden geometry and the rendered arrowhead
against every witness's existing 0.2 mm clearance.

The better drawing preserves main's boss route while placing the added notch
information around it. Relative to the carrying view, the boss route and label
match the original route fixture to floating-point precision:

| Boss route measure | Regressed | Repaired |
| --- | ---: | ---: |
| Hidden crossings | 1 | 0 |
| Arrowhead/extension contacts | 1 | 0 |
| Route length, mm | 11.702 | 15.384 |
| Run beside ink, mm | 0 | 0 |

The repaired length is main's exact 15.384 mm, within the unchanged +2 mm gate.
The 21.2° angle, 20.0 station and shared `10.6 × 10.6°` bevel/notch-depth owner
remain. The two native pocket statements, their locations and the hex-pocket
depth remain. An audit of all 30 parts retains every dimension measurement
claim/value and every previously placed dimension ID, with unchanged sheets and
scales. CTC-01 retains both 60° angles, the shared `2× 50` depth, all four R25
radius-centre rows and A3 at 1:5; 10020 retains A3 at 2:1.

## Verification and drawing review

All commands use `CARGO_BUILD_JOBS=12`, `RUST_TEST_THREADS=12`,
`DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus` and
`DRAFTWRIGHT_CORPUS_REQUIRED=1`. The source remains unchanged during verification.
Logs, exact commands, exit statuses and timings are under
`target/leader-route-repair/`; `results.json` indexes the required checks.

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | PASS |
| `cargo build --release` | PASS |
| `tools/quality.py`, corpus paths from `tools/quality_parts.txt` | PASS: all 30 parts |
| `tools/check_leader_routes.py`, unchanged fixture | PASS: 144 callouts on 30 parts; eight unchanged pre-existing runs |
| Quality against `tools/ci-quality-baseline.json` | PASS: all 300 cells no worse |
| `tools/check_quality_table.py docs/status-2026-10-10.md` | PASS: all 450 cells no worse |
| `cargo test --release --test rendering oriented_chamfers::` | PASS: 12 tests, including the new rendered-route regression |
| `cargo test --release --lib` | PASS: 250 tests |
| Complete workspace nextest suite, once after targeted checks | PASS: 1,309 tests across 122 binaries, 0 failed, 0 skipped; 968.754 seconds of execution |

The complete suite uses the existing artifact-local nextest configuration from
`target/oriented-main-merge/nextest-local.toml`. It reserves all 12 execution
slots for the native session and multi-sheet wall-clock tests, with all tests
selected and every timing threshold unchanged.

Fresh 150 dpi renders of both `10020.png` and `ctc01.png` in
`target/leader-route-repair/` were visually inspected. On 10020 the boss and bore
leaders leave to the right on distinct routes, the hex statement sits above
them, and the notch angle/depth statements remain legible. CTC-01's angles,
depth, location statements, bore labels and radius table remain legible at its
established scale. Both inspected source SVGs match the final quality output
byte for byte. `inventory-and-render-audit.json` records the retained inventory
and the SVG/PNG hashes; `quality/leader-routes.json` contains the route report.

The full suite includes passing native-session and multi-sheet timing gates.
Quality totals remain 8 ink findings, 65 feature crossings, 7 repeated labels and
28 leader/extension contacts. Text-off-span findings remain 4; the other
CI-baseline defect metrics remain zero.

No other CI acceptance job was run. No push was performed.
