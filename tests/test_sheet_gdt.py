"""Sheet declarative GD&T aspect verbs — P2c.1: `.finish()` + `sheet.datum()` (ADR 0011 #479).

The fluent surface over the P2b render core: point at a feature or a build123d planar face,
declare a surface finish / datum symbol, and the target view + strip side are DERIVED from the
geometry (feature axis → face-on view; face normal → edge-on view). `view=`/`side=` override.
These tests pin the derivation (view/side/site/origin) and that a placed symbol is lint-clean.
"""

import pytest
from build123d import Box, Cylinder, Pos, Rotation

from draftwright.model.declare import gdt_target
from draftwright.sheet_dsl import Sheet


def _part():
    return Box(80, 50, 20) - Pos(0, 0, 0) * Cylinder(6, 20)


def _top_face(part):
    return part.faces().sort_by()[-1]  # +Z top


def test_finish_on_feature_derives_face_on_view():
    part = _part()
    s = Sheet(part)
    s.hole(Pos(0, 0, 0) * Cylinder(6, 20)).finish("1.6")
    fin = next(f for f in s.features if f.kind == "finish")
    assert fin.ra == "1.6"
    assert fin.view == "plan"  # a z-axis feature is face-on in plan
    assert fin.side == "below"
    assert fin.frame.origin == (0.0, 0.0, 0.0)
    assert fin.origin is s.features[0]  # provenance → the hole feature (finish is features[1])


def test_datum_on_planar_face_derives_edge_on_view():
    part = _part()
    s = Sheet(part)
    s.datum("A", _top_face(part))
    d = next(f for f in s.features if f.kind == "datum_ref")
    assert d.letter == "A"
    assert d.view == "front"  # a +Z face shows edge-on in front
    assert d.side == "above"  # face sits above the part centre (z=10 > 0)
    assert d.frame.axis == "z"
    assert d.origin is None  # a bare face has no source feature


def test_dim_handle_finish():
    part = _part()
    s = Sheet(part)
    s.diameter(Pos(30, 0, 0) * Cylinder(8, 20)).finish("3.2")
    fin = next(f for f in s.features if f.kind == "finish")
    assert fin.ra == "3.2" and fin.view == "plan"


def test_view_side_overrides_win():
    part = _part()
    v, side, site, axis = gdt_target(_top_face(part), part, view="plan", side="left")
    assert v == "plan" and side == "left"


def test_non_axis_aligned_face_raises():
    part = Rotation(0, 30, 0) * Box(40, 40, 40)
    skew = max(part.faces(), key=lambda f: abs(f.normal_at().X * f.normal_at().Z))
    with pytest.raises(ValueError, match="not axis-aligned"):
        gdt_target(skew, part)


def test_bad_inputs_raise():
    part = _part()
    s = Sheet(part)
    with pytest.raises(ValueError, match="letter"):
        s.datum("", _top_face(part))
    with pytest.raises(ValueError, match="roughness"):
        s.finish("   ", _top_face(part))


def test_face_datum_places_lint_clean():
    part = _part()
    s = Sheet(part)
    s.envelope()
    s.hole(Pos(0, 0, 0) * Cylinder(6, 20))
    s.datum("A", _top_face(part))
    dwg = s.build()
    assert [n for n in dwg._named if "gdt" in n]  # the datum placed
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]


def test_finish_before_depth_keeps_provenance():
    # Adversarial-review finding (CONFIRMED): declaring .finish() then a size verb (.depth) on
    # the SAME handle replaces the source feature; the finish's origin must re-bind to the FINAL
    # feature at build (index-sourced), else annotations_of() misses it and drop() orphans it.
    part = Box(80, 50, 20) - Pos(0, 0, 0) * Cylinder(6, 20)
    s = Sheet(part)
    s.envelope()
    h = s.hole(Pos(0, 0, 0) * Cylinder(6, 20))
    h.finish("1.6", view="front", side="above")  # declared BEFORE the size verb
    h.depth(5)  # replaces the hole feature (through=False)
    dwg = s.build()
    hole_feat = next(f for f in s.features if f.kind == "hole")
    fin_names = [n for n in dwg._named if "gdt" in n]
    assert fin_names, "the finish placed"
    # provenance re-bound to the FINAL (depth=5) hole, not the stale through hole
    assert set(fin_names) <= set(dwg.annotations_of(hole_feat))
    removed = dwg.drop(hole_feat)
    assert all(n in removed for n in fin_names)  # dropped with its feature, not orphaned


def test_override_recovers_a_congested_default():
    # The feature-path default (plan/below) is congested by the envelope width dim, so a bare
    # finish there drops with a warning; an explicit free side recovers it (the override seam).
    part = _part()
    s = Sheet(part)
    s.envelope()
    h = s.hole(Pos(0, 0, 0) * Cylinder(6, 20))
    h.finish("1.6", view="front", side="above")  # a roomy strip
    dwg = s.build()
    assert "m_gdt0" in dwg._named
    assert not [i for i in dwg._build_issues if i.code == "gdt_dropped"]
