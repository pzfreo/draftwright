from collections import Counter
from pathlib import Path

import pytest

from draftwright import build_drawing
from draftwright.model import StepLevelFeature
from draftwright.model.compiled import compile_dimensions

_ISSUE_915 = Path(__file__).parent / "fixtures" / "issue_915_case_study_2.step"


#: The one defect this fixture is known to carry, so that `lint() == []` can stay a
#: real assertion instead of being relaxed to "some issues are fine".
#:
#: `'170'` and `'50 × 120 × 5 DEEP'` share 0.13 mm² of ink: rendered at 600 dpi it is a
#: dimension arrowhead sitting on the `×` of the pocket callout. #1321 measured it, and
#: #1322's first revision set the ink floor at 0.3 mm² specifically to exclude it —
#: which also silenced the check on every STEP fixture in this repository, including a
#: dimension line struck clean through a NIST CTC-02 hole-table row. The floor now sits
#: at the sibling application's 0.05 and this fixture reports its collision.
#:
#: Naming the pair rather than the code keeps the assertion sharp: a *second* ink
#: overlap on this drawing, or any other lint code, still fails these tests.
_KNOWN_INK_OVERLAP = ("annotation_ink_overlap", "'170'", "'50 × 120 × 5 DEEP'")


def _lint_apart_from_the_known_overlap(dwg):
    """Every issue except the one collision this fixture is known to carry."""
    return [
        issue
        for issue in dwg.lint()
        if not (
            issue.code == _KNOWN_INK_OVERLAP[0]
            and all(token in issue.message for token in _KNOWN_INK_OVERLAP[1:])
        )
    ]


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


def test_issue_915_actually_carries_the_known_overlap():
    """The precondition for the two assertions that filter it out.

    If this fixture stopped producing the collision, `_lint_apart_from_the_known_overlap`
    would filter nothing and those tests would pass while asserting less than they claim.
    """
    dwg = build_drawing(_ISSUE_915, page="A1", scale=0.5, detail_view=True)
    matching = [
        issue
        for issue in dwg.lint()
        if issue.code == _KNOWN_INK_OVERLAP[0]
        and all(token in issue.message for token in _KNOWN_INK_OVERLAP[1:])
    ]
    assert len(matching) == 1, (
        "the fixture no longer carries the known ink overlap — the filter in this "
        "module is now vacuous and must be removed"
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
    assert _lint_apart_from_the_known_overlap(dwg) == []


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
    assert _lint_apart_from_the_known_overlap(dwg) == []
