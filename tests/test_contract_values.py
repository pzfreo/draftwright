"""Shared scalar contracts at the IR and provider boundaries."""

from decimal import Decimal

import pytest

from draftwright import contract_values
from draftwright.model.ir import _strict_finite_real
from draftwright.oriented_slot_contract import _real


@pytest.mark.parametrize("value", [True, "1", Decimal("1"), float("inf"), float("nan")])
def test_finite_real_rejects_schema_impostors_at_both_boundaries(value) -> None:
    for validate in (
        lambda: _strict_finite_real("probe", value),
        lambda: _real(value, name="probe"),
    ):
        with pytest.raises(ValueError, match="^probe must be a finite real number$"):
            validate()


def test_finite_real_preserves_oriented_slot_positive_option() -> None:
    assert _strict_finite_real("probe", 0) == 0.0
    assert _real(0, name="probe") == 0.0
    with pytest.raises(ValueError, match="^probe must be a finite real number$"):
        _real(0, name="probe", positive=True)


def test_huge_integer_is_rejected_without_leaking_float_overflow() -> None:
    for validate in (
        lambda: _strict_finite_real("probe", 10**1000),
        lambda: _real(10**1000, name="probe"),
    ):
        with pytest.raises(ValueError, match="^probe must be a finite real number$") as error:
            validate()
        assert isinstance(error.value.__cause__, OverflowError)


def test_both_boundaries_use_the_shared_finite_real(monkeypatch) -> None:
    calls = []

    def substitute(value, *, name, positive=False):
        calls.append((value, name, positive))
        return 9.5

    monkeypatch.setattr(contract_values, "finite_real", substitute)
    assert _strict_finite_real("ir", 7) == 9.5
    assert _real(8, name="provider", positive=True) == 9.5
    assert calls == [(7, "ir", False), (8, "provider", True)]
