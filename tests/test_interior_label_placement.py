"""Production evidence for feature labels in proven interior whitespace (#1738)."""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

from draftwright import build_drawing
from draftwright.annotations import _common
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


def test_ctc01_a3_recovers_required_dimensions_in_proven_interior_whitespace(
    ctc01_without_pmi,
):
    page, drawing, _event = ctc01_without_pmi
    if page != "A3":
        pytest.skip("A3-specific dimension migration boundary")

    codes = [issue.code for issue in drawing.lint()]
    assert not [code for code in codes if code.endswith("callout_dropped")]
    assert "location_ref_dropped" not in codes
    assert "overall_dim_withheld" not in codes
    assert "feature_not_located" not in codes
    assert {"m_locy0", "m_locy1", "m_env_width"} <= set(drawing.annotations())
    assert getattr(drawing.get_annotation("m_locy0"), "_dw_candidate_region", None) == "interior"
    assert (
        getattr(drawing.get_annotation("m_env_width"), "_dw_candidate_region", None) == "interior"
    )


def test_interior_dimension_retry_preserves_the_original_honest_drop(monkeypatch):
    """Permission to try the interior does not make an infeasible mark disappear."""
    dropped = []
    specimen = SimpleNamespace(
        _dw_spec=SimpleNamespace(
            p1=(10.0, 10.0),
            p2=(30.0, 10.0),
            draft=object(),
            kwargs={},
        )
    )
    ctx = _common.PlacementContext(
        interior_dimensions=[
            _common.InteriorDimensionJob(
                name="blocked",
                view="front",
                side="above",
                build=lambda _position: specimen,
                on_place=lambda _name: pytest.fail("an infeasible candidate was placed"),
                on_drop=dropped.append,
                lane_step=4.0,
            )
        ]
    )
    drawing = SimpleNamespace(view_bounds=lambda _view: (0.0, 0.0, 40.0, 40.0))

    monkeypatch.setattr(_common, "_drawing_bounds", lambda _drawing: (0.0, 0.0, 50.0, 50.0))
    monkeypatch.setattr(
        _common,
        "view_label_clearance",
        lambda _drawing, _view: lambda _box: False,
    )
    monkeypatch.setattr(
        _common,
        "_dim",
        lambda *_args, **_kwargs: SimpleNamespace(label_bbox=(12.0, 12.0, 20.0, 16.0)),
    )
    monkeypatch.setattr(_common, "_geom_box", lambda _annotation: (10.0, 10.0, 30.0, 20.0))

    _common._drain_interior_dimensions(ctx, drawing)

    assert dropped == ["blocked"]
    assert ctx.interior_dimensions == []


def test_interior_dimension_retry_checks_sheet_wide_fixed_ink(monkeypatch):
    """An adjacent view's page-space ink remains a hard interior blocker."""
    dropped = []
    checked_views = []
    ctx = _common.PlacementContext(
        interior_dimensions=[
            _common.InteriorDimensionJob(
                name="cross_view_blocked",
                view="front",
                side="above",
                build=lambda _position: None,
                on_place=lambda _name: pytest.fail("cross-view fixed ink was ignored"),
                on_drop=dropped.append,
                lane_step=4.0,
                interior_build=lambda _position: None,
                analytical_geometry=lambda position: _common.AnalyticalDimensionInk(
                    label_bbox=(10.0, position - 1.0, 20.0, position + 1.0),
                    segments=(((10.0, position), (20.0, position)),),
                    box=(9.0, position - 2.0, 21.0, position + 2.0),
                ),
            )
        ]
    )
    drawing = SimpleNamespace(view_bounds=lambda _view: (0.0, 0.0, 40.0, 40.0))

    monkeypatch.setattr(_common, "_drawing_bounds", lambda _drawing: (0.0, 0.0, 50.0, 50.0))
    monkeypatch.setattr(
        _common,
        "view_label_clearance",
        lambda _drawing, _view: lambda _box: True,
    )

    def blocked_by_sheet_ink(_drawing, _candidate, *, view=None, additional=()):
        checked_views.append(view)
        return False

    monkeypatch.setattr(_common, "annotation_ink_clear", blocked_by_sheet_ink)

    _common._drain_interior_dimensions(ctx, drawing)

    assert dropped == ["cross_view_blocked"]
    assert checked_views and set(checked_views) == {None}
