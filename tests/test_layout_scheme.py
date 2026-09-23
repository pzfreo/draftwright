from build123d import Box

from draftwright.compose import _measure_strips
from draftwright.layout_scheme import plan_annotation_scheme
from draftwright.model import Frame, PartModel
from draftwright.model.ir import ControlFrame, DatumRef, Note, PmiFeature


def _model():
    solid = Box(100, 60, 20)
    frame = Frame((10, 20, 0), "z")
    features = [
        ControlFrame(frame, "position", "0.1", "front", "above", source_id="gdt:1"),
        DatumRef(frame, "A", "front", "above", source_id="datum:1"),
        Note(frame, "Ra 3.2", "side", "below", source_id="finish:1"),
        PmiFeature(frame, "location", 10.0, "?", "X", source_id="dimension:raw"),
    ]
    return PartModel(solid.bounding_box(), "z", features)


def test_scheme_groups_explicit_semantics_by_view_corridor_without_coordinates():
    scheme = plan_annotation_scheme(_model())

    assert scheme.corridor_counts() == {("front", "above"): 2, ("side", "below"): 1}
    assert [demand.identity for demand in scheme.corridor("front", "above")] == [
        "gdt:1",
        "datum:1",
    ]
    assert scheme.demands[0].model_site == (10.0, 20.0, 0.0)
    assert [(item.identity, item.reason) for item in scheme.unplanned] == [
        ("dimension:raw", "raw PMI has no typed corridor")
    ]


def test_strip_measurement_carries_the_scheme_without_changing_depths():
    model = _model()
    strips = _measure_strips(model, 0, model.bbox)

    assert strips.scheme == plan_annotation_scheme(model)
    assert strips.right == 20.0
    assert strips.left == 20.0
