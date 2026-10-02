"""Manufacturing PMI is lowered from stated facts and keeps its source meaning on replay."""

from dataclasses import replace

import pytest
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
from draftwright.model.pmi_lowering import (
    lower_ap242_document_requirements,
    lower_ap242_manufacturing_requirements,
)
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


def test_structured_through_tap_lowers_without_a_prose_sentence_issue_2137():
    hole = HoleFeature(Frame((-20.0, 0.0, 0.0), "x"), 1.6, depth=None, through=True)
    raw = replace(
        _raw(
            "internal_thread",
            "internal thread",
            _reference(1.6, (-20.0, 20.0), "internal"),
            "#structured",
        ),
        structured_fields=(
            ("thread side", "internal"),
            ("designation", "M2x0.4"),
            ("nominal size", "M2"),
            ("pitch", 0.4),
            ("fit class", "6H"),
            ("hand", "right"),
            ("through", "true"),
            ("tapping drill diameter", 1.6),
        ),
    )
    assert raw.label == "internal thread" and raw.structured_fields

    lowered = lower_ap242_manufacturing_requirements(_model(hole, raw))

    thread = lowered.features[0].thread
    assert isinstance(thread, ThreadRequirement)
    assert thread.through and thread.drill_depth is None
    assert thread.callout_suffix == "M2 x 0.4-6H RH; THRU"
    assert thread.source_ids == ("manufacturing_requirement:#structured",)
    assert not any(isinstance(feature, PmiFeature) for feature in lowered.features)


@pytest.mark.parametrize("reverse", (False, True))
def test_conflicting_structured_threads_leave_every_source_unlowered_issue_2137(reverse):
    hole = HoleFeature(Frame((-20.0, 0.0, 0.0), "x"), 1.6, depth=None, through=True)
    sources = tuple(
        replace(
            _raw(
                "internal_thread",
                "internal thread",
                _reference(1.6, (-20.0, 20.0), "internal"),
                f"#pitch_{pitch}",
            ),
            structured_fields=(
                ("thread side", "internal"),
                ("designation", f"M2x{pitch}"),
                ("nominal size", "M2"),
                ("pitch", pitch),
                ("fit class", "6H"),
                ("hand", "right"),
                ("through", "true"),
                ("tapping drill diameter", 1.6),
            ),
        )
        for pitch in (0.4, 0.5)
    )
    assert {dict(source.structured_fields)["pitch"] for source in sources} == {0.4, 0.5}
    assert all(source.cylindrical_refs == sources[0].cylindrical_refs for source in sources)

    lowered = lower_ap242_manufacturing_requirements(
        _model(hole, *(reversed(sources) if reverse else sources))
    )

    assert lowered.features[0].thread is None
    fallbacks = [feature for feature in lowered.features if isinstance(feature, PmiFeature)]
    assert {feature.source_id for feature in fallbacks} == {
        "manufacturing_requirement:#pitch_0.4",
        "manufacturing_requirement:#pitch_0.5",
    }
    assert all(
        feature.lowering_blockers
        == (
            "ambiguous thread ownership: multiple manufacturing requirements claim canonical feature",
        )
        for feature in fallbacks
    )


