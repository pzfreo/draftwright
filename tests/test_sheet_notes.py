"""Sheet anchored manufacturing notes — `sheet.note()` / `.note()` (ADR 0011 P2c, #488).

A free-text leader note is the shop callout detection can't infer: thread specs (`M3x0.5 TAP`),
`DEBURR`, chip-relief, knurl. It reuses the P2b GD&T corridor machinery whole — a note is a
GD&T-kind IR item whose glyph is a single-line `TextBlock`, placed as a first-class ADR 0009
corridor candidate (NOT the dimension planner). These tests pin the target derivation
(view/side/site/origin), that a placed note is lint-clean, and provenance re-binding.
"""

import pytest
from build123d import Box, Cylinder, Pos

from draftwright.model import note as _declare_note
from draftwright.sheet import Sheet


def _part():
    return Box(80, 50, 20) - Pos(0, 0, 0) * Cylinder(6, 20)


def _top_face(part):
    return part.faces().sort_by()[-1]  # +Z top


def test_note_on_feature_derives_face_on_view():
    part = _part()
    s = Sheet(part)
    s.hole(Pos(0, 0, 0) * Cylinder(6, 20)).note("M3x0.5 TAP")
    nt = next(f for f in s.features if f.kind == "note")
    assert nt.text == "M3x0.5 TAP"
    assert nt.view == "plan"  # a z-axis feature is face-on in plan
    assert nt.side == "above"  # the plan's roomy strip (below carries the width envelope)
    assert nt.frame.origin == (0.0, 0.0, 0.0)
    assert nt.origin is s.features[0]  # provenance → the hole feature (note is features[1])


def test_note_on_planar_face_derives_edge_on_view():
    part = _part()
    s = Sheet(part)
    s.note("DEBURR", _top_face(part))
    nt = next(f for f in s.features if f.kind == "note")
    assert nt.text == "DEBURR"
    assert nt.view == "front"  # a +Z face shows edge-on in front
    assert nt.side == "above"  # face sits above the part centre (z=10 > 0)
    assert nt.frame.axis == "z"
    assert nt.origin is None  # a bare face has no source feature


def test_dim_handle_note():
    part = _part()
    s = Sheet(part)
    s.diameter(Pos(30, 0, 0) * Cylinder(8, 20)).note("KNURL 0.8 STRAIGHT")
    nt = next(f for f in s.features if f.kind == "note")
    assert nt.text == "KNURL 0.8 STRAIGHT" and nt.view == "plan"


def test_slot_and_pocket_handle_note():
    # #841: the multi-param handle (slot/pocket/envelope) grew an explicit .note(), so
    # `sheet.slot(...).note("...")` works like a hole/dim handle instead of forwarding to the
    # bare Sheet.note and raising "missing 'ref'".
    from build123d import Box, Plane, SlotOverall, extrude

    part = Box(60, 30, 12) - extrude(Plane.XY * SlotOverall(20, 8), 12, both=True)
    s = Sheet(part)
    h = s.slot(width=8, length=20, long_axis="x", width_axis="y", lo=-10, hi=10, w_center=0)
    assert h.note("5X OBROUND SLOT") is h  # explicit method, chainable — no TypeError
    nt = next(f for f in s.features if f.kind == "note")
    assert nt.text == "5X OBROUND SLOT"

    p = Box(60, 30, 20) - Pos(0, 0, 4) * extrude(Plane.XY * SlotOverall(20, 8), 12)
    s2 = Sheet(p)
    s2.pocket(
        width=8,
        length=20,
        depth=12,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        w_center=0,
        lo=-10,
        hi=10,
    ).note("POCKET NOTE")
    assert any(f.kind == "note" and f.text == "POCKET NOTE" for f in s2.features)


def test_dim_handle_knurl():
    # #765: knurl() is sugar over note() with canonical KNURL formatting.
    part = _part()
    s = Sheet(part)
    s.diameter(Pos(30, 0, 0) * Cylinder(8, 20)).knurl("0.8")
    nt = next(f for f in s.features if f.kind == "note")
    assert nt.text == "KNURL 0.8 STRAIGHT" and nt.view == "plan"

    s2 = Sheet(part)
    s2.diameter(Pos(30, 0, 0) * Cylinder(8, 20)).knurl("0.5", "DIAMOND")
    nt2 = next(f for f in s2.features if f.kind == "note")
    assert nt2.text == "KNURL 0.5 DIAMOND"


def test_view_side_overrides_win():
    part = _part()
    s = Sheet(part)
    s.hole(Pos(0, 0, 0) * Cylinder(6, 20)).note("TAP", view="front", side="left")
    nt = next(f for f in s.features if f.kind == "note")
    assert nt.view == "front" and nt.side == "left"


def test_bad_inputs_raise():
    part = _part()
    s = Sheet(part)
    with pytest.raises(ValueError, match="note needs text"):
        s.note("   ", _top_face(part))
    with pytest.raises(ValueError, match="note needs text"):
        _declare_note("", _top_face(part), part)


def test_note_places_lint_clean():
    part = _part()
    s = Sheet(part)
    s.envelope()
    s.hole(Pos(0, 0, 0) * Cylinder(6, 20)).note("M3x0.5 TAP")
    dwg = s.build()
    assert "m_gdt0" in dwg.annotations()  # the note placed as a GD&T-kind corridor item
    assert not [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]


def test_face_note_places_lint_clean():
    part = _part()
    s = Sheet(part)
    s.envelope()
    s.hole(Pos(0, 0, 0) * Cylinder(6, 20))
    s.note("BREAK ALL EDGES 0.3", _top_face(part))
    dwg = s.build()
    assert [n for n in dwg.annotations() if "gdt" in n]  # the note placed
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]


def test_note_before_depth_keeps_provenance():
    # Mirrors the finish provenance test: declaring .note() then a size verb (.depth) on the SAME
    # handle replaces the source feature; the note's origin must re-bind to the FINAL feature at
    # build (index-sourced), else annotations_of() misses it and drop() orphans it.
    part = Box(80, 50, 20) - Pos(0, 0, 0) * Cylinder(6, 20)
    s = Sheet(part)
    s.envelope()
    h = s.hole(Pos(0, 0, 0) * Cylinder(6, 20))
    h.note("M3x0.5 TAP", view="front", side="above")  # declared BEFORE the size verb
    h.depth(5)  # replaces the hole feature (through=False)
    dwg = s.build()
    hole_feat = next(f for f in s.features if f.kind == "hole")
    nt_names = [n for n in dwg.annotations() if "gdt" in n]
    assert nt_names, "the note placed"
    # provenance re-bound to the FINAL (depth=5) hole, not the stale through hole
    assert set(nt_names) <= set(dwg.annotations_of(hole_feat))
    removed = dwg.drop(hole_feat)
    assert all(n in removed for n in nt_names)  # dropped with its feature, not orphaned


def test_note_ignored_by_model_inspection_paths():
    # model() is the cheap no-render inspection path; a note is a render-time GD&T-kind aspect,
    # not a dimension-bearing feature — it must not add DimParameters.
    part = _part()
    s = Sheet(part)
    s.hole(Pos(0, 0, 0) * Cylinder(6, 20)).note("TAP")
    nt = next(f for f in s.features if f.kind == "note")
    assert nt.parameters() == [] and nt.references() == []


def test_chaining_returns_the_handle_and_sheet():
    part = _part()
    s = Sheet(part)
    h = s.hole(Pos(0, 0, 0) * Cylinder(6, 20))
    assert h.note("TAP") is h  # handle chains
    assert s.note("DEBURR", _top_face(part)) is s  # sheet chains
