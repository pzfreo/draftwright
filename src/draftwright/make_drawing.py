"""Compat facade: the engine split into stage modules (#138 / ADR 0005).

The `Drawing` result object now lives in `drawing.py`; build orchestration
(`build_drawing`/`make_drawing`/`generate_script`/`_cli`) in `builder.py`. This
module re-exports the public surface so `from draftwright.make_drawing import ...`
and the `draftwright` CLI entry point keep working.
"""

from draftwright.builder import (  # noqa: F401
    _cli,
    build_drawing,
    generate_script,
    make_drawing,
)
from draftwright.drawing import Drawing, FeatureInfo  # noqa: F401
from draftwright.export import fix_svg_page_size  # noqa: F401
from draftwright.linting import lint_feature_coverage  # noqa: F401

if __name__ == "__main__":
    _cli()
