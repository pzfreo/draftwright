"""#798 — the filled material field and its exact segment-span probe.

These are leaf tests: pure page-plane numbers, no OCC, no drawing. They pin the
claims the leader router will rely on, each with the mutation that breaks it noted
beside it, because a green assertion that survives deleting the code it guards is
not evidence.
"""

from __future__ import annotations

import math

import pytest

from draftwright._geometry import (
    MaterialField,
    _segment_triangle_interval,
    material_field,
    material_span,
)


def _square(x0, y0, x1, y1):
    """Two triangles tiling an axis-aligned rectangle."""
    return [
        ((x0, y0), (x1, y0), (x1, y1)),
        ((x0, y0), (x1, y1), (x0, y1)),
    ]


class TestFieldConstruction:
    def test_degenerate_triangles_are_dropped(self):
        # A zero-area triangle carries no material; keeping it would only cost probe
        # time. Mutation: removing the winding check keeps it and len() becomes 2.
        field = material_field([((0, 0), (1, 0), (2, 0)), ((0, 0), (1, 0), (0, 1))])
        assert len(field.triangles) == 1

    def test_non_finite_triangles_are_dropped(self):
        field = material_field([((0, 0), (1, 0), (float("nan"), 1))])
        assert not field
        assert field.box is None

    def test_the_box_is_the_aggregate_extent(self):
        field = material_field(_square(1.0, 2.0, 5.0, 8.0))
        assert field.box == (1.0, 2.0, 5.0, 8.0)

    def test_an_empty_field_probes_as_clear(self):
        assert material_span((0, 0), (10, 10), material_field([])) == 0.0


class TestMaterialSpan:
    def test_a_segment_inside_material_measures_its_whole_length(self):
        field = material_field(_square(0, 0, 10, 10))
        assert material_span((2, 5), (8, 5), field) == pytest.approx(6.0)

    def test_a_segment_clear_of_material_measures_zero(self):
        field = material_field(_square(0, 0, 10, 10))
        assert material_span((20, 20), (30, 30), field) == 0.0

    def test_a_crossing_segment_measures_only_the_traversed_part(self):
        # Half in, half out: the probe is a magnitude, not a flag.
        field = material_field(_square(0, 0, 10, 10))
        assert material_span((5, 5), (15, 5), field) == pytest.approx(5.0)

    def test_a_void_between_two_bodies_is_not_counted(self):
        # THE claim that separates a filled field from a crossing COUNT. A shaft
        # spanning two bodies with a 10 mm gap crosses four outline edges, but
        # travels through only 10 mm of material. A count-based test calls this a
        # cut; the filled test prices it honestly.
        field = material_field(_square(0, 0, 5, 10) + _square(15, 0, 20, 10))
        assert material_span((0, 5), (20, 5), field) == pytest.approx(10.0)

    def test_a_hole_inside_a_body_is_a_void_the_field_never_had(self):
        # The hole case stated as the router will meet it: material is lowered as the
        # filled FACE, so an annulus is two bodies and the bore is simply absent. A
        # leader tipped in the bore and run outward pays only for the wall it crosses.
        field = material_field(_square(-20, -1, -5, 1) + _square(5, -1, 20, 1))
        assert material_span((0, 0), (30, 0), field) == pytest.approx(15.0)

    def test_overlapping_triangles_are_unioned_not_summed(self):
        # Adjacent projected faces share edges and a mesh may double up. Mutation:
        # summing the intervals instead of unioning them doubles this to 20.
        field = material_field(_square(0, 0, 10, 10) + _square(0, 0, 10, 10))
        assert material_span((0, 5), (10, 5), field) == pytest.approx(10.0)

    def test_the_span_never_exceeds_the_segment_length(self):
        field = material_field(_square(0, 0, 10, 10) * 5)
        length = math.hypot(10, 10)
        assert material_span((0, 0), (10, 10), field) <= length + 1e-9

    def test_a_zero_length_segment_measures_zero(self):
        field = material_field(_square(0, 0, 10, 10))
        assert material_span((5, 5), (5, 5), field) == 0.0

    def test_a_grazing_segment_ranks_below_a_cut(self):
        # The ordering the Policy-B penalty depends on: both routes cross the body,
        # one barely. A boolean predicate ties them and the solver cannot prefer the
        # better one.
        field = material_field(_square(0, 0, 10, 10))
        graze = material_span((-5, 9.8), (5, 9.8), field)
        cut = material_span((-5, 5), (15, 5), field)
        assert 0 < graze < cut


