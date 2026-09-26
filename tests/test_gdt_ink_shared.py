"""Exact same-batch ink decisions for a GD&T frame and dimension (#1862)."""

from types import SimpleNamespace

from build123d_drafting import FeatureControlFrame, Leader
from build123d_drafting.helpers import Draft

from draftwright._core import Strip
from draftwright.annotations import _common


class _Ink:
    def __init__(self, pos, box, label_bbox, segments, *, dimension=False):
        self.pos = pos
        self.box = box
        self.label_bbox = label_bbox
        self.segments = segments
        self._dw_dimension_candidate = dimension

    def bounding_box(self):
        x0, y0, x1, y1 = self.box
        return SimpleNamespace(
            min=SimpleNamespace(X=x0, Y=y0),
            max=SimpleNamespace(X=x1, Y=y1),
        )


class _Drawing:
    page_w = 100.0
    page_h = 100.0
    drawable_bounds = (0.0, 0.0, 100.0, 100.0)
    trace = None
    annotation_lanes = None
    part_model = None
    interior_dimensions = None
    exterior_dimensions_only = False
    registry = None

    def __init__(self):
        self.added = []

    def iter_annotations(self):
        return list(self.added)

    def view_of(self, _name):
        return "plan"

    def place(self, item, name, view=None, feature=None, measurement=None):
        self.added.append((name, item))


def _dimension(pos):
    # The horizontal dimension stroke crosses the frame's natural glyph, not
    # its own distant label. Moving the frame outward leaves a permissible
    # dimension/leader shaft crossing but restores the glyph's legibility.
    return _Ink(
        pos,
        (20.0, pos, 60.0, pos + 10.0),
        (50.0, pos, 60.0, pos + 5.0),
        (((20.0, pos + 10.0), (30.0, pos + 10.0)),),
        dimension=True,
    )


def _frame(pos):
    return _Ink(
        pos,
        (20.0, 0.0, 30.0, pos + 5.0),
        (20.0, pos, 30.0, pos + 5.0),
        (((25.0, 0.0), (25.0, pos)),),
    )


