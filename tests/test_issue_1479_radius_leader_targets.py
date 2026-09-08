"""GRM04 radius leaders must land on the named trimmed profile arcs."""

from copy import deepcopy
from pathlib import Path
from runpy import run_path

import pytest
from build123d import GeomType, Pos, Vertex

from draftwright import build_drawing
from draftwright.sheet_emit import generate_sheet_script

FIXTURE = Path(__file__).parent / "fixtures" / "grm04_drive_plate.step"


@pytest.fixture(scope="module")
def automatic():
    return build_drawing(FIXTURE, title="GRM-04")


def _radius_leaders(drawing):
    features = [f for f in drawing.model().features if f.kind == "blend"]
    assert sorted(f.radius for f in features) == [3.0, 4.0]
    result = []
    for feature in features:
        names = drawing.annotations_of(feature)
        assert len(names) == 1
        name = next(iter(names))
        assert drawing.view_of(name) == "side"
        result.append((feature, name, drawing.get_annotation(name)))
    return result


def _assert_on_profile_arcs(drawing):
    for feature, name, leader in _radius_leaders(drawing):
        # Independently lift the page tip onto each physical circle plane. Testing the
        # trimmed OCC edge rejects both the bore rim and the missing half of the R3 circle.
        origin = drawing.at("side", 0, 0, 0)
        unit_y = drawing.at("side", 0, 1, 0)[0] - origin[0]
        unit_z = drawing.at("side", 0, 0, 1)[1] - origin[1]
        y = (leader.tip[0] - origin[0]) / unit_y
        z = (leader.tip[1] - origin[1]) / unit_z
        arcs = [
            edge
            for edge in drawing.working_part.edges()
            if edge.geom_type == GeomType.CIRCLE
            and abs(edge.radius - feature.radius) < 1e-6
            and abs(edge.arc_center.Z - feature.frame.origin[2]) < 1e-6
        ]
        assert arcs
        distance = min(edge.distance_to(Vertex(edge.arc_center.X, y, z)) for edge in arcs)
        assert distance < 1e-6, (name, leader.tip, distance)


def test_automatic_radius_tips_land_on_trimmed_profile_arcs(automatic):
    _assert_on_profile_arcs(automatic)


def test_generated_authored_radius_tips_land_on_trimmed_profile_arcs(tmp_path):
    script = generate_sheet_script(str(FIXTURE), out=str(tmp_path / "grm04"))
    drawing = run_path(str(script))["drawing"]
    _assert_on_profile_arcs(drawing)


@pytest.mark.parametrize("wrong_site", [(0, 0, 9.5), (0, 2.1, 9.5), (0, 0, 6.5)])
def test_lint_rejects_centre_bore_and_untrimmed_circle_extension(
    automatic, monkeypatch, wrong_site
):
    feature, name, leader = next(row for row in _radius_leaders(automatic) if row[0].radius == 3)
    tip = automatic.at("side", *wrong_site)[:2]
    monkeypatch.setattr(
        leader,
        "location",
        Pos(tip[0] - leader.tip[0], tip[1] - leader.tip[1], 0) * leader.location,
    )
    issues = [issue for issue in automatic.lint() if issue.code == "radius_leader_target_mismatch"]
    assert len(issues) == 1
    assert name in issues[0].message
    assert issues[0].location == tip
    assert issues[0].measurement_ids == automatic.registry.measurement_of(name)


@pytest.mark.parametrize("mode", ["live", "deferred", "retained"])
def test_editing_keeps_radius_tips_on_the_profile(mode):
    drawing = build_drawing(FIXTURE)
    features = [feature for feature, _, _ in _radius_leaders(drawing)]
    if mode == "retained":
        for _, name, _ in _radius_leaders(drawing):
            drawing.pin(name)
        with drawing.deferred():
            drawing.locate(next(f for f in drawing.model().features if f.kind == "hole"))
    else:
        for feature in features:
            drawing.drop(feature)
        if mode == "live":
            for feature in features:
                drawing.callout(feature)
        else:
            with drawing.deferred():
                for feature in features:
                    drawing.callout(feature)
    _assert_on_profile_arcs(drawing)
    assert not [
        issue for issue in drawing.lint() if issue.code.startswith("radius_leader_target_")
    ]


def _arc_gap(drawing, view, tip, arc):
    centre = arc.arc_center
    u = arc.position_at(0) - centre
    v = arc.tangent_at(0) * arc.radius
    c = drawing.at(view, *centre)
    pu = drawing.at(view, *(centre + u))
    pv = drawing.at(view, *(centre + v))
    ux, uy = pu[0] - c[0], pu[1] - c[1]
    vx, vy = pv[0] - c[0], pv[1] - c[1]
    dx, dy = tip[0] - c[0], tip[1] - c[1]
    determinant = ux * vy - uy * vx
    assert abs(determinant) > 1e-6
    point = (
        centre + u * ((dx * vy - dy * vx) / determinant) + v * ((ux * dy - uy * dx) / determinant)
    )
    return arc.distance_to(Vertex(point))


@pytest.mark.parametrize("rotation", [(0, 0, 0), (0, 90, 0), (90, 0, 0), (17, 31, 43)])
@pytest.mark.parametrize("projection", [None, "third", "first"])
def test_radius_targets_follow_rotated_translated_geometry(rotation, projection):
    from build123d import Axis, Box, Rot, fillet

    stock = Box(27, 19, 13)
    part = Pos(30, -20, 40) * Rot(*rotation) * fillet([stock.edges().filter_by(Axis.Z)[0]], 0.4)
    drawing = build_drawing(part, projection=projection)
    (feature,) = [f for f in drawing.model().features if f.kind == "blend"]
    (name,) = drawing.annotations_of(feature)
    annotation = drawing.get_annotation(name)
    view = drawing.view_of(name)
    arcs = [edge for edge in part.edges() if edge.geom_type == GeomType.CIRCLE]
    assert len(arcs) == 2
    assert min(_arc_gap(drawing, view, annotation.tip, arc) for arc in arcs) < 1e-6
    assert not [
        issue for issue in drawing.lint() if issue.code.startswith("radius_leader_target_")
    ]


