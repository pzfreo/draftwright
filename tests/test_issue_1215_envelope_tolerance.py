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

    def test_a_bare_tolerance_folds_onto_every_extent(self):
        """`envelope().tolerance(0.05)` with no `on=` — the kind-keyed fallback in `_decorated`.

        Untested until now, and the PR's central architectural argument rests on it: sourcing
        the decoration through `_decorated` rather than reading `model.decorations` here. A
        mutation dropping the kind-keyed lookup silently loses the ± on the HEIGHT alone and
        passed the entire fast tier, because every other test passes `on=` (#1234 review r2).
        """
        sheet = Sheet(Box(30, 20, 10))
        sheet.envelope().tolerance(0.05)
        sheet.auto_dimensions()
        drawing = sheet.build()
        for axis in _AXES:
            assert _label(drawing, _ANNOTATION[axis]) == f"{_BARE[axis]} ±0.1", axis

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


class TestTheToleranceReachesTheInk:
    """Labels are metadata. This is what the reader sees."""

    @pytest.mark.parametrize("axis", _AXES)
    def test_the_tolerance_reaches_the_exported_ink(self, axis, tmp_path):
        # All three axes, not just width: the HEIGHT is the path with two code changes
        # (compiler rung + ladder renderer) and had no ink assertion at all (#1234 review r2).
        import re

        def measured(tolerance):
            drawing = _built(axis) if tolerance else _built(axis=None)
            annotation = drawing.registry.named(_ANNOTATION[axis])
            out = tmp_path / f"{axis}{int(bool(tolerance))}.svg"
            drawing.export(str(out))
            paths = len(re.findall(r"<path", out.read_text()))
            return len(annotation.faces()), paths

        bare_faces, bare_paths = measured(False)
        tol_faces, tol_paths = measured(True)
        assert tol_faces > bare_faces, (bare_faces, tol_faces)
        assert tol_paths > bare_paths, (bare_paths, tol_paths)


class TestTheCostOnACrowdedSheet:
    """A longer label grows ALONG the dimension line when the label is rotated.

    The height dim is `side="right"`, so its label is rotated and the suffix extends it in Y —
    the direction the corridor does not reserve, since the strip's depth runs in X. Measured on
    `Sheet(Box(30, 20, 10)).envelope().tolerance(0.05, on="height")`:

        label bbox height  3.273 -> 12.300
        annotation bbox    (64.95, 85.05) -> unchanged

    The annotation's own `bounding_box()` not moving is why a bbox comparison misses this
    entirely, and why the corridor solve does not account for it.

    **What this class does NOT assert is the lint outcome.** On this machine the fixture goes
    from `view_annotation_inside_extents` (info) to `view_annotation_overlap` (warning), and
    `repair()` does not clear it. On CI it produces neither — `codes` is the empty set. I first
    pinned the warning and it failed on three ubuntu shards: whether the taller label actually
    crosses the front view's edge depends on the solved offset, which differs by platform. The
    geometry is the durable fact; the lint code is not, and asserting it was pinning my own
    machine (#1234 review r2, and its CI run).
    """

    @staticmethod
    def _label_box(drawing, name):
        return getattr(drawing.registry.named(name), "label_bbox")

    def test_a_rotated_label_grows_along_the_dimension_line(self):
        bare = self._label_box(_built(axis=None), "dim_height")
        tol = self._label_box(_built("height"), "dim_height")
        assert (tol[3] - tol[1]) > (bare[3] - bare[1]) * 2, (bare, tol)
        # ...and NOT across it: the strip depth is unchanged, which is the whole problem.
        assert round(tol[2] - tol[0], 3) == round(bare[2] - bare[0], 3), (bare, tol)

    def test_the_annotations_own_bounding_box_does_not_move(self):
        # Why this went unnoticed: the obvious check sees nothing.
        def box(drawing):
            b = drawing.registry.named("dim_height").bounding_box()
            return (round(b.min.Y, 2), round(b.max.Y, 2))

        assert box(_built(axis=None)) == box(_built("height"))

    def test_an_unrotated_extent_grows_the_other_way(self):
        # The contrast that makes the ROTATION the cause rather than the suffix: `m_env_width`
        # is horizontal, so its label grows in X while the height's grows in Y. Both grow along
        # their own dimension line, and neither growth is into the reserved slot depth — an
        # earlier version of this comment claimed X "IS reserved", which is false: `_queue`
        # registers the envelope extents with `axis="y"`, so their reserved depth runs in Y.
        # The lint fires for the height and not the width for a POSITIONAL reason — the height
        # label sits inside the front view's extents, the width label in open space below the
        # plan (#1234 review r3).
        bare = self._label_box(_built(axis=None), "m_env_width")
        tol = self._label_box(_built("width"), "m_env_width")
        assert (tol[2] - tol[0]) > (bare[2] - bare[0]), (bare, tol)
        assert round(tol[3] - tol[1], 3) == round(bare[3] - bare[1], 3), (bare, tol)


