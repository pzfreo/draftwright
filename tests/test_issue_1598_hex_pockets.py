"""Nut pockets need a supported regular-hex grammar, not merely six vertices."""

from math import cos, pi, sin, sqrt
from pathlib import Path

import pytest

from draftwright.analysis import _analyse
from draftwright.section_recess_contract import hex_pocket_fields, hex_pocket_geometry


def _hexagon(across=4.3, angle=0, centre=(10, 20)):
    radius = across / sqrt(3)
    return tuple(
        (
            centre[0] + radius * cos(angle + i * pi / 3),
            centre[1] + radius * sin(angle + i * pi / 3),
        )
        for i in range(6)
    )


@pytest.mark.parametrize("axis", "xyz")
@pytest.mark.parametrize("open_sign", [-1, 1])
def test_declared_hex_geometry_retains_mouth_floor_and_physical_wall_anchors(axis, open_sign):
    run = "xyz".index(axis)
    transverse = [i for i in range(3) if i != run]
    at = [0.0, 0.0, 0.0]
    at[run] = 7.0
    for i, world in enumerate(transverse):
        at[world] = (10, 20)[i]
    section = _hexagon(angle=pi / 7)
    data = hex_pocket_geometry(axis, 1.9, open_sign, at, section)
    assert data["across_flats"] == pytest.approx(4.3)
    assert data["section"] == section
    assert data["origin"] == tuple(at)
    assert len(data["flat_centres"]) == len(data["flat_directions"]) == 6
    for i, (centre, direction) in enumerate(
        zip(data["flat_centres"], data["flat_directions"], strict=True)
    ):
        assert centre[run] == pytest.approx(7 - open_sign * 1.9 / 2)
        assert direction[run] == 0
        assert sum(component**2 for component in direction) == pytest.approx(1)
        for j, world in enumerate(transverse):
            assert centre[world] == pytest.approx((section[i][j] + section[(i + 1) % 6][j]) / 2)


@pytest.fixture(
    scope="module", params=["whistle_frame_reference.step", "issue_1595_whistle_key_frame.step"]
)
def hex_analysis(request):
    return _analyse(
        Path(__file__).parent / "fixtures" / request.param,
        title="",
        number="",
        tolerance=None,
        drawn_by="",
        out="",
    )


def test_both_frames_supply_six_supported_nut_pocket_sections(hex_analysis):
    from dataclasses import asdict

    records = [
        r
        for r in hex_analysis.recognition.section_recesses
        if r.classification.section_shape == "hexagonal"
    ]
    assert len(records) == 6
    for record in records:
        original = asdict(record)
        data = hex_pocket_fields(record)
        assert data["axis"] == "z"
        assert data["across_flats"] == pytest.approx(4.3)
        assert data["depth"] == pytest.approx(1.9)
        assert data["open_sign"] == -1
        assert data["origin"][2] == 0
        assert all(point[2] == pytest.approx(0.95) for point in data["flat_centres"])
        assert asdict(record) == original


@pytest.mark.parametrize(
    "defect",
    [
        "irregular",
        "concave",
        "duplicated",
        "unordered",
        "five",
        "wrong_centre",
        "depth",
        "sign",
        "axis",
    ],
)
def test_six_vertex_labels_do_not_admit_unsupported_or_invalid_hex_geometry(defect):
    section = list(_hexagon())
    axis, depth, sign, at = "z", 1.9, -1, (10, 20, 0)
    if defect == "irregular":
        section[2] = (section[2][0] + 0.05, section[2][1])
    elif defect == "concave":
        section[2] = (10, 20)
    elif defect == "duplicated":
        section[2] = section[1]
    elif defect == "unordered":
        section[2], section[3] = section[3], section[2]
    elif defect == "five":
        section.pop()
    elif defect == "wrong_centre":
        at = (11, 20, 0)
    elif defect == "depth":
        depth = 0
    elif defect == "sign":
        sign = True
    else:
        axis = "free"
    if defect in ("irregular", "concave", "duplicated", "unordered"):
        at = (sum(p[0] for p in section) / 6, sum(p[1] for p in section) / 6, 0)
    with pytest.raises(ValueError, match="not regular" if defect == "irregular" else None):
        hex_pocket_geometry(axis, depth, sign, at, section)


def _declare_hex(sheet, data):
    return sheet.hex_pocket(
        axis=data["axis"],
        depth=data["depth"],
        open_sign=data["open_sign"],
        at=data["origin"],
        section=data["section"],
    )


