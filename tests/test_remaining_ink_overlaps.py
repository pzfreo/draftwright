"""One honest exact-ink decision for pitch and corridor dimensions."""

from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Draft

from draftwright import build_drawing
from draftwright._core import Strip
from draftwright.annotations import _common, holes


def _pitch_fixture(monkeypatch, *, ink_clear):
    placed = []
    issues = []
    drawing = SimpleNamespace(
        draft=Draft(),
        box_cache={},
        iter_annotations=lambda: iter(()),
        view_of=lambda _name: None,
        annotations_in_view=lambda _view: (),
    )
    project = SimpleNamespace(
        plan_x=float,
        plan_y=float,
        front_x=float,
        front_z=float,
        side_x=float,
        side_z=float,
    )

    def point(value):
        return SimpleNamespace(X=value, Y=value, Z=value)

    zones = SimpleNamespace(left=None, right=None, above=None, below=None)
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=210.0,
        PAGE_H=297.0,
        SCALE=1.0,
        rv_zones=None,
        bb=SimpleNamespace(min=point(50.0), max=point(130.0)),
        proj=project,
        pv_zones=zones,
        fv_zones=zones,
        sv_zones=zones,
    )
    context = SimpleNamespace(
        place=lambda dim, name, **_kwargs: placed.append((name, dim)),
        record_issue=lambda _severity, code, _message, **_kwargs: issues.append(code),
    )
    monkeypatch.setattr(
        holes,
        "_dim",
        lambda *_args, **_kwargs: SimpleNamespace(
            label_bbox=(20.0, 20.0, 30.0, 24.0),
            segments=(((15.0, 18.0), (35.0, 18.0)),),
        ),
    )
    monkeypatch.setattr(holes, "dim_footprint", lambda *_args, **_kwargs: (20, 20, 30, 24))
    monkeypatch.setattr(holes, "_geom_box", lambda _dim: (20, 20, 30, 24))
    monkeypatch.setattr(holes, "annotation_ink_clear", ink_clear)
    return drawing, analysis, context, placed, issues


def _place_pitch(drawing, analysis, context):
    holes._place_pitch_dim(
        drawing,
        analysis,
        "plan",
        (70.0, 70.0, 0.0),
        (110.0, 110.0, 0.0),
        2,
        "40",
        lambda location: location,
        "test_pitch",
        drop_code="hole_pattern_dim_dropped",
        ctx=context,
    )


def test_pitch_tries_bounded_clear_ink_alternative(monkeypatch):
    checks = []

    def ink_clear(_drawing, _candidate, *, view=None):
        checks.append(view)
        return len(checks) >= 3

    drawing, analysis, context, placed, issues = _pitch_fixture(monkeypatch, ink_clear=ink_clear)
    _place_pitch(drawing, analysis, context)
    assert checks == ["plan", "plan", "plan", None]
    assert [name for name, _dim in placed] == ["test_pitch"]
    assert issues == []


def test_pitch_reports_drop_when_no_ink_clear_alternative(monkeypatch):
    checks = []

    def ink_clear(_drawing, _candidate, *, view=None):
        assert view == "plan"
        checks.append(True)
        return False

    drawing, analysis, context, placed, issues = _pitch_fixture(monkeypatch, ink_clear=ink_clear)
    _place_pitch(drawing, analysis, context)
    assert placed == []
    assert issues == ["hole_pattern_dim_dropped"]
    assert 1 <= len(checks) <= 18  # bounded offsets on both sides


@pytest.mark.parametrize("owner_view", ["plan", "front"])
def test_pitch_does_not_print_through_settled_callout_text(monkeypatch, owner_view):
    drawing, analysis, context, placed, issues = _pitch_fixture(
        monkeypatch, ink_clear=_common.annotation_ink_clear
    )
    callout = SimpleNamespace(
        label_bbox=(20.0, 16.0, 30.0, 19.0),
        segments=(((40.0, 10.0), (50.0, 10.0)),),
    )
    drawing.iter_annotations = lambda: iter((("callout", callout),))
    drawing.view_of = lambda _name: owner_view
    _place_pitch(drawing, analysis, context)
    assert placed == []
    assert issues == ["hole_pattern_dim_dropped"]


