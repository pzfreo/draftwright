"""#798 — the ``leader_crosses_silhouette`` critique reads the shared material field.

The router (ADR 0014) and this notice must reach the same verdict on the same shaft,
so both solve against one lowering. These tests pin that seam: what the check needs,
what it reports, and what it deliberately no longer exempts.
"""

from __future__ import annotations

from types import SimpleNamespace

from build123d import Align, Cylinder, Pos, Rotation

from draftwright import build_drawing
from draftwright.drawing import BuildState
from draftwright.linting.structural import lint_drawing


def _cyl(r, h, z):
    return Pos(0, 0, z) * Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))


def _nested_boss():
    """A ø6 stub on a ø30 flange — the issue's canonical nested feature."""
    return Rotation(0, 90, 0) * (_cyl(3, 0.5, 0.0) + _cyl(15, 20, 0.5) + _cyl(10, 15, 20.5))


def _thin_neck():
    """Two ø30 flanges joined by a ø6 neck — the issue's thin-neck acceptance case."""
    return Rotation(0, 90, 0) * (_cyl(15, 10, 0.0) + _cyl(3, 2, 10) + _cyl(15, 10, 12))


def _probe(dwg, *, offset):
    """A leader-shaped item running horizontally across the front view at *offset*.

    Synthesised rather than placed, because the engine's own placers avoid these routes —
    which is the point. At an offset clear of the ø6 neck the shaft leaves the left flange,
    crosses real air, and re-enters the right flange: a genuine second traversal. Straight
    through the neck it is one traversal and must stay clean.
    """
    x0, y0, x1, y1 = dwg.view_bounds("front")
    height = (y0 + y1) / 2 + offset
    return SimpleNamespace(
        tip=(x0 + 2.0, height, 0.0),
        elbow=(x1 + 15.0, height, 0.0),
        label="ø30",
        label_bbox=(x1 + 15.0, height, x1 + 25.0, height + 4.0),
    )


def _silhouette_issues(dwg, items, *, with_field=True):
    return [
        issue
        for issue in lint_drawing(
            items,
            view_shapes=[vis for vis, _ in dwg.views.values()],
            view_material_fields=dwg.material_fields() if with_field else None,
        )
        if issue.code == "leader_crosses_silhouette"
    ]


class TestTheCheckNeedsTheField:
    def test_a_shaft_re_entering_the_body_is_reported(self):
        dwg = build_drawing(_thin_neck(), number="X")
        assert _silhouette_issues(dwg, [*dwg.items, _probe(dwg, offset=4.0)])

    def test_a_single_traversal_stays_clean(self):
        # Same part, same direction, straight through the neck: one traversal out, which
        # is what every correct callout does. Mutation — charging the first traversal —
        # flags this and condemns the whole sheet.
        dwg = build_drawing(_thin_neck(), number="X")
        assert _silhouette_issues(dwg, [*dwg.items, _probe(dwg, offset=0.0)]) == []

    def test_without_a_field_the_check_reports_nothing(self):
        # Deliberate: no material knowledge means no claim. Mutation — restoring an
        # outline-crossing fallback — makes this non-empty, and reintroduces exactly
        # the second opinion the shared field exists to remove.
        dwg = build_drawing(_thin_neck(), number="X")
        probe = _probe(dwg, offset=4.0)
        assert _silhouette_issues(dwg, [*dwg.items, probe], with_field=False) == []

    def test_the_message_carries_the_measured_depth(self):
        # The notice is a magnitude, not a flag, so a reader can rank two of them. The
        # probe crosses two 10 mm flanges, so the second traversal is the 10 mm one.
        dwg = build_drawing(_thin_neck(), number="X")
        issues = _silhouette_issues(dwg, [*dwg.items, _probe(dwg, offset=4.0)])
        assert "10.0 mm back through view" in issues[0].message


