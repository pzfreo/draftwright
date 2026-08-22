"""#798 — the ``leader_crosses_silhouette`` critique reads the shared material field.

The router (ADR 0014) and this notice must reach the same verdict on the same shaft,
so both solve against one lowering. These tests pin that seam: what the check needs,
what it reports, and what it deliberately no longer exempts.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
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


@pytest.fixture(scope="module")
def thin_neck_drawing():
    """One thin-neck build shared by the read-only probe critiques below (#1226 review).

    Every consumer synthesises its own probe and only READS the drawing (views,
    material fields, lint); a test that mutates build state must build its own.
    """
    return build_drawing(_thin_neck(), number="X")


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
    def test_a_shaft_re_entering_the_body_is_reported(self, thin_neck_drawing):
        dwg = thin_neck_drawing
        assert _silhouette_issues(dwg, [*dwg.items, _probe(dwg, offset=4.0)])

    def test_a_single_traversal_stays_clean(self, thin_neck_drawing):
        # Same part, same direction, straight through the neck: one traversal out, which
        # is what every correct callout does. Mutation — charging the first traversal —
        # flags this and condemns the whole sheet.
        dwg = thin_neck_drawing
        assert _silhouette_issues(dwg, [*dwg.items, _probe(dwg, offset=0.0)]) == []

    def test_without_a_field_the_check_reports_nothing(self, thin_neck_drawing):
        # Deliberate: no material knowledge means no claim. Mutation — restoring an
        # outline-crossing fallback — makes this non-empty, and reintroduces exactly
        # the second opinion the shared field exists to remove.
        dwg = thin_neck_drawing
        probe = _probe(dwg, offset=4.0)
        assert _silhouette_issues(dwg, [*dwg.items, probe], with_field=False) == []

    def test_the_message_carries_the_measured_depth(self, thin_neck_drawing):
        # The notice is a magnitude, not a flag, so a reader can rank two of them. The
        # probe crosses two 10 mm flanges, so the second traversal is the 10 mm one.
        dwg = thin_neck_drawing
        issues = _silhouette_issues(dwg, [*dwg.items, _probe(dwg, offset=4.0)])
        assert "10.0 mm back through view" in issues[0].message


class TestExemptionsTheFilledFieldRemoves:
    def test_covers_diameters_is_no_longer_a_blanket_escape(self, thin_neck_drawing):
        # The outline form exempted bore callouts wholesale, because a shaft leaving a
        # bore crosses two circles and read as a cut. That exemption also hid genuine
        # re-entries — on the #881 flange it hid a callout spending 27.6 mm of its 49 mm
        # shaft inside the body. Under the filled field the exit costs nothing by itself,
        # so the flag can be set and a real cut still reports.
        dwg = thin_neck_drawing
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
        # the mesh is the expensive part (~1 s on the largest fixture). The RETURNED dict
        # is rebuilt each call on purpose — it is reconciled against the current views, so
        # a view added by a later stage is not permanently missing — but the fields inside
        # it, and the mesh behind them, are the same objects.
        dwg = build_drawing(_nested_boss())
        first = dwg.material_fields()
        assert first, "no material field was built for a meshable part"
        again = dwg.material_fields()
        assert set(again) == set(first)
        assert all(again[key] is field for key, field in first.items())

    def test_a_view_added_after_the_first_call_is_lowered_too(self):
        # The routing stage asks for the fields before the section/detail stages run, so
        # a build-once cache would leave every later view permanently unmeasured and its
        # leaders silently unchecked. Mutation: caching on first call makes this KeyError.
        dwg = build_drawing(_nested_boss())
        before = dwg.material_fields()
        extra = next(iter(dwg.views))
        later, _hidden = dwg.views[extra]
        dwg.views["detail_probe"] = (later, None)
        try:
            after = dwg.material_fields()
        finally:
            del dwg.views["detail_probe"]
        assert set(after) >= set(before)

    def test_a_replaced_view_shape_is_not_served_from_the_stale_entry(self):
        # id() is reused after collection, so a cache keyed on it alone can hand back
        # another view's material. Entries carry their shape and are identity-checked,
        # the same guard _view_edge_entries uses (#143).
        dwg = build_drawing(_nested_boss())
        first = dwg.material_fields()
        view = next(iter(dwg.views))
        shape, hidden = dwg.views[view]
        key = id(shape)
        assert key in first
        # Forge a stale entry under the same key with a different owner object.
        dwg._build.material_fields[key] = (object(), "stale")
        assert dwg.material_fields()[key] != "stale"

    def test_the_field_is_keyed_by_view_shape_identity(self):
        # Projected view shapes carry no label — lint takes their names from
        # `Drawing.views` since #1196 — so the
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


class TestCardinalityIsNotTradedForCleanliness:
    """#798 — a crossing is cosmetic; a missing dimension is not.

    Preferring a clear route can only ever reorder a job's own alternatives, never
    reject one: material is a preference in the resource-cap floor, never an
    acceptance test, so no callout is dropped *because* its route cuts. The residual
    risk is second-order — placement is sequential, so a re-chosen winner is a
    different obstacle for later jobs, and one of those could fail to place.

    Measured across every fixture that reaches the floor, the placed count is unchanged by
    the route preference. These fixtures pin that per part, so a future change that does start
    trading callouts for tidiness fails here and names the part, rather than being noticed on a
    drawing. The integer is deliberately annotation cardinality, not physical-feature
    cardinality: #1254 collapses CTC-04's eight equal chamfers into one truthful ``8× C5``
    annotation carrying all eight measurement identities, so that inventory has seven fewer
    jobs without losing seven requirements. The grouped-provenance invariant is asserted
    separately in ``test_issue_1254_turned_chamfers.py`` and ``test_audit_differential.py``.
    """

    # (fixture stem, feature-leader callouts the floor must keep placing)
    CASES = (
        ("nist_ctc_04_asme1_ap203", 11),
        ("nist_ctc_05_asme1_ap242", 18),
    )

    # Parametrized per fixture (#656): each dense build is ~60 s, and one test running
    # them serially set the wall-clock floor for the whole suite under --dist loadscope.
    @pytest.mark.slow  # >=30 s inherent dense build; post-merge tier (#656)
    @pytest.mark.parametrize(("stem", "expected"), CASES)
    def test_the_dense_fixtures_place_the_same_callouts_as_before(
        self, tmp_path, monkeypatch, stem, expected
    ):
        import json
        from pathlib import Path

        trace = tmp_path / f"{stem}.json"
        monkeypatch.setenv("DRAFTWRIGHT_TRACE", str(trace))
        build_drawing(step_file=str(Path("tests/fixtures") / f"{stem}.stp"))
        events = json.loads(trace.read_text())["pass_events"]
        event = next(e for e in events if e.get("label") == "feature_leader_inventory")
        assert event["objective"]["placed"] == expected, (
            f"{stem}: the leader floor placed {event['objective']['placed']} callouts, "
            f"not {expected} — a route preference must never cost a dimension"
        )

    def test_material_is_never_an_acceptance_test(self):
        # The direct guarantee, read off the code rather than a fixture: the floor's
        # accept decision is computed before material is consulted, so a cutting route
        # stays eligible (Policy B) and can still be selected when nothing else is.
        import inspect

        from draftwright.annotations import leaders

        source = inspect.getsource(leaders.drain_feature_leaders)
        accept = source.index("accepted = not hard_blockers")
        units = source.index("units = _material_units(candidate, field)")
        assert accept < units, "material must not participate in the acceptance decision"


class TestTheFieldDegradesHonestly:
    def test_an_unmeshable_part_yields_no_fields_rather_than_empty_ones(self, monkeypatch):
        # "No material known" and "no material" must not be the same value: an empty dict
        # makes every consumer skip the check, where a dict of empty fields would make
        # them all conclude the routes are clear.
        import draftwright.drawing as drawing_module

        monkeypatch.setattr(drawing_module, "part_material_mesh", lambda *_a, **_k: None)
        dwg = build_drawing(_nested_boss())
        assert dwg.material_fields() == {}
        assert [i for i in dwg.lint() if i.code == "leader_crosses_silhouette"] == []

    def test_the_failed_mesh_is_not_retried_on_every_call(self, monkeypatch):
        # A failure is a cacheable outcome. Re-meshing an unmeshable part on every lint
        # would pay the most expensive step repeatedly for the same answer.
        import draftwright.drawing as drawing_module

        calls = []

        def counted(*_args, **_kwargs):
            calls.append(1)
            return None

        monkeypatch.setattr(drawing_module, "part_material_mesh", counted)
        dwg = build_drawing(_nested_boss())
        attempted = len(calls)
        assert attempted == 1, "the mesh should be attempted once during the build"
        dwg.material_fields()
        dwg.material_fields()
        assert len(calls) == attempted
