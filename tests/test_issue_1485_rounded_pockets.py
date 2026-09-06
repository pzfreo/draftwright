"""Released mixed-boundary pockets retain their corner measurements and ownership."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, Pos, RectangleRounded, extrude

from draftwright import build_drawing
from draftwright.linting.pocket_coverage import pocket_requirement_outcomes
from draftwright.linting.pocket_pattern_coverage import pocket_pattern_requirement_outcomes
from draftwright.section_recess_contract import UnsupportedSectionRecess, section_recess_fields
from draftwright.sheet_emit import emit_sheet_script


@pytest.fixture(scope="module")
def tuner():
    drawing = build_drawing(
        Path(__file__).parent / "fixtures/tuner_jig_blind_obround_pockets.step"
    )
    assert len(drawing.recognition().section_recesses) == 5
    assert drawing.recognition().section_recess_refusals == ()
    assert {r.classification.section_shape for r in drawing.recognition().section_recesses} == {
        "general"
    }
    return drawing


def test_tuner_preserves_the_five_rounded_profiles_and_their_radius(tuner):
    (pattern,) = [f for f in tuner.model().features if f.kind == "pocket_pattern"]
    assert pattern.count == 5
    assert pattern.member.corner_radius == pytest.approx(3.94)
    assert (pattern.member.width, pattern.member.length, pattern.member.depth) == pytest.approx(
        (7.9, 13.6, 19)
    )
    assert sorted(p[1] for p in pattern.members) == pytest.approx([21.2, 48.4, 75.6, 102.8, 130])
    ownership = tuner.recognition_ownership()
    evidence = ownership.evidence
    sources = tuner.recognition().section_recesses
    refs = [
        ref
        for ref in evidence.features
        if any(evidence.record(ref) is source for source in sources)
    ]
    assert len(refs) == 5
    assert all(ownership.binding_for(ref) is not None for ref in refs)
    rows = [
        r
        for r in tuner.report()["recognition"]["requirements"]
        if r["family"] == "pocket_patterns"
    ]
    assert len(rows) == 7 and {r["state"] for r in rows} == {"placed"}
    (width,) = [r for r in rows if r["parameter_id"] == "pocket_width.length"]
    assert len(width["occurrence_ids"]) == 5
    (name,) = width["annotations"]
    assert tuner.get_annotation(name).label == "5× 7.9 × 13.6 × 19 DEEP"
    radii = [r for r in tuner.report()["recognition"]["requirements"] if r["family"] == "blends"]
    assert len(radii) == 20 and {r["state"] for r in radii} == {"placed"}
    names = {name for r in radii for name in r["annotations"]}
    assert len(names) == 1
    assert tuner.get_annotation(names.pop()).label == "20× R3.9"


def test_tuner_pitch_and_absolute_locations_form_a_complete_location_scheme(tuner):
    assert not [i for i in tuner.lint() if i.code == "pocket_not_located"]


def test_a_changed_pattern_radius_cannot_certify_the_original_profiles(tuner):
    pattern = next(f for f in tuner.model().features if f.kind == "pocket_pattern")
    changed = replace(pattern, member=replace(pattern.member, corner_radius=3.0))
    outcomes = pocket_pattern_requirement_outcomes(tuner.recognition(), (changed,), tuner.registry)
    assert len(outcomes) == 1 and outcomes[0].state == "unverifiable"
    assert outcomes[0].requirement_count == 7
    assert all(
        a is b
        for a, b in zip(
            outcomes[0].source_records, tuner.recognition().section_recesses, strict=True
        )
    )


@pytest.fixture(scope="module")
def rounded():
    part = Box(80, 60, 20) - Pos(0, 0, 5) * extrude(RectangleRounded(30, 20, 3), 10)
    drawing = build_drawing(part)
    (source,) = drawing.recognition().section_recesses
    assert source.classification.section_shape == "general"
    (feature,) = [f for f in drawing.model().features if f.kind == "pocket"]
    assert (feature.width, feature.length, feature.depth, feature.corner_radius) == (20, 30, 5, 3)
    return part, drawing, feature


def test_rounded_pocket_script_round_trip_and_export(rounded, tmp_path):
    part, drawing, feature = rounded
    source = emit_sheet_script(
        drawing.model(),
        "part = input_part",
        str(tmp_path / "rounded"),
        title="Rounded pocket",
        number="1485",
        formats=(),
    )
    assert "corner_radius=3" in source
    namespace = {"__name__": "__rounded_test__", "input_part": part}
    exec(compile(source, "<rounded pocket>", "exec"), namespace)
    rebuilt = namespace["drawing"]
    assert feature in rebuilt.model().features
    rows = [r for r in drawing.report()["recognition"]["requirements"] if r["family"] == "pockets"]
    assert len(rows) == 5 and {r["state"] for r in rows} == {"placed"}
    outcomes = pocket_requirement_outcomes(
        rebuilt.recognition(), rebuilt.model().features, rebuilt.registry
    )
    assert len(outcomes) == 5 and {row.state for row in outcomes} == {"placed"}
    paths = rebuilt.export(str(tmp_path / "rounded"), formats=("svg",))
    assert Path(paths["svg"]).stat().st_size > 0


def test_omitted_ir_pocket_keeps_the_physical_corner_requirement(rounded):
    _, drawing, _ = rounded
    (outcome,) = pocket_requirement_outcomes(drawing.recognition(), (), drawing.registry)
    assert outcome.state == "unverifiable" and outcome.requirement_count == 5


@pytest.mark.parametrize("fault", ["unequal_corner", "major_arc", "missing_corner"])
def test_mixed_profiles_cannot_be_flattened_into_a_rounded_rectangle(rounded, fault):
    _, drawing, _ = rounded
    source = drawing.recognition().section_recesses[0]
    assert section_recess_fields(source)[1]["corner_radius"] == 3
    altered = deepcopy(source)
    vertex = next(v for v in altered.geometry.profile.boundary if v.bulge)
    if fault == "unequal_corner":
        object.__setattr__(vertex, "point", (vertex.point[0] + 0.2, vertex.point[1]))
    else:
        object.__setattr__(vertex, "bulge", 2.414213562373 if fault == "major_arc" else 0.0)
    with pytest.raises((UnsupportedSectionRecess, ValueError)):
        section_recess_fields(altered)


def test_corner_radius_remains_editable_through_its_blend_measurement(rounded):
    part, drawing, _ = rounded
    blends = [f for f in drawing.model().features if f.kind == "blend"]
    assert len(blends) == 4 and {f.radius for f in blends} == {3}
    edited = replace(drawing.model(), decorations={(blends[0], "radius", "blend"): (0, 0.1)})
    rebuilt = build_drawing(part, model=edited)
    labels = [
        annotation.label
        for name, annotation in rebuilt.iter_annotations()
        if name.startswith("m_blend")
    ]
    assert sorted(labels) == ["3× R3", "R3 +0.1 -0.0"]
    assert not any(
        "R3" in annotation.label
        for name, annotation in rebuilt.iter_annotations()
        if name.startswith("m_pocket_")
    )


def test_removing_pattern_pitch_reopens_the_member_location_warning():
    drawing = build_drawing(
        Path(__file__).parent / "fixtures/tuner_jig_blind_obround_pockets.step"
    )
    (pitch,) = [name for name in drawing.annotations() if name.startswith("dim_pocketpat_pitch")]
    assert not [i for i in drawing.lint() if i.code == "pocket_not_located"]
    drawing.remove(pitch)
    assert [i for i in drawing.lint() if i.code == "pocket_not_located"]
    rows = [
        r
        for r in drawing.report()["recognition"]["requirements"]
        if r["family"] == "pocket_patterns"
    ]
    assert any(row["parameter_id"] == "pitch.length" and row["state"] == "missing" for row in rows)


def test_removing_the_radius_callout_retains_all_four_corner_requirements(rounded):
    part, _, _ = rounded
    drawing = build_drawing(part)
    (name,) = [n for n in drawing.annotations() if n.startswith("m_blend")]
    assert drawing.get_annotation(name).label == "4× R3"
    assert len(drawing.measurement_keys(name)) == 4
    drawing.remove(name)
    rows = [r for r in drawing.report()["recognition"]["requirements"] if r["family"] == "blends"]
    assert len(rows) == 4 and {r["state"] for r in rows} == {"missing"}
    assert not any(
        "R3" in annotation.label
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_pocket_")
    )


def test_valid_unequal_tangent_corners_cannot_be_reported_as_one_radius(rounded):
    _, drawing, _ = rounded
    source = drawing.recognition().section_recesses[0]
    template = source.geometry.profile.boundary[0]
    # Opposite corners have R2 and R3. The profile is centred and all arcs are
    # tangent quarter circles, but one shared corner radius cannot describe it.
    points = ((-15, -8), (-13, -10), (12, -10), (15, -7), (15, 8), (13, 10), (-12, 10), (-15, 7))
    vertices = tuple(
        replace(template, point=point, bulge=0.414213562373 if i % 2 == 0 else 0.0)
        for i, point in enumerate(points)
    )
    profile = replace(source.geometry.profile, boundary=vertices)
    altered = replace(source, geometry=replace(source.geometry, profile=profile))
    assert altered.geometry.profile is profile
    assert section_recess_fields(source)[1]["corner_radius"] == 3
    with pytest.raises(UnsupportedSectionRecess, match="equal tangent corners"):
        section_recess_fields(altered)


def test_authored_corner_geometry_keeps_its_precision_in_a_generated_declaration(
    rounded, tmp_path
):
    part, drawing, feature = rounded
    changed = replace(feature, corner_radius=3.141592653589793)
    model = replace(
        drawing.model(),
        features=[changed if f == feature else f for f in drawing.model().features],
    )
    source = emit_sheet_script(
        model,
        "part = input_part",
        str(tmp_path / "authored-radius"),
        title="Authored rounded profile",
        number="1485",
        formats=(),
    )
    namespace = {"__name__": "__authored_radius_test__", "input_part": part}
    exec(compile(source, "<authored rounded profile>", "exec"), namespace)
    rebuilt = namespace["drawing"]
    (actual,) = [f for f in rebuilt.model().features if f.kind == "pocket"]
    assert actual == changed
    (outcome,) = pocket_requirement_outcomes(
        rebuilt.recognition(), rebuilt.model().features, rebuilt.registry
    )
    assert outcome.state == "unverifiable" and outcome.requirement_count == 5
