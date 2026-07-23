"""GD&T aspect side-layer placement (ADR 0011 §4 / ADR 0009, #61).

Declared feature control frames / datum feature symbols / surface finishes are placed
as first-class ADR 0009 corridor candidates — through the SAME collect-then-solve strip
machinery as the auto-dimensions, NOT a leftover first-fit. These tests lock down: the
glyphs render into their target strip, they carry their real footprint so stacked frames
never overlap, a full strip drops honestly (a warning, not a silent vanish), and the
placement stays lint-clean.
"""

import pytest
from build123d import Box, Cylinder, Draft, Pos
from build123d_drafting import FeatureControlFrame

from draftwright.builder import build_drawing, detect_part_model
from draftwright.model.ir import ControlFrame, DatumRef, Finish, Frame


def _part():
    """A prismatic block with a central through-hole — roomy plan/front views."""
    return Box(80, 50, 20) - Pos(0, 0, 0) * Cylinder(6, 20)


def _build(*extra_features, part=None):
    part = part if part is not None else _part()
    m = detect_part_model(part)
    m.features.extend(extra_features)
    return build_drawing(part, model=m)


def _fcf_height():
    """The bare feature-control-frame glyph height — the strip footprint a frame reserves
    (NOT the leader+frame box). Two stacked frames must sit at least this far apart."""
    g = FeatureControlFrame("position", "0.1", datums=("A",), draft=Draft(font_size=3.0))
    return g.bounding_box().size.Y


