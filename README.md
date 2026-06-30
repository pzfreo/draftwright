# draftwright

[![CI](https://github.com/pzfreo/draftwright/actions/workflows/ci.yml/badge.svg)](https://github.com/pzfreo/draftwright/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/draftwright.svg)](https://pypi.org/project/draftwright/)
[![Python](https://img.shields.io/pypi/pyversions/draftwright.svg)](https://pypi.org/project/draftwright/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Automated technical-drawing generation for [build123d](https://github.com/gumyr/build123d).
Point it at a solid (or a STEP file) and get a fully-annotated multi-view engineering
drawing — orthographic views, dimensions, section A–A, ISO hatching, title block — ready
to export as SVG and DXF.

```python
from build123d import Box, Cylinder, Pos
from draftwright import make_drawing

part = Box(80, 60, 20) - Pos(0, 0, 5) * Cylinder(8, 20)
make_drawing(part, out="my_part", title="Mounting Block", number="DWG-001")
# writes my_part.svg and my_part.dxf
```

Or from the command line:

```
draftwright my_part.step --title "Mounting Block" --number DWG-001
```

## What it produces

- **Three orthographic views** (front, plan, side) sized and scaled automatically to the
  page
- **Dimensions** on every principal envelope face, plus bore callouts (diameter, depth,
  counterbore, spotface) on all holes, and ø leader-callouts for the external stepped
  diameters of turned parts
- **Section A–A** with ISO 128-44 solid filled cutting-plane arrows and ISO 128-50 45°
  hatching on the cut face, triggered automatically when blind or stepped holes would
  otherwise be hidden-line-only
- **Title block** (ISO 7200) with part name, drawing number, scale, tolerance, and date
- **Lint** — `Drawing.lint()` checks annotation coverage, page bounds, and ISO compliance
  and returns structured `LintIssue` objects

All output is real build123d geometry, so SVG and DXF export come from the same source
and dimensions are live on the DXF layer.

## Installation

```
pip install draftwright
```

Requires Python ≥ 3.10 and build123d ≥ 0.9.0. Annotation primitives are provided by
[`build123d-drafting-helpers`](https://github.com/pzfreo/build123d-drafting-helpers),
which is installed automatically as a dependency.

## Usage

### From a build123d solid

```python
from draftwright import make_drawing, build_drawing, Drawing

# One-shot: write SVG + DXF
make_drawing(part, out="drawing", title="My Part", number="DWG-001")

# Composable: get a Drawing object to inspect or extend
dwg = build_drawing(part, title="My Part")
issues = dwg.lint()          # list[LintIssue]
svg_path, dxf_path = dwg.export("drawing")
```

### From a STEP file

```python
from draftwright import make_drawing
make_drawing(step_file="part.step", out="drawing")
```

Or via the CLI:

```
draftwright part.step --out drawing --scale 2 --page A3
draftwright part.step --script   # write an editable .py drawing script instead
```

### Scale and page control

```python
from draftwright import choose_scale

# Auto-select the best ISO/ASME standard scale for an A3 sheet
scale, page_w, page_h, n_steps = choose_scale(80, 60, 20, page="A3")

# Override
make_drawing(part, out="drawing", scale=2.0, page="A2")
```

### Edit, critique, and self-repair

Edit a `Drawing` in **domain vocabulary** — the engine places annotations
automatically, so you say *what* to dimension, not *where*:

```python
dwg = build_drawing(part)

# Inspect detected features and add a dimension in domain terms (auto-placed):
for f in dwg.features("plan"):
    dwg.place_dim(f.page_pos, (f.page_pos[0] + f.diameter, f.page_pos[1]),
                  "below", "plan", dwg.draft, name="dim_pocket")

crit = dwg.lint_summary()   # {"passed", "score", "by_code", "issues":[…suggestion]}
dwg.repair()                # auto-fix mechanically-fixable lint; never worsens
```

Each `LintIssue` carries a domain-meaningful `code` and, when computable, a
ready-to-apply `suggestion`. See `docs/adr/` for the design (deterministic
generation, the lint→repair loop, and the constraint-based layout engine).

## Architecture

draftwright is structured as a **part-drawing compiler** (ADR 0008): feature
detectors → a `PartModel` IR (`Feature`s exposing `DimParameter`s) → a dimensioning
planner → renderers, feeding a shared layout/projection/export stack. It builds on
two libraries:

```
draftwright
    └── build123d-drafting-helpers  — Dimension, Leader, HoleCallout, …
    └── build123d                   — CAD kernel
```

It owns feature recognition (`recognition/`) and linting (`linting/`); annotation
primitives (`Dimension`, `Leader`, etc.) live in `build123d-drafting-helpers` and can
be used independently. The compiler is largely converged in production — turned
dims/lengths, centre marks, envelope, slots, holes (callouts/locations/grouping),
the section A–A trigger, the prismatic step-ladder + rotational furniture, and PMI/GD&T
are all on the IR — the migration is complete (one path; the orchestrator is
build → plan → render). See
[`docs/target-architecture.md`](docs/target-architecture.md) and
[`docs/adr/`](docs/adr/). The engine handles view layout (strip/zone model), scale
selection, annotation placement, and section rendering.
