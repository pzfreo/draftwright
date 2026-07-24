"""Grouped blind-pocket arrays — `pocket_pattern` / `Sheet.pocket_pattern` (#841 outcome 1).

The recess analog of the hole `PatternFeature`: N identical blind pockets render as ONE
``count× W × L × D DEEP`` callout + the ``(n-1)× pitch`` dim(s), instead of N competing
per-pocket size dims (some of which drop for lack of room). These tests pin the declare
composition, the grouped render (linear + grid), the input guards, and that the member
pockets are not double-rendered.
"""

import pytest
from build123d import Box

from draftwright.model import pocket, pocket_pattern
from draftwright.sheet import Sheet


def _member():
    # one representative pocket, 7.88 × 13.6 × 19 deep, opening +Z, centred at y=-58
    return pocket(
        width=7.88,
        length=13.6,
        depth=19.0,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        lo=-6.8,
        hi=6.8,
        w_center=-58.0,
        at=(0.0, -58.0, 1.0),
    )


def test_declare_composes_member_and_layout():
    pp = pocket_pattern(_member(), kind="linear", count=5, pitch=27.2, direction=(0, 1, 0))
    assert pp.kind == "pocket_pattern"
    assert pp.count == 5
    assert len(pp.members) == 5
    # the array lies in the opening plane (perpendicular to depth z), so members vary in Y
    ys = sorted(m[1] for m in pp.members)
    assert ys[0] == pytest.approx(-58.0 - 2 * 27.2)
    assert ys[-1] == pytest.approx(-58.0 + 2 * 27.2)
    # parameters = the member's three size params + the array pitch
    roles = [(p.role, p.value) for p in pp.parameters()]
    assert ("pocket_width", 7.88) in roles
    assert ("pocket_depth", 19.0) in roles
    assert ("pitch", 27.2) in roles


def test_linear_pattern_renders_one_grouped_callout_plus_pitch():
    part = Box(26, 161, 21)  # the tuner-jig bar
    s = Sheet(part)
    s.envelope()
    s.pocket_pattern(_member(), kind="linear", count=5, pitch=27.2, direction=(0, 1, 0))
    dwg = s.build()
    names = dwg.annotations()
    callouts = [n for n in names if "pocketpat" in n]
    pitch = [n for n in names if "pitch" in n]
    assert len(callouts) == 1
    assert dwg.get_annotation(callouts[0]).label == "5× 7.9 × 13.6 × 19 DEEP"
    assert len(pitch) == 1
    assert dwg.get_annotation(pitch[0]).label == "4× 27.2"  # (n-1)× pitch
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]
    # the member pockets are composed into the pattern — NOT rendered individually
    assert not [n for n in names if n.startswith("m_pocket_")]


def test_grid_pattern_renders_both_pitch_dims():
    part = Box(120, 90, 12)
    member = pocket(
        width=8.0, length=8.0, depth=4.0, long_axis="x", width_axis="y",
        depth_axis="z", lo=-4.0, hi=4.0, w_center=0.0, at=(0.0, 0.0, 4.0),
    )
    s = Sheet(part)
    s.envelope()
    s.pocket_pattern(member, kind="grid", count=6, grid=(30.0, 40.0), rows=2, cols=3)
    dwg = s.build()
    names = dwg.annotations()
    assert [dwg.get_annotation(n).label for n in names if "pocketpat" in n] == ["6× 8 × 8 × 4 DEEP"]
    pitch_labels = sorted(dwg.get_annotation(n).label for n in names if "pitch" in n)
    assert pitch_labels == ["1× 30", "2× 40"]  # (rows-1)× row_pitch, (cols-1)× col_pitch
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]


def test_bad_inputs_raise():
    m = _member()
    with pytest.raises(ValueError, match="not a known arrangement"):
        pocket_pattern(m, kind="bolt_circle", count=3, pitch=10.0)
    with pytest.raises(ValueError, match="linear.*pitch"):
        pocket_pattern(m, kind="linear", count=3)  # no pitch
    with pytest.raises(ValueError, match="must equal len"):
        pocket_pattern(m, kind="linear", count=3, pitch=10.0, members=[(0, 0, 0), (0, 10, 0)])
    with pytest.raises(ValueError, match="rows.*cols.*count|rows\\*cols"):
        pocket_pattern(m, kind="grid", count=6, grid=(10.0, 10.0), rows=2, cols=2)


def test_model_inspection_sees_the_pattern():
    # the cheap no-render model() path exposes the declared pattern feature
    s = Sheet(Box(26, 161, 21))
    s.pocket_pattern(_member(), kind="linear", count=5, pitch=27.2, direction=(0, 1, 0))
    model = s.model()
    pats = [f for f in model.features if f.kind == "pocket_pattern"]
    assert len(pats) == 1 and pats[0].count == 5
