# Deprecations

Every deprecated surface in draftwright, what replaces it, and when it goes.

ADR 1 (was 0005 §4): *"Each alias carries a tracking issue and a removal target... A facade with no
exit date is a failure mode, not a success."* This page is where those dates live in one
place. `tests/test_deprecation_dates.py` fails if any `@deprecated` message or
`DeprecationWarning` lacks a removal statement, so the rule is executable rather than a
convention — but the test cannot check that a row exists *here*, so add one when you deprecate
something.

## Discouraged — supported, no removal planned

These are **soft deprecated**: they warn, they steer you elsewhere, and they are **not going
away**. They raise `draftwright.SoftDeprecationWarning` (a `UserWarning` subclass), *not*
`DeprecationWarning`, and they deliberately carry **no removal target**.

That is not a violation of ADR 1 (was 0005 §4)'s exit-date rule — it is the reason the two categories
are separate. §4 governs *compat surfaces*: things kept alive only so old code keeps working,
which rot if they never leave. These are different. They work, they are supported, and there
is a better way to do the same thing. Writing a removal date we did not mean would be the exact
failure §4 names, wearing a date.

`tests/test_deprecation_dates.py` scans `DeprecationWarning`s only, so these rows are outside
it by construction.

To silence the category in your own code:

```python
import warnings
from draftwright import SoftDeprecationWarning

warnings.filterwarnings("ignore", category=SoftDeprecationWarning)
```

