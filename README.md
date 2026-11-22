<p align="center">
  <img src="docs/images/draftwright-logo.png" alt="draftwright: technical drawings, generated" width="460">
</p>

<p align="center">
  <a href="https://crates.io/crates/draftwright"><img src="https://img.shields.io/crates/v/draftwright.svg" alt="crates.io"></a>
  <a href="https://pypi.org/project/draftwright/"><img src="https://img.shields.io/pypi/v/draftwright.svg" alt="PyPI"></a>
  <a href="https://pypi.org/project/draftwright/"><img src="https://img.shields.io/pypi/pyversions/draftwright.svg" alt="Python versions"></a>
  <a href="https://docs.rs/draftwright"><img src="https://img.shields.io/docsrs/draftwright" alt="docs.rs"></a>
  <a href="https://github.com/pzfreo/draftwright/actions/workflows/ci.yml"><img src="https://github.com/pzfreo/draftwright/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-AGPL--3.0-blue.svg" alt="Licence: AGPL-3.0"></a>
</p>

Draftwright turns a STEP model into a dimensioned engineering drawing: SVG, PDF or editable
DXF. It recognises manufacturing features, reads requirements from the STEP file, chooses
views and dimensions, and lays them out on standard sheets. It reports what it could not state.

<p align="center">
  <img src="docs/images/hero-t2c-enclosure-base.png" alt="An A4 drawing of an electronics enclosure base: plan, front and side views, section A–A, an isometric view, the pocket and mounting-hole callouts and a title block" width="100%">
  <br>
  <sub>An electronics enclosure base made by a text-to-CAD tool, drawn by 0.6.0-alpha.2 from its STEP
  file with only the title set. Every view, dimension and callout is placed automatically.</sub>
</p>