def test_control_frame_places_first_class():
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="above",
        datums=("A",),
        diameter=True,
    )
    dwg = _build(frame)
    assert "m_gdt0" in dwg.annotations()
    assert not [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    # It rendered the actual frame geometry (a wide box, not an empty leader).
    assert dwg.get_annotation("m_gdt0").bounding_box().size.X > 15


def test_datum_and_finish_place():
    datum = DatumRef(frame=Frame((30.0, 0.0, 0.0), "z"), letter="A", view="plan", side="above")
    finish = Finish(frame=Frame((0.0, 25.0, 0.0), "z"), ra="3.2", view="front", side="above")
    dwg = _build(datum, finish)
    placed = {n for n in dwg.annotations() if n.startswith("m_gdt")}
    assert placed == {"m_gdt0", "m_gdt1"}


def test_stacked_frames_reserve_real_footprint():
    # Two frames on the same above strip. If placement reserved only one label-height
    # (the pre-#61 (tier, tier) hardcode) the ~6 mm-tall glyphs would overlap; the real
    # footprint keeps their centres >= one glyph-height apart. Different sites so the
    # leader shafts are not collinear and each frame's glyph edge is the strip-far bbox
    # edge (an above strip stacks the glyph at the TOP: centre = max.Y - h/2).
    f0 = ControlFrame(
        frame=Frame((-20.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="above",
    )
    f1 = ControlFrame(
        frame=Frame((20.0, 0.0, 0.0), "z"),
        characteristic="flatness",
        tolerance="0.05",
        view="plan",
        side="above",
    )
    dwg = _build(f0, f1)
    assert {"m_gdt0", "m_gdt1"} <= set(dwg.annotations())
    h = _fcf_height()
    c0 = dwg.get_annotation("m_gdt0").bounding_box().max.Y - h / 2
    c1 = dwg.get_annotation("m_gdt1").bounding_box().max.Y - h / 2
    assert abs(c0 - c1) >= h - 1e-6  # glyphs do not overlap in the stack


def test_congested_side_falls_through_to_opposite():
    # #481: the plan-below strip carries the overall-width envelope dim, so a frame declared
    # there has no free tier — but rather than drop, render_gdt falls through to the OPPOSITE
    # side (plan-above) and places it there. No gdt_dropped warning; the frame survives.
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="below",
    )
    dwg = _build(frame)
    assert "m_gdt0" in dwg.annotations()  # recovered on the opposite side
    assert not [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]
    # It landed ABOVE the plan view (the fallthrough side): the frame (leader's far end) sits
    # above the view centre, whereas a below placement would keep the whole box at/under it.
    assert (
        dwg.get_annotation("m_gdt0").bounding_box().max.Y > dwg.at("plan", *dwg.centroid)[1] + 10
    )


@pytest.mark.parametrize("side", ["above", "below"])
def test_gdt_never_overlaps_the_title_block(side):
    # #481 review (CONFIRMED, both paths): the side/below strip runs down into the title-block
    # region, which is added AFTER the corridor drain — so neither the PRIMARY corridor solve
    # (force-kept) nor the fallthrough's carve can see it. Stacking frames on the side view (the
    # bottom-right one) must REJECT any spot over the title block (drop/relocate) rather than
    # overlap 'DRAWING'. side="below" exercises the primary path, side="above" the fallthrough.
    frames = [
        ControlFrame(
            frame=Frame((x, 0.0, 0.0), "z"),
            characteristic="position",
            tolerance="0.1",
            view="side",
            side=side,
        )
        for x in (-20.0, 0.0, 20.0)
    ]
    dwg = _build(*frames)
    assert not [x for x in dwg.lint() if x.code == "annotation_overlap"]


def test_bad_target_drops_without_crashing():
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="nope",
        side="above",
    )
    dwg = _build(frame)
    assert [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    assert "m_gdt0" not in dwg.annotations()


def test_invalid_glyph_spec_drops_not_crashes():
    # The IR is public input (ADR 0011): a mistyped characteristic must drop the one item
    # with a gdt_dropped warning, NOT raise ValueError and take down the whole drawing.
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="postion",  # codespell:ignore postion — deliberate typo; helper raises "Unknown characteristic"
        tolerance="0.1",
        view="plan",
        side="above",
    )
    dwg = _build(frame)  # must not raise
    dropped = [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    assert dropped and "m_gdt0" in dropped[0].message
    assert "m_gdt0" not in dwg.annotations()


def test_wide_frame_in_narrow_strip_drops_not_overshoots():
    # Adversarial-review finding (CONFIRMED): a wide GD&T glyph (multi-datum FCF ~33 mm) on
    # a left/right strip narrower than the glyph must DROP with a gdt_dropped warning — not
    # render off the drawable area. Pre-fix it placed at min.X=-7.17 (17 mm past outer_limit,
    # annotation_out_of_bounds error). The boundary reservation now uses the glyph's real
    # outward extent, so a too-narrow strip has no feasible tier and the frame drops.
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="left",
        datums=("A", "B"),
    )
    dwg = _build(frame)
    assert [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    assert "m_gdt0" not in dwg.annotations()
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]


def test_degenerate_leader_site_does_not_crash():
    # Focused-review finding (CONFIRMED): a declared site that projects ONTO the solved strip
    # tier (pos == py) makes the leader shaft zero-length, which raised in OCC and crashed the
    # whole build (public IR). _build now guarantees a minimum shaft, so it places instead.
    # oy=44.5 is the reviewer's reproduced coincidence for this part/part-of-strip geometry.
    frame = ControlFrame(
        frame=Frame((0.0, 44.5, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="above",
    )
    dwg = _build(frame)  # must not raise
    assert "m_gdt0" in dwg.annotations()


def test_placement_is_lint_clean():
    # The Tier-1 claim: a frame placed through the strip solve does not overlap the dims
    # it annotates (the failure mode that motivated routing GD&T through the solver).
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="above",
        datums=("A",),
        diameter=True,
    )
    dwg = _build(frame)
    overlaps = [x for x in dwg.lint() if x.code == "annotation_overlap"]
    assert not overlaps


def test_provenance_back_link():
    # A frame decorating a detected hole records that hole as its annotation's feature
    # (ADR 0010 provenance) so the read/edit surface can find it.
    m = detect_part_model(_part())
    hole = next(f for f in m.features if f.kind == "hole")
    m.features.append(
        ControlFrame(
            frame=Frame((0.0, 0.0, 0.0), "z"),
            characteristic="position",
            tolerance="0.1",
            view="plan",
            side="above",
            origin=hole,
        )
    )
    dwg = build_drawing(_part(), model=m)
    assert "m_gdt0" in dwg.annotations_of(hole)
