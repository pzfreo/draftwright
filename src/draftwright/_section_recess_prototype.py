"""Private consumer spike for the recogniser's section-recess JSON contract.

This is deliberately not connected to automatic detection or the public IR.  It proves that a
Draftwright-like consumer can validate the geometry document and derive drafting dimensions without
depending on recogniser Python classes or duplicated width/length fields.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class PrototypePocket:
    occurrence: int
    body: int
    origin: tuple[float, float, float]
    run: tuple[float, float, float]
    depth: float
    length: float
    width: float
    defining_faces: tuple[int, ...]
    constituent_faces: tuple[int, ...]


def _numbers(value: object, size: int, name: str) -> tuple[float, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != size
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
    ):
        raise ValueError(f"{name} must contain {size} numbers")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{name} must contain finite numbers")
    return result


def _indices(value: object, name: str, limit: int) -> tuple[int, ...]:
    if not isinstance(value, list) or any(type(item) is not int for item in value):
        raise ValueError(f"{name} must be an index array")
    result = tuple(value)
    if result != tuple(sorted(set(result))) or any(item < 0 or item >= limit for item in result):
        raise ValueError(f"{name} indices must be sorted, unique, and in range")
    return result


def _arc_radius(start: tuple[float, float], end: tuple[float, float], bulge: float) -> float:
    chord = math.dist(start, end)
    if chord <= 1e-9 or abs(bulge) <= 1e-12:
        raise ValueError("arc must have distinct endpoints and nonzero bulge")
    return chord * (1 + bulge * bulge) / (4 * abs(bulge))


def _obround_dimensions(boundary: object) -> tuple[float, float]:
    if not isinstance(boundary, list) or len(boundary) != 4:
        raise ValueError("prototype supports a four-vertex obround boundary")
    vertices: list[tuple[tuple[float, float], float]] = []
    for item in boundary:
        if not isinstance(item, Mapping) or set(item) != {"point", "bulge"}:
            raise ValueError("profile vertex must contain point and bulge")
        point = _numbers(item["point"], 2, "profile point")
        bulge = _numbers((item["bulge"],), 1, "profile bulge")[0]
        vertices.append(((point[0], point[1]), bulge))
    arcs = [index for index, (_, bulge) in enumerate(vertices) if bulge != 0.0]
    lines = [index for index, (_, bulge) in enumerate(vertices) if bulge == 0.0]
    if (
        len(arcs) != 2
        or len(lines) != 2
        or any(abs(abs(vertices[index][1]) - 1) > 1e-9 for index in arcs)
    ):
        raise ValueError("prototype requires two semicircular arcs and two straight sides")
    radii = [
        _arc_radius(vertices[index][0], vertices[(index + 1) % 4][0], vertices[index][1])
        for index in arcs
    ]
    if not math.isclose(radii[0], radii[1], abs_tol=1e-3):
        raise ValueError("obround end radii disagree")
    side_lengths = [math.dist(vertices[index][0], vertices[(index + 1) % 4][0]) for index in lines]
    if not math.isclose(side_lengths[0], side_lengths[1], abs_tol=1e-3):
        raise ValueError("obround straight-side lengths disagree")
    width = 2 * sum(radii) / 2
    return side_lengths[0] + width, width


def consume_section_recess_document(document: Mapping[str, Any]) -> tuple[PrototypePocket, ...]:
    """Validate the experimental document and derive obround drawing dimensions."""

    if document.get("schema_version") != 1 or document.get("reference_scope") != "result":
        raise ValueError("unsupported section-recess prototype document")
    bodies = document.get("bodies")
    faces = document.get("faces")
    occurrences = document.get("occurrences")
    if not isinstance(bodies, list) or [item.get("index") for item in bodies] != list(
        range(len(bodies))
    ):
        raise ValueError("body roster must be dense and ordered")
    if not isinstance(faces, list) or [item.get("index") for item in faces] != list(
        range(len(faces))
    ):
        raise ValueError("face roster must be dense and ordered")
    if not isinstance(occurrences, list):
        raise ValueError("occurrences must be an array")
    result = []
    for expected, item in enumerate(occurrences):
        if not isinstance(item, Mapping) or item.get("index") != expected:
            raise ValueError("occurrence roster must be dense and ordered")
        body = item.get("body")
        if type(body) is not int or body < 0 or body >= len(bodies):
            raise ValueError("occurrence body index is out of range")
        classification = item.get("classification")
        if classification != {"feature_kind": "pocket", "section_shape": "obround"}:
            raise ValueError("consumer prototype supports obround pockets only")
        geometry = item.get("geometry")
        evidence = item.get("evidence")
        if not isinstance(geometry, Mapping) or geometry.get("type") != "section_recess":
            raise ValueError("occurrence has no section-recess geometry")
        if not isinstance(evidence, Mapping):
            raise ValueError("occurrence has no evidence")
        frame = geometry.get("frame")
        profile = geometry.get("profile")
        ends = geometry.get("ends")
        interval = _numbers(geometry.get("run_interval"), 2, "run interval")
        if interval[1] <= interval[0]:
            raise ValueError("run interval must increase")
        if not isinstance(frame, Mapping) or not isinstance(profile, Mapping):
            raise ValueError("geometry frame and profile are required")
        if profile.get("closure") != "closed":
            raise ValueError("pocket prototype requires a closed profile")
        if not isinstance(ends, Mapping):
            raise ValueError("geometry ends are required")
        conditions = [ends.get(key, {}).get("condition") for key in ("low", "high")]
        if conditions.count("capped") != 1 or conditions.count("open") != 1:
            raise ValueError("pocket requires one capped and one open end")
        length, width = _obround_dimensions(profile.get("boundary"))
        defining = _indices(evidence.get("defining_faces"), "defining faces", len(faces))
        constituent = _indices(evidence.get("constituent_faces"), "constituent faces", len(faces))
        if not set(defining) <= set(constituent):
            raise ValueError("defining faces must be constituents")
        result.append(
            PrototypePocket(
                expected,
                body,
                cast(
                    tuple[float, float, float],
                    _numbers(frame.get("origin"), 3, "frame origin"),
                ),
                cast(
                    tuple[float, float, float],
                    _numbers(frame.get("run"), 3, "frame run"),
                ),
                interval[1] - interval[0],
                length,
                width,
                defining,
                constituent,
            )
        )
    return tuple(result)


__all__ = ["PrototypePocket", "consume_section_recess_document"]
