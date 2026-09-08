"""Short shoulders must not erase neighbouring axial measurements (#1505)."""

from collections import Counter

import pytest
from build123d import Cylinder, Pos, Rot

from draftwright import Sheet
from draftwright.model.compiled import compile_dimensions
from draftwright.sheet_emit import emit_sheet_script


@pytest.fixture(scope="module", params=(False, True), ids=("two-step", "symmetric-flange"))
def axial_chain(request):
    intervals = [(72, -25, -3, "lower"), (40, -3, 0, "lower")]
    if request.param:
        intervals.extend([(96, 0, 6, "flange"), (40, 6, 9, "upper"), (72, 9, 31, "upper")])
    solids = [Pos(0, 0, (lo + hi) / 2) * Cylinder(d / 2, hi - lo) for d, lo, hi, _ in intervals]
    part = solids[0].fuse(*solids[1:])
    sheet = Sheet(part, page="A3", scale=1, scale_policy="permissive")
    sheet.authored_dimensions()
    for diameter, lo, hi, group in intervals:
        handle = sheet.step(
            diameter=diameter,
            length=hi - lo,
            at=(0, 0, (lo + hi) / 2),
            axis="z",
            profile_group=group,
        )
        sheet.dimension(handle, "step.length")
        sheet.dimension(handle, "step.diameter")
    return sheet.build(), intervals


def test_every_authored_shoulder_has_its_own_value_supports_and_identity(axial_chain):
    drawing, intervals = axial_chain
    marks = [
        (name, item)
        for name, item in drawing.iter_annotations()
        if any(
            identity.parameter == "step.length"
            for identity in drawing.registry.measurement_of(name)
        )
    ]
    assert Counter(item.label for _, item in marks) == Counter(
        str(hi - lo) for _, lo, hi, _ in intervals
    )
    spans = []
    for name, item in marks:
        (identity,) = drawing.registry.measurement_of(name)
        lo, hi = sorted(point[2] for point in identity.feature.span)
        spans.append((lo, hi))
        view = drawing.registry.view_of(name)
        expected = sorted(drawing.at(view, 0, 0, z)[1] for z in (lo, hi))
        # Read the rendered horizontal extension strokes, not the renderer's
        # nominal length metadata. Both physical shoulder stations must survive.
        extensions = [
            first[1]
            for first, second in item.segments
            if abs(first[1] - second[1]) < 1e-6 and abs(first[0] - second[0]) > 1
        ]
        assert all(
            any(abs(station - actual) < 1e-6 for actual in extensions) for station in expected
        )
    assert Counter(spans) == Counter((lo, hi) for _, lo, hi, _ in intervals)
    problems = {
        "step_dim_dropped",
        "axial_length_missing",
        "plan_incomplete",
        "annotation_overlap",
        "annotation_ink_overlap",
        "view_annotation_overlap",
        "dim_label_vs_geometry",
    }
    assert not [issue for issue in drawing.lint() if issue.code in problems]


def _shoulder_detail_sheet(axis="z", *, omitted=None, factor=3):
    rotation = {"x": Rot(0, 90, 0), "y": Rot(-90, 0, 0), "z": Rot(0, 0, 0)}[axis]
    origin = (60, 40, 20)
    part = (
        Pos(*origin)
        * rotation
        * (Pos(0, 0, -14) * Cylinder(15, 22) + Pos(0, 0, -1.5) * Cylinder(10, 3))
    )
    sheet = Sheet(part, page="A2", scale=1, scale_policy="permissive")
    for diameter, station, length in ((30, -14, 22), (20, -1.5, 3)):
        position = tuple(
            value + (station if i == "xyz".index(axis) else 0) for i, value in enumerate(origin)
        )
        handle = sheet.step(diameter=diameter, length=length, at=position, axis=axis)
        handle.tolerance(0.2, on="length")
        for parameter in ("step.length", "step.diameter"):
            if length != 3 or parameter != omitted:
                sheet.dimension(handle, parameter)
    sheet.detail_view("A", around=handle).scale(factor)
    return sheet


