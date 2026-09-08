"""A cylindrical mouth preserves variable depth through the public drawing pipeline."""

from dataclasses import replace
from math import sqrt
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos, Rotation

from draftwright import Sheet, build_drawing
from draftwright.linting.pocket_coverage import pocket_requirement_outcomes
from draftwright.sheet_emit import emit_sheet_script


@pytest.fixture(scope="module", params=["centered", "offset", "negative", "rotated"])
def curved(request):
    offset = request.param == "offset"
    y, length = (9, 6) if offset else (0, 24)
    part = Rotation(0, 90, 0) * Cylinder(20, 80) - Pos(0, y, 14) * Box(6, length, 12)
    if request.param == "negative":
        part = Rotation(180, 0, 0) * part
    if request.param == "rotated":
        part = Pos(11, 22, 33) * Rotation(0, 90, 0) * part
    drawing = build_drawing(part)
    (record,) = drawing.recognition().section_recesses
    ends = (record.geometry.ends.low.surface, record.geometry.ends.high.surface)
    assert sorted(end.type for end in ends) == ["cylinder", "plane"]
    assert not drawing.recognition().section_recess_refusals
    (feature,) = [f for f in drawing.model().features if f.kind == "pocket"]
    return request.param, part, drawing, feature


def test_curved_mouth_owns_maximum_not_uniform_depth(curved):
    case, _, drawing, feature = curved
    expected = sqrt(400 - 36) - 8 if case == "offset" else 12
    assert feature.depth == pytest.approx(expected)
    assert feature.mouth_radius == 20
    assert feature.mouth_axis == ("z" if case == "rotated" else "x")
    assert feature.open_sign == (-1 if case == "negative" else 1)
    assert feature.width == 6
    assert feature.length == (6 if case == "offset" else 24)
    ids = {p.parameter_id for p in feature.parameters()}
    assert "pocket_max_depth.length" in ids and "pocket_depth.length" not in ids
    rows = [r for r in drawing.report()["recognition"]["requirements"] if r["family"] == "pockets"]
    assert len(rows) == 5 and {r["state"] for r in rows} == {"placed"}
    (row,) = [r for r in rows if r["parameter_id"] == "pocket_max_depth.length"]
    (name,) = row["annotations"]
    assert drawing.get_annotation(name).label.endswith("MAX DEEP")
    assert not [i for i in drawing.lint() if "pocket" in i.code]


def test_curved_pocket_script_retains_the_exact_surface_and_exports(curved, tmp_path):
    _, part, drawing, feature = curved
    source = emit_sheet_script(
        drawing.model(),
        "part = input_part",
        str(tmp_path / "curved"),
        title="Curved mouth",
        number="1485",
        formats=(),
    )
    assert "mouth_radius=20" in source and "pocket_max_depth.length" in source
    namespace = {"__name__": "__curved_test__", "input_part": part}
    exec(compile(source, "<curved pocket>", "exec"), namespace)
    rebuilt = namespace["drawing"]
    (actual,) = [f for f in rebuilt.model().features if f.kind == "pocket"]
    assert actual == feature
    outcomes = pocket_requirement_outcomes(
        rebuilt.recognition(), rebuilt.model().features, rebuilt.registry
    )
    assert len(outcomes) == 5 and {o.state for o in outcomes} == {"placed"}
    paths = rebuilt.export(str(tmp_path / "curved"), formats=("svg",))
    assert Path(paths["svg"]).stat().st_size > 0


def test_a_planar_substitution_cannot_certify_the_curved_pocket(curved):
    _, _, drawing, feature = curved
    planar = replace(feature, mouth_axis=None, mouth_radius=None, mouth_at=None)
    (outcome,) = pocket_requirement_outcomes(drawing.recognition(), (planar,), drawing.registry)
    assert outcome.state == "unverifiable" and outcome.requirement_count == 5
    assert outcome.source_records[0] is drawing.recognition().section_recesses[0]


