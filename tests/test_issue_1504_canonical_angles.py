"""An angle is a named compiled measurement, including tolerances and omissions."""

import json
from dataclasses import replace
from math import acos, cos, dist, pi, radians, sin, sqrt, tan
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Axis, Cylinder, GeomType, Polygon, Pos, extrude, fillet
from jsonschema import ValidationError
from jsonschema.validators import validator_for

from draftwright import ReportUnavailableError, Sheet, build_drawing
from draftwright.audit import compare_measurements
from draftwright.linting.angular import (
    lint_angular_supports,
    profile_angle_requirement_outcomes,
)
from draftwright.model import AngularReference, angle
from draftwright.model.compiled import compile_dimensions
from draftwright.profile_angles import profile_angle_repetitions
from draftwright.reporting import drawing_report
from draftwright.sheet_emit import emit_sheet_script


@pytest.fixture(scope="module")
def angle_part():
    return extrude(Polygon((0, 0), (30, 0), (15, 15 * sqrt(3)), align=None), amount=3)


def _declared(part, tolerance=None, *, scale=1, sector="minor"):
    sheet = Sheet(part, scale=scale)
    handle = sheet.angle(
        vertex=(0, 0, 3), first=(15, 0, 3), second=(7.5, 7.5 * sqrt(3), 3), sector=sector
    )
    if tolerance is not None:
        handle.tolerance(*tolerance, on="included.angle")
    sheet.dimension(handle, "included.angle")
    return sheet, handle


def _approved(model):
    return [entry for group in compile_dimensions(model).of_kind("angle") for entry in group.dims]


@pytest.mark.parametrize(
    ("tolerance", "label"),
    [
        (None, "60°"),
        ((0.05,), "60° ±0.05°"),
        ((0.0, 0.1), "60° +0.1° -0.0°"),
        ((0.000000123,), "60° ±0.000000123°"),
    ],
)
@pytest.mark.parametrize("scale", (None, 1))
@pytest.mark.parametrize("sector", ("minor", "opposite"))
def test_canonical_angle_and_complete_tolerance_reach_the_same_mark(
    angle_part, tolerance, label, scale, sector
):
    sheet, handle = _declared(angle_part, tolerance, scale=scale, sector=sector)
    assert handle.dimension_ids() == ("included.angle",)
    (entry,) = _approved(sheet.model())
    assert entry.value == pytest.approx(60)
    assert entry.final_label == label and entry.angular_reference is not None
    drawing = sheet.build()
    marks = [
        (name, item)
        for name, item in drawing.iter_annotations()
        if hasattr(item, "measured_angle")
    ]
    assert len(marks) == 1 and marks[0][1].label == label
    (identity,) = drawing.registry.measurement_of(marks[0][0])
    assert identity.parameter == "included.angle" and identity.feature is entry.id.feature
    assert compare_measurements(drawing, drawing)["status"] == "preserved"


def test_omission_removes_angular_ink_and_reports_a_lost_named_measurement(angle_part):
    sheet, _ = _declared(angle_part)
    model = sheet.model()
    assert len(_approved(model)) == 1
    omitted = replace(model, authored_dimensions=())
    assert _approved(omitted) == []
    plan = compile_dimensions(omitted)
    assert any(
        item.parameter_id == "included.angle" and "authored" in item.reason
        for item in plan.diagnostics
    )
    before, after = [build_drawing(angle_part, model=value, scale=1) for value in (model, omitted)]
    comparison = compare_measurements(before, after)
    assert comparison["status"] == "changed"
    assert [item["parameter_id"] for item in comparison["lost"]] == ["included.angle"]
    assert not [item for _, item in after.iter_annotations() if hasattr(item, "measured_angle")]


@pytest.mark.parametrize("sector", ("minor", "opposite"))
def test_script_executes_with_exact_references_and_small_tolerances(angle_part, tmp_path, sector):
    sheet, _ = _declared(angle_part, (0.000000123,), sector=sector)
    source = emit_sheet_script(
        sheet.model(), "part", str(tmp_path / "angle"), title="T", number="N", formats=("svg",)
    )
    namespace = {"part": angle_part}
    exec(source, namespace)
    (original,) = _approved(sheet.model())
    (replayed,) = _approved(namespace["sheet"].model())
    assert replayed.angular_reference == original.angular_reference
    assert replayed.final_label == original.final_label
    marks = [
        item
        for _, item in namespace["drawing"].iter_annotations()
        if hasattr(item, "measured_angle")
    ]
    assert len(marks) == 1 and marks[0].label == original.final_label
    assert (tmp_path / "angle.svg").is_file()