@pytest.fixture(scope="module", params=("x", "y", "z"))
def shoulder_detail(request):
    axis = request.param
    sheet = _shoulder_detail_sheet(axis)
    plan = compile_dimensions(sheet.model())
    (approved,) = [
        group.dim(kind="length")
        for group in plan.of_kind("step")
        if group.dim(kind="length").value == 3
    ]
    assert approved.tolerance is not None, "the detail must preserve a real approved tolerance"
    return sheet.build(), approved, axis, sheet.view_constraints


def test_semantic_shoulder_detail_keeps_profile_scale_tolerance_and_supports(shoulder_detail):
    drawing, approved, axis, _constraints = shoulder_detail
    ((name, mark),) = [
        (name, mark)
        for name, mark in drawing.iter_annotations()
        if drawing.view_of(name) == "detail_a"
    ]
    assert mark.label == "3 ±0.2"
    (identity,) = drawing.registry.measurement_of(name)
    assert identity.feature is approved.id.feature and identity.parameter == "step.length"
    points = [drawing.at("detail_a", *point) for point in approved.span]
    coordinate = 1 if axis == "z" else 0
    stations = sorted(point[coordinate] for point in points)
    assert stations[1] - stations[0] == pytest.approx(9)
    strokes = [
        first[coordinate]
        for first, second in mark.segments
        if abs(first[coordinate] - second[coordinate]) < 1e-6
        and abs(first[1 - coordinate] - second[1 - coordinate]) > 1
    ]
    assert all(any(abs(station - stroke) < 1e-6 for stroke in strokes) for station in stations)
    parent = "side" if axis == "y" else "front"
    parent_stations = sorted(drawing.at(parent, *point)[coordinate] for point in approved.span)
    marker = drawing.registry.named("detail_marker_A").bounding_box()
    assert (tuple(marker.min)[coordinate], tuple(marker.max)[coordinate]) == pytest.approx(
        parent_stations
    )
    assert not [issue for issue in drawing.lint() if issue.severity != "info"]


def test_shoulder_detail_round_trips_as_editable_intent(shoulder_detail, tmp_path):
    drawing, _approved, _axis, constraints = shoulder_detail
    source = emit_sheet_script(
        drawing.model(),
        "part",
        str(tmp_path / "shoulder"),
        title="T",
        number="N",
        page="A2",
        scale=1,
        scale_policy="permissive",
        view_constraints=constraints,
        formats=("svg",),
    )
    namespace = {"part": drawing.working_part}
    exec(source, namespace)
    replayed = namespace["drawing"]
    assert "detail_a" in replayed.views
    assert replayed.registry.named("detail_a_steplen0").label == "3 ±0.2"

    def claims(value):
        return Counter(
            (identity.parameter, identity.feature.frame, identity.feature.span, mark.label)
            for name, mark in value.iter_annotations()
            for identity in value.registry.measurement_of(name)
        )

    assert claims(drawing) == claims(replayed)
    assert not [issue for issue in replayed.lint() if issue.severity != "info"]
    assert (tmp_path / "shoulder.svg").is_file()


@pytest.mark.parametrize("omitted", ("step.length", "step.diameter"))
def test_shoulder_detail_does_not_reconstruct_omitted_content(omitted):
    drawing = _shoulder_detail_sheet(omitted=omitted).build()
    assert "detail_a" in drawing.views
    measurements = [
        identity.parameter
        for name in drawing.registry.names()
        if drawing.view_of(name) == "detail_a"
        for identity in drawing.registry.measurement_of(name)
    ]
    assert measurements == ([] if omitted == "step.length" else ["step.length"])


@pytest.mark.parametrize("failure", ("no-ink", "no-room"))
def test_authored_shoulder_detail_refuses_unfulfilled_recovery(monkeypatch, failure):
    from draftwright.annotations import from_model

    redraws = []
    if failure == "no-ink":
        original = from_model._draw_step_chain

        def decline_detail(dwg, view, *args, **kwargs):
            if view == "detail_a":
                redraws.append(view)
                return 0
            return original(dwg, view, *args, **kwargs)

        monkeypatch.setattr(from_model, "_draw_step_chain", decline_detail)
    sheet = _shoulder_detail_sheet(factor=1000 if failure == "no-room" else 3)
    with pytest.raises(ValueError, match="authored detail.*not relaxed"):
        sheet.build()
    assert redraws == (["detail_a"] if failure == "no-ink" else [])


