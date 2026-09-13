"""Circular seats retain their physical arc rather than becoming rectangular channels."""

from math import cos, radians, sin
from pathlib import Path

import pytest
from build123d import Box

from draftwright.analysis import _analyse
from draftwright.model import PartModel, circular_channel
from draftwright.model.compiled import compile_dimensions
from draftwright.section_recess_contract import circular_channel_fields


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("sweep", [60, 180, 270])
def test_declared_arc_retains_minor_semicircular_and_major_shapes(axis, sweep):
    radius = 5
    section = tuple(
        (radius * cos(radians(a)), radius * sin(radians(a))) for a in (0, sweep / 2, sweep)
    )
    run = "xyz".index(axis)
    first, last = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    first[run], last[run] = -3, 3
    feature = circular_channel(
        axis=axis, radius=radius, length=6, centreline=(first, last), section=section
    )
    assert feature.sweep == pytest.approx(sweep)
    assert feature.axis_origin == (0, 0, 0)
    transverse = [i for i in range(3) if i != run]
    assert tuple(feature.frame.origin[i] for i in transverse) == pytest.approx(section[1])
    plan = compile_dimensions(
        PartModel(features=[feature], orientation=None, bbox=Box(20, 20, 20).bounding_box())
    )
    (group,) = plan.of_kind("circular_channel")
    assert {(d.role, d.value) for d in group.dims} == {
        ("seat_diameter", 10.0),
        ("seat_run", 6.0),
        ("seat_sweep", feature.sweep),
    }


@pytest.fixture(
    scope="module", params=["whistle_frame_reference.step", "issue_1595_whistle_key_frame.step"]
)
def seat_analysis(request):
    analysis = _analyse(
        Path(__file__).parent / "fixtures" / request.param,
        title="",
        number="",
        tolerance=None,
        drawn_by="",
        out="",
    )
    return analysis


@pytest.fixture(scope="module")
def seat_records(seat_analysis):
    records = tuple(
        r
        for r in seat_analysis.recognition.section_recesses
        if r.classification.section_shape == "circular"
    )
    assert len(records) == 3
    return records


def test_both_real_frame_variants_supply_three_dimensional_seat_contracts(seat_records):
    for record in seat_records:
        data = circular_channel_fields(record)
        anchor = data.pop("origin")
        feature = circular_channel(**data)
        assert feature.frame.origin == pytest.approx(anchor)
        assert 2 * feature.radius == pytest.approx(14.3, abs=0.0003)
        assert feature.length == pytest.approx(6)
        assert feature.sweep == pytest.approx(62.8888, abs=0.0001)
        assert feature.axis_origin[0] == pytest.approx(0)
        assert feature.axis_origin[2] == pytest.approx(11.1, abs=0.0003)
        assert feature.frame.origin[2] < 4.0  # leader anchors on the curved wall, not its axis
        assert feature.centreline[0][1] == record.geometry.run_interval[0]
        assert feature.centreline[1][1] == record.geometry.run_interval[1]


def test_a_wrong_radius_or_wrong_arc_midpoint_cannot_be_declared():
    from draftwright.section_recess_contract import circular_channel_geometry

    # A real semicircle's physical midpoint establishes which half of the cylinder exists.
    line = ((0, 0, -3), (0, 0, 3))
    with pytest.raises(ValueError, match="cylinder"):
        circular_channel_geometry("z", 4, 6, line, ((5, 0), (0, 5), (-5, 0)))
    with pytest.raises(ValueError, match="ordered"):
        circular_channel_geometry("z", 5, 6, line, ((5, 0), (3, 4), (-5, 0)))


