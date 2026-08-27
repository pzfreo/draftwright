"""#1349 — precision is display policy on a referential dimension intent."""

from __future__ import annotations

import warnings

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot
from build123d_drafting.helpers import Draft

from draftwright import Sheet, build_drawing
from draftwright._geometry import _fmt
from draftwright.annotations.from_model import callout_from_spec
from draftwright.compose import _est_planned_bore_callout_width
from draftwright.model import PartModel, double_d_bore, hole, pattern
from draftwright.model.callout import hole_callout_spec
from draftwright.model.compiled import _value_text, compile_dimensions
from draftwright.model.declare import envelope as declare_envelope
from draftwright.model.ir import RequestedDimension
from draftwright.model.planner import plan_dimensions
from draftwright.sheet_emit import _requested_display_decimals, emit_sheet_script


def _width_plan(value: float, decimals: int | None):
    part = Box(value, 10, 5)
    sheet = Sheet(part, title="Precision", number="1349").authored_dimensions()
    envelope = sheet.envelope()
    intent = sheet.dimension(envelope, "width.length")
    if decimals is not None:
        intent.format(decimals=decimals)
    model = sheet.model()
    plan = compile_dimensions(model)
    group = next(group for group in plan.groups if group.feature_kind == "envelope")
    return part, sheet, model, group.dim(role="width")


@pytest.mark.parametrize(
    ("nominal", "decimals", "printed"),
    [
        (13.55, 2, "13.55"),
        (6.25, 2, "6.25"),
        (4.75, 2, "4.75"),
        (0.85, 2, "0.85"),
        (1.875, 3, "1.875"),
    ],
)
def test_feature_linked_nominals_preserve_requested_precision(nominal, decimals, printed):
    _part, _sheet, _model, approved = _width_plan(nominal, decimals)

    assert approved.value_text == printed
    assert approved.value == pytest.approx(nominal), "display policy must not replace the value"
    assert approved.id.parameter == "width.length", "the referential identity must survive"


def test_automatic_default_formatting_is_unchanged_without_an_explicit_policy():
    _part, _sheet, _model, approved = _width_plan(13.55, None)
    assert approved.value_text == "13.6"
    automatic = build_drawing(Box(13.55, 10, 5))
    assert automatic.get_annotation("m_env_width").label == "13.6"


def test_requested_precision_reaches_the_rendered_dimension_without_false_lint():
    _part, sheet, _model, _approved = _width_plan(13.55, 2)
    drawing = sheet.build()

    assert drawing.get_annotation("m_env_width").label == "13.55"
    assert not [issue for issue in drawing.lint() if issue.code == "label_vs_measured"]


def test_requested_precision_reaches_compound_hole_callouts_too():
    part = Box(20, 20, 5) - Cylinder(3.125, 10)
    sheet = Sheet(part, title="Precision", number="1349").authored_dimensions()
    hole = sheet.hole(diameter=6.25, at=(0, 0, 0), axis="z")
    sheet.dimension(hole, "bore.diameter").format(decimals=2)

    drawing = sheet.build()
    assert drawing.get_annotation("hc_plan0").label == "⌀6.25 THRU"
    assert not [issue for issue in drawing.lint() if issue.code == "label_vs_measured"]

    estimate = _est_planned_bore_callout_width(plan_dimensions(drawing.model()), drawing.draft)
    group = next(
        group for group in plan_dimensions(drawing.model()) if group.feature.kind == "hole"
    )
    rendered = callout_from_spec(hole_callout_spec(group), drawing.draft, None)
    assert rendered is not None
    assert estimate >= rendered.callout_width


def _compound_callout_model():
    sheet = Sheet(Box(40, 40, 10)).authored_dimensions()
    compound = (
        sheet.hole(diameter=6.25, at=(0, 0, 0), axis="z")
        .depth(4.75)
        .cbore(diameter=8.75, depth=1.875)
        .countersink(major=10.25, angle=82.5)
    )
    for role, decimals in (
        ("bore.diameter", 2),
        ("bore.depth", 2),
        ("counterbore.diameter", 2),
        ("counterbore.depth", 3),
        ("countersink.diameter", 2),
        ("countersink.angle", 2),
    ):
        sheet.dimension(compound, role).format(decimals=decimals)
    return sheet.model()


