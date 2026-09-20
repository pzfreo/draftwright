"""Production evidence for feature labels in proven interior whitespace (#1738)."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from draftwright import build_drawing
from draftwright.linting.quality import is_hard_layout_issue

_CTC01_AP203 = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap203.stp"


@pytest.fixture(scope="module", params=("A2", "A3"))
def ctc01_without_pmi(request, tmp_path_factory):
    page = request.param
    trace = tmp_path_factory.mktemp("ctc01-interior") / f"{page}.json"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        drawing = build_drawing(
            _CTC01_AP203,
            page=page,
            scale=0.2,
            scale_policy="permissive",
            pmi="off",
            title="CTC-01",
            number="NIST-01",
            trace=trace,
        )
    event = [
        item
        for item in json.loads(trace.read_text(encoding="utf-8"))["pass_events"]
        if item.get("label") == "feature_leader_inventory"
    ][-1]
    return page, drawing, event


def _selected_region(item):
    selected = next(
        candidate
        for candidate in item["candidate_inventory"]
        if candidate["outcome"] == "selected"
    )
    return selected["region"]


def test_ctc01_feature_families_share_one_interior_solve(ctc01_without_pmi):
    page, drawing, event = ctc01_without_pmi

    assert drawing.scale == pytest.approx(0.2)
    assert (drawing.page_w, drawing.page_h) == {
        "A2": (594.0, 420.0),
        "A3": (420.0, 297.0),
    }[page]
    assert event["assignment"] == "joint"
    assert event["optimal"] is True

    interior = {
        item["name"]
        for item in event["items"]
        if item["outcome"] == "placed" and _selected_region(item) == "interior"
    }
    assert any(name.startswith("hc_") for name in interior)
    assert any(name.startswith("m_chamfer_") for name in interior)
    assert any(name.startswith("m_fillet_") for name in interior)
    assert any(name.startswith("m_blend_") for name in interior)
    assert any(name.startswith("m_polygonal_boss_") for name in interior)

    issues = drawing.lint()
    assert not [issue for issue in issues if is_hard_layout_issue(issue)]
    assert not [issue for issue in issues if issue.code == "leader_crosses_silhouette"]


def test_ctc01_a3_remaining_losses_are_dimensions_not_feature_labels(
    ctc01_without_pmi,
):
    page, drawing, _event = ctc01_without_pmi
    if page != "A3":
        pytest.skip("A3-specific dimension migration boundary")

    codes = [issue.code for issue in drawing.lint()]
    assert not [code for code in codes if code.endswith("callout_dropped")]
    assert codes.count("location_ref_dropped") == 2
    assert codes.count("overall_dim_withheld") == 1
