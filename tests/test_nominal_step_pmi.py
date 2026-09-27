"""A source-owned step length shares its canonical dimension instead of repeating it."""

from dataclasses import replace
from pathlib import Path

from build123d import Box

from draftwright.analysis import _analyse
from draftwright.model.ir import (
    AuthoredDimension,
    Frame,
    NominalRequirement,
    PartModel,
    StepFeature,
)
from draftwright.model.pmi_lowering import lower_ap242_nominal_step_lengths


def _step(lo=0.0, hi=20.0):
    return StepFeature(
        Frame(((lo + hi) / 2, 0, 0), "x"),
        hi - lo,
        3,
        ((lo, 0, 0), (hi, 0, 0)),
    )


def _source(lo=0.0, hi=20.0):
    return AuthoredDimension(
        Frame(((lo + hi) / 2, 0, 0), "x"),
        "linear",
        hi - lo,
        f"{hi - lo:g}",
        "X",
        ref_pts=((lo, 0, 0), (hi, 0, 0)),
        source_id="dimension:step-length",
    )


def _model(*features):
    return PartModel(Box(30, 10, 10).bounding_box(), "x", list(features))


def test_exact_step_length_coowns_one_canonical_dimension():
    step, source = _step(), _source()
    lowered = lower_ap242_nominal_step_lengths(_model(step, source))

    assert lowered.features == [step]
    assert lowered.decorations == {
        (step, "nominal_requirement", "step.length"): NominalRequirement(
            20, "ap242_pmi", ("dimension:step-length",)
        )
    }


def test_step_length_does_not_coalesce_on_value_alone_or_ambiguous_geometry():
    step, source = _step(), _source()
    for features in (
        (step, _source(2, 22)),
        (step, step, source),
        (step, replace(source, ref_pts=((0, 1, 0), (20, 1, 0)))),
        (step, replace(source, upper_tol=0.1)),
        (step, replace(source, label="20 REF")),
        (step, replace(source, value=20.00001)),
    ):
        lowered = lower_ap242_nominal_step_lengths(_model(*features))
        assert lowered.features == list(features)
        assert lowered.decorations == {}


def test_grm03_source_lengths_correlate_with_exact_steps():
    fixture = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
    analysis = _analyse(fixture, "PART", "", "", "", None, pmi="annotate")
    lengths = {
        source_id
        for (feature, kind, parameter), requirement in analysis.model.decorations.items()
        if kind == "nominal_requirement" and parameter == "step.length"
        for source_id in requirement.source_ids
    }
    assert lengths == {f"dimension:0:1:4:{index}" for index in range(6, 11)}
    assert not [
        feature for feature in analysis.model.features if isinstance(feature, AuthoredDimension)
    ]
