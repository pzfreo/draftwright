from collections import Counter
from pathlib import Path

import pytest

from draftwright import build_drawing
from draftwright.model import StepLevelFeature
from draftwright.model.compiled import compile_dimensions

_ISSUE_915 = Path(__file__).parent / "fixtures" / "issue_915_case_study_2.step"


def test_issue_915_hole_callouts_share_one_spacing_solve():
    """Keep the real dense-plan failure distinct from its step-detail follow-up."""
    dwg = build_drawing(_ISSUE_915, page="A2", scale=0.5, detail_view=False)

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
    assert dwg.lint() == []


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
    assert dwg.lint() == []
