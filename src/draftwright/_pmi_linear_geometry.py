"""Prove authored PMI linear reference stations before dimension lowering."""

from __future__ import annotations

import math

from draftwright.model.ir import _linear_projection_view

_LINEAR_AXIS_ABS_TOL = 0.005
_LINEAR_AXIS_REL_TOL = 1e-3
_LINEAR_VALUE_ABS_TOL = 0.01
_LINEAR_VALUE_REL_TOL = 5e-4
_LINEAR_OBLIQUE_VALUE_ABS_TOL = 0.05


def _linear_reference_stations(
    stations: tuple[tuple[float, float, float] | None, ...],
    nominal: float,
    *,
    plane_axis: str | None = None,
) -> tuple[tuple[tuple[float, float, float], ...], str, tuple[str, ...]]:
    """Prove a truthful principal-axis span from the two authored reference groups.

    A merged bbox answers how large the referenced faces are, not which direction relates
    them. Circular end faces in GRM-03 are wider than their axial separation, which made
    short X dimensions render across Y. Conversely, CTC-04 includes a genuinely oblique
    relationship and one dimension whose second group XCAF does not transfer. Those must
    remain explicit omissions rather than being guessed from the nominal value (#1209).
    """
    measurable = tuple(station for station in stations if station is not None)
    if len(stations) != 2 or len(measurable) != 2:
        return (
            measurable,
            "?",
            ("linear dimension needs two measurable authored reference groups",),
        )

    first, second = measurable
    delta = tuple(second[index] - first[index] for index in range(3))
    magnitudes = tuple(abs(value) for value in delta)
    axis_index = (
        "XYZ".index(plane_axis)
        if plane_axis is not None
        else max(range(3), key=magnitudes.__getitem__)
    )
    primary = magnitudes[axis_index]
    if primary <= 1e-9:
        return measurable, "?", ("linear reference groups occupy the same station",)

    transverse = max(value for index, value in enumerate(magnitudes) if index != axis_index)
    direction_tol = max(_LINEAR_AXIS_ABS_TOL, primary * _LINEAR_AXIS_REL_TOL)
    # Parallel planar supports establish their distance along the plane normal.
    # Their finite face centres can be offset arbitrarily within either plane.
    oblique = plane_axis is None and transverse > direction_tol
    if oblique and _linear_projection_view(measurable) is None:
        return (
            measurable,
            "?",
            (
                "linear reference relationship does not lie in a principal projection plane "
                f"(delta=({delta[0]:.6g}, {delta[1]:.6g}, {delta[2]:.6g}) mm)",
            ),
        )

    axis = "?" if oblique else "XYZ"[axis_index]
    span = math.hypot(*delta) if oblique else primary
    value_tol = max(
        _LINEAR_OBLIQUE_VALUE_ABS_TOL if oblique else _LINEAR_VALUE_ABS_TOL,
        abs(nominal) * _LINEAR_VALUE_REL_TOL,
    )
    if abs(span - nominal) > value_tol:
        return (
            measurable,
            axis,
            (
                f"linear reference-station span {span:.6g} mm differs from nominal "
                f"{nominal:.6g} mm",
            ),
        )
    return measurable, axis, ()


def _dimension_reference_stations(
    stations: tuple[tuple[float, float, float] | None, ...],
    nominal: float,
    kind: str,
    *,
    plane_axis: str | None = None,
) -> tuple[tuple[tuple[float, float, float], ...], str, tuple[str, ...]]:
    """Apply the dimension kind's wording to the shared station proof."""
    points, axis, reasons = _linear_reference_stations(stations, nominal, plane_axis=plane_axis)
    if kind == "thickness":
        reasons = tuple(
            reason.replace("linear dimension", "thickness dimension").replace(
                "linear reference", "thickness reference"
            )
            for reason in reasons
        )
    return points, axis, reasons
