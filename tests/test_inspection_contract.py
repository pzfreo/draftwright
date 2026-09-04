"""Independent checks for Draftwright's stable declared-geometry inspection join."""

from __future__ import annotations

import copy
import importlib.metadata
import inspect
from pathlib import Path

import b123d_recognisers.inspection as inspection
import pytest

from draftwright.inspection_contract import (
    InspectionContractError,
    consumer_inspection_declaration,
    validate_inspection_contract,
)

ROOT = Path(__file__).parents[1]


def _manifest() -> dict:
    return inspection.inspection_api_manifest()


def test_installed_pypi_wheel_satisfies_the_inspection_contract() -> None:
    distribution = importlib.metadata.distribution("b123d-recognisers")

    assert distribution.version == "0.4.14"
    assert distribution.read_text("direct_url.json") is None
    assert (
        Path(inspect.getfile(inspection.inspection_api_manifest))
        .resolve()
        .is_relative_to(ROOT / ".venv")
    )
    validate_inspection_contract()


def test_consumer_declaration_is_an_isolated_value() -> None:
    first = consumer_inspection_declaration()
    first["api"]["symbols"]["inspect_face"]["contract"]["signature"] = "changed"

    assert consumer_inspection_declaration() != first


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("format_version",), 2),
        (("package", "version"), "0.4.5"),
        (("api", "major"), 2),
        (("api", "namespace"), "b123d_recognisers.experimental_geometry"),
    ],
)
def test_provider_identity_and_version_mutations_fail_closed(
    path: tuple[str, ...], replacement: object
) -> None:
    package = copy.deepcopy(_manifest())
    target = package
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = replacement

    with pytest.raises(InspectionContractError, match="mismatch"):
        validate_inspection_contract(package)


def test_cylinder_parameter_layout_mutation_fails_closed() -> None:
    package = copy.deepcopy(_manifest())
    package["api"]["surface_parameters"]["cylinder"][-1]["unit"] = "unitless"

    with pytest.raises(InspectionContractError, match="cylinder parameter contract"):
        validate_inspection_contract(package)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda package: package.update(api=None), "api must be an object"),
        (
            lambda package: package["api"].update(surface_parameters=None),
            "surface_parameters must be an object",
        ),
        (lambda package: package["api"].update(symbols=None), "symbols must be a list"),
        (
            lambda package: package["api"].update(symbols=[{"kind": "function"}]),
            "symbol must be a named object",
        ),
    ],
)
def test_malformed_provider_containers_fail_closed(mutate, message: str) -> None:
    package = copy.deepcopy(_manifest())
    mutate(package)

    with pytest.raises(InspectionContractError, match=message):
        validate_inspection_contract(package)


@pytest.mark.parametrize("symbol", ["inspect_face", "classify_bevel", "read_double_d_tool"])
def test_required_signature_mutation_fails_closed(symbol: str) -> None:
    package = copy.deepcopy(_manifest())
    entry = next(item for item in package["api"]["symbols"] if item["name"] == symbol)
    entry["contract"]["signature"] = "(*args)"

    with pytest.raises(InspectionContractError, match=repr(symbol)):
        validate_inspection_contract(package)


def test_bevel_rejection_reason_mutation_fails_closed() -> None:
    package = copy.deepcopy(_manifest())
    entry = next(item for item in package["api"]["symbols"] if item["name"] == "BevelReject")
    entry["contract"]["attributes"][0]["values"].remove("compound")

    with pytest.raises(InspectionContractError, match="BevelReject"):
        validate_inspection_contract(package)


def test_double_d_tuple_semantics_mutation_fails_closed() -> None:
    package = copy.deepcopy(_manifest())
    entry = next(
        item for item in package["api"]["symbols"] if item["name"] == "read_double_d_tool"
    )
    entry["contract"]["returns"]["members"][3]["unit"] = "unitless"

    with pytest.raises(InspectionContractError, match="read_double_d_tool"):
        validate_inspection_contract(package)


def test_missing_or_duplicate_required_symbol_fails_closed() -> None:
    missing = copy.deepcopy(_manifest())
    missing["api"]["symbols"] = [
        item for item in missing["api"]["symbols"] if item["name"] != "cone_rims"
    ]
    duplicate = copy.deepcopy(_manifest())
    duplicate["api"]["symbols"].append(copy.deepcopy(duplicate["api"]["symbols"][0]))

    with pytest.raises(InspectionContractError, match="cone_rims.*missing"):
        validate_inspection_contract(missing)
    with pytest.raises(InspectionContractError, match="duplicated"):
        validate_inspection_contract(duplicate)


def test_additive_provider_symbol_does_not_break_the_consumed_subset() -> None:
    package = copy.deepcopy(_manifest())
    package["api"]["symbols"].append(
        {
            "name": "future_inspection",
            "kind": "function",
            "qualified_name": "b123d_recognisers.inspection.future_inspection",
            "contract": {"signature": "() -> None"},
        }
    )

    validate_inspection_contract(package)


def test_consumer_declaration_mutation_fails_closed() -> None:
    declaration = consumer_inspection_declaration()
    declaration["api"]["symbols"]["SurfaceKind"]["contract"]["members"].pop()

    with pytest.raises(InspectionContractError, match="consumer inspection declaration"):
        validate_inspection_contract(declaration=declaration)