def test_equal_valued_support_substitution_is_not_reported_preserved(angle_part):
    sheet, _ = _declared(angle_part)
    old = sheet.model()
    owner = old.features[0]
    reference = owner.angular_reference
    moved = replace(reference, vertex=(3, 0, 3), first=(18, 0, 3), second=(10.5, 7.5 * sqrt(3), 3))
    replacement = replace(owner, angular_reference=moved)
    new = replace(
        old,
        features=(replacement,),
        authored_dimensions=tuple(
            replace(request, feature=replacement) for request in old.authored_dimensions
        ),
    )
    assert _approved(old)[0].value == _approved(new)[0].value
    before, after = [build_drawing(angle_part, model=model, scale=1) for model in (old, new)]
    comparison = compare_measurements(before, after, feature_pairs=((owner, replacement),))
    assert comparison["status"] == "changed" and comparison["changed"]


def test_angular_options_cannot_choose_a_distorted_view_or_opposite_sector(angle_part):
    sheet, handle = _declared(angle_part)
    assert sheet.validate_dimension(handle, "included.angle", view="plan", side="right")[
        "supported"
    ]
    assert not sheet.validate_dimension(handle, "included.angle", view="front")["supported"]
    assert not sheet.validate_dimension(handle, "included.angle", side="left")["supported"]


def test_canonical_oblique_angle_is_refused_before_it_can_select_an_apparent_view():
    reference = AngularReference((0, 0, 0), (1, 1, 0), (0, 0, 1))
    assert reference.principal_axis == "?"
    with pytest.raises(ValueError, match="oblique is unsupported"):
        angle(vertex=reference.vertex, first=reference.first, second=reference.second)


@pytest.mark.parametrize(
    ("deferred", "pin", "priority"), ((False, True, 2), (True, True, 2), (True, False, 3))
)
def test_live_and_deferred_edits_preserve_the_compiled_angle_and_pin(
    angle_part, tmp_path, deferred, pin, priority
):
    sheet, _ = _declared(angle_part, (0.05,))
    drawing = build_drawing(
        angle_part, model=sheet.model(), scale=1, trace=True, out=str(tmp_path / "edit")
    )
    owner = drawing.model().features[0]
    before = drawing.measurement_snapshot()
    drawing.drop(owner)
    assert not drawing.annotations_of(owner)
    if deferred:
        with drawing.deferred():
            drawing.dimension(
                owner, "included.angle", name="edited_angle", pin=pin, priority=priority
            )
    else:
        assert (
            drawing.dimension(
                owner, "included.angle", name="edited_angle", pin=pin, priority=priority
            )
            == "edited_angle"
        )
    assert drawing.registry.is_pinned("edited_angle") is pin
    assert drawing.registry.named("edited_angle").label == "60° ±0.05°"
    assert compare_measurements(before, drawing)["status"] == "preserved"
    candidates = [
        candidate
        for solve in drawing.solve_trace.solves
        for candidate in solve["candidates"]
        if candidate["name"] == "edited_angle"
    ]
    assert candidates and all(candidate["anchored"] is pin for candidate in candidates)
    assert all(
        candidate["priority"] == (max(priority, 100) if pin else priority)
        for candidate in candidates
    )
    if pin:
        label = drawing.registry.named("edited_angle").label_bbox
        positions = [
            (stage["axis"], item.get("strip_pos", item["pos"]), stage["span"])
            for solve in drawing.solve_trace.solves
            for stage in solve["passes"]
            for item in stage["placed"]
            if item["name"] == "edited_angle"
        ]
        assert positions
        for axis, position, span in positions:
            index = 0 if axis == "x" else 1
            centre = (label[index] + label[index + 2]) / 2
            assert centre == pytest.approx(position)
            # A pin cannot be recovered at a compact position outside the
            # chosen strip merely because its smaller ink happens to be clear.
            assert span[0] - 1e-6 <= centre <= span[1] + 1e-6