| Surface | Prefer | Since | Why |
|---|---|---|---|
| `Sheet.auto_dimensions()` | `authored_dimensions()` + `dimension(feature, role)` lines | 0.4.1 (#1043) | authored is what `--script` emits, is editable text, and is the only form where omission can mean suppression (ADR 4 (was 0016)) |
| `Sheet.add_dimension()` | a `dimension(feature, role)` line on an authored set | 0.4.1 (#1043) | it augments the automatic set, which is itself discouraged here |

**Not affected:** `build_drawing(part)`'s automatic dimensioning. Point the CLI at a STEP or a
build123d object and get a fully dimensioned drawing — that is the detected front door and
being automatic is the whole point of it.

## Compatibility import shims

These modules warn on import (#1936) and retain their existing exports through 0.5.x.
Private historical recognition submodules were not retained.

| Surface | Use instead | Compatibility since | Removed in |
|---|---|---|---|
| `draftwright.recognition` public symbols | import the same symbols from `quiddity` | 0.4.6 (`b123d-recognisers#1`) | 0.6.0 |
| `draftwright.score.feature_census` | `quiddity.feature_census` | 0.4.6 (`b123d-recognisers#1`) | 0.6.0 |
| `draftwright.make_drawing` module | import each symbol from its owning module (`builder`, `drawing`, `linting`, `export`, `cli`) | engine split (#138) | 0.6.0 |
| `draftwright.annotate` module | `_auto_annotate`, `build_model`, `build_rotational_feature` from `draftwright.annotations.orchestrator`; `_wrap_rows` from `draftwright._core`; `_step_repeat` from `draftwright.model.compiled` | annotation split (#164) | 0.6.0 |

The `python -m draftwright.make_drawing` CLI entry point remains available during this window;
use the installed `draftwright` command instead.

### Drawing build-state compatibility properties

These properties continue to forward to `BuildState` without a runtime warning. They are
inventoried for removal in 0.6.0 (#1936); this change does not alter their behavior.

| Property | Current owner | Removed in |
|---|---|---|
| `Drawing._analysis` (getter and setter) | `BuildState.analysis` | 0.6.0 |
| `Drawing._part_model` (getter) | `BuildState.part_model` | 0.6.0 |
| `Drawing._view_edge_cache` (getter) | `BuildState.view_edge_cache` | 0.6.0 |
| `Drawing._ann_box_cache` (getter) | `BuildState.ann_box_cache` | 0.6.0 |

## Live deprecations

| Surface | Use instead | Deprecated in | Removed in |
|---|---|---|---|
| `Drawing.place_dim()` | `dimension(feature, param, pin=True)` / `locate(…, pin=True)` | **0.2.12** (0.3.8 added the PEP 702 shim) | gated on #707, target 0.6.0 |
| `Sheet.section()` | `add_section_view("A", through=feature)` or `add_section_view("A", at=y)` | 0.4.10 (#1260) | 0.6.0 |
| `Sheet.detail()` | `add_detail_view("A", around=feature)` | 0.4.10 (#1260) | 0.6.0 |

### Drawing API removals in 0.5.0

The seven raw `Drawing` wrappers, `export_pdf()`, and the two legacy `export()`
call shapes listed under **Removed** below ended at their published 0.5.0 target
(#2113). Use the private engine methods only inside the engine; user code should
use the feature-scoped verbs and explicit `export(out, formats=(...))`. The
`make_drawing()` SVG/DXF tuple and `Sheet.export()` PDF default remain supported.

### ⚠ The two #963 removals broke without a warning release — deliberately

Both deprecations were added **after v0.3.9** (`4030913`) and removed in 0.4.0, so the
`DeprecationWarning` never appeared in a released version — and no longer exists at all.
Upgrading from v0.3.9 or earlier goes straight from working to a raise.

That matters because the bare role is the **pre-existing** spelling: `dimension(f, "width")`
is what scripts were written with, and `"width.length"` is the new one. So this is a hard
break on longstanding usage with no migration release.

**Decided: 0.4.0** (maintainer, 2026-08-01), as ADR 4 (was 0016) already specified. The warning period
is skipped knowingly rather than by oversight, which makes it **a documented break**: this
page, the 0.4.0 CHANGELOG entry, and the raise itself have to carry what the runtime cannot.
Nothing in your own run will tell you.

Migration — replace the bare family role with the parameter id (`"width"` → `"width.length"`;
`dimension_ids()` on a `Sheet` handle lists the valid ones), and replace
`sheet.dimension(kind=…, value=…)` with `sheet.measured_dimension(…)`. Both failures name
their replacement rather than raising about argument counts.

**Note on scale — and on how the estimate was wrong.** Before doing it, this section said
"one call site in the whole test corpus, measured". That was wrong twice over.

The measurement counted `DeprecationWarning`s emitted by a *selection* of test files, not the
corpus — so it reported the one call site in the files it happened to run and missed nine more
in `tests/test_add_dimension.py` and `tests/test_compiled_plan_boundary.py`, which it never
executed. A count is only corpus-wide if it was taken corpus-wide, and "measured" made it
sound like it had been. What found the rest was running the whole suite.

It also counted only *calls*, missing four tests that existed to pin the deprecated behaviour
itself (warn-and-normalise for both verbs, the warning's `stacklevel`, the legacy spelling
reaching the emitter). Those were rewritten to assert the refusal rather than deleted: a
removal nobody asserts is a removal that comes back.

The true scope was ten call sites across three files plus four rewritten tests. Recorded
because the failure mode generalises: a number attached to the word "measured" gets trusted
in place of the thing it was supposed to measure.

### Why `place_dim` has a gate rather than a version

ADR 4 (was 0012) makes it the sanctioned raw page-coordinate escape hatch until the full
auto-plus-user recompose lands (#426 / #661 / #707). Until then it has no replacement for the
cases it exists to serve, so dating it to a release would be a promise the engine cannot keep.
A gate names the blocker instead of inventing a version — still an answer to "when", and still
checkable.

## Not deprecated, and deliberately so

- **`--style`** — survives with exactly one legal value, `sheet`. It is retained **indefinitely**
  so existing invocations keep working, which is a decision rather than an oversight: removing
  it would break every script that passes `--style sheet` to buy nothing. (`--style imperative`
  was a compat stub with a date, and was deleted at it in #720.)

## Removed

| Surface | Removed in | Notes |
|---|---|---|
| `Drawing.add()` / `add_view()` / `clear_annotations()` | 0.5.0 (#2113) | use the placement and feature-scoped verbs; view projection is private |
| `Drawing.set_view_coordinates()` / `drop_view_coordinates()` | 0.5.0 (#2113) | engine plumbing is private |
| `Drawing.attach_part_model()` / `attach_solve_trace()` | 0.5.0 (#2113) | build state is engine-owned |
| `Drawing.export_pdf()` | 0.5.0 (#2113) | use `export(out, formats=("pdf",))["pdf"]` |
| `Drawing.export(svg=, dxf=)` / omitted or `None` `formats` tuple | 0.5.0 (#2113) | pass explicit `formats`; `Drawing.export` returns a dict |
| `Drawing._named` / `_anno_view` / `_pinned` / `_build_issues` | 0.4.0 (#720) | private; use `dwg.registry` |
| `Drawing._pattern_callouts` / `_patterned_holes` / `_dropped_callout_diams` | 0.4.0 (#720) | private; use `dwg.coverage` |
| `draftwright.sheet_dsl` | 0.4.0 (#720) | import from `draftwright.sheet` (renamed #640) |
| `generate_script` | 0.4.0 (#720) | retired #940; use `--script` / `emit_sheet_script` |
| `--style imperative` (bespoke message) | 0.4.0 (#720) | now an ordinary unrecognised value |
| bare dimension-role spellings — `dimension(f, "width")` | 0.4.0 (#720) | **breaking, never warned in a release** — use the id (`"width.length"`); `dimension_ids()` lists them |
| `Sheet.dimension(kind=…, value=…)` call shape | 0.4.0 (#720) | **breaking, never warned in a release** — use `measured_dimension(...)` |

The last two raise with the replacement named, rather than resolving or `TypeError`-ing about
argument counts, because that message is the only notice this break gets — see the section
above on why the warning period is deliberately absent.

Absence is asserted by `test_expired_drawing_wrappers_are_absent_issue_2113`,
`test_the_expired_compat_aliases_stay_deleted`, and
`test_the_deleted_modules_and_stubs_stay_deleted`.
