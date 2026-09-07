"""A declaration edit must preserve owners and meaning, not merely counts or labels."""

from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet, build_drawing
from draftwright._core import _dim
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


def _replace_with_provenance(drawing, old, new, provenance):
    for attr, value in vars(provenance).items():
        if attr.startswith("covers_") or (attr.startswith("_dw_") and attr != "_dw_spec"):
            setattr(new, attr, value)
    drawing.items[next(i for i, item in enumerate(drawing.items) if item is old)] = new
    drawing.registry.replace_object(old, new)


@pytest.mark.parametrize(
    ("fixture", "names"),
    [
        ("grm04_drive_plate.step", ("dim_step_0", "dim_step_1")),
        ("tuner_jig_blind_obround_pockets.step", ("m_locx0", "m_locy0")),
    ],
    ids=["step-spans", "pocket-location-axes"],
)
def test_swapping_labels_between_coarse_claims_changes_the_measurements(fixture, names):
    drawing = build_drawing(Path(__file__).parent / "fixtures" / fixture)
    original = [drawing.get_annotation(name) for name in names]
    assert original[0].label != original[1].label
    before = drawing.measurement_snapshot()
    assert not before.unknown
    # Rebuild real dimension geometry with exchanged labels while retaining its
    # recorded ownership and spans. The two labels still form the same multiset.
    for index, old in enumerate(original):
        spec = old._dw_spec
        new = _dim(
            spec.p1,
            spec.p2,
            spec.side,
            spec.distance,
            spec.draft,
            **dict(spec.kwargs, label=original[1 - index].label),
        )
        _replace_with_provenance(drawing, old, new, old)
    assert sum(issue.code == "label_vs_measured" for issue in drawing.lint(physical=False)) == 2
    result = compare_measurements(before, drawing)
    assert result["status"] == "changed", result
    assert result["changed"]


def test_a_scale_change_preserves_real_dimension_measurements():
    part = Box(60, 40, 10)
    before = build_drawing(part, scale=1)
    after = build_drawing(part, model=before.model(), scale=2)
    assert before.scale == 1 and after.scale == 2
    assert any(getattr(item, "measured_length", None) for item in before.items)
    result = compare_measurements(before, after)
    assert result["status"] == "preserved", result


def test_equal_valued_location_axes_keep_their_recorded_component_identity():
    part = Box(50, 50, 30) - Pos(10, 10, 10) * Box(22, 14, 10)
    drawing = build_drawing(part)
    x = drawing.get_annotation("m_locx0")
    y = drawing.get_annotation("m_locy0")
    assert x.label == y.label == "35"
    assert x.covers_hole_locations[0][1].endswith(".x")
    assert y.covers_hole_locations[0][1].endswith(".y")
    before = drawing.measurement_snapshot()
    # Substitute another real Y dimension, including its producer-recorded axis,
    # for X. The coarse registry id, label and measured length all remain equal.
    spec = y._dw_spec
    duplicate = _dim(spec.p1, spec.p2, spec.side, spec.distance, spec.draft, **spec.kwargs)
    _replace_with_provenance(drawing, x, duplicate, y)

    result = compare_measurements(before, drawing)
    assert result["status"] == "changed", result
    assert result["changed"]


def test_a_changed_dimension_path_cannot_keep_its_original_claim():
    drawing = build_drawing(Box(60, 40, 10), scale=2)
    old = drawing.get_annotation("dim_height")
    before = drawing.measurement_snapshot()
    spec = old._dw_spec
    end = (spec.p2[0], spec.p2[1] + drawing.scale, spec.p2[2])
    new = _dim(spec.p1, end, spec.side, spec.distance, spec.draft, **spec.kwargs)
    assert new.label == old.label
    assert new.measured_length != old.measured_length
    _replace_with_provenance(drawing, old, new, old)
    assert any(issue.code == "label_vs_measured" for issue in drawing.lint(physical=False))

    result = compare_measurements(before, drawing)
    assert result["status"] == "changed", result
    assert result["changed"]
