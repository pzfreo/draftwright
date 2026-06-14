"""PMI (Product Manufacturing Information) extractor for AP242 STEP files.

Reads semantic PMI from an ISO 10303-242 STEP file via a second
``STEPCAFControl_Reader`` pass with ``SetGDTMode(True)``.  Returns a list of
:class:`PmiRecord` objects that ``_annotate_pmi`` in ``make_drawing.py`` turns
into drawing annotations.

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
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OCP capability guard
# ---------------------------------------------------------------------------

try:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import (
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
    0: "location",       # Location_None
    1: "curved_dist",    # Location_CurvedDistance
    2: "linear",         # Location_LinearDistance (outer-to-outer, generic)
    3: "linear",         # FromCenterToOuter
    4: "linear",         # FromCenterToInner
    5: "linear",         # FromOuterToCenter
    6: "linear",         # FromOuterToOuter
    7: "linear",         # FromOuterToInner
    8: "linear",         # FromInnerToCenter
    9: "linear",         # FromInnerToOuter
    10: "linear",        # FromInnerToInner
    11: "angular",       # Location_Angular (incl. curved centre-to-centre)
    12: "oriented",      # Location_Oriented
    14: "curve_length",  # Size_CurveLength
    15: "diameter",      # Size_Diameter  ← add ø prefix
    16: "diameter",      # Size_SphericalDiameter
    17: "radius",        # Size_Radius    ← add R prefix
    18: "radius",        # Size_SphericalRadius
    27: "thickness",     # Size_Thickness
    28: "angular",       # Size_Angular
    30: "label",         # CommonLabel     ← no numeric value, skip
    31: "presentation",  # DimensionPresentation ← graphical only, skip
}

# Types whose GetValue() is a meaningful length/angle (skip label/presentation)
_SKIP_TYPES = {30, 31}

# prefix character for the label
_DIM_PREFIX: dict[str, str] = {
    "diameter": "ø",
    "radius": "R",
}

# int → short tag for XCAFDimTolObjects_GeomToleranceType
_GTOL_TYPE: dict[int, str] = {
    1: "straightness",
    2: "flatness",
    3: "circularity",
    4: "cylindricity",
    5: "profile_line",
    6: "profile_surface",
    7: "perpendicularity",
    8: "angularity",
    9: "parallelism",
    10: "position",
    11: "concentricity",
    12: "symmetry",
    13: "circular_runout",
    14: "total_runout",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PmiRecord:
    """One semantic PMI annotation from an AP242 STEP file.

    Attributes:
        kind:           Human-readable category (``"linear"``, ``"diameter"``,
                        ``"angular"``, ``"gtol"``, ``"datum"``).
        type_code:      Raw OCCT enum integer.
        value:          Nominal value in mm (or degrees for angular).
        upper_tol:      Upper tolerance in mm, or ``None``.
        lower_tol:      Lower tolerance in mm, or ``None``.
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
    """

    kind: str
    type_code: int
    value: float
    upper_tol: float | None = None
    lower_tol: float | None = None
    ref_pts: list[tuple[float, float, float]] = field(default_factory=list)
    ref_bbox: tuple[float, float, float, float, float, float] | None = None
    dominant_axis: str = "?"
    label: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shape_bbox(shape) -> tuple[float, float, float, float, float, float]:
    """Return ``(xmin, ymin, zmin, xmax, ymax, zmax)`` of *shape* in global space."""
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    return bb.Get()


def _bbox_centroid(bbox: tuple) -> tuple[float, float, float]:
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    return ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)


def _merge_bboxes(
    boxes: list[tuple[float, float, float, float, float, float]]
) -> tuple[float, float, float, float, float, float]:
    """Return the combined axis-aligned bbox of *boxes*."""
    xs = [b[0] for b in boxes] + [b[3] for b in boxes]
    ys = [b[1] for b in boxes] + [b[4] for b in boxes]
    zs = [b[2] for b in boxes] + [b[5] for b in boxes]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _dominant_from_bbox(
    bbox: tuple[float, float, float, float, float, float]
) -> str:
    """Return ``'X'``/``'Y'``/``'Z'`` for the axis with the largest bbox extent."""
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    spans = [("X", abs(xmax - xmin)), ("Y", abs(ymax - ymin)), ("Z", abs(zmax - zmin))]
    dom = max(spans, key=lambda t: t[1])
    return dom[0] if dom[1] > 1e-6 else "?"


def _make_label(kind: str, value: float, upper_tol: float | None, lower_tol: float | None) -> str:
    """Format the annotation label with optional tolerance suffix."""
    from draftwright.make_drawing import _fmt  # local import to avoid circularity

    prefix = _DIM_PREFIX.get(kind, "")
    base = f"{prefix}{_fmt(value)}"
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_pmi(step_file: str | Path) -> list[PmiRecord]:
    """Extract semantic PMI from an AP242 STEP file.

    Returns an empty list (with a log message) when:

    - the file contains no GDT data;
    - OCP's GDT support is unavailable (``_PMI_AVAILABLE`` is False);
    - the file uses AP203/AP214 which carry no semantic PMI.

    Does **not** modify the solid geometry — purely a read-only second pass.
    """
    if not _PMI_AVAILABLE:
        _log.debug("PMI extraction unavailable (OCP SetGDTMode not found)")
        return []

    path = str(step_file)
    doc = TDocStd_Document(TCollection_ExtendedString("XCAF"))
    reader = STEPCAFControl_Reader()
    reader.SetGDTMode(True)
    reader.SetNameMode(True)
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        _log.warning("PMI extraction: ReadFile failed for %s (status=%s)", Path(step_file).name, status)
        return []
    reader.Transfer(doc)

    main = doc.Main()
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(main)
    dt = XCAFDoc_DocumentTool.DimTolTool_s(main)

    records: list[PmiRecord] = []

    # ---- Dimensions --------------------------------------------------------
    dims = TDF_LabelSequence()
    dt.GetDimensionLabels(dims)
    n_dims_ok = 0

    for i in range(1, dims.Length() + 1):
        lab = dims.Value(i)
        try:
            obj = XCAFDoc_Dimension.Set_s(lab).GetObject()
            tc = int(obj.GetType())
            if tc in _SKIP_TYPES:
                continue

            # Nominal value: scalar first, array fallback
            val: float = 0.0
            try:
                val = float(obj.GetValue())
            except Exception:
                try:
                    arr = obj.GetValues()
                    if arr is not None:
                        val = float(arr.Value(arr.Lower()))
                except Exception:
                    pass

            # Tolerances
            upper_tol: float | None = None
            lower_tol: float | None = None
            try:
                u = float(obj.GetUpperTolValue())
                if abs(u) > 1e-9:
                    upper_tol = u
            except Exception:
                pass
            try:
                lo = float(obj.GetLowerTolValue())
                if abs(lo) > 1e-9:
                    lower_tol = lo
            except Exception:
                pass

            # Referenced geometry → bboxes and centroids
            f_seq = TDF_LabelSequence()
            s_seq = TDF_LabelSequence()
            XCAFDoc_DimTolTool.GetRefShapeLabel_s(lab, f_seq, s_seq)
            pts: list[tuple[float, float, float]] = []
            raw_bboxes: list[tuple[float, float, float, float, float, float]] = []
            for seq in (f_seq, s_seq):
                for k in range(1, seq.Length() + 1):
                    shp = shape_tool.GetShape_s(seq.Value(k))
                    if shp is not None and not shp.IsNull():
                        try:
                            bb6 = _shape_bbox(shp)
                            raw_bboxes.append(bb6)
                            pts.append(_bbox_centroid(bb6))
                        except Exception:
                            pass

            # Combined bbox of all referenced shapes, used for witness placement.
            # The outer edges of the referenced geometry give the correct measurement
            # span (e.g., the two far sides of a ø35 bore) rather than the much
            # shorter centroid-to-centroid distance.
            ref_bbox = _merge_bboxes(raw_bboxes) if raw_bboxes else None
            dom = _dominant_from_bbox(ref_bbox) if ref_bbox else "?"

            kind = _DIM_TYPE.get(tc, f"type{tc}")
            lbl = _make_label(kind, val, upper_tol, lower_tol)
            records.append(
                PmiRecord(
                    kind=kind,
                    type_code=tc,
                    value=val,
                    upper_tol=upper_tol,
                    lower_tol=lower_tol,
                    ref_pts=pts,
                    ref_bbox=ref_bbox,
                    dominant_axis=dom,
                    label=lbl,
                )
            )
            n_dims_ok += 1
        except Exception as exc:
            _log.debug("PMI dim[%d] skipped: %s", i, exc)

    # ---- Geometric tolerances ----------------------------------------------
    gts = TDF_LabelSequence()
    dt.GetGeomToleranceLabels(gts)
    n_gtol_ok = 0

    for i in range(1, gts.Length() + 1):
        lab = gts.Value(i)
        try:
            obj = XCAFDoc_GeomTolerance.Set_s(lab).GetObject()
            tc = int(obj.GetType())
            val = float(obj.GetValue())
            kind = _GTOL_TYPE.get(tc, f"gtol{tc}")
            records.append(
                PmiRecord(
                    kind=kind,
                    type_code=tc,
                    value=val,
                    label=f"{kind} {val:.3g}" if val else kind,
                )
            )
            n_gtol_ok += 1
        except Exception as exc:
            _log.debug("PMI gtol[%d] skipped: %s", i, exc)

    _log.info(
        "PMI extracted from %s: %d/%d dims, %d/%d gtols",
        Path(step_file).name,
        n_dims_ok,
        dims.Length(),
        n_gtol_ok,
        gts.Length(),
    )
    return records
