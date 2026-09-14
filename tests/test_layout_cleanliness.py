"""Finished-sheet layout cleanliness invariants."""

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing


class TestLayoutCleanlinessInvariant:
    """End-to-end invariant (#293): for a spread of representative part shapes the
    *finished* drawing must place its views and annotations without layout
    collisions — the OUTCOME, not just the trigger mechanics the unit tests above
    cover. This is the check that would have caught the GRM-03 staggered chain
    bumping the plan view: the trigger unit tests were green while a real part
    rendered with overlapping annotations. A regression here means the layout
    engine (estimate → measure-and-repack) let a real collision through."""

    # Genuine layout defects — NOT view_annotation_inside_extents, the soft info
    # code for a callout legitimately sitting inside a large face.
    _DEFECTS = {
        "view_annotation_overlap",
        "view_overlap",
        "view_out_of_bounds",
        "annotation_out_of_bounds",
        "annotation_overlap",
    }

    @staticmethod
    def _x_simple():
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        s = Cylinder(8, 20, align=(Align.CENTER, Align.CENTER, b)) + Pos(0, 0, 20) * Cylinder(
            5, 20, align=(Align.CENTER, Align.CENTER, b)
        )
        return Rotation(0, 90, 0) * s  # roomy X-turned chain → one tier, no zig-zag

    @staticmethod
    def _x_crowded():
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN  # GRM-03 shape: fine head steps + long shaft → stagger + view lift
        s = None
        z = 0.0
        for d, ln in [(8, 1.0), (12, 1.0), (8, 1.0), (12, 1.0), (6, 30.0)]:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            s = seg if s is None else s + seg
            z += ln
        return Rotation(0, 90, 0) * s

    @staticmethod
    def _z_stepped():
        from build123d import Cylinder, Pos

        return Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)  # Z-turned ladder

    @staticmethod
    def _prism_holes():
        part = Box(80, 60, 20)  # a row of holes → location dims above the plan view
        for x in (-30, -10, 10, 30):
            part -= Pos(x, 20, 0) * Cylinder(3, 30)
        return part

    @staticmethod
    def _bolt_circle():
        import math

        part = Box(60, 60, 15)  # 6-hole bolt circle → ballooned plan-view halo
        for i in range(6):
            a = i * math.pi / 3
            part -= Pos(20 * math.cos(a), 20 * math.sin(a), 0) * Cylinder(2.5, 30)
        return part

    @staticmethod
    def _counterbored():
        part = Box(60, 40, 20)  # counterbore → full section A-A
        part -= Cylinder(4, 30)
        part -= Pos(0, 0, 2) * Cylinder(7, 20)
        return part

    @pytest.mark.parametrize(
        "factory",
        ["_x_simple", "_x_crowded", "_z_stepped", "_prism_holes", "_bolt_circle", "_counterbored"],
    )
    def test_finished_sheet_has_no_layout_collisions(self, factory):
        dwg = build_drawing(getattr(self, factory)())
        hits = sorted({i.code for i in dwg.lint()} & self._DEFECTS)
        assert not hits, f"{factory}: layout defects in finished drawing: {hits}"