class TestTheSuffixDoesNotBreakLabelIdentity:
    """`_overall_height_name` matched `dim_height` by exact string equality against a re-derived
    numeric label. Composing a suffix into that label broke it — in BOTH the canonical branch
    and the generalised fallback — so a toleranced part silently stopped finding its own height
    dim.

    The consequence is quiet by design: the caller treats `None` as "no demotion, the safe
    outcome", so a crowded part would drop its detail view instead of demoting the height, with
    nothing red. That is why this is asserted directly rather than through a build — I could not
    construct a part that reaches the #661 demotion retry, and neither could the review
    (#1234 review r4). The mechanism is what is pinned.
    """

    @staticmethod
    def _analysis_for(drawing):
        """The only field `_overall_height_name` reads off the analysis is `z_size`.

        Built from the public model rather than reaching for `drawing._analysis`: #741's guard
        budgets test-side reads of `Drawing` privates and this would have added two, which is
        reach-through where the public surface already answers the question.
        """
        from types import SimpleNamespace

        return SimpleNamespace(z_size=drawing.model().bbox.size.Z)

    def test_the_height_dim_is_still_found_when_it_carries_a_tolerance(self):
        from draftwright.annotations.sections import _overall_height_name

        for axis, expected in ((None, "10"), ("height", "10 ±0.1")):
            drawing = _built(axis) if axis else _built(axis=None)
            assert _label(drawing, "dim_height") == expected, axis
            found = _overall_height_name(drawing, self._analysis_for(drawing))
            assert found == "dim_height", (axis, found)

    def test_a_fit_class_suffix_is_matched_too(self):
        # The other suffix form `_tol_suffix` emits: a class code, not a ± pair.
        from draftwright.annotations.sections import _overall_height_name
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
            decorations={(envelope, "length", "height"): fit_class("h6", 10.0, "class")},
            title="T",
            number="N-1",
        )
        assert _label(drawing, "dim_height") == "10 h6"
        assert _overall_height_name(drawing, self._analysis_for(drawing)) == "dim_height"


class TestTheGeneralisedFallbackMatchesToo:
    """`_overall_height_name` has TWO branches and only the canonical one was covered.

    Reverting the fallback's match to exact string equality passed the entire fast tier —
    the r4 commit and its test docstring both claimed the break was fixed "in BOTH the
    canonical branch and the generalised fallback", and the second half was prose. That is the
    failure mode this PR's own commit messages indict twice, recurring a third time inside the
    fix for it (#1234 review r5).

    Removing `dim_height` forces the fallback, which searches envelope-attributed annotations
    for a portrait-shaped one whose label states the height.
    """

    def test_the_fallback_finds_a_toleranced_height_under_another_name(self):
        from draftwright.annotations.sections import _overall_height_name

        drawing = _built("height")
        assert _label(drawing, "dim_height") == "10 ±0.1"
        envelope = next(f for f in drawing.model().features if f.kind == "envelope")
        drawing.remove("dim_height")
        assert "dim_height" not in drawing.registry.names()

        drawing.place_dim(
            (98, 70, 0),
            (98, 80, 0),
            "right",
            "front",
            drawing.draft,
            name="dim_length7",
            feature=envelope,
            label="10",
            tolerance=0.05,
        )
        assert str(drawing.get_annotation("dim_length7").label) == "10 ±0.1"
        found = _overall_height_name(
            drawing, TestTheSuffixDoesNotBreakLabelIdentity._analysis_for(drawing)
        )
        assert found == "dim_length7", found


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
        #
        # `_number_with_units` is the INK path — the branch helpers take when `label is None`.
        # An earlier version of this test asserted the same thing about `_format_label`, which
        # is the lint-parity approximation; both raise, so the claim held, but the test did not
        # test what its own comment said (#1234 review r2).
        import pytest as _pytest

        from draftwright.builder import build_drawing
        from draftwright.fits import fit_class

        draft = build_drawing(Box(30, 20, 10), title="T", number="N-1").draft
        with _pytest.raises(TypeError):
            draft._number_with_units(30.0, fit_class("h6", 30.0, "class"))
