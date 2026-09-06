"""Published recess geometry preserves the existing independently authored drafting sizes."""

from __future__ import annotations

from dataclasses import replace

import pytest
from build123d import (
    Align,
    Box,
    BuildLine,
    BuildSketch,
    Line,
    Pos,
    RadiusArc,
    Vector,
    extrude,
    make_face,
)
from quiddity import SectionRecess, SectionRecessClassification
from quiddity.evidence import build_recognition_evidence

from draftwright.section_recess_contract import (
    UnsupportedSectionRecess,
    section_recess_fields,
)


def _round_bottom():
    with BuildLine() as boundary:
        Line((-5, 0), (5, 0))
        RadiusArc((5, 0), (2, -3), 3)
        Line((2, -3), (-2, -3))
        RadiusArc((-2, -3), (-5, 0), 3)
    with BuildSketch() as sketch:
        make_face(boundary.line)
    return Pos(0, -5, 0) * Box(30, 10, 40) - extrude(sketch.sketch, amount=20, dir=Vector(0, 0, 1))


def _recess(kind):
    if kind == "pocket":
        part = Box(40, 30, 20) - Pos(0, 0, 8) * Box(20, 10, 10)
        expected = dict(
            origin=(0.0, 0.0, 6.5),
            axis="z",
            width_axis="y",
            long_axis="x",
            width=10.0,
            length=20.0,
            depth=7.0,
            w_center=0.0,
            lo=-10.0,
            hi=10.0,
            edge_anchored=False,
            open_sign=1,
        )
    elif kind == "rectangular_blind_slot":
        part = Box(30, 20, 40, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Pos(0, 5, 0) * Box(
            10, 5, 20, align=(Align.CENTER, Align.MIN, Align.MIN)
        )
        expected = dict(
            origin=(0.0, 7.5, 10.0),
            axis="z",
            width_axis="x",
            depth_axis="y",
            open_sign=-1,
            depth_sign=1,
            width=10.0,
            length=20.0,
            depth=5.0,
        )
    elif kind == "round_bottom_blind_slot":
        part = _round_bottom()
        expected = dict(
            origin=(0.0, -1.5, 10.0),
            axis="z",
            width_axis="x",
            depth_axis="y",
            open_sign=1,
            depth_sign=1,
            length=20.0,
            radius=3.0,
            flat_width=4.0,
        )
    else:
        part = Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)
        expected = dict(
            origin=(0.0, 0.0, 7.5),
            axis="x",
            width_axis="y",
            long_axis="x",
            width=20.0,
            w_center=0.0,
            lo=-40.0,
            hi=40.0,
            d_lo=0.0,
            d_hi=15.0,
            open_sign=1,
        )
    evidence = build_recognition_evidence(part, rotational=False)
    result = evidence.result
    assert len(result.section_recesses) == 1
    assert result.section_recess_refusals == ()
    source = result.section_recesses[0]
    refs = [ref for ref in evidence.features if evidence.record(ref) is source]
    assert len(refs) == 1 and evidence.family(refs[0]) == "section_recesses"
    assert evidence.defining_faces(refs[0])
    return kind, source, expected


@pytest.fixture(
    scope="module",
    params=("pocket", "rectangular_blind_slot", "round_bottom_blind_slot", "channel"),
)
def recess(request):
    return _recess(request.param)


def test_released_geometry_matches_authored_sizes_and_retains_original_record(recess):
    kind, source, expected = recess
    before = source.to_dict()
    assert section_recess_fields(source) == (kind, expected)
    assert source.to_dict() == before


def test_nested_record_subclasses_are_not_public_authority(recess):
    _, source, _ = recess

    class ForgedClassification(SectionRecessClassification):
        pass

    altered = replace(
        source,
        classification=ForgedClassification(
            source.classification.feature_kind, source.classification.section_shape
        ),
    )
    assert type(altered) is SectionRecess
    with pytest.raises(TypeError, match="exact public"):
        section_recess_fields(altered)


def test_empty_defining_evidence_cannot_create_drafting_geometry(recess):
    _, source, _ = recess
    altered = replace(source, evidence=replace(source.evidence, defining_faces=()))
    assert altered.evidence.constituent_faces
    with pytest.raises(ValueError, match="defining-face"):
        section_recess_fields(altered)


