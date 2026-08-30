"""PMI (Product Manufacturing Information) extractor for AP242 STEP files.

Reads semantic PMI from an ISO 10303-242 STEP file via a second
``STEPCAFControl_Reader`` pass with ``SetGDTMode(True)``. The canonical result is
:class:`PmiExtractionReport`: it preserves every discovered source entity and its
extraction outcome. :func:`extract_pmi` remains the compatible records-only projection.

build123d's ``import_step`` already uses ``STEPCAFControl_Reader`` + an XCAF
document (for names/colours/layers) but never enables GDT mode and discards
the document, so the PMI is inaccessible after that call.  This module runs
a *separate*, read-only pass against the same file to recover the semantic PMI
without touching the solid geometry at all.

Key OCP gotcha: the ``label.FindAttribute(GetID_s(), attr)`` out-param pattern
returns True but leaves ``attr.Label()`` null so ``GetObject()`` throws.  The
working pattern is ``XCAFDoc_Dimension.Set_s(label).GetObject()`` (same for
GeomTolerance, Datum).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from draftwright._pmi_part21 import (
    DatumOccurrenceFact,
    GeometricToleranceFact,
    match_datum_occurrence,
    match_geometric_tolerance,
    read_datum_occurrences,
    read_geometric_tolerances,
    read_manufacturing_requirements,
)
from draftwright.model.ir import CylindricalReference

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OCP capability guard
# ---------------------------------------------------------------------------

try:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBndLib import BRepBndLib
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
    from OCP.TDF import TDF_LabelSequence, TDF_Tool
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.XCAFDoc import (
        XCAFDoc_Datum,
        XCAFDoc_Dimension,
        XCAFDoc_DimTolTool,
        XCAFDoc_DocumentTool,
        XCAFDoc_GeomTolerance,
    )

    _PMI_AVAILABLE = hasattr(STEPCAFControl_Reader, "SetGDTMode")
except ImportError:
    _PMI_AVAILABLE = False


# ---------------------------------------------------------------------------
# Type-code tables
# ---------------------------------------------------------------------------

# int → human tag for XCAFDimTolObjects_DimensionType enum
_DIM_TYPE: dict[int, str] = {
    0: "location",  # Location_None
    1: "curved_dist",  # Location_CurvedDistance
    2: "linear",  # Location_LinearDistance (outer-to-outer, generic)
    3: "linear",  # FromCenterToOuter
    4: "linear",  # FromCenterToInner
    5: "linear",  # FromOuterToCenter
    6: "linear",  # FromOuterToOuter
    7: "linear",  # FromOuterToInner
    8: "linear",  # FromInnerToCenter
    9: "linear",  # FromInnerToOuter
    10: "linear",  # FromInnerToInner
    11: "angular",  # Location_Angular (incl. curved centre-to-centre)
    12: "oriented",  # Location_Oriented
    14: "curve_length",  # Size_CurveLength
    15: "diameter",  # Size_Diameter  ← add ø prefix
    16: "diameter",  # Size_SphericalDiameter
    17: "radius",  # Size_Radius    ← add R prefix
    18: "radius",  # Size_SphericalRadius
    27: "thickness",  # Size_Thickness
    28: "angular",  # Size_Angular
    30: "label",  # CommonLabel     ← no numeric value, skip
    31: "presentation",  # DimensionPresentation ← graphical only, skip
}

# Type 31 is graphical presentation only. Type 30 (CommonLabel) can carry authored meaning
# despite having no numeric GetValue, so it must fail visibly until supported rather than be
# discarded with presentation geometry (#623 review).
_PRESENTATION_TYPES = {31}

# prefix character for the label
_DIM_PREFIX: dict[str, str] = {
    "diameter": "ø",
    "radius": "R",
}

# int → short tag for XCAFDimTolObjects_GeomToleranceType
_GTOL_TYPE: dict[int, str] = {
    1: "angularity",
    2: "circular_runout",
    3: "circularity",
    4: "coaxiality",
    5: "concentricity",
    6: "cylindricity",
    7: "flatness",
    8: "parallelism",
    9: "perpendicularity",
    10: "position",
    11: "profile_line",
    12: "profile_surface",
    13: "straightness",
    14: "symmetry",
    15: "total_runout",
}

# int → stable semantic name for XCAFDimTolObjects_GeomToleranceModif.  The whole OCCT
# vocabulary is inventoried even though only the leader-scope symbols have faithful,
# export-safe drafting representations today; every other known value remains explicit and
# fail-closed.
_GTOL_MODIFIER: dict[int, str] = {
    0: "any_cross_section",
    1: "common_zone",
    2: "each_radial_element",
    3: "free_state",
    4: "least_material_requirement",
    5: "line_element",
    6: "major_diameter",
    7: "maximum_material_requirement",
    8: "minor_diameter",
    9: "not_convex",
    10: "pitch_diameter",
    11: "reciprocity_requirement",
    12: "separate_requirement",
    13: "statistical_tolerance",
    14: "tangent_plane",
    15: "all_around",
    16: "all_over",
}
_SUPPORTED_GTOL_SCOPE_MODIFIERS = frozenset(("all_around", "all_over"))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

PmiSourceCategory = Literal[
    "dimension", "geometric_tolerance", "datum", "manufacturing_requirement"
]


@dataclass(frozen=True)
class PmiRecord:
    """One semantic PMI annotation from an AP242 STEP file.

    Attributes:
        kind:           Human-readable category (``"linear"``, ``"diameter"``,
                        ``"angular"``, ``"gtol"``, ``"datum"``, ``"external_thread"``).
        type_code:      Raw OCCT enum integer.
        value:          Nominal value in mm (or degrees for angular).
        upper_tol:      Upper tolerance in mm, or ``None``.
        lower_tol:      Lower tolerance in mm, or ``None``.
        lower_bound:    Lower limit of a range dimension, in the same units as ``value``.
        upper_bound:    Upper limit of a range dimension, in the same units as ``value``.
        ref_pts:        Reference stations in global STEP space (same coordinate frame as
                        the imported solid). For a linear or thickness dimension these are
                        the centroids of its authored reference groups; other records retain
                        the per-shape bounding-box centroids.
        ref_bbox:       Combined axis-aligned bbox of ALL referenced shapes:
                        ``(xmin, ymin, zmin, xmax, ymax, zmax)``. Linear rendering uses
                        it for transverse witness support only; the authored stations,
                        not this outer envelope, own the measured span.
        dominant_axis:  ``'X'``, ``'Y'``, ``'Z'``, or ``'?'`` — a linear/thickness
                        dimension's proven reference-station direction; for other records,
                        the referenced geometry's largest bbox extent.
        label:          Ready-to-use annotation label (e.g. ``"ø35"``, ``"60"``).
        datum_refs:     Ordered datum letters referenced by a geometric tolerance.
        part21_id:      Part21 entity id supplying an overlaid tolerance magnitude.
        source_category: Source inventory category; structural evidence for concept lowering.
        gtol_modifiers: Stable names for the source geometric-tolerance modifier sequence.
        lowering_blockers: Missing/unrepresented facts that make concept lowering unsafe.
        rendering_blockers: Source-geometry facts that make a typed dimension unsafe to draw.
        source_ids:     All source occurrences represented by one projected definition.
        datum_contexts: Tolerance semantic names in which a datum definition is referenced.
        reference_item_ids: Exact Part21 representation items bound to a datum feature.
        reference_axis: Axis normal to a proven datum reference plane.
        semantic_name: Stable source name for a semantic manufacturing requirement.
        shape_aspect_ids: Part21 shape aspects associating a semantic requirement to geometry.
        cylindrical_refs: Canonical finite-cylinder topology referenced by a Size_Diameter
                        requirement. Empty for other dimension families or unresolved geometry.
    """

    kind: str
    type_code: int | None
    value: float
    upper_tol: float | None = None
    lower_tol: float | None = None
    ref_pts: tuple[tuple[float, float, float], ...] = ()
    ref_bbox: tuple[float, float, float, float, float, float] | None = None
    dominant_axis: str = "?"
    label: str = ""
    # Stable within the source XCAF document: category + TDF label entry. Blank only for
    # hand-constructed compatibility records; extraction always fills it (#623).
    source_id: str = ""
    # Appended to preserve the positional compatibility of the original record fields.
    lower_bound: float | None = None
    upper_bound: float | None = None
    datum_refs: tuple[str, ...] = ()
    part21_id: str = ""
    source_category: PmiSourceCategory | Literal[""] = ""
    gtol_modifiers: tuple[str, ...] = ()
    lowering_blockers: tuple[str, ...] = ()
    # One datum feature can be referenced by several source occurrences. Non-datum records
    # keep this empty and use the compatible singular ``source_id`` above.
    source_ids: tuple[str, ...] = ()
    datum_contexts: tuple[str, ...] = ()
    reference_item_ids: tuple[str, ...] = ()
    reference_axis: str = ""
    semantic_name: str = ""
    shape_aspect_ids: tuple[str, ...] = ()
    # Kept separate from ``lowering_blockers``: an imported requirement may fail to enrich a
    # canonical owner yet remain a truthful standalone dimension (#1116/#1209).
    rendering_blockers: tuple[str, ...] = ()
    cylindrical_refs: tuple[CylindricalReference, ...] = ()


def _frame_direction(direction, frame) -> tuple[float, float, float]:
    """Rotate a caller-space direction into *frame* without applying its origin."""

    return tuple(
        sum(float(direction[index]) * float(axis[index]) for index in range(3))
        for axis in (frame.x, frame.y, frame.z)
    )  # type: ignore[return-value]


def _frame_axis(axis: str, frame) -> str:
    if axis.upper() not in {"X", "Y", "Z"}:
        return "?" if axis else ""
    unit = tuple(1.0 if index == "XYZ".index(axis.upper()) else 0.0 for index in range(3))
    local = _frame_direction(unit, frame)
    dominant = max(range(3), key=lambda index: abs(local[index]))
    if abs(abs(local[dominant]) - 1.0) > 1e-6 or any(
        abs(local[index]) > 1e-6 for index in range(3) if index != dominant
    ):
        return "?"
    return "XYZ"[dominant]


def _frame_bbox(bbox, frame) -> tuple[float, float, float, float, float, float] | None:
    if bbox is None:
        return None
    corners = tuple(
        frame.to_local((bbox[ix], bbox[iy], bbox[iz]))
        for ix in (0, 3)
        for iy in (1, 4)
        for iz in (2, 5)
    )
    return tuple(
        min(point[index] for point in corners) for index in range(3)
    ) + tuple(max(point[index] for point in corners) for index in range(3))


def _frame_cylinder(reference: CylindricalReference, frame) -> CylindricalReference:
    start = tuple(
        reference.axis_origin[index]
        + reference.axial_interval[0] * reference.axis_direction[index]
        for index in range(3)
    )
    finish = tuple(
        reference.axis_origin[index]
        + reference.axial_interval[1] * reference.axis_direction[index]
        for index in range(3)
    )
    local_start = frame.to_local(start)
    local_finish = frame.to_local(finish)
    direction = tuple(local_finish[index] - local_start[index] for index in range(3))
    length = math.sqrt(sum(component * component for component in direction))
    return CylindricalReference.canonical(
        axis_point=local_start,
        axis_direction=direction,
        radius=reference.radius,
        local_interval=(0.0, length),
        sense=reference.sense,
    )


def reframe_pmi_records(records, frame) -> list[PmiRecord]:
    """Map AP242 geometry facts from caller space into the framed working solid.

    This is the only source→working coordinate conversion in Draftwright. Numeric requirement
    values and source identities are unchanged; points, support bounds, axes, and finite-cylinder
    provenance move together so PMI lowering never correlates caller-space facts with local IR.
    """

    return [
        replace(
            record,
            ref_pts=tuple(frame.to_local(point) for point in record.ref_pts),
            ref_bbox=_frame_bbox(record.ref_bbox, frame),
            dominant_axis=_frame_axis(record.dominant_axis, frame),
            reference_axis=_frame_axis(record.reference_axis, frame),
            cylindrical_refs=tuple(
                _frame_cylinder(reference, frame) for reference in record.cylindrical_refs
            ),
        )
        for record in records
    ]


PmiExtractionOutcome = Literal[
    "extracted", "partially_extracted", "presentation_only", "not_extracted"
]


@dataclass(frozen=True)
class PmiSourceEntity:
    """One source AP242 entity and the outcome of the extraction stage."""

    source_id: str
    category: PmiSourceCategory
    type_code: int | None
    outcome: PmiExtractionOutcome
    reason: str = ""


@dataclass(frozen=True)
class PmiExtractionReport:
    """The immutable source census and successful record projection from one XCAF pass."""

    sources: tuple[PmiSourceEntity, ...] = ()
    records: tuple[PmiRecord, ...] = ()
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shape_bbox(shape) -> tuple[float, float, float, float, float, float]:
    """Return ``(xmin, ymin, zmin, xmax, ymax, zmax)`` of *shape* in global space."""
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    return cast(tuple[float, float, float, float, float, float], bb.Get())


def _bbox_centroid(bbox: tuple) -> tuple[float, float, float]:
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    return ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)


def _merge_bboxes(
    boxes: list[tuple[float, float, float, float, float, float]],
) -> tuple[float, float, float, float, float, float]:
    """Return the combined axis-aligned bbox of *boxes*."""
    xs = [b[0] for b in boxes] + [b[3] for b in boxes]
    ys = [b[1] for b in boxes] + [b[4] for b in boxes]
    zs = [b[2] for b in boxes] + [b[5] for b in boxes]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _dominant_from_bbox(bbox: tuple[float, float, float, float, float, float]) -> str:
    """Return ``'X'``/``'Y'``/``'Z'`` for the axis with the largest bbox extent."""
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    spans = [("X", abs(xmax - xmin)), ("Y", abs(ymax - ymin)), ("Z", abs(zmax - zmin))]
    dom = max(spans, key=lambda t: t[1])
    return dom[0] if dom[1] > 1e-6 else "?"


_LINEAR_AXIS_ABS_TOL = 0.005
_LINEAR_AXIS_REL_TOL = 1e-3
_LINEAR_VALUE_ABS_TOL = 0.01
_LINEAR_VALUE_REL_TOL = 5e-4


def _linear_reference_stations(
    stations: tuple[tuple[float, float, float] | None, ...], nominal: float
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
    axis_index = max(range(3), key=magnitudes.__getitem__)
    primary = magnitudes[axis_index]
    if primary <= 1e-9:
        return measurable, "?", ("linear reference groups occupy the same station",)

    transverse = max(value for index, value in enumerate(magnitudes) if index != axis_index)
    direction_tol = max(_LINEAR_AXIS_ABS_TOL, primary * _LINEAR_AXIS_REL_TOL)
    if transverse > direction_tol:
        return (
            measurable,
            "?",
            (
                "linear reference relationship is not principal-axis aligned "
                f"(delta=({delta[0]:.6g}, {delta[1]:.6g}, {delta[2]:.6g}) mm)",
            ),
        )

    axis = "XYZ"[axis_index]
    value_tol = max(_LINEAR_VALUE_ABS_TOL, abs(nominal) * _LINEAR_VALUE_REL_TOL)
    if abs(primary - nominal) > value_tol:
        return (
            measurable,
            axis,
            (
                f"linear reference-station span {primary:.6g} mm differs from nominal "
                f"{nominal:.6g} mm",
            ),
        )
    return measurable, axis, ()


def _dimension_geometry_blockers(
    kind: str,
    reference_reasons: tuple[str, ...],
    station_reasons: tuple[str, ...],
) -> tuple[str, ...]:
    """Rendering blockers owned by incomplete source geometry, not later correlation.

    A group station computed from the measurable subset is not proof that the authored
    relationship is complete when another referenced shape was unavailable. Keep those XCAF
    extraction failures in ``lowering_blockers`` for source accounting *and* in this distinct
    render gate. Correlation blockers added later by model lowering never pass through here,
    so #1116's standalone fallback remains drawable (#1209 re-review).
    """
    if kind not in ("linear", "thickness"):
        return ()
    return tuple(dict.fromkeys((*reference_reasons, *station_reasons)))


def _make_label(
    kind: str,
    value: float,
    upper_tol: float | None,
    lower_tol: float | None,
    *,
    lower_bound: float | None = None,
    upper_bound: float | None = None,
) -> str:
    """Format the annotation label with optional deviation or limit tolerance."""
    from draftwright._core import _fmt

    prefix = _DIM_PREFIX.get(kind, "")
    base = f"{prefix}{_fmt(value)}"
    if lower_bound is not None and upper_bound is not None:
        return f"{prefix}{_fmt(lower_bound)} - {prefix}{_fmt(upper_bound)}"
    # OCCT returns tolerances as positive magnitudes regardless of sign
    # convention.  upper_tol is always the + deviation; lower_tol is always
    # the - deviation stored as a positive magnitude.  We add explicit signs
    # so the label is unambiguous on the drawing.
    if upper_tol is not None and lower_tol is not None:
        if abs(abs(upper_tol) - abs(lower_tol)) < 1e-4:
            base += f" ±{_fmt(abs(upper_tol))}"
        else:
            base += f" +{_fmt(abs(upper_tol))}/-{_fmt(abs(lower_tol))}"
    elif upper_tol is not None:
        base += f" +{_fmt(abs(upper_tol))}"
    elif lower_tol is not None:
        base += f" -{_fmt(abs(lower_tol))}"
    return base


def _label_entry(label) -> str:
    """Return the stable TDF entry path for one XCAF source label."""
    entry = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, entry)
    return str(entry.ToCString())


def _source_id(category: PmiSourceCategory, label) -> str:
    return f"{category}:{_label_entry(label)}"


def _dimension_without_record(source_id: str, type_code: int) -> PmiSourceEntity | None:
    """Classify dimension labels that deliberately produce no extracted record."""
    if type_code in _PRESENTATION_TYPES:
        return PmiSourceEntity(
            source_id=source_id,
            category="dimension",
            type_code=type_code,
            outcome="presentation_only",
            reason="graphical presentation is not a semantic requirement",
        )
    if type_code == 30:
        return PmiSourceEntity(
            source_id=source_id,
            category="dimension",
            type_code=type_code,
            outcome="not_extracted",
            reason="common-label dimension extraction is not implemented",
        )
    return None


def _failure_reason(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _reference_geometry_with_groups(label, shape_tool):
    """Measure the geometry relationships shared by dimensions and tolerances."""
    first_refs = TDF_LabelSequence()
    second_refs = TDF_LabelSequence()
    XCAFDoc_DimTolTool.GetRefShapeLabel_s(label, first_refs, second_refs)
    points: list[tuple[float, float, float]] = []
    boxes: list[tuple[float, float, float, float, float, float]] = []
    group_stations: list[tuple[float, float, float] | None] = []
    partial_reasons: list[str] = []
    reference_count = first_refs.Length() + second_refs.Length()
    for refs in (first_refs, second_refs):
        group_boxes: list[tuple[float, float, float, float, float, float]] = []
        for index in range(1, refs.Length() + 1):
            shape = shape_tool.GetShape_s(refs.Value(index))
            if shape is None or shape.IsNull():
                partial_reasons.append("one referenced shape is unavailable")
                continue
            try:
                bbox = _shape_bbox(shape)
                boxes.append(bbox)
                point = _bbox_centroid(bbox)
                points.append(point)
                group_boxes.append(bbox)
            except Exception as exc:
                partial_reasons.append(
                    f"one referenced shape could not be measured ({_failure_reason(exc)})"
                )
        # One logical reference group may be split into any number of topology items. The
        # merged-envelope centre is invariant to that segmentation; averaging item centres
        # would move merely because one face was split into two (#1209).
        group_stations.append(_bbox_centroid(_merge_bboxes(group_boxes)) if group_boxes else None)
    if reference_count == 0:
        partial_reasons.append("referenced geometry is unavailable")

    ref_bbox = _merge_bboxes(boxes) if boxes else None
    dominant_axis = _dominant_from_bbox(ref_bbox) if ref_bbox else "?"
    return (
        tuple(points),
        ref_bbox,
        dominant_axis,
        tuple(dict.fromkeys(partial_reasons)),
        tuple(group_stations),
    )


def _reference_geometry(label, shape_tool):
    """Compatible flattened geometry projection shared by non-dimensional PMI."""
    points, ref_bbox, dominant_axis, reasons, _groups = _reference_geometry_with_groups(
        label, shape_tool
    )
    return points, ref_bbox, dominant_axis, reasons


def _cylindrical_references(label, shape_tool):
    """Return canonical finite cylinders for a Size_Diameter relationship.

    A diameter may truthfully reference one lateral cylindrical face.  XCAF commonly
    repeats that same face in a logical group, so geometrically identical values coalesce;
    distinct cylinders (for example pattern members) remain distinct.  No bbox participates
    in the axis, radius, line, span, or internal/external decision (#1296).
    """
    first_refs = TDF_LabelSequence()
    second_refs = TDF_LabelSequence()
    XCAFDoc_DimTolTool.GetRefShapeLabel_s(label, first_refs, second_refs)
    references: list[CylindricalReference] = []
    reasons: list[str] = []
    source_count = first_refs.Length() + second_refs.Length()
    for refs in (first_refs, second_refs):
        for index in range(1, refs.Length() + 1):
            shape = shape_tool.GetShape_s(refs.Value(index))
            if shape is None or shape.IsNull():
                reasons.append("one diameter reference shape is unavailable")
                continue
            try:
                if shape.ShapeType() != TopAbs_FACE:
                    reasons.append("one diameter reference is not a face")
                    continue
                surface = BRepAdaptor_Surface(TopoDS.Face_s(shape))
                if surface.GetType() != GeomAbs_Cylinder:
                    reasons.append("one diameter reference face is not cylindrical")
                    continue
                orientation = shape.Orientation()
                sense: Literal["external", "internal"]
                if orientation == TopAbs_FORWARD:
                    sense = "external"
                elif orientation == TopAbs_REVERSED:
                    sense = "internal"
                else:
                    reasons.append("one cylindrical reference has unsupported face orientation")
                    continue
                cylinder = surface.Cylinder()
                axis = cylinder.Axis()
                point = axis.Location()
                direction = axis.Direction()
                references.append(
                    CylindricalReference.canonical(
                        axis_point=(point.X(), point.Y(), point.Z()),
                        axis_direction=(direction.X(), direction.Y(), direction.Z()),
                        radius=float(cylinder.Radius()),
                        local_interval=(
                            float(surface.FirstVParameter()),
                            float(surface.LastVParameter()),
                        ),
                        sense=sense,
                    )
                )
            except Exception as exc:
                reasons.append(
                    f"one cylindrical reference could not be measured ({_failure_reason(exc)})"
                )
    if source_count == 0:
        reasons.append("diameter reference geometry is unavailable")

    # XCAF emits repeated labels for one logical cylindrical face in several benchmark
    # files. Use a precision finer than generated-Sheet output to collapse only values that
    # are genuinely the same topology, never nearby equal-diameter members.
    unique: dict[tuple, CylindricalReference] = {}
    for reference in references:
        signature = (
            *(round(value, 9) for value in reference.axis_origin),
            *(round(value, 9) for value in reference.axis_direction),
            round(reference.radius, 9),
            *(round(value, 9) for value in reference.axial_interval),
            reference.sense,
        )
        unique.setdefault(signature, reference)
    return tuple(unique.values()), tuple(dict.fromkeys(reasons))


def _cylindrical_references_from_shapes(shapes, *, noun: str):
    """Measure exact imported faces already resolved from Part21 identities."""
    references: list[CylindricalReference] = []
    reasons: list[str] = []
    for shape in shapes:
        try:
            if shape.ShapeType() != TopAbs_FACE:
                reasons.append(f"one {noun} reference is not a face")
                continue
            surface = BRepAdaptor_Surface(TopoDS.Face_s(shape))
            if surface.GetType() != GeomAbs_Cylinder:
                reasons.append(f"one {noun} reference face is not cylindrical")
                continue
            orientation = shape.Orientation()
            sense: Literal["external", "internal"]
            if orientation == TopAbs_FORWARD:
                sense = "external"
            elif orientation == TopAbs_REVERSED:
                sense = "internal"
            else:
                reasons.append(f"one {noun} cylindrical reference has unsupported orientation")
                continue
            cylinder = surface.Cylinder()
            axis = cylinder.Axis()
            point = axis.Location()
            direction = axis.Direction()
            references.append(
                CylindricalReference.canonical(
                    axis_point=(point.X(), point.Y(), point.Z()),
                    axis_direction=(direction.X(), direction.Y(), direction.Z()),
                    radius=float(cylinder.Radius()),
                    local_interval=(
                        float(surface.FirstVParameter()),
                        float(surface.LastVParameter()),
                    ),
                    sense=sense,
                )
            )
        except Exception as exc:
            reasons.append(f"one {noun} reference could not be measured ({_failure_reason(exc)})")
    if not references and not reasons:
        reasons.append(f"{noun} reference geometry is unavailable")
    return tuple(references), tuple(dict.fromkeys(reasons))


def _diameter_reference_blockers(
    references: tuple[CylindricalReference, ...], nominal: float, reasons: tuple[str, ...]
) -> tuple[str, ...]:
    """Facts that make a Size_Diameter relationship unsafe to draw or correlate."""
    blockers = list(reasons)
    if not references:
        if not blockers:
            blockers.append("diameter dimension needs a measurable cylindrical-face reference")
        return tuple(dict.fromkeys(blockers))
    axes = {reference.principal_axis for reference in references}
    if "?" in axes:
        blockers.append("diameter cylindrical-reference axis is not principal-axis aligned")
    elif len(axes) != 1:
        blockers.append("diameter references do not share one cylinder axis direction")
    senses = {reference.sense for reference in references}
    if len(senses) != 1:
        blockers.append("diameter references mix internal and external cylindrical faces")
    value_tol = max(0.01, abs(nominal) * 5e-4)
    mismatches = [
        reference.diameter
        for reference in references
        if not math.isclose(reference.diameter, nominal, rel_tol=0.0, abs_tol=value_tol)
    ]
    if mismatches:
        values = ", ".join(f"{value:.6g}" for value in mismatches)
        blockers.append(
            f"cylindrical reference diameter(s) {values} mm differ from nominal {nominal:.6g} mm"
        )
    return tuple(dict.fromkeys(blockers))


def _datum_geometry_from_shapes(shapes):
    """Measure exact datum faces and require one compatible axis-aligned surface."""
    points: list[tuple[float, float, float]] = []
    boxes: list[tuple[float, float, float, float, float, float]] = []
    axes: list[str] = []
    surface_kinds: list[str] = []
    supports: list[tuple[float, ...]] = []
    reasons: list[str] = []
    for shape in shapes:
        if shape is None or shape.IsNull():
            reasons.append("one referenced shape is unavailable")
            continue
        try:
            bbox = _shape_bbox(shape)
            boxes.append(bbox)
            points.append(_bbox_centroid(bbox))
            surface = BRepAdaptor_Surface(TopoDS.Face_s(shape))
            surface_type = surface.GetType()
            if surface_type == GeomAbs_Plane:
                kind = "plane"
                geometry = surface.Plane()
            elif surface_type == GeomAbs_Cylinder:
                kind = "cylinder"
                geometry = surface.Cylinder()
            else:
                reasons.append("one datum reference is neither planar nor cylindrical")
                continue
            axis = geometry.Axis()
            direction = axis.Direction()
            components = (abs(direction.X()), abs(direction.Y()), abs(direction.Z()))
            axis_index = max(range(3), key=components.__getitem__)
            if (
                components[axis_index] < 1e-6
                or sum(components) - components[axis_index] > 0.1 * components[axis_index]
            ):
                reasons.append("one datum reference surface is not axis-aligned")
                continue
            location = axis.Location()
            coordinates = (location.X(), location.Y(), location.Z())
            if kind == "plane":
                support = (coordinates[axis_index],)
            else:
                support = tuple(
                    value for index, value in enumerate(coordinates) if index != axis_index
                )
            axes.append("XYZ"[axis_index])
            surface_kinds.append(kind)
            supports.append(support)
        except Exception as exc:
            reasons.append(f"one datum reference surface is unavailable ({_failure_reason(exc)})")
    if not shapes:
        reasons.append("referenced geometry is unavailable")

    ref_bbox = _merge_bboxes(boxes) if boxes else None
    if not axes:
        reasons.append("datum reference surface is unavailable")
        reference_axis = ""
    elif len(set(surface_kinds)) != 1:
        reasons.append("datum reference faces mix planar and cylindrical surfaces")
        reference_axis = ""
    elif len(set(axes)) != 1 or any(
        any(abs(value - supports[0][index]) > 1e-4 for index, value in enumerate(support))
        for support in supports[1:]
    ):
        qualifier = "coplanar" if surface_kinds[0] == "plane" else "coaxial"
        reasons.append(f"datum reference faces are not {qualifier}")
        reference_axis = ""
    else:
        reference_axis = axes[0]
    return tuple(points), ref_bbox, reference_axis, tuple(dict.fromkeys(reasons))


def _datum_reference_geometry(label, shape_tool):
    """Measure datum faces reached through the direct XCAF relationship."""
    first_refs = TDF_LabelSequence()
    second_refs = TDF_LabelSequence()
    XCAFDoc_DimTolTool.GetRefShapeLabel_s(label, first_refs, second_refs)
    shapes = []
    for refs in (first_refs, second_refs):
        for index in range(1, refs.Length() + 1):
            shapes.append(shape_tool.GetShape_s(refs.Value(index)))
    return _datum_geometry_from_shapes(shapes)


class _DatumTopologyResolver:
    """Resolve Part21 face labels through OCCT's native transfer binders.

    ``TransferOne(rank)`` stays inside C++, avoiding the OCP wrapper's loss of pointer
    identity when a STEP entity is returned to Python.  It reuses the existing transfer
    binder.  ``FindIndex`` then proves that result is ``IsSame`` to a face in the original
    imported topology; no geometric comparison participates in correspondence.
    """

    def __init__(self, step_reader, imported_faces):
        self._reader = step_reader
        self._model = step_reader.StepModel()
        self._imported_faces = imported_faces
        self._items: dict[str, tuple[object, int]] = {}
        self._definitions: dict[str, tuple[object, ...]] = {}
        self._claims: dict[int, str] = {}

    def _transfer_face(self, item_id: str):
        cached = self._items.get(item_id)
        if cached is not None:
            return cached, ""
        rank = self._model.NextNumberForLabel(item_id, 0, True)
        if rank <= 0:
            return None, f"Part21 representation item {item_id} is unavailable"
        entity = self._model.Value(rank)
        if entity.DynamicType().Name() != "StepShape_AdvancedFace":
            return None, f"Part21 representation item {item_id} is not an advanced face"
        before = self._reader.NbShapes()
        try:
            transferred = self._reader.TransferOne(rank)
        except Exception as exc:
            return None, (
                f"Part21 representation item {item_id} could not be transferred "
                f"({_failure_reason(exc)})"
            )
        after = self._reader.NbShapes()
        if not transferred or after != before + 1:
            return None, f"Part21 representation item {item_id} could not be transferred"
        shape = self._reader.Shape(after)
        if shape is None or shape.IsNull() or shape.ShapeType() != TopAbs_FACE:
            return None, f"Part21 representation item {item_id} did not transfer to one face"
        face_index = self._imported_faces.FindIndex(shape)
        if face_index <= 0:
            return None, (
                f"Part21 representation item {item_id} is not a face in the imported topology"
            )
        result = (shape, face_index)
        self._items[item_id] = result
        return result, ""

    def resolve(
        self,
        definition_id: str,
        item_ids: tuple[str, ...],
        *,
        noun: str = "datum feature",
    ):
        """Return exact imported faces, or reasons that make the definition unsafe."""
        cached = self._definitions.get(definition_id)
        if cached is not None:
            return cached, ()
        if not item_ids:
            return (), (f"{noun} has no Part21 representation items",)

        resolved = []
        reasons: list[str] = []
        for item_id in item_ids:
            result, reason = self._transfer_face(item_id)
            if reason:
                reasons.append(reason)
            elif result is not None:
                resolved.append(result)
        if reasons:
            return (), tuple(dict.fromkeys(reasons))

        indices = [face_index for _shape, face_index in resolved]
        if len(set(indices)) != len(indices):
            return (), ("datum feature representation items resolve to the same imported face",)
        for face_index in indices:
            owner = self._claims.get(face_index)
            if owner is not None and owner != definition_id:
                reasons.append(f"one imported face is already claimed by {noun} {owner}")
        if reasons:
            return (), tuple(dict.fromkeys(reasons))

        shapes = tuple(shape for shape, _face_index in resolved)
        for face_index in indices:
            self._claims[face_index] = definition_id
        self._definitions[definition_id] = shapes
        return shapes, ()


def _datum_letter(label) -> tuple[str, str]:
    try:
        identification = XCAFDoc_Datum.Set_s(label).GetIdentification()
        letter = str(identification.ToCString()).strip() if identification is not None else ""
    except Exception as exc:
        return "", f"datum letter is unavailable ({_failure_reason(exc)})"
    return (letter, "") if letter else ("", "datum occurrence has no letter")


def _datum_context(label, dim_tol_tool) -> tuple[str, str]:
    tolerances = TDF_LabelSequence()
    try:
        dim_tol_tool.GetTolerOfDatumLabels(label, tolerances)
    except Exception as exc:
        return "", f"datum tolerance context is unavailable ({_failure_reason(exc)})"
    if tolerances.Length() != 1:
        return "", f"datum occurrence has {tolerances.Length()} tolerance contexts"
    try:
        tolerance = XCAFDoc_GeomTolerance.Set_s(tolerances.Value(1)).GetObject()
        name = tolerance.GetSemanticName()
        context = str(name.ToCString()).strip() if name is not None else ""
    except Exception as exc:
        return "", f"datum tolerance context is unavailable ({_failure_reason(exc)})"
    return (context, "") if context else ("", "datum occurrence has no tolerance context")


def _coalesce_datum_records(records: list[PmiRecord]) -> list[PmiRecord]:
    """Project occurrence records onto authored datum-feature definitions."""
    grouped: dict[str, list[PmiRecord]] = {}
    for record in records:
        grouped.setdefault(record.part21_id or record.source_id, []).append(record)

    projected: list[PmiRecord] = []
    for group in grouped.values():
        source_ids = tuple(record.source_id for record in group)
        letters = {record.label for record in group if record.label}
        item_ids = {record.reference_item_ids for record in group}
        geometry = next((record for record in group if record.ref_bbox is not None), group[0])
        blockers = list(geometry.lowering_blockers)
        if len(letters) != 1:
            blockers.append("datum feature occurrences disagree about the datum letter")
        if len(item_ids) != 1:
            blockers.append("datum feature occurrences disagree about referenced Part21 items")
        geometry_signatures = {
            (record.ref_bbox, record.reference_axis)
            for record in group
            if record.ref_bbox is not None
        }
        if len(geometry_signatures) > 1:
            blockers.append("datum feature occurrences disagree about referenced geometry")
        projected.append(
            PmiRecord(
                kind="datum",
                type_code=None,
                value=0.0,
                ref_pts=geometry.ref_pts,
                ref_bbox=geometry.ref_bbox,
                dominant_axis=geometry.reference_axis or "?",
                label=group[0].label,
                source_id=source_ids[0],
                part21_id=group[0].part21_id,
                source_category="datum",
                lowering_blockers=tuple(dict.fromkeys(blockers)),
                source_ids=source_ids,
                datum_contexts=tuple(
                    context for record in group for context in record.datum_contexts
                ),
                reference_item_ids=group[0].reference_item_ids,
                reference_axis=geometry.reference_axis,
            )
        )
    return projected


def _datum_references(label, dim_tol_tool) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the ordered datum letters attached to one tolerance label."""
    datum_labels = TDF_LabelSequence()
    try:
        dim_tol_tool.GetDatumOfTolerLabels_s(label, datum_labels)
    except Exception as exc:
        return (), (f"datum references are unavailable ({_failure_reason(exc)})",)

    datum_refs: list[str] = []
    partial_reasons: list[str] = []
    for index in range(1, datum_labels.Length() + 1):
        try:
            datum = XCAFDoc_Datum.Set_s(datum_labels.Value(index)).GetObject()
            name = datum.GetName()
            datum_ref = str(name.ToCString()).strip() if name is not None else ""
        except Exception as exc:
            partial_reasons.append(f"one datum reference is unavailable ({_failure_reason(exc)})")
            continue
        if datum_ref:
            datum_refs.append(datum_ref)
        else:
            partial_reasons.append("one datum reference has no letter")
    return tuple(datum_refs), tuple(dict.fromkeys(partial_reasons))


def _semantic_name(obj) -> tuple[str, str]:
    """Return the XCAF name used solely for evidence-gated Part21 correspondence."""
    try:
        semantic_name = obj.GetSemanticName()
        name = str(semantic_name.ToCString()).strip() if semantic_name is not None else ""
    except Exception as exc:
        return "", f"XCAF semantic name is unavailable ({_failure_reason(exc)})"
    if not name:
        return "", "XCAF geometric tolerance has no semantic name"
    return name, ""


def _geometric_tolerance_modifiers(obj) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Inventory XCAF's modifier sequence and admit representable scope symbols."""
    try:
        codes = tuple(int(modifier) for modifier in obj.GetModifiers())
    except Exception as exc:
        return (), (f"geometric-tolerance modifiers are unavailable ({_failure_reason(exc)})",)

    names: list[str] = []
    reasons: list[str] = []
    for code in codes:
        name = _GTOL_MODIFIER.get(code)
        if name is None:
            names.append(f"unknown({code})")
            reasons.append(f"geometric-tolerance modifier {code} is unknown")
            continue
        names.append(name)
        if name not in _SUPPORTED_GTOL_SCOPE_MODIFIERS:
            reasons.append(f"geometric-tolerance modifier {name!r} is not supported")

    if len(names) > 1:
        reasons.append(
            f"geometric-tolerance modifier combination {tuple(names)!r} is not supported"
        )
    return tuple(names), tuple(dict.fromkeys(reasons))


def _unpreserved_geometric_tolerance_fields(obj) -> tuple[str, ...]:
    """Keep a source partial when XCAF exposes requirement fields we do not yet carry."""
    reasons: list[str] = []
    enum_fields = (
        ("GetTypeOfValue", "type-of-value"),
        ("GetMaterialRequirementModifier", "material-requirement modifier"),
        ("GetZoneModifier", "zone modifier"),
    )
    for accessor, description in enum_fields:
        try:
            enum_value = int(getattr(obj, accessor)())
        except Exception as exc:
            reasons.append(
                f"geometric-tolerance {description} is unavailable ({_failure_reason(exc)})"
            )
        else:
            if enum_value != 0:
                reasons.append(f"geometric-tolerance {description} {enum_value} is not preserved")

    float_fields = (
        ("GetValueOfZoneModifier", "zone-modifier value"),
        ("GetMaxValueModifier", "maximum-value modifier"),
    )
    for accessor, description in float_fields:
        try:
            numeric_value = float(getattr(obj, accessor)())
        except Exception as exc:
            reasons.append(
                f"geometric-tolerance {description} is unavailable ({_failure_reason(exc)})"
            )
        else:
            if abs(numeric_value) > 1e-9:
                reasons.append(
                    f"geometric-tolerance {description} {numeric_value:g} is not preserved"
                )

    return tuple(reasons)


def _dimension_record(
    label, obj, type_code: int, shape_tool, source_id: str
) -> tuple[PmiRecord, tuple[str, ...]]:
    """Convert one semantic XCAF dimension label, allowing its caller to record failures."""
    partial_reasons = []
    # Nominal value: scalar first, array fallback.
    value = 0.0
    try:
        value = float(obj.GetValue())
    except Exception:
        try:
            values = obj.GetValues()
            if values is not None:
                value = float(values.Value(values.Lower()))
            else:
                partial_reasons.append("nominal value is unavailable")
        except Exception as exc:
            partial_reasons.append(f"nominal value is unavailable ({_failure_reason(exc)})")

    upper_tol: float | None = None
    lower_tol: float | None = None
    try:
        candidate = float(obj.GetUpperTolValue())
        if abs(candidate) > 1e-9:
            upper_tol = candidate
    except Exception:
        pass

    try:
        candidate = float(obj.GetLowerTolValue())
        if abs(candidate) > 1e-9:
            lower_tol = candidate
    except Exception:
        pass

    lower_bound: float | None = None
    upper_bound: float | None = None
    if obj.IsDimWithRange():
        try:
            lower_bound = float(obj.GetLowerBound())
        except Exception as exc:
            partial_reasons.append(f"lower range bound is unavailable ({_failure_reason(exc)})")
        try:
            upper_bound = float(obj.GetUpperBound())
        except Exception as exc:
            partial_reasons.append(f"upper range bound is unavailable ({_failure_reason(exc)})")

    points, ref_bbox, dominant_axis, reference_reasons, group_stations = (
        _reference_geometry_with_groups(label, shape_tool)
    )
    partial_reasons.extend(reference_reasons)
    kind = _DIM_TYPE.get(type_code, f"type{type_code}")
    rendering_blockers: tuple[str, ...] = ()
    cylindrical_refs: tuple[CylindricalReference, ...] = ()
    if kind in ("linear", "thickness"):
        points, dominant_axis, station_reasons = _linear_reference_stations(group_stations, value)
        if kind == "thickness":
            station_reasons = tuple(
                reason.replace("linear dimension", "thickness dimension").replace(
                    "linear reference", "thickness reference"
                )
                for reason in station_reasons
            )
        rendering_blockers = _dimension_geometry_blockers(kind, reference_reasons, station_reasons)
    elif type_code == 15:  # XCAFDimTolObjects_DimensionType_Size_Diameter
        cylindrical_refs, cylinder_reasons = _cylindrical_references(label, shape_tool)
        # The generic XCAF relationship already owns missing-reference failures.  Do not
        # restate the same absent shape as a diameter-specific extraction failure; that
        # would turn one partial outcome into several aliases.  Once a cylinder was
        # recovered, its topology is self-sufficient and bbox measurement failures are
        # irrelevant to rendering/correlation.
        effective_reasons = cylinder_reasons
        if not cylindrical_refs and reference_reasons:
            effective_reasons = reference_reasons
        else:
            partial_reasons.extend(cylinder_reasons)
        rendering_blockers = _diameter_reference_blockers(
            cylindrical_refs, value, effective_reasons
        )
        if cylindrical_refs:
            points = tuple(reference.midpoint for reference in cylindrical_refs)
            axes = {reference.principal_axis for reference in cylindrical_refs}
            dominant_axis = next(iter(axes)) if len(axes) == 1 and "?" not in axes else "?"
    lowering_blockers = tuple(dict.fromkeys(partial_reasons))
    blockers = tuple(dict.fromkeys((*lowering_blockers, *rendering_blockers)))
    return (
        PmiRecord(
            kind=kind,
            type_code=type_code,
            value=value,
            upper_tol=upper_tol,
            lower_tol=lower_tol,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            ref_pts=tuple(points),
            ref_bbox=ref_bbox,
            dominant_axis=dominant_axis,
            label=_make_label(
                kind,
                value,
                upper_tol,
                lower_tol,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
            ),
            source_id=source_id,
            source_category="dimension",
            lowering_blockers=lowering_blockers,
            rendering_blockers=rendering_blockers,
            cylindrical_refs=cylindrical_refs,
        ),
        blockers,
    )


def _geometric_tolerance_record(
    label,
    obj,
    type_code: int,
    shape_tool,
    dim_tol_tool,
    source_id: str,
    part21_facts: tuple[GeometricToleranceFact, ...] = (),
    part21_error: str = "",
) -> tuple[PmiRecord, tuple[str, ...]]:
    """Convert the XCAF-owned fields of one geometric tolerance."""
    value = float(obj.GetValue())
    kind = _GTOL_TYPE.get(type_code, f"gtol{type_code}")
    partial_reasons: list[str] = []
    part21_id = ""
    if type_code not in _GTOL_TYPE:
        partial_reasons.append(f"geometric-tolerance type {type_code} is unsupported")
    if value <= 0:
        if part21_error:
            magnitude_reason = part21_error
        else:
            semantic_name, name_reason = _semantic_name(obj)
            if name_reason:
                magnitude_reason = name_reason
            else:
                fact, magnitude_reason = match_geometric_tolerance(
                    part21_facts, semantic_name, kind
                )
                if fact is not None:
                    part21_id = fact.entity_id
                    if not magnitude_reason and fact.value_mm is not None:
                        value = fact.value_mm
        if value <= 0:
            partial_reasons.append(f"tolerance magnitude is unavailable ({magnitude_reason})")
    gtol_modifiers, modifier_reasons = _geometric_tolerance_modifiers(obj)
    partial_reasons.extend(modifier_reasons)
    partial_reasons.extend(_unpreserved_geometric_tolerance_fields(obj))
    points, ref_bbox, dominant_axis, reference_reasons = _reference_geometry(label, shape_tool)
    partial_reasons.extend(reference_reasons)
    datum_refs, datum_reasons = _datum_references(label, dim_tol_tool)
    partial_reasons.extend(datum_reasons)
    lowering_blockers = tuple(dict.fromkeys(partial_reasons))
    return (
        PmiRecord(
            kind=kind,
            type_code=type_code,
            value=value,
            ref_pts=points,
            ref_bbox=ref_bbox,
            dominant_axis=dominant_axis,
            label=f"{kind} {value:.3g}" if value > 0 else kind,
            source_id=source_id,
            datum_refs=datum_refs,
            part21_id=part21_id,
            source_category="geometric_tolerance",
            gtol_modifiers=gtol_modifiers,
            lowering_blockers=lowering_blockers,
        ),
        lowering_blockers,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _manufacturing_requirement_projection(
    step_file: str | Path,
) -> tuple[tuple[PmiSourceEntity, ...], tuple[PmiRecord, ...]]:
    """Build the Part21-only source/record projection independently of XCAF availability."""
    sources: list[PmiSourceEntity] = []
    records: list[PmiRecord] = []
    try:
        requirement_facts = read_manufacturing_requirements(step_file)
    except Exception as exc:
        reason = f"Part21 manufacturing-requirement read failed: {_failure_reason(exc)}"
        sources.append(
            PmiSourceEntity(
                source_id="manufacturing_requirement:part21",
                category="manufacturing_requirement",
                type_code=None,
                outcome="not_extracted",
                reason=reason,
            )
        )
        _log.debug("PMI manufacturing-requirement inventory unavailable: %s", exc)
        return tuple(sources), ()

    for requirement in requirement_facts:
        source_id = f"manufacturing_requirement:{requirement.entity_id}"
        if requirement.text:
            kind = (
                "_".join(requirement.semantic_name.lower().split()) or "manufacturing_requirement"
            )
            blockers = (requirement.reason,) if requirement.reason else ()
            records.append(
                PmiRecord(
                    kind=kind,
                    type_code=None,
                    value=0.0,
                    label=requirement.text,
                    source_id=source_id,
                    part21_id=requirement.entity_id,
                    source_category="manufacturing_requirement",
                    lowering_blockers=blockers,
                    reference_item_ids=requirement.reference_item_ids,
                    semantic_name=requirement.semantic_name,
                    shape_aspect_ids=requirement.shape_aspect_ids,
                )
            )
        sources.append(
            PmiSourceEntity(
                source_id=source_id,
                category="manufacturing_requirement",
                type_code=None,
                outcome=(
                    "partially_extracted"
                    if requirement.text and requirement.reason
                    else "extracted"
                    if requirement.text
                    else "not_extracted"
                ),
                reason=requirement.reason,
            )
        )
    return tuple(sources), tuple(records)


_CYLINDRICAL_REQUIREMENT_KINDS = frozenset(("external_thread", "internal_thread", "knurl"))


def _manufacturing_requirement_topology(records, step_reader) -> tuple[PmiRecord, ...]:
    """Attach exact finite-cylinder evidence to supported manufacturing records."""
    imported_faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(step_reader.OneShape(), TopAbs_FACE, imported_faces)
    resolver = _DatumTopologyResolver(step_reader, imported_faces)
    projected = []
    for record in records:
        if record.kind not in _CYLINDRICAL_REQUIREMENT_KINDS or record.lowering_blockers:
            projected.append(record)
            continue
        shapes, topology_reasons = resolver.resolve(
            record.part21_id,
            record.reference_item_ids,
            noun="manufacturing requirement",
        )
        references, geometry_reasons = _cylindrical_references_from_shapes(
            shapes,
            noun="manufacturing requirement",
        )
        blockers = tuple(
            dict.fromkeys((*record.lowering_blockers, *topology_reasons, *geometry_reasons))
        )
        projected.append(replace(record, cylindrical_refs=references, lowering_blockers=blockers))
    return tuple(projected)


def extract_pmi_report(step_file: str | Path) -> PmiExtractionReport:
    """Inventory and extract semantic PMI from an AP242 STEP file in one XCAF pass.

    The report retains one source outcome for every dimension, geometric tolerance,
    datum-reference occurrence and semantic manufacturing requirement. Graphical
    presentation-only dimension labels are inventoried but are not manufacturing requirements.
    Repeated datum occurrences project onto their authored datum-feature definition without
    shrinking the source denominator.

    Returns an empty report (with a report-level error where applicable) when no source
    identities can be recovered and:

    - the file contains neither XCAF GDT data nor semantic manufacturing requirements;
    - the file uses AP203/AP214 which carry no semantic PMI.

    Part21-only manufacturing requirements remain inventoried even when OCP's GDT support is
    unavailable or the XCAF transfer fails; the report also retains that global XCAF error.

    Does **not** modify the solid geometry — purely a read-only second pass.
    """
    requirement_sources, requirement_records = _manufacturing_requirement_projection(step_file)

    def failed(reason: str) -> PmiExtractionReport:
        return PmiExtractionReport(
            sources=requirement_sources,
            records=requirement_records,
            error=reason,
        )

    if not _PMI_AVAILABLE:
        reason = "OCP SetGDTMode is unavailable"
        _log.debug("PMI extraction unavailable (%s)", reason)
        return failed(reason)

    path = str(step_file)
    doc = TDocStd_Document(TCollection_ExtendedString("XCAF"))
    reader = STEPCAFControl_Reader()
    reader.SetGDTMode(True)
    reader.SetNameMode(True)
    try:
        status = reader.ReadFile(path)
    except Exception as exc:
        reason = f"ReadFile failed: {_failure_reason(exc)}"
        _log.warning("PMI extraction: %s for %s", reason, Path(step_file).name)
        return failed(reason)
    if status != IFSelect_RetDone:
        reason = f"ReadFile failed with status {status}"
        _log.warning("PMI extraction: %s for %s", reason, Path(step_file).name)
        return failed(reason)
    try:
        transferred = reader.Transfer(doc)
    except Exception as exc:
        reason = f"Transfer failed: {_failure_reason(exc)}"
        _log.warning("PMI extraction: %s for %s", reason, Path(step_file).name)
        return failed(reason)
    if transferred is False:
        reason = "Transfer failed"
        _log.warning("PMI extraction: %s for %s", reason, Path(step_file).name)
        return failed(reason)

    main = doc.Main()
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(main)
    dt = XCAFDoc_DocumentTool.DimTolTool_s(main)

    try:
        requirement_records = _manufacturing_requirement_topology(
            requirement_records,
            reader.Reader(),
        )
    except Exception as exc:
        reason = f"manufacturing requirement topology is unavailable ({_failure_reason(exc)})"
        requirement_records = tuple(
            replace(
                record,
                lowering_blockers=tuple(dict.fromkeys((*record.lowering_blockers, reason))),
            )
            if record.kind in _CYLINDRICAL_REQUIREMENT_KINDS
            else record
            for record in requirement_records
        )

    records: list[PmiRecord] = []
    sources: list[PmiSourceEntity] = []

    # ---- Dimensions --------------------------------------------------------
    dims = TDF_LabelSequence()
    dt.GetDimensionLabels(dims)
    for index in range(1, dims.Length() + 1):
        label = dims.Value(index)
        source_id = _source_id("dimension", label)
        type_code: int | None = None
        try:
            obj = XCAFDoc_Dimension.Set_s(label).GetObject()
            type_code = int(obj.GetType())
            without_record = _dimension_without_record(source_id, type_code)
            if without_record is not None:
                sources.append(without_record)
                continue
            record, partial_reasons = _dimension_record(
                label, obj, type_code, shape_tool, source_id
            )
        except Exception as exc:
            sources.append(
                PmiSourceEntity(
                    source_id=source_id,
                    category="dimension",
                    type_code=type_code,
                    outcome="not_extracted",
                    reason=_failure_reason(exc),
                )
            )
            _log.debug("PMI %s not extracted: %s", source_id, exc)
        else:
            records.append(record)
            sources.append(
                PmiSourceEntity(
                    source_id,
                    "dimension",
                    type_code,
                    "partially_extracted" if partial_reasons else "extracted",
                    "; ".join(partial_reasons),
                )
            )

    # ---- Geometric tolerances ----------------------------------------------
    tolerances = TDF_LabelSequence()
    dt.GetGeomToleranceLabels(tolerances)
    part21_facts: tuple[GeometricToleranceFact, ...] = ()
    part21_error = ""
    if tolerances.Length() > 0:
        try:
            part21_facts = read_geometric_tolerances(step_file)
        except Exception as exc:
            part21_error = f"Part21 read failed: {_failure_reason(exc)}"
            _log.debug("PMI Part21 overlay unavailable for %s: %s", Path(step_file).name, exc)
    for index in range(1, tolerances.Length() + 1):
        label = tolerances.Value(index)
        source_id = _source_id("geometric_tolerance", label)
        tolerance_type_code: int | None = None
        try:
            obj = XCAFDoc_GeomTolerance.Set_s(label).GetObject()
            tolerance_type_code = int(obj.GetType())
            record, partial_reasons = _geometric_tolerance_record(
                label,
                obj,
                tolerance_type_code,
                shape_tool,
                dt,
                source_id,
                part21_facts,
                part21_error,
            )
        except Exception as exc:
            sources.append(
                PmiSourceEntity(
                    source_id=source_id,
                    category="geometric_tolerance",
                    type_code=tolerance_type_code,
                    outcome="not_extracted",
                    reason=_failure_reason(exc),
                )
            )
            _log.debug("PMI %s not extracted: %s", source_id, exc)
        else:
            records.append(record)
            sources.append(
                PmiSourceEntity(
                    source_id,
                    "geometric_tolerance",
                    tolerance_type_code,
                    "partially_extracted" if partial_reasons else "extracted",
                    "; ".join(partial_reasons),
                )
            )

    # ---- Datums ------------------------------------------------------------
    datums = TDF_LabelSequence()
    dt.GetDatumLabels(datums)
    datum_facts: tuple[DatumOccurrenceFact, ...] = ()
    datum_part21_error = ""
    datum_topology = None
    datum_topology_error = ""
    if datums.Length() > 0:
        try:
            datum_facts = read_datum_occurrences(step_file)
        except Exception as exc:
            datum_part21_error = f"Part21 datum read failed: {_failure_reason(exc)}"
            _log.debug("PMI datum overlay unavailable for %s: %s", Path(step_file).name, exc)
        if not datum_part21_error:
            try:
                step_reader = reader.Reader()
                imported_faces = TopTools_IndexedMapOfShape()
                TopExp.MapShapes_s(step_reader.OneShape(), TopAbs_FACE, imported_faces)
                datum_topology = _DatumTopologyResolver(step_reader, imported_faces)
            except Exception as exc:
                datum_topology_error = (
                    f"datum imported-topology map is unavailable ({_failure_reason(exc)})"
                )
    datum_records: list[PmiRecord] = []
    for index in range(1, datums.Length() + 1):
        label = datums.Value(index)
        source_id = _source_id("datum", label)
        try:
            letter, letter_reason = _datum_letter(label)
            context, context_reason = _datum_context(label, dt)
            fact: DatumOccurrenceFact | None = None
            correspondence_reason = datum_part21_error
            if not correspondence_reason and not letter_reason and not context_reason:
                fact, correspondence_reason = match_datum_occurrence(datum_facts, context, letter)
            points, ref_bbox, reference_axis, geometry_reasons = _datum_reference_geometry(
                label, shape_tool
            )
            if fact is not None and fact.reference_item_ids:
                if datum_topology is None:
                    topology_shapes = ()
                    topology_reasons = (datum_topology_error or datum_part21_error,)
                else:
                    topology_shapes, topology_reasons = datum_topology.resolve(
                        fact.datum_feature_id, fact.reference_item_ids
                    )
                if topology_shapes:
                    points, ref_bbox, reference_axis, geometry_reasons = (
                        _datum_geometry_from_shapes(topology_shapes)
                    )
                else:
                    geometry_reasons = tuple(dict.fromkeys((*geometry_reasons, *topology_reasons)))
            blockers = tuple(
                dict.fromkeys(
                    reason
                    for reason in (
                        letter_reason,
                        context_reason,
                        correspondence_reason,
                        *geometry_reasons,
                    )
                    if reason
                )
            )
            datum_records.append(
                PmiRecord(
                    kind="datum",
                    type_code=None,
                    value=0.0,
                    ref_pts=points,
                    ref_bbox=ref_bbox,
                    dominant_axis=reference_axis or "?",
                    label=letter,
                    source_id=source_id,
                    part21_id=fact.datum_feature_id if fact is not None else "",
                    source_category="datum",
                    lowering_blockers=blockers,
                    source_ids=(source_id,),
                    datum_contexts=(context,) if context else (),
                    reference_item_ids=fact.reference_item_ids if fact is not None else (),
                    reference_axis=reference_axis,
                )
            )
        except Exception as exc:
            sources.append(
                PmiSourceEntity(
                    source_id=source_id,
                    category="datum",
                    type_code=None,
                    outcome="not_extracted",
                    reason=_failure_reason(exc),
                )
            )
            _log.debug("PMI %s not extracted: %s", source_id, exc)
        else:
            sources.append(
                PmiSourceEntity(
                    source_id,
                    "datum",
                    None,
                    "partially_extracted" if blockers else "extracted",
                    "; ".join(blockers),
                )
            )
    records.extend(_coalesce_datum_records(datum_records))

    # XCAF exposes neither authoritative descriptive text nor its shape-aspect association.
    # The independent Part21 projection was collected before XCAF so it survives every early
    # transfer failure; append it after XCAF categories to keep the established report order.
    sources.extend(requirement_sources)
    records.extend(requirement_records)

    semantic_dimensions = sum(
        source.category == "dimension" and source.outcome != "presentation_only"
        for source in sources
    )
    extracted_dimensions = sum(
        source.category == "dimension" and source.outcome == "extracted" for source in sources
    )
    partial_dimensions = sum(
        source.category == "dimension" and source.outcome == "partially_extracted"
        for source in sources
    )
    presentation_dimensions = sum(
        source.category == "dimension" and source.outcome == "presentation_only"
        for source in sources
    )
    partial_tolerances = sum(
        source.category == "geometric_tolerance" and source.outcome == "partially_extracted"
        for source in sources
    )
    extracted_tolerances = sum(
        source.category == "geometric_tolerance" and source.outcome == "extracted"
        for source in sources
    )
    extracted_datums = sum(
        source.category == "datum" and source.outcome == "extracted" for source in sources
    )
    partial_datums = sum(
        source.category == "datum" and source.outcome == "partially_extracted"
        for source in sources
    )
    extracted_requirements = sum(
        source.category == "manufacturing_requirement" and source.outcome == "extracted"
        for source in sources
    )
    partial_requirements = sum(
        source.category == "manufacturing_requirement" and source.outcome == "partially_extracted"
        for source in sources
    )
    requirement_source_count = sum(
        source.category == "manufacturing_requirement" for source in sources
    )

    _log.info(
        "PMI extracted from %s: %d/%d complete semantic dims (%d partial, "
        "%d presentation-only), "
        "%d/%d complete gtols (%d partial), %d/%d datum occurrences (%d partial), "
        "%d/%d manufacturing requirements (%d partial)",
        Path(step_file).name,
        extracted_dimensions,
        semantic_dimensions,
        partial_dimensions,
        presentation_dimensions,
        extracted_tolerances,
        tolerances.Length(),
        partial_tolerances,
        extracted_datums,
        datums.Length(),
        partial_datums,
        extracted_requirements,
        requirement_source_count,
        partial_requirements,
    )
    return PmiExtractionReport(sources=tuple(sources), records=tuple(records))


def extract_pmi(step_file: str | Path) -> list[PmiRecord]:
    """Return the successful-record projection of :func:`extract_pmi_report`.

    This compatibility surface deliberately remains a list. Callers that need to know what
    the source contained or why a record is absent must use :func:`extract_pmi_report`.
    """
    return list(extract_pmi_report(step_file).records)
