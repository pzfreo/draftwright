"""Manufacturing PMI is lowered from stated facts and keeps its source meaning on replay."""

from build123d import Box
from build123d_drafting.helpers import Draft

from draftwright.annotations.from_model import callout_from_spec
from draftwright.model.callout import hole_callout_spec
from draftwright.model.ir import (
    CylindricalReference,
    Frame,
    HoleFeature,
    KnurlRequirement,
    PartModel,
    PmiFeature,
    StepFeature,
    ThreadRequirement,
)
from draftwright.model.planner import plan_dimensions
from draftwright.model.pmi_lowering import lower_ap242_manufacturing_requirements
from draftwright.sheet_emit import emit_sheet_script

TAP_WITHOUT_DRILL_POINT = (
    "M2 x 0.4-6H RH, 6 mm minimum full thread; DIA 1.6 tapping drill x 8 mm full-diameter depth"
)
UNCHAMFERED_KNURL = (
    "Straight knurl, 1 mm pitch, DIA 10 mm maximum after knurling; cut or formed process permitted"
)
THROUGH_TAP = "M2 x 0.4-6H RH, full thread through; DIA 1.6 tapping drill through"


def _reference(diameter, interval, sense):
    return CylindricalReference(
        axis_origin=(0.0, 0.0, 0.0),
        axis_direction=(1.0, 0.0, 0.0),
        radius=diameter / 2,
        axial_interval=interval,
        sense=sense,
    )


def _raw(kind, text, reference, entity):
    return PmiFeature(
        frame=Frame((0.0, 0.0, 0.0), "x"),
        pmi_kind=kind,
        value=0.0,
        label=text,
        dominant_axis="X",
        source_id=f"manufacturing_requirement:{entity}",
        part21_id=entity,
        source_category="manufacturing_requirement",
        reference_item_ids=(f"{entity}:face",),
        semantic_name=kind.replace("_", " "),
        shape_aspect_ids=(f"{entity}:aspect",),
        cylindrical_refs=(reference,),
    )


def _model(*features):
    return PartModel(Box(40, 20, 20).bounding_box(), "x", list(features))


def test_tap_without_drill_point_clause_omits_unstated_angle_issue_2135():
    assert "drill point" not in TAP_WITHOUT_DRILL_POINT
    hole = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 1.6, depth=8.0, through=False)
    raw = _raw(
        "internal_thread",
        TAP_WITHOUT_DRILL_POINT,
        _reference(1.6, (0.0, 8.0), "internal"),
        "#flat",
    )

    lowered = lower_ap242_manufacturing_requirements(_model(hole, raw))

    thread = lowered.features[0].thread
    assert isinstance(thread, ThreadRequirement)
    assert thread.drill_point_angle is None
    assert "DRILL POINT" not in thread.callout_suffix
    assert not any(isinstance(feature, PmiFeature) for feature in lowered.features)


def test_through_tap_matches_only_through_hole_issue_2135():
    assert "through" in THROUGH_TAP and "depth" not in THROUGH_TAP
    reference = _reference(1.6, (-20.0, 20.0), "internal")
    raw = _raw("internal_thread", THROUGH_TAP, reference, "#through")
    through = HoleFeature(Frame((-20.0, 0.0, 0.0), "x"), 1.6, depth=None, through=True)
    blind = HoleFeature(Frame((-20.0, 0.0, 0.0), "x"), 1.6, depth=40.0, through=False)

    lowered = lower_ap242_manufacturing_requirements(_model(through, raw))
    thread = lowered.features[0].thread
    assert isinstance(thread, ThreadRequirement)
    assert thread.through and thread.drill_depth is None
    assert "THRU" in thread.callout_suffix
    assert not any(isinstance(feature, PmiFeature) for feature in lowered.features)

    spec = hole_callout_spec(plan_dimensions(lowered)[0])
    callout = callout_from_spec(spec, Draft(), 1)
    assert "M2 x 0.4-6H RH; THRU" in callout.label
    assert callout.label.count("THRU") == 1
    assert callout.covers_hole_requirements == ("bore.through",)
    assert callout.geometry_qualifiers == ("bore.through",)

    refused = lower_ap242_manufacturing_requirements(_model(blind, raw))
    assert refused.features[0].thread is None
    fallback = next(feature for feature in refused.features if isinstance(feature, PmiFeature))
    assert "no canonical feature matches" in fallback.lowering_blockers[0]


def test_unchamfered_knurl_omits_unstated_edge_chamfer_issue_2135():
    assert "chamfer" not in UNCHAMFERED_KNURL
    head = StepFeature(
        frame=Frame((1.0, 0.0, 0.0), "x"),
        length=2.0,
        diameter=10.0,
        span=((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
    )
    raw = _raw(
        "knurl",
        UNCHAMFERED_KNURL,
        _reference(10.0, (0.2, 1.8), "external"),
        "#no_chamfer",
    )

    lowered = lower_ap242_manufacturing_requirements(_model(head, raw))

    knurl = lowered.features[0].knurl
    assert isinstance(knurl, KnurlRequirement)
    assert knurl.edge_chamfer is None
    assert not knurl.full_width
    assert "CHAMFER" not in knurl.callout_suffix
    assert "FULL WIDTH" not in knurl.callout_suffix
    assert not any(isinstance(feature, PmiFeature) for feature in lowered.features)


def test_generated_sheet_preserves_through_tap_requirement_issue_2135():
    part = Box(40, 20, 20)
    hole = HoleFeature(Frame((-20.0, 0.0, 0.0), "x"), 1.6, depth=None, through=True)
    raw = _raw(
        "internal_thread",
        THROUGH_TAP,
        _reference(1.6, (-20.0, 20.0), "internal"),
        "#through",
    )
    lowered = lower_ap242_manufacturing_requirements(
        PartModel(part.bounding_box(), "x", [hole, raw])
    )
    original = lowered.features[0].thread
    assert isinstance(original, ThreadRequirement) and original.through

    source = emit_sheet_script(lowered, "part", "through-tap", title="P", number="N")
    assert "through=True" in source
    namespace = {"part": part}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<through-tap-emit>", "exec"),
        namespace,
    )
    restored = namespace["sheet"].model()
    restored_hole = next(feature for feature in restored.features if feature.kind == "hole")
    assert restored_hole.thread == original
    assert restored_hole.thread.through
