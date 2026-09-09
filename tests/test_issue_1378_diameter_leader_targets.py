"""Diameter leaders must touch the physical boundary of the hole they name."""

from dataclasses import replace

import pytest
from build123d import Box, Cylinder, GeomType, Pos, Rot, Vertex

from draftwright import build_drawing


def _hole_leader(drawing):
    (feature,) = [f for f in drawing.model().features if f.kind == "hole"]
    (name,) = [
        name
        for name in drawing.annotations_of(feature)
        if hasattr(drawing.get_annotation(name), "tip")
    ]
    return feature, name, drawing.get_annotation(name)


def _physical_gap(drawing):
    _, name, leader = _hole_leader(drawing)
    view = drawing.view_of(name)
    arcs = [
        edge
        for edge in drawing.working_part.edges()
        if edge.geom_type == GeomType.CIRCLE and abs(edge.radius - 4) < 1e-6
    ]
    assert len(arcs) == 2, "fixture must have exactly the two bore rims"
    distances = []
    for arc in arcs:
        centre = arc.arc_center
        u = arc.position_at(0) - centre
        v = arc.tangent_at(0) * arc.radius
        c = drawing.at(view, *centre)
        pu = drawing.at(view, *(centre + u))
        pv = drawing.at(view, *(centre + v))
        ux, uy = pu[0] - c[0], pu[1] - c[1]
        vx, vy = pv[0] - c[0], pv[1] - c[1]
        dx, dy = leader.tip[0] - c[0], leader.tip[1] - c[1]
        determinant = ux * vy - uy * vx
        assert abs(determinant) > 1e-6, "the diameter view must expose the bore rim"
        point = (
            centre
            + u * ((dx * vy - dy * vx) / determinant)
            + v * ((ux * dy - uy * dx) / determinant)
        )
        distances.append(arc.distance_to(Vertex(point)))
    return min(distances)


@pytest.mark.parametrize("margin", [1, 2])
@pytest.mark.parametrize("scale", [1, 2, 4])
def test_automatic_diameter_tip_stays_on_bore_rim_near_sheet_strip(margin, scale):
    part = Box(20, 20, 5) - Pos(6 - margin, 0, 0) * Cylinder(4, 5)
    drawing = build_drawing(part, scale=scale)
    assert _physical_gap(drawing) < 1e-6
    assert not [i for i in drawing.lint() if i.code.startswith("diameter_leader_target_")]


@pytest.mark.parametrize("mode", ["declared", "live", "deferred"])
@pytest.mark.parametrize("rotation", [(0, 0, 0), (0, 90, 0), (90, 0, 0)])
def test_diameter_target_survives_declared_and_edit_paths(mode, rotation):
    part = Rot(*rotation) * (Box(20, 20, 5) - Pos(5, 0, 0) * Cylinder(4, 5))
    drawing = build_drawing(part, scale=2)
    feature, _, _ = _hole_leader(drawing)
    if mode == "declared":
        drawing = build_drawing(drawing.working_part, model=drawing.model(), scale=2)
    else:
        drawing.drop(feature)
        if mode == "live":
            drawing.callout(feature)
        else:
            with drawing.deferred():
                drawing.callout(feature)
    assert _physical_gap(drawing) < 1e-6
    assert not [i for i in drawing.lint() if i.code.startswith("diameter_leader_target_")]


def test_lint_rejects_tip_in_blank_material_and_lowers_fidelity(monkeypatch):
    drawing = build_drawing(Box(20, 20, 5) - Pos(5, 0, 0) * Cylinder(4, 5), scale=2)
    _, name, leader = _hole_leader(drawing)
    before = drawing.lint_summary()["quality"]["fidelity"]
    tip = drawing.at(drawing.view_of(name), 8.65, 0, 0)
    monkeypatch.setattr(
        leader,
        "location",
        Pos(tip[0] - leader.tip[0], tip[1] - leader.tip[1], 0) * leader.location,
    )
    assert _physical_gap(drawing) == pytest.approx(0.35)
    issues = [i for i in drawing.lint() if i.code == "diameter_leader_target_mismatch"]
    assert len(issues) == 1 and name in issues[0].message
    assert issues[0].measurement_ids == drawing.registry.measurement_of(name)
    assert issues[0].location == leader.tip
    after = drawing.lint_summary()["quality"]["fidelity"]
    assert after["score"] < before["score"]
    assert after["by_code"]["diameter_leader_target_mismatch"] == 1