def _bolt_circle_callout_model():
    member = hole(diameter=6.25, at=(0, 0, 0), axis="z")
    feature = pattern(member, kind="bolt_circle", count=4, bcd=13.55)
    requests = tuple(
        RequestedDimension(feature, role, display_decimals=2)
        for role in ("bore.diameter", "bolt_circle.diameter")
    )
    return PartModel(
        bbox=Box(40, 40, 10).bounding_box(),
        orientation=None,
        features=[feature],
        authored_dimensions=requests,
    )


def _double_d_callout_model():
    feature = double_d_bore(
        major_diameter=10.25,
        across_flats=6.25,
        at=(0, 0, 0),
        axis="z",
    )
    requests = tuple(
        RequestedDimension(feature, role, display_decimals=2)
        for role in ("bore.diameter", "profile_across_flats.length")
    )
    return PartModel(
        bbox=Box(40, 40, 10).bounding_box(),
        orientation=None,
        features=[feature],
        authored_dimensions=requests,
    )


@pytest.mark.parametrize(
    ("model_factory", "expected"),
    [
        (
            _compound_callout_model,
            "⌀6.25 ↧ 4.75 ⌴ ⌀8.75 ↧ 1.875 ⌵ ⌀10.25 × 82.50°",
        ),
        (_bolt_circle_callout_model, "4× ⌀6.25 THRU EQ SP ON ø13.55 BC"),
        (_double_d_callout_model, "⌀10.25 THRU DOUBLE-D 6.25 A/F"),
    ],
    ids=["all compound terms", "bolt-circle suffix", "double-D across flats"],
)
def test_every_compound_and_suffix_term_uses_the_policy_and_fits_its_reservation(
    model_factory, expected
):
    model = model_factory()
    plan = plan_dimensions(model)
    spec = next(spec for group in plan if (spec := hole_callout_spec(group)) is not None)
    draft = Draft(font_size=3.0)
    callout = callout_from_spec(spec, draft, spec["count"])

    assert callout is not None
    assert callout.label == expected
    assert _est_planned_bore_callout_width(plan, draft) >= callout.callout_width


@pytest.mark.parametrize(
    ("axis", "part"),
    [
        ("x", Rot(0, 90, 0) * Cylinder(3.125, 6.25)),
        ("y", Rot(90, 0, 0) * Cylinder(3.125, 6.25)),
        ("z", Cylinder(3.125, 6.25)),
    ],
)
def test_precision_reaches_every_turned_diameter_and_step_chain(axis, part):
    centre = part.bounding_box().center()
    sheet = Sheet(part, title="Precision", number="1349").authored_dimensions()
    step = sheet.step(
        diameter=6.25,
        length=6.25,
        at=(centre.X, centre.Y, centre.Z),
        axis=axis,
    )
    sheet.dimension(step, "step.diameter").format(decimals=2)
    sheet.dimension(step, "step.length").format(decimals=2)

    labels = {
        name: item.label
        for name, item in sheet.build().iter_annotations()
        if name.startswith(("m_dia", "m_steplen"))
    }
    assert labels == {f"m_dia_{axis}0": "ø6.25", "m_steplen0": "6.25"}


def test_precision_reaches_a_prismatic_boss_diameter():
    part = Box(20, 20, 5) + Pos(0, 0, 5) * Cylinder(3.125, 5)
    sheet = Sheet(part, title="Precision", number="1349").authored_dimensions()
    boss = sheet.boss(diameter=6.25, height=5, at=(0, 0, 7.5), axis="z")
    sheet.dimension(boss, "boss.diameter").format(decimals=2)
    assert sheet.build().get_annotation("m_bossdia_z0").label == "ø6.25"


