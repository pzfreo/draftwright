from collections import Counter
from pathlib import Path

import pytest

from draftwright import build_drawing
from draftwright.model import StepLevelFeature
from draftwright.model.compiled import compile_dimensions

_ISSUE_915 = Path(__file__).parent / "fixtures" / "issue_915_case_study_2.step"


#: The two crossings this fixture is known to carry, so that `lint() == []` can stay
#: a real assertion instead of being relaxed to "some issues are fine".
#:
#: Both are `'170'`'s dimension line running through another annotation's text, and
#: both appear on the A1 and the A2 build alike. The larger, 18.2 mm through
#: `'50 × 120 × 5 DEEP'`, is most of the width of that callout. Rendered at 500 dpi
#: and confirmed: this drawing genuinely carries them, and the `lint() == []` that
#: used to stand here was asserting something false.
#:
#: Stored as (crosser, crossed) and matched in that order — a reversed pair is a
#: different defect and must not be filtered as a known one. Naming the pairs rather
#: than the code also keeps the assertion sharp: a *third* crossing on either sheet,
#: or any other lint code, still fails these tests.
_KNOWN_CROSSINGS = (
    ("170", "50 × 120 × 5 DEEP"),
    ("170", "75"),
)


def _is_known(issue):
    return issue.code == "annotation_ink_overlap" and any(
        f"'{crosser}' draws" in issue.message and f"through the label '{crossed}'" in issue.message
        for crosser, crossed in _KNOWN_CROSSINGS
    )


def _lint_apart_from_the_known_crossings(dwg):
    """Every issue except the two crossings this fixture is known to carry."""
    return [issue for issue in dwg.lint() if not _is_known(issue)]


def test_issue_915_hole_callouts_share_one_spacing_solve():
    """Keep the real dense-plan failure distinct from its step-detail follow-up."""
    dwg = build_drawing(
        _ISSUE_915,
        page="A2",
        scale=0.5,
        scale_policy="permissive",
        detail_view=False,
    )

    assert Counter(feature.kind for feature in dwg.model().features) == Counter(
        {
            "fillet": 4,
            "hole": 3,
            "pattern": 2,
            "pocket": 2,
            "envelope": 1,
            "step_level": 1,
        }
    ), "the imported case must retain the inventory that makes its plan callouts compete"

    callout_names = []
    for feature in dwg.model().features:
        if feature.kind not in {"hole", "pattern"}:
            continue
        owned = [name for name in dwg.annotations_of(feature) if name.startswith("hc_")]
        assert len(owned) == 1, f"{feature.kind} must retain one owned defining callout"
        callout_names.extend(owned)
    assert len(set(callout_names)) == 5, "all five hole/pattern definitions must remain placed"

    issues = dwg.lint()
    assert not [
        issue for issue in issues if issue.code in {"annotation_overlap", "callout_dropped"}
    ]

    # Detail escalation is deliberately the next #915 slice. Keep its five missing heights
    # explicit so fixing this independent callout defect cannot make the full case look done.
    step_drops = [issue for issue in issues if issue.code == "step_dim_dropped"]
    assert len(step_drops) == 1
    assert "5 step height(s)" in step_drops[0].message


@pytest.fixture(scope="module", params=["A1", "A2"])
def detail_dwg(request):
    """The A1 and A2 detail builds, once each for the whole module.

    CLAUDE.md: "a critique-style test should share a module-scoped built drawing,
    not mint a new dense fixture." Each build of this sheet costs ~3.1 s and it is
    in the PR-gate tier.
    """
    return request.param, build_drawing(
        _ISSUE_915, page=request.param, scale=0.5, detail_view=True
    )


def test_issue_915_actually_carries_the_known_crossings(detail_dwg):
    """The precondition for the assertions that filter these out.

    Parametrised over both pages because both builds apply the filter: if either
    sheet stopped producing the crossings, `_lint_apart_from_the_known_crossings`
    would filter nothing there and that test would pass while asserting less than
    it claims.
    """
    page, dwg = detail_dwg
    matched = [issue for issue in dwg.lint() if _is_known(issue)]
    assert len(matched) == len(_KNOWN_CROSSINGS), (
        f"the {page} sheet no longer carries both known crossings — the filter in "
        f"this module is now over-broad; matched {[i.message for i in matched]}"
    )


def test_issue_915_wide_detail_uses_a_matching_empty_region():
    """A1 has enough wide, short space even though its largest square is too narrow."""
    dwg = build_drawing(_ISSUE_915, page="A1", scale=0.5, detail_view=True)

    assert "detail_a" in dwg.views
    labels = [
        annotation.label
        for name, annotation in dwg.iter_annotations()
        if name.startswith("dim_detail_a_step")
    ]
    assert labels == ["13", "20", "40", "60", "65"]
    assert _lint_apart_from_the_known_crossings(dwg) == []


def test_issue_915_a2_detail_uses_each_levels_supporting_geometry():
    """A level's own face support, not the envelope edge, defines its witness and crop."""
    dwg = build_drawing(_ISSUE_915, page="A2", scale=0.5, detail_view=True)
    step = next(
        feature for feature in dwg.model().features if isinstance(feature, StepLevelFeature)
    )

    expected_support_rights = [
        (10.0, 135.0),
        (13.0, 121.686),
        (20.0, 105.0),
        (30.0, 170.0),
        (40.0, 170.0),
        (57.0, 121.686),
        (60.0, 135.0),
        (65.0, 120.0),
    ]
    assert [support.level for support in step.level_supports] == pytest.approx(
        [level for level, _ in expected_support_rights], abs=0.001
    )
    assert [support.x_span[1] for support in step.level_supports] == pytest.approx(
        [x for _, x in expected_support_rights], abs=0.001
    )

    plan = compile_dimensions(dwg.model())
    ladder = plan.ladder("step_height")
    assert ladder is not None
    rungs = {rung.final_label: rung for rung in ladder.rungs}
    assert {label: rung.span[1][0] for label, rung in rungs.items()} == pytest.approx(
        {str(int(level)): x for level, x in expected_support_rights}, abs=0.001
    )

    assert "detail_a" in dwg.views
    x0, _, x1, _ = dwg.view_bounds("detail_a")
    detail_world_width = (x1 - x0) / dwg.coords("detail_a")._scale
    assert detail_world_width < dwg.model().bbox.size.X / 2
    detail_dims = {
        annotation.label: annotation
        for name, annotation in dwg.iter_annotations()
        if name.startswith("dim_detail_a_step")
    }
    assert list(detail_dims) == ["13", "20", "40", "60", "65"]
    for label, dimension in detail_dims.items():
        expected = dwg.at("detail_a", *rungs[label].span[1])
        assert dimension._dw_spec.p2[:2] == pytest.approx(expected[:2])
    assert _lint_apart_from_the_known_crossings(dwg) == []
