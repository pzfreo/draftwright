"""Shared layout-signature helpers + the small part corpus.

Extracted from the retired ``test_layout_snapshot.py`` (a temporary ADR 0009
characterization gate, deleted once the migration landed — #319/#641 gap 3). ``_signature``
(a determinism fingerprint) and ``CORPUS`` (the exercised parts) outlive it: ``_signature`` is
used by ``test_layout_property`` (determinism gate) and ``test_layout_cleanliness``; ``CORPUS``
by ``test_layout_cleanliness`` and ``test_strip_layout``. (``test_layout_hypothesis`` uses
neither — it generates its own parts and checks lint codes, not this fingerprint.)

Underscore-prefixed so pytest does not collect it as a test module.
"""

from __future__ import annotations

from build123d import (
    Align,
    Box,
    BuildPart,
    Cylinder,
    Hole,
    Locations,
    Mode,
    Pos,
    Rotation,
)

# --- corpus: small, fast parts that exercise the strip placers -------------


def _box():
    return Box(40, 30, 12)


def _plate_holes():
    with BuildPart() as p:
        Box(90, 60, 20)
        with Locations((30, 18, 0), (-30, 18, 0), (30, -18, 0), (-30, -18, 0)):
            Hole(4, depth=20)
    return p.part


def _bracket():
    # central bore + offset counterbore → plan callouts + section A-A.
    return Box(90, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)


def _side_drilled():
    # radial (X-axis) through-holes at two heights → the contended side/below
    # strip: off-axis location dims (#133/#225) sharing space with callouts.
    part = Box(60, 40, 30)
    for z in (8, 20):
        part -= Pos(0, 0, z) * Rotation(0, 90, 0) * Cylinder(3, 80)
    return part


def _slotted():
    # an enclosed through-slot (#135) → slot width/length/position dims.
    return Box(50, 30, 20) - Box(20, 8, 30)


def _holed_slot():
    # A hole whose X-location coincides with the slot's near edge (both measure datum→"20"):
    # the #345 duplicate + #346 interleave. Locks the unified corridor solve — one solve
    # over the plan-above strip dedups the coincident slot-position line and orders the
    # ladder (size dim innermost, hole locations nesting outward, monotonically). Three
    # non-collinear holes (so no linear-array pattern collapses the X-locations) give a
    # real 3-rung ladder to order.
    with BuildPart() as p:
        Box(60, 40, 20)
        Box(20, 8, 30, mode=Mode.SUBTRACT)  # slot: long_axis X, near edge x=-10
        # hole @ x=-10 coincides with the slot edge; the others give distinct X-locations.
        with Locations((-10, 14, 0), (20, 14, 0), (8, -14, 0)):
            Hole(3, depth=20)
    return p.part


def _turned_shaft():
    # Z-turned stepped cylinder → step diameters + axial length chain.
    base = (Align.CENTER, Align.CENTER, Align.MIN)
    s = Cylinder(12, 16, align=base)
    s += Pos(0, 0, 16) * Cylinder(8, 14, align=base)
    s += Pos(0, 0, 30) * Cylinder(5, 10, align=base)
    return s


def _drive_screw_x():
    # X-turned cylinder + coaxial axial bore — the #305 round-view case.
    with BuildPart() as p:
        Cylinder(radius=6, height=20)
        Hole(0.8, depth=8)
    assert p.part is not None
    return Rotation(0, 90, 0) * p.part


def _dshape():
    # D-profile bar (circle + flat) with a coaxial stepped bore. Its callout must
    # clear the coaxial bore's location-dim line even though the flat makes the part
    # non-rotational and offsets the bore off centre — the #321 case the shape gate
    # missed. Locks in the occupancy/row-driven lift.
    body = Cylinder(radius=4, height=24)
    body -= Pos(0, -5, 0) * Box(12, 6, 26)
    body -= Cylinder(radius=1.65, height=26)
    body -= Pos(0, 0, 12) * Cylinder(
        radius=0.8, height=3.5, align=(Align.CENTER, Align.CENTER, Align.MAX)
    )
    return Rotation(0, 90, 0) * body


def _flange():
    import math

    flange = Cylinder(radius=45, height=10) - Cylinder(radius=8, height=10)
    for i in range(5):
        ang = math.radians(72 * i)
        flange -= Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(3, 10)
    return flange


CORPUS = {
    "box": _box,
    "plate_holes": _plate_holes,
    "bracket": _bracket,
    "side_drilled": _side_drilled,
    "slotted": _slotted,
    "holed_slot": _holed_slot,
    "dshape": _dshape,
    "turned_shaft": _turned_shaft,
    "drive_screw_x": _drive_screw_x,
    "flange": _flange,
}


# --- signature -------------------------------------------------------------


# Quantise to a 0.1 mm grid with a 1e-6 bias. (a) 0.1 mm, not finer: the placement drift
# this fingerprint cares about is >= ~1 mm. (b) the bias: a Dimension's geometry-box edges
# can land exactly on the .X5 round-half boundary, and a ~1 ULP floating-point reorder would
# otherwise flip an on-boundary value's rounding and spuriously change the signature. 1e-6
# is far above the ~1e-13 FP-noise floor and far below the 0.1 mm grid, so on-boundary values
# resolve the same way regardless of that noise.
def _round_bbox(box):
    if box is None:
        return None
    return [round(float(v) + 1e-6, 1) for v in box]


def _label_box(o):
    return getattr(o, "label_bbox", None)


def _geom_box(o):
    # The FULL rendered geometry bbox — leader shafts/arrow tips, dimension witness
    # and extension lines, hatch — none of which the label box covers.
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception:
        return None


def _signature(dwg) -> dict:
    """A determinism fingerprint of a finished drawing: every annotation's owning view,
    type, label, and rounded bbox, plus each view's projected bbox and the render-list
    size. Two builds of the same part must produce an identical signature."""
    annotations = sorted(
        (
            {
                "name": name,
                "view": dwg.view_of(name),
                "type": type(o).__name__,
                "label": getattr(o, "label", "") or "",
                "label_bbox": _round_bbox(_label_box(o)),
                "geom_bbox": _round_bbox(_geom_box(o)),
            }
            for name, o in dwg.iter_annotations()
        ),
        key=lambda a: a["name"],
    )
    views = {}
    for vname, shapes in dwg.views.items():
        vis = shapes[0] if isinstance(shapes, (tuple, list)) else shapes
        views[vname] = _round_bbox(_geom_box(vis))
    # Total render-list size catches drift in unnamed annotations too, which
    # iter_annotations() (named only) would otherwise hide.
    return {"views": views, "annotations": annotations, "item_count": len(dwg.items)}