def test_declared_seat_sizes_reach_ink_and_script_replay(seat_analysis, seat_records):
    from draftwright import Sheet
    from draftwright.sheet_emit import emit_sheet_script

    data = circular_channel_fields(seat_records[0])
    data.pop("origin")
    sheet = Sheet(seat_analysis.part, page="A2", scale=2, title="SEAT")
    sheet.authored_dimensions()
    seat = sheet.circular_channel(**data)
    for role in seat.dimension_ids():
        sheet.dimension(seat, role)
    drawing = sheet.build()
    names = [name for name in drawing.annotations() if name.startswith("m_circular_channel_")]
    assert len(names) == 1
    keys = drawing.measurement_keys(names[0])
    assert {key["parameter_id"] for key in keys} == {
        "seat_diameter.diameter",
        "seat_run.length",
        "seat_sweep.angle",
    }
    annotation = drawing.get_annotation(names[0])
    # Helpers outline labels; the leader retains the exact text it was built from.
    assert (
        "14.3" in annotation.label
        and "6 LONG" in annotation.label
        and "62.9° ARC" in annotation.label
    )
    script = emit_sheet_script(
        drawing.model(), "part", "seat", title="SEAT", number="", page="A2", scale=2
    )
    body = script.replace("\npart\n", "\n", 1).split("drawing = sheet.build()", 1)[0]
    namespace = {"part": seat_analysis.part}
    exec(compile(body, "<seat-sheet>", "exec"), namespace)  # noqa: S102
    replay = namespace["sheet"].build()
    assert replay.get_annotation(names[0]).label == annotation.label


def test_axis_locations_share_coaxial_ink_and_keep_each_physical_owner(
    seat_analysis, seat_records
):
    from draftwright import Sheet, build_drawing

    sheet = Sheet(seat_analysis.part, page="A2", scale=2).authored_dimensions()
    for record in seat_records:
        data = circular_channel_fields(record)
        data.pop("origin")
        seat = sheet.circular_channel(**data)
        sheet.dimension(seat, "location").format(decimals=3)
    model = sheet.model()
    compiled = compile_dimensions(model)
    locations = [
        dimension for dimension in compiled.locations if dimension.role == "seat_location"
    ]
    assert len(locations) == 9
    z_values = {dimension.value for dimension in locations if dimension.discriminator == "z"}
    assert len(z_values) == 1
    assert next(iter(z_values)) == pytest.approx(11.1, abs=0.0003)

    drawing = sheet.build()
    names = [name for name in drawing.annotations() if name.startswith("m_seatloc_")]
    assert len(names) == 5  # one common X, one common Z, three different Y stations
    assert sum(len(drawing.measurement_keys(name)) for name in names) == 9
    z_name = next(name for name in names if name.startswith("m_seatloc_z"))
    assert drawing.get_annotation(z_name).label == "11.100"
    assert len(drawing.measurement_keys(z_name)) == 3
    assert not any(name.startswith("m_circular_channel_") for name in drawing.annotations())

    # Live and deferred verbs consume the same approved physical spans.
    for deferred in (False, True):
        edited = build_drawing(
            seat_analysis.part, model=model, auto_dims=False, page="A2", scale=2
        )
        features = edited.model().features
        if deferred:
            with edited.deferred():
                for feature in features:
                    edited.locate(feature, pin=True)
        else:
            from dataclasses import replace

            with pytest.raises(ValueError, match="not from this drawing"):
                edited.locate(replace(features[0]), axes=("z",))
            with pytest.raises(ValueError, match="axes must be a subset"):
                edited.locate(features[0], axes=("w",))
            placed = edited.locate(features[0], axes=("z",), pin=True)
            assert len(placed) == 1 and placed[0].startswith("m_seatloc_z")
        z_names = [name for name in edited.annotations() if name.startswith("m_seatloc_z")]
        assert len(z_names) == 1
        assert edited.registry.is_pinned(z_names[0])
        assert all(edited.get_annotation(name).label == "11.100" for name in z_names)
        assert sum(len(edited.measurement_keys(name)) for name in z_names) == (
            3 if deferred else 1
        )


@pytest.fixture(scope="module")
def detected_seats(seat_analysis):
    from draftwright import build_drawing

    drawing = build_drawing(seat_analysis.part, page="A2", scale=2, auto_dims=False)
    with drawing.deferred():
        for feature in drawing.model().features:
            if feature.kind == "circular_channel":
                drawing.callout(feature)
                drawing.locate(feature)
    return drawing


