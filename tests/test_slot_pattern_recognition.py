"""Recognition + emit for the slot-pattern kind (#841 confirmed-behaviour #1, recognise side).

The through-slot analog of `test_pocket_pattern_recognition`: `recognise_slot_patterns` groups
identical milled slots into one `SlotArray`/`SlotGrid`, `build_part_model` emits ONE
`SlotPatternFeature` and excludes the member slots, and `sheet_emit` round-trips it. The payoff
is that a row of identical slots renders ONE grouped ``count× SLOT W × L`` callout, not N
competing per-slot size dims (some of which drop for lack of room — #841 confirmed-behaviour #1).
"""

import dataclasses
from pathlib import Path

from b123d_recognisers import (
    SlotArray,
    SlotGrid,
    recognise_slot_patterns,
    recognise_slots,
)
from build123d import Box, Pos

from draftwright.make_drawing import build_drawing
from draftwright.model.detect import build_part_model


def _slot_row(n=4, pitch=30.0):
    part = Box(60, pitch * (n + 2), 20)
    for i in range(n):  # n identical THROUGH slots on one Y centreline (cutter spans full Z)
        part -= Pos(0, (i - (n - 1) / 2) * pitch, 0) * Box(30, 8, 20)
    return part


def _slot_grid(nx=2, ny=3, px=44.0, py=34.0):
    part = Box(px * (nx + 2), py * (ny + 1), 20)
    for i in range(nx):
        for j in range(ny):
            part -= Pos((i - (nx - 1) / 2) * px, (j - (ny - 1) / 2) * py, 0) * Box(24, 8, 20)
    return part


def test_recognise_linear_slot_array():
    slots = recognise_slots(_slot_row(n=4, pitch=30.0))
    assert len(slots) == 4
    pats = recognise_slot_patterns(slots)
    assert len(pats) == 1
    sa = pats[0]
    assert isinstance(sa, SlotArray)
    assert len(sa.slots) == 4
    assert sa.pitch == 30.0
    assert abs(sa.direction[1]) == 1.0  # runs along Y


def test_recognise_slot_grid():
    pats = recognise_slot_patterns(recognise_slots(_slot_grid(2, 3)))
    assert len(pats) == 1
    sg = pats[0]
    assert isinstance(sg, SlotGrid)
    assert len(sg.slots) == 6
    assert {sg.rows, sg.cols} == {2, 3}


def test_two_slots_are_not_a_pattern():
    assert recognise_slot_patterns(recognise_slots(_slot_row(n=2, pitch=30.0))) == []


def test_different_size_slots_do_not_group():
    part = Box(60, 200, 20)
    part -= Pos(0, -45, 0) * Box(30, 8, 20)
    part -= Pos(0, 0, 0) * Box(30, 12, 20)  # wider
    part -= Pos(0, 45, 0) * Box(40, 8, 20)  # longer
    assert recognise_slot_patterns(recognise_slots(part)) == []


def test_non_coplanar_slots_do_not_merge():
    # identical slots whose in-plane centres line up but which lie on DIFFERENT through planes
    # (staggered d_lo/d_hi) must NOT merge into one array — the spec key includes the through
    # extent (mirrors _pocket_spec_key).
    from b123d_recognisers.slots import Slot

    def sl(cy, d_lo):
        return Slot(
            width_axis="y",
            long_axis="x",
            width=8.0,
            length=30.0,
            w_center=cy,
            lo=-15.0,
            hi=15.0,
            d_lo=d_lo,
            d_hi=d_lo + 20,
        )

    staggered = [sl(-30, 0.0), sl(0, 5.0), sl(30, 10.0)]
    assert recognise_slot_patterns(staggered) == []
    coplanar = [sl(-30, 0.0), sl(0, 0.0), sl(30, 0.0)]
    assert len(recognise_slot_patterns(coplanar)) == 1


