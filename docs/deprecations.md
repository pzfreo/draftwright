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
| `Sheet.dimension(kind=…, value=…)` call shape | `Sheet.measured_dimension(...)` | never released (#963) | **0.4.0** (#720) — breaking, **see below** |
| bare dimension-role spellings — `dimension(f, "width")` | the parameter id — `"width.length"` | never released (#963) | **0.4.0** (#720) — breaking, **see below** |
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

### ⚠ The two #963 deprecations break without a warning release — deliberately

Both were added **after v0.3.9** (`4030913`), so they have never appeared in a released
version — and ADR 0016 dates them to expire at 0.4.0. As written, 0.4.0 is both the first
release in which the warning exists and the release that removes the surface: nobody
upgrading from v0.3.9 ever sees the `DeprecationWarning` before the break.

That matters because bare roles are the **pre-existing** spelling. `dimension(f, "width")`
is what scripts have been written with since the verb existed; `"width.length"` is the new
one. So the effect is a hard break on longstanding usage with no migration release.

The mechanical scale is small — one call site in the whole test corpus, measured — so this was
a policy question, not a work question.

**Decided: they go at 0.4.0** (maintainer, 2026-08-01), as ADR 0016 already specified. The
warning period is skipped knowingly rather than by oversight, so it is **a documented break**:
this section, the 0.4.0 CHANGELOG entry, and the removal itself have to spell out what changed
and what to write instead, because the runtime will not get the chance to. If you are upgrading
from v0.3.9 or earlier, this is the page that tells you — nothing in your own run will.

Migration: replace the bare family role with the parameter id (`"width"` → `"width.length"`;
`dimension_ids()` on a `Sheet` handle lists the valid ones), and replace
`sheet.dimension(kind=…, value=…)` with `sheet.measured_dimension(…)`.

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

Absence is asserted by `test_the_expired_compat_aliases_stay_deleted` and
`test_the_deleted_modules_and_stubs_stay_deleted` — a deletion nobody asserts is a deletion
that comes back.
