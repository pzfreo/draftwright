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

import hashlib
import logging
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast

from quiddity import PartFrame

from draftwright._pmi_linear_geometry import (
    _LINEAR_AXIS_ABS_TOL as _LINEAR_AXIS_ABS_TOL,
)
from draftwright._pmi_linear_geometry import (
    _LINEAR_AXIS_REL_TOL as _LINEAR_AXIS_REL_TOL,
)
from draftwright._pmi_linear_geometry import (
    _LINEAR_OBLIQUE_VALUE_ABS_TOL as _LINEAR_OBLIQUE_VALUE_ABS_TOL,
)
from draftwright._pmi_linear_geometry import (
    _LINEAR_VALUE_ABS_TOL as _LINEAR_VALUE_ABS_TOL,
)
from draftwright._pmi_linear_geometry import (
    _LINEAR_VALUE_REL_TOL as _LINEAR_VALUE_REL_TOL,
)
from draftwright._pmi_linear_geometry import (
    _linear_reference_stations,
)
from draftwright._pmi_part21 import (
    CommonLabelFact,
    DatumDefinitionFact,
    DatumOccurrenceFact,
    DimensionAssociationFact,
    DimensionDisplayFact,
    GeometricToleranceFact,
    MaterialFact,
    match_common_label,
    match_datum_occurrence,
    match_dimension_association,
    match_dimension_display,
    match_geometric_tolerance,
    part21_read_session,
    read_common_labels,
    read_datum_definitions,
    read_datum_occurrences,
    read_dimension_associations,
    read_dimension_display_facts,
    read_dimension_length_factor,
    read_geometric_tolerances,
    read_manufacturing_requirements,
    read_material_properties,
    read_surface_labels,
)
from draftwright._pmi_schema import (
    _DIM_PREFIX,
    _DIM_TYPE,
    _GTOL_TYPE,
    _LENGTH_DIMENSION_KINDS,
    _PRESENTATION_TYPES,
)
from draftwright._pmi_schema import (
    _GTOL_MATERIAL_REQUIREMENT as _GTOL_MATERIAL_REQUIREMENT,
)
from draftwright._pmi_schema import (
    _GTOL_MODIFIER as _GTOL_MODIFIER,
)
from draftwright._pmi_schema import (
    _GTOL_TYPE_OF_VALUE as _GTOL_TYPE_OF_VALUE,
)
from draftwright._pmi_schema import (
    _SUPPORTED_GTOL_SCOPE_MODIFIERS as _SUPPORTED_GTOL_SCOPE_MODIFIERS,
)
from draftwright._pmi_support_blockers import (
    _dimension_geometry_blockers,
    _failure_reason,
    _geometric_tolerance_modifiers,
    _geometric_tolerance_qualifiers,
    _is_direct_xcaf_angular_failure,
    _is_direct_xcaf_diameter_failure,
    _is_direct_xcaf_reference_failure,
    _unpreserved_geometric_tolerance_fields,
    _without_direct_xcaf_angular_failures,
    _without_direct_xcaf_diameter_failures,
    _without_direct_xcaf_reference_failures,
)
from draftwright._pmi_topology import (
    _CommonLabelTopologyResolver,
    _DatumTopologyResolver,
    _DimensionSupportResolver,
    _SurfaceLabelTopologyResolver,
)
from draftwright.model.ir import (
    AngularReference,
    CircularReference,
    CylindricalReference,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

PmiSourceCategory = Literal[
    "dimension",
    "geometric_tolerance",
    "datum",
    "manufacturing_requirement",
    "surface_label",
]


@dataclass(frozen=True)
class PmiRecord:
    """One semantic PMI annotation from an AP242 STEP file.

    Attributes:
        kind:           Human-readable category (``"linear"``, ``"diameter"``,
                        ``"angular"``, ``"gtol"``, ``"datum"``, ``"surface_label"``,
                        ``"external_thread"``).
        type_code:      Raw OCCT enum integer.
        value:          Nominal value in mm (or degrees for angular).
        upper_tol:      Upper tolerance in mm, or ``None``.
        lower_tol:      Lower tolerance in mm, or ``None``.
        lower_bound:    Lower limit of a range dimension, in the same units as ``value``.
        upper_bound:    Upper limit of a range dimension, in the same units as ``value``.
        ref_pts:        Reference stations in the extraction coordinate space: global STEP
                        space by default, or frame-local when extraction receives a
                        :class:`PartFrame`. For a linear or thickness dimension these are the
                        centroids of its authored reference groups; other records retain the
                        per-shape bounding-box centroids.
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
        gtol_modifiers: Stable names for source geometric-tolerance qualifiers and modifiers.
        lowering_blockers: Missing/unrepresented facts that make concept lowering unsafe.
        rendering_blockers: Source-geometry facts that make a typed dimension unsafe to draw.
        source_value_blockers: Unreadable authored values that make rendering unsafe even if
                        Part21 later supplies complete reference geometry.
        source_ids:     All source occurrences represented by one projected definition.
        datum_contexts: Tolerance semantic names in which a datum definition is referenced.
        reference_item_ids: Exact Part21 representation items bound to a datum feature.
        reference_item_groups: Ordered Part21 support groups bound to a dimension.
        reference_axis: Axis normal to a proven datum reference plane.
        semantic_name: Stable source name for a semantic manufacturing requirement.
        shape_aspect_ids: Part21 shape aspects associating a semantic requirement to geometry.
        cylindrical_refs: Canonical finite-cylinder topology referenced by a Size_Diameter
                        requirement. Empty for other dimension families or unresolved geometry.
        reference_bboxes: Per-item bounds for exact imported manufacturing supports.
        circular_refs: Canonical circular-edge topology referenced by a Size_Diameter
                        requirement whose semantic association names edges rather than faces.
        angular_reference: Oriented planar supports for an angular requirement. ``None``
                        when the two source reference groups do not prove such supports.
        angular_references: Ordered member references for an angular pattern. Their order
                        matches ``shape_aspect_ids`` and ``reference_item_groups``.
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
    reference_bboxes: tuple[tuple[float, float, float, float, float, float], ...] = ()
    angular_reference: AngularReference | None = None
    reference_item_groups: tuple[tuple[str, ...], ...] = ()
    circular_refs: tuple[CircularReference, ...] = ()
    angular_references: tuple[AngularReference, ...] = ()
    source_value_blockers: tuple[str, ...] = ()


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
    #: The document this census was read from, and its digest. Empty when extraction never ran.
    #: Carried because since #1563 the document need not be the drawing's own geometry source: a
    #: `Sheet` holds an in-memory solid and NAMES the STEP it came from, which is the caller's
    #: claim rather than a proof. Reporting name and digest puts that claim on the record instead
    #: of leaving a reconciliation silently attributed to whatever file was passed.
    source_name: str = ""
    source_sha256: str = ""
    #: Product-owned title-block facts share this extraction's Part21 snapshot, but are
    #: not feature PMI and therefore do not enter its annotation source denominator.
    material_facts: tuple[MaterialFact, ...] = ()
    material_error: str = ""


# ---------------------------------------------------------------------------
# OCP capability guard
# ---------------------------------------------------------------------------

try:
    from OCP.Bnd import Bnd_Box
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.BRepBndLib import BRepBndLib
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.gp import gp_Trsf
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
    from OCP.TDF import TDF_LabelSequence, TDF_Tool
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import (
        TopAbs_EDGE,
        TopAbs_FACE,
        TopAbs_FORWARD,
        TopAbs_REVERSED,
        TopAbs_VERTEX,
    )
    from OCP.TopExp import TopExp
    from OCP.TopLoc import TopLoc_Location
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
# Helpers
# ---------------------------------------------------------------------------


def _source_to_local_transform(frame: PartFrame):
    """Return the rigid OCCT transform matching :meth:`PartFrame.to_local`."""
    transform = gp_Trsf()
    axes = (frame.x, frame.y, frame.z)
    values = tuple(component for axis in axes for component in axis)
    offsets = tuple(-sum(axis[index] * frame.origin[index] for index in range(3)) for axis in axes)
    transform.SetValues(
        values[0],
        values[1],
        values[2],
        offsets[0],
        values[3],
        values[4],
        values[5],
        offsets[1],
        values[6],
        values[7],
        values[8],
        offsets[2],
    )
    return transform


def _shape_bbox(
    shape, frame: PartFrame | None = None
) -> tuple[float, float, float, float, float, float]:
    """Return *shape*'s AABB in source space or the requested local frame.

    Applying a rigid location before measuring is intentionally tighter than transforming the
    source AABB: the latter encloses air introduced by the source axes after an arbitrary rotation.
    ``Moved`` changes evaluated coordinates without rebuilding the imported topology.
    """
    if frame is not None:
        shape = shape.Moved(TopLoc_Location(_source_to_local_transform(frame)))
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


def _frame_point(
    point: tuple[float, float, float], frame: PartFrame | None
) -> tuple[float, float, float]:
    """Express one source-space point in *frame*, preserving default extraction exactly."""
    if frame is None:
        return point
    return frame.to_local(point)


def _frame_vector(
    vector: tuple[float, float, float], frame: PartFrame | None
) -> tuple[float, float, float]:
    """Express one free vector in *frame* without applying the frame origin."""
    if frame is None:
        return vector
    return cast(
        tuple[float, float, float],
        tuple(
            sum(component * basis[index] for index, component in enumerate(vector))
            for basis in (frame.x, frame.y, frame.z)
        ),
    )


def _dominant_from_bbox(bbox: tuple[float, float, float, float, float, float]) -> str:
    """Return ``'X'``/``'Y'``/``'Z'`` for the axis with the largest bbox extent."""
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    spans = [("X", abs(xmax - xmin)), ("Y", abs(ymax - ymin)), ("Z", abs(zmax - zmin))]
    dom = max(spans, key=lambda t: t[1])
    return dom[0] if dom[1] > 1e-6 else "?"


def _direct_xcaf_support_is_incomplete(record: PmiRecord) -> bool:
    """Whether direct XCAF failed to supply all support geometry, independent of rendering."""
    reasons = (*record.lowering_blockers, *record.rendering_blockers)
    missing_groups = (
        "linear dimension needs two measurable authored reference groups",
        "thickness dimension needs two measurable authored reference groups",
        "diameter dimension needs a measurable",
    )
    return any(
        _is_direct_xcaf_reference_failure(reason)
        or _is_direct_xcaf_diameter_failure(reason)
        or _is_direct_xcaf_angular_failure(reason)
        or reason.startswith(missing_groups)
        for reason in reasons
    )


def _make_label(
    kind: str,
    value: float,
    upper_tol: float | None,
    lower_tol: float | None,
    *,
    lower_bound: float | None = None,
    upper_bound: float | None = None,
    value_decimals: int | None = None,
    tolerance_decimals: int | None = None,
    unit_name: str = "",
) -> str:
    """Format the annotation label with optional deviation or limit tolerance."""
    from draftwright._core import _fmt
    from draftwright._geometry import _fmt_pmi_magnitude

    prefix = _DIM_PREFIX.get(kind, "")
    base = f"{prefix}{_fmt(value, value_decimals)}"
    if lower_bound is not None and upper_bound is not None:
        base = (
            f"{prefix}{_fmt_pmi_magnitude(lower_bound, value_decimals)} - "
            f"{prefix}{_fmt_pmi_magnitude(upper_bound, value_decimals)}"
        )
        return f"{base} {unit_name}" if unit_name else base
    # OCCT returns tolerances as positive magnitudes regardless of sign
    # convention.  upper_tol is always the + deviation; lower_tol is always
    # the - deviation stored as a positive magnitude.  We add explicit signs
    # so the label is unambiguous on the drawing.
    if upper_tol is not None and lower_tol is not None:
        if abs(upper_tol) == abs(lower_tol):
            base += f" ±{_fmt_pmi_magnitude(abs(upper_tol), tolerance_decimals)}"
        else:
            base += (
                f" +{_fmt_pmi_magnitude(abs(upper_tol), tolerance_decimals)}"
                f"/-{_fmt_pmi_magnitude(abs(lower_tol), tolerance_decimals)}"
            )
    elif upper_tol is not None:
        base += f" +{_fmt_pmi_magnitude(abs(upper_tol), tolerance_decimals)}"
    elif lower_tol is not None:
        base += f" -{_fmt_pmi_magnitude(abs(lower_tol), tolerance_decimals)}"
    return f"{base} {unit_name}" if unit_name else base


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
    return None


def _reference_geometry_with_groups(label, shape_tool, frame: PartFrame | None = None):
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
                bbox = _shape_bbox(shape) if frame is None else _shape_bbox(shape, frame)
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


def _reference_geometry(label, shape_tool, frame: PartFrame | None = None):
    """Compatible flattened geometry projection shared by non-dimensional PMI."""
    if frame is None:
        geometry = _reference_geometry_with_groups(label, shape_tool)
    else:
        geometry = _reference_geometry_with_groups(label, shape_tool, frame)
    points, ref_bbox, dominant_axis, reasons, _groups = geometry
    return points, ref_bbox, dominant_axis, reasons


def _angular_reference_from_planes(
    planes: tuple[
        tuple[tuple[float, float, float], tuple[float, float, float]],
        tuple[tuple[float, float, float], tuple[float, float, float]],
    ],
    group_stations: tuple[tuple[float, float, float] | None, ...],
    nominal: float,
) -> tuple[AngularReference | None, tuple[str, ...]]:
    """Construct a common normal-section angle from two support-plane equations."""
    if len(group_stations) != 2 or any(point is None for point in group_stations):
        return None, ("angular dimension needs two measurable authored reference groups",)
    first_point, second_point = cast(
        tuple[tuple[float, float, float], tuple[float, float, float]], group_stations
    )
    target = (
        (first_point[0] + second_point[0]) / 2,
        (first_point[1] + second_point[1]) / 2,
        (first_point[2] + second_point[2]) / 2,
    )
    (plane_a, normal_a), (plane_b, normal_b) = planes
    coupling = sum(a * b for a, b in zip(normal_a, normal_b, strict=True))
    determinant = 1.0 - coupling * coupling
    if determinant <= 1e-12:
        return None, ("angular reference planes are parallel or coincident",)
    residual_a = sum(normal_a[index] * (plane_a[index] - target[index]) for index in range(3))
    residual_b = sum(normal_b[index] * (plane_b[index] - target[index]) for index in range(3))
    weight_a = (residual_a - coupling * residual_b) / determinant
    weight_b = (residual_b - coupling * residual_a) / determinant
    vertex = (
        target[0] + weight_a * normal_a[0] + weight_b * normal_b[0],
        target[1] + weight_a * normal_a[1] + weight_b * normal_b[1],
        target[2] + weight_a * normal_a[2] + weight_b * normal_b[2],
    )
    line = (
        normal_a[1] * normal_b[2] - normal_a[2] * normal_b[1],
        normal_a[2] * normal_b[0] - normal_a[0] * normal_b[2],
        normal_a[0] * normal_b[1] - normal_a[1] * normal_b[0],
    )
    line_length = math.hypot(*line)
    if line_length <= 1e-12:
        return None, ("angular reference planes are parallel or coincident",)
    line = (line[0] / line_length, line[1] / line_length, line[2] / line_length)

    witnesses: list[tuple[float, float, float]] = []
    for station, normal in ((first_point, normal_a), (second_point, normal_b)):
        # A bbox centre can sit off an oblique face, and two face centres can sit at
        # different positions along the planes' intersection. Build the ray in the support
        # plane and in one common normal section; the station chooses only which half-ray.
        support = (
            line[1] * normal[2] - line[2] * normal[1],
            line[2] * normal[0] - line[0] * normal[2],
            line[0] * normal[1] - line[1] * normal[0],
        )
        signed_distance = sum(
            (station[index] - vertex[index]) * support[index] for index in range(3)
        )
        if abs(signed_distance) <= 1e-9:
            return None, ("one angular reference face does not select a support half-ray",)
        witnesses.append(
            (
                vertex[0] + signed_distance * support[0],
                vertex[1] + signed_distance * support[1],
                vertex[2] + signed_distance * support[2],
            )
        )
    try:
        reference = AngularReference(
            vertex=vertex,
            first=witnesses[0],
            second=witnesses[1],
            virtual_vertex=True,
        )
    except ValueError as exc:
        return None, (f"angular reference is invalid ({exc})",)
    if not math.isclose(reference.angle_degrees, nominal, rel_tol=0.0, abs_tol=0.01):
        return reference, (
            f"angular reference angle {reference.angle_degrees:.6g} deg differs from nominal "
            f"{nominal:.6g} deg",
        )
    return reference, ()


def _angular_reference(
    label,
    shape_tool,
    group_stations: tuple[tuple[float, float, float] | None, ...],
    nominal: float,
    frame: PartFrame | None = None,
) -> tuple[AngularReference | None, tuple[str, ...]]:
    """Recover two oriented planar supports and their intersection from an XCAF relation."""
    first_refs = TDF_LabelSequence()
    second_refs = TDF_LabelSequence()
    XCAFDoc_DimTolTool.GetRefShapeLabel_s(label, first_refs, second_refs)
    groups = (first_refs, second_refs)
    if tuple(group.Length() for group in groups) != (1, 1):
        return None, ("angular dimension needs one planar face in each reference group",)

    planes: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    for group in groups:
        shape = shape_tool.GetShape_s(group.Value(1))
        if shape is None or shape.IsNull() or shape.ShapeType() != TopAbs_FACE:
            return None, ("one angular reference is not a face",)
        try:
            surface = BRepAdaptor_Surface(TopoDS.Face_s(shape))
            if surface.GetType() != GeomAbs_Plane:
                return None, ("one angular reference face is not planar",)
            plane = surface.Plane()
            location = plane.Location()
            direction = plane.Axis().Direction()
            planes.append(
                (
                    _frame_point((location.X(), location.Y(), location.Z()), frame),
                    _frame_vector((direction.X(), direction.Y(), direction.Z()), frame),
                )
            )
        except Exception as exc:
            return None, (
                f"one angular reference plane could not be measured ({_failure_reason(exc)})",
            )
    assert len(planes) == 2
    return _angular_reference_from_planes((planes[0], planes[1]), group_stations, nominal)


def _angular_reference_from_shapes(
    shapes: tuple[Any, ...],
    nominal: float,
    frame: PartFrame | None = None,
) -> tuple[AngularReference | None, tuple[str, ...]]:
    """Recover one included cone angle from identity-proven split support faces."""
    if len(shapes) != 2:
        return None, (f"needs exactly two support faces (got {len(shapes)})",)

    cones = []
    for shape in shapes:
        if shape.ShapeType() != TopAbs_FACE:
            return None, ("one support is not a face",)
        try:
            surface = BRepAdaptor_Surface(TopoDS.Face_s(shape))
            if surface.GetType() != GeomAbs_Cone:
                return None, ("one support face is not conical",)
            cone = surface.Cone()
            apex = cone.Apex()
            direction = cone.Axis().Direction()
            axis = _frame_vector((direction.X(), direction.Y(), direction.Z()), frame)
            length = math.hypot(*axis)
            if length <= 1e-12:
                return None, ("one conical support has a degenerate axis",)
            axis = (axis[0] / length, axis[1] / length, axis[2] / length)
            for component in axis:
                if abs(component) > 1e-12:
                    if component < 0:
                        axis = (-axis[0], -axis[1], -axis[2])
                    break
            witness = _face_topology_witness(shape, frame)
            transformed_apex = _frame_point((apex.X(), apex.Y(), apex.Z()), frame)
            axial_offset = sum(
                (witness[index] - transformed_apex[index]) * axis[index] for index in range(3)
            )
            if abs(axial_offset) <= 1e-9:
                return None, ("one support face does not select a cone nappe",)
            cones.append(
                (
                    transformed_apex,
                    axis,
                    abs(float(cone.SemiAngle())),
                    1.0 if axial_offset > 0 else -1.0,
                )
            )
        except Exception as exc:
            return None, (f"conical support could not be measured ({_failure_reason(exc)})",)

    first, second = cones
    if any(
        not math.isclose(a, b, rel_tol=0.0, abs_tol=1e-6)
        for a, b in zip(first[0], second[0], strict=True)
    ):
        return None, ("support faces do not share one cone apex",)
    if abs(sum(a * b for a, b in zip(first[1], second[1], strict=True))) < 1.0 - 1e-9:
        return None, ("support faces do not share one cone axis",)
    if not math.isclose(first[2], second[2], rel_tol=0.0, abs_tol=1e-9):
        return None, ("support faces do not share one cone semi-angle",)
    if first[3] != second[3]:
        return None, ("support faces occupy opposite cone nappes",)

    axis = first[1]
    dominant = max(range(3), key=lambda index: abs(axis[index]))
    if any(abs(axis[index]) > 1e-6 for index in range(3) if index != dominant):
        return None, ("cone axis has no true-angle principal projection",)
    principal_axis = cast(
        tuple[float, float, float],
        tuple(1.0 if index == dominant else 0.0 for index in range(3)),
    )
    radial_index = 1 if dominant == 0 else 0
    radial = cast(
        tuple[float, float, float],
        tuple(1.0 if index == radial_index else 0.0 for index in range(3)),
    )
    cosine = math.cos(first[2])
    sine = math.sin(first[2])
    first_ray = cast(
        tuple[float, float, float],
        tuple(
            first[0][index] + first[3] * principal_axis[index] * cosine + radial[index] * sine
            for index in range(3)
        ),
    )
    second_ray = cast(
        tuple[float, float, float],
        tuple(
            first[0][index] + first[3] * principal_axis[index] * cosine - radial[index] * sine
            for index in range(3)
        ),
    )
    try:
        reference = AngularReference(
            vertex=first[0], first=first_ray, second=second_ray, virtual_vertex=True
        )
    except ValueError as exc:
        return None, (f"conical angular reference is invalid ({exc})",)
    if not math.isclose(reference.angle_degrees, nominal, rel_tol=0.0, abs_tol=0.01):
        return reference, (
            f"included cone angle {reference.angle_degrees:.6g} deg differs from nominal "
            f"{nominal:.6g} deg",
        )
    return reference, ()


def _cylindrical_references(label, shape_tool, frame: PartFrame | None = None):
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
                        axis_point=_frame_point((point.X(), point.Y(), point.Z()), frame),
                        axis_direction=_frame_vector(
                            (direction.X(), direction.Y(), direction.Z()), frame
                        ),
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

    return _unique_cylindrical_references(references), tuple(dict.fromkeys(reasons))


def _unique_cylindrical_references(
    references: list[CylindricalReference],
) -> tuple[CylindricalReference, ...]:
    """Coalesce repeated transfers of one canonical cylinder without merging neighbours."""
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
    return tuple(unique.values())


def _cylindrical_references_from_shapes(shapes, *, noun: str, frame: PartFrame | None = None):
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
                    axis_point=_frame_point((point.X(), point.Y(), point.Z()), frame),
                    axis_direction=_frame_vector(
                        (direction.X(), direction.Y(), direction.Z()), frame
                    ),
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
    return _unique_cylindrical_references(references), tuple(dict.fromkeys(reasons))


def _circular_references_from_shapes(
    shapes, *, noun: str, frame: PartFrame | None = None
) -> tuple[tuple[CircularReference, ...], tuple[str, ...]]:
    """Measure exact imported circular edges without inventing cylindrical extent or sense."""
    references: list[CircularReference] = []
    reasons: list[str] = []
    for shape in shapes:
        try:
            if shape.ShapeType() != TopAbs_EDGE:
                reasons.append(f"one {noun} reference is not an edge")
                continue
            curve = BRepAdaptor_Curve(TopoDS.Edge_s(shape))
            if curve.GetType() != GeomAbs_Circle:
                reasons.append(f"one {noun} reference edge is not circular")
                continue
            circle = curve.Circle()
            center = circle.Location()
            normal = circle.Axis().Direction()
            references.append(
                CircularReference.canonical(
                    center=_frame_point((center.X(), center.Y(), center.Z()), frame),
                    normal=_frame_vector((normal.X(), normal.Y(), normal.Z()), frame),
                    radius=float(circle.Radius()),
                )
            )
        except Exception as exc:
            reasons.append(f"one {noun} reference could not be measured ({_failure_reason(exc)})")
    if not references and not reasons:
        reasons.append(f"{noun} reference geometry is unavailable")
    unique: dict[tuple, CircularReference] = {}
    for reference in references:
        signature = (
            *(round(value, 9) for value in reference.center),
            *(round(value, 9) for value in reference.normal),
            round(reference.radius, 9),
        )
        unique.setdefault(signature, reference)
    return tuple(unique.values()), tuple(dict.fromkeys(reasons))


def _circular_diameter_blockers(
    references: tuple[CircularReference, ...], nominal: float, reasons: tuple[str, ...]
) -> tuple[str, ...]:
    """Validate exact circular-edge evidence for one authored diameter."""
    blockers = list(reasons)
    if not references:
        if not blockers:
            blockers.append("diameter dimension needs a measurable circular-edge reference")
        return tuple(dict.fromkeys(blockers))
    normals = {
        tuple(round(component, 9) for component in reference.normal) for reference in references
    }
    if len(normals) != 1:
        blockers.append("diameter circular references do not share one normal direction")
    value_tol = max(0.01, abs(nominal) * 5e-4)
    mismatches = [
        reference.diameter
        for reference in references
        if not math.isclose(reference.diameter, nominal, rel_tol=0.0, abs_tol=value_tol)
    ]
    if mismatches:
        values = ", ".join(f"{value:.6g}" for value in mismatches)
        blockers.append(
            f"circular reference diameter(s) {values} mm differ from nominal {nominal:.6g} mm"
        )
    return tuple(dict.fromkeys(blockers))


def _diameter_reference_blockers(
    references: tuple[CylindricalReference, ...], nominal: float, reasons: tuple[str, ...]
) -> tuple[str, ...]:
    """Facts that make a Size_Diameter relationship unsafe to draw or correlate."""
    blockers = list(reasons)
    if not references:
        if not blockers:
            blockers.append("diameter dimension needs a measurable cylindrical-face reference")
        return tuple(dict.fromkeys(blockers))
    directions = {
        tuple(round(component, 9) for component in reference.axis_direction)
        for reference in references
    }
    if len(directions) != 1:
        blockers.append("diameter references do not share one cylinder axis direction")
    else:
        direction = next(iter(directions))
        if min(abs(component) for component in direction) > 1e-6:
            blockers.append("diameter cylinder axis does not lie in a principal projection plane")
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


def _datum_geometry_from_shapes(shapes, frame: PartFrame | None = None):
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
            bbox = _shape_bbox(shape) if frame is None else _shape_bbox(shape, frame)
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
            local_direction = _frame_vector((direction.X(), direction.Y(), direction.Z()), frame)
            components = tuple(abs(value) for value in local_direction)
            axis_index = max(range(3), key=components.__getitem__)
            if (
                components[axis_index] < 1e-6
                or sum(components) - components[axis_index] > 0.1 * components[axis_index]
            ):
                reasons.append("one datum reference surface is not axis-aligned")
                continue
            location = axis.Location()
            coordinates = _frame_point((location.X(), location.Y(), location.Z()), frame)
            support: tuple[float, ...]
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


def _datum_reference_shapes(label, shape_tool):
    """Keep the exact XCAF supports for the Part21 correspondence check."""
    first_refs = TDF_LabelSequence()
    second_refs = TDF_LabelSequence()
    XCAFDoc_DimTolTool.GetRefShapeLabel_s(label, first_refs, second_refs)
    shapes = []
    for refs in (first_refs, second_refs):
        for index in range(1, refs.Length() + 1):
            shapes.append(shape_tool.GetShape_s(refs.Value(index)))
    return tuple(shapes)


def _datum_reference_geometry(label, shape_tool, frame: PartFrame | None = None):
    """Measure datum faces reached through the direct XCAF relationship."""
    shapes = _datum_reference_shapes(label, shape_tool)
    if frame is None:
        return _datum_geometry_from_shapes(shapes)
    return _datum_geometry_from_shapes(shapes, frame)


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


def _datum_definition(
    letter: str, definitions: tuple[DatumDefinitionFact, ...]
) -> tuple[DatumDefinitionFact | None, str]:
    """Use a datum's own complete Part21 definition, independent of its tolerance uses."""
    matches = [definition for definition in definitions if definition.letter == letter]
    if len(matches) > 1:
        return None, f"Part21 has {len(matches)} datum definitions for letter {letter!r}"
    if matches and not matches[0].reason and matches[0].datum_feature_id:
        return matches[0], ""
    return None, ""


def _same_datum_support(
    xcaf_shapes, part21_shapes, xcaf_bbox, xcaf_axis, part21_bbox, part21_axis
) -> bool:
    """Require identical imported faces, even when distinct faces coincide geometrically."""
    return bool(
        xcaf_shapes
        and part21_shapes
        and all(shape is not None and not shape.IsNull() for shape in xcaf_shapes)
        and all(shape is not None and not shape.IsNull() for shape in part21_shapes)
        and all(any(shape.IsSame(other) for other in part21_shapes) for shape in xcaf_shapes)
        and all(any(shape.IsSame(other) for other in xcaf_shapes) for shape in part21_shapes)
        and xcaf_bbox is not None
        and part21_bbox is not None
        and xcaf_axis not in ("", "?")
        and xcaf_axis == part21_axis
        and all(
            abs(left - right) <= 1e-5 for left, right in zip(xcaf_bbox, part21_bbox, strict=True)
        )
    )


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
        blockers = list(
            dict.fromkeys(reason for record in group for reason in record.lowering_blockers)
        )
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


def _dimension_record(
    label,
    obj,
    type_code: int,
    shape_tool,
    source_id: str,
    frame: PartFrame | None = None,
    length_factor_mm: float = 1.0,
    length_factor_reason: str = "",
    display_facts: tuple[DimensionDisplayFact, ...] = (),
    association_fact: DimensionAssociationFact | None = None,
    association_reason: str = "",
) -> tuple[PmiRecord, tuple[str, ...]]:
    """Convert one semantic XCAF dimension label, allowing its caller to record failures."""
    partial_reasons = [association_reason] if association_reason else []
    if association_fact is not None and association_fact.reason:
        partial_reasons.append(association_fact.reason)
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
    tolerance_read_reasons: list[str] = []
    try:
        has_plus_minus_tolerance = bool(obj.IsDimWithPlusMinusTolerance())
    except Exception as exc:
        reason = f"plus/minus tolerance status is unavailable ({_failure_reason(exc)})"
        partial_reasons.append(reason)
        tolerance_read_reasons.append(reason)
    else:
        if has_plus_minus_tolerance:
            try:
                candidate = float(obj.GetUpperTolValue())
                if abs(candidate) > 1e-9:
                    upper_tol = candidate
            except Exception as exc:
                reason = f"upper tolerance is unavailable ({_failure_reason(exc)})"
                partial_reasons.append(reason)
                tolerance_read_reasons.append(reason)

            try:
                candidate = float(obj.GetLowerTolValue())
                if abs(candidate) > 1e-9:
                    lower_tol = candidate
            except Exception as exc:
                reason = f"lower tolerance is unavailable ({_failure_reason(exc)})"
                partial_reasons.append(reason)
                tolerance_read_reasons.append(reason)

    lower_bound: float | None = None
    upper_bound: float | None = None
    if obj.IsDimWithRange():
        try:
            lower_bound = float(obj.GetLowerBound())
        except Exception as exc:
            reason = f"lower range bound is unavailable ({_failure_reason(exc)})"
            partial_reasons.append(reason)
            tolerance_read_reasons.append(reason)
        try:
            upper_bound = float(obj.GetUpperBound())
        except Exception as exc:
            reason = f"upper range bound is unavailable ({_failure_reason(exc)})"
            partial_reasons.append(reason)
            tolerance_read_reasons.append(reason)

    kind = _DIM_TYPE.get(type_code, f"type{type_code}")
    authored_value = value
    try:
        semantic_name_obj = obj.GetSemanticName()
        semantic_name = (
            str(semantic_name_obj.ToCString()).strip() if semantic_name_obj is not None else ""
        )
    except Exception:
        semantic_name = ""
    display_fact = match_dimension_display(display_facts, semantic_name, kind, authored_value)
    if kind in _LENGTH_DIMENSION_KINDS:
        if length_factor_reason:
            partial_reasons.append(length_factor_reason)
        else:
            value *= length_factor_mm
            if lower_bound is not None:
                lower_bound *= length_factor_mm
            if upper_bound is not None:
                upper_bound *= length_factor_mm

    if frame is None:
        reference_geometry = _reference_geometry_with_groups(label, shape_tool)
    else:
        reference_geometry = _reference_geometry_with_groups(label, shape_tool, frame)
    points, ref_bbox, dominant_axis, reference_reasons, group_stations = reference_geometry
    partial_reasons.extend(reference_reasons)
    rendering_blockers: tuple[str, ...] = ()
    cylindrical_refs: tuple[CylindricalReference, ...] = ()
    angular_reference: AngularReference | None = None
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
        if frame is None:
            cylindrical_refs, cylinder_reasons = _cylindrical_references(label, shape_tool)
        else:
            cylindrical_refs, cylinder_reasons = _cylindrical_references(label, shape_tool, frame)
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
    elif kind == "angular":
        angular_reference, angular_reasons = _angular_reference(
            label, shape_tool, group_stations, value, frame
        )
        rendering_blockers = tuple(dict.fromkeys((*reference_reasons, *angular_reasons)))
        if angular_reference is not None:
            dominant_axis = angular_reference.principal_axis
            points = (
                angular_reference.first,
                angular_reference.vertex,
                angular_reference.second,
            )
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
            label=(
                _make_label(
                    kind,
                    display_fact.authored_value,
                    upper_tol / length_factor_mm if upper_tol is not None else None,
                    lower_tol / length_factor_mm if lower_tol is not None else None,
                    lower_bound=(
                        lower_bound / length_factor_mm if lower_bound is not None else None
                    ),
                    upper_bound=(
                        upper_bound / length_factor_mm if upper_bound is not None else None
                    ),
                    value_decimals=display_fact.value_decimals,
                    tolerance_decimals=display_fact.tolerance_decimals,
                    unit_name=display_fact.unit_name,
                )
                if (
                    display_fact is not None
                    and display_fact.unit_name
                    and not length_factor_reason
                    and math.isclose(
                        display_fact.unit_factor_mm,
                        length_factor_mm,
                        rel_tol=1e-12,
                    )
                )
                else _make_label(
                    kind,
                    value,
                    upper_tol,
                    lower_tol,
                    lower_bound=lower_bound,
                    upper_bound=upper_bound,
                )
            ),
            source_id=source_id,
            part21_id=association_fact.entity_id if association_fact is not None else "",
            source_category="dimension",
            lowering_blockers=lowering_blockers,
            reference_item_ids=(
                tuple(item for group in association_fact.reference_item_groups for item in group)
                if association_fact is not None
                else ()
            ),
            reference_item_groups=(
                association_fact.reference_item_groups if association_fact is not None else ()
            ),
            shape_aspect_ids=(
                association_fact.shape_aspect_ids if association_fact is not None else ()
            ),
            rendering_blockers=rendering_blockers,
            source_value_blockers=tuple(dict.fromkeys(tolerance_read_reasons)),
            cylindrical_refs=cylindrical_refs,
            angular_reference=angular_reference,
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
    frame: PartFrame | None = None,
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
    qualifiers, qualifier_reasons = _geometric_tolerance_qualifiers(obj)
    gtol_modifiers = tuple(dict.fromkeys((*gtol_modifiers, *qualifiers)))
    partial_reasons.extend(qualifier_reasons)
    partial_reasons.extend(_unpreserved_geometric_tolerance_fields(obj))
    if frame is None:
        reference_geometry = _reference_geometry(label, shape_tool)
    else:
        reference_geometry = _reference_geometry(label, shape_tool, frame)
    points, ref_bbox, dominant_axis, reference_reasons = reference_geometry
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


def _surface_label_projection(
    step_file: str | Path,
) -> tuple[tuple[PmiSourceEntity, ...], tuple[PmiRecord, ...]]:
    """Build the Part21-only source/record projection for associated descriptive labels."""
    sources: list[PmiSourceEntity] = []
    records: list[PmiRecord] = []
    try:
        facts = read_surface_labels(step_file)
    except Exception as exc:
        reason = f"Part21 surface-label read failed: {_failure_reason(exc)}"
        return (
            PmiSourceEntity(
                source_id="surface_label:part21",
                category="surface_label",
                type_code=None,
                outcome="not_extracted",
                reason=reason,
            ),
        ), ()

    for fact in facts:
        source_id = f"surface_label:{fact.entity_id}"
        blockers = (fact.reason,) if fact.reason else ()
        if fact.text:
            records.append(
                PmiRecord(
                    kind="surface_label",
                    type_code=None,
                    value=0.0,
                    label=fact.text,
                    source_id=source_id,
                    part21_id=fact.entity_id,
                    source_category="surface_label",
                    lowering_blockers=blockers,
                    reference_item_ids=fact.reference_item_ids,
                    shape_aspect_ids=(fact.shape_aspect_id,) if fact.shape_aspect_id else (),
                )
            )
        sources.append(
            PmiSourceEntity(
                source_id=source_id,
                category="surface_label",
                type_code=None,
                outcome=(
                    "partially_extracted"
                    if fact.text and fact.reason
                    else "extracted"
                    if fact.text
                    else "not_extracted"
                ),
                reason=fact.reason,
            )
        )
    return tuple(sources), tuple(records)


_CYLINDRICAL_REQUIREMENT_KINDS = frozenset(("external_thread", "internal_thread", "knurl"))


def _chamfer_reference_bboxes(shapes, frame: PartFrame | None = None):
    """Measure exact conical faces referenced by a semantic chamfer requirement."""
    boxes = []
    reasons = []
    for shape in shapes:
        try:
            if shape.ShapeType() != TopAbs_FACE:
                reasons.append("one chamfer reference is not a face")
                continue
            surface = BRepAdaptor_Surface(TopoDS.Face_s(shape))
            if surface.GetType() != GeomAbs_Cone:
                reasons.append("one chamfer reference face is not conical")
                continue
            boxes.append(_shape_bbox(shape) if frame is None else _shape_bbox(shape, frame))
        except Exception as exc:
            reasons.append(f"one chamfer reference could not be measured ({_failure_reason(exc)})")
    if not boxes and not reasons:
        reasons.append("chamfer reference geometry is unavailable")
    return tuple(boxes), tuple(dict.fromkeys(reasons))


def _manufacturing_requirement_topology(
    records, step_reader, frame: PartFrame | None = None
) -> tuple[PmiRecord, ...]:
    """Attach exact finite-cylinder evidence to supported manufacturing records."""
    imported_faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(step_reader.OneShape(), TopAbs_FACE, imported_faces)
    resolver = _DatumTopologyResolver(step_reader, imported_faces)
    projected = []
    for record in records:
        if (
            record.kind not in _CYLINDRICAL_REQUIREMENT_KINDS | {"chamfers"}
            or record.lowering_blockers
        ):
            projected.append(record)
            continue
        shapes, topology_reasons = resolver.resolve(
            record.part21_id,
            record.reference_item_ids,
            noun="manufacturing requirement",
        )
        if record.kind == "chamfers":
            boxes, geometry_reasons = _chamfer_reference_bboxes(shapes, frame)
            blockers = tuple(
                dict.fromkeys((*record.lowering_blockers, *topology_reasons, *geometry_reasons))
            )
            projected.append(replace(record, reference_bboxes=boxes, lowering_blockers=blockers))
            continue
        if frame is None:
            references, geometry_reasons = _cylindrical_references_from_shapes(
                shapes, noun="manufacturing requirement"
            )
        else:
            references, geometry_reasons = _cylindrical_references_from_shapes(
                shapes,
                noun="manufacturing requirement",
                frame=frame,
            )
        blockers = tuple(
            dict.fromkeys((*record.lowering_blockers, *topology_reasons, *geometry_reasons))
        )
        projected.append(replace(record, cylindrical_refs=references, lowering_blockers=blockers))
    return tuple(projected)


def _surface_label_topology(
    records, step_reader, frame: PartFrame | None = None
) -> tuple[PmiRecord, ...]:
    """Attach exact imported edge sites to semantic surface labels."""
    imported_edges = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(step_reader.OneShape(), TopAbs_EDGE, imported_edges)
    resolver = _SurfaceLabelTopologyResolver(step_reader, imported_edges)
    projected = []
    for record in records:
        if record.source_category != "surface_label" or record.lowering_blockers:
            projected.append(record)
            continue
        definition_id = record.shape_aspect_ids[0] if len(record.shape_aspect_ids) == 1 else ""
        shapes, topology_reasons = resolver.resolve(
            definition_id, record.reference_item_ids, noun="surface label"
        )
        boxes = []
        witnesses = []
        geometry_reasons = []
        for shape in shapes:
            try:
                boxes.append(_shape_bbox(shape) if frame is None else _shape_bbox(shape, frame))
                curve = BRepAdaptor_Curve(TopoDS.Edge_s(shape))
                first, last = curve.FirstParameter(), curve.LastParameter()
                if not (math.isfinite(first) and math.isfinite(last)):
                    raise ValueError("edge has no finite parameter interval")
                point = curve.Value((first + last) / 2)
                witnesses.append(_frame_point((point.X(), point.Y(), point.Z()), frame))
            except Exception as exc:
                geometry_reasons.append(
                    f"surface label reference could not be measured ({_failure_reason(exc)})"
                )
        ref_bbox = _merge_bboxes(boxes) if boxes else None
        blockers = tuple(
            dict.fromkeys((*record.lowering_blockers, *topology_reasons, *geometry_reasons))
        )
        projected.append(
            replace(
                record,
                ref_pts=tuple(witnesses),
                ref_bbox=ref_bbox,
                dominant_axis=_dominant_from_bbox(ref_bbox) if ref_bbox is not None else "?",
                lowering_blockers=blockers,
            )
        )
    return tuple(projected)


def _common_label_topology(
    records, step_reader, frame: PartFrame | None = None
) -> tuple[PmiRecord, ...]:
    """Attach exact imported face sites to XCAF common-label occurrences."""
    imported_faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(step_reader.OneShape(), TopAbs_FACE, imported_faces)
    resolver = _CommonLabelTopologyResolver(step_reader, imported_faces)
    projected = []
    for record in records:
        if record.kind != "common_label" or record.lowering_blockers:
            projected.append(record)
            continue
        definition_id = record.shape_aspect_ids[0] if len(record.shape_aspect_ids) == 1 else ""
        shapes, topology_reasons = resolver.resolve(
            definition_id, record.reference_item_ids, noun="common label"
        )
        boxes = []
        witnesses = []
        geometry_reasons = []
        for shape in shapes:
            try:
                boxes.append(_shape_bbox(shape) if frame is None else _shape_bbox(shape, frame))
                witnesses.append(_face_topology_witness(shape, frame))
            except Exception as exc:
                geometry_reasons.append(
                    f"common-label reference could not be measured ({_failure_reason(exc)})"
                )
        ref_bbox = _merge_bboxes(boxes) if boxes else None
        blockers = tuple(
            dict.fromkeys((*record.lowering_blockers, *topology_reasons, *geometry_reasons))
        )
        projected.append(
            replace(
                record,
                ref_pts=tuple(witnesses),
                ref_bbox=ref_bbox,
                dominant_axis=_dominant_from_bbox(ref_bbox) if ref_bbox is not None else "?",
                lowering_blockers=blockers,
            )
        )
    return tuple(projected)


def _dimension_support_topology(
    records, step_reader, frame: PartFrame | None = None
) -> tuple[PmiRecord, ...]:
    """Replace incomplete XCAF dimension supports with their exact Part21 groups."""
    resolver = _DimensionSupportResolver(step_reader)
    projected = []
    for record in records:
        if (
            record.kind not in ("linear", "thickness", "diameter", "angular")
            or not record.reference_item_groups
        ):
            projected.append(record)
            continue
        if len(record.shape_aspect_ids) != len(record.reference_item_groups):
            reason = "dimension shape-aspect and support-group counts disagree"
            projected.append(
                replace(
                    record,
                    lowering_blockers=tuple(dict.fromkeys((*record.lowering_blockers, reason))),
                )
            )
            continue

        boxes = []
        stations = []
        group_shapes = []
        topology_reasons = []
        for aspect_id, item_ids in zip(
            record.shape_aspect_ids, record.reference_item_groups, strict=True
        ):
            shapes, group_reasons = resolver.resolve_group(aspect_id, item_ids)
            topology_reasons.extend(group_reasons)
            group_shapes.append(shapes)
            group_boxes = []
            for shape in shapes:
                try:
                    box = _shape_bbox(shape) if frame is None else _shape_bbox(shape, frame)
                except Exception as exc:
                    topology_reasons.append(
                        "dimension support geometry could not be measured "
                        f"({_failure_reason(exc)})"
                    )
                else:
                    boxes.append(box)
                    group_boxes.append(box)
            stations.append(_bbox_centroid(_merge_bboxes(group_boxes)) if group_boxes else None)

        topology_reasons = list(dict.fromkeys(topology_reasons))
        ref_bbox = _merge_bboxes(boxes) if boxes else None
        if topology_reasons:
            if _direct_xcaf_support_is_incomplete(record):
                projected.append(
                    replace(
                        record,
                        lowering_blockers=tuple(
                            dict.fromkeys((*record.lowering_blockers, *topology_reasons))
                        ),
                    )
                )
            else:
                projected.append(record)
            continue
        if record.kind == "angular":
            references = []
            angular_geometry_reasons: list[str] = []
            for aspect_id, shapes in zip(record.shape_aspect_ids, group_shapes, strict=True):
                reference, member_reasons = _angular_reference_from_shapes(
                    shapes, record.value, frame
                )
                angular_geometry_reasons.extend(
                    f"angular member {aspect_id}: {reason}" for reason in member_reasons
                )
                if reference is not None:
                    references.append(reference)
            if len(references) != len(group_shapes) and not angular_geometry_reasons:
                angular_geometry_reasons.append(
                    "one angular member produced no reference geometry"
                )
            axes = {reference.principal_axis for reference in references}
            if len(axes) > 1:
                angular_geometry_reasons.append(
                    "angular members do not share one principal projection axis"
                )
            blockers = tuple(dict.fromkeys((*topology_reasons, *angular_geometry_reasons)))
            projected.append(
                replace(
                    record,
                    ref_bbox=ref_bbox,
                    dominant_axis=(next(iter(axes)) if len(axes) == 1 else record.dominant_axis),
                    lowering_blockers=tuple(
                        dict.fromkeys(
                            (
                                *_without_direct_xcaf_reference_failures(record.lowering_blockers),
                                *blockers,
                            )
                        )
                    ),
                    rendering_blockers=tuple(
                        dict.fromkeys(
                            (
                                *_without_direct_xcaf_reference_failures(
                                    _without_direct_xcaf_angular_failures(
                                        record.rendering_blockers
                                    )
                                ),
                                *blockers,
                            )
                        )
                    ),
                    angular_reference=None,
                    angular_references=tuple(references),
                )
            )
            continue
        if record.kind == "diameter":
            shapes = tuple(shape for group in group_shapes for shape in group)
            shape_types = {shape.ShapeType() for shape in shapes}
            cylindrical_refs: tuple[CylindricalReference, ...] = ()
            circular_refs: tuple[CircularReference, ...] = ()
            geometry_reasons: tuple[str, ...]
            if shape_types == {TopAbs_FACE}:
                cylindrical_refs, geometry_reasons = _cylindrical_references_from_shapes(
                    shapes, noun="diameter", frame=frame
                )
                rendering_blockers = _diameter_reference_blockers(
                    cylindrical_refs, record.value, geometry_reasons
                )
                points = tuple(reference.midpoint for reference in cylindrical_refs)
                axes = {reference.principal_axis for reference in cylindrical_refs}
            elif shape_types == {TopAbs_EDGE}:
                circular_refs, geometry_reasons = _circular_references_from_shapes(
                    shapes, noun="diameter", frame=frame
                )
                rendering_blockers = _circular_diameter_blockers(
                    circular_refs, record.value, geometry_reasons
                )
                points = tuple(reference.center for reference in circular_refs)
                axes = {reference.principal_axis for reference in circular_refs}
            else:
                geometry_reasons = ("diameter support groups mix face and edge topology",)
                rendering_blockers = geometry_reasons
                points = ()
                axes = set()
            blockers = tuple(dict.fromkeys((*topology_reasons, *geometry_reasons)))
            projected.append(
                replace(
                    record,
                    ref_pts=points,
                    ref_bbox=ref_bbox,
                    dominant_axis=(
                        next(iter(axes)) if len(axes) == 1 and "?" not in axes else "?"
                    ),
                    lowering_blockers=tuple(
                        dict.fromkeys(
                            (
                                *_without_direct_xcaf_diameter_failures(record.lowering_blockers),
                                *blockers,
                            )
                        )
                    ),
                    rendering_blockers=rendering_blockers,
                    cylindrical_refs=cylindrical_refs,
                    circular_refs=circular_refs,
                )
            )
            continue

        points, dominant_axis, station_reasons = _linear_reference_stations(
            tuple(stations), record.value
        )
        if record.kind == "thickness":
            station_reasons = tuple(
                reason.replace("linear dimension", "thickness dimension").replace(
                    "linear reference", "thickness reference"
                )
                for reason in station_reasons
            )
        projected.append(
            replace(
                record,
                ref_pts=points,
                ref_bbox=ref_bbox,
                dominant_axis=dominant_axis,
                lowering_blockers=tuple(
                    dict.fromkeys(
                        (
                            *_without_direct_xcaf_reference_failures(record.lowering_blockers),
                            *topology_reasons,
                        )
                    )
                ),
                rendering_blockers=_dimension_geometry_blockers(
                    record.kind, tuple(topology_reasons), station_reasons
                ),
            )
        )
    return tuple(projected)


def _face_topology_witness(shape, frame: PartFrame | None = None) -> tuple[float, float, float]:
    """Choose a stable point proven to belong to one exact transferred face boundary."""
    vertices = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, vertices)
    if vertices.Extent() == 0:
        raise ValueError("face has no topological vertices")
    candidates = []
    for index in range(1, vertices.Extent() + 1):
        point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertices.FindKey(index)))
        candidates.append(_frame_point((point.X(), point.Y(), point.Z()), frame))
    bbox = _shape_bbox(shape) if frame is None else _shape_bbox(shape, frame)
    center = _bbox_centroid(bbox)
    return min(
        candidates,
        key=lambda point: sum((point[index] - center[index]) ** 2 for index in range(3)),
    )