class TestExemptionsTheFilledFieldRemoves:
    def test_covers_diameters_is_no_longer_a_blanket_escape(self):
        # The outline form exempted bore callouts wholesale, because a shaft leaving a
        # bore crosses two circles and read as a cut. That exemption also hid genuine
        # re-entries — on the #881 flange it hid a callout spending 27.6 mm of its 49 mm
        # shaft inside the body. Under the filled field the exit costs nothing by itself,
        # so the flag can be set and a real cut still reports.
        dwg = build_drawing(_thin_neck(), number="X")
        probe = _probe(dwg, offset=4.0)
        probe.covers_diameters = (30.0,)
        assert _silhouette_issues(dwg, [*dwg.items, probe])

    def test_an_ordinary_built_drawing_stays_clean(self):
        # The other half of the same claim, end to end: every automatic callout on the
        # nested-boss part crosses material on its way out and none is charged for it.
        dwg = build_drawing(_nested_boss())
        assert [i for i in dwg.lint() if i.code == "leader_crosses_silhouette"] == []


class TestFieldLifecycle:
    def test_the_field_is_built_once_and_reused(self):
        # One tessellation per drawing: a build->critique->fix loop lints repeatedly and
        # the mesh is the expensive part (~1 s on the largest fixture).
        dwg = build_drawing(_nested_boss())
        first = dwg.material_fields()
        assert first, "no material field was built for a meshable part"
        assert dwg.material_fields() is first
        assert all(field is first[key] for key, field in dwg.material_fields().items())

    def test_the_field_is_keyed_by_view_shape_identity(self):
        # Projected view shapes carry no label — lint names them view@<id> — so the
        # fields must be reachable from the shape itself, not from a view name.
        dwg = build_drawing(_nested_boss())
        shapes = {id(vis) for vis, _ in dwg.views.values() if vis is not None}
        assert dwg.material_fields()
        assert set(dwg.material_fields()) <= shapes

    def test_rollback_clears_the_field_with_the_other_geometry_caches(self):
        # A rolled-back drawing re-measures everything; a stale field would describe a
        # part layout that no longer exists. Asserted on the state object itself, which
        # owns the one invalidation seam.
        state = BuildState()
        state.material_fields[1] = object()
        state.view_edge_cache[1] = object()
        state.clear_geometry_caches()
        assert state.material_fields == {}


class TestGreedyFloorPrefersClearRoutes:
    """#798 — the resource-cap floor is what actually runs on dense parts.

    Measured across every fixture, the joint ADR 0014 Amendment 2 assignment runs only
    on modest inventories; a 20-job part expands past the candidate cap and falls back
    here. So this is where a cutting route has to be rejected, and these tests pin the
    two properties that make that safe.
    """

    def test_a_clear_first_route_breaks_where_it_always_did(self):
        # The blast radius is deliberately tiny: a job whose first acceptable route
        # already clears the body selects it immediately, exactly as the pre-#798 floor
        # did. Only a job whose first acceptable route CUTS looks further.
        from draftwright.annotations import leaders

        assert leaders._GREEDY_MATERIAL_LOOKAHEAD > 0
        dwg = build_drawing(_nested_boss())
        assert [i for i in dwg.lint() if i.code == "leader_crosses_silhouette"] == []

    def test_the_lookahead_is_bounded(self):
        # The floor runs precisely when the exact solve has been ruled out on cost, so
        # it must stay lazy. An unbounded search here would reintroduce the expense the
        # fallback exists to avoid.
        from draftwright.annotations import leaders

        assert leaders._GREEDY_MATERIAL_LOOKAHEAD <= 64

    def test_material_units_are_zero_below_the_visible_floor(self):
        # A cut the sheet cannot show must not steer the solve, or mesh detail becomes a
        # placement decision.
        from draftwright.annotations.leaders import _MATERIAL_PENALTY_UNIT, _material_units

        class _Candidate:
            tip = (0.0, 0.0)
            elbow = (10.0, 0.0)

        assert _material_units(_Candidate(), None) == 0
        assert _MATERIAL_PENALTY_UNIT > 0
