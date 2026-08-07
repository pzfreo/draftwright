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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OCP capability guard
# ---------------------------------------------------------------------------

try:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
    from OCP.TDF import TDF_LabelSequence, TDF_Tool
    from OCP.TDocStd import TDocStd_Document
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


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PmiRecord:
    """One semantic PMI annotation from an AP242 STEP file.

    Attributes:
        kind:           Human-readable category (``"linear"``, ``"diameter"``,
                        ``"angular"``, ``"gtol"``, ``"datum"``).
        type_code:      Raw OCCT enum integer.
        value:          Nominal value in mm (or degrees for angular).
        upper_tol:      Upper tolerance in mm, or ``None``.
        lower_tol:      Lower tolerance in mm, or ``None``.
        lower_bound:    Lower limit of a range dimension, in the same units as ``value``.
        upper_bound:    Upper limit of a range dimension, in the same units as ``value``.
        ref_pts:        Bounding-box centroids of referenced geometry in global
                        STEP space (same coordinate frame as the imported solid).
        ref_bbox:       Combined axis-aligned bbox of ALL referenced shapes:
                        ``(xmin, ymin, zmin, xmax, ymax, zmax)``.  Used by
                        ``_annotate_pmi`` for witness-point placement — the outer
                        edges of the referenced geometry give the correct measurement
                        span rather than the shorter centroid-to-centroid distance.
        dominant_axis:  ``'X'``, ``'Y'``, ``'Z'``, or ``'?'`` — the direction
                        in which the dimension primarily spans (based on the outer
                        bbox extent, not the centroid difference).
        label:          Ready-to-use annotation label (e.g. ``"ø35"``, ``"60"``).
        datum_refs:     Ordered datum letters referenced by a geometric tolerance.
    """

    kind: str
    type_code: int
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


PmiSourceCategory = Literal["dimension", "geometric_tolerance", "datum"]
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


def _reference_geometry(label, shape_tool):
    """Measure the geometry relationships shared by dimensions and tolerances."""
    first_refs = TDF_LabelSequence()
    second_refs = TDF_LabelSequence()
    XCAFDoc_DimTolTool.GetRefShapeLabel_s(label, first_refs, second_refs)
    points: list[tuple[float, float, float]] = []
    boxes: list[tuple[float, float, float, float, float, float]] = []
    partial_reasons: list[str] = []
    reference_count = first_refs.Length() + second_refs.Length()
    for refs in (first_refs, second_refs):
        for index in range(1, refs.Length() + 1):
            shape = shape_tool.GetShape_s(refs.Value(index))
            if shape is None or shape.IsNull():
                partial_reasons.append("one referenced shape is unavailable")
                continue
            try:
                bbox = _shape_bbox(shape)
                boxes.append(bbox)
                points.append(_bbox_centroid(bbox))
            except Exception as exc:
                partial_reasons.append(
                    f"one referenced shape could not be measured ({_failure_reason(exc)})"
                )
    if reference_count == 0:
        partial_reasons.append("referenced geometry is unavailable")

    ref_bbox = _merge_bboxes(boxes) if boxes else None
    dominant_axis = _dominant_from_bbox(ref_bbox) if ref_bbox else "?"
    return (
        tuple(points),
        ref_bbox,
        dominant_axis,
        tuple(dict.fromkeys(partial_reasons)),
    )


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

    # The outer edges of the referenced geometry give the correct measurement span (e.g.
    # the two far sides of a diameter) rather than the shorter centroid-to-centroid distance.
    points, ref_bbox, dominant_axis, reference_reasons = _reference_geometry(label, shape_tool)
    partial_reasons.extend(reference_reasons)
    kind = _DIM_TYPE.get(type_code, f"type{type_code}")
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
        ),
        tuple(dict.fromkeys(partial_reasons)),
    )


