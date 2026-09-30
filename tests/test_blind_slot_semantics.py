"""Consumer semantics for rectangular and round-bottom blind slots (#1421)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from fractions import Fraction

import pytest
from _section_recess_cases import corrupt_recess, declaration_fields
from build123d import (
    Align,
    Axis,
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
from quiddity import build_raw_recognition_result

from draftwright import Sheet, build_drawing
from draftwright.annotations import from_model
from draftwright.annotations._common import PlacementContext
from draftwright.builder import detect_part_model
from draftwright.model import (
    Frame,
    RectangularBlindSlotFeature,
    RoundBottomBlindSlotFeature,
    rectangular_blind_slot,
    round_bottom_blind_slot,
)
from draftwright.model.compiled import compile_dimensions
from draftwright.model.detect import build_part_model
from draftwright.sheet_emit import _feature_block, _feature_line


def _rectangular_part():
    stock = Box(30, 20, 40, align=(Align.CENTER, Align.CENTER, Align.MIN))
    tool = Pos(0, 5, 0) * Box(10, 5, 20, align=(Align.CENTER, Align.MIN, Align.MIN))
    return stock - tool


def _round_bottom_part():
    width, radius, length = 10.0, 3.0, 20.0
    half_width = width / 2
    half_flat = (width - 2 * radius) / 2
    with BuildLine() as boundary:
        Line((-half_width, 0), (half_width, 0))
        RadiusArc((half_width, 0), (half_flat, -radius), radius)
        Line((half_flat, -radius), (-half_flat, -radius))
        RadiusArc((-half_flat, -radius), (-half_width, 0), radius)
    with BuildSketch() as sketch:
        make_face(boundary.line)
    stock = Pos(0, -5, 0) * Box(30, 10, 40)
    tool = extrude(sketch.sketch, amount=length, dir=Vector(0, 0, 1))
    return stock - tool


@dataclass(frozen=True)
class _Case:
    kind: str
    part: Callable
    declare: Callable
    feature_type: type
    renderer: Callable
    label: str
    parameters: frozenset[str]
    sizes: tuple[str, str, str]
    open_sign: int
    page: str | None
    tolerance_parameter: str
    emitted_comment: str
    invalid_size: str
    invalid_other_size: str
    invalid_error: str

    def record(self):
        result = build_raw_recognition_result(self.part())
        assert result.slots == ()
        assert len(result.section_recesses) == 1
        return result.section_recesses[0]

    def fields(self, source):
        return declaration_fields(source, self.kind)

    def declared(self, source=None):
        return self.declare(**self.fields(self.record() if source is None else source))

    def feature(self, model):
        return next(feature for feature in model.features if feature.kind == self.kind)

    @property
    def dropped(self):
        return f"{self.kind}_dropped"

    @property
    def suppressed(self):
        return f"{self.kind}_requirement_suppressed"


_RECT = _Case(
    "rectangular_blind_slot",
    _rectangular_part,
    rectangular_blind_slot,
    RectangularBlindSlotFeature,
    from_model.render_rectangular_blind_slots,
    "OPEN SLOT 10 × 20 × 5 DEEP",
    frozenset(
        {
            "rectangular_blind_slot_width.length",
            "rectangular_blind_slot_length.length",
            "rectangular_blind_slot_depth.length",
        }
    ),
    ("width", "length", "depth"),
    -1,
    None,
    "rectangular_blind_slot_depth.length",
    "open slot",
    "width",
    "depth",
    "rectangular blind slot",
)
_ROUND = _Case(
    "round_bottom_blind_slot",
    _round_bottom_part,
    round_bottom_blind_slot,
    RoundBottomBlindSlotFeature,
    from_model.render_round_bottom_blind_slots,
    "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × R3 × 20 LONG",
    frozenset(
        {
            "round_bottom_blind_slot_length.length",
            "round_bottom_blind_slot_flat_width.length",
            "round_bottom_blind_slot_radius.radius",
        }
    ),
    ("length", "radius", "flat_width"),
    1,
    "A3",
    "round_bottom_blind_slot_radius.radius",
    "round-bottom open slot",
    "flat_width",
    "radius",
    "round-bottom blind slot",
)
_CASES = (pytest.param(_RECT, id="rectangular"), pytest.param(_ROUND, id="round-bottom"))


@pytest.mark.parametrize("case", _CASES)
def test_aggregate_record_lowers_without_slot_pocket_or_channel_coownership(case) -> None:
    source = case.record()
    model = detect_part_model(case.part())
    blind_slots = [feature for feature in model.features if feature.kind == case.kind]

    assert len(blind_slots) == 1
    feature = blind_slots[0]
    assert feature == case.declared(source)
    fields = case.fields(source)
    assert (feature.axis, feature.open_sign) == (fields["axis"], fields["open_sign"])
    assert (feature.width_axis, feature.depth_axis, feature.depth_sign) == (
        fields["width_axis"],
        fields["depth_axis"],
        fields["depth_sign"],
    )
    assert tuple(getattr(feature, name) for name in case.sizes) == tuple(
        fields[name] for name in case.sizes
    )
    assert not ({"slot", "pocket", "channel"} & {item.kind for item in model.features})


@pytest.mark.parametrize("case", _CASES)
def test_explicit_sheet_word_and_generated_line_round_trip_the_exact_ir(case) -> None:
    source = case.record()
    sheet = Sheet(case.part()).authored_dimensions()
    getattr(sheet, case.kind)(**case.fields(source))
    declared = sheet.model().features[0]
    assert declared == case.declared(source)

    line = _feature_line(declared)
    assert line.startswith(f"sheet.{case.kind}(")
    assert f"open_sign={case.open_sign}" in line
    assert "depth_sign=1" in line
    namespace = {"sheet": type("GeneratedSheet", (), {case.kind: staticmethod(case.declare)})()}
    assert eval(line.split("   #", 1)[0], {"__builtins__": {}}, namespace) == declared  # noqa: S307


@pytest.mark.parametrize("case", _CASES)
def test_drawing_places_one_solver_owned_open_slot_callout_with_all_sizes(case) -> None:
    drawing = build_drawing(case.part())
    feature = case.feature(drawing.model())
    annotations = drawing.annotations_of(feature)
    assert len(annotations) == 1
    name, annotation = next(iter(annotations.items()))
    assert annotation.label == case.label
    assert drawing.view_of(name) == "front"
    assert {key["parameter_id"] for key in drawing.measurement_keys(name)} == case.parameters
    assert not {
        issue.code
        for issue in drawing.lint()
        if issue.code in {case.dropped, "annotation_overlap"}
    }


_SUBSETS = (
    (_RECT, ("rectangular_blind_slot_width.length",), "OPEN SLOT 10 WIDE"),
    (_RECT, ("rectangular_blind_slot_length.length",), "OPEN SLOT 20 LONG"),
    (_RECT, ("rectangular_blind_slot_depth.length",), "OPEN SLOT 5 DEEP"),
    (
        _RECT,
        ("rectangular_blind_slot_width.length", "rectangular_blind_slot_length.length"),
        "OPEN SLOT 10 WIDE × 20 LONG",
    ),
    (
        _RECT,
        ("rectangular_blind_slot_width.length", "rectangular_blind_slot_depth.length"),
        "OPEN SLOT 10 WIDE × 5 DEEP",
    ),
    (
        _RECT,
        ("rectangular_blind_slot_length.length", "rectangular_blind_slot_depth.length"),
        "OPEN SLOT 20 LONG × 5 DEEP",
    ),
    (
        _RECT,
        (
            "rectangular_blind_slot_width.length",
            "rectangular_blind_slot_length.length",
            "rectangular_blind_slot_depth.length",
        ),
        "OPEN SLOT 10 × 20 × 5 DEEP",
    ),
    (
        _ROUND,
        ("round_bottom_blind_slot_flat_width.length",),
        "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT",
    ),
    (_ROUND, ("round_bottom_blind_slot_radius.radius",), "ROUND-BOTTOM OPEN SLOT R3"),
    (_ROUND, ("round_bottom_blind_slot_length.length",), "ROUND-BOTTOM OPEN SLOT 20 LONG"),
    (
        _ROUND,
        ("round_bottom_blind_slot_flat_width.length", "round_bottom_blind_slot_length.length"),
        "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × 20 LONG",
    ),
    (
        _ROUND,
        ("round_bottom_blind_slot_flat_width.length", "round_bottom_blind_slot_radius.radius"),
        "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × R3",
    ),
    (
        _ROUND,
        ("round_bottom_blind_slot_length.length", "round_bottom_blind_slot_radius.radius"),
        "ROUND-BOTTOM OPEN SLOT R3 × 20 LONG",
    ),
    (
        _ROUND,
        (
            "round_bottom_blind_slot_flat_width.length",
            "round_bottom_blind_slot_length.length",
            "round_bottom_blind_slot_radius.radius",
        ),
        "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × R3 × 20 LONG",
    ),
)


@pytest.mark.parametrize(("case", "parameters", "expected_label"), _SUBSETS)
def test_every_nonempty_authored_parameter_subset_survives_rendering(
    case, parameters, expected_label
) -> None:
    source = case.record()
    sheet = Sheet(case.part(), page=case.page).authored_dimensions()
    handle = getattr(sheet, case.kind)(**case.fields(source))
    for parameter in parameters:
        sheet.dimension(handle, parameter)

    drawing = sheet.build()
    feature = case.feature(drawing.model())
    annotations = drawing.annotations_of(feature)
    assert len(annotations) == 1
    name, annotation = next(iter(annotations.items()))
    assert annotation.label == expected_label
    assert {key["parameter_id"] for key in drawing.measurement_keys(name)} == set(parameters)
    issues = drawing.lint()
    assert not [issue for issue in issues if issue.code == "layout_repack_stalled"]
    assert len(issues) == 3 - len(parameters)
    assert {issue.code for issue in issues} <= {case.suppressed}


_ORIENTATIONS = (
    (_RECT, None, 0, "z", -1),
    (_RECT, Axis.X, 180, "z", 1),
    (_RECT, Axis.Y, 90, "x", -1),
    (_RECT, Axis.Y, -90, "x", 1),
    (_RECT, Axis.X, 90, "y", 1),
    (_RECT, Axis.X, -90, "y", -1),
    (_ROUND, None, 0, "z", 1),
    (_ROUND, Axis.X, 180, "z", -1),
    (_ROUND, Axis.Y, 90, "x", 1),
    (_ROUND, Axis.Y, -90, "x", -1),
    (_ROUND, Axis.X, 90, "y", -1),
    (_ROUND, Axis.X, -90, "y", 1),
)


@pytest.mark.parametrize(
    ("case", "rotation_axis", "angle", "expected_axis", "expected_open_sign"), _ORIENTATIONS
)
def test_automatic_leader_tip_targets_material_never_the_open_mouth(
    case, rotation_axis, angle, expected_axis, expected_open_sign
) -> None:
    part = case.part()
    drawing = build_drawing(part if rotation_axis is None else part.rotate(rotation_axis, angle))
    feature = case.feature(drawing.model())
    assert (feature.axis, feature.open_sign) == (expected_axis, expected_open_sign)
    name, annotation = next(iter(drawing.annotations_of(feature).items()))
    view = drawing.view_of(name)
    origin = list(feature.frame.origin)
    axis_index = "xyz".index(feature.axis)
    width_index = "xyz".index(feature.width_axis)

    mouth = origin.copy()
    mouth[axis_index] += feature.open_sign * feature.length / 2
    mouth_page = drawing.at(view, *mouth)[:2]

    cap = origin.copy()
    cap[axis_index] -= feature.open_sign * feature.length / 2
    material_targets = [cap]
    for side_sign in (-1, 1):
        side = origin.copy()
        side[width_index] += side_sign * feature.width / 2
        corner = cap.copy()
        corner[width_index] += side_sign * feature.width / 2
        material_targets.extend((side, corner))
        if case is _ROUND:
            floor_end = origin.copy()
            floor_end[width_index] += side_sign * feature.flat_width / 2
            material_targets.append(floor_end)
    material_page_targets = [drawing.at(view, *target)[:2] for target in material_targets]

    assert annotation.tip != pytest.approx(mouth_page)
    assert any(annotation.tip == pytest.approx(target) for target in material_page_targets)
    assert not [
        issue for issue in drawing.lint() if issue.code in {case.dropped, "annotation_overlap"}
    ]


@pytest.mark.parametrize("case", _CASES)
def test_live_and_deferred_callout_verbs_reuse_the_same_renderer(case) -> None:
    signatures = []
    for mode in ("live", "deferred"):
        drawing = build_drawing(case.part())
        feature = case.feature(drawing.model())
        drawing.drop(feature)
        if mode == "live":
            name = drawing.callout(feature)
        else:
            with drawing.deferred():
                assert drawing.callout(feature) == ""
            name = next(iter(drawing.annotations_of(feature)))

        annotations = drawing.annotations_of(feature)
        assert list(annotations) == [name]
        assert annotations[name].label == case.label
        assert drawing.view_of(name) == "front"
        parameter_ids = {key["parameter_id"] for key in drawing.measurement_keys(name)}
        assert parameter_ids == case.parameters
        signatures.append((annotations[name].label, drawing.view_of(name), parameter_ids))
        if mode == "deferred":
            assert drawing.lint() == []
        else:
            assert not [issue for issue in drawing.lint() if issue.code == case.dropped]
    assert signatures[0] == signatures[1]


@pytest.mark.parametrize("case", _CASES)
def test_renderer_fails_closed_for_an_excluded_feature_or_missing_view(case, monkeypatch) -> None:
    drawing = build_drawing(case.part())
    plan = compile_dimensions(drawing.model())

    def collected() -> PlacementContext:
        return PlacementContext(
            registry=drawing.registry,
            coverage=drawing.coverage,
            items=drawing.items,
            feature_leaders=[],
        )

    excluded = collected()
    case.renderer(drawing, plan, None, ctx=excluded, only=set())
    assert excluded.feature_leaders == []

    with monkeypatch.context() as patch:
        patch.setattr(from_model, "_END_ON", {})
        unmapped = collected()
        case.renderer(drawing, plan, None, ctx=unmapped)
        assert unmapped.feature_leaders == []

    with monkeypatch.context() as patch:
        patch.setattr(drawing, "view_bounds", lambda _view: None)
        absent = collected()
        case.renderer(drawing, plan, None, ctx=absent)
        assert absent.feature_leaders == []


@pytest.mark.parametrize("case", _CASES)
def test_generated_block_preserves_a_role_specific_tolerance_after_the_call(case) -> None:
    source = case.record()
    sheet = Sheet(case.part()).authored_dimensions()
    handle = getattr(sheet, case.kind)(**case.fields(source))
    handle.tolerance(0, 0.1, on=case.tolerance_parameter)
    original = sheet.model()
    lines, _names = _feature_block(original.features, decorations=original.decorations)
    declaration = next(line for line in lines if f"sheet.{case.kind}(" in line)
    assert f").tolerance(0, 0.1, on='{case.tolerance_parameter}')" in declaration
    assert declaration.index(".tolerance(") < declaration.index(f"   # {case.emitted_comment}")

    replay = Sheet(case.part()).authored_dimensions()
    exec(declaration, {"sheet": replay})  # noqa: S102
    rebuilt = replay.model()
    assert rebuilt.features == original.features
    assert rebuilt.decorations == original.decorations


@pytest.mark.parametrize("case", _CASES)
def test_raw_native_and_framed_rigid_motion_preserve_sizes_and_drawing_semantics(case) -> None:
    raw = build_drawing(case.part())
    moved = case.part().rotate(Axis.X, 23).rotate(Axis.Z, 31)
    framed = build_drawing(moved, framed_recognition=True)
    raw_feature = case.feature(raw.model())
    framed_feature = case.feature(framed.model())
    assert tuple(getattr(raw_feature, name) for name in case.sizes) == tuple(
        getattr(framed_feature, name) for name in case.sizes
    )
    raw_labels = {annotation.label for annotation in raw.annotations_of(raw_feature).values()}
    framed_labels = {
        annotation.label for annotation in framed.annotations_of(framed_feature).values()
    }
    assert raw_labels == framed_labels == {case.label}
    raw_name = next(iter(raw.annotations_of(raw_feature)))
    framed_name = next(iter(framed.annotations_of(framed_feature)))
    assert (
        {key["parameter_id"] for key in raw.measurement_keys(raw_name)}
        == {key["parameter_id"] for key in framed.measurement_keys(framed_name)}
        == case.parameters
    )


_COMMON_INVALID = (
    {"axis": "x", "width_axis": "x"},
    {"axis": ""},
    {"axis": "xy"},
    {"width_axis": "yz"},
    {"open_sign": 0},
    {"depth_sign": True},
    {"length": float("inf")},
    {"length": Fraction(10**10_000, 1)},
    {"frame": Frame(("0", 0, 0), "z")},
    {"frame": Frame([0, 7.5, 10], "z")},
    {"frame": Frame((Fraction(10**10_000, 1), 0, 0), "z")},
    {"frame": Frame((0, float("nan"), 0), "z")},
)
_INVALID = tuple(
    (case, change)
    for case in (_RECT, _ROUND)
    for change in (
        *_COMMON_INVALID[:6],
        {case.invalid_size: True},
        {case.invalid_size: "10"},
        {case.invalid_size: 0},
        *_COMMON_INVALID[6:8],
        {case.invalid_other_size: float("nan")},
        {case.invalid_other_size: "not-a-number"},
        *_COMMON_INVALID[8:],
    )
)


@pytest.mark.parametrize(("case", "change"), _INVALID)
def test_ir_rejects_malformed_axes_signs_and_sizes(case, change) -> None:
    valid = case.declared()
    with pytest.raises(ValueError, match=case.invalid_error):
        replace(valid, **change)


@pytest.mark.parametrize("case", _CASES)
def test_injected_public_record_uses_the_same_converter_and_validation(case) -> None:
    source = case.record()
    model = build_part_model(case.part(), section_recesses=(source,), section_recess_patterns=())
    assert case.declared(source) in model.features

    malformed = corrupt_recess(source, "run_interval", (0, 0))
    with pytest.raises(ValueError):
        build_part_model(case.part(), section_recesses=(malformed,), section_recess_patterns=())


_SCHEMA_COERCIONS = (
    ("boundary_coordinate", "10"),
    ("run_interval", (0, Fraction(10**10_000, 1))),
    ("origin", ("0", 7.5, 10)),
    ("origin", [0, 7.5, 10]),
    ("origin", (Fraction(10**10_000, 1), 7.5, 10)),
)


@pytest.mark.parametrize("case", _CASES)
@pytest.mark.parametrize(("field", "value"), _SCHEMA_COERCIONS)
def test_injected_public_record_rejects_schema_coercions(case, field, value) -> None:
    source = case.record()
    malformed = corrupt_recess(source, field, value)
    with pytest.raises((TypeError, ValueError)):
        build_part_model(case.part(), section_recesses=(malformed,), section_recess_patterns=())


@pytest.mark.parametrize("case", _CASES)
def test_hand_built_ir_requires_frame_and_run_axes_to_agree(case) -> None:
    fields = dict(
        frame=Frame((0, 0, 0), "x"),
        axis="y",
        open_sign=1,
        width_axis="x",
        depth_axis="z",
        depth_sign=-1,
        length=12,
    )
    if case is _RECT:
        fields.update(width=6, depth=3)
    else:
        fields.update(radius=3, flat_width=6)
    with pytest.raises(ValueError, match="frame axis must equal"):
        case.feature_type(**fields)


@pytest.mark.parametrize("case", _CASES)
def test_ir_has_no_implicit_datum_references(case) -> None:
    assert case.declared().references() == []
