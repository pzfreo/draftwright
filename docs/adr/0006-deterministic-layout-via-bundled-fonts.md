# ADR 0006 — Deterministic cross-platform layout via bundled, path-pinned fonts

- **Status:** Accepted
- **Date:** 2026-06-27
- **Deciders:** Paul Fremantle (pzfreo)

## Context

draftwright's layout is a function of measured text widths. `_text_width`
(`make_drawing.py`) sizes labels by rasterising real glyph geometry (#31, for
accuracy), those widths size annotation footprints (ADR 0004 compose-then-pack),
and the footprints feed page centering and the iso fit. So **where every view and
annotation lands depends on how wide the text is**.

Text was measured and rendered by font *name* — `Draft.font` defaults to
`"Arial"`, and the helper rendered every label via that name. A font name is
resolved through the OS font stack (fontconfig / CoreText), which **substitutes**
when the name is absent: "Arial" → Liberation/DejaVu Sans on Linux, real Arial on
macOS. Different fonts → different metrics → **the whole sheet drifts across
platforms**. Measured while building the ADR-0005 golden gate: NIST CTC-01 shifted
**+1.028 mm in X on Linux vs macOS** (views and annotations together), and even a
*symmetric* synthetic holed plate shifted **+0.904 mm** — symmetry of geometry
does not balance the asymmetry of the text widths that drive centering.

Two costs: drawings were **not reproducible across platforms** (and were literally
rendered in a different typeface on Linux), and the golden gate (ADR 0005) could
only portably pin trivial primitives — real parts were excluded.

## Decision

Pin all text to **bundled font files** referenced by path, never by system name.

1. **Vendor the fonts.** draftwright ships IBM Plex (OFL-1.1) in
   `src/draftwright/fonts/`: **Plex Mono** for dimensions / callouts / notes
   (monospace — digits and tolerances line up), **Plex Sans Condensed** for the
   title block (fits the tight ISO 7200 cells). OFL bundles freely in both the
   AGPL and a commercial distribution (keep the notices).
2. **Render via `font_path`.** `build123d-drafting-helpers >= 0.13.0` renders
   every label through `font_path` (helpers#172). draftwright sets its Plex font
   on the shared `Draft` (Plex Mono) and on the title-block's own `Draft`
   (Plex Sans Condensed).
3. **Measure via the same file.** `_text_width` is pinned to the same Plex Mono
   `font_path` as the annotations render with, so the layout *estimate* and the
   *render* agree.

Glyph **outlines are vector data in the font file**, not rasterised pixels, so the
same file yields identical geometry on every OS and FreeType version. This keeps
#31's real-glyph-metric accuracy — it just measures a *consistent* font.

osifont (an ISO-3098 face) was considered for a "standards" preset but **dropped**:
it is LGPL/GPL, and keeping the bundle OFL-only avoids copyleft obligations in a
commercial distribution. It can return later as an opt-in, user-supplied font.

## Consequences

**Positive**
- Layout is **deterministic across Linux / macOS / Windows** — drawings are
  reproducible, and the ADR-0005 golden gate now pins a **real part** (CTC-01),
  closing its deferred real-part-coverage limitation.
- Fixes the silent **typeface-substitution** bug (consistent IBM Plex everywhere),
  and removes the proprietary-Arial default.

**Negative / costs**
- **Output changes for every drawing** — positions shift from the
  Arial-substituted baseline. A one-time intended change (ADR 0004/0005
  discipline): all golden snapshots regenerated, and geometry-level tests that
  asserted text-dependent placement updated to the new values.
- Vendored binaries (~250 KB) and their OFL licenses now live in the package.
- A small cross-repo coupling: the rendered-label font lives in the helper, so
  full determinism needs `>= 0.13.0`.

**Neutral / follow-ups**
- Per-role fonts (mono dims, condensed title) mean two `Draft`s; further roles
  would extend the same pattern.
- If an ISO-3098 standards look is wanted, add osifont as an opt-in user font
  (not bundled), per the licensing note above.

## Related

- [ADR 0004](0004-compose-then-pack-view-blocks.md) — the layout this text
  measurement feeds.
- [ADR 0005](0005-pipeline-architecture-and-state-ownership.md) — the golden gate
  whose real-part-coverage deferral this closes.
- draftwright #149 (this change); build123d-drafting-helpers #172 (the
  `font_path` rendering support it depends on).