def _geometric_tolerance_record(
    label, obj, type_code: int, shape_tool, dim_tol_tool, source_id: str
) -> tuple[PmiRecord, tuple[str, ...]]:
    """Convert the XCAF-owned fields of one geometric tolerance."""
    value = float(obj.GetValue())
    kind = _GTOL_TYPE.get(type_code, f"gtol{type_code}")
    partial_reasons: list[str] = []
    if type_code not in _GTOL_TYPE:
        partial_reasons.append(f"geometric-tolerance type {type_code} is unsupported")
    if value <= 0:
        partial_reasons.append("tolerance magnitude is unavailable")
    points, ref_bbox, dominant_axis, reference_reasons = _reference_geometry(label, shape_tool)
    partial_reasons.extend(reference_reasons)
    datum_refs, datum_reasons = _datum_references(label, dim_tol_tool)
    partial_reasons.extend(datum_reasons)
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
        ),
        tuple(dict.fromkeys(partial_reasons)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_pmi_report(step_file: str | Path) -> PmiExtractionReport:
    """Inventory and extract semantic PMI from an AP242 STEP file in one XCAF pass.

    The report retains one source outcome for every dimension, geometric tolerance and
    datum label. Graphical presentation-only dimension labels are inventoried but are not
    manufacturing requirements. Datums are currently reported as ``not_extracted`` rather
    than disappearing from the records-only projection.

    Returns an empty report (with a report-level error where applicable) when:

    - the file contains no GDT data;
    - OCP's GDT support is unavailable (``_PMI_AVAILABLE`` is False);
    - the file uses AP203/AP214 which carry no semantic PMI.

    Does **not** modify the solid geometry — purely a read-only second pass.
    """
    if not _PMI_AVAILABLE:
        reason = "OCP SetGDTMode is unavailable"
        _log.debug("PMI extraction unavailable (%s)", reason)
        return PmiExtractionReport(error=reason)

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
        return PmiExtractionReport(error=reason)
    if status != IFSelect_RetDone:
        reason = f"ReadFile failed with status {status}"
        _log.warning("PMI extraction: %s for %s", reason, Path(step_file).name)
        return PmiExtractionReport(error=reason)
    try:
        transferred = reader.Transfer(doc)
    except Exception as exc:
        reason = f"Transfer failed: {_failure_reason(exc)}"
        _log.warning("PMI extraction: %s for %s", reason, Path(step_file).name)
        return PmiExtractionReport(error=reason)
    if transferred is False:
        reason = "Transfer failed"
        _log.warning("PMI extraction: %s for %s", reason, Path(step_file).name)
        return PmiExtractionReport(error=reason)

    main = doc.Main()
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(main)
    dt = XCAFDoc_DocumentTool.DimTolTool_s(main)

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
    for index in range(1, tolerances.Length() + 1):
        label = tolerances.Value(index)
        source_id = _source_id("geometric_tolerance", label)
        tolerance_type_code: int | None = None
        try:
            obj = XCAFDoc_GeomTolerance.Set_s(label).GetObject()
            tolerance_type_code = int(obj.GetType())
            record, partial_reasons = _geometric_tolerance_record(
                label, obj, tolerance_type_code, shape_tool, dt, source_id
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
    # XCAF discovers these entities, but the current extractor creates no record for them.
    # Keeping each source identity and explicit failure is the first #623 vertical slice;
    # datum concept lowering follows separately rather than hiding the entire category.
    datums = TDF_LabelSequence()
    dt.GetDatumLabels(datums)
    for index in range(1, datums.Length() + 1):
        source_id = _source_id("datum", datums.Value(index))
        sources.append(
            PmiSourceEntity(
                source_id=source_id,
                category="datum",
                type_code=None,
                outcome="not_extracted",
                reason="datum extraction is not implemented",
            )
        )

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

    _log.info(
        "PMI extracted from %s: %d/%d complete semantic dims (%d partial, "
        "%d presentation-only), "
        "%d/%d complete gtols (%d partial), 0/%d datums",
        Path(step_file).name,
        extracted_dimensions,
        semantic_dimensions,
        partial_dimensions,
        presentation_dimensions,
        extracted_tolerances,
        tolerances.Length(),
        partial_tolerances,
        datums.Length(),
    )
    return PmiExtractionReport(sources=tuple(sources), records=tuple(records))


def extract_pmi(step_file: str | Path) -> list[PmiRecord]:
    """Return the successful-record projection of :func:`extract_pmi_report`.

    This compatibility surface deliberately remains a list. Callers that need to know what
    the source contained or why a record is absent must use :func:`extract_pmi_report`.
    """
    return list(extract_pmi_report(step_file).records)
