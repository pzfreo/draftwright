# Command-line reference

Run `draftwright --help` for all commands and `draftwright COMMAND --help` for a
command's full option list. Bare render help is `draftwright part.step --help`.
The [engine guide](engine.md#using-it) covers recognition, revision reports and
export behaviour; the [model reference](model.md) explains every Spec field.

## Commands

| Command | Usual form and result |
| --- | --- |
| `render` | `draftwright render part.step -o out`: write SVG/PDF files and print their paths and problems. |
| `review` | `draftwright review part.step`: print SVG text, problems and lint findings as JSON. |
| `plan` | `draftwright plan part.step`: print choices, their basis and problems; `plan --analysis a.json` needs no STEP file. |
| `analyse` | `draftwright analyse part.step`: print the Analysis, including native recognition. |
| `parts` | `draftwright parts assembly.step`: list persistent part keys and indices. |
| `pack` | `draftwright pack assembly.step -o drawings`: write GA and made-part sheets, BOM and report. |
| `diff` | `draftwright diff old.step new.step --spec drawing.json -o changes`: carry intent to a revision and report changes. |
| `spec` | `draftwright spec out.pdf`: read the Spec embedded in SVG, PDF, PNG or DXF. |
| `edit` | `draftwright edit drawing.json ops.json`: print the Spec with edit operations applied. |
| `completions` | `draftwright completions zsh`: print a shell completion script. |
| Bare render | `draftwright part.step --out out`: render with the common options; default output is beside the STEP input. |

Subcommand names take priority over filenames: use `./render` for a file named
`render`. `serve` provides JSON-lines requests; `draftwright-mcp` provides MCP.

## Common options

Options belong to particular commands; check that command's `--help`.

- **Output:** render takes `-o STEM` / `--out STEM` or `--out-dir DIR`, using the
  input filename as the stem in that directory. Bare render uses `--out STEM` or
  `--out-dir DIR`; pack and diff require `-o DIR`. Render and bare render strip
  an SVG/PDF/PNG/DXF extension from their output stem when using `--out-dir` or
  the bare form. `-o` and `--out-dir` cannot be combined on render.
- **Formats:** render takes `--formats svg,pdf,png,dxf` (default `svg,pdf`);
  pack takes `--formats pdf,csv,json` (all three by default). Bare render writes
  SVG/PDF. `--dpi 150` selects PNG resolution on render and diff (default 150).
- **Paper and scale:** render, review, plan and bare render take `--size NAME`
  (force a sheet), `--max-size NAME` (preferred largest paper), `--units mm` or
  `--units in`, and `--paper iso`, `ansi` or `arch`. Names are `A4` through `A0`,
  `ANSI A` through `ANSI E`, or `ARCH A` through `ARCH E`; quote names with spaces.
  All four forms take `--scale 5`, `--scale 0.5`, `--scale 5:1` or
  `--scale 1:2`; omit it for automatic scale. Ratios must be positive and valid
  for the drawing's standard scale series. Geometry stays in mm. Decimal inches
  default to three places, trailing zeros and no leading zero below one; paper
  defaults to ISO for mm and ANSI for inches unless a paper preference selects it.
- **Pack:** `--size` and `--scale` constrain only the GA. `--max-size` records the
  preference on the GA and every part drawing. Individual part constraints use
  pack `--set` paths below. A preferred maximum is not a forced sheet size.
- **Title block:** render and bare render take `--title`, `--number`, `--material`,
  `--drawn-by`, `--approved-by`, `--date`, `--revision`, `--company` and
  `--tolerance`. Use quoted text, e.g. `--title 'Mounting plate'`.
- **Pictorial views:** render, review, plan (also `plan --analysis`) and bare render
  take `--shaded`, `--below`, and `--colour b8c4cc` (`--color` is an alias).
  Colour accepts six hex digits, case-insensitively, optionally prefixed with `#`
  (quote that form), and requires `--shaded` even if the Spec already has shading.
  It colours iso and, with `--below`, the underside; other views retain their settings.
  `--below` makes `iso_below` visible and overrides its style: lines alone, shaded
  with `--shaded`. `--shaded` changes iso style without changing an authored omission.
- **Spec:** `--spec drawing.json` starts from that file instead of defaults.
  Drawing overrides preserve unrelated fields, including camera directions and
  title fields, and are embedded in SVG/PDF/PNG/DXF; read them with `spec`.
  Pack takes a pack Spec; diff takes the old revision's drawing Spec.
- **Part:** `--part KEY` or `--part INDEX` selects one assembly part on analyse,
  plan, render, review and diff. Get persistent keys from `parts`; quote spaces.
- **Recognition:** analyse, plan, render and review use native recognition by
  default. `--features r.json` or `--no-features` changes the feature input;
  plan, render and review can reuse `--analysis a.json`. See
  [the engine guide](engine.md#using-it) for their constraints.

With no overrides, existing defaults and output are unchanged. For example:

```sh
draftwright render part.step -o part --formats svg,pdf,png --dpi 150
draftwright render part.step -o part --shaded --below --colour b8c4cc
draftwright part.step --spec drawing.json --shaded --below --out part
```

## `--set PATH=VALUE`

Render, review, plan, bare render and pack accept repeatable `--set` arguments.
They edit a Spec before planning, without changing the input Spec file:

```sh
draftwright render part.step -o out --set sheet/size=A3 --set views/iso/omit=true
draftwright pack assembly.step -o drawings --set ga/drawing/sheet/size=A3
```

- Paths are the Spec's `/`-separated edit paths, without a leading or trailing
  slash or empty segments (`/sheet//size/` is refused). Within a
  key, write `/` as `~1` and `~` as `~0`, e.g.
  `dimensions/d:f:hole:f6~1bore.diameter/omit=true`. Setting an object replaces
  that object; use a leaf path to retain its other fields. Whole values only;
  set an array or object in one go. Array-element paths are refused.
- Values parse as JSON when possible (`true`, `3`, `[184,196,204]`, `{"ra":1.6}`);
  otherwise they are strings (`A3`, `1:2`). Use a JSON string to keep numeric text
  as text: `--set 'sheet/title/number="123"'`. JSON `null` clears optional values.
  The table uses POSIX-shell single quotes around each whole assignment.
- Both named flags and sets override `--spec` or the defaults. Repeated sets apply
  in command-line order, so the last value wins. A set overlapping a named flag's
  path, including a parent object, is refused in either order. For example,
  `--size A3 --set sheet/size=A4` is a conflict. On pack, `--max-size` overrides
  `preferred_max_size` in the Spec file for the GA and every part, including
  existing per-part values. It also conflicts with sets targeting any drawing's
  preferred maximum.
- Unknown paths and invalid values are refused with exit 2 and a readable path,
  e.g. `--set sheet/typo=A3` prints `error: sheet/typo: ... unknown field ...`
  on stderr, followed by `  entry: sheet/typo`. A missing `=` is refused too.

### Common settings

Paths and values come from [spec.json](../schemas/spec.json) and
[pack-spec.json](../schemas/pack-spec.json). Run each example independently after
`draftwright plan part.step`; use `pack assembly.step -o drawings` for pack rows.
Replace `pin A` with a key from `draftwright parts assembly.step`.

<!-- tested-set-examples -->
| Path | Values | Effect | Example |
| --- | --- | --- | --- |
| `sheet/size` | Named paper or `null` | Force a landscape sheet. | `--set 'sheet/size=A3'` |
| `sheet/scale` | Standard ratio string or `null` | Force the drawing scale. | `--set 'sheet/scale=1:2'` |
| `sheet/preferred_max_size` | Named paper or `null` | Preferred largest automatic paper. | `--set 'sheet/preferred_max_size=A3'` |
| `units` | `mm`, `in` | Printed length units. | `--set 'units=in'` |
| `sheet/paper` | `iso`, `ansi`, `arch`, `null` | Automatic paper family. | `--set 'sheet/paper=ansi'` |
| `sheet/projection` | `first`, `third`, `null` | First- or third-angle projection. | `--set 'sheet/projection=first'` |
| `sheet/zones` | Boolean or `null` | Grid zones and centring marks; cited section zones stay. | `--set 'sheet/zones=false'` |
| `sheet/frame_gutter` | 0–20 mm or `null` | Clearance inside the always-drawn frame. | `--set 'sheet/frame_gutter=3'` |
| `sheet/title/title` | String | Drawing title. | `--set 'sheet/title/title=Mounting plate'` |
| `sheet/title/number` | String | Drawing number. | `--set 'sheet/title/number="123"'` |
| `sheet/title` | Object of string fields | Replace title fields: material, authorship, approval, date, revision and company. | `--set 'sheet/title={"material":"Aluminium","drawn_by":"PA","approved_by":"QA","date":"2026-11-17","revision":"B","company":"Workshop"}'` |
| `sheet/title/tolerance` | String | State the intended general tolerance. | `--set 'sheet/title/tolerance=ISO 2768-mK'` |
| `sheet/default_finish` | Object: positive `ra` in µm; optional `removal`: `any`, `required`, `prohibited`; or `null` | State the default ISO 1302 finish beside the title block. | `--set 'sheet/default_finish={"ra":1.6}'` |
| `views/iso/style` | `wireframe`, `shaded`, `null` | Isometric line drawing or vector shading. | `--set 'views/iso/style=shaded'` |
| `views/iso/colour` | Three integers 0–255 or `null` | Base sRGB colour when shaded. | `--set 'views/iso/colour=[184,196,204]'` |
| `views/iso/omit` | Boolean | Hide or show the isometric view. | `--set 'views/iso/omit=true'` |
| `views/<view>/omit` | Boolean | Omit a named view, e.g. `front`, `plan` or `side`; retain a principal view. | `--set 'views/side/omit=true'` |
| `views/iso_below/omit` | Boolean | Request the underside pictorial, or hide it with `true`. | `--set 'views/iso_below/omit=false'` |
| `sheet/preferred_max_sheets` | Integer ≥1 or `null` | Preferred sheet count (default 2), not a hard cap. | `--set 'sheet/preferred_max_sheets=1'` |
| `sheets/max` | Integer 1–9 or `null` | Hard maximum sheet count. | `--set 'sheets/max=2'` |
| `sheet/view_gutter` | 0–1189 mm or `null` | Preferred maximum blank gutter between views (default 25). | `--set 'sheet/view_gutter=15'` |
| `sheet/dimension_spacing` | `normal`, `compact`, `null` | Select normal or compact dimension lanes. | `--set 'sheet/dimension_spacing=compact'` |
| `ga/drawing/sheet/size` (pack) | Named paper or `null` | Force only the GA sheet. | `--set 'ga/drawing/sheet/size=A3'` |
| `parts/<key>/drawing/sheet/scale` (pack) | Standard ratio string or `null` | Constrain one part drawing, independently of GA. | `--set 'parts/pin A/drawing/sheet/scale=1:2'` |
<!-- /tested-set-examples -->

There is no `sheet/frame` toggle: Rust always draws a frame. The compatibility
`--frame` flag requests that behaviour; use `sheet/frame_gutter` for clearance.
General tolerance and default finish are accepted Spec-authored requirements,
not cosmetic labels; use the intended manufacturing values. Spec requirements
can override source PMI, with the override reported.

For any drawing path above, pack uses `ga/drawing/<path>` or
`parts/<key>/drawing/<path>`. For example, a part's omission is
`parts/pin A/drawing/views/iso/omit`. Pack has shared `title` defaults but no shared
drawing defaults; `--max-size` records the preference for every drawing.

## Errors and exit codes

Successful command results are JSON on stdout; `completions`, `--help` and
`--version` print text. A one-shot refusal leaves stdout empty and prints
`error: ...` on stderr, optionally followed by `  choose from: ...` and an escaped
`  entry: ...` path. Exit codes: **0** success, **2** invalid input or command line,
**1** a failed operation. Planning/drawing problems can accompany a successful
result: inspect its problems/report. `serve` and MCP keep their JSON error replies.

## Shell completion

`draftwright completions SHELL` generates scripts for bash, zsh, fish, powershell
and elvish. Regenerate the script after upgrading the CLI. These bash/zsh/fish
paths match the [README](../README.md#install):

```sh
mkdir -p ~/.bash_completions ~/.zfunc ~/.config/fish/completions
draftwright completions bash > ~/.bash_completions/draftwright.sh
draftwright completions zsh > ~/.zfunc/_draftwright
draftwright completions fish > ~/.config/fish/completions/draftwright.fish
```

- **Bash:** source `~/.bash_completions/draftwright.sh` from `~/.bashrc`.
  With bash-completion loaded, the automatic discovery location
  `~/.local/share/bash-completion/completions/draftwright` is another option.
- **Zsh:** put `fpath=(~/.zfunc $fpath)` before `autoload -Uz compinit; compinit`
  in `~/.zshrc`, then restart zsh. A directory already on `fpath` also works.
- **Fish:** reads the script from `~/.config/fish/completions/draftwright.fish`.
- **PowerShell:** save `draftwright completions powershell` to
  `draftwright-completions.ps1` beside your `$PROFILE` (create its directory if
  needed), then add
  `. (Join-Path (Split-Path $PROFILE) 'draftwright-completions.ps1')` to that
  profile. See [PowerShell profiles](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_profiles).
- **Elvish (Unix defaults):** save `draftwright completions elvish` to
  `~/.config/elvish/lib/draftwright.elv` (create the directory), then add
  `use draftwright` to `~/.config/elvish/rc.elv`. For XDG/Windows locations, see
  [Elvish configuration](https://elv.sh/ref/command.html).
