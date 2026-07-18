"""sheet.of(feature) — decorate a generated/existing feature (ADR 0011, #463).

Where the from_part-generated feature list and the aspect layer (P2a/P2a.2) meet: a handle
onto an existing feature so `.fit(...)` / `.tolerance(...)` reach a feature you didn't declare
from scratch.
"""

import pytest
from build123d import Box, Cylinder, Pos, Rot

from draftwright import Sheet


def _shaft():
    return (Rot(0, 90, 0) * Cylinder(4, 20)) + (Pos(15, 0, 0) * Rot(0, 90, 0) * Cylinder(6, 10))


class TestOf:
    def test_of_object_decorates_a_generated_step(self):
        # the headline: from_part generates the list; .of(object).fit() decorates one, end to end
        s = Sheet.from_part(_shaft())
        s.of(Pos(15, 0, 0) * Rot(0, 90, 0) * Cylinder(6, 10)).fit("g6")  # the ⌀12 collar
        dwg = s.build()
        dias = {n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia")}
        assert any(lbl == "ø12 g6" for lbl in dias.values()), dias

    def test_of_index_returns_a_decoratable_handle(self):
        s = Sheet.from_part(Box(80, 50, 8) - Pos(20, 10, 4) * Cylinder(4, 20))
        i = next(i for i, f in enumerate(s.features) if f.kind == "hole")
        s.of(i).fit("H7")  # records the fit on the hole's bore ⌀
        assert (i, "diameter") in s._tolerances

    def test_of_feature_identity(self):
        s = Sheet.from_part(_shaft())
        step = next(f for f in s.features if f.kind == "step")
        s.of(step).tolerance(0.0, 0.1)  # by the Feature object itself
        assert any(k[1] == "length" for k in s._tolerances)

    def test_of_hole_handle_supports_cbore(self):
        part = Box(100, 70, 24) - Pos(0, 0, 0) * Cylinder(9, 40) - Pos(0, 0, 8) * Cylinder(15, 20)
        s = Sheet.from_part(part)
        i = next(i for i, f in enumerate(s.features) if f.kind == "hole")
        s.of(i).cbore(Pos(0, 0, 8) * Cylinder(15, 20))  # the hole handle carries .cbore
        assert s.features[i].cbore == pytest.approx((30.0, 14.0))

    def test_of_unmatched_object_raises(self):
        s = Sheet.from_part(Box(80, 50, 8) - Pos(20, 10, 4) * Cylinder(4, 20))
        with pytest.raises(ValueError):
            s.of(Pos(0, 0, 0) * Cylinder(3, 10))  # ⌀6 at origin — no such feature

    def test_of_matches_on_axis_not_just_in_plane(self):
        # #463 review: a same-⌀ feature on a DIFFERENT axis (a cross-hole) sharing the in-plane
        # coords must not match — the axis is part of the identity.
        s = Sheet(Box(40, 40, 40))
        s.hole(diameter=8, at=(0, 0, 0), axis="z")  # the intended target
        s.diameter(diameter=8, at=(0, 0, 0), axis="x")  # same ⌀ + in-plane, different axis
        h = s.of(Pos(0, 0, 0) * Cylinder(4, 20))  # a z-axis tool → only the z hole
        assert s.features[h._i].frame.axis == "z"

    def test_of_ambiguous_object_raises(self):
        s = Sheet(Box(40, 40, 30))
        s.hole(diameter=8, at=(0, 0, 5), axis="z")
        s.hole(diameter=8, at=(0, 0, 20), axis="z")  # same ⌀ + in-plane (0,0)
        with pytest.raises(ValueError):
            s.of(Pos(0, 0, 10) * Cylinder(4, 10))  # matches both → ambiguous

    def test_of_non_diameter_feature_raises(self):
        s = Sheet(Box(30, 20, 5))
        s.envelope()
        with pytest.raises(ValueError):
            s.of(0)  # an envelope has no ⌀ aspect handle

    def test_of_out_of_range_index_raises(self):
        s = Sheet.from_part(Box(30, 20, 5))
        with pytest.raises(IndexError):
            s.of(99)

    def test_of_bool_rejected(self):
        s = Sheet.from_part(Box(30, 20, 5))
        with pytest.raises(TypeError):
            s.of(True)