def _extract_pmi_report(
    step_file: str | Path, *, frame: PartFrame | None = None
) -> PmiExtractionReport:
    """Implementation behind the extraction-scoped Part21 read session."""
    census = _extract_pmi_census(step_file, frame=frame)
    try:
        path = Path(step_file)
        name, digest = path.name, hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:  # unreadable, a directory, gone between read and hash
        _log.debug("PMI provenance unavailable for %s: %s", step_file, exc)
        return census
    return replace(census, source_name=name, source_sha256=digest)


def _enrich_part21_topology(
    requirement_records: tuple[PmiRecord, ...],
    label_records: tuple[PmiRecord, ...],
    reader: STEPCAFControl_Reader,
    frame: PartFrame | None,
) -> tuple[tuple[PmiRecord, ...], tuple[PmiRecord, ...]]:
    """Enrich independent Part21 projections after XCAF transfer."""
    try:
        if frame is None:
            requirement_records = _manufacturing_requirement_topology(
                requirement_records, reader.Reader()
            )
        else:
            requirement_records = _manufacturing_requirement_topology(
                requirement_records, reader.Reader(), frame
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

    try:
        if frame is None:
            label_records = _surface_label_topology(label_records, reader.Reader())
        else:
            label_records = _surface_label_topology(label_records, reader.Reader(), frame)
    except Exception as exc:
        reason = f"surface-label topology is unavailable ({_failure_reason(exc)})"
        label_records = tuple(
            replace(
                record,
                lowering_blockers=tuple(dict.fromkeys((*record.lowering_blockers, reason))),
            )
            for record in label_records
        )

    return requirement_records, label_records


def _extract_xcaf_dimensions(
    step_file: str | Path,
    dt: XCAFDoc_DimTolTool,
    shape_tool: Any,
    reader: STEPCAFControl_Reader,
    frame: PartFrame | None,
) -> tuple[list[PmiRecord], list[PmiSourceEntity]]:
    """Extract dimension occurrences and reconcile their imported supports."""
    records: list[PmiRecord] = []
    sources: list[PmiSourceEntity] = []
    # ---- Dimensions --------------------------------------------------------
    dims = TDF_LabelSequence()
    dt.GetDimensionLabels(dims)
    length_factor_mm = 1.0
    length_factor_reason = ""
    dimension_display_facts: tuple[DimensionDisplayFact, ...] = ()
    dimension_association_facts: tuple[DimensionAssociationFact, ...] = ()
    dimension_association_error = ""
    common_label_facts: tuple[CommonLabelFact, ...] = ()
    common_label_error = ""
    if dims.Length() > 0:
        try:
            candidate, length_factor_reason = read_dimension_length_factor(step_file)
        except Exception as exc:
            length_factor_reason = (
                f"authored length-dimension unit is unavailable ({_failure_reason(exc)})"
            )
        else:
            if candidate is not None:
                length_factor_mm = candidate
                length_factor_reason = ""
        try:
            dimension_display_facts = read_dimension_display_facts(step_file)
        except Exception as exc:
            _log.debug(
                "PMI dimension display metadata unavailable for %s: %s",
                Path(step_file).name,
                exc,
            )
        try:
            dimension_association_facts = read_dimension_associations(step_file)
        except Exception as exc:
            dimension_association_error = (
                f"Part21 dimension-association read failed: {_failure_reason(exc)}"
            )
        try:
            common_label_facts = read_common_labels(step_file)
        except Exception as exc:
            common_label_error = f"Part21 common-label read failed: {_failure_reason(exc)}"
    for index in range(1, dims.Length() + 1):
        label = dims.Value(index)
        source_id = _source_id("dimension", label)
        type_code: int | None = None
        partial_reasons: tuple[str, ...] = ()
        try:
            obj = XCAFDoc_Dimension.Set_s(label).GetObject()
            type_code = int(obj.GetType())
            presentation = obj.GetPresentationName()
            presentation_name = (
                str(presentation.ToCString()).strip() if presentation is not None else ""
            )
            if type_code == 30:
                common_fact = None
                match_reason = common_label_error
                if not match_reason:
                    common_fact, match_reason = match_common_label(
                        common_label_facts, presentation_name
                    )
                if common_fact is None:
                    sources.append(
                        PmiSourceEntity(
                            source_id,
                            "dimension",
                            type_code,
                            "not_extracted",
                            match_reason,
                        )
                    )
                    continue
                blockers = (common_fact.reason,) if common_fact.reason else ()
                record = PmiRecord(
                    kind="common_label",
                    type_code=type_code,
                    value=0.0,
                    label=common_fact.text,
                    source_id=source_id,
                    part21_id=common_fact.entity_id,
                    source_category="dimension",
                    lowering_blockers=blockers,
                    source_ids=(source_id,),
                    reference_item_ids=common_fact.reference_item_ids,
                    shape_aspect_ids=(
                        (common_fact.shape_aspect_id,) if common_fact.shape_aspect_id else ()
                    ),
                )
                partial_reasons = blockers
            else:
                without_record = _dimension_without_record(source_id, type_code)
                if without_record is not None:
                    sources.append(without_record)
                    continue
            if type_code == 30:
                pass
            else:
                association_fact = None
                association_reason = ""
                dimension_kind = _DIM_TYPE.get(type_code, f"type{type_code}")
                # This overlay enriches XCAF's already-valid direct reference geometry. An
                # unnamed dimension cannot be correlated to Part21, but that absence does not
                # invalidate geometry XCAF did transfer. Once XCAF exposes a presentation
                # identity, however, require its Part21 association to be unambiguous.
                if presentation_name and (
                    dimension_kind in _LENGTH_DIMENSION_KINDS or type_code == 28
                ):
                    association_reason = dimension_association_error
                    if not association_reason:
                        association_fact, association_reason = match_dimension_association(
                            dimension_association_facts, presentation_name
                        )
                if frame is None:
                    record, partial_reasons = _dimension_record(
                        label,
                        obj,
                        type_code,
                        shape_tool,
                        source_id,
                        length_factor_mm=length_factor_mm,
                        length_factor_reason=length_factor_reason,
                        display_facts=dimension_display_facts,
                        association_fact=association_fact,
                        association_reason=association_reason,
                    )
                else:
                    record, partial_reasons = _dimension_record(
                        label,
                        obj,
                        type_code,
                        shape_tool,
                        source_id,
                        frame,
                        length_factor_mm=length_factor_mm,
                        length_factor_reason=length_factor_reason,
                        display_facts=dimension_display_facts,
                        association_fact=association_fact,
                        association_reason=association_reason,
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

    try:
        if frame is None:
            records = list(_dimension_support_topology(records, reader.Reader()))
        else:
            records = list(_dimension_support_topology(records, reader.Reader(), frame))
    except Exception as exc:
        reason = f"dimension support topology is unavailable ({_failure_reason(exc)})"
        records = [
            replace(
                record,
                lowering_blockers=tuple(dict.fromkeys((*record.lowering_blockers, reason))),
            )
            if record.kind in ("linear", "thickness", "diameter", "angular")
            and record.reference_item_groups
            else record
            for record in records
        ]
    dimension_records = {
        record.source_id: record
        for record in records
        if record.kind in ("linear", "thickness", "diameter", "angular")
        and record.reference_item_groups
    }
    sources = [
        replace(
            source,
            outcome=(
                "partially_extracted"
                if (
                    dimension_records[source.source_id].lowering_blockers
                    or dimension_records[source.source_id].rendering_blockers
                )
                else "extracted"
            ),
            reason="; ".join(
                dict.fromkeys(
                    (
                        *dimension_records[source.source_id].lowering_blockers,
                        *dimension_records[source.source_id].rendering_blockers,
                    )
                )
            ),
        )
        if source.source_id in dimension_records
        else source
        for source in sources
    ]

    try:
        if frame is None:
            records = list(_common_label_topology(records, reader.Reader()))
        else:
            records = list(_common_label_topology(records, reader.Reader(), frame))
    except Exception as exc:
        reason = f"common-label topology is unavailable ({_failure_reason(exc)})"
        records = [
            replace(
                record,
                lowering_blockers=tuple(dict.fromkeys((*record.lowering_blockers, reason))),
            )
            if record.kind == "common_label"
            else record
            for record in records
        ]
    common_label_records = {
        record.source_id: record for record in records if record.kind == "common_label"
    }
    sources = [
        replace(
            source,
            outcome="partially_extracted",
            reason="; ".join(common_label_records[source.source_id].lowering_blockers),
        )
        if source.source_id in common_label_records
        and common_label_records[source.source_id].lowering_blockers
        else source
        for source in sources
    ]

    return records, sources


def _extract_xcaf_tolerances(
    step_file: str | Path,
    dt: XCAFDoc_DimTolTool,
    shape_tool: Any,
    frame: PartFrame | None,
) -> tuple[list[PmiRecord], list[PmiSourceEntity], int]:
    """Extract geometric-tolerance occurrences in source order."""
    records: list[PmiRecord] = []
    sources: list[PmiSourceEntity] = []
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
            arguments = (
                label,
                obj,
                tolerance_type_code,
                shape_tool,
                dt,
                source_id,
                part21_facts,
                part21_error,
            )
            if frame is None:
                record, partial_reasons = _geometric_tolerance_record(*arguments)
            else:
                record, partial_reasons = _geometric_tolerance_record(*arguments, frame)
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

    return records, sources, tolerances.Length()


@dataclass(frozen=True)
class _DatumExtractionState:
    dt: XCAFDoc_DimTolTool
    shape_tool: Any
    frame: PartFrame | None
    facts: tuple[DatumOccurrenceFact, ...]
    definitions: tuple[DatumDefinitionFact, ...]
    part21_error: str
    topology: Any
    topology_error: str


def _datum_extraction_state(step_file, dt, shape_tool, reader, frame):
    """Read datum source facts and prepare their one imported-topology resolver."""
    datums = TDF_LabelSequence()
    dt.GetDatumLabels(datums)
    datum_facts: tuple[DatumOccurrenceFact, ...] = ()
    datum_definitions: tuple[DatumDefinitionFact, ...] = ()
    datum_part21_error = ""
    datum_topology = None
    datum_topology_error = ""
    if datums.Length() > 0:
        try:
            datum_facts = read_datum_occurrences(step_file)
        except Exception as exc:
            datum_part21_error = f"Part21 datum read failed: {_failure_reason(exc)}"
            _log.debug("PMI datum overlay unavailable for %s: %s", Path(step_file).name, exc)
    try:
        datum_definitions = read_datum_definitions(step_file)
    except Exception as exc:
        _log.debug("PMI datum definitions unavailable for %s: %s", Path(step_file).name, exc)
    if datum_facts or datum_definitions:
        try:
            step_reader = reader.Reader()
            imported_faces = TopTools_IndexedMapOfShape()
            TopExp.MapShapes_s(step_reader.OneShape(), TopAbs_FACE, imported_faces)
            datum_topology = _DatumTopologyResolver(step_reader, imported_faces)
        except Exception as exc:
            datum_topology_error = (
                f"datum imported-topology map is unavailable ({_failure_reason(exc)})"
            )
    return datums, _DatumExtractionState(
        dt,
        shape_tool,
        frame,
        datum_facts,
        datum_definitions,
        datum_part21_error,
        datum_topology,
        datum_topology_error,
    )


def _xcaf_datum_occurrence(label, source_id: str, state: _DatumExtractionState):
    """Correlate one XCAF occurrence with its Part21 definition and exact support."""
    letter, letter_reason = _datum_letter(label)
    definition, definition_reason = (
        _datum_definition(letter, state.definitions) if not letter_reason else (None, "")
    )
    fact: DatumOccurrenceFact | DatumDefinitionFact | None = definition
    context, context_reason = "", ""
    correspondence_reason = definition_reason
    contexts: tuple[str, ...] = ()
    if definition is not None:
        # Context is optional provenance. A writer may attach this same datum to
        # several tolerances (or none), and their names are not its identity.
        context, _ignored_context_reason = _datum_context(label, state.dt)
        contexts = (context,) if context else ()
    elif not definition_reason:
        # Older files can have an incomplete standalone definition but an exact
        # named use. Retain that correspondence as a compatibility fallback.
        context, context_reason = _datum_context(label, state.dt)
        correspondence_reason = state.part21_error
        if not correspondence_reason and not letter_reason and not context_reason:
            fact, correspondence_reason = match_datum_occurrence(state.facts, context, letter)
        contexts = (context,) if context else ()
    if state.frame is None:
        datum_geometry = _datum_reference_geometry(label, state.shape_tool)
    else:
        datum_geometry = _datum_reference_geometry(label, state.shape_tool, state.frame)
    xcaf_shapes = _datum_reference_shapes(label, state.shape_tool)
    points, ref_bbox, reference_axis, geometry_reasons = datum_geometry
    mismatch_id = ""
    if fact is not None and fact.reference_item_ids:
        if state.topology is None:
            topology_shapes = ()
            topology_reasons = (state.topology_error or state.part21_error,)
        else:
            topology_shapes, topology_reasons = state.topology.resolve(
                fact.datum_feature_id, fact.reference_item_ids
            )
        if topology_shapes:
            if state.frame is None:
                datum_geometry = _datum_geometry_from_shapes(topology_shapes)
            else:
                datum_geometry = _datum_geometry_from_shapes(topology_shapes, state.frame)
            matched_points, matched_bbox, matched_axis, matched_reasons = datum_geometry
            # An XCAF occurrence with no face claim can use its unique authored
            # Part21 definition. A present but unmeasurable XCAF face cannot.
            unlocated_correspondence = not xcaf_shapes
            if not unlocated_correspondence and not _same_datum_support(
                xcaf_shapes,
                topology_shapes,
                ref_bbox,
                reference_axis,
                matched_bbox,
                matched_axis,
            ):
                if (
                    ref_bbox is None
                    or reference_axis in ("", "?")
                    or matched_bbox is None
                    or matched_axis in ("", "?")
                ):
                    reason = "datum occurrence support cannot be matched to Part21 definition"
                else:
                    mismatch_id = fact.datum_feature_id
                    reason = (
                        "datum definition support disagrees with XCAF"
                        if definition is not None
                        else "datum occurrence support disagrees with XCAF"
                    )
                geometry_reasons = tuple(
                    dict.fromkeys((*geometry_reasons, *matched_reasons, reason))
                )
                fact = None
            else:
                points, ref_bbox, reference_axis = matched_points, matched_bbox, matched_axis
                geometry_reasons = (
                    matched_reasons
                    if unlocated_correspondence
                    else tuple(dict.fromkeys((*geometry_reasons, *matched_reasons)))
                )
        else:
            geometry_reasons = tuple(dict.fromkeys((*geometry_reasons, *topology_reasons)))
    blockers = tuple(
        dict.fromkeys(
            reason
            for reason in (letter_reason, context_reason, correspondence_reason, *geometry_reasons)
            if reason
        )
    )
    record = PmiRecord(
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
        datum_contexts=contexts,
        reference_item_ids=fact.reference_item_ids if fact is not None else (),
        reference_axis=reference_axis,
    )
    return record, mismatch_id


def _unrepresented_datum_definition(
    definition: DatumDefinitionFact, state: _DatumExtractionState, mismatched: set[str]
):
    """Retain a definition that no XCAF occurrence represented, including its blockers."""
    definition_id = definition.datum_feature_id or definition.datum_id
    source_id = f"datum_definition:{definition.datum_id}"
    definition_blockers = [definition.reason] if definition.reason else []
    if definition_id in mismatched:
        definition_blockers.append("datum definition support disagrees with XCAF")
    definition_points: tuple[tuple[float, float, float], ...] = ()
    definition_bbox = None
    definition_axis = ""
    if not definition_blockers:
        if state.topology is None:
            definition_blockers.append(state.topology_error or state.part21_error)
        else:
            topology_shapes, topology_reasons = state.topology.resolve(
                definition_id, definition.reference_item_ids
            )
            definition_blockers.extend(topology_reasons)
            if topology_shapes:
                if state.frame is None:
                    datum_geometry = _datum_geometry_from_shapes(topology_shapes)
                else:
                    datum_geometry = _datum_geometry_from_shapes(topology_shapes, state.frame)
                definition_points, definition_bbox, definition_axis, geometry_reasons = (
                    datum_geometry
                )
                definition_blockers.extend(geometry_reasons)
    if not definition.letter:
        definition_blockers.append("datum definition has no letter")
    unique_blockers = tuple(dict.fromkeys(reason for reason in definition_blockers if reason))
    record = PmiRecord(
        kind="datum",
        type_code=None,
        value=0.0,
        ref_pts=definition_points,
        ref_bbox=definition_bbox,
        dominant_axis=definition_axis or "?",
        label=definition.letter,
        source_id=source_id,
        part21_id=definition_id,
        source_category="datum",
        lowering_blockers=unique_blockers,
        source_ids=(source_id,),
        reference_item_ids=definition.reference_item_ids,
        reference_axis=definition_axis,
    )
    source = PmiSourceEntity(
        source_id,
        "datum",
        None,
        "partially_extracted" if unique_blockers else "extracted",
        "; ".join(unique_blockers),
    )
    return record, source


def _extract_xcaf_datums(
    step_file: str | Path,
    dt: XCAFDoc_DimTolTool,
    shape_tool: Any,
    reader: STEPCAFControl_Reader,
    frame: PartFrame | None,
) -> tuple[list[PmiRecord], list[PmiSourceEntity]]:
    """Extract datum occurrences and unrepresented definitions."""
    datums, state = _datum_extraction_state(step_file, dt, shape_tool, reader, frame)
    sources: list[PmiSourceEntity] = []
    datum_records: list[PmiRecord] = []
    mismatched_definitions: set[str] = set()
    for index in range(1, datums.Length() + 1):
        label = datums.Value(index)
        source_id = _source_id("datum", label)
        try:
            record, mismatch_id = _xcaf_datum_occurrence(label, source_id, state)
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
            datum_records.append(record)
            if mismatch_id:
                mismatched_definitions.add(mismatch_id)
            blockers = record.lowering_blockers
            sources.append(
                PmiSourceEntity(
                    source_id,
                    "datum",
                    None,
                    "partially_extracted" if blockers else "extracted",
                    "; ".join(blockers),
                )
            )
    represented_definitions = {record.part21_id for record in datum_records if record.part21_id}
    for definition in state.definitions:
        definition_id = definition.datum_feature_id or definition.datum_id
        if definition_id in represented_definitions:
            continue
        record, source = _unrepresented_datum_definition(definition, state, mismatched_definitions)
        datum_records.append(record)
        sources.append(source)
    records = list(_coalesce_datum_records(datum_records))

    return records, sources


def _log_pmi_census(
    step_file: str | Path,
    sources: list[PmiSourceEntity],
    tolerance_count: int,
) -> None:
    """Log source-outcome totals from the completed census."""
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
    datum_source_count = sum(source.category == "datum" for source in sources)
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
        tolerance_count,
        partial_tolerances,
        extracted_datums,
        datum_source_count,
        partial_datums,
        extracted_requirements,
        requirement_source_count,
        partial_requirements,
    )


def _extract_pmi_census(
    step_file: str | Path, *, frame: PartFrame | None = None
) -> PmiExtractionReport:
    """Inventory and extract semantic PMI from an AP242 STEP file in one XCAF pass.

    The report retains one source outcome for every dimension, geometric tolerance,
    datum-reference occurrence, standalone datum definition, associated surface label, and
    semantic manufacturing requirement. Graphical
    presentation-only dimension labels are inventoried but are not manufacturing requirements.
    Repeated datum occurrences project onto their authored datum-feature definition without
    shrinking the source denominator.

    Returns an empty report (with a report-level error where applicable) when no source
    identities can be recovered and:

    - the file contains neither XCAF GDT data nor semantic Part21 annotations;
    - the file uses AP203/AP214 which carry no semantic PMI.

    Part21-only manufacturing requirements and surface labels remain inventoried even when
    OCP's GDT support is unavailable or the XCAF transfer fails; the report also retains that
    global XCAF error.

    Geometry evidence is returned in global STEP coordinates by default. Passing ``frame``
    expresses points, vectors, boxes, cylinders, and datum geometry in that frame's local
    coordinates before principal-axis relationships are validated. Scalars and source
    identities are unchanged.

    Does **not** modify the solid geometry — purely a read-only second pass.
    """
    try:
        material_facts = read_material_properties(step_file)
        material_error = ""
    except Exception as exc:
        material_facts = ()
        material_error = f"{type(exc).__name__}: {exc}"
    requirement_sources, requirement_records = _manufacturing_requirement_projection(step_file)
    label_sources, label_records = _surface_label_projection(step_file)

    def failed(reason: str) -> PmiExtractionReport:
        return PmiExtractionReport(
            sources=(*requirement_sources, *label_sources),
            records=(*requirement_records, *label_records),
            error=reason,
            material_facts=material_facts,
            material_error=material_error,
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

    requirement_records, label_records = _enrich_part21_topology(
        requirement_records, label_records, reader, frame
    )

    records, sources = _extract_xcaf_dimensions(step_file, dt, shape_tool, reader, frame)

    tolerance_records, tolerance_sources, tolerance_count = _extract_xcaf_tolerances(
        step_file, dt, shape_tool, frame
    )
    records.extend(tolerance_records)
    sources.extend(tolerance_sources)

    datum_records, datum_sources = _extract_xcaf_datums(step_file, dt, shape_tool, reader, frame)
    records.extend(datum_records)
    sources.extend(datum_sources)

    # XCAF exposes neither authoritative descriptive text nor its shape-aspect association.
    # The independent Part21 projection was collected before XCAF so it survives every early
    # transfer failure; append it after XCAF categories to keep the established report order.
    sources.extend(requirement_sources)
    records.extend(requirement_records)
    sources.extend(label_sources)
    records.extend(label_records)

    _log_pmi_census(step_file, sources, tolerance_count)
    return PmiExtractionReport(
        sources=tuple(sources),
        records=tuple(records),
        material_facts=material_facts,
        material_error=material_error,
    )


def extract_pmi_report(
    step_file: str | Path, *, frame: PartFrame | None = None
) -> PmiExtractionReport:
    """Inventory and extract semantic PMI, crediting the document it was read from.

    The Part21 overlay is parsed once for this extraction. Provenance is stamped once around
    the census's many exits so no result can silently claim a different source document. A
    digest failure leaves a readable census intact but unattributed.
    """

    with part21_read_session():
        return _extract_pmi_report(step_file, frame=frame)


def extract_pmi(step_file: str | Path, *, frame: PartFrame | None = None) -> list[PmiRecord]:
    """Return the successful-record projection of :func:`extract_pmi_report`.

    This compatibility surface deliberately remains a list. Callers that need to know what
    the source contained or why a record is absent must use :func:`extract_pmi_report`.
    """
    if frame is None:
        return list(extract_pmi_report(step_file).records)
    return list(extract_pmi_report(step_file, frame=frame).records)