def test_thread_and_knurl_aspects_on_one_owner_lower_independently_issue_2137():
    head = StepFeature(
        frame=Frame((1.0, 0.0, 0.0), "x"),
        length=2.0,
        diameter=10.0,
        span=((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
    )
    thread = replace(
        _raw(
            "external_thread",
            "external thread",
            _reference(10.0, (0.0, 2.0), "external"),
            "#thread",
        ),
        structured_fields=(
            ("thread side", "external"),
            ("designation", "M10x1.5"),
            ("nominal size", "M10"),
            ("pitch", 1.5),
            ("fit class", "6g"),
            ("hand", "right"),
            ("thread length", 2.0),
        ),
    )
    knurl = replace(
        _raw("knurl", "knurl", _reference(10.0, (0.2, 1.8), "external"), "#knurl"),
        structured_fields=(
            ("pattern", "straight"),
            ("pitch", 1.0),
            ("maximum diameter", 10.0),
        ),
    )
    remaps = []

    lowered = lower_ap242_manufacturing_requirements(
        _model(head, thread, knurl), feature_remap=lambda *args: remaps.append(args)
    )

    assert len(lowered.features) == 1
    assert lowered.features[0].thread is not None
    assert lowered.features[0].knurl is not None
    assert remaps == [(head, (lowered.features[0],), None)]


def test_source_shaped_paired_manufacturing_fields_lower_with_explicit_prose_issue_2137():
    hole = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 4.2, depth=8.0, through=False)
    head = StepFeature(
        frame=Frame((1.0, 0.0, 0.0), "x"),
        length=2.0,
        diameter=10.0,
        span=((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
    )
    end = StepFeature(
        frame=Frame((5.0, 0.0, 0.0), "x"),
        length=2.0,
        diameter=6.0,
        span=((4.0, 0.0, 0.0), (6.0, 0.0, 0.0)),
    )
    internal = replace(
        _raw(
            "internal_thread",
            "M5 x 0.8-6H RH, 6 mm minimum full thread; DIA 4.2 tapping drill x 8 mm full-diameter depth; conventional 118 degree drill point",
            _reference(4.2, (0.0, 8.0), "internal"),
            "#internal",
        ),
        source_ids=(
            "manufacturing_requirement:#internal",
            "manufacturing_requirement:#internal_uda",
        ),
        structured_fields=(
            ("thread side", "internal"),
            ("designation", "M5x0.8"),
            ("fit class", "6H"),
            ("hand", "right"),
            ("full thread length", 6.0),
            ("drill diameter", 4.2),
            ("drill depth", 8.0),
        ),
    )
    external = replace(
        _raw(
            "external_thread",
            "M6 x 1-6g RH, full available length on nominal DIA 6 region",
            _reference(6.0, (4.0, 6.0), "external"),
            "#external",
        ),
        source_ids=(
            "manufacturing_requirement:#external",
            "manufacturing_requirement:#external_uda",
        ),
        structured_fields=(
            ("thread side", "external"),
            ("designation", "M6x1"),
            ("fit class", "6g"),
            ("hand", "right"),
        ),
    )
    knurl = replace(
        _raw(
            "knurl",
            "Straight knurl, 1 mm pitch, full width between C0.3 chamfers, DIA 10 mm maximum after knurling; cut or formed process permitted",
            _reference(10.0, (0.2, 1.8), "external"),
            "#knurl",
        ),
        source_ids=("manufacturing_requirement:#knurl", "manufacturing_requirement:#knurl_uda"),
        structured_fields=(("pattern", "straight"), ("pitch", 1.0), ("maximum diameter", 10.0)),
    )
    assert "thread length" not in dict(external.structured_fields)
    assert "minimum full thread" not in dict(internal.structured_fields)

    lowered = lower_ap242_manufacturing_requirements(
        _model(hole, head, end, internal, external, knurl)
    )

    assert len(lowered.features) == 3
    assert lowered.features[0].thread.minimum_full_thread == 6.0
    assert lowered.features[0].thread.drill_point_angle == 118.0
    assert lowered.features[1].knurl.edge_chamfer == 0.3
    assert lowered.features[2].thread.full_available_length
    assert lowered.features[2].thread.pitch == 1.0

    conflicting_length = replace(
        internal,
        structured_fields=tuple(
            (name, 7.0 if name == "full thread length" else value)
            for name, value in internal.structured_fields
        ),
    )
    refused = lower_ap242_manufacturing_requirements(_model(hole, conflicting_length))
    assert refused.features[0].thread is None
    assert refused.features[1].lowering_blockers == (
        "structured thread values disagree with prose",
    )


def test_structured_thread_cannot_infer_unstated_length_meaning_issue_2137():
    blind = replace(
        _raw(
            "internal_thread", "internal thread", _reference(4.2, (0.0, 8.0), "internal"), "#blind"
        ),
        structured_fields=(
            ("thread side", "internal"),
            ("designation", "M5x0.8"),
            ("fit class", "6H"),
            ("hand", "right"),
            ("full thread length", 6.0),
            ("drill diameter", 4.2),
            ("drill depth", 8.0),
        ),
    )
    external = replace(
        _raw(
            "external_thread",
            "external thread",
            _reference(6.0, (4.0, 6.0), "external"),
            "#external",
        ),
        structured_fields=(
            ("thread side", "external"),
            ("designation", "M6x1"),
            ("fit class", "6g"),
            ("hand", "right"),
        ),
    )
    assert all(source.structured_fields for source in (blind, external))

    lowered = lower_ap242_manufacturing_requirements(_model(blind, external))

    assert [feature.lowering_blockers for feature in lowered.features] == [
        ("structured full thread length needs a matching minimum-full-thread prose requirement",),
        ("structured external thread has no length requirement",),
    ]


@pytest.mark.parametrize("kind", ("internal_thread", "knurl", "general_tolerances"))
@pytest.mark.parametrize("extra", ("unknown", "duplicate"))
def test_structured_manufacturing_field_schema_rejects_unclaimed_meaning_issue_2137(kind, extra):
    if kind == "internal_thread":
        owner = HoleFeature(Frame((-20.0, 0.0, 0.0), "x"), 1.6, depth=None, through=True)
        raw = replace(
            _raw(kind, "internal thread", _reference(1.6, (-20.0, 20.0), "internal"), "#thread"),
            structured_fields=(
                ("thread side", "internal"),
                ("designation", "M2x0.4"),
                ("nominal size", "M2"),
                ("pitch", 0.4),
                ("fit class", "6H"),
                ("hand", "right"),
                ("through", "true"),
                ("tapping drill diameter", 1.6),
            ),
        )
    elif kind == "knurl":
        owner = StepFeature(
            frame=Frame((1.0, 0.0, 0.0), "x"),
            length=2.0,
            diameter=10.0,
            span=((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
        )
        raw = replace(
            _raw(kind, "knurl", _reference(10.0, (0.2, 1.8), "external"), "#knurl"),
            structured_fields=(
                ("pattern", "straight"),
                ("pitch", 1.0),
                ("maximum diameter", 10.0),
            ),
        )
    else:
        owner = None
        raw = PmiFeature(
            frame=Frame((0.0, 0.0, 0.0), "z"),
            pmi_kind=kind,
            value=0.0,
            label="general tolerances",
            dominant_axis="?",
            source_id="manufacturing_requirement:#default",
            source_category="manufacturing_requirement",
            structured_fields=(("tolerance class", "ISO 2768-m"),),
        )
    lower = (
        lower_ap242_document_requirements
        if kind == "general_tolerances"
        else lower_ap242_manufacturing_requirements
    )
    model = _model(*(feature for feature in (owner, raw) if feature is not None))
    assert raw.structured_fields and raw.pmi_kind == kind
    baseline = lower(model)
    assert not any(isinstance(feature, PmiFeature) for feature in baseline.features)

    extra_field = ("number of starts", 2.0) if extra == "unknown" else raw.structured_fields[0]
    changed = replace(raw, structured_fields=(*raw.structured_fields, extra_field))
    assert len(changed.structured_fields) == len(raw.structured_fields) + 1
    rejected = lower(_model(*(feature for feature in (owner, changed) if feature is not None)))
    fallback = next(feature for feature in rejected.features if isinstance(feature, PmiFeature))
    assert (
        "unsupported structured manufacturing fields" if extra == "unknown" else "duplicate"
    ) in (fallback.lowering_blockers[0])


def test_structured_and_prose_thread_disagreement_is_explicit_issue_2137():
    hole = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 1.6, depth=8.0, through=False)
    raw = replace(
        _raw(
            "internal_thread",
            TAP_WITHOUT_DRILL_POINT,
            _reference(1.6, (0.0, 8.0), "internal"),
            "#prose",
        ),
        source_ids=("manufacturing_requirement:#prose", "manufacturing_requirement:#uda"),
        structured_fields=(
            ("thread side", "internal"),
            ("designation", "M2x0.5"),
            ("nominal size", "M2"),
            ("pitch", 0.5),
            ("fit class", "6H"),
            ("hand", "right"),
            ("tapping drill diameter", 1.6),
            ("tapping drill depth", 8.0),
            ("minimum full thread", 6.0),
        ),
    )
    assert "0.4" in raw.label and dict(raw.structured_fields)["pitch"] == 0.5

    refused = lower_ap242_manufacturing_requirements(_model(hole, raw))

    assert refused.features[0].thread is None
    fallback = next(feature for feature in refused.features if isinstance(feature, PmiFeature))
    assert fallback.lowering_blockers == ("structured thread values disagree with prose",)

    agreeing = replace(
        raw,
        structured_fields=tuple(
            (
                name,
                0.4 if name == "pitch" else "M2x0.4" if name == "designation" else value,
            )
            for name, value in raw.structured_fields
        ),
    )
    lowered = lower_ap242_manufacturing_requirements(_model(hole, agreeing))
    thread = lowered.features[0].thread
    assert isinstance(thread, ThreadRequirement)
    assert thread.source_ids == raw.source_ids

    unparseable_prose = replace(agreeing, label="M2 x 0.4-6H RH; unsupported extra thread claim")
    assert unparseable_prose.source_ids == raw.source_ids
    refused = lower_ap242_manufacturing_requirements(_model(hole, unparseable_prose))
    assert refused.features[0].thread is None
    fallback = next(feature for feature in refused.features if isinstance(feature, PmiFeature))
    assert fallback.lowering_blockers == ("structured thread cannot reconcile with prose",)


def test_structured_knurl_and_default_tolerance_need_no_prose_issue_2137():
    head = StepFeature(
        frame=Frame((1.0, 0.0, 0.0), "x"),
        length=2.0,
        diameter=10.0,
        span=((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
    )
    raw_knurl = replace(
        _raw("knurl", "knurl", _reference(10.0, (0.2, 1.8), "external"), "#knurl"),
        structured_fields=(
            ("pattern", "straight"),
            ("pitch", 1.0),
            ("maximum diameter", 10.0),
        ),
    )
    raw_default = PmiFeature(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        pmi_kind="general_tolerances",
        value=0.0,
        label="general tolerances",
        dominant_axis="?",
        source_id="manufacturing_requirement:#default",
        part21_id="#default",
        source_category="manufacturing_requirement",
        structured_fields=(("tolerance class", "ISO 2768-m"),),
    )
    assert raw_knurl.label == "knurl" and raw_default.label == "general tolerances"

    lowered = lower_ap242_manufacturing_requirements(_model(head, raw_knurl, raw_default))
    lowered = lower_ap242_document_requirements(lowered)

    knurl = lowered.features[0].knurl
    assert isinstance(knurl, KnurlRequirement)
    assert (knurl.pattern, knurl.pitch, knurl.maximum_diameter) == ("straight", 1.0, 10.0)
    assert not knurl.full_width and knurl.edge_chamfer is None
    default = next(feature for feature in lowered.features if feature.kind == "general_tolerance")
    assert default.designation == "ISO 2768-m"
    assert default.source_id == "manufacturing_requirement:#default"

    disagreement = replace(raw_default, label="ISO 2768-f")
    refused = lower_ap242_document_requirements(_model(disagreement))
    assert refused.features[0].kind == "pmi"
    assert refused.features[0].lowering_blockers == (
        "structured general-tolerance class disagrees with prose",
    )

    paired = replace(
        raw_default,
        label="ISO 2768-m",
        source_id="manufacturing_requirement:#prose",
        source_ids=("manufacturing_requirement:#prose", "manufacturing_requirement:#uda"),
    )
    paired_model = lower_ap242_document_requirements(_model(paired))
    paired_default = paired_model.features[0]
    assert paired_default.source_ids == paired.source_ids
    part = Box(40, 20, 20)
    source = emit_sheet_script(paired_model, "part", "default", title="P", number="N")
    namespace = {"part": part}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<default-emit>", "exec"),
        namespace,
    )
    restored_default = namespace["sheet"].model().features[0]
    assert restored_default.designation == "ISO 2768-m"
    assert restored_default.source_ids == paired.source_ids


@pytest.mark.parametrize(
    "fields",
    (
        (("pattern", "straight"), ("diametral pitch", 1.0), ("maximum diameter", 10.0)),
        (("pattern", "straight"), ("pitch", 1.0), ("major diameter", 10.0)),
        (("pattern", "straight"), ("diametral pitch", 1.0), ("major diameter", 10.0)),
    ),
)
def test_structured_knurl_aliases_need_prose_for_their_drawing_meaning_issue_2137(fields):
    head = StepFeature(
        frame=Frame((1.0, 0.0, 0.0), "x"),
        length=2.0,
        diameter=10.0,
        span=((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
    )
    raw = replace(
        _raw("knurl", "knurl", _reference(10.0, (0.2, 1.8), "external"), "#uda"),
        structured_fields=fields,
    )
    assert raw.label == "knurl" and raw.structured_fields == fields
    assert {name for name, _value in fields} & {"diametral pitch", "major diameter"}

    refused = lower_ap242_manufacturing_requirements(_model(head, raw))

    assert refused.features[0].knurl is None
    fallback = refused.features[1]
    assert isinstance(fallback, PmiFeature)
    assert fallback.source_id == raw.source_id
    assert fallback.structured_fields == fields
    assert fallback.lowering_blockers == (
        "structured knurl aliases need a matching prose requirement",
    )

    paired = replace(
        raw,
        label=UNCHAMFERED_KNURL,
        source_id="manufacturing_requirement:#prose",
        part21_id="#prose",
        source_ids=("manufacturing_requirement:#prose", raw.source_id),
    )
    assert "1 mm pitch" in paired.label and "maximum after knurling" in paired.label
    lowered = lower_ap242_manufacturing_requirements(_model(head, paired))

    assert len(lowered.features) == 1
    knurl = lowered.features[0].knurl
    assert isinstance(knurl, KnurlRequirement)
    assert (knurl.pitch, knurl.maximum_diameter) == (1.0, 10.0)
    assert knurl.source_ids == paired.source_ids