def test_omitting_an_approved_angular_tolerance_is_not_a_confirmed_claim(angle_part):
    sheet, _ = _declared(angle_part, (0.05,))
    drawing = sheet.build()
    assert not [finding for finding in drawing.lint() if finding.code == "claimed_value_absent"]
    mark = drawing.registry.named("m_angle_0")
    assert mark.label == "60° ±0.05°"
    mark.label = "60°"
    assert mark.measured_angle == pytest.approx(60)
    assert [finding for finding in drawing.lint() if finding.code == "claimed_value_absent"]
    assert drawing.measurement_snapshot().unknown


@pytest.mark.parametrize("corruption", ("floating-witnesses", "wrong-vertex"))
def test_declared_rays_do_not_prove_their_own_physical_supports(angle_part, corruption):
    sheet, _ = _declared(angle_part)
    model = sheet.model()
    owner = model.features[0]
    reference = owner.angular_reference
    if corruption == "floating-witnesses":
        reference = replace(reference, first=(45, 0, 3), second=(22.5, 22.5 * sqrt(3), 3))
    else:
        reference = AngularReference(
            *(
                tuple(x + offset for x, offset in zip(point, (5, 5, 0), strict=True))
                for point in (reference.vertex, reference.first, reference.second)
            )
        )
    changed = replace(owner, angular_reference=reference)
    model = replace(
        model,
        features=(changed,),
        authored_dimensions=tuple(
            replace(request, feature=changed) for request in model.authored_dimensions
        ),
    )
    drawing = build_drawing(angle_part, model=model, scale=1)
    assert drawing.registry.named("m_angle_0").label == "60°"
    issues = drawing.lint()
    assert not [issue for issue in issues if issue.code == "angular_geometry_mismatch"]
    assert [issue.code for issue in issues if issue.code.startswith("angular_support_")] == [
        "angular_support_unverifiable"
    ]
    assert drawing.lint_summary()["quality"]["fidelity"]["score"] < 1


@pytest.mark.parametrize("case", ("valid", "omitted", "duplicate", "floating", "absent"))
def test_declared_angle_coverage_requires_a_unique_finite_physical_corner(angle_part, case):
    sheet, _ = _declared(angle_part)
    drawing = sheet.build()
    assert drawing.recognition_ownership() is None
    evidence = drawing.recognition_evidence()
    model = drawing.model()
    owner = model.features[0]
    features, omissions = model.features, ()
    if case == "duplicate":
        features = (*features, replace(owner))
    elif case == "floating":
        features = (
            replace(
                owner,
                angular_reference=replace(
                    owner.angular_reference, first=(45, 0, 3), second=(22.5, 22.5 * sqrt(3), 3)
                ),
            ),
        )
    elif case == "absent":
        features = ()
    elif case == "omitted":
        for name in tuple(drawing.annotations_of(owner)):
            drawing.remove(name)
        omissions = (SimpleNamespace(feature=owner, parameter_id="included.angle", authored=True),)
    outcomes = profile_angle_requirement_outcomes(
        evidence, None, features, drawing.registry, omissions
    )
    assert len(outcomes) == 3, "all physical corners remain, including undeclared corners"
    (corner,) = [
        outcome for outcome in outcomes if dist(outcome.source_profile.vertex, (0, 0, 3)) < 1e-6
    ]
    assert (
        corner.state
        == {
            "valid": "placed",
            "omitted": "suppressed",
            "duplicate": "unverifiable",
            "floating": "unverifiable",
            "absent": "unverifiable",
        }[case]
    )
    assert sum(outcome.state == "unverifiable" for outcome in outcomes) == (
        2 if case in {"valid", "omitted"} else 3
    )
    assert drawing.recognition_ownership() is None, "physical critique must not invent ownership"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"param": "length"}, "included.angle"),
        ({"view": "front"}, "cannot render"),
        ({"side": "left"}, "cannot render"),
        ({"label": "120°"}, "unsupported angular edit controls"),
    ],
)
def test_angular_edits_refuse_wrong_measurements_views_and_raw_content(
    angle_part, options, message
):
    sheet, _ = _declared(angle_part)
    drawing = sheet.build()
    owner = drawing.model().features[0]
    arguments = {"param": "included.angle", **options}
    before = drawing.measurement_snapshot()
    with pytest.raises(ValueError, match=message):
        drawing.dimension(owner, **arguments)
    assert compare_measurements(before, drawing)["status"] == "preserved"


