"""Fail-closed contract for Draftwright's declared-feature geometry reads.

Recognition-family adoption is governed by :mod:`draftwright.recogniser_contract`.  This
separate join covers the smaller, stable inspection API used when a caller declares a feature
from build123d geometry and Draftwright must measure it.
"""

from __future__ import annotations

import copy
from importlib.metadata import version as distribution_version
from typing import Any

from b123d_recognisers.inspection import inspection_api_manifest

CONSUMER_INSPECTION_FORMAT = "draftwright-inspection-api"
CONSUMER_INSPECTION_FORMAT_VERSION = 1
SUPPORTED_INSPECTION_API_MAJOR = 1
SUPPORTED_RECOGNISER_VERSION = "0.4.10"
_DISTRIBUTION = "b123d-recognisers"
_PROVIDER_FORMAT = "b123d-recognisers-inspection-api"
_NAMESPACE = "b123d_recognisers.inspection"


class InspectionContractError(RuntimeError):
    """The installed inspection API cannot safely serve Draftwright's declared reads."""


_CYLINDER_PARAMETERS = [
    {"name": "axis_point_x", "unit": "model-length"},
    {"name": "axis_point_y", "unit": "model-length"},
    {"name": "axis_point_z", "unit": "model-length"},
    {"name": "axis_x", "unit": "unitless"},
    {"name": "axis_y", "unit": "unitless"},
    {"name": "axis_z", "unit": "unitless"},
    {"name": "radius", "unit": "model-length"},
]

_REQUIRED_SYMBOLS: dict[str, dict[str, Any]] = {
    "AnalyticSurface": {
        "kind": "dataclass",
        "qualified_name": f"{_NAMESPACE}.AnalyticSurface",
        "contract": {
            "fields": [
                {"name": "kind", "type": "SurfaceKind"},
                {"name": "provenance", "type": "SurfaceProvenance"},
                {"name": "orientation", "type": "OrientationCapability"},
                {"name": "parameters", "type": "tuple[float,...]"},
                {"name": "requested_tolerance", "type": "float"},
                {"name": "kernel_reported_gap", "type": "float"},
            ],
            "frozen": True,
            "slots": True,
        },
    },
    "BevelReject": {
        "kind": "exception",
        "qualified_name": f"{_NAMESPACE}.BevelReject",
        "contract": {
            "attributes": [
                {
                    "name": "reason",
                    "type": "str",
                    "values": ["nonplanar", "degenerate", "aligned", "compound"],
                }
            ],
            "base": "ValueError",
        },
    },
    "FaceInspection": {
        "kind": "dataclass",
        "qualified_name": f"{_NAMESPACE}.FaceInspection",
        "contract": {
            "fields": [
                {"name": "surface", "type": "AnalyticSurface|RefusedSurface"},
                {"name": "anchor", "type": "tuple[float,float,float]|null"},
            ],
            "frozen": True,
            "slots": True,
        },
    },
    "SurfaceKind": {
        "kind": "enum",
        "qualified_name": f"{_NAMESPACE}.SurfaceKind",
        "contract": {
            "members": [
                {"name": "PLANE", "value": "plane"},
                {"name": "CYLINDER", "value": "cylinder"},
                {"name": "CONE", "value": "cone"},
                {"name": "SPHERE", "value": "sphere"},
            ]
        },
    },
    "classify_bevel": {
        "kind": "function",
        "qualified_name": f"{_NAMESPACE}.classify_bevel",
        "contract": {
            "signature": (
                "(face: 'FaceLike') -> 'tuple[int, Vector3, dict[int, tuple[float, float]], "
                "float, float]'"
            )
        },
    },
    "cone_rims": {
        "kind": "function",
        "qualified_name": f"{_NAMESPACE}.cone_rims",
        "contract": {
            "signature": "(face: 'FaceLike') -> 'tuple[EdgeLike, EdgeLike, float] | None'"
        },
    },
    "floor_face_anchor": {
        "kind": "function",
        "qualified_name": f"{_NAMESPACE}.floor_face_anchor",
        "contract": {"signature": "(face: 'FaceLike') -> 'tuple[float, float, float]'"},
    },
    "inspect_face": {
        "kind": "function",
        "qualified_name": f"{_NAMESPACE}.inspect_face",
        "contract": {"signature": "(face: 'FaceLike') -> 'FaceInspection'"},
    },
    "read_double_d_tool": {
        "kind": "function",
        "qualified_name": f"{_NAMESPACE}.read_double_d_tool",
        "contract": {
            "signature": (
                "(obj: 'Part', *, tol: 'float' = 1e-05) -> "
                "'tuple[str, float, float, Vector3, float, Vector3]'"
            ),
            "returns": {
                "kind": "tuple",
                "members": [
                    {"name": "axis", "type": "str", "unit": None, "values": ["x", "y", "z"]},
                    {
                        "name": "major_diameter",
                        "type": "float",
                        "unit": "model-length",
                        "values": None,
                    },
                    {
                        "name": "across_flats",
                        "type": "float",
                        "unit": "model-length",
                        "values": None,
                    },
                    {
                        "name": "origin",
                        "type": "tuple[float,float,float]",
                        "unit": "model-length",
                        "values": None,
                    },
                    {
                        "name": "depth",
                        "type": "float",
                        "unit": "model-length",
                        "values": None,
                    },
                    {
                        "name": "profile_direction",
                        "type": "tuple[float,float,float]",
                        "unit": "unitless",
                        "values": None,
                    },
                ],
            },
        },
    },
}


