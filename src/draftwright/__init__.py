"""draftwright — automated technical-drawing generation for build123d.

Takes a build123d solid and produces a fully-annotated multi-view technical
drawing (orthographic views, dimensions, section A–A, title block) ready for
DXF/SVG export::

    from draftwright import make_drawing
    make_drawing(my_part, out="drawing")

Requires build123d-drafting-helpers for annotation primitives.
Licensed under the GNU Affero General Public License v3 (AGPL-3.0).
"""

import sys as _sys
import types as _types

from draftwright.analysis import analyse_face_levels, dedup_diams
from draftwright.builder import (
    build_drawing,
    generate_script,
    make_drawing,
)
from draftwright.drawing import Drawing, FeatureInfo
from draftwright.export import fix_svg_page_size
from draftwright.linting import lint_feature_coverage
from draftwright.pmi import PmiRecord, extract_pmi
from draftwright.sheet import choose_scale

_make_drawing_function = make_drawing


class _DraftwrightModule(_types.ModuleType):
    def __getattribute__(self, name):
        if name == "make_drawing":
            namespace = _types.ModuleType.__getattribute__(self, "__dict__")
            value = namespace.get(name)
            if (
                isinstance(value, _types.ModuleType)
                and value.__name__ == "draftwright.make_drawing"
            ):
                public = namespace["_make_drawing_function"]
                _types.ModuleType.__setattr__(self, name, public)
                return public
        return _types.ModuleType.__getattribute__(self, name)


_sys.modules[__name__].__class__ = _DraftwrightModule

__all__ = [
    "Drawing",
    "FeatureInfo",
    "PmiRecord",
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