def test_an_equal_valued_foreign_angle_does_not_acquire_edit_authority(angle_part):
    sheet, _ = _declared(angle_part)
    drawing = sheet.build()
    owner = drawing.model().features[0]
    foreign = replace(owner)
    assert owner == foreign and owner is not foreign
    with pytest.raises(ValueError, match="exact feature"):
        drawing.dimension(foreign, "included.angle")


@pytest.mark.parametrize("tolerance", (-0.1, float("inf"), float("nan")))
def test_invalid_angular_tolerances_fail_before_ink(angle_part, tolerance):
    sheet, _ = _declared(angle_part, (tolerance,))
    with pytest.raises(ValueError, match="angular tolerance"):
        compile_dimensions(sheet.model())


def test_unplaceable_canonical_angle_reports_its_measurement_identity():
    theta = radians(0.25)
    part = extrude(
        Polygon((0, 0), (30, 0), (30 * cos(theta), 30 * sin(theta)), align=None), amount=3
    )
    assert min(part.bounding_box().size) > 0.1
    sheet = Sheet(part, scale=1, scale_policy="permissive")
    handle = sheet.angle(
        vertex=(0, 0, 3), first=(15, 0, 3), second=(15 * cos(theta), 15 * sin(theta), 3)
    )
    sheet.dimension(handle, "included.angle")
    assert len(_approved(sheet.model())) == 1
    drawing = sheet.build()
    findings = [
        finding
        for finding in drawing.lint(physical=False)
        if finding.code == "angular_dimension_dropped"
    ]
    assert findings and any(
        key.parameter == "included.angle" and key.feature is drawing.model().features[0]
        for finding in findings
        for key in finding.measurement_ids
    )
    assert not [item for _, item in drawing.iter_annotations() if hasattr(item, "measured_angle")]


@pytest.fixture(scope="module")
def rounded_flange_drawings():
    vertices = tuple(
        (
            (60 if index % 2 == 0 else 40) * cos(pi / 6 + index * pi / 3),
            (60 if index % 2 == 0 else 40) * sin(pi / 6 + index * pi / 3),
            3,
        )
        for index in range(6)
    )
    part = extrude(Polygon(*(point[:2] for point in vertices), align=None), amount=6)
    angles = []
    for index, vertex in enumerate(vertices):
        radius = 3 if index % 2 == 0 else 8
        edge = min(
            part.edges().filter_by(Axis.Z), key=lambda item: dist(tuple(item.center()), vertex)
        )
        assert dist(tuple(edge.center()), vertex) < 1e-6
        part = fillet(edge, radius)
        rays = [
            tuple((p - v) / dist(point, vertex) for p, v in zip(point, vertex, strict=True))
            for point in (vertices[index - 1], vertices[(index + 1) % 6])
        ]
        included = acos(sum(a * b for a, b in zip(*rays, strict=True)))
        tangent = radius / tan(included / 2)
        points = [tuple(v + tangent * u for v, u in zip(vertex, ray, strict=True)) for ray in rays]
        angles.append(
            angle(
                vertex=(*vertex[:2], 6),
                first=(*points[0][:2], 6),
                second=(*points[1][:2], 6),
                virtual_vertex=True,
                sector="opposite",
            )
        )
    for index in range(3):
        theta = pi / 6 + index * 2 * pi / 3
        part -= Pos(20 * cos(theta), 20 * sin(theta), 3) * Cylinder(3, 12)
    cap = part.faces().filter_by(GeomType.PLANE).sort_by(Axis.Z)[-1]
    assert len(cap.outer_wire().edges()) == 12
    assert len(cap.inner_wires()) == 3
    options = dict(scale=1, scale_policy="permissive", page="A2")
    after = build_drawing(part, **options)
    detected = tuple(feature for feature in after.model().features if feature.kind == "angle")
    assert len(detected) == 2
    detected_references = tuple(
        parameter.angular_reference for feature in detected for parameter in feature.parameters()
    )
    assert len(detected_references) == len(angles) == 6
    for authored in angles:
        expected = authored.angular_reference
        actual = min(
            detected_references, key=lambda reference: dist(reference.vertex, expected.vertex)
        )
        assert actual.vertex == pytest.approx(expected.vertex)
        assert actual.virtual_vertex and actual.sector == expected.sector
        for point in (expected.first, expected.second):
            assert min(dist(point, actual.first), dist(point, actual.second)) < 1e-6
    model = replace(
        after.model(),
        features=tuple(feature for feature in after.model().features if feature.kind != "angle"),
    )
    before = build_drawing(part, model=model, **options)
    return before, after, detected