def test_six_pocket_sizes_share_ink_preserve_all_owners_and_replay(hex_analysis):
    from draftwright import Sheet, build_drawing
    from draftwright.sheet_emit import emit_sheet_script

    records = [
        record
        for record in hex_analysis.recognition.section_recesses
        if record.classification.section_shape == "hexagonal"
    ]
    assert len(records) == 6
    sheet = Sheet(hex_analysis.part, page="A2", scale=2).authored_dimensions()
    for record in records:
        pocket = _declare_hex(sheet, hex_pocket_fields(record))
        for parameter in pocket.dimension_ids():
            sheet.dimension(pocket, parameter)
    drawing = sheet.build()
    names = [name for name in drawing.annotations() if name.startswith("m_hex_pocket_")]
    assert len(names) == 1
    name = names[0]
    assert drawing.get_annotation(name).label == "6× HEX 4.3 A/F × 1.9 DEEP"
    assert len(drawing.measurement_keys(name)) == 12
    assert {key["parameter_id"] for key in drawing.measurement_keys(name)} == {
        "polygon_across_flats.length",
        "pocket_depth.length",
    }
    script = emit_sheet_script(
        drawing.model(), "part", "hex", title="", number="", page="A2", scale=2
    )
    body = script.replace("\npart\n", "\n", 1).split("drawing = sheet.build()", 1)[0]
    namespace = {"part": hex_analysis.part}
    exec(compile(body, "<hex-sheet>", "exec"), namespace)  # noqa: S102
    replay = namespace["sheet"].build()
    assert replay.get_annotation(name).label == drawing.get_annotation(name).label
    assert replay.model().features == drawing.model().features

    # Both editing routes reuse the same renderer and its per-feature measurement ids.
    for deferred in (False, True):
        edited = build_drawing(
            hex_analysis.part, model=drawing.model(), auto_dims=False, page="A2", scale=2
        )
        features = edited.model().features
        if deferred:
            with edited.deferred():
                for feature in features:
                    edited.callout(feature)
            assert edited.get_annotation(name).label == drawing.get_annotation(name).label
            assert len(edited.measurement_keys(name)) == 12
        else:
            single = edited.callout(features[0])
            assert edited.get_annotation(single).label == "HEX 4.3 A/F × 1.9 DEEP"
            assert len(edited.measurement_keys(single)) == 2


@pytest.mark.parametrize("roles", [(), ("polygon_across_flats.length",), ("pocket_depth.length",)])
def test_hex_labels_only_state_approved_dimensions(roles):
    from build123d import Box

    from draftwright import Sheet

    sheet = Sheet(Box(40, 50, 10)).authored_dimensions()
    pocket = sheet.hex_pocket(axis="z", depth=1.9, open_sign=1, at=(10, 20, 5), section=_hexagon())
    for role in roles:
        sheet.dimension(pocket, role).format(decimals=2)
    drawing = sheet.build()
    labels = [
        drawing.get_annotation(name).label
        for name in drawing.annotations()
        if name.startswith("m_hex_pocket_")
    ]
    assert labels == (
        []
        if not roles
        else ["HEX 4.30 A/F" if "polygon_across_flats.length" in roles else "1.90 DEEP"]
    )


def test_detected_hex_pockets_receive_source_owned_credit_only_for_present_ink(hex_analysis):
    from collections import Counter

    from draftwright import build_drawing
    from draftwright.linting.section_recess_coverage import hex_pocket_requirement_outcomes

    drawing = build_drawing(hex_analysis.part, page="A2", scale=2, auto_dims=False)
    features = [feature for feature in drawing.model().features if feature.kind == "hex_pocket"]
    assert len(features) == 6
    with drawing.deferred():
        for feature in features:
            drawing.callout(feature)
        for feature in drawing.model().features:
            if feature.kind == "flat":
                drawing.callout(feature)
    outcomes = hex_pocket_requirement_outcomes(
        drawing.recognition(), drawing.model().features, drawing.registry
    )
    assert len(outcomes) == 12
    assert sorted(Counter(id(row.source_records[0]) for row in outcomes).values()) == [2] * 6
    assert {row.state for row in outcomes} == {"placed"}
    assert all(row.carriers for row in outcomes)
    flats = [name for name in drawing.annotations() if name.startswith("m_flat_")]
    assert len(flats) == 3
    assert {drawing.get_annotation(name).label for name in flats} == {
        "3× 6.00 A/F",
        "3× 6.05 A/F",
        "4× 6.50 A/F",
    }
    name = next(name for name in drawing.annotations() if name.startswith("m_hex_pocket_"))
    assert drawing.registry.feature_of(name) is None
    report = drawing.report()
    rows = [
        row
        for row in report["recognition"]["requirements"]
        if row["parameter_id"] == "polygon_across_flats.length"
    ]
    assert len(rows) == 6
    assert all(row["state"] == "placed" for row in rows)
    drawing.remove(name)
    assert {
        row.state
        for row in hex_pocket_requirement_outcomes(
            drawing.recognition(), drawing.model().features, drawing.registry
        )
    } == {"missing"}


