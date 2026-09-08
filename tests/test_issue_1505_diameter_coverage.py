"""A hole's equal diameter cannot erase a physical shoulder or boss measurement."""

from dataclasses import replace

import pytest
from build123d import Box, Compound, Cylinder, Pos

from draftwright import build_drawing
from draftwright.model import PartModel, rotational, step
from draftwright.model.compiled import compile_dimensions
from draftwright.model.ir import RequestedDimension


@pytest.fixture(scope="module", params=("shoulders", "boss"))
def equal_diameter_drawing(request):
    if request.param == "shoulders":
        lower = Pos(0, 0, -14) * Cylinder(36, 22) + Pos(0, 0, -1.5) * Cylinder(20, 3)
        upper = Pos(0, 0, 14) * Cylinder(36, 22) + Pos(0, 0, 1.5) * Cylinder(20, 3)
        flange = Box(120, 110, 6) - Cylinder(20, 12)
        part = Compound(children=[lower, upper, flange])
        assert len(part.solids()) == 3
        kind, diameter, count = "step", 40, 2
    else:
        part = Box(100, 60, 10) - Pos(-30, 0, 0) * Cylinder(10, 20)
        part += Pos(30, 0, 10) * Cylinder(10, 10)
        assert len(part.solids()) == 1
        kind, diameter, count = "boss", 20, 1
    drawing = build_drawing(part, page="A2", scale=1, scale_policy="permissive")
    owners = [
        feature
        for feature in drawing.model().features
        if feature.kind == kind and feature.diameter == pytest.approx(diameter)
    ]
    assert len(owners) == count
    holes = [
        feature
        for feature in drawing.model().features
        if feature.kind == "hole" and feature.diameter == pytest.approx(diameter)
    ]
    assert len(holes) == 1, "the independent equal-diameter hole must actually be recognised"
    return drawing, owners, holes[0]


@pytest.mark.parametrize("mode", ("automatic", "deferred"))
def test_equal_bore_and_external_diameters_keep_separate_measurement_coverage(
    equal_diameter_drawing, mode
):
    drawing, owners, hole = equal_diameter_drawing
    hole_names = {
        name
        for name in drawing.registry.names()
        for identity in drawing.registry.measurement_of(name)
        if identity.feature is hole and identity.parameter == "bore.diameter"
    }
    assert hole_names, "the independent equal-diameter hole must be printed"
    saved_registry, saved_items = drawing.registry.snapshot(), list(drawing.items)
    try:
        if mode == "deferred":
            # The hole is committed before this edit. Automatic leader queuing
            # can otherwise defer it until after the external-diameter pass.
            existing = {
                name
                for name in drawing.registry.names()
                for identity in drawing.registry.measurement_of(name)
                if any(identity.feature is owner for owner in owners)
                and identity.parameter.endswith(".diameter")
            }
            assert existing and existing.isdisjoint(hole_names)
            for name in existing:
                drawing.remove(name)
            with drawing.deferred():
                for owner in owners:
                    drawing.callout(owner)
        for owner in owners:
            names = {
                name
                for name in drawing.registry.names()
                for identity in drawing.registry.measurement_of(name)
                if identity.feature is owner and identity.parameter == f"{owner.kind}.diameter"
            }
            assert names and names.isdisjoint(hole_names)
        report = drawing.report()
        assert not [
            row
            for row in report["recognition"]["requirements"]
            if row["family"] == "turned_steps" and row["state"] != "placed"
        ]
    finally:
        drawing.registry.restore(saved_registry)
        drawing.items[:] = saved_items


def test_equal_external_diameters_remain_independently_removable(equal_diameter_drawing):
    drawing, owners, hole = equal_diameter_drawing
    saved_registry, saved_items = drawing.registry.snapshot(), list(drawing.items)
    try:
        names_by_owner = [
            {
                name
                for name in drawing.registry.names()
                for identity in drawing.registry.measurement_of(name)
                if identity.feature is owner and identity.parameter == f"{owner.kind}.diameter"
            }
            for owner in owners
        ]
        assert all(len(names) == 1 for names in names_by_owner)
        assert len(set.union(*names_by_owner)) == len(owners), (
            "equal diameters on distinct shoulders cannot become one unowned mark"
        )
        removed = drawing.drop(owners[0])
        assert names_by_owner[0] <= set(removed)
        assert not names_by_owner[0] & set(drawing.registry.names())
        assert all(names <= set(drawing.registry.names()) for names in names_by_owner[1:])
        assert any(
            identity.feature is hole and identity.parameter == "bore.diameter"
            for name in drawing.registry.names()
            for identity in drawing.registry.measurement_of(name)
        )
    finally:
        drawing.registry.restore(saved_registry)
        drawing.items[:] = saved_items


@pytest.fixture(scope="module")
def diameter_model():
    band = step(diameter=30, length=40, at=(0, 0, 0), axis="z")
    body = rotational(od=30, at=(0, 0, 0), axis="z")
    return PartModel(Cylinder(15, 40).bounding_box(), "z", features=[band, body])


@pytest.mark.parametrize("case", ("unique", "offset", "equal-peer", "hidden-peer", "tolerance"))
def test_global_od_reuse_requires_one_undecorated_band_on_the_same_axis(diameter_model, case):
    model = replace(diameter_model, features=list(diameter_model.features))
    band, body = model.features
    if case == "offset":
        model.features[0] = replace(band, frame=replace(band.frame, origin=(2, 0, 0)))
    elif case in {"equal-peer", "hidden-peer"}:
        peer = step(diameter=30, length=10, at=(0, 0, 40), axis="z")
        model.features.append(peer)
        if case == "hidden-peer":
            model.authored_dimensions = (
                RequestedDimension(band, "step.diameter"),
                RequestedDimension(body, "od.diameter"),
            )
    elif case == "tolerance":
        model.decorations = {(band, "diameter"): 0.1}
    plan = compile_dimensions(model)
    (group,) = plan.of_kind("rotational")
    od = group.dim(kind="diameter", role="od")
    assert od is not None
    if case == "unique":
        assert len(od.measurement_ids) == 2
        assert od.equivalent_ids[0].feature is band
        assert od.equivalent_ids[0].parameter == "step.diameter"
    else:
        assert od.equivalent_ids == (), "unrelated or decorated facts cannot borrow a global OD"
