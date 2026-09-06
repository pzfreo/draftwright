"""PMI lowering must not produce nominal ownership that the planner rejects."""

from dataclasses import replace

import pytest
from build123d import Box

from draftwright.model import plan_dimensions
from draftwright.model.ir import (
    AuthoredDimension,
    CylindricalReference,
    Frame,
    NominalRequirement,
    PartModel,
    StepFeature,
)
from draftwright.model.pmi_lowering import lower_ap242_nominal_diameters


def _model(nominal, diameter):
    frame = Frame((0, 0, 0), "z")
    owner = StepFeature(frame=frame, length=10, diameter=diameter, span=((0, 0, -5), (0, 0, 5)))
    dimension = AuthoredDimension(
        frame=frame,
        dimension_kind="diameter",
        value=nominal,
        label=f"ø{nominal:g}",
        dominant_axis="Z",
        ref_pts=((0, 0, 0),),
        source_id="dimension:1325",
        cylindrical_refs=(
            CylindricalReference(
                axis_origin=(0, 0, 0),
                axis_direction=(0, 0, 1),
                radius=diameter / 2,
                axial_interval=(-5, 5),
                sense="external",
            ),
        ),
    )
    return PartModel(Box(20, 20, 10).bounding_box(), "z", [owner, dimension])


@pytest.mark.parametrize(
    "nominal,diameter", [(4, 4.005), (4, 3.995), (4, 4.000002), (10000, 10000.005)]
)
def test_mismatched_nominal_is_blocked_before_planning(nominal, diameter):
    model = _model(nominal, diameter)
    # Topology correspondence is exact; only the stated nominal differs.
    assert model.features[1].cylindrical_refs[0].diameter == diameter
    assert abs(nominal - diameter) > 1e-6
    lowered = lower_ap242_nominal_diameters(model)
    plan_dimensions(lowered)
    assert not lowered.decorations
    retained = [f for f in lowered.features if isinstance(f, AuthoredDimension)]
    assert len(retained) == 1 and retained[0].value == nominal
    assert any("nominal" in reason for reason in retained[0].lowering_blockers)


@pytest.mark.parametrize("delta", [0, 0.0000005, -0.0000005])
def test_agreeing_nominal_keeps_canonical_owner(delta):
    lowered = lower_ap242_nominal_diameters(_model(4, 4 + delta))
    assert len(lowered.features) == 1
    assert len(lowered.decorations) == 1
    requirement = next(iter(lowered.decorations.values()))
    assert requirement.value == 4
    assert requirement.source_ids == ("dimension:1325",)
    plan_dimensions(lowered)


def test_direct_authored_conflict_still_fails_in_planner():
    model = _model(4, 4.005)
    owner = model.features[0]
    model = replace(
        model,
        features=[owner],
        decorations={
            (owner, "nominal_requirement", "step.diameter"): NominalRequirement(
                4, "ap242_pmi", ("dimension:1325",)
            )
        },
    )
    with pytest.raises(ValueError, match="nominal requirement"):
        plan_dimensions(model)


def test_mismatch_survives_generated_sheet_without_crashing_or_changing_source(tmp_path):
    from draftwright.sheet_emit import emit_sheet_script

    lowered = lower_ap242_nominal_diameters(_model(4, 4.005))
    source = emit_sheet_script(
        lowered,
        "from build123d import Cylinder\npart = Cylinder(2.0025, 10)",
        str(tmp_path / "nominal"),
        title="Nominal mismatch",
        number="1325",
        formats=(),
    )
    namespace = {}
    exec(compile(source, "<nominal-1325>", "exec"), namespace)
    drawing = namespace["drawing"]
    retained = [f for f in drawing.model().features if isinstance(f, AuthoredDimension)]
    assert len(retained) == 1
    assert retained[0].value == 4
    assert retained[0].source_id == "dimension:1325"
    assert retained[0].lowering_blockers
    assert "authored_dim_source_unresolved" in {i.code for i in drawing.lint()}