def test_rounded_flange_keeps_all_six_corner_angles_and_existing_claims(rounded_flange_drawings):
    before, after, angles = rounded_flange_drawings
    # Independent construction: the alternating turns sum to 720 degrees.
    expected = (2 * 180 / pi * acos(2 / sqrt(7)), 240 - 2 * 180 / pi * acos(2 / sqrt(7)))
    assert sorted(
        parameter.value for item in angles for parameter in item.parameters()
    ) == pytest.approx(sorted(expected * 3))
    for feature in angles:
        marks = [
            item
            for item in after.annotations_of(feature).values()
            if hasattr(item, "measured_angle")
        ]
        assert len(marks) == 1
        assert marks[0].label.startswith("3× ")
        assert marks[0].arc_radius < 40, "a corner dimension must not expand across the whole part"
    old = {(id(c.owner), c.parameter): c.meaning for c in before.measurement_snapshot().claims}
    new = {(id(c.owner), c.parameter): c.meaning for c in after.measurement_snapshot().claims}
    assert old and {kind for _owner, kind in old} >= {"bore.diameter", "blend.radius"}
    assert all(new.get(key) == value for key, value in old.items())
    assert not [issue for issue in after.lint(physical=False) if issue.code.startswith("angular_")]


def test_detected_rounded_angles_have_same_run_support_ownership(rounded_flange_drawings):
    _before, drawing, angles = rounded_flange_drawings
    evidence = drawing.recognition_evidence()
    bindings = drawing.recognition_ownership().profile_angles
    assert len(bindings) == sum(len(feature.parameters()) for feature in angles) == 6
    for feature in angles:
        for parameter in feature.parameters():
            (binding,) = [
                binding
                for binding in bindings
                if binding.feature is feature and binding.parameter_id == parameter.parameter_id
            ]
            source = binding.requirement
            assert evidence.planar_outer_profile(source.source.face) is source.source
            reference = parameter.angular_reference
            assert source.vertex == reference.vertex and source.virtual_vertex
            for index, point in (
                (source.first_index, reference.first),
                (source.second_index, reference.second),
            ):
                edge = evidence.profile_edge(source.source, index)
                assert min(dist(point, tuple(edge.position_at(t))) for t in (0, 1)) < 1e-6
    assert not [issue for issue in drawing.lint() if issue.code.startswith("angular_support_")]
    repetitions = profile_angle_repetitions(evidence)
    assert len(repetitions) == 2
    assert [len(group.members) for group in repetitions] == [3, 3]
    assert {
        (member.source.face, member.first_index, member.second_index)
        for group in repetitions
        for member in group.members
    } == {
        (
            binding.requirement.source.face,
            binding.requirement.first_index,
            binding.requirement.second_index,
        )
        for binding in bindings
    }