**Contents:** [Examples](#examples) · [Install](#install) · [Command line](#command-line) · [Output and the Spec](#output-and-the-spec) · [Python](#python) · [What it does](#what-it-does) · [Assemblies and revisions](#assemblies-and-revisions) · [With agents](#with-agents) · [Documentation](#documentation) · [How it's built](#how-its-built) · [Related projects](#related-projects) · [Licence and contributing](#licence-and-contributing)

The current version is **0.6.1** ([release notes](https://github.com/pzfreo/draftwright/releases/tag/v0.6.1)).
0.6.0 made it about ten times faster than the Python 0.4 package in a sixth of the memory
([0.6.0 notes](https://github.com/pzfreo/draftwright/releases/tag/v0.6.0)).

## Examples

To see what draftwright does with real parts, browse
**[pzfreo/dw-examples](https://github.com/pzfreo/dw-examples)**: NIST test parts, FreeCAD
library parts, text-to-CAD and build123d parts, and assemblies, each drawn by the stock CLI with
only a title block set. They are unreviewed output, mistakes included, and each drawing's
`.review.json` lists what draftwright found it could not state. Its CI rebuilds them whenever the
pinned draftwright release is bumped.

<table>
  <tr>
    <td><a href="https://github.com/pzfreo/dw-examples/blob/main/drawings/t2c-circular-flange/t2c-circular-flange.pdf"><img src="docs/images/example-t2c-circular-flange.png" alt="Circular flange drawing"></a></td>
    <td><a href="https://github.com/pzfreo/dw-examples/blob/main/drawings/nema17-mount-bracket/nema17-mount-bracket.pdf"><img src="docs/images/example-nema17-mount-bracket.png" alt="NEMA 17 mount bracket drawing"></a></td>
    <td><a href="https://github.com/pzfreo/dw-examples/blob/main/drawings/nist-ftc-06/nist-ftc-06.pdf"><img src="docs/images/example-nist-ftc-06.png" alt="NIST FTC-06 drawing with hole tables and a section"></a></td>
  </tr>
  <tr>
    <td align="center"><sub>Flange: bolt circle callout</sub></td>
    <td align="center"><sub>Bracket: slots, chamfers, fillets</sub></td>
    <td align="center"><sub>NIST FTC-06: hole tables, GD&amp;T, section</sub></td>
  </tr>
</table>

## Install

For the command line on macOS or Linux (arm64 / x86_64):

```sh
curl -fsSL https://raw.githubusercontent.com/pzfreo/draftwright/main/install.sh | sh
```

To inspect it first: download `install.sh`, read it, then run `sh install.sh`.
For Windows, in PowerShell 5.1 or 7:

```powershell
irm https://raw.githubusercontent.com/pzfreo/draftwright/main/install.ps1 | iex
```

The installers verify SHA-256 checksums and install `draftwright` and `draftwright-mcp`
without administrator access. Set `DRAFTWRIGHT_INSTALL_DIR` to change the destination,
or `DRAFTWRIGHT_VERSION=v0.6.0` (also accepts `0.6.0`) to pin a release instead of
the latest stable. On macOS/Linux, set these variables on `sh` when piping, e.g.
`curl -fsSL https://raw.githubusercontent.com/pzfreo/draftwright/main/install.sh | DRAFTWRIGHT_VERSION=v0.6.0 sh`.
On Windows, set `$env:DRAFTWRIGHT_VERSION` / `$env:DRAFTWRIGHT_INSTALL_DIR` before the `irm` line.

| Installer platform | Binary | Default destination |
| --- | --- | --- |
| macOS arm64 (including a Rosetta shell) | `aarch64-apple-darwin` | `~/.local/bin` |
| macOS Intel | `x86_64-apple-darwin` | `~/.local/bin` |
| Linux x86_64 | `x86_64-unknown-linux-gnu`, glibc >= 2.17 for releases newer than v0.6.0 | `~/.local/bin` |
| Linux arm64 | `aarch64-unknown-linux-gnu`, glibc >= 2.17 | `~/.local/bin` |
| Windows x64 / ARM64 | `x86_64-pc-windows-msvc` (x64 emulation on ARM64) | `%LOCALAPPDATA%\Programs\draftwright\bin`, added to USER PATH |

Releases through v0.6.0 require glibc >= 2.35 on Linux x86_64; v0.6.0 has no
Linux arm64 or Intel Mac archive. The installers report unavailable targets explicitly.
Linux archives require glibc; on Alpine/musl, use the Python package or build with Cargo.
For other platforms or unavailable older targets, use `uv tool install draftwright`
or `cargo install draftwright`, or see [Releases](https://github.com/pzfreo/draftwright/releases).

| You use | Install | Platforms |
| --- | --- | --- |
| **Python 3.9+ (abi3)** | `pip install draftwright`, or `uv add draftwright` in a uv project ([PyPI](https://pypi.org/project/draftwright/), [Python guide](docs/python.md)); no Rust needed | Linux x86_64 and aarch64, macOS arm64 and x86_64, Windows x64 |
| **Command line** | `uv tool install draftwright`; installs `draftwright` and `draftwright-mcp` | The same five wheel platforms |
| **Rust crate** | `cargo install draftwright` for both binaries; `draftwright = "0.6"` for the library ([operations](docs/model.md#the-five-operations)) | Native Rust toolchain |
| **Prebuilt archives** | Download a CLI archive from [Releases](https://github.com/pzfreo/draftwright/releases); includes both binaries, schemas and agent docs | macOS arm64 / Intel, Linux x86_64 / arm64, Windows x64; availability depends on the release |
| **Browser** | [draftwright.io](https://draftwright.io) | WebAssembly ([package guide](docs/wasm.md)) |

0.6 replaces the old Python package of the same name; pin `draftwright<0.6` to keep it.

**Upgrading from the old Python package.** If you installed 0.4 or 0.5 as a tool with uv or pipx,
its `draftwright` command stays on your PATH and the new install won't replace it. Remove the old
one first, then install 0.6:

```sh
uv tool uninstall draftwright        # if you used uv
pipx uninstall draftwright           # if you used pipx
uv tool install draftwright          # or: pipx install draftwright
draftwright --version                # should print 0.6.0 or later
```

If pipx says the command "is not on your PATH" or points elsewhere, `pipx install --force` takes
the link over. An older Rust build in `~/.cargo/bin` can also shadow it: `cargo uninstall draftwright`.

If you installed shell completion for the old command (`--install-completion`), pressing Tab after
`draftwright` prints a mess until you replace it. Write the new script over the old one, then open a
new shell:

```sh
draftwright completions zsh  > ~/.zfunc/_draftwright                       # zsh
draftwright completions bash > ~/.bash_completions/draftwright.sh          # bash
draftwright completions fish > ~/.config/fish/completions/draftwright.fish # fish
```

For PowerShell, run this in the current session or add it to `$PROFILE`:

```powershell
draftwright completions powershell | Out-String | Invoke-Expression
```

## Command line

```sh
draftwright render part.step -o part --formats svg,pdf,dxf  # includes editable DXF
draftwright render part.step -o part --units in --paper ansi
draftwright render part.step -o part --max-size A3
draftwright render part.step -o part --set views/iso/omit=true
draftwright review part.step
```

`review` prints JSON containing the drawing as SVG text, the problems and lint's findings.
The [command-line reference](docs/cli.md) lists commands, common options and tested
`--set` settings, including paper and pictorial choices. `draftwright completions zsh`
prints a completion script; see [shell completion](docs/cli.md#shell-completion) for
installation and the other supported shells.

## Output and the Spec

A **Spec** is a small JSON file of drawing settings and edits. Without one, draftwright uses
its automatic choices. `render` writes `part.svg`, `part.pdf` and `part.dxf` for the first
command above (SVG and PDF by default); PNG is also available. Extra sheets get numbered
files, while PDF holds all the sheets. Each format embeds the Spec that made it, and the
command prints JSON listing the files and problems.

For example, save this as `part.drawing.json` to fill in the title block:

```json
{ "sheet": { "title": { "title": "Mounting plate", "number": "MP-001" } } }
```

```sh
draftwright render part.step --spec part.drawing.json -o part
draftwright spec part.pdf  # read the embedded Spec
```

The [model and Spec reference](docs/model.md) describes the settings, documents and Rust API.

## Python

```python
from draftwright import make_drawing, Part
svg, dxf = make_drawing("part.step", out="drawing", title="Bracket")
from build123d import Box
part = Part(Box(120, 80, 20), "plate", title="Mounting plate")
svg, dxf = part.drawing(sheet="A3").render("plate")
```

Install build123d separately for shapes and `Part`. Start with [Drawings from build123d](docs/build123d.md), then the [Python guide](docs/python.md)
for requirements, layout edits and the parts of the build123d API still being completed.

Moving from 0.4: see [Python migration](docs/python.md#migrating-from-python-0-4) and [CLI migration](docs/cli-migration.md).

## What it does

- **Recognises features** with [quiddity](https://github.com/pzfreo/quiddity): holes,
  counterbores, patterns, slots, pockets, turned steps, grooves, chamfers, fillets and threads.
- **Dimensions features** with rules for each family; omissions and placement failures are
  reported with a reason.
- **Reads AP242 PMI (tolerances and GD&T stored in the STEP file)**, including fits, datums,
  threads and surface finish. Unsupported requirements are reported.
- **Lays out sheets** with views, sections, details, hole tables and an ISO 7200 title block;
  it chooses scale and paper size, using more sheets when needed.
- **Produces deterministic output:** the same inputs give the same bytes. SVG and PDF carry
  real, searchable text; DXF has editable text, dimensions, blocks and title-block attributes.

## Assemblies and revisions

```sh
draftwright pack assembly.step -o pack
draftwright diff old.step new.step --spec part.drawing.json -o changes
```

`pack` draws each made part once and writes an ISO 7573 parts list, item numbers and a CSV/JSON
bill of materials. `diff` carries the Spec to a new revision, marks up what changed and reports
edits that need a decision. See [bills of materials](docs/bom.md) and [the engine guide](docs/engine.md#using-it).

## With agents

The CLI release archives include **`draftwright-mcp`**, a local stdio MCP server for Claude
and other MCP clients; the wheel and Cargo installs provide it too. Clients can inspect a
part, edit its Spec, render the drawing and read the findings. The [MCP guide](docs/mcp.md)
also covers the newer local `draftwright mcp-stdio` and `draftwright mcp-http` adapters.

The Spec is the steering surface: an agent inspects the drawing and findings, changes settings
or edits by item ID, then renders and reviews again. Requirements belong in STEP; the Spec
controls the drawing.

Two agent skills ship in [`skills/`](skills/): **[draftwright-step](skills/draftwright-step/SKILL.md)**
for any STEP file, as a basic drawing or with requirements written by Specify, and
**[draftwright-build123d](skills/draftwright-build123d/SKILL.md)** for build123d scripts. For Claude
Code, copy a folder to `.claude/skills/` in your project or to `~/.claude/skills/`.

## Documentation

- [Command-line reference](docs/cli.md): commands, common options, `--set` settings and shell completion
- [Engine guide](docs/engine.md): building, recognition and revision workflows, what is and isn't supported,
  known limitations, checking a change
- [Model and Spec reference](docs/model.md): the Analysis, Spec and Plan, and every setting
- [Drawings from build123d: support and how-to](docs/build123d.md)
- [Python API and migration](docs/python.md)
- [MCP and agents](docs/mcp.md)
- [Browser / WASM package](docs/wasm.md)
- [Thread callouts](docs/thread-callouts.md) · [Bills of materials](docs/bom.md) ·
  [Finding impact](docs/finding-impact.md)
- [Changelog](CHANGELOG.md)

## How it's built

Draftwright is AI-coded with Claude and Codex, directed by its maintainer. Large changes get
independent review. The output is deterministic, and the public examples are built by the
stock CLI with no hand edits.

## Related projects

- [quiddity](https://github.com/pzfreo/quiddity): feature recognition from STEP B-rep, built on
  the haecceity geometry kernel that draftwright uses.
- [Specify](https://github.com/pzfreo/specify-core): writes tolerances, fits, GD&T and BOM data
  into STEP as AP242 PMI; available at [draftwright.io/specify](https://draftwright.io/specify).
- [dw-examples](https://github.com/pzfreo/dw-examples): the public example drawings.
- [draftwright-python](https://github.com/pzfreo/draftwright-python): the earlier Python
  implementation, being retired.

## Licence and contributing

Draftwright is licensed under the [GNU AGPL v3.0](LICENSE), and is also available under
separate commercial terms; contact paul@draftwright.io. quiddity and haecceity are Apache-2.0.
The bundled IBM Plex fonts are under the SIL Open Font License 1.1.

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md); pull requests need the
[Contributor License Agreement](CLA.md).
