"""Typed limit dimensions satisfy their nominal claim without midpoint guessing (#1759)."""

from dataclasses import dataclass
from types import SimpleNamespace

from build123d import Box

from draftwright import Sheet
from draftwright.linting.evidence import verify_measurement_claims
from draftwright.model.compiled import ApprovedDimension, compile_dimensions


@dataclass(frozen=True)
class _Claim:
    parameter: str


class _Registry:
    def __init__(self, label: str, claim: _Claim) -> None:
        self.annotation = SimpleNamespace(label=label)
        self.claim = claim

    def names(self):
        return {"limit_callout"}

    def named(self, _name):
        return self.annotation

    def measurement_of(self, _name):
        return (self.claim,)


def _plan(claim: _Claim, *, limit_bounds=(34.8, 35.2), tolerance=(0.2, 0.2)):
    approved = ApprovedDimension(
        id=claim,
        value_text="35",
        value=35.0,
        span=None,
        tolerance=tolerance,
        limit_bounds=limit_bounds,
    )
    return SimpleNamespace(
        groups=(SimpleNamespace(dims=(approved,)),),
        ladders=(),
        locations=(),
        contingencies=(),
        schedules=(),
    )


def _state(label: str, *, limit_bounds=(34.8, 35.2), tolerance=(0.2, 0.2)) -> str:
    claim = _Claim("bore.diameter")
    outcomes = verify_measurement_claims(
        _Registry(label, claim),
        _plan(claim, limit_bounds=limit_bounds, tolerance=tolerance),
    )
    assert len(outcomes) == 1
    return outcomes[0].state


def test_two_typed_limits_bear_out_the_nominal_claim() -> None:
    assert _state("2× ⌀34.8 - ⌀35.2 THRU") == "confirmed"


def test_changed_bound_or_missing_bound_does_not_bear_out_the_interval() -> None:
    assert _state("2× ⌀34.7 - ⌀35.2 THRU") == "value_absent"
    assert _state("2× ⌀35.2 THRU") == "value_absent"


def test_nominal_only_does_not_satisfy_a_limit_presentation() -> None:
    assert _state("2× ⌀35 THRU") == "value_absent"


def test_two_numbers_around_a_nominal_are_not_inferred_as_limits() -> None:
    assert _state("2× ⌀34.8 - ⌀35.2 THRU", limit_bounds=None) == "value_absent"


def test_incoherent_typed_interval_fails_closed_instead_of_falling_back_to_nominal() -> None:
    assert _state("2× ⌀35 THRU", tolerance=(0.1, 0.2)) == "no_expected_value"
    assert _state("2× ⌀35 THRU", limit_bounds=(35.2, 34.8)) == "no_expected_value"


def test_limit_bounds_cross_the_compiler_boundary_with_their_owner() -> None:
    sheet = Sheet(Box(80, 60, 10))
    hole = sheet.hole(diameter=35, at=(0, 0, 0), axis="z", depth=10)
    hole.tolerance(
        0.2,
        0.2,
        source="ap242_pmi",
        source_ids=("dimension:probe",),
        limit_bounds=(34.8, 35.2),
    )
    sheet.authored_dimensions()
    sheet.dimension(hole, "bore.diameter")

    model = sheet.model()
    group = compile_dimensions(model).of_kind("hole")[0]
    diameter = next(row for row in group.dims if row.parameter_id == "bore.diameter")

    assert diameter.limit_bounds == (34.8, 35.2)
    assert diameter.tolerance == (0.2, 0.2)
    assert diameter.id is not None and diameter.id.feature is model.features[0]