def test_sloped_ends_refuse_existing_perpendicular_drafting_grammar(recess):
    _, source, _ = recess
    geometry = source.geometry
    altered = replace(
        source,
        geometry=replace(
            geometry,
            ends=replace(
                geometry.ends,
                low=replace(
                    geometry.ends.low,
                    surface=replace(geometry.ends.low.surface, gradient=(0.1, 0.0)),
                ),
            ),
        ),
    )
    assert section_recess_fields(source)
    with pytest.raises(UnsupportedSectionRecess, match="perpendicular"):
        section_recess_fields(altered)


def test_round_bottom_major_arcs_cannot_use_quarter_arc_dimensions():
    _, source, _ = _recess("round_bottom_blind_slot")
    vertices = source.geometry.profile.boundary
    assert tuple(v.bulge for v in vertices) == (0.414213562373, 0.0, 0.414213562373, 0.0)
    altered = replace(
        source,
        geometry=replace(
            source.geometry,
            profile=replace(
                source.geometry.profile,
                boundary=tuple(
                    replace(v, bulge=2.414213562373) if i in (0, 2) else v
                    for i, v in enumerate(vertices)
                ),
            ),
        ),
    )
    with pytest.raises(UnsupportedSectionRecess, match="quarter arcs"):
        section_recess_fields(altered)


@pytest.mark.parametrize("angle", (0, 90))
def test_obround_pocket_measures_arc_extrema_and_preserves_complete_drawing(angle):
    from build123d import Plane, Rot, SlotOverall

    from draftwright import build_drawing

    part = Box(60, 60, 20) - Pos(0, 0, 5) * Rot(0, 0, angle) * extrude(
        Plane.XY * SlotOverall(30, 8), 12
    )
    drawing = build_drawing(part)
    (source,) = drawing.recognition().section_recesses
    assert source.classification.section_shape == "obround"
    assert sorted(v.bulge for v in source.geometry.profile.boundary) == [0.0, 0.0, 1.0, 1.0]
    (pocket,) = [f for f in drawing.model().features if f.kind == "pocket"]
    assert (pocket.width, pocket.length, pocket.depth) == (8.0, 30.0, 5.0)
    assert pocket.hi - pocket.lo == 30.0
    assert drawing.lint() == []
    rows = drawing.report()["recognition"]["requirements"]
    sizes = [
        row
        for row in rows
        if row["family"] == "pockets" and row["parameter_id"].startswith("pocket_")
    ]
    assert len(sizes) == 3 and {row["state"] for row in sizes} == {"placed"}


@pytest.fixture(scope="module")
def refused_drawing():
    from _section_recess_cases import unsupported_roof_recess
    from build123d import Compound

    from draftwright import build_drawing

    # Suspended material violates the constant-section proof. Keep five independent
    # refusals now that the tuner fixture's five rounded pockets are accepted (#1485).
    part = unsupported_roof_recess()
    drawing = build_drawing(Compound(children=[Pos(75 * i, 0, 0) * part for i in range(5)]))
    recognition = drawing.recognition()
    assert recognition.section_recesses == ()
    assert len(recognition.section_recess_refusals) == 5
    assert {r.reason for r in recognition.section_recess_refusals} == {
        "unsupported_support_geometry"
    }
    return drawing


def test_refusals_retain_exact_occurrence_identity_and_explicit_outcomes(refused_drawing):
    drawing = refused_drawing
    ownership = drawing.recognition_ownership()
    evidence = ownership.evidence
    refs = [ref for ref in evidence.features if evidence.family(ref) == "section_recesses"]
    assert len(refs) == 5
    for ref, source in zip(refs, drawing.recognition().section_recess_refusals, strict=True):
        assert evidence.record(ref) is source
        assert ownership.binding_for(ref) is None
        assert ownership.status(ref) == "unsupported"
    assert len([i for i in drawing.lint() if i.code == "section_recess_recognition_refused"]) == 5


def test_refusals_are_counted_once_in_the_finished_report(refused_drawing):
    report = refused_drawing.report()["recognition"]
    rows = [r for r in report["occurrences"] if r["family"] == "section_recesses"]
    assert len(rows) == 5 and {r["disposition"] for r in rows} == {"unsupported"}
    requirements = [r for r in report["requirements"] if r["family"] == "section_recesses"]
    assert len(requirements) == 5 and {r["state"] for r in requirements} == {"unsupported"}
    assert {r["occurrence_ids"][0] for r in requirements} == {r["id"] for r in rows}
    assert all(len(r["occurrence_ids"]) == 1 and not r["owner_ids"] for r in requirements)