def test_detected_seats_have_six_source_owned_requirements_each(detected_seats):
    from collections import Counter

    from draftwright.linting.section_recess_coverage import circular_channel_requirement_outcomes

    drawing = detected_seats
    features = [
        feature for feature in drawing.model().features if feature.kind == "circular_channel"
    ]
    assert len(features) == 3
    outcomes = circular_channel_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        bbox=drawing.model().bbox,
    )
    assert len(outcomes) == 18
    assert sorted(Counter(id(row.source_records[0]) for row in outcomes).values()) == [6, 6, 6]
    assert {outcome.state for outcome in outcomes} == {"placed"}
    assert all(outcome.carriers for outcome in outcomes)
    callouts = [name for name in drawing.annotations() if name.startswith("m_circular_channel_")]
    assert len(callouts) == 1
    assert drawing.get_annotation(callouts[0]).label.startswith("3× ø14.3")
    assert len(drawing.measurement_keys(callouts[0])) == 9
    assert drawing.registry.feature_of(callouts[0]) is None
    report = drawing.report()
    seats = [
        row
        for row in report["recognition"]["requirements"]
        if (row["parameter_id"] or "").startswith("seat_")
    ]
    assert len(seats) == 18
    marks = [name for name in drawing.annotations() if name.startswith("m_seat_axis_")]
    assert len(marks) == 4  # one end-view axis and three longitudinal stations
    assert all(drawing.get_annotation(name).is_centerline for name in marks)
    assert all(not drawing.measurement_keys(name) for name in marks)
    for name in marks:
        annotation = drawing.get_annotation(name)
        view = drawing.registry.view_of(name)
        # The visible marker is centred on a physical cylinder axis, never the wall anchor.
        centre = annotation.bounding_box().center()
        candidates = [drawing.at(view, *feature.axis_origin) for feature in features]
        assert any(
            abs(centre.X - point[0]) < 1e-6 and abs(centre.Y - point[1]) < 1e-6
            for point in candidates
        )


def test_seat_ledger_rejects_missing_ambiguous_or_wrong_geometry(seat_analysis, seat_records):
    from dataclasses import replace

    from draftwright.linting.section_recess_coverage import circular_channel_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    features = []
    for record in seat_records:
        data = circular_channel_fields(record)
        data.pop("origin")
        features.append(circular_channel(**data))
    registry = AnnotationRegistry()
    recognition, bbox = seat_analysis.recognition, seat_analysis.bb

    def states(selected):
        return [
            row.state
            for row in circular_channel_requirement_outcomes(
                recognition,
                selected,
                registry,
                bbox=bbox,
            )
        ]

    assert states(features) == ["missing"] * 18
    assert states([]) == ["unverifiable"] * 18
    assert states([*features, features[0]])[:6] == ["unverifiable"] * 6
    # A shifted seat is internally valid but cannot match the original physical occurrence.
    old = features[0]
    line = tuple((x, y + 0.5, z) for x, y, z in old.centreline)
    wrong = circular_channel(
        axis=old.axis, radius=old.radius, length=old.length, centreline=line, section=old.section
    )
    assert states([wrong, *features[1:]])[:6] == ["unverifiable"] * 6
    duplicate_source = replace(
        recognition, section_recesses=(*recognition.section_recesses, seat_records[0])
    )
    ambiguous = circular_channel_requirement_outcomes(
        duplicate_source, features, registry, bbox=bbox
    )
    assert sum(row.state == "unverifiable" for row in ambiguous) == 12