@pytest.mark.parametrize("unresolved_neighbour", (False, True))
def test_detail_retracts_only_recovered_main_view_length_drops(monkeypatch, unresolved_neighbour):
    from draftwright.annotations import from_model

    original = from_model._draw_step_chain
    refused = []

    def decline_main(dwg, view, segments, *args, ctx, **kwargs):
        if view == "front":
            kept = []
            for segment in segments:
                if segment.value == 3 or unresolved_neighbour:
                    refused.extend(segment.measurements)
                    ctx.record_issue(
                        "warning",
                        "step_dim_dropped",
                        "main-view placement refused",
                        measurement=segment.measurements,
                    )
                else:
                    kept.append(segment)
            segments = kept
        return original(dwg, view, segments, *args, ctx=ctx, **kwargs)

    monkeypatch.setattr(from_model, "_draw_step_chain", decline_main)
    drawing = _shoulder_detail_sheet().build()
    assert len(refused) == (2 if unresolved_neighbour else 1)
    assert drawing.registry.named("detail_a_steplen0").label == "3 ±0.2"
    remaining = [issue for issue in drawing.lint() if issue.code == "step_dim_dropped"]
    assert len(remaining) == int(unresolved_neighbour)
    assert all(
        identity.feature.length == 22 for issue in remaining for identity in issue.measurement_ids
    )
    if remaining:
        from draftwright.linting import _suggest_fix

        suggestion = _suggest_fix(remaining[0], drawing)
        assert 'sheet.detail_view("A", around=shoulder).scale(3)' in suggestion
        assert "detail_view=True" not in suggestion


def test_naturally_off_page_z_shoulders_are_recovered_and_read_from_details(monkeypatch):
    part = Pos(0, 0, -14) * Cylinder(15, 22) + Pos(0, 0, -1.5) * Cylinder(10, 3)
    sheet = Sheet(part, page="A4", scale=1, scale_policy="permissive").authored_dimensions()
    # Pin a whole view through the public semantic surface. Its geometry fits,
    # while its shoulder labels naturally exceed the remaining page margin.
    sheet.view("front").pin((260, 125))
    handles = []
    for diameter, station, length in ((30, -14, 22), (20, -1.5, 3)):
        handle = sheet.step(
            diameter=diameter,
            length=length,
            at=(0, 0, station),
            axis="z",
            profile_group="profile",
        )
        handle.tolerance(0.2, on="length")
        sheet.dimension(handle, "step.length")
        handles.append(handle)
    cramped = sheet.build()
    assert not any(cramped.measurement_keys(name) for name in cramped.annotations())
    drops = [issue for issue in cramped.lint() if issue.code == "step_dim_dropped"]
    assert len(drops) == 2 and all("off the drawable page" in issue.message for issue in drops)

    for label, handle in zip(("A", "B"), handles, strict=True):
        sheet.detail_view(label, around=handle).scale(3)
    recovered = sheet.build()
    assert Counter(
        mark.label
        for name, mark in recovered.iter_annotations()
        if recovered.measurement_keys(name)
    ) == Counter({"22 ±0.2": 1, "3 ±0.2": 1})
    assert not [
        issue
        for issue in recovered.lint()
        if issue.code in {"step_dim_dropped", "axial_length_missing", "annotation_ink_overlap"}
    ]

    # An end-on projection must not certify every shoulder from one coincident
    # endpoint. Preserve the drawn geometry while corrupting only that projection.
    project = recovered.at
    witness = recovered.get_annotation("detail_b_steplen0")._dw_spec
    assert witness.p1[0] == witness.p2[0]
    with monkeypatch.context() as patch:
        patch.setattr(
            recovered,
            "at",
            lambda view, x, y, z: (
                witness.p1 if view.startswith("detail_") else project(view, x, y, z)
            ),
        )
        assert any(issue.code == "axial_length_missing" for issue in recovered.lint())
    recovered.remove("detail_b_steplen0")
    assert any(issue.code == "axial_length_missing" for issue in recovered.lint())
