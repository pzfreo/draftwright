"""#798 — the filled material field and its exact segment-span probe.

These are leaf tests: pure page-plane numbers, no OCC, no drawing. They pin the
claims the leader router will rely on, each with the mutation that breaks it noted
beside it, because a green assertion that survives deleting the code it guards is
not evidence.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from draftwright._geometry import (
    _MATERIAL_SPAN_TICKS,
    MaterialField,
    _segment_triangle_interval,
    material_field,
    material_intervals,
    material_reentry_span,
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


class TestMaterialIntervals:
    def test_two_bodies_are_two_intervals(self):
        field = material_field(_square(0, 0, 5, 10) + _square(15, 0, 20, 10))
        assert len(material_intervals((0, 5), (20, 5), field)) == 2

    def test_one_body_is_one_interval(self):
        field = material_field(_square(0, 0, 10, 10))
        assert len(material_intervals((-5, 5), (15, 5), field)) == 1

    def test_intervals_are_ordered_along_the_segment(self):
        field = material_field(_square(0, 0, 5, 10) + _square(15, 0, 20, 10))
        intervals = material_intervals((0, 5), (20, 5), field)
        assert intervals == tuple(sorted(intervals))

    def test_a_bridge_closes_a_narrow_gap(self):
        # Two bodies 0.1 mm apart read as one traversal under a 0.5 mm bridge: the
        # gap is below the resolution the lowering can be trusted at.
        field = material_field(_square(0, 0, 10, 10) + _square(10.1, 0, 20, 10))
        assert len(material_intervals((0, 5), (20, 5), field)) == 2
        assert len(material_intervals((0, 5), (20, 5), field, bridge=0.5)) == 1

    def test_a_bridge_does_not_absorb_a_real_gap(self):
        # Mutation guard on the bridge: a 10 mm void must survive a 0.5 mm bridge, or
        # a genuine cut is silently reported as one traversal.
        field = material_field(_square(0, 0, 5, 10) + _square(15, 0, 20, 10))
        assert len(material_intervals((0, 5), (20, 5), field, bridge=0.5)) == 2


class TestMaterialReentrySpan:
    def test_a_leader_exiting_its_own_body_is_not_a_defect(self):
        # THE case that makes the raw span unusable as a predicate: every correct ⌀,
        # hole and pocket callout starts on the part and crosses material to reach
        # clear page. Charging for that would condemn the whole sheet.
        field = material_field(_square(0, 0, 10, 10))
        assert material_span((5, 5), (25, 5), field) == pytest.approx(5.0)
        assert material_reentry_span((5, 5), (25, 5), field) == 0.0

    def test_cutting_a_second_body_is_the_defect(self):
        # Tip inside the left body, shaft ploughing on through a neighbour. The
        # tolerance is the documented tick resolution (1e-6 of the segment length),
        # not an arbitrary slack: the interval union is deliberately fixed-point so
        # that summing it is platform-stable.
        field = material_field(_square(0, 0, 5, 10) + _square(15, 0, 20, 10))
        assert material_reentry_span((2, 5), (25, 5), field) == pytest.approx(5.0, abs=1e-3)

    def test_passing_over_a_through_hole_is_not_a_reentry(self):
        # The filled model's payoff, stated as a leader: a shaft leaving a bore
        # crosses the annulus wall once. A crossing COUNT sees two outline crossings
        # and calls it a cut; there is no second material traversal here at all.
        field = material_field(_square(-20, -1, -5, 1) + _square(5, -1, 20, 1))
        assert material_reentry_span((0, 0), (30, 0), field) == 0.0

    def test_the_defect_is_a_magnitude_not_a_flag(self):
        # Two cuts of different severity must be rankable, or the solver cannot
        # prefer the better of two imperfect routes.
        field = material_field(_square(0, 0, 5, 10) + _square(15, 0, 17, 10))
        slight = material_reentry_span((2, 5), (25, 5), field)
        field_worse = material_field(_square(0, 0, 5, 10) + _square(15, 0, 30, 10))
        severe = material_reentry_span((2, 5), (40, 5), field_worse)
        assert 0 < slight < severe

    def test_a_clear_route_scores_zero(self):
        field = material_field(_square(0, 0, 10, 10))
        assert material_reentry_span((15, 15), (25, 25), field) == 0.0

    def test_the_reentry_never_exceeds_the_total_span(self):
        field = material_field(_square(0, 0, 5, 10) + _square(15, 0, 20, 10))
        probe = ((2, 5), (25, 5))
        assert material_reentry_span(*probe, field) <= material_span(*probe, field) + 1e-9

    def test_a_bridged_sliver_does_not_invent_a_reentry(self):
        # A tip grazing a curved boundary can be split into a sliver plus the real
        # traversal by where facets fall. Without the bridge that mesh detail becomes
        # a routing verdict; the assertion pins that it does not.
        field = material_field(_square(0, 0, 0.02, 10) + _square(0.04, 0, 10, 10))
        assert material_reentry_span((0, 5), (25, 5), field) > 0
        assert material_reentry_span((0, 5), (25, 5), field, bridge=0.1) == 0.0


class TestTicksContract:
    def test_intervals_are_expressed_in_the_documented_tick_scale(self):
        # A full-length traversal spans the whole tick range, so callers converting
        # ticks back to mm share one convention with the module.
        field = material_field(_square(0, 0, 10, 10))
        assert material_intervals((0, 5), (10, 5), field) == ((0, _MATERIAL_SPAN_TICKS),)


class TestTheIndexAgreesWithBruteForce:
    """The grid index is an optimisation; if it can skip a cell, a shaft that cuts the
    part scores as clear and the whole model quietly under-reports. So the indexed probe
    is checked against an exhaustive scan of every triangle."""

    @staticmethod
    def _brute_span(p, q, field):
        from draftwright._geometry import _MATERIAL_SPAN_TICKS, _segment_triangle_interval

        length = math.hypot(q[0] - p[0], q[1] - p[1])
        if length <= 1e-12:
            return 0.0
        raw = []
        for triangle in field.triangles:  # every triangle, no index
            span = _segment_triangle_interval(p, q, triangle)
            if span is None:
                continue
            lo = max(0, min(_MATERIAL_SPAN_TICKS, round(span[0] * _MATERIAL_SPAN_TICKS)))
            hi = max(0, min(_MATERIAL_SPAN_TICKS, round(span[1] * _MATERIAL_SPAN_TICKS)))
            if hi > lo:
                raw.append((lo, hi))
        if not raw:
            return 0.0
        raw.sort()
        total, current_lo, current_hi = 0, *raw[0]
        for lo, hi in raw[1:]:
            if lo > current_hi:
                total += current_hi - current_lo
                current_lo, current_hi = lo, hi
            else:
                current_hi = max(current_hi, hi)
        total += current_hi - current_lo
        return length * total / _MATERIAL_SPAN_TICKS

    def test_random_scenes_and_probes_agree_exactly(self):
        import random

        rng = random.Random(20260816)  # fixed: a flaky geometry guard is worse than none
        probes = 0
        for _ in range(400):
            triangles = []
            for _member in range(rng.randint(1, 60)):
                cx, cy = rng.uniform(0, 100), rng.uniform(0, 100)
                spread = rng.uniform(0.4, 22)
                triangles.append(
                    tuple(
                        (cx + rng.uniform(-spread, spread), cy + rng.uniform(-spread, spread))
                        for _corner in range(3)
                    )
                )
            field = material_field(triangles)
            if not field:
                continue
            for _probe in range(6):
                p = (rng.uniform(-20, 120), rng.uniform(-20, 120))
                q = (rng.uniform(-20, 120), rng.uniform(-20, 120))
                probes += 1
                assert material_span(p, q, field) == pytest.approx(
                    self._brute_span(p, q, field), abs=1e-9
                ), f"indexed probe disagrees with the exhaustive scan for {p}->{q}"
        assert probes > 1000, "the sweep must actually exercise the index"


class TestTheLoweringFailsWhole:
    """A partial mesh is worse than none: routes through the missing faces would read as
    clear. `part_material_mesh` returns None rather than a subset, and the field it feeds
    is empty rather than optimistic."""

    def test_a_budget_overrun_yields_no_mesh_rather_than_a_subset(self):
        from build123d import Box

        from draftwright.projection import part_material_mesh

        assert part_material_mesh(Box(10, 10, 10), 1.0, max_triangles=1) is None

    def test_a_meshable_part_yields_triangles(self):
        from build123d import Box

        from draftwright.projection import part_material_mesh

        mesh = part_material_mesh(Box(10, 10, 10), 1.0)
        assert mesh and all(len(triangle) == 3 for triangle in mesh)

    def test_an_unmeshable_object_yields_no_mesh(self):
        from draftwright.projection import part_material_mesh

        assert part_material_mesh(object(), 1.0) is None

    def test_an_unprojectable_mesh_yields_an_empty_field(self):
        # Not a field of "everything is clear": an empty field reads as "no material
        # known", and callers treat that as no evidence rather than as clearance.
        from draftwright.projection import view_material_field

        def explode(_point):
            raise RuntimeError("projector unavailable")

        field = view_material_field((((0, 0, 0), (1, 0, 0), (0, 1, 0)),), explode)
        assert not field
        assert material_span((0, 0), (10, 10), field) == 0.0

    def test_no_mesh_yields_an_empty_field(self):
        from draftwright.projection import view_material_field

        assert not view_material_field(None, lambda point: (point[0], point[1]))


class TestMalformedLoweringEntries:
    """The lowering is fed by OCC tessellation, so the field constructor takes whatever
    the mesher produced. A malformed entry must be dropped, never interpreted."""

    def test_a_non_triangle_is_dropped(self):
        field = material_field([((0, 0), (1, 0)), ((0, 0), (1, 0), (0, 1))])
        assert len(field.triangles) == 1

    def test_an_unreadable_corner_is_dropped(self):
        field = material_field([("not a point", (1, 0), (0, 1)), ((0, 0), (1, 0), (0, 1))])
        assert len(field.triangles) == 1

    def test_a_corner_with_too_few_coordinates_is_dropped(self):
        field = material_field([((0,), (1, 0), (0, 1)), ((0, 0), (1, 0), (0, 1))])
        assert len(field.triangles) == 1

    def test_a_degenerate_triangle_probes_as_clear(self):
        # Reachable only by calling the interval helper directly — material_field drops
        # zero-area triangles — but it must answer "no material", not divide by zero.
        assert (
            _segment_triangle_interval(
                (0.0, 0.0), (10.0, 0.0), ((0.0, 0.0), (5.0, 0.0), (9.0, 0.0))
            )
            is None
        )

    def test_a_field_with_no_grid_still_probes_every_triangle(self):
        # Defensive: a MaterialField carrying no usable cell size falls back to scanning
        # the whole triangle list rather than silently reporting clear.
        triangle = ((0.0, 0.0), (10.0, 0.0), (0.0, 10.0))
        ungridded = MaterialField((triangle,), (0.0, 0.0, 10.0, 10.0), {}, 0.0)
        assert material_span((1.0, 1.0), (2.0, 2.0), ungridded) > 0


class TestPerFaceLoweringFailures:
    """`part_material_mesh` returns None rather than a partial mesh for EVERY way a face
    can fail, not just an unusable solid. A mesh missing some faces reports routes
    straight through the gap as clear, which is worse than having no field at all —
    the caller can fall back honestly from None, but cannot detect a silent hole."""

    @staticmethod
    def _fake_part(monkeypatch, face):
        """Swap the deepcopy for a stand-in whose faces misbehave.

        The real solid's ``wrapped`` is kept so the OCC clean/remesh calls still work;
        only the face iteration is substituted, which is where the per-face guards live.
        """
        from build123d import Box

        from draftwright import projection

        real = Box(10, 10, 10)

        class _Part:
            wrapped = real.wrapped

            @staticmethod
            def faces():
                return [face]

        monkeypatch.setattr(projection.copy, "deepcopy", lambda _part: _Part())
        return real

    @staticmethod
    def _vertex(x, y, z):
        return SimpleNamespace(X=x, Y=y, Z=z)

    def test_a_face_that_cannot_be_tessellated_yields_no_mesh(self, monkeypatch):
        from draftwright.projection import part_material_mesh

        class _Raising:
            @staticmethod
            def tessellate(_tolerance):
                raise RuntimeError("unmeshable face")

        real = self._fake_part(monkeypatch, _Raising())
        assert part_material_mesh(real, 1.0) is None

    def test_a_non_finite_vertex_yields_no_mesh(self, monkeypatch):
        from draftwright.projection import part_material_mesh

        vertices = [
            self._vertex(float("nan"), 0.0, 0.0),
            self._vertex(1.0, 0.0, 0.0),
            self._vertex(0.0, 1.0, 0.0),
        ]

        class _NonFinite:
            @staticmethod
            def tessellate(_tolerance):
                return vertices, [(0, 1, 2)]

        real = self._fake_part(monkeypatch, _NonFinite())
        assert part_material_mesh(real, 1.0) is None

    def test_a_facet_referencing_a_missing_vertex_yields_no_mesh(self, monkeypatch):
        from draftwright.projection import part_material_mesh

        vertices = [
            self._vertex(0.0, 0.0, 0.0),
            self._vertex(1.0, 0.0, 0.0),
            self._vertex(0.0, 1.0, 0.0),
        ]

        class _BadIndex:
            @staticmethod
            def tessellate(_tolerance):
                return vertices, [(0, 1, 7)]

        real = self._fake_part(monkeypatch, _BadIndex())
        assert part_material_mesh(real, 1.0) is None