class TestProbeIndex:
    def test_a_long_diagonal_finds_material_across_the_grid(self):
        # The cell walk, not a bbox sweep: this diagonal's bounding box covers the
        # whole grid, so a bbox prefilter would "work" here by accident. The guard is
        # the next test.
        field = material_field(_square(0, 0, 100, 100))
        assert material_span((-10, -10), (110, 110), field) == pytest.approx(math.hypot(100, 100))

    def test_the_walk_reaches_a_far_cell_it_must_not_skip(self):
        # A thin body in the LAST cell of a large sparse field. If the traversal
        # terminated early or stepped past a cell, this reads 0.
        triangles = _square(0, 0, 1, 1) + _square(98, 98, 100, 100)
        field = material_field(triangles)
        assert material_span((99, 0), (99, 100), field) == pytest.approx(2.0)

    def test_an_axis_aligned_probe_along_a_cell_boundary_still_hits(self):
        # Degenerate walk input: the segment runs exactly along a grid line.
        field = material_field(_square(0, 0, 40, 40))
        assert material_span((0, 20), (40, 20), field) == pytest.approx(40.0)

    def test_the_index_covers_every_triangle(self):
        field = material_field(_square(0, 0, 30, 30) + _square(60, 60, 90, 90))
        indexed = {position for cell in field.index.values() for position in cell}
        assert indexed == set(range(len(field.triangles)))


class TestSegmentTriangleInterval:
    def test_winding_does_not_change_the_answer(self):
        # A projected mesh has no guaranteed winding. Mutation: dropping the `sign`
        # normalisation makes one of these None.
        forward = ((0.0, 0.0), (10.0, 0.0), (0.0, 10.0))
        reversed_ = ((0.0, 0.0), (0.0, 10.0), (10.0, 0.0))
        probe = ((1.0, 1.0), (2.0, 2.0))
        assert _segment_triangle_interval(*probe, forward) == _segment_triangle_interval(
            *probe, reversed_
        )
        assert _segment_triangle_interval(*probe, forward) is not None

    def test_a_miss_returns_none(self):
        assert (
            _segment_triangle_interval(
                (20.0, 20.0), (30.0, 30.0), ((0.0, 0.0), (10.0, 0.0), (0.0, 10.0))
            )
            is None
        )

    def test_the_boundary_is_inclusive_because_the_mesh_is_a_decomposition(self):
        # Deliberately NOT _convex_polygons_overlap's touching-is-clear rule, and the
        # test that forced the choice: a square meshed as two triangles has its
        # diagonal as a SHARED INTERIOR edge. Excluding boundaries reads that wholly
        # internal route as clear, making the answer depend on how the mesher cut the
        # region — the platform-dependence this field exists to remove.
        square = _square(0, 0, 10, 10)
        assert material_span((0, 0), (10, 10), material_field(square)) == pytest.approx(
            math.hypot(10, 10)
        )
        # The cost of that choice, stated honestly: a shaft drawn exactly along an
        # OUTLINE edge also counts as material. That is a route worth penalising
        # anyway — a shaft on the outline is indistinguishable from it.
        assert material_span((0, 0), (10, 0), material_field(square)) == pytest.approx(10.0)


class TestDeterminism:
    def test_the_probe_is_order_independent(self):
        # The fixed-point union must not depend on the order triangles were lowered
        # in, or the same drawing scores differently on two machines.
        triangles = _square(0, 0, 10, 10) + _square(5, 0, 15, 10)
        forward = material_span((0, 5), (15, 5), material_field(triangles))
        backward = material_span((0, 5), (15, 5), material_field(list(reversed(triangles))))
        assert forward == backward

    def test_repeated_probes_are_bit_identical(self):
        field = material_field(_square(0, 0, 10, 10))
        first = material_span((-3, 4.4), (13, 6.1), field)
        assert all(material_span((-3, 4.4), (13, 6.1), field) == first for _ in range(5))

    def test_the_field_is_frozen(self):
        field = material_field(_square(0, 0, 10, 10))
        assert isinstance(field, MaterialField)
        with pytest.raises(Exception):
            field.cell = 1.0  # type: ignore[misc]