def test_a_self_consistent_angle_moved_off_its_part_supports_is_contradicted(
    rounded_flange_drawings, monkeypatch
):
    _before, drawing, angles = rounded_flange_drawings
    name, mark = next(iter(drawing.annotations_of(angles[0]).items()))
    monkeypatch.setattr(mark, "location", mark.location * Pos(5, 0, 0))
    issues = drawing.lint()
    assert not [issue for issue in issues if issue.code == "angular_geometry_mismatch"]
    mismatches = [issue for issue in issues if issue.code == "angular_support_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0].measurement_ids == drawing.registry.measurement_of(name)
    assert (
        drawing.lint_summary()["quality"]["fidelity"]["by_code"]["angular_support_mismatch"] == 1
    )


@pytest.mark.parametrize("corruption", ("absent-owner", "equal-copy", "absent-binding"))
def test_profile_denominator_survives_lost_ir_or_conversion_binding(
    rounded_flange_drawings, corruption
):
    _before, drawing, angles = rounded_flange_drawings
    ownership = drawing.recognition_ownership()
    features = drawing.model().features
    if corruption == "absent-binding":
        ownership = replace(ownership, profile_angles=())
    else:
        features = tuple(
            replace(feature) if corruption == "equal-copy" and feature is angles[0] else feature
            for feature in features
            if corruption != "absent-owner" or feature is not angles[0]
        )
    outcomes = profile_angle_requirement_outcomes(
        drawing.recognition_evidence(), ownership, features, drawing.registry
    )
    assert len(outcomes) == 6, "IR removal cannot shrink the six physical corner requirements"
    assert sum(item.state == "placed" for item in outcomes) == (
        0 if corruption == "absent-binding" else 3
    )
    assert sum(item.state == "unverifiable" for item in outcomes) == (
        6 if corruption == "absent-binding" else 0
    )
    assert sum(item.state == "missing" for item in outcomes) == (
        0 if corruption == "absent-binding" else 3
    )


def test_removing_an_angle_reduces_coverage_and_keeps_its_profile_source_in_report(
    rounded_flange_drawings,
):
    _before, drawing, angles = rounded_flange_drawings
    before = drawing.lint_summary()["quality"]["completeness"]
    assert before["by_family"]["outer_profile_angles"] == 6
    name = next(iter(drawing.annotations_of(angles[0])))
    saved_registry, saved_items = drawing.registry.snapshot(), list(drawing.items)
    drawing.remove(name)
    try:
        after = drawing.lint_summary()["quality"]["completeness"]
        assert after["requirements"] == before["requirements"]
        assert after["placed"] == before["placed"] - 3
        assert after["missing"] == before["missing"] + 3
        assert [
            issue.code for issue in drawing.lint() if issue.code.startswith("angular_requirement_")
        ] == ["angular_requirement_missing"] * 3
        report = drawing.report()
        rows = [
            row
            for row in report["recognition"]["requirements"]
            if row["family"] == "outer_profile_angles"
        ]
        assert len(rows) == 6 and sum(row["state"] == "missing" for row in rows) == 3
        assert all(row["occurrence_ids"] == [] and len(row["owner_ids"]) == 1 for row in rows)
        assert all(row["profile_source"]["kind"] == "planar_outer_profile" for row in rows)
        assert (
            len(
                {
                    (
                        row["profile_source"]["profile_id"],
                        tuple(row["profile_source"]["support_ids"]),
                    )
                    for row in rows
                }
            )
            == 6
        )
        schema = json.loads(
            (
                Path(__file__).parents[1] / "docs/reference/draftwright-report-v3.schema.json"
            ).read_text()
        )
        validator_for(schema).check_schema(schema)
        validator = validator_for(schema)(schema)
        validator.validate(report)
        # A profile requirement cannot masquerade as an accepted occurrence,
        # and losing its source cannot produce a valid empty-source requirement.
        original = dict(rows[0])
        for malformed in (
            {key: value for key, value in original.items() if key != "profile_source"},
            {**original, "occurrence_ids": ["invented:1"]},
            {
                **original,
                "profile_source": {
                    **original["profile_source"],
                    "support_ids": ["support:1", "support:1"],
                },
            },
        ):
            rows[0].clear()
            rows[0].update(malformed)
            with pytest.raises(ValidationError):
                validator.validate(report)
        rows[0].clear()
        rows[0].update(original)
    finally:
        drawing.registry.restore(saved_registry)
        drawing.items[:] = saved_items


@pytest.mark.parametrize("corruption", ("missing-requirement", "unissued-source-copy"))
def test_report_refuses_a_shrunken_or_foreign_profile_denominator(
    rounded_flange_drawings, corruption
):
    from draftwright.linting.requirements import recognized_requirement_outcomes

    _before, drawing, _angles = rounded_flange_drawings
    evidence, ownership, model = (
        drawing.recognition_evidence(),
        drawing.recognition_ownership(),
        drawing.model(),
    )
    outcomes = dict(
        recognized_requirement_outcomes(
            evidence.result,
            model.features,
            drawing.registry,
            (),
            evidence=evidence,
            ownership=ownership,
        )
    )
    corners = outcomes["outer_profile_angles"]
    assert len(corners) == 6
    if corruption == "missing-requirement":
        outcomes["outer_profile_angles"] = corners[1:]
    else:
        requirement = corners[0].source_profile
        source = requirement.source
        unissued = SimpleNamespace(
            face=source.face, profile=source.profile, body_faces=source.body_faces
        )
        copied = replace(requirement, source=unissued)
        outcomes["outer_profile_angles"] = (
            replace(corners[0], source_profile=copied),
            *corners[1:],
        )
    with pytest.raises(ReportUnavailableError, match="profile requirement ledger"):
        drawing_report(
            evidence=evidence,
            ownership=ownership,
            model=model,
            lint={},
            source=None,
            registry=drawing.registry,
            requirement_outcomes=outcomes,
        )


@pytest.mark.parametrize(
    "corruption",
    (
        "same-value-corner",
        "absent-authority",
        "physical-edge",
        "equal-copy-owner",
        "unshown-member",
        "absent-repetition",
    ),
)
def test_angular_support_proof_requires_the_exact_binding_and_actual_edges(
    rounded_flange_drawings, monkeypatch, corruption
):
    _before, drawing, angles = rounded_flange_drawings
    ownership = drawing.recognition_ownership()
    evidence = drawing.recognition_evidence()
    registry = drawing.registry
    binding = next(entry for entry in ownership.profile_angles if entry.feature is angles[0])
    mark = next(iter(drawing.annotations_of(angles[0]).values()))
    assert len(ownership.profile_angles) == 6
    code = "angular_support_mismatch"
    if corruption == "unshown-member":
        binding = next(
            entry
            for entry in ownership.profile_angles
            if entry.feature is angles[0]
            and dist(drawing.at("plan", *entry.requirement.vertex)[:2], mark.angular_points[1]) > 1
        )
    if corruption == "same-value-corner":
        other = next(
            entry
            for entry in ownership.profile_angles
            if entry.feature is binding.feature and entry.parameter_id != binding.parameter_id
        )
        ownership = replace(
            ownership,
            profile_angles=tuple(
                replace(entry, requirement=other.requirement) if entry is binding else entry
                for entry in ownership.profile_angles
            ),
        )
    elif corruption == "absent-authority":
        evidence = None
        code = "angular_support_unverifiable"
    elif corruption == "equal-copy-owner":
        from draftwright.registry import AnnotationRegistry

        registry = AnnotationRegistry()
        registry.add(mark, "copy", "plan", feature=replace(binding.feature))
        code = "angular_support_unverifiable"
    elif corruption == "absent-repetition":
        monkeypatch.setattr("draftwright.linting.angular.profile_angle_repetitions", lambda _: ())
    else:
        physical_edge = type(evidence).profile_edge

        def wrong_edge(self, source, index):
            if source is binding.requirement.source and index == binding.requirement.first_index:
                index = binding.requirement.second_index
            return physical_edge(self, source, index)

        monkeypatch.setattr(type(evidence), "profile_edge", wrong_edge)
    issues = lint_angular_supports(
        [mark],
        registry=registry,
        evidence=evidence,
        ownership=ownership,
        to_page=drawing.at,
    )
    assert [issue.code for issue in issues] == [code]


def test_compact_angle_cannot_move_its_label_onto_a_fixed_note(angle_part):
    sheet, _handle = _declared(angle_part, sector="opposite")
    drawing = sheet.build()
    owner = drawing.model().features[0]
    original = next(iter(drawing.annotations_of(owner).values()))
    box = original.label_bbox
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    drawing.drop(owner)
    note = drawing.note("KEEP CLEAR", centre, view="plan")
    fixed = drawing.registry.named(note).bounding_box()
    assert fixed.min.X < centre[0] < fixed.max.X and fixed.min.Y < centre[1] < fixed.max.Y
    name = drawing.dimension(owner, "included.angle")
    rebuilt = drawing.registry.named(name)
    if rebuilt is None:
        assert any(
            issue.code == "angular_dimension_dropped"
            and any(identity.feature is owner for identity in issue.measurement_ids)
            for issue in drawing.lint(physical=False)
        )
        return
    label = rebuilt.label_bbox
    assert rebuilt.arc_radius > original.arc_radius
    overlap = min(label[2], fixed.max.X) > max(label[0], fixed.min.X) and min(
        label[3], fixed.max.Y
    ) > max(label[1], fixed.min.Y)
    assert not overlap
