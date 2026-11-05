# Steerability reproducers

These fixtures exercise draftwright-rust issues #293–#331, excluding #306 (a PR).
The issue snapshots in `repro/issues/` are the authority; the test-case column of
`repro/steerability-backlog.md` supplies the grouping and intended outcomes.
The suite changes no production policy or existing expectations.

## Provenance

The six `*.step.gz` parts are losslessly gzipped exports copied from
[pzfreo/dw-examples](https://github.com/pzfreo/dw-examples), branch
`ai-driven-examples`, `steps/text-to-cad/`. They were exported unchanged with
build123d 0.13.0 from Thompson Labs LLC's MIT-licensed text-to-cad models at
[`85d8fdbfb467ebd7e22994fe8bcd86afa6626ed3`](https://github.com/amywork777/text-to-cad-upstream/tree/85d8fdbfb467ebd7e22994fe8bcd86afa6626ed3),
2026-10-29, as documented in the source directory's README. `LICENSE` reproduces
that MIT license. No issue needs the 5.2 MB motorcycle shock absorber, so it is
excluded. CTC-01 reuses `../pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz`, already
committed with its provenance in `../pmi/README.md`; it is not duplicated.

The named `*.spec.json` files are the corresponding dw-examples `*-ai.spec.json`
Specs. `inline-293.spec.json` and `inline-301.spec.json` are the complete inline
Specs from those issue snapshots (also repeated in the related issues). Tests
apply the issue's requested change to these immutable inputs. CTC-01 now has
native recognition of its two rectangular notches; `input()` explicitly omits
the five coincident generated owners (two depths, two widths and the left
notch's x location) so K6 accepts the original authored statements. This is a
fixture compatibility edit, not an engine or lint-expectation change. No input is read
from `repro/`, the network, or an external corpus at test runtime. The missing
`sections`, `break`, and top-level `part` fields deliberately remain in the
requests exercised by their cases; deserialization is part of the public API.

## Contract

Run `cargo test --release -j 8 --test steerability -- --test-threads 8`.
Each ordinary Rust test is named `steer_<issue>_<short>`. `expected.json` lists
only currently failing cases, with a one-line reason and the exact set of
failing target assertions. All cases run. An unexpected pass or a changed set
of failing assertions fails the harness and requires review of the table.
An analysis/render panic, missing fixture, or unrelated refusal always fails;
there is no catch-all panic-to-xfail conversion. After fixing a case, remove its
entry (or remove resolved subchecks when only part of an issue is fixed).

Each part is analysed once per process; plans and rendered findings are cached
by the complete input Spec, and render projections are shared per part. CI's
existing whole-binary harness can preserve these caches (nextest otherwise
starts a separate process per test). The normal workspace nextest run also
includes the target. No extra CI job is necessary.

Assertions inspect public plans, rendered Drawing text/font support and lint
findings, never SVG bytes or output hashes. #307 removes each controlling
statement from the actual rendered marks before running independent lint, so
auto-planning cannot silently put it back. False/unmeasurable requests in #308
remain hard assertions, outside the expected-failure mechanism.

Proposal issues #308/#309 are represented by their concrete refusal examples
and by plan decision bases naming their override. Written policy/inventory
work is intentionally left to their separately approved implementation stream.
#298 exercises the built CLI's help/version, service discovery, plan offset
metadata, and the actual release packaging recipe. The package step is run in
a temporary directory with both built binaries and the shipped source assets;
`tar` member names must include MCP, the schema, and the model docs. No upload
step is executed. The portable tar branch checks the common staging payload.
#299 covers items 2–4 from the backlog's test-case column; changing stable ID
vocabulary (item 1) needs its own geometry-edit identity contract.

## CTC-01 differences from the issue snapshots

The engine source is main at `040ca8bb` (PR #332 included). The five K6
omissions above only permit the authored notch statements; they do **not**
restore the v0.2.9 layout. Main additionally generates the other notch's 507.5
x location, its 350 y location, an envelope notch angle of 60°, and an envelope
notch depth of 50. These retained statements change the dimension stacks and
page fit. Each CTC case is checked against this current layout:

| Case | Current reason/outcome and difference from the issue |
| --- | --- |
| #326 | Expected failure. The issue's 150/120 now sit below the plan view, opposite the 635/400. All horizontal **and** vertical linear dimensions, authored and generated, are compared per side. The 400, 207.5 and 85 outside 507.5 and the 100 outside 207.5 reproduce four horizontal inversions. There are also four vertical inversions around the 350 and 225. Total extension crossings increase from 18 to 24. |
| #327 | Expected failure. The schema-only drawing now recognises and dimensions the rectangular notches instead of reporting them as unrecognised. Neither unstated inner ear face at x = ±192.5 is identified by a location/definition finding. |
| #328 | Passes on main, unlike the issue's four y-location findings. Both slots' y locations are credited with the retained generated notch dimensions present. The authored variant additionally requires the actual 205 and 200 dimensions to be placed. |
| #331 | Two independent target checks. The unmodified reproducer really overflows: the generated 650 is 3.1 mm above the drawable area with the 510 placed, 0.1 mm without that placement. This is an overflow expected failure, not evidence of a false warning. A fitting variant moves **only** that 650 below the plan view (`place: {side: below, lane: 0}`), retaining all dimensions and A3 1:5. Both paired renders fit and retain the 650/510; the false warning occurs only with the 510 placement. That is the separate false-warning expected failure. |

The #301 negative control removes the authored start while keeping the generated
pocket location omitted; x must still be reported missing. #310 also checks an
unannotated CTC rectangular grid with no concentric round outline: both centre
locations must still produce `hole_pattern_requirement_missing`. These hard
controls prevent blanket suppression of the completeness codes.

#299/#308 accept either an actually drawn detail, a named detail layout finding,
or a refusal that identifies the missing source or insufficient space. The
existing undifferentiated "source view and room" refusal remains an expected
failure. #299 additionally exercises a real specific source refusal and an
accepted A2 crop as positive controls. #304 checks the existing public schema
validator's `input/views/sections` path alongside decoding the proposed request;
a generic serde type error anywhere in the Spec is insufficient.