def test_live_turned_callout_uses_the_compiler_approved_text_after_drop():
    part = Rot(0, 90, 0) * Cylinder(3.125, 6.25)
    centre = part.bounding_box().center()
    sheet = Sheet(part, title="Precision", number="1349").authored_dimensions()
    step = sheet.step(diameter=6.25, length=6.25, at=tuple(centre), axis="x")
    sheet.dimension(step, "step.diameter").format(decimals=3)
    drawing = sheet.build()
    feature = next(feature for feature in drawing.model().features if feature.kind == "step")

    drawing.drop(feature)
    name = drawing.callout(feature)

    assert drawing.get_annotation(name).label == "ø6.250"


def test_repeated_step_chain_keeps_its_compact_form_with_explicit_precision():
    shaft = Cylinder(30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    for index, radius in enumerate((25, 20, 15), start=1):
        shaft += Pos(0, 0, 10 * index) * Cylinder(
            radius, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)
        )
    sheet = Sheet(shaft, title="Precision", number="1349").authored_dimensions()
    for index, diameter in enumerate((60, 50, 40, 30)):
        step = sheet.step(diameter=diameter, length=10, at=(0, 0, 5 + 10 * index), axis="z")
        sheet.dimension(step, "step.length").format(decimals=2)

    drawing = sheet.build()
    assert drawing.get_annotation("m_steplen_typ").label == "4× 10.00"


