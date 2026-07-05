"""draftwright — automated technical-drawing generation for build123d.

Takes a build123d solid and produces a fully-annotated multi-view technical
drawing (orthographic views, dimensions, section A–A, title block) ready for
DXF/SVG export::

    from draftwright import make_drawing
    make_drawing(my_part, out="drawing")

Requires build123d-drafting-helpers for annotation primitives.
Licensed under the GNU Affero General Public License v3 (AGPL-3.0).
"""

import importlib as _importlib
import sys as _sys
import types as _types
from typing import TYPE_CHECKING

# Public API resolved lazily (PEP 562): `import draftwright` — and, crucially,
# `import draftwright.cli` (which runs this __init__ first) — must NOT eagerly
# pull in the engine. The engine drags build123d/OCP, ~5 s of CAD-kernel import.
# The CLI's shell completion, --help and --version import this package but touch
# none of these names, so they stay sub-second instead of paying for the kernel
# on every TAB press (#313). Each name maps to the submodule that provides it.
_LAZY = {
    "analyse_face_levels": "draftwright.analysis",
    "dedup_diams": "draftwright.analysis",
    "build_drawing": "draftwright.builder",
    "generate_script": "draftwright.builder",
    "make_drawing": "draftwright.builder",
    "Drawing": "draftwright.drawing",
    "FeatureInfo": "draftwright.drawing",
    "Sheet": "draftwright.sheet_dsl",
    "fix_svg_page_size": "draftwright.export",
    "lint_feature_coverage": "draftwright.linting",
    "PmiRecord": "draftwright.pmi",
    "extract_pmi": "draftwright.pmi",
    "choose_scale": "draftwright.sheet",
}


def _resolve(name):
    """Import the providing submodule and cache the public object on the package."""
    value = getattr(_importlib.import_module(_LAZY[name]), name)
    _types.ModuleType.__setattr__(_sys.modules[__name__], name, value)
    return value


class _DraftwrightModule(_types.ModuleType):
    def __getattr__(self, name):
        # PEP 562 miss handler: resolve a public name on first access.
        if name in _LAZY:
            return _resolve(name)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __getattribute__(self, name):
        # `draftwright.make_drawing` must be the FUNCTION even after the compat
        # SUBMODULE of the same name is imported and shadows it as a package
        # attribute (importing draftwright.make_drawing binds the module here).
        if name == "make_drawing":
            namespace = _types.ModuleType.__getattribute__(self, "__dict__")
            value = namespace.get("make_drawing")
            if (
                isinstance(value, _types.ModuleType)
                and value.__name__ == "draftwright.make_drawing"
            ):
                return _resolve("make_drawing")
        return _types.ModuleType.__getattribute__(self, name)


_sys.modules[__name__].__class__ = _DraftwrightModule


if TYPE_CHECKING:  # static analysers / IDEs — no runtime import, no kernel cost
    from draftwright.analysis import analyse_face_levels, dedup_diams
    from draftwright.builder import build_drawing, generate_script, make_drawing
    from draftwright.drawing import Drawing, FeatureInfo
    from draftwright.export import fix_svg_page_size
    from draftwright.linting import lint_feature_coverage
    from draftwright.pmi import PmiRecord, extract_pmi
    from draftwright.sheet import choose_scale
    from draftwright.sheet_dsl import Sheet


def __dir__():
    # Surface the lazy public names (not in __dict__ until first accessed)
    # *alongside* the normal module contents — dunders, imported submodules — so
    # introspection / REPL completion sees the full surface, not a subset.
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "Drawing",
    "FeatureInfo",
    "PmiRecord",
    "Sheet",
    "analyse_face_levels",
    "build_drawing",
    "choose_scale",
    "dedup_diams",
    "extract_pmi",
    "fix_svg_page_size",
    "generate_script",
    "lint_feature_coverage",
    "make_drawing",
]