def consumer_inspection_declaration() -> dict[str, Any]:
    """Return an isolated declaration of the inspection contract Draftwright consumes."""
    return {
        "format": CONSUMER_INSPECTION_FORMAT,
        "format_version": CONSUMER_INSPECTION_FORMAT_VERSION,
        "consumer": {"name": "draftwright"},
        "package": {"name": _DISTRIBUTION, "version": SUPPORTED_RECOGNISER_VERSION},
        "api": {
            "format": _PROVIDER_FORMAT,
            "format_version": 1,
            "major": SUPPORTED_INSPECTION_API_MAJOR,
            "namespace": _NAMESPACE,
            "surface_parameters": {"cylinder": copy.deepcopy(_CYLINDER_PARAMETERS)},
            "symbols": copy.deepcopy(_REQUIRED_SYMBOLS),
        },
    }


def _require_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise InspectionContractError(f"{label} mismatch: expected {expected!r}, got {actual!r}")


def validate_inspection_contract(
    package_manifest: dict[str, Any] | None = None,
    declaration: dict[str, Any] | None = None,
) -> None:
    """Validate the installed provider against Draftwright's exact consumed subset."""
    expected_declaration = consumer_inspection_declaration()
    consumer = expected_declaration if declaration is None else declaration
    _require_equal("consumer inspection declaration", consumer, expected_declaration)

    installed_version = distribution_version(_DISTRIBUTION)
    _require_equal("installed package version", installed_version, SUPPORTED_RECOGNISER_VERSION)

    package = inspection_api_manifest() if package_manifest is None else package_manifest
    _require_equal("provider format", package.get("format"), _PROVIDER_FORMAT)
    _require_equal("provider format version", package.get("format_version"), 1)
    _require_equal("provider package", package.get("package"), consumer["package"])

    api = package.get("api")
    if not isinstance(api, dict):
        raise InspectionContractError("provider api must be an object")
    _require_equal("inspection API major", api.get("major"), SUPPORTED_INSPECTION_API_MAJOR)
    _require_equal("inspection namespace", api.get("namespace"), _NAMESPACE)

    parameters = api.get("surface_parameters")
    if not isinstance(parameters, dict):
        raise InspectionContractError("provider surface_parameters must be an object")
    _require_equal("cylinder parameter contract", parameters.get("cylinder"), _CYLINDER_PARAMETERS)

    symbols = api.get("symbols")
    if not isinstance(symbols, list):
        raise InspectionContractError("provider symbols must be a list")
    named_symbols: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        if not isinstance(symbol, dict) or not isinstance(symbol.get("name"), str):
            raise InspectionContractError("every provider symbol must be a named object")
        name = symbol["name"]
        if name in named_symbols:
            raise InspectionContractError(f"provider symbol {name!r} is duplicated")
        named_symbols[name] = symbol

    required = consumer["api"]["symbols"]
    for name, expected in required.items():
        if name not in named_symbols:
            raise InspectionContractError(f"required inspection symbol {name!r} is missing")
        actual = named_symbols[name]
        consumed = {
            "kind": actual.get("kind"),
            "qualified_name": actual.get("qualified_name"),
            "contract": actual.get("contract"),
        }
        _require_equal(f"inspection symbol {name!r}", consumed, expected)