def test_seat_ledger_distinguishes_suppression_drop_and_structured_note(
    seat_analysis, seat_records
):
    from types import SimpleNamespace

    from draftwright.linting.issues import LintIssue
    from draftwright.linting.section_recess_coverage import circular_channel_requirement_outcomes
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    data = circular_channel_fields(seat_records[0])
    data.pop("origin")
    feature = circular_channel(**data)
    registry = AnnotationRegistry()
    registry.record_issue(
        LintIssue(
            "warning",
            "no room",
            code="circular_channel_location_dropped",
            measurement_ids=(DimensionId(feature, "seat_location.location.z"),),
            outcome_stage="placement",
        )
    )
    registry.add(
        object(),
        "approved_note",
        "front",
        feature=feature,
        satisfaction=DimensionId(feature, "seat_sweep.angle"),
    )
    omissions = (
        SimpleNamespace(feature=feature, parameter_id="seat_diameter.diameter", authored=True),
    )
    rows = circular_channel_requirement_outcomes(
        seat_analysis.recognition,
        [feature],
        registry,
        omissions,
        bbox=seat_analysis.bb,
    )
    assert {row.parameter_id: row.state for row in rows if row.features} == {
        "seat_diameter.diameter": "suppressed",
        "seat_run.length": "missing",
        "seat_sweep.angle": "satisfied_by_structured_note",
        "seat_location.location.x": "missing",
        "seat_location.location.y": "missing",
        "seat_location.location.z": "dropped",
    }


@pytest.mark.parametrize("other_length,expected_jobs", [(6 - 4e-15, 1), (6 + 1e-6, 2)])
def test_counted_seats_ignore_only_arithmetic_noise_not_distinct_sizes(
    monkeypatch, other_length, expected_jobs
):
    from types import SimpleNamespace

    from draftwright._core import draft_preset
    from draftwright.annotations import from_model
    from draftwright.model.ir import RequestedDimension

    features = [
        circular_channel(
            axis="y",
            radius=5,
            length=length,
            centreline=((0, station, 0), (0, station + length, 0)),
            section=((5, 0), (0, 5), (-5, 0)),
        )
        for station, length in ((0, 6), (10, other_length))
    ]
    plan = compile_dimensions(
        PartModel(
            features=features,
            orientation=None,
            bbox=Box(30, 40, 20).bounding_box(),
            requested_dimensions=tuple(
                RequestedDimension(feature, "seat_run", display_decimals=0) for feature in features
            ),
        )
    )
    # Both lengths round to the same displayed text; the second case must still remain distinct.
    assert {
        group.dim(role="seat_run").value_text for group in plan.of_kind("circular_channel")
    } == {"6"}
    captured = []

    def capture(_drawing, _analysis, jobs, **kwargs):
        captured.extend(jobs)
        return len(jobs)

    monkeypatch.setattr(from_model, "place_machined_leader_jobs", capture)
    drawing = SimpleNamespace(draft=draft_preset(), view_bounds=lambda _view: (0, 0, 30, 30))
    assert from_model.render_circular_channels(drawing, plan, None, ctx=None) == expected_jobs
    assert sum(len(job[-1]) for job in captured) == 6


@pytest.mark.parametrize(
    "change",
    [
        {"axis": "free"},
        {"radius": True},
        {"radius": float("nan")},
        {"radius": -5},
        {"length": 0},
        {"length": float("inf")},
        {"centreline": ((0, -3, 0),)},
        {"centreline": ((0, -3, 0), (1, 3, 0))},
        {"centreline": ((0, 3, 0), (0, -3, 0))},
        {"section": ((5, 0), (-5, 0))},
        {"section": ((5, 0), (0, 4), (-5, 0))},
        {"section": ((5, 0), (5, 0), (5, 0))},
    ],
)
def test_invalid_seat_geometry_is_refused_at_the_public_declaration(change):
    data = dict(
        axis="y",
        radius=5,
        length=6,
        centreline=((0, -3, 0), (0, 3, 0)),
        section=((5, 0), (0, 5), (-5, 0)),
    )
    data.update(change)
    with pytest.raises(ValueError):
        circular_channel(**data)


