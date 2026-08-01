# Deprecations

Every deprecated surface in draftwright, what replaces it, and when it goes.

ADR 0005 §4: *"Each alias carries a tracking issue and a removal target... A facade with no
exit date is a failure mode, not a success."* This page is where those dates live in one
place. `tests/test_deprecation_dates.py` fails if any `@deprecated` message or
`DeprecationWarning` lacks a removal statement, so the rule is executable rather than a
convention — but the test cannot check that a row exists *here*, so add one when you deprecate
something.

## Live deprecations

| Surface | Use instead | Deprecated in | Removed in |
|---|---|---|---|
| `Drawing.add()` | the placement verbs (`callout` / `dimension` / `note` / `add_table`) | 0.3.8 (#817) | 0.5.0 |
| `Drawing.add_view()` | the section verb; the raw projector is private | 0.3.8 (#817) | 0.5.0 |
| `Drawing.clear_annotations()` | the feature-scoped verbs (`drop` / `remove`) | 0.3.8 (#817) | 0.5.0 |
| `Drawing.set_view_coordinates()` | — (engine plumbing, now private) | 0.3.8 (#817) | 0.5.0 |
| `Drawing.drop_view_coordinates()` | — (engine plumbing, now private) | 0.3.8 (#817) | 0.5.0 |
| `Drawing.attach_part_model()` | — (engine plumbing, now private) | 0.3.8 (#817) | 0.5.0 |
| `Drawing.attach_solve_trace()` | — (engine plumbing, now private) | 0.3.8 (#817) | 0.5.0 |
| `Drawing.export_pdf()` | `export(out, formats=("pdf",))["pdf"]` | 0.3.1 | 0.5.0 |
| `Drawing.export(svg=, dxf=)` keywords + tuple return | `export(out, formats=[...])` → `{format: path}` | 0.3.1 | 0.5.0 — **warns nowhere, see below** |
| `Drawing.place_dim()` | `dimension(feature, param, pin=True)` / `locate(…, pin=True)` | **0.2.12** (0.3.8 added the PEP 702 shim) | gated on #707, target 0.6.0 |

### ⚠ `export(svg=, dxf=)` is declared deprecated but emits no warning

It sits under v0.3.1's **"### Deprecated"** heading in the CHANGELOG, and `export()`'s docstring
calls it "legacy (kept for back-compat)" — but the code path at `drawing.py:2753` warns nowhere.
`tests/test_deprecation_dates.py` therefore cannot see it *by construction*: that guard scans
things that warn.

A deprecation nobody is warned about is not a deprecation, it is documentation. Before 0.5.0
removes it, it has to start warning — otherwise the removal is a silent break for every caller
still using the tuple form. Adding that warning is a behaviour change beyond dating, so it is
**not** in this pass; it is the reason the row above is annotated rather than plain.

### ⚠ The two #963 removals broke without a warning release — deliberately

Both deprecations were added **after v0.3.9** (`4030913`) and removed in 0.4.0, so the
`DeprecationWarning` never appeared in a released version — and no longer exists at all.
Upgrading from v0.3.9 or earlier goes straight from working to a raise.

That matters because the bare role is the **pre-existing** spelling: `dimension(f, "width")`
is what scripts were written with, and `"width.length"` is the new one. So this is a hard
break on longstanding usage with no migration release.

**Decided: 0.4.0** (maintainer, 2026-08-01), as ADR 0016 already specified. The warning period
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

ADR 0012 makes it the sanctioned raw page-coordinate escape hatch until the full
auto-plus-user recompose lands (#426 / #661 / #707). Until then it has no replacement for the
cases it exists to serve, so dating it to a release would be a promise the engine cannot keep.
A gate names the blocker instead of inventing a version — still an answer to "when", and still
checkable.

## Not deprecated, and deliberately so

- **`--style`** — survives with exactly one legal value, `sheet`. It is retained **indefinitely**
  so existing invocations keep working, which is a decision rather than an oversight: removing
  it would break every script that passes `--style sheet` to buy nothing. (`--style imperative`
  was a compat stub with a date, and was deleted at it in #720.)
- **`make_drawing.py`** — a permanent re-export facade, not a transitional one.

## Removed

| Surface | Removed in | Notes |
|---|---|---|
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

Absence is asserted by `test_the_expired_compat_aliases_stay_deleted` and
`test_the_deleted_modules_and_stubs_stay_deleted` — a deletion nobody asserts is a deletion
that comes back.