@pytest.mark.parametrize("owner_view", ["plan", "front"])
def test_immediate_callout_gate_distinguishes_text_from_shaft_crossing(owner_view):
    fixed = SimpleNamespace(
        label_bbox=(60.0, 60.0, 70.0, 64.0),
        segments=(((15.0, 18.0), (35.0, 18.0)),),
    )
    drawing = SimpleNamespace(
        iter_annotations=lambda: iter((("pitch", fixed),)),
        view_of=lambda _name: owner_view,
    )
    label_hit = SimpleNamespace(
        label_bbox=(20.0, 16.0, 30.0, 20.0),
        segments=(((10.0, 10.0), (10.0, 20.0)),),
    )
    shaft_only = SimpleNamespace(
        label_bbox=(40.0, 30.0, 50.0, 34.0),
        segments=(((25.0, 10.0), (25.0, 25.0)),),
    )
    assert not _common.annotation_text_ink_clear(drawing, label_hit)
    assert _common.annotation_text_ink_clear(drawing, shaft_only)


@pytest.mark.parametrize(
    ("candidate_label", "candidate_segment"),
    [
        ((20.0, 20.0, 30.0, 24.0), ((50.0, 50.0), (60.0, 50.0))),
        ((50.0, 50.0, 60.0, 54.0), ((15.0, 22.0), (35.0, 22.0))),
    ],
)
def test_immediate_callout_rejects_cross_view_label_or_shaft_on_text(
    candidate_label, candidate_segment
):
    fixed = SimpleNamespace(
        label_bbox=(20.0, 20.0, 30.0, 24.0),
        segments=(((70.0, 70.0), (80.0, 70.0)),),
    )
    drawing = SimpleNamespace(
        iter_annotations=lambda: iter((("foreign_view", fixed),)),
        view_of=lambda _name: "front",
    )
    candidate = SimpleNamespace(label_bbox=candidate_label, segments=(candidate_segment,))

    assert not _common.annotation_text_ink_clear(drawing, candidate)


def test_immediate_callout_fails_closed_on_unreadable_ink_metadata():
    class Unreadable:
        @property
        def label_bbox(self):
            raise ValueError("no label geometry")

    clear = SimpleNamespace(
        label_bbox=(50.0, 50.0, 60.0, 54.0),
        segments=(((45.0, 45.0), (55.0, 45.0)),),
    )
    drawing = SimpleNamespace(iter_annotations=lambda: iter((("fixed", Unreadable()),)))

    assert not _common.annotation_text_ink_clear(drawing, clear)
    assert not _common.annotation_text_ink_clear(drawing, Unreadable())


def test_dense_hole_callout_reports_text_collision_instead_of_placing(monkeypatch):
    monkeypatch.setattr(holes, "_TABULATE_MIN_HOLES", 1)
    monkeypatch.setattr(holes, "annotation_text_ink_clear", lambda _drawing, _leader: False)
    drawing = build_drawing(Box(60, 40, 10) - Cylinder(4, 10))
    assert any(
        issue.code == "callout_dropped" and "settled annotation ink" in issue.message
        for issue in drawing.lint()
    )


def test_required_dimension_ink_conflict_uses_normal_drop_path(monkeypatch):
    class Drawing:
        page_w = 100.0
        page_h = 100.0
        drawable_bounds = (0.0, 0.0, 100.0, 100.0)

        def __init__(self):
            self.added = []

        def iter_annotations(self):
            return self.added

        def view_of(self, _name):
            return "plan"

        def place(self, item, name, **_kwargs):
            self.added.append((name, item))

    class Ink:
        def __init__(self, pos):
            self.label_bbox = (20.0, pos, 30.0, pos + 5.0)
            self.segments = (((25.0, 0.0), (25.0, pos)),)

        def bounding_box(self):
            return SimpleNamespace(
                min=SimpleNamespace(X=20.0, Y=0.0),
                max=SimpleNamespace(X=30.0, Y=self.label_bbox[3]),
            )

    monkeypatch.setattr(_common, "strip_obstacles", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(_common, "pending_title_block_box", lambda _drawing: None)
    drawing = Drawing()
    remaining = _common.place_strip_candidates(
        drawing,
        Strip(anchor=0.0, outer_limit=50.0, direction=1.0, gap=8.0, spacing=3.0),
        "plan",
        "y",
        [("first", Ink), ("second", Ink)],
        5.0,
        force=True,
        ctx=drawing,
        require_clear_ink={"first", "second"},
    )
    assert len(drawing.added) == 1
    assert len(remaining) == 1
    assert {drawing.added[0][0], remaining[0][0]} == {"first", "second"}