def test_seat_locations_preserve_authored_omission_view_requirements_and_zero_offsets():
    from dataclasses import replace

    from build123d import Pos

    from draftwright.model.ir import RequestedDimension
    from draftwright.view_plan import ViewPlanIncomplete

    feature = circular_channel(
        axis="y",
        radius=5,
        length=6,
        centreline=((0, -3, 0), (0, 3, 0)),
        section=((5, 0), (0, 5), (-5, 0)),
    )
    model = PartModel(features=[feature], orientation=None, bbox=Box(20, 20, 20).bounding_box())
    location_only = replace(model, authored_dimensions=(RequestedDimension(feature, "location"),))
    with pytest.raises(ViewPlanIncomplete) as error:
        compile_dimensions(location_only, planned_views=("front",))
    assert [row.identity.parameter for row in error.value.uncovered] == [
        "seat_location.location.y"
    ]

    suppressed = compile_dimensions(
        replace(model, authored_dimensions=()), planned_views=("plan",)
    )
    assert not suppressed.locations
    assert {row.parameter_id for row in suppressed.diagnostics if row.authored} >= {
        "seat_location.location.x",
        "seat_location.location.y",
        "seat_location.location.z",
    }
    # An axis on the stock datum is a physical coincidence, not a zero-length dimension.
    coincident = compile_dimensions(
        replace(location_only, bbox=(Pos(10, 10, 10) * Box(20, 20, 20)).bounding_box()),
        planned_views=("plan",),
    )
    assert not coincident.locations
    zeros = [
        row for row in coincident.diagnostics if row.code == "circular_channel_location_coincident"
    ]
    assert len(zeros) == 3
    assert all(row.value == 0 and not row.authored for row in zeros)


def test_seat_ledger_requires_stock_datum_and_records_physical_coincidence(
    seat_analysis, seat_records
):
    from dataclasses import replace
    from types import SimpleNamespace

    from draftwright.linting.section_recess_coverage import circular_channel_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    data = circular_channel_fields(seat_records[0])
    data.pop("origin")
    feature = circular_channel(**data)
    recognition = replace(seat_analysis.recognition, section_recesses=(seat_records[0],))
    registry = AnnotationRegistry()
    assert circular_channel_requirement_outcomes(None, [], registry) == []
    with pytest.raises(TypeError, match="exact RecognitionResult"):
        circular_channel_requirement_outcomes(SimpleNamespace(), [], registry)
    rows = circular_channel_requirement_outcomes(recognition, [feature], registry)
    assert [row.state for row in rows] == ["missing"] * 3 + ["unverifiable"] * 3
    bbox = SimpleNamespace(
        min=SimpleNamespace(**dict(zip("XYZ", feature.axis_origin, strict=True)))
    )
    rows = circular_channel_requirement_outcomes(recognition, [feature], registry, bbox=bbox)
    assert [row.state for row in rows] == ["missing"] * 3 + ["inapplicable"] * 3
    for row in rows[3:]:
        assert not row.carriers
        assert row.intrinsic_exclusion is not None
        assert row.intrinsic_exclusion.span == (feature.axis_origin, feature.axis_origin)


@pytest.mark.parametrize("corruption", ["frame", "parameters", "source_evidence"])
def test_seat_ledger_does_not_credit_corrupted_geometry_or_measurements(
    monkeypatch, seat_analysis, seat_records, corruption
):
    from dataclasses import replace

    from draftwright.linting.section_recess_coverage import circular_channel_requirement_outcomes
    from draftwright.model.ir import Frame
    from draftwright.registry import AnnotationRegistry

    source = seat_records[0]
    data = circular_channel_fields(source)
    data.pop("origin")
    feature = circular_channel(**data)
    if corruption == "frame":
        wrong = Frame((0, 0, 0), feature.axis)
        with pytest.raises(ValueError, match="physical arc midpoint"):
            replace(feature, frame=wrong)
        # Simulate a downstream implementation defect after valid construction.
        object.__setattr__(feature, "frame", wrong)
    elif corruption == "parameters":
        monkeypatch.setattr(type(feature), "parameters", lambda self: [])
    else:
        source = replace(source, evidence=replace(source.evidence, defining_faces=()))
        with pytest.raises(ValueError, match="defining-face evidence"):
            circular_channel_fields(source)
    recognition = replace(seat_analysis.recognition, section_recesses=(source,))
    rows = circular_channel_requirement_outcomes(
        recognition, [feature], AnnotationRegistry(), bbox=seat_analysis.bb
    )
    assert len(rows) == 6
    assert {row.state for row in rows} == {"unverifiable"}
    assert all(not row.carriers and not row.features for row in rows)


