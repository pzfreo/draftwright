"""Grouped blind-pocket arrays — `pocket_pattern` / `Sheet.pocket_pattern` (#841 outcome 1).

The recess analog of the hole `PatternFeature`: N identical blind pockets render as ONE
``count× W × L × D DEEP`` callout + the ``(n-1)× pitch`` dim(s), instead of N competing
per-pocket size dims (some of which drop for lack of room). These tests pin the declare
composition, the grouped render (linear + grid), the input guards, and that the member
pockets are not double-rendered.
"""

import pytest
from build123d import Box

from draftwright.model import hole, pocket, pocket_pattern
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
    callouts = [n for n in names if n.startswith("m_pocketpat")]
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
        width=8.0,
        length=8.0,
        depth=4.0,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        lo=-4.0,
        hi=4.0,
        w_center=0.0,
        at=(0.0, 0.0, 4.0),
    )
    s = Sheet(part)
    s.envelope()
    s.pocket_pattern(member, kind="grid", count=6, grid=(30.0, 40.0), rows=2, cols=3)
    dwg = s.build()
    names = dwg.annotations()
    assert [dwg.get_annotation(n).label for n in names if n.startswith("m_pocketpat")] == [
        "6× 8 × 8 × 4 DEEP"
    ]
    pitch_labels = sorted(dwg.get_annotation(n).label for n in names if "pitch" in n)
    assert pitch_labels == ["1× 30", "2× 40"]  # (rows-1)× row_pitch, (cols-1)× col_pitch
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]


def test_bad_inputs_raise():
    m = _member()
    with pytest.raises(ValueError, match="not a known arrangement"):
        pocket_pattern(m, kind="bolt_circle", count=3, pitch=10.0)
    with pytest.raises(ValueError, match="linear.*pitch"):
        pocket_pattern(m, kind="linear", count=3)  # no pitch
    with pytest.raises(ValueError, match="rows.*cols.*count|rows\\*cols"):
        pocket_pattern(m, kind="grid", count=6, grid=(10.0, 10.0), rows=3, cols=3)


def test_out_of_plane_direction_rejected():
    # the array lies in the OPENING plane (perpendicular to the depth axis, here z), so a
    # linear direction with a z-component would march the members into the material while the
    # grouped callout still claimed an in-plane array — physical nonsense, rejected (Codex #848).
    m = _member()  # depth_axis z, centred at y=-58
    with pytest.raises(ValueError, match="opening plane.*z-depth|no z-depth"):
        pocket_pattern(m, kind="linear", count=3, pitch=10.0, direction=(0, 0, 1))


def test_members_override_rejected_for_both_kinds():
    # a DECLARED pattern is computed from count + pitch/grid + layout; explicit members= could
    # contradict the grouped size/pitch labels without full lattice/pitch/centroid validation,
    # so it is rejected outright for BOTH linear and grid (Codex #848 r2/r3). The detector
    # builds the IR dataclass directly with real geometry, bypassing this constructor.
    m = _member()
    with pytest.raises(ValueError, match="does not accept explicit members"):
        pocket_pattern(m, kind="linear", count=3, pitch=27.2, members=[(0, -58, 1), (0, -30, 1)])
    with pytest.raises(ValueError, match="does not accept explicit members"):
        pocket_pattern(
            m, kind="grid", count=4, grid=(10.0, 10.0), rows=2, cols=2, members=[(0, 0, 0)]
        )


def test_grid_needs_two_by_two():
    # a single-row/column grid has only one populated lattice axis, so its pitch dim would be
    # silently dropped — such an array IS linear (Codex #848 r3). Reject rows<2 or cols<2.
    m = _member()
    with pytest.raises(ValueError, match="rows>=2 and cols>=2"):
        pocket_pattern(m, kind="grid", count=3, grid=(10.0, 10.0), rows=1, cols=3)
    with pytest.raises(ValueError, match="rows>=2 and cols>=2"):
        pocket_pattern(m, kind="grid", count=3, grid=(10.0, 10.0), rows=3, cols=1)


def test_pitch_dim_names_do_not_collide_with_hole_pattern():
    # a plan-view hole pattern and pocket pattern both index from 0, so both once produced
    # `dim_pitch_plan0` — the second silently overwrote the first. Distinct prefixes now let
    # both pitch dims survive on one sheet (Codex #848 r2).
    part = Box(140, 120, 14)
    s = Sheet(part)
    s.envelope()
    s.pattern(  # hole pattern, +Y half, plan view (z holes)
        hole(diameter=6.0, at=(0.0, 35.0, 0.0), axis="z"),
        kind="linear",
        count=3,
        pitch=25.0,
        direction=(1, 0, 0),
    )
    s.pocket_pattern(  # pocket pattern, -Y half, plan view (z-depth pockets)
        pocket(
            width=8.0,
            length=8.0,
            depth=4.0,
            long_axis="x",
            width_axis="y",
            depth_axis="z",
            lo=-4.0,
            hi=4.0,
            w_center=-35.0,
            at=(0.0, -35.0, 7.0),
        ),
        kind="linear",
        count=3,
        pitch=25.0,
        direction=(1, 0, 0),
    )
    dwg = s.build()
    names = dwg.annotations()
    hole_pitch = [n for n in names if n.startswith("dim_pitch_")]
    pocket_pitch = [n for n in names if n.startswith("dim_pocketpat_pitch_")]
    assert hole_pitch, "hole-pattern pitch dim was overwritten"
    assert pocket_pitch, "pocket-pattern pitch dim was overwritten"
    assert set(hole_pitch).isdisjoint(pocket_pitch)


def test_manual_callout_verb_raises_clearly():
    # the manual dwg.callout() edit verb for a pocket pattern is a deferred #841 follow-up;
    # it must raise a clear error, NOT fall through to the hole-callout path and crash.
    s = Sheet(Box(26, 161, 21))
    s.envelope()
    s.pocket_pattern(_member(), kind="linear", count=5, pitch=27.2, direction=(0, 1, 0))
    dwg = s.build()
    feat = next(f for f in dwg.model().features if f.kind == "pocket_pattern")
    with pytest.raises(ValueError, match="placed automatically at build time.*#841"):
        dwg.callout(feat)


def test_model_inspection_sees_the_pattern():
    # the cheap no-render model() path exposes the declared pattern feature
    s = Sheet(Box(26, 161, 21))
    s.pocket_pattern(_member(), kind="linear", count=5, pitch=27.2, direction=(0, 1, 0))
    model = s.model()
    pats = [f for f in model.features if f.kind == "pocket_pattern"]
    assert len(pats) == 1 and pats[0].count == 5
