"""A declaration edit must preserve owners and meaning, not merely counts or labels."""

from dataclasses import replace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet, build_drawing
from draftwright.audit import compare_measurements, diff_builds, explain
from draftwright.model.ir import RequestedDimension


@pytest.fixture(scope="module")
def declared_pair():
    part = Box(60, 50, 10)
    points = ((-15, -10, 0), (15, 10, 0))
    sheet = Sheet(part, scale=2).authored_dimensions()
    for point in points:
        part -= Pos(*point) * Cylinder(2, 10)
        sheet.hole(diameter=4, at=point, axis="z", depth=10)
    model = sheet.model()
    holes = tuple(item for item in model.features if item.kind == "hole")
    assert len(holes) == 2 and holes[0] is not holes[1]
    model = replace(model, authored_dimensions=(RequestedDimension(holes[0], "bore.diameter"),))
    return part, model, holes


def _build(part, model):
    return build_drawing(part, model=model, scale=2)


def test_same_kind_same_label_owner_substitution_is_a_loss_and_gain(declared_pair):
    part, model, holes = declared_pair
    before = _build(part, model)
    after = _build(
        part, replace(model, authored_dimensions=(RequestedDimension(holes[1], "bore.diameter"),))
    )
    difference = diff_builds(before, after)
    assert difference["dimensions_lost"] == difference["dimensions_changed"] == {}
    assert difference["measurements_substituted"]
    result = compare_measurements(before, after)
    assert result["status"] == "changed"
    assert [row["parameter_id"] for row in result["lost"]] == ["bore.diameter"]
    assert [row["parameter_id"] for row in result["gained"]] == ["bore.diameter"]
    assert result["lost"][0]["owner"] != result["gained"][0]["owner"]


def test_rebuilt_equal_features_are_unknown_without_an_explicit_pair(declared_pair):
    part, model, holes = declared_pair
    replacement = replace(holes[0])
    assert replacement == holes[0] and replacement is not holes[0]
    after_model = replace(
        model,
        features=[replacement, holes[1]],
        authored_dimensions=(RequestedDimension(replacement, "bore.diameter"),),
    )
    before, after = _build(part, model), _build(part, after_model)
    assert compare_measurements(before, after)["status"] == "unknown"
    result = compare_measurements(before, after, feature_pairs=((holes[0], replacement),))
    assert result["status"] == "preserved", result


def test_a_layout_edit_preserves_compiled_measurements(declared_pair):
    part, model, _holes = declared_pair
    before = _build(part, model)
    after = _build(
        part,
        replace(
            model,
            authored_dimensions=tuple(replace(r, side="left") for r in model.authored_dimensions),
        ),
    )
    result = compare_measurements(before, after)
    assert result["status"] == "preserved", result


def test_a_datum_change_hidden_by_label_rounding_changes_meaning(declared_pair):
    part, model, holes = declared_pair
    model = replace(
        model,
        authored_dimensions=(
            RequestedDimension(holes[0], "location", member=0, discriminator="x"),
        ),
        datums=[replace(model.datums[0], at=(-29.999, -25, -5))],
    )
    before = _build(part, model)
    datum = model.datums[0]
    altered = replace(datum, at=(datum.at[0] + 0.001, *datum.at[1:]))
    after = _build(part, replace(model, datums=[altered]))
    assert not diff_builds(before, after)["dimensions_changed"]
    result = compare_measurements(before, after)
    assert result["status"] == "changed", result
    assert result["changed"][0]["parameter_id"] == "location.location.member.0.x"


def test_a_tolerance_change_is_not_preservation(declared_pair):
    part, model, holes = declared_pair
    before = _build(part, replace(model, decorations={(holes[0], "diameter"): 0.1}))
    after = _build(part, replace(model, decorations={(holes[0], "diameter"): 0.1001}))
    result = compare_measurements(before, after)
    assert result["status"] == "changed", result
    assert result["changed"][0]["parameter_id"] == "bore.diameter"


def test_moving_feature_and_datum_together_changes_the_reference_span(declared_pair):
    part, model, holes = declared_pair
    model = replace(
        model,
        authored_dimensions=(
            RequestedDimension(holes[0], "location", member=0, discriminator="x"),
        ),
    )
    before = _build(part, model)
    datum = model.datums[0]
    # Both offsets read 15 mm, but their physical reference points differ.
    assert holes[0].frame.origin[0] == -15 and datum.at[0] == -30
    moved = replace(holes[0], frame=replace(holes[0].frame, origin=(-5, -10, 0)))
    moved_part = Box(60, 50, 10)
    for feature in (moved, holes[1]):
        moved_part -= Pos(*feature.frame.origin) * Cylinder(2, 10)
    after = _build(
        moved_part,
        replace(
            model,
            features=[moved, holes[1]],
            datums=[replace(datum, at=(-20, *datum.at[1:]))],
            authored_dimensions=(
                RequestedDimension(moved, "location", member=0, discriminator="x"),
            ),
        ),
    )
    assert not diff_builds(before, after)["dimensions_changed"]
    result = compare_measurements(before, after, feature_pairs=((holes[0], moved),))
    assert result["status"] == "changed", result
    assert result["changed"][0]["parameter_id"] == "location.location.member.0.x"


def test_snapshot_retains_a_measurement_removed_from_the_live_drawing(declared_pair):
    part, model, _holes = declared_pair
    drawing = _build(part, model)
    before = drawing.measurement_snapshot()
    assert len(before.claims) == 1
    drawing.remove(before.claims[0].annotation)
    result = compare_measurements(before, drawing)
    assert result["status"] == "changed"
    assert len(result["lost"]) == 1


def test_changed_claim_text_cannot_hide_behind_the_same_confirmed_number(declared_pair):
    part, model, _holes = declared_pair
    drawing = _build(part, model)
    before = drawing.measurement_snapshot()
    assert len(before.claims) == 1
    drawing.registry.named(before.claims[0].annotation).label = "⌀4 ±0.2 THRU"
    result = compare_measurements(before, drawing)
    assert result["status"] == "changed", result


def test_a_claim_missing_its_compiled_value_is_unknown(declared_pair):
    part, model, _holes = declared_pair
    drawing = _build(part, model)
    before = drawing.measurement_snapshot()
    drawing.registry.named(before.claims[0].annotation).label = "⌀999 THRU"
    result = compare_measurements(before, drawing)
    assert result["status"] == "unknown"
    assert result["lost"] == []
    assert any(item["reason"] == "compiled_claim_unconfirmed" for item in result["unknown"])
    reverse = compare_measurements(drawing, before)
    assert reverse["status"] == "unknown" and reverse["gained"] == []
    assert any(line.startswith("UNKNOWN:") for line in explain(diff_builds(drawing, drawing)))


def test_feature_pair_must_reference_the_exact_snapshot_inventory(declared_pair):
    part, model, holes = declared_pair
    drawing = _build(part, model)
    with pytest.raises(ValueError, match="exact owners"):
        compare_measurements(drawing, drawing, feature_pairs=((replace(holes[0]), holes[0]),))
    with pytest.raises(ValueError, match="one-to-one"):
        compare_measurements(drawing, drawing, feature_pairs=((holes[0], holes[1]),))
