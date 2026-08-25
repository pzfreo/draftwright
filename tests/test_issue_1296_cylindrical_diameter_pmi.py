"""#1296 — one cylindrical AP242 face owns one canonical diameter annotation."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, Cylinder, import_step

from draftwright import Sheet, build_drawing
from draftwright.model import plan_dimensions
from draftwright.model.declare import measured_dimension
from draftwright.model.ir import (
    AuthoredDimension,
    BossFeature,
    CylindricalReference,
    Frame,
    HoleFeature,
    NominalRequirement,
    PartModel,
    PatternFeature,
    RotationalFeature,
    StepFeature,
    ToleranceDecoration,
)
from draftwright.model.pmi_lowering import (
    _external_owner_matches,
    _internal_member_matches,
    _standalone_cylinder_blocker,
    lower_ap242_dimensions,
    lower_ap242_nominal_diameters,
)
from draftwright.pmi import _diameter_reference_blockers, extract_pmi_report
from draftwright.sheet import _requirement_parameter
from draftwright.sheet_emit import emit_sheet_script

FIXTURE = Path(__file__).parent / "fixtures" / "ap242_single_cylinder_diameter.step"
FIXTURE_SHA256 = "e1a819891ceadf5ac95c0c018713f839dd0532098d380224fd69240b3542c306"
GRM03 = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
GRM03_SHA256 = "4b6462b9cc9f0d419250933bd77fb305f9cfebb7ec2b3f377008732876010a21"
GRM_DIAMETERS = {f"dimension:0:1:4:{index}" for index in range(1, 6)}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cylinder(
    *,
    interval=(-3.2, 0.0),
    diameter=4.0,
    axis_origin=(0.0, 0.0, 0.0),
    sense="external",
) -> CylindricalReference:
    return CylindricalReference(
        axis_origin=axis_origin,
        axis_direction=(1.0, 0.0, 0.0),
        radius=diameter / 2,
        axial_interval=interval,
        sense=sense,
    )


def _dimension(reference: CylindricalReference, source_id="dimension:test") -> AuthoredDimension:
    return AuthoredDimension(
        frame=Frame(reference.midpoint, "x"),
        dimension_kind="diameter",
        value=reference.diameter,
        label=f"ø{reference.diameter:g}",
        dominant_axis="X",
        ref_pts=(reference.midpoint,),
        source_id=source_id,
        cylindrical_refs=(reference,),
    )


def _requirement_signature(model):
    return sorted(
        (key[0].kind, key[1:], value.value, value.source, value.source_ids)
        for key, value in model.decorations.items()
        if isinstance(value, NominalRequirement)
    )


def _names_for_parameter(drawing, key) -> list[str]:
    if key[1] == "nominal_requirement":
        expected = key[2]
        kind = expected.rsplit(".", 1)[-1]
    else:
        kind = key[1]
        role = key[2] if len(key) > 2 else ""
        expected = f"{role}.{kind}" if role else ""
    return sorted(
        name
        for name in drawing.registry.names()
        if any(
            measurement.feature == key[0]
            and (
                measurement.parameter == expected
                if expected
                else measurement.parameter.endswith(f".{kind}")
            )
            for measurement in drawing.registry.measurement_of(name)
        )
    )


def test_minimal_fixture_is_redistributable_generated_ap242_and_uses_topology_axis():
    assert _sha256(FIXTURE) == FIXTURE_SHA256
    report = extract_pmi_report(FIXTURE)
    assert report.error is None
    assert [(source.source_id, source.outcome) for source in report.sources] == [
        ("dimension:0:1:4:1", "extracted")
    ]
    (record,) = report.records
    assert record.kind == "diameter"
    assert record.value == 4
    assert record.dominant_axis == "X"  # its bbox's largest spans are Y/Z, not X
    assert len(record.ref_pts) == 1
    assert record.lowering_blockers == record.rendering_blockers == ()
    assert record.cylindrical_refs == (
        CylindricalReference(
            axis_origin=(0.0, 2.0, 3.0),
            axis_direction=(1.0, 0.0, 0.0),
            radius=2.0,
            axial_interval=(-1.5, 1.7000000000000002),
            sense="external",
        ),
    )


def test_minimal_single_face_source_owns_existing_od_without_duplicate(tmp_path):
    drawing = build_drawing(FIXTURE, pmi="annotate", out=str(tmp_path / "single-cylinder"))
    assert not [
        feature for feature in drawing.model().features if isinstance(feature, AuthoredDimension)
    ]
    requirements = [
        (key, value)
        for key, value in drawing.model().decorations.items()
        if isinstance(value, NominalRequirement)
    ]
    assert len(requirements) == 1
    key, requirement = requirements[0]
    assert key[0].kind == "rotational"
    assert key[1:] == ("nominal_requirement", "od.diameter")
    assert requirement == NominalRequirement(4, "ap242_pmi", ("dimension:0:1:4:1",))
    assert _names_for_parameter(drawing, key) == ["dim_od"]
    assert not [issue for issue in drawing.lint() if issue.code == "pmi_not_rendered"]
    assert drawing.lint_summary()["pmi"] == {
        "mode": "annotate",
        "sources": 1,
        "by_category": {"dimension": 1},
        "extracted": 1,
        "lowered": 1,
        "rendered": 1,
        "dropped": 0,
    }

    source = emit_sheet_script(drawing.model(), "part", "single-cylinder", title="P", number="N")
    namespace = {"part": import_step(str(FIXTURE))}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<single-cylinder>", "exec"),
        namespace,
    )
    assert _requirement_signature(namespace["sheet"].model()) == _requirement_signature(
        drawing.model()
    )


def test_finite_span_and_axis_line_disambiguate_equal_nominal_steps():
    first = StepFeature(
        frame=Frame((-1.6, 0.0, 0.0), "x"),
        length=3.2,
        diameter=4,
        span=((-3.2, 0.0, 0.0), (0.0, 0.0, 0.0)),
    )
    second = StepFeature(
        frame=Frame((11.6, 0.0, 0.0), "x"),
        length=3.2,
        diameter=4,
        span=((10.0, 0.0, 0.0), (13.2, 0.0, 0.0)),
    )
    model = PartModel(
        Box(20, 10, 10).bounding_box(), "x", [first, second, _dimension(_cylinder())]
    )
    lowered = lower_ap242_nominal_diameters(model)
    assert lowered.features == [first, second]
    assert lowered.decorations == {
        (first, "nominal_requirement", "step.diameter"): NominalRequirement(
            4, "ap242_pmi", ("dimension:test",)
        )
    }

    offset = _dimension(_cylinder(axis_origin=(0.0, 7.0, 0.0)))
    unmatched = lower_ap242_nominal_diameters(PartModel(model.bbox, "x", [first, second, offset]))
    fallback = next(
        feature for feature in unmatched.features if isinstance(feature, AuthoredDimension)
    )
    assert fallback.lowering_blockers == (
        "unmatched diameter ownership: no canonical feature matches the source cylinder topology",
    )


def test_nominal_hole_ownership_coexists_with_bore_tolerance_and_round_trips():
    hole = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 4.0, 10.0, True)
    nominal = _dimension(
        _cylinder(interval=(-5.0, 5.0), diameter=4.0, sense="internal"),
        "dimension:nominal",
    )
    tolerance = ToleranceDecoration(0.05, "ap242_pmi", ("dimension:tolerance",))
    model = PartModel(
        Box(20, 10, 10).bounding_box(),
        "x",
        [hole, nominal],
        decorations={(hole, "diameter"): tolerance},
    )
    lowered = lower_ap242_nominal_diameters(model)
    assert lowered.features == [hole]
    assert lowered.decorations == {
        (hole, "diameter"): tolerance,
        (hole, "nominal_requirement", "bore.diameter"): NominalRequirement(
            4, "ap242_pmi", ("dimension:nominal",)
        ),
    }
    bore = next(
        dimension
        for group in plan_dimensions(lowered)
        if group.feature == hole
        for dimension in group.dims
        if dimension.param.kind == "diameter" and dimension.param.role == "bore"
    )
    assert bore.param.tolerance == 0.05

    source = emit_sheet_script(lowered, "part", "owned-hole", title="P", number="N")
    namespace = {"part": Box(20, 10, 10)}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<owned-hole>", "exec"),
        namespace,
    )
    rebuilt = namespace["sheet"].model()
    values = {key[1:]: value for key, value in rebuilt.decorations.items()}
    assert values == {
        ("diameter",): tolerance,
        ("nominal_requirement", "bore.diameter"): NominalRequirement(
            4, "ap242_pmi", ("dimension:nominal",)
        ),
    }

    declared = Sheet(Box(20, 10, 10))
    declared.auto_dimensions()
    declared.hole(diameter=4, at=(0, 0, 0), axis="x").tolerance(0.05).requirement(
        4, source="ap242_pmi", source_ids=("dimension:nominal",)
    )
    declared_model = declared.model()
    assert {key[1:] for key in declared_model.decorations} == {
        ("diameter",),
        ("nominal_requirement", "bore.diameter"),
    }
    declared_bore = next(
        dimension
        for group in plan_dimensions(declared_model)
        for dimension in group.dims
        if dimension.param.kind == "diameter" and dimension.param.role == "bore"
    )
    assert declared_bore.param.tolerance == 0.05


def test_through_hole_requires_finite_axial_provenance():
    hole = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 4.0, 10.0, True)
    remote = _dimension(
        _cylinder(interval=(100.0, 105.0), diameter=4.0, sense="internal"),
        "dimension:remote",
    )
    lowered = lower_ap242_nominal_diameters(
        PartModel(Box(20, 10, 10).bounding_box(), "x", [hole, remote])
    )
    fallback = next(
        feature for feature in lowered.features if isinstance(feature, AuthoredDimension)
    )
    assert fallback.lowering_blockers == (
        "unmatched diameter ownership: no canonical feature matches the source cylinder topology",
    )
    assert not [
        value for value in lowered.decorations.values() if isinstance(value, NominalRequirement)
    ]


def test_unmatched_distinct_cylinder_lines_fail_closed_instead_of_using_their_centroid():
    first = _cylinder(interval=(-2.0, 2.0), diameter=4.0, axis_origin=(0.0, 0.0, 0.0))
    second = _cylinder(interval=(-2.0, 2.0), diameter=4.0, axis_origin=(0.0, 10.0, 0.0))
    dimension = AuthoredDimension(
        frame=Frame(first.midpoint, "x"),
        dimension_kind="diameter",
        value=4.0,
        label="ø4",
        dominant_axis="X",
        ref_pts=(first.midpoint, second.midpoint),
        source_id="dimension:parallel",
        cylindrical_refs=(first, second),
    )
    lowered = lower_ap242_nominal_diameters(
        PartModel(Box(20, 20, 20).bounding_box(), None, [dimension])
    )
    (fallback,) = lowered.features
    assert isinstance(fallback, AuthoredDimension)
    assert fallback.rendering_blockers == (
        "standalone diameter fallback references multiple distinct cylinder axis lines; "
        "no single truthful leader target exists",
    )
    drawing = build_drawing(Box(20, 20, 20), model=lowered, pmi="annotate")
    assert not [name for name in drawing.annotations() if name.startswith("pmi_d_")]
    assert [issue for issue in drawing.lint() if issue.code == "authored_dim_source_unresolved"]

    # Tolerance lowering is a separate path, but an unmatched toleranced diameter needs the
    # same typed geometry reason rather than relying only on a defensive renderer refusal.
    toleranced = replace(dimension, upper_tol=0.1, lower_tol=0.1)
    toleranced_model = lower_ap242_dimensions(
        PartModel(Box(20, 20, 20).bounding_box(), None, [toleranced])
    )
    (toleranced_fallback,) = toleranced_model.features
    assert isinstance(toleranced_fallback, AuthoredDimension)
    assert toleranced_fallback.rendering_blockers == fallback.rendering_blockers
    toleranced_drawing = build_drawing(Box(20, 20, 20), model=toleranced_model, pmi="annotate")
    assert [
        issue
        for issue in toleranced_drawing.lint()
        if issue.code == "authored_dim_source_unresolved"
    ]


def test_unmatched_typed_cylinder_and_nominal_requirement_both_round_trip():
    reference = _cylinder(axis_origin=(0.0, 7.0, 0.0))
    fallback = lower_ap242_nominal_diameters(
        PartModel(Box(20, 20, 20).bounding_box(), None, [_dimension(reference)])
    )
    source = emit_sheet_script(fallback, "part", "fallback", title="P", number="N")
    namespace = {"part": Box(20, 20, 20)}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<cylinder-fallback>", "exec"),
        namespace,
    )
    rebuilt_fallback = namespace["sheet"].model()
    rebuilt_dimension = next(
        feature for feature in rebuilt_fallback.features if isinstance(feature, AuthoredDimension)
    )
    assert rebuilt_dimension.cylindrical_refs == (reference,)
    assert rebuilt_dimension.lowering_blockers == fallback.features[0].lowering_blockers

    exact_nominal = 4.1234
    step = StepFeature(
        frame=Frame((-1.6, 0.0, 0.0), "x"),
        length=3.2,
        diameter=exact_nominal,
        span=((-3.2, 0.0, 0.0), (0.0, 0.0, 0.0)),
    )
    lowered = lower_ap242_nominal_diameters(
        PartModel(
            Box(20, 20, 20).bounding_box(),
            "x",
            [step, _dimension(_cylinder(diameter=exact_nominal))],
        )
    )
    source = emit_sheet_script(lowered, "part", "owned", title="P", number="N")
    namespace = {"part": Box(20, 20, 20)}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<cylinder-owned>", "exec"),
        namespace,
    )
    rebuilt_owned = namespace["sheet"].model()
    assert _requirement_signature(rebuilt_owned) == _requirement_signature(lowered)
    rebuilt_step = next(
        feature for feature in rebuilt_owned.features if isinstance(feature, StepFeature)
    )
    assert rebuilt_step.diameter == exact_nominal
    assert plan_dimensions(rebuilt_owned)
    requirement_key = next(
        key
        for key, value in rebuilt_owned.decorations.items()
        if isinstance(value, NominalRequirement)
    )
    mismatched = replace(
        rebuilt_owned,
        decorations={
            **rebuilt_owned.decorations,
            requirement_key: NominalRequirement(
                exact_nominal + 0.001,
                "ap242_pmi",
                ("dimension:test",),
            ),
        },
    )
    with pytest.raises(ValueError, match="disagrees with step.diameter"):
        plan_dimensions(mismatched)

    large_step = replace(step, diameter=1000.0)
    large_model = PartModel(
        Box(1200, 20, 20).bounding_box(),
        "x",
        [large_step],
        decorations={
            (large_step, "nominal_requirement", "step.diameter"): NominalRequirement(
                1000.0005,
                "ap242_pmi",
                ("dimension:large",),
            )
        },
    )
    with pytest.raises(ValueError, match="disagrees with step.diameter"):
        plan_dimensions(large_model)


def test_all_supported_nominal_diameter_owners_round_trip_losslessly():
    step = StepFeature(
        frame=Frame((5.0, 5.0, 5.0), "x"),
        length=2.0,
        diameter=4.1234,
        span=((4.0, 5.0, 5.0), (6.0, 5.0, 5.0)),
    )
    boss = BossFeature(
        frame=Frame((20.0, 5.0, 5.0), "z"),
        diameter=6.2345,
        height=2.0,
        span=((20.0, 5.0, 4.0), (20.0, 5.0, 6.0)),
    )
    bore = HoleFeature(Frame((30.0, 5.0, 5.0), "z"), 3.3456, 4.0, False)
    member = HoleFeature(Frame((40.0, 5.0, 5.0), "z"), 2.4567, 3.0, False)
    pattern = PatternFeature(
        frame=member.frame,
        pattern="linear",
        count=2,
        member=member,
        members=((40.0, 5.0, 5.0), (50.0, 5.0, 5.0)),
        pitch=10.0,
        direction=(1.0, 0.0, 0.0),
    )
    rotational = RotationalFeature(Frame((60.0, 5.0, 5.0), "x"), 10.5678)
    owners = (
        (step, "step.diameter", step.diameter),
        (boss, "boss.diameter", boss.diameter),
        (bore, "bore.diameter", bore.diameter),
        (pattern, "bore.diameter", member.diameter),
        (rotational, "od.diameter", rotational.od),
    )
    model = PartModel(
        Box(80, 20, 20).bounding_box(),
        "x",
        [feature for feature, _parameter, _value in owners],
        decorations={
            (feature, "nominal_requirement", parameter): NominalRequirement(
                value,
                "ap242_pmi",
                (f"dimension:{feature.kind}",),
            )
            for feature, parameter, value in owners
        },
    )

    source = emit_sheet_script(model, "part", "all-owners", title="P", number="N")
    assert ".requirement(2.4567, on='bore.diameter'" in source
    namespace = {"part": Box(80, 20, 20)}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<all-owners>", "exec"),
        namespace,
    )
    rebuilt = namespace["sheet"].model()

    assert _requirement_signature(rebuilt) == _requirement_signature(model)
    rebuilt_values = {
        "step": next(f for f in rebuilt.features if isinstance(f, StepFeature)).diameter,
        "boss": next(f for f in rebuilt.features if isinstance(f, BossFeature)).diameter,
        "hole": next(f for f in rebuilt.features if isinstance(f, HoleFeature)).diameter,
        "pattern": next(
            f for f in rebuilt.features if isinstance(f, PatternFeature)
        ).member.diameter,
        "rotational": next(f for f in rebuilt.features if isinstance(f, RotationalFeature)).od,
    }
    assert rebuilt_values == {feature.kind: value for feature, _parameter, value in owners}
    assert plan_dimensions(rebuilt)


def test_exact_nominal_owner_disables_lossy_object_reference_substitution():
    from draftwright.sheet_emit import _object_references

    candidate = Cylinder(4.123 / 2, 20.0)
    boss = BossFeature(Frame((0.0, 0.0, 0.0), "z"), 4.1234, height=20.0)
    requirement = NominalRequirement(
        4.1234,
        "ap242_pmi",
        ("dimension:object-backed",),
    )
    model = PartModel(
        candidate.bounding_box(),
        None,
        [boss],
        decorations={(boss, "nominal_requirement", "boss.diameter"): requirement},
    )
    candidates = {"features.boss": candidate}
    assert _object_references([boss], candidate, candidates) == {id(boss): "features.boss"}

    source = emit_sheet_script(
        model,
        "part",
        "object-backed",
        title="P",
        number="N",
        source_part=candidate,
        object_candidates=candidates,
    )
    assert "sheet.diameter(diameter=4.1234" in source
    assert "sheet.diameter(features.boss" not in source
    namespace = {"part": candidate}
    exec(  # noqa: S102
        compile(
            source[: source.index("drawing = sheet.build()")],
            "<object-backed-owner>",
            "exec",
        ),
        namespace,
    )
    rebuilt = namespace["sheet"].model()
    rebuilt_boss = next(f for f in rebuilt.features if isinstance(f, BossFeature))
    assert rebuilt_boss.diameter == boss.diameter
    assert _requirement_signature(rebuilt) == _requirement_signature(model)
    assert plan_dimensions(rebuilt)


def test_requirement_api_rejects_parameters_the_emitter_cannot_preserve():
    declared = Sheet(Box(20, 20, 5)).auto_dimensions()
    slot = declared.slot(
        width=4,
        length=8,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        w_center=5,
        lo=2,
        hi=10,
        at=(6, 5, 2.5),
    )
    with pytest.raises(ValueError, match="canonical imported diameter owner"):
        slot.requirement(
            4,
            on="slot_width",
            source="ap242_pmi",
            source_ids=("dimension:unsupported",),
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"axis_origin": None}, "finite 3-vector"),
        ({"axis_origin": (0, 0)}, "finite 3-vector"),
        ({"axis_direction": (2, 0, 0)}, "unit length"),
        ({"axis_direction": (-1, 0, 0)}, "positive dominant"),
        ({"axis_origin": (1, 0, 0)}, "perpendicular"),
        ({"axial_interval": None}, "two finite values"),
        ({"axial_interval": (1, 0)}, "finite and increasing"),
        ({"radius": True}, "finite and positive"),
        ({"radius": 0}, "finite and positive"),
        ({"sense": "unknown"}, "sense must be"),
    ],
)
def test_cylindrical_reference_rejects_invalid_provenance(changes, message):
    values = {
        "axis_origin": (0, 0, 0),
        "axis_direction": (1, 0, 0),
        "radius": 2,
        "axial_interval": (0, 1),
        "sense": "external",
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        CylindricalReference(**values)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"axis_direction": (0, 0, 0)}, "non-zero"),
        ({"local_interval": None}, "two finite values"),
        ({"local_interval": (2, 1)}, "finite and increasing"),
    ],
)
def test_cylindrical_reference_canonical_rejects_invalid_kernel_values(changes, message):
    values = {
        "axis_point": (0, 0, 0),
        "axis_direction": (1, 0, 0),
        "radius": 2,
        "local_interval": (0, 1),
        "sense": "external",
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        CylindricalReference.canonical(**values)


@pytest.mark.parametrize(
    ("value", "source", "source_ids", "message"),
    [
        (True, "ap242_pmi", ("dimension:1",), "finite and positive"),
        (0, "ap242_pmi", ("dimension:1",), "finite and positive"),
        (4, " ", ("dimension:1",), "non-empty string"),
        (4, "ap242_pmi", ("", " "), "at least one source id"),
    ],
)
def test_nominal_requirement_rejects_invalid_provenance(value, source, source_ids, message):
    with pytest.raises(ValueError, match=message):
        NominalRequirement(value, source, source_ids)


def test_measured_dimension_validates_and_derives_typed_cylinder_inputs():
    reference = _cylinder(interval=(2, 6))
    required = {"label": "ø4", "ref_pts": ()}
    direct = measured_dimension(
        kind="diameter",
        value=4,
        dominant_axis="X",
        cylindrical_refs=(reference,),
        **required,
    )
    assert direct.cylindrical_refs == (reference,)
    assert direct.frame.origin == reference.midpoint

    mapping = {
        "axis_origin": (0, 0, 0),
        "axis_direction": (1, 0, 0),
        "radius": 2,
        "axial_interval": (2, 6),
        "sense": "external",
    }
    assert measured_dimension(
        kind="diameter",
        value=4,
        dominant_axis="X",
        cylindrical_refs=(mapping,),
        **required,
    ).cylindrical_refs == (reference,)

    with pytest.raises(ValueError, match="items must be mappings"):
        measured_dimension(
            kind="diameter",
            value=4,
            dominant_axis="X",
            cylindrical_refs=(object(),),
            **required,
        )
    with pytest.raises(ValueError, match="missing 'sense'"):
        measured_dimension(
            kind="diameter",
            value=4,
            dominant_axis="X",
            cylindrical_refs=({k: v for k, v in mapping.items() if k != "sense"},),
            **required,
        )

    oblique = CylindricalReference(
        axis_origin=(0, 0, 0),
        axis_direction=(2**-0.5, 2**-0.5, 0),
        radius=2,
        axial_interval=(0, 1),
        sense="external",
    )
    with pytest.raises(ValueError, match="one principal-axis direction"):
        measured_dimension(
            kind="diameter",
            value=4,
            dominant_axis="X",
            cylindrical_refs=(oblique,),
            **required,
        )
    blocked = measured_dimension(
        kind="diameter",
        value=4,
        dominant_axis="?",
        source_id="dimension:blocked",
        rendering_blockers=("unusable topology",),
        cylindrical_refs=(oblique,),
        **required,
    )
    assert blocked.cylindrical_refs == (oblique,)
    with pytest.raises(ValueError, match="dominant_axis disagrees"):
        measured_dimension(
            kind="diameter",
            value=4,
            dominant_axis="Z",
            cylindrical_refs=(reference,),
            **required,
        )


def test_owner_match_helpers_fail_closed_for_every_topology_component():
    bbox = Box(20, 20, 20).bounding_box()
    step = StepFeature(Frame((5, 0, 0), "x"), 10, 4, span=((0, 0, 0), (10, 0, 0)))
    assert not _external_owner_matches(_cylinder(interval=(0, 10), sense="internal"), step)
    assert not _external_owner_matches(_cylinder(interval=(0, 10), diameter=6), step)
    assert not _external_owner_matches(_cylinder(interval=(0, 10)), replace(step, span=None))

    through = HoleFeature(Frame((0, 0, 0), "x"), 4, None, True)
    assert not _internal_member_matches(
        _cylinder(interval=(0, 10), sense="external"), through, (0, 0, 0), bbox
    )
    assert not _internal_member_matches(
        _cylinder(interval=(0, 10), diameter=6, sense="internal"),
        through,
        (0, 0, 0),
        bbox,
    )
    assert not _internal_member_matches(
        _cylinder(interval=(0, 10), axis_origin=(0, 2, 0), sense="internal"),
        through,
        (0, 0, 0),
        bbox,
    )
    assert not _internal_member_matches(
        _cylinder(interval=(-11, 1), sense="internal"), through, (0, 0, 0), bbox
    )
    assert not _internal_member_matches(
        _cylinder(interval=(0, 0.005), sense="internal"), through, (0, 0, 0), bbox
    )
    blind_without_depth = HoleFeature(Frame((0, 0, 0), "x"), 4, None, False)
    assert not _internal_member_matches(
        _cylinder(interval=(0, 5), sense="internal"),
        blind_without_depth,
        (0, 0, 0),
        bbox,
    )
    blind = replace(blind_without_depth, depth=5)
    assert _internal_member_matches(
        _cylinder(interval=(0, 5), sense="internal"), blind, (0, 0, 0), bbox
    )
    assert (
        _standalone_cylinder_blocker((_cylinder(interval=(0, 1)), _cylinder(interval=(1, 2))))
        == ""
    )


def test_nominal_lowering_reports_skips_ambiguity_merges_and_conflicts():
    bbox = Box(20, 20, 20).bounding_box()
    step = StepFeature(Frame((5, 0, 0), "x"), 10, 4, span=((0, 0, 0), (10, 0, 0)))
    dimension = _dimension(_cylinder(interval=(0, 10)), "dimension:new")

    skipped = lower_ap242_nominal_diameters(
        PartModel(bbox, "x", [step, replace(dimension, lowering_blockers=("blocked",))])
    )
    assert skipped.features[-1].lowering_blockers == ("blocked",)

    ambiguous = lower_ap242_nominal_diameters(PartModel(bbox, "x", [step, step, dimension]))
    ambiguous_dimension = next(
        feature for feature in ambiguous.features if isinstance(feature, AuthoredDimension)
    )
    assert ambiguous_dimension.lowering_blockers == (
        "ambiguous diameter ownership: source cylinder topology matches 2 canonical features",
    )

    key = (step, "nominal_requirement", "step.diameter")
    merged = lower_ap242_nominal_diameters(
        PartModel(
            bbox,
            "x",
            [step, dimension],
            decorations={key: NominalRequirement(4, "ap242_pmi", ("dimension:old",))},
        )
    )
    assert merged.decorations[key].source_ids == ("dimension:old", "dimension:new")
    assert merged.features == [step]

    conflict = lower_ap242_nominal_diameters(
        PartModel(
            bbox,
            "x",
            [step, dimension],
            decorations={key: ToleranceDecoration(0.1, "ap242_pmi", ("dimension:old",))},
        )
    )
    conflict_dimension = next(
        feature for feature in conflict.features if isinstance(feature, AuthoredDimension)
    )
    assert "already has an authored aspect" in conflict_dimension.lowering_blockers[0]


def test_diameter_blockers_and_standalone_target_reject_inconsistent_groups():
    assert _diameter_reference_blockers((), 4, ()) == (
        "diameter dimension needs a measurable cylindrical-face reference",
    )
    mixed_axes = (_cylinder(), replace(_cylinder(), axis_direction=(0, 1, 0)))
    assert "diameter references do not share one cylinder axis direction" in (
        _diameter_reference_blockers(mixed_axes, 4, ())
    )
    mixed_senses = (_cylinder(), _cylinder(sense="internal"))
    assert "diameter references mix internal and external cylindrical faces" in (
        _diameter_reference_blockers(mixed_senses, 4, ())
    )

    rotational = RotationalFeature(Frame((0, 0, 0), "z"), 10, bores=(4, 6))
    with pytest.raises(ValueError, match="does not name exactly one parameter"):
        _requirement_parameter(rotational, "diameter")


def test_exact_grm03_five_diameters_each_own_one_canonical_annotation(tmp_path):
    assert _sha256(GRM03) == GRM03_SHA256
    drawing = build_drawing(GRM03, pmi="annotate", out=str(tmp_path / "grm03"))
    requirements = [
        (key, value)
        for key, value in drawing.model().decorations.items()
        if isinstance(value, NominalRequirement) and set(value.source_ids) <= GRM_DIAMETERS
    ]
    assert {source_id for _key, value in requirements for source_id in value.source_ids} == (
        GRM_DIAMETERS
    )
    assert {
        source_id: _names_for_parameter(drawing, key)
        for key, value in requirements
        for source_id in value.source_ids
    } == {
        "dimension:0:1:4:1": ["m_dia_x0"],
        "dimension:0:1:4:2": ["m_dia_x1"],
        "dimension:0:1:4:3": ["m_dia_x2"],
        "dimension:0:1:4:4": ["m_dia_x3"],
        "dimension:0:1:4:5": ["hc_side0"],
    }
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in ("pmi_not_rendered", "label_vs_measured")
        and set(issue.source_ids) & GRM_DIAMETERS
    ]

    model = drawing.model()
    source = emit_sheet_script(model, "part", "grm03", title="GRM-03", number="GRM-03")
    namespace = {"part": import_step(str(GRM03))}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<grm03-emit>", "exec"),
        namespace,
    )
    assert _requirement_signature(namespace["sheet"].model()) == _requirement_signature(model)
