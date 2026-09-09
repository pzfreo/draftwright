# Frame build profile and progress (#1534)

## Workload and reproduction

This is the original automatic call from #1529, using the print-oriented STEP pinned at
`pzfreo/whistle-key@76ccdc2`. It is not the later datum-aligned, authored two-sheet package.
The script refuses any other input hash and creates a fresh output directory.

```sh
curl -L https://raw.githubusercontent.com/pzfreo/whistle-key/76ccdc2/exports/three-key/print-oriented/frame_print.step -o /tmp/frame_print.step
uv run python docs/research/profile-frame-1534.py /tmp/frame_print.step /tmp/frame-baseline
uv run python docs/research/profile-frame-1534.py /tmp/frame_print.step /tmp/frame-observed --observe
uv run python docs/research/profile-frame-1534.py /tmp/frame_print.step /tmp/frame-diagnostic --observe --permissive-diagnostic
uv run python docs/research/profile-frame-1534.py /tmp/frame_print.step /tmp/frame-cancelled --observe --cancel-at placement.feature_leaders
```

The baseline and observed commands may exit nonzero: refusal is a measured outcome, not a
profiling failure to hide. Read `failure.json`, `environment.json`, cumulative text and pstats.
Observed runs additionally write `progress.jsonl`. Successful builds receive separate lint
and export profiles, requirement diagnostics, decisions and artifact byte counts. A failed
build has no separate downstream lint/export result. For comparison, run the same script in
each revision's isolated environment with the same input and policy; `--observe` requires
the new API. The harness records installed packages, kernel distribution, input SHA-256,
commit and working-diff hash. Timing excludes process startup and imports.

## Measured baseline

On 2026-09-09, commit `07b4af257248c71f26cf499b95ab5798b1965ff8`, macOS 26.6.2,
Apple M5 Max (18 logical CPUs), Python 3.14.7, Draftwright 0.4.26.dev0,
Quiddity 0.2.6, build123d 0.11.1, drafting-helpers 0.15.1 and
cadquery-ocp-novtk 7.9.3.1:

- Input SHA-256: `7d5c95e5367d2d3cbc418d628b5d418a3018b1ed9e6dbdb73337c417496146e2`.
- A2, requested scale 2, third angle, default automatic dimensions/detail and fallback policy;
  title, number, material, tolerance, revision, date and reproducibility match the field report.
- Build: **67.123 s under cProfile**, nine one-pass attempts, sixteen assemblies.
- Outcome: `ScaleIncompatibilityError`, `no_complete_scale`. Attempted scales:
  2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01, 0.005. The last hits the existing render floor.
  Retained blockers include callout, flat, location, off-axis location and pad-height drops.
- No successfully returned Drawing, separate final lint or export was available.

| Function/stage | Calls | Cumulative seconds |
|---|---:|---:|
| Feature leader placement | 96 | 40.004 |
| Measured fixed-point repack | 8 | 31.673 |
| Validated face meshes | 6,974 | 18.558 |
| Fixed annotation ink | 696 | 17.656 |
| Recognition evidence acquisition | 1 | 1.387 |
| Solid `project_to_viewport` | 101 | 1.222 |
| Lint inside the build | 16 | 2.650 |
| Repair inside the build | 8 | 1.301 |

These cumulative costs overlap: do not sum them. In particular repack includes placement,
meshing includes tessellation, and repair includes lint. cProfile adds overhead; this one
local run is neither a portable timeout nor a regression benchmark. It confirms expensive
repeated annotation work, not an infinite loop or an exact-HLR root cause. Cross-attempt
ink reuse needs a proven invalidation boundary before a cache is justified (#1137).

## Instrumented replay and downstream diagnostic

The first replay with progress finished in 65.618 s under cProfile and raised the same
exception/message with the same attempted scales. It reported recognition once, all eight
`scale_completeness` retries, and the existing `greedy_pair_budget` reason. The terminal
build event was `failed`, not `finished`. This is a behavior check, not a speedup claim;
there is no timing threshold in the regression suite and no optimization in this change.

A separate **explicitly permissive diagnostic** kept scale 2 to make downstream work
measurable. Build/lint/export took 11.585/0.191/14.729 s under cProfile. Its 58 audited
requirements were 50 placed, 1 dropped, 1 unverifiable and 6 unsupported. Its 0 errors and
16 warnings do not make it complete. It produced SVG/PDF/DXF of 557,142/184,504/2,354,874
bytes. This is a different policy and outcome, not an accelerated equivalent of the default
call. Export-size investigation remains #1537.

An actual terminal Ctrl-C during `build / repack / assemble / placement.drain` exited 130
with a structured `keyboard_interrupt` diagnostic after 4.242 s. Small regression tests also
request cancellation inside repeated mesh and fixed-obstacle work, check that internal
candidates are not returned, and prove deferred-edit and provisional-repair rollback.

## Architectural fit

Activity is emitted at existing compiler stages and decision producers (ADRs 1–2). It is
not another trace ledger, placement engine or search policy. Recognition runs at its existing
owned acquisition sites (ADR 3); observation neither detects extra features nor invents
intent (ADR 4). Existing caps and capability losses stay visible. Cancellation never presents
an internal, partially built candidate as a finished drawing, and provisional repair is
restored when validation is interrupted (ADR 5). No native hard timeout or speculative cache
is added. The CLI is a subscriber to the headless observer, following #276's direction.
