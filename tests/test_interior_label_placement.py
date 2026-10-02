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
from draftwright.registry import DimensionPlacementSpec, PlacedDimension

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


def test_ctc01_feature_families_share_one_joint_assignment(ctc01_without_pmi):
    page, drawing, event = ctc01_without_pmi

    assert drawing.scale == pytest.approx(0.2)
    assert (drawing.page_w, drawing.page_h) == {
        "A2": (594.0, 420.0),
        "A3": (420.0, 297.0),
    }[page]
    assert event["assignment"] == "joint"
    assert event["optimal"] is True

    placed = {item["name"] for item in event["items"] if item["outcome"] == "placed"}
    for prefix in ("hc_", "m_chamfer_", "m_fillet_", "m_blend_", "m_polygonal_boss_"):
        assert any(name.startswith(prefix) for name in placed)
    # Which family uses interior whitespace may change as other sheet ink changes;
    # the invariant is that all families join one assignment and at least one
    # chooses a proven interior region without losing required annotations.
    assert any(
        _selected_region(item) == "interior"
        for item in event["items"]
        if item["outcome"] == "placed"
    )

    issues = drawing.lint()
    assert not [issue for issue in issues if is_hard_layout_issue(issue)]
    assert not [issue for issue in issues if issue.code == "leader_crosses_silhouette"]