def test_authored_maximum_depth_survives_alone_with_its_tolerance(curved):
    _, part, _, feature = curved
    sheet = Sheet(part)
    sheet.authored_dimensions()
    handle = sheet.pocket(
        width=feature.width,
        length=feature.length,
        depth=feature.depth,
        long_axis=feature.long_axis,
        width_axis=feature.width_axis,
        at=feature.frame.origin,
        w_center=feature.w_center,
        lo=feature.lo,
        hi=feature.hi,
        open_sign=feature.open_sign,
        mouth_axis=feature.mouth_axis,
        mouth_radius=feature.mouth_radius,
        mouth_at=feature.mouth_at,
    )
    sheet.dimension(handle, "pocket_max_depth.length")
    handle.tolerance(0, 0.1, on="pocket_max_depth.length")
    drawing = sheet.build()
    (label,) = [a.label for n, a in drawing.iter_annotations() if n.startswith("m_pocket_")]
    assert label.startswith("POCKET ") and label.endswith(" +0.1 -0.0 MAX DEEP")
    assert " WIDE" not in label and " LONG" not in label


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"open_sign": True}, "open_sign"),
        ({"open_sign": 0}, "open_sign"),
        ({"corner_radius": 1}, "closed rectangular"),
        ({"edge_anchored": True}, "closed rectangular"),
        ({"mouth_radius": None}, "numbers"),
        ({"mouth_at": None}, "finite numbers"),
        ({"mouth_axis": None}, "partial"),
        ({"mouth_radius": float("nan")}, "finite"),
        ({"mouth_radius": 0}, "positive"),
        ({"mouth_axis": "z"}, "section plane"),
        ({"mouth_radius": 10}, "outside"),
        ({"depth": 11}, "maximum depth"),
        ({"depth": 2, "frame": None}, "floor"),
    ],
)
def test_inconsistent_mouth_geometry_is_rejected(changes, match):
    from draftwright.model.declare import pocket
    from draftwright.model.ir import Frame

    feature = pocket(
        width=6,
        length=24,
        depth=12,
        width_axis="x",
        long_axis="y",
        at=(0, 0, 14),
        lo=-12,
        hi=12,
        w_center=0,
        mouth_axis="x",
        mouth_radius=20,
        mouth_at=(0, 0, 0),
    )
    if "frame" in changes:
        changes = {**changes, "frame": Frame((0, 0, 19), "z")}
    with pytest.raises(ValueError, match=match):
        replace(feature, **changes)


def test_curved_source_rejects_a_floor_that_breaks_through_the_mouth(curved):
    from draftwright.section_recess_contract import section_recess_pocket_fields

    _, _, drawing, _ = curved
    record = drawing.recognition().section_recesses[0]
    source = record.to_dict()
    plane_index = 0 if record.geometry.ends.low.surface.type == "plane" else 1
    cylinder = (
        record.geometry.ends.high if plane_index == 0 else record.geometry.ends.low
    ).surface
    low, high = cylinder.polygon_height_bounds(
        tuple(v.point for v in record.geometry.profile.boundary)
    )
    assert high - low > 1
    interval = list(record.geometry.run_interval)
    interval[plane_index] = (low + high) / 2
    source["geometry"]["run_interval"] = interval
    with pytest.raises(ValueError, match="positive depth over its complete profile"):
        section_recess_pocket_fields(source, schema_version=3)


def test_curved_source_rejects_a_false_centroid_intersection(curved):
    from draftwright.section_recess_contract import section_recess_pocket_fields

    _, _, drawing, _ = curved
    source = drawing.recognition().section_recesses[0].to_dict()
    index = 0 if source["geometry"]["ends"]["low"]["surface"]["type"] == "cylinder" else 1
    interval = list(source["geometry"]["run_interval"])
    interval[index] += 0.5
    source["geometry"]["run_interval"] = interval
    with pytest.raises(ValueError, match="published centroid intersection"):
        section_recess_pocket_fields(source, schema_version=3)


@pytest.mark.parametrize("fault", ["unknown_surface", "two_cylinders", "inward_branch"])
def test_curved_source_refuses_unsupported_or_contradictory_end_surfaces(curved, fault):
    from draftwright.section_recess_contract import (
        UnsupportedSectionRecess,
        section_recess_pocket_fields,
    )

    _, _, drawing, _ = curved
    source = drawing.recognition().section_recesses[0].to_dict()
    assert section_recess_pocket_fields(source, schema_version=3)["mouth_radius"] == 20
    ends = source["geometry"]["ends"]
    cylinder = next(end for end in ends.values() if end["surface"]["type"] == "cylinder")
    plane = next(end for end in ends.values() if end["surface"]["type"] == "plane")
    if fault == "unknown_surface":
        plane["surface"] = {"type": "sphere"}
        error, match = ValueError, "known surface type"
    elif fault == "two_cylinders":
        plane["surface"] = dict(cylinder["surface"])
        error, match = UnsupportedSectionRecess, "one cylindrical end"
    else:
        surface = cylinder["surface"]
        surface["branch"] = "negative" if surface["branch"] == "positive" else "positive"
        error, match = ValueError, "open away from its planar floor"
    with pytest.raises(error, match=match):
        section_recess_pocket_fields(source, schema_version=3)


