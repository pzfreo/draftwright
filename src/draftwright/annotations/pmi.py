"""PMI annotation pass (#138 / ADR 0005, P5c).

Place dimensions/notes for the PMI (GD&T, semantic tolerances) extracted from a
STEP AP242 part — the *annotation* side (the extraction lives in draftwright.pmi).
Self-contained: its placement helpers are nested; imports stay below annotate.
"""

from __future__ import annotations

import math

from build123d_drafting.helpers import (
    Leader,
)

from draftwright._core import (
    Analysis,
    _dim,
    _log,
)


def _annotate_pmi(dwg, a: Analysis, draft) -> None:
    """Add PMI-derived dimension annotations to *dwg* using remaining strip space.

    Called from ``_auto_annotate`` after all automatic dimensions are placed so
    PMI dims consume the strips' leftover capacity.  Skips records whose page
    projection is degenerate (< 3 mm span) or whose extension lines would exceed
    twice the nominal value.

    View assignment:
    - dominant X → front view, fv_zones.above / fv_zones.below
    - dominant Z → front view, fv_zones.right / fv_zones.left
    - dominant Y → side view, sv_zones.above / sv_zones.below
                   (falls back to pv_zones.below for Y dims that are
                    too compressed in the side view)
    """
    pmi = a.pmi
    usable = [r for r in pmi if r.value > 0 and len(r.ref_pts) >= 2]
    n_gtol = sum(
        1
        for r in pmi
        if r.kind
        not in (
            "linear",
            "diameter",
            "radius",
            "angular",
            "curved_dist",
            "oriented",
            "curve_length",
            "thickness",
            "label",
            "presentation",
        )
        and r.value > 0
    )
    if n_gtol:
        _log.debug("PMI annotate: %d gtol/datum record(s) not yet annotatable (Phase 4)", n_gtol)
    if not usable:
        _log.info("PMI annotate: no usable records (value>0 with 2+ ref pts)")
        return

    FX = a.proj.front_x
    FZ = a.proj.front_z
    SX = a.proj.side_x
    SZ = a.proj.side_z
    PX = a.proj.plan_x
    PY = a.proj.plan_y

    _SLOT = 10.0  # mm — slot size for PMI dim lines in the strip

    def _bore_info(rec):
        """For Size_Diameter / Size_Radius records, return (bore_axis, cx, cy, cz).

        bore_axis is the bbox's LONGEST extent (the bore's depth direction).
        Reuses rec.dominant_axis set by extract_pmi; falls back to re-sorting
        the bbox spans only when dominant_axis is '?' (degenerate bbox).
        The diameter/radius is then placed perpendicular to the bore axis in the
        view where the bore appears as a circle.  Returns None if ref_bbox absent.
        """
        bb = rec.ref_bbox
        if bb is None:
            return None
        bore_axis = rec.dominant_axis
        if bore_axis == "?":
            xmin, ymin, zmin, xmax, ymax, zmax = bb
            spans = sorted(
                [("X", abs(xmax - xmin)), ("Y", abs(ymax - ymin)), ("Z", abs(zmax - zmin))],
                key=lambda t: t[1],
                reverse=True,
            )
            bore_axis = spans[0][0]
        cx_f = sum(p[0] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
        cy_f = sum(p[1] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
        cz_f = sum(p[2] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
        return bore_axis, cx_f, cy_f, cz_f

    def _witness_from_bbox(rec, view: str):
        """Witness points from the outer edges of the combined reference bbox.

        Gives the correct span for linear dims where both ref faces are flush
        (e.g. two parallel faces of a slot or step).  Not suitable for bore
        diameters — use _bore_info instead.
        """
        bb = rec.ref_bbox
        if bb is None:
            return None
        xmin, ymin, zmin, xmax, ymax, zmax = bb
        ax = rec.dominant_axis

        if view == "front" and ax == "X":
            p1 = (FX(xmin), FZ((zmin + zmax) / 2), 0)
            p2 = (FX(xmax), FZ((zmin + zmax) / 2), 0)
            avg_t = FZ((zmin + zmax) / 2)
        elif view == "front" and ax == "Z":
            p1 = (FX((xmin + xmax) / 2), FZ(zmin), 0)
            p2 = (FX((xmin + xmax) / 2), FZ(zmax), 0)
            avg_t = FX((xmin + xmax) / 2)
        elif view == "side" and ax == "Y":
            p1 = (SX(ymin), SZ((zmin + zmax) / 2), 0)
            p2 = (SX(ymax), SZ((zmin + zmax) / 2), 0)
            avg_t = SZ((zmin + zmax) / 2)
        elif view == "plan" and ax == "Y":
            avg_x = (xmin + xmax) / 2
            p1 = (PX(avg_x), PY(ymin), 0)
            p2 = (PX(avg_x), PY(ymax), 0)
            avg_t = PX(avg_x)
        else:
            return None

        span = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if span < 3:
            return None
        return p1, p2, avg_t

    def _try_above(p1, p2, strip, label, name, view):
        """Place a horizontal dimension line ABOVE the witness points."""
        if strip is None:
            return False
        witness_y = max(p1[1], p2[1]) + 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) <= witness_y:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            _dim(
                (p1[0], witness_y, 0),
                (p2[0], witness_y, 0),
                "above",
                slot - witness_y,
                draft,
                label=label,
            ),
            name,
            view=view,
        )
        return True

    def _try_below(p1, p2, strip, label, name, view):
        """Place a horizontal dimension line BELOW the witness points."""
        if strip is None:
            return False
        witness_y = min(p1[1], p2[1]) - 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) >= witness_y:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            _dim(
                (p1[0], witness_y, 0),
                (p2[0], witness_y, 0),
                "below",
                witness_y - slot,
                draft,
                label=label,
            ),
            name,
            view=view,
        )
        return True

    def _try_right(p1, p2, strip, label, name, view):
        """Place a vertical dimension line to the RIGHT of the witness points."""
        if strip is None:
            return False
        witness_x = max(p1[0], p2[0]) + 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) <= witness_x:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            _dim(
                (witness_x, p1[1], 0),
                (witness_x, p2[1], 0),
                "right",
                slot - witness_x,
                draft,
                label=label,
            ),
            name,
            view=view,
        )
        return True

    def _try_left(p1, p2, strip, label, name, view):
        """Place a vertical dimension line to the LEFT of the witness points."""
        if strip is None:
            return False
        witness_x = min(p1[0], p2[0]) - 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) >= witness_x:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            _dim(
                (witness_x, p1[1], 0),
                (witness_x, p2[1], 0),
                "left",
                witness_x - slot,
                draft,
                label=label,
            ),
            name,
            view=view,
        )
        return True
        return False

    emitted = 0
    for idx, rec in enumerate(usable):
        ax = rec.dominant_axis
        label = rec.label
        placed = False
        name_x = f"pmi_x_{idx}"
        name_z = f"pmi_z_{idx}"
        name_y = f"pmi_y_{idx}"
        name_d = f"pmi_d_{idx}"

        if rec.kind in ("diameter", "radius"):
            # --- Bore size: centroid ± value/2 perpendicular to bore axis ---
            info = _bore_info(rec)
            if info is None:
                _log.debug("PMI dim[%d] diam: no ref_bbox, skip", idx)
                continue
            bore_axis, cx_f, cy_f, cz_f = info
            half = rec.value / 2 if rec.kind == "diameter" else rec.value

            # Bore diameter page span = diameter × scale.  When the span is
            # narrower than ~8 mm the centred label text overflows the gap
            # and the extension lines punch through it.  Use a Leader
            # (arrowhead at bore edge, text on a horizontal shelf) for
            # narrow bores; bracket dims only when span fits the text.
            half_pg = half * a.SCALE  # bore radius on page (mm)

            if bore_axis == "Z":
                # Z-axis bore: circle visible in plan view.
                if half_pg >= 4.0:
                    p1 = (PX(cx_f - half), PY(cy_f), 0)
                    p2 = (PX(cx_f + half), PY(cy_f), 0)
                    placed = _try_above(
                        p1, p2, a.pv_zones.above, label, name_d, "plan"
                    ) or _try_below(p1, p2, a.pv_zones.below, label, name_d, "plan")
                else:
                    tip = (PX(cx_f), PY(cy_f) + half_pg, 0)
                    slot = a.pv_zones.above.allocate(_SLOT)
                    if slot is not None:
                        dwg.add(
                            Leader(tip, (PX(cx_f), slot, 0), label, draft), name_d, view="plan"
                        )
                        placed = True
                    else:
                        slot = a.pv_zones.below.allocate(_SLOT)
                        if slot is not None:
                            tip = (PX(cx_f), PY(cy_f) - half_pg, 0)
                            dwg.add(
                                Leader(tip, (PX(cx_f), slot, 0), label, draft), name_d, view="plan"
                            )
                            placed = True

            elif bore_axis == "X":
                # X-axis bore: circle visible in side view.
                if half_pg >= 4.0:
                    p1 = (SX(cy_f - half), SZ(cz_f), 0)
                    p2 = (SX(cy_f + half), SZ(cz_f), 0)
                    placed = _try_above(
                        p1, p2, a.sv_zones.above, label, name_d, "side"
                    ) or _try_below(p1, p2, a.sv_zones.below, label, name_d, "side")
                else:
                    tip = (SX(cy_f), SZ(cz_f) + half_pg, 0)
                    slot = a.sv_zones.above.allocate(_SLOT)
                    if slot is not None:
                        dwg.add(
                            Leader(tip, (SX(cy_f), slot, 0), label, draft), name_d, view="side"
                        )
                        placed = True
                    else:
                        slot = a.sv_zones.below.allocate(_SLOT)
                        if slot is not None:
                            tip = (SX(cy_f), SZ(cz_f) - half_pg, 0)
                            dwg.add(
                                Leader(tip, (SX(cy_f), slot, 0), label, draft), name_d, view="side"
                            )
                            placed = True

            elif bore_axis == "Y":
                # Y-axis bore: circle visible in front view as a circle.
                if half_pg >= 4.0:
                    p1 = (FX(cx_f - half), FZ(cz_f), 0)
                    p2 = (FX(cx_f + half), FZ(cz_f), 0)
                    placed = _try_above(
                        p1, p2, a.fv_zones.above, label, name_d, "front"
                    ) or _try_below(p1, p2, a.fv_zones.below, label, name_d, "front")
                else:
                    # Narrow bore: leader from bore bottom into the below strip.
                    tip = (FX(cx_f), FZ(cz_f) - half_pg, 0)
                    slot = a.fv_zones.below.allocate(_SLOT)
                    if slot is not None:
                        elbow = (FX(cx_f), slot, 0)
                        dwg.add(Leader(tip, elbow, label, draft), name_d, view="front")
                        placed = True
                    else:
                        # Fall back: leader upward into the above strip.
                        slot = a.fv_zones.above.allocate(_SLOT)
                        if slot is not None:
                            tip = (FX(cx_f), FZ(cz_f) + half_pg, 0)
                            elbow = (FX(cx_f), slot, 0)
                            dwg.add(Leader(tip, elbow, label, draft), name_d, view="front")
                            placed = True

        elif ax == "X":
            wp = _witness_from_bbox(rec, "front")
            if wp is None:
                _log.debug("PMI dim[%d] X: degenerate bbox", idx)
                continue
            p1, p2, avg_pz = wp
            if avg_pz >= a.FV_Y:
                placed = _try_above(p1, p2, a.fv_zones.above, label, name_x, "front")
            if not placed:
                placed = _try_below(p1, p2, a.fv_zones.below, label, name_x, "front")

        elif ax == "Z":
            wp = _witness_from_bbox(rec, "front")
            if wp is None:
                _log.debug("PMI dim[%d] Z: degenerate bbox", idx)
                continue
            p1, p2, avg_px = wp
            if avg_px >= a.FV_X:
                placed = _try_right(p1, p2, a.fv_zones.right, label, name_z, "front")
            if not placed:
                placed = _try_left(p1, p2, a.fv_zones.left, label, name_z, "front")

        elif ax == "Y":
            # Try side view (Y maps to SX horizontal).
            wp = _witness_from_bbox(rec, "side")
            if wp is not None:
                p1, p2, avg_sz = wp
                if avg_sz >= a.SV_Y:
                    placed = _try_above(p1, p2, a.sv_zones.above, label, name_y, "side")
                if not placed:
                    placed = _try_below(p1, p2, a.sv_zones.below, label, name_y, "side")
            # Fall back: plan view (Y maps to PY vertical).
            if not placed:
                wp = _witness_from_bbox(rec, "plan")
                if wp is not None:
                    p1, p2, _ = wp
                    placed = _try_below(p1, p2, a.pv_zones.below, label, name_y, "plan")

        if placed:
            emitted += 1
            _log.info("PMI dim[%d] %s %.3g → annotated (%s)", idx, ax, rec.value, label)
        else:
            _log.info("PMI dim[%d] %s %.3g → no strip space", idx, ax, rec.value)

    _log.info("PMI annotate: %d/%d dims placed", emitted, len(usable))