@pytest.mark.parametrize(
    "withdraw", ["faces", "ownership", "foreign_evidence", "owner", "wrong_owner", "view"]
)
def test_lint_reports_unverifiable_target_authority(automatic, monkeypatch, withdraw):
    from draftwright.linting.blend_coverage import lint_blend_leader_targets

    evidence = automatic.recognition_evidence()
    ownership = automatic.recognition_ownership()
    registry = automatic.registry
    kwargs = dict(
        registry=registry,
        cylinders=automatic.recognition().cylinders,
        project=automatic.at,
        evidence=evidence,
        ownership=ownership,
    )
    assert lint_blend_leader_targets(**kwargs) == []
    if withdraw == "faces":
        monkeypatch.setattr(type(evidence), "defining_faces", lambda *_: ())
    elif withdraw == "ownership":
        from types import SimpleNamespace

        kwargs["ownership"] = SimpleNamespace(evidence=evidence, bindings=())
    elif withdraw == "foreign_evidence":
        kwargs["evidence"] = object()
    elif withdraw == "owner":
        monkeypatch.setattr(registry, "feature_of", lambda *_: None)
    elif withdraw == "wrong_owner":
        from dataclasses import replace

        kwargs["ownership"] = None
        kwargs["evidence"] = None
        original = registry.feature_of
        owners = {name: replace(original(name)) for _, name, _ in _radius_leaders(automatic)}
        monkeypatch.setattr(registry, "feature_of", lambda name: owners.get(name, original(name)))
    else:
        monkeypatch.setattr(registry, "view_of", lambda *_: None)
    issues = lint_blend_leader_targets(**kwargs)
    assert len(issues) == 2
    assert all(issue.code == "radius_leader_target_unverifiable" for issue in issues)
    assert all(issue.measurement_ids for issue in issues)


@pytest.mark.parametrize("defect", ["radius", "side", "direction", "axis", "station", "ambiguous"])
def test_declared_target_requires_one_matching_physical_support(automatic, defect):
    from draftwright._geometry import _straight_blend_faces

    feature = next(f for f, _, _ in _radius_leaders(automatic) if f.radius == 3)
    cylinders = automatic.recognition().cylinders
    (face,) = _straight_blend_faces(feature, cylinders, feature.radius)
    (support,) = [dict(c) for family in cylinders for c in family if c["face"] is face]
    if defect == "radius":
        support["diameter"] = 4.2
    elif defect == "side":
        support["external"] = False
    elif defect == "direction":
        support["dir_xyz"] = (0, 1, 0)
        support["axis_xyz"] = feature.frame.origin
    elif defect == "axis":
        support["axis_xyz"] = (0, 10, 9.5)
    elif defect == "station":
        support["s_lo"], support["s_hi"] = (10, 20)
    inventory = ((support, support) if defect == "ambiguous" else (support,), ())
    assert _straight_blend_faces(feature, inventory, feature.radius) == ()
    # Exact automatic occurrence authority still resolves its own face in an ambiguous inventory.
    assert _straight_blend_faces(feature, inventory, feature.radius, defining_faces=(face,)) == (
        face,
    )
    assert _straight_blend_faces(feature, cylinders, feature.radius, defining_faces=()) == ()


@pytest.mark.parametrize("defect", ["missing", "ambiguous"])
def test_unverifiable_declared_support_is_an_explicit_placement_drop(automatic, defect):
    from build123d import Compound

    from draftwright import Sheet

    part = automatic.working_part
    if defect == "ambiguous":
        part = Compound(children=[deepcopy(part), deepcopy(part)])
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.blend(axis="x", radius=3, at=(0.0, 0.0, 9.5 if defect == "ambiguous" else 90.0))
    sheet.dimension(handle, "blend.radius")
    with pytest.warns(UserWarning, match="blend_dropped"):
        drawing = sheet.build()
    (feature,) = drawing.model().features
    assert drawing.recognition_evidence() is None
    assert not drawing.annotations_of(feature)
    issue = next(issue for issue in drawing.lint(physical=False) if issue.code == "blend_dropped")
    assert issue.outcome_stage == "placement"
    assert issue.measurement_ids[0].feature is feature
    assert issue.measurement_ids[0].parameter == "blend.radius"


def test_wrong_radius_tip_lowers_fidelity(automatic, monkeypatch):
    _, _, leader = next(row for row in _radius_leaders(automatic) if row[0].radius == 3)
    before = automatic.lint_summary()["quality"]["fidelity"]
    tip = automatic.at("side", 0, 0, 9.5)
    monkeypatch.setattr(
        leader,
        "location",
        Pos(tip[0] - leader.tip[0], tip[1] - leader.tip[1], 0) * leader.location,
    )
    after = automatic.lint_summary()["quality"]["fidelity"]
    assert before["score"] == 1.0
    assert after["score"] < before["score"]
    assert after["by_code"]["radius_leader_target_mismatch"] == 1


def test_profile_edges_require_the_matching_cylindrical_surface(automatic):
    from draftwright._geometry import _blend_profile_arcs

    faces = automatic.working_part.faces()
    arcs = _blend_profile_arcs(faces, 3)
    assert len(arcs) == 2
    assert all(edge.geom_type == GeomType.CIRCLE and edge.radius == 3 for edge in arcs)
    assert _blend_profile_arcs(faces, 30) == ()