def test_hex_ledger_checks_geometry_ambiguity_and_each_outcome(hex_analysis):
    from dataclasses import replace
    from types import SimpleNamespace

    from draftwright import Sheet
    from draftwright.linting.issues import LintIssue
    from draftwright.linting.section_recess_coverage import hex_pocket_requirement_outcomes
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    records = [
        record
        for record in hex_analysis.recognition.section_recesses
        if record.classification.section_shape == "hexagonal"
    ]
    sheet = Sheet(hex_analysis.part).authored_dimensions()
    for record in records:
        _declare_hex(sheet, hex_pocket_fields(record))
    features = sheet.model().features
    assert len(features) == 6
    recognition = hex_analysis.recognition
    registry = AnnotationRegistry()

    def states(selected, source=recognition):
        return [row.state for row in hex_pocket_requirement_outcomes(source, selected, registry)]

    assert states(features) == ["missing"] * 12
    assert states([]) == ["unverifiable"] * 12
    assert states([*features, features[0]])[:2] == ["unverifiable"] * 2
    wrong = replace(features[0], depth=features[0].depth + 0.01)
    assert states([wrong, *features[1:]])[:2] == ["unverifiable"] * 2

    class WrongParameters(type(features[0])):
        def parameters(self):
            return [
                replace(parameter, value=parameter.value + 1) for parameter in super().parameters()
            ]

    source = features[0]
    wrong_parameters = WrongParameters(
        source.frame, source.depth, source.open_sign, source.section
    )
    assert states([wrong_parameters, *features[1:]])[:2] == ["unverifiable"] * 2
    duplicate_source = replace(
        recognition, section_recesses=(*recognition.section_recesses, records[0])
    )
    assert states(features, duplicate_source).count("unverifiable") == 4

    registry.record_issue(
        LintIssue(
            "warning",
            "no room",
            code="hex_pocket_dropped",
            measurement_ids=(DimensionId(features[0], "pocket_depth.length"),),
            outcome_stage="placement",
        )
    )
    registry.add(
        object(),
        "approved_note",
        "plan",
        feature=features[1],
        satisfaction=DimensionId(features[1], "pocket_depth.length"),
    )
    omissions = (
        SimpleNamespace(
            feature=features[0], parameter_id="polygon_across_flats.length", authored=True
        ),
    )
    rows = hex_pocket_requirement_outcomes(recognition, features, registry, omissions)
    assert [row.state for row in rows[:4]] == [
        "suppressed",
        "dropped",
        "missing",
        "satisfied_by_structured_note",
    ]


@pytest.mark.parametrize("difference", ["identical", "depth", "opening", "precision"])
def test_counted_hexes_preserve_distinct_claims_and_rank_above_incidental_flats(
    monkeypatch, difference
):
    from types import SimpleNamespace

    from build123d import Box

    from draftwright._core import draft_preset
    from draftwright.annotations import from_model
    from draftwright.model import PartModel, flat, hex_pocket
    from draftwright.model.compiled import compile_dimensions
    from draftwright.model.ir import RequestedDimension

    features = [
        hex_pocket(
            axis="z",
            depth=1.9 + (1e-6 if index and difference == "depth" else 0),
            open_sign=-1 if index and difference == "opening" else 1,
            at=(index * 10, 0, 5),
            section=_hexagon(centre=(index * 10, 0)),
        )
        for index in range(2)
    ]
    features.append(flat(axis="z", across=6, at=(0, 0, 0)))
    plan = compile_dimensions(
        PartModel(
            features=features,
            orientation=None,
            bbox=Box(40, 30, 10).bounding_box(),
            requested_dimensions=tuple(
                RequestedDimension(
                    feature,
                    "pocket_depth",
                    display_decimals=2 if index and difference == "precision" else 1,
                )
                for index, feature in enumerate(features[:2])
            ),
        )
    )
    captured = []
    monkeypatch.setattr(
        from_model, "collect_feature_leader", lambda _ctx, job: captured.append(job)
    )
    drawing = SimpleNamespace(draft=draft_preset(), view_bounds=lambda _view: (0, 0, 40, 30))
    context = SimpleNamespace(feature_leaders=[])
    from_model.render_hex_pockets(drawing, plan, None, ctx=context)
    from_model.render_flats(drawing, plan, None, ctx=context)
    pockets = [job for job in captured if job.name.startswith("m_hex_pocket_")]
    assert len(pockets) == (1 if difference == "identical" else 2)
    assert sum(len(job.measurement) for job in pockets) == 4
    assert [job.priority for job in pockets] == [1.0] * len(pockets)
    assert [job.priority for job in captured if job.name.startswith("m_flat_")] == [0.0]
