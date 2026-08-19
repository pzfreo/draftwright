"""#1215 — an authored envelope tolerance must render, on all three extents.

`sheet.envelope().tolerance(0.05, on="height")` recorded the decoration on the model and it was
never printed. Measured before the fix, on all three axes:

    tolerance on height  -> {'dim_height': '10', 'm_env_depth': '20', 'm_env_width': '30'}
    tolerance on width   -> {'dim_height': '10', 'm_env_depth': '20', 'm_env_width': '30'}
    tolerance on depth   -> {'dim_height': '10', 'm_env_depth': '20', 'm_env_width': '30'}

Two independent drops, which is why the issue says the two renderers need the same answer:

* `render_envelope` built width/depth labels from `value_text` and passed no `tolerance=`;
* `_compile_overall_height` built the ladder rung with no tolerance at all, so the height had
  nothing to pass even if the renderer had asked.

**Where the assertions look, and why.** `Dimension` formats its own tolerance and leaves `label`
alone — measured, a `Dimension(label="30", tolerance=0.02)` still reports `label == "30"`. So the
tolerance is NOT visible on the annotation's label, and a test reading `label` would pass against
completely unfixed code. These read the `tolerance` the Dimension was constructed with, via the
`_dw_spec` the repair loop already records.

The suffix is `_tol_suffix`'s, which is documented as matching helpers' `_format_label` byte for
byte — verified in `test_the_suffix_matches_what_the_helper_renders` rather than assumed, because
the footprint depends on it.
"""

from __future__ import annotations

import pytest
from build123d import Box

from draftwright._core import _tol_suffix
from draftwright.sheet import Sheet

_AXES = ("width", "depth", "height")
_ANNOTATION = {"width": "m_env_width", "depth": "m_env_depth", "height": "dim_height"}


def _built(axis, tolerance=0.05):
    sheet = Sheet(Box(30, 20, 10))
    sheet.envelope().tolerance(tolerance, on=axis)
    sheet.auto_dimensions()
    return sheet.build()


def _tolerance_of(drawing, name):
    spec = getattr(drawing.registry.named(name), "_dw_spec", None)
    assert spec is not None, f"{name} was not built through `_dim`, so it records no spec"
    return spec.kwargs.get("tolerance")


class TestTheDecorationReachesAllThreeExtents:
    @pytest.mark.parametrize("axis", _AXES)
    def test_the_decorated_extent_renders_its_tolerance(self, axis):
        drawing = _built(axis)
        assert _tolerance_of(drawing, _ANNOTATION[axis]) == 0.05

    @pytest.mark.parametrize("axis", _AXES)
    def test_the_other_two_extents_are_untouched(self, axis):
        # The decoration is per-parameter. Without this a fix that tolerances every extent
        # would pass the test above on all three.
        drawing = _built(axis)
        for other in _AXES:
            if other == axis:
                continue
            assert _tolerance_of(drawing, _ANNOTATION[other]) is None, other

    def test_an_undecorated_envelope_stays_bare(self):
        # The precondition for both assertions above: they mean nothing if a plain envelope
        # already carried a tolerance from somewhere.
        #
        # `envelope()` is still called — declaring the envelope is what puts the width and
        # depth extents on the sheet at all. Measured, a bare `Sheet(Box(...)).auto_dimensions()`
        # draws only `dim_height`; asserting over `_ANNOTATION` on that build fails looking up
        # an annotation that does not exist, which is a broken precondition, not a passing one.
        sheet = Sheet(Box(30, 20, 10))
        sheet.envelope()
        sheet.auto_dimensions()
        drawing = sheet.build()
        for name in _ANNOTATION.values():
            assert name in drawing.registry.names(), f"{name} absent; nothing to assert about"
            assert _tolerance_of(drawing, name) is None, name

    def test_a_limit_pair_survives_as_a_pair(self):
        # `_tol_value` keeps `(lower, upper)`; the renderers must not coerce it to a float.
        drawing = _built("height", tolerance=None)
        sheet = Sheet(Box(30, 20, 10))
        sheet.envelope().tolerance(0.1, 0.2, on="height")
        sheet.auto_dimensions()
        drawing = sheet.build()
        assert _tolerance_of(drawing, "dim_height") == (0.1, 0.2)


class TestTheTwoPathsAgree:
    """#1215's second acceptance line: the ladder path and the ordinary envelope path.

    The height rides `render_height_ladder`, width and depth ride `render_envelope`. They took
    the decoration from different places and only one of them looked.
    """

    def test_both_paths_carry_the_same_kind_of_value(self):
        for axis in _AXES:
            assert _tolerance_of(_built(axis), _ANNOTATION[axis]) == 0.05, axis

    def test_the_label_itself_stays_bare_on_both_paths(self):
        # Stated because it is surprising, and because it is why these tests read `_dw_spec`:
        # `Dimension` renders the tolerance without touching `label`. A test asserting on the
        # label would pass against unfixed code.
        for axis in _AXES:
            drawing = _built(axis)
            label = str(getattr(drawing.registry.named(_ANNOTATION[axis]), "label", None))
            assert "±" not in label, (axis, label)


class TestTheFootprintMeasuresWhatIsDrawn:
    def test_the_suffix_matches_what_the_helper_renders(self):
        """The corridor reserves space using `_tol_suffix`; the sheet draws helpers'
        `_format_label`. If they disagree a toleranced extent reserves the wrong width."""
        from build123d_drafting.helpers import _format_label

        from draftwright.builder import build_drawing

        draft = build_drawing(Box(30, 20, 10), title="T", number="N-1").draft
        for tolerance in (0.05, 0.5, (0.1, 0.2)):
            expected = _format_label(30.0, draft, tolerance)
            ours = f"{round(30.0, draft.decimal_precision):.{draft.decimal_precision}f}"
            assert ours + _tol_suffix(tolerance, draft) == expected, tolerance


class TestWhatThisDoesNotCover:
    def test_an_envelope_cannot_take_a_fit_class(self):
        """#1215's third acceptance line asks for `FitClass` rendering. It is unreachable.

        `_Params` — the envelope handle — has no `fit()`, and `_Dim.fit` is diametral by
        design ("a fit is diametral"), reading `self._sheet._features[self._i].diameter`. An
        envelope's parameters are width/height/depth and it has no diameter, so no `FitClass`
        can key onto one. `_tol_suffix` handles `FitClass` already, so the day an envelope
        grows a diametral parameter the suffix is ready; nothing else here is.

        Asserted rather than left as prose, so this stops being true loudly.
        """
        sheet = Sheet(Box(30, 20, 10))
        handle = sheet.envelope()
        assert not hasattr(handle, "fit"), "an envelope grew fit(); #1215's third line is now live"
        assert "diameter" not in {p.role for p in sheet.features[0].parameters()}