def _solve(
    monkeypatch,
    *,
    repair,
    required=True,
    committed=(),
    outer_limit=50.0,
    dimension_priority=0.0,
):
    monkeypatch.setattr(_common, "strip_obstacles", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(_common, "pending_title_block_box", lambda _drawing: None)
    drawing = _Drawing()
    drawing.added.extend(committed)
    strip = Strip(anchor=0.0, outer_limit=outer_limit, direction=1.0, gap=8.0, spacing=3.0)
    remaining = _common.place_strip_candidates(
        drawing,
        strip,
        "plan",
        "y",
        [("dimension", _dimension), ("frame", _frame)],
        tier=5.0,
        force=True,
        ctx=drawing,
        sizes={"frame": (10.0, 5.0)},
        priorities={
            "frame": _common.PRIORITY.AUTHORED,
            "dimension": dimension_priority,
        },
        ink_repair_candidates=(
            {"frame": lambda original: (_frame(original.pos + 8.0),)} if repair else {}
        ),
        require_clear_ink={"frame"} if required else (),
    )
    return drawing, remaining


def test_frame_moves_as_whole_when_same_batch_dimension_crosses_its_glyph(monkeypatch):
    unchecked, unchecked_remaining = _solve(monkeypatch, repair=False, required=False)
    assert unchecked_remaining == [], [(name, item.pos) for name, item in unchecked.added]
    assert dict((name, item.pos) for name, item in unchecked.added) == {
        "dimension": 8.0,
        "frame": 16.0,
    }
    assert _common.annotation_ink_clear(_Drawing(), _frame(24.0), additional=(_dimension(8.0),))
    drawing, remaining = _solve(monkeypatch, repair=True)
    assert remaining == [], ([(name, item.pos) for name, item in drawing.added], remaining)
    placed = dict(drawing.added)
    assert placed["frame"].pos == placed["dimension"].pos + 16.0
    assert _common.annotation_ink_clear(
        _Drawing(), placed["frame"], additional=(placed["dimension"],)
    )


def test_real_frame_glyph_and_leader_use_the_same_exact_ink_predicate():
    draft = Draft(font_size=3.0)

    def frame(elbow_y):
        glyph = FeatureControlFrame("position", "0.1", datums=("A",), draft=draft)
        return Leader(
            tip=(30.0, 20.0), elbow=(30.0, elbow_y), label="", draft=draft, callout=glyph
        )

    dimension = _Ink(
        40.0,
        (35.0, 40.0, 70.0, 45.0),
        (60.0, 40.0, 70.0, 45.0),
        (((35.0, 40.0), (45.0, 40.0)),),
        dimension=True,
    )
    natural, moved = frame(40.0), frame(55.0)
    assert natural.label_bbox[0] < 45.0 < natural.label_bbox[2]
    assert not _common.annotation_ink_clear(_Drawing(), natural, additional=(dimension,))
    assert _common.annotation_ink_clear(_Drawing(), moved, additional=(dimension,))


def test_conflicting_auto_dimension_yields_to_authored_frame(monkeypatch):
    drawing, remaining = _solve(monkeypatch, repair=False)
    assert [name for name, _item in drawing.added] == ["frame"]
    assert [name for name, _build in remaining] == ["dimension"]


def test_mandatory_dimension_does_not_yield_to_frame(monkeypatch):
    drawing, remaining = _solve(
        monkeypatch, repair=False, dimension_priority=_common.PRIORITY.MANDATORY
    )
    assert [name for name, _item in drawing.added] == ["dimension"]
    assert [name for name, _build in remaining] == ["frame"]


def test_frame_leader_shaft_conflict_cannot_be_hidden_by_outward_repair(monkeypatch):
    leader = _Ink(
        12.0,
        (20.0, 12.0, 60.0, 17.0),
        (50.0, 12.0, 60.0, 17.0),
        (((20.0, 12.0), (30.0, 12.0)),),
    )
    drawing, remaining = _solve(monkeypatch, repair=True, committed=(("leader", leader),))
    assert [name for name, _item in drawing.added] == ["leader", "dimension"]
    assert [name for name, _build in remaining] == ["frame"]


def test_frame_frame_ink_conflict_returns_to_relocation_path(monkeypatch):
    other_frame = _Ink(
        16.0,
        (20.0, 0.0, 30.0, 21.0),
        (20.0, 16.0, 30.0, 21.0),
        (((25.0, 0.0), (25.0, 16.0)),),
    )
    drawing, remaining = _solve(
        monkeypatch, repair=True, committed=(("other_frame", other_frame),)
    )
    assert [name for name, _item in drawing.added] == ["other_frame", "dimension"]
    assert [name for name, _build in remaining] == ["frame"]


def test_authored_frame_survives_over_capacity_before_auto_dimension(monkeypatch):
    drawing, remaining = _solve(monkeypatch, repair=False, outer_limit=18.0)
    assert [name for name, _item in drawing.added] == ["frame"]
    assert [name for name, _build in remaining] == ["dimension"]


def test_force_pass_cannot_restore_dimension_displaced_by_frame_ink(monkeypatch):
    monkeypatch.setattr(_common, "strip_obstacles", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(_common, "pending_title_block_box", lambda _drawing: None)
    drawing = _Drawing()
    drawing.post_drain = []
    dropped = []
    strip = Strip(anchor=0.0, outer_limit=50.0, direction=1.0, gap=8.0, spacing=3.0)
    candidates = [
        _common.CorridorCandidate(
            name="dimension",
            build=_dimension,
            order=(0, 0),
            on_place=lambda _name: None,
            on_drop=dropped.append,
            force=True,
        ),
        _common.CorridorCandidate(
            name="frame",
            build=_frame,
            order=(1, 0),
            on_place=lambda _name: None,
            on_drop=dropped.append,
            priority=_common.PRIORITY.AUTHORED,
            force=True,
            size=(10.0, 5.0),
            require_clear_ink=True,
        ),
    ]

    _common.solve_corridor(drawing, strip, "plan", "y", candidates, 5.0, ctx=drawing)

    assert [name for name, _item in drawing.added] == ["frame"]
    assert dropped == ["dimension"]
