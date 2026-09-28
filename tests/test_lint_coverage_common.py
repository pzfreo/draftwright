"""Shared blind-slot facts keep the released record's strict value boundary."""

import pytest

from draftwright.linting._coverage_common import (
    recess_point,
    recess_positive,
    recess_rounded,
)


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
