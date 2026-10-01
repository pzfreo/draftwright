"""Compat facade: the engine split into stage modules (#138 / ADR 1 (was 0005)).

The `Drawing` result object now lives in `drawing.py`; build orchestration
(`build_drawing`/`make_drawing`) in `builder.py`; the `_cli`
compat shim beside the Typer app in `cli.py` (#523). This module re-exports the
public surface so `from draftwright.make_drawing import ...` keeps working. The
installed `draftwright` command points to `draftwright.cli:app`.
"""

import warnings

from draftwright.builder import (  # noqa: F401
    build_drawing,
    make_drawing,
)
from draftwright.cli import _cli  # noqa: F401 — #523: the shim lives beside the Typer app now
from draftwright.drawing import Drawing, FeatureInfo  # noqa: F401
from draftwright.export import fix_svg_page_size  # noqa: F401
from draftwright.linting import lint_feature_coverage  # noqa: F401

warnings.warn(
    "draftwright.make_drawing is deprecated (#1936); import build_drawing and make_drawing "
    "from draftwright.builder, Drawing from draftwright.drawing, and other symbols from "
    "their owning modules. Removed in 0.6.0.",
    DeprecationWarning,
    stacklevel=2,
)

if __name__ == "__main__":
    _cli()
