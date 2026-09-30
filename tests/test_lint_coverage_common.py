"""Shared coverage evidence and blind-slot values preserve their source contracts."""

from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from draftwright.linting._coverage_common import (
    index_evidence,
    index_pad_pocket_evidence,
    recess_point,
    recess_positive,
    recess_rounded,
)
from draftwright.linting._registry import RequirementCarrier, measurement_outcome_index
from draftwright.linting.issues import LintIssue, is_placement_drop


def test_exact_measurement_index_keeps_placed_notes_and_placement_drops_distinct():
    @dataclass(frozen=True)
    class Identity:
        feature: object
        parameter: object

    feature = object()
    placed = Identity(feature, "width.length")
    note = Identity(feature, "height.length")
    dropped = Identity(feature, "depth.length")
    validation_only = Identity(feature, "radius.length")
    placement_issue = LintIssue(
        "warning",
        "no room",
        code="depth_dropped",
        measurement_ids=(dropped, Identity(None, "invalid"), Identity(feature, 42)),
    )
    validation_issue = LintIssue(
        "warning",
        "invalid record",
        code="radius_dropped",
        outcome_stage="validation",
        measurement_ids=(validation_only,),
    )

    class Registry:
        issues = (placement_issue, validation_issue)

        def names(self):
            return ("dimension", "note")

        def measurement_of(self, name):
            return (placed,) if name == "dimension" else ()

        def satisfaction_of(self, name):
            return (
                (note, Identity(None, "invalid"), Identity(feature, 42)) if name == "note" else ()
            )

    assert (
        len({placed.parameter, note.parameter, dropped.parameter, validation_only.parameter}) == 4
    )
    assert is_placement_drop(placement_issue)
    assert not is_placement_drop(validation_issue)
    assert index_evidence is measurement_outcome_index
    assert index_evidence(Registry()) == (
        {(feature, "width.length")},
        {(feature, "height.length")},
        {(feature, "depth.length")},
    )


@pytest.mark.parametrize("family", ("pad", "pocket"))
def test_pad_pocket_index_keeps_physical_evidence_in_its_family(family):
    from draftwright.linting.pad_coverage import _index_evidence as pad_index
    from draftwright.linting.pocket_coverage import _index_evidence as pocket_index

    @dataclass(frozen=True)
    class Feature:
        kind: str

    @dataclass(frozen=True)
    class Identity:
        feature: object
        parameter: object

    owner = Feature(family)
    other = Feature("pocket" if family == "pad" else "pad")
    invalid = object()
    point = (1.23456, 2, 3)
    rounded = (1.235, 2.0, 3.0)
    location_parameter = f"location_{family}.location.x"
    foreign_parameter = f"location_{other.kind}.location.x"
    annotations = {
        "dimension": SimpleNamespace(
            covers_hole_locations=(
                (owner, location_parameter, point),
                (Identity(owner, location_parameter), point),
                (other, foreign_parameter, point),
                invalid,
            )
        ),
        "note": SimpleNamespace(),
    }
    placement = LintIssue(
        "warning",
        "no room",
        code="location_dropped",
        measurement_ids=(Identity(owner, "height.length"), Identity(None, "invalid")),
        hole_requirement_ids=((owner, location_parameter), (other, foreign_parameter)),
    )
    validation = LintIssue(
        "warning",
        "bad input",
        code="location_dropped",
        outcome_stage="validation",
        measurement_ids=(Identity(owner, "validation.length"),),
        hole_requirement_ids=((owner, "validation.location"),),
    )

    class Registry:
        issues = (placement, validation)

        def names(self):
            return tuple(annotations)

        def named(self, name):
            return annotations[name]

        def measurement_of(self, name):
            return (Identity(owner, "width.length"),) if name == "dimension" else ()

        def satisfaction_of(self, name):
            return (
                (Identity(owner, "depth.length"), Identity(None, "invalid"))
                if name == "note"
                else ()
            )

    carriers = SimpleNamespace(locations=defaultdict(list))
    expected = (
        {(owner, "width.length")},
        {(owner, location_parameter): {rounded}},
        {(owner, "depth.length")},
        {(owner, "height.length"), (owner, location_parameter)},
    )
    result = index_pad_pocket_evidence(Registry(), family=family, carriers=carriers)
    assert result == expected
    assert carriers.locations[(owner, location_parameter, rounded)] == [
        RequirementCarrier("dimension", "physical_location"),
        RequirementCarrier("dimension", "physical_location"),
    ]
    assert (other, foreign_parameter, rounded) not in carriers.locations
    assert (pad_index if family == "pad" else pocket_index)(Registry()) == expected


def test_blind_slot_rounding_preserves_conversion_and_finite_failures():
    assert recess_rounded(1.23456) == 1.235
    with pytest.raises(ValueError) as converted:
        recess_rounded("not a number")
    assert isinstance(converted.value.__cause__, ValueError)
    with pytest.raises(ValueError):
        recess_rounded(float("inf"))


@pytest.mark.parametrize("value", [True, "2", 0.0001, -1])
def test_blind_slot_positive_size_rejects_nonreal_or_rounded_zero(value):
    with pytest.raises(ValueError):
        recess_positive(value)
    assert recess_positive(2.12345) == 2.123


@pytest.mark.parametrize("value", [[1, 2, 3], (1, True, 3), (1, 2), (1, 2, 3, 4)])
def test_blind_slot_point_requires_three_real_tuple_components(value):
    with pytest.raises(ValueError):
        recess_point(value)
    assert recess_point((1.23456, 2, 3)) == (1.235, 2.0, 3.0)