@pytest.mark.parametrize("withdraw", ["faces", "ownership", "foreign_evidence", "owner", "view"])
def test_lint_requires_the_named_holes_physical_authority(monkeypatch, withdraw):
    from draftwright.linting.hole_coverage import (
        hole_requirement_outcomes,
        lint_hole_leader_targets,
    )

    drawing = build_drawing(Box(20, 20, 5) - Pos(5, 0, 0) * Cylinder(4, 5), scale=2)
    evidence = drawing.recognition_evidence()
    kwargs = dict(
        registry=drawing.registry,
        outcomes=hole_requirement_outcomes(
            drawing.recognition(), drawing.model().features, drawing.registry
        ),
        project=drawing.at,
        evidence=evidence,
        ownership=drawing.recognition_ownership(),
        recognition=drawing.recognition(),
    )
    assert lint_hole_leader_targets(**kwargs) == []
    if withdraw == "faces":
        monkeypatch.setattr(type(evidence), "defining_faces", lambda *_: ())
    elif withdraw == "ownership":
        kwargs["ownership"] = None
    elif withdraw == "foreign_evidence":
        kwargs["evidence"] = object()
    elif withdraw == "owner":
        monkeypatch.setattr(drawing.registry, "feature_of", lambda *_: None)
    else:
        monkeypatch.setattr(drawing.registry, "view_of", lambda *_: None)
    (issue,) = lint_hole_leader_targets(**kwargs)
    assert issue.code == "diameter_leader_target_unverifiable"
    assert issue.measurement_ids
    _, name, _ = _hole_leader(drawing)
    assert issue.annotation_name == name
    assert issue.view == drawing.view_of(name)
    expected = {
        "faces": "named occurrence has no defining boundary edges",
        "ownership": "exact physical occurrence ownership unavailable",
        "foreign_evidence": "same-run recognition evidence unavailable",
        "owner": "exact physical occurrence ownership unavailable",
        "view": "annotation view unavailable",
    }
    assert issue.evidence_reason == expected[withdraw]


@pytest.mark.parametrize("declared", [False, True])
def test_double_d_target_uses_trimmed_profile_including_flats(declared, monkeypatch):
    part = Box(30, 30, 10) - (Cylinder(5, 20) & Box(7.2, 30, 20))
    drawing = build_drawing(part)
    if declared:
        drawing = build_drawing(drawing.working_part, model=drawing.model())
    feature, name, leader = _hole_leader(drawing)
    assert feature.profile == "double_d" and feature.across_flats == 7.2
    assert not [i for i in drawing.lint() if i.code.startswith("diameter_leader_target_")]
    # The missing parent-circle portion is not a physical Double-D boundary.
    tip = drawing.at(drawing.view_of(name), 5, 0, 0)
    monkeypatch.setattr(
        leader,
        "location",
        Pos(tip[0] - leader.tip[0], tip[1] - leader.tip[1], 0) * leader.location,
    )
    assert any(i.code == "diameter_leader_target_mismatch" for i in drawing.lint())


@pytest.mark.parametrize("grouped", [False, True])
def test_split_declared_holes_cannot_claim_the_other_members_rim(monkeypatch, grouped):
    part = Box(50, 30, 5) - Pos(-10, 0, 0) * Cylinder(4, 5) - Pos(10, 0, 0) * Cylinder(4, 5)
    automatic = build_drawing(part)
    (group,) = [f for f in automatic.model().features if f.kind == "hole"]
    assert group.count == 2
    singles = [
        replace(
            group,
            frame=replace(group.frame, origin=point),
            count=1,
            members=(point,),
            through_indicator=None if grouped else ("THRU", "THROUGH ALL")[index],
        )
        for index, point in enumerate(group.members)
    ]
    model = replace(
        automatic.model(),
        features=tuple(f for f in automatic.model().features if f is not group) + tuple(singles),
    )
    drawing = build_drawing(automatic.working_part, model=model)
    assert not [i for i in drawing.lint() if i.code.startswith("diameter_leader_target_")]
    (name,) = [
        n for n in drawing.annotations_of(singles[0]) if hasattr(drawing.get_annotation(n), "tip")
    ]
    leader = drawing.get_annotation(name)
    target_owner = drawing.registry.feature_of(name)
    assert target_owner in singles
    if grouped:
        assert (
            len(
                {
                    n
                    for f in singles
                    for n in drawing.annotations_of(f)
                    if hasattr(drawing.get_annotation(n), "tip")
                }
            )
            == 1
        )
        assert leader.covers_count == 2
    other = next(feature for feature in singles if feature is not target_owner).frame.origin
    tip = drawing.at(drawing.view_of(name), other[0] + 4, other[1], other[2])
    monkeypatch.setattr(
        leader,
        "location",
        Pos(tip[0] - leader.tip[0], tip[1] - leader.tip[1], 0) * leader.location,
    )
    issues = [i for i in drawing.lint() if i.code == "diameter_leader_target_mismatch"]
    assert len(issues) == 1 and name in issues[0].message


@pytest.mark.parametrize(
    "defect", ["diameter", "site", "axis", "duplicate_record", "duplicate_owner"]
)
def test_declared_profile_target_requires_precise_unique_physical_support(defect):
    from draftwright.linting.profiled_bore_coverage import profiled_bore_target_sources

    drawing = build_drawing(Box(30, 30, 10) - (Cylinder(5, 20) & Box(7.2, 30, 20)))
    feature, _, _ = _hole_leader(drawing)
    (record,) = drawing.recognition().double_d_bores
    features, records = (feature,), (record,)
    assert profiled_bore_target_sources(features, records) == {id(feature): records}
    if defect == "diameter":
        records = (replace(record, major_diameter=record.major_diameter + 0.0004),)
    elif defect == "site":
        records = (replace(record, location=(0.0004, *record.location[1:])),)
    elif defect == "axis":
        records = (replace(record, axis=(0.6, 0, 0.8)),)
    elif defect == "duplicate_record":
        records = (record, replace(record))
    else:
        features = (feature, replace(feature))
    assert profiled_bore_target_sources(features, records) == {}