def test_build_part_model_groups_and_excludes_members():
    pm = build_part_model(_slot_row(n=4, pitch=30.0))
    kinds = [f.kind for f in pm.features]
    assert kinds.count("slot_pattern") == 1
    assert kinds.count("slot") == 0  # members folded into the pattern, not emitted individually
    pat = next(f for f in pm.features if f.kind == "slot_pattern")
    assert pat.count == 4
    assert pat.member.width == 8.0 and pat.member.length == 30.0


def test_injected_value_equal_pattern_still_excludes_members():
    part = _slot_row(n=4, pitch=30.0)
    slots = recognise_slots(part)
    pats = recognise_slot_patterns(slots)
    copied_slots = [dataclasses.replace(sl) for sl in slots]  # value-equal, new ids
    copied_pats = [
        dataclasses.replace(p, slots=tuple(dataclasses.replace(m) for m in p.slots)) for p in pats
    ]
    pm = build_part_model(part, slots=copied_slots, slot_patterns=copied_pats)
    kinds = [f.kind for f in pm.features]
    assert kinds.count("slot_pattern") == 1
    assert kinds.count("slot") == 0  # value-equal copies still excluded


def test_sheet_emit_round_trips_the_pattern(tmp_path):
    from draftwright.sheet_emit import generate_sheet_script

    py = generate_sheet_script(_slot_row(n=4, pitch=30.0), out=str(tmp_path / "sp_emit_rt"))
    src = Path(py).read_text(encoding="utf-8")  # the script carries non-ASCII (×); Windows
    line = next(ln for ln in src.splitlines() if "sheet.slot_pattern(" in ln)
    assert "members=" not in line  # declare rejects members= — uses at=/pitch=/direction=
    assert 'kind="linear"' in line and "count=4" in line
    assert "at=" in line and "pitch=" in line and "direction=" in line
    assert "depth=" not in line  # a slot has no depth


def test_rectangular_grid_emit_round_trips_faithfully():
    # a RECTANGULAR (rows≠cols, row_pitch≠col_pitch) grid must reconstruct its exact member
    # lattice through the emitted at=/grid=/rows=/cols=/angle= (guards the recognition angle/
    # basis convention against declare._pattern_members' — Codex #852 flagged a possible
    # rows↔cols transpose; this pins that it does NOT transpose).
    from draftwright.model import slot, slot_pattern

    pm = build_part_model(_slot_grid(2, 3))  # 44 mm × 34 mm rectangular lattice
    feat = next(f for f in pm.features if f.kind == "slot_pattern")
    assert feat.rows != feat.cols and feat.grid[0] != feat.grid[1]  # genuinely rectangular
    m = feat.member
    rebuilt = slot_pattern(
        slot(
            width=m.width,
            length=m.length,
            long_axis=m.long_axis,
            width_axis=m.width_axis,
            depth_axis="z",
            lo=m.lo,
            hi=m.hi,
            w_center=m.w_center,
            at=m.frame.origin,
        ),
        kind="grid",
        count=feat.count,
        grid=feat.grid,
        rows=feat.rows,
        cols=feat.cols,
        angle=feat.angle,
        at=feat.frame.origin,
    )
    detected = sorted((round(p[0], 2), round(p[1], 2)) for p in feat.members)
    reconstructed = sorted((round(p[0], 2), round(p[1], 2)) for p in rebuilt.members)
    assert detected == reconstructed


def test_slot_row_renders_one_grouped_callout_not_four():
    # the payoff: a row of four identical slots renders ONE `4× SLOT 8 × 30` (width × length)
    # callout + a pitch dim, not four competing per-slot size dims (#841 confirmed-behaviour #1).
    dwg = build_drawing(_slot_row(n=4, pitch=30.0))
    names = dwg.annotations()
    callouts = [n for n in names if n.startswith("m_slotpat")]
    assert len(callouts) == 1
    assert dwg.get_annotation(callouts[0]).label == "4× SLOT 8 × 30"
    assert not [
        n for n in names if n.startswith("m_slot0")
    ]  # members not individually dimensioned
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]