def test_near_uniform_automatic_chain_keeps_its_existing_default_collapse():
    shaft = Cylinder(30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    shaft += Pos(0, 0, 10) * Cylinder(25, 10.5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    shaft += Pos(0, 0, 20.5) * Cylinder(20, 9.5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    drawing = build_drawing(shaft)
    labels = {
        name: item.label
        for name, item in drawing.iter_annotations()
        if name.startswith("m_steplen")
    }
    assert labels == {"m_steplen_typ": "3× 10"}


def test_close_diameters_with_distinct_authoritative_text_do_not_deduplicate():
    shaft = Cylinder(3.1205, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))
    shaft += Pos(0, 0, 30) * Cylinder(3.122, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))
    part = Rot(0, 90, 0) * shaft
    sheet = Sheet(part, title="Precision", number="1349", page="A3").authored_dimensions()
    for position, diameter in ((15, 6.241), (45, 6.244)):
        step = sheet.step(diameter=diameter, length=30, at=(position, 0, 0), axis="x")
        sheet.dimension(step, "step.diameter").format(decimals=3)

    labels = {
        name: item.label
        for name, item in sheet.build().iter_annotations()
        if name.startswith("m_dia")
    }
    assert labels == {"m_dia_x0": "ø6.241", "m_dia_x1": "ø6.244"}


def test_special_ladders_consume_the_same_display_policy():
    part = Box(20, 20, 13.55)
    sheet = Sheet(part, title="Precision", number="1349").authored_dimensions()
    envelope = sheet.envelope()
    level = sheet.step_level(base=0, levels=(13.55,), shoulders=(), datum=(0, 0, 0))
    sheet.dimension(envelope, "height.length").format(decimals=2)
    sheet.dimension(level, "step_height.length").format(decimals=2)

    plan = compile_dimensions(sheet.model())
    labels = {
        ladder.kind: {rung.value_text for rung in ladder.rungs}
        for ladder in plan.ladders
        if ladder.kind in {"overall_height", "step_height"}
    }
    assert labels == {"overall_height": {"13.55"}, "step_height": {"13.55"}}


def test_display_policy_keeps_tolerance_and_numeric_provenance_separate():
    part = Box(13.55, 10, 5)
    sheet = Sheet(part, title="Precision", number="1349").authored_dimensions()
    envelope = sheet.envelope().tolerance(0.05, on="width")
    sheet.dimension(envelope, "width.length").format(decimals=2)

    plan = compile_dimensions(sheet.model())
    approved = next(group for group in plan.groups if group.feature_kind == "envelope").dim(
        role="width"
    )
    assert approved.value_text == "13.55"
    assert approved.value == pytest.approx(13.55)
    assert approved.tolerance == pytest.approx(0.05)


def test_generated_sheet_code_round_trips_the_display_policy():
    part, _sheet, model, _approved = _width_plan(1.875, 3)
    source = emit_sheet_script(
        model,
        "part",
        "precision",
        title="Precision",
        number="1349",
    )
    assert 'sheet.dimension(envelope1, "width.length").format(decimals=3)' in source

    namespace = {"part": part}
    body = source.replace("\npart\n", "\n", 1)
    body = body[: body.index("drawing = sheet.build()")]
    exec(compile(body, "<precision-round-trip>", "exec"), namespace)  # noqa: S102

    (request,) = namespace["sheet"].model().authored_dimensions
    assert request.display_decimals == 3
    regenerated = compile_dimensions(namespace["sheet"].model())
    approved = next(group for group in regenerated.groups if group.feature_kind == "envelope").dim(
        role="width"
    )
    assert approved.value_text == "1.875"


def test_generated_mirror_preserves_precision_on_an_augmenting_intent():
    part = Box(13.55, 10, 5)
    sheet = Sheet(part, title="Precision", number="1349")
    envelope = sheet.envelope()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sheet.auto_dimensions()
        sheet.add_dimension(envelope, "width.length").format(decimals=2)

    source = emit_sheet_script(
        sheet.model(), "part", "precision", title="Precision", number="1349"
    )
    assert 'sheet.dimension(envelope1, "width.length").format(decimals=2)' in source

    namespace = {"part": part}
    body = source.replace("\npart\n", "\n", 1)
    body = body[: body.index("drawing = sheet.build()")]
    exec(compile(body, "<augmenting-precision-round-trip>", "exec"), namespace)  # noqa: S102
    regenerated = namespace["sheet"].model()
    assert regenerated.authored_dimensions[0].display_decimals == 2
    assert compile_dimensions(regenerated).groups[0].dim(role="width").value_text == "13.55"


def test_generated_mirror_preserves_each_discriminated_grid_pitch_policy():
    part = Box(50, 50, 5)
    sheet = Sheet(part, title="Precision", number="1349")
    envelope = sheet.envelope()
    member = hole(diameter=4, at=(0, 0, 0), axis="z")
    grid = sheet.pattern(
        member,
        kind="grid",
        count=4,
        grid=(13.55, 20.125),
        rows=2,
        cols=2,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sheet.auto_dimensions()
        # A foreign-feature request first exercises the identity gate while the two
        # legitimate variants prove discriminator matching through the public surface.
        sheet.add_dimension(envelope, "width.length").format(decimals=1)
        sheet.add_dimension(grid, "grid_pitch", axis="row").format(decimals=2)
        sheet.add_dimension(grid, "grid_pitch", axis="col").format(decimals=3)

    source = emit_sheet_script(
        sheet.model(), "part", "precision-grid", title="Precision", number="1349"
    )
    assert '"grid_pitch.length.row").format(decimals=2)' in source
    assert '"grid_pitch.length.col").format(decimals=3)' in source

    namespace = {"part": part}
    body = source.replace("\npart\n", "\n", 1)
    body = body[: body.index("drawing = sheet.build()")]
    exec(compile(body, "<grid-precision-round-trip>", "exec"), namespace)  # noqa: S102
    requests = {
        request.role: request.display_decimals
        for request in namespace["sheet"].model().authored_dimensions
        if request.role.startswith("grid_pitch")
    }
    assert requests == {"grid_pitch.length.row": 2, "grid_pitch.length.col": 3}


def test_direct_part_model_input_preserves_precision_without_restatement():
    part = Box(13.55, 10, 5)
    envelope = declare_envelope(part)
    request = RequestedDimension(envelope, "width.length", display_decimals=2)
    model = PartModel(
        bbox=part.bounding_box(),
        orientation=None,
        features=[envelope],
        authored_dimensions=(request,),
    )
    approved = compile_dimensions(model).groups[0].dim(role="width")
    assert approved.value_text == "13.55"
    assert approved.value == pytest.approx(13.55)

    requested_model = PartModel(
        bbox=part.bounding_box(),
        orientation=None,
        features=[envelope],
        requested_dimensions=(request,),
    )
    requested = compile_dimensions(requested_model).groups[0].dim(role="width")
    assert requested.value_text == "13.55"


def test_policy_matching_uses_semantic_role_and_discriminator_without_leaking():
    part = Box(13.55, 10, 5)
    envelope = declare_envelope(part)
    other = declare_envelope(Box(20, 10, 5))
    mismatched = RequestedDimension(
        envelope,
        "width",
        discriminator="not-length",
        display_decimals=3,
    )
    matching = RequestedDimension(envelope, "width", display_decimals=2)
    model = PartModel(
        bbox=part.bounding_box(),
        orientation=None,
        features=[envelope],
        requested_dimensions=(
            RequestedDimension(other, "width", display_decimals=4),
            mismatched,
            matching,
        ),
    )

    assert _value_text(model, envelope, "width.length", 13.55) == "13.55"
    assert _requested_display_decimals(model, envelope, "width.length", None) == 2


def test_y_turned_repeated_chain_collapses_before_requesting_a_detail():
    shaft = Cylinder(30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    for index, radius in enumerate((25, 20, 15), start=1):
        shaft += Pos(0, 0, 10 * index) * Cylinder(
            radius, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)
        )
    part = Rot(90, 0, 0) * shaft
    sheet = Sheet(part, title="Precision", number="1349").authored_dimensions()
    for index, diameter in enumerate((60, 50, 40, 30)):
        step = sheet.step(diameter=diameter, length=10, at=(0, -5 - 10 * index, 0), axis="y")
        sheet.dimension(step, "step.length").format(decimals=2)

    drawing = sheet.build()
    labels = {
        name: item.label
        for name, item in drawing.iter_annotations()
        if name.startswith("m_steplen")
    }
    assert set(labels.values()) == {"4× 10.00"}
    assert len(labels) == 1
    assert "detail_a" not in drawing.views


def test_conflicting_precision_for_one_semantic_dimension_fails_closed():
    part = Box(13.55, 10, 5)
    envelope = declare_envelope(part)
    model = PartModel(
        bbox=part.bounding_box(),
        orientation=None,
        features=[envelope],
        authored_dimensions=(
            RequestedDimension(envelope, "width.length", display_decimals=2),
            RequestedDimension(envelope, "width.length", display_decimals=3),
        ),
    )
    with pytest.raises(ValueError, match="conflicting display precision"):
        compile_dimensions(model)


@pytest.mark.parametrize("bad", [-1, 16, 1.5, True])
def test_invalid_precision_is_refused_at_both_public_inputs(bad):
    part = Box(10, 10, 5)
    sheet = Sheet(part).authored_dimensions()
    envelope = sheet.envelope()
    with pytest.raises(ValueError, match="integer from 0 to 15"):
        sheet.dimension(envelope, "width.length").format(decimals=bad)

    feature = sheet.features[0]
    with pytest.raises(ValueError, match="integer from 0 to 15"):
        RequestedDimension(feature, "width.length", display_decimals=bad)


def test_precision_boundaries_and_negative_zero_are_stable():
    assert _fmt(1.25, 0) == "1"
    assert _fmt(1.0, 15) == "1.000000000000000"
    assert _fmt(-0.0001, 2) == "0.00"

    part = Box(10, 10, 5)
    envelope = declare_envelope(part)
    assert RequestedDimension(envelope, "width.length", display_decimals=0).display_decimals == 0
    assert RequestedDimension(envelope, "width.length", display_decimals=15).display_decimals == 15


def test_a_compound_location_intent_refuses_one_misleading_precision_policy():
    part = Box(20, 20, 5)
    sheet = Sheet(part).authored_dimensions()
    hole = sheet.hole(diameter=4, at=(0, 0, 0), axis="z")
    with pytest.raises(ValueError, match="multiple directional values"):
        sheet.dimension(hole, "location").format(decimals=2)

    with pytest.raises(ValueError, match="multiple directional values"):
        RequestedDimension(sheet.features[0], "location", display_decimals=2)
