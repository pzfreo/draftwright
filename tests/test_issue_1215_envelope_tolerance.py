"""#1215 — an authored envelope tolerance must render, on all three extents.

`sheet.envelope().tolerance(0.05, on="height")` recorded the decoration and never printed it:

    tolerance on height  -> {'dim_height': '10', 'm_env_depth': '20', 'm_env_width': '30'}
    tolerance on width   -> {'dim_height': '10', 'm_env_depth': '20', 'm_env_width': '30'}
    tolerance on depth   -> {'dim_height': '10', 'm_env_depth': '20', 'm_env_width': '30'}

Two independent drops: `render_envelope` composed its width/depth labels from `value_text` alone,
and `_compile_overall_height` built the ladder rung with no tolerance, so the height had nothing
to render even once its renderer asked.

**These tests assert the LABEL, and that is the whole point.** The first version of this fix
passed `Dimension(tolerance=...)` and asserted on the constructor argument via `_dw_spec`. That
renders nothing — helpers do
`rendered = label if label is not None else draft._number_with_units(measured, tolerance)`, so an
explicit label DISCARDS the tolerance, and every dimension here passes one because the compiler
owns the value text. The tests passed, the sheet was unchanged, and one of them asserted
`"±" not in label` — codifying the defect as intended behaviour, passing on unfixed code and
failing on a working fix (#1234 review).

`test_the_tolerance_reaches_the_exported_ink` is the backstop: labels are metadata, and glyph
count is what the reader sees.
"""

from __future__ import annotations

import pytest
from build123d import Box

from draftwright.sheet import Sheet

_AXES = ("width", "depth", "height")
_ANNOTATION = {"width": "m_env_width", "depth": "m_env_depth", "height": "dim_height"}
_BARE = {"width": "30", "depth": "20", "height": "10"}


def _built(axis=None, lo=0.05, hi=None):
    sheet = Sheet(Box(30, 20, 10))
    params = sheet.envelope()
    if axis is not None:
        params.tolerance(lo, hi, on=axis) if hi is not None else params.tolerance(lo, on=axis)
    sheet.auto_dimensions()
    return sheet.build()


def _label(drawing, name):
    assert name in drawing.registry.names(), f"{name} absent; nothing to assert about"
    return str(getattr(drawing.registry.named(name), "label", None))


class TestTheDecorationReachesAllThreeExtents:
    @pytest.mark.parametrize("axis", _AXES)
    def test_the_decorated_extent_prints_its_tolerance(self, axis):
        assert _label(_built(axis), _ANNOTATION[axis]) == f"{_BARE[axis]} ±0.1"

    @pytest.mark.parametrize("axis", _AXES)
    def test_the_other_two_extents_stay_bare(self, axis):
        # The decoration is per-parameter: a fix that tolerances every extent passes the test
        # above on all three.
        drawing = _built(axis)
        for other in _AXES:
            if other != axis:
                assert _label(drawing, _ANNOTATION[other]) == _BARE[other], other

    def test_an_undecorated_envelope_prints_no_tolerance(self):
        # The precondition. `envelope()` is still called — declaring it is what puts the width
        # and depth extents on the sheet at all; a bare `Sheet(Box(...)).auto_dimensions()`
        # registers only `dim_height`, and asserting over `_ANNOTATION` there fails looking up
        # an annotation that does not exist.
        drawing = _built(axis=None)
        for axis in _AXES:
            assert _label(drawing, _ANNOTATION[axis]) == _BARE[axis], axis

    def test_a_limit_pair_prints_both_limits(self):
        assert _label(_built("height", 0.1, 0.2), "dim_height") == "10 +0.2 -0.1"


class TestTheTwoPathsAgree:
    """#1215's second acceptance line. The height rides `render_height_ladder`; width and depth
    ride `render_envelope`. They took the decoration from different places and neither printed
    it, so "they agree" was satisfied vacuously until both did."""

    def test_every_extent_prints_the_same_suffix_for_the_same_tolerance(self):
        suffixes = {
            axis: _label(_built(axis), _ANNOTATION[axis]).split(" ", 1)[1] for axis in _AXES
        }
        assert set(suffixes.values()) == {"±0.1"}, suffixes


class TestTheTolerandeReachesTheInk:
    """Labels are metadata. This is what the reader sees."""

    def test_the_tolerance_reaches_the_exported_ink(self, tmp_path):
        import re

        def measured(tolerance):
            drawing = _built("width") if tolerance else _built(axis=None)
            annotation = drawing.registry.named("m_env_width")
            out = tmp_path / f"o{int(bool(tolerance))}.svg"
            drawing.export(str(out))
            paths = len(re.findall(r"<path", out.read_text()))
            return len(annotation.faces()), paths

        bare_faces, bare_paths = measured(False)
        tol_faces, tol_paths = measured(True)
        assert tol_faces > bare_faces, (bare_faces, tol_faces)
        assert tol_paths > bare_paths, (bare_paths, tol_paths)


class TestAFitClassRendersToo:
    """#1215's third acceptance line, which I first asserted was unreachable. It is not.

    `build_drawing(decorations=...)` is public (ADR 0011's public-IR input) and `_Params`
    forwards unknown attributes to the `Sheet`, so `not hasattr(handle, "fit")` proved only that
    `Sheet` has no `fit` verb at all — not that an envelope cannot take one (#1234 review).

    It also matters which mechanism renders it: `_tol_suffix` handles `FitClass`, while the ink
    path's `_number_with_units` raises `TypeError: 'FitClass' object is not subscriptable`. So
    composing the label is the only route that satisfies this line at all.
    """

    def test_a_fit_class_on_an_envelope_extent_renders_its_code(self):
        from draftwright.builder import build_drawing
        from draftwright.fits import fit_class

        sheet = Sheet(Box(30, 20, 10))
        sheet.envelope()
        sheet.auto_dimensions()
        model = sheet.build().model()
        envelope = next(f for f in model.features if f.kind == "envelope")
        drawing = build_drawing(
            Box(30, 20, 10),
            model=model,
            decorations={(envelope, "length", "width"): fit_class("h6", 30.0, "class")},
            title="T",
            number="N-1",
        )
        assert _label(drawing, "m_env_width") == "30 h6"

    def test_the_ink_path_would_have_raised_on_it(self):
        # Why the label route is not merely a preference. If the tolerance were handed to the
        # Dimension instead, this is what the reader would get.
        import pytest as _pytest
        from build123d_drafting.helpers import _format_label

        from draftwright.builder import build_drawing
        from draftwright.fits import fit_class

        draft = build_drawing(Box(30, 20, 10), title="T", number="N-1").draft
        with _pytest.raises(TypeError):
            _format_label(30.0, draft, fit_class("h6", 30.0, "class"))