def test_repeated_curved_pockets_keep_one_group_and_every_original_occurrence(tmp_path):
    part = Rotation(0, 90, 0) * Cylinder(20, 100)
    for x in (-30, 0, 30):
        part -= Pos(x, 9, 14) * Box(6, 6, 12)
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert len(recognition.section_recesses) == 3
    # The released aggregate now emits this physical group once. Keep the
    # consumer's exact-duplicate guard exercised with an equal-valued copy.
    (first,) = recognition.section_recess_patterns
    second = replace(first)
    assert first == second and first is not second
    from draftwright.section_recess_contract import distinct_section_recess_patterns

    assert distinct_section_recess_patterns((first, second), recognition.section_recesses) == (
        first,
    )
    subset = replace(first, members=first.members[:2])
    assert distinct_section_recess_patterns((first, subset), recognition.section_recesses) == (
        first,
        subset,
    )
    with pytest.raises(ValueError, match="lattice"):
        distinct_section_recess_patterns(
            (first, replace(second, pitch=31)), recognition.section_recesses
        )
    (pattern,) = [f for f in drawing.model().features if f.kind == "pocket_pattern"]
    assert pattern.count == 3 and pattern.member.depth == pytest.approx(sqrt(364) - 8)
    rows = [
        r
        for r in drawing.report()["recognition"]["requirements"]
        if r["family"] == "pocket_patterns"
    ]
    assert len(rows) == 7 and {r["state"] for r in rows} == {"placed"}
    (depth,) = [r for r in rows if r["parameter_id"] == "pocket_max_depth.length"]
    assert len(depth["occurrence_ids"]) == 3
    (name,) = depth["annotations"]
    assert drawing.get_annotation(name).label == "3× 6 × 6 × 11.1 MAX DEEP"
    source = emit_sheet_script(
        drawing.model(),
        "part = input_part",
        str(tmp_path / "pattern"),
        title="Curved pattern",
        number="1485",
        formats=(),
    )
    namespace = {"__name__": "__pattern_test__", "input_part": part}
    exec(compile(source, "<curved pattern>", "exec"), namespace)
    rebuilt = namespace["drawing"]
    assert pattern in rebuilt.model().features
    from draftwright.linting.pocket_pattern_coverage import pocket_pattern_requirement_outcomes

    outcomes = pocket_pattern_requirement_outcomes(
        rebuilt.recognition(), rebuilt.model().features, rebuilt.registry
    )
    assert len(outcomes) == 7 and {o.state for o in outcomes} == {"placed"}


@pytest.mark.parametrize("count", [1, 3])
def test_authored_curved_geometry_round_trips_without_rounding_its_constraints(count, tmp_path):
    from draftwright.model.declare import pocket, pocket_pattern

    width, length, center_y = 6.123456789, 6.345678901, 9.123456789
    lo, hi = center_y - length / 2, center_y + length / 2
    depth = sqrt(400 - lo * lo) - 8
    center_z = 8 + depth / 2
    part = Rotation(0, 90, 0) * Cylinder(20, 120)
    xs = (0,) if count == 1 else (-30, 0, 30)
    for x in xs:
        part -= Pos(x, center_y, 14) * Box(width, length, 12)
    member = pocket(
        width=width,
        length=length,
        depth=depth,
        long_axis="y",
        width_axis="x",
        lo=lo,
        hi=hi,
        at=(xs[0], center_y, center_z),
        w_center=xs[0],
        mouth_axis="x",
        mouth_radius=20,
        mouth_at=(0, 0, 0),
    )
    feature = (
        member
        if count == 1
        else pocket_pattern(
            member,
            kind="linear",
            count=3,
            pitch=30,
            direction=(1, 0, 0),
            at=(0, center_y, center_z),
        )
    )
    drawing = build_drawing(part, model=[feature])
    source = emit_sheet_script(
        drawing.model(),
        "part = input_part",
        str(tmp_path / "precision"),
        title="Curved precision",
        number="1485",
        formats=(),
    )
    namespace = {"__name__": "__curved_precision_test__", "input_part": part}
    exec(compile(source, "<curved precision>", "exec"), namespace)
    assert namespace["drawing"].model().features == drawing.model().features