def test_ctc01_a3_keeps_required_dimensions_when_exterior_space_is_available(
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
    # These dimensions previously needed an interior retry. A clearer exterior
    # arrangement is equally valid; their semantic survival is what matters.


def test_interior_dimension_retry_preserves_the_original_honest_drop(monkeypatch, tmp_path):
    """Permission to try the interior does not make an infeasible mark disappear."""
    dropped = []
    specimen = object.__new__(PlacedDimension)
    specimen.placement_spec = DimensionPlacementSpec(
        p1=(10.0, 10.0),
        p2=(30.0, 10.0),
        side="above",
        distance=4.0,
        draft=object(),
        kwargs={},
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
    ctx.trace = _common.SolveTrace(tmp_path / "interior-drop.json")
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
    [item] = ctx.trace.pass_events[-1]["items"]
    assert item["outcome"] == "dropped"
    assert item["reason"] == "no_clear_candidate"
    assert "projected_view_ink" in item["rejections"]
    assert item["candidate_inventory"] == []


@pytest.mark.parametrize(
    ("invalid", "reason"),
    [("raises", "candidate_rebuild_failed"), ("moves", "candidate_changed_during_commit")],
)
def test_interior_trace_records_failed_commit_without_claiming_placement(
    monkeypatch, tmp_path, invalid, reason
):
    def analytical(position):
        return SimpleNamespace(
            label_bbox=(10.0, position - 1.0, 20.0, position + 1.0),
            box=(9.0, position - 2.0, 21.0, position + 2.0),
        )

    def built(position):
        if invalid == "raises":
            raise ValueError("simulated renderer failure")
        return SimpleNamespace(label_bbox=(10.0, 60.0, 20.0, 62.0), box=(9, 59, 21, 63))

    dropped = []
    ctx = _common.PlacementContext(
        interior_dimensions=[
            _common.InteriorDimensionJob(
                name="changed",
                view="front",
                side="above",
                build=built,
                on_place=lambda _name: pytest.fail("invalid rebuilt ink was placed"),
                on_drop=dropped.append,
                lane_step=5.0,
                interior_build=built,
                analytical_geometry=analytical,
            )
        ]
    )
    ctx.trace = _common.SolveTrace(tmp_path / "interior-failed-commit.json")
    drawing = SimpleNamespace(view_bounds=lambda _view: (0.0, 0.0, 40.0, 40.0))
    monkeypatch.setattr(_common, "_drawing_bounds", lambda _drawing: (0.0, 0.0, 50.0, 50.0))
    monkeypatch.setattr(_common, "view_label_clearance", lambda *_args: lambda _box: True)
    monkeypatch.setattr(_common, "annotation_ink_clear", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(_common, "_geom_box", lambda annotation: annotation.box)

    _common._drain_interior_dimensions(ctx, drawing)

    assert dropped == ["changed"]
    [item] = ctx.trace.pass_events[-1]["items"]
    assert item["outcome"] == "dropped" and item["reason"] == reason
    assert reason in item["rejections"]
    assert any(
        candidate["outcome"] == "failed_commit" for candidate in item["candidate_inventory"]
    )


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


def test_declared_feature_relative_lanes_share_assignment_and_keep_region_provenance(
    monkeypatch,
    tmp_path,
):
    """One semantic lane may resolve inside or outside without accepting a coordinate."""

    placed = []

    def dimension(position):
        return SimpleNamespace(
            label_bbox=(position - 2.0, 15.0, position + 2.0, 18.0),
            box=(position - 3.0, 10.0, position + 3.0, 22.0),
        )

    jobs = [
        _common.InteriorDimensionJob(
            name=name,
            view="front",
            side="right",
            build=dimension,
            on_place=lambda _name: None,
            on_drop=lambda _name: pytest.fail("a clear declared lane was dropped"),
            lane_step=5.0,
            interior_build=dimension,
            explicit_position=position,
            requested_lane=lane,
        )
        for name, position, lane in (("inside", 20.0, 3), ("outside", 45.0, 4))
    ]
    ctx = _common.PlacementContext(interior_dimensions=jobs)
    ctx.trace = _common.SolveTrace(tmp_path / "interior-placed.json")
    ctx.place = lambda _annotation, name, **kwargs: placed.append(
        (name, kwargs["candidate_region"])
    )
    drawing = SimpleNamespace(view_bounds=lambda _view: (0.0, 0.0, 40.0, 40.0))

    monkeypatch.setattr(_common, "_drawing_bounds", lambda _drawing: (0.0, 0.0, 50.0, 50.0))
    monkeypatch.setattr(_common, "view_label_clearance", lambda *_args: lambda _box: True)
    monkeypatch.setattr(_common, "annotation_ink_clear", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(_common, "_geom_box", lambda annotation: annotation.box)

    _common._drain_interior_dimensions(ctx, drawing)

    assert placed == [("inside", "interior"), ("outside", "exterior")]
    assert [item["outcome"] for item in ctx.trace.pass_events[-1]["items"]] == [
        "placed",
        "placed",
    ]
    assert all(
        any(candidate["outcome"] == "selected" for candidate in item["candidate_inventory"])
        for item in ctx.trace.pass_events[-1]["items"]
    )


def test_declared_lane_that_straddles_the_view_boundary_fails_closed(monkeypatch):
    dropped = []
    rejection_reasons = []

    def dimension(position):
        return SimpleNamespace(
            label_bbox=(position - 2.0, 15.0, position + 2.0, 18.0),
            box=(position - 3.0, 10.0, position + 3.0, 22.0),
        )

    ctx = _common.PlacementContext(
        interior_dimensions=[
            _common.InteriorDimensionJob(
                name="straddled",
                view="front",
                side="right",
                build=dimension,
                on_place=lambda _name: pytest.fail("a boundary-straddling lane was placed"),
                on_drop=dropped.append,
                lane_step=5.0,
                interior_build=dimension,
                explicit_position=39.0,
                requested_lane=8,
                rejection_reasons=rejection_reasons,
            )
        ]
    )
    ctx.place = lambda _annotation, _name, **_kwargs: pytest.fail(
        "a boundary-straddling lane reached the placement boundary"
    )
    drawing = SimpleNamespace(view_bounds=lambda _view: (0.0, 0.0, 40.0, 40.0))

    monkeypatch.setattr(_common, "_drawing_bounds", lambda _drawing: (0.0, 0.0, 50.0, 50.0))
    monkeypatch.setattr(_common, "view_label_clearance", lambda *_args: lambda _box: True)
    monkeypatch.setattr(_common, "annotation_ink_clear", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(_common, "_geom_box", lambda annotation: annotation.box)

    _common._drain_interior_dimensions(ctx, drawing)

    assert dropped == ["straddled"]
    assert rejection_reasons == ["view_boundary_straddle"]
