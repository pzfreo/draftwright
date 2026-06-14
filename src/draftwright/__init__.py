"""draftwright — automated technical-drawing generation for build123d.

Takes a build123d solid and produces a fully-annotated multi-view technical
drawing (orthographic views, dimensions, section A–A, title block) ready for
DXF/SVG export::

    from draftwright import make_drawing
    make_drawing(my_part, out="drawing")

Requires build123d-drafting-helpers for annotation primitives.
Licensed under the GNU Affero General Public License v3 (AGPL-3.0).
"""

from draftwright.make_drawing import (
    Drawing,
    analyse_face_levels,
    build_drawing,
    choose_scale,
    dedup_diams,
    fix_svg_page_size,
    generate_script,
    lint_feature_coverage,
    make_drawing,
)
from draftwright.pmi import PmiRecord, extract_pmi

__all__ = [
    "Drawing",
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
