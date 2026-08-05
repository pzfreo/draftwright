from collections import Counter
from pathlib import Path

from draftwright import build_drawing

_ISSUE_915 = Path(__file__).parent / "fixtures" / "issue_915_case_study_2.step"


def test_issue_915_hole_callouts_share_one_spacing_solve():
    """Keep the real dense-plan failure distinct from its step-detail follow-up."""
    dwg = build_drawing(_ISSUE_915, page="A2", scale=0.5)

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
