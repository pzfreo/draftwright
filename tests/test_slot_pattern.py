"""Grouped through-slot arrays — `slot_pattern` / `Sheet.slot_pattern` (#841 behaviour 1).

The through-slot analog of the pocket `PocketPatternFeature`: N identical milled slots render
as ONE ``count× SLOT W × L`` leader + the ``(n-1)× pitch`` dim(s), instead of N competing
per-slot size dims (some of which drop for lack of room — the #841 confirmed-behaviour #1). A
slot has NO depth, so the label carries no ``× D DEEP``. These tests pin the declare
composition, the grouped render (linear + grid), and the input guards.
"""

import pytest
from build123d import Box

from draftwright.model import slot, slot_pattern
from draftwright.sheet import Sheet


def _member():
    # one representative through-Z slot, 8 (y) wide × 20 (x) long, centred at the origin
    return slot(
        width=8.0,
        length=20.0,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        lo=-10.0,
        hi=10.0,
        w_center=0.0,
        at=(0.0, 0.0, 0.0),
    )


def test_declare_composes_member_and_layout():
    sp = slot_pattern(_member(), kind="linear", count=4, pitch=30.0, direction=(0, 1, 0))
    assert sp.kind == "slot_pattern"
    assert sp.count == 4
    assert len(sp.members) == 4
    ys = sorted(m[1] for m in sp.members)  # the array lies in the face plane, varying in Y
    assert ys[-1] - ys[0] == pytest.approx(3 * 30.0)
    roles = [(p.role, p.value) for p in sp.parameters()]
    assert ("slot_width", 8.0) in roles
    assert ("slot_length", 20.0) in roles
    assert ("pitch", 30.0) in roles
    assert not [r for r in roles if r[0] == "slot_depth"]  # a slot has no depth


def test_linear_pattern_renders_one_grouped_callout_plus_pitch():
    s = Sheet(Box(60, 161, 21))
    s.envelope()
    s.slot_pattern(_member(), kind="linear", count=4, pitch=30.0, direction=(0, 1, 0))
    dwg = s.build()
    names = dwg.annotations()
    callouts = [n for n in names if n.startswith("m_slotpat")]
    pitch = [n for n in names if n.startswith("dim_slotpat_pitch")]
    assert len(callouts) == 1
    assert dwg.get_annotation(callouts[0]).label == "4× SLOT 8 × 20"  # no × D DEEP
    assert len(pitch) == 1
    assert dwg.get_annotation(pitch[0]).label == "3× 30"  # (n-1)× pitch
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]
    # the member slots are composed into the pattern — NOT rendered individually
    assert not [n for n in names if n.startswith("m_slot0")]


def test_grid_pattern_renders_both_pitch_dims():
    member = slot(
        width=6.0,
        length=12.0,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        lo=-6.0,
        hi=6.0,
        w_center=0.0,
        at=(0.0, 0.0, 0.0),
    )
    s = Sheet(Box(160, 120, 14))
    s.envelope()
    s.slot_pattern(member, kind="grid", count=6, grid=(30.0, 40.0), rows=2, cols=3)
    dwg = s.build()
    names = dwg.annotations()
    assert [dwg.get_annotation(n).label for n in names if n.startswith("m_slotpat")] == [
        "6× SLOT 6 × 12"
    ]
    pitch_labels = sorted(dwg.get_annotation(n).label for n in names if "slotpat_pitch" in n)
    assert pitch_labels == ["1× 30", "2× 40"]  # (rows-1)× row_pitch, (cols-1)× col_pitch
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]


def test_bad_inputs_raise():
    m = _member()
    with pytest.raises(ValueError, match="not a known arrangement"):
        slot_pattern(m, kind="bolt_circle", count=3, pitch=10.0)
    with pytest.raises(ValueError, match="linear.*pitch"):
        slot_pattern(m, kind="linear", count=3)  # no pitch
    with pytest.raises(ValueError, match="count>=2"):
        slot_pattern(m, kind="linear", count=1, pitch=10.0)
    with pytest.raises(ValueError, match="does not accept explicit members"):
        slot_pattern(m, kind="linear", count=3, pitch=10.0, members=[(0, 0, 0), (0, 10, 0)])
    with pytest.raises(ValueError, match="rows>=2 and cols>=2"):
        slot_pattern(m, kind="grid", count=3, grid=(10.0, 10.0), rows=1, cols=3)
    with pytest.raises(ValueError, match="rows.*cols.*count|rows\\*cols"):
        slot_pattern(m, kind="grid", count=6, grid=(10.0, 10.0), rows=3, cols=3)


def test_direction_through_plane_rejected():
    # the array lies in the face plane (perpendicular to the slot's through axis, here z); a
    # linear direction with a z-through component is physical nonsense and rejected.
    m = _member()  # through axis z
    with pytest.raises(ValueError, match="face plane.*z-through|no z-through"):
        slot_pattern(m, kind="linear", count=3, pitch=10.0, direction=(0, 0, 1))


def test_model_inspection_sees_the_pattern():
    s = Sheet(Box(60, 161, 21))
    s.slot_pattern(_member(), kind="linear", count=4, pitch=30.0, direction=(0, 1, 0))
    model = s.model()
    pats = [f for f in model.features if f.kind == "slot_pattern"]
    assert len(pats) == 1 and pats[0].count == 4
