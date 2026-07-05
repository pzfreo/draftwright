"""Object-reading aspect verbs — the number-free 3b layer (ADR 0011 #462).

`.cbore(tool)` / `.spotface(tool)` read a counterbore/spotface's ⌀ + depth off the tool
object and the part, so the drawing layer restates no numbers and tracks the geometry.
"""

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet
from draftwright.model.declare import read_bore_step


class TestReadBoreStep:
    def test_top_counterbore_reads_diameter_and_depth(self):
        part = Box(100, 70, 24) - Pos(0, 0, 0) * Cylinder(9, 40) - Pos(0, 0, 8) * Cylinder(15, 20)
        tool = Pos(0, 0, 8) * Cylinder(15, 20)  # ⌀30, overhangs the z=12 top → depth 14
        assert read_bore_step(part, tool, "z") == pytest.approx((30.0, 14.0))

    def test_depth_measured_from_the_near_face_both_orientations(self):
        part = Box(40, 40, 20)  # z spans -10..10
        top = Pos(0, 0, 6) * Cylinder(8, 12)  # sits high → opens at +z (10); floor at 0 → depth 10
        bot = Pos(0, 0, -6) * Cylinder(
            8, 12
        )  # sits low → opens at -z (-10); floor at 0 → depth 10
        assert read_bore_step(part, top, "z")[1] == pytest.approx(10.0)
        assert read_bore_step(part, bot, "z")[1] == pytest.approx(10.0)

    def test_diameter_tracks_the_tool_parametrically(self):
        part = Box(60, 60, 20)
        assert read_bore_step(part, Pos(0, 0, 6) * Cylinder(10, 12), "z")[0] == pytest.approx(20.0)
        assert read_bore_step(part, Pos(0, 0, 6) * Cylinder(12, 12), "z")[0] == pytest.approx(24.0)


class TestCboreVerb:
    def _plate(self):
        plate = Box(100, 70, 24)
        bore = Pos(0, 0, 0) * Cylinder(9, 40)
        cbore = Pos(0, 0, 8) * Cylinder(15, 20)
        return (plate - bore - cbore), bore, cbore

    def test_number_free_counterbore_renders_lint_clean(self):
        part, bore, cbore = self._plate()
        s = Sheet(part, title="MP")
        s.envelope()
        s.hole(bore).cbore(cbore)  # ⌀ + depth read off the objects — no numbers
        dwg = s.build()
        assert s.features[1].cbore == pytest.approx((30.0, 14.0))
        assert [i for i in dwg.lint() if i.severity in ("warning", "error")] == []

    def test_explicit_kwargs_override_the_object_read(self):
        part, bore, cbore = self._plate()
        s = Sheet(part)
        s.hole(bore).cbore(cbore, depth=6)  # keep the read ⌀, override the depth
        assert s.features[0].cbore == pytest.approx((30.0, 6.0))

    def test_explicit_values_without_an_object(self):
        s = Sheet(Box(40, 40, 10))
        s.hole(diameter=6, at=(0, 0, 0), axis="z").cbore(diameter=12, depth=4)
        assert s.features[0].cbore == (12, 4)

    def test_needs_object_or_explicit_values(self):
        s = Sheet(Box(40, 40, 10))
        with pytest.raises(ValueError):
            s.hole(diameter=6, at=(0, 0, 0), axis="z").cbore()

    def test_spotface_reads_off_the_tool(self):
        part = Box(60, 60, 20)
        sf = Pos(0, 0, 8) * Cylinder(12, 6)  # ⌀24, opens at +z(10), floor at 5 → depth 5
        s = Sheet(part - Pos(0, 0, 0) * Cylinder(4, 40) - sf)
        s.hole(Pos(0, 0, 0) * Cylinder(4, 40)).spotface(sf)
        assert s.features[0].spotface == pytest.approx((24.0, 5.0))