@pytest.fixture(scope="module")
def stock_length_seat():
    from build123d import Cylinder, Pos, Rot

    from draftwright import build_drawing

    part = Box(40, 30, 10) - Pos(0, 0, 5) * Rot(90, 0, 0) * Cylinder(5, 40)
    return build_drawing(part)


@pytest.mark.parametrize(
    "carrier",
    [
        "placed",
        "structured_note",
        "dropped",
        "missing",
        "wrong_axis",
        "shifted_planes",
        "foreign_owner",
    ],
)
def test_consolidated_seat_run_requires_its_actual_end_planes_and_live_carrier(
    stock_length_seat, carrier
):
    from dataclasses import replace

    from draftwright.linting.issues import LintIssue
    from draftwright.linting.section_recess_coverage import circular_channel_requirement_outcomes
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = stock_length_seat
    model = drawing.model()
    plan = compile_dimensions(model)
    (omission,) = [row for row in plan.diagnostics if row.parameter_id == "seat_run.length"]
    owner = omission.conveyed_by
    assert owner is not None and owner.parameter == "depth.length"
    features = list(model.features)
    if carrier == "wrong_axis":
        owner = DimensionId(owner.feature, "width.length")
    elif carrier in ("shifted_planes", "foreign_owner"):
        envelope = owner.feature
        shifted = replace(
            envelope,
            bbox_min=tuple(v + (1 if i == 1 else 0) for i, v in enumerate(envelope.bbox_min)),
            bbox_max=tuple(v + (1 if i == 1 else 0) for i, v in enumerate(envelope.bbox_max)),
        )
        owner = DimensionId(shifted, owner.parameter)
        if carrier == "shifted_planes":
            features = [shifted if feature is envelope else feature for feature in features]
    registry = AnnotationRegistry()
    if carrier == "structured_note":
        registry.add(object(), "extent", "side", satisfaction=owner)
    elif carrier == "dropped":
        registry.record_issue(
            LintIssue(
                "warning",
                "extent did not fit",
                code="dimension_dropped",
                measurement_ids=(owner,),
                outcome_stage="placement",
            )
        )
    elif carrier != "missing":
        registry.add(object(), "extent", "side", measurement=owner)
    rows = circular_channel_requirement_outcomes(
        drawing.recognition(),
        features,
        registry,
        (replace(omission, conveyed_by=owner),),
        bbox=model.bbox,
    )
    (run,) = [row for row in rows if row.parameter_id == "seat_run.length"]
    expected = {
        "placed": "placed",
        "structured_note": "satisfied_by_structured_note",
        "dropped": "dropped",
    }.get(carrier, "missing")
    assert run.state == expected
    assert [item.annotation for item in run.carriers] == (
        ["extent"] if carrier in ("placed", "structured_note") else []
    )
    if carrier == "placed":
        report_rows = [
            row
            for row in drawing.report()["recognition"]["requirements"]
            if row["parameter_id"] == "seat_run.length"
        ]
        assert len(report_rows) == 1
        assert report_rows[0]["state"] == "placed"
        assert report_rows[0]["annotations"] == ["m_env_depth"]


def test_independent_envelope_identity_rejects_substitutes_and_re_registration():
    from types import SimpleNamespace

    from draftwright.feature_identity import (
        is_exact_envelope_feature,
        register_envelope_feature_type,
    )
    from draftwright.model.ir import EnvelopeFeature

    register_envelope_feature_type(EnvelopeFeature)
    assert not is_exact_envelope_feature(SimpleNamespace(kind="envelope"))
    with pytest.raises(RuntimeError, match="already registered"):
        register_envelope_feature_type(SimpleNamespace)
